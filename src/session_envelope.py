"""Cache-stable session envelope contract for agent/runtime turns.

The envelope records the parts of a session that affect prompt/cache
compatibility without storing raw prompts, tool schemas, provider output,
private content, tokens, or endpoint secrets.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import re
from typing import Any, Iterable

from src.tool_catalog import ToolManifest


_MAX_ID_LENGTH = 140
_MAX_VERSION_LENGTH = 80
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:@/+~-]{1,140}$")
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")
_FORBIDDEN_TEXT_BITS = (
    "api_key",
    "authorization:",
    "bearer ",
    "cookie:",
    "password",
    "secret",
    "token=",
)


class SessionEnvelopeError(ValueError):
    """Raised when a session envelope field is unsafe or invalid."""


class CacheBoundaryReason(StrEnum):
    SAME = "same"
    MODEL_CHANGED = "model_changed"
    BUDGET_CHANGED = "budget_changed"
    SYSTEM_PROMPT_CHANGED = "system_prompt_changed"
    TOOL_MANIFEST_CHANGED = "tool_manifest_changed"
    MCP_SELECTION_CHANGED = "mcp_selection_changed"
    PLUGIN_SELECTION_CHANGED = "plugin_selection_changed"
    REASONING_CHANGED = "reasoning_changed"


class SessionMutationPhase(StrEnum):
    SESSION_START = "session_start"
    MID_SESSION = "mid_session"
    AFTER_COMPACTION = "after_compaction"
    OPERATOR_APPROVED = "operator_approved"


def _reject_forbidden_text(value: Any, *, field_name: str) -> None:
    text = str(value or "").lower()
    if any(bit in text for bit in _FORBIDDEN_TEXT_BITS):
        raise SessionEnvelopeError(f"{field_name} must not contain secrets or raw credentials")


def _normalize_ref(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise SessionEnvelopeError(f"{field_name} must not be empty")
    _reject_forbidden_text(text, field_name=field_name)
    lowered = text.lower()
    if "\\" in text or lowered.startswith("/") or re.match(r"^[a-z]:/", lowered) or "/users/" in lowered or "/home/" in lowered:
        digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        return f"sha256:{digest}"
    if not _SAFE_ID_RE.fullmatch(text):
        digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        return f"sha256:{digest}"
    if len(text) > _MAX_ID_LENGTH:
        raise SessionEnvelopeError(f"{field_name} exceeds max length {_MAX_ID_LENGTH}")
    return text


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise SessionEnvelopeError(f"{field_name} must not be empty")
    _reject_forbidden_text(raw, field_name=field_name)
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise SessionEnvelopeError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_VERSION_LENGTH:
        raise SessionEnvelopeError(f"{field_name} exceeds max length {_MAX_VERSION_LENGTH}")
    return normalized


def _normalize_positive_int(value: Any, *, field_name: str, max_value: int = 5_000_000) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise SessionEnvelopeError(f"{field_name} must be an integer") from None
    if number <= 0 or number > max_value:
        raise SessionEnvelopeError(f"{field_name} must be between 1 and {max_value}")
    return number


def _normalize_ref_tuple(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    seen: set[str] = set()
    items: list[str] = []
    for value in values:
        item = _normalize_ref(value, field_name=field_name)
        if item not in seen:
            seen.add(item)
            items.append(item)
    return tuple(sorted(items))


@dataclass(frozen=True, slots=True)
class SessionEnvelope:
    model_ref: str
    reasoning_profile: str
    context_budget_tokens: int
    output_budget_tokens: int
    system_prompt_version: str
    tool_manifest_refs: tuple[str, ...]
    selected_schema_refs: tuple[str, ...]
    mcp_server_refs: tuple[str, ...]
    plugin_refs: tuple[str, ...]
    cache_boundary_marker: str

    @classmethod
    def create(
        cls,
        *,
        model_ref: str,
        reasoning_profile: str,
        context_budget_tokens: int,
        output_budget_tokens: int,
        system_prompt_version: str,
        tool_manifests: Iterable[ToolManifest] = (),
        tool_manifest_refs: Iterable[str] = (),
        selected_schema_refs: Iterable[str] = (),
        mcp_server_refs: Iterable[str] = (),
        plugin_refs: Iterable[str] = (),
    ) -> "SessionEnvelope":
        manifest_refs = list(tool_manifest_refs)
        for manifest in tool_manifests:
            if not isinstance(manifest, ToolManifest):
                raise SessionEnvelopeError("tool_manifests must contain ToolManifest instances")
            manifest_refs.append(f"{manifest.tool_id}:{manifest.schema_ref}:{manifest.visibility_state.value}")

        normalized = {
            "model_ref": _normalize_ref(model_ref, field_name="model_ref"),
            "reasoning_profile": _normalize_slug(reasoning_profile, field_name="reasoning_profile"),
            "context_budget_tokens": _normalize_positive_int(context_budget_tokens, field_name="context_budget_tokens"),
            "output_budget_tokens": _normalize_positive_int(output_budget_tokens, field_name="output_budget_tokens"),
            "system_prompt_version": _normalize_slug(system_prompt_version, field_name="system_prompt_version"),
            "tool_manifest_refs": _normalize_ref_tuple(manifest_refs, field_name="tool_manifest_ref"),
            "selected_schema_refs": _normalize_ref_tuple(selected_schema_refs, field_name="selected_schema_ref"),
            "mcp_server_refs": _normalize_ref_tuple(mcp_server_refs, field_name="mcp_server_ref"),
            "plugin_refs": _normalize_ref_tuple(plugin_refs, field_name="plugin_ref"),
        }
        marker = _stable_hash(normalized)
        return cls(cache_boundary_marker=marker, **normalized)

    def stable_payload(self) -> dict[str, Any]:
        return {
            "model_ref": self.model_ref,
            "reasoning_profile": self.reasoning_profile,
            "context_budget_tokens": self.context_budget_tokens,
            "output_budget_tokens": self.output_budget_tokens,
            "system_prompt_version": self.system_prompt_version,
            "tool_manifest_refs": self.tool_manifest_refs,
            "selected_schema_refs": self.selected_schema_refs,
            "mcp_server_refs": self.mcp_server_refs,
            "plugin_refs": self.plugin_refs,
        }

    def audit_summary(self) -> dict[str, Any]:
        return {
            **self.stable_payload(),
            "cache_boundary_marker": self.cache_boundary_marker,
            "tool_manifest_count": len(self.tool_manifest_refs),
            "selected_schema_count": len(self.selected_schema_refs),
            "mcp_server_count": len(self.mcp_server_refs),
            "plugin_count": len(self.plugin_refs),
            "raw_prompt_visible": False,
            "raw_schema_visible": False,
            "raw_content_visible": False,
            "token_value_visible": False,
        }


@dataclass(frozen=True, slots=True)
class CacheBoundaryDiff:
    changed: bool
    reasons: tuple[CacheBoundaryReason, ...]
    previous_marker: str
    current_marker: str

    def audit_summary(self) -> dict[str, Any]:
        return {
            "changed": self.changed,
            "reasons": tuple(reason.value for reason in self.reasons),
            "previous_marker": self.previous_marker,
            "current_marker": self.current_marker,
            "raw_prompt_visible": False,
            "raw_content_visible": False,
            "token_value_visible": False,
        }


@dataclass(frozen=True, slots=True)
class CacheBoundaryPolicyDecision:
    allowed: bool
    phase: SessionMutationPhase
    diff: CacheBoundaryDiff
    requires_new_session: bool
    requires_operator_go: bool
    decision: str

    def audit_summary(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "phase": self.phase.value,
            "changed": self.diff.changed,
            "reasons": tuple(reason.value for reason in self.diff.reasons),
            "requires_new_session": self.requires_new_session,
            "requires_operator_go": self.requires_operator_go,
            "decision": self.decision,
            "previous_marker": self.diff.previous_marker,
            "current_marker": self.diff.current_marker,
            "raw_prompt_visible": False,
            "raw_content_visible": False,
            "token_value_visible": False,
        }


def compare_session_envelopes(previous: SessionEnvelope, current: SessionEnvelope) -> CacheBoundaryDiff:
    if not isinstance(previous, SessionEnvelope) or not isinstance(current, SessionEnvelope):
        raise SessionEnvelopeError("previous and current must be SessionEnvelope instances")
    reasons: list[CacheBoundaryReason] = []
    if previous.cache_boundary_marker == current.cache_boundary_marker:
        reasons.append(CacheBoundaryReason.SAME)
    else:
        if previous.model_ref != current.model_ref:
            reasons.append(CacheBoundaryReason.MODEL_CHANGED)
        if (
            previous.context_budget_tokens != current.context_budget_tokens
            or previous.output_budget_tokens != current.output_budget_tokens
        ):
            reasons.append(CacheBoundaryReason.BUDGET_CHANGED)
        if previous.system_prompt_version != current.system_prompt_version:
            reasons.append(CacheBoundaryReason.SYSTEM_PROMPT_CHANGED)
        if previous.tool_manifest_refs != current.tool_manifest_refs or previous.selected_schema_refs != current.selected_schema_refs:
            reasons.append(CacheBoundaryReason.TOOL_MANIFEST_CHANGED)
        if previous.mcp_server_refs != current.mcp_server_refs:
            reasons.append(CacheBoundaryReason.MCP_SELECTION_CHANGED)
        if previous.plugin_refs != current.plugin_refs:
            reasons.append(CacheBoundaryReason.PLUGIN_SELECTION_CHANGED)
        if previous.reasoning_profile != current.reasoning_profile:
            reasons.append(CacheBoundaryReason.REASONING_CHANGED)
    return CacheBoundaryDiff(
        changed=previous.cache_boundary_marker != current.cache_boundary_marker,
        reasons=tuple(reasons),
        previous_marker=previous.cache_boundary_marker,
        current_marker=current.cache_boundary_marker,
    )


def evaluate_cache_boundary_policy(
    previous: SessionEnvelope,
    current: SessionEnvelope,
    *,
    phase: SessionMutationPhase | str,
    operator_go: bool = False,
) -> CacheBoundaryPolicyDecision:
    try:
        normalized_phase = phase if isinstance(phase, SessionMutationPhase) else SessionMutationPhase(str(phase))
    except ValueError:
        raise SessionEnvelopeError("phase must be a known session mutation phase") from None

    diff = compare_session_envelopes(previous, current)
    if not diff.changed:
        return CacheBoundaryPolicyDecision(
            allowed=True,
            phase=normalized_phase,
            diff=diff,
            requires_new_session=False,
            requires_operator_go=False,
            decision="same_envelope",
        )

    if normalized_phase in (SessionMutationPhase.SESSION_START, SessionMutationPhase.AFTER_COMPACTION):
        return CacheBoundaryPolicyDecision(
            allowed=True,
            phase=normalized_phase,
            diff=diff,
            requires_new_session=False,
            requires_operator_go=False,
            decision="cache_boundary_allowed_at_phase",
        )

    if normalized_phase is SessionMutationPhase.OPERATOR_APPROVED and operator_go:
        return CacheBoundaryPolicyDecision(
            allowed=True,
            phase=normalized_phase,
            diff=diff,
            requires_new_session=False,
            requires_operator_go=False,
            decision="cache_boundary_allowed_by_operator_go",
        )

    return CacheBoundaryPolicyDecision(
        allowed=False,
        phase=normalized_phase,
        diff=diff,
        requires_new_session=True,
        requires_operator_go=normalized_phase is SessionMutationPhase.MID_SESSION,
        decision="cache_boundary_change_blocked_mid_session",
    )


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
