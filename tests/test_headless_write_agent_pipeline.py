import pytest

from src.headless_write_agent_pipeline import (
    ApprovalCapability,
    HeadlessCommitEvidence,
    HeadlessCommitIntent,
    HeadlessPromotionStage,
    HeadlessWriteAgentPipelineError,
    PromotionEnvelope,
    prepare_commit_project_call,
)


DIFF_DIGEST = "sha256:" + "d" * 64
CHECKS_DIGEST = "sha256:" + "c" * 64


def _capability(**overrides):
    payload = {
        "capability_id": "hwa_cap_" + "a" * 32,
        "nonce": "hwa_nonce_" + "b" * 32,
        "stage": "project_commit",
        "owner_id": "local-user",
        "repo_id": "project-one",
        "task_id": "task-one",
        "plan_id": "plan-one",
        "slice_id": "slice-one",
        "agent_run_id": "bob-run-one",
        "approver_ref": "operator:charlie",
        "policy_version": "hwa-policy-v1",
        "input_digest": DIFF_DIGEST,
        "allowed_paths": ["src/project", "tests/test_project.py"],
        "blocked_paths": ["src/project/secrets"],
        "lease_fence": 7,
        "max_attempts": 1,
        "issued_at": "2026-07-13T10:00:00Z",
        "expires_at": "2026-07-13T11:00:00Z",
    }
    payload.update(overrides)
    return ApprovalCapability.create(**payload)


def _evidence(**overrides):
    payload = {
        "evidence_ref": "hwa_evd_" + "e" * 32,
        "owner_id": "local-user",
        "repo_id": "project-one",
        "task_id": "task-one",
        "plan_id": "plan-one",
        "slice_id": "slice-one",
        "agent_run_id": "bob-run-one",
        "worktree_ref": "coding-worktree:task-one",
        "lease_fence": 7,
        "base_commit_sha": "1" * 40,
        "diff_digest": DIFF_DIGEST,
        "checks_digest": CHECKS_DIGEST,
        "reviewed_paths": ["src/project/service.py", "tests/test_project.py"],
        "reviewer_ref": "review:charlie:42",
        "checks_passed": True,
        "content_reviewed": True,
        "verified_at": "2026-07-13T10:15:00Z",
    }
    payload.update(overrides)
    return HeadlessCommitEvidence.create(**payload)


def _envelope(**overrides):
    payload = {
        "stage": "project_commit",
        "capability_id": "hwa_cap_" + "a" * 32,
        "owner_id": "local-user",
        "repo_id": "project-one",
        "task_id": "task-one",
        "plan_id": "plan-one",
        "slice_id": "slice-one",
        "agent_run_id": "bob-run-one",
        "input_digest": DIFF_DIGEST,
        "predecessor_refs": ["hwa_evd_" + "e" * 32],
        "target_ref": "project-one",
    }
    payload.update(overrides)
    return PromotionEnvelope.create(**payload)


def _intent():
    return HeadlessCommitIntent.create(
        title="Add strict headless commit bridge",
        description="Bind the verified subagent evidence to the canonical project commit pipeline.",
        version_label="0.23.0-dev",
        change_notes=["No provider selection is exposed", "Merge and deploy remain separate"],
    )


def _prepare(**overrides):
    payload = {
        "capability": _capability(),
        "envelope": _envelope(),
        "evidence": _evidence(),
        "intent": _intent(),
        "authenticated_owner_id": "local-user",
        "current_lease_fence": 7,
        "checked_at": "2026-07-13T10:20:00Z",
    }
    payload.update(overrides)
    return prepare_commit_project_call(**payload)


def test_prepares_only_the_canonical_commit_project_arguments_from_verified_evidence():
    prepared = _prepare()

    assert set(prepared.arguments) == {
        "repo_id",
        "title",
        "description",
        "version_label",
        "change_notes",
        "reviewed_paths",
        "checks_passed",
        "content_reviewed",
        "confirmed",
        "idempotency_key",
    }
    assert prepared.arguments["reviewed_paths"] == ["src/project/service.py", "tests/test_project.py"]
    assert prepared.arguments["checks_passed"] is True
    assert prepared.arguments["content_reviewed"] is True
    assert prepared.arguments["confirmed"] is True
    assert prepared.arguments["idempotency_key"].startswith("hwa_commit_")
    assert not ({"owner_id", "provider", "remote", "branch", "worktree", "merge", "deploy"} & set(prepared.arguments))
    assert prepared.handler_context()["authenticated_owner_id"] == "local-user"
    assert prepared.audit_summary()["provider_argument_present"] is False


def test_commit_preparation_is_deterministically_idempotent_for_same_capability_and_evidence():
    first = _prepare()
    second = _prepare()

    assert first.arguments["idempotency_key"] == second.arguments["idempotency_key"]


@pytest.mark.parametrize("stage", ["workspace_write", "provider_sync", "merge", "deploy", "rollback"])
def test_non_commit_capability_cannot_escalate_into_commit(stage):
    cap = _capability(stage=stage, allowed_paths=[] if stage not in {"workspace_write"} else ["src"])
    envelope = _envelope(stage=stage)

    with pytest.raises(HeadlessWriteAgentPipelineError, match="does not permit project commit"):
        _prepare(capability=cap, envelope=envelope)


def test_authenticated_owner_must_match_server_issued_capability():
    with pytest.raises(HeadlessWriteAgentPipelineError, match="authenticated owner"):
        _prepare(authenticated_owner_id="other-user")


def test_identity_mismatch_between_capability_envelope_and_evidence_is_rejected():
    with pytest.raises(HeadlessWriteAgentPipelineError, match="task_id is not bound"):
        _prepare(evidence=_evidence(task_id="other-task"))


def test_exact_diff_digest_must_match_approval_and_evidence():
    other_digest = "sha256:" + "f" * 64

    with pytest.raises(HeadlessWriteAgentPipelineError, match="input digest"):
        _prepare(evidence=_evidence(diff_digest=other_digest))


def test_stale_lease_fence_and_consumed_capability_are_rejected():
    with pytest.raises(HeadlessWriteAgentPipelineError, match="lease fence"):
        _prepare(current_lease_fence=8)

    with pytest.raises(HeadlessWriteAgentPipelineError, match="already consumed"):
        _prepare(capability_consumed=True)


@pytest.mark.parametrize("checked_at", ["2026-07-13T09:59:59Z", "2026-07-13T11:00:00Z"])
def test_capability_must_be_active_at_effect_time(checked_at):
    with pytest.raises(HeadlessWriteAgentPipelineError, match="not active"):
        _prepare(checked_at=checked_at)


def test_evidence_must_be_server_verified_and_inside_capability_scope():
    with pytest.raises(HeadlessWriteAgentPipelineError, match="server-verified"):
        _prepare(evidence=_evidence(checks_passed=False))

    with pytest.raises(HeadlessWriteAgentPipelineError, match="outside approval scope"):
        _prepare(evidence=_evidence(reviewed_paths=["routes/admin.py"]))

    with pytest.raises(HeadlessWriteAgentPipelineError, match="overlaps blocked scope"):
        _prepare(evidence=_evidence(reviewed_paths=["src/project/secrets/token.py"]))


def test_approval_window_is_bounded_and_paths_are_repo_relative():
    with pytest.raises(HeadlessWriteAgentPipelineError, match="24 hours"):
        _capability(expires_at="2026-07-14T10:00:01Z")

    with pytest.raises(HeadlessWriteAgentPipelineError, match="repo-relative"):
        _capability(allowed_paths=["../outside"])


def test_stage_is_single_and_explicit():
    capability = _capability()

    assert capability.stage == HeadlessPromotionStage.PROJECT_COMMIT
    summary = capability.audit_summary()
    assert summary["one_shot"] is True
    assert summary["owner_bound"] is True
