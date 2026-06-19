from src.live_release_evidence_closeout import build_live_release_evidence_closeout


def test_default_builder_marks_only_manual_provider_gate_open():
    closeout = build_live_release_evidence_closeout()
    gate_status = {gate.gate_id: gate.status for gate in closeout.gates}

    assert gate_status == {
        "automated_release_gate": "go",
        "known_limits_review": "go",
        "provider_fallback_answer_run": "needs_manual_evidence",
        "test_vault_export_import_rebuild": "go",
    }


def test_default_decision_requires_manual_evidence_and_is_not_external_go():
    closeout = build_live_release_evidence_closeout()

    assert closeout.decision.decision == "needs_manual_evidence"
    assert closeout.decision.internal_release_candidate_ready is True
    assert closeout.decision.external_release_ready is False


def test_next_allowed_slices_point_to_live1_when_provider_gate_is_open():
    closeout = build_live_release_evidence_closeout()

    assert closeout.next_allowed_slices == (
        "LIVE1-provider-proof-run",
    )


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
                "summary": "known limits remain reviewed and explicitly keep external 1.0 as no-go",
            },
            {
                "gate_id": "provider_fallback_answer_run",
                "status": "needs_manual_evidence",
                "summary": "provider proof still requires a manual fallback-answer verification run",
            },
            {
                "gate_id": "test_vault_export_import_rebuild",
                "status": "go",
                "summary": "test-vault export/import/rebuild proof is recorded with isolated redacted evidence",
            },
        ),
        "decision": {
            "decision": "needs_manual_evidence",
            "internal_release_candidate_ready": True,
            "external_release_ready": False,
            "next_action": "complete the remaining manual provider-proof evidence run",
        },
        "next_allowed_slices": (
            "LIVE1-provider-proof-run",
        ),
    }


def test_markdown_is_operator_friendly():
    closeout = build_live_release_evidence_closeout()
    markdown = closeout.to_markdown()

    assert "# Live Release Evidence Closeout" in markdown
    assert "needs_manual_evidence" in markdown
    assert "LIVE1-provider-proof-run" in markdown
