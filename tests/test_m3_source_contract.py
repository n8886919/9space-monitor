"""Source-level M3 boundary and identity checks."""

from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components/nvr_monitor"


class M3SourceContractTests(unittest.TestCase):
    def test_integration_has_no_nvr_credentials_or_direct_nvr_protocols(self):
        python = "\n".join(path.read_text() for path in INTEGRATION.glob("*.py"))
        for forbidden in (
            "CONF_NVR_HOST",
            "CONF_NVR_HTTP_PORT",
            "CONF_NVR_RTSP_PORT",
            "NvrConfig",
            "mediaFileFind.cgi",
            "DahuaRecordingClient",
            "CameraNetworkCoordinator",
            "async_ping",
            "icmplib",
            "nvr.username",
            "nvr.password",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, python)

    def test_manifest_has_no_icmplib(self):
        manifest = json.loads((INTEGRATION / "manifest.json").read_text())
        self.assertNotIn("icmplib", " ".join(manifest.get("requirements", [])))

    def test_manifest_version_is_0_2_9(self):
        manifest = json.loads((INTEGRATION / "manifest.json").read_text())
        self.assertEqual("0.2.9", manifest.get("version"))
        self.assertIn("recorder", manifest.get("after_dependencies", []))

    def test_local_addon_url_is_fixed_and_not_user_configured(self):
        const = (INTEGRATION / "const.py").read_text()
        config_flow = (INTEGRATION / "config_flow.py").read_text()
        init = (INTEGRATION / "__init__.py").read_text()
        self.assertIn(
            'ADDON_BASE_URL = "http://afa94ae2-9space-snapshot-addon:8000"',
            const,
        )
        self.assertIn("ADDON_SCHEMA = vol.Schema({})", config_flow)
        self.assertNotIn("CONF_ADDON_BASE_URL", config_flow)
        self.assertIn("AddonApiClient(ADDON_BASE_URL", init)

    def test_snapshot_camera_platform_and_client_are_removed(self):
        const = (INTEGRATION / "const.py").read_text()
        python = "\n".join(path.read_text() for path in INTEGRATION.glob("*.py"))
        self.assertFalse((INTEGRATION / "camera.py").exists())
        self.assertNotIn("Platform.CAMERA", const)
        self.assertNotIn("async_get_snapshot", python)
        self.assertNotIn("AddonSnapshotUnavailable", python)

    def test_unproduced_dahua_event_entities_are_removed(self):
        python = "\n".join(path.read_text() for path in INTEGRATION.glob("*.py"))
        self.assertFalse((INTEGRATION / "events.py").exists())
        for obsolete in (
            "motion_count_24h",
            "last_motion",
            "last_dahua_event",
            "motion_active",
            "video_loss",
            "video_blind",
            "dahua_event_received",
        ):
            with self.subTest(obsolete=obsolete):
                self.assertNotIn(obsolete, python)

    def test_debug_and_recording_gap_entities_use_addon_data(self):
        sensor = (INTEGRATION / "sensor.py").read_text()
        addon_api = (INTEGRATION / "addon_api.py").read_text()
        addon_main = (ROOT / "9space_snapshot_api/main.py").read_text()
        for key in (
            "nvr_first_packet",
            "nvr_probe_duration",
            "recording_gap_count_24h",
            "recording_gap_total_24h",
            "largest_recording_gap_24h",
        ):
            with self.subTest(key=key):
                self.assertIn(f'key="{key}"', sensor)
        self.assertIn("recording_gap_total_seconds_24h", addon_api)
        self.assertIn("largest_recording_gap_seconds_24h", addon_api)
        for field in (
            "recording_gap_count_24h",
            "recording_gap_total_seconds_24h",
            "largest_recording_gap_seconds_24h",
            "nvr_first_packet_ms",
            "nvr_probe_duration_ms",
        ):
            with self.subTest(field=field):
                self.assertIn(f'"{field}"', addon_main)

    def test_existing_entity_unique_id_formula_remains(self):
        entity = (INTEGRATION / "entity.py").read_text()
        self.assertIn('f"{entry.entry_id}_{subentry.subentry_id}_{description.key}"', entity)

    def test_channel_mapping_has_no_offset(self):
        coordinator = (INTEGRATION / "coordinator.py").read_text()
        self.assertIn("by_channel[camera.channel]", coordinator)
        self.assertNotIn("camera.channel +", coordinator)
        self.assertNotIn("camera.channel -", coordinator)

    def test_recording_and_live_aggregate_sensor_identities_are_preserved(self):
        sensor = (INTEGRATION / "sensor.py").read_text()
        for key in (
            "daily_online_rate",
            "nvr_live_video_disconnect_count_24h",
            "recording_count_24h",
            "recording_coverage_24h",
        ):
            with self.subTest(key=key):
                self.assertEqual(1, sensor.count(f'\n        key="{key}",'))

    def test_live_history_is_owned_only_by_integration(self):
        coordinator = (INTEGRATION / "coordinator.py").read_text()
        history = (INTEGRATION / "live_history.py").read_text()
        addon_state = (ROOT / "9space_snapshot_api/channel_state.py").read_text()
        addon_telemetry = (ROOT / "9space_snapshot_api/telemetry.py").read_text()
        self.assertIn("LiveHistoryStore", coordinator)
        self.assertIn("LIVE_WINDOW_MS", history)
        integration = (INTEGRATION / "__init__.py").read_text()
        recorder_history = (INTEGRATION / "recorder_history.py").read_text()
        self.assertIn("async_restore_live_history", integration)
        self.assertIn("history.get_significant_states", recorder_history)
        self.assertIn("async_add_executor_job", recorder_history)
        self.assertNotIn("_live_samples", addon_state)
        self.assertNotIn("_live_samples", addon_telemetry)
        self.assertNotIn("disconnect_count_24h", addon_telemetry)

    def test_telemetry_scheduler_stops_only_after_successful_platform_unload(self):
        init = (INTEGRATION / "__init__.py").read_text()
        self.assertIn("telemetry_unsubscribe", init)
        self.assertIn("platforms_unloaded=unloaded", init)
        self.assertNotIn("entry.async_on_unload(\n            async_track_time_interval", init)


if __name__ == "__main__":
    unittest.main()
