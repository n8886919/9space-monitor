"""Direct ASGI tests for Center without the unstable TestClient portal."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
import tempfile
import unittest

from center.app import create_app
from center.storage import TelemetryStorage
from center.validation import MAX_BODY_BYTES


async def asgi_request(
    app,
    method: str,
    path: str,
    *,
    chunks: list[bytes] | None = None,
    query: str = "",
    content_length: bool = True,
    pathsend: bool = False,
) -> tuple[int, dict[str, str], bytes]:
    """Drive the ASGI app directly, including controllable request chunks."""
    chunks = list(chunks or [])
    headers = [(b"content-type", b"application/json")]
    if content_length:
        headers.append((b"content-length", str(sum(map(len, chunks))).encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("center.test", 80),
        "root_path": "",
        "extensions": {"http.response.pathsend": {}} if pathsend else {},
    }
    request_index = 0
    request_complete = False
    sent: list[dict] = []

    async def receive() -> dict:
        nonlocal request_index, request_complete
        if request_index < len(chunks):
            body = chunks[request_index]
            request_index += 1
            request_complete = request_index == len(chunks)
            return {
                "type": "http.request",
                "body": body,
                "more_body": request_index < len(chunks),
            }
        if not request_complete:
            request_complete = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    await app(scope, receive, send)
    start = next(message for message in sent if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    response_headers = {
        key.decode().lower(): value.decode() for key, value in start["headers"]
    }
    return int(start["status"]), response_headers, body


class CenterAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        storage = TelemetryStorage(os.path.join(self.tempdir.name, "center.sqlite3"))

        async def run_immediately(function, *args, **kwargs):
            return function(*args, **kwargs)

        self.app = create_app(storage, run_sync=run_immediately)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def request(self, method: str, path: str, **kwargs):
        return asyncio.run(asgi_request(self.app, method, path, **kwargs))

    def test_health_and_data_routes_are_registered(self) -> None:
        routes = {route.path: route for route in self.app.routes}
        self.assertEqual(
            asyncio.run(routes["/healthz"].endpoint()), {"status": "ok"}
        )
        for path in (
            "/api/v1/telemetry",
            "/api/v1/sites/{site_id}/events",
            "/api/v1/sites/{site_id}/latest",
            "/api/v1/sites/{site_id}/export.json",
            "/api/v1/sites/{site_id}/cameras/{camera_id}/snapshot",
        ):
            self.assertIn(path, routes)
        serialized = str(self.app.openapi()).lower()
        self.assertIn("image/jpeg", serialized)
        self.assertNotIn("application/octet-stream", serialized)

    def test_body_bound_without_content_length_rejects_one_giant_chunk(self) -> None:
        status, _headers, _body = self.request(
            "POST",
            "/api/v1/telemetry",
            chunks=[b"x" * (MAX_BODY_BYTES + 1)],
            content_length=False,
        )
        self.assertEqual(status, 413)

    def test_body_bound_without_content_length_rejects_segmented_overflow(self) -> None:
        status, _headers, _body = self.request(
            "POST",
            "/api/v1/telemetry",
            chunks=[b"x" * (MAX_BODY_BYTES - 10), b"y" * 11],
            content_length=False,
        )
        self.assertEqual(status, 413)

    def test_sensitive_ingest_never_reaches_query_latest_or_export(self) -> None:
        sensitive_metrics = (
            {"state": "hunter2"},
            {"state": "192.168.0.10"},
            {"state": "2001:db8::1"},
            {"state": "rtsp://user:pass@example.invalid/live"},
            {"state": "Authorization: Digest secret"},
            {"state": "data:image/jpeg;base64,AAAA"},
            {"jpeg": "AAAA"},
            {"raw_payload": "hidden"},
            {"entity_id": "binary_sensor.192_168_0_101"},
        )
        markers: list[bytes] = []
        for index, metrics in enumerate(sensitive_metrics):
            payload = {
                "site_id": "chengde",
                "display_name": "承德",
                "source": "addon",
                "events": [
                    {
                        "event_id": hashlib.sha256(
                            f"addon|chengde|nvr.live|1|rejected-{index}".encode()
                        ).hexdigest(),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "kind": "nvr.live",
                        "channel_id": 1,
                        "metrics": metrics,
                    }
                ],
            }
            raw = json.dumps(payload).encode()
            status, _headers, response = self.request(
                "POST", "/api/v1/telemetry", chunks=[raw]
            )
            self.assertEqual(status, 422)
            for value in metrics.values():
                marker = str(value).encode()
                markers.append(marker)
                self.assertNotIn(marker, response)

        for path in (
            "/api/v1/sites/chengde/events",
            "/api/v1/sites/chengde/latest",
            "/api/v1/sites/chengde/export.json",
        ):
            status, _headers, body = self.request("GET", path)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["events"], [])
            for marker in markers:
                self.assertNotIn(marker, body)

    def test_secret_site_bad_kind_and_opaque_event_id_never_land(self) -> None:
        base = {
            "site_id": "chengde",
            "display_name": "承德",
            "source": "addon",
            "events": [
                {
                    "event_id": hashlib.sha256(
                        b"addon|chengde|nvr.live|1|identity-contract"
                    ).hexdigest(),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "kind": "nvr.live",
                    "channel_id": 1,
                    "metrics": {"live_video": True},
                }
            ],
        }
        variants = []
        secret_site = json.loads(json.dumps(base))
        secret_site["site_id"] = "password-site"
        variants.append(secret_site)
        bad_kind = json.loads(json.dumps(base))
        bad_kind["events"][0]["kind"] = "password"
        variants.append(bad_kind)
        opaque_event = json.loads(json.dumps(base))
        opaque_event["events"][0]["event_id"] = "hunter2"
        variants.append(opaque_event)

        for payload in variants:
            status, _headers, _body = self.request(
                "POST",
                "/api/v1/telemetry",
                chunks=[json.dumps(payload).encode()],
            )
            self.assertEqual(status, 422)

        status, _headers, _body = self.request(
            "GET", "/api/v1/sites/token-site/events"
        )
        self.assertEqual(status, 422)
        for path in (
            "/api/v1/sites/chengde/events",
            "/api/v1/sites/chengde/latest",
            "/api/v1/sites/chengde/export.json",
        ):
            status, _headers, body = self.request("GET", path)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["events"], [])
