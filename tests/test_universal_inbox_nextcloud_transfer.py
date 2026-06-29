import json
from pathlib import Path

from src.universal_inbox_nextcloud_transfer import (
    UniversalInboxNextcloudTransferRequest,
    execute_universal_inbox_nextcloud_transfer,
)


class FakeNextcloudTransferClient:
    def __init__(self, *, existing: dict[str, bytes] | None = None):
        self.files = dict(existing or {})
        self.puts = []
        self.sidecars = {}

    def stat(self, relative_path: str):
        if relative_path not in self.files:
            return None
        return {"size_bytes": len(self.files[relative_path]), "etag": f"etag-{relative_path}"}

    def put_file(self, source_path: Path, relative_path: str):
        payload = source_path.read_bytes()
        self.files[relative_path] = payload
        self.puts.append((relative_path, len(payload)))
        return {"size_bytes": len(payload), "etag": "etag-uploaded"}

    def put_text(self, relative_path: str, text: str):
        self.sidecars[relative_path] = text
        return {"size_bytes": len(text.encode("utf-8")), "etag": "etag-sidecar"}


def _request(source: Path, **overrides):
    placement = {
        "target_path": "Documents/Private/Reference/file.txt",
        "sidecar_path": "AI Inbox/Metadata/file.odysseus.json",
        "overwrite_existing": False,
        "delete_original": False,
    }
    placement.update(overrides.pop("placement", {}))
    return UniversalInboxNextcloudTransferRequest.from_placement_plan(
        placement,
        source_path=source,
        source_hash="a" * 64,
        review_approved=overrides.pop("review_approved", True),
        operator_live_go=overrides.pop("operator_live_go", False),
        dry_run=overrides.pop("dry_run", True),
        **overrides,
    )


def test_dry_run_never_calls_client_or_writes(tmp_path):
    source = tmp_path / "file.txt"
    source.write_text("private runtime body", encoding="utf-8")
    client = FakeNextcloudTransferClient()

    result = execute_universal_inbox_nextcloud_transfer(_request(source), client=client)
    payload = result.to_dict()

    assert payload["status"] == "dry_run_ready"
    assert payload["writes_performed"] is False
    assert payload["verified"] is False
    assert payload["target_path"] == "Documents/Private/Reference/file.txt"
    assert client.puts == []
    assert client.sidecars == {}
    assert "private runtime body" not in json.dumps(payload, sort_keys=True)


def test_live_copy_requires_review_and_operator_go(tmp_path):
    source = tmp_path / "file.txt"
    source.write_text("body", encoding="utf-8")

    no_review = execute_universal_inbox_nextcloud_transfer(
        _request(source, review_approved=False, dry_run=False, operator_live_go=True),
        client=FakeNextcloudTransferClient(),
    )
    no_go = execute_universal_inbox_nextcloud_transfer(
        _request(source, review_approved=True, dry_run=False, operator_live_go=False),
        client=FakeNextcloudTransferClient(),
    )

    assert no_review.status == "blocked"
    assert "review_approval_missing" in no_review.blocked_reasons
    assert no_go.status == "blocked"
    assert "operator_live_go_missing" in no_go.blocked_reasons


def test_live_copy_writes_sidecar_and_verifies_size(tmp_path):
    source = tmp_path / "file.txt"
    source.write_text("copy me", encoding="utf-8")
    client = FakeNextcloudTransferClient()

    result = execute_universal_inbox_nextcloud_transfer(
        _request(source, dry_run=False, operator_live_go=True),
        client=client,
    )
    payload = result.to_dict()
    sidecar = json.loads(client.sidecars["AI Inbox/Metadata/file.odysseus.json"])

    assert payload["status"] == "completed"
    assert payload["writes_performed"] is True
    assert payload["verified"] is True
    assert payload["source_size_bytes"] == 7
    assert payload["target_size_bytes"] == 7
    assert client.files["Documents/Private/Reference/file.txt"] == b"copy me"
    assert sidecar["target_path"] == "Documents/Private/Reference/file.txt"
    assert sidecar["copy_only"] is True
    assert sidecar["delete_original"] is False
    assert sidecar["overwrite_existing"] is False
    assert "copy me" not in json.dumps(payload, sort_keys=True)
    assert "copy me" not in json.dumps(sidecar, sort_keys=True)


def test_live_copy_blocks_existing_target_without_overwrite(tmp_path):
    source = tmp_path / "file.txt"
    source.write_text("new", encoding="utf-8")
    client = FakeNextcloudTransferClient(existing={"Documents/Private/Reference/file.txt": b"old"})

    result = execute_universal_inbox_nextcloud_transfer(
        _request(source, dry_run=False, operator_live_go=True),
        client=client,
    )

    assert result.status == "blocked"
    assert result.reason == "target_exists"
    assert result.writes_performed is False
    assert client.files["Documents/Private/Reference/file.txt"] == b"old"


def test_request_rejects_unsafe_target_paths(tmp_path):
    source = tmp_path / "file.txt"
    source.write_text("body", encoding="utf-8")

    try:
        _request(source, placement={"target_path": "C:/Users/private/file.txt"})
    except ValueError as exc:
        assert "relative path must not be absolute" in str(exc)
    else:
        raise AssertionError("absolute target paths should be rejected")
