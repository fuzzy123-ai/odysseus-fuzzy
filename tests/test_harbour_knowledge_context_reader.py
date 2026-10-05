from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace
from hashlib import sha256
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, ChatMessage, Session
from src.private_context_confirmation import ConfirmationError
from src.private_context_confirmation_app import (
    ContextSelection, CookieConfirmationAuthority, PrivateContextConfirmationApp, SourceSelection,
)
from src.harbour_knowledge_active_runtime import (
    KnowledgeContextError,
    KnowledgeContextManifestProducer,
    KnowledgeContextPageConsumer,
    KnowledgeContextPageRequest,
)
from src.harbour_knowledge_active_runtime import KnowledgeContextPage
from src.harbour_knowledge_store import HarbourKnowledgeStore
from src.unified_source_index_owner_scope import OwnerScope
from src.unified_source_index_sources.knowledge import project_harbour_knowledge_index_records
from tests.test_harbour_knowledge_active_runtime import _complete_fixture


@pytest.fixture
def context_fixture(tmp_path, monkeypatch):
    auth, subject, _owner_scope, _source_ids, composition, _key_path = _complete_fixture(
        tmp_path, monkeypatch,
    )
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = session_factory()
    db.add(Session(
        id="knowledge-chat", name="Knowledge", endpoint_url="local", model="test",
        owner="alice", message_count=0,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
    ))
    db.commit()
    db.close()
    yield auth, subject, composition, session_factory
    composition.close()
    engine.dispose()


@pytest.fixture
def public_context_fixture(tmp_path, monkeypatch):
    original_create = HarbourKnowledgeStore.create_object

    def create_public(store, *args, **kwargs):
        if kwargs.get("knowledge_id") == "active-note":
            kwargs["classification"] = "public"
        return original_create(store, *args, **kwargs)

    monkeypatch.setattr(HarbourKnowledgeStore, "create_object", create_public)
    auth, subject, _owner_scope, _source_ids, composition, _key_path = _complete_fixture(
        tmp_path, monkeypatch,
    )
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = session_factory()
    db.add(Session(
        id="public-knowledge-chat", name="Knowledge", endpoint_url="local", model="test",
        owner="alice", message_count=0,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
    ))
    db.commit()
    db.close()
    yield auth, subject, composition, session_factory
    composition.close()
    engine.dispose()


def _selection_and_digest(composition, auth, session_factory):
    from src.private_context_confirmation_app import CookieConfirmationAuthority

    authority = CookieConfirmationAuthority(auth)
    token = auth.create_session("alice", "Test-Password-930!")
    assert type(token) is str
    with authority.hold(token, "knowledge-chat") as binding:
        subject, _store, _principal, _generation, _selection_digest, _snapshot = composition._context_runtime(binding)
        index = composition._staging.selected_runtime(subject)[4].capture_index_snapshot(
            composition._context_runtime(binding)[2], owner_id=subject,
        )
        record = index.records[0]
        projection = project_harbour_knowledge_index_records(
            OwnerScope.for_subject_id(subject).value, record,
        )
        selection = ContextSelection((SourceSelection(
            "knowledge", projection.source.source_id,
            projection.source_version.source_version_id,
        ),))
    return token, selection


def _public_selection_and_digest(composition, auth, chat_id):
    from src.private_context_confirmation_app import CookieConfirmationAuthority

    authority = CookieConfirmationAuthority(auth)
    token = auth.create_session("alice", "Test-Password-930!")
    assert type(token) is str
    with authority.hold(token, chat_id) as binding:
        subject, _store, principal, _generation, _selection_digest, _snapshot = composition._context_runtime(binding)
        index = composition._staging.selected_runtime(subject)[4].capture_index_snapshot(
            principal, owner_id=subject,
        )
        record = index.records[0]
        projection = project_harbour_knowledge_index_records(
            OwnerScope.for_subject_id(subject).value, record,
        )
        selection = ContextSelection((SourceSelection(
            "knowledge", projection.source.source_id,
            projection.source_version.source_version_id,
        ),))
    return token, selection


def test_public_metadata_is_detached_and_redacted(context_fixture):
    auth, _subject, composition, session_factory = context_fixture
    token, selection = _selection_and_digest(composition, auth, session_factory)
    producer = KnowledgeContextManifestProducer(composition)
    from src.private_context_confirmation_app import CookieConfirmationAuthority

    authority = CookieConfirmationAuthority(auth)
    with authority.hold(token, "knowledge-chat") as binding:
        manifest = producer.capture_metadata(binding, selection)
    assert manifest.items[0].source == "knowledge"
    assert manifest.items[0].classification == "private"
    assert "KnowledgeContextManifest" in repr(manifest)
    assert "payload" not in repr(manifest).lower()


def test_private_page_requires_fresh_disclosure(context_fixture):
    auth, _subject, composition, session_factory = context_fixture
    token, selection = _selection_and_digest(composition, auth, session_factory)
    producer = KnowledgeContextManifestProducer(composition)
    confirmation = PrivateContextConfirmationApp(session_factory, auth, producer)
    consumer = KnowledgeContextPageConsumer(composition, auth, confirmation, session_factory)
    with consumer._authority.hold(token, "knowledge-chat") as binding:
        manifest = producer.capture_metadata(binding, selection)
    request = KnowledgeContextPageRequest(
        selection, selection.items[0], "knowledge-chat", manifest.digest, 0, 8,
    )
    with pytest.raises(ConfirmationError) as error:
        consumer.read_page(token, request)
    assert error.value.code == "invalid_context"


def test_private_page_returns_bounded_bytes_after_paired_confirmation(context_fixture):
    auth, _subject, composition, session_factory = context_fixture
    token, selection = _selection_and_digest(composition, auth, session_factory)
    producer = KnowledgeContextManifestProducer(composition)
    confirmation = PrivateContextConfirmationApp(session_factory, auth, producer)
    challenge = confirmation.issue_for_selection(token, "knowledge-chat", selection)
    verified = confirmation.consume_challenge(token, "knowledge-chat", challenge)
    consumer = KnowledgeContextPageConsumer(composition, auth, confirmation, session_factory)
    with consumer._authority.hold(token, "knowledge-chat") as binding:
        manifest = producer.capture_metadata(binding, selection)
    request = KnowledgeContextPageRequest(
        selection, selection.items[0], "knowledge-chat", manifest.digest, 0, 8,
        verified.disclosure_message_id,
    )
    page = consumer.read_page(token, request)
    assert type(page) is KnowledgeContextPage
    assert page.total >= page.end == len(page.payload_bytes)
    assert page.whole_sha256.startswith("sha256:")
    assert page.page_sha256.startswith("sha256:")


def test_private_page_rechecks_signed_disclosure_before_next_page(context_fixture):
    auth, _subject, composition, session_factory = context_fixture
    token, selection = _selection_and_digest(composition, auth, session_factory)
    producer = KnowledgeContextManifestProducer(composition)
    confirmation = PrivateContextConfirmationApp(session_factory, auth, producer)
    challenge = confirmation.issue_for_selection(token, "knowledge-chat", selection)
    verified = confirmation.consume_challenge(token, "knowledge-chat", challenge)
    consumer = KnowledgeContextPageConsumer(composition, auth, confirmation, session_factory)
    with consumer._authority.hold(token, "knowledge-chat") as binding:
        manifest = producer.capture_metadata(binding, selection)
    first_request = KnowledgeContextPageRequest(
        selection, selection.items[0], "knowledge-chat", manifest.digest, 0, 8,
        verified.disclosure_message_id,
    )
    first = consumer.read_page(token, first_request)
    assert first.payload_bytes
    db = session_factory()
    try:
        row = db.query(ChatMessage).filter(
            ChatMessage.id == str(verified.disclosure_message_id),
            ChatMessage.session_id == "knowledge-chat",
        ).one()
        row.content = "tampered confirmation"
        db.commit()
    finally:
        db.close()
    next_request = KnowledgeContextPageRequest(
        selection, selection.items[0], "knowledge-chat", manifest.digest, first.end, 8,
        verified.disclosure_message_id,
    )
    with pytest.raises(ConfirmationError):
        consumer.read_page(token, next_request)


def test_real_public_page_has_honest_multipage_hashes(public_context_fixture):
    auth, _subject, composition, session_factory = public_context_fixture
    token, selection = _public_selection_and_digest(
        composition, auth, "public-knowledge-chat",
    )
    producer = KnowledgeContextManifestProducer(composition)
    confirmation = PrivateContextConfirmationApp(session_factory, auth, producer)
    consumer = KnowledgeContextPageConsumer(composition, auth, confirmation, session_factory)
    with consumer._authority.hold(token, "public-knowledge-chat") as binding:
        manifest = producer.capture_metadata(binding, selection)
    first_request = KnowledgeContextPageRequest(
        selection, selection.items[0], "public-knowledge-chat", manifest.digest, 0, 5,
    )
    second_request = KnowledgeContextPageRequest(
        selection, selection.items[0], "public-knowledge-chat", manifest.digest, 5, 64,
    )
    first = consumer.read_page(token, first_request)
    page = consumer.read_page(token, second_request)
    expected = b"orbital blue memory"
    assert first.payload_bytes + page.payload_bytes == expected
    assert first.payload_bytes == expected[:5]
    assert page.payload_bytes == expected[5:]
    assert page.total == len(expected)
    assert first.offset == 0 and first.end == 5
    assert page.offset == 5 and page.end == len(expected)
    assert page.whole_sha256 == "sha256:" + sha256(expected).hexdigest()
    assert first.whole_sha256 == page.whole_sha256
    assert first.page_sha256 == "sha256:" + sha256(expected[:5]).hexdigest()
    assert page.page_sha256 == "sha256:" + sha256(expected[5:]).hexdigest()


def test_native_domain_allowlist_denies_context_capture(public_context_fixture):
    auth, _subject, composition, session_factory = public_context_fixture
    token, selection = _public_selection_and_digest(
        composition, auth, "public-knowledge-chat",
    )
    producer = KnowledgeContextManifestProducer(composition)
    confirmation = PrivateContextConfirmationApp(session_factory, auth, producer)
    consumer = KnowledgeContextPageConsumer(composition, auth, confirmation, session_factory)
    with consumer._authority.hold(token, "public-knowledge-chat") as binding:
        manifest = producer.capture_metadata(binding, selection)
    composition._config = replace(composition._config, allowed_domains=("planning",))
    request = KnowledgeContextPageRequest(
        selection, selection.items[0], "public-knowledge-chat", manifest.digest, 0, 5,
    )
    with pytest.raises(KnowledgeContextError) as error:
        consumer.read_page(token, request)
    assert error.value.code == "read_unavailable"


def test_selected_usi_parent_drift_is_denied(public_context_fixture, monkeypatch):
    auth, _subject, composition, session_factory = public_context_fixture
    token, selection = _public_selection_and_digest(
        composition, auth, "public-knowledge-chat",
    )
    producer = KnowledgeContextManifestProducer(composition)
    confirmation = PrivateContextConfirmationApp(session_factory, auth, producer)
    authority = consumer = KnowledgeContextPageConsumer(
        composition, auth, confirmation, session_factory,
    )._authority
    real_begin = composition._usi.begin_read

    class MissingParent:
        def __init__(self, read):
            self._read = read

        def get(self, *args, **kwargs):
            return None

        def close(self):
            self._read.close()

    monkeypatch.setattr(
        composition._usi, "begin_read",
        lambda snapshot=None: MissingParent(real_begin(snapshot)),
    )
    with authority.hold(token, "public-knowledge-chat") as binding:
        with pytest.raises(KnowledgeContextError) as error:
            producer.capture_metadata(binding, selection)
    assert error.value.code == "capture_unavailable"


def test_same_payload_old_manifest_is_rejected_on_store_selection_drift(public_context_fixture, monkeypatch):
    auth, _subject, composition, session_factory = public_context_fixture
    token, selection = _public_selection_and_digest(
        composition, auth, "public-knowledge-chat",
    )
    producer = KnowledgeContextManifestProducer(composition)
    confirmation = PrivateContextConfirmationApp(session_factory, auth, producer)
    consumer = KnowledgeContextPageConsumer(composition, auth, confirmation, session_factory)
    with consumer._authority.hold(token, "public-knowledge-chat") as binding:
        manifest = producer.capture_metadata(binding, selection)
    request = KnowledgeContextPageRequest(
        selection, selection.items[0], "public-knowledge-chat", manifest.digest, 0, 5,
    )
    selected_store = composition._staging.selected_runtime(_subject)[4]
    real_capture = selected_store.capture_index_snapshot
    calls = 0

    def drift_capture(*args, **kwargs):
        nonlocal calls
        calls += 1
        snapshot = real_capture(*args, **kwargs)
        if calls == 2:
            return replace(
                snapshot,
                captured_head_boundary="sha256:" + "f" * 64,
                snapshot_digest="sha256:" + "e" * 64,
            )
        return snapshot

    monkeypatch.setattr(selected_store, "capture_index_snapshot", drift_capture)
    with pytest.raises(KnowledgeContextError) as error:
        consumer.read_page(token, request)
    assert error.value.code == "read_unavailable"


def test_context_operation_keeps_close_waiting_until_recapture_finishes(public_context_fixture, monkeypatch):
    auth, _subject, composition, _session_factory = public_context_fixture
    token, selection = _public_selection_and_digest(
        composition, auth, "public-knowledge-chat",
    )
    producer = KnowledgeContextManifestProducer(composition)
    authority = CookieConfirmationAuthority(auth)
    entered = threading.Event()
    release = threading.Event()
    close_waiting = threading.Event()
    closed_before_release = threading.Event()
    nested_capture_entered = threading.Event()
    real_capture = composition._capture_context_material
    real_wait = composition._condition.wait

    def blocked_capture(binding, selected):
        entered.set()
        assert release.wait(2)
        with composition._context_operation():
            nested_capture_entered.set()
            return real_capture(binding, selected)

    monkeypatch.setattr(composition, "_capture_context_material", blocked_capture)

    def observed_wait(timeout=None):
        if composition._closed:
            close_waiting.set()
            closed_before_release.set()
        return real_wait(timeout)

    monkeypatch.setattr(composition._condition, "wait", observed_wait)

    def capture():
        with authority.hold(token, "public-knowledge-chat") as binding:
            producer.capture_metadata(binding, selection)

    def close():
        composition.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        capture_future = pool.submit(capture)
        assert entered.wait(2)
        close_future = pool.submit(close)
        assert close_waiting.wait(2)
        assert closed_before_release.is_set()
        assert not nested_capture_entered.is_set()
        assert not close_future.done()
        release.set()
        capture_future.result(timeout=5)
        assert nested_capture_entered.is_set()
        close_future.result(timeout=5)
