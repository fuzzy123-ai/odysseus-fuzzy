"""Deterministic digest helpers for orchestration activation bundles."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from src.orchestration_activation_bundle import OrchestrationActivationBundle


def render_activation_bundle_canonical_json(
    bundle: OrchestrationActivationBundle,
    *,
    include_generated_at: bool = False,
) -> str:
    if not isinstance(bundle, OrchestrationActivationBundle):
        raise TypeError("bundle must be an OrchestrationActivationBundle")
    payload = _canonical_payload(bundle, include_generated_at=include_generated_at)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_activation_bundle(
    bundle: OrchestrationActivationBundle,
    *,
    include_generated_at: bool = False,
) -> str:
    return hashlib.sha256(
        render_activation_bundle_canonical_json(bundle, include_generated_at=include_generated_at).encode("utf-8")
    ).hexdigest()


def _canonical_payload(
    bundle: OrchestrationActivationBundle,
    *,
    include_generated_at: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "label": bundle.label,
        "readiness_report": bundle.readiness_report.to_dict(),
        "activation_plan": bundle.activation_plan.to_dict(),
        "summary": bundle.summary.to_dict(),
    }
    if include_generated_at:
        payload["generated_at"] = bundle.generated_at
    return payload
