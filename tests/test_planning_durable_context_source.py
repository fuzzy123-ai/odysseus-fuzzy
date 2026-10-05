from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import src.planning_revision_store as planning_store_module

import pytest

from core.auth import AuthManager
from src.harbour_authority import AuthenticatedPrincipalIssuer
from src.mcp_authenticated_dispatch import AppOwnedPlanningRuntime
from src.planning_definition_contract import compute_roadmap_content_hash
from src.planning_durable_context_source import (
    DEFAULT_DURABLE_PLANNING_CONTEXT_POLICY,
    DurablePlanningContextError,
    DurablePlanningContextSource,
)
from src.planning_mcp_service import _contains_sensitive_value
from src.planning_revision_store import DurablePlanningRevisionStore, PlanningRevisionStore
from src.planning_revision_store import PlanningAcceptedHeadBinding
from src.planning_roadmap_migration import build_h1r03_migration_manifest
from src.private_context_confirmation_app import (
    ContextSelection,
    CookieConfirmationAuthority,
    SourceSelection,
)
from src.unified_source_index_contract import (
    Classification,
    ContentPolicy,
    SourceKind,
    SourceRecord,
    SourceVersionRecord,
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _safe_sanitized_text(prefix: str, path: tuple[object, ...]) -> str:
    suffix = "-".join(str(part) for part in path)
    return f"{prefix}-{suffix}" if suffix else prefix


def _clean_descendant(value: object, path: tuple[object, ...] = ()) -> object:
    if type(value) is str:
        if "\\" in value or re.search(r"(?i)(?:[A-Z]:[\\/]|\\\\|/(?:home|Users|private|var|tmp)/)", value):
            return _safe_sanitized_text("repository-relative-reference", path)
        if re.search(r"(?i)(?:password|secret|token|api[_-]?key|authorization|cookie|chat[_-]?id|bearer|sk-[A-Za-z0-9_-]{12,})", value):
            return _safe_sanitized_text("policy-safe-reference", path)
        return value
    if type(value) is list:
        return [_clean_descendant(item, path + (index,)) for index, item in enumerate(value)]
    if type(value) is dict:
        return {key: _clean_descendant(item, path + (key,)) for key, item in value.items()}
    return value


def _append_clean_descendants(fixture, binding) -> None:
    store = fixture["store"]
    principal = fixture["principal"]
    owner, _batch, records, heads = store.read_subject_bound_accepted_migration_records(
        principal, project_id="harbour-one",
    )
    assert owner == "fuzzy"
    for head in heads:
        payload = store.read_payload(
            principal, owner_id=owner, revision_id=head["current_revision_id"],
        )
        document = _clean_descendant(json.loads(payload.decode("utf-8")), ("document",))
        roadmap = document["roadmaps"][0]
        roadmap["revision"] = head["current_revision"] + 1
        roadmap["title"] = f"Übersicht Prüfung — {roadmap['roadmap_id']}"
        roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
        document["project"]["latest_approved_revision"] = {
            roadmap["roadmap_id"]: {
                "revision": roadmap["revision"],
                "content_hash": roadmap["content_hash"],
            },
        }
        document["project"]["draft_refs"] = []
        assert not _contains_sensitive_value(_canonical(document).decode("utf-8"))
        current = store.get_head(
            principal, owner_id=owner, project_id="harbour-one",
            roadmap_id=roadmap["roadmap_id"],
        )
        appended = store.append_revision(
            principal, owner_id=owner, project_id="harbour-one",
            roadmap_id=roadmap["roadmap_id"], payload_bytes=_canonical(document),
            expected_head=current,
            idempotency_key=f"native-clean-descendant-{roadmap['roadmap_id']}",
        )
        store.approve_revision(
            principal, owner_id=owner, project_id="harbour-one",
            roadmap_id=roadmap["roadmap_id"], revision_id=appended.new_revision_id,
            expected_head=appended.after_head,
            idempotency_key=f"native-clean-approval-{roadmap['roadmap_id']}",
        )
    _owner, _batch, retained_records, retained_heads = store.read_subject_bound_accepted_migration_records(
        principal, project_id="harbour-one",
    )
    assert len(heads) == len(retained_heads) == 6
    for head in retained_heads:
        chain = [
            document for _record_owner, document, _revision_ref in retained_records
            if document["roadmaps"][0]["roadmap_id"] == head["roadmap_id"]
        ]
        assert len(chain) >= 2
        assert head["current_revision"] == head["approved_revision"]


@pytest.fixture
def durable_planning_source(tmp_path: Path):
    auth = AuthManager(str(tmp_path / "auth.json"))
    assert auth.create_user("fuzzy", "Password-123", is_admin=True)
    cookie = auth.create_session("fuzzy", "Password-123")
    assert cookie
    owner_sid = auth.subject_id_for_username("fuzzy")
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="odysseus-auth",
        key_id="planning-key-1",
        secret=b"native-durable-planning-source-secret-0001",
        clock=lambda: datetime.now(timezone.utc),
    )
    store = DurablePlanningRevisionStore(
        tmp_path / "durable-planning", trusted_issuers=(issuer,)
    )
    principal = issuer.issue(
        subject_id=owner_sid,
        owner_id="fuzzy",
        auth_context_digest="sha256:" + "4" * 64,
        ttl_seconds=3600,
        nonce="native-source-fixture-0001",
    )
    manifest = build_h1r03_migration_manifest(Path(__file__).resolve().parents[1])
    store.prepare_migration(principal, manifest=manifest)
    store.accept_migration(
        principal,
        batch_id=manifest["batch_id"],
        current_manifest=lambda: deepcopy(manifest),
    )
    runtime = AppOwnedPlanningRuntime(
        repo_root=str(Path(__file__).resolve().parents[1]),
        legacy_store=PlanningRevisionStore(),
        durable_store=store,
        issuer=issuer,
    )
    source = DurablePlanningContextSource(
        runtime=runtime,
        auth_manager=auth,
        project_id="harbour-one",
        policy=DEFAULT_DURABLE_PLANNING_CONTEXT_POLICY,
    )
    authority = CookieConfirmationAuthority(auth)
    with authority.hold(cookie, "planning-native-chat") as binding:
        with pytest.raises(DurablePlanningContextError) as initial_error:
            source.capture(binding)
        assert initial_error.value.code == "policy_denied"

    with authority.hold(cookie, "planning-native-chat") as binding:
        _append_clean_descendants(
            {"store": store, "principal": principal}, binding,
        )

    with authority.hold(cookie, "planning-native-chat") as binding:
        yield {
            "auth": auth,
            "cookie": cookie,
            "authority": authority,
            "binding": binding,
            "issuer": issuer,
            "principal": principal,
            "store": store,
            "source": source,
        }


def _selection(source: DurablePlanningContextSource, binding) -> ContextSelection:
    items = source.discover(binding)
    return ContextSelection(tuple(sorted(items, key=lambda item: (
        item.source, item.selection_ref, item.revision_ref,
    ))))


def _append_revision(fixture, target: SourceSelection, *, title: str, key: str):
    source = fixture["source"]
    payload = source.read_document(fixture["binding"], target)
    document = json.loads(payload.decode("utf-8"))
    roadmap = document["roadmaps"][0]
    roadmap_id = roadmap["roadmap_id"]
    roadmap["revision"] += 1
    roadmap["title"] = title
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    document["project"]["latest_approved_revision"] = {
        roadmap["roadmap_id"]: {
            "revision": roadmap["revision"],
            "content_hash": roadmap["content_hash"],
        },
    }
    document["project"]["draft_refs"] = []
    head = fixture["store"].get_head(
        fixture["principal"], owner_id="fuzzy", project_id="harbour-one",
        roadmap_id=roadmap["roadmap_id"],
    )
    receipt = fixture["store"].append_revision(
        fixture["principal"], owner_id="fuzzy", project_id="harbour-one",
        roadmap_id=roadmap_id, payload_bytes=_canonical(document),
        expected_head=head, idempotency_key=key,
    )
    return receipt, roadmap_id


def test_native_source_discovers_resolves_and_reads_canonical_bytes(durable_planning_source):
    fixture = durable_planning_source
    source = fixture["source"]
    binding = fixture["binding"]
    snapshot = source.capture(binding)
    selections = source.discover(binding)
    resolved = source.resolve(binding, _selection(source, binding))

    assert snapshot.policy is DEFAULT_DURABLE_PLANNING_CONTEXT_POLICY
    assert len(snapshot.descriptors) == len(selections) == len(resolved) == 6
    assert all(item.source == "planning" for item in selections)
    target = selections[0]
    payload = source.read_document(binding, target)
    assert payload == _canonical(json.loads(payload.decode("utf-8")))
    assert b"password" not in payload.lower()
    descriptor = next(item for item in resolved if item.selection() == target)
    assert "sha256:" + hashlib.sha256(payload).hexdigest() == descriptor.durable_payload_sha256
    assert type(descriptor.source) is SourceRecord
    assert type(descriptor.source_version) is SourceVersionRecord
    assert descriptor.source.source_kind is SourceKind.PLANNING
    assert descriptor.source.classification is Classification.PRIVATE
    assert descriptor.source.content_policy is ContentPolicy.INLINE_LOCAL
    assert descriptor.source_version.policy_evidence.parent_id == descriptor.source.source_id
    assert descriptor.source_version.policy_evidence.owner_scope == descriptor.source.owner_scope
    assert descriptor.source_version.version_observed_at
    assert descriptor.source_version.content_hash == descriptor.durable_payload_sha256
    assert descriptor.source_version.classification is Classification.PRIVATE
    assert descriptor.source_version.content_policy is ContentPolicy.INLINE_LOCAL
    assert descriptor.source_version.source_id == descriptor.source.source_id
    assert "Übersicht Prüfung" in payload.decode("utf-8")


def test_native_source_rejects_forged_selection_and_wrong_sid(durable_planning_source):
    fixture = durable_planning_source
    source = fixture["source"]
    binding = fixture["binding"]
    target = source.discover(binding)[0]
    forged = SourceSelection("planning", target.selection_ref, "forged-revision-ref")
    with pytest.raises(DurablePlanningContextError) as error:
        source.resolve(binding, ContextSelection((forged,)))
    assert error.value.code == "source_not_found"

    wrong = type(binding)(
        owner_sid="owner_" + "0" * 32,
        chat_session_id=binding.chat_session_id,
        auth_session_binding=binding.auth_session_binding,
        security_revision=binding.security_revision,
    )
    with pytest.raises(DurablePlanningContextError) as error:
        source.capture(wrong)
    assert error.value.code == "invalid_binding"


class _StringSubclass(str):
    pass


class _TupleSubclass(tuple):
    pass


class _IntSubclass(int):
    pass


class _NativePrincipalCallbackManager:
    def __init__(self, binding, identity, revision):
        self.identity = identity
        self.revision = revision
        self.binding = binding

    @contextmanager
    def guard_current_admin_subject(self, _owner_sid):
        yield "fuzzy"

    def active_admin_subject_identity(self, *, subject_id):
        return self.identity

    def security_action_revision(self):
        return self.revision


@pytest.mark.parametrize(
    "kind",
    ("tuple-subclass", "list", "sid-subclass", "alias-subclass", "bool-revision", "int-subclass-revision"),
)
def test_native_principal_rejects_hostile_initial_identity_and_revision_types(durable_planning_source, kind):
    fixture = durable_planning_source
    source = fixture["source"]
    binding = fixture["binding"]
    identity = ("fuzzy", binding.owner_sid)
    revision = binding.security_revision
    if kind == "tuple-subclass":
        identity = _TupleSubclass(identity)
    elif kind == "list":
        identity = list(identity)
    elif kind == "sid-subclass":
        identity = ("fuzzy", _StringSubclass(binding.owner_sid))
    elif kind == "alias-subclass":
        identity = (_StringSubclass("fuzzy"), binding.owner_sid)
    elif kind == "bool-revision":
        revision = True
    else:
        revision = _IntSubclass(binding.security_revision)
    manager = _NativePrincipalCallbackManager(binding, identity, revision)
    original_manager = source._auth_manager
    object.__setattr__(source, "_auth_manager", manager)
    try:
        with pytest.raises(DurablePlanningContextError, match="invalid_binding"):
            with source._principal(binding):
                pytest.fail("hostile initial binding escaped the principal fence")
    finally:
        object.__setattr__(source, "_auth_manager", original_manager)


@pytest.mark.parametrize(
    "kind",
    ("tuple-subclass", "list", "sid-subclass", "alias-subclass", "bool-revision", "int-subclass-revision"),
)
def test_native_principal_rejects_hostile_final_identity_and_revision_types(durable_planning_source, kind):
    fixture = durable_planning_source
    source = fixture["source"]
    binding = fixture["binding"]
    manager = _NativePrincipalCallbackManager(
        binding, ("fuzzy", binding.owner_sid), binding.security_revision,
    )
    original_manager = source._auth_manager
    object.__setattr__(source, "_auth_manager", manager)
    try:
        with pytest.raises(DurablePlanningContextError, match="invalid_binding"):
            with source._principal(binding):
                if kind == "tuple-subclass":
                    manager.identity = _TupleSubclass(("fuzzy", binding.owner_sid))
                elif kind == "list":
                    manager.identity = ["fuzzy", binding.owner_sid]
                elif kind == "sid-subclass":
                    manager.identity = ("fuzzy", _StringSubclass(binding.owner_sid))
                elif kind == "alias-subclass":
                    manager.identity = (_StringSubclass("fuzzy"), binding.owner_sid)
                elif kind == "bool-revision":
                    manager.revision = True
                else:
                    manager.revision = _IntSubclass(binding.security_revision)
    finally:
        object.__setattr__(source, "_auth_manager", original_manager)


def test_native_principal_accepts_unchanged_exact_identity_and_revision(durable_planning_source):
    fixture = durable_planning_source
    source = fixture["source"]
    binding = fixture["binding"]
    manager = _NativePrincipalCallbackManager(
        binding, ("fuzzy", binding.owner_sid), binding.security_revision,
    )
    original_manager = source._auth_manager
    object.__setattr__(source, "_auth_manager", manager)
    try:
        with source._principal(binding) as (principal, alias):
            assert principal.owner_id == "fuzzy"
            assert alias == "fuzzy"
    finally:
        object.__setattr__(source, "_auth_manager", original_manager)


def test_native_source_rejects_head_tuple_mismatched_with_retained_record(durable_planning_source):
    fixture = durable_planning_source
    source = fixture["source"]
    store = fixture["store"]
    bundle = store.read_subject_bound_accepted_migration_bundle(
        fixture["principal"], project_id="harbour-one",
    )
    head = bundle.heads[0]
    forged_hash = "sha256:" + "0" * 64
    forged_head = object.__new__(PlanningAcceptedHeadBinding)
    for name, value in (
        ("owner_id", head.owner_id), ("project_id", head.project_id),
        ("roadmap_id", head.roadmap_id),
        ("accepted_revision_id", head.accepted_revision_id),
        ("accepted_revision", head.accepted_revision),
        ("accepted_content_sha256", head.accepted_content_sha256),
        ("current_revision_id", head.current_revision_id),
        ("current_revision", head.current_revision),
        ("current_content_sha256", forged_hash),
        ("approved_revision_id", head.approved_revision_id),
        ("approved_revision", head.approved_revision),
        ("approved_content_sha256", forged_hash),
        ("generation", head.generation),
    ):
        object.__setattr__(forged_head, name, value)
    heads = source._heads("fuzzy", (forged_head, *bundle.heads[1:]))
    with pytest.raises(DurablePlanningContextError) as error:
        source._records("fuzzy", bundle.revisions, heads)
    assert error.value.code == "planning_stale"


def test_unapproved_append_stales_old_selection_then_clean_approval_replaces_it(durable_planning_source):
    fixture = durable_planning_source
    source = fixture["source"]
    target = source.discover(fixture["binding"])[0]
    appended, roadmap_id = _append_revision(
        fixture, target, title="Clean approved descendant", key="native-clean-append-0001",
    )
    with pytest.raises(DurablePlanningContextError) as error:
        source.capture(fixture["binding"])
    assert error.value.code == "planning_stale"
    fixture["store"].approve_revision(
        fixture["principal"], owner_id="fuzzy", project_id="harbour-one",
        roadmap_id=roadmap_id,
        revision_id=appended.new_revision_id,
        expected_head=appended.after_head,
        idempotency_key="native-clean-approve-0001",
    )
    refreshed = source.discover(fixture["binding"])
    assert target not in refreshed
    assert len(refreshed) == len(source.capture(fixture["binding"]).descriptors)


def test_native_source_denies_sensitive_current_approved_document(durable_planning_source):
    fixture = durable_planning_source
    source = fixture["source"]
    target = source.discover(fixture["binding"])[0]
    appended, roadmap_id = _append_revision(
        fixture, target, title="C:\\Users\\fuzzy\\.ssh\\id_ed25519",
        key="native-sensitive-append-0001",
    )
    fixture["store"].approve_revision(
        fixture["principal"], owner_id="fuzzy", project_id="harbour-one",
        roadmap_id=roadmap_id,
        revision_id=appended.new_revision_id,
        expected_head=appended.after_head,
        idempotency_key="native-sensitive-approve-0001",
    )
    with pytest.raises(DurablePlanningContextError) as error:
        source.capture(fixture["binding"])
    assert error.value.code == "policy_denied"


def test_native_source_rejects_runtime_store_and_issuer_replacements(durable_planning_source):
    fixture = durable_planning_source
    source = fixture["source"]
    runtime = source._runtime
    replacement_store = DurablePlanningRevisionStore(
        fixture["store"].root,
        trusted_issuers=(fixture["issuer"],),
    )
    runtime.durable_store = replacement_store
    with pytest.raises(DurablePlanningContextError) as store_error:
        source.capture(fixture["binding"])
    assert store_error.value.code == "invalid_context"
    runtime.durable_store = fixture["store"]

    replacement_issuer = AuthenticatedPrincipalIssuer(
        issuer_id="replacement-auth",
        key_id="replacement-key",
        secret=b"replacement-native-planning-source-secret-0001",
    )
    runtime.issuer = replacement_issuer
    with pytest.raises(DurablePlanningContextError) as issuer_error:
        source.capture(fixture["binding"])
    assert issuer_error.value.code == "invalid_context"


def test_native_bundle_capture_and_bytes_use_two_fresh_full_verifications(durable_planning_source, monkeypatch):
    fixture = durable_planning_source
    source, binding, store = fixture["source"], fixture["binding"], fixture["store"]
    target = source.discover(binding)[0]
    expected_bytes = source.read_document(binding, target)
    original_verify = planning_store_module._verify_harbour_state
    calls = []

    def counted_verify(state):
        calls.append(1)
        return original_verify(state)

    def forbidden_read(*args, **kwargs):
        pytest.fail("native bundle escaped to per-record public read")

    monkeypatch.setattr(planning_store_module, "_verify_harbour_state", counted_verify)
    for name in ("get_head", "read_revision", "read_payload", "read_subject_bound_accepted_migration_records"):
        monkeypatch.setattr(store, name, forbidden_read)
    snapshot = source.capture(binding)
    assert len(snapshot.descriptors) == 6
    assert len(calls) == 2
    calls.clear()
    assert len(source.resolve(binding, ContextSelection((target,)))) == 1
    assert len(calls) == 2
    calls.clear()
    assert source.read_document(binding, target) == expected_bytes
    assert len(calls) == 2
