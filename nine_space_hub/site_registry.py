"""Bounded, atomic persistence for validated Hub site registrations."""

from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import tempfile
import threading
from urllib.parse import urlsplit

from .scheduler import SnapshotSite
from .validation import RegistrationValidationError, validate_registration

MAX_REGISTERED_SITES = 32
MAX_REGISTRY_BYTES = 128 * 1024
REGISTRY_VERSION = 1
LOCAL_SNAPSHOT_HOSTNAME = "afa94ae2-9space-snapshot"
TAILSCALE_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILSCALE_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")


def _valid_base_url(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid_site_registry")
    try:
        parsed = urlsplit(value)
        address = ipaddress.ip_address(parsed.hostname or "")
    except ValueError:
        address = None
    local = (
        parsed.scheme == "http"
        and parsed.hostname == LOCAL_SNAPSHOT_HOSTNAME
        and parsed.port == 8000
    )
    remote = (
        parsed.scheme == "http"
        and address is not None
        and (address in TAILSCALE_V4 or address in TAILSCALE_V6)
        and parsed.port == 8222
    )
    if (
        not (local or remote)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid_site_registry")
    return value.rstrip("/")


def _decode_site(raw: object, refresh_seconds: int) -> SnapshotSite:
    expected = {"site_id", "display_name", "base_url", "channels", "concurrency", "timeout_seconds"}
    if not isinstance(raw, dict) or set(raw) != expected:
        raise ValueError("invalid_site_registry")
    try:
        registration = validate_registration({
            "site_id": raw["site_id"],
            "display_name": raw["display_name"],
            "channels": raw["channels"],
            "concurrency": raw["concurrency"],
            "timeout_seconds": raw["timeout_seconds"],
            "site_ip": None,
        })
    except RegistrationValidationError as exc:
        raise ValueError("invalid_site_registry") from exc
    return SnapshotSite(
        registration.site_id,
        registration.display_name,
        _valid_base_url(raw["base_url"]),
        registration.channels,
        registration.concurrency,
        registration.timeout_seconds,
        refresh_seconds,
    )


def _encode_site(site: SnapshotSite) -> dict[str, object]:
    return {
        "site_id": site.site_id,
        "display_name": site.display_name,
        "base_url": _valid_base_url(site.base_url),
        "channels": list(site.channels),
        "concurrency": site.concurrency,
        "timeout_seconds": site.timeout_seconds,
    }


class SiteRegistry:
    """Persist only current site configuration; health and attempts stay in RAM."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._sites: dict[str, SnapshotSite] = {}

    def load(self, *, refresh_seconds: int) -> tuple[SnapshotSite, ...]:
        try:
            raw_bytes = self.path.read_bytes()
        except FileNotFoundError:
            self._sites = {}
            return ()
        if len(raw_bytes) > MAX_REGISTRY_BYTES:
            raise ValueError("site_registry_too_large")
        try:
            payload = json.loads(raw_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_site_registry") from exc
        if (
            not isinstance(payload, dict)
            or set(payload) != {"version", "sites"}
            or payload["version"] != REGISTRY_VERSION
            or not isinstance(payload["sites"], list)
            or len(payload["sites"]) > MAX_REGISTERED_SITES
        ):
            raise ValueError("invalid_site_registry")
        sites = tuple(_decode_site(item, refresh_seconds) for item in payload["sites"])
        if len({site.site_id for site in sites}) != len(sites):
            raise ValueError("invalid_site_registry")
        self._sites = {site.site_id: site for site in sites}
        return sites

    def upsert(self, site: SnapshotSite) -> bool:
        with self._lock:
            if site.site_id not in self._sites and len(self._sites) >= MAX_REGISTERED_SITES:
                return False
            updated = dict(self._sites)
            updated[site.site_id] = site
            payload = {
                "version": REGISTRY_VERSION,
                "sites": [_encode_site(item) for item in updated.values()],
            }
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
            if len(encoded) > MAX_REGISTRY_BYTES:
                raise ValueError("site_registry_too_large")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary_name = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb", dir=self.path.parent, prefix=f".{self.path.name}.", delete=False
                ) as handle:
                    temporary_name = handle.name
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_name, self.path)
            finally:
                if temporary_name is not None:
                    Path(temporary_name).unlink(missing_ok=True)
            self._sites = updated
            return True
