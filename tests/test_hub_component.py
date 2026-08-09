"""Focused component API and source contracts without Home Assistant runtime."""

from __future__ import annotations

import json
import importlib.util
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "nine_space_monitor_hub"
aiohttp = types.ModuleType("aiohttp")
aiohttp.ClientSession = object
aiohttp.ClientTimeout = lambda **kwargs: kwargs
aiohttp.ClientError = type("ClientError", (Exception,), {})
aiohttp.ContentTypeError = type("ContentTypeError", (Exception,), {})
sys.modules.setdefault("aiohttp", aiohttp)
SPEC = importlib.util.spec_from_file_location("hub_component_api", COMPONENT / "hub_api.py")
assert SPEC and SPEC.loader
API = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = API
SPEC.loader.exec_module(API)
HubInvalidResponse = API.HubInvalidResponse
normalize_base_url = API.normalize_base_url
parse_sites = API.parse_sites


def payload():
    return {"sites": [{
        "site_id": "safe-site", "display_name": "Safe", "updated_at": 1_800_000_000_000,
        "cameras": [{
            "camera_id": 1, "label": "Camera 01", "snapshot_available": True,
            "last_good_age_seconds": 5,
            "latest_attempt": {"success": True, "timestamp": 1_800_000_000_000, "latency_ms": 12.5, "error_code": None},
            "live_video": True, "live_checked_at": "2026-08-10T00:00:00+00:00",
            "recording_query_ok": True, "recording_recent": True,
            "last_recording": "2026-08-10T00:00:00+00:00",
            "recording_checked_at": "2026-08-10T00:00:00+00:00",
            "recording_files_24h": 10, "recording_coverage_24h": 99.5,
            "recording_error": None,
        }],
    }]}


class HubComponentTests(unittest.TestCase):
    def test_parser_maps_sites_and_current_camera_state(self):
        sites = parse_sites(payload())
        camera = sites["safe-site"].cameras[0]
        self.assertEqual((camera.site_id, camera.camera_id), ("safe-site", 1))
        self.assertIs(camera.live_video, True)
        self.assertEqual(camera.snapshot_latency_ms, 12.5)

    def test_parser_rejects_duplicates_and_out_of_contract_values(self):
        duplicate = payload(); duplicate["sites"][0]["cameras"] *= 2
        with self.assertRaises(HubInvalidResponse):
            parse_sites(duplicate)
        invalid = payload(); invalid["sites"][0]["cameras"][0]["recording_coverage_24h"] = 101
        with self.assertRaises(HubInvalidResponse):
            parse_sites(invalid)

    def test_url_rejects_credentials_and_query(self):
        self.assertEqual(normalize_base_url("http://hub:8765/"), "http://hub:8765")
        for value in ("http://user:pass@hub:8765", "http://hub:8765/?token=x"):
            with self.assertRaises(ValueError):
                normalize_base_url(value)

    def test_manifest_and_platforms_expose_recorder_friendly_current_entities(self):
        manifest = json.loads((COMPONENT / "manifest.json").read_text())
        self.assertEqual(manifest["domain"], "nine_space_monitor_hub")
        self.assertTrue(manifest["config_flow"])
        const = (COMPONENT / "const.py").read_text()
        self.assertIn('"camera", "binary_sensor", "sensor"', const)
        self.assertIn("Home Assistant Recorder", (COMPONENT / "binary_sensor.py").read_text())
        for forbidden in ("sqlite", "history.get_significant_states", ".storage"):
            source = "\n".join(path.read_text() for path in COMPONENT.rglob("*.py")).lower()
            self.assertNotIn(forbidden.lower(), source)
