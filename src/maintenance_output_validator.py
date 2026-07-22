"""Strict, content-free validation for productive maintenance consumers.

The validator owns the one-retry policy above the single-attempt typed runtime.
It never serializes prompts, raw model output, parsed values, endpoints or source
hashes into audit evidence.  Parsed candidates remain in-memory only and never
authorize a truth write here.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import json
import math
import re
from typing import Any, Mapping, Sequence

from src.gemma4_maintenance_router import (
    PromptCapsule,
    validate_gemma4_maintenance_output,
)
from src.maintenance_llm_runtime import (
    MaintenanceLLMMessage,
    MaintenanceLLMRequest,
    MaintenanceLLMResult,
    call_maintenance_llm,
    call_maintenance_llm_async,
)


MAINTENANCE_OUTPUT_VALIDATION_SCHEMA = "odysseus.maintenance_output_validation.v1"
MAINTENANCE_VALIDATED_RUN_SCHEMA = "odysseus.maintenance_validated_run.v1"
MAX_MAINTENANCE_OUTPUT_CHARS = 12_000
MAX_COLLECTION_ITEMS = 16
MAX_SCALAR_CHARS = 280

_STATUS_VALUES = frozenset(
    {"ready", "review", "blocked", "skipped", "candidate", "partial", "no_go"}
)
_CLASSIFICATION_VALUES = frozenset(
    {"public", "private", "sensitive", "secret", "unknown"}
)
_MEMORY_STATUS_VALUES = frozenset({"ready", "review", "blocked", "skipped"})
_SAFE_DOCUMENT_TYPE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SOURCE_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_PROVENANCE_KEYS = frozenset({"source_hash", "source_hashes"})
_CANDIDATE_FACT_KEYS = frozenset(
    {"subject", "predicate", "object", "source_hash", "confidence"}
)
_REPAIRABLE_FAILURES = frozenset(
    {
        "json_invalid",
        "json_not_object",
        "output_too_large",
        "schema_missing_fields",
        "schema_unknown_fields",
        "schema_type",
        "schema_enum",
        "schema_bounds",
        "provenance_missing",
    }
)


class MaintenanceOutputStatus(StrEnum):
    VALID = "valid"
    RETRY = "retry"
    REVIEW = "review"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class MaintenanceOutputValidation:
    status: MaintenanceOutputStatus
    schema_valid: bool
    parsed: Mapping[str, Any]
    failure_reasons: tuple[str, ...]
    retry_allowed: bool
    review_required: bool
    matched_source_count: int = 0
    schema: str = MAINTENANCE_OUTPUT_VALIDATION_SCHEMA

    def audit_dict(self) -> dict[str, Any]:
        """Return closed, content-free validation evidence."""

        return {
            "schema": self.schema,
            "status": self.status.value,
            "schema_valid": self.schema_valid,
            "failure_reasons": self.failure_reasons,
            "retry_allowed": self.retry_allowed,
            "review_required": self.review_required,
            "matched_source_count": self.matched_source_count,
            "parsed_retained_in_evidence": False,
            "truth_write_authorized": False,
        }


@dataclass(frozen=True, slots=True)
class MaintenanceValidatedRun:
    result: MaintenanceLLMResult
    validation: MaintenanceOutputValidation
    call_count: int
    retry_count: int
    schema: str = MAINTENANCE_VALIDATED_RUN_SCHEMA

    def audit_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "status": (
                "validated_candidate"
                if self.validation.status is MaintenanceOutputStatus.VALID
                else "review_required"
            ),
            "call_count": self.call_count,
            "retry_count": self.retry_count,
            "runtime": self.result.audit_dict(),
            "validation": self.validation.audit_dict(),
            "output_retained_in_evidence": False,
            "fallback_used": False,
            "truth_write_performed": False,
        }


def maintenance_output_schema_instruction(
    capsule: PromptCapsule,
    *,
    allowed_source_hashes: Sequence[str],
) -> str:
    """Build the runtime-only strict schema suffix for a capsule prompt."""

    allowed = _normalize_allowed_source_hashes(allowed_source_hashes)
    keys = ", ".join(capsule.required_fields)
    sources = json.dumps(allowed, separators=(",", ":"))
    return (
        "Return exactly one JSON object with no markdown and exactly these keys: "
        f"{keys}. status/classification/document_type/review_reason are strings; "
        "confidence is a number from 0 to 1. provenance must contain only "
        f"source_hash or source_hashes and may reference only: {sources}. "
        "Do not invent, replace, or omit provenance."
    )


def validate_maintenance_output(
    raw: str | Mapping[str, Any],
    *,
    capsule: PromptCapsule,
    allowed_source_hashes: Sequence[str],
    allow_retry: bool = True,
) -> MaintenanceOutputValidation:
    """Validate exact JSON, strict workload schema and trusted provenance."""

    allowed_sources = _normalize_allowed_source_hashes(allowed_source_hashes)
    parsed, parse_failure = _parse_exact_object(raw)
    failures: list[str] = []
    if parse_failure:
        failures.append(parse_failure)
    if parse_failure is None:
        required = frozenset(capsule.required_fields)
        keys = frozenset(parsed)
        if required - keys:
            failures.append("schema_missing_fields")
        if keys - required:
            failures.append("schema_unknown_fields")
        failures.extend(_validate_common_types(parsed))
        failures.extend(
            _validate_workload_fields(
                parsed,
                required=required,
                allowed_sources=allowed_sources,
            )
        )
        matched_source_count, provenance_failure = _validate_provenance(
            parsed.get("provenance"),
            allowed_sources=allowed_sources,
        )
        if provenance_failure:
            failures.append(provenance_failure)
        failures.extend(_semantic_conflicts(parsed))
        router_validation = validate_gemma4_maintenance_output(
            parsed,
            capsule=capsule,
            allow_retry=False,
        )
        if any(
            reason.startswith(("output_forbidden", "unsafe_output"))
            for reason in router_validation.failure_reasons
        ):
            failures.append("forbidden_content")
    else:
        matched_source_count = 0

    normalized_failures = tuple(dict.fromkeys(failures))
    schema_valid = not normalized_failures
    repairable = bool(normalized_failures) and all(
        reason in _REPAIRABLE_FAILURES for reason in normalized_failures
    )
    if schema_valid:
        status = MaintenanceOutputStatus.VALID
    elif allow_retry and repairable:
        status = MaintenanceOutputStatus.RETRY
    elif any(
        reason in {"forbidden_content", "provenance_hallucinated", "semantic_conflict"}
        for reason in normalized_failures
    ):
        status = MaintenanceOutputStatus.BLOCKED
    else:
        status = MaintenanceOutputStatus.REVIEW
    return MaintenanceOutputValidation(
        status=status,
        schema_valid=schema_valid,
        parsed=dict(parsed) if schema_valid else {},
        failure_reasons=normalized_failures,
        retry_allowed=status is MaintenanceOutputStatus.RETRY,
        review_required=status is not MaintenanceOutputStatus.VALID,
        matched_source_count=matched_source_count,
    )


def call_validated_maintenance_llm(
    request: MaintenanceLLMRequest,
    *,
    capsule: PromptCapsule,
    allowed_source_hashes: Sequence[str],
    attempt=None,
    registry=None,
) -> MaintenanceValidatedRun:
    """Run the typed sync boundary and at most one compact validation retry."""

    result = call_maintenance_llm(request, attempt=attempt, registry=registry)
    validation = validate_maintenance_output(
        result.text,
        capsule=capsule,
        allowed_source_hashes=allowed_source_hashes,
        allow_retry=True,
    )
    call_count = 1
    retry_count = 0
    if validation.retry_allowed:
        retry_count = 1
        call_count = 2
        result = call_maintenance_llm(
            _compact_retry_request(request),
            attempt=attempt,
            registry=registry,
        )
        validation = validate_maintenance_output(
            result.text,
            capsule=capsule,
            allowed_source_hashes=allowed_source_hashes,
            allow_retry=False,
        )
    return MaintenanceValidatedRun(
        result=result,
        validation=validation,
        call_count=call_count,
        retry_count=retry_count,
    )


async def call_validated_maintenance_llm_async(
    request: MaintenanceLLMRequest,
    *,
    capsule: PromptCapsule,
    allowed_source_hashes: Sequence[str],
    attempt=None,
    registry=None,
) -> MaintenanceValidatedRun:
    """Run the typed async boundary and at most one compact validation retry."""

    result = await call_maintenance_llm_async(
        request,
        attempt=attempt,
        registry=registry,
    )
    validation = validate_maintenance_output(
        result.text,
        capsule=capsule,
        allowed_source_hashes=allowed_source_hashes,
        allow_retry=True,
    )
    call_count = 1
    retry_count = 0
    if validation.retry_allowed:
        retry_count = 1
        call_count = 2
        result = await call_maintenance_llm_async(
            _compact_retry_request(request),
            attempt=attempt,
            registry=registry,
        )
        validation = validate_maintenance_output(
            result.text,
            capsule=capsule,
            allowed_source_hashes=allowed_source_hashes,
            allow_retry=False,
        )
    return MaintenanceValidatedRun(
        result=result,
        validation=validation,
        call_count=call_count,
        retry_count=retry_count,
    )


def _compact_retry_request(request: MaintenanceLLMRequest) -> MaintenanceLLMRequest:
    return replace(
        request,
        messages=(
            MaintenanceLLMMessage(
                "system",
                "Retry once: return exactly one strict JSON object; no markdown, extra keys, source changes, or explanation.",
            ),
            request.messages[-1],
        ),
        max_attempts=1,
        stream=False,
        fallback_requested=False,
        truth_write_requested=False,
    )


def _parse_exact_object(raw: str | Mapping[str, Any]) -> tuple[dict[str, Any], str | None]:
    if isinstance(raw, Mapping):
        return dict(raw), None
    if not isinstance(raw, str):
        return {}, "json_not_object"
    if len(raw) > MAX_MAINTENANCE_OUTPUT_CHARS:
        return {}, "output_too_large"
    try:
        parsed = json.loads(raw.strip())
    except (TypeError, ValueError):
        return {}, "json_invalid"
    if not isinstance(parsed, dict):
        return {}, "json_not_object"
    return parsed, None


def _validate_common_types(parsed: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    status = parsed.get("status")
    if not isinstance(status, str):
        failures.append("schema_type")
    elif status not in _STATUS_VALUES:
        failures.append("schema_enum")
    classification = parsed.get("classification")
    if not isinstance(classification, str):
        failures.append("schema_type")
    elif classification not in _CLASSIFICATION_VALUES:
        failures.append("schema_enum")
    document_type = parsed.get("document_type")
    if not isinstance(document_type, str):
        failures.append("schema_type")
    elif not _SAFE_DOCUMENT_TYPE.fullmatch(document_type):
        failures.append("schema_enum")
    confidence = parsed.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        failures.append("schema_type")
    elif not math.isfinite(float(confidence)) or not 0.0 <= float(confidence) <= 1.0:
        failures.append("schema_bounds")
    review_reason = parsed.get("review_reason")
    if not isinstance(review_reason, str):
        failures.append("schema_type")
    elif len(review_reason) > MAX_SCALAR_CHARS:
        failures.append("schema_bounds")
    return failures


def _validate_workload_fields(
    parsed: Mapping[str, Any],
    *,
    required: frozenset[str],
    allowed_sources: tuple[str, ...],
) -> list[str]:
    failures: list[str] = []
    if "memory_write_intent_status" in required:
        value = parsed.get("memory_write_intent_status")
        if not isinstance(value, str):
            failures.append("schema_type")
        elif value not in _MEMORY_STATUS_VALUES:
            failures.append("schema_enum")
    if "should_remember" in required and not isinstance(parsed.get("should_remember"), bool):
        failures.append("schema_type")
    if "candidate_facts" in required:
        failures.extend(
            _validate_candidate_facts(
                parsed.get("candidate_facts"),
                allowed_sources=allowed_sources,
            )
        )
    if "contradiction_hints" in required:
        failures.extend(_validate_string_list(parsed.get("contradiction_hints")))
    return failures


def _validate_candidate_facts(
    value: Any,
    *,
    allowed_sources: tuple[str, ...],
) -> list[str]:
    if not isinstance(value, list):
        return ["schema_type"]
    if len(value) > MAX_COLLECTION_ITEMS:
        return ["schema_bounds"]
    for item in value:
        if not isinstance(item, Mapping):
            return ["schema_type"]
        if frozenset(item) - _CANDIDATE_FACT_KEYS:
            return ["schema_unknown_fields"]
        if not {"subject", "predicate", "object", "source_hash"}.issubset(item):
            return ["schema_missing_fields"]
        for key in ("subject", "predicate", "object", "source_hash"):
            scalar = item.get(key)
            if not isinstance(scalar, str):
                return ["schema_type"]
            if len(scalar) > MAX_SCALAR_CHARS:
                return ["schema_bounds"]
        source_hash = item.get("source_hash")
        if not _SOURCE_HASH.fullmatch(source_hash) or source_hash not in allowed_sources:
            return ["provenance_hallucinated"]
    return []


def _validate_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return ["schema_type"]
    if len(value) > MAX_COLLECTION_ITEMS:
        return ["schema_bounds"]
    if any(not isinstance(item, str) for item in value):
        return ["schema_type"]
    if any(len(item) > MAX_SCALAR_CHARS for item in value):
        return ["schema_bounds"]
    return []


def _validate_provenance(
    value: Any,
    *,
    allowed_sources: tuple[str, ...],
) -> tuple[int, str | None]:
    if not isinstance(value, Mapping):
        return 0, "provenance_missing"
    if frozenset(value) - _PROVENANCE_KEYS:
        return 0, "schema_unknown_fields"
    sources: list[Any] = []
    if "source_hash" in value:
        sources.append(value.get("source_hash"))
    if "source_hashes" in value:
        source_hashes = value.get("source_hashes")
        if not isinstance(source_hashes, list):
            return 0, "schema_type"
        sources.extend(source_hashes)
    if not sources:
        return 0, "provenance_missing"
    if len(sources) > MAX_COLLECTION_ITEMS or any(not isinstance(item, str) for item in sources):
        return 0, "schema_bounds"
    if any(not _SOURCE_HASH.fullmatch(item) for item in sources):
        return 0, "provenance_hallucinated"
    allowed = frozenset(allowed_sources)
    if any(item not in allowed for item in sources):
        return 0, "provenance_hallucinated"
    return len(frozenset(sources)), None


def _semantic_conflicts(parsed: Mapping[str, Any]) -> list[str]:
    status = parsed.get("status")
    review_reason = str(parsed.get("review_reason") or "").strip()
    if status in {"ready", "candidate"} and review_reason:
        return ["semantic_conflict"]
    if "should_remember" in parsed and "memory_write_intent_status" in parsed:
        should_remember = parsed.get("should_remember")
        memory_status = parsed.get("memory_write_intent_status")
        if should_remember is False and memory_status != "skipped":
            return ["semantic_conflict"]
        if status == "ready" and memory_status in {"review", "blocked"}:
            return ["semantic_conflict"]
    contradictions = parsed.get("contradiction_hints")
    if isinstance(contradictions, list) and contradictions:
        return ["semantic_conflict"]
    return []


def _normalize_allowed_source_hashes(values: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(str(value or "") for value in values))
    if not normalized or any(not _SOURCE_HASH.fullmatch(value) for value in normalized):
        raise ValueError("allowed_source_hashes must contain trusted sha256 references")
    if len(normalized) > MAX_COLLECTION_ITEMS:
        raise ValueError("allowed_source_hashes exceeds the bounded source count")
    return normalized


__all__ = [
    "MAINTENANCE_OUTPUT_VALIDATION_SCHEMA",
    "MAINTENANCE_VALIDATED_RUN_SCHEMA",
    "MAX_MAINTENANCE_OUTPUT_CHARS",
    "MaintenanceOutputStatus",
    "MaintenanceOutputValidation",
    "MaintenanceValidatedRun",
    "call_validated_maintenance_llm",
    "call_validated_maintenance_llm_async",
    "maintenance_output_schema_instruction",
    "validate_maintenance_output",
]
