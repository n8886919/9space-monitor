"""SQLite retention, deduplication and quota tests for Center."""

from __future__ import annotations

from datetime import datetime, timezone
import concurrent.futures
import hashlib
import os
import tempfile
import unittest
from unittest import mock

from center.storage import CapacityExceeded, InvalidEventTimestamp, TelemetryStorage
from center.validation import validate_batch


NOW_MS = int(datetime(2026, 8, 3, 12, tzinfo=timezone.utc).timestamp() * 1000)


def batch(
    event_id: str,
    *,
    site_id: str = "chengde",
    source: str = "addon",
    timestamp_ms: int = NOW_MS,
    display_name: str = "承德",
    state: str = "ok",
):
    timestamp = datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat()
    safe_event_id = hashlib.sha256(
        f"{source}|{site_id}|nvr.live|1|{event_id}".encode()
    ).hexdigest()
    return validate_batch(
        {
            "site_id": site_id,
            "display_name": display_name,
            "source": source,
            "events": [
                {
                    "event_id": safe_event_id,
                    "timestamp": timestamp,
                    "kind": "nvr.live",
                    "channel_id": 1,
                    "metrics": {"state": state},
                }
            ],
        }
    )


class CenterStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tempdir.name, "telemetry.sqlite3")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_insert_query_and_source_scoped_deduplication(self) -> None:
        storage = TelemetryStorage(self.path)
        first = storage.ingest(batch("same-id"), now_ms=NOW_MS)
        duplicate = storage.ingest(batch("same-id"), now_ms=NOW_MS)
        other_source = storage.ingest(
            batch("same-id", source="integration"), now_ms=NOW_MS
        )
        self.assertEqual(first.inserted, 1)
        self.assertEqual(duplicate.duplicates, 1)
        self.assertEqual(other_source.inserted, 1)
        rows = storage.query("chengde", now_ms=NOW_MS)
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["source"] for row in rows}, {"addon", "integration"})
        self.assertNotIn("logical_bytes", rows[0])

    def test_seven_day_prune_removes_old_data(self) -> None:
        storage = TelemetryStorage(self.path)
        storage.ingest(batch("old"), now_ms=NOW_MS)
        removed = storage.prune(now_ms=NOW_MS + 8 * 24 * 60 * 60 * 1000)
        self.assertEqual(removed, 1)
        self.assertEqual(storage.query("chengde", now_ms=NOW_MS + 8 * 86400 * 1000), [])

    def test_expired_event_is_never_inserted(self) -> None:
        storage = TelemetryStorage(self.path)
        old = NOW_MS - 8 * 24 * 60 * 60 * 1000
        result = storage.ingest(batch("already-old", timestamp_ms=old), now_ms=NOW_MS)
        self.assertEqual(result.expired, 1)
        self.assertEqual(result.inserted, 0)

    def test_single_event_over_cap_fails_atomically(self) -> None:
        storage = TelemetryStorage(
            self.path, site_limit_bytes=100, global_limit_bytes=200
        )
        with self.assertRaises(CapacityExceeded):
            storage.ingest(batch("too-large"), now_ms=NOW_MS)
        self.assertEqual(storage.usage()["sites"], [])

    def test_site_cap_prunes_oldest_before_insert(self) -> None:
        storage = TelemetryStorage(
            self.path, site_limit_bytes=350, global_limit_bytes=700
        )
        for index in range(3):
            storage.ingest(
                batch(f"event-{index}", timestamp_ms=NOW_MS + index * 1000),
                now_ms=NOW_MS + index * 1000,
            )
        rows = storage.query("chengde", now_ms=NOW_MS + 3000)
        self.assertLess(len(rows), 3)
        self.assertEqual(
            rows[-1]["event_id"],
            hashlib.sha256(b"addon|chengde|nvr.live|1|event-2").hexdigest(),
        )
        self.assertLessEqual(storage.usage()["sites"][0]["logical_bytes"], 350)

    def test_global_cap_fails_closed_without_pruning_other_sites(self) -> None:
        storage = TelemetryStorage(
            self.path, site_limit_bytes=220, global_limit_bytes=260
        )
        storage.ingest(batch("one", site_id="site-one"), now_ms=NOW_MS)
        with self.assertRaises(CapacityExceeded):
            storage.ingest(
                batch("two", site_id="site-two", display_name="站二", timestamp_ms=NOW_MS + 1000),
                now_ms=NOW_MS + 1000,
            )
        usage = storage.usage()
        self.assertLessEqual(usage["logical_bytes"], 260)
        self.assertEqual(len(storage.query("site-one", now_ms=NOW_MS + 2000)), 1)
        self.assertEqual(storage.query("site-two", now_ms=NOW_MS + 2000), [])

    def test_site_quota_never_prunes_another_site(self) -> None:
        storage = TelemetryStorage(
            self.path, site_limit_bytes=350, global_limit_bytes=2000
        )
        storage.ingest(batch("protected", site_id="other-site"), now_ms=NOW_MS)
        for index in range(3):
            storage.ingest(
                batch(
                    f"busy-{index}",
                    site_id="busy-site",
                    timestamp_ms=NOW_MS + (index + 1) * 1000,
                ),
                now_ms=NOW_MS + (index + 1) * 1000,
            )
        self.assertEqual(
            [row["event_id"] for row in storage.query("other-site", now_ms=NOW_MS + 5000)],
            [hashlib.sha256(b"addon|other-site|nvr.live|1|protected").hexdigest()],
        )

    def test_latest_keeps_one_per_source_kind_and_channel(self) -> None:
        storage = TelemetryStorage(self.path)
        storage.ingest(batch("older"), now_ms=NOW_MS)
        storage.ingest(
            batch("newer", timestamp_ms=NOW_MS + 1000), now_ms=NOW_MS + 1000
        )
        latest = storage.latest("chengde", now_ms=NOW_MS + 2000)
        self.assertEqual(
            [row["event_id"] for row in latest],
            [hashlib.sha256(b"addon|chengde|nvr.live|1|newer").hexdigest()],
        )

    def test_future_timestamp_beyond_skew_is_rejected(self) -> None:
        storage = TelemetryStorage(self.path, future_skew_seconds=300)
        with self.assertRaises(InvalidEventTimestamp):
            storage.ingest(
                batch("future", timestamp_ms=NOW_MS + 301_000), now_ms=NOW_MS
            )
        self.assertEqual(storage.query("chengde", now_ms=NOW_MS), [])

    def test_physical_preflight_fails_closed_at_injected_low_limit(self) -> None:
        storage = TelemetryStorage(
            self.path,
            site_limit_bytes=40 * 1024,
            global_limit_bytes=64 * 1024,
            physical_limit_bytes=80 * 1024,
            physical_reserve_bytes=32 * 1024,
        )
        with self.assertRaisesRegex(CapacityExceeded, "physical_capacity_preflight"):
            storage.ingest(batch("physical-preflight"), now_ms=NOW_MS)
        self.assertEqual(storage.usage()["sites"], [])

    def test_physical_postwrite_failure_rolls_back_without_harming_other_site(self) -> None:
        storage = TelemetryStorage(self.path)
        storage.ingest(batch("protected", site_id="other-site"), now_ms=NOW_MS)
        real_usage = storage.physical_usage
        calls = 0

        def injected_usage() -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                return 0
            return storage.physical_limit_bytes + 1

        with mock.patch.object(storage, "physical_usage", side_effect=injected_usage):
            with self.assertRaisesRegex(CapacityExceeded, "physical_capacity_limit"):
                storage.ingest(
                    batch("rejected", site_id="new-site", timestamp_ms=NOW_MS + 1000),
                    now_ms=NOW_MS + 1000,
                )
        self.assertGreaterEqual(calls, 2)
        self.assertGreater(real_usage(), 0)
        self.assertEqual(
            [row["event_id"] for row in storage.query("other-site", now_ms=NOW_MS + 2000)],
            [hashlib.sha256(b"addon|other-site|nvr.live|1|protected").hexdigest()],
        )
        self.assertEqual(storage.query("new-site", now_ms=NOW_MS + 2000), [])

    def test_concurrent_ingest_is_serialized_and_lossless(self) -> None:
        storage = TelemetryStorage(self.path)

        def insert(index: int) -> int:
            result = storage.ingest(
                batch(f"thread-{index}", timestamp_ms=NOW_MS + index * 1000),
                now_ms=NOW_MS + 20_000,
            )
            return result.inserted

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            inserted = list(executor.map(insert, range(16)))
        self.assertEqual(inserted, [1] * 16)
        self.assertEqual(len(storage.query("chengde", now_ms=NOW_MS + 30_000)), 16)
