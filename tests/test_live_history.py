"""Tests for integration-owned, volatile live-video aggregates."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest


PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components/nine_space_nvr_monitor/live_history.py"
)
SPEC = importlib.util.spec_from_file_location("nine_space_nvr_monitor_live_history_test", PATH)
assert SPEC and SPEC.loader
live_history = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = live_history
SPEC.loader.exec_module(live_history)

HOUR_MS = 60 * 60 * 1000


class LiveHistoryStoreTests(unittest.TestCase):
    def test_counts_only_online_to_non_online_transitions(self) -> None:
        store = live_history.LiveHistoryStore()
        states = (False, True, False, False, True, None)
        result = {}
        for index, state in enumerate(states, start=1):
            result = store.observe(
                "camera-1",
                checked_at_ms=index * 1000,
                live_video=state,
                now_ms=index * 1000,
            )

        self.assertEqual(2, result["nvr_live_video_disconnect_count_24h"])
        self.assertEqual(40.0, result["daily_online_rate"])

    def test_duplicate_and_older_probe_timestamps_are_ignored(self) -> None:
        store = live_history.LiveHistoryStore()
        store.observe("camera-1", checked_at_ms=1000, live_video=True, now_ms=1000)
        store.observe("camera-1", checked_at_ms=1000, live_video=False, now_ms=2000)
        result = store.observe(
            "camera-1", checked_at_ms=500, live_video=False, now_ms=3000
        )

        self.assertEqual(0, result["nvr_live_video_disconnect_count_24h"])
        self.assertEqual(100.0, result["daily_online_rate"])

    def test_samples_older_than_24_hours_are_discarded(self) -> None:
        store = live_history.LiveHistoryStore()
        store.observe("camera-1", checked_at_ms=0, live_video=True, now_ms=0)
        store.observe(
            "camera-1",
            checked_at_ms=HOUR_MS // 2,
            live_video=False,
            now_ms=HOUR_MS // 2,
        )
        result = store.observe(
            "camera-1",
            checked_at_ms=25 * HOUR_MS,
            live_video=True,
            now_ms=25 * HOUR_MS,
        )

        self.assertEqual(0, result["nvr_live_video_disconnect_count_24h"])
        self.assertEqual(0.0, result["daily_online_rate"])

    def test_restore_rebuilds_time_weighted_window_and_disconnects(self) -> None:
        store = live_history.LiveHistoryStore()
        result = store.restore(
            "camera-1",
            [
                (0, True),
                (12 * HOUR_MS, False),
            ],
            now_ms=24 * HOUR_MS,
        )

        self.assertEqual(1, result["nvr_live_video_disconnect_count_24h"])
        self.assertEqual(50.0, result["daily_online_rate"])

    def test_restore_deduplicates_timestamps_and_observe_continues(self) -> None:
        store = live_history.LiveHistoryStore()
        store.restore(
            "camera-1",
            [(1000, True), (2000, True), (2000, False)],
            now_ms=3000,
        )
        result = store.observe(
            "camera-1", checked_at_ms=4000, live_video=True, now_ms=5000
        )

        self.assertEqual(1, result["nvr_live_video_disconnect_count_24h"])
        self.assertEqual(50.0, result["daily_online_rate"])

    def test_recorder_samples_ignore_non_probe_availability_states(self) -> None:
        states = [
            SimpleNamespace(state="on", last_updated_timestamp=1.0),
            SimpleNamespace(state="unavailable", last_updated_timestamp=2.0),
            SimpleNamespace(state="off", last_updated_timestamp=3.0),
            SimpleNamespace(state="unknown", last_updated_timestamp=4.0),
        ]

        self.assertEqual(
            [(1000, True), (3000, False)],
            live_history.samples_from_recorder_states(states),
        )

    def test_clear_drops_old_data_without_migration(self) -> None:
        store = live_history.LiveHistoryStore()
        store.observe("camera-1", checked_at_ms=1000, live_video=True, now_ms=1000)
        store.clear()
        result = store.observe(
            "camera-1", checked_at_ms=2000, live_video=False, now_ms=2000
        )

        self.assertEqual(0, result["nvr_live_video_disconnect_count_24h"])
        self.assertEqual(0.0, result["daily_online_rate"])


if __name__ == "__main__":
    unittest.main()
