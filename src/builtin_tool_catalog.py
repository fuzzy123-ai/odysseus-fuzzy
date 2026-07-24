"""Declarative built-in tool identity, policy, and projection contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable, Mapping

from src.tool_catalog import (
    ToolAvailability,
    ToolAnalyticsIdentityIndex,
    ToolCatalogError,
    ToolDescriptorV2,
    ToolDescriptorV2Index,
    ToolEffectClass,
    ToolFamily,
    ToolLifecycle,
    ToolPermission,
    ToolRiskLevel,
    ToolSource,
    ToolVisibility,
)


class BuiltInCatalogError(ToolCatalogError):
    """Raised when a built-in declaration or consumer projection drifts."""


class BuiltInRegistrationDisposition(StrEnum):
    ACTIVE_RUNTIME = "active_runtime"
    CONFIRMED_ROUTE_ONLY = "confirmed_route_only"
    DEFERRED = "deferred"
    SECURITY_BLOCKED = "security_blocked"


class BuiltInDefaultPolicy(StrEnum):
    STANDARD = "standard"
    DEFERRED_BY_OPERATOR_PRIORITY = "deferred_by_operator_priority"
    DEPENDENT_DEFERRED = "dependent_deferred"
    DEFERRED = "deferred"


CATALOG_TOOL_IDS = (
    "adopt_served_model",
    "api_call",
    "app_api",
    "archive_email",
    "ask_teacher",
    "ask_user",
    "bash",
    "bulk_email",
    "cancel_download",
    "chat_with_model",
    "commit_project",
    "create_document",
    "create_session",
    "delegate",
    "delete_email",
    "download_model",
    "edit_document",
    "edit_file",
    "edit_image",
    "generate_image",
    "get_workspace",
    "glob",
    "grep",
    "list_cached_models",
    "list_cookbook_servers",
    "list_downloads",
    "list_email_accounts",
    "list_emails",
    "list_models",
    "list_serve_presets",
    "list_served_models",
    "list_sessions",
    "ls",
    "manage_assistant",
    "manage_bg_jobs",
    "manage_calendar",
    "manage_contact",
    "manage_documents",
    "manage_embeddings",
    "manage_endpoints",
    "manage_github_issues",
    "manage_mcp",
    "manage_memory",
    "manage_nextcloud_transfer",
    "manage_notes",
    "manage_todos",
    "manage_personal_docs",
    "manage_plugins",
    "manage_presets",
    "manage_repos",
    "manage_research",
    "manage_session",
    "manage_settings",
    "manage_skills",
    "manage_subagents",
    "manage_tasks",
    "manage_tokens",
    "manage_webhooks",
    "mark_email_read",
    "pipeline",
    "publish_artifact",
    "python",
    "read_email",
    "read_file",
    "recent_changes",
    "reply_to_email",
    "resolve_contact",
    "search_chats",
    "search_hf_models",
    "send_email",
    "send_to_session",
    "serve_model",
    "serve_preset",
    "spawn_subagent",
    "stop_served_model",
    "suggest_document",
    "tail_serve_output",
    "trigger_research",
    "ui_control",
    "update_document",
    "update_plan",
    "verify_pygame_headless",
    "web_fetch",
    "web_search",
    "write_file",
)

REGISTRATION_GAPS = frozenset(
    {
        "manage_assistant",
        "manage_embeddings",
        "manage_personal_docs",
        "manage_plugins",
        "manage_presets",
        "tail_serve_output",
    }
)
DEFERRED_REGISTRATION_GAPS = frozenset({"manage_assistant", "manage_presets"})
CONFIRMED_ROUTE_REGISTRATION_GAPS = frozenset(
    {"manage_embeddings", "manage_personal_docs", "manage_plugins"}
)
SECURITY_BLOCKED_REGISTRATION_GAPS = frozenset({"tail_serve_output"})
NON_NATIVE_SCHEMA_TOOLS = frozenset({"generate_image"})
RAG_ONLY_PROMPT_TOOLS = frozenset(
    {
        "adopt_served_model",
        "api_call",
        "commit_project",
        "delegate",
        "edit_image",
        "glob",
        "grep",
        "list_cookbook_servers",
        "list_serve_presets",
        "ls",
        "manage_bg_jobs",
        "manage_nextcloud_transfer",
        "manage_subagents",
        "serve_preset",
        "spawn_subagent",
        "trigger_research",
    }
)
EMAIL_DISPATCH_FAMILY = frozenset(
    {
        "archive_email",
        "bulk_email",
        "delete_email",
        "list_email_accounts",
        "list_emails",
        "mark_email_read",
        "read_email",
        "reply_to_email",
        "send_email",
    }
)
OPERATOR_PRIORITY_DEFERRED_TOOLS = EMAIL_DISPATCH_FAMILY | frozenset(
    {"manage_calendar"}
)
DEPENDENT_CONTACT_DEFERRED_TOOLS = frozenset(
    {"manage_contact", "resolve_contact"}
)
OTHER_DEFAULT_DEFERRED_TOOLS = frozenset(
    {"manage_assistant", "manage_presets"}
)
DEFAULT_DEFERRED_TOOLS = (
    OPERATOR_PRIORITY_DEFERRED_TOOLS
    | DEPENDENT_CONTACT_DEFERRED_TOOLS
    | OTHER_DEFAULT_DEFERRED_TOOLS
)
AGENT_HANDLER_TOOLS = frozenset(
    {
        "ask_teacher",
        "bash",
        "chat_with_model",
        "commit_project",
        "create_document",
        "create_session",
        "edit_document",
        "edit_file",
        "get_workspace",
        "glob",
        "grep",
        "list_models",
        "list_sessions",
        "ls",
        "manage_bg_jobs",
        "manage_documents",
        "manage_session",
        "publish_artifact",
        "python",
        "read_file",
        "send_to_session",
        "suggest_document",
        "update_document",
        "verify_pygame_headless",
        "web_fetch",
        "web_search",
        "write_file",
    }
)

_FAMILY_GROUPS: Mapping[ToolFamily, frozenset[str]] = {
    ToolFamily.CODE_FILESYSTEM: frozenset(
        {
            "bash",
            "commit_project",
            "edit_file",
            "get_workspace",
            "glob",
            "grep",
            "ls",
            "publish_artifact",
            "python",
            "read_file",
            "verify_pygame_headless",
            "write_file",
        }
    ),
    ToolFamily.SEARCH_WEB: frozenset(
        {"manage_research", "trigger_research", "web_fetch", "web_search"}
    ),
    ToolFamily.KNOWLEDGE_MEMORY: frozenset({"manage_memory", "search_chats"}),
    ToolFamily.DOCUMENTS_MEDIA: frozenset(
        {
            "create_document",
            "edit_document",
            "edit_image",
            "generate_image",
            "manage_documents",
            "manage_personal_docs",
            "suggest_document",
            "update_document",
        }
    ),
    ToolFamily.MODEL_OPS: frozenset(
        {
            "adopt_served_model",
            "ask_teacher",
            "cancel_download",
            "chat_with_model",
            "download_model",
            "list_cached_models",
            "list_cookbook_servers",
            "list_downloads",
            "list_models",
            "list_serve_presets",
            "list_served_models",
            "manage_embeddings",
            "search_hf_models",
            "serve_model",
            "serve_preset",
            "stop_served_model",
            "tail_serve_output",
        }
    ),
    ToolFamily.PROJECTS_REPOSITORIES: frozenset(
        {"manage_github_issues", "manage_repos", "recent_changes"}
    ),
    ToolFamily.ORCHESTRATION_SESSIONS: frozenset(
        {
            "create_session",
            "delegate",
            "list_sessions",
            "manage_bg_jobs",
            "manage_session",
            "manage_subagents",
            "pipeline",
            "send_to_session",
            "spawn_subagent",
        }
    ),
    ToolFamily.PLANNING_COMMUNICATION: frozenset(
        {
            "archive_email",
            "ask_user",
            "bulk_email",
            "delete_email",
            "list_email_accounts",
            "list_emails",
            "manage_assistant",
            "manage_calendar",
            "manage_contact",
            "manage_notes",
            "manage_todos",
            "manage_tasks",
            "mark_email_read",
            "read_email",
            "reply_to_email",
            "resolve_contact",
            "send_email",
            "update_plan",
        }
    ),
    ToolFamily.ADMIN_SYSTEM: frozenset(
        {
            "manage_endpoints",
            "manage_presets",
            "manage_settings",
            "manage_skills",
            "manage_tokens",
            "manage_webhooks",
            "ui_control",
        }
    ),
    ToolFamily.PLUGINS_MCP: frozenset({"manage_mcp", "manage_plugins"}),
    ToolFamily.EXTERNAL_PROVIDERS: frozenset(
        {"api_call", "app_api", "manage_nextcloud_transfer"}
    ),
    ToolFamily.EXPERIMENTAL: frozenset(),
}

_READ_EFFECT_TOOLS = frozenset(
    {
        "ask_teacher",
        "get_workspace",
        "glob",
        "grep",
        "list_cached_models",
        "list_cookbook_servers",
        "list_downloads",
        "list_email_accounts",
        "list_emails",
        "list_models",
        "list_serve_presets",
        "list_served_models",
        "list_sessions",
        "ls",
        "read_email",
        "read_file",
        "recent_changes",
        "resolve_contact",
        "search_chats",
        "search_hf_models",
        "tail_serve_output",
        "web_fetch",
        "web_search",
    }
)
_LOCAL_WRITE_EFFECT_TOOLS = frozenset(
    {
        "commit_project",
        "create_document",
        "edit_document",
        "edit_file",
        "edit_image",
        "generate_image",
        "publish_artifact",
        "suggest_document",
        "update_document",
        "write_file",
    }
)
_EXTERNAL_WRITE_EFFECT_TOOLS = frozenset(
    {
        "api_call",
        "app_api",
        "archive_email",
        "bulk_email",
        "manage_calendar",
        "manage_contact",
        "manage_nextcloud_transfer",
        "reply_to_email",
        "send_email",
    }
)
_DESTRUCTIVE_EFFECT_TOOLS = frozenset({"delete_email"})
_ADMIN_PERMISSION_TOOLS = frozenset(
    {
        "app_api",
        "bash",
        "manage_endpoints",
        "manage_mcp",
        "manage_plugins",
        "manage_settings",
        "manage_tokens",
        "manage_webhooks",
        "python",
        "tail_serve_output",
    }
)


def _family_map() -> dict[str, ToolFamily]:
    result: dict[str, ToolFamily] = {}
    for family, tool_ids in _FAMILY_GROUPS.items():
        for tool_id in tool_ids:
            if tool_id in result:
                raise BuiltInCatalogError(f"tool {tool_id} belongs to multiple families")
            result[tool_id] = family
    missing = set(CATALOG_TOOL_IDS) - set(result)
    extra = set(result) - set(CATALOG_TOOL_IDS)
    if missing or extra:
        raise BuiltInCatalogError(
            f"family partition drift; missing={sorted(missing)}, extra={sorted(extra)}"
        )
    return result


_FAMILY_BY_TOOL = _family_map()


def _effect_for(tool_id: str) -> ToolEffectClass:
    if tool_id in _DESTRUCTIVE_EFFECT_TOOLS:
        return ToolEffectClass.DESTRUCTIVE
    if tool_id in _EXTERNAL_WRITE_EFFECT_TOOLS:
        return ToolEffectClass.EXTERNAL_WRITE
    if tool_id in _LOCAL_WRITE_EFFECT_TOOLS:
        return ToolEffectClass.LOCAL_WRITE
    if tool_id in _READ_EFFECT_TOOLS:
        return ToolEffectClass.READ
    return ToolEffectClass.CONTROL


def _display_name(tool_id: str) -> str:
    acronyms = {
        "ai": "AI",
        "api": "API",
        "bg": "background",
        "hf": "Hugging Face",
        "mcp": "MCP",
        "ui": "UI",
    }
    value = " ".join(acronyms.get(part, part) for part in tool_id.split("_"))
    return value[:1].upper() + value[1:]


def _safe_short_description(tool_id: str, description: str) -> str:
    normalized = " ".join(str(description).split())
    first_sentence = normalized.split(". ", 1)[0].rstrip(".")
    if (
        not first_sentence
        or "/" in first_sentence
        or "\\" in first_sentence
        or "://" in first_sentence
    ):
        first_sentence = f"Use the {_display_name(tool_id)} built-in capability"
    return first_sentence + "."


@dataclass(frozen=True, slots=True)
class BuiltInToolSpec:
    tool_id: str
    family: ToolFamily
    lifecycle: ToolLifecycle
    availability: ToolAvailability
    risk_level: ToolRiskLevel
    permission: ToolPermission
    effect_class: ToolEffectClass
    registration_disposition: BuiltInRegistrationDisposition
    default_policy: BuiltInDefaultPolicy
    runtime_registered: bool
    native_schema: bool
    searchable_index: bool
    dedicated_prompt_section: bool
    handler_ref: str
    projection_exceptions: tuple[str, ...]

    def build_descriptor(self, description: str) -> ToolDescriptorV2:
        available = self.availability == ToolAvailability.AVAILABLE
        return ToolDescriptorV2.create(
            tool_id=self.tool_id,
            analytics_id=self.tool_id.replace("_", "-"),
            display_name=_display_name(self.tool_id),
            description=_safe_short_description(self.tool_id, description),
            family=self.family,
            source=ToolSource.BUILTIN,
            lifecycle=self.lifecycle,
            availability=self.availability,
            default_enabled=False,
            default_visibility=(
                ToolVisibility.HIDDEN
                if available and self.default_policy != BuiltInDefaultPolicy.STANDARD
                else ToolVisibility.VISIBLE
                if available
                else ToolVisibility.BLOCKED
            ),
            risk_level=self.risk_level,
            permission=self.permission,
            effect_class=self.effect_class,
            requires_confirmation=self.effect_class != ToolEffectClass.READ,
            schema_ref=f"function:{self.tool_id}" if self.native_schema else None,
            handler_ref=self.handler_ref,
            prompt_ref=f"tool_index:{self.tool_id}",
            feature_flag="tool-catalog-v2",
            introduced_in="legacy-v1",
        )


def _build_specs() -> tuple[BuiltInToolSpec, ...]:
    specs: list[BuiltInToolSpec] = []
    for tool_id in CATALOG_TOOL_IDS:
        is_gap = tool_id in REGISTRATION_GAPS
        effect = _effect_for(tool_id)
        exceptions: list[str] = []
        if is_gap:
            exceptions.append("registration_gap_deferred_to_TAX3")
            if tool_id in CONFIRMED_ROUTE_REGISTRATION_GAPS:
                exceptions.append("legacy_tag_gap_closed_by_confirmed_catalog_route")
            elif tool_id in DEFERRED_REGISTRATION_GAPS:
                exceptions.append("deferred_by_catalog_policy")
            else:
                exceptions.append("blocked_until_TAX5_owner_session_binding")
        if tool_id in OPERATOR_PRIORITY_DEFERRED_TOOLS:
            exceptions.append("deferred_by_operator_priority")
        elif tool_id in DEPENDENT_CONTACT_DEFERRED_TOOLS:
            exceptions.append("dependent_deferred_by_communications_priority")
        elif tool_id in OTHER_DEFAULT_DEFERRED_TOOLS:
            exceptions.append("default_deferred")
        if tool_id in NON_NATIVE_SCHEMA_TOOLS:
            exceptions.append("text_only_no_native_schema")
        if tool_id in RAG_ONLY_PROMPT_TOOLS:
            exceptions.append("rag_index_only_no_dedicated_prompt_section")
        if tool_id in AGENT_HANDLER_TOOLS:
            handler_ref = f"agent_tools:{tool_id}"
        elif tool_id in EMAIL_DISPATCH_FAMILY:
            handler_ref = "dispatcher:email_family"
        elif is_gap:
            handler_ref = f"dispatcher_reserved:{tool_id}"
        else:
            handler_ref = f"dispatcher:{tool_id}"
        specs.append(
            BuiltInToolSpec(
                tool_id=tool_id,
                family=_FAMILY_BY_TOOL[tool_id],
                lifecycle=(
                    ToolLifecycle.DEFERRED
                    if tool_id in DEFAULT_DEFERRED_TOOLS
                    else ToolLifecycle.BLOCKED
                    if is_gap
                    else ToolLifecycle.CONTEXTUAL
                ),
                availability=(
                    ToolAvailability.BLOCKED if is_gap else ToolAvailability.AVAILABLE
                ),
                risk_level=(
                    ToolRiskLevel.SAFE
                    if effect == ToolEffectClass.READ
                    else ToolRiskLevel.ELEVATED
                    if effect == ToolEffectClass.CONTROL
                    else ToolRiskLevel.DANGEROUS
                ),
                permission=(
                    ToolPermission.ADMIN
                    if tool_id in _ADMIN_PERMISSION_TOOLS
                    else ToolPermission.OWNER
                ),
                effect_class=effect,
                registration_disposition=(
                    BuiltInRegistrationDisposition.CONFIRMED_ROUTE_ONLY
                    if tool_id in CONFIRMED_ROUTE_REGISTRATION_GAPS
                    else BuiltInRegistrationDisposition.DEFERRED
                    if tool_id in DEFERRED_REGISTRATION_GAPS
                    else BuiltInRegistrationDisposition.SECURITY_BLOCKED
                    if tool_id in SECURITY_BLOCKED_REGISTRATION_GAPS
                    else BuiltInRegistrationDisposition.ACTIVE_RUNTIME
                ),
                default_policy=(
                    BuiltInDefaultPolicy.DEFERRED_BY_OPERATOR_PRIORITY
                    if tool_id in OPERATOR_PRIORITY_DEFERRED_TOOLS
                    else BuiltInDefaultPolicy.DEPENDENT_DEFERRED
                    if tool_id in DEPENDENT_CONTACT_DEFERRED_TOOLS
                    else BuiltInDefaultPolicy.DEFERRED
                    if tool_id in OTHER_DEFAULT_DEFERRED_TOOLS
                    else BuiltInDefaultPolicy.STANDARD
                ),
                runtime_registered=not is_gap,
                native_schema=tool_id not in NON_NATIVE_SCHEMA_TOOLS,
                searchable_index=True,
                dedicated_prompt_section=tool_id not in RAG_ONLY_PROMPT_TOOLS,
                handler_ref=handler_ref,
                projection_exceptions=tuple(sorted(exceptions)),
            )
        )
    return tuple(specs)


BUILTIN_TOOL_SPECS = _build_specs()


def builtin_spec(tool_id: str) -> BuiltInToolSpec | None:
    normalized = str(tool_id or "").strip()
    return next((spec for spec in BUILTIN_TOOL_SPECS if spec.tool_id == normalized), None)


def catalog_call_allowed(tool_id: str) -> bool:
    spec = builtin_spec(tool_id)
    return bool(
        spec
        and spec.registration_disposition
        in {
            BuiltInRegistrationDisposition.ACTIVE_RUNTIME,
            BuiltInRegistrationDisposition.CONFIRMED_ROUTE_ONLY,
        }
    )


def catalog_fenced_tool_names() -> frozenset[str]:
    return frozenset(spec.tool_id for spec in BUILTIN_TOOL_SPECS if catalog_call_allowed(spec.tool_id))


def _as_set(values: Iterable[str]) -> set[str]:
    return {str(value) for value in values}


def _assert_projection(name: str, actual: Iterable[str], expected: set[str]) -> None:
    normalized = _as_set(actual)
    if normalized != expected:
        raise BuiltInCatalogError(
            f"{name} projection drift; missing={sorted(expected - normalized)}, "
            f"unexpected={sorted(normalized - expected)}"
        )


def validate_builtin_projections(
    *,
    runtime_tags: Iterable[str],
    function_schemas: Iterable[str],
    tool_index_entries: Iterable[str],
    prompt_sections: Iterable[str],
    agent_handlers: Iterable[str],
    dispatcher_condition_ids: Iterable[str],
) -> None:
    """Strictly validate every existing built-in consumer against the catalog."""

    if len(BUILTIN_TOOL_SPECS) != len(CATALOG_TOOL_IDS):
        raise BuiltInCatalogError("built-in catalog contains duplicate tool IDs")
    catalog_ids = set(CATALOG_TOOL_IDS)
    _assert_projection(
        "runtime",
        runtime_tags,
        {spec.tool_id for spec in BUILTIN_TOOL_SPECS if spec.runtime_registered},
    )
    _assert_projection(
        "function schema",
        function_schemas,
        {spec.tool_id for spec in BUILTIN_TOOL_SPECS if spec.native_schema},
    )
    _assert_projection(
        "tool index",
        tool_index_entries,
        {spec.tool_id for spec in BUILTIN_TOOL_SPECS if spec.searchable_index},
    )
    _assert_projection(
        "dedicated prompt",
        prompt_sections,
        {spec.tool_id for spec in BUILTIN_TOOL_SPECS if spec.dedicated_prompt_section},
    )

    handlers = _as_set(agent_handlers)
    dispatcher = _as_set(dispatcher_condition_ids)
    _assert_projection("agent handler", handlers, set(AGENT_HANDLER_TOOLS))
    for spec in BUILTIN_TOOL_SPECS:
        if spec.tool_id in REGISTRATION_GAPS:
            if spec.tool_id not in dispatcher:
                raise BuiltInCatalogError(
                    f"reserved registration gap {spec.tool_id} has no dispatcher projection"
                )
            continue
        if spec.tool_id in EMAIL_DISPATCH_FAMILY:
            continue
        if spec.tool_id in AGENT_HANDLER_TOOLS:
            continue
        if spec.tool_id not in dispatcher:
            raise BuiltInCatalogError(
                f"runtime tool {spec.tool_id} has no handler or dispatcher projection"
            )

    declared_ids = {spec.tool_id for spec in BUILTIN_TOOL_SPECS}
    if declared_ids != catalog_ids:
        raise BuiltInCatalogError("catalog declarations do not match canonical tool IDs")


def build_builtin_descriptors(
    descriptions: Mapping[str, str],
) -> ToolDescriptorV2Index:
    expected = set(CATALOG_TOOL_IDS)
    actual = set(descriptions)
    if actual != expected:
        raise BuiltInCatalogError(
            f"description projection drift; missing={sorted(expected - actual)}, "
            f"unexpected={sorted(actual - expected)}"
        )
    return ToolDescriptorV2Index.build(
        spec.build_descriptor(descriptions[spec.tool_id]) for spec in BUILTIN_TOOL_SPECS
    )


def build_builtin_analytics_identity_contract(
    descriptions: Mapping[str, str],
    *,
    historical_reservations: Mapping[str, str] | None = None,
    historical_alias_targets: Mapping[str, str] | None = None,
) -> ToolAnalyticsIdentityIndex:
    """Project the canonical built-in catalog into the public TAX10 contract."""

    return build_builtin_descriptors(descriptions).analytics_identity_contract(
        historical_reservations=historical_reservations,
        historical_alias_targets=historical_alias_targets,
    )


def builtin_catalog_audit_summary() -> dict[str, Any]:
    counts = {
        "catalog": len(BUILTIN_TOOL_SPECS),
        "runtime_registered": sum(spec.runtime_registered for spec in BUILTIN_TOOL_SPECS),
        "native_schema": sum(spec.native_schema for spec in BUILTIN_TOOL_SPECS),
        "searchable_index": sum(spec.searchable_index for spec in BUILTIN_TOOL_SPECS),
        "dedicated_prompt_section": sum(
            spec.dedicated_prompt_section for spec in BUILTIN_TOOL_SPECS
        ),
        "registration_gaps": len(REGISTRATION_GAPS),
    }
    return {
        "schema_version": ToolDescriptorV2.SCHEMA_VERSION,
        "counts": counts,
        "tool_ids": CATALOG_TOOL_IDS,
        "registration_gap_ids": tuple(sorted(REGISTRATION_GAPS)),
        "registration_dispositions": {
            "confirmed_route_only": tuple(sorted(CONFIRMED_ROUTE_REGISTRATION_GAPS)),
            "deferred": tuple(sorted(DEFERRED_REGISTRATION_GAPS)),
            "security_blocked": tuple(sorted(SECURITY_BLOCKED_REGISTRATION_GAPS)),
        },
        "default_policies": {
            "deferred_by_operator_priority": tuple(
                sorted(OPERATOR_PRIORITY_DEFERRED_TOOLS)
            ),
            "dependent_deferred": tuple(sorted(DEPENDENT_CONTACT_DEFERRED_TOOLS)),
            "deferred": tuple(sorted(OTHER_DEFAULT_DEFERRED_TOOLS)),
        },
        "raw_content_visible": False,
        "callable_visible": False,
        "tool_arguments_visible": False,
        "tool_results_visible": False,
        "secret_values_visible": False,
    }
