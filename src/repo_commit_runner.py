"""Operator-gated commit runner for registered repositories."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Protocol

from src.agent_verification_receipt import ReceiptError, repository_binding
from src.claim_evidence_gate import (
    AgentMaintenanceCompletionEvidence,
    evaluate_agent_maintenance_completion,
)
from src.constants import MAX_OUTPUT_CHARS
from src.project_forge_contract import ProjectForgeContractError, validate_persisted_text
from src.repo_registry import RepoRecord, RepoRegistry, RepoRegistryError


_DECISIONS = ("blocked", "hold", "plan_ready")
_MAX_TIMEOUT_SECONDS = 300
_MAX_CHANGED_PATHS = 80
_MAX_REVIEWED_CONTENT_BYTES = 32 * 1024 * 1024
_SECRET_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer)\b\s*[:=]\s*\S+")
_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s\t]+")
_ABSOLUTE_PATH_RE = re.compile(r"(?<![A-Za-z0-9._-])/(?:[^\s/]+/)*[^\s]+")
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9._/@+ -]{1,180}$")
_COMMIT_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_UNSAFE_CONTROL_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")


class RepoCommitRunnerError(ValueError):
    """Raised when a registered repo commit request is unsafe."""


class RepoCommitCommandRunner(Protocol):
    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        timeout_seconds: int,
        env: Mapping[str, str],
    ) -> "RepoCommitCommandResult":
        ...


@dataclass(frozen=True, slots=True)
class RepoCommitCommandResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    duration_seconds: float | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "ok": self.ok,
            "stdout": _bounded_redacted(self.stdout),
            "stderr": _bounded_redacted(self.stderr),
            "timed_out": self.timed_out,
            "duration_seconds": self.duration_seconds,
        }


@dataclass(frozen=True, slots=True)
class RepoCommitStatusEntry:
    code: str
    path: str
    staged: bool

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "path": self.path, "staged": self.staged}


@dataclass(frozen=True, slots=True)
class RepoCommitIndexEntry:
    mode: str
    blob_id: str
    stage: int
    path: str


@dataclass(frozen=True, slots=True)
class RepoCommitAuthority:
    action: str
    repo_id: str
    reviewed_paths: tuple[str, ...]
    head_revision: str
    reviewed_content_digest: str
    reviewed_index: tuple[RepoCommitIndexEntry, ...]
    granted: bool


@dataclass(frozen=True, slots=True)
class RepoCommitPlan:
    repo_id: str
    repo_path_ref: str
    branch_line: str
    objective: str
    changed_paths: tuple[str, ...]
    commit_message: str
    checks_passed: bool
    content_reviewed: bool
    confirmed: bool
    completion_verified: bool
    action_authorized: bool
    decision: str
    blockers: tuple[str, ...]
    status_entries: tuple[RepoCommitStatusEntry, ...]
    planned_steps: tuple[dict[str, Any], ...]
    next_human_decision: str
    commit_body: str = ""

    @property
    def can_commit(self) -> bool:
        return self.decision == "plan_ready"

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "repo_path_ref": self.repo_path_ref,
            "branch_line": self.branch_line,
            "objective": self.objective,
            "changed_paths": list(self.changed_paths),
            "commit_message": self.commit_message,
            "commit_body": self.commit_body,
            "checks_passed": self.checks_passed,
            "content_reviewed": self.content_reviewed,
            "confirmed": self.confirmed,
            "completion_verified": self.completion_verified,
            "action_authorized": self.action_authorized,
            "can_commit": self.can_commit,
            "decision": self.decision,
            "blockers": list(self.blockers),
            "status_entries": [entry.to_dict() for entry in self.status_entries],
            "planned_steps": [dict(step) for step in self.planned_steps],
            "next_human_decision": self.next_human_decision,
        }


@dataclass(frozen=True, slots=True)
class RepoCommitReport:
    status: str
    executed: bool
    plan: RepoCommitPlan
    command_results: tuple[RepoCommitCommandResult, ...]
    committed_paths: tuple[str, ...]
    blockers: tuple[str, ...]
    commit_sha: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "executed": self.executed,
            "plan": self.plan.to_dict(),
            "command_results": [result.to_dict() for result in self.command_results],
            "committed_paths": list(self.committed_paths),
            "blockers": list(self.blockers),
            "commit_sha": self.commit_sha,
        }


def build_repo_commit_authority(
    *,
    repo_id: Any,
    repo_path: str | Path,
    reviewed_paths: Iterable[Any],
    granted: bool,
) -> RepoCommitAuthority:
    """Bind explicit commit authority to AMH-04's admitted current dirty state."""

    if type(granted) is not bool:
        raise RepoCommitRunnerError("granted must be a boolean")
    normalized_repo_id = _normalize_text(repo_id, field_name="repo_id", max_len=120)
    paths = _dedupe_paths(reviewed_paths)
    root = Path(repo_path).resolve()
    if not root.is_dir() or not (root / ".git").exists():
        raise RepoCommitRunnerError("repo_path is not a local Git repository")
    try:
        head_revision, content_digest, reviewed_index = _reviewed_content_binding(
            root,
            paths,
        )
    except (OSError, ReceiptError) as exc:
        raise RepoCommitRunnerError("safe repository binding is unavailable") from exc
    return RepoCommitAuthority(
        action="commit",
        repo_id=normalized_repo_id,
        reviewed_paths=paths,
        head_revision=head_revision,
        reviewed_content_digest=content_digest,
        reviewed_index=reviewed_index,
        granted=granted,
    )


def plan_repo_local_commit(
    *,
    registry: RepoRegistry,
    repo_id: Any,
    workspace_base: str | Path,
    objective: Any,
    changed_paths: Iterable[Any],
    checks_passed: bool,
    content_reviewed: bool,
    confirmed: bool = False,
    commit_message: Any | None = None,
    commit_body: Any | None = None,
    repo_roots: Mapping[str, str | os.PathLike[str]] | None = None,
    command_runner: RepoCommitCommandRunner | None = None,
    completion_evidence: AgentMaintenanceCompletionEvidence | None = None,
    commit_authority: RepoCommitAuthority | None = None,
) -> RepoCommitReport:
    record, repo_path = _resolve_repo(
        registry=registry,
        repo_id=repo_id,
        workspace_base=workspace_base,
        repo_roots=repo_roots,
    )
    paths = _dedupe_paths(changed_paths)
    completion_verified, action_authorized = _current_commit_gates(
        repo_path=repo_path,
        repo_id=record.repo_id,
        changed_paths=paths,
        completion_evidence=completion_evidence,
        commit_authority=commit_authority,
    )
    status_result = (command_runner or run_git_commit_subprocess_command)(
        ("git", "status", "--short", "--branch"),
        cwd=repo_path,
        timeout_seconds=_MAX_TIMEOUT_SECONDS,
        env={},
    )
    if not status_result.ok:
        plan = _blocked_plan(
            record=record,
            objective=objective,
            changed_paths=paths,
            checks_passed=checks_passed,
            content_reviewed=content_reviewed,
            confirmed=confirmed,
            commit_message=commit_message,
            commit_body=commit_body,
            reason="status command failed before commit planning",
            status_output=status_result.stdout,
            completion_verified=completion_verified,
            action_authorized=action_authorized,
        )
        return RepoCommitReport(
            status="blocked",
            executed=False,
            plan=plan,
            command_results=(status_result,),
            committed_paths=(),
            blockers=plan.blockers,
        )

    plan = build_repo_commit_plan(
        record=record,
        repo_path=repo_path,
        objective=objective,
        changed_paths=paths,
        checks_passed=checks_passed,
        content_reviewed=content_reviewed,
        confirmed=confirmed,
        commit_message=commit_message,
        commit_body=commit_body,
        status_output=status_result.stdout,
        completion_verified=completion_verified,
        action_authorized=action_authorized,
    )
    return RepoCommitReport(
        status=plan.decision,
        executed=False,
        plan=plan,
        command_results=(status_result,),
        committed_paths=(),
        blockers=plan.blockers,
    )


def run_repo_local_commit(
    *,
    registry: RepoRegistry,
    repo_id: Any,
    workspace_base: str | Path,
    objective: Any,
    changed_paths: Iterable[Any],
    checks_passed: bool,
    content_reviewed: bool,
    confirmed: bool,
    commit_message: Any | None = None,
    commit_body: Any | None = None,
    repo_roots: Mapping[str, str | os.PathLike[str]] | None = None,
    command_runner: RepoCommitCommandRunner | None = None,
    completion_evidence: AgentMaintenanceCompletionEvidence | None = None,
    commit_authority: RepoCommitAuthority | None = None,
) -> RepoCommitReport:
    record, repo_path = _resolve_repo(
        registry=registry,
        repo_id=repo_id,
        workspace_base=workspace_base,
        repo_roots=repo_roots,
    )
    runner = command_runner or run_git_commit_subprocess_command
    paths = _dedupe_paths(changed_paths)
    completion_verified, action_authorized = _current_commit_gates(
        repo_path=repo_path,
        repo_id=record.repo_id,
        changed_paths=paths,
        completion_evidence=completion_evidence,
        commit_authority=commit_authority,
    )
    status_result = runner(
        ("git", "status", "--short", "--branch"),
        cwd=repo_path,
        timeout_seconds=_MAX_TIMEOUT_SECONDS,
        env={},
    )
    if not status_result.ok:
        plan = _blocked_plan(
            record=record,
            objective=objective,
            changed_paths=paths,
            checks_passed=checks_passed,
            content_reviewed=content_reviewed,
            confirmed=confirmed,
            commit_message=commit_message,
            commit_body=commit_body,
            reason="status command failed before commit",
            status_output=status_result.stdout,
            completion_verified=completion_verified,
            action_authorized=action_authorized,
        )
        return RepoCommitReport(
            status="blocked",
            executed=False,
            plan=plan,
            command_results=(status_result,),
            committed_paths=(),
            blockers=plan.blockers,
        )

    plan = build_repo_commit_plan(
        record=record,
        repo_path=repo_path,
        objective=objective,
        changed_paths=paths,
        checks_passed=checks_passed,
        content_reviewed=content_reviewed,
        confirmed=confirmed,
        commit_message=commit_message,
        commit_body=commit_body,
        status_output=status_result.stdout,
        completion_verified=completion_verified,
        action_authorized=action_authorized,
    )
    results: list[RepoCommitCommandResult] = [status_result]
    if not plan.can_commit:
        return RepoCommitReport(
            status="blocked",
            executed=False,
            plan=plan,
            command_results=tuple(results),
            committed_paths=(),
            blockers=plan.blockers,
        )

    for path in plan.changed_paths:
        _assert_exact_commit_path(repo_path, path, plan.status_entries)

    add_command = ("git", "add", "--", *plan.changed_paths)
    add_result = runner(add_command, cwd=repo_path, timeout_seconds=_MAX_TIMEOUT_SECONDS, env={})
    results.append(add_result)
    if not add_result.ok:
        return RepoCommitReport(
            status="failed",
            executed=True,
            plan=plan,
            command_results=tuple(results),
            committed_paths=(),
            blockers=("commit command failed: git add",),
        )

    staged_command = ("git", "diff", "--cached", "--name-only", "-z")
    staged_result = runner(staged_command, cwd=repo_path, timeout_seconds=_MAX_TIMEOUT_SECONDS, env={})
    results.append(staged_result)
    try:
        staged_paths = _parse_staged_paths(staged_result.stdout) if staged_result.ok else ()
    except RepoCommitRunnerError:
        staged_paths = ()
    if not staged_result.ok or set(staged_paths) != set(plan.changed_paths) or len(staged_paths) != len(plan.changed_paths):
        return RepoCommitReport(
            status="failed",
            executed=True,
            plan=plan,
            command_results=tuple(results),
            committed_paths=(),
            blockers=("staging_requires_review: staged paths do not exactly match reviewed paths",),
        )

    commit_command = (
        ("git", "commit", "-m", plan.commit_message, "-m", plan.commit_body)
        if plan.commit_body
        else ("git", "commit", "-m", plan.commit_message)
    )
    commit_result = runner(commit_command, cwd=repo_path, timeout_seconds=_MAX_TIMEOUT_SECONDS, env={})
    results.append(commit_result)
    if not commit_result.ok:
        return RepoCommitReport(
            status="failed",
            executed=True,
            plan=plan,
            command_results=tuple(results),
            committed_paths=(),
            blockers=("commit command failed: git commit",),
        )

    sha_command = ("git", "rev-parse", "--verify", "HEAD^{commit}")
    sha_result = runner(sha_command, cwd=repo_path, timeout_seconds=_MAX_TIMEOUT_SECONDS, env={})
    results.append(sha_result)
    commit_sha = _parse_commit_sha(sha_result.stdout) if sha_result.ok else ""
    committed_paths = tuple(f"{record.path_ref}/{path}" for path in plan.changed_paths)
    if not _COMMIT_SHA_RE.fullmatch(commit_sha):
        return RepoCommitReport(
            status="reconcile_required",
            executed=True,
            plan=plan,
            command_results=tuple(results),
            committed_paths=committed_paths,
            blockers=("commit succeeded but its full object id could not be verified",),
        )

    return RepoCommitReport(
        status="committed",
        executed=True,
        plan=plan,
        command_results=tuple(results),
        committed_paths=committed_paths,
        blockers=(),
        commit_sha=commit_sha,
    )


def build_repo_commit_plan(
    *,
    record: RepoRecord,
    repo_path: Path,
    objective: Any,
    changed_paths: Iterable[Any],
    checks_passed: bool,
    content_reviewed: bool,
    confirmed: bool,
    status_output: str,
    commit_message: Any | None = None,
    commit_body: Any | None = None,
) -> RepoCommitPlan:
    if not isinstance(record, RepoRecord):
        raise RepoCommitRunnerError("record must be a RepoRecord")
    objective_text = _normalize_text(objective, field_name="objective", max_len=500)
    paths = _dedupe_paths(changed_paths)
    message = _normalize_commit_title(
        commit_message if commit_message is not None else _default_commit_message(objective_text),
    )
    body = _normalize_commit_body(commit_body)
    branch_line, entries = parse_repo_commit_status(status_output)
    staged_paths = tuple(entry.path for entry in entries if entry.staged)

    blockers: list[str] = []
    if "commit" not in record.allowed_actions:
        blockers.append("repo allowed_actions does not include commit")
    checks_are_bool = type(checks_passed) is bool
    content_is_bool = type(content_reviewed) is bool
    confirmed_is_bool = type(confirmed) is bool
    if not confirmed_is_bool:
        blockers.append("confirmed must be a boolean")
    elif not confirmed:
        blockers.append("confirmed=true is required before staging and committing reviewed paths")
    if not checks_are_bool:
        blockers.append("checks_passed must be a boolean")
    elif not checks_passed:
        blockers.append("checks_passed=true is required before commit")
    if not content_is_bool:
        blockers.append("content_reviewed must be a boolean")
    elif not content_reviewed:
        blockers.append("content_reviewed=true is required to confirm no secret or private-content risk")
    if completion_verified is not True:
        blockers.append("current claims and machine verification receipt are required before commit")
    if action_authorized is not True:
        blockers.append("typed explicit commit authority is required")
    if not paths:
        blockers.append("changed_paths are required before commit")
    if staged_paths:
        blockers.append("repo has pre-existing staged changes; clear staging before using the commit runner")
    for path in paths:
        try:
            _assert_exact_commit_path(repo_path, path, entries)
        except RepoCommitRunnerError as exc:
            blockers.append(str(exc))

    decision = "plan_ready" if not blockers else "hold"
    return RepoCommitPlan(
        repo_id=record.repo_id,
        repo_path_ref=record.path_ref,
        branch_line=_bounded_redacted(branch_line),
        objective=objective_text,
        changed_paths=paths,
        commit_message=message,
        checks_passed=checks_passed if checks_are_bool else False,
        content_reviewed=content_reviewed if content_is_bool else False,
        confirmed=confirmed if confirmed_is_bool else False,
        decision=_normalize_choice(decision, field_name="decision", choices=_DECISIONS),
        blockers=tuple(dict.fromkeys(blockers)),
        status_entries=entries,
        planned_steps=(
            {"step_id": "git_status", "summary": "capture registered repo status", "executes": True},
            {"step_id": "git_add", "summary": f"stage {len(paths)} reviewed path(s)", "executes": True},
            {"step_id": "git_commit", "summary": f"commit reviewed paths: {message}", "executes": True},
            {"step_id": "push_gate", "summary": "Push is handled by the separate push runner", "executes": False},
        ),
        next_human_decision=_next_human_decision(decision),
        commit_body=body,
    )


def parse_repo_commit_status(output: str) -> tuple[str, tuple[RepoCommitStatusEntry, ...]]:
    lines = [line for line in str(output or "").splitlines() if line.strip()]
    branch_line = lines[0] if lines and lines[0].startswith("##") else ""
    entry_lines = lines[1:] if branch_line else lines
    if len(entry_lines) > _MAX_CHANGED_PATHS:
        raise RepoCommitRunnerError("repository status exceeds the safe changed path limit")
    entries: list[RepoCommitStatusEntry] = []
    for raw_line in entry_lines[:_MAX_CHANGED_PATHS]:
        if len(raw_line) < 4:
            continue
        code = raw_line[:2]
        path = _status_path(raw_line[3:].strip())
        if not path:
            continue
        try:
            normalized_path = _normalize_repo_path(path, field_name="status_path")
        except RepoCommitRunnerError:
            normalized_path = _bounded_redacted(path)
        staged = code != "??" and code[0] != " "
        entries.append(RepoCommitStatusEntry(code=_bounded_redacted(code), path=normalized_path, staged=staged))
    return branch_line, tuple(entries)


def run_git_commit_subprocess_command(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    timeout_seconds: int,
    env: Mapping[str, str],
) -> RepoCommitCommandResult:
    if not repo_commit_command_is_allowed(argv):
        raise RepoCommitRunnerError("unsupported repo commit command")
    started = time.monotonic()
    try:
        completed = subprocess.run(
            list(argv),
            cwd=str(cwd),
            env=_merge_env(env),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return RepoCommitCommandResult(
            exit_code=124,
            stdout=str(exc.stdout or ""),
            stderr=str(exc.stderr or "command timed out"),
            timed_out=True,
            duration_seconds=round(time.monotonic() - started, 3),
        )
    return RepoCommitCommandResult(
        exit_code=int(completed.returncode),
        stdout=str(completed.stdout or ""),
        stderr=str(completed.stderr or ""),
        timed_out=False,
        duration_seconds=round(time.monotonic() - started, 3),
    )


def repo_commit_command_is_allowed(argv: tuple[str, ...]) -> bool:
    if argv == ("git", "status", "--short", "--branch"):
        return True
    if len(argv) >= 4 and argv[:3] == ("git", "add", "--"):
        return all(_path_arg_is_safe(path) for path in argv[3:])
    if argv == ("git", "diff", "--cached", "--name-only", "-z"):
        return True
    if len(argv) == 4 and argv[:3] == ("git", "commit", "-m"):
        try:
            _normalize_commit_title(argv[3])
            return True
        except RepoCommitRunnerError:
            return False
    if len(argv) == 6 and argv[:3] == ("git", "commit", "-m") and argv[4] == "-m":
        try:
            _normalize_commit_title(argv[3])
            return bool(_normalize_commit_body(argv[5]))
        except RepoCommitRunnerError:
            return False
    if argv == ("git", "rev-parse", "--verify", "HEAD^{commit}"):
        return True
    return False


def _blocked_plan(
    *,
    record: RepoRecord,
    objective: Any,
    changed_paths: Iterable[Any],
    checks_passed: bool,
    content_reviewed: bool,
    confirmed: bool,
    commit_message: Any | None,
    commit_body: Any | None,
    reason: str,
    status_output: str,
    completion_verified: bool = False,
    action_authorized: bool = False,
) -> RepoCommitPlan:
    objective_text = _normalize_text(objective, field_name="objective", max_len=500)
    paths = _dedupe_paths(changed_paths)
    message = _normalize_commit_title(
        commit_message if commit_message is not None else _default_commit_message(objective_text),
    )
    body = _normalize_commit_body(commit_body)
    branch_line, entries = parse_repo_commit_status(status_output)
    return RepoCommitPlan(
        repo_id=record.repo_id,
        repo_path_ref=record.path_ref,
        branch_line=_bounded_redacted(branch_line),
        objective=objective_text,
        changed_paths=paths,
        commit_message=message,
        checks_passed=checks_passed if type(checks_passed) is bool else False,
        content_reviewed=content_reviewed if type(content_reviewed) is bool else False,
        confirmed=confirmed if type(confirmed) is bool else False,
        decision="blocked",
        blockers=(reason,),
        status_entries=entries,
        planned_steps=({"step_id": "git_status", "summary": "capture registered repo status", "executes": True},),
        next_human_decision="Fix the local repo status read before planning a commit.",
        commit_body=body,
    )


def _resolve_repo(
    *,
    registry: RepoRegistry,
    repo_id: Any,
    workspace_base: str | Path,
    repo_roots: Mapping[str, str | os.PathLike[str]] | None = None,
) -> tuple[RepoRecord, Path]:
    if not isinstance(registry, RepoRegistry):
        raise RepoCommitRunnerError("registry must be a RepoRegistry")
    try:
        record = registry.get(repo_id)
    except RepoRegistryError as exc:
        raise RepoCommitRunnerError(str(exc)) from exc
    base = Path(workspace_base).resolve()
    roots = {str(key): Path(value).resolve() for key, value in (repo_roots or {}).items()}
    repo_path = roots.get(record.repo_id, (base / record.path_ref).resolve())
    _assert_child_path(base, repo_path)
    if not repo_path.is_dir() or not (repo_path / ".git").exists():
        raise RepoCommitRunnerError("registered repo path is not a local Git repository")
    return record, repo_path


def _assert_exact_commit_path(repo_path: Path, path: str, entries: tuple[RepoCommitStatusEntry, ...]) -> None:
    matching_entries = tuple(entry for entry in entries if entry.path == path)
    if not matching_entries:
        raise RepoCommitRunnerError(f"changed_path `{path}` is not changed in the initial repository status")
    candidate = (repo_path / path).resolve()
    _assert_child_path(repo_path, candidate)
    if candidate.is_dir():
        raise RepoCommitRunnerError(f"changed_path `{path}` is a directory; provide exact file paths")
    if candidate.exists():
        return
    if any("D" in entry.code for entry in matching_entries):
        return
    raise RepoCommitRunnerError(f"changed_path `{path}` does not exist and is not a tracked deletion")


def _current_commit_gates(
    *,
    repo_path: Path,
    repo_id: str,
    changed_paths: tuple[str, ...],
    completion_evidence: AgentMaintenanceCompletionEvidence | None,
    commit_authority: RepoCommitAuthority | None,
) -> tuple[bool, bool]:
    completion = evaluate_agent_maintenance_completion(
        completion_evidence,
        repo_root=repo_path,
    )
    return (
        completion.completed,
        _commit_authority_matches_current(
            commit_authority,
            repo_path=repo_path,
            repo_id=repo_id,
            changed_paths=changed_paths,
        ),
    )


def _read_staged_review_binding(
    runner: RepoCommitCommandRunner,
    *,
    repo_path: Path,
    paths: tuple[str, ...],
    authority: RepoCommitAuthority | None,
) -> tuple[tuple[RepoCommitCommandResult, ...], bool]:
    if not isinstance(authority, RepoCommitAuthority):
        return (), False
    index_result = runner(
        ("git", "ls-files", "--stage", "-z", "--", *paths),
        cwd=repo_path,
        timeout_seconds=_MAX_TIMEOUT_SECONDS,
        env={},
    )
    hash_results = tuple(
        runner(
            ("git", "hash-object", "--path", path, "--", path),
            cwd=repo_path,
            timeout_seconds=_MAX_TIMEOUT_SECONDS,
            env={},
        )
        for path in paths
    )
    results = (index_result, *hash_results)
    if not index_result.ok or any(not result.ok for result in hash_results):
        return results, False
    try:
        staged_entries = _parse_index_entries(index_result.stdout)
        worktree_blobs = tuple(
            _parse_hash_object_output(result.stdout, paths=(path,))[0]
            for path, result in zip(paths, hash_results, strict=True)
        )
    except RepoCommitRunnerError:
        return results, False
    expected_by_path = {entry.path: entry for entry in authority.reviewed_index}
    staged_by_path = {entry.path: entry for entry in staged_entries}
    worktree_by_path = dict(zip(paths, worktree_blobs, strict=True))
    exact = set(expected_by_path) == set(paths) == set(staged_by_path)
    if exact:
        for path in paths:
            expected = expected_by_path[path]
            staged = staged_by_path[path]
            if (
                staged.mode != expected.mode
                or staged.stage != 0
                or staged.blob_id != expected.blob_id
                or worktree_by_path[path] != expected.blob_id
            ):
                exact = False
                break
    return results, exact


def _commit_authority_matches_current(
    authority: RepoCommitAuthority | None,
    *,
    repo_path: Path,
    repo_id: str,
    changed_paths: tuple[str, ...],
) -> bool:
    if (
        not isinstance(authority, RepoCommitAuthority)
        or authority.action != "commit"
        or authority.granted is not True
        or authority.repo_id != repo_id
        or authority.reviewed_paths != changed_paths
    ):
        return False
    try:
        head_revision, content_digest, reviewed_index = _reviewed_content_binding(
            repo_path,
            changed_paths,
        )
    except (OSError, ReceiptError, RepoCommitRunnerError):
        return False
    return (
        head_revision == authority.head_revision
        and content_digest == authority.reviewed_content_digest
        and reviewed_index == authority.reviewed_index
    )


def _reviewed_content_binding(
    repo_path: Path,
    reviewed_paths: tuple[str, ...],
) -> tuple[str, str, tuple[RepoCommitIndexEntry, ...]]:
    admission_before = repository_binding(repo_path)
    if admission_before.workspace_state != "dirty_diff":
        raise RepoCommitRunnerError("commit authority requires a safely admitted dirty diff")
    digest = hashlib.sha256()
    digest.update(b"odysseus.repo_commit_review.v1\0")
    digest.update(admission_before.head_revision.encode("ascii"))
    existing_index = _read_index_entries_direct(repo_path, reviewed_paths)
    existing_by_path = {
        entry.path: entry
        for entry in existing_index
        if entry.stage == 0
    }
    total = 0
    reviewed_index: list[RepoCommitIndexEntry] = []
    for path in reviewed_paths:
        lexical = repo_path / path
        if lexical.is_symlink():
            raise RepoCommitRunnerError("reviewed path must be a regular repository file")
        candidate = lexical.resolve(strict=True)
        _assert_child_path(repo_path, candidate)
        if not candidate.is_file():
            raise RepoCommitRunnerError("reviewed path must be a regular repository file")
        data = candidate.read_bytes()
        total += len(data)
        if total > _MAX_REVIEWED_CONTENT_BYTES:
            raise RepoCommitRunnerError("reviewed content exceeds the safe size limit")
        encoded_path = path.encode("utf-8")
        digest.update(len(encoded_path).to_bytes(4, "big"))
        digest.update(encoded_path)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
        existing = existing_by_path.get(path)
        mode = (
            existing.mode
            if existing is not None
            else "100755"
            if candidate.stat().st_mode & stat.S_IXUSR
            else "100644"
        )
        reviewed_index.append(
            RepoCommitIndexEntry(
                mode=mode,
                blob_id=_read_filtered_blob_id(repo_path, path),
                stage=0,
                path=path,
            )
        )
    admission_after = repository_binding(repo_path)
    if admission_after != admission_before:
        raise RepoCommitRunnerError("repository changed while reviewed content was bound")
    return admission_after.head_revision, digest.hexdigest(), tuple(reviewed_index)


def _assert_child_path(parent: Path, child: Path) -> None:
    parent_resolved = parent.resolve()
    child_resolved = child.resolve()
    try:
        child_resolved.relative_to(parent_resolved)
    except ValueError as exc:
        raise RepoCommitRunnerError("commit path escapes registered repository") from exc


def _git_metadata_directory(repo_path: Path) -> Path:
    marker = repo_path / ".git"
    if marker.is_dir():
        return marker.resolve()
    if not marker.is_file() or marker.stat().st_size > 4096:
        raise RepoCommitRunnerError("Git metadata directory is unavailable")
    try:
        text = marker.read_text(encoding="utf-8", errors="strict").strip()
    except (OSError, UnicodeError) as exc:
        raise RepoCommitRunnerError("Git metadata directory is unavailable") from exc
    prefix = "gitdir:"
    if not text.lower().startswith(prefix) or "\n" in text or "\r" in text:
        raise RepoCommitRunnerError("Git metadata directory is unavailable")
    raw_path = text[len(prefix) :].strip()
    if not raw_path:
        raise RepoCommitRunnerError("Git metadata directory is unavailable")
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = marker.parent / candidate
    resolved = candidate.resolve()
    if not resolved.is_dir():
        raise RepoCommitRunnerError("Git metadata directory is unavailable")
    return resolved


def _dedupe_paths(values: Iterable[Any]) -> tuple[str, ...]:
    paths: list[str] = []
    for value in values:
        path = _normalize_repo_path(value, field_name="changed_path")
        if path not in paths:
            paths.append(path)
    if len(paths) > _MAX_CHANGED_PATHS:
        raise RepoCommitRunnerError(f"changed_paths exceeds max length {_MAX_CHANGED_PATHS}")
    return tuple(paths)


def _normalize_repo_path(value: Any, *, field_name: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise RepoCommitRunnerError(f"{field_name} must not be empty")
    if "\\" in raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise RepoCommitRunnerError(f"{field_name} must be repo-relative and use forward slashes")
    if not _SAFE_PATH_RE.fullmatch(raw):
        raise RepoCommitRunnerError(f"{field_name} contains unsupported characters")
    parts = PurePosixPath(raw).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise RepoCommitRunnerError(f"{field_name} must not contain traversal segments")
    path = "/".join(parts)
    lowered = path.lower()
    blocked_names = {".env", ".git", ".ssh", "id_rsa", "id_dsa", "id_ed25519"}
    if any(part.lower() in blocked_names for part in parts):
        raise RepoCommitRunnerError(f"{field_name} targets a blocked repo path")
    return path


def _normalize_text(value: Any, *, field_name: str, max_len: int = 220) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        raise RepoCommitRunnerError(f"{field_name} must not be empty")
    if len(text) > max_len:
        raise RepoCommitRunnerError(f"{field_name} exceeds max length {max_len}")
    if _SECRET_RE.search(text):
        raise RepoCommitRunnerError(f"{field_name} appears to contain secret material")
    return text


def _normalize_commit_title(value: Any) -> str:
    raw = str(value or "")
    if _UNSAFE_CONTROL_RE.search(raw) or "\n" in raw or "\r" in raw:
        raise RepoCommitRunnerError("commit_message contains unsupported characters")
    try:
        return validate_persisted_text(raw, field_name="commit_message", max_len=120)
    except ProjectForgeContractError as exc:
        raise RepoCommitRunnerError(str(exc)) from exc


def _normalize_commit_body(value: Any | None) -> str:
    if value is None:
        return ""
    raw = str(value)
    if _UNSAFE_CONTROL_RE.search(raw) or "\r" in raw:
        raise RepoCommitRunnerError("commit_body contains unsupported characters")
    try:
        return validate_persisted_text(
            raw,
            field_name="commit_body",
            allow_empty=True,
            max_len=6000,
            multiline=True,
        )
    except ProjectForgeContractError as exc:
        raise RepoCommitRunnerError(str(exc)) from exc


def _parse_staged_paths(output: str) -> tuple[str, ...]:
    raw = str(output or "")
    if not raw:
        return ()
    if not raw.endswith("\x00"):
        raise RepoCommitRunnerError("staged path output is not NUL terminated")
    paths = tuple(_normalize_repo_path(value, field_name="staged_path") for value in raw[:-1].split("\x00"))
    if len(set(paths)) != len(paths):
        raise RepoCommitRunnerError("staged path output contains duplicates")
    return paths


def _parse_commit_sha(output: str) -> str:
    raw = str(output or "")
    if raw.endswith("\r\n"):
        candidate = raw[:-2]
    elif raw.endswith("\n"):
        candidate = raw[:-1]
    else:
        candidate = raw
    return candidate if _COMMIT_SHA_RE.fullmatch(candidate) else ""


def _normalize_choice(value: Any, *, field_name: str, choices: tuple[str, ...]) -> str:
    text = _normalize_text(value, field_name=field_name, max_len=80).lower().replace("-", "_")
    if text not in choices:
        raise RepoCommitRunnerError(f"unsupported {field_name}: {value!r}")
    return text


def _status_path(value: str) -> str:
    path = str(value or "").strip()
    if " -> " in path:
        path = path.rsplit(" -> ", 1)[-1]
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        path = path[1:-1]
    return path


def _path_arg_is_safe(value: str) -> bool:
    try:
        _normalize_repo_path(value, field_name="changed_path")
    except RepoCommitRunnerError:
        return False
    return True


def _default_commit_message(objective: str) -> str:
    normalized = re.sub(r"[^a-z0-9 ._/-]+", "", objective.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip(" ._/-")
    if not normalized:
        normalized = "registered repo update"
    return f"chore: {normalized}"[:120]


def _next_human_decision(decision: str) -> str:
    if decision == "plan_ready":
        return "Commit can run now; push remains a separate gated action."
    if decision == "blocked":
        return "Fix the blocked commit request before trying again."
    return "Provide confirmed=true, exact changed_paths, checks_passed=true, and content_reviewed=true."


def _bounded_redacted(value: str) -> str:
    return _redact_output(str(value or ""))[:MAX_OUTPUT_CHARS]


def _redact_output(value: str) -> str:
    text = str(value or "")
    text = _SECRET_RE.sub("[redacted-secret]", text)
    text = _WINDOWS_PATH_RE.sub("[redacted-path]", text)
    return _ABSOLUTE_PATH_RE.sub("[redacted-path]", text)


def _merge_env(extra: Mapping[str, str]) -> dict[str, str]:
    allowed_keys = ("PATH", "SYSTEMROOT", "COMSPEC", "HOME", "USERPROFILE")
    merged = {key: value for key, value in os.environ.items() if key.upper() in allowed_keys}
    merged.update({str(key): str(value) for key, value in extra.items()})
    merged["GIT_TERMINAL_PROMPT"] = "0"
    return merged
