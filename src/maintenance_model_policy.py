"""Runtime policy for the local maintenance model lane.

Gemma4 E4B is intended as a bounded Inbox/Memory maintenance worker: it gets
small prepared packets, decides gates, and prepares abstractions. It is not the
chat default and it never grants direct truth writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any, Mapping


MAINTENANCE_POLICY_SCHEMA = "odysseus.maintenance_model_policy.v1"
DEFAULT_MAINTENANCE_MODEL = "gemma4:e4b"
DEFAULT_MAINTENANCE_PROVIDER = "local_ollama"
DEFAULT_FALLBACK_MODEL_REF = "api-review-model"
DEFAULT_TOKEN_BUDGET = 1200
DEFAULT_MAX_INPUT_CHARS = 6000
DEFAULT_CHUNK_BUDGET = 4
DEFAULT_SOURCE_REF_BUDGET = 4
DEFAULT_LATENCY_BUDGET_MS = 45_000
DEFAULT_MAX_QUEUE_CONCURRENCY = 1

_SAFE_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SENSITIVE_CLASSIFICATIONS = {"sensitive", "secret"}
_FALLBACK_GATE_REASONS = {
    "schema_invalid",
    "json_invalid",
    "low_confidence",
    "weak_evidence",
    "timeout",
    "model_unavailable",
}


class MaintenanceModelPolicyError(ValueError):
    """Raised when a maintenance model policy payload is unsafe."""


class MaintenanceWorkload(StrEnum):
    INBOX_TRIAGE = "inbox_triage"
    SENSITIVITY_CLASSIFICATION = "sensitivity_classification"
    MEMORY_WRITE_INTENT = "memory_write_intent"
    RAPTORGRAPH_ABSTRACTION = "raptorgraph_abstraction"
    RAPTORGRAPH_MAINTENANCE = "raptorgraph_maintenance"
    VOICE_TRANSCRIPT = "voice_transcript"
    EXPORT_CONVERSION_PREFLIGHT = "export_conversion_preflight"
    LONG_DOCUMENT_PREFLIGHT = "long_document_preflight"


class MaintenanceRouteAction(StrEnum):
    STAY_ON_MAINTENANCE_MODEL = "stay_on_maintenance_model"
    PREPARE_SMALLER_PACKET = "prepare_smaller_packet"
    ROUTE_TO_FALLBACK_MODEL = "route_to_fallback_model"
    ROUTE_TO_REVIEW = "route_to_review"
    STOP = "stop"


@dataclass(frozen=True, slots=True)
class MaintenanceModelProfile:
    model_ref: str = DEFAULT_MAINTENANCE_MODEL
    provider: str = DEFAULT_MAINTENANCE_PROVIDER
    fallback_model_ref: str = DEFAULT_FALLBACK_MODEL_REF
    token_budget: int = DEFAULT_TOKEN_BUDGET
    max_input_chars: int = DEFAULT_MAX_INPUT_CHARS
    chunk_budget: int = DEFAULT_CHUNK_BUDGET
    source_ref_budget: int = DEFAULT_SOURCE_REF_BUDGET
    latency_budget_ms: int = DEFAULT_LATENCY_BUDGET_MS
    max_queue_concurrency: int = DEFAULT_MAX_QUEUE_CONCURRENCY
    api_fallback_enabled: bool = False
    truth_write_allowed: bool = False

    @classmethod
    def create(cls, **overrides: Any) -> "MaintenanceModelProfile":
        profile = cls(**overrides)
        if profile.truth_write_allowed:
            raise MaintenanceModelPolicyError("maintenance model must not allow truth writes")
        _validate_ref(profile.model_ref, "model_ref")
        _validate_ref(profile.provider, "provider")
        _validate_ref(profile.fallback_model_ref, "fallback_model_ref")
        for field, value in (
            ("token_budget", profile.token_budget),
            ("max_input_chars", profile.max_input_chars),
            ("chunk_budget", profile.chunk_budget),
            ("source_ref_budget", profile.source_ref_budget),
            ("latency_budget_ms", profile.latency_budget_ms),
            ("max_queue_concurrency", profile.max_queue_concurrency),
        ):
            if int(value) <= 0:
                raise MaintenanceModelPolicyError(f"{field} must be > 0")
        if profile.token_budget > DEFAULT_TOKEN_BUDGET:
            raise MaintenanceModelPolicyError("Gemma maintenance token budget must stay <= 1200")
        if profile.max_queue_concurrency != 1:
            raise MaintenanceModelPolicyError("Gemma maintenance queue concurrency must stay 1")
        return profile

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": MAINTENANCE_POLICY_SCHEMA,
            "model_ref": self.model_ref,
            "provider": self.provider,
            "fallback_model_ref": self.fallback_model_ref,
            "token_budget": self.token_budget,
            "max_input_chars": self.max_input_chars,
            "chunk_budget": self.chunk_budget,
            "source_ref_budget": self.source_ref_budget,
            "latency_budget_ms": self.latency_budget_ms,
            "max_queue_concurrency": self.max_queue_concurrency,
            "api_fallback_enabled": self.api_fallback_enabled,
            "truth_write_allowed": False,
            "role": "local_inbox_memory_maintenance",
        }


@dataclass(frozen=True, slots=True)
class MaintenanceRouteDecision:
    workload: MaintenanceWorkload
    action: MaintenanceRouteAction
    model_ref: str
    provider: str
    fallback_model_ref: str
    local_only_required: bool
    api_escalation_allowed: bool
    review_required: bool
    reason: str
    token_budget: int
    max_input_chars: int
    raw_content_allowed: bool = False
    truth_write_allowed: bool = False
    schema: str = MAINTENANCE_POLICY_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "workload": self.workload.value,
            "action": self.action.value,
            "model_ref": self.model_ref,
            "provider": self.provider,
            "fallback_model_ref": self.fallback_model_ref,
            "local_only_required": self.local_only_required,
            "api_escalation_allowed": self.api_escalation_allowed,
            "review_required": self.review_required,
            "reason": self.reason,
            "token_budget": self.token_budget,
            "max_input_chars": self.max_input_chars,
            "raw_content_allowed": False,
            "truth_write_allowed": False,
        }


def default_maintenance_model_profile() -> MaintenanceModelProfile:
    return MaintenanceModelProfile.create()


def maintenance_model_profile_from_settings(settings: Mapping[str, Any] | None) -> MaintenanceModelProfile:
    payload = dict(settings or {})
    return MaintenanceModelProfile.create(
        model_ref=payload.get("maintenance_model_ref") or DEFAULT_MAINTENANCE_MODEL,
        provider=payload.get("maintenance_model_provider") or DEFAULT_MAINTENANCE_PROVIDER,
        fallback_model_ref=payload.get("maintenance_model_fallback_ref") or DEFAULT_FALLBACK_MODEL_REF,
        token_budget=payload.get("maintenance_model_token_budget") or DEFAULT_TOKEN_BUDGET,
        max_input_chars=payload.get("maintenance_model_max_input_chars") or DEFAULT_MAX_INPUT_CHARS,
        chunk_budget=payload.get("maintenance_model_chunk_budget") or DEFAULT_CHUNK_BUDGET,
        source_ref_budget=payload.get("maintenance_model_source_ref_budget") or DEFAULT_SOURCE_REF_BUDGET,
        latency_budget_ms=payload.get("maintenance_model_latency_budget_ms") or DEFAULT_LATENCY_BUDGET_MS,
        api_fallback_enabled=bool(payload.get("maintenance_model_api_fallback_enabled", False)),
        truth_write_allowed=False,
    )


def plan_maintenance_model_route(
    *,
    workload: MaintenanceWorkload | str,
    classification: str = "private",
    dsgvo_mode: bool = False,
    input_chars: int = 0,
    chunk_count: int = 1,
    source_ref_count: int = 1,
    confidence: float = 1.0,
    extraction_status: str = "completed",
    api_escalation_allowed: bool = True,
    fallback_gate_reason: str = "",
    profile: MaintenanceModelProfile | Mapping[str, Any] | None = None,
) -> MaintenanceRouteDecision:
    model_profile = _coerce_profile(profile)
    normalized_workload = _normalize_workload(workload)
    normalized_classification = _token(classification, fallback="private")
    local_only = bool(dsgvo_mode) or normalized_classification in _SENSITIVE_CLASSIFICATIONS
    bounded = (
        int(input_chars or 0) <= model_profile.max_input_chars
        and int(chunk_count or 1) <= model_profile.chunk_budget
        and int(source_ref_count or 1) <= model_profile.source_ref_budget
    )
    partial = _token(extraction_status, fallback="unknown") in {
        "partial",
        "metadata_only",
        "unsupported",
        "failed",
        "blocked",
        "needs_review",
    }
    low_confidence = float(confidence or 0.0) < 0.72
    fallback_reason = _token(fallback_gate_reason, fallback="")

    if not bounded:
        return _decision(
            normalized_workload,
            model_profile,
            action=MaintenanceRouteAction.PREPARE_SMALLER_PACKET,
            local_only=local_only,
            api_allowed=False if local_only else bool(api_escalation_allowed),
            review_required=True,
            reason="maintenance_packet_exceeds_budget",
        )
    if partial or low_confidence:
        return _decision(
            normalized_workload,
            model_profile,
            action=MaintenanceRouteAction.ROUTE_TO_REVIEW,
            local_only=local_only,
            api_allowed=False if local_only else bool(api_escalation_allowed),
            review_required=True,
            reason="maintenance_review_required",
        )
    if (
        model_profile.api_fallback_enabled
        and not local_only
        and bool(api_escalation_allowed)
        and fallback_reason in _FALLBACK_GATE_REASONS
    ):
        return _decision(
            normalized_workload,
            model_profile,
            action=MaintenanceRouteAction.ROUTE_TO_FALLBACK_MODEL,
            local_only=False,
            api_allowed=True,
            review_required=True,
            reason=f"fallback_gate:{fallback_reason}",
        )
    return _decision(
        normalized_workload,
        model_profile,
        action=MaintenanceRouteAction.STAY_ON_MAINTENANCE_MODEL,
        local_only=local_only,
        api_allowed=False if local_only else bool(api_escalation_allowed),
        review_required=False,
        reason="maintenance_model_default",
    )


def _decision(
    workload: MaintenanceWorkload,
    profile: MaintenanceModelProfile,
    *,
    action: MaintenanceRouteAction,
    local_only: bool,
    api_allowed: bool,
    review_required: bool,
    reason: str,
) -> MaintenanceRouteDecision:
    return MaintenanceRouteDecision(
        workload=workload,
        action=action,
        model_ref=profile.model_ref,
        provider=profile.provider,
        fallback_model_ref=profile.fallback_model_ref,
        local_only_required=local_only,
        api_escalation_allowed=api_allowed,
        review_required=review_required,
        reason=reason,
        token_budget=profile.token_budget,
        max_input_chars=profile.max_input_chars,
    )


def _coerce_profile(profile: MaintenanceModelProfile | Mapping[str, Any] | None) -> MaintenanceModelProfile:
    if profile is None:
        return default_maintenance_model_profile()
    if isinstance(profile, MaintenanceModelProfile):
        return profile
    if isinstance(profile, Mapping):
        return MaintenanceModelProfile.create(**dict(profile))
    raise MaintenanceModelPolicyError("profile must be a MaintenanceModelProfile or mapping")


def _normalize_workload(value: MaintenanceWorkload | str) -> MaintenanceWorkload:
    if isinstance(value, MaintenanceWorkload):
        return value
    token = _token(value, fallback="")
    try:
        return MaintenanceWorkload(token)
    except ValueError as exc:
        raise MaintenanceModelPolicyError("unsupported maintenance workload") from exc


def _validate_ref(value: Any, field: str) -> None:
    text = str(value or "").strip()
    if not text:
        raise MaintenanceModelPolicyError(f"{field} must not be empty")
    lowered = text.lower()
    if any(term in lowered for term in ("secret", "token", "password", "truth-write", "global-authority")):
        raise MaintenanceModelPolicyError(f"{field} contains forbidden maintenance term")


def _token(value: Any, *, fallback: str) -> str:
    token = str(value or fallback).strip().lower().replace("-", "_").replace("/", "_").replace(" ", "_")
    return token if _SAFE_TOKEN_RE.fullmatch(token) else fallback
