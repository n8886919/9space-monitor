"""Fake-based regression tests for the M2B code-review fixes:

1. Legacy ffmpeg `detail` redaction (no raw stderr / credentials / RTSP URL).
2. RTP "wait a bit longer after the first packet" deadline extension.
3. RTSP header/body size limits + overall per-exchange deadline.
4. Dahua CGI response size limit + `found` count validation.
5. `mediaFileFind.cgi` destroy-failure logging (redacted, best-effort).
6. Shutdown stops scheduling further channels/rounds while a worker is
   blocked in a background thread.

None of these require a real NVR, Docker or HAOS.

Run locally with:
    pip install fastapi httpx
    python -m unittest discover -s 9space_snapshot_api/test -v
"""

from __future__ import annotations

import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from urllib.error import URLError
from unittest.mock import patch

ADDON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_DIR))

import live_probe  # noqa: E402
import main  # noqa: E402
import recording_query  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


FAKE_OPTS = {
    "nvr_host": "127.0.0.1",
    "rtsp_port": 554,
    "username": "admin",
    "password": "hunter2",
    "subtype": 0,
    "health_timeout_ms": 100,
    "jpeg_qv": 32,
    "max_concurrency": 2,
    "nvr_http_port": 80,
    "channel_count": 3,
    "snapshot_cache_ms": 0,
}


def _fake_live_probe_channel(channel_id, nvr):
    return {"live_video": False, "error_code": "no_video"}


def _fake_recording_query_channel(channel_id, nvr):
    return {
        "recording_query_ok": True,
        "recording_recent": False,
        "last_recording": None,
        "error_code": None,
    }


# ---------------------------------------------------------------------------
# Fix 1: legacy ffmpeg detail redaction.
# ---------------------------------------------------------------------------


class LegacyFfmpegRedactionTests(unittest.TestCase):
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
        self.client = self._client_cm.__enter__()
        self.assertTrue(main._live_first_round_ready.wait(timeout=5))
        self.assertTrue(main._recording_first_round_ready.wait(timeout=5))

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)
        self._recording_query_patch.stop()
        self._live_probe_patch.stop()
        self._opts_patch.stop()

    def test_legacy_endpoint_never_leaks_credentials_or_rtsp_url(self) -> None:
        secret_url = "rtsp://fake_user:fake_password@127.0.0.1/cam/realmonitor?channel=1&subtype=0"
        fake_stderr = (
            f"[rtsp @ 0x1] 401 Unauthorized\nfailed to open input: {secret_url}\n"
        ).encode("utf-8")

        class FakeProc:
            returncode = 1

            async def communicate(self_inner):
                return b"", fake_stderr

            def kill(self_inner):
                pass

        async def fake_create_subprocess_exec(*cmd, **kwargs):
            return FakeProc()

        with self.assertLogs("main", level="DEBUG") as log_capture:
            with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
                resp = self.client.get("/api/camera/1")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(set(body.keys()), {"camera_id", "ok", "latency_ms", "detail"})
        self.assertFalse(body["ok"])
        self.assertIn(
            body["detail"],
            {"authentication_failed", "connection_failed", "capture_failed", "timeout", "exception"},
        )
        self.assertNotIn("fake_user", body["detail"])
        self.assertNotIn("fake_password", body["detail"])
        self.assertNotIn("rtsp://", body["detail"])

        combined_logs = "\n".join(log_capture.output)
        self.assertNotIn("fake_user", combined_logs)
        self.assertNotIn("fake_password", combined_logs)
        self.assertNotIn("rtsp://", combined_logs)


# ---------------------------------------------------------------------------
# Fix 2: RTP first-packet deadline extension.
# ---------------------------------------------------------------------------


def _rtp_frame(video_channel: int, timestamp: int) -> bytes:
    payload = bytes([0x80, 0x60]) + (0).to_bytes(2, "big") + timestamp.to_bytes(4, "big") + (1).to_bytes(4, "big")
    return bytes([0x24, video_channel]) + len(payload).to_bytes(2, "big") + payload


class _ScriptedClockSocket:
    """Fake socket whose ``recv`` is driven by a shared, monotonically
    increasing fake clock (patched over ``time.perf_counter``), so the test
    never actually sleeps for multiple seconds."""

    def __init__(self, clock: list, frame1: bytes, frame1_at: float, frame2: bytes, frame2_at: float):
        self._clock = clock
        self._frame1 = frame1
        self._frame1_at = frame1_at
        self._frame2 = frame2
        self._frame2_at = frame2_at
        self._frame1_sent = False
        self._frame2_sent = False

    def settimeout(self, _value: float) -> None:
        pass

    def recv(self, _n: int) -> bytes:
        now = self._clock[0]
        if not self._frame1_sent and now >= self._frame1_at:
            self._frame1_sent = True
            return self._frame1
        if self._frame1_sent and not self._frame2_sent and now >= self._frame2_at:
            self._frame2_sent = True
            return self._frame2
        raise socket.timeout()


class RtpDeadlineExtensionTests(unittest.TestCase):
    """Regression test for the M2B review fix restoring the integration's
    original "wait a bit longer after the first packet" RTP deadline."""

    def test_second_packet_after_first_but_before_extended_deadline_is_live(self) -> None:
        clock = [0.0]

        def _fake_perf_counter() -> float:
            clock[0] += 0.05
            return clock[0]

        video_channel = 0
        frame1 = _rtp_frame(video_channel, timestamp=1000)
        frame2 = _rtp_frame(video_channel, timestamp=2000)
        # First packet arrives close to the original RTP_FIRST_PACKET_TIMEOUT
        # (3.0s); second packet arrives ~2s after that -- inside the
        # extended deadline (first_packet_time + RTP_AFTER_FIRST_PACKET_SECONDS)
        # but well after the original 3.0s deadline would have expired.
        fake_sock = _ScriptedClockSocket(
            clock, frame1, frame1_at=2.9, frame2=frame2, frame2_at=4.5
        )

        with patch.object(live_probe.time, "perf_counter", side_effect=_fake_perf_counter):
            result = live_probe._observe_rtp(fake_sock, b"", video_channel)

        self.assertTrue(result["live_video"])

    def test_without_second_packet_stays_not_live(self) -> None:
        # Sanity check: only one packet ever arrives -> not live, regardless
        # of the deadline extension.
        clock = [0.0]

        def _fake_perf_counter() -> float:
            clock[0] += 0.05
            return clock[0]

        video_channel = 0
        frame1 = _rtp_frame(video_channel, timestamp=1000)
        fake_sock = _ScriptedClockSocket(
            clock, frame1, frame1_at=1.0, frame2=b"", frame2_at=999.0
        )

        with patch.object(live_probe.time, "perf_counter", side_effect=_fake_perf_counter):
            result = live_probe._observe_rtp(fake_sock, b"", video_channel)

        self.assertFalse(result["live_video"])


# ---------------------------------------------------------------------------
# Fix 5: RTSP header/body limits + overall deadline.
# ---------------------------------------------------------------------------


class _StaticSocket:
    def __init__(self, data: bytes):
        self._data = data

    def recv(self, n: int) -> bytes:
        chunk = self._data[:n]
        self._data = self._data[n:]
        return chunk


class RtspResponseLimitTests(unittest.TestCase):
    def test_oversized_content_length_raises_without_crashing(self) -> None:
        oversized = live_probe.MAX_RTSP_BODY_BYTES + 1
        header = f"RTSP/1.0 200 OK\r\nCSeq: 1\r\nContent-Length: {oversized}\r\n\r\n".encode()
        sock = _StaticSocket(header)
        with self.assertRaises(ValueError):
            live_probe._read_rtsp_response(sock)

    def test_negative_content_length_raises_without_crashing(self) -> None:
        header = b"RTSP/1.0 200 OK\r\nCSeq: 1\r\nContent-Length: -5\r\n\r\n"
        sock = _StaticSocket(header)
        with self.assertRaises(ValueError):
            live_probe._read_rtsp_response(sock)

    def test_slow_trickle_past_overall_deadline_times_out(self) -> None:
        # A peer that only ever sends 1 byte at a time can never complete
        # the header within an overall per-exchange deadline, even though
        # each individual recv() "succeeds" instantly.
        class _TrickleSocket:
            def recv(self_inner, _n: int) -> bytes:
                return b"X"

        with self.assertRaises(TimeoutError):
            live_probe._read_rtsp_response(_TrickleSocket(), deadline=time.monotonic() - 0.001)


# ---------------------------------------------------------------------------
# Fix 5: Dahua CGI response size limit + found-count validation.
# ---------------------------------------------------------------------------


class _FakeCgiResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = len(self._data)
        chunk = self._data[:n]
        self._data = self._data[n:]
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class CgiResponseLimitTests(unittest.TestCase):
    def _client(self) -> recording_query.DahuaRecordingClient:
        nvr = recording_query.NvrHttpConfig(host="127.0.0.1", http_port=1, username="a", password="b")
        return recording_query.DahuaRecordingClient(nvr)

    def test_oversized_cgi_response_raises_without_crashing(self) -> None:
        client = self._client()
        oversized = b"x" * (recording_query.MAX_CGI_RESPONSE_BYTES + 10)
        with patch.object(client.opener, "open", return_value=_FakeCgiResponse(oversized)):
            with self.assertRaises(ValueError):
                client._get("/cgi-bin/mediaFileFind.cgi", [("action", "factory.create")])

    def test_found_greater_than_requested_count_raises(self) -> None:
        with self.assertRaises(ValueError):
            recording_query._parse_items("found=500\n", 100)

    def test_negative_found_raises(self) -> None:
        with self.assertRaises(ValueError):
            recording_query._parse_items("found=-5\n", 100)

    def test_found_within_bounds_parses_normally(self) -> None:
        items = recording_query._parse_items(
            "found=1\nitems[0].StartTime=2026-08-01 00:00:00\n", 100
        )
        self.assertEqual(len(items), 1)


# ---------------------------------------------------------------------------
# Fix 6: destroy failure -- redacted logging, main result unaffected.
# ---------------------------------------------------------------------------


def _make_get_with_destroy_failure(*, raise_network_error: bool):
    def _get(path, params):
        action = dict(params).get("action")
        if action == "factory.create":
            return "result=abc123secretobjectid"
        if action == "findFile":
            return "OK"
        if action == "findNextFile":
            return "found=0\n"
        if action == "destroy":
            if raise_network_error:
                raise URLError("boom")
            return "ERROR unexpected body"
        raise AssertionError(f"unexpected action {action}")

    return _get


class DestroyFailureTests(unittest.TestCase):
    def _client(self) -> recording_query.DahuaRecordingClient:
        nvr = recording_query.NvrHttpConfig(host="127.0.0.1", http_port=1, username="a", password="b")
        return recording_query.DahuaRecordingClient(nvr)

    def test_destroy_network_failure_logs_redacted_warning_and_keeps_main_result(self) -> None:
        client = self._client()
        with patch.object(client, "_get", side_effect=_make_get_with_destroy_failure(raise_network_error=True)):
            with self.assertLogs("recording_query", level="WARNING") as log_capture:
                result = client.query_channel(1)

        self.assertTrue(result["recording_query_ok"])
        combined = "\n".join(log_capture.output)
        self.assertNotIn("abc123secretobjectid", combined)
        self.assertNotIn("mediaFileFind.cgi", combined)
        self.assertNotIn("boom", combined)

    def test_destroy_failure_body_logs_redacted_warning_and_keeps_main_result(self) -> None:
        client = self._client()
        with patch.object(client, "_get", side_effect=_make_get_with_destroy_failure(raise_network_error=False)):
            with self.assertLogs("recording_query", level="WARNING") as log_capture:
                result = client.query_channel(1)

        self.assertTrue(result["recording_query_ok"])
        combined = "\n".join(log_capture.output)
        self.assertNotIn("abc123secretobjectid", combined)
        self.assertNotIn("ERROR unexpected body", combined)


# ---------------------------------------------------------------------------
# Shutdown boundary: stop scheduling once a worker is blocked.
# ---------------------------------------------------------------------------


class ShutdownBoundaryTests(unittest.TestCase):
    def test_shutdown_stops_scheduling_next_channel_while_worker_blocked(self) -> None:
        main._cache.clear()
        main._sem = None
        main._channel_store.clear()
        opts = dict(FAKE_OPTS, channel_count=2, max_concurrency=2)

        channel1_started = threading.Event()
        channel2_called = threading.Event()

        def _fake_live(channel_id, nvr):
            if channel_id == 1:
                channel1_started.set()
                time.sleep(0.3)
                return {"live_video": True, "error_code": None}
            channel2_called.set()
            return {"live_video": True, "error_code": None}

        def _fake_recording(channel_id, nvr):
            return {
                "recording_query_ok": True,
                "recording_recent": True,
                "last_recording": None,
                "error_code": None,
            }

        with patch.object(main, "_load_options", return_value=opts), patch.object(
            live_probe, "probe_channel", side_effect=_fake_live
        ), patch.object(recording_query, "query_channel", side_effect=_fake_recording):
            with TestClient(main.app):
                self.assertTrue(channel1_started.wait(timeout=2))
                # Shutdown fires on context-manager exit below, while
                # channel 1's fake is still "blocked" in its thread.

        # Channel 2 must never have been scheduled: with the shared
        # background semaphore (fix 3) and shutdown cancelling the loop
        # (not waiting for the blocked thread), no further channel should
        # be picked up after shutdown starts.
        self.assertFalse(channel2_called.is_set())


if __name__ == "__main__":
    unittest.main()
