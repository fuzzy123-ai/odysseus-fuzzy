"""Read-only Git adapter for repos registered in ``src.repo_registry``."""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from src.constants import MAX_OUTPUT_CHARS
from src.repo_registry import RepoRecord, RepoRegistry, RepoRegistryError, redact_remote_url


_MAX_TIMEOUT_SECONDS = 8
_MAX_LOG_LIMIT = 100
_MAX_LIST_ITEMS = 200
_LOG_PRETTY = "format:%H%x09%ad%x09%an%x09%s"
_SECRET_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer)\b\s*[:=]\s*\S+")
_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s\t]+")
_ABSOLUTE_PATH_RE = re.compile(r"(?<![A-Za-z0-9._-])/(?:[^\s/]+/)*[^\s]+")


class RepoGitAdapterError(ValueError):
    """Raised when read-only repo Git access is unsafe or fails."""


@dataclass(frozen=True, slots=True)
class RepoGitCommandResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    duration_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "ok": self.ok,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "duration_seconds": self.duration_seconds,
        }


@dataclass(frozen=True, slots=True)
class RepoGitCommit:
    commit: str
    authored_at: str
    author: str
    subject: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "commit": self.commit,
            "authored_at": self.authored_at,
            "author": self.author,
            "subject": self.subject,
        }


@dataclass(frozen=True, slots=True)
class RepoGitChangedPath:
    status: str
    path: str

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "path": self.path}


@dataclass(frozen=True, slots=True)
class RepoGitRemote:
    name: str
    url_redacted: str
    direction: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url_redacted": self.url_redacted,
            "direction": self.direction,
        }


@dataclass(frozen=True, slots=True)
class RepoGitStatus:
    repo_id: str
    branch_line: str
    entries: tuple[str, ...]

    @property
    def dirty(self) -> bool:
        return bool(self.entries)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "branch_line": self.branch_line,
            "dirty": self.dirty,
            "entries": list(self.entries),
        }


@dataclass(frozen=True, slots=True)
class RepoGitSnapshot:
    repo_id: str
    current_branch: str
    status: RepoGitStatus
    commits: tuple[RepoGitCommit, ...]
    changed_paths: tuple[RepoGitChangedPath, ...]
    diff_stat: str
    remotes: tuple[RepoGitRemote, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "current_branch": self.current_branch,
            "status": self.status.to_dict(),
            "commits": [commit.to_dict() for commit in self.commits],
            "changed_paths": [item.to_dict() for item in self.changed_paths],
            "diff_stat": self.diff_stat,
            "remotes": [remote.to_dict() for remote in self.remotes],
        }


RepoGitCommandRunner = Callable[
    [tuple[str, ...]],
    RepoGitCommandResult,
]


class RepoGitAdapter:
    """Read Git facts for repo records without exposing a free shell surface."""

    def __init__(
        self,
        *,
        registry: RepoRegistry,
        repo_roots: Mapping[str, str | os.PathLike[str]] | None = None,
        workspace_base: str | os.PathLike[str] | None = None,
        command_runner: Callable[..., RepoGitCommandResult] | None = None,
        timeout_seconds: int = _MAX_TIMEOUT_SECONDS,
    ) -> None:
        if not isinstance(registry, RepoRegistry):
            raise RepoGitAdapterError("registry must be a RepoRegistry")
        self.registry = registry
        self.repo_roots = {str(key): Path(value).resolve() for key, value in (repo_roots or {}).items()}
        self.workspace_base = Path(workspace_base).resolve() if workspace_base is not None else None
        self.command_runner = command_runner or run_git_read_subprocess_command
        self.timeout_seconds = max(1, min(int(timeout_seconds), _MAX_TIMEOUT_SECONDS))

    def status(self, repo_id: Any) -> RepoGitStatus:
        record, root = self._resolve_repo(repo_id)
        result = self._run(("git", "status", "--short", "--branch"), cwd=root)
        self._require_ok(result, "status")
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        branch_line = _redact_output(lines[0]) if lines else ""
        entries = tuple(_redact_output(line) for line in lines[1:_MAX_LIST_ITEMS])
        return RepoGitStatus(repo_id=record.repo_id, branch_line=branch_line, entries=entries)

    def current_branch(self, repo_id: Any) -> str:
        _, root = self._resolve_repo(repo_id)
        result = self._run(("git", "branch", "--show-current"), cwd=root)
        self._require_ok(result, "branch")
        return _redact_output(result.stdout.strip())

    def log(self, repo_id: Any, *, limit: int = 10) -> tuple[RepoGitCommit, ...]:
        _, root = self._resolve_repo(repo_id)
        count = _normalize_limit(limit)
        result = self._run(
            ("git", "log", "--max-count", str(count), "--date=iso", f"--pretty={_LOG_PRETTY}"),
            cwd=root,
        )
        self._require_ok(result, "log")
        return _parse_log(result.stdout)

    def changed_paths(self, repo_id: Any) -> tuple[RepoGitChangedPath, ...]:
        _, root = self._resolve_repo(repo_id)
        result = self._run(("git", "diff", "--name-status"), cwd=root)
        self._require_ok(result, "changed paths")
        return _parse_changed_paths(result.stdout)

    def diff_stat(self, repo_id: Any) -> str:
        _, root = self._resolve_repo(repo_id)
        result = self._run(("git", "diff", "--stat"), cwd=root)
        self._require_ok(result, "diff stat")
        return result.stdout

    def remotes(self, repo_id: Any) -> tuple[RepoGitRemote, ...]:
        _, root = self._resolve_repo(repo_id)
        result = self._run(("git", "remote", "-v"), cwd=root, redact_stdout=False)
        self._require_ok(result, "remotes")
        return _parse_remotes(result.stdout)

    def snapshot(self, repo_id: Any, *, log_limit: int = 10) -> RepoGitSnapshot:
        status = self.status(repo_id)
        return RepoGitSnapshot(
            repo_id=status.repo_id,
            current_branch=self.current_branch(repo_id),
            status=status,
            commits=self.log(repo_id, limit=log_limit),
            changed_paths=self.changed_paths(repo_id),
            diff_stat=self.diff_stat(repo_id),
            remotes=self.remotes(repo_id),
        )

    def _resolve_repo(self, repo_id: Any) -> tuple[RepoRecord, Path]:
        try:
            record = self.registry.get(repo_id)
        except RepoRegistryError as exc:
            raise RepoGitAdapterError(str(exc)) from exc
        root = self.repo_roots.get(record.repo_id)
        if root is None:
            if self.workspace_base is None:
                raise RepoGitAdapterError("workspace_base or repo_roots is required")
            root = (self.workspace_base / record.path_ref).resolve()
        if self.workspace_base is not None:
            _assert_child_path(self.workspace_base, root)
        if not root.is_dir() or not (root / ".git").exists():
            raise RepoGitAdapterError("registered repo path is not a local Git repository")
        return record, root

    def _run(self, argv: tuple[str, ...], *, cwd: Path, redact_stdout: bool = True) -> RepoGitCommandResult:
        if not git_read_command_is_allowed(argv):
            raise RepoGitAdapterError("unsupported read-only Git command")
        result = self.command_runner(argv, cwd=cwd, timeout_seconds=self.timeout_seconds, env={})
        if not isinstance(result, RepoGitCommandResult):
            raise RepoGitAdapterError("command_runner must return RepoGitCommandResult")
        return RepoGitCommandResult(
            exit_code=result.exit_code,
            stdout=_bounded_redacted(result.stdout) if redact_stdout else str(result.stdout or "")[:MAX_OUTPUT_CHARS],
            stderr=_bounded_redacted(result.stderr),
            timed_out=result.timed_out,
            duration_seconds=result.duration_seconds,
        )

    @staticmethod
    def _require_ok(result: RepoGitCommandResult, label: str) -> None:
        if not result.ok:
            reason = _bounded_redacted(result.stderr.strip() or result.stdout.strip() or "unknown error")
            raise RepoGitAdapterError(f"git {label} failed: {reason}")


def run_git_read_subprocess_command(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    timeout_seconds: int,
    env: Mapping[str, str],
) -> RepoGitCommandResult:
    if not git_read_command_is_allowed(argv):
        raise RepoGitAdapterError("unsupported read-only Git command")
    started = time.monotonic()
    merged_env = _merge_env(env)
    try:
        completed = subprocess.run(
            list(argv),
            cwd=str(cwd),
            env=merged_env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return RepoGitCommandResult(
            exit_code=124,
            stdout=str(exc.stdout or ""),
            stderr=str(exc.stderr or "command timed out"),
            timed_out=True,
            duration_seconds=round(time.monotonic() - started, 3),
        )
    return RepoGitCommandResult(
        exit_code=int(completed.returncode),
        stdout=str(completed.stdout or ""),
        stderr=str(completed.stderr or ""),
        timed_out=False,
        duration_seconds=round(time.monotonic() - started, 3),
    )


def git_read_command_is_allowed(argv: tuple[str, ...]) -> bool:
    if argv == ("git", "status", "--short", "--branch"):
        return True
    if argv == ("git", "branch", "--show-current"):
        return True
    if len(argv) == 6 and argv[:2] == ("git", "log"):
        return (
            argv[2] == "--max-count"
            and argv[3].isdigit()
            and 1 <= int(argv[3]) <= _MAX_LOG_LIMIT
            and argv[4] == "--date=iso"
            and argv[5] == f"--pretty={_LOG_PRETTY}"
        )
    if argv == ("git", "diff", "--name-status"):
        return True
    if argv == ("git", "diff", "--stat"):
        return True
    if argv == ("git", "remote", "-v"):
        return True
    return False


def _parse_log(output: str) -> tuple[RepoGitCommit, ...]:
    commits: list[RepoGitCommit] = []
    for line in output.splitlines()[:_MAX_LIST_ITEMS]:
        parts = line.split("\t", 3)
        if len(parts) != 4:
            continue
        commit, authored_at, author, subject = parts
        commits.append(
            RepoGitCommit(
                commit=_redact_output(commit[:40]),
                authored_at=_redact_output(authored_at),
                author=_redact_output(author),
                subject=_redact_output(subject),
            )
        )
    return tuple(commits)


def _parse_changed_paths(output: str) -> tuple[RepoGitChangedPath, ...]:
    rows: list[RepoGitChangedPath] = []
    for line in output.splitlines()[:_MAX_LIST_ITEMS]:
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = _redact_output(parts[0].strip())
        path = _redact_output(parts[-1].strip())
        if path:
            rows.append(RepoGitChangedPath(status=status, path=path))
    return tuple(rows)


def _parse_remotes(output: str) -> tuple[RepoGitRemote, ...]:
    rows: list[RepoGitRemote] = []
    seen: set[tuple[str, str, str]] = set()
    for line in output.splitlines()[:_MAX_LIST_ITEMS]:
        parts = line.split()
        if len(parts) < 3:
            continue
        name, raw_url, raw_direction = parts[0], parts[1], parts[2]
        direction = raw_direction.strip("()")
        remote = RepoGitRemote(
            name=_redact_output(name),
            url_redacted=redact_remote_url(raw_url),
            direction=_redact_output(direction),
        )
        key = (remote.name, remote.url_redacted, remote.direction)
        if key not in seen:
            seen.add(key)
            rows.append(remote)
    return tuple(rows)


def _normalize_limit(value: int) -> int:
    try:
        limit = int(value)
    except Exception as exc:
        raise RepoGitAdapterError("limit must be an integer") from exc
    if limit < 1 or limit > _MAX_LOG_LIMIT:
        raise RepoGitAdapterError(f"limit must be between 1 and {_MAX_LOG_LIMIT}")
    return limit


def _bounded_redacted(value: str) -> str:
    return _redact_output(str(value or ""))[:MAX_OUTPUT_CHARS]


def _redact_output(value: str) -> str:
    text = str(value or "")
    text = _SECRET_RE.sub("[redacted-secret]", text)
    text = _WINDOWS_PATH_RE.sub("[redacted-path]", text)
    return _ABSOLUTE_PATH_RE.sub("[redacted-path]", text)


def _assert_child_path(parent: Path, child: Path) -> None:
    parent_resolved = parent.resolve()
    child_resolved = child.resolve()
    try:
        child_resolved.relative_to(parent_resolved)
    except ValueError as exc:
        raise RepoGitAdapterError("registered repo path is outside the allowed workspace") from exc


def _merge_env(extra: Mapping[str, str]) -> dict[str, str]:
    allowed_keys = ("PATH", "SYSTEMROOT", "COMSPEC", "HOME", "USERPROFILE")
    merged = {key: value for key, value in os.environ.items() if key.upper() in allowed_keys}
    merged.update({str(key): str(value) for key, value in extra.items()})
    merged["GIT_TERMINAL_PROMPT"] = "0"
    merged["GIT_OPTIONAL_LOCKS"] = "0"
    return merged
