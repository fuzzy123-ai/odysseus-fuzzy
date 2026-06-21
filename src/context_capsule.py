"""Small backend contract for subagent context capsules."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
import re
from typing import Any, Iterable

from src.agent_identity import AgentIdentity


_MAX_CAPSULE_ID_LENGTH = 80
_MAX_OBJECTIVE_LENGTH = 400
_MAX_MEMORY_SUMMARY_LENGTH = 240
_MAX_MEMORY_BUDGET_CHARS = 1600
_MAX_SUMMARY_TEXT = 120
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")


class ContextCapsuleError(ValueError):
    """Raised when a context capsule payload cannot be normalized safely."""


class CapsuleMemoryKind(StrEnum):
    DECISION = "decision"
    EVIDENCE = "evidence"
    CONSTRAINT = "constraint"
    RISK = "risk"


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


def _normalize_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        raise ContextCapsuleError("confidence must be a number between 0 and 1") from None
    if confidence < 0 or confidence > 1:
        raise ContextCapsuleError("confidence must be between 0 and 1")
    return round(confidence, 3)


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
class CapsuleMemoryItem:
    item_id: str
    kind: CapsuleMemoryKind
    source_ref: str
    summary: str
    confidence: float
    evidence_refs: tuple[str, ...]
    accepted: bool

    @classmethod
    def create(
        cls,
        *,
        item_id: str,
        kind: CapsuleMemoryKind | str,
        source_ref: str,
        summary: str,
        confidence: Any,
        evidence_refs: Iterable[str] = (),
        accepted: bool = True,
    ) -> "CapsuleMemoryItem":
        normalized_summary = " ".join(str(summary or "").split())
        if not normalized_summary:
            raise ContextCapsuleError("memory summary must not be empty")
        if len(normalized_summary) > _MAX_MEMORY_SUMMARY_LENGTH:
            raise ContextCapsuleError(f"memory summary exceeds max length {_MAX_MEMORY_SUMMARY_LENGTH}")
        return cls(
            item_id=_normalize_slug(item_id, field_name="memory_item_id"),
            kind=kind if isinstance(kind, CapsuleMemoryKind) else CapsuleMemoryKind(str(kind)),
            source_ref=_normalize_repo_path(source_ref),
            summary=normalized_summary,
            confidence=_normalize_confidence(confidence),
            evidence_refs=_normalize_text_list(evidence_refs, field_name="memory_evidence_ref", allow_empty=True),
            accepted=bool(accepted),
        )

    def to_context_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "kind": self.kind.value,
            "source_ref": self.source_ref,
            "summary": self.summary,
            "confidence": self.confidence,
            "evidence_refs": list(self.evidence_refs),
        }


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
    memory_items: tuple[CapsuleMemoryItem, ...]

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
        memory_items: Iterable[CapsuleMemoryItem] = (),
    ) -> "ContextCapsule":
        if not isinstance(agent_identity, AgentIdentity):
            raise ContextCapsuleError("agent_identity must be an AgentIdentity")
        allowed = _normalize_path_list(allowed_files, field_name="allowed_files", allow_empty=False)
        blocked = _normalize_path_list(blocked_files, field_name="blocked_files", allow_empty=True)
        overlap = sorted(set(allowed) & set(blocked))
        if overlap:
            raise ContextCapsuleError(f"allowed_files and blocked_files overlap: {', '.join(overlap)}")
        normalized_memory = _normalize_memory_items(memory_items)
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
            memory_items=normalized_memory,
        )

    def memory_context(self) -> tuple[dict[str, Any], ...]:
        return tuple(item.to_context_dict() for item in self.memory_items)

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
            "memory_item_count": len(self.memory_items),
            "memory_source_refs": tuple(item.source_ref for item in self.memory_items),
            "memory_summary_previews": {
                item.item_id: _truncate_preview(item.summary) for item in self.memory_items
            },
        }


def _normalize_memory_items(values: Iterable[CapsuleMemoryItem]) -> tuple[CapsuleMemoryItem, ...]:
    items = tuple(values)
    if any(not isinstance(item, CapsuleMemoryItem) for item in items):
        raise ContextCapsuleError("memory_items must contain CapsuleMemoryItem instances")
    if any(not item.accepted for item in items):
        raise ContextCapsuleError("memory_items must be accepted before entering a context capsule")
    total_summary_chars = sum(len(item.summary) for item in items)
    if total_summary_chars > _MAX_MEMORY_BUDGET_CHARS:
        raise ContextCapsuleError("memory_items exceed capsule memory summary budget")
    seen: set[str] = set()
    deduped: list[CapsuleMemoryItem] = []
    for item in items:
        if item.item_id not in seen:
            seen.add(item.item_id)
            deduped.append(item)
    return tuple(deduped)
