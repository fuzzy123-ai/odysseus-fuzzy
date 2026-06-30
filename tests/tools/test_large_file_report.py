from pathlib import Path

from scripts.large_file_report import build_report, render_markdown


def _write_lines(path: Path, count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"line {idx}" for idx in range(count)) + "\n", encoding="utf-8")


def _item(report, path):
    return next(item for item in report["items"] if item["path"] == path)


def test_large_file_report_uses_refactoring_threshold_bands(tmp_path):
    _write_lines(tmp_path / "src" / "monitor.py", 600)
    _write_lines(tmp_path / "src" / "warning.py", 801)
    _write_lines(tmp_path / "src" / "candidate.py", 2001)
    _write_lines(tmp_path / "src" / "normal.py", 599)

    report = build_report(tmp_path)

    assert _item(report, "src/monitor.py")["band"] == "monitor"
    assert _item(report, "src/warning.py")["band"] == "warning"
    assert _item(report, "src/candidate.py")["band"] == "candidate"
    assert all(item["path"] != "src/normal.py" for item in report["items"])
    assert report["production_runtime_summary"] == {"monitor": 1, "warning": 1, "candidate": 1}


def test_large_file_report_excludes_docs_tests_and_mockups_from_runtime_view(tmp_path):
    _write_lines(tmp_path / "docs" / "plans" / "big.md", 2001)
    _write_lines(tmp_path / "tests" / "test_big.py", 2001)
    _write_lines(tmp_path / "static" / "mockups" / "demo.html", 2001)
    _write_lines(tmp_path / "routes" / "real.py", 2001)

    report = build_report(tmp_path)

    assert _item(report, "docs/plans/big.md")["production_runtime"] is False
    assert _item(report, "tests/test_big.py")["production_runtime"] is False
    assert _item(report, "static/mockups/demo.html")["production_runtime"] is False
    assert _item(report, "routes/real.py")["production_runtime"] is True
    assert report["source_like_summary"]["candidate"] == 4
    assert report["production_runtime_summary"]["candidate"] == 1


def test_large_file_report_marks_allowlisted_data_without_hiding_real_code(tmp_path):
    _write_lines(tmp_path / "services" / "hwfit" / "data" / "hf_models.json", 3000)
    _write_lines(tmp_path / "static" / "app.min.js", 3000)
    _write_lines(tmp_path / "src" / "tool_implementations.py", 3000)

    report = build_report(tmp_path)
    data = _item(report, "services/hwfit/data/hf_models.json")
    minified = _item(report, "static/app.min.js")
    code = _item(report, "src/tool_implementations.py")

    assert data["allowlisted"] is True
    assert data["allowlist_category"] == "generated_data"
    assert minified["allowlisted"] is True
    assert minified["allowlist_category"] == "minified_asset"
    assert code["allowlisted"] is False
    assert report["candidate_count"] == 1
    assert report["allowlisted_count"] == 2


def test_large_file_report_skips_runtime_and_dependency_trees(tmp_path):
    _write_lines(tmp_path / "output" / "generated.py", 3000)
    _write_lines(tmp_path / "node_modules" / "pkg" / "index.js", 3000)
    _write_lines(tmp_path / "venv" / "Lib" / "site.py", 3000)
    _write_lines(tmp_path / "src" / "real.py", 3000)

    report = build_report(tmp_path)
    paths = {item["path"] for item in report["items"]}

    assert paths == {"src/real.py"}


def test_large_file_report_markdown_renders_human_summary(tmp_path):
    _write_lines(tmp_path / "src" / "candidate.py", 2001)

    markdown = render_markdown(build_report(tmp_path))

    assert "# Large File Report" in markdown
    assert "| Production/runtime | 0 | 0 | 1 |" in markdown
    assert "`src/candidate.py`" in markdown
