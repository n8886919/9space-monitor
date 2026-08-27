"""Debug UI contract for 9Space Hub."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1] / "nine_space_hub" / "static"


class HubUiTests(unittest.TestCase):
    def test_ui_uses_relative_routes_and_safe_dom(self):
        source = (ROOT / "app.js").read_text()
        page = (ROOT / "index.html").read_text()
        self.assertIn("9Space 中樞", page)
        self.assertIn('fetch("api/v1/dashboard/summary"', source)
        self.assertIn("last-good-snapshot", source)
        self.assertIn("encodeURIComponent(siteId)", source)
        self.assertIn("textContent", source)
        self.assertNotIn("innerHTML", source)
        self.assertNotIn("statistics", source)
        self.assertNotIn("Ping", source)

    def test_ui_has_channel_toggle_and_freshness_borders(self):
        source = (ROOT / "app.js").read_text()
        styles = (ROOT / "styles.css").read_text()
        self.assertIn('["CH",', source)
        self.assertIn('`CH ${String(camera.camera_id).padStart(2, "0")} 啟用`', source)
        self.assertIn('camera.enabled', source)
        self.assertIn('/enabled`', source)
        self.assertIn('snapshot_available ? "snapshot-fresh" : "snapshot-stale"', source)
        self.assertIn('.snapshot-fresh', styles)
        self.assertIn('.snapshot-stale', styles)

    def test_disabled_channels_hide_metrics_and_last_good_cards(self):
        source = (ROOT / "app.js").read_text()
        styles = (ROOT / "styles.css").read_text()
        self.assertIn('const metrics = camera.enabled', source)
        self.assertIn('["—", "—", "—", "—", "—", "—"]', source)
        self.assertIn('site.cameras.filter(camera => camera.enabled)', source)
        self.assertIn('className: camera.enabled ? "" : "channel-disabled"', source)
        self.assertIn('.channel-disabled', styles)

    def test_ui_uses_icons_and_versioned_static_assets(self):
        source = (ROOT / "app.js").read_text()
        page = (ROOT / "index.html").read_text()
        self.assertIn('value === true ? "✓" : value === false ? "✕" : "—"', source)
        self.assertNotIn('value === true ? "是"', source)
        self.assertIn('static/styles.css?v=__APP_VERSION__', page)
        self.assertIn('static/app.js?v=__APP_VERSION__', page)

    def test_site_tabs_use_health_borders(self):
        source = (ROOT / "app.js").read_text()
        styles = (ROOT / "styles.css").read_text()
        self.assertIn('site.site_reachable === true', source)
        self.assertIn('"site-online"', source)
        self.assertIn('"site-offline"', source)
        self.assertIn('"site-unknown"', source)
        self.assertIn('button.site-online', styles)
        self.assertIn('button.site-offline', styles)

    def test_ui_is_explicitly_debug_and_recorder_owns_history(self):
        page = (ROOT / "index.html").read_text()
        self.assertIn("Debug view", page)
        self.assertIn("Home Assistant Recorder", page)
