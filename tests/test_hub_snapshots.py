"""Last-good JPEG store tests for 9Space Hub."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from nine_space_hub.snapshots import SnapshotStore

NOW_MS = 1_800_000_000_000


class SnapshotStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SnapshotStore(os.path.join(self.tempdir.name, "snapshots"))

    def tearDown(self):
        self.tempdir.cleanup()

    def test_atomic_replace_keeps_one_last_good(self):
        self.store.write("safe-site", 1, b"first", timestamp_ms=NOW_MS)
        self.store.write("safe-site", 1, b"second", timestamp_ms=NOW_MS + 1)
        self.assertEqual(self.store.read_last_good("safe-site", 1), b"second")
        self.assertEqual(len(list(self.store.root.rglob("*.jpg"))), 1)

    def test_failed_replace_preserves_previous_file(self):
        self.store.write("safe-site", 1, b"first", timestamp_ms=NOW_MS)
        with mock.patch("nine_space_hub.snapshots.os.replace", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                self.store.write("safe-site", 1, b"second", timestamp_ms=NOW_MS + 1)
        self.assertEqual(self.store.read_last_good("safe-site", 1), b"first")

    def test_capacity_is_bounded_without_deleting_last_good(self):
        store = SnapshotStore(self.store.root, max_snapshot_bytes=10, store_limit_bytes=12)
        store.write("safe-site", 1, b"opaque-one", timestamp_ms=NOW_MS)
        with self.assertRaisesRegex(ValueError, "snapshot_store_capacity"):
            store.write("safe-site", 2, b"opaque-two", timestamp_ms=NOW_MS)
        self.assertEqual(store.read_last_good("safe-site", 1), b"opaque-one")

    def test_stale_and_future_images_are_not_publicly_available(self):
        self.store.write("safe-site", 1, b"opaque", timestamp_ms=NOW_MS)
        self.assertIsNone(self.store.get("safe-site", 1, now_ms=NOW_MS + 121_000, max_stale_seconds=120))
        self.assertIsNone(self.store.get("safe-site", 1, now_ms=NOW_MS - 1, max_stale_seconds=120))
        self.assertEqual(self.store.read_last_good("safe-site", 1), b"opaque")
