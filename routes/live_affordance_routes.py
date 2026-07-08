"""Read-only readiness routes for live affordances."""

from __future__ import annotations

from fastapi import APIRouter, Request

from core.middleware import require_admin
from src.gate_evidence_adapters import adapt_live_affordance_readiness
from src.gate_evidence_core import what_can_safely_happen_now
from src.live_affordance_readiness import build_live_affordance_readiness


def setup_live_affordance_routes() -> APIRouter:
    router = APIRouter(tags=["live-affordances"])

    @router.get("/api/live-affordances/readiness")
    def live_affordance_readiness(request: Request):
        require_admin(request)
        payload = build_live_affordance_readiness()
        canonical_gates = adapt_live_affordance_readiness(payload)
        return {
            **payload,
            "canonical_gate_evidence": [gate.to_dict() for gate in canonical_gates],
            "canonical_safe_now": what_can_safely_happen_now(canonical_gates),
        }

    return router
