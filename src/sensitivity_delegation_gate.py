"""Sensitivity delegation policy for local workers and API orchestrators.

The gate is deliberately side-effect free. It never calls providers, reads
documents, or persists prompts. Callers pass already-known runtime metadata and
receive a compact decision about whether raw content must stay local, whether an
external model may see only redacted context, or whether direct external routing
is allowed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from src.data_classification import DataClassification, resolve_classification
from src.privacy_runtime import is_dsgvo_mode_enabled


DELEGATION_GATE_SCHEMA = "odysseus.sensitivity_delegation_gate.v1"


class DelegationMode(StrEnum):
    LOCAL_RAW_WORKER = "local_raw_worker"
    EXTERNAL_REDACTED_ORCHESTRATOR = "external_redacted_orchestrator"
    EXTERNAL_DIRECT = "external_direct"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class SensitivityDelegationDecision:
    mode: DelegationMode
    classification: str
    dsgvo_mode: bool
    local_worker_required: bool
    external_orchestrator_allowed: bool
    external_raw_allowed: bool
    external_redacted_allowed: bool
    local_raw_allowed: bool
    redacted_context_required: bool
    reasons: tuple[str, ...]
    raw_content_visible: bool
    schema: str = DELEGATION_GATE_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "mode": self.mode.value,
            "classification": self.classification,
            "dsgvo_mode": self.dsgvo_mode,
            "local_worker_required": self.local_worker_required,
            "external_orchestrator_allowed": self.external_orchestrator_allowed,
            "external_raw_allowed": self.external_raw_allowed,
            "external_redacted_allowed": self.external_redacted_allowed,
            "local_raw_allowed": self.local_raw_allowed,
            "redacted_context_required": self.redacted_context_required,
            "reasons": self.reasons,
            "raw_content_visible": self.raw_content_visible,
        }


def decide_sensitivity_delegation(
    *,
    dsgvo_mode: bool | None = None,
    classification: Any = None,
    raw_content_visible: bool = False,
    api_model_allowed: bool = False,
    local_only_required: bool = False,
    redacted_context_available: bool = False,
    settings: Mapping[str, Any] | None = None,
) -> SensitivityDelegationDecision:
    """Decide how a request may be delegated across local/API model boundaries."""

    effective_dsgvo = bool(is_dsgvo_mode_enabled(settings=settings) if dsgvo_mode is None else dsgvo_mode)
    normalized = _normalize_classification(classification)
    sensitive = normalized in {DataClassification.SENSITIVE.value, DataClassification.SECRET.value}
    unknown = normalized == "unknown"

    reasons: list[str] = []
    if effective_dsgvo:
        reasons.append("dsgvo_mode")
    if bool(local_only_required):
        reasons.append("local_only_required")
    if sensitive:
        reasons.append(f"{normalized}_classification")
    if unknown:
        reasons.append("unknown_classification")
    if bool(raw_content_visible):
        reasons.append("raw_content_visible")

    local_worker_required = bool(effective_dsgvo or local_only_required or sensitive or unknown)
    external_raw_allowed = bool(api_model_allowed and not local_worker_required)
    external_redacted_allowed = bool(redacted_context_available and not effective_dsgvo)
    external_orchestrator_allowed = bool(external_raw_allowed or external_redacted_allowed)

    if local_worker_required:
        if external_redacted_allowed:
            mode = DelegationMode.EXTERNAL_REDACTED_ORCHESTRATOR
        else:
            mode = DelegationMode.LOCAL_RAW_WORKER
    elif external_raw_allowed:
        mode = DelegationMode.EXTERNAL_DIRECT
    elif external_redacted_allowed:
        mode = DelegationMode.EXTERNAL_REDACTED_ORCHESTRATOR
    else:
        mode = DelegationMode.BLOCKED
        if "api_model_not_allowed" not in reasons:
            reasons.append("api_model_not_allowed")

    return SensitivityDelegationDecision(
        mode=mode,
        classification=normalized,
        dsgvo_mode=effective_dsgvo,
        local_worker_required=local_worker_required,
        external_orchestrator_allowed=external_orchestrator_allowed,
        external_raw_allowed=external_raw_allowed,
        external_redacted_allowed=external_redacted_allowed,
        local_raw_allowed=True,
        redacted_context_required=mode == DelegationMode.EXTERNAL_REDACTED_ORCHESTRATOR,
        reasons=tuple(dict.fromkeys(reasons)),
        raw_content_visible=bool(raw_content_visible),
    )


def _normalize_classification(value: Any) -> str:
    resolution = resolve_classification(value)
    if resolution.normalized is None:
        return "unknown"
    return resolution.normalized.value
