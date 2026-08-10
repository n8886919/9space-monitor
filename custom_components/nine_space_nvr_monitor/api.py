"""Blocking camera-LAN probes run by the service coordinator executor."""

from __future__ import annotations

import concurrent.futures
import re
import socket
import time
from typing import Any

from .models import CameraConfig, ProbeResults

CONNECT_TIMEOUT = 1.5
RTSP_RESPONSE_TIMEOUT = 3.0
MAX_RTSP_MESSAGE_BYTES = 128 * 1024
CAMERA_WORKERS = 4


def _tcp_probe(host: str, port: int) -> tuple[bool, float | None, str]:
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT):
            return True, round((time.perf_counter() - started) * 1000, 1), ""
    except socket.timeout:
        return False, None, "timeout"
    except ConnectionRefusedError:
        return False, None, "refused"
    except OSError as err:
        return False, None, f"oserror_{err.errno}" if err.errno else "oserror"


def _read_rtsp_response(sock: socket.socket) -> tuple[int | None, dict[str, str]]:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("rtsp_connection_closed")
        data += chunk
        if len(data) > MAX_RTSP_MESSAGE_BYTES:
            raise ValueError("rtsp_header_too_large")
    lines = data.split(b"\r\n\r\n", 1)[0].decode(
        "iso-8859-1", errors="replace"
    ).split("\r\n")
    match = re.match(r"RTSP/\d\.\d\s+(\d{3})", lines[0])
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    return (int(match.group(1)) if match else None), headers


def _camera_services(camera: CameraConfig) -> tuple[str, dict[str, Any]]:
    """Probe only the configured camera IP and its LAN ports."""
    result: dict[str, Any] = {
        "ip": camera.ip,
        "channel": camera.channel,
    }
    for key, port in (
        ("onvif_port", camera.onvif_port),
        ("rtsp_port", camera.rtsp_port),
    ):
        ok, latency, error = _tcp_probe(camera.ip, port)
        result[key] = ok
        result[f"{key}_ms"] = latency
        result[f"{key}_error"] = error

    started = time.perf_counter()
    try:
        with socket.create_connection(
            (camera.ip, camera.rtsp_port), timeout=CONNECT_TIMEOUT
        ) as sock:
            sock.settimeout(RTSP_RESPONSE_TIMEOUT)
            uri = f"rtsp://{camera.ip}:{camera.rtsp_port}/stream1"
            sock.sendall(
                (
                    f"DESCRIBE {uri} RTSP/1.0\r\n"
                    "CSeq: 1\r\n"
                    "User-Agent: NVR-Monitor/0.1\r\n"
                    "Accept: application/sdp\r\n\r\n"
                ).encode()
            )
            status, headers = _read_rtsp_response(sock)
        result.update(
            {
                "camera_rtsp_alive": status is not None,
                "camera_rtsp_status": status,
                "camera_rtsp_server": headers.get("server", ""),
                "camera_rtsp_ms": round(
                    (time.perf_counter() - started) * 1000, 1
                ),
                "camera_rtsp_error": "",
            }
        )
        return camera.subentry_id, result
    except socket.timeout:
        error = "timeout"
    except ConnectionRefusedError:
        error = "refused"
    except (OSError, ConnectionError, ValueError) as err:
        error = str(err) or type(err).__name__.lower()
    result.update(
        {
            "camera_rtsp_alive": False,
            "camera_rtsp_status": None,
            "camera_rtsp_server": "",
            "camera_rtsp_ms": None,
            "camera_rtsp_error": error,
        }
    )
    return camera.subentry_id, result


class CameraProbeClient:
    """Run bounded camera-only service probes."""

    def probe_services(self, cameras: list[CameraConfig]) -> ProbeResults:
        """Probe camera RTSP and ONVIF ports without contacting the NVR."""
        results: ProbeResults = {}
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=CAMERA_WORKERS
        ) as executor:
            jobs = [executor.submit(_camera_services, camera) for camera in cameras]
            for future in concurrent.futures.as_completed(jobs):
                subentry_id, data = future.result()
                results[subentry_id] = data
        return results
