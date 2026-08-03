"""Static container contract tests that do not require Docker."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CENTER = ROOT / "center"


class CenterContainerContractTests(unittest.TestCase):
    def test_dockerfile_is_portable_non_root_and_uses_data_volume(self) -> None:
        text = (CENTER / "Dockerfile").read_text()
        self.assertIn("FROM python:3.13-slim", text)
        self.assertNotIn("--platform=", text)
        self.assertIn("USER center:center", text)
        self.assertIn('VOLUME ["/data"]', text)
        self.assertIn("EXPOSE 8765", text)
        self.assertIn('"--port", "8765"', text)

    def test_compose_has_required_runtime_safety_and_log_rotation(self) -> None:
        text = (CENTER / "compose.yaml").read_text()
        self.assertIn("restart: unless-stopped", text)
        self.assertIn("center-data:/data", text)
        self.assertIn("read_only: true", text)
        self.assertIn("cap_drop:\n      - ALL", text)
        self.assertIn("no-new-privileges:true", text)
        self.assertIn('max-size: "10m"', text)
        self.assertIn('max-file: "3"', text)
        self.assertNotIn("privileged: true", text)

    def test_compose_defaults_to_loopback_not_public_listener(self) -> None:
        text = (CENTER / "compose.yaml").read_text()
        self.assertIn("${CENTER_BIND_ADDRESS:-127.0.0.1}:8765:8765", text)

    def test_dockerignore_excludes_local_environment_file(self) -> None:
        ignored = (CENTER / ".dockerignore").read_text().splitlines()
        self.assertIn(".env", ignored)
