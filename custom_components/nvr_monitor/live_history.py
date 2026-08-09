"""Bounded, memory-only live-video history owned by the HA integration."""

from __future__ import annotations

from collections import defaultdict, deque


LIVE_WINDOW_MS = 24 * 60 * 60 * 1000
LIVE_SAMPLE_CAPACITY = 4096


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
        """Observe one distinct add-on probe and return rolling aggregates."""
        samples = self._samples[camera_id]
        if not samples or checked_at_ms > samples[-1][0]:
            samples.append((checked_at_ms, live_video))
        self._prune(samples, now_ms)
        return self._aggregates(samples, now_ms)

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
                sum(value is True for value in known) * 100 / len(known)
                if known
                else None
            ),
            "nvr_live_video_disconnect_count_24h": disconnects,
        }

    def clear(self) -> None:
        """Discard all volatile history without migration or persistence."""
        self._samples.clear()
