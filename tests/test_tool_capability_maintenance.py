import json

from src.tool_capability_maintenance import (
    build_tool_capability_snapshot,
    build_tool_memory_records,
    build_tool_raptorgraph_event,
    load_tool_capability_provider_payload,
    persist_tool_capability_knowledge,
    refresh_tool_capability_knowledge,
)


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
