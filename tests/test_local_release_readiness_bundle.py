import json
from pathlib import Path

from src.local_release_readiness_bundle import build_local_release_readiness_bundle


def test_local_release_readiness_bundle_uses_bundled_registry_and_plugins():
    bundle = build_local_release_readiness_bundle()

    assert bundle.plugin_gate.ok
    assert bundle.artifact_manifest.ok
    assert bundle.pipeline.report.status == "go"
    assert "REL-final-external-review" in bundle.handoff_markdown
    assert "REL-provider-proof-evidence" not in bundle.handoff_markdown
    assert "REL-test-vault-rebuild-evidence" not in bundle.handoff_markdown


def test_local_release_readiness_bundle_reports_missing_registry(tmp_path):
    plugins = tmp_path / "plugins"
    _write_plugin(plugins / "demo")

    bundle = build_local_release_readiness_bundle(
        registry_path=tmp_path / "missing-registry.json",
        plugin_directory=plugins,
    )

    assert not bundle.plugin_gate.ok
    assert bundle.plugin_gate.errors == ("registry:file:missing",)
    assert "REL-plugin-release-gate-fix" in bundle.handoff_markdown


def test_local_release_readiness_bundle_reports_invalid_json(tmp_path):
    registry = tmp_path / "registry.json"
    registry.write_text("{not json", encoding="utf-8")
    plugins = tmp_path / "plugins"
    _write_plugin(plugins / "demo")

    bundle = build_local_release_readiness_bundle(
        registry_path=registry,
        plugin_directory=plugins,
    )

    assert not bundle.plugin_gate.ok
    assert bundle.plugin_gate.errors == ("registry:file:invalid_json",)


def test_local_release_readiness_bundle_to_dict_is_stable_shape(tmp_path):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"plugins": [_entry("demo")]}), encoding="utf-8")
    plugins = tmp_path / "plugins"
    _write_plugin(plugins / "demo")

    payload = build_local_release_readiness_bundle(
        registry_path=registry,
        plugin_directory=plugins,
    ).to_dict()

    assert payload["plugin_gate"]["ok"] is True
    assert payload["local_plugin_audit"]["ok"] is True
    assert payload["local_plugin_audit"]["failing_ids"] == ()
    assert payload["artifact_manifest"]["ok"] is True
    assert payload["plugin_markdown"].startswith("# Plugin Release Gate")
    assert payload["local_plugin_audit_markdown"].startswith("# Local Plugin Audit")
    assert payload["artifact_markdown"].startswith("# Release Artifact Manifest")
    assert payload["pipeline"]["report"]["status"] == "go"
    assert payload["handoff_markdown"].startswith("# Release Orchestration Status")


def test_local_release_readiness_bundle_reports_artifact_manifest(tmp_path):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"plugins": [_entry("demo")]}), encoding="utf-8")
    plugins = tmp_path / "plugins"
    _write_plugin(plugins / "demo")

    payload = build_local_release_readiness_bundle(
        registry_path=registry,
        plugin_directory=plugins,
        artifact_root=tmp_path,
    ).to_dict()

    assert payload["artifact_manifest"]["ok"] is False
    assert "docs/plans/1.0-evidence-release-checklist.md" in payload["artifact_manifest"]["missing_required_paths"]


def _write_plugin(path: Path) -> None:
    path.mkdir(parents=True)
    (path / "plugin.py").write_text(
        "PLUGIN = {'name': 'Demo', 'version': '1.0.0', 'permission': 'admin'}\n",
        encoding="utf-8",
    )


def _entry(plugin_id: str):
    return {
        "id": plugin_id,
        "name": "Demo",
        "version": "1.0.0",
        "category": "Testing",
        "description": "Demo plugin",
        "homepage": "https://example.com/demo",
        "download": "https://example.com/demo.zip",
        "sha256": "a" * 64,
    }
