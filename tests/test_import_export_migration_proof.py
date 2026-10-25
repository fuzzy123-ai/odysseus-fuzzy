import ast
from dataclasses import FrozenInstanceError, asdict, fields, replace
import hashlib
import inspect
import json

import pytest

import src.import_export_migration_proof as comparison_module
from src.harbour_knowledge_store import KnowledgeHeadMetadata, KnowledgeMigrationSnapshot
from src.import_export_migration_proof import (
    CountComparison,
    HarbourMigrationComparison,
    HarbourMigrationComparisonError,
    HarbourMigrationDelta,
    MigrationManifest,
    MigrationProof,
    MigrationProofError,
    MigrationProofStatus,
    SampleComparison,
    compare_harbour_migration_snapshots,
)


def _make_manifest(**overrides) -> MigrationManifest:
    payload = {
        "manifest_ref": "manifest-src",
        "run_id": "export-run-1",
        "store_ref": "memory-store",
        "schema_version": "v1",
        "source_count": 10,
        "chunk_count": 20,
        "embedding_count": 20,
        "entity_count": 5,
        "relation_count": 4,
        "provenance_count": 20,
        "evidence_ref": "export manifest proof",
    }
    payload.update(overrides)
    return MigrationManifest.create(**payload)


def _make_count_comparison(**overrides) -> CountComparison:
    payload = {
        "counts_match": True,
        "source_manifest_ref": "manifest-src",
        "target_manifest_ref": "manifest-dst",
        "reason": "",
        "next_action": "",
    }
    payload.update(overrides)
    return CountComparison.create(**payload)


def _make_sample_comparison(**overrides) -> SampleComparison:
    payload = {
        "samples_match": True,
        "sample_size": 5,
        "reason": "",
        "next_action": "",
    }
    payload.update(overrides)
    return SampleComparison.create(**payload)


def _make_proof(**overrides) -> MigrationProof:
    payload = {
        "export_run_id": "export-run-1",
        "import_run_id": "import-run-1",
        "source_manifest_ref": "manifest-src",
        "target_manifest_ref": "manifest-dst",
        "backup_ref": "backup-001",
        "restore_ref": "restore-001",
        "count_comparison": _make_count_comparison(),
        "sample_comparison": _make_sample_comparison(),
        "read_only_compare": True,
        "rollback_plan": "restore backup and re-run compare",
        "go_no_go_status": "go",
        "proof_evidence_ref": "migration proof bundle",
        "reason": "",
        "next_action": "",
    }
    payload.update(overrides)
    return MigrationProof.create(**payload)


def test_valid_migration_proof_normalizes_stably() -> None:
    proof = _make_proof(
        export_run_id=" Export Run 1 ",
        go_no_go_status="ready_for_review",
        backup_ref="backup-001",
        restore_ref="restore-001",
        rollback_plan="restore backup and re-run compare",
        read_only_compare=True,
    )

    assert proof.export_run_id == "export-run-1"
    assert proof.go_no_go_status is MigrationProofStatus.READY_FOR_REVIEW
    assert proof.count_comparison.counts_match is True
    assert proof.sample_comparison.samples_match is True


def test_count_mismatch_without_reason_or_next_action_is_rejected() -> None:
    try:
        _make_count_comparison(counts_match=False, reason=" ", next_action=" ")
    except MigrationProofError as exc:
        assert "count mismatches require reason or next_action" in str(exc)
    else:
        raise AssertionError("expected count mismatch validation to fail")


def test_sample_mismatch_without_reason_or_next_action_is_rejected() -> None:
    try:
        _make_sample_comparison(samples_match=False, reason=" ", next_action=" ")
    except MigrationProofError as exc:
        assert "sample mismatches require reason or next_action" in str(exc)
    else:
        raise AssertionError("expected sample mismatch validation to fail")


def test_go_without_backup_restore_rollback_compare_is_rejected() -> None:
    try:
        _make_proof(backup_ref="", restore_ref="", rollback_plan="", read_only_compare=False, go_no_go_status="go")
    except MigrationProofError as exc:
        assert "go requires backup_ref" in str(exc)
    else:
        raise AssertionError("expected go readiness validation to fail")


def test_go_with_count_or_sample_mismatch_is_rejected() -> None:
    try:
        _make_proof(count_comparison=_make_count_comparison(counts_match=False, reason="counts off"))
    except MigrationProofError as exc:
        assert "matching counts and samples" in str(exc)
    else:
        raise AssertionError("expected go count mismatch validation to fail")

    try:
        _make_proof(sample_comparison=_make_sample_comparison(samples_match=False, reason="sample drift"))
    except MigrationProofError as exc:
        assert "matching counts and samples" in str(exc)
    else:
        raise AssertionError("expected go sample mismatch validation to fail")


def test_runtime_cutover_or_dual_write_claim_is_rejected() -> None:
    try:
        _make_proof(proof_evidence_ref="ready for dual-write cutover")
    except MigrationProofError as exc:
        assert "runtime cutover or dual-write claims" in str(exc)
    else:
        raise AssertionError("expected runtime-claim validation to fail")


def test_audit_summary_contains_manifest_refs_status_and_match_flags_without_long_dumps() -> None:
    proof = _make_proof(
        go_no_go_status="ready_for_review",
        proof_evidence_ref="evidence " + ("x" * 500),
    )

    summary = proof.audit_summary()

    assert summary["source_manifest_ref"] == "manifest-src"
    assert summary["target_manifest_ref"] == "manifest-dst"
    assert summary["go_no_go_status"] == "ready_for_review"
    assert summary["counts_match"] is True
    assert summary["samples_match"] is True
    assert summary["sample_size"] == 5
    assert "evidence" not in str(summary).lower()
    assert "x" * 200 not in str(summary)


_SNAPSHOT_SCHEMA = "odysseus.harbour.knowledge_migration_snapshot.v1"
_OWNER_DIGEST = "sha256:" + "a" * 64


def _snapshot_digest(kind, value):
    encoded = json.dumps(
        {"schema_id": _SNAPSHOT_SCHEMA, "kind": kind, "value": value},
        ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"),
    ).encode("ascii")
    return "sha256:" + hashlib.sha256(len(encoded).to_bytes(8, "big") + encoded).hexdigest()


def _comparison_head(index=1, *, owner=_OWNER_DIGEST, **overrides):
    metadata = {
        "knowledge_id_digest": f"sha256:{index:064x}",
        "revision_id_digest": "sha256:" + "1" * 64,
        "revision": 2, "generation": 3, "state": "active", "knowledge_kind": "note",
        "payload_sha256": "sha256:" + "2" * 64,
        "classification_digest": "sha256:" + "3" * 64,
        "retention_digest": "sha256:" + "4" * 64,
        "provenance_digest": "sha256:" + "5" * 64,
        "source_revisions_digest": "sha256:" + "6" * 64,
        "references_digest": "sha256:" + "7" * 64,
        "revision_metadata_digest": "sha256:" + "8" * 64,
        "head_metadata_digest": "sha256:" + "9" * 64,
    }
    metadata.update(overrides)
    return KnowledgeHeadMetadata(**metadata, record_digest=_snapshot_digest(
        "head_record", {"owner_ref_digest": owner, "metadata": metadata},
    ))


def _comparison_snapshot(heads=(), *, owner=_OWNER_DIGEST):
    records = [asdict(head) for head in heads]
    boundary = _snapshot_digest("head_boundary", {
        "owner_ref_digest": owner, "record_digests": [record["record_digest"] for record in records],
    })
    body = {"schema_id": _SNAPSHOT_SCHEMA, "owner_ref_digest": owner,
            "captured_head_boundary": boundary, "heads": records}
    return KnowledgeMigrationSnapshot(_SNAPSHOT_SCHEMA, owner, boundary, heads, _snapshot_digest("snapshot", body))


def _assert_invalid_comparison(call):
    try:
        raise RuntimeError("private ambient caller context")
    except RuntimeError:
        with pytest.raises(HarbourMigrationComparisonError) as caught:
            call()
    error = caught.value
    assert error.code == "invalid_comparison"
    assert error.args == ("invalid_comparison",)
    assert str(error) == "invalid_comparison"
    assert repr(error) == "HarbourMigrationComparisonError(code='invalid_comparison')"
    assert error.__context__ is None and error.__cause__ is None
    return error


def test_harbour_comparison_golden_and_empty_vectors():
    one = _comparison_snapshot((_comparison_head(),))
    empty = _comparison_snapshot()
    comparison = compare_harbour_migration_snapshots(one, one, empty)
    assert type(comparison) is HarbourMigrationComparison
    assert asdict(comparison) == {
        "schema_id": "odysseus.harbour.snapshot_comparison.v1",
        "owner_ref_digest": _OWNER_DIGEST,
        "source_snapshot_digest": "sha256:48ddc008cae663ec9fc72569d20601adde88584e6ce4b072d8c9a2caa8ac2673",
        "target_snapshot_digest": "sha256:48ddc008cae663ec9fc72569d20601adde88584e6ce4b072d8c9a2caa8ac2673",
        "post_snapshot_digest": "sha256:4718778ef552667ff40d0b9eef4317c03c219a41df34ec65c0eee6c5861ed281",
        "source_to_target": {"label": "source_to_target", "missing_knowledge_ids": (),
                             "extra_knowledge_ids": (), "changed_knowledge_ids": ()},
        "target_to_post": {"label": "target_to_post", "missing_knowledge_ids": ("sha256:" + "0" * 63 + "1",),
                           "extra_knowledge_ids": (), "changed_knowledge_ids": ()},
    }
    all_empty = compare_harbour_migration_snapshots(empty, empty, empty)
    assert all_empty.source_to_target == HarbourMigrationDelta("source_to_target", (), (), ())
    assert all_empty.target_to_post == HarbourMigrationDelta("target_to_post", (), (), ())
    assert comparison == compare_harbour_migration_snapshots(one, one, empty)
    for value in (comparison, comparison.source_to_target, comparison.target_to_post):
        assert not hasattr(value, "__dict__")
        with pytest.raises(FrozenInstanceError):
            setattr(value, fields(value)[0].name, "forged")
    assert repr(comparison) == "HarbourMigrationComparison(<metadata-only>)"
    assert repr(comparison.target_to_post) == "HarbourMigrationDelta(<metadata-only>)"


def test_harbour_comparison_missing_extra_changed_are_ordered_and_directional():
    source = _comparison_snapshot(tuple(_comparison_head(i) for i in (1, 2, 5, 6, 8)))
    target = _comparison_snapshot((
        _comparison_head(2, generation=4), _comparison_head(3), _comparison_head(4),
        _comparison_head(5), _comparison_head(8, state="tombstoned"),
    ))
    post = _comparison_snapshot((_comparison_head(2, generation=4), _comparison_head(3, generation=5), _comparison_head(9)))
    comparison = compare_harbour_migration_snapshots(source, target, post)
    ids = lambda *items: tuple(f"sha256:{item:064x}" for item in items)
    assert comparison.source_to_target == HarbourMigrationDelta("source_to_target", ids(1, 6), ids(3, 4), ids(2, 8))
    assert comparison.target_to_post == HarbourMigrationDelta("target_to_post", ids(4, 5, 8), ids(9), ids(3))


@pytest.mark.parametrize("slot", range(3))
@pytest.mark.parametrize("field,value", [
    ("schema_id", "foreign"), ("owner_ref_digest", "sha256:" + "b" * 64),
    ("captured_head_boundary", "sha256:" + "b" * 64),
    ("snapshot_digest", "sha256:" + "A" * 64),
    ("snapshot_digest", "sha256:" + "a" * 64 + "\n"),
    ("snapshot_digest", "x" * 129), ("schema_id", None),
])
def test_harbour_comparison_rejects_bad_envelope_in_each_slot(slot, field, value):
    values = [_comparison_snapshot() for _ in range(3)]
    values[slot] = replace(values[slot], **{field: value})
    _assert_invalid_comparison(lambda: compare_harbour_migration_snapshots(*values))


def test_harbour_comparison_rejects_different_valid_owner_even_for_empty_snapshots():
    empty = _comparison_snapshot()
    other = _comparison_snapshot(owner="sha256:" + "b" * 64)
    for values in ((empty, other, empty), (empty, empty, other), (other, empty, empty)):
        _assert_invalid_comparison(lambda: compare_harbour_migration_snapshots(*values))


@pytest.mark.parametrize("field", [field.name for field in fields(KnowledgeHeadMetadata)])
def test_harbour_comparison_binds_every_record_field(field):
    head = _comparison_head()
    changed = {"revision": 1, "generation": 4, "state": "purged", "knowledge_kind": "document"}.get(
        field, "sha256:" + "f" * 64,
    )
    invalid = _comparison_snapshot((replace(head, **{field: changed}),))
    _assert_invalid_comparison(lambda: compare_harbour_migration_snapshots(invalid, invalid, invalid))


@pytest.mark.parametrize("field,value", [
    ("revision", True), ("revision", 0), ("revision", 2**63), ("revision", 1.0),
    ("generation", False), ("generation", 1), ("state", "unknown"),
    ("state", "é"), ("knowledge_kind", "unknown"), ("payload_sha256", "sha256:" + "A" * 64),
])
def test_harbour_comparison_rejects_resealed_invalid_scalars(field, value):
    invalid = _comparison_snapshot((_comparison_head(**{field: value}),))
    _assert_invalid_comparison(lambda: compare_harbour_migration_snapshots(invalid, invalid, invalid))


def test_harbour_comparison_rejects_order_duplicates_and_over_budget():
    first, second = _comparison_head(1), _comparison_head(2)
    for heads in ((second, first), (first, first)):
        invalid = _comparison_snapshot(heads)
        _assert_invalid_comparison(lambda: compare_harbour_migration_snapshots(invalid, invalid, invalid))
    invalid = replace(_comparison_snapshot(), heads=(object(),) * 10_001)
    _assert_invalid_comparison(lambda: compare_harbour_migration_snapshots(invalid, invalid, invalid))


def test_harbour_comparison_accepts_ten_thousand_heads():
    snapshot = _comparison_snapshot(tuple(_comparison_head(i) for i in range(10_000)))
    empty = _comparison_snapshot()
    comparison = compare_harbour_migration_snapshots(empty, snapshot, empty)
    expected = tuple(f"sha256:{i:064x}" for i in range(10_000))
    assert comparison.source_to_target.extra_knowledge_ids == expected
    assert comparison.target_to_post.missing_knowledge_ids == expected


def test_harbour_comparison_rejects_hostile_types_without_invoking_protocols():
    calls = []

    def hostile(*args, **kwargs):
        calls.append(True)
        raise AssertionError("private hostile protocol")

    class Hostile:
        __class__ = property(hostile)
        __getattr__ = __iter__ = __str__ = __eq__ = hostile

    class SnapshotSubclass(KnowledgeMigrationSnapshot):
        __getattribute__ = hostile

    class HeadSubclass(KnowledgeHeadMetadata):
        __getattribute__ = hostile

    class TupleSubclass(tuple):
        __iter__ = __len__ = hostile

    class StringSubclass(str):
        encode = __eq__ = hostile

    class IntSubclass(int):
        __lt__ = __le__ = hostile

    valid = _comparison_snapshot((_comparison_head(),))
    invalids = [Hostile(), {}, iter(()), object.__new__(SnapshotSubclass), object.__new__(KnowledgeMigrationSnapshot)]
    for heads in ([], Hostile(), iter(valid.heads), TupleSubclass(valid.heads),
                  (Hostile(),), (object.__new__(HeadSubclass),), (object.__new__(KnowledgeHeadMetadata),)):
        invalids.append(replace(valid, heads=heads))
    for field, value in (("payload_sha256", Hostile()), ("payload_sha256", StringSubclass("sha256:" + "2" * 64)),
                         ("revision", IntSubclass(2)), ("state", Hostile())):
        invalids.append(replace(valid, heads=(replace(valid.heads[0], **{field: value}),)))
    for invalid in invalids:
        _assert_invalid_comparison(lambda: compare_harbour_migration_snapshots(valid, invalid, valid))
    assert calls == []


def test_harbour_comparison_clones_all_inputs_before_validation(monkeypatch):
    snapshots = [_comparison_snapshot((_comparison_head(i),)) for i in (1, 2, 3)]
    expected = compare_harbour_migration_snapshots(*snapshots)
    validate = comparison_module.project_harbour_knowledge_metadata
    calls = []

    def mutate_callers_then_validate(clone):
        calls.append(clone)
        for original in snapshots:
            assert clone is not original
            assert clone.heads[0] is not original.heads[0]
            object.__setattr__(original.heads[0], "record_digest", "private mutated record")
            object.__setattr__(original, "snapshot_digest", "private mutated snapshot")
        return validate(clone)

    monkeypatch.setattr(comparison_module, "project_harbour_knowledge_metadata", mutate_callers_then_validate)
    actual = compare_harbour_migration_snapshots(*snapshots)
    assert len(calls) == 3 and actual == expected
    object.__setattr__(snapshots[0], "owner_ref_digest", "private mutated owner")
    assert actual.owner_ref_digest == _OWNER_DIGEST
    _assert_invalid_comparison(lambda: compare_harbour_migration_snapshots(*snapshots))


def test_harbour_comparison_validator_errors_are_fresh_and_context_free(monkeypatch):
    def fail(*args):
        raise RuntimeError("private validator exception")

    monkeypatch.setattr(comparison_module, "project_harbour_knowledge_metadata", fail)
    empty = _comparison_snapshot()
    errors = [_assert_invalid_comparison(lambda: compare_harbour_migration_snapshots(empty, empty, empty)) for _ in range(2)]
    assert errors[0] is not errors[1]


def test_harbour_comparison_has_no_legacy_or_io_operations(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("comparison crossed authority boundary")

    for name in ("MigrationProof", "MigrationProofStatus", "MigrationManifest", "CountComparison", "SampleComparison"):
        monkeypatch.setattr(comparison_module, name, forbidden)
    empty = _comparison_snapshot()
    result = compare_harbour_migration_snapshots(empty, empty, empty)
    assert not {"go_no_go_status", "evidence", "ready", "authority", "content"}.intersection(asdict(result))
    tree = ast.parse(inspect.getsource(comparison_module))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and
                 (node.name.startswith("_harbour_comparison_") or node.name == "compare_harbour_migration_snapshots")]
    forbidden_names = {"MigrationProof", "MigrationProofStatus", "MigrationManifest", "CountComparison", "SampleComparison",
                       "open", "eval", "exec", "__import__", "NativeKnowledgeStore", "KnowledgeSourceAdapter",
                       "HarbourKnowledgeStore", "knowledge_source_registration"}
    forbidden_attributes = {"capture", "capture_migration_snapshot", "connect", "execute", "read", "write", "now", "random"}
    for function in functions:
        for node in ast.walk(function):
            if isinstance(node, ast.Name):
                assert node.id not in forbidden_names
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden_attributes
