from __future__ import annotations

import hashlib
import json

import pytest

from src.project_forge_nextcloud import (
    NextcloudForgePayload,
    NextcloudForgePayloadFile,
    NextcloudForgeSyncAdapter,
)
from src.project_forge_sync import ForgeSyncRequest
from src.project_version_store import owner_key_for


OPERATION = "pfo_" + "a" * 32
TRANSACTION = "pct_" + "b" * 32
VERSION = "pv_" + "c" * 32
COMMIT = "d" * 40
LOCAL_MANIFEST = "sha256:" + "e" * 64
OWNER_KEY = owner_key_for("nextcloud-owner@example.test")


def _digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _file(path: str, content: bytes = b"plain project content\n") -> NextcloudForgePayloadFile:
    return NextcloudForgePayloadFile(
        relative_path=path,
        content=content,
        sha256=_digest(content),
        size_bytes=len(content),
    )


def _request(*, provider: str = "nextcloud") -> ForgeSyncRequest:
    return ForgeSyncRequest(
        provider=provider,
        owner_key=OWNER_KEY,
        operation_id=OPERATION,
        idempotency_key=OPERATION,
        repo_id="readable-project",
        transaction_id=TRANSACTION,
        version_id=VERSION,
        commit_sha=COMMIT,
        manifest_evidence={
            "schema": "odysseus.project_version_manifest.v1",
            "sha256": LOCAL_MANIFEST,
            "reference": f"version:{VERSION}",
        },
        expected_fingerprint="sha256:" + "f" * 64,
    )


def _payload(**overrides) -> NextcloudForgePayload:
    values = {
        "files": (_file("README.md", b"# Readable\n"), _file("src/main.py", b"print('ok')\n")),
        "description": "A readable Nextcloud project version.",
        "version_label": "Version 1",
        "change_notes": ("Keep the generated files.",),
        "artifacts": (_file("build/report.txt", b"green\n"),),
        "repository_bundle": _file("repository.bundle", b"bundle-bytes"),
        "include_readable_tree": True,
        "client_side_encryption": False,
    }
    values.update(overrides)
    return NextcloudForgePayload(**values)


class FakePayloadSource:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def load_payload(self, **identifiers):
        self.calls.append(identifiers)
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload


class FakeWebDAV:
    def __init__(self):
        self.files = {}
        self.calls = []
        self.fail_put = False
        self.fail_move = False
        self.move_then_fail = False
        self.corrupt_stage_reads = False

    def stat(self, relative_path):
        self.calls.append(("stat", relative_path))
        if relative_path in self.files:
            return {
                "relative_path": relative_path,
                "size_bytes": len(self.files[relative_path]),
                "etag": "not-a-hash",
                "is_collection": False,
            }
        prefix = relative_path.rstrip("/") + "/"
        if any(path.startswith(prefix) for path in self.files):
            return {
                "relative_path": relative_path,
                "size_bytes": 0,
                "etag": "",
                "is_collection": True,
            }
        return None

    def get_file_bytes(self, relative_path, *, max_bytes):
        self.calls.append(("get", relative_path, max_bytes))
        content = self.files[relative_path]
        if self.corrupt_stage_reads and "/.odysseus/staging/" in relative_path:
            return b"x" * len(content)
        if len(content) > max_bytes:
            raise RuntimeError("oversize")
        return content

    def put_bytes_create_only(self, relative_path, content, *, max_bytes):
        self.calls.append(("put", relative_path, content, True, max_bytes))
        if self.fail_put:
            raise RuntimeError("Bearer must never escape")
        if relative_path in self.files:
            raise RuntimeError("create conflict")
        if len(content) > max_bytes:
            raise RuntimeError("oversize")
        self.files[relative_path] = bytes(content)
        return {"size_bytes": len(content), "etag": "opaque"}

    def move_create_only(self, source_relative, destination_relative):
        self.calls.append(("move", source_relative, destination_relative, False))
        source_prefix = source_relative.rstrip("/") + "/"
        destination_prefix = destination_relative.rstrip("/") + "/"
        if any(path == destination_relative or path.startswith(destination_prefix) for path in self.files):
            raise RuntimeError("destination exists")
        selected = {path: data for path, data in self.files.items() if path.startswith(source_prefix)}
        if self.fail_move and not self.move_then_fail:
            raise RuntimeError("move failed")
        for path in selected:
            del self.files[path]
        for path, data in selected.items():
            self.files[destination_prefix + path[len(source_prefix) :]] = data
        if self.move_then_fail:
            raise RuntimeError("response lost")
        return {"created": True, "etag": ""}


def _adapter(client, payload, **limits):
    source = FakePayloadSource(payload)
    return NextcloudForgeSyncAdapter(
        webdav_client=client,
        payload_source=source,
        **limits,
    ), source


def _mutations(client):
    return [call for call in client.calls if call[0] in {"put", "move", "delete", "copy"}]


def test_stage_verify_promote_keeps_tree_and_artifacts_readable():
    client = FakeWebDAV()
    adapter, source = _adapter(client, _payload())

    outcome = adapter.sync(_request())

    final = f"Odysseus/Projects/readable-project/Versions/{VERSION}"
    assert outcome.status == "synced"
    assert outcome.idempotency_key == OPERATION
    assert outcome.version_id == VERSION
    assert client.files[f"{final}/Tree/README.md"] == b"# Readable\n"
    assert client.files[f"{final}/Tree/src/main.py"] == b"print('ok')\n"
    assert client.files[f"{final}/Artifacts/build/report.txt"] == b"green\n"
    assert client.files[f"{final}/repository.bundle"] == b"bundle-bytes"
    manifest_bytes = client.files[f"{final}/manifest.json"]
    manifest = json.loads(manifest_bytes)
    assert manifest["readable_tree"] is True
    assert manifest["client_side_encryption"] is False
    assert manifest["project_current_promoted"] is False
    assert manifest["description"] == "A readable Nextcloud project version."
    assert not any("/.git/" in path or path.endswith("/.git") for path in client.files)
    assert source.calls == [
        {
            "owner_key": OWNER_KEY,
            "operation_id": OPERATION,
            "repo_id": "readable-project",
            "transaction_id": TRANSACTION,
            "version_id": VERSION,
        }
    ]
    puts = [call for call in client.calls if call[0] == "put"]
    assert puts[-1][1].endswith("/manifest.json")
    assert all(call[3] is True for call in puts)
    assert [call[0] for call in client.calls].count("move") == 1
    assert not any(call[0] in {"delete", "copy"} for call in client.calls)


def test_identical_manifest_is_already_synced_without_second_upload():
    client = FakeWebDAV()
    first, _ = _adapter(client, _payload())
    assert first.sync(_request()).status == "synced"
    mutations_before = list(_mutations(client))
    second, _ = _adapter(client, _payload())

    outcome = second.sync(_request())

    assert outcome.status == "already_synced"
    assert outcome.provider_fingerprint.startswith("sha256:")
    assert _mutations(client) == mutations_before


def test_different_remote_manifest_is_diverged_without_overwrite():
    client = FakeWebDAV()
    final_manifest = (
        f"Odysseus/Projects/readable-project/Versions/{VERSION}/manifest.json"
    )
    client.files[final_manifest] = b'{"different":true}\n'
    adapter, _ = _adapter(client, _payload())

    outcome = adapter.sync(_request())

    assert outcome.status == "diverged"
    assert outcome.provider_fingerprint == _digest(b'{"different":true}\n')
    assert _mutations(client) == []
    assert client.files[final_manifest] == b'{"different":true}\n'


def test_existing_version_without_manifest_is_diverged_before_staging():
    client = FakeWebDAV()
    final_tree = (
        f"Odysseus/Projects/readable-project/Versions/{VERSION}/Tree/orphan.txt"
    )
    client.files[final_tree] = b"orphan"
    adapter, _ = _adapter(client, _payload())

    outcome = adapter.sync(_request())

    assert outcome.status == "diverged"
    assert outcome.provider_fingerprint.startswith("sha256:")
    assert _mutations(client) == []


@pytest.mark.parametrize(
    "unsafe_path",
    (
        "../escape.txt",
        "/absolute.txt",
        "folder\\file.txt",
        ".git/config",
        ".env.local",
        ".ssh/config",
        "node_modules/lib.js",
        ".cache/value",
        "tmp/output.txt",
        "credentials.json",
        "private-key.pem",
        "nul\x00file",
    ),
)
def test_unsafe_payload_paths_are_blocked_before_webdav_mutation(unsafe_path):
    client = FakeWebDAV()
    adapter, _ = _adapter(client, _payload(files=(_file(unsafe_path),)))

    outcome = adapter.sync(_request())

    assert outcome.status == "permanent_failure"
    assert outcome.error_code == "payload_invalid"
    assert _mutations(client) == []


def test_size_and_hash_errors_are_blocked_before_webdav_mutation():
    cases = (
        _payload(files=(NextcloudForgePayloadFile("a.txt", b"abc", _digest(b"abc"), 2),)),
        _payload(files=(NextcloudForgePayloadFile("a.txt", b"abc", _digest(b"other"), 3),)),
        _payload(files=(_file("a.txt", b"12345"),)),
    )
    for index, payload in enumerate(cases):
        client = FakeWebDAV()
        limits = {"max_file_bytes": 4} if index == 2 else {}
        adapter, _ = _adapter(client, payload, **limits)
        outcome = adapter.sync(_request())
        assert outcome.status == "permanent_failure"
        assert _mutations(client) == []


def test_file_count_and_total_size_limits_block_before_webdav_mutation():
    cases = (
        (_payload(), {"max_files": 1}),
        (_payload(files=(_file("a.txt", b"123"), _file("b.txt", b"456")), artifacts=(), repository_bundle=None), {"max_total_bytes": 5}),
    )
    for payload, limits in cases:
        client = FakeWebDAV()
        adapter, _ = _adapter(client, payload, **limits)
        outcome = adapter.sync(_request())
        assert outcome.status == "permanent_failure"
        assert _mutations(client) == []


@pytest.mark.parametrize(
    "payload",
    (
        _payload(client_side_encryption=True),
        _payload(include_readable_tree=False),
        _payload(description="token=not-a-real-value"),
    ),
)
def test_encryption_non_readable_and_sensitive_metadata_are_blocked(payload):
    client = FakeWebDAV()
    adapter, _ = _adapter(client, payload)

    outcome = adapter.sync(_request())

    assert outcome.status in {"blocked", "permanent_failure"}
    assert _mutations(client) == []


def test_upload_verify_and_promotion_failures_are_redacted_and_never_delete():
    clients = []

    upload_failure = FakeWebDAV()
    upload_failure.fail_put = True
    clients.append((upload_failure, "webdav_upload_failed"))

    verify_failure = FakeWebDAV()
    verify_failure.corrupt_stage_reads = True
    clients.append((verify_failure, "remote_verify_failed"))

    promotion_failure = FakeWebDAV()
    promotion_failure.fail_move = True
    clients.append((promotion_failure, "promotion_failed"))

    for client, error_code in clients:
        adapter, _ = _adapter(client, _payload())
        outcome = adapter.sync(_request())
        assert outcome.status == "retryable_failure"
        assert outcome.error_code == error_code
        assert "Bearer" not in repr(outcome.to_dict())
        assert not any(call[0] in {"delete", "copy"} for call in client.calls)


def test_lost_move_response_is_recognized_as_idempotent_success():
    client = FakeWebDAV()
    client.fail_move = True
    client.move_then_fail = True
    adapter, _ = _adapter(client, _payload())

    outcome = adapter.sync(_request())

    assert outcome.status == "already_synced"
    assert outcome.provider_fingerprint.startswith("sha256:")


def test_wrong_provider_is_blocked_before_payload_or_webdav_access():
    client = FakeWebDAV()
    adapter, source = _adapter(client, _payload())

    outcome = adapter.sync(_request(provider="github"))

    assert outcome.status == "blocked"
    assert outcome.error_code == "provider_mismatch"
    assert source.calls == []
    assert client.calls == []
