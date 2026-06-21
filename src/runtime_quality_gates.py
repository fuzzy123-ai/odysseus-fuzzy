"""Runtime quality gate evaluation from scoped runtime evidence.

The evaluator still accepts already-collected snapshots, and the scoped runner
can collect a narrow set of local git/test evidence for Charlie verification.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shlex
import subprocess
from typing import Any, Iterable

from src.handoff_mailbox import HandoffStatus, ParsedHandoff
from src.quality_gates import QualityGate, QualityGateResult, QualityGateType


_MAX_TEXT = 220
_MAX_COMMAND_OUTPUT = 4000
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9._/ -]+$")
_ABS_WINDOWS_RE = re.compile(r"^[A-Za-z]:[\\/]")
_SHELL_META_RE = re.compile(r"[;&|<>`]")
_PYTHON_NAMES = {"python", "python.exe", "py", "py.exe"}
_DEFAULT_COMMAND_TIMEOUT_SECONDS = 120


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
class RuntimeCommandResult:
    argv: tuple[str, ...]
    exit_code: int
    output_summary: str
    timed_out: bool = False

    @classmethod
    def create(
        cls,
        *,
        argv: Iterable[Any],
        exit_code: Any,
        output_summary: Any = "",
        timed_out: Any = False,
    ) -> "RuntimeCommandResult":
        normalized_argv = tuple(_normalize_command_token(token) for token in argv)
        if not normalized_argv:
            raise RuntimeQualityGateError("argv must not be empty")
        try:
            code = int(exit_code)
        except (TypeError, ValueError):
            raise RuntimeQualityGateError("exit_code must be an int") from None
        return cls(
            argv=normalized_argv,
            exit_code=code,
            output_summary=_normalize_command_output(output_summary),
            timed_out=bool(timed_out),
        )


@dataclass(frozen=True, slots=True)
class RuntimeQualityGateRunRequest:
    agent_run_id: str
    plan_node_id: str
    subject_ref: str
    verified_at: str
    verified_by: str
    handoff: ParsedHandoff
    repo_root: Path
    test_commands: tuple[tuple[str, ...], ...]
    allowed_files: tuple[str, ...]
    hot_files: tuple[str, ...]
    timeout_seconds: int

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
        repo_root: Any = ".",
        test_commands: Iterable[Any] | None = None,
        allowed_files: Iterable[Any],
        hot_files: Iterable[Any] = (),
        timeout_seconds: Any = _DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ) -> "RuntimeQualityGateRunRequest":
        if not isinstance(handoff, ParsedHandoff):
            raise RuntimeQualityGateError("handoff must be a ParsedHandoff")
        try:
            timeout = int(timeout_seconds)
        except (TypeError, ValueError):
            raise RuntimeQualityGateError("timeout_seconds must be an int") from None
        if timeout <= 0 or timeout > 600:
            raise RuntimeQualityGateError("timeout_seconds must be between 1 and 600")
        commands = tuple(_normalize_focused_pytest_command(command) for command in (test_commands or handoff.tests))
        if len(commands) > 8:
            raise RuntimeQualityGateError("test_commands exceeds max count 8")
        root = Path(str(repo_root or ".")).resolve()
        return cls(
            agent_run_id=_normalize_slug(agent_run_id, field_name="agent_run_id"),
            plan_node_id=_normalize_slug(plan_node_id, field_name="plan_node_id"),
            subject_ref=_normalize_slug(subject_ref, field_name="subject_ref"),
            verified_at=_normalize_text(verified_at, field_name="verified_at", allow_empty=False),
            verified_by=_normalize_text(verified_by, field_name="verified_by", allow_empty=False),
            handoff=handoff,
            repo_root=root,
            test_commands=commands,
            allowed_files=_normalize_paths(allowed_files, field_name="allowed_files"),
            hot_files=_normalize_paths(hot_files, field_name="hot_files"),
            timeout_seconds=timeout,
        )


@dataclass(frozen=True, slots=True)
class RuntimeQualityGateRunResult:
    gate_result: QualityGateResult
    git_status: GitStatusSnapshot
    test_results: tuple[TestExecutionSnapshot, ...]
    command_results: tuple[RuntimeCommandResult, ...]

    @property
    def verified_done(self) -> bool:
        return self.gate_result.verified_done

    def audit_summary(self) -> dict[str, Any]:
        return {
            "verified_done": self.verified_done,
            "blocking_gate_ids": self.gate_result.blocking_gate_ids,
            "git": {
                "branch": self.git_status.branch,
                "clean": self.git_status.clean,
                "commit": self.git_status.commit,
                "dirty_file_count": len(self.git_status.dirty_files),
            },
            "test_count": len(self.test_results),
            "command_count": len(self.command_results),
        }


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


def run_scoped_runtime_quality_gates(
    request: RuntimeQualityGateRunRequest,
    *,
    command_runner: Any | None = None,
) -> RuntimeQualityGateRunResult:
    if not isinstance(request, RuntimeQualityGateRunRequest):
        raise RuntimeQualityGateError("request must be a RuntimeQualityGateRunRequest")
    runner = command_runner or _run_command
    command_results: list[RuntimeCommandResult] = []

    git_status, git_commands = _collect_git_status(request, runner=runner)
    command_results.extend(git_commands)

    test_results: list[TestExecutionSnapshot] = []
    for command in request.test_commands:
        result = runner(command, cwd=request.repo_root, timeout_seconds=request.timeout_seconds)
        if not isinstance(result, RuntimeCommandResult):
            raise RuntimeQualityGateError("command_runner must return RuntimeCommandResult")
        command_results.append(result)
        test_results.append(
            TestExecutionSnapshot.create(
                command=" ".join(result.argv),
                exit_code=result.exit_code,
                summary=result.output_summary or ("timed out" if result.timed_out else ""),
            )
        )

    gate_input = RuntimeQualityGateInput.create(
        agent_run_id=request.agent_run_id,
        plan_node_id=request.plan_node_id,
        subject_ref=request.subject_ref,
        verified_at=request.verified_at,
        verified_by=request.verified_by,
        handoff=request.handoff,
        git_status=git_status,
        test_results=test_results,
        changed_files=request.handoff.changed_files,
        allowed_files=request.allowed_files,
        hot_files=request.hot_files,
    )
    return RuntimeQualityGateRunResult(
        gate_result=evaluate_runtime_quality_gates(gate_input),
        git_status=git_status,
        test_results=tuple(test_results),
        command_results=tuple(command_results),
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


def _collect_git_status(
    request: RuntimeQualityGateRunRequest,
    *,
    runner: Any,
) -> tuple[GitStatusSnapshot, tuple[RuntimeCommandResult, ...]]:
    status_result = runner(("git", "status", "--short", "--branch"), cwd=request.repo_root, timeout_seconds=20)
    rev_result = runner(("git", "rev-parse", "--short", "HEAD"), cwd=request.repo_root, timeout_seconds=20)
    if not isinstance(status_result, RuntimeCommandResult) or not isinstance(rev_result, RuntimeCommandResult):
        raise RuntimeQualityGateError("command_runner must return RuntimeCommandResult")
    branch, staged, unstaged, untracked = _parse_git_status_short(status_result.output_summary)
    commit = _first_output_line(rev_result.output_summary)
    git_failed = status_result.exit_code != 0 or rev_result.exit_code != 0
    return (
        GitStatusSnapshot.create(
            branch=branch or "unknown",
            clean=not git_failed and not (staged or unstaged or untracked),
            commit=commit,
            staged_files=staged,
            unstaged_files=unstaged if not git_failed else (*unstaged, "git-status-error"),
            untracked_files=untracked,
        ),
        (status_result, rev_result),
    )


def _parse_git_status_short(output: str) -> tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    branch = "unknown"
    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    for raw_line in str(output or "").splitlines():
        line = raw_line.strip("\n")
        if not line:
            continue
        if line.startswith("## "):
            branch = line[3:].split("...", 1)[0].split(" ", 1)[0] or "unknown"
            continue
        if len(line) < 3:
            continue
        status = line[:2]
        path = _git_status_path(line[3:])
        if not path:
            continue
        if status == "??":
            untracked.append(path)
            continue
        if status[0] not in {" ", "?"}:
            staged.append(path)
        if status[1] not in {" ", "?"}:
            unstaged.append(path)
    return (
        branch,
        tuple(dict.fromkeys(staged)),
        tuple(dict.fromkeys(unstaged)),
        tuple(dict.fromkeys(untracked)),
    )


def _git_status_path(value: str) -> str:
    path = str(value or "").strip().strip('"').replace("\\", "/")
    if " -> " in path:
        path = path.rsplit(" -> ", 1)[-1]
    try:
        return _normalize_paths([path], field_name="git_status_path")[0]
    except (IndexError, RuntimeQualityGateError):
        return ""


def _normalize_focused_pytest_command(command: Any) -> tuple[str, ...]:
    if isinstance(command, str):
        raw = command.strip()
        if not raw:
            raise RuntimeQualityGateError("test command must not be empty")
        if _SHELL_META_RE.search(raw) or "$(" in raw:
            raise RuntimeQualityGateError("test command must not contain shell operators")
        argv = tuple(_normalize_command_token(part) for part in shlex.split(raw))
    elif isinstance(command, (list, tuple)):
        argv = tuple(_normalize_command_token(part) for part in command)
    else:
        raise RuntimeQualityGateError("test command must be a string or argv list")
    if not argv:
        raise RuntimeQualityGateError("test command must not be empty")
    pytest_index = _pytest_index(argv)
    if pytest_index is None:
        raise RuntimeQualityGateError("test command must be focused pytest")
    for token in argv[pytest_index + 1 :]:
        _validate_pytest_arg(token)
    return argv


def _pytest_index(argv: tuple[str, ...]) -> int | None:
    first = Path(argv[0]).name.lower()
    if first == "pytest":
        return 0
    if first in _PYTHON_NAMES and len(argv) >= 3 and argv[1] == "-m" and argv[2] == "pytest":
        return 2
    return None


def _validate_pytest_arg(token: str) -> None:
    if not token or token.startswith("-"):
        return
    normalized = token.replace("\\", "/")
    if normalized.startswith(("http://", "https://")):
        raise RuntimeQualityGateError("test command must not use network targets")
    if _ABS_WINDOWS_RE.match(token) or normalized.startswith("/") or ".." in normalized.split("/"):
        raise RuntimeQualityGateError("test command paths must be repo-relative")
    if "/" in normalized or normalized.endswith(".py"):
        if normalized != "tests" and not normalized.startswith("tests/"):
            raise RuntimeQualityGateError("test command must target tests/ paths")


def _normalize_command_token(value: Any) -> str:
    token = str(value or "").strip()
    if not token:
        raise RuntimeQualityGateError("command token must not be empty")
    if "\n" in token or "\r" in token or "\x00" in token:
        raise RuntimeQualityGateError("command token contains unsupported characters")
    if _SHELL_META_RE.search(token):
        raise RuntimeQualityGateError("command token must not contain shell operators")
    return token


def _normalize_command_output(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if "\x00" in text:
        raise RuntimeQualityGateError("command output contains unsupported characters")
    if len(text) > _MAX_COMMAND_OUTPUT:
        text = text[-_MAX_COMMAND_OUTPUT:]
    return text


def _run_command(argv: Iterable[str], *, cwd: Path, timeout_seconds: int) -> RuntimeCommandResult:
    normalized = tuple(_normalize_command_token(item) for item in argv)
    try:
        completed = subprocess.run(
            list(normalized),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        return RuntimeCommandResult.create(
            argv=normalized,
            exit_code=124,
            output_summary=_summarize_output(exc.stdout, exc.stderr, fallback="command timed out"),
            timed_out=True,
        )
    except OSError as exc:
        return RuntimeCommandResult.create(
            argv=normalized,
            exit_code=127,
            output_summary=_normalize_text(str(exc), field_name="command_error", allow_empty=True),
        )
    return RuntimeCommandResult.create(
        argv=normalized,
        exit_code=completed.returncode,
        output_summary=_summarize_output(completed.stdout, completed.stderr, fallback=f"exit {completed.returncode}"),
    )


def _summarize_output(stdout: Any, stderr: Any, *, fallback: str) -> str:
    text = "\n".join(str(part or "") for part in (stdout, stderr))
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return _normalize_text(lines[-1] if lines else fallback, field_name="output_summary", allow_empty=True)


def _first_output_line(output: str) -> str:
    for line in str(output or "").splitlines():
        text = line.strip()
        if text:
            return _normalize_text(text, field_name="output_line", allow_empty=True)
    return ""


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
