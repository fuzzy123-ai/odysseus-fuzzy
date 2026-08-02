import pytest

from src.native_knowledge_store import (
    MAX_EXPORT_LIMIT,
    KnowledgeAccessDenied,
    KnowledgeNotFound,
    KnowledgeStoreError,
    KnowledgeTombstoned,
    NativeKnowledgeStore,
    KnowledgeVersion,
)


def test_identical_synthetic_create_has_deterministic_immutable_version_identity():
    store = NativeKnowledgeStore()
    first = store.create(owner_id="owner-a", knowledge_id="note-1", policy="private", content="alpha")
    repeated = store.create(owner_id="owner-a", knowledge_id="note-1", policy="private", content="alpha")

    assert first == repeated
    assert first.version == 1
    assert first.version_id.startswith("sha256:")
    assert len(first.version_id) == 71


def test_exact_read_is_owner_policy_and_version_pinned():
    store = NativeKnowledgeStore()
    first = store.create(owner_id="owner-a", knowledge_id="note-1", policy="private", content="old")
    second = store.create(owner_id="owner-a", knowledge_id="note-1", policy="private", content="new")

    assert store.read_exact(owner_id="owner-a", knowledge_id="note-1", policy="private", version=1) == first
    assert store.read_exact(owner_id="owner-a", knowledge_id="note-1", policy="private", version=2) == second
    with pytest.raises(KnowledgeNotFound):
        store.read_exact(owner_id="owner-b", knowledge_id="note-1", policy="private", version=1)
    with pytest.raises(KnowledgeAccessDenied):
        store.read_exact(owner_id="owner-a", knowledge_id="note-1", policy="shared", version=1)


def test_missing_version_and_tombstone_fail_closed():
    store = NativeKnowledgeStore()
    store.create(owner_id="owner-a", knowledge_id="note-1", policy="private", content="alpha")

    with pytest.raises(KnowledgeNotFound):
        store.read_exact(owner_id="owner-a", knowledge_id="note-1", policy="private", version=2)
    current = store.read_exact(owner_id="owner-a", knowledge_id="note-1", policy="private", version=1)
    store.tombstone(
        owner_id="owner-a", knowledge_id="note-1", policy="private", version=1, version_id=current.version_id
    )
    with pytest.raises(KnowledgeTombstoned):
        store.read_exact(owner_id="owner-a", knowledge_id="note-1", policy="private", version=1)
    with pytest.raises(KnowledgeTombstoned):
        store.create(owner_id="owner-a", knowledge_id="note-1", policy="private", content="replacement")


def test_export_is_owner_policy_bound_excludes_tombstones_and_is_bounded():
    store = NativeKnowledgeStore()
    store.create(owner_id="owner-a", knowledge_id="note-2", policy="private", content="two")
    store.create(owner_id="owner-a", knowledge_id="note-1", policy="private", content="one")
    store.create(owner_id="owner-a", knowledge_id="shared", policy="shared", content="shared")
    store.create(owner_id="owner-b", knowledge_id="other", policy="private", content="other")
    stale = store.read_exact(owner_id="owner-a", knowledge_id="note-2", policy="private", version=1)
    store.tombstone(
        owner_id="owner-a", knowledge_id="note-2", policy="private", version=1, version_id=stale.version_id
    )

    assert [item.knowledge_id for item in store.export(owner_id="owner-a", policy="private", limit=2)] == ["note-1"]
    assert store.export(owner_id="owner-a", policy="shared", limit=1)[0].knowledge_id == "shared"
    with pytest.raises(KnowledgeStoreError):
        store.export(owner_id="owner-a", policy="private", limit=MAX_EXPORT_LIMIT + 1)
    with pytest.raises(KnowledgeStoreError):
        store.export(owner_id="owner-a", policy="private", limit=True)


def test_invalid_identifiers_and_unbounded_content_are_rejected():
    store = NativeKnowledgeStore()
    with pytest.raises(KnowledgeStoreError):
        store.create(owner_id="", knowledge_id="note", policy="private", content="alpha")
    with pytest.raises(KnowledgeStoreError):
        store.create(owner_id="owner", knowledge_id="note", policy="private", content="x" * 16_385)


@pytest.mark.parametrize("value", ["has space", "line\nbreak", "/absolute-path", "api_token_value"])
def test_identifiers_and_constructed_versions_fail_closed(value):
    store = NativeKnowledgeStore()
    with pytest.raises(KnowledgeStoreError):
        store.create(owner_id=value, knowledge_id="note", policy="private", content="alpha")
    with pytest.raises(KnowledgeStoreError):
        KnowledgeVersion(
            owner_id="owner-a",
            knowledge_id="note-1",
            policy="private",
            version=True,
            version_id="sha256:" + "0" * 64,
            content="alpha",
        )


def test_tombstone_requires_current_version_id_and_current_policy():
    store = NativeKnowledgeStore()
    first = store.create(owner_id="owner-a", knowledge_id="note-1", policy="private", content="old")
    current = store.create(owner_id="owner-a", knowledge_id="note-1", policy="shared", content="new")

    with pytest.raises(KnowledgeAccessDenied):
        store.tombstone(
            owner_id="owner-a", knowledge_id="note-1", policy="private", version=first.version, version_id=first.version_id
        )
    same_policy_store = NativeKnowledgeStore()
    stale = same_policy_store.create(owner_id="owner-a", knowledge_id="note-2", policy="shared", content="old")
    latest = same_policy_store.create(owner_id="owner-a", knowledge_id="note-2", policy="shared", content="new")
    with pytest.raises(KnowledgeStoreError, match="current exact version"):
        same_policy_store.tombstone(
            owner_id="owner-a", knowledge_id="note-2", policy="shared", version=stale.version, version_id=stale.version_id
        )
    with pytest.raises(KnowledgeStoreError, match="current exact version"):
        same_policy_store.tombstone(
            owner_id="owner-a", knowledge_id="note-2", policy="shared", version=latest.version, version_id=stale.version_id
        )
    with pytest.raises(KnowledgeNotFound):
        store.read_exact(owner_id="owner-a", knowledge_id="note-1", policy="shared", version=True)
