"""Static Supervisor app contract tests."""

from pathlib import Path
import unittest

from nine_space_hub.app import APP_VERSION


ROOT = Path(__file__).resolve().parents[1]
HUB = ROOT / "nine_space_hub"


class HubAddonContractTests(unittest.TestCase):
    def test_addon_metadata_and_ingress_contract(self):
        config = (HUB / "config.yaml").read_text()
        self.assertIn("name: 9Space Hub", config)
        self.assertIn("slug: 9space_hub", config)
        self.assertIn("ingress_port: 8765", config)
        self.assertIn("ingress: true", config)
        self.assertIn("8765/tcp: 8765", config)
        self.assertIn("snapshot_refresh_seconds: 30", config)
        self.assertIn(f'version: "{APP_VERSION}"', config)
        self.assertNotIn("sites:", config)

    def test_container_runs_hub_package_and_has_no_sqlite_runtime(self):
        dockerfile = (HUB / "Dockerfile").read_text()
        run = (HUB / "run.sh").read_text()
        requirements = (HUB / "requirements.txt").read_text().lower()
        self.assertIn("ghcr.io/home-assistant/base", dockerfile)
        self.assertIn("nine_space_hub.app:app", run)
        self.assertIn("--no-proxy-headers", run)
        self.assertNotIn("sqlite", requirements)
        self.assertFalse((HUB / "storage.py").exists())
        self.assertFalse((HUB / "compose.yaml").exists())
