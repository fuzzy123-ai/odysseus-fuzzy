"""Pure projections for immutable Planning Definition v2 documents."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import re
from typing import Any, Iterable, Mapping

from src.planning_definition_contract import (
    FORBIDDEN_EXECUTION_STATES,
    PLANNING_DEFINITION_SCHEMA_ID,
    REVISION_STATES,
    PlanningDefinitionContractError,
    compute_roadmap_content_hash,
    validate_planning_definition,
)


READ_MODEL_SCHEMA_ID = "odysseus.planning.definition_read_model.v2"
MAINTENANCE_HANDOFF_SCHEMA_ID = "odysseus.agent_maintenance_handoff.v1"
MAINTENANCE_HANDOFF_MAX_RECORDS = 128
LEGACY_KINDS = frozenset({"odysseus.planning.roadmap", "harbor.planning.roadmap"})
ORIGIN_STATES = frozenset({"loading", "live", "stale", "unavailable", "error"})
_MAINTENANCE_RESOLVED_STATES = frozenset(
    {"accepted", "closed", "completed", "deferred", "done", "resolved", "satisfied"}
)
_MAINTENANCE_SENSITIVE_PARTS = frozenset(
    {
        "body",
        "chat",
        "content",
        "credential",
        "email",
        "message",
        "password",
        "path",
        "private",
        "prompt",
        "raw",
        "secret",
        "token",
        "transcript",
        "value",
    }
)


class PlanningDefinitionProjectionError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class PlanningDefinitionProjector:
    """Normalize canonical or legacy definitions without reading runtime state."""

    def normalize_document(
        self,
        payload: Mapping[str, Any],
        *,
        source_ref: str = "",
    ) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise PlanningDefinitionProjectionError("invalid_definition", "definition must be an object")
        if payload.get("schema_id") == PLANNING_DEFINITION_SCHEMA_ID:
            document = deepcopy(dict(payload))
        elif payload.get("kind") in LEGACY_KINDS:
            document = self._project_legacy(payload, source_ref=source_ref)
        else:
            raise PlanningDefinitionProjectionError(
                "unsupported_definition",
                "definition is neither Planning Definition v2 nor a supported legacy roadmap",
            )
        try:
            validate_planning_definition(document)
        except PlanningDefinitionContractError as exc:
            raise PlanningDefinitionProjectionError(exc.reason_code, exc.detail) from exc
        return document

    def project_revision(
        self,
        *,
        project: Mapping[str, Any],
        roadmap: Mapping[str, Any],
        origin_state: str = "live",
        origin_reason: str = "canonical_definition_available",
    ) -> dict[str, Any]:
        state = _origin_state(origin_state)
        project_id = str(project.get("project_id") or "")
        roadmap_id = str(roadmap.get("roadmap_id") or "")
        revision = roadmap.get("revision")
        document = {
            "schema_id": PLANNING_DEFINITION_SCHEMA_ID,
            "project": deepcopy(dict(project)),
            "roadmaps": [deepcopy(dict(roadmap))],
        }
        # A single-revision read model needs a matching project reference set.
        document["project"]["roadmap_refs"] = [roadmap_id]
        latest = document["project"].get("latest_approved_revision", {})
        document["project"]["latest_approved_revision"] = (
            {roadmap_id: deepcopy(latest[roadmap_id])}
            if roadmap_id in latest and latest[roadmap_id].get("revision") == revision
            else {}
        )
        document["project"]["draft_refs"] = [
            deepcopy(item)
            for item in document["project"].get("draft_refs", [])
            if item.get("roadmap_id") == roadmap_id and item.get("base_revision") == revision
        ]
        try:
            validate_planning_definition(document)
        except PlanningDefinitionContractError as exc:
            raise PlanningDefinitionProjectionError(exc.reason_code, exc.detail) from exc
        return {
            "schema": READ_MODEL_SCHEMA_ID,
            "project": deepcopy(document["project"]),
            "roadmap": deepcopy(document["roadmaps"][0]),
            "graph": {
                "nodes": deepcopy(document["roadmaps"][0]["nodes"]),
                "edges": deepcopy(document["roadmaps"][0]["edges"]),
                "gate_definitions": deepcopy(document["roadmaps"][0]["gates"]),
            },
            "origin": origin_metadata(
                state,
                source="planning_revision_store",
                reason=origin_reason,
                as_of=str(roadmap.get("updated_at") or ""),
            ),
            "read_only": True,
            "launch_authorized": False,
        }

    def canonical_bytes(self, value: Mapping[str, Any]) -> bytes:
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise PlanningDefinitionProjectionError(
                "non_canonical_value", "projection contains a non-JSON value"
            ) from exc

    def _project_legacy(
        self,
        payload: Mapping[str, Any],
        *,
        source_ref: str,
    ) -> dict[str, Any]:
        project_id = _identifier(payload.get("project_id"), fallback="legacy-project")
        roadmap_id = _identifier(
            payload.get("roadmap_id") or payload.get("id"),
            fallback=_source_identifier(source_ref) or "legacy-roadmap",
        )
        revision = payload.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            revision = 1
        revision_state = str(payload.get("revision_state") or payload.get("status") or "draft").lower()
        if revision_state not in REVISION_STATES or revision_state in FORBIDDEN_EXECUTION_STATES:
            revision_state = "draft"

        raw_nodes = _legacy_list(payload, "slice_queue", "slices", "tasks", "nodes")
        node_ids = _deduplicated_ids(raw_nodes, "node", ("node_id", "slice_id", "id"))
        if not raw_nodes:
            raw_nodes = [{}]
            node_ids = ["legacy-root"]
        node_set = set(node_ids)

        raw_gates = _legacy_list(payload, "gate_queue", "gates")
        gate_ids = _deduplicated_ids(raw_gates, "gate", ("gate_id", "id"))
        gate_set = set(gate_ids)
        rule_id = "legacy-definition-valid"

        nodes: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_nodes):
            item = raw if isinstance(raw, Mapping) else {}
            dependencies = [
                value for value in _identifier_list(item.get("depends_on")) if value in node_set
            ]
            declared_gates = [
                value for value in _identifier_list(item.get("gate_ids")) if value in gate_set
            ]
            nodes.append(
                {
                    "node_id": node_ids[index],
                    "kind": _node_kind(item.get("kind") or item.get("class")),
                    "title": _text(item.get("title") or item.get("name"), fallback=f"Legacy node {index + 1}", maximum=200),
                    "objective": _text(item.get("objective") or item.get("goal"), fallback="Preserve the legacy definition intent.", maximum=4_000),
                    "depends_on": dependencies,
                    "gate_ids": declared_gates,
                    "deliverables": _text_list(item.get("deliverables")),
                    "allowed_paths": _repo_paths(item.get("allowed_paths")),
                    "blocked_paths": _repo_paths(item.get("blocked_paths")),
                    "capability_requirements": _text_list(item.get("capability_requirements")),
                    "verification_rule_ids": [rule_id],
                }
            )

        gates: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_gates):
            item = raw if isinstance(raw, Mapping) else {}
            targets = [value for value in _identifier_list(item.get("blocks")) if value in node_set]
            if not targets:
                targets = [node_ids[-1]]
            gates.append(
                {
                    "gate_id": gate_ids[index],
                    "kind": _gate_kind(item.get("kind") or item.get("class")),
                    "title": _text(item.get("title"), fallback=f"Legacy gate {index + 1}", maximum=200),
                    "blocks": targets,
                    "decision_needed": _text(item.get("decision_needed"), fallback="Review the declared gate requirement.", maximum=4_000),
                    "safe_default": _text(item.get("safe_default"), fallback="Do not cross this gate without the declared decision.", maximum=4_000),
                    "approval_scope_schema": {"type": "object", "additionalProperties": False},
                    "required_verification_rule_ids": [rule_id],
                }
            )

        edges = [
            {"from": node["node_id"], "to": dependency, "kind": "depends_on"}
            for node in nodes
            for dependency in node["depends_on"]
        ]
        created_at = _timestamp(payload.get("created_at"), fallback="1970-01-01T00:00:00Z")
        updated_at = _timestamp(payload.get("updated_at"), fallback=created_at)
        roadmap = {
            "roadmap_id": roadmap_id,
            "project_id": project_id,
            "revision": revision,
            "content_hash": "sha256:" + ("0" * 64),
            "revision_state": revision_state,
            "title": _text(payload.get("title"), fallback="Legacy roadmap", maximum=200),
            "objective": _text(payload.get("objective") or payload.get("goal") or payload.get("summary"), fallback="Preserve the legacy roadmap definition.", maximum=4_000),
            "assumptions": _text_list(payload.get("assumptions")),
            "constraints": _text_list(payload.get("constraints")),
            "nodes": nodes,
            "edges": edges,
            "gates": gates,
            "done_contract": {
                "required_node_ids": list(node_ids),
                "required_gate_ids": list(gate_ids),
                "verification_rules": [
                    {
                        "rule_id": rule_id,
                        "kind": "static",
                        "description": "The structurally projected legacy definition passes Definition v2 validation.",
                    }
                ],
                "completion_rule": "all_required_nodes_and_gates",
            },
            "source_refs": [source_ref] if _repo_path(source_ref) else [],
            "created_at": created_at,
            "updated_at": updated_at,
        }
        roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
        latest = (
            {roadmap_id: {"revision": revision, "content_hash": roadmap["content_hash"]}}
            if revision_state == "approved"
            else {}
        )
        return {
            "schema_id": PLANNING_DEFINITION_SCHEMA_ID,
            "project": {
                "project_id": project_id,
                "title": _text(payload.get("project_title") or payload.get("title"), fallback="Legacy project", maximum=200),
                "objective": _text(payload.get("project_objective") or payload.get("goal"), fallback="Preserve legacy Planning definitions.", maximum=4_000),
                "scope": {"in": ["Legacy Planning definition"], "out": ["Agent execution state"]},
                "constraints": ["Legacy runtime fields are omitted from Definition v2."],
                "roadmap_refs": [roadmap_id],
                "latest_approved_revision": latest,
                "draft_refs": [],
            },
            "roadmaps": [roadmap],
        }


def origin_metadata(
    state: str,
    *,
    source: str,
    reason: str,
    as_of: str = "",
) -> dict[str, Any]:
    return {
        "state": _origin_state(state),
        "source": _text(source, fallback="planning_revision_store", maximum=80),
        "reason": _text(reason, fallback="definition_origin_unknown", maximum=160),
        "as_of": _timestamp(as_of, fallback="1970-01-01T00:00:00Z"),
    }


def build_agent_maintenance_handoff(
    *,
    roadmap: Mapping[str, Any],
    run_state: Mapping[str, Any],
    gate_queue: Iterable[Mapping[str, Any]] = (),
    clarifications: Iterable[Mapping[str, Any]] = (),
    receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project one bounded, read-only maintenance resume packet.

    Only identifiers, fixed states, booleans, counts and content-free receipt
    references are emitted. Question text, blocker reasons, raw evidence and
    source paths are deliberately excluded.
    """

    conflicts: set[str] = set()
    roadmap_value = roadmap if type(roadmap) is dict else {}
    state_value = run_state if type(run_state) is dict else {}
    if type(roadmap) is not dict:
        conflicts.add("invalid_roadmap_authority")
    if type(run_state) is not dict:
        conflicts.add("invalid_run_state_authority")

    route = state_value.get("route")
    route_value = route if type(route) is dict else {}
    if route is not None and type(route) is not dict:
        conflicts.add("invalid_route_authority")
    route_slice = _maintenance_id(
        route_value.get("slice_id"),
        fallback="unknown_slice",
    )
    if route_slice == "unknown_slice":
        conflicts.add("missing_route_slice")
    claims = _maintenance_records(
        state_value.get("active_claims"),
        conflicts=conflicts,
    )
    projected_claims = [
        {
            "claim_id": _maintenance_id(
                _maintenance_first_string(
                    claim.get("claim_id"),
                    claim.get("slice_id"),
                ),
                fallback=f"claim_{index + 1}",
            ),
            "slice_id": _maintenance_id(
                claim.get("slice_id"),
                fallback="unknown_slice",
            ),
            "owner_role": _maintenance_id(
                claim.get("owner"),
                fallback="unknown_owner",
            ),
            "state": _maintenance_id(
                _maintenance_first_string(claim.get("state"), "active"),
                fallback="active",
            ),
        }
        for index, claim in enumerate(claims)
    ]
    projected_claims.sort(key=lambda item: (item["slice_id"], item["claim_id"]))
    if len(projected_claims) > 1:
        conflicts.add("multiple_active_claims")
    if projected_claims and route_slice != projected_claims[0]["slice_id"]:
        conflicts.add("route_claim_mismatch")

    gate_records = _maintenance_records(gate_queue, conflicts=conflicts)
    blockers = _maintenance_blockers(
        (
            (
                "run_state",
                _maintenance_records(
                    state_value.get("known_blockers"),
                    conflicts=conflicts,
                ),
            ),
            ("gate_queue", gate_records),
        ),
        conflicts=conflicts,
    )
    owner_questions = _maintenance_owner_questions(
        [
            *_maintenance_records(clarifications, conflicts=conflicts),
            *_maintenance_gate_questions(gate_records),
        ],
        conflicts=conflicts,
    )

    state_status = _maintenance_id(
        _maintenance_first_string(state_value.get("state"), "unknown"),
        fallback="unknown",
    )
    if state_status == "unknown":
        conflicts.add("unknown_run_state")
    if state_status == "stale" or state_value.get("stale") is True:
        conflicts.add("stale_authority")

    roadmap_id = _maintenance_id(
        roadmap_value.get("roadmap_id"),
        fallback="unknown_roadmap",
    )
    if roadmap_id == "unknown_roadmap":
        conflicts.add("missing_roadmap_id")

    receipt_reference, receipt_limits, receipt_conflict = _maintenance_receipt(
        receipt,
        expected_revision=state_value.get("revision_ref"),
    )
    if receipt_conflict:
        conflicts.add(receipt_conflict)

    if conflicts:
        status = "blocked_conflict"
        next_action = "reconcile_authority"
    elif owner_questions:
        status = "waiting_on_user"
        next_action = "waiting_on_user"
    elif blockers:
        status = "blocked"
        next_action = "resolve_blocker"
    elif projected_claims:
        status = "active"
        next_action = "continue_claim"
    else:
        status = "ready"
        candidates = _maintenance_id_list(state_value.get("next_runnable_slices"))
        next_action = candidates[0] if candidates else "no_safe_frontier"

    not_verified = set(receipt_limits)
    if receipt_reference["status"] == "missing":
        not_verified.add("machine_receipt")
    not_verified_values = sorted(not_verified)
    if len(not_verified_values) > MAINTENANCE_HANDOFF_MAX_RECORDS:
        not_verified_values = [
            "verification_limit_exceeded",
            *not_verified_values[: MAINTENANCE_HANDOFF_MAX_RECORDS - 1],
        ]

    return {
        "schema": MAINTENANCE_HANDOFF_SCHEMA_ID,
        "status": status,
        "goal": {
            "roadmap_id": roadmap_id,
            "goal_id": _maintenance_id(
                _maintenance_first_string(
                    roadmap_value.get("goal_id"),
                    roadmap_value.get("roadmap_id"),
                ),
                fallback="unknown_goal",
            ),
            "state": _maintenance_id(
                roadmap_value.get("status"),
                fallback="unknown",
            ),
        },
        "slice": {
            "slice_id": route_slice,
            "state": _maintenance_id(
                _maintenance_first_string(route_value.get("state"), state_status),
                fallback="unknown",
            ),
        },
        "claim": (
            projected_claims[0]
            if projected_claims
            else {
                "claim_id": "none",
                "slice_id": route_slice,
                "owner_role": "none",
                "state": "unclaimed",
            }
        ),
        "active_claim_count": len(projected_claims),
        "next_action": next_action,
        "blockers": blockers,
        "owner_questions": owner_questions,
        "receipt_reference": receipt_reference,
        "not_verified": not_verified_values,
        "conflicts": sorted(conflicts),
        "read_only": True,
        "write_action_enabled": False,
        "raw_evidence_visible": False,
        "private_content_visible": False,
    }


def _maintenance_blockers(
    sources: Iterable[tuple[str, list[Mapping[str, Any]]]],
    *,
    conflicts: set[str],
) -> list[dict[str, str]]:
    grouped: dict[str, dict[str, set[str]]] = {}
    for source, records in sources:
        for index, record in enumerate(records):
            state = _maintenance_id(
                _maintenance_first_string(
                    record.get("state"),
                    record.get("status"),
                    "pending",
                ),
                fallback="pending",
            )
            if _maintenance_state_is_resolved(state):
                continue
            blocker_id = _maintenance_id(
                _maintenance_first_string(record.get("id"), record.get("gate_id")),
                fallback=f"{source}_blocker_{index + 1}",
            )
            item = grouped.setdefault(
                blocker_id,
                {"states": set(), "sources": set()},
            )
            item["states"].add(state)
            item["sources"].add(source)

    result: list[dict[str, str]] = []
    for blocker_id, values in grouped.items():
        states = values["states"]
        if len(states) > 1:
            conflicts.add("conflicting_blocker_state")
            state = "conflict"
        else:
            state = next(iter(states))
        result.append(
            {
                "blocker_id": blocker_id,
                "state": state,
                "source": "+".join(sorted(values["sources"])),
            }
        )
    result.sort(key=lambda item: item["blocker_id"])
    if len(result) > MAINTENANCE_HANDOFF_MAX_RECORDS:
        conflicts.add("projected_blocker_limit_exceeded")
    return result[:MAINTENANCE_HANDOFF_MAX_RECORDS]


def _maintenance_owner_questions(
    records: list[Mapping[str, Any]],
    *,
    conflicts: set[str],
) -> list[dict[str, str]]:
    grouped: dict[str, dict[str, set[str]]] = {}
    for index, record in enumerate(records):
        state = _maintenance_id(
            _maintenance_first_string(
                record.get("state"),
                record.get("status"),
                "waiting_on_user",
            ),
            fallback="waiting_on_user",
        )
        if _maintenance_state_is_resolved(state):
            continue
        question_id = _maintenance_id(
            _maintenance_first_string(
                record.get("question_id"),
                record.get("id"),
            ),
            fallback=f"owner_question_{index + 1}",
        )
        item = grouped.setdefault(
            question_id,
            {"states": set(), "types": set()},
        )
        item["states"].add(state)
        item["types"].add(
            _maintenance_id(
                _maintenance_first_string(
                    record.get("question_type"),
                    record.get("type"),
                    "owner_decision",
                ),
                fallback="owner_decision",
            )
        )

    result: list[dict[str, str]] = []
    for question_id, values in grouped.items():
        if len(values["states"]) > 1 or len(values["types"]) > 1:
            conflicts.add("conflicting_owner_question")
            state = "conflict"
            question_type = "owner_decision"
        else:
            state = next(iter(values["states"]))
            question_type = next(iter(values["types"]))
        result.append(
            {
                "question_id": question_id,
                "question_type": question_type,
                "state": state,
            }
        )
    result.sort(key=lambda item: item["question_id"])
    if len(result) > MAINTENANCE_HANDOFF_MAX_RECORDS:
        conflicts.add("projected_owner_question_limit_exceeded")
    return result[:MAINTENANCE_HANDOFF_MAX_RECORDS]


def _maintenance_gate_questions(
    records: list[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    questions: list[Mapping[str, Any]] = []
    for index, record in enumerate(records):
        if not any(
            record.get(key) is True
            for key in ("decision_needed", "decision_required", "waiting_on_user")
        ):
            continue
        state = _maintenance_id(
            _maintenance_first_string(
                record.get("state"),
                record.get("status"),
                "waiting_on_user",
            ),
            fallback="waiting_on_user",
        )
        if _maintenance_state_is_resolved(state):
            continue
        questions.append(
            {
                "question_id": _maintenance_first_string(
                    record.get("question_id"),
                    record.get("gate_id"),
                    record.get("id"),
                    f"gate_question_{index + 1}",
                ),
                "question_type": _maintenance_first_string(
                    record.get("question_type"),
                    "owner_decision",
                ),
                "state": state,
            }
        )
    return questions


def _maintenance_receipt(
    receipt: Mapping[str, Any] | None,
    *,
    expected_revision: Any,
) -> tuple[dict[str, str], tuple[str, ...], str]:
    if type(receipt) is not dict:
        return (
            {
                "receipt_id": "none",
                "revision": "none",
                "diff_digest": "none",
                "status": "missing",
            },
            ("machine_receipt",),
            "",
        )

    revision = _maintenance_revision(receipt.get("revision"))
    expected = _maintenance_revision(expected_revision)
    conflict = (
        "receipt_revision_mismatch"
        if expected != "none" and revision != expected
        else ""
    )
    receipt_id = _maintenance_id(
        receipt.get("receipt_id"),
        fallback="unknown_receipt",
    )
    diff_digest = _maintenance_digest(receipt.get("diff_digest"))
    status = _maintenance_id(receipt.get("status"), fallback="unknown")
    limits = tuple(_maintenance_id_list(receipt.get("not_verified")))
    if type(receipt.get("not_verified")) not in {list, type(None)}:
        limits = tuple(sorted(set(limits) | {"receipt_not_verified_shape"}))
    elif (
        type(receipt.get("not_verified")) is list
        and len(receipt["not_verified"]) > MAINTENANCE_HANDOFF_MAX_RECORDS
    ):
        limits = tuple(sorted(set(limits) | {"receipt_not_verified_truncated"}))
    if expected == "none":
        limits = tuple(sorted(set(limits) | {"expected_revision"}))
    if revision == "none":
        limits = tuple(sorted(set(limits) | {"receipt_revision"}))
    if diff_digest == "none":
        limits = tuple(sorted(set(limits) | {"receipt_diff_digest"}))
    if receipt_id == "unknown_receipt":
        limits = tuple(sorted(set(limits) | {"receipt_identity"}))
    if not (
        status in {"accepted", "passed"}
        or status.startswith("accepted_")
        or status.startswith("passed_")
    ):
        limits = tuple(sorted(set(limits) | {"required_verification"}))
    return (
        {
            "receipt_id": receipt_id,
            "revision": revision,
            "diff_digest": diff_digest,
            "status": status,
        },
        limits,
        conflict,
    )


def _maintenance_records(
    value: Any,
    *,
    conflicts: set[str] | None = None,
) -> list[Mapping[str, Any]]:
    if value is None:
        return []
    if type(value) not in {list, tuple}:
        if conflicts is not None:
            conflicts.add("invalid_authority_record_shape")
        return []
    if len(value) > MAINTENANCE_HANDOFF_MAX_RECORDS and conflicts is not None:
        conflicts.add("authority_record_limit_exceeded")
    records: list[Mapping[str, Any]] = []
    for item in value[:MAINTENANCE_HANDOFF_MAX_RECORDS]:
        if type(item) is dict:
            records.append(item)
        elif conflicts is not None:
            conflicts.add("invalid_authority_record_shape")
    return records


def _maintenance_state_is_resolved(state: str) -> bool:
    return (
        state in _MAINTENANCE_RESOLVED_STATES
        or any(
            state.startswith(prefix)
            for prefix in ("accepted_", "closed_", "completed_", "resolved_", "satisfied_")
        )
    )


def _maintenance_id_list(value: Any) -> list[str]:
    if type(value) is not list:
        return []
    result: list[str] = []
    for index, item in enumerate(value[:MAINTENANCE_HANDOFF_MAX_RECORDS]):
        token = _maintenance_id(item, fallback=f"item_{index + 1}")
        if token not in result:
            result.append(token)
    return result


def _maintenance_id(value: Any, *, fallback: str) -> str:
    if type(value) is not str:
        return fallback
    token = re.sub(r"[^a-z0-9._:-]+", "_", value.strip().lower())
    token = token.strip("_.:-")[:96]
    parts = frozenset(re.split(r"[._:-]+", token))
    if not token or not token[0].isalnum() or parts & _MAINTENANCE_SENSITIVE_PARTS:
        return fallback
    return token


def _maintenance_first_string(*values: Any) -> str | None:
    for value in values:
        if type(value) is str and value.strip():
            return value
    return None


def _maintenance_revision(value: Any) -> str:
    if type(value) is not str:
        return "none"
    token = value.strip().lower()
    if re.fullmatch(r"[0-9a-f]{7,64}", token):
        return token
    return "none"


def _maintenance_digest(value: Any) -> str:
    if type(value) is not str:
        return "none"
    token = value.strip().lower()
    if re.fullmatch(r"sha256:[0-9a-f]{64}", token):
        return token
    return "none"


def _origin_state(value: Any) -> str:
    state = str(value or "").strip().lower()
    if state not in ORIGIN_STATES:
        raise PlanningDefinitionProjectionError("invalid_origin_state", "origin state is not supported")
    return state


def _legacy_list(payload: Mapping[str, Any], *keys: str) -> list[Any]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value[:1_000]
    return []


def _deduplicated_ids(
    records: list[Any],
    prefix: str,
    keys: tuple[str, ...],
) -> list[str]:
    result: list[str] = []
    used: set[str] = set()
    for index, raw in enumerate(records):
        record = raw if isinstance(raw, Mapping) else {}
        candidate: Any = None
        for key in keys:
            if record.get(key):
                candidate = record[key]
                break
        base = _identifier(candidate, fallback=f"{prefix}-{index + 1}")
        value = base
        suffix = 2
        while value in used:
            value = f"{base}-{suffix}"
            suffix += 1
        used.add(value)
        result.append(value)
    return result


def _identifier(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip()
    normalized = re.sub(r"[^A-Za-z0-9._:-]+", "-", text).strip("-._:")[:128]
    if not normalized or not normalized[0].isalnum():
        normalized = fallback
    return normalized[:128]


def _identifier_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for index, item in enumerate(value[:1_000]):
        identifier = _identifier(item, fallback=f"ref-{index + 1}")
        if identifier not in result:
            result.append(identifier)
    return result


def _source_identifier(value: str) -> str:
    leaf = str(value or "").replace("\\", "/").rsplit("/", 1)[-1]
    if leaf.lower().endswith(".json"):
        leaf = leaf[:-5]
    return _identifier(leaf, fallback="") if leaf else ""


def _text(value: Any, *, fallback: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text:
        text = fallback
    text = "".join(character for character in text if ord(character) >= 32 or character in "\n\t")
    return text[:maximum]


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:1_000]:
        text = _text(item, fallback="", maximum=4_000)
        if text and text not in result:
            result.append(text)
    return result


def _repo_paths(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:1_000]:
        text = str(item or "").strip()
        if _repo_path(text) and text not in result:
            result.append(text)
    return result


def _repo_path(value: Any) -> bool:
    text = str(value or "").strip()
    if not text or "\\" in text or text.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", text):
        return False
    parts = text.split("/")
    return not any(part in {"", ".", "..", ".git", ".env", ".ssh"} for part in parts)


def _node_kind(value: Any) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in {"work", "gate", "milestone", "group"} else "work"


def _gate_kind(value: Any) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in {"design", "operator", "repo", "live", "security", "dependency"} else "operator"


def _timestamp(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        text += "T00:00:00Z"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return fallback
    return text if parsed.tzinfo is not None else fallback


__all__ = [
    "LEGACY_KINDS",
    "MAINTENANCE_HANDOFF_SCHEMA_ID",
    "MAINTENANCE_HANDOFF_MAX_RECORDS",
    "ORIGIN_STATES",
    "READ_MODEL_SCHEMA_ID",
    "PlanningDefinitionProjectionError",
    "PlanningDefinitionProjector",
    "build_agent_maintenance_handoff",
    "origin_metadata",
]
