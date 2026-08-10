"""Bounded snapshot scheduler tests without persistent statistics."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest

from nine_space_hub.scheduler import SnapshotScheduler, SnapshotSite, load_options
from nine_space_hub.snapshots import SnapshotStore
from nine_space_hub.state import CurrentState


class SchedulerTests(unittest.TestCase):
    def test_options_only_contain_global_hub_limits(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "options.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"max_stale_seconds": 120, "snapshot_store_limit_mb": 64}, handle)
            self.assertEqual(load_options(path), (120, 64 * 1024 * 1024, 30))
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"sites": [], "max_stale_seconds": 120, "snapshot_store_limit_mb": 64}, handle)
            self.assertEqual(load_options(path), (120, 64 * 1024 * 1024, 30))
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({
                    "max_stale_seconds": 120,
                    "snapshot_store_limit_mb": 64,
                    "snapshot_refresh_seconds": 45,
                }, handle)
            self.assertEqual(load_options(path), (120, 64 * 1024 * 1024, 45))

    def test_runtime_registration_is_bounded(self):
        sites = tuple(
            SnapshotSite(f"site-{index}", "Safe", "http://example.invalid", (1,), 1, 2, 5)
            for index in range(32)
        )
        state = CurrentState(sites)
        self.assertFalse(state.register(
            SnapshotSite("site-overflow", "Safe", "http://example.invalid", (1,), 1, 2, 5)
        ))
        self.assertTrue(state.register(sites[0]))

    def test_batches_thirteen_channels_four_four_four_one(self):
        with tempfile.TemporaryDirectory() as root:
            site = SnapshotSite("safe-site", "Safe", "http://example.invalid", tuple(range(1, 14)), 4, 2, 5)
            state = CurrentState((site,)); store = SnapshotStore(os.path.join(root, "snap"))
            active = peak = 0; starts = []

            async def fetch(url, _timeout):
                nonlocal active, peak
                self.assertNotIn("/api/camera/", url)
                active += 1; peak = max(peak, active); starts.append(active)
                await asyncio.sleep(0); active -= 1
                return 200, "image/jpeg", b"opaque"

            async def immediate(function, *args, **kwargs):
                return function(*args, **kwargs)

            scheduler = SnapshotScheduler((site,), state, store, fetcher=fetch, run_sync=immediate)
            asyncio.run(scheduler.run_round(site))
            self.assertEqual(peak, 4)
            self.assertEqual(starts, [1, 2, 3, 4] * 3 + [1])
            summary = state.sites(store, max_stale_seconds=120)
            self.assertEqual(sum(camera["latest_attempt"] is not None for camera in summary[0]["cameras"]), 13)

    def test_failed_attempt_preserves_last_good_and_replaces_only_ram_status(self):
        with tempfile.TemporaryDirectory() as root:
            site = SnapshotSite("safe-site", "Safe", "http://example.invalid", (1,), 1, 2, 5)
            state = CurrentState((site,)); store = SnapshotStore(os.path.join(root, "snap"))

            async def immediate(function, *args, **kwargs):
                return function(*args, **kwargs)

            responses = [(200, "image/jpeg", b"opaque"), (503, "application/json", b"ignored")]
            async def fetch(*_): return responses.pop(0)
            scheduler = SnapshotScheduler((site,), state, store, fetcher=fetch, run_sync=immediate)
            asyncio.run(scheduler.run_round(site)); asyncio.run(scheduler.run_round(site))
            self.assertEqual(store.read_last_good("safe-site", 1), b"opaque")
            attempt = state.sites(store, max_stale_seconds=120)[0]["cameras"][0]["latest_attempt"]
            self.assertIs(attempt["success"], False)
            self.assertEqual(attempt["error_code"], "snapshot_unavailable")
            self.assertFalse(any(path.suffix in {".db", ".sqlite", ".sqlite3"} for path in store.root.parent.rglob("*")))
