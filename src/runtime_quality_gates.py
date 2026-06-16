"""Runtime quality gate evaluation from injected evidence snapshots.

AUTO5 keeps command execution outside this module. The evaluator turns already
collected git/test/scope/handoff facts into QualityGateResult objects.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable

from src.handoff_mailbox import HandoffStatus, ParsedHandoff
from src.quality_gates import QualityGate, QualityGateResult, QualityGateType


_MAX_TEXT = 220
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9._/ -]+$")
_ABS_WINDOWS_RE = re.compile(r"^[A-Za-z]:[\\/]")


class RuntimeQualityGateError(ValueError):
    """Raised when runtime gate inputs are invalid."""


@dataclass(frozen=True, slots=True)
class GitStatusSnapshot:
    branch: str
    clean: bool
    commit: str
    staged_files: tuple[str, ...]
    unstaged_files: tuple[str, ...]
    untracked_files: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        branch: Any,
        clean: Any,
        commit: Any = "",
        staged_files: Iterable[Any] = (),
        unstaged_files: Iterable[Any] = (),
        untracked_files: Iterable[Any] = (),
    ) -> "GitStatusSnapshot":
        return cls(
            branch=_normalize_text(branch, field_name="branch", allow_empty=False),
            clean=bool(clean),
            commit=_normalize_text(commit, field_name="commit", allow_empty=True),
            staged_files=_normalize_paths(staged_files, field_name="staged_files"),
            unstaged_files=_normalize_paths(unstaged_files, field_name="unstaged_files"),
            untracked_files=_normalize_paths(untracked_files, field_name="untracked_files"),
        )

    @property
    def dirty_files(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.staged_files, *self.unstaged_files, *self.untracked_files)))


@dataclass(frozen=True, slots=True)
class TestExecutionSnapshot:
    command: str
    exit_code: int
    summary: str

    @classmethod
    def create(cls, *, command: Any, exit_code: Any, summary: Any = "") -> "TestExecutionSnapshot":
        try:
            code = int(exit_code)
        except (TypeError, ValueError):
            raise RuntimeQualityGateError("exit_code must be an int") from None
        return cls(
            command=_normalize_text(command, field_name="command", allow_empty=False),
            exit_code=code,
            summary=_normalize_text(summary, field_name="summary", allow_empty=True),
        )

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


@dataclass(frozen=True, slots=True)
class RuntimeQualityGateInput:
    agent_run_id: str
    plan_node_id: str
    subject_ref: str
    verified_at: str
    verified_by: str
    handoff: ParsedHandoff
    git_status: GitStatusSnapshot
    test_results: tuple[TestExecutionSnapshot, ...]
    changed_files: tuple[str, ...]
    allowed_files: tuple[str, ...]
    hot_files: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        agent_run_id: Any,
        plan_node_id: Any,
        subject_ref: Any,
        verified_at: Any,
        verified_by: Any,
        handoff: ParsedHandoff,
        git_status: GitStatusSnapshot,
        test_results: Iterable[TestExecutionSnapshot],
        changed_files: Iterable[Any],
        allowed_files: Iterable[Any],
        hot_files: Iterable[Any] = (),
    ) -> "RuntimeQualityGateInput":
        if not isinstance(handoff, ParsedHandoff):
            raise RuntimeQualityGateError("handoff must be a ParsedHandoff")
        if not isinstance(git_status, GitStatusSnapshot):
            raise RuntimeQualityGateError("git_status must be a GitStatusSnapshot")
        tests = tuple(test_results)
        if any(not isinstance(test, TestExecutionSnapshot) for test in tests):
            raise RuntimeQualityGateError("test_results must contain TestExecutionSnapshot items")
        return cls(
            agent_run_id=_normalize_slug(agent_run_id, field_name="agent_run_id"),
            plan_node_id=_normalize_slug(plan_node_id, field_name="plan_node_id"),
            subject_ref=_normalize_slug(subject_ref, field_name="subject_ref"),
            verified_at=_normalize_text(verified_at, field_name="verified_at", allow_empty=False),
            verified_by=_normalize_text(verified_by, field_name="verified_by", allow_empty=False),
            handoff=handoff,
            git_status=git_status,
            test_results=tests,
            changed_files=_normalize_paths(changed_files, field_name="changed_files"),
            allowed_files=_normalize_paths(allowed_files, field_name="allowed_files"),
            hot_files=_normalize_paths(hot_files, field_name="hot_files"),
        )


def evaluate_runtime_quality_gates(payload: RuntimeQualityGateInput) -> QualityGateResult:
    if not isinstance(payload, RuntimeQualityGateInput):
        raise RuntimeQualityGateError("payload must be a RuntimeQualityGateInput")
    return QualityGateResult.create(
        gates=[
            _handoff_gate(payload),
            _git_gate(payload),
            _test_gate(payload),
            _evidence_gate(payload),
            _scope_gate(payload),
            _hot_file_gate(payload),
        ]
    )


def _handoff_gate(payload: RuntimeQualityGateInput) -> QualityGate:
    if payload.handoff.status == HandoffStatus.DONE:
        return _gate(
            payload,
            gate_id="handoff-pass",
            gate_type="handoff",
            status="pass",
            severity="medium",
            evidence=[f"handoff done by {payload.handoff.agent}"],
        )
    return _gate(
        payload,
        gate_id="handoff-block",
        gate_type="handoff",
        status="block",
        severity="high",
        block_reason=f"handoff status is {payload.handoff.status.value}",
        next_action="resolve handoff before verifying done",
    )


def _git_gate(payload: RuntimeQualityGateInput) -> QualityGate:
    if payload.git_status.clean and payload.handoff.commit:
        return _gate(
            payload,
            gate_id="git-pass",
            gate_type="git",
            status="pass",
            severity="medium",
            evidence=[f"clean git snapshot on {payload.git_status.branch}", f"commit {payload.handoff.commit}"],
        )
    reasons: list[str] = []
    if not payload.git_status.clean:
        reasons.append("worktree dirty")
    if not payload.handoff.commit:
        reasons.append("handoff missing commit")
    if payload.git_status.dirty_files:
        reasons.append("dirty files: " + ", ".join(payload.git_status.dirty_files[:5]))
    return _gate(
        payload,
        gate_id="git-block",
        gate_type="git",
        status="block",
        severity="high",
        block_reason="; ".join(reasons),
        next_action="commit or clean scoped files before verified done",
    )


def _test_gate(payload: RuntimeQualityGateInput) -> QualityGate:
    if not payload.test_results:
        return _gate(
            payload,
            gate_id="tests-block",
            gate_type="tests",
            status="block",
            severity="high",
            block_reason="no verified test execution snapshot",
            next_action="run focused tests before verified done",
        )
    failed = tuple(test for test in payload.test_results if not test.passed)
    if failed:
        return _gate(
            payload,
            gate_id="tests-fail",
            gate_type="tests",
            status="fail",
            severity="critical",
            block_reason="failed tests: " + ", ".join(test.command for test in failed[:5]),
            next_action="fix focused test failures before dispatch",
        )
    return _gate(
        payload,
        gate_id="tests-pass",
        gate_type="tests",
        status="pass",
        severity="medium",
        evidence=[test.command for test in payload.test_results],
    )


def _evidence_gate(payload: RuntimeQualityGateInput) -> QualityGate:
    evidence = tuple((*payload.handoff.evidence, *(test.summary for test in payload.test_results if test.summary)))
    if evidence:
        return _gate(
            payload,
            gate_id="evidence-pass",
            gate_type="evidence",
            status="pass",
            severity="medium",
            evidence=evidence,
        )
    return _gate(
        payload,
        gate_id="evidence-block",
        gate_type="evidence",
        status="block",
        severity="medium",
        block_reason="handoff and test snapshots contain no evidence",
        next_action="attach concise verification evidence",
    )


def _scope_gate(payload: RuntimeQualityGateInput) -> QualityGate:
    allowed = set(payload.allowed_files)
    unexpected = tuple(path for path in payload.changed_files if path not in allowed)
    if not unexpected:
        return _gate(
            payload,
            gate_id="scope-pass",
            gate_type="scope",
            status="pass",
            severity="medium",
            evidence=["changed files are within declared scope"],
        )
    return _gate(
        payload,
        gate_id="scope-block",
        gate_type="scope",
        status="block",
        severity="high",
        block_reason="out-of-scope files: " + ", ".join(unexpected[:5]),
        next_action="split or review out-of-scope changes",
    )


def _hot_file_gate(payload: RuntimeQualityGateInput) -> QualityGate:
    hot = set(payload.hot_files)
    overlap = tuple(path for path in payload.changed_files if path in hot)
    if not overlap:
        return _gate(
            payload,
            gate_id="hot-file-pass",
            gate_type=QualityGateType.HOT_FILE,
            status="pass",
            severity="medium",
            evidence=["no hot-file overlap"],
        )
    return _gate(
        payload,
        gate_id="hot-file-block",
        gate_type=QualityGateType.HOT_FILE,
        status="block",
        severity="critical",
        block_reason="hot-file overlap: " + ", ".join(overlap[:5]),
        next_action="serialize work before touching hot files",
    )


def _gate(
    payload: RuntimeQualityGateInput,
    *,
    gate_id: str,
    gate_type: QualityGateType | str,
    status: str,
    severity: str,
    evidence: Iterable[str] = (),
    block_reason: str = "",
    next_action: str = "",
) -> QualityGate:
    return QualityGate.create(
        gate_id=gate_id,
        gate_type=gate_type,
        subject_ref=payload.subject_ref,
        agent_run_id=payload.agent_run_id,
        plan_node_id=payload.plan_node_id,
        status=status,
        severity=severity,
        required=True,
        evidence=evidence,
        verified_at=payload.verified_at,
        verified_by=payload.verified_by,
        block_reason=block_reason,
        next_action=next_action,
    )


def _normalize_paths(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        path = str(value or "").strip().replace("\\", "/")
        if not path:
            continue
        if _ABS_WINDOWS_RE.match(str(value)) or path.startswith("/") or ".." in path.split("/"):
            raise RuntimeQualityGateError(f"{field_name} must contain repo-relative paths")
        if not _SAFE_PATH_RE.fullmatch(path):
            raise RuntimeQualityGateError(f"{field_name} contains unsupported characters")
        normalized.append(path)
    return tuple(dict.fromkeys(normalized))


def _normalize_slug(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = re.sub(r"-{2,}", "-", text)
    if not text:
        raise RuntimeQualityGateError(f"{field_name} must not be empty")
    return text


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool) -> str:
    text = " ".join(str(value or "").split())
    if not text and not allow_empty:
        raise RuntimeQualityGateError(f"{field_name} must not be empty")
    if len(text) > _MAX_TEXT:
        text = text[: _MAX_TEXT - 3] + "..."
    return text
