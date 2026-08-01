import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from fastapi import FastAPI, Path, Response
from fastapi.responses import JSONResponse

app = FastAPI(title="Dahua RTSP Snapshot API")

OPTIONS_PATH = "/data/options.json"

# --- hard-coded queue timeout (ms): wait this long for a free ffmpeg slot, else 503 busy
QUEUE_TIMEOUT_MS = 300

_sem: Optional[asyncio.Semaphore] = None


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
            return False, latency, None, "timeout(wait_for)"

        latency = int((time.perf_counter() - t0) * 1000)

        if proc.returncode == 124:
            return False, latency, None, "timeout"

        if proc.returncode == 0:
            try:
                with open(out_path, "rb") as f:
                    jpeg = f.read()
                return True, latency, jpeg, "decoded 1 frame"
            except Exception:
                return False, latency, None, "read_tmp_failed"
            finally:
                try:
                    os.remove(out_path)
                except Exception:
                    pass

        err = (stderr or b"").decode("utf-8", errors="ignore").strip()
        if err:
            err = err.splitlines()[-1][:200]
        else:
            err = f"ffmpeg exit code {proc.returncode}"
        # cleanup
        try:
            os.remove(out_path)
        except Exception:
            pass
        return False, latency, None, err

    except Exception:
        latency = int((time.perf_counter() - t0) * 1000)
        try:
            os.remove(out_path)
        except Exception:
            pass
        return False, latency, None, "exception"


@app.on_event("startup")
async def _startup():
    global _sem
    opts = _load_options()
    max_conc = int(_opt(opts, "max_concurrency", 2))
    _sem = asyncio.Semaphore(max(1, max_conc))


@app.get("/api/camera/{camera_id}")
async def camera_status_and_snapshot(
    camera_id: str = Path(..., description="Dahua channel number, e.g. 1"),
):
    opts = _load_options()
    timeout_ms = int(_opt(opts, "health_timeout_ms", 2500))
    jpeg_qv = int(_opt(opts, "jpeg_qv", 7))
    cache_ms = int(_opt(opts, "snapshot_cache_ms", 800))

    # Cache hit? (RAM only; no files written)
    now_ms = int(time.time() * 1000)
    async with _cache_lock:
        ce = _cache.get(camera_id)
        if ce and (now_ms - ce.ts_ms) <= max(0, cache_ms):
            return _make_response(camera_id, ce.ok, ce.latency_ms, ce.detail, ce.jpeg)

    rtsp_url = _build_rtsp_url(opts, camera_id)

    assert _sem is not None

    # Acquire a slot with a hard-coded queue timeout; if too busy, return 503 quickly.
    try:
        await asyncio.wait_for(_sem.acquire(), timeout=QUEUE_TIMEOUT_MS / 1000.0)
    except asyncio.TimeoutError:
        status = {
            "camera_id": camera_id,
            "ok": False,
            "latency_ms": 0,
            "detail": "busy",
        }
        return JSONResponse(status_code=503, content=status)

    try:
        ok, latency_ms, jpeg, detail = await _ffmpeg_grab_jpeg(rtsp_url, timeout_ms, jpeg_qv)
    finally:
        _sem.release()

    # Update cache (RAM only)
    async with _cache_lock:
        _cache[camera_id] = CacheEntry(
            ts_ms=now_ms, ok=ok, latency_ms=latency_ms, detail=detail, jpeg=jpeg
        )

    return _make_response(camera_id, ok, latency_ms, detail, jpeg)


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
