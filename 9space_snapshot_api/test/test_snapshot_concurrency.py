"""Regression test for the add-on's hard Snapshot capture cap."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ADDON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_DIR))

import background  # noqa: E402
import main  # noqa: E402


class SnapshotConcurrencyTests(unittest.TestCase):
    def test_high_legacy_option_still_allows_only_one_capture(self) -> None:
        """Capture activity, rather than semaphore internals, proves the cap."""

        async def scenario() -> int:
            main._cache.clear()
            main._sem = None
            active = 0
            peak = 0

            async def idle_loop(*_args, **_kwargs) -> None:
                await asyncio.Event().wait()

            async def fake_grab(*_args) -> tuple[bool, int, bytes, str]:
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.02)
                active -= 1
                return True, 1, b"jpeg", "decoded 1 frame"

            options = {
                "health_timeout_ms": 100,
                "jpeg_qv": 32,
                "snapshot_cache_ms": 0,
                "max_concurrency": 8,
            }
            with patch.object(main, "_load_options", return_value=options), patch.object(
                background, "live_probe_loop", side_effect=idle_loop
            ), patch.object(
                background, "recording_query_loop", side_effect=idle_loop
            ), patch.object(main, "_ffmpeg_grab_jpeg", side_effect=fake_grab):
                await main._startup()
                try:
                    await asyncio.gather(
                        main._capture_snapshot("1"), main._capture_snapshot("2")
                    )
                finally:
                    await main._shutdown()
            return peak

        self.assertEqual(asyncio.run(scenario()), 1)
