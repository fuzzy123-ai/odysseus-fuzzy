from __future__ import annotations

from pathlib import Path

import pytest

from src.unified_source_index_runtime_config import (
    DEFAULT_SQLITE_RELATIVE_PATH,
    UnifiedSourceIndexRuntimeConfig,
    UnifiedSourceIndexRuntimeConfigError,
    UnifiedSourceIndexRuntimeMode,
)


PRODUCTIVE_BOOLEAN_ENV_NAMES = (
    "ODYSSEUS_USI_RUNTIME_ENABLED",
    "ODYSSEUS_USI_CBM_PROVIDER_ENABLED",
    "ODYSSEUS_USI_LINEAGE_PROVIDER_ENABLED",
    "ODYSSEUS_USI_RAPTOR_PROVIDER_ENABLED",
    "ODYSSEUS_USI_CHROMA_PROVIDER_ENABLED",
)


def _generation() -> str:
    return "usi_generation_" + "a" * 64


def _enabled_environment(**overrides: str) -> dict[str, str]:
    values = {
        "ODYSSEUS_USI_RUNTIME_ENABLED": "true",
        "ODYSSEUS_USI_RUNTIME_MODE": "shadow",
        "ODYSSEUS_USI_SELECTED_GENERATION": _generation(),
        "ODYSSEUS_USI_ALLOWED_OWNERS": "owner_a",
        "ODYSSEUS_USI_ALLOWED_SOURCES": "source_a",
        "ODYSSEUS_USI_ALLOWED_DOMAINS": "document",
    }
    values.update(overrides)
    return values


def test_defaults_are_disabled_and_keep_the_database_beneath_selected_data_root(tmp_path: Path):
    data_root = tmp_path / "data"
    sqlite_path = data_root / DEFAULT_SQLITE_RELATIVE_PATH
    assert not data_root.exists()
    assert not sqlite_path.exists()

    config = UnifiedSourceIndexRuntimeConfig.from_environment({}, data_root=data_root)

    assert config.mode is UnifiedSourceIndexRuntimeMode.DISABLED
    assert config.runtime_enabled is False
    assert config.sqlite_path == (config.data_root / DEFAULT_SQLITE_RELATIVE_PATH).resolve()
    assert config.sqlite_path.is_relative_to(config.data_root)
    assert config.allowed_owners == ()
    assert config.allowed_sources == ()
    assert config.allowed_domains == ()
    assert not data_root.exists()
    assert not sqlite_path.exists()
    assert not any(
        (
            config.cbm_provider_enabled,
            config.lineage_provider_enabled,
            config.raptor_provider_enabled,
            config.chroma_provider_enabled,
        )
    )


@pytest.mark.parametrize("name", PRODUCTIVE_BOOLEAN_ENV_NAMES)
@pytest.mark.parametrize("value", ["True", "FALSE", "1", "yes", "", " false "])
def test_productive_boolean_environment_values_are_strictly_parsed(
    name: str, value: str, tmp_path: Path
):
    with pytest.raises(UnifiedSourceIndexRuntimeConfigError, match=name):
        UnifiedSourceIndexRuntimeConfig.from_environment(
            {name: value}, data_root=tmp_path
        )


def test_provider_flags_are_independent_and_default_off(tmp_path: Path):
    config = UnifiedSourceIndexRuntimeConfig.from_environment(
        _enabled_environment(
            ODYSSEUS_USI_CBM_PROVIDER_ENABLED="true",
            ODYSSEUS_USI_LINEAGE_PROVIDER_ENABLED="false",
            ODYSSEUS_USI_RAPTOR_PROVIDER_ENABLED="true",
            ODYSSEUS_USI_CHROMA_PROVIDER_ENABLED="false",
        ),
        data_root=tmp_path,
    )

    assert config.cbm_provider_enabled is True
    assert config.lineage_provider_enabled is False
    assert config.raptor_provider_enabled is True
    assert config.chroma_provider_enabled is False


@pytest.mark.parametrize(
    "name",
    PRODUCTIVE_BOOLEAN_ENV_NAMES[1:],
)
def test_disabled_runtime_rejects_every_productive_provider_flag(name: str, tmp_path: Path):
    with pytest.raises(UnifiedSourceIndexRuntimeConfigError, match="disabled runtime"):
        UnifiedSourceIndexRuntimeConfig.from_environment({name: "true"}, data_root=tmp_path)


@pytest.mark.parametrize("path", ["../outside.sqlite3", "/outside.sqlite3", ""])
def test_productive_sqlite_override_cannot_escape_selected_data_root(path: str, tmp_path: Path):
    with pytest.raises(UnifiedSourceIndexRuntimeConfigError):
        UnifiedSourceIndexRuntimeConfig.from_environment(
            {"ODYSSEUS_USI_SQLITE_PATH": path}, data_root=tmp_path / "data"
        )


def test_temporary_database_override_is_explicit_and_cannot_enable_runtime(tmp_path: Path):
    fixture_path = (tmp_path / "fixture" / "usi.sqlite3").resolve()
    config = UnifiedSourceIndexRuntimeConfig.for_test(fixture_path)

    assert config.test_fixture_mode is True
    assert config.runtime_enabled is False
    assert config.sqlite_path == fixture_path
    assert config.data_root == fixture_path.parent


@pytest.mark.parametrize(
    "provider_name",
    (
        "cbm_provider_enabled",
        "lineage_provider_enabled",
        "raptor_provider_enabled",
        "chroma_provider_enabled",
    ),
)
def test_test_fixture_mode_rejects_enabled_productive_provider_flags(
    provider_name: str, tmp_path: Path
):
    config = UnifiedSourceIndexRuntimeConfig.for_test((tmp_path / "fixture.sqlite3").resolve())
    values = {field: getattr(config, field) for field in config.__dataclass_fields__}
    values[provider_name] = True

    with pytest.raises(UnifiedSourceIndexRuntimeConfigError, match="fixture configuration"):
        UnifiedSourceIndexRuntimeConfig(**values)


@pytest.mark.parametrize(
    "name,value",
    [
        ("ODYSSEUS_USI_ALLOWED_OWNERS", "*"),
        ("ODYSSEUS_USI_ALLOWED_OWNERS", ""),
        ("ODYSSEUS_USI_ALLOWED_SOURCES", "source_a,*"),
        ("ODYSSEUS_USI_ALLOWED_DOMAINS", "*"),
    ],
)
def test_enabled_runtime_rejects_empty_or_wildcard_scope_allowlists(
    name: str, value: str, tmp_path: Path
):
    with pytest.raises(UnifiedSourceIndexRuntimeConfigError):
        UnifiedSourceIndexRuntimeConfig.from_environment(
            _enabled_environment(**{name: value}), data_root=tmp_path
        )


def test_enabled_runtime_requires_every_selected_scope_dimension(tmp_path: Path):
    values = _enabled_environment()
    del values["ODYSSEUS_USI_ALLOWED_SOURCES"]

    with pytest.raises(UnifiedSourceIndexRuntimeConfigError, match="owner, source, and domain"):
        UnifiedSourceIndexRuntimeConfig.from_environment(values, data_root=tmp_path)


@pytest.mark.parametrize(
    "name,value",
    [
        ("ODYSSEUS_USI_QUERY_MAX_RESULTS", "0"),
        ("ODYSSEUS_USI_WORKER_MAX_CONCURRENCY", "9"),
        ("ODYSSEUS_USI_SHADOW_SAMPLE_RATE_PERCENT", "101"),
        ("ODYSSEUS_USI_CIRCUIT_BREAKER_RESET_SECONDS", "1.5"),
    ],
)
def test_invalid_or_unbounded_budget_values_fail_closed(name: str, value: str, tmp_path: Path):
    with pytest.raises(UnifiedSourceIndexRuntimeConfigError, match=name):
        UnifiedSourceIndexRuntimeConfig.from_environment({name: value}, data_root=tmp_path)


def test_runtime_mode_and_enabled_flag_must_agree(tmp_path: Path):
    with pytest.raises(UnifiedSourceIndexRuntimeConfigError, match="mode and enabled"):
        UnifiedSourceIndexRuntimeConfig.from_environment(
            {"ODYSSEUS_USI_RUNTIME_MODE": "shadow"}, data_root=tmp_path
        )


def test_config_accessor_is_disabled_and_does_not_create_usi_sqlite(tmp_path: Path):
    from src.config import get_unified_source_index_runtime_config

    data_root = tmp_path / "accessor-data"
    config = get_unified_source_index_runtime_config({}, data_root=data_root)

    assert config.mode is UnifiedSourceIndexRuntimeMode.DISABLED
    assert config.runtime_enabled is False
    assert config.sqlite_path == (data_root / DEFAULT_SQLITE_RELATIVE_PATH).resolve()
    assert not config.sqlite_path.exists()
