from src.test_vault_export_import_rebuild import build_test_vault_export_import_rebuild


def test_default_builder_is_conservative_and_needs_test_vault_evidence():
    result = build_test_vault_export_import_rebuild()

    assert result.gate_id == "test_vault_export_import_rebuild"
    assert result.decision == "needs_test_vault_evidence"
    assert result.status == "needs_test_vault_evidence"


def test_ready_requires_all_positive_rebuild_evidence_gates():
    result = build_test_vault_export_import_rebuild(
        test_vault_scope_recorded=True,
        export_artifact_recorded=True,
        import_target_recorded=True,
        rebuild_result_recorded=True,
        source_write_disabled=True,
        data_loss_check_recorded=True,
        rollback_plan_recorded=True,
        operator_confirmation_recorded=True,
    )

    assert result.decision == "test_vault_rebuild_ready"
    assert result.status == "go"


def test_blocked_when_scope_write_or_data_loss_boundary_fails():
    result = build_test_vault_export_import_rebuild(
        test_vault_scope_recorded=True,
        export_artifact_recorded=True,
        data_loss_detected=True,
    )

    assert result.decision == "blocked"
    assert result.status == "blocked"
    assert "data loss" in result.summary.lower()


def test_to_dict_is_compact_and_stable():
    result = build_test_vault_export_import_rebuild(
        test_vault_scope_recorded=True,
        export_artifact_recorded=True,
    )

    assert result.to_dict() == {
        "gate_id": "test_vault_export_import_rebuild",
        "decision": "needs_test_vault_evidence",
        "status": "needs_test_vault_evidence",
        "summary": (
            "Test-vault export/import/rebuild still needs compact scope, artifact, import target, "
            "rebuild result, source-write-off, data-loss, rollback, and operator confirmation evidence."
        ),
        "next_allowed_actions": [
            "Record only compact test-vault scope, export, import-target, and rebuild evidence.",
            "Verify source-write remains disabled and rollback steps stay documented before any manual go.",
            "Keep export/import/rebuild execution out of scope until manual release evidence is approved.",
        ],
    }


def test_deferred_does_not_claim_go_or_external_release_evidence():
    result = build_test_vault_export_import_rebuild(
        test_vault_scope_recorded=True,
        export_artifact_recorded=None,
        import_target_recorded=True,
    )

    payload = result.to_dict()
    evidence_text = [
        payload["decision"],
        payload["status"],
        payload["summary"],
        *payload["next_allowed_actions"],
    ]
    combined_text = " ".join(evidence_text).lower()

    assert result.decision == "deferred"
    assert result.status == "deferred"
    assert "manual release evidence is approved" in combined_text
    assert "test_vault_rebuild_ready" not in combined_text
    assert "external 1.0" not in combined_text


def test_partial_evidence_does_not_claim_real_export_import_or_rebuild_execution():
    result = build_test_vault_export_import_rebuild(
        test_vault_scope_recorded=True,
        export_artifact_recorded=True,
        import_target_recorded=True,
    )

    payload = result.to_dict()
    evidence_text = [
        payload["decision"],
        payload["status"],
        payload["summary"],
        *payload["next_allowed_actions"],
    ]
    combined_text = " ".join(evidence_text).lower()

    assert result.decision == "needs_test_vault_evidence"
    assert result.status == "needs_test_vault_evidence"
    assert "execution out of scope" in combined_text
    assert "rebuild_ready" not in combined_text
    assert "executed" not in combined_text


def test_markdown_is_operator_friendly_and_vault_content_safe():
    result = build_test_vault_export_import_rebuild(
        test_vault_scope_recorded=True,
        export_artifact_recorded=True,
        import_target_recorded=True,
        rebuild_result_recorded=True,
        source_write_disabled=True,
        data_loss_check_recorded=True,
        rollback_plan_recorded=True,
        operator_confirmation_recorded=True,
    )

    markdown = result.to_markdown()

    assert "# Test Vault Export Import Rebuild" in markdown
    assert "test_vault_rebuild_ready" in markdown
    assert "vault contents" not in markdown.lower()
    assert "source writes disabled" in markdown.lower()
