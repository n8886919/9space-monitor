"""Persistent Hub site registration contract."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from nine_space_hub.scheduler import SnapshotSite
from nine_space_hub.site_registry import SiteRegistry


class SiteRegistryTests(unittest.TestCase):
    def test_old_registry_defaults_to_enabled_and_new_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "sites.json"
            path.write_text(json.dumps({"version": 1, "sites": [{
                "site_id": "safe-site", "display_name": "Safe", "base_url": "http://100.64.0.10:8222",
                "channels": [1, 2], "concurrency": 1, "timeout_seconds": 2,
            }]}))
            registry = SiteRegistry(path)
            registry.load(refresh_seconds=30)
            self.assertEqual(registry.disabled_cameras(), set())
            self.assertTrue(registry.set_camera_enabled("safe-site", 2, False))
            restored = SiteRegistry(path)
            restored.load(refresh_seconds=30)
            self.assertEqual(restored.disabled_cameras(), {("safe-site", 2)})
            before = path.read_bytes()
            self.assertFalse(restored.set_camera_enabled("safe-site", 3, False))
            self.assertEqual(path.read_bytes(), before)

    def test_removed_channel_setting_is_pruned_and_new_channel_defaults_to_enabled(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "sites.json"
            registry = SiteRegistry(path)
            registry.upsert(SnapshotSite("safe-site", "Safe", "http://100.64.0.10:8222", (1, 2), 1, 2, 30))
            registry.set_camera_enabled("safe-site", 1, False)
            registry.set_camera_enabled("safe-site", 2, False)
            registry.upsert(SnapshotSite("safe-site", "Safe", "http://100.64.0.10:8222", (2, 3), 1, 2, 30))
            restored = SiteRegistry(path)
            restored.load(refresh_seconds=30)
            self.assertEqual(restored.disabled_cameras(), {("safe-site", 2)})

    def test_concurrent_choices_and_heartbeats_do_not_lose_settings(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "sites.json"
            registry = SiteRegistry(path)
            channels = tuple(range(1, 17))
            site = SnapshotSite("safe-site", "Safe", "http://100.64.0.10:8222", channels, 1, 2, 30)
            registry.upsert(site)
            def disable(channel):
                registry.upsert(site)
                return registry.set_camera_enabled("safe-site", channel, False)
            with ThreadPoolExecutor(max_workers=4) as executor:
                self.assertTrue(all(executor.map(disable, channels)))
            restored = SiteRegistry(path)
            restored.load(refresh_seconds=30)
            self.assertEqual(restored.disabled_cameras(), {("safe-site", channel) for channel in channels})

    def test_invalid_disabled_channels_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "sites.json"
            for disabled in ([True], [3], [1, 1], "1"):
                with self.subTest(disabled=disabled):
                    path.write_text(json.dumps({"version": 1, "sites": [{
                        "site_id": "safe-site", "display_name": "Safe", "base_url": "http://100.64.0.10:8222",
                        "channels": [1, 2], "concurrency": 1, "timeout_seconds": 2,
                        "disabled_channels": disabled,
                    }]}))
                    with self.assertRaisesRegex(ValueError, "invalid_site_registry"):
                        SiteRegistry(path).load(refresh_seconds=30)

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
