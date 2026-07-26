"""Strict, default-off configuration values for the Unified Source Index.

This module is a configuration boundary only.  Importing or constructing its
values does not create directories, open SQLite, start a provider, schedule a
worker, read a source, or authorize a live runtime cutover.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import os
from pathlib import Path
import re
from typing import Mapping

from src.constants import DATA_DIR, UNIFIED_SOURCE_INDEX_RELATIVE_DB_PATH


# Keep this relative so even a legacy relative ODYSSEUS_DATA_DIR cannot make
# module import fail.  ``from_environment`` resolves and confines it later.
DEFAULT_SQLITE_RELATIVE_PATH = Path(UNIFIED_SOURCE_INDEX_RELATIVE_DB_PATH)
MAX_ALLOWLIST_ENTRIES = 64

_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_GENERATION_RE = re.compile(r"^usi_generation_[0-9a-f]{64}$")
_INTEGER_RE = re.compile(r"^[0-9]+$")


class UnifiedSourceIndexRuntimeConfigError(ValueError):
    """Raised when USI configuration is invalid, ambiguous, or unsafe.

    Messages deliberately name only the configuration field, never an input
    value that could contain a private path or scope identifier.
    """


class UnifiedSourceIndexRuntimeMode(StrEnum):
    DISABLED = "disabled"
    READ_ONLY = "read_only"
    SHADOW = "shadow"
    CANARY = "canary"
    ACTIVE = "active"
    DEGRADED = "degraded"
    ROLLBACK = "rollback"


_ALLOWED_DOMAINS = frozenset({"code", "document", "memory", "planning", "inbox"})


@dataclass(frozen=True, slots=True)
class UnifiedSourceIndexRuntimeConfig:
    """Validated USI runtime configuration with productive behavior off by default."""

    data_root: Path
    sqlite_path: Path
    mode: UnifiedSourceIndexRuntimeMode
    runtime_enabled: bool
    selected_generation: str | None
    allowed_owners: tuple[str, ...]
    allowed_sources: tuple[str, ...]
    allowed_domains: tuple[str, ...]
    cbm_provider_enabled: bool
    lineage_provider_enabled: bool
    raptor_provider_enabled: bool
    chroma_provider_enabled: bool
    query_max_results: int
    query_timeout_seconds: int
    worker_batch_size: int
    worker_max_concurrency: int
    worker_max_seconds: int
    shadow_sample_rate_percent: int
    stale_generation_max_age_seconds: int
    fallback_max_attempts: int
    circuit_breaker_failure_threshold: int
    circuit_breaker_reset_seconds: int
    test_fixture_mode: bool = False

    def __post_init__(self) -> None:
        data_root = _resolved_directory(self.data_root, "data_root")
        sqlite_path = _resolved_file(self.sqlite_path, "sqlite_path")
        if not self.test_fixture_mode and not _is_beneath(sqlite_path, data_root):
            raise UnifiedSourceIndexRuntimeConfigError("sqlite_path must remain beneath data_root")
        if type(self.runtime_enabled) is not bool or type(self.test_fixture_mode) is not bool:
            raise UnifiedSourceIndexRuntimeConfigError("runtime and fixture flags must be boolean")
        try:
            mode = UnifiedSourceIndexRuntimeMode(self.mode)
        except (TypeError, ValueError) as exc:
            raise UnifiedSourceIndexRuntimeConfigError("runtime mode is invalid") from exc
        if (mode is UnifiedSourceIndexRuntimeMode.DISABLED) != (not self.runtime_enabled):
            raise UnifiedSourceIndexRuntimeConfigError("runtime mode and enabled flag conflict")
        generation = _generation(self.selected_generation)
        owners = _allowlist(self.allowed_owners, "allowed_owners")
        sources = _allowlist(self.allowed_sources, "allowed_sources")
        domains = _domains(self.allowed_domains)
        if self.runtime_enabled:
            if generation is None:
                raise UnifiedSourceIndexRuntimeConfigError("selected_generation is required when enabled")
            if not owners or not sources or not domains:
                raise UnifiedSourceIndexRuntimeConfigError(
                    "enabled runtime requires non-empty owner, source, and domain allowlists"
                )
        for value, name, minimum, maximum in (
            (self.query_max_results, "query_max_results", 1, 64),
            (self.query_timeout_seconds, "query_timeout_seconds", 1, 60),
            (self.worker_batch_size, "worker_batch_size", 1, 256),
            (self.worker_max_concurrency, "worker_max_concurrency", 1, 8),
            (self.worker_max_seconds, "worker_max_seconds", 1, 600),
            (self.shadow_sample_rate_percent, "shadow_sample_rate_percent", 0, 100),
            (self.stale_generation_max_age_seconds, "stale_generation_max_age_seconds", 1, 604800),
            (self.fallback_max_attempts, "fallback_max_attempts", 0, 3),
            (self.circuit_breaker_failure_threshold, "circuit_breaker_failure_threshold", 1, 20),
            (self.circuit_breaker_reset_seconds, "circuit_breaker_reset_seconds", 1, 3600),
        ):
            _bounded_integer(value, name, minimum, maximum)
        for value, name in (
            (self.cbm_provider_enabled, "cbm_provider_enabled"),
            (self.lineage_provider_enabled, "lineage_provider_enabled"),
            (self.raptor_provider_enabled, "raptor_provider_enabled"),
            (self.chroma_provider_enabled, "chroma_provider_enabled"),
        ):
            if type(value) is not bool:
                raise UnifiedSourceIndexRuntimeConfigError(f"{name} must be boolean")
        productive_provider_enabled = any(
            (
                self.cbm_provider_enabled,
                self.lineage_provider_enabled,
                self.raptor_provider_enabled,
                self.chroma_provider_enabled,
            )
        )
        if self.test_fixture_mode and (
            self.runtime_enabled or productive_provider_enabled
        ):
            raise UnifiedSourceIndexRuntimeConfigError(
                "test fixture configuration cannot enable runtime or providers"
            )
        if mode is UnifiedSourceIndexRuntimeMode.DISABLED and productive_provider_enabled:
            raise UnifiedSourceIndexRuntimeConfigError(
                "disabled runtime cannot enable productive providers"
            )
        object.__setattr__(self, "data_root", data_root)
        object.__setattr__(self, "sqlite_path", sqlite_path)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "selected_generation", generation)
        object.__setattr__(self, "allowed_owners", owners)
        object.__setattr__(self, "allowed_sources", sources)
        object.__setattr__(self, "allowed_domains", domains)

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        data_root: str | Path | None = None,
    ) -> "UnifiedSourceIndexRuntimeConfig":
        """Parse the productive configuration from a supplied environment mapping.

        ``data_root`` is an explicit composition input, not an environment
        override.  Tests that need an arbitrary temporary SQLite file must use
        :meth:`for_test`; production environment values can only select a
        relative path under this root.
        """
        values: Mapping[str, str] = os.environ if environ is None else environ
        root = Path(DATA_DIR) if data_root is None else Path(data_root)
        relative_path = _relative_sqlite_path(
            values.get("ODYSSEUS_USI_SQLITE_PATH"),
            "ODYSSEUS_USI_SQLITE_PATH",
        )
        return cls(
            data_root=root,
            sqlite_path=_resolved_directory(root, "data_root") / relative_path,
            mode=_mode(values),
            runtime_enabled=_environment_bool(values, "ODYSSEUS_USI_RUNTIME_ENABLED", False),
            selected_generation=_environment_generation(values),
            allowed_owners=_environment_allowlist(values, "ODYSSEUS_USI_ALLOWED_OWNERS"),
            allowed_sources=_environment_allowlist(values, "ODYSSEUS_USI_ALLOWED_SOURCES"),
            allowed_domains=_environment_domains(values),
            cbm_provider_enabled=_environment_bool(values, "ODYSSEUS_USI_CBM_PROVIDER_ENABLED", False),
            lineage_provider_enabled=_environment_bool(values, "ODYSSEUS_USI_LINEAGE_PROVIDER_ENABLED", False),
            raptor_provider_enabled=_environment_bool(values, "ODYSSEUS_USI_RAPTOR_PROVIDER_ENABLED", False),
            chroma_provider_enabled=_environment_bool(values, "ODYSSEUS_USI_CHROMA_PROVIDER_ENABLED", False),
            query_max_results=_environment_integer(values, "ODYSSEUS_USI_QUERY_MAX_RESULTS", 12, 1, 64),
            query_timeout_seconds=_environment_integer(values, "ODYSSEUS_USI_QUERY_TIMEOUT_SECONDS", 10, 1, 60),
            worker_batch_size=_environment_integer(values, "ODYSSEUS_USI_WORKER_BATCH_SIZE", 32, 1, 256),
            worker_max_concurrency=_environment_integer(values, "ODYSSEUS_USI_WORKER_MAX_CONCURRENCY", 1, 1, 8),
            worker_max_seconds=_environment_integer(values, "ODYSSEUS_USI_WORKER_MAX_SECONDS", 30, 1, 600),
            shadow_sample_rate_percent=_environment_integer(values, "ODYSSEUS_USI_SHADOW_SAMPLE_RATE_PERCENT", 0, 0, 100),
            stale_generation_max_age_seconds=_environment_integer(values, "ODYSSEUS_USI_STALE_GENERATION_MAX_AGE_SECONDS", 86400, 1, 604800),
            fallback_max_attempts=_environment_integer(values, "ODYSSEUS_USI_FALLBACK_MAX_ATTEMPTS", 1, 0, 3),
            circuit_breaker_failure_threshold=_environment_integer(values, "ODYSSEUS_USI_CIRCUIT_BREAKER_FAILURE_THRESHOLD", 3, 1, 20),
            circuit_breaker_reset_seconds=_environment_integer(values, "ODYSSEUS_USI_CIRCUIT_BREAKER_RESET_SECONDS", 60, 1, 3600),
        )

    @classmethod
    def for_test(cls, sqlite_path: str | Path) -> "UnifiedSourceIndexRuntimeConfig":
        """Create an explicit disabled fixture configuration for a temp SQLite file.

        This is deliberately not controlled by an environment variable and it
        cannot enable productive runtime behavior.
        """
        path = Path(sqlite_path)
        if not path.is_absolute():
            raise UnifiedSourceIndexRuntimeConfigError("test sqlite_path must be absolute")
        return cls(
            data_root=path.parent,
            sqlite_path=path,
            mode=UnifiedSourceIndexRuntimeMode.DISABLED,
            runtime_enabled=False,
            selected_generation=None,
            allowed_owners=(),
            allowed_sources=(),
            allowed_domains=(),
            cbm_provider_enabled=False,
            lineage_provider_enabled=False,
            raptor_provider_enabled=False,
            chroma_provider_enabled=False,
            query_max_results=12,
            query_timeout_seconds=10,
            worker_batch_size=32,
            worker_max_concurrency=1,
            worker_max_seconds=30,
            shadow_sample_rate_percent=0,
            stale_generation_max_age_seconds=86400,
            fallback_max_attempts=1,
            circuit_breaker_failure_threshold=3,
            circuit_breaker_reset_seconds=60,
            test_fixture_mode=True,
        )


def _environment_bool(values: Mapping[str, str], name: str, default: bool) -> bool:
    if name not in values:
        return default
    value = values[name]
    if not isinstance(value, str) or value not in {"true", "false"}:
        raise UnifiedSourceIndexRuntimeConfigError(f"{name} must be true or false")
    return value == "true"


def _environment_integer(
    values: Mapping[str, str], name: str, default: int, minimum: int, maximum: int
) -> int:
    if name not in values:
        return default
    value = values[name]
    if not isinstance(value, str) or not _INTEGER_RE.fullmatch(value):
        raise UnifiedSourceIndexRuntimeConfigError(f"{name} must be a bounded integer")
    result = int(value)
    _bounded_integer(result, name, minimum, maximum)
    return result


def _environment_allowlist(values: Mapping[str, str], name: str) -> tuple[str, ...]:
    if name not in values:
        return ()
    raw = values[name]
    if not isinstance(raw, str) or not raw:
        raise UnifiedSourceIndexRuntimeConfigError(f"{name} must be a non-empty allowlist")
    return _allowlist(tuple(raw.split(",")), name)


def _environment_domains(values: Mapping[str, str]) -> tuple[str, ...]:
    name = "ODYSSEUS_USI_ALLOWED_DOMAINS"
    if name not in values:
        return ()
    raw = values[name]
    if not isinstance(raw, str) or not raw:
        raise UnifiedSourceIndexRuntimeConfigError(f"{name} must be a non-empty allowlist")
    return _domains(tuple(raw.split(",")))


def _environment_generation(values: Mapping[str, str]) -> str | None:
    name = "ODYSSEUS_USI_SELECTED_GENERATION"
    if name not in values:
        return None
    return _generation(values[name])


def _mode(values: Mapping[str, str]) -> UnifiedSourceIndexRuntimeMode:
    name = "ODYSSEUS_USI_RUNTIME_MODE"
    if name not in values:
        return UnifiedSourceIndexRuntimeMode.DISABLED
    try:
        return UnifiedSourceIndexRuntimeMode(values[name])
    except (TypeError, ValueError) as exc:
        raise UnifiedSourceIndexRuntimeConfigError(f"{name} is invalid") from exc


def _relative_sqlite_path(value: object, name: str) -> Path:
    if value is None:
        return DEFAULT_SQLITE_RELATIVE_PATH
    if not isinstance(value, str) or not value:
        raise UnifiedSourceIndexRuntimeConfigError(f"{name} must be a relative SQLite path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path.name in {"", ".", ".."}:
        raise UnifiedSourceIndexRuntimeConfigError(f"{name} must remain beneath data_root")
    return path


def _resolved_directory(value: str | Path, name: str) -> Path:
    try:
        return Path(value).resolve(strict=False)
    except (OSError, TypeError, ValueError) as exc:
        raise UnifiedSourceIndexRuntimeConfigError(f"{name} is invalid") from exc


def _resolved_file(value: str | Path, name: str) -> Path:
    path = _resolved_directory(value, name)
    if path.name in {"", ".", ".."}:
        raise UnifiedSourceIndexRuntimeConfigError(f"{name} is invalid")
    return path


def _is_beneath(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return path != root


def _generation(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _GENERATION_RE.fullmatch(value):
        raise UnifiedSourceIndexRuntimeConfigError("selected_generation is invalid")
    return value


def _allowlist(values: object, name: str) -> tuple[str, ...]:
    if not isinstance(values, tuple) or len(values) > MAX_ALLOWLIST_ENTRIES:
        raise UnifiedSourceIndexRuntimeConfigError(f"{name} is unbounded")
    if any(not isinstance(item, str) or not _TOKEN_RE.fullmatch(item) for item in values):
        raise UnifiedSourceIndexRuntimeConfigError(f"{name} contains an invalid entry")
    if len(set(values)) != len(values):
        raise UnifiedSourceIndexRuntimeConfigError(f"{name} contains duplicate entries")
    return tuple(sorted(values))


def _domains(values: object) -> tuple[str, ...]:
    domains = _allowlist(values, "allowed_domains")
    if any(domain not in _ALLOWED_DOMAINS for domain in domains):
        raise UnifiedSourceIndexRuntimeConfigError("allowed_domains contains an invalid entry")
    return domains


def _bounded_integer(value: object, name: str, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise UnifiedSourceIndexRuntimeConfigError(f"{name} is outside its safe bound")


__all__ = [
    "DEFAULT_SQLITE_RELATIVE_PATH",
    "MAX_ALLOWLIST_ENTRIES",
    "UnifiedSourceIndexRuntimeConfig",
    "UnifiedSourceIndexRuntimeConfigError",
    "UnifiedSourceIndexRuntimeMode",
]
