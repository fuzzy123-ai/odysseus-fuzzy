from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_plan_json_is_reincluded_after_docs_exclusions():
    lines = [
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    docs_exclusion = lines.index("docs/")
    markdown_exclusion = lines.index("*.md")
    assert lines.index("!docs/") > max(docs_exclusion, markdown_exclusion)
    assert lines.index("docs/*") > lines.index("!docs/")
    assert lines.index("!docs/plans/") > lines.index("docs/*")
    assert lines.index("docs/plans/*") > lines.index("!docs/plans/")
    assert lines.index("!docs/plans/*.json") > lines.index("docs/plans/*")
    assert (ROOT / "docs" / "plans" / "mvp-roadmap-runner-state.json").is_file()
