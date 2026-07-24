from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from scripts.audit_todo_state_drift import build_parser, main
from src.todo_state_drift_audit import (
    TODO_DATA_REPAIR_LIVE_GATE,
    TodoStateDriftAuditError,
    audit_todo_state_drift,
    audit_todo_state_files,
)


def _note(*, owner="alice", note_id="list-alpha", items=None, pinned=False):
    return SimpleNamespace(
        id=note_id,
        owner=owner,
        title="Private list title",
        items=json.dumps(items or []),
        note_type="checklist",
        archived=False,
        pinned=pinned,
        due_date=None,
        updated_at="2026-07-22T06:00:00",
        label=None,
    )


def _fixture_sources():
    notes = [
        _note(items=[
            {"id": "itm_0000000000000001", "text": "Aufgabe Alpha", "done": False},
            {"id": "itm_0000000000000002", "text": "Aufgabe Alpha", "done": True},
            {"id": "itm_0000000000000003", "text": "Aufgabe Beta", "done": False},
        ]),
        _note(
            owner="bob",
            note_id="list-bob",
            items=[{"id": "itm_0000000000000004", "text": "Private Bob", "done": False}],
        ),
    ]
    memories = [
        {
            "id": "private-memory-alpha",
            "owner": "alice",
            "category": "task",
            "text": "Aufgabe Alpha",
            "status": "completed",
        },
        {
            "id": "private-memory-gamma",
            "owner": "alice",
            "category": "task",
            "text": "Todo: Aufgabe Gamma",
        },
        {
            "id": "private-memory-bob",
            "owner": "bob",
            "category": "task",
            "text": "Todo: Private Bob",
        },
        {
            "id": "preference-alice",
            "owner": "alice",
            "category": "preference",
            "text": "Prefers concise answers",
        },
    ]
    return notes, memories


def test_default_audit_is_owner_scoped_redacted_and_non_applying():
    notes, memories = _fixture_sources()
    before_notes = deepcopy(notes)
    before_memories = deepcopy(memories)

    report = audit_todo_state_drift(
        owner="alice",
        notes=notes,
        memories=memories,
        digest_limit=1,
    )
    encoded = repr(report)

    assert report["status"] == "drift_detected"
    assert report["read_only"] is True
    assert report["mutations_performed"] is False
    assert report["raw_content_visible"] is False
    assert report["counts"]["notes"] == 1
    assert report["counts"]["todo_items"] == 3
    assert report["counts"]["scoped_memories"] == 3
    assert report["counts"]["prohibited_todo_memories"] == 2
    assert report["counts"]["duplicate_groups"] == 1
    assert report["counts"]["completion_conflicts"] == 1
    assert report["counts"]["memory_completion_conflicts"] == 1
    assert report["counts"]["memory_only_candidates"] == 1
    assert report["counts"]["digest_limit_exclusions"] == 1
    assert report["repair_preview"]["apply_supported"] is False
    assert report["repair_preview"]["required_live_gate"] == TODO_DATA_REPAIR_LIVE_GATE
    assert report["repair_preview"]["action_count"] >= 5
    assert "Aufgabe Alpha" not in encoded
    assert "Aufgabe Beta" not in encoded
    assert "Aufgabe Gamma" not in encoded
    assert "Private Bob" not in encoded
    assert "private-memory" not in encoded
    assert notes == before_notes
    assert memories == before_memories


def test_exact_review_requires_two_explicit_flags_and_remains_ephemeral():
    notes, memories = _fixture_sources()

    with pytest.raises(TodoStateDriftAuditError, match="operator authorization"):
        audit_todo_state_drift(
            owner="alice",
            notes=notes,
            memories=memories,
            include_review_details=True,
        )

    report = audit_todo_state_drift(
        owner="alice",
        notes=notes,
        memories=memories,
        include_review_details=True,
        operator_authorized=True,
    )

    assert report["raw_content_visible"] is True
    assert report["operator_review"]["not_for_persistence"] is True
    assert "Aufgabe Alpha" in repr(report["operator_review"])


def test_legacy_and_malformed_items_are_counted_without_guessing_identity():
    note = _note(items=[
        {"text": "Aufgabe Legacy", "done": False},
        "not-an-item",
    ])

    report = audit_todo_state_drift(owner="alice", notes=[note], memories=[])

    assert report["counts"]["legacy_items_without_stable_id"] == 1
    assert report["counts"]["digest_unverifiable_legacy_open_items"] == 1
    assert report["counts"]["malformed_items"] == 1
    assert report["status"] == "drift_detected"


def _write_sqlite_fixture(path: Path):
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE notes ("
            "id TEXT, owner TEXT, title TEXT, items TEXT, note_type TEXT, "
            "archived INTEGER, pinned INTEGER, due_date TEXT, updated_at TEXT)"
        )
        connection.execute(
            "INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "list-alpha",
                "alice",
                "Private list",
                json.dumps([
                    {"id": "itm_0000000000000001", "text": "Aufgabe Datei", "done": False}
                ]),
                "checklist",
                0,
                0,
                None,
                "2026-07-22T06:00:00",
            ),
        )
        connection.commit()
    finally:
        connection.close()


def test_file_audit_reads_sqlite_and_memory_without_changing_sources(tmp_path, capsys):
    database = tmp_path / "app.db"
    memory_file = tmp_path / "memory.json"
    _write_sqlite_fixture(database)
    memory_file.write_text(json.dumps([
        {
            "id": "memory-file-alpha",
            "owner": "alice",
            "category": "task",
            "text": "Aufgabe Datei",
        }
    ]), encoding="utf-8")
    database_before = database.read_bytes()
    memory_before = memory_file.read_bytes()

    report = audit_todo_state_files(
        owner="alice",
        database_path=database,
        memory_path=memory_file,
    )
    exit_code = main([
        "--owner", "alice",
        "--database", str(database),
        "--memory-file", str(memory_file),
    ])
    stdout = capsys.readouterr().out

    assert report["status"] == "drift_detected"
    assert exit_code == 1
    assert "Aufgabe Datei" not in stdout
    assert database.read_bytes() == database_before
    assert memory_file.read_bytes() == memory_before


def test_missing_memory_file_is_not_created(tmp_path):
    database = tmp_path / "app.db"
    missing_memory = tmp_path / "missing-memory.json"
    _write_sqlite_fixture(database)

    report = audit_todo_state_files(
        owner="alice",
        database_path=database,
        memory_path=missing_memory,
    )

    assert report["counts"]["scoped_memories"] == 0
    assert not missing_memory.exists()


def test_cli_has_no_apply_option():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--owner", "alice", "--apply"])
