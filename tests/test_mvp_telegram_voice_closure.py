from src.mvp_telegram_voice_closure import (
    TelegramVoiceClosureGate,
    build_telegram_voice_closure_report,
)


def test_default_telegram_voice_progress_tracks_offline_pipeline_done_runtime_open():
    report = build_telegram_voice_closure_report()

    assert report.roadmap_id == "telegram_voice_pipeline"
    assert report.percent_complete == 80
    assert "Manual live voice smoke" in report.why_not_100
    assert "Grant or defer" in report.recommended_next_human_decision

    gates = {gate.gate_id: gate for gate in report.gates}
    assert gates["metadata_intake_boundary"].status == "go"
    assert gates["download_gate_plan"].status == "go"
    assert gates["fake_stt_boundary"].status == "go"
    assert gates["gated_reply_plan"].status == "go"
    assert gates["plugin_runtime_integration"].status == "go"
    assert gates["live_voice_smoke"].slice_class == "needs_live_go"
    assert gates["voice_ui_live"].slice_class == "needs_design"


def test_telegram_voice_reaches_100_when_all_gates_complete():
    report = build_telegram_voice_closure_report(
        plugin_runtime_integration_go=True,
        live_voice_smoke_go=True,
        voice_ui_live_go=True,
    )

    assert report.percent_complete == 100
    assert report.why_not_100 == "-"
    assert "ORCA / Lens" in report.recommended_next_human_decision
    assert report.to_markdown_row() == "| 5 | Telegram Voice Pipeline | 100 | - |"


def test_telegram_voice_live_gate_is_next_after_plugin_hooks():
    report = build_telegram_voice_closure_report(plugin_runtime_integration_go=True)

    assert report.percent_complete == 80
    assert "Manual live voice smoke" in report.why_not_100
    assert "Grant or defer" in report.recommended_next_human_decision


def test_telegram_voice_gate_validation_rejects_unknown_values():
    try:
        TelegramVoiceClosureGate.create(
            gate_id="bad",
            title="Bad",
            status="maybe",
            slice_class="repo_only",
            reason="invalid status",
        )
    except ValueError as exc:
        assert "unsupported telegram voice closure gate status" in str(exc)
    else:
        raise AssertionError("unknown status should fail closed")

    try:
        TelegramVoiceClosureGate.create(
            gate_id="bad",
            title="Bad",
            status="go",
            slice_class="send_voice_live",
            reason="invalid class",
        )
    except ValueError as exc:
        assert "unsupported telegram voice closure slice class" in str(exc)
    else:
        raise AssertionError("unknown slice class should fail closed")
