from types import SimpleNamespace

from src.rag_reindex_dry_run import build_rag_reindex_dry_run_plan


class _Collection:
    def __init__(self, name, count):
        self.name = name
        self._count = count

    def count(self):
        return self._count


class _Client:
    def __init__(self, collections):
        self.collections = {collection.name: collection for collection in collections}
        self.add_called = False

    def list_collections(self):
        return list(self.collections.values())

    def get_collection(self, name):
        return self.collections[name]

    def get_or_create_collection(self, *args, **kwargs):
        self.add_called = True
        raise AssertionError("dry-run must not create collections")


def test_rag_reindex_dry_run_plans_existing_lane_collections_only():
    client = _Client(
        [
            _Collection("odysseus_rag_fastembed", 12),
            _Collection("odysseus_rag_custom", 5),
            _Collection("odysseus_rag", 3),
            _Collection("memory_vectors_fastembed", 99),
        ]
    )

    plan = build_rag_reindex_dry_run_plan(chroma_client=client)

    assert plan["dry_run"] is True
    assert plan["read_only"] is True
    assert plan["writes_performed"] == 0
    assert plan["status"] == "ready"
    assert plan["legacy_collection"] == {"name": "odysseus_rag", "count": 3}
    assert [target["source_collection"] for target in plan["targets"]] == [
        "odysseus_rag_custom",
        "odysseus_rag_fastembed",
    ]
    assert [target["writes_planned"] for target in plan["targets"]] == [5, 12]
    assert all(target["writes_performed"] == 0 for target in plan["targets"])
    assert client.add_called is False


def test_rag_reindex_dry_run_handles_chroma_versions_returning_names():
    class NameClient(_Client):
        def list_collections(self):
            return list(self.collections)

    client = NameClient([_Collection("odysseus_rag_fastembed", 7)])

    plan = build_rag_reindex_dry_run_plan(chroma_client=client, generation="next")

    assert plan["targets"][0]["target_collection"] == "odysseus_rag_fastembed__chunkgen_next"
    assert plan["targets"][0]["writes_planned"] == 7


def test_rag_reindex_dry_run_degrades_without_rag_lanes():
    client = _Client([_Collection("memory_vectors_fastembed", 99)])

    plan = build_rag_reindex_dry_run_plan(chroma_client=client)

    assert plan["status"] == "degraded_no_rag_lane_collections"
    assert plan["targets"] == []
    assert plan["rollback_supported"] is False
    assert plan["private_content_visible"] is False
    assert plan["secret_values_visible"] is False
