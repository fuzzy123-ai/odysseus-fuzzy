"""Bounded, read-only Todo state drift audit and non-applying repair preview.

This module deliberately has no database model dependency and never opens a
write-capable connection.  Its public pure function accepts small, synthetic
record objects so its privacy and owner-boundary rules can be tested directly.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace
from typing import Any

from src.memory_category_policy import TODO_ALIASES
from src.todo_digest_receipts import redact_ref

SCHEMA = "odysseus.todo_state_drift_audit.v2"
PREVIEW_SCHEMA = "odysseus.todo_state_repair_preview.v1"
LIVE_GATE = "TTD-LIVE-DATA-REPAIR"
MAX_RECORDS = 1000
MAX_ITEMS_PER_NOTE = 500
MAX_ACTIONS = 500
MAX_TEXT = 4096
MAX_ITEM_TEXT = 5000
MAX_SOURCE_BYTES = 50 * 1024 * 1024
_SHORT_ID = re.compile(r"^[a-z0-9]{8}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_TODO_PREFIX = re.compile(r"^\s*(?:todo|task|checklist|aufgabe)\s*[:\-]\s*(.+)$", re.I)
_SNAPSHOT_REF = re.compile(r"^source_snapshot:sha256:[a-f0-9]{64}$")
_MANIFEST_LIST_REF = re.compile(r"^list:[0-9a-f]{16}$")
_MANIFEST_ITEM_REF = re.compile(r"^item:[0-9a-f]{16}$")


class TodoStateDriftAuditError(ValueError):
    """A source or pure input was not safe to audit."""


def audit_todo_state_drift(*, owner: str, notes: Iterable[Any], memories: Iterable[Any],
                           digest_limit: int = 20, include_review_details: bool = False,
                           operator_authorized: bool = False,
                           source_snapshot_ref: str | None = None) -> dict[str, Any]:
    """Return a content-free audit; never mutates supplied records or sources."""
    _owner(owner)
    if type(include_review_details) is not bool or type(operator_authorized) is not bool:
        raise TodoStateDriftAuditError("review_flags_invalid")
    if source_snapshot_ref is not None and (not isinstance(source_snapshot_ref, str) or not _SNAPSHOT_REF.fullmatch(source_snapshot_ref)):
        raise TodoStateDriftAuditError("snapshot_ref_invalid")
    if type(digest_limit) is not int or not 1 <= digest_limit <= 20:
        raise TodoStateDriftAuditError("invalid_digest_limit")
    if include_review_details is not operator_authorized:
        raise TodoStateDriftAuditError("review_authorization_required")
    note_rows = _bounded_iterable(notes, "notes")
    memory_rows = _bounded_iterable(memories, "memories")
    note_views = [_note(record) for record in note_rows]
    memory_views = [_memory(record) for record in memory_rows]
    scoped_notes = [record for record in note_views if record.owner == owner]
    scoped_memories = [record for record in memory_views if record.get("owner") == owner]
    # SQL uses the same leading Digest order; id makes ties deterministic.
    # Stable passes express pinned DESC, updated_at DESC, id ASC exactly.
    scoped_notes.sort(key=lambda n: n.note_id)
    scoped_notes.sort(key=lambda n: _sort_time(n.updated_at), reverse=True)
    scoped_notes.sort(key=lambda n: n.pinned, reverse=True)

    # Bind every exact-owner row that can influence safety/status, including
    # invalid archive flags and selector-unsafe rows, before any projection.
    snapshot = source_snapshot_ref or _snapshot_from_records(owner, scoped_notes, scoped_memories)

    # Audit only active Notes.  Owner-local rows are first validated so an
    # invalid archived flag cannot be silently treated as active or inactive.
    active_notes = [note for note in scoped_notes if note.archived_valid and not note.archived]
    invalid_note_count = sum(not _safe_note_for_selector(note) for note in active_notes)
    invalid_archive_count = sum(not note.archived_valid for note in scoped_notes)
    selector_notes = [note for note in active_notes if _safe_note_for_selector(note)]
    item_rows: list[dict[str, Any]] = []
    bad_note_count = legacy_count = duplicate_id_count = malformed_memory_count = 0
    for note_index, note in enumerate(selector_notes):
        if not _safe_note_identity(note):
            continue
        if note.note_type != "checklist":
            continue
        parsed, problem = _items(note.items)
        if problem:
            bad_note_count += 1
            continue
        seen_ids: set[str] = set()
        for index, item in enumerate(parsed):
            if not isinstance(item, Mapping):
                bad_note_count += 1
                continue
            text = item.get("text")
            done = item.get("done", False)
            raw_id = item.get("id")
            if (not isinstance(text, str) or not text.strip() or len(text) > MAX_ITEM_TEXT
                    or ("done" in item and type(done) is not bool)):
                bad_note_count += 1
                continue
            legacy = raw_id is None
            if legacy:
                legacy_count += 1
                identity = f"legacy:{note.note_id}:{index}"
            elif not isinstance(raw_id, str) or not (_SHORT_ID.fullmatch(raw_id) or _UUID.fullmatch(raw_id)):
                bad_note_count += 1
                continue
            else:
                identity = raw_id
                if raw_id in seen_ids:
                    duplicate_id_count += 1
                seen_ids.add(raw_id)
            digest_list_ref = redact_ref("list", note.note_id)
            digest_item_ref = redact_ref("item", raw_id if not legacy else f"legacy:{note_index}:{index}")
            if digest_list_ref is None or digest_item_ref is None:
                bad_note_count += 1
                continue
            item_rows.append({"note": note, "note_index": note_index, "index": index, "item_id": identity, "legacy": legacy,
                              "text": text, "done": done, "list_ref": _ref(owner, "list", note.note_id),
                              "item_ref": _ref(owner, "item", note.note_id + "\0" + identity),
                              "digest_list_ref": digest_list_ref, "digest_item_ref": digest_item_ref,
                              "match": _normalized_text(text)})

    # Run the accepted selector against the same owner-scoped snapshot.  This
    # call is projection-only and supplies the current manifest refs/order.
    try:
        from src.builtin_actions import _todo_digest_selection_from_notes
        selection = _todo_digest_selection_from_notes(selector_notes, limit=digest_limit)
        selected = list(selection.get("open_items", ()))[:digest_limit]
        if not isinstance(selected, list) or any(not isinstance(x, Mapping) for x in selected):
            raise ValueError
    except Exception:
        return _blocked_report(snapshot, "digest_projection_unavailable")
    projection_bad = False
    selected_refs: set[tuple[str, str]] = set()
    for entry in selected:
        try:
            manifest_list_ref = entry.get("manifest_list_ref")
            manifest_item_ref = entry.get("manifest_item_ref")
        except Exception:
            projection_bad = True
            break
        if (not isinstance(manifest_list_ref, str) or not isinstance(manifest_item_ref, str)
                or not _MANIFEST_LIST_REF.fullmatch(manifest_list_ref)
                or not _MANIFEST_ITEM_REF.fullmatch(manifest_item_ref)):
            projection_bad = True
            break
        selected_refs.add((manifest_list_ref, manifest_item_ref))
    if projection_bad:
        selected_refs.clear()
    completed_selected = any(
        row["done"] and (row["digest_list_ref"], row["digest_item_ref"]) in selected_refs for row in item_rows
    )

    actions: list[dict[str, Any]] = []
    by_text: dict[str, list[dict[str, Any]]] = {}
    for row in item_rows:
        by_text.setdefault(row["match"], []).append(row)
    duplicate_text_groups = 0
    for group in by_text.values():
        if len(group) > 1:
            duplicate_text_groups += 1
            _action(actions, "dedupe_review", _group_ref(owner, "duplicate", *(x["item_ref"] for x in group)))
    for row in item_rows:
        if row["legacy"]:
            _action(actions, "legacy_review", row["item_ref"])
    todo_memories = 0
    memory_only = completion_mismatch = 0
    for index, memory in enumerate(scoped_memories):
        category = memory.get("category")
        text = memory.get("text")
        if not isinstance(text, str) or len(text) > MAX_TEXT:
            # A malformed owner-scoped memory prevents consistency, but is not
            # promoted to Todo truth or a deletion candidate.
            malformed_memory_count += 1
            continue
        if not text.strip():
            malformed_memory_count += 1
            continue
        category_alias = isinstance(category, str) and category.strip().lower() in TODO_ALIASES
        prefix = None if category_alias else _TODO_PREFIX.match(text)
        if not category_alias and not prefix:
            continue
        todo_memories += 1
        mem_ref = _ref(owner, "memory", str(index) + "\0" + (memory.get("id") if isinstance(memory.get("id"), str) else ""))
        _action(actions, "archive_prohibited_memory", mem_ref)
        memory_done, completion_safe = _completion(memory)
        if not completion_safe:
            malformed_memory_count += 1
        matches = by_text.get(_normalized_text(prefix.group(1) if prefix else text), [])
        if not matches:
            memory_only += 1
            _action(actions, "missing_note_candidate", mem_ref)
        elif completion_safe:
            if memory_done is not None and any(x["done"] != memory_done for x in matches):
                completion_mismatch += 1
                _action(actions, "completion_review", mem_ref)

    for number in range(duplicate_id_count):
        _action(actions, "malformed_review", _group_ref(owner, "duplicate_id", str(number)))
    for number in range(bad_note_count + invalid_note_count + invalid_archive_count):
        _action(actions, "malformed_review", _group_ref(owner, "malformed_note", str(number)))
    for number in range(malformed_memory_count):
        _action(actions, "malformed_review", _group_ref(owner, "malformed_memory", str(number)))
    if projection_bad or completed_selected:
        _action(actions, "digest_projection_review", _group_ref(owner, "digest_projection", "unsafe"))

    limit_excluded = sum(1 for row in item_rows if not row["done"] and
                         (row["digest_list_ref"], row["digest_item_ref"]) not in selected_refs)
    unsafe = invalid_note_count or invalid_archive_count or bad_note_count or malformed_memory_count or duplicate_id_count or legacy_count or projection_bad or completed_selected
    drift = unsafe or duplicate_text_groups or todo_memories or completion_mismatch
    complete = not bool(unsafe)
    body = {
        "schema": SCHEMA, "status": "drift_detected" if drift else "consistent",
        "read_only": True, "mutations_performed": False, "owner_scoped": True,
        "raw_content_visible": False, "complete": complete, "truncated": False,
        "source_snapshot_ref": snapshot,
        "digest": {"selector": "builtin_default", "limit": digest_limit,
                   "selected_count": len(selected), "limit_exclusion_count": limit_excluded,
                   "projection_valid": not projection_bad and not completed_selected and not invalid_note_count
                   and not invalid_archive_count and not bad_note_count,
                   "manifest": ([] if projection_bad else [{"position": position, "list_ref": entry["manifest_list_ref"],
                                 "item_ref": entry["manifest_item_ref"]}
                                for position, entry in enumerate(selected)])},
        "counts": {"notes": len(active_notes), "items": len(item_rows), "malformed": bad_note_count,
                   "invalid_note_identities": invalid_note_count, "invalid_archive_flags": invalid_archive_count,
                   "malformed_memories": malformed_memory_count,
                   "legacy": legacy_count, "duplicate_ids": duplicate_id_count,
                   "duplicate_text_groups": duplicate_text_groups, "prohibited_memories": todo_memories,
                   "memory_only_candidates": memory_only, "completion_mismatches": completion_mismatch},
        "repair_preview": {"schema": PREVIEW_SCHEMA, "status": "review_required" if actions else "none",
                           "action_count": len(actions), "actions": actions, "preview_only": True,
                           "apply_supported": False, "mutations_performed": False,
                           "required_live_gate": LIVE_GATE, "source_snapshot_ref": snapshot,
                           "preview_ref": "", "complete": complete, "truncated": False},
    }
    body["repair_preview"]["preview_ref"] = _digest("preview", body["repair_preview"])
    body["audit_ref"] = _digest("audit", body)
    if include_review_details:
        body["operator_review"] = {"not_for_persistence": True, "ephemeral": True,
                                   "raw_content_visible": True,
                                   "items": [{"list_ref": x["list_ref"], "item_ref": x["item_ref"], "text": x["text"], "done": x["done"]} for x in item_rows],
                                   "prohibited_memories": [{"memory_ref": _ref(owner, "memory", str(index) + "\0" + (memory.get("id") if isinstance(memory.get("id"), str) else "")),
                                                              "id": memory.get("id"), "text": memory.get("text"), "category": memory.get("category")}
                                                             for index, memory in enumerate(scoped_memories)
                                                             if _is_prohibited_memory(memory)]}
    return body


def audit_todo_state_files(*, owner: str, database_path: str | Path, memory_path: str | Path,
                           digest_limit: int = 20, include_review_details: bool = False,
                           operator_authorized: bool = False) -> dict[str, Any]:
    _owner(owner)
    memory_bytes = _read_bytes(memory_path, "memory")
    notes = read_notes_sqlite_read_only(database_path, owner=owner)
    memories = _parse_memory_bytes(memory_bytes, owner=owner)
    snapshot = _snapshot_from_file_rows(owner, notes, memory_bytes)
    return audit_todo_state_drift(owner=owner, notes=notes, memories=memories, digest_limit=digest_limit,
                                  include_review_details=include_review_details, operator_authorized=operator_authorized,
                                  source_snapshot_ref=snapshot)


def read_notes_sqlite_read_only(database_path: str | Path, *, owner: str) -> list[SimpleNamespace]:
    _owner(owner)
    path = _file(database_path, "database")
    try:
        if path.stat().st_size > MAX_SOURCE_BYTES or Path(str(path) + "-wal").exists() or Path(str(path) + "-shm").exists():
            raise TodoStateDriftAuditError("database_snapshot_unavailable")
    except OSError:
        raise TodoStateDriftAuditError("database_unavailable") from None
    try:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        con.execute("PRAGMA query_only = ON")
        rows = con.execute("SELECT id, owner, title, items, note_type, archived, pinned, due_date, updated_at, label "
                           "FROM notes WHERE owner = ? AND archived = 0 "
                           "ORDER BY pinned DESC, updated_at DESC, id ASC LIMIT ?", (owner, MAX_RECORDS + 1)).fetchall()
        if len(rows) > MAX_RECORDS:
            raise TodoStateDriftAuditError("database_budget_exceeded")
        return [SimpleNamespace(id=r[0], owner=r[1], title=r[2], items=r[3], note_type=r[4], archived=r[5], pinned=r[6], due_date=r[7], updated_at=r[8], label=r[9]) for r in rows]
    except (sqlite3.Error, OSError):
        raise TodoStateDriftAuditError("database_unavailable") from None
    finally:
        try: con.close()
        except UnboundLocalError: pass


def read_memory_json_read_only(memory_path: str | Path, *, owner: str) -> list[dict[str, Any]]:
    _owner(owner)
    return _parse_memory_bytes(_read_bytes(memory_path, "memory"), owner=owner)


def _parse_memory_bytes(raw: bytes, *, owner: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise TodoStateDriftAuditError("memory_unavailable") from None
    if not isinstance(payload, list) or len(payload) > MAX_RECORDS:
        raise TodoStateDriftAuditError("memory_unavailable")
    if any(not isinstance(value, Mapping) for value in payload):
        raise TodoStateDriftAuditError("memory_unavailable")
    try:
        values = [dict(value) for value in payload]
    except Exception:
        raise TodoStateDriftAuditError("memory_unavailable") from None
    return [value for value in values if value.get("owner") == owner]


def _blocked_report(snapshot: str | None, reason: str) -> dict[str, Any]:
    body = {"schema": SCHEMA, "status": "blocked", "reason": reason, "read_only": True,
            "mutations_performed": False, "raw_content_visible": False, "complete": False,
            "truncated": False, "source_snapshot_ref": snapshot or "source:unavailable"}
    body["audit_ref"] = _digest("blocked_audit", body)
    return body


def _action(actions: list[dict[str, Any]], action: str, target_ref: str) -> None:
    if len(actions) >= MAX_ACTIONS: raise TodoStateDriftAuditError("action_budget_exceeded")
    actions.append({"action": action, "target_ref": target_ref, "preview_only": True,
                    "review_required": True, "apply_supported": False, "mutations_performed": False,
                    "required_live_gate": LIVE_GATE})


def _items(value: Any) -> tuple[list[Any], bool]:
    if value is None or value == "":
        return [], False
    if not isinstance(value, str) or len(value) > MAX_ITEM_TEXT * MAX_ITEMS_PER_NOTE: return [], True
    try: parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError): return [], True
    return (parsed, False) if isinstance(parsed, list) and len(parsed) <= MAX_ITEMS_PER_NOTE else ([], True)


def _note(record: Any) -> SimpleNamespace:
    raw_id = _value(record, "id")
    raw_title = _value(record, "title")
    raw_type = _value(record, "note_type")
    raw_archived = _value(record, "archived")
    raw_pinned = _value(record, "pinned")
    archived, archived_valid = _flag(raw_archived)
    pinned, pinned_valid = _flag(raw_pinned)
    return SimpleNamespace(id=raw_id if isinstance(raw_id, str) else "", note_id=raw_id if isinstance(raw_id, str) else "",
                           raw_id=raw_id, raw_archived=raw_archived, raw_pinned=raw_pinned,
                           owner=_value(record, "owner"), title=raw_title, items=_value(record, "items"),
                           note_type=raw_type, archived=archived, archived_valid=archived_valid,
                           pinned=pinned, pinned_valid=pinned_valid, due_date=_value(record, "due_date"),
                           updated_at=_value(record, "updated_at"), label=_value(record, "label"))


def _memory(record: Any) -> dict[str, Any]:
    if not isinstance(record, Mapping): raise TodoStateDriftAuditError("memory_record_invalid")
    try: return dict(record)
    except Exception: raise TodoStateDriftAuditError("memory_record_invalid") from None


def _value(record: Any, key: str) -> Any:
    try: return record.get(key) if isinstance(record, Mapping) else getattr(record, key, None)
    except Exception: raise TodoStateDriftAuditError("hostile_record") from None


def _string(record: Any, key: str) -> str:
    value = _value(record, key)
    return value if isinstance(value, str) else ""


def _flag(value: Any) -> tuple[bool, bool]:
    if type(value) is bool:
        return value, True
    if type(value) is int and value in (0, 1):
        return bool(value), True
    # SQL NULL is the only documented conservative fallback: treat it as not
    # archived/not pinned but mark it visible as an unsafe source condition.
    if value is None:
        return False, False
    return False, False


def _exact_owner(record: Any, owner: str) -> bool:
    return _value(record, "owner") == owner


def _bounded_iterable(value: Iterable[Any], name: str) -> list[Any]:
    if isinstance(value, (str, bytes, Mapping)): raise TodoStateDriftAuditError(name + "_invalid")
    try:
        iterator = iter(value)
        result = []
        for _ in range(MAX_RECORDS + 1):
            try: result.append(next(iterator))
            except StopIteration: break
    except Exception: raise TodoStateDriftAuditError(name + "_invalid") from None
    if len(result) > MAX_RECORDS: raise TodoStateDriftAuditError(name + "_budget_exceeded")
    return result


def _owner(owner: Any) -> None:
    if not isinstance(owner, str) or not owner or owner != owner.strip() or len(owner) > 256: raise TodoStateDriftAuditError("owner_required")


def _file(value: str | Path, name: str) -> Path:
    try: path = Path(value)
    except TypeError: raise TodoStateDriftAuditError(name + "_unavailable") from None
    if not path.is_file(): raise TodoStateDriftAuditError(name + "_unavailable")
    return path


def _read_bytes(value: str | Path, name: str) -> bytes:
    try:
        path = _file(value, name)
        if path.stat().st_size > MAX_SOURCE_BYTES: raise TodoStateDriftAuditError(name + "_unavailable")
        return path.read_bytes()
    except OSError: raise TodoStateDriftAuditError(name + "_unavailable") from None


def _normalized_text(value: str) -> str: return " ".join(value.split()).casefold()
def _completion(memory: Mapping[str, Any]) -> tuple[bool | None, bool]:
    try:
        metadata = memory.get("metadata")
    except Exception:
        return None, False
    if metadata is not None and not isinstance(metadata, Mapping):
        return None, False
    states: list[bool] = []
    for source in (memory, metadata):
        if source is None:
            continue
        try:
            for key in ("done", "completed"):
                if key in source:
                    value = source.get(key)
                    if type(value) is not bool:
                        return None, False
                    states.append(value)
            if "status" in source:
                status = source.get("status")
                if not isinstance(status, str) or len(status) > 16:
                    return None, False
                if status in {"open", "pending", "active"}:
                    states.append(False)
                elif status in {"done", "completed", "closed"}:
                    states.append(True)
                else:
                    return None, False
        except Exception:
            return None, False
    return (states[0], True) if states and len(set(states)) == 1 else (None, not states)
def _sort_time(value: Any) -> str: return value if isinstance(value, str) else ""
def _ref(owner: str, kind: str, value: str) -> str: return f"{kind}:" + sha256(("ttd06-ref:" + kind + "\0" + owner + "\0" + value).encode()).hexdigest()[:16]
def _group_ref(owner: str, kind: str, *values: str) -> str: return _ref(owner, "group", kind + "\0" + "\0".join(sorted(values)))
def _digest(kind: str, value: Any) -> str: return f"{kind}:sha256:" + sha256(("ttd06:" + kind + "\0" + json.dumps(value, sort_keys=True, separators=(",", ":"))).encode()).hexdigest()
def _snapshot_from_records(owner: str, notes: list[Any], memories: list[Any]) -> str:
    # Raw values are only an input to this domain-separated digest; they never
    # leave this function.  This makes a pure-call preview bind its exact input.
    material = {
        "owner": owner,
        "notes": [{"id": n.note_id, "raw_id": n.raw_id, "title": n.title, "items": n.items, "note_type": n.note_type,
                   "archived": n.archived, "raw_archived": n.raw_archived, "archived_valid": n.archived_valid,
                   "pinned": n.pinned, "raw_pinned": n.raw_pinned, "pinned_valid": n.pinned_valid, "due_date": n.due_date,
                   "updated_at": n.updated_at, "label": n.label} for n in notes],
        "memories": memories,
    }
    try: return _digest("source_snapshot", material)
    except Exception: raise TodoStateDriftAuditError("snapshot_unsafe") from None


def _snapshot_from_file_rows(owner: str, notes: list[Any], memory_bytes: bytes) -> str:
    """Bind the actual owner-scoped SQL result and exact parsed-file bytes."""
    material = {
        "owner": owner,
        "notes": [{"id": n.id, "owner": n.owner, "title": n.title, "items": n.items,
                   "note_type": n.note_type, "archived": n.archived, "pinned": n.pinned,
                   "due_date": n.due_date, "updated_at": n.updated_at, "label": n.label} for n in notes],
        "memory_bytes_sha256": sha256(memory_bytes).hexdigest(),
    }
    try: return _digest("source_snapshot", material)
    except Exception: raise TodoStateDriftAuditError("snapshot_unsafe") from None


def _safe_note_identity(note: SimpleNamespace) -> bool:
    return isinstance(note.note_id, str) and bool(note.note_id) and note.note_id == note.note_id.strip() and len(note.note_id) <= 256


def _safe_note_for_selector(note: SimpleNamespace) -> bool:
    return (_safe_note_identity(note) and (note.title is None or (isinstance(note.title, str) and len(note.title) <= MAX_TEXT))
            and isinstance(note.note_type, str) and note.note_type in {"note", "checklist"}
            and note.pinned_valid and note.archived_valid
            and (note.items is None or (isinstance(note.items, str) and len(note.items) <= MAX_ITEM_TEXT * MAX_ITEMS_PER_NOTE))
            and (note.label is None or (isinstance(note.label, str) and len(note.label) <= 256))
            and (note.due_date is None or (isinstance(note.due_date, str) and len(note.due_date) <= 64))
            and (note.updated_at is None or (isinstance(note.updated_at, str) and len(note.updated_at) <= 64)))


def _is_prohibited_memory(memory: Mapping[str, Any]) -> bool:
    try:
        text = memory.get("text")
        category = memory.get("category")
        return isinstance(text, str) and bool(text.strip()) and len(text) <= MAX_TEXT and (
            (isinstance(category, str) and category.strip().lower() in TODO_ALIASES) or bool(_TODO_PREFIX.match(text))
        )
    except Exception:
        return False


__all__ = ["LIVE_GATE", "SCHEMA", "TodoStateDriftAuditError", "audit_todo_state_drift", "audit_todo_state_files", "read_notes_sqlite_read_only", "read_memory_json_read_only"]
