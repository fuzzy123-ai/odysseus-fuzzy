"""Read-only storage stats for personal memory, vector memory, and RAG indexes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROLE_CANONICAL = "canonical"
ROLE_DERIVED_INDEX = "derived_index"
ROLE_KNOWLEDGE_INDEX = "knowledge_index"


@dataclass(frozen=True)
class MemoryStoreStats:
    personal_memory_entries: int
    memory_json_bytes: int | None
    memory_json_path: str | None
    vector_index_healthy: bool
    vector_index_count: int
    vector_lanes: tuple[str, ...]
    chroma_bytes: int | None = None
    rag_document_count: int | None = None
    personal_memory_role: str = ROLE_CANONICAL
    vector_index_role: str = ROLE_DERIVED_INDEX
    rag_index_role: str = ROLE_KNOWLEDGE_INDEX

    def to_dict(self) -> dict[str, Any]:
        return {
            "personal_memory_entries": self.personal_memory_entries,
            "memory_json_bytes": self.memory_json_bytes,
            "memory_json_path": self.memory_json_path,
            "vector_index_healthy": self.vector_index_healthy,
            "vector_index_count": self.vector_index_count,
            "vector_lanes": list(self.vector_lanes),
            "chroma_bytes": self.chroma_bytes,
            "rag_document_count": self.rag_document_count,
            "roles": {
                "personal_memory": self.personal_memory_role,
                "vector_index": self.vector_index_role,
                "knowledge_index": self.rag_index_role,
            },
        }


def build_memory_store_stats(
    memory_manager: Any | None = None,
    vector_stats: Any | None = None,
    rag_stats: Any | None = None,
    memory_json_path: str | None = None,
    chroma_path: str | None = None,
    max_chroma_files: int = 2048,
) -> MemoryStoreStats:
    memory_path = memory_json_path or getattr(memory_manager, "memory_file", None)
    normalized_vector = _normalize_stats_input(vector_stats)
    normalized_rag = _normalize_stats_input(rag_stats)
    return MemoryStoreStats(
        personal_memory_entries=_count_memory_entries(memory_manager),
        memory_json_bytes=_safe_file_size(memory_path),
        memory_json_path=str(memory_path) if memory_path else None,
        vector_index_healthy=bool(normalized_vector.get("healthy", False)),
        vector_index_count=_coerce_non_negative_int(normalized_vector.get("count", 0), default=0),
        vector_lanes=_normalize_lanes(normalized_vector.get("lanes")),
        chroma_bytes=get_bounded_directory_size(chroma_path, max_files=max_chroma_files),
        rag_document_count=_extract_rag_document_count(normalized_rag),
    )


def get_bounded_directory_size(
    directory: str | Path | None,
    *,
    max_files: int = 2048,
) -> int | None:
    if directory is None:
        return None
    path = Path(directory)
    if not path.exists() or not path.is_dir():
        return 0

    total = 0
    seen = 0
    try:
        for file_path in path.rglob("*"):
            if seen >= max_files:
                break
            if not file_path.is_file():
                continue
            seen += 1
            try:
                total += file_path.stat().st_size
            except OSError:
                continue
    except OSError:
        return 0
    return total


def _normalize_stats_input(source: Any | None) -> dict[str, Any]:
    if source is None:
        return {}
    if hasattr(source, "get_stats") and callable(source.get_stats):
        try:
            source = source.get_stats()
        except Exception:
            return {}
    if isinstance(source, dict):
        return source
    return {}


def _count_memory_entries(memory_manager: Any | None) -> int:
    if memory_manager is None:
        return 0
    for attr in ("load_all", "load"):
        loader = getattr(memory_manager, attr, None)
        if callable(loader):
            try:
                loaded = loader()
            except TypeError:
                continue
            except Exception:
                return 0
            if isinstance(loaded, (list, tuple)):
                return len(loaded)
            return 0
    return 0


def _safe_file_size(path: str | Path | None) -> int | None:
    if path is None:
        return None
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def _normalize_lanes(raw_lanes: Any) -> tuple[str, ...]:
    if not isinstance(raw_lanes, list):
        return ()
    names: list[str] = []
    for lane in raw_lanes:
        if isinstance(lane, dict):
            name = lane.get("name")
        else:
            name = getattr(lane, "name", None)
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return tuple(names)


def _extract_rag_document_count(rag_stats: dict[str, Any]) -> int | None:
    if not rag_stats:
        return None
    for key in ("document_count", "documents", "count"):
        value = rag_stats.get(key)
        if isinstance(value, list):
            return len(value)
        coerced = _coerce_non_negative_int(value, default=None)
        if coerced is not None:
            return coerced
    return None


def _coerce_non_negative_int(value: Any, *, default: int | None) -> int | None:
    if value is None:
        return default
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        return default
    return max(coerced, 0)
