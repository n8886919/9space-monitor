"""Minimal fake-based tests for the add-on API (legacy + /api/v1 skeleton).

These tests do not require a real NVR. They monkeypatch the ffmpeg capture
function and the options loader so the FastAPI app can be exercised purely
with fakes (per README M2A: "使用 fake service/result 寫最少測試").

Run locally with:
    pip install fastapi httpx
    python -m unittest discover -s 9space_snapshot_api/test -v
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ADDON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_DIR))

import main  # noqa: E402
import live_probe  # noqa: E402
import recording_query  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


FAKE_OPTS = {
    "nvr_host": "127.0.0.1",
    "rtsp_port": 554,
    "username": "admin",
    "password": "password",
    "subtype": 0,
    "health_timeout_ms": 100,
    "jpeg_qv": 32,
    "max_concurrency": 2,
    "nvr_http_port": 80,
    "channel_count": 3,
    "snapshot_cache_ms": 0,
}


def _fake_live_probe_channel(channel_id, nvr):
    # Deterministic "NVR reachable, no video yet" fake so tests never
    # depend on real sockets or a real NVR.
    return {"live_video": False, "error_code": "no_video"}


def _fake_recording_query_channel(channel_id, nvr):
    # Deterministic "query ok, nothing recent" fake.
    return {
        "recording_query_ok": True,
        "recording_recent": False,
        "last_recording": None,
        "error_code": None,
        "metrics": {
            "valid_file_count_24h": 42,
            "recording_coverage_24h_pct": 97.5,
        },
    }


class AddonApiTests(unittest.TestCase):
    def setUp(self) -> None:
        main._cache.clear()
        main._sem = None
        main._channel_store.clear()
        self._opts_patch = patch.object(main, "_load_options", return_value=dict(FAKE_OPTS))
        self._opts_patch.start()
        self._live_probe_patch = patch.object(
            live_probe, "probe_channel", side_effect=_fake_live_probe_channel
        )
        self._live_probe_patch.start()
        self._recording_query_patch = patch.object(
            recording_query, "query_channel", side_effect=_fake_recording_query_channel
        )
        self._recording_query_patch.start()
        self._client_cm = TestClient(main.app)
        self.client = self._client_cm.__enter__()  # triggers startup event
        # Startup's first probe rounds run in the background; wait for them
        # so assertions below are deterministic instead of racing the task.
        self.assertTrue(main._live_first_round_ready.wait(timeout=5))
        self.assertTrue(main._recording_first_round_ready.wait(timeout=5))

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)
        self._recording_query_patch.stop()
        self._live_probe_patch.stop()
        self._opts_patch.stop()

    def test_healthz_ok(self) -> None:
        resp = self.client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    def test_nvr_http_port_option_does_not_affect_app_behaviour(self) -> None:
        # nvr_http_port configures the Dahua NVR's own HTTP/CGI port, not the
        # add-on's uvicorn listen port. main.py must never read it, so
        # changing its value cannot change any response here.
        alt_opts = dict(FAKE_OPTS)
        alt_opts["nvr_http_port"] = 8080
        with patch.object(main, "_load_options", return_value=alt_opts):
            resp = self.client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    def test_list_channels_uses_channel_count_option(self) -> None:
        resp = self.client.get("/api/v1/channels")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body), 3)
        self.assertEqual(
            [c["channel_id"] for c in body],
            [1, 2, 3],
        )
        for channel in body:
            self.assertEqual(channel["live_video"], False)
            self.assertTrue(channel["recording_query_ok"])
            self.assertEqual(channel["recording_recent"], False)
            self.assertIsNone(channel["last_recording"])
            self.assertEqual(channel["recording_files_24h"], 42)
            self.assertEqual(channel["recording_coverage_24h"], 97.5)
            self.assertIsNotNone(channel["live_checked_at"])
            self.assertNotIn("daily_online_rate", channel)
            self.assertNotIn("nvr_live_video_disconnect_count_24h", channel)

    def test_get_channel_not_found(self) -> None:
        resp = self.client.get("/api/v1/channels/99")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error_code": "channel_not_found"})

    def test_get_channel_zero_is_not_found(self) -> None:
        resp = self.client.get("/api/v1/channels/0")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error_code": "channel_not_found"})

    def test_get_channel_negative_is_not_found(self) -> None:
        resp = self.client.get("/api/v1/channels/-1")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error_code": "channel_not_found"})

    def test_get_channel_valid(self) -> None:
        resp = self.client.get("/api/v1/channels/1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["channel_id"], 1)

    def test_snapshot_unknown_channel_404(self) -> None:
        resp = self.client.get("/api/v1/channels/99/snapshot")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error_code": "channel_not_found"})

    def test_snapshot_zero_channel_404(self) -> None:
        resp = self.client.get("/api/v1/channels/0/snapshot")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error_code": "channel_not_found"})

    def test_snapshot_negative_channel_404(self) -> None:
        resp = self.client.get("/api/v1/channels/-1/snapshot")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error_code": "channel_not_found"})

    def test_snapshot_success_returns_jpeg(self) -> None:
        fake_jpeg = b"\xff\xd8\xff\xe0fakejpeg"

        async def fake_grab(rtsp_url, timeout_ms, jpeg_qv):
            return True, 42, fake_jpeg, "decoded 1 frame"

        with patch.object(main, "_ffmpeg_grab_jpeg", side_effect=fake_grab):
            resp = self.client.get("/api/v1/channels/1/snapshot")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["content-type"], "image/jpeg")
        self.assertEqual(resp.content, fake_jpeg)

    def test_snapshot_failure_returns_503(self) -> None:
        async def fake_grab(rtsp_url, timeout_ms, jpeg_qv):
            return False, 5, None, "timeout"

        with patch.object(main, "_ffmpeg_grab_jpeg", side_effect=fake_grab):
            resp = self.client.get("/api/v1/channels/2/snapshot")

        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json(), {"error_code": "snapshot_unavailable"})

    def test_snapshot_returns_503_when_busy(self) -> None:
        # Fully-held semaphore: any acquire attempt times out immediately.
        main._sem = asyncio.Semaphore(0)
        with patch.object(main, "QUEUE_TIMEOUT_MS", 20):
            resp = self.client.get("/api/v1/channels/1/snapshot")

        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json(), {"error_code": "snapshot_unavailable"})

    def test_legacy_endpoint_json_only_when_capture_fails(self) -> None:
        async def fake_grab(rtsp_url, timeout_ms, jpeg_qv):
            return False, 5, None, "timeout"

        with patch.object(main, "_ffmpeg_grab_jpeg", side_effect=fake_grab):
            resp = self.client.get("/api/camera/1")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(
            set(body.keys()), {"camera_id", "ok", "latency_ms", "detail"}
        )
        self.assertEqual(body["camera_id"], "1")
        self.assertFalse(body["ok"])

    def test_legacy_endpoint_multipart_when_capture_succeeds(self) -> None:
        fake_jpeg = b"\xff\xd8\xff\xe0fakejpeg"

        async def fake_grab(rtsp_url, timeout_ms, jpeg_qv):
            return True, 42, fake_jpeg, "decoded 1 frame"

        with patch.object(main, "_ffmpeg_grab_jpeg", side_effect=fake_grab):
            resp = self.client.get("/api/camera/1")

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.headers["content-type"].startswith("multipart/mixed"))
        self.assertIn(b"Content-Type: application/json", resp.content)
        self.assertIn(b"Content-Type: image/jpeg", resp.content)
        self.assertIn(fake_jpeg, resp.content)

    def test_legacy_endpoint_returns_503_when_busy(self) -> None:
        # Fully-held semaphore: any acquire attempt times out immediately,
        # matching the original add-on's busy behaviour (503, not 200).
        main._sem = asyncio.Semaphore(0)
        with patch.object(main, "QUEUE_TIMEOUT_MS", 20):
            resp = self.client.get("/api/camera/1")

        self.assertEqual(resp.status_code, 503)
        self.assertEqual(
            resp.json(),
            {"camera_id": "1", "ok": False, "latency_ms": 0, "detail": "busy"},
        )
        # Busy responses must not be cached (matches original behaviour).
        self.assertNotIn("1", main._cache)


if __name__ == "__main__":
    unittest.main()
