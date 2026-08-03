"""Contract tests for Center telemetry validation."""

from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import unittest

from center.validation import MAX_BATCH_EVENTS, TelemetryValidationError, validate_batch


def valid_payload() -> dict:
    return {
        "site_id": "chengde",
        "display_name": "承德",
        "source": "addon",
        "events": [
            {
                "event_id": hashlib.sha256(b"addon|chengde|nvr.live|1|001").hexdigest(),
                "timestamp": "2026-08-03T12:00:00+08:00",
                "kind": "nvr.live",
                "channel_id": 1,
                "metrics": {
                    "live_video": True,
                    "probe_duration_ms": 42.5,
                    "error_code": None,
                },
            }
        ],
    }


class CenterValidationTests(unittest.TestCase):
    def test_accepts_chinese_display_name_and_primitives(self) -> None:
        batch = validate_batch(valid_payload())
        self.assertEqual(batch.site_id, "chengde")
        self.assertEqual(batch.display_name, "承德")
        self.assertTrue(batch.events[0].metrics["live_video"])

    def test_rejects_more_than_bounded_batch(self) -> None:
        payload = valid_payload()
        payload["events"] = payload["events"] * (MAX_BATCH_EVENTS + 1)
        with self.assertRaisesRegex(TelemetryValidationError, "batch_too_large"):
            validate_batch(payload)

    def test_rejects_unallowlisted_secret_and_image_keys(self) -> None:
        for key in ("password", "authorization", "jpeg", "image", "raw_payload"):
            with self.subTest(key=key):
                payload = valid_payload()
                payload["events"][0]["metrics"] = {key: "not-stored"}
                with self.assertRaisesRegex(
                    TelemetryValidationError, "metric_not_allowlisted"
                ):
                    validate_batch(payload)

    def test_rejects_url_authorization_and_image_values(self) -> None:
        values = (
            "rtsp://example.invalid/cam/realmonitor",
            "https://example.invalid/api",
            "Authorization: Digest secret",
            "data:image/jpeg;base64,AAAA",
            "192.168.0.10",
            "2001:db8::1",
            "hunter2",
        )
        for value in values:
            with self.subTest(value=value):
                payload = valid_payload()
                payload["events"][0]["metrics"] = {"state": value}
                with self.assertRaises(TelemetryValidationError):
                    validate_batch(payload)

    def test_metric_schema_rejects_wrong_type_range_and_free_text(self) -> None:
        invalid_metrics = (
            {"live_video": 1},
            {"live_online_rate_24h": 101},
            {"disconnect_count_24h": -1},
            {"describe_status": 99},
            {"state": "arbitrary free text"},
            {"unit": "passwords"},
            {"error_code": "Not A Safe Code"},
            {"source_version": "latest"},
        )
        for metrics in invalid_metrics:
            with self.subTest(metrics=metrics):
                payload = valid_payload()
                payload["events"][0]["metrics"] = metrics
                with self.assertRaises(TelemetryValidationError):
                    validate_batch(payload)

    def test_rejects_ip_or_url_in_display_name_metadata(self) -> None:
        for display_name in (
            "站點 192.168.0.10",
            "https://example.invalid",
            "2001:db8::1",
            "password 站",
            "token 站",
        ):
            with self.subTest(display_name=display_name):
                payload = valid_payload()
                payload["display_name"] = display_name
                with self.assertRaisesRegex(TelemetryValidationError, "invalid_display_name"):
                    validate_batch(payload)

    def test_event_id_rejects_ip_and_credential_words(self) -> None:
        for event_id in (
            "192.168.0.10",
            "2001:db8::1",
            "password-001",
            "secret-001",
            "hunter2",
            "A" * 64,
            "a" * 63,
        ):
            with self.subTest(event_id=event_id):
                payload = valid_payload()
                payload["events"][0]["event_id"] = event_id
                with self.assertRaisesRegex(TelemetryValidationError, "invalid_event_id"):
                    validate_batch(payload)

    def test_raw_entity_id_is_not_allowlisted(self) -> None:
        payload = valid_payload()
        payload["events"][0]["metrics"] = {
            "entity_id": "binary_sensor.192_168_0_101"
        }
        with self.assertRaisesRegex(TelemetryValidationError, "metric_not_allowlisted"):
            validate_batch(payload)

    def test_kind_is_a_fixed_allowlist(self) -> None:
        for kind in ("password", "nvr.custom", "ha.entity", "rtsp://bad"):
            with self.subTest(kind=kind):
                payload = valid_payload()
                payload["events"][0]["kind"] = kind
                with self.assertRaisesRegex(TelemetryValidationError, "invalid_kind"):
                    validate_batch(payload)

    def test_site_id_rejects_credential_keywords(self) -> None:
        for site_id in ("password-site", "secret-site", "token-site", "api-key-site"):
            with self.subTest(site_id=site_id):
                payload = valid_payload()
                payload["site_id"] = site_id
                with self.assertRaisesRegex(TelemetryValidationError, "invalid_site_id"):
                    validate_batch(payload)

    def test_rejects_base64_and_binary_or_nested_metrics(self) -> None:
        payloads = []
        encoded = base64.b64encode(bytes(range(96))).decode()
        for value in (encoded, b"binary", {"nested": True}, [1, 2, 3]):
            payload = deepcopy(valid_payload())
            payload["events"][0]["metrics"] = {"state": value}
            payloads.append(payload)
        for payload in payloads:
            with self.subTest(value=type(payload["events"][0]["metrics"]["state"])):
                with self.assertRaises(TelemetryValidationError):
                    validate_batch(payload)

    def test_rejects_extra_top_level_or_event_fields(self) -> None:
        payload = valid_payload()
        payload["nvr_host"] = "not-allowed"
        with self.assertRaisesRegex(TelemetryValidationError, "invalid_batch_contract"):
            validate_batch(payload)
        payload = valid_payload()
        payload["events"][0]["raw"] = "not-allowed"
        with self.assertRaisesRegex(TelemetryValidationError, "invalid_event_contract"):
            validate_batch(payload)
