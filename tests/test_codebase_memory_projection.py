from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from src.code_intelligence_contract import (
    CodeFileMapping,
    ExtractionEvidence,
    ExtractionMethod,
)
from src.codebase_memory_projection import (
    CodebaseMemoryProjectionError,
    GenerationState,
    OneWayRepositoryBridge,
    ProjectionGenerationStore,
)
from src.project_version_store import (
    StoredProjectVersion,
    VERSION_MANIFEST_SCHEMA,
    canonical_json_bytes,
    owner_key_for,
)
from src.repo_registry import RepoRecord, RepoRegistry
from src.unified_source_index_contract import (
    Classification,
    ContentPolicy,
    SourceKind,
    SourceRecord,
    SourceVersionRecord,
    content_hash,
)


COMMIT = "a" * 40
SNAPSHOT_A = "usi_snapshot_" + "1" * 64
SNAPSHOT_B = "usi_snapshot_" + "2" * 64
NOW = "2026-07-18T10:00:00Z"


def _registry(tmp_path: Path, *, owner: str = "alice") -> RepoRegistry:
    record = RepoRecord.create(
        repo_id="demo",
        title="Demo",
        owner=owner,
        workspace_root="repos",
        project_root="repos/demo",
        path_ref="repos/demo",
        created_at=NOW,
        current_branch="main",
        allowed_actions=("status",),
    )
    registry = RepoRegistry()
    registry.add(record)
    return registry


def _project_version(*, owner: str = "alice", repo_id: str = "demo", commit: str = COMMIT):
    manifest = {
        "schema": VERSION_MANIFEST_SCHEMA,
        "owner_key": owner_key_for(owner),
        "repo_id": repo_id,
        "transaction_id": "pct_" + "b" * 32,
        "version_id": "pv_" + "c" * 32,
        "commit_sha": commit,
        "created_at": NOW,
        "policy_snapshot": {"schema": "policy.v1", "mode": "local"},
        "artifacts": [],
    }
    digest = "sha256:" + hashlib.sha256(canonical_json_bytes(manifest)).hexdigest()
    return StoredProjectVersion(manifest=manifest, manifest_sha256=digest)


def _inputs(
    *,
    repo_id: str = "demo",
    owner: str = "alice",
    commit: str = COMMIT,
    relative_path: str = "src/main.py",
    engine_file_ref: str = "file-fixture",
):
    extraction = ExtractionEvidence(
        ExtractionMethod.CBM_PARSER,
        0.95,
        "cbm",
        "0.9.0",
        False,
    )
    source = SourceRecord(
        owner_scope=f"user:{owner}",
        source_kind=SourceKind.CODE,
        canonical_ref=f"repo:{repo_id}/{relative_path}",
        classification=Classification.PRIVATE,
        content_policy=ContentPolicy.REFERENCE_ONLY,
        provider_ref="local-git",
    )
    version = SourceVersionRecord.create(
        source,
        revision_ref=f"git:{commit}",
        content_hash=content_hash("fixture code"),
        version_observed_at=NOW,
    )
    mapping = CodeFileMapping.create(
        source,
        version,
        repo_id=repo_id,
        relative_path=relative_path,
        byte_length=12,
        engine_project_ref="project-fixture",
        engine_file_ref=engine_file_ref,
        evidence=extraction,
    )
    return (mapping,), (version.evidence_ref(),)


def _plan(
    tmp_path: Path,
    *,
    snapshot: str = SNAPSHOT_A,
    registry: RepoRegistry | None = None,
    project_version: StoredProjectVersion | None = None,
):
    registry = registry or _registry(tmp_path)
    project_version = project_version or _project_version()
    mappings, evidence = _inputs()
    plan = OneWayRepositoryBridge().create_plan(
        registry,
        repo_id="demo",
        project_version=project_version,
        input_snapshot_ref=snapshot,
        file_mappings=mappings,
        input_evidence=evidence,
    )
    return registry, project_version, mappings, evidence, plan


def test_plan_is_deterministic_content_free_and_does_not_mutate_authorities(tmp_path: Path):
    registry = _registry(tmp_path)
    project_version = _project_version()
    mappings, evidence = _inputs()
    registry_before = json.dumps(registry.to_dict(), sort_keys=True)
    version_before = json.dumps(project_version.to_dict(), sort_keys=True)
    evidence_before = tuple(item.to_json() for item in evidence)
    bridge = OneWayRepositoryBridge()

    first = bridge.create_plan(
        registry,
        repo_id="demo",
        project_version=project_version,
        input_snapshot_ref=SNAPSHOT_A,
        file_mappings=mappings,
        input_evidence=evidence,
    )
    second = bridge.create_plan(
        registry,
        repo_id="demo",
        project_version=project_version,
        input_snapshot_ref=SNAPSHOT_A,
        file_mappings=mappings,
        input_evidence=evidence,
    )

    assert first == second
    assert first.to_dict() == second.to_dict()
    assert json.dumps(registry.to_dict(), sort_keys=True) == registry_before
    assert json.dumps(project_version.to_dict(), sort_keys=True) == version_before
    assert tuple(item.to_json() for item in evidence) == evidence_before
    rendered = json.dumps(first.to_dict(), sort_keys=True)
    assert str(tmp_path) not in rendered
    assert "workspace_root" not in rendered
    assert "project_root" not in rendered
    assert "path_ref" not in rendered


def test_plan_binds_registry_version_usi_inputs_and_all_controls_off(tmp_path: Path):
    _registry_value, project_version, mappings, evidence, plan = _plan(tmp_path)
    config = plan.engine_config

    assert plan.repo_id == "demo"
    assert plan.project_version_id == project_version.version_id
    assert plan.commit_sha == COMMIT
    assert plan.generation_ref.startswith("cbm_generation_")
    assert plan.plan_id.startswith("cbm_plan_")
    assert config.project_key.startswith("cbm_project_")
    assert config.source_root_ref.startswith("path_sha256:")
    assert config.input_snapshot_ref == SNAPSHOT_A
    assert config.file_mapping_keys == (mappings[0].fallback_key,)
    assert config.input_record_ids == (evidence[0].record_id,)
    assert all(value is False for value in config.to_dict()["controls"].values())
    assert plan.usi_manifest.output_generation_ref == plan.generation_ref
    assert plan.usi_manifest.projection_kind.value == "code_graph"
    assert plan.usi_manifest.content_policy is ContentPolicy.METADATA_ONLY
    assert plan.usi_manifest.input_evidence == evidence


def test_engine_config_or_plan_identity_tampering_fails_closed(tmp_path: Path):
    *_values, plan = _plan(tmp_path)
    with pytest.raises(CodebaseMemoryProjectionError, match="config_hash"):
        replace(plan.engine_config, config_hash="sha256:" + "f" * 64)
    with pytest.raises(CodebaseMemoryProjectionError, match="plan_id"):
        replace(plan, plan_id="cbm_plan_" + "f" * 64)
    with pytest.raises(CodebaseMemoryProjectionError, match="authority_digest"):
        replace(plan, authority_digest="sha256:" + "f" * 64)


def test_version_owner_repo_or_digest_mismatch_fails_closed(tmp_path: Path):
    registry = _registry(tmp_path)
    mappings, evidence = _inputs()
    bridge = OneWayRepositoryBridge()

    for version in (
        _project_version(owner="mallory"),
        _project_version(repo_id="other"),
        replace(_project_version(), manifest_sha256="sha256:" + "f" * 64),
    ):
        with pytest.raises(CodebaseMemoryProjectionError):
            bridge.create_plan(
                registry,
                repo_id="demo",
                project_version=version,
                input_snapshot_ref=SNAPSHOT_A,
                file_mappings=mappings,
                input_evidence=evidence,
            )


def test_file_mapping_must_match_registry_owner_repo_and_project_commit(tmp_path: Path):
    registry = _registry(tmp_path)
    project_version = _project_version()
    bridge = OneWayRepositoryBridge()

    for mapping_values, evidence_values in (
        _inputs(repo_id="other"),
        _inputs(owner="mallory"),
        _inputs(commit="d" * 40),
    ):
        with pytest.raises(CodebaseMemoryProjectionError):
            bridge.create_plan(
                registry,
                repo_id="demo",
                project_version=project_version,
                input_snapshot_ref=SNAPSHOT_A,
                file_mappings=mapping_values,
                input_evidence=evidence_values,
            )


def test_evidence_must_belong_to_mapped_source_version(tmp_path: Path):
    registry = _registry(tmp_path)
    mappings, _evidence = _inputs()
    _other_mappings, other_evidence = _inputs(commit="d" * 40)

    with pytest.raises(CodebaseMemoryProjectionError, match="escapes"):
        OneWayRepositoryBridge().create_plan(
            registry,
            repo_id="demo",
            project_version=_project_version(),
            input_snapshot_ref=SNAPSHOT_A,
            file_mappings=mappings,
            input_evidence=other_evidence,
        )


def test_duplicate_or_empty_projection_inputs_fail_closed(tmp_path: Path):
    registry = _registry(tmp_path)
    mappings, evidence = _inputs()
    bridge = OneWayRepositoryBridge()

    for mapping_values, evidence_values in (
        ((), evidence),
        (mappings, ()),
        ((mappings[0], mappings[0]), evidence),
        (mappings, (evidence[0], evidence[0])),
    ):
        with pytest.raises(CodebaseMemoryProjectionError):
            bridge.create_plan(
                registry,
                repo_id="demo",
                project_version=_project_version(),
                input_snapshot_ref=SNAPSHOT_A,
                file_mappings=mapping_values,
                input_evidence=evidence_values,
            )


def test_every_mapped_source_version_requires_usi_evidence(tmp_path: Path):
    registry = _registry(tmp_path)
    first_mappings, first_evidence = _inputs()
    second_mappings, second_evidence = _inputs(
        relative_path="src/other.py",
        engine_file_ref="file-other",
    )
    mappings = first_mappings + second_mappings
    bridge = OneWayRepositoryBridge()

    with pytest.raises(CodebaseMemoryProjectionError, match="every mapped"):
        bridge.create_plan(
            registry,
            repo_id="demo",
            project_version=_project_version(),
            input_snapshot_ref=SNAPSHOT_A,
            file_mappings=mappings,
            input_evidence=first_evidence,
        )

    plan = bridge.create_plan(
        registry,
        repo_id="demo",
        project_version=_project_version(),
        input_snapshot_ref=SNAPSHOT_A,
        file_mappings=mappings,
        input_evidence=first_evidence + second_evidence,
    )
    assert len(plan.engine_config.file_mapping_keys) == 2
    assert len(plan.usi_manifest.input_evidence) == 2


def test_authority_verification_detects_registry_drift_without_writing_back(tmp_path: Path):
    registry, project_version, mappings, evidence, plan = _plan(tmp_path)
    bridge = OneWayRepositoryBridge()
    assert bridge.verify_authority_unchanged(
        plan,
        registry,
        project_version=project_version,
        file_mappings=mappings,
        input_evidence=evidence,
    )

    current = registry.get("demo")
    registry.put(
        current.with_policy(
            allowed_actions=("status", "log"),
            updated_at="2026-07-18T11:00:00Z",
        )
    )
    assert not bridge.verify_authority_unchanged(
        plan,
        registry,
        project_version=project_version,
        file_mappings=mappings,
        input_evidence=evidence,
    )


def test_engine_registration_backflow_is_explicitly_rejected(tmp_path: Path):
    registry = _registry(tmp_path)
    before = json.dumps(registry.to_dict(), sort_keys=True)

    with pytest.raises(CodebaseMemoryProjectionError, match="reverse authority"):
        OneWayRepositoryBridge().reject_engine_registration(
            {"project": "new", "repository": "engine-owned"}
        )
    assert json.dumps(registry.to_dict(), sort_keys=True) == before


def test_prepare_and_first_activation_use_compare_and_switch(tmp_path: Path):
    *_values, plan = _plan(tmp_path)
    store = ProjectionGenerationStore()
    prepared = store.prepare(plan)
    active = store.activate(plan.generation_ref, expected_active_ref="")

    assert prepared.state is GenerationState.PREPARED
    assert active.state is GenerationState.ACTIVE
    assert active.state_revision == 2
    assert store.active_generation_ref == plan.generation_ref
    assert store.get(plan.generation_ref) == active


def test_prepare_is_idempotent_but_identity_collision_is_impossible(tmp_path: Path):
    *_values, plan = _plan(tmp_path)
    store = ProjectionGenerationStore()
    first = store.prepare(plan)
    second = store.prepare(plan)

    assert first == second
    assert store.list() == (first,)


def test_activation_conflict_is_atomic_and_preserves_current_pointer(tmp_path: Path):
    *_values, first_plan = _plan(tmp_path, snapshot=SNAPSHOT_A)
    *_other, second_plan = _plan(tmp_path, snapshot=SNAPSHOT_B)
    store = ProjectionGenerationStore()
    store.prepare(first_plan)
    store.activate(first_plan.generation_ref, expected_active_ref="")
    store.prepare(second_plan)
    before = tuple(item.to_dict() for item in store.list())

    with pytest.raises(CodebaseMemoryProjectionError, match="compare-and-switch"):
        store.activate(second_plan.generation_ref, expected_active_ref="")
    assert store.active_generation_ref == first_plan.generation_ref
    assert tuple(item.to_dict() for item in store.list()) == before


def test_failed_prepared_generation_does_not_disturb_active_generation(tmp_path: Path):
    *_values, first_plan = _plan(tmp_path, snapshot=SNAPSHOT_A)
    *_other, second_plan = _plan(tmp_path, snapshot=SNAPSHOT_B)
    store = ProjectionGenerationStore()
    store.prepare(first_plan)
    store.activate(first_plan.generation_ref, expected_active_ref="")
    store.prepare(second_plan)
    failed = store.mark_failed(second_plan.generation_ref, error_code="build_failed")

    assert failed.state is GenerationState.FAILED
    assert failed.error_code == "build_failed"
    assert store.active_generation_ref == first_plan.generation_ref
    assert store.get(first_plan.generation_ref).state is GenerationState.ACTIVE
    with pytest.raises(CodebaseMemoryProjectionError, match="prepared"):
        store.activate(
            second_plan.generation_ref,
            expected_active_ref=first_plan.generation_ref,
        )


def test_stale_state_is_explicit_and_keeps_pointer_for_safe_fallback(tmp_path: Path):
    *_values, plan = _plan(tmp_path)
    store = ProjectionGenerationStore()
    store.prepare(plan)
    store.activate(plan.generation_ref, expected_active_ref="")
    stale = store.mark_stale(plan.generation_ref)

    assert stale.state is GenerationState.STALE
    assert store.active_generation_ref == plan.generation_ref


def test_generation_switch_marks_previous_stale_transactionally(tmp_path: Path):
    *_values, first_plan = _plan(tmp_path, snapshot=SNAPSHOT_A)
    *_other, second_plan = _plan(tmp_path, snapshot=SNAPSHOT_B)
    store = ProjectionGenerationStore()
    store.prepare(first_plan)
    store.activate(first_plan.generation_ref, expected_active_ref="")
    store.prepare(second_plan)
    second = store.activate(
        second_plan.generation_ref,
        expected_active_ref=first_plan.generation_ref,
    )

    assert second.state is GenerationState.ACTIVE
    assert second.previous_generation_ref == first_plan.generation_ref
    assert store.get(first_plan.generation_ref).state is GenerationState.STALE
    assert store.active_generation_ref == second_plan.generation_ref


def test_delete_and_rebuild_are_projection_only_and_reproduce_identity(tmp_path: Path):
    registry, project_version, mappings, evidence, plan = _plan(tmp_path)
    bridge = OneWayRepositoryBridge()
    registry_before = json.dumps(registry.to_dict(), sort_keys=True)
    version_before = json.dumps(project_version.to_dict(), sort_keys=True)
    store = ProjectionGenerationStore()
    store.prepare(plan)
    store.activate(plan.generation_ref, expected_active_ref="")

    receipt = store.delete_all(expected_active_ref=plan.generation_ref)
    assert receipt.deleted_generation_count == 1
    assert receipt.canonical_writes == 0
    assert store.active_generation_ref == ""
    assert store.list() == ()
    assert json.dumps(registry.to_dict(), sort_keys=True) == registry_before
    assert json.dumps(project_version.to_dict(), sort_keys=True) == version_before
    assert bridge.verify_authority_unchanged(
        plan,
        registry,
        project_version=project_version,
        file_mappings=mappings,
        input_evidence=evidence,
    )

    rebuilt_plan = bridge.create_plan(
        registry,
        repo_id="demo",
        project_version=project_version,
        input_snapshot_ref=SNAPSHOT_A,
        file_mappings=mappings,
        input_evidence=evidence,
    )
    assert rebuilt_plan == plan
    rebuilt = store.prepare(rebuilt_plan)
    assert rebuilt.generation_ref == plan.generation_ref
    assert rebuilt.state is GenerationState.PREPARED


def test_delete_compare_and_switch_conflict_preserves_every_generation(tmp_path: Path):
    *_values, plan = _plan(tmp_path)
    store = ProjectionGenerationStore()
    store.prepare(plan)
    store.activate(plan.generation_ref, expected_active_ref="")

    with pytest.raises(CodebaseMemoryProjectionError, match="compare-and-switch"):
        store.delete_all(expected_active_ref="")
    assert store.active_generation_ref == plan.generation_ref
    assert len(store.list()) == 1


def test_unknown_generation_and_invalid_state_transitions_fail_closed(tmp_path: Path):
    *_values, plan = _plan(tmp_path)
    store = ProjectionGenerationStore()
    with pytest.raises(CodebaseMemoryProjectionError, match="unknown"):
        store.get("cbm_generation_" + "f" * 64)

    store.prepare(plan)
    store.mark_failed(plan.generation_ref, error_code="build_failed")
    with pytest.raises(CodebaseMemoryProjectionError):
        store.mark_stale(plan.generation_ref)
    with pytest.raises(CodebaseMemoryProjectionError):
        store.mark_failed(plan.generation_ref, error_code="again")


def test_changed_usi_snapshot_produces_a_new_generation_without_canonical_changes(tmp_path: Path):
    registry = _registry(tmp_path)
    project_version = _project_version()
    mappings, evidence = _inputs()
    bridge = OneWayRepositoryBridge()
    first = bridge.create_plan(
        registry,
        repo_id="demo",
        project_version=project_version,
        input_snapshot_ref=SNAPSHOT_A,
        file_mappings=mappings,
        input_evidence=evidence,
    )
    second = bridge.create_plan(
        registry,
        repo_id="demo",
        project_version=project_version,
        input_snapshot_ref=SNAPSHOT_B,
        file_mappings=mappings,
        input_evidence=evidence,
    )

    assert first.registry_digest == second.registry_digest
    assert first.version_manifest_digest == second.version_manifest_digest
    assert first.usi_input_digest != second.usi_input_digest
    assert first.engine_config.config_hash != second.engine_config.config_hash
    assert first.generation_ref != second.generation_ref
    assert first.plan_id != second.plan_id
