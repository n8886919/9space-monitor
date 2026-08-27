"""Focused Hub component API contracts without Home Assistant runtime."""

import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "nine_space_hub"
aiohttp = types.ModuleType("aiohttp")
aiohttp.ClientSession = object; aiohttp.ClientTimeout = lambda **kwargs: kwargs
aiohttp.ClientError = type("ClientError", (Exception,), {})
aiohttp.ContentTypeError = type("ContentTypeError", (Exception,), {})
sys.modules.setdefault("aiohttp", aiohttp)
SPEC = importlib.util.spec_from_file_location("hub_component_api", COMPONENT / "hub_api.py")
API = importlib.util.module_from_spec(SPEC); sys.modules[SPEC.name] = API; SPEC.loader.exec_module(API)
MIGRATION_SPEC = importlib.util.spec_from_file_location(
    "hub_component_migration", COMPONENT / "migration.py"
)
MIGRATION = importlib.util.module_from_spec(MIGRATION_SPEC)
sys.modules[MIGRATION_SPEC.name] = MIGRATION
MIGRATION_SPEC.loader.exec_module(MIGRATION)


def payload():
    return {"sites": [{"site_id": "safe-site", "display_name": "Safe",
        "site_reachable": True, "site_last_seen_at": 1, "updated_at": 1,
        "cameras": [{"camera_id": 1, "label": "Camera 01", "enabled": True, "snapshot_available": True,
            "last_good_age_seconds": 5, "latest_attempt": {"success": True, "timestamp": 1,
            "latency_ms": 12.5, "error_code": None}, "snapshot_success_count": 9,
            "snapshot_failure_count": 1, "snapshot_consecutive_failures": 0,
            "snapshot_success_rate": 90.0}]}]}


class HubComponentTests(unittest.TestCase):
    def test_parser_maps_snapshot_only_state(self):
        site = API.parse_sites(payload())["safe-site"]
        camera = site.cameras[0]
        self.assertIs(site.site_reachable, True)
        self.assertEqual(site.site_last_seen_at, 1)
        self.assertIs(camera.enabled, True)
        self.assertEqual(camera.snapshot_success_rate, 90.0)
        self.assertEqual(camera.snapshot_latency_ms, 12.5)
        for field in ("live_video", "recording_query_ok", "recording_recent"):
            self.assertFalse(hasattr(camera, field))

    def test_parser_rejects_duplicate_camera(self):
        invalid = payload(); invalid["sites"][0]["cameras"] *= 2
        with self.assertRaises(API.HubInvalidResponse): API.parse_sites(invalid)

    def test_manifest_exposes_camera_and_snapshot_statistics(self):
        manifest = json.loads((COMPONENT / "manifest.json").read_text())
        self.assertEqual(manifest["domain"], "nine_space_hub")
        entity_source = "\n".join(
            (COMPONENT / filename).read_text()
            for filename in ("binary_sensor.py", "sensor.py")
        )
        for retired in (
            "live_video", "recording_query_ok", "recording_recent",
            "recording_files_24h", "recording_coverage_24h", "last_recording",
            "last_snapshot_attempt",
        ):
            self.assertNotIn(f'key="{retired}"', entity_source)
        self.assertIn("snapshot_success_rate", entity_source)
        self.assertIn("site_reachable", entity_source)
        self.assertIn("site_last_seen", entity_source)

    def test_retired_entities_are_removed_by_exact_unique_id_suffix(self):
        for key in MIGRATION.RETIRED_ENTITY_KEYS:
            self.assertTrue(MIGRATION.is_retired_hub_unique_id(f"safe-site_1_{key}"))
        for current in (
            "snapshot_success", "snapshot_success_rate", "snapshot_age",
            "snapshot_latency", "site_reachable", "site_last_seen",
        ):
            self.assertFalse(MIGRATION.is_retired_hub_unique_id(f"safe-site_1_{current}"))

        entries = [
            SimpleNamespace(
                entity_id="binary_sensor.old_hub_live_video",
                platform="nine_space_hub",
                unique_id="safe-site_1_live_video",
            ),
            SimpleNamespace(
                entity_id="binary_sensor.current_hub_snapshot_success",
                platform="nine_space_hub",
                unique_id="safe-site_1_snapshot_success",
            ),
            SimpleNamespace(
                entity_id="binary_sensor.current_nvr_recording_recent",
                platform="nine_space_nvr_monitor",
                unique_id="safe-site_1_recording_recent",
            ),
        ]
        self.assertEqual(
            MIGRATION.retired_hub_entity_ids(entries),
            ("binary_sensor.old_hub_live_video",),
        )

    def test_translations_match_current_hub_entities(self):
        translation = json.loads((COMPONENT / "translations" / "zh-Hant.json").read_text())
        entities = translation["entity"]
        self.assertEqual(entities["binary_sensor"]["snapshot_success"]["name"], "截圖成功")
        self.assertEqual(entities["binary_sensor"]["site_reachable"]["name"], "站點可連線")
        self.assertEqual(entities["sensor"]["site_last_seen"]["name"], "站點上次可連線")
        serialized = json.dumps(entities, ensure_ascii=False)
        self.assertNotIn("上次截圖嘗試", serialized)
        self.assertNotIn("上次錄影", serialized)
