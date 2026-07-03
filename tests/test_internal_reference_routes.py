import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.internal_reference_routes as internal_reference_routes
from routes.internal_reference_routes import setup_internal_reference_routes
from src.universal_inbox_raptorgraph_store import normalize_universal_inbox_raptorgraph_event


class _AuthManager:
    is_configured = True


class _MemoryManager:
    def __init__(self, rows):
        self._rows = rows

    def load(self, owner=None):
        if owner is None:
            return list(self._rows)
        return [row for row in self._rows if row.get("owner") == owner]


def _app(memory_rows, *, user="alice") -> FastAPI:
    app = FastAPI()
    app.state.auth_manager = _AuthManager()

    @app.middleware("http")
    async def _stamp_user(request, call_next):
        if user is not None:
            request.state.current_user = user
        return await call_next(request)

    app.include_router(setup_internal_reference_routes(_MemoryManager(memory_rows)))
    return app


def test_resolves_memory_ref_without_exposing_text_or_paths():
    client = TestClient(
        _app(
            [
                {
                    "id": "mem-1",
                    "text": "private memory text must not leave this route",
                    "category": "fact",
                    "source": "universal_inbox",
                    "timestamp": 1783094400,
                    "owner": "alice",
                }
            ]
        )
    )

    response = client.get("/api/internal-refs/resolve", params={"ref": "odysseus://memory/mem-1"})
    payload = response.json()
    encoded = json.dumps(payload, sort_keys=True)

    assert response.status_code == 200
    assert payload["status"] == "resolved"
    assert payload["exists"] is True
    assert payload["ref"]["uri"] == "odysseus://memory/mem-1"
    assert payload["target"]["read_route"] == "/api/memory/mem-1"
    assert payload["target"]["text_redacted"] is True
    assert payload["raw_content_visible"] is False
    assert "private memory text" not in encoded


def test_memory_ref_denies_cross_owner_as_not_found():
    client = TestClient(
        _app(
            [
                {
                    "id": "mem-2",
                    "text": "bob private memory",
                    "owner": "bob",
                }
            ],
            user="alice",
        )
    )

    response = client.get("/api/internal-refs/resolve", params={"ref": "memory:mem-2"})
    payload = response.json()

    assert response.status_code == 200
    assert payload["status"] == "not_found"
    assert payload["exists"] is False


def test_resolves_raptor_edge_from_redacted_event_store(tmp_path, monkeypatch):
    graph_dir = tmp_path / "universal_inbox_raptorgraph"
    graph_dir.mkdir()
    event = normalize_universal_inbox_raptorgraph_event(
        {
            "source_hash": "a" * 64,
            "memory_record_ids": ("mem-1",),
            "classification": "private",
            "document_type": "reference",
            "domain": "admin",
            "local_only": True,
            "dsgvo_mode": True,
            "review_reasons": ("needs_review",),
        }
    )
    (graph_dir / "events.jsonl").write_text(json.dumps(event, sort_keys=True) + "\n", encoding="utf-8")
    monkeypatch.setattr(internal_reference_routes, "DATA_DIR", str(tmp_path))

    response = TestClient(_app([])).get(
        "/api/internal-refs/resolve",
        params={"ref": f"odysseus://raptor/edge/{event['event_id']}"},
    )
    payload = response.json()
    encoded = json.dumps(payload, sort_keys=True)

    assert response.status_code == 200
    assert payload["status"] == "resolved"
    assert payload["exists"] is True
    assert payload["target"]["event_id"] == event["event_id"]
    assert payload["target"]["memory_record_count"] == 1
    assert payload["target"]["raw_content_visible"] is False
    assert str(tmp_path) not in encoded


def test_raptor_node_ref_falls_back_to_provenance_diagnostics():
    response = TestClient(_app([])).get(
        "/api/internal-refs/resolve",
        params={"ref": "raptor:uix-raptor-missing"},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["status"] == "diagnostics_fallback"
    assert payload["exists"] is False
    assert payload["target"]["read_route"] == "/api/diagnostics/memory-provenance?event_type=raptorgraph_mutation"
    assert payload["target"]["node_id"] == "uix-raptor-missing"


def test_rag_and_graph_refs_return_reference_only_targets_without_raw_content():
    client = TestClient(_app([]))

    rag_response = client.get(
        "/api/internal-refs/resolve",
        params={"ref": "odysseus://rag/source/source-1"},
    )
    graph_response = client.get(
        "/api/internal-refs/resolve",
        params={"ref": "odysseus://graph/node/node-1"},
    )
    rag_payload = rag_response.json()
    graph_payload = graph_response.json()

    assert rag_response.status_code == 200
    assert rag_payload["status"] == "reference_only"
    assert rag_payload["target"]["kind"] == "rag_reference"
    assert rag_payload["target"]["read_route"] == "/api/rag/stats"
    assert rag_payload["target"]["raw_content_visible"] is False
    assert graph_response.status_code == 200
    assert graph_payload["status"] == "reference_only"
    assert graph_payload["target"]["kind"] == "graph_reference"
    assert graph_payload["target"]["read_route"] == "/api/diagnostics/memory-provenance"
    assert graph_payload["target"]["content_redacted"] is True


def test_rejects_unsafe_internal_ref():
    response = TestClient(_app([])).get(
        "/api/internal-refs/resolve",
        params={"ref": "memory:C:/Users/private/file.txt"},
    )

    assert response.status_code == 400
