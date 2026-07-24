from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import subprocess

import pytest

from src.agent_verification_receipt import (
    build_verification_receipt,
    repository_binding,
)
from src.claim_evidence_gate import (
    AgentMaintenanceClaimOwnership,
    AgentMaintenanceCompletionEvidence,
    AgentMaintenanceCompletionReport,
    ClaimEvidenceFinding,
    ClaimEvidenceReport,
    evaluate_agent_maintenance_completion,
)
from src.effectful_tool_matrix import build_effectful_action_snapshot


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Completion Test")
    _git(root, "config", "user.email", "completion@example.invalid")
    (root / "tracked.py").write_text("answer = 1\n", encoding="utf-8")
    _git(root, "add", "tracked.py")
    _git(root, "commit", "-qm", "initial")
    return root


def _receipt(root: Path, *, lane: str = "guards-only", evidence: str = "static") -> dict:
    binding = repository_binding(root)
    report = {
        "lane": lane,
        "strongest_evidence_level": evidence,
        "checks": [
            {
                "check_id": "current_check",
                "required": True,
                "status": "passed",
                "evidence_level": evidence,
            }
        ],
        "verification_limits": ["live_not_verified"],
    }
    return build_verification_receipt(
        report,
        binding_before=binding,
        binding_after=binding,
    )


def _evidence(
    root: Path,
    *,
    receipt: dict | None = None,
    lane: str = "guards-only",
    required: str = "static",
    claims: ClaimEvidenceReport | None = None,
    ownership: AgentMaintenanceClaimOwnership | None = None,
) -> AgentMaintenanceCompletionEvidence:
    status_lines = _git(root, "status", "--porcelain").splitlines()
    changed = tuple(line[3:].strip() for line in status_lines if len(line) >= 4)
    staged = tuple(
        line[3:].strip()
        for line in status_lines
        if len(line) >= 4 and line[:2] != "??" and line[0] != " "
    )
    return AgentMaintenanceCompletionEvidence(
        receipt=receipt if receipt is not None else _receipt(root, lane=lane),
        claim_report=claims if claims is not None else ClaimEvidenceReport(()),
        expected_lane=lane,
        required_evidence_level=required,
        claim_ownership=ownership
        if ownership is not None
        else AgentMaintenanceClaimOwnership(
            expected_claim_id="AMH-06",
            expected_owner="bob",
            allowed_paths=("tracked.py",),
            current_claim_id="AMH-06",
            current_owner="bob",
            current_changed_paths=changed,
            current_staged_paths=staged,
        ),
    )


def _assert_disabled_completion_contract(
    report: AgentMaintenanceCompletionReport,
    *,
    claims_current: bool,
) -> None:
    """The legacy compatibility gate is deliberately non-validating and fail-closed."""
    payload = report.to_dict()

    assert report.completed is False
    assert report.receipt_current is False
    assert report.ownership_current is False
    assert report.claims_current is claims_current
    assert report.actual_evidence_level == "none"
    assert report.blockers == (
        "maintenance completion verification is disabled pending architecture review",
    )
    assert payload["origin_authenticated"] is False
    assert payload["commit_authorized"] is False
    assert payload["push_authorized"] is False
    assert payload["live_authorized"] is False


def test_current_compatible_evidence_cannot_complete_while_gate_is_disabled(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)

    report = evaluate_agent_maintenance_completion(_evidence(root), repo_root=root)
    payload = report.to_dict()

    _assert_disabled_completion_contract(report, claims_current=True)
    assert "receipt_digest" not in payload
    assert "producer" not in payload


@pytest.mark.parametrize(
    "variant",
    (
        "missing",
        "mismatched_lane",
        "weaker",
        "unsupported_claim",
        "metadata_only",
    ),
)
def test_legacy_evidence_variants_remain_non_authorizing_while_gate_is_disabled(
    tmp_path: Path,
    variant: str,
) -> None:
    root = _repo(tmp_path)
    if variant == "missing":
        evidence = AgentMaintenanceCompletionEvidence(
            receipt={},
            claim_report=ClaimEvidenceReport(()),
            expected_lane="guards-only",
            required_evidence_level="static",
            claim_ownership=_evidence(root).claim_ownership,
        )
    elif variant == "mismatched_lane":
        evidence = _evidence(root, receipt=_receipt(root, lane="fast"))
    elif variant == "weaker":
        evidence = _evidence(
            root,
            receipt=_receipt(root, lane="fast", evidence="static"),
            lane="fast",
            required="fast",
        )
    elif variant == "unsupported_claim":
        evidence = _evidence(
            root,
            claims=ClaimEvidenceReport(
                (ClaimEvidenceFinding("test", "unsupported", "missing evidence"),)
            ),
        )
    else:
        evidence = AgentMaintenanceCompletionEvidence(
            receipt={"producer": {"id": "odysseus.scripts.verify"}, "receipt_digest": "a" * 64},
            claim_report=ClaimEvidenceReport(()),
            expected_lane="guards-only",
            required_evidence_level="static",
            claim_ownership=_evidence(root).claim_ownership,
        )

    report = evaluate_agent_maintenance_completion(evidence, repo_root=root)

    _assert_disabled_completion_contract(
        report,
        claims_current=variant != "unsupported_claim",
    )


def test_stale_and_tampered_receipts_remain_non_authorizing_while_gate_is_disabled(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    current = _receipt(root)
    tampered = deepcopy(current)
    tampered["receipt_digest"] = "f" * 64

    tampered_report = evaluate_agent_maintenance_completion(
        _evidence(root, receipt=tampered),
        repo_root=root,
    )
    _assert_disabled_completion_contract(tampered_report, claims_current=True)

    (root / "tracked.py").write_text("answer = 2\n", encoding="utf-8")
    stale_report = evaluate_agent_maintenance_completion(
        _evidence(root, receipt=current),
        repo_root=root,
    )
    _assert_disabled_completion_contract(stale_report, claims_current=True)


def test_wrong_root_cannot_authorize_while_gate_is_disabled(tmp_path: Path) -> None:
    first = _repo(tmp_path / "first")
    second = _repo(tmp_path / "second")
    (second / "tracked.py").write_text("answer = 9\n", encoding="utf-8")
    _git(second, "add", "tracked.py")
    _git(second, "commit", "-qm", "different head")

    report = evaluate_agent_maintenance_completion(
        _evidence(first),
        repo_root=second,
    )

    _assert_disabled_completion_contract(report, claims_current=True)


def test_missing_or_mismatched_claim_ownership_cannot_authorize_while_gate_is_disabled(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    current = _evidence(root)
    missing = AgentMaintenanceCompletionEvidence(
        receipt=current.receipt,
        claim_report=current.claim_report,
        expected_lane=current.expected_lane,
        required_evidence_level=current.required_evidence_level,
        claim_ownership=None,  # type: ignore[arg-type]
    )
    mismatch = _evidence(
        root,
        ownership=AgentMaintenanceClaimOwnership(
            expected_claim_id="AMH-06",
            expected_owner="bob",
            allowed_paths=("tracked.py",),
            current_claim_id="AMH-05",
            current_owner="bob",
            current_changed_paths=(),
            current_staged_paths=(),
        ),
    )

    _assert_disabled_completion_contract(
        evaluate_agent_maintenance_completion(missing, repo_root=root),
        claims_current=True,
    )
    mismatch_report = evaluate_agent_maintenance_completion(mismatch, repo_root=root)
    _assert_disabled_completion_contract(mismatch_report, claims_current=True)


def test_unknown_or_incompatible_lane_cannot_authorize_while_gate_is_disabled(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    current = _evidence(root)
    unknown = AgentMaintenanceCompletionEvidence(
        receipt=current.receipt,
        claim_report=current.claim_report,
        expected_lane="invented",
        required_evidence_level="static",
        claim_ownership=current.claim_ownership,
    )
    incompatible = AgentMaintenanceCompletionEvidence(
        receipt=current.receipt,
        claim_report=current.claim_report,
        expected_lane="guards-only",
        required_evidence_level="full",
        claim_ownership=current.claim_ownership,
    )

    _assert_disabled_completion_contract(
        evaluate_agent_maintenance_completion(unknown, repo_root=root),
        claims_current=True,
    )
    report = evaluate_agent_maintenance_completion(incompatible, repo_root=root)
    _assert_disabled_completion_contract(report, claims_current=True)


def test_foreign_path_cannot_authorize_while_gate_is_disabled(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "foreign.txt").write_text("ordinary foreign change\n", encoding="utf-8")

    report = evaluate_agent_maintenance_completion(_evidence(root), repo_root=root)

    _assert_disabled_completion_contract(report, claims_current=True)


def test_effect_matrix_classifies_normalized_read_actions_independently_of_metadata() -> None:
    read_only = build_effectful_action_snapshot(
        ({"tool": "manage_repos", "action": "status"},)
    )
    success_annotated = build_effectful_action_snapshot(
        (
            {
                "tool": "manage_repos",
                "action": "status",
                "transaction_status": "succeeded",
            },
        )
    )
    unknown = build_effectful_action_snapshot(
        ({"tool": "manage_repos", "action": "unexpected_read"},)
    )

    assert read_only["effectful_count"] == 0
    assert read_only["categories"] == ()
    assert success_annotated["effectful_count"] == 0
    assert success_annotated["categories"] == ()
    assert success_annotated["transaction_status"][0]["status"] == "succeeded"
    assert success_annotated["transaction_status"][0]["verified_done"] is True
    assert unknown["effectful_count"] == 1
    assert unknown["categories"] == ("repo_registry_write",)
