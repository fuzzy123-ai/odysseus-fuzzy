from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
import hashlib
import json

import pytest

import src.harbour_snapshot_comparison as comparison
from src.harbour_knowledge_store import KnowledgeHeadMetadata, KnowledgeMigrationSnapshot


_SCHEMA = "odysseus.harbour.knowledge_migration_snapshot.v1"
_OWNER = "sha256:" + "a" * 64


def _digest(kind: str, value: object) -> str:
    encoded = json.dumps(
        {"schema_id": _SCHEMA, "kind": kind, "value": value},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ).encode("ascii")
    return "sha256:" + hashlib.sha256(len(encoded).to_bytes(8, "big") + encoded).hexdigest()


def _head(index: int = 1, *, owner: str = _OWNER, **overrides: object) -> KnowledgeHeadMetadata:
    values: dict[str, object] = {
        "knowledge_id_digest": f"sha256:{index:064x}",
        "revision_id_digest": "sha256:" + "1" * 64,
        "revision": 2,
        "generation": 3,
        "state": "active",
        "knowledge_kind": "note",
        "payload_sha256": "sha256:" + "2" * 64,
        "classification_digest": "sha256:" + "3" * 64,
        "retention_digest": "sha256:" + "4" * 64,
        "provenance_digest": "sha256:" + "5" * 64,
        "source_revisions_digest": "sha256:" + "6" * 64,
        "references_digest": "sha256:" + "7" * 64,
        "revision_metadata_digest": "sha256:" + "8" * 64,
        "head_metadata_digest": "sha256:" + "9" * 64,
    }
    values.update(overrides)
    return KnowledgeHeadMetadata(**values, record_digest=_digest(
        "head_record", {"owner_ref_digest": owner, "metadata": values},
    ))


def _snapshot(
    heads: tuple[KnowledgeHeadMetadata, ...] | None = None,
    *,
    schema: str = _SCHEMA,
    owner: str = _OWNER,
) -> KnowledgeMigrationSnapshot:
    heads = (_head(owner=owner),) if heads is None else heads
    records = [{field.name: getattr(head, field.name) for field in fields(KnowledgeHeadMetadata)} for head in heads]
    boundary = _digest("head_boundary", {"owner_ref_digest": owner, "record_digests": [
        record["record_digest"] for record in records
    ]})
    body = {
        "schema_id": schema,
        "owner_ref_digest": owner,
        "captured_head_boundary": boundary,
        "heads": records,
    }
    return KnowledgeMigrationSnapshot(schema, owner, boundary, heads, _digest("snapshot", body))


def _assert_invalid(call) -> comparison.HarbourSnapshotComparisonError:
    with pytest.raises(comparison.HarbourSnapshotComparisonError) as caught:
        call()
    error = caught.value
    assert error.code == "invalid_comparison"
    assert str(error) == "invalid_comparison"
    assert repr(error) == "HarbourSnapshotComparisonError(<invalid>)"
    assert error.__context__ is None and error.__cause__ is None
    return error


def test_golden_equal_and_empty_snapshots_are_redacted_and_frozen() -> None:
    vector = _snapshot((_head(1), _head(2)))
    result = comparison.compare_harbour_snapshot_fixtures(vector, vector, vector)

    assert result.schema_id == "odysseus.harbour.snapshot_comparison.v1"
    assert result.owner_ref_digest == _OWNER
    assert (result.source_snapshot_digest, result.target_snapshot_digest, result.post_snapshot_digest) == (
        vector.snapshot_digest,
        vector.snapshot_digest,
        vector.snapshot_digest,
    )
    assert result.source_to_target == comparison.HarbourSnapshotDelta("source_to_target", (), (), ())
    assert result.target_to_post == comparison.HarbourSnapshotDelta("target_to_post", (), (), ())
    assert repr(result) == "HarbourSnapshotComparison(<redacted>)"
    assert repr(result.source_to_target) == "HarbourSnapshotDelta(<redacted>)"
    assert not hasattr(result, "__dict__") and not hasattr(result.source_to_target, "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.schema_id = "other"

    empty = _snapshot(())
    assert comparison.compare_harbour_snapshot_fixtures(empty, empty, empty).source_to_target.missing_knowledge_ids == ()


def test_changed_missing_extra_and_two_delta_ordering_are_lexical() -> None:
    one, two, three = _head(1), _head(2), _head(3)
    source = _snapshot((one, two))
    target = _snapshot((_head(1, payload_sha256="sha256:" + "a" * 64), three))
    post = _snapshot((_head(1, payload_sha256="sha256:" + "a" * 64), two,
                      _head(3, state="purged")))

    result = comparison.compare_harbour_snapshot_fixtures(source, target, post)

    assert result.source_to_target.missing_knowledge_ids == (two.knowledge_id_digest,)
    assert result.source_to_target.extra_knowledge_ids == (three.knowledge_id_digest,)
    assert result.source_to_target.changed_knowledge_ids == (one.knowledge_id_digest,)
    assert result.target_to_post.missing_knowledge_ids == ()
    assert result.target_to_post.extra_knowledge_ids == (two.knowledge_id_digest,)
    assert result.target_to_post.changed_knowledge_ids == (three.knowledge_id_digest,)
    for delta in (result.source_to_target, result.target_to_post):
        for value in (delta.missing_knowledge_ids, delta.extra_knowledge_ids, delta.changed_knowledge_ids):
            assert value == tuple(sorted(value))


@pytest.mark.parametrize("field,value", [
    ("schema_id", "foreign"),
    ("owner_ref_digest", "sha256:" + "b" * 64),
    ("snapshot_digest", "sha256:" + "A" * 64),
    ("heads", ()),
])
def test_schema_owner_and_envelope_rejections_are_context_free(field: str, value: object) -> None:
    valid = _snapshot()
    forged = replace(valid, **{field: value})
    _assert_invalid(lambda: comparison.compare_harbour_snapshot_fixtures(forged, valid, valid))


def test_alternate_owner_snapshot_projects_before_common_owner_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_a = _OWNER
    owner_b = "sha256:" + "b" * 64
    source = _snapshot(owner=owner_a)
    target = _snapshot(owner=owner_b)
    post = _snapshot(owner=owner_a)
    observed: list[KnowledgeMigrationSnapshot] = []
    original = comparison.project_harbour_knowledge_metadata

    def observe(clone: KnowledgeMigrationSnapshot):
        observed.append(clone)
        return original(clone)

    monkeypatch.setattr(comparison, "project_harbour_knowledge_metadata", observe)
    _assert_invalid(lambda: comparison.compare_harbour_snapshot_fixtures(source, target, post))

    assert len(observed) == 3
    assert tuple(snapshot.owner_ref_digest for snapshot in observed) == (owner_a, owner_b, owner_a)
    for clone, original_input in zip(observed, (source, target, post)):
        assert clone == original_input
        assert clone is not original_input
        assert clone.heads == original_input.heads
        assert clone.heads is not original_input.heads
        for cloned_head, original_head in zip(clone.heads, original_input.heads):
            assert cloned_head == original_head
            assert cloned_head is not original_head


def test_exact_type_slots_digest_order_duplicate_and_budget_rejections_are_context_free() -> None:
    valid = _snapshot()

    class SnapshotChild(KnowledgeMigrationSnapshot):
        def __getattribute__(self, name: str):
            raise AssertionError("unexpected slot read")

    class HeadChild(KnowledgeHeadMetadata):
        def __getattribute__(self, name: str):
            raise AssertionError("unexpected slot read")

    class TextChild(str):
        def encode(self, *args: object, **kwargs: object) -> bytes:
            raise AssertionError("unexpected encoding")

    class CountChild(int):
        def __lt__(self, other: object) -> bool:
            raise AssertionError("unexpected comparison")

    malformed = replace(valid, heads=(replace(valid.heads[0], payload_sha256="sha256:" + "z" * 64),))
    unordered = _snapshot((_head(2), _head(1)))
    duplicate = _snapshot((_head(1), _head(1)))
    too_many = replace(valid, heads=(valid.heads[0],) * 10_001)
    missing = object.__new__(KnowledgeMigrationSnapshot)
    hostile = [
        object.__new__(SnapshotChild), object.__new__(HeadChild), {}, iter(()), missing,
        replace(valid, heads=(replace(valid.heads[0], revision=True),)),
        replace(valid, heads=(replace(valid.heads[0], revision=CountChild(2)),)),
        replace(valid, heads=(replace(valid.heads[0], state=TextChild("active")),)),
        malformed, unordered, duplicate, too_many,
    ]
    for item in hostile:
        _assert_invalid(lambda item=item: comparison.compare_harbour_snapshot_fixtures(item, valid, valid))


def test_all_clones_precede_projection_and_inputs_cannot_split_result(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _snapshot((_head(1),))
    target = _snapshot((_head(2),))
    post = _snapshot((_head(3),))
    before = (source.snapshot_digest, target.snapshot_digest, post.snapshot_digest)
    original = comparison.project_harbour_knowledge_metadata
    seen: list[KnowledgeMigrationSnapshot] = []

    def observe(clone: KnowledgeMigrationSnapshot):
        seen.append(clone)
        if len(seen) == 1:
            object.__setattr__(target, "heads", ())
            object.__setattr__(post, "owner_ref_digest", "sha256:" + "f" * 64)
        return original(clone)

    monkeypatch.setattr(comparison, "project_harbour_knowledge_metadata", observe)
    result = comparison.compare_harbour_snapshot_fixtures(source, target, post)

    assert [snapshot is not original_input for snapshot, original_input in zip(seen, (source, target, post))] == [True] * 3
    assert (result.source_snapshot_digest, result.target_snapshot_digest, result.post_snapshot_digest) == before
    assert result.source_to_target.extra_knowledge_ids == (_head(2).knowledge_id_digest,)
    assert result.target_to_post.extra_knowledge_ids == (_head(3).knowledge_id_digest,)


def test_projection_and_unexpected_failures_are_context_free(monkeypatch: pytest.MonkeyPatch) -> None:
    valid = _snapshot()

    def fail(_: KnowledgeMigrationSnapshot):
        raise RuntimeError("hostile ambient detail")

    monkeypatch.setattr(comparison, "project_harbour_knowledge_metadata", fail)
    first = _assert_invalid(lambda: comparison.compare_harbour_snapshot_fixtures(valid, valid, valid))
    second = _assert_invalid(lambda: comparison.compare_harbour_snapshot_fixtures(valid, valid, valid))
    assert first is not second


def test_output_has_no_mutable_input_aliases() -> None:
    source, target, post = _snapshot(), _snapshot((_head(2),)), _snapshot((_head(3),))
    result = comparison.compare_harbour_snapshot_fixtures(source, target, post)
    expected = result.source_to_target.extra_knowledge_ids
    object.__setattr__(target, "snapshot_digest", "sha256:" + "f" * 64)
    object.__setattr__(target, "heads", ())
    assert result.source_to_target.extra_knowledge_ids == expected
    assert type(result.source_to_target.extra_knowledge_ids) is tuple


def test_module_surface_and_code_metadata_are_supplementary_in_memory_proofs() -> None:
    namespace = vars(comparison)
    compare = namespace["compare_harbour_snapshot_fixtures"]

    def code_tree(code: type(compare.__code__)):
        yield code
        for constant in code.co_consts:
            if type(constant) is type(code):
                yield from code_tree(constant)

    assert tuple(namespace["__all__"]) == (
        "HarbourSnapshotComparison",
        "HarbourSnapshotComparisonError",
        "HarbourSnapshotDelta",
        "compare_harbour_snapshot_fixtures",
    )
    assert not hasattr(comparison, "compare_harbour_migration_snapshots")
    assert compare is comparison.compare_harbour_snapshot_fixtures
    assert compare.__code__.co_argcount == 3
    assert compare.__code__.co_kwonlyargcount == 0
    assert compare.__code__.co_freevars == ()
    function_type = type(compare)
    source_functions = [
        value for value in namespace.values()
        if type(value) is function_type and value.__module__ == comparison.__name__
    ]
    source_methods = [
        method for value in namespace.values()
        if type(value) is type and value.__module__ == comparison.__name__
        for method in vars(value).values() if type(method) is function_type
    ]
    test_functions = [
        value for value in globals().values()
        if type(value) is function_type and value.__module__ == __name__
    ]
    prohibited_interfaces = frozenset((
        "__import__", "__loader__", "__file__", "ast", "compile", "dis", "eval", "exec",
        "get_source", "import_module", "marshal", "open", "read", "resources",
    ))
    for function in source_functions + source_methods + test_functions:
        assert not any(
            name in prohibited_interfaces
            for code in code_tree(function.__code__)
            for name in code.co_names
        )
