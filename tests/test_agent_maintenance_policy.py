from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENT_POLICY = ROOT / "AGENTS.md"
RUNBOOK = ROOT / "docs" / "agent-maintenance-runbook.md"
CONTRIBUTING = ROOT / "CONTRIBUTING.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized(text: str) -> str:
    return " ".join(text.replace("`", "").split())


def test_root_policy_is_bounded_stable_and_actionable() -> None:
    policy = _read(AGENT_POLICY)
    lowered = _normalized(policy).lower()

    assert len(policy) < 7_500
    for phrase in (
        "Safe start and ownership",
        "current structured state",
        "path-scoped claim",
        "Preserve all unrelated working-tree and staged changes",
        "waiting_on_user",
        "Repository and external effects",
        "git reset --hard",
        "force pushes",
        "Verification and handoff",
        "Not verified",
        "dirty-diff digest",
    ):
        assert phrase.lower() in lowered

    for mutable_field in (
        '"run_id"',
        '"active_claims"',
        '"last_heartbeat_at"',
        '"resume_cursor"',
    ):
        assert mutable_field not in policy


def test_root_policy_keeps_diagnostics_secret_safe_before_consumers() -> None:
    policy = _normalized(_read(AGENT_POLICY))

    for phrase in (
        "No credential value, prefix, suffix, length, or hash",
        "before a repository-owned redaction boundary",
        "fixed-key boolean presence and bounded aggregate counts",
        "env",
        "printenv",
        ".env contents",
        "unredacted compose config",
    ):
        assert phrase in policy


def test_runbook_uses_existing_authorities_and_has_a_complete_handoff() -> None:
    runbook = _read(RUNBOOK)

    for phrase in (
        "versioned roadmap",
        "gate queue",
        "claim",
        "clarification records",
        "STATE.md",
        "OWNER_QUEUE.md",
        "Establish current authority",
        "Claim the minimum paths",
        "Diagnose without exposing secrets",
        "Verify at the declared level",
        "Complete or hand off",
        "Changed paths:",
        "Tests and results:",
        "Evidence reference:",
        "Blockers or owner questions:",
        "Not verified:",
        "Publication or live authority:",
    ):
        assert phrase in runbook


def test_runbook_never_grants_publication_or_live_authority() -> None:
    runbook = _read(RUNBOOK)

    assert "This document grants no authority to commit, push, deploy" in runbook
    assert "Do not automatically stage, commit, push, deploy" in runbook
    assert "action-specific Go" in runbook
    assert "A weak lane" in runbook
    assert "cannot substitute for required machine evidence" in runbook


def test_contributing_links_policy_and_requires_verification_limits() -> None:
    contributing = _read(CONTRIBUTING)

    assert "[agent policy](AGENTS.md)" in contributing
    assert "[maintenance runbook](docs/agent-maintenance-runbook.md)" in contributing
    assert "**Verification**" in contributing
    assert "**Not verified**" in contributing
    assert "Automation does not imply authority" in contributing
