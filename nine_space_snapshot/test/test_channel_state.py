"""Regression tests for app latest-only channel state."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ADDON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_DIR))

from channel_state import ChannelStateStore  # noqa: E402


class ChannelStateStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_recording_metrics_expose_valid_files_and_coverage(self) -> None:
        store = ChannelStateStore()
        await store.update_recording(
            1,
            recording_query_ok=True,
            recording_recent=True,
            last_recording="2026-08-09T00:00:00+00:00",
            checked_at_ms=1000,
            error_code=None,
            metrics={
                "file_count_24h": 12,
                "valid_file_count_24h": 10,
                "recording_coverage_24h_pct": 91.25,
                "gap_count_24h": 3,
                "gap_total_seconds_24h": 120.5,
                "largest_gap_seconds_24h": 60.25,
            },
        )

        snapshot = store.snapshot(1)
        self.assertEqual(snapshot["recording_files_24h"], 10)
        self.assertEqual(snapshot["recording_coverage_24h"], 91.25)
        self.assertEqual(snapshot["recording_gap_count_24h"], 3)
        self.assertEqual(snapshot["recording_gap_total_seconds_24h"], 120.5)
        self.assertEqual(snapshot["largest_recording_gap_seconds_24h"], 60.25)

        await store.update_recording(
            1,
            recording_query_ok=False,
            recording_recent=None,
            last_recording=None,
            checked_at_ms=2000,
            error_code="recording_query_failed",
        )
        snapshot = store.snapshot(1)
        self.assertIsNone(snapshot["recording_files_24h"])
        self.assertIsNone(snapshot["recording_coverage_24h"])
        self.assertIsNone(snapshot["recording_gap_count_24h"])
        self.assertIsNone(snapshot["recording_gap_total_seconds_24h"])
        self.assertIsNone(snapshot["largest_recording_gap_seconds_24h"])

    async def test_live_state_exposes_probe_time_without_history(self) -> None:
        store = ChannelStateStore()
        await store.update_live(
            1,
            live_video=False,
            checked_at_ms=1000,
            error_code="no_video",
            first_packet_ms=250.5,
            probe_duration_ms=3250.75,
        )

        snapshot = store.snapshot(1)
        self.assertEqual(snapshot["live_checked_at"], "1970-01-01T00:00:01+00:00")
        self.assertNotIn("nvr_live_video_disconnect_count_24h", snapshot)
        self.assertNotIn("daily_online_rate", snapshot)
        self.assertEqual(snapshot["nvr_first_packet_ms"], 250.5)
        self.assertEqual(snapshot["nvr_probe_duration_ms"], 3250.75)


if __name__ == "__main__":
    unittest.main()
