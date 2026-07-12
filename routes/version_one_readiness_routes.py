"""Admin-gated Version 1.0 readiness route."""

from __future__ import annotations

from fastapi import APIRouter, Request

from core.middleware import require_admin
from src.gate_evidence_adapters import adapt_release_readiness
from src.gate_evidence_core import what_can_safely_happen_now
from src.version_one_readiness import load_version_one_readiness


def setup_version_one_readiness_routes() -> APIRouter:
    router = APIRouter(tags=["version-one"])

    @router.get("/api/version-one/readiness")
    def version_one_readiness(request: Request):
        require_admin(request)
        payload = load_version_one_readiness()
        canonical_gate = adapt_release_readiness(payload, gate_id="version-one-readiness")
        return {
            **payload,
            "canonical_gate_evidence": [canonical_gate.to_dict()],
            "canonical_safe_now": what_can_safely_happen_now([canonical_gate]),
        }

    return router
