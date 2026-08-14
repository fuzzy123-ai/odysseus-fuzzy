from threading import Barrier, Event, Thread

import pytest

import src.native_knowledge_store as native_knowledge

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


def test_store_generation_is_exact_monotonic_and_idempotent_create_does_not_advance():
    store = NativeKnowledgeStore()

    assert type(store.generation) is int
    assert store.generation == 0

    first = store.create(
        owner_id="owner-a", knowledge_id="note-1", policy="private", content="alpha"
    )
    assert store.generation == 1

    repeated = store.create(
        owner_id="owner-a", knowledge_id="note-1", policy="private", content="alpha"
    )
    assert repeated == first
    assert repeated is not first
    assert store.generation == 1

    store.create(owner_id="owner-a", knowledge_id="note-1", policy="private", content="beta")
    assert store.generation == 2


def test_capture_and_read_exact_require_the_same_generation():
    store = NativeKnowledgeStore()
    created = store.create(
        owner_id="owner-a", knowledge_id="note-1", policy="private", content="alpha"
    )

    snapshot = store.capture(owner_id="owner-a", policy="private", limit=10)
    assert type(snapshot) is native_knowledge.KnowledgeStoreSnapshot
    assert snapshot.generation == 1
    assert snapshot.owner_id == "owner-a"
    assert snapshot.policy == "private"
    assert snapshot.records == (created,)
    assert snapshot.records[0] is not created

    selected = store.read_exact_at_generation(
        owner_id="owner-a",
        knowledge_id="note-1",
        policy="private",
        version=1,
        expected_generation=snapshot.generation,
    )
    assert selected == created
    assert selected is not created

    store.create(owner_id="owner-a", knowledge_id="note-2", policy="private", content="beta")
    with pytest.raises(native_knowledge.KnowledgeGenerationMismatch) as raised:
        store.read_exact_at_generation(
            owner_id="owner-a",
            knowledge_id="note-1",
            policy="private",
            version=1,
            expected_generation=snapshot.generation,
        )
    assert raised.value.args == ("stale_authority",)


def test_capture_exact_successor_requires_observed_generation_equal_prior_plus_one():
    store = NativeKnowledgeStore()
    store.create(owner_id="owner-a", knowledge_id="note-1", policy="private", content="alpha")
    prior = store.capture(owner_id="owner-a", policy="private", limit=10)

    store.create(owner_id="owner-a", knowledge_id="note-2", policy="private", content="beta")
    successor = store.capture_exact_successor(
        owner_id="owner-a", policy="private", limit=10, prior_generation=prior.generation
    )

    assert successor.generation == prior.generation + 1
    assert tuple(record.knowledge_id for record in successor.records) == ("note-1", "note-2")

    with pytest.raises(native_knowledge.KnowledgeGenerationMismatch):
        store.capture_exact_successor(
            owner_id="owner-a",
            policy="private",
            limit=10,
            prior_generation=successor.generation,
        )


def test_capture_exact_successor_rejects_interleaved_extra_generation_as_stale_authority():
    store = NativeKnowledgeStore()
    target = store.create(
        owner_id="owner-a", knowledge_id="target", policy="private", content="alpha"
    )
    prior = store.capture(owner_id="owner-a", policy="private", limit=10)

    store.tombstone(
        owner_id="owner-a",
        knowledge_id="target",
        policy="private",
        version=target.version,
        version_id=target.version_id,
    )
    store.create(owner_id="owner-a", knowledge_id="other", policy="private", content="beta")

    with pytest.raises(native_knowledge.KnowledgeGenerationMismatch) as raised:
        store.capture_exact_successor(
            owner_id="owner-a", policy="private", limit=10, prior_generation=prior.generation
        )
    error = raised.value
    assert type(error) is native_knowledge.KnowledgeGenerationMismatch
    assert error.args == ("stale_authority",)
    assert error.__cause__ is None
    assert error.__context__ is None


def test_store_generation_advances_once_for_tombstone_and_zero_for_failed_mutations():
    store = NativeKnowledgeStore()
    current = store.create(
        owner_id="owner-a", knowledge_id="note-1", policy="private", content="alpha"
    )
    before_tombstone = store.generation

    with pytest.raises(KnowledgeAccessDenied):
        store.tombstone(
            owner_id="owner-a",
            knowledge_id="note-1",
            policy="shared",
            version=current.version,
            version_id=current.version_id,
        )
    assert store.generation == before_tombstone

    with pytest.raises(KnowledgeStoreError):
        store.tombstone(
            owner_id="owner-a",
            knowledge_id="note-1",
            policy="private",
            version=current.version,
            version_id="sha256:" + "0" * 64,
        )
    assert store.generation == before_tombstone

    store.tombstone(
        owner_id="owner-a",
        knowledge_id="note-1",
        policy="private",
        version=current.version,
        version_id=current.version_id,
    )
    assert store.generation == before_tombstone + 1

    with pytest.raises(KnowledgeTombstoned):
        store.tombstone(
            owner_id="owner-a",
            knowledge_id="note-1",
            policy="private",
            version=current.version,
            version_id=current.version_id,
        )
    assert store.generation == before_tombstone + 1


def test_store_returns_detached_values_and_snapshot_repr_is_content_free():
    store = NativeKnowledgeStore()
    created = store.create(
        owner_id="owner-a", knowledge_id="note-1", policy="private", content="alpha"
    )
    object.__setattr__(created, "content", "secret-shaped-caller-mutation")

    selected = store.read_exact(
        owner_id="owner-a", knowledge_id="note-1", policy="private", version=1
    )
    assert selected.content == "alpha"
    object.__setattr__(selected, "policy", "caller-mutated")

    exported = store.export(owner_id="owner-a", policy="private", limit=10)
    assert exported[0].policy == "private"
    assert exported[0] is not selected

    snapshot = store.capture(owner_id="owner-a", policy="private", limit=10)
    snapshot_repr = repr(snapshot)
    assert "owner-a" not in snapshot_repr
    assert "private" not in snapshot_repr
    assert "alpha" not in snapshot_repr
    object.__setattr__(snapshot.records[0], "content", "snapshot-caller-mutation")
    object.__setattr__(snapshot, "records", ())

    later = store.capture(owner_id="owner-a", policy="private", limit=10)
    assert later.records[0].content == "alpha"
    assert later.records[0] is not exported[0]


def test_generation_inputs_and_generation_mismatch_public_error_fail_closed():
    class IntSubclass(int):
        pass

    store = NativeKnowledgeStore()
    store.create(owner_id="owner-a", knowledge_id="note-1", policy="private", content="alpha")

    invalid_generations = (True, IntSubclass(0), 1.0, "1", -1)
    errors = []
    for invalid in invalid_generations:
        with pytest.raises(native_knowledge.KnowledgeGenerationMismatch) as raised:
            store.capture_exact_successor(
                owner_id="owner-a", policy="private", limit=10, prior_generation=invalid
            )
        errors.append(raised.value)

        with pytest.raises(native_knowledge.KnowledgeGenerationMismatch) as raised:
            store.read_exact_at_generation(
                owner_id="owner-a",
                knowledge_id="note-1",
                policy="private",
                version=1,
                expected_generation=invalid,
            )
        errors.append(raised.value)

    for invalid in (0, 1, 2):
        if invalid + 1 == store.generation:
            continue
        with pytest.raises(native_knowledge.KnowledgeGenerationMismatch) as raised:
            store.capture_exact_successor(
                owner_id="owner-a", policy="private", limit=10, prior_generation=invalid
            )
        errors.append(raised.value)

    assert len({id(error) for error in errors}) == len(errors)
    for error in errors:
        assert type(error) is native_knowledge.KnowledgeGenerationMismatch
        assert error.args == ("stale_authority",)
        assert error.__cause__ is None
        assert error.__context__ is None
        assert "owner-a" not in repr(error)
        assert "note-1" not in repr(error)
    assert store.generation == 1


def test_store_lock_linearizes_capture_mutation_and_generation_checked_read():
    store = NativeKnowledgeStore()
    target = store.create(
        owner_id="owner-a", knowledge_id="note-1", policy="private", content="alpha"
    )
    initial_generation = store.generation
    start = Barrier(3)
    create_done = Event()
    tombstone_done = Event()
    failures = []
    observed = []

    def create_other():
        try:
            start.wait()
            store.create(
                owner_id="owner-a", knowledge_id="note-2", policy="private", content="beta"
            )
        except BaseException as error:  # pragma: no cover - asserted through failures
            failures.append(error)
        finally:
            create_done.set()

    def tombstone_target():
        try:
            start.wait()
            store.tombstone(
                owner_id="owner-a",
                knowledge_id="note-1",
                policy="private",
                version=target.version,
                version_id=target.version_id,
            )
        except BaseException as error:  # pragma: no cover - asserted through failures
            failures.append(error)
        finally:
            tombstone_done.set()

    def observe():
        try:
            start.wait()
            while not (create_done.is_set() and tombstone_done.is_set()):
                snapshot = store.capture(owner_id="owner-a", policy="private", limit=10)
                record_ids = tuple(record.knowledge_id for record in snapshot.records)
                observed.append((snapshot.generation, record_ids))
                store.export(owner_id="owner-a", policy="private", limit=10)
                _ = store.generation
                try:
                    store.read_exact_at_generation(
                        owner_id="owner-a",
                        knowledge_id="note-1",
                        policy="private",
                        version=1,
                        expected_generation=snapshot.generation,
                    )
                except (
                    native_knowledge.KnowledgeGenerationMismatch,
                    KnowledgeTombstoned,
                ):
                    pass
        except BaseException as error:  # pragma: no cover - asserted through failures
            failures.append(error)

    threads = [Thread(target=create_other), Thread(target=tombstone_target), Thread(target=observe)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert failures == []
    allowed_states = {
        (initial_generation, ("note-1",)),
        (initial_generation + 1, ()),
        (initial_generation + 1, ("note-1", "note-2")),
        (initial_generation + 2, ("note-2",)),
    }
    assert all(state in allowed_states for state in observed)

    final = store.capture(owner_id="owner-a", policy="private", limit=10)
    assert final.generation == initial_generation + 2
    assert tuple(record.knowledge_id for record in final.records) == ("note-2",)
    with pytest.raises(native_knowledge.KnowledgeGenerationMismatch):
        store.capture_exact_successor(
            owner_id="owner-a", policy="private", limit=10, prior_generation=initial_generation
        )
