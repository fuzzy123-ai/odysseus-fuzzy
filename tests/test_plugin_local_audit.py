from pathlib import Path

from src.plugin_local_audit import audit_plugin_path, audit_plugins_directory


def test_audits_bundled_plugins_without_importing_them():
    summary = audit_plugins_directory("plugins")

    assert summary.ok
    assert summary.plugin_count >= 2
    assert "example" not in summary.failing_ids
    assert "system_health_checker" not in summary.failing_ids


def test_reports_missing_entrypoint(tmp_path):
    plugin_dir = tmp_path / "empty"
    plugin_dir.mkdir()

    audit = audit_plugin_path("empty", plugin_dir)

    assert not audit.ok
    assert audit.errors == ("missing_entrypoint",)
    assert audit.entrypoint is None


def test_reports_missing_manifest(tmp_path):
    plugin_dir = tmp_path / "no_manifest"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.py").write_text("def setup(ctx): pass\n", encoding="utf-8")

    audit = audit_plugin_path("no_manifest", plugin_dir)

    assert not audit.ok
    assert audit.errors == ("missing_manifest",)


def test_reports_non_literal_manifest(tmp_path):
    plugin_dir = tmp_path / "dynamic"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.py").write_text(
        "def build(): return {'name': 'Dynamic'}\nPLUGIN = build()\n",
        encoding="utf-8",
    )

    audit = audit_plugin_path("dynamic", plugin_dir)

    assert not audit.ok
    assert audit.errors == ("manifest_not_literal",)


def test_single_file_plugin_is_supported(tmp_path):
    plugin_file = tmp_path / "single_plugin.py"
    plugin_file.write_text(
        "PLUGIN = {'name': 'Single', 'version': '1.0.0', 'permission': 'admin'}\n",
        encoding="utf-8",
    )

    audit = audit_plugin_path("single", plugin_file)

    assert audit.ok
    assert audit.manifest["name"] == "Single"


def test_local_audit_applies_capability_boundary(tmp_path):
    plugin_dir = tmp_path / "unsafe_ui"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.py").write_text(
        "PLUGIN = {'name': 'Unsafe UI', 'version': '1.0.0', 'kind': 'ui', 'capabilities': ['host_metrics']}\n",
        encoding="utf-8",
    )

    audit = audit_plugin_path("unsafe_ui", plugin_dir)

    assert not audit.ok
    assert audit.errors == ("ui_plugin_requests_host_capability",)


def test_local_audit_allows_host_agent_with_local_api(tmp_path):
    plugin_dir = tmp_path / "health_agent"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.py").write_text(
        "PLUGIN = {'name': 'Health Agent', 'version': '1.0.0', 'kind': 'host-agent', 'capabilities': ['local_api', 'host_metrics']}\n",
        encoding="utf-8",
    )

    audit = audit_plugin_path("health_agent", plugin_dir)

    assert audit.ok
    assert audit.errors == ()
    assert audit.warnings == ()


def test_directory_summary_is_sorted_and_counts_loaded_plugins(tmp_path):
    good = tmp_path / "good"
    bad = tmp_path / "bad"
    good.mkdir()
    bad.mkdir()
    (good / "plugin.py").write_text(
        "PLUGIN = {'name': 'Good', 'version': '1.0.0'}\n",
        encoding="utf-8",
    )

    summary = audit_plugins_directory(tmp_path)

    assert not summary.ok
    assert summary.plugin_count == 2
    assert summary.loaded_count == 1
    assert [audit.plugin_id for audit in summary.audits] == ["bad", "good"]
    assert summary.failing_ids == ("bad",)
