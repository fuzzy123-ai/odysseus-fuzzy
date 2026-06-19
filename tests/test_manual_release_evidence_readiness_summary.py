from src.manual_release_evidence_readiness_summary import build_current_manual_release_evidence_readiness_summary


def test_current_summary_is_not_ok_while_manual_gates_are_open():
    summary = build_current_manual_release_evidence_readiness_summary(
        label="Morning Evidence",
        generated_at="2026-06-17T11:00:00Z",
    )

    assert summary.ok is False
    assert summary.status_label == "NO_GO"
    assert summary.open_gate_count == 1
    assert "Provider Proof" in summary.no_go_hint
    assert "Export/Import/Rebuild" not in summary.no_go_hint


def test_summary_contains_deterministic_markdown_and_json_filenames():
    summary = build_current_manual_release_evidence_readiness_summary(
        label="Morning Evidence",
        generated_at="2026-06-17T11:00:00Z",
    )

    assert summary.suggested_markdown_filename.endswith(".md")
    assert summary.suggested_json_filename.endswith(".json")
    assert "morning-evidence-2026-06-17t11-00-00z" in summary.suggested_markdown_filename
    assert summary.suggested_markdown_filename.replace(".md", "") == summary.suggested_json_filename.replace(".json", "")


def test_summary_dict_is_stable():
    summary = build_current_manual_release_evidence_readiness_summary(
        label="Morning Evidence",
        generated_at="2026-06-17T11:00:00Z",
    )

    assert summary.to_dict() == {
        "label": "Morning Evidence",
        "generated_at": "2026-06-17T11:00:00Z",
        "ok": False,
        "status_label": "NO_GO",
        "sha256": summary.sha256,
        "suggested_markdown_filename": summary.suggested_markdown_filename,
        "suggested_json_filename": summary.suggested_json_filename,
        "open_gate_count": 1,
        "no_go_hint": "Manual release evidence still has an open gate and remains no-go until Provider Proof is closed.",
    }
