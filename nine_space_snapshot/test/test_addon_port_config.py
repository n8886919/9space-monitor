"""Static checks for the canonical app's port configuration.

These are plain text assertions against config.yaml / run.sh (no PyYAML
dependency) to lock down fixed network ports:

- Dahua RTSP is fixed at 554 and HTTP/CGI is fixed at 80.
- The app itself always listens on container port 8000, mapped to the
  monorepo dev instance's default host port 8222. This is NOT configurable
  via any option.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parents[1]
CONFIG_YAML = (ADDON_DIR / "config.yaml").read_text(encoding="utf-8")
RUN_SH = (ADDON_DIR / "run.sh").read_text(encoding="utf-8")
DOCKERFILE = (ADDON_DIR / "Dockerfile").read_text(encoding="utf-8")
CONSTANTS = (ADDON_DIR / "constants.py").read_text(encoding="utf-8")
BACKGROUND = (ADDON_DIR / "background.py").read_text(encoding="utf-8")
MAIN = (ADDON_DIR / "main.py").read_text(encoding="utf-8")


class AddonPortConfigTests(unittest.TestCase):
    def test_config_yaml_maps_container_port_8000_to_host_8222(self) -> None:
        self.assertRegex(CONFIG_YAML, r"(?m)^ports:\n\s+8000/tcp:\s*8222\s*$")

    def test_nvr_ports_are_fixed_constants_not_options(self) -> None:
        self.assertNotRegex(CONFIG_YAML, r"(?m)^\s*(?:rtsp_port|nvr_http_port):")
        self.assertIn("NVR_RTSP_PORT = 554", CONSTANTS)
        self.assertIn("NVR_HTTP_PORT = 80", CONSTANTS)
        self.assertNotIn('opts.get("rtsp_port")', BACKGROUND + MAIN)
        self.assertNotIn('opts.get("nvr_http_port")', BACKGROUND + MAIN)
        self.assertIn("COPY constants.py /app/constants.py", DOCKERFILE)

    def test_config_yaml_no_longer_has_the_old_wrong_http_port_option(self) -> None:
        # The old (wrong) option name must not reappear.
        self.assertNotRegex(CONFIG_YAML, r"(?m)^\s*http_port:")

    def test_hub_destination_is_one_hostname_only_option(self) -> None:
        self.assertRegex(CONFIG_YAML, r'(?m)^\s*hub_ip:\s*""\s*$')
        self.assertRegex(CONFIG_YAML, r"(?m)^\s*hub_ip:\s*str\s*$")
        for old in ("center_telemetry_url", "hub_snapshot_base_url", "hub_snapshot_refresh_seconds"):
            self.assertNotIn(old, CONFIG_YAML)

    def test_run_sh_starts_uvicorn_on_fixed_port_8000(self) -> None:
        self.assertRegex(RUN_SH, r"uvicorn main:app .*--port 8000\b")

    def test_run_sh_does_not_read_options_for_its_own_listen_port(self) -> None:
        # The process launcher must not read options to decide its listen port.
        self.assertNotIn("options.json", RUN_SH)
        self.assertNotIn("jq ", RUN_SH)
        self.assertNotRegex(RUN_SH, r"--port\s+\"?\$")


if __name__ == "__main__":
    unittest.main()
