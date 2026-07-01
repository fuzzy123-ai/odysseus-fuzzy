"""Read-only dry-run planning for RAG chunk-generation rebuilds."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from src.embedding_lanes import LANE_CUSTOM, LANE_FASTEMBED, collection_generation_name, collection_name
from src.rag_text_chunking import STRUCTURED_SPLITTER_VERSION
from src.rag_vector import COLLECTION_NAME


RAG_REINDEX_DRY_RUN_SCHEMA = "odysseus.rag_reindex_generation_readonly_plan.v1"
KNOWN_RAG_LANES = (LANE_CUSTOM, LANE_FASTEMBED)


@dataclass(frozen=True, slots=True)
class CollectionSnapshot:
    name: str
    count: int

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "count": self.count}


def build_rag_reindex_dry_run_plan(
    *,
    chroma_client: Any,
    generation: str = STRUCTURED_SPLITTER_VERSION,
    base_collection: str = COLLECTION_NAME,
) -> dict[str, Any]:
    """Build a read-only plan from existing Chroma collections."""

    snapshots = _collection_snapshots(chroma_client)
    by_name = {snapshot.name: snapshot for snapshot in snapshots}
    targets: list[dict[str, Any]] = []
    for lane in KNOWN_RAG_LANES:
        source_name = collection_name(base_collection, lane)
        snapshot = by_name.get(source_name)
        if snapshot is None:
            continue
        targets.append(
            {
                "lane": lane,
                "source_collection": source_name,
                "target_collection": collection_generation_name(base_collection, lane, generation),
                "rollback_collection": source_name,
                "source_count": snapshot.count,
                "writes_planned": snapshot.count,
                "writes_performed": 0,
                "live_write_required": True,
            }
        )

    legacy = by_name.get(base_collection)
    return {
        "schema": RAG_REINDEX_DRY_RUN_SCHEMA,
        "dry_run": True,
        "read_only": True,
        "base_collection": base_collection,
        "generation": generation,
        "splitter_version": generation,
        "status": "ready" if targets else "degraded_no_rag_lane_collections",
        "targets": targets,
        "legacy_collection": legacy.to_dict() if legacy else None,
        "collection_snapshots": [snapshot.to_dict() for snapshot in snapshots],
        "writes_performed": 0,
        "rollback_supported": bool(targets),
        "next_action": (
            "operator_go_required_before_collection_writes"
            if targets
            else "start_or_repair_chromadb_rag_lanes_before_reindex"
        ),
        "private_content_visible": False,
        "secret_values_visible": False,
    }


def _collection_snapshots(chroma_client: Any) -> list[CollectionSnapshot]:
    snapshots: list[CollectionSnapshot] = []
    for name in sorted(_collection_names(chroma_client)):
        try:
            collection = chroma_client.get_collection(name)
            count = int(collection.count())
        except Exception:
            count = 0
        snapshots.append(CollectionSnapshot(name=name, count=count))
    return snapshots


def _collection_names(chroma_client: Any) -> Iterable[str]:
    raw = chroma_client.list_collections()
    for item in raw or []:
        name = item if isinstance(item, str) else getattr(item, "name", "")
        name = str(name or "").strip()
        if name:
            yield name
