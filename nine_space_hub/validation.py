"""Strict validation for snapshot-site registration and safe path values."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re
from typing import Any

MAX_BODY_BYTES = 16 * 1024
MAX_DISPLAY_NAME_CHARS = 100

_SITE_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_FORBIDDEN_RE = re.compile(
    r"(?:https?|rtsp)://|authorization|basic\s+|digest\s+|password|passwd|secret|credential|token|api[-_ ]?key",
    re.IGNORECASE,
)


class RegistrationValidationError(ValueError):
    """A registration payload is outside the fixed snapshot contract."""


@dataclass(frozen=True, slots=True)
class SnapshotRegistration:
    site_id: str
    display_name: str
    channels: tuple[int, ...]
    concurrency: int
    timeout_seconds: int
    site_ip: str | None


def _safe_text(value: Any, *, display_name: bool = False) -> str:
    limit = MAX_DISPLAY_NAME_CHARS if display_name else 64
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > limit
        or any(ord(char) < 32 for char in value)
        or _FORBIDDEN_RE.search(value)
    ):
        raise RegistrationValidationError("invalid_display_name" if display_name else "invalid_site_id")
    return value.strip()


def validate_registration(payload: Any) -> SnapshotRegistration:
    expected = {"site_id", "display_name", "channels", "concurrency", "timeout_seconds", "site_ip"}
    if not isinstance(payload, dict) or set(payload) != expected:
        raise RegistrationValidationError("invalid_registration_contract")
    site_id = _safe_text(payload["site_id"])
    if not _SITE_ID_RE.fullmatch(site_id):
        raise RegistrationValidationError("invalid_site_id")
    display_name = _safe_text(payload["display_name"], display_name=True)
    channels = payload["channels"]
    if (
        not isinstance(channels, list)
        or not channels
        or len(channels) > 256
        or any(type(channel) is not int or not 1 <= channel <= 4096 for channel in channels)
        or len(set(channels)) != len(channels)
    ):
        raise RegistrationValidationError("invalid_channels")
    concurrency = payload["concurrency"]
    timeout = payload["timeout_seconds"]
    if type(concurrency) is not int or not 1 <= concurrency <= 8:
        raise RegistrationValidationError("invalid_concurrency")
    if type(timeout) is not int or not 2 <= timeout <= 60:
        raise RegistrationValidationError("invalid_timeout")
    site_ip = payload["site_ip"]
    if site_ip is not None:
        try:
            address = ipaddress.ip_address(site_ip)
        except ValueError:
            raise RegistrationValidationError("invalid_site_ip") from None
        if address not in ipaddress.ip_network("100.64.0.0/10") and address not in ipaddress.ip_network("fd7a:115c:a1e0::/48"):
            raise RegistrationValidationError("invalid_site_ip")
        site_ip = str(address)
    return SnapshotRegistration(site_id, display_name, tuple(channels), concurrency, timeout, site_ip)


def validate_site_id(site_id: str) -> str:
    if not isinstance(site_id, str) or not _SITE_ID_RE.fullmatch(site_id) or _FORBIDDEN_RE.search(site_id):
        raise RegistrationValidationError("invalid_site_id")
    return site_id


def validate_error_code(value: Any) -> str:
    if not isinstance(value, str) or not _CODE_RE.fullmatch(value) or _FORBIDDEN_RE.search(value):
        raise RegistrationValidationError("invalid_error_code")
    return value
