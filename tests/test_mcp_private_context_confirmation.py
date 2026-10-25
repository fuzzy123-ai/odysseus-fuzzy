"""Native MCP consent tests using the accepted three-domain fixture."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import threading

import pytest

import src.mcp_private_context_confirmation as confirmation_module
from src.mcp_private_context_confirmation import (
    McpPrivateContextConfirmationError,
    McpPrivateContextConfirmationService,
    Permit,
)
from src.private_context_confirmation_app import CookieConfirmationAuthority
from tests.test_mcp_context_metadata import _lease, native_mcp_fixture


def _service(fixture, *, wall_time=None, monotonic=None):
    return McpPrivateContextConfirmationService(
        auth_manager=fixture["auth"], authority=fixture["authority"],
        producer=fixture["combined"],
        cookie_authority=CookieConfirmationAuthority(fixture["auth"]),
        session_factory=fixture["session_factory"], monotonic=monotonic or (lambda: 100.0),
        wall_time=wall_time or (lambda: datetime.now(timezone.utc)),
    )


class _ReleaseEarlierSampleOnLaterLockAttempt:
    def __init__(self, release_earlier):
        self._lock = threading.RLock()
        self._release_earlier = release_earlier

    def __enter__(self):
        if threading.current_thread().name == "later-clock-sample":
            if self._lock.acquire(blocking=False):
                self._release_earlier.set()
                return self
            self._release_earlier.set()
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._lock.release()


def test_real_intent_is_cookie_free_until_browser_attachment(native_mcp_fixture):
    fixture = native_mcp_fixture
    lease = _lease(fixture, "consent-intent")
    service = _service(fixture)
    try:
        intent = service.prepare_intent(lease, fixture["selection"])
        assert intent.private_required is True
        assert intent.item_count == len(fixture["selection"].items)
    finally:
        service.close()
        fixture["authority"].release_read_lease(lease)


def test_prepare_uses_two_fresh_dual_planning_images(native_mcp_fixture, monkeypatch):
    import json
    import src.planning_revision_store as store_module

    fixture = native_mcp_fixture
    lease = _lease(fixture, "dual-prepare")
    second_lease = None
    service = _service(fixture)
    store = fixture["durable_source"]._store
    composition = fixture["knowledge_composition"]
    original_materialize = store._materialize_accepted_migration_locked
    original_validate = store_module.validate_planning_definition
    original_verify = store_module._verify_harbour_state
    original_read = Path.read_bytes
    revision_count = len(json.loads(store._path.read_bytes())["revisions"])
    materializations, validations, verifications, physical_reads = [], [], [], []

    def materialize(**kwargs):
        materializations.append((
            kwargs["batch"]["batch_id"],
            composition._inflight,
            getattr(composition._context_local, "depth", 0),
            getattr(store._planning_validation_pair_local, "scope", None),
        ))
        return original_materialize(**kwargs)

    def validate(document):
        validations.append(1)
        return original_validate(document)

    def verify(state):
        verifications.append(1)
        return original_verify(state)

    def read_bytes(path):
        if path == store._path:
            physical_reads.append(1)
        return original_read(path)

    monkeypatch.setattr(
        store, "_materialize_accepted_migration_locked", materialize,
    )
    monkeypatch.setattr(
        store, "read_subject_bound_accepted_migration_bundle",
        lambda *_args, **_kwargs: pytest.fail("mixed checkpoint used legacy Planning read"),
    )
    monkeypatch.setattr(store_module, "validate_planning_definition", validate)
    monkeypatch.setattr(store_module, "_verify_harbour_state", verify)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    try:
        prepared = service.prepare(
            lease, fixture["cookie"], fixture["selection"], page_bytes=1024,
        )
        assert prepared.item_count == len(fixture["selection"].items)
        assert len(materializations) == 2
        assert len(validations) == revision_count
        assert len(verifications) == 2
        assert len(physical_reads) == 4
        assert all(
            inflight == 1 and depth >= 3 and scope is not None
            for _batch, inflight, depth, scope in materializations
        )
        assert composition._inflight == 0
        assert getattr(composition._context_local, "depth", 0) == 0
        assert getattr(store._planning_validation_pair_local, "scope", None) is None

        service.revoke(prepared.request_id)
        second_lease = _lease(fixture, "dual-prepare-second", token_index=1)
        service.prepare(
            second_lease, fixture["cookie"], fixture["selection"], page_bytes=1024,
        )
        assert len(materializations) == 4
        assert len(validations) == revision_count * 2
        assert len(verifications) == 4
        assert len(physical_reads) == 8
    finally:
        service.close()
        fixture["authority"].release_read_lease(lease)
        if second_lease is not None:
            fixture["authority"].release_read_lease(second_lease)


@pytest.mark.parametrize("failure_stage", ["sample1", "sample2"])
def test_dual_checkpoint_scope_unwinds_at_each_failure_boundary(
    native_mcp_fixture, failure_stage,
):
    fixture = native_mcp_fixture
    lease = _lease(fixture, "dual-unwind-" + failure_stage)
    combined = fixture["combined"]
    composition = fixture["knowledge_composition"]
    store = fixture["durable_source"]._store
    try:
        with pytest.raises(RuntimeError, match=failure_stage):
            with combined._dual_sample_pair():
                assert composition._inflight == 1
                assert getattr(composition._context_local, "depth", 0) == 1
                assert getattr(
                    store._planning_validation_pair_local, "scope", None,
                ) is not None
                with CookieConfirmationAuthority(fixture["auth"]).hold(
                    fixture["cookie"], fixture["chat_id"],
                ) as binding:
                    combined.capture_dual_snapshot(
                        binding, lease, fixture["selection"],
                    )
                    assert composition._inflight == 1
                    assert getattr(composition._context_local, "depth", 0) == 1
                    if failure_stage == "sample1":
                        raise RuntimeError("sample1")
                    combined.capture_dual_snapshot(
                        binding, lease, fixture["selection"],
                    )
                    raise RuntimeError("sample2")
        assert composition._inflight == 0
        assert getattr(composition._context_local, "depth", 0) == 0
        assert getattr(
            store._planning_validation_pair_local, "scope", None,
        ) is None
    finally:
        fixture["authority"].release_read_lease(lease)


def test_dual_checkpoint_reservation_defers_concurrent_knowledge_teardown(
    generated_id_app, monkeypatch,
):
    from fastapi.testclient import TestClient
    from tests.test_app_mcp_private_context_confirmation import (
        SESSION_COOKIE,
        _assert_success,
        _headers,
    )

    close_started = threading.Event()
    close_finished = threading.Event()
    closer = None

    with TestClient(generated_id_app) as client:
        fixture = client.app.state.fixture
        service = client.app.state.service
        composition = fixture["knowledge_composition"]
        prepared = _assert_success(client.post(
            "/test/prepare",
            headers={"Authorization": "Bearer " + fixture["mcp_raw_tokens"][0]},
        )).json()
        request_id = prepared["request_id"]
        client.cookies.set(SESSION_COOKIE, fixture["cookie"])
        challenge = _assert_success(client.post(
            "/api/context/mcp/challenge",
            headers=_headers(),
            json={"request_id": request_id},
        )).json()["token"]
        _assert_success(client.post(
            "/api/chat",
            headers=_headers(),
            json={
                "message": "/mcp-context-disclose " + challenge,
                "session": "chat-owned",
            },
        ))
        original_capture = service._capture_dual
        captures = []

        def close_composition():
            close_started.set()
            composition.close()
            close_finished.set()

        def capture(binding, lease, selection):
            nonlocal closer
            if captures:
                assert close_started.is_set()
                assert not close_finished.is_set()
            result = original_capture(binding, lease, selection)
            captures.append("sample")
            if len(captures) == 1:
                closer = threading.Thread(
                    target=close_composition,
                    name="dual-checkpoint-knowledge-close",
                )
                closer.start()
                assert close_started.wait(timeout=5)
                assert not close_finished.wait(timeout=0.1)
            return result

        monkeypatch.setattr(service, "_capture_dual", capture)
        permit = service.issue_permit(request_id)
        assert type(permit) is Permit
        assert captures == ["sample", "sample"]
        assert close_finished.wait(timeout=5)
        assert closer is not None
        closer.join(timeout=5)
        assert not closer.is_alive()
        assert composition._inflight == 0


def test_content_admission_returns_the_fresh_sample_bound_to_the_exact_request(
    generated_id_app, monkeypatch,
):
    from fastapi.testclient import TestClient
    from src.context_manifest_producer import CombinedContextSnapshot
    from tests.test_mcp_context_native_page import _authorize

    with TestClient(generated_id_app) as client:
        request_id = _authorize(client)
        fixture = client.app.state.fixture
        service = client.app.state.service
        lease = client.app.state.lease
        selection = fixture["selection"]
        entry = service._entries[request_id]
        before = _entry_snapshot(entry)
        retained_snapshot = entry.mcp_snapshot
        observed = []
        original_capture = service._capture_dual

        def capture(binding, current_lease, current_selection):
            result = original_capture(binding, current_lease, current_selection)
            observed.append(result)
            return result

        monkeypatch.setattr(service, "_capture_dual", capture)
        admitted = service._issue_captured_permit(
            request_id, lease, selection, entry.page_bytes,
        )

        assert type(admitted) is tuple and len(admitted) == 2
        permit, fresh_snapshot = admitted
        assert type(permit) is Permit
        assert type(fresh_snapshot) is CombinedContextSnapshot
        assert len(observed) == 2
        assert fresh_snapshot is observed[0][1]
        assert fresh_snapshot == observed[1][1]
        assert fresh_snapshot == retained_snapshot and fresh_snapshot is not retained_snapshot
        assert service._reject_native_permit(permit) is True
        assert request_id not in service._entries
        assert _entry_fully_scrubbed(entry, before)


@pytest.mark.parametrize(
    "mismatch",
    [
        pytest.param("wrong_lease", id="wrong-lease"),
        pytest.param("reordered_selection", id="reordered-selection"),
        pytest.param("different_descriptor", id="different-page-descriptor"),
    ],
)
def test_content_admission_rejects_exact_request_mismatch_before_body_capture(
    generated_id_app, monkeypatch, mismatch,
):
    from fastapi.testclient import TestClient
    from tests.test_mcp_context_native_page import _authorize

    wrong_lease = None
    mutated_item = None
    original_source = None
    original_selection_items = None
    with TestClient(generated_id_app) as client:
        request_id = _authorize(client)
        fixture = client.app.state.fixture
        service = client.app.state.service
        lease = client.app.state.lease
        selection = fixture["selection"]
        entry = service._entries[request_id]
        before = _entry_snapshot(entry)
        calls = []

        def body_capture(*_args, **_kwargs):
            calls.append("capture")
            pytest.fail("body capture ran before exact admission")

        monkeypatch.setattr(service, "_capture_dual", body_capture)
        candidate_lease = lease
        candidate_selection = selection
        candidate_page_bytes = entry.page_bytes
        if mismatch == "wrong_lease":
            wrong_lease = _lease(fixture, "content-admission-wrong-lease", token_index=1)
            candidate_lease = wrong_lease
        elif mismatch == "reordered_selection":
            original_selection_items = selection.items
            object.__setattr__(
                selection, "items", tuple(reversed(original_selection_items)),
            )
        else:
            mutated_item = selection.items[0]
            original_source = mutated_item.source
            object.__setattr__(
                mutated_item, "source",
                "knowledge" if original_source != "knowledge" else "forge",
            )

        try:
            with pytest.raises(McpPrivateContextConfirmationError) as rejected:
                service._issue_captured_permit(
                    request_id, candidate_lease, candidate_selection, candidate_page_bytes,
                )
            assert rejected.value.code == "invalid_binding"
            assert calls == []
            assert request_id not in service._entries
            assert _entry_fully_scrubbed(entry, before)
        finally:
            if original_selection_items is not None:
                object.__setattr__(selection, "items", original_selection_items)
            if mutated_item is not None:
                object.__setattr__(mutated_item, "source", original_source)
            if wrong_lease is not None:
                fixture["authority"].release_read_lease(wrong_lease)


@pytest.mark.parametrize(
    "target",
    [
        "checkpoint_pair", "knowledge_operation",
        "acceptance_pair_class", "acceptance_pair_instance",
        "content_capture_class", "content_capture_instance",
        "content_revalidation_class", "content_revalidation_instance",
        "producer_content_class",
    ],
)
def test_dual_checkpoint_rejects_new_scope_helper_substitution_without_execution(
    native_mcp_fixture, monkeypatch, target,
):
    fixture = native_mcp_fixture
    lease = _lease(fixture, "dual-scope-pin-" + target)
    service = _service(fixture)
    fake_calls = []

    def hostile(*_args, **_kwargs):
        fake_calls.append(target)
        raise AssertionError("hostile checkpoint helper executed")

    if target == "checkpoint_pair":
        owner_type = type(fixture["combined"])
        member = "_dual_sample_pair"
        monkeypatch.setattr(owner_type, member, hostile)
    elif target == "knowledge_operation":
        owner_type = type(fixture["knowledge_composition"])
        member = "_context_operation"
        monkeypatch.setattr(owner_type, member, hostile)
    elif target == "acceptance_pair_class":
        monkeypatch.setattr(
            McpPrivateContextConfirmationService, "_pair", staticmethod(hostile),
        )
    elif target == "acceptance_pair_instance":
        monkeypatch.setattr(service, "_pair", hostile)
    elif target == "content_capture_class":
        monkeypatch.setattr(
            McpPrivateContextConfirmationService, "_capture_dual_content", hostile,
        )
    elif target == "content_capture_instance":
        monkeypatch.setattr(service, "_capture_dual_content", hostile)
    elif target == "content_revalidation_class":
        monkeypatch.setattr(
            McpPrivateContextConfirmationService,
            "_revalidate_planning_content_under_guard", hostile,
        )
    elif target == "content_revalidation_instance":
        monkeypatch.setattr(
            service, "_revalidate_planning_content_under_guard", hostile,
        )
    else:
        monkeypatch.setattr(
            type(fixture["combined"]), "capture_dual_snapshot_content", hostile,
        )
    try:
        with pytest.raises(McpPrivateContextConfirmationError) as rejected:
            service.prepare(
                lease, fixture["cookie"], fixture["selection"], page_bytes=1024,
            )
        assert rejected.value.code == "invalid_binding"
        assert fake_calls == []
        assert not service._entries
        assert not service._permits and not service._exchanges
        assert not service._completions and not service._native_completions
    finally:
        service.close()
        fixture["authority"].release_read_lease(lease)


@pytest.mark.parametrize("slot", ["class", "instance"])
def test_acceptance_pair_transient_provider_substitution_never_executes_or_publishes(
    native_mcp_fixture, monkeypatch, slot,
):
    fixture = native_mcp_fixture
    lease = _lease(fixture, "dual-acceptance-pair-transient-" + slot)
    service = _service(fixture)
    knowledge_type = type(fixture["knowledge_producer"])
    original_mcp = knowledge_type.capture_metadata_mcp
    mcp_calls, fake_calls = [], []

    def hostile_pair(*_args, **_kwargs):
        fake_calls.append(slot)
        raise AssertionError("hostile acceptance pair executed")

    def mcp_capture(self, admission, metadata_binding, selection):
        result = original_mcp(self, admission, metadata_binding, selection)
        mcp_calls.append("mcp")
        if len(mcp_calls) == 2:
            if slot == "class":
                monkeypatch.setattr(
                    McpPrivateContextConfirmationService,
                    "_pair",
                    staticmethod(hostile_pair),
                )
            else:
                monkeypatch.setattr(service, "_pair", hostile_pair)
        return result

    monkeypatch.setattr(knowledge_type, "capture_metadata_mcp", mcp_capture)
    try:
        with pytest.raises(McpPrivateContextConfirmationError) as rejected:
            service.prepare(
                lease, fixture["cookie"], fixture["selection"], page_bytes=1024,
            )
        assert rejected.value.code == "invalid_binding"
        assert mcp_calls == ["mcp", "mcp"]
        assert fake_calls == []
        assert not service._entries
        assert not service._permits and not service._exchanges
        assert not service._completions and not service._native_completions
    finally:
        service.close()
        fixture["authority"].release_read_lease(lease)


def test_m1_cookie_source_mutation_is_rejected_before_c2_publication(
    native_mcp_fixture, monkeypatch,
):
    fixture = native_mcp_fixture
    lease = _lease(fixture, "dual-m1-cookie-mutation")
    service = _service(fixture)
    store = fixture["durable_source"]._store
    original_fence = store._assert_retained_image_locked
    calls = []

    def mutate_knowledge(binding, suffix):
        _subject, store, principal, *_rest = fixture[
            "knowledge_composition"
        ]._context_runtime(binding)
        store.create_object(
            principal,
            owner_id=binding.owner_sid,
            knowledge_id="dual-sample-" + suffix,
            knowledge_kind="note",
            payload_bytes=("dual sample " + suffix).encode(),
            payload_schema_id="harbour.note.v1",
            payload_media_type="text/plain",
            classification="private",
            retention={
                "retention_class": "user_managed",
                "deletion_mode": "tombstone_only",
                "retain_until": None,
                "grace_until": None,
                "legal_hold": False,
            },
            idempotency_key="dual-sample-" + suffix,
        )

    def retained_fence(entry_bytes, entry_digest):
        result = original_fence(entry_bytes, entry_digest)
        calls.append("sample")
        if len(calls) == 1:
            binding = service._cookie_authority.current_binding()
            mutate_knowledge(binding, "after-m1")
        return result

    monkeypatch.setattr(store, "_assert_retained_image_locked", retained_fence)
    try:
        with pytest.raises(McpPrivateContextConfirmationError) as rejected:
            service.prepare(
                lease, fixture["cookie"], fixture["selection"], page_bytes=1024,
            )
        assert rejected.value.code in {"context_stale", "invalid_binding"}
        assert calls == ["sample", "sample"]
        assert not service._entries
        assert not service._permits and not service._exchanges
        assert not service._completions and not service._native_completions
    finally:
        service.close()
        fixture["authority"].release_read_lease(lease)


def test_c2_cookie_callback_mutation_is_rejected_by_m2(
    native_mcp_fixture, monkeypatch,
):
    fixture = native_mcp_fixture
    lease = _lease(fixture, "dual-c2-mcp-mutation")
    service = _service(fixture)
    producer_type = type(fixture["knowledge_producer"])
    original_cookie = producer_type.capture_metadata
    original_mcp = producer_type.capture_metadata_mcp
    cookie_calls, mcp_calls = [], []

    def mutate_knowledge(binding):
        _subject, store, principal, *_rest = fixture[
            "knowledge_composition"
        ]._context_runtime(binding)
        store.create_object(
            principal,
            owner_id=binding.owner_sid,
            knowledge_id="dual-sample-between-c2-m2",
            knowledge_kind="note",
            payload_bytes=b"dual sample between c2 and m2",
            payload_schema_id="harbour.note.v1",
            payload_media_type="text/plain",
            classification="private",
            retention={
                "retention_class": "user_managed",
                "deletion_mode": "tombstone_only",
                "retain_until": None,
                "grace_until": None,
                "legal_hold": False,
            },
            idempotency_key="dual-sample-between-c2-m2",
        )

    def cookie_capture(self, binding, selection):
        result = original_cookie(self, binding, selection)
        cookie_calls.append("cookie")
        if len(cookie_calls) == 2:
            mutate_knowledge(binding)
        return result

    def mcp_capture(self, admission, metadata_binding, selection):
        mcp_calls.append("mcp")
        return original_mcp(self, admission, metadata_binding, selection)

    monkeypatch.setattr(producer_type, "capture_metadata", cookie_capture)
    monkeypatch.setattr(producer_type, "capture_metadata_mcp", mcp_capture)
    try:
        with pytest.raises(McpPrivateContextConfirmationError) as rejected:
            service.prepare(
                lease, fixture["cookie"], fixture["selection"], page_bytes=1024,
            )
        assert rejected.value.code in {"context_stale", "invalid_binding"}
        assert cookie_calls == ["cookie", "cookie"]
        assert 1 <= len(mcp_calls) <= 2
        assert not service._entries
        assert not service._permits and not service._exchanges
        assert not service._completions and not service._native_completions
    finally:
        service.close()
        fixture["authority"].release_read_lease(lease)


@pytest.mark.parametrize(
    "mutation_kind",
    ["unselected_head", "selected_payload"],
)
def test_ack_readback_planning_mutation_scrubs_before_publication(
    generated_id_app, mutation_kind,
):
    from fastapi.testclient import TestClient
    from sqlalchemy import event
    from tests.test_app_mcp_private_context_confirmation import (
        SESSION_COOKIE,
        _assert_success,
        _headers,
    )
    from tests.test_planning_durable_context_source import (
        _canonical,
        compute_roadmap_content_hash,
    )

    with TestClient(generated_id_app) as client:
        fixture = client.app.state.fixture
        service = client.app.state.service
        prepared = _assert_success(client.post(
            "/test/prepare",
            headers={"Authorization": "Bearer " + fixture["mcp_raw_tokens"][0]},
        )).json()
        request_id = prepared["request_id"]
        client.cookies.set(SESSION_COOKIE, fixture["cookie"])
        challenge = _assert_success(client.post(
            "/api/context/mcp/challenge",
            headers=_headers(),
            json={"request_id": request_id},
        )).json()["token"]
        _assert_success(client.post(
            "/api/chat",
            headers=_headers(),
            json={
                "message": "/mcp-context-disclose " + challenge,
                "session": "chat-owned",
            },
        ))
        entry = service._entries[request_id]
        before = _entry_snapshot(entry)
        service_fields_before = frozenset(vars(service))
        engine = fixture["session_factory"].kw["bind"]
        mutations, ack_scopes = [], []
        append_receipts, completed_writes = [], []
        store = fixture["durable_source"]._store
        planning_targets = tuple(
            item for item in fixture["selection"].items
            if item.source == "planning"
        )
        selected_target = planning_targets[0]
        assert len(planning_targets) > 1
        unselected_target = next(
            item for item in planning_targets if item != selected_target
        )
        prepared_append = None
        selected_revision_id = None
        with CookieConfirmationAuthority(fixture["auth"]).hold(
            fixture["cookie"], fixture["chat_id"],
        ) as planning_binding:
            if mutation_kind == "unselected_head":
                payload = fixture["durable_source"].read_document(
                    planning_binding, unselected_target,
                )
                document = json.loads(payload.decode("utf-8"))
                roadmap = document["roadmaps"][0]
                roadmap_id = roadmap["roadmap_id"]
                roadmap["revision"] += 1
                roadmap["title"] = "ACK unselected head mutation"
                roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
                document["project"]["latest_approved_revision"] = {
                    roadmap_id: {
                        "revision": roadmap["revision"],
                        "content_hash": roadmap["content_hash"],
                    },
                }
                document["project"]["draft_refs"] = []
                expected_head = store.get_head(
                    fixture["principal"], owner_id="fuzzy",
                    project_id="harbour-one", roadmap_id=roadmap_id,
                )
                prepared_append = (
                    roadmap_id, _canonical(document), expected_head,
                )
            else:
                selected_descriptor = fixture["durable_source"].resolve(
                    planning_binding, type(fixture["selection"])((selected_target,)),
                )[0]
                selected_revision_id = selected_descriptor.durable_revision_id

        def mutate_on_ack_query(
            _connection, _cursor, _statement, _parameters, _context, _executemany,
        ):
            if (
                mutations
                or entry.state != "executing"
                or type(_statement) is not str
                or "chat_messages" not in _statement.lower()
            ):
                return
            composition = fixture["knowledge_composition"]
            ack_scopes.append((
                composition._inflight,
                getattr(composition._context_local, "depth", 0),
                getattr(store._planning_validation_pair_local, "scope", None),
            ))
            if mutation_kind == "unselected_head":
                roadmap_id, payload_bytes, expected_head = prepared_append
                receipt = store.append_revision(
                    fixture["principal"], owner_id="fuzzy",
                    project_id="harbour-one", roadmap_id=roadmap_id,
                    payload_bytes=payload_bytes, expected_head=expected_head,
                    idempotency_key="dual-ack-unselected-head-mutation-0001",
                )
                append_receipts.append(receipt)
            else:
                state = json.loads(store._path.read_text(encoding="utf-8"))
                assert selected_revision_id in state["revisions"]
                state["revisions"][selected_revision_id]["payload_b64"] = (
                    base64.b64encode(b'{"selected":"tampered"}').decode("ascii")
                )
                encoded_state = json.dumps(
                    state, ensure_ascii=False, allow_nan=False,
                    sort_keys=True, separators=(",", ":"),
                ).encode("utf-8")
                completed_writes.append((
                    store._path.write_bytes(encoded_state), len(encoded_state),
                ))
            mutations.append("ack")

        event.listen(engine, "before_cursor_execute", mutate_on_ack_query)
        try:
            denied = client.post("/test/consume")
        finally:
            event.remove(engine, "before_cursor_execute", mutate_on_ack_query)

        assert mutations == ["ack"]
        if mutation_kind == "unselected_head":
            assert completed_writes == []
            assert len(append_receipts) == 1
            receipt = append_receipts[0]
            roadmap_id, _payload_bytes, expected_head = prepared_append
            assert receipt.command_kind == "append_revision"
            assert receipt.before_head == expected_head
            assert receipt.after_head.current_revision_id == receipt.new_revision_id
            assert receipt.after_head.generation == expected_head.generation + 1
            assert store.get_head(
                fixture["principal"], owner_id="fuzzy",
                project_id="harbour-one", roadmap_id=roadmap_id,
            ) == receipt.after_head
        else:
            assert append_receipts == []
            assert len(completed_writes) == 1
            assert completed_writes[0][0] == completed_writes[0][1]
        assert denied.status_code == 403
        assert denied.json() == {"error": "context_stale"}
        assert "page_bytes" not in denied.text
        assert len(ack_scopes) == 1
        assert ack_scopes[0][0:2] == (1, 1)
        assert ack_scopes[0][2] is not None
        assert request_id not in service._entries
        _assert_operation_payload_not_retained(
            service, entry, before, service_fields_before,
        )


def test_prepare_and_real_consume_hold_dual_scope_through_acceptance(
    tmp_path, monkeypatch, request,
):
    from fastapi.testclient import TestClient
    from sqlalchemy import event
    from tests.test_app_mcp_private_context_confirmation import (
        SESSION_COOKIE,
        _assert_success,
        _headers,
    )

    original_pair = McpPrivateContextConfirmationService._pair
    original_content_capture = (
        McpPrivateContextConfirmationService._capture_dual_content
    )
    phase = {"value": "prepare"}
    refs = {}
    pair_scopes, ack_scopes, content_samples = [], [], []

    def scope_state():
        fixture = refs["fixture"]
        composition = fixture["knowledge_composition"]
        store = fixture["durable_source"]._store
        return (
            composition._inflight,
            getattr(composition._context_local, "depth", 0),
            getattr(store._planning_validation_pair_local, "scope", None),
        )

    def observed_pair(cookie, mcp, selection):
        pair_scopes.append((phase["value"], *scope_state()))
        return original_pair(cookie, mcp, selection)

    def observed_content_capture(self, binding, lease, selection, target):
        captured = original_content_capture(
            self, binding, lease, selection, target,
        )
        content_samples.append(captured)
        return captured

    monkeypatch.setattr(
        McpPrivateContextConfirmationService,
        "_pair",
        staticmethod(observed_pair),
    )
    monkeypatch.setattr(
        McpPrivateContextConfirmationService,
        "_capture_dual_content",
        observed_content_capture,
    )
    from tests.test_app_mcp_private_context_confirmation import consent_app
    observed_app = consent_app.__wrapped__(tmp_path, monkeypatch, request)
    with TestClient(observed_app) as client:
        fixture = client.app.state.fixture
        service = client.app.state.service
        refs["fixture"] = fixture
        prepared = _assert_success(client.post(
            "/test/prepare",
            headers={"Authorization": "Bearer " + fixture["mcp_raw_tokens"][0]},
        )).json()
        request_id = prepared["request_id"]
        client.cookies.set(SESSION_COOKIE, fixture["cookie"])
        phase["value"] = "attach"
        challenge = _assert_success(client.post(
            "/api/context/mcp/challenge",
            headers=_headers(),
            json={"request_id": request_id},
        )).json()["token"]
        _assert_success(client.post(
            "/api/chat",
            headers=_headers(),
            json={
                "message": "/mcp-context-disclose " + challenge,
                "session": "chat-owned",
            },
        ))
        entry = service._entries[request_id]
        before = _entry_snapshot(entry)
        service_fields_before = frozenset(vars(service))
        disclosed_cookie = entry.cookie_snapshot
        disclosed_mcp = entry.mcp_snapshot
        planning_target = next(
            item for item in fixture["selection"].items
            if item.source == "planning"
        )
        standalone_payload = client.app.state.oracle_payloads[
            planning_target.selection_ref
        ]
        engine = fixture["session_factory"].kw["bind"]

        def observe_ack(
            _connection, _cursor, _statement, _parameters, _context, _executemany,
        ):
            if (
                type(_statement) is not str
                or "chat_messages" not in _statement.lower()
            ):
                return
            ack_scopes.append((*scope_state(), len(content_samples)))

        phase["value"] = "consume"
        event.listen(engine, "before_cursor_execute", observe_ack)
        try:
            page = _assert_success(client.post("/test/consume")).json()
        finally:
            event.remove(engine, "before_cursor_execute", observe_ack)

        assert page["oracle_equal"] is True
        assert len(content_samples) == 2
        assert ack_scopes and any(row[3] == 1 for row in ack_scopes)
        first_cookie, first_mcp, cookie_payload1, mcp_payload1 = content_samples[0]
        last_cookie, last_mcp, cookie_payload2, mcp_payload2 = content_samples[1]
        assert first_cookie == last_cookie == disclosed_cookie
        assert first_mcp == last_mcp == disclosed_mcp
        assert (
            cookie_payload1 == mcp_payload1
            == cookie_payload2 == mcp_payload2 == standalone_payload
        )
        assert page["whole_sha256"] == (
            "sha256:" + hashlib.sha256(standalone_payload).hexdigest()
        )
        selected_item = next(
            item for item in first_mcp.items
            if (item.source, item.selection_ref, item.revision_ref)
            == (
                planning_target.source,
                planning_target.selection_ref,
                planning_target.revision_ref,
            )
        )
        assert selected_item.payload_sha256 == hashlib.sha256(
            standalone_payload,
        ).hexdigest()
        content_samples.clear()
        assert service._entries[request_id] is entry
        assert entry.state == "disclosed"
        assert not service._permits and not service._exchanges
        assert not service._completions and not service._native_completions
        assert frozenset(vars(service)) == service_fields_before
        assert service.revoke(request_id) is None
        assert request_id not in service._entries
        _assert_operation_payload_not_retained(
            service, entry, before, service_fields_before,
        )
        assert any(row[0] == "attach" for row in pair_scopes)
        assert any(row[0] == "consume" for row in pair_scopes)
        assert all(
            inflight == 1 and depth == 1 and scope is not None
            for _phase, inflight, depth, scope in pair_scopes
        )
        assert all(
            inflight == 1 and depth == 1 and scope is not None
            for inflight, depth, scope, _content_count in ack_scopes
        )


@pytest.mark.parametrize(
    "substitution",
    [
        "content_capture_class", "content_capture_instance",
        "content_revalidation_class", "content_revalidation_instance",
        "producer_content_class",
    ],
)
def test_planning_content_helper_transient_substitution_denies_without_bytes_and_scrubs(
    generated_id_app, monkeypatch, substitution,
):
    from fastapi.testclient import TestClient
    from tests.test_mcp_context_native_page import _authorize

    with TestClient(generated_id_app) as client:
        request_id = _authorize(client)
        service = client.app.state.service
        entry = service._entries[request_id]
        before = _entry_snapshot(entry)
        service_fields_before = frozenset(vars(service))
        instance_member = {
            "content_capture_instance": "_capture_dual_content",
            "content_revalidation_instance": "_revalidate_planning_content_under_guard",
        }.get(substitution)
        if instance_member is not None:
            assert instance_member not in vars(service)
        fake_calls = []

        with monkeypatch.context() as late_patch:
            def hostile(*_args, **_kwargs):
                fake_calls.append(substitution)
                raise AssertionError("transient content helper executed")

            def substitute_after_disclosure():
                if substitution == "content_capture_class":
                    late_patch.setattr(type(service), "_capture_dual_content", hostile)
                elif substitution == "content_capture_instance":
                    late_patch.setitem(vars(service), instance_member, hostile)
                elif substitution == "content_revalidation_class":
                    late_patch.setattr(
                        type(service), "_revalidate_planning_content_under_guard", hostile,
                    )
                elif substitution == "content_revalidation_instance":
                    late_patch.setitem(vars(service), instance_member, hostile)
                else:
                    late_patch.setattr(
                        type(client.app.state.fixture["combined"]),
                        "capture_dual_snapshot_content", hostile,
                    )

            client.app.state.before_execution_hook = substitute_after_disclosure
            denied = client.post("/test/consume")
            if instance_member is not None:
                assert vars(service)[instance_member] is hostile

        assert denied.status_code == 403
        assert b'"page_bytes"' not in denied.content
        assert fake_calls == []
        assert request_id not in service._entries
        _assert_operation_payload_not_retained(
            service, entry, before, service_fields_before,
        )


@pytest.mark.parametrize("page_bytes", [1, 16384])
def test_real_intent_page_size_boundaries(native_mcp_fixture, page_bytes):
    fixture = native_mcp_fixture
    lease = _lease(fixture, f"page-boundary-{page_bytes}")
    service = _service(fixture)
    try:
        prepared = service.prepare_intent(lease, fixture["selection"], page_bytes=page_bytes)
        assert prepared.item_count == len(fixture["selection"].items)
    finally:
        service.close()
        fixture["authority"].release_read_lease(lease)


@pytest.mark.parametrize("page_bytes", [0, 16385, True, 1.0, b"1"])
def test_real_intent_rejects_non_exact_page_sizes(native_mcp_fixture, page_bytes):
    fixture = native_mcp_fixture
    lease = _lease(fixture, "page-invalid-" + type(page_bytes).__name__)
    service = _service(fixture)
    try:
        with pytest.raises(McpPrivateContextConfirmationError) as error:
            service.prepare_intent(lease, fixture["selection"], page_bytes=page_bytes)
        assert error.value.code == "invalid_request"
    finally:
        service.close()
        fixture["authority"].release_read_lease(lease)


def test_service_does_not_accept_forged_permits(native_mcp_fixture):
    service = _service(native_mcp_fixture)
    try:
        with pytest.raises(McpPrivateContextConfirmationError) as error:
            with service.verify(Permit()):
                pass
        assert error.value.code == "permit_invalid"
    finally:
        service.close()


def test_service_revoke_rejects_hostile_nonstring_without_mutation(native_mcp_fixture):
    fixture = native_mcp_fixture
    lease = _lease(fixture, "hostile-revoke")
    service = _service(fixture)
    try:
        prepared = service.prepare_intent(lease, fixture["selection"])
        entry = service._entries[prepared.request_id]
        before_slots = _entry_snapshot(entry)
        callbacks = []

        class HostileRequestId:
            def __hash__(self):
                callbacks.append("hash")
                return hash(prepared.request_id)

            def __eq__(self, other):
                callbacks.append("eq")
                return False

        with pytest.raises(McpPrivateContextConfirmationError) as error:
            service.revoke(HostileRequestId())
        assert error.value.code == "invalid_request"
        assert callbacks == []
        assert service._entries.get(prepared.request_id) is entry
        assert _entry_unchanged(entry, before_slots)
    finally:
        service.close()
        fixture["authority"].release_read_lease(lease)


# Closed lifecycle oracle: new/unclassified fields must fail this contract.
_ENTRY_FIELDS = frozenset("""request_id lease_ref binding_ref owner_sid selection cookie
page_bytes cookie_snapshot mcp_snapshot metadata_binding issued deadline mono_deadline
 generation state challenge challenge_deadline challenge_mono_deadline receipt ack_wall_time
ack_mono_time exchange_nonce ack_deadline ack_mono_deadline ack_descriptor ack_attestation
ack_role_hmacs disclosure_generation disclosure_nonce disclosure_operation_id pending_ack_operation
permit_operation_nonce permit_ref permit_nonce permit_deadline permit_mono_deadline
page_request_sha256 intent execution_nonce execution_thread_ref execution_thread_ident execution_pid""".split())
_PRESERVED_FIELDS = frozenset({"request_id", "owner_sid", "issued", "deadline", "mono_deadline"})

def _entry_snapshot(entry):
    assert len(_ENTRY_FIELDS) == 42
    assert frozenset(type(entry).__dataclass_fields__) == _ENTRY_FIELDS
    return {name: getattr(entry, name) for name in _ENTRY_FIELDS}

def _entry_unchanged(entry, before):
    return (entry is not None and before is not None
            and frozenset(type(entry).__dataclass_fields__) == _ENTRY_FIELDS
            and all(getattr(entry, name) is before[name] for name in _ENTRY_FIELDS))

def _entry_fully_scrubbed(entry, before):
    if entry is None or before is None or frozenset(type(entry).__dataclass_fields__) != _ENTRY_FIELDS:
        return False
    empty = _ENTRY_FIELDS - _PRESERVED_FIELDS - {"state", "generation"}
    return (len(empty) == 35
            and all(getattr(entry, name) is before[name] for name in _PRESERVED_FIELDS)
            and entry.state == "expired" and type(entry.generation) is int
            and entry.generation > before["generation"]
            and type(entry.cookie) is str and entry.cookie == ""
            and type(entry.page_bytes) is int and entry.page_bytes == 0
            and entry.intent is False
            and all(getattr(entry, name) is None for name in empty - {"cookie", "page_bytes", "intent"}))


def _assert_operation_payload_not_retained(
    service, entry, before, service_fields_before,
):
    assert frozenset(type(entry).__dataclass_fields__) == _ENTRY_FIELDS
    assert _entry_fully_scrubbed(entry, before)
    assert Permit.__slots__ == (
        "__native_rejection_capsule_ref", "__weakref__",
    )
    assert frozenset(
        confirmation_module._CompletionRow.__dataclass_fields__
    ) == frozenset({
        "receipt_ref", "capture_ref", "entry_ref", "service_ref",
        "authority_ref", "lease_ref", "binding_ref", "thread_ref",
        "request_id", "generation", "nonce", "pid", "recipient",
        "page_sha256",
    })
    assert not service._permits and not service._exchanges
    assert not service._completions and not service._native_completions
    assert frozenset(vars(service)) == service_fields_before


@pytest.mark.parametrize("fault", [
    "mono_nan", "mono_infinity", "mono_negative_infinity", "mono_bool", "mono_string",
    "mono_rollback", "wall_rollback", "wall_naive", "wall_missing",
])
def test_native_clock_failure_scrubs_all_registered_recipients(native_mcp_fixture, fault):
    fixture = native_mcp_fixture
    clocks = {"wall": datetime.now(timezone.utc), "mono": 100.0}
    service = _service(fixture, wall_time=lambda: clocks["wall"], monotonic=lambda: clocks["mono"])
    leases = []
    try:
        primary = _lease(fixture, "clock-primary")
        leases.append(primary)
        secondary = _lease(fixture, "clock-secondary", token_index=1)
        leases.append(secondary)
        first = service.prepare_intent(primary, fixture["selection"])
        second = service.prepare_intent(secondary, fixture["selection"])
        entries = [service._entries[first.request_id], service._entries[second.request_id]]
        snapshots = [_entry_snapshot(entry) for entry in entries]
        assert len(service._entries) == 2
        if fault == "mono_nan":
            clocks["mono"] = float("nan")
        elif fault == "mono_infinity":
            clocks["mono"] = float("inf")
        elif fault == "mono_negative_infinity":
            clocks["mono"] = float("-inf")
        elif fault == "mono_bool":
            clocks["mono"] = True
        elif fault == "mono_string":
            clocks["mono"] = "100"
        elif fault == "mono_rollback":
            clocks["mono"] = 99.0
        elif fault == "wall_rollback":
            clocks["wall"] -= timedelta(seconds=1)
        elif fault == "wall_naive":
            clocks["wall"] = clocks["wall"].replace(tzinfo=None)
        else:
            clocks["wall"] = None
        with pytest.raises(McpPrivateContextConfirmationError) as error:
            service.prepare_intent(primary, fixture["selection"])
        assert error.value.code == "expired"
        # Invalid process clocks deliberately invalidate every registered request.
        # No consume/prune/close helper runs before these lifecycle assertions.
        assert not service._entries
        assert not service._permits and not service._exchanges and not service._completions
        assert all(_entry_fully_scrubbed(entry, before) for entry, before in zip(entries, snapshots))
    finally:
        service.close()
        for lease in leases:
            fixture["authority"].release_read_lease(lease)


def test_native_concurrent_clock_completion_order_does_not_fake_rollback(native_mcp_fixture):
    fixture = native_mcp_fixture
    initial_wall = datetime.now(timezone.utc)
    service = _service(fixture, wall_time=lambda: initial_wall, monotonic=lambda: 100.0)
    leases = []
    try:
        primary = _lease(fixture, "clock-order-primary")
        leases.append(primary)
        secondary = _lease(fixture, "clock-order-secondary", token_index=1)
        leases.append(secondary)
        first = service.prepare_intent(primary, fixture["selection"])
        second = service.prepare_intent(secondary, fixture["selection"])
        entries = [service._entries[first.request_id], service._entries[second.request_id]]
        snapshots = [_entry_snapshot(entry) for entry in entries]

        release_earlier = threading.Event()
        earlier_mono_entered = threading.Event()
        sample_guard = threading.Lock()
        wall_samples = iter((initial_wall + timedelta(seconds=1),
                             initial_wall + timedelta(seconds=2)))
        mono_samples = iter((101.0, 102.0))

        def wall_time():
            with sample_guard:
                return next(wall_samples)

        def monotonic():
            with sample_guard:
                value = next(mono_samples)
            if value == 101.0:
                earlier_mono_entered.set()
                assert release_earlier.wait(timeout=5)
            return value

        service._wall = wall_time
        service._mono = monotonic
        service._lock = _ReleaseEarlierSampleOnLaterLockAttempt(release_earlier)
        outcomes = {}

        def sample(label):
            try:
                outcomes[label] = ("ok", service._clock())
            except McpPrivateContextConfirmationError as error:
                outcomes[label] = ("error", error.code)

        earlier = threading.Thread(target=sample, args=("earlier",), name="earlier-clock-sample")
        later = threading.Thread(target=sample, args=("later",), name="later-clock-sample")
        earlier.start()
        assert earlier_mono_entered.wait(timeout=5)
        later.start()
        earlier.join(timeout=5)
        later.join(timeout=5)

        assert not earlier.is_alive() and not later.is_alive()
        assert outcomes == {
            "earlier": ("ok", (initial_wall + timedelta(seconds=1), 101.0)),
            "later": ("ok", (initial_wall + timedelta(seconds=2), 102.0)),
        }
        assert len(service._entries) == 2
        assert all(service._entries.get(entry.request_id) is entry for entry in entries)
        assert all(_entry_unchanged(entry, before) for entry, before in zip(entries, snapshots))
        assert not service._permits and not service._exchanges and not service._completions
    finally:
        service.close()
        for lease in leases:
            fixture["authority"].release_read_lease(lease)


def test_native_duplicate_lease_capacity_preserves_registered_intent(native_mcp_fixture):
    fixture = native_mcp_fixture
    lease = _lease(fixture, "capacity-one-lease")
    service = _service(fixture)
    try:
        prepared = service.prepare_intent(lease, fixture["selection"])
        entry = service._entries[prepared.request_id]
        before = _entry_snapshot(entry)
        with pytest.raises(McpPrivateContextConfirmationError) as error:
            service.prepare_intent(lease, fixture["selection"])
        assert error.value.code == "capacity"
        assert len(service._entries) == 1
        assert service._entries.get(prepared.request_id) is entry
        assert _entry_unchanged(entry, before)
        assert not service._permits and not service._exchanges and not service._completions
    finally:
        service.close()
        fixture["authority"].release_read_lease(lease)

@pytest.fixture
def generated_id_app(tmp_path, monkeypatch, request):
    from tests.test_app_mcp_private_context_confirmation import consent_app

    return consent_app.__wrapped__(tmp_path, monkeypatch, request)


@pytest.mark.parametrize("preparation", ["prepare", "prepare_intent"])
@pytest.mark.parametrize("leading", ["-", "_"])
def test_generated_request_id_preserves_nonce_and_native_identity(
    generated_id_app, monkeypatch, preparation, leading,
):
    import src.mcp_private_context_confirmation as confirmation_module
    from fastapi.testclient import TestClient
    from src.mcp_context_cursor_authority import _safe_id
    from tests.test_app_mcp_private_context_confirmation import SESSION_COOKIE, _headers

    with TestClient(generated_id_app) as client:
        fixture = client.app.state.fixture
        service = client.app.state.service
        nonce = leading + "A" * 42
        requested_bytes = []

        def forced_nonce(size):
            requested_bytes.append(size)
            assert size == 32
            return nonce

        def prepare_in_portal():
            lease = _lease(fixture, "request-id-compatibility-" + preparation + leading)
            client.app.state.lease = lease
            with monkeypatch.context() as nonce_patch:
                nonce_patch.setattr(confirmation_module.secrets, "token_urlsafe", forced_nonce)
                if preparation == "prepare":
                    prepared = service.prepare(lease, fixture["cookie"], fixture["selection"])
                else:
                    prepared = service.prepare_intent(lease, fixture["selection"])
            request_id = "r." + nonce
            client.app.state.request_id = request_id
            assert requested_bytes == [32]
            assert prepared.request_id == request_id
            assert prepared.private_required is True
            assert prepared.item_count == len(fixture["selection"].items)
            assert _safe_id(prepared.request_id, failure="context_unavailable") == request_id
            assert set(service._entries) == {request_id}
            assert nonce not in service._entries
            entry = service._entries[request_id]
            client.app.state.generated_id_entry = entry
            assert entry.request_id == request_id
            assert entry.lease_ref() is lease
            if preparation == "prepare":
                return request_id, service.issue_challenge(request_id).token
            return request_id, None

        def revoke_in_portal(challenge_token):
            request_id = client.app.state.request_id
            entry = client.app.state.generated_id_entry
            assert service._entries[request_id] is entry
            assert entry.request_id == request_id
            assert entry.challenge == challenge_token
            before = _entry_snapshot(entry)
            assert service.revoke(request_id) is None
            assert _entry_fully_scrubbed(entry, before)

        def release_in_portal():
            lease = getattr(client.app.state, "lease", None)
            if lease is not None:
                fixture["authority"].release_read_lease(lease)

        try:
            request_id, challenge_token = client.portal.call(prepare_in_portal)
            if preparation == "prepare_intent":
                client.cookies.set(SESSION_COOKIE, fixture["cookie"])
                response = client.post(
                    "/api/context/mcp/challenge", headers=_headers(),
                    json={"request_id": request_id},
                )
                assert response.status_code == 200
                challenge_token = response.json()["token"]
            client.portal.call(revoke_in_portal, challenge_token)
        finally:
            client.portal.call(release_in_portal)
