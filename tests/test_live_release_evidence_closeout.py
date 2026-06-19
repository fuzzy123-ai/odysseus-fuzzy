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
    assert "LIVE1-provider-proof-run" not in markdown
