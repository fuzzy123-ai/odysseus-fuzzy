"""Read-only, privacy-safe Todo/Memory/digest drift audit and repair preview."""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace
from typing import Any, Iterable, Mapping

from src.todo_domain_service import make_list_ref
from src.todo_intent import is_todo_memory_payload, normalize_todo_match_text


TODO_STATE_DRIFT_SCHEMA = "odysseus.todo_state_drift_audit.v1"
TODO_STATE_REPAIR_PREVIEW_SCHEMA = "odysseus.todo_state_repair_preview.v1"
TODO_DATA_REPAIR_LIVE_GATE = "TTD-LIVE-DATA-REPAIR"
_ITEM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


class TodoStateDriftAuditError(ValueError):
    """Raised when a read-only audit input is unsafe or malformed."""


def audit_todo_state_drift(
    *,
    owner: str,
    notes: Iterable[Any],
    memories: Iterable[Mapping[str, Any]],
    digest_limit: int = 20,
    include_review_details: bool = False,
    operator_authorized: bool = False,
) -> dict[str, Any]:
    """Build a non-applying drift report from already-read source records."""
    owner = _require_owner(owner)
    if isinstance(digest_limit, bool) or not isinstance(digest_limit, int) or not 1 <= digest_limit <= 500:
        raise TodoStateDriftAuditError("digest_limit must be between 1 and 500")
    if include_review_details and not operator_authorized:
        raise TodoStateDriftAuditError(
            "exact review details require explicit operator authorization"
        )

    scoped_notes = [
        _note_view(note)
        for note in notes
        if str(_get(note, "owner", "") or "") == owner
        and not bool(_get(note, "archived", False))
    ]
    scoped_notes.sort(
        key=lambda note: (
            bool(note.pinned),
            str(note.updated_at or ""),
            str(note.id or ""),
        ),
        reverse=True,
    )
    scoped_memories = [
        dict(memory)
        for memory in memories
        if isinstance(memory, Mapping)
        and str(memory.get("owner") or "") == owner
    ]

    note_items, malformed_items = _note_item_records(owner, scoped_notes)
    by_fingerprint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in note_items:
        if item["fingerprint"]:
            by_fingerprint[item["fingerprint"]].append(item)

    duplicate_groups: list[dict[str, Any]] = []
    completion_conflicts: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    for fingerprint, items in sorted(by_fingerprint.items()):
        if len(items) < 2:
            continue
        group_ref = _group_ref("duplicate", fingerprint)
        item_refs = [item["item_ref"] for item in items]
        duplicate_groups.append({
            "group_ref": group_ref,
            "item_count": len(items),
            "item_refs": item_refs,
            "content_fingerprint": fingerprint,
        })
        actions.append(_preview_action(
            "dedupe_todo_items",
            group_ref,
            affected_refs=item_refs,
            review_required=True,
        ))
        states = sorted({bool(item["done"]) for item in items})
        if len(states) > 1:
            conflict_ref = _group_ref("completion", fingerprint)
            completion_conflicts.append({
                "conflict_ref": conflict_ref,
                "item_refs": item_refs,
                "observed_done_states": states,
            })
            actions.append(_preview_action(
                "reconcile_todo_completion",
                conflict_ref,
                affected_refs=item_refs,
                review_required=True,
            ))

    prohibited_memories: list[dict[str, Any]] = []
    memory_only_candidates: list[dict[str, Any]] = []
    memory_completion_conflicts: list[dict[str, Any]] = []
    for index, memory in enumerate(scoped_memories):
        text = str(memory.get("text") or "")
        category = str(memory.get("category") or "fact").strip().lower()
        if not is_todo_memory_payload(text=text, category=category):
            continue
        fingerprint = _content_fingerprint(owner, text)
        memory_ref = _memory_ref(memory.get("id"), fingerprint, index)
        matched = by_fingerprint.get(fingerprint, [])
        packet = {
            "memory_ref": memory_ref,
            "category": category[:32],
            "content_fingerprint": fingerprint,
            "matches_note_count": len(matched),
        }
        prohibited_memories.append(packet)
        actions.append(_preview_action(
            "archive_prohibited_todo_memory",
            memory_ref,
            affected_refs=[memory_ref],
            review_required=True,
        ))
        memory_done = _memory_done_state(memory)
        if matched:
            if memory_done is not None:
                mismatches = [item for item in matched if item["done"] is not memory_done]
                if mismatches:
                    conflict_ref = _group_ref("memory-completion", memory_ref)
                    item_refs = [item["item_ref"] for item in mismatches]
                    memory_completion_conflicts.append({
                        "conflict_ref": conflict_ref,
                        "memory_ref": memory_ref,
                        "item_refs": item_refs,
                        "memory_done": memory_done,
                        "note_done_states": sorted({item["done"] for item in mismatches}),
                    })
                    actions.append(_preview_action(
                        "review_memory_note_completion_mismatch",
                        conflict_ref,
                        affected_refs=[memory_ref, *item_refs],
                        review_required=True,
                    ))
        else:
            candidate_ref = _group_ref("memory-only", memory_ref)
            memory_only_candidates.append({
                "candidate_ref": candidate_ref,
                "memory_ref": memory_ref,
                "content_fingerprint": fingerprint,
            })
            actions.append(_preview_action(
                "review_missing_note_candidate",
                candidate_ref,
                affected_refs=[memory_ref],
                review_required=True,
            ))

    digest_projection: dict[str, Any] = {}
    from src.builtin_actions import _todo_digest_from_notes

    _todo_digest_from_notes(
        scoped_notes,
        owner=owner,
        limit=digest_limit,
        projection=digest_projection,
    )
    included_refs = set(digest_projection.get("included_item_refs") or ())
    open_stable = [
        item for item in note_items if not item["done"] and item["stable_identity"]
    ]
    digest_limit_exclusions = [
        {
            "item_ref": item["item_ref"],
            "list_ref": item["list_ref"],
            "reason": "outside_current_digest_projection",
        }
        for item in open_stable
        if item["item_ref"] not in included_refs
    ]
    digest_done_inclusions = [
        {
            "item_ref": item["item_ref"],
            "list_ref": item["list_ref"],
            "reason": "completed_item_in_digest_projection",
        }
        for item in note_items
        if item["done"] and item["item_ref"] in included_refs
    ]
    legacy_open_items = sum(
        1
        for item in note_items
        if not item["done"] and not item["stable_identity"]
    )

    drift_count = (
        len(duplicate_groups)
        + len(completion_conflicts)
        + len(prohibited_memories)
        + len(memory_only_candidates)
        + len(memory_completion_conflicts)
        + len(digest_done_inclusions)
        + malformed_items
    )
    redacted_body = {
        "schema": TODO_STATE_DRIFT_SCHEMA,
        "status": "drift_detected" if drift_count else "consistent",
        "owner_scoped": True,
        "read_only": True,
        "dry_run": True,
        "mutations_performed": False,
        "counts": {
            "notes": len(scoped_notes),
            "todo_items": len(note_items),
            "open_todo_items": sum(not item["done"] for item in note_items),
            "legacy_items_without_stable_id": sum(
                not item["stable_identity"] for item in note_items
            ),
            "malformed_items": malformed_items,
            "scoped_memories": len(scoped_memories),
            "prohibited_todo_memories": len(prohibited_memories),
            "duplicate_groups": len(duplicate_groups),
            "completion_conflicts": len(completion_conflicts),
            "memory_completion_conflicts": len(memory_completion_conflicts),
            "memory_only_candidates": len(memory_only_candidates),
            "digest_included_stable_items": len(included_refs),
            "digest_limit_exclusions": len(digest_limit_exclusions),
            "digest_done_inclusions": len(digest_done_inclusions),
            "digest_unverifiable_legacy_open_items": legacy_open_items,
        },
        "drift": {
            "duplicate_groups": duplicate_groups,
            "completion_conflicts": completion_conflicts,
            "memory_completion_conflicts": memory_completion_conflicts,
            "prohibited_todo_memories": prohibited_memories,
            "memory_only_candidates": memory_only_candidates,
            "digest_limit_exclusions": digest_limit_exclusions,
            "digest_done_inclusions": digest_done_inclusions,
        },
        "digest_projection_ref": digest_projection.get("projection_ref"),
        "repair_preview": {
            "schema": TODO_STATE_REPAIR_PREVIEW_SCHEMA,
            "status": "review_required" if actions else "no_repairs_proposed",
            "actions": actions,
            "action_count": len(actions),
            "apply_supported": False,
            "mutations_performed": False,
            "required_live_gate": TODO_DATA_REPAIR_LIVE_GATE,
        },
        "privacy": {
            "raw_content_visible": False,
            "direct_owner_id_visible": False,
            "direct_memory_ids_visible": False,
            "content_fingerprints_domain_separated": True,
        },
        "raw_content_visible": False,
    }
    audit_digest = hashlib.sha256(
        json.dumps(redacted_body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32]
    redacted_body["audit_ref"] = f"todo-state-audit:v1:{audit_digest}"

    if include_review_details:
        redacted_body["operator_review"] = {
            "status": "operator_authorized_ephemeral_review",
            "not_for_persistence": True,
            "raw_content_visible": True,
            "notes": [
                {
                    "list_ref": item["list_ref"],
                    "item_ref": item["item_ref"],
                    "text": item["text"],
                    "done": item["done"],
                }
                for item in note_items
            ],
            "prohibited_memories": [
                {
                    "memory_ref": _memory_ref(
                        memory.get("id"),
                        _content_fingerprint(owner, memory.get("text")),
                        index,
                    ),
                    "text": str(memory.get("text") or ""),
                    "category": str(memory.get("category") or "fact"),
                }
                for index, memory in enumerate(scoped_memories)
                if is_todo_memory_payload(
                    text=str(memory.get("text") or ""),
                    category=str(memory.get("category") or "fact"),
                )
            ],
        }
        redacted_body["raw_content_visible"] = True
    return redacted_body


def audit_todo_state_files(
    *,
    owner: str,
    database_path: str | Path,
    memory_path: str | Path,
    digest_limit: int = 20,
    include_review_details: bool = False,
    operator_authorized: bool = False,
) -> dict[str, Any]:
    """Read local sources without creating or changing either source file."""
    notes = read_notes_sqlite_read_only(database_path)
    memories = read_memory_json_read_only(memory_path)
    return audit_todo_state_drift(
        owner=owner,
        notes=notes,
        memories=memories,
        digest_limit=digest_limit,
        include_review_details=include_review_details,
        operator_authorized=operator_authorized,
    )


def read_notes_sqlite_read_only(database_path: str | Path) -> list[SimpleNamespace]:
    path = Path(database_path).expanduser().resolve()
    if not path.is_file():
        raise TodoStateDriftAuditError("Notes database does not exist")
    uri = path.as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise TodoStateDriftAuditError("Notes database could not be opened read-only") from exc
    try:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id, owner, title, items, note_type, archived, pinned, due_date, updated_at "
            "FROM notes"
        ).fetchall()
        return [SimpleNamespace(**dict(row)) for row in rows]
    except sqlite3.Error as exc:
        raise TodoStateDriftAuditError("Notes table could not be read") from exc
    finally:
        connection.close()


def read_memory_json_read_only(memory_path: str | Path) -> list[dict[str, Any]]:
    path = Path(memory_path).expanduser().resolve()
    if not path.exists():
        return []
    if not path.is_file():
        raise TodoStateDriftAuditError("Memory path is not a file")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TodoStateDriftAuditError("Memory JSON could not be read") from exc
    if not isinstance(payload, list):
        raise TodoStateDriftAuditError("Memory JSON must contain a list")
    return [dict(item) for item in payload if isinstance(item, Mapping)]


def _note_item_records(owner: str, notes: Iterable[SimpleNamespace]) -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    malformed = 0
    for note in notes:
        if note.note_type != "checklist":
            continue
        list_ref = make_list_ref(owner, str(note.id))
        try:
            items = json.loads(note.items or "[]")
        except (TypeError, json.JSONDecodeError):
            malformed += 1
            continue
        if not isinstance(items, list):
            malformed += 1
            continue
        for index, item in enumerate(items):
            if not isinstance(item, Mapping):
                malformed += 1
                continue
            text = str(item.get("text") or "")
            item_id = str(item.get("id") or "").strip()
            stable = bool(_ITEM_ID_RE.fullmatch(item_id))
            item_ref = (
                f"todo-item:v1:{item_id}"
                if stable
                else f"todo-legacy-item:v1:{_short_hash(f'{list_ref}:{index}') }"
            )
            records.append({
                "list_ref": list_ref,
                "item_ref": item_ref,
                "stable_identity": stable,
                "text": text,
                "fingerprint": _content_fingerprint(owner, text),
                "done": bool(item.get("done")),
            })
    return records, malformed


def _note_view(note: Any) -> SimpleNamespace:
    return SimpleNamespace(
        id=str(_get(note, "id", "") or ""),
        owner=str(_get(note, "owner", "") or ""),
        title=str(_get(note, "title", "") or ""),
        items=_get(note, "items", None),
        note_type=str(_get(note, "note_type", "note") or "note"),
        archived=bool(_get(note, "archived", False)),
        pinned=bool(_get(note, "pinned", False)),
        due_date=_get(note, "due_date", None),
        updated_at=_get(note, "updated_at", None),
        label=_get(note, "label", None),
    )


def _get(value: Any, key: str, default: Any) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _memory_done_state(memory: Mapping[str, Any]) -> bool | None:
    metadata = memory.get("metadata") if isinstance(memory.get("metadata"), Mapping) else {}
    for container in (memory, metadata):
        for key in ("done", "completed"):
            value = container.get(key)
            if value is True or value is False:
                return value
        status = str(container.get("status") or "").strip().lower()
        if status in {"done", "completed", "closed"}:
            return True
        if status in {"open", "pending", "active"}:
            return False
    return None


def _preview_action(
    action: str,
    target_ref: str,
    *,
    affected_refs: Iterable[str],
    review_required: bool,
) -> dict[str, Any]:
    return {
        "action": action,
        "target_ref": target_ref,
        "affected_refs": list(dict.fromkeys(affected_refs)),
        "status": "preview_only",
        "review_required": review_required,
        "apply_supported": False,
        "required_live_gate": TODO_DATA_REPAIR_LIVE_GATE,
    }


def _require_owner(owner: Any) -> str:
    text = str(owner or "").strip()
    if not text or len(text) > 512:
        raise TodoStateDriftAuditError("owner scope is required")
    return text


def _content_fingerprint(owner: str, value: Any) -> str:
    normalized = normalize_todo_match_text(str(value or ""))
    digest = hashlib.sha256(
        f"todo-drift-content:v1\0{owner}\0{normalized}".encode("utf-8")
    ).hexdigest()[:32]
    return f"todo-content-fingerprint:v1:{digest}"


def _memory_ref(memory_id: Any, fingerprint: str, index: int) -> str:
    material = str(memory_id or "") or f"{fingerprint}:{index}"
    return f"todo-memory-ref:v1:{_short_hash(material)}"


def _group_ref(kind: str, material: str) -> str:
    return f"todo-drift-group:v1:{kind}:{_short_hash(material)}"


def _short_hash(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:24]


__all__ = [
    "TODO_DATA_REPAIR_LIVE_GATE",
    "TODO_STATE_DRIFT_SCHEMA",
    "TODO_STATE_REPAIR_PREVIEW_SCHEMA",
    "TodoStateDriftAuditError",
    "audit_todo_state_drift",
    "audit_todo_state_files",
    "read_memory_json_read_only",
    "read_notes_sqlite_read_only",
]
