"""Post-apply dispatch request contract for visual PlanRuntime mutations.

This module deliberately stops before runtime execution. It turns an applied
visual mutation payload into a confirmed, auditable dispatch request object, but
it never starts an agent, sends a thread message, or writes state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.plan_runtime import PlanRuntimeError, PlanRuntimeState
from src.subagent_plan_binding import SubagentPlanBindingError, build_subagent_spec_from_plan_runtime


DISPATCH_CONFIRMATION_TOKEN = "REQUEST_POST_APPLY_AGENT_DISPATCH"


class PlanRuntimePostApplyDispatchError(ValueError):
    """Raised when a post-apply dispatch request cannot be built."""


@dataclass(frozen=True, slots=True)
class PlanRuntimePostApplyDispatchRequest:
    request_id: str
    state: str
    valid: bool
    can_start_agent: bool
    dispatched: bool
    stops: tuple[dict[str, str], ...]
    dispatch: dict[str, str]
    audit: dict[str, str]
    subagent_run_spec: dict[str, Any]
    policy: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "state": self.state,
            "valid": self.valid,
            "can_start_agent": self.can_start_agent,
            "dispatched": self.dispatched,
            "stops": list(self.stops),
            "dispatch": self.dispatch,
            "audit": self.audit,
            "subagent_run_spec": self.subagent_run_spec,
            "policy": self.policy,
        }


def build_post_apply_agent_dispatch_request(
    payload: dict[str, Any],
    *,
    created_at: str,
) -> PlanRuntimePostApplyDispatchRequest:
    """Build a safe dispatch request from an applied visual mutation payload."""

    if not isinstance(payload, dict):
        raise PlanRuntimePostApplyDispatchError("payload must be an object")
    timestamp = _normalize_timestamp(created_at)
    apply_result = payload.get("apply_result", {})
    if hasattr(apply_result, "to_dict"):
        apply_result = apply_result.to_dict()
    if not isinstance(apply_result, dict):
        raise PlanRuntimePostApplyDispatchError("apply_result must be an object")

    operator_id = _clean_text(payload.get("operator_id", "") or _audit(apply_result).get("operator_id", ""))
    agent_id = _clean_slug(payload.get("agent_id", "bob") or "bob")
    node_id = _clean_slug(payload.get("node_id", ""))
    dispatch_confirmation = _clean_text(payload.get("dispatch_confirmation", ""))
    stops: list[dict[str, str]] = []

    if not operator_id:
        stops.append(_stop("missing_operator_id", "operator_id is required for dispatch audit"))
    if apply_result.get("state") != "applied_to_payload" or apply_result.get("valid") is not True:
        stops.append(_stop("apply_not_ready", "apply_result must be applied_to_payload and valid=true"))
    request = apply_result.get("agent_start_request", {})
    if not isinstance(request, dict) or request.get("state") != "ready_for_dispatch":
        stops.append(_stop("no_ready_dispatch_request", "apply_result does not contain a ready_for_dispatch request"))
    if dispatch_confirmation != DISPATCH_CONFIRMATION_TOKEN:
        stops.append(
            _stop(
                "missing_dispatch_confirmation",
                f"dispatch_confirmation must be {DISPATCH_CONFIRMATION_TOKEN}",
            )
        )

    spec_payload: dict[str, Any] = {}
    dispatch: dict[str, str] = {"state": "blocked", "reason": "dispatch request was not authorized"}
    if not stops:
        try:
            applied_payload = apply_result.get("applied_payload", {})
            runtime = PlanRuntimeState.from_dict(applied_payload)
            spec = build_subagent_spec_from_plan_runtime(
                runtime,
                node_id=node_id,
                agent_id=agent_id,
                role_id=_clean_slug(payload.get("role_id", "")),
                model=_clean_text(payload.get("model", "fake-model") or "fake-model"),
                thinking=_clean_text(payload.get("thinking", "medium") or "medium"),
                created_at=timestamp,
            )
            spec_payload = _spec_payload(spec)
            dispatch = {
                "state": "request_ready",
                "node_id": spec.node_id,
                "agent_id": spec.agent_id,
                "agent_run_id": spec.agent_run_id,
                "target_kind": spec.target_kind.value,
                "reason": "confirmed request object only; orchestration runtime must execute separately",
            }
        except (PlanRuntimeError, SubagentPlanBindingError, TypeError, ValueError) as exc:
            stops.append(_stop("dispatch_spec_not_ready", str(exc)))

    valid = not stops
    audit = _audit(apply_result) | {
        "operator_id": operator_id,
        "dispatch_confirmation": dispatch_confirmation,
        "requested_at": timestamp,
    }
    request_id = f"post-apply-dispatch-{_clean_slug(audit.get('operator_id', 'unknown'))}-{_clean_slug(timestamp)}"
    return PlanRuntimePostApplyDispatchRequest(
        request_id=request_id,
        state="dispatch_request_ready" if valid else "blocked",
        valid=valid,
        can_start_agent=False,
        dispatched=False,
        stops=tuple(stops),
        dispatch=dispatch,
        audit=audit,
        subagent_run_spec=spec_payload,
        policy={
            "mode": "post_apply_dispatch_request",
            "execution_boundary": "request object only; no thread, job, process, or live agent is started",
            "confirmation_token": DISPATCH_CONFIRMATION_TOKEN,
            "apply_boundary": "requires a valid applied visual mutation payload with ready_for_dispatch",
        },
    )


def _audit(apply_result: dict[str, Any]) -> dict[str, str]:
    audit = apply_result.get("audit", {})
    if not isinstance(audit, dict):
        return {}
    return {str(key): _clean_text(value) for key, value in audit.items()}


def _spec_payload(spec: Any) -> dict[str, Any]:
    return {
        "agent_run_id": spec.agent_run_id,
        "plan_id": spec.plan_id,
        "node_id": spec.node_id,
        "slice_id": spec.slice_id,
        "agent_id": spec.agent_id,
        "role_id": spec.role_id,
        "objective": spec.objective,
        "allowed_files": list(spec.allowed_files),
        "blocked_files": list(spec.blocked_files),
        "inputs": spec.inputs,
        "expected_outputs": list(spec.expected_outputs),
        "tests": list(spec.tests),
        "handoff_format": list(spec.handoff_format),
        "stop_conditions": list(spec.stop_conditions),
        "evidence_required": list(spec.evidence_required),
        "model": spec.model,
        "thinking": spec.thinking,
        "created_at": spec.created_at,
        "target_kind": spec.target_kind.value,
        "thread_id": spec.thread_id,
        "job_id": spec.job_id,
    }


def _normalize_timestamp(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        raise PlanRuntimePostApplyDispatchError("created_at must not be empty")
    if text.endswith("+00:00"):
        return f"{text[:-6]}Z"
    return text


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _clean_slug(value: Any) -> str:
    return "-".join(_clean_text(value).lower().replace("_", "-").split())


def _stop(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}
