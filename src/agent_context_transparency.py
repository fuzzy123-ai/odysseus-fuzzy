"""Fail-closed schema-v1 contracts for Agent Context Transparency payloads.

The module is intentionally offline and side-effect free.  It validates and
serializes bounded transparency payloads, but does not persist feedback,
mutate policy, call providers, or integrate with UI/event services.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import PurePosixPath
import re
import unicodedata
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
MAX_CONTEXT_ITEM_BYTES = 8 * 1024
MAX_ANSWER_PACK_BYTES = 128 * 1024
MAX_INFLUENCE_BYTES = 32 * 1024
MAX_FEEDBACK_BYTES = 8 * 1024
MAX_ITEMS = 64
MAX_REFS = 64
MAX_EVIDENCE_REFS = 32

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
_REPO_PATH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")
_WINDOWS_ABSOLUTE_RE = re.compile(r"(?i)(?:^|\s)[a-z]:[\\/]")
_PRIVATE_UNIX_RE = re.compile(r"(?:^|\s)/(?:home|users)/[^\s/]+/")
_CREDENTIAL_VALUE_RE = re.compile(r"(?i)(?:bearer\s+[a-z0-9._-]{8,}|sk-[a-z0-9_-]{8,})")
_CREDENTIAL_URI_RE = re.compile(r"(?i)[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")
_FORBIDDEN_KEYS = {
    "absolute_private_path",
    "access_token",
    "api_key",
    "authorization",
    "authorization_header",
    "chat_id",
    "client_secret",
    "cookie",
    "cookies",
    "full_prompt",
    "password",
    "private_key",
    "raw_private_content",
    "raw_prompt",
    "raw_provider_output",
    "raw_secret",
    "refresh_token",
    "secret_value",
    "telegram_chat_id",
    "token",
}

TRUTH_LEVELS = {"runtime_trace", "semantic_projection", "local_model_internals", "visual_effect"}
CLASSIFICATIONS = {"public", "private", "sensitive", "secret", "unknown"}
REDACTION_STATES = {"none", "summary_only", "metadata_only", "fully_redacted", "blocked"}
REVIEW_REASONS = {"uncertainty", "conflict", "policy_risk", "user_visible_writeback"}
REVIEW_REASON_ORDER = ("uncertainty", "conflict", "policy_risk", "user_visible_writeback")
SCOPES = {"turn", "conversation", "project", "workspace", "global"}

QUIET_REVIEW_OBSERVATIONS = {
    "routine_read",
    "routine_selection",
    "answer_pack_inspection",
    "feedback_recording",
}
REVIEW_OBSERVATION_REASONS = {
    "confidence_unknown": "uncertainty",
    "confidence_low": "uncertainty",
    "freshness_unknown": "uncertainty",
    "freshness_stale": "uncertainty",
    "classification_unknown": "uncertainty",
    "source_disagreement": "conflict",
    "feedback_disagreement": "conflict",
    "classification_boundary": "policy_risk",
    "secure_mode_boundary": "policy_risk",
    "provider_boundary": "policy_risk",
    "tool_boundary": "policy_risk",
    "memory_writeback": "user_visible_writeback",
    "project_writeback": "user_visible_writeback",
    "roadmap_writeback": "user_visible_writeback",
    "policy_writeback": "user_visible_writeback",
}
MAX_REVIEW_OBSERVATIONS = 32

SELECTION_REASON_PRIORITY = (
    "pinned",
    "explicit_mention",
    "active_project",
    "active_roadmap",
    "recent",
    "semantic_match",
    "memory_preference",
    "tool_evidence",
    "system_requirement",
    "continuity",
)
_SELECTION_REASON_DETAILS = {
    "pinned": ("user_pinned", "Pinned by you."),
    "explicit_mention": ("explicit_mention", "Mentioned in this conversation."),
    "active_project": ("active_project", "Part of the active project."),
    "active_roadmap": ("active_roadmap", "Part of the active roadmap."),
    "recent": ("recently_updated", "Recently updated."),
    "semantic_match": ("semantic_match", "Matches the current request."),
    "memory_preference": ("memory_preference", "Matches a saved context preference."),
    "tool_evidence": ("tool_evidence", "Produced by a tool used for this task."),
    "system_requirement": ("system_requirement", "Required by system policy."),
    "continuity": ("conversation_continuity", "Continues the current conversation."),
}
_SELECTION_EVIDENCE_FIELDS = {
    "context_id", "created_at", "context_kind", "label", "source_ref", "selection_state", "scope",
    "reason_flags", "evidence_refs", "classification", "redaction_state", "freshness", "confidence",
    "pinned", "removable", "summary", "redacted_preview", "exclusion_reason", "token_estimate",
    "source_revision_ref", "parent_context_id", "policy_blocked", "source_disagreement",
    "secure_mode_boundary", "provider_boundary", "tool_boundary",
}
_CONTEXT_SOURCE_COMPATIBILITY = {
    "system_rule": {"system_rule"},
    "user_message": {"user_turn"},
    "pinned_document": {"document", "repo_file"},
    "project": {"project"},
    "repo": {"repo_file"},
    "roadmap": {"roadmap"},
    "memory": {"memory"},
    "rag": {"rag_chunk"},
    "tool_evidence": {"tool_result"},
    "import_summary": {"import_summary"},
    "other": {"other"},
}

_CLASSIFICATION_ORDER = {"public": 0, "private": 1, "sensitive": 2, "secret": 3, "unknown": 4}


class AgentContextContractError(ValueError):
    """Raised when a transparency payload is invalid or unsafe."""


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AgentContextContractError(f"{field_name} must be an object")
    _scan_forbidden(value)
    return value


def _scan_forbidden(value: Any) -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = re.sub(r"[-\s]+", "_", str(raw_key).strip().lower())
            if key in _FORBIDDEN_KEYS:
                raise AgentContextContractError("payload contains a forbidden field")
            _scan_forbidden(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _scan_forbidden(child)
    elif isinstance(value, str):
        if _WINDOWS_ABSOLUTE_RE.search(value) or _PRIVATE_UNIX_RE.search(value):
            raise AgentContextContractError("payload contains a forbidden private path")
        if _CREDENTIAL_VALUE_RE.search(value) or _CREDENTIAL_URI_RE.search(value):
            raise AgentContextContractError("payload contains credential-like content")


def _plain_text(value: Any, field_name: str, maximum: int, *, required: bool = True) -> str:
    if value is None:
        if required:
            raise AgentContextContractError(f"{field_name} is required")
        return ""
    if not isinstance(value, str):
        raise AgentContextContractError(f"{field_name} must be text")
    text = unicodedata.normalize("NFC", value).strip()
    if required and not text:
        raise AgentContextContractError(f"{field_name} is required")
    if len(text) > maximum:
        raise AgentContextContractError(f"{field_name} exceeds its budget")
    for char in text:
        if unicodedata.category(char) == "Cc" and char not in "\n\r\t":
            raise AgentContextContractError(f"{field_name} contains control characters")
    _scan_forbidden(text)
    return text


def _optional_text(value: Any, field_name: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _plain_text(value, field_name, maximum, required=False)


def _id(value: Any, field_name: str) -> str:
    text = _plain_text(value, field_name, 128)
    if not _ID_RE.fullmatch(text):
        raise AgentContextContractError(f"{field_name} is invalid")
    return text


def _enum(value: Any, field_name: str, allowed: set[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise AgentContextContractError(f"{field_name} is invalid")
    return value


def _boolean(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise AgentContextContractError(f"{field_name} must be boolean")
    return value


def _integer(value: Any, field_name: str, *, minimum: int = 0, nullable: bool = False) -> int | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise AgentContextContractError(f"{field_name} must be an integer")
    if value < minimum:
        raise AgentContextContractError(f"{field_name} is out of range")
    return value


def _score(value: Any, field_name: str, *, nullable: bool = False) -> float | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AgentContextContractError(f"{field_name} must be a number")
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > 1:
        raise AgentContextContractError(f"{field_name} is out of range")
    return number


def _timestamp(value: Any, field_name: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    text = _plain_text(value, field_name, 40)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise AgentContextContractError(f"{field_name} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise AgentContextContractError(f"{field_name} must be UTC")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _sequence(value: Any, field_name: str, maximum: int) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise AgentContextContractError(f"{field_name} must be an array")
    if len(value) > maximum:
        raise AgentContextContractError(f"{field_name} exceeds its item budget")
    return value


def _ids(value: Any, field_name: str, maximum: int = MAX_REFS, *, require_one: bool = False) -> tuple[str, ...]:
    items = tuple(_id(item, f"{field_name} item") for item in _sequence(value, field_name, maximum))
    if require_one and not items:
        raise AgentContextContractError(f"{field_name} must not be empty")
    if len(set(items)) != len(items):
        raise AgentContextContractError(f"{field_name} contains duplicate ids")
    return items


def _schema_and_kind(data: Mapping[str, Any], expected_kind: str) -> None:
    if data.get("schema_version") != SCHEMA_VERSION:
        raise AgentContextContractError("schema_version is invalid")
    if data.get("kind") != expected_kind:
        raise AgentContextContractError("kind is invalid")


def _payload_budget(value: Mapping[str, Any], maximum: int) -> None:
    try:
        size = len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise AgentContextContractError("payload is not JSON serializable") from exc
    if size > maximum:
        raise AgentContextContractError("payload exceeds its serialized budget")


def _validate_redaction(classification: str, redaction_state: str) -> None:
    if classification in {"sensitive", "secret", "unknown"} and redaction_state == "none":
        raise AgentContextContractError("redaction_state is too weak for classification")


def strongest_classification(values: Sequence[str]) -> str:
    if not values:
        raise AgentContextContractError("classification sources must not be empty")
    normalized = [_enum(value, "classification", CLASSIFICATIONS) for value in values]
    return max(normalized, key=_CLASSIFICATION_ORDER.__getitem__)


@dataclass(frozen=True, slots=True)
class SourceRef:
    ref_type: str
    ref_id: str
    section_ref: str | None = None
    repo_rel_path: str | None = None

    @classmethod
    def create(cls, **values: Any) -> "SourceRef":
        return cls.from_dict(values)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SourceRef":
        data = _mapping(value, "source_ref")
        ref_type = _enum(data.get("ref_type"), "ref_type", {
            "system_rule", "user_turn", "document", "repo_file", "project", "roadmap", "gate",
            "todo", "memory", "rag_chunk", "tool_result", "import_summary", "other",
        })
        path = data.get("repo_rel_path")
        normalized_path: str | None = None
        if path is not None:
            if not isinstance(path, str):
                raise AgentContextContractError("repo_rel_path must be text")
            raw = unicodedata.normalize("NFC", path).strip()
            if raw.startswith(("/", "\\", "//", "./")) or re.match(r"^[A-Za-z]:", raw):
                raise AgentContextContractError("repo_rel_path must be repository-relative")
            if any(part in raw for part in ("%", "?", "#", "://", "\x00")):
                raise AgentContextContractError("repo_rel_path contains unsafe syntax")
            normalized_path = raw.replace("\\", "/")
            parts = PurePosixPath(normalized_path).parts
            if not parts or any(part in {"", ".", ".."} for part in parts):
                raise AgentContextContractError("repo_rel_path contains traversal")
            if not _REPO_PATH_RE.fullmatch(normalized_path):
                raise AgentContextContractError("repo_rel_path is invalid")
            if ref_type != "repo_file":
                raise AgentContextContractError("repo_rel_path requires a repo_file source")
        return cls(
            ref_type=ref_type,
            ref_id=_id(data.get("ref_id"), "ref_id"),
            section_ref=_id(data.get("section_ref"), "section_ref") if data.get("section_ref") is not None else None,
            repo_rel_path=normalized_path,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"ref_type": self.ref_type, "ref_id": self.ref_id}
        if self.section_ref is not None:
            result["section_ref"] = self.section_ref
        if self.repo_rel_path is not None:
            result["repo_rel_path"] = self.repo_rel_path
        return result


@dataclass(frozen=True, slots=True)
class WhySelected:
    code: str
    summary: str
    evidence_refs: tuple[str, ...]
    truth_level: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WhySelected":
        data = _mapping(value, "why_selected")
        truth = _enum(data.get("truth_level"), "why_selected truth_level", TRUTH_LEVELS)
        if truth not in {"runtime_trace", "semantic_projection"}:
            raise AgentContextContractError("why_selected truth_level cannot be evidence")
        return cls(
            code=_enum(data.get("code"), "why_selected code", {
                "user_pinned", "explicit_mention", "active_project", "active_roadmap", "recently_updated",
                "semantic_match", "memory_preference", "tool_evidence", "system_requirement",
                "conversation_continuity", "other",
            }),
            summary=_plain_text(data.get("summary"), "why_selected summary", 240),
            evidence_refs=_ids(data.get("evidence_refs", ()), "why_selected evidence_refs", 8),
            truth_level=truth,
        )

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "summary": self.summary, "evidence_refs": list(self.evidence_refs), "truth_level": self.truth_level}


@dataclass(frozen=True, slots=True)
class Freshness:
    state: str
    observed_at: str | None
    source_updated_at: str | None
    age_seconds: int | None
    reason: str | None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Freshness":
        data = _mapping(value, "freshness")
        return cls(
            state=_enum(data.get("state"), "freshness state", {"current", "recent", "stale", "expired", "unknown"}),
            observed_at=_timestamp(data.get("observed_at"), "observed_at", nullable=True),
            source_updated_at=_timestamp(data.get("source_updated_at"), "source_updated_at", nullable=True),
            age_seconds=_integer(data.get("age_seconds"), "age_seconds", nullable=True),
            reason=_optional_text(data.get("reason"), "freshness reason", 160),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state, "observed_at": self.observed_at, "source_updated_at": self.source_updated_at,
            "age_seconds": self.age_seconds, "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class Confidence:
    level: str
    score: float | None
    basis: str
    summary: str | None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Confidence":
        data = _mapping(value, "confidence")
        score = _score(data.get("score"), "confidence score", nullable=True)
        basis = _enum(data.get("basis"), "confidence basis", {
            "direct", "rule", "retrieval_score", "multiple_sources", "user_confirmed", "unknown",
        })
        if score is not None and basis == "unknown":
            raise AgentContextContractError("confidence score requires a documented basis")
        return cls(
            level=_enum(data.get("level"), "confidence level", {"high", "medium", "low", "unknown"}),
            score=score,
            basis=basis,
            summary=_optional_text(data.get("summary"), "confidence summary", 160),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"level": self.level, "score": self.score, "basis": self.basis, "summary": self.summary}


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    required: bool
    reason_codes: tuple[str, ...]
    summary: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReviewDecision":
        data = _mapping(value, "review")
        required = _boolean(data.get("required"), "review required")
        raw_reasons = _sequence(data.get("reason_codes", ()), "review reason_codes", 4)
        reasons = tuple(_enum(item, "review reason", REVIEW_REASONS) for item in raw_reasons)
        if len(set(reasons)) != len(reasons):
            raise AgentContextContractError("review reason_codes contains duplicates")
        if required != bool(reasons):
            raise AgentContextContractError("review required and reason_codes disagree")
        return cls(required, reasons, _optional_text(data.get("summary"), "review summary", 240))

    def to_dict(self) -> dict[str, Any]:
        return {"required": self.required, "reason_codes": list(self.reason_codes), "summary": self.summary}


def classify_review(
    observations: Sequence[str] = (),
    *,
    uncertainty: bool = False,
    conflict: bool = False,
    policy_risk: bool = False,
    user_visible_writeback: bool = False,
) -> ReviewDecision:
    """Classify bounded observations into a quiet or visible review decision.

    Observation names describe validated state, not event types or raw payloads.
    Unknown observations fail closed instead of being guessed into a noisy queue.
    """

    raw_observations = _sequence(observations, "review observations", MAX_REVIEW_OBSERVATIONS)
    reasons: set[str] = set()
    for observation in raw_observations:
        if not isinstance(observation, str):
            raise AgentContextContractError("review observation must be text")
        if observation in QUIET_REVIEW_OBSERVATIONS:
            continue
        reason = REVIEW_OBSERVATION_REASONS.get(observation)
        if reason is None:
            raise AgentContextContractError("review observation is invalid")
        reasons.add(reason)

    direct_flags = {
        "uncertainty": _boolean(uncertainty, "uncertainty"),
        "conflict": _boolean(conflict, "conflict"),
        "policy_risk": _boolean(policy_risk, "policy_risk"),
        "user_visible_writeback": _boolean(user_visible_writeback, "user_visible_writeback"),
    }
    reasons.update(reason for reason, enabled in direct_flags.items() if enabled)
    ordered = tuple(reason for reason in REVIEW_REASON_ORDER if reason in reasons)
    if not ordered:
        return ReviewDecision.from_dict({"required": False, "reason_codes": [], "summary": None})

    summary_parts = {
        "uncertainty": "evidence is uncertain",
        "conflict": "sources or feedback conflict",
        "policy_risk": "a policy boundary may be unsafe",
        "user_visible_writeback": "a durable user-visible change is proposed",
    }
    summary = "Needs review: " + "; ".join(summary_parts[reason] for reason in ordered) + "."
    return ReviewDecision.from_dict({"required": True, "reason_codes": list(ordered), "summary": summary})


build_review_decision = classify_review


def build_context_item_from_evidence(value: Mapping[str, Any]) -> "ContextItem":
    """Build one validated ContextItem from bounded normalized selection evidence."""

    data = _mapping(value, "selection evidence")
    if set(data) - _SELECTION_EVIDENCE_FIELDS:
        raise AgentContextContractError("selection evidence contains unsupported fields")

    raw_flags = _sequence(data.get("reason_flags"), "reason_flags", len(SELECTION_REASON_PRIORITY))
    flags = tuple(_enum(flag, "reason flag", set(SELECTION_REASON_PRIORITY)) for flag in raw_flags)
    if not flags:
        raise AgentContextContractError("selection evidence requires a reason flag")
    if len(set(flags)) != len(flags):
        raise AgentContextContractError("reason_flags contains duplicates")
    selected_reason = next(reason for reason in SELECTION_REASON_PRIORITY if reason in flags)
    reason_code, reason_summary = _SELECTION_REASON_DETAILS[selected_reason]

    source = SourceRef.from_dict(data.get("source_ref"))
    context_kind = _enum(data.get("context_kind"), "context_kind", set(_CONTEXT_SOURCE_COMPATIBILITY))
    if source.ref_type not in _CONTEXT_SOURCE_COMPATIBILITY[context_kind]:
        raise AgentContextContractError("context_kind and source_ref type disagree")
    if "active_project" in flags and source.ref_type != "project":
        raise AgentContextContractError("active_project reason requires a project source")
    if "active_roadmap" in flags and source.ref_type != "roadmap":
        raise AgentContextContractError("active_roadmap reason requires a roadmap source")
    if "memory_preference" in flags and source.ref_type != "memory":
        raise AgentContextContractError("memory_preference reason requires a memory source")
    if "tool_evidence" in flags and source.ref_type != "tool_result":
        raise AgentContextContractError("tool_evidence reason requires a tool source")
    if "system_requirement" in flags and source.ref_type != "system_rule":
        raise AgentContextContractError("system_requirement reason requires a system rule")

    freshness = Freshness.from_dict(data.get("freshness") or {
        "state": "unknown", "observed_at": None, "source_updated_at": None, "age_seconds": None, "reason": None,
    })
    confidence = Confidence.from_dict(data.get("confidence") or {
        "level": "unknown", "score": None, "basis": "unknown", "summary": None,
    })
    if "recent" in flags and freshness.state not in {"current", "recent"}:
        raise AgentContextContractError("recent reason requires current or recent freshness")
    truth_level = "semantic_projection" if selected_reason == "semantic_match" else "runtime_trace"
    if selected_reason == "semantic_match":
        if confidence.score is None or confidence.basis != "retrieval_score":
            raise AgentContextContractError("semantic_match requires a normalized retrieval score basis")

    evidence_refs = _ids(data.get("evidence_refs"), "evidence_refs", 8, require_one=True)
    selection_state = _enum(data.get("selection_state"), "selection_state", {
        "included", "excluded", "clipped", "blocked",
    })
    classification = _enum(data.get("classification"), "classification", CLASSIFICATIONS)
    redaction_state = _enum(data.get("redaction_state"), "redaction_state", REDACTION_STATES)
    policy_blocked = _boolean(data.get("policy_blocked", False), "policy_blocked")
    source_disagreement = _boolean(data.get("source_disagreement", False), "source_disagreement")
    secure_boundary = _boolean(data.get("secure_mode_boundary", False), "secure_mode_boundary")
    provider_boundary = _boolean(data.get("provider_boundary", False), "provider_boundary")
    tool_boundary = _boolean(data.get("tool_boundary", False), "tool_boundary")
    if policy_blocked and selection_state != "blocked":
        raise AgentContextContractError("policy_blocked evidence must have blocked selection_state")

    observations: list[str] = []
    if confidence.level == "unknown":
        observations.append("confidence_unknown")
    elif confidence.level == "low":
        observations.append("confidence_low")
    if freshness.state == "unknown":
        observations.append("freshness_unknown")
    elif freshness.state in {"stale", "expired"}:
        observations.append("freshness_stale")
    if classification == "unknown":
        observations.append("classification_unknown")
    if source_disagreement:
        observations.append("source_disagreement")
    if policy_blocked:
        observations.append("classification_boundary")
    if secure_boundary:
        observations.append("secure_mode_boundary")
    if provider_boundary:
        observations.append("provider_boundary")
    if tool_boundary:
        observations.append("tool_boundary")
    if not observations:
        observations.append("routine_selection" if selection_state == "included" else "answer_pack_inspection")
    review = classify_review(observations)

    label = _plain_text(data.get("label"), "label", 120)
    summary = _optional_text(data.get("summary"), "summary", 500)
    preview = _optional_text(data.get("redacted_preview"), "redacted_preview", 300)
    exclusion_reason = _optional_text(data.get("exclusion_reason"), "exclusion_reason", 240)
    if selection_state == "blocked" or redaction_state in {"fully_redacted", "blocked"}:
        label = "Blocked context" if selection_state == "blocked" else "Redacted context"
        summary = None
        preview = None
        source = SourceRef(ref_type=source.ref_type, ref_id=source.ref_id)
    if selection_state == "blocked":
        redaction_state = "blocked"
        if policy_blocked:
            exclusion_reason = "Blocked by context policy."

    pinned = _boolean(data.get("pinned", "pinned" in flags), "pinned")
    if pinned != ("pinned" in flags):
        raise AgentContextContractError("pinned state and reason flag disagree")

    return ContextItem.from_dict({
        "schema_version": SCHEMA_VERSION,
        "kind": "agent.context_item",
        "context_id": data.get("context_id"),
        "created_at": data.get("created_at"),
        "truth_level": truth_level,
        "classification": classification,
        "redaction_state": redaction_state,
        "review": review.to_dict(),
        "context_kind": context_kind,
        "label": label,
        "source_ref": source.to_dict(),
        "selection_state": selection_state,
        "scope": data.get("scope"),
        "why_selected": {
            "code": reason_code,
            "summary": reason_summary,
            "evidence_refs": list(evidence_refs),
            "truth_level": truth_level,
        },
        "freshness": freshness.to_dict(),
        "confidence": confidence.to_dict(),
        "pinned": pinned,
        "removable": data.get("removable", True),
        "summary": summary,
        "redacted_preview": preview,
        "exclusion_reason": exclusion_reason,
        "token_estimate": data.get("token_estimate"),
        "source_revision_ref": data.get("source_revision_ref"),
        "parent_context_id": data.get("parent_context_id"),
    })


def build_context_items_from_evidence(values: Sequence[Mapping[str, Any]]) -> tuple["ContextItem", ...]:
    """Build a bounded batch and reject duplicate ContextItem identities."""

    raw_items = _sequence(values, "selection evidence batch", MAX_ITEMS)
    items = tuple(build_context_item_from_evidence(value) for value in raw_items)
    if len({item.context_id for item in items}) != len(items):
        raise AgentContextContractError("selection evidence batch contains duplicate context ids")
    return items


def _common(data: Mapping[str, Any], expected_kind: str) -> dict[str, Any]:
    _schema_and_kind(data, expected_kind)
    classification = _enum(data.get("classification"), "classification", CLASSIFICATIONS)
    redaction = _enum(data.get("redaction_state"), "redaction_state", REDACTION_STATES)
    _validate_redaction(classification, redaction)
    return {
        "created_at": _timestamp(data.get("created_at"), "created_at"),
        "truth_level": _enum(data.get("truth_level"), "truth_level", TRUTH_LEVELS),
        "classification": classification,
        "redaction_state": redaction,
        "review": ReviewDecision.from_dict(data.get("review")),
    }


@dataclass(frozen=True, slots=True)
class ContextItem:
    context_id: str
    created_at: str
    truth_level: str
    classification: str
    redaction_state: str
    review: ReviewDecision
    context_kind: str
    label: str
    source_ref: SourceRef
    selection_state: str
    scope: str
    why_selected: WhySelected
    freshness: Freshness
    confidence: Confidence
    pinned: bool
    removable: bool
    summary: str | None = None
    redacted_preview: str | None = None
    exclusion_reason: str | None = None
    token_estimate: int | None = None
    source_revision_ref: str | None = None
    parent_context_id: str | None = None

    @classmethod
    def create(cls, **values: Any) -> "ContextItem":
        values.setdefault("schema_version", SCHEMA_VERSION)
        values.setdefault("kind", "agent.context_item")
        return cls.from_dict(values)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ContextItem":
        data = _mapping(value, "context_item")
        common = _common(data, "agent.context_item")
        truth = common["truth_level"]
        if truth not in {"runtime_trace", "semantic_projection"}:
            raise AgentContextContractError("ContextItem truth_level cannot be evidence")
        selection = _enum(data.get("selection_state"), "selection_state", {"candidate", "included", "excluded", "clipped", "blocked"})
        exclusion = _optional_text(data.get("exclusion_reason"), "exclusion_reason", 240)
        if selection in {"excluded", "clipped", "blocked"} and not exclusion:
            raise AgentContextContractError("exclusion_reason is required for non-included context")
        if selection in {"candidate", "included"} and exclusion is not None:
            raise AgentContextContractError("exclusion_reason is not valid for selected context")
        preview = _optional_text(data.get("redacted_preview"), "redacted_preview", 300)
        if common["redaction_state"] in {"fully_redacted", "blocked"} and preview is not None:
            raise AgentContextContractError("redacted_preview is forbidden for this redaction state")
        if selection == "blocked" and common["redaction_state"] != "blocked":
            raise AgentContextContractError("blocked context requires blocked redaction_state")
        if common["redaction_state"] == "blocked" and selection != "blocked":
            raise AgentContextContractError("blocked redaction_state requires blocked context")
        context_kind = _enum(data.get("context_kind"), "context_kind", {
            "system_rule", "user_message", "pinned_document", "project", "repo", "roadmap", "memory",
            "rag", "tool_evidence", "import_summary", "other",
        })
        removable = _boolean(data.get("removable"), "removable")
        if not removable and context_kind != "system_rule":
            raise AgentContextContractError("non-removable context must be a system rule")
        result = cls(
            context_id=_id(data.get("context_id"), "context_id"),
            context_kind=context_kind,
            label=_plain_text(data.get("label"), "label", 120),
            source_ref=SourceRef.from_dict(data.get("source_ref")),
            selection_state=selection,
            scope=_enum(data.get("scope"), "scope", SCOPES),
            why_selected=WhySelected.from_dict(data.get("why_selected")),
            freshness=Freshness.from_dict(data.get("freshness")),
            confidence=Confidence.from_dict(data.get("confidence")),
            pinned=_boolean(data.get("pinned"), "pinned"),
            removable=removable,
            summary=_optional_text(data.get("summary"), "summary", 500),
            redacted_preview=preview,
            exclusion_reason=exclusion,
            token_estimate=_integer(data.get("token_estimate"), "token_estimate", nullable=True),
            source_revision_ref=_id(data.get("source_revision_ref"), "source_revision_ref") if data.get("source_revision_ref") is not None else None,
            parent_context_id=_id(data.get("parent_context_id"), "parent_context_id") if data.get("parent_context_id") is not None else None,
            **common,
        )
        _payload_budget(result.to_dict(), MAX_CONTEXT_ITEM_BYTES)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "kind": "agent.context_item", "context_id": self.context_id,
            "created_at": self.created_at, "truth_level": self.truth_level, "classification": self.classification,
            "redaction_state": self.redaction_state, "review": self.review.to_dict(), "context_kind": self.context_kind,
            "label": self.label, "source_ref": self.source_ref.to_dict(), "selection_state": self.selection_state,
            "scope": self.scope, "why_selected": self.why_selected.to_dict(), "freshness": self.freshness.to_dict(),
            "confidence": self.confidence.to_dict(), "pinned": self.pinned, "removable": self.removable,
            "summary": self.summary, "redacted_preview": self.redacted_preview, "exclusion_reason": self.exclusion_reason,
            "token_estimate": self.token_estimate, "source_revision_ref": self.source_revision_ref,
            "parent_context_id": self.parent_context_id,
        }

    def to_json(self) -> str:
        return _json(self.to_dict())


@dataclass(frozen=True, slots=True)
class ModelRoute:
    model_ref: str
    locality: str
    security_mode: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ModelRoute":
        data = _mapping(value, "model_route")
        result = cls(
            _id(data.get("model_ref"), "model_ref"),
            _enum(data.get("locality"), "model locality", {"local", "api"}),
            _enum(data.get("security_mode"), "security_mode", {"normal", "secure"}),
        )
        if result.security_mode == "secure" and result.locality != "local":
            raise AgentContextContractError("Secure Mode requires a local model route")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {"model_ref": self.model_ref, "locality": self.locality, "security_mode": self.security_mode}


@dataclass(frozen=True, slots=True)
class TokenBudget:
    total: int | None
    used: int | None
    remaining: int | None
    unit: str = "tokens"

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TokenBudget":
        data = _mapping(value, "token_budget")
        if data.get("unit") != "tokens":
            raise AgentContextContractError("token budget unit is invalid")
        result = cls(
            _integer(data.get("total"), "token total", nullable=True),
            _integer(data.get("used"), "token used", nullable=True),
            _integer(data.get("remaining"), "token remaining", nullable=True),
        )
        if result.total is not None and result.used is not None and result.used > result.total:
            raise AgentContextContractError("token budget is inconsistent")
        if None not in (result.total, result.used, result.remaining) and result.total != result.used + result.remaining:
            raise AgentContextContractError("token budget is inconsistent")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {"total": self.total, "used": self.used, "remaining": self.remaining, "unit": self.unit}


@dataclass(frozen=True, slots=True)
class ExcludedItem:
    context_id: str
    reason_code: str
    reason_summary: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExcludedItem":
        data = _mapping(value, "excluded_item")
        return cls(
            _id(data.get("context_id"), "excluded context_id"),
            _enum(data.get("reason_code"), "exclusion reason_code", {
                "budget", "stale", "duplicate", "policy", "conflict", "low_relevance", "user_removed", "other",
            }),
            _plain_text(data.get("reason_summary"), "exclusion reason_summary", 240),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"context_id": self.context_id, "reason_code": self.reason_code, "reason_summary": self.reason_summary}


@dataclass(frozen=True, slots=True)
class AnswerPackSummary:
    pack_id: str
    conversation_ref: str
    turn_ref: str
    phase: str
    model_route: ModelRoute
    token_budget: TokenBudget
    context_used_ratio: float | None
    items: tuple[ContextItem | str, ...]
    included_count: int
    excluded_count: int
    clipped_count: int
    stale_count: int
    sensitive_count: int
    excluded_items: tuple[ExcludedItem, ...]
    complete: bool
    created_at: str
    truth_level: str
    classification: str
    redaction_state: str
    review: ReviewDecision
    response_ref: str | None = None
    missing_expected_source_types: tuple[str, ...] = ()
    conflict_count: int = 0
    truncated: bool = False

    @classmethod
    def create(cls, **values: Any) -> "AnswerPackSummary":
        values.setdefault("schema_version", SCHEMA_VERSION)
        values.setdefault("kind", "agent.answer_pack_summary")
        return cls.from_dict(values)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AnswerPackSummary":
        data = _mapping(value, "answer_pack_summary")
        common = _common(data, "agent.answer_pack_summary")
        if common["truth_level"] != "runtime_trace":
            raise AgentContextContractError("AnswerPackSummary truth_level must be runtime_trace")
        raw_items = _sequence(data.get("items"), "items", MAX_ITEMS)
        items: list[ContextItem | str] = []
        for raw in raw_items:
            items.append(ContextItem.from_dict(raw) if isinstance(raw, Mapping) else _id(raw, "context_id ref"))
        excluded_items = tuple(ExcludedItem.from_dict(item) for item in _sequence(data.get("excluded_items"), "excluded_items", MAX_ITEMS))
        if len({item.context_id for item in excluded_items}) != len(excluded_items):
            raise AgentContextContractError("excluded_items contains duplicate context ids")
        phase = _enum(data.get("phase"), "phase", {"pre_generation", "post_generation"})
        response_ref = _id(data.get("response_ref"), "response_ref") if data.get("response_ref") is not None else None
        if phase == "post_generation" and response_ref is None:
            raise AgentContextContractError("post_generation pack requires response_ref")
        complete = _boolean(data.get("complete"), "complete")
        truncated = _boolean(data.get("truncated", False), "truncated")
        if complete and truncated:
            raise AgentContextContractError("a complete pack cannot be truncated")
        counts = {
            name: _integer(data.get(name), name)
            for name in ("included_count", "excluded_count", "clipped_count", "stale_count", "sensitive_count")
        }
        embedded = [item for item in items if isinstance(item, ContextItem)]
        if complete and len(embedded) != len(items):
            raise AgentContextContractError("a complete pack requires embedded ContextItems")
        if complete:
            if any(item.selection_state == "candidate" for item in embedded):
                raise AgentContextContractError("a complete pack cannot contain candidate context")
            actual = {
                "included_count": sum(item.selection_state == "included" for item in embedded),
                "excluded_count": sum(item.selection_state in {"excluded", "blocked"} for item in embedded),
                "clipped_count": sum(item.selection_state == "clipped" for item in embedded),
                "stale_count": sum(item.freshness.state in {"stale", "expired"} for item in embedded),
                "sensitive_count": sum(item.classification in {"sensitive", "secret"} for item in embedded),
            }
            if any(counts[name] != actual[name] for name in actual):
                raise AgentContextContractError("answer pack counts are inconsistent")
        route = ModelRoute.from_dict(data.get("model_route"))
        if route.security_mode == "normal" and any(
            item.selection_state == "included" and item.classification in {"sensitive", "secret", "unknown"}
            for item in embedded
        ):
            raise AgentContextContractError("normal mode cannot include restricted or unknown context")
        if embedded:
            effective = strongest_classification([item.classification for item in embedded])
            if common["classification"] != effective:
                raise AgentContextContractError("answer pack classification does not match its sources")
        result = cls(
            pack_id=_id(data.get("pack_id"), "pack_id"),
            conversation_ref=_id(data.get("conversation_ref"), "conversation_ref"),
            turn_ref=_id(data.get("turn_ref"), "turn_ref"),
            phase=phase,
            model_route=route,
            token_budget=TokenBudget.from_dict(data.get("token_budget")),
            context_used_ratio=_score(data.get("context_used_ratio"), "context_used_ratio", nullable=True),
            items=tuple(items),
            excluded_items=excluded_items,
            complete=complete,
            response_ref=response_ref,
            missing_expected_source_types=_ids(data.get("missing_expected_source_types", ()), "missing_expected_source_types"),
            conflict_count=_integer(data.get("conflict_count", 0), "conflict_count"),
            truncated=truncated,
            **counts,
            **common,
        )
        _payload_budget(result.to_dict(), MAX_ANSWER_PACK_BYTES)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "kind": "agent.answer_pack_summary", "pack_id": self.pack_id,
            "conversation_ref": self.conversation_ref, "turn_ref": self.turn_ref, "phase": self.phase,
            "model_route": self.model_route.to_dict(), "token_budget": self.token_budget.to_dict(),
            "context_used_ratio": self.context_used_ratio,
            "items": [item.to_dict() if isinstance(item, ContextItem) else item for item in self.items],
            "included_count": self.included_count, "excluded_count": self.excluded_count,
            "clipped_count": self.clipped_count, "stale_count": self.stale_count,
            "sensitive_count": self.sensitive_count, "excluded_items": [item.to_dict() for item in self.excluded_items],
            "complete": self.complete, "response_ref": self.response_ref,
            "missing_expected_source_types": list(self.missing_expected_source_types),
            "conflict_count": self.conflict_count, "truncated": self.truncated,
            "created_at": self.created_at, "truth_level": self.truth_level, "classification": self.classification,
            "redaction_state": self.redaction_state, "review": self.review.to_dict(),
        }

    def to_json(self) -> str:
        return _json(self.to_dict())


@dataclass(frozen=True, slots=True)
class MemoryInfluenceRecord:
    influence_id: str
    response_ref: str
    pack_id: str
    context_ids: tuple[str, ...]
    memory_refs: tuple[SourceRef, ...]
    project_refs: tuple[SourceRef, ...]
    source_refs: tuple[SourceRef, ...]
    influence_type: str
    reason_summary: str
    confidence: Confidence
    evidence_event_refs: tuple[str, ...]
    created_at: str
    truth_level: str
    classification: str
    redaction_state: str
    review: ReviewDecision
    answer_segment_refs: tuple[str, ...] = ()
    rank: int | None = None
    relevance_score: float | None = None
    freshness: Freshness | None = None

    @classmethod
    def create(cls, **values: Any) -> "MemoryInfluenceRecord":
        values.setdefault("schema_version", SCHEMA_VERSION)
        values.setdefault("kind", "agent.memory_influence_record")
        return cls.from_dict(values)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MemoryInfluenceRecord":
        data = _mapping(value, "memory_influence_record")
        common = _common(data, "agent.memory_influence_record")
        if common["truth_level"] not in {"runtime_trace", "semantic_projection"}:
            raise AgentContextContractError("MemoryInfluenceRecord truth_level cannot be evidence")
        memory_refs = tuple(SourceRef.from_dict(item) for item in _sequence(data.get("memory_refs"), "memory_refs", MAX_REFS))
        project_refs = tuple(SourceRef.from_dict(item) for item in _sequence(data.get("project_refs"), "project_refs", MAX_REFS))
        source_refs = tuple(SourceRef.from_dict(item) for item in _sequence(data.get("source_refs"), "source_refs", MAX_REFS))
        if any(ref.ref_type != "memory" for ref in memory_refs):
            raise AgentContextContractError("memory_refs must contain memory SourceRefs")
        if any(ref.ref_type != "project" for ref in project_refs):
            raise AgentContextContractError("project_refs must contain project SourceRefs")
        if not memory_refs and not project_refs:
            raise AgentContextContractError("influence requires a memory or project ref")
        influence_type = _enum(data.get("influence_type"), "influence_type", {
            "retrieved", "selected_for_context", "cited_support", "conflict", "excluded", "writeback_candidate",
        })
        review: ReviewDecision = common["review"]
        if influence_type == "conflict" and "conflict" not in review.reason_codes:
            raise AgentContextContractError("conflict influence requires conflict review")
        if influence_type == "writeback_candidate" and "user_visible_writeback" not in review.reason_codes:
            raise AgentContextContractError("writeback influence requires writeback review")
        reason = _plain_text(data.get("reason_summary"), "reason_summary", 500)
        lowered = reason.lower()
        if any(fragment in lowered for fragment in ("chain of thought", "hidden reasoning", "caused the model")):
            raise AgentContextContractError("reason_summary claims hidden model causality")
        confidence = Confidence.from_dict(data.get("confidence"))
        relevance = _score(data.get("relevance_score"), "relevance_score", nullable=True)
        if relevance is not None and confidence.basis == "unknown":
            raise AgentContextContractError("relevance_score requires a documented confidence basis")
        result = cls(
            influence_id=_id(data.get("influence_id"), "influence_id"),
            response_ref=_id(data.get("response_ref"), "response_ref"),
            pack_id=_id(data.get("pack_id"), "pack_id"),
            context_ids=_ids(data.get("context_ids"), "context_ids", require_one=True),
            memory_refs=memory_refs,
            project_refs=project_refs,
            source_refs=source_refs,
            influence_type=influence_type,
            reason_summary=reason,
            confidence=confidence,
            evidence_event_refs=_ids(data.get("evidence_event_refs"), "evidence_event_refs", MAX_EVIDENCE_REFS, require_one=True),
            answer_segment_refs=_ids(data.get("answer_segment_refs", ()), "answer_segment_refs"),
            rank=_integer(data.get("rank"), "rank", minimum=1, nullable=True),
            relevance_score=relevance,
            freshness=Freshness.from_dict(data.get("freshness")) if data.get("freshness") is not None else None,
            **common,
        )
        _payload_budget(result.to_dict(), MAX_INFLUENCE_BYTES)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "kind": "agent.memory_influence_record",
            "influence_id": self.influence_id, "response_ref": self.response_ref, "pack_id": self.pack_id,
            "context_ids": list(self.context_ids), "memory_refs": [ref.to_dict() for ref in self.memory_refs],
            "project_refs": [ref.to_dict() for ref in self.project_refs],
            "source_refs": [ref.to_dict() for ref in self.source_refs], "influence_type": self.influence_type,
            "reason_summary": self.reason_summary, "confidence": self.confidence.to_dict(),
            "evidence_event_refs": list(self.evidence_event_refs), "answer_segment_refs": list(self.answer_segment_refs),
            "rank": self.rank, "relevance_score": self.relevance_score,
            "freshness": self.freshness.to_dict() if self.freshness else None,
            "created_at": self.created_at, "truth_level": self.truth_level, "classification": self.classification,
            "redaction_state": self.redaction_state, "review": self.review.to_dict(),
        }

    def to_json(self) -> str:
        return _json(self.to_dict())


@dataclass(frozen=True, slots=True)
class LearnedRuleCandidate:
    candidate_id: str
    status: str
    candidate_type: str
    scope: str
    target_ref: SourceRef
    summary: str
    evidence_feedback_refs: tuple[str, ...]
    requires_review: bool

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LearnedRuleCandidate":
        data = _mapping(value, "learned_rule_candidate")
        if data.get("status") != "proposed":
            raise AgentContextContractError("learned rule status must be proposed")
        return cls(
            candidate_id=_id(data.get("candidate_id"), "candidate_id"),
            status="proposed",
            candidate_type=_enum(data.get("candidate_type"), "candidate_type", {"prefer", "exclude", "confirm", "hide", "display_label"}),
            scope=_enum(data.get("scope"), "candidate scope", SCOPES),
            target_ref=SourceRef.from_dict(data.get("target_ref")),
            summary=_plain_text(data.get("summary"), "candidate summary", 500),
            evidence_feedback_refs=_ids(data.get("evidence_feedback_refs"), "evidence_feedback_refs", require_one=True),
            requires_review=_boolean(data.get("requires_review"), "requires_review"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id, "status": self.status, "candidate_type": self.candidate_type,
            "scope": self.scope, "target_ref": self.target_ref.to_dict(), "summary": self.summary,
            "evidence_feedback_refs": list(self.evidence_feedback_refs), "requires_review": self.requires_review,
        }


@dataclass(frozen=True, slots=True)
class UserContextFeedback:
    feedback_id: str
    context_id: str
    target_ref: ContextItem | SourceRef
    action: str
    scope: str
    actor_ref: str
    result: str
    policy_effect: str
    created_at: str
    truth_level: str
    classification: str
    redaction_state: str
    review: ReviewDecision
    reason: str | None = None
    proposed_label: str | None = None
    learned_rule_candidate: LearnedRuleCandidate | None = None

    @classmethod
    def create(cls, **values: Any) -> "UserContextFeedback":
        values.setdefault("schema_version", SCHEMA_VERSION)
        values.setdefault("kind", "agent.user_context_feedback")
        return cls.from_dict(values)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "UserContextFeedback":
        data = _mapping(value, "user_context_feedback")
        common = _common(data, "agent.user_context_feedback")
        if common["truth_level"] != "runtime_trace":
            raise AgentContextContractError("UserContextFeedback truth_level must be runtime_trace")
        target_data = _mapping(data.get("target_ref"), "target_ref")
        target: ContextItem | SourceRef
        if target_data.get("kind") == "agent.context_item":
            target = ContextItem.from_dict(target_data)
            if common["classification"] != target.classification:
                raise AgentContextContractError("feedback classification does not match its target")
        else:
            target = SourceRef.from_dict(target_data)
        action = _enum(data.get("action"), "action", {"pin", "remove", "approve", "hide", "rename"})
        proposed_label = _optional_text(data.get("proposed_label"), "proposed_label", 120)
        if action == "rename" and not proposed_label:
            raise AgentContextContractError("rename requires proposed_label")
        if action != "rename" and proposed_label is not None:
            raise AgentContextContractError("proposed_label is valid only for rename")
        if data.get("policy_effect") != "none":
            raise AgentContextContractError("feedback policy_effect must remain none")
        candidate = LearnedRuleCandidate.from_dict(data.get("learned_rule_candidate")) if data.get("learned_rule_candidate") is not None else None
        result_state = _enum(data.get("result"), "result", {"recorded", "candidate_created", "review_required", "rejected"})
        if result_state == "candidate_created" and candidate is None:
            raise AgentContextContractError("candidate_created requires a proposed candidate")
        if candidate is not None:
            expected_type = {"pin": "prefer", "remove": "exclude", "approve": "confirm", "hide": "hide", "rename": "display_label"}[action]
            if candidate.candidate_type != expected_type:
                raise AgentContextContractError("learned candidate type does not match feedback action")
            if candidate.scope != data.get("scope"):
                raise AgentContextContractError("learned candidate scope does not match feedback scope")
            if candidate.requires_review and not common["review"].required:
                raise AgentContextContractError("review-required candidate requires review")
        if result_state == "review_required" and not common["review"].required:
            raise AgentContextContractError("review_required result requires review reasons")
        if data.get("scope") == "global" and candidate is not None:
            if not candidate.requires_review or "user_visible_writeback" not in common["review"].reason_codes:
                raise AgentContextContractError("global learned rules require writeback review")
        if candidate is not None and common["classification"] in {"sensitive", "secret", "unknown"}:
            if "policy_risk" not in common["review"].reason_codes:
                raise AgentContextContractError("restricted learned rules require policy review")
        result = cls(
            feedback_id=_id(data.get("feedback_id"), "feedback_id"),
            context_id=_id(data.get("context_id"), "context_id"),
            target_ref=target,
            action=action,
            scope=_enum(data.get("scope"), "scope", SCOPES),
            actor_ref=_id(data.get("actor_ref"), "actor_ref"),
            result=result_state,
            policy_effect="none",
            reason=_optional_text(data.get("reason"), "reason", 500),
            proposed_label=proposed_label,
            learned_rule_candidate=candidate,
            **common,
        )
        _payload_budget(result.to_dict(), MAX_FEEDBACK_BYTES)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "kind": "agent.user_context_feedback",
            "feedback_id": self.feedback_id, "context_id": self.context_id,
            "target_ref": self.target_ref.to_dict(), "action": self.action, "scope": self.scope,
            "actor_ref": self.actor_ref, "result": self.result, "policy_effect": self.policy_effect,
            "reason": self.reason, "proposed_label": self.proposed_label,
            "learned_rule_candidate": self.learned_rule_candidate.to_dict() if self.learned_rule_candidate else None,
            "created_at": self.created_at, "truth_level": self.truth_level, "classification": self.classification,
            "redaction_state": self.redaction_state, "review": self.review.to_dict(),
        }

    def to_json(self) -> str:
        return _json(self.to_dict())


Payload = ContextItem | AnswerPackSummary | MemoryInfluenceRecord | UserContextFeedback


def validate_payload(value: Payload | Mapping[str, Any]) -> Payload:
    if isinstance(value, (ContextItem, AnswerPackSummary, MemoryInfluenceRecord, UserContextFeedback)):
        value = value.to_dict()
    data = _mapping(value, "payload")
    kind = data.get("kind")
    validators = {
        "agent.context_item": ContextItem.from_dict,
        "agent.answer_pack_summary": AnswerPackSummary.from_dict,
        "agent.memory_influence_record": MemoryInfluenceRecord.from_dict,
        "agent.user_context_feedback": UserContextFeedback.from_dict,
    }
    validator = validators.get(kind)
    if validator is None:
        raise AgentContextContractError("payload kind is invalid")
    return validator(data)


def payload_from_json(value: str) -> Payload:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AgentContextContractError("payload JSON is invalid") from exc
    return validate_payload(parsed)


def payload_to_json(value: Payload | Mapping[str, Any]) -> str:
    return _json(validate_payload(value).to_dict())


def project_to_ai_lens(value: Payload | Mapping[str, Any]) -> dict[str, Any] | None:
    """Return a bounded service-neutral AI Lens projection, or None for feedback."""

    payload = validate_payload(value)
    if isinstance(payload, UserContextFeedback):
        return None
    base = {
        "truth_level": payload.truth_level,
        "classification": payload.classification,
        "redaction_state": payload.redaction_state,
    }
    if isinstance(payload, ContextItem):
        base.update({
            "event_type": "context_item_selected" if payload.selection_state == "included" else "context_item_excluded",
            "context_id": payload.context_id,
            "source_ref": payload.source_ref.to_dict(),
            "selection_state": payload.selection_state,
        })
    elif isinstance(payload, AnswerPackSummary):
        base.update({
            "event_type": "context_pack_composed", "pack_id": payload.pack_id,
            "included_count": payload.included_count, "excluded_count": payload.excluded_count,
            "clipped_count": payload.clipped_count, "stale_count": payload.stale_count,
            "sensitive_count": payload.sensitive_count, "complete": payload.complete,
        })
    else:
        event_type = "source_conflict_detected" if payload.influence_type == "conflict" else (
            "memory_hit" if payload.influence_type == "retrieved" else "answer_provenance_summary"
        )
        base.update({
            "event_type": event_type, "influence_id": payload.influence_id, "pack_id": payload.pack_id,
            "context_ids": list(payload.context_ids), "source_refs": [ref.to_dict() for ref in payload.source_refs],
            "evidence_event_refs": list(payload.evidence_event_refs),
        })
    _payload_budget(base, MAX_CONTEXT_ITEM_BYTES)
    return base


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = [
    "AgentContextContractError", "AnswerPackSummary", "Confidence", "ContextItem", "Freshness",
    "LearnedRuleCandidate", "MemoryInfluenceRecord", "ReviewDecision", "SCHEMA_VERSION", "SourceRef",
    "UserContextFeedback", "WhySelected", "build_context_item_from_evidence",
    "build_context_items_from_evidence", "build_review_decision", "classify_review", "payload_from_json",
    "payload_to_json", "project_to_ai_lens", "strongest_classification", "validate_payload",
]
