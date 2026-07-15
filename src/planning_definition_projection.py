"""Pure projections for immutable Planning Definition v2 documents."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import re
from typing import Any, Mapping

from src.planning_definition_contract import (
    FORBIDDEN_EXECUTION_STATES,
    PLANNING_DEFINITION_SCHEMA_ID,
    REVISION_STATES,
    PlanningDefinitionContractError,
    compute_roadmap_content_hash,
    validate_planning_definition,
)


READ_MODEL_SCHEMA_ID = "odysseus.planning.definition_read_model.v2"
LEGACY_KINDS = frozenset({"odysseus.planning.roadmap", "harbor.planning.roadmap"})
ORIGIN_STATES = frozenset({"loading", "live", "stale", "unavailable", "error"})


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
    "ORIGIN_STATES",
    "READ_MODEL_SCHEMA_ID",
    "PlanningDefinitionProjectionError",
    "PlanningDefinitionProjector",
    "origin_metadata",
]
