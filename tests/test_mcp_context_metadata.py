from __future__ import annotations

import copy
import gc
import json
import pickle
import threading
import weakref
from dataclasses import replace
from types import SimpleNamespace

import bcrypt
import pytest
from sqlalchemy.orm import sessionmaker

from core.database import ApiToken, Base, Session as DbSession
from src.context_manifest_producer import CombinedContextManifestProducer, ContextManifestProducerError
from src.forge_context_metadata import ForgeContextMetadataError
from src.mcp_authenticated_dispatch import McpAuthenticatedIdentity
from src.mcp_context_authority import (
    McpContextAuthority,
    McpContextAuthorityError,
    McpContextBearerProof,
    McpContextReadLease,
    issue_mcp_bearer_proof,
)
from src.context_metadata_binding import (
    McpContextMetadataAdmissionError,
    McpContextMetadataBinding,
    _mcp_recipient_digest,
    _open_mcp_metadata_admission,
    _require_mcp_metadata_admission,
)
import src.context_metadata_binding as context_metadata_binding
from src.planning_context_reader import PlanningContextManifestProducer
from src.planning_durable_context_source import (
    DEFAULT_DURABLE_PLANNING_CONTEXT_POLICY,
    DurablePlanningContextError,
    DurablePlanningContextSource,
)
from src.planning_context_reader import PlanningContextReaderError
from src.harbour_knowledge_active_runtime import KnowledgeContextError
from src.private_context_confirmation_app import ContextSelection, CookieConfirmationAuthority
from src.private_context_confirmation import ConfirmationError, ContextManifest, ManifestItem
from src.repo_registry import RepoRegistry

from tests.test_context_manifest_producer import _real_three_domain_context_body
from tests.test_harbour_knowledge_context_reader import public_context_fixture
from tests.test_planning_durable_context_source import _append_clean_descendants, _append_revision


def _install_real_mcp_authority(fixture: dict[str, object]) -> McpContextAuthority:
    auth = fixture["auth"]
    session_factory = fixture["session_factory"]
    owner_sid = auth.subject_id_for_username("fuzzy")
    raw_tokens = ("ody_" + "m" * 43, "ody_" + "n" * 43)
    state = SimpleNamespace(
        _token_cache_generation=4,
        _token_cache_dirty=False,
        mcp_context_plugin_enabled=True,
        allow_context_reads=True,
        _process_epoch="epoch_" + "c" * 32,
    )
    Base.metadata.create_all(session_factory.kw["bind"])
    with session_factory() as db:
        for token_id, raw in zip(("mcp-metadata-token", "mcp-metadata-token-2"), raw_tokens):
            db.add(ApiToken(
                id=token_id, owner="fuzzy", name="metadata fixture",
                token_hash=bcrypt.hashpw(raw.encode("ascii"), bcrypt.gensalt()).decode("ascii"),
                token_prefix=raw[:8], scopes="mcp:harbour:context:read", owner_subject_id=owner_sid,
                is_active=True,
            ))
        owned = db.query(DbSession).filter(DbSession.id == "chat-owned").one()
        assert owned.owner == "fuzzy"
        db.add(DbSession(
            id="chat-owned-2", name="Owned two", endpoint_url="local", model="test", owner="fuzzy",
            message_count=0,
        ))
        db.commit()
    fixture["mcp_raw_tokens"] = raw_tokens
    fixture["mcp_state"] = state
    return McpContextAuthority(auth, session_factory, app_state=state)


@pytest.fixture
def native_mcp_fixture(tmp_path, monkeypatch):
    body = _real_three_domain_context_body(tmp_path, monkeypatch)
    fixture = next(body)
    authority = None
    try:
        auth = fixture["auth"]
        runtime = fixture["planning_producer"]._runtime
        owner_sid = auth.subject_id_for_username("fuzzy")
        principal = runtime.issuer.issue(
            subject_id=owner_sid, owner_id="fuzzy", auth_context_digest="sha256:" + "5" * 64,
            ttl_seconds=3600, nonce="native-mcp-test-principal",
        )
        with CookieConfirmationAuthority(auth).hold(fixture["cookie"], fixture["chat_id"]) as setup_binding:
            _append_clean_descendants(
                {"store": runtime.durable_store, "principal": principal}, setup_binding,
            )
            durable_source = DurablePlanningContextSource(
                runtime=runtime, auth_manager=auth, project_id="harbour-one",
                policy=DEFAULT_DURABLE_PLANNING_CONTEXT_POLICY,
            )
            planning_items = durable_source.discover(setup_binding)
        native_planning = PlanningContextManifestProducer(
            runtime, auth, None, "harbour-one", durable_source=durable_source,
        )
        selection = ContextSelection(tuple(sorted(
            (fixture["forge_selection"], *planning_items, fixture["knowledge_target"]),
            key=lambda item: (item.source, item.selection_ref, item.revision_ref),
        )))
        authority = _install_real_mcp_authority(fixture)
        combined = CombinedContextManifestProducer(
            auth_manager=auth, forge=fixture["forge"], planning=native_planning,
            knowledge=fixture["knowledge_producer"], mcp_authority=authority,
        )
        fixture.update({
            "authority": authority, "planning_producer": native_planning,
            "selection": selection, "principal": principal, "durable_source": durable_source,
            "combined": combined,
        })
        yield fixture
    finally:
        # This fixture owns only the MCP authority it installs.  The generator
        # retains ownership of its stores and must perform its own teardown.
        if authority is not None:
            authority.close()
        try:
            next(body)
        except StopIteration:
            pass


def _lease(fixture: dict[str, object], request_id: str = "metadata-request", *, token_index: int = 0):
    auth = fixture["auth"]
    authority = fixture["authority"]
    owner_sid = auth.subject_id_for_username("fuzzy")
    identity = McpAuthenticatedIdentity(
        f"mcp-metadata-token{'-2' if token_index else ''}", "fuzzy", owner_sid,
        fixture["mcp_state"]._token_cache_generation,
        auth.security_action_revision(), "mcp:harbour:context:read",
    )
    proof = issue_mcp_bearer_proof(fixture["mcp_raw_tokens"][token_index])
    binding = authority.issue_binding(identity, proof, "chat-owned" if token_index == 0 else "chat-owned-2")
    return authority.issue_read_lease(binding, request_id)


def test_real_three_domain_native_metadata_is_detached_and_recipient_bound(native_mcp_fixture):
    fixture = native_mcp_fixture
    lease = _lease(fixture)
    snapshot = fixture["combined"].capture_mcp_snapshot(lease, fixture["selection"])
    assert len(snapshot.items) == 8
    assert snapshot.mcp_metadata_binding is not None
    assert len(snapshot.target_manifest_digests) == len(fixture["selection"].items)
    assert all(len(value) == 64 for value in snapshot.target_manifest_digests)
    assert all(type(item) is ManifestItem for item in snapshot.items)
    binding = snapshot.mcp_metadata_binding
    assert set(binding.canonical()) == {
        "schema", "audience", "owner_sid", "chat_session_id", "recipient_token_id",
        "scope", "cache_generation", "security_revision", "app_epoch",
    }
    rendered = repr(snapshot)
    assert all(secret not in rendered for secret in (*fixture["mcp_raw_tokens"], fixture["cookie"]))
    assert all(name not in rendered for name in ("McpContextAuthority", "McpContextReadLease", "McpContextBearerProof"))
    fixture["authority"].release_read_lease(lease)


def test_knowledge_mcp_manifest_binding_fields_are_raw_sha256(native_mcp_fixture):
    fixture = native_mcp_fixture
    lease = _lease(fixture, "knowledge-raw-owner-digest")
    selection = ContextSelection(tuple(item for item in fixture["selection"].items if item.source == "knowledge"))
    with _open_mcp_metadata_admission(fixture["authority"], lease) as (admission, metadata):
        manifest, target = fixture["knowledge_producer"].capture_metadata_mcp(admission, metadata, selection)
        assert type(target) is str and len(target) == 64
        assert all(type(value) is str and len(value) == 64 for value in (
            manifest.owner_binding_digest, manifest.selection_binding_digest,
            manifest.canonical_generation_digest, manifest.usi_snapshot_digest,
            manifest.captured_head_boundary, manifest.binding_digest,
        ))
        assert all(len(item.boundary_sha256) == len(item.record_sha256) == 64 for item in manifest.items)
    fixture["authority"].release_read_lease(lease)


def test_native_planning_mcp_source_keeps_prefixed_internal_digests_and_public_items_raw(native_mcp_fixture):
    fixture = native_mcp_fixture
    lease = _lease(fixture, "planning-prefixed-source")
    planning_selection = ContextSelection(tuple(
        item for item in fixture["selection"].items if item.source == "planning"
    ))
    with _open_mcp_metadata_admission(fixture["authority"], lease) as (admission, metadata):
        descriptors = fixture["durable_source"].resolve_mcp(admission, metadata, planning_selection)
        assert descriptors
        assert all(value.startswith("sha256:") and len(value) == 71 for descriptor in descriptors for value in (
            descriptor.boundary_sha256, descriptor.record_sha256,
        ))
        snapshot = fixture["durable_source"]._stable_capture_mcp(
            admission, metadata, fixture["principal"], "fuzzy", lease.binding,
        ).snapshot
        assert snapshot.metadata_sha256.startswith("sha256:") and len(snapshot.metadata_sha256) == 71
    captured = fixture["combined"].capture_mcp_snapshot(lease, planning_selection)
    assert all(len(item.boundary_sha256) == len(item.record_sha256) == 64 for item in captured.items)
    fixture["authority"].release_read_lease(lease)


def test_native_snapshot_has_only_detached_manifest_and_metadata_values(native_mcp_fixture):
    fixture = native_mcp_fixture
    lease = _lease(fixture, "detached-value-graph")
    snapshot = fixture["combined"].capture_mcp_snapshot(lease, fixture["selection"])

    def assert_detached(value):
        if value is None or type(value) in {str, int, bool}:
            return
        if type(value) is tuple:
            for child in value:
                assert_detached(child)
            return
        if type(value) is ManifestItem:
            assert_detached(tuple(value._canonical().values()))
            return
        if type(value) is McpContextMetadataBinding:
            assert_detached(tuple(value.canonical().values()))
            return
        pytest.fail("native snapshot exposed a non-detached value: " + type(value).__name__)

    assert_detached(snapshot.items)
    assert_detached(snapshot.target_manifest_digests)
    assert_detached(snapshot.mcp_metadata_binding)
    fixture["authority"].release_read_lease(lease)


def test_two_valid_recipient_leases_share_native_items_but_change_digests(native_mcp_fixture):
    fixture = native_mcp_fixture
    first = _lease(fixture, "recipient-one")
    first_snapshot = fixture["combined"].capture_mcp_snapshot(first, fixture["selection"])
    fixture["authority"].release_read_lease(first)
    second = _lease(fixture, "recipient-two", token_index=1)
    second_snapshot = fixture["combined"].capture_mcp_snapshot(second, fixture["selection"])
    first_by_key = {
        (item.source, item.selection_ref, item.revision_ref): item
        for item in first_snapshot.items
    }
    second_by_key = {
        (item.source, item.selection_ref, item.revision_ref): item
        for item in second_snapshot.items
    }
    assert first_by_key.keys() == second_by_key.keys()
    for key, first_item in first_by_key.items():
        second_item = second_by_key[key]
        assert (
            first_item.payload_sha256,
            first_item.provenance_ref,
            first_item.classification,
        ) == (
            second_item.payload_sha256,
            second_item.provenance_ref,
            second_item.classification,
        )
        assert first_item.boundary_sha256 != second_item.boundary_sha256
        assert first_item.record_sha256 != second_item.record_sha256
    assert first_snapshot.target_manifest_digests != second_snapshot.target_manifest_digests
    assert first_snapshot.mcp_metadata_binding != second_snapshot.mcp_metadata_binding
    fixture["authority"].release_read_lease(second)


@pytest.mark.parametrize("field,replacement", [
    ("owner_sid", "owner_other"), ("chat_session_id", "chat-other"),
    ("recipient_token_id", "recipient-other"), ("cache_generation", 5),
    ("security_revision", None), ("app_epoch", "epoch_" + "z" * 32),
])
def test_recipient_digest_changes_for_each_recipient_binding_field(native_mcp_fixture, field, replacement):
    fixture = native_mcp_fixture
    lease = _lease(fixture, "recipient-field-" + field)
    with _open_mcp_metadata_admission(fixture["authority"], lease) as (_admission, metadata):
        if replacement is None:
            replacement = metadata.security_revision + 1
        changed = replace(metadata, **{field: replacement})
        body = {"kind": "recipient-field", "fixed": True}
        actual = _mcp_recipient_digest("odysseus.context.mcp_metadata.knowledge.boundary.v1", metadata, body)
        altered = _mcp_recipient_digest("odysseus.context.mcp_metadata.knowledge.boundary.v1", changed, body)
        assert type(actual) is str and len(actual) == 64
        assert type(altered) is str and len(altered) == 64
        assert actual != altered
        assert (changed.schema, changed.audience, changed.scope) == (metadata.schema, metadata.audience, metadata.scope)
    fixture["authority"].release_read_lease(lease)


def test_metadata_binding_is_exactly_nine_scalars_and_admission_is_single_use(native_mcp_fixture):
    fixture = native_mcp_fixture
    lease = _lease(fixture, "binding-shape")
    with _open_mcp_metadata_admission(fixture["authority"], lease) as (admission, metadata):
        assert type(metadata) is McpContextMetadataBinding
        assert tuple(metadata.canonical()) == (
            "schema", "audience", "owner_sid", "chat_session_id", "recipient_token_id",
            "scope", "cache_generation", "security_revision", "app_epoch",
        )
        assert _require_mcp_metadata_admission(admission, metadata) is lease.binding
        with pytest.raises(ValueError):
            with _open_mcp_metadata_admission(fixture["authority"], lease):
                pass
    fixture["authority"].release_read_lease(lease)


def test_metadata_binding_rejects_hostile_scalar_types_before_comparison(native_mcp_fixture):
    fixture = native_mcp_fixture
    lease = _lease(fixture, "binding-hostile-scalars")
    try:
        with _open_mcp_metadata_admission(fixture["authority"], lease) as (_admission, metadata):
            values = metadata.canonical()
            assert all(type(values[name]) is str for name in (
                "schema", "audience", "owner_sid", "chat_session_id", "recipient_token_id", "scope", "app_epoch",
            ))
            assert all(type(values[name]) is int for name in ("cache_generation", "security_revision"))

            callbacks = []

            class HostileScalar:
                def __eq__(self, other):
                    callbacks.append("eq")
                    return True

                def __lt__(self, other):
                    callbacks.append("lt")
                    return True

                def __bool__(self):
                    callbacks.append("bool")
                    return True

                def __len__(self):
                    callbacks.append("len")
                    return 1

            class HostileString(str):
                def __eq__(self, other):
                    callbacks.append("str_eq")
                    return True

            class HostileInteger(int):
                def __lt__(self, other):
                    callbacks.append("int_lt")
                    return True

            for name in values:
                malformed = dict(values)
                malformed[name] = HostileScalar()
                with pytest.raises(ValueError, match="^invalid_binding$"):
                    McpContextMetadataBinding(**malformed)
                assert callbacks == []

            for name in ("schema", "audience", "owner_sid", "chat_session_id", "recipient_token_id", "scope", "app_epoch"):
                malformed = dict(values)
                malformed[name] = HostileString(values[name])
                with pytest.raises(ValueError, match="^invalid_binding$"):
                    McpContextMetadataBinding(**malformed)
                assert callbacks == []

            for name in ("cache_generation", "security_revision"):
                malformed = dict(values)
                malformed[name] = HostileInteger(values[name])
                with pytest.raises(ValueError, match="^invalid_binding$"):
                    McpContextMetadataBinding(**malformed)
                assert callbacks == []

            for name in ("cache_generation", "security_revision"):
                malformed = dict(values)
                malformed[name] = True
                with pytest.raises(ValueError, match="^invalid_binding$"):
                    McpContextMetadataBinding(**malformed)
                assert callbacks == []
    finally:
        fixture["authority"].release_read_lease(lease)


def test_metadata_admission_cannot_replay_after_exit_or_open_twice(native_mcp_fixture):
    fixture = native_mcp_fixture
    lease = _lease(fixture, "admission-after-exit")
    with _open_mcp_metadata_admission(fixture["authority"], lease) as (admission, metadata):
        with pytest.raises(ValueError):
            with _open_mcp_metadata_admission(fixture["authority"], lease):
                pass
        assert _require_mcp_metadata_admission(admission, metadata) is lease.binding
    with pytest.raises(McpContextMetadataAdmissionError):
        _require_mcp_metadata_admission(admission, metadata)
    fixture["authority"].release_read_lease(lease)


def test_metadata_admission_weakref_cleanup_reopens_the_same_live_lease(native_mcp_fixture):
    fixture = native_mcp_fixture
    lease = _lease(fixture, "admission-weakref-cleanup")
    manager = _open_mcp_metadata_admission(fixture["authority"], lease)
    admission, metadata = manager.__enter__()
    admission_ref, metadata_ref = weakref.ref(admission), weakref.ref(metadata)
    assert _require_mcp_metadata_admission(admission, metadata) is lease.binding
    del admission, metadata, manager
    gc.collect()
    assert admission_ref() is None and metadata_ref() is None
    with _open_mcp_metadata_admission(fixture["authority"], lease) as (reopened, reopened_metadata):
        assert _require_mcp_metadata_admission(reopened, reopened_metadata) is lease.binding
    fixture["authority"].release_read_lease(lease)


def test_metadata_admission_cap_rejects_second_real_lease_until_first_exits(native_mcp_fixture, monkeypatch):
    fixture = native_mcp_fixture
    assert context_metadata_binding._ADMISSION_CAP == 64
    monkeypatch.setattr(context_metadata_binding, "_ADMISSION_CAP", 1)
    first = _lease(fixture, "admission-cap-first", token_index=0)
    second = _lease(fixture, "admission-cap-second", token_index=1)
    manager = _open_mcp_metadata_admission(fixture["authority"], first)
    try:
        admission, metadata = manager.__enter__()
        assert _require_mcp_metadata_admission(admission, metadata) is first.binding
        with pytest.raises(McpContextMetadataAdmissionError):
            with _open_mcp_metadata_admission(fixture["authority"], second):
                pass
    finally:
        manager.__exit__(None, None, None)
    with _open_mcp_metadata_admission(fixture["authority"], second) as (admission, metadata):
        assert _require_mcp_metadata_admission(admission, metadata) is second.binding
    fixture["authority"].release_read_lease(first)
    fixture["authority"].release_read_lease(second)


def test_mcp_session_factory_pin_rejects_same_database_sessionmaker_clone(native_mcp_fixture, monkeypatch):
    fixture = native_mcp_fixture
    authority = fixture["authority"]
    clone = sessionmaker(bind=fixture["session_factory"].kw["bind"])
    lease = _lease(fixture, "factory-direct-pin")
    monkeypatch.setattr(authority, "_session_factory", clone)
    with pytest.raises(McpContextAuthorityError) as error:
        authority.recheck_lease(lease)
    assert error.value.code == "stale_authentication"


def test_mcp_factory_swap_rejects_live_admission_and_combined_provider(native_mcp_fixture, monkeypatch):
    fixture = native_mcp_fixture
    authority = fixture["authority"]
    clone = sessionmaker(bind=fixture["session_factory"].kw["bind"])
    lease = _lease(fixture, "factory-admission-pin")
    with _open_mcp_metadata_admission(authority, lease) as (admission, metadata):
        with monkeypatch.context() as swapped:
            swapped.setattr(authority, "_session_factory", clone)
            with pytest.raises(McpContextMetadataAdmissionError) as admission_error:
                _require_mcp_metadata_admission(admission, metadata)
            assert str(admission_error.value) == "invalid_binding"
    with monkeypatch.context() as swapped:
        swapped.setattr(authority, "_session_factory", clone)
        with pytest.raises(ContextManifestProducerError) as producer_error:
            fixture["combined"].capture_mcp_snapshot(lease, fixture["selection"])
        assert producer_error.value.code == "invalid_provider"

    exit_type = type(authority.live_lease(lease))
    real_exit = exit_type.__exit__
    exited = []

    def exit_then_swap(self, exc_type, exc, tb):
        result = real_exit(self, exc_type, exc, tb)
        if self._authority is authority:
            exited.append(True)
            authority._session_factory = clone
        return result

    with monkeypatch.context() as swapped:
        swapped.setattr(exit_type, "__exit__", exit_then_swap)
        with pytest.raises(ContextManifestProducerError) as exit_error:
            fixture["combined"].capture_mcp_snapshot(lease, fixture["selection"])
        assert exit_error.value.code == "invalid_provider"
        assert exited


def test_real_metadata_admission_global_cap_releases_only_an_exited_slot(native_mcp_fixture):
    """Exercise the real 64-row metadata cap with eight persisted owners."""
    fixture = native_mcp_fixture
    auth = fixture["auth"]
    authority = fixture["authority"]
    state = fixture["mcp_state"]
    retained = []
    with fixture["session_factory"]() as db:
        for owner_number in range(8):
            owner = "metadata-owner-" + str(owner_number)
            assert auth.create_user(owner, "Password-123", is_admin=True)
            owner_sid = auth.subject_id_for_username(owner)
            for token_number in range(8):
                raw = "ody_" + (str(owner_number) + str(token_number)).ljust(43, "x")
                token_id = "metadata-cap-" + str(owner_number) + "-" + str(token_number)
                chat_id = "metadata-cap-chat-" + str(owner_number) + "-" + str(token_number)
                db.add(ApiToken(id=token_id, owner=owner, name="metadata capacity",
                    token_hash=bcrypt.hashpw(raw.encode("ascii"), bcrypt.gensalt()).decode("ascii"),
                    token_prefix=raw[:8], scopes="mcp:harbour:context:read", owner_subject_id=owner_sid,
                    is_active=True))
                db.add(DbSession(id=chat_id, name="Metadata capacity", endpoint_url="local", model="test",
                    owner=owner, message_count=0))
                retained.append((owner, owner_sid, token_id, chat_id, raw))
        db.commit()
    managers = []
    handles = []
    reopened = None
    try:
        for number, (owner, owner_sid, token_id, chat_id, raw) in enumerate(retained):
            identity = McpAuthenticatedIdentity(token_id, owner, owner_sid, state._token_cache_generation,
                auth.security_action_revision(), "mcp:harbour:context:read")
            binding = authority.issue_binding(identity, issue_mcp_bearer_proof(raw), chat_id)
            lease = authority.issue_read_lease(binding, "metadata-cap-" + str(number))
            manager = _open_mcp_metadata_admission(authority, lease)
            admission, metadata = manager.__enter__()
            assert _require_mcp_metadata_admission(admission, metadata) is binding
            managers.append(manager)
            handles.append((binding, lease))
        assert len(handles) == len(managers) == 64
        with pytest.raises(McpContextAuthorityError) as error:
            issue_mcp_bearer_proof("ody_" + "z" * 43)
        assert error.value.code == "context_unavailable"
        assert all(authority.recheck_lease(lease) is binding for binding, lease in handles)
        managers.pop().__exit__(None, None, None)
        reopened = _open_mcp_metadata_admission(authority, handles[-1][1])
        admission, metadata = reopened.__enter__()
        assert _require_mcp_metadata_admission(admission, metadata) is handles[-1][0]
    finally:
        if reopened is not None:
            reopened.__exit__(None, None, None)
        for manager in reversed(managers):
            manager.__exit__(None, None, None)
        for binding, lease in handles:
            authority.release_read_lease(lease)
            authority.release_binding(binding)


def test_forged_metadata_and_admission_objects_are_rejected(native_mcp_fixture):
    fixture = native_mcp_fixture
    lease = _lease(fixture, "forged-metadata")
    with _open_mcp_metadata_admission(fixture["authority"], lease) as (admission, metadata):
        forged_metadata = McpContextMetadataBinding(
            metadata.schema, metadata.audience, metadata.owner_sid, metadata.chat_session_id,
            "foreign-token", metadata.scope, metadata.cache_generation,
            metadata.security_revision, metadata.app_epoch,
        )
        forged_admission = object.__new__(type(admission))
        with pytest.raises(McpContextMetadataAdmissionError):
            _require_mcp_metadata_admission(forged_admission, metadata)
        with pytest.raises(McpContextMetadataAdmissionError):
            _require_mcp_metadata_admission(admission, forged_metadata)
        with pytest.raises(TypeError):
            type("ForgedAdmission", (type(admission),), {})
    fixture["authority"].release_read_lease(lease)


def test_live_lease_cannot_cross_threads_or_replay_after_capture(native_mcp_fixture):
    fixture = native_mcp_fixture
    lease = _lease(fixture, "thread-replay")
    errors = []

    with _open_mcp_metadata_admission(fixture["authority"], lease) as (admission, metadata):
        def read_from_other_thread():
            try:
                _require_mcp_metadata_admission(admission, metadata)
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=read_from_other_thread)
        worker.start()
        worker.join()
        assert len(errors) == 1
        assert type(errors[0]) is McpContextMetadataAdmissionError
    fixture["combined"].capture_mcp_snapshot(lease, fixture["selection"])
    fixture["authority"].release_read_lease(lease)
    with pytest.raises((ContextManifestProducerError, McpContextAuthorityError)):
        fixture["combined"].capture_mcp_snapshot(lease, fixture["selection"])


def test_live_admission_cannot_cross_process_identity(native_mcp_fixture, monkeypatch):
    fixture = native_mcp_fixture
    lease = _lease(fixture, "pid-replay")
    with _open_mcp_metadata_admission(fixture["authority"], lease) as (admission, metadata):
        real_getpid = context_metadata_binding.os.getpid
        real_pid = real_getpid()
        monkeypatch.setattr(context_metadata_binding.os, "getpid", lambda: real_pid + 1)
        with pytest.raises(McpContextMetadataAdmissionError):
            _require_mcp_metadata_admission(admission, metadata)
        monkeypatch.setattr(context_metadata_binding.os, "getpid", real_getpid)
    fixture["authority"].release_read_lease(lease)


@pytest.mark.parametrize("mutation,error_type,code", [
    ("missing", ContextManifestProducerError, "invalid_binding"),
    ("foreign", McpContextAuthorityError, "unauthorized"),
    ("subclass", McpContextAuthorityError, "unauthorized"),
])
def test_mcp_entry_requires_exact_live_lease(native_mcp_fixture, mutation, error_type, code):
    fixture = native_mcp_fixture
    lease = _lease(fixture, "hostile-" + mutation)
    candidate = lease
    if mutation == "missing":
        candidate = object()
    elif mutation == "foreign":
        candidate = _lease(fixture, "foreign-source")
        fixture["authority"].release_read_lease(candidate)
    elif mutation == "subclass":
        candidate = object.__new__(McpContextReadLease)
    with pytest.raises(error_type) as error:
        fixture["combined"].capture_mcp_snapshot(candidate, fixture["selection"])
    assert error.value.code == code
    fixture["authority"].release_read_lease(lease)


@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy, pickle.dumps])
def test_mcp_lease_copy_and_pickle_operations_are_rejected(native_mcp_fixture, operation):
    fixture = native_mcp_fixture
    lease = _lease(fixture, "copy-boundary")
    with pytest.raises(Exception):
        operation(lease)
    fixture["authority"].release_read_lease(lease)


@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy, pickle.dumps])
def test_real_metadata_admission_copy_and_pickle_are_rejected(native_mcp_fixture, operation):
    fixture = native_mcp_fixture
    lease = _lease(fixture, "admission-copy-boundary")
    with _open_mcp_metadata_admission(fixture["authority"], lease) as (admission, metadata):
        with pytest.raises(TypeError):
            operation(admission)
        assert _require_mcp_metadata_admission(admission, metadata) is lease.binding
    fixture["authority"].release_read_lease(lease)


@pytest.mark.parametrize("state_field", ["_token_cache_generation", "_process_epoch", "allow_context_reads", "mcp_context_plugin_enabled"])
def test_persisted_auth_and_gate_drift_is_denied(native_mcp_fixture, state_field):
    fixture = native_mcp_fixture
    lease = _lease(fixture, "drift-" + state_field)
    state = fixture["mcp_state"]
    setattr(state, state_field, (5 if state_field == "_token_cache_generation" else "epoch_" + "d" * 32) if state_field in {"_token_cache_generation", "_process_epoch"} else False)
    with pytest.raises((ContextManifestProducerError, McpContextAuthorityError)):
        fixture["combined"].capture_mcp_snapshot(lease, fixture["selection"])


@pytest.mark.parametrize("column", ["token_hash", "scopes", "owner", "owner_subject_id", "is_active", "chat_owner"])
def test_persisted_token_row_drift_is_denied(native_mcp_fixture, column):
    fixture = native_mcp_fixture
    lease = _lease(fixture, "row-drift-" + column)
    with fixture["session_factory"]() as db:
        row = db.query(ApiToken).filter(ApiToken.id == "mcp-metadata-token").one()
        if column == "chat_owner":
            db.query(DbSession).filter(DbSession.id == "chat-owned").one().owner = "mallory"
            db.commit()
        else:
            setattr(row, column, {
            "token_hash": bcrypt.hashpw(b"ody_" + b"z" * 43, bcrypt.gensalt()).decode("ascii"),
            "scopes": "mcp:other:scope",
            "owner": "mallory",
            "owner_subject_id": "owner_foreign",
            "is_active": False,
            }[column])
            db.commit()
    with pytest.raises((ContextManifestProducerError, McpContextAuthorityError)):
        fixture["combined"].capture_mcp_snapshot(lease, fixture["selection"])


def test_foreign_authority_and_wrong_provider_identity_are_rejected(native_mcp_fixture, tmp_path):
    fixture = native_mcp_fixture
    auth = fixture["auth"]
    foreign = McpContextAuthority(auth, fixture["session_factory"], app_state=fixture["mcp_state"])
    swapped = CombinedContextManifestProducer(
        auth_manager=auth, forge=fixture["forge"], planning=fixture["planning_producer"],
        knowledge=fixture["knowledge_producer"], mcp_authority=foreign,
    )
    lease = _lease(fixture, "foreign-authority")
    with pytest.raises(McpContextAuthorityError) as error:
        swapped.capture_mcp_snapshot(lease, fixture["selection"])
    assert error.value.code == "unauthorized"
    foreign_auth = type(auth)(str(tmp_path / "foreign-auth.json"))
    with pytest.raises(ContextManifestProducerError) as error:
        CombinedContextManifestProducer(
            auth_manager=foreign_auth, forge=fixture["forge"], planning=fixture["planning_producer"],
            knowledge=fixture["knowledge_producer"], mcp_authority=fixture["authority"],
        )
    assert error.value.code == "invalid_provider"
    fixture["authority"].release_read_lease(lease)


@pytest.mark.parametrize("operation,code", [("cancel", "cancelled"), ("release", "unauthorized"), ("close", "unauthorized")])
def test_cancel_release_and_close_fence_native_capture(native_mcp_fixture, operation, code):
    fixture = native_mcp_fixture
    lease = _lease(fixture, "lifecycle-" + operation)
    if operation == "cancel":
        fixture["authority"].cancel_lease(lease)
    elif operation == "release":
        fixture["authority"].release_read_lease(lease)
    else:
        fixture["authority"].close()
    with pytest.raises(McpContextAuthorityError) as error:
        fixture["combined"].capture_mcp_snapshot(lease, fixture["selection"])
    assert error.value.code == code


def test_native_capture_rechecks_one_second_real_lease_expiry_between_captures(native_mcp_fixture, monkeypatch):
    fixture = native_mcp_fixture
    clock = [1000.0]
    authority = McpContextAuthority(
        fixture["auth"], fixture["session_factory"], app_state=fixture["mcp_state"],
        monotonic=lambda: clock[0], wall_time=lambda: clock[0],
    )
    owner_sid = fixture["auth"].subject_id_for_username("fuzzy")
    identity = McpAuthenticatedIdentity(
        "mcp-metadata-token", "fuzzy", owner_sid,
        fixture["mcp_state"]._token_cache_generation, fixture["auth"].security_action_revision(),
        "mcp:harbour:context:read",
    )
    binding = authority.issue_binding(identity, issue_mcp_bearer_proof(fixture["mcp_raw_tokens"][0]), "chat-owned")
    lease = authority.issue_read_lease(binding, "expiry-between-captures", ttl_seconds=1)
    combined = CombinedContextManifestProducer(
        auth_manager=fixture["auth"], forge=fixture["forge"], planning=fixture["planning_producer"],
        knowledge=fixture["knowledge_producer"], mcp_authority=authority,
    )
    producer_type = type(combined)
    real_capture = producer_type._capture_native_mcp
    calls = 0

    def capture_then_expire(self, admission, metadata, selection):
        nonlocal calls
        result = real_capture(self, admission, metadata, selection)
        if self is combined:
            calls += 1
            if calls == 1:
                clock[0] += 1.0
        return result

    monkeypatch.setattr(producer_type, "_capture_native_mcp", capture_then_expire)
    try:
        with pytest.raises(McpContextAuthorityError) as error:
            combined.capture_mcp_snapshot(lease, fixture["selection"])
        assert error.value.code == "timeout"
        assert calls == 1
    finally:
        authority.close()


def test_cookie_canonical_path_remains_distinct_and_payload_seams_reject_mcp(native_mcp_fixture):
    fixture = native_mcp_fixture
    lease = _lease(fixture, "boundary-check")
    with pytest.raises(ContextManifestProducerError):
        fixture["combined"].capture_metadata(lease, fixture["selection"])
    with CookieConfirmationAuthority(fixture["auth"]).hold(fixture["cookie"], fixture["chat_id"]) as binding:
        before = fixture["combined"].capture_snapshot(binding, fixture["selection"])
        before_manifest = ContextManifest(before.items)
        before_binding = json.dumps(binding._canonical(), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        before_manifest_bytes = object.__getattribute__(before_manifest, "_canonical_value")
        mcp = fixture["combined"].capture_mcp_snapshot(lease, fixture["selection"])
        after = fixture["combined"].capture_snapshot(binding, fixture["selection"])
        after_manifest = ContextManifest(after.items)
        after_binding = json.dumps(binding._canonical(), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        after_manifest_bytes = object.__getattribute__(after_manifest, "_canonical_value")
        assert after.items == before.items
        assert after.target_manifest_digests == before.target_manifest_digests
        assert after_manifest.digest == before_manifest.digest
        assert after_binding == before_binding
        assert after_manifest_bytes == before_manifest_bytes
        assert mcp.mcp_metadata_binding is not None
    fixture["authority"].release_read_lease(lease)


def test_real_legacy_cookie_ports_reject_all_mcp_objects_before_payload_access(native_mcp_fixture):
    from tests.test_context_pack_assembler import _assembler
    from src.context_pack_assembler import ContextPackAssemblerError
    from src.forge_context_page import ForgeContextPageRequest
    from src.planning_context_reader import PlanningContextPageRequest
    from src.harbour_knowledge_active_runtime import KnowledgeContextPageRequest

    fixture = native_mcp_fixture
    forge_item = fixture["forge_selection"]
    planning_items = tuple(item for item in fixture["selection"].items if item.source == "planning")
    knowledge_items = tuple(item for item in fixture["selection"].items if item.source == "knowledge")
    planning_selection = ContextSelection(planning_items)
    knowledge_selection = ContextSelection(knowledge_items)
    locator = forge_item.forge_locator
    assembler, confirmation = _assembler(fixture)
    challenge = confirmation.issue_for_selection(fixture["cookie"], fixture["chat_id"], fixture["selection"])
    verified = confirmation.consume_challenge(fixture["cookie"], fixture["chat_id"], challenge)
    with CookieConfirmationAuthority(fixture["auth"]).hold(fixture["cookie"], fixture["chat_id"]) as binding:
        forge_manifest = fixture["forge"].capture_version_metadata(
            binding, locator.repo_id, locator.version_id, (locator.path,))
        planning_manifest = fixture["planning_producer"].capture(binding, planning_selection)
        knowledge_capture = fixture["knowledge_composition"]._capture_context_material(binding, knowledge_selection)
    requests = (
        (assembler._forge_pages, ForgeContextPageRequest(
            fixture["selection"], forge_item, fixture["chat_id"], forge_manifest.manifest_digest,
            0, 8, verified.disclosure_message_id), ConfirmationError, "invalid_binding"),
        (assembler._planning_pages, PlanningContextPageRequest(
            fixture["selection"], planning_items[0], fixture["chat_id"], planning_manifest.digest,
            0, 8, verified.disclosure_message_id), PlanningContextReaderError, "disclosure_unverified"),
        (assembler._knowledge_pages, KnowledgeContextPageRequest(
            fixture["selection"], knowledge_items[0], fixture["chat_id"], knowledge_capture.manifest.digest,
            0, 8, verified.disclosure_message_id), ConfirmationError, "invalid_binding"),
    )
    lease = _lease(fixture, "legacy-all-mcp-objects")
    try:
        with _open_mcp_metadata_admission(fixture["authority"], lease) as (admission, metadata):
            for candidate in (lease, metadata, admission):
                calls = (
                    (lambda: fixture["combined"].capture_metadata(candidate, fixture["selection"]), ContextManifestProducerError),
                    (lambda: fixture["combined"].capture(candidate, fixture["selection"]), ContextManifestProducerError),
                    (lambda: fixture["combined"].capture_snapshot(candidate, fixture["selection"]), ContextManifestProducerError),
                    (lambda: fixture["forge"].capture_version_metadata(candidate, locator.repo_id, locator.version_id, (locator.path,)), ForgeContextMetadataError),
                    (lambda: fixture["forge"].resolve_selection(candidate, forge_manifest.descriptor), ForgeContextMetadataError),
                    (lambda: fixture["forge"].revalidate_metadata(candidate, forge_manifest), ForgeContextMetadataError),
                    (lambda: fixture["planning_producer"].capture(candidate, planning_selection), PlanningContextReaderError),
                    (lambda: fixture["planning_producer"].capture_metadata(candidate, planning_selection), PlanningContextReaderError),
                    (lambda: fixture["planning_producer"].read_document(candidate, planning_items[0]), PlanningContextReaderError),
                    (lambda: fixture["durable_source"].capture(candidate), DurablePlanningContextError),
                    (lambda: fixture["durable_source"].discover(candidate), DurablePlanningContextError),
                    (lambda: fixture["durable_source"].resolve(candidate, planning_selection), DurablePlanningContextError),
                    (lambda: fixture["durable_source"].read_document(candidate, planning_items[0]), DurablePlanningContextError),
                )
                for call, error_type in calls:
                    with pytest.raises(error_type):
                        call()
                with pytest.raises(KnowledgeContextError) as error:
                    fixture["knowledge_producer"].capture_metadata(candidate, knowledge_selection)
                assert error.value.code == "capture_unavailable"
                for call in (
                    lambda: fixture["knowledge_producer"].capture(candidate, knowledge_selection),
                    lambda: fixture["knowledge_producer"].capture_private_subset(candidate, knowledge_selection),
                    lambda: fixture["knowledge_composition"]._read_context_payload(candidate, knowledge_capture.entries[0]),
                ):
                    with pytest.raises(ValueError, match="^knowledge context binding unavailable$"):
                        call()
                for consumer, request, error_type, code in requests:
                    with pytest.raises(error_type) as error:
                        consumer.read_page(candidate, request)
                    assert error.value.code == code
                with pytest.raises(ContextPackAssemblerError) as error:
                    assembler.page(candidate, fixture["chat_id"], fixture["selection"], 8,
                                   disclosure_message_id=verified.disclosure_message_id)
                assert error.value.code == "invalid_request"
    finally:
        fixture["authority"].release_read_lease(lease)


@pytest.mark.parametrize("domain", [
    "odysseus.context.mcp_metadata.forge.boundary.v1",
    "odysseus.context.mcp_metadata.forge.record.v1",
    "odysseus.context.mcp_metadata.forge.target.v1",
    "odysseus.context.mcp_metadata.planning.principal.v1",
    "odysseus.context.mcp_metadata.planning.boundary.v1",
    "odysseus.context.mcp_metadata.planning.record.v1",
    "odysseus.context.mcp_metadata.planning.target.v1",
    "odysseus.context.mcp_metadata.knowledge.principal.v1",
    "odysseus.context.mcp_metadata.knowledge.boundary.v1",
    "odysseus.context.mcp_metadata.knowledge.record.v1",
    "odysseus.context.mcp_metadata.knowledge.target.v1",
])
def test_recipient_digest_accepts_only_known_framed_domains(native_mcp_fixture, domain):
    lease = _lease(native_mcp_fixture, "digest-domain-" + domain.rsplit(".", 2)[-2])
    with _open_mcp_metadata_admission(native_mcp_fixture["authority"], lease) as (_admission, metadata):
        digest = _mcp_recipient_digest(domain, metadata, {"kind": "native-metadata-test"})
        assert type(digest) is str and len(digest) == 64
        with pytest.raises(ValueError):
            _mcp_recipient_digest(domain + ".unknown", metadata, {})
    native_mcp_fixture["authority"].release_read_lease(lease)


def test_native_capture_rechecks_live_admission_between_real_captures(native_mcp_fixture, monkeypatch):
    fixture = native_mcp_fixture
    lease = _lease(fixture, "between-capture-cancel")
    combined = fixture["combined"]
    producer_type = type(combined)
    real_capture = producer_type._capture_native_mcp
    calls = 0

    def cancel_between_captures(self, admission, metadata, selection):
        nonlocal calls
        result = real_capture(self, admission, metadata, selection)
        if self is combined:
            calls += 1
        if self is combined and calls == 1:
            fixture["authority"].cancel_lease(lease)
        return result

    monkeypatch.setattr(producer_type, "_capture_native_mcp", cancel_between_captures)
    with pytest.raises(McpContextAuthorityError) as error:
        combined.capture_mcp_snapshot(lease, fixture["selection"])
    assert error.value.code == "cancelled"


def test_native_forge_registry_privacy_drift_between_real_captures(native_mcp_fixture, monkeypatch):
    fixture = native_mcp_fixture
    forge = fixture["forge"]
    lease = _lease(fixture, "forge-registry-drift")
    real_capture = type(forge).capture_version_metadata_mcp
    calls = 0

    def capture_then_mutate_registry(self, *args):
        nonlocal calls
        result = real_capture(self, *args)
        if self is forge:
            calls += 1
            if calls == 1:
                registry = RepoRegistry.load_or_empty(forge._registry_path)
                record = registry.get("demo")
                registry.put(replace(record, privacy_class="private"))
                registry.save_json(forge._registry_path)
        return result

    monkeypatch.setattr(type(forge), "capture_version_metadata_mcp", capture_then_mutate_registry)
    with pytest.raises(ContextManifestProducerError) as error:
        fixture["combined"].capture_mcp_snapshot(lease, fixture["selection"])
    assert error.value.code in {"context_stale", "capture_unavailable"}
    fixture["authority"].release_read_lease(lease)


@pytest.mark.parametrize("replacement", ["durable_store", "issuer"])
def test_native_planning_runtime_provider_replacement_is_pinned(native_mcp_fixture, monkeypatch, replacement):
    fixture = native_mcp_fixture
    runtime = fixture["planning_producer"]._runtime
    lease = _lease(fixture, "planning-provider-" + replacement)
    monkeypatch.setattr(runtime, replacement, object())
    with pytest.raises(ContextManifestProducerError) as error:
        fixture["combined"].capture_mcp_snapshot(lease, fixture["selection"])
    assert error.value.code == "invalid_provider"
    fixture["authority"].release_read_lease(lease)


@pytest.mark.parametrize("approve", [False, True])
def test_native_planning_old_selection_rejects_real_unapproved_or_approved_head(native_mcp_fixture, approve):
    fixture = native_mcp_fixture
    runtime = fixture["planning_producer"]._runtime
    target = next(item for item in fixture["selection"].items if item.source == "planning")
    with CookieConfirmationAuthority(fixture["auth"]).hold(fixture["cookie"], fixture["chat_id"]) as binding:
        setup = {
            "source": fixture["durable_source"], "binding": binding,
            "store": runtime.durable_store, "principal": fixture["principal"],
        }
        receipt, roadmap_id = _append_revision(
            setup, target, title="Native MCP temporal drift", key="mcp-native-head-" + str(approve),
        )
    if approve:
        runtime.durable_store.approve_revision(
            fixture["principal"], owner_id="fuzzy", project_id="harbour-one", roadmap_id=roadmap_id,
            revision_id=receipt.new_revision_id, expected_head=receipt.after_head,
            idempotency_key="mcp-native-head-approve",
        )
    lease = _lease(fixture, "planning-head-" + str(approve))
    with pytest.raises(ContextManifestProducerError) as error:
        fixture["combined"].capture_mcp_snapshot(lease, fixture["selection"])
    assert error.value.code in {"context_stale", "capture_unavailable"}
    fixture["authority"].release_read_lease(lease)


@pytest.mark.parametrize("field,value", [
    ("allowed_owners", ("owner_other",)), ("allowed_sources", ("forge",)), ("allowed_domains", ("planning",)),
])
def test_native_knowledge_config_fences_are_rechecked(native_mcp_fixture, field, value):
    fixture = native_mcp_fixture
    composition = fixture["knowledge_composition"]
    lease = _lease(fixture, "knowledge-config-" + field)
    composition._config = replace(composition._config, **{field: value})
    with pytest.raises(ContextManifestProducerError) as error:
        fixture["combined"].capture_mcp_snapshot(lease, fixture["selection"])
    assert error.value.code in {"context_stale", "capture_unavailable"}
    fixture["authority"].release_read_lease(lease)


def test_native_knowledge_tombstone_and_close_fence_old_selection(native_mcp_fixture):
    fixture = native_mcp_fixture
    composition = fixture["knowledge_composition"]
    with CookieConfirmationAuthority(fixture["auth"]).hold(fixture["cookie"], fixture["chat_id"]) as binding:
        owner_id, store, principal, *_ = composition._context_runtime(binding)
        selected_store = composition._staging.selected_runtime(owner_id)[4]
        record = selected_store.capture_index_snapshot(principal, owner_id=owner_id).records[0]
        head = store.get_head(principal, owner_id=owner_id, knowledge_id=record.knowledge_id)
        store.tombstone(
            principal, owner_id=owner_id, knowledge_id=record.knowledge_id, expected_head=head,
            idempotency_key="mcp-native-tombstone",
        )
    lease = _lease(fixture, "knowledge-tombstone")
    with pytest.raises(ContextManifestProducerError) as error:
        fixture["combined"].capture_mcp_snapshot(lease, fixture["selection"])
    assert error.value.code in {"context_stale", "capture_unavailable"}
    fixture["authority"].release_read_lease(lease)

    live = _lease(fixture, "knowledge-close")
    composition.close()
    with pytest.raises(ContextManifestProducerError) as close_error:
        fixture["combined"].capture_mcp_snapshot(live, fixture["selection"])
    assert close_error.value.code in {"context_stale", "capture_unavailable"}


@pytest.mark.parametrize("domain", ["planning", "knowledge"])
def test_native_store_mutation_after_first_complete_capture_is_denied(native_mcp_fixture, monkeypatch, domain):
    fixture = native_mcp_fixture
    with CookieConfirmationAuthority(fixture["auth"]).hold(fixture["cookie"], fixture["chat_id"]) as binding:
        if domain == "planning":
            from tests.test_planning_durable_context_source import _canonical, compute_roadmap_content_hash
            target = next(item for item in fixture["selection"].items if item.source == "planning")
            document = json.loads(fixture["durable_source"].read_document(binding, target).decode("utf-8"))
            roadmap = document["roadmaps"][0]
            roadmap["revision"] += 1
            roadmap["title"] = "Actual between-capture revision"
            roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
            document["project"]["latest_approved_revision"] = {
                roadmap["roadmap_id"]: {"revision": roadmap["revision"], "content_hash": roadmap["content_hash"]},
            }
            document["project"]["draft_refs"] = []
            store = fixture["planning_producer"]._runtime.durable_store
            principal = fixture["principal"]
            roadmap_id = roadmap["roadmap_id"]
            head = store.get_head(principal, owner_id="fuzzy", project_id="harbour-one", roadmap_id=roadmap_id)
            payload = _canonical(document)

            def mutate():
                receipt = store.append_revision(principal, owner_id="fuzzy", project_id="harbour-one",
                    roadmap_id=roadmap_id, payload_bytes=payload, expected_head=head,
                    idempotency_key="mcp-between-append")
                store.approve_revision(principal, owner_id="fuzzy", project_id="harbour-one",
                    roadmap_id=roadmap_id, revision_id=receipt.new_revision_id, expected_head=receipt.after_head,
                    idempotency_key="mcp-between-approve")
        else:
            composition = fixture["knowledge_composition"]
            owner, store, principal, *_ = composition._context_runtime(binding)
            assert composition._staging.selected_runtime(owner)[4] is store
            record = store.capture_index_snapshot(principal, owner_id=owner).records[0]
            head = store.get_head(principal, owner_id=owner, knowledge_id=record.knowledge_id)

            def mutate():
                store.tombstone(principal, owner_id=owner, knowledge_id=record.knowledge_id,
                    expected_head=head, idempotency_key="mcp-between-tombstone")
    combined = fixture["combined"]
    real_capture = type(combined)._capture_native_mcp
    calls = 0

    def capture_then_mutate(self, admission, metadata, selection):
        nonlocal calls
        result = real_capture(self, admission, metadata, selection)
        if self is combined:
            calls += 1
            if calls == 1:
                mutate()
        return result

    monkeypatch.setattr(type(combined), "_capture_native_mcp", capture_then_mutate)
    lease = _lease(fixture, "between-native-" + domain)
    try:
        with pytest.raises(ContextManifestProducerError) as error:
            combined.capture_mcp_snapshot(lease, fixture["selection"])
        assert error.value.code == "context_stale"
        assert calls == 1  # second native capture rejects the changed selected object
    finally:
        fixture["authority"].release_read_lease(lease)


@pytest.mark.parametrize("family", ["selected_runtime", "verify_completion"])
def test_public_knowledge_final_runtime_and_completion_fields_are_rechecked(public_context_fixture, monkeypatch, family):
    from tests.test_harbour_knowledge_context_reader import _public_selection_and_digest
    from src.harbour_knowledge_active_runtime import KnowledgeContextManifestProducer

    auth, subject, composition, session_factory = public_context_fixture
    _cookie, selection = _public_selection_and_digest(composition, auth, "public-knowledge-chat")
    raw = "ody_" + "p" * 43
    with session_factory() as db:
        db.add(ApiToken(id="public-metadata-token", owner="alice", name="public metadata test",
            token_hash=bcrypt.hashpw(raw.encode("ascii"), bcrypt.gensalt()).decode("ascii"),
            token_prefix=raw[:8], scopes="mcp:harbour:context:read", owner_subject_id=subject, is_active=True))
        db.commit()
    state = SimpleNamespace(_token_cache_generation=4, _token_cache_dirty=False,
        mcp_context_plugin_enabled=True, allow_context_reads=True, _process_epoch="epoch_" + "e" * 32)
    authority = McpContextAuthority(auth, session_factory, app_state=state)
    identity = McpAuthenticatedIdentity("public-metadata-token", "alice", subject, 4,
        auth.security_action_revision(), "mcp:harbour:context:read")
    producer = KnowledgeContextManifestProducer(composition)
    lease = authority.issue_read_lease(authority.issue_binding(identity, issue_mcp_bearer_proof(raw),
        "public-knowledge-chat"), "public-final-" + family)
    staging = composition._staging
    staging_type = type(staging)
    real_selected = staging_type.selected_runtime
    real_completion = staging_type.verify_completion
    armed = False
    calls = 0

    def selected_with_final_drift(self, *args, **kwargs):
        nonlocal calls
        result = real_selected(self, *args, **kwargs)
        if self is staging and armed and family == "selected_runtime":
            calls += 1
            if calls == 2:
                reference, owners, generation, digest, store = result
                changed = "sha256:" + ("1" * 64 if digest != "sha256:" + "1" * 64 else "2" * 64)
                return reference, owners, generation, changed, store
        return result

    def completion_with_final_drift(self, *args, **kwargs):
        nonlocal calls
        result = real_completion(self, *args, **kwargs)
        if self is staging and armed and family == "verify_completion":
            calls += 1
            if calls == 4:
                changed = "sha256:" + ("1" * 64 if result.head_manifest_digest != "sha256:" + "1" * 64 else "2" * 64)
                return replace(result, head_manifest_digest=changed)
        return result

    monkeypatch.setattr(staging_type, "selected_runtime", selected_with_final_drift)
    monkeypatch.setattr(staging_type, "verify_completion", completion_with_final_drift)
    try:
        with _open_mcp_metadata_admission(authority, lease) as (admission, metadata):
            manifest, target = producer.capture_metadata_mcp(admission, metadata, selection)
            assert manifest.items and all(item.classification == "public" for item in manifest.items)
            assert len(target) == 64
            armed = True
            with pytest.raises(ValueError, match="^knowledge context selection changed$"):
                producer.capture_metadata_mcp(admission, metadata, selection)
            assert calls >= (2 if family == "selected_runtime" else 4)
    finally:
        authority.release_read_lease(lease)
        authority.close()
