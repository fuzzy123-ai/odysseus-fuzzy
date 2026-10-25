"""Backend contracts for a focused Odysseus coding-agent lane.

This module owns the boring but important invariants before any UI or LLM
workflow touches code: registered repos only, isolated worktree plans, bounded
check commands, and explicit quality gates.
"""

from __future__ import annotations

import hashlib
import difflib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Protocol

from src.constants import DATA_DIR, MAX_OUTPUT_CHARS
from src.repo_git_adapter import RepoGitAdapterError
from src.repo_registry import RepoRecord, RepoRegistry, RepoRegistryError


_MAX_OBJECTIVE = 500
_MAX_PATHS = 200
_MAX_TIMEOUT_SECONDS = 1800
_MAX_PATCH_BYTES = 256_000
_MAX_DIFF_CHARS = 120_000
_DEFAULT_TIMEOUT_SECONDS = 300
_OPERATOR_DECISIONS = ("go", "hold", "no_go", "missing")
_DECISIONS = ("blocked", "hold", "plan_ready", "created", "verified")
_REVIEW_DECISIONS = ("approved", "changes_requested", "blocked", "missing")
_PUBLISH_DECISIONS = ("blocked", "plan_ready")
_SUBAGENT_ROLES = ("worker", "reviewer")
_REF_RE = re.compile(r"^[A-Za-z0-9._/@:-]{1,160}$")
_TASK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")
_SECRET_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer)\b\s*[:=]\s*\S+")
_HARD_BLOCKED_ROOTS = (".git", ".hg", ".svn", ".env", ".venv", "venv", "node_modules")
_TRUE_VALUES = {"1", "true", "yes", "on"}
_SHA256_RE = re.compile(r"^sha256:[a-f0-9]{64}$")


class CodingAgentBackendError(ValueError):
    """Raised when a coding-agent backend request is unsafe or invalid."""


class CodingCommandRunner(Protocol):
    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        timeout_seconds: int,
        env: Mapping[str, str],
    ) -> "CodingCommandResult":
        ...


def _normalize_bool(value: Any, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise CodingAgentBackendError(f"{field_name} must be a boolean")
    return value


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False, max_len: int = _MAX_OBJECTIVE) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text and not allow_empty:
        raise CodingAgentBackendError(f"{field_name} must not be empty")
    if len(text) > max_len:
        raise CodingAgentBackendError(f"{field_name} exceeds max length {max_len}")
    if _SECRET_RE.search(text):
        raise CodingAgentBackendError(f"{field_name} appears to contain secret material")
    return text


def _normalize_choice(value: Any, *, field_name: str, choices: tuple[str, ...]) -> str:
    text = _normalize_text(value, field_name=field_name).lower().replace("-", "_")
    if text not in choices:
        raise CodingAgentBackendError(f"unsupported {field_name}: {value!r}")
    return text


def _normalize_repo_path(value: Any, *, field_name: str, allow_blocked: bool = False) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise CodingAgentBackendError(f"{field_name} must not be empty")
    if "\\" in raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise CodingAgentBackendError(f"{field_name} must be repo-relative and use forward slashes")
    parts = PurePosixPath(raw).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise CodingAgentBackendError(f"{field_name} must not contain traversal segments")
    path = "/".join(parts)
    lowered = path.lower()
    if not allow_blocked and _path_hits_blocked_root(lowered, _HARD_BLOCKED_ROOTS):
        raise CodingAgentBackendError(f"{field_name} targets a blocked project path")
    if len(path) > 240:
        raise CodingAgentBackendError(f"{field_name} exceeds max length 240")
    if _SECRET_RE.search(path):
        raise CodingAgentBackendError(f"{field_name} appears to contain secret material")
    return path


def _normalize_path_list(
    values: Iterable[Any],
    *,
    field_name: str,
    allow_empty: bool = True,
    allow_blocked: bool = False,
) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _normalize_repo_path(value, field_name=field_name, allow_blocked=allow_blocked)
        if item not in seen:
            seen.add(item)
            result.append(item)
    if len(result) > _MAX_PATHS:
        raise CodingAgentBackendError(f"{field_name} exceeds max item count {_MAX_PATHS}")
    if not allow_empty and not result:
        raise CodingAgentBackendError(f"{field_name} must not be empty")
    return tuple(sorted(result))


def _normalize_git_ref(value: Any, *, field_name: str, fallback: str = "HEAD") -> str:
    ref = _normalize_text(value or fallback, field_name=field_name, max_len=160)
    if (
        not _REF_RE.fullmatch(ref)
        or ref.startswith(("-", ".", "/"))
        or ref.endswith(("/", ".lock"))
        or ".." in ref
        or "//" in ref
        or "@{" in ref
    ):
        raise CodingAgentBackendError(f"{field_name} contains unsupported git ref syntax")
    return ref


def _normalize_task_id(value: Any, *, repo_id: str, objective: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        digest = hashlib.sha256(f"{repo_id}:{objective}".encode("utf-8")).hexdigest()[:10]
        raw = f"task-{digest}"
    normalized = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-._")
    if not _TASK_ID_RE.fullmatch(normalized):
        raise CodingAgentBackendError("task_id must be 3-80 chars using lowercase letters, digits, '.', '_' or '-'")
    return normalized


def _normalize_timeout(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CodingAgentBackendError("timeout_seconds must be an int")
    if value < 1 or value > _MAX_TIMEOUT_SECONDS:
        raise CodingAgentBackendError(f"timeout_seconds must be between 1 and {_MAX_TIMEOUT_SECONDS}")
    return value


def _bool_env(value: str | None) -> bool:
    return str(value or "").strip().lower() in _TRUE_VALUES


def _redact(value: str) -> str:
    text = str(value or "")
    text = _SECRET_RE.sub("[redacted-secret]", text)
    return text[:MAX_OUTPUT_CHARS]


def _bounded_diff(value: str) -> str:
    text = str(value or "")
    if _SECRET_RE.search(text):
        return "[redacted-secret-diff]"
    return text[:_MAX_DIFF_CHARS]


def _sha256(value: str | bytes) -> str:
    raw = value if isinstance(value, bytes) else str(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _canonical_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return _sha256(encoded)


def _paths_binding_digest(paths: Iterable[str]) -> str:
    """Match the canonical Planning path digest without importing runner state."""

    return _sha256(json.dumps(tuple(paths), ensure_ascii=False, sort_keys=True, default=str))


def _require_sha256(value: Any, *, field_name: str) -> str:
    text = str(value or "")
    if not _SHA256_RE.fullmatch(text):
        raise CodingAgentBackendError(f"{field_name} must be canonical lowercase SHA-256")
    return text


def _normalize_identity(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    return _normalize_text(value, field_name=field_name, allow_empty=allow_empty, max_len=80)


def _normalize_commit_revision(value: Any, *, field_name: str) -> str:
    revision = str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise CodingAgentBackendError(f"{field_name} must be a 40-character lowercase commit SHA")
    return revision


def _path_has_prefix(path: str, root: str) -> bool:
    return path == root or path.startswith(f"{root}/")


def _path_hits_blocked_root(path: str, blocked_roots: Iterable[str]) -> bool:
    lowered = str(path or "").lower()
    parts = lowered.split("/")
    for root in blocked_roots:
        clean = str(root or "").strip().lower().strip("/")
        if clean and (_path_has_prefix(lowered, clean) or clean in parts):
            return True
    return False


def _path_in_allowed_scope(path: str, allowed_paths: tuple[str, ...]) -> bool:
    if not allowed_paths:
        return True
    return any(_path_has_prefix(path, allowed) for allowed in allowed_paths)


def _resolve_child(root: Path, candidate: Path, *, label: str) -> Path:
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve()
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise CodingAgentBackendError(f"{label} escapes configured root") from exc
    return resolved_candidate


def _worktree_path_from_plan(plan: "CodingTaskPlan", worktree_base: str | Path | None) -> Path:
    base = _worktree_base(worktree_base)
    return _resolve_child(base, base / plan.repo_id / plan.task_id, label="worktree path")


def _repo_root_for_record(record: RepoRecord, workspace_base: str | Path) -> Path:
    base = Path(workspace_base).resolve()
    repo_root = (base / record.path_ref).resolve()
    _resolve_child(base, repo_root, label="repo path")
    if not repo_root.is_dir() or not (repo_root / ".git").exists():
        raise CodingAgentBackendError("registered repo path is not a local Git repository")
    return repo_root


def _worktree_base(worktree_base: str | Path | None) -> Path:
    raw = Path(worktree_base) if worktree_base is not None else Path(DATA_DIR) / "coding-worktrees"
    return raw.expanduser().resolve()


def _merge_env(extra_env: Mapping[str, str]) -> dict[str, str]:
    allowed_keys = ("PATH", "SYSTEMROOT", "COMSPEC", "HOME", "USERPROFILE")
    merged = {key: value for key, value in os.environ.items() if key.upper() in allowed_keys}
    merged.update({str(key): str(value) for key, value in extra_env.items()})
    merged["GIT_TERMINAL_PROMPT"] = "0"
    merged["GIT_OPTIONAL_LOCKS"] = "0"
    return merged


@dataclass(frozen=True, slots=True)
class CodingCheckCommand:
    argv: tuple[str, ...]
    timeout_seconds: int

    @classmethod
    def create(cls, *, argv: Iterable[Any], timeout_seconds: Any = _DEFAULT_TIMEOUT_SECONDS) -> "CodingCheckCommand":
        normalized = tuple(_normalize_text(item, field_name="check.argv", max_len=140) for item in argv)
        if not normalized:
            raise CodingAgentBackendError("check argv must not be empty")
        if not coding_check_command_is_allowed(normalized):
            raise CodingAgentBackendError("check command is not allowed")
        return cls(argv=normalized, timeout_seconds=_normalize_timeout(timeout_seconds))

    def to_dict(self) -> dict[str, Any]:
        return {"argv": list(self.argv), "timeout_seconds": self.timeout_seconds}


@dataclass(frozen=True, slots=True)
class CodingCommandResult:
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
            "stdout": _redact(self.stdout),
            "stderr": _redact(self.stderr),
            "timed_out": self.timed_out,
            "duration_seconds": self.duration_seconds,
        }


@dataclass(frozen=True, slots=True)
class CodingPatchOperation:
    path: str
    find: str
    replace: str
    expected_replacements: int

    @classmethod
    def create(
        cls,
        *,
        path: Any,
        find: Any,
        replace: Any,
        expected_replacements: Any = 1,
    ) -> "CodingPatchOperation":
        needle = str(find or "")
        replacement = str(replace or "")
        if not needle:
            raise CodingAgentBackendError("patch find text must not be empty")
        if len(needle.encode("utf-8")) > _MAX_PATCH_BYTES or len(replacement.encode("utf-8")) > _MAX_PATCH_BYTES:
            raise CodingAgentBackendError(f"patch text exceeds max size {_MAX_PATCH_BYTES}")
        if _SECRET_RE.search(needle) or _SECRET_RE.search(replacement):
            raise CodingAgentBackendError("patch text appears to contain secret material")
        try:
            expected = int(expected_replacements)
        except Exception as exc:
            raise CodingAgentBackendError("expected_replacements must be an integer") from exc
        if expected < 1 or expected > 50:
            raise CodingAgentBackendError("expected_replacements must be between 1 and 50")
        return cls(
            path=_normalize_repo_path(path, field_name="patch.path"),
            find=needle,
            replace=replacement,
            expected_replacements=expected,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "find_bytes": len(self.find.encode("utf-8")),
            "replace_bytes": len(self.replace.encode("utf-8")),
            "expected_replacements": self.expected_replacements,
        }


@dataclass(frozen=True, slots=True)
class CodingPatchResult:
    operation: CodingPatchOperation
    status: str
    replacements: int
    diff: str
    blocker: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "applied"

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation.to_dict(),
            "status": self.status,
            "replacements": self.replacements,
            "diff": _bounded_diff(self.diff),
            "blocker": self.blocker,
        }


@dataclass(frozen=True, slots=True)
class CodingWorktreeTransactionReceipt:
    """Content-free rollback boundary for one isolated coding worktree.

    This is deliberately an evidence receipt, not a second worktree authority.
    It binds the existing task plan to an exact commit, clean starting diff and
    bounded implementer identity while retaining no source content or host path.
    """

    receipt_id: str
    transaction_id: str
    repo_id: str
    task_id: str
    worktree_ref: str
    base_revision: str
    source_status_digest: str
    allowed_paths_digest: str
    rollback_boundary: str
    implementer_id: str
    execution_allowed: bool = False
    write_allowed: bool = False
    dispatch_allowed: bool = False
    live_effect_allowed: bool = False
    raw_content_visible: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "receipt_id",
            "transaction_id",
            "source_status_digest",
            "allowed_paths_digest",
            "rollback_boundary",
        ):
            _require_sha256(getattr(self, field_name), field_name=field_name)
        _normalize_text(self.repo_id, field_name="repo_id", max_len=80)
        _normalize_task_id(self.task_id, repo_id=self.repo_id, objective="transaction")
        _normalize_repo_path(self.worktree_ref, field_name="worktree_ref", allow_blocked=True)
        _normalize_commit_revision(self.base_revision, field_name="base_revision")
        _normalize_identity(self.implementer_id, field_name="implementer_id")
        for field_name in (
            "execution_allowed",
            "write_allowed",
            "dispatch_allowed",
            "live_effect_allowed",
            "raw_content_visible",
        ):
            if getattr(self, field_name) is not False:
                raise CodingAgentBackendError(f"worktree transaction {field_name} must remain false")
        if self.receipt_id != _canonical_digest(self.semantic_dict()):
            raise CodingAgentBackendError("worktree transaction receipt_id does not match canonical facts")

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "schema": "odysseus.coding_agent.worktree_transaction_receipt.v1",
            "transaction_id": self.transaction_id,
            "repo_id": self.repo_id,
            "task_id": self.task_id,
            "worktree_ref": self.worktree_ref,
            "base_revision": self.base_revision,
            "source_status_digest": self.source_status_digest,
            "allowed_paths_digest": self.allowed_paths_digest,
            "rollback_boundary": self.rollback_boundary,
            "implementer_id": self.implementer_id,
            "execution_allowed": False,
            "write_allowed": False,
            "dispatch_allowed": False,
            "live_effect_allowed": False,
            "raw_content_visible": False,
        }

    def to_dict(self) -> dict[str, Any]:
        return {"receipt_id": self.receipt_id, **self.semantic_dict(), "rollback_ready": True}


@dataclass(frozen=True, slots=True)
class CodingExactReadReceipt:
    """Fresh content-hash receipt required by strict transactional edits."""

    receipt_id: str
    transaction_id: str
    worktree_receipt_id: str
    repo_path: str
    source_digest: str
    anchor_digest: str
    reader_id: str
    line_count: int
    execution_allowed: bool = False
    write_allowed: bool = False
    raw_content_visible: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "receipt_id",
            "transaction_id",
            "worktree_receipt_id",
            "source_digest",
            "anchor_digest",
        ):
            _require_sha256(getattr(self, field_name), field_name=field_name)
        _normalize_repo_path(self.repo_path, field_name="repo_path")
        _normalize_identity(self.reader_id, field_name="reader_id")
        if isinstance(self.line_count, bool) or not isinstance(self.line_count, int) or self.line_count < 1 or self.line_count > 2_000_000:
            raise CodingAgentBackendError("line_count must be a bounded positive integer")
        for field_name in ("execution_allowed", "write_allowed", "raw_content_visible"):
            if getattr(self, field_name) is not False:
                raise CodingAgentBackendError(f"exact read {field_name} must remain false")
        if self.receipt_id != _canonical_digest(self.semantic_dict()):
            raise CodingAgentBackendError("exact read receipt_id does not match canonical facts")

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "schema": "odysseus.coding_agent.exact_read_receipt.v1",
            "transaction_id": self.transaction_id,
            "worktree_receipt_id": self.worktree_receipt_id,
            "repo_path": self.repo_path,
            "source_digest": self.source_digest,
            "anchor_digest": self.anchor_digest,
            "reader_id": self.reader_id,
            "line_count": self.line_count,
            "execution_allowed": False,
            "write_allowed": False,
            "raw_content_visible": False,
        }

    def to_dict(self) -> dict[str, Any]:
        return {"receipt_id": self.receipt_id, **self.semantic_dict()}


@dataclass(frozen=True, slots=True)
class CodingPatchTransactionReport:
    """Redacted receipt for an all-or-nothing patch attempt."""

    transaction_id: str
    worktree_receipt_id: str
    status: str
    changed_paths: tuple[str, ...]
    exact_read_receipt_ids: tuple[str, ...]
    diff_digest: str
    rollback_boundary: str
    rollback_ready: bool
    blockers: tuple[str, ...] = ()
    raw_content_visible: bool = False

    def __post_init__(self) -> None:
        _require_sha256(self.transaction_id, field_name="transaction_id")
        _require_sha256(self.worktree_receipt_id, field_name="worktree_receipt_id")
        _require_sha256(self.diff_digest, field_name="diff_digest")
        _require_sha256(self.rollback_boundary, field_name="rollback_boundary")
        if self.status not in {"applied", "blocked", "rolled_back"}:
            raise CodingAgentBackendError("patch transaction status is invalid")
        normalized_paths = _normalize_path_list(
            self.changed_paths, field_name="changed_path", allow_empty=True
        )
        if normalized_paths != self.changed_paths:
            raise CodingAgentBackendError("patch transaction changed_paths must be canonical")
        receipt_ids = tuple(
            _require_sha256(item, field_name="exact_read_receipt_id")
            for item in self.exact_read_receipt_ids
        )
        if len(set(receipt_ids)) != len(receipt_ids):
            raise CodingAgentBackendError("patch transaction exact read receipt ids must be unique")
        if type(self.rollback_ready) is not bool:
            raise CodingAgentBackendError("patch transaction rollback_ready must be a boolean")
        if self.raw_content_visible is not False:
            raise CodingAgentBackendError("patch transaction raw_content_visible must remain false")
        if self.status == "applied" and (
            not self.changed_paths or len(self.changed_paths) != len(self.exact_read_receipt_ids)
        ):
            raise CodingAgentBackendError("applied patch transaction requires one exact receipt per changed path")

    @property
    def applied(self) -> bool:
        return self.status == "applied"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "odysseus.coding_agent.patch_transaction_report.v1",
            "transaction_id": self.transaction_id,
            "worktree_receipt_id": self.worktree_receipt_id,
            "status": self.status,
            "changed_paths": list(self.changed_paths),
            "exact_read_receipt_ids": list(self.exact_read_receipt_ids),
            "diff_digest": self.diff_digest,
            "rollback_boundary": self.rollback_boundary,
            "rollback_ready": self.rollback_ready,
            "blockers": list(self.blockers),
            "raw_content_visible": False,
        }


@dataclass(frozen=True, slots=True)
class CodingToolCapsule:
    """An argv-only, non-executing capability projection for one check."""

    capsule_id: str
    task_id: str
    check_index: int
    argv: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    timeout_seconds: int
    network_mode: str = "none"
    secrets_allowed: bool = False
    host_shell_allowed: bool = False
    execution_allowed: bool = False
    dispatch_allowed: bool = False
    live_effect_allowed: bool = False
    raw_content_visible: bool = False

    def __post_init__(self) -> None:
        _require_sha256(self.capsule_id, field_name="capsule_id")
        _normalize_task_id(self.task_id, repo_id="capsule", objective="tool capsule")
        if isinstance(self.check_index, bool) or not isinstance(self.check_index, int) or self.check_index < 1 or self.check_index > 200:
            raise CodingAgentBackendError("check_index must be a bounded positive integer")
        if not isinstance(self.argv, tuple) or not coding_check_command_is_allowed(self.argv):
            raise CodingAgentBackendError("tool capsule argv is not allowlisted")
        if not self.allowed_paths:
            raise CodingAgentBackendError("tool capsule requires explicit allowed_paths")
        normalized_paths = _normalize_path_list(self.allowed_paths, field_name="allowed_path", allow_empty=False)
        if normalized_paths != self.allowed_paths:
            raise CodingAgentBackendError("tool capsule allowed_paths must be canonical")
        if self.network_mode != "none":
            raise CodingAgentBackendError("tool capsule network must remain none")
        if _normalize_timeout(self.timeout_seconds) != self.timeout_seconds:
            raise CodingAgentBackendError("tool capsule timeout must be canonical")
        for field_name in (
            "secrets_allowed",
            "host_shell_allowed",
            "execution_allowed",
            "dispatch_allowed",
            "live_effect_allowed",
            "raw_content_visible",
        ):
            if getattr(self, field_name) is not False:
                raise CodingAgentBackendError(f"tool capsule {field_name} must remain false")
        if self.capsule_id != _canonical_digest(self.semantic_dict()):
            raise CodingAgentBackendError("tool capsule capsule_id does not match canonical facts")

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "schema": "odysseus.coding_agent.tool_capsule.v1",
            "task_id": self.task_id,
            "check_index": self.check_index,
            "argv": self.argv,
            "allowed_paths": self.allowed_paths,
            "timeout_seconds": self.timeout_seconds,
            "network_mode": "none",
            "secrets_allowed": False,
            "host_shell_allowed": False,
            "execution_allowed": False,
            "dispatch_allowed": False,
            "live_effect_allowed": False,
            "raw_content_visible": False,
        }

    def to_dict(self) -> dict[str, Any]:
        return {"capsule_id": self.capsule_id, **self.semantic_dict()}


@dataclass(frozen=True, slots=True)
class CodingTaskPlan:
    repo_id: str
    task_id: str
    objective: str
    base_ref: str
    source_ref: str
    worktree_ref: str
    allowed_paths: tuple[str, ...]
    blocked_paths: tuple[str, ...]
    checks: tuple[CodingCheckCommand, ...]
    operator_decision: str
    live_enabled: bool
    decision: str
    blockers: tuple[str, ...]
    next_human_decision: str

    @property
    def can_create_worktree(self) -> bool:
        return self.decision == "plan_ready" and self.live_enabled and self.operator_decision == "go"

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "task_id": self.task_id,
            "objective": self.objective,
            "base_ref": self.base_ref,
            "source_ref": self.source_ref,
            "worktree_ref": self.worktree_ref,
            "allowed_paths": list(self.allowed_paths),
            "blocked_paths": list(self.blocked_paths),
            "checks": [check.to_dict() for check in self.checks],
            "operator_decision": self.operator_decision,
            "live_enabled": self.live_enabled,
            "can_create_worktree": self.can_create_worktree,
            "decision": self.decision,
            "blockers": list(self.blockers),
            "next_human_decision": self.next_human_decision,
        }


@dataclass(frozen=True, slots=True)
class CodingWorktreeReport:
    status: str
    executed: bool
    plan: CodingTaskPlan
    command_results: tuple[CodingCommandResult, ...]
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "executed": self.executed,
            "plan": self.plan.to_dict(),
            "command_results": [result.to_dict() for result in self.command_results],
            "blockers": list(self.blockers),
        }


@dataclass(frozen=True, slots=True)
class CodingTransactionalWorktreeReport:
    worktree_report: CodingWorktreeReport
    transaction: CodingWorktreeTransactionReceipt | None
    blockers: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return self.worktree_report.status == "created" and self.transaction is not None and not self.blockers

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "odysseus.coding_agent.transactional_worktree_report.v1",
            "ready": self.ready,
            "worktree_report": self.worktree_report.to_dict(),
            "transaction": self.transaction.to_dict() if self.transaction is not None else None,
            "blockers": list(self.blockers),
            "raw_content_visible": False,
        }


@dataclass(frozen=True, slots=True)
class CodingQualityGateReport:
    status: str
    changed_paths: tuple[str, ...]
    check_results: tuple[CodingCommandResult, ...]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def verified(self) -> bool:
        return self.status == "verified"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "verified": self.verified,
            "changed_paths": list(self.changed_paths),
            "check_results": [result.to_dict() for result in self.check_results],
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class CodingDoneGateReport:
    status: str
    quality_gate: CodingQualityGateReport
    review_decision: str
    reviewed_by: str
    content_reviewed: bool
    blockers: tuple[str, ...]
    implemented_by: str = ""

    @property
    def done(self) -> bool:
        return self.status == "done"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "done": self.done,
            "quality_gate": self.quality_gate.to_dict(),
            "review_decision": self.review_decision,
            "reviewed_by": self.reviewed_by,
            "implemented_by": self.implemented_by,
            "independent_review": bool(self.implemented_by and self.implemented_by != self.reviewed_by),
            "content_reviewed": self.content_reviewed,
            "blockers": list(self.blockers),
        }


@dataclass(frozen=True, slots=True)
class CodingPublishPlan:
    repo_id: str
    task_id: str
    changed_paths: tuple[str, ...]
    commit_message: str
    remote_name: str
    branch_name: str
    commit_sha: str
    commit_decision: str
    push_decision: str
    operator_gate: dict[str, Any]
    evidence_summary: dict[str, Any]
    blockers: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return self.commit_decision == "plan_ready" and self.push_decision == "plan_ready"

    @property
    def mutation_allowed(self) -> bool:
        return self.ready and bool(self.operator_gate.get("operator_go"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "task_id": self.task_id,
            "changed_paths": list(self.changed_paths),
            "commit_message": self.commit_message,
            "remote_name": self.remote_name,
            "branch_name": self.branch_name,
            "commit_sha": self.commit_sha,
            "commit_decision": self.commit_decision,
            "push_decision": self.push_decision,
            "ready": self.ready,
            "mutation_allowed": self.mutation_allowed,
            "operator_gate": dict(self.operator_gate),
            "evidence_summary": dict(self.evidence_summary),
            "blockers": list(self.blockers),
            "raw_content_visible": False,
        }


@dataclass(frozen=True, slots=True)
class CodingHandoffPlan:
    repo_id: str
    task_id: str
    source_worktree: str
    target_mode: str
    changed_paths: tuple[str, ...]
    decision: str
    blockers: tuple[str, ...]
    required_operator_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "task_id": self.task_id,
            "source_worktree": self.source_worktree,
            "target_mode": self.target_mode,
            "changed_paths": list(self.changed_paths),
            "decision": self.decision,
            "blockers": list(self.blockers),
            "required_operator_action": self.required_operator_action,
        }


@dataclass(frozen=True, slots=True)
class CodingSubagentContract:
    agent_id: str
    role: str
    slice_id: str
    objective: str
    worktree_ref: str
    allowed_paths: tuple[str, ...]
    blocked_paths: tuple[str, ...]
    checks: tuple[CodingCheckCommand, ...]
    stop_rules: tuple[str, ...]
    expected_handoff: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "role": self.role,
            "slice_id": self.slice_id,
            "objective": self.objective,
            "worktree_ref": self.worktree_ref,
            "allowed_paths": list(self.allowed_paths),
            "blocked_paths": list(self.blocked_paths),
            "checks": [check.to_dict() for check in self.checks],
            "stop_rules": list(self.stop_rules),
            "expected_handoff": list(self.expected_handoff),
        }


@dataclass(frozen=True, slots=True)
class CodingSubagentPlan:
    repo_id: str
    task_id: str
    contracts: tuple[CodingSubagentContract, ...]
    blockers: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.blockers

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "task_id": self.task_id,
            "ready": self.ready,
            "contracts": [contract.to_dict() for contract in self.contracts],
            "blockers": list(self.blockers),
        }


def coding_check_command_is_allowed(argv: tuple[str, ...]) -> bool:
    if argv == ("git", "status", "--short", "--branch"):
        return True
    if len(argv) >= 3 and argv[:3] == ("python", "-m", "pytest"):
        return all(_check_arg_is_safe(arg) for arg in argv[3:])
    if argv in {
        ("npm", "test"),
        ("npm", "run", "test"),
        ("npm", "run", "build"),
        ("pnpm", "test"),
        ("pnpm", "run", "test"),
        ("pnpm", "run", "build"),
    }:
        return True
    if len(argv) == 3 and argv[:2] == ("node", "--check"):
        return _check_arg_is_safe(argv[2])
    return False


def _check_arg_is_safe(value: str) -> bool:
    if value in {"-q", "-x", "--maxfail=1", "--disable-warnings"}:
        return True
    try:
        _normalize_repo_path(value, field_name="check_arg")
    except CodingAgentBackendError:
        return False
    return True


def build_coding_tool_capsules(plan: CodingTaskPlan) -> tuple[CodingToolCapsule, ...]:
    """Return non-executing capsules for the plan's pre-approved checks.

    The caller may render these into a sandbox request, but this function does
    not create a worker, shell, network path, or secret-bearing environment.
    """

    if not isinstance(plan, CodingTaskPlan):
        raise CodingAgentBackendError("plan must be a CodingTaskPlan")
    if not plan.allowed_paths:
        raise CodingAgentBackendError("safe tool capsules require explicit allowed_paths")
    capsules: list[CodingToolCapsule] = []
    for index, check in enumerate(plan.checks, start=1):
        if not isinstance(check, CodingCheckCommand):
            raise CodingAgentBackendError("plan checks must be CodingCheckCommand items")
        for target in _tool_capsule_path_targets(check.argv):
            if not _path_in_allowed_scope(target, plan.allowed_paths):
                raise CodingAgentBackendError("tool capsule argv target is outside allowed_paths")
        semantic = {
            "schema": "odysseus.coding_agent.tool_capsule.v1",
            "task_id": plan.task_id,
            "check_index": index,
            "argv": check.argv,
            "allowed_paths": plan.allowed_paths,
            "timeout_seconds": check.timeout_seconds,
            "network_mode": "none",
            "secrets_allowed": False,
            "host_shell_allowed": False,
            "execution_allowed": False,
            "dispatch_allowed": False,
            "live_effect_allowed": False,
            "raw_content_visible": False,
        }
        capsules.append(CodingToolCapsule(capsule_id=_canonical_digest(semantic), **{key: value for key, value in semantic.items() if key != "schema"}))
    return tuple(capsules)


def _tool_capsule_path_targets(argv: tuple[str, ...]) -> tuple[str, ...]:
    """Return only command-form-defined repository path arguments.

    The coding-check allowlist admits repo-wide npm/pnpm commands without a
    target.  Pytest targets begin after its fixed prefix, while node --check
    carries its one target at argv[2].  Treating every argv position alike
    would either miss the Node target or accidentally reinterpret flags.
    """

    if len(argv) >= 3 and argv[:3] == ("python", "-m", "pytest"):
        return tuple(item for item in argv[3:] if not item.startswith("-"))
    if len(argv) == 3 and argv[:2] == ("node", "--check"):
        return (argv[2],)
    return ()


def build_coding_task_plan(
    *,
    registry: RepoRegistry,
    repo_id: Any,
    workspace_base: str | Path,
    objective: Any,
    allowed_paths: Iterable[Any] = (),
    blocked_paths: Iterable[Any] = (),
    checks: Iterable[CodingCheckCommand] = (),
    base_ref: Any = "",
    task_id: Any = "",
    worktree_base: str | Path | None = None,
    allow_existing_worktree: bool = False,
    live_enabled: bool | None = None,
    operator_decision: Any = "missing",
    clarification_ready_for_plan: bool = True,
    clarification_id: Any = "",
) -> CodingTaskPlan:
    if not isinstance(registry, RepoRegistry):
        raise CodingAgentBackendError("registry must be a RepoRegistry")
    try:
        record = registry.get(repo_id)
    except RepoRegistryError as exc:
        raise CodingAgentBackendError(str(exc)) from exc

    repo_root = _repo_root_for_record(record, workspace_base)
    normalized_objective = _normalize_text(objective, field_name="objective")
    normalized_task_id = _normalize_task_id(task_id, repo_id=record.repo_id, objective=normalized_objective)
    normalized_allowed = _normalize_path_list(allowed_paths, field_name="allowed_path", allow_empty=True)
    normalized_blocked = tuple(sorted(set((*_HARD_BLOCKED_ROOTS, *_normalize_path_list(blocked_paths, field_name="blocked_path")))))
    normalized_base = _normalize_git_ref(
        base_ref,
        field_name="base_ref",
        fallback=record.current_branch or record.default_branch or "HEAD",
    )
    normalized_operator = _normalize_choice(operator_decision, field_name="operator_decision", choices=_OPERATOR_DECISIONS)
    resolved_live = (
        _bool_env(os.getenv("ODYSSEUS_CODING_AGENT_LIVE_ENABLED"))
        if live_enabled is None
        else bool(live_enabled)
    )
    check_tuple = tuple(checks)
    base_dir = _worktree_base(worktree_base)
    worktree_path = _resolve_child(base_dir, base_dir / record.repo_id / normalized_task_id, label="worktree path")

    blockers: list[str] = []
    if not bool(clarification_ready_for_plan):
        suffix = f" ({_normalize_text(clarification_id, field_name='clarification_id', allow_empty=True, max_len=120)})" if clarification_id else ""
        blockers.append(f"clarification must be ready_for_plan before coding plan execution{suffix}")
    if normalized_operator == "no_go":
        blockers.append("operator decision is no_go")
    if normalized_operator != "go":
        blockers.append("operator decision is not go")
    if not resolved_live:
        blockers.append("ODYSSEUS_CODING_AGENT_LIVE_ENABLED is not enabled")
    if not check_tuple:
        blockers.append("at least one quality check is required")
    if "branch" not in record.allowed_actions:
        blockers.append("repo registry does not allow branch/worktree actions")
    if worktree_path.exists() and not allow_existing_worktree:
        blockers.append("coding worktree already exists")

    decision = "blocked" if normalized_operator == "no_go" else ("hold" if blockers else "plan_ready")
    next_decision = (
        "Coding worktree may be created; editing, tests, commit, and push remain separate gated actions."
        if decision == "plan_ready"
        else "Provide live_enabled=true, operator_decision=go, branch action permission, and checks."
    )

    return CodingTaskPlan(
        repo_id=record.repo_id,
        task_id=normalized_task_id,
        objective=normalized_objective,
        base_ref=normalized_base,
        source_ref=record.path_ref,
        worktree_ref=f"coding-worktrees/{record.repo_id}/{normalized_task_id}",
        allowed_paths=normalized_allowed,
        blocked_paths=normalized_blocked,
        checks=check_tuple,
        operator_decision=normalized_operator,
        live_enabled=resolved_live,
        decision=_normalize_choice(decision, field_name="decision", choices=_DECISIONS),
        blockers=tuple(blockers),
        next_human_decision=next_decision,
    )


def build_coding_worktree_transaction_receipt(
    *,
    plan: CodingTaskPlan,
    base_revision: Any,
    source_status: Any = "",
    implementer_id: Any,
) -> CodingWorktreeTransactionReceipt:
    """Bind a clean isolated worktree to a reversible, non-authoritative receipt."""

    if not isinstance(plan, CodingTaskPlan):
        raise CodingAgentBackendError("plan must be a CodingTaskPlan")
    if not plan.allowed_paths:
        raise CodingAgentBackendError("transactional worktree requires explicit allowed_paths")
    status = str(source_status or "")
    if status.strip():
        raise CodingAgentBackendError("transactional worktree requires a clean recorded source status")
    revision = _normalize_commit_revision(base_revision, field_name="base_revision")
    implementer = _normalize_identity(implementer_id, field_name="implementer_id")
    source_status_digest = _sha256(status)
    allowed_paths_digest = _paths_binding_digest(plan.allowed_paths)
    transaction_id = _canonical_digest(
        {
            "repo_id": plan.repo_id,
            "task_id": plan.task_id,
            "worktree_ref": plan.worktree_ref,
            "base_revision": revision,
            "source_status_digest": source_status_digest,
            "allowed_paths_digest": allowed_paths_digest,
            "implementer_id": implementer,
        }
    )
    rollback_boundary = _canonical_digest(
        {
            "transaction_id": transaction_id,
            "base_revision": revision,
            "worktree_ref": plan.worktree_ref,
            "rollback": "discard_isolated_worktree_only",
        }
    )
    semantic = {
        "schema": "odysseus.coding_agent.worktree_transaction_receipt.v1",
        "transaction_id": transaction_id,
        "repo_id": plan.repo_id,
        "task_id": plan.task_id,
        "worktree_ref": plan.worktree_ref,
        "base_revision": revision,
        "source_status_digest": source_status_digest,
        "allowed_paths_digest": allowed_paths_digest,
        "rollback_boundary": rollback_boundary,
        "implementer_id": implementer,
        "execution_allowed": False,
        "write_allowed": False,
        "dispatch_allowed": False,
        "live_effect_allowed": False,
        "raw_content_visible": False,
    }
    return CodingWorktreeTransactionReceipt(
        receipt_id=_canonical_digest(semantic),
        **{key: value for key, value in semantic.items() if key != "schema"},
    )


def capture_coding_exact_read_receipt(
    *,
    plan: CodingTaskPlan,
    transaction: CodingWorktreeTransactionReceipt,
    repo_path: Any,
    reader_id: Any,
    worktree_base: str | Path | None = None,
) -> CodingExactReadReceipt:
    """Capture one hash-only exact read immediately before a strict edit."""

    if not isinstance(plan, CodingTaskPlan) or not isinstance(transaction, CodingWorktreeTransactionReceipt):
        raise CodingAgentBackendError("plan and transaction must be typed")
    _validate_transaction_matches_plan(plan=plan, transaction=transaction)
    path = _normalize_repo_path(repo_path, field_name="repo_path")
    reader = _normalize_identity(reader_id, field_name="reader_id")
    if reader != transaction.implementer_id:
        raise CodingAgentBackendError("exact read reader must be the transaction implementer")
    if _path_hits_blocked_root(path, plan.blocked_paths) or not _path_in_allowed_scope(path, plan.allowed_paths):
        raise CodingAgentBackendError("exact read path is outside transactional worktree scope")
    worktree_path = _worktree_path_from_plan(plan, worktree_base)
    target = _resolve_child(worktree_path, worktree_path / path, label="exact read path")
    if not target.is_file():
        raise CodingAgentBackendError("exact read target does not exist")
    content = target.read_bytes()
    line_count = max(1, content.count(b"\n") + (0 if content.endswith(b"\n") else 1))
    source_digest = _sha256(content)
    semantic = {
        "schema": "odysseus.coding_agent.exact_read_receipt.v1",
        "transaction_id": transaction.transaction_id,
        "worktree_receipt_id": transaction.receipt_id,
        "repo_path": path,
        "source_digest": source_digest,
        "anchor_digest": source_digest,
        "reader_id": reader,
        "line_count": line_count,
        "execution_allowed": False,
        "write_allowed": False,
        "raw_content_visible": False,
    }
    return CodingExactReadReceipt(
        receipt_id=_canonical_digest(semantic),
        **{key: value for key, value in semantic.items() if key != "schema"},
    )


def apply_coding_patch_transaction(
    *,
    plan: CodingTaskPlan,
    transaction: CodingWorktreeTransactionReceipt,
    patches: Iterable[CodingPatchOperation],
    exact_reads: Iterable[CodingExactReadReceipt],
    worktree_base: str | Path | None = None,
    live_enabled: bool | None = None,
    operator_decision: Any = "missing",
) -> CodingPatchTransactionReport:
    """Apply a fully preflighted patch set or leave the worktree unchanged.

    Legacy patch execution remains available for compatibility, while this path
    enforces the ACPR-12 receipt, changed-path, rollback and review handoff
    prerequisites before any file is written.
    """

    if not isinstance(plan, CodingTaskPlan) or not isinstance(transaction, CodingWorktreeTransactionReceipt):
        raise CodingAgentBackendError("plan and transaction must be typed")
    _validate_transaction_matches_plan(plan=plan, transaction=transaction)
    patch_tuple = tuple(patches)
    receipt_tuple = tuple(exact_reads)
    if not patch_tuple or not all(isinstance(item, CodingPatchOperation) for item in patch_tuple):
        raise CodingAgentBackendError("transactional patches must contain CodingPatchOperation items")
    if not receipt_tuple or not all(isinstance(item, CodingExactReadReceipt) for item in receipt_tuple):
        raise CodingAgentBackendError("transactional exact_reads must contain CodingExactReadReceipt items")
    duplicate_paths = _duplicate_patch_paths(patch_tuple)
    if duplicate_paths:
        return _transaction_report(
            transaction,
            status="blocked",
            blockers=(f"patch transaction contains duplicate paths: {', '.join(duplicate_paths)}",),
        )
    receipt_by_path = {receipt.repo_path: receipt for receipt in receipt_tuple}
    if len(receipt_by_path) != len(receipt_tuple) or set(receipt_by_path) != {patch.path for patch in patch_tuple}:
        return _transaction_report(transaction, status="blocked", blockers=("exact read receipt coverage does not match patch paths",))
    normalized_operator = _normalize_choice(operator_decision, field_name="operator_decision", choices=_OPERATOR_DECISIONS)
    resolved_live = _bool_env(os.getenv("ODYSSEUS_CODING_AGENT_PATCH_LIVE_ENABLED")) if live_enabled is None else bool(live_enabled)
    if normalized_operator != "go" or not resolved_live:
        return _transaction_report(transaction, status="blocked", blockers=("patch execution requires live_enabled=true and operator_decision=go",))
    worktree_path = _worktree_path_from_plan(plan, worktree_base)
    if not worktree_path.is_dir():
        raise CodingAgentBackendError("coding worktree does not exist")

    prepared: list[tuple[CodingPatchOperation, CodingExactReadReceipt, Path, bytes, bytes, str]] = []
    blockers: list[str] = []
    for patch in patch_tuple:
        receipt = receipt_by_path[patch.path]
        if (
            receipt.transaction_id != transaction.transaction_id
            or receipt.worktree_receipt_id != transaction.receipt_id
            or receipt.reader_id != transaction.implementer_id
        ):
            blockers.append(f"exact read receipt binding mismatch: {patch.path}")
            continue
        if _path_hits_blocked_root(patch.path, plan.blocked_paths) or not _path_in_allowed_scope(patch.path, plan.allowed_paths):
            blockers.append(f"patch path is outside transactional worktree scope: {patch.path}")
            continue
        target = _resolve_child(worktree_path, worktree_path / patch.path, label="patch path")
        if not target.is_file():
            blockers.append(f"patch target does not exist: {patch.path}")
            continue
        before_bytes = target.read_bytes()
        if _sha256(before_bytes) != receipt.source_digest or receipt.anchor_digest != receipt.source_digest:
            blockers.append(f"exact read receipt is stale: {patch.path}")
            continue
        before = before_bytes.decode("utf-8")
        replacements = before.count(patch.find)
        if replacements != patch.expected_replacements:
            blockers.append(f"expected {patch.expected_replacements} replacement(s), found {replacements}: {patch.path}")
            continue
        after = before.replace(patch.find, patch.replace, patch.expected_replacements)
        after_bytes = after.encode("utf-8")
        diff = "".join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), fromfile=f"a/{patch.path}", tofile=f"b/{patch.path}"))
        prepared.append((patch, receipt, target, before_bytes, after_bytes, diff))
    if blockers:
        return _transaction_report(transaction, status="blocked", blockers=tuple(dict.fromkeys(blockers)))

    written: list[tuple[Path, bytes]] = []
    try:
        for _patch, _receipt, target, before_bytes, after_bytes, _diff in prepared:
            written.append((target, before_bytes))
            target.write_bytes(after_bytes)
    except OSError:
        rollback_failures = []
        for target, before_bytes in reversed(written):
            try:
                target.write_bytes(before_bytes)
            except OSError:
                rollback_failures.append(target.name)
        code = "patch transaction failed and rollback could not restore every file" if rollback_failures else "patch transaction failed and was rolled back"
        return _transaction_report(transaction, status="rolled_back" if not rollback_failures else "blocked", blockers=(code,))

    changed = tuple(sorted(item[0].path for item in prepared))
    diff_digest = _sha256("".join(item[5] for item in prepared))
    return CodingPatchTransactionReport(
        transaction_id=transaction.transaction_id,
        worktree_receipt_id=transaction.receipt_id,
        status="applied",
        changed_paths=changed,
        exact_read_receipt_ids=tuple(sorted(item[1].receipt_id for item in prepared)),
        diff_digest=diff_digest,
        rollback_boundary=transaction.rollback_boundary,
        rollback_ready=True,
    )


def _validate_transaction_matches_plan(*, plan: CodingTaskPlan, transaction: CodingWorktreeTransactionReceipt) -> None:
    if transaction.repo_id != plan.repo_id or transaction.task_id != plan.task_id or transaction.worktree_ref != plan.worktree_ref:
        raise CodingAgentBackendError("worktree transaction does not match coding plan")
    if transaction.allowed_paths_digest != _paths_binding_digest(plan.allowed_paths):
        raise CodingAgentBackendError("worktree transaction allowed paths do not match coding plan")


def _duplicate_patch_paths(patches: Iterable[CodingPatchOperation]) -> tuple[str, ...]:
    counts: dict[str, int] = {}
    for patch in patches:
        counts[patch.path] = counts.get(patch.path, 0) + 1
    return tuple(sorted(path for path, count in counts.items() if count > 1))


def _transaction_report(
    transaction: CodingWorktreeTransactionReceipt,
    *,
    status: str,
    blockers: tuple[str, ...],
) -> CodingPatchTransactionReport:
    return CodingPatchTransactionReport(
        transaction_id=transaction.transaction_id,
        worktree_receipt_id=transaction.receipt_id,
        status=status,
        changed_paths=(),
        exact_read_receipt_ids=(),
        diff_digest=_sha256(""),
        rollback_boundary=transaction.rollback_boundary,
        rollback_ready=True,
        blockers=blockers,
    )


def collect_coding_worktree_changed_paths(
    *,
    plan: CodingTaskPlan,
    worktree_base: str | Path | None = None,
    command_runner: CodingCommandRunner | None = None,
) -> tuple[tuple[str, ...], tuple[CodingCommandResult, ...]]:
    if not isinstance(plan, CodingTaskPlan):
        raise CodingAgentBackendError("plan must be a CodingTaskPlan")
    worktree_path = _worktree_path_from_plan(plan, worktree_base)
    if not worktree_path.is_dir() or not (worktree_path / ".git").exists():
        raise CodingAgentBackendError("coding worktree does not exist")
    runner = command_runner or run_coding_subprocess_command
    result = runner(("git", "status", "--porcelain"), cwd=worktree_path, timeout_seconds=30, env={})
    if not result.ok:
        return (), (result,)
    paths: list[str] = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        raw_path = line[3:].strip()
        if " -> " in raw_path:
            raw_path = raw_path.rsplit(" -> ", 1)[-1].strip()
        if not raw_path:
            continue
        paths.append(_normalize_repo_path(raw_path.strip('"'), field_name="changed_path", allow_blocked=True))
    return tuple(sorted(dict.fromkeys(paths))), (result,)


def run_coding_checks(
    *,
    plan: CodingTaskPlan,
    worktree_base: str | Path | None = None,
    command_runner: CodingCommandRunner | None = None,
) -> tuple[CodingCommandResult, ...]:
    if not isinstance(plan, CodingTaskPlan):
        raise CodingAgentBackendError("plan must be a CodingTaskPlan")
    worktree_path = _worktree_path_from_plan(plan, worktree_base)
    if not worktree_path.is_dir() or not (worktree_path / ".git").exists():
        raise CodingAgentBackendError("coding worktree does not exist")
    runner = command_runner or run_coding_subprocess_command
    results: list[CodingCommandResult] = []
    for check in plan.checks:
        results.append(
            runner(
                check.argv,
                cwd=worktree_path,
                timeout_seconds=check.timeout_seconds,
                env={},
            )
        )
    return tuple(results)


def evaluate_coding_worktree_gate(
    *,
    plan: CodingTaskPlan,
    worktree_base: str | Path | None = None,
    command_runner: CodingCommandRunner | None = None,
) -> CodingQualityGateReport:
    changed, status_results = collect_coding_worktree_changed_paths(
        plan=plan,
        worktree_base=worktree_base,
        command_runner=command_runner,
    )
    if status_results and not status_results[0].ok:
        return CodingQualityGateReport(
            status="blocked",
            changed_paths=(),
            check_results=status_results,
            blockers=("could not inspect coding worktree status",),
            warnings=(),
        )
    check_results = run_coding_checks(
        plan=plan,
        worktree_base=worktree_base,
        command_runner=command_runner,
    )
    return evaluate_coding_quality_gate(
        changed_paths=changed,
        allowed_paths=plan.allowed_paths,
        blocked_paths=plan.blocked_paths,
        check_results=check_results,
    )


def create_coding_worktree(
    *,
    registry: RepoRegistry,
    repo_id: Any,
    workspace_base: str | Path,
    objective: Any,
    allowed_paths: Iterable[Any] = (),
    blocked_paths: Iterable[Any] = (),
    checks: Iterable[CodingCheckCommand] = (),
    base_ref: Any = "",
    task_id: Any = "",
    worktree_base: str | Path | None = None,
    live_enabled: bool | None = None,
    operator_decision: Any = "missing",
    clarification_ready_for_plan: bool = True,
    clarification_id: Any = "",
    command_runner: CodingCommandRunner | None = None,
) -> CodingWorktreeReport:
    plan = build_coding_task_plan(
        registry=registry,
        repo_id=repo_id,
        workspace_base=workspace_base,
        objective=objective,
        allowed_paths=allowed_paths,
        blocked_paths=blocked_paths,
        checks=checks,
        base_ref=base_ref,
        task_id=task_id,
        worktree_base=worktree_base,
        live_enabled=live_enabled,
        operator_decision=operator_decision,
        clarification_ready_for_plan=clarification_ready_for_plan,
        clarification_id=clarification_id,
    )
    if not plan.can_create_worktree:
        return CodingWorktreeReport(
            status="blocked",
            executed=False,
            plan=plan,
            command_results=(),
            blockers=plan.blockers,
        )

    record = registry.get(repo_id)
    repo_root = _repo_root_for_record(record, workspace_base)
    base_dir = _worktree_base(worktree_base)
    worktree_path = _resolve_child(base_dir, base_dir / plan.repo_id / plan.task_id, label="worktree path")
    runner = command_runner or run_coding_subprocess_command

    dirty = runner(("git", "status", "--porcelain"), cwd=repo_root, timeout_seconds=30, env={})
    if not dirty.ok:
        return CodingWorktreeReport(
            status="blocked",
            executed=True,
            plan=plan,
            command_results=(dirty,),
            blockers=("could not inspect source repo status",),
        )
    if dirty.stdout.strip():
        return CodingWorktreeReport(
            status="blocked",
            executed=True,
            plan=plan,
            command_results=(dirty,),
            blockers=("source repo has uncommitted changes; create a clean base or explicitly hand off first",),
        )

    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    created = runner(
        ("git", "worktree", "add", "--detach", str(worktree_path), plan.base_ref),
        cwd=repo_root,
        timeout_seconds=120,
        env={},
    )
    if not created.ok:
        return CodingWorktreeReport(
            status="blocked",
            executed=True,
            plan=plan,
            command_results=(dirty, created),
            blockers=("git worktree creation failed",),
        )
    inspected = runner(("git", "status", "--porcelain"), cwd=worktree_path, timeout_seconds=30, env={})
    status = "created" if inspected.ok else "blocked"
    blockers = () if inspected.ok else ("created worktree could not be inspected",)
    return CodingWorktreeReport(
        status=status,
        executed=True,
        plan=plan,
        command_results=(dirty, created, inspected),
        blockers=blockers,
    )


def create_transactional_coding_worktree(
    *,
    registry: RepoRegistry,
    repo_id: Any,
    workspace_base: str | Path,
    objective: Any,
    implementer_id: Any,
    allowed_paths: Iterable[Any] = (),
    blocked_paths: Iterable[Any] = (),
    checks: Iterable[CodingCheckCommand] = (),
    base_ref: Any = "",
    task_id: Any = "",
    worktree_base: str | Path | None = None,
    live_enabled: bool | None = None,
    operator_decision: Any = "missing",
    clarification_ready_for_plan: bool = True,
    clarification_id: Any = "",
    command_runner: CodingCommandRunner | None = None,
) -> CodingTransactionalWorktreeReport:
    """Create a normal gated worktree, then record its exact rollback boundary."""

    report = create_coding_worktree(
        registry=registry,
        repo_id=repo_id,
        workspace_base=workspace_base,
        objective=objective,
        allowed_paths=allowed_paths,
        blocked_paths=blocked_paths,
        checks=checks,
        base_ref=base_ref,
        task_id=task_id,
        worktree_base=worktree_base,
        live_enabled=live_enabled,
        operator_decision=operator_decision,
        clarification_ready_for_plan=clarification_ready_for_plan,
        clarification_id=clarification_id,
        command_runner=command_runner,
    )
    if report.status != "created":
        return CodingTransactionalWorktreeReport(worktree_report=report, transaction=None, blockers=report.blockers)
    runner = command_runner or run_coding_subprocess_command
    worktree_path = _worktree_path_from_plan(report.plan, worktree_base)
    revision = runner(("git", "rev-parse", "HEAD"), cwd=worktree_path, timeout_seconds=30, env={})
    status = runner(("git", "status", "--porcelain"), cwd=worktree_path, timeout_seconds=30, env={})
    if not revision.ok or not status.ok:
        return CodingTransactionalWorktreeReport(
            worktree_report=report,
            transaction=None,
            blockers=("transactional worktree boundary could not be inspected",),
        )
    try:
        transaction = build_coding_worktree_transaction_receipt(
            plan=report.plan,
            base_revision=revision.stdout.strip(),
            source_status=status.stdout,
            implementer_id=implementer_id,
        )
    except CodingAgentBackendError as exc:
        return CodingTransactionalWorktreeReport(worktree_report=report, transaction=None, blockers=(str(exc),))
    return CodingTransactionalWorktreeReport(worktree_report=report, transaction=transaction, blockers=())


def apply_coding_patch_set(
    *,
    plan: CodingTaskPlan,
    patches: Iterable[CodingPatchOperation],
    worktree_base: str | Path | None = None,
    live_enabled: bool | None = None,
    operator_decision: Any = "missing",
) -> tuple[CodingPatchResult, ...]:
    if not isinstance(plan, CodingTaskPlan):
        raise CodingAgentBackendError("plan must be a CodingTaskPlan")
    normalized_operator = _normalize_choice(operator_decision, field_name="operator_decision", choices=_OPERATOR_DECISIONS)
    resolved_live = (
        _bool_env(os.getenv("ODYSSEUS_CODING_AGENT_PATCH_LIVE_ENABLED"))
        if live_enabled is None
        else bool(live_enabled)
    )
    if normalized_operator != "go" or not resolved_live:
        blocker = "patch execution requires live_enabled=true and operator_decision=go"
        return tuple(
            CodingPatchResult(operation=patch, status="blocked", replacements=0, diff="", blocker=blocker)
            for patch in patches
        )

    worktree_path = _worktree_path_from_plan(plan, worktree_base)
    if not worktree_path.is_dir():
        raise CodingAgentBackendError("coding worktree does not exist")

    patch_tuple = tuple(patches)
    for patch in patch_tuple:
        if not isinstance(patch, CodingPatchOperation):
            raise CodingAgentBackendError("patches must contain CodingPatchOperation items")
    duplicate_paths = _duplicate_patch_paths(patch_tuple)
    if duplicate_paths:
        blocker = f"patch transaction contains duplicate paths: {', '.join(duplicate_paths)}"
        return tuple(
            CodingPatchResult(operation=patch, status="blocked", replacements=0, diff="", blocker=blocker)
            for patch in patch_tuple
        )

    prepared: list[tuple[CodingPatchOperation, Path, bytes, bytes, str, int]] = []
    blockers: list[tuple[CodingPatchOperation, str, int]] = []
    for patch in patch_tuple:
        if _path_hits_blocked_root(patch.path, plan.blocked_paths):
            blockers.append((patch, f"patch path is blocked: {patch.path}", 0))
            continue
        if not _path_in_allowed_scope(patch.path, plan.allowed_paths):
            blockers.append((patch, f"patch path is outside allowed scope: {patch.path}", 0))
            continue
        target = _resolve_child(worktree_path, worktree_path / patch.path, label="patch path")
        if not target.is_file():
            blockers.append((patch, f"patch target does not exist: {patch.path}", 0))
            continue
        before_bytes = target.read_bytes()
        before = before_bytes.decode("utf-8")
        count = before.count(patch.find)
        if count != patch.expected_replacements:
            blockers.append((patch, f"expected {patch.expected_replacements} replacement(s), found {count}: {patch.path}", count))
            continue
        after = before.replace(patch.find, patch.replace, patch.expected_replacements)
        after_bytes = after.encode("utf-8")
        diff = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{patch.path}",
                tofile=f"b/{patch.path}",
            )
        )
        prepared.append((patch, target, before_bytes, after_bytes, diff, count))

    if blockers:
        details = {patch.path: (blocker, count) for patch, blocker, count in blockers}
        return tuple(
            CodingPatchResult(
                operation=patch,
                status="blocked",
                replacements=details.get(patch.path, ("patch transaction preflight failed", 0))[1],
                diff="",
                blocker=details.get(patch.path, ("patch transaction preflight failed", 0))[0],
            )
            for patch in patch_tuple
        )

    written: list[tuple[Path, bytes]] = []
    try:
        for _patch, target, before_bytes, after_bytes, _diff, _count in prepared:
            written.append((target, before_bytes))
            target.write_bytes(after_bytes)
    except OSError:
        rollback_failed = False
        for target, before_bytes in reversed(written):
            try:
                target.write_bytes(before_bytes)
            except OSError:
                rollback_failed = True
        blocker = "patch transaction failed and rollback could not restore every file" if rollback_failed else "patch transaction failed and was rolled back"
        return tuple(
            CodingPatchResult(operation=patch, status="blocked", replacements=0, diff="", blocker=blocker)
            for patch in patch_tuple
        )
    return tuple(
        CodingPatchResult(operation=patch, status="applied", replacements=count, diff=diff)
        for patch, _target, _before_bytes, _after_bytes, diff, count in prepared
    )


def evaluate_coding_quality_gate(
    *,
    changed_paths: Iterable[Any],
    check_results: Iterable[CodingCommandResult],
    allowed_paths: Iterable[Any] = (),
    blocked_paths: Iterable[Any] = (),
) -> CodingQualityGateReport:
    changed = _normalize_path_list(
        changed_paths,
        field_name="changed_path",
        allow_empty=True,
        allow_blocked=True,
    )
    allowed = _normalize_path_list(allowed_paths, field_name="allowed_path", allow_empty=True)
    blocked = tuple(
        sorted(
            set(
                (
                    *_HARD_BLOCKED_ROOTS,
                    *_normalize_path_list(
                        blocked_paths,
                        field_name="blocked_path",
                        allow_blocked=True,
                    ),
                )
            )
        )
    )
    results = tuple(check_results)
    blockers: list[str] = []
    warnings: list[str] = []

    if not changed:
        blockers.append("no changed paths were provided")
    for path in changed:
        if _path_hits_blocked_root(path, blocked):
            blockers.append(f"blocked path changed: {path}")
        if not _path_in_allowed_scope(path, allowed):
            blockers.append(f"changed path is outside allowed scope: {path}")
    if not results:
        blockers.append("no quality check results were provided")
    for result in results:
        if not isinstance(result, CodingCommandResult):
            raise CodingAgentBackendError("check_results must contain CodingCommandResult items")
        if not result.ok:
            blockers.append("one or more quality checks failed")
            break
    if len(changed) > 50:
        warnings.append("large change set; require reviewer attention before commit")

    return CodingQualityGateReport(
        status="blocked" if blockers else "verified",
        changed_paths=changed,
        check_results=results,
        blockers=tuple(dict.fromkeys(blockers)),
        warnings=tuple(warnings),
    )


def evaluate_coding_done_gate(
    *,
    quality_gate: CodingQualityGateReport,
    review_decision: Any = "missing",
    reviewed_by: Any = "",
    implemented_by: Any = "",
    content_reviewed: Any = False,
) -> CodingDoneGateReport:
    if not isinstance(quality_gate, CodingQualityGateReport):
        raise CodingAgentBackendError("quality_gate must be a CodingQualityGateReport")
    review = _normalize_choice(review_decision, field_name="review_decision", choices=_REVIEW_DECISIONS)
    reviewer = _normalize_text(reviewed_by, field_name="reviewed_by", allow_empty=True, max_len=80)
    implementer = _normalize_identity(implemented_by, field_name="implemented_by", allow_empty=True)
    reviewed = _normalize_bool(content_reviewed, field_name="content_reviewed")
    blockers: list[str] = []
    if not quality_gate.verified:
        blockers.extend(quality_gate.blockers or ("quality gate is not verified",))
    if review != "approved":
        blockers.append("review decision is not approved")
    if not reviewer:
        blockers.append("reviewer identity is required")
    if implementer and reviewer == implementer:
        blockers.append("reviewer identity must differ from implementer")
    if not reviewed:
        blockers.append("content_reviewed must be true")
    return CodingDoneGateReport(
        status="blocked" if blockers else "done",
        quality_gate=quality_gate,
        review_decision=review,
        reviewed_by=reviewer,
        content_reviewed=reviewed,
        blockers=tuple(dict.fromkeys(blockers)),
        implemented_by=implementer,
    )


def build_coding_handoff_plan(
    *,
    plan: CodingTaskPlan,
    done_gate: CodingDoneGateReport,
    changed_paths: Iterable[Any],
    target_mode: Any = "local",
) -> CodingHandoffPlan:
    if not isinstance(plan, CodingTaskPlan):
        raise CodingAgentBackendError("plan must be a CodingTaskPlan")
    if not isinstance(done_gate, CodingDoneGateReport):
        raise CodingAgentBackendError("done_gate must be a CodingDoneGateReport")
    target = _normalize_choice(target_mode, field_name="target_mode", choices=("local", "branch", "archive"))
    changed = _normalize_path_list(changed_paths, field_name="changed_path", allow_empty=False, allow_blocked=True)
    blockers: list[str] = []
    if not done_gate.done:
        blockers.extend(done_gate.blockers or ("done gate is not complete",))
    for path in changed:
        if _path_hits_blocked_root(path, plan.blocked_paths):
            blockers.append(f"blocked path changed: {path}")
        if not _path_in_allowed_scope(path, plan.allowed_paths):
            blockers.append(f"changed path is outside allowed scope: {path}")
    action = (
        "Apply the reviewed worktree diff through the operator-controlled Git handoff flow."
        if target in {"local", "branch"}
        else "Archive or discard the worktree after preserving the review summary."
    )
    return CodingHandoffPlan(
        repo_id=plan.repo_id,
        task_id=plan.task_id,
        source_worktree=plan.worktree_ref,
        target_mode=target,
        changed_paths=changed,
        decision="blocked" if blockers else "plan_ready",
        blockers=tuple(dict.fromkeys(blockers)),
        required_operator_action=action,
    )


def build_transactional_coding_handoff_plan(
    *,
    plan: CodingTaskPlan,
    transaction: CodingWorktreeTransactionReceipt,
    patch_report: CodingPatchTransactionReport,
    done_gate: CodingDoneGateReport,
    target_mode: Any = "local",
) -> CodingHandoffPlan:
    """Build a review-only handoff after an independently reviewed transaction."""

    if not isinstance(transaction, CodingWorktreeTransactionReceipt):
        raise CodingAgentBackendError("transaction must be a CodingWorktreeTransactionReceipt")
    if not isinstance(patch_report, CodingPatchTransactionReport):
        raise CodingAgentBackendError("patch_report must be a CodingPatchTransactionReport")
    _validate_transaction_matches_plan(plan=plan, transaction=transaction)
    blockers: list[str] = []
    if patch_report.transaction_id != transaction.transaction_id or patch_report.worktree_receipt_id != transaction.receipt_id:
        blockers.append("patch report is not bound to the worktree transaction")
    if not patch_report.applied:
        blockers.extend(patch_report.blockers or ("patch transaction was not applied",))
    if not patch_report.rollback_ready or patch_report.rollback_boundary != transaction.rollback_boundary:
        blockers.append("patch transaction rollback boundary is unavailable")
    if done_gate.implemented_by != transaction.implementer_id:
        blockers.append("done gate implementer identity is not bound to transaction")
    if done_gate.reviewed_by == transaction.implementer_id:
        blockers.append("reviewer identity must differ from implementer")
    if blockers:
        return CodingHandoffPlan(
            repo_id=plan.repo_id,
            task_id=plan.task_id,
            source_worktree=plan.worktree_ref,
            target_mode=_normalize_choice(target_mode, field_name="target_mode", choices=("local", "branch", "archive")),
            changed_paths=patch_report.changed_paths,
            decision="blocked",
            blockers=tuple(dict.fromkeys(blockers)),
            required_operator_action="Keep the isolated worktree unchanged until receipt binding and independent review are complete.",
        )
    return build_coding_handoff_plan(
        plan=plan,
        done_gate=done_gate,
        changed_paths=patch_report.changed_paths,
        target_mode=target_mode,
    )


def build_coding_publish_plan(
    *,
    plan: CodingTaskPlan,
    done_gate: CodingDoneGateReport,
    changed_paths: Iterable[Any],
    commit_message: Any,
    remote_name: Any = "fuzzy",
    branch_name: Any = "",
    commit_sha: Any = "",
    commit_confirmed: Any = False,
    push_confirmed: Any = False,
    operator_go: Any = False,
) -> CodingPublishPlan:
    if not isinstance(plan, CodingTaskPlan):
        raise CodingAgentBackendError("plan must be a CodingTaskPlan")
    if not isinstance(done_gate, CodingDoneGateReport):
        raise CodingAgentBackendError("done_gate must be a CodingDoneGateReport")
    changed = _normalize_path_list(changed_paths, field_name="changed_path", allow_empty=False, allow_blocked=True)
    message = _normalize_text(commit_message, field_name="commit_message", max_len=180)
    remote = _normalize_text(remote_name, field_name="remote_name", max_len=80)
    branch = _normalize_git_ref(branch_name or f"codex/{plan.repo_id}-{plan.task_id}", field_name="branch_name")
    sha = _normalize_text(commit_sha, field_name="commit_sha", allow_empty=True, max_len=40)
    blockers: list[str] = []
    if not done_gate.done:
        blockers.extend(done_gate.blockers or ("done gate is not complete",))
    if not _normalize_bool(commit_confirmed, field_name="commit_confirmed"):
        blockers.append("commit_confirmed must be true")
    if not _normalize_bool(push_confirmed, field_name="push_confirmed"):
        blockers.append("push_confirmed must be true")
    if not _normalize_bool(operator_go, field_name="operator_go"):
        blockers.append("operator_go must be true")
    if remote != "fuzzy":
        blockers.append("push remote must be fuzzy")
    if sha and not re.fullmatch(r"[0-9a-fA-F]{7,40}", sha):
        blockers.append("commit_sha must be a 7-40 char hex ref when provided")
    for path in changed:
        if _path_hits_blocked_root(path, plan.blocked_paths):
            blockers.append(f"blocked path changed: {path}")
        if not _path_in_allowed_scope(path, plan.allowed_paths):
            blockers.append(f"changed path is outside allowed scope: {path}")
    decision = "blocked" if blockers else "plan_ready"
    return CodingPublishPlan(
        repo_id=plan.repo_id,
        task_id=plan.task_id,
        changed_paths=changed,
        commit_message=message,
        remote_name=remote,
        branch_name=branch,
        commit_sha=sha,
        commit_decision=decision,
        push_decision=decision,
        operator_gate={
            "commit_confirmed": bool(commit_confirmed),
            "push_confirmed": bool(push_confirmed),
            "operator_go": bool(operator_go),
            "remote_name": remote,
            "branch_name": branch,
            "requires_separate_review": True,
            "mutation_allowed": decision == "plan_ready",
            "raw_content_visible": False,
        },
        evidence_summary={
            "changed_path_count": len(changed),
            "quality_gate_verified": done_gate.quality_gate.verified,
            "done_gate_complete": done_gate.done,
            "review_decision": done_gate.review_decision,
            "reviewed_by_present": bool(done_gate.reviewed_by),
            "commit_sha_present": bool(sha),
            "raw_content_visible": False,
        },
        blockers=tuple(dict.fromkeys(blockers)),
    )


def build_coding_subagent_plan(
    *,
    plan: CodingTaskPlan,
    worker_agent_id: Any = "bob",
    reviewer_agent_id: Any = "charlie",
) -> CodingSubagentPlan:
    if not isinstance(plan, CodingTaskPlan):
        raise CodingAgentBackendError("plan must be a CodingTaskPlan")
    blockers: list[str] = []
    if not plan.allowed_paths:
        blockers.append("subagent contracts require explicit allowed_paths")
    if not plan.checks:
        blockers.append("subagent contracts require explicit checks")
    stop_rules = (
        "Do not touch files outside allowed_paths.",
        "Do not touch blocked_paths, secrets, tokens, private data, or UI/design files.",
        "Stop on red checks unless the fix stays inside allowed_paths.",
        "Do not commit, push, reset, clean, merge, or delete without a separate publish gate.",
        "Do not revert unrelated changes from other agents or the operator.",
    )
    handoff = (
        "status",
        "changed_paths",
        "tests",
        "diff_summary",
        "risks",
        "next_action",
    )
    worker = CodingSubagentContract(
        agent_id=_normalize_text(worker_agent_id, field_name="worker_agent_id", max_len=80),
        role="worker",
        slice_id=f"{plan.task_id}-worker",
        objective=plan.objective,
        worktree_ref=plan.worktree_ref,
        allowed_paths=plan.allowed_paths,
        blocked_paths=plan.blocked_paths,
        checks=plan.checks,
        stop_rules=stop_rules,
        expected_handoff=handoff,
    )
    reviewer = CodingSubagentContract(
        agent_id=_normalize_text(reviewer_agent_id, field_name="reviewer_agent_id", max_len=80),
        role="reviewer",
        slice_id=f"{plan.task_id}-reviewer",
        objective=f"Review coding task {plan.task_id} for scope, tests, diff risk, and done gate readiness.",
        worktree_ref=plan.worktree_ref,
        allowed_paths=plan.allowed_paths,
        blocked_paths=plan.blocked_paths,
        checks=plan.checks,
        stop_rules=stop_rules,
        expected_handoff=("review_decision", *handoff),
    )
    return CodingSubagentPlan(
        repo_id=plan.repo_id,
        task_id=plan.task_id,
        contracts=(worker, reviewer),
        blockers=tuple(blockers),
    )


def run_coding_subprocess_command(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    timeout_seconds: int,
    env: Mapping[str, str],
) -> CodingCommandResult:
    if not _coding_internal_command_is_allowed(argv):
        raise CodingAgentBackendError("unsupported coding backend command")
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
        return CodingCommandResult(
            exit_code=124,
            stdout=str(exc.stdout or ""),
            stderr=str(exc.stderr or "command timed out"),
            timed_out=True,
            duration_seconds=round(time.monotonic() - started, 3),
        )
    return CodingCommandResult(
        exit_code=int(completed.returncode),
        stdout=str(completed.stdout or ""),
        stderr=str(completed.stderr or ""),
        timed_out=False,
        duration_seconds=round(time.monotonic() - started, 3),
    )


def _coding_internal_command_is_allowed(argv: tuple[str, ...]) -> bool:
    if argv == ("git", "status", "--porcelain"):
        return True
    if argv == ("git", "rev-parse", "HEAD"):
        return True
    if len(argv) == 6 and argv[:4] == ("git", "worktree", "add", "--detach"):
        target = argv[4]
        ref = argv[5]
        return bool(target and _REF_RE.fullmatch(ref) and not ref.startswith("-"))
    if coding_check_command_is_allowed(argv):
        return True
    return False


def repo_git_snapshot_for_coding_task(
    *,
    registry: RepoRegistry,
    repo_id: Any,
    workspace_base: str | Path,
) -> dict[str, Any]:
    try:
        from src.repo_git_adapter import RepoGitAdapter

        return RepoGitAdapter(registry=registry, workspace_base=workspace_base).snapshot(repo_id).to_dict()
    except (RepoRegistryError, RepoGitAdapterError) as exc:
        raise CodingAgentBackendError(str(exc)) from exc
