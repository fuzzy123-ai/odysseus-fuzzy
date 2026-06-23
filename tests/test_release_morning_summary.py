import json
from pathlib import Path

from src.local_release_readiness_bundle import build_local_release_readiness_bundle
from src.release_morning_summary import build_current_release_morning_summary, build_release_morning_summary


def test_current_release_morning_summary_is_compact_and_version_gated():
    summary = build_current_release_morning_summary()

    assert summary.status == "blocked"
    assert summary.external_release_go is False
    assert summary.plugin_gate_ok is True
    assert summary.local_plugin_audit_ok is True
    assert summary.artifact_manifest_ok is True
    assert summary.active_owners == ("Charlie",)
    assert summary.next_action_ids == ("REL-mvp-version-1-gate",)
    assert summary.missing_required_artifacts == ()
    assert summary.local_plugin_failing_ids == ()


def test_release_morning_summary_reports_missing_artifacts(tmp_path):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"plugins": [_entry("demo")]}), encoding="utf-8")
    plugins = tmp_path / "plugins"
    _write_plugin(plugins / "demo")

    bundle = build_local_release_readiness_bundle(
        registry_path=registry,
        plugin_directory=plugins,
        artifact_root=tmp_path,
    )
    summary = build_release_morning_summary(bundle)

    assert summary.plugin_gate_ok is True
    assert summary.local_plugin_audit_ok is True
    assert summary.artifact_manifest_ok is False
    assert "docs/plans/1.0-evidence-release-checklist.md" in summary.missing_required_artifacts


def test_release_morning_summary_to_dict_is_stable():
    payload = build_current_release_morning_summary().to_dict()

    assert payload["status"] == "blocked"
    assert payload["external_release_go"] is False
    assert payload["plugin_gate_ok"] is True
    assert payload["local_plugin_audit_ok"] is True
    assert payload["artifact_manifest_ok"] is True
    assert payload["active_owners"] == ("Charlie",)
    assert payload["next_action_ids"] == ("REL-mvp-version-1-gate",)
    assert payload["missing_required_artifacts"] == ()
    assert payload["local_plugin_failing_ids"] == ()


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
