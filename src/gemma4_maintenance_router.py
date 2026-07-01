"""Gemma4 E4B maintenance router contracts.

This module is deliberately side-effect free: it does not call models, read
documents, persist prompts, or write memories. It turns trusted runtime
metadata into a small Gemma maintenance route, prompt capsule, output contract,
and queue/budget decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from src.maintenance_model_policy import (
    MaintenanceModelPolicyError,
    MaintenanceModelProfile,
    MaintenanceWorkload,
    default_maintenance_model_profile,
    plan_maintenance_model_route,
)


ROUTER_SCHEMA = "odysseus.gemma4_maintenance_router.v1"
PROMPT_CAPSULE_SCHEMA = "odysseus.gemma4_prompt_capsule.v1"
OUTPUT_VALIDATION_SCHEMA = "odysseus.gemma4_output_validation.v1"
QUEUE_POLICY_SCHEMA = "odysseus.gemma4_queue_policy.v1"

DEFAULT_REQUIRED_FIELDS = (
    "status",
    "classification",
    "document_type",
    "confidence",
    "review_reason",
    "provenance",
)

_SAFE_ID_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,95}$")
_FORBIDDEN_OUTPUT_KEYS = {
    "raw_text",
    "full_text",
    "document_text",
    "file_content",
    "content",
    "body",
    "transcript",
    "ocr_text",
    "base64",
    "bytes",
    "path",
    "host_path",
    "absolute_path",
    "chat_id",
    "telegram_chat_id",
    "token",
    "secret",
    "password",
    "api_key",
    "authorization",
    "cookie",
}
_SECRET_VALUE_RE = re.compile(
    r"(bearer\s+[a-z0-9._-]{12,}|api[_-]?key|password\s*[:=]|-----BEGIN [A-Z ]*PRIVATE KEY-----)",
    re.IGNORECASE,
)


class GemmaMaintenanceRouterError(ValueError):
    """Raised when a Gemma maintenance route or payload is unsafe."""


class GemmaMaintenanceSurface(StrEnum):
    UNIVERSAL_INBOX = "universal_inbox"
    TELEGRAM = "telegram"
    NEXTCLOUD = "nextcloud"
    MEMORY = "memory"
    RAPTORGRAPH = "raptorgraph"
    VOICE = "voice"
    EXPORT_CONVERSION = "export_conversion"
    LONG_DOCUMENT = "long_document"


class GemmaOutputStatus(StrEnum):
    VALID = "valid"
    RETRY = "retry"
    REVIEW = "review"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class PromptCapsule:
    capsule_id: str
    workload: MaintenanceWorkload
    title: str
    instruction: str
    required_fields: tuple[str, ...] = DEFAULT_REQUIRED_FIELDS
    max_excerpt_chars: int = 1200
    schema: str = PROMPT_CAPSULE_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "capsule_id": self.capsule_id,
            "workload": self.workload.value,
            "title": self.title,
            "required_fields": self.required_fields,
            "max_excerpt_chars": self.max_excerpt_chars,
            "raw_content_persistence_allowed": False,
        }

    def build_prompt(self, *, metadata: Mapping[str, Any], excerpt: str = "") -> str:
        """Build the runtime prompt. Callers must not persist this prompt."""

        bounded_excerpt = str(excerpt or "")[: self.max_excerpt_chars]
        meta = _safe_metadata(metadata)
        return (
            f"{self.instruction}\n"
            "Return JSON only. Do not include markdown, chain of thought, raw quotes, "
            "host paths, chat IDs, tokens, or private source text.\n"
            f"Required fields: {', '.join(self.required_fields)}.\n"
            f"metadata: {json.dumps(meta, sort_keys=True, ensure_ascii=False)}\n"
            f"bounded_excerpt: {bounded_excerpt}\n"
        )


@dataclass(frozen=True, slots=True)
class QueuePolicy:
    max_queue_concurrency: int
    latency_budget_ms: int
    token_budget: int
    max_input_chars: int
    chunk_budget: int
    schema: str = QUEUE_POLICY_SCHEMA

    @classmethod
    def from_profile(cls, profile: MaintenanceModelProfile) -> "QueuePolicy":
        return cls(
            max_queue_concurrency=profile.max_queue_concurrency,
            latency_budget_ms=profile.latency_budget_ms,
            token_budget=profile.token_budget,
            max_input_chars=profile.max_input_chars,
            chunk_budget=profile.chunk_budget,
        )

    def decide(self, *, active_jobs: int = 0, input_chars: int = 0, chunk_count: int = 1) -> dict[str, Any]:
        reasons: list[str] = []
        if int(active_jobs or 0) >= self.max_queue_concurrency:
            reasons.append("queue_concurrency_exhausted")
        if int(input_chars or 0) > self.max_input_chars:
            reasons.append("input_chars_exceed_budget")
        if int(chunk_count or 1) > self.chunk_budget:
            reasons.append("chunk_count_exceeds_budget")
        return {
            "schema": self.schema,
            "status": "wait" if reasons == ["queue_concurrency_exhausted"] else ("prepare_smaller_packet" if reasons else "admit"),
            "reasons": tuple(reasons),
            "max_queue_concurrency": self.max_queue_concurrency,
            "latency_budget_ms": self.latency_budget_ms,
            "token_budget": self.token_budget,
            "max_input_chars": self.max_input_chars,
            "chunk_budget": self.chunk_budget,
        }

    def to_dict(self) -> dict[str, Any]:
        return self.decide()


@dataclass(frozen=True, slots=True)
class GemmaMaintenanceRoutePlan:
    surface: GemmaMaintenanceSurface
    capsule: PromptCapsule
    route: Mapping[str, Any]
    queue_policy: QueuePolicy
    source_hashes: tuple[str, ...]
    excerpt_hash: str
    input_chars: int
    chunk_count: int
    schema: str = ROUTER_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "surface": self.surface.value,
            "prompt_capsule": self.capsule.to_dict(),
            "route": dict(self.route),
            "queue_policy": self.queue_policy.to_dict(),
            "source_hashes": self.source_hashes,
            "excerpt_hash": self.excerpt_hash,
            "input_chars": self.input_chars,
            "chunk_count": self.chunk_count,
            "raw_content_visible": False,
            "raw_content_persisted": False,
        }

    def flat_route_report(self) -> dict[str, Any]:
        report = dict(self.route)
        report["prompt_capsule_id"] = self.capsule.capsule_id
        report["prompt_capsule_schema"] = self.capsule.schema
        report["queue_policy"] = self.queue_policy.to_dict()
        report["source_hashes"] = self.source_hashes
        report["excerpt_hash"] = self.excerpt_hash
        return report


@dataclass(frozen=True, slots=True)
class GemmaOutputValidationResult:
    status: GemmaOutputStatus
    schema_valid: bool
    parsed: Mapping[str, Any]
    failure_reasons: tuple[str, ...]
    retry_allowed: bool
    review_required: bool
    schema: str = OUTPUT_VALIDATION_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "status": self.status.value,
            "schema_valid": self.schema_valid,
            "parsed": dict(self.parsed),
            "failure_reasons": self.failure_reasons,
            "retry_allowed": self.retry_allowed,
            "review_required": self.review_required,
            "raw_content_visible": False,
        }


_CAPSULES: dict[MaintenanceWorkload, PromptCapsule] = {
    MaintenanceWorkload.INBOX_TRIAGE: PromptCapsule(
        capsule_id="gemma4.inbox_triage.v1",
        workload=MaintenanceWorkload.INBOX_TRIAGE,
        title="Universal Inbox triage",
        instruction="Classify the incoming document packet for safe routing and memory intent.",
    ),
    MaintenanceWorkload.SENSITIVITY_CLASSIFICATION: PromptCapsule(
        capsule_id="gemma4.sensitivity_classification.v1",
        workload=MaintenanceWorkload.SENSITIVITY_CLASSIFICATION,
        title="Sensitivity classification",
        instruction="Decide whether the source must remain local-only and name the review reason.",
    ),
    MaintenanceWorkload.MEMORY_WRITE_INTENT: PromptCapsule(
        capsule_id="gemma4.memory_write_intent.v1",
        workload=MaintenanceWorkload.MEMORY_WRITE_INTENT,
        title="Memory write intent",
        instruction="Produce a candidate memory-write intent without truth-writing.",
        required_fields=DEFAULT_REQUIRED_FIELDS + ("memory_write_intent_status", "should_remember"),
    ),
    MaintenanceWorkload.RAPTORGRAPH_ABSTRACTION: PromptCapsule(
        capsule_id="gemma4.raptorgraph_candidate.v1",
        workload=MaintenanceWorkload.RAPTORGRAPH_ABSTRACTION,
        title="RaptorGraph candidate",
        instruction="Produce candidate graph facts with provenance and contradiction hints only.",
        required_fields=DEFAULT_REQUIRED_FIELDS + ("candidate_facts", "contradiction_hints"),
    ),
    MaintenanceWorkload.RAPTORGRAPH_MAINTENANCE: PromptCapsule(
        capsule_id="gemma4.raptorgraph_maintenance.v1",
        workload=MaintenanceWorkload.RAPTORGRAPH_MAINTENANCE,
        title="RaptorGraph maintenance",
        instruction="Summarize maintenance candidates and gate them for backend review.",
    ),
    MaintenanceWorkload.VOICE_TRANSCRIPT: PromptCapsule(
        capsule_id="gemma4.voice_transcript.v1",
        workload=MaintenanceWorkload.VOICE_TRANSCRIPT,
        title="Voice transcript maintenance",
        instruction="Summarize a transcript into a bounded follow-up reference without storing raw speech.",
    ),
    MaintenanceWorkload.EXPORT_CONVERSION_PREFLIGHT: PromptCapsule(
        capsule_id="gemma4.export_conversion_preflight.v1",
        workload=MaintenanceWorkload.EXPORT_CONVERSION_PREFLIGHT,
        title="Export/conversion preflight",
        instruction="Classify a requested file conversion and whether local-only handling is required.",
    ),
    MaintenanceWorkload.LONG_DOCUMENT_PREFLIGHT: PromptCapsule(
        capsule_id="gemma4.long_document_preflight.v1",
        workload=MaintenanceWorkload.LONG_DOCUMENT_PREFLIGHT,
        title="Long document preflight",
        instruction="Plan chunking and review gates for a long document without ingesting all text.",
    ),
}

_SURFACE_DEFAULTS: dict[GemmaMaintenanceSurface, MaintenanceWorkload] = {
    GemmaMaintenanceSurface.UNIVERSAL_INBOX: MaintenanceWorkload.INBOX_TRIAGE,
    GemmaMaintenanceSurface.TELEGRAM: MaintenanceWorkload.INBOX_TRIAGE,
    GemmaMaintenanceSurface.NEXTCLOUD: MaintenanceWorkload.INBOX_TRIAGE,
    GemmaMaintenanceSurface.MEMORY: MaintenanceWorkload.MEMORY_WRITE_INTENT,
    GemmaMaintenanceSurface.RAPTORGRAPH: MaintenanceWorkload.RAPTORGRAPH_ABSTRACTION,
    GemmaMaintenanceSurface.VOICE: MaintenanceWorkload.VOICE_TRANSCRIPT,
    GemmaMaintenanceSurface.EXPORT_CONVERSION: MaintenanceWorkload.EXPORT_CONVERSION_PREFLIGHT,
    GemmaMaintenanceSurface.LONG_DOCUMENT: MaintenanceWorkload.LONG_DOCUMENT_PREFLIGHT,
}


def list_prompt_capsules() -> tuple[PromptCapsule, ...]:
    return tuple(_CAPSULES.values())


def get_prompt_capsule(workload: MaintenanceWorkload | str) -> PromptCapsule:
    normalized = _normalize_workload(workload)
    try:
        return _CAPSULES[normalized]
    except KeyError as exc:
        raise GemmaMaintenanceRouterError("missing_prompt_capsule") from exc


def plan_gemma4_maintenance_route(
    *,
    surface: GemmaMaintenanceSurface | str,
    workload: MaintenanceWorkload | str | None = None,
    classification: str = "private",
    dsgvo_mode: bool = False,
    input_chars: int = 0,
    chunk_count: int = 1,
    source_refs: Sequence[str] | None = None,
    excerpt: str = "",
    confidence: float = 1.0,
    extraction_status: str = "completed",
    api_escalation_allowed: bool = True,
    fallback_gate_reason: str = "",
    profile: MaintenanceModelProfile | Mapping[str, Any] | None = None,
) -> GemmaMaintenanceRoutePlan:
    model_profile = _coerce_profile(profile)
    normalized_surface = _normalize_surface(surface)
    normalized_workload = _normalize_workload(workload or _SURFACE_DEFAULTS[normalized_surface])
    capsule = get_prompt_capsule(normalized_workload)
    source_hashes = tuple(_hash_ref(ref) for ref in (source_refs or ())[: model_profile.source_ref_budget])
    effective_input_chars = max(int(input_chars or 0), len(str(excerpt or "")))
    route = plan_maintenance_model_route(
        workload=normalized_workload,
        classification=classification,
        dsgvo_mode=dsgvo_mode,
        input_chars=effective_input_chars,
        chunk_count=chunk_count,
        source_ref_count=max(len(source_hashes), 1),
        confidence=confidence,
        extraction_status=extraction_status,
        api_escalation_allowed=api_escalation_allowed,
        fallback_gate_reason=fallback_gate_reason,
        profile=model_profile,
    )
    return GemmaMaintenanceRoutePlan(
        surface=normalized_surface,
        capsule=capsule,
        route=route.to_dict(),
        queue_policy=QueuePolicy.from_profile(model_profile),
        source_hashes=source_hashes,
        excerpt_hash=_hash_ref(excerpt),
        input_chars=effective_input_chars,
        chunk_count=int(chunk_count or 1),
    )


def validate_gemma4_maintenance_output(
    raw: str | Mapping[str, Any],
    *,
    capsule: PromptCapsule | MaintenanceWorkload | str,
    allow_retry: bool = True,
) -> GemmaOutputValidationResult:
    prompt_capsule = capsule if isinstance(capsule, PromptCapsule) else get_prompt_capsule(capsule)
    parsed, parse_error = _parse_output(raw)
    failures: list[str] = []
    if parse_error:
        failures.append(f"json_invalid:{parse_error}")
    missing = [field for field in prompt_capsule.required_fields if field not in parsed]
    if missing:
        failures.append("schema_missing_fields:" + ",".join(missing))
    try:
        _reject_forbidden_payload(parsed)
    except GemmaMaintenanceRouterError as exc:
        failures.append(str(exc))

    schema_valid = not failures
    if schema_valid:
        status = GemmaOutputStatus.VALID
    elif any(reason.startswith("output_forbidden") for reason in failures):
        status = GemmaOutputStatus.BLOCKED
    elif allow_retry and any(reason.startswith(("json_invalid", "schema_missing_fields")) for reason in failures):
        status = GemmaOutputStatus.RETRY
    else:
        status = GemmaOutputStatus.REVIEW
    return GemmaOutputValidationResult(
        status=status,
        schema_valid=schema_valid,
        parsed=_redacted_parsed(parsed),
        failure_reasons=tuple(failures),
        retry_allowed=status is GemmaOutputStatus.RETRY,
        review_required=status in {GemmaOutputStatus.REVIEW, GemmaOutputStatus.BLOCKED},
    )


def _coerce_profile(profile: MaintenanceModelProfile | Mapping[str, Any] | None) -> MaintenanceModelProfile:
    if profile is None:
        return default_maintenance_model_profile()
    if isinstance(profile, MaintenanceModelProfile):
        return profile
    if isinstance(profile, Mapping):
        return MaintenanceModelProfile.create(**dict(profile))
    raise MaintenanceModelPolicyError("profile must be a MaintenanceModelProfile or mapping")


def _normalize_surface(value: GemmaMaintenanceSurface | str) -> GemmaMaintenanceSurface:
    if isinstance(value, GemmaMaintenanceSurface):
        return value
    token = _token(value)
    try:
        return GemmaMaintenanceSurface(token)
    except ValueError as exc:
        raise GemmaMaintenanceRouterError("unsupported_surface") from exc


def _normalize_workload(value: MaintenanceWorkload | str) -> MaintenanceWorkload:
    if isinstance(value, MaintenanceWorkload):
        return value
    token = _token(value)
    try:
        return MaintenanceWorkload(token)
    except ValueError as exc:
        raise GemmaMaintenanceRouterError("unsupported_workload") from exc


def _safe_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in dict(metadata or {}).items():
        token = _token(key)
        if token in _FORBIDDEN_OUTPUT_KEYS:
            continue
        text = str(value or "")
        if len(text) > 240:
            text = text[:237] + "..."
        if _SECRET_VALUE_RE.search(text):
            text = "[redacted]"
        safe[token] = text
    return safe


def _parse_output(raw: str | Mapping[str, Any]) -> tuple[dict[str, Any], str | None]:
    if isinstance(raw, Mapping):
        return dict(raw), None
    text = str(raw or "").strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if match:
        text = match.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        value = json.loads(text)
    except Exception as exc:
        return {}, type(exc).__name__
    if not isinstance(value, dict):
        return {}, "not_object"
    return value, None


def _reject_forbidden_payload(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            token = str(key or "").strip().lower()
            if token in _FORBIDDEN_OUTPUT_KEYS:
                raise GemmaMaintenanceRouterError(f"output_forbidden_key:{token}")
            _reject_forbidden_payload(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _reject_forbidden_payload(item)
        return
    text = str(value or "")
    if len(text) > 1600:
        raise GemmaMaintenanceRouterError("output_forbidden_value_too_large")
    if _SECRET_VALUE_RE.search(text):
        raise GemmaMaintenanceRouterError("output_forbidden_secret_marker")
    if re.search(r"^[A-Za-z]:[\\/]|^/home/|^/Users/|^~[\\/]", text):
        raise GemmaMaintenanceRouterError("output_forbidden_host_path")


def _redacted_parsed(parsed: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in dict(parsed or {}).items():
        token = _token(key)
        if token in _FORBIDDEN_OUTPUT_KEYS:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[token] = value if not isinstance(value, str) or len(value) <= 280 else value[:277] + "..."
        elif isinstance(value, list):
            result[token] = tuple(str(item)[:120] for item in value[:12])
        elif isinstance(value, Mapping):
            result[token] = {str(k)[:40]: str(v)[:120] for k, v in list(value.items())[:12]}
    return result


def _hash_ref(value: Any) -> str:
    return "sha256:" + hashlib.sha256(str(value or "").encode("utf-8", errors="replace")).hexdigest()


def _token(value: Any) -> str:
    token = str(value or "").strip().lower().replace("-", "_").replace("/", "_").replace(" ", "_")
    if not _SAFE_ID_RE.fullmatch(token):
        raise GemmaMaintenanceRouterError("unsafe_token")
    return token
