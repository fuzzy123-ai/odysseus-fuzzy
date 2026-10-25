from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace
import src.planning_durable_context_source as source_module
import src.planning_revision_store as planning_store_module

import pytest

from core.auth import AuthManager
from src.harbour_authority import AuthenticatedPrincipalIssuer, PrincipalRef
from src.mcp_authenticated_dispatch import AppOwnedPlanningRuntime
from src.planning_definition_contract import compute_roadmap_content_hash
from src.planning_durable_context_source import (
    DEFAULT_DURABLE_PLANNING_CONTEXT_POLICY,
    DurablePlanningContextError,
    DurablePlanningContextSource,
)
from src.planning_mcp_service import _contains_sensitive_value
from src.planning_revision_store import (
    DurablePlanningRevisionStore,
    PlanningAcceptedHeadBinding,
    PlanningRevision,
    PlanningRevisionStore,
)
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


def _isolated_constructor_subclass(
    native_class: type, method_names: tuple[str, ...],
) -> type:
    namespace = {"__slots__": ()}
    namespace.update(
        (name, native_class.__dict__[name]) for name in method_names
    )
    return type(f"_Isolated{native_class.__name__}", (native_class,), namespace)


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


def _dual_inputs(fixture, nonce: str):
    from src.context_metadata_binding import McpContextMetadataBinding

    binding = fixture["binding"]
    principal = fixture["issuer"].issue(
        subject_id=binding.owner_sid,
        owner_id="fuzzy",
        auth_context_digest="sha256:" + "8" * 64,
        ttl_seconds=3600,
        nonce=nonce,
    )
    foundation = SimpleNamespace(
        owner_subject_id=binding.owner_sid,
        security_action_revision=binding.security_revision,
    )
    metadata = McpContextMetadataBinding(
        "odysseus.context.mcp_metadata_binding.v1",
        "authenticated_mcp_context_read",
        binding.owner_sid,
        binding.chat_session_id,
        f"dual-source-{nonce}",
        "mcp:harbour:context:read",
        1,
        binding.security_revision,
        f"dual-source-{nonce}",
    )
    marker = object()

    @contextmanager
    def principal_mcp(_self, admission, metadata_binding):
        assert admission is marker and metadata_binding is metadata
        yield principal, "fuzzy", foundation

    return marker, metadata, foundation, principal, principal_mcp


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


def test_native_stable_pair_reuses_validation_only_and_rechecks_both_source_chains(
    durable_planning_source, monkeypatch,
):
    fixture = durable_planning_source
    source, binding, store = fixture["source"], fixture["binding"], fixture["store"]
    target = source.discover(binding)[0]
    revision_count = len(json.loads(store._path.read_bytes())["revisions"])
    original_validate = planning_store_module.validate_planning_definition
    original_records = DurablePlanningContextSource._records
    original_read = Path.read_bytes
    validations, source_chain_checks, physical_reads = [], [], []

    def counted_validate(document):
        validations.append(1)
        return original_validate(document)

    def counted_records(self, alias, records, heads, **kwargs):
        source_chain_checks.append(1)
        return original_records(self, alias, records, heads, **kwargs)

    def counted_read(path):
        if path == store._path:
            physical_reads.append(1)
        return original_read(path)

    monkeypatch.setattr(
        planning_store_module, "validate_planning_definition", counted_validate,
    )
    monkeypatch.setattr(DurablePlanningContextSource, "_records", counted_records)
    monkeypatch.setattr(Path, "read_bytes", counted_read)
    snapshot = source.capture(binding)
    assert len(snapshot.descriptors) == 6
    assert len(validations) == revision_count
    assert len(source_chain_checks) == 2
    assert len(physical_reads) == 4

    standalone = store.read_subject_bound_accepted_migration_bundle(
        fixture["principal"], project_id="harbour-one",
    )
    assert len(standalone.revisions) == revision_count
    assert len(validations) == revision_count * 2
    assert len(source_chain_checks) == 2
    assert len(physical_reads) == 6

    assert source.read_document(binding, target)
    assert len(validations) == revision_count * 4
    assert len(source_chain_checks) == 4
    assert len(physical_reads) == 10


def test_native_mcp_stable_pair_uses_one_lexical_validation_scope(
    durable_planning_source, monkeypatch,
):
    fixture = durable_planning_source
    source, binding, store = fixture["source"], fixture["binding"], fixture["store"]
    expected = source._stable_capture(
        binding, fixture["principal"], "fuzzy",
    )
    revision_count = len(json.loads(store._path.read_bytes())["revisions"])
    original_validate = planning_store_module.validate_planning_definition
    validations, captures = [], []

    def counted_validate(document):
        validations.append(1)
        return original_validate(document)

    def paired_capture(self, admission, metadata_binding, principal, alias, foundation):
        captures.append((admission, metadata_binding, principal, alias, foundation))
        bundle = self._store.read_subject_bound_accepted_migration_bundle(
            principal, project_id=self._project_id,
        )
        assert len(bundle.revisions) == revision_count
        return expected

    monkeypatch.setattr(
        planning_store_module, "validate_planning_definition", counted_validate,
    )
    monkeypatch.setattr(DurablePlanningContextSource, "_capture_mcp", paired_capture)
    admission, metadata_binding, foundation = object(), object(), object()
    actual = source._stable_capture_mcp(
        admission, metadata_binding, fixture["principal"], "fuzzy", foundation,
    )
    assert actual == expected
    assert len(captures) == 2
    assert all(
        call == (admission, metadata_binding, fixture["principal"], "fuzzy", foundation)
        for call in captures
    )
    assert len(validations) == revision_count


def test_real_native_mcp_stable_pair_reuses_validation_and_rechecks_source_chain(
    tmp_path, monkeypatch,
):
    from src.context_metadata_binding import _open_mcp_metadata_admission
    from tests.test_context_manifest_producer import _real_three_domain_context_body
    from tests.test_mcp_context_metadata import _install_real_mcp_authority, _lease

    body = _real_three_domain_context_body(tmp_path, monkeypatch)
    fixture = next(body)
    authority = None
    lease = None
    try:
        auth = fixture["auth"]
        runtime = fixture["planning_producer"]._runtime
        owner_sid = auth.subject_id_for_username("fuzzy")
        principal = runtime.issuer.issue(
            subject_id=owner_sid,
            owner_id="fuzzy",
            auth_context_digest="sha256:" + "5" * 64,
            ttl_seconds=3600,
            nonce="real-native-mcp-pair-validation",
        )
        with CookieConfirmationAuthority(auth).hold(
            fixture["cookie"], fixture["chat_id"],
        ) as setup_binding:
            _append_clean_descendants(
                {"store": runtime.durable_store, "principal": principal}, setup_binding,
            )
        source = DurablePlanningContextSource(
            runtime=runtime,
            auth_manager=auth,
            project_id="harbour-one",
            policy=DEFAULT_DURABLE_PLANNING_CONTEXT_POLICY,
        )
        authority = _install_real_mcp_authority(fixture)
        fixture["authority"] = authority
        lease = _lease(fixture, "real-planning-pair-validation")
        store = runtime.durable_store
        revision_count = len(json.loads(store._path.read_bytes())["revisions"])
        original_validate = planning_store_module.validate_planning_definition
        original_records = DurablePlanningContextSource._records
        original_read = Path.read_bytes
        validations, source_chain_checks, physical_reads = [], [], []

        def counted_validate(document):
            validations.append(1)
            return original_validate(document)

        def counted_records(self, alias, records, heads, **kwargs):
            source_chain_checks.append(1)
            return original_records(self, alias, records, heads, **kwargs)

        def counted_read(path):
            if path == store._path:
                physical_reads.append(1)
            return original_read(path)

        monkeypatch.setattr(
            planning_store_module, "validate_planning_definition", counted_validate,
        )
        monkeypatch.setattr(DurablePlanningContextSource, "_records", counted_records)
        monkeypatch.setattr(Path, "read_bytes", counted_read)
        with _open_mcp_metadata_admission(authority, lease) as (admission, metadata):
            captured = source._stable_capture_mcp(
                admission, metadata, principal, "fuzzy", lease.binding,
            )
        assert len(captured.snapshot.descriptors) == 6
        assert len(validations) == revision_count
        assert len(source_chain_checks) == 2
        assert len(physical_reads) == 4
    finally:
        if authority is not None and lease is not None:
            authority.release_read_lease(lease)
        if authority is not None:
            authority.close()
        try:
            next(body)
        except StopIteration:
            pass


def test_dual_projection_uses_one_bundle_for_distinct_recipient_domains(
    durable_planning_source, monkeypatch,
):
    fixture = durable_planning_source
    source = fixture["source"]
    binding = fixture["binding"]
    selection = _selection(source, binding)
    marker, metadata, foundation, _mcp_principal, principal_mcp = _dual_inputs(
        fixture, "mcp-0001",
    )
    original_materialize = fixture["store"]._materialize_accepted_migration_locked
    original_dual = DurablePlanningContextSource._capture_dual_from_bundle
    original_cookie_principal = DurablePlanningContextSource._principal
    original_mcp_principal = DurablePlanningContextSource._principal_mcp
    materializations = []
    observed_bundles = []
    observed_pairs = []
    lifecycle_trace = []

    def materialize(**kwargs):
        materializations.append(kwargs["batch"]["batch_id"])
        return original_materialize(**kwargs)

    def observed_dual(self, *args, **kwargs):
        observed_bundles.append(args[3])
        inner = original_dual(self, *args, **kwargs)
        try:
            captures = next(inner)
            observed_pairs.append(captures)
            yield captures
            phase = next(inner)
            lifecycle_trace.append("interexit_check")
            yield phase
            phase = next(inner)
            lifecycle_trace.append("postcookie_check")
            yield phase
            try:
                next(inner)
            except StopIteration as completed:
                if completed.value is not None:
                    raise AssertionError("dual validator returned a payload")
                lifecycle_trace.append("finalstamp")
                return
            raise AssertionError("dual validator yielded an extra phase")
        finally:
            inner.close()

    monkeypatch.setattr(
        fixture["store"], "_materialize_accepted_migration_locked", materialize,
    )
    with monkeypatch.context() as adapter:
        adapter.setattr(
            source_module, "_require_mcp_metadata_admission",
            lambda admission, metadata_binding: foundation,
        )
        adapter.setattr(DurablePlanningContextSource, "_principal_mcp", principal_mcp)
        adapter.setattr(
            DurablePlanningContextSource, "_capture_dual_from_bundle", observed_dual,
        )
        dual_source = DurablePlanningContextSource(
            runtime=source._runtime,
            auth_manager=source._auth_manager,
            project_id=source._project_id,
            policy=source._policy,
        )
        cookie, mcp = dual_source.resolve_dual(
            binding, marker, metadata, selection,
        )
        assert len(observed_bundles) == 1
        expected_cookie_capture = dual_source._capture_from_bundle(
            binding, observed_bundles[0], "fuzzy",
        )
        expected_mcp_capture = dual_source._capture_mcp_from_bundle(
            marker, metadata, observed_bundles[0], "fuzzy", foundation,
        )
        expected_cookie_by_key = {
            (item.source.source_id, item.source_version.source_version_id): item
            for item in expected_cookie_capture.snapshot.descriptors
        }
        expected_mcp_by_key = {
            (item.source.source_id, item.source_version.source_version_id): item
            for item in expected_mcp_capture.snapshot.descriptors
        }
        expected_cookie = tuple(
            expected_cookie_by_key[(item.selection_ref, item.revision_ref)]
            for item in selection.items
        )
        expected_mcp = tuple(
            expected_mcp_by_key[(item.selection_ref, item.revision_ref)]
            for item in selection.items
        )

        assert observed_pairs == [(expected_cookie_capture, expected_mcp_capture)]
        for actual, expected in zip(
            observed_pairs[0], (expected_cookie_capture, expected_mcp_capture),
        ):
            assert actual.snapshot.policy == expected.snapshot.policy
            assert actual.snapshot.boundary_sha256 == expected.snapshot.boundary_sha256
            assert actual.snapshot.metadata_sha256 == expected.snapshot.metadata_sha256
            assert actual.snapshot.descriptors == expected.snapshot.descriptors
            assert actual.payloads == expected.payloads
        assert cookie == expected_cookie
        assert mcp == expected_mcp
        assert tuple(item.selection() for item in cookie) == selection.items
        assert tuple(item.selection() for item in mcp) == selection.items
        assert cookie[0].boundary_sha256 != mcp[0].boundary_sha256
        assert cookie[0].record_sha256 != mcp[0].record_sha256

        constructor_specs = {
            "owner_scope_new": (
                "OwnerScope", source_module.OwnerScope,
                ("__init__", "__post_init__", "for_subject_id"),
            ),
            "source_new": (
                "SourceRecord", source_module.SourceRecord,
                ("__init__", "__post_init__"),
            ),
            "version_new": (
                "SourceVersionRecord", source_module.SourceVersionRecord,
                ("__init__", "__post_init__", "create"),
            ),
            "descriptor_new": (
                "DurablePlanningSourceDescriptor",
                source_module.DurablePlanningSourceDescriptor,
                ("__init__", "__post_init__", "canonical"),
            ),
            "snapshot_new": (
                "DurablePlanningSourceSnapshot",
                source_module.DurablePlanningSourceSnapshot,
                ("__init__", "__post_init__"),
            ),
            "capture_new": (
                "_Capture", source_module._Capture, ("__init__",),
            ),
        }
        constructor_raw = {
            target: {
                name: native_class.__dict__.get(name)
                for name in tuple(dict.fromkeys(
                    ("__new__", "__init__", "__post_init__") + method_names
                ))
            }
            for target, (_alias, native_class, method_names)
            in constructor_specs.items()
        }

        for target in (
            "cookie_principal", "mcp_principal", "cookie_projection",
            "mcp_projection", "dual_projection", "heads", "records",
            "pin_checker", "store_instance", "policy_module", "digest_module",
            "object_digest_module", "json_loads", "json_dumps",
            "hashlib_sha256", "content_hash_module", "admission_module",
            "heads_descriptor", "version_descriptor", "owner_scope_descriptor",
            "owner_scope_new", "owner_scope_init", "owner_scope_postinit",
            "source_new", "source_init", "source_postinit",
            "version_new", "version_init", "version_postinit",
            "descriptor_class", "descriptor_new", "descriptor_init",
            "descriptor_postinit", "snapshot_class", "snapshot_new",
            "snapshot_init", "snapshot_postinit", "capture_class",
            "capture_new", "capture_init", "capture_postinit",
            "identity_tuple", "identity_anchor", "identity_slot_descriptor",
            "identity_anchor_slot_descriptor", "identity_slot_reference",
            "identity_anchor_slot_reference", "stop_iteration_alias",
        ):
            fake_calls = []

            def fake(*_args, **_kwargs):
                fake_calls.append(target)
                raise AssertionError("hostile dual helper executed")

            class HostileDescriptor:
                def __get__(self, instance, owner=None):
                    return fake(instance, owner)

            if target in constructor_specs:
                alias, native_class, method_names = constructor_specs[target]
                isolated_class = _isolated_constructor_subclass(
                    native_class, method_names,
                )
                with monkeypatch.context() as isolated:
                    isolated.setattr(source_module, alias, isolated_class)
                    isolated.setattr(
                        fixture["store"],
                        "_materialize_accepted_migration_locked",
                        original_materialize,
                    )
                    isolated.setattr(
                        DurablePlanningContextSource,
                        "_capture_dual_from_bundle", original_dual,
                    )
                    isolated_source = DurablePlanningContextSource(
                        runtime=source._runtime,
                        auth_manager=source._auth_manager,
                        project_id=source._project_id,
                        policy=source._policy,
                    )
                    baseline_cookie, baseline_mcp = isolated_source.resolve_dual(
                        binding, marker, metadata, selection,
                    )
                    assert baseline_cookie and baseline_mcp
                    with monkeypatch.context() as mutation:
                        mutation.setattr(
                            isolated_class, "__new__", staticmethod(fake),
                            raising=False,
                        )
                        with pytest.raises(DurablePlanningContextError) as rejected:
                            isolated_source.resolve_dual(
                                binding, marker, metadata, selection,
                            )
                        assert rejected.value.code == "invalid_context"
                        assert fake_calls == []
                isolated_class = None
                isolated_source = None
                baseline_cookie = baseline_mcp = None
                assert getattr(source_module, alias) is native_class
                assert all(
                    native_class.__dict__.get(name) is raw
                    for name, raw in constructor_raw[target].items()
                )
                with monkeypatch.context() as native:
                    native.setattr(
                        fixture["store"],
                        "_materialize_accepted_migration_locked",
                        original_materialize,
                    )
                    native.setattr(
                        DurablePlanningContextSource,
                        "_capture_dual_from_bundle", original_dual,
                    )
                    native_source = DurablePlanningContextSource(
                        runtime=source._runtime,
                        auth_manager=source._auth_manager,
                        project_id=source._project_id,
                        policy=source._policy,
                    )
                    native_cookie, native_mcp = native_source.resolve_dual(
                        binding, marker, metadata, selection,
                    )
                    assert native_cookie and native_mcp
                continue

            with monkeypatch.context() as hostile:
                if target == "cookie_principal":
                    hostile.setattr(DurablePlanningContextSource, "_principal", fake)
                elif target == "mcp_principal":
                    hostile.setattr(DurablePlanningContextSource, "_principal_mcp", fake)
                elif target == "cookie_projection":
                    hostile.setattr(DurablePlanningContextSource, "_capture_from_bundle", fake)
                elif target == "mcp_projection":
                    hostile.setattr(DurablePlanningContextSource, "_capture_mcp_from_bundle", fake)
                elif target == "dual_projection":
                    hostile.setattr(DurablePlanningContextSource, "_capture_dual_from_bundle", fake)
                elif target == "heads":
                    hostile.setattr(DurablePlanningContextSource, "_heads", fake)
                elif target == "records":
                    hostile.setattr(DurablePlanningContextSource, "_records", fake)
                elif target == "pin_checker":
                    hostile.setattr(DurablePlanningContextSource, "_assert_runtime_pins", fake)
                elif target == "policy_module":
                    hostile.setattr(source_module.planning_policy, "_contains_sensitive_value", fake)
                elif target == "digest_module":
                    hostile.setattr(source_module, "_digest", fake)
                elif target == "object_digest_module":
                    hostile.setattr(source_module, "_object_digest", fake)
                elif target == "json_loads":
                    hostile.setattr(source_module.json, "loads", fake)
                elif target == "json_dumps":
                    hostile.setattr(source_module.json, "dumps", fake)
                elif target == "hashlib_sha256":
                    hostile.setattr(source_module.hashlib, "sha256", fake)
                elif target == "content_hash_module":
                    hostile.setattr(source_module, "compute_roadmap_content_hash", fake)
                elif target == "admission_module":
                    hostile.setattr(source_module, "_require_mcp_metadata_admission", fake)
                elif target == "heads_descriptor":
                    hostile.setattr(
                        DurablePlanningContextSource, "_heads", HostileDescriptor(),
                    )
                elif target == "version_descriptor":
                    hostile.setattr(SourceVersionRecord, "create", HostileDescriptor())
                elif target == "owner_scope_descriptor":
                    hostile.setattr(source_module.OwnerScope, "for_subject_id", HostileDescriptor())
                elif target == "owner_scope_init":
                    hostile.setattr(source_module.OwnerScope, "__init__", fake)
                elif target == "owner_scope_postinit":
                    hostile.setattr(source_module.OwnerScope, "__post_init__", fake)
                elif target == "source_init":
                    hostile.setattr(SourceRecord, "__init__", fake)
                elif target == "source_postinit":
                    hostile.setattr(SourceRecord, "__post_init__", fake)
                elif target == "version_init":
                    hostile.setattr(SourceVersionRecord, "__init__", fake)
                elif target == "version_postinit":
                    hostile.setattr(SourceVersionRecord, "__post_init__", fake)
                elif target == "descriptor_class":
                    hostile.setattr(source_module, "DurablePlanningSourceDescriptor", fake)
                elif target == "descriptor_init":
                    hostile.setattr(source_module.DurablePlanningSourceDescriptor, "__init__", fake)
                elif target == "descriptor_postinit":
                    hostile.setattr(source_module.DurablePlanningSourceDescriptor, "__post_init__", fake)
                elif target == "snapshot_class":
                    hostile.setattr(source_module, "DurablePlanningSourceSnapshot", fake)
                elif target == "snapshot_init":
                    hostile.setattr(source_module.DurablePlanningSourceSnapshot, "__init__", fake)
                elif target == "snapshot_postinit":
                    hostile.setattr(source_module.DurablePlanningSourceSnapshot, "__post_init__", fake)
                elif target == "capture_class":
                    hostile.setattr(source_module, "_Capture", fake)
                elif target == "capture_init":
                    hostile.setattr(source_module._Capture, "__init__", fake)
                elif target == "capture_postinit":
                    hostile.setattr(source_module._Capture, "__post_init__", fake, raising=False)
                elif target == "identity_tuple":
                    hostile.setattr(
                        dual_source, "_dual_identity",
                        tuple(list(dual_source._dual_identity)),
                    )
                elif target == "identity_anchor":
                    hostile.setattr(
                        dual_source, "_dual_identity_anchor",
                        tuple(list(dual_source._dual_identity_anchor)),
                    )
                elif target == "identity_slot_descriptor":
                    hostile.setattr(
                        DurablePlanningContextSource, "_dual_identity",
                        HostileDescriptor(),
                    )
                elif target == "identity_anchor_slot_descriptor":
                    hostile.setattr(
                        DurablePlanningContextSource, "_dual_identity_anchor",
                        HostileDescriptor(),
                    )
                elif target == "identity_slot_reference":
                    hostile.setattr(
                        source_module, "_DURABLE_PLANNING_DUAL_IDENTITY_SLOT",
                        HostileDescriptor(),
                    )
                elif target == "identity_anchor_slot_reference":
                    hostile.setattr(
                        source_module,
                        "_DURABLE_PLANNING_DUAL_IDENTITY_ANCHOR_SLOT",
                        HostileDescriptor(),
                    )
                elif target == "stop_iteration_alias":
                    hostile.setattr(
                        source_module, "StopIteration", fake, raising=False,
                    )
                else:
                    assert target == "store_instance"
                    assert (
                        "_read_dual_subject_bound_accepted_migration_bundle"
                        not in fixture["store"].__dict__
                    )
                    hostile.setitem(
                        fixture["store"].__dict__,
                        "_read_dual_subject_bound_accepted_migration_bundle", fake,
                    )
                with pytest.raises(DurablePlanningContextError) as rejected:
                    dual_source.resolve_dual(binding, marker, metadata, selection)
                assert rejected.value.code == "invalid_context"
                assert fake_calls == []
            if target == "store_instance":
                assert (
                    "_read_dual_subject_bound_accepted_migration_bundle"
                    not in fixture["store"].__dict__
                )

    assert len(materializations) == 1

    for protocol_case in (
        "nongenerator", "malformed_capture", "premature",
        "wrong_intermediate", "extra_yield", "return_payload",
        "finalizer_error",
    ):
        protocol_fake_calls = []

        class ForeignProtocol:
            def __next__(self):
                protocol_fake_calls.append("next")
                raise AssertionError("foreign next executed")

            def close(self):
                protocol_fake_calls.append("close")
                raise AssertionError("foreign close executed")

        if protocol_case == "nongenerator":
            def protocol_helper(*_args, **_kwargs):
                return ForeignProtocol()
        else:
            def protocol_helper(self, *args, **kwargs):
                inner = None
                try:
                    if protocol_case == "premature":
                        return
                    if protocol_case == "malformed_capture":
                        yield object(), object()
                        return
                    inner = original_dual(self, *args, **kwargs)
                    captures = next(inner)
                    if protocol_case == "finalizer_error":
                        try:
                            yield captures
                        finally:
                            raise RuntimeError("validator finalizer failure")
                    yield captures
                    intermediate = next(inner)
                    if protocol_case == "wrong_intermediate":
                        yield "not-none"
                        return
                    yield intermediate
                    yield next(inner)
                    try:
                        next(inner)
                    except StopIteration as completed:
                        if completed.value is not None:
                            raise AssertionError("inner validator returned a payload")
                    else:
                        raise AssertionError("inner validator yielded an extra phase")
                    if protocol_case == "extra_yield":
                        yield None
                        return
                    if protocol_case == "return_payload":
                        return "unexpected"
                finally:
                    if inner is not None:
                        inner.close()

        with monkeypatch.context() as protocol:
            protocol.setattr(
                source_module, "_require_mcp_metadata_admission",
                lambda admission, metadata_binding: foundation,
            )
            protocol.setattr(
                DurablePlanningContextSource, "_principal_mcp", principal_mcp,
            )
            protocol.setattr(
                DurablePlanningContextSource,
                "_capture_dual_from_bundle", protocol_helper,
            )
            protocol_source = DurablePlanningContextSource(
                runtime=source._runtime,
                auth_manager=source._auth_manager,
                project_id=source._project_id,
                policy=source._policy,
            )
            with pytest.raises(DurablePlanningContextError):
                protocol_source.resolve_dual(binding, marker, metadata, selection)
        assert protocol_fake_calls == []

    lifecycle_trace.clear()

    @contextmanager
    def traced_cookie(self, current_binding):
        try:
            with original_cookie_principal(self, current_binding) as values:
                lifecycle_trace.append("cookie_enter")
                yield values
        finally:
            lifecycle_trace.append("cookie_exit")

    @contextmanager
    def traced_mcp(self, current_admission, current_metadata):
        try:
            with original_mcp_principal(
                self, current_admission, current_metadata,
            ) as values:
                lifecycle_trace.append("mcp_enter")
                yield values
        finally:
            lifecycle_trace.append("mcp_exit")

    def traced_admission(current_admission, current_metadata):
        if lifecycle_trace and lifecycle_trace[-1] == "postcookie_check":
            lifecycle_trace.append("final_admission")
        return foundation

    with monkeypatch.context() as traced:
        traced.setattr(
            source_module, "_require_mcp_metadata_admission", traced_admission,
        )
        traced.setattr(DurablePlanningContextSource, "_principal", traced_cookie)
        traced.setattr(DurablePlanningContextSource, "_principal_mcp", traced_mcp)
        traced.setattr(
            DurablePlanningContextSource, "_capture_dual_from_bundle", observed_dual,
        )
        traced_source = DurablePlanningContextSource(
            runtime=source._runtime,
            auth_manager=source._auth_manager,
            project_id=source._project_id,
            policy=source._policy,
        )
        traced_source.resolve_dual(binding, marker, metadata, selection)

    assert lifecycle_trace == [
        "cookie_enter", "mcp_enter", "mcp_exit", "interexit_check",
        "cookie_exit", "postcookie_check", "final_admission", "finalstamp",
    ]

    fake_checker_calls = []
    cookie_closes = []
    manager_entries = []
    manager_exits = []
    original_manager_guard = type(fixture["auth"]).__dict__["guard_current_admin_subject"]

    def fake_checker(*_args, **_kwargs):
        fake_checker_calls.append(1)
        raise AssertionError("substituted runtime checker executed")

    @contextmanager
    def closing_cookie(self, current_binding):
        try:
            with original_cookie_principal(self, current_binding) as values:
                yield values
        finally:
            cookie_closes.append(1)

    @contextmanager
    def tracked_manager_guard(self, subject_id):
        entry = len(manager_entries) + 1
        manager_entries.append(entry)
        try:
            with original_manager_guard(self, subject_id) as alias:
                yield alias
        finally:
            manager_exits.append(entry)

    with monkeypatch.context() as hostile_finalizer:
        @contextmanager
        def mutating_mcp(self, current_admission, current_metadata):
            with original_mcp_principal(
                self, current_admission, current_metadata,
            ) as values:
                try:
                    yield values
                finally:
                    hostile_finalizer.setattr(
                        DurablePlanningContextSource,
                        "_assert_runtime_pins", fake_checker,
                    )

        hostile_finalizer.setattr(
            source_module, "_require_mcp_metadata_admission",
            lambda admission, metadata_binding: foundation,
        )
        hostile_finalizer.setattr(
            type(fixture["auth"]),
            "guard_current_admin_subject", tracked_manager_guard,
        )
        hostile_finalizer.setattr(
            DurablePlanningContextSource, "_principal", closing_cookie,
        )
        hostile_finalizer.setattr(
            DurablePlanningContextSource, "_principal_mcp", mutating_mcp,
        )
        finalizer_source = DurablePlanningContextSource(
            runtime=source._runtime,
            auth_manager=source._auth_manager,
            project_id=source._project_id,
            policy=source._policy,
        )
        with pytest.raises(DurablePlanningContextError):
            finalizer_source.resolve_dual(binding, marker, metadata, selection)

    assert fake_checker_calls == []
    assert cookie_closes == [1]
    assert manager_entries == [1, 2]
    assert manager_exits == [2, 1]

    raw_holder = {}
    raw_events = []

    def retain_bundle(self, *args, **kwargs):
        raw_holder["bundle"] = args[3]
        raw_holder["batch_bytes"] = args[3].batch_bytes
        inner = original_dual(self, *args, **kwargs)
        try:
            yield next(inner)
            try:
                phase = next(inner)
            except DurablePlanningContextError:
                raw_events.append("interexit_reject")
                raise
            yield phase
            yield next(inner)
            try:
                next(inner)
            except StopIteration as completed:
                if completed.value is not None:
                    raise AssertionError("inner validator returned a payload")
            else:
                raise AssertionError("inner validator yielded an extra phase")
        finally:
            inner.close()

    @contextmanager
    def restoring_cookie(self, current_binding):
        with original_cookie_principal(self, current_binding) as values:
            try:
                yield values
            finally:
                object.__setattr__(
                    raw_holder["bundle"], "batch_bytes", raw_holder["batch_bytes"],
                )
                raw_events.append("cookie_restore")

    @contextmanager
    def mutating_raw_mcp(self, current_admission, current_metadata):
        with original_mcp_principal(
            self, current_admission, current_metadata,
        ) as values:
            try:
                yield values
            finally:
                bundle = raw_holder["bundle"]
                object.__setattr__(
                    bundle, "batch_bytes", bytes(bytearray(bundle.batch_bytes)),
                )
                raw_events.append("mcp_mutate")

    with monkeypatch.context() as raw_mutation:
        raw_mutation.setattr(
            source_module, "_require_mcp_metadata_admission",
            lambda admission, metadata_binding: foundation,
        )
        raw_mutation.setattr(
            DurablePlanningContextSource, "_principal", restoring_cookie,
        )
        raw_mutation.setattr(
            DurablePlanningContextSource, "_principal_mcp", mutating_raw_mcp,
        )
        raw_mutation.setattr(
            DurablePlanningContextSource, "_capture_dual_from_bundle", retain_bundle,
        )
        raw_source = DurablePlanningContextSource(
            runtime=source._runtime,
            auth_manager=source._auth_manager,
            project_id=source._project_id,
            policy=source._policy,
        )
        with pytest.raises(DurablePlanningContextError):
            raw_source.resolve_dual(binding, marker, metadata, selection)

    assert raw_events == ["mcp_mutate", "interexit_reject", "cookie_restore"]
    assert raw_holder["bundle"].batch_bytes is raw_holder["batch_bytes"]

    cookie_exit_holder = {}
    cookie_exit_events = []

    def retain_cookie_exit_bundle(self, *args, **kwargs):
        cookie_exit_holder["bundle"] = args[3]
        inner = original_dual(self, *args, **kwargs)
        try:
            yield next(inner)
            yield next(inner)
            try:
                phase = next(inner)
            except DurablePlanningContextError:
                cookie_exit_events.append("postcookie_reject")
                raise
            yield phase
            try:
                next(inner)
            except StopIteration as completed:
                if completed.value is not None:
                    raise AssertionError("inner validator returned a payload")
            else:
                raise AssertionError("inner validator yielded an extra phase")
        finally:
            inner.close()

    @contextmanager
    def mutating_cookie_exit(self, current_binding):
        with original_cookie_principal(self, current_binding) as values:
            try:
                yield values
            finally:
                bundle = cookie_exit_holder["bundle"]
                object.__setattr__(
                    bundle, "batch_bytes", bytes(bytearray(bundle.batch_bytes)),
                )
                cookie_exit_events.append("cookie_mutate")

    with monkeypatch.context() as cookie_exit_mutation:
        cookie_exit_mutation.setattr(
            source_module, "_require_mcp_metadata_admission",
            lambda admission, metadata_binding: foundation,
        )
        cookie_exit_mutation.setattr(
            DurablePlanningContextSource, "_principal", mutating_cookie_exit,
        )
        cookie_exit_mutation.setattr(
            DurablePlanningContextSource,
            "_capture_dual_from_bundle", retain_cookie_exit_bundle,
        )
        cookie_exit_source = DurablePlanningContextSource(
            runtime=source._runtime,
            auth_manager=source._auth_manager,
            project_id=source._project_id,
            policy=source._policy,
        )
        with pytest.raises(DurablePlanningContextError):
            cookie_exit_source.resolve_dual(binding, marker, metadata, selection)

    assert cookie_exit_events == ["cookie_mutate", "postcookie_reject"]

    for failure_phase in (
        "mcp_entry", "render", "mcp_exit", "interexit",
        "cookie_exit", "selection", "final_admission",
    ):
        scope_events = []
        admission_calls = []
        validator_events = []

        @contextmanager
        def failure_cookie(_self, _binding):
            scope_events.append("cookie_enter")
            try:
                yield fixture["principal"], "fuzzy"
            finally:
                scope_events.append("cookie_exit")
                if failure_phase == "cookie_exit":
                    raise RuntimeError("cookie exit failure")

        @contextmanager
        def failure_mcp(_self, _admission, _metadata):
            if failure_phase == "mcp_entry":
                raise RuntimeError("mcp entry failure")
            scope_events.append("mcp_enter")
            try:
                yield _mcp_principal, "fuzzy", foundation
            finally:
                scope_events.append("mcp_exit")
                if failure_phase == "mcp_exit":
                    raise RuntimeError("mcp exit failure")

        def failure_admission(current_admission, current_metadata):
            admission_calls.append(1)
            if failure_phase == "final_admission" and len(admission_calls) == 4:
                raise RuntimeError("final admission failure")
            return foundation

        def failure_helper(self, *args, **kwargs):
            inner = original_dual(self, *args, **kwargs)
            try:
                if failure_phase == "render":
                    raise RuntimeError("render failure")
                captures = next(inner)
                if failure_phase == "selection":
                    cookie_capture = captures[0]
                    empty_snapshot = source_module.DurablePlanningSourceSnapshot(
                        (), cookie_capture.snapshot.policy,
                        cookie_capture.snapshot.boundary_sha256,
                        cookie_capture.snapshot.metadata_sha256,
                    )
                    captures = (
                        source_module._Capture(empty_snapshot, cookie_capture.payloads),
                        captures[1],
                    )
                yield captures
                if failure_phase == "interexit":
                    raise RuntimeError("inter-exit failure")
                yield next(inner)
                yield next(inner)
                try:
                    next(inner)
                except StopIteration as completed:
                    if completed.value is not None:
                        raise AssertionError("inner validator returned a payload")
                else:
                    raise AssertionError("inner validator yielded an extra phase")
            finally:
                inner.close()
                validator_events.append("validator_close")

        with monkeypatch.context() as failure:
            failure.setattr(
                source_module, "_require_mcp_metadata_admission", failure_admission,
            )
            failure.setattr(
                DurablePlanningContextSource, "_principal", failure_cookie,
            )
            failure.setattr(
                DurablePlanningContextSource, "_principal_mcp", failure_mcp,
            )
            failure.setattr(
                DurablePlanningContextSource,
                "_capture_dual_from_bundle", failure_helper,
            )
            failure_source = DurablePlanningContextSource(
                runtime=source._runtime,
                auth_manager=source._auth_manager,
                project_id=source._project_id,
                policy=source._policy,
            )
            with pytest.raises(DurablePlanningContextError):
                failure_source.resolve_dual(binding, marker, metadata, selection)

        assert scope_events.count("cookie_enter") == 1
        assert scope_events.count("cookie_exit") == 1
        expected_mcp = 0 if failure_phase == "mcp_entry" else 1
        assert scope_events.count("mcp_enter") == expected_mcp
        assert scope_events.count("mcp_exit") == expected_mcp
        expected_validator = 0 if failure_phase == "mcp_entry" else 1
        assert validator_events.count("validator_close") == expected_validator


def test_dual_projection_reduces_neutral_operations_and_preserves_fresh_reads(
    durable_planning_source, monkeypatch,
):
    fixture = durable_planning_source
    source, binding, store = fixture["source"], fixture["binding"], fixture["store"]
    selection = _selection(source, binding)
    marker, metadata, foundation, _principal, principal_mcp = _dual_inputs(
        fixture, "operation-counts",
    )
    revision_count = len(json.loads(store._path.read_bytes())["revisions"])
    roadmap_ids = tuple(
        item.roadmap_id for item in source.capture(binding).descriptors
    )
    original_validate = planning_store_module.validate_planning_definition
    original_records = DurablePlanningContextSource._records
    original_policy = source_module.planning_policy._contains_sensitive_value
    original_canonical = source_module._canonical
    original_dual = DurablePlanningContextSource._capture_dual_from_bundle
    original_materialize = store._materialize_accepted_migration_locked
    original_read = Path.read_bytes
    validations, record_calls, policy_order = [], [], []
    projection_calls, materializations, physical_reads = [], [], []
    observed_bundles = []

    def validate(document):
        validations.append(1)
        return original_validate(document)

    def records(self, alias, values, heads, **kwargs):
        record_calls.append(1)
        return original_records(self, alias, values, heads, **kwargs)

    def policy(text):
        document = json.loads(text)
        policy_order.append(document["roadmaps"][0]["roadmap_id"])
        return original_policy(text)

    def canonical(value):
        if type(value) is dict and value.get("schema_id") == source_module._PROJECTION_SCHEMA:
            projection_calls.append(value["roadmap"]["roadmap_id"])
        return original_canonical(value)

    def materialize(**kwargs):
        materializations.append(kwargs["batch"]["batch_id"])
        return original_materialize(**kwargs)

    def observed_dual(self, *args, **kwargs):
        observed_bundles.append(args[3])
        return original_dual(self, *args, **kwargs)

    def read_bytes(path):
        if path == store._path:
            physical_reads.append(1)
        return original_read(path)

    with monkeypatch.context() as adapter:
        adapter.setattr(planning_store_module, "validate_planning_definition", validate)
        adapter.setattr(DurablePlanningContextSource, "_records", records)
        adapter.setattr(source_module.planning_policy, "_contains_sensitive_value", policy)
        adapter.setattr(source_module, "_canonical", canonical)
        adapter.setattr(
            source_module, "_require_mcp_metadata_admission",
            lambda admission, metadata_binding: foundation,
        )
        adapter.setattr(DurablePlanningContextSource, "_principal_mcp", principal_mcp)
        adapter.setattr(
            DurablePlanningContextSource, "_capture_dual_from_bundle", observed_dual,
        )
        adapter.setattr(store, "_materialize_accepted_migration_locked", materialize)
        adapter.setattr(Path, "read_bytes", read_bytes)
        dual_source = DurablePlanningContextSource(
            runtime=source._runtime,
            auth_manager=source._auth_manager,
            project_id=source._project_id,
            policy=source._policy,
        )
        cookie, mcp = dual_source.resolve_dual(binding, marker, metadata, selection)
        dual_counts = (
            len(validations), len(record_calls), tuple(projection_calls),
            tuple(policy_order), len(materializations), len(physical_reads),
        )
        cookie2, mcp2 = dual_source.resolve_dual(binding, marker, metadata, selection)
        second_counts = (
            len(validations), len(record_calls), tuple(projection_calls),
            tuple(policy_order), len(materializations), len(physical_reads),
        )
        expected_cookie_capture = dual_source._capture_from_bundle(
            binding, observed_bundles[0], "fuzzy",
        )
        expected_mcp_capture = dual_source._capture_mcp_from_bundle(
            marker, metadata, observed_bundles[0], "fuzzy", foundation,
        )
        expected_cookie_by_key = {
            (item.source.source_id, item.source_version.source_version_id): item
            for item in expected_cookie_capture.snapshot.descriptors
        }
        expected_mcp_by_key = {
            (item.source.source_id, item.source_version.source_version_id): item
            for item in expected_mcp_capture.snapshot.descriptors
        }
        expected_cookie = tuple(
            expected_cookie_by_key[(item.selection_ref, item.revision_ref)]
            for item in selection.items
        )
        expected_mcp = tuple(
            expected_mcp_by_key[(item.selection_ref, item.revision_ref)]
            for item in selection.items
        )

    assert len(cookie) == len(mcp) == len(roadmap_ids)
    assert cookie == expected_cookie
    assert mcp == expected_mcp
    assert cookie2 == cookie
    assert mcp2 == mcp
    assert len(observed_bundles) == 2
    assert observed_bundles[0] is not observed_bundles[1]
    assert dual_counts == (
        revision_count, 1, roadmap_ids, roadmap_ids + roadmap_ids, 1, 2,
    )
    assert second_counts == (
        revision_count * 2, 2, roadmap_ids * 2, roadmap_ids * 4, 2, 4,
    )
    assert len(validations) == revision_count * 2
    assert len(record_calls) == 4
    assert tuple(projection_calls) == roadmap_ids * 4
    assert tuple(policy_order) == roadmap_ids * 6
    assert len(materializations) == 2
    assert len(physical_reads) == 4


_DUAL_RETAINED_ALIAS_CASES = (
    "batch_equal_bytes",
    "heads_equal_tuple",
    "revisions_list",
    "head_bool_revision",
    "payload_equal_bytes",
    "payload_bytearray",
    "revision_created_at",
    "revision_created_at_after_cookie",
    "revision_subclass",
    "principal_ref_owner",
    "principal_ref_subclass",
    "mcp_principal_owner",
    "cookie_principal_sealed",
    "source_project_type",
    "source_policy_identity",
    "batch_equal_bytes_final_admission",
    "revision_created_at_digest",
    "payload_equal_bytes_version_constructor",
)


@pytest.mark.parametrize("case", _DUAL_RETAINED_ALIAS_CASES, ids=_DUAL_RETAINED_ALIAS_CASES)
def test_dual_projection_rejects_retained_alias_mutation_without_pair(
    durable_planning_source, monkeypatch, case,
):
    fixture = durable_planning_source
    source, binding = fixture["source"], fixture["binding"]
    selection = _selection(source, binding)
    marker, metadata, foundation, _principal, principal_mcp = _dual_inputs(
        fixture, f"retained-{case}",
    )
    original_dual = DurablePlanningContextSource._capture_dual_from_bundle
    original_policy = source_module.planning_policy._contains_sensitive_value
    original_digest = source_module._digest
    original_version_descriptor = SourceVersionRecord.__dict__["create"]
    holder = {}
    policy_calls = []

    class RevisionSubclass(PlanningRevision):
        pass

    class PrincipalRefSubclass(PrincipalRef):
        pass

    def observed_dual(self, *args, **kwargs):
        holder["source"] = self
        holder["bundle"] = args[3]
        holder["cookie_principal"] = args[6]
        holder["mcp_principal"] = args[7]
        return original_dual(self, *args, **kwargs)

    def mutate():
        dual_source = holder["source"]
        bundle = holder["bundle"]
        member = bundle.revisions[0]
        revision = member.revision
        principal_ref = revision.principal_ref
        if case in {"batch_equal_bytes", "batch_equal_bytes_final_admission"}:
            object.__setattr__(bundle, "batch_bytes", bytes(bytearray(bundle.batch_bytes)))
        elif case == "heads_equal_tuple":
            object.__setattr__(bundle, "heads", tuple(list(bundle.heads)))
        elif case == "revisions_list":
            object.__setattr__(bundle, "revisions", list(bundle.revisions))
        elif case == "head_bool_revision":
            object.__setattr__(bundle.heads[0], "approved_revision", True)
        elif case in {"payload_equal_bytes", "payload_equal_bytes_version_constructor"}:
            object.__setattr__(member, "payload", bytes(bytearray(member.payload)))
        elif case == "payload_bytearray":
            object.__setattr__(member, "payload", bytearray(member.payload))
        elif case in {
            "revision_created_at", "revision_created_at_after_cookie",
            "revision_created_at_digest",
        }:
            object.__setattr__(revision, "created_at", revision.created_at + "0")
        elif case == "revision_subclass":
            replacement = RevisionSubclass(
                revision.revision_id, revision.owner_id, revision.project_id,
                revision.roadmap_id, revision.revision,
                revision.parent_revision_id, revision.payload_schema_id,
                revision.payload_sha256, revision.content_sha256,
                revision.immutable_locator, revision.revision_state,
                revision.created_at, revision.principal_ref,
            )
            object.__setattr__(member, "revision", replacement)
        elif case == "principal_ref_owner":
            object.__setattr__(principal_ref, "owner_id", "not-fuzzy")
        elif case == "principal_ref_subclass":
            replacement = PrincipalRefSubclass(
                principal_ref.issuer_id, principal_ref.subject_id,
                principal_ref.owner_id, principal_ref.auth_context_digest,
                principal_ref.principal_digest,
            )
            object.__setattr__(revision, "principal_ref", replacement)
        elif case == "mcp_principal_owner":
            object.__setattr__(holder["mcp_principal"], "_owner_id", "not-fuzzy")
        elif case == "cookie_principal_sealed":
            object.__setattr__(holder["cookie_principal"], "_sealed", 1)
        elif case == "source_project_type":
            object.__setattr__(dual_source, "_project_id", ["harbour-one"])
        elif case == "source_policy_identity":
            object.__setattr__(
                dual_source,
                "_policy",
                type(dual_source._policy)(
                    dual_source._policy.namespace,
                    dual_source._policy.policy_ref,
                    dual_source._policy.classification,
                    dual_source._policy.content_policy,
                    dual_source._policy.byteform,
                    dual_source._policy.reject_sensitive_content,
                ),
            )

    def mutate_once(text):
        policy_calls.append(1)
        trigger = len(selection.items) + 1 if case.endswith("_after_cookie") else 1
        callback_phase = case.endswith((
            "_final_admission", "_digest", "_version_constructor",
        ))
        if not callback_phase and len(policy_calls) == trigger:
            mutate()
        return original_policy(text)

    def digest(value):
        result = original_digest(value)
        if case.endswith("_digest"):
            mutate()
        return result

    def version_create(cls, source_record, **kwargs):
        result = original_version_descriptor.__func__(cls, source_record, **kwargs)
        if case.endswith("_version_constructor"):
            mutate()
        return result

    admission_calls = []

    def admission_check(admission, metadata_binding):
        admission_calls.append(1)
        if case.endswith("_final_admission") and len(admission_calls) == 4:
            mutate()
        return foundation

    with monkeypatch.context() as adapter:
        adapter.setattr(
            source_module, "_require_mcp_metadata_admission",
            admission_check,
        )
        adapter.setattr(DurablePlanningContextSource, "_principal_mcp", principal_mcp)
        adapter.setattr(
            DurablePlanningContextSource, "_capture_dual_from_bundle", observed_dual,
        )
        adapter.setattr(
            source_module.planning_policy, "_contains_sensitive_value", mutate_once,
        )
        adapter.setattr(source_module, "_digest", digest)
        adapter.setattr(SourceVersionRecord, "create", classmethod(version_create))
        dual_source = DurablePlanningContextSource(
            runtime=source._runtime,
            auth_manager=source._auth_manager,
            project_id=source._project_id,
            policy=source._policy,
        )
        with pytest.raises(DurablePlanningContextError) as rejected:
            dual_source.resolve_dual(binding, marker, metadata, selection)

    assert rejected.value.code in {"invalid_binding", "invalid_context", "planning_stale"}
    if case.endswith("_final_admission"):
        assert len(admission_calls) == 4
    expected_calls = (
        len(selection.items) * 2 if case.endswith("_final_admission")
        else 0 if case.endswith("_digest")
        else len(selection.items) + 1 if case.endswith("_after_cookie") else 1
    )
    assert len(policy_calls) == expected_calls


def test_dual_projection_isolates_cookie_graphs_and_transient_helper_shadow(
    durable_planning_source, monkeypatch,
):
    fixture = durable_planning_source
    source, binding = fixture["source"], fixture["binding"]
    selection = _selection(source, binding)
    marker, metadata, foundation, _principal, principal_mcp = _dual_inputs(
        fixture, "graph-isolation",
    )
    original_canonical = source_module._canonical
    original_policy = source_module.planning_policy._contains_sensitive_value
    original_heads_descriptor = DurablePlanningContextSource.__dict__["_heads"]
    retained = {}
    fake_calls = []
    policy_calls = []
    cross_constructor_specs = {
        "owner_scope_new": (
            "OwnerScope", source_module.OwnerScope,
            ("__init__", "__post_init__", "for_subject_id"),
        ),
        "capture_new": (
            "_Capture", source_module._Capture, ("__init__",),
        ),
    }
    cross_constructor_raw = {
        target: {
            name: native_class.__dict__.get(name)
            for name in tuple(dict.fromkeys(
                ("__new__", "__init__", "__post_init__") + method_names
            ))
        }
        for target, (_alias, native_class, method_names)
        in cross_constructor_specs.items()
    }

    def canonical(value):
        result = original_canonical(value)
        if type(value) is dict and {"backend", "batch", "heads", "records"} <= set(value):
            if "binding" in value:
                retained["cookie"] = value["batch"]
                value["batch"]["state"] = "hostile-retained-cookie-mutation"
                value["heads"].clear()
                return result
            retained["mcp"] = value["batch"]
        return result

    def transient_fake(*_args, **_kwargs):
        fake_calls.append(1)
        raise AssertionError("transient helper executed")

    def policy(text):
        policy_calls.append(1)
        if len(policy_calls) == 1:
            setattr(DurablePlanningContextSource, "_heads", transient_fake)
            setattr(DurablePlanningContextSource, "_heads", original_heads_descriptor)
        return original_policy(text)

    with monkeypatch.context() as adapter:
        adapter.setattr(
            source_module, "_require_mcp_metadata_admission",
            lambda admission, metadata_binding: foundation,
        )
        adapter.setattr(DurablePlanningContextSource, "_principal_mcp", principal_mcp)
        adapter.setattr(source_module, "_canonical", canonical)
        adapter.setattr(source_module.planning_policy, "_contains_sensitive_value", policy)
        dual_source = DurablePlanningContextSource(
            runtime=source._runtime,
            auth_manager=source._auth_manager,
            project_id=source._project_id,
            policy=source._policy,
        )
        cookie, mcp = dual_source.resolve_dual(binding, marker, metadata, selection)

    assert cookie and mcp
    assert fake_calls == []
    assert retained["cookie"] is not retained["mcp"]
    assert retained["cookie"]["state"] == "hostile-retained-cookie-mutation"
    assert retained["mcp"]["state"] == "accepted"

    for cross_target in (
        "digest", "loader", "content_hash", "owner_scope_new",
        "descriptor_class", "snapshot_init", "capture_new",
        "identity_slot_descriptor", "runtime_checker",
    ):
        cross_fake_calls = []
        substituted = []
        constructor_spec = cross_constructor_specs.get(cross_target)
        mutation_enabled = [constructor_spec is None]
        isolated_class = None

        def cross_fake(*_args, **_kwargs):
            cross_fake_calls.append(cross_target)
            raise AssertionError("cross-callback helper executed")

        with monkeypatch.context() as adapter:
            if constructor_spec is not None:
                alias, native_class, method_names = constructor_spec
                isolated_class = _isolated_constructor_subclass(
                    native_class, method_names,
                )
                adapter.setattr(source_module, alias, isolated_class)

            def cross_canonical(value):
                result = original_canonical(value)
                if (
                    mutation_enabled[0]
                    and not substituted
                    and type(value) is dict
                    and "roadmaps" in value
                ):
                    substituted.append(cross_target)
                    if cross_target == "digest":
                        adapter.setattr(source_module, "_digest", cross_fake)
                    elif cross_target == "loader":
                        adapter.setattr(source_module.json, "loads", cross_fake)
                    elif cross_target == "content_hash":
                        adapter.setattr(
                            source_module, "compute_roadmap_content_hash", cross_fake,
                        )
                    elif cross_target == "owner_scope_new":
                        assert source_module.OwnerScope is isolated_class
                        adapter.setattr(
                            isolated_class, "__new__",
                            staticmethod(cross_fake), raising=False,
                        )
                    elif cross_target == "descriptor_class":
                        adapter.setattr(
                            source_module, "DurablePlanningSourceDescriptor", cross_fake,
                        )
                    elif cross_target == "snapshot_init":
                        adapter.setattr(
                            source_module.DurablePlanningSourceSnapshot,
                            "__init__", cross_fake,
                        )
                    elif cross_target == "capture_new":
                        assert source_module._Capture is isolated_class
                        adapter.setattr(
                            isolated_class, "__new__",
                            staticmethod(cross_fake), raising=False,
                        )
                    elif cross_target == "identity_slot_descriptor":
                        class CrossHostileDescriptor:
                            def __get__(self, instance, owner=None):
                                return cross_fake(instance, owner)

                        adapter.setattr(
                            DurablePlanningContextSource, "_dual_identity",
                            CrossHostileDescriptor(),
                        )
                    else:
                        adapter.setattr(
                            DurablePlanningContextSource,
                            "_assert_runtime_pins", cross_fake,
                        )
                return result

            adapter.setattr(
                source_module, "_require_mcp_metadata_admission",
                lambda admission, metadata_binding: foundation,
            )
            adapter.setattr(DurablePlanningContextSource, "_principal_mcp", principal_mcp)
            adapter.setattr(source_module, "_canonical", cross_canonical)
            cross_source = DurablePlanningContextSource(
                runtime=source._runtime,
                auth_manager=source._auth_manager,
                project_id=source._project_id,
                policy=source._policy,
            )
            if constructor_spec is not None:
                baseline_cookie, baseline_mcp = cross_source.resolve_dual(
                    binding, marker, metadata, selection,
                )
                assert baseline_cookie and baseline_mcp
                mutation_enabled[0] = True
            with pytest.raises(DurablePlanningContextError) as rejected:
                cross_source.resolve_dual(binding, marker, metadata, selection)

        cross_source = None
        baseline_cookie = baseline_mcp = None
        isolated_class = None
        assert rejected.value.code == "invalid_context"
        assert substituted == [cross_target]
        assert cross_fake_calls == []
        if constructor_spec is not None:
            alias, native_class, _method_names = constructor_spec
            assert getattr(source_module, alias) is native_class
            assert all(
                native_class.__dict__.get(name) is raw
                for name, raw in cross_constructor_raw[cross_target].items()
            )
            with monkeypatch.context() as native:
                native.setattr(
                    source_module, "_require_mcp_metadata_admission",
                    lambda admission, metadata_binding: foundation,
                )
                native.setattr(
                    DurablePlanningContextSource, "_principal_mcp", principal_mcp,
                )
                native_source = DurablePlanningContextSource(
                    runtime=source._runtime,
                    auth_manager=source._auth_manager,
                    project_id=source._project_id,
                    policy=source._policy,
                )
                native_cookie, native_mcp = native_source.resolve_dual(
                    binding, marker, metadata, selection,
                )
                assert native_cookie and native_mcp


def test_dual_projection_cookie_allow_mcp_deny_returns_no_pair(
    durable_planning_source, monkeypatch,
):
    fixture = durable_planning_source
    source, binding = fixture["source"], fixture["binding"]
    selection = _selection(source, binding)
    marker, metadata, foundation, _principal, principal_mcp = _dual_inputs(
        fixture, "cookie-allow-mcp-deny",
    )
    original_policy = source_module.planning_policy._contains_sensitive_value
    calls = []

    def role_policy(text):
        calls.append(json.loads(text)["roadmaps"][0]["roadmap_id"])
        if len(calls) > len(selection.items):
            return True
        return original_policy(text)

    with monkeypatch.context() as adapter:
        adapter.setattr(
            source_module, "_require_mcp_metadata_admission",
            lambda admission, metadata_binding: foundation,
        )
        adapter.setattr(DurablePlanningContextSource, "_principal_mcp", principal_mcp)
        adapter.setattr(source_module.planning_policy, "_contains_sensitive_value", role_policy)
        dual_source = DurablePlanningContextSource(
            runtime=source._runtime,
            auth_manager=source._auth_manager,
            project_id=source._project_id,
            policy=source._policy,
        )
        with pytest.raises(DurablePlanningContextError) as rejected:
            dual_source.resolve_dual(binding, marker, metadata, selection)

    assert rejected.value.code == "policy_denied"
    assert len(calls) == len(selection.items) + 1


def test_dual_content_projection_returns_exact_bytes_only_after_all_guard_exits(
    durable_planning_source, monkeypatch,
):
    fixture = durable_planning_source
    source, binding = fixture["source"], fixture["binding"]
    selection = _selection(source, binding)
    target = selection.items[0]
    expected = source.read_document(binding, target)
    marker, metadata, foundation, _principal, principal_mcp = _dual_inputs(
        fixture, "dual-content",
    )
    events = []
    original_principal = DurablePlanningContextSource._principal
    original_capture = DurablePlanningContextSource._capture_dual_from_bundle

    @contextmanager
    def traced_principal(self, actual_binding):
        with original_principal(self, actual_binding) as values:
            yield values
        events.append("cookie_exit")

    @contextmanager
    def traced_principal_mcp(self, admission, metadata_binding):
        with principal_mcp(self, admission, metadata_binding) as values:
            yield values
        events.append("mcp_exit")

    def traced_admission(admission, metadata_binding):
        if events[-1:] == ["cookie_exit"]:
            events.append("final_admission")
        return foundation

    def traced_capture(*args, **kwargs):
        inner = original_capture(*args, **kwargs)
        yield next(inner)
        second = next(inner)
        events.append("interexit_check")
        yield second
        third = next(inner)
        yield third
        try:
            next(inner)
        except StopIteration:
            events.append("stamp")
            return
        raise AssertionError("validation generator exceeded its terminal stamp")

    with monkeypatch.context() as adapter:
        adapter.setattr(
            source_module, "_require_mcp_metadata_admission",
            traced_admission,
        )
        adapter.setattr(DurablePlanningContextSource, "_principal", traced_principal)
        adapter.setattr(
            DurablePlanningContextSource, "_principal_mcp", traced_principal_mcp,
        )
        adapter.setattr(
            DurablePlanningContextSource, "_capture_dual_from_bundle", traced_capture,
        )
        content_source = DurablePlanningContextSource(
            runtime=source._runtime,
            auth_manager=source._auth_manager,
            project_id=source._project_id,
            policy=source._policy,
        )
        metadata_only = content_source.resolve_dual(
            binding, marker, metadata, selection,
        )
        resolved = content_source.resolve_dual(
            binding, marker, metadata, selection, _content_target=target,
        )

        assert type(metadata_only) is tuple and len(metadata_only) == 2
        assert all(
            type(item) is not bytes
            for descriptors in metadata_only for item in descriptors
        )
        assert type(resolved) is tuple and len(resolved) == 4
        cookie, mcp, cookie_payload, mcp_payload = resolved
        assert tuple(item.selection() for item in cookie) == selection.items
        assert tuple(item.selection() for item in mcp) == selection.items
        assert cookie_payload == mcp_payload == expected
        assert hashlib.sha256(cookie_payload).hexdigest() == next(
            item.durable_payload_sha256
            for item in cookie if item.selection() == target
        ).removeprefix("sha256:")
        assert events[-5:] == [
            "mcp_exit", "interexit_check", "cookie_exit", "final_admission", "stamp",
        ]

        fake_calls = []

        def hostile_verify(*_args, **_kwargs):
            fake_calls.append("verify")
            raise AssertionError("substituted payload verifier executed")

        with monkeypatch.context() as hostile:
            hostile.setattr(
                DurablePlanningContextSource,
                "_verify_payload",
                staticmethod(hostile_verify),
            )
            with pytest.raises(DurablePlanningContextError) as rejected:
                content_source.resolve_dual(
                    binding, marker, metadata, selection,
                    _content_target=target,
                )
        assert rejected.value.code == "invalid_context"
        assert fake_calls == []

    original_principal_mcp = DurablePlanningContextSource._principal_mcp
    exits = []

    @contextmanager
    def failing_principal_mcp(self, admission, metadata_binding):
        with original_principal_mcp(self, admission, metadata_binding) as values:
            yield values
        exits.append("mcp_exit")
        raise DurablePlanningContextError("invalid_binding")

    returned = []
    with monkeypatch.context() as exit_failure:
        exit_failure.setattr(
            source_module, "_require_mcp_metadata_admission",
            lambda admission, metadata_binding: foundation,
        )
        exit_failure.setattr(
            DurablePlanningContextSource, "_principal_mcp", failing_principal_mcp,
        )
        guarded_source = DurablePlanningContextSource(
            runtime=source._runtime,
            auth_manager=source._auth_manager,
            project_id=source._project_id,
            policy=source._policy,
        )
        with pytest.raises(DurablePlanningContextError) as guard_rejected:
            returned.append(guarded_source.resolve_dual(
                binding, marker, metadata, selection,
                _content_target=target,
            ))
    assert guard_rejected.value.code == "invalid_binding"
    assert exits == ["mcp_exit"]
    assert returned == []


@pytest.mark.parametrize(
    "failure_stage",
    [
        "mcp_exit", "generator_interexit", "cookie_exit",
        "generator_end", "final_admission", "final_guard", "generator_stamp",
    ],
)
def test_dual_content_never_returns_provisional_bytes_across_terminal_fences(
    durable_planning_source, monkeypatch, failure_stage,
):
    fixture = durable_planning_source
    source, binding = fixture["source"], fixture["binding"]
    selection = _selection(source, binding)
    target = selection.items[0]
    marker, metadata, foundation, _principal, principal_mcp = _dual_inputs(
        fixture, "dual-content-fence-" + failure_stage,
    )
    original_principal = DurablePlanningContextSource._principal
    original_capture = DurablePlanningContextSource._capture_dual_from_bundle
    original_verify = DurablePlanningContextSource._verify_payload
    original_audience = binding.audience
    verify_calls = []
    admission_calls = []
    reached = []
    returned = []

    def observed_verify(payload, descriptor, **kwargs):
        result = original_verify(payload, descriptor, **kwargs)
        verify_calls.append(descriptor.durable_revision_id)
        return result

    @contextmanager
    def guarded_principal(self, actual_binding):
        with original_principal(self, actual_binding) as values:
            yield values
        if failure_stage == "cookie_exit":
            reached.append("cookie_exit")
            raise DurablePlanningContextError("invalid_binding")

    @contextmanager
    def guarded_principal_mcp(self, admission, metadata_binding):
        with principal_mcp(self, admission, metadata_binding) as values:
            yield values
        if failure_stage == "mcp_exit":
            reached.append("mcp_exit")
            raise DurablePlanningContextError("invalid_binding")

    def guarded_admission(admission, metadata_binding):
        admission_calls.append(1)
        if len(admission_calls) == 4:
            if failure_stage == "final_admission":
                reached.append("final_admission")
                raise DurablePlanningContextError("invalid_binding")
            if failure_stage == "final_guard":
                reached.append("final_guard")
                object.__setattr__(binding, "audience", "hostile-final-guard")
        return foundation

    def guarded_capture(*args, **kwargs):
        inner = original_capture(*args, **kwargs)
        try:
            yield next(inner)
            second = next(inner)
            if failure_stage == "generator_interexit":
                reached.append("generator_interexit")
                raise DurablePlanningContextError("invalid_context")
            yield second
            third = next(inner)
            if failure_stage == "generator_end":
                reached.append("generator_end")
                raise DurablePlanningContextError("invalid_context")
            yield third
            try:
                next(inner)
            except StopIteration:
                if failure_stage == "generator_stamp":
                    reached.append("generator_stamp")
                    raise DurablePlanningContextError("invalid_context")
                return
            raise AssertionError("validation generator exceeded terminal stamp")
        finally:
            inner.close()

    try:
        with monkeypatch.context() as guarded:
            guarded.setattr(
                source_module, "_require_mcp_metadata_admission", guarded_admission,
            )
            guarded.setattr(
                DurablePlanningContextSource, "_principal", guarded_principal,
            )
            guarded.setattr(
                DurablePlanningContextSource, "_principal_mcp", guarded_principal_mcp,
            )
            guarded.setattr(
                DurablePlanningContextSource,
                "_capture_dual_from_bundle", guarded_capture,
            )
            guarded.setattr(
                DurablePlanningContextSource,
                "_verify_payload", staticmethod(observed_verify),
            )
            guarded_source = DurablePlanningContextSource(
                runtime=source._runtime,
                auth_manager=source._auth_manager,
                project_id=source._project_id,
                policy=source._policy,
            )
            with pytest.raises(DurablePlanningContextError):
                returned.append(guarded_source.resolve_dual(
                    binding, marker, metadata, selection,
                    _content_target=target,
                ))
    finally:
        object.__setattr__(binding, "audience", original_audience)

    assert len(verify_calls) == 2
    assert reached == [failure_stage]
    assert returned == []
