"""Offline preflight validator for the Odysseus updater module."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Mapping

_REPORT_STATUSES = ("ready", "partial", "blocked", "deferred")


def _snapshot_to_mapping(snapshot: Any, *, field_name: str) -> Mapping[str, Any]:
    if isinstance(snapshot, Mapping):
        return snapshot
    if is_dataclass(snapshot):
        return asdict(snapshot)
    raise ValueError(f"{field_name} must be a mapping or dataclass")


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_optional_text(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    text = _normalize_text(value, field_name=field_name, allow_empty=True)
    return text or None


def _normalize_bool(value: Any, *, field_name: str) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field_name} must be a bool when provided")


def _normalize_nonnegative_int(value: Any, *, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative int when provided")
    return value


def _normalize_name_tuple(values: Any, *, field_name: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if not isinstance(values, (list, tuple, set)):
        raise ValueError(f"{field_name} must be an iterable of names")
    names: list[str] = []
    for value in values:
        normalized = _normalize_text(value, field_name=field_name)
        if normalized not in names:
            names.append(normalized)
    return tuple(names)


def _normalize_path_tuple(values: Any, *, field_name: str) -> tuple[str, ...]:
    return _normalize_name_tuple(values, field_name=field_name)


def _normalize_status(value: Any) -> str:
    status = _normalize_text(value, field_name="status").lower()
    if status not in _REPORT_STATUSES:
        raise ValueError("unsupported preflight report status")
    return status


def _coalesce(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


@dataclass(frozen=True, slots=True)
class WorktreeSnapshot:
    dirty: bool | None = None
    staged_files: tuple[str, ...] = ()
    allowed_staged_files: tuple[str, ...] = ()
    hotfile_conflict: bool | None = None

    @classmethod
    def create(cls, snapshot: Any) -> "WorktreeSnapshot":
        data = _snapshot_to_mapping(snapshot, field_name="worktree_snapshot")
        return cls(
            dirty=_normalize_bool(_coalesce(data, "dirty", "is_dirty"), field_name="dirty"),
            staged_files=_normalize_path_tuple(
                _coalesce(data, "staged_files", "staged"), field_name="staged_files"
            ),
            allowed_staged_files=_normalize_path_tuple(
                _coalesce(data, "allowed_staged_files", "allowed_files"),
                field_name="allowed_staged_files",
            ),
            hotfile_conflict=_normalize_bool(
                _coalesce(data, "hotfile_conflict", "has_hotfile_conflict"),
                field_name="hotfile_conflict",
            ),
        )

    @property
    def foreign_staged_files(self) -> tuple[str, ...]:
        if not self.allowed_staged_files:
            return ()
        allowed = set(self.allowed_staged_files)
        return tuple(path for path in self.staged_files if path not in allowed)


@dataclass(frozen=True, slots=True)
class BranchSnapshot:
    current_branch: str | None = None
    expected_branch: str | None = None
    branch_candidates: tuple[str, ...] = ()
    detached: bool | None = None
    ahead: int | None = None
    behind: int | None = None

    @classmethod
    def create(cls, snapshot: Any) -> "BranchSnapshot":
        data = _snapshot_to_mapping(snapshot, field_name="branch_snapshot")
        return cls(
            current_branch=_normalize_optional_text(
                _coalesce(data, "current_branch", "branch"), field_name="current_branch"
            ),
            expected_branch=_normalize_optional_text(
                _coalesce(data, "expected_branch", "target_branch"),
                field_name="expected_branch",
            ),
            branch_candidates=_normalize_name_tuple(
                _coalesce(data, "branch_candidates", "candidates"),
                field_name="branch_candidates",
            ),
            detached=_normalize_bool(_coalesce(data, "detached"), field_name="detached"),
            ahead=_normalize_nonnegative_int(_coalesce(data, "ahead"), field_name="ahead"),
            behind=_normalize_nonnegative_int(_coalesce(data, "behind"), field_name="behind"),
        )


@dataclass(frozen=True, slots=True)
class EnvSnapshot:
    required_names: tuple[str, ...]
    present_names: tuple[str, ...]

    @classmethod
    def create(cls, snapshot: Any) -> "EnvSnapshot":
        data = _snapshot_to_mapping(snapshot, field_name="env_snapshot")
        return cls(
            required_names=_normalize_name_tuple(
                _coalesce(data, "required_names", "required_env_names"),
                field_name="required_names",
            ),
            present_names=_normalize_name_tuple(
                _coalesce(data, "present_names", "available_names", "env_names"),
                field_name="present_names",
            ),
        )

    @property
    def missing_required_names(self) -> tuple[str, ...]:
        present = set(self.present_names)
        return tuple(name for name in self.required_names if name not in present)


@dataclass(frozen=True, slots=True)
class BackupSnapshot:
    mount_ready: bool | None = None

    @classmethod
    def create(cls, snapshot: Any) -> "BackupSnapshot":
        data = _snapshot_to_mapping(snapshot, field_name="backup_snapshot")
        return cls(
            mount_ready=_normalize_bool(
                _coalesce(data, "mount_ready", "backup_ready", "mounted"),
                field_name="mount_ready",
            )
        )


@dataclass(frozen=True, slots=True)
class PreflightReport:
    status: str
    reasons: tuple[str, ...]
    blockers: tuple[str, ...]
    next_actions: tuple[str, ...]
    worktree: WorktreeSnapshot
    branch: BranchSnapshot
    env: EnvSnapshot
    backup: BackupSnapshot

    @classmethod
    def create(
        cls,
        *,
        status: Any,
        reasons: tuple[str, ...],
        blockers: tuple[str, ...],
        next_actions: tuple[str, ...],
        worktree: WorktreeSnapshot,
        branch: BranchSnapshot,
        env: EnvSnapshot,
        backup: BackupSnapshot,
    ) -> "PreflightReport":
        return cls(
            status=_normalize_status(status),
            reasons=reasons,
            blockers=blockers,
            next_actions=next_actions,
            worktree=worktree,
            branch=branch,
            env=env,
            backup=backup,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reasons": list(self.reasons),
            "blockers": list(self.blockers),
            "next_actions": list(self.next_actions),
            "worktree": {
                "dirty": self.worktree.dirty,
                "staged_files": list(self.worktree.staged_files),
                "allowed_staged_files": list(self.worktree.allowed_staged_files),
                "foreign_staged_files": list(self.worktree.foreign_staged_files),
                "hotfile_conflict": self.worktree.hotfile_conflict,
            },
            "branch": {
                "current_branch": self.branch.current_branch,
                "expected_branch": self.branch.expected_branch,
                "branch_candidates": list(self.branch.branch_candidates),
                "detached": self.branch.detached,
                "ahead": self.branch.ahead,
                "behind": self.branch.behind,
            },
            "env": {
                "required_names": list(self.env.required_names),
                "present_names": list(self.env.present_names),
                "missing_required_names": list(self.env.missing_required_names),
            },
            "backup": {
                "mount_ready": self.backup.mount_ready,
            },
        }


def _dedupe(items: list[str]) -> tuple[str, ...]:
    unique: list[str] = []
    for item in items:
        normalized = _normalize_text(item, field_name="report_item")
        if normalized not in unique:
            unique.append(normalized)
    return tuple(unique)


def _derive_status(*, blockers: tuple[str, ...], deferred: tuple[str, ...], partial: tuple[str, ...]) -> str:
    if blockers:
        return "blocked"
    if deferred:
        return "deferred"
    if partial:
        return "partial"
    return "ready"


def build_odysseus_updater_preflight_report(
    *,
    worktree_snapshot: Any,
    branch_snapshot: Any,
    env_snapshot: Any,
    backup_snapshot: Any,
) -> PreflightReport:
    worktree = WorktreeSnapshot.create(worktree_snapshot)
    branch = BranchSnapshot.create(branch_snapshot)
    env = EnvSnapshot.create(env_snapshot)
    backup = BackupSnapshot.create(backup_snapshot)

    reasons: list[str] = []
    blockers: list[str] = []
    deferred: list[str] = []
    partial: list[str] = []
    next_actions: list[str] = []

    if worktree.hotfile_conflict is True:
        blockers.append("hotfile conflict is present in the supplied worktree snapshot")
        next_actions.append("wait for the hotfile overlap to clear before updater review")
    if worktree.foreign_staged_files:
        blockers.append("foreign staged files are mixed into the updater slice")
        next_actions.append("unstage or move foreign staged files out of the updater slice")
    if worktree.dirty is True:
        partial.append("worktree is dirty outside the staged updater slice")
        next_actions.append("review or shelve unrelated worktree changes before update handoff")
    if worktree.dirty is None:
        deferred.append("worktree cleanliness was not supplied")
        next_actions.append("provide a worktree dirty/clean flag in the snapshot")

    if branch.detached is True:
        deferred.append("branch state is detached and cannot be validated safely")
        next_actions.append("supply a named branch snapshot instead of a detached state")
    if len(branch.branch_candidates) > 1:
        deferred.append("branch snapshot is ambiguous across multiple candidates")
        next_actions.append("reduce branch candidates to a single reviewed branch name")
    if branch.current_branch is None:
        deferred.append("current branch name was not supplied")
        next_actions.append("include the current branch name in the snapshot")
    if (
        branch.current_branch
        and branch.expected_branch
        and branch.current_branch != branch.expected_branch
    ):
        deferred.append("current branch does not match the expected updater branch")
        next_actions.append("align the current branch with the expected updater branch")

    if branch.ahead is None or branch.behind is None:
        deferred.append("ahead/behind counters are incomplete")
        next_actions.append("supply both ahead and behind counters in the branch snapshot")
    elif branch.behind > 0 and branch.ahead > 0:
        blockers.append("branch has diverged with both ahead and behind commits")
        next_actions.append("reconcile branch divergence before updater review")
    elif branch.behind > 0:
        blockers.append("branch is behind its tracked reference")
        next_actions.append("rebase or refresh the branch snapshot before updater review")
    elif branch.ahead > 0:
        partial.append("branch is ahead of its tracked reference")
        next_actions.append("confirm local-only commits are intended for the updater slice")

    missing_required_names = env.missing_required_names
    if missing_required_names:
        blockers.append("required environment names are missing from the supplied snapshot")
        next_actions.append("supply the missing required env names without exposing any values")
    elif not env.required_names:
        deferred.append("required environment names were not supplied")
        next_actions.append("provide the required env names list for offline validation")

    if backup.mount_ready is False:
        blockers.append("backup gate is not ready in the supplied snapshot")
        next_actions.append("restore the backup mount gate before updater review")
    elif backup.mount_ready is None:
        deferred.append("backup mount readiness was not supplied")
        next_actions.append("include backup mount readiness in the snapshot")

    reasons.extend(blockers)
    reasons.extend(deferred)
    reasons.extend(partial)
    if not reasons:
        reasons.append("all supplied updater preflight snapshots passed offline validation")

    return PreflightReport.create(
        status=_derive_status(
            blockers=_dedupe(blockers),
            deferred=_dedupe(deferred),
            partial=_dedupe(partial),
        ),
        reasons=_dedupe(reasons),
        blockers=_dedupe(blockers),
        next_actions=_dedupe(next_actions),
        worktree=worktree,
        branch=branch,
        env=env,
        backup=backup,
    )
