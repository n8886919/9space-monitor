"""Bounded Center-to-add-on last-good snapshot refresh scheduler."""
from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlsplit

from .snapshots import DEFAULT_MAX_SNAPSHOT_BYTES, SnapshotStore, validate_camera_id
from .storage import TelemetryStorage
from .validation import TelemetryValidationError, validate_display_name, validate_site_id

MAX_CONFIG_BYTES = 128 * 1024
MAX_SITES = 32
MAX_CHANNELS_PER_SITE = 256
MAX_CONCURRENCY = 8
MIN_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS = 1, 15
MIN_REFRESH_SECONDS, MAX_REFRESH_SECONDS = 5, 86400


@dataclass(frozen=True)
class SnapshotSite:
    site_id: str
    display_name: str
    base_url: str
    channels: tuple[int, ...]
    concurrency: int
    timeout_seconds: int
    refresh_seconds: int


def load_sites(path: str | Path) -> tuple[SnapshotSite, ...]:
    """Read bounded private JSON; an absent file intentionally disables pull."""
    try:
        with Path(path).open("rb") as handle:
            raw_bytes = handle.read(MAX_CONFIG_BYTES + 1)
    except FileNotFoundError:
        return ()
    if len(raw_bytes) > MAX_CONFIG_BYTES:
        raise ValueError("snapshot_sites_config_too_large")
    try:
        raw = json.loads(raw_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_snapshot_sites_config") from exc
    if not isinstance(raw, dict) or set(raw) != {"sites"} or not isinstance(raw["sites"], list):
        raise ValueError("invalid_snapshot_sites_config")
    if len(raw["sites"]) > MAX_SITES:
        raise ValueError("too_many_snapshot_sites")
    sites = tuple(_site(item) for item in raw["sites"])
    if len({site.site_id for site in sites}) != len(sites):
        raise ValueError("duplicate_snapshot_site")
    return sites


def _site(value: object) -> SnapshotSite:
    expected = {"site_id", "display_name", "base_url", "channels", "concurrency", "timeout_seconds", "refresh_seconds"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("invalid_snapshot_site")
    try:
        site_id = validate_site_id(value["site_id"])
        display_name = validate_display_name(value["display_name"])
    except TelemetryValidationError as exc:
        raise ValueError("invalid_snapshot_site") from exc
    base_url = value["base_url"]
    parsed = urlsplit(base_url) if isinstance(base_url, str) else None
    if not parsed or parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("invalid_snapshot_site_url")
    channels = value["channels"]
    if not isinstance(channels, list) or not channels or len(channels) > MAX_CHANNELS_PER_SITE:
        raise ValueError("invalid_snapshot_channels")
    try:
        checked = tuple(validate_camera_id(channel) for channel in channels)
    except ValueError as exc:
        raise ValueError("invalid_snapshot_channels") from exc
    if len(set(checked)) != len(checked):
        raise ValueError("duplicate_snapshot_channel")
    bounds = ("concurrency", "timeout_seconds", "refresh_seconds")
    if any(type(value[key]) is not int for key in bounds):
        raise ValueError("invalid_snapshot_bounds")
    if not 1 <= value["concurrency"] <= MAX_CONCURRENCY or not MIN_TIMEOUT_SECONDS <= value["timeout_seconds"] <= MAX_TIMEOUT_SECONDS or not MIN_REFRESH_SECONDS <= value["refresh_seconds"] <= MAX_REFRESH_SECONDS:
        raise ValueError("invalid_snapshot_bounds")
    return SnapshotSite(site_id, display_name, base_url.rstrip("/"), checked, value["concurrency"], value["timeout_seconds"], value["refresh_seconds"])


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


async def default_fetch(url: str, timeout_seconds: int) -> tuple[int, str, bytes]:
    def fetch() -> tuple[int, str, bytes]:
        opener = urllib.request.build_opener(_NoRedirect())
        try:
            with opener.open(url, timeout=timeout_seconds) as response:
                if response.geturl() != url:
                    raise ValueError("snapshot_redirected")
                return response.status, response.headers.get_content_type(), response.read(DEFAULT_MAX_SNAPSHOT_BYTES + 1)
        except urllib.error.HTTPError as error:
            return error.code, "", b""
    return await asyncio.to_thread(fetch)


class SnapshotScheduler:
    def __init__(self, sites: tuple[SnapshotSite, ...], storage: TelemetryStorage, snapshots: SnapshotStore, *, fetcher: Callable[[str, int], Awaitable[tuple[int, str, bytes]]] = default_fetch, run_sync: Callable[..., Awaitable[object]] = asyncio.to_thread) -> None:
        self.sites = sites
        self.storage = storage
        self.snapshots = snapshots
        self.fetcher = fetcher
        self.run_sync = run_sync
        self.metadata_dropped = 0
        self._task: asyncio.Task | None = None

    async def register(self) -> None:
        for site in self.sites:
            for channel in site.channels:
                await self.run_sync(self.storage.register_snapshot_camera, site.site_id, channel, site.display_name)

    async def run_round(self, site: SnapshotSite) -> None:
        for start in range(0, len(site.channels), site.concurrency):
            batch = site.channels[start:start + site.concurrency]
            await asyncio.gather(*(self._attempt(site, channel) for channel in batch))

    async def _attempt(self, site: SnapshotSite, channel: int) -> None:
        started = time.perf_counter()
        success, code = False, "internal_error"
        try:
            url = f"{site.base_url}/api/v1/channels/{channel}/snapshot"
            status, content_type, body = await asyncio.wait_for(self.fetcher(url, site.timeout_seconds), timeout=site.timeout_seconds)
            if status != 200:
                code = "snapshot_unavailable"
            elif len(body) > DEFAULT_MAX_SNAPSHOT_BYTES:
                code = "snapshot_too_large"
            elif content_type.lower().split(";", 1)[0].strip() != "image/jpeg" or not body:
                code = "invalid_snapshot_response"
            else:
                try:
                    await self.run_sync(self.snapshots.write, site.site_id, channel, body)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    code = "snapshot_store_failed"
                else:
                    success, code = True, None
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            code = "snapshot_timeout"
        except Exception:
            code = "snapshot_fetch_failed"
        now_ms = int(time.time() * 1000)
        try:
            await self.run_sync(self.storage.record_snapshot_attempt, site.site_id, channel, success=success, timestamp_ms=now_ms, latency_ms=(time.perf_counter() - started) * 1000, error_code=code, now_ms=now_ms)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.metadata_dropped += 1

    async def _loop(self, site: SnapshotSite) -> None:
        while True:
            await self.run_round(site)
            await asyncio.sleep(site.refresh_seconds)

    async def _run(self) -> None:
        await asyncio.gather(*(self._loop(site) for site in self.sites))

    async def start(self) -> None:
        if self._task is not None or not self.sites:
            return
        await self.register()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
