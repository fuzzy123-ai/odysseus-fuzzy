from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path

import pytest

from src.bigdata_ledger_contract import BigDataLedgerItem, BigDataLedgerRecord
from src.harbour_authority import AuthenticatedPrincipalIssuer
from src.harbour_knowledge_store import (
    HarbourKnowledgeStore,
    HarbourKnowledgeStoreError,
    KnowledgeHead,
)
import src.nextcloud_knowledge_ingestion as ingestion
from src.personal_docs_owner_reader import PersonalDocsOwnerReader


NOW = datetime(2026, 10, 9, 7, 0, tzinfo=timezone.utc)
AUTH = "sha256:" + "a" * 64
ROOT_REF = "sha256:" + "b" * 64
OBSERVED = "2026-10-09T06:59:00Z"


def _issuer(*, now: datetime = NOW, name: str = "h1r11-test") -> AuthenticatedPrincipalIssuer:
    return AuthenticatedPrincipalIssuer(
        issuer_id=name,
        key_id=name + "-key",
        secret=(name.encode("ascii") + b"x" * 64)[:32],
        clock=lambda: now,
    )


def _principal(issuer: AuthenticatedPrincipalIssuer, *, owner: str = "alice"):
    return issuer.issue(
        subject_id=owner + "-subject",
        owner_id=owner,
        auth_context_digest=AUTH,
        ttl_seconds=3600,
        nonce=owner + "-nonce",
    )


def _store(tmp_path: Path, issuer: AuthenticatedPrincipalIssuer) -> HarbourKnowledgeStore:
    return HarbourKnowledgeStore(
        tmp_path / "knowledge.sqlite3",
        trusted_issuers=(issuer,),
        clock=lambda: NOW,
    )


def _reader(root: Path, *, owner: str = "alice") -> PersonalDocsOwnerReader:
    return PersonalDocsOwnerReader(owner_id=owner, root_ref=ROOT_REF, root_path=str(root))


def _inventory(
    relative_path: str,
    content: str,
    *,
    extension: object | None = None,
    category: object = "text_extractable",
    candidate: object = True,
    size: int | None = None,
    extra_metadata: dict[str, object] | None = None,
    observed_at: str = OBSERVED,
) -> BigDataLedgerRecord:
    suffix = "." + relative_path.rsplit(".", 1)[1].lower() if "." in relative_path else ""
    metadata: dict[str, object] = {
        "file_category": category,
        "extension": suffix if extension is None else extension,
        "privacy": {
            "memory_write_candidate": candidate,
            "privacy_class": "archive_candidate",
        },
    }
    metadata.update(extra_metadata or {})
    return BigDataLedgerRecord(
        item=BigDataLedgerItem(
            provider="nextcloud",
            source_id="nextcloud-synthetic",
            relative_path=relative_path,
            size_bytes=len(content.encode("utf-8")) if size is None else size,
            mtime=observed_at,
        ),
        stage="inventory",
        status="completed",
        updated_at=observed_at,
        metadata=metadata,
    )


def _setup(tmp_path: Path, *, content: str = "hello private world", filename: str = "note.txt"):
    root = tmp_path / "personal-docs"
    root.mkdir()
    path = root / filename
    path.write_text(content, encoding="utf-8", newline="")
    issuer = _issuer()
    principal = _principal(issuer)
    store = _store(tmp_path, issuer)
    record = _inventory(filename, content)
    return root, path, issuer, principal, store, record


def _ingest(reader, record, store, principal, *, owner="alice"):
    return ingestion.ingest_nextcloud_personal_document(
        reader=reader,
        inventory_record=record,
        store=store,
        principal=principal,
        owner_id=owner,
    )


def _code(callback, expected: str) -> None:
    with pytest.raises(ingestion.NextcloudKnowledgeIngestionError) as raised:
        callback()
    assert raised.value.code == expected
    assert raised.value.args == (expected,)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def _counts(store: HarbourKnowledgeStore) -> tuple[int, ...]:
    return tuple(
        store._conn().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in (
            "knowledge_blobs",
            "knowledge_revisions",
            "knowledge_heads",
            "knowledge_events",
            "knowledge_receipts",
            "knowledge_idempotency",
        )
    )


def test_owner_reference_published_golden() -> None:
    assert ingestion.canonical_personal_docs_owner_ref("alice") == (
        "sha256:9252d17f54fbaca5563d5e4460654985e29a39951275a10ebc785de8e3a518f4"
    )


def test_exact_create_persists_payload_source_provenance_and_retention(tmp_path: Path) -> None:
    content = "strict UTF-8: café\n"
    root, _path, _issuer_value, principal, store, record = _setup(tmp_path, content=content)
    reader = _reader(root)

    result = _ingest(reader, record, store, principal)
    revision = store.read_revision(
        principal, owner_id="alice", revision_id=result.head.current_revision_id
    )
    source = revision.source_revisions[0]
    snapshot = reader.capture()
    descriptor = snapshot.descriptors[0]

    assert result.outcome == "created"
    assert result.receipt is not None and result.receipt.command_kind == "create_object"
    assert store.read_payload(principal, owner_id="alice", revision_id=revision.revision_id) == content.encode()
    assert revision.knowledge_kind == "document"
    assert revision.payload_schema_id == ingestion.PAYLOAD_SCHEMA_ID
    assert revision.payload_media_type == "text/plain"
    assert revision.classification == "sensitive"
    assert revision.retention == ingestion.RETENTION
    assert source == {
        "source_revision_id": result.source_revision_id,
        "source_kind": "personal_document",
        "source_id": descriptor.source_ref,
        "revision_ref": descriptor.revision_ref,
        "source_content_sha256": "sha256:" + hashlib.sha256(content.encode()).hexdigest(),
        "disposition": "imported",
        "locator_digest": descriptor.locator.locator_digest,
        "authority_domain": "personal_docs",
        "authority_revision": snapshot.snapshot_digest,
        "authority_digest": descriptor.descriptor_digest,
        "predecessor_source_revision_id": None,
        "observed_at": "2026-10-09T06:59:00.000000Z",
    }
    assert revision.provenance == ({
        "role": "origin",
        "source_revision_id": result.source_revision_id,
        "knowledge_revision_id": None,
        "transformation_digest": None,
        "admission_receipt_digest": snapshot.snapshot_digest,
    },)
    assert source["authority_revision"] != source["authority_digest"]
    assert revision.provenance[0]["admission_receipt_digest"] == source["authority_revision"]
    assert record.item.item_id not in result.knowledge_id
    assert record.item.relative_path not in result.knowledge_id


@pytest.mark.parametrize(
    ("filename", "content", "media_type"),
    (
        ("note.md", "# strict UTF-8: café\n", "text/markdown"),
        ("note.json", '{"message":"café"}\n', "application/json"),
    ),
)
def test_markdown_and_json_preserve_exact_payload_and_media_type(
    tmp_path: Path, filename: str, content: str, media_type: str
) -> None:
    root, _path, _issuer_value, principal, store, record = _setup(
        tmp_path, content=content, filename=filename
    )

    result = _ingest(_reader(root), record, store, principal)
    revision = store.read_revision(
        principal, owner_id="alice", revision_id=result.head.current_revision_id
    )

    assert store.read_payload(
        principal, owner_id="alice", revision_id=revision.revision_id
    ) == content.encode("utf-8")
    assert revision.payload_media_type == media_type


def test_restart_same_source_is_zero_write(tmp_path: Path) -> None:
    root, _path, issuer, principal, store, record = _setup(tmp_path)
    first = _ingest(_reader(root), record, store, principal)
    before = _counts(store)
    database = store.database_path
    store.close()
    restarted = HarbourKnowledgeStore.open_existing(
        database, trusted_issuers=(issuer,), clock=lambda: NOW
    )

    second = _ingest(_reader(root), record, restarted, principal)

    assert second.outcome == "unchanged"
    assert second.receipt is None
    assert second.knowledge_id == first.knowledge_id
    assert second.source_revision_id == first.source_revision_id
    assert second.head == first.head
    assert _counts(restarted) == before


def test_changed_source_appends_once_with_predecessor(tmp_path: Path) -> None:
    root, path, _issuer_value, principal, store, first_record = _setup(tmp_path, content="one")
    first = _ingest(_reader(root), first_record, store, principal)
    path.write_text("two changed", encoding="utf-8")
    second_record = _inventory("note.txt", "two changed", observed_at="2026-10-09T07:00:00Z")

    second = _ingest(_reader(root), second_record, store, principal)
    revision = store.read_revision(
        principal, owner_id="alice", revision_id=second.head.current_revision_id
    )

    assert second.outcome == "appended"
    assert second.knowledge_id == first.knowledge_id
    assert second.head.generation == first.head.generation + 1
    assert revision.parent_revision_id == first.head.current_revision_id
    assert revision.source_revisions[0]["predecessor_source_revision_id"] == first.source_revision_id
    assert store._conn().execute("SELECT COUNT(*) FROM knowledge_revisions").fetchone()[0] == 2


@pytest.mark.parametrize("changed", (False, True))
def test_existing_same_owner_references_are_denied_before_exact_read_or_write(
    tmp_path: Path, monkeypatch, changed: bool
) -> None:
    root, path, _issuer_value, principal, store, record = _setup(tmp_path, content="one")
    created = _ingest(_reader(root), record, store, principal)
    original = store.read_revision(
        principal, owner_id="alice", revision_id=created.head.current_revision_id
    )
    foreign_reference = {
        "domain": "knowledge",
        "owner_id": "alice",
        "target_id": "foreign-knowledge-object",
        "revision_ref": "foreign-knowledge-revision",
        "content_sha256": original.payload_sha256,
    }
    referenced = store.append_revision(
        principal,
        owner_id="alice",
        knowledge_id=created.knowledge_id,
        payload_bytes=b"one",
        payload_schema_id=original.payload_schema_id,
        payload_media_type=original.payload_media_type,
        classification=original.classification,
        source_revisions=original.source_revisions,
        provenance=original.provenance,
        references=(foreign_reference,),
        retention=original.retention,
        expected_head=created.head,
        idempotency_key="same-owner-reference-append-001",
    )
    referenced_revision = store.read_revision(
        principal,
        owner_id="alice",
        revision_id=referenced.after_head.current_revision_id,
    )
    assert referenced_revision.references == (foreign_reference,)

    if changed:
        path.write_text("two changed", encoding="utf-8")
        record = _inventory("note.txt", "two changed")
    before_counts = _counts(store)
    reads: list[bool] = []

    def forbidden_read(self, descriptor):
        reads.append(True)
        pytest.fail("existing reference denial must precede exact content read")

    monkeypatch.setattr(PersonalDocsOwnerReader, "read_exact", forbidden_read)
    _code(lambda: _ingest(_reader(root), record, store, principal), "existing_state_denied")

    assert reads == []
    assert _counts(store) == before_counts
    assert store.get_head(
        principal, owner_id="alice", knowledge_id=created.knowledge_id
    ) == referenced.after_head
    preserved = store.read_revision(
        principal,
        owner_id="alice",
        revision_id=referenced.after_head.current_revision_id,
    )
    assert preserved.references == (foreign_reference,)


def test_authentication_and_owner_scope_precede_capture(tmp_path: Path, monkeypatch) -> None:
    root, _path, issuer, principal, store, record = _setup(tmp_path)
    captured: list[bool] = []

    def forbidden_capture(self):
        captured.append(True)
        pytest.fail("source capture must not run")

    monkeypatch.setattr(PersonalDocsOwnerReader, "capture", forbidden_capture)
    _code(lambda: _ingest(_reader(root), record, store, principal, owner="bob"), "authentication_denied")

    expired_issuer = _issuer(now=NOW - timedelta(hours=2), name="expired-h1r11")
    expired = expired_issuer.issue(
        subject_id="alice-subject",
        owner_id="alice",
        auth_context_digest=AUTH,
        ttl_seconds=60,
        nonce="expired-nonce",
    )
    expired_store = HarbourKnowledgeStore(
        tmp_path / "expired.sqlite3",
        trusted_issuers=(expired_issuer,),
        clock=lambda: NOW,
    )
    _code(lambda: _ingest(_reader(root), record, expired_store, expired), "authentication_denied")
    assert captured == []


def test_capture_owner_mismatch_precedes_exact_read_and_write(tmp_path: Path, monkeypatch) -> None:
    root, _path, _issuer_value, principal, store, record = _setup(tmp_path)
    reads: list[bool] = []

    def forbidden_read(self, descriptor):
        reads.append(True)
        pytest.fail("exact read must not run")

    monkeypatch.setattr(PersonalDocsOwnerReader, "read_exact", forbidden_read)
    _code(lambda: _ingest(_reader(root, owner="bob"), record, store, principal), "source_denied")
    assert reads == []
    assert _counts(store) == (0, 0, 0, 0, 0, 0)


@pytest.mark.parametrize(
    "record_builder",
    (
        lambda: _inventory("other.txt", "hello private world"),
        lambda: _inventory("note.txt", "hello private world", size=999),
        lambda: _inventory("note.txt", "hello private world", category="document_extractable"),
        lambda: _inventory("note.txt", "hello private world", candidate=False),
        lambda: _inventory("note.txt", "hello private world", extension=".md"),
        lambda: _inventory("note.txt", "hello private world", candidate=1),
        lambda: _inventory(
            "note.txt", "hello private world", extra_metadata={"owner_id": "alice"}
        ),
    ),
)
def test_inventory_boundary_rejects_wrong_fields_before_exact_read_or_write(
    tmp_path: Path, monkeypatch, record_builder
) -> None:
    root, _path, _issuer_value, principal, store, _record = _setup(tmp_path)
    reads: list[bool] = []

    def forbidden_read(self, descriptor):
        reads.append(True)
        pytest.fail("exact read must not run")

    monkeypatch.setattr(PersonalDocsOwnerReader, "read_exact", forbidden_read)
    _code(lambda: _ingest(_reader(root), record_builder(), store, principal), "inventory_denied")
    assert reads == []
    assert _counts(store) == (0, 0, 0, 0, 0, 0)


def test_inventory_metadata_mutation_during_capture_is_denied_before_read(tmp_path: Path, monkeypatch) -> None:
    root, _path, _issuer_value, principal, store, record = _setup(tmp_path)
    original_capture = PersonalDocsOwnerReader.capture
    reads: list[bool] = []

    def mutating_capture(self):
        snapshot = original_capture(self)
        record.metadata["file_category"] = "changed"
        return snapshot

    def forbidden_read(self, descriptor):
        reads.append(True)
        pytest.fail("exact read must not run")

    monkeypatch.setattr(PersonalDocsOwnerReader, "capture", mutating_capture)
    monkeypatch.setattr(PersonalDocsOwnerReader, "read_exact", forbidden_read)
    _code(lambda: _ingest(_reader(root), record, store, principal), "inventory_denied")
    assert reads == []
    assert _counts(store) == (0, 0, 0, 0, 0, 0)


def test_inventory_metadata_mutation_during_exact_read_is_denied_before_write(tmp_path: Path, monkeypatch) -> None:
    root, _path, _issuer_value, principal, store, record = _setup(tmp_path)
    original_read = PersonalDocsOwnerReader.read_exact

    def mutating_read(self, descriptor):
        exact = original_read(self, descriptor)
        record.metadata["privacy"]["memory_write_candidate"] = False
        return exact

    monkeypatch.setattr(PersonalDocsOwnerReader, "read_exact", mutating_read)
    _code(lambda: _ingest(_reader(root), record, store, principal), "inventory_denied")
    assert _counts(store) == (0, 0, 0, 0, 0, 0)


def test_authority_digest_mutation_after_exact_read_is_denied_before_write(
    tmp_path: Path, monkeypatch
) -> None:
    root, _path, _issuer_value, principal, store, record = _setup(tmp_path)
    reader = _reader(root)
    original_capture = PersonalDocsOwnerReader.capture
    original_read = PersonalDocsOwnerReader.read_exact
    captured: dict[str, object] = {}

    def capture(self):
        snapshot = original_capture(self)
        captured["snapshot"] = snapshot
        return snapshot

    def mutating_read(self, descriptor):
        exact = original_read(self, descriptor)
        object.__setattr__(descriptor, "descriptor_digest", "sha256:" + "c" * 64)
        object.__setattr__(captured["snapshot"], "snapshot_digest", "sha256:" + "d" * 64)
        return exact

    monkeypatch.setattr(PersonalDocsOwnerReader, "capture", capture)
    monkeypatch.setattr(PersonalDocsOwnerReader, "read_exact", mutating_read)
    _code(lambda: _ingest(reader, record, store, principal), "source_denied")
    assert _counts(store) == (0, 0, 0, 0, 0, 0)


@pytest.mark.parametrize(
    ("sealed_content", "substituted_content"),
    (
        ("abcd", "wxyz"),
        ("abcd", "x"),
        ("ab", "é"),
        ("a", "é"),
    ),
)
def test_mutated_exact_read_content_is_denied_before_write(
    tmp_path: Path, monkeypatch, sealed_content: str, substituted_content: str
) -> None:
    root, path, _issuer_value, principal, store, record = _setup(
        tmp_path, content=sealed_content
    )
    original_read = PersonalDocsOwnerReader.read_exact

    def mutating_read(self, descriptor):
        exact = original_read(self, descriptor)
        object.__setattr__(exact, "content", substituted_content)
        return exact

    monkeypatch.setattr(PersonalDocsOwnerReader, "read_exact", mutating_read)
    _code(lambda: _ingest(_reader(root), record, store, principal), "source_denied")
    assert _counts(store) == (0, 0, 0, 0, 0, 0)
    assert path.read_bytes() == sealed_content.encode("utf-8")


def test_empty_utf8_is_denied_before_store_mutation(tmp_path: Path) -> None:
    root, _path, _issuer_value, principal, store, record = _setup(tmp_path, content="")
    _code(lambda: _ingest(_reader(root), record, store, principal), "source_denied")
    assert _counts(store) == (0, 0, 0, 0, 0, 0)


def test_exact_read_drift_is_denied_without_store_mutation(tmp_path: Path, monkeypatch) -> None:
    root, path, _issuer_value, principal, store, record = _setup(tmp_path, content="before")
    original_capture = PersonalDocsOwnerReader.capture

    def capture_then_change(self):
        snapshot = original_capture(self)
        path.write_text("after!", encoding="utf-8")
        return snapshot

    monkeypatch.setattr(PersonalDocsOwnerReader, "capture", capture_then_change)
    _code(lambda: _ingest(_reader(root), record, store, principal), "source_denied")
    assert _counts(store) == (0, 0, 0, 0, 0, 0)


def test_stale_append_fails_once_without_retry(tmp_path: Path, monkeypatch) -> None:
    root, path, _issuer_value, principal, store, first_record = _setup(tmp_path, content="one")
    first = _ingest(_reader(root), first_record, store, principal)
    path.write_text("two", encoding="utf-8")
    second_record = _inventory("note.txt", "two")
    original_append = store.append_revision
    calls: list[bool] = []

    def race(principal_arg, **kwargs):
        calls.append(True)
        competitor = dict(kwargs)
        competitor["payload_bytes"] = b"competitor"
        competitor["idempotency_key"] = "competitor-append-001"
        original_append(principal_arg, **competitor)
        return original_append(principal_arg, **kwargs)

    monkeypatch.setattr(store, "append_revision", race)
    _code(lambda: _ingest(_reader(root), second_record, store, principal), "store_failed")
    assert calls == [True]
    assert store.get_head(principal, owner_id="alice", knowledge_id=first.knowledge_id).generation == 2


def test_store_failure_preserves_exact_prior_head(tmp_path: Path, monkeypatch) -> None:
    root, path, _issuer_value, principal, store, first_record = _setup(tmp_path, content="one")
    first = _ingest(_reader(root), first_record, store, principal)
    path.write_text("changed", encoding="utf-8")
    changed = _inventory("note.txt", "changed")
    prior = store.get_head(principal, owner_id="alice", knowledge_id=first.knowledge_id)

    def fail(*args, **kwargs):
        raise RuntimeError("private store exception")

    monkeypatch.setattr(store, "append_revision", fail)
    _code(lambda: _ingest(_reader(root), changed, store, principal), "store_failed")
    assert store.get_head(principal, owner_id="alice", knowledge_id=first.knowledge_id) == prior


def test_unchanged_head_race_is_closed_with_zero_adapter_writes(tmp_path: Path, monkeypatch) -> None:
    root, _path, _issuer_value, principal, store, record = _setup(tmp_path)
    first = _ingest(_reader(root), record, store, principal)
    original_get = store.get_head
    calls = 0

    def racing_get(principal_arg, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            current = original_get(principal_arg, **kwargs)
            store.tombstone(
                principal_arg,
                owner_id="alice",
                knowledge_id=first.knowledge_id,
                expected_head=current,
                idempotency_key="competitor-tombstone-001",
            )
        return original_get(principal_arg, **kwargs)

    monkeypatch.setattr(store, "get_head", racing_get)
    _code(lambda: _ingest(_reader(root), record, store, principal), "stale_state")
    assert calls == 2
    assert _counts(store)[1] == 1


def test_tombstone_is_denied_until_separate_explicit_restore(tmp_path: Path, monkeypatch) -> None:
    root, _path, _issuer_value, principal, store, record = _setup(tmp_path)
    created = _ingest(_reader(root), record, store, principal)
    tombstoned = store.tombstone(
        principal,
        owner_id="alice",
        knowledge_id=created.knowledge_id,
        expected_head=created.head,
        idempotency_key="explicit-tombstone-001",
    )
    reads: list[bool] = []
    original_read = PersonalDocsOwnerReader.read_exact

    def forbidden_read(self, descriptor):
        reads.append(True)
        pytest.fail("tombstone denial must precede exact content read")

    monkeypatch.setattr(PersonalDocsOwnerReader, "read_exact", forbidden_read)
    _code(lambda: _ingest(_reader(root), record, store, principal), "lifecycle_denied")
    assert reads == []
    monkeypatch.setattr(PersonalDocsOwnerReader, "read_exact", original_read)

    restored = store.restore(
        principal,
        owner_id="alice",
        knowledge_id=created.knowledge_id,
        expected_head=tombstoned.after_head,
        idempotency_key="explicit-restore-001",
    )
    result = _ingest(_reader(root), record, store, principal)
    assert restored.after_head.state == "active"
    assert result.outcome == "unchanged"
    assert result.receipt is None


@pytest.mark.parametrize("state", ("purge_pending", "purged"))
def test_purge_state_is_denied_before_exact_read_without_store_mutation(
    tmp_path: Path, monkeypatch, state: str
) -> None:
    root, _path, _issuer_value, principal, store, record = _setup(tmp_path)
    created = _ingest(_reader(root), record, store, principal)
    before = _counts(store)
    lifecycle_head = KnowledgeHead(
        created.head.owner_id,
        created.head.knowledge_id,
        created.head.knowledge_kind,
        created.head.current_revision_id,
        created.head.current_payload_sha256,
        created.head.generation,
        state,
        OBSERVED,
        OBSERVED,
    )
    reads: list[bool] = []

    def exact_lifecycle_head(principal_arg, **kwargs):
        return lifecycle_head

    def forbidden_read(self, descriptor):
        reads.append(True)
        pytest.fail("purge lifecycle denial must precede exact content read")

    monkeypatch.setattr(store, "get_head", exact_lifecycle_head)
    monkeypatch.setattr(PersonalDocsOwnerReader, "read_exact", forbidden_read)
    _code(lambda: _ingest(_reader(root), record, store, principal), "lifecycle_denied")
    assert reads == []
    assert _counts(store) == before


def test_result_and_errors_are_content_free(tmp_path: Path) -> None:
    private = "VERY_PRIVATE_DOCUMENT_BODY"
    root, _path, _issuer_value, principal, store, record = _setup(tmp_path, content=private)
    result = _ingest(_reader(root), record, store, principal)
    rendered = repr(result)
    for forbidden in (private, "alice", "note.txt", str(root), record.item.item_id):
        assert forbidden not in rendered
    _code(
        lambda: ingestion.ingest_nextcloud_personal_document(
            reader=_reader(root),
            inventory_record=record,
            store=store,
            principal=object(),
            owner_id="alice",
        ),
        "invalid_request",
    )


def test_static_boundary_has_no_ledger_provider_route_or_source_write_capability() -> None:
    path = Path(ingestion.__file__)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    assert imports == {
        "__future__",
        "dataclasses",
        "hashlib",
        "re",
        "types",
        "typing",
        "src.bigdata_ledger_contract",
        "src.harbour_authority",
        "src.harbour_knowledge_store",
        "src.personal_docs_owner_reader",
    }
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert called_attributes.isdisjoint(
        {
            "append_record",
            "append_event",
            "write",
            "write_text",
            "write_bytes",
            "mkdir",
            "unlink",
            "remove",
            "replace",
            "request",
            "post",
            "put",
            "delete",
            "index_personal_documents",
            "activate",
            "grant",
        }
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"open", "exec", "eval", "compile"}
        for node in ast.walk(tree)
    )
