"""Small backend contract for tool catalog visibility and selection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any, Iterable

from src.agent_identity import AgentIdentity
from src.context_capsule import ContextCapsule


_MAX_ID_LENGTH = 80
_MAX_SUMMARY_CHARS = 140
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")


class ToolCatalogError(ValueError):
    """Raised when tool catalog inputs cannot be normalized safely."""


class ToolVisibility(StrEnum):
    VISIBLE = "visible"
    HIDDEN = "hidden"
    BLOCKED = "blocked"
    REQUIRES_APPROVAL = "requires_approval"
    UNAVAILABLE = "unavailable"


class ToolRiskLevel(StrEnum):
    SAFE = "safe"
    ELEVATED = "elevated"
    DANGEROUS = "dangerous"


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise ToolCatalogError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise ToolCatalogError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID_LENGTH:
        raise ToolCatalogError(f"{field_name} exceeds max length {_MAX_ID_LENGTH}")
    return normalized


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_SUMMARY_CHARS) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ToolCatalogError(f"{field_name} must not be empty")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _normalize_slug_list(values: Iterable[Any], *, field_name: str, allow_empty: bool) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _normalize_slug(value, field_name=field_name)
        if item not in seen:
            seen.add(item)
            normalized.append(item)
    if not allow_empty and not normalized:
        raise ToolCatalogError(f"{field_name} must not be empty")
    return tuple(sorted(normalized))


def _budget_for(tool: "ToolDescriptor") -> int:
    return 20 + len(tool.capabilities) * 12 + len(tool.label) // 4


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    tool_id: str
    label: str
    capabilities: tuple[str, ...]
    risk_level: ToolRiskLevel
    requires_approval: bool
    allowed_roles: tuple[str, ...]
    blocked_scopes: tuple[str, ...]
    summary: str

    @classmethod
    def create(
        cls,
        *,
        tool_id: str,
        label: str,
        capabilities: Iterable[Any],
        risk_level: ToolRiskLevel | str,
        requires_approval: bool,
        allowed_roles: Iterable[Any],
        blocked_scopes: Iterable[Any],
        summary: str,
    ) -> "ToolDescriptor":
        return cls(
            tool_id=_normalize_slug(tool_id, field_name="tool_id"),
            label=_normalize_text(label, field_name="label", allow_empty=False, limit=80),
            capabilities=_normalize_slug_list(capabilities, field_name="capability", allow_empty=False),
            risk_level=risk_level if isinstance(risk_level, ToolRiskLevel) else ToolRiskLevel(str(risk_level)),
            requires_approval=bool(requires_approval),
            allowed_roles=_normalize_slug_list(allowed_roles, field_name="allowed_role", allow_empty=True),
            blocked_scopes=_normalize_slug_list(blocked_scopes, field_name="blocked_scope", allow_empty=True),
            summary=_normalize_text(summary, field_name="summary", allow_empty=False),
        )


@dataclass(frozen=True, slots=True)
class ToolSelectionRequest:
    agent_identity: AgentIdentity
    context_capsule: ContextCapsule
    requested_capabilities: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        agent_identity: AgentIdentity,
        context_capsule: ContextCapsule,
        requested_capabilities: Iterable[Any],
    ) -> "ToolSelectionRequest":
        if not isinstance(agent_identity, AgentIdentity):
            raise ToolCatalogError("agent_identity must be an AgentIdentity")
        if not isinstance(context_capsule, ContextCapsule):
            raise ToolCatalogError("context_capsule must be a ContextCapsule")
        return cls(
            agent_identity=agent_identity,
            context_capsule=context_capsule,
            requested_capabilities=_normalize_slug_list(
                requested_capabilities,
                field_name="requested_capability",
                allow_empty=False,
            ),
        )


@dataclass(frozen=True, slots=True)
class ToolSelectionResult:
    request: ToolSelectionRequest
    visible_tools: tuple[ToolDescriptor, ...]
    blocked_tools: tuple[ToolDescriptor, ...]
    warnings: tuple[str, ...]
    prompt_budget_estimate: int

    @classmethod
    def select(
        cls,
        *,
        request: ToolSelectionRequest,
        catalog: Iterable[ToolDescriptor],
    ) -> "ToolSelectionResult":
        if not isinstance(request, ToolSelectionRequest):
            raise ToolCatalogError("request must be a ToolSelectionRequest")

        descriptors = sorted(catalog, key=lambda item: item.tool_id)
        visible: list[ToolDescriptor] = []
        blocked: list[ToolDescriptor] = []
        warnings: list[str] = []
        seen_warnings: set[str] = set()
        scope_tags = _selection_scope_tags(request)
        role_id = request.agent_identity.role_id
        requested = set(request.requested_capabilities)

        for tool in descriptors:
            matched_capabilities = requested & set(tool.capabilities)
            if not matched_capabilities:
                continue

            if set(tool.blocked_scopes) & scope_tags:
                blocked.append(tool)
                _warn(
                    warnings,
                    seen_warnings,
                    f"blocked:{tool.tool_id}:{','.join(sorted(set(tool.blocked_scopes) & scope_tags))}",
                )
                continue

            if tool.allowed_roles and role_id not in tool.allowed_roles:
                _warn(warnings, seen_warnings, f"hidden:{tool.tool_id}:role-mismatch")
                continue

            if tool.requires_approval or tool.risk_level == ToolRiskLevel.DANGEROUS:
                blocked.append(tool)
                _warn(warnings, seen_warnings, f"approval:{tool.tool_id}")
                continue

            visible.append(tool)

        prompt_budget_estimate = sum(_budget_for(tool) for tool in visible) + len(request.requested_capabilities) * 8
        return cls(
            request=request,
            visible_tools=tuple(visible),
            blocked_tools=tuple(blocked),
            warnings=tuple(warnings),
            prompt_budget_estimate=prompt_budget_estimate,
        )

    def audit_summary(self) -> dict[str, Any]:
        return {
            "capsule_id": self.request.context_capsule.capsule_id,
            "agent_id": self.request.agent_identity.agent_id,
            "role_id": self.request.agent_identity.role_id,
            "requested_capabilities": self.request.requested_capabilities,
            "visible_tool_ids": tuple(tool.tool_id for tool in self.visible_tools),
            "blocked_tool_ids": tuple(tool.tool_id for tool in self.blocked_tools),
            "visible_count": len(self.visible_tools),
            "blocked_count": len(self.blocked_tools),
            "warning_count": len(self.warnings),
            "warnings": self.warnings,
            "prompt_budget_estimate": self.prompt_budget_estimate,
        }


def _selection_scope_tags(request: ToolSelectionRequest) -> set[str]:
    identity = request.agent_identity
    capsule = request.context_capsule
    return {
        f"agent-{identity.agent_id}",
        f"role-{identity.role_id}",
        f"project-{identity.project_id}",
        f"memory-{identity.memory_scope}",
        f"workspace-{identity.workspace_scope}",
        f"capsule-{capsule.capsule_id}",
    }


def _warn(bucket: list[str], seen: set[str], warning: str) -> None:
    if warning not in seen:
        seen.add(warning)
        bucket.append(warning)
