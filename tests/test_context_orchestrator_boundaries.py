import ast
from pathlib import Path


def test_core_does_not_import_obsidian_plugin():
    repo = Path(__file__).resolve().parents[1]
    offenders = []
    for path in (repo / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                if module == "plugins.obsidian" or module.startswith("plugins.obsidian.") or module == "obsidian.backend" or module.startswith("obsidian.backend."):
                    offenders.append(f"{path.relative_to(repo)}:{node.lineno}:{module}")

    assert offenders == []
