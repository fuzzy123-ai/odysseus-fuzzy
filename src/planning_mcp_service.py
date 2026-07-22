"""Pure, read-only service contract for repository Planning sources.

The service deliberately owns no HTTP or MCP transport and performs no writes.
It projects allowlisted JSON roadmaps into bounded public payloads that can be
reused by routes and MCP servers in later slices.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import unicodedata
from typing import Any, Iterable, Mapping
from urllib.parse import unquote

from src.memory_lifecycle_adapters import plan_planning_memory_lifecycle
from src.planning_agent_handoff import (
    PlanningAgentHandoffError,
    build_agent_plan_handoff,
)
from src.planning_definition_contract import (
    GATE_RUNTIME_FIELD_DENYLIST,
    PLANNING_DEFINITION_SCHEMA_ID,
    RUNTIME_FIELD_DENYLIST,
    PlanningDefinitionContractError,
    validate_planning_definition,
)
from src.planning_revision_store import (
    PlanningRevisionStore,
    PlanningRevisionStoreError,
)
from src.planning_source_inventory import build_planning_source_inventory
from src.planning_source_memory import (
    build_derived_planning_memory_records,
    build_planning_memory_capsules,
    project_accepted_planning_memory,
)
PLANNING_ROOTS = ("docs/plans", "specs/roadmaps")
MAX_SOURCE_BYTES = 2_000_000
MAX_LIST_LIMIT = 100
MAX_CONTEXT_ITEMS = 24
MAX_CONTEXT_BYTES = 65_536
MAX_SECTION_CONTEXT_BYTES = 32_768
MAX_RAW_PREVIEW_CHARS = 16_384
MAX_DRAFT_SLICES = 100
MAX_DRAFT_GATES = 50
CANONICAL_ROADMAP_KIND = "odysseus.planning.roadmap"
LEGACY_HARBOR_ROADMAP_KIND = "harbor.planning.roadmap"
ROADMAP_KIND_ALIASES = frozenset({CANONICAL_ROADMAP_KIND, LEGACY_HARBOR_ROADMAP_KIND})

_DRAFT_MODES = frozenset({"Standard ABC", "Overnight Backend Mode"})
_PATCHABLE_FIELDS = frozenset({
    "title",
    "goal",
    "summary",
    "status",
    "source_refs",
    "slices",
    "slice_queue",
    "gates",
    "stop_rules",
    "verification",
})

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_URI_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{12,}", re.IGNORECASE),
    re.compile(
        r"(?i)(api[_-]?key|authorization|cookie|token|secret|password|chat[_-]?id)"
        r"\s*[:=]\s*['\"]?[^'\"\s,}]+"
    ),
)
_PRIVATE_PATH_PATTERNS = (
    re.compile(r"(?i)\b[A-Z]:[\\/][^\s\"'<>]+"),
    re.compile(r"\\\\[^\s\"'<>]+"),
    re.compile(r"/(?:home|Users|private|var|tmp)/[^\s\"'<>]+"),
)


class PlanningServiceError(ValueError):
    """Fail-closed error with a bounded public representation."""

    def __init__(self, code: str, message: str) -> None:
        self.code = _safe_token(code, fallback="planning_service_error", max_chars=80)
        self.public_message = _safe_text(message, max_chars=180)
        super().__init__(self.public_message)

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.public_message}


_DEFINITION_SILENT_EVENTS = frozenset(
    {
        "planning_context_pack_read",
        "planning_summary_refreshed",
        "planning_raptor_memory_processed",
        "planning_definition_validation_succeeded",
    }
)
_DEFINITION_NOTIFICATION_EVENTS = frozenset(
    {
        "project_created",
        "project_deleted",
        "roadmap_created",
        "roadmap_deleted",
        "roadmap_revision_approved",
        "roadmap_revision_conflict",
        "undo_available_after_structural_delete",
    }
)
_REVISION_NOTIFICATION_EVENTS = frozenset(
    {"roadmap_revision_approved", "roadmap_revision_conflict"}
)


class PlanningMcpService:
    """Read-only Planning operations rooted at one injected repository."""

    def __init__(
        self,
        repo_root: str | os.PathLike[str],
        *,
        preview_chars: int = 240,
        context_budget_bytes: int = MAX_CONTEXT_BYTES,
        definition_store: PlanningRevisionStore | None = None,
        definition_owner: str = "",
    ) -> None:
        self.repo_root = Path(repo_root).resolve(strict=True)
        if not self.repo_root.is_dir():
            raise PlanningServiceError("invalid_repo_root", "Planning repository root is not a directory")
        self.preview_chars = _clamp_int(preview_chars, minimum=0, maximum=2_000, default=240)
        self.context_budget_bytes = _clamp_int(
            context_budget_bytes,
            minimum=4_096,
            maximum=MAX_CONTEXT_BYTES,
            default=MAX_CONTEXT_BYTES,
        )
        self._allowed_roots = self._resolve_allowed_roots()
        self._definition_store = definition_store
        self._definition_owner = str(definition_owner or "").strip()

    def list_roadmaps(
        self,
        *,
        kind: str | None = None,
        status: str | None = None,
        query: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        """Return bounded metadata for validated or recognizable JSON roadmaps."""

        records = self._roadmap_records()
        filtered = self._filter_records(records, kind=kind, status=status, query=query)
        bounded_limit = _clamp_int(limit, minimum=1, maximum=MAX_LIST_LIMIT, default=50)
        items = [self._record_summary(record) for record in filtered[:bounded_limit]]
        return {
            "schema": "odysseus.planning.roadmap_list.v1",
            "read_only": True,
            "writes_supported": False,
            "query": _safe_text(query, max_chars=200),
            "filters": {
                "kind": _safe_token(kind, fallback="", max_chars=100) if kind else "",
                "status": _safe_token(status, fallback="", max_chars=80) if status else "",
            },
            "summary": {
                "total_roadmaps": len(records),
                "total_matches": len(filtered),
                "returned": len(items),
                "clipped": len(filtered) > len(items),
            },
            "roadmaps": items,
        }

    def read_roadmap(
        self,
        source_id_or_path: str,
        *,
        include_nodes: bool = True,
        include_raw_preview_chars: int = 0,
    ) -> dict[str, Any]:
        """Read one roadmap by stable source id or exact allowlisted path."""

        record, payload, raw = self._resolve_roadmap(source_id_or_path)
        validation = planning_validate_roadmap(payload, source_ref=record["path"])
        project_id, roadmap_id, ids_derived = _logical_ids(record, payload)
        raw_limit = _clamp_int(
            include_raw_preview_chars,
            minimum=0,
            maximum=MAX_RAW_PREVIEW_CHARS,
            default=0,
        )
        nodes = _extract_slices(payload, limit=MAX_CONTEXT_ITEMS) if include_nodes else []
        gates = _extract_gates(payload, limit=MAX_CONTEXT_ITEMS) if include_nodes else []
        source_refs = _extract_source_refs(payload, record, limit=24)
        dependencies = _extract_dependencies(payload, record, limit=24)
        result = {
            "schema": "odysseus.planning.roadmap_read.v1",
            "read_only": True,
            "writes_supported": False,
            "source": self._record_summary(record),
            "roadmap": _roadmap_projection(payload, project_id=project_id, roadmap_id=roadmap_id),
            "logical_ids": {
                "project_id": project_id,
                "roadmap_id": roadmap_id,
                "derived": ids_derived,
            },
            "source_refs": source_refs,
            "dependency_hints": dependencies,
            "slices": nodes,
            "gates": gates,
            "validation": validation,
            "raw_json_preview": _safe_text(raw, max_chars=raw_limit, preserve_whitespace=True) if raw_limit else "",
            "raw_json_truncated": bool(raw_limit and len(raw) > raw_limit),
            "absolute_paths_visible": False,
        }
        return result

    def read_document(
        self,
        project_id: str,
        roadmap_id: str,
        *,
        max_items: int = MAX_CONTEXT_ITEMS,
        canonical_json_chars: int = 8_192,
        include_memory: bool = False,
    ) -> dict[str, Any]:
        """Return one stable-ID roadmap document projection for Harbor readers."""

        safe_project_id = _strict_document_id(project_id, field="project_id")
        safe_roadmap_id = _strict_document_id(roadmap_id, field="roadmap_id")
        item_limit = _strict_budget(max_items, field="max_items", minimum=1, maximum=MAX_CONTEXT_ITEMS)
        json_limit = _strict_budget(
            canonical_json_chars,
            field="canonical_json_chars",
            minimum=0,
            maximum=MAX_RAW_PREVIEW_CHARS,
        )
        if not isinstance(include_memory, bool):
            raise PlanningServiceError("invalid_memory_option", "include_memory must be boolean")

        matches = [
            record
            for record in self._roadmap_records()
            if record.get("project_id") == safe_project_id and record.get("roadmap_id") == safe_roadmap_id
        ]
        if not matches:
            raise PlanningServiceError("roadmap_document_not_found", "Roadmap document was not found")
        if len(matches) != 1:
            raise PlanningServiceError("roadmap_document_ambiguous", "Roadmap document identity resolves to multiple sources")

        record = matches[0]
        payload = record.get("_payload") if isinstance(record.get("_payload"), Mapping) else {}
        context = self.get_context_pack(
            str(record["source_id"]),
            max_items=item_limit,
            include_memory=include_memory,
        )
        tasks = [dict(item) for item in context.get("slices") or []]
        gates = [dict(item) for item in context.get("gates") or []]
        source_refs = list(context.get("source_refs") or [])
        summary = _safe_text(payload.get("summary") or payload.get("goal") or "", max_chars=1_000)
        canonical_projection = {
            "schema_version": payload.get("schema_version", 1),
            "kind": _safe_token(payload.get("kind"), fallback="planning_roadmap", max_chars=120),
            "project_id": safe_project_id,
            "roadmap_id": safe_roadmap_id,
            "title": _safe_text(payload.get("title") or "Untitled roadmap", max_chars=200),
            "goal": _safe_text(payload.get("goal") or payload.get("summary") or "", max_chars=1_000),
            "summary": summary,
            "status": _safe_token(payload.get("status"), fallback="unknown", max_chars=80),
            "revision": payload.get("revision") if isinstance(payload.get("revision"), int) else None,
            "created_at": _safe_text(payload.get("created_at") or "", max_chars=40),
            "updated_at": _safe_text(payload.get("updated_at") or "", max_chars=40),
            "source_refs": source_refs,
            "slices": tasks,
            "gates": gates,
            "gate_refs": _safe_scalar_list(payload.get("gate_refs"), limit=item_limit),
            "dependency_refs": _safe_scalar_list(payload.get("dependency_refs"), limit=item_limit),
            "verification": _safe_scalar_list(payload.get("verification"), limit=item_limit),
            "stop_rules": _safe_scalar_list(payload.get("stop_rules"), limit=item_limit),
        }
        canonical_json = json.dumps(canonical_projection, ensure_ascii=False, sort_keys=True, indent=2)
        canonical_preview = _safe_text(canonical_json, max_chars=json_limit, preserve_whitespace=True) if json_limit else ""
        _slice_key, raw_slices = _slice_collection(payload)
        raw_slice_count = len(raw_slices) if isinstance(raw_slices, list) else 0
        raw_gate_count = len(_extract_gates(payload, limit=200))
        lens = context.get("roadmap_lens") if isinstance(context.get("roadmap_lens"), Mapping) else {}
        memory_summary = dict(context.get("memory_summary") or {})
        truncated = bool(
            raw_slice_count > len(tasks)
            or raw_gate_count > len(gates)
            or len(canonical_json) > json_limit
            or context.get("clipped")
            or memory_summary.get("truncated")
            or lens.get("clipped")
        )
        incomplete = bool(
            truncated
            or not context.get("validation", {}).get("valid", False)
            or memory_summary.get("incomplete")
            or lens.get("incomplete")
        )
        document = {
            "schema": "odysseus.planning.roadmap_document.v1",
            "read_only": True,
            "writes_supported": False,
            "project_id": safe_project_id,
            "roadmap_id": safe_roadmap_id,
            "source_id": record["source_id"],
            "source_ref": record["path"],
            "source_hash": record["source_hash"],
            "title": canonical_projection["title"],
            "goal": canonical_projection["goal"],
            "summary": summary,
            "status": canonical_projection["status"],
            "revision": canonical_projection["revision"],
            "tasks": tasks,
            "slices": deepcopy(tasks),
            "gates": gates,
            "source_refs": source_refs,
            "readable_sections": _document_sections(summary, tasks, gates, source_refs),
            "canonical": {
                "projection": canonical_projection,
                "projection_hash": _payload_hash(canonical_projection),
                "source_hash": record["source_hash"],
                "revision": canonical_projection["revision"],
                "json_preview": canonical_preview,
                "json_preview_chars": len(canonical_preview),
                "json_preview_budget_chars": json_limit,
                "truncated": len(canonical_json) > len(canonical_preview),
                "raw_source_included": False,
            },
            "lens_summary": {
                "schema": lens.get("schema") or "odysseus.planning.roadmap_lens_summary.v1",
                "available": bool(lens.get("available")),
                "projection": lens.get("projection") or "structured_read_evidence",
                "evidence_ref": lens.get("evidence_ref") or record["path"],
                "evidence_hash": lens.get("evidence_hash") or record["source_hash"],
                "status": lens.get("status") or "incomplete",
                "active_node_id": lens.get("active_node_id") or "",
                "node_count": len(lens.get("nodes") or []),
                "edge_count": len(lens.get("edges") or []),
                "clipped": bool(lens.get("clipped")),
                "incomplete": bool(lens.get("incomplete")),
            },
            "memory_summary": memory_summary,
            "memory_refs": [
                _safe_token(item.get("memory_ref"), fallback="planning-memory", max_chars=180)
                for item in context.get("memory") or []
                if isinstance(item, Mapping)
            ][:item_limit],
            "validation": context.get("validation") or {},
            "budget": {
                "max_items": item_limit,
                "canonical_json_chars": json_limit,
                "payload_budget_bytes": MAX_CONTEXT_BYTES,
            },
            "truncated": truncated,
            "incomplete": incomplete,
            "raw_content_included": False,
            "absolute_paths_visible": False,
        }
        return _fit_document_budget(document, MAX_CONTEXT_BYTES)

    def propose_document_edit(
        self,
        project_id: str,
        roadmap_id: str,
        edit_request: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Build a Summary/Task/Data document edit proposal without writing."""

        if not isinstance(edit_request, Mapping):
            raise PlanningServiceError("invalid_document_edit", "Roadmap document edit must be an object")
        allowed = {
            "section", "section_id", "task_id", "proposed_value", "proposed_payload",
            "reason", "base_source_hash", "base_revision", "base_projection_hash",
        }
        if set(edit_request) - allowed:
            raise PlanningServiceError("invalid_document_edit", "Roadmap document edit contains unsupported fields")
        _assert_no_forbidden_content(edit_request)
        section = str(edit_request.get("section") or "").strip().lower()
        section_id = str(edit_request.get("section_id") or "").strip().lower()
        if section not in {"summary", "task", "data"}:
            raise PlanningServiceError("invalid_document_section", "Roadmap document section is not editable")
        expected_section_id = {"summary": "summary", "task": "tasks", "data": "data"}[section]
        if section_id != expected_section_id:
            raise PlanningServiceError("invalid_document_section_id", "Roadmap document section id is invalid")
        reason = str(edit_request.get("reason") or "").strip()
        if not reason or len(reason) > 300:
            raise PlanningServiceError("invalid_document_edit_reason", "Roadmap document edit reason is required and bounded")
        for field in ("base_source_hash", "base_projection_hash"):
            value = edit_request.get(field)
            if not isinstance(value, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
                raise PlanningServiceError("invalid_document_base", f"Roadmap document {field} is required")
        if "base_revision" not in edit_request:
            raise PlanningServiceError("invalid_document_base", "Roadmap document base_revision is required")
        base_revision = edit_request.get("base_revision")
        if base_revision is not None and (
            not isinstance(base_revision, int) or isinstance(base_revision, bool) or base_revision < 1
        ):
            raise PlanningServiceError("invalid_document_base", "Roadmap document base_revision is invalid")

        document = self.read_document(project_id, roadmap_id, max_items=MAX_CONTEXT_ITEMS)
        actual_source_hash = str(document["source_hash"])
        actual_revision = document.get("revision")
        actual_projection_hash = str(document["canonical"]["projection_hash"])
        base_material = {
            "project_id": document["project_id"],
            "roadmap_id": document["roadmap_id"],
            "section": section,
            "section_id": section_id,
            "task_id": str(edit_request.get("task_id") or ""),
            "base_source_hash": edit_request["base_source_hash"],
            "base_revision": base_revision,
            "base_projection_hash": edit_request["base_projection_hash"],
            "reason": reason,
        }
        conflicts: list[dict[str, str]] = []
        if edit_request["base_source_hash"] != actual_source_hash:
            conflicts.append(_issue("source_hash_mismatch", "$.base_source_hash", "Document source hash no longer matches"))
        if base_revision != actual_revision:
            conflicts.append(_issue("revision_mismatch", "$.base_revision", "Document revision no longer matches"))
        if edit_request["base_projection_hash"] != actual_projection_hash:
            conflicts.append(_issue("projection_hash_mismatch", "$.base_projection_hash", "Document projection hash no longer matches"))
        if conflicts:
            return _document_edit_envelope(
                document=document,
                section=section,
                section_id=section_id,
                task_id=str(edit_request.get("task_id") or ""),
                reason=reason,
                edit_hash_material={**base_material, "conflicts": [item["code"] for item in conflicts]},
                status="conflict",
                conflicts=conflicts,
            )

        record, payload = self._document_source(document["project_id"], document["roadmap_id"])
        task_id = ""
        changes: dict[str, Any]
        operations: list[dict[str, Any]]
        candidate_validation: dict[str, Any] | None = None
        if section == "summary":
            proposed = edit_request.get("proposed_value")
            if not isinstance(proposed, str) or not proposed.strip() or len(proposed) > 1_200:
                raise PlanningServiceError("invalid_document_value", "Summary edit value is required and bounded")
            current = payload.get("summary")
            changes = {"summary": proposed.strip()}
            operations = [_document_operation("/summary", current, proposed.strip())]
        elif section == "task":
            task_id = str(edit_request.get("task_id") or "").strip()
            if not task_id or len(task_id) > 120:
                raise PlanningServiceError("invalid_document_task_id", "Task edit requires a bounded task id")
            slice_key, raw_slices = _slice_collection(payload)
            if not isinstance(raw_slices, list):
                raise PlanningServiceError("document_task_not_found", "Roadmap task was not found")
            matches = [index for index, item in enumerate(raw_slices) if isinstance(item, Mapping) and str(item.get("id") or item.get("node_id") or "") == task_id]
            if not matches:
                raise PlanningServiceError("document_task_not_found", "Roadmap task was not found")
            if len(matches) != 1:
                raise PlanningServiceError("document_task_ambiguous", "Roadmap task id is ambiguous")
            index = matches[0]
            before_task = dict(raw_slices[index])
            updates = _document_task_updates(edit_request.get("proposed_value"))
            after_task = deepcopy(before_task)
            after_task.update(updates)
            candidate_slices = deepcopy(raw_slices)
            candidate_slices[index] = after_task
            changes = {slice_key: candidate_slices}
            pointer = _json_pointer_token(task_id)
            operations = [
                _document_operation(f"/{slice_key}/{pointer}/{field}", before_task.get(field), value)
                for field, value in sorted(updates.items())
            ]
        else:
            raw_candidate = edit_request.get("proposed_payload", edit_request.get("proposed_value"))
            candidate = _document_data_candidate(raw_candidate)
            current_projection = document["canonical"]["projection"]
            _validate_document_candidate_identity(candidate, current_projection)
            candidate_validation = planning_validate_roadmap(candidate, source_ref=str(record["path"]))
            changes = {}
            operations = []
            slice_key, _raw_slices = _slice_collection(payload)
            for field in sorted(_PATCHABLE_FIELDS - {"slice_queue"}):
                if field not in candidate:
                    continue
                current_value = current_projection.get(field)
                candidate_value = candidate.get(field)
                if candidate_value == current_value:
                    continue
                target_field = slice_key if field == "slices" and slice_key else field
                changes[target_field] = deepcopy(candidate_value)
                operations.append(_document_operation(f"/{field}", current_value, candidate_value))
            if not changes:
                raise PlanningServiceError("empty_document_edit", "Data edit contains no changed fields")

        patch = self.propose_patch(
            str(record["source_id"]),
            {
                "base_source_hash": actual_source_hash,
                "base_revision": actual_revision,
                "changes": changes,
            },
            reason=reason,
        )
        if candidate_validation is not None and not candidate_validation.get("valid"):
            patch["status"] = "invalid"
            patch["ready_for_apply"] = False
            patch["validation"] = candidate_validation
        status = str(patch.get("status") or "invalid")
        return _document_edit_envelope(
            document=document,
            section=section,
            section_id=section_id,
            task_id=task_id,
            reason=reason,
            edit_hash_material={
                **base_material,
                "changes_hash": _payload_hash(changes),
                "operations": [(item["op"], item["path"], item["after"]["hash"]) for item in operations],
            },
            status=status,
            operations=operations,
            conflicts=list(patch.get("conflicts") or []),
            warnings=list(patch.get("warnings") or []),
            validation=dict(patch.get("validation") or {}),
            patch=patch,
        )

    def get_section_context_pack(
        self,
        project_id: str,
        roadmap_id: str,
        section_id: str,
        *,
        item_id: str = "",
        task_id: str = "",
        gate_id: str = "",
        max_items: int = 12,
        include_memory: bool = True,
    ) -> dict[str, Any]:
        """Build a bounded Spark handoff for one stable roadmap section or item."""

        section = str(section_id or "").strip().lower()
        if section not in {"summary", "tasks", "gates", "sources", "data"}:
            raise PlanningServiceError("invalid_section_context", "Roadmap context section is not supported")
        item_limit = _strict_budget(max_items, field="max_items", minimum=1, maximum=MAX_CONTEXT_ITEMS)
        if not isinstance(include_memory, bool):
            raise PlanningServiceError("invalid_memory_option", "include_memory must be boolean")
        selector_values = [
            (name, str(value or "").strip())
            for name, value in (("item_id", item_id), ("task_id", task_id), ("gate_id", gate_id))
            if str(value or "").strip()
        ]
        if len({value for _name, value in selector_values}) > 1:
            raise PlanningServiceError("ambiguous_section_item", "Roadmap context item selectors disagree")
        selector = selector_values[0][1] if selector_values else ""
        if selector and (len(selector) > 120 or _contains_sensitive_value(selector)):
            raise PlanningServiceError("invalid_section_item", "Roadmap context item id is invalid")
        if task_id and section != "tasks":
            raise PlanningServiceError("invalid_section_item", "Task selector requires the tasks section")
        if gate_id and section != "gates":
            raise PlanningServiceError("invalid_section_item", "Gate selector requires the gates section")
        if selector and section not in {"tasks", "gates"}:
            raise PlanningServiceError("invalid_section_item", "This roadmap section has no selectable items")

        document = self.read_document(
            project_id,
            roadmap_id,
            max_items=item_limit,
            canonical_json_chars=0,
            include_memory=include_memory,
        )
        _record, source_payload = self._document_source(document["project_id"], document["roadmap_id"])
        all_tasks = _extract_slices(source_payload, limit=200)
        all_gates = _extract_gates(source_payload, limit=200)
        selected_item_id = ""
        if section == "summary":
            content: dict[str, Any] = {
                "kind": "summary",
                "summary": _safe_text(document.get("summary") or "", max_chars=1_000),
            }
            returned_count = 1
            total_count = 1
        elif section == "tasks":
            items = all_tasks
            if selector:
                matches = [item for item in items if item.get("id") == selector]
                if not matches:
                    raise PlanningServiceError("section_task_not_found", "Roadmap context task was not found")
                if len(matches) != 1:
                    raise PlanningServiceError("section_task_ambiguous", "Roadmap context task id is ambiguous")
                items = matches
                selected_item_id = selector
            else:
                items = items[:item_limit]
            content = {"kind": "task" if selector else "tasks", "items": deepcopy(items)}
            returned_count = len(items)
            total_count = len(all_tasks)
        elif section == "gates":
            items = all_gates
            if selector:
                matches = [item for item in items if item.get("id") == selector]
                if not matches:
                    raise PlanningServiceError("section_gate_not_found", "Roadmap context gate was not found")
                if len(matches) != 1:
                    raise PlanningServiceError("section_gate_ambiguous", "Roadmap context gate id is ambiguous")
                items = matches
                selected_item_id = selector
            else:
                items = items[:item_limit]
            content = {"kind": "gate" if selector else "gates", "items": deepcopy(items)}
            returned_count = len(items)
            total_count = len(all_gates)
        elif section == "sources":
            refs = list(document.get("source_refs") or [])[:item_limit]
            content = {"kind": "sources", "items": refs}
            returned_count = len(refs)
            total_count = len(document.get("source_refs") or [])
        else:
            projection = document.get("canonical", {}).get("projection") or {}
            task_refs = [
                {"id": item.get("id") or "", "status": item.get("status") or "unknown"}
                for item in all_tasks[:item_limit]
            ]
            gate_refs = [
                {"id": item.get("id") or "", "status": item.get("status") or "open"}
                for item in all_gates[:item_limit]
            ]
            content = {
                "kind": "canonical_projection",
                "projection": {
                    "schema_version": projection.get("schema_version"),
                    "kind": projection.get("kind") or "planning_roadmap",
                    "project_id": document["project_id"],
                    "roadmap_id": document["roadmap_id"],
                    "title": document.get("title") or "",
                    "goal": document.get("goal") or "",
                    "status": document.get("status") or "unknown",
                    "revision": document.get("revision"),
                    "source_refs": list(document.get("source_refs") or [])[:item_limit],
                    "task_refs": task_refs,
                    "gate_refs": gate_refs,
                    "dependency_refs": _safe_scalar_list(projection.get("dependency_refs"), limit=item_limit),
                },
                "projection_hash": document.get("canonical", {}).get("projection_hash") or "",
                "raw_json_included": False,
                "canonical_json_included": False,
            }
            returned_count = len(task_refs) + len(gate_refs)
            total_count = len(all_tasks) + len(all_gates)

        source_refs = list(document.get("source_refs") or [])[:item_limit]
        lens_ref = document.get("lens_summary", {}).get("evidence_ref") or document.get("source_ref") or ""
        lens_evidence_refs = _dedupe(_safe_ref_list([lens_ref], limit=item_limit), limit=item_limit)
        memory_refs = list(document.get("memory_refs") or [])[: min(item_limit, 12)] if include_memory else []
        list_truncated = not selector and total_count > returned_count
        supporting_incomplete = bool(
            not document.get("validation", {}).get("valid", False)
            or document.get("lens_summary", {}).get("incomplete")
            or document.get("memory_summary", {}).get("incomplete")
        )
        identity = {
            "project_id": document["project_id"],
            "roadmap_id": document["roadmap_id"],
            "section_id": section,
            "item_id": selected_item_id,
            "source_hash": document["source_hash"],
        }
        pack = {
            "schema": "odysseus.planning.section_context_pack.v1",
            "context_pack_id": "planning-context-" + _payload_hash(identity).split(":", 1)[-1][:20],
            "read_only": True,
            "writes_supported": False,
            "agent_dispatch_performed": False,
            "events_emitted": False,
            "notifications_emitted": False,
            "project_id": document["project_id"],
            "roadmap_id": document["roadmap_id"],
            "section_id": section,
            "item_id": selected_item_id,
            "item_kind": "task" if section == "tasks" and selected_item_id else "gate" if selected_item_id else "",
            "section_ref": f"roadmap:{document['project_id']}:{document['roadmap_id']}:{section}",
            "item_ref": (
                f"roadmap:{document['project_id']}:{document['roadmap_id']}:{section}:{selected_item_id}"
                if selected_item_id else ""
            ),
            "title": _safe_text(document.get("title") or "Untitled roadmap", max_chars=200),
            "summary": _safe_text(document.get("summary") or document.get("goal") or "", max_chars=600),
            "content": content,
            "source_refs": source_refs,
            "memory_refs": memory_refs,
            "lens_evidence_refs": lens_evidence_refs,
            "truth_level": "semantic_projection",
            "classification": "private",
            "redaction_state": "summary_only",
            "source_of_truth": False,
            "canonical_source": {
                "source_id": document.get("source_id") or "",
                "source_ref": document.get("source_ref") or "",
                "source_hash": document.get("source_hash") or "",
                "revision": document.get("revision"),
            },
            "selection": {
                "requested_items": item_limit,
                "total_items": total_count,
                "returned_items": returned_count,
                "memory_requested": include_memory,
            },
            "truncated": list_truncated or bool(document.get("memory_summary", {}).get("truncated")),
            "incomplete": list_truncated or supporting_incomplete,
            "raw_content_included": False,
            "absolute_paths_visible": False,
            "payload_budget_bytes": MAX_SECTION_CONTEXT_BYTES,
        }
        return _fit_section_context_budget(pack, MAX_SECTION_CONTEXT_BYTES)

    def plan_document_memory_bridge(
        self,
        project_id: str,
        roadmap_id: str,
        metadata: Mapping[str, Any],
        *,
        existing_records: Iterable[Mapping[str, Any]] | Mapping[str, Any] = (),
        max_operations: int = 100,
    ) -> dict[str, Any]:
        """Build derived Planning memory and its lifecycle plan without writing."""

        if not isinstance(metadata, Mapping):
            raise PlanningServiceError("invalid_memory_bridge_metadata", "Planning memory bridge metadata must be an object")
        allowed = {
            "validation", "project_id", "roadmap_id", "source_id", "source_ref",
            "source_hash", "revision",
        }
        required = allowed
        if set(metadata) != required:
            raise PlanningServiceError(
                "invalid_memory_bridge_metadata",
                "Planning memory bridge metadata must contain the exact identity contract",
            )
        _assert_no_forbidden_content(metadata)
        validation = metadata.get("validation")
        if not isinstance(validation, Mapping) or set(validation) != {"valid", "mode"}:
            raise PlanningServiceError("invalid_memory_bridge_validation", "Planning memory bridge validation is invalid")
        validation_mode = str(validation.get("mode") or "").strip().lower()
        if validation.get("valid") is not True or validation_mode not in {"canonical", "transition"}:
            raise PlanningServiceError("invalid_memory_bridge_validation", "Planning memory bridge validation must be approved")
        operation_limit = _strict_budget(
            max_operations,
            field="max_operations",
            minimum=1,
            maximum=500,
        )

        document = self.read_document(
            project_id,
            roadmap_id,
            max_items=MAX_CONTEXT_ITEMS,
            canonical_json_chars=0,
            include_memory=False,
        )
        _record, source_payload = self._document_source(document["project_id"], document["roadmap_id"])
        actual = {
            "project_id": document["project_id"],
            "roadmap_id": document["roadmap_id"],
            "source_id": document["source_id"],
            "source_ref": document["source_ref"],
            "source_hash": document["source_hash"],
            "revision": document.get("revision"),
        }
        supplied_hash = str(metadata.get("source_hash") or "").strip().lower()
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", supplied_hash):
            raise PlanningServiceError("invalid_memory_bridge_metadata", "Planning memory bridge source hash is invalid")
        supplied_revision = metadata.get("revision")
        if not isinstance(supplied_revision, int) or isinstance(supplied_revision, bool) or supplied_revision < 1:
            raise PlanningServiceError("invalid_memory_bridge_metadata", "Planning memory bridge revision is invalid")
        for field in ("project_id", "roadmap_id", "source_id", "source_ref"):
            if str(metadata.get(field) or "") != str(actual[field] or ""):
                raise PlanningServiceError("memory_bridge_identity_conflict", "Planning memory bridge identity no longer matches")
        if supplied_hash != str(actual["source_hash"] or "").lower():
            raise PlanningServiceError("memory_bridge_hash_conflict", "Planning memory bridge source hash no longer matches")
        if supplied_revision != actual["revision"]:
            raise PlanningServiceError("memory_bridge_revision_conflict", "Planning memory bridge revision no longer matches")

        source_validation = planning_validate_roadmap(source_payload, source_ref=document["source_ref"])
        if not source_validation.get("valid"):
            raise PlanningServiceError("memory_bridge_source_invalid", "Planning memory bridge source validation failed")
        if validation_mode == "canonical" and not _is_planning_roadmap_kind(source_payload.get("kind")):
            raise PlanningServiceError("memory_bridge_mode_conflict", "Planning memory bridge canonical mode does not match the source")

        projection = document.get("canonical", {}).get("projection") or {}
        builder_input = {
            "validation": {"valid": True, "mode": validation_mode},
            **actual,
            "source_revision": str(actual["revision"]),
            "safe_summary": _safe_text(
                document.get("summary") or document.get("goal") or document.get("title") or "",
                max_chars=360,
            ),
            "gate_refs": _safe_scalar_list(projection.get("gate_refs"), limit=24),
            "dependency_refs": _safe_scalar_list(projection.get("dependency_refs"), limit=24),
            "source_refs": list(document.get("source_refs") or [])[:24],
            "classification": "private",
            "acceptance_status": "accepted",
            "source_status": "current",
            "precedence_rank": 100 if validation_mode == "canonical" else 80,
        }
        derived = build_derived_planning_memory_records(
            [builder_input],
            max_records=1,
            summary_chars=360,
            ref_budget=24,
        )
        if derived.get("summary", {}).get("returned") != 1 or len(derived.get("entries") or []) != 1:
            raise PlanningServiceError("memory_bridge_projection_rejected", "Planning memory bridge projection was rejected")
        safe_existing = _memory_bridge_existing_records(
            existing_records,
            project_id=document["project_id"],
            roadmap_id=document["roadmap_id"],
            source_id=document["source_id"],
        )
        lifecycle = plan_planning_memory_lifecycle(
            derived,
            existing_records=safe_existing,
            max_operations=operation_limit,
        )
        response = {
            "schema": "odysseus.planning.document_memory_bridge_plan.v1",
            "project_id": document["project_id"],
            "roadmap_id": document["roadmap_id"],
            "source_id": document["source_id"],
            "source_ref": document["source_ref"],
            "source_hash": document["source_hash"],
            "revision": document.get("revision"),
            "validation": {
                "valid": True,
                "mode": validation_mode,
                "source_schema": source_validation.get("schema") or "",
            },
            "derived_index": derived,
            "derived_entries": list(derived.get("entries") or []),
            "lifecycle_plan": lifecycle,
            "source_of_truth": False,
            "derived": True,
            "rebuildable": True,
            "dry_run": True,
            "read_only": True,
            "writes_supported": False,
            "writes_performed": False,
            "memory_manager_called": False,
            "vector_write_performed": False,
            "database_write_performed": False,
            "file_write_performed": False,
            "raw_json_included": False,
            "raw_body_included": False,
            "absolute_paths_visible": False,
            "truncated": bool(derived.get("summary", {}).get("truncated") or lifecycle.get("summary", {}).get("truncated")),
        }
        response["plan_id"] = "planning-memory-plan-" + _payload_hash({
            "identity": actual,
            "validation_mode": validation_mode,
            "derived_hashes": [item.get("content_hash") for item in response["derived_entries"]],
            "operations": lifecycle.get("operations") or [],
        }).split(":", 1)[-1][:20]
        response["payload_bytes"] = _json_size(response)
        if response["payload_bytes"] > MAX_CONTEXT_BYTES:
            raise PlanningServiceError("memory_bridge_output_budget_exceeded", "Planning memory bridge plan exceeds its output budget")
        return response

    def classify_planning_event(
        self,
        event_type: str,
        *,
        project_id: str,
        roadmap_id: str | None = None,
        gate_id: str | None = None,
        revision: int | None = None,
        content_hash: str = "",
        reason: str,
        created_at: str = "1970-01-01T00:00:00Z",
    ) -> dict[str, Any]:
        """Classify one definition event without accepting execution events."""

        safe_project_id = _strict_document_id(project_id, field="project_id")
        safe_roadmap_id = _strict_document_id(roadmap_id, field="roadmap_id") if roadmap_id is not None else None
        if gate_id is not None:
            raise PlanningServiceError(
                "runtime_gate_event_forbidden",
                "Planning event classification does not accept runtime gate events",
            )
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 240:
            raise PlanningServiceError("invalid_planning_event_reason", "Planning event reason is required and bounded")
        _assert_no_forbidden_content({"reason": reason})
        if re.search(r"(?i)\b[A-Za-z][A-Za-z0-9+.-]*://", reason):
            raise PlanningServiceError("invalid_planning_event_reason", "Planning event reason contains forbidden material")
        if event_type in _DEFINITION_SILENT_EVENTS:
            classification = "silent"
        elif event_type in _DEFINITION_NOTIFICATION_EVENTS:
            classification = "notification_candidate"
        else:
            raise PlanningServiceError("invalid_planning_event", "Planning event type is not allowlisted")
        if event_type not in {"project_created", "project_deleted"} and safe_roadmap_id is None:
            raise PlanningServiceError(
                "invalid_planning_event_metadata",
                "Planning roadmap identity is required for this definition event",
            )
        if event_type in _REVISION_NOTIFICATION_EVENTS:
            safe_revision = _definition_revision_selector(revision)
            if safe_revision == "latest_approved":
                raise PlanningServiceError(
                    "invalid_revision",
                    "Definition notification requires an exact positive revision",
                )
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(content_hash or "")):
                raise PlanningServiceError(
                    "invalid_content_hash",
                    "Definition notification requires an exact content hash",
                )
            safe_content_hash = str(content_hash)
        else:
            if revision is not None or content_hash:
                raise PlanningServiceError(
                    "invalid_planning_event_metadata",
                    "Revision metadata is accepted only for definition revision events",
                )
            safe_revision = None
            safe_content_hash = ""
        safe_created_at = _definition_event_timestamp(created_at)

        refs = {
            "project_id": safe_project_id,
            **({"roadmap_id": safe_roadmap_id} if safe_roadmap_id is not None else {}),
            **({"revision": safe_revision} if safe_revision is not None else {}),
            **({"content_hash": safe_content_hash} if safe_content_hash else {}),
        }
        category = "routine_definition_event" if classification == "silent" else "structural_definition_event"
        reason_code = "definition_event_silent_by_policy" if classification == "silent" else "sparse_definition_candidate_by_policy"
        audit = {
            "schema_id": "odysseus.planning.definition_event_audit.v2",
            "event_type": event_type,
            "category": category,
            "reason_code": reason_code,
            "ref_fields": sorted(refs),
            "ref_count": len(refs),
            "ref_hash": _payload_hash(refs),
            "raw_refs_visible": False,
            "raw_reason_visible": False,
            "derived_entries_visible": False,
            "private_paths_visible": False,
        }
        if classification == "silent":
            candidate_payload: dict[str, Any] | None = None
            dedupe_key = _payload_hash({"event_type": event_type, "refs": refs, "category": category})
        else:
            dedupe_key = _payload_hash({"event_type": event_type, "refs": refs, "category": category})
            highlight_id = safe_roadmap_id or safe_project_id
            candidate_payload = {
                "schema_id": "odysseus.planning.definition_notification_candidate.v2",
                "classification": "notification_candidate",
                "event_type": event_type,
                "project_id": safe_project_id,
                "roadmap_id": safe_roadmap_id,
                "revision": safe_revision,
                "content_hash": safe_content_hash,
                "reason": reason.strip(),
                "created_at": safe_created_at,
                "ui_target": {
                    "workspace": "planning",
                    "view": "overview",
                    "highlight_kind": "roadmap" if safe_roadmap_id else "project",
                    "highlight_id": highlight_id,
                    "highlight_mode": "expand_summary" if safe_roadmap_id else "focus",
                    "document_view_intent": "open_roadmap_document" if safe_roadmap_id else "none",
                },
                "dedupe_key": dedupe_key,
                "delivery_authorized": False,
                "live_delivery_performed": False,
            }

        result = {
            "schema_id": "odysseus.planning.definition_event_classification.v2",
            "classification": classification,
            "candidate": candidate_payload,
            "dedupe_key": dedupe_key,
            "audit": audit,
            "source_of_truth": False,
            "dry_run": True,
            "read_only": True,
            "writes_performed": False,
            "events_emitted": False,
            "notifications_emitted": False,
            "delivery_authorized": False,
            "live_delivery_performed": False,
        }
        result["payload_bytes"] = _json_size(result)
        _assert_no_runtime_fields(result)
        return result

    def read_gate_definitions(
        self,
        project_id: str,
        roadmap_id: str,
        *,
        revision_or_latest_approved: str | int = "latest_approved",
        node_id: str = "",
    ) -> dict[str, Any]:
        """Return immutable gate requirements without consulting runtime state."""

        read_model = self._definition_revision(
            project_id,
            roadmap_id,
            revision_or_latest_approved=revision_or_latest_approved,
        )
        roadmap = read_model["roadmap"]
        safe_node_id = _strict_optional_definition_id(node_id, field="node_id")
        gates = []
        for raw_gate in roadmap.get("gates") or []:
            if not isinstance(raw_gate, Mapping):
                raise PlanningServiceError(
                    "invalid_definition_read_model",
                    "Planning gate definition is invalid",
                )
            gate = _definition_gate_projection(raw_gate)
            if safe_node_id and safe_node_id not in gate["blocks"] and safe_node_id != gate["gate_id"]:
                continue
            gates.append(gate)
        result = {
            "schema_id": "odysseus.planning.gate_definitions.v2",
            "project_id": roadmap["project_id"],
            "roadmap_id": roadmap["roadmap_id"],
            "revision": roadmap["revision"],
            "content_hash": roadmap["content_hash"],
            "node_id": safe_node_id,
            "gate_definitions": gates,
            "read_only": True,
            "writes_performed": False,
        }
        _assert_no_runtime_fields(result)
        return result

    def create_agent_handoff(
        self,
        project_id: str,
        roadmap_id: str,
        *,
        revision_or_latest_approved: str | int = "latest_approved",
    ) -> dict[str, Any]:
        """Create a hash-pinned composer envelope without launching an Agent run."""

        read_model = self._definition_revision(
            project_id,
            roadmap_id,
            revision_or_latest_approved=revision_or_latest_approved,
        )
        roadmap = read_model["roadmap"]
        try:
            envelope = build_agent_plan_handoff(
                read_model,
                expected_revision=roadmap["revision"],
                expected_hash=roadmap["content_hash"],
            )
        except PlanningAgentHandoffError as exc:
            raise PlanningServiceError(exc.code, "Planning Agent handoff was rejected") from exc
        _assert_no_runtime_fields(envelope)
        return envelope

    def deprecated_tool_response(self, name: str) -> dict[str, Any]:
        """Return a stable zero-side-effect response for legacy runtime tools."""

        replacements = {
            "planning_mark_status": {"replacement_surface": "agent"},
            "planning_gate_status": {"replacement_tool": "planning_read_gate_definitions"},
        }
        if name not in replacements:
            raise PlanningServiceError("unknown_deprecated_tool", "Planning tool is not deprecated")
        return {
            "schema_id": "odysseus.planning.deprecated_tool.v1",
            "tool": name,
            "error": "deprecated_tool",
            **replacements[name],
            "read_only": True,
            "writes_performed": False,
        }

    def validate_definition(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Validate Definition v2 and return a bounded, content-free receipt."""

        if not isinstance(payload, Mapping):
            raise PlanningServiceError("invalid_definition", "Planning definition must be an object")
        try:
            receipt = validate_planning_definition(deepcopy(dict(payload))).to_dict()
        except PlanningDefinitionContractError as exc:
            raise PlanningServiceError(exc.reason_code, "Planning definition validation failed") from exc
        result = {
            "schema_id": "odysseus.planning.definition_validation.v2",
            "validation": receipt,
            "read_only": True,
            "writes_performed": False,
        }
        _assert_no_runtime_fields(result)
        return result

    def _definition_revision(
        self,
        project_id: str,
        roadmap_id: str,
        *,
        revision_or_latest_approved: str | int,
    ) -> dict[str, Any]:
        if self._definition_store is None or not self._definition_owner:
            raise PlanningServiceError(
                "definition_store_unavailable",
                "Planning Definition v2 store is unavailable",
            )
        safe_project_id = _strict_definition_id(project_id, field="project_id")
        safe_roadmap_id = _strict_definition_id(roadmap_id, field="roadmap_id")
        revision = _definition_revision_selector(revision_or_latest_approved)
        try:
            read_model = self._definition_store.get_roadmap(
                self._definition_owner,
                safe_project_id,
                safe_roadmap_id,
                revision=revision,
            )
        except PlanningRevisionStoreError as exc:
            raise PlanningServiceError(exc.code, "Planning definition revision was not resolved") from exc
        if not isinstance(read_model, Mapping):
            raise PlanningServiceError(
                "invalid_definition_read_model",
                "Planning definition read model is invalid",
            )
        project = read_model.get("project")
        roadmap = read_model.get("roadmap")
        if not isinstance(project, Mapping) or not isinstance(roadmap, Mapping):
            raise PlanningServiceError(
                "invalid_definition_read_model",
                "Planning definition read model is invalid",
            )
        try:
            validate_planning_definition(
                {
                    "schema_id": PLANNING_DEFINITION_SCHEMA_ID,
                    "project": deepcopy(dict(project)),
                    "roadmaps": [deepcopy(dict(roadmap))],
                }
            )
        except PlanningDefinitionContractError as exc:
            raise PlanningServiceError(exc.reason_code, "Planning definition read model is invalid") from exc
        return deepcopy(dict(read_model))

    def _document_source(self, project_id: str, roadmap_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        matches = [
            record
            for record in self._roadmap_records()
            if record.get("project_id") == project_id and record.get("roadmap_id") == roadmap_id
        ]
        if not matches:
            raise PlanningServiceError("roadmap_document_not_found", "Roadmap document was not found")
        if len(matches) != 1:
            raise PlanningServiceError("roadmap_document_ambiguous", "Roadmap document identity resolves to multiple sources")
        record = matches[0]
        payload = record.get("_payload") if isinstance(record.get("_payload"), Mapping) else {}
        return record, dict(payload)

    def search_roadmaps(
        self,
        query: str,
        *,
        filters: Mapping[str, Any] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Search bounded roadmap metadata and structural fields."""

        safe_filters = filters if isinstance(filters, Mapping) else {}
        records = self._roadmap_records()
        filtered = self._filter_records(
            records,
            kind=str(safe_filters.get("kind") or "") or None,
            status=str(safe_filters.get("status") or "") or None,
            query=query,
        )
        bounded_limit = _clamp_int(limit, minimum=1, maximum=MAX_LIST_LIMIT, default=20)
        results = [self._record_summary(record) for record in filtered[:bounded_limit]]
        return {
            "schema": "odysseus.planning.roadmap_search.v1",
            "read_only": True,
            "query": _safe_text(query, max_chars=200),
            "summary": {
                "matches": len(filtered),
                "returned": len(results),
                "clipped": len(filtered) > len(results),
            },
            "results": results,
        }

    def validate_roadmap(
        self,
        roadmap_payload: Mapping[str, Any] | str,
        *,
        source_ref: str | None = None,
    ) -> dict[str, Any]:
        return planning_validate_roadmap(roadmap_payload, source_ref=source_ref)

    def create_roadmap_draft(
        self,
        proposal: Mapping[str, Any] | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        """Create a validated in-memory roadmap draft; persistence is out of scope."""

        return _create_roadmap_draft(proposal, fields)

    def propose_patch(
        self,
        roadmap_ref: str,
        proposal: Mapping[str, Any],
        *,
        reason: str,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Build a deterministic field-level patch proposal without applying it."""

        if not _coerce_bool(dry_run, default=True):
            raise PlanningServiceError("apply_not_supported", "PMCP-4 supports dry-run patch proposals only")
        if not isinstance(proposal, Mapping):
            raise PlanningServiceError("invalid_patch_proposal", "Patch proposal must be an object")
        safe_reason = str(reason or "").strip()
        if not safe_reason:
            raise PlanningServiceError("missing_patch_reason", "Patch proposal reason is required")
        if len(safe_reason) > 300:
            raise PlanningServiceError("patch_reason_too_large", "Patch proposal reason exceeds the budget")
        _assert_no_forbidden_content({"reason": safe_reason, "proposal": proposal})

        record, current, _raw = self._resolve_roadmap(roadmap_ref)
        proposal_copy = deepcopy(dict(proposal))
        expected_hash = str(proposal_copy.pop("base_source_hash", "") or "")
        expected_revision = proposal_copy.pop("base_revision", None)
        wrapped_changes = proposal_copy.pop("changes", None)
        if wrapped_changes is None:
            changes = proposal_copy
        else:
            if proposal_copy:
                raise PlanningServiceError("unknown_patch_metadata", "Patch proposal contains unsupported metadata fields")
            if not isinstance(wrapped_changes, Mapping):
                raise PlanningServiceError("invalid_patch_changes", "Patch changes must be an object")
            changes = deepcopy(dict(wrapped_changes))
        if not changes:
            raise PlanningServiceError("empty_patch", "Patch proposal must contain at least one field change")
        unknown = sorted(set(changes) - _PATCHABLE_FIELDS)
        if unknown:
            raise PlanningServiceError("forbidden_patch_field", "Patch proposal contains a non-patchable field")
        _validate_patch_change_budgets(changes)

        candidate = deepcopy(current)
        base_revision = current.get("revision") if isinstance(current.get("revision"), int) else None
        operations: list[dict[str, Any]] = []
        for field in sorted(changes):
            before = current.get(field)
            after = deepcopy(changes[field])
            candidate[field] = after
            operations.append({
                "op": "replace" if field in current else "add",
                "path": f"/{field}",
                "before": _value_summary(before),
                "after": _value_summary(after),
            })
        candidate_revision = base_revision + 1 if base_revision is not None else None
        if candidate_revision is not None:
            candidate["revision"] = candidate_revision

        conflicts: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        actual_hash = str(record.get("source_hash") or "")
        if expected_hash and expected_hash != actual_hash:
            conflicts.append(_issue("source_hash_mismatch", "$.base_source_hash", "Patch base source hash does not match the current roadmap"))
        elif not expected_hash:
            warnings.append(_issue("base_source_hash_missing", "$.base_source_hash", "Patch proposal has no optimistic source-hash evidence"))
        if expected_revision is not None and expected_revision != base_revision:
            conflicts.append(_issue("revision_mismatch", "$.base_revision", "Patch base revision does not match the current roadmap"))
        elif expected_revision is None:
            warnings.append(_issue("base_revision_missing", "$.base_revision", "Patch proposal has no optimistic revision evidence"))

        validation = planning_validate_roadmap(candidate, source_ref=str(record["path"]))
        candidate_hash = _payload_hash(candidate)
        project_id, roadmap_id, ids_derived = _logical_ids(record, current)
        proposal_material = {
            "source_id": record.get("source_id"),
            "base_source_hash": actual_hash,
            "base_revision": base_revision,
            "changes": changes,
            "reason": safe_reason,
        }
        patch_id = "planning-patch-" + _payload_hash(proposal_material).split(":", 1)[-1][:20]
        ready = validation["valid"] and not conflicts
        return {
            "schema": "odysseus.planning.patch_proposal.v1",
            "patch_id": patch_id,
            "dry_run": True,
            "writes_performed": False,
            "events_emitted": False,
            "notifications_emitted": False,
            "apply_supported": False,
            "required_apply_gate": "PLANNING-APPLY-GO",
            "status": "ready" if ready else ("conflict" if conflicts else "invalid"),
            "ready_for_apply": ready,
            "target": {
                "source_id": record["source_id"],
                "source_ref": record["path"],
                "source_hash": actual_hash,
                "project_id": project_id,
                "roadmap_id": roadmap_id,
                "ids_derived": ids_derived,
            },
            "base_source_hash": actual_hash,
            "base_revision": base_revision,
            "candidate_revision": candidate_revision,
            "candidate_hash": candidate_hash,
            "reason": _safe_text(safe_reason, max_chars=300),
            "operations": operations,
            "diff": {"operation_count": len(operations), "operations": operations},
            "validation": validation,
            "conflicts": conflicts,
            "warnings": warnings,
            "candidate": _roadmap_projection(candidate, project_id=project_id, roadmap_id=roadmap_id),
            "rollback": {"available": False, "reason": "no_write_performed"},
        }

    def get_context_pack(
        self,
        roadmap_ref: str,
        *,
        task: str = "",
        node_id: str = "",
        max_items: int = MAX_CONTEXT_ITEMS,
        include_memory: bool = False,
        memory_capsules: Iterable[Mapping[str, Any]] | Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a compact source-linked handoff without raw roadmap dumps."""

        if not isinstance(include_memory, bool):
            raise PlanningServiceError("invalid_memory_option", "include_memory must be boolean")

        record, payload, _raw = self._resolve_roadmap(roadmap_ref)
        project_id, roadmap_id, ids_derived = _logical_ids(record, payload)
        item_limit = _clamp_int(max_items, minimum=1, maximum=MAX_CONTEXT_ITEMS, default=MAX_CONTEXT_ITEMS)
        slices = _extract_slices(payload, limit=item_limit, preferred_id=node_id)
        gates = _extract_gates(payload, limit=item_limit)
        source_refs = _extract_source_refs(payload, record, limit=item_limit)
        lens_summary = _roadmap_lens_summary(
            payload,
            source_ref=str(record["path"]),
            source_hash=str(record["source_hash"]),
            slices=slices,
            gates=gates,
            limit=item_limit,
        )
        memory_source = "not_requested"
        memory_projection: dict[str, Any] = {
            "entries": [],
            "summary": {
                "candidates": 0,
                "accepted": 0,
                "matched": 0,
                "deduplicated": 0,
                "returned": 0,
                "rejected": 0,
                "truncated": False,
                "incomplete": False,
            },
        }
        if include_memory:
            if memory_capsules is None:
                memory_source = "repo_source_builder"
                memory_candidates: Iterable[Mapping[str, Any]] | Mapping[str, Any] = build_planning_memory_capsules(
                    str(self.repo_root),
                    preview_chars=min(self.preview_chars, 240),
                )
            else:
                memory_source = "injected"
                memory_candidates = memory_capsules
            memory_projection = project_accepted_planning_memory(
                memory_candidates,
                source_id=str(record["source_id"]),
                source_ref=str(record["path"]),
                project_id=project_id,
                roadmap_id=roadmap_id,
                related_refs=source_refs,
                limit=min(item_limit, 12),
                preview_chars=min(self.preview_chars, 240),
            )
        pack = {
            "schema": "odysseus.planning.context_pack.v1",
            "read_only": True,
            "writes_supported": False,
            "roadmap_ref": {
                "source_id": record["source_id"],
                "source_ref": record["path"],
                "source_hash": record["source_hash"],
                "project_id": project_id,
                "roadmap_id": roadmap_id,
                "ids_derived": ids_derived,
            },
            "task": _safe_text(task, max_chars=500),
            "active_node_id": _safe_token(node_id, fallback="", max_chars=120) if node_id else "",
            "roadmap": _roadmap_projection(payload, project_id=project_id, roadmap_id=roadmap_id),
            "slices": slices,
            "gates": gates,
            "dependency_hints": _extract_dependencies(payload, record, limit=item_limit),
            "source_refs": source_refs,
            "roadmap_lens": lens_summary,
            "memory_included": include_memory,
            "memory_source": memory_source,
            "memory": memory_projection.get("entries") or [],
            "memory_summary": {
                "requested": include_memory,
                "source": memory_source,
                **dict(memory_projection.get("summary") or {}),
            },
            "validation": planning_validate_roadmap(payload, source_ref=record["path"]),
            "raw_content_included": False,
            "absolute_paths_visible": False,
            "clipped": False,
        }
        return _fit_context_budget(pack, self.context_budget_bytes)

    def _resolve_allowed_roots(self) -> tuple[tuple[str, Path], ...]:
        roots: list[tuple[str, Path]] = []
        for relative in PLANNING_ROOTS:
            candidate = self.repo_root.joinpath(*relative.split("/"))
            if not candidate.exists():
                continue
            resolved = candidate.resolve(strict=True)
            if not _is_relative_to(resolved, self.repo_root):
                raise PlanningServiceError(
                    "unsafe_planning_root",
                    "Configured Planning root resolves outside the repository",
                )
            roots.append((relative, resolved))
        return tuple(roots)

    def _inventory_records(self) -> list[dict[str, Any]]:
        inventory = build_planning_source_inventory(
            str(self.repo_root),
            allowlist=PLANNING_ROOTS,
            preview_chars=self.preview_chars,
        )
        records: list[dict[str, Any]] = []
        for item in inventory.get("sources") or []:
            if not isinstance(item, Mapping) or str(item.get("extension") or "").lower() != "json":
                continue
            try:
                self._secure_existing_path(str(item.get("path") or ""))
            except PlanningServiceError:
                continue
            records.append(dict(item))
        return records

    def _roadmap_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for record in self._inventory_records():
            try:
                payload, _raw = self._load_json(record["path"])
            except PlanningServiceError:
                if str(record.get("kind")) == "roadmap_json" or "roadmap" in str(record.get("path", "")).lower():
                    record["valid_json"] = False
                    records.append(record)
                continue
            if not _is_roadmap_payload(payload, record):
                continue
            record["valid_json"] = True
            record["_payload"] = payload
            project_id, roadmap_id, ids_derived = _logical_ids(record, payload)
            record["project_id"] = project_id
            record["roadmap_id"] = roadmap_id
            record["ids_derived"] = ids_derived
            record["status"] = _safe_token(payload.get("status"), fallback="unknown", max_chars=80)
            record["goal"] = _safe_text(payload.get("goal") or payload.get("summary") or "", max_chars=300)
            records.append(record)
        return records

    def _resolve_roadmap(self, source_id_or_path: str) -> tuple[dict[str, Any], dict[str, Any], str]:
        reference = str(source_id_or_path or "").strip()
        if not reference:
            raise PlanningServiceError("missing_roadmap_ref", "Roadmap source id or path is required")
        inventory = self._inventory_records()
        if reference.startswith("repo-plan:"):
            matches = [record for record in inventory if record.get("source_id") == reference]
            if not matches:
                raise PlanningServiceError("roadmap_not_found", "Roadmap source id was not found")
            record = matches[0]
        else:
            normalized = _normalize_repo_path(reference)
            matches = [record for record in inventory if str(record.get("path")) == normalized]
            if not matches:
                # Resolve once even when inventory omitted an unsafe link, so callers
                # get an explicit fail-closed path error instead of filesystem access.
                self._secure_existing_path(normalized)
                raise PlanningServiceError("roadmap_not_found", "Roadmap path was not found in the Planning inventory")
            record = matches[0]
        payload, raw = self._load_json(str(record["path"]))
        if not _is_roadmap_payload(payload, record):
            raise PlanningServiceError("not_a_roadmap", "Planning JSON does not contain a roadmap structure")
        return record, payload, raw

    def _secure_existing_path(self, relative: str) -> Path:
        normalized = _normalize_repo_path(relative)
        allowed = next(
            (
                resolved
                for prefix, resolved in self._allowed_roots
                if normalized == prefix or normalized.startswith(prefix + "/")
            ),
            None,
        )
        if allowed is None:
            raise PlanningServiceError("path_not_allowlisted", "Planning path is outside the allowlisted roots")
        candidate = self.repo_root.joinpath(*normalized.split("/"))
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise PlanningServiceError("roadmap_not_found", "Planning path does not resolve to a file") from exc
        if not resolved.is_file():
            raise PlanningServiceError("not_a_file", "Planning path does not resolve to a file")
        if not _is_relative_to(resolved, allowed) or not _is_relative_to(resolved, self.repo_root):
            raise PlanningServiceError("path_escape", "Planning path resolves outside the allowlisted repository root")
        return resolved

    def _load_json(self, relative: str) -> tuple[dict[str, Any], str]:
        path = self._secure_existing_path(relative)
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise PlanningServiceError("roadmap_unavailable", "Planning source metadata is unavailable") from exc
        if size > MAX_SOURCE_BYTES:
            raise PlanningServiceError("roadmap_too_large", "Planning source exceeds the read budget")
        try:
            raw = path.read_text(encoding="utf-8")
            loaded = json.loads(raw)
        except (OSError, UnicodeError) as exc:
            raise PlanningServiceError("roadmap_unavailable", "Planning source could not be read") from exc
        except json.JSONDecodeError as exc:
            raise PlanningServiceError("invalid_json", "Planning source is not valid JSON") from exc
        if not isinstance(loaded, dict):
            raise PlanningServiceError("invalid_roadmap_shape", "Planning source JSON must be an object")
        return loaded, raw

    def _filter_records(
        self,
        records: Iterable[dict[str, Any]],
        *,
        kind: str | None,
        status: str | None,
        query: str,
    ) -> list[dict[str, Any]]:
        kind_filter = str(kind or "").strip().lower()
        status_filter = str(status or "").strip().lower()
        query_filter = str(query or "").strip().lower()
        filtered: list[dict[str, Any]] = []
        for record in records:
            payload = record.get("_payload") if isinstance(record.get("_payload"), Mapping) else {}
            payload_kind = str(payload.get("kind") or record.get("kind") or "").lower()
            payload_status = str(payload.get("status") or record.get("status") or "unknown").lower()
            if kind_filter and payload_kind != kind_filter:
                continue
            if status_filter and payload_status != status_filter:
                continue
            haystack = " ".join(
                str(value or "")
                for value in (
                    record.get("title"),
                    record.get("path"),
                    record.get("source_id"),
                    record.get("project_id"),
                    record.get("roadmap_id"),
                    payload_kind,
                    payload_status,
                    payload.get("goal"),
                    payload.get("summary"),
                    json.dumps(_extract_slices(payload, limit=MAX_CONTEXT_ITEMS), ensure_ascii=False),
                    json.dumps(_extract_gates(payload, limit=MAX_CONTEXT_ITEMS), ensure_ascii=False),
                )
            ).lower()
            if query_filter and query_filter not in haystack:
                continue
            filtered.append(record)
        return filtered

    def _record_summary(self, record: Mapping[str, Any]) -> dict[str, Any]:
        payload = record.get("_payload") if isinstance(record.get("_payload"), Mapping) else {}
        project_id, roadmap_id, ids_derived = _logical_ids(record, payload)
        return {
            "source_id": _safe_token(record.get("source_id"), fallback="unknown", max_chars=120),
            "source_ref": _normalize_repo_path(str(record.get("path") or "")),
            "source_hash": _safe_token(record.get("source_hash"), fallback="", max_chars=90),
            "project_id": project_id,
            "roadmap_id": roadmap_id,
            "ids_derived": ids_derived,
            "title": _safe_text(payload.get("title") or record.get("title") or "Untitled roadmap", max_chars=200),
            "goal": _safe_text(payload.get("goal") or payload.get("summary") or "", max_chars=300),
            "kind": _safe_token(payload.get("kind") or record.get("kind"), fallback="planning_roadmap", max_chars=120),
            "status": _safe_token(payload.get("status"), fallback="unknown", max_chars=80),
            "size_bytes": min(max(int(record.get("size_bytes") or 0), 0), MAX_SOURCE_BYTES),
            "valid_json": bool(record.get("valid_json", True)),
            "preview": _safe_text(record.get("preview") or "", max_chars=self.preview_chars),
            "repo_relative": True,
            "absolute_path_recorded": False,
        }


def planning_list_roadmaps(repo_root: str | os.PathLike[str], **kwargs: Any) -> dict[str, Any]:
    return PlanningMcpService(repo_root).list_roadmaps(**kwargs)


def planning_create_roadmap_draft(
    proposal: Mapping[str, Any] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    return _create_roadmap_draft(proposal, fields)


def planning_read_roadmap(
    repo_root: str | os.PathLike[str],
    source_id_or_path: str,
    **kwargs: Any,
) -> dict[str, Any]:
    return PlanningMcpService(repo_root).read_roadmap(source_id_or_path, **kwargs)


def planning_search_roadmaps(
    repo_root: str | os.PathLike[str],
    query: str,
    **kwargs: Any,
) -> dict[str, Any]:
    return PlanningMcpService(repo_root).search_roadmaps(query, **kwargs)


def planning_validate_roadmap(
    roadmap_payload: Mapping[str, Any] | str,
    *,
    source_ref: str | None = None,
) -> dict[str, Any]:
    """Validate a roadmap in memory without changing the caller's object."""

    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    payload: Any
    if isinstance(roadmap_payload, str):
        if len(roadmap_payload.encode("utf-8")) > MAX_SOURCE_BYTES:
            payload = None
            errors.append(_issue("payload_too_large", "$", "Roadmap payload exceeds the validation budget"))
        else:
            try:
                payload = json.loads(roadmap_payload)
            except json.JSONDecodeError:
                payload = None
                errors.append(_issue("invalid_json", "$", "Roadmap payload is not valid JSON"))
    elif isinstance(roadmap_payload, Mapping):
        payload = deepcopy(dict(roadmap_payload))
    else:
        payload = None
        errors.append(_issue("invalid_type", "$", "Roadmap payload must be an object or JSON text"))

    if payload is not None and not isinstance(payload, dict):
        errors.append(_issue("invalid_shape", "$", "Roadmap payload must be a JSON object"))
        payload = None

    if isinstance(payload, dict):
        schema_version = payload.get("schema_version", 1)
        if not isinstance(schema_version, int) or isinstance(schema_version, bool) or schema_version != 1:
            errors.append(_issue("unsupported_schema_version", "$.schema_version", "Schema version must be integer 1"))
        for field in ("title", "goal"):
            if not str(payload.get(field) or "").strip():
                errors.append(_issue("missing_required_field", f"$.{field}", f"Roadmap {field} is required"))

        slice_key, slices = _slice_collection(payload)
        if slices is None:
            errors.append(_issue("missing_slices", "$", "Roadmap requires slices, slice_queue or graph_nodes"))
        elif not isinstance(slices, list):
            errors.append(_issue("invalid_slices", f"$.{slice_key}", "Roadmap slices must be an array"))
        elif not slices:
            errors.append(_issue("missing_slices", f"$.{slice_key}", "Roadmap slices must not be empty"))
        else:
            seen_ids: set[str] = set()
            for index, item in enumerate(slices[:200]):
                field = f"$.{slice_key}[{index}]"
                if not isinstance(item, Mapping):
                    errors.append(_issue("invalid_slice", field, "Roadmap slice must be an object"))
                    continue
                item_id = str(item.get("id") or item.get("node_id") or "").strip()
                if not item_id:
                    errors.append(_issue("missing_slice_id", field, "Roadmap slice requires id or node_id"))
                elif item_id in seen_ids:
                    errors.append(_issue("duplicate_slice_id", field, "Roadmap slice ids must be unique"))
                seen_ids.add(item_id)
            if len(slices) > 200:
                errors.append(_issue("slice_budget_exceeded", f"$.{slice_key}", "Roadmap contains too many slices"))

        for field in ("source_refs", "stop_rules"):
            value = payload.get(field)
            if value is not None and not isinstance(value, list):
                errors.append(_issue("invalid_array", f"$.{field}", f"Roadmap {field} must be an array"))
        verification = payload.get("verification")
        if verification is not None and not isinstance(verification, (list, Mapping)):
            errors.append(_issue("invalid_verification", "$.verification", "Roadmap verification must be an array or structured object"))
        if "verification" not in payload:
            warnings.append(_issue("verification_missing", "$.verification", "Roadmap has no verification list"))
        if "stop_rules" not in payload:
            warnings.append(_issue("stop_rules_missing", "$.stop_rules", "Roadmap has no stop rules list"))

        for index, ref in enumerate(payload.get("source_refs") or []):
            if not _is_safe_public_ref(ref):
                errors.append(_issue("unsafe_source_ref", f"$.source_refs[{index}]", "Source reference is not repository-relative or typed"))

        serialized = json.dumps(payload, ensure_ascii=False, default=str)
        if _contains_sensitive_value(serialized):
            errors.append(_issue("sensitive_value", "$", "Roadmap payload contains a credential-like or private-path value"))

        if _is_planning_roadmap_kind(payload.get("kind")):
            _validate_canonical_roadmap(payload, errors)

        if source_ref:
            try:
                normalized_source = _normalize_repo_path(source_ref)
            except PlanningServiceError:
                errors.append(_issue("unsafe_source_ref", "$.source_ref", "Validation source reference is unsafe"))
                normalized_source = ""
            if normalized_source and _is_planning_roadmap_kind(payload.get("kind")):
                expected_id = Path(normalized_source).name.removesuffix(".roadmap.json")
                if "/roadmaps/" in normalized_source and payload.get("roadmap_id") != expected_id:
                    errors.append(_issue("roadmap_id_path_mismatch", "$.roadmap_id", "Roadmap id does not match the canonical file name"))

    return {
        "schema": "odysseus.planning.roadmap_validation.v1",
        "read_only": True,
        "writes_performed": False,
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": {"errors": len(errors), "warnings": len(warnings)},
    }


def planning_get_context_pack(
    repo_root: str | os.PathLike[str],
    roadmap_ref: str,
    **kwargs: Any,
) -> dict[str, Any]:
    return PlanningMcpService(repo_root).get_context_pack(roadmap_ref, **kwargs)


def planning_propose_patch(
    repo_root: str | os.PathLike[str],
    roadmap_ref: str,
    proposal: Mapping[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    return PlanningMcpService(repo_root).propose_patch(roadmap_ref, proposal, **kwargs)


def _create_roadmap_draft(
    proposal: Mapping[str, Any] | None,
    fields: Mapping[str, Any],
) -> dict[str, Any]:
    if proposal is not None and not isinstance(proposal, Mapping):
        raise PlanningServiceError("invalid_draft_input", "Roadmap draft input must be an object")
    incoming = deepcopy(dict(proposal or {}))
    for key, value in fields.items():
        if key in incoming:
            raise PlanningServiceError("duplicate_draft_field", "Roadmap draft field was supplied more than once")
        incoming[key] = deepcopy(value)
    allowed = {
        "title", "goal", "mode", "source_refs", "slices", "gates",
        "stop_rules", "verification", "dry_run",
    }
    if set(incoming) - allowed:
        raise PlanningServiceError("unknown_draft_field", "Roadmap draft contains an unsupported field")
    if not _coerce_bool(incoming.get("dry_run"), default=True):
        raise PlanningServiceError("write_not_supported", "PMCP-4 supports dry-run roadmap drafts only")
    _assert_no_forbidden_content(incoming)

    title = str(incoming.get("title") or "").strip()
    goal = str(incoming.get("goal") or "").strip()
    mode = str(incoming.get("mode") or "Standard ABC").strip()
    if not title or len(title) > 200:
        raise PlanningServiceError("invalid_draft_title", "Roadmap draft title is required and bounded")
    if not goal or len(goal) > 1_200:
        raise PlanningServiceError("invalid_draft_goal", "Roadmap draft goal is required and bounded")
    if mode not in _DRAFT_MODES:
        raise PlanningServiceError("invalid_draft_mode", "Roadmap draft mode is not supported")
    source_refs = _require_safe_refs(incoming.get("source_refs", []), field="source_refs", limit=50)
    slices = _normalize_draft_slices(incoming.get("slices"))
    gates = _normalize_draft_gates(incoming.get("gates", []))
    stop_rules = _require_scalar_list(incoming.get("stop_rules"), field="stop_rules", limit=100, required=True)
    verification = _require_scalar_list(incoming.get("verification"), field="verification", limit=100, required=True)

    roadmap = {
        "schema_version": 1,
        "kind": "odysseus.planning_roadmap_draft",
        "title": _safe_text(title, max_chars=200),
        "goal": _safe_text(goal, max_chars=1_200),
        "status": "planned",
        "mode": mode,
        "source_refs": source_refs,
        "slices": slices,
        "gates": gates,
        "stop_rules": stop_rules,
        "verification": verification,
    }
    validation = planning_validate_roadmap(roadmap)
    draft_hash = _payload_hash(roadmap)
    return {
        "schema": "odysseus.planning.roadmap_draft.v1",
        "draft_id": "planning-draft-" + draft_hash.split(":", 1)[-1][:20],
        "draft_hash": draft_hash,
        "dry_run": True,
        "writes_performed": False,
        "events_emitted": False,
        "notifications_emitted": False,
        "persist_supported": False,
        "required_persist_gate": "PLANNING-WRITE-GO",
        "validation": validation,
        "roadmap": roadmap,
    }


def _normalize_repo_path(value: str) -> str:
    raw = str(value or "")
    if not raw or "\x00" in raw:
        raise PlanningServiceError("invalid_path", "Planning path is missing or invalid")
    text = unicodedata.normalize("NFKC", raw.strip())
    decoded = unquote(text)
    if _URI_RE.match(text) or _URI_RE.match(decoded):
        raise PlanningServiceError("uri_path_rejected", "URI-like Planning paths are not allowed")
    if text.startswith(("/", "\\")) or decoded.startswith(("/", "\\")):
        raise PlanningServiceError("absolute_path_rejected", "Absolute or UNC Planning paths are not allowed")
    if ntpath.splitdrive(text)[0] or ntpath.splitdrive(decoded)[0]:
        raise PlanningServiceError("drive_path_rejected", "Drive-qualified Planning paths are not allowed")
    normalized_slashes = decoded.replace("\\", "/")
    parts = normalized_slashes.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise PlanningServiceError("path_traversal", "Planning path contains unsafe traversal segments")
    if any(any(char in part for char in "*?[]") for part in parts):
        raise PlanningServiceError("path_pattern_rejected", "Planning path patterns are not allowed")
    normalized = "/".join(parts)
    lower = normalized.casefold()
    if not any(lower == root.casefold() or lower.startswith(root.casefold() + "/") for root in PLANNING_ROOTS):
        raise PlanningServiceError("path_not_allowlisted", "Planning path is outside the allowlisted roots")
    return normalized


def _is_roadmap_payload(payload: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
    if str(record.get("kind") or "") == "roadmap_json":
        return True
    kind = str(payload.get("kind") or "").lower()
    return bool(
        "roadmap" in kind
        or payload.get("plan_id")
        or any(key in payload for key in ("slice_queue", "slices", "graph_nodes"))
    )


def _logical_ids(record: Mapping[str, Any], payload: Mapping[str, Any]) -> tuple[str, str, bool]:
    explicit_project = _valid_id(payload.get("project_id"))
    explicit_roadmap = _valid_id(payload.get("roadmap_id") or payload.get("plan_id") or payload.get("id"))
    digest = re.sub(r"[^a-f0-9]", "", str(record.get("source_id") or "").split(":")[-1].lower())[:16]
    if not digest:
        digest = "unknown"
    return (
        explicit_project or f"project-{digest[:12]}",
        explicit_roadmap or f"roadmap-{digest[:12]}",
        not bool(explicit_project and explicit_roadmap),
    )


def _roadmap_projection(payload: Mapping[str, Any], *, project_id: str, roadmap_id: str) -> dict[str, Any]:
    return {
        "schema_version": payload.get("schema_version", 1),
        "kind": _safe_token(payload.get("kind"), fallback="planning_roadmap", max_chars=120),
        "project_id": project_id,
        "roadmap_id": roadmap_id,
        "plan_id": _safe_token(payload.get("plan_id"), fallback="", max_chars=120) if payload.get("plan_id") else "",
        "title": _safe_text(payload.get("title") or "Untitled roadmap", max_chars=200),
        "goal": _safe_text(payload.get("goal") or payload.get("summary") or "", max_chars=500),
        "status": _safe_token(payload.get("status"), fallback="unknown", max_chars=80),
        "revision": payload.get("revision") if isinstance(payload.get("revision"), int) else None,
        "created_at": _safe_text(payload.get("created_at") or "", max_chars=40),
        "updated_at": _safe_text(payload.get("updated_at") or "", max_chars=40),
    }


def _slice_collection(payload: Mapping[str, Any]) -> tuple[str, Any]:
    for key in ("slices", "slice_queue", "graph_nodes"):
        if key in payload:
            return key, payload.get(key)
    return "", None


def _extract_slices(
    payload: Mapping[str, Any],
    *,
    limit: int,
    preferred_id: str = "",
) -> list[dict[str, Any]]:
    _key, raw_items = _slice_collection(payload)
    if not isinstance(raw_items, list):
        return []
    items: list[dict[str, Any]] = []
    for raw in raw_items[:200]:
        if not isinstance(raw, Mapping):
            continue
        item_id = str(raw.get("id") or raw.get("node_id") or "").strip()
        items.append({
            "id": _safe_token(item_id, fallback="unnamed_slice", max_chars=120),
            "title": _safe_text(raw.get("title") or raw.get("label") or item_id, max_chars=180),
            "objective": _safe_text(raw.get("objective") or raw.get("summary") or raw.get("goal") or "", max_chars=360),
            "class": _safe_token(raw.get("class"), fallback="", max_chars=80) if raw.get("class") else "",
            "status": _safe_token(raw.get("status"), fallback="unknown", max_chars=80),
            "owner": _safe_token(raw.get("owner"), fallback="", max_chars=80) if raw.get("owner") else "",
            "dependencies": _safe_scalar_list(raw.get("depends_on") or raw.get("dependencies"), limit=12),
            "gates": _safe_scalar_list(raw.get("gates"), limit=12),
            "source_refs": _safe_ref_list(raw.get("source_refs"), limit=12),
        })
    if preferred_id:
        preferred = str(preferred_id)
        items.sort(key=lambda item: item["id"] != preferred)
    return items[:limit]


def _extract_gates(payload: Mapping[str, Any], *, limit: int) -> list[dict[str, Any]]:
    candidates: list[Any] = []
    if isinstance(payload.get("gates"), list):
        candidates.extend(payload.get("gates") or [])
    policy = payload.get("security_and_policy")
    if isinstance(policy, Mapping) and isinstance(policy.get("gates"), list):
        candidates.extend(policy.get("gates") or [])
    gates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in candidates[:200]:
        if not isinstance(raw, Mapping):
            continue
        gate_id = _safe_token(raw.get("id") or raw.get("gate_id"), fallback="unnamed_gate", max_chars=120)
        if gate_id in seen:
            continue
        seen.add(gate_id)
        gates.append({
            "id": gate_id,
            "class": _safe_token(raw.get("class"), fallback="", max_chars=80) if raw.get("class") else "",
            "status": _safe_token(raw.get("status"), fallback="open", max_chars=80),
            "decision_needed": _safe_text(raw.get("decision_needed") or "", max_chars=360),
            "blocks": _safe_scalar_list(raw.get("blocks"), limit=12),
            "risk_if_bypassed": _safe_text(raw.get("risk_if_bypassed") or "", max_chars=240),
        })
    return gates[:limit]


def _extract_source_refs(payload: Mapping[str, Any], record: Mapping[str, Any], *, limit: int) -> list[str]:
    refs: list[Any] = [record.get("path")]
    refs.extend(payload.get("source_refs") if isinstance(payload.get("source_refs"), list) else [])
    refs.extend(record.get("source_refs") if isinstance(record.get("source_refs"), list) else [])
    return _dedupe(_safe_ref_list(refs, limit=limit), limit=limit)


def _extract_dependencies(payload: Mapping[str, Any], record: Mapping[str, Any], *, limit: int) -> list[str]:
    values: list[Any] = []
    values.extend(record.get("dependency_hints") if isinstance(record.get("dependency_hints"), list) else [])
    for item in _extract_slices(payload, limit=MAX_CONTEXT_ITEMS):
        values.extend(item.get("dependencies") or [])
    return _dedupe(_safe_scalar_list(values, limit=limit * 2), limit=limit)


def _fit_context_budget(payload: dict[str, Any], budget: int) -> dict[str, Any]:
    result = deepcopy(payload)
    clipped = bool(result.get("clipped"))
    trim_order = ("memory", "slices", "gates", "dependency_hints", "source_refs")
    result["payload_bytes"] = 0
    while _json_size(result) > budget:
        trimmed = False
        for key in trim_order:
            values = result.get(key)
            if isinstance(values, list) and values:
                values.pop()
                if key == "memory" and isinstance(result.get("memory_summary"), Mapping):
                    result["memory_summary"]["returned"] = len(values)
                    result["memory_summary"]["truncated"] = True
                    result["memory_summary"]["incomplete"] = True
                clipped = True
                trimmed = True
                break
        if not trimmed:
            lens = result.get("roadmap_lens")
            if isinstance(lens, dict):
                for key in ("edges", "nodes", "aggregates", "claimable_node_ids"):
                    values = lens.get(key)
                    if isinstance(values, list) and values:
                        values.pop()
                        lens["clipped"] = True
                        lens["incomplete"] = True
                        clipped = True
                        trimmed = True
                        break
        if not trimmed:
            validation = result.get("validation")
            if isinstance(validation, dict):
                for key in ("errors", "warnings"):
                    values = validation.get(key)
                    if isinstance(values, list) and values:
                        values.pop()
                        validation["details_truncated"] = True
                        clipped = True
                        trimmed = True
                        break
        if not trimmed:
            before = _json_size(result)
            result["task"] = _safe_text(result.get("task") or "", max_chars=120)
            result["roadmap"]["goal"] = _safe_text(result["roadmap"].get("goal") or "", max_chars=180)
            trimmed = _json_size(result) < before
            clipped = clipped or trimmed
        if not trimmed:
            break
    if _json_size(result) > budget:
        result["task"] = ""
        result["slices"] = []
        result["gates"] = []
        result["dependency_hints"] = []
        result["source_refs"] = []
        result["memory"] = []
        if isinstance(result.get("memory_summary"), dict):
            result["memory_summary"].update({"returned": 0, "truncated": True, "incomplete": True})
        validation = result.get("validation") if isinstance(result.get("validation"), Mapping) else {}
        result["validation"] = {
            "schema": validation.get("schema") or "odysseus.planning.roadmap_validation.v1",
            "valid": bool(validation.get("valid")),
            "summary": dict(validation.get("summary") or {}),
            "details_truncated": True,
            "read_only": True,
            "writes_performed": False,
        }
        lens = result.get("roadmap_lens")
        if isinstance(lens, dict):
            for key in ("nodes", "edges", "aggregates", "claimable_node_ids"):
                lens[key] = []
            lens["clipped"] = True
            lens["incomplete"] = True
        result["roadmap"]["goal"] = ""
        clipped = True
    result["clipped"] = clipped
    for _ in range(3):
        result["payload_bytes"] = _json_size(result)
    return result


def _roadmap_lens_summary(
    payload: Mapping[str, Any],
    *,
    source_ref: str,
    source_hash: str,
    slices: list[dict[str, Any]],
    gates: list[dict[str, Any]],
    limit: int,
) -> dict[str, Any]:
    try:
        from src.plan_runtime import PlanRuntimeError, PlanRuntimeState
        from src.progressive_graph_api import GraphQueryBudget
        from src.roadmap_lens import build_roadmap_lens_page

        runtime = PlanRuntimeState.from_dict(dict(payload), roadmap_path=source_ref)
        node_limit = max(4, min(72, limit * 3))
        edge_limit = max(8, min(144, limit * 6))
        budget = GraphQueryBudget.create(
            limit=node_limit,
            max_nodes=node_limit,
            max_edges=edge_limit,
            depth=2,
            max_hops=0,
            time_budget_ms=200,
            payload_budget_bytes=16_384,
        )
        lens = build_roadmap_lens_page(runtime, budget=budget).to_dict()
        return {
            "schema": "odysseus.planning.roadmap_lens_summary.v1",
            "available": True,
            "projection": "roadmap_lens",
            "evidence_ref": source_ref,
            "evidence_hash": source_hash,
            "status": lens.get("status") or "complete",
            "active_node_id": lens.get("active_node_id") or "",
            "claimable_node_ids": list(lens.get("claimable_node_ids") or ())[:limit],
            "nodes": [dict(item) for item in (lens.get("nodes") or ())][:node_limit],
            "edges": [dict(item) for item in (lens.get("edges") or ())][:edge_limit],
            "aggregates": [dict(item) for item in (lens.get("aggregates") or ())][:limit],
            "clipped": bool(lens.get("clipped")),
            "incomplete": bool(lens.get("partial") or lens.get("clipped")),
            "source_of_truth": False,
            "raw_content_included": False,
        }
    except (PlanRuntimeError, TypeError, ValueError, KeyError):
        return {
            "schema": "odysseus.planning.roadmap_lens_summary.v1",
            "available": False,
            "projection": "structured_read_evidence",
            "reason": "roadmap_not_planruntime_compatible",
            "evidence_ref": source_ref,
            "evidence_hash": source_hash,
            "slice_count": len(slices),
            "gate_count": len(gates),
            "dependency_count": sum(len(item.get("dependencies") or ()) for item in slices),
            "nodes": [],
            "edges": [],
            "aggregates": [],
            "claimable_node_ids": [],
            "clipped": False,
            "incomplete": True,
            "source_of_truth": False,
            "raw_content_included": False,
        }


def _document_sections(
    summary: str,
    tasks: list[dict[str, Any]],
    gates: list[dict[str, Any]],
    source_refs: list[str],
) -> list[dict[str, Any]]:
    return [
        {"id": "summary", "title": "Summary", "kind": "summary", "content": summary},
        {"id": "tasks", "title": "Tasks", "kind": "tasks", "items": deepcopy(tasks)},
        {"id": "gates", "title": "Gates", "kind": "gates", "items": deepcopy(gates)},
        {"id": "sources", "title": "Sources", "kind": "source_refs", "items": list(source_refs)},
        {"id": "data", "title": "Data", "kind": "canonical_json", "canonical_ref": "canonical"},
    ]


def _document_edit_envelope(
    *,
    document: Mapping[str, Any],
    section: str,
    section_id: str,
    task_id: str,
    reason: str,
    edit_hash_material: Mapping[str, Any],
    status: str,
    operations: list[dict[str, Any]] | None = None,
    conflicts: list[dict[str, str]] | None = None,
    warnings: list[dict[str, str]] | None = None,
    validation: Mapping[str, Any] | None = None,
    patch: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    edit_id = "roadmap-edit-" + _payload_hash(edit_hash_material).split(":", 1)[-1][:20]
    safe_operations = list(operations or [])[:24]
    safe_validation = dict(validation or {
        "schema": "odysseus.planning.roadmap_validation.v1",
        "valid": False,
        "errors": [],
        "warnings": [],
        "summary": {"errors": 0, "warnings": 0},
        "read_only": True,
        "writes_performed": False,
    })
    return {
        "schema": "odysseus.planning.roadmap_document_edit_proposal.v1",
        "draft_id": edit_id,
        "patch_id": str((patch or {}).get("patch_id") or edit_id),
        "status": status,
        "ready_for_apply": status == "ready" and bool((patch or {}).get("ready_for_apply", True)),
        "dry_run": True,
        "writes_performed": False,
        "events_emitted": False,
        "notifications_emitted": False,
        "apply_supported": False,
        "required_apply_gate": "PLANNING-APPLY-GO",
        "target": {
            "project_id": document.get("project_id") or "",
            "roadmap_id": document.get("roadmap_id") or "",
            "source_id": document.get("source_id") or "",
            "source_ref": document.get("source_ref") or "",
        },
        "base": {
            "source_hash": document.get("source_hash") or "",
            "revision": document.get("revision"),
            "projection_hash": (document.get("canonical") or {}).get("projection_hash") or "",
        },
        "section": section,
        "section_id": section_id,
        "task_id": task_id,
        "reason": _safe_text(reason, max_chars=300),
        "operations": safe_operations,
        "diff": {"operation_count": len(safe_operations), "operations": safe_operations},
        "validation": safe_validation,
        "conflicts": list(conflicts or []),
        "warnings": list(warnings or []),
        "patch": dict(patch or {}),
        "rollback": {"available": False, "reason": "no_write_performed"},
        "raw_content_included": False,
        "absolute_paths_visible": False,
    }


def _document_operation(path: str, before: Any, after: Any) -> dict[str, Any]:
    return {
        "op": "replace" if before is not None else "add",
        "path": path,
        "before": _value_summary(before),
        "after": _value_summary(after),
    }


def _document_task_updates(value: Any) -> dict[str, str]:
    if isinstance(value, str):
        updates: Mapping[str, Any] = {"objective": value}
    elif isinstance(value, Mapping):
        updates = value
    else:
        raise PlanningServiceError("invalid_document_value", "Task edit value must be text or an object")
    allowed = {"title", "objective", "status", "class", "owner", "done_when"}
    if not updates or set(updates) - allowed:
        raise PlanningServiceError("invalid_document_value", "Task edit contains unsupported fields")
    budgets = {"title": 200, "objective": 1_000, "status": 80, "class": 80, "owner": 80, "done_when": 600}
    normalized: dict[str, str] = {}
    for field, raw in updates.items():
        if not isinstance(raw, str) or not raw.strip() or len(raw) > budgets[field]:
            raise PlanningServiceError("invalid_document_value", "Task edit contains an empty or oversized field")
        normalized[field] = raw.strip()
    return normalized


def _document_data_candidate(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        if len(value.encode("utf-8")) > 262_144:
            raise PlanningServiceError("document_data_too_large", "Data edit exceeds the candidate budget")
        try:
            candidate = json.loads(value)
        except json.JSONDecodeError as exc:
            raise PlanningServiceError("invalid_document_data", "Data edit is not valid JSON") from exc
    elif isinstance(value, Mapping):
        candidate = deepcopy(dict(value))
    else:
        raise PlanningServiceError("invalid_document_data", "Data edit requires a JSON object")
    if not isinstance(candidate, dict):
        raise PlanningServiceError("invalid_document_data", "Data edit requires a JSON object")
    allowed = {
        "schema_version", "kind", "project_id", "roadmap_id", "title", "goal", "summary",
        "status", "revision", "created_at", "updated_at", "source_refs", "slices", "gates",
        "gate_refs", "dependency_refs", "verification", "stop_rules",
    }
    if set(candidate) - allowed:
        raise PlanningServiceError("invalid_document_data", "Data edit contains fields outside the canonical projection")
    _assert_no_forbidden_content(candidate)
    return candidate


def _is_planning_roadmap_kind(value: Any) -> bool:
    return str(value or "").strip() in ROADMAP_KIND_ALIASES


def _validate_document_candidate_identity(candidate: Mapping[str, Any], current: Mapping[str, Any]) -> None:
    immutable = {
        "schema_version", "kind", "project_id", "roadmap_id", "revision",
        "created_at", "updated_at", "gate_refs", "dependency_refs",
    }
    required = {"schema_version", "kind", "project_id", "roadmap_id", "revision", "title", "goal", "slices"}
    if not required.issubset(candidate):
        raise PlanningServiceError("invalid_document_data", "Data edit omits required canonical fields")
    for field in immutable:
        if candidate.get(field) != current.get(field):
            raise PlanningServiceError("document_data_identity_mismatch", "Data edit changes immutable identity or revision fields")


def _json_pointer_token(value: str) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _fit_document_budget(payload: dict[str, Any], budget: int) -> dict[str, Any]:
    result = deepcopy(payload)
    result["payload_bytes"] = 0
    while _json_size(result) > budget:
        trimmed = False
        preview = result.get("canonical", {}).get("json_preview", "")
        if len(preview) > 512:
            result["canonical"]["json_preview"] = preview[: max(512, len(preview) // 2)]
            result["canonical"]["json_preview_chars"] = len(result["canonical"]["json_preview"])
            result["canonical"]["truncated"] = True
            trimmed = True
        else:
            for key in ("memory_refs", "tasks", "gates", "source_refs"):
                values = result.get(key)
                if isinstance(values, list) and values:
                    values.pop()
                    trimmed = True
                    break
        if not trimmed:
            break
        result["slices"] = deepcopy(result.get("tasks") or [])
        projection = result.get("canonical", {}).get("projection")
        if isinstance(projection, dict):
            projection["slices"] = deepcopy(result.get("tasks") or [])
            projection["gates"] = deepcopy(result.get("gates") or [])
            projection["source_refs"] = list(result.get("source_refs") or [])
            _refresh_document_canonical(result["canonical"])
        result["readable_sections"] = _document_sections(
            str(result.get("summary") or ""),
            result.get("tasks") or [],
            result.get("gates") or [],
            result.get("source_refs") or [],
        )
        result["truncated"] = True
        result["incomplete"] = True
    result["payload_bytes"] = _json_size(result)
    result["payload_bytes"] = _json_size(result)
    return result


def _fit_section_context_budget(payload: dict[str, Any], budget: int) -> dict[str, Any]:
    result = deepcopy(payload)
    result["payload_bytes"] = 0
    while _json_size(result) > budget:
        trimmed = False
        content = result.get("content") if isinstance(result.get("content"), dict) else {}
        items = content.get("items")
        if isinstance(items, list) and items:
            items.pop()
            result["selection"]["returned_items"] = len(items)
            trimmed = True
        elif content.get("kind") == "canonical_projection":
            projection = content.get("projection") if isinstance(content.get("projection"), dict) else {}
            for key in ("task_refs", "gate_refs", "source_refs", "dependency_refs"):
                values = projection.get(key)
                if isinstance(values, list) and values:
                    values.pop()
                    trimmed = True
                    break
        if not trimmed:
            for key in ("memory_refs", "source_refs", "lens_evidence_refs"):
                values = result.get(key)
                if isinstance(values, list) and values:
                    values.pop()
                    trimmed = True
                    break
        if not trimmed and len(str(result.get("summary") or "")) > 120:
            result["summary"] = _safe_text(result.get("summary") or "", max_chars=120)
            trimmed = True
        if not trimmed:
            raise PlanningServiceError("section_context_budget_exceeded", "Roadmap section context exceeds its hard budget")
        result["truncated"] = True
        result["incomplete"] = True
    for _ in range(3):
        result["payload_bytes"] = _json_size(result)
    return result


def _memory_bridge_existing_records(
    values: Iterable[Mapping[str, Any]] | Mapping[str, Any],
    *,
    project_id: str,
    roadmap_id: str,
    source_id: str,
) -> list[dict[str, Any]]:
    raw_values: Any = values.get("entries") if isinstance(values, Mapping) and "entries" in values else values
    if isinstance(raw_values, Mapping):
        iterator: Iterable[Any] = (raw_values,)
    elif isinstance(raw_values, (str, bytes)) or raw_values is None:
        if raw_values in (None, "", b""):
            iterator = ()
        else:
            raise PlanningServiceError("invalid_existing_memory_records", "Existing Planning memory records must be objects")
    else:
        try:
            iterator = iter(raw_values)
        except TypeError as exc:
            raise PlanningServiceError("invalid_existing_memory_records", "Existing Planning memory records must be iterable") from exc

    target_ref = f"planning:{project_id}:{roadmap_id}"
    projected: list[dict[str, Any]] = []
    for index, raw in enumerate(iterator):
        if index >= 100:
            raise PlanningServiceError("existing_memory_budget_exceeded", "Existing Planning memory records exceed the item budget")
        if not isinstance(raw, Mapping):
            raise PlanningServiceError("invalid_existing_memory_records", "Existing Planning memory record is invalid")
        _assert_no_forbidden_content(raw)
        memory_ref = str(raw.get("memory_ref") or "")
        if memory_ref != target_ref:
            continue
        if (
            str(raw.get("project_id") or "") != project_id
            or str(raw.get("roadmap_id") or "") != roadmap_id
            or str(raw.get("source_id") or "") != source_id
        ):
            raise PlanningServiceError("existing_memory_identity_conflict", "Existing Planning memory identity is inconsistent")
        source_hash = str(raw.get("source_hash") or "").lower()
        content_hash = str(raw.get("content_hash") or "").lower()
        source_revision = str(raw.get("source_revision") or "")
        revision = raw.get("revision")
        source_revision_ref = str(raw.get("source_revision_ref") or "")
        if (
            not re.fullmatch(r"sha256:[a-f0-9]{64}", source_hash)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", content_hash)
            or not isinstance(revision, int)
            or isinstance(revision, bool)
            or revision < 1
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}", source_revision)
            or source_revision_ref != f"{source_id}@{source_revision}"
        ):
            raise PlanningServiceError("invalid_existing_memory_records", "Existing Planning memory evidence is invalid")
        if raw.get("derived") is not True or raw.get("rebuildable") is not True or raw.get("source_of_truth") is not False:
            raise PlanningServiceError("invalid_existing_memory_records", "Existing Planning memory record is not derived")
        source_status = str(raw.get("source_status") or "current").lower()
        if source_status not in {"active", "current", "available", "deleted"}:
            raise PlanningServiceError("invalid_existing_memory_records", "Existing Planning memory status is invalid")
        projected.append({
            "source": "planning_source",
            "derived": True,
            "rebuildable": True,
            "source_of_truth": False,
            "memory_ref": target_ref,
            "project_id": project_id,
            "roadmap_id": roadmap_id,
            "source_id": source_id,
            "source_status": source_status,
            "revision": revision,
            "source_revision": source_revision,
            "source_revision_ref": source_revision_ref,
            "source_hash": source_hash,
            "content_hash": content_hash,
        })
    return projected


def _refresh_document_canonical(canonical: dict[str, Any]) -> None:
    projection = canonical.get("projection") if isinstance(canonical.get("projection"), Mapping) else {}
    current_cap = len(str(canonical.get("json_preview") or ""))
    configured_cap = int(canonical.get("json_preview_budget_chars") or 0)
    cap = min(configured_cap, current_cap) if current_cap else 0
    serialized = json.dumps(projection, ensure_ascii=False, sort_keys=True, indent=2)
    canonical["projection_hash"] = _payload_hash(projection)
    canonical["json_preview"] = _safe_text(serialized, max_chars=cap, preserve_whitespace=True) if cap else ""
    canonical["json_preview_chars"] = len(canonical["json_preview"])
    canonical["truncated"] = len(serialized) > canonical["json_preview_chars"]


def _strict_document_id(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not _ID_RE.fullmatch(text):
        raise PlanningServiceError("invalid_document_id", f"Roadmap document {field} is invalid")
    return text


def _strict_definition_id(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", text):
        raise PlanningServiceError("invalid_definition_id", f"Planning definition {field} is invalid")
    return text


def _strict_optional_definition_id(value: Any, *, field: str) -> str:
    if value in {None, ""}:
        return ""
    return _strict_definition_id(value, field=field)


def _definition_revision_selector(value: Any) -> str | int:
    if value == "latest_approved":
        return "latest_approved"
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PlanningServiceError(
            "invalid_revision",
            "Planning revision must be latest_approved or a positive integer",
        )
    return value


def _definition_event_timestamp(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) > 40 or not text:
        raise PlanningServiceError(
            "invalid_planning_event_timestamp",
            "Planning event timestamp is invalid",
        )
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PlanningServiceError(
            "invalid_planning_event_timestamp",
            "Planning event timestamp is invalid",
        ) from exc
    if parsed.tzinfo is None:
        raise PlanningServiceError(
            "invalid_planning_event_timestamp",
            "Planning event timestamp must include a timezone",
        )
    return text


def _definition_gate_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    _assert_no_runtime_fields(value)
    required = {
        "gate_id",
        "kind",
        "title",
        "blocks",
        "decision_needed",
        "safe_default",
        "approval_scope_schema",
        "required_verification_rule_ids",
    }
    if set(value) != required:
        raise PlanningServiceError(
            "invalid_gate_definition",
            "Planning gate definition fields do not match Definition v2",
        )
    gate_id = _strict_definition_id(value["gate_id"], field="gate_id")
    blocks = [
        _strict_definition_id(item, field="gate block")
        for item in value["blocks"]
    ] if isinstance(value["blocks"], list) else None
    rule_ids = [
        _strict_definition_id(item, field="verification rule")
        for item in value["required_verification_rule_ids"]
    ] if isinstance(value["required_verification_rule_ids"], list) else None
    if blocks is None or rule_ids is None:
        raise PlanningServiceError("invalid_gate_definition", "Planning gate references must be arrays")
    return {
        "gate_id": gate_id,
        "kind": _safe_token(value["kind"], fallback="gate", max_chars=80),
        "title": _safe_text(value["title"], max_chars=200),
        "blocks": blocks,
        "decision_needed": _safe_text(value["decision_needed"], max_chars=4_000),
        "safe_default": _safe_text(value["safe_default"], max_chars=4_000),
        "approval_scope_schema": deepcopy(dict(value["approval_scope_schema"])),
        "required_verification_rule_ids": rule_ids,
    }


def _assert_no_runtime_fields(value: Any, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key)
            normalized = key_text.strip().lower()
            if normalized in RUNTIME_FIELD_DENYLIST or normalized in GATE_RUNTIME_FIELD_DENYLIST:
                raise PlanningServiceError(
                    "runtime_field_forbidden",
                    f"Planning definition boundary rejects field at {path}.{key_text}",
                )
            _assert_no_runtime_fields(nested, path=f"{path}.{key_text}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _assert_no_runtime_fields(nested, path=f"{path}[{index}]")


def _strict_budget(value: Any, *, field: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum or value > maximum:
        raise PlanningServiceError("invalid_document_budget", f"Roadmap document {field} is outside the budget")
    return value


def _validate_canonical_roadmap(payload: Mapping[str, Any], errors: list[dict[str, str]]) -> None:
    required = (
        "project_id", "roadmap_id", "revision", "created_at", "updated_at", "status",
        "source_refs", "slices", "gate_refs", "dependency_refs", "verification",
    )
    for field in required:
        if field not in payload:
            errors.append(_issue("missing_canonical_field", f"$.{field}", f"Canonical roadmap requires {field}"))
    for field in ("project_id", "roadmap_id"):
        if field in payload and not _valid_id(payload.get(field)):
            errors.append(_issue("invalid_id", f"$.{field}", f"Canonical {field} is invalid"))
    revision = payload.get("revision")
    if revision is not None and (not isinstance(revision, int) or isinstance(revision, bool) or revision < 1):
        errors.append(_issue("invalid_revision", "$.revision", "Canonical roadmap revision must be a positive integer"))
    if "verification" in payload and not isinstance(payload.get("verification"), list):
        errors.append(_issue("invalid_verification", "$.verification", "Canonical roadmap verification must be an array"))


def _is_safe_public_ref(value: Any) -> bool:
    text = str(value or "").strip()
    if not text or "\x00" in text or _URI_RE.match(text):
        return False
    if re.match(r"^(?:project|roadmap|gate|slice|todo|repo-plan):[A-Za-z0-9._/-]+$", text):
        return True
    normalized = unicodedata.normalize("NFKC", unquote(text)).replace("\\", "/")
    if normalized.startswith("/") or ntpath.splitdrive(normalized)[0]:
        return False
    parts = normalized.split("/")
    return bool(parts) and all(part not in {"", ".", ".."} for part in parts)


def _safe_ref_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [
        _safe_text(item, max_chars=240)
        for item in value
        if _is_safe_public_ref(item)
    ][:limit]


def _safe_scalar_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [
        _safe_text(item, max_chars=180)
        for item in value
        if not isinstance(item, (Mapping, list, tuple, set)) and str(item or "").strip()
    ][:limit]


def _dedupe(values: Iterable[str], *, limit: int) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
        if len(result) >= limit:
            break
    return result


def _safe_text(value: Any, *, max_chars: int, preserve_whitespace: bool = False) -> str:
    if max_chars <= 0:
        return ""
    text = str(value or "")
    if not preserve_whitespace:
        text = " ".join(text.split())
    for pattern in _SECRET_PATTERNS + _PRIVATE_PATH_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text[:max_chars]


def _safe_token(value: Any, *, fallback: str, max_chars: int) -> str:
    text = re.sub(r"[^A-Za-z0-9_.:/-]+", "_", str(value or fallback)).strip("._-")
    return (text or fallback)[:max_chars]


def _valid_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if _ID_RE.fullmatch(text) else ""


def _issue(code: str, field: str, message: str) -> dict[str, str]:
    return {
        "code": _safe_token(code, fallback="validation_error", max_chars=80),
        "field": _safe_text(field, max_chars=120),
        "message": _safe_text(message, max_chars=180),
    }


def _contains_sensitive_value(serialized: str) -> bool:
    return any(pattern.search(serialized) for pattern in _SECRET_PATTERNS + _PRIVATE_PATH_PATTERNS)


def _clamp_int(value: Any, *, minimum: int, maximum: int, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _assert_no_forbidden_content(value: Any) -> None:
    try:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError) as exc:
        raise PlanningServiceError("invalid_structured_input", "Planning input is not JSON serializable") from exc
    if len(serialized.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise PlanningServiceError("input_budget_exceeded", "Planning input exceeds the structured payload budget")
    if _contains_sensitive_value(serialized):
        raise PlanningServiceError("forbidden_content", "Planning input contains credential-like or private-path content")


def _require_safe_refs(value: Any, *, field: str, limit: int) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise PlanningServiceError("invalid_reference_list", f"Planning {field} must be an array")
    if len(value) > limit:
        raise PlanningServiceError("reference_budget_exceeded", f"Planning {field} exceeds the item budget")
    refs: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if not _is_safe_public_ref(text) or any(char in text for char in "*?[]"):
            raise PlanningServiceError("unsafe_reference", f"Planning {field} contains an unsafe reference")
        refs.append(_safe_text(text, max_chars=240))
    return _dedupe(refs, limit=limit)


def _require_scope_refs(value: Any, *, field: str, limit: int) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise PlanningServiceError("invalid_scope_list", f"Planning {field} must be an array")
    if len(value) > limit:
        raise PlanningServiceError("scope_budget_exceeded", f"Planning {field} exceeds the item budget")
    refs: list[str] = []
    for item in value:
        text = str(item or "").strip().replace("\\", "/")
        wildcard = text.endswith("/*") and text.count("*") == 1 and not any(char in text for char in "?[]")
        candidate = text[:-2] if wildcard else text
        if not _is_safe_public_ref(candidate):
            raise PlanningServiceError("unsafe_scope", f"Planning {field} contains an unsafe repository scope")
        refs.append(_safe_text(text, max_chars=240))
    return _dedupe(refs, limit=limit)


def _require_scalar_list(
    value: Any,
    *,
    field: str,
    limit: int,
    required: bool = False,
) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise PlanningServiceError("invalid_scalar_list", f"Planning {field} must be an array")
    if required and not value:
        raise PlanningServiceError("required_list_empty", f"Planning {field} must not be empty")
    if len(value) > limit:
        raise PlanningServiceError("scalar_budget_exceeded", f"Planning {field} exceeds the item budget")
    result: list[str] = []
    for item in value:
        if isinstance(item, (Mapping, list, tuple, set)):
            raise PlanningServiceError("invalid_scalar_item", f"Planning {field} entries must be scalar text")
        text = str(item or "").strip()
        if not text or len(text) > 600:
            raise PlanningServiceError("invalid_scalar_item", f"Planning {field} contains an empty or oversized entry")
        result.append(_safe_text(text, max_chars=600))
    return result


def _normalize_draft_slices(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)) or not value:
        raise PlanningServiceError("invalid_draft_slices", "Roadmap draft requires a non-empty slices array")
    if len(value) > MAX_DRAFT_SLICES:
        raise PlanningServiceError("draft_slice_budget_exceeded", "Roadmap draft exceeds the slice budget")
    allowed = {
        "id", "title", "objective", "class", "owner", "status", "depends_on",
        "source_refs", "allowed_paths", "tests", "done_when",
    }
    seen: set[str] = set()
    slices: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) - allowed:
            raise PlanningServiceError("invalid_draft_slice", "Roadmap draft slice contains unsupported structure")
        item_id = str(raw.get("id") or "").strip()
        if not item_id or len(item_id) > 120 or _safe_token(item_id, fallback="", max_chars=120) != item_id:
            raise PlanningServiceError("invalid_draft_slice_id", "Roadmap draft slice id is invalid")
        if item_id in seen:
            raise PlanningServiceError("duplicate_draft_slice_id", "Roadmap draft slice ids must be unique")
        seen.add(item_id)
        objective = str(raw.get("objective") or "").strip()
        if not objective or len(objective) > 1_000:
            raise PlanningServiceError("invalid_draft_slice_objective", "Roadmap draft slice objective is required and bounded")
        slices.append({
            "id": item_id,
            "title": _safe_text(raw.get("title") or item_id, max_chars=200),
            "objective": _safe_text(objective, max_chars=1_000),
            "class": _safe_token(raw.get("class"), fallback="repo_only", max_chars=80),
            "owner": _safe_token(raw.get("owner"), fallback="Bob", max_chars=80),
            "status": _safe_token(raw.get("status"), fallback="planned", max_chars=80),
            "depends_on": _require_scalar_list(raw.get("depends_on", []), field="slice depends_on", limit=24),
            "source_refs": _require_safe_refs(raw.get("source_refs", []), field="slice source_refs", limit=24),
            "allowed_paths": _require_scope_refs(raw.get("allowed_paths", []), field="slice allowed_paths", limit=24),
            "tests": _require_scalar_list(raw.get("tests", []), field="slice tests", limit=24),
            "done_when": _safe_text(raw.get("done_when") or "", max_chars=600),
        })
    return slices


def _normalize_draft_gates(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        raise PlanningServiceError("invalid_draft_gates", "Roadmap draft gates must be an array")
    if len(value) > MAX_DRAFT_GATES:
        raise PlanningServiceError("draft_gate_budget_exceeded", "Roadmap draft exceeds the gate budget")
    allowed = {
        "id", "class", "status", "decision_needed", "blocks",
        "safe_preparation_done", "risk_if_bypassed",
    }
    seen: set[str] = set()
    gates: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) - allowed:
            raise PlanningServiceError("invalid_draft_gate", "Roadmap draft gate contains unsupported structure")
        gate_id = str(raw.get("id") or "").strip()
        if not gate_id or len(gate_id) > 120 or _safe_token(gate_id, fallback="", max_chars=120) != gate_id:
            raise PlanningServiceError("invalid_draft_gate_id", "Roadmap draft gate id is invalid")
        if gate_id in seen:
            raise PlanningServiceError("duplicate_draft_gate_id", "Roadmap draft gate ids must be unique")
        seen.add(gate_id)
        gates.append({
            "id": gate_id,
            "class": _safe_token(raw.get("class"), fallback="repo_only", max_chars=80),
            "status": _safe_token(raw.get("status"), fallback="open", max_chars=80),
            "decision_needed": _safe_text(raw.get("decision_needed") or "", max_chars=500),
            "blocks": _require_scalar_list(raw.get("blocks", []), field="gate blocks", limit=24),
            "safe_preparation_done": _safe_text(raw.get("safe_preparation_done") or "", max_chars=500),
            "risk_if_bypassed": _safe_text(raw.get("risk_if_bypassed") or "", max_chars=500),
        })
    return gates


def _validate_patch_change_budgets(changes: Mapping[str, Any]) -> None:
    _assert_no_forbidden_content(changes)
    scalar_budgets = {"title": 200, "goal": 1_200, "summary": 1_200, "status": 80}
    for field, limit in scalar_budgets.items():
        if field in changes:
            value = changes[field]
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise PlanningServiceError("invalid_patch_value", f"Patch field {field} is empty or exceeds its budget")
    if "source_refs" in changes:
        _require_safe_refs(changes["source_refs"], field="source_refs", limit=50)
    for field in ("slices", "slice_queue"):
        if field in changes and (not isinstance(changes[field], list) or len(changes[field]) > 200):
            raise PlanningServiceError("invalid_patch_slices", "Patch slices must be a bounded array")
    if "gates" in changes and (not isinstance(changes["gates"], list) or len(changes["gates"]) > 100):
        raise PlanningServiceError("invalid_patch_gates", "Patch gates must be a bounded array")
    if "stop_rules" in changes:
        _require_scalar_list(changes["stop_rules"], field="stop_rules", limit=100)
    if "verification" in changes:
        verification = changes["verification"]
        if isinstance(verification, list):
            _require_scalar_list(verification, field="verification", limit=100)
        elif not isinstance(verification, Mapping):
            raise PlanningServiceError("invalid_patch_verification", "Patch verification must be an array or structured object")


def _value_summary(value: Any) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "type": type(value).__name__,
        "hash": _payload_hash(value),
    }
    if isinstance(value, Mapping):
        summary["count"] = len(value)
        summary["keys"] = sorted(_safe_token(key, fallback="field", max_chars=60) for key in value)[:12]
    elif isinstance(value, (list, tuple)):
        summary["count"] = len(value)
    elif value is not None:
        summary["preview"] = _safe_text(value, max_chars=180)
    return summary


def _payload_hash(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "go"}


__all__ = [
    "PlanningMcpService",
    "PlanningServiceError",
    "planning_create_roadmap_draft",
    "planning_get_context_pack",
    "planning_list_roadmaps",
    "planning_propose_patch",
    "planning_read_roadmap",
    "planning_search_roadmaps",
    "planning_validate_roadmap",
]
