"""Native service-capacity tests staged for independent review."""
from __future__ import annotations

import bcrypt
import pytest

from core.database import ApiToken, Session as DbSession
from src.mcp_authenticated_dispatch import McpAuthenticatedIdentity
from src.mcp_context_authority import McpContextAuthorityError, issue_mcp_bearer_proof
from src.mcp_private_context_confirmation import McpPrivateContextConfirmationError
from tests.test_mcp_context_metadata import native_mcp_fixture
from tests.test_mcp_private_context_confirmation import (
    _entry_snapshot,
    _entry_unchanged,
    _service,
)


def _eight_live_native_owner_handles(fixture):
    """Issue eight real authority bindings and leases for the fixture owner."""
    auth = fixture["auth"]
    authority = fixture["authority"]
    owner = "fuzzy"
    owner_sid = auth.subject_id_for_username(owner)
    records = []
    with fixture["session_factory"]() as db:
        for number in range(8):
            raw = "ody_" + ("capacity" + str(number)).ljust(43, "x")
            token_id = "consent-capacity-token-" + str(number)
            chat_id = "consent-capacity-chat-" + str(number)
            db.add(ApiToken(
                id=token_id,
                owner=owner,
                name="consent capacity",
                token_hash=bcrypt.hashpw(raw.encode("ascii"), bcrypt.gensalt(rounds=4)).decode("ascii"),
                token_prefix=raw[:8],
                scopes="mcp:harbour:context:read",
                owner_subject_id=owner_sid,
                is_active=True,
            ))
            db.add(DbSession(
                id=chat_id,
                name="Consent capacity",
                endpoint_url="local",
                model="test",
                owner=owner,
                message_count=0,
            ))
            records.append((token_id, chat_id, raw))
        db.commit()

    handles = []
    for number, (token_id, chat_id, raw) in enumerate(records):
        identity = McpAuthenticatedIdentity(
            token_id,
            owner,
            owner_sid,
            fixture["mcp_state"]._token_cache_generation,
            auth.security_action_revision(),
            "mcp:harbour:context:read",
        )
        binding = authority.issue_binding(identity, issue_mcp_bearer_proof(raw), chat_id)
        lease = authority.issue_read_lease(binding, "consent-capacity-" + str(number))
        handles.append((binding, lease))
    return tuple(handles)


def _ninth_native_owner_handle(fixture):
    auth = fixture["auth"]
    owner_sid = auth.subject_id_for_username("fuzzy")
    raw = "ody_" + "capacity-nine".ljust(43, "x")
    token_id = "consent-capacity-token-nine"
    chat_id = "consent-capacity-chat-nine"
    with fixture["session_factory"]() as db:
        db.add(ApiToken(
            id=token_id, owner="fuzzy", name="consent capacity nine",
            token_hash=bcrypt.hashpw(raw.encode("ascii"), bcrypt.gensalt(rounds=4)).decode("ascii"),
            token_prefix=raw[:8], scopes="mcp:harbour:context:read",
            owner_subject_id=owner_sid, is_active=True,
        ))
        db.add(DbSession(
            id=chat_id, name="Consent capacity nine", endpoint_url="local",
            model="test", owner="fuzzy", message_count=0,
        ))
        db.commit()
    identity = McpAuthenticatedIdentity(
        token_id, "fuzzy", owner_sid, fixture["mcp_state"]._token_cache_generation,
        auth.security_action_revision(), "mcp:harbour:context:read",
    )
    authority = fixture["authority"]
    binding = authority.issue_binding(identity, issue_mcp_bearer_proof(raw), chat_id)
    return binding, authority.issue_read_lease(binding, "consent-capacity-nine")


def _assert_eight_intents_only(fixture, service, handles, prepared):
    request_ids = tuple(request.request_id for request in prepared)
    owner_sid = fixture["auth"].subject_id_for_username("fuzzy")
    assert len(handles) == len(request_ids) == len(service._entries) == 8
    assert tuple(service._entries) == request_ids
    entries = tuple(service._entries[request_id] for request_id in request_ids)
    assert all(entry.state == "intent" and entry.intent is True for entry in entries)
    assert all(
        entry.owner_sid == owner_sid
        and entry.binding_ref() is binding
        and entry.lease_ref() is lease
        for entry, (binding, lease) in zip(entries, handles)
    )
    assert all(
        entry.challenge is None
        and entry.receipt is None
        and entry.pending_ack_operation is None
        and entry.permit_ref is None
        for entry in entries
    )
    assert not service._permits and not service._exchanges and not service._completions
    assert all(
        fixture["authority"].recheck_lease(lease) is binding
        for binding, lease in handles
    )
    return entries


def test_native_service_accepts_exactly_eight_live_intents_for_one_owner(native_mcp_fixture):
    fixture = native_mcp_fixture
    handles = _eight_live_native_owner_handles(fixture)
    service = _service(fixture)
    try:
        prepared = tuple(
            service.prepare_intent(lease, fixture["selection"])
            for _binding, lease in handles
        )
        _assert_eight_intents_only(fixture, service, handles, prepared)
    finally:
        # Service state is closed first; the owning native fixture closes every
        # authority handle after this test without per-lease teardown masking.
        service.close()


def test_native_service_retained_owner_registry_rejects_distinct_ninth_lease(native_mcp_fixture):
    fixture = native_mcp_fixture
    handles = _eight_live_native_owner_handles(fixture)
    service = _service(fixture)
    try:
        prepared = tuple(
            service.prepare_intent(lease, fixture["selection"])
            for _binding, lease in handles
        )
        entries = _assert_eight_intents_only(fixture, service, handles, prepared)
        before = tuple(_entry_snapshot(entry) for entry in entries)

        # Retain every original Python handle and service entry while freeing
        # one native SID slot through the real authority revocation path.
        authority = fixture["authority"]
        authority.release_binding(handles[0][0])
        with pytest.raises(McpContextAuthorityError) as revoked:
            authority.recheck_lease(handles[0][1])
        assert revoked.value.code == "unauthorized"
        assert all(authority.recheck_lease(lease) is binding for binding, lease in handles[1:])
        ninth_binding, ninth_lease = _ninth_native_owner_handle(fixture)
        assert all(ninth_lease is not lease for _binding, lease in handles)
        assert authority.recheck_lease(ninth_lease) is ninth_binding
        with pytest.raises(McpPrivateContextConfirmationError) as error:
            service.prepare_intent(ninth_lease, fixture["selection"])

        assert error.value.code == "capacity"
        assert tuple(service._entries.values()) == entries
        assert all(
            service._entries.get(entry.request_id) is entry
            and _entry_unchanged(entry, snapshot)
            for entry, snapshot in zip(entries, before)
        )
        assert all(entry.state == "intent" and entry.intent is True for entry in entries)
        assert all(
            entry.binding_ref() is binding and entry.lease_ref() is lease
            for entry, (binding, lease) in zip(entries, handles)
        )
        assert not service._permits and not service._exchanges and not service._completions
        assert all(authority.recheck_lease(lease) is binding for binding, lease in handles[1:])
        assert authority.recheck_lease(ninth_lease) is ninth_binding
        with pytest.raises(McpContextAuthorityError) as still_revoked:
            authority.recheck_lease(handles[0][1])
        assert still_revoked.value.code == "unauthorized"
    finally:
        service.close()
