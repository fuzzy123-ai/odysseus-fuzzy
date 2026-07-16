from pathlib import Path

from src.tool_catalog import (
    CATALOG_V2_DEFAULT_ENABLED,
    CATALOG_V2_ENV,
    CATALOG_V2_FEATURE_FLAG,
    catalog_v2_enabled,
)


ROOT = Path(__file__).resolve().parents[1]


def test_catalog_v2_runtime_switch_is_default_off_and_fail_closed():
    assert CATALOG_V2_FEATURE_FLAG == "tool-catalog-v2"
    assert CATALOG_V2_ENV == "ODYSSEUS_TOOL_CATALOG_V2_ENABLED"
    assert CATALOG_V2_DEFAULT_ENABLED is False
    assert catalog_v2_enabled({}) is False
    assert catalog_v2_enabled({CATALOG_V2_ENV: "false"}) is False
    assert catalog_v2_enabled({CATALOG_V2_ENV: "unexpected"}) is False


def test_catalog_v2_runtime_switch_accepts_only_explicit_true_values():
    for value in ("1", "true", "TRUE", " yes ", "on"):
        assert catalog_v2_enabled({CATALOG_V2_ENV: value}) is True


def test_compose_passes_catalog_v2_switch_with_default_off():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert (
        "ODYSSEUS_TOOL_CATALOG_V2_ENABLED="
        "${ODYSSEUS_TOOL_CATALOG_V2_ENABLED:-false}"
    ) in compose
