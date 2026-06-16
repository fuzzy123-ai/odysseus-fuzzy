"""Small backend contract for an in-memory agent run store model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
import re
from typing import Any, Iterable


_MAX_ID = 80
_MAX_TEXT = 160
_MAX_TIMESTAMP = 40
_MAX_COMMIT = 40
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")
_COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_STATUS_COMPATIBLE = {"pending", "running", "done", "blocked", "failed", "handoff", "skipped"}


class AgentRunStoreError(ValueError):
    """Raised when an agent run store payload is invalid or unsafe."""


class AgentRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    BLOCKED = "blocked"
    FAILED = "failed"
    HANDOFF = "handoff"
    SKIPPED = "skipped"


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise AgentRunStoreError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise AgentRunStoreError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID:
        raise AgentRunStoreError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise AgentRunStoreError(f"{field_name} must not be empty")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _normalize_text_list(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _normalize_text(value, field_name=field_name, allow_empty=True)
        if not text:
            continue
        if text not in seen:
            seen.add(text)
            normalized.append(text)
    return tuple(normalized)


def _normalize_repo_path(value: Any, *, field_name: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise AgentRunStoreError(f"{field_name} must not be empty")
    if "\\" in raw:
        raise AgentRunStoreError(f"{field_name} must use forward slashes only")
    lowered = raw.lower()
    if lowered.startswith("/") or lowered.startswith("./") or re.match(r"^[a-z]:", lowered):
        raise AgentRunStoreError(f"{field_name} must be repo-relative")
    parts = PurePosixPath(raw).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise AgentRunStoreError(f"{field_name} must not contain traversal segments")
    return "/".join(parts)


def _normalize_path_list(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        path = _normalize_repo_path(value, field_name=field_name)
        if path not in seen:
            seen.add(path)
            normalized.append(path)
    return tuple(sorted(normalized))


def _normalize_commit(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if len(text) > _MAX_COMMIT or not _COMMIT_RE.fullmatch(text):
        raise AgentRunStoreError("commit must be empty or a git sha-like hex id")
    return text


def _normalize_timestamp(value: Any, *, field_name: str, allow_empty: bool) -> str:
    text = str(value or "").strip()
    if not text:
        if allow_empty:
            return ""
        raise AgentRunStoreError(f"{field_name} must not be empty")
    if len(text) > _MAX_TIMESTAMP or not _TIMESTAMP_RE.fullmatch(text):
        raise AgentRunStoreError(f"{field_name} must be an ISO-8601 UTC timestamp")
    return text


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")


def _normalize_status(value: Any, *, field_name: str) -> AgentRunStatus:
    normalized = _normalize_slug(value, field_name=field_name)
    if normalized not in _STATUS_COMPATIBLE:
        raise AgentRunStoreError(f"{field_name} is not compatible with plan/tool-truth status vocabulary")
    return AgentRunStatus(normalized)


@dataclass(frozen=True, slots=True)
class AgentRunEvidence:
    evidence: tuple[str, ...]
    tests: tuple[str, ...]
    commit: str
    changed_files: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        evidence: Iterable[Any],
        tests: Iterable[Any],
        commit: Any,
        changed_files: Iterable[Any],
    ) -> "AgentRunEvidence":
        return cls(
            evidence=_normalize_text_list(evidence, field_name="evidence"),
            tests=_normalize_text_list(tests, field_name="tests"),
            commit=_normalize_commit(commit),
            changed_files=_normalize_path_list(changed_files, field_name="changed_file"),
        )

    def has_completion_signal(self) -> bool:
        return bool(self.evidence or self.tests or self.commit)


@dataclass(frozen=True, slots=True)
class AgentRun:
    agent_run_id: str
    plan_id: str
    node_id: str
    slice_id: str
    agent_id: str
    role_id: str
    model: str
    thinking: str
    status: AgentRunStatus
    started_at: str
    completed_at: str
    evidence: AgentRunEvidence
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    blocker: str
    next_action: str

    @classmethod
    def create(
        cls,
        *,
        agent_run_id: Any,
        plan_id: Any,
        node_id: Any,
        slice_id: Any,
        agent_id: Any,
        role_id: Any,
        model: Any,
        thinking: Any,
        status: AgentRunStatus | str,
        started_at: Any,
        completed_at: Any,
        changed_files: Iterable[Any],
        tests: Iterable[Any],
        commit: Any,
        warnings: Iterable[Any],
        errors: Iterable[Any],
        blocker: Any,
        next_action: Any,
        evidence: Iterable[Any] = (),
    ) -> "AgentRun":
        normalized_status = status if isinstance(status, AgentRunStatus) else _normalize_status(status, field_name="status")
        normalized_evidence = AgentRunEvidence.create(
            evidence=evidence,
            tests=tests,
            commit=commit,
            changed_files=changed_files,
        )
        normalized_started_at = _normalize_timestamp(started_at, field_name="started_at", allow_empty=False)
        normalized_completed_at = _normalize_timestamp(completed_at, field_name="completed_at", allow_empty=True)
        normalized_errors = _normalize_text_list(errors, field_name="errors")
        normalized_blocker = _normalize_text(blocker, field_name="blocker", allow_empty=True)
        normalized_next_action = _normalize_text(next_action, field_name="next_action", allow_empty=True)
        if normalized_completed_at and _parse_timestamp(normalized_completed_at) < _parse_timestamp(normalized_started_at):
            raise AgentRunStoreError("completed_at must not be before started_at")
        if normalized_status == AgentRunStatus.DONE and not normalized_evidence.has_completion_signal():
            raise AgentRunStoreError("done runs require evidence or commit/test completion signals")
        if normalized_status == AgentRunStatus.FAILED and not normalized_errors:
            raise AgentRunStoreError("failed runs require at least one error")
        if normalized_status == AgentRunStatus.BLOCKED and not normalized_blocker:
            raise AgentRunStoreError("blocked runs require a blocker")
        if normalized_status in {AgentRunStatus.HANDOFF, AgentRunStatus.SKIPPED} and not normalized_next_action:
            raise AgentRunStoreError("handoff and skipped runs require next_action")
        return cls(
            agent_run_id=_normalize_slug(agent_run_id, field_name="agent_run_id"),
            plan_id=_normalize_slug(plan_id, field_name="plan_id"),
            node_id=_normalize_slug(node_id, field_name="node_id"),
            slice_id=_normalize_slug(slice_id, field_name="slice_id"),
            agent_id=_normalize_slug(agent_id, field_name="agent_id"),
            role_id=_normalize_slug(role_id, field_name="role_id"),
            model=_normalize_text(model, field_name="model", allow_empty=False),
            thinking=_normalize_text(thinking, field_name="thinking", allow_empty=False, limit=40),
            status=normalized_status,
            started_at=normalized_started_at,
            completed_at=normalized_completed_at,
            evidence=normalized_evidence,
            warnings=_normalize_text_list(warnings, field_name="warnings"),
            errors=normalized_errors,
            blocker=normalized_blocker,
            next_action=normalized_next_action,
        )

    def audit_summary(self) -> dict[str, Any]:
        return {
            "agent_run_id": self.agent_run_id,
            "plan_id": self.plan_id,
            "node_id": self.node_id,
            "slice_id": self.slice_id,
            "agent_id": self.agent_id,
            "role_id": self.role_id,
            "status": self.status.value,
            "commit": self.evidence.commit,
            "changed_file_count": len(self.evidence.changed_files),
            "test_count": len(self.evidence.tests),
            "evidence_count": len(self.evidence.evidence),
            "warning_count": len(self.warnings),
            "error_count": len(self.errors),
            "has_blocker": bool(self.blocker),
            "next_action": self.next_action,
            "tests": self.evidence.tests,
        }
