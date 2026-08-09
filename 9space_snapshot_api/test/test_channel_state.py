"""Regression tests for local-only 24-hour channel aggregates."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ADDON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_DIR))

from channel_state import ChannelStateStore  # noqa: E402


HOUR_MS = 60 * 60 * 1000


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
            },
        )

        snapshot = store.snapshot(1)
        self.assertEqual(snapshot["recording_files_24h"], 10)
        self.assertEqual(snapshot["recording_coverage_24h"], 91.25)

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

    async def test_disconnect_requires_online_to_non_online_transition(self) -> None:
        store = ChannelStateStore()
        await store.update_live(1, live_video=False, checked_at_ms=1000, error_code="no_video")
        await store.update_live(1, live_video=True, checked_at_ms=2000, error_code=None)
        await store.update_live(1, live_video=False, checked_at_ms=3000, error_code="no_video")
        await store.update_live(1, live_video=False, checked_at_ms=4000, error_code="no_video")
        await store.update_live(1, live_video=True, checked_at_ms=5000, error_code=None)
        await store.update_live(1, live_video=None, checked_at_ms=6000, error_code="rtsp_timeout")

        snapshot = store.snapshot(1, now_ms=6000)
        self.assertEqual(snapshot["nvr_live_video_disconnect_count_24h"], 2)
        self.assertEqual(snapshot["daily_online_rate"], 40.0)

    async def test_disconnect_older_than_24_hours_is_excluded(self) -> None:
        store = ChannelStateStore()
        await store.update_live(1, live_video=True, checked_at_ms=0, error_code=None)
        await store.update_live(
            1, live_video=False, checked_at_ms=HOUR_MS // 2, error_code="no_video"
        )
        await store.update_live(1, live_video=True, checked_at_ms=25 * HOUR_MS, error_code=None)

        snapshot = store.snapshot(1, now_ms=25 * HOUR_MS)
        self.assertEqual(snapshot["nvr_live_video_disconnect_count_24h"], 0)
        self.assertEqual(snapshot["daily_online_rate"], 100.0)


if __name__ == "__main__":
    unittest.main()
