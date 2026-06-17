from src.plugin_release_gate import PluginReleaseGate
from src.plugin_release_markdown import render_plugin_release_gate_markdown


def test_plugin_release_gate_markdown_renders_pass():
    markdown = render_plugin_release_gate_markdown(
        PluginReleaseGate(
            ok=True,
            registry_ok=True,
            local_plugins_ok=True,
            registry_plugin_count=3,
            local_plugin_count=3,
        )
    )

    assert markdown.startswith("# Plugin Release Gate")
    assert "Status: **PASS**" in markdown
    assert "| Registry | pass | 3 |" in markdown
    assert "| Local plugins | pass | 3 |" in markdown


def test_plugin_release_gate_markdown_renders_errors_and_warnings():
    markdown = render_plugin_release_gate_markdown(
        PluginReleaseGate(
            ok=False,
            registry_ok=False,
            local_plugins_ok=True,
            registry_plugin_count=0,
            local_plugin_count=2,
            errors=("registry:file:missing",),
            warnings=("local:demo:missing_version",),
        )
    )

    assert "Status: **BLOCKED**" in markdown
    assert "| Registry | blocked | 0 |" in markdown
    assert "- `registry:file:missing`" in markdown
    assert "- `local:demo:missing_version`" in markdown
