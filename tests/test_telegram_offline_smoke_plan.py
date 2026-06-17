from src.telegram_offline_smoke_plan import build_telegram_offline_smoke_plan


def test_default_builder_is_conservative_and_needs_offline_smoke_evidence():
    plan = build_telegram_offline_smoke_plan()

    assert plan.gate_id == "telegram_offline_smoke_plan"
    assert plan.decision == "needs_offline_smoke_evidence"
    assert plan.status == "needs_offline_smoke_evidence"


def test_offline_smoke_ready_requires_all_positive_redacted_gates():
    plan = build_telegram_offline_smoke_plan(
        redacted_secret_reference_recorded=True,
        env_var_name_recorded=True,
        dry_run_payload_recorded=True,
        send_disabled_recorded=True,
        network_disabled_recorded=True,
        operator_confirmation_required=True,
        rollback_command_documented=True,
        live_smoke_deferred_until_manual_go=True,
    )

    assert plan.decision == "telegram_offline_smoke_ready"
    assert plan.status == "go"


def test_blocked_when_secret_chat_id_or_runtime_boundary_fails():
    plan = build_telegram_offline_smoke_plan(
        redacted_secret_reference_recorded=True,
        env_var_name_recorded=True,
        raw_chat_id_persisted=True,
    )

    assert plan.decision == "blocked"
    assert plan.status == "blocked"
    assert "chat-id" in plan.summary.lower()


def test_to_dict_is_compact_and_stable():
    plan = build_telegram_offline_smoke_plan(
        redacted_secret_reference_recorded=True,
        env_var_name_recorded=True,
    )

    assert plan.to_dict() == {
        "gate_id": "telegram_offline_smoke_plan",
        "decision": "needs_offline_smoke_evidence",
        "status": "needs_offline_smoke_evidence",
        "summary": (
            "Offline smoke plan still needs redacted evidence for environment-only secret "
            "loading, dry-run payload, disabled network/send paths, rollback, and manual go."
        ),
        "next_allowed_actions": [
            "Confirm redacted environment variable naming only.",
            "Review offline dry-run payload without any secret or chat-id values.",
            "Keep network and send paths disabled until manual operator go.",
        ],
    }


def test_markdown_is_operator_friendly_and_secret_safe():
    plan = build_telegram_offline_smoke_plan(
        redacted_secret_reference_recorded=True,
        env_var_name_recorded=True,
        dry_run_payload_recorded=True,
        send_disabled_recorded=True,
        network_disabled_recorded=True,
        operator_confirmation_required=True,
        rollback_command_documented=True,
        live_smoke_deferred_until_manual_go=True,
    )

    markdown = plan.to_markdown()

    assert "# Telegram Offline Smoke Plan" in markdown
    assert "telegram_offline_smoke_ready" in markdown
    assert "secret" not in markdown.lower() or "redacted" in markdown.lower()
    assert "chat-id values" in markdown
