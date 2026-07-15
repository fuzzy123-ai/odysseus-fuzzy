"""Hash-pinned, non-launching Planning-to-Agent handoff envelope."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping

from src.planning_definition_contract import (
    PLANNING_DEFINITION_SCHEMA_ID,
    PlanningDefinitionContractError,
    compute_roadmap_content_hash,
    validate_planning_definition,
)


AGENT_PLAN_HANDOFF_SCHEMA_ID = "odysseus.agent.plan_handoff.v1"
REQUESTED_ENTRYPOINT = "/abc"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ENVELOPE_FIELDS = frozenset(
    {
        "schema_id",
        "project_id",
        "roadmap_id",
        "revision",
        "content_hash",
        "title",
        "requested_entrypoint",
        "composer_text",
        "launch_authorized",
        "read_only",
    }
)
FORBIDDEN_HANDOFF_FIELDS = frozenset(
    {
        "skill",
        "skills",
        "model",
        "models",
        "run",
        "run_id",
        "workflow_id",
        "workflow_run_id",
        "command",
        "commands",
        "allowed_commands",
        "auto_submit",
        "submitted",
    }
)


class PlanningAgentHandoffError(ValueError):
    def __init__(self, code: str, path: str, detail: str) -> None:
        self.code = code
        self.path = path
        self.detail = detail
        super().__init__(f"{code} at {path}: {detail}")


def build_agent_plan_handoff(
    read_model: Mapping[str, Any],
    *,
    expected_revision: int,
    expected_hash: str,
) -> dict[str, Any]:
    """Build one envelope only when the requested approved head still matches."""

    if not isinstance(read_model, Mapping):
        _fail("invalid_read_model", "$", "Planning read model must be an object")
    revision = _revision(expected_revision, "$.expected_revision")
    content_hash = _content_hash(expected_hash, "$.expected_hash")
    project = _mapping(read_model.get("project"), "$.project")
    roadmap = _mapping(read_model.get("roadmap"), "$.roadmap")
    project_id = _identifier(project.get("project_id"), "$.project.project_id")
    roadmap_id = _identifier(roadmap.get("roadmap_id"), "$.roadmap.roadmap_id")
    if roadmap.get("project_id") != project_id:
        _fail("handoff_reference_mismatch", "$.roadmap.project_id", "roadmap project does not match")
    if roadmap.get("revision") != revision:
        _fail("handoff_revision_mismatch", "$.roadmap.revision", "resolved revision differs from request")
    if roadmap.get("content_hash") != content_hash:
        _fail("handoff_hash_mismatch", "$.roadmap.content_hash", "resolved hash differs from request")
    if roadmap.get("revision_state") != "approved":
        _fail("handoff_revision_not_approved", "$.roadmap.revision_state", "revision is not approved")
    latest = project.get("latest_approved_revision")
    if not isinstance(latest, Mapping):
        _fail("invalid_read_model", "$.project.latest_approved_revision", "approved references are missing")
    current = latest.get(roadmap_id)
    if not isinstance(current, Mapping):
        _fail("handoff_revision_superseded", "$.project.latest_approved_revision", "requested revision is not current")
    if current.get("revision") != revision or current.get("content_hash") != content_hash:
        _fail("handoff_revision_superseded", f"$.project.latest_approved_revision.{roadmap_id}", "requested revision is not the approved head")
    if compute_roadmap_content_hash(roadmap) != content_hash:
        _fail("handoff_content_integrity", "$.roadmap.content_hash", "roadmap content no longer matches its hash")
    try:
        validate_planning_definition(
            {
                "schema_id": PLANNING_DEFINITION_SCHEMA_ID,
                "project": deepcopy(dict(project)),
                "roadmaps": [deepcopy(dict(roadmap))],
            }
        )
    except PlanningDefinitionContractError as exc:
        _fail("invalid_read_model", exc.path, exc.detail)
    title = _title(roadmap.get("title"), "$.roadmap.title")
    envelope = {
        "schema_id": AGENT_PLAN_HANDOFF_SCHEMA_ID,
        "project_id": project_id,
        "roadmap_id": roadmap_id,
        "revision": revision,
        "content_hash": content_hash,
        "title": title,
        "requested_entrypoint": REQUESTED_ENTRYPOINT,
        "composer_text": _composer_text(roadmap_id, revision, content_hash),
        "launch_authorized": False,
        "read_only": True,
    }
    validate_agent_plan_handoff(envelope)
    return envelope


def validate_agent_plan_handoff(value: Mapping[str, Any]) -> dict[str, Any]:
    envelope = _mapping(value, "$")
    keys = set(envelope)
    forbidden = sorted(keys & FORBIDDEN_HANDOFF_FIELDS)
    if forbidden:
        _fail("handoff_field_forbidden", f"$.{forbidden[0]}", "field can influence Agent execution")
    missing = sorted(_ENVELOPE_FIELDS - keys)
    if missing:
        _fail("missing_field", "$", f"missing required field {missing[0]}")
    unknown = sorted(keys - _ENVELOPE_FIELDS)
    if unknown:
        _fail("unknown_field", f"$.{unknown[0]}", "field is not part of the handoff envelope")
    if envelope["schema_id"] != AGENT_PLAN_HANDOFF_SCHEMA_ID:
        _fail("invalid_literal", "$.schema_id", "handoff schema is invalid")
    project_id = _identifier(envelope["project_id"], "$.project_id")
    roadmap_id = _identifier(envelope["roadmap_id"], "$.roadmap_id")
    revision = _revision(envelope["revision"], "$.revision")
    content_hash = _content_hash(envelope["content_hash"], "$.content_hash")
    _title(envelope["title"], "$.title")
    if envelope["requested_entrypoint"] != REQUESTED_ENTRYPOINT:
        _fail("invalid_literal", "$.requested_entrypoint", "entrypoint must be /abc")
    expected_text = _composer_text(roadmap_id, revision, content_hash)
    if envelope["composer_text"] != expected_text:
        _fail("composer_text_mismatch", "$.composer_text", "composer text is not hash-pinned")
    if envelope["launch_authorized"] is not False:
        _fail("launch_forbidden", "$.launch_authorized", "Planning cannot authorize launch")
    if envelope["read_only"] is not True:
        _fail("read_only_required", "$.read_only", "handoff must be read-only")
    return deepcopy(dict(envelope))


def _composer_text(roadmap_id: str, revision: int, content_hash: str) -> str:
    return f"/abc run roadmap:{roadmap_id}@{revision} hash:{content_hash}"


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail("invalid_type", path, "expected object")
    return value


def _identifier(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        _fail("invalid_identifier", path, "expected stable identifier")
    return value


def _revision(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _fail("invalid_revision", path, "expected positive integer revision")
    return value


def _content_hash(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        _fail("invalid_content_hash", path, "expected sha256 lowercase hexadecimal")
    return value


def _title(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        _fail("invalid_title", path, "title must be a bounded non-empty string")
    if any(ord(character) < 32 for character in value):
        _fail("invalid_title", path, "title contains control characters")
    return value


def _fail(code: str, path: str, detail: str) -> None:
    raise PlanningAgentHandoffError(code, path, detail)


__all__ = [
    "AGENT_PLAN_HANDOFF_SCHEMA_ID",
    "FORBIDDEN_HANDOFF_FIELDS",
    "REQUESTED_ENTRYPOINT",
    "PlanningAgentHandoffError",
    "build_agent_plan_handoff",
    "validate_agent_plan_handoff",
]
