"""Declarative source of truth for Odysseus built-in tool projections.

The module intentionally imports only the Python standard library at import
time.  Agent startup paths can therefore validate or consume its primitive
projection sets without pulling in the heavier catalog/runtime dependency
graph.  Descriptor V2 construction imports :mod:`src.tool_catalog` lazily.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


CATALOG_VERSION = "0.24.0"
STATIC_PROMPT_BASELINE_CHARACTERS = 39_789
HISTORICAL_TOOL_ALIASES: Mapping[str, str] = {
    "manage_rag": "manage_personal_docs",
}
RETIRED_TOOL_ANALYTICS_IDS: frozenset[str] = frozenset()


_TOOL_IDS = (
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

RUNTIME_REGISTRATION_GAPS = frozenset(
    {
        "manage_assistant",
        "manage_embeddings",
        "manage_personal_docs",
        "manage_plugins",
        "manage_presets",
        "tail_serve_output",
    }
)
ACTIVE_GATED_REGISTRATION_IDS = frozenset(
    {"manage_embeddings", "manage_personal_docs", "manage_plugins"}
)
DEFERRED_REGISTRATION_IDS = frozenset({"manage_assistant", "manage_presets"})
BLOCKED_REGISTRATION_IDS = frozenset({"tail_serve_output"})
NON_NATIVE_SCHEMA_IDS = frozenset({"generate_image"})
EMAIL_ADAPTER_TOOL_IDS = frozenset(
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
OPERATOR_PRIORITY_DEFERRED_IDS = frozenset(
    {
        *EMAIL_ADAPTER_TOOL_IDS,
        "manage_assistant",
        "manage_calendar",
        "manage_contact",
        "manage_presets",
        "resolve_contact",
    }
)

# Deployment-level runtime policy is intentionally stricter than family/risk
# inference for these built-ins.  Keep the exact permission in the canonical
# descriptor so Admin/API consumers can never advertise a weaker role than the
# dispatcher actually accepts.  The security layer imports this stdlib-only
# constant, avoiding a second independently maintained permission inventory.
RUNTIME_ADMIN_PERMISSION_IDS = frozenset(
    {
        "adopt_served_model",
        "api_call",
        "app_api",
        "bash",
        "bulk_email",
        "cancel_download",
        "commit_project",
        "delete_email",
        "download_model",
        "edit_file",
        "get_workspace",
        "glob",
        "grep",
        "list_emails",
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
        "manage_personal_docs",
        "manage_plugins",
        "manage_presets",
        "manage_repos",
        "manage_settings",
        "manage_skills",
        "manage_subagents",
        "manage_tasks",
        "manage_tokens",
        "manage_webhooks",
        "publish_artifact",
        "python",
        "read_email",
        "read_file",
        "recent_changes",
        "reply_to_email",
        "resolve_contact",
        "search_chats",
        "send_email",
        "serve_model",
        "serve_preset",
        "spawn_subagent",
        "stop_served_model",
        "tail_serve_output",
        "verify_pygame_headless",
        "write_file",
    }
)
INDEX_INJECTED_PROMPT_IDS = frozenset(
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
INTERNAL_DISPATCH_CONTROL_IDS = frozenset(
    {"invalid_tool_call", "json", "vault_get", "vault_search", "vault_unlock", "xml"}
)

_DEFERRED_PRIORITY_IDS = frozenset(
    {
        *RUNTIME_REGISTRATION_GAPS,
        *EMAIL_ADAPTER_TOOL_IDS,
        "manage_calendar",
        "manage_contact",
        "resolve_contact",
    }
)
_EXPERIMENTAL_IDS = frozenset({"delegate", "manage_subagents", "spawn_subagent"})

_FAMILY_MEMBERS: Mapping[str, frozenset[str]] = {
    "code_filesystem": frozenset(
        {
            "bash",
            "edit_file",
            "get_workspace",
            "glob",
            "grep",
            "ls",
            "python",
            "read_file",
            "verify_pygame_headless",
            "write_file",
        }
    ),
    "search_web": frozenset({"api_call", "web_fetch", "web_search"}),
    "knowledge_memory": frozenset(
        {
            "manage_embeddings",
            "manage_memory",
            "manage_personal_docs",
            "manage_research",
            "recent_changes",
            "trigger_research",
        }
    ),
    "documents_media": frozenset(
        {
            "create_document",
            "edit_document",
            "edit_image",
            "generate_image",
            "manage_documents",
            "publish_artifact",
            "suggest_document",
            "update_document",
        }
    ),
    "model_ops": frozenset(
        {
            "adopt_served_model",
            "cancel_download",
            "chat_with_model",
            "download_model",
            "list_cached_models",
            "list_cookbook_servers",
            "list_downloads",
            "list_models",
            "list_serve_presets",
            "list_served_models",
            "manage_endpoints",
            "manage_presets",
            "search_hf_models",
            "serve_model",
            "serve_preset",
            "stop_served_model",
            "tail_serve_output",
        }
    ),
    "projects_repositories": frozenset({"commit_project", "manage_repos"}),
    "orchestration_sessions": frozenset(
        {
            "ask_teacher",
            "ask_user",
            "create_session",
            "delegate",
            "list_sessions",
            "manage_bg_jobs",
            "manage_session",
            "manage_subagents",
            "pipeline",
            "search_chats",
            "send_to_session",
            "spawn_subagent",
            "update_plan",
        }
    ),
    "planning_communication": frozenset(
        {
            "archive_email",
            "bulk_email",
            "delete_email",
            "list_email_accounts",
            "list_emails",
            "manage_calendar",
            "manage_contact",
            "manage_notes",
            "manage_tasks",
            "mark_email_read",
            "read_email",
            "reply_to_email",
            "resolve_contact",
            "send_email",
        }
    ),
    "admin_system": frozenset(
        {
            "app_api",
            "manage_assistant",
            "manage_settings",
            "manage_tokens",
            "manage_webhooks",
            "ui_control",
        }
    ),
    "plugins_mcp": frozenset({"manage_mcp", "manage_plugins", "manage_skills"}),
    "external_providers": frozenset(
        {"manage_github_issues", "manage_nextcloud_transfer"}
    ),
}

_DESTRUCTIVE_IDS = frozenset({"delete_email"})
_EXTERNAL_WRITE_IDS = frozenset(
    {
        "archive_email",
        "bulk_email",
        "manage_calendar",
        "manage_contact",
        "manage_github_issues",
        "manage_nextcloud_transfer",
        "manage_webhooks",
        "mark_email_read",
        "reply_to_email",
        "send_email",
    }
)
_LOCAL_WRITE_IDS = frozenset(
    {
        "commit_project",
        "create_document",
        "create_session",
        "edit_document",
        "edit_file",
        "edit_image",
        "generate_image",
        "manage_documents",
        "manage_memory",
        "manage_notes",
        "manage_personal_docs",
        "manage_repos",
        "manage_session",
        "manage_tasks",
        "publish_artifact",
        "suggest_document",
        "update_document",
        "update_plan",
        "write_file",
    }
)
_CONTROL_IDS = frozenset(
    {
        "adopt_served_model",
        "app_api",
        "ask_teacher",
        "ask_user",
        "bash",
        "cancel_download",
        "delegate",
        "download_model",
        "manage_assistant",
        "manage_bg_jobs",
        "manage_embeddings",
        "manage_endpoints",
        "manage_mcp",
        "manage_plugins",
        "manage_presets",
        "manage_settings",
        "manage_skills",
        "manage_subagents",
        "manage_tokens",
        "pipeline",
        "python",
        "serve_model",
        "serve_preset",
        "spawn_subagent",
        "stop_served_model",
        "tail_serve_output",
        "trigger_research",
        "ui_control",
        "verify_pygame_headless",
    }
)
_DANGEROUS_IDS = frozenset(
    {
        "api_call",
        "bash",
        "bulk_email",
        "delete_email",
        "edit_file",
        "manage_mcp",
        "manage_plugins",
        "manage_repos",
        "manage_settings",
        "manage_tokens",
        "python",
        "reply_to_email",
        "send_email",
        "write_file",
    }
)
_ELEVATED_IDS = frozenset(
    {
        *_DEFERRED_PRIORITY_IDS,
        *_EXTERNAL_WRITE_IDS,
        *_LOCAL_WRITE_IDS,
        "read_file",
        "web_fetch",
        "web_search",
    }
) - _DANGEROUS_IDS


class CatalogProjectionError(ValueError):
    """Raised when a static built-in projection drifts from the catalog."""


@dataclass(frozen=True, slots=True)
class BuiltinToolDefinition:
    tool_id: str
    family: str
    lifecycle: str
    availability: str
    availability_reason: str | None
    risk_level: str
    permission: str
    effect_class: str
    runtime_registered: bool
    native_schema: bool
    static_prompt_section: bool
    handler_projection: str
    registration_disposition: str
    aliases: tuple[str, ...] = ()

    @property
    def display_name(self) -> str:
        return self.tool_id.replace("_", " ").title()

    @property
    def projection_exceptions(self) -> tuple[tuple[str, str], ...]:
        exceptions: list[tuple[str, str]] = []
        if not self.runtime_registered:
            exceptions.append(("runtime_tags", self.registration_disposition))
        if not self.native_schema:
            exceptions.append(("function_schemas", "non-native-image-schema-adapter"))
        if not self.static_prompt_section:
            exceptions.append(("prompt_sections", "index-injected-prompt"))
        if self.handler_projection == "email_adapter":
            exceptions.append(("dispatcher", "qualified-email-schema-adapter"))
        return tuple(exceptions)

    @property
    def parser_registered(self) -> bool:
        return self.registration_disposition in {"runtime", "active-gated"}


@dataclass(frozen=True, slots=True)
class BuiltinProjectionSnapshot:
    runtime_tags: frozenset[str]
    function_schemas: frozenset[str]
    tool_index: frozenset[str]
    prompt_sections: frozenset[str]
    dispatcher: frozenset[str]

    @classmethod
    def create(
        cls,
        *,
        runtime_tags: Iterable[str],
        function_schemas: Iterable[str],
        tool_index: Iterable[str],
        prompt_sections: Iterable[str],
        dispatcher: Iterable[str],
    ) -> "BuiltinProjectionSnapshot":
        return cls(
            runtime_tags=frozenset(runtime_tags),
            function_schemas=frozenset(function_schemas),
            tool_index=frozenset(tool_index),
            prompt_sections=frozenset(prompt_sections),
            dispatcher=frozenset(dispatcher),
        )


@dataclass(frozen=True, slots=True)
class BuiltinProjectionIssue:
    surface: str
    relation: str
    tool_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BuiltinProjectionReport:
    expected_counts: tuple[tuple[str, int], ...]
    actual_counts: tuple[tuple[str, int], ...]
    issues: tuple[BuiltinProjectionIssue, ...]

    @property
    def clean(self) -> bool:
        return not self.issues

    def assert_valid(self) -> None:
        if self.clean:
            return
        detail = "; ".join(
            f"{issue.surface}:{issue.relation}={','.join(issue.tool_ids)}"
            for issue in self.issues
        )
        raise CatalogProjectionError(f"built-in projection drift: {detail}")


def _family_for(tool_id: str) -> str:
    matches = [family for family, members in _FAMILY_MEMBERS.items() if tool_id in members]
    if len(matches) != 1:
        raise CatalogProjectionError(
            f"tool family must resolve exactly once: {tool_id} ({len(matches)})"
        )
    return matches[0]


def _effect_for(tool_id: str) -> str:
    if tool_id in _DESTRUCTIVE_IDS:
        return "destructive"
    if tool_id in _EXTERNAL_WRITE_IDS:
        return "external_write"
    if tool_id in _LOCAL_WRITE_IDS:
        return "local_write"
    if tool_id in _CONTROL_IDS:
        return "control"
    return "read"


def _risk_for(tool_id: str) -> str:
    if tool_id in _DANGEROUS_IDS:
        return "dangerous"
    if tool_id in _ELEVATED_IDS:
        return "elevated"
    return "safe"


def _permission_for(tool_id: str, risk_level: str, effect_class: str) -> str:
    if tool_id in RUNTIME_ADMIN_PERMISSION_IDS or risk_level == "dangerous":
        return "admin"
    if effect_class in {"external_write", "destructive"} or risk_level == "elevated":
        return "owner"
    return "user"


def _definition(tool_id: str) -> BuiltinToolDefinition:
    registration_disposition = (
        "active-gated"
        if tool_id in ACTIVE_GATED_REGISTRATION_IDS
        else "deferred"
        if tool_id in DEFERRED_REGISTRATION_IDS
        else "blocked-until-tax5"
        if tool_id in BLOCKED_REGISTRATION_IDS
        else "runtime"
    )
    lifecycle = (
        "experimental"
        if tool_id in _EXPERIMENTAL_IDS
        else "deferred"
        if tool_id in _DEFERRED_PRIORITY_IDS
        else "contextual"
    )
    availability = "disabled" if tool_id in RUNTIME_REGISTRATION_GAPS else "available"
    availability_reason = (
        f"registration-{registration_disposition}"
        if availability == "disabled"
        else None
    )
    effect_class = _effect_for(tool_id)
    risk_level = _risk_for(tool_id)
    return BuiltinToolDefinition(
        tool_id=tool_id,
        family=_family_for(tool_id),
        lifecycle=lifecycle,
        availability=availability,
        availability_reason=availability_reason,
        risk_level=risk_level,
        permission=_permission_for(tool_id, risk_level, effect_class),
        effect_class=effect_class,
        runtime_registered=tool_id not in RUNTIME_REGISTRATION_GAPS,
        native_schema=tool_id not in NON_NATIVE_SCHEMA_IDS,
        static_prompt_section=tool_id not in INDEX_INJECTED_PROMPT_IDS,
        handler_projection=(
            "email_adapter" if tool_id in EMAIL_ADAPTER_TOOL_IDS else "dispatcher"
        ),
        registration_disposition=registration_disposition,
        aliases=("manage_rag",) if tool_id == "manage_personal_docs" else (),
    )


BUILTIN_TOOL_DEFINITIONS = tuple(_definition(tool_id) for tool_id in _TOOL_IDS)
PARSER_REGISTERED_TOOL_IDS = frozenset(
    definition.tool_id
    for definition in BUILTIN_TOOL_DEFINITIONS
    if definition.parser_registered
)


def definitions_by_id() -> Mapping[str, BuiltinToolDefinition]:
    return {definition.tool_id: definition for definition in BUILTIN_TOOL_DEFINITIONS}


def resolve_operator_priority_disabled(
    configured_tool_ids: Iterable[str] = (),
    *,
    setting_present: bool,
) -> tuple[frozenset[str], bool]:
    """Resolve safe defaults without rewriting an explicit legacy setting.

    ``setting_present=False`` represents a new installation or settings file
    without a ``disabled_tools`` key.  Once that key exists, even an explicit
    empty list remains authoritative until the TAX9 migration can review it.
    """
    configured = frozenset(
        str(tool_id).strip()
        for tool_id in configured_tool_ids
        if str(tool_id).strip()
    )
    if setting_present:
        return configured, False
    return configured | OPERATOR_PRIORITY_DEFERRED_IDS, True


def expected_projection_sets() -> Mapping[str, frozenset[str]]:
    definitions = BUILTIN_TOOL_DEFINITIONS
    return {
        "runtime_tags": frozenset(
            item.tool_id for item in definitions if item.runtime_registered
        ),
        "function_schemas": frozenset(
            item.tool_id for item in definitions if item.native_schema
        ),
        "tool_index": frozenset(item.tool_id for item in definitions),
        "prompt_sections": frozenset(
            item.tool_id for item in definitions if item.static_prompt_section
        ),
        "dispatcher": frozenset(
            item.tool_id
            for item in definitions
            if item.handler_projection == "dispatcher"
        ),
    }


def validate_builtin_projections(
    snapshot: BuiltinProjectionSnapshot,
) -> BuiltinProjectionReport:
    expected = expected_projection_sets()
    actual = {
        "runtime_tags": snapshot.runtime_tags,
        "function_schemas": snapshot.function_schemas,
        "tool_index": snapshot.tool_index,
        "prompt_sections": snapshot.prompt_sections,
        "dispatcher": snapshot.dispatcher,
    }
    issues: list[BuiltinProjectionIssue] = []
    for surface in (
        "runtime_tags",
        "function_schemas",
        "tool_index",
        "prompt_sections",
        "dispatcher",
    ):
        expected_ids = expected[surface]
        actual_ids = actual[surface]
        allowed_extras = INTERNAL_DISPATCH_CONTROL_IDS if surface == "dispatcher" else frozenset()
        missing = tuple(sorted(expected_ids - actual_ids))
        unexpected = tuple(sorted(actual_ids - expected_ids - allowed_extras))
        if missing:
            issues.append(BuiltinProjectionIssue(surface, "missing", missing))
        if unexpected:
            issues.append(BuiltinProjectionIssue(surface, "unexpected", unexpected))
    return BuiltinProjectionReport(
        expected_counts=tuple((name, len(expected[name])) for name in sorted(expected)),
        actual_counts=tuple((name, len(actual[name])) for name in sorted(actual)),
        issues=tuple(issues),
    )


def build_builtin_descriptor_catalog():
    """Build Descriptor V2 objects without adding an agent-startup import."""
    from src.tool_catalog import (  # Deliberately lazy; see module docstring.
        ToolDescriptorCatalogV2,
        ToolDescriptorV2,
    )

    descriptors = []
    for definition in BUILTIN_TOOL_DEFINITIONS:
        native_schema = definition.native_schema
        descriptors.append(
            ToolDescriptorV2.create(
                tool_id=definition.tool_id,
                analytics_id=definition.tool_id,
                display_name=definition.display_name,
                description=(
                    f"Built-in {definition.display_name} capability in the "
                    f"{definition.family.replace('_', ' ')} family."
                ),
                family=definition.family,
                source="builtin",
                lifecycle=definition.lifecycle,
                availability=definition.availability,
                availability_reason=definition.availability_reason,
                default_enabled=(
                    definition.lifecycle == "contextual"
                    and definition.availability == "available"
                ),
                default_visibility="hidden",
                risk_level=definition.risk_level,
                permission=definition.permission,
                effect_class=definition.effect_class,
                requires_confirmation=(
                    definition.effect_class in {"external_write", "destructive"}
                    or definition.risk_level == "dangerous"
                    or definition.tool_id in ACTIVE_GATED_REGISTRATION_IDS
                ),
                schema_ref=f"function:{definition.tool_id}" if native_schema else None,
                handler_ref=(
                    f"email-adapter:{definition.tool_id}"
                    if definition.handler_projection == "email_adapter"
                    else f"dispatcher:{definition.tool_id}"
                ),
                prompt_ref=(
                    f"prompt:{definition.tool_id}"
                    if definition.static_prompt_section
                    else f"index:{definition.tool_id}"
                ),
                aliases=definition.aliases,
                feature_flag="builtin-tool-catalog-v2",
                introduced_in=CATALOG_VERSION,
                native_schema=native_schema,
                projection_exception_reason=(
                    None if native_schema else "non-native-image-schema-adapter"
                ),
            )
        )
    return ToolDescriptorCatalogV2.create(descriptors)


def build_tool_analytics_identity_contract():
    """Return the versioned public TAX identity resolver consumed by TUA."""

    from src.tool_catalog import ToolAnalyticsIdentityContractV1

    return ToolAnalyticsIdentityContractV1.create(
        build_builtin_descriptor_catalog(),
        historical_aliases=HISTORICAL_TOOL_ALIASES,
        retired_analytics_ids=RETIRED_TOOL_ANALYTICS_IDS,
    )


def resolve_tool_analytics_identity(tool_or_alias, *, source=None):
    """Resolve one runtime identity without exposing dynamic source identifiers."""

    return build_tool_analytics_identity_contract().resolve(
        tool_or_alias,
        source=source,
    )


def catalog_audit_summary() -> dict[str, object]:
    expected = expected_projection_sets()
    return {
        "catalog_version": CATALOG_VERSION,
        "builtin_count": len(BUILTIN_TOOL_DEFINITIONS),
        "projection_counts": {
            name: len(tool_ids) for name, tool_ids in sorted(expected.items())
        },
        "runtime_registration_gap_ids": tuple(sorted(RUNTIME_REGISTRATION_GAPS)),
        "active_gated_registration_ids": tuple(
            sorted(ACTIVE_GATED_REGISTRATION_IDS)
        ),
        "deferred_registration_ids": tuple(sorted(DEFERRED_REGISTRATION_IDS)),
        "blocked_registration_ids": tuple(sorted(BLOCKED_REGISTRATION_IDS)),
        "operator_priority_deferred_ids": tuple(
            sorted(OPERATOR_PRIORITY_DEFERRED_IDS)
        ),
        "parser_registered_count": len(PARSER_REGISTERED_TOOL_IDS),
        "non_native_schema_ids": tuple(sorted(NON_NATIVE_SCHEMA_IDS)),
        "email_adapter_ids": tuple(sorted(EMAIL_ADAPTER_TOOL_IDS)),
        "index_injected_prompt_ids": tuple(sorted(INDEX_INJECTED_PROMPT_IDS)),
        "internal_dispatch_control_ids": tuple(sorted(INTERNAL_DISPATCH_CONTROL_IDS)),
        "raw_content_visible": False,
        "schema_arguments_visible": False,
        "secret_value_visible": False,
    }


def _validate_catalog_definition() -> None:
    ids = tuple(item.tool_id for item in BUILTIN_TOOL_DEFINITIONS)
    if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
        raise CatalogProjectionError("built-in definitions must be unique and sorted")
    if set(ids) != set().union(*_FAMILY_MEMBERS.values()):
        raise CatalogProjectionError("family membership must cover the catalog exactly")
    gap_dispositions = (
        ACTIVE_GATED_REGISTRATION_IDS
        | DEFERRED_REGISTRATION_IDS
        | BLOCKED_REGISTRATION_IDS
    )
    if gap_dispositions != RUNTIME_REGISTRATION_GAPS:
        raise CatalogProjectionError("registration dispositions must cover all six gaps")
    if any(
        (
            ACTIVE_GATED_REGISTRATION_IDS & DEFERRED_REGISTRATION_IDS,
            ACTIVE_GATED_REGISTRATION_IDS & BLOCKED_REGISTRATION_IDS,
            DEFERRED_REGISTRATION_IDS & BLOCKED_REGISTRATION_IDS,
        )
    ):
        raise CatalogProjectionError("registration dispositions must be disjoint")
    if len(PARSER_REGISTERED_TOOL_IDS) != 81:
        raise CatalogProjectionError("parser registration baseline must contain 81 tools")
    expected_counts = {
        "runtime_tags": 78,
        "function_schemas": 83,
        "tool_index": 84,
        "prompt_sections": 68,
        "dispatcher": 75,
    }
    actual_counts = {
        name: len(values) for name, values in expected_projection_sets().items()
    }
    if actual_counts != expected_counts:
        raise CatalogProjectionError(
            f"built-in projection baseline mismatch: {actual_counts!r}"
        )


_validate_catalog_definition()
