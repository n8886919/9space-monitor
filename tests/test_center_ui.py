"""M5F-3 UI summary and static-client contract tests (opaque fake bytes only)."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from unittest import mock

from center.app import create_app
from center.snapshots import SnapshotStore
from center.storage import TelemetryStorage
from center.validation import ValidatedBatch, ValidatedEvent
from tests.test_center_app import asgi_request


NOW_MS = 1_800_000_000_000


class CenterUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.storage = TelemetryStorage(os.path.join(self.tempdir.name, "center.sqlite3"))
        self.snapshots = SnapshotStore(os.path.join(self.tempdir.name, "snapshots"))
        self.storage.register_snapshot_camera("chengde", 1, "Chengde", now_ms=NOW_MS)

        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        self.app = create_app(self.storage, snapshots=self.snapshots, run_sync=immediate)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def request(self, path: str, *, pathsend: bool = False):
        return asyncio.run(asgi_request(self.app, "GET", path, pathsend=pathsend))

    def test_summary_has_safe_camera_site_stats_and_failed_last_attempt(self) -> None:
        self.snapshots.write("chengde", 1, b"opaque-last-good", timestamp_ms=NOW_MS - 30_000)
        self.storage.record_snapshot_attempt(
            "chengde", 1, success=True, timestamp_ms=NOW_MS - 30_000,
            latency_ms=10.0, now_ms=NOW_MS,
        )
        self.storage.record_snapshot_attempt(
            "chengde", 1, success=False, timestamp_ms=NOW_MS - 1_000,
            latency_ms=30.0, error_code="snapshot_unavailable", now_ms=NOW_MS,
        )
        self.storage.ingest(ValidatedBatch(
            site_id="chengde", display_name="Chengde", source="addon",
            events=(ValidatedEvent(
                event_id="a" * 64, timestamp_ms=NOW_MS, kind="producer.health",
                channel_id=None, metrics={"dropped_events": 2, "available": True},
            ),),
        ), now_ms=NOW_MS)
        with mock.patch("center.app.time.time", return_value=NOW_MS / 1000):
            status, _headers, body = self.request("/api/v1/dashboard/summary")
        self.assertEqual(status, 200)
        summary = json.loads(body)
        camera = summary["sites"][0]["cameras"][0]
        self.assertEqual(camera["latest_attempt"]["status"], "failure")
        self.assertEqual(camera["last_good_age_seconds"], 30)
        self.assertEqual(camera["statistics"]["1h"]["attempts"], 2)
        self.assertEqual(summary["sites"][0]["statistics"]["1h"]["success_rate"], 0.5)
        self.assertEqual(summary["sites"][0]["producer_health"][0]["metrics"]["dropped_events"], 2)
        self.assertEqual(summary["capacity"]["snapshots"]["file_count"], 1)
        self.assertEqual(summary["capacity"]["snapshots"]["bytes"], len(b"opaque-last-good"))
        encoded = json.dumps(summary).lower()
        for forbidden in ("opaque-last-good", "base_url", "snapshot_path", "file_path", "\"path\""):
            self.assertNotIn(forbidden, encoded)

    def test_public_stale_snapshot_is_503_but_ui_last_good_is_200(self) -> None:
        self.snapshots.write("chengde", 1, b"opaque-last-good", timestamp_ms=NOW_MS - 121_000)
        with mock.patch("center.app.time.time", return_value=NOW_MS / 1000):
            status, _headers, body = self.request("/api/v1/sites/chengde/cameras/1/snapshot")
            ui_status, ui_headers, ui_body = self.request(
                "/api/v1/sites/chengde/cameras/1/last-good-snapshot"
            )
        self.assertEqual((status, json.loads(body)), (503, {"error_code": "snapshot_stale"}))
        self.assertEqual(ui_status, 200)
        self.assertEqual(ui_headers["content-type"], "image/jpeg")
        self.assertEqual(ui_body, b"opaque-last-good")

    def test_static_client_uses_relative_safe_dom_and_metadata_tables(self) -> None:
        root = os.path.join(os.path.dirname(__file__), "..", "center", "static")
        with open(os.path.join(root, "app.js"), encoding="utf-8") as handle:
            source = handle.read()
        with open(os.path.join(root, "styles.css"), encoding="utf-8") as handle:
            css = handle.read()
        self.assertIn("fetch(\"api/v1/dashboard/summary\"", source)
        self.assertIn("ping-summary", source)
        self.assertIn("encodeURIComponent(site.site_id)", source)
        self.assertIn("textContent", source)
        self.assertNotIn("innerHTML", source)
        self.assertIn("last-good-snapshot", source)
        self.assertIn('createElement("img")', source)
        self.assertIn('["Camera", "Ping (ICMP)", "延遲/時", "丟包率/時", "延遲/日", "丟包率/日"]', source)
        self.assertIn("NVR live / recording", source)
        self.assertIn("snapshot attempt", source)
        self.assertIn("latency_population_stddev_ms", source)
        self.assertIn("invalid_file_count_24h", source)
        self.assertIn("gap_total_seconds_24h", source)
        self.assertIn("largest_gap_seconds_24h", source)
        self.assertIn('liveSamples > 0', source)
        self.assertIn('"—（無樣本）"', source)
        self.assertNotIn('|| (site.producer_health || [])[0]', source)
        self.assertIn("selectedSiteId !== site.site_id", source)
        self.assertIn("last-good snapshot store", source)
        self.assertNotIn('shown(site, "logical_limit_bytes")', source)
        self.assertIn("不完整", source)
        self.assertIn("無樣本", source)
        self.assertIn("site-summary", css)
        self.assertIn("data-table", css)
        self.assertIn("attempt-success", css)
        self.assertIn("attempt-failure", css)
        self.assertIn("placeholder", source)
        self.assertIn("setTimeout(refreshLoop, 15000)", source)
        self.assertIn("tabs.replaceChildren()", source)
        self.assertIn("selectedSiteId", source)
        self.assertNotIn("JSON.stringify", source)

    def test_dashboard_index_and_static_asset_are_served(self) -> None:
        async def direct_sync(function, *args, **kwargs):
            return function(*args, **kwargs)

        with mock.patch("starlette.responses.anyio.to_thread.run_sync", side_effect=direct_sync), \
             mock.patch("starlette.staticfiles.anyio.to_thread.run_sync", side_effect=direct_sync):
            status, headers, _body = self.request("/", pathsend=True)
            static_status, static_headers, _static_body = self.request(
                "/static/styles.css", pathsend=True
            )
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "text/html; charset=utf-8")
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertIn("default-src 'self'", headers["content-security-policy"])
        self.assertEqual(static_status, 200)
        self.assertTrue(static_headers["content-type"].startswith("text/css"))
