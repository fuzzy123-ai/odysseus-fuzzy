"""Content-free aggregate snapshot for the Universal Inbox workbench."""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping


UNIVERSAL_INBOX_SNAPSHOT_SCHEMA = "odysseus.universal_inbox.snapshot.v1"

_STATUSES = ("uploaded", "needs_review", "blocked", "unsupported")
_FAMILIES = (
    "archive",
    "asset",
    "audio",
    "dangerous",
    "document",
    "image",
    "message",
    "text",
    "unknown",
    "video",
)


def build_universal_inbox_workspace_snapshot(
    items: Iterable[Mapping[str, Any]],
    *,
    admin_override: bool = False,
) -> dict[str, Any]:
    """Aggregate allowlisted counts without retaining item identifiers."""

    status_counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()
    total = 0

    for item in items:
        if not isinstance(item, Mapping):
            continue
        total += 1
        status = str(item.get("status") or "")
        if status not in _STATUSES:
            status = "unsupported"
        status_counts[status] += 1

        metadata = item.get("metadata")
        family = (
            str(metadata.get("family") or "")
            if isinstance(metadata, Mapping)
            else ""
        )
        if family not in _FAMILIES:
            family = "unknown"
        family_counts[family] += 1

    counts = {status: status_counts[status] for status in _STATUSES}
    families = {
        family: family_counts[family]
        for family in _FAMILIES
        if family_counts[family]
    }
    readiness_state = _readiness_state(total=total, counts=counts)

    return {
        "schema": UNIVERSAL_INBOX_SNAPSHOT_SCHEMA,
        "scope": {
            "source_kind": "upload",
            "owner_scoped": True,
            "admin_override": bool(admin_override),
            "owner_identifier_visible": False,
        },
        "total_count": total,
        "counts": counts,
        "family_counts": families,
        "readiness": {
            "state": readiness_state,
            "ready_count": counts["uploaded"],
            "attention_count": counts["needs_review"] + counts["unsupported"],
            "blocked_count": counts["blocked"],
        },
        "item_names_visible": False,
        "source_refs_visible": False,
        "absolute_paths_visible": False,
        "raw_content_visible": False,
    }


def _readiness_state(*, total: int, counts: Mapping[str, int]) -> str:
    if total == 0:
        return "empty"
    if counts["blocked"]:
        return "blocked_items_present"
    if counts["needs_review"] or counts["unsupported"]:
        return "review_required"
    return "ready"


__all__ = [
    "UNIVERSAL_INBOX_SNAPSHOT_SCHEMA",
    "build_universal_inbox_workspace_snapshot",
]
