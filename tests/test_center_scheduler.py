"""Bounded snapshot scheduler tests without persistent statistics."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest

from nine_space_monitor_hub.scheduler import SnapshotScheduler, SnapshotSite, load_options
from nine_space_monitor_hub.snapshots import SnapshotStore
from nine_space_monitor_hub.state import CurrentState


def options(site):
    return {"sites": [site], "max_stale_seconds": 120, "snapshot_store_limit_mb": 64}


class SchedulerTests(unittest.TestCase):
    def test_options_reject_credentials_and_duplicate_channels(self):
        base = {
            "site_id": "safe", "display_name": "Safe", "base_url": "http://example.invalid",
            "channels": [1], "concurrency": 1, "timeout_seconds": 1, "refresh_seconds": 5,
        }
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "options.json")
            for update, code in (
                ({"base_url": "http://user:pass@example.invalid"}, "invalid_snapshot_site_url"),
                ({"channels": [1, 1]}, "duplicate_snapshot_channel"),
            ):
                value = {**base, **update}
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(options(value), handle)
                with self.assertRaisesRegex(ValueError, code):
                    load_options(path)

    def test_batches_thirteen_channels_four_four_four_one(self):
        with tempfile.TemporaryDirectory() as root:
            site = SnapshotSite("safe-site", "Safe", "http://example.invalid", tuple(range(1, 14)), 4, 1, 5)
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
            site = SnapshotSite("safe-site", "Safe", "http://example.invalid", (1,), 1, 1, 5)
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
