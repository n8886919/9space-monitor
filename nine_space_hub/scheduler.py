"""Bounded site configuration and last-good snapshot refresh scheduler."""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from .snapshots import DEFAULT_MAX_SNAPSHOT_BYTES, SnapshotStore, validate_camera_id

MAX_CONFIG_BYTES = 128 * 1024


@dataclass(frozen=True)
class SnapshotSite:
    site_id: str
    display_name: str
    base_url: str
    channels: tuple[int, ...]
    concurrency: int
    timeout_seconds: int
    refresh_seconds: int


class AttemptSink(Protocol):
    def record_snapshot_attempt(
        self,
        site_id: str,
        camera_id: int,
        *,
        success: bool,
        timestamp_ms: int,
        latency_ms: float,
        error_code: str | None,
    ) -> None: ...


def load_options(path: str | Path = "/data/options.json") -> tuple[int, int, int]:
    """Load validated Supervisor options without logging private site URLs."""
    try:
        with Path(path).open("rb") as handle:
            raw_bytes = handle.read(MAX_CONFIG_BYTES + 1)
    except FileNotFoundError:
        return 120, 1024 * 1024 * 1024, 30
    if len(raw_bytes) > MAX_CONFIG_BYTES:
        raise ValueError("hub_options_too_large")
    try:
        raw = json.loads(raw_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_hub_options") from exc
    required = {"max_stale_seconds", "snapshot_store_limit_mb"}
    allowed = required | {"sites", "snapshot_refresh_seconds"}
    if (
        not isinstance(raw, dict)
        or not required.issubset(raw)
        or not set(raw).issubset(allowed)
    ):
        raise ValueError("invalid_hub_options")
    stale = raw["max_stale_seconds"]
    limit_mb = raw["snapshot_store_limit_mb"]
    refresh = raw.get("snapshot_refresh_seconds", 30)
    if type(stale) is not int or not 0 <= stale <= 86400:
        raise ValueError("invalid_max_stale_seconds")
    if type(limit_mb) is not int or not 8 <= limit_mb <= 8192:
        raise ValueError("invalid_snapshot_store_limit")
    if type(refresh) is not int or not 5 <= refresh <= 86400:
        raise ValueError("invalid_snapshot_refresh_seconds")
    return stale, limit_mb * 1024 * 1024, refresh


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
                return (
                    response.status,
                    response.headers.get_content_type(),
                    response.read(DEFAULT_MAX_SNAPSHOT_BYTES + 1),
                )
        except urllib.error.HTTPError as error:
            return error.code, "", b""
    return await asyncio.to_thread(fetch)


class SnapshotScheduler:
    def __init__(
        self,
        sites: tuple[SnapshotSite, ...],
        state: AttemptSink,
        snapshots: SnapshotStore,
        *,
        fetcher: Callable[[str, int], Awaitable[tuple[int, str, bytes]]] = default_fetch,
        run_sync: Callable[..., Awaitable[Any]] = asyncio.to_thread,
    ) -> None:
        self.sites = {site.site_id: site for site in sites}
        self.state = state
        self.snapshots = snapshots
        self.fetcher = fetcher
        self.run_sync = run_sync
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._running = False

    async def run_round(self, site: SnapshotSite) -> None:
        for start in range(0, len(site.channels), site.concurrency):
            batch = site.channels[start:start + site.concurrency]
            await asyncio.gather(*(self._attempt(site, channel) for channel in batch))

    async def _attempt(self, site: SnapshotSite, channel: int) -> None:
        started = time.perf_counter()
        success, code = False, "internal_error"
        try:
            url = f"{site.base_url}/api/v1/channels/{channel}/snapshot"
            status, content_type, body = await asyncio.wait_for(
                self.fetcher(url, site.timeout_seconds), timeout=site.timeout_seconds
            )
            if status != 200:
                code = "snapshot_unavailable"
            elif len(body) > DEFAULT_MAX_SNAPSHOT_BYTES:
                code = "snapshot_too_large"
            elif content_type.lower().split(";", 1)[0].strip() != "image/jpeg" or not body:
                code = "invalid_snapshot_response"
            else:
                await self.run_sync(self.snapshots.write, site.site_id, channel, body)
                success, code = True, None
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            code = "snapshot_timeout"
        except Exception:
            code = "snapshot_fetch_failed"
        now_ms = int(time.time() * 1000)
        self.state.record_snapshot_attempt(
            site.site_id,
            channel,
            success=success,
            timestamp_ms=now_ms,
            latency_ms=(time.perf_counter() - started) * 1000,
            error_code=code,
        )

    async def _loop(self, site: SnapshotSite) -> None:
        while True:
            await self.run_round(site)
            await asyncio.sleep(site.refresh_seconds)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        for site in tuple(self.sites.values()):
            await self.upsert(site)

    async def upsert(self, site: SnapshotSite) -> None:
        previous = self.sites.get(site.site_id)
        self.sites[site.site_id] = site
        if not self._running or previous == site and site.site_id in self._tasks:
            return
        old = self._tasks.pop(site.site_id, None)
        if old is not None:
            old.cancel()
            try:
                await old
            except asyncio.CancelledError:
                pass
        self._tasks[site.site_id] = asyncio.create_task(self._loop(site))

    async def stop(self) -> None:
        self._running = False
        tasks, self._tasks = tuple(self._tasks.values()), {}
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
