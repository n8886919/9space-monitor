"""M5F-1 tests: last-good snapshot files and metadata-only statistics."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from unittest import mock

from center.app import create_app
from center.snapshots import SnapshotStore
from center.storage import TelemetryStorage
from center.validation import TelemetryValidationError
from tests.test_center_app import asgi_request


NOW_MS = 1_800_000_000_000


class SnapshotStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SnapshotStore(os.path.join(self.tempdir.name, "snapshots"))

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_atomic_replace_keeps_only_one_last_good_file(self) -> None:
        self.store.write("chengde", 1, b"opaque-first", timestamp_ms=NOW_MS)
        self.store.write("chengde", 1, b"opaque-second", timestamp_ms=NOW_MS + 1)
        path = self.store.get("chengde", 1, now_ms=NOW_MS + 1, max_stale_seconds=120)
        self.assertIsNotNone(path)
        self.assertEqual(path.read_bytes(), b"opaque-second")
        self.assertEqual(len(list(self.store.root.rglob("*.jpg"))), 1)

    def test_replace_failure_preserves_previous_last_good_file(self) -> None:
        self.store.write("chengde", 1, b"opaque-first", timestamp_ms=NOW_MS)
        with mock.patch("center.snapshots.os.replace", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                self.store.write("chengde", 1, b"opaque-second", timestamp_ms=NOW_MS + 1)
        path = self.store.get("chengde", 1, now_ms=NOW_MS + 1, max_stale_seconds=120)
        self.assertIsNotNone(path)
        self.assertEqual(path.read_bytes(), b"opaque-first")

    def test_store_capacity_is_bounded_without_deleting_last_good_files(self) -> None:
        store = SnapshotStore(self.store.root, max_snapshot_bytes=10, store_limit_bytes=12)
        store.write("chengde", 1, b"opaque-one", timestamp_ms=NOW_MS)
        with self.assertRaisesRegex(ValueError, "snapshot_store_capacity"):
            store.write("chengde", 2, b"opaque-two", timestamp_ms=NOW_MS)
        self.assertEqual(
            store.get("chengde", 1, now_ms=NOW_MS, max_stale_seconds=120).read_bytes(),
            b"opaque-one",
        )

    def test_orphaned_regular_temp_file_counts_toward_capacity(self) -> None:
        store = SnapshotStore(self.store.root, max_snapshot_bytes=10, store_limit_bytes=12)
        orphan = store.root / "chengde" / ".1-crash.tmp"
        orphan.parent.mkdir()
        orphan.write_bytes(b"orphaned")
        with self.assertRaisesRegex(ValueError, "snapshot_store_capacity"):
            store.write("chengde", 1, b"opaque-one", timestamp_ms=NOW_MS)
        self.assertEqual(orphan.read_bytes(), b"orphaned")
        self.assertEqual(list(store.root.rglob("*.jpg")), [])

    def test_future_mtime_is_not_treated_as_fresh(self) -> None:
        self.store.write("chengde", 1, b"opaque-one", timestamp_ms=NOW_MS + 1)
        self.assertIsNone(
            self.store.get("chengde", 1, now_ms=NOW_MS, max_stale_seconds=120)
        )

    def test_negative_max_stale_is_rejected_before_lookup(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid_max_stale"):
            self.store.get("chengde", 1, now_ms=NOW_MS, max_stale_seconds=-1)


class SnapshotMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.storage = TelemetryStorage(os.path.join(self.tempdir.name, "center.sqlite3"))
        self.storage.register_snapshot_camera("chengde", 1, "承德", now_ms=NOW_MS)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_attempts_have_rolling_success_and_latency_population_statistics(self) -> None:
        for offset_ms, success, latency_ms in (
            (-2 * 60 * 60 * 1000, True, 10.0),
            (-30 * 60 * 1000, True, 20.0),
            (-20 * 60 * 1000, False, 30.0),
            (-10 * 60 * 1000, True, 40.0),
        ):
            self.storage.record_snapshot_attempt(
                "chengde", 1, success=success, timestamp_ms=NOW_MS + offset_ms,
                latency_ms=latency_ms, error_code=None if success else "snapshot_unavailable",
                now_ms=NOW_MS,
            )
        stats = self.storage.snapshot_statistics("chengde", 1, now_ms=NOW_MS)
        self.assertEqual(stats["1h"]["attempts"], 3)
        self.assertEqual(stats["1h"]["success_rate"], 2 / 3)
        self.assertEqual(stats["1h"]["latency_mean_ms"], 30.0)
        self.assertAlmostEqual(
            stats["1h"]["latency_population_stddev_ms"], (200 / 3) ** 0.5
        )
        self.assertEqual(stats["24h"]["attempts"], 4)
        self.assertEqual(stats["24h"]["success_rate"], 0.75)
        self.assertEqual(stats["7d"]["latency_mean_ms"], 25.0)

    def test_attempt_metadata_never_stores_snapshot_bytes(self) -> None:
        self.storage.record_snapshot_attempt(
            "chengde", 1, success=True, timestamp_ms=NOW_MS, latency_ms=12.0,
            now_ms=NOW_MS,
        )
        with self.storage._connect() as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(snapshot_attempts)")}
            rows = connection.execute("SELECT * FROM snapshot_attempts").fetchall()
        self.assertNotIn("jpeg", columns)
        self.assertNotIn("bytes", columns)
        self.assertEqual(len(rows), 1)

    def test_attempt_metadata_uses_the_same_retention_path_as_telemetry(self) -> None:
        storage = TelemetryStorage(
            os.path.join(self.tempdir.name, "short-retention.sqlite3"), retention_seconds=60
        )
        storage.register_snapshot_camera("chengde", 1, "承德", now_ms=NOW_MS)
        storage.record_snapshot_attempt(
            "chengde", 1, success=True, timestamp_ms=NOW_MS - 61_000,
            latency_ms=10.0, now_ms=NOW_MS,
        )
        storage.record_snapshot_attempt(
            "chengde", 1, success=True, timestamp_ms=NOW_MS,
            latency_ms=20.0, now_ms=NOW_MS,
        )
        self.assertEqual(storage.snapshot_statistics("chengde", 1, now_ms=NOW_MS)["1h"]["attempts"], 1)

    def test_registry_and_attempt_validation_reuse_sanitized_contract(self) -> None:
        for display_name in (
            "site 192.168.0.10",
            "rtsp://bad.example",
            "Authorization: secret",
            "bad\nname",
        ):
            with self.assertRaises(TelemetryValidationError):
                self.storage.register_snapshot_camera("safe-site", 1, display_name, now_ms=NOW_MS)
        for error_code in ("A_BAD_CODE", "password_error", "x" * 65, "bad-code"):
            with self.assertRaises(TelemetryValidationError):
                self.storage.record_snapshot_attempt(
                    "chengde", 1, success=False, timestamp_ms=NOW_MS,
                    latency_ms=1.0, error_code=error_code, now_ms=NOW_MS,
                )
        for latency_ms in (float("nan"), float("inf"), 3_600_000.1):
            with self.assertRaises(ValueError):
                self.storage.record_snapshot_attempt(
                    "chengde", 1, success=True, timestamp_ms=NOW_MS,
                    latency_ms=latency_ms, now_ms=NOW_MS,
                )

    def test_attempt_postwrite_failure_rolls_back_and_checkpoints_wal(self) -> None:
        self.storage.record_snapshot_attempt(
            "chengde", 1, success=True, timestamp_ms=NOW_MS - 1,
            latency_ms=10.0, now_ms=NOW_MS,
        )
        calls = 0

        def injected_usage() -> int:
            nonlocal calls
            calls += 1
            return 0 if calls == 1 else self.storage.physical_limit_bytes + 1

        with mock.patch.object(self.storage, "physical_usage", side_effect=injected_usage), \
             mock.patch.object(self.storage, "_checkpoint_wal", wraps=self.storage._checkpoint_wal) as checkpoint:
            with self.assertRaisesRegex(Exception, "physical_capacity_limit"):
                self.storage.record_snapshot_attempt(
                    "chengde", 1, success=False, timestamp_ms=NOW_MS,
                    latency_ms=20.0, error_code="snapshot_unavailable", now_ms=NOW_MS,
                )
        self.assertGreaterEqual(calls, 2)
        checkpoint.assert_called_once()
        self.assertEqual(
            self.storage.snapshot_statistics("chengde", 1, now_ms=NOW_MS)["1h"]["attempts"], 1
        )

    def test_site_statistics_aggregate_multiple_cameras(self) -> None:
        self.storage.register_snapshot_camera("chengde", 2, "承德", now_ms=NOW_MS)
        for camera_id, offset_ms, success, latency_ms in (
            (1, -2 * 60 * 60 * 1000, True, 10.0),
            (1, -30 * 60 * 1000, False, 20.0),
            (2, -20 * 60 * 1000, True, 30.0),
            (2, -10 * 60 * 1000, True, 40.0),
        ):
            self.storage.record_snapshot_attempt(
                "chengde", camera_id, success=success, timestamp_ms=NOW_MS + offset_ms,
                latency_ms=latency_ms, error_code=None if success else "snapshot_unavailable",
                now_ms=NOW_MS,
            )
        stats = self.storage.snapshot_statistics("chengde", now_ms=NOW_MS)
        self.assertEqual(stats["1h"]["attempts"], 3)
        self.assertEqual(stats["1h"]["success_rate"], 2 / 3)
        self.assertEqual(stats["24h"]["attempts"], 4)
        self.assertEqual(stats["24h"]["latency_mean_ms"], 25.0)
        self.assertEqual(stats["7d"]["latency_population_stddev_ms"], (125.0) ** 0.5)


class SnapshotApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.storage = TelemetryStorage(os.path.join(self.tempdir.name, "center.sqlite3"))
        self.snapshots = SnapshotStore(os.path.join(self.tempdir.name, "snapshots"))
        self.storage.register_snapshot_camera("chengde", 1, "承德", now_ms=NOW_MS)

        async def run_immediately(function, *args, **kwargs):
            return function(*args, **kwargs)

        self.app = create_app(self.storage, snapshots=self.snapshots, run_sync=run_immediately)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def request(self, path: str):
        return asyncio.run(asgi_request(self.app, "GET", path))

    def test_unknown_and_known_never_success_are_distinct(self) -> None:
        status, _headers, body = self.request("/api/v1/sites/missing/cameras/1/snapshot")
        self.assertEqual((status, json.loads(body)), (404, {"error_code": "snapshot_not_found"}))
        status, _headers, body = self.request("/api/v1/sites/chengde/cameras/1/snapshot")
        self.assertEqual((status, json.loads(body)), (503, {"error_code": "snapshot_unavailable"}))

    def test_recent_last_good_stays_available_after_failed_refresh_and_stale_is_rejected(self) -> None:
        self.snapshots.write("chengde", 1, b"opaque-last-good", timestamp_ms=NOW_MS)
        self.storage.record_snapshot_attempt(
            "chengde", 1, success=False, timestamp_ms=NOW_MS + 1,
            latency_ms=15.0, error_code="snapshot_unavailable",
            now_ms=NOW_MS + 1,
        )
        with mock.patch("center.app.time.time", return_value=(NOW_MS + 2) / 1000):
            status, headers, body = self.request("/api/v1/sites/chengde/cameras/1/snapshot")
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "image/jpeg")
        self.assertEqual(body, b"opaque-last-good")
        with mock.patch("center.app.time.time", return_value=(NOW_MS + 121_000) / 1000):
            status, _headers, body = self.request("/api/v1/sites/chengde/cameras/1/snapshot")
        self.assertEqual((status, json.loads(body)), (503, {"error_code": "snapshot_stale"}))
