"""Bounded entity-registry migration helpers for 9Space Hub."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol


HUB_PLATFORM = "nine_space_hub"


RETIRED_ENTITY_KEYS = frozenset(
    {
        "live_video",
        "recording_query_ok",
        "recording_recent",
        "recording_files_24h",
        "recording_coverage_24h",
        "last_recording",
        "last_snapshot_attempt",
    }
)


class RegistryEntryLike(Protocol):
    entity_id: str
    platform: str
    unique_id: str


def is_retired_hub_unique_id(unique_id: str) -> bool:
    """Return whether a Hub unique ID ends in one retired entity key."""
    return any(unique_id.endswith(f"_{key}") for key in RETIRED_ENTITY_KEYS)


def retired_hub_entity_ids(
    entries: Iterable[RegistryEntryLike],
) -> tuple[str, ...]:
    """Select only retired entities owned by the Hub platform."""
    return tuple(
        entry.entity_id
        for entry in entries
        if entry.platform == HUB_PLATFORM
        and is_retired_hub_unique_id(entry.unique_id)
    )
