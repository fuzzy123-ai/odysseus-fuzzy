"""Bounded memory capsules for repository planning sources."""

from __future__ import annotations

import re
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional

from src.planning_source_inventory import build_planning_source_inventory
from src.runtime_event_envelope import stable_payload_hash


PLANNING_MEMORY_SCHEMA = "odysseus.planning_source_memory.v1"
PLANNING_MEMORY_SOURCE = "planning_source"
PLANNING_MEMORY_CATEGORY = "project"
DERIVED_PLANNING_MEMORY_SCHEMA = "odysseus.planning.derived_memory.v1"

_ACCEPTED_STATUSES = frozenset({"accepted", "approved", "current", "current_source_of_truth"})
_ACTIVE_SOURCE_STATUSES = frozenset({"active", "current", "available"})
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{12,}", re.IGNORECASE),
    re.compile(r"(?i)(api[_-]?key|authorization|token|secret|password|chat[_-]?id)\s*[:=]\s*[^\s,}]+"),
    re.compile(r"(?i)\b[A-Z]:[\\/][^\s\"'<>]+|\\\\[^\s\"'<>]+|/(?:home|Users|private|var|tmp)/[^\s\"'<>]+"),
)


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


def project_accepted_planning_memory(
    candidates: Iterable[Mapping[str, Any]] | Mapping[str, Any],
    *,
    source_id: str,
    source_ref: str,
    project_id: str = "",
    roadmap_id: str = "",
    related_refs: Iterable[str] = (),
    limit: int = 12,
    preview_chars: int = 240,
) -> Dict[str, Any]:
    """Project matching accepted Planning memory without exposing stored bodies."""

    raw_candidates = candidates.get("capsules") if isinstance(candidates, Mapping) else candidates
    items = list(raw_candidates) if isinstance(raw_candidates, (list, tuple)) else list(raw_candidates or ())
    bounded_limit = max(1, min(int(limit or 12), 50))
    preview_limit = max(0, min(int(preview_chars or 0), 500))
    target_refs = {str(source_ref or "").strip(), *(str(item or "").strip() for item in related_refs)} - {""}
    accepted = matched = rejected = 0
    projected: list[Dict[str, Any]] = []

    for raw in items[:500]:
        normalized = _normalize_memory_candidate(raw, preview_chars=preview_limit)
        if normalized is None:
            rejected += 1
            continue
        accepted += 1
        direct_match = bool(source_id and normalized["source_id"] == source_id) or bool(
            source_ref and normalized["source_ref"] == source_ref
        )
        id_match = bool(project_id and normalized["project_id"] == project_id) or bool(
            roadmap_id and normalized["roadmap_id"] == roadmap_id
        )
        ref_match = bool(target_refs.intersection(normalized["source_refs"]))
        if not (direct_match or id_match or ref_match):
            continue
        matched += 1
        projected.append(normalized)

    projected.sort(
        key=lambda item: (
            -int(item.get("precedence_rank") or 0),
            str(item.get("source_id") or ""),
            str(item.get("source_ref") or ""),
            str(item.get("memory_ref") or ""),
        )
    )
    deduped: list[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in projected:
        key = (str(item.get("source_id") or ""), str(item.get("source_ref") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    returned = deduped[:bounded_limit]
    return {
        "schema": "odysseus.planning.context_memory.v1",
        "read_only": True,
        "writes_performed": False,
        "raw_bodies_included": False,
        "entries": returned,
        "summary": {
            "candidates": min(len(items), 500),
            "accepted": accepted,
            "matched": matched,
            "deduplicated": len(projected) - len(deduped),
            "returned": len(returned),
            "rejected": rejected,
            "truncated": len(deduped) > len(returned) or len(items) > 500,
            "incomplete": len(deduped) > len(returned) or len(items) > 500,
        },
    }


def build_derived_planning_memory_records(
    roadmaps: Iterable[Mapping[str, Any]] | Mapping[str, Any],
    *,
    max_records: int = 100,
    summary_chars: int = 360,
    ref_budget: int = 24,
) -> Dict[str, Any]:
    """Build a bounded, rebuildable projection from validated roadmap metadata.

    The input is an intentionally narrow handoff contract: every item must carry
    ``validation.valid=true`` and ``validation.mode`` set to ``canonical`` or
    ``transition``. Only allowlisted metadata is copied into the result.
    """

    record_limit = max(1, min(int(max_records or 100), 500))
    text_limit = max(40, min(int(summary_chars or 360), 1_000))
    refs_limit = max(1, min(int(ref_budget or 24), 50))
    candidates: list[Dict[str, Any]] = []
    rejected = 0
    input_count = 0
    raw_roadmaps = roadmaps.get("entries") if isinstance(roadmaps, Mapping) and "entries" in roadmaps else roadmaps
    items: Iterable[Any] = (raw_roadmaps,) if isinstance(raw_roadmaps, Mapping) else (raw_roadmaps or ())
    for raw in items:
        input_count += 1
        if input_count > 1_000:
            break
        normalized = _derived_planning_record(raw, summary_chars=text_limit, ref_budget=refs_limit)
        if normalized is None:
            rejected += 1
            continue
        candidates.append(normalized)

    candidates.sort(
        key=lambda item: (
            str(item["memory_ref"]),
            -int(item["precedence_rank"]),
            -int(item.get("revision") or 0),
            str(item["source_hash"]),
        )
    )
    deduped: list[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        memory_ref = str(item["memory_ref"])
        if memory_ref in seen:
            continue
        seen.add(memory_ref)
        deduped.append(item)
    returned = deduped[:record_limit]
    return {
        "schema": "odysseus.planning.derived_memory_index.v1",
        "source": PLANNING_MEMORY_SOURCE,
        "derived": True,
        "rebuildable": True,
        "source_of_truth": False,
        "read_only": True,
        "writes_performed": False,
        "raw_bodies_included": False,
        "source_revision_refs": sorted({str(item["source_revision_ref"]) for item in returned}),
        "entries": returned,
        "summary": {
            "input": min(input_count, 1_000),
            "accepted": len(candidates),
            "rejected": rejected,
            "deduplicated": len(candidates) - len(deduped),
            "returned": len(returned),
            "truncated": input_count > 1_000 or len(deduped) > len(returned),
        },
    }


def _derived_planning_record(
    value: Any,
    *,
    summary_chars: int,
    ref_budget: int,
) -> Optional[Dict[str, Any]]:
    if not isinstance(value, Mapping):
        return None
    validation = value.get("validation") if isinstance(value.get("validation"), Mapping) else {}
    validation_mode = str(validation.get("mode") or "").lower()
    if validation.get("valid") is not True or validation_mode not in {"canonical", "transition"}:
        return None

    project_id = _safe_storage_id(value.get("project_id"))
    roadmap_id = _safe_storage_id(value.get("roadmap_id"))
    source_id = _safe_source_id(value.get("source_id"))
    source_ref = _safe_repo_ref(value.get("source_ref"))
    source_hash = _normalized_sha256(value.get("source_hash"))
    revision = value.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        return None
    source_revision = _safe_revision(value.get("source_revision") or revision)
    if not all((project_id, roadmap_id, source_id, source_ref, source_hash, source_revision)):
        return None

    acceptance_status = str(value.get("acceptance_status") or "accepted").lower()
    source_status = str(value.get("source_status") or "current").lower()
    if acceptance_status not in _ACCEPTED_STATUSES or source_status not in _ACTIVE_SOURCE_STATUSES:
        return None
    classification = str(value.get("classification") or "internal").lower()
    if classification not in {"public", "internal", "private", "confidential", "restricted"}:
        classification = "restricted"

    raw_summary = value.get("safe_summary") or value.get("summary") or value.get("goal") or value.get("title") or ""
    safe_summary = _safe_preview(raw_summary, summary_chars)
    gate_refs = _safe_planning_refs(value.get("gate_refs") or value.get("gates"), prefix="gate", limit=ref_budget)
    dependency_refs = _safe_planning_refs(
        value.get("dependency_refs") or value.get("dependencies"),
        prefix="roadmap",
        limit=ref_budget,
    )
    raw_source_refs = value.get("source_refs") if isinstance(value.get("source_refs"), (list, tuple, set)) else ()
    source_refs = _safe_source_refs((source_ref, *raw_source_refs), limit=ref_budget)
    try:
        precedence = int(value.get("precedence_rank") or (100 if validation_mode == "canonical" else 80))
    except (TypeError, ValueError):
        precedence = 100 if validation_mode == "canonical" else 80
    precedence_rank = max(0, min(precedence, 1_000))
    memory_ref = f"planning:{project_id}:{roadmap_id}"
    source_revision_ref = f"{source_id}@{source_revision}"
    record: Dict[str, Any] = {
        "schema": DERIVED_PLANNING_MEMORY_SCHEMA,
        "memory_ref": memory_ref,
        "source": PLANNING_MEMORY_SOURCE,
        "derived": True,
        "rebuildable": True,
        "source_of_truth": False,
        "project_id": project_id,
        "roadmap_id": roadmap_id,
        "source_id": source_id,
        "source_ref": source_ref,
        "source_hash": source_hash,
        "revision": revision,
        "source_revision": source_revision,
        "source_revision_ref": source_revision_ref,
        "safe_summary": safe_summary,
        "gate_refs": gate_refs,
        "dependency_refs": dependency_refs,
        "source_refs": source_refs,
        "provenance": {
            "source_id": source_id,
            "source_ref": source_ref,
            "source_hash": source_hash,
            "source_revision": source_revision,
            "validation_mode": validation_mode,
        },
        "precedence_rank": precedence_rank,
        "acceptance_status": acceptance_status,
        "source_status": source_status,
        "classification": classification,
        "redaction": {
            "applied": safe_summary != " ".join(str(raw_summary or "").split()),
            "raw_body_included": False,
            "private_paths_included": False,
            "secrets_included": False,
        },
    }
    record["content_hash"] = stable_payload_hash(record)
    return record


def _safe_storage_id(value: Any) -> str:
    text = str(value or "").strip()
    return text if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", text) else ""


def _normalized_sha256(value: Any) -> str:
    text = str(value or "").strip().lower()
    digest = text[7:] if text.startswith("sha256:") else text
    return f"sha256:{digest}" if re.fullmatch(r"[a-f0-9]{64}", digest) else ""


def _safe_source_id(value: Any) -> str:
    text = str(value or "").strip()
    return text if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}(?::[A-Za-z0-9][A-Za-z0-9._-]{0,119})?", text) else ""


def _safe_revision(value: Any) -> str:
    text = str(value or "").strip()
    return text if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}", text) else ""


def _safe_planning_refs(value: Any, *, prefix: str, limit: int) -> List[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    refs: set[str] = set()
    for item in value:
        raw = (item.get("id") or item.get(f"{prefix}_id")) if isinstance(item, Mapping) else item
        token = str(raw or "").strip()
        if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", token):
            token = f"{prefix}:{token}"
        elif not re.fullmatch(r"(?:project|roadmap|gate|slice|todo|repo-plan):[A-Za-z0-9][A-Za-z0-9._-]{0,119}", token):
            continue
        refs.add(token)
    return sorted(refs)[:limit]


def _safe_source_refs(value: Any, *, limit: int) -> List[str]:
    refs = {_safe_repo_ref(item) for item in value if item is not None}
    return sorted(ref for ref in refs if ref)[:limit]


def _capsule_from_source(source: Mapping[str, Any], *, preview_chars: int) -> Dict[str, Any]:
    kind = str(source.get("kind") or "planning_source")
    source_ref = str(source.get("path") or "")
    return {
        "schema": PLANNING_MEMORY_SCHEMA,
        "source": PLANNING_MEMORY_SOURCE,
        "source_status": "active",
        "acceptance_status": "accepted",
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
        "acceptance_status": "accepted",
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


def _normalize_memory_candidate(value: Any, *, preview_chars: int) -> Optional[Dict[str, Any]]:
    if not isinstance(value, Mapping):
        return None
    metadata = value.get("metadata") if isinstance(value.get("metadata"), Mapping) else {}
    source = str(value.get("source") or metadata.get("source") or "")
    if source != PLANNING_MEMORY_SOURCE:
        return None
    source_status = str(value.get("source_status") or metadata.get("source_status") or "active").lower()
    acceptance = str(
        value.get("acceptance_status")
        or metadata.get("acceptance_status")
        or value.get("memory_status")
        or metadata.get("memory_status")
        or ""
    ).lower()
    accepted_flag = value.get("accepted") is True or metadata.get("accepted") is True
    if source_status not in _ACTIVE_SOURCE_STATUSES or (acceptance not in _ACCEPTED_STATUSES and not accepted_flag):
        return None

    source_id = _safe_identity(value.get("source_id") or metadata.get("source_id"))
    source_ref = _safe_repo_ref(value.get("source_ref") or metadata.get("source_ref"))
    if not source_id or not source_ref:
        return None
    raw_source_refs = value.get("source_refs") or metadata.get("source_refs") or ()
    if not isinstance(raw_source_refs, (list, tuple, set)):
        raw_source_refs = ()
    source_refs = tuple(
        ref
        for ref in (_safe_repo_ref(item) for item in raw_source_refs)
        if ref
    )[:24]
    preview = value.get("preview") or metadata.get("preview") or metadata.get("safe_summary") or ""
    return {
        "memory_ref": _safe_identity(value.get("memory_ref") or value.get("id") or _memory_id(source_id)),
        "source_id": source_id,
        "source_ref": source_ref,
        "source_hash": _safe_identity(value.get("source_hash") or metadata.get("source_hash")),
        "project_id": _safe_identity(value.get("project_id") or metadata.get("project_id")),
        "roadmap_id": _safe_identity(value.get("roadmap_id") or metadata.get("roadmap_id") or value.get("plan_id") or metadata.get("plan_id")),
        "title": _safe_preview(value.get("title") or metadata.get("title") or "Planning memory", 160),
        "kind": _safe_identity(value.get("kind") or metadata.get("kind") or "planning_source"),
        "memory_status": _safe_identity(value.get("memory_status") or metadata.get("memory_status") or acceptance),
        "acceptance_status": _safe_identity(acceptance or "accepted"),
        "source_status": _safe_identity(source_status),
        "precedence_rank": max(0, min(int(value.get("precedence_rank") or metadata.get("precedence_rank") or 0), 1000)),
        "dependency_hints": tuple(_safe_preview(item, 160) for item in (value.get("dependency_hints") or ()) if str(item or "").strip())[:24],
        "source_refs": source_refs,
        "preview": _safe_preview(preview, preview_chars),
        "provenance": {
            "source_id": source_id,
            "source_ref": source_ref,
            "source_hash": _safe_identity(value.get("source_hash") or metadata.get("source_hash")),
        },
        "raw_body_included": False,
    }


def _safe_identity(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.:/-]+", "_", str(value or "")).strip("._-")
    return text[:180]


def _safe_repo_ref(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text or "\x00" in text or text.startswith("/") or re.match(r"^[A-Za-z]:/|^[A-Za-z][A-Za-z0-9+.-]*://", text):
        return ""
    if any(part in {"", ".", ".."} for part in text.split("/")):
        return ""
    return _safe_preview(text, 240)


def _safe_preview(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text[: max(0, int(limit or 0))]


def _vector_add(memory_vector: Any, memory_id: str, text: str) -> None:
    if memory_vector is not None and getattr(memory_vector, "healthy", True):
        memory_vector.add(memory_id, text)


def _vector_replace(memory_vector: Any, memory_id: str, text: str) -> None:
    if memory_vector is not None and getattr(memory_vector, "healthy", True):
        try:
            memory_vector.remove(memory_id)
        finally:
            memory_vector.add(memory_id, text)
