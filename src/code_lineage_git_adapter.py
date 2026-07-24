"""Bounded read-only Git history adapter for code lineage.

This module may execute only allowlisted local ``git`` read commands against an
explicit repository root.  It never fetches, writes Git state, mutates source
files, or exposes raw command output as product evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re
import subprocess
import time
from typing import Any, Callable, Iterable, Mapping

from src.code_lineage_contract import (
    CommitEvidenceRef,
    FileEventKind,
    HistoryState,
)
from src.project_version_store import validate_commit_sha, validate_repo_id


MAX_HISTORY_COMMITS = 512
MAX_PATH_CHANGES = 4096
MAX_OUTPUT_CHARS = 262_144
MAX_TIMEOUT_SECONDS = 10
NULL_BLOB_ID = "0" * 40

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@{}~^:+-]{0,255}$")
_PATH_RE = re.compile(r"^(?!/|~|[A-Za-z]:)(?!.*(?:^|/)\.{1,2}(?:/|$))(?!.*//)[^\x00-\x1f\x7f\\]{1,1024}$")
_SECRET_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer)\b\s*[:=]\s*\S+")
_ABSOLUTE_PATH_RE = re.compile(r"(?<![A-Za-z0-9._-])(?:[A-Za-z]:[\\/]|/)(?:[^\s/\\]+[/\\])*[^\s]+")


class CodeLineageGitAdapterError(ValueError):
    """Raised when a Git history read is unsafe, incomplete, or inconsistent."""


@dataclass(frozen=True, slots=True)
class GitCommandResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    duration_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass(frozen=True, slots=True)
class GitPathChange:
    event_kind: FileEventKind
    old_path: str
    new_path: str
    old_blob_id: str
    new_blob_id: str
    score: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_kind": self.event_kind.value,
            "old_path": self.old_path,
            "new_path": self.new_path,
            "old_blob_id": self.old_blob_id,
            "new_blob_id": self.new_blob_id,
            "score": self.score,
        }


@dataclass(frozen=True, slots=True)
class GitCommitChangeSet:
    commit_ref: str
    commit_id: str
    parent_ids: tuple[str, ...]
    changes: tuple[GitPathChange, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "commit_ref": self.commit_ref,
            "commit_id": self.commit_id,
            "parent_ids": list(self.parent_ids),
            "changes": [change.to_dict() for change in self.changes],
        }


@dataclass(frozen=True, slots=True)
class GitHistorySlice:
    repo_id: str
    head_commit_id: str
    shallow_repository: bool
    history_state: HistoryState
    missing_ref: str
    commits: tuple[CommitEvidenceRef, ...]
    change_sets: tuple[GitCommitChangeSet, ...]
    command_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "head_commit_id": self.head_commit_id,
            "shallow_repository": self.shallow_repository,
            "history_state": self.history_state.value,
            "missing_ref": self.missing_ref,
            "commits": [commit.to_dict() for commit in self.commits],
            "change_sets": [change_set.to_dict() for change_set in self.change_sets],
            "command_count": self.command_count,
        }


GitCommandRunner = Callable[[tuple[str, ...]], GitCommandResult]


class CodeLineageGitAdapter:
    """Read a bounded local Git history slice and normalize lineage evidence."""

    def __init__(
        self,
        repo_root: str | os.PathLike[str],
        *,
        repo_id: str,
        command_runner: Callable[..., GitCommandResult] | None = None,
        timeout_seconds: int = MAX_TIMEOUT_SECONDS,
    ) -> None:
        self.repo_id = validate_repo_id(repo_id)
        self.repo_root = Path(repo_root).expanduser().resolve(strict=False)
        self.timeout_seconds = _timeout(timeout_seconds)
        self.command_runner = command_runner or run_git_history_subprocess_command
        self._assert_local_git_repo()

    def read_history(
        self,
        *,
        end_ref: str = "HEAD",
        start_ref: str = "",
        max_commits: int = 100,
        indexed_at: str,
    ) -> GitHistorySlice:
        end = _ref(end_ref, "end_ref")
        start = _ref(start_ref, "start_ref", allow_empty=True)
        limit = _limit(max_commits)
        command_count = 0
        shallow_result = self._run(("git", "rev-parse", "--is-shallow-repository"))
        command_count += 1
        self._require_ok(shallow_result, "shallow check")
        shallow = shallow_result.stdout.strip().lower() == "true"

        head_result = self._run(("git", "rev-parse", "--verify", f"{end}^{{commit}}"))
        command_count += 1
        if not head_result.ok:
            return self._missing_slice(
                missing_ref=end,
                shallow=shallow,
                indexed_at=indexed_at,
                command_count=command_count,
            )
        head = _sha(head_result.stdout.strip(), "head_commit_id")
        rev_args = ("git", "rev-list", "--topo-order", "--parents", "--max-count", str(limit), *self._range_args(start, head))
        rev_result = self._run(rev_args)
        command_count += 1
        if not rev_result.ok:
            return self._missing_slice(
                missing_ref=start or end,
                shallow=shallow,
                indexed_at=indexed_at,
                command_count=command_count,
                head_commit_id=head,
            )

        rows = tuple(line.strip().split() for line in rev_result.stdout.splitlines() if line.strip())
        commits: list[CommitEvidenceRef] = []
        changes: list[GitCommitChangeSet] = []
        ids_in_window = {row[0] for row in rows if row}
        for row in rows:
            commit_id = _sha(row[0], "commit_id")
            parent_ids = tuple(_sha(item, "parent_id") for item in row[1:])
            detail = self._run(("git", "show", "-s", "--format=%H%x00%P%x00%aI%x00%cI", commit_id))
            command_count += 1
            self._require_ok(detail, "commit detail")
            commit = _commit_from_show(
                detail.stdout,
                repo_id=self.repo_id,
                indexed_at=indexed_at,
                shallow=shallow,
                known_ids=ids_in_window,
            )
            commits.append(commit)
            diff = self._run(("git", "diff-tree", "--root", "-r", "--raw", "-M", "-C", "--no-commit-id", commit_id))
            command_count += 1
            self._require_ok(diff, "path changes")
            changes.append(
                GitCommitChangeSet(
                    commit_ref=commit.commit_ref,
                    commit_id=commit.commit_id,
                    parent_ids=commit.parent_ids,
                    changes=_parse_raw_diff(diff.stdout),
                )
            )
        history_state = HistoryState.PARTIAL if shallow or any(commit.missing_parent_ids for commit in commits) else HistoryState.COMPLETE
        return GitHistorySlice(
            repo_id=self.repo_id,
            head_commit_id=head,
            shallow_repository=shallow,
            history_state=history_state,
            missing_ref="",
            commits=tuple(sorted(commits, key=lambda item: item.commit_id)),
            change_sets=tuple(sorted(changes, key=lambda item: item.commit_id)),
            command_count=command_count,
        )

    def _range_args(self, start: str, end: str) -> tuple[str, ...]:
        if not start:
            return (end,)
        return (f"{start}..{end}",)

    def _missing_slice(
        self,
        *,
        missing_ref: str,
        shallow: bool,
        indexed_at: str,
        command_count: int,
        head_commit_id: str = "",
    ) -> GitHistorySlice:
        return GitHistorySlice(
            repo_id=self.repo_id,
            head_commit_id=head_commit_id,
            shallow_repository=shallow,
            history_state=HistoryState.PARTIAL,
            missing_ref=_ref(missing_ref, "missing_ref"),
            commits=(),
            change_sets=(),
            command_count=command_count,
        )

    def _run(self, argv: tuple[str, ...]) -> GitCommandResult:
        if not git_history_command_is_allowed(argv):
            raise CodeLineageGitAdapterError("unsupported read-only Git history command")
        result = self.command_runner(argv, cwd=self.repo_root, timeout_seconds=self.timeout_seconds, env={})
        if not isinstance(result, GitCommandResult):
            raise CodeLineageGitAdapterError("command_runner must return GitCommandResult")
        return GitCommandResult(
            exit_code=result.exit_code,
            stdout=str(result.stdout or "")[:MAX_OUTPUT_CHARS],
            stderr=_redact(str(result.stderr or "")),
            timed_out=result.timed_out,
            duration_seconds=result.duration_seconds,
        )

    @staticmethod
    def _require_ok(result: GitCommandResult, label: str) -> None:
        if not result.ok:
            raise CodeLineageGitAdapterError(f"git {label} failed: {_redact(result.stderr or result.stdout or 'unknown')}")

    def _assert_local_git_repo(self) -> None:
        if not self.repo_root.is_dir() or not (self.repo_root / ".git").exists():
            raise CodeLineageGitAdapterError("repo_root must be a local Git repository")
        try:
            self.repo_root.resolve(strict=False).relative_to(Path.cwd().resolve(strict=False))
        except ValueError:
            # Temporary test repositories outside cwd are allowed only when the
            # caller supplied the explicit root; all command cwd values stay
            # pinned to that resolved root.
            pass


def run_git_history_subprocess_command(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    timeout_seconds: int,
    env: Mapping[str, str],
) -> GitCommandResult:
    if not git_history_command_is_allowed(argv):
        raise CodeLineageGitAdapterError("unsupported read-only Git history command")
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
        return GitCommandResult(
            exit_code=124,
            stdout=str(exc.stdout or ""),
            stderr=str(exc.stderr or "command timed out"),
            timed_out=True,
            duration_seconds=round(time.monotonic() - started, 3),
        )
    return GitCommandResult(
        exit_code=int(completed.returncode),
        stdout=str(completed.stdout or ""),
        stderr=str(completed.stderr or ""),
        timed_out=False,
        duration_seconds=round(time.monotonic() - started, 3),
    )


def git_history_command_is_allowed(argv: tuple[str, ...]) -> bool:
    try:
        if argv == ("git", "rev-parse", "--is-shallow-repository"):
            return True
        if len(argv) == 4 and argv[:3] == ("git", "rev-parse", "--verify"):
            return argv[3].endswith("^{commit}") and bool(_ref(argv[3][:-9], "ref"))
        if len(argv) in (6, 7) and argv[:4] == ("git", "rev-list", "--topo-order", "--parents"):
            return argv[4] == "--max-count" and argv[5].isdigit() and 1 <= int(argv[5]) <= MAX_HISTORY_COMMITS and all(_range_ref(item) for item in argv[6:])
        if len(argv) == 5 and argv[:4] == ("git", "show", "-s", "--format=%H%x00%P%x00%aI%x00%cI"):
            return _SHA_RE.fullmatch(argv[4]) is not None
        if len(argv) == 9 and argv[:8] == ("git", "diff-tree", "--root", "-r", "--raw", "-M", "-C", "--no-commit-id"):
            return _SHA_RE.fullmatch(argv[8]) is not None
        return False
    except CodeLineageGitAdapterError:
        return False


def _commit_from_show(
    output: str,
    *,
    repo_id: str,
    indexed_at: str,
    shallow: bool,
    known_ids: set[str],
) -> CommitEvidenceRef:
    parts = output.rstrip("\n").split("\x00")
    if len(parts) != 4:
        raise CodeLineageGitAdapterError("commit detail output is incomplete")
    commit_id = _sha(parts[0], "commit_id")
    parent_ids = tuple(_sha(item, "parent_id") for item in parts[1].split() if item)
    missing_parents = tuple(parent for parent in parent_ids if parent not in known_ids)
    state = HistoryState.PARTIAL if shallow or missing_parents else HistoryState.COMPLETE
    return CommitEvidenceRef(
        repo_id=repo_id,
        commit_id=commit_id,
        parent_ids=parent_ids,
        authored_at=parts[2],
        committed_at=parts[3],
        indexed_at=indexed_at,
        history_state=state,
        shallow_boundary=shallow and bool(missing_parents),
        missing_parent_ids=missing_parents,
    )


def _parse_raw_diff(output: str) -> tuple[GitPathChange, ...]:
    changes: list[GitPathChange] = []
    for line in output.splitlines()[:MAX_PATH_CHANGES]:
        if not line.startswith(":"):
            continue
        header, *paths = line.split("\t")
        fields = header.split()
        if len(fields) != 5:
            continue
        old_blob = _blob(fields[2], "old_blob_id")
        new_blob = _blob(fields[3], "new_blob_id")
        status_token = fields[4]
        status = status_token[0]
        score = _score(status_token[1:]) if len(status_token) > 1 else None
        old_path = _path(paths[0]) if paths else ""
        new_path = _path(paths[-1]) if paths else ""
        event_kind = _event_kind(status, old_path=old_path, new_path=new_path)
        if event_kind is FileEventKind.ADDED:
            old_path = ""
        if event_kind is FileEventKind.DELETED:
            new_path = ""
        changes.append(GitPathChange(event_kind, old_path, new_path, old_blob, new_blob, score))
    return tuple(sorted(changes, key=lambda item: (item.new_path or item.old_path, item.event_kind.value, item.score or -1)))


def _event_kind(status: str, *, old_path: str, new_path: str) -> FileEventKind:
    if status == "A":
        return FileEventKind.ADDED
    if status == "D":
        return FileEventKind.DELETED
    if status == "R":
        return FileEventKind.RENAMED
    if status == "C":
        return FileEventKind.COPIED
    if status == "M":
        return FileEventKind.MODIFIED
    if status == "T":
        return FileEventKind.MODIFIED
    raise CodeLineageGitAdapterError(f"unsupported path change status: {status}")


def _merge_env(extra: Mapping[str, str]) -> dict[str, str]:
    allowed = {"PATH", "SYSTEMROOT", "COMSPEC", "HOME", "USERPROFILE"}
    merged = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    merged.update({str(key): str(value) for key, value in extra.items()})
    merged["GIT_TERMINAL_PROMPT"] = "0"
    merged["GIT_OPTIONAL_LOCKS"] = "0"
    return merged


def _limit(value: int) -> int:
    if isinstance(value, bool):
        raise CodeLineageGitAdapterError("max_commits must be an integer")
    try:
        limit = int(value)
    except Exception as exc:
        raise CodeLineageGitAdapterError("max_commits must be an integer") from exc
    if limit < 1 or limit > MAX_HISTORY_COMMITS:
        raise CodeLineageGitAdapterError(f"max_commits must be between 1 and {MAX_HISTORY_COMMITS}")
    return limit


def _timeout(value: int) -> int:
    if isinstance(value, bool):
        raise CodeLineageGitAdapterError("timeout_seconds must be an integer")
    try:
        timeout = int(value)
    except Exception as exc:
        raise CodeLineageGitAdapterError("timeout_seconds must be an integer") from exc
    return max(1, min(timeout, MAX_TIMEOUT_SECONDS))


def _ref(value: str, field_name: str, *, allow_empty: bool = False) -> str:
    text = str(value or "").strip()
    if not text and allow_empty:
        return ""
    if not _REF_RE.fullmatch(text) or text.startswith("-") or ".." in text:
        raise CodeLineageGitAdapterError(f"{field_name} is not a bounded Git ref")
    return text


def _range_ref(value: str) -> bool:
    text = str(value or "")
    if ".." in text:
        start, end = text.split("..", 1)
        return bool(start) and bool(end) and _ref(start, "start") and (_SHA_RE.fullmatch(end) is not None or bool(_ref(end, "end")))
    return _SHA_RE.fullmatch(text) is not None or bool(_ref(text, "ref"))


def _sha(value: Any, field_name: str) -> str:
    try:
        return validate_commit_sha(value)
    except Exception as exc:
        raise CodeLineageGitAdapterError(f"{field_name} must be a 40-character commit SHA") from exc


def _blob(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if text == NULL_BLOB_ID or _SHA_RE.fullmatch(text):
        return text
    raise CodeLineageGitAdapterError(f"{field_name} must be a Git blob id")


def _path(value: Any) -> str:
    text = str(value or "").strip()
    if not _PATH_RE.fullmatch(text):
        raise CodeLineageGitAdapterError("Git path must be relative and normalized")
    return text


def _score(value: str) -> int:
    if not value.isdigit():
        raise CodeLineageGitAdapterError("rename/copy score must be numeric")
    score = int(value)
    if score < 0 or score > 100:
        raise CodeLineageGitAdapterError("rename/copy score must be between 0 and 100")
    return score


def _redact(value: str) -> str:
    text = _SECRET_RE.sub("[redacted-secret]", str(value or ""))
    return _ABSOLUTE_PATH_RE.sub("[redacted-path]", text)[:4000]
