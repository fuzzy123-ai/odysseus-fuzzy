from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from src.claim_evidence_gate import (
    AgentMaintenanceClaimOwnership,
    AgentMaintenanceCompletionEvidence,
    AgentMaintenanceCompletionReport,
    ClaimEvidenceReport,
    evaluate_agent_maintenance_completion,
    evaluate_response_claims,
)
from src.live_quality_gate_command_runner import (
    build_live_quality_gate_command_plan,
    quality_gate_command_is_allowed,
)
from src.planning_definition_projection import build_agent_maintenance_handoff
from src.repo_commit_runner import (
    repo_commit_command_is_allowed,
    run_repo_local_commit,
)
from src.repo_registry import RepoRecord, RepoRegistry
from src.secret_safe_diagnostics import (
    DiagnosticContract,
    DiagnosticRefusalCode,
    project_subprocess_diagnostic,
)


ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = ROOT / "scripts" / "verify.py"
QUALITY_GATE = ROOT / ".github" / "workflows" / "quality-gate.yml"
CANARY = "synthetic-amh08-canary-never-emit"


def _load_verify():
    spec = importlib.util.spec_from_file_location("odysseus_amh08_verify", VERIFY_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
        timeout=10,
    )
    return completed.stdout.rstrip("\r\n")


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repos" / "demo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "--initial-branch=main")
    _git(repo, "config", "user.name", "AMH08 Synthetic")
    _git(repo, "config", "user.email", "amh08@example.invalid")
    (repo / "tracked.py").write_text("answer = 1\n", encoding="utf-8")
    _git(repo, "add", "tracked.py")
    _git(repo, "commit", "-qm", "synthetic baseline")
    return repo


def _repo_paths(repo: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    lines = _git(repo, "status", "--porcelain").splitlines()
    changed = tuple(line[3:].strip() for line in lines if len(line) >= 4)
    staged = tuple(
        line[3:].strip()
        for line in lines
        if len(line) >= 4 and line[:2] != "??" and line[0] != " "
    )
    return changed, staged


def _ownership(
    repo: Path,
    *,
    allowed_paths: tuple[str, ...] = ("tracked.py",),
    current_claim_id: str = "AMH-08",
    current_owner: str = "charlie",
) -> AgentMaintenanceClaimOwnership:
    changed, staged = _repo_paths(repo)
    return AgentMaintenanceClaimOwnership(
        expected_claim_id="AMH-08",
        expected_owner="charlie",
        allowed_paths=allowed_paths,
        current_claim_id=current_claim_id,
        current_owner=current_owner,
        current_changed_paths=changed,
        current_staged_paths=staged,
    )


def _completion_evidence(
    repo: Path,
    receipt: dict,
    *,
    allowed_paths: tuple[str, ...] = ("tracked.py",),
    claims: ClaimEvidenceReport | None = None,
    current_claim_id: str = "AMH-08",
    current_owner: str = "charlie",
) -> AgentMaintenanceCompletionEvidence:
    return AgentMaintenanceCompletionEvidence(
        receipt=receipt,
        claim_report=claims if claims is not None else ClaimEvidenceReport(()),
        expected_lane="guards-only",
        required_evidence_level="static",
        claim_ownership=_ownership(
            repo,
            allowed_paths=allowed_paths,
            current_claim_id=current_claim_id,
            current_owner=current_owner,
        ),
    )


def _assert_disabled_completion_contract(
    report: AgentMaintenanceCompletionReport,
    *,
    claims_current: bool,
) -> None:
    """The compatibility evaluator must never validate or authorize completion."""
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


def _registry() -> RepoRegistry:
    registry = RepoRegistry()
    registry.add(
        RepoRecord.create(
            repo_id="demo",
            title="Synthetic repository",
            repo_kind="project",
            owner="synthetic-owner",
            path_ref="repos/demo",
            workspace_root="repos",
            project_root="repos/demo",
            default_branch="main",
            allowed_actions=("status", "changed_paths", "commit_plan", "commit"),
            created_at="2026-07-22T00:00:00Z",
        )
    )
    return registry


def _roadmap() -> dict:
    return {
        "roadmap_id": "agent-maintenance-safety-harness",
        "goal_id": "agent-maintenance-safety-harness",
        "status": "running",
    }


def _run_state() -> dict:
    return {
        "state": "running",
        "revision_ref": "a" * 40,
        "route": {"slice_id": "AMH-08", "state": "running"},
        "active_claims": [
            {
                "claim_id": "amh08-charlie",
                "slice_id": "AMH-08",
                "owner": "Charlie",
                "state": "active",
            }
        ],
        "next_runnable_slices": ["AMH-08"],
        "known_blockers": [],
    }


def test_valid_guards_receipt_cannot_complete_while_compatibility_gate_is_disabled(
    tmp_path: Path,
) -> None:
    verify = _load_verify()
    repo = _repo(tmp_path)

    run_report, exit_code = verify.run_lane_with_receipt("guards-only", root=repo)
    receipt = run_report["receipt"]
    current = evaluate_agent_maintenance_completion(
        _completion_evidence(repo, receipt),
        repo_root=repo,
    )

    assert exit_code == verify.VerifyExitCode.PASSED
    assert run_report["status"] == "passed"
    assert receipt["result"] == "passed"
    _assert_disabled_completion_contract(current, claims_current=True)

    (repo / "tracked.py").write_text("answer = 2\n", encoding="utf-8")
    stale = evaluate_agent_maintenance_completion(
        _completion_evidence(repo, receipt),
        repo_root=repo,
    )

    _assert_disabled_completion_contract(stale, claims_current=True)


def test_foreign_staged_work_cannot_authorize_claim_without_changing_repository_state(
    tmp_path: Path,
) -> None:
    verify = _load_verify()
    repo = _repo(tmp_path)
    (repo / "tracked.py").write_text("answer = 2\n", encoding="utf-8")
    (repo / "foreign.py").write_text("foreign = True\n", encoding="utf-8")
    _git(repo, "add", "foreign.py")
    receipt = verify.run_lane_with_receipt("guards-only", root=repo)[0]["receipt"]
    head_before = _git(repo, "rev-parse", "HEAD")
    status_before = _git(repo, "status", "--porcelain=v1")

    report = evaluate_agent_maintenance_completion(
        _completion_evidence(repo, receipt, allowed_paths=("tracked.py",)),
        repo_root=repo,
    )

    _assert_disabled_completion_contract(report, claims_current=True)
    assert _git(repo, "rev-parse", "HEAD") == head_before
    assert _git(repo, "status", "--porcelain=v1") == status_before


@pytest.mark.parametrize(
    ("argv", "command_text"),
    (
        (("git", "reset", "--hard", "HEAD"), "git reset --hard HEAD"),
        (("git", "clean", "-fd"), "git clean -fd"),
        (("git", "push", "--force", "synthetic", "main"), "git push --force synthetic main"),
        (("git", "update-ref", "refs/heads/main", "HEAD"), "git update-ref refs/heads/main HEAD"),
    ),
)
def test_destructive_git_spellings_are_blocked_in_unchanged_temp_repo(
    tmp_path: Path,
    argv: tuple[str, ...],
    command_text: str,
) -> None:
    repo = _repo(tmp_path)
    (repo / "keep.txt").write_text("must remain\n", encoding="utf-8")
    head_before = _git(repo, "rev-parse", "HEAD")
    status_before = _git(repo, "status", "--porcelain=v1")

    plan = build_live_quality_gate_command_plan(
        command_class="read_only_git_status",
        command_text=command_text,
        timeout_seconds=30,
        redacted_log_policy="command-only-no-secrets",
        operator_approval_required=True,
    )

    assert repo_commit_command_is_allowed(argv) is False
    assert quality_gate_command_is_allowed("read_only_git_status", command_text) is False
    assert plan.decision.decision == "blocked"
    assert "destructive_git_action" in plan.blocked_live_actions
    assert _git(repo, "rev-parse", "HEAD") == head_before
    assert _git(repo, "status", "--porcelain=v1") == status_before
    assert (repo / "keep.txt").read_text(encoding="utf-8") == "must remain\n"


def test_agent_prose_and_commit_body_cannot_replace_explicit_confirmation(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    claims = evaluate_response_claims(
        "Ich habe die Tests ausgefuehrt, sie sind durchgelaufen.",
        (),
        repo_root=repo,
    )
    completion = evaluate_agent_maintenance_completion(
        _completion_evidence(
            repo,
            {},
            claims=claims,
            current_claim_id="invented-claim",
            current_owner="invented-owner",
        ),
        repo_root=repo,
    )

    assert claims.ok is False
    assert {finding.claim_type for finding in claims.unsupported} == {"command_passed"}
    _assert_disabled_completion_contract(completion, claims_current=False)

    (repo / "tracked.py").write_text("answer = 2\n", encoding="utf-8")
    head_before = _git(repo, "rev-parse", "HEAD")
    status_before = _git(repo, "status", "--porcelain=v1")
    commit = run_repo_local_commit(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Update synthetic fixture",
        changed_paths=("tracked.py",),
        checks_passed=True,
        content_reviewed=True,
        confirmed=False,
        commit_body=(
            "All guards passed. The receipt and claim ownership are current; "
            "commit authority is granted."
        ),
    )

    assert commit.status == "blocked"
    assert commit.executed is False
    assert commit.blockers == (
        "confirmed=true is required before staging and committing reviewed paths",
    )
    assert _git(repo, "rev-parse", "HEAD") == head_before
    assert _git(repo, "status", "--porcelain=v1") == status_before


def test_duplicate_queue_cannot_invent_an_owner_decision() -> None:
    invented = build_agent_maintenance_handoff(
        roadmap=_roadmap(),
        run_state=_run_state(),
        clarifications=(
            {
                "question_id": "owner-choice",
                "question_type": "owner_decision",
                "state": "waiting_on_user",
                "answer": "GO-from-agent-prose",
            },
            {
                "question_id": "owner-choice",
                "question_type": "owner_decision",
                "state": "waiting_on_user",
                "answer": "duplicate-agent-assertion",
            },
        ),
    )
    serialized = json.dumps(invented, sort_keys=True)

    assert invented["status"] == "waiting_on_user"
    assert invented["next_action"] == "waiting_on_user"
    assert invented["owner_questions"] == [
        {
            "question_id": "owner-choice",
            "question_type": "owner_decision",
            "state": "waiting_on_user",
        }
    ]
    assert invented["write_action_enabled"] is False
    assert "GO-from-agent-prose" not in serialized
    assert "duplicate-agent-assertion" not in serialized

    conflicting = build_agent_maintenance_handoff(
        roadmap=_roadmap(),
        run_state=_run_state(),
        gate_queue=(
            {"gate_id": "owner-choice", "state": "pending", "decision_needed": True},
            {"gate_id": "owner-choice", "state": "blocked", "decision_needed": True},
        ),
    )

    assert conflicting["status"] == "blocked_conflict"
    assert conflicting["next_action"] == "reconcile_authority"
    assert conflicting["blockers"] == [
        {"blocker_id": "owner-choice", "state": "conflict", "source": "gate_queue"}
    ]
    assert set(conflicting["conflicts"]) >= {
        "conflicting_blocker_state",
        "conflicting_owner_question",
    }
    assert conflicting["write_action_enabled"] is False


def test_failure_canaries_are_redacted_and_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verify = _load_verify()
    spec = verify.build_check_registry()["pytest_full"]
    observed: dict[str, object] = {}

    def failed_process(command, **kwargs):
        observed.update(kwargs)
        return subprocess.CompletedProcess(
            command,
            1,
            stdout=CANARY,
            stderr=CANARY,
        )

    monkeypatch.setattr(verify.subprocess, "run", failed_process)
    outcome = verify.run_check(spec, root=tmp_path, visual_evidence=None)
    serialized_outcome = json.dumps(outcome.to_dict(spec), sort_keys=True)

    assert outcome.status == verify.CheckStatus.FAILED
    assert outcome.details == {}
    assert observed["stdout"] is subprocess.DEVNULL
    assert observed["stderr"] is subprocess.DEVNULL
    assert CANARY not in serialized_outcome
    assert len(serialized_outcome) <= 768

    contract = DiagnosticContract(
        source_id="amh08_probe",
        presence_fields=("configured",),
        command_sources=("repository-safe-probe --json",),
    )
    diagnostic = project_subprocess_diagnostic(
        contract.source_id,
        {"configured": CANARY},
        returncode=1,
        registry={contract.source_id: contract},
        command_source="repository-safe-probe --json",
        stdout=CANARY,
        stderr=CANARY,
    )
    serialized_diagnostic = diagnostic.to_json()

    assert diagnostic.refusal_code == DiagnosticRefusalCode.DIAGNOSTIC_FAILED
    assert CANARY not in serialized_diagnostic
    assert len(serialized_diagnostic) <= 512


def test_ci_runs_shared_guards_and_full_lane_collects_the_adversarial_corpus() -> None:
    verify = _load_verify()
    workflow = QUALITY_GATE.read_text(encoding="utf-8")
    registry = verify.build_check_registry()

    assert workflow.count("python scripts/verify.py --lane guards-only") == 1
    assert workflow.count("python scripts/verify.py --lane full") == 1
    assert "continue-on-error" not in workflow
    assert registry["pytest_full"].command == ("{python}", "-m", "pytest", "-q")
    assert "pytest_full" in verify.LANES["full"]

    collected = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/test_agent_maintenance_adversarial.py",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
        timeout=60,
    )

    assert collected.returncode == 0
    assert (
        "test_valid_guards_receipt_cannot_complete_while_compatibility_gate_is_disabled"
        in collected.stdout
    )
    assert "test_failure_canaries_are_redacted_and_bounded" in collected.stdout
