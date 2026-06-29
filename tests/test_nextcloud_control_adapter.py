from src.nextcloud_control_adapter import plan_nextcloud_control_action


def _green_backup_gate() -> dict:
    return {
        "risk_level": "high",
        "evaluated_at": "2026-06-29T08:45:49Z",
        "evidence": (
            {
                "evidence_id": "pre_update_snapshot",
                "state": "green",
                "result_label": "pass",
                "checked_at": "2026-06-29T08:43:56Z",
                "summary": "Pre-update snapshot evidence is green.",
            },
            {
                "evidence_id": "repository_check",
                "state": "green",
                "result_label": "pass",
                "checked_at": "2026-06-29T08:44:01Z",
                "summary": "Repository check evidence is green.",
            },
            {
                "evidence_id": "restore_smoke",
                "state": "green",
                "result_label": "pass",
                "checked_at": "2026-06-29T08:45:47Z",
                "summary": "Restore smoke evidence is green.",
            },
        ),
    }


def _folders() -> dict[str, str]:
    return {
        "Inbox": "Inbox",
        "Review": "Review",
        "Archive": "Archive",
        "Generated": "Generated",
        "Published": "Published",
    }


def _config(**overrides):
    config = {
        "source_provider": {
            "provider_id": "nextcloud_sync",
            "actor": "odysseus-intake",
            "permission_scope": ("no-delete", "copy-only", "review-gated"),
            "root_path": "/app/nextcloud-data/odysseus-intake/files",
            "folders": _folders(),
            "enabled": True,
        },
        "runtime_backend": "podman_pod",
        "action": "list",
        "operator_live_go": False,
        "review_approved": False,
        "backup_gate": _green_backup_gate(),
    }
    config.update(overrides)
    return config


def test_read_only_control_action_waits_for_operator_go():
    plan = plan_nextcloud_control_action(_config())

    assert plan.status == "needs_operator_input"
    assert plan.action == "list"
    assert plan.provider_id == "nextcloud_sync"
    assert plan.runtime_backend == "podman_pod"
    assert plan.actor == "odysseus-intake"
    assert plan.read_only is True
    assert plan.review_gated is False
    assert plan.live_execution_allowed is False
    assert plan.backup_gate_status == "ready"
    assert plan.backup_gate_decision == "go"
    assert plan.errors == ()
    assert "podman_runtime_confirmed" in plan.reasons
    assert "read_only_action" in plan.reasons
    assert "backup_gate_ready" in plan.reasons
    assert "docker_runtime" in plan.blocked_operations


def test_read_only_control_action_reaches_live_go_with_operator_go():
    plan = plan_nextcloud_control_action(_config(operator_live_go=True))

    assert plan.status == "ready_for_live_go"
    assert plan.live_execution_allowed is True
    assert plan.next_actions[-1].startswith("Proceed with the smallest read-only")


def test_live_nextcloud_control_requires_green_backup_gate():
    plan = plan_nextcloud_control_action(
        _config(operator_live_go=True, backup_gate=None)
    )

    assert plan.status == "blocked"
    assert plan.live_execution_allowed is False
    assert plan.backup_gate_status == "blocked"
    assert plan.backup_gate_decision == "no_go"
    assert "backup_gate_not_green" in plan.errors
    assert any("pre-update snapshot" in action for action in plan.next_actions)


def test_review_gated_write_requires_review_and_operator_go():
    plan = plan_nextcloud_control_action(
        _config(action="copy", operator_live_go=True, review_approved=False)
    )

    assert plan.status == "needs_operator_input"
    assert plan.review_gated is True
    assert plan.live_execution_allowed is False
    assert "review_approval_missing" in plan.reasons

    approved = plan_nextcloud_control_action(
        _config(action="copy", operator_live_go=True, review_approved=True)
    )

    assert approved.status == "ready_for_live_go"
    assert approved.live_execution_allowed is True


def test_destructive_or_admin_actions_are_blocked():
    for action in ("delete", "move", "overwrite", "occ_admin"):
        plan = plan_nextcloud_control_action(
            _config(action=action, operator_live_go=True, review_approved=True)
        )

        assert plan.status == "blocked"
        assert plan.live_execution_allowed is False
        assert "action_forbidden" in plan.errors


def test_docker_runtime_is_blocked():
    plan = plan_nextcloud_control_action(
        _config(runtime_backend="docker", operator_live_go=True)
    )

    assert plan.status == "blocked"
    assert plan.runtime_backend is None
    assert "runtime_backend_unsupported" in plan.errors
    assert "Podman/pod runtime" in plan.next_actions[0]
