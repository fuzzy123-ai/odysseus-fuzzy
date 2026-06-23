from src.mvp_secure_data_closure import (
    SecureDataClosureGate,
    build_secure_data_closure_report,
)


def test_default_secure_data_closure_is_complete():
    report = build_secure_data_closure_report()

    assert report.roadmap_id == "secure_data_mode_runtime_hooks"
    assert report.percent_complete == 100
    assert report.why_not_100 == "-"
    assert "Private Data / Nextcloud Memory Ingestion" in report.recommended_next_human_decision

    gates = {gate.gate_id: gate for gate in report.gates}
    assert gates["data_classification_model"].status == "go"
    assert gates["chat_security_state"].status == "go"
    assert gates["central_policy_gate"].status == "go"
    assert gates["local_model_routing_guard"].status == "go"
    assert gates["sensitive_retrieval_guard"].status == "go"
    assert gates["telegram_channel_policy"].status == "go"
    assert gates["provider_runtime_hook"].status == "go"
    assert gates["retrieval_runtime_hook"].status == "go"
    assert gates["telegram_runtime_hook"].status == "go"
    assert gates["private_source_runtime_hook"].status == "go"


def test_secure_data_closure_reaches_100_when_runtime_hooks_are_integrated():
    report = build_secure_data_closure_report(
        provider_runtime_hook_go=True,
        retrieval_runtime_hook_go=True,
        telegram_runtime_hook_go=True,
        private_source_runtime_hook_go=True,
    )

    assert report.percent_complete == 100
    assert report.why_not_100 == "-"
    assert "Private Data / Nextcloud Memory Ingestion" in report.recommended_next_human_decision
    assert report.to_markdown_row() == "| 2 | Secure Data Mode Runtime Hooks | 100 | - |"


def test_secure_data_closure_fails_closed_for_missing_foundation_gate():
    report = build_secure_data_closure_report(
        data_classification_model_go=False,
        provider_runtime_hook_go=False,
        retrieval_runtime_hook_go=False,
        telegram_runtime_hook_go=False,
        private_source_runtime_hook_go=False,
    )

    assert report.percent_complete == 50
    assert "Data classification model" in report.why_not_100
    assert "Resolve Data classification model" in report.recommended_next_human_decision


def test_secure_data_closure_gate_validation_rejects_unknown_values():
    try:
        SecureDataClosureGate.create(
            gate_id="bad",
            title="Bad",
            status="maybe",
            slice_class="repo_only",
            reason="invalid status",
        )
    except ValueError as exc:
        assert "unsupported secure data closure gate status" in str(exc)
    else:
        raise AssertionError("unknown status should fail closed")

    try:
        SecureDataClosureGate.create(
            gate_id="bad",
            title="Bad",
            status="go",
            slice_class="wishful_thinking",
            reason="invalid class",
        )
    except ValueError as exc:
        assert "unsupported secure data closure slice class" in str(exc)
    else:
        raise AssertionError("unknown slice class should fail closed")
