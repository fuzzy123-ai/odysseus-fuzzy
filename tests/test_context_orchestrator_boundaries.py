from pathlib import Path


def test_core_does_not_import_obsidian_plugin():
    repo = Path(__file__).resolve().parents[1]
    forbidden = ("plugins.obsidian", "plugins/obsidian", "plugins\\obsidian", "obsidian.backend")
    offenders = []
    for path in (repo / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if any(item in text for item in forbidden):
            offenders.append(str(path.relative_to(repo)))

    assert offenders == []
