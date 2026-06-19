import json

import pytest

from src.nextcloud_intake_ledger import (
    NextcloudIntakeLedgerEntry,
    NextcloudIntakeLedgerError,
    compute_content_hash,
    dumps_entry,
    summarize_entries,
)


def test_entry_roundtrip_stays_metadata_only_and_reconstructable():
    digest = compute_content_hash(b"offline test bytes")
    entry = NextcloudIntakeLedgerEntry(
        digest=digest,
        path="inbox/contracts/nda.pdf",
        size=4096,
        mtime="2026-06-19T08:15:00Z",
        status="needs_review",
        actor="bob.worker",
        permission_scope="metadata_only:review",
        errors=("api_key=<redacted-test-sentinel>",),
        metadata={
            "etag": "abc123",
            "note": "safe summary",
            "content": "private body should never persist",
            "api_key": "<redacted-test-sentinel>",
            "tags": ["legal", "priority"],
        },
    )

    payload = entry.to_dict()
    rebuilt = NextcloudIntakeLedgerEntry.from_dict(payload)
    encoded = json.dumps(payload, sort_keys=True)

    assert rebuilt == entry
    assert payload["provider"] == "nextcloud_inbox"
    assert payload["metadata"]["etag"] == "abc123"
    assert payload["metadata"]["content"] == "[redacted]"
    assert payload["metadata"]["api_key"] == "[redacted]"
    assert payload["errors"] == ["[redacted]"]
    assert "offline test bytes" not in encoded
    assert "private body should never persist" not in encoded
    assert "redacted-test-sentinel" not in encoded


def test_compute_content_hash_accepts_text_and_bytes_without_filesystem_reads():
    assert compute_content_hash("hello") == compute_content_hash(b"hello")
    assert compute_content_hash("hello") == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("path", "../secrets.txt"),
        ("provider", "dropbox"),
        ("actor", "bad actor"),
        ("permission_scope", "bad scope!"),
        ("status", "uploaded"),
    ],
)
def test_invalid_fields_are_rejected(field, value):
    kwargs = {
        "digest": compute_content_hash("x"),
        "path": "inbox/file.txt",
        "size": 1,
        "mtime": "2026-06-19T08:15:00Z",
        "status": "pending",
        "actor": "worker.bot",
        "permission_scope": "metadata_only",
        "provider": "nextcloud_inbox",
    }
    kwargs[field] = value

    with pytest.raises(NextcloudIntakeLedgerError):
        NextcloudIntakeLedgerEntry(**kwargs)


def test_report_and_summary_are_compact_and_routing_ready():
    routed = NextcloudIntakeLedgerEntry(
        digest=compute_content_hash("routed"),
        path="inbox/a.txt",
        size=10,
        mtime="2026-06-19T08:15:00Z",
        status="routed_indexed",
        actor="router.bot",
        permission_scope="metadata_only:route",
        metadata={"name": "a.txt", "raw_text": "sensitive"},
    )
    denied = NextcloudIntakeLedgerEntry(
        digest=compute_content_hash("denied"),
        path="inbox/b.txt",
        size=20,
        mtime="2026-06-19T08:16:00Z",
        status="permission_denied",
        actor="router.bot",
        permission_scope="metadata_only:route",
        errors=("authorization=<redacted-test-sentinel>", "access denied"),
        metadata={"owner": "alice"},
    )

    routed_report = routed.to_report()
    summary = summarize_entries([routed, denied])

    assert routed_report == {
        "digest": routed.digest,
        "path": "inbox/a.txt",
        "status": "routed_indexed",
        "provider": "nextcloud_inbox",
        "actor": "router.bot",
        "permission_scope": "metadata_only:route",
        "error_count": 0,
        "review_required": False,
        "route_ready": True,
        "metadata_keys": ["name", "raw_text"],
    }
    assert summary["total"] == 2
    assert summary["by_status"] == {"permission_denied": 1, "routed_indexed": 1}
    assert summary["review_items"] == 1
    assert summary["route_ready"] == 1
    assert "redacted-test-sentinel" not in json.dumps(summary, sort_keys=True)
    assert summary["items"][1]["review_required"] is True


def test_dumps_entry_returns_stable_redacted_json():
    entry = NextcloudIntakeLedgerEntry(
        digest=compute_content_hash("stable"),
        path="inbox/stable.txt",
        size=3,
        mtime="2026-06-19T08:15:00Z",
        status="metadata_written",
        actor="ledger.bot",
        permission_scope="metadata_only:index",
        metadata={"chat_id": "<redacted-test-sentinel>", "summary": "ok"},
    )

    dumped = dumps_entry(entry)

    assert '"chat_id": "[redacted]"' in dumped
    assert '"status": "metadata_written"' in dumped
    assert '"provider": "nextcloud_inbox"' in dumped
