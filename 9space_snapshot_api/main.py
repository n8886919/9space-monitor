import asyncio
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from fastapi import FastAPI, Path, Response
from fastapi.responses import JSONResponse

import background
from channel_state import ChannelStateStore

_LOGGER = logging.getLogger(__name__)

app = FastAPI(title="Dahua RTSP Snapshot API")

OPTIONS_PATH = "/data/options.json"

# --- hard-coded queue timeout (ms): wait this long for a free ffmpeg slot, else 503 busy
QUEUE_TIMEOUT_MS = 300

_sem: Optional[asyncio.Semaphore] = None

# M2B review fix: live-video probing and recording queries share one
# background concurrency slot (background.BACKGROUND_CONCURRENCY), separate
# from the snapshot ffmpeg semaphore (_sem) above, which still uses the
# existing max_concurrency option.
_background_sem: Optional[asyncio.Semaphore] = None

# M2B: in-memory per-channel state written only by the background probe
# loops below; API handlers only ever read it (never trigger NVR I/O).
_channel_store = ChannelStateStore()
_live_task: Optional[asyncio.Task] = None
_recording_task: Optional[asyncio.Task] = None
# Set once each background loop's first round has completed. Tests can wait
# on these (from a different thread) instead of racing the background task.
_live_first_round_ready = threading.Event()
_recording_first_round_ready = threading.Event()


@dataclass
class CacheEntry:
    ts_ms: int
    ok: bool
    latency_ms: int
    detail: str
    jpeg: Optional[bytes]


_cache: Dict[str, CacheEntry] = {}
_cache_lock = asyncio.Lock()


def _load_options() -> dict:
    try:
        with open(OPTIONS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _opt(opts: dict, key: str, default):
    v = opts.get(key, default)
    return default if v is None else v


def _build_rtsp_url(opts: dict, camera_id: str) -> str:
    host = _opt(opts, "nvr_host", "127.0.0.1")
    port = int(_opt(opts, "rtsp_port", 554))
    user = _opt(opts, "username", "admin")
    pwd = _opt(opts, "password", "")
    subtype = int(_opt(opts, "subtype", 0))
    return f"rtsp://{user}:{pwd}@{host}:{port}/cam/realmonitor?channel={camera_id}&subtype={subtype}"


def _classify_ffmpeg_failure(stderr_text: str) -> str:
    """Map raw ffmpeg stderr to one of a small, fixed set of safe detail
    values. Never return the raw stderr text itself: ffmpeg's own error
    messages frequently echo back the full input URL, including the RTSP
    username and password (e.g. "Unauthorized: rtsp://user:pass@host/..."),
    which must never reach the legacy API response or any log line."""
    text = stderr_text.lower()
    if "401" in text or "unauthorized" in text or "authoriz" in text:
        return "authentication_failed"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if (
        "connection refused" in text
        or "no route to host" in text
        or "network is unreachable" in text
        or "could not connect" in text
        or "immediate exit requested" in text
        or "end of file" in text
        or "server returned 4" in text
        or "server returned 5" in text
    ):
        return "connection_failed"
    return "capture_failed"


async def _ffmpeg_grab_jpeg(
    rtsp_url: str, timeout_ms: int, jpeg_qv: int
) -> Tuple[bool, int, Optional[bytes], str]:
    timeout_sec = max(1, int((max(1, timeout_ms) + 999) / 1000))  # ceil(ms/1000)

    vf = "scale=-2:640"

    out_path = f"/tmp/snap_{os.getpid()}_{uuid.uuid4().hex}.jpg"

    cmd = [
        "timeout", f"{timeout_sec}s",
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-rtsp_transport", "tcp",
        "-i", rtsp_url,
        "-an", "-sn", "-dn",
        "-frames:v", "1",
        "-vf", vf,
        "-q:v", str(jpeg_qv),
        "-y", out_path,
    ]

    t0 = time.perf_counter()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec + 2.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            latency = int((time.perf_counter() - t0) * 1000)
            return False, latency, None, "timeout"

        latency = int((time.perf_counter() - t0) * 1000)

        if proc.returncode == 124:
            return False, latency, None, "timeout"

        if proc.returncode == 0:
            try:
                with open(out_path, "rb") as f:
                    jpeg = f.read()
                return True, latency, jpeg, "decoded 1 frame"
            except Exception:
                return False, latency, None, "capture_failed"
            finally:
                try:
                    os.remove(out_path)
                except Exception:
                    pass

        # stderr may contain the full RTSP URL (with credentials) -- it is
        # only ever used locally to pick a fixed, safe detail value below.
        # It must never be included in the response or logged verbatim.
        raw_err = (stderr or b"").decode("utf-8", errors="ignore")
        detail = _classify_ffmpeg_failure(raw_err)
        _LOGGER.debug("ffmpeg capture failed: %s", detail)
        # cleanup
        try:
            os.remove(out_path)
        except Exception:
            pass
        return False, latency, None, detail

    except Exception:
        latency = int((time.perf_counter() - t0) * 1000)
        try:
            os.remove(out_path)
        except Exception:
            pass
        return False, latency, None, "exception"


@app.on_event("startup")
async def _startup():
    global _sem, _background_sem, _live_task, _recording_task
    opts = _load_options()
    max_conc = int(_opt(opts, "max_concurrency", 2))
    _sem = asyncio.Semaphore(max(1, max_conc))
    # Live-video probing and recording queries share this single semaphore
    # (fixed background.BACKGROUND_CONCURRENCY, not the max_concurrency
    # option) so total background NVR operations never exceed 1 at a time.
    _background_sem = asyncio.Semaphore(background.BACKGROUND_CONCURRENCY)

    _live_first_round_ready.clear()
    _recording_first_round_ready.clear()
    _live_task = asyncio.create_task(
        background.live_probe_loop(
            _channel_store,
            _load_options,
            _background_sem,
            ready_event=_live_first_round_ready,
        )
    )
    _recording_task = asyncio.create_task(
        background.recording_query_loop(
            _channel_store,
            _load_options,
            _background_sem,
            ready_event=_recording_first_round_ready,
        )
    )


@app.on_event("shutdown")
async def _shutdown():
    """Stop scheduling new background work and cancel both loops.

    ``live_probe.probe_channel`` / ``recording_query.query_channel`` run
    inside ``asyncio.to_thread`` (plain blocking sockets/urllib). Cancelling
    the outer task stops the *coroutine* from awaiting further channels or
    scheduling another round, but it cannot forcibly kill the underlying
    OS thread if one is already blocked inside a socket/HTTP call -- Python
    has no API to do that safely. The recording query and RTSP control
    exchange therefore use complete monotonic operation deadlines to bound
    their normal network work. Those bounds do not make the thread
    force-cancellable, and lower-level DNS / OS socket behaviour may still
    delay the underlying thread beyond the coroutine's cancellation.
    """
    global _live_task, _recording_task
    for task in (_live_task, _recording_task):
        if task is not None:
            task.cancel()
    for task in (_live_task, _recording_task):
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
    _live_task = None
    _recording_task = None


# ---------------------------------------------------------------------------
# Legacy endpoint. Path, status codes, JSON fields, multipart and JPEG
# response must stay unchanged. See API.md "Legacy API".
# ---------------------------------------------------------------------------


class _Busy(Exception):
    """Raised when the ffmpeg concurrency slot could not be acquired within
    QUEUE_TIMEOUT_MS. Kept distinct from a normal (cacheable) CacheEntry so
    the legacy endpoint can still return its original 503 "busy" response
    instead of the generic 200 JSON used for other capture failures."""


async def _capture_snapshot(camera_id: str) -> CacheEntry:
    """Shared demand-driven capture + cache used by both the legacy endpoint
    and the new /api/v1 snapshot endpoint, so there is only one probe path.

    Raises _Busy (not cached) when the concurrency slot could not be
    acquired in time, matching the original add-on's busy behaviour."""
    opts = _load_options()
    timeout_ms = int(_opt(opts, "health_timeout_ms", 2500))
    jpeg_qv = int(_opt(opts, "jpeg_qv", 7))
    cache_ms = int(_opt(opts, "snapshot_cache_ms", 800))

    now_ms = int(time.time() * 1000)
    async with _cache_lock:
        ce = _cache.get(camera_id)
        if ce and (now_ms - ce.ts_ms) <= max(0, cache_ms):
            return ce

    rtsp_url = _build_rtsp_url(opts, camera_id)

    assert _sem is not None
    try:
        await asyncio.wait_for(_sem.acquire(), timeout=QUEUE_TIMEOUT_MS / 1000.0)
    except asyncio.TimeoutError:
        raise _Busy() from None

    try:
        ok, latency_ms, jpeg, detail = await _ffmpeg_grab_jpeg(rtsp_url, timeout_ms, jpeg_qv)
    finally:
        _sem.release()

    entry = CacheEntry(ts_ms=now_ms, ok=ok, latency_ms=latency_ms, detail=detail, jpeg=jpeg)
    async with _cache_lock:
        _cache[camera_id] = entry
    return entry


@app.get("/api/camera/{camera_id}")
async def camera_status_and_snapshot(
    camera_id: str = Path(..., description="Dahua channel number, e.g. 1"),
):
    try:
        entry = await _capture_snapshot(camera_id)
    except _Busy:
        status = {
            "camera_id": camera_id,
            "ok": False,
            "latency_ms": 0,
            "detail": "busy",
        }
        return JSONResponse(status_code=503, content=status)
    return _make_response(camera_id, entry.ok, entry.latency_ms, entry.detail, entry.jpeg)


def _make_response(
    camera_id: str, ok: bool, latency_ms: int, detail: str, jpeg: Optional[bytes]
):
    status = {
        "camera_id": camera_id,
        "ok": ok,
        "latency_ms": latency_ms,
        "detail": detail,
    }

    # If no image, return JSON only
    if not ok or not jpeg:
        return JSONResponse(status_code=200, content=status)

    # Multipart: JSON + JPEG
    boundary = "BOUNDARY"
    json_part = json.dumps(status, ensure_ascii=False).encode("utf-8")

    body = b""
    body += f"--{boundary}\r\n".encode()
    body += b"Content-Type: application/json; charset=utf-8\r\n\r\n"
    body += json_part + b"\r\n"
    body += f"--{boundary}\r\n".encode()
    body += b"Content-Type: image/jpeg\r\n"
    body += b"Content-Disposition: inline; filename=snapshot.jpg\r\n\r\n"
    body += jpeg + b"\r\n"
    body += f"--{boundary}--\r\n".encode()

    return Response(content=body, media_type=f"multipart/mixed; boundary={boundary}")


# ---------------------------------------------------------------------------
# /api/v1 skeleton (API.md "Minimal local API").
#
# M2A only reflected the snapshot cache (live_video/recording fields always
# null/false). M2B (background.py, live_probe.py, recording_query.py) adds
# the real NVR RTSP live-video probe and Dahua recording query as background
# loops; API handlers below only ever read the in-memory results via
# `_channel_store`, they never call the NVR themselves.
# The snapshot sub-endpoint still reuses the same demand-driven capture +
# cache as the legacy endpoint above; nothing NVR-related is duplicated.
# ---------------------------------------------------------------------------


def _channel_count(opts: dict) -> int:
    return int(_opt(opts, "channel_count", 14))


def _is_valid_channel(channel_id: int, opts: dict) -> bool:
    return 1 <= channel_id <= _channel_count(opts)


def _channel_status(channel_id: int) -> dict:
    """Build one channel's status from data already known to this process
    (in-memory only): the snapshot cache plus the latest background
    live-video probe / recording query results. No NVR call is made here."""
    cached = _cache.get(str(channel_id))
    state = _channel_store.snapshot(channel_id)
    return {
        "channel_id": channel_id,
        "live_video": state["live_video"],
        "snapshot_available": bool(cached and cached.ok and cached.jpeg),
        "recording_query_ok": state["recording_query_ok"],
        "recording_recent": state["recording_recent"],
        "last_recording": state["last_recording"],
        "checked_at": state["checked_at"],
        "error_code": state["error_code"],
    }


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/api/v1/channels")
async def list_channels() -> List[dict]:
    opts = _load_options()
    return [_channel_status(cid) for cid in range(1, _channel_count(opts) + 1)]


@app.get("/api/v1/channels/{channel_id}")
async def get_channel(channel_id: int = Path(...)):
    opts = _load_options()
    if not _is_valid_channel(channel_id, opts):
        return JSONResponse(status_code=404, content={"error_code": "channel_not_found"})
    return _channel_status(channel_id)


@app.get("/api/v1/channels/{channel_id}/snapshot")
async def channel_snapshot(channel_id: int = Path(...)):
    opts = _load_options()
    if not _is_valid_channel(channel_id, opts):
        return JSONResponse(status_code=404, content={"error_code": "channel_not_found"})

    try:
        entry = await _capture_snapshot(str(channel_id))
    except _Busy:
        return JSONResponse(status_code=503, content={"error_code": "snapshot_unavailable"})
    if entry.ok and entry.jpeg:
        return Response(content=entry.jpeg, media_type="image/jpeg")
    return JSONResponse(status_code=503, content={"error_code": "snapshot_unavailable"})
