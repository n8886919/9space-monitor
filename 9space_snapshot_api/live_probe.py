"""NVR RTSP live-video probe, ported from
``custom_components/nvr_monitor/api.py`` (``_nvr_channel``).

This intentionally reuses the same DESCRIBE / SETUP / PLAY / RTP-packet
detection algorithm as the integration used to run itself (per AGENTS.md:
"不重新設計另一套完全不同的探測方法"). The only real differences are:

- Operates on a plain ``channel_id: int`` instead of a ``CameraConfig``
  subentry, since the add-on has no concept of Home Assistant subentries.
- Returns a small, already-redacted dict (``live_video`` + ``error_code``)
  instead of the integration's larger diagnostic dict, so nothing NVR
  credential/URL related can leak into the add-on API response or logs.
- Runs synchronously (blocking sockets); callers must run it via
  ``asyncio.to_thread`` so it never blocks the FastAPI event loop.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import socket
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

CONNECT_TIMEOUT = 1.5
RTSP_RESPONSE_TIMEOUT = 3.0
RTP_FIRST_PACKET_TIMEOUT = 3.0
RTP_AFTER_FIRST_PACKET_SECONDS = 2.0
MAX_RTSP_MESSAGE_BYTES = 128 * 1024
MAX_INTERLEAVED_FRAME_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class NvrConfig:
    """Dahua NVR RTSP connection settings (add-on scope only)."""

    host: str
    port: int
    username: str
    password: str


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
        "User-Agent": "9Space-Snapshot-Addon/0.3",
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
    sock.sendall(_encode_request(method, uri, cseq, authorization, extra_headers))
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
    challenge: str, username: str, password: str, method: str, uri: str
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
        _authorization(challenge, username, password, method, uri) if challenge else ""
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


def _resolve_control_uri(presentation_uri: str, content_base: str, control: str) -> str:
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
    return urlunsplit((parsed.scheme, parsed.netloc, f"{path}{control}", parsed.query, ""))


def _video_control_uri(sdp: str, presentation_uri: str, content_base: str) -> str:
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


def _observe_rtp(sock: socket.socket, buffered: bytes, video_channel: int) -> dict[str, Any]:
    started = time.perf_counter()
    deadline = started + RTP_FIRST_PACKET_TIMEOUT
    data = bytearray(buffered)
    packets = 0
    timestamps: set[int] = set()

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
        if packets >= 2 and len(timestamps) >= 2:
            break

    return {"live_video": packets >= 2 and len(timestamps) >= 2}


def _classify_error(exc_or_reason: str) -> str:
    """Map internal probe failure reasons to the stable API.md error codes.

    Never include usernames, passwords, full RTSP URLs or exception text
    here; only short, pre-defined codes are allowed in the API response.
    """
    reason = exc_or_reason.lower()
    if "401" in reason or "auth" in reason:
        return "authentication_failed"
    if "timeout" in reason:
        return "rtsp_timeout"
    if "refused" in reason or "oserror" in reason or "connection" in reason:
        return "nvr_unreachable"
    if "no_video" in reason or "sdp_has_no_video" in reason or "rtp_video_timeout" in reason:
        return "no_video"
    return "internal_error"


def probe_channel(channel_id: int, nvr: NvrConfig) -> dict:
    """Run one DESCRIBE/SETUP/PLAY/RTP probe for ``channel_id``.

    Blocking (uses plain sockets); must be called via ``asyncio.to_thread``.
    Returns ``{"live_video": bool, "error_code": str | None}`` only -- no
    credentials, RTSP URL or raw exception text.
    """
    uri = f"rtsp://{nvr.host}:{nvr.port}/cam/realmonitor?channel={channel_id}&subtype=1"
    sock: socket.socket | None = None
    session_id = ""
    challenge = ""
    cseq = 1
    live_video = False
    error_code: str | None = None
    try:
        sock = socket.create_connection((nvr.host, nvr.port), timeout=CONNECT_TIMEOUT)
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
        if status != 200:
            raise RuntimeError(f"describe_status_{status}")
        sdp = body.decode(errors="replace")
        if "m=video" not in sdp:
            raise RuntimeError("sdp_has_no_video")

        control_uri = _video_control_uri(sdp, uri, headers.get("content-base", ""))
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
        if status != 200:
            raise RuntimeError(f"setup_status_{status}")
        session_id = headers.get("session", "").split(";", 1)[0].strip()
        if not session_id:
            raise RuntimeError("missing_rtsp_session")
        match = re.search(r"interleaved=(\d+)-(\d+)", headers.get("transport", ""), re.I)
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
        if status != 200:
            raise RuntimeError(f"play_status_{status}")

        observed = _observe_rtp(sock, buffered, video_channel)
        live_video = observed["live_video"]
        if not live_video:
            error_code = "no_video"
    except socket.timeout:
        error_code = _classify_error("timeout")
    except ConnectionRefusedError:
        error_code = _classify_error("refused")
    except (OSError, ConnectionError, ValueError, RuntimeError) as err:
        error_code = _classify_error(str(err) or type(err).__name__)
    finally:
        if sock is not None:
            if session_id:
                try:
                    auth = _authorization(challenge, nvr.username, nvr.password, "TEARDOWN", uri)
                    sock.sendall(
                        _encode_request("TEARDOWN", uri, cseq, auth, {"Session": session_id})
                    )
                except (OSError, ValueError):
                    pass
            sock.close()

    return {"live_video": live_video, "error_code": error_code}
