"""Background NVR probe loops for the add-on.

Two independent loops run for the lifetime of the process:

- live-video probe: every LIVE_PROBE_INTERVAL_SECONDS (default 300s)
- recording query: every RECORDING_QUERY_INTERVAL_SECONDS (default 900s)

Both loops run their first round immediately on startup (no initial wait),
bound concurrency so at most a few channels are probed at once (reusing the
add-on's existing ``max_concurrency`` option), and never let one channel's
failure stop the rest of the round or crash the loop itself. API handlers
never call into these modules directly; they only read ``ChannelStateStore``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Awaitable, Callable, Iterable, Optional

import live_probe
import recording_query
from channel_state import ChannelStateStore

_LOGGER = logging.getLogger(__name__)

LIVE_PROBE_INTERVAL_SECONDS = 300
RECORDING_QUERY_INTERVAL_SECONDS = 900


async def _bounded_gather(
    channel_ids: Iterable[int],
    worker: Callable[[int], Awaitable[None]],
    max_concurrency: int,
) -> None:
    """Run ``worker(channel_id)`` for every channel, bounded by a semaphore
    so at most ``max_concurrency`` channels are probed at once. One
    channel's exception never stops the others or this round; only a
    short, credential-free warning (exception type name) is logged."""
    semaphore = asyncio.Semaphore(max(1, max_concurrency))

    async def _run_one(channel_id: int) -> None:
        async with semaphore:
            try:
                await worker(channel_id)
            except Exception as err:  # noqa: BLE001 - must never abort the round
                _LOGGER.warning(
                    "background probe failed for channel %s: %s",
                    channel_id,
                    type(err).__name__,
                )

    await asyncio.gather(*(_run_one(cid) for cid in channel_ids))


async def _live_probe_one(
    channel_id: int, nvr: live_probe.NvrConfig, store: ChannelStateStore
) -> None:
    result = await asyncio.to_thread(live_probe.probe_channel, channel_id, nvr)
    await store.update_live(
        channel_id,
        live_video=result["live_video"],
        checked_at_ms=int(time.time() * 1000),
        error_code=result["error_code"],
    )


async def _recording_query_one(
    channel_id: int, nvr: recording_query.NvrHttpConfig, store: ChannelStateStore
) -> None:
    result = await asyncio.to_thread(recording_query.query_channel, channel_id, nvr)
    await store.update_recording(
        channel_id,
        recording_query_ok=result["recording_query_ok"],
        recording_recent=result["recording_recent"],
        last_recording=result["last_recording"],
        checked_at_ms=int(time.time() * 1000),
        error_code=result["error_code"],
    )


def _channel_count(opts: dict) -> int:
    return int(opts.get("channel_count") or 14)


def _max_concurrency(opts: dict) -> int:
    return int(opts.get("max_concurrency") or 2)


async def live_probe_loop(
    store: ChannelStateStore,
    get_options: Callable[[], dict],
    interval_seconds: int = LIVE_PROBE_INTERVAL_SECONDS,
    ready_event: Optional[threading.Event] = None,
) -> None:
    """Run the live-video probe for every channel, forever. The first round
    runs immediately (no initial sleep). ``ready_event`` (if given) is set
    once the first round finishes, so tests can wait for it deterministically.
    Must be cancelled and awaited on shutdown."""
    first = True
    while True:
        opts = get_options()
        nvr = live_probe.NvrConfig(
            host=str(opts.get("nvr_host") or "127.0.0.1"),
            port=int(opts.get("rtsp_port") or 554),
            username=str(opts.get("username") or "admin"),
            password=str(opts.get("password") or ""),
        )
        await _bounded_gather(
            range(1, _channel_count(opts) + 1),
            lambda cid: _live_probe_one(cid, nvr, store),
            _max_concurrency(opts),
        )
        if first and ready_event is not None:
            ready_event.set()
        first = False
        await asyncio.sleep(interval_seconds)


async def recording_query_loop(
    store: ChannelStateStore,
    get_options: Callable[[], dict],
    interval_seconds: int = RECORDING_QUERY_INTERVAL_SECONDS,
    ready_event: Optional[threading.Event] = None,
) -> None:
    """Run the recording query for every channel, forever. The first round
    runs immediately (no initial sleep). ``ready_event`` (if given) is set
    once the first round finishes, so tests can wait for it deterministically.
    Must be cancelled and awaited on shutdown."""
    first = True
    while True:
        opts = get_options()
        nvr = recording_query.NvrHttpConfig(
            host=str(opts.get("nvr_host") or "127.0.0.1"),
            http_port=int(opts.get("nvr_http_port") or 80),
            username=str(opts.get("username") or "admin"),
            password=str(opts.get("password") or ""),
        )
        await _bounded_gather(
            range(1, _channel_count(opts) + 1),
            lambda cid: _recording_query_one(cid, nvr, store),
            _max_concurrency(opts),
        )
        if first and ready_event is not None:
            ready_event.set()
        first = False
        await asyncio.sleep(interval_seconds)
