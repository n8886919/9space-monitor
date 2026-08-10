"""Tests for snapshot-only Hub registration."""

import unittest
from nine_space_snapshot.hub_registration import channel_ids, hub_registration_destination, safe_hub_ip, safe_site_metadata


class HubRegistrationTests(unittest.TestCase):
    def test_fixed_local_and_remote_registration_destinations(self):
        self.assertEqual(
            hub_registration_destination("hub.example.ts.net", "hub"),
            ("http://afa94ae2-9space-hub:8765/api/v1/snapshot-sites/register", "hub.example.ts.net:8765", None),
        )
        addresses = {"hub.example.ts.net": "100.64.0.10", "site.example.ts.net": "100.64.0.11"}
        self.assertEqual(
            hub_registration_destination("hub.example.ts.net", "site", resolver=addresses.get),
            ("http://100.64.0.10:8765/api/v1/snapshot-sites/register", "hub.example.ts.net:8765", "100.64.0.11"),
        )

    def test_validation_is_bounded(self):
        self.assertEqual(channel_ids(3), [1, 2, 3])
        self.assertEqual(channel_ids(True), [])
        self.assertIsNotNone(safe_site_metadata("safe-site", "Safe"))
        self.assertIsNone(safe_site_metadata("safe-site", "password=bad"))
        self.assertEqual(safe_hub_ip("hub.example.ts.net"), "hub.example.ts.net")
        self.assertIsNone(safe_hub_ip("http://hub.example.ts.net"))
