"""MCP-First Dispatcher: End-to-End Tool Dispatch for Harbour Bounded Contexts.

Anchors headless execution for planning, memory, gateway, and inbox tools.
Preserves strict import barriers: domain packages are imported lazily within
their respective dispatch handlers to protect isolated processes from forbidden imports.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

MAX_INBOX_DOCUMENT_BYTES = 256 * 1024


class HarbourMcpDispatchError(Exception):
    """Raised when an MCP-first tool dispatch fails at policy, parameter, or execution boundary."""

    __slots__ = ("code", "detail")

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def build_harbour_mcp_tool_contracts(scope: str | None = None) -> tuple[dict[str, Any], ...]:
    """Build standardized MCP tool contracts for Harbour tools, optionally filtered by scope."""
    from src.harbour.mcp import HARBOUR_MCP_FIRST_TOOLS

    contracts = []
    for tool_name, tool_meta in HARBOUR_MCP_FIRST_TOOLS.items():
        if scope is not None and tool_meta.get("scope") != scope:
            continue
        contracts.append({
            "name": tool_meta["name"],
            "description": tool_meta["description"],
            "inputSchema": dict(tool_meta["input_schema"]),
        })
    return tuple(contracts)


class HarbourMcpDispatcher:
    """Dispatches headless MCP-First tool calls across the 4 Harbour bounded contexts."""

    __slots__ = ("repo_root", "_gateway_coordinator")

    def __init__(
        self,
        repo_root: str | Path | None = None,
        gateway_coordinator: Any = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve() if repo_root else Path.cwd().resolve()
        self._gateway_coordinator = gateway_coordinator

    def dispatch(
        self,
        name: str,
        arguments: Mapping[str, Any],
        identity: Any = None,
    ) -> dict[str, Any]:
        """Dispatch a tool call to the appropriate domain handler with scope and parameter checks."""
        from src.harbour.mcp import HARBOUR_MCP_FIRST_TOOLS

        if name not in HARBOUR_MCP_FIRST_TOOLS:
            raise HarbourMcpDispatchError("invalid_params", f"Unknown Harbour MCP tool: {name}")

        tool_meta = HARBOUR_MCP_FIRST_TOOLS[name]
        namespace = tool_meta.get("namespace")
        tool_scope = tool_meta.get("scope")

        # Validate caller scope when identity is provided
        caller_scope = getattr(identity, "scope", None)
        if caller_scope is not None:
            allowed_scopes = self._allowed_scopes_for_tool(namespace, tool_scope)
            if caller_scope not in allowed_scopes:
                raise HarbourMcpDispatchError(
                    "unauthorized",
                    f"Caller scope '{caller_scope}' is not authorized for tool '{name}'",
                )

        if namespace == "harbour_planning":
            return self._dispatch_planning(name, arguments, identity)
        if namespace == "harbour_memory":
            return self._dispatch_memory(name, arguments, identity)
        if namespace == "harbour_gateway":
            return self._dispatch_gateway(name, arguments, identity)
        if namespace == "harbour_inbox":
            return self._dispatch_inbox(name, arguments, identity)

        raise HarbourMcpDispatchError("invalid_params", f"Unsupported namespace: {namespace}")

    @staticmethod
    def _allowed_scopes_for_tool(namespace: str | None, tool_scope: str | None) -> tuple[str, ...]:
        """Determine permitted caller scopes for a given namespace and tool scope."""
        if namespace == "harbour_planning":
            return ("mcp:planning:read",)
        if namespace == "harbour_memory":
            return ("mcp:harbour:context:read", "mcp:planning:read")
        if namespace == "harbour_gateway":
            return ("mcp:harbour:gateway", "mcp:planning:read")
        if namespace == "harbour_inbox":
            return ("mcp:harbour:inbox",)
        if tool_scope:
            return (tool_scope,)
        return ()

    def _dispatch_planning(
        self,
        name: str,
        arguments: Mapping[str, Any],
        identity: Any,
    ) -> dict[str, Any]:
        """Lazy-loaded dispatch for harbour_planning_* tools."""
        from src.planning_roadmap_migration import build_h1r03_structural_census

        definitions_dir = self.repo_root / "data" / "planning" / "definitions"
        if name == "harbour_planning_get_projection":
            revision_id = arguments.get("revision_id")
            if revision_id is not None and not isinstance(revision_id, str):
                raise HarbourMcpDispatchError("invalid_params", "revision_id must be a string")

            if definitions_dir.is_dir():
                try:
                    from src.harbour.core import PlanningRevisionStore
                    store = PlanningRevisionStore.from_directory(definitions_dir)
                    projects = store.list_projects("admin")
                    return {
                        "schema": "odysseus.harbour.planning_projection.v1",
                        "revision_id": revision_id or "definitions-head",
                        "source": "planning_definitions_store",
                        "projects": projects,
                    }
                except Exception:
                    pass

            census, supporting_observation = build_h1r03_structural_census(self.repo_root)
            heads = [r for r in census if r.get("structural_role") == "primary_import_candidate"]
            return {
                "schema": "odysseus.harbour.planning_projection.v1",
                "revision_id": revision_id or "h1r03-census",
                "source": "h1r03_structural_census",
                "total_sources": len(census),
                "supporting_observation": supporting_observation,
                "heads": heads,
                "census_sample": census[:50],
            }

        if name == "harbour_planning_lookup_revision":
            revision_id = arguments.get("revision_id")
            if not isinstance(revision_id, str) or not revision_id.strip():
                raise HarbourMcpDispatchError("invalid_params", "revision_id is required")
            revision_id = revision_id.strip()

            census, _ = build_h1r03_structural_census(self.repo_root)
            match = None
            for r in census:
                if (
                    r.get("source_id") == revision_id
                    or r.get("path") == revision_id
                    or revision_id in r.get("source_id", "")
                ):
                    match = r
                    break
            return {
                "schema": "odysseus.harbour.planning_revision_lookup.v1",
                "revision_id": revision_id,
                "found": match is not None,
                "record": match,
            }

        if name == "harbour_planning_list_heads":
            census, _ = build_h1r03_structural_census(self.repo_root)
            heads = [r for r in census if r.get("structural_role") == "primary_import_candidate"]
            return {
                "schema": "odysseus.harbour.planning_heads.v1",
                "heads": heads,
                "total_heads": len(heads),
            }

        raise HarbourMcpDispatchError("invalid_params", f"Unknown planning tool: {name}")

    def _dispatch_memory(
        self,
        name: str,
        arguments: Mapping[str, Any],
        identity: Any,
    ) -> dict[str, Any]:
        """Lazy-loaded dispatch for harbour_memory_* tools."""
        if name == "harbour_memory_query_usi":
            query = arguments.get("query")
            if not isinstance(query, str) or not query.strip():
                raise HarbourMcpDispatchError("invalid_params", "query must be a non-empty string")
            limit = arguments.get("limit", 10)
            if not isinstance(limit, int) or limit < 1 or limit > 100:
                raise HarbourMcpDispatchError("invalid_params", "limit must be between 1 and 100")

            from src.planning_roadmap_migration import build_h1r03_structural_census

            census, _ = build_h1r03_structural_census(self.repo_root)
            q = query.strip().lower()
            matches = []
            for r in census:
                if (
                    q in r.get("path", "").lower()
                    or q in r.get("source_id", "").lower()
                    or q in r.get("kind", "").lower()
                ):
                    matches.append({
                        "source_id": r.get("source_id"),
                        "path": r.get("path"),
                        "kind": r.get("kind"),
                        "structural_role": r.get("structural_role"),
                    })
                    if len(matches) >= limit:
                        break
            return {
                "schema": "odysseus.harbour.memory_query_result.v1",
                "query": query.strip(),
                "limit": limit,
                "total_matches": len(matches),
                "matches": matches,
            }

        if name == "harbour_memory_get_context_manifest":
            session_id = arguments.get("session_id")
            if session_id is not None and not isinstance(session_id, str):
                raise HarbourMcpDispatchError("invalid_params", "session_id must be a string")
            return {
                "schema": "odysseus.harbour.context_manifest.v1",
                "manifest_version": "1.0.0",
                "session_id": session_id or "default-harbour-session",
                "memory_domain": {
                    "usi_status": "ready",
                    "active_sources": ["planning", "specs", "docs"],
                },
                "planning_domain": {
                    "status": "synchronized",
                    "scope": "mcp:harbour:context:read",
                },
                "bounded": True,
            }

        if name == "harbour_memory_runtime_status":
            from src.unified_source_index_runtime import build_knowledge_runtime
            from src.unified_source_index_runtime_config import UnifiedSourceIndexRuntimeConfig
            from src.unified_source_index_job_runner import build_inert_cooperative_index_job_pump
            from src.unified_source_index_runtime_status import project_unified_source_index_runtime_status

            config = UnifiedSourceIndexRuntimeConfig.from_environment()
            kr = build_knowledge_runtime((config.mode.value, config.runtime_enabled))
            pump = build_inert_cooperative_index_job_pump(config)
            return project_unified_source_index_runtime_status(kr, pump)

        raise HarbourMcpDispatchError("invalid_params", f"Unknown memory tool: {name}")

    def _dispatch_gateway(
        self,
        name: str,
        arguments: Mapping[str, Any],
        identity: Any,
    ) -> dict[str, Any]:
        """Lazy-loaded dispatch for harbour_gateway_* tools."""
        from src.harbour.gateway import (
            WORKER_EXECUTION_POLICY,
            HarbourWorkerGatewayCoordinator,
            WorkerGatewayIsolationError,
            WorkerGatewayLeaseError,
        )

        if self._gateway_coordinator is None:
            self._gateway_coordinator = HarbourWorkerGatewayCoordinator(workspace_base=self.repo_root)
        coordinator: HarbourWorkerGatewayCoordinator = self._gateway_coordinator

        if name == "harbour_gateway_dispatch_task":
            task_plan = arguments.get("task_plan")
            if not isinstance(task_plan, dict):
                raise HarbourMcpDispatchError("invalid_params", "task_plan must be an object")

            # Validate against WORKER_EXECUTION_POLICY
            if task_plan.get("authorizes_mutation") is True:
                raise HarbourMcpDispatchError("policy_violation", "task_plan cannot authorize mutation directly")
            if task_plan.get("direct_main_process_execution_allowed") is True:
                raise HarbourMcpDispatchError("policy_violation", "direct main process execution is forbidden")

            isolation_mode = task_plan.get("isolation_mode", WORKER_EXECUTION_POLICY["isolation_mode"])
            if isolation_mode != WORKER_EXECUTION_POLICY["isolation_mode"]:
                raise HarbourMcpDispatchError(
                    "policy_violation",
                    f"isolation_mode must be '{WORKER_EXECUTION_POLICY['isolation_mode']}'",
                )

            lease_required = task_plan.get("lease_required", True)
            if not lease_required:
                raise HarbourMcpDispatchError("policy_violation", "lease_required must be True")

            if "worktree_path" in task_plan:
                try:
                    coordinator.validate_worktree_isolation(task_plan["worktree_path"])
                except WorkerGatewayIsolationError as exc:
                    raise HarbourMcpDispatchError("policy_violation", str(exc)) from exc

            owner_subject_id = getattr(identity, "owner_subject_id", None) or "subject:anonymous"
            try:
                ticket, _ = coordinator.plan_and_lease_task(
                    task_plan,
                    owner_principal_id=owner_subject_id,
                )
            except WorkerGatewayLeaseError as exc:
                raise HarbourMcpDispatchError("lease_error", str(exc)) from exc
            except WorkerGatewayIsolationError as exc:
                raise HarbourMcpDispatchError("policy_violation", str(exc)) from exc

            raw_sig = json.dumps(task_plan, sort_keys=True).encode("utf-8")
            task_id = str(task_plan.get("task_id") or f"task_{hashlib.sha256(raw_sig).hexdigest()[:12]}")

            return {
                "schema": "odysseus.harbour.gateway_dispatch_result.v1",
                "task_id": task_id,
                "lease_id": ticket.lease_id,
                "status": "dispatched",
                "isolation_mode": isolation_mode,
                "lease_required": True,
                "authorizes_mutation": False,
                "dispatched_at": datetime.now(timezone.utc).isoformat(),
            }

        if name == "harbour_gateway_get_runner_state":
            task_id = arguments.get("task_id")
            if not isinstance(task_id, str) or not task_id.strip():
                raise HarbourMcpDispatchError("invalid_params", "task_id is required")
            task_id = task_id.strip()

            state = coordinator.get_runner_state(task_id)
            if state is None:
                return {
                    "schema": "odysseus.harbour.runner_state.v1",
                    "task_id": task_id,
                    "status": "not_found",
                    "phase": "unknown",
                    "found": False,
                }
            return {
                "schema": "odysseus.harbour.runner_state.v1",
                "task_id": task_id,
                "status": state.phase,
                "phase": state.phase,
                "found": True,
                "progress_percent": state.progress_percent,
                "gates_waiting": list(state.gates_waiting),
                "blockers": list(state.blockers),
            }

        if name == "harbour_gateway_evaluate_quality_gate":
            task_id = arguments.get("task_id")
            if not isinstance(task_id, str) or not task_id.strip():
                raise HarbourMcpDispatchError("invalid_params", "task_id is required")
            task_id = task_id.strip()

            changed_paths = arguments.get("changed_paths") or ()
            check_results = arguments.get("check_results") or ()

            eval_res = coordinator.evaluate_task_quality_gate(
                task_id=task_id,
                changed_paths=changed_paths,
                check_results=check_results,
            )
            return {
                "schema": "odysseus.harbour.quality_gate_evaluation.v1",
                "task_id": task_id,
                "quality_gate": eval_res["quality_gate"],
            }

        raise HarbourMcpDispatchError("invalid_params", f"Unknown gateway tool: {name}")

    def _dispatch_inbox(
        self,
        name: str,
        arguments: Mapping[str, Any],
        identity: Any,
    ) -> dict[str, Any]:
        """Lazy-loaded dispatch for harbour_inbox_* tools."""
        owner_subject_id = getattr(identity, "owner_subject_id", None) or "subject:anonymous"

        if name == "harbour_inbox_list_items":
            filter_status = arguments.get("filter_status")
            if filter_status is not None and not isinstance(filter_status, str):
                raise HarbourMcpDispatchError("invalid_params", "filter_status must be a string")

            return {
                "schema": "odysseus.harbour.inbox_items_list.v1",
                "filter_status": filter_status or "all",
                "owner_subject_id": owner_subject_id,
                "items": [],
                "total_count": 0,
            }

        if name == "harbour_inbox_capture_document":
            filename = arguments.get("filename")
            if not isinstance(filename, str) or not filename.strip():
                raise HarbourMcpDispatchError("invalid_params", "filename is required")
            filename = filename.strip()

            # Prevent directory traversal
            if any(part in {"..", "/", "\\"} for part in Path(filename).parts) or ":" in filename:
                raise HarbourMcpDispatchError("invalid_params", "filename cannot contain traversal components")

            content_bytes = arguments.get("content_bytes")
            if not isinstance(content_bytes, str):
                raise HarbourMcpDispatchError("invalid_params", "content_bytes is required")

            raw_bytes = content_bytes.encode("utf-8")
            if len(raw_bytes) > MAX_INBOX_DOCUMENT_BYTES:
                raise HarbourMcpDispatchError(
                    "budget_exceeded",
                    f"content_bytes exceeds 256 KB budget (got {len(raw_bytes)} bytes)",
                )

            content_hash = hashlib.sha256(raw_bytes).hexdigest()
            doc_id = f"doc_{content_hash[:16]}"
            return {
                "schema": "odysseus.harbour.inbox_captured_document.v1",
                "document_id": doc_id,
                "filename": filename,
                "size_bytes": len(raw_bytes),
                "content_hash": f"sha256:{content_hash}",
                "owner_subject_id": owner_subject_id,
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "stamped": False,
            }

        if name == "harbour_inbox_stamp_document":
            document_id = arguments.get("document_id")
            if not isinstance(document_id, str) or not document_id.strip():
                raise HarbourMcpDispatchError("invalid_params", "document_id is required")
            document_id = document_id.strip()

            stamp_data = f"{document_id}:{owner_subject_id}".encode("utf-8")
            stamp_hash = hashlib.sha256(stamp_data).hexdigest()
            return {
                "schema": "odysseus.harbour.inbox_stamped_document.v1",
                "document_id": document_id,
                "stamped": True,
                "owner_subject_id": owner_subject_id,
                "stamp_hash": f"sha256:{stamp_hash}",
                "stamped_at": datetime.now(timezone.utc).isoformat(),
            }

        raise HarbourMcpDispatchError("invalid_params", f"Unknown inbox tool: {name}")


__all__ = [
    "MAX_INBOX_DOCUMENT_BYTES",
    "HarbourMcpDispatchError",
    "HarbourMcpDispatcher",
    "build_harbour_mcp_tool_contracts",
]
