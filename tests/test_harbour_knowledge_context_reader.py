from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace
from hashlib import sha256
from concurrent.futures import ThreadPoolExecutor
import threading
from typing import Callable

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, ChatMessage, Session
from src.private_context_confirmation import ConfirmationError, DisclosureBinding, ManifestItem
from src.private_context_confirmation_app import (
    ContextSelection, CookieConfirmationAuthority, PrivateContextConfirmationApp, SourceSelection,
)
from src.harbour_knowledge_active_runtime import (
    ActiveKnowledgeComposition,
    KnowledgeContextError,
    KnowledgeContextManifestProducer,
    KnowledgeContextPageConsumer,
    KnowledgeContextPageRequest,
    read_native_knowledge_context_page,
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


@pytest.fixture
def large_public_context_fixture(tmp_path, monkeypatch):
    payload = b"0123456789abcdef" * 1024 + b"x"
    original_create = HarbourKnowledgeStore.create_object

    def create_large_public(store, *args, **kwargs):
        if kwargs.get("knowledge_id") == "active-note":
            kwargs["classification"] = "public"
            kwargs["payload_bytes"] = payload
        return original_create(store, *args, **kwargs)

    monkeypatch.setattr(HarbourKnowledgeStore, "create_object", create_large_public)
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
        id="large-public-knowledge-chat", name="Knowledge", endpoint_url="local", model="test",
        owner="alice", message_count=0,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
    ))
    db.commit()
    db.close()
    yield auth, subject, composition, session_factory, payload
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


def _different_sha(value: str, digit: str) -> str:
    candidate = digit * 64
    raw = value[7:] if value.startswith("sha256:") else value
    return candidate if candidate != raw else (("f" if digit != "f" else "e") * 64)


def _recipient_item(native_item) -> ManifestItem:
    return ManifestItem(
        native_item.source,
        native_item.selection_ref,
        native_item.revision_ref,
        _different_sha(native_item.boundary_sha256, "a"),
        _different_sha(native_item.record_sha256, "b"),
        native_item.payload_sha256,
        native_item.classification,
        native_item.provenance_ref,
    )


def _native_arguments(composition, binding, selection) -> dict[str, object]:
    captured = composition._capture_context_material(binding, selection)
    target = selection.items[0]
    native_item = next(
        item for item in captured.manifest.items
        if item.selection_ref == target.selection_ref
        and item.revision_ref == target.revision_ref
    )
    return {
        "composition": composition,
        "binding": binding,
        "knowledge_selection": selection,
        "target": target,
        "expected_item": _recipient_item(native_item),
        "expected_manifest_digest": captured.manifest.digest,
        "offset": 0,
        "page_bytes": 8,
    }


class _StringSubclass(str):
    pass


class _IntegerSubclass(int):
    pass


class _CompositionSubclass(ActiveKnowledgeComposition):
    pass


class _BindingSubclass(DisclosureBinding):
    pass


class _ContextSelectionSubclass(ContextSelection):
    pass


class _SourceSelectionSubclass(SourceSelection):
    pass


class _ManifestItemSubclass(ManifestItem):
    pass


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


def test_native_page_reads_two_exact_stored_pages_with_honest_hashes(public_context_fixture):
    auth, _subject, composition, _session_factory = public_context_fixture
    token, selection = _public_selection_and_digest(
        composition, auth, "public-knowledge-chat",
    )
    expected = b"orbital blue memory"
    with CookieConfirmationAuthority(auth).hold(token, "public-knowledge-chat") as binding:
        arguments = _native_arguments(composition, binding, selection)
        arguments["page_bytes"] = 7
        first = read_native_knowledge_context_page(**arguments)
        arguments["offset"] = first.end
        arguments["page_bytes"] = 16_384
        second = read_native_knowledge_context_page(**arguments)
    assert first.payload_bytes == expected[:7]
    assert second.payload_bytes == expected[7:]
    assert first.payload_bytes + second.payload_bytes == expected
    assert first.whole_sha256 == second.whole_sha256 == "sha256:" + sha256(expected).hexdigest()
    assert first.page_sha256 == "sha256:" + sha256(first.payload_bytes).hexdigest()
    assert second.page_sha256 == "sha256:" + sha256(second.payload_bytes).hexdigest()
    assert first.manifest_digest == second.manifest_digest == arguments["expected_manifest_digest"]
    assert first.byteform == second.byteform == "knowledge_payload"


def test_native_page_allows_exact_eof_and_denies_past_eof(public_context_fixture):
    auth, _subject, composition, _session_factory = public_context_fixture
    token, selection = _public_selection_and_digest(
        composition, auth, "public-knowledge-chat",
    )
    total = len(b"orbital blue memory")
    with CookieConfirmationAuthority(auth).hold(token, "public-knowledge-chat") as binding:
        arguments = _native_arguments(composition, binding, selection)
        arguments["offset"] = total
        eof = read_native_knowledge_context_page(**arguments)
        arguments["offset"] = total + 1
        with pytest.raises(KnowledgeContextError, match="^read_unavailable$") as failure:
            read_native_knowledge_context_page(**arguments)
    assert eof.payload_bytes == b""
    assert eof.offset == eof.end == eof.total == total
    assert eof.page_sha256 == "sha256:" + sha256(b"").hexdigest()
    assert failure.value.code == "read_unavailable"


def test_native_16k_limit_and_legacy_one_megabyte_limit_remain_distinct(
    large_public_context_fixture,
):
    auth, _subject, composition, session_factory, payload = large_public_context_fixture
    token, selection = _public_selection_and_digest(
        composition, auth, "large-public-knowledge-chat",
    )
    with CookieConfirmationAuthority(auth).hold(
        token, "large-public-knowledge-chat",
    ) as binding:
        arguments = _native_arguments(composition, binding, selection)
        arguments["page_bytes"] = 16_384
        native = read_native_knowledge_context_page(**arguments)
        arguments["page_bytes"] = 16_385
        with pytest.raises(KnowledgeContextError, match="^read_unavailable$"):
            read_native_knowledge_context_page(**arguments)
    producer = KnowledgeContextManifestProducer(composition)
    confirmation = PrivateContextConfirmationApp(session_factory, auth, producer)
    consumer = KnowledgeContextPageConsumer(composition, auth, confirmation, session_factory)
    legacy = consumer.read_page(
        token,
        KnowledgeContextPageRequest(
            selection,
            selection.items[0],
            "large-public-knowledge-chat",
            arguments["expected_manifest_digest"],
            0,
            1_000_000,
        ),
    )
    assert native.payload_bytes == payload[:16_384]
    assert native.end == 16_384
    assert legacy.payload_bytes == payload
    assert legacy.end == legacy.total == len(payload)
    assert legacy.byteform == "knowledge_payload"


def _binding_subclass(_arguments, binding):
    return _BindingSubclass(
        binding.owner_sid,
        binding.chat_session_id,
        binding.auth_session_binding,
        binding.security_revision,
        binding.audience,
    )


def _selection_subclass(arguments, _binding):
    return _ContextSelectionSubclass(arguments["knowledge_selection"].items)


def _target_subclass(arguments, _binding):
    target = arguments["target"]
    return _SourceSelectionSubclass(
        target.source, target.selection_ref, target.revision_ref, target.forge_locator,
    )


def _item_subclass(arguments, _binding):
    item = arguments["expected_item"]
    return _ManifestItemSubclass(
        item.source,
        item.selection_ref,
        item.revision_ref,
        item.boundary_sha256,
        item.record_sha256,
        item.payload_sha256,
        item.classification,
        item.provenance_ref,
    )


def _forged_binding_scalar(_arguments, binding):
    forged = replace(binding)
    object.__setattr__(forged, "security_revision", True)
    return forged


def _forged_target_scalar(arguments, _binding):
    target = replace(arguments["target"])
    object.__setattr__(target, "selection_ref", _StringSubclass(target.selection_ref))
    return target


def _forged_item_scalar(arguments, _binding):
    item = replace(arguments["expected_item"])
    object.__setattr__(item, "provenance_ref", _StringSubclass(item.provenance_ref))
    return item


@pytest.mark.parametrize(
    ("field", "value_factory"),
    (
        pytest.param(
            "composition", lambda _arguments, _binding: object.__new__(_CompositionSubclass),
            id="composition-subclass",
        ),
        pytest.param("binding", _binding_subclass, id="binding-subclass"),
        pytest.param("knowledge_selection", _selection_subclass, id="selection-subclass"),
        pytest.param("target", _target_subclass, id="target-subclass"),
        pytest.param("expected_item", _item_subclass, id="manifest-item-subclass"),
        pytest.param(
            "expected_manifest_digest",
            lambda arguments, _binding: _StringSubclass(arguments["expected_manifest_digest"]),
            id="manifest-digest-string-subclass",
        ),
        pytest.param("offset", lambda _arguments, _binding: True, id="offset-bool"),
        pytest.param("offset", lambda _arguments, _binding: _IntegerSubclass(0), id="offset-int-subclass"),
        pytest.param("offset", lambda _arguments, _binding: (1 << 53), id="offset-over-json-limit"),
        pytest.param("page_bytes", lambda _arguments, _binding: True, id="page-bytes-bool"),
        pytest.param("page_bytes", lambda _arguments, _binding: _IntegerSubclass(1), id="page-bytes-int-subclass"),
        pytest.param("page_bytes", lambda _arguments, _binding: 0, id="page-bytes-zero"),
        pytest.param("page_bytes", lambda _arguments, _binding: 16_385, id="page-bytes-over-native-limit"),
        pytest.param("binding", _forged_binding_scalar, id="forged-binding-scalar"),
        pytest.param("target", _forged_target_scalar, id="forged-target-scalar"),
        pytest.param("expected_item", _forged_item_scalar, id="forged-item-scalar"),
    ),
)
def test_native_entrypoint_rejects_nonexact_types_and_hostile_scalars(
    public_context_fixture,
    field: str,
    value_factory: Callable[[dict[str, object], DisclosureBinding], object],
):
    auth, _subject, composition, _session_factory = public_context_fixture
    token, selection = _public_selection_and_digest(
        composition, auth, "public-knowledge-chat",
    )
    with CookieConfirmationAuthority(auth).hold(token, "public-knowledge-chat") as binding:
        arguments = _native_arguments(composition, binding, selection)
        arguments[field] = value_factory(arguments, binding)
        with pytest.raises(KnowledgeContextError, match="^read_unavailable$") as failure:
            read_native_knowledge_context_page(**arguments)
    assert failure.value.code == "read_unavailable"


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        pytest.param("source", "planning", id="source"),
        pytest.param("selection_ref", "knowledge.source.drift", id="selection-ref"),
        pytest.param("revision_ref", "knowledge.revision.drift", id="revision-ref"),
        pytest.param("payload_sha256", "c" * 64, id="payload-sha256"),
        pytest.param("classification", "private", id="classification"),
        pytest.param("provenance_ref", "knowledge.provenance.drift", id="provenance-ref"),
    ),
)
def test_native_entrypoint_denies_each_recipient_item_invariant_drift(
    public_context_fixture, field: str, replacement: str,
):
    auth, _subject, composition, _session_factory = public_context_fixture
    token, selection = _public_selection_and_digest(
        composition, auth, "public-knowledge-chat",
    )
    with CookieConfirmationAuthority(auth).hold(token, "public-knowledge-chat") as binding:
        arguments = _native_arguments(composition, binding, selection)
        arguments["expected_item"] = replace(arguments["expected_item"], **{field: replacement})
        with pytest.raises(KnowledgeContextError, match="^read_unavailable$"):
            read_native_knowledge_context_page(**arguments)


@pytest.mark.parametrize(
    "digest_field",
    (
        pytest.param("boundary_sha256", id="boundary-sha256"),
        pytest.param("record_sha256", id="record-sha256"),
    ),
)
def test_native_entrypoint_requires_recipient_rebound_hashes(
    public_context_fixture, digest_field: str,
):
    auth, _subject, composition, _session_factory = public_context_fixture
    token, selection = _public_selection_and_digest(
        composition, auth, "public-knowledge-chat",
    )
    with CookieConfirmationAuthority(auth).hold(token, "public-knowledge-chat") as binding:
        arguments = _native_arguments(composition, binding, selection)
        captured = composition._capture_context_material(binding, selection)
        native_item = captured.manifest.items[0]
        arguments["expected_item"] = replace(
            arguments["expected_item"],
            **{digest_field: getattr(native_item, digest_field)},
        )
        with pytest.raises(KnowledgeContextError, match="^read_unavailable$"):
            read_native_knowledge_context_page(**arguments)


def test_native_entrypoint_rejects_wrong_context_inputs(public_context_fixture):
    auth, _subject, composition, _session_factory = public_context_fixture
    token, selection = _public_selection_and_digest(
        composition, auth, "public-knowledge-chat",
    )
    with CookieConfirmationAuthority(auth).hold(token, "public-knowledge-chat") as binding:
        base = _native_arguments(composition, binding, selection)
        wrong_target = SourceSelection(
            "knowledge", selection.items[0].selection_ref + ".other",
            selection.items[0].revision_ref,
        )
        cases = (
            {"composition": object.__new__(ActiveKnowledgeComposition)},
            {"binding": replace(binding, chat_session_id="other-public-knowledge-chat")},
            {"knowledge_selection": ContextSelection((wrong_target,)), "target": wrong_target},
            {"target": wrong_target},
            {"expected_manifest_digest": "0" * 64},
        )
        for changes in cases:
            arguments = dict(base)
            arguments.update(changes)
            with pytest.raises(KnowledgeContextError, match="^read_unavailable$"):
                read_native_knowledge_context_page(**arguments)


def _drift_capture_manifest(capture):
    return replace(
        capture,
        manifest=replace(capture.manifest, binding_digest="sha256:" + "3" * 64),
    )


def _drift_capture_head(capture):
    return replace(
        capture,
        manifest=replace(capture.manifest, captured_head_boundary="sha256:" + "4" * 64),
    )


def _drift_capture_generation(capture):
    return replace(capture, canonical_generation="generation-drift")


def _drift_capture_usi(capture):
    return replace(
        capture,
        manifest=replace(capture.manifest, usi_snapshot_digest="sha256:" + "5" * 64),
    )


def _drift_capture_selection(capture):
    return replace(capture, selection_digest="sha256:" + "6" * 64)


def _drift_capture_store(capture):
    return replace(capture, store=object.__new__(HarbourKnowledgeStore))


def _drift_capture_entry(capture):
    return replace(
        capture,
        entries=(replace(capture.entries[0], generation=capture.entries[0].generation + 1),),
    )


@pytest.mark.parametrize(
    "drift",
    (
        pytest.param(_drift_capture_manifest, id="manifest"),
        pytest.param(_drift_capture_head, id="head"),
        pytest.param(_drift_capture_generation, id="generation"),
        pytest.param(_drift_capture_usi, id="usi"),
        pytest.param(_drift_capture_selection, id="store-selection"),
        pytest.param(_drift_capture_store, id="store"),
        pytest.param(_drift_capture_entry, id="entry-generation"),
    ),
)
def test_native_entrypoint_denies_before_after_native_material_drift(
    public_context_fixture, monkeypatch, drift: Callable[[object], object],
):
    auth, _subject, composition, _session_factory = public_context_fixture
    token, selection = _public_selection_and_digest(
        composition, auth, "public-knowledge-chat",
    )
    with CookieConfirmationAuthority(auth).hold(token, "public-knowledge-chat") as binding:
        arguments = _native_arguments(composition, binding, selection)
        original = composition._capture_context_material
        calls = 0

        def drift_final(held_binding, selected):
            nonlocal calls
            calls += 1
            captured = original(held_binding, selected)
            return drift(captured) if calls == 2 else captured

        monkeypatch.setattr(composition, "_capture_context_material", drift_final)
        with pytest.raises(KnowledgeContextError, match="^read_unavailable$"):
            read_native_knowledge_context_page(**arguments)
    assert calls == 2


@pytest.mark.parametrize(
    "mode",
    (
        pytest.param("short", id="short"),
        pytest.param("different-content", id="different-content"),
    ),
)
def test_native_entrypoint_rejects_exact_bytes_with_wrong_payload_hash(
    public_context_fixture, monkeypatch, mode: str,
):
    auth, _subject, composition, _session_factory = public_context_fixture
    token, selection = _public_selection_and_digest(
        composition, auth, "public-knowledge-chat",
    )
    original = composition._read_context_payload

    def corrupt_payload(binding, entry):
        payload = original(binding, entry)
        if mode == "short":
            return payload[:-1]
        return bytes((payload[0] ^ 1,)) + payload[1:]

    monkeypatch.setattr(composition, "_read_context_payload", corrupt_payload)
    with CookieConfirmationAuthority(auth).hold(token, "public-knowledge-chat") as binding:
        arguments = _native_arguments(composition, binding, selection)
        with pytest.raises(KnowledgeContextError, match="^read_unavailable$"):
            read_native_knowledge_context_page(**arguments)


def test_native_entrypoint_rejects_forged_exact_manifest_material(
    public_context_fixture, monkeypatch,
):
    auth, _subject, composition, _session_factory = public_context_fixture
    token, selection = _public_selection_and_digest(
        composition, auth, "public-knowledge-chat",
    )
    with CookieConfirmationAuthority(auth).hold(token, "public-knowledge-chat") as binding:
        arguments = _native_arguments(composition, binding, selection)
        original = composition._capture_context_material

        def forged_capture(held_binding, selected):
            captured = original(held_binding, selected)
            forged_manifest = replace(
                captured.manifest, binding_digest="sha256:" + "7" * 64,
            )
            object.__setattr__(
                forged_manifest, "_digest_value", captured.manifest.digest,
            )
            return replace(captured, manifest=forged_manifest)

        monkeypatch.setattr(composition, "_capture_context_material", forged_capture)
        with pytest.raises(KnowledgeContextError, match="^read_unavailable$"):
            read_native_knowledge_context_page(**arguments)
