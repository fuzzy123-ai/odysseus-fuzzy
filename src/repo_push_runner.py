"""Operator-gated push runner for registered repositories."""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.parse import urlsplit

from src.claim_evidence_gate import (
    AgentMaintenanceCompletionEvidence,
    evaluate_agent_maintenance_completion,
)
from src.constants import MAX_OUTPUT_CHARS
from src.repo_registry import RepoRecord, RepoRegistry, RepoRegistryError, redact_remote_url
from src.repo_remote_policy import RepoRemotePolicyDecision, evaluate_remote_branch_policy, normalize_branch_name


_DECISIONS = ("blocked", "hold", "plan_ready")
_MAX_TIMEOUT_SECONDS = 300
_MAX_LIST_ITEMS = 120
_SECRET_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer)\b\s*[:=]\s*\S+")
_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s\t]+")
_ABSOLUTE_PATH_RE = re.compile(r"(?<![A-Za-z0-9._-])/(?:[^\s/]+/)*[^\s]+")
_REMOTE_URL_RE = re.compile(r"(?i)\b(?:file|https?|ssh)://[^\s]+")
_SAFE_REMOTE_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
_COMMIT_SHA_RE = re.compile(r"^[A-Fa-f0-9]{7,40}$")
_FULL_COMMIT_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class RepoPushRunnerError(ValueError):
    """Raised when a registered repo push request is unsafe."""


class RepoPushCommandRunner(Protocol):
    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        timeout_seconds: int,
        env: Mapping[str, str],
    ) -> "RepoPushCommandResult":
        ...


@dataclass(frozen=True, slots=True)
class RepoPushCommandResult:
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
class RepoForgeGitTransportCommands:
    """Fixed argv commands for one exact, non-force Forge delivery."""

    verify_commit: tuple[str, ...]
    remote_url: tuple[str, ...]
    remote_ref: tuple[str, ...]
    push: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RepoPushStatusEntry:
    code: str
    path: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "path": self.path}


@dataclass(frozen=True, slots=True)
class RepoPushPlan:
    repo_id: str
    repo_path_ref: str
    remote_name: str
    branch_name: str
    commit_sha: str
    actual_branch: str
    actual_commit_sha: str
    confirmed: bool
    operator_go: bool
    live_enabled: bool
    completion_verified: bool
    action_authorized: bool
    decision: str
    blockers: tuple[str, ...]
    remote_policy: RepoRemotePolicyDecision
    status_entries: tuple[RepoPushStatusEntry, ...]
    planned_steps: tuple[dict[str, Any], ...]
    next_human_decision: str

    @property
    def can_push(self) -> bool:
        return self.decision == "plan_ready"

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "repo_path_ref": self.repo_path_ref,
            "remote_name": self.remote_name,
            "branch_name": self.branch_name,
            "commit_sha": self.commit_sha,
            "actual_branch": self.actual_branch,
            "actual_commit_sha": self.actual_commit_sha,
            "confirmed": self.confirmed,
            "operator_go": self.operator_go,
            "live_enabled": self.live_enabled,
            "completion_verified": self.completion_verified,
            "action_authorized": self.action_authorized,
            "can_push": self.can_push,
            "decision": self.decision,
            "blockers": list(self.blockers),
            "remote_policy": self.remote_policy.to_dict(),
            "status_entries": [entry.to_dict() for entry in self.status_entries],
            "planned_steps": [dict(step) for step in self.planned_steps],
            "next_human_decision": self.next_human_decision,
        }


@dataclass(frozen=True, slots=True)
class RepoPushReport:
    status: str
    executed: bool
    plan: RepoPushPlan
    command_results: tuple[RepoPushCommandResult, ...]
    pushed_ref: str
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "executed": self.executed,
            "plan": self.plan.to_dict(),
            "command_results": [result.to_dict() for result in self.command_results],
            "pushed_ref": self.pushed_ref,
            "blockers": list(self.blockers),
        }


def build_repo_push_authority(
    *,
    repo_id: Any,
    remote_name: Any,
    branch_name: Any,
    commit_sha: Any,
    granted: bool,
) -> RepoPushAuthority:
    if type(granted) is not bool:
        raise RepoPushRunnerError("granted must be a boolean")
    return RepoPushAuthority(
        action="push",
        repo_id=_normalize_text(repo_id, field_name="repo_id", max_len=120),
        remote_name=_normalize_remote_name(remote_name),
        branch_name=normalize_branch_name(branch_name),
        commit_sha=_normalize_commit_sha(commit_sha, field_name="commit_sha"),
        granted=granted,
    )


def plan_repo_push(
    *,
    registry: RepoRegistry,
    repo_id: Any,
    workspace_base: str | Path,
    remote_name: Any,
    branch_name: Any,
    commit_sha: Any,
    confirmed: bool = False,
    operator_go: bool = False,
    live_enabled: bool | None = None,
    repo_roots: Mapping[str, str | os.PathLike[str]] | None = None,
    command_runner: RepoPushCommandRunner | None = None,
    completion_evidence: AgentMaintenanceCompletionEvidence | None = None,
    push_authority: RepoPushAuthority | None = None,
) -> RepoPushReport:
    return _run_push_flow(
        registry=registry,
        repo_id=repo_id,
        workspace_base=workspace_base,
        remote_name=remote_name,
        branch_name=branch_name,
        commit_sha=commit_sha,
        confirmed=confirmed,
        operator_go=operator_go,
        live_enabled=live_enabled,
        repo_roots=repo_roots,
        command_runner=command_runner,
        execute_push=False,
        completion_evidence=completion_evidence,
        push_authority=push_authority,
    )


def run_repo_push(
    *,
    registry: RepoRegistry,
    repo_id: Any,
    workspace_base: str | Path,
    remote_name: Any,
    branch_name: Any,
    commit_sha: Any,
    confirmed: bool,
    operator_go: bool,
    live_enabled: bool | None = None,
    repo_roots: Mapping[str, str | os.PathLike[str]] | None = None,
    command_runner: RepoPushCommandRunner | None = None,
    completion_evidence: AgentMaintenanceCompletionEvidence | None = None,
    push_authority: RepoPushAuthority | None = None,
) -> RepoPushReport:
    return _run_push_flow(
        registry=registry,
        repo_id=repo_id,
        workspace_base=workspace_base,
        remote_name=remote_name,
        branch_name=branch_name,
        commit_sha=commit_sha,
        confirmed=confirmed,
        operator_go=operator_go,
        live_enabled=live_enabled,
        repo_roots=repo_roots,
        command_runner=command_runner,
        execute_push=True,
        completion_evidence=completion_evidence,
        push_authority=push_authority,
    )


def build_repo_push_plan(
    *,
    record: RepoRecord,
    remote_name: Any,
    branch_name: Any,
    commit_sha: Any,
    actual_branch: Any,
    actual_commit_sha: Any,
    status_output: str,
    confirmed: bool,
    operator_go: bool,
    live_enabled: bool | None = None,
    completion_verified: bool = False,
    action_authorized: bool = False,
) -> RepoPushPlan:
    if not isinstance(record, RepoRecord):
        raise RepoPushRunnerError("record must be a RepoRecord")
    remote = _normalize_remote_name(remote_name)
    branch = normalize_branch_name(branch_name, repo_id=record.repo_id)
    expected_sha = _normalize_commit_sha(commit_sha, field_name="commit_sha")
    current_branch = normalize_branch_name(actual_branch, repo_id=record.repo_id)
    current_sha = _normalize_commit_sha(actual_commit_sha, field_name="actual_commit_sha")
    confirmed_is_bool = type(confirmed) is bool
    operator_go_is_bool = type(operator_go) is bool
    live_is_bool = live_enabled is None or type(live_enabled) is bool
    resolved_live = (
        _bool_env(os.getenv("ODYSSEUS_REPO_PUSH_RUNNER_LIVE_ENABLED"))
        if live_enabled is None
        else live_enabled
        if type(live_enabled) is bool
        else False
    )
    _, entries = parse_repo_push_status(status_output)
    policy = evaluate_remote_branch_policy(
        record=record,
        remote_name=remote,
        branch_name=branch,
        action="push",
    )
    selected_remote = next(
        (item for item in record.remotes if item.name == remote),
        None,
    )

    blockers: list[str] = []
    if policy.decision == "blocked":
        blockers.append(policy.reason)
    elif policy.decision == "hold":
        blockers.append(policy.reason)
    if selected_remote is None or not _transport_url_is_allowed(selected_remote.url_redacted):
        blockers.append("remote transport scheme must be HTTPS or file")
    if not confirmed_is_bool:
        blockers.append("confirmed must be a boolean")
    elif not confirmed:
        blockers.append("confirmed=true is required before pushing a registered repo")
    if not operator_go_is_bool:
        blockers.append("operator_go must be a boolean")
    elif not operator_go:
        blockers.append("operator_go=true is required for live push")
    if not live_is_bool:
        blockers.append("live_enabled must be a boolean or None")
    elif not resolved_live:
        blockers.append("ODYSSEUS_REPO_PUSH_RUNNER_LIVE_ENABLED or live_enabled=true is required for live push")
    if completion_verified is not True:
        blockers.append("current claims and machine verification receipt are required before push")
    if action_authorized is not True:
        blockers.append("typed explicit push authority is required")
    if current_branch != branch:
        blockers.append(f"branch_name `{branch}` does not match current branch `{current_branch}`")
    if current_sha != expected_sha:
        blockers.append("commit_sha does not match current HEAD")
    if entries:
        blockers.append("repository must be clean before push")

    if policy.decision == "blocked":
        decision = "blocked"
    elif blockers:
        decision = "hold"
    else:
        decision = "plan_ready"

    return RepoPushPlan(
        repo_id=record.repo_id,
        repo_path_ref=record.path_ref,
        remote_name=remote,
        branch_name=branch,
        commit_sha=expected_sha,
        actual_branch=current_branch,
        actual_commit_sha=current_sha,
        confirmed=confirmed if confirmed_is_bool else False,
        operator_go=operator_go if operator_go_is_bool else False,
        live_enabled=resolved_live,
        completion_verified=completion_verified is True,
        action_authorized=action_authorized is True,
        decision=_normalize_choice(decision, field_name="decision", choices=_DECISIONS),
        blockers=tuple(dict.fromkeys(blockers)),
        remote_policy=policy,
        status_entries=entries,
        planned_steps=(
            {"step_id": "git_status", "summary": "capture registered repo status before push", "executes": True},
            {"step_id": "git_branch", "summary": "verify current branch matches requested branch", "executes": True},
            {"step_id": "git_rev_parse", "summary": "verify current HEAD matches requested commit_sha", "executes": True},
            {
                "step_id": "git_push",
                "summary": f"push exact {expected_sha}:refs/heads/{branch} to {remote}",
                "executes": True,
            },
        ),
        next_human_decision=_next_human_decision(decision, policy),
    )


def parse_repo_push_status(output: str) -> tuple[str, tuple[RepoPushStatusEntry, ...]]:
    lines = [line for line in str(output or "").splitlines() if line.strip()]
    branch_line = lines[0] if lines and lines[0].startswith("##") else ""
    entry_lines = lines[1:] if branch_line else lines
    entries: list[RepoPushStatusEntry] = []
    for raw_line in entry_lines[:_MAX_LIST_ITEMS]:
        if len(raw_line) < 4:
            continue
        code = raw_line[:2]
        path = raw_line[3:].strip()
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1]
        if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
            path = path[1:-1]
        if path:
            entries.append(RepoPushStatusEntry(code=_bounded_redacted(code), path=_bounded_redacted(path)))
    return branch_line, tuple(entries)


def run_git_push_subprocess_command(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    timeout_seconds: int,
    env: Mapping[str, str],
) -> RepoPushCommandResult:
    if not repo_push_command_is_allowed(argv):
        raise RepoPushRunnerError("unsupported repo push command")
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
        return RepoPushCommandResult(
            exit_code=124,
            stdout=str(exc.stdout or ""),
            stderr=str(exc.stderr or "command timed out"),
            timed_out=True,
            duration_seconds=round(time.monotonic() - started, 3),
        )
    return RepoPushCommandResult(
        exit_code=int(completed.returncode),
        stdout=str(completed.stdout or ""),
        stderr=str(completed.stderr or ""),
        timed_out=False,
        duration_seconds=round(time.monotonic() - started, 3),
    )


def repo_push_command_is_allowed(argv: tuple[str, ...]) -> bool:
    if argv == ("git", "status", "--short", "--branch"):
        return True
    if argv == ("git", "branch", "--show-current"):
        return True
    if argv == ("git", "rev-parse", "HEAD"):
        return True
    if len(argv) == 4 and argv[:3] == ("git", "rev-parse", "--verify"):
        revision = argv[3]
        suffix = "^{commit}"
        return revision.endswith(suffix) and bool(_FULL_COMMIT_SHA_RE.fullmatch(revision[: -len(suffix)]))
    if len(argv) == 6 and argv[:5] == ("git", "remote", "get-url", "--push", "--all"):
        _normalize_remote_name(argv[5])
        return True
    if len(argv) == 5 and argv[:3] == ("git", "ls-remote", "--heads"):
        _normalize_remote_name(argv[3])
        _branch_from_remote_ref(argv[4])
        return True
    if len(argv) == 4 and argv[:2] == ("git", "push"):
        _normalize_remote_name(argv[2])
        if ":" in argv[3]:
            source, destination = argv[3].split(":", 1)
            if not _FULL_COMMIT_SHA_RE.fullmatch(source):
                return False
            _branch_from_remote_ref(destination)
        else:
            if argv[3].startswith(("+", "refs/")):
                return False
            branch = normalize_branch_name(argv[3])
        return True
    if len(argv) == 5 and argv[:3] == ("git", "merge-base", "--is-ancestor"):
        return bool(
            _FULL_COMMIT_SHA_RE.fullmatch(argv[3])
            and _FULL_COMMIT_SHA_RE.fullmatch(argv[4])
        )
    return False


def build_repo_forge_git_transport_commands(
    *,
    remote_name: Any,
    branch_name: Any,
    commit_sha: Any,
) -> RepoForgeGitTransportCommands:
    """Build the only Git transport argv accepted by the GitHub adapter."""

    remote = _normalize_remote_name(remote_name)
    branch = normalize_branch_name(branch_name)
    sha = str(commit_sha or "").strip().lower()
    if not _FULL_COMMIT_SHA_RE.fullmatch(sha):
        raise RepoPushRunnerError("commit_sha must be a full Git object id")
    remote_ref = f"refs/heads/{branch}"
    commands = RepoForgeGitTransportCommands(
        verify_commit=("git", "rev-parse", "--verify", f"{sha}^{{commit}}"),
        remote_url=("git", "remote", "get-url", "--push", "--all", remote),
        remote_ref=("git", "ls-remote", "--heads", remote, remote_ref),
        push=("git", "push", remote, f"{sha}:{remote_ref}"),
    )
    for argv in (commands.verify_commit, commands.remote_url, commands.remote_ref, commands.push):
        if not repo_push_command_is_allowed(argv):
            raise RepoPushRunnerError("generated Forge Git command is not allowed")
    return commands


def build_repo_forge_ancestry_command(
    *,
    ancestor_sha: Any,
    descendant_sha: Any,
) -> tuple[str, ...]:
    """Build the fixed local-only fast-forward ancestry check."""

    ancestor = str(ancestor_sha or "").strip().lower()
    descendant = str(descendant_sha or "").strip().lower()
    argv = ("git", "merge-base", "--is-ancestor", ancestor, descendant)
    if not repo_push_command_is_allowed(argv):
        raise RepoPushRunnerError("Forge ancestry command is not allowed")
    return argv


def parse_repo_remote_head_sha(output: Any, *, branch_name: Any) -> str | None:
    """Parse one exact ``git ls-remote --heads`` result without provider text."""

    branch = normalize_branch_name(branch_name)
    expected_ref = f"refs/heads/{branch}"
    lines = [line.strip() for line in str(output or "").splitlines() if line.strip()]
    if not lines:
        return None
    if len(lines) != 1:
        raise RepoPushRunnerError("remote ref check returned an ambiguous result")
    parts = lines[0].split()
    if len(parts) != 2 or parts[1] != expected_ref:
        raise RepoPushRunnerError("remote ref check returned an unexpected ref")
    sha = parts[0].casefold()
    if not _FULL_COMMIT_SHA_RE.fullmatch(sha):
        raise RepoPushRunnerError("remote ref check returned an invalid object id")
    return sha


def _run_push_flow(
    *,
    registry: RepoRegistry,
    repo_id: Any,
    workspace_base: str | Path,
    remote_name: Any,
    branch_name: Any,
    commit_sha: Any,
    confirmed: bool,
    operator_go: bool,
    live_enabled: bool | None,
    repo_roots: Mapping[str, str | os.PathLike[str]] | None,
    command_runner: RepoPushCommandRunner | None,
    execute_push: bool,
    completion_evidence: AgentMaintenanceCompletionEvidence | None,
    push_authority: RepoPushAuthority | None,
) -> RepoPushReport:
    record, repo_path = _resolve_repo(
        registry=registry,
        repo_id=repo_id,
        workspace_base=workspace_base,
        repo_roots=repo_roots,
    )
    runner = command_runner or run_git_push_subprocess_command
    completion_verified = evaluate_agent_maintenance_completion(
        completion_evidence,
        repo_root=repo_path,
    ).completed
    action_authorized = _push_authority_matches(
        push_authority,
        repo_id=record.repo_id,
        remote_name=remote_name,
        branch_name=branch_name,
        commit_sha=commit_sha,
    )
    read_commands = (
        ("git", "status", "--short", "--branch"),
        ("git", "branch", "--show-current"),
        ("git", "rev-parse", "HEAD"),
    )
    results: list[RepoPushCommandResult] = []
    for command in read_commands:
        result = runner(command, cwd=repo_path, timeout_seconds=_MAX_TIMEOUT_SECONDS, env={})
        results.append(result)
        if not result.ok:
            plan = _blocked_plan(
                record=record,
                remote_name=remote_name,
                branch_name=branch_name,
                commit_sha=commit_sha,
                confirmed=confirmed,
                operator_go=operator_go,
                live_enabled=live_enabled,
                reason=f"push preflight command failed: {' '.join(command[:2])}",
                completion_verified=completion_verified,
                action_authorized=action_authorized,
            )
            return RepoPushReport(
                status="blocked",
                executed=False,
                plan=plan,
                command_results=tuple(results),
                pushed_ref="",
                blockers=plan.blockers,
            )

    plan = build_repo_push_plan(
        record=record,
        remote_name=remote_name,
        branch_name=branch_name,
        commit_sha=commit_sha,
        actual_branch=results[1].stdout.strip(),
        actual_commit_sha=results[2].stdout.strip(),
        status_output=results[0].stdout,
        confirmed=confirmed,
        operator_go=operator_go,
        live_enabled=live_enabled,
        completion_verified=completion_verified,
        action_authorized=action_authorized,
    )
    if not execute_push:
        return RepoPushReport(
            status=plan.decision,
            executed=False,
            plan=plan,
            command_results=tuple(results),
            pushed_ref="",
            blockers=plan.blockers,
        )
    if not plan.can_push:
        return RepoPushReport(
            status="blocked",
            executed=False,
            plan=plan,
            command_results=tuple(results),
            pushed_ref="",
            blockers=plan.blockers,
        )

    completion_verified = evaluate_agent_maintenance_completion(
        completion_evidence,
        repo_root=repo_path,
    ).completed
    action_authorized = _push_authority_matches(
        push_authority,
        repo_id=record.repo_id,
        remote_name=plan.remote_name,
        branch_name=plan.branch_name,
        commit_sha=plan.commit_sha,
    )
    if not completion_verified or not action_authorized:
        return RepoPushReport(
            status="blocked",
            executed=False,
            plan=plan,
            command_results=tuple(results),
            pushed_ref="",
            blockers=("current completion evidence or push authority changed",),
        )

    expected_remote_url = next(
        (
            remote.url_redacted
            for remote in record.remotes
            if remote.name == plan.remote_name
        ),
        "",
    )
    commands = build_repo_forge_git_transport_commands(
        remote_name=plan.remote_name,
        branch_name=plan.branch_name,
        commit_sha=plan.commit_sha,
        push_target_url=expected_remote_url,
    )
    verify_result = runner(
        commands.verify_commit,
        cwd=repo_path,
        timeout_seconds=_MAX_TIMEOUT_SECONDS,
        env={},
    )
    results.append(verify_result)
    if not verify_result.ok or _parse_exact_full_sha(verify_result.stdout) != plan.commit_sha:
        return RepoPushReport(
            status="blocked",
            executed=False,
            plan=plan,
            command_results=tuple(results),
            pushed_ref="",
            blockers=("push source full object id could not be verified",),
        )

    sanitized_remote_result, remote_url_matches = _read_sanitized_remote_url(
        runner,
        command=commands.remote_url,
        repo_path=repo_path,
        expected_remote_url=expected_remote_url,
    )
    results.append(sanitized_remote_result)
    if not remote_url_matches:
        return RepoPushReport(
            status="blocked",
            executed=False,
            plan=plan,
            command_results=tuple(results),
            pushed_ref="",
            blockers=("push remote URL could not be verified",),
        )

    rewrite_result, rewrites_clear = _read_url_rewrite_gate(
        runner,
        command=commands.url_rewrites,
        repo_path=repo_path,
    )
    results.append(rewrite_result)
    if not rewrites_clear:
        return RepoPushReport(
            status="blocked",
            executed=False,
            plan=plan,
            command_results=tuple(results),
            pushed_ref="",
            blockers=("Git URL rewrite configuration is present or unverifiable",),
        )

    remote_before_result = runner(
        commands.remote_ref,
        cwd=repo_path,
        timeout_seconds=_MAX_TIMEOUT_SECONDS,
        env={},
    )
    results.append(remote_before_result)
    try:
        remote_before = (
            parse_repo_remote_head_sha(
                remote_before_result.stdout,
                branch_name=plan.branch_name,
            )
            if remote_before_result.ok
            else None
        )
    except RepoPushRunnerError:
        remote_before = None
        remote_before_valid = False
    else:
        remote_before_valid = remote_before_result.ok
    if not remote_before_valid:
        return RepoPushReport(
            status="blocked",
            executed=False,
            plan=plan,
            command_results=tuple(results),
            pushed_ref="",
            blockers=("remote branch preflight is unavailable or ambiguous",),
        )

    if remote_before and remote_before != plan.commit_sha:
        ancestry_command = build_repo_forge_ancestry_command(
            ancestor_sha=remote_before,
            descendant_sha=plan.commit_sha,
        )
        ancestry_result = runner(
            ancestry_command,
            cwd=repo_path,
            timeout_seconds=_MAX_TIMEOUT_SECONDS,
            env={},
        )
        results.append(ancestry_result)
        if not ancestry_result.ok:
            return RepoPushReport(
                status="blocked",
                executed=False,
                plan=plan,
                command_results=tuple(results),
                pushed_ref="",
                blockers=("remote history is not an ancestor of the authorized commit",),
            )

    final_status_result = runner(
        ("git", "status", "--short", "--branch"),
        cwd=repo_path,
        timeout_seconds=_MAX_TIMEOUT_SECONDS,
        env={},
    )
    results.append(final_status_result)
    try:
        _, final_status_entries = (
            parse_repo_push_status(final_status_result.stdout)
            if final_status_result.ok
            else ("", ())
        )
    except RepoPushRunnerError:
        final_status_entries = ()
        final_status_valid = False
    else:
        final_status_valid = final_status_result.ok and not final_status_entries
    if not final_status_valid:
        return RepoPushReport(
            status="blocked",
            executed=False,
            plan=plan,
            command_results=tuple(results),
            pushed_ref="",
            blockers=("repository changed or became dirty before push",),
        )

    completion_verified = evaluate_agent_maintenance_completion(
        completion_evidence,
        repo_root=repo_path,
    ).completed
    action_authorized = _push_authority_matches(
        push_authority,
        repo_id=record.repo_id,
        remote_name=plan.remote_name,
        branch_name=plan.branch_name,
        commit_sha=plan.commit_sha,
    )
    if not completion_verified or not action_authorized:
        return RepoPushReport(
            status="blocked",
            executed=False,
            plan=plan,
            command_results=tuple(results),
            pushed_ref="",
            blockers=("current completion evidence or push authority changed before push",),
        )

    # Git has no immutable remote-handle primitive. This exact sanitized
    # readback is therefore the final trust-boundary check immediately before
    # the fixed push argv; a remote/config swap after the earlier preflight is
    # detected without persisting the raw (potentially credentialed) URL.
    final_remote_result, final_remote_matches = _read_sanitized_remote_url(
        runner,
        command=commands.remote_url,
        repo_path=repo_path,
        expected_remote_url=expected_remote_url,
    )
    results.append(final_remote_result)
    if not final_remote_matches:
        return RepoPushReport(
            status="blocked",
            executed=False,
            plan=plan,
            command_results=tuple(results),
            pushed_ref="",
            blockers=("push remote URL changed before push",),
        )

    final_rewrite_result, final_rewrites_clear = _read_url_rewrite_gate(
        runner,
        command=commands.url_rewrites,
        repo_path=repo_path,
    )
    results.append(final_rewrite_result)
    if not final_rewrites_clear:
        return RepoPushReport(
            status="blocked",
            executed=False,
            plan=plan,
            command_results=tuple(results),
            pushed_ref="",
            blockers=("Git URL rewrite configuration changed before push",),
        )

    push_result = runner(
        commands.push,
        cwd=repo_path,
        timeout_seconds=_MAX_TIMEOUT_SECONDS,
        env={},
    )
    results.append(push_result)
    if not push_result.ok:
        return RepoPushReport(
            status="failed",
            executed=True,
            plan=plan,
            command_results=tuple(results),
            pushed_ref="",
            blockers=("push command failed: git push",),
        )
    remote_after_result = runner(
        commands.remote_ref,
        cwd=repo_path,
        timeout_seconds=_MAX_TIMEOUT_SECONDS,
        env={},
    )
    results.append(remote_after_result)
    try:
        remote_after = (
            parse_repo_remote_head_sha(
                remote_after_result.stdout,
                branch_name=plan.branch_name,
            )
            if remote_after_result.ok
            else None
        )
    except RepoPushRunnerError:
        remote_after = None
    if remote_after != plan.commit_sha:
        return RepoPushReport(
            status="reconcile_required",
            executed=True,
            plan=plan,
            command_results=tuple(results),
            pushed_ref="",
            blockers=("push returned but exact remote full object id was not verified",),
        )
    return RepoPushReport(
        status="pushed",
        executed=True,
        plan=plan,
        command_results=tuple(results),
        pushed_ref=f"{plan.remote_name}/{plan.branch_name}@{plan.commit_sha}",
        blockers=(),
    )


def _blocked_plan(
    *,
    record: RepoRecord,
    remote_name: Any,
    branch_name: Any,
    commit_sha: Any,
    confirmed: bool,
    operator_go: bool,
    live_enabled: bool | None,
    reason: str,
    completion_verified: bool = False,
    action_authorized: bool = False,
) -> RepoPushPlan:
    policy = evaluate_remote_branch_policy(
        record=record,
        remote_name=remote_name,
        branch_name=branch_name,
        action="push",
    )
    resolved_live = (
        _bool_env(os.getenv("ODYSSEUS_REPO_PUSH_RUNNER_LIVE_ENABLED"))
        if live_enabled is None
        else live_enabled
        if type(live_enabled) is bool
        else False
    )
    return RepoPushPlan(
        repo_id=record.repo_id,
        repo_path_ref=record.path_ref,
        remote_name=_normalize_remote_name(remote_name),
        branch_name=normalize_branch_name(branch_name, repo_id=record.repo_id),
        commit_sha=_normalize_commit_sha(commit_sha, field_name="commit_sha"),
        actual_branch="",
        actual_commit_sha="",
        confirmed=confirmed if type(confirmed) is bool else False,
        operator_go=operator_go if type(operator_go) is bool else False,
        live_enabled=resolved_live,
        completion_verified=completion_verified is True,
        action_authorized=action_authorized is True,
        decision="blocked",
        blockers=(reason,),
        remote_policy=policy,
        status_entries=(),
        planned_steps=({"step_id": "git_status", "summary": "capture registered repo status before push", "executes": True},),
        next_human_decision="Fix push preflight before trying again.",
    )


def _read_sanitized_remote_url(
    runner: RepoPushCommandRunner,
    *,
    command: tuple[str, ...],
    repo_path: Path,
    expected_remote_url: str,
) -> tuple[RepoPushCommandResult, bool]:
    raw_result = runner(
        command,
        cwd=repo_path,
        timeout_seconds=_MAX_TIMEOUT_SECONDS,
        env={},
    )
    matches = False
    if raw_result.ok:
        lines = [line.strip() for line in raw_result.stdout.splitlines() if line.strip()]
        if len(lines) == 1:
            try:
                matches = redact_remote_url(lines[0]) == expected_remote_url
            except (TypeError, ValueError):
                matches = False
    return (
        RepoPushCommandResult(
            exit_code=raw_result.exit_code,
            stdout="remote_readback_verified" if matches else "",
            stderr="",
            timed_out=raw_result.timed_out,
            duration_seconds=raw_result.duration_seconds,
        ),
        matches,
    )


def _read_url_rewrite_gate(
    runner: RepoPushCommandRunner,
    *,
    command: tuple[str, ...],
    repo_path: Path,
) -> tuple[RepoPushCommandResult, bool]:
    raw_result = runner(
        command,
        cwd=repo_path,
        timeout_seconds=_MAX_TIMEOUT_SECONDS,
        env={},
    )
    clear = (
        raw_result.exit_code == 1
        and not raw_result.timed_out
        and raw_result.stdout == ""
        and raw_result.stderr == ""
    )
    return (
        RepoPushCommandResult(
            exit_code=0 if clear else 1,
            stdout="",
            stderr="",
            timed_out=raw_result.timed_out,
            duration_seconds=raw_result.duration_seconds,
        ),
        clear,
    )


def _resolve_repo(
    *,
    registry: RepoRegistry,
    repo_id: Any,
    workspace_base: str | Path,
    repo_roots: Mapping[str, str | os.PathLike[str]] | None = None,
) -> tuple[RepoRecord, Path]:
    if not isinstance(registry, RepoRegistry):
        raise RepoPushRunnerError("registry must be a RepoRegistry")
    try:
        record = registry.get(repo_id)
    except RepoRegistryError as exc:
        raise RepoPushRunnerError(str(exc)) from exc
    base = Path(workspace_base).resolve()
    roots = {str(key): Path(value).resolve() for key, value in (repo_roots or {}).items()}
    repo_path = roots.get(record.repo_id, (base / record.path_ref).resolve())
    _assert_child_path(base, repo_path)
    if not repo_path.is_dir() or not (repo_path / ".git").exists():
        raise RepoPushRunnerError("registered repo path is not a local Git repository")
    return record, repo_path


def _assert_child_path(parent: Path, child: Path) -> None:
    parent_resolved = parent.resolve()
    child_resolved = child.resolve()
    try:
        child_resolved.relative_to(parent_resolved)
    except ValueError as exc:
        raise RepoPushRunnerError("push path escapes registered repository") from exc


def _normalize_remote_name(value: Any) -> str:
    remote = _normalize_text(value, field_name="remote_name", max_len=80)
    if not _SAFE_REMOTE_RE.fullmatch(remote) or remote.startswith("-"):
        raise RepoPushRunnerError("remote_name contains unsupported characters")
    return remote


def _branch_from_remote_ref(value: Any) -> str:
    remote_ref = str(value or "")
    prefix = "refs/heads/"
    if not remote_ref.startswith(prefix):
        raise RepoPushRunnerError("remote ref must target refs/heads")
    return normalize_branch_name(remote_ref[len(prefix) :])


def _normalize_commit_sha(value: Any, *, field_name: str) -> str:
    text = _normalize_text(value, field_name=field_name, max_len=64).lower()
    if not _FULL_COMMIT_SHA_RE.fullmatch(text):
        raise RepoPushRunnerError(f"{field_name} must be a full Git object id")
    return text.lower()


def _normalize_text(value: Any, *, field_name: str, max_len: int = 220) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        raise RepoPushRunnerError(f"{field_name} must not be empty")
    if len(text) > max_len:
        raise RepoPushRunnerError(f"{field_name} exceeds max length {max_len}")
    if _SECRET_RE.search(text):
        raise RepoPushRunnerError(f"{field_name} appears to contain secret material")
    return text


def _normalize_choice(value: Any, *, field_name: str, choices: tuple[str, ...]) -> str:
    text = _normalize_text(value, field_name=field_name, max_len=80).lower().replace("-", "_")
    if text not in choices:
        raise RepoPushRunnerError(f"unsupported {field_name}: {value!r}")
    return text


def _bool_env(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _next_human_decision(decision: str, policy: RepoRemotePolicyDecision) -> str:
    if decision == "plan_ready":
        return "Push can run now; deploy, release, and exposure remain separate gates."
    if policy.decision == "blocked":
        return f"Update repo remote policy or choose a push_allowed remote. {policy.next_safe_action}"
    if policy.decision == "hold":
        return policy.next_safe_action
    return "Provide confirmed=true, operator_go=true, live_enabled=true, matching branch_name, and matching commit_sha."


def _bounded_redacted(value: str) -> str:
    return _redact_output(str(value or ""))[:MAX_OUTPUT_CHARS]


def _redact_output(value: str) -> str:
    text = str(value or "")
    text = _SECRET_RE.sub("[redacted-secret]", text)
    text = _REMOTE_URL_RE.sub("[redacted-remote]", text)
    text = _WINDOWS_PATH_RE.sub("[redacted-path]", text)
    return _ABSOLUTE_PATH_RE.sub("[redacted-path]", text)


def _merge_env(extra: Mapping[str, str]) -> dict[str, str]:
    allowed_keys = ("PATH", "SYSTEMROOT", "COMSPEC", "HOME", "USERPROFILE")
    merged = {key: value for key, value in os.environ.items() if key.upper() in allowed_keys}
    merged.update({str(key): str(value) for key, value in extra.items()})
    merged["GIT_TERMINAL_PROMPT"] = "0"
    return merged


def _transport_url_is_allowed(value: str) -> bool:
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return False
    if parsed.scheme.lower() == "https":
        return bool(parsed.hostname)
    if parsed.scheme.lower() == "file":
        return not parsed.username and not parsed.password and bool(parsed.path)
    return False


def _normalize_transport_target(value: Any) -> str:
    raw = str(value or "").strip()
    try:
        redacted = redact_remote_url(raw)
    except (RepoRegistryError, TypeError, ValueError) as exc:
        raise RepoPushRunnerError("push target URL is invalid") from exc
    if raw != redacted or not _transport_url_is_allowed(redacted):
        raise RepoPushRunnerError("push target URL must be credential-free HTTPS or file")
    return redacted
