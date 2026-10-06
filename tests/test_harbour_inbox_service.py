from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import threading
import time
from types import SimpleNamespace

import bcrypt
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.auth import AuthManager
from core.database import ApiToken, Base, ClarificationEvent, ClarificationRun, Document, Session
from src.auth_helpers import SessionPrincipal
from src.clarification_store import ClarificationStore, ClarificationStoreError
from src.harbour_inbox_service import (
    HARBOUR_INBOX_TOOL_NAMES,
    HarbourInboxError,
    HarbourInboxService,
    MCP_HARBOUR_INBOX_SCOPE,
    build_harbour_inbox_tool_contracts,
    DocumentInboxItem,
    DocumentInboxSnapshot,
    _validate_arguments,
    _capture_document_inbox_snapshot,
    _document_row_bytes,
    _frame,
    _typed_payload,
    _unified_digest,
    _unified_payload,
)
import src.harbour_inbox_service as harbour_inbox_service
from src.mcp_authenticated_dispatch import (
    McpAuthenticatedIdentity,
    McpBearerProof,
    McpDispatchFailure,
    capture_mcp_bearer_proof,
    dispatch_http_payload,
)


def _request():
    return {
        "schema": "odysseus.clarification_request.v2",
        "scope": "project",
        "intent_summary": "Choose the bounded next action.",
        "questions": [],
        "batch": {"label": "Decision", "index": 1, "total": 1, "max_visible_questions": 3},
        "defaults_visible": False,
    }


def _fixture(tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("admin", "password", is_admin=True)
    subject = manager.subject_id_for_username("admin")
    raw = b"ody_inbox_private_fixture_value"
    token_id = "inbox001"
    db = factory()
    try:
        db.add(ApiToken(
            id=token_id,
            owner="admin",
            name="inbox",
            token_hash=bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8"),
            token_prefix="ody_inbo",
            scopes=MCP_HARBOUR_INBOX_SCOPE,
            owner_subject_id=subject,
            is_active=True,
        ))
        db.commit()
    finally:
        db.close()
    principal = SessionPrincipal("admin", subject, "browser_session")
    created = ClarificationStore(session_factory=factory).create_run(
        principal=principal,
        session_id="private-browser-session",
        request=_request(),
        clarification_id="clar-inbox-001",
        project_slug="private-project",
    )
    identity = McpAuthenticatedIdentity(token_id, "admin", subject, 1, 0, MCP_HARBOUR_INBOX_SCOPE)
    proof = capture_mcp_bearer_proof(SimpleNamespace(
        state=SimpleNamespace(api_token=True),
        headers={"authorization": "Bearer " + raw.decode("utf-8")},
    ))
    service = HarbourInboxService(
        auth_manager=manager,
        bearer_proof=proof,
        session_factory=factory,
    )
    return engine, factory, manager, service, identity, principal, created.run


def _action(service, identity, name, version, key="action-one"):
    return service.dispatch(name, {
        "clarification_id": "clar-inbox-001",
        "expected_version": version,
        "idempotency_key": key,
    }, identity)


def _recovery(service, identity, name, version, key):
    return service.recover_action(name, {
        "clarification_id": "clar-inbox-001",
        "expected_version": version,
        "idempotency_key": key,
    }, identity)


def _db_state(factory):
    db = factory()
    try:
        run = db.query(ClarificationRun).filter(
            ClarificationRun.id == "clar-inbox-001",
        ).one()
        return int(run.version), db.query(ClarificationEvent).filter(
            ClarificationEvent.clarification_id == "clar-inbox-001",
        ).count()
    finally:
        db.close()


def _rename_fixture_owner(factory, manager, identity, old_alias="admin", new_alias="renamed"):
    assert manager.rename_user(old_alias, new_alias, old_alias) is True
    db = factory()
    try:
        db.query(ApiToken).filter(ApiToken.id == identity.token_id).update({"owner": new_alias})
        db.query(ClarificationRun).filter(
            ClarificationRun.id == "clar-inbox-001",
        ).update({"owner": new_alias})
        db.query(ClarificationEvent).filter(
            ClarificationEvent.clarification_id == "clar-inbox-001",
        ).update({"owner": new_alias})
        db.commit()
    finally:
        db.close()
    return McpAuthenticatedIdentity(
        identity.token_id, new_alias, identity.owner_subject_id,
        identity.cache_generation, identity.security_action_revision, identity.scope,
    ), SessionPrincipal(new_alias, identity.owner_subject_id, "mcp_subject")


def test_unified_receipts_discovers_a_real_current_action_and_exact_lookup(tmp_path):
    _engine, _factory, _manager, service, identity, _principal, created = _fixture(tmp_path)
    _action(service, identity, "harbour_inbox_pause", created["version"], "receipt-view-pause")

    enriched = service.dispatch("harbour_inbox_list", {
        "view": "unified_receipts", "limit": 10,
    }, identity)

    assert enriched.authority_finalized is False
    assert enriched.payload["schema"] == "odysseus.harbour_inbox_unified_list.v2"
    row = next(item for item in enriched.payload["items"] if item["source"] == "clarification")
    receipt_ref = row["receipt_ref"]
    assert set(receipt_ref) == {
        "source", "clarification_id", "event_version", "expected_current_version", "receipt_handle",
    }
    exact = service.dispatch("harbour_inbox_list", {
        "view": "receipt", "receipt_ref": receipt_ref,
    }, identity)
    assert exact.authority_finalized is False
    assert exact.payload["source"] == "clarification"
    assert exact.payload["operation"] == "pause"
    assert exact.payload["authorizes_mutation"] is False


def test_unified_receipts_keeps_untouched_run_receipt_ref_null(tmp_path):
    _engine, _factory, _manager, service, identity, _principal, _created = _fixture(tmp_path)

    enriched = service.dispatch("harbour_inbox_list", {
        "view": "unified_receipts", "limit": 10,
    }, identity)

    row = next(item for item in enriched.payload["items"] if item["source"] == "clarification")
    assert row["receipt_ref"] is None


def _planning_capture(identity, *, count=1, clipped=False):
    """Build only canonical Planning DTOs; no mapping stands in for a capture."""
    from src.mcp_authenticated_dispatch import _inbox_canonical_digest
    from src.harbour_inbox_service import PlanningInboxItem, PlanningInboxSnapshot

    batch = {"accepted": "batch"}
    # The detached head now carries the provider's monotonic generation.  It
    # belongs to the capture binding only: the public Planning Inbox DTO stays
    # deliberately limited to current/approved revisions.
    heads = [{"roadmap_id": "roadmap-" + str(index), "actual_generation": index + 1}
             for index in range(count)]
    source = _inbox_canonical_digest("source", {
        "batch": batch, "heads": heads, "owner": identity.owner_subject_id,
    })
    items = tuple(
        PlanningInboxItem(
            index + 2,
            1,
            _inbox_canonical_digest("item", {
                "approved_revision_id": "approved-1", "current_revision_id": f"current-{index + 2}",
                "owner": identity.owner_subject_id, "roadmap_id": f"roadmap-{index}",
                "records": [f"durable:record-{index}"], "source_binding": source,
            }),
        )
        for index in range(count)
    )
    binding = _inbox_canonical_digest("binding", {
        "batch": batch, "heads": heads,
        "items": [{"approved_revision": item.approved_revision, "current_revision": item.current_revision,
                   "record": item.record} for item in items],
        "owner": identity.owner_subject_id, "source_binding": source,
    })
    return PlanningInboxSnapshot(
        _inbox_canonical_digest("owner", {"subject": identity.owner_subject_id}),
        _inbox_canonical_digest("batch", batch), _inbox_canonical_digest("heads", heads),
        items, clipped, binding,
    )


def _clarification_capture(identity, *, count=1, clipped=False):
    from src.clarification_store import ClarificationInboxItem, ClarificationInboxSnapshot, _inbox_digest

    owner = _inbox_digest("owner", identity.owner_subject_id)
    rows = [
        {"id": f"clar-inbox-{index:03d}", "status": "clarifying", "version": 1,
         "ready": False, "updated": "2026-10-04T12:00:00Z"}
        for index in range(count)
    ]
    boundary = _inbox_digest("boundary", {"owner": owner, "rows": rows})
    items = tuple(
        ClarificationInboxItem(
            row["id"], row["status"], row["version"], row["ready"], row["updated"],
            _inbox_digest("item", {"owner": owner, **row}),
        )
        for row in rows
    )
    binding = _inbox_digest("snapshot", {
        "owner": owner, "boundary": boundary,
        "items": [item.binding_digest for item in items], "clipped": clipped,
    })
    return ClarificationInboxSnapshot(owner, boundary, items, clipped, binding)


def _knowledge_capture(identity, *, count=1, clipped=False, boundary="head-a"):
    """Use the frozen Knowledge types and the source's framed digest helper."""
    import src.harbour_knowledge_active_runtime as knowledge_runtime
    from src.harbour_knowledge_store import KnowledgeInboxSignal
    from src.unified_source_index_sources.knowledge import knowledge_source_capability_manifest

    digest = knowledge_runtime._inbox_digest
    selected_generation = knowledge_source_capability_manifest().generation_ref
    actual_generation = 7
    owner = digest("owner", {"subject": identity.owner_subject_id})
    selection = digest("selection", {"generation": selected_generation, "owner": owner, "selection": digest("selected", {"v": 1})})
    signals = tuple(
        KnowledgeInboxSignal(
            digest("signal", {"index": index}), "purge_requested", "note", "purge_pending", actual_generation, index + 1,
            digest("revision-ref", {"index": index}), digest("head-metadata", {"index": index}),
            digest("revision-metadata", {"index": index}), digest("event", {"index": index}),
            digest("receipt", {"index": index}), "2026-10-04T12:00:00.000000Z",
            digest("signal-binding", {"index": index, "owner": owner}),
        )
        for index in range(count)
    )
    head = digest("head", {"value": boundary})
    snapshot = digest("snapshot", {"head": head, "signals": [item.binding_digest for item in signals]})
    binding = digest("binding", {"head_boundary": head, "selection": selection, "snapshot": snapshot})
    return knowledge_runtime.KnowledgeInboxSnapshot(
        owner, selection, digest("generation", {"generation": selected_generation}), snapshot, head, signals, clipped, binding,
    )


def test_contract_is_exact_and_list_action_replay_are_closed(tmp_path):
    _engine, factory, _manager, service, identity, principal, created = _fixture(tmp_path)
    contracts = build_harbour_inbox_tool_contracts()
    assert tuple(tool["name"] for tool in contracts) == HARBOUR_INBOX_TOOL_NAMES
    assert all(
        (all(branch["additionalProperties"] is False for branch in tool["inputSchema"]["oneOf"])
        if tool["name"] == "harbour_inbox_list" else tool["inputSchema"]["additionalProperties"] is False
        )
        for tool in contracts
    )

    listed = service.dispatch("harbour_inbox_list", {"limit": 10}, identity)
    assert listed.authority_finalized is True
    assert listed.payload["count"] == 1
    item = listed.payload["items"][0]
    assert item["owner"] == "self" and item["authority"] == "mcp_subject"
    forbidden = {"session_id", "project_slug", "creator_subject_id", "answers", "request", "summary", "event_id", "payload"}
    assert forbidden.isdisjoint(item)

    first = _action(service, identity, "harbour_inbox_pause", created["version"])
    replay = _action(service, identity, "harbour_inbox_pause", created["version"])
    assert first.payload == {**replay.payload, "idempotent_replay": False}
    assert replay.payload["idempotent_replay"] is True
    assert first.payload["item"]["status"] == "paused"
    assert forbidden.isdisjoint(first.payload["item"])
    with pytest.raises(HarbourInboxError) as stale:
        _action(service, identity, "harbour_inbox_reopen", created["version"], "stale-version")
    assert stale.value.code == "version_conflict"
    browser = ClarificationStore(session_factory=factory).read_run(
        principal=principal, clarification_id="clar-inbox-001"
    )
    assert browser["version"] == created["version"] + 1 and browser["status"] == "paused"


def test_typed_list_contract_accepts_only_the_closed_v3_argument_shapes():
    inbox = next(tool for tool in build_harbour_inbox_tool_contracts() if tool["name"] == "harbour_inbox_list")
    typed = [branch for branch in inbox["inputSchema"]["oneOf"] if branch["properties"].get("view", {}).get("const") == "typed"]
    assert len(typed) == 1 and typed[0]["additionalProperties"] is False
    assert _validate_arguments("harbour_inbox_list", {"view": "typed"}) == {"view": "typed", "limit": 25}
    assert _validate_arguments("harbour_inbox_list", {"view": "typed", "limit": 100}) == {"view": "typed", "limit": 100}
    for arguments in ({"view": "typed", "limit": True}, {"view": "typed", "limit": 101}, {"view": "typed", "extra": 1}):
        with pytest.raises(HarbourInboxError) as error:
            _validate_arguments("harbour_inbox_list", arguments)
        assert error.value.code == "invalid_params"


def test_typed_payload_has_exact_v3_shapes_and_keeps_document_ids_private(tmp_path):
    _engine, factory, _manager, _service, identity, _principal, _created = _fixture(tmp_path)
    subject = identity.owner_subject_id
    db = factory()
    try:
        db.add(Session(id="private-session", name="private", endpoint_url="http://example.invalid", model="test", owner="admin"))
        db.add_all([
            Document(id="typed-doc-active-001", creator_subject_id=subject, session_id="private-session",
                     version_count=3, is_active=False, archived=False, tidy_verdict=None,
                     source_kind="working_copy", source_ref_hash="sha256:" + "a" * 64),
            Document(id="typed-doc-archive-01", creator_subject_id=subject, session_id="private-session",
                     version_count=1, is_active=True, archived=True, tidy_verdict=None,
                     source_kind="working_copy", source_ref_hash="sha256:" + "b" * 64),
            Document(id="typed-doc-foreign-01", creator_subject_id="owner_" + "b" * 32, session_id="private-session",
                     version_count=1, is_active=True, archived=False, tidy_verdict=None,
                     source_kind="working_copy", source_ref_hash="sha256:" + "c" * 64),
            Document(id="typed-doc-null-session", creator_subject_id=subject, session_id=None,
                     version_count=1, is_active=True, archived=False, tidy_verdict=None,
                     source_kind="working_copy", source_ref_hash="sha256:" + "d" * 64),
        ])
        db.commit()
    finally:
        db.close()
    document = _capture_document_inbox_snapshot(factory, subject, 25)
    payload = _typed_payload({
        "clarification": _clarification_capture(identity, count=1),
        "planning": _planning_capture(identity, count=1),
        "knowledge": _knowledge_capture(identity, count=1),
        "document": document,
    }, 25)
    assert set(payload) == {"schema", "items", "count", "sources", "complete", "clipped", "authority", "freshness", "cross_source_atomic"}
    assert payload["schema"] == "odysseus.harbour_inbox_unified_list.v3"
    assert [(row["source"], row["status"]) for row in payload["sources"]] == [
        ("clarification", "captured_revalidated"), ("planning", "captured_revalidated"),
        ("knowledge", "captured_revalidated"), ("document", "captured_revalidated"),
    ]
    active_descriptor = next(item for item in document.items if item.document_id == "typed-doc-active-001")
    row = next(
        item for item in payload["items"]
        if item["source"] == "document" and item["item_handle"] == active_descriptor.item_handle
    )
    assert set(row) == {"item_handle", "source", "owner", "signal_type", "status", "required_action", "due_state", "authority", "binding_digest", "receipt_ref", "redaction_flags", "current_revision"}
    assert row["status"] == "available" and row["current_revision"] == 3
    assert row["authority"] == {"access": "read", "owner_subsystem": "document_workbench", "mutation_supported": False}
    assert row["redaction_flags"] == {"content": True, "identity": True, "events": True}
    rendered = json.dumps(payload, sort_keys=True)
    assert "typed-doc-active-001" not in rendered and "private-session" not in rendered and subject not in rendered
    assert document.whole_count == 3 and document.eligible_count == 2 and document.clipped is False
    assert {item.document_id for item in document.items} == {"typed-doc-active-001", "typed-doc-null-session"}
    assert {item.current_revision for item in document.items} == {1, 3}
    public_documents = [item for item in payload["items"] if item["source"] == "document"]
    assert len(public_documents) == 2
    assert {item["current_revision"] for item in public_documents} == {1, 3}
    assert all(len(item.canonical_row) == 11 and item.canonical_row[1] == subject for item in document.items)
    assert "typed-doc-null-session" not in rendered
    knowledge = next(item for item in payload["items"] if item["source"] == "knowledge")
    assert set(knowledge["receipt_ref"]) == {"source", "item_handle", "binding_digest", "receipt_handle"}
    assert knowledge["receipt_ref"]["source"] == "knowledge"
    db = factory()
    try:
        doc = db.get(Document, "typed-doc-active-001")
        doc.archived = True
        db.commit()
    finally:
        db.close()
    archived = _capture_document_inbox_snapshot(factory, subject, 25)
    assert archived.eligible_count == 1 and archived.binding_digest != document.binding_digest
    db = factory()
    try:
        db.get(Document, "typed-doc-active-001").archived = False
        db.commit()
    finally:
        db.close()
    restored = _capture_document_inbox_snapshot(factory, subject, 25)
    assert restored.eligible_count == 2 and restored.items and restored.binding_digest != archived.binding_digest
    db = factory()
    try:
        db.delete(db.get(Session, "private-session"))
        db.commit()
    finally:
        db.close()
    after_session_deletion = _capture_document_inbox_snapshot(factory, subject, 25)
    assert {item.document_id for item in after_session_deletion.items} == {
        "typed-doc-active-001", "typed-doc-null-session",
    }
    assert next(item for item in after_session_deletion.items if item.document_id == "typed-doc-active-001").canonical_row[2] is None


@pytest.mark.parametrize("bad_row", [
    (),
    ("short",) * 10,
    ("long",) * 12,
    (1, "owner_" + "a" * 32, "s", 1, True, False, None, "working_copy", "sha256:" + "a" * 64, None, None),
    ("d", "owner_" + "a" * 32, "s", 0, True, False, None, "working_copy", "sha256:" + "a" * 64, None, None),
    ("d", "owner_" + "a" * 32, "s", 1, True, "false", None, "working_copy", "sha256:" + "a" * 64, None, None),
    ("d", "owner_" + "a" * 32, "s", 1, True, False, None, "working_copy", "sha256:" + "z" * 64, None, None),
])
def test_typed_document_row_rejects_malformed_closed_eleven_column_rows(bad_row):
    with pytest.raises(HarbourInboxError) as error:
        _document_row_bytes(bad_row, "owner_" + "a" * 32)
    assert error.value.code == "inbox_failed"


def test_typed_payload_enforces_global_300_and_zero_item_byte_budget(tmp_path):
    _engine, _factory, _manager, _service, identity, _principal, _created = _fixture(tmp_path)
    document = DocumentInboxSnapshot(0, 0, "sha256:" + "d" * 64, (), False)
    payload = _typed_payload({
        "clarification": _clarification_capture(identity, count=100),
        "planning": _planning_capture(identity, count=100),
        "knowledge": _knowledge_capture(identity, count=100),
        "document": document,
    }, 100)
    # The producer cap is applied before the closed v3 envelope's independent
    # byte cap.  The byte cap may remove the deterministic suffix, never alter
    # the source ordering or produce an over-budget response.
    assert payload["count"] < 300 and payload["clipped"] is True and payload["complete"] is False
    assert [row["source"] for row in payload["sources"]] == ["clarification", "planning", "knowledge", "document"]
    rank = {"clarification": 0, "planning": 1, "knowledge": 2, "document": 3}
    assert [(rank[row["source"]], row["item_handle"]) for row in payload["items"]] == sorted(
        (rank[row["source"]], row["item_handle"]) for row in payload["items"]
    )
    assert len(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")) <= 32_768
    # The oversized binding, zero-envelope branch is defensive: no native
    # source can create it because the source capture is bounded and typed.
    with pytest.raises(HarbourInboxError) as error:
        _typed_payload({
            "clarification": _clarification_capture(identity, count=0),
            "planning": _planning_capture(identity, count=0),
            "knowledge": _knowledge_capture(identity, count=0),
            "document": DocumentInboxSnapshot(0, 0, "sha256:" + "f" * 40000, (), False),
        }, 1)
    assert error.value.code == "budget_exceeded"


@pytest.mark.parametrize("case", ["archive", "unarchive", "beyond_prefix", "planning_failure", "subject_drift"])
def test_typed_service_revalidates_every_mandatory_source_and_denies_drift(tmp_path, case):
    _engine, factory, manager, service, identity, _principal, _created = _fixture(tmp_path)
    subject = identity.owner_subject_id
    db = factory()
    try:
        db.add(Session(id="typed-revalidate-session", name="typed", endpoint_url="http://example.invalid", model="test", owner="admin"))
        db.add(Document(id="typed-revalidate-doc", creator_subject_id=subject, session_id="typed-revalidate-session", version_count=1, is_active=False, archived=False, source_kind="working_copy", source_ref_hash="sha256:" + "9" * 64))
        db.add(Document(id="typed-revalidate-archived", creator_subject_id=subject, session_id="typed-revalidate-session", version_count=1, is_active=True, archived=True, source_kind="working_copy", source_ref_hash="sha256:" + "8" * 64))
        db.add(Document(id="typed-revalidate-prefix-tail", creator_subject_id=subject, session_id="typed-revalidate-session", version_count=1, is_active=True, archived=False, source_kind="working_copy", source_ref_hash="sha256:" + "7" * 64))
        db.commit()
    finally:
        db.close()
    beyond_prefix_id = None
    if case == "beyond_prefix":
        selected = _capture_document_inbox_snapshot(factory, subject, 1).items[0].item_handle
        db = factory()
        try:
            rows = db.query(Document.id, Document.creator_subject_id, Document.session_id, Document.version_count,
                            Document.is_active, Document.archived, Document.tidy_verdict, Document.source_kind,
                            Document.source_ref_hash, Document.created_at, Document.updated_at).filter(
                            Document.creator_subject_id == subject,
                            ).all()
            for row in rows:
                captured = tuple(row)
                binding = "sha256:" + hashlib.sha256(
                    b"odysseus.harbour_inbox.document_item.v3\0" + _frame(subject.encode("utf-8")) + _frame(_document_row_bytes(captured, subject))
                ).hexdigest()
                if "hi1:document:" + _unified_digest("document", binding, captured[0]) != selected:
                    beyond_prefix_id = captured[0]
                    break
        finally:
            db.close()
        assert beyond_prefix_id is not None
    planning = _planning_capture(identity)
    knowledge = _knowledge_capture(identity)
    class Planning:
        def capture_inbox_snapshot(self, **kwargs):
            if case == "planning_failure" and "expected_binding_digest" in kwargs:
                raise RuntimeError("closed failure")
            return planning
    class Knowledge:
        @contextmanager
        def guard_inbox_read(self): yield
        def capture_inbox_snapshot(self, **kwargs):
            if case in {"archive", "unarchive", "beyond_prefix"} and "expected_capture" not in kwargs:
                db = factory()
                try:
                    target = {"archive": "typed-revalidate-doc", "unarchive": "typed-revalidate-archived", "beyond_prefix": beyond_prefix_id}[case]
                    db.get(Document, target).archived = case != "unarchive"
                    if case == "beyond_prefix":
                        db.get(Document, target).version_count = 2
                    db.commit()
                finally:
                    db.close()
            return knowledge
    service._planning_runtime, service._knowledge_composition = Planning(), Knowledge()
    if case == "subject_drift":
        db = factory()
        try:
            db.query(ApiToken).filter(ApiToken.id == identity.token_id).update({"owner_subject_id": "owner_" + "c" * 32})
            db.commit()
        finally:
            db.close()
    with pytest.raises(HarbourInboxError) as error:
        service.dispatch("harbour_inbox_list", {"view": "typed", "limit": 1}, identity)
    assert error.value.code in {"inbox_failed", "unauthorized"}


def test_typed_service_requires_all_four_sources_even_when_every_capture_is_empty(tmp_path):
    _engine, _factory, _manager, service, identity, _principal, created = _fixture(tmp_path)
    _action(service, identity, "harbour_inbox_cancel", created["version"], "typed-empty-cancel")
    class Planning:
        def capture_inbox_snapshot(self, **kwargs): return _planning_capture(identity, count=0)
    class Knowledge:
        @contextmanager
        def guard_inbox_read(self): yield
        def capture_inbox_snapshot(self, **kwargs): return _knowledge_capture(identity, count=0)
    service._planning_runtime, service._knowledge_composition = Planning(), Knowledge()
    outcome = service.dispatch("harbour_inbox_list", {"view": "typed", "limit": 1}, identity)
    assert outcome.authority_finalized is False
    assert outcome.payload["count"] == 0
    assert [row["source"] for row in outcome.payload["sources"]] == ["clarification", "planning", "knowledge", "document"]


@pytest.mark.parametrize("failure", ["document", "clarification"])
def test_typed_service_maps_initial_mandatory_capture_failures_to_fixed_inbox_failure(tmp_path, monkeypatch, failure):
    _engine, _factory, _manager, service, identity, _principal, _created = _fixture(tmp_path)
    if failure == "document":
        monkeypatch.setattr(harbour_inbox_service, "_capture_document_inbox_snapshot", lambda *_args: (_ for _ in ()).throw(RuntimeError()))
    else:
        monkeypatch.setattr(ClarificationStore, "capture_inbox_snapshot", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError()))
    with pytest.raises(HarbourInboxError) as error:
        service.dispatch("harbour_inbox_list", {"view": "typed", "limit": 1}, identity)
    assert error.value.code == "inbox_failed"


def test_unified_projection_has_fixed_sources_and_redaction(tmp_path):
    _engine, _factory, _manager, _service, identity, _principal, _created = _fixture(tmp_path)
    capture = _clarification_capture(identity)
    payload = _unified_payload({"clarification": capture}, {"planning", "knowledge"}, 25)
    assert tuple(row["source"] for row in payload["sources"]) == ("clarification", "planning", "knowledge")
    assert payload["complete"] is False and payload["sources"][1]["binding_digest"] is None
    item = payload["items"][0]
    assert item["item_handle"].startswith("hi1:clarification:") and item["redaction_flags"]["identity"] is True


def test_unified_projection_global_and_byte_clipping_keep_only_canonical_opaque_handles(tmp_path):
    _engine, _factory, _manager, _service, identity, _principal, _created = _fixture(tmp_path)
    clarification = _clarification_capture(identity, count=100)
    planning = _planning_capture(identity, count=100, clipped=True)
    knowledge = _knowledge_capture(identity, count=100)
    payload = _unified_payload(
        {"clarification": clarification, "planning": planning, "knowledge": knowledge}, set(), 100,
    )
    assert payload["complete"] is False and payload["clipped"] is True
    assert payload["count"] == len(payload["items"])
    assert payload["count"] < len(clarification.items)
    handles = [item["item_handle"] for item in payload["items"]]
    assert len(handles) == len(set(handles))
    assert [item["source"] for item in payload["items"]] == ["clarification"] * payload["count"]
    assert all(item["source"] == "clarification" for item in payload["items"])
    clarification_only = _unified_payload(
        {"clarification": clarification}, {"planning", "knowledge"}, 100,
    )
    assert payload["items"] == clarification_only["items"][:payload["count"]]
    assert [row["source"] for row in payload["sources"]] == ["clarification", "planning", "knowledge"]
    assert payload["sources"][1]["clipped"] is True
    assert len(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")) <= 32_768


@pytest.mark.parametrize("case", ["stale_planning", "absent_after_capture", "corrupt_knowledge", "stale_knowledge"])
def test_unified_capture_revalidates_each_configured_source_and_rejects_noncanonical_dtos(tmp_path, case):
    _engine, _factory, _manager, service, identity, _principal, _created = _fixture(tmp_path)
    planning = _planning_capture(identity)
    knowledge = _knowledge_capture(identity)

    class Planning:
        calls = 0
        def capture_inbox_snapshot(self, **kwargs):
            self.calls += 1
            if case == "absent_after_capture" and "expected_binding_digest" in kwargs:
                raise McpDispatchFailure("planning_unavailable")
            if case == "stale_planning" and "expected_binding_digest" in kwargs:
                return _planning_capture(identity, count=1, clipped=True)
            return planning

    class Knowledge:
        calls = 0
        @contextmanager
        def guard_inbox_read(self):
            yield
        def capture_inbox_snapshot(self, **kwargs):
            self.calls += 1
            if case == "corrupt_knowledge":
                return {"not": "a frozen snapshot"}
            if case == "stale_knowledge" and "expected_capture" in kwargs:
                return _knowledge_capture(identity, boundary="head-b")
            return knowledge

    service._planning_runtime = Planning()
    service._knowledge_composition = Knowledge()
    with pytest.raises(HarbourInboxError) as error:
        service.dispatch("harbour_inbox_list", {"view": "unified", "limit": 1}, identity)
    assert error.value.code == "inbox_failed"
    assert service._planning_runtime.calls >= 1 and service._knowledge_composition.calls >= 1


class _StatusSubclass(str):
    pass


@pytest.mark.parametrize(("field", "value"), [
    ("knowledge_kind", "foreign_kind"),
    ("occurred_at", "not-a-timestampZ"),
    ("state", _StatusSubclass("purge_pending")),
])
def test_unified_projection_rejects_wrong_knowledge_scalars_even_when_digests_are_canonical(tmp_path, field, value):
    _engine, _factory, _manager, _service, identity, _principal, _created = _fixture(tmp_path)
    knowledge = _knowledge_capture(identity)
    invalid_signal = replace(knowledge.items[0], **{field: value})
    invalid = replace(knowledge, items=(invalid_signal,))
    with pytest.raises(HarbourInboxError) as error:
        _unified_payload({
            "clarification": _clarification_capture(identity),
            "planning": _planning_capture(identity), "knowledge": invalid,
        }, set(), 1)
    assert error.value.code == "inbox_failed"


def test_explicit_unified_list_is_a_nonfinalized_read(tmp_path):
    _engine, _factory, _manager, service, identity, _principal, _created = _fixture(tmp_path)
    outcome = service.dispatch("harbour_inbox_list", {"limit": 1, "view": "unified"}, identity)
    assert outcome.authority_finalized is False
    assert outcome.payload["schema"] == "odysseus.harbour_inbox_unified_list.v1"


def test_valid_unified_controls_preserve_private_generation_and_public_planning_shape(tmp_path):
    _engine, _factory, _manager, _service, identity, _principal, _created = _fixture(tmp_path)
    payload = _unified_payload({
        "clarification": _clarification_capture(identity),
        "planning": _planning_capture(identity),
        "knowledge": _knowledge_capture(identity),
    }, set(), 1)
    planning = next(item for item in payload["items"] if item["source"] == "planning")
    knowledge = next(item for item in payload["items"] if item["source"] == "knowledge")
    assert set(planning) == {
        "item_handle", "source", "owner", "signal_type", "status", "required_action",
        "binding_digest", "due", "receipt_handle", "redaction_flags", "current_revision", "approved_revision",
    }
    assert planning["current_revision"] == 2 and planning["approved_revision"] == 1
    assert type(knowledge["generation"]) is int and knowledge["generation"] == 7
    assert knowledge["redaction_flags"] == {"content": True, "identity": True, "events": True}


@pytest.mark.parametrize(("tool", "status"), [
    ("harbour_inbox_pause", "paused"),
    ("harbour_inbox_reopen", "understanding_review"),
    ("harbour_inbox_cancel", "cancelled"),
])
def test_each_action_uses_exact_args_and_returns_closed_status(tmp_path, tool, status):
    _engine, _factory, _manager, service, identity, _principal, created = _fixture(tmp_path)
    result = _action(service, identity, tool, created["version"], "per-action")
    assert result.payload["action"] == tool.rsplit("_", 1)[-1]
    assert result.payload["item"]["status"] == status
    assert result.payload["item"]["freshness"] == "committed_receipt"
    with pytest.raises(HarbourInboxError) as extra:
        service.dispatch(tool, {
            "clarification_id": "clar-inbox-001", "expected_version": 1,
            "idempotency_key": "per-action", "owner": "admin",
        }, identity)
    assert extra.value.code == "invalid_params"


def test_live_hash_replacement_same_id_is_denied_before_clarification_query(tmp_path):
    engine, factory, _manager, service, identity, _principal, _created = _fixture(tmp_path)
    db = factory()
    try:
        row = db.query(ApiToken).filter(ApiToken.id == identity.token_id).one()
        row.token_hash = bcrypt.hashpw(b"ody_replacement_private_fixture", bcrypt.gensalt()).decode("utf-8")
        row.token_prefix = "ody_inbo"
        db.commit()
    finally:
        db.close()
    clarification_queries = []
    def observe(_conn, _cursor, statement, _parameters, _context, _many):
        if "clarification_" in statement.lower():
            clarification_queries.append(statement.split()[0].lower())
    event.listen(engine, "before_cursor_execute", observe)
    try:
        with pytest.raises(HarbourInboxError) as denied:
            service.dispatch("harbour_inbox_list", {}, identity)
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert denied.value.code == "unauthorized"
    assert clarification_queries == []
    assert "redacted" in repr(service._bearer_proof) and "ody_" not in repr(service._bearer_proof)


@pytest.mark.parametrize("mutation", ["deleted", "inactive", "composite_scope", "stale_sid"])
def test_other_live_token_failures_precede_every_clarification_query(tmp_path, mutation):
    engine, factory, _manager, service, identity, _principal, _created = _fixture(tmp_path)
    db = factory()
    try:
        row = db.query(ApiToken).filter(ApiToken.id == identity.token_id).one()
        if mutation == "deleted":
            db.delete(row)
        elif mutation == "inactive":
            row.is_active = False
        elif mutation == "composite_scope":
            row.scopes = MCP_HARBOUR_INBOX_SCOPE + ",chat"
        else:
            row.owner_subject_id = "owner_ffffffffffffffffffffffffffffffff"
        db.commit()
    finally:
        db.close()
    clarification_queries = []
    def observe(_conn, _cursor, statement, _parameters, _context, _many):
        if "clarification_" in statement.lower():
            clarification_queries.append(statement.split()[0].lower())
    event.listen(engine, "before_cursor_execute", observe)
    try:
        with pytest.raises(HarbourInboxError) as denied:
            service.dispatch("harbour_inbox_list", {}, identity)
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert denied.value.code == "unauthorized"
    assert clarification_queries == []


def test_mcp_replay_returns_exact_original_closed_receipt_after_later_mutation(tmp_path):
    _engine, factory, _manager, service, identity, _principal, created = _fixture(tmp_path)
    first = _action(service, identity, "harbour_inbox_pause", created["version"], "original-key")
    later = _action(service, identity, "harbour_inbox_reopen", first.payload["item"]["version"], "later-key")
    replay = _action(service, identity, "harbour_inbox_pause", created["version"], "original-key")
    assert later.payload["item"]["version"] == first.payload["item"]["version"] + 1
    assert replay.payload == {**first.payload, "idempotent_replay": True}
    assert replay.authority_finalized is True
    assert first.payload["item"]["freshness"] == "committed_receipt"
    assert replay.payload["item"]["freshness"] == "committed_receipt"

    listed = service.dispatch("harbour_inbox_list", {}, identity)
    assert listed.payload["freshness"] == "current"
    assert listed.payload["count"] == 1
    live = listed.payload["items"][0]
    assert (live["status"], live["version"], live["freshness"]) == (
        "understanding_review", created["version"] + 2, "current",
    )

    db = factory()
    try:
        persisted = db.query(ClarificationRun).filter(
            ClarificationRun.id == "clar-inbox-001"
        ).one()
        events = db.query(ClarificationEvent).filter(
            ClarificationEvent.clarification_id == "clar-inbox-001"
        ).order_by(ClarificationEvent.version).all()
        assert (persisted.status, persisted.version) == (
            "understanding_review", created["version"] + 2,
        )
        assert [row.version for row in events] == [1, 2, 3]
    finally:
        db.close()


def test_subject_scope_excludes_foreign_and_local_null_rows(tmp_path):
    _engine, factory, manager, service, identity, _principal, _created = _fixture(tmp_path)
    assert manager.create_user("other", "password")
    other = SessionPrincipal("other", manager.subject_id_for_username("other"), "browser_session")
    local = SessionPrincipal("local", None, "local")
    store = ClarificationStore(session_factory=factory)
    store.create_run(principal=other, session_id="other-private", request=_request(), clarification_id="clar-other-001")
    store.create_run(principal=local, session_id="local-private", request=_request(), clarification_id="clar-local-001")
    listed = service.dispatch("harbour_inbox_list", {}, identity).payload
    assert [item["clarification_id"] for item in listed["items"]] == ["clar-inbox-001"]


def test_browser_namespace_collision_fails_closed_without_cross_channel_replay(tmp_path):
    _engine, factory, _manager, service, identity, principal, created = _fixture(tmp_path)
    public_key = "collision-key"
    internal_key = "mcp-hi-v1:" + hashlib.sha256(public_key.encode("utf-8")).hexdigest()
    browser = ClarificationStore(session_factory=factory).pause_run(
        principal=principal,
        clarification_id="clar-inbox-001",
        expected_version=created["version"],
        idempotency_key=internal_key,
    )
    assert browser.run["version"] == 2
    with pytest.raises(HarbourInboxError) as conflict:
        _action(service, identity, "harbour_inbox_pause", created["version"], public_key)
    assert conflict.value.code == "idempotency_conflict"


def test_dispatch_rejects_action_notification_and_batch_before_runtime_call():
    identity = McpAuthenticatedIdentity("id", "admin", "owner_" + "a" * 32, 1, 0, MCP_HARBOUR_INBOX_SCOPE)
    class Runtime:
        calls = 0
        def dispatch(self, *_args): self.calls += 1
        def tool_contracts(self): return build_harbour_inbox_tool_contracts()
    runtime = Runtime()
    action = {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "harbour_inbox_pause", "arguments": {
        "clarification_id": "clar-inbox-001", "expected_version": 1, "idempotency_key": "one",
    }}}
    with pytest.raises(McpDispatchFailure):
        asyncio.run(dispatch_http_payload(action, identity=identity, runtime=runtime, started=time.monotonic()))
    with pytest.raises(McpDispatchFailure):
        asyncio.run(dispatch_http_payload([{**action, "id": 1}], identity=identity, runtime=runtime, started=time.monotonic()))
    assert runtime.calls == 0


def test_account_delete_waits_for_guarded_action_commit_and_threads_join(tmp_path, monkeypatch):
    engine, factory, manager, service, identity, _principal, created = _fixture(tmp_path)
    assert manager.create_user("guardian", "password", is_admin=True)
    @contextmanager
    def database_context():
        db = factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    monkeypatch.setattr("core.database.get_db_session", database_context)
    commit_entered = threading.Event()
    release_commit = threading.Event()
    errors = []
    def after_flush(session, _flush_context):
        if any(getattr(value, "event_type", None) == "run_paused" for value in session.new):
            session.info["h1r09_pause_flushed"] = True
    def before_commit(session):
        if session.info.get("h1r09_pause_flushed") is True:
            commit_entered.set()
            if not release_commit.wait(3):
                raise TimeoutError("test barrier")
    event.listen(factory.class_, "after_flush", after_flush)
    event.listen(factory.class_, "before_commit", before_commit)
    action_result = []
    def act():
        try: action_result.append(_action(service, identity, "harbour_inbox_pause", created["version"]))
        except BaseException as exc: errors.append(type(exc))
    deletion_result = []
    def remove():
        try: deletion_result.append(manager.delete_user("admin", "guardian"))
        except BaseException as exc: errors.append(type(exc))
    actor = threading.Thread(target=act)
    remover = threading.Thread(target=remove)
    actor_started = remover_started = False
    try:
        actor.start()
        actor_started = True
        assert commit_entered.wait(2)
        remover.start()
        remover_started = True
        remover.join(.2)
        assert remover.is_alive()
        release_commit.set()
        actor.join(5); remover.join(5)
    finally:
        release_commit.set()
        if actor_started: actor.join(1)
        if remover_started: remover.join(1)
        event.remove(factory.class_, "after_flush", after_flush)
        event.remove(factory.class_, "before_commit", before_commit)
    assert not actor.is_alive() and not remover.is_alive() and errors == []
    assert action_result[0].authority_finalized is True and deletion_result == [True]


@pytest.mark.parametrize("mutation", ["token_revoke", "account_demote"])
def test_real_sql_pause_commit_blocks_token_revoke_or_account_demote(tmp_path, monkeypatch, mutation):
    engine, factory, manager, service, identity, _principal, created = _fixture(tmp_path)
    assert manager.create_user("guardian", "password", is_admin=True)
    @contextmanager
    def database_context():
        db = factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    monkeypatch.setattr("core.database.get_db_session", database_context)
    token_delete = token_request = None
    if mutation == "token_revoke":
        import routes.api_token_routes as token_routes
        monkeypatch.setenv("AUTH_ENABLED", "true")
        token_delete = token_routes
        token_request = SimpleNamespace(
            state=SimpleNamespace(current_user="admin"), headers={}, cookies={},
            app=SimpleNamespace(state=SimpleNamespace(
                auth_manager=manager, invalidate_token_cache=lambda: None,
            )),
        )
    commit_entered, release_commit = threading.Event(), threading.Event()
    def after_flush(session, _flush_context):
        if any(getattr(value, "event_type", None) == "run_paused" for value in session.new):
            session.info["h1r09_pause_flushed"] = True
    def before_commit(session):
        if session.info.get("h1r09_pause_flushed") is True:
            commit_entered.set()
            if not release_commit.wait(3):
                raise TimeoutError("test barrier")
    event.listen(factory.class_, "after_flush", after_flush)
    event.listen(factory.class_, "before_commit", before_commit)
    action_result, mutation_result, errors = [], [], []
    def act():
        try: action_result.append(_action(service, identity, "harbour_inbox_pause", created["version"]))
        except BaseException as exc: errors.append(type(exc))
    def mutate_authority():
        try:
            if mutation == "token_revoke":
                with token_delete._guard_token_mutation(token_request) as current_manager:
                    with database_context() as db:
                        row = db.query(ApiToken).filter(ApiToken.id == identity.token_id).one()
                        token_delete._validate_existing_mcp_token(row, "admin", current_manager)
                        db.delete(row)
                mutation_result.append(True)
            else:
                mutation_result.append(manager.set_admin("admin", False, "guardian").value == "ok")
        except BaseException as exc: errors.append(type(exc))
    actor, mutator = threading.Thread(target=act), threading.Thread(target=mutate_authority)
    actor_started = mutator_started = False
    try:
        actor.start(); actor_started = True
        assert commit_entered.wait(2)
        mutator.start(); mutator_started = True
        mutator.join(.2)
        assert mutator.is_alive()
        release_commit.set(); actor.join(5); mutator.join(5)
    finally:
        release_commit.set()
        if actor_started: actor.join(1)
        if mutator_started: mutator.join(1)
        event.remove(factory.class_, "after_flush", after_flush)
        event.remove(factory.class_, "before_commit", before_commit)
    assert not actor.is_alive() and not mutator.is_alive() and errors == []
    assert action_result[0].payload["item"]["status"] == "paused" and mutation_result == [True]


@pytest.mark.parametrize("mode", ["timeout", "cancel", "exception"])
def test_real_sql_dispatch_joins_worker_and_leaves_determinate_transaction_and_unlocked_authority(
    tmp_path, monkeypatch, mode,
):
    _engine, factory, manager, service, identity, principal, created = _fixture(tmp_path)
    commit_entered, release_commit, worker_done = threading.Event(), threading.Event(), threading.Event()
    def after_flush(session, _flush_context):
        if any(getattr(value, "event_type", None) == "run_paused" for value in session.new):
            session.info["h1r09_dispatch_pause"] = True
    def before_commit(session):
        if session.info.get("h1r09_dispatch_pause") is not True:
            return
        commit_entered.set()
        if mode == "exception":
            raise RuntimeError("injected commit fault")
        if not release_commit.wait(3):
            raise TimeoutError("test barrier")
    def after_end(session):
        if session.info.get("h1r09_dispatch_pause") is True:
            worker_done.set()
    event.listen(factory.class_, "after_flush", after_flush)
    event.listen(factory.class_, "before_commit", before_commit)
    event.listen(factory.class_, "after_commit", after_end)
    event.listen(factory.class_, "after_rollback", after_end)
    payload = {"jsonrpc": "2.0", "id": 41, "method": "tools/call", "params": {
        "name": "harbour_inbox_pause", "arguments": {
            "clarification_id": "clar-inbox-001", "expected_version": created["version"],
            "idempotency_key": "dispatch-" + mode,
        },
    }}
    timer = None
    async def run_dispatch():
        nonlocal timer
        if mode == "timeout":
            monkeypatch.setattr("src.mcp_authenticated_dispatch.MCP_TOTAL_BUDGET_SECONDS", .2)
        task = asyncio.create_task(dispatch_http_payload(
            payload, identity=identity, runtime=service, started=time.monotonic(),
        ))
        assert await asyncio.to_thread(commit_entered.wait, 2)
        if mode == "cancel":
            task.cancel()
            release_commit.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            return
        if mode == "timeout":
            timer = threading.Timer(.3, release_commit.set)
            timer.start()
        result = await task
        if mode == "exception":
            assert result["error"]["message"] == "Planning read failed"
        else:
            assert getattr(result, "payload", None) is not None
    try:
        asyncio.run(run_dispatch())
    finally:
        release_commit.set()
        if timer is not None:
            timer.join(1)
        event.remove(factory.class_, "after_flush", after_flush)
        event.remove(factory.class_, "before_commit", before_commit)
        event.remove(factory.class_, "after_commit", after_end)
        event.remove(factory.class_, "after_rollback", after_end)
    assert worker_done.is_set()
    persisted = ClarificationStore(session_factory=factory).read_run(
        principal=principal, clarification_id="clar-inbox-001",
    )
    assert (persisted["version"], persisted["status"]) == (
        (created["version"], "understanding_review")
        if mode == "exception" else (created["version"] + 1, "paused")
    )
    db = factory()
    try:
        assert db.query(ClarificationEvent).filter(
            ClarificationEvent.clarification_id == "clar-inbox-001"
        ).count() == (1 if mode == "exception" else 2)
    finally:
        db.close()
    authority_unlocked = threading.Event()
    def acquire_after_dispatch():
        with manager.guard_session_authority():
            authority_unlocked.set()
    probe = threading.Thread(target=acquire_after_dispatch)
    probe.start(); probe.join(2)
    assert not probe.is_alive() and authority_unlocked.is_set()


def test_mcp_recovery_before_commit_returns_none_without_mutation(tmp_path):
    _engine, factory, _manager, service, identity, principal, _created = _fixture(tmp_path)
    mcp = SessionPrincipal(principal.owner, principal.creator_subject_id, "mcp_subject")
    before = _db_state(factory)
    store = ClarificationStore(session_factory=factory)
    assert store.read_mcp_inbox_action_result(
        principal=mcp, clarification_id="clar-inbox-001", operation="pause",
        expected_version=1,
        idempotency_key="mcp-hi-v1:" + hashlib.sha256(b"precommit").hexdigest(),
    ) is None
    with pytest.raises(HarbourInboxError) as missing:
        _recovery(service, identity, "harbour_inbox_pause", 1, "precommit")
    assert missing.value.code == "inbox_failed"
    assert _db_state(factory) == before


@pytest.mark.parametrize("operation", ["pause", "reopen", "cancel"])
def test_mcp_recovery_replays_each_operation_after_real_later_actions(tmp_path, operation):
    _engine, factory, _manager, service, identity, _principal, created = _fixture(tmp_path)
    target_key = "recovery-" + operation
    if operation == "pause":
        first = _action(service, identity, "harbour_inbox_pause", created["version"], target_key)
        _action(service, identity, "harbour_inbox_reopen", first.payload["item"]["version"], "later-pause")
        target_version = created["version"]
    elif operation == "reopen":
        first = _action(service, identity, "harbour_inbox_reopen", created["version"], target_key)
        _action(service, identity, "harbour_inbox_pause", first.payload["item"]["version"], "later-reopen")
        target_version = created["version"]
    else:
        paused = _action(service, identity, "harbour_inbox_pause", created["version"], "before-cancel")
        reopened = _action(service, identity, "harbour_inbox_reopen", paused.payload["item"]["version"], "before-cancel-reopen")
        first = _action(service, identity, "harbour_inbox_cancel", reopened.payload["item"]["version"], target_key)
        target_version = reopened.payload["item"]["version"]
    before = _db_state(factory)
    recovered = _recovery(
        service, identity, "harbour_inbox_" + operation, target_version, target_key,
    )
    assert recovered.authority_finalized is True
    assert recovered.payload == {**first.payload, "idempotent_replay": True}
    assert _db_state(factory) == before


@pytest.mark.parametrize("operation", ["pause", "reopen", "cancel"])
def test_mcp_recovery_and_existing_receipt_follow_real_coordinated_rename(tmp_path, operation):
    _engine, factory, manager, service, identity, _principal, created = _fixture(tmp_path)
    target_key = "rename-recovery-" + operation
    if operation == "pause":
        first = _action(service, identity, "harbour_inbox_pause", created["version"], target_key)
        later = _action(service, identity, "harbour_inbox_reopen", first.payload["item"]["version"], "rename-later-pause")
    elif operation == "reopen":
        first = _action(service, identity, "harbour_inbox_reopen", created["version"], target_key)
        later = _action(service, identity, "harbour_inbox_pause", first.payload["item"]["version"], "rename-later-reopen")
    else:
        paused = _action(service, identity, "harbour_inbox_pause", created["version"], "rename-before-cancel")
        reopened = _action(service, identity, "harbour_inbox_reopen", paused.payload["item"]["version"], "rename-before-cancel-reopen")
        first = _action(service, identity, "harbour_inbox_cancel", reopened.payload["item"]["version"], target_key)
        later = first
    target_version = created["version"] if operation != "cancel" else later.payload["item"]["version"] - 1
    renamed_identity, renamed_principal = _rename_fixture_owner(factory, manager, identity)
    before = _db_state(factory)
    recovered = _recovery(
        service, renamed_identity, "harbour_inbox_" + operation, target_version, target_key,
    )
    assert recovered.payload == {**first.payload, "idempotent_replay": True}
    current_version = before[0]
    receipt = ClarificationStore(session_factory=factory).read_inbox_action_receipt(
        principal=renamed_principal, clarification_id="clar-inbox-001",
        event_version=target_version + 1, expected_current_version=current_version,
        expected_receipt_handle=None,
    )
    assert receipt is not None and receipt.operation == operation
    assert _db_state(factory) == before
    db = factory()
    try:
        assert db.query(ClarificationRun).filter(ClarificationRun.owner == "renamed").count() == 1
        assert db.query(ClarificationEvent).filter(
            ClarificationEvent.clarification_id == "clar-inbox-001",
            ClarificationEvent.owner == "renamed",
        ).count() == before[1]
    finally:
        db.close()


def test_mcp_recovery_accepts_last_derived_version_without_widening_input(tmp_path):
    _engine, factory, _manager, service, identity, _principal, created = _fixture(tmp_path)
    db = factory()
    try:
        db.query(ClarificationRun).filter(
            ClarificationRun.id == "clar-inbox-001"
        ).update({"version": 1_000_000})
        db.commit()
    finally:
        db.close()

    first = _action(service, identity, "harbour_inbox_pause", 1_000_000, "last-derived-version")
    assert first.payload["item"]["version"] == 1_000_001
    before = _db_state(factory)
    recovered = service.recover_action(
        "harbour_inbox_pause", {
            "clarification_id": "clar-inbox-001",
            "expected_version": 1_000_000,
            "idempotency_key": "last-derived-version",
        }, identity,
    )
    assert _db_state(factory) == before
    assert recovered.authority_finalized is True
    assert recovered.payload == {**first.payload, "idempotent_replay": True}
    with pytest.raises(HarbourInboxError) as widened:
        _action(service, identity, "harbour_inbox_reopen", 1_000_001, "widened-input")
    assert widened.value.code == "invalid_params"


@pytest.mark.parametrize("mutation", ["revoked", "replaced", "wrong_sid"])
def test_mcp_recovery_rechecks_live_token_and_sid_before_read(tmp_path, mutation):
    _engine, factory, _manager, service, identity, _principal, created = _fixture(tmp_path)
    _action(service, identity, "harbour_inbox_pause", created["version"], "auth-negative")
    before = _db_state(factory)
    recovery_identity = identity
    db = factory()
    try:
        token = db.query(ApiToken).filter(ApiToken.id == identity.token_id).one()
        if mutation == "revoked":
            token.is_active = False
        elif mutation == "replaced":
            token.token_hash = bcrypt.hashpw(b"ody_replaced_recovery_token", bcrypt.gensalt()).decode("utf-8")
        else:
            recovery_identity = McpAuthenticatedIdentity(
                identity.token_id, identity.owner_alias,
                "owner_ffffffffffffffffffffffffffffffff",
                identity.cache_generation, identity.security_action_revision, identity.scope,
            )
        db.commit()
    finally:
        db.close()
    with pytest.raises(HarbourInboxError) as denied:
        _recovery(service, recovery_identity, "harbour_inbox_pause", created["version"], "auth-negative")
    assert denied.value.code == "unauthorized"
    assert _db_state(factory) == before


@pytest.mark.parametrize("target", ["run", "event"])
def test_mcp_recovery_rejects_current_owner_alias_drift_without_mutation(tmp_path, target):
    _engine, factory, _manager, service, identity, _principal, created = _fixture(tmp_path)
    _action(service, identity, "harbour_inbox_pause", created["version"], "owner-drift")
    db = factory()
    try:
        if target == "run":
            db.query(ClarificationRun).filter(
                ClarificationRun.id == "clar-inbox-001",
            ).update({"owner": "drifted"})
        else:
            db.query(ClarificationEvent).filter(
                ClarificationEvent.clarification_id == "clar-inbox-001",
                ClarificationEvent.version == 2,
            ).update({"owner": "drifted"})
        db.commit()
    finally:
        db.close()
    before = _db_state(factory)
    with pytest.raises(HarbourInboxError) as denied:
        _recovery(service, identity, "harbour_inbox_pause", created["version"], "owner-drift")
    assert denied.value.code == "inbox_failed"
    assert _db_state(factory) == before


@pytest.mark.parametrize("private_side", ["run", "event"])
def test_mcp_recovery_rejects_private_historical_owner_disagreement(tmp_path, private_side):
    _engine, factory, _manager, service, identity, _principal, created = _fixture(tmp_path)
    _action(service, identity, "harbour_inbox_pause", created["version"], "private-owner-drift")
    db = factory()
    try:
        row = db.query(ClarificationEvent).filter(
            ClarificationEvent.clarification_id == "clar-inbox-001",
            ClarificationEvent.version == 2,
        ).one()
        payload = json.loads(row.payload_json)
        receipt = payload["__odysseus_clarification_private_v1"]["receipt"]
        receipt[private_side]["owner"] = "historical-disagreement"
        row.payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        db.commit()
    finally:
        db.close()
    before = _db_state(factory)
    with pytest.raises(HarbourInboxError) as denied:
        _recovery(service, identity, "harbour_inbox_pause", created["version"], "private-owner-drift")
    assert denied.value.code == "inbox_failed"
    assert _db_state(factory) == before


@pytest.mark.parametrize("case", ["operation", "version", "fingerprint"])
def test_mcp_recovery_classifies_existing_operation_version_and_fingerprint_conflicts(tmp_path, case):
    _engine, factory, _manager, service, identity, _principal, created = _fixture(tmp_path)
    _action(service, identity, "harbour_inbox_pause", created["version"], "conflict-key")
    if case == "fingerprint":
        db = factory()
        try:
            row = db.query(ClarificationEvent).filter(
                ClarificationEvent.clarification_id == "clar-inbox-001",
                ClarificationEvent.version == 2,
            ).one()
            payload = json.loads(row.payload_json)
            payload["__odysseus_clarification_private_v1"]["fingerprint"] = "0" * 64
            row.payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            db.commit()
        finally:
            db.close()
    before = _db_state(factory)
    recovery_name = "harbour_inbox_reopen" if case == "operation" else "harbour_inbox_pause"
    recovery_version = created["version"] + 1 if case == "version" else created["version"]
    with pytest.raises(HarbourInboxError) as conflict:
        _recovery(service, identity, recovery_name, recovery_version, "conflict-key")
    assert conflict.value.code == "idempotency_conflict"
    assert _db_state(factory) == before


def test_mcp_recovery_rejects_public_or_malformed_namespace_at_store_boundary(tmp_path):
    _engine, factory, _manager, service, identity, principal, created = _fixture(tmp_path)
    _action(service, identity, "harbour_inbox_pause", created["version"], "namespace-key")
    mcp = SessionPrincipal(principal.owner, principal.creator_subject_id, "mcp_subject")
    before = _db_state(factory)
    with pytest.raises(ClarificationStoreError) as malformed:
        ClarificationStore(session_factory=factory).read_mcp_inbox_action_result(
            principal=mcp, clarification_id="clar-inbox-001", operation="pause",
            expected_version=created["version"], idempotency_key="namespace-key",
        )
    assert getattr(malformed.value, "code", None) == "inbox_failed"
    assert _db_state(factory) == before


@pytest.mark.parametrize("name,arguments", [
    ("harbour_inbox_unknown", {"clarification_id": "clar-inbox-001", "expected_version": 1, "idempotency_key": "bad-name"}),
    ("harbour_inbox_pause", {"clarification_id": "bad", "expected_version": 1, "idempotency_key": "bad-id"}),
    ("harbour_inbox_pause", {"clarification_id": "clar-inbox-001", "expected_version": 1_000_001, "idempotency_key": "bad-version"}),
    ("harbour_inbox_pause", {"clarification_id": "clar-inbox-001", "expected_version": 1, "idempotency_key": "bad key"}),
])
def test_mcp_recovery_rejects_malformed_arguments_without_read_or_mutation(tmp_path, name, arguments):
    _engine, factory, _manager, service, identity, _principal, _created = _fixture(tmp_path)
    before = _db_state(factory)
    with pytest.raises(HarbourInboxError) as denied:
        service.recover_action(name, arguments, identity)
    assert denied.value.code == "invalid_params"
    assert _db_state(factory) == before


@pytest.mark.parametrize("tamper", ["question_id", "descriptor", "envelope", "payload_status"])
def test_mcp_recovery_rejects_event_descriptor_and_envelope_tamper(tmp_path, tamper):
    _engine, factory, _manager, service, identity, _principal, created = _fixture(tmp_path)
    _action(service, identity, "harbour_inbox_pause", created["version"], "descriptor-tamper")
    db = factory()
    try:
        row = db.query(ClarificationEvent).filter(
            ClarificationEvent.clarification_id == "clar-inbox-001",
            ClarificationEvent.version == 2,
        ).one()
        if tamper == "question_id":
            row.question_id = "unexpected-question"
        elif tamper == "descriptor":
            row.id = "!"
        elif tamper == "envelope":
            row.payload_json = "{}"
        else:
            payload = json.loads(row.payload_json)
            payload["status"] = "cancelled"
            row.payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        db.commit()
    finally:
        db.close()
    before = _db_state(factory)
    with pytest.raises(HarbourInboxError) as denied:
        _recovery(service, identity, "harbour_inbox_pause", created["version"], "descriptor-tamper")
    assert denied.value.code == "inbox_failed"
    assert _db_state(factory) == before


def test_mcp_recovery_rejects_duplicate_matching_events_without_mutation(tmp_path):
    _engine, factory, _manager, service, identity, _principal, created = _fixture(tmp_path)
    _action(service, identity, "harbour_inbox_pause", created["version"], "duplicate-event")
    db = factory()
    try:
        db.connection().exec_driver_sql("DROP INDEX IF EXISTS ix_clarification_events_idempotency")
        original = db.query(ClarificationEvent).filter(
            ClarificationEvent.clarification_id == "clar-inbox-001",
            ClarificationEvent.version == 2,
        ).one()
        db.add(ClarificationEvent(
            id="evt-duplicate-recovery",
            clarification_id=original.clarification_id,
            owner=original.owner,
            event_type=original.event_type,
            version=original.version,
            question_id=original.question_id,
            idempotency_key=original.idempotency_key,
            payload_json=original.payload_json,
            created_at=original.created_at,
        ))
        db.commit()
    finally:
        db.close()
    before = _db_state(factory)
    with pytest.raises(HarbourInboxError) as denied:
        _recovery(service, identity, "harbour_inbox_pause", created["version"], "duplicate-event")
    assert denied.value.code == "inbox_failed"
    assert _db_state(factory) == before
