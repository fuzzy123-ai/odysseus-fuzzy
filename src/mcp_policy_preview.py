"""Policy preview helpers for the Odysseus MCP Workbench."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from src.mcp_client_profiles import McpClientProfile, build_mcp_client_profile
from src.mcp_server_tool_policy import McpToolDecision, McpToolPolicyOptions, classify_mcp_tool


MCP_POLICY_PREVIEW_SCHEMA = "odysseus.mcp.policy_preview.v1"

GATES_BY_CATEGORY = {
    "owner_scoped_write": "MCP-OWNER-WRITE-GO",
    "private_read": "MCP-PRIVATE-READ-GO",
    "filesystem_read": "MCP-FILESYSTEM-READ-GO",
    "generic_api": "MCP-GENERIC-API-GO",
    "high_risk": "MCP-HIGH-RISK-NO-GO",
    "unclassified": "MCP-UNCLASSIFIED-TOOL-GO",
}


@dataclass(frozen=True)
class McpToolPreview:
    tool_name: str
    exposed: bool
    category: str
    reason: str
    required_gate: str = ""

    @classmethod
    def from_decision(cls, decision: McpToolDecision) -> "McpToolPreview":
        return cls(
            tool_name=decision.tool_name,
            exposed=decision.exposed,
            category=decision.category,
            reason=decision.reason,
            required_gate="" if decision.exposed else GATES_BY_CATEGORY.get(decision.category, ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "exposed": self.exposed,
            "category": self.category,
            "reason": self.reason,
            "required_gate": self.required_gate,
        }


@dataclass(frozen=True)
class McpPolicyPreview:
    tools: tuple[McpToolPreview, ...]
    profile: McpClientProfile | None = None

    @property
    def exposed_count(self) -> int:
        return sum(1 for item in self.tools if item.exposed)

    @property
    def hidden_count(self) -> int:
        return sum(1 for item in self.tools if not item.exposed)

    @property
    def required_gates(self) -> tuple[str, ...]:
        gates = {item.required_gate for item in self.tools if item.required_gate}
        return tuple(sorted(gates))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": MCP_POLICY_PREVIEW_SCHEMA,
            "client_profile": self.profile.to_public_dict() if self.profile else {},
            "enabled_profile_active": self.profile.is_active() if self.profile else False,
            "exposed_count": self.exposed_count,
            "hidden_count": self.hidden_count,
            "required_gates": self.required_gates,
            "tools": tuple(item.to_dict() for item in self.tools),
            "live_client_connection_allowed": False,
            "token_value_visible": False,
            "secret_value_visible": False,
        }


def build_mcp_policy_preview(
    tools: Iterable[str | Mapping[str, Any]],
    *,
    options: McpToolPolicyOptions | None = None,
    client_profile: McpClientProfile | Mapping[str, Any] | None = None,
) -> McpPolicyPreview:
    profile = None
    policy_options = options or McpToolPolicyOptions()
    if client_profile is not None:
        profile = client_profile if isinstance(client_profile, McpClientProfile) else build_mcp_client_profile(client_profile)
        policy_options = profile.to_policy_options()
    previews = tuple(
        sorted(
            (McpToolPreview.from_decision(classify_mcp_tool(tool, policy_options)) for tool in tools),
            key=lambda item: item.tool_name,
        )
    )
    return McpPolicyPreview(tools=previews, profile=profile)
