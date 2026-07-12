from src.plugin_lifecycle_readiness import (
    PLUGIN_LIFECYCLE_READINESS_SCHEMA,
    build_plugin_lifecycle_readiness,
    build_plugin_lifecycle_readiness_from_audit,
)
from src.plugin_local_audit import LocalPluginAudit, LocalPluginAuditSummary


def test_lifecycle_readiness_marks_audited_plugin_loadable_without_import(tmp_path):
    plugin_dir = tmp_path / "plugins" / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.py").write_text(
        "PLUGIN = {'name': 'Demo', 'version': '1.0.0', 'lifecycle': 'audited'}\n",
        encoding="utf-8",
    )

    readiness = build_plugin_lifecycle_readiness(str(tmp_path / "plugins")).to_dict()

    assert readiness["schema"] == PLUGIN_LIFECYCLE_READINESS_SCHEMA
    assert readiness["status"] == "ready"
    assert readiness["plugin_count"] == 1
    assert readiness["loadable_count"] == 1
    assert readiness["entries"][0]["lifecycle"] == "audited"
    assert readiness["entries"][0]["readiness"] == "ready"
    assert readiness["runtime_import_performed"] is False
    assert readiness["registry_network_performed"] is False
    assert readiness["plugin_paths_visible"] is False
    assert "plugin.py" not in str(readiness)


def test_runtime_loaded_status_promotes_loadable_audit_to_loaded():
    audit = LocalPluginAudit(
        plugin_id="demo",
        path="C:/private/plugins/demo",
        entrypoint="C:/private/plugins/demo/plugin.py",
        ok=True,
        manifest={"name": "Demo"},
    )
    summary = LocalPluginAuditSummary(True, 1, 1, (audit,))

    readiness = build_plugin_lifecycle_readiness_from_audit(
        summary,
        runtime_records=({"id": "demo", "enabled": True, "status": "loaded"},),
    )

    assert readiness.status == "ready"
    assert readiness.loaded_count == 1
    assert readiness.entries[0].lifecycle == "loaded"
    assert "runtime_status:loaded" in readiness.entries[0].evidence


def test_audit_warnings_create_degraded_operator_review_state():
    audit = LocalPluginAudit(
        plugin_id="demo",
        path="redacted",
        entrypoint="redacted",
        ok=True,
        warnings=("legacy_permission_needs_tier_review",),
        manifest={"name": "Demo"},
    )
    summary = LocalPluginAuditSummary(True, 1, 1, (audit,))

    readiness = build_plugin_lifecycle_readiness_from_audit(summary)

    assert readiness.status == "degraded"
    assert readiness.degraded_count == 1
    assert readiness.operator_review_required is True
    assert readiness.entries[0].next_action.startswith("review warnings")


def test_audit_errors_quarantine_plugin_and_block_summary():
    audit = LocalPluginAudit(
        plugin_id="bad",
        path="redacted",
        entrypoint=None,
        ok=False,
        errors=("missing_entrypoint",),
    )
    summary = LocalPluginAuditSummary(False, 1, 0, (audit,))

    readiness = build_plugin_lifecycle_readiness_from_audit(summary).to_dict()

    assert readiness["status"] == "blocked"
    assert readiness["quarantined_count"] == 1
    assert readiness["gaps"] == ("quarantined_plugins_present",)
    assert readiness["entries"][0]["lifecycle"] == "quarantined"
    assert readiness["entries"][0]["operator_review_required"] is True


def test_empty_plugin_directory_is_a_readiness_gap(tmp_path):
    plugin_root = tmp_path / "plugins"
    plugin_root.mkdir()

    readiness = build_plugin_lifecycle_readiness(str(plugin_root))

    assert readiness.status == "blocked"
    assert readiness.plugin_count == 0
    assert readiness.gaps == ("no_plugins_discovered",)
