"""Direct ASGI tests for the history-free 9Space Monitor Hub."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
import tempfile
import unittest

from nine_space_monitor_hub.app import create_app
from nine_space_monitor_hub.scheduler import SnapshotSite
from nine_space_monitor_hub.snapshots import SnapshotStore
from nine_space_monitor_hub.state import CurrentState
from nine_space_monitor_hub.validation import MAX_BODY_BYTES


async def asgi_request(app, method: str, path: str, *, chunks=None, content_length=True, pathsend=False):
    chunks = list(chunks or [])
    headers = [(b"content-type", b"application/json")]
    if content_length:
        headers.append((b"content-length", str(sum(map(len, chunks))).encode()))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": headers, "client": ("127.0.0.1", 1),
        "server": ("hub.test", 80), "root_path": "",
        "extensions": {"http.response.pathsend": {}} if pathsend else {},
    }
    index = 0
    sent = []

    async def receive():
        nonlocal index
        if index < len(chunks):
            body = chunks[index]; index += 1
            return {"type": "http.request", "body": body, "more_body": index < len(chunks)}
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    start = next(item for item in sent if item["type"] == "http.response.start")
    body = b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body")
    response_headers = {key.decode().lower(): value.decode() for key, value in start["headers"]}
    return start["status"], response_headers, body


class HubAppTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.site = SnapshotSite("safe-site", "Safe", "http://example.invalid", (1,), 1, 1, 30)
        self.store = SnapshotStore(os.path.join(self.tempdir.name, "snapshots"))
        self.state = CurrentState((self.site,))

        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        self.app = create_app(
            sites=(self.site,), snapshots=self.store, state=self.state,
            max_stale_seconds=120, run_sync=immediate,
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def request(self, method, path, **kwargs):
        return asyncio.run(asgi_request(self.app, method, path, **kwargs))

    def payload(self, metrics=None):
        return {
            "site_id": "safe-site", "display_name": "Safe", "source": "addon",
            "events": [{
                "event_id": hashlib.sha256(b"event").hexdigest(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "kind": "nvr.live", "channel_id": 1,
                "metrics": metrics or {"live_video": True, "checked_at": datetime.now(timezone.utc).isoformat()},
            }],
        }

    def test_routes_expose_current_state_and_snapshot_but_no_history_export(self):
        routes = {route.path for route in self.app.routes}
        self.assertIn("/api/v1/telemetry", routes)
        self.assertIn("/api/v1/sites", routes)
        self.assertIn("/api/v1/sites/{site_id}/cameras/{camera_id}/snapshot", routes)
        serialized = str(self.app.openapi()).lower()
        self.assertIn("image/jpeg", serialized)
        for forbidden in ("/events", "/latest", "/export.json", "ping-summary"):
            self.assertNotIn(forbidden, " ".join(routes))

    def test_ingest_keeps_current_state_in_memory(self):
        raw = json.dumps(self.payload()).encode()
        status, _headers, body = self.request("POST", "/api/v1/telemetry", chunks=[raw])
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"accepted": 1})
        status, _headers, body = self.request("GET", "/api/v1/sites")
        camera = json.loads(body)["sites"][0]["cameras"][0]
        self.assertIs(camera["live_video"], True)
        self.assertNotIn("statistics", camera)

    def test_unknown_site_is_rejected_and_not_discovered(self):
        payload = self.payload(); payload["site_id"] = "unknown"
        status, _headers, _body = self.request(
            "POST", "/api/v1/telemetry", chunks=[json.dumps(payload).encode()]
        )
        self.assertEqual(status, 404)
        status, _headers, body = self.request("GET", "/api/v1/sites")
        self.assertEqual([site["site_id"] for site in json.loads(body)["sites"]], ["safe-site"])

    def test_addon_registration_discovers_site_without_hub_site_options(self):
        empty_state = CurrentState(())

        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        app = create_app(
            sites=(), snapshots=self.store, state=empty_state,
            max_stale_seconds=120, run_sync=immediate,
        )
        payload = self.payload()
        payload["snapshot_registration"] = {
            "base_url": "http://100.64.0.10:8222",
            "channels": [1, 2],
            "concurrency": 1,
            "timeout_seconds": 15,
            "refresh_seconds": 30,
        }
        status, _headers, _body = asyncio.run(asgi_request(
            app, "POST", "/api/v1/telemetry", chunks=[json.dumps(payload).encode()]
        ))
        self.assertEqual(status, 200)
        status, _headers, body = asyncio.run(asgi_request(app, "GET", "/api/v1/sites"))
        discovered = json.loads(body)["sites"]
        self.assertEqual(discovered[0]["site_id"], "safe-site")
        self.assertEqual([camera["camera_id"] for camera in discovered[0]["cameras"]], [1, 2])
        self.assertNotIn("base_url", json.dumps(discovered))

    def test_streaming_body_bound_and_sensitive_values_fail_closed(self):
        status, _headers, _body = self.request(
            "POST", "/api/v1/telemetry", chunks=[b"x" * (MAX_BODY_BYTES + 1)], content_length=False
        )
        self.assertEqual(status, 413)
        raw = json.dumps(self.payload({"state": "rtsp://user:pass@example.invalid/live"})).encode()
        status, _headers, body = self.request("POST", "/api/v1/telemetry", chunks=[raw])
        self.assertEqual(status, 422)
        self.assertNotIn(b"user:pass", body)

    def test_snapshot_contract_uses_one_last_good_jpeg(self):
        status, _headers, body = self.request("GET", "/api/v1/sites/safe-site/cameras/1/snapshot")
        self.assertEqual((status, json.loads(body)), (503, {"error_code": "snapshot_unavailable"}))
        self.store.write("safe-site", 1, b"opaque", timestamp_ms=int(datetime.now(timezone.utc).timestamp() * 1000))
        status, headers, body = self.request("GET", "/api/v1/sites/safe-site/cameras/1/snapshot")
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "image/jpeg")
        self.assertEqual(body, b"opaque")
