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

    def test_manifest_version_is_0_2_2(self):
        manifest = json.loads((INTEGRATION / "manifest.json").read_text())
        self.assertEqual("0.2.2", manifest.get("version"))

    def test_snapshot_camera_platform_and_client_are_removed(self):
        const = (INTEGRATION / "const.py").read_text()
        python = "\n".join(path.read_text() for path in INTEGRATION.glob("*.py"))
        self.assertFalse((INTEGRATION / "camera.py").exists())
        self.assertNotIn("Platform.CAMERA", const)
        self.assertNotIn("async_get_snapshot", python)
        self.assertNotIn("AddonSnapshotUnavailable", python)

    def test_existing_entity_unique_id_formula_remains(self):
        entity = (INTEGRATION / "entity.py").read_text()
        self.assertIn('f"{entry.entry_id}_{subentry.subentry_id}_{description.key}"', entity)

    def test_channel_mapping_has_no_offset(self):
        coordinator = (INTEGRATION / "coordinator.py").read_text()
        self.assertIn("by_channel[camera.channel]", coordinator)
        self.assertNotIn("camera.channel +", coordinator)
        self.assertNotIn("camera.channel -", coordinator)


if __name__ == "__main__":
    unittest.main()
