"""Local-sensitive worker contract for redacted orchestration.

This module registers a narrow agent tool that lets an external/API
orchestrator request local handling of sensitive material without receiving the
raw material itself. The executable slice is intentionally metadata-first:
callers may pass trusted source references, task intent, classification, and an
optional redacted abstraction. Raw text, transcripts, host paths, Telegram IDs,
tokens, and document bodies are rejected at the tool boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping

from src.data_classification import resolve_classification
from src.gemma4_maintenance_router import (
    GemmaMaintenanceSurface,
    plan_gemma4_maintenance_route,
)
from src.maintenance_model_policy import MaintenanceWorkload
from src.sensitivity_delegation_gate import decide_sensitivity_delegation


SENSITIVE_LOCAL_ANALYSIS_TOOL = "sensitive_local_analysis"
SENSITIVE_LOCAL_WORKER_SCHEMA = "odysseus.sensitive_local_worker.v1"

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:@/-]{0,160}$")
_FORBIDDEN_ARG_KEYS = {
    "raw_text",
    "full_text",
    "document_text",
    "file_content",
    "content",
    "body",
    "email_body",
    "transcript",
    "ocr_text",
    "payload",
    "bytes",
    "base64",
    "image_data",
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


class SensitiveLocalWorkerError(ValueError):
    """Raised when a local-sensitive worker request is unsafe."""


@dataclass(frozen=True, slots=True)
class SensitiveLocalWorkerResult:
    status: str
    source_ref: str
    task: str
    classification: str
    delegation: Mapping[str, Any]
    redacted_abstraction: Mapping[str, Any]
    local_job_request: Mapping[str, Any]
    blocked_reason: str = ""
    schema: str = SENSITIVE_LOCAL_WORKER_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": self.schema,
            "status": self.status,
            "source_ref": self.source_ref,
            "task": self.task,
            "classification": self.classification,
            "delegation": dict(self.delegation),
            "redacted_abstraction": dict(self.redacted_abstraction),
            "local_job_request": dict(self.local_job_request),
            "raw_content_visible": False,
            "raw_content_returned": False,
            "external_model_may_see_result": True,
        }
        if self.blocked_reason:
            payload["blocked_reason"] = self.blocked_reason
        _reject_forbidden_payload(payload)
        return payload


def build_sensitive_local_worker_result(payload: Mapping[str, Any]) -> SensitiveLocalWorkerResult:
    """Build a redacted worker result from already-trusted metadata."""

    _reject_forbidden_args(payload)
    classification = _normalize_classification(payload.get("classification"))
    source_ref = _safe_label(payload.get("source_ref") or payload.get("doc_ref") or "", field="source_ref")
    task = _safe_text(payload.get("task") or payload.get("analysis_goal") or "analyze", field="task", limit=240)
    redacted_context = _safe_text(payload.get("redacted_context") or "", field="redacted_context", limit=1000)
    dsgvo_mode = _truthy(payload.get("dsgvo_mode"))
    local_only_required = _truthy(payload.get("local_only_required"))
    surface = _normalize_surface(payload.get("surface") or payload.get("source_channel") or "universal_inbox")
    workload = _normalize_workload(payload.get("workload") or payload.get("maintenance_workload") or "sensitivity_classification")

    delegation = decide_sensitivity_delegation(
        dsgvo_mode=dsgvo_mode,
        classification=classification,
        raw_content_visible=False,
        api_model_allowed=False,
        local_only_required=local_only_required,
        redacted_context_available=bool(redacted_context),
    ).to_dict()
    route_plan = plan_gemma4_maintenance_route(
        surface=surface,
        workload=workload,
        classification=classification,
        dsgvo_mode=dsgvo_mode,
        input_chars=len(redacted_context),
        source_refs=(source_ref,),
        excerpt=redacted_context,
        api_escalation_allowed=False,
    )
    abstraction = {
        "summary": redacted_context,
        "source_hash": _stable_hash(source_ref),
        "worker": "local_sensitive_worker",
        "model_scope": "local_only",
        "prompt_capsule_id": route_plan.capsule.capsule_id,
        "limitations": (
            "No raw source content was exposed to the orchestrator. "
            "If details are required, the final answer must be produced by a local model."
        ),
    }
    local_job_request = {
        "schema": "odysseus.sensitive_local_worker.job_request.v1",
        "status": "ready" if redacted_context else "pending_local_raw_source",
        "surface": surface,
        "workload": workload,
        "source_hash": _stable_hash(source_ref),
        "task_hash": _stable_hash(task),
        "prompt_capsule_id": route_plan.capsule.capsule_id,
        "maintenance_route": route_plan.flat_route_report(),
        "raw_content_visible": False,
        "raw_content_returned": False,
    }
    status = "ready" if redacted_context else "needs_local_raw_source"
    return SensitiveLocalWorkerResult(
        status=status,
        source_ref=source_ref,
        task=task,
        classification=classification,
        delegation=delegation,
        redacted_abstraction=abstraction,
        local_job_request=local_job_request,
    )


async def execute_sensitive_local_analysis(
    content: str,
    *,
    owner: str | None = None,
    session_id: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Agent-tool entry point. Rejects raw content and returns redacted JSON."""

    try:
        payload = json.loads(content) if str(content or "").strip() else {}
        if not isinstance(payload, dict):
            raise SensitiveLocalWorkerError("arguments_must_be_object")
        result = build_sensitive_local_worker_result(payload).to_dict()
        result["owner_scope"] = _safe_label(owner or "unknown", field="owner")
        result["session_ref"] = _safe_label(session_id or "", field="session_id")
        _reject_forbidden_payload(result)
        return result
    except SensitiveLocalWorkerError as exc:
        return {
            "schema": SENSITIVE_LOCAL_WORKER_SCHEMA,
            "status": "blocked",
            "error": str(exc),
            "raw_content_visible": False,
            "raw_content_returned": False,
            "exit_code": 1,
        }


def register_sensitive_local_worker_tool() -> None:
    from src.tool_registry import ToolSpec, register_tool

    register_tool(ToolSpec(
        name=SENSITIVE_LOCAL_ANALYSIS_TOOL,
        description=(
            "Request local-only handling for sensitive/private source material. "
            "Pass only a safe source_ref, classification, task, and optional redacted_context; "
            "never pass raw text, transcripts, host paths, chat IDs, tokens, or document bodies."
        ),
        parameters={
            "type": "object",
            "properties": {
                "source_ref": {
                    "type": "string",
                    "description": "Opaque safe reference to the local source; no host paths or chat IDs.",
                },
                "classification": {
                    "type": "string",
                    "enum": ["public", "private", "sensitive", "secret"],
                },
                "task": {
                    "type": "string",
                    "description": "Bounded local analysis request, without raw source content.",
                },
                "redacted_context": {
                    "type": "string",
                    "description": "Optional redacted abstraction that an external orchestrator may see.",
                },
                "dsgvo_mode": {"type": "boolean"},
                "local_only_required": {"type": "boolean"},
                "surface": {
                    "type": "string",
                    "enum": ["universal_inbox", "telegram", "nextcloud", "memory", "raptorgraph", "voice", "export_conversion", "long_document"],
                },
                "workload": {
                    "type": "string",
                    "enum": [
                        "inbox_triage",
                        "sensitivity_classification",
                        "memory_write_intent",
                        "raptorgraph_abstraction",
                        "raptorgraph_maintenance",
                        "voice_transcript",
                        "export_conversion_preflight",
                        "long_document_preflight",
                    ],
                },
            },
            "required": ["source_ref", "classification", "task"],
            "additionalProperties": False,
        },
        execute=execute_sensitive_local_analysis,
        permission="admin",
        prompt=(
            "- ```sensitive_local_analysis``` - Request a local-only sensitive worker. "
            "Use this instead of raw-reading tools when DSGVO/sensitive context is involved. "
            "Arguments must contain only source_ref, classification, task, optional redacted_context, "
            "dsgvo_mode, and local_only_required. Never include raw document text, transcripts, "
            "host paths, chat IDs, tokens, secrets, or full tool outputs. If the tool says "
            "needs_local_raw_source, do not guess the sensitive details; ask for or wait for a local result."
        ),
    ))


def _reject_forbidden_args(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = str(key or "").strip().lower()
            if normalized_key in _FORBIDDEN_ARG_KEYS:
                raise SensitiveLocalWorkerError(f"forbidden_argument_key:{normalized_key}")
            _reject_forbidden_args(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _reject_forbidden_args(item)
        return
    text = str(value or "")
    if len(text) > 1500:
        raise SensitiveLocalWorkerError("argument_value_too_large")
    if _SECRET_VALUE_RE.search(text):
        raise SensitiveLocalWorkerError("argument_value_contains_secret_marker")
    if re.search(r"^[A-Za-z]:[\\/]|^/home/|^/Users/|^~[\\/]", text):
        raise SensitiveLocalWorkerError("argument_value_contains_host_path")


def _reject_forbidden_payload(payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    lowered = encoded.lower()
    forbidden = ("authorization", "bearer ", "api_key", "password", "cookie", "begin private key")
    if any(marker in lowered for marker in forbidden):
        raise SensitiveLocalWorkerError("payload_contains_forbidden_marker")


def _normalize_classification(value: Any) -> str:
    resolution = resolve_classification(value)
    if resolution.normalized is None:
        raise SensitiveLocalWorkerError("classification_required")
    return resolution.normalized.value


def _normalize_surface(value: Any) -> str:
    text = str(value or "universal_inbox").strip().lower().replace("-", "_").replace(" ", "_")
    allowed = {item.value for item in GemmaMaintenanceSurface}
    return text if text in allowed else GemmaMaintenanceSurface.UNIVERSAL_INBOX.value


def _normalize_workload(value: Any) -> str:
    text = str(value or "sensitivity_classification").strip().lower().replace("-", "_").replace(" ", "_")
    allowed = {item.value for item in MaintenanceWorkload}
    return text if text in allowed else MaintenanceWorkload.SENSITIVITY_CLASSIFICATION.value


def _safe_label(value: Any, *, field: str) -> str:
    text = " ".join(str(value or "").split())
    if not text and field == "source_ref":
        raise SensitiveLocalWorkerError("source_ref_required")
    if len(text) > 160:
        text = text[:160]
    lowered = text.lower()
    if any(hint in lowered for hint in ("secret", "token", "password", "api_key", "chat_id")):
        raise SensitiveLocalWorkerError(f"{field}_contains_forbidden_marker")
    if re.search(r"^[A-Za-z]:[\\/]|^/|^~", text):
        raise SensitiveLocalWorkerError(f"{field}_must_not_be_host_path")
    if not _SAFE_ID_RE.fullmatch(text):
        return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
    return text


def _safe_text(value: Any, *, field: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    if _SECRET_VALUE_RE.search(text):
        raise SensitiveLocalWorkerError(f"{field}_contains_secret_marker")
    return text


def _stable_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(str(value or "").encode("utf-8", errors="replace")).hexdigest()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return False
