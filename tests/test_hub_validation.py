"""Snapshot registration validation contract."""

import unittest

from nine_space_hub.validation import RegistrationValidationError, validate_registration


def payload():
    return {"site_id": "safe-site", "display_name": "Safe", "channels": [1, 2],
            "concurrency": 2, "timeout_seconds": 15, "site_ip": None}


class RegistrationValidationTests(unittest.TestCase):
    def test_accepts_only_snapshot_registration_fields(self):
        value = validate_registration(payload())
        self.assertEqual(value.channels, (1, 2))
        for extra in ("events", "live_video", "recording_query_ok", "latest_telemetry"):
            invalid = payload(); invalid[extra] = True
            with self.assertRaises(RegistrationValidationError):
                validate_registration(invalid)

    def test_rejects_bad_channels_and_sensitive_text(self):
        for channels in ([], [0], [1, 1], [True]):
            invalid = payload(); invalid["channels"] = channels
            with self.assertRaises(RegistrationValidationError):
                validate_registration(invalid)
        invalid = payload(); invalid["display_name"] = "rtsp://private"
        with self.assertRaises(RegistrationValidationError):
            validate_registration(invalid)
