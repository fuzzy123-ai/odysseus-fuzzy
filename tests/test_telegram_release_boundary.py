from src.telegram_release_boundary import build_telegram_release_boundary


def test_default_builder_is_conservative_and_needs_secret_rotation():
    gate = build_telegram_release_boundary()

    assert gate.gate_id == "telegram_release_boundary"
    assert gate.decision == "needs_secret_rotation"
    assert gate.status == "needs_secret_rotation"


def test_boundary_ready_requires_all_positive_secret_and_dry_run_gates():
    gate = build_telegram_release_boundary(
        token_rotated_out_of_band=True,
        token_not_persisted=True,
        env_only_secret_loading=True,
        dry_run_plan_recorded=True,
        no_network_default=True,
        no_send_default=True,
        operator_live_smoke_required=True,
        rollback_instruction_recorded=True,
    )

    assert gate.decision == "telegram_boundary_ready"
    assert gate.status == "go"


def test_blocked_when_secret_or_runtime_boundary_fails():
    gate = build_telegram_release_boundary(
        token_rotated_out_of_band=True,
        token_not_persisted=True,
        env_only_secret_loading=True,
        dry_run_plan_recorded=True,
        raw_token_logged=True,
    )

    assert gate.decision == "blocked"
    assert gate.status == "blocked"


def test_to_dict_is_compact_and_stable():
    gate = build_telegram_release_boundary(
        token_rotated_out_of_band=True,
        token_not_persisted=False,
        env_only_secret_loading=True,
        dry_run_plan_recorded=True,
        no_network_default=True,
        no_send_default=False,
        operator_live_smoke_required=True,
        rollback_instruction_recorded=False,
    )

    assert gate.to_dict() == {
        "gate_id": "telegram_release_boundary",
        "decision": "needs_secret_rotation",
        "status": "needs_secret_rotation",
        "summary": "telegram release boundary still needs rotation, dry-run, or rollback evidence before review",
        "next_allowed_actions": (
            "verify token rotation outside the repository through operator-controlled secret handling",
            "confirm environment-only loading, dry-run defaults, and rollback instructions offline",
            "keep network and send paths disabled until a manual live-smoke review is approved",
        ),
    }


def test_markdown_is_operator_friendly_and_secret_safe():
    gate = build_telegram_release_boundary()

    markdown = gate.to_markdown()
    assert "# Telegram Release Boundary" in markdown
    assert "needs_secret_rotation" in markdown
    assert "Next Allowed Actions" in markdown
