"""Runtime policy for the local Gemma 3 maintenance-model lane.

The exact ``gemma3:4b`` model is a bounded Inbox/Memory maintenance worker: it
gets small prepared packets, decides gates, and prepares abstractions. It is
not the chat default, cannot fall back to an API model, and never grants direct
truth writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any, Mapping


MAINTENANCE_POLICY_SCHEMA = "odysseus.maintenance_model_policy.v2"
DEFAULT_MAINTENANCE_MODEL = "gemma3:4b"
DEFAULT_MAINTENANCE_PROVIDER = "local_ollama"
DEFAULT_FALLBACK_MODEL_REF = "api-review-model"
DEFAULT_TOKEN_BUDGET = 1200
DEFAULT_MAX_INPUT_CHARS = 6000
DEFAULT_CHUNK_BUDGET = 4
DEFAULT_SOURCE_REF_BUDGET = 4
DEFAULT_LATENCY_BUDGET_MS = 45_000
DEFAULT_MAX_QUEUE_CONCURRENCY = 1

_LEGACY_MAINTENANCE_MODELS = {"gemma4:e4b"}

_SAFE_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SENSITIVE_CLASSIFICATIONS = {"sensitive", "secret"}


class MaintenanceModelPolicyError(ValueError):
    """Raised when a maintenance model policy payload is unsafe."""


class MaintenanceModelRole(StrEnum):
    MAINTENANCE = "maintenance"


class MaintenanceEligibilityReason(StrEnum):
    ELIGIBLE = "eligible"
    MODEL_MISMATCH = "model_mismatch"
    PROVIDER_MISMATCH = "provider_mismatch"
    ROLE_UNTYPED_OR_FORBIDDEN = "role_untyped_or_forbidden"
    AUTHORITY_FLAG_INVALID = "authority_flag_invalid"
    FALLBACK_FORBIDDEN = "fallback_forbidden"
    TRUTH_WRITE_FORBIDDEN = "truth_write_forbidden"


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
class MaintenanceModelEligibilityDecision:
    eligible: bool
    reason: MaintenanceEligibilityReason
    model_scope: str
    provider_scope: str
    role_scope: str
    fallback_allowed: bool = False
    truth_write_allowed: bool = False
    schema: str = "odysseus.maintenance_model_eligibility.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "eligible": self.eligible,
            "reason": self.reason.value,
            "model_scope": self.model_scope,
            "provider_scope": self.provider_scope,
            "role_scope": self.role_scope,
            "fallback_allowed": False,
            "truth_write_allowed": False,
        }


@dataclass(frozen=True, slots=True)
class MaintenanceModelProfile:
    role: MaintenanceModelRole = MaintenanceModelRole.MAINTENANCE
    model_ref: str = DEFAULT_MAINTENANCE_MODEL
    provider: str = DEFAULT_MAINTENANCE_PROVIDER
    fallback_model_ref: str = DEFAULT_FALLBACK_MODEL_REF
    token_budget: int = DEFAULT_TOKEN_BUDGET
    max_input_chars: int = DEFAULT_MAX_INPUT_CHARS
    chunk_budget: int = DEFAULT_CHUNK_BUDGET
    source_ref_budget: int = DEFAULT_SOURCE_REF_BUDGET
    latency_budget_ms: int = DEFAULT_LATENCY_BUDGET_MS
    max_queue_concurrency: int = DEFAULT_MAX_QUEUE_CONCURRENCY
    runtime_enabled: bool = False
    fallback_allowed: bool = False
    truth_write_allowed: bool = False

    @classmethod
    def create(cls, **overrides: Any) -> "MaintenanceModelProfile":
        values = dict(overrides)
        if "role" in values and not isinstance(values["role"], MaintenanceModelRole):
            try:
                values["role"] = MaintenanceModelRole(values["role"])
            except (TypeError, ValueError) as exc:
                raise MaintenanceModelPolicyError("role must be maintenance") from exc
        profile = cls(**values)
        if profile.role is not MaintenanceModelRole.MAINTENANCE:
            raise MaintenanceModelPolicyError("role must be maintenance")
        if profile.model_ref != DEFAULT_MAINTENANCE_MODEL:
            raise MaintenanceModelPolicyError("maintenance model_ref must be exactly gemma3:4b")
        for field, value in (
            ("runtime_enabled", profile.runtime_enabled),
            ("fallback_allowed", profile.fallback_allowed),
            ("truth_write_allowed", profile.truth_write_allowed),
        ):
            if not isinstance(value, bool):
                raise MaintenanceModelPolicyError(f"{field} must be a boolean")
        if profile.fallback_allowed:
            raise MaintenanceModelPolicyError("maintenance model must not allow fallback")
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
            if isinstance(value, bool) or not isinstance(value, int):
                raise MaintenanceModelPolicyError(f"{field} must be an integer")
            if value <= 0:
                raise MaintenanceModelPolicyError(f"{field} must be > 0")
        for field, value, upper_bound in (
            ("token_budget", profile.token_budget, DEFAULT_TOKEN_BUDGET),
            ("max_input_chars", profile.max_input_chars, DEFAULT_MAX_INPUT_CHARS),
            ("chunk_budget", profile.chunk_budget, DEFAULT_CHUNK_BUDGET),
            ("source_ref_budget", profile.source_ref_budget, DEFAULT_SOURCE_REF_BUDGET),
            ("latency_budget_ms", profile.latency_budget_ms, DEFAULT_LATENCY_BUDGET_MS),
        ):
            if value > upper_bound:
                raise MaintenanceModelPolicyError(f"{field} must stay <= {upper_bound}")
        if profile.max_queue_concurrency != 1:
            raise MaintenanceModelPolicyError("Gemma 3 maintenance queue concurrency must stay 1")
        return profile

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": MAINTENANCE_POLICY_SCHEMA,
            "role": self.role.value,
            "model_ref": self.model_ref,
            "provider": self.provider,
            "fallback_model_ref": self.fallback_model_ref,
            "token_budget": self.token_budget,
            "max_input_chars": self.max_input_chars,
            "chunk_budget": self.chunk_budget,
            "source_ref_budget": self.source_ref_budget,
            "latency_budget_ms": self.latency_budget_ms,
            "max_queue_concurrency": self.max_queue_concurrency,
            "runtime_enabled": self.runtime_enabled,
            "fallback_allowed": False,
            "truth_write_allowed": False,
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


def evaluate_maintenance_model_eligibility(
    *,
    model_ref: Any,
    provider: Any,
    role: Any,
    fallback_requested: Any = False,
    truth_write_requested: Any = False,
) -> MaintenanceModelEligibilityDecision:
    """Return a content-free, fail-closed decision for scheduler admission."""

    model_matches = isinstance(model_ref, str) and model_ref == DEFAULT_MAINTENANCE_MODEL
    provider_matches = isinstance(provider, str) and provider == DEFAULT_MAINTENANCE_PROVIDER
    role_matches = isinstance(role, MaintenanceModelRole) and role is MaintenanceModelRole.MAINTENANCE
    scopes = {
        "model_scope": "gemma3_4b" if model_matches else "other",
        "provider_scope": "local_ollama" if provider_matches else "other",
        "role_scope": "maintenance" if role_matches else "rejected",
    }
    if not model_matches:
        return _eligibility(False, MaintenanceEligibilityReason.MODEL_MISMATCH, **scopes)
    if not provider_matches:
        return _eligibility(False, MaintenanceEligibilityReason.PROVIDER_MISMATCH, **scopes)
    if not role_matches:
        return _eligibility(False, MaintenanceEligibilityReason.ROLE_UNTYPED_OR_FORBIDDEN, **scopes)
    if not isinstance(fallback_requested, bool) or not isinstance(truth_write_requested, bool):
        return _eligibility(False, MaintenanceEligibilityReason.AUTHORITY_FLAG_INVALID, **scopes)
    if fallback_requested:
        return _eligibility(False, MaintenanceEligibilityReason.FALLBACK_FORBIDDEN, **scopes)
    if truth_write_requested:
        return _eligibility(False, MaintenanceEligibilityReason.TRUTH_WRITE_FORBIDDEN, **scopes)
    return _eligibility(True, MaintenanceEligibilityReason.ELIGIBLE, **scopes)


def maintenance_model_profile_from_settings(settings: Mapping[str, Any] | None) -> MaintenanceModelProfile:
    payload = dict(settings or {})
    model_ref = payload.get("maintenance_model_ref", DEFAULT_MAINTENANCE_MODEL)
    if isinstance(model_ref, str) and model_ref in _LEGACY_MAINTENANCE_MODELS:
        model_ref = DEFAULT_MAINTENANCE_MODEL
    return MaintenanceModelProfile.create(
        model_ref=model_ref,
        provider=payload.get("maintenance_model_provider") or DEFAULT_MAINTENANCE_PROVIDER,
        fallback_model_ref=payload.get("maintenance_model_fallback_ref") or DEFAULT_FALLBACK_MODEL_REF,
        token_budget=payload.get("maintenance_model_token_budget", DEFAULT_TOKEN_BUDGET),
        max_input_chars=payload.get("maintenance_model_max_input_chars", DEFAULT_MAX_INPUT_CHARS),
        chunk_budget=payload.get("maintenance_model_chunk_budget", DEFAULT_CHUNK_BUDGET),
        source_ref_budget=payload.get("maintenance_model_source_ref_budget", DEFAULT_SOURCE_REF_BUDGET),
        latency_budget_ms=payload.get("maintenance_model_latency_budget_ms", DEFAULT_LATENCY_BUDGET_MS),
        runtime_enabled=payload.get("maintenance_runtime_enabled", False),
        fallback_allowed=False,
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
    if not bounded:
        return _decision(
            normalized_workload,
            model_profile,
            action=MaintenanceRouteAction.PREPARE_SMALLER_PACKET,
            local_only=local_only,
            api_allowed=False,
            review_required=True,
            reason="maintenance_packet_exceeds_budget",
        )
    if partial or low_confidence:
        return _decision(
            normalized_workload,
            model_profile,
            action=MaintenanceRouteAction.ROUTE_TO_REVIEW,
            local_only=local_only,
            api_allowed=False,
            review_required=True,
            reason="maintenance_review_required",
        )
    return _decision(
        normalized_workload,
        model_profile,
        action=MaintenanceRouteAction.STAY_ON_MAINTENANCE_MODEL,
        local_only=local_only,
        api_allowed=False,
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


def _eligibility(
    eligible: bool,
    reason: MaintenanceEligibilityReason,
    *,
    model_scope: str,
    provider_scope: str,
    role_scope: str,
) -> MaintenanceModelEligibilityDecision:
    return MaintenanceModelEligibilityDecision(
        eligible=eligible,
        reason=reason,
        model_scope=model_scope,
        provider_scope=provider_scope,
        role_scope=role_scope,
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
