"""Bounded memory capsules for repository planning sources."""

from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List, Mapping, Optional

from src.planning_source_inventory import build_planning_source_inventory


PLANNING_MEMORY_SCHEMA = "odysseus.planning_source_memory.v1"
PLANNING_MEMORY_SOURCE = "planning_source"
PLANNING_MEMORY_CATEGORY = "project"


def build_planning_memory_capsules(
    repo_root: str,
    *,
    preview_chars: int = 240,
) -> Dict[str, Any]:
    inventory = build_planning_source_inventory(repo_root, preview_chars=preview_chars)
    capsules = [_capsule_from_source(item, preview_chars=inventory["preview_chars"]) for item in inventory["sources"]]
    return {
        "schema": PLANNING_MEMORY_SCHEMA,
        "read_only": True,
        "writes_supported": False,
        "inventory": {
            "schema": inventory["schema"],
            "summary": inventory["summary"],
            "allowlist": inventory["allowlist"],
            "preview_chars": inventory["preview_chars"],
        },
        "summary": _capsule_summary(capsules),
        "capsules": capsules,
    }


def ingest_planning_sources_to_memory(
    memory_manager: Any,
    repo_root: str,
    *,
    owner: Optional[str] = None,
    memory_vector: Any = None,
    preview_chars: int = 240,
    dry_run: bool = False,
) -> Dict[str, Any]:
    payload = build_planning_memory_capsules(repo_root, preview_chars=preview_chars)
    capsules = payload["capsules"]
    existing = list(memory_manager.load_all())
    existing_by_source = _existing_planning_memories(existing, owner=owner)
    current_source_ids = {capsule["source_id"] for capsule in capsules}
    created = updated = unchanged = deleted_marked = 0
    operations: List[Dict[str, str]] = []
    now = int(time.time())

    for capsule in capsules:
        source_id = capsule["source_id"]
        text = _capsule_text(capsule)
        previous = existing_by_source.get(source_id)
        if previous is None:
            created += 1
            operations.append({"source_id": source_id, "operation": "create", "path": capsule["source_ref"]})
            if not dry_run:
                entry = _new_memory_entry(memory_manager, capsule, text, owner=owner, now=now)
                existing.append(entry)
                _vector_add(memory_vector, entry["id"], text)
            continue

        metadata = previous.get("metadata") if isinstance(previous.get("metadata"), dict) else {}
        if metadata.get("source_hash") == capsule["source_hash"] and previous.get("text") == text:
            unchanged += 1
            operations.append({"source_id": source_id, "operation": "unchanged", "path": capsule["source_ref"]})
            continue

        updated += 1
        operations.append({"source_id": source_id, "operation": "update", "path": capsule["source_ref"]})
        if not dry_run:
            previous["text"] = text
            previous["timestamp"] = now
            previous["source"] = PLANNING_MEMORY_SOURCE
            previous["category"] = PLANNING_MEMORY_CATEGORY
            previous["owner"] = owner
            previous["metadata"] = _memory_metadata(capsule, source_status="active")
            _vector_replace(memory_vector, previous["id"], text)

    for source_id, entry in sorted(existing_by_source.items()):
        if source_id in current_source_ids:
            continue
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        if metadata.get("source_status") == "deleted":
            continue
        deleted_marked += 1
        operations.append({"source_id": source_id, "operation": "mark_deleted", "path": metadata.get("source_ref", "")})
        if not dry_run:
            entry["timestamp"] = now
            metadata = dict(metadata)
            metadata["source_status"] = "deleted"
            entry["metadata"] = metadata
            entry["text"] = _deleted_source_text(entry)
            _vector_replace(memory_vector, entry["id"], entry["text"])

    if not dry_run:
        memory_manager.save(existing)

    return {
        "schema": "odysseus.planning_source_memory_ingest.v1",
        "dry_run": bool(dry_run),
        "repo_source": "planning_sources",
        "summary": {
            "capsules": len(capsules),
            "created": created,
            "updated": updated,
            "unchanged": unchanged,
            "deleted_marked": deleted_marked,
            "raw_content_bounded": all(len(capsule.get("preview", "")) <= int(preview_chars or 0) for capsule in capsules),
            "source_refs_present": all(bool(capsule.get("source_ref")) for capsule in capsules),
            "current_json_roadmaps_have_priority": all(
                capsule["precedence_rank"] >= 100
                for capsule in capsules
                if capsule.get("kind") == "roadmap_json"
            ),
        },
        "operations": operations[:200],
    }


def _capsule_from_source(source: Mapping[str, Any], *, preview_chars: int) -> Dict[str, Any]:
    kind = str(source.get("kind") or "planning_source")
    source_ref = str(source.get("path") or "")
    return {
        "schema": PLANNING_MEMORY_SCHEMA,
        "source_id": str(source.get("source_id") or ""),
        "source_ref": source_ref,
        "source_hash": str(source.get("source_hash") or ""),
        "title": str(source.get("title") or ""),
        "plan_id": str(source.get("plan_id") or ""),
        "kind": kind,
        "memory_status": "current_source_of_truth" if kind == "roadmap_json" else "supporting_plan_source",
        "precedence_rank": _precedence_rank(kind),
        "dependency_hints": _safe_string_list(source.get("dependency_hints")),
        "source_refs": _safe_string_list(source.get("source_refs")),
        "preview": str(source.get("preview") or "")[: max(0, int(preview_chars or 0))],
    }


def _capsule_text(capsule: Mapping[str, Any]) -> str:
    lines = [
        f"Planning source: {capsule.get('title') or capsule.get('source_ref')}",
        f"Source ref: {capsule.get('source_ref')}",
        f"Kind: {capsule.get('kind')}",
        f"Plan ID: {capsule.get('plan_id') or 'n/a'}",
        f"Status: {capsule.get('memory_status')}",
        f"Precedence rank: {capsule.get('precedence_rank')}",
        f"Source hash: {capsule.get('source_hash')}",
    ]
    dependencies = ", ".join(_safe_string_list(capsule.get("dependency_hints"))) or "none"
    source_refs = ", ".join(_safe_string_list(capsule.get("source_refs"))) or "none"
    lines.append(f"Dependencies: {dependencies}")
    lines.append(f"Referenced sources: {source_refs}")
    preview = str(capsule.get("preview") or "").strip()
    if preview:
        lines.append(f"Bounded preview: {preview}")
    return "\n".join(lines)


def _new_memory_entry(memory_manager: Any, capsule: Mapping[str, Any], text: str, *, owner: Optional[str], now: int) -> Dict[str, Any]:
    entry = memory_manager.add_entry(
        text,
        source=PLANNING_MEMORY_SOURCE,
        category=PLANNING_MEMORY_CATEGORY,
        owner=owner,
    )
    entry["id"] = _memory_id(capsule["source_id"])
    entry["timestamp"] = now
    entry["metadata"] = _memory_metadata(capsule, source_status="active")
    if owner is not None:
        entry["owner"] = owner
    return entry


def _memory_metadata(capsule: Mapping[str, Any], *, source_status: str) -> Dict[str, Any]:
    return {
        "schema": PLANNING_MEMORY_SCHEMA,
        "source_id": str(capsule.get("source_id") or ""),
        "source_ref": str(capsule.get("source_ref") or ""),
        "source_hash": str(capsule.get("source_hash") or ""),
        "plan_id": str(capsule.get("plan_id") or ""),
        "kind": str(capsule.get("kind") or ""),
        "memory_status": str(capsule.get("memory_status") or ""),
        "source_status": source_status,
        "precedence_rank": int(capsule.get("precedence_rank") or 0),
    }


def _deleted_source_text(entry: Mapping[str, Any]) -> str:
    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    return "\n".join(
        [
            "Planning source deleted or unavailable.",
            f"Source ref: {metadata.get('source_ref') or 'unknown'}",
            f"Source ID: {metadata.get('source_id') or 'unknown'}",
            "Status: deleted",
        ]
    )


def _existing_planning_memories(entries: Iterable[Mapping[str, Any]], *, owner: Optional[str]) -> Dict[str, Dict[str, Any]]:
    found: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("source") != PLANNING_MEMORY_SOURCE:
            continue
        if owner is not None and entry.get("owner") != owner:
            continue
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        source_id = str(metadata.get("source_id") or "")
        if source_id:
            found[source_id] = entry
    return found


def _capsule_summary(capsules: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    capsule_list = list(capsules)
    return {
        "capsules": len(capsule_list),
        "source_refs_present": all(bool(capsule.get("source_ref")) for capsule in capsule_list),
        "raw_content_bounded": True,
        "current_json_roadmaps_have_priority": all(
            capsule["precedence_rank"] >= 100
            for capsule in capsule_list
            if capsule.get("kind") == "roadmap_json"
        ),
    }


def _precedence_rank(kind: str) -> int:
    if kind == "roadmap_json":
        return 100
    if kind in {"planning_doc_json", "roadmap_doc"}:
        return 70
    if kind == "planning_json":
        return 60
    return 50


def _memory_id(source_id: str) -> str:
    suffix = str(source_id or "").split(":", 1)[-1]
    return f"planning-source-{suffix}"


def _safe_string_list(value: Any) -> List[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()][:50]


def _vector_add(memory_vector: Any, memory_id: str, text: str) -> None:
    if memory_vector is not None and getattr(memory_vector, "healthy", True):
        memory_vector.add(memory_id, text)


def _vector_replace(memory_vector: Any, memory_id: str, text: str) -> None:
    if memory_vector is not None and getattr(memory_vector, "healthy", True):
        try:
            memory_vector.remove(memory_id)
        finally:
            memory_vector.add(memory_id, text)
