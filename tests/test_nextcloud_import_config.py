from pathlib import Path

from src.nextcloud_import_config import (
    CONFIG_SCHEMA,
    default_nextcloud_import_config,
    load_nextcloud_import_config,
    normalize_nextcloud_import_config,
)


def test_default_nextcloud_import_config_is_dry_run_without_absolute_source_path():
    config = default_nextcloud_import_config()

    assert config["schema"] == CONFIG_SCHEMA
    assert config["mode"] == "dry_run"
    assert "source_root" not in config
    assert config["source_root_env"] == "ODYSSEUS_NEXTCLOUD_IMPORT_ROOT"
    assert "Privat" in config["sensitive_roots"]
    assert ".exe" in config["binary_extensions"]


def test_loads_versioned_config_file():
    config = load_nextcloud_import_config(Path("config/nextcloud_import_config.json"))

    assert config["schema"] == CONFIG_SCHEMA
    assert config["mode"] == "dry_run"
    assert config["source_id"] == "nextcloud-main"
    assert ".pdf" in config["document_extensions_initial"]
    assert config["software_archives"]["review_required"] is True


def test_rejects_persisted_source_root():
    try:
        normalize_nextcloud_import_config({"source_root": "C:/private/source"})
    except ValueError as exc:
        assert "source_root must be provided at runtime" in str(exc)
    else:
        raise AssertionError("persisted absolute source root should be rejected")
