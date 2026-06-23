from src.mvp_runtime_closure import (
    RuntimeClosureGate,
    build_runtime_closure_report,
)


def test_default_runtime_closure_report_is_backend_prepared_but_live_gated():
    report = build_runtime_closure_report()

    assert report.roadmap_id == "runtime_closure_gates"
    assert report.percent_complete == 38
    assert "Updater server runtime evidence" in report.why_not_100
    assert "server-local updater evidence" in report.recommended_next_human_decision

    gates = {gate.gate_id: gate for gate in report.gates}
    assert gates["updater_backend_contract"].status == "go"
    assert gates["mcp_offline_policy"].status == "go"
    assert gates["telegram_text_offline_boundary"].status == "go"
    assert gates["mcp_local_route_smoke"].slice_class == "needs_live_go"
    assert gates["telegram_text_live_roundtrip"].slice_class == "needs_live_go"


def test_runtime_closure_reaches_100_only_when_all_live_gates_are_go():
    report = build_runtime_closure_report(
        updater_server_runtime_evidence_go=True,
        updates_backups_live_smoke_go=True,
        mcp_runtime_plugin_present_go=True,
        mcp_local_route_smoke_go=True,
        telegram_text_live_roundtrip_go=True,
    )

    assert report.percent_complete == 100
    assert report.why_not_100 == "-"
    assert "Secure Data Mode Runtime Hooks" in report.recommended_next_human_decision
    assert report.to_markdown_row() == "| 1 | Runtime Closure Gates | 100 | - |"


def test_runtime_closure_reports_blocked_offline_contracts_before_live_gates():
    report = build_runtime_closure_report(updater_backend_contract_go=False)

    assert report.percent_complete == 25
    assert "Updates and backups backend contract" in report.why_not_100
    assert "Resolve Updates and backups backend contract" in report.recommended_next_human_decision


def test_runtime_closure_gate_validation_rejects_unknown_values():
    try:
        RuntimeClosureGate.create(
            gate_id="bad",
            title="Bad",
            status="maybe",
            slice_class="repo_only",
            reason="invalid status",
        )
    except ValueError as exc:
        assert "unsupported runtime closure gate status" in str(exc)
    else:
        raise AssertionError("unknown status should fail closed")

    try:
        RuntimeClosureGate.create(
            gate_id="bad",
            title="Bad",
            status="go",
            slice_class="live_but_surely_fine",
            reason="invalid class",
        )
    except ValueError as exc:
        assert "unsupported runtime closure slice class" in str(exc)
    else:
        raise AssertionError("unknown slice class should fail closed")
