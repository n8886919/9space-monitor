"""Static checks for the canonical add-on's port configuration.

These are plain text assertions against config.yaml / run.sh (no PyYAML
dependency) to lock down the fix for the `http_port` naming mistake:

- `nvr_http_port` (default 80) is the Dahua NVR's own HTTP/CGI port.
- The add-on itself always listens on container port 8000, mapped to the
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


class AddonPortConfigTests(unittest.TestCase):
    def test_config_yaml_maps_container_port_8000_to_host_8222(self) -> None:
        self.assertRegex(CONFIG_YAML, r"(?m)^ports:\n\s+8000/tcp:\s*8222\s*$")

    def test_config_yaml_has_nvr_http_port_option_default_80(self) -> None:
        self.assertRegex(CONFIG_YAML, r"(?m)^\s*nvr_http_port:\s*80\s*$")
        self.assertRegex(CONFIG_YAML, r"(?m)^\s*nvr_http_port:\s*int\s*$")

    def test_config_yaml_no_longer_has_the_old_wrong_http_port_option(self) -> None:
        # The old (wrong) option name must not reappear.
        self.assertNotRegex(CONFIG_YAML, r"(?m)^\s*http_port:")

    def test_run_sh_starts_uvicorn_on_fixed_port_8000(self) -> None:
        self.assertRegex(RUN_SH, r"uvicorn main:app .*--port 8000\b")

    def test_run_sh_does_not_read_options_for_its_own_listen_port(self) -> None:
        # run.sh may mention nvr_http_port in a comment to explain why it is
        # irrelevant here, but it must not actually read options.json (e.g.
        # via jq) to decide its own uvicorn listen port.
        self.assertNotIn("options.json", RUN_SH)
        self.assertNotIn("jq ", RUN_SH)
        self.assertNotRegex(RUN_SH, r"--port\s+\"?\$")


if __name__ == "__main__":
    unittest.main()
