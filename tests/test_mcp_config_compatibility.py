from src.mcp_config_compatibility import (
    MCP_CONFIG_COMPATIBILITY_SCHEMA,
    MCP_DEFAULT_CONFIG,
    normalize_mcp_config,
)


def test_mcp_config_defaults_to_disabled_readonly():
    report = normalize_mcp_config(None)
    payload = report.to_dict()

    assert report.config == MCP_DEFAULT_CONFIG
    assert payload["schema"] == MCP_CONFIG_COMPATIBILITY_SCHEMA
    assert payload["default_disabled"] is True
    assert payload["live_client_connection_allowed"] is False
    assert payload["token_value_visible"] is False
    assert payload["secret_value_visible"] is False


def test_mcp_config_migrates_legacy_scope_aliases():
    report = normalize_mcp_config({
        "enabled": "true",
        "owner_writes": "yes",
        "private_read": 1,
        "fs_read": "on",
        "generic_api": "go",
    })

    assert report.config == {
        "enabled": True,
        "allow_owner_scoped_writes": True,
        "allow_private_reads": True,
        "allow_filesystem_reads": True,
        "allow_generic_api": True,
    }
    assert report.migrated_keys == (
        "fs_read->allow_filesystem_reads",
        "generic_api->allow_generic_api",
        "owner_writes->allow_owner_scoped_writes",
        "private_read->allow_private_reads",
    )


def test_mcp_config_ignores_unknown_keys_and_expose_all():
    report = normalize_mcp_config({
        "enabled": True,
        "expose_all": True,
        "token": "secret",
        "random": "value",
    })
    payload = report.to_dict()

    assert report.config["enabled"] is True
    assert report.expose_all_requested is True
    assert payload["expose_all_supported"] is False
    assert payload["ignored_keys"] == ("expose_all", "random", "token")


def test_mcp_config_rejects_non_mapping_as_default_report():
    report = normalize_mcp_config(["not", "a", "mapping"])

    assert report.config == MCP_DEFAULT_CONFIG
    assert report.migrated_keys == ()
    assert report.ignored_keys == ()
