"""Native MCP consent tests using the accepted three-domain fixture."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading

import pytest

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
