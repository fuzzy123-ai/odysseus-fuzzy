import json
from types import SimpleNamespace

from src import context_orchestrator, memory_provenance_ledger


def test_context_retrieval_records_redacted_memory_usage(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_provenance_ledger, "MEMORY_PROVENANCE_LEDGER_DIR", str(tmp_path))

    provider = SimpleNamespace(
        id="native_memory",
        plugin_id=None,
        capabilities=("chat",),
        retrieve=lambda **_kwargs: {
            "snippets": ["PRIVATE RAW TEXT should not enter provenance"],
            "sources": [{"id": "mem-1"}],
            "memory": {"summary": {"readiness_state": "ready"}},
        },
    )
    monkeypatch.setattr(context_orchestrator, "get_context_providers", lambda capability: [provider])

    payloads, warnings = context_orchestrator.preload_provider_context(
        owner="alice",
        query="private query",
        budget_tokens=1000,
        mode="chat",
    )

    rows = [
        json.loads(line)
        for line in memory_provenance_ledger.ledger_path().read_text(encoding="utf-8").splitlines()
    ]
    encoded = json.dumps(rows, sort_keys=True)

    assert len(payloads) == 1
    assert warnings == []
    assert rows[0]["event_type"] == "memory_retrieval"
    assert rows[0]["retrieval_count"] == 1
    assert rows[0]["used_in_context"] is True
    assert "PRIVATE RAW TEXT" not in encoded
    assert "private query" not in encoded
