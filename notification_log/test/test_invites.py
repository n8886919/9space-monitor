"""Required Pikmin mushroom invite API regression tests."""

from __future__ import annotations

from datetime import datetime, timezone
import http.client
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
SPEC = importlib.util.spec_from_file_location("notification_log_invite_app", APP_PATH)
assert SPEC is not None and SPEC.loader is not None
APP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(APP)


class InviteApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "notifications.sqlite3"
        self._start_server()

    def tearDown(self) -> None:
        self._stop_server()
        self.tempdir.cleanup()

    def _start_server(self) -> None:
        self.store = APP.NotificationStore(self.database, retention_days=30, max_rows=100)
        self.server = APP.NotificationServer(("127.0.0.1", 0), self.store)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _restart_server(self) -> None:
        self._stop_server()
        self._start_server()

    def request(
        self,
        method: str,
        path: str,
        body: object | None = None,
    ) -> tuple[int, dict]:
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        headers: dict[str, str] = {}
        encoded = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        connection.request(method, path, body=encoded, headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        return response.status, payload

    def post_at(self, inviter: str, now: datetime) -> tuple[int, dict]:
        with patch.object(APP, "utc_now", return_value=now):
            return self.request("POST", "/api/v1/invites", {"inviter": inviter})

    def test_post_inviter_returns_201(self) -> None:
        status, payload = self.request("POST", "/api/v1/invites", {"inviter": "Alice"})
        self.assertEqual(201, status)
        self.assertEqual({"ok": True, "id": 1}, payload)

    def test_empty_inviter_returns_422(self) -> None:
        for inviter in ("", "   \t\n"):
            with self.subTest(inviter=repr(inviter)):
                status, payload = self.request(
                    "POST", "/api/v1/invites", {"inviter": inviter}
                )
                self.assertEqual(422, status)
                self.assertEqual("invalid_inviter", payload["error_code"])

    def test_chinese_inviter_is_preserved(self) -> None:
        status, _ = self.request("POST", "/api/v1/invites", {"inviter": "  小明  "})
        self.assertEqual(201, status)
        status, payload = self.request("GET", "/api/v1/invites?limit=100")
        self.assertEqual(200, status)
        self.assertEqual("小明", payload["invites"][0]["inviter"])

    def test_emoji_inviter_is_preserved(self) -> None:
        status, _ = self.request("POST", "/api/v1/invites", {"inviter": "🌱皮克敏🍄"})
        self.assertEqual(201, status)
        status, payload = self.request("GET", "/api/v1/invites/stats")
        self.assertEqual(200, status)
        self.assertEqual("🌱皮克敏🍄", payload["inviters"][0]["inviter"])

    def test_same_inviter_twice_has_count_two(self) -> None:
        for _ in range(2):
            status, _ = self.request("POST", "/api/v1/invites", {"inviter": "Alice"})
            self.assertEqual(201, status)
        status, payload = self.request("GET", "/api/v1/invites/stats")
        self.assertEqual(2, payload["inviters"][0]["count"])

    def test_stats_group_and_count_are_correct(self) -> None:
        for inviter in ("AAA", "BBB", "AAA", "CCC", "AAA", "BBB"):
            status, _ = self.request("POST", "/api/v1/invites", {"inviter": inviter})
            self.assertEqual(201, status)
        status, payload = self.request("GET", "/api/v1/invites/stats")
        self.assertEqual(200, status)
        self.assertEqual(6, payload["total_invites"])
        self.assertEqual(3, payload["unique_inviters"])
        self.assertEqual(
            [("AAA", 3), ("BBB", 2), ("CCC", 1)],
            [(item["inviter"], item["count"]) for item in payload["inviters"]],
        )

    def test_last_invited_at_uses_latest_server_utc_time(self) -> None:
        first = datetime(2026, 8, 30, 1, 2, 3, 4000, tzinfo=timezone.utc)
        latest = datetime(2026, 8, 31, 4, 5, 6, 7000, tzinfo=timezone.utc)
        self.post_at("Alice", first)
        self.post_at("Alice", latest)
        status, payload = self.request("GET", "/api/v1/invites/stats")
        self.assertEqual(200, status)
        self.assertEqual("2026-08-31T04:05:06.007Z", payload["inviters"][0]["last_invited_at"])

    def test_sqlite_data_survives_server_restart(self) -> None:
        status, _ = self.request("POST", "/api/v1/invites", {"inviter": "Alice"})
        self.assertEqual(201, status)
        self._restart_server()
        status, payload = self.request("GET", "/api/v1/invites/stats")
        self.assertEqual(200, status)
        self.assertEqual(1, payload["total_invites"])
        self.assertEqual("Alice", payload["inviters"][0]["inviter"])


if __name__ == "__main__":
    unittest.main()
