"""Small backend contract for tool catalog visibility and selection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from src.agent_identity import AgentIdentity
from src.context_capsule import ContextCapsule


_MAX_ID_LENGTH = 80
_MAX_SUMMARY_CHARS = 140
_MAX_SCHEMA_REF_LENGTH = 120
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")
_TOOL_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
_STATIC_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}$")
_WINDOWS_ABSOLUTE_REF_RE = re.compile(r"^[A-Za-z]:/")


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
    DYNAMIC = "dynamic"


class ToolIdentifierDisposition(StrEnum):
    """Outcome of resolving a persisted tool identifier during migration."""

    CANONICAL = "canonical"
    ALIAS = "alias"
    LEGACY_NON_RUNTIME = "legacy_non_runtime"
    UNKNOWN = "unknown"


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
    UNCONFIGURED = "unconfigured"
    DISABLED = "disabled"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class ToolEffectClass(StrEnum):
    READ = "read"
    LOCAL_WRITE = "local_write"
    EXTERNAL_WRITE = "external_write"
    DESTRUCTIVE = "destructive"
    CONTROL = "control"


class ToolPermission(StrEnum):
    PUBLIC = "public"
    OWNER = "owner"
    ADMIN = "admin"
    SYSTEM = "system"


LEGACY_NON_RUNTIME_TOOL_IDS: Mapping[str, str] = MappingProxyType(
    {
        "manage_rag": "legacy_ui_identifier_without_runtime_tool",
    }
)


@dataclass(frozen=True, slots=True)
class ToolIdentifierResolution:
    """Safe canonicalization result for one persisted tool identifier."""

    supplied_id: str
    canonical_id: str | None
    disposition: ToolIdentifierDisposition
    reason_code: str


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


def _strict_analytics_id(value: Any) -> str:
    raw = str(value or "").strip()
    normalized = _normalize_slug(raw, field_name="analytics_id")
    if normalized != raw:
        raise ToolCatalogError("analytics_id must already be a lowercase hyphenated slug")
    return normalized


def _enum_value(enum_type: type[StrEnum], value: Any, *, field_name: str) -> StrEnum:
    try:
        return value if isinstance(value, enum_type) else enum_type(str(value))
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise ToolCatalogError(f"{field_name} must be one of: {allowed}") from exc


def _strict_bool(value: Any, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ToolCatalogError(f"{field_name} must be a boolean")
    return value


def _optional_ref(value: Any, *, field_name: str) -> str | None:
    if value is None or not str(value).strip():
        return None
    normalized = _normalize_text(
        value,
        field_name=field_name,
        allow_empty=False,
        limit=_MAX_SCHEMA_REF_LENGTH,
    )
    if (
        not _STATIC_REF_RE.fullmatch(normalized)
        or normalized.startswith("/")
        or _WINDOWS_ABSOLUTE_REF_RE.match(normalized)
        or "://" in normalized
    ):
        raise ToolCatalogError(f"{field_name} must be a static non-absolute reference")
    return normalized


def _aliases(values: Iterable[Any], *, tool_id: str) -> tuple[str, ...]:
    aliases = sorted(_normalize_tool_id(value, field_name="alias") for value in values)
    if len(aliases) != len(set(aliases)):
        raise ToolCatalogError("aliases must not contain duplicates")
    if tool_id in aliases:
        raise ToolCatalogError("aliases must not repeat the canonical tool_id")
    return tuple(aliases)


_DEFAULT_OFF_LIFECYCLES = {
    ToolLifecycle.DEFERRED,
    ToolLifecycle.EXPERIMENTAL,
    ToolLifecycle.DEPRECATED,
    ToolLifecycle.BLOCKED,
}

_ALLOWED_LIFECYCLE_TRANSITIONS = {
    ToolLifecycle.ACTIVE: {
        ToolLifecycle.ACTIVE,
        ToolLifecycle.CONTEXTUAL,
        ToolLifecycle.DEFERRED,
        ToolLifecycle.DEPRECATED,
        ToolLifecycle.BLOCKED,
    },
    ToolLifecycle.CONTEXTUAL: {
        ToolLifecycle.ACTIVE,
        ToolLifecycle.CONTEXTUAL,
        ToolLifecycle.DEFERRED,
        ToolLifecycle.DEPRECATED,
        ToolLifecycle.BLOCKED,
    },
    ToolLifecycle.DEFERRED: {
        ToolLifecycle.ACTIVE,
        ToolLifecycle.CONTEXTUAL,
        ToolLifecycle.DEFERRED,
        ToolLifecycle.DEPRECATED,
        ToolLifecycle.BLOCKED,
    },
    ToolLifecycle.EXPERIMENTAL: {
        ToolLifecycle.ACTIVE,
        ToolLifecycle.CONTEXTUAL,
        ToolLifecycle.DEFERRED,
        ToolLifecycle.EXPERIMENTAL,
        ToolLifecycle.DEPRECATED,
        ToolLifecycle.BLOCKED,
    },
    ToolLifecycle.DEPRECATED: {
        ToolLifecycle.DEPRECATED,
        ToolLifecycle.BLOCKED,
    },
    ToolLifecycle.BLOCKED: {
        ToolLifecycle.BLOCKED,
    },
}


def validate_tool_lifecycle_transition(
    previous: ToolLifecycle | str,
    next_value: ToolLifecycle | str,
) -> ToolLifecycle:
    previous_value = _enum_value(ToolLifecycle, previous, field_name="previous_lifecycle")
    normalized_next = _enum_value(ToolLifecycle, next_value, field_name="next_lifecycle")
    if normalized_next not in _ALLOWED_LIFECYCLE_TRANSITIONS[previous_value]:
        raise ToolCatalogError(
            f"lifecycle transition {previous_value.value}->{normalized_next.value} is not allowed"
        )
    return normalized_next


_LEGACY_FAMILY_MAP = {
    "filesystem": ToolFamily.CODE_FILESYSTEM,
    "execution": ToolFamily.CODE_FILESYSTEM,
    "network": ToolFamily.SEARCH_WEB,
    "knowledge": ToolFamily.KNOWLEDGE_MEMORY,
    "content": ToolFamily.DOCUMENTS_MEDIA,
    "email": ToolFamily.PLANNING_COMMUNICATION,
    "planning": ToolFamily.PLANNING_COMMUNICATION,
    "orchestration": ToolFamily.ORCHESTRATION_SESSIONS,
    "admin": ToolFamily.ADMIN_SYSTEM,
    "mcp": ToolFamily.PLUGINS_MCP,
    "general": ToolFamily.EXPERIMENTAL,
}


def _effect_from_capabilities(capabilities: Iterable[str]) -> ToolEffectClass:
    values = set(capabilities)
    if "destructive" in values:
        return ToolEffectClass.DESTRUCTIVE
    if "external-send" in values:
        return ToolEffectClass.EXTERNAL_WRITE
    if "write" in values:
        return ToolEffectClass.LOCAL_WRITE
    if {"execute", "manage", "schedule"} & values:
        return ToolEffectClass.CONTROL
    return ToolEffectClass.READ


@dataclass(frozen=True, slots=True)
class ToolDescriptorV2:
    schema_version: str
    tool_id: str
    analytics_id: str
    display_name: str
    description: str
    family: ToolFamily
    source: ToolSource
    lifecycle: ToolLifecycle
    availability: ToolAvailability
    default_enabled: bool
    default_visibility: ToolVisibility
    risk_level: ToolRiskLevel
    permission: ToolPermission
    effect_class: ToolEffectClass
    requires_confirmation: bool
    schema_ref: str | None
    handler_ref: str | None
    prompt_ref: str | None
    aliases: tuple[str, ...]
    feature_flag: str | None
    introduced_in: str
    deprecated_in: str | None

    SCHEMA_VERSION = "odysseus.tool_descriptor.v2"

    @classmethod
    def create(
        cls,
        *,
        tool_id: Any,
        analytics_id: Any,
        display_name: Any,
        description: Any,
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
        schema_ref: Any = None,
        handler_ref: Any = None,
        prompt_ref: Any = None,
        aliases: Iterable[Any] = (),
        feature_flag: Any = None,
        introduced_in: Any = "unknown",
        deprecated_in: Any = None,
    ) -> "ToolDescriptorV2":
        normalized_tool_id = _normalize_tool_id(tool_id)
        normalized_lifecycle = _enum_value(ToolLifecycle, lifecycle, field_name="lifecycle")
        normalized_availability = _enum_value(
            ToolAvailability,
            availability,
            field_name="availability",
        )
        normalized_visibility = _enum_value(
            ToolVisibility,
            default_visibility,
            field_name="default_visibility",
        )
        normalized_risk = _enum_value(ToolRiskLevel, risk_level, field_name="risk_level")
        normalized_effect = _enum_value(
            ToolEffectClass,
            effect_class,
            field_name="effect_class",
        )
        enabled = _strict_bool(default_enabled, field_name="default_enabled")
        confirmed = _strict_bool(requires_confirmation, field_name="requires_confirmation")

        if enabled and normalized_lifecycle in _DEFAULT_OFF_LIFECYCLES:
            raise ToolCatalogError(f"{normalized_lifecycle.value} tools must be default_enabled=false")
        if enabled and normalized_availability != ToolAvailability.AVAILABLE:
            raise ToolCatalogError("only available tools may be default_enabled")
        if enabled and normalized_visibility in {
            ToolVisibility.HIDDEN,
            ToolVisibility.BLOCKED,
            ToolVisibility.UNAVAILABLE,
        }:
            raise ToolCatalogError("enabled tools require a visible or approval-gated default visibility")
        if normalized_risk == ToolRiskLevel.DANGEROUS and not confirmed:
            raise ToolCatalogError("dangerous tools require confirmation")
        if normalized_effect in {
            ToolEffectClass.EXTERNAL_WRITE,
            ToolEffectClass.DESTRUCTIVE,
        } and not confirmed:
            raise ToolCatalogError(f"{normalized_effect.value} tools require confirmation")

        normalized_deprecated_in = _optional_ref(deprecated_in, field_name="deprecated_in")
        if normalized_lifecycle == ToolLifecycle.DEPRECATED and not normalized_deprecated_in:
            raise ToolCatalogError("deprecated tools require deprecated_in")
        if normalized_lifecycle != ToolLifecycle.DEPRECATED and normalized_deprecated_in:
            raise ToolCatalogError("deprecated_in is only valid for deprecated tools")

        return cls(
            schema_version=cls.SCHEMA_VERSION,
            tool_id=normalized_tool_id,
            analytics_id=_strict_analytics_id(analytics_id),
            display_name=_normalize_text(
                display_name,
                field_name="display_name",
                allow_empty=False,
                limit=80,
            ),
            description=_normalize_text(
                description,
                field_name="description",
                allow_empty=False,
                limit=160,
            ),
            family=_enum_value(ToolFamily, family, field_name="family"),
            source=_enum_value(ToolSource, source, field_name="source"),
            lifecycle=normalized_lifecycle,
            availability=normalized_availability,
            default_enabled=enabled,
            default_visibility=normalized_visibility,
            risk_level=normalized_risk,
            permission=_enum_value(ToolPermission, permission, field_name="permission"),
            effect_class=normalized_effect,
            requires_confirmation=confirmed,
            schema_ref=_optional_ref(schema_ref, field_name="schema_ref"),
            handler_ref=_optional_ref(handler_ref, field_name="handler_ref"),
            prompt_ref=_optional_ref(prompt_ref, field_name="prompt_ref"),
            aliases=_aliases(aliases, tool_id=normalized_tool_id),
            feature_flag=_optional_ref(feature_flag, field_name="feature_flag"),
            introduced_in=_normalize_text(
                introduced_in,
                field_name="introduced_in",
                allow_empty=False,
                limit=40,
            ),
            deprecated_in=normalized_deprecated_in,
        )

    @classmethod
    def conservative_dynamic(
        cls,
        *,
        tool_id: Any,
        display_name: Any,
        description: Any,
    ) -> "ToolDescriptorV2":
        normalized_tool_id = _normalize_tool_id(tool_id)
        return cls.create(
            tool_id=normalized_tool_id,
            analytics_id=_normalize_slug(normalized_tool_id, field_name="analytics_id"),
            display_name=display_name,
            description=description,
            family=ToolFamily.UNCLASSIFIED_DYNAMIC,
            source=ToolSource.DYNAMIC,
            lifecycle=ToolLifecycle.BLOCKED,
            availability=ToolAvailability.UNKNOWN,
            default_enabled=False,
            default_visibility=ToolVisibility.HIDDEN,
            risk_level=ToolRiskLevel.ELEVATED,
            permission=ToolPermission.ADMIN,
            effect_class=ToolEffectClass.CONTROL,
            requires_confirmation=True,
            introduced_in="dynamic",
        )

    @classmethod
    def from_v1_manifest(
        cls,
        manifest: "ToolManifest",
        *,
        source: ToolSource | str = ToolSource.LEGACY,
    ) -> "ToolDescriptorV2":
        if not isinstance(manifest, ToolManifest):
            raise ToolCatalogError("manifest must be a ToolManifest")
        effect = _effect_from_capabilities(manifest.capabilities)
        availability = (
            ToolAvailability.UNAVAILABLE
            if manifest.visibility_state == ToolVisibility.UNAVAILABLE
            else ToolAvailability.BLOCKED
            if manifest.visibility_state == ToolVisibility.BLOCKED
            else ToolAvailability.AVAILABLE
        )
        confirmation = manifest.risk_level == ToolRiskLevel.DANGEROUS or effect in {
            ToolEffectClass.EXTERNAL_WRITE,
            ToolEffectClass.DESTRUCTIVE,
        }
        return cls.create(
            tool_id=manifest.tool_id,
            analytics_id=_normalize_slug(manifest.tool_id, field_name="analytics_id"),
            display_name=manifest.tool_id.replace("_", " ").replace("-", " ").title(),
            description=manifest.short_description,
            family=_LEGACY_FAMILY_MAP.get(manifest.family, ToolFamily.EXPERIMENTAL),
            source=source,
            lifecycle=ToolLifecycle.CONTEXTUAL,
            availability=availability,
            default_enabled=False,
            default_visibility=manifest.visibility_state,
            risk_level=manifest.risk_level,
            permission=(
                ToolPermission.ADMIN
                if manifest.risk_level == ToolRiskLevel.DANGEROUS
                else ToolPermission.OWNER
            ),
            effect_class=effect,
            requires_confirmation=confirmation,
            schema_ref=manifest.schema_ref,
            introduced_in="legacy-v1",
        )

    def audit_summary(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "tool_id": self.tool_id,
            "analytics_id": self.analytics_id,
            "family": self.family.value,
            "source": self.source.value,
            "lifecycle": self.lifecycle.value,
            "availability": self.availability.value,
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
            "raw_content_visible": False,
            "callable_visible": False,
            "tool_arguments_visible": False,
            "tool_results_visible": False,
            "secret_values_visible": False,
        }


@dataclass(frozen=True, slots=True)
class ToolDescriptorV2Index:
    descriptors: tuple[ToolDescriptorV2, ...]
    alias_targets: tuple[tuple[str, str], ...]

    @classmethod
    def build(cls, descriptors: Iterable[ToolDescriptorV2]) -> "ToolDescriptorV2Index":
        values = tuple(descriptors)
        if any(not isinstance(item, ToolDescriptorV2) for item in values):
            raise ToolCatalogError("descriptor index accepts ToolDescriptorV2 values only")
        ordered = tuple(sorted(values, key=lambda item: item.tool_id))

        by_tool_id: dict[str, ToolDescriptorV2] = {}
        analytics_ids: dict[str, str] = {}
        for descriptor in ordered:
            if descriptor.tool_id in by_tool_id:
                raise ToolCatalogError(f"duplicate tool_id {descriptor.tool_id}")
            by_tool_id[descriptor.tool_id] = descriptor
            previous_tool = analytics_ids.get(descriptor.analytics_id)
            if previous_tool:
                raise ToolCatalogError(
                    f"analytics_id {descriptor.analytics_id} collides between "
                    f"{previous_tool} and {descriptor.tool_id}"
                )
            analytics_ids[descriptor.analytics_id] = descriptor.tool_id

        alias_targets: dict[str, str] = {}
        for descriptor in ordered:
            for alias in descriptor.aliases:
                if alias in by_tool_id:
                    raise ToolCatalogError(f"alias {alias} collides with canonical tool_id")
                if alias in alias_targets:
                    raise ToolCatalogError(f"alias {alias} is assigned more than once")
                alias_targets[alias] = descriptor.tool_id
        return cls(
            descriptors=ordered,
            alias_targets=tuple(sorted(alias_targets.items())),
        )

    def resolve(self, tool_id_or_alias: Any) -> ToolDescriptorV2 | None:
        value = _normalize_tool_id(tool_id_or_alias, field_name="tool_id_or_alias")
        by_tool_id = {item.tool_id: item for item in self.descriptors}
        if value in by_tool_id:
            return by_tool_id[value]
        alias_targets = dict(self.alias_targets)
        target = alias_targets.get(value)
        return by_tool_id.get(target) if target else None

    def audit_summary(self) -> dict[str, Any]:
        return {
            "schema_version": ToolDescriptorV2.SCHEMA_VERSION,
            "descriptor_count": len(self.descriptors),
            "tool_ids": tuple(item.tool_id for item in self.descriptors),
            "analytics_ids": tuple(item.analytics_id for item in self.descriptors),
            "alias_targets": self.alias_targets,
            "raw_content_visible": False,
            "callable_visible": False,
            "secret_values_visible": False,
        }


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
