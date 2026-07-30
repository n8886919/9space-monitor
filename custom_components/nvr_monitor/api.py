"""Low-load camera and Dahua NVR probes."""

from __future__ import annotations

import base64
import concurrent.futures
from dataclasses import dataclass
import hashlib
import re
import secrets
import socket
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .models import CameraConfig, ProbeResults

CONNECT_TIMEOUT = 1.5
RTSP_RESPONSE_TIMEOUT = 3.0
RTP_FIRST_PACKET_TIMEOUT = 3.0
RTP_AFTER_FIRST_PACKET_SECONDS = 2.0
MAX_RTSP_MESSAGE_BYTES = 128 * 1024
MAX_INTERLEAVED_FRAME_BYTES = 2 * 1024 * 1024
CAMERA_WORKERS = 4
NVR_WORKERS = 1


@dataclass(frozen=True, slots=True)
class NvrConfig:
    """Dahua NVR connection settings."""

    host: str
    http_port: int
    port: int
    username: str
    password: str


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


def _read_rtsp_response(
    sock: socket.socket, buffered: bytes = b""
) -> tuple[int | None, dict[str, str], bytes, bytes]:
    data = buffered
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("rtsp_connection_closed")
        data += chunk
        if len(data) > MAX_RTSP_MESSAGE_BYTES:
            raise ValueError("rtsp_header_too_large")

    raw_header, remainder = data.split(b"\r\n\r\n", 1)
    lines = raw_header.decode("iso-8859-1", errors="replace").split("\r\n")
    match = re.match(r"RTSP/\d\.\d\s+(\d{3})", lines[0])
    status = int(match.group(1)) if match else None
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()

    content_length = int(headers.get("content-length", "0") or "0")
    while len(remainder) < content_length:
        chunk = sock.recv(min(4096, content_length - len(remainder)))
        if not chunk:
            raise ConnectionError("rtsp_body_truncated")
        remainder += chunk
    return (
        status,
        headers,
        remainder[:content_length],
        remainder[content_length:],
    )


def _encode_request(
    method: str,
    uri: str,
    cseq: int,
    authorization: str = "",
    extra_headers: dict[str, str] | None = None,
) -> bytes:
    headers = {
        "CSeq": str(cseq),
        "User-Agent": "NVR-Monitor/0.1",
    }
    if authorization:
        headers["Authorization"] = authorization
    if extra_headers:
        headers.update(extra_headers)
    encoded = "".join(f"{key}: {value}\r\n" for key, value in headers.items())
    return f"{method} {uri} RTSP/1.0\r\n{encoded}\r\n".encode()


def _send_request(
    sock: socket.socket,
    method: str,
    uri: str,
    cseq: int,
    authorization: str = "",
    extra_headers: dict[str, str] | None = None,
    buffered: bytes = b"",
) -> tuple[int | None, dict[str, str], bytes, bytes]:
    sock.sendall(
        _encode_request(method, uri, cseq, authorization, extra_headers)
    )
    return _read_rtsp_response(sock, buffered)


def _parse_auth_challenge(header: str) -> tuple[str, dict[str, str]]:
    scheme, _, remainder = header.partition(" ")
    values: dict[str, str] = {}
    for match in re.finditer(
        r'([A-Za-z][A-Za-z0-9_-]*)\s*=\s*(?:"([^"]*)"|([^,\s]+))',
        remainder,
    ):
        values[match.group(1).lower()] = match.group(2) or match.group(3) or ""
    return scheme.lower(), values


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _authorization(
    challenge: str,
    username: str,
    password: str,
    method: str,
    uri: str,
) -> str:
    scheme, values = _parse_auth_challenge(challenge)
    if scheme == "basic":
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        return f"Basic {token}"
    if scheme != "digest":
        raise ValueError("unsupported_rtsp_auth")

    realm = values.get("realm", "")
    nonce = values.get("nonce", "")
    if not nonce:
        raise ValueError("missing_rtsp_nonce")
    algorithm = values.get("algorithm", "MD5").upper()
    cnonce = secrets.token_hex(8)
    ha1 = _md5(f"{username}:{realm}:{password}")
    if algorithm == "MD5-SESS":
        ha1 = _md5(f"{ha1}:{nonce}:{cnonce}")
    elif algorithm != "MD5":
        raise ValueError("unsupported_digest_algorithm")
    ha2 = _md5(f"{method}:{uri}")
    qops = [item.strip() for item in values.get("qop", "").split(",")]
    qop = "auth" if "auth" in qops else ""
    if qop:
        nc = "00000001"
        response = _md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
    else:
        nc = ""
        response = _md5(f"{ha1}:{nonce}:{ha2}")

    fields = [
        f'username="{username}"',
        f'realm="{realm}"',
        f'nonce="{nonce}"',
        f'uri="{uri}"',
        f'response="{response}"',
        f"algorithm={algorithm}",
    ]
    if values.get("opaque"):
        fields.append(f'opaque="{values["opaque"]}"')
    if qop:
        fields.extend([f"qop={qop}", f"nc={nc}", f'cnonce="{cnonce}"'])
    return "Digest " + ", ".join(fields)


def _send_authenticated(
    sock: socket.socket,
    method: str,
    uri: str,
    cseq: int,
    username: str,
    password: str,
    challenge: str,
    extra_headers: dict[str, str] | None = None,
    buffered: bytes = b"",
) -> tuple[int | None, dict[str, str], bytes, bytes, int, str]:
    auth = (
        _authorization(challenge, username, password, method, uri)
        if challenge
        else ""
    )
    status, headers, body, extra = _send_request(
        sock, method, uri, cseq, auth, extra_headers, buffered
    )
    cseq += 1
    if status == 401 and (new_challenge := headers.get("www-authenticate")):
        challenge = new_challenge
        auth = _authorization(challenge, username, password, method, uri)
        status, headers, body, extra = _send_request(
            sock, method, uri, cseq, auth, extra_headers, extra
        )
        cseq += 1
    return status, headers, body, extra, cseq, challenge


def _resolve_control_uri(
    presentation_uri: str, content_base: str, control: str
) -> str:
    if not control or control == "*":
        return presentation_uri
    if control.lower().startswith("rtsp://"):
        return control
    if control.startswith("/"):
        parsed = urlsplit(presentation_uri)
        return f"{parsed.scheme}://{parsed.netloc}{control}"
    base = content_base or presentation_uri
    parsed = urlsplit(base)
    path = parsed.path if parsed.path.endswith("/") else f"{parsed.path}/"
    return urlunsplit(
        (parsed.scheme, parsed.netloc, f"{path}{control}", parsed.query, "")
    )


def _video_control_uri(
    sdp: str, presentation_uri: str, content_base: str
) -> str:
    in_video = False
    control = ""
    for raw_line in sdp.splitlines():
        line = raw_line.strip()
        if line.startswith("m="):
            if in_video:
                break
            in_video = line.startswith("m=video")
        elif in_video and line.startswith("a=control:"):
            control = line.partition(":")[2].strip()
            break
    return _resolve_control_uri(presentation_uri, content_base, control)


def _observe_rtp(
    sock: socket.socket, buffered: bytes, video_channel: int
) -> dict[str, Any]:
    started = time.perf_counter()
    deadline = started + RTP_FIRST_PACKET_TIMEOUT
    data = bytearray(buffered)
    packets = 0
    timestamps: set[int] = set()
    first_packet_ms: float | None = None

    while time.perf_counter() < deadline:
        while len(data) < 4 and time.perf_counter() < deadline:
            sock.settimeout(max(0.05, min(0.5, deadline - time.perf_counter())))
            try:
                chunk = sock.recv(8192)
            except socket.timeout:
                continue
            if not chunk:
                break
            data.extend(chunk)
        if len(data) < 4:
            continue
        if data[0] != 0x24:
            marker = data.find(0x24)
            if marker < 0:
                data.clear()
            else:
                del data[:marker]
            continue

        channel = data[1]
        frame_length = int.from_bytes(data[2:4], "big")
        if frame_length > MAX_INTERLEAVED_FRAME_BYTES:
            raise ValueError("interleaved_frame_too_large")
        while len(data) < 4 + frame_length and time.perf_counter() < deadline:
            sock.settimeout(max(0.05, min(0.5, deadline - time.perf_counter())))
            try:
                chunk = sock.recv(min(65536, 4 + frame_length - len(data)))
            except socket.timeout:
                continue
            if not chunk:
                break
            data.extend(chunk)
        if len(data) < 4 + frame_length:
            continue

        payload = bytes(data[4 : 4 + frame_length])
        del data[: 4 + frame_length]
        if channel != video_channel or len(payload) < 12 or payload[0] >> 6 != 2:
            continue
        packets += 1
        timestamps.add(int.from_bytes(payload[4:8], "big"))
        if first_packet_ms is None:
            first_at = time.perf_counter()
            first_packet_ms = round((first_at - started) * 1000, 1)
            deadline = max(deadline, first_at + RTP_AFTER_FIRST_PACKET_SECONDS)
        if packets >= 2 and len(timestamps) >= 2:
            break

    return {
        "nvr_live_video": packets >= 2 and len(timestamps) >= 2,
        "nvr_rtp_packets": packets,
        "nvr_rtp_timestamps": len(timestamps),
        "nvr_first_packet_ms": first_packet_ms,
    }


def _camera_services(camera: CameraConfig) -> tuple[str, dict[str, Any]]:
    result: dict[str, Any] = {}
    for key, port in (
        ("onvif_port", camera.onvif_port),
        ("rtsp_port", camera.rtsp_port),
    ):
        ok, latency, error = _tcp_probe(camera.ip, port)
        result[key] = ok
        result[f"{key}_ms"] = latency
        result[f"{key}_error"] = error

    started = time.perf_counter()
    uri = f"rtsp://{camera.ip}:{camera.rtsp_port}/stream1"
    try:
        with socket.create_connection(
            (camera.ip, camera.rtsp_port), timeout=CONNECT_TIMEOUT
        ) as sock:
            sock.settimeout(RTSP_RESPONSE_TIMEOUT)
            status, headers, _, _ = _send_request(
                sock,
                "DESCRIBE",
                uri,
                1,
                extra_headers={"Accept": "application/sdp"},
            )
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
    except socket.timeout:
        error = "timeout"
    except ConnectionRefusedError:
        error = "refused"
    except (OSError, ConnectionError, ValueError) as err:
        error = str(err) or type(err).__name__.lower()
    else:
        return camera.subentry_id, result
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


def _nvr_channel(camera: CameraConfig, nvr: NvrConfig) -> tuple[str, dict[str, Any]]:
    started = time.perf_counter()
    uri = (
        f"rtsp://{nvr.host}:{nvr.port}/cam/realmonitor"
        f"?channel={camera.channel}&subtype=1"
    )
    result: dict[str, Any] = {
        "nvr_describe_ok": False,
        "nvr_setup_ok": False,
        "nvr_play_ok": False,
        "nvr_live_video": False,
        "nvr_rtp_packets": 0,
        "nvr_rtp_timestamps": 0,
        "nvr_first_packet_ms": None,
        "nvr_error": "",
    }
    sock: socket.socket | None = None
    session_id = ""
    challenge = ""
    cseq = 1
    try:
        sock = socket.create_connection(
            (nvr.host, nvr.port), timeout=CONNECT_TIMEOUT
        )
        sock.settimeout(RTSP_RESPONSE_TIMEOUT)
        status, headers, body, buffered, cseq, challenge = _send_authenticated(
            sock,
            "DESCRIBE",
            uri,
            cseq,
            nvr.username,
            nvr.password,
            challenge,
            {"Accept": "application/sdp"},
        )
        result["nvr_describe_status"] = status
        if status != 200:
            raise RuntimeError(f"describe_status_{status}")
        sdp = body.decode(errors="replace")
        result["nvr_describe_ok"] = True
        if "m=video" not in sdp:
            raise RuntimeError("sdp_has_no_video")

        control_uri = _video_control_uri(
            sdp, uri, headers.get("content-base", "")
        )
        status, headers, _, buffered, cseq, challenge = _send_authenticated(
            sock,
            "SETUP",
            control_uri,
            cseq,
            nvr.username,
            nvr.password,
            challenge,
            {"Transport": "RTP/AVP/TCP;unicast;interleaved=0-1"},
            buffered,
        )
        result["nvr_setup_status"] = status
        if status != 200:
            raise RuntimeError(f"setup_status_{status}")
        result["nvr_setup_ok"] = True
        session_id = headers.get("session", "").split(";", 1)[0].strip()
        if not session_id:
            raise RuntimeError("missing_rtsp_session")
        match = re.search(
            r"interleaved=(\d+)-(\d+)", headers.get("transport", ""), re.I
        )
        video_channel = int(match.group(1)) if match else 0

        status, _, _, buffered, cseq, challenge = _send_authenticated(
            sock,
            "PLAY",
            uri,
            cseq,
            nvr.username,
            nvr.password,
            challenge,
            {"Session": session_id, "Range": "npt=0.000-"},
            buffered,
        )
        result["nvr_play_status"] = status
        if status != 200:
            raise RuntimeError(f"play_status_{status}")
        result["nvr_play_ok"] = True
        result.update(_observe_rtp(sock, buffered, video_channel))
        if not result["nvr_live_video"]:
            result["nvr_error"] = "rtp_video_timeout"
    except socket.timeout:
        result["nvr_error"] = "timeout"
    except ConnectionRefusedError:
        result["nvr_error"] = "refused"
    except (OSError, ConnectionError, ValueError, RuntimeError) as err:
        result["nvr_error"] = str(err) or type(err).__name__.lower()
    finally:
        if sock is not None:
            if session_id:
                try:
                    auth = _authorization(
                        challenge,
                        nvr.username,
                        nvr.password,
                        "TEARDOWN",
                        uri,
                    )
                    sock.sendall(
                        _encode_request(
                            "TEARDOWN",
                            uri,
                            cseq,
                            auth,
                            {"Session": session_id},
                        )
                    )
                except (OSError, ValueError):
                    pass
            sock.close()
    result["nvr_probe_ms"] = round(
        (time.perf_counter() - started) * 1000, 1
    )
    return camera.subentry_id, result


class CameraProbeClient:
    """Run bounded-concurrency service probes."""

    def __init__(self, nvr: NvrConfig) -> None:
        self.nvr = nvr

    def validate_nvr(self) -> None:
        """Validate NVR RTSP authentication without starting an RTP stream."""
        uri = (
            f"rtsp://{self.nvr.host}:{self.nvr.port}/cam/realmonitor"
            "?channel=1&subtype=1"
        )
        with socket.create_connection(
            (self.nvr.host, self.nvr.port), timeout=CONNECT_TIMEOUT
        ) as sock:
            sock.settimeout(RTSP_RESPONSE_TIMEOUT)
            status, _, _, _, _, _ = _send_authenticated(
                sock,
                "DESCRIBE",
                uri,
                1,
                self.nvr.username,
                self.nvr.password,
                "",
                {"Accept": "application/sdp"},
            )
        if status == 401:
            raise PermissionError("invalid_auth")
        if status != 200:
            raise ConnectionError(f"describe_status_{status}")

    def probe_services(self, cameras: list[CameraConfig]) -> ProbeResults:
        """Probe direct camera services and NVR RTP without decoding video."""
        results: ProbeResults = {
            camera.subentry_id: {
                "ip": camera.ip,
                "channel": camera.channel,
            }
            for camera in cameras
        }
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=CAMERA_WORKERS
        ) as executor:
            jobs = [executor.submit(_camera_services, camera) for camera in cameras]
            for future in concurrent.futures.as_completed(jobs):
                subentry_id, data = future.result()
                results[subentry_id].update(data)

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=NVR_WORKERS
        ) as executor:
            jobs = [
                executor.submit(_nvr_channel, camera, self.nvr)
                for camera in cameras
            ]
            for future in concurrent.futures.as_completed(jobs):
                subentry_id, data = future.result()
                results[subentry_id].update(data)
        return results
