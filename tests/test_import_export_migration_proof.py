from src.import_export_migration_proof import (
    CountComparison,
    MigrationManifest,
    MigrationProof,
    MigrationProofError,
    MigrationProofStatus,
    SampleComparison,
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
