"""Bounded, memory-only live-video history owned by the HA integration."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable
from typing import Any


LIVE_WINDOW_MS = 24 * 60 * 60 * 1000
LIVE_SAMPLE_CAPACITY = 4096


def samples_from_recorder_states(states: Iterable[Any]) -> list[tuple[int, bool]]:
    """Convert Recorder states to live samples, excluding HA availability noise."""
    samples: list[tuple[int, bool]] = []
    for state in states:
        if state.state == "on":
            live_video = True
        elif state.state == "off":
            live_video = False
        else:
            # Integration reloads also produce unavailable/unknown states. They
            # are not NVR probes and must not create false disconnects.
            continue
        samples.append((int(state.last_updated_timestamp * 1000), live_video))
    return samples


class LiveHistoryStore:
    """Calculate rolling live aggregates without persistent storage."""

    def __init__(self) -> None:
        self._samples: dict[str, deque[tuple[int, bool | None]]] = defaultdict(
            lambda: deque(maxlen=LIVE_SAMPLE_CAPACITY)
        )

    def observe(
        self,
        camera_id: str,
        *,
        checked_at_ms: int,
        live_video: bool | None,
        now_ms: int,
    ) -> dict[str, int | float | None]:
        """Observe one distinct app probe and return rolling aggregates."""
        samples = self._samples[camera_id]
        if not samples or checked_at_ms > samples[-1][0]:
            samples.append((checked_at_ms, live_video))
        self._prune(samples, now_ms)
        return self._aggregates(samples, now_ms)

    def restore(
        self,
        camera_id: str,
        samples: Iterable[tuple[int, bool | None]],
        *,
        now_ms: int,
    ) -> dict[str, int | float | None]:
        """Restore a bounded window from Home Assistant Recorder states."""
        restored = self._samples[camera_id]
        restored.clear()
        for timestamp, live_video in sorted(samples, key=lambda item: item[0]):
            if restored and timestamp == restored[-1][0]:
                restored[-1] = (timestamp, live_video)
            elif not restored or timestamp > restored[-1][0]:
                restored.append((timestamp, live_video))
        self._prune(restored, now_ms)
        return self._aggregates(restored, now_ms)

    @staticmethod
    def _prune(samples: deque[tuple[int, bool | None]], now_ms: int) -> None:
        cutoff = now_ms - LIVE_WINDOW_MS
        # Retain one pre-window anchor so a transition into the window can be
        # classified without retaining older history.
        while len(samples) > 1 and samples[1][0] < cutoff:
            samples.popleft()

    @staticmethod
    def _aggregates(
        samples: deque[tuple[int, bool | None]], now_ms: int
    ) -> dict[str, int | float | None]:
        cutoff = now_ms - LIVE_WINDOW_MS
        relevant = list(samples)
        disconnects = sum(
            previous is True and current is not True and current_timestamp >= cutoff
            for (_previous_timestamp, previous), (current_timestamp, current) in zip(
                relevant, relevant[1:]
            )
        )
        known_duration = 0
        online_duration = 0
        for index, (timestamp, value) in enumerate(relevant):
            if type(value) is not bool:
                continue
            interval_start = max(timestamp, cutoff)
            interval_end = min(
                relevant[index + 1][0] if index + 1 < len(relevant) else now_ms,
                now_ms,
            )
            duration = max(0, interval_end - interval_start)
            known_duration += duration
            if value is True:
                online_duration += duration
        latest_known = next(
            (value for _timestamp, value in reversed(relevant) if type(value) is bool),
            None,
        )
        return {
            "daily_online_rate": (
                online_duration * 100 / known_duration
                if known_duration
                else (100.0 if latest_known is True else 0.0)
                if latest_known is not None
                else None
            ),
            "nvr_live_video_disconnect_count_24h": disconnects,
        }

    def clear(self) -> None:
        """Discard all volatile history without migration or persistence."""
        self._samples.clear()
