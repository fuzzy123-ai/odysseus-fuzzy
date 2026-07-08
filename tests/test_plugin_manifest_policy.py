import ast
import json
from pathlib import Path

from src.plugin_manifest_policy import (
    summarize_registry,
    validate_local_manifest,
    validate_registry_document,
    validate_registry_entry,
)


def test_validates_bundled_registry_document():
    registry = json.loads(Path("plugins/registry.json").read_text(encoding="utf-8"))

    report = validate_registry_document(registry)

    assert report.ok
    assert report.normalized["count"] == 3
    assert report.normalized["plugin_ids"] == ("cloudflare_tunnel", "image_splat", "mcp_server")


def test_registry_blocks_duplicate_ids():
    registry = {
        "version": 1,
        "plugins": [
            _entry("demo"),
            _entry("demo"),
        ],
    }

    report = validate_registry_document(registry)

    assert not report.ok
    assert "duplicate_plugin_id" in report.error_codes


def test_registry_entry_requires_https_download_and_digest():
    entry = _entry("demo", download="http://example.com/demo.zip", sha256="abc")

    report = validate_registry_entry(entry)

    assert not report.ok
    assert "download_not_https" in report.error_codes
    assert "invalid_sha256" in report.error_codes


def test_registry_rejects_unsafe_plugin_id():
    report = validate_registry_entry(_entry("../evil"))

    assert not report.ok
    assert "invalid_plugin_id" in report.error_codes


def test_registry_summary_is_compact_and_deterministic():
    registry = {"plugins": [_entry("zeta"), _entry("alpha")]}

    summary = summarize_registry(registry)

    assert summary == {
        "ok": True,
        "count": 2,
        "plugin_ids": ("alpha", "zeta"),
        "errors": (),
        "warnings": (),
    }


def test_local_manifest_accepts_system_health_checker_manifest():
    plugin_path = Path("plugins/system_health_checker/plugin.py")

    report = validate_local_manifest(_load_plugin_manifest(plugin_path))

    assert report.ok
    assert report.normalized["permission"] == "admin"
    assert report.normalized["ui.open"] == "/api/plugins/system_health_checker/app"


def test_local_manifest_defaults_to_admin_but_warns_without_version():
    report = validate_local_manifest({"name": "Scratch"})

    assert report.ok
    assert report.normalized["permission"] == "admin"
    assert "missing_version" in report.warning_codes


def test_local_manifest_accepts_shared_schema_fields():
    report = validate_local_manifest(
        {
            "name": "Shared Schema Demo",
            "version": "1.2.3",
            "permission": "owner_scoped_write",
            "capabilities": ["Local_API", "notes.search", "local_api"],
            "compatibility": {
                "min_odysseus": "1.0.0",
                "max_odysseus": "2.0.0-beta",
            },
            "lifecycle": "loadable",
            "manifest_version": "1.0",
        }
    )

    assert report.ok
    assert report.normalized["permission"] == "owner_scoped_write"
    assert report.normalized["capabilities"] == ("local_api", "notes.search")
    assert report.normalized["compatibility"] == {
        "min_odysseus": "1.0.0",
        "max_odysseus": "2.0.0-beta",
    }
    assert report.normalized["lifecycle"] == "loadable"
    assert report.normalized["manifest_version"] == "1.0"


def test_local_manifest_keeps_legacy_permission_and_schema_version_compatibility():
    report = validate_local_manifest(
        {
            "name": "Legacy",
            "version": "1.0.0",
            "permission": "user",
            "schema_version": 1,
        }
    )

    assert report.ok
    assert report.normalized["permission"] == "user"
    assert report.normalized["schema_version"] == "1"


def test_local_manifest_rejects_unsafe_ui_path_and_permission():
    report = validate_local_manifest(
        {
            "name": "Bad",
            "version": "1.0.0",
            "permission": "root",
            "ui": {"open": "javascript:alert(1)"},
        }
    )

    assert not report.ok
    assert "invalid_permission" in report.error_codes
    assert "unsafe_ui_open" in report.error_codes


def test_local_manifest_rejects_invalid_capabilities():
    report = validate_local_manifest(
        {
            "name": "Bad Capabilities",
            "version": "1.0.0",
            "capabilities": ["local_api", "../secret"],
        }
    )

    assert not report.ok
    assert "invalid_capabilities" in report.error_codes


def test_local_manifest_rejects_invalid_compatibility():
    report = validate_local_manifest(
        {
            "name": "Bad Compatibility",
            "version": "1.0.0",
            "compatibility": {"min_odysseus": "soon"},
        }
    )

    assert not report.ok
    assert "invalid_compatibility" in report.error_codes


def test_local_manifest_rejects_invalid_lifecycle():
    report = validate_local_manifest(
        {
            "name": "Bad Lifecycle",
            "version": "1.0.0",
            "lifecycle": "executing",
        }
    )

    assert not report.ok
    assert "invalid_lifecycle" in report.error_codes


def test_local_manifest_requires_requires_list():
    report = validate_local_manifest(
        {
            "name": "Bad Requires",
            "version": "1.0.0",
            "requires": "requests",
        }
    )

    assert not report.ok
    assert "invalid_requires" in report.error_codes


def _entry(plugin_id: str, **overrides):
    entry = {
        "id": plugin_id,
        "name": "Demo",
        "version": "1.0.0",
        "category": "Testing",
        "description": "Demo plugin",
        "homepage": "https://example.com/demo",
        "download": "https://example.com/demo.zip",
        "sha256": "a" * 64,
    }
    entry.update(overrides)
    return entry


def _load_plugin_manifest(path: Path):
    module = ast.parse(path.read_text(encoding="utf-8"))
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "PLUGIN":
                    return ast.literal_eval(node.value)
    raise AssertionError(f"no PLUGIN manifest in {path}")
