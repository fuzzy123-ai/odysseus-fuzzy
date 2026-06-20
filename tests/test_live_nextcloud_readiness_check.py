from src.live_nextcloud_readiness_check import build_live_nextcloud_readiness_check


def _folders() -> dict[str, str]:
    return {
        "Inbox": "Inbox",
        "Review": "Review",
        "Archive": "Archive",
        "Generated": "Generated",
        "Published": "Published",
    }


def _ready_webdav_config() -> dict[str, object]:
    return {
        "provider_id": "nextcloud_webdav",
        "actor": "odysseus-intake",
        "permission_scope": "no-delete",
        "webdav_endpoint": "https://cloud.example.test/remote.php/dav/files/odysseus-intake",
        "folders": _folders(),
        "enabled": True,
    }


def test_ready_config_maps_to_operator_review_not_live_enablement():
    check = build_live_nextcloud_readiness_check(_ready_webdav_config())

    assert check.status == "ready_for_operator_review"
    assert check.source_status == "ready"
    assert check.external_release_ready is False
    assert check.blocked_live_actions == (
        "nextcloud_api_call",
        "webdav_request",
        "credential_or_token_capture",
        "delete_move_or_overwrite",
        "nextcloud_tag_write",
        "inbox_worker_start",
        "graph_or_memory_write",
        "automatic_provider_enablement",
    )


def test_partial_config_requires_operator_review():
    config = _ready_webdav_config()
    config["actor"] = "alice-intake"

    check = build_live_nextcloud_readiness_check(config)

    assert check.status == "needs_operator_review"
    assert check.source_status == "partial"
    assert check.warnings == ("non_recommended_actor",)


def test_blocked_config_has_no_next_actions():
    config = _ready_webdav_config()
    config["permission_scope"] = ["copy-only", "delete"]

    check = build_live_nextcloud_readiness_check(config)

    assert check.status == "blocked"
    assert check.next_actions == ()
    assert check.errors == ("invalid_permission_scope",)


def test_redacted_summary_excludes_paths_endpoints_and_actor_values():
    check = build_live_nextcloud_readiness_check(_ready_webdav_config())
    payload = str(check.to_dict())
    markdown = check.to_markdown()

    assert "cloud.example.test" not in payload
    assert "/remote.php/dav" not in payload
    assert "odysseus-intake" not in payload
    assert "cloud.example.test" not in markdown
    assert "/remote.php/dav" not in markdown
    assert "odysseus-intake" not in markdown


def test_to_dict_is_stable():
    check = build_live_nextcloud_readiness_check(_ready_webdav_config())

    assert check.to_dict() == {
        "status": "ready_for_operator_review",
        "provider_id": "nextcloud_webdav",
        "source_status": "ready",
        "external_release_ready": False,
        "reasons": ("offline_readonly_policy_satisfied",),
        "errors": (),
        "warnings": (),
        "next_actions": (
            "Proceed with offline review-gated planning only; do not execute network sync.",
        ),
        "blocked_live_actions": (
            "nextcloud_api_call",
            "webdav_request",
            "credential_or_token_capture",
            "delete_move_or_overwrite",
            "nextcloud_tag_write",
            "inbox_worker_start",
            "graph_or_memory_write",
            "automatic_provider_enablement",
        ),
    }


def test_markdown_is_operator_friendly():
    check = build_live_nextcloud_readiness_check(_ready_webdav_config())
    markdown = check.to_markdown()

    assert "# Live Nextcloud Readiness Check" in markdown
    assert "ready_for_operator_review" in markdown
    assert "Blocked Live Actions" in markdown
    assert "nextcloud_api_call" in markdown
