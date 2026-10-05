from __future__ import annotations

import ast
import base64
from dataclasses import fields, replace, FrozenInstanceError
import hashlib
import inspect
import json
from datetime import datetime, timezone
import pytest

import src.unified_source_index_sources.knowledge as knowledge_module
from src.harbour_authority import AuthenticatedPrincipalIssuer
from src.harbour_knowledge_store import HarbourKnowledgeStore, KnowledgeHeadMetadata, KnowledgeMigrationSnapshot, KNOWLEDGE_MIGRATION_SNAPSHOT_SCHEMA
from src.unified_source_index_adapters import AdapterScope, ExactReadRequest, ExtractionProfile, PolicyReadContext, SourceAdapterError, UnavailableReason
from src.unified_source_index_contract import Classification, ContentPolicy, EvidenceRef, IndexJobKind, IndexJobRecord, JobStatus, RecordKind, SourceKind, SourceScope, content_hash, MAX_TEXT_CHARS
from src.unified_source_index_adapters import MAX_ADAPTER_CURSOR_CHARS
from src.unified_source_index_stores import InMemoryUnifiedSourceIndexStore, StoredRecord
from src.unified_source_index_jobs import UnifiedSourceIndexJobRuntime
from src.unified_source_index_sources.knowledge import (
    KNOWLEDGE_SOURCE_ADAPTER_VERSION, KNOWLEDGE_SOURCE_EXACT_READER_BOUNDARY,
    KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE, KnowledgeSourceAdapter, KnowledgeSourceAdapterError, HarbourKnowledgeMetadataReference,
    HarbourKnowledgeIndexProjection, knowledge_source_capability_manifest, knowledge_source_registration,
    project_harbour_knowledge_index_records, project_harbour_knowledge_metadata,
)

_HARBOUR_SCHEMA=KNOWLEDGE_MIGRATION_SNAPSHOT_SCHEMA
_HARBOUR_OWNER="sha256:"+"a"*64
NOW=datetime(2026,10,1,12,0,tzinfo=timezone.utc)
OWNER="owner_"+"a"*32
AUTH="sha256:"+"a"*64

def _assert_error(error,code):
    assert isinstance(error,KnowledgeSourceAdapterError) and error.code==code

def _assert_ambient_error(callback,code,ambient):
    try: callback()
    except KnowledgeSourceAdapterError as error: _assert_error(error,code); return error
    else: pytest.fail("expected bounded adapter error")

def _fixture(tmp_path,payload=b"hello\nworld",media="text/plain",classification="private",*,subject=OWNER,retention=None,ceiling=Classification.SENSITIVE):
    issuer=AuthenticatedPrincipalIssuer(issuer_id="usi-test",key_id="test-key",secret=b"k"*32,clock=lambda:NOW)
    principal=issuer.issue(subject_id=subject,owner_id=OWNER,auth_context_digest=AUTH,ttl_seconds=3600)
    store=HarbourKnowledgeStore(tmp_path/"knowledge.sqlite3",trusted_issuers=(issuer,),clock=lambda:NOW)
    store.create_object(principal,owner_id=OWNER,knowledge_id="secret-source-id",knowledge_kind="note",payload_bytes=payload,payload_schema_id="harbour.note.v1",payload_media_type=media,classification=classification,idempotency_key="create-note-001",retention=retention)
    return issuer,principal,store,KnowledgeSourceAdapter(store=store,principal=principal,owner_id=OWNER,cursor_signing_key=b"k"*32,classification_ceiling=ceiling)

def _discover(adapter):
    cap=adapter.describe_capability()
    scope=AdapterScope(cap.owner_scope,cap.classification_ceiling)
    page=adapter.discover(scope,cursor="",limit=10,time_budget_ms=5000)
    return page.items[0].source,page

def test_harbour_adapter_uses_public_store_and_common_capability(tmp_path,monkeypatch):
    _,principal,store,adapter=_fixture(tmp_path)
    assert KNOWLEDGE_SOURCE_ADAPTER_VERSION=="v4"
    assert KNOWLEDGE_SOURCE_EXACT_READER_BOUNDARY=="harbour_knowledge_store.read_payload_at_generation"
    assert adapter.manifest.source_kind is SourceKind.OTHER
    assert adapter.manifest.content_policy is ContentPolicy.INLINE_LOCAL
    assert adapter.manifest.classification_ceiling is Classification.UNKNOWN
    assert knowledge_source_registration().manifest == adapter.manifest
    assert adapter.describe_capability().required_job_profile_ref==adapter.manifest.job_profile_ref
    assert adapter.describe_capability().classification_ceiling is Classification.SENSITIVE
    assert "secret-source-id" not in adapter.discover(AdapterScope(adapter.describe_capability().owner_scope,Classification.SENSITIVE),cursor="",limit=10,time_budget_ms=5000).items[0].source.canonical_ref
    assert not hasattr(adapter,"_conn") and not hasattr(adapter,"_connection")
    adapter_tree = ast.parse(inspect.getsource(KnowledgeSourceAdapter))
    store_calls = {
        node.func.attr
        for node in ast.walk(adapter_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Attribute)
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "self"
        and node.func.value.attr == "_store"
    }
    assert store_calls == {"capture_index_snapshot", "read_payload_at_generation"}
    store.close()


@pytest.mark.parametrize(
    ("raw_classification", "expected"),
    [
        ("public", Classification.PUBLIC),
        ("private", Classification.PRIVATE),
        ("sensitive", Classification.SENSITIVE),
        ("secret", Classification.SECRET),
    ],
)
def test_harbour_classification_mapping_is_exact(tmp_path, raw_classification, expected):
    _, _, store, adapter = _fixture(
        tmp_path,
        classification=raw_classification,
        ceiling=Classification.SECRET,
    )
    source, _ = _discover(adapter)
    assert source.classification is expected
    from src.unified_source_index_contract import RecordRef

    version = adapter.observe_version(RecordRef(RecordKind.SOURCE, source.source_id))
    assert version.classification is expected
    store.close()


def test_harbour_unknown_classification_is_never_downgraded(tmp_path):
    _, _, store, adapter = _fixture(
        tmp_path,
        classification="unrecognized-classification",
        ceiling=Classification.SECRET,
    )
    capability = adapter.describe_capability()
    page = adapter.discover(
        AdapterScope(capability.owner_scope, Classification.SECRET),
        cursor="",
        limit=10,
        time_budget_ms=5000,
    )
    assert page.items == ()
    store.close()

def test_harbour_text_extract_pages_and_exact_read_are_generation_fenced(tmp_path):
    _,principal,store,adapter=_fixture(tmp_path,("a"*270000).encode())
    source,_=_discover(adapter)
    version=adapter.observe_version(__import__("src.unified_source_index_contract",fromlist=["RecordRef"]).RecordRef(RecordKind.SOURCE,source.source_id))
    profile=ExtractionProfile(KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE,max_items=100,max_chars=1000000,time_budget_ms=5000)
    result=adapter.extract(version,profile)
    assert len(result.chunks)==2 and sum(len(c.content) for c in result.chunks)==270000
    cursor=""; seq=0; all_records=[]
    while True:
        page=adapter.extract_page(version,ExtractionProfile(KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE,1,262144,5000),cursor=cursor,sequence_start=seq)
        all_records.extend(page.records); seq+=len(page.records)
        if not page.next_cursor:
            assert page.final_record_count==2
            assert page.final_records_digest=="sha256:"+hashlib.sha256(b"\n".join(__import__("src.unified_source_index_contract",fromlist=["canonical_json"]).canonical_json(c.to_dict()).encode() for c in all_records)).hexdigest()
            break
        cursor=page.next_cursor
    chunk=all_records[0]
    evidence=chunk.evidence_ref()
    read=adapter.read_exact(ExactReadRequest(evidence,10),PolicyReadContext(version.owner_scope,Classification.SENSITIVE,True))
    assert read.content=="a"*10 and read.clipped
    store.close()

def test_harbour_adapter_rejects_cursor_tampering_and_caller_fabricated_unavailable(tmp_path,monkeypatch):
    _,principal,store,adapter=_fixture(tmp_path)
    store.create_object(principal,owner_id=OWNER,knowledge_id="second-secret-source-id",knowledge_kind="note",payload_bytes=b"second",payload_schema_id="harbour.note.v1",payload_media_type="text/plain",classification="private",idempotency_key="create-note-002")
    cap=adapter.describe_capability(); scope=AdapterScope(cap.owner_scope,cap.classification_ceiling)
    page=adapter.discover(scope,cursor="",limit=1,time_budget_ms=5000)
    assert page.next_cursor
    with pytest.raises(KnowledgeSourceAdapterError): adapter.discover(scope,cursor=page.next_cursor+"!!!!",limit=1,time_budget_ms=5000)
    from src.unified_source_index_contract import RecordRef

    version=adapter.observe_version(RecordRef(RecordKind.SOURCE,page.items[0].source.source_id))
    calls=[]
    monkeypatch.setattr(store,"_conn",lambda: calls.append(1))
    with pytest.raises(KnowledgeSourceAdapterError):
        adapter.extract_page(version,ExtractionProfile(KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE,1,100,5000),cursor="x"*(MAX_ADAPTER_CURSOR_CHARS+1),sequence_start=0)
    assert calls==[]
    invalid_profile = ExtractionProfile(KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE, 1, 100, 5000)
    object.__setattr__(invalid_profile, "max_chars", 1_000_001)
    with pytest.raises(KnowledgeSourceAdapterError):
        adapter.extract_page(version, invalid_profile, cursor="", sequence_start=0)
    with pytest.raises(KnowledgeSourceAdapterError):
        adapter.extract_page(version, ExtractionProfile(KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE,1,100,5000), cursor="", sequence_start=True)
    assert calls==[]
    monkeypatch.undo()
    with pytest.raises(KnowledgeSourceAdapterError):
        adapter.extract_page(version,ExtractionProfile(KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE,1,100,5000),cursor=[],sequence_start=0)
    ref=RecordRef(RecordKind.SOURCE,page.items[0].source.source_id)
    with pytest.raises(Exception): adapter.observe_unavailable(ref,UnavailableReason.DELETED)
    store.close()


def test_harbour_discovery_cursor_is_keyed_and_resumes_across_adapter_instances(tmp_path,monkeypatch):
    _, principal, store, adapter = _fixture(tmp_path)
    for index in range(2):
        store.create_object(
            principal,
            owner_id=OWNER,
            knowledge_id=f"additional-source-{index}",
            knowledge_kind="note",
            payload_bytes=b"page",
            payload_schema_id="harbour.note.v1",
            payload_media_type="text/plain",
            classification="private",
            idempotency_key=f"create-additional-{index}",
        )
    capability = adapter.describe_capability()
    scope = AdapterScope(capability.owner_scope, capability.classification_ceiling)
    first = adapter.discover(scope, cursor="", limit=1, time_budget_ms=5_000)
    assert first.next_cursor

    # Recreate an adapter with the deployment key to model a job pump restart.
    resumed_adapter = KnowledgeSourceAdapter(
        store=store,
        principal=principal,
        owner_id=OWNER,
        cursor_signing_key=b"k" * 32,
    )
    second = resumed_adapter.discover(
        scope, cursor=first.next_cursor, limit=10, time_budget_ms=5_000
    )
    assert len(second.items) == 2

    wrong_key_adapter = KnowledgeSourceAdapter(
        store=store,
        principal=principal,
        owner_id=OWNER,
        cursor_signing_key=b"w" * 32,
    )
    calls = []
    monkeypatch.setattr(
        store,
        "capture_index_snapshot",
        lambda *args, **kwargs: calls.append("capture"),
    )

    # Recreate the old public checksum over a canonical cursor with a forged,
    # valid-looking source ID. The adapter must reject it before Store access.
    encoded_body, _ = first.next_cursor.split(".", 1)
    raw = base64.urlsafe_b64decode(encoded_body + "=" * (-len(encoded_body) % 4))
    payload = json.loads(raw)
    payload["last"] = "usi_source_" + "f" * 64
    forged_raw = knowledge_module.canonical_json(payload).encode("utf-8")
    forged_body = base64.urlsafe_b64encode(forged_raw).decode("ascii").rstrip("=")
    public_checksum = hashlib.sha256(b"knowledge-v4\x00" + forged_raw).hexdigest()
    forged_cursor = f"{forged_body}.{public_checksum}"
    with pytest.raises(KnowledgeSourceAdapterError):
        adapter.discover(scope, cursor=forged_cursor, limit=10, time_budget_ms=5_000)
    with pytest.raises(KnowledgeSourceAdapterError):
        wrong_key_adapter.discover(
            scope, cursor=first.next_cursor, limit=10, time_budget_ms=5_000
        )
    assert calls == []
    store.close()


def test_harbour_cursor_key_and_mutated_enum_rejected_before_store(tmp_path, monkeypatch):
    _, principal, store, adapter = _fixture(tmp_path)
    calls = []
    monkeypatch.setattr(
        store,
        "capture_index_snapshot",
        lambda *args, **kwargs: calls.append("capture"),
    )
    with pytest.raises(KnowledgeSourceAdapterError):
        KnowledgeSourceAdapter(
            store=store,
            principal=principal,
            owner_id=OWNER,
            cursor_signing_key=bytearray(b"k" * 32),
        )
    scope = AdapterScope(adapter._owner_scope, Classification.SENSITIVE)
    original = object.__getattribute__(Classification.SENSITIVE, "_value_")
    object.__setattr__(Classification.SENSITIVE, "_value_", "forged-sensitive")
    try:
        with pytest.raises(KnowledgeSourceAdapterError):
            adapter.discover(scope, cursor="", limit=1, time_budget_ms=5_000)
        with pytest.raises(SourceAdapterError):
            PolicyReadContext(adapter._owner_scope, Classification.SENSITIVE, True)
        assert calls == []
    finally:
        object.__setattr__(Classification.SENSITIVE, "_value_", original)
    store.close()

def test_harbour_binary_and_malformed_json_have_no_text_chunks(tmp_path):
    _,_,store,binary=_fixture(tmp_path,b"\\x00\\xff","application/octet-stream")
    source,_=_discover(binary)
    from src.unified_source_index_contract import RecordRef
    version=binary.observe_version(RecordRef(RecordKind.SOURCE,source.source_id))
    out=binary.extract(version,ExtractionProfile(KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE,10,1000,5000))
    assert out.chunks==()
    store.close()
    badpath=tmp_path/"badcase"; badpath.mkdir()
    _,_,store,bad=_fixture(badpath,b"{bad","application/json")
    source,_=_discover(bad); version=bad.observe_version(RecordRef(RecordKind.SOURCE,source.source_id))
    with pytest.raises(Exception): bad.extract(version,ExtractionProfile(KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE,10,1000,5000))
    store.close()


@pytest.mark.parametrize(
    ("payload", "media", "provider_failure"),
    [
        (b"\xff", "text/plain", False),
        (b"{bad", "application/json", False),
        (b"secret provider detail", "text/plain", True),
    ],
)
def test_harbour_payload_errors_have_no_raw_exception_context(
    tmp_path, monkeypatch, payload, media, provider_failure
):
    _, _, store, adapter = _fixture(tmp_path, payload, media)
    source, _ = _discover(adapter)
    from src.unified_source_index_contract import RecordRef

    version = adapter.observe_version(RecordRef(RecordKind.SOURCE, source.source_id))
    if provider_failure:
        def fail_provider(*args, **kwargs):
            raise RuntimeError("secret provider response")

        monkeypatch.setattr(store, "read_payload_at_generation", fail_provider)
    with pytest.raises(KnowledgeSourceAdapterError) as caught:
        adapter.extract(
            version,
            ExtractionProfile(KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE, 10, 1000, 5000),
        )
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert "secret" not in str(caught.value)
    store.close()

def test_harbour_wrong_owner_fails_before_store_capture(tmp_path,monkeypatch):
    _,principal,store,_=_fixture(tmp_path)
    calls=[]
    monkeypatch.setattr(store,"_conn",lambda: calls.append(1))
    with pytest.raises(Exception): KnowledgeSourceAdapter(store=store,principal=principal,owner_id="owner_"+"b"*32,cursor_signing_key=b"k"*32)
    outsider=AuthenticatedPrincipalIssuer(issuer_id="other-issuer",key_id="other-key",secret=b"o"*32,clock=lambda:NOW)
    forged=outsider.issue(subject_id=OWNER,owner_id=OWNER,auth_context_digest=AUTH,ttl_seconds=3600)
    with pytest.raises(Exception): KnowledgeSourceAdapter(store=store,principal=forged,owner_id=OWNER,cursor_signing_key=b"k"*32)
    assert calls==[]
    store.close()


def test_harbour_owner_scope_is_stable_across_delegated_subjects(tmp_path):
    _, _, store, first = _fixture(tmp_path, subject="first-authorized-subject")
    second = KnowledgeSourceAdapter(
        store=store,
        principal=AuthenticatedPrincipalIssuer(
            issuer_id="usi-test", key_id="test-key", secret=b"k" * 32, clock=lambda: NOW
        ).issue(subject_id="second-authorized-subject", owner_id=OWNER, auth_context_digest=AUTH, ttl_seconds=3600),
        owner_id=OWNER,
        cursor_signing_key=b"k" * 32,
    )
    first_source, _ = _discover(first)
    second_source, _ = _discover(second)
    assert first_source.source_id == second_source.source_id
    assert first_source.owner_scope == second_source.owner_scope
    store.close()

def test_harbour_exact_read_rejects_forged_chunk_evidence(tmp_path):
    _,_,store,adapter=_fixture(tmp_path,b"bound evidence")
    source,_=_discover(adapter)
    from src.unified_source_index_contract import RecordRef, TextRangeLocator
    version=adapter.observe_version(RecordRef(RecordKind.SOURCE,source.source_id))
    profile=ExtractionProfile(KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE,10,1000,5000)
    chunk=adapter.extract(version,profile).chunks[0]
    evidence=chunk.evidence_ref()
    context=PolicyReadContext(version.owner_scope,Classification.SENSITIVE,True)
    assert adapter.read_exact(ExactReadRequest(evidence,100),context).content=="bound evidence"
    forged=replace(evidence,locator=TextRangeLocator(1,5))
    with pytest.raises(KnowledgeSourceAdapterError): adapter.read_exact(ExactReadRequest(forged,100),context)
    forged=replace(evidence,content_hash="sha256:"+"f"*64)
    with pytest.raises(KnowledgeSourceAdapterError): adapter.read_exact(ExactReadRequest(forged,100),context)
    forged_version_id="usi_version_"+"f"*64
    forged=replace(
        evidence,
        source_version_id=forged_version_id,
        policy_evidence=replace(evidence.policy_evidence,source_version_id=forged_version_id,evidence_id=""),
    )
    with pytest.raises(KnowledgeSourceAdapterError): adapter.read_exact(ExactReadRequest(forged,100),context)
    forged_chunk_id="usi_chunk_"+"f"*64
    forged=replace(
        evidence,
        record_id=forged_chunk_id,
        policy_evidence=replace(evidence.policy_evidence,parent_id=forged_chunk_id,evidence_id=""),
    )
    with pytest.raises(KnowledgeSourceAdapterError): adapter.read_exact(ExactReadRequest(forged,100),context)
    forged=replace(evidence,policy_evidence=replace(evidence.policy_evidence,classification=Classification.PUBLIC,evidence_id=""))
    with pytest.raises(KnowledgeSourceAdapterError): adapter.read_exact(ExactReadRequest(forged,100),context)
    with pytest.raises(KnowledgeSourceAdapterError): adapter.extract(version,ExtractionProfile("knowledge.other",10,1000,5000))
    store.close()


def test_harbour_old_evidence_fails_after_append(tmp_path):
    _,principal,store,adapter=_fixture(tmp_path,b"before")
    source,_=_discover(adapter)
    from src.unified_source_index_contract import RecordRef
    old=adapter.observe_version(RecordRef(RecordKind.SOURCE,source.source_id))
    old_chunk=adapter.extract(old,ExtractionProfile(KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE,10,1000,5000)).chunks[0]
    head=store.get_head(principal,owner_id=OWNER,knowledge_id="secret-source-id")
    store.append_revision(principal,owner_id=OWNER,knowledge_id="secret-source-id",payload_bytes=b"after",payload_schema_id="harbour.note.v1",payload_media_type="text/plain",classification="private",expected_head=head,idempotency_key="append-note-001")
    with pytest.raises(KnowledgeSourceAdapterError): adapter.read_exact(ExactReadRequest(old_chunk.evidence_ref(),100),PolicyReadContext(old.owner_scope,Classification.SENSITIVE,True))
    with pytest.raises(KnowledgeSourceAdapterError): adapter.extract(old,ExtractionProfile(KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE,10,1000,5000))
    store.close()


def test_harbour_old_evidence_fails_after_tombstone_restore_and_purge(tmp_path):
    retention = {
        "retention_class": "time_bound",
        "deletion_mode": "purge_eligible",
        "retain_until": "2026-08-28T00:00:00.000000Z",
        "grace_until": "2026-08-28T12:00:00.000000Z",
        "legal_hold": False,
    }
    _, principal, store, adapter = _fixture(tmp_path, b"lifecycle", retention=retention)
    from src.unified_source_index_contract import RecordRef

    source, _ = _discover(adapter)
    version = adapter.observe_version(RecordRef(RecordKind.SOURCE, source.source_id))
    chunk = adapter.extract(
        version, ExtractionProfile(KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE, 10, 1000, 5000)
    ).chunks[0]
    evidence = chunk.evidence_ref()
    context = PolicyReadContext(version.owner_scope, Classification.SENSITIVE, True)
    head = store.get_head(principal, owner_id=OWNER, knowledge_id="secret-source-id")

    def assert_stale():
        with pytest.raises(KnowledgeSourceAdapterError):
            adapter.read_exact(ExactReadRequest(evidence, 100), context)

    tombstone = store.tombstone(
        principal, owner_id=OWNER, knowledge_id="secret-source-id",
        expected_head=head, idempotency_key="tombstone-lifecycle-001",
    )
    assert_stale()
    restored = store.restore(
        principal, owner_id=OWNER, knowledge_id="secret-source-id",
        expected_head=tombstone.after_head, idempotency_key="restore-lifecycle-001",
    )
    assert_stale()
    tombstone_again = store.tombstone(
        principal, owner_id=OWNER, knowledge_id="secret-source-id",
        expected_head=restored.after_head, idempotency_key="tombstone-lifecycle-002",
    )
    assert_stale()
    pending = store.request_purge(
        principal, owner_id=OWNER, knowledge_id="secret-source-id",
        expected_head=tombstone_again.after_head, idempotency_key="purge-request-lifecycle-001",
    )
    assert_stale()
    store.complete_purge(
        principal, owner_id=OWNER, knowledge_id="secret-source-id",
        expected_head=pending.after_head, policy_receipt="sha256:" + "a" * 64,
        projection_deletion_receipts=("sha256:" + "b" * 64,),
        backup_deletion_receipts=("sha256:" + "c" * 64,),
        idempotency_key="purge-complete-lifecycle-001",
    )
    assert_stale()
    store.close()


def test_harbour_adapter_commits_through_real_paged_job_runtime(tmp_path):
    _, _, harbour, adapter = _fixture(tmp_path, b"job runtime payload")
    source, page = _discover(adapter)
    assert not page.next_cursor
    capability = adapter.describe_capability()
    assert capability.required_job_profile_ref == adapter.manifest.job_profile_ref
    assert capability.required_job_profile_ref == KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE

    index_store = InMemoryUnifiedSourceIndexStore()
    runtime = UnifiedSourceIndexJobRuntime(index_store)
    job = IndexJobRecord.create(
        job_kind=IndexJobKind.EXTRACTION,
        source_scope=SourceScope.create((source.policy_evidence(),)),
        request_ref="knowledge.integration.request",
        profile_ref=capability.required_job_profile_ref,
        max_items=10,
        time_budget_ms=30_000,
    )
    runtime.register(job)
    result = runtime.run_to_completion(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="knowledge.integration.worker",
        adapter=adapter,
        now="2026-10-01T12:00:00Z",
        max_steps=10,
    )
    assert result.terminal
    assert result.lease.job.status is JobStatus.COMPLETED
    from src.unified_source_index_contract import RecordRef

    observed_version = adapter.observe_version(RecordRef(RecordKind.SOURCE, source.source_id))
    with index_store.begin_read() as read:
        stored_source = read.get(RecordKind.SOURCE, source.source_id, owner_scope=source.owner_scope)
        stored_version = read.get(
            RecordKind.SOURCE_VERSION,
            observed_version.source_version_id,
            owner_scope=source.owner_scope,
        )
        stored_chunks = read.list_records(
            RecordKind.CHUNK, owner_scope=source.owner_scope, limit=10
        ).items
        assert isinstance(stored_source, StoredRecord)
        assert isinstance(stored_version, StoredRecord)
    chunk = next(item.record for item in stored_chunks if isinstance(item, StoredRecord))
    assert chunk.content == "job runtime payload"
    exact = adapter.read_exact(
        ExactReadRequest(chunk.evidence_ref(), 100),
        PolicyReadContext(source.owner_scope, Classification.SENSITIVE, True),
    )
    assert exact.content == "job runtime payload"
    harbour.close()


def test_harbour_pages_cover_store_max_payload_without_clipping(tmp_path):
    payload=b"z"*(8*1024*1024)
    _,_,store,adapter=_fixture(tmp_path,payload)
    source,_=_discover(adapter)
    from src.unified_source_index_contract import RecordRef
    version=adapter.observe_version(RecordRef(RecordKind.SOURCE,source.source_id))
    profile=ExtractionProfile(KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE,100,1_000_000,10_000)
    cursor=""; sequence=0; count=0; chars=0
    while True:
        page=adapter.extract_page(version,profile,cursor=cursor,sequence_start=sequence)
        assert page.records
        assert all(len(record.content or "")<=MAX_TEXT_CHARS for record in page.records)
        count+=len(page.records); sequence+=len(page.records)
        chars+=sum(len(record.content or "") for record in page.records)
        if not page.next_cursor:
            assert page.final_record_count==count
            assert chars==8*1024*1024
            break
        cursor=page.next_cursor
    store.close()


def _harbour_vector_digest(kind, value):
    encoded = json.dumps(
        {"schema_id": _HARBOUR_SCHEMA, "kind": kind, "value": value},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ).encode("ascii")
    return "sha256:" + hashlib.sha256(len(encoded).to_bytes(8, "big") + encoded).hexdigest()


def _harbour_vector_record(index=1, **overrides):
    # Deliberately synthetic opaque digests: conversion proves consistency,
    # not authenticated origin or any knowledge of unexported R/T values.
    metadata = {
        "knowledge_id_digest": f"sha256:{index:064x}",
        "revision_id_digest": "sha256:" + "1" * 64,
        "revision": 2, "generation": 3, "state": "active", "knowledge_kind": "note",
        "payload_sha256": "sha256:" + "2" * 64,
        "classification_digest": "sha256:" + "3" * 64,
        "retention_digest": "sha256:" + "4" * 64,
        "provenance_digest": "sha256:" + "5" * 64,
        "source_revisions_digest": "sha256:" + "6" * 64,
        "references_digest": "sha256:" + "7" * 64,
        "revision_metadata_digest": "sha256:" + "8" * 64,
        "head_metadata_digest": "sha256:" + "9" * 64,
    }
    metadata.update(overrides)
    return KnowledgeHeadMetadata(**metadata, record_digest=_harbour_vector_digest(
        "head_record", {"owner_ref_digest": _HARBOUR_OWNER, "metadata": metadata},
    ))


def _harbour_vector_snapshot(heads=None, **overrides):
    heads = (_harbour_vector_record(),) if heads is None else heads
    records = [{f.name: getattr(head, f.name) for f in fields(KnowledgeHeadMetadata)} for head in heads]
    boundary = _harbour_vector_digest("head_boundary", {
        "owner_ref_digest": _HARBOUR_OWNER,
        "record_digests": [record["record_digest"] for record in records],
    })
    body = {"schema_id": _HARBOUR_SCHEMA, "owner_ref_digest": _HARBOUR_OWNER,
            "captured_head_boundary": boundary, "heads": records}
    return replace(KnowledgeMigrationSnapshot(
        _HARBOUR_SCHEMA, _HARBOUR_OWNER, boundary, heads, _harbour_vector_digest("snapshot", body),
    ), **overrides)


def test_harbour_metadata_literal_golden_mapping_and_empty_snapshot():
    snapshot = _harbour_vector_snapshot()
    references = project_harbour_knowledge_metadata(snapshot)
    assert type(references) is tuple and len(references) == 1
    reference = references[0]
    assert type(reference) is HarbourKnowledgeMetadataReference
    assert {field.name: getattr(reference, field.name) for field in fields(reference)} == {
        "schema_id": "odysseus.harbour.knowledge_metadata_reference.v1",
        "snapshot_digest": snapshot.snapshot_digest,
        "owner_ref_digest": _HARBOUR_OWNER,
        "record_digest": snapshot.heads[0].record_digest,
        "knowledge_id_digest": "sha256:" + "0" * 63 + "1",
        "revision": 2, "generation": 3, "state": "active", "knowledge_kind": "note",
        "payload_sha256": "sha256:" + "2" * 64,
        "classification_digest": "sha256:" + "3" * 64,
        "retention_digest": "sha256:" + "4" * 64,
        "provenance_digest": "sha256:" + "5" * 64,
        "source_revisions_digest": "sha256:" + "6" * 64,
        "references_digest": "sha256:" + "7" * 64,
        "revision_metadata_digest": "sha256:" + "8" * 64,
        "head_metadata_digest": "sha256:" + "9" * 64,
    }
    assert references == project_harbour_knowledge_metadata(snapshot)
    assert project_harbour_knowledge_metadata(_harbour_vector_snapshot(())) == ()
    assert not hasattr(reference, "__dict__")
    with pytest.raises(FrozenInstanceError):
        reference.state = "purged"
    assert repr(reference) == "HarbourKnowledgeMetadataReference(<metadata-only>)"


def test_harbour_index_projection_matches_adapter_canonical_parents(tmp_path):
    issuer, principal, store, adapter = _fixture(tmp_path)
    snapshot = store.capture_index_snapshot(principal, owner_id=OWNER)
    record, = snapshot.records
    projection = project_harbour_knowledge_index_records(
        adapter.describe_capability().owner_scope, record,
    )
    expected_source, expected_version, _ = adapter._version(record)
    assert type(projection) is HarbourKnowledgeIndexProjection
    assert projection.source == expected_source
    assert projection.source_version == expected_version
    generation_changed = project_harbour_knowledge_index_records(
        adapter.describe_capability().owner_scope,
        replace(record, generation=record.generation + 1),
    )
    revision_changed = project_harbour_knowledge_index_records(
        adapter.describe_capability().owner_scope,
        replace(record, revision_id="query-note-revision-next"),
    )
    payload_changed = project_harbour_knowledge_index_records(
        adapter.describe_capability().owner_scope,
        replace(record, payload_sha256="sha256:" + "f" * 64),
    )
    assert generation_changed.source_version.source_version_id != projection.source_version.source_version_id
    assert revision_changed.source_version.source_version_id != projection.source_version.source_version_id
    assert payload_changed.source_version.source_version_id != projection.source_version.source_version_id
    assert not hasattr(projection, "__dict__")
    with pytest.raises(FrozenInstanceError):
        projection.source = expected_source


@pytest.mark.parametrize("field,value", [
    ("schema_id", "foreign"), ("schema_id", "x" * 129),
    ("owner_ref_digest", "sha256:" + "b" * 64),
    ("captured_head_boundary", "sha256:" + "b" * 64),
    ("snapshot_digest", "sha256:" + "b" * 64),
    ("snapshot_digest", "sha256:" + "A" * 64),
    ("snapshot_digest", "sha256:" + "a" * 64 + "\n"),
    ("snapshot_digest", "a" * 1_000_000), ("owner_ref_digest", None),
], ids=["foreign-schema", "schema-budget", "owner-mismatch", "boundary-mismatch",
        "snapshot-mismatch", "uppercase-digest", "trailing-newline", "digest-budget", "missing-owner"])
def test_harbour_metadata_snapshot_tampering_is_bounded(field, value):
    snapshot = replace(_harbour_vector_snapshot(), **{field: value})
    _assert_ambient_error(lambda: project_harbour_knowledge_metadata(snapshot),
                          "invalid_snapshot", "private ambient context")


@pytest.mark.parametrize("field", [field.name for field in fields(KnowledgeHeadMetadata)])
def test_harbour_metadata_every_record_field_is_bound(field):
    head = _harbour_vector_record()
    value = {"revision": 1, "generation": 4, "state": "purged", "knowledge_kind": "document"}.get(
        field, "sha256:" + "f" * 64,
    )
    # Reseal the outer envelopes only. Any metadata change must still fail
    # against the unchanged record digest; changing that digest also fails.
    snapshot = _harbour_vector_snapshot((replace(head, **{field: value}),))
    with pytest.raises(KnowledgeSourceAdapterError) as caught:
        project_harbour_knowledge_metadata(snapshot)
    _assert_error(caught.value, "invalid_snapshot")


@pytest.mark.parametrize("field,value", [
    ("revision", True), ("revision", 0), ("revision", -1), ("revision", 2**63),
    ("revision", 1.0), ("generation", True), ("generation", 0),
    ("generation", 2**63), ("generation", 1),
    ("state", "unknown"), ("state", "é"), ("knowledge_kind", "unknown"),
    ("state", "x" * 129), ("payload_sha256", "sha256:" + "A" * 64),
])
def test_harbour_metadata_rejects_self_consistent_invalid_scalars(field, value):
    snapshot = _harbour_vector_snapshot((_harbour_vector_record(**{field: value}),))
    with pytest.raises(KnowledgeSourceAdapterError) as caught:
        project_harbour_knowledge_metadata(snapshot)
    _assert_error(caught.value, "invalid_snapshot")


def test_harbour_metadata_order_duplicates_and_limits(monkeypatch):
    first, second = _harbour_vector_record(1), _harbour_vector_record(2)
    for heads in ((second, first), (first, first), (first, replace(first, revision=3))):
        with pytest.raises(KnowledgeSourceAdapterError) as caught:
            project_harbour_knowledge_metadata(_harbour_vector_snapshot(heads))
        _assert_error(caught.value, "invalid_snapshot")
    references = project_harbour_knowledge_metadata(_harbour_vector_snapshot((first, second)))
    assert tuple(r.knowledge_id_digest for r in references) == (first.knowledge_id_digest, second.knowledge_id_digest)
    upper = _harbour_vector_record(revision=2**63 - 1, generation=2**63 - 1)
    assert project_harbour_knowledge_metadata(_harbour_vector_snapshot((upper,)))[0].revision == 2**63 - 1
    for state in ("active", "tombstoned", "purge_pending", "purged"):
        assert project_harbour_knowledge_metadata(_harbour_vector_snapshot((
            _harbour_vector_record(state=state),
        )))[0].state == state
    for kind in ("personal_memory", "document", "note", "imported_record", "derived_abstraction", "causal_claim"):
        assert project_harbour_knowledge_metadata(_harbour_vector_snapshot((
            _harbour_vector_record(knowledge_kind=kind),
        )))[0].knowledge_kind == kind
    snapshot = _harbour_vector_snapshot()
    object.__setattr__(snapshot, "heads", (first,) * 10_001)
    with pytest.raises(KnowledgeSourceAdapterError) as caught:
        project_harbour_knowledge_metadata(snapshot)
    _assert_error(caught.value, "invalid_snapshot")
    monkeypatch.setattr(knowledge_module, "_HARBOUR_MAX_METADATA_BYTES", 1)
    with pytest.raises(KnowledgeSourceAdapterError) as caught:
        project_harbour_knowledge_metadata(_harbour_vector_snapshot())
    _assert_error(caught.value, "invalid_snapshot")


def test_harbour_metadata_accepts_ten_thousand_ordered_heads():
    heads = tuple(_harbour_vector_record(index) for index in range(10_000))
    references = project_harbour_knowledge_metadata(_harbour_vector_snapshot(heads))
    assert len(references) == 10_000
    assert references[-1].knowledge_id_digest == heads[-1].knowledge_id_digest


def test_harbour_metadata_exact_types_reject_hostile_protocols_without_calls():
    class Hostile:
        @property
        def __class__(self):
            raise AssertionError("private __class__ getter")

        def __getattr__(self, name):
            raise AssertionError("private getter")

        def __iter__(self):
            raise AssertionError("private iterator")

        def __str__(self):
            raise AssertionError("private string")

    class SnapshotSubclass(KnowledgeMigrationSnapshot):
        def __getattribute__(self, name):
            raise AssertionError("private snapshot getter")

    class HeadSubclass(KnowledgeHeadMetadata):
        def __getattribute__(self, name):
            raise AssertionError("private head getter")

    class TupleSubclass(tuple):
        def __iter__(self):
            raise AssertionError("private tuple iterator")

    class StrSubclass(str):
        def encode(self, *args, **kwargs):
            raise AssertionError("private encoding")

    class IntSubclass(int):
        def __lt__(self, other):
            raise AssertionError("private comparison")

    valid = _harbour_vector_snapshot()
    inputs = [Hostile(), {}, iter(()), object.__new__(SnapshotSubclass), object.__new__(KnowledgeMigrationSnapshot, KNOWLEDGE_MIGRATION_SNAPSHOT_SCHEMA)]
    for heads in ([], Hostile(), iter(valid.heads), TupleSubclass(valid.heads),
                  (Hostile(),), (object.__new__(HeadSubclass),), (object.__new__(KnowledgeHeadMetadata),)):
        inputs.append(replace(valid, heads=heads))
    for field, value in (("payload_sha256", Hostile()), ("payload_sha256", StrSubclass("sha256:" + "2" * 64)),
                         ("revision", IntSubclass(2)), ("state", Hostile())):
        inputs.append(replace(valid, heads=(replace(valid.heads[0], **{field: value}),)))
    for snapshot in inputs:
        _assert_ambient_error(lambda: project_harbour_knowledge_metadata(snapshot),
                              "invalid_snapshot", "private ambient failure")


def test_harbour_metadata_detaches_before_hashing_and_retains_no_input_alias(monkeypatch):
    snapshot = _harbour_vector_snapshot()
    expected = project_harbour_knowledge_metadata(snapshot)
    original_digest = knowledge_module._harbour_metadata_digest

    def mutate_input_then_hash(kind, value):
        object.__setattr__(snapshot.heads[0], "state", "private forged state")
        object.__setattr__(snapshot, "owner_ref_digest", "private forged owner")
        return original_digest(kind, value)

    monkeypatch.setattr(knowledge_module, "_harbour_metadata_digest", mutate_input_then_hash)
    assert project_harbour_knowledge_metadata(snapshot) == expected
    assert expected[0].state == "active" and expected[0].owner_ref_digest == _HARBOUR_OWNER
    with pytest.raises(KnowledgeSourceAdapterError) as caught:
        project_harbour_knowledge_metadata(snapshot)
    _assert_error(caught.value, "invalid_snapshot")
    object.__setattr__(expected[0], "state", "private malformed reference")
    assert repr(expected[0]) == "HarbourKnowledgeMetadataReference(<metadata-only>)"


def test_harbour_metadata_hash_failures_are_fresh_and_context_free(monkeypatch):
    def fail_hash(*args):
        raise RuntimeError("private hash failure")

    monkeypatch.setattr(knowledge_module, "_harbour_metadata_digest", fail_hash)
    snapshot = _harbour_vector_snapshot()
    errors = [_assert_ambient_error(lambda: project_harbour_knowledge_metadata(snapshot),
                                   "invalid_snapshot", "private caller context") for _ in range(2)]
    assert errors[0] is not errors[1]


def test_harbour_metadata_conversion_has_no_native_or_io_authority(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("conversion crossed authority boundary")

    for name in ("KnowledgeSourceAdapter", "SourceRecord", "SourceVersionRecord",
                 "ChunkRecord", "knowledge_source_registration", "knowledge_source_capability_manifest", "content_hash"):
        monkeypatch.setattr(knowledge_module, name, forbidden)
    snapshot = _harbour_vector_snapshot()
    reference, = project_harbour_knowledge_metadata(snapshot)
    assert reference.payload_sha256 == snapshot.heads[0].payload_sha256
    names = {field.name for field in fields(reference)}
    assert not names.intersection({"content", "payload", "content_hash", "source_id", "source_version_id",
                                   "owner_id", "locator", "binding", "timestamp", "revision_id_digest"})
    tree = ast.parse(inspect.getsource(knowledge_module))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and
                 (node.name.startswith("_harbour_metadata_") or node.name == "project_harbour_knowledge_metadata")]
    forbidden_calls = {"open", "HarbourKnowledgeStore", "KnowledgeSourceAdapter",
                       "SourceRecord", "SourceVersionRecord", "ChunkRecord", "content_hash",
                       "knowledge_source_registration", "knowledge_source_capability_manifest"}
    forbidden_attributes = {"capture", "capture_migration_snapshot", "connect", "execute", "read", "write", "now", "random"}
    for function in functions:
        for node in ast.walk(function):
            if isinstance(node, ast.Call):
                assert not (isinstance(node.func, ast.Name) and node.func.id in forbidden_calls)
                assert not (isinstance(node.func, ast.Attribute) and node.func.attr in forbidden_attributes)
