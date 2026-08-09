"""Regression tests for bounded site-configured Snapshot capture concurrency."""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ADDON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_DIR))

import background  # noqa: E402
import main  # noqa: E402


class SnapshotConcurrencyTests(unittest.TestCase):
    def test_hub_registration_reuses_local_snapshot_limits(self) -> None:
        registration = main._hub_snapshot_registration({
            "hub_snapshot_base_url": "http://100.64.0.10:8222/",
            "hub_snapshot_refresh_seconds": 45,
            "channel_count": 3,
            "max_concurrency": 2,
            "health_timeout_ms": 1200,
        })
        self.assertEqual(registration, {
            "base_url": "http://100.64.0.10:8222",
            "channels": [1, 2, 3],
            "concurrency": 2,
            "timeout_seconds": 7,
            "refresh_seconds": 45,
        })

    def test_hub_registration_is_optional_and_bad_bounds_fall_back(self) -> None:
        self.assertIsNone(main._hub_snapshot_registration({"channel_count": 3}))
        registration = main._hub_snapshot_registration({
            "hub_snapshot_base_url": "https://site.example.ts.net",
            "hub_snapshot_refresh_seconds": True,
            "channel_count": 1,
            "max_concurrency": 999,
            "health_timeout_ms": "bad",
        })
        self.assertEqual(registration["concurrency"], 8)
        self.assertEqual(registration["timeout_seconds"], 15)
        self.assertEqual(registration["refresh_seconds"], 30)

    def _peak_for_option(self, option) -> int:
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
                "max_concurrency": option,
            }
            with patch.object(main, "_load_options", return_value=options), patch.object(
                background, "live_probe_loop", side_effect=idle_loop
            ), patch.object(
                background, "recording_query_loop", side_effect=idle_loop
            ), patch.object(main, "_ffmpeg_grab_jpeg", side_effect=fake_grab):
                await main._startup()
                try:
                    await asyncio.gather(
                        *(main._capture_snapshot(str(channel)) for channel in range(1, 9))
                    )
                finally:
                    await main._shutdown()
            return peak

        return asyncio.run(scenario())

    def test_site_option_allows_four_parallel_captures(self) -> None:
        self.assertEqual(self._peak_for_option(4), 4)

    def test_excessive_site_option_is_runtime_clamped(self) -> None:
        self.assertEqual(self._peak_for_option(999), main.MAX_SNAPSHOT_CONCURRENCY)

    def test_invalid_or_non_positive_site_option_falls_back_to_one(self) -> None:
        for value in (0, -1, True, False, "4", 4.0, None):
            with self.subTest(value=value):
                self.assertEqual(self._peak_for_option(value), 1)

    def test_direct_legacy_busy_contract_avoids_testclient_portal(self) -> None:
        async def scenario():
            main._cache.clear()
            main._sem = asyncio.Semaphore(0)
            with patch.object(main, "QUEUE_TIMEOUT_MS", 1):
                return await main.camera_status_and_snapshot("1")
        response = asyncio.run(scenario())
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            json.loads(response.body),
            {"camera_id": "1", "ok": False, "latency_ms": 0, "detail": "busy"},
        )

    def test_legacy_multipart_shape_stays_unchanged(self) -> None:
        response = main._make_response("1", True, 1, "decoded 1 frame", b"opaque")
        self.assertTrue(response.media_type.startswith("multipart/mixed"))
        self.assertIn(b"Content-Type: application/json", response.body)
        self.assertIn(b"Content-Type: image/jpeg", response.body)
