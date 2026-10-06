from types import SimpleNamespace
import threading
import gc
import weakref
import copy
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import bcrypt
import pytest

from core.auth import AuthManager
from core.database import ApiToken, Base, Session as DbSession, SessionLocal, engine
from src.mcp_authenticated_dispatch import McpAuthenticatedIdentity
from src.mcp_context_authority import (
    McpContextAuthority,
    McpContextAuthorityError,
    McpContextBinding,
    McpContextReadLease,
    McpContextBearerProof,
    issue_mcp_bearer_proof,
)
from src.mcp_context_contract import MCP_CONTEXT_SCOPE


def _fixture(tmp_path):
    Base.metadata.create_all(engine)
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.setup("admin", "correct horse battery staple") is True
    sid = manager.subject_id_for_username("admin")
    raw = "ody_" + "a" * 43
    token_id = "ctx-token"
    with SessionLocal() as db:
        db.query(ApiToken).filter(ApiToken.id == token_id).delete(synchronize_session=False)
        db.query(DbSession).filter(DbSession.id.in_(("chat.1", "chat.foreign"))).delete(synchronize_session=False)
        db.add(ApiToken(
            id=token_id,
            owner="admin",
            name="context fixture",
            token_hash=bcrypt.hashpw(raw.encode("ascii"), bcrypt.gensalt()).decode("ascii"),
            token_prefix=raw[:8],
            scopes=MCP_CONTEXT_SCOPE,
            owner_subject_id=sid,
            is_active=True,
        ))
        db.add(DbSession(
            id="chat.1", name="fixture", endpoint_url="local", model="fixture", owner="admin"
        ))
        db.commit()
    epoch = "epoch_" + "b" * 32
    state = SimpleNamespace(
        _token_cache_generation=4,
        _token_cache_dirty=False,
        mcp_context_plugin_enabled=True,
        allow_context_reads=True,
        _process_epoch=epoch,
    )
    identity = McpAuthenticatedIdentity(token_id, "admin", sid, 4, manager.security_action_revision(), MCP_CONTEXT_SCOPE)
    authority = McpContextAuthority(
        manager,
        SessionLocal,
        app_state=state,
    )
    return manager, authority, identity, issue_mcp_bearer_proof(raw), state


def test_binding_requires_exact_issued_proof_real_row_and_owned_chat(tmp_path):
    manager, authority, identity, proof, _state = _fixture(tmp_path)
    binding = authority.issue_binding(identity, proof, "chat.1")
    assert type(binding) is McpContextBinding
    assert binding.audience == "authenticated_mcp_context_read"
    assert repr(binding) == "<McpContextBinding redacted>"
    assert "ody_" not in repr(binding)
    authority.recheck_binding(binding)
    with pytest.raises(McpContextAuthorityError) as error:
        authority.issue_binding(identity, None, "chat.1")
    assert error.value.code == "unauthorized"
    assert error.value.__cause__ is None


def test_forged_or_subclassed_binding_and_mutated_fields_fail(tmp_path):
    _manager, authority, identity, proof, _state = _fixture(tmp_path / "expired")
    binding = authority.issue_binding(identity, proof, "chat.1")
    forged = object.__new__(McpContextBinding)
    for name in (
        "__issuer", "__authority", "__proof", "__token_id", "__owner_alias",
        "__owner_subject_id", "__scope", "__cache_generation",
        "__security_action_revision", "__chat_session_id", "__process_epoch",
    ):
        value = object.__getattribute__(binding, "_McpContextBinding" + name)
        object.__setattr__(forged, "_McpContextBinding" + name, value)
    with pytest.raises(McpContextAuthorityError) as error:
        authority.recheck_binding(forged)
    assert error.value.code == "unauthorized"
    object.__setattr__(binding, "_McpContextBinding__owner_alias", "mallory")
    with pytest.raises(McpContextAuthorityError) as error:
        authority.recheck_binding(binding)
    assert error.value.code == "unauthorized"
    with pytest.raises(TypeError):
        class BindingChild(McpContextBinding):
            pass


def test_forged_or_cross_authority_proofs_fail(tmp_path):
    manager, authority, identity, proof, state = _fixture(tmp_path)
    copied = object.__new__(McpContextBearerProof)
    with pytest.raises(McpContextAuthorityError) as error:
        authority.issue_binding(identity, copied, "chat.1")
    assert error.value.code == "unauthorized"
    authority.issue_binding(identity, proof, "chat.1")
    foreign = McpContextAuthority(
        manager,
        SessionLocal,
        app_state=state,
    )
    with pytest.raises(McpContextAuthorityError) as error:
        foreign.issue_binding(identity, proof, "chat.1")
    assert error.value.code == "unauthorized"


def test_stale_revoked_replaced_scope_cache_security_and_foreign_chat_fail(tmp_path):
    manager, authority, identity, proof, state = _fixture(tmp_path)
    binding = authority.issue_binding(identity, proof, "chat.1")
    with SessionLocal() as db:
        row = db.query(ApiToken).filter(ApiToken.id == identity.token_id).first()
        row.is_active = False
        db.commit()
    with pytest.raises(McpContextAuthorityError) as error:
        authority.recheck_binding(binding)
    assert error.value.code == "unauthorized"

    with SessionLocal() as db:
        row = db.query(ApiToken).filter(ApiToken.id == identity.token_id).first()
        row.is_active = True
        row.owner_subject_id = "owner_" + "c" * 32
        db.commit()
    with pytest.raises(McpContextAuthorityError) as error:
        authority.issue_binding(identity, proof, "chat.1")
    assert error.value.code == "unauthorized"

    with SessionLocal() as db:
        row = db.query(ApiToken).filter(ApiToken.id == identity.token_id).first()
        row.owner_subject_id = identity.owner_subject_id
        db.add(DbSession(id="chat.foreign", name="foreign", endpoint_url="local", model="fixture", owner="mallory"))
        db.commit()
    with pytest.raises(McpContextAuthorityError) as error:
        authority.issue_binding(identity, proof, "chat.foreign")
    assert error.value.code == "unauthorized"

    with pytest.raises(McpContextAuthorityError) as error:
        authority.issue_binding(
            McpAuthenticatedIdentity(identity.token_id, identity.owner_alias, identity.owner_subject_id, 4, identity.security_action_revision, "mcp:planning:read"),
            proof,
            "chat.1",
        )
    assert error.value.code == "unauthorized"

    state._token_cache_generation = 5
    with pytest.raises(McpContextAuthorityError) as error:
        authority.issue_binding(identity, proof, "chat.1")
    assert error.value.code == "stale_authentication"
    state._token_cache_generation = 4
    manager._bump_security_action_revision()
    with pytest.raises(McpContextAuthorityError) as error:
        authority.issue_binding(identity, proof, "chat.1")
    assert error.value.code == "stale_authentication"


def test_gate_default_is_closed_and_lease_is_live_cancellable_and_bounded(tmp_path):
    manager, authority, identity, proof, state = _fixture(tmp_path)
    state.allow_context_reads = False
    with pytest.raises(McpContextAuthorityError) as error:
        authority.issue_binding(identity, proof, "chat.1")
    assert error.value.code == "disabled"
    state.mcp_context_plugin_enabled = False
    state.plugin_enabled = True
    proof = issue_mcp_bearer_proof("ody_" + "a" * 43)
    with pytest.raises(McpContextAuthorityError) as error:
        authority.issue_binding(identity, proof, "chat.1")
    assert error.value.code == "disabled"
    state.mcp_context_plugin_enabled = True
    state.allow_context_reads = True
    proof = issue_mcp_bearer_proof("ody_" + "a" * 43)
    binding = authority.issue_binding(identity, proof, "chat.1")
    lease = authority.issue_read_lease(binding, "request.1", ttl_seconds=300)
    assert type(lease) is McpContextReadLease
    assert repr(lease) == "<McpContextReadLease redacted>"
    assert authority.require_live_lease(lease) is binding
    authority.cancel_lease(lease)
    with pytest.raises(McpContextAuthorityError) as error:
        authority.require_live_lease(lease)
    assert error.value.code == "cancelled"
    # Explicit release is the only operation which removes the weak marker.
    authority.release_read_lease(lease)
    with pytest.raises(McpContextAuthorityError) as error:
        authority.require_live_lease(lease)
    assert error.value.code == "unauthorized"
    with pytest.raises(McpContextAuthorityError) as error:
        authority.issue_read_lease(binding, "request.2", ttl_seconds=901)
    assert error.value.code == "invalid_request"


def test_failed_pending_proof_cannot_retry_after_gate_restored(tmp_path):
    _manager, authority, identity, proof, state = _fixture(tmp_path)
    state.allow_context_reads = False
    with pytest.raises(McpContextAuthorityError) as error:
        authority.issue_binding(identity, proof, "chat.1")
    assert error.value.code == "disabled"
    state.allow_context_reads = True
    with pytest.raises(McpContextAuthorityError) as error:
        authority.issue_binding(identity, proof, "chat.1")
    assert error.value.code == "unauthorized"
    fresh = issue_mcp_bearer_proof("ody_" + "a" * 43)
    assert authority.issue_binding(identity, fresh, "chat.1") is not None


@pytest.mark.parametrize("value", (float("nan"), float("inf"), 99.0))
def test_wall_only_invalid_or_regressing_clock_terminally_revokes(tmp_path, value):
    manager, _unused, identity, proof, state = _fixture(tmp_path)
    clock = [10.0, 100.0]
    authority = McpContextAuthority(manager, SessionLocal, app_state=state,
                                    monotonic=lambda: clock[0], wall_time=lambda: clock[1])
    binding = authority.issue_binding(identity, proof, "chat.1")
    lease = authority.issue_read_lease(binding, "wall.invalid")
    clock[1] = value
    with pytest.raises(McpContextAuthorityError) as error: authority.recheck_lease(lease)
    assert error.value.code == "context_unavailable"
    with pytest.raises(McpContextAuthorityError): authority.recheck_binding(binding)


@pytest.mark.parametrize("mode", ("nan", "inf", "raising"))
def test_pending_claim_invalid_monotonic_consumes_exact_proof(tmp_path, monkeypatch, mode):
    from src import mcp_context_authority as context_authority
    clock = [100.0]
    monkeypatch.setattr(context_authority.time, "monotonic", lambda: clock[0])
    _manager, authority, identity, proof, _state = _fixture(tmp_path)
    if mode == "nan": clock[0] = float("nan")
    elif mode == "inf": clock[0] = float("inf")
    else:
        def raising(): raise RuntimeError("clock")
        monkeypatch.setattr(context_authority.time, "monotonic", raising)
    with pytest.raises(McpContextAuthorityError) as error:
        authority.issue_binding(identity, proof, "chat.1")
    assert error.value.code == "context_unavailable"
    monkeypatch.setattr(context_authority.time, "monotonic", lambda: 100.0)
    with pytest.raises(McpContextAuthorityError) as error:
        authority.issue_binding(identity, proof, "chat.1")
    assert error.value.code == "unauthorized"


@pytest.mark.parametrize("operation,field", [
    ("issue", "__native"), ("issue", "__issuer"),
    ("recheck", "__native"), ("recheck", "__issuer"),
])
def test_proof_mutation_during_real_external_validation_fails_closed(tmp_path, operation, field):
    _manager, authority, identity, proof, _state = _fixture(tmp_path)
    binding = authority.issue_binding(identity, proof, "chat.1") if operation == "recheck" else None
    target = proof
    replacement = issue_mcp_bearer_proof("ody_" + "a" * 43)
    name = "_McpContextBearerProof" + field
    original_value = object.__getattribute__(target, name)
    original_row_check = authority._row_and_chat
    def wrapped(*args):
        original_row_check(*args)
        object.__setattr__(target, name, object.__getattribute__(replacement, name))
    authority._row_and_chat = wrapped
    try:
        with pytest.raises(McpContextAuthorityError) as error:
            if operation == "issue": authority.issue_binding(identity, proof, "chat.1")
            else: authority.recheck_binding(binding)
        assert error.value.code == "unauthorized"
    finally:
        authority._row_and_chat = original_row_check
        object.__setattr__(target, name, original_value)
        if binding is not None:
            authority.release_binding(binding)
        else:
            try:
                from src.mcp_context_authority import release_mcp_bearer_proof
                release_mcp_bearer_proof(proof)
            except McpContextAuthorityError:
                pass
        try:
            from src.mcp_context_authority import release_mcp_bearer_proof
            release_mcp_bearer_proof(replacement)
        except McpContextAuthorityError:
            pass


def test_missing_cache_authority_state_is_stale(tmp_path):
    manager, authority, identity, proof, state = _fixture(tmp_path)
    del state._token_cache_generation
    del state._token_cache_dirty
    with pytest.raises(McpContextAuthorityError) as error:
        authority.issue_binding(identity, proof, "chat.1")
    assert error.value.code == "stale_authentication"


def test_release_and_close_revoke_handles_without_live_state(tmp_path):
    _manager, authority, identity, proof, state = _fixture(tmp_path)
    binding = authority.issue_binding(identity, proof, "chat.1")
    lease = authority.issue_read_lease(binding, "release.1")
    del state.allow_context_reads
    authority.release_read_lease(lease)
    authority.release_binding(binding)
    authority.close()
    with pytest.raises(McpContextAuthorityError):
        authority.recheck_binding(binding)


@pytest.mark.parametrize("field,value,code", [
    ("mcp_context_plugin_enabled", None, "disabled"),
    ("allow_context_reads", 1, "disabled"),
    ("_token_cache_generation", -1, "stale_authentication"),
    ("_token_cache_dirty", True, "stale_authentication"),
    ("_process_epoch", "epoch_bad", "stale_authentication"),
])
def test_each_live_state_field_is_mandatory(tmp_path, field, value, code):
    _manager, authority, identity, proof, state = _fixture(tmp_path)
    setattr(state, field, value)
    with pytest.raises(McpContextAuthorityError) as error: authority.issue_binding(identity, proof, "chat.1")
    assert error.value.code == code


def test_real_persisted_hash_replacement_and_same_alias_recreation_revoke(tmp_path):
    manager, authority, identity, proof, state = _fixture(tmp_path)
    binding = authority.issue_binding(identity, proof, "chat.1")
    with SessionLocal() as db:
        row = db.query(ApiToken).filter(ApiToken.id == identity.token_id).first()
        row.token_hash = bcrypt.hashpw(b"ody_" + b"z" * 43, bcrypt.gensalt()).decode("ascii")
        db.commit()
    with pytest.raises(McpContextAuthorityError) as error:
        authority.recheck_binding(binding)
    assert error.value.code == "unauthorized"
    # The user lifecycle is real AuthManager state: the same alias receives a
    # distinct subject id after deletion and recreation.
    assert manager.create_user("operator", "correct horse battery staple", is_admin=True) is True
    assert manager.delete_user("admin", "operator") is True
    assert manager.create_user("admin", "correct horse battery staple", is_admin=True) is True
    assert manager.subject_id_for_username("admin") != identity.owner_subject_id
    fresh = issue_mcp_bearer_proof("ody_" + "a" * 43)
    with pytest.raises(McpContextAuthorityError) as error:
        authority.issue_binding(identity, fresh, "chat.1")
    assert error.value.code in {"unauthorized", "stale_authentication"}


@pytest.mark.parametrize("field,value,code", [
    ("mcp_context_plugin_enabled", False, "disabled"),
    ("allow_context_reads", False, "disabled"),
    ("_token_cache_generation", 9, "stale_authentication"),
    ("_token_cache_dirty", True, "stale_authentication"),
    ("_process_epoch", "epoch_" + "c" * 32, "stale_authentication"),
])
def test_post_issue_binding_and_lease_recheck_every_live_state_field(tmp_path, field, value, code):
    manager, authority, identity, proof, state = _fixture(tmp_path)
    binding = authority.issue_binding(identity, proof, "chat.1")
    lease = authority.issue_read_lease(binding, "drift.1")
    setattr(state, field, value)
    with pytest.raises(McpContextAuthorityError) as error:
        authority.recheck_binding(binding)
    assert error.value.code == code
    with pytest.raises(McpContextAuthorityError) as error:
        authority.recheck_lease(lease)
    assert error.value.code == code


def test_generic_plugin_flag_cannot_open_missing_context_gate(tmp_path):
    _manager, authority, identity, proof, state = _fixture(tmp_path)
    del state.mcp_context_plugin_enabled
    state.plugin_enabled = True
    with pytest.raises(McpContextAuthorityError) as error:
        authority.issue_binding(identity, proof, "chat.1")
    assert error.value.code == "disabled"


def test_failed_admission_releases_only_its_still_pending_proof(tmp_path):
    _manager, authority, identity, proof, state = _fixture(tmp_path)
    state.allow_context_reads = False
    with pytest.raises(McpContextAuthorityError) as error:
        authority.issue_binding(identity, proof, "chat.1")
    assert error.value.code == "disabled"
    from src.mcp_context_authority import release_mcp_bearer_proof
    with pytest.raises(McpContextAuthorityError):
        release_mcp_bearer_proof(proof)


def test_two_authorities_same_proof_have_one_winner_and_loser_cleanup_preserves_it(tmp_path):
    local_engine = create_engine(f"sqlite:///{tmp_path / 'race.sqlite'}")
    LocalSession = sessionmaker(bind=local_engine)
    Base.metadata.create_all(local_engine)
    manager = AuthManager(str(tmp_path / "race-auth.json"))
    assert manager.setup("admin", "correct horse battery staple") is True
    sid = manager.subject_id_for_username("admin")
    raw = "ody_" + "a" * 43
    with LocalSession() as db:
        db.add(ApiToken(id="race-token", owner="admin", name="race", token_hash=bcrypt.hashpw(raw.encode("ascii"), bcrypt.gensalt()).decode("ascii"), token_prefix=raw[:8], scopes=MCP_CONTEXT_SCOPE, owner_subject_id=sid, is_active=True))
        db.add(DbSession(id="chat.1", name="race", endpoint_url="local", model="fixture", owner="admin")); db.commit()
    state = SimpleNamespace(_token_cache_generation=4, _token_cache_dirty=False, mcp_context_plugin_enabled=True, allow_context_reads=True, _process_epoch="epoch_" + "b" * 32)
    identity = McpAuthenticatedIdentity("race-token", "admin", sid, 4, manager.security_action_revision(), MCP_CONTEXT_SCOPE)
    proof = issue_mcp_bearer_proof(raw)
    winner = McpContextAuthority(manager, LocalSession, app_state=state)
    loser = McpContextAuthority(manager, LocalSession, app_state=state)
    barrier = threading.Barrier(2)
    original = winner._row_and_chat
    def gated(*args):
        barrier.wait(timeout=3)
        return original(*args)
    winner._row_and_chat = gated
    loser._row_and_chat = gated
    results = []
    def issue(authority):
        try: results.append(("ok", authority, authority.issue_binding(identity, proof, "chat.1")))
        except McpContextAuthorityError as error: results.append((error.code, authority, None))
    first = threading.Thread(target=issue, args=(winner,)); second = threading.Thread(target=issue, args=(loser,))
    first.start(); second.start(); first.join(5); second.join(5)
    winner._row_and_chat = original
    loser._row_and_chat = original
    successes = [row for row in results if row[0] == "ok"]
    assert len(successes) == 1 and len(results) == 2 and not first.is_alive() and not second.is_alive()
    successes[0][1].recheck_binding(successes[0][2])


def test_clock_and_ttl_failures_are_fixed_and_release_slots(tmp_path):
    manager, _authority, identity, proof, state = _fixture(tmp_path)
    clock = [100.0, 1000.0]
    authority = McpContextAuthority(manager, SessionLocal, app_state=state, monotonic=lambda: clock[0], wall_time=lambda: clock[1])
    binding = authority.issue_binding(identity, proof, "chat.1")
    lease = authority.issue_read_lease(binding, "clock.1", ttl_seconds=900)
    clock[1] += 901
    with pytest.raises(McpContextAuthorityError) as error: authority.recheck_lease(lease)
    assert error.value.code == "timeout"
    with pytest.raises(McpContextAuthorityError) as error: authority.issue_read_lease(binding, "clock.2", ttl_seconds=901)
    assert error.value.code == "invalid_request"


@pytest.mark.parametrize("which", ("monotonic", "wall"))
def test_raising_clock_terminally_revokes_binding_and_proof(tmp_path, which):
    manager, _authority, identity, proof, state = _fixture(tmp_path)
    clocks = {"monotonic": lambda: 10.0, "wall": lambda: 100.0}
    authority = McpContextAuthority(manager, SessionLocal, app_state=state,
                                    monotonic=clocks["monotonic"], wall_time=clocks["wall"])
    binding = authority.issue_binding(identity, proof, "chat.1")
    lease = authority.issue_read_lease(binding, "raising.1")
    clocks[which] = lambda: (_ for _ in ()).throw(RuntimeError("clock"))
    authority._monotonic, authority._wall_time = clocks["monotonic"], clocks["wall"]
    with pytest.raises(McpContextAuthorityError) as error:
        authority.recheck_lease(lease)
    assert error.value.code == "context_unavailable"
    with pytest.raises(McpContextAuthorityError):
        authority.recheck_binding(binding)


def test_cancelled_marker_does_not_consume_live_lease_capacity(tmp_path):
    _manager, authority, identity, proof, _state = _fixture(tmp_path)
    binding = authority.issue_binding(identity, proof, "chat.1")
    lease = authority.issue_read_lease(binding, "cancel.capacity")
    authority.cancel_lease(lease)
    # Cancelled records are markers, not live slots; a fresh request can issue.
    assert authority.issue_read_lease(binding, "after.cancel") is not None


@pytest.mark.parametrize("which", ("monotonic", "wall"))
def test_each_clock_timeout_and_regression_terminally_revoke(tmp_path, which):
    manager, _unused, identity, proof, state = _fixture(tmp_path)
    clock = [10.0, 100.0]
    authority = McpContextAuthority(manager, SessionLocal, app_state=state,
                                    monotonic=lambda: clock[0], wall_time=lambda: clock[1])
    binding = authority.issue_binding(identity, proof, "chat.1")
    lease = authority.issue_read_lease(binding, "time.1", ttl_seconds=1)
    clock[0 if which == "monotonic" else 1] += 2
    with pytest.raises(McpContextAuthorityError) as error: authority.recheck_lease(lease)
    assert error.value.code == "timeout"
    with pytest.raises(McpContextAuthorityError): authority.recheck_binding(binding)


@pytest.mark.parametrize("value", (float("nan"), float("inf"), -1.0))
def test_nonfinite_or_regressing_clocks_terminally_revoke(tmp_path, value):
    manager, _unused, identity, proof, state = _fixture(tmp_path)
    clock = [10.0, 100.0]
    authority = McpContextAuthority(manager, SessionLocal, app_state=state,
                                    monotonic=lambda: clock[0], wall_time=lambda: clock[1])
    binding = authority.issue_binding(identity, proof, "chat.1")
    lease = authority.issue_read_lease(binding, "time.2")
    clock[0] = value
    with pytest.raises(McpContextAuthorityError) as error: authority.recheck_lease(lease)
    assert error.value.code == "context_unavailable"
    with pytest.raises(McpContextAuthorityError): authority.recheck_binding(binding)


def test_ttl_default_max_and_bool_are_closed(tmp_path):
    _manager, authority, identity, proof, _state = _fixture(tmp_path)
    binding = authority.issue_binding(identity, proof, "chat.1")
    default = authority.issue_read_lease(binding, "ttl.default")
    authority.release_read_lease(default)
    maximum = authority.issue_read_lease(binding, "ttl.max", ttl_seconds=900)
    authority.release_read_lease(maximum)
    with pytest.raises(McpContextAuthorityError) as error:
        authority.issue_read_lease(binding, "ttl.bool", ttl_seconds=True)
    assert error.value.code == "invalid_request"


def test_pending_proof_ttl_is_checked_at_claim_without_new_factory(tmp_path, monkeypatch):
    from src import mcp_context_authority as context_authority
    clock = [100.0]
    monkeypatch.setattr(context_authority.time, "monotonic", lambda: clock[0])
    _manager, authority, identity, proof, _state = _fixture(tmp_path)
    clock[0] = 399.999
    assert authority.issue_binding(identity, proof, "chat.1") is not None
    clock[0] = 500.0
    _manager, authority, identity, proof, _state = _fixture(tmp_path / "expired")
    clock[0] = 800.0
    with pytest.raises(McpContextAuthorityError) as error:
        authority.issue_binding(identity, proof, "chat.1")
    assert error.value.code == "context_unavailable"
    from src.mcp_context_authority import release_mcp_bearer_proof
    with pytest.raises(McpContextAuthorityError):
        release_mcp_bearer_proof(proof)


@pytest.mark.parametrize("clock_value", (float("nan"), float("inf")))
def test_factory_nonfinite_monotonic_is_context_unavailable(monkeypatch, clock_value):
    from src import mcp_context_authority as context_authority
    monkeypatch.setattr(context_authority.time, "monotonic", lambda: clock_value)
    with pytest.raises(McpContextAuthorityError) as error:
        issue_mcp_bearer_proof("ody_" + "x" * 43)
    assert error.value.code == "context_unavailable"


def test_factory_raising_monotonic_is_context_unavailable(monkeypatch):
    from src import mcp_context_authority as context_authority
    def raising(): raise RuntimeError("clock")
    monkeypatch.setattr(context_authority.time, "monotonic", raising)
    with pytest.raises(McpContextAuthorityError) as error:
        issue_mcp_bearer_proof("ody_" + "x" * 43)
    assert error.value.code == "context_unavailable"


def test_proof_registry_default_cap_and_no_pending_eviction(monkeypatch):
    from src import mcp_context_authority as context_authority
    assert context_authority._PROOF_CAP == 64
    assert context_authority._PER_SID_CAP == 8
    # Lowering only the test-local cap proves admission fails closed and does
    # not evict the first still-retained pending proof.
    gc.collect()
    with context_authority._PROOF_LOCK: assert not context_authority._PROOFS
    monkeypatch.setattr(context_authority, "_PROOF_CAP", 2)
    first = issue_mcp_bearer_proof("ody_" + "q" * 43)
    second = issue_mcp_bearer_proof("ody_" + "w" * 43)
    with pytest.raises(McpContextAuthorityError) as error:
        issue_mcp_bearer_proof("ody_" + "e" * 43)
    assert error.value.code == "context_unavailable"
    from src.mcp_context_authority import release_mcp_bearer_proof
    release_mcp_bearer_proof(first)
    release_mcp_bearer_proof(second)


def test_closed_admission_discards_exact_pending_proof(tmp_path):
    _manager, authority, identity, proof, _state = _fixture(tmp_path)
    authority.close()
    with pytest.raises(McpContextAuthorityError) as error:
        authority.issue_binding(identity, proof, "chat.1")
    assert error.value.code == "context_unavailable"
    from src.mcp_context_authority import release_mcp_bearer_proof
    with pytest.raises(McpContextAuthorityError):
        release_mcp_bearer_proof(proof)


def test_lowered_per_subject_cap_keeps_existing_winners_and_cleans_failed_pending(tmp_path, monkeypatch):
    from src import mcp_context_authority as context_authority
    monkeypatch.setattr(context_authority, "_PER_SID_CAP", 2)
    _manager, authority, identity, first, _state = _fixture(tmp_path)
    second = issue_mcp_bearer_proof("ody_" + "a" * 43)
    third = issue_mcp_bearer_proof("ody_" + "a" * 43)
    one = authority.issue_binding(identity, first, "chat.1")
    two = authority.issue_binding(identity, second, "chat.1")
    with pytest.raises(McpContextAuthorityError) as error:
        authority.issue_binding(identity, third, "chat.1")
    assert error.value.code == "context_unavailable"
    authority.recheck_binding(one); authority.recheck_binding(two)
    from src.mcp_context_authority import release_mcp_bearer_proof
    with pytest.raises(McpContextAuthorityError): release_mcp_bearer_proof(third)


def test_pending_and_claimed_weak_gc_release_own_registry_state(tmp_path):
    from src import mcp_context_authority as context_authority
    pending = issue_mcp_bearer_proof("ody_" + "p" * 43)
    pending_key = id(pending); pending_ref = weakref.ref(pending)
    del pending; gc.collect()
    assert pending_ref() is None and pending_key not in context_authority._PROOFS
    _manager, authority, identity, proof, _state = _fixture(tmp_path)
    binding = authority.issue_binding(identity, proof, "chat.1")
    proof_key = id(proof); binding_ref = weakref.ref(binding)
    del binding; gc.collect()
    assert binding_ref() is None and proof_key not in context_authority._PROOFS


def test_dead_weak_authority_claim_is_pruned_without_reviving_retained_binding(tmp_path, monkeypatch):
    from src import mcp_context_authority as context_authority
    monkeypatch.setattr(context_authority, "_PROOF_CAP", 1)
    _manager, authority, identity, proof, state = _fixture(tmp_path)
    binding = authority.issue_binding(identity, proof, "chat.1")
    owner_ref = weakref.ref(authority)
    del authority; gc.collect()
    assert owner_ref() is None
    replacement = issue_mcp_bearer_proof("ody_" + "a" * 43)
    fresh = McpContextAuthority(_manager, SessionLocal, app_state=state)
    assert fresh.issue_binding(identity, replacement, "chat.1") is not None
    with pytest.raises(McpContextAuthorityError):
        binding_owner = owner_ref()
        if binding_owner is not None: binding_owner.recheck_binding(binding)
        else: raise McpContextAuthorityError("unauthorized")


def test_dead_owner_is_pruned_before_per_subject_claim_admission(tmp_path, monkeypatch):
    from src import mcp_context_authority as context_authority
    monkeypatch.setattr(context_authority, "_PER_SID_CAP", 1)
    manager, owner_a, identity, first, state = _fixture(tmp_path)
    second = issue_mcp_bearer_proof("ody_" + "a" * 43)
    retained = owner_a.issue_binding(identity, first, "chat.1")
    owner_ref = weakref.ref(owner_a); del owner_a; gc.collect()
    assert owner_ref() is None
    owner_b = McpContextAuthority(manager, SessionLocal, app_state=state)
    assert owner_b.issue_binding(identity, second, "chat.1") is not None
    assert retained is not None


def test_retained_cancel_markers_are_bounded_and_binding_release_clears_them(tmp_path):
    _manager, authority, identity, proof, _state = _fixture(tmp_path)
    binding = authority.issue_binding(identity, proof, "chat.1")
    cancelled = []
    for number in range(64):
        lease = authority.issue_read_lease(binding, f"marker.{number}")
        authority.cancel_lease(lease)
        cancelled.append(lease)
    assert len(authority._leases) == 0 and len(authority._cancelled_leases) == 64
    extra = authority.issue_read_lease(binding, "marker.extra")
    with pytest.raises(McpContextAuthorityError) as error:
        authority.cancel_lease(extra)
    assert error.value.code == "context_unavailable"
    with pytest.raises(McpContextAuthorityError): authority.recheck_lease(extra)
    with pytest.raises(McpContextAuthorityError) as error: authority.recheck_lease(cancelled[0])
    assert error.value.code == "cancelled"
    authority.release_read_lease(cancelled[0])
    replacement = authority.issue_read_lease(binding, "marker.replacement")
    authority.cancel_lease(replacement)
    with pytest.raises(McpContextAuthorityError): authority.recheck_lease(cancelled[0])
    authority.release_binding(binding)
    assert not authority._leases and not authority._cancelled_leases
    with pytest.raises(McpContextAuthorityError): authority.recheck_binding(binding)


@pytest.mark.parametrize("body_fails,exit_fails", ((False, False), (False, True), (True, False), (True, True)))
def test_live_lease_four_body_exit_cases(tmp_path, body_fails, exit_fails):
    _manager, authority, identity, proof, state = _fixture(tmp_path)
    binding = authority.issue_binding(identity, proof, "chat.1")
    lease = authority.issue_read_lease(binding, "exit.matrix")
    marker = RuntimeError("body marker")
    if body_fails:
        with pytest.raises(Exception) as error:
            with authority.live_lease(lease):
                if exit_fails: state.allow_context_reads = False
                raise marker
        if exit_fails:
            assert type(error.value) is McpContextAuthorityError and error.value.code == "disabled"
            assert error.value.__cause__ is None and error.value.__context__ is None
        else:
            assert error.value is marker
    elif exit_fails:
        with pytest.raises(McpContextAuthorityError) as error:
            with authority.live_lease(lease): state.allow_context_reads = False
        assert error.value.code == "disabled" and error.value.__cause__ is None
    else:
        with authority.live_lease(lease) as current: assert current is binding


def test_live_lease_entry_failure_never_enters_body(tmp_path):
    _manager, authority, identity, proof, state = _fixture(tmp_path)
    binding = authority.issue_binding(identity, proof, "chat.1")
    lease = authority.issue_read_lease(binding, "entry.fail")
    state.allow_context_reads = False
    entered = [False]
    with pytest.raises(McpContextAuthorityError):
        with authority.live_lease(lease): entered[0] = True
    assert entered == [False]


@pytest.mark.parametrize("kind,field", [
    ("binding", "__issuer"), ("binding", "__authority"),
    ("lease", "__issuer"), ("lease", "__authority"),
])
def test_private_issuer_and_authority_tampering_is_rejected(tmp_path, kind, field):
    _manager, authority, identity, proof, _state = _fixture(tmp_path)
    binding = authority.issue_binding(identity, proof, "chat.1")
    lease = authority.issue_read_lease(binding, "private.fields")
    handle = binding if kind == "binding" else lease
    prefix = "_McpContextBinding" if kind == "binding" else "_McpContextReadLease"
    name = prefix + field
    original = object.__getattribute__(handle, name)
    object.__setattr__(handle, name, object())
    with pytest.raises(McpContextAuthorityError) as error:
        authority.recheck_binding(binding) if kind == "binding" else authority.recheck_lease(lease)
    assert error.value.code == "unauthorized"
    object.__setattr__(handle, name, original)
    if kind == "binding":
        assert authority.recheck_binding(binding) is None
    else:
        assert authority.recheck_lease(lease) is binding


@pytest.mark.parametrize("handle_kind", ("binding", "lease"))
def test_registered_global_proof_loss_rejects_exact_handles(tmp_path, handle_kind):
    from src import mcp_context_authority as context_authority
    _manager, authority, identity, proof, _state = _fixture(tmp_path)
    binding = authority.issue_binding(identity, proof, "chat.1")
    lease = authority.issue_read_lease(binding, "global.loss")
    context_authority._PROOFS.pop(id(proof), None)
    with pytest.raises(McpContextAuthorityError) as error:
        authority.recheck_binding(binding) if handle_kind == "binding" else authority.recheck_lease(lease)
    assert error.value.code == "unauthorized"


@pytest.mark.parametrize("operation", ("release", "close"))
def test_post_external_validation_invalidation_fails_closed(tmp_path, operation):
    _manager, authority, identity, proof, _state = _fixture(tmp_path)
    binding = authority.issue_binding(identity, proof, "chat.1")
    lease = authority.issue_read_lease(binding, "post.validation")
    original = authority._row_and_chat
    def wrapped(*args):
        original(*args)
        if operation == "release": authority.release_binding(binding)
        else: authority.close()
    authority._row_and_chat = wrapped
    with pytest.raises(McpContextAuthorityError): authority.recheck_binding(binding)
    with pytest.raises(McpContextAuthorityError): authority.recheck_lease(lease)


@pytest.mark.parametrize("field", ("__native", "__issuer"))
def test_registered_proof_private_transplant_is_rejected(tmp_path, field):
    _manager, authority, identity, proof, _state = _fixture(tmp_path)
    binding = authority.issue_binding(identity, proof, "chat.1")
    lease = authority.issue_read_lease(binding, "proof.transplant")
    other = issue_mcp_bearer_proof("ody_" + "a" * 43)
    name = "_McpContextBearerProof" + field
    original = object.__getattribute__(proof, name)
    object.__setattr__(proof, name, object.__getattribute__(other, name))
    with pytest.raises(McpContextAuthorityError): authority.recheck_binding(binding)
    with pytest.raises(McpContextAuthorityError): authority.recheck_lease(lease)
    object.__setattr__(proof, name, original)
    assert authority.recheck_binding(binding) is None


def test_forged_handle_clones_and_subclasses_are_rejected(tmp_path):
    _manager, authority, identity, proof, _state = _fixture(tmp_path)
    binding = authority.issue_binding(identity, proof, "chat.1")
    lease = authority.issue_read_lease(binding, "clone.handles")
    for klass in (McpContextBearerProof, McpContextBinding, McpContextReadLease):
        with pytest.raises(TypeError):
            type("Child", (klass,), {})
    forged = object.__new__(McpContextBinding)
    for name in ("__issuer", "__authority", "__proof", "__token_id", "__owner_alias", "__owner_subject_id", "__scope", "__cache_generation", "__security_action_revision", "__chat_session_id", "__process_epoch", "__audience"):
        object.__setattr__(forged, "_McpContextBinding" + name, object.__getattribute__(binding, "_McpContextBinding" + name))
    with pytest.raises(McpContextAuthorityError): authority.recheck_binding(forged)
    assert authority.recheck_lease(lease) is binding


@pytest.mark.parametrize("kind", ("proof", "binding", "lease"))
def test_exact_class_slot_clones_and_copies_fail_registered_authority(tmp_path, kind):
    _manager, authority, identity, proof, _state = _fixture(tmp_path)
    binding = authority.issue_binding(identity, proof, "chat.1")
    lease = authority.issue_read_lease(binding, "clone.exact")
    original = {"proof": proof, "binding": binding, "lease": lease}[kind]
    clone = object.__new__(type(original))
    for slot in type(original).__slots__:
        if slot == "__weakref__": continue
        mangled = "_" + type(original).__name__ + slot if slot.startswith("__") else slot
        object.__setattr__(clone, mangled, object.__getattribute__(original, mangled))
    with pytest.raises(McpContextAuthorityError):
        if kind == "proof": authority.issue_binding(identity, clone, "chat.1")
        elif kind == "binding": authority.recheck_binding(clone)
        else: authority.recheck_lease(clone)
    try:
        copied = copy.copy(original)
    except (AttributeError, TypeError):
        copied = None
    if copied is not None:
        with pytest.raises(McpContextAuthorityError):
            if kind == "proof": authority.issue_binding(identity, copied, "chat.1")
            elif kind == "binding": authority.recheck_binding(copied)
            else: authority.recheck_lease(copied)
    assert authority.recheck_binding(binding) is None
    assert authority.recheck_lease(lease) is binding


def test_live_lease_both_failure_drops_body_marker_and_traceback(tmp_path):
    class Marker: pass
    class Body(Exception): pass
    _manager, authority, identity, proof, state = _fixture(tmp_path)
    binding = authority.issue_binding(identity, proof, "chat.1")
    lease = authority.issue_read_lease(binding, "exit.weak")
    refs = []
    def bodyhelper():
        marker = Marker(); refs.append(weakref.ref(marker))
        state.allow_context_reads = False
        raise Body(marker)
    def run():
        try:
            with authority.live_lease(lease): bodyhelper()
        except McpContextAuthorityError as error:
            return error
    fixed = run(); gc.collect()
    assert fixed.code == "disabled" and fixed.__cause__ is None and fixed.__context__ is None
    assert refs[0]() is None
    names = []
    trace = fixed.__traceback__
    while trace is not None:
        names.append(trace.tb_frame.f_code.co_name); trace = trace.tb_next
    assert "bodyhelper" not in names


@pytest.mark.parametrize("field,value,code", [
    ("mcp_context_plugin_enabled", 1, "disabled"), ("mcp_context_plugin_enabled", None, "disabled"),
    ("allow_context_reads", 0, "disabled"), ("allow_context_reads", "yes", "disabled"),
    ("_token_cache_generation", True, "stale_authentication"), ("_token_cache_generation", "4", "stale_authentication"),
    ("_token_cache_generation", -1, "stale_authentication"), ("_token_cache_dirty", 0, "stale_authentication"),
    ("_token_cache_dirty", None, "stale_authentication"), ("_process_epoch", None, "stale_authentication"),
    ("_process_epoch", 4, "stale_authentication"), ("_process_epoch", "epoch_bad", "stale_authentication"),
])
def test_live_state_exact_type_matrix_is_closed(tmp_path, field, value, code):
    _manager, authority, identity, proof, state = _fixture(tmp_path)
    setattr(state, field, value)
    with pytest.raises(McpContextAuthorityError) as error: authority.issue_binding(identity, proof, "chat.1")
    assert error.value.code == code


def test_real_security_revision_drift_revokes_binding_and_lease(tmp_path):
    manager, authority, identity, proof, _state = _fixture(tmp_path)
    binding = authority.issue_binding(identity, proof, "chat.1")
    lease = authority.issue_read_lease(binding, "security.drift")
    before = manager.security_action_revision()
    assert manager.create_user("security-other", "correct horse battery staple", is_admin=False) is True
    assert manager.rename_user("security-other", "security-other-renamed", "admin") is True
    assert manager.security_action_revision() > before
    with pytest.raises(McpContextAuthorityError) as error: authority.recheck_binding(binding)
    assert error.value.code == "stale_authentication"
    with pytest.raises(McpContextAuthorityError) as error: authority.recheck_lease(lease)
    assert error.value.code == "stale_authentication"
    with pytest.raises(McpContextAuthorityError) as error:
        authority.issue_read_lease(binding, "ttl.too-high", ttl_seconds=901)
    assert error.value.code == "invalid_request"


def test_foreign_release_and_second_live_lease_are_rejected(tmp_path):
    manager, authority, identity, proof, state = _fixture(tmp_path)
    binding = authority.issue_binding(identity, proof, "chat.1")
    foreign = McpContextAuthority(manager, SessionLocal, app_state=state)
    with pytest.raises(McpContextAuthorityError): foreign.release_binding(binding)
    authority.recheck_binding(binding)
    lease = authority.issue_read_lease(binding, "one.1")
    assert authority.require_live_lease(lease) is binding
    with pytest.raises(McpContextAuthorityError) as error: authority.issue_read_lease(binding, "one.2")
    assert error.value.code == "context_unavailable"


def test_live_lease_preserves_body_failure_when_exit_is_valid(tmp_path):
    _manager, authority, identity, proof, _state = _fixture(tmp_path)
    binding = authority.issue_binding(identity, proof, "chat.1")
    lease = authority.issue_read_lease(binding, "exit.1")
    marker = RuntimeError("body")
    with pytest.raises(RuntimeError) as error:
        with authority.live_lease(lease): raise marker
    assert error.value is marker

