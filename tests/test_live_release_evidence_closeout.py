from src.live_release_evidence_closeout import build_live_release_evidence_closeout


def test_default_builder_marks_all_release_evidence_gates_go():
    closeout = build_live_release_evidence_closeout()
    gate_status = {gate.gate_id: gate.status for gate in closeout.gates}

    assert gate_status == {
        "automated_release_gate": "go",
        "known_limits_review": "go",
        "provider_fallback_answer_run": "go",
        "test_vault_export_import_rebuild": "go",
    }


def test_default_decision_is_external_go():
    closeout = build_live_release_evidence_closeout()

    assert closeout.decision.decision == "external_go"
    assert closeout.decision.internal_release_candidate_ready is True
    assert closeout.decision.external_release_ready is True


def test_next_allowed_slices_are_empty_when_evidence_is_complete():
    closeout = build_live_release_evidence_closeout()

    assert closeout.next_allowed_slices == ()


def test_live_phase_entry_gates_keep_runtime_actions_disabled():
    closeout = build_live_release_evidence_closeout()
    gate_status = {gate.gate_id: gate.status for gate in closeout.live_phase_entry_gates}

    assert closeout.decision.decision == "external_go"
    assert gate_status == {
        "export_import_rebuild_disabled": "go",
        "host_telegram_network_disabled": "go",
        "operator_review_required": "go",
        "provider_calls_disabled": "go",
        "secrets_and_raw_logs_blocked": "go",
    }
    combined = " ".join(gate.summary for gate in closeout.live_phase_entry_gates)
    assert "separate operator-run flow" in combined
    assert "remain disabled" in combined


def test_to_dict_is_stable():
    closeout = build_live_release_evidence_closeout()

    assert closeout.to_dict() == {
        "gates": (
            {
                "gate_id": "automated_release_gate",
                "status": "go",
                "summary": "automated release gates are green for the internal release candidate",
            },
            {
                "gate_id": "known_limits_review",
                "status": "go",
                "summary": "known limits remain reviewed without implying deploy, tag, or distribution execution",
            },
            {
                "gate_id": "provider_fallback_answer_run",
                "status": "go",
                "summary": "provider proof is recorded with isolated redacted cloud-answer evidence",
            },
            {
                "gate_id": "test_vault_export_import_rebuild",
                "status": "go",
                "summary": "test-vault export/import/rebuild proof is recorded with isolated redacted evidence",
            },
        ),
        "live_phase_entry_gates": (
            {
                "gate_id": "export_import_rebuild_disabled",
                "status": "go",
                "summary": "export, import, and rebuild actions remain disabled until explicit operator execution",
            },
            {
                "gate_id": "host_telegram_network_disabled",
                "status": "go",
                "summary": "host, Telegram, and network actions remain disabled for the closeout slice",
            },
            {
                "gate_id": "operator_review_required",
                "status": "go",
                "summary": "operator review remains required before any live integration follow-up executes",
            },
            {
                "gate_id": "provider_calls_disabled",
                "status": "go",
                "summary": "provider calls remain disabled until a separate operator-run flow is approved",
            },
            {
                "gate_id": "secrets_and_raw_logs_blocked",
                "status": "go",
                "summary": "closeout artifacts allow only compact redacted status labels and evidence references",
            },
        ),
        "decision": {
            "decision": "external_go",
            "internal_release_candidate_ready": True,
            "external_release_ready": True,
            "next_action": "external release evidence is complete",
        },
        "next_allowed_slices": (),
    }


def test_markdown_is_operator_friendly():
    closeout = build_live_release_evidence_closeout()
    markdown = closeout.to_markdown()

    assert "# Live Release Evidence Closeout" in markdown
    assert "external_go" in markdown
    assert "Live Phase Entry Gates" in markdown
    assert "provider_calls_disabled" in markdown
    assert "LIVE1-provider-proof-run" not in markdown
