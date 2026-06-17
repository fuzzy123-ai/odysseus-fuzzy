from pathlib import Path
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.memory_store_stats import (
    ROLE_CANONICAL,
    ROLE_DERIVED_INDEX,
    ROLE_KNOWLEDGE_INDEX,
    build_memory_store_stats,
    get_bounded_directory_size,
)


class MemoryManagerStub:
    def __init__(self, memory_file: Path, entries):
        self.memory_file = str(memory_file)
        self._entries = entries

    def load_all(self):
        return list(self._entries)


class VectorStatsStub:
    def get_stats(self):
        return {
            "healthy": True,
            "count": 4,
            "lanes": [{"name": "fastembed"}, {"name": "custom"}],
        }


def test_build_memory_store_stats_distinguishes_roles_without_leaking_memory_text(tmp_path):
    memory_file = tmp_path / "memory.json"
    memory_file.write_text('[{"id":"1","text":"private memory text"}]', encoding="utf-8")
    chroma_dir = tmp_path / "chroma"
    chroma_dir.mkdir()
    (chroma_dir / "segment.bin").write_bytes(b"abc123")
    manager = MemoryManagerStub(
        memory_file,
        entries=[
            {"id": "1", "text": "private memory text"},
            {"id": "2", "text": "secret preference"},
        ],
    )

    stats = build_memory_store_stats(
        memory_manager=manager,
        vector_stats=VectorStatsStub(),
        rag_stats={"document_count": 12},
        chroma_path=chroma_dir,
    )

    payload = stats.to_dict()

    assert payload["personal_memory_entries"] == 2
    assert payload["memory_json_bytes"] == memory_file.stat().st_size
    assert payload["memory_json_path"] == str(memory_file)
    assert payload["vector_index_healthy"] is True
    assert payload["vector_index_count"] == 4
    assert payload["vector_lanes"] == ["fastembed", "custom"]
    assert payload["chroma_bytes"] == 6
    assert payload["rag_document_count"] == 12
    assert payload["roles"] == {
        "personal_memory": ROLE_CANONICAL,
        "vector_index": ROLE_DERIVED_INDEX,
        "knowledge_index": ROLE_KNOWLEDGE_INDEX,
    }
    assert "private memory text" not in str(payload)
    assert "secret preference" not in str(payload)


def test_build_memory_store_stats_is_stable_for_missing_optional_sources(tmp_path):
    stats = build_memory_store_stats(
        memory_manager=None,
        vector_stats={"healthy": False, "count": 0, "lanes": []},
        rag_stats=None,
        memory_json_path=str(tmp_path / "missing-memory.json"),
        chroma_path=tmp_path / "missing-chroma",
    )

    assert stats.personal_memory_entries == 0
    assert stats.memory_json_bytes == 0
    assert stats.vector_index_healthy is False
    assert stats.vector_index_count == 0
    assert stats.vector_lanes == ()
    assert stats.chroma_bytes == 0
    assert stats.rag_document_count is None


def test_bounded_directory_size_stops_after_file_limit(tmp_path):
    capped_dir = tmp_path / "bounded"
    capped_dir.mkdir()
    (capped_dir / "a.bin").write_bytes(b"1111")
    (capped_dir / "b.bin").write_bytes(b"22222")
    (capped_dir / "c.bin").write_bytes(b"333333")

    size = get_bounded_directory_size(capped_dir, max_files=2)

    assert size in {9, 10, 11}
    assert size < 15


def test_rag_stats_can_be_derived_from_document_list(tmp_path):
    memory_file = tmp_path / "memory.json"
    memory_file.write_text("[]", encoding="utf-8")
    manager = MemoryManagerStub(memory_file, entries=[])

    stats = build_memory_store_stats(
        memory_manager=manager,
        vector_stats={"healthy": True, "count": 1, "lanes": [{"name": "fastembed"}]},
        rag_stats={"documents": ["a", "b", "c"]},
    )

    assert stats.rag_document_count == 3


def test_memory_stats_route_is_admin_gated_and_does_not_leak_text(monkeypatch, tmp_path):
    import routes.memory_routes as memory_routes

    admin_calls = []
    monkeypatch.setattr(memory_routes, "require_admin", lambda request: admin_calls.append(request))
    memory_file = tmp_path / "memory.json"
    memory_file.write_text('[{"id":"1","text":"private memory text"}]', encoding="utf-8")
    (tmp_path / "chroma").mkdir()
    (tmp_path / "chroma" / "index.bin").write_bytes(b"vector")
    manager = MemoryManagerStub(
        memory_file,
        entries=[{"id": "1", "text": "private memory text"}],
    )

    app = FastAPI()
    app.include_router(memory_routes.setup_memory_routes(manager, MagicMock(), VectorStatsStub()))
    response = TestClient(app).get("/api/memory/stats")

    assert response.status_code == 200
    payload = response.json()
    assert admin_calls
    assert payload["personal_memory_entries"] == 1
    assert payload["memory_json_bytes"] == memory_file.stat().st_size
    assert payload["vector_index_healthy"] is True
    assert payload["vector_index_count"] == 4
    assert payload["chroma_bytes"] == 6
    assert payload["roles"] == {
        "personal_memory": ROLE_CANONICAL,
        "vector_index": ROLE_DERIVED_INDEX,
        "knowledge_index": ROLE_KNOWLEDGE_INDEX,
    }
    assert "private memory text" not in str(payload)
