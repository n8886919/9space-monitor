"""Persistent Hub site registration contract."""

import json
from pathlib import Path
import tempfile
import unittest

from nine_space_hub.scheduler import SnapshotSite
from nine_space_hub.site_registry import SiteRegistry


class SiteRegistryTests(unittest.TestCase):
    def test_round_trip_persists_registration_without_runtime_health(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "sites.json"
            registry = SiteRegistry(path)
            site = SnapshotSite("safe-site", "Safe", "http://100.64.0.10:8222", (1, 2), 2, 4, 30)
            self.assertTrue(registry.upsert(site))

            restored = SiteRegistry(path).load(refresh_seconds=45)
            self.assertEqual(len(restored), 1)
            self.assertEqual(restored[0].site_id, "safe-site")
            self.assertEqual(restored[0].base_url, "http://100.64.0.10:8222")
            self.assertEqual(restored[0].refresh_seconds, 45)
            payload = json.loads(path.read_text())
            self.assertNotIn("reachable", json.dumps(payload))
            self.assertNotIn("failure", json.dumps(payload))

    def test_registry_rejects_untrusted_persisted_origin(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "sites.json"
            path.write_text(json.dumps({
                "version": 1,
                "sites": [{
                    "site_id": "safe-site", "display_name": "Safe",
                    "base_url": "http://example.com:8222", "channels": [1],
                    "concurrency": 1, "timeout_seconds": 2,
                }],
            }))
            with self.assertRaisesRegex(ValueError, "invalid_site_registry"):
                SiteRegistry(path).load(refresh_seconds=30)

    def test_same_site_id_updates_in_place(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "sites.json"
            registry = SiteRegistry(path)
            first = SnapshotSite("safe-site", "Old", "http://100.64.0.10:8222", (1,), 1, 2, 30)
            updated = SnapshotSite("safe-site", "New", "http://100.64.0.11:8222", (1, 2), 2, 3, 30)
            self.assertTrue(registry.upsert(first))
            self.assertTrue(registry.upsert(updated))
            restored = SiteRegistry(path).load(refresh_seconds=30)
            self.assertEqual([(site.display_name, site.channels) for site in restored], [("New", (1, 2))])
