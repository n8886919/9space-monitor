"""In-memory per-channel state shared between the background probes
(live-video, recording query) and the ``/api/v1`` API handlers.

API handlers only ever read this store; they never trigger a network call
themselves. Background loops in ``background.py`` are the only writers.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from typing import Dict, Mapping, Optional


_LIVE_WINDOW_MS = 24 * 60 * 60 * 1000
_LIVE_SAMPLE_CAPACITY = 4096


def _iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()


# Fixed error-code priority (highest first). A higher-priority code must
# never be shadowed by a lower-priority one; codes not in this list (should
# not normally happen) are treated as lowest priority.
_ERROR_PRIORITY = [
    "authentication_failed",
    "internal_error",
    "recording_query_failed",
    "nvr_unreachable",
    "rtsp_timeout",
    "no_video",
]


@dataclass
class ChannelState:
    channel_id: int
    live_video: Optional[bool] = None
    live_checked_at_ms: Optional[int] = None
    live_error: Optional[str] = None

    recording_query_ok: bool = False
    recording_recent: Optional[bool] = None
    last_recording: Optional[str] = None
    recording_checked_at_ms: Optional[int] = None
    recording_error: Optional[str] = None
    recording_metrics: dict[str, int | float | bool] = field(default_factory=dict)

    def _checked_at_ms(self) -> Optional[int]:
        candidates = [
            t for t in (self.live_checked_at_ms, self.recording_checked_at_ms) if t is not None
        ]
        return max(candidates) if candidates else None

    def _error_code(self) -> Optional[str]:
        # Explicit fixed priority (see ``_ERROR_PRIORITY``): a higher
        # priority error must never be shadowed by a lower priority one.
        # Same priority -> the source with the newer timestamp wins.
        candidates = []
        if self.live_error:
            candidates.append((self.live_error, self.live_checked_at_ms or -1))
        if self.recording_error:
            candidates.append((self.recording_error, self.recording_checked_at_ms or -1))
        if not candidates:
            return None

        def _rank(code: str) -> int:
            try:
                return _ERROR_PRIORITY.index(code)
            except ValueError:
                return len(_ERROR_PRIORITY)

        best_rank = min(_rank(code) for code, _ts in candidates)
        top = [item for item in candidates if _rank(item[0]) == best_rank]
        top.sort(key=lambda item: item[1], reverse=True)
        return top[0][0]

    def as_dict(self) -> dict:
        checked_ms = self._checked_at_ms()
        return {
            "live_video": self.live_video,
            "recording_query_ok": self.recording_query_ok,
            "recording_recent": self.recording_recent,
            "last_recording": self.last_recording,
            "checked_at": _iso(checked_ms) if checked_ms is not None else None,
            "error_code": self._error_code(),
        }


class ChannelStateStore:
    """Async-safe in-memory store for background probe results."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._states: Dict[int, ChannelState] = {}
        self._live_samples: dict[int, deque[tuple[int, Optional[bool]]]] = defaultdict(
            lambda: deque(maxlen=_LIVE_SAMPLE_CAPACITY)
        )

    def _observe_live(
        self, channel_id: int, checked_at_ms: int, live_video: Optional[bool]
    ) -> None:
        samples = self._live_samples[channel_id]
        cutoff = checked_at_ms - _LIVE_WINDOW_MS
        while len(samples) > 1 and samples[1][0] < cutoff:
            samples.popleft()
        samples.append((checked_at_ms, live_video))

    def _live_aggregates(self, channel_id: int, now_ms: int) -> dict[str, int | float | None]:
        samples = self._live_samples.get(channel_id)
        if not samples:
            return {
                "daily_online_rate": None,
                "nvr_live_video_disconnect_count_24h": 0,
            }
        cutoff = now_ms - _LIVE_WINDOW_MS
        relevant = list(samples)
        observed = [value for timestamp, value in relevant if timestamp >= cutoff]
        known = [value for value in observed if type(value) is bool]
        disconnects = sum(
            previous is True and current is not True and current_timestamp >= cutoff
            for (_previous_timestamp, previous), (current_timestamp, current) in zip(
                relevant, relevant[1:]
            )
        )
        return {
            "daily_online_rate": (
                sum(value is True for value in known) * 100 / len(known) if known else None
            ),
            "nvr_live_video_disconnect_count_24h": disconnects,
        }

    def _get_or_create(self, channel_id: int) -> ChannelState:
        state = self._states.get(channel_id)
        if state is None:
            state = ChannelState(channel_id=channel_id)
            self._states[channel_id] = state
        return state

    async def update_live(
        self,
        channel_id: int,
        *,
        live_video: Optional[bool],
        checked_at_ms: int,
        error_code: Optional[str],
    ) -> None:
        async with self._lock:
            state = self._get_or_create(channel_id)
            state.live_video = live_video
            state.live_checked_at_ms = checked_at_ms
            state.live_error = error_code
            self._observe_live(channel_id, checked_at_ms, live_video)

    async def update_recording(
        self,
        channel_id: int,
        *,
        recording_query_ok: bool,
        recording_recent: Optional[bool],
        last_recording: Optional[str],
        checked_at_ms: int,
        error_code: Optional[str],
        metrics: Mapping[str, int | float | bool] | None = None,
    ) -> None:
        async with self._lock:
            state = self._get_or_create(channel_id)
            state.recording_query_ok = recording_query_ok
            state.recording_recent = recording_recent
            state.last_recording = last_recording
            state.recording_checked_at_ms = checked_at_ms
            state.recording_error = error_code
            state.recording_metrics = dict(metrics) if recording_query_ok and metrics else {}

    async def mark_live_internal_error(
        self, channel_id: int, *, checked_at_ms: int, error_code: str = "internal_error"
    ) -> None:
        """Record an unexpected (non-probe) exception in the live-video
        worker. Must never leave a stale ``live_video=True`` in place."""
        async with self._lock:
            state = self._get_or_create(channel_id)
            state.live_video = False
            state.live_checked_at_ms = checked_at_ms
            state.live_error = error_code
            self._observe_live(channel_id, checked_at_ms, False)

    async def mark_recording_internal_error(
        self, channel_id: int, *, checked_at_ms: int, error_code: str = "internal_error"
    ) -> None:
        """Record an unexpected (non-query) exception in the recording
        worker. Must never leave a stale ``recording_query_ok=True`` or
        ``recording_recent=True`` in place."""
        async with self._lock:
            state = self._get_or_create(channel_id)
            state.recording_query_ok = False
            state.recording_recent = None
            state.last_recording = None
            state.recording_checked_at_ms = checked_at_ms
            state.recording_error = error_code
            state.recording_metrics = {}

    def snapshot(self, channel_id: int, *, now_ms: int | None = None) -> dict:
        """Read the latest known state for one channel (non-blocking,
        no lock -- dict attribute reads/writes are already atomic under
        the GIL and this is only ever read from the API handlers)."""
        state = self._states.get(channel_id)
        result = (
            ChannelState(channel_id=channel_id).as_dict()
            if state is None
            else state.as_dict()
        )
        metrics = state.recording_metrics if state is not None else {}
        result.update(
            {
                "recording_files_24h": metrics.get("valid_file_count_24h"),
                "recording_coverage_24h": metrics.get("recording_coverage_24h_pct"),
            }
        )
        aggregate_now = now_ms
        if aggregate_now is None:
            aggregate_now = int(time.time() * 1000)
        result.update(self._live_aggregates(channel_id, aggregate_now))
        return result

    def telemetry_snapshot(self, channel_id: int) -> dict:
        """Return source-specific state for the internal telemetry worker.

        Unlike ``snapshot()``, this deliberately does not merge error codes:
        the Center's ``nvr.live`` and ``nvr.recording`` events must retain
        their own NVR-operation provenance.  It is not exposed by any API.
        """
        state = self._states.get(channel_id)
        if state is None:
            state = ChannelState(channel_id=channel_id)
        return {
            "live": {
                "live_video": state.live_video,
                "error_code": state.live_error,
            },
            "recording": {
                "recording_query_ok": state.recording_query_ok,
                "recording_recent": state.recording_recent,
                "last_recording": state.last_recording,
                "error_code": state.recording_error,
                "metrics": dict(state.recording_metrics),
            },
        }

    def clear(self) -> None:
        self._states.clear()
        self._live_samples.clear()
