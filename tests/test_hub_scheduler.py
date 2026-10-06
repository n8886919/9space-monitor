"""Bounded snapshot scheduler tests without persistent statistics."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from nine_space_hub.scheduler import SiteHealthMonitor, SnapshotScheduler, SnapshotSite, load_options
from nine_space_hub.snapshots import SnapshotStore
from nine_space_hub.state import CurrentState


class SchedulerTests(unittest.TestCase):
    def test_health_probe_succeeds_while_snapshot_worker_pool_is_busy(self):
        with tempfile.TemporaryDirectory() as root:
            site = SnapshotSite("safe-site", "Safe", "http://example.invalid", (1,), 1, 2, 30)
            state = CurrentState((site,)); store = SnapshotStore(os.path.join(root, "snap"))
            started, release = threading.Event(), threading.Event()

            def busy_snapshot_worker():
                started.set()
                release.wait(5)

            class HealthyResponse:
                status = 200
                def geturl(self): return "http://example.invalid/healthz"
                def __enter__(self): return self
                def __exit__(self, *args): pass

            async def run():
                loop = asyncio.get_running_loop()
                loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
                blocker = loop.run_in_executor(None, busy_snapshot_worker)
                while not started.is_set():
                    await asyncio.sleep(0)
                monitor = SiteHealthMonitor((site,), state, timeout_seconds=1)
                try:
                    with patch("nine_space_hub.scheduler.urllib.request.build_opener") as opener:
                        opener.return_value.open.return_value = HealthyResponse()
                        for _ in range(3):
                            await monitor.run_round()
                        self.assertEqual(opener.return_value.open.call_count, 3)
                        summary = state.sites(store, max_stale_seconds=120)[0]
                        self.assertIs(summary["site_reachable"], True)
                finally:
                    release.set()
                    await blocker
                    await monitor.stop()

            asyncio.run(run())

    def test_health_worker_pool_closes_on_stop_and_restarts_cleanly(self):
        site = SnapshotSite("safe-site", "Safe", "http://example.invalid", (1,), 1, 2, 30)
        state = CurrentState((site,))
        monitor = SiteHealthMonitor((site,), state, interval_seconds=3600)

        class HealthyResponse:
            status = 200
            def geturl(self): return "http://example.invalid/healthz"
            def __enter__(self): return self
            def __exit__(self, *args): pass

        async def run():
            with patch("nine_space_hub.scheduler.urllib.request.build_opener") as opener:
                opener.return_value.open.return_value = HealthyResponse()
                async def wait_for_success():
                    while not state._site_health.get("safe-site", {}).get("reachable"):
                        await asyncio.sleep(0)
                try:
                    await monitor.start()
                    await asyncio.wait_for(wait_for_success(), timeout=3)
                    first_pool = monitor._executor
                    await monitor.stop()
                    with self.assertRaises(RuntimeError):
                        first_pool.submit(lambda: True)
                    state.record_site_health("safe-site", reachable=False, timestamp_ms=1)
                    state.record_site_health("safe-site", reachable=False, timestamp_ms=1)
                    state.record_site_health("safe-site", reachable=False, timestamp_ms=1)
                    await monitor.start()
                    await asyncio.wait_for(wait_for_success(), timeout=3)
                    self.assertIsNot(monitor._executor, first_pool)
                    self.assertEqual(opener.return_value.open.call_count, 2)
                finally:
                    await monitor.stop()

        asyncio.run(run())

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
            camera = state.sites(store, max_stale_seconds=120)[0]["cameras"][0]
            self.assertEqual(camera["snapshot_success_rate"], 50.0)
            self.assertEqual((camera["snapshot_success_count"], camera["snapshot_failure_count"]), (1, 1))
            self.assertEqual(camera["snapshot_consecutive_failures"], 1)
            self.assertFalse(any(path.suffix in {".db", ".sqlite", ".sqlite3"} for path in store.root.parent.rglob("*")))

    def test_disabled_channel_is_skipped_and_can_be_reenabled(self):
        with tempfile.TemporaryDirectory() as root:
            site = SnapshotSite("safe-site", "Safe", "http://example.invalid", (1, 2), 2, 2, 5)
            state = CurrentState((site,)); store = SnapshotStore(os.path.join(root, "snap"))
            fetched = []

            async def fetch(url, _timeout):
                fetched.append(url)
                return 200, "image/jpeg", b"opaque"

            async def immediate(function, *args, **kwargs):
                return function(*args, **kwargs)

            self.assertTrue(state.set_camera_enabled("safe-site", 2, False))
            scheduler = SnapshotScheduler((site,), state, store, fetcher=fetch, run_sync=immediate)
            asyncio.run(scheduler.run_round(site))
            self.assertEqual(fetched, ["http://example.invalid/api/v1/channels/1/snapshot"])
            self.assertFalse(state.sites(store, max_stale_seconds=120)[0]["cameras"][1]["enabled"])

            self.assertTrue(state.set_camera_enabled("safe-site", 2, True))
            asyncio.run(scheduler.run_round(site))
            self.assertTrue(any(url.endswith("/2/snapshot") for url in fetched))

    def test_site_health_requires_three_failures_and_recovers_once(self):
        with tempfile.TemporaryDirectory() as root:
            site = SnapshotSite("safe-site", "Safe", "http://example.invalid", (1,), 1, 2, 5)
            state = CurrentState((site,)); store = SnapshotStore(os.path.join(root, "snap"))
            results = [False, False, False, True]
            calls = []

            async def probe(base_url, timeout):
                calls.append((base_url, timeout))
                return results.pop(0)

            monitor = SiteHealthMonitor((site,), state, probe=probe)
            for expected in (None, None, False, True):
                asyncio.run(monitor.run_round())
                summary = state.sites(store, max_stale_seconds=120)[0]
                self.assertIs(summary["site_reachable"], expected)
            self.assertTrue(state.sites(store, max_stale_seconds=120)[0]["site_last_seen_at"])
            self.assertEqual(calls, [("http://example.invalid", 2)] * 4)
