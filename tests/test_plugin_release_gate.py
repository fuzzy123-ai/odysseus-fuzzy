import json
from pathlib import Path

from src.plugin_release_gate import evaluate_plugin_release_gate


def test_plugin_release_gate_passes_for_bundled_registry_and_plugins():
    registry = json.loads(Path("plugins/registry.json").read_text(encoding="utf-8"))

    gate = evaluate_plugin_release_gate(registry, "plugins")

    assert gate.ok
    assert gate.registry_ok
    assert gate.local_plugins_ok
    assert gate.registry_plugin_count == 3
    assert gate.local_plugin_count >= 2
    assert gate.errors == ()


def test_plugin_release_gate_blocks_bad_registry_even_with_good_local_plugin(tmp_path):
    plugin_dir = tmp_path / "plugins"
    demo = plugin_dir / "demo"
    demo.mkdir(parents=True)
    (demo / "plugin.py").write_text(
        "PLUGIN = {'name': 'Demo', 'version': '1.0.0'}\n",
        encoding="utf-8",
    )
    bad_registry = {"plugins": [_entry("demo", download="http://example.com/demo.zip")]}

    gate = evaluate_plugin_release_gate(bad_registry, str(plugin_dir))

    assert not gate.ok
    assert not gate.registry_ok
    assert gate.local_plugins_ok
    assert "registry:plugins[0].download:download_not_https" in gate.errors


def test_plugin_release_gate_blocks_bad_local_plugin_even_with_good_registry(tmp_path):
    plugin_dir = tmp_path / "plugins"
    bad = plugin_dir / "bad"
    bad.mkdir(parents=True)
    (bad / "plugin.py").write_text("PLUGIN = {'version': '1.0.0'}\n", encoding="utf-8")

    gate = evaluate_plugin_release_gate({"plugins": [_entry("good")]}, str(plugin_dir))

    assert not gate.ok
    assert gate.registry_ok
    assert not gate.local_plugins_ok
    assert "local:bad:missing_required_field" in gate.errors


def test_plugin_release_gate_reports_missing_plugin_directory():
    gate = evaluate_plugin_release_gate({"plugins": [_entry("good")]}, "does-not-exist")

    assert not gate.ok
    assert gate.errors == ("local:plugins:missing_directory",)


def test_plugin_release_gate_to_dict_is_stable(tmp_path):
    plugin_dir = tmp_path / "plugins"
    demo = plugin_dir / "demo"
    demo.mkdir(parents=True)
    (demo / "plugin.py").write_text(
        "PLUGIN = {'name': 'Demo', 'version': '1.0.0'}\n",
        encoding="utf-8",
    )

    gate = evaluate_plugin_release_gate({"plugins": [_entry("demo")]}, str(plugin_dir))

    assert gate.to_dict() == {
        "ok": True,
        "registry_ok": True,
        "local_plugins_ok": True,
        "registry_plugin_count": 1,
        "local_plugin_count": 1,
        "errors": (),
        "warnings": (),
    }


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
