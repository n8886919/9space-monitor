"""Fake-client tests for the bounded, memory-only M5B telemetry producer."""

from __future__ import annotations

import asyncio
import sys
import time
import unittest
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
