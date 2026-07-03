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
_MAX_SCHEMA_REF_LENGTH = 120
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")
_TOOL_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")


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


def _normalize_tool_id(value: Any, *, field_name: str = "tool_id") -> str:
    text = str(value or "").strip()
    if not text:
        raise ToolCatalogError(f"{field_name} must not be empty")
    if not _TOOL_ID_RE.fullmatch(text):
        raise ToolCatalogError(f"{field_name} contains unsafe characters")
    return text[:_MAX_SCHEMA_REF_LENGTH]


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


def _manifest_budget(capability_count: int, description: str) -> int:
    return 12 + capability_count * 8 + max(1, len(description) // 16)


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
class ToolManifest:
    tool_id: str
    family: str
    short_description: str
    capabilities: tuple[str, ...]
    risk_level: ToolRiskLevel
    schema_ref: str
    visibility_state: ToolVisibility
    prompt_budget_estimate: int

    @classmethod
    def create(
        cls,
        *,
        tool_id: str,
        family: str,
        short_description: str,
        capabilities: Iterable[Any],
        risk_level: ToolRiskLevel | str,
        schema_ref: str,
        visibility_state: ToolVisibility | str = ToolVisibility.HIDDEN,
    ) -> "ToolManifest":
        normalized_capabilities = _normalize_slug_list(
            capabilities,
            field_name="capability",
            allow_empty=False,
        )
        description = _normalize_text(
            short_description,
            field_name="short_description",
            allow_empty=False,
            limit=120,
        )
        return cls(
            tool_id=_normalize_tool_id(tool_id),
            family=_normalize_slug(family, field_name="family"),
            short_description=description,
            capabilities=normalized_capabilities,
            risk_level=risk_level if isinstance(risk_level, ToolRiskLevel) else ToolRiskLevel(str(risk_level)),
            schema_ref=_normalize_text(schema_ref, field_name="schema_ref", allow_empty=False, limit=_MAX_SCHEMA_REF_LENGTH),
            visibility_state=visibility_state
            if isinstance(visibility_state, ToolVisibility)
            else ToolVisibility(str(visibility_state)),
            prompt_budget_estimate=_manifest_budget(len(normalized_capabilities), description),
        )

    @classmethod
    def from_function_schema(
        cls,
        schema: dict[str, Any],
        *,
        visibility_state: ToolVisibility | str = ToolVisibility.HIDDEN,
    ) -> "ToolManifest":
        fn = schema.get("function") if isinstance(schema, dict) else None
        payload = fn if isinstance(fn, dict) else schema
        if not isinstance(payload, dict):
            raise ToolCatalogError("schema must be a mapping")
        name = str(payload.get("name") or "")
        description = str(payload.get("description") or "")
        if not name:
            raise ToolCatalogError("schema function name must not be empty")
        return cls.create(
            tool_id=name,
            family=infer_tool_family(name),
            short_description=description or f"{name} tool",
            capabilities=infer_tool_capabilities(name, description),
            risk_level=infer_tool_risk_level(name),
            schema_ref=f"function:{name}",
            visibility_state=visibility_state,
        )

    def compact_prompt_dict(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "family": self.family,
            "description": self.short_description,
            "capabilities": self.capabilities,
            "risk_level": self.risk_level.value,
            "visibility_state": self.visibility_state.value,
            "schema_ref": self.schema_ref,
            "prompt_budget_estimate": self.prompt_budget_estimate,
        }

    def audit_summary(self) -> dict[str, Any]:
        return {
            **self.compact_prompt_dict(),
            "raw_schema_visible": False,
            "raw_content_visible": False,
            "token_value_visible": False,
        }


def build_tool_manifests_from_function_schemas(
    schemas: Iterable[dict[str, Any]],
    *,
    visibility_state: ToolVisibility | str = ToolVisibility.HIDDEN,
) -> tuple[ToolManifest, ...]:
    manifests: list[ToolManifest] = []
    seen: set[str] = set()
    for schema in schemas:
        manifest = ToolManifest.from_function_schema(schema, visibility_state=visibility_state)
        if manifest.tool_id in seen:
            continue
        seen.add(manifest.tool_id)
        manifests.append(manifest)
    return tuple(sorted(manifests, key=lambda item: item.tool_id))


def infer_tool_family(tool_id: str) -> str:
    name = str(tool_id or "").lower()
    if name.startswith("mcp__"):
        return "mcp"
    if name in {"bash", "python", "manage_bg_jobs"}:
        return "execution"
    if name in {"read_file", "write_file", "edit_file", "ls", "glob", "grep", "get_workspace"}:
        return "filesystem"
    if "email" in name or name in {"send_email", "reply_to_email", "bulk_email"}:
        return "email"
    if "calendar" in name or "task" in name or "note" in name:
        return "planning"
    if "memory" in name or "research" in name or "rag" in name:
        return "knowledge"
    if "web" in name or name in {"api_call", "manage_webhooks"}:
        return "network"
    if "image" in name or "document" in name:
        return "content"
    if "session" in name or "subagent" in name or name in {"delegate", "ask_user"}:
        return "orchestration"
    if "settings" in name or "token" in name or "mcp" in name or "plugin" in name:
        return "admin"
    return "general"


def infer_tool_capabilities(tool_id: str, description: str = "") -> tuple[str, ...]:
    text = f"{tool_id} {description}".lower()
    capabilities: list[str] = []
    for needle, capability in (
        ("read", "read"),
        ("list", "read"),
        ("search", "search"),
        ("fetch", "network"),
        ("web", "network"),
        ("write", "write"),
        ("edit", "write"),
        ("delete", "destructive"),
        ("send", "external-send"),
        ("reply", "external-send"),
        ("manage", "manage"),
        ("create", "write"),
        ("run", "execute"),
        ("execute", "execute"),
        ("bash", "execute"),
        ("python", "execute"),
        ("memory", "memory"),
        ("task", "schedule"),
        ("calendar", "calendar"),
        ("document", "document"),
        ("image", "image"),
    ):
        if needle in text and capability not in capabilities:
            capabilities.append(capability)
    if not capabilities:
        capabilities.append("use")
    return tuple(capabilities)


def infer_tool_risk_level(tool_id: str) -> ToolRiskLevel:
    name = str(tool_id or "").lower()
    dangerous_exact = {
        "bash",
        "python",
        "write_file",
        "edit_file",
        "send_email",
        "reply_to_email",
        "bulk_email",
        "delete_email",
        "manage_tokens",
        "manage_settings",
        "manage_mcp",
        "manage_plugins",
        "manage_repos",
        "api_call",
    }
    dangerous_prefixes = ("mcp__",)
    elevated_exact = {
        "read_file",
        "web_search",
        "web_fetch",
        "manage_calendar",
        "manage_tasks",
        "manage_notes",
        "manage_memory",
        "manage_personal_docs",
        "manage_documents",
        "manage_contact",
        "list_emails",
        "read_email",
        "archive_email",
        "mark_email_read",
    }
    if name in dangerous_exact or name.startswith(dangerous_prefixes):
        return ToolRiskLevel.DANGEROUS
    if name in elevated_exact or name.startswith("manage_"):
        return ToolRiskLevel.ELEVATED
    return ToolRiskLevel.SAFE


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
