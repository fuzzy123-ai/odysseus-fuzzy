"""Read-only Git adapter for repos registered in ``src.repo_registry``."""

from __future__ import annotations

import os
import re
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from src.constants import MAX_OUTPUT_CHARS
from src.repo_registry import RepoRecord, RepoRegistry, RepoRegistryError, redact_remote_url


_MAX_TIMEOUT_SECONDS = 8
_MAX_LOG_LIMIT = 100
_MAX_LIST_ITEMS = 200
_LOG_PRETTY = "format:%H%x09%ad%x09%an%x09%s"
_SECRET_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer)\b\s*[:=]\s*\S+")
_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s\t]+")
_ABSOLUTE_PATH_RE = re.compile(r"(?<![A-Za-z0-9._-])/(?:[^\s/]+/)*[^\s]+")
_FORGE_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_FORGE_COMMIT_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_FORGE_VERSION_RE = re.compile(r"^pv_[0-9a-f]{32}$")
_FORGE_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_FORGE_OWNER_SCOPE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}:[a-z0-9][a-z0-9_.-]{0,127}$")
_FORGE_WINDOWS_INVALID_PATH_CHARS = frozenset('<>:"|?*')
_FORGE_WINDOWS_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)

MAX_FORGE_SNAPSHOT_FILES = 512
MAX_FORGE_FILE_BYTES = 16 * 1024 * 1024
MAX_FORGE_EXACT_READ_BYTES = 1_000_000


class RepoGitAdapterError(ValueError):
    """Raised when read-only repo Git access is unsafe or fails."""


class ForgeSnapshotError(RepoGitAdapterError):
    """Raised when immutable Forge snapshot evidence is incomplete or unsafe."""


@dataclass(frozen=True, slots=True)
class ForgeSnapshotAuthorityBinding:
    """Versioned adapter and admission authority bound to every Forge fact."""

    adapter_id: str
    adapter_version: str
    adapter_generation: str
    admission_policy_generation: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "adapter_id", _forge_token(self.adapter_id, "adapter_id"))
        object.__setattr__(self, "adapter_version", _forge_token(self.adapter_version, "adapter_version"))
        object.__setattr__(self, "adapter_generation", _forge_token(self.adapter_generation, "adapter_generation"))
        object.__setattr__(
            self,
            "admission_policy_generation",
            _forge_token(self.admission_policy_generation, "admission_policy_generation"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "adapter_generation": self.adapter_generation,
            "admission_policy_generation": self.admission_policy_generation,
        }


@dataclass(frozen=True, slots=True)
class ForgeSnapshotRequest:
    """An authenticated, version-bound request for content-free Forge facts.

    ``authorization_ref`` is an opaque reference resolved only by the supplied
    Forge boundary.  It is deliberately not a credential and is never returned
    in a snapshot or reader reference.
    """

    owner_scope: str
    authorization_ref: str
    repo_id: str
    version_id: str
    commit_sha: str
    authority_binding: ForgeSnapshotAuthorityBinding

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_scope", _forge_owner_scope(self.owner_scope))
        object.__setattr__(self, "authorization_ref", _forge_token(self.authorization_ref, "authorization_ref"))
        object.__setattr__(self, "repo_id", _forge_token(self.repo_id, "repo_id"))
        object.__setattr__(self, "version_id", _forge_version_id(self.version_id))
        object.__setattr__(self, "commit_sha", _forge_commit_sha(self.commit_sha))
        if type(self.authority_binding) is not ForgeSnapshotAuthorityBinding:
            raise ForgeSnapshotError("authority_binding must be a typed Forge authority binding")


@dataclass(frozen=True, slots=True)
class ForgeSnapshotFile:
    """One source-free file descriptor from an immutable Forge snapshot."""

    path: str
    content_sha256: str
    byte_count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _forge_relative_path(self.path))
        object.__setattr__(self, "content_sha256", _forge_sha256(self.content_sha256, "content_sha256"))
        if type(self.byte_count) is not int or not 0 <= self.byte_count <= MAX_FORGE_FILE_BYTES:
            raise ForgeSnapshotError("byte_count is invalid or exceeds the Forge file bound")

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "content_sha256": self.content_sha256,
            "byte_count": self.byte_count,
        }


@dataclass(frozen=True, slots=True)
class ForgeSnapshotInventory:
    """Immutable, content-free inventory for one retained Forge version."""

    owner_scope: str
    repo_id: str
    version_id: str
    commit_sha: str
    manifest_sha256: str
    authority_binding: ForgeSnapshotAuthorityBinding
    files: tuple[ForgeSnapshotFile, ...]
    snapshot_digest: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_scope", _forge_owner_scope(self.owner_scope))
        object.__setattr__(self, "repo_id", _forge_token(self.repo_id, "repo_id"))
        object.__setattr__(self, "version_id", _forge_version_id(self.version_id))
        object.__setattr__(self, "commit_sha", _forge_commit_sha(self.commit_sha))
        object.__setattr__(self, "manifest_sha256", _forge_sha256(self.manifest_sha256, "manifest_sha256"))
        if type(self.authority_binding) is not ForgeSnapshotAuthorityBinding:
            raise ForgeSnapshotError("authority_binding must be a typed Forge authority binding")
        if type(self.files) is not tuple or len(self.files) > MAX_FORGE_SNAPSHOT_FILES:
            raise ForgeSnapshotError("Forge snapshot files must be a bounded tuple")
        if not all(type(item) is ForgeSnapshotFile for item in self.files):
            raise ForgeSnapshotError("Forge snapshot files must be typed descriptors")
        files = tuple(sorted(self.files, key=lambda item: item.path))
        if len({_forge_locator_key(item.path) for item in files}) != len(files):
            raise ForgeSnapshotError("Forge snapshot contains case-insensitive duplicate file paths")
        object.__setattr__(self, "files", files)
        expected = self._digest()
        supplied = self.snapshot_digest
        if supplied and _forge_sha256(supplied, "snapshot_digest") != expected:
            raise ForgeSnapshotError("Forge snapshot digest does not match immutable inventory")
        object.__setattr__(self, "snapshot_digest", expected)

    def _digest(self) -> str:
        import hashlib
        import json

        payload = {
            "schema": "odysseus.forge_snapshot_inventory.v1",
            "owner_scope": self.owner_scope,
            "repo_id": self.repo_id,
            "version_id": self.version_id,
            "commit_sha": self.commit_sha,
            "manifest_sha256": self.manifest_sha256,
            "authority_binding": self.authority_binding.to_dict(),
            "files": [item.to_dict() for item in self.files],
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    def file(self, path: object) -> ForgeSnapshotFile:
        locator_key = _forge_locator_key(path)
        for item in self.files:
            if _forge_locator_key(item.path) == locator_key:
                return item
        raise ForgeSnapshotError("file is not present in the immutable Forge snapshot")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "odysseus.forge_snapshot_inventory.v1",
            "owner_scope": self.owner_scope,
            "repo_id": self.repo_id,
            "version_id": self.version_id,
            "commit_sha": self.commit_sha,
            "manifest_sha256": self.manifest_sha256,
            "authority_binding": self.authority_binding.to_dict(),
            "files": [item.to_dict() for item in self.files],
            "snapshot_digest": self.snapshot_digest,
        }


@dataclass(frozen=True, slots=True)
class ForgeExactReaderReference:
    """A bounded locator for a future exact reader, never file content."""

    owner_scope: str
    repo_id: str
    version_id: str
    commit_sha: str
    snapshot_digest: str
    path: str
    content_sha256: str
    max_bytes: int
    authority_binding: ForgeSnapshotAuthorityBinding

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_scope", _forge_owner_scope(self.owner_scope))
        object.__setattr__(self, "repo_id", _forge_token(self.repo_id, "repo_id"))
        object.__setattr__(self, "version_id", _forge_version_id(self.version_id))
        object.__setattr__(self, "commit_sha", _forge_commit_sha(self.commit_sha))
        object.__setattr__(self, "snapshot_digest", _forge_sha256(self.snapshot_digest, "snapshot_digest"))
        object.__setattr__(self, "path", _forge_relative_path(self.path, require_canonical=True))
        object.__setattr__(self, "content_sha256", _forge_sha256(self.content_sha256, "content_sha256"))
        if type(self.authority_binding) is not ForgeSnapshotAuthorityBinding:
            raise ForgeSnapshotError("authority_binding must be a typed Forge authority binding")
        if type(self.max_bytes) is not int or not 1 <= self.max_bytes <= MAX_FORGE_EXACT_READ_BYTES:
            raise ForgeSnapshotError("max_bytes is invalid or exceeds the exact-reader bound")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "odysseus.forge_exact_reader_reference.v1",
            "owner_scope": self.owner_scope,
            "repo_id": self.repo_id,
            "version_id": self.version_id,
            "commit_sha": self.commit_sha,
            "snapshot_digest": self.snapshot_digest,
            "path": self.path,
            "content_sha256": self.content_sha256,
            "max_bytes": self.max_bytes,
            "authority_binding": self.authority_binding.to_dict(),
        }


@runtime_checkable
class ForgeSnapshotReader(Protocol):
    """Injected authenticated Forge boundary; it must not be a worktree scanner."""

    def inventory(self, request: ForgeSnapshotRequest) -> ForgeSnapshotInventory: ...

    def exact_reader_reference(
        self,
        request: ForgeSnapshotRequest,
        *,
        path: str,
        max_bytes: int,
    ) -> ForgeExactReaderReference: ...


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


def _forge_token(value: object, field_name: str) -> str:
    if type(value) is not str or not _FORGE_TOKEN_RE.fullmatch(value):
        raise ForgeSnapshotError(f"{field_name} must be a bounded Forge token")
    return value


def _forge_owner_scope(value: object) -> str:
    if type(value) is not str or not _FORGE_OWNER_SCOPE_RE.fullmatch(value):
        raise ForgeSnapshotError("owner_scope must be an authenticated explicit scope")
    return value


def _forge_version_id(value: object) -> str:
    if type(value) is not str or not _FORGE_VERSION_RE.fullmatch(value):
        raise ForgeSnapshotError("version_id must be an immutable Forge version")
    return value


def _forge_commit_sha(value: object) -> str:
    if type(value) is not str or not _FORGE_COMMIT_RE.fullmatch(value):
        raise ForgeSnapshotError("commit_sha must be an immutable Forge commit")
    return value


def _forge_sha256(value: object, field_name: str) -> str:
    if type(value) is not str:
        raise ForgeSnapshotError(f"{field_name} must be a SHA-256 digest")
    normalized = value.lower()
    if not normalized.startswith("sha256:"):
        normalized = "sha256:" + normalized
    if not _FORGE_SHA256_RE.fullmatch(normalized):
        raise ForgeSnapshotError(f"{field_name} must be a SHA-256 digest")
    return normalized


def _forge_relative_path(value: object, *, require_canonical: bool = False) -> str:
    if type(value) is not str or not value or len(value) > 1024:
        raise ForgeSnapshotError("Forge file path is invalid or unbounded")
    normalized = unicodedata.normalize("NFC", value)
    if require_canonical and normalized != value:
        raise ForgeSnapshotError("Forge file path must use its canonical NFC locator")
    if any(ord(character) < 32 for character in normalized):
        raise ForgeSnapshotError("Forge file path contains control characters")
    if "\\" in normalized or normalized.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", normalized):
        raise ForgeSnapshotError("Forge file path must be relative and use forward slashes")
    parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ForgeSnapshotError("Forge file path contains invalid segments")
    for part in parts:
        if any(character in _FORGE_WINDOWS_INVALID_PATH_CHARS for character in part):
            raise ForgeSnapshotError("Forge file path contains Windows-ambiguous characters")
        if part.endswith((".", " ")):
            raise ForgeSnapshotError("Forge file path contains a Windows-ambiguous trailing character")
        if part.split(".", 1)[0].casefold() in _FORGE_WINDOWS_RESERVED_NAMES:
            raise ForgeSnapshotError("Forge file path contains a reserved Windows device name")
    return normalized


def _forge_locator_key(value: object) -> str:
    """Return the NFC and Windows-case-insensitive identity for one locator."""

    return _forge_relative_path(value).casefold()
