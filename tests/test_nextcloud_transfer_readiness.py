from src.nextcloud_transfer_readiness import build_nextcloud_transfer_readiness_plan


def _folders() -> dict[str, str]:
    return {
        "Inbox": "Inbox",
        "Review": "Review",
        "Archive": "Archive",
        "Generated": "Generated",
        "Published": "Published",
    }


def _ready_config(**overrides):
    config = {
        "source_provider": {
            "provider_id": "nextcloud_webdav",
            "actor": "odysseus-intake",
            "permission_scope": "no-delete",
            "webdav_endpoint": "https://cloud.example.test/remote.php/dav/files/odysseus-intake",
            "folders": _folders(),
            "enabled": True,
        },
        "source_label": "nextcloud-private-corpus",
        "source_path_confirmed": True,
        "target_path": "/srv/odysseus/private-nextcloud-mirror",
        "expected_bytes": 120 * 1024**3,
        "available_bytes": 200 * 1024**3,
        "reserve_bytes": 30 * 1024**3,
        "transfer_tool": "rclone_webdav",
        "dry_run_command": "rclone copy remote:Private /srv/odysseus/private-nextcloud-mirror --dry-run --checksum",
        "dry_run_reviewed": True,
        "operator_live_go": False,
    }
    config.update(overrides)
    return config


def test_transfer_readiness_is_operator_input_until_live_go():
    plan = build_nextcloud_transfer_readiness_plan(_ready_config())

    assert plan.status == "needs_operator_input"
    assert plan.provider_id == "nextcloud_webdav"
    assert plan.transfer_tool == "rclone_webdav"
    assert plan.source_confirmed is True
    assert plan.target_confirmed is True
    assert plan.disk_budget_verified is True
    assert plan.dry_run_no_delete is True
    assert plan.operator_live_go is False
    assert plan.errors == ()
    assert "operator_live_go_missing" in plan.reasons
    assert "Ask the operator" in plan.next_actions[-1]
    assert "network_sync" in plan.blocked_live_actions


def test_transfer_readiness_reaches_live_go_only_with_explicit_operator_go():
    plan = build_nextcloud_transfer_readiness_plan(_ready_config(operator_live_go=True))

    assert plan.status == "ready_for_live_go"
    assert plan.operator_live_go is True
    assert plan.next_actions[-1].startswith("Proceed only with the smallest approved live batch")


def test_transfer_readiness_blocks_destructive_or_unreviewed_dry_run():
    plan = build_nextcloud_transfer_readiness_plan(
        _ready_config(
            dry_run_command="rclone sync remote:Private /srv/odysseus/private-nextcloud-mirror --dry-run --delete",
            dry_run_reviewed=True,
        )
    )

    assert plan.status == "blocked"
    assert plan.dry_run_no_delete is False
    assert "dry_run_contains_destructive_token" in plan.errors


def test_transfer_readiness_blocks_insufficient_disk_budget():
    plan = build_nextcloud_transfer_readiness_plan(
        _ready_config(
            expected_bytes=120 * 1024**3,
            available_bytes=130 * 1024**3,
            reserve_bytes=30 * 1024**3,
        )
    )

    assert plan.status == "blocked"
    assert plan.disk_budget_verified is False
    assert "disk_budget_insufficient" in plan.errors


def test_transfer_readiness_report_stays_redacted():
    plan = build_nextcloud_transfer_readiness_plan(_ready_config())
    payload = str(plan.to_dict())

    assert "cloud.example.test" not in payload
    assert "/remote.php/dav" not in payload
    assert "/srv/odysseus/private-nextcloud-mirror" not in payload
    assert "remote:Private" not in payload
