"""Pure Planning-bound reducer for non-executing scoped sandbox job plans."""

from __future__ import annotations

from typing import Any

from src.coding_execution_contracts import (
    BoundedCheckRequest,
    CodingExecutionContractError,
    SandboxJobRequest,
    SandboxMount,
    create_sandbox_job_request,
)
from src.coding_lifecycle_authority import CodingLifecycleAuthority
from src.coding_loop_contracts import CodingLoopIntentKind
from src.coding_loop_controller import CodingLoopControllerState, CodingLoopDisposition
from src.coding_subagent_capsule import CodingSubagentLifecycleDescriptor, CodingSubagentRole


class CodingExecutionPlaneError(CodingExecutionContractError):
    """Raised when Planning, controller, capsule, or check facts do not bind."""


def reduce_scoped_execution_plan(
    controller: CodingLoopControllerState, *, request: BoundedCheckRequest
) -> SandboxJobRequest:
    """Render one deterministic job plan; this function never dispatches it."""

    if not isinstance(controller, CodingLoopControllerState):
        raise CodingExecutionPlaneError("controller must be typed")
    if not isinstance(request, BoundedCheckRequest):
        raise CodingExecutionPlaneError("request must be typed")
    if controller.lifecycle.state != "verifying" or controller.disposition is not CodingLoopDisposition.RUNNING:
        raise CodingExecutionPlaneError("controller must be actively verifying")
    intents = tuple(
        item for item in controller.intents
        if item.intent_kind is CodingLoopIntentKind.REQUEST_BOUNDED_CHECK
    )
    if len(intents) != 1:
        raise CodingExecutionPlaneError("controller must contain exactly one bounded-check intent")
    intent = intents[0]
    if intent.role != CodingSubagentRole.TESTER.value:
        raise CodingExecutionPlaneError("bounded-check intent must be tester scoped")
    capsule = next((item for item in controller.capsules if item.capsule_id == intent.capsule_id), None)
    if capsule is None or capsule.role is not CodingSubagentRole.TESTER:
        raise CodingExecutionPlaneError("tester capsule binding is required")
    if capsule.lifecycle_descriptor is not CodingSubagentLifecycleDescriptor.CAPSULE_READY:
        raise CodingExecutionPlaneError("tester capsule is not ready")
    _validate_authority(controller.lifecycle.authority)
    _validate_request_binding(controller, request, intent, capsule)
    if request.check_ref not in capsule.acceptance_check_refs:
        raise CodingExecutionPlaneError("check is not authorized by tester capsule")
    if request.capability_ref not in capsule.tool_capability_refs:
        raise CodingExecutionPlaneError("capability is not authorized by tester capsule")
    if request.resources.wall_time_seconds > capsule.time_budget_seconds:
        raise CodingExecutionPlaneError("requested check exceeds tester time budget")
    for target in request.argv[4:]:
        if not any(_scope_contains(scope, target) for scope in controller.lifecycle.authority.claim_scope):
            raise CodingExecutionPlaneError("argv target is outside claim scope")
    mounts = tuple(SandboxMount(repo_path=path) for path in controller.lifecycle.authority.claim_scope)
    return create_sandbox_job_request(request, mounts=mounts)


def _validate_authority(authority: CodingLifecycleAuthority) -> None:
    if not authority.claim_scope:
        raise CodingExecutionPlaneError("claim scope must be non-empty")
    errors = authority.validation_errors_for_state("verifying")
    if errors:
        raise CodingExecutionPlaneError("claim scope fails Planning authority validation")


def _validate_request_binding(controller: CodingLoopControllerState, request: BoundedCheckRequest, intent: Any, capsule: Any) -> None:
    envelope = controller.parent_envelope
    authority = controller.lifecycle.authority
    if envelope is None:
        raise CodingExecutionPlaneError("controller requires a parent envelope")
    expected = {
        "controller_state_id": controller.state_id,
        "intent_id": intent.intent_id,
        "planning_item_id": authority.planning_item_id,
        "planning_revision": authority.planning_revision,
        "claim_id": authority.claim_id,
        "claim_owner": authority.claim_owner,
        "scope_digest": authority.claim_scope_digest,
        "input_revision": authority.input_revision,
        "parent_envelope_id": envelope.envelope_id,
        "capsule_id": capsule.capsule_id,
    }
    for field, value in expected.items():
        if getattr(request, field) != value:
            raise CodingExecutionPlaneError("check request authority binding mismatch")
    if (
        capsule.planning_item_id != authority.planning_item_id
        or capsule.planning_revision != authority.planning_revision
        or capsule.claim_id != authority.claim_id
        or capsule.claim_owner != authority.claim_owner
        or capsule.scope_digest != authority.claim_scope_digest
        or capsule.input_revision != authority.input_revision
        or intent.parent_envelope_id != envelope.envelope_id
        or intent.exact_read_required_ref not in capsule.exact_read_refs
    ):
        raise CodingExecutionPlaneError("intent or capsule authority binding mismatch")


def _scope_contains(scope: str, path: str) -> bool:
    return path == scope or path.startswith(f"{scope}/")


__all__ = ["CodingExecutionPlaneError", "reduce_scoped_execution_plan"]
