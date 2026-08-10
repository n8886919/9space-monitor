"""Async client for the local 9Space Snapshot app API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp


class AddonApiError(Exception):
    """Base class for safe app API errors."""


class AddonCannotConnect(AddonApiError):
    """The local app could not be reached."""


class AddonInvalidResponse(AddonApiError):
    """The app returned a response outside the API contract."""


@dataclass(frozen=True, slots=True)
class AddonChannel:
    """Parsed minimal channel state from the local API."""

    channel_id: int
    live_video: bool | None
    recording_query_ok: bool
    recording_recent: bool | None
    last_recording: str | None
    live_checked_at: str | None
    recording_files_24h: int | None
    recording_coverage_24h: float | None
    recording_gap_count_24h: int | None
    recording_gap_total_seconds_24h: float | None
    largest_recording_gap_seconds_24h: float | None
    nvr_first_packet_ms: float | None
    nvr_probe_duration_ms: float | None
    daily_online_rate: float | None
    nvr_live_video_disconnect_count_24h: int | None
    checked_at: str | None
    error_code: str | None

    @classmethod
    def from_json(cls, value: Any) -> AddonChannel:
        """Validate and parse one API channel object."""
        if not isinstance(value, dict):
            raise AddonInvalidResponse("invalid_channel_contract")
        required = {
            "channel_id",
            "live_video",
            "recording_query_ok",
            "recording_recent",
            "last_recording",
            "checked_at",
            "error_code",
        }
        if not required.issubset(value):
            raise AddonInvalidResponse("invalid_channel_contract")
        channel_id = value["channel_id"]
        if type(channel_id) is not int or channel_id < 1:
            raise AddonInvalidResponse("invalid_channel_contract")
        if value["live_video"] is not None and type(value["live_video"]) is not bool:
            raise AddonInvalidResponse("invalid_channel_contract")
        if type(value["recording_query_ok"]) is not bool:
            raise AddonInvalidResponse("invalid_channel_contract")
        if value["recording_recent"] is not None and type(value["recording_recent"]) is not bool:
            raise AddonInvalidResponse("invalid_channel_contract")
        for key in ("last_recording", "live_checked_at", "checked_at", "error_code"):
            if key not in value:
                continue
            if value[key] is not None and not isinstance(value[key], str):
                raise AddonInvalidResponse("invalid_channel_contract")
        for key in ("last_recording", "live_checked_at", "checked_at"):
            if value.get(key) is not None:
                try:
                    parsed = datetime.fromisoformat(value[key])
                except ValueError:
                    raise AddonInvalidResponse(
                        "invalid_channel_contract"
                    ) from None
                if parsed.tzinfo is None:
                    raise AddonInvalidResponse("invalid_channel_contract")
        recording_files = value.get("recording_files_24h")
        recording_gap_count = value.get("recording_gap_count_24h")
        disconnects = value.get("nvr_live_video_disconnect_count_24h")
        for metric in (recording_files, recording_gap_count, disconnects):
            if metric is not None and (
                type(metric) is not int or not 0 <= metric <= 10_000_000
            ):
                raise AddonInvalidResponse("invalid_channel_contract")
        coverage = value.get("recording_coverage_24h")
        online_rate = value.get("daily_online_rate")
        for metric in (coverage, online_rate):
            if metric is not None and (
                type(metric) not in {int, float} or not 0 <= float(metric) <= 100
            ):
                raise AddonInvalidResponse("invalid_channel_contract")
        timing_fields = (
            "recording_gap_total_seconds_24h",
            "largest_recording_gap_seconds_24h",
            "nvr_first_packet_ms",
            "nvr_probe_duration_ms",
        )
        timings: dict[str, float | None] = {}
        for key in timing_fields:
            metric = value.get(key)
            if metric is not None and (
                type(metric) not in {int, float}
                or not 0 <= float(metric) <= 86_400_000
            ):
                raise AddonInvalidResponse("invalid_channel_contract")
            timings[key] = float(metric) if metric is not None else None
        return cls(
            channel_id=channel_id,
            live_video=value["live_video"],
            recording_query_ok=value["recording_query_ok"],
            recording_recent=value["recording_recent"],
            last_recording=value["last_recording"],
            live_checked_at=value.get("live_checked_at"),
            recording_files_24h=recording_files,
            recording_coverage_24h=float(coverage) if coverage is not None else None,
            recording_gap_count_24h=recording_gap_count,
            recording_gap_total_seconds_24h=timings[
                "recording_gap_total_seconds_24h"
            ],
            largest_recording_gap_seconds_24h=timings[
                "largest_recording_gap_seconds_24h"
            ],
            nvr_first_packet_ms=timings["nvr_first_packet_ms"],
            nvr_probe_duration_ms=timings["nvr_probe_duration_ms"],
            daily_online_rate=float(online_rate) if online_rate is not None else None,
            nvr_live_video_disconnect_count_24h=disconnects,
            checked_at=value["checked_at"],
            error_code=value["error_code"],
        )

    def as_dict(self) -> dict[str, Any]:
        """Return the entity-facing representation."""
        return {
            "channel_id": self.channel_id,
            "nvr_live_video": self.live_video,
            "recording_query_ok": self.recording_query_ok,
            "recording_recent": self.recording_recent,
            "last_recording": self.last_recording,
            "live_checked_at": self.live_checked_at,
            "recording_files_24h": self.recording_files_24h,
            "recording_coverage_24h": self.recording_coverage_24h,
            "recording_gap_count_24h": self.recording_gap_count_24h,
            "recording_gap_total_seconds_24h": (
                self.recording_gap_total_seconds_24h
            ),
            "largest_recording_gap_seconds_24h": (
                self.largest_recording_gap_seconds_24h
            ),
            "nvr_first_packet_ms": self.nvr_first_packet_ms,
            "nvr_probe_duration_ms": self.nvr_probe_duration_ms,
            "daily_online_rate": self.daily_online_rate,
            "nvr_live_video_disconnect_count_24h": (
                self.nvr_live_video_disconnect_count_24h
            ),
            "checked_at": self.checked_at,
            "nvr_error": self.error_code or "",
            "recording_error": (
                "" if self.recording_query_ok else self.error_code or "recording_query_failed"
            ),
        }


def normalize_base_url(value: str) -> str:
    """Validate and normalize an HTTP(S) base URL without credentials."""
    raw = value.strip()
    parsed = urlsplit(raw)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid_addon_base_url")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


class AddonApiClient:
    """Use an injected aiohttp-compatible session to query the app."""

    def __init__(
        self,
        base_url: str,
        session: aiohttp.ClientSession,
        *,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def _request(self, path: str) -> Any:
        try:
            return await self._session.get(
                f"{self.base_url}{path}", timeout=self._timeout
            )
        except (aiohttp.ClientError, TimeoutError):
            raise AddonCannotConnect("addon_unavailable") from None

    @staticmethod
    def _map_response_transport_error() -> AddonCannotConnect:
        return AddonCannotConnect("addon_unavailable")

    async def async_get_health(self) -> None:
        """Validate the process health endpoint."""
        response = await self._request("/healthz")
        try:
            async with response:
                if response.status < 200 or response.status >= 300:
                    raise AddonInvalidResponse("unexpected_http_status")
                try:
                    payload = await response.json()
                except (ValueError, aiohttp.ContentTypeError):
                    raise AddonInvalidResponse("invalid_json") from None
                except (aiohttp.ClientError, TimeoutError):
                    raise self._map_response_transport_error() from None
        except AddonApiError:
            raise
        except (aiohttp.ClientError, TimeoutError):
            raise self._map_response_transport_error() from None
        if payload != {"status": "ok"}:
            raise AddonInvalidResponse("invalid_health_contract")

    async def async_get_channels(self) -> list[AddonChannel]:
        """Return all validated one-based channel states."""
        response = await self._request("/api/v1/channels")
        try:
            async with response:
                if response.status < 200 or response.status >= 300:
                    raise AddonInvalidResponse("unexpected_http_status")
                try:
                    payload = await response.json()
                except (ValueError, aiohttp.ContentTypeError):
                    raise AddonInvalidResponse("invalid_json") from None
                except (aiohttp.ClientError, TimeoutError):
                    raise self._map_response_transport_error() from None
        except AddonApiError:
            raise
        except (aiohttp.ClientError, TimeoutError):
            raise self._map_response_transport_error() from None
        if not isinstance(payload, list):
            raise AddonInvalidResponse("invalid_channels_contract")
        channels = [AddonChannel.from_json(item) for item in payload]
        ids = [channel.channel_id for channel in channels]
        if len(ids) != len(set(ids)):
            raise AddonInvalidResponse("duplicate_channel_id")
        return channels
