import json

import pytest

from scripts.compare_unified_source_index import main


def test_cli_complete_check_writes_content_free_report(tmp_path, capsys):
    output = tmp_path / "comparison.json"

    exit_code = main(["--fixture", "complete", "--check", "--output", str(output)])
    captured = capsys.readouterr()
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert "ready=true" in captured.out
    assert "live_cutover_authorized=false" in captured.out
    assert payload["all_gates_ready"] is True
    assert payload["private_corpus_accessed"] is False
    assert payload["shadow_requests_sent"] is False
    assert payload["dual_write_performed"] is False
    assert payload["live_cutover_authorized"] is False
    assert len(payload["lanes"]) == 4


@pytest.mark.parametrize(
    "fixture,expected_failure",
    [
        ("missing", "coverage_ratio"),
        ("locator_mismatch", "locator_parity"),
        ("policy_mismatch", "policy_parity"),
    ],
)
def test_cli_check_returns_nonzero_for_negative_fixture(
    fixture,
    expected_failure,
    tmp_path,
    capsys,
):
    output = tmp_path / f"{fixture}.json"

    exit_code = main(["--fixture", fixture, "--check", "--output", str(output)])
    payload = json.loads(output.read_text(encoding="utf-8"))
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "ready=false" in captured.out
    assert any(
        expected_failure in lane["gate_failures"]
        for lane in payload["lanes"]
    )
    assert payload["active_path_modified"] is False


def test_cli_without_check_reports_negative_fixture_but_exits_successfully(capsys):
    assert main(["--fixture", "missing"]) == 0
    assert "ready=false" in capsys.readouterr().out


def test_cli_rejects_unknown_fixture_before_any_comparison(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["--fixture", "live"])

    assert caught.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
