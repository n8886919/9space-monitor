"""Async client and strict response parsing for 9Space Hub."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import aiohttp


class HubApiError(Exception):
    """Safe base error for Hub access."""


class HubCannotConnect(HubApiError):
    """Hub transport failed."""


class HubInvalidResponse(HubApiError):
    """Hub returned data outside the component contract."""


def normalize_base_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid_hub_base_url")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _timestamp(value: Any) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise HubInvalidResponse("invalid_timestamp")
    return value


def _iso_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise HubInvalidResponse("invalid_iso_timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HubInvalidResponse("invalid_iso_timestamp") from None
    if parsed.tzinfo is None:
        raise HubInvalidResponse("invalid_iso_timestamp")
    return value


def _optional_bool(value: Any) -> bool | None:
    if value is not None and type(value) is not bool:
        raise HubInvalidResponse("invalid_boolean")
    return value


def _optional_number(value: Any, lower: float, upper: float) -> int | float | None:
    if value is None:
        return None
    if type(value) not in {int, float} or not lower <= float(value) <= upper:
        raise HubInvalidResponse("invalid_number")
    return value


def _optional_int(value: Any, lower: int, upper: int) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not lower <= value <= upper:
        raise HubInvalidResponse("invalid_integer")
    return value


@dataclass(frozen=True, slots=True)
class HubCamera:
    site_id: str
    site_name: str
    camera_id: int
    label: str
    snapshot_available: bool
    last_good_age_seconds: int | None
    snapshot_success: bool | None
    snapshot_timestamp_ms: int | None
    snapshot_latency_ms: float | None
    snapshot_error: str | None
    live_video: bool | None
    live_checked_at: str | None
    recording_query_ok: bool | None
    recording_recent: bool | None
    last_recording: str | None
    recording_checked_at: str | None
    recording_files_24h: int | None
    recording_coverage_24h: float | None
    recording_error: str | None


@dataclass(frozen=True, slots=True)
class HubSite:
    site_id: str
    display_name: str
    updated_at: int | None
    cameras: tuple[HubCamera, ...]


def parse_sites(payload: Any) -> dict[str, HubSite]:
    if not isinstance(payload, dict) or set(payload) != {"sites"} or not isinstance(payload["sites"], list):
        raise HubInvalidResponse("invalid_sites_contract")
    result: dict[str, HubSite] = {}
    for raw_site in payload["sites"]:
        if not isinstance(raw_site, dict):
            raise HubInvalidResponse("invalid_site")
        site_id = raw_site.get("site_id")
        display_name = raw_site.get("display_name")
        cameras_raw = raw_site.get("cameras")
        if (
            not isinstance(site_id, str) or not site_id
            or not isinstance(display_name, str) or not display_name
            or not isinstance(cameras_raw, list)
            or site_id in result
        ):
            raise HubInvalidResponse("invalid_site")
        cameras: list[HubCamera] = []
        seen: set[int] = set()
        for raw in cameras_raw:
            if not isinstance(raw, dict):
                raise HubInvalidResponse("invalid_camera")
            camera_id = raw.get("camera_id")
            label = raw.get("label")
            if type(camera_id) is not int or not 1 <= camera_id <= 4096 or camera_id in seen:
                raise HubInvalidResponse("invalid_camera")
            if not isinstance(label, str) or not label:
                raise HubInvalidResponse("invalid_camera")
            seen.add(camera_id)
            attempt = raw.get("latest_attempt")
            if attempt is not None and not isinstance(attempt, dict):
                raise HubInvalidResponse("invalid_snapshot_attempt")
            attempt = attempt or {}
            error = attempt.get("error_code")
            recording_error = raw.get("recording_error")
            if error is not None and not isinstance(error, str):
                raise HubInvalidResponse("invalid_error_code")
            if recording_error is not None and not isinstance(recording_error, str):
                raise HubInvalidResponse("invalid_error_code")
            snapshot_available = raw.get("snapshot_available")
            if type(snapshot_available) is not bool:
                raise HubInvalidResponse("invalid_camera")
            cameras.append(HubCamera(
                site_id=site_id,
                site_name=display_name,
                camera_id=camera_id,
                label=label,
                snapshot_available=snapshot_available,
                last_good_age_seconds=_optional_int(raw.get("last_good_age_seconds"), 0, 10**9),
                snapshot_success=_optional_bool(attempt.get("success")),
                snapshot_timestamp_ms=_timestamp(attempt.get("timestamp")),
                snapshot_latency_ms=_optional_number(attempt.get("latency_ms"), 0, 3_600_000),
                snapshot_error=error,
                live_video=_optional_bool(raw.get("live_video")),
                live_checked_at=_iso_timestamp(raw.get("live_checked_at")),
                recording_query_ok=_optional_bool(raw.get("recording_query_ok")),
                recording_recent=_optional_bool(raw.get("recording_recent")),
                last_recording=_iso_timestamp(raw.get("last_recording")),
                recording_checked_at=_iso_timestamp(raw.get("recording_checked_at")),
                recording_files_24h=_optional_int(raw.get("recording_files_24h"), 0, 10_000_000),
                recording_coverage_24h=_optional_number(raw.get("recording_coverage_24h"), 0, 100),
                recording_error=recording_error,
            ))
        result[site_id] = HubSite(site_id, display_name, _timestamp(raw_site.get("updated_at")), tuple(cameras))
    return result


class HubApiClient:
    def __init__(self, base_url: str, session: aiohttp.ClientSession, *, timeout: float = 10.0) -> None:
        self.base_url = normalize_base_url(base_url)
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def _get(self, path: str):
        try:
            return await self._session.get(f"{self.base_url}{path}", timeout=self._timeout)
        except (aiohttp.ClientError, TimeoutError):
            raise HubCannotConnect("hub_unavailable") from None

    async def async_get_sites(self) -> dict[str, HubSite]:
        response = await self._get("/api/v1/sites")
        try:
            async with response:
                if response.status != 200:
                    raise HubInvalidResponse("unexpected_http_status")
                payload = await response.json()
        except HubApiError:
            raise
        except (aiohttp.ClientError, aiohttp.ContentTypeError, TimeoutError, ValueError):
            raise HubCannotConnect("hub_unavailable") from None
        return parse_sites(payload)

    async def async_get_snapshot(self, site_id: str, camera_id: int) -> bytes:
        path = f"/api/v1/sites/{quote(site_id, safe='')}/cameras/{camera_id}/snapshot"
        response = await self._get(path)
        try:
            async with response:
                if response.status != 200:
                    raise HubCannotConnect("snapshot_unavailable")
                if response.headers.get("Content-Type", "").split(";", 1)[0].lower() != "image/jpeg":
                    raise HubInvalidResponse("invalid_snapshot_content_type")
                body = await response.read()
        except HubApiError:
            raise
        except (aiohttp.ClientError, TimeoutError):
            raise HubCannotConnect("hub_unavailable") from None
        if not body or len(body) > 8 * 1024 * 1024:
            raise HubInvalidResponse("invalid_snapshot_body")
        return body
