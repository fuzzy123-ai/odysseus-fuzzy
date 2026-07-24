"""Pure, default-off runtime-state value contracts for the Unified Source Index.

This module performs no environment, filesystem, provider, database, or runtime
access.  It models configuration/state values only; representing ``active`` or
``canary`` never authorizes a live activation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any, Mapping


RUNTIME_STATE_SCHEMA = "odysseus.unified_source_index.runtime_state.v1"
MAX_SELECTED_SCOPES = 64
MAX_SCOPE_SOURCE_COUNT = 256
MAX_PROVIDER_CAPABILITIES = 16

_GENERATION_RE = re.compile(r"^usi_generation_[0-9a-f]{64}$")
_SCOPE_RE = re.compile(r"^usi_scope_[0-9a-f]{64}$")


class RuntimeStateContractError(ValueError):
    """Raised when a runtime state is unsafe, ambiguous, or unbounded."""


class RuntimeMode(StrEnum):
    DISABLED = "disabled"
    READ_ONLY = "read_only"
    SHADOW = "shadow"
    CANARY = "canary"
    ACTIVE = "active"
    DEGRADED = "degraded"
    ROLLBACK = "rollback"


class WorkerPolicy(StrEnum):
    STOPPED = "stopped"
    READ_ONLY = "read_only"
    RUNNING = "running"


class DomainScope(StrEnum):
    CODE = "code"
    DOCUMENT = "document"
    MEMORY = "memory"
    PLANNING = "planning"
    INBOX = "inbox"


class ProviderKind(StrEnum):
    LEXICAL = "lexical"
    SEMANTIC = "semantic"
    SYMBOL = "symbol"
    GRAPH = "graph"
    TIMELINE = "timeline"
    RAPTOR = "raptor"
    LINEAGE = "lineage"


class ProviderHealth(StrEnum):
    READY = "ready"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    DEGRADED = "degraded"


class RuntimeHealthState(StrEnum):
    DISABLED = "disabled"
    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class FallbackReason(StrEnum):
    RUNTIME_DISABLED = "runtime_disabled"
    SCOPE_NOT_SELECTED = "scope_not_selected"
    CORE_UNAVAILABLE = "core_unavailable"
    OPTIONAL_PROVIDER_UNAVAILABLE = "optional_provider_unavailable"
    POLICY_BLOCKED = "policy_blocked"
    STALE_GENERATION = "stale_generation"
    ROLLBACK_ACTIVE = "rollback_active"


@dataclass(frozen=True, slots=True)
class RuntimeGeneration:
    generation_ref: str
    previous_generation_ref: str = ""

    def __post_init__(self) -> None:
        generation = _generation_ref(self.generation_ref, "generation_ref")
        previous = ""
        if self.previous_generation_ref:
            previous = _generation_ref(
                self.previous_generation_ref,
                "previous_generation_ref",
            )
            if previous == generation:
                raise RuntimeStateContractError("previous generation must differ")
        object.__setattr__(self, "generation_ref", generation)
        object.__setattr__(self, "previous_generation_ref", previous)

    def to_dict(self) -> dict[str, str]:
        return {
            "generation_ref": self.generation_ref,
            "previous_generation_ref": self.previous_generation_ref,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "RuntimeGeneration":
        data = _exact_mapping(
            value,
            {"generation_ref", "previous_generation_ref"},
            "generation",
        )
        return cls(data["generation_ref"], data["previous_generation_ref"])


@dataclass(frozen=True, slots=True)
class SelectedScope:
    scope_ref: str
    domains: tuple[DomainScope, ...]
    source_count: int
    eligible: bool

    def __post_init__(self) -> None:
        scope_ref = _scope_ref(self.scope_ref)
        if not isinstance(self.domains, tuple) or not self.domains:
            raise RuntimeStateContractError("scope domains must be a non-empty tuple")
        try:
            domains = tuple(sorted({DomainScope(value) for value in self.domains}, key=str))
        except (TypeError, ValueError) as exc:
            raise RuntimeStateContractError("scope domain is invalid") from exc
        if len(domains) != len(self.domains):
            raise RuntimeStateContractError("scope domains must be unique")
        if not 1 <= len(domains) <= len(DomainScope):
            raise RuntimeStateContractError("scope domains are unbounded")
        if type(self.source_count) is not int or not 1 <= self.source_count <= MAX_SCOPE_SOURCE_COUNT:
            raise RuntimeStateContractError("scope source_count is invalid")
        if type(self.eligible) is not bool:
            raise RuntimeStateContractError("scope eligibility must be boolean")
        object.__setattr__(self, "scope_ref", scope_ref)
        object.__setattr__(self, "domains", domains)

    def to_dict(self) -> dict[str, object]:
        return {
            "scope_ref": self.scope_ref,
            "domains": [item.value for item in self.domains],
            "source_count": self.source_count,
            "eligible": self.eligible,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "SelectedScope":
        data = _exact_mapping(
            value,
            {"scope_ref", "domains", "source_count", "eligible"},
            "selected scope",
        )
        if not isinstance(data["domains"], list):
            raise RuntimeStateContractError("scope domains must be a list")
        return cls(
            data["scope_ref"],
            tuple(data["domains"]),
            data["source_count"],
            data["eligible"],
        )


@dataclass(frozen=True, slots=True)
class ProviderCapability:
    provider: ProviderKind
    health: ProviderHealth

    def __post_init__(self) -> None:
        try:
            provider = ProviderKind(self.provider)
            health = ProviderHealth(self.health)
        except (TypeError, ValueError) as exc:
            raise RuntimeStateContractError("provider capability is invalid") from exc
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "health", health)

    def to_dict(self) -> dict[str, str]:
        return {"provider": self.provider.value, "health": self.health.value}

    @classmethod
    def from_dict(cls, value: Any) -> "ProviderCapability":
        data = _exact_mapping(value, {"provider", "health"}, "provider capability")
        return cls(data["provider"], data["health"])


@dataclass(frozen=True, slots=True)
class RuntimeHealth:
    core: RuntimeHealthState
    providers: tuple[ProviderCapability, ...]
    reasons: tuple[FallbackReason, ...] = ()

    def __post_init__(self) -> None:
        try:
            core = RuntimeHealthState(self.core)
        except (TypeError, ValueError) as exc:
            raise RuntimeStateContractError("core health is invalid") from exc
        if not isinstance(self.providers, tuple) or len(self.providers) > MAX_PROVIDER_CAPABILITIES:
            raise RuntimeStateContractError("provider capabilities are unbounded")
        if not all(isinstance(item, ProviderCapability) for item in self.providers):
            raise RuntimeStateContractError("provider capability must be typed")
        providers = tuple(sorted(self.providers, key=lambda item: item.provider.value))
        if len({item.provider for item in providers}) != len(providers):
            raise RuntimeStateContractError("provider capabilities must be unique")
        if not isinstance(self.reasons, tuple):
            raise RuntimeStateContractError("fallback reasons must be a tuple")
        try:
            reasons = tuple(sorted({FallbackReason(item) for item in self.reasons}, key=str))
        except (TypeError, ValueError) as exc:
            raise RuntimeStateContractError("fallback reason is invalid") from exc
        if len(reasons) != len(self.reasons):
            raise RuntimeStateContractError("fallback reasons must be unique")
        if core is RuntimeHealthState.DISABLED:
            if providers:
                raise RuntimeStateContractError("disabled health cannot expose providers")
            if any(reason is not FallbackReason.RUNTIME_DISABLED for reason in reasons):
                raise RuntimeStateContractError("disabled health only permits runtime_disabled")
        else:
            if FallbackReason.RUNTIME_DISABLED in reasons:
                raise RuntimeStateContractError("non-disabled health cannot carry runtime_disabled")
            optional_provider_unhealthy = any(
                provider.provider is not ProviderKind.LEXICAL
                and provider.health in {ProviderHealth.UNAVAILABLE, ProviderHealth.DEGRADED}
                for provider in providers
            )
            has_optional_reason = FallbackReason.OPTIONAL_PROVIDER_UNAVAILABLE in reasons
            if optional_provider_unhealthy != has_optional_reason:
                raise RuntimeStateContractError(
                    "optional provider availability must match its fallback reason"
                )
            if core is RuntimeHealthState.READY:
                incompatible_reasons = {
                    FallbackReason.CORE_UNAVAILABLE,
                    FallbackReason.STALE_GENERATION,
                    FallbackReason.RUNTIME_DISABLED,
                }
                if incompatible_reasons.intersection(reasons):
                    raise RuntimeStateContractError(
                        "ready core cannot carry core-unavailable fallback reasons"
                    )
            else:
                core_failure_reasons = {
                    FallbackReason.CORE_UNAVAILABLE,
                    FallbackReason.STALE_GENERATION,
                }
                if not core_failure_reasons.intersection(reasons):
                    raise RuntimeStateContractError(
                        "unhealthy core requires a compatible fallback reason"
                    )
                if (
                    core is RuntimeHealthState.UNAVAILABLE
                    and FallbackReason.CORE_UNAVAILABLE not in reasons
                ):
                    raise RuntimeStateContractError("unavailable core requires core_unavailable")
        object.__setattr__(self, "core", core)
        object.__setattr__(self, "providers", providers)
        object.__setattr__(self, "reasons", reasons)

    def to_dict(self) -> dict[str, object]:
        return {
            "core": self.core.value,
            "providers": [item.to_dict() for item in self.providers],
            "reasons": [item.value for item in self.reasons],
        }

    @classmethod
    def from_dict(cls, value: Any) -> "RuntimeHealth":
        data = _exact_mapping(value, {"core", "providers", "reasons"}, "health")
        if not isinstance(data["providers"], list) or not isinstance(data["reasons"], list):
            raise RuntimeStateContractError("health lists are invalid")
        return cls(
            data["core"],
            tuple(ProviderCapability.from_dict(item) for item in data["providers"]),
            tuple(data["reasons"]),
        )


@dataclass(frozen=True, slots=True)
class RuntimeStateRecord:
    mode: RuntimeMode
    generation: RuntimeGeneration | None
    selected_scopes: tuple[SelectedScope, ...]
    health: RuntimeHealth
    worker_policy: WorkerPolicy
    legacy_authoritative: bool
    prompt_injection: bool
    fallback_enabled: bool
    live_activation_authorized: bool = False

    def __post_init__(self) -> None:
        try:
            mode = RuntimeMode(self.mode)
            worker_policy = WorkerPolicy(self.worker_policy)
        except (TypeError, ValueError) as exc:
            raise RuntimeStateContractError("runtime mode or worker policy is invalid") from exc
        if self.generation is not None and not isinstance(self.generation, RuntimeGeneration):
            raise RuntimeStateContractError("generation must be typed")
        if not isinstance(self.selected_scopes, tuple) or len(self.selected_scopes) > MAX_SELECTED_SCOPES:
            raise RuntimeStateContractError("selected scopes are unbounded")
        if not all(isinstance(item, SelectedScope) for item in self.selected_scopes):
            raise RuntimeStateContractError("selected scope must be typed")
        scopes = tuple(sorted(self.selected_scopes, key=lambda item: item.scope_ref))
        if len({item.scope_ref for item in scopes}) != len(scopes):
            raise RuntimeStateContractError("selected scopes must be unique")
        if not isinstance(self.health, RuntimeHealth):
            raise RuntimeStateContractError("health must be typed")
        for flag, name in (
            (self.legacy_authoritative, "legacy_authoritative"),
            (self.prompt_injection, "prompt_injection"),
            (self.fallback_enabled, "fallback_enabled"),
            (self.live_activation_authorized, "live_activation_authorized"),
        ):
            if type(flag) is not bool:
                raise RuntimeStateContractError(f"{name} must be boolean")
        if self.live_activation_authorized:
            raise RuntimeStateContractError("runtime state cannot authorize live activation")
        _validate_mode(
            mode,
            self.generation,
            scopes,
            self.health,
            worker_policy,
            self.legacy_authoritative,
            self.prompt_injection,
            self.fallback_enabled,
        )
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "selected_scopes", scopes)
        object.__setattr__(self, "worker_policy", worker_policy)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": RUNTIME_STATE_SCHEMA,
            "mode": self.mode.value,
            "generation": self.generation.to_dict() if self.generation is not None else None,
            "selected_scopes": [item.to_dict() for item in self.selected_scopes],
            "health": self.health.to_dict(),
            "worker_policy": self.worker_policy.value,
            "legacy_authoritative": self.legacy_authoritative,
            "prompt_injection": self.prompt_injection,
            "fallback_enabled": self.fallback_enabled,
            "live_activation_authorized": False,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "RuntimeStateRecord":
        data = _exact_mapping(
            value,
            {
                "schema",
                "mode",
                "generation",
                "selected_scopes",
                "health",
                "worker_policy",
                "legacy_authoritative",
                "prompt_injection",
                "fallback_enabled",
                "live_activation_authorized",
            },
            "runtime state",
        )
        if data["schema"] != RUNTIME_STATE_SCHEMA:
            raise RuntimeStateContractError("runtime state schema is invalid")
        if data["generation"] is not None and not isinstance(data["generation"], Mapping):
            raise RuntimeStateContractError("generation must be an object or null")
        if not isinstance(data["selected_scopes"], list):
            raise RuntimeStateContractError("selected_scopes must be a list")
        return cls(
            data["mode"],
            RuntimeGeneration.from_dict(data["generation"]) if data["generation"] is not None else None,
            tuple(SelectedScope.from_dict(item) for item in data["selected_scopes"]),
            RuntimeHealth.from_dict(data["health"]),
            data["worker_policy"],
            data["legacy_authoritative"],
            data["prompt_injection"],
            data["fallback_enabled"],
            data["live_activation_authorized"],
        )


def _validate_mode(
    mode: RuntimeMode,
    generation: RuntimeGeneration | None,
    scopes: tuple[SelectedScope, ...],
    health: RuntimeHealth,
    worker_policy: WorkerPolicy,
    legacy_authoritative: bool,
    prompt_injection: bool,
    fallback_enabled: bool,
) -> None:
    if mode is RuntimeMode.DISABLED:
        if generation is not None or scopes or health.providers or health.core is not RuntimeHealthState.DISABLED:
            raise RuntimeStateContractError("disabled state cannot bind USI runtime state")
        if (
            worker_policy is not WorkerPolicy.STOPPED
            or prompt_injection
            or not legacy_authoritative
            or not fallback_enabled
        ):
            raise RuntimeStateContractError("disabled state must preserve legacy-only behavior")
        return
    if mode is RuntimeMode.READ_ONLY:
        if (
            generation is None
            or health.core is RuntimeHealthState.DISABLED
            or worker_policy is WorkerPolicy.RUNNING
            or prompt_injection
            or not legacy_authoritative
            or not fallback_enabled
        ):
            raise RuntimeStateContractError("read_only state cannot route prompts or run workers")
        return
    if mode is RuntimeMode.SHADOW:
        if (
            generation is None
            or not scopes
            or not all(scope.eligible for scope in scopes)
            or health.core is not RuntimeHealthState.READY
            or not _has_ready_lexical(health)
            or prompt_injection
            or not legacy_authoritative
            or not fallback_enabled
        ):
            raise RuntimeStateContractError("shadow state must retain bounded legacy authority")
        return
    if mode in {RuntimeMode.CANARY, RuntimeMode.ACTIVE}:
        if generation is None or not scopes or not all(scope.eligible for scope in scopes):
            raise RuntimeStateContractError("canary/active state requires eligible selected scopes")
        if (
            health.core is not RuntimeHealthState.READY
            or not _has_ready_lexical(health)
            or legacy_authoritative
            or not prompt_injection
            or not fallback_enabled
        ):
            raise RuntimeStateContractError("canary/active state requires ready core and explicit fallback")
        return
    if mode is RuntimeMode.DEGRADED:
        compatible_reasons = {
            FallbackReason.CORE_UNAVAILABLE,
            FallbackReason.STALE_GENERATION,
            FallbackReason.OPTIONAL_PROVIDER_UNAVAILABLE,
        }
        if (
            generation is None
            or health.core is RuntimeHealthState.DISABLED
            or not compatible_reasons.intersection(health.reasons)
        ):
            raise RuntimeStateContractError("degraded state requires a generated runtime with a compatible reason")
        if not health.reasons or not legacy_authoritative or prompt_injection or not fallback_enabled or worker_policy is WorkerPolicy.RUNNING:
            raise RuntimeStateContractError("degraded state must fail closed with explicit fallback")
        return
    if mode is RuntimeMode.ROLLBACK:
        if generation is None or not generation.previous_generation_ref:
            raise RuntimeStateContractError("rollback state requires a previous generation")
        if (
            worker_policy is WorkerPolicy.RUNNING
            or not fallback_enabled
            or FallbackReason.ROLLBACK_ACTIVE not in health.reasons
        ):
            raise RuntimeStateContractError("rollback state must stop new work and retain fallback")
        return
    raise RuntimeStateContractError("unsupported runtime mode")


def _has_ready_lexical(health: RuntimeHealth) -> bool:
    return any(
        provider.provider is ProviderKind.LEXICAL and provider.health is ProviderHealth.READY
        for provider in health.providers
    )


def _generation_ref(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _GENERATION_RE.fullmatch(value):
        raise RuntimeStateContractError(f"{field_name} must be an opaque generation reference")
    return value


def _scope_ref(value: Any) -> str:
    if not isinstance(value, str) or not _SCOPE_RE.fullmatch(value):
        raise RuntimeStateContractError("scope_ref must be an opaque scope reference")
    return value


def _exact_mapping(value: Any, keys: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys or not all(isinstance(key, str) for key in value):
        raise RuntimeStateContractError(f"{name} fields are invalid")
    return value


__all__ = [
    "DomainScope",
    "FallbackReason",
    "MAX_PROVIDER_CAPABILITIES",
    "MAX_SCOPE_SOURCE_COUNT",
    "MAX_SELECTED_SCOPES",
    "ProviderCapability",
    "ProviderHealth",
    "ProviderKind",
    "RUNTIME_STATE_SCHEMA",
    "RuntimeGeneration",
    "RuntimeHealth",
    "RuntimeHealthState",
    "RuntimeMode",
    "RuntimeStateContractError",
    "RuntimeStateRecord",
    "SelectedScope",
    "WorkerPolicy",
]
