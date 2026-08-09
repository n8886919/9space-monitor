"""Unit tests for M5C telemetry without importing Home Assistant."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import unittest

from center.validation import validate_batch


MODULE_PATH = Path(__file__).parents[1] / "custom_components/nvr_monitor/ha_telemetry.py"
SPEC = importlib.util.spec_from_file_location("ha_telemetry_test", MODULE_PATH)
assert SPEC and SPEC.loader
telemetry = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = telemetry
SPEC.loader.exec_module(telemetry)


RAW_ENTITY = "sensor.private_memory_measurement"
MAPPING_JSON = json.dumps([{"entity_id": RAW_ENTITY, "kind": "ha.system", "metric": "memory_used_percent", "unit": "%", "channel_id": None}])
PING_MAPPING = {"entity_id": "binary_sensor.private_ping", "kind": "ha.ping", "metric": "available", "unit": None, "channel_id": 7}


class State:
    def __init__(self, state: object) -> None:
        self.state = state


class FakeCenter:
    def __init__(self, *, block: bool = False, fail: bool = False) -> None:
        self.payloads: list[dict] = []
        self.block, self.fail = block, fail
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def post(self, _url: str, payload: dict, _timeout: float) -> None:
        self.started.set()
        if self.block:
            await self.release.wait()
        if self.fail:
            raise OSError("offline")
        self.payloads.append(payload)


class FakeProducer:
    def __init__(self) -> None:
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


def producer(client: FakeCenter, **kwargs) -> object:
    mapping = telemetry.parse_mapping_json(MAPPING_JSON)
    assert mapping is not None
    return telemetry.HATelemetryProducer(
        center_url="https://center.tailnet.example/api/v1/telemetry",
        site_id="chengde",
        display_name="Site", mapping=mapping, client=client, **kwargs,
    )


class HATelemetryTests(unittest.IsolatedAsyncioTestCase):
    def test_mapping_is_explicit_and_fail_closed(self) -> None:
        self.assertIsNotNone(telemetry.parse_mapping_json(MAPPING_JSON))
        self.assertIsNone(telemetry.parse_mapping_json("[]"))
        self.assertIsNone(telemetry.parse_mapping([PING_MAPPING] * (telemetry.MAX_MAPPING_ITEMS + 1)))
        self.assertIsNone(telemetry.parse_mapping_json(json.dumps([{"entity_id": RAW_ENTITY, "kind": "ha.system", "metric": "download_mbps", "unit": "Mbps", "channel_id": None}])))
        self.assertIsNone(telemetry.parse_mapping_json(json.dumps([{"entity_id": RAW_ENTITY, "kind": "ha.system", "metric": "memory_used_percent", "unit": "%", "channel_id": 1}])))
        self.assertIsNone(telemetry.parse_mapping_json(json.dumps([{**PING_MAPPING, "channel_id": None}])))
        self.assertEqual((), telemetry.parse_mapping_json(json.dumps([PING_MAPPING])))
        self.assertIsNone(telemetry.parse_mapping_json(json.dumps([PING_MAPPING, {**PING_MAPPING, "entity_id": "binary_sensor.other"}])))
        self.assertIsNone(telemetry.safe_center_url("https://@center/api/v1/telemetry"))
        self.assertIsNone(telemetry.safe_center_url("https://center/api/v1/telemetry?x=1"))
        self.assertIsNone(telemetry.safe_site_metadata("chengde", "Site 203.0.113.9"))
        self.assertIsNone(telemetry.safe_site_metadata("chengde", "2001:db8::1"))
        self.assertIsNone(telemetry.safe_site_metadata("chengde", "Digest sample"))
        self.assertIsNone(telemetry.safe_site_metadata("chengde", "A" * 80))

    async def test_payload_is_allowlisted_and_entity_id_is_not_exported(self) -> None:
        client = FakeCenter()
        item = producer(client)
        item.start()
        self.assertTrue(item.sample(lambda entity_id: State("48.5") if entity_id == RAW_ENTITY else None, now_ms=1_000))
        await asyncio.wait_for(client.started.wait(), 1)
        await asyncio.sleep(0)
        payload = client.payloads[0]
        serialized = json.dumps(payload)
        self.assertNotIn(RAW_ENTITY, serialized)
        self.assertNotIn("entity_id", serialized)
        validate_batch(payload)
        event = payload["events"][0]
        self.assertEqual("ha.system", event["kind"])
        self.assertEqual({"memory_used_percent": 48.5, "unit": "%"}, event["metrics"])
        self.assertEqual("producer.health", payload["events"][1]["kind"])
        await item.stop()

    async def test_unknown_is_dropped_and_ring_evicts_after_24h(self) -> None:
        item = producer(FakeCenter())
        self.assertTrue(item.sample(lambda _id: State("unknown"), now_ms=1_000))
        self.assertEqual(1, len(item._ring))
        item.sample(lambda _id: State("1"), now_ms=1_000)
        self.assertEqual(3, len(item._ring))
        item.sample(lambda _id: State("2"), now_ms=1_000 + telemetry.RING_WINDOW_MS + 1)
        self.assertEqual(2, len(item._ring))

    async def test_queue_full_and_center_unavailable_drop_without_blocking(self) -> None:
        client = FakeCenter(block=True)
        item = producer(client, queue_max_batches=1)
        item.start()
        item.sample(lambda _id: State("1"), now_ms=1)
        await asyncio.wait_for(client.started.wait(), 1)
        item.sample(lambda _id: State("2"), now_ms=2)
        self.assertFalse(item.sample(lambda _id: State("3"), now_ms=3))
        self.assertEqual(2, item.dropped_events)
        await item.stop()
        client.release.set()

        failed = FakeCenter(fail=True)
        item = producer(failed)
        item.start()
        item.sample(lambda _id: State("4"), now_ms=4)
        await asyncio.wait_for(failed.started.wait(), 1)
        await asyncio.sleep(0)
        self.assertEqual(2, item.dropped_events)
        await item.stop()

    async def test_timeout_and_shutdown_are_bounded(self) -> None:
        client = FakeCenter(block=True)
        item = producer(client, timeout_seconds=0.01, shutdown_wait_seconds=0.01)
        item.start()
        item.sample(lambda _id: State("5"), now_ms=5)
        await asyncio.wait_for(client.started.wait(), 1)
        await asyncio.sleep(0.03)
        self.assertEqual(2, item.dropped_events)
        await asyncio.wait_for(item.stop(), 0.2)

    async def test_ping_stays_local_while_other_metrics_and_ring_hard_cap_work(self) -> None:
        mapping = telemetry.parse_mapping([
            PING_MAPPING,
            {"entity_id": "sensor.cpu", "kind": "ha.system", "metric": "processor_use_percent", "unit": "%", "channel_id": None},
            {"entity_id": "sensor.boot", "kind": "ha.system", "metric": "last_boot", "unit": None, "channel_id": None},
            {"entity_id": "sensor.uptime", "kind": "ha.system", "metric": "uptime_seconds", "unit": "s", "channel_id": None},
        ])
        assert mapping is not None
        client = FakeCenter()
        item = telemetry.HATelemetryProducer(center_url="https://center.tailnet.example/api/v1/telemetry", site_id="chengde", display_name="Site", mapping=mapping, client=client)
        item.start()
        values = {"binary_sensor.private_ping": State("on"), "sensor.cpu": State("99.5"), "sensor.boot": State("2026-08-04T00:00:00+00:00"), "sensor.uptime": State("12")}
        item.sample(values.get, now_ms=10)
        await asyncio.wait_for(client.started.wait(), 1)
        await asyncio.sleep(0)
        payload = client.payloads[0]
        validate_batch(payload)
        self.assertNotIn("ha.ping", {event["kind"] for event in payload["events"]})
        self.assertEqual("ha.system", payload["events"][0]["kind"])
        self.assertIsNone(payload["events"][0]["channel_id"])
        self.assertNotIn("binary_sensor.private_ping", json.dumps(payload))
        item._ring.extend(
            telemetry._RingEvent(index, {})
            for index in range(telemetry.RING_MAX_EVENTS + 1)
        )
        self.assertEqual(telemetry.RING_MAX_EVENTS, len(item._ring))
        await item.stop()

    def test_ping_only_legacy_mapping_does_not_build_center_producer(self) -> None:
        config = {
            "telemetry_site_id": "chengde",
            "telemetry_display_name": "Site",
            "telemetry_center_url": "https://center.tailnet.example/api/v1/telemetry",
            "telemetry_mapping": json.dumps([PING_MAPPING]),
        }
        self.assertIsNone(telemetry.build_producer(config, FakeCenter()))

    async def test_lifecycle_only_stops_after_successful_platform_unload(self) -> None:
        producer = FakeProducer()
        cancelled = False

        def unsubscribe() -> None:
            nonlocal cancelled
            cancelled = True

        await telemetry.async_finalize_unload(
            producer, unsubscribe, platforms_unloaded=False
        )
        self.assertFalse(cancelled)
        self.assertFalse(producer.stopped)
        await telemetry.async_finalize_unload(
            producer, unsubscribe, platforms_unloaded=True
        )
        self.assertTrue(cancelled)
        self.assertTrue(producer.stopped)


if __name__ == "__main__":
    unittest.main()
