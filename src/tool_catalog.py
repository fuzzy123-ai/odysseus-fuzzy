"""Small backend contract for tool catalog visibility and selection."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from enum import StrEnum
import json
import re
from typing import Any, ClassVar, Iterable, Mapping

from src.agent_identity import AgentIdentity
from src.context_capsule import ContextCapsule


_MAX_ID_LENGTH = 80
_MAX_SUMMARY_CHARS = 140
_MAX_SCHEMA_REF_LENGTH = 120
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")
_TOOL_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
_DESCRIPTOR_ID_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,119}$")
_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
_VERSION_RE = re.compile(r"^[0-9]+(?:\.(?:[0-9]+|x)){1,2}(?:[-+][A-Za-z0-9.-]+)?$")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(?:api[_ -]?key|access[_ -]?token|secret|password|credential)\s*[:=]"
)
_PRIVATE_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|/home/|/Users/|/root/|\\\\)")

TOOL_SETTINGS_SCHEMA_VERSION = 1
TOOL_SETTINGS_SCHEMA_KEY = "tool_settings_schema_version"
TOOL_SETTINGS_MIGRATION_KEY = "tool_settings_migration"
TOOL_SETTINGS_QUARANTINE_KEY = "disabled_tools_quarantine"


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


class ToolFamily(StrEnum):
    CODE_FILESYSTEM = "code_filesystem"
    SEARCH_WEB = "search_web"
    KNOWLEDGE_MEMORY = "knowledge_memory"
    DOCUMENTS_MEDIA = "documents_media"
    MODEL_OPS = "model_ops"
    PROJECTS_REPOSITORIES = "projects_repositories"
    ORCHESTRATION_SESSIONS = "orchestration_sessions"
    PLANNING_COMMUNICATION = "planning_communication"
    ADMIN_SYSTEM = "admin_system"
    PLUGINS_MCP = "plugins_mcp"
    EXTERNAL_PROVIDERS = "external_providers"
    EXPERIMENTAL = "experimental"
    UNCLASSIFIED_DYNAMIC = "unclassified_dynamic"


class ToolSource(StrEnum):
    BUILTIN = "builtin"
    PLUGIN = "plugin"
    MCP = "mcp"
    PROVIDER = "provider"
    LEGACY = "legacy"


class ToolAnalyticsResolution(StrEnum):
    CANONICAL = "canonical"
    HISTORICAL_ALIAS = "historical_alias"
    SOURCE_BUCKET = "source_bucket"
    LEGACY_BUCKET = "legacy_bucket"


class ToolLifecycle(StrEnum):
    ACTIVE = "active"
    CONTEXTUAL = "contextual"
    DEFERRED = "deferred"
    EXPERIMENTAL = "experimental"
    DEPRECATED = "deprecated"
    BLOCKED = "blocked"


class ToolAvailability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    NOT_CONFIGURED = "not_configured"
    DISABLED = "disabled"
    DEGRADED = "degraded"


class ToolEffectClass(StrEnum):
    READ = "read"
    LOCAL_WRITE = "local_write"
    EXTERNAL_WRITE = "external_write"
    DESTRUCTIVE = "destructive"
    CONTROL = "control"


class ToolPermission(StrEnum):
    PUBLIC = "public"
    USER = "user"
    OWNER = "owner"
    ADMIN = "admin"
    SYSTEM = "system"


_LIFECYCLE_TRANSITIONS: Mapping[ToolLifecycle, frozenset[ToolLifecycle]] = {
    ToolLifecycle.ACTIVE: frozenset(
        {
            ToolLifecycle.CONTEXTUAL,
            ToolLifecycle.DEFERRED,
            ToolLifecycle.DEPRECATED,
            ToolLifecycle.BLOCKED,
        }
    ),
    ToolLifecycle.CONTEXTUAL: frozenset(
        {
            ToolLifecycle.ACTIVE,
            ToolLifecycle.DEFERRED,
            ToolLifecycle.DEPRECATED,
            ToolLifecycle.BLOCKED,
        }
    ),
    ToolLifecycle.DEFERRED: frozenset(
        {
            ToolLifecycle.ACTIVE,
            ToolLifecycle.CONTEXTUAL,
            ToolLifecycle.DEPRECATED,
            ToolLifecycle.BLOCKED,
        }
    ),
    ToolLifecycle.EXPERIMENTAL: frozenset(
        {
            ToolLifecycle.ACTIVE,
            ToolLifecycle.CONTEXTUAL,
            ToolLifecycle.DEFERRED,
            ToolLifecycle.DEPRECATED,
            ToolLifecycle.BLOCKED,
        }
    ),
    ToolLifecycle.DEPRECATED: frozenset({ToolLifecycle.BLOCKED}),
    ToolLifecycle.BLOCKED: frozenset(),
}


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


def resolve_tool_identifier_for_migration(
    value: Any,
    *,
    known_tool_ids: Iterable[Any],
    alias_targets: Mapping[Any, Any] | None = None,
) -> ToolIdentifierResolution:
    """Resolve a stored tool ID without inventing runtime capabilities.

    Alias targets must name a known canonical tool. ``manage_rag`` deliberately
    has no default target: it was a stale UI identifier, not a second handler.
    A future evidence-backed alias may still be supplied explicitly.
    """

    supplied_id = _normalize_tool_id(value, field_name="stored_tool_id")
    known = {
        _normalize_tool_id(item, field_name="known_tool_id")
        for item in known_tool_ids
    }
    aliases: dict[str, str] = {}
    for source, target in (alias_targets or {}).items():
        normalized_source = _normalize_tool_id(source, field_name="alias_source")
        normalized_target = _normalize_tool_id(target, field_name="alias_target")
        if normalized_source in known:
            raise ToolCatalogError("alias source collides with a canonical tool ID")
        if normalized_target not in known:
            raise ToolCatalogError("alias target must be a known canonical tool ID")
        aliases[normalized_source] = normalized_target

    if supplied_id in known:
        return ToolIdentifierResolution(
            supplied_id=supplied_id,
            canonical_id=supplied_id,
            disposition=ToolIdentifierDisposition.CANONICAL,
            reason_code="canonical_tool_id",
        )
    if supplied_id in aliases:
        return ToolIdentifierResolution(
            supplied_id=supplied_id,
            canonical_id=aliases[supplied_id],
            disposition=ToolIdentifierDisposition.ALIAS,
            reason_code="legacy_alias_resolved",
        )
    if supplied_id in LEGACY_NON_RUNTIME_TOOL_IDS:
        return ToolIdentifierResolution(
            supplied_id=supplied_id,
            canonical_id=None,
            disposition=ToolIdentifierDisposition.LEGACY_NON_RUNTIME,
            reason_code=LEGACY_NON_RUNTIME_TOOL_IDS[supplied_id],
        )
    return ToolIdentifierResolution(
        supplied_id=supplied_id,
        canonical_id=None,
        disposition=ToolIdentifierDisposition.UNKNOWN,
        reason_code="unknown_tool_id",
    )


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


def _controlled_enum(enum_type, value: Any, *, field_name: str):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except (TypeError, ValueError) as exc:
        raise ToolCatalogError(f"{field_name} is not a controlled value") from exc


def _strict_descriptor_id(value: Any, *, field_name: str) -> str:
    if callable(value):
        raise ToolCatalogError(f"{field_name} must not be callable")
    text = str(value or "")
    if not text or text != text.strip() or not _DESCRIPTOR_ID_RE.fullmatch(text):
        raise ToolCatalogError(f"{field_name} must be a stable lowercase technical id")
    return text


def _safe_descriptor_text(value: Any, *, field_name: str, limit: int) -> str:
    if callable(value):
        raise ToolCatalogError(f"{field_name} must not be callable")
    text = " ".join(str(value or "").split())
    if not text:
        raise ToolCatalogError(f"{field_name} must not be empty")
    if len(text) > limit:
        raise ToolCatalogError(f"{field_name} exceeds max length {limit}")
    if _SECRET_ASSIGNMENT_RE.search(text) or _PRIVATE_PATH_RE.search(text):
        raise ToolCatalogError(f"{field_name} contains secret-like or private-path content")
    return text


def _safe_optional_code(value: Any, *, field_name: str) -> str | None:
    if value is None or value == "":
        return None
    if callable(value):
        raise ToolCatalogError(f"{field_name} must not be callable")
    text = str(value)
    if text != text.strip() or not _REFERENCE_RE.fullmatch(text):
        raise ToolCatalogError(f"{field_name} must be a content-free reference")
    return text


def _version(value: Any, *, field_name: str, required: bool) -> str | None:
    if value is None or value == "":
        if required:
            raise ToolCatalogError(f"{field_name} must not be empty")
        return None
    if callable(value):
        raise ToolCatalogError(f"{field_name} must not be callable")
    text = str(value)
    if text != text.strip() or not _VERSION_RE.fullmatch(text):
        raise ToolCatalogError(f"{field_name} must be a machine-readable version")
    return text


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


@dataclass(frozen=True, slots=True)
class ToolDescriptorV2:
    """Canonical, content-free descriptor contract for every tool source."""

    CONTRACT_ID: ClassVar[str] = "odysseus.tool_descriptor.v2"

    tool_id: str
    analytics_id: str
    display_name: str
    description: str
    family: ToolFamily
    source: ToolSource
    source_id: str | None
    lifecycle: ToolLifecycle
    availability: ToolAvailability
    availability_reason: str | None
    default_enabled: bool
    default_visibility: ToolVisibility
    risk_level: ToolRiskLevel
    permission: ToolPermission
    effect_class: ToolEffectClass
    requires_confirmation: bool
    schema_ref: str | None
    handler_ref: str
    prompt_ref: str | None
    aliases: tuple[str, ...]
    feature_flag: str | None
    introduced_in: str
    deprecated_in: str | None
    native_schema: bool
    projection_exception_reason: str | None

    def __post_init__(self) -> None:
        if _strict_descriptor_id(self.tool_id, field_name="tool_id") != self.tool_id:
            raise ToolCatalogError("tool_id is not canonical")
        if _strict_descriptor_id(self.analytics_id, field_name="analytics_id") != self.analytics_id:
            raise ToolCatalogError("analytics_id is not canonical")
        if _safe_descriptor_text(self.display_name, field_name="display_name", limit=80) != self.display_name:
            raise ToolCatalogError("display_name is not canonical")
        if _safe_descriptor_text(self.description, field_name="description", limit=240) != self.description:
            raise ToolCatalogError("description is not canonical")
        for field_name, enum_type in (
            ("family", ToolFamily),
            ("source", ToolSource),
            ("lifecycle", ToolLifecycle),
            ("availability", ToolAvailability),
            ("default_visibility", ToolVisibility),
            ("risk_level", ToolRiskLevel),
            ("permission", ToolPermission),
            ("effect_class", ToolEffectClass),
        ):
            if not isinstance(getattr(self, field_name), enum_type):
                raise ToolCatalogError(f"{field_name} must use its controlled enum")
        for field_name in ("default_enabled", "requires_confirmation", "native_schema"):
            if not isinstance(getattr(self, field_name), bool):
                raise ToolCatalogError(f"{field_name} must be boolean")

        normalized_source_id = (
            _strict_descriptor_id(self.source_id, field_name="source_id")
            if self.source_id is not None
            else None
        )
        if normalized_source_id != self.source_id:
            raise ToolCatalogError("source_id is not canonical")
        if self.source in {ToolSource.PLUGIN, ToolSource.MCP, ToolSource.PROVIDER} and not self.source_id:
            raise ToolCatalogError("dynamic and provider sources require a redacted source_id")
        if self.source == ToolSource.BUILTIN and self.source_id is not None:
            raise ToolCatalogError("builtin sources must not invent a source_id")

        reason = _safe_optional_code(self.availability_reason, field_name="availability_reason")
        if reason != self.availability_reason:
            raise ToolCatalogError("availability_reason is not canonical")
        if self.availability == ToolAvailability.AVAILABLE and self.availability_reason is not None:
            raise ToolCatalogError("available tools must not carry an unavailable reason")
        if self.availability != ToolAvailability.AVAILABLE and not self.availability_reason:
            raise ToolCatalogError("non-available tools require a content-free reason code")

        for field_name in ("schema_ref", "handler_ref", "prompt_ref", "feature_flag", "projection_exception_reason"):
            normalized = _safe_optional_code(getattr(self, field_name), field_name=field_name)
            if normalized != getattr(self, field_name):
                raise ToolCatalogError(f"{field_name} is not canonical")
        if not self.handler_ref:
            raise ToolCatalogError("handler_ref must not be empty")
        if self.lifecycle in {ToolLifecycle.ACTIVE, ToolLifecycle.CONTEXTUAL} and not self.prompt_ref:
            raise ToolCatalogError("active/contextual tools require a prompt_ref")

        if self.native_schema and not self.schema_ref:
            raise ToolCatalogError("native-schema tools require a schema_ref")
        if self.native_schema and self.projection_exception_reason:
            raise ToolCatalogError("native-schema tools must not carry a projection exception")
        if not self.native_schema and self.schema_ref:
            raise ToolCatalogError("non-native tools must not carry a schema_ref")
        if not self.native_schema and not self.projection_exception_reason:
            raise ToolCatalogError("non-native tools require a projection exception reason")

        normalized_aliases = tuple(
            sorted(
                {
                    _strict_descriptor_id(alias, field_name="alias")
                    for alias in self.aliases
                }
            )
        )
        if normalized_aliases != self.aliases:
            raise ToolCatalogError("aliases must be unique and sorted")
        if self.tool_id in self.aliases:
            raise ToolCatalogError("aliases must not repeat the canonical tool_id")

        introduced = _version(self.introduced_in, field_name="introduced_in", required=True)
        deprecated = _version(self.deprecated_in, field_name="deprecated_in", required=False)
        if introduced != self.introduced_in or deprecated != self.deprecated_in:
            raise ToolCatalogError("version fields are not canonical")
        if self.lifecycle == ToolLifecycle.DEPRECATED and not self.deprecated_in:
            raise ToolCatalogError("deprecated tools require deprecated_in")
        if self.lifecycle not in {ToolLifecycle.DEPRECATED, ToolLifecycle.BLOCKED} and self.deprecated_in:
            raise ToolCatalogError("only deprecated/blocked tools may carry deprecated_in")

        if self.lifecycle in {
            ToolLifecycle.DEFERRED,
            ToolLifecycle.EXPERIMENTAL,
            ToolLifecycle.DEPRECATED,
            ToolLifecycle.BLOCKED,
        } and self.default_enabled:
            raise ToolCatalogError("deferred/experimental/deprecated/blocked tools default disabled")
        if self.availability != ToolAvailability.AVAILABLE and self.default_enabled:
            raise ToolCatalogError("non-available tools default disabled")
        if self.lifecycle in {
            ToolLifecycle.DEFERRED,
            ToolLifecycle.EXPERIMENTAL,
            ToolLifecycle.DEPRECATED,
        } and self.default_visibility == ToolVisibility.VISIBLE:
            raise ToolCatalogError("non-active lifecycle must not default visible")
        if self.lifecycle == ToolLifecycle.BLOCKED and self.default_visibility not in {
            ToolVisibility.BLOCKED,
            ToolVisibility.UNAVAILABLE,
        }:
            raise ToolCatalogError("blocked tools require blocked/unavailable visibility")
        if self.effect_class in {ToolEffectClass.EXTERNAL_WRITE, ToolEffectClass.DESTRUCTIVE} and not self.requires_confirmation:
            raise ToolCatalogError("external/destructive effects require confirmation")

    @classmethod
    def create(
        cls,
        *,
        tool_id: str,
        display_name: str,
        description: str,
        family: ToolFamily | str,
        source: ToolSource | str,
        lifecycle: ToolLifecycle | str,
        availability: ToolAvailability | str,
        default_enabled: bool,
        default_visibility: ToolVisibility | str,
        risk_level: ToolRiskLevel | str,
        permission: ToolPermission | str,
        effect_class: ToolEffectClass | str,
        requires_confirmation: bool,
        schema_ref: str | None,
        handler_ref: str,
        prompt_ref: str | None,
        introduced_in: str,
        analytics_id: str | None = None,
        source_id: str | None = None,
        availability_reason: str | None = None,
        aliases: Iterable[str] = (),
        feature_flag: str | None = None,
        deprecated_in: str | None = None,
        native_schema: bool = True,
        projection_exception_reason: str | None = None,
    ) -> "ToolDescriptorV2":
        canonical_tool_id = _strict_descriptor_id(tool_id, field_name="tool_id")
        alias_values = tuple(
            sorted(
                {
                    _strict_descriptor_id(alias, field_name="alias")
                    for alias in aliases
                }
            )
        )
        return cls(
            tool_id=canonical_tool_id,
            analytics_id=_strict_descriptor_id(
                analytics_id if analytics_id is not None else canonical_tool_id,
                field_name="analytics_id",
            ),
            display_name=_safe_descriptor_text(display_name, field_name="display_name", limit=80),
            description=_safe_descriptor_text(description, field_name="description", limit=240),
            family=_controlled_enum(ToolFamily, family, field_name="family"),
            source=_controlled_enum(ToolSource, source, field_name="source"),
            source_id=(
                _strict_descriptor_id(source_id, field_name="source_id")
                if source_id is not None
                else None
            ),
            lifecycle=_controlled_enum(ToolLifecycle, lifecycle, field_name="lifecycle"),
            availability=_controlled_enum(ToolAvailability, availability, field_name="availability"),
            availability_reason=_safe_optional_code(
                availability_reason, field_name="availability_reason"
            ),
            default_enabled=default_enabled,
            default_visibility=_controlled_enum(
                ToolVisibility, default_visibility, field_name="default_visibility"
            ),
            risk_level=_controlled_enum(ToolRiskLevel, risk_level, field_name="risk_level"),
            permission=_controlled_enum(ToolPermission, permission, field_name="permission"),
            effect_class=_controlled_enum(ToolEffectClass, effect_class, field_name="effect_class"),
            requires_confirmation=requires_confirmation,
            schema_ref=_safe_optional_code(schema_ref, field_name="schema_ref"),
            handler_ref=str(_safe_optional_code(handler_ref, field_name="handler_ref") or ""),
            prompt_ref=_safe_optional_code(prompt_ref, field_name="prompt_ref"),
            aliases=alias_values,
            feature_flag=_safe_optional_code(feature_flag, field_name="feature_flag"),
            introduced_in=str(_version(introduced_in, field_name="introduced_in", required=True)),
            deprecated_in=_version(deprecated_in, field_name="deprecated_in", required=False),
            native_schema=native_schema,
            projection_exception_reason=_safe_optional_code(
                projection_exception_reason,
                field_name="projection_exception_reason",
            ),
        )

    @classmethod
    def conservative_dynamic(
        cls,
        *,
        tool_id: str,
        source: ToolSource | str,
        source_id: str,
        display_name: str | None = None,
        description: str = "Unclassified dynamic tool; unavailable until reviewed.",
        introduced_in: str = "0.24.0",
    ) -> "ToolDescriptorV2":
        source_value = _controlled_enum(ToolSource, source, field_name="source")
        if source_value not in {ToolSource.PLUGIN, ToolSource.MCP, ToolSource.PROVIDER}:
            raise ToolCatalogError("conservative dynamic defaults require plugin, mcp or provider source")
        canonical_tool_id = _strict_descriptor_id(tool_id, field_name="tool_id")
        return cls.create(
            tool_id=canonical_tool_id,
            analytics_id=canonical_tool_id,
            display_name=display_name or canonical_tool_id.replace("_", " ").replace("-", " ").title(),
            description=description,
            family=ToolFamily.UNCLASSIFIED_DYNAMIC,
            source=source_value,
            source_id=source_id,
            lifecycle=ToolLifecycle.EXPERIMENTAL,
            availability=ToolAvailability.UNAVAILABLE,
            availability_reason="unclassified-dynamic-source",
            default_enabled=False,
            default_visibility=ToolVisibility.UNAVAILABLE,
            risk_level=ToolRiskLevel.DANGEROUS,
            permission=ToolPermission.ADMIN,
            effect_class=ToolEffectClass.CONTROL,
            requires_confirmation=True,
            schema_ref=f"dynamic:{canonical_tool_id}",
            handler_ref=f"registry:{canonical_tool_id}",
            prompt_ref=None,
            aliases=(),
            feature_flag=None,
            introduced_in=introduced_in,
        )

    @classmethod
    def from_v1_manifest(
        cls,
        manifest: ToolManifest | Mapping[str, Any],
        *,
        introduced_in: str = "0.24.0",
        source: ToolSource | str = ToolSource.BUILTIN,
        source_id: str | None = None,
        aliases: Iterable[str] = (),
    ) -> "ToolDescriptorV2":
        if isinstance(manifest, ToolManifest):
            tool_id = manifest.tool_id
            family = manifest.family
            description = manifest.short_description
            capabilities = manifest.capabilities
            risk = manifest.risk_level
            schema_ref = manifest.schema_ref
            visibility = manifest.visibility_state
        elif isinstance(manifest, Mapping):
            tool_id = manifest.get("tool_id")
            family = manifest.get("family")
            description = manifest.get("short_description", manifest.get("description"))
            capabilities = manifest.get("capabilities") or ()
            risk = manifest.get("risk_level")
            schema_ref = manifest.get("schema_ref")
            visibility = manifest.get("visibility_state", ToolVisibility.HIDDEN)
        else:
            raise ToolCatalogError("v1 manifest must be ToolManifest or mapping")

        canonical_tool_id = _strict_descriptor_id(tool_id, field_name="tool_id")
        visibility_value = _controlled_enum(
            ToolVisibility, visibility, field_name="visibility_state"
        )
        risk_value = _controlled_enum(ToolRiskLevel, risk, field_name="risk_level")
        capability_values = _normalize_slug_list(
            capabilities, field_name="capability", allow_empty=False
        )
        blocked = visibility_value in {ToolVisibility.BLOCKED, ToolVisibility.UNAVAILABLE}
        unavailable = visibility_value == ToolVisibility.UNAVAILABLE
        effect = _effect_from_v1(capability_values, risk_value)
        return cls.create(
            tool_id=canonical_tool_id,
            analytics_id=canonical_tool_id,
            display_name=canonical_tool_id.replace("_", " ").replace("-", " ").title(),
            description=description,
            family=_family_from_v1(str(family or "")),
            source=source,
            source_id=source_id,
            lifecycle=ToolLifecycle.BLOCKED if blocked else ToolLifecycle.CONTEXTUAL,
            availability=ToolAvailability.UNAVAILABLE if unavailable else (
                ToolAvailability.DISABLED if blocked else ToolAvailability.AVAILABLE
            ),
            availability_reason=(
                "v1-visibility-unavailable" if unavailable else (
                    "v1-visibility-blocked" if blocked else None
                )
            ),
            default_enabled=not blocked,
            default_visibility=visibility_value,
            risk_level=risk_value,
            permission=(
                ToolPermission.ADMIN
                if risk_value == ToolRiskLevel.DANGEROUS
                else ToolPermission.OWNER
                if risk_value == ToolRiskLevel.ELEVATED
                else ToolPermission.USER
            ),
            effect_class=effect,
            requires_confirmation=(
                risk_value == ToolRiskLevel.DANGEROUS
                or effect in {ToolEffectClass.EXTERNAL_WRITE, ToolEffectClass.DESTRUCTIVE}
            ),
            schema_ref=schema_ref,
            handler_ref=f"builtin:{canonical_tool_id}",
            prompt_ref=f"index:{canonical_tool_id}",
            aliases=aliases,
            introduced_in=introduced_in,
        )

    def transition_lifecycle(
        self,
        lifecycle: ToolLifecycle | str,
        *,
        changed_in: str,
    ) -> "ToolDescriptorV2":
        target = _controlled_enum(ToolLifecycle, lifecycle, field_name="lifecycle")
        _version(changed_in, field_name="changed_in", required=True)
        if target == self.lifecycle:
            return self
        if target not in _LIFECYCLE_TRANSITIONS[self.lifecycle]:
            raise ToolCatalogError(
                f"lifecycle transition {self.lifecycle.value}->{target.value} is not allowed"
            )
        updates: dict[str, Any] = {"lifecycle": target}
        if target == ToolLifecycle.DEPRECATED:
            updates.update(
                deprecated_in=changed_in,
                default_enabled=False,
                default_visibility=ToolVisibility.HIDDEN,
            )
        elif target == ToolLifecycle.BLOCKED:
            updates.update(
                default_enabled=False,
                default_visibility=ToolVisibility.BLOCKED,
                availability=ToolAvailability.DISABLED,
                availability_reason="lifecycle-blocked",
            )
        elif target in {ToolLifecycle.DEFERRED, ToolLifecycle.EXPERIMENTAL}:
            updates.update(
                default_enabled=False,
                default_visibility=ToolVisibility.HIDDEN,
            )
        return replace(self, **updates)

    def audit_dict(self) -> dict[str, Any]:
        return {
            "contract": self.CONTRACT_ID,
            "tool_id": self.tool_id,
            "analytics_id": self.analytics_id,
            "display_name": self.display_name,
            "description": self.description,
            "family": self.family.value,
            "source": self.source.value,
            "source_id": self.source_id,
            "lifecycle": self.lifecycle.value,
            "availability": self.availability.value,
            "availability_reason": self.availability_reason,
            "default_enabled": self.default_enabled,
            "default_visibility": self.default_visibility.value,
            "risk_level": self.risk_level.value,
            "permission": self.permission.value,
            "effect_class": self.effect_class.value,
            "requires_confirmation": self.requires_confirmation,
            "schema_ref": self.schema_ref,
            "handler_ref": self.handler_ref,
            "prompt_ref": self.prompt_ref,
            "aliases": self.aliases,
            "feature_flag": self.feature_flag,
            "introduced_in": self.introduced_in,
            "deprecated_in": self.deprecated_in,
            "native_schema": self.native_schema,
            "projection_exception_reason": self.projection_exception_reason,
            "callable_visible": False,
            "arguments_visible": False,
            "raw_content_visible": False,
            "secret_value_visible": False,
        }

    def audit_json(self) -> str:
        return json.dumps(self.audit_dict(), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class ToolDescriptorCatalogV2:
    descriptors: tuple[ToolDescriptorV2, ...]
    aliases: tuple[tuple[str, str], ...]

    @classmethod
    def create(cls, descriptors: Iterable[ToolDescriptorV2]) -> "ToolDescriptorCatalogV2":
        ordered = tuple(sorted(descriptors, key=lambda item: item.tool_id))
        if not all(isinstance(item, ToolDescriptorV2) for item in ordered):
            raise ToolCatalogError("catalog entries must be ToolDescriptorV2")
        canonical_ids = [item.tool_id for item in ordered]
        analytics_ids = [item.analytics_id for item in ordered]
        if len(set(canonical_ids)) != len(canonical_ids):
            raise ToolCatalogError("canonical tool_id collision")
        if len(set(analytics_ids)) != len(analytics_ids):
            raise ToolCatalogError("analytics_id collision")

        canonical = set(canonical_ids)
        alias_map: dict[str, str] = {}
        for item in ordered:
            for alias in item.aliases:
                if alias in canonical:
                    raise ToolCatalogError("alias collides with a canonical tool_id")
                if alias in alias_map:
                    raise ToolCatalogError("alias collision")
                alias_map[alias] = item.tool_id
        return cls(
            descriptors=ordered,
            aliases=tuple(sorted(alias_map.items())),
        )

    @classmethod
    def from_v1_manifests(
        cls,
        manifests: Iterable[ToolManifest | Mapping[str, Any]],
        *,
        introduced_in: str = "0.24.0",
    ) -> "ToolDescriptorCatalogV2":
        descriptors = (
            ToolDescriptorV2.from_v1_manifest(
                manifest,
                introduced_in=introduced_in,
            )
            for manifest in manifests
        )
        return cls.create(descriptors)

    def resolve(self, tool_or_alias: str) -> ToolDescriptorV2:
        identity = _strict_descriptor_id(tool_or_alias, field_name="tool_or_alias")
        canonical = dict(self.aliases).get(identity, identity)
        for descriptor in self.descriptors:
            if descriptor.tool_id == canonical:
                return descriptor
        raise ToolCatalogError("unknown tool identity")

    def audit_summary(self) -> dict[str, Any]:
        return {
            "contract": ToolDescriptorV2.CONTRACT_ID,
            "descriptor_count": len(self.descriptors),
            "alias_count": len(self.aliases),
            "descriptors": tuple(item.audit_dict() for item in self.descriptors),
            "aliases": self.aliases,
            "raw_content_visible": False,
            "secret_value_visible": False,
        }


_TOOL_ANALYTICS_SOURCE_BUCKETS: Mapping[ToolSource, str] = {
    ToolSource.PLUGIN: "dynamic.plugin.unclassified",
    ToolSource.MCP: "dynamic.mcp.unclassified",
    ToolSource.PROVIDER: "dynamic.provider.unclassified",
    ToolSource.LEGACY: "legacy.unclassified",
}


@dataclass(frozen=True, slots=True)
class ToolAnalyticsIdentityV1:
    """Content-free identity packet consumed by usage analytics."""

    CONTRACT_ID: ClassVar[str] = "odysseus.tool_analytics_identity.v1"

    analytics_id: str
    family: ToolFamily
    source: ToolSource
    resolution: ToolAnalyticsResolution
    canonical_tool_id: str | None
    alias_applied: bool
    source_bucket: bool

    def __post_init__(self) -> None:
        if _strict_descriptor_id(self.analytics_id, field_name="analytics_id") != self.analytics_id:
            raise ToolCatalogError("analytics_id is not canonical")
        if not isinstance(self.family, ToolFamily):
            raise ToolCatalogError("family must use the controlled ToolFamily enum")
        if not isinstance(self.source, ToolSource):
            raise ToolCatalogError("source must use the controlled ToolSource enum")
        if not isinstance(self.resolution, ToolAnalyticsResolution):
            raise ToolCatalogError("resolution must use the controlled analytics enum")
        if not isinstance(self.alias_applied, bool) or not isinstance(self.source_bucket, bool):
            raise ToolCatalogError("analytics identity flags must be boolean")
        if self.canonical_tool_id is not None:
            if _strict_descriptor_id(
                self.canonical_tool_id,
                field_name="canonical_tool_id",
            ) != self.canonical_tool_id:
                raise ToolCatalogError("canonical_tool_id is not canonical")
        if self.source_bucket:
            if self.canonical_tool_id is not None or self.alias_applied:
                raise ToolCatalogError("source buckets must not carry runtime or alias identities")
            if self.family != ToolFamily.UNCLASSIFIED_DYNAMIC:
                raise ToolCatalogError("source buckets must remain unclassified")
        elif self.canonical_tool_id is None:
            raise ToolCatalogError("catalog identities require a canonical_tool_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract": self.CONTRACT_ID,
            "analytics_id": self.analytics_id,
            "family": self.family.value,
            "source": self.source.value,
            "resolution": self.resolution.value,
            "canonical_tool_id": self.canonical_tool_id,
            "alias_applied": self.alias_applied,
            "source_bucket": self.source_bucket,
            "owner_identity_visible": False,
            "session_identity_visible": False,
            "source_identity_visible": False,
            "raw_content_visible": False,
        }

    def to_event_fields(self) -> dict[str, str]:
        """Return exactly the three TAX-owned fields in ToolUsageEventV1."""

        return {
            "tool_analytics_id": self.analytics_id,
            "tool_family": self.family.value,
            "tool_source": self.source.value,
        }


@dataclass(frozen=True, slots=True)
class ToolAnalyticsIdentityContractV1:
    """Versioned resolver joining TAX descriptors to the TUA event contract."""

    CONTRACT_ID: ClassVar[str] = ToolAnalyticsIdentityV1.CONTRACT_ID

    catalog: ToolDescriptorCatalogV2
    historical_aliases: tuple[tuple[str, str], ...]
    retired_analytics_ids: tuple[str, ...]

    @classmethod
    def create(
        cls,
        catalog: ToolDescriptorCatalogV2,
        *,
        historical_aliases: Mapping[str, str] | Iterable[tuple[str, str]] = (),
        retired_analytics_ids: Iterable[str] = (),
    ) -> "ToolAnalyticsIdentityContractV1":
        if not isinstance(catalog, ToolDescriptorCatalogV2):
            raise ToolCatalogError("analytics contract requires a Descriptor-v2 catalog")
        canonical_ids = {descriptor.tool_id for descriptor in catalog.descriptors}
        alias_map = dict(catalog.aliases)
        supplied_aliases = (
            historical_aliases.items()
            if isinstance(historical_aliases, Mapping)
            else historical_aliases
        )
        for raw_alias, raw_target in supplied_aliases:
            alias = _strict_descriptor_id(raw_alias, field_name="historical_alias")
            target = _strict_descriptor_id(raw_target, field_name="historical_alias_target")
            if alias in canonical_ids:
                raise ToolCatalogError("historical alias cannot be recycled as a canonical tool_id")
            if target not in canonical_ids:
                raise ToolCatalogError("historical alias target is not in the catalog")
            existing = alias_map.get(alias)
            if existing is not None and existing != target:
                raise ToolCatalogError("historical alias has conflicting canonical targets")
            alias_map[alias] = target

        retired = tuple(
            sorted(
                {
                    _strict_descriptor_id(value, field_name="retired_analytics_id")
                    for value in retired_analytics_ids
                }
            )
        )
        active_analytics = {descriptor.analytics_id for descriptor in catalog.descriptors}
        source_buckets = set(_TOOL_ANALYTICS_SOURCE_BUCKETS.values())
        if active_analytics & set(retired):
            raise ToolCatalogError("retired analytics identity cannot be reused")
        if active_analytics & source_buckets:
            raise ToolCatalogError("catalog analytics identity collides with a reserved source bucket")
        return cls(
            catalog=catalog,
            historical_aliases=tuple(sorted(alias_map.items())),
            retired_analytics_ids=retired,
        )

    def resolve(
        self,
        tool_or_alias: Any,
        *,
        source: ToolSource | str | None = None,
    ) -> ToolAnalyticsIdentityV1:
        """Resolve known IDs or return a non-personal aggregate source bucket."""

        raw_identity = str(tool_or_alias or "").strip().lower()
        descriptor_by_id = {
            descriptor.tool_id: descriptor for descriptor in self.catalog.descriptors
        }
        alias_map = dict(self.historical_aliases)
        canonical = alias_map.get(raw_identity, raw_identity)
        descriptor = descriptor_by_id.get(canonical)
        if descriptor is not None:
            return ToolAnalyticsIdentityV1(
                analytics_id=descriptor.analytics_id,
                family=descriptor.family,
                source=descriptor.source,
                resolution=(
                    ToolAnalyticsResolution.HISTORICAL_ALIAS
                    if canonical != raw_identity
                    else ToolAnalyticsResolution.CANONICAL
                ),
                canonical_tool_id=descriptor.tool_id,
                alias_applied=canonical != raw_identity,
                source_bucket=False,
            )

        source_value = (
            ToolSource.LEGACY
            if source is None
            else _controlled_enum(ToolSource, source, field_name="source")
        )
        if source_value == ToolSource.BUILTIN:
            raise ToolCatalogError("unknown built-in analytics identity")
        bucket = _TOOL_ANALYTICS_SOURCE_BUCKETS.get(source_value)
        if bucket is None:
            raise ToolCatalogError("unknown source requires a fail-closed analytics bucket")
        return ToolAnalyticsIdentityV1(
            analytics_id=bucket,
            family=ToolFamily.UNCLASSIFIED_DYNAMIC,
            source=source_value,
            resolution=(
                ToolAnalyticsResolution.LEGACY_BUCKET
                if source_value == ToolSource.LEGACY
                else ToolAnalyticsResolution.SOURCE_BUCKET
            ),
            canonical_tool_id=None,
            alias_applied=False,
            source_bucket=True,
        )

    def resolve_descriptor(self, descriptor: ToolDescriptorV2) -> ToolAnalyticsIdentityV1:
        if not isinstance(descriptor, ToolDescriptorV2):
            raise ToolCatalogError("analytics resolution requires ToolDescriptorV2")
        known = {
            item.tool_id: item for item in self.catalog.descriptors
        }.get(descriptor.tool_id)
        if known is not None and known == descriptor:
            return self.resolve(descriptor.tool_id)
        return self.resolve("", source=descriptor.source)

    def audit_dict(self) -> dict[str, Any]:
        return {
            "contract": self.CONTRACT_ID,
            "descriptor_count": len(self.catalog.descriptors),
            "historical_alias_count": len(self.historical_aliases),
            "retired_analytics_id_count": len(self.retired_analytics_ids),
            "dynamic_source_buckets": tuple(
                (source.value, analytics_id)
                for source, analytics_id in sorted(
                    _TOOL_ANALYTICS_SOURCE_BUCKETS.items(),
                    key=lambda item: item[0].value,
                )
            ),
            "owner_identity_visible": False,
            "session_identity_visible": False,
            "source_identity_visible": False,
            "raw_content_visible": False,
        }


@dataclass(frozen=True, slots=True)
class ToolSettingsMigrationReport:
    """Aggregate-only result of the settings migration; never contains IDs or values."""

    from_version: int
    to_version: int
    changed: bool
    disabled_setting_present: bool
    disabled_count: int
    alias_rewrite_count: int
    quarantined_count: int
    invalid_value_count: int
    legacy_enabled_deferred_count: int

    def audit_dict(self) -> dict[str, Any]:
        return {
            "contract": "odysseus.tool_settings_migration.v1",
            "from_version": self.from_version,
            "to_version": self.to_version,
            "changed": self.changed,
            "disabled_setting_present": self.disabled_setting_present,
            "disabled_count": self.disabled_count,
            "alias_rewrite_count": self.alias_rewrite_count,
            "quarantined_count": self.quarantined_count,
            "invalid_value_count": self.invalid_value_count,
            "legacy_enabled_deferred_count": self.legacy_enabled_deferred_count,
            "raw_values_visible": False,
            "user_data_visible": False,
            "provider_data_visible": False,
        }


def migrate_tool_settings(
    settings: Mapping[str, Any],
) -> tuple[dict[str, Any], ToolSettingsMigrationReport]:
    """Migrate legacy tool settings once while retaining exact rollback state.

    Unknown string identities remain in the denylist and are additionally
    quarantined. This preserves the safest legacy behavior without pretending
    that an unknown identity is a registered tool.
    """

    if not isinstance(settings, Mapping):
        raise ToolCatalogError("settings must be a mapping")
    current_version = _tool_settings_version(settings.get(TOOL_SETTINGS_SCHEMA_KEY, 0))
    if current_version > TOOL_SETTINGS_SCHEMA_VERSION:
        raise ToolCatalogError("tool settings schema is newer than this runtime")
    disabled_setting_present = "disabled_tools" in settings
    if current_version == TOOL_SETTINGS_SCHEMA_VERSION:
        disabled = settings.get("disabled_tools")
        disabled_count = len(disabled) if isinstance(disabled, list) else 0
        quarantine = settings.get(TOOL_SETTINGS_QUARANTINE_KEY)
        quarantined_count = len(quarantine) if isinstance(quarantine, list) else 0
        report = ToolSettingsMigrationReport(
            from_version=current_version,
            to_version=current_version,
            changed=False,
            disabled_setting_present=disabled_setting_present,
            disabled_count=disabled_count,
            alias_rewrite_count=0,
            quarantined_count=quarantined_count,
            invalid_value_count=0,
            legacy_enabled_deferred_count=0,
        )
        return deepcopy(dict(settings)), report

    from src.builtin_tool_catalog import (
        BUILTIN_TOOL_DEFINITIONS,
        OPERATOR_PRIORITY_DEFERRED_IDS,
    )

    canonical_ids = {definition.tool_id for definition in BUILTIN_TOOL_DEFINITIONS}
    aliases = {
        alias: definition.tool_id
        for definition in BUILTIN_TOOL_DEFINITIONS
        for alias in definition.aliases
    }
    raw_disabled = settings.get("disabled_tools", [])
    if isinstance(raw_disabled, str):
        disabled_values: list[Any] = [raw_disabled]
    elif isinstance(raw_disabled, (list, tuple, set)):
        disabled_values = list(raw_disabled)
    elif raw_disabled is None:
        disabled_values = []
    else:
        disabled_values = [raw_disabled]

    disabled: set[str] = set()
    quarantined: set[str] = set()
    alias_occurrences: dict[tuple[str, str], int] = {}
    invalid_value_count = 0
    for value in disabled_values:
        if not isinstance(value, str) or not value.strip():
            invalid_value_count += 1
            continue
        identity = value.strip()
        canonical = aliases.get(identity, identity)
        if canonical != identity:
            key = (identity, canonical)
            alias_occurrences[key] = alias_occurrences.get(key, 0) + 1
        disabled.add(canonical)
        if canonical not in canonical_ids:
            quarantined.add(canonical)

    existing_quarantine = settings.get(TOOL_SETTINGS_QUARANTINE_KEY, [])
    if isinstance(existing_quarantine, str):
        existing_quarantine_values: list[Any] = [existing_quarantine]
    elif isinstance(existing_quarantine, (list, tuple, set)):
        existing_quarantine_values = list(existing_quarantine)
    elif existing_quarantine is None:
        existing_quarantine_values = []
    else:
        existing_quarantine_values = [existing_quarantine]
    for value in existing_quarantine_values:
        if not isinstance(value, str) or not value.strip():
            invalid_value_count += 1
            continue
        identity = value.strip()
        quarantined.add(identity)
        disabled.add(identity)

    legacy_enabled_deferred = (
        set(OPERATOR_PRIORITY_DEFERRED_IDS) - disabled
        if disabled_setting_present
        else set()
    )
    disabled.update(OPERATOR_PRIORITY_DEFERRED_IDS)

    tracked_keys = (
        "disabled_tools",
        TOOL_SETTINGS_QUARANTINE_KEY,
        TOOL_SETTINGS_SCHEMA_KEY,
        TOOL_SETTINGS_MIGRATION_KEY,
    )
    rollback = {
        key: {
            "present": key in settings,
            "value": deepcopy(settings.get(key)),
        }
        for key in tracked_keys
    }
    migration_metadata = {
        "contract": "odysseus.tool_settings_migration.v1",
        "from_version": current_version,
        "to_version": TOOL_SETTINGS_SCHEMA_VERSION,
        "alias_rewrites": [
            {
                "alias": alias,
                "canonical": canonical,
                "occurrences": alias_occurrences[(alias, canonical)],
            }
            for alias, canonical in sorted(alias_occurrences)
        ],
        "unknown_disabled_tools": sorted(quarantined),
        "legacy_enabled_deferred_tools": sorted(legacy_enabled_deferred),
        "invalid_disabled_tool_value_count": invalid_value_count,
        "rollback": rollback,
    }
    migrated = deepcopy(dict(settings))
    migrated["disabled_tools"] = sorted(disabled)
    migrated[TOOL_SETTINGS_QUARANTINE_KEY] = sorted(quarantined)
    migrated[TOOL_SETTINGS_MIGRATION_KEY] = migration_metadata
    migrated[TOOL_SETTINGS_SCHEMA_KEY] = TOOL_SETTINGS_SCHEMA_VERSION
    report = ToolSettingsMigrationReport(
        from_version=current_version,
        to_version=TOOL_SETTINGS_SCHEMA_VERSION,
        changed=True,
        disabled_setting_present=disabled_setting_present,
        disabled_count=len(disabled),
        alias_rewrite_count=sum(alias_occurrences.values()),
        quarantined_count=len(quarantined),
        invalid_value_count=invalid_value_count,
        legacy_enabled_deferred_count=len(legacy_enabled_deferred),
    )
    return migrated, report


def rollback_tool_settings_migration(settings: Mapping[str, Any]) -> dict[str, Any]:
    """Restore the exact pre-migration values of every migration-owned key."""

    if not isinstance(settings, Mapping):
        raise ToolCatalogError("settings must be a mapping")
    metadata = settings.get(TOOL_SETTINGS_MIGRATION_KEY)
    if not isinstance(metadata, Mapping) or metadata.get("contract") != "odysseus.tool_settings_migration.v1":
        raise ToolCatalogError("tool settings rollback metadata is missing")
    rollback = metadata.get("rollback")
    if not isinstance(rollback, Mapping):
        raise ToolCatalogError("tool settings rollback state is missing")

    restored = deepcopy(dict(settings))
    for key in (
        "disabled_tools",
        TOOL_SETTINGS_QUARANTINE_KEY,
        TOOL_SETTINGS_SCHEMA_KEY,
        TOOL_SETTINGS_MIGRATION_KEY,
    ):
        snapshot = rollback.get(key)
        if not isinstance(snapshot, Mapping) or "present" not in snapshot:
            raise ToolCatalogError("tool settings rollback state is incomplete")
        if snapshot["present"]:
            restored[key] = deepcopy(snapshot.get("value"))
        else:
            restored.pop(key, None)
    return restored


def _tool_settings_version(value: Any) -> int:
    if isinstance(value, bool):
        raise ToolCatalogError("tool settings schema version must be an integer")
    try:
        version = int(value)
    except (TypeError, ValueError) as exc:
        raise ToolCatalogError("tool settings schema version must be an integer") from exc
    if version < 0 or str(version) != str(value):
        raise ToolCatalogError("tool settings schema version must be a canonical integer")
    return version


_V1_FAMILY_MAP: Mapping[str, ToolFamily] = {
    "admin": ToolFamily.ADMIN_SYSTEM,
    "content": ToolFamily.DOCUMENTS_MEDIA,
    "email": ToolFamily.PLANNING_COMMUNICATION,
    "execution": ToolFamily.CODE_FILESYSTEM,
    "filesystem": ToolFamily.CODE_FILESYSTEM,
    "general": ToolFamily.EXPERIMENTAL,
    "knowledge": ToolFamily.KNOWLEDGE_MEMORY,
    "mcp": ToolFamily.PLUGINS_MCP,
    "network": ToolFamily.SEARCH_WEB,
    "orchestration": ToolFamily.ORCHESTRATION_SESSIONS,
    "planning": ToolFamily.PLANNING_COMMUNICATION,
}


def _family_from_v1(value: str) -> ToolFamily:
    try:
        return _V1_FAMILY_MAP[value]
    except KeyError as exc:
        raise ToolCatalogError("v1 family has no controlled v2 mapping") from exc


def _effect_from_v1(
    capabilities: Iterable[str],
    risk_level: ToolRiskLevel,
) -> ToolEffectClass:
    values = set(capabilities)
    if "destructive" in values:
        return ToolEffectClass.DESTRUCTIVE
    if "external-send" in values or "network" in values and "write" in values:
        return ToolEffectClass.EXTERNAL_WRITE
    if "write" in values:
        return ToolEffectClass.LOCAL_WRITE
    if "execute" in values or "manage" in values or risk_level == ToolRiskLevel.DANGEROUS:
        return ToolEffectClass.CONTROL
    return ToolEffectClass.READ


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


@dataclass(frozen=True, slots=True)
class DeferredToolSchemaSelection:
    manifests: tuple[ToolManifest, ...]
    selected_schemas: tuple[dict[str, Any], ...]
    selected_schema_refs: tuple[str, ...]
    deferred_schema_refs: tuple[str, ...]
    blocked_schema_refs: tuple[str, ...]
    warnings: tuple[str, ...]
    prompt_budget_estimate: int

    def audit_summary(self) -> dict[str, Any]:
        return {
            "manifest_count": len(self.manifests),
            "selected_schema_count": len(self.selected_schemas),
            "deferred_schema_count": len(self.deferred_schema_refs),
            "blocked_schema_count": len(self.blocked_schema_refs),
            "selected_schema_refs": self.selected_schema_refs,
            "deferred_schema_refs": self.deferred_schema_refs,
            "blocked_schema_refs": self.blocked_schema_refs,
            "warnings": self.warnings,
            "prompt_budget_estimate": self.prompt_budget_estimate,
            "raw_schema_visible": False,
            "raw_content_visible": False,
            "token_value_visible": False,
        }


def select_deferred_tool_schemas(
    schemas: Iterable[dict[str, Any]],
    *,
    relevant_tool_ids: Iterable[str] | None,
    required_tool_ids: Iterable[str] = (),
    disabled_tool_ids: Iterable[str] = (),
    admin_tool_ids: Iterable[str] = (),
    needs_admin: bool = False,
    allow_full_fallback: bool = False,
) -> DeferredToolSchemaSelection:
    """Return manifest-first tool context plus the small full-schema subset.

    The function is intentionally pure: no tool execution, no provider calls and
    no MCP discovery. Callers decide which ids are relevant from trusted runtime
    state; this helper only maps that decision to compact manifests and deferred
    full schemas.
    """

    schema_by_name: dict[str, dict[str, Any]] = {}
    for schema in schemas:
        name = _function_schema_name(schema)
        if not name or name in schema_by_name:
            continue
        schema_by_name[name] = schema

    disabled = {_normalize_tool_id(item, field_name="disabled_tool_id") for item in disabled_tool_ids}
    required = {_normalize_tool_id(item, field_name="required_tool_id") for item in required_tool_ids}
    admin = {_normalize_tool_id(item, field_name="admin_tool_id") for item in admin_tool_ids}

    if relevant_tool_ids is None:
        selected = set(schema_by_name) if allow_full_fallback else set()
        warnings = ["fallback_full_schema_selection"] if allow_full_fallback else ["schema_selection_requires_relevant_tool_ids"]
    else:
        selected = {_normalize_tool_id(item, field_name="relevant_tool_id") for item in relevant_tool_ids}
        warnings = []
    selected |= required
    if needs_admin:
        selected |= admin
    selected -= disabled

    manifests: list[ToolManifest] = []
    selected_refs: list[str] = []
    deferred_refs: list[str] = []
    blocked_refs: list[str] = []
    selected_schemas: list[dict[str, Any]] = []

    for name in sorted(schema_by_name):
        schema = schema_by_name[name]
        schema_ref = f"function:{name}"
        if name in disabled:
            visibility = ToolVisibility.BLOCKED
            blocked_refs.append(schema_ref)
        elif name in selected:
            visibility = ToolVisibility.VISIBLE
            selected_refs.append(schema_ref)
            selected_schemas.append(schema)
        else:
            visibility = ToolVisibility.HIDDEN
            deferred_refs.append(schema_ref)
        manifests.append(ToolManifest.from_function_schema(schema, visibility_state=visibility))

    missing_selected = sorted(item for item in selected if item not in schema_by_name)
    warnings.extend(f"selected_schema_missing:{item}" for item in missing_selected)
    missing_disabled = sorted(item for item in disabled if item not in schema_by_name)
    warnings.extend(f"disabled_schema_missing:{item}" for item in missing_disabled)

    prompt_budget = sum(item.prompt_budget_estimate for item in manifests)
    prompt_budget += sum(_schema_budget_estimate(schema) for schema in selected_schemas)
    return DeferredToolSchemaSelection(
        manifests=tuple(manifests),
        selected_schemas=tuple(selected_schemas),
        selected_schema_refs=tuple(selected_refs),
        deferred_schema_refs=tuple(deferred_refs),
        blocked_schema_refs=tuple(blocked_refs),
        warnings=tuple(warnings),
        prompt_budget_estimate=prompt_budget,
    )


def _function_schema_name(schema: dict[str, Any]) -> str:
    fn = schema.get("function") if isinstance(schema, dict) else None
    payload = fn if isinstance(fn, dict) else schema
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("name") or "").strip()


def _schema_budget_estimate(schema: dict[str, Any]) -> int:
    text = repr(schema)
    return max(24, min(600, len(text) // 8))


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
