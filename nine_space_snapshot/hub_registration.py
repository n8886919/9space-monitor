"""Best-effort snapshot-site registration with 9Space Hub."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import subprocess
from urllib.request import Request, urlopen

try:
    from .constants import HUB_TELEMETRY_PORT, LOCAL_HUB_HOSTNAME, MAGICDNS_SERVER
except ImportError:  # Container runs modules directly from /app.
    from constants import HUB_TELEMETRY_PORT, LOCAL_HUB_HOSTNAME, MAGICDNS_SERVER

REGISTRATION_INTERVAL_SECONDS = 300
REGISTRATION_TIMEOUT_SECONDS = 2.0
_SITE_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_IPV4_RE = re.compile(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")
_FORBIDDEN_RE = re.compile(r"password|passwd|secret|credential|authorization|token|api[-_ ]?key", re.I)


def safe_site_metadata(site_id: object, display_name: object) -> tuple[str, str] | None:
    if not isinstance(site_id, str) or not _SITE_ID_RE.fullmatch(site_id):
        return None
    if (
        not isinstance(display_name, str)
        or not display_name.strip()
        or len(display_name) > 100
        or any(ord(char) < 32 for char in display_name)
        or _FORBIDDEN_RE.search(display_name)
    ):
        return None
    return site_id, display_name.strip()


def safe_hub_ip(value: object) -> str | None:
    if not isinstance(value, str) or value != value.strip().lower() or len(value) > 253:
        return None
    labels = value.split(".")
    if len(labels) < 4 or labels[-2:] != ["ts", "net"] or any(not _HOST_LABEL_RE.fullmatch(label) for label in labels):
        return None
    return value


def resolve_magicdns_ipv4(hostname: str) -> str | None:
    if safe_hub_ip(hostname) is None:
        return None
    try:
        result = subprocess.run(
            ["nslookup", hostname, MAGICDNS_SERVER], check=False, capture_output=True,
            text=True, timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    for candidate in reversed(_IPV4_RE.findall(result.stdout)):
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if candidate != MAGICDNS_SERVER and address in ipaddress.ip_network("100.64.0.0/10"):
            return candidate
    return None


def hub_registration_destination(value: object, site_id: str, *, resolver=resolve_magicdns_ipv4) -> tuple[str, str, str | None] | None:
    hub_host = safe_hub_ip(value)
    if hub_host is None:
        return None
    host_header = f"{hub_host}:{HUB_TELEMETRY_PORT}"
    if site_id == hub_host.split(".", 1)[0]:
        return f"http://{LOCAL_HUB_HOSTNAME}:{HUB_TELEMETRY_PORT}/api/v1/snapshot-sites/register", host_header, None
    hub_address = resolver(hub_host)
    site_address = resolver(f"{site_id}.{hub_host.split('.', 1)[1]}")
    if hub_address is None or site_address is None:
        return None
    return f"http://{hub_address}:{HUB_TELEMETRY_PORT}/api/v1/snapshot-sites/register", host_header, site_address


def channel_ids(channel_count: object) -> list[int]:
    if type(channel_count) is not int or channel_count < 1:
        return []
    return list(range(1, min(channel_count, 4096) + 1))


async def post_registration(url: str, host_header: str, payload: dict, timeout: float = REGISTRATION_TIMEOUT_SECONDS) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()

    def send() -> None:
        request = Request(url, data=body, headers={"Content-Type": "application/json", "Host": host_header})
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - validated local/Tailscale destination
            if not 200 <= response.status < 300:
                raise OSError("hub_rejected_registration")

    await asyncio.to_thread(send)
