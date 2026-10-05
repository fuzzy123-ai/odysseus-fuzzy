from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.planning_context_reader as planning_reader
from core.auth import AuthManager
from core.database import Base, ChatMessage, Session
from src.harbour_authority import AuthenticatedPrincipalIssuer
from src.private_context_confirmation_app import (
    ContextSelection,
    CookieConfirmationAuthority,
    PrivateContextConfirmationApp,
    SourceSelection,
)
from src.mcp_authenticated_dispatch import AppOwnedPlanningRuntime
from src.planning_mcp_service import PlanningMcpService
from src.planning_revision_store import DurablePlanningRevisionStore, PlanningRevisionStore
from src.planning_roadmap_migration import build_h1r03_migration_manifest
from src.planning_definition_contract import compute_roadmap_content_hash
from src.planning_context_reader import (
    PlanningContextPage,
    PlanningContextPageRequest,
    PlanningContextPageConsumer,
    PlanningContextReaderError,
    PlanningContextManifestProducer,
)
from src.unified_source_index_owner_scope import OwnerScope
from src.unified_source_index_sources.planning import PlanningSourceAdapter
from tests.test_planning_durable_context_source import durable_planning_source, _append_revision


def digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def planning_selection() -> tuple[ContextSelection, SourceSelection]:
    item = SourceSelection(
        "planning",
        "usi_source_" + "1" * 64,
        "usi_version_" + "2" * 64,
    )
    return ContextSelection((item,)), item


def test_page_request_requires_the_complete_original_planning_selection() -> None:
    selection, target = planning_selection()
    request = PlanningContextPageRequest(
        selection=selection,
        target=target,
        chat_session_id="chat-owned",
        expected_manifest_digest=digest(b"manifest"),
        offset=0,
        limit=128,
        disclosure_message_id=7,
    )
    assert request.selection.items == (target,)
    with pytest.raises(PlanningContextReaderError, match="invalid_page"):
        PlanningContextPageRequest(
            selection=selection,
            target=SourceSelection("planning", target.selection_ref, "approved:2:sha256:" + "3" * 64),
            chat_session_id="chat-owned",
            expected_manifest_digest=digest(b"manifest"),
            offset=0,
            limit=128,
            disclosure_message_id=7,
        )


def test_page_reports_separate_whole_and_page_digests() -> None:
    payload = b'{"project": {"project_id": "harbour-one"}}'
    page = payload[3:17]
    value = PlanningContextPage(
        page_bytes=page,
        total_bytes=len(payload),
        offset=3,
        end=17,
        whole_sha256=digest(payload),
        page_sha256=digest(page),
        manifest_digest=digest(b"manifest"),
    )
    assert value.whole_sha256 == digest(payload)
    assert value.page_sha256 == digest(page)
    assert value.byteform == "durable_revision_document"


def test_page_total_is_honest_without_an_arbitrary_sixteen_mebibyte_cap() -> None:
    payload_size = (1_048_576 * 16) + 1
    value = PlanningContextPage(
        page_bytes=b"x",
        total_bytes=payload_size,
        offset=0,
        end=1,
        whole_sha256=digest(b"large-payload"),
        page_sha256=digest(b"x"),
        manifest_digest=digest(b"manifest"),
    )
    assert value.total_bytes == payload_size


def test_durable_records_use_json_lists_and_store_canonical_utf8() -> None:
    document = {
        "project": {"project_id": "harbour-one"},
        "roadmaps": [{
            "roadmap_id": "roadmap-a",
            "revision": 1,
            "revision_state": "approved",
            "content_hash": digest("café".encode("utf-8")),
        }],
    }
    records = planning_reader.PlanningContextManifestProducer._validated_records(
        [("fuzzy", document, "durable:revision-1")], "fuzzy"
    )
    assert records["revision-1"].roadmap_id == "roadmap-a"
    assert planning_reader._canonical({"text": "café"}) == json.dumps(
        {"text": "café"},
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    with pytest.raises(PlanningContextReaderError, match="planning_unavailable"):
        planning_reader._canonical({"value": float("nan")})


def test_current_must_equal_approved_before_metadata_is_materialized() -> None:
    with pytest.raises(PlanningContextReaderError, match="planning_stale"):
        PlanningContextManifestProducer._validated_heads(
            [{
                "owner_id": "fuzzy",
                "project_id": "harbour-one",
                "roadmap_id": "roadmap-a",
                "accepted_revision_id": "rev-accepted",
                "accepted_revision": 1,
                "accepted_content_sha256": digest(b"accepted"),
                "current_revision_id": "rev-draft",
                "current_revision": 2,
                "current_content_sha256": digest(b"draft"),
                "approved_revision_id": "rev-approved",
                "approved_revision": 1,
                "approved_content_sha256": digest(b"approved"),
                "generation": 1,
            }],
            "fuzzy",
        )


def test_producer_requires_the_app_owned_durable_runtime(tmp_path) -> None:
    auth = AuthManager(str(tmp_path / "auth.json"))
    service = PlanningMcpService(tmp_path)
    runtime = AppOwnedPlanningRuntime(
        repo_root=str(tmp_path),
        legacy_store=PlanningRevisionStore(),
        durable_store=None,
        issuer=None,
    )
    with pytest.raises(PlanningContextReaderError, match="planning_unavailable"):
        PlanningContextManifestProducer(runtime, auth, service, "harbour-one")


def test_head_generation_must_be_positive_before_metadata_is_materialized() -> None:
    with pytest.raises(PlanningContextReaderError, match="planning_stale"):
        PlanningContextManifestProducer._validated_heads(
            [{
                "owner_id": "fuzzy",
                "project_id": "harbour-one",
                "roadmap_id": "roadmap-a",
                "accepted_revision_id": "rev-accepted",
                "accepted_revision": 1,
                "accepted_content_sha256": digest(b"accepted"),
                "current_revision_id": "rev-approved",
                "current_revision": 1,
                "current_content_sha256": digest(b"approved"),
                "approved_revision_id": "rev-approved",
                "approved_revision": 1,
                "approved_content_sha256": digest(b"approved"),
                "generation": 0,
            }],
            "fuzzy",
        )


def test_real_accepted_planning_runtime_pages_unicode_and_rejects_row_tamper(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    auth = AuthManager(str(tmp_path / "auth.json"))
    assert auth.create_user("fuzzy", "Password-123", is_admin=True)
    cookie = auth.create_session("fuzzy", "Password-123")
    assert cookie
    owner_sid = auth.subject_id_for_username("fuzzy")
    assert isinstance(owner_sid, str) and owner_sid

    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="odysseus-auth",
        key_id="planning-key-1",
        secret=b"real-planning-reader-test-secret-0001",
    )
    store = DurablePlanningRevisionStore(
        tmp_path / "durable-planning",
        trusted_issuers=(issuer,),
    )
    principal = issuer.issue(
        subject_id=owner_sid,
        owner_id="fuzzy",
        auth_context_digest="sha256:" + "4" * 64,
        ttl_seconds=3600,
        nonce="real-planning-reader-accepted",
    )
    accepted = build_h1r03_migration_manifest(repo_root)
    store.prepare_migration(principal, manifest=accepted)
    store.accept_migration(
        principal,
        batch_id=accepted["batch_id"],
        current_manifest=lambda: deepcopy(accepted),
    )
    owner, _batch, records, _heads = store.read_subject_bound_accepted_migration_records(
        principal,
        project_id="harbour-one",
    )
    selected_roadmap = "abc-roadmap-reconciliation-import"
    accepted_document = deepcopy(next(
        document for _record_owner, document, _revision_ref in records
        if document["roadmaps"][0]["roadmap_id"] == selected_roadmap
    ))
    roadmap = accepted_document["roadmaps"][0]
    roadmap["revision"] = 2
    roadmap["title"] = "Übersicht der Prüfung"
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    accepted_document["project"]["latest_approved_revision"] = {
        selected_roadmap: {"revision": 2, "content_hash": roadmap["content_hash"]},
    }
    head = store.get_head(principal, owner_id=owner, project_id="harbour-one", roadmap_id=selected_roadmap)
    appended = store.append_revision(
        principal, owner_id=owner, project_id="harbour-one", roadmap_id=selected_roadmap,
        payload_bytes=json.dumps(accepted_document, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        expected_head=head, idempotency_key="unicode-approved-append",
    )
    store.approve_revision(
        principal, owner_id=owner, project_id="harbour-one", roadmap_id=selected_roadmap,
        revision_id=appended.new_revision_id, expected_head=appended.after_head,
        idempotency_key="unicode-approved-head",
    )
    owner, _batch, records, _heads = store.read_subject_bound_accepted_migration_records(
        principal, project_id="harbour-one",
    )
    source_paths = {
        item["roadmap_id"]: item["path"] for item in accepted["source_items"]
    }
    legacy_records = [
        (owner, document, source_paths[document["roadmaps"][0]["roadmap_id"]])
        for _record_owner, document, _revision_ref in records
        if document["roadmaps"][0]["roadmap_id"] == selected_roadmap
        and document["roadmaps"][0]["revision"] == 2
    ]
    # Owner-source policy validates the exact physical document against its
    # approved store row. Accepted migrations normalize legacy documents;
    # provide those same accepted documents in an isolated source repository.
    fixture_root = tmp_path / "planning-source"
    fixture_root.mkdir()
    for _record_owner, document, source_ref in legacy_records:
        relative = Path(source_ref)
        assert not relative.is_absolute() and ".." not in relative.parts
        destination = (fixture_root / relative).resolve()
        assert destination.is_relative_to(fixture_root.resolve())
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(document, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
    legacy = PlanningRevisionStore(
        legacy_records,
        cursor_secret=b"real-planning-reader-legacy-secret",
    )
    service = PlanningMcpService(
        fixture_root,
        definition_store=legacy,
        definition_owner="fuzzy",
    )
    runtime = AppOwnedPlanningRuntime(
        repo_root=str(repo_root),
        legacy_store=legacy,
        durable_store=store,
        issuer=issuer,
    )
    producer = PlanningContextManifestProducer(runtime, auth, service, "harbour-one")

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = session_factory()
    db.add(
        Session(
            id="chat-owned",
            name="Owned",
            endpoint_url="local",
            model="test",
            owner="fuzzy",
            message_count=0,
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
    )
    db.commit()
    db.close()

    try:
        adapter = PlanningSourceAdapter(
            service=service,
            owner_scope=OwnerScope.for_subject_id(owner_sid),
            expected_owner_ref="fuzzy",
            observed_at="2026-10-04T00:00:00Z",
            max_sources=100,
        )
        binding = adapter.binding
        discovered = adapter.discover(
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest=binding.snapshot_digest,
            cursor=0,
            limit=100,
        )
        target_descriptor = next(
            item for item in discovered.items
            if item.evidence.roadmap_id == "abc-roadmap-reconciliation-import"
        )
        target = SourceSelection(
            "planning",
            target_descriptor.source.source_id,
            target_descriptor.source_version.source_version_id,
        )
        selection = ContextSelection((target,))

        authority = CookieConfirmationAuthority(auth)
        with authority.hold(cookie, "chat-owned") as disclosure_binding:
            metadata = producer.capture_metadata(disclosure_binding, selection)
            manifest = producer.capture(disclosure_binding, selection)
            payload = producer.read_document(disclosure_binding, target)
        assert any(byte > 127 for byte in payload)
        assert len(payload) > 2
        assert len(metadata) == 1
        assert metadata[0].selection == target
        assert len(manifest.digest) == 64
        assert all(character in "0123456789abcdef" for character in manifest.digest)

        confirmation = PrivateContextConfirmationApp(session_factory, auth, producer)
        consumer = PlanningContextPageConsumer(producer, auth, confirmation, session_factory)
        challenge = confirmation.issue_for_selection(cookie, "chat-owned", selection)
        verified = confirmation.consume_challenge(cookie, "chat-owned", challenge)
        first_request = PlanningContextPageRequest(
            selection=selection,
            target=target,
            chat_session_id="chat-owned",
            expected_manifest_digest=manifest.digest,
            offset=0,
            limit=max(1, len(payload) // 2),
            disclosure_message_id=verified.disclosure_message_id,
        )
        first_page = consumer.read_page(cookie, first_request)
        second_request = PlanningContextPageRequest(
            selection=selection,
            target=target,
            chat_session_id="chat-owned",
            expected_manifest_digest=manifest.digest,
            offset=first_page.end,
            limit=1_048_576,
            disclosure_message_id=verified.disclosure_message_id,
        )
        second_page = consumer.read_page(cookie, second_request)
        assert first_page.page_bytes + second_page.page_bytes == payload
        assert first_page.whole_sha256 == second_page.whole_sha256 == digest(payload)
        assert first_page.total_bytes == second_page.total_bytes == len(payload)
        assert first_page.end == second_page.offset

        db = session_factory()
        row = db.query(ChatMessage).filter(ChatMessage.id == str(verified.disclosure_message_id)).one()
        row.content = "tampered"
        db.commit()
        db.close()
        with pytest.raises(PlanningContextReaderError, match="disclosure_unverified"):
            consumer.read_page(cookie, first_request)
    finally:
        engine.dispose()


@pytest.mark.parametrize("drift", ("alias", "sid", "revision"))
def test_legacy_principal_rechecks_identity_and_revision_after_yield(native_reader_context, drift: str) -> None:
    context = native_reader_context
    binding = context["binding"]

    class CallbackManager:
        def __init__(self) -> None:
            self.alias = "fuzzy"
            self.sid = binding.owner_sid
            self.revision = binding.security_revision

        @contextmanager
        def guard_current_admin_subject(self, _owner_sid: str):
            yield self.alias

        def active_admin_subject_identity(self, *, subject_id: str):
            return self.alias, self.sid

        def security_action_revision(self):
            return self.revision

    manager = CallbackManager()
    producer = context["producer"]
    original_manager = producer._auth_manager
    object.__setattr__(producer, "_auth_manager", manager)
    try:
        with pytest.raises(PlanningContextReaderError, match="invalid_binding"):
            with producer._principal(binding):
                if drift == "alias":
                    manager.alias = "changed"
                elif drift == "sid":
                    manager.sid = "changed-sid"
                else:
                    manager.revision += 1
    finally:
        object.__setattr__(producer, "_auth_manager", original_manager)


def test_legacy_principal_accepts_unchanged_identity_and_revision_after_yield(native_reader_context) -> None:
    context = native_reader_context
    binding = context["binding"]

    class CallbackManager:
        @contextmanager
        def guard_current_admin_subject(self, _owner_sid: str):
            yield "fuzzy"

        def active_admin_subject_identity(self, *, subject_id: str):
            return "fuzzy", binding.owner_sid

        def security_action_revision(self):
            return binding.security_revision

    producer = context["producer"]
    original_manager = producer._auth_manager
    object.__setattr__(producer, "_auth_manager", CallbackManager())
    try:
        with producer._principal(binding) as (principal, alias):
            assert principal.owner_id == "fuzzy"
            assert alias == "fuzzy"
    finally:
        object.__setattr__(producer, "_auth_manager", original_manager)


@pytest.fixture
def native_reader_context(durable_planning_source):
    context = durable_planning_source
    source = context["source"]
    producer = PlanningContextManifestProducer(
        source._runtime, context["auth"], None, "harbour-one", durable_source=source,
    )
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    chat = context["binding"].chat_session_id
    with session_factory() as db:
        db.add(Session(
            id=chat, name="Native owned", endpoint_url="local", model="test", owner="fuzzy",
            message_count=0, created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
        ))
        db.commit()
    confirmation = PrivateContextConfirmationApp(session_factory, context["auth"], producer)
    with confirmation._authority.hold(context["cookie"], chat) as family_binding:
        selection = ContextSelection(tuple(sorted(source.discover(family_binding)[:2], key=lambda item: (
            item.source, item.selection_ref, item.revision_ref,
        ))))
    assert len(selection.items) == 2
    challenge = confirmation.issue_for_selection(context["cookie"], chat, selection)
    verified = confirmation.consume_challenge(context["cookie"], chat, challenge)
    consumer = PlanningContextPageConsumer(producer, context["auth"], confirmation, session_factory)
    try:
        yield {**context, "producer": producer, "confirmation": confirmation,
               "selection": selection, "verified": verified,
               "consumer": consumer, "session_factory": session_factory, "chat": chat}
    finally:
        engine.dispose()


def test_native_reader_real_full_selection_grant_pages_unicode_and_tamper(native_reader_context):
    context = native_reader_context
    selection = context["selection"]
    target = selection.items[0]
    producer = context["producer"]
    with context["confirmation"]._authority.hold(context["cookie"], context["chat"]) as family_binding:
        manifest = producer.capture(family_binding, selection)
        payload = producer.read_document(family_binding, target)
    assert any(byte > 127 for byte in payload)
    split = max(1, len(payload) // 2)
    first_request = PlanningContextPageRequest(
        selection, target, context["chat"], manifest.digest, 0, split,
        context["verified"].disclosure_message_id,
    )
    first = context["consumer"].read_page(context["cookie"], first_request)
    second = context["consumer"].read_page(context["cookie"], PlanningContextPageRequest(
        selection, target, context["chat"], manifest.digest, first.end, 1_048_576,
        context["verified"].disclosure_message_id,
    ))
    assert first.page_bytes + second.page_bytes == payload
    assert first.total_bytes == second.total_bytes == len(payload)
    assert first.whole_sha256 == second.whole_sha256 == digest(payload)
    assert first.page_sha256 == digest(first.page_bytes)
    assert second.page_sha256 == digest(second.page_bytes)
    assert first.manifest_digest == second.manifest_digest == manifest.digest
    assert first.end == second.offset
    with context["confirmation"]._authority.hold(context["cookie"], context["chat"]) as family_binding:
        metadata = producer.capture_metadata(family_binding, selection)
    assert tuple(item.selection for item in metadata) == selection.items
    assert metadata[0].selection is selection.items[0]
    assert metadata[0].durable_payload_sha256 == digest(payload)
    with pytest.raises(PlanningContextReaderError, match="disclosure_unverified"):
        context["consumer"].read_page("invalid-cookie", first_request)
    with context["session_factory"]() as db:
        row = db.query(ChatMessage).filter(
            ChatMessage.id == str(context["verified"].disclosure_message_id),
            ChatMessage.session_id == context["chat"],
        ).one()
        row.content = "tampered native confirmation"
        db.commit()
    with pytest.raises(PlanningContextReaderError, match="disclosure_unverified"):
        context["consumer"].read_page(context["cookie"], first_request)


def test_native_reader_page_authority_is_distinct_but_same_binding_family(native_reader_context):
    context = native_reader_context
    confirmation_authority = context["confirmation"]._authority
    page_authority = context["consumer"]._authority
    assert page_authority is not confirmation_authority
    assert page_authority._auth_manager is confirmation_authority._auth_manager is context["auth"]
    assert page_authority._key == confirmation_authority._key
    with confirmation_authority.hold(context["cookie"], context["chat"]) as grant_binding:
        with page_authority.hold(context["cookie"], context["chat"]) as page_binding:
            assert page_binding == grant_binding


def test_native_reader_actual_head_drift_denies_old_grant(native_reader_context):
    context = native_reader_context
    selection = context["selection"]
    target = selection.items[0]
    with context["confirmation"]._authority.hold(context["cookie"], context["chat"]) as family_binding:
        manifest = context["producer"].capture(family_binding, selection)
        _append_revision({**context, "binding": family_binding}, target,
                         title="Actual unapproved reader revision", key="reader-native-draft-0001")
    with pytest.raises(PlanningContextReaderError, match="planning_stale"):
        context["consumer"].read_page(context["cookie"], PlanningContextPageRequest(
            selection, target, context["chat"], manifest.digest, 0, 128,
            context["verified"].disclosure_message_id,
        ))


def test_native_reader_constructor_rejects_dual_or_mismatched_backends(durable_planning_source):
    context = durable_planning_source
    source = context["source"]
    runtime = source._runtime
    for manager, service, project, candidate in (
        (context["auth"], object(), "harbour-one", source),
        (object(), None, "harbour-one", source),
        (context["auth"], None, "foreign-project", source),
        (context["auth"], None, "harbour-one", object()),
    ):
        with pytest.raises(PlanningContextReaderError, match="invalid_context"):
            PlanningContextManifestProducer(runtime, manager, service, project, durable_source=candidate)
