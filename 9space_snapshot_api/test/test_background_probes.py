"""Fake-based tests for the M2B background NVR probes: live-video probe,
Dahua recording query, and the shared in-memory ChannelStateStore.

These tests never require a real NVR, Docker, or HAOS. They monkeypatch
``live_probe.probe_channel`` and ``recording_query.query_channel`` (the
only two functions that would otherwise touch a real network) with fakes,
matching the pattern already used in test_channels_api.py.

Run locally with:
    pip install fastapi httpx
    python -m unittest discover -s 9space_snapshot_api/test -v
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ADDON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_DIR))

import background  # noqa: E402
import live_probe  # noqa: E402
import main  # noqa: E402
import recording_query  # noqa: E402
from channel_state import ChannelStateStore  # noqa: E402
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


class ChannelStoreTests(unittest.TestCase):
    """API handlers only ever read this store; test its merge/priority
    logic directly, independent of FastAPI/background loops."""

    def test_default_state_is_all_unknown(self) -> None:
        store = ChannelStateStore()
        state = store.snapshot(1)
        self.assertIsNone(state["live_video"])
        self.assertFalse(state["recording_query_ok"])
        self.assertIsNone(state["recording_recent"])
        self.assertIsNone(state["last_recording"])
        self.assertIsNone(state["checked_at"])
        self.assertIsNone(state["error_code"])

    def test_update_live_then_snapshot(self) -> None:
        store = ChannelStateStore()

        async def run() -> None:
            await store.update_live(
                1, live_video=True, checked_at_ms=1_000, error_code=None
            )

        asyncio.run(run())
        state = store.snapshot(1)
        self.assertTrue(state["live_video"])
        self.assertIsNotNone(state["checked_at"])

    def test_checked_at_is_newer_of_live_and_recording(self) -> None:
        store = ChannelStateStore()

        async def run() -> None:
            await store.update_live(
                1, live_video=True, checked_at_ms=1_000, error_code=None
            )
            await store.update_recording(
                1,
                recording_query_ok=True,
                recording_recent=True,
                last_recording="2026-08-01T00:00:00+00:00",
                checked_at_ms=5_000,
                error_code=None,
            )

        asyncio.run(run())
        state = store.snapshot(1)
        # 5000ms is later than 1000ms; checked_at must reflect the newer one.
        from datetime import datetime, timezone

        expected = datetime.fromtimestamp(5.0, tz=timezone.utc).isoformat()
        self.assertEqual(state["checked_at"], expected)

    def test_higher_priority_error_code_wins_regardless_of_source(self) -> None:
        # Fixed priority list (see channel_state._ERROR_PRIORITY):
        # nvr_unreachable outranks rtsp_timeout, even though it comes from
        # the less-frequently-run recording query and has a newer timestamp
        # here than the live probe's rtsp_timeout.
        store = ChannelStateStore()

        async def run() -> None:
            await store.update_live(
                1, live_video=False, checked_at_ms=1_000, error_code="rtsp_timeout"
            )
            await store.update_recording(
                1,
                recording_query_ok=False,
                recording_recent=None,
                last_recording=None,
                checked_at_ms=2_000,
                error_code="nvr_unreachable",
            )

        asyncio.run(run())
        state = store.snapshot(1)
        self.assertEqual(state["error_code"], "nvr_unreachable")

    def test_authentication_failed_wins_over_no_video_regardless_of_timestamp(
        self,
    ) -> None:
        # Recording auth failure + live no_video at the same time: result
        # must always be authentication_failed (highest priority), even if
        # no_video's timestamp is newer.
        store = ChannelStateStore()

        async def run() -> None:
            await store.update_live(
                1, live_video=False, checked_at_ms=9_000, error_code="no_video"
            )
            await store.update_recording(
                1,
                recording_query_ok=False,
                recording_recent=None,
                last_recording=None,
                checked_at_ms=1_000,
                error_code="authentication_failed",
            )

        asyncio.run(run())
        state = store.snapshot(1)
        self.assertEqual(state["error_code"], "authentication_failed")

    def test_same_priority_ties_break_on_newer_timestamp(self) -> None:
        store = ChannelStateStore()

        async def run() -> None:
            await store.update_live(
                1, live_video=False, checked_at_ms=1_000, error_code="internal_error"
            )
            await store.update_recording(
                1,
                recording_query_ok=False,
                recording_recent=None,
                last_recording=None,
                checked_at_ms=5_000,
                error_code="internal_error",
            )

        asyncio.run(run())
        state = store.snapshot(1)
        self.assertEqual(state["error_code"], "internal_error")

    def test_recording_internal_error_clears_previous_last_recording(self) -> None:
        store = ChannelStateStore()

        async def run() -> None:
            await store.update_recording(
                1,
                recording_query_ok=True,
                recording_recent=True,
                last_recording="2026-08-01T12:00:00+08:00",
                checked_at_ms=1_000,
                error_code=None,
            )
            await store.mark_recording_internal_error(1, checked_at_ms=2_000)

        asyncio.run(run())
        state = store.snapshot(1)
        self.assertFalse(state["recording_query_ok"])
        self.assertIsNone(state["recording_recent"])
        self.assertIsNone(state["last_recording"])
        self.assertEqual(state["error_code"], "internal_error")


class LiveProbeUnitTests(unittest.TestCase):
    """live_probe.probe_channel() itself, without FastAPI, using a fake TCP
    listener so no real NVR is required."""

    def test_probe_channel_unreachable_host_returns_nvr_unreachable(self) -> None:
        nvr = live_probe.NvrConfig(host="127.0.0.1", port=1, username="a", password="b")
        result = live_probe.probe_channel(1, nvr)
        self.assertFalse(result["live_video"])
        self.assertIn(result["error_code"], {"nvr_unreachable", "rtsp_timeout"})


class RecordingQueryUnitTests(unittest.TestCase):
    """recording_query.query_channel() itself, without FastAPI."""

    def test_query_channel_unreachable_host_returns_nvr_unreachable(self) -> None:
        nvr = recording_query.NvrHttpConfig(
            host="127.0.0.1", http_port=1, username="a", password="b"
        )
        result = recording_query.query_channel(1, nvr)
        self.assertFalse(result["recording_query_ok"])
        self.assertEqual(result["error_code"], "nvr_unreachable")


def _fake_live_probe_success(channel_id, nvr):
    return {"live_video": True, "error_code": None}


def _fake_live_probe_timeout(channel_id, nvr):
    return {"live_video": False, "error_code": "rtsp_timeout"}


def _fake_live_probe_one_channel_fails(channel_id, nvr):
    if channel_id == 2:
        raise RuntimeError("boom")
    return {"live_video": True, "error_code": None}


def _fake_recording_success(channel_id, nvr):
    return {
        "recording_query_ok": True,
        "recording_recent": True,
        "last_recording": "2026-08-01T12:00:00+00:00",
        "error_code": None,
    }


def _fake_recording_failure(channel_id, nvr):
    return {
        "recording_query_ok": False,
        "recording_recent": None,
        "last_recording": None,
        "error_code": "recording_query_failed",
    }


class UnexpectedExceptionStateTests(unittest.TestCase):
    """M2B review fix 4: an unexpected (non-classified) exception from a
    background worker must positively overwrite the channel state instead
    of just logging and leaving a stale/unknown value in place."""

    def test_live_worker_first_time_unexpected_exception_sets_error_state(self) -> None:
        store = ChannelStateStore()
        ready = threading.Event()
        opts = dict(FAKE_OPTS, channel_count=1)

        def _raise(channel_id, nvr):
            raise RuntimeError("boom")

        async def run():
            sem = asyncio.Semaphore(1)
            with patch.object(live_probe, "probe_channel", side_effect=_raise):
                task = asyncio.create_task(
                    background.live_probe_loop(store, lambda: opts, sem, ready_event=ready)
                )
                while not ready.is_set():
                    await asyncio.sleep(0.005)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(run())
        state = store.snapshot(1)
        self.assertFalse(state["live_video"])
        self.assertEqual(state["error_code"], "internal_error")
        self.assertIsNotNone(state["checked_at"])

    def test_recording_worker_first_time_unexpected_exception_sets_error_state(self) -> None:
        store = ChannelStateStore()
        ready = threading.Event()
        opts = dict(FAKE_OPTS, channel_count=1)

        def _raise(channel_id, nvr):
            raise RuntimeError("boom")

        async def run():
            sem = asyncio.Semaphore(1)
            with patch.object(recording_query, "query_channel", side_effect=_raise):
                task = asyncio.create_task(
                    background.recording_query_loop(store, lambda: opts, sem, ready_event=ready)
                )
                while not ready.is_set():
                    await asyncio.sleep(0.005)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(run())
        state = store.snapshot(1)
        self.assertFalse(state["recording_query_ok"])
        self.assertIsNone(state["recording_recent"])
        self.assertEqual(state["error_code"], "internal_error")
        self.assertIsNotNone(state["checked_at"])

    def test_live_previous_success_then_unexpected_failure_replaces_state(self) -> None:
        store = ChannelStateStore()
        ready = threading.Event()
        opts = dict(FAKE_OPTS, channel_count=1)
        calls = {"n": 0}

        def _flaky(channel_id, nvr):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"live_video": True, "error_code": None}
            raise RuntimeError("boom")

        async def run():
            sem = asyncio.Semaphore(1)
            with patch.object(live_probe, "probe_channel", side_effect=_flaky):
                task = asyncio.create_task(
                    background.live_probe_loop(
                        store, lambda: opts, sem, interval_seconds=0.01, ready_event=ready
                    )
                )
                while not ready.is_set():
                    await asyncio.sleep(0.005)
                # First round must have reported success before the failure.
                first_state = store.snapshot(1)
                assert first_state["live_video"] is True
                deadline = time.monotonic() + 3
                while calls["n"] < 2 and time.monotonic() < deadline:
                    await asyncio.sleep(0.01)
                await asyncio.sleep(0.05)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            return store.snapshot(1)

        final_state = asyncio.run(run())
        self.assertGreaterEqual(calls["n"], 2)
        # The stale success from round 1 must not survive round 2's crash.
        self.assertFalse(final_state["live_video"])
        self.assertEqual(final_state["error_code"], "internal_error")


class SharedBackgroundConcurrencyTests(unittest.TestCase):
    """M2B review fix 3: live-video probing and recording queries must
    share one background concurrency slot (max 1 concurrent NVR op)."""

    def test_live_and_recording_loops_never_run_concurrently(self) -> None:
        store = ChannelStateStore()
        live_ready = threading.Event()
        recording_ready = threading.Event()
        opts = dict(FAKE_OPTS, channel_count=1)

        state_lock = threading.Lock()
        active = 0
        max_active = 0

        def _blocking(channel_id, nvr, result):
            nonlocal active, max_active
            with state_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with state_lock:
                active -= 1
            return result

        def _fake_live(channel_id, nvr):
            return _blocking(
                channel_id, nvr, {"live_video": True, "error_code": None}
            )

        def _fake_recording(channel_id, nvr):
            return _blocking(
                channel_id,
                nvr,
                {
                    "recording_query_ok": True,
                    "recording_recent": True,
                    "last_recording": None,
                    "error_code": None,
                },
            )

        async def run():
            sem = asyncio.Semaphore(background.BACKGROUND_CONCURRENCY)
            with patch.object(live_probe, "probe_channel", side_effect=_fake_live), patch.object(
                recording_query, "query_channel", side_effect=_fake_recording
            ):
                live_task = asyncio.create_task(
                    background.live_probe_loop(
                        store, lambda: opts, sem, ready_event=live_ready
                    )
                )
                recording_task = asyncio.create_task(
                    background.recording_query_loop(
                        store, lambda: opts, sem, ready_event=recording_ready
                    )
                )
                while not (live_ready.is_set() and recording_ready.is_set()):
                    await asyncio.sleep(0.005)
                for task in (live_task, recording_task):
                    task.cancel()
                for task in (live_task, recording_task):
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

        asyncio.run(run())
        self.assertEqual(max_active, 1)


class BackgroundProbeApiTests(unittest.TestCase):
    """End-to-end (fake-based) tests through the FastAPI app, covering the
    M2B requirements list: startup first round, API reads cache only,
    per-channel isolation, one-based channel mapping, redaction, and
    graceful background task shutdown."""

    def _start_client(self, live_fake, recording_fake) -> TestClient:
        self._opts_patch = patch.object(main, "_load_options", return_value=dict(FAKE_OPTS))
        self._opts_patch.start()
        self._live_patch = patch.object(live_probe, "probe_channel", side_effect=live_fake)
        self._live_patch.start()
        self._recording_patch = patch.object(
            recording_query, "query_channel", side_effect=recording_fake
        )
        self._recording_patch.start()
        main._cache.clear()
        main._sem = None
        main._channel_store.clear()
        self._client_cm = TestClient(main.app)
        client = self._client_cm.__enter__()
        self._exited = False
        self.assertTrue(main._live_first_round_ready.wait(timeout=5))
        self.assertTrue(main._recording_first_round_ready.wait(timeout=5))
        return client

    def tearDown(self) -> None:
        if not self._exited:
            self._client_cm.__exit__(None, None, None)
            self._exited = True
        self._recording_patch.stop()
        self._live_patch.stop()
        self._opts_patch.stop()

    # 1 & 3: startup's first round updates live-video state; API handler
    # only reads that cache (it is not itself calling the NVR).
    def test_startup_first_round_updates_live_video_state(self) -> None:
        client = self._start_client(_fake_live_probe_success, _fake_recording_success)
        resp = client.get("/api/v1/channels/1")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["live_video"])

    # 2: startup's first round updates recording state.
    def test_startup_first_round_updates_recording_state(self) -> None:
        client = self._start_client(_fake_live_probe_success, _fake_recording_success)
        resp = client.get("/api/v1/channels/1")
        body = resp.json()
        self.assertTrue(body["recording_query_ok"])
        self.assertTrue(body["recording_recent"])
        self.assertEqual(body["last_recording"], "2026-08-01T12:00:00+00:00")

    # 4: live probe success.
    def test_live_probe_success_reports_true(self) -> None:
        client = self._start_client(_fake_live_probe_success, _fake_recording_success)
        body = client.get("/api/v1/channels/2").json()
        self.assertTrue(body["live_video"])
        self.assertIsNone(body["error_code"])

    # 5: live probe timeout / no-video.
    def test_live_probe_timeout_reports_false_with_error_code(self) -> None:
        client = self._start_client(_fake_live_probe_timeout, _fake_recording_success)
        body = client.get("/api/v1/channels/2").json()
        self.assertFalse(body["live_video"])
        self.assertEqual(body["error_code"], "rtsp_timeout")

    # 6: recording query success.
    def test_recording_query_success(self) -> None:
        client = self._start_client(_fake_live_probe_success, _fake_recording_success)
        body = client.get("/api/v1/channels/1").json()
        self.assertTrue(body["recording_query_ok"])

    # 7: recording query failure.
    def test_recording_query_failure(self) -> None:
        client = self._start_client(_fake_live_probe_success, _fake_recording_failure)
        body = client.get("/api/v1/channels/1").json()
        self.assertFalse(body["recording_query_ok"])
        self.assertEqual(body["error_code"], "recording_query_failed")

    # 8: channel mapping stays one-based (channel 1 in the API/store must
    # correspond to NVR channel 1 passed straight through, no remap).
    def test_channel_mapping_is_one_based(self) -> None:
        seen_channel_ids: list[int] = []

        def _fake_live(channel_id, nvr):
            seen_channel_ids.append(channel_id)
            return {"live_video": True, "error_code": None}

        client = self._start_client(_fake_live, _fake_recording_success)
        client.get("/api/v1/channels/1")
        self.assertEqual(sorted(seen_channel_ids), [1, 2, 3])
        self.assertNotIn(0, seen_channel_ids)

    # 9: one channel's failure does not affect the others in the same round.
    def test_one_channel_failure_does_not_affect_others(self) -> None:
        client = self._start_client(
            _fake_live_probe_one_channel_fails, _fake_recording_success
        )
        ok_channel = client.get("/api/v1/channels/1").json()
        failed_channel = client.get("/api/v1/channels/2").json()
        self.assertTrue(ok_channel["live_video"])
        # Channel 2's worker raised; background.py must not just log and
        # leave the previous/unknown state in place -- it must positively
        # mark the channel as not live with a safe internal_error code, so
        # a stale/unknown state can never be misread as "still fine".
        self.assertFalse(failed_channel["live_video"])
        self.assertEqual(failed_channel["error_code"], "internal_error")
        self.assertIsNotNone(failed_channel["checked_at"])


    # 10: credentials, full RTSP URL and CGI body never appear in the API
    # response, for any channel, in any of the fake scenarios above.
    def test_no_credentials_or_urls_in_api_response(self) -> None:
        client = self._start_client(_fake_live_probe_success, _fake_recording_success)
        resp = client.get("/api/v1/channels")
        raw = resp.text
        self.assertNotIn(FAKE_OPTS["password"], raw)
        self.assertNotIn("rtsp://", raw)
        self.assertNotIn("mediaFileFind.cgi", raw)

    # 12: /healthz stays HTTP 200 even when the NVR is completely
    # unreachable in the background loops.
    def test_healthz_ok_when_nvr_unreachable(self) -> None:
        client = self._start_client(_fake_live_probe_timeout, _fake_recording_failure)
        resp = client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    # 11: background task shutdown cancels cleanly (no hang, no exception
    # bubbling out of TestClient's __exit__).
    def test_background_tasks_are_cancelled_on_shutdown(self) -> None:
        client = self._start_client(_fake_live_probe_success, _fake_recording_success)
        live_task = main._live_task
        recording_task = main._recording_task
        self.assertIsNotNone(live_task)
        self.assertIsNotNone(recording_task)
        self._client_cm.__exit__(None, None, None)
        self._exited = True
        self.assertTrue(live_task.cancelled() or live_task.done())
        self.assertTrue(recording_task.cancelled() or recording_task.done())


if __name__ == "__main__":
    unittest.main()
