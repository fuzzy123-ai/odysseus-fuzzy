import json

from src.live_nextcloud_readiness_check import build_live_nextcloud_readiness_check
from src.nextcloud_import_report import NextcloudImportDryRunReport
from src.nextcloud_transfer_readiness import build_nextcloud_transfer_readiness_plan
from src.nextcloud_universal_inbox_flow_adapter import build_nextcloud_universal_inbox_flow_state


def _folders() -> dict[str, str]:
    return {
        "Inbox": "Inbox",
        "Review": "Review",
        "Archive": "Archive",
        "Generated": "Generated",
        "Published": "Published",
    }


def _provider_config(**overrides):
    config = {
        "provider_id": "nextcloud_webdav",
        "actor": "odysseus-intake",
        "permission_scope": "no-delete",
        "webdav_endpoint": "https://cloud.example.test/remote.php/dav/files/odysseus-intake",
        "folders": _folders(),
        "enabled": True,
    }
    config.update(overrides)
    return config


def _transfer_config(**overrides):
    config = {
        "source_provider": _provider_config(),
        "source_label": "nextcloud-private-corpus",
        "source_path_confirmed": True,
        "target_path": "/srv/odysseus/private-nextcloud-mirror",
        "expected_bytes": 120 * 1024**3,
        "available_bytes": 200 * 1024**3,
        "reserve_bytes": 30 * 1024**3,
        "transfer_tool": "rclone_webdav",
        "runtime_backend": "podman_pod",
        "dry_run_command": "rclone copy remote:Private /srv/odysseus/private-nextcloud-mirror --dry-run --checksum",
        "dry_run_reviewed": True,
        "operator_live_go": False,
    }
    config.update(overrides)
    return config


def _report(**overrides):
    report = NextcloudImportDryRunReport(
        source_id="nextcloud-main",
        inventory_total=7,
        by_file_category={"document_extractable": 2, "media_metadata": 1},
        by_privacy_class={"archive_candidate": 6, "local_sensitive": 1},
        long_path_count=1,
        document_candidates=2,
        document_candidate_profile={"private_review_candidates": 1},
        metadata_only_candidates=2,
        review_candidates=1,
        software_archive_candidates=1,
        software_archive_paths=("Software Archives/private-tool.zip",),
        sample_review_paths=("Private/secret.pdf",),
    )
    payload = report.to_dict()
    payload.update(overrides)
    return payload


def test_nextcloud_adapter_builds_redacted_review_flow_from_dry_run_payloads():
    transfer = build_nextcloud_transfer_readiness_plan(_transfer_config())
    live = build_live_nextcloud_readiness_check(_provider_config())

    payload = build_nextcloud_universal_inbox_flow_state(
        import_report=_report(),
        transfer_readiness=transfer,
        live_readiness=live,
    ).to_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["source_kind"] == "nextcloud"
    assert payload["source_ref_visible"] is False
    assert payload["source_path_visible"] is False
    assert payload["raw_content_visible"] is False
    assert payload["live_write_allowed"] is False
    assert payload["overall_status"] == "review"
    assert payload["next_action"] == "operator_review"
    assert "operator_review_required" in payload["review_reasons"]
    assert payload["review_reason_details"][0]["category"] == "operator_gate"
    assert payload["steps"][0]["metadata"]["inventory_total"] == 7
    assert payload["steps"][6]["metadata"]["document_candidates"] == 2
    assert payload["steps"][6]["metadata"]["writes_performed"] is False
    assert "cloud.example.test" not in encoded
    assert "/remote.php/dav" not in encoded
    assert "/srv/odysseus/private-nextcloud-mirror" not in encoded
    assert "remote:Private" not in encoded
    assert "Private/secret.pdf" not in encoded
    assert "Software Archives/private-tool.zip" not in encoded


def test_nextcloud_adapter_blocks_flow_when_readiness_blocks():
    transfer = build_nextcloud_transfer_readiness_plan(
        _transfer_config(
            dry_run_command="rclone sync remote:Private /srv/odysseus/private-nextcloud-mirror --dry-run --delete",
            dry_run_reviewed=True,
        )
    )

    payload = build_nextcloud_universal_inbox_flow_state(
        import_report=_report(review_candidates=0),
        transfer_readiness=transfer,
    ).to_dict()

    assert payload["overall_status"] == "blocked"
    assert payload["next_action"] == "fix_blocker"
    assert "dry_run_contains_destructive_token" in payload["review_reasons"]
    assert payload["steps"][6]["status"] == "blocked"
    assert payload["runtime_event"]["status"] == "blocked"


def test_nextcloud_adapter_keeps_live_write_disabled_unless_explicitly_allowed():
    transfer = build_nextcloud_transfer_readiness_plan(_transfer_config(operator_live_go=True))
    live = build_live_nextcloud_readiness_check(_provider_config())
    pipeline_run = {
        "stages": {
            "extraction": {"status": "completed"},
            "memory_abstraction": {"status": "completed"},
            "routing": {"status": "completed"},
        },
        "routing_decision": {"status": "go", "copy_only": True, "delete_original": False},
        "memory_abstraction_event": {"event": "universal_inbox_memory_abstraction", "status": "completed"},
        "policy_gate": {"status": "go"},
    }
    memory_intent = {
        "status": "ready",
        "reason": "policy_allows_abstract_memory_write",
        "dry_run": True,
        "ready_to_write": True,
        "writes_performed": False,
        "memory_records": ({"memory_id": "uix-1"},),
        "raptorgraph_event": {"event": "universal_inbox_memory_write_intent", "status": "ready"},
    }

    default_payload = build_nextcloud_universal_inbox_flow_state(
        import_report=_report(review_candidates=0),
        transfer_readiness=transfer,
        live_readiness=live,
        pipeline_run=pipeline_run,
        memory_intent=memory_intent,
    ).to_dict()
    allowed_payload = build_nextcloud_universal_inbox_flow_state(
        import_report=_report(review_candidates=0),
        transfer_readiness=transfer,
        live_readiness=live,
        pipeline_run=pipeline_run,
        memory_intent=memory_intent,
        allow_live_write=True,
    ).to_dict()

    assert default_payload["live_write_allowed"] is False
    assert default_payload["next_action"] == "hold_for_live_go"
    assert allowed_payload["live_write_allowed"] is True
    assert allowed_payload["next_action"] == "ready_for_bounded_live_action"
