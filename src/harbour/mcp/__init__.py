"""MCP bounded context: Primary Interface & MCP-First Service Dispatch.

This module anchors the MCP-First primary interface (mcp-first-2026-10-02),
exposing typed, headless tools for planning, memory, gateway execution, and inbox.
The UI layer remains deferred and frozen (H1R-14).
"""

from __future__ import annotations

from typing import Any

from src.mcp_context_app import (
    McpContextApp,
    install_mcp_context_app,
)
from src.mcp_context_authority import (
    McpContextAuthority,
)
from src.mcp_private_context_confirmation import (
    McpPrivateContextConfirmationService,
)
from src.planning_mcp_service import (
    PlanningMcpService,
)
from src.harbour.mcp.dispatcher import (
    HARBOUR_MCP_TOOL_ALIASES,
    HarbourMcpDispatchError,
    HarbourMcpDispatcher,
    build_harbour_mcp_tool_contracts,
)

MCP_FIRST_SURFACE_SCHEMA = "odysseus.harbour.mcp_first_surface.v1"
PRIMARY_INTERFACE = "mcp"
UI_STATUS = "deferred_frozen_h1r14"

CANONICAL_NAMESPACES = (
    "harbour_planning",
    "harbour_memory",
    "harbour_gateway",
    "harbour_inbox",
)

HARBOUR_MCP_FIRST_TOOLS: dict[str, dict[str, Any]] = {
    # 1. harbour_planning_*
    "harbour_planning_get_projection": {
        "name": "harbour_planning_get_projection",
        "namespace": "harbour_planning",
        "description": "Retrieve current Harbour Planning v2 projection snapshot and heads",
        "bound_service": "PlanningMcpService",
        "scope": "mcp:planning:read",
        "input_schema": {
            "type": "object",
            "properties": {
                "revision_id": {"type": "string", "description": "Optional specific planning revision"}
            },
        },
    },
    "harbour_planning_lookup_revision": {
        "name": "harbour_planning_lookup_revision",
        "namespace": "harbour_planning",
        "description": "Inspect exact planning revision metadata and roadmap bindings",
        "bound_service": "PlanningMcpService",
        "scope": "mcp:planning:read",
        "input_schema": {
            "type": "object",
            "required": ["revision_id"],
            "properties": {
                "revision_id": {"type": "string", "description": "Revision identifier"}
            },
        },
    },
    "harbour_planning_list_heads": {
        "name": "harbour_planning_list_heads",
        "namespace": "harbour_planning",
        "description": "List all accepted planning heads across active roadmaps",
        "bound_service": "PlanningMcpService",
        "scope": "mcp:planning:read",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    # 2. harbour_memory_*
    "harbour_memory_query_usi": {
        "name": "harbour_memory_query_usi",
        "namespace": "harbour_memory",
        "description": "Query Unified Source Index for documents, code lineage, and planning context",
        "bound_service": "KnowledgeRuntime",
        "scope": "mcp:harbour:context:read",
        "input_schema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "description": "Search term or concept query"},
                "limit": {"type": "integer", "default": 10},
            },
        },
    },
    "harbour_memory_get_context_manifest": {
        "name": "harbour_memory_get_context_manifest",
        "namespace": "harbour_memory",
        "description": "Generate combined context manifest across memory and planning domains",
        "bound_service": "CombinedContextManifestProducer",
        "scope": "mcp:harbour:context:read",
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
            },
        },
    },
    "harbour_memory_runtime_status": {
        "name": "harbour_memory_runtime_status",
        "namespace": "harbour_memory",
        "description": "Inspect cooperative indexing pump status and adapter synchronization state",
        "bound_service": "KnowledgeRuntime",
        "scope": "mcp:harbour:context:read",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    # 3. harbour_gateway_*
    "harbour_gateway_dispatch_task": {
        "name": "harbour_gateway_dispatch_task",
        "namespace": "harbour_gateway",
        "description": "Dispatch sandboxed coding or verification plan into isolated git worktree",
        "bound_service": "CodingAgentBackend",
        "scope": "mcp:harbour:gateway",
        "input_schema": {
            "type": "object",
            "required": ["task_plan"],
            "properties": {
                "task_plan": {"type": "object"},
            },
        },
    },
    "harbour_gateway_get_runner_state": {
        "name": "harbour_gateway_get_runner_state",
        "namespace": "harbour_gateway",
        "description": "Inspect execution runner state, sandbox job status, and quality gate reports",
        "bound_service": "CodingAgentRunnerState",
        "scope": "mcp:harbour:gateway",
        "input_schema": {
            "type": "object",
            "required": ["task_id"],
            "properties": {
                "task_id": {"type": "string"},
            },
        },
    },
    "harbour_gateway_evaluate_quality_gate": {
        "name": "harbour_gateway_evaluate_quality_gate",
        "namespace": "harbour_gateway",
        "description": "Evaluate automated quality and done gates for sandboxed worktree",
        "bound_service": "CodingAgentBackend",
        "scope": "mcp:harbour:gateway",
        "input_schema": {
            "type": "object",
            "required": ["task_id"],
            "properties": {
                "task_id": {"type": "string"},
            },
        },
    },
    "harbour_gateway_claim_lease": {
        "name": "harbour_gateway_claim_lease",
        "namespace": "harbour_gateway",
        "description": "Acquire a path-isolated worker execution lease with fencing token",
        "bound_service": "CodingAgentBackend",
        "scope": "mcp:harbour:gateway",
        "input_schema": {
            "type": "object",
            "required": ["task_id", "write_paths"],
            "properties": {
                "task_id": {"type": "string", "description": "Unique task identifier"},
                "write_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File or directory paths claimed for exclusive mutation",
                },
                "read_only_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional paths read by worker without mutation lock",
                },
                "ttl_seconds": {
                    "type": "integer",
                    "default": 300,
                    "description": "Lease duration in seconds",
                },
                "claim_id": {
                    "type": "string",
                    "description": "Optional durable claim reference",
                },
            },
        },
    },
    "harbour_gateway_release_lease": {
        "name": "harbour_gateway_release_lease",
        "namespace": "harbour_gateway",
        "description": "Release an active worker execution lease and record verification receipt",
        "bound_service": "CodingAgentBackend",
        "scope": "mcp:harbour:gateway",
        "input_schema": {
            "type": "object",
            "required": ["lease_id"],
            "properties": {
                "lease_id": {"type": "string", "description": "Active lease identifier"},
                "task_id": {"type": "string", "description": "Optional matching task identifier"},
                "verification_receipt": {
                    "type": "object",
                    "description": "Verification outcome or receipt dictionary",
                },
            },
        },
    },
    # 4. harbour_inbox_*
    "harbour_inbox_list_items": {
        "name": "harbour_inbox_list_items",
        "namespace": "harbour_inbox",
        "description": "List inbox documents and pending human-in-the-loop review items",
        "bound_service": "HarbourInboxService",
        "scope": "mcp:harbour:inbox",
        "input_schema": {
            "type": "object",
            "properties": {
                "filter_status": {"type": "string"},
            },
        },
    },
    "harbour_inbox_capture_document": {
        "name": "harbour_inbox_capture_document",
        "namespace": "harbour_inbox",
        "description": "Ingest and attach document with scoped authority proof",
        "bound_service": "HarbourInboxService",
        "scope": "mcp:harbour:inbox",
        "input_schema": {
            "type": "object",
            "required": ["filename", "content_bytes"],
            "properties": {
                "filename": {"type": "string"},
                "content_bytes": {"type": "string"},
            },
        },
    },
    "harbour_inbox_stamp_document": {
        "name": "harbour_inbox_stamp_document",
        "namespace": "harbour_inbox",
        "description": "Apply cryptographic stamp and subject-scope token to document",
        "bound_service": "HarbourInboxService",
        "scope": "mcp:harbour:inbox",
        "input_schema": {
            "type": "object",
            "required": ["document_id"],
            "properties": {
                "document_id": {"type": "string"},
            },
        },
    },
}


def get_mcp_first_surface_manifest() -> dict[str, Any]:
    """Return the MCP-First primary interface surface manifest."""
    scopes = sorted({tool["scope"] for tool in HARBOUR_MCP_FIRST_TOOLS.values()})
    return {
        "schema": MCP_FIRST_SURFACE_SCHEMA,
        "manifest_version": "1.0.0",
        "primary_interface": PRIMARY_INTERFACE,
        "ui_status": UI_STATUS,
        "namespaces": list(CANONICAL_NAMESPACES),
        "scopes": scopes,
        "tools": {k: dict(v) for k, v in HARBOUR_MCP_FIRST_TOOLS.items()},
    }


__all__ = [
    "MCP_FIRST_SURFACE_SCHEMA",
    "PRIMARY_INTERFACE",
    "UI_STATUS",
    "CANONICAL_NAMESPACES",
    "HARBOUR_MCP_FIRST_TOOLS",
    "get_mcp_first_surface_manifest",
    # Service and authority re-exports
    "McpContextApp",
    "install_mcp_context_app",
    "McpContextAuthority",
    "McpPrivateContextConfirmationService",
    "PlanningMcpService",
    # Dispatcher
    "HARBOUR_MCP_TOOL_ALIASES",
    "HarbourMcpDispatcher",
    "HarbourMcpDispatchError",
    "build_harbour_mcp_tool_contracts",
]
