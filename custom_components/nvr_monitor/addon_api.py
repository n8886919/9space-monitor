"""Async client for the local 9Space snapshot add-on API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp


class AddonApiError(Exception):
    """Base class for safe add-on API errors."""


class AddonCannotConnect(AddonApiError):
    """The local add-on could not be reached."""


class AddonInvalidResponse(AddonApiError):
    """The add-on returned a response outside the API contract."""


class AddonChannelNotFound(AddonApiError):
    """The requested one-based channel does not exist."""


class AddonSnapshotUnavailable(AddonApiError):
    """The requested snapshot is temporarily unavailable."""


@dataclass(frozen=True, slots=True)
class AddonChannel:
    """Parsed minimal channel state from the local API."""

    channel_id: int
    live_video: bool | None
    snapshot_available: bool
    recording_query_ok: bool
    recording_recent: bool | None
    last_recording: str | None
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
            "snapshot_available",
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
        if type(value["snapshot_available"]) is not bool:
            raise AddonInvalidResponse("invalid_channel_contract")
        if type(value["recording_query_ok"]) is not bool:
            raise AddonInvalidResponse("invalid_channel_contract")
        if value["recording_recent"] is not None and type(value["recording_recent"]) is not bool:
            raise AddonInvalidResponse("invalid_channel_contract")
        for key in ("last_recording", "checked_at", "error_code"):
            if value[key] is not None and not isinstance(value[key], str):
                raise AddonInvalidResponse("invalid_channel_contract")
        for key in ("last_recording", "checked_at"):
            if value[key] is not None:
                try:
                    parsed = datetime.fromisoformat(value[key])
                except ValueError:
                    raise AddonInvalidResponse(
                        "invalid_channel_contract"
                    ) from None
                if parsed.tzinfo is None:
                    raise AddonInvalidResponse("invalid_channel_contract")
        return cls(**{key: value[key] for key in required})

    def as_dict(self) -> dict[str, Any]:
        """Return the entity-facing representation."""
        return {
            "channel_id": self.channel_id,
            "nvr_live_video": self.live_video,
            "snapshot_available": self.snapshot_available,
            "recording_query_ok": self.recording_query_ok,
            "recording_recent": self.recording_recent,
            "last_recording": self.last_recording,
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
    """Use an injected aiohttp-compatible session to query the add-on."""

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

    async def async_get_snapshot(self, channel_id: int) -> bytes:
        """Return a non-empty JPEG for a one-based channel."""
        if type(channel_id) is not int or channel_id < 1:
            raise AddonChannelNotFound("channel_not_found")
        response = await self._request(
            f"/api/v1/channels/{channel_id}/snapshot"
        )
        try:
            async with response:
                if response.status == 404:
                    raise AddonChannelNotFound("channel_not_found")
                if response.status == 503:
                    raise AddonSnapshotUnavailable("snapshot_unavailable")
                if response.status != 200:
                    raise AddonInvalidResponse("unexpected_http_status")
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                if content_type != "image/jpeg":
                    raise AddonInvalidResponse("invalid_snapshot_content_type")
                try:
                    body = await response.read()
                except (aiohttp.ClientError, TimeoutError):
                    raise self._map_response_transport_error() from None
        except AddonApiError:
            raise
        except (aiohttp.ClientError, TimeoutError):
            raise self._map_response_transport_error() from None
        if not body:
            raise AddonInvalidResponse("empty_snapshot")
        return body
