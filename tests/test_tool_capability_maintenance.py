import json

from src import memory_provenance_ledger
from src.tool_capability_maintenance import (
    append_tool_capability_raptorgraph_event,
    build_tool_capability_memory_write_intent,
    build_tool_capability_snapshot,
    build_tool_memory_records,
    build_tool_raptorgraph_event,
    execute_tool_capability_memory_write,
    load_tool_capability_provider_payload,
    persist_tool_capability_knowledge,
    refresh_tool_capability_knowledge,
    normalize_tool_capability_raptorgraph_event,
)


class FakeMemoryManager:
    def __init__(self):
        self.entries = []
        self.save_calls = 0

    def load_all(self):
        return list(self.entries)

    def save(self, entries):
        self.entries = list(entries)
        self.save_calls += 1


class FakeMemoryVector:
    def __init__(self):
        self.added = []
        self.removed = []

    def add(self, memory_id, text):
        self.added.append((memory_id, text))

    def remove(self, memory_id):
        self.removed.append(memory_id)


def test_tool_capability_snapshot_mentions_file_and_repo_tools():
    snapshot = build_tool_capability_snapshot(
        reason="unit-test",
        commit="abc1234",
        index_status={"status": "ok"},
        generated_at="2026-07-02T00:00:00+00:00",
    )

    assert snapshot["builtin_tool_count"] == len(snapshot["tool_names"])
    assert "read_file" in snapshot["tool_names"]
    assert "bash" in snapshot["tool_names"]
    assert "manage_repos" in snapshot["tool_names"]
    assert snapshot["index_status"]["status"] == "ok"
    assert snapshot["redaction_policy"]["stores_private_content"] is False


def test_tool_capability_memory_records_and_raptor_event_are_safe(tmp_path):
    snapshot = build_tool_capability_snapshot(
        reason="post-update",
        commit="def5678",
        index_status={"status": "ok"},
        generated_at="2026-07-02T00:00:00+00:00",
    )
    records = build_tool_memory_records(snapshot)
    event = build_tool_raptorgraph_event(snapshot, memory_records=records)

    assert records
    assert any(record["metadata"]["chunk"] == "summary" for record in records)
    assert event["event"] == "tool_capability_knowledge_refresh"
    assert event["memory_record_ids"]

    persist_tool_capability_knowledge(
        snapshot=snapshot,
        memory_records=records,
        raptorgraph_event=event,
        data_dir=tmp_path,
    )
    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    history = (tmp_path / "history.jsonl").read_text(encoding="utf-8")
    encoded = json.dumps(latest, sort_keys=True)

    assert latest["snapshot"]["id"] == snapshot["id"]
    assert "tool_capability_knowledge_refresh" in history
    assert "C:\\\\" not in encoded
    assert "Authorization" not in encoded


def test_refresh_can_build_packet_without_live_index(tmp_path):
    report = refresh_tool_capability_knowledge(
        reason="unit-test",
        commit="abc1234",
        data_dir=tmp_path,
        refresh_index=False,
    )

    assert report.index_status["status"] == "skipped"
    assert report.persisted is True
    assert report.memory_records
    assert (tmp_path / "latest.json").exists()


def test_provider_payload_exposes_key_tools_without_private_content():
    payload = load_tool_capability_provider_payload(query="Kannst du Dateien lesen und git nutzen?", budget=1000)

    state = payload["structured_state"]["tool_capability_snapshot"]
    assert "read_file" in state["key_tools_available"]
    assert "bash" in state["key_tools_available"]
    assert payload["memory"]["summary"]["readiness_state"] in {"ready", "degraded"}
    encoded = json.dumps(payload, sort_keys=True)
    assert "C:\\\\" not in encoded


def test_tool_capability_memory_write_blocks_without_gate():
    snapshot = build_tool_capability_snapshot(index_status={"status": "ok"}, generated_at="2026-07-03T00:00:00+00:00")
    records = build_tool_memory_records(snapshot)
    event = build_tool_raptorgraph_event(snapshot, memory_records=records)
    intent = build_tool_capability_memory_write_intent(
        snapshot=snapshot,
        memory_records=records,
        raptorgraph_event=event,
        owner="system",
    )

    report = execute_tool_capability_memory_write(intent, write_gate_open=False, dry_run=False, memory_manager=FakeMemoryManager())

    assert report.status == "blocked"
    assert report.reason == "write_gate_closed"
    assert report.writes_performed is False


def test_tool_capability_memory_write_dry_run_plans_without_writing():
    snapshot = build_tool_capability_snapshot(index_status={"status": "ok"}, generated_at="2026-07-03T00:00:00+00:00")
    records = build_tool_memory_records(snapshot)
    event = build_tool_raptorgraph_event(snapshot, memory_records=records)
    intent = build_tool_capability_memory_write_intent(
        snapshot=snapshot,
        memory_records=records,
        raptorgraph_event=event,
        owner="system",
    )
    manager = FakeMemoryManager()

    report = execute_tool_capability_memory_write(intent, write_gate_open=True, dry_run=True, memory_manager=manager)

    assert report.status == "planned"
    assert manager.entries == []
    assert report.memory_records_planned == len(records)


def test_tool_capability_memory_write_upserts_idempotently():
    snapshot = build_tool_capability_snapshot(index_status={"status": "ok"}, generated_at="2026-07-03T00:00:00+00:00")
    records = build_tool_memory_records(snapshot)
    event = build_tool_raptorgraph_event(snapshot, memory_records=records)
    intent = build_tool_capability_memory_write_intent(
        snapshot=snapshot,
        memory_records=records,
        raptorgraph_event=event,
        owner="system",
    )
    manager = FakeMemoryManager()
    vector = FakeMemoryVector()

    first = execute_tool_capability_memory_write(
        intent,
        write_gate_open=True,
        dry_run=False,
        memory_manager=manager,
        memory_vector=vector,
        owner="system",
    )
    second = execute_tool_capability_memory_write(
        intent,
        write_gate_open=True,
        dry_run=False,
        memory_manager=manager,
        memory_vector=vector,
        owner="system",
    )

    assert first.status == "written"
    assert first.memory_records_written == len(records)
    assert second.status == "written"
    assert second.memory_records_written == 0
    assert second.memory_records_skipped == len(records)
    assert len(manager.entries) == len(records)
    assert all(entry["owner"] == "system" for entry in manager.entries)
    assert len(vector.added) == len(records)


def test_tool_capability_raptorgraph_event_normalizes_and_dedupes(tmp_path, monkeypatch):
    ledger_dir = tmp_path / "ledger"
    graph_dir = tmp_path / "graph"
    monkeypatch.setattr(memory_provenance_ledger, "MEMORY_PROVENANCE_LEDGER_DIR", str(ledger_dir))
    snapshot = build_tool_capability_snapshot(index_status={"status": "ok"}, generated_at="2026-07-03T00:00:00+00:00")
    records = build_tool_memory_records(snapshot)
    event = build_tool_raptorgraph_event(snapshot, memory_records=records)

    normalized = normalize_tool_capability_raptorgraph_event(event)
    first = append_tool_capability_raptorgraph_event(event, root=graph_dir).to_dict()
    second = append_tool_capability_raptorgraph_event(event, root=graph_dir).to_dict()

    assert normalized["event"] == "tool_capability_knowledge_refresh"
    assert normalized["event_id"].startswith("tool-rg-")
    assert first["status"] == "written"
    assert second["status"] == "duplicate"
    rows = [json.loads(line) for line in (graph_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["source_provider"] == "tool_capability_maintenance"
    assert rows[0]["raw_content_visible"] is False
    provenance_rows = [
        json.loads(line)
        for line in memory_provenance_ledger.ledger_path().read_text(encoding="utf-8").splitlines()
    ]
    assert [row["event_type"] for row in provenance_rows] == ["raptorgraph_mutation", "raptorgraph_mutation"]
    assert [row["status"] for row in provenance_rows] == ["written", "duplicate"]
    assert provenance_rows[0]["graph_event_id"].startswith("tool-rg-")
