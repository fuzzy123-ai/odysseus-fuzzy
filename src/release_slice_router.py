"""Map release readiness blockers to small follow-up slices.

The router is intentionally deterministic and read-only. It does not run tests,
providers, vault writes, git commands, or agent dispatches; it only prepares the
next safe work items for Alice/Bob/Charlie.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.release_readiness_report import ReleaseReadinessReport


@dataclass(frozen=True)
class ReleaseFollowupSlice:
    slice_id: str
    owner: str
    title: str
    scope: tuple[str, ...]
    exit_criteria: str
    parallel_safe: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "slice_id": self.slice_id,
            "owner": self.owner,
            "title": self.title,
            "scope": self.scope,
            "exit_criteria": self.exit_criteria,
            "parallel_safe": self.parallel_safe,
        }


def route_release_followups(report: ReleaseReadinessReport) -> tuple[ReleaseFollowupSlice, ...]:
    slices: list[ReleaseFollowupSlice] = []
    seen: set[str] = set()

    for reason in report.blocking_reasons:
        mapped = _map_blocker(reason)
        if mapped and mapped.slice_id not in seen:
            seen.add(mapped.slice_id)
            slices.append(mapped)

    for action in report.next_actions:
        mapped = _map_action(action)
        if mapped and mapped.slice_id not in seen:
            seen.add(mapped.slice_id)
            slices.append(mapped)

    if report.external_release_go and not slices:
        slices.append(
            ReleaseFollowupSlice(
                "REL-final-external-review",
                "Charlie",
                "Prepare final external 1.0 review",
                ("docs/plans/1.0-evidence-release-checklist.md",),
                "Release checklist shows Go with no pending manual evidence",
                False,
            )
        )

    return tuple(slices)


def _map_blocker(reason: str) -> ReleaseFollowupSlice | None:
    if reason in {
        "manual:partial:provider-proof",
        "manual:pending:provider-proof",
        "manual:missing:provider-proof",
        "release:manual_pending:provider-proof",
    }:
        return ReleaseFollowupSlice(
            "REL-provider-proof-evidence",
            "Bob",
            "Complete provider/fallback proof support",
            (
                "docs/plans/1.0-manual-release-evidence-log.md",
                "docs/plans/provider-fallback-answer-run-contract.md",
                "src/provider_fallback_answer_run.py",
                "tests/test_provider_fallback_answer_run.py",
            ),
            "Provider/fallback behavior is evidenced or a focused blocker is documented",
            False,
        )
    if reason in {
        "manual:partial:export-import-rebuild",
        "manual:pending:export-import-rebuild",
        "manual:missing:export-import-rebuild",
        "release:manual_pending:export-import-rebuild",
    }:
        return ReleaseFollowupSlice(
            "REL-test-vault-rebuild-evidence",
            "Alice",
            "Prepare controlled test-vault export/import/rebuild evidence",
            (
                "docs/plans/1.0-manual-release-evidence-runbook.md",
                "docs/plans/1.0-manual-release-evidence-log.md",
            ),
            "Test-vault runbook is ready and no productive user artifacts are touched",
            True,
        )
    if reason.startswith("plugin:"):
        return ReleaseFollowupSlice(
            "REL-plugin-release-gate-fix",
            "Charlie",
            "Fix plugin release gate evidence",
            (
                "src/plugin_release_gate.py",
                "src/plugin_manifest_policy.py",
                "src/plugin_local_audit.py",
                "tests/test_plugin_release_gate.py",
            ),
            "Plugin release gate passes or a focused plugin blocker is documented",
            False,
        )
    if reason.startswith("release:blocking:"):
        return ReleaseFollowupSlice(
            "REL-automated-gate-fix",
            "Bob",
            "Fix blocking automated release gate",
            ("tests/", "docs/plans/1.0-evidence-release-checklist.md"),
            "Blocking automated release gate is green or reproducible",
            False,
        )
    return None


def _map_action(action: str) -> ReleaseFollowupSlice | None:
    if action == "complete_partial_manual_evidence":
        return ReleaseFollowupSlice(
            "REL-partial-manual-evidence-closeout",
            "Charlie",
            "Close out partial manual release evidence",
            (
                "docs/plans/1.0-manual-release-evidence-log.md",
                "docs/plans/1.0-evidence-release-checklist.md",
            ),
            "Every partial gate is either Go with evidence or No-Go with explicit blocker",
            False,
        )
    if action == "complete_manual_release_evidence":
        return ReleaseFollowupSlice(
            "REL-manual-evidence-closeout",
            "Charlie",
            "Complete missing or pending manual release evidence",
            (
                "docs/plans/1.0-manual-release-evidence-runbook.md",
                "docs/plans/1.0-manual-release-evidence-log.md",
            ),
            "No required manual gate is missing or pending",
            False,
        )
    return None
