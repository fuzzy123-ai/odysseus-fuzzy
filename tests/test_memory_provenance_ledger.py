import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import diagnostics_routes
from src import memory_provenance_ledger


def _rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_memory_provenance_records_redacted_events(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_provenance_ledger, "MEMORY_PROVENANCE_LEDGER_DIR", str(tmp_path))

    record = memory_provenance_ledger.record_memory_provenance(
        "memory_write_intent",
        owner="alice",
        surface="telegram",
        source="universal_inbox",
        action="execute_write_intent",
        status="review",
        reason="review_confirmation_required",
        document_ref="C:/private/source.pdf",
        source_hash="a" * 64,
        memory_record_ids=("uix-abc",),
        classification="private",
        dsgvo_mode=True,
        local_only=True,
        metadata={"note": "safe_marker", "count": 1},
    )

    encoded = json.dumps(record, sort_keys=True)
    assert record["schema"] == "odysseus.memory_provenance.v1"
    assert record["document_ref"].startswith("sha256:")
    assert record["raw_content_visible"] is False
    assert "C:/private/source.pdf" not in encoded


def test_memory_provenance_rejects_secret_markers():
    with pytest.raises(memory_provenance_ledger.MemoryProvenanceLedgerError):
        memory_provenance_ledger.build_memory_provenance_record(
            "memory_retrieval",
            model_id="Bearer secret-token",
        )


def test_memory_provenance_read_filters_and_summarizes(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_provenance_ledger, "MEMORY_PROVENANCE_LEDGER_DIR", str(tmp_path))

    memory_provenance_ledger.record_memory_provenance(
        "memory_retrieval",
        owner="alice",
        surface="context_orchestrator",
        source="native_memory",
        status="success",
        retrieval_count=3,
        used_in_context=True,
    )
    memory_provenance_ledger.record_memory_provenance(
        "memory_maintenance",
        owner="alice",
        surface="memory_audit",
        source="memory_vector",
        action="rebuild",
        status="success",
        before_count=2,
        after_count=2,
    )

    result = memory_provenance_ledger.read_memory_provenance(event_type="memory_retrieval")

    assert result["count"] == 1
    assert result["records"][0]["event_type"] == "memory_retrieval"
    assert result["summary"]["by_event_type"] == {"memory_retrieval": 1}


def test_memory_provenance_records_user_interaction_with_agent_and_model_stamp(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_provenance_ledger, "MEMORY_PROVENANCE_LEDGER_DIR", str(tmp_path))

    record = memory_provenance_ledger.record_memory_provenance(
        "memory_user_interaction",
        owner="alice",
        surface="telegram",
        source="memory_review",
        action="approve_memory_candidate",
        status="approved",
        session_id="session-abc",
        task_id="review-123",
        model_id="gemma-local-2b",
        agent_id="odysseus-ai",
        review_required=False,
        dry_run=False,
        writes_performed=False,
        metadata={"decision": "approved", "raw_preview": "not-stored"},
    )

    result = memory_provenance_ledger.read_memory_provenance(event_type="memory_user_interaction")
    encoded = json.dumps(record, sort_keys=True)

    assert record["event_type"] == "memory_user_interaction"
    assert record["model_id"] == "gemma-local-2b"
    assert record["agent_id"] == "odysseus-ai"
    assert record["raw_content_visible"] is False
    assert result["count"] == 1
    assert result["records"][0]["action"] == "approve_memory_candidate"
    assert "authorization" not in encoded.lower()


def test_memory_provenance_diagnostics_route_is_admin_gated_and_redacted(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_provenance_ledger, "MEMORY_PROVENANCE_LEDGER_DIR", str(tmp_path))
    monkeypatch.setattr(diagnostics_routes, "require_admin", lambda _request: None)
    app = FastAPI()
    app.include_router(diagnostics_routes.setup_diagnostics_routes(None, False, None))

    memory_provenance_ledger.record_memory_provenance(
        "raptorgraph_mutation",
        owner="alice",
        surface="universal_inbox",
        source="raptorgraph_event_store",
        action="append_event",
        status="written",
        graph_event_id="uix-rg-abc",
        source_hash="b" * 64,
    )

    response = TestClient(app).get("/api/diagnostics/memory-provenance?event_type=raptorgraph_mutation")
    payload = response.json()
    encoded = json.dumps(payload, sort_keys=True)

    assert response.status_code == 200
    assert payload["status"] == "success"
    assert payload["count"] == 1
    assert payload["records"][0]["event_type"] == "raptorgraph_mutation"
    assert "authorization" not in encoded.lower()
