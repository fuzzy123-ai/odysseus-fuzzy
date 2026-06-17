from pathlib import Path

from src.plugin_local_audit import audit_plugins_directory
from src.plugin_local_audit_markdown import render_local_plugin_audit_markdown


def test_local_plugin_audit_markdown_renders_bundled_plugins():
    markdown = render_local_plugin_audit_markdown(audit_plugins_directory("plugins"))

    assert markdown.startswith("# Local Plugin Audit")
    assert "Status: **PASS**" in markdown
    assert "`system_health_checker`" in markdown


def test_local_plugin_audit_markdown_renders_errors(tmp_path):
    good = tmp_path / "good"
    bad = tmp_path / "bad"
    good.mkdir()
    bad.mkdir()
    (good / "plugin.py").write_text(
        "PLUGIN = {'name': 'Good', 'version': '1.0.0'}\n",
        encoding="utf-8",
    )

    markdown = render_local_plugin_audit_markdown(audit_plugins_directory(tmp_path))

    assert "Status: **BLOCKED**" in markdown
    assert "| `bad` | blocked | `none` | `missing_entrypoint` | `none` |" in markdown
    assert "| `good` | pass | `" in markdown


def test_local_plugin_audit_markdown_renders_empty_directory(tmp_path):
    markdown = render_local_plugin_audit_markdown(audit_plugins_directory(tmp_path))

    assert "Plugins discovered: `0`" in markdown
    assert "| `none` | blocked | `none` | `no_plugins_found` | `none` |" in markdown


def test_local_plugin_audit_markdown_renders_missing_directory(tmp_path):
    markdown = render_local_plugin_audit_markdown(audit_plugins_directory(Path(tmp_path) / "missing"))

    assert "Status: **BLOCKED**" in markdown
    assert "| `none` | blocked | `none` | `no_plugins_found` | `none` |" in markdown
