import json

from scripts import refresh_tool_capability_knowledge as cli


def test_refresh_cli_can_read_diagnostics_and_assert_acceptance(tmp_path, capsys):
    data_dir = tmp_path / "knowledge"
    graph_dir = tmp_path / "graph"

    rc = cli.main([
        "--reason",
        "unit-acceptance",
        "--commit",
        "abc123",
        "--data-dir",
        str(data_dir),
        "--raptorgraph-dir",
        str(graph_dir),
        "--skip-index",
        "--write-memory",
        "--dry-run-memory-write",
        "--write-raptorgraph",
        "--read-diagnostics",
        "--assert-acceptance",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["acceptance"]["status"] == "passed"
    assert payload["diagnostics"]["status"] == "success"
    assert payload["diagnostics"]["memory_records"]["count"] == payload["memory_records"]
    assert payload["diagnostics"]["raptorgraph"]["store_event_count"] == 1
    assert payload["diagnostics"]["raw_content_visible"] is False


def test_refresh_cli_acceptance_fails_without_persisted_diagnostics(tmp_path, capsys):
    rc = cli.main([
        "--reason",
        "unit-acceptance-fail",
        "--commit",
        "abc123",
        "--data-dir",
        str(tmp_path / "knowledge"),
        "--skip-index",
        "--no-persist",
        "--read-diagnostics",
        "--assert-acceptance",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 3
    assert payload["acceptance"]["status"] == "failed"
    assert "diagnostics_not_success" in payload["acceptance"]["errors"]
