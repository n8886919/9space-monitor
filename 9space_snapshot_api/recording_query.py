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

import logging
import re
import time
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

_LOGGER = logging.getLogger(__name__)

# The Dahua CGI's StartTime/EndTime fields are naive timestamps in the NVR's
# own local time, not UTC. This must match the integration's original
# assumption (custom_components/nvr_monitor/recording.py LOCAL_TZ) so the
# query window sent to the NVR is not shifted by the local UTC offset.
LOCAL_TZ = ZoneInfo("Asia/Taipei")

HTTP_TIMEOUT = 8
# One media-file search may require factory.create, findFile, up to 20
# findNextFile pages, and a best-effort destroy. Bound the complete sequence
# rather than allowing every HTTP operation its own full timeout.
RECORDING_OPERATION_TIMEOUT = 30.0
MAX_FILES = 2000
RECENT_WINDOW_HOURS = 24
MAX_CGI_RESPONSE_BYTES = 1 * 1024 * 1024
FIND_NEXT_FILE_COUNT = 100


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


def _parse_items(response: str, requested_count: int) -> list[dict[str, str]]:
    found_match = re.search(r"^found=(\d+)\s*$", response, re.M)
    if not found_match:
        raise ValueError("missing_found_count")
    found = int(found_match.group(1))
    # The regex only matches non-negative digits, but validate explicitly
    # (defense in depth) and never build a list sized by an NVR-controlled
    # value larger than what was actually requested in this page.
    if found < 0 or found > requested_count:
        raise ValueError("invalid_found_count")
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

    @staticmethod
    def _remaining_timeout(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("recording_operation_deadline_exceeded")
        return min(HTTP_TIMEOUT, remaining)

    @staticmethod
    def _set_response_timeout(response, timeout: float) -> None:
        """Shorten urllib's underlying socket timeout when available.

        ``HTTPResponse.read1`` is used below so a slow trickle returns control
        after each socket read. The attribute path is deliberately optional
        because fake responses and alternate urllib handlers need not expose
        the CPython HTTPResponse internals.
        """
        fp = getattr(response, "fp", None)
        raw = getattr(fp, "raw", None)
        response_socket = getattr(raw, "_sock", None)
        if response_socket is not None:
            response_socket.settimeout(timeout)

    def _get(
        self, path: str, params: list[tuple[str, str]], deadline: float
    ) -> str:
        query = urlencode(params, quote_via=quote)
        timeout = self._remaining_timeout(deadline)
        with self.opener.open(
            f"{self.base_url}{path}?{query}", timeout=timeout
        ) as response:
            # ``read1`` performs at most one underlying buffered read, unlike
            # ``read(n)`` which may internally wait for all n bytes while a
            # peer trickles data. Re-check and shorten the timeout before
            # every read so the complete CGI operation cannot renew its
            # timeout indefinitely.
            read_chunk = getattr(response, "read1", response.read)
            data = bytearray()
            while len(data) <= MAX_CGI_RESPONSE_BYTES:
                timeout = self._remaining_timeout(deadline)
                self._set_response_timeout(response, timeout)
                chunk = read_chunk(
                    min(64 * 1024, MAX_CGI_RESPONSE_BYTES + 1 - len(data))
                )
                if time.monotonic() >= deadline:
                    raise TimeoutError("recording_operation_deadline_exceeded")
                if not chunk:
                    break
                data.extend(chunk)
            if len(data) > MAX_CGI_RESPONSE_BYTES:
                raise ValueError("cgi_response_too_large")
            return bytes(data).decode("utf-8", errors="replace")

    def query_channel(self, channel_id: int) -> dict:
        """Query the last 24h of recordings for one NVR channel.

        ``channel_id`` is the one-based Dahua NVR channel; channel 1 here
        always queries NVR channel 1 (no 0/1-based remapping).
        """
        deadline = time.monotonic() + RECORDING_OPERATION_TIMEOUT
        now = datetime.now(LOCAL_TZ)
        start = now - timedelta(hours=RECENT_WINDOW_HOURS)
        object_id = ""
        stage = "factory_create"
        try:
            object_id = _parse_object_id(
                self._get(
                    "/cgi-bin/mediaFileFind.cgi",
                    [("action", "factory.create")],
                    deadline,
                )
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
                deadline,
            )
            if not started.lstrip().startswith("OK"):
                raise ValueError("find_file_failed")

            files: list[dict[str, str]] = []
            while len(files) < MAX_FILES:
                self._remaining_timeout(deadline)
                stage = "find_next_file"
                page = _parse_items(
                    self._get(
                        "/cgi-bin/mediaFileFind.cgi",
                        [("action", "findNextFile"), ("object", object_id), ("count", str(FIND_NEXT_FILE_COUNT))],
                        deadline,
                    ),
                    FIND_NEXT_FILE_COUNT,
                )
                files.extend(page)
                if len(page) < FIND_NEXT_FILE_COUNT:
                    break

            self._remaining_timeout(deadline)
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
            if object_id and time.monotonic() < deadline:
                try:
                    destroy_body = self._get(
                        "/cgi-bin/mediaFileFind.cgi",
                        [("action", "destroy"), ("object", object_id)],
                        deadline,
                    )
                    # Best-effort confirmation only; never log the object
                    # handle, request URL, response body or exception text.
                    if not destroy_body.lstrip().upper().startswith("OK"):
                        _LOGGER.warning(
                            "mediaFileFind destroy did not confirm success for channel %s",
                            channel_id,
                        )
                except (HTTPError, URLError, OSError, TimeoutError, ValueError):
                    _LOGGER.warning(
                        "mediaFileFind destroy failed for channel %s", channel_id
                    )
        return {
            "recording_query_ok": False,
            "recording_recent": None,
            "last_recording": None,
            "error_code": error_code,
        }


def query_channel(channel_id: int, nvr: NvrHttpConfig) -> dict:
    """Convenience wrapper: one bounded recording query for one channel."""
    return DahuaRecordingClient(nvr).query_channel(channel_id)
