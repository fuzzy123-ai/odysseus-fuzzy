import json

from scripts import performance_baseline as perf


def test_phase0_report_has_stable_shape():
    report = perf.build_report(
        message_counts=(5, 10),
        context_budget=2048,
        reserve_tokens=256,
        iterations=1,
    )

    assert report["schema_version"] == 1
    assert report["phase"] == 0
    assert report["config"]["message_counts"] == [5, 10]
    assert [row["message_count"] for row in report["long_chat"]] == [5, 10]
    assert [row["message_count"] for row in report["session_materialization"]] == [5, 10]

    first = report["long_chat"][0]
    assert first["tokens_before"] > 0
    assert first["tokens_after_trim"] <= first["tokens_before"]
    assert first["estimate_tokens"]["iterations"] == 1
    assert first["trim_for_context"]["iterations"] == 1


def test_phase0_session_materialization_counts_context_messages():
    report = perf.build_report(message_counts=(3, 7), iterations=1)

    rows = report["session_materialization"]
    assert rows[0]["context_message_count"] == 3
    assert rows[1]["context_message_count"] == 7
    assert rows[1]["get_context_messages"]["avg_ms"] >= 0


def test_phase0_cli_writes_json(tmp_path):
    output = tmp_path / "baseline.json"

    exit_code = perf.main([
        "--counts",
        "4,8",
        "--iterations",
        "1",
        "--output",
        str(output),
    ])

    assert exit_code == 0
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["config"]["message_counts"] == [4, 8]
    assert len(data["long_chat"]) == 2
