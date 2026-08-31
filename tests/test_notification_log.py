"""End-to-end API and SQLite tests for the notification log add-on."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import http.client
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "notification_log" / "app.py"
SPEC = importlib.util.spec_from_file_location("notification_log_app", APP_PATH)
assert SPEC is not None and SPEC.loader is not None
APP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(APP)


class NotificationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = APP.NotificationStore(
            Path(self.tempdir.name) / "notifications.sqlite3",
            retention_days=30,
            max_rows=100,
        )
        self.server = APP.NotificationServer(("127.0.0.1", 0), self.store, "test-token")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tempdir.cleanup()

    def request(
        self,
        method: str,
        path: str,
        body: object | None = None,
        token: str | None = "test-token",
    ) -> tuple[int, dict]:
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        headers: dict[str, str] = {}
        encoded = None
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if body is not None:
            headers["Content-Type"] = "application/json"
            encoded = json.dumps(body).encode()
        connection.request(method, path, body=encoded, headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        return response.status, payload

    def test_health_is_public_but_data_is_authenticated(self) -> None:
        status, payload = self.request("GET", "/healthz", token=None)
        self.assertEqual(200, status)
        self.assertEqual({"status": "ok"}, payload)

        status, payload = self.request("GET", "/api/v1/notifications", token=None)
        self.assertEqual(401, status)
        self.assertEqual("unauthorized", payload["error_code"])

    def test_tasker_post_is_persisted_and_returned(self) -> None:
        body = {
            "package_name": "com.example.chat",
            "app_name": "Chat",
            "title": "你好",
            "text": "測試通知",
            "occurred_at": 1_787_000_000_000,
            "source_device": "phone",
            "extra": {"profile": "notification-capture"},
        }
        status, created = self.request("POST", "/api/v1/notifications", body)
        self.assertEqual(201, status)
        self.assertEqual(1, created["id"])
        self.assertEqual("你好", created["title"])
        self.assertEqual("2026-08-17T20:53:20.000Z", created["occurred_at"])
        self.assertEqual({"profile": "notification-capture"}, created["extra"])

        status, listing = self.request(
            "GET", "/api/v1/notifications?package_name=com.example.chat&limit=1"
        )
        self.assertEqual(200, status)
        self.assertEqual([created], listing["items"])
        self.assertEqual(1, listing["next_before_id"])

        status, stats = self.request("GET", "/api/v1/stats")
        self.assertEqual(200, status)
        self.assertEqual(1, stats["row_count"])

    def test_payload_validation_is_strict_and_does_not_insert(self) -> None:
        status, payload = self.request(
            "POST",
            "/api/v1/notifications",
            {"title": "ok", "password": "must-not-be-stored"},
        )
        self.assertEqual(422, status)
        self.assertEqual("unknown_fields", payload["error_code"])
        self.assertEqual(0, self.store.stats()["row_count"])

        status, payload = self.request(
            "POST", "/api/v1/notifications", {"title": "ok", "occurred_at": "2026-08-31"}
        )
        self.assertEqual(422, status)
        self.assertEqual("invalid_timestamp", payload["error_code"])

    def test_pagination_is_newest_first(self) -> None:
        for title in ("one", "two", "three"):
            status, _ = self.request("POST", "/api/v1/notifications", {"title": title})
            self.assertEqual(201, status)
        status, first = self.request("GET", "/api/v1/notifications?limit=2")
        self.assertEqual(["three", "two"], [item["title"] for item in first["items"]])
        self.assertEqual(2, first["next_before_id"])
        status, second = self.request(
            "GET", f"/api/v1/notifications?limit=2&before_id={first['next_before_id']}"
        )
        self.assertEqual(["one"], [item["title"] for item in second["items"]])
        self.assertIsNone(second["next_before_id"])


class NotificationStoreTests(unittest.TestCase):
    def test_retention_prunes_old_rows_and_row_limit_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store = APP.NotificationStore(
                Path(tempdir) / "notifications.sqlite3", retention_days=1, max_rows=100
            )
            old = datetime(2026, 8, 1, tzinfo=timezone.utc)
            payload = APP.validate_notification({"title": "old"}, old)
            store.insert(payload, old)
            current = old + timedelta(days=2)
            for index in range(101):
                payload = APP.validate_notification({"title": str(index)}, current)
                store.insert(payload, current)
            items = store.list(500, None, None)
            self.assertEqual(100, len(items))
            self.assertEqual("100", items[0]["title"])
            self.assertEqual("1", items[-1]["title"])


class NotificationAddonContractTests(unittest.TestCase):
    def test_addon_metadata_exposes_only_the_tasker_port(self) -> None:
        config = (ROOT / "notification_log" / "config.yaml").read_text()
        self.assertIn('version: "0.1.0"', config)
        self.assertIn("slug: 9space_notification_log", config)
        self.assertIn("  8099/tcp: 8099", config)
        self.assertIn("  auth_token: password", config)
        self.assertNotIn("8122", config)

    def test_container_packages_app_and_uses_persistent_addon_data(self) -> None:
        dockerfile = (ROOT / "notification_log" / "Dockerfile").read_text()
        run_script = (ROOT / "notification_log" / "run.sh").read_text()
        source = APP_PATH.read_text()
        self.assertIn("COPY app.py /app/app.py", dockerfile)
        self.assertIn("COPY run.sh /run.sh", dockerfile)
        self.assertIn("exec python3 /app/app.py", run_script)
        self.assertIn('Path("/data/notifications.sqlite3")', source)
        self.assertIn('Path("/data/options.json")', source)


if __name__ == "__main__":
    unittest.main()
