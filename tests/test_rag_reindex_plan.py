from types import SimpleNamespace

import pytest

from src.embedding_lanes import collection_generation_name
from src.rag_vector import VectorRAG


class _Lane(SimpleNamespace):
    def count(self):
        return self.source_count


def test_collection_generation_name_uses_parallel_chunk_generation():
    assert (
        collection_generation_name("odysseus_rag", "fastembed", "rag_structured_v1")
        == "odysseus_rag_fastembed__chunkgen_rag_structured_v1"
    )


@pytest.mark.parametrize("generation", ["", "bad label", "../bad", "x" * 81])
def test_collection_generation_name_rejects_unsafe_generation_labels(generation):
    with pytest.raises(ValueError):
        collection_generation_name("odysseus_rag", "fastembed", generation)


def test_vectorrag_reindex_generation_plan_is_dry_run_and_rollback_ready():
    rag = VectorRAG.__new__(VectorRAG)
    rag._lanes = [
        _Lane(name="fastembed", collection_name="odysseus_rag_fastembed", source_count=12),
        _Lane(name="custom", collection_name="odysseus_rag_custom", source_count=5),
    ]

    plan = rag.plan_reindex_generation("rag_structured_v1")

    assert plan["dry_run"] is True
    assert plan["live_write_required"] is True
    assert plan["rollback_supported"] is True
    assert plan["status"] == "ready"
    assert [target["target_collection"] for target in plan["targets"]] == [
        "odysseus_rag_fastembed__chunkgen_rag_structured_v1",
        "odysseus_rag_custom__chunkgen_rag_structured_v1",
    ]
    assert [target["writes_planned"] for target in plan["targets"]] == [12, 5]
    assert all(target["writes_performed"] == 0 for target in plan["targets"])
    assert all(target["rollback_collection"].startswith("odysseus_rag_") for target in plan["targets"])


def test_vectorrag_reindex_generation_plan_degrades_without_lanes():
    rag = VectorRAG.__new__(VectorRAG)
    rag._lanes = []

    plan = rag.plan_reindex_generation()

    assert plan["status"] == "degraded_no_lanes"
    assert plan["targets"] == []
