"""In-memory per-channel state shared between the background probes
(live-video, recording query) and the ``/api/v1`` API handlers.

API handlers only ever read this store; they never trigger a network call
themselves. Background loops in ``background.py`` are the only writers.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional


def _iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()


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

    def _checked_at_ms(self) -> Optional[int]:
        candidates = [
            t for t in (self.live_checked_at_ms, self.recording_checked_at_ms) if t is not None
        ]
        return max(candidates) if candidates else None

    def _error_code(self) -> Optional[str]:
        # Live-video probe errors take priority since they run more often
        # (every 300s vs 900s) and are usually the more actionable signal.
        return self.live_error or self.recording_error

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

    async def update_recording(
        self,
        channel_id: int,
        *,
        recording_query_ok: bool,
        recording_recent: Optional[bool],
        last_recording: Optional[str],
        checked_at_ms: int,
        error_code: Optional[str],
    ) -> None:
        async with self._lock:
            state = self._get_or_create(channel_id)
            state.recording_query_ok = recording_query_ok
            state.recording_recent = recording_recent
            state.last_recording = last_recording
            state.recording_checked_at_ms = checked_at_ms
            state.recording_error = error_code

    def snapshot(self, channel_id: int) -> dict:
        """Read the latest known state for one channel (non-blocking,
        no lock -- dict attribute reads/writes are already atomic under
        the GIL and this is only ever read from the API handlers)."""
        state = self._states.get(channel_id)
        if state is None:
            return ChannelState(channel_id=channel_id).as_dict()
        return state.as_dict()

    def clear(self) -> None:
        self._states.clear()
