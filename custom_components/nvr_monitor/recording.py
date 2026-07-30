"""Query Dahua recording files through the local CGI API."""

from __future__ import annotations

import concurrent.futures
from datetime import datetime, timedelta
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import (
    HTTPDigestAuthHandler,
    HTTPPasswordMgrWithDefaultRealm,
    build_opener,
)
from zoneinfo import ZoneInfo

from .api import NvrConfig
from .models import CameraConfig, ProbeResults

LOCAL_TZ = ZoneInfo("Asia/Taipei")
HTTP_TIMEOUT = 8
MAX_FILES = 2000


def _parse_object_id(response: str) -> str:
    match = re.search(r"(?:result|object)\s*=\s*([A-Za-z0-9]+)", response)
    if not match:
        raise ValueError("missing_media_finder_object")
    return match.group(1)


def _parse_items(response: str) -> list[dict[str, str]]:
    found_match = re.search(r"^found=(\d+)\s*$", response, re.M)
    if not found_match:
        raise ValueError("missing_found_count")
    found = int(found_match.group(1))
    items: list[dict[str, str]] = [dict() for _ in range(found)]
    for match in re.finditer(
        r"^items\[(\d+)\]\.([A-Za-z0-9_]+)=(.*)$", response, re.M
    ):
        index = int(match.group(1))
        if index < found:
            items[index][match.group(2)] = match.group(3).strip()
    return items


def _parse_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=LOCAL_TZ
    )


def _coverage(
    files: list[dict[str, str]], start: datetime, end: datetime
) -> tuple[float, int, datetime | None]:
    intervals: list[tuple[datetime, datetime]] = []
    latest: datetime | None = None
    for item in files:
        try:
            item_start = max(start, _parse_time(item["StartTime"]))
            item_end = min(end, _parse_time(item["EndTime"]))
        except (KeyError, ValueError):
            continue
        if item_end <= item_start:
            continue
        intervals.append((item_start, item_end))
        latest = max(latest, item_end) if latest else item_end

    intervals.sort()
    merged: list[list[datetime]] = []
    for interval_start, interval_end in intervals:
        if not merged or interval_start > merged[-1][1]:
            merged.append([interval_start, interval_end])
        else:
            merged[-1][1] = max(merged[-1][1], interval_end)
    covered_seconds = sum(
        (interval_end - interval_start).total_seconds()
        for interval_start, interval_end in merged
    )
    window_seconds = max(1.0, (end - start).total_seconds())
    return (
        round(min(100.0, covered_seconds / window_seconds * 100), 2),
        max(0, len(merged) - 1),
        latest,
    )


class DahuaRecordingClient:
    """Perform authenticated, bounded Dahua media-file searches."""

    def __init__(self, nvr: NvrConfig) -> None:
        self.nvr = nvr
        self.base_url = f"http://{nvr.host}:{nvr.http_port}"
        password_manager = HTTPPasswordMgrWithDefaultRealm()
        password_manager.add_password(
            None, self.base_url, nvr.username, nvr.password
        )
        self.opener = build_opener(HTTPDigestAuthHandler(password_manager))

    def _get(self, path: str, params: list[tuple[str, str]]) -> str:
        query = urlencode(params, quote_via=quote)
        with self.opener.open(
            f"{self.base_url}{path}?{query}", timeout=HTTP_TIMEOUT
        ) as response:
            return response.read().decode("utf-8", errors="replace")

    def _probe_one(
        self, camera: CameraConfig
    ) -> tuple[str, dict[str, Any]]:
        now = datetime.now(LOCAL_TZ)
        start = now - timedelta(hours=24)
        object_id = ""
        try:
            object_id = _parse_object_id(
                self._get(
                    "/cgi-bin/mediaFileFind.cgi",
                    [("action", "factory.create")],
                )
            )
            started = self._get(
                "/cgi-bin/mediaFileFind.cgi",
                [
                    ("action", "findFile"),
                    ("object", object_id),
                    ("condition.Channel", str(camera.channel - 1)),
                    (
                        "condition.StartTime",
                        start.strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                    (
                        "condition.EndTime",
                        now.strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                    ("condition.Types[0]", "dav"),
                ],
            )
            if not started.lstrip().startswith("OK"):
                raise ValueError("find_file_failed")

            files: list[dict[str, str]] = []
            while len(files) < MAX_FILES:
                page = _parse_items(
                    self._get(
                        "/cgi-bin/mediaFileFind.cgi",
                        [
                            ("action", "findNextFile"),
                            ("object", object_id),
                            ("count", "100"),
                        ],
                    )
                )
                files.extend(page)
                if len(page) < 100:
                    break
            coverage, gap_count, latest = _coverage(files, start, now)
            age_hours = (
                round((now - latest).total_seconds() / 3600, 2)
                if latest
                else None
            )
            return camera.subentry_id, {
                "recording_query_ok": True,
                "recording_count_24h": len(files),
                "recording_coverage_24h_pct": coverage,
                "recording_gap_count_24h": gap_count,
                "last_recording": latest.isoformat() if latest else None,
                "last_recording_age_hours": age_hours,
                "recording_recent": age_hours is not None and age_hours <= 24,
                "recording_error": "",
                "recording_truncated": len(files) >= MAX_FILES,
            }
        except HTTPError as err:
            error = "invalid_auth" if err.code == 401 else f"http_{err.code}"
        except (URLError, TimeoutError):
            error = "cannot_connect"
        except (OSError, ValueError) as err:
            error = str(err) or type(err).__name__.lower()
        finally:
            if object_id:
                try:
                    self._get(
                        "/cgi-bin/mediaFileFind.cgi",
                        [("action", "destroy"), ("object", object_id)],
                    )
                except (HTTPError, URLError, OSError, TimeoutError):
                    pass
        return camera.subentry_id, {
            "recording_query_ok": False,
            "recording_count_24h": None,
            "recording_coverage_24h_pct": None,
            "recording_gap_count_24h": None,
            "last_recording": None,
            "last_recording_age_hours": None,
            "recording_recent": False,
            "recording_error": error,
            "recording_truncated": False,
        }

    def probe_recordings(self, cameras: list[CameraConfig]) -> ProbeResults:
        """Search recordings sequentially to minimize NVR load."""
        results: ProbeResults = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            jobs = [executor.submit(self._probe_one, camera) for camera in cameras]
            for future in concurrent.futures.as_completed(jobs):
                subentry_id, result = future.result()
                results[subentry_id] = result
        return results
