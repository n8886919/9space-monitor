"""Background NVR probe loops for the add-on.

Two independent loops run for the lifetime of the process:

- live-video probe: every LIVE_PROBE_INTERVAL_SECONDS (default 300s)
- recording query: every RECORDING_QUERY_INTERVAL_SECONDS (default 900s)

Both loops run their first round immediately on startup (no initial wait),
share a fixed single background concurrency slot, and never let one channel's
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
from constants import NVR_HTTP_PORT, NVR_RTSP_PORT
from channel_state import ChannelStateStore

_LOGGER = logging.getLogger(__name__)

LIVE_PROBE_INTERVAL_SECONDS = 300
RECORDING_QUERY_INTERVAL_SECONDS = 900

# First real-hardware release: live-video probing and recording queries
# share a single background concurrency slot (total background NVR
# operations = 1 at a time), independent of the snapshot ffmpeg
# legacy snapshot ``max_concurrency`` option. Callers create one
# ``asyncio.Semaphore`` with this value and pass the same instance to both
# loops below.
BACKGROUND_CONCURRENCY = 1


async def _bounded_gather(
    channel_ids: Iterable[int],
    worker: Callable[[int], Awaitable[None]],
    semaphore: asyncio.Semaphore,
    on_error: Optional[Callable[[int], Awaitable[None]]] = None,
) -> None:
    """Run ``worker(channel_id)`` for every channel, bounded by the shared
    ``semaphore`` so at most one background NVR operation (live probe or
    recording query, across both loops) runs at a time. One channel's
    exception never stops the others or this round; only a short,
    credential-free warning (exception type name, no message) is logged,
    and ``on_error`` (if given) records a safe, non-stale channel state."""

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
                if on_error is not None:
                    try:
                        await on_error(channel_id)
                    except Exception:  # noqa: BLE001 - state update must not abort the round
                        _LOGGER.warning(
                            "background probe error-state update failed for channel %s",
                            channel_id,
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
        first_packet_ms=result.get("nvr_first_packet_ms"),
        probe_duration_ms=result.get("nvr_probe_duration_ms"),
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
        metrics=result.get("metrics"),
    )


def _channel_count(opts: dict) -> int:
    return int(opts.get("channel_count") or 14)


async def live_probe_loop(
    store: ChannelStateStore,
    get_options: Callable[[], dict],
    semaphore: asyncio.Semaphore,
    interval_seconds: int = LIVE_PROBE_INTERVAL_SECONDS,
    ready_event: Optional[threading.Event] = None,
) -> None:
    """Run the live-video probe for every channel, forever. The first round
    runs immediately (no initial sleep). ``semaphore`` must be the same
    instance passed to ``recording_query_loop`` so both loops share one
    background concurrency budget. ``ready_event`` (if given) is set once
    the first round finishes, so tests can wait for it deterministically.
    Must be cancelled and awaited on shutdown."""
    first = True
    while True:
        opts = get_options()
        nvr = live_probe.NvrConfig(
            host=str(opts.get("nvr_host") or "127.0.0.1"),
            port=NVR_RTSP_PORT,
            username=str(opts.get("username") or "admin"),
            password=str(opts.get("password") or ""),
        )

        async def _on_error(channel_id: int) -> None:
            await store.mark_live_internal_error(
                channel_id, checked_at_ms=int(time.time() * 1000)
            )

        await _bounded_gather(
            range(1, _channel_count(opts) + 1),
            lambda cid: _live_probe_one(cid, nvr, store),
            semaphore,
            on_error=_on_error,
        )
        if first and ready_event is not None:
            ready_event.set()
        first = False
        await asyncio.sleep(interval_seconds)


async def recording_query_loop(
    store: ChannelStateStore,
    get_options: Callable[[], dict],
    semaphore: asyncio.Semaphore,
    interval_seconds: int = RECORDING_QUERY_INTERVAL_SECONDS,
    ready_event: Optional[threading.Event] = None,
) -> None:
    """Run the recording query for every channel, forever. The first round
    runs immediately (no initial sleep). ``semaphore`` must be the same
    instance passed to ``live_probe_loop`` so both loops share one
    background concurrency budget. ``ready_event`` (if given) is set once
    the first round finishes, so tests can wait for it deterministically.
    Must be cancelled and awaited on shutdown."""
    first = True
    while True:
        opts = get_options()
        nvr = recording_query.NvrHttpConfig(
            host=str(opts.get("nvr_host") or "127.0.0.1"),
            http_port=NVR_HTTP_PORT,
            username=str(opts.get("username") or "admin"),
            password=str(opts.get("password") or ""),
        )

        async def _on_error(channel_id: int) -> None:
            await store.mark_recording_internal_error(
                channel_id, checked_at_ms=int(time.time() * 1000)
            )

        await _bounded_gather(
            range(1, _channel_count(opts) + 1),
            lambda cid: _recording_query_one(cid, nvr, store),
            semaphore,
            on_error=_on_error,
        )
        if first and ready_event is not None:
            ready_event.set()
        first = False
        await asyncio.sleep(interval_seconds)
