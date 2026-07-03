from src.nextcloud_source_provider import (
    assess_nextcloud_source_provider,
    summarize_nextcloud_source_provider,
)


def _folders() -> dict[str, str]:
    return {
        "Inbox": "Inbox",
        "Review": "Review",
        "Archive": "Archive",
        "Generated": "Generated",
        "Published": "Published",
    }


def test_nextcloud_sync_ready_with_recommended_actor_and_readonly_scope() -> None:
    report = assess_nextcloud_source_provider(
        {
            "provider_id": "nextcloud_sync",
            "actor": "odysseus-intake",
            "permission_scope": ["copy-only", "review-gated"],
            "root_path": "/Odysseus/Intake",
            "folders": _folders(),
            "enabled": True,
        }
    )

    assert report.status == "ready"
    assert report.reasons == ("offline_readonly_policy_satisfied",)
    assert report.secure_policy_decision == "allow"
    assert report.secure_policy_allowed is True
    assert report.root_path == "/Odysseus/Intake"
    assert report.webdav_endpoint is None
    payload = report.to_dict()
    assert payload["correlation_id"].startswith("sha256:")
    assert payload["runtime_event"]["surface"] == "universal_inbox"
    assert payload["runtime_event"]["component"] == "nextcloud_source_provider"
    assert payload["runtime_event"]["status"] == "success"
    assert payload["runtime_event"]["raw_content_visible"] is False
    assert "/Odysseus/Intake" not in str(payload["runtime_event"])


def test_nextcloud_webdav_ready_with_https_dav_endpoint() -> None:
    report = assess_nextcloud_source_provider(
        {
            "provider_id": "nextcloud_webdav",
            "actor": "odysseus-intake",
            "permission_scope": "no-delete",
            "webdav_endpoint": "https://cloud.example.test/remote.php/dav/files/odysseus-intake",
            "folders": _folders(),
            "enabled": True,
        }
    )

    assert report.status == "ready"
    assert report.webdav_endpoint == "https://cloud.example.test/remote.php/dav/files/odysseus-intake"
    assert report.permission_scope == ("no-delete",)


def test_non_recommended_actor_is_partial_not_blocked() -> None:
    report = assess_nextcloud_source_provider(
        {
            "provider_id": "nextcloud_sync",
            "actor": "alice-intake",
            "permission_scope": ["copy-only"],
            "root_path": "/Odysseus/Inbox",
            "folders": _folders(),
            "enabled": True,
        }
    )

    assert report.status == "partial"
    assert report.reasons == ("actor_not_recommended",)
    assert report.warnings[0].code == "non_recommended_actor"
    assert "odysseus-intake" in report.next_actions[0]


def test_forbidden_permission_rights_block_readiness() -> None:
    report = assess_nextcloud_source_provider(
        {
            "provider_id": "nextcloud_sync",
            "actor": "odysseus-intake",
            "permission_scope": ["copy-only", "delete"],
            "root_path": "/Odysseus/Inbox",
            "folders": _folders(),
            "enabled": True,
        }
    )

    assert report.status == "blocked"
    assert report.errors[0].code == "invalid_permission_scope"
    assert "forbidden rights" in report.errors[0].message


def test_invalid_provider_id_blocks_report() -> None:
    report = assess_nextcloud_source_provider(
        {
            "provider_id": "nextcloud_live",
            "actor": "odysseus-intake",
            "permission_scope": "copy-only",
            "root_path": "/Odysseus/Inbox",
            "folders": _folders(),
            "enabled": True,
        }
    )

    assert report.status == "blocked"
    assert report.provider_id == "unknown"
    assert report.errors[0].code == "invalid_provider_id"


def test_invalid_folder_name_blocks_report() -> None:
    report = assess_nextcloud_source_provider(
        {
            "provider_id": "nextcloud_sync",
            "actor": "odysseus-intake",
            "permission_scope": "review-gated",
            "root_path": "/Odysseus/Inbox",
            "folders": {
                "Inbox": "Inbox",
                "Review": "Review",
                "Archive": "Archive",
                "Generated": "Generated-AI",
                "Published": "Published",
            },
            "enabled": True,
        }
    )

    assert report.status == "blocked"
    assert report.errors[0].code == "invalid_folders"
    assert "Generated" in report.errors[0].message


def test_disabled_provider_is_deferred() -> None:
    report = assess_nextcloud_source_provider(
        {
            "provider_id": "nextcloud_webdav",
            "actor": "odysseus-intake",
            "permission_scope": "copy-only",
            "webdav_endpoint": "https://cloud.example.test/remote.php/dav/files/odysseus-intake",
            "folders": _folders(),
            "enabled": False,
        }
    )

    assert report.status == "deferred"
    assert report.reasons == ("provider_disabled_by_config",)
    assert "disabled" in report.next_actions[0].lower()


def test_sensitive_nextcloud_source_requires_secure_chat_before_ingestion() -> None:
    report = assess_nextcloud_source_provider(
        {
            "provider_id": "nextcloud_sync",
            "actor": "odysseus-intake",
            "permission_scope": ["copy-only", "review-gated"],
            "root_path": "/Odysseus/Intake",
            "folders": _folders(),
            "enabled": True,
        },
        security_mode="normal",
        source_classification="sensitive",
    )

    assert report.status == "blocked"
    assert report.secure_policy_decision == "require_secure_chat"
    assert report.secure_policy_allowed is False
    assert report.secure_policy_reason == "sensitive_source_in_normal_chat"
    assert "start_secure_chat" in report.next_actions


def test_sensitive_nextcloud_source_is_allowed_in_secure_chat() -> None:
    report = assess_nextcloud_source_provider(
        {
            "provider_id": "nextcloud_sync",
            "actor": "odysseus-intake",
            "permission_scope": ["copy-only", "review-gated"],
            "root_path": "/Odysseus/Intake",
            "folders": _folders(),
            "enabled": True,
        },
        security_mode="secure",
        source_classification="sensitive",
    )

    assert report.status == "ready"
    assert report.secure_policy_decision == "allow"
    assert report.secure_policy_allowed is True


def test_summary_is_compact_and_redacts_error_details_to_codes() -> None:
    summary = summarize_nextcloud_source_provider(
        {
            "provider_id": "nextcloud_webdav",
            "actor": "odysseus-intake",
            "permission_scope": "copy-only",
            "webdav_endpoint": "https://cloud.example.test/remote.php/webdav",
            "folders": _folders(),
            "enabled": True,
        }
    )

    assert summary["status"] == "blocked"
    assert summary["errors"] == ("invalid_webdav_endpoint",)
    assert summary["warnings"] == ()
    assert summary["webdav_endpoint"] is None
    assert summary["runtime_event"]["status"] == "blocked"
    assert "https://cloud.example.test" not in str(summary["runtime_event"])
