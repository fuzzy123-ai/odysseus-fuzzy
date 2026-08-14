import json

import pytest

from src.coding_lifecycle import CODING_LIFECYCLE_SCHEMA, build_coding_lifecycle_state
from src.coding_lifecycle_authority import (
    CODING_LIFECYCLE_AUTHORITY_SCHEMA,
    MAX_AUTHORITY_ID_LENGTH,
    MAX_AUTHORITY_SCOPE_ENTRIES,
    PRODUCTION_CODING_LIFECYCLE_STATES,
    CodingLifecycleAuthority,
    CodingLifecycleAuthorityError,
    CodingLifecycleCompletionProof,
    resume_authorized_coding_lifecycle,
    start_authorized_coding_lifecycle,
    transition_authorized_coding_lifecycle,
)


def _authority(**overrides):
    values = {
        "planning_item_id": "CAO-08A",
        "planning_revision": "planning-rev-17",
        "acceptance_criteria_id": "acceptance-contract-cao08a",
        "allowed_scope": ("src", "tests"),
        "blocked_scope": ("ops", ".git"),
        "claim_id": "claim-cao08a-bob",
        "claim_owner": "bob",
        "claim_scope": (
            "src/coding_lifecycle_authority.py",
            "tests/test_coding_lifecycle_authority.py",
        ),
        "input_revision": "worktree-rev-4",
        "input_diff_digest": "sha256:diff4",
        "acceptance_decision_id": "acceptance-decision-review-9",
        "evidence_id": "evidence-cao08a-9",
    }
    values.update(overrides)
    return CodingLifecycleAuthority.create(**values)


def _planning_authority(**overrides):
    values = {
        "planning_item_id": "CAO-08A",
        "planning_revision": "planning-rev-17",
        "acceptance_criteria_id": "acceptance-contract-cao08a",
        "allowed_scope": ("src", "tests"),
        "blocked_scope": ("ops", ".git"),
    }
    values.update(overrides)
    return CodingLifecycleAuthority.create(**values)


def _advance_to_publish_ready(authority=None):
    binding = authority or _authority()
    state = start_authorized_coding_lifecycle(
        task_id="task-cao08a", repo_id="odysseus", authority=binding
    )
    for target in (
        "planning",
        "ready_for_claim",
        "claimed",
        "context_building",
        "context_ready",
        "worktree_ready",
        "acting",
        "verifying",
        "review_ready",
        "memory_review",
        "publish_ready",
    ):
        state = transition_authorized_coding_lifecycle(state, target_state=target)
    return state


def test_positive_authorized_lifecycle_is_dag_validated_and_side_effect_free():
    legacy = build_coding_lifecycle_state(task_id="task-cao08a", repo_id="odysseus")
    authority = _authority()
    state = start_authorized_coding_lifecycle(
        task_id="task-cao08a",
        repo_id="odysseus",
        authority=authority,
        legacy_state=legacy,
    )
    visited = [state.state]
    for target in (
        "planning", "ready_for_claim", "claimed", "context_building", "context_ready",
        "worktree_ready", "acting", "verifying", "repair_planning", "acting",
        "verifying", "review_ready", "memory_review", "publish_ready",
    ):
        state = transition_authorized_coding_lifecycle(state, target_state=target)
        visited.append(state.state)
    proof = CodingLifecycleCompletionProof.create(
        acceptance_decision_id=authority.acceptance_decision_id,
        evidence_id=authority.evidence_id,
        reviewer_id="alice-reviewer",
        all_required_gates_closed=True,
        independent_review=True,
    )
    state = transition_authorized_coding_lifecycle(
        state, target_state="done", completion_proof=proof
    )
    payload = state.to_dict()

    assert tuple(visited[:8]) == PRODUCTION_CODING_LIFECYCLE_STATES[:8]
    assert state.state == "done"
    assert payload["schema"] == CODING_LIFECYCLE_AUTHORITY_SCHEMA
    assert payload["legacy_compatibility"]["schema"] == CODING_LIFECYCLE_SCHEMA
    assert payload["legacy_compatibility"]["payload_embedded"] is False
    assert payload["side_effects"] == ("none",)
    assert payload["runtime_event"]["side_effects"] == ("none",)


def test_authority_requirements_are_phase_dependent_and_missing_claim_waits():
    state = start_authorized_coding_lifecycle(
        task_id="task-cao08a", repo_id="odysseus", authority=_planning_authority()
    )
    assert state.state == "clarifying"
    state = transition_authorized_coding_lifecycle(state, target_state="planning")
    state = transition_authorized_coding_lifecycle(state, target_state="ready_for_claim")
    state = transition_authorized_coding_lifecycle(state, target_state="claimed")

    assert state.state == "waiting"
    assert state.last_accepted_state == "ready_for_claim"
    assert "missing_authority:claim_id" in state.waiting_reasons
    assert state.resume_condition == "supply_authority_for:claimed"


def test_missing_planning_authority_can_resume_from_revision_bound_checkpoint():
    state = start_authorized_coding_lifecycle(
        task_id="task-cao08a", repo_id="odysseus", authority=None
    )
    assert state.state == "waiting"
    resumed = resume_authorized_coding_lifecycle(
        state,
        observed_authority=_planning_authority(),
        satisfied_condition=state.resume_condition,
        resume_checkpoint=state.resume_checkpoint,
    )

    assert resumed.state == "clarifying"
    assert resumed.last_accepted_state == "clarifying"
    assert resumed.authority.planning_revision == "planning-rev-17"


def test_invalid_transition_is_rejected_without_narrative_override():
    state = start_authorized_coding_lifecycle(
        task_id="task-cao08a", repo_id="odysseus", authority=_authority()
    )
    with pytest.raises(CodingLifecycleAuthorityError, match="invalid lifecycle transition"):
        transition_authorized_coding_lifecycle(state, target_state="done")


@pytest.mark.parametrize(
    "field,replacement",
    (
        ("planning_revision", "stale-planning-rev"),
        ("claim_id", "foreign-claim"),
        ("claim_owner", "foreign-owner"),
        ("claim_scope", ("src/foreign.py",)),
        ("acceptance_decision_id", "foreign-decision"),
        ("evidence_id", "foreign-evidence"),
        ("input_revision", "foreign-revision"),
        ("input_diff_digest", "sha256:foreign"),
    ),
)
def test_stale_or_mismatched_authority_blocks(field, replacement):
    authority = _authority()
    state = start_authorized_coding_lifecycle(
        task_id="task-cao08a", repo_id="odysseus", authority=authority
    )
    observed_values = {
        name: getattr(authority, name)
        for name in (
            "planning_item_id", "planning_revision", "acceptance_criteria_id", "allowed_scope",
            "blocked_scope", "claim_id", "claim_owner", "claim_scope", "claim_scope_digest",
            "input_revision", "input_diff_digest", "acceptance_decision_id", "evidence_id",
        )
    }
    observed_values[field] = replacement
    if field == "claim_scope":
        observed_values["claim_scope_digest"] = ""
    observed = CodingLifecycleAuthority.create(**observed_values)
    blocked = transition_authorized_coding_lifecycle(
        state, target_state="planning", observed_authority=observed
    )

    assert blocked.state == "blocked"
    assert blocked.last_accepted_state == "clarifying"
    assert any(item.startswith(f"authority_mismatch:{field}") for item in blocked.blockers)


def test_waiting_and_blocked_preserve_last_state_and_require_exact_checkpoint():
    state = start_authorized_coding_lifecycle(
        task_id="task-cao08a", repo_id="odysseus", authority=_authority()
    )
    state = transition_authorized_coding_lifecycle(state, target_state="planning")
    held = transition_authorized_coding_lifecycle(
        state, target_state="waiting", resume_condition="owner_decision_ready"
    )
    wrong = resume_authorized_coding_lifecycle(
        held,
        observed_authority=held.authority,
        satisfied_condition=held.resume_condition,
        resume_checkpoint="sha256:wrong",
    )
    assert wrong.state == "blocked"
    assert wrong.last_accepted_state == "planning"
    assert wrong.blockers == ("resume_checkpoint_mismatch",)

    resumed = resume_authorized_coding_lifecycle(
        held,
        observed_authority=held.authority,
        satisfied_condition=held.resume_condition,
        resume_checkpoint=held.resume_checkpoint,
    )
    assert resumed.state == "planning"
    assert resumed.resume_condition == ""


def test_scope_is_component_normalized_and_blocked_scope_wins():
    blocked_authority = _authority(
        allowed_scope=("src", "tests", "ops-not"),
        blocked_scope=("ops",),
        claim_scope=("src/./coding_lifecycle.py", "ops/deploy.py"),
        claim_scope_digest="",
    )
    state = start_authorized_coding_lifecycle(
        task_id="task-cao08a", repo_id="odysseus", authority=blocked_authority
    )
    state = transition_authorized_coding_lifecycle(state, target_state="planning")
    state = transition_authorized_coding_lifecycle(state, target_state="ready_for_claim")
    state = transition_authorized_coding_lifecycle(state, target_state="claimed")

    assert state.state == "blocked"
    assert any(item.startswith("claim_scope_blocked:") for item in state.blockers)

    allowed_authority = _authority(
        allowed_scope=("src", "tests", "ops-not"),
        blocked_scope=("ops",),
        claim_scope=("ops-not/deploy.py",),
        claim_scope_digest="",
    )
    allowed = start_authorized_coding_lifecycle(
        task_id="task-cao08a-safe", repo_id="odysseus", authority=allowed_authority
    )
    allowed = transition_authorized_coding_lifecycle(allowed, target_state="planning")
    allowed = transition_authorized_coding_lifecycle(allowed, target_state="ready_for_claim")
    allowed = transition_authorized_coding_lifecycle(allowed, target_state="claimed")
    assert allowed.state == "claimed"

    with pytest.raises(CodingLifecycleAuthorityError, match="parent traversal"):
        _authority(claim_scope=("src/../ops/deploy.py",), claim_scope_digest="")


def test_done_requires_matching_evidence_decision_and_independent_reviewer():
    state = _advance_to_publish_ready()
    no_proof = transition_authorized_coding_lifecycle(state, target_state="done")
    assert no_proof.state == "blocked"
    assert no_proof.last_accepted_state == "publish_ready"
    assert no_proof.blockers == ("completion_proof_missing",)

    self_proof = CodingLifecycleCompletionProof.create(
        acceptance_decision_id=state.authority.acceptance_decision_id,
        evidence_id=state.authority.evidence_id,
        reviewer_id=state.authority.claim_owner,
        all_required_gates_closed=True,
        independent_review=True,
    )
    self_done = transition_authorized_coding_lifecycle(
        state, target_state="done", completion_proof=self_proof
    )
    assert self_done.state == "blocked"
    assert self_done.blockers == ("completion_reviewer_not_independent",)


def test_authority_payload_redacts_private_identifiers_and_never_embeds_legacy_payload():
    legacy = build_coding_lifecycle_state(task_id="legacy-task", repo_id="odysseus")
    state = start_authorized_coding_lifecycle(
        task_id=r"C:\Users\private\task",
        repo_id="odysseus",
        authority=_authority(),
        legacy_state=legacy,
    )
    dumped = json.dumps(state.to_dict(), default=str)

    assert r"C:\Users\private" not in dumped
    assert "sha256:" in dumped
    assert "payload_embedded\": false" in dumped


@pytest.mark.parametrize(
    "field,value",
    (
        ("planning_item_id", "token=abc123"),
        ("planning_revision", r"C:\Users\private\revision"),
        ("acceptance_criteria_id", "criteria with spaces"),
        ("claim_id", "../foreign-claim"),
        ("claim_owner", "bearer credential"),
        ("claim_scope_digest", "digest with spaces"),
        ("input_revision", r"/home/private/revision"),
        ("input_diff_digest", "secret=diff"),
        ("acceptance_decision_id", "decision/private"),
        ("evidence_id", "password=abc"),
        ("planning_item_id", " CAO-08A "),
        ("planning_revision", 17),
    ),
)
def test_unsafe_authority_identifiers_are_rejected_not_hashed(field, value):
    with pytest.raises(CodingLifecycleAuthorityError, match=field):
        _authority(**{field: value})


@pytest.mark.parametrize(
    "scope_path",
    (
        r"C:\Users\private\repo\file.py",
        r"\\server\share\file.py",
        "/home/private/file.py",
        "~/private/file.py",
        ".",
        "src//file.py",
        "src/file.py:private-stream",
        "",
        17,
    ),
)
def test_absolute_unc_home_and_root_like_scopes_are_rejected(scope_path):
    with pytest.raises(CodingLifecycleAuthorityError, match="scope path"):
        _authority(
            allowed_scope=(scope_path,),
            claim_scope=(scope_path,),
            claim_scope_digest="",
        )


def test_unsafe_completion_proof_identity_is_rejected_not_hashed():
    with pytest.raises(CodingLifecycleAuthorityError, match="completion.reviewer_id"):
        CodingLifecycleCompletionProof.create(
            acceptance_decision_id="acceptance-decision-review-9",
            evidence_id="evidence-cao08a-9",
            reviewer_id="token=abc123",
            all_required_gates_closed=True,
            independent_review=True,
        )


def test_direct_dataclass_constructors_cannot_bypass_authority_validation():
    with pytest.raises(CodingLifecycleAuthorityError, match="planning_item_id"):
        CodingLifecycleAuthority(planning_item_id="token=abc123")
    with pytest.raises(CodingLifecycleAuthorityError, match="completion.evidence_id"):
        CodingLifecycleCompletionProof(
            acceptance_decision_id="decision-9",
            evidence_id=r"C:\Users\private\evidence",
            reviewer_id="alice-reviewer",
            all_required_gates_closed=True,
            independent_review=True,
        )


def test_authority_identity_and_scope_collection_bounds_are_fail_closed():
    with pytest.raises(CodingLifecycleAuthorityError, match="planning_item_id"):
        _authority(planning_item_id="x" * (MAX_AUTHORITY_ID_LENGTH + 1))
    with pytest.raises(CodingLifecycleAuthorityError, match="scope exceeds"):
        _authority(
            allowed_scope=tuple(
                f"src/component-{index}" for index in range(MAX_AUTHORITY_SCOPE_ENTRIES + 1)
            )
        )
