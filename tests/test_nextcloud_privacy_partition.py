import json

from src.nextcloud_privacy_partition import (
    ARCHIVE_CANDIDATE,
    LOCAL_SENSITIVE,
    UNKNOWN_PRIVATE,
    classify_nextcloud_relative_path,
    summarize_nextcloud_privacy_partition,
)


def test_sensitive_roots_route_to_local_only_without_serializing_marker():
    decision = classify_nextcloud_relative_path(
        "sensitive-root/document.pdf",
        sensitive_roots=("sensitive-root",),
    )

    encoded = json.dumps(decision.to_metadata(), sort_keys=True)
    assert decision.privacy_class == LOCAL_SENSITIVE
    assert decision.archive_allowed is False
    assert decision.mirror_to_new_nextcloud is False
    assert decision.memory_write_candidate is True
    assert decision.local_model_only is True
    assert decision.inspection_allowed is False
    assert decision.required_model_scope == "local_only"
    assert "sensitive-root" not in encoded


def test_non_sensitive_roots_are_archive_candidates_but_not_content_readable():
    decision = classify_nextcloud_relative_path(
        "projects/workbook.xlsx",
        sensitive_roots=("sensitive-root",),
    )

    assert decision.privacy_class == ARCHIVE_CANDIDATE
    assert decision.archive_allowed is True
    assert decision.mirror_to_new_nextcloud is True
    assert decision.memory_write_candidate is True
    assert decision.local_model_only is False
    assert decision.inspection_allowed is False
    assert decision.required_model_scope == "policy_selected"


def test_unknown_private_default_blocks_archive_until_review():
    decision = classify_nextcloud_relative_path(
        "unclassified/file.txt",
        sensitive_roots=(),
        default_unknown_private=True,
    )

    assert decision.privacy_class == UNKNOWN_PRIVATE
    assert decision.archive_allowed is False
    assert decision.mirror_to_new_nextcloud is False
    assert decision.memory_write_candidate is False
    assert decision.local_model_only is True


def test_partition_summary_is_aggregate_only():
    summary = summarize_nextcloud_privacy_partition(
        ("sensitive-root/a.pdf", "projects/b.pdf", "projects/c.pdf"),
        sensitive_roots=("sensitive-root",),
    )
    encoded = json.dumps(summary.to_dict(), sort_keys=True)

    assert summary.total == 3
    assert summary.local_sensitive == 1
    assert summary.archive_candidates == 2
    assert summary.sensitive_root_marker_count == 1
    assert "sensitive-root" not in encoded
