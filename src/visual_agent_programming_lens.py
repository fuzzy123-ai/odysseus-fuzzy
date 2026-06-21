"""Read-only visual-agent-programming roadmap projection.

The visual editor branch is intentionally observable before it is editable.
This module exposes the branch shape, version layers, controls, and blocked
actions without mutating the roadmap or starting agents.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from src.plan_runtime import PlanRuntimeError, PlanRuntimeNode, PlanRuntimeState


_BRANCH_PREFIX = "visual-agent-programming"
_VALID_EDIT_ACTIONS = {"create_node", "connect_dependency"}
_VALID_EDIT_STATUSES = {"planned", "research", "deferred"}
_VALID_VISUAL_STATUSES = {"draft", "reviewed", "ready", "working", "completed"}
_VISUAL_STATUS_COLORS = {
    "draft": "slate",
    "reviewed": "amber",
    "ready": "blue",
    "working": "violet",
    "completed": "green",
}
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")


class VisualAgentProgrammingLensError(ValueError):
    """Raised when the visual-agent-programming snapshot cannot be built."""


@dataclass(frozen=True, slots=True)
class VisualAgentProgrammingSnapshot:
    snapshot_id: str
    plan_id: str
    mode: str
    active_node_id: str
    next_claimable_node_id: str
    progress: dict[str, int]
    version_layers: tuple[dict[str, Any], ...]
    nodes: tuple[dict[str, Any], ...]
    branch_edges: tuple[dict[str, str], ...]
    controls: dict[str, dict[str, str]]
    blocked_actions: tuple[dict[str, str], ...]
    next_steps: tuple[dict[str, str], ...]
    context_policy: dict[str, str]
    last_updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "plan_id": self.plan_id,
            "mode": self.mode,
            "active_node_id": self.active_node_id,
            "next_claimable_node_id": self.next_claimable_node_id,
            "progress": self.progress,
            "version_layers": list(self.version_layers),
            "nodes": list(self.nodes),
            "branch_edges": list(self.branch_edges),
            "controls": self.controls,
            "blocked_actions": list(self.blocked_actions),
            "next_steps": list(self.next_steps),
            "context_policy": self.context_policy,
            "last_updated_at": self.last_updated_at,
        }


@dataclass(frozen=True, slots=True)
class VisualPlanEditDryRunResult:
    action: str
    state: str
    valid: bool
    can_write: bool
    can_start_agent: bool
    stops: tuple[dict[str, str], ...]
    collisions: tuple[dict[str, str], ...]
    proposed_events: tuple[dict[str, Any], ...]
    accepted_events: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "state": self.state,
            "valid": self.valid,
            "can_write": self.can_write,
            "can_start_agent": self.can_start_agent,
            "stops": list(self.stops),
            "collisions": list(self.collisions),
            "proposed_events": list(self.proposed_events),
            "accepted_events": list(self.accepted_events),
            "policy": {
                "mode": "dry_run_only",
                "write_boundary": "no roadmap file writes in validator slice",
                "agent_start_boundary": "no dry-run result can start an agent",
            },
        }


@dataclass(frozen=True, slots=True)
class VisualPlanProposalQueueSnapshot:
    queue_id: str
    plan_id: str
    mode: str
    items: tuple[dict[str, Any], ...]
    controls: dict[str, dict[str, str]]
    counts: dict[str, int]
    context_policy: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "queue_id": self.queue_id,
            "plan_id": self.plan_id,
            "mode": self.mode,
            "items": list(self.items),
            "controls": self.controls,
            "counts": self.counts,
            "context_policy": self.context_policy,
        }


@dataclass(frozen=True, slots=True)
class VisualPlanAcceptanceContractResult:
    decision: str
    state: str
    valid: bool
    can_write: bool
    can_start_agent: bool
    stops: tuple[dict[str, str], ...]
    audit: dict[str, str]
    event_projection: dict[str, Any]
    accepted_events: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "state": self.state,
            "valid": self.valid,
            "can_write": self.can_write,
            "can_start_agent": self.can_start_agent,
            "stops": list(self.stops),
            "audit": self.audit,
            "event_projection": self.event_projection,
            "accepted_events": list(self.accepted_events),
            "policy": {
                "mode": "operator_acceptance_contract",
                "write_boundary": "event projection only; roadmap mutation belongs to a future adapter",
                "agent_start_boundary": "acceptance never starts agents",
            },
        }


@dataclass(frozen=True, slots=True)
class VisualPlanMutationPatchResult:
    patch_id: str
    state: str
    valid: bool
    can_write: bool
    can_start_agent: bool
    stops: tuple[dict[str, str], ...]
    patch: dict[str, Any]
    audit: dict[str, str]
    version: dict[str, str]
    status_palette: dict[str, str]
    agent_start_request: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "state": self.state,
            "valid": self.valid,
            "can_write": self.can_write,
            "can_start_agent": self.can_start_agent,
            "stops": list(self.stops),
            "patch": self.patch,
            "audit": self.audit,
            "version": self.version,
            "status_palette": self.status_palette,
            "agent_start_request": self.agent_start_request,
            "policy": {
                "mode": "mutation_patch_contract",
                "write_boundary": "patch is authorized but not applied by this endpoint",
                "agent_start_boundary": "agent start is represented as a separate request, not executed here",
            },
        }


def build_visual_agent_programming_snapshot(
    runtime: PlanRuntimeState,
    *,
    last_updated_at: str,
) -> VisualAgentProgrammingSnapshot:
    if not isinstance(runtime, PlanRuntimeState):
        raise VisualAgentProgrammingLensError("runtime must be a PlanRuntimeState")
    if not str(last_updated_at or "").strip():
        raise VisualAgentProgrammingLensError("last_updated_at must not be empty")

    node_map = runtime.node_map()
    branch_nodes = tuple(node for node in runtime.nodes if node.node_id.startswith(_BRANCH_PREFIX))
    if not branch_nodes:
        raise VisualAgentProgrammingLensError("visual-agent-programming branch is missing")

    claimable_ids = {node.node_id for node in runtime.claimable_nodes()}
    next_claimable = runtime.next_claimable_node_id()
    return VisualAgentProgrammingSnapshot(
        snapshot_id=f"{runtime.plan_id}-visual-agent-programming",
        plan_id=runtime.plan_id,
        mode="read_only",
        active_node_id=runtime.recommended_active_node,
        next_claimable_node_id=next_claimable,
        progress=_progress(runtime.nodes, branch_nodes),
        version_layers=_version_layers(branch_nodes),
        nodes=_nodes(branch_nodes, claimable_ids=claimable_ids),
        branch_edges=_branch_edges(branch_nodes, node_map=node_map),
        controls=_controls(),
        blocked_actions=_blocked_actions(),
        next_steps=_next_steps(runtime=runtime, claimable_ids=claimable_ids),
        context_policy=_context_policy(runtime),
        last_updated_at=str(last_updated_at).strip(),
    )


def build_visual_plan_mutation_patch(
    runtime: PlanRuntimeState,
    payload: dict[str, Any],
    *,
    last_updated_at: str,
) -> VisualPlanMutationPatchResult:
    if not isinstance(runtime, PlanRuntimeState):
        raise VisualAgentProgrammingLensError("runtime must be a PlanRuntimeState")
    if not isinstance(payload, dict):
        raise VisualAgentProgrammingLensError("payload must be an object")
    if not str(last_updated_at or "").strip():
        raise VisualAgentProgrammingLensError("last_updated_at must not be empty")

    operator_id = str(payload.get("operator_id", "")).strip()
    permission_mode = str(payload.get("permission_mode", "require_confirmation")).strip().lower()
    mutation_confirmation = str(payload.get("mutation_confirmation", "")).strip()
    visual_status = str(payload.get("visual_status", "draft")).strip().lower()
    proposal = payload.get("proposal", {})
    stops: list[dict[str, str]] = []
    if not operator_id:
        stops.append(_stop("missing_operator_id", "operator_id is required"))
    if permission_mode not in {"require_confirmation", "approve_for_me"}:
        stops.append(_stop("unknown_permission_mode", "permission_mode must be require_confirmation or approve_for_me"))
    if permission_mode == "require_confirmation" and mutation_confirmation != "APPLY_VISUAL_PLAN_MUTATION":
        stops.append(_stop("missing_mutation_confirmation", "mutation_confirmation must be APPLY_VISUAL_PLAN_MUTATION"))
    if visual_status not in _VALID_VISUAL_STATUSES:
        stops.append(_stop("unknown_visual_status", "visual_status must be draft, reviewed, ready, working, or completed"))

    dry_run = validate_visual_plan_edit(runtime, proposal if isinstance(proposal, dict) else {})
    dry_run_payload = dry_run.to_dict()
    if not dry_run_payload["valid"]:
        stops.append(_stop("proposal_not_valid", "only valid dry-run proposals can become mutation patches"))

    can_write = not stops
    agent_start_request = _agent_start_request(payload, can_write=can_write)
    if agent_start_request["state"] == "blocked":
        stops.append(_stop("agent_start_not_authorized", agent_start_request["reason"]))
        can_write = False
        agent_start_request = _agent_start_request(payload, can_write=False)

    patch = _mutation_patch(runtime, proposal if isinstance(proposal, dict) else {}, visual_status=visual_status) if can_write else {}
    patch_id = f"visual-mutation-{_slug(operator_id or 'unknown')}-{_slug(last_updated_at)}"
    return VisualPlanMutationPatchResult(
        patch_id=patch_id,
        state="patch_ready" if can_write else "rejected",
        valid=can_write,
        can_write=can_write,
        can_start_agent=agent_start_request["state"] == "authorized_after_apply",
        stops=tuple(stops),
        patch=patch,
        audit={
            "operator_id": operator_id,
            "permission_mode": permission_mode,
            "mutation_confirmation": mutation_confirmation,
            "self_approved": "true" if permission_mode == "approve_for_me" else "false",
            "roadmap_path": runtime.roadmap_path,
            "created_at": str(last_updated_at).strip(),
        },
        version=_version_metadata(runtime, proposal if isinstance(proposal, dict) else {}, last_updated_at=str(last_updated_at).strip()),
        status_palette=dict(_VISUAL_STATUS_COLORS),
        agent_start_request=agent_start_request,
    )


def validate_visual_plan_acceptance(
    runtime: PlanRuntimeState,
    payload: dict[str, Any],
) -> VisualPlanAcceptanceContractResult:
    if not isinstance(runtime, PlanRuntimeState):
        raise VisualAgentProgrammingLensError("runtime must be a PlanRuntimeState")
    if not isinstance(payload, dict):
        raise VisualAgentProgrammingLensError("payload must be an object")

    decision = str(payload.get("decision", "accept")).strip().lower()
    operator_id = str(payload.get("operator_id", "")).strip()
    confirmation = str(payload.get("operator_confirmation", "")).strip()
    stops: list[dict[str, str]] = []
    if decision not in {"accept", "reject"}:
        stops.append(_stop("unknown_decision", "decision must be accept or reject"))
    if not operator_id:
        stops.append(_stop("missing_operator_id", "operator_id is required for audit"))
    expected_confirmation = "ACCEPT_PLAN_EVENT" if decision == "accept" else "REJECT_PLAN_EVENT"
    if confirmation != expected_confirmation:
        stops.append(_stop("missing_operator_confirmation", f"operator_confirmation must be {expected_confirmation}"))

    proposal = payload.get("proposal", {})
    dry_run = validate_visual_plan_edit(runtime, proposal if isinstance(proposal, dict) else {})
    dry_run_payload = dry_run.to_dict()
    if decision == "accept" and not dry_run_payload["valid"]:
        stops.append(_stop("proposal_not_valid", "only valid dry-run proposals can be accepted"))

    valid = not stops
    event_projection = _acceptance_event_projection(
        decision=decision,
        operator_id=operator_id,
        dry_run_payload=dry_run_payload,
    ) if valid else {}
    return VisualPlanAcceptanceContractResult(
        decision=decision if decision in {"accept", "reject"} else "unknown",
        state=_acceptance_state(decision, valid),
        valid=valid,
        can_write=False,
        can_start_agent=False,
        stops=tuple(stops),
        audit={
            "operator_id": operator_id,
            "confirmation": confirmation,
            "roadmap_path": runtime.roadmap_path,
        },
        event_projection=event_projection,
        accepted_events=(),
    )


def build_visual_plan_proposal_queue(
    runtime: PlanRuntimeState,
    payload: dict[str, Any],
) -> VisualPlanProposalQueueSnapshot:
    if not isinstance(runtime, PlanRuntimeState):
        raise VisualAgentProgrammingLensError("runtime must be a PlanRuntimeState")
    if not isinstance(payload, dict):
        raise VisualAgentProgrammingLensError("payload must be an object")

    proposals = _list(payload.get("proposals", []))
    items: list[dict[str, Any]] = []
    for index, proposal in enumerate(proposals, start=1):
        result = validate_visual_plan_edit(runtime, proposal if isinstance(proposal, dict) else {})
        result_payload = result.to_dict()
        items.append(
            {
                "queue_item_id": f"visual-proposal-{index}",
                "index": index,
                "action": result_payload["action"],
                "state": result_payload["state"],
                "valid": result_payload["valid"],
                "can_write": False,
                "can_start_agent": False,
                "stops": result_payload["stops"],
                "collisions": result_payload["collisions"],
                "proposed_events": result_payload["proposed_events"],
                "accepted_events": [],
            }
        )

    return VisualPlanProposalQueueSnapshot(
        queue_id=f"{runtime.plan_id}-visual-proposal-review-queue",
        plan_id=runtime.plan_id,
        mode="read_only",
        items=tuple(items),
        controls=_proposal_queue_controls(),
        counts={
            "total_items": len(items),
            "valid_items": sum(1 for item in items if item["valid"]),
            "blocked_items": sum(1 for item in items if not item["valid"]),
            "accepted_items": 0,
        },
        context_policy={
            **_context_policy(runtime),
            "queue_state": "ephemeral_dry_run",
            "acceptance_mode": "operator_gated_future_slice",
        },
    )


def validate_visual_plan_edit(runtime: PlanRuntimeState, payload: dict[str, Any]) -> VisualPlanEditDryRunResult:
    if not isinstance(runtime, PlanRuntimeState):
        raise VisualAgentProgrammingLensError("runtime must be a PlanRuntimeState")
    if not isinstance(payload, dict):
        raise VisualAgentProgrammingLensError("payload must be an object")

    action = str(payload.get("action", "")).strip()
    stops: list[dict[str, str]] = []
    collisions: list[dict[str, str]] = []
    proposed_events: list[dict[str, Any]] = []
    if action not in _VALID_EDIT_ACTIONS:
        stops.append(_stop("unknown_action", "action must be create_node or connect_dependency"))
    elif action == "create_node":
        _validate_create_node(runtime, payload, stops=stops, collisions=collisions, proposed_events=proposed_events)
    elif action == "connect_dependency":
        _validate_connect_dependency(runtime, payload, stops=stops, collisions=collisions, proposed_events=proposed_events)

    valid = not stops and not collisions
    return VisualPlanEditDryRunResult(
        action=action or "unknown",
        state="valid_dry_run" if valid else "rejected",
        valid=valid,
        can_write=False,
        can_start_agent=False,
        stops=tuple(stops),
        collisions=tuple(collisions),
        proposed_events=tuple(proposed_events) if valid else (),
        accepted_events=(),
    )


def _validate_create_node(
    runtime: PlanRuntimeState,
    payload: dict[str, Any],
    *,
    stops: list[dict[str, str]],
    collisions: list[dict[str, str]],
    proposed_events: list[dict[str, Any]],
) -> None:
    node_map = runtime.node_map()
    node_id = _slug(payload.get("node_id", ""))
    if not node_id:
        stops.append(_stop("missing_node_id", "node_id must contain slug characters"))
        return
    if node_id in node_map:
        collisions.append(_collision("node_exists", node_id, "a node with this id already exists"))
        return
    depends_on_list = [_slug(item) for item in _list(payload.get("depends_on", []))]
    from_node = _slug(payload.get("from_node", ""))
    if from_node and from_node not in depends_on_list:
        depends_on_list.insert(0, from_node)
    depends_on = tuple(dep for dep in depends_on_list if dep)
    missing_deps = tuple(dep for dep in depends_on if dep not in node_map)
    if missing_deps:
        stops.extend(_stop("unknown_dependency", f"dependency does not exist: {dep}") for dep in missing_deps)
        return
    status = _slug(payload.get("status", "planned")).replace("-", "_")
    if status not in _VALID_EDIT_STATUSES:
        stops.append(_stop("unsafe_status", "new visual nodes may only be planned, research, or deferred in dry-run"))
        return

    new_node = {
        "id": node_id,
        "kind": _slug(payload.get("kind", "runtime")),
        "priority_rank": int(payload.get("priority_rank") or _next_priority(runtime)),
        "title": str(payload.get("title") or node_id).strip(),
        "horizon": _slug(payload.get("horizon", "later")),
        "target_version": str(payload.get("target_version") or "future").strip(),
        "status": status,
        "depends_on": list(depends_on),
        "unlocks": [_slug(item) for item in _list(payload.get("unlocks", []))],
        "gates": [str(item).strip() for item in _list(payload.get("gates", [])) if str(item).strip()],
        "source_refs": [str(item).strip() for item in _list(payload.get("source_refs", [runtime.roadmap_path]))],
        "deliverables": [str(item).strip() for item in _list(payload.get("deliverables", ["Dry-run proposed node"]))],
    }
    _validate_runtime_payload(runtime, extra_nodes=(new_node,), stops=stops)
    proposed_events.append({"type": "plan_node_proposed", "node_id": node_id, "status": status})


def _validate_connect_dependency(
    runtime: PlanRuntimeState,
    payload: dict[str, Any],
    *,
    stops: list[dict[str, str]],
    collisions: list[dict[str, str]],
    proposed_events: list[dict[str, Any]],
) -> None:
    node_map = runtime.node_map()
    from_node = _slug(payload.get("from_node", ""))
    to_node = _slug(payload.get("to_node", ""))
    kind = str(payload.get("kind", "depends_on")).strip().lower().replace("-", "_")
    if not from_node or not to_node:
        stops.append(_stop("missing_endpoint", "from_node and to_node are required"))
        return
    missing = [node_id for node_id in (from_node, to_node) if node_id not in node_map]
    if missing:
        stops.extend(_stop("unknown_endpoint", f"node does not exist: {node_id}") for node_id in missing)
        return
    if from_node == to_node:
        collisions.append(_collision("self_dependency", to_node, "a node cannot depend on itself"))
        return
    if kind not in {"depends_on", "unlocks"}:
        stops.append(_stop("unknown_edge_kind", "kind must be depends_on or unlocks"))
        return
    if kind == "depends_on":
        target = node_map[to_node]
        if from_node in target.depends_on:
            collisions.append(_collision("edge_exists", to_node, "dependency already exists"))
            return
        replacements = {
            to_node: _node_to_payload(target) | {"depends_on": sorted(set(target.depends_on) | {from_node})}
        }
        if _has_dependency_cycle(runtime, extra_dependency=(from_node, to_node)):
            collisions.append(_collision("dependency_cycle", to_node, "the proposed dependency would create a cycle"))
            return
    else:
        source = node_map[from_node]
        if to_node in source.unlocks:
            collisions.append(_collision("edge_exists", from_node, "unlock edge already exists"))
            return
        replacements = {
            from_node: _node_to_payload(source) | {"unlocks": sorted(set(source.unlocks) | {to_node})}
        }
    _validate_runtime_payload(runtime, replacements=replacements, stops=stops)
    proposed_events.append({"type": "plan_edge_proposed", "from": from_node, "to": to_node, "kind": kind})


def _progress(all_nodes: tuple[PlanRuntimeNode, ...], branch_nodes: tuple[PlanRuntimeNode, ...]) -> dict[str, int]:
    executable = [node for node in all_nodes if node.status != "research"]
    branch_executable = [node for node in branch_nodes if node.status != "research"]
    return {
        "total_nodes": len(all_nodes),
        "live_done_nodes": sum(1 for node in all_nodes if node.is_live_done),
        "branch_nodes": len(branch_nodes),
        "branch_live_done_nodes": sum(1 for node in branch_nodes if node.is_live_done),
        "branch_executable_nodes": len(branch_executable),
        "overall_percent": round(100 * sum(1 for node in executable if node.is_live_done) / len(executable))
        if executable
        else 100,
    }


def _version_layers(branch_nodes: tuple[PlanRuntimeNode, ...]) -> tuple[dict[str, Any], ...]:
    layers: dict[tuple[str, str], list[str]] = {}
    for node in branch_nodes:
        layers.setdefault((node.horizon, node.target_version), []).append(node.node_id)
    return tuple(
        {
            "horizon": horizon,
            "target_version": target_version,
            "node_ids": tuple(sorted(node_ids)),
        }
        for (horizon, target_version), node_ids in sorted(layers.items())
    )


def _nodes(branch_nodes: tuple[PlanRuntimeNode, ...], *, claimable_ids: set[str]) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "node_id": node.node_id,
            "title": node.title,
            "kind": node.kind,
            "status": node.status,
            "horizon": node.horizon,
            "target_version": node.target_version,
            "claimable": node.node_id in claimable_ids,
            "live_done": node.is_live_done,
            "depends_on": node.depends_on,
            "unlocks": node.unlocks,
            "gates": node.gates,
            "deliverables": node.deliverables,
            "completion_status": node.completion_status,
            "completion_commit": node.completion_commit,
            "visual_status": _visual_status(node),
            "visual_color": _VISUAL_STATUS_COLORS[_visual_status(node)],
        }
        for node in branch_nodes
    )


def _branch_edges(
    branch_nodes: tuple[PlanRuntimeNode, ...],
    *,
    node_map: dict[str, PlanRuntimeNode],
) -> tuple[dict[str, str], ...]:
    branch_ids = {node.node_id for node in branch_nodes}
    edges: list[dict[str, str]] = []
    for node in branch_nodes:
        for dep in node.depends_on:
            if dep in branch_ids or dep in node_map:
                edges.append({"from": dep, "to": node.node_id, "kind": "depends_on"})
        for unlocked in node.unlocks:
            if unlocked in branch_ids:
                edges.append({"from": node.node_id, "to": unlocked, "kind": "unlocks"})
    return tuple(edges)


def _controls() -> dict[str, dict[str, str]]:
    reason = "visual-agent-programming read-only slice; mutations require the dry-run validator and operator go"
    return {
        "create_slice": {"state": "policy_gated", "reason": reason},
        "connect_dependency": {"state": "policy_gated", "reason": reason},
        "assign_agent": {"state": "policy_gated", "reason": reason},
        "start_run": {"state": "policy_gated", "reason": "agent start requires explicit operator action"},
    }


def _proposal_queue_controls() -> dict[str, dict[str, str]]:
    reason = "proposal review queue is read-only; accepted plan events require a future operator-go slice"
    return {
        "accept_proposal": {"state": "policy_gated", "reason": reason},
        "reject_proposal": {"state": "policy_gated", "reason": reason},
        "apply_to_roadmap": {"state": "policy_gated", "reason": "no direct roadmap write from queue"},
        "start_agent": {"state": "policy_gated", "reason": "no agent start from queued proposals"},
    }


def _acceptance_state(decision: str, valid: bool) -> str:
    if not valid:
        return "rejected"
    if decision == "reject":
        return "rejected_event_ready"
    return "accepted_event_ready"


def _acceptance_event_projection(
    *,
    decision: str,
    operator_id: str,
    dry_run_payload: dict[str, Any],
) -> dict[str, Any]:
    event_type = "visual_plan_proposal_rejected" if decision == "reject" else "visual_plan_proposal_accepted"
    proposed_events = dry_run_payload.get("proposed_events", [])
    return {
        "type": event_type,
        "operator_id": operator_id,
        "source_action": dry_run_payload.get("action", "unknown"),
        "source_state": dry_run_payload.get("state", "unknown"),
        "proposed_events": proposed_events,
        "requires_future_write_adapter": True,
    }


def _mutation_patch(runtime: PlanRuntimeState, proposal: dict[str, Any], *, visual_status: str) -> dict[str, Any]:
    action = str(proposal.get("action", "")).strip()
    if action == "create_node":
        node_id = _slug(proposal.get("node_id", ""))
        from_node = _slug(proposal.get("from_node", ""))
        depends_on = [dep for dep in [_slug(item) for item in _list(proposal.get("depends_on", []))] if dep]
        if from_node and from_node not in depends_on:
            depends_on.insert(0, from_node)
        return {
            "target_path": runtime.roadmap_path,
            "operations": [
                {
                    "op": "add_node",
                    "node": {
                        "id": node_id,
                        "kind": _slug(proposal.get("kind", "runtime")),
                        "priority_rank": int(proposal.get("priority_rank") or _next_priority(runtime)),
                        "title": str(proposal.get("title") or node_id).strip(),
                        "horizon": _slug(proposal.get("horizon", "later")),
                        "target_version": str(proposal.get("target_version") or "future").strip(),
                        "status": str(proposal.get("status") or "planned").strip(),
                        "visual_status": visual_status,
                        "depends_on": depends_on,
                        "unlocks": [_slug(item) for item in _list(proposal.get("unlocks", []))],
                        "gates": [str(item).strip() for item in _list(proposal.get("gates", [])) if str(item).strip()],
                        "source_refs": [
                            str(item).strip()
                            for item in _list(proposal.get("source_refs", [runtime.roadmap_path]))
                            if str(item).strip()
                        ],
                        "deliverables": [
                            str(item).strip()
                            for item in _list(proposal.get("deliverables", ["Visual plan node"]))
                            if str(item).strip()
                        ],
                    },
                },
                *(
                    [
                        {
                            "op": "add_edge",
                            "from": from_node,
                            "to": node_id,
                            "kind": "depends_on",
                        }
                    ]
                    if from_node
                    else []
                ),
            ],
        }
    if action == "connect_dependency":
        return {
            "target_path": runtime.roadmap_path,
            "operations": [
                {
                    "op": "add_edge",
                    "from": _slug(proposal.get("from_node", "")),
                    "to": _slug(proposal.get("to_node", "")),
                    "kind": str(proposal.get("kind", "depends_on")).strip().lower().replace("-", "_"),
                    "visual_status": visual_status,
                }
            ],
        }
    return {"target_path": runtime.roadmap_path, "operations": []}


def _version_metadata(runtime: PlanRuntimeState, proposal: dict[str, Any], *, last_updated_at: str) -> dict[str, str]:
    action = str(proposal.get("action", "")).strip()
    node_delta = 1 if action == "create_node" else 0
    base = f"nodes-{len(runtime.nodes)}-live-{sum(1 for node in runtime.nodes if node.is_live_done)}"
    next_version = f"nodes-{len(runtime.nodes) + node_delta}-live-{sum(1 for node in runtime.nodes if node.is_live_done)}"
    return {
        "base_version": base,
        "next_version": next_version,
        "created_at": last_updated_at,
        "version_scheme": "node-count/live-count/timestamp",
    }


def _agent_start_request(payload: dict[str, Any], *, can_write: bool) -> dict[str, str]:
    if not payload.get("start_agent_after_apply"):
        return {"state": "not_requested", "reason": "no agent start requested"}
    confirmation = str(payload.get("agent_start_confirmation", "")).strip()
    if confirmation != "START_AGENT_AFTER_MUTATION":
        return {"state": "blocked", "reason": "agent_start_confirmation must be START_AGENT_AFTER_MUTATION"}
    if not can_write:
        return {"state": "blocked", "reason": "mutation patch is not write-authorized"}
    return {"state": "authorized_after_apply", "reason": "agent start is authorized only after a future apply adapter succeeds"}


def _visual_status(node: PlanRuntimeNode) -> str:
    if node.is_live_done:
        return "completed"
    if node.status == "active":
        return "working"
    if node.status in {"planned", "active_candidate"}:
        return "ready"
    if node.status in {"done", "partial"}:
        return "reviewed"
    return "draft"


def _blocked_actions() -> tuple[dict[str, str], ...]:
    return (
        {
            "action": "mutate_graph",
            "state": "blocked",
            "reason": "no_graph_mutation gate is active for this slice",
        },
        {
            "action": "start_agent",
            "state": "blocked",
            "reason": "no_agent_start gate is active for this slice",
        },
        {
            "action": "accept_report_directly",
            "state": "blocked",
            "reason": "agent reports must be reduced into accepted plan events before future context uses them",
        },
    )


def _next_steps(*, runtime: PlanRuntimeState, claimable_ids: set[str]) -> tuple[dict[str, str], ...]:
    node_map = runtime.node_map()
    steps: list[dict[str, str]] = []
    for node_id in runtime.next_action_node_ids:
        node = node_map[node_id]
        if node.status == "research":
            state = "research"
        elif node.node_id in claimable_ids:
            state = "claimable"
        else:
            state = "waiting"
        steps.append(
            {
                "node_id": node.node_id,
                "title": node.title,
                "state": state,
                "target_version": node.target_version,
            }
        )
    return tuple(steps)


def _context_policy(runtime: PlanRuntimeState) -> dict[str, str]:
    return {
        "source_of_truth": runtime.roadmap_path,
        "agent_write_mode": "reports_only_until_reducer_accepts_events",
        "live_boundary": "one_feature_must_be_live_installed_before_next_feature",
        "mutation_mode": "read_only",
    }


def _validate_runtime_payload(
    runtime: PlanRuntimeState,
    *,
    stops: list[dict[str, str]],
    extra_nodes: tuple[dict[str, Any], ...] = (),
    replacements: dict[str, dict[str, Any]] | None = None,
) -> None:
    try:
        PlanRuntimeState.from_dict(_runtime_payload(runtime, extra_nodes=extra_nodes, replacements=replacements or {}))
    except (PlanRuntimeError, TypeError, ValueError) as exc:
        stops.append(_stop("planruntime_validation_failed", str(exc)))


def _runtime_payload(
    runtime: PlanRuntimeState,
    *,
    extra_nodes: tuple[dict[str, Any], ...],
    replacements: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    nodes = []
    for node in runtime.nodes:
        nodes.append(replacements.get(node.node_id, _node_to_payload(node)))
    nodes.extend(extra_nodes)
    return {
        "schema_version": 1,
        "plan_id": runtime.plan_id,
        "title": runtime.title,
        "format_decision": {"source_of_truth": runtime.source_of_truth},
        "recommended_active_node": runtime.recommended_active_node,
        "version_horizons": [{"id": horizon} for horizon in runtime.version_horizons],
        "graph_nodes": nodes,
        "next_actions": [{"node_id": node_id} for node_id in runtime.next_action_node_ids],
        "plan_graph_projection": {"status_mapping": runtime.status_mapping},
    }


def _node_to_payload(node: PlanRuntimeNode) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": node.node_id,
        "kind": node.kind,
        "priority_rank": node.priority_rank,
        "title": node.title,
        "horizon": node.horizon,
        "target_version": node.target_version,
        "status": node.status,
        "depends_on": list(node.depends_on),
        "unlocks": list(node.unlocks),
        "gates": list(node.gates),
        "source_refs": list(node.source_refs),
        "deliverables": list(node.deliverables),
    }
    if node.completion_status:
        payload["completion_state"] = {"status": node.completion_status, "commit": node.completion_commit}
    return payload


def _has_dependency_cycle(runtime: PlanRuntimeState, *, extra_dependency: tuple[str, str]) -> bool:
    source, target = extra_dependency
    deps: dict[str, set[str]] = {node.node_id: set(node.depends_on) for node in runtime.nodes}
    deps.setdefault(target, set()).add(source)

    def visit(node_id: str, path: set[str]) -> bool:
        if node_id in path:
            return True
        return any(visit(dep, path | {node_id}) for dep in deps.get(node_id, set()))

    return any(visit(node_id, set()) for node_id in deps)


def _next_priority(runtime: PlanRuntimeState) -> int:
    return max((node.priority_rank for node in runtime.nodes), default=0) + 1


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _slug(value: Any) -> str:
    normalized = _NON_SLUG_CHARS_RE.sub("-", str(value or "").strip().lower()).strip("-")
    return re.sub(r"-{2,}", "-", normalized)


def _stop(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _collision(code: str, node_id: str, message: str) -> dict[str, str]:
    return {"code": code, "node_id": node_id, "message": message}
