"""Small backend contract for subagent context capsules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from typing import Any, Iterable

from src.agent_identity import AgentIdentity


_MAX_CAPSULE_ID_LENGTH = 80
_MAX_OBJECTIVE_LENGTH = 400
_MAX_SUMMARY_TEXT = 120
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")


class ContextCapsuleError(ValueError):
    """Raised when a context capsule payload cannot be normalized safely."""


def _normalize_slug(value: str, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise ContextCapsuleError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise ContextCapsuleError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_CAPSULE_ID_LENGTH:
        raise ContextCapsuleError(f"{field_name} exceeds max length {_MAX_CAPSULE_ID_LENGTH}")
    return normalized


def _normalize_objective(value: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise ContextCapsuleError("objective must not be empty")
    if len(text) > _MAX_OBJECTIVE_LENGTH:
        raise ContextCapsuleError(f"objective exceeds max length {_MAX_OBJECTIVE_LENGTH}")
    return text


def _normalize_repo_path(path: str) -> str:
    raw = str(path or "").strip()
    if not raw:
        raise ContextCapsuleError("path must not be empty")
    if "\\" in raw:
        raise ContextCapsuleError("path must use forward slashes only")
    lowered = raw.lower()
    if lowered.startswith("/") or lowered.startswith("./") or re.match(r"^[a-z]:", lowered):
        raise ContextCapsuleError("path must be relative to the repo root")
    parts = PurePosixPath(raw).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ContextCapsuleError("path must not contain traversal segments")
    return "/".join(parts)


def _normalize_path_list(values: Iterable[str], *, field_name: str, allow_empty: bool) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        path = _normalize_repo_path(value)
        if path not in seen:
            seen.add(path)
            normalized.append(path)
    if not allow_empty and not normalized:
        raise ContextCapsuleError(f"{field_name} must not be empty")
    return tuple(sorted(normalized))


def _normalize_text_list(values: Iterable[str], *, field_name: str, allow_empty: bool) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = " ".join(str(value or "").split())
        if not text:
            continue
        if text not in seen:
            seen.add(text)
            normalized.append(text)
    if not allow_empty and not normalized:
        raise ContextCapsuleError(f"{field_name} must not be empty")
    return tuple(normalized)


def _normalize_inputs(values: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key in sorted(values):
        clean_key = _normalize_slug(key, field_name="input_key")
        normalized[clean_key] = values[key]
    return normalized


def _truncate_preview(value: Any) -> str:
    text = " ".join(str(value).split())
    if len(text) <= _MAX_SUMMARY_TEXT:
        return text
    return text[: _MAX_SUMMARY_TEXT - 3] + "..."


@dataclass(frozen=True, slots=True)
class ContextCapsule:
    capsule_id: str
    objective: str
    agent_identity: AgentIdentity
    allowed_files: tuple[str, ...]
    blocked_files: tuple[str, ...]
    inputs: dict[str, Any]
    expected_outputs: tuple[str, ...]
    tests: tuple[str, ...]
    handoff_format: tuple[str, ...]
    stop_conditions: tuple[str, ...]
    evidence_required: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        capsule_id: str,
        objective: str,
        agent_identity: AgentIdentity,
        allowed_files: Iterable[str],
        blocked_files: Iterable[str],
        inputs: dict[str, Any],
        expected_outputs: Iterable[str],
        tests: Iterable[str],
        handoff_format: Iterable[str],
        stop_conditions: Iterable[str],
        evidence_required: Iterable[str],
    ) -> "ContextCapsule":
        if not isinstance(agent_identity, AgentIdentity):
            raise ContextCapsuleError("agent_identity must be an AgentIdentity")
        allowed = _normalize_path_list(allowed_files, field_name="allowed_files", allow_empty=False)
        blocked = _normalize_path_list(blocked_files, field_name="blocked_files", allow_empty=True)
        overlap = sorted(set(allowed) & set(blocked))
        if overlap:
            raise ContextCapsuleError(f"allowed_files and blocked_files overlap: {', '.join(overlap)}")
        return cls(
            capsule_id=_normalize_slug(capsule_id, field_name="capsule_id"),
            objective=_normalize_objective(objective),
            agent_identity=agent_identity,
            allowed_files=allowed,
            blocked_files=blocked,
            inputs=_normalize_inputs(inputs),
            expected_outputs=_normalize_text_list(expected_outputs, field_name="expected_outputs", allow_empty=True),
            tests=_normalize_text_list(tests, field_name="tests", allow_empty=True),
            handoff_format=_normalize_text_list(handoff_format, field_name="handoff_format", allow_empty=False),
            stop_conditions=_normalize_text_list(stop_conditions, field_name="stop_conditions", allow_empty=True),
            evidence_required=_normalize_text_list(evidence_required, field_name="evidence_required", allow_empty=True),
        )

    def audit_summary(self) -> dict[str, Any]:
        return {
            "capsule_id": self.capsule_id,
            "agent_id": self.agent_identity.agent_id,
            "role_id": self.agent_identity.role_id,
            "project_id": self.agent_identity.project_id,
            "run_id": self.agent_identity.run_id,
            "identity_key": self.agent_identity.identity_key(),
            "allowed_file_count": len(self.allowed_files),
            "blocked_file_count": len(self.blocked_files),
            "input_count": len(self.inputs),
            "input_keys": tuple(sorted(self.inputs)),
            "input_previews": {
                key: _truncate_preview(value) for key, value in sorted(self.inputs.items())
            },
            "tests": self.tests,
            "expected_output_count": len(self.expected_outputs),
            "stop_condition_count": len(self.stop_conditions),
            "evidence_required_count": len(self.evidence_required),
        }
