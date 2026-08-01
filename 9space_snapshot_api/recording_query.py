"""Dahua recording (mediaFileFind.cgi) query, ported from
``custom_components/nvr_monitor/recording.py`` (``DahuaRecordingClient``).

Same query/auth/coverage algorithm as the integration used (per AGENTS.md:
"不重新設計另一套完全不同的探測方法"). Differences:

- Operates on a plain ``channel_id: int`` instead of ``CameraConfig``.
- Returns only the small, already-redacted fields the add-on API exposes
  (``recording_query_ok``, ``recording_recent``, ``last_recording``,
  ``error_code``) -- never the raw CGI response body, full request URL or
  credentials.
- Runs synchronously (blocking ``urllib`` calls); callers must run it via
  ``asyncio.to_thread`` so it never blocks the FastAPI event loop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import (
    HTTPDigestAuthHandler,
    HTTPPasswordMgrWithDefaultRealm,
    build_opener,
)
from zoneinfo import ZoneInfo

# The Dahua CGI's StartTime/EndTime fields are naive timestamps in the NVR's
# own local time, not UTC. This must match the integration's original
# assumption (custom_components/nvr_monitor/recording.py LOCAL_TZ) so the
# query window sent to the NVR is not shifted by the local UTC offset.
LOCAL_TZ = ZoneInfo("Asia/Taipei")

HTTP_TIMEOUT = 8
MAX_FILES = 2000
RECENT_WINDOW_HOURS = 24


@dataclass(frozen=True)
class NvrHttpConfig:
    """Dahua NVR CGI connection settings (add-on scope only)."""

    host: str
    http_port: int
    username: str
    password: str


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
    for match in re.finditer(r"^items\[(\d+)\]\.([A-Za-z0-9_]+)=(.*)$", response, re.M):
        index = int(match.group(1))
        if index < found:
            items[index][match.group(2)] = match.group(3).strip()
    return items


def _parse_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=LOCAL_TZ)


def _latest_recent(
    files: list[dict[str, str]], start: datetime, end: datetime
) -> tuple[datetime | None, bool]:
    latest: datetime | None = None
    for item in files:
        try:
            item_end = min(end, _parse_time(item["EndTime"]))
            item_start = max(start, _parse_time(item["StartTime"]))
        except (KeyError, ValueError):
            continue
        if item_end <= item_start:
            continue
        latest = max(latest, item_end) if latest else item_end
    recent = latest is not None and (end - latest) <= timedelta(hours=RECENT_WINDOW_HOURS)
    return latest, recent


def _classify_error(exc: BaseException, stage: str) -> str:
    """Map failures to the stable API.md error codes. Never includes the
    CGI response body, full request URL or credentials."""
    if isinstance(exc, HTTPError):
        return "authentication_failed" if exc.code == 401 else "recording_query_failed"
    if isinstance(exc, (URLError, TimeoutError)):
        return "nvr_unreachable"
    return "recording_query_failed"


class DahuaRecordingClient:
    """Perform one bounded, authenticated Dahua media-file search."""

    def __init__(self, nvr: NvrHttpConfig) -> None:
        self.nvr = nvr
        self.base_url = f"http://{nvr.host}:{nvr.http_port}"
        password_manager = HTTPPasswordMgrWithDefaultRealm()
        password_manager.add_password(None, self.base_url, nvr.username, nvr.password)
        self.opener = build_opener(HTTPDigestAuthHandler(password_manager))

    def _get(self, path: str, params: list[tuple[str, str]]) -> str:
        query = urlencode(params, quote_via=quote)
        with self.opener.open(f"{self.base_url}{path}?{query}", timeout=HTTP_TIMEOUT) as response:
            return response.read().decode("utf-8", errors="replace")

    def query_channel(self, channel_id: int) -> dict:
        """Query the last 24h of recordings for one NVR channel.

        ``channel_id`` is the one-based Dahua NVR channel; channel 1 here
        always queries NVR channel 1 (no 0/1-based remapping).
        """
        now = datetime.now(LOCAL_TZ)
        start = now - timedelta(hours=RECENT_WINDOW_HOURS)
        object_id = ""
        stage = "factory_create"
        try:
            object_id = _parse_object_id(
                self._get("/cgi-bin/mediaFileFind.cgi", [("action", "factory.create")])
            )
            stage = "find_file"
            started = self._get(
                "/cgi-bin/mediaFileFind.cgi",
                [
                    ("action", "findFile"),
                    ("object", object_id),
                    ("condition.Channel", str(channel_id)),
                    ("condition.StartTime", start.strftime("%Y-%m-%d %H:%M:%S")),
                    ("condition.EndTime", now.strftime("%Y-%m-%d %H:%M:%S")),
                    ("condition.Types[0]", "dav"),
                ],
            )
            if not started.lstrip().startswith("OK"):
                raise ValueError("find_file_failed")

            files: list[dict[str, str]] = []
            while len(files) < MAX_FILES:
                stage = "find_next_file"
                page = _parse_items(
                    self._get(
                        "/cgi-bin/mediaFileFind.cgi",
                        [("action", "findNextFile"), ("object", object_id), ("count", "100")],
                    )
                )
                files.extend(page)
                if len(page) < 100:
                    break

            latest, recent = _latest_recent(files, start, now)
            return {
                "recording_query_ok": True,
                "recording_recent": recent,
                "last_recording": latest.isoformat() if latest else None,
                "error_code": None,
            }
        except HTTPError as err:
            error_code = _classify_error(err, stage)
        except (URLError, TimeoutError) as err:
            error_code = _classify_error(err, stage)
        except (OSError, ValueError) as err:
            error_code = _classify_error(err, stage)
        finally:
            if object_id:
                try:
                    self._get("/cgi-bin/mediaFileFind.cgi", [("action", "destroy"), ("object", object_id)])
                except (HTTPError, URLError, OSError, TimeoutError):
                    pass
        return {
            "recording_query_ok": False,
            "recording_recent": None,
            "last_recording": None,
            "error_code": error_code,
        }


def query_channel(channel_id: int, nvr: NvrHttpConfig) -> dict:
    """Convenience wrapper: one bounded recording query for one channel."""
    return DahuaRecordingClient(nvr).query_channel(channel_id)
