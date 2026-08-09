"""Debug UI contract for 9Space Monitor Hub."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1] / "nine_space_monitor_hub" / "static"


class HubUiTests(unittest.TestCase):
    def test_ui_uses_relative_routes_and_safe_dom(self):
        source = (ROOT / "app.js").read_text()
        page = (ROOT / "index.html").read_text()
        self.assertIn("9Space 監控中樞", page)
        self.assertIn('fetch("api/v1/dashboard/summary"', source)
        self.assertIn("last-good-snapshot", source)
        self.assertIn("encodeURIComponent(siteId)", source)
        self.assertIn("textContent", source)
        self.assertNotIn("innerHTML", source)
        self.assertNotIn("statistics", source)
        self.assertNotIn("Ping", source)

    def test_ui_is_explicitly_debug_and_recorder_owns_history(self):
        page = (ROOT / "index.html").read_text()
        self.assertIn("Debug view", page)
        self.assertIn("Home Assistant Recorder", page)
