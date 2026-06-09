import shutil
from pathlib import Path

from fastapi import FastAPI

from src.plugin_system import PluginManager


def test_obsidian_plugin_loads_through_plugin_manager(tmp_path, monkeypatch):
    repo_root = Path(__file__).resolve().parents[1]
    source = repo_root / "plugins" / "obsidian"
    plugins_dir = tmp_path / "plugins"
    target = plugins_dir / "obsidian"
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"),
    )
    monkeypatch.setenv("ODYSSEUS_DATA_DIR", str(tmp_path / "data"))

    app = FastAPI()
    manager = PluginManager(app=app, directory=str(plugins_dir))
    manager.load_enabled(app)

    record = manager.records["obsidian"]
    paths = {getattr(route, "path", "") for route in app.router.routes}

    assert record.status == "loaded", record.error
    assert record.public()["ui"] == {
        "open": "/api/plugins/obsidian/app",
        "label": "Open Vault",
    }
    assert "/api/plugins/obsidian/app" in paths
    assert "/api/plugins/obsidian/files" in paths
    assert "/api/plugins/obsidian/web/{filename:path}" in paths
