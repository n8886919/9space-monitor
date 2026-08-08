"""Fake-client tests for the bounded, memory-only M5B telemetry producer."""

from __future__ import annotations

import asyncio
from datetime import datetime
import sys
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from center.validation import validate_batch

ADDON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_DIR))

from telemetry import (
    NvrTelemetryModel,
    TelemetryProducer,
    safe_center_url,
    safe_site_metadata,
    telemetry_channel_ids,
)
from channel_state import ChannelStateStore
import recording_query
from recording_query import recording_interval_metrics


def recording_query_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=recording_query.LOCAL_TZ
    )


class FakeCenterClient:
    def __init__(self) -> None:
        self.payloads: list[dict] = []
        self.attempted_payloads: list[dict] = []
        self.urls: list[str] = []
        self.fail = False
        self.cancelled = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False

    async def post(self, url: str, payload: dict, _timeout_seconds: float) -> None:
        self.urls.append(url)
        self.attempted_payloads.append(payload)
        self.started.set()
        if self.block:
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        if self.fail:
            raise TimeoutError("unreachable")
        self.payloads.append(payload)


class CancellationIgnoringClient(FakeCenterClient):
    async def post(self, url: str, payload: dict, timeout_seconds: float) -> None:
        self.urls.append(url)
        self.attempted_payloads.append(payload)
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            await self.release.wait()


class TelemetryProducerTests(unittest.IsolatedAsyncioTestCase):
    async def test_queue_is_bounded_and_drops_without_blocking(self) -> None:
        client = FakeCenterClient()
        client.block = True
        producer = TelemetryProducer(
            center_url="https://center.invalid/api/v1/telemetry",
            site_id="sample-site",
            display_name="範例站點",
            client=client,
            queue_max_batches=1,
            timeout_seconds=0.01,
        )
        producer.start()
        self.assertTrue(producer.enqueue([{"kind": "nvr.live"}]))
        await asyncio.wait_for(client.started.wait(), timeout=0.2)
        self.assertTrue(producer.enqueue([{"kind": "nvr.live"}]))
        self.assertFalse(producer.enqueue([{"kind": "nvr.live"}]))
        self.assertEqual(producer.dropped_events, 1)
        await producer.stop()

    async def test_center_timeout_drops_blocked_batch_without_payload_url(self) -> None:
        client = FakeCenterClient()
        client.block = True
        producer = TelemetryProducer(
            center_url="https://center.invalid/api/v1/telemetry",
            site_id="sample-site",
            display_name="範例站點",
            client=client,
            timeout_seconds=0.01,
        )
        producer.start()
        worker = producer._task
        producer.enqueue([{"kind": "nvr.live"}, {"kind": "nvr.recording"}])
        await asyncio.wait_for(client.started.wait(), timeout=0.2)
        await asyncio.sleep(0.03)
        self.assertEqual(producer.dropped_events, 2)
        self.assertTrue(client.cancelled)
        self.assertEqual(client.urls, ["https://center.invalid/api/v1/telemetry"])
        self.assertNotIn(client.urls[0], repr(client.attempted_payloads))
        await producer.stop()
        self.assertIsNotNone(worker)
        self.assertTrue(worker.done())

    async def test_shutdown_does_not_wait_for_center_client(self) -> None:
        client = FakeCenterClient()
        client.block = True
        producer = TelemetryProducer(
            center_url="https://center.invalid/api/v1/telemetry",
            site_id="sample-site",
            display_name="範例站點",
            client=client,
            shutdown_wait_seconds=0.01,
        )
        producer.start()
        worker = producer._task
        producer.enqueue([{"kind": "nvr.live"}])
        await asyncio.wait_for(client.started.wait(), timeout=0.2)
        started = time.monotonic()
        await producer.stop()
        self.assertLess(time.monotonic() - started, 0.1)
        self.assertIsNotNone(worker)
        self.assertTrue(worker.done())
        self.assertFalse(producer._stopping_tasks)

    async def test_shutdown_timeout_tracks_then_reaps_cancellation_ignoring_worker(self) -> None:
        client = CancellationIgnoringClient()
        producer = TelemetryProducer(
            center_url="https://center.invalid/api/v1/telemetry",
            site_id="sample-site",
            display_name="範例站點",
            client=client,
            shutdown_wait_seconds=0.01,
        )
        producer.start()
        worker = producer._task
        producer.enqueue([{"kind": "nvr.live"}])
        await asyncio.wait_for(client.started.wait(), timeout=0.2)
        await producer.stop()
        self.assertIsNotNone(worker)
        self.assertIn(worker, producer._stopping_tasks)
        client.release.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(worker), timeout=0.2)
        self.assertFalse(producer._stopping_tasks)


class NvrTelemetryModelTests(unittest.TestCase):
    def test_recording_intervals_clip_merge_and_ignore_normal_segment_boundaries(self) -> None:
        start = recording_query_time("2026-08-08 00:00:00")
        end = recording_query_time("2026-08-09 00:00:00")
        files = [
            {"StartTime": "2026-08-07 23:00:00", "EndTime": "2026-08-08 01:00:00"},
            {"StartTime": "2026-08-08 00:59:00", "EndTime": "2026-08-08 02:00:00"},
            {"StartTime": "2026-08-08 02:00:30", "EndTime": "2026-08-08 03:00:00"},
            {"StartTime": "2026-08-08 05:30:00", "EndTime": "2026-08-08 06:00:00"},
            {"StartTime": "not-a-time", "EndTime": "2026-08-08 06:10:00"},
        ]
        metrics = recording_interval_metrics(files, start, end, truncated=False)
        self.assertEqual(metrics["file_count_24h"], 5)
        self.assertEqual(metrics["valid_file_count_24h"], 4)
        self.assertEqual(metrics["invalid_file_count_24h"], 1)
        # The 30-second split is below the explicit threshold and is merged.
        self.assertEqual(metrics["gap_count_24h"], 2)
        self.assertEqual(metrics["gap_total_seconds_24h"], 73_800.0)
        self.assertEqual(metrics["largest_gap_seconds_24h"], 64_800.0)
        self.assertAlmostEqual(metrics["recording_coverage_24h_pct"], 14.583333333333334)
        self.assertFalse(metrics["truncated"])

    def test_recording_intervals_mark_max_file_result_truncated(self) -> None:
        start = recording_query_time("2026-08-08 00:00:00")
        end = recording_query_time("2026-08-09 00:00:00")
        metrics = recording_interval_metrics([], start, end, truncated=True)
        self.assertTrue(metrics["truncated"])
        self.assertEqual(metrics["gap_count_24h"], 1)
        self.assertEqual(metrics["gap_total_seconds_24h"], 86_400.0)

    def test_failed_recording_state_does_not_emit_old_success_aggregates(self) -> None:
        model = NvrTelemetryModel(sample_interval_seconds=300)
        states = {1: {"recording": {
            "recording_query_ok": False,
            "recording_recent": None,
            "last_recording": None,
            "error_code": "recording_query_failed",
            "metrics": {"file_count_24h": 99, "truncated": False},
        }}}
        event = next(event for event in model.events("sample-site", states, now_ms=1_000, dropped_events=0)
                     if event["kind"] == "nvr.recording")
        self.assertNotIn("file_count_24h", event["metrics"])
        self.assertNotIn("truncated", event["metrics"])

    def test_recording_and_health_filters_reject_non_allowlisted_or_bad_values(self) -> None:
        model = NvrTelemetryModel(sample_interval_seconds=300)
        events = model.events(
            "sample-site", {1: {"recording": {
                "recording_query_ok": True, "recording_recent": True,
                "last_recording": None, "error_code": None,
                "metrics": {"file_count_24h": 2, "gap_count_24h": "wrong", "options": "x", "password": "x", "raw_payload": "x", "url": "http://bad"},
            }}}, now_ms=1_000, dropped_events=0,
            producer_health={"source_version": "wrong", "snapshot_max_concurrency": 9,
                             "telemetry_queue_depth": "wrong", "producer_state": "leak",
                             "center_reachable": "wrong", "options": "x", "password": "x"},
        )
        recording = next(event for event in events if event["kind"] == "nvr.recording")
        health = next(event for event in events if event["kind"] == "producer.health")
        self.assertEqual(recording["metrics"]["file_count_24h"], 2)
        for event in (recording, health):
            encoded = repr(event).lower()
            for forbidden in ("options", "password", "raw_payload", "http://"):
                self.assertNotIn(forbidden, encoded)
        self.assertNotIn("gap_count_24h", recording["metrics"])
        self.assertNotIn("source_version", health["metrics"])

    def test_query_marks_full_max_page_truncated_without_an_extra_page(self) -> None:
        client = recording_query.DahuaRecordingClient(
            recording_query.NvrHttpConfig(host="example", http_port=80, username="a", password="b")
        )
        now = datetime.now(recording_query.LOCAL_TZ).replace(microsecond=0)
        item_start = (now - recording_query.timedelta(seconds=10)).strftime("%Y-%m-%d %H:%M:%S")
        item_end = now.strftime("%Y-%m-%d %H:%M:%S")
        full_page = "found=100\n" + "\n".join(
            f"items[{index}].StartTime={item_start}\nitems[{index}].EndTime={item_end}"
            for index in range(100)
        )
        responses = iter(["object=abc", "OK", full_page, "OK"])
        calls: list[str] = []

        def fake_get(path, params, deadline):
            calls.append(dict(params)["action"])
            return next(responses)

        with patch.object(recording_query, "MAX_FILES", 100), patch.object(client, "_get", side_effect=fake_get):
            result = client.query_channel(1)
        self.assertTrue(result["recording_query_ok"])
        self.assertTrue(result["metrics"]["truncated"])
        self.assertEqual(result["metrics"]["page_count"], 1)
        self.assertIn("query_duration_ms", result["metrics"])
        self.assertIn("last_recording_age_hours", result["metrics"])
        self.assertEqual(calls, ["factory.create", "findFile", "findNextFile", "destroy"])

    def test_query_short_final_page_is_not_truncated(self) -> None:
        client = recording_query.DahuaRecordingClient(
            recording_query.NvrHttpConfig(host="example", http_port=80, username="a", password="b")
        )
        responses = iter(["object=abc", "OK", "found=0", "OK"])
        with patch.object(client, "_get", side_effect=lambda *args: next(responses)):
            result = client.query_channel(1)
        self.assertTrue(result["recording_query_ok"])
        self.assertFalse(result["metrics"]["truncated"])
        self.assertEqual(result["metrics"]["page_count"], 1)

    def test_source_specific_errors_are_not_cross_sent(self) -> None:
        store = ChannelStateStore()

        async def populate() -> None:
            await store.update_live(
                1,
                live_video=False,
                checked_at_ms=1_000,
                error_code="rtsp_timeout",
            )
            await store.update_recording(
                1,
                recording_query_ok=False,
                recording_recent=None,
                last_recording=None,
                checked_at_ms=2_000,
                error_code="recording_query_failed",
            )

        asyncio.run(populate())
        state = {1: store.telemetry_snapshot(1)}
        model = NvrTelemetryModel(sample_interval_seconds=300)
        model.observe(state, now_ms=2_000)
        events = model.events("sample-site", state, now_ms=2_000, dropped_events=0)
        by_kind = {event["kind"]: event for event in events}
        self.assertEqual(by_kind["nvr.live"]["metrics"]["error_code"], "rtsp_timeout")
        self.assertEqual(
            by_kind["nvr.recording"]["metrics"]["error_code"], "recording_query_failed"
        )

    def test_ring_evicts_samples_older_than_24_hours(self) -> None:
        model = NvrTelemetryModel(sample_interval_seconds=300)
        old = 1_000
        model.observe({1: {"live_video": True}}, now_ms=old)
        model.observe({1: {"live_video": False}}, now_ms=old + 86_400_001)
        event = model.events(
            site_id="sample-site",
            channel_states={1: {"live_video": False, "recording_query_ok": False,
                                "recording_recent": None, "last_recording": None,
                                "error_code": None}},
            now_ms=old + 86_400_001,
            dropped_events=0,
        )[0]
        self.assertEqual(event["metrics"]["live_sample_count_24h"], 1)
        self.assertEqual(event["metrics"]["disconnect_count_24h"], 0)

    def test_events_are_sanitized_and_follow_center_contract(self) -> None:
        model = NvrTelemetryModel(sample_interval_seconds=300)
        states = {
            7: {
                "live_video": True,
                "recording_query_ok": True,
                "recording_recent": False,
                "last_recording": "2026-08-03T12:00:00+00:00",
                "error_code": "unallowlisted_detail",
            }
        }
        model.observe(states, now_ms=1_000)
        events = model.events("sample-site", states, now_ms=1_000, dropped_events=3)
        self.assertEqual({event["kind"] for event in events}, {
            "nvr.live", "nvr.recording", "producer.health"
        })
        self.assertTrue(all(len(event["event_id"]) == 64 for event in events))
        self.assertTrue(all("unallowlisted_detail" not in repr(event) for event in events))
        self.assertTrue(all("192.168" not in repr(event) for event in events))
        for event in events:
            self.assertNotIn("raw", event)
            self.assertNotIn("url", repr(event).lower())
        validate_batch(
            {
                "site_id": "sample-site",
                "display_name": "範例站點",
                "source": "addon",
                "events": events,
            }
        )

    def test_producer_health_is_explicitly_allowlisted_and_has_no_options(self) -> None:
        model = NvrTelemetryModel(sample_interval_seconds=300)
        events = model.events(
            "sample-site", {1: {}}, now_ms=1_000, dropped_events=3,
            producer_health={
                "source_version": "0.3.5",
                "snapshot_max_concurrency": 4,
                "telemetry_queue_depth": 2,
                "telemetry_queue_capacity": 10,
                "producer_state": "running",
                "center_reachable": True,
            },
        )
        health = next(event for event in events if event["kind"] == "producer.health")
        self.assertEqual(health["metrics"]["channel_count"], 1)
        self.assertEqual(health["metrics"]["dropped_events"], 3)
        self.assertNotIn("options", repr(health).lower())
        self.assertNotIn("password", repr(health).lower())
        validate_batch({"site_id": "sample-site", "display_name": "範例站點", "source": "addon", "events": events})

    def test_site_metadata_rejects_embedded_ip_or_secret_words(self) -> None:
        self.assertIsNone(safe_site_metadata("sample-site", "站點192.168.0.10"))
        self.assertIsNone(safe_site_metadata("sample-site", "token 站點"))
        self.assertIsNone(safe_site_metadata("token-site", "範例站點"))
        self.assertIsNone(safe_site_metadata("sample-site", "Basic example"))
        self.assertIsNone(safe_site_metadata("sample-site", "Digest example"))
        self.assertIsNone(safe_site_metadata("sample-site", "2001:db8::1"))
        self.assertIsNone(safe_site_metadata("sample-site", "A" * 80))

    def test_center_destination_is_strict_and_channel_ids_are_capped(self) -> None:
        self.assertEqual(
            safe_center_url("https://center.invalid/api/v1/telemetry"),
            "https://center.invalid/api/v1/telemetry",
        )
        for value in (
            "ftp://center.invalid/api/v1/telemetry",
            "https://user@center.invalid/api/v1/telemetry",
            "https://center.invalid/other",
            "https://center.invalid/api/v1/telemetry?extra=1",
            "https://center.invalid/api/v1/telemetry#fragment",
        ):
            with self.subTest(value=value):
                self.assertIsNone(safe_center_url(value))
        self.assertEqual(list(telemetry_channel_ids(0)), [])
        self.assertEqual(list(telemetry_channel_ids("not-an-int")), [])
        ids = telemetry_channel_ids(10_000)
        self.assertEqual(ids.start, 1)
        self.assertEqual(ids.stop, 4097)
