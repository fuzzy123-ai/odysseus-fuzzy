from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from src.builtin_actions import _todo_digest_selection_from_notes
from src.todo_state_drift_audit import TodoStateDriftAuditError, audit_todo_state_drift


def note(note_id: str, owner: str, items, **extra):
    return SimpleNamespace(id=note_id, owner=owner, title=extra.get("title", "private title"),
        items=json.dumps(items), note_type=extra.get("note_type", "checklist"),
        archived=extra.get("archived", False), pinned=extra.get("pinned", False),
        due_date=None, updated_at=extra.get("updated_at", "2026-01-01T00:00:00"), label=None)


def item(identifier: str | None, text: str, done=False):
    value = {"text": text, "done": done}
    if identifier is not None: value["id"] = identifier
    return value


def test_report_is_redacted_deterministic_non_applying_and_sources_unchanged(tmp_path):
    database = tmp_path / "notes.sqlite"; memory = tmp_path / "memory.json"
    con = sqlite3.connect(database); con.execute("CREATE TABLE notes (id, owner, title, items, note_type, archived, pinned, due_date, updated_at, label)")
    con.execute("INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("list-a", "alice-secret", "Sensitive title", json.dumps([item("abcd1234", "Private phrase"), item("efgh1234", "Private phrase")]), "checklist", 0, 0, None, "2026-01-01", None)); con.commit(); con.close()
    memory.write_text(json.dumps([{"owner": "alice-secret", "id": "m1", "category": "todo", "text": "Private phrase"}]), encoding="utf-8")
    before = (hashlib.sha256(database.read_bytes()).hexdigest(), hashlib.sha256(memory.read_bytes()).hexdigest())
    from src.todo_state_drift_audit import audit_todo_state_files
    first = audit_todo_state_files(owner="alice-secret", database_path=database, memory_path=memory)
    second = audit_todo_state_files(owner="alice-secret", database_path=database, memory_path=memory)
    rendered = json.dumps(first)
    assert first == second and first["status"] == "drift_detected"
    assert "Private phrase" not in rendered and "Sensitive title" not in rendered and "alice-secret" not in rendered
    assert all(action["preview_only"] and action["review_required"] and not action["apply_supported"] and not action["mutations_performed"] for action in first["repair_preview"]["actions"])
    assert before == (hashlib.sha256(database.read_bytes()).hexdigest(), hashlib.sha256(memory.read_bytes()).hexdigest())


def test_exact_owner_isolation_and_cross_owner_records_never_become_truth():
    report = audit_todo_state_drift(owner="alice", notes=[note("a", "alice", [item("abcd1234", "alice item")]), note("b", "bob", [item("efgh1234", "bob item")])],
        memories=[{"owner": "bob", "category": "todo", "text": "bob item"}, {"owner": None, "category": "todo", "text": "orphan"}])
    assert report["status"] == "consistent" and report["counts"]["notes"] == 1 and report["counts"]["prohibited_memories"] == 0
    assert "bob" not in json.dumps(report)


def test_digest_selector_parity_keeps_pinned_non_checklist_in_limit_position():
    pinned = note("pin", "alice", [], note_type="note", pinned=True, title="Pinned private", updated_at="2026-02-01")
    checklist = note("list", "alice", [item(f"a00000{i:02d}", f"task {i}") for i in range(20)], updated_at="2026-01-01")
    direct = _todo_digest_selection_from_notes([pinned, checklist], limit=20)
    report = audit_todo_state_drift(owner="alice", notes=[pinned, checklist], memories=[])
    assert len(direct["open_items"][:20]) == 20 and direct["open_items"][0]["list_ref"] is None
    assert report["digest"]["selected_count"] == 20 and report["digest"]["limit_exclusion_count"] == 1 and report["status"] == "consistent"


def test_malformed_legacy_duplicate_and_hostile_records_fail_closed_or_report_drift():
    legacy_and_duplicate = note("list", "alice", [item(None, "same"), item("abcd1234", "same"), item("abcd1234", "other"), {"text": "bad", "done": "no"}])
    report = audit_todo_state_drift(owner="alice", notes=[legacy_and_duplicate], memories=[])
    assert report["status"] == "drift_detected" and report["counts"]["legacy"] == 1 and report["counts"]["duplicate_ids"] == 1 and report["counts"]["malformed"] == 1
    class Hostile:
        @property
        def owner(self): raise RuntimeError("private")
    with pytest.raises(TodoStateDriftAuditError): audit_todo_state_drift(owner="alice", notes=[Hostile()], memories=[])


def test_exact_review_requires_dual_flag_and_is_ephemeral_only():
    records = [note("list", "alice", [item("abcd1234", "private exact")])]
    with pytest.raises(TodoStateDriftAuditError): audit_todo_state_drift(owner="alice", notes=records, memories=[], include_review_details=True)
    report = audit_todo_state_drift(owner="alice", notes=records, memories=[], include_review_details=True, operator_authorized=True)
    assert report["raw_content_visible"] is False and report["operator_review"]["not_for_persistence"] is True and report["operator_review"]["raw_content_visible"] is True


def test_cli_requires_explicit_paths_has_no_apply_and_uses_exit_codes(tmp_path):
    script = Path(__file__).parents[1] / "scripts" / "audit_todo_state_drift.py"
    missing = subprocess.run([sys.executable, str(script), "--owner", "alice"], text=True, capture_output=True)
    blocked = subprocess.run([sys.executable, str(script), "--owner", "alice", "--database", str(tmp_path / "absent.db"), "--memory-file", str(tmp_path / "absent.json")], text=True, capture_output=True)
    assert missing.returncode == 2 and "--database" in missing.stderr and "--apply" not in script.read_text(encoding="utf-8")
    assert blocked.returncode == 2 and json.loads(blocked.stdout)["status"] == "blocked"


def test_file_backed_sqlite_integer_pinned_consumes_digest_position_and_snapshot_is_safe(tmp_path):
    database = tmp_path / "notes.sqlite"; memory = tmp_path / "memory.json"
    con = sqlite3.connect(database); con.execute("CREATE TABLE notes (id, owner, title, items, note_type, archived, pinned, due_date, updated_at, label)")
    con.execute("INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("pin", "alice", "pin", None, "note", 0, 1, None, "2026-02-01", None))
    con.execute("INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("list", "alice", "list", json.dumps([item(f"a00000{i:02d}", f"task {i}") for i in range(20)]), "checklist", 0, 0, None, "2026-01-01", None)); con.commit(); con.close()
    memory.write_text("[]", encoding="utf-8")
    from src.todo_state_drift_audit import audit_todo_state_files
    report = audit_todo_state_files(owner="alice", database_path=database, memory_path=memory)
    assert report["status"] == "consistent" and report["digest"]["selected_count"] == 20 and report["digest"]["limit_exclusion_count"] == 1
    assert report["digest"]["manifest"][0]["list_ref"] is not None and "alice" not in report["source_snapshot_ref"]


def test_item_identity_is_per_list_and_prefix_memory_matching_is_explicit_only():
    first = note("list-a", "alice", [item("abcd1234", "same task")])
    second = note("list-b", "alice", [item("abcd1234", "other task")])
    report = audit_todo_state_drift(owner="alice", notes=[first, second], memories=[{"owner": "alice", "category": "fact", "text": "Todo: same task"}])
    assert report["counts"]["duplicate_ids"] == 0 and report["counts"]["memory_only_candidates"] == 0
    assert report["digest"]["manifest"][0]["list_ref"] != report["digest"]["manifest"][1]["list_ref"]
    alias = audit_todo_state_drift(owner="alice", notes=[first], memories=[{"owner": "alice", "category": "todo", "text": "Todo: same task"}])
    assert alias["counts"]["memory_only_candidates"] == 1


def test_invalid_owner_malformed_memory_source_injected_snapshot_and_oversized_identity_fail_closed(tmp_path):
    with pytest.raises(TodoStateDriftAuditError): audit_todo_state_drift(owner=" alice ", notes=[], memories=[])
    with pytest.raises(TodoStateDriftAuditError): audit_todo_state_drift(owner="alice", notes=[], memories=[], source_snapshot_ref="private source")
    oversized = note("x" * 513, "alice", [item("abcd1234", "task")])
    assert audit_todo_state_drift(owner="alice", notes=[oversized], memories=[])["status"] == "drift_detected"
    memory = tmp_path / "memory.json"; memory.write_text(json.dumps([{"owner": "alice", "category": "fact", "text": "ok"}, "malformed"]), encoding="utf-8")
    from src.todo_state_drift_audit import read_memory_json_read_only
    with pytest.raises(TodoStateDriftAuditError): read_memory_json_read_only(memory, owner="alice")


def test_archived_notes_are_excluded_and_empty_ids_flags_and_unsafe_completeness_are_reviewed():
    archived = note("old", "alice", [{"text": "ignored", "done": "bad"}], archived=True)
    healthy = note("live", "alice", [item("abcd1234", "live")])
    assert audit_todo_state_drift(owner="alice", notes=[archived, healthy], memories=[])["status"] == "consistent"
    empty_checklist = note("empty", "alice", [])
    empty_checklist.items = None
    assert audit_todo_state_drift(owner="alice", notes=[empty_checklist], memories=[])["status"] == "consistent"
    missing_done = note("open", "alice", [{"id": "abcd1234", "text": "open item"}])
    missing_done_report = audit_todo_state_drift(owner="alice", notes=[missing_done], memories=[])
    assert missing_done_report["status"] == "consistent" and missing_done_report["digest"]["selected_count"] == 1
    unsafe = note("unsafe", "alice", [item("", "empty id")])
    report = audit_todo_state_drift(owner="alice", notes=[unsafe], memories=[])
    assert report["status"] == "drift_detected" and report["complete"] is False and report["repair_preview"]["complete"] is False
    assert report["counts"]["malformed"] == 1 and report["repair_preview"]["action_count"] >= 1
    assert report["repair_preview"]["status"] != "none"
    invalid_archive = note("flag", "alice", [item("abcd1234", "flag")], archived="invalid")
    invalid_archive_report = audit_todo_state_drift(owner="alice", notes=[healthy, invalid_archive], memories=[])
    healthy_report = audit_todo_state_drift(owner="alice", notes=[healthy], memories=[])
    assert invalid_archive_report["complete"] is False and invalid_archive_report["source_snapshot_ref"] != healthy_report["source_snapshot_ref"]
    other_invalid_archive = note("flag", "alice", [item("abcd1234", "flag")], archived=3)
    assert audit_todo_state_drift(owner="alice", notes=[healthy, other_invalid_archive], memories=[])["source_snapshot_ref"] != invalid_archive_report["source_snapshot_ref"]
    with pytest.raises(TodoStateDriftAuditError): audit_todo_state_drift(owner="a" * 257, notes=[], memories=[])
    unknown_type = note("weird", "alice", [], note_type="unknown")
    assert audit_todo_state_drift(owner="alice", notes=[unknown_type], memories=[])["complete"] is False
    blank_memory = audit_todo_state_drift(owner="alice", notes=[healthy], memories=[{"owner": "alice", "category": "todo", "text": "  "}])
    assert blank_memory["complete"] is False and blank_memory["repair_preview"]["status"] == "review_required" and blank_memory["counts"]["prohibited_memories"] == 0
    with pytest.raises(TodoStateDriftAuditError): audit_todo_state_drift(owner="alice", notes=[], memories=[], include_review_details=1, operator_authorized=True)
    review = audit_todo_state_drift(owner="alice", notes=[healthy], memories=[{"owner": "alice", "id": "m1", "category": "todo", "text": "live"}], include_review_details=True, operator_authorized=True)
    assert review["operator_review"]["prohibited_memories"][0]["text"] == "live" and review["raw_content_visible"] is False
