"""Direct ASGI tests for snapshot-only 9Space Hub."""

import asyncio
import json
import os
import tempfile
import time
import unittest

from nine_space_hub.app import create_app
from nine_space_hub.scheduler import SnapshotSite
from nine_space_hub.snapshots import SnapshotStore
from nine_space_hub.state import CurrentState


async def asgi_request(app, method, path, *, chunks=None, content_length=True, client_host="127.0.0.1", extra_headers=()):
    chunks = list(chunks or []); headers = [(b"content-type", b"application/json")]
    if content_length: headers.append((b"content-length", str(sum(map(len, chunks))).encode()))
    headers.extend(extra_headers)
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
        "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"", "headers": headers,
        "client": (client_host, 1), "server": ("hub.test", 80), "root_path": ""}
    index = 0; request_complete = False; sent = []
    async def receive():
        nonlocal index, request_complete
        if index < len(chunks):
            body = chunks[index]; index += 1
            more = index < len(chunks)
            request_complete = not more
            return {"type": "http.request", "body": body, "more_body": more}
        if not request_complete:
            request_complete = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}
    async def send(message): sent.append(message)
    await app(scope, receive, send)
    start = next(item for item in sent if item["type"] == "http.response.start")
    body = b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body")
    return start["status"], {k.decode().lower(): v.decode() for k, v in start["headers"]}, body


class HubAppTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.site = SnapshotSite("safe-site", "Safe", "http://example.invalid", (1,), 1, 1, 30)
        self.store = SnapshotStore(os.path.join(self.tempdir.name, "snapshots"))
        self.state = CurrentState((self.site,))
        async def immediate(function, *args, **kwargs): return function(*args, **kwargs)
        self.app = create_app(sites=(self.site,), snapshots=self.store, state=self.state,
                              max_stale_seconds=120, run_sync=immediate)

    def tearDown(self): self.tempdir.cleanup()
    def request(self, method, path, **kwargs): return asyncio.run(asgi_request(self.app, method, path, **kwargs))

    def test_routes_are_snapshot_only(self):
        routes = {route.path for route in self.app.routes}
        self.assertIn("/api/v1/snapshot-sites/register", routes)
        self.assertIn("/api/v1/sites/{site_id}/cameras/{camera_id}/snapshot", routes)
        self.assertNotIn("/api/v1/telemetry", routes)
        for forbidden in ("events", "latest", "export.json", "ping-summary"):
            self.assertNotIn(forbidden, " ".join(routes))

    def test_dashboard_disables_html_cache_and_versions_static_assets(self):
        status, headers, body = self.request("GET", "/")
        page = body.decode()
        self.assertEqual(status, 200)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertIn("static/styles.css?v=0.3.5", page)
        self.assertIn("static/app.js?v=0.3.5", page)
        self.assertNotIn("__APP_VERSION__", page)

    def test_snapshot_contract_and_statistics(self):
        status, _, body = self.request("GET", "/api/v1/sites/safe-site/cameras/1/snapshot")
        self.assertEqual((status, json.loads(body)), (503, {"error_code": "snapshot_unavailable"}))
        self.store.write("safe-site", 1, b"opaque", timestamp_ms=int(time.time() * 1000))
        self.state.record_snapshot_attempt("safe-site", 1, success=True, timestamp_ms=1, latency_ms=12, error_code=None)
        status, headers, body = self.request("GET", "/api/v1/sites/safe-site/cameras/1/snapshot")
        self.assertEqual((status, headers["content-type"], body), (200, "image/jpeg", b"opaque"))
        _, _, body = self.request("GET", "/api/v1/sites")
        camera = json.loads(body)["sites"][0]["cameras"][0]
        self.assertEqual(camera["snapshot_success_rate"], 100.0)
        for forbidden in ("live_video", "recording_query_ok", "recording_recent", "recording_files_24h"):
            self.assertNotIn(forbidden, camera)

    def test_channel_enabled_endpoint_updates_runtime_state(self):
        status, _, body = self.request(
            "PUT",
            "/api/v1/sites/safe-site/cameras/1/enabled",
            chunks=[b'{"enabled":false}'],
        )
        self.assertEqual((status, json.loads(body)), (200, {"enabled": False}))
        _, _, body = self.request("GET", "/api/v1/dashboard/summary")
        self.assertFalse(json.loads(body)["sites"][0]["cameras"][0]["enabled"])

        status, _, body = self.request(
            "PUT",
            "/api/v1/sites/safe-site/cameras/1/enabled",
            chunks=[b'{"enabled":"false"}'],
        )
        self.assertEqual((status, json.loads(body)), (422, {"detail": "invalid_enabled"}))
