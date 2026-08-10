"""Repository-level consistency checks."""

from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOMAIN = "nine_space_nvr_monitor"
INTEGRATION = ROOT / "custom_components" / DOMAIN


class RepositoryTests(unittest.TestCase):
    """Check metadata without importing Home Assistant."""

    def test_manifest_matches_directory(self) -> None:
        manifest = json.loads((INTEGRATION / "manifest.json").read_text())

        self.assertEqual(DOMAIN, manifest["domain"])
        self.assertEqual("hub", manifest["integration_type"])
        self.assertTrue(manifest["config_flow"])
        self.assertEqual("local_polling", manifest["iot_class"])

    def test_translation_titles_match_strings(self) -> None:
        strings = json.loads((INTEGRATION / "strings.json").read_text())
        english = json.loads(
            (INTEGRATION / "translations" / "en.json").read_text()
        )

        self.assertEqual(strings["title"], english["title"])

    def test_old_identity_is_absent(self) -> None:
        old_tokens = (
            "nine_space_camera_monitor",
            "nine_space_nvr_monitor_legacy",
            "9Space Camera Monitor",
        )
        source_files = [
            *INTEGRATION.rglob("*.py"),
            *INTEGRATION.rglob("*.json"),
        ]

        for path in source_files:
            content = path.read_text()
            for token in old_tokens:
                with self.subTest(path=path, token=token):
                    self.assertNotIn(token, content)


if __name__ == "__main__":
    unittest.main()
