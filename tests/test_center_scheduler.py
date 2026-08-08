"""M5F-2 fake-only bounded outbound snapshot scheduler tests."""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from unittest import mock

from center.scheduler import SnapshotScheduler, default_fetch, load_sites
from center.snapshots import SnapshotStore
from center.storage import TelemetryStorage


class SchedulerTests(unittest.TestCase):
    def test_private_mapping_batches_thirteen_channels_four_four_four_one(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            config = os.path.join(root, "sites.json")
            with open(config, "w", encoding="utf-8") as handle:
                handle.write('{"sites":[{"site_id":"safe-site","display_name":"Safe","base_url":"http://example.invalid","channels":[1,2,3,4,5,6,7,8,9,10,11,12,13],"concurrency":4,"timeout_seconds":1,"refresh_seconds":5}]}')
            site = load_sites(config)[0]
            storage = TelemetryStorage(os.path.join(root, "center.sqlite3"))
            store = SnapshotStore(os.path.join(root, "snapshots"))
            active = peak = 0; starts: list[int] = []
            async def fetch(url, _timeout):
                nonlocal active, peak
                self.assertEqual(
                    url,
                    f"http://example.invalid/api/v1/channels/{len(starts) + 1}/snapshot",
                )
                self.assertNotIn("/api/camera/", url)
                active += 1; peak = max(peak, active)
                starts.append(active)
                await asyncio.sleep(0)
                active -= 1
                return 200, "image/jpeg", b"opaque"
            async def immediate(function, *args, **kwargs): return function(*args, **kwargs)
            scheduler = SnapshotScheduler((site,), storage, store, fetcher=fetch, run_sync=immediate)
            async def run():
                await scheduler.register()
                await scheduler.run_round(site)
            asyncio.run(run())
            self.assertEqual(peak, 4)
            self.assertEqual(starts, [1, 2, 3, 4] * 3 + [1])
            self.assertEqual(storage.snapshot_statistics("safe-site")["1h"]["attempts"], 13)

    def test_parser_rejects_credentials_duplicates_and_oversized_file(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "sites.json")
            for raw, code in (
                ('{"sites":[{"site_id":"safe","display_name":"Safe","base_url":"http://user:pass@example.invalid","channels":[1],"concurrency":1,"timeout_seconds":1,"refresh_seconds":5}]}', "invalid_snapshot_site_url"),
                ('{"sites":[{"site_id":"safe","display_name":"Safe","base_url":"http://example.invalid","channels":[1,1],"concurrency":1,"timeout_seconds":1,"refresh_seconds":5}]}', "duplicate_snapshot_channel"),
            ):
                with open(path, "w", encoding="utf-8") as handle: handle.write(raw)
                with self.assertRaisesRegex(ValueError, code): load_sites(path)
            with open(path, "wb") as handle: handle.write(b"x" * (128 * 1024 + 1))
            with self.assertRaisesRegex(ValueError, "too_large"): load_sites(path)

    def test_missing_private_mapping_disables_outbound_scheduler(self) -> None:
        self.assertEqual(load_sites("/definitely/not/a/snapshot-sites.json"), ())

    def test_default_fetch_rejects_redirect_without_reading_body(self) -> None:
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): return None
            def geturl(self): return "http://other.invalid/redirected"
            @property
            def headers(self): raise AssertionError("redirect headers/body must not be read")
        opener = mock.Mock()
        opener.open.return_value = Response()
        async def immediate_sync(function, *args, **kwargs):
            return function(*args, **kwargs)
        with mock.patch("center.scheduler.urllib.request.build_opener", return_value=opener), \
             mock.patch("center.scheduler.asyncio.to_thread", side_effect=immediate_sync):
            with self.assertRaisesRegex(ValueError, "redirected"):
                asyncio.run(default_fetch("http://example.invalid/api/v1/channels/1/snapshot", 1))

    def test_failed_attempt_preserves_last_good_and_only_records_safe_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            config = os.path.join(root, "sites.json")
            with open(config, "w", encoding="utf-8") as handle:
                handle.write('{"sites":[{"site_id":"safe-site","display_name":"Safe","base_url":"http://example.invalid","channels":[1],"concurrency":1,"timeout_seconds":1,"refresh_seconds":5}]}')
            site = load_sites(config)[0]; storage = TelemetryStorage(os.path.join(root, "db")); store = SnapshotStore(os.path.join(root, "snap"))
            async def immediate(function, *args, **kwargs): return function(*args, **kwargs)
            async def good(*_): return 200, "image/jpeg", b"opaque-last-good"
            async def bad(*_): return 503, "application/json", b"never-inspected"
            async def run():
                scheduler = SnapshotScheduler((site,), storage, store, fetcher=good, run_sync=immediate); await scheduler.register(); await scheduler.run_round(site)
                scheduler.fetcher = bad; await scheduler.run_round(site)
            asyncio.run(run())
            self.assertEqual(store.get("safe-site", 1, max_stale_seconds=120).read_bytes(), b"opaque-last-good")
            with storage._connect() as connection:
                self.assertEqual([row[0] for row in connection.execute("SELECT error_code FROM snapshot_attempts ORDER BY row_id")], [None, "snapshot_unavailable"])

    def test_metadata_failure_does_not_stop_next_attempt_or_round(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            site = load_sites_path(root)
            storage = TelemetryStorage(os.path.join(root, "db")); store = SnapshotStore(os.path.join(root, "snap"))
            calls = 0
            async def immediate(function, *args, **kwargs):
                nonlocal calls
                if function == storage.record_snapshot_attempt:
                    calls += 1
                    if calls == 1: raise OSError("metadata")
                return function(*args, **kwargs)
            async def fetch(*_): return 200, "image/jpeg", b"opaque"
            async def run():
                scheduler = SnapshotScheduler((site,), storage, store, fetcher=fetch, run_sync=immediate)
                await scheduler.register(); await scheduler.run_round(site); await scheduler.run_round(site)
                return scheduler.metadata_dropped
            self.assertEqual(asyncio.run(run()), 1)
            self.assertEqual(calls, 2)

    def test_stop_is_idempotent_and_cancelled_fetch_records_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            site = load_sites_path(root); storage = TelemetryStorage(os.path.join(root, "db")); store = SnapshotStore(os.path.join(root, "snap"))
            entered = asyncio.Event()
            async def fetch(*_): entered.set(); await asyncio.Event().wait()
            async def immediate(function, *args, **kwargs): return function(*args, **kwargs)
            async def run():
                scheduler = SnapshotScheduler((site,), storage, store, fetcher=fetch, run_sync=immediate)
                await scheduler.start(); await entered.wait(); await scheduler.stop(); await scheduler.stop()
                return scheduler._task, storage.snapshot_statistics("safe-site")["1h"]["attempts"]
            self.assertEqual(asyncio.run(run()), (None, 0))


def load_sites_path(root: str):
    path = os.path.join(root, "sites.json")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write('{"sites":[{"site_id":"safe-site","display_name":"Safe","base_url":"http://example.invalid","channels":[1],"concurrency":1,"timeout_seconds":1,"refresh_seconds":5}]}')
    return load_sites(path)[0]
