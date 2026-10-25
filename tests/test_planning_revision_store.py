from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone, tzinfo
from contextlib import contextmanager
import hashlib
import json
import multiprocessing
from pathlib import Path
from threading import Event, Thread

import pytest

import src.planning_revision_store as durable_module
from src.harbour_authority import AuthenticatedPrincipalIssuer
from src.planning_definition_contract import compute_roadmap_content_hash, validate_planning_definition
from src.planning_roadmap_migration import (
    DOCS_ONLY_AUTHORITY_MAP_ID,
    DOCS_ONLY_AUTHORITY_PINS,
    REPO_ONLY_AUTHORITY_MAP_ID,
    REPO_ONLY_AUTHORITY_PINS,
    PRODUCT_READINESS_AUTHORITY_MAP_ID,
    PRODUCT_READINESS_AUTHORITY_PINS,
    HISTORICAL_AUTHORITY_PINS,
    MIGRATION_MANIFEST_SCHEMA_V1,
    build_h1r03_migration_manifest,
    strict_json_object,
)
from src.planning_revision_store import (
    DurablePlanningRevisionStore,
    DurablePlanningStoreError,
    PlanningRevisionStore,
    PlanningRevisionStoreError,
    PlanningHead,
    TEMPORARY_REPOSITORY_MARKER,
    TEMPORARY_REPOSITORY_SCHEMA,
    PlanningRevisionRepository,
    PlanningRevisionRepositoryError,
)
from src.mcp_authenticated_dispatch import (
    AppOwnedPlanningRuntime,
    McpAuthenticatedIdentity,
    McpDispatchFailure,
)
from tests.test_planning_definition_projection import definition_fixture
from tests.planning_durable_store_fixture import durable_planning_store


_HISTORICAL_VECTOR_FIXTURE = (
    Path(__file__).resolve().parents[1] / "tests/fixtures/h1r04-prepromotion-manifest-v3.json"
)


def _historical_vector_manifest() -> dict:
    fixture = strict_json_object(_HISTORICAL_VECTOR_FIXTURE.read_bytes())
    if set(fixture) != {
        "batch_id", "fixture_id", "manifest_digest", "manifest_identity", "provenance",
        "schema", "supporting_observation",
    }:
        raise AssertionError("historical vector fixture shape drift")
    if fixture["schema"] != "odysseus.harbour.planning_manifest_fixture.v1":
        raise AssertionError("historical vector fixture schema drift")
    provenance = fixture["provenance"]
    if set(provenance) != {
        "accepted_batch_id", "accepted_manifest_digest", "immutable_vector_refs",
        "materialization_copy_path", "materialization_copy_sha256", "receipt_manifest_digest",
        "receipt_path", "source_artifact_class", "source_artifact_path", "source_artifact_sha256",
    }:
        raise AssertionError("historical vector fixture provenance shape drift")
    if (
        provenance["source_artifact_sha256"] != "a62097be6864ffc3785d9ae017d49edfcc6b5f3e8139a9728ee92b80b29eb979"
        or provenance["source_artifact_class"] != "accepted_historical_serialized_store_bundle"
        or provenance["receipt_path"] != "docs/plans/evidence/harbour-one-h1r05-repo-acceptance-20261003.receipt"
    ):
        raise AssertionError("historical vector fixture provenance drift")
    observation = fixture["supporting_observation"]
    if (
        set(observation) != {"count", "digest"}
        or type(observation["count"]) is not int
        or observation["count"] != 424
        or type(observation["digest"]) is not str
        or observation["digest"] != "sha256:828c5f11324883f6350574d4d2b7ed8cf2498de7e4f2aee322511c1604c38330"
    ):
        raise AssertionError("historical census observation drift")
    identity = fixture["manifest_identity"]
    if set(identity) != {
        "authority_map_id", "authority_pins", "normalized_payloads", "owner_id",
        "project_id", "schema", "source_items", "structural_census", "supporting_relations",
    }:
        raise AssertionError("historical manifest identity shape drift")
    if identity["schema"] != "odysseus.harbour.planning_migration_manifest.v2" or identity["authority_map_id"] != DOCS_ONLY_AUTHORITY_MAP_ID:
        raise AssertionError("historical manifest identity map drift")
    if (
        len(identity["source_items"]) != 6
        or len(identity["normalized_payloads"]) != 6
        or len(identity["structural_census"]) != 766
        or len(identity["authority_pins"]) != 11
        or len(identity["supporting_relations"]) != 1
    ):
        raise AssertionError("historical manifest identity cardinality drift")
    digest = durable_module._migration_digest(durable_module._migration_manifest_identity(identity))
    if (
        digest != fixture["manifest_digest"]
        or fixture["batch_id"] != "planning-migration-batch:" + digest[7:]
        or provenance["accepted_manifest_digest"] != digest
        or provenance["accepted_batch_id"] != fixture["batch_id"]
        or provenance["receipt_manifest_digest"] != digest
    ):
        raise AssertionError("historical manifest fixture digest drift")
    manifest = deepcopy(identity)
    manifest["manifest_digest"] = digest
    manifest["batch_id"] = fixture["batch_id"]
    manifest["supporting_observation"] = deepcopy(observation)
    return manifest


def test_subject_bound_bundle_is_complete_immutable_and_one_verified_image(durable_planning_store, monkeypatch):
    from dataclasses import fields, FrozenInstanceError
    from src.planning_revision_store import PlanningAcceptedMigrationBundle, PlanningAcceptedHeadBinding, PlanningContextRevisionMember
    fixture = durable_planning_store
    store, principal = fixture["store"], fixture["principal"]
    owner, batch, records, heads = store.read_subject_bound_accepted_migration_records(principal, project_id="harbour-one")
    expected = {}
    for _owner, _document, reference in records:
        revision_id = reference.removeprefix("durable:")
        expected[revision_id] = (store.read_revision(principal, owner_id=owner, revision_id=revision_id), store.read_payload(principal, owner_id=owner, revision_id=revision_id))
    original_verify, original_authenticate = durable_module._verify_harbour_state, store._authenticate
    verifications, auth_outside = [], []

    def verify(image):
        verifications.append(1)
        return original_verify(image)

    def authenticate(value):
        auth_outside.append(not store._lock._is_owned())
        return original_authenticate(value)

    def forbidden(*args, **kwargs):
        pytest.fail("bundle used independent per-record reads")

    monkeypatch.setattr(durable_module, "_verify_harbour_state", verify)
    monkeypatch.setattr(store, "_authenticate", authenticate)
    for name in ("get_head", "read_revision", "read_payload", "read_subject_bound_accepted_migration_records"):
        monkeypatch.setattr(store, name, forbidden)
    bundle = store.read_subject_bound_accepted_migration_bundle(principal, project_id="harbour-one")
    assert type(bundle) is PlanningAcceptedMigrationBundle
    assert bundle.owner_id == owner and bundle.project_id == "harbour-one"
    assert len(bundle.heads) == 6 and len(bundle.revisions) == 12
    assert type(bundle.batch_bytes) is bytes and json.loads(bundle.batch_bytes) == batch
    assert bundle.batch_bytes == json.dumps(batch, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
    assert type(bundle.heads) is tuple and type(bundle.revisions) is tuple
    assert all(type(head) is PlanningAcceptedHeadBinding for head in bundle.heads)
    assert all(type(member) is PlanningContextRevisionMember and type(member.payload) is bytes for member in bundle.revisions)
    assert [{field.name: getattr(head, field.name) for field in fields(head)} for head in bundle.heads] == sorted(heads, key=lambda head: (head["project_id"], head["roadmap_id"]))
    assert len(fields(PlanningAcceptedHeadBinding)) == 13
    assert {member.revision.revision_id: (member.revision, member.payload) for member in bundle.revisions} == expected
    assert len(verifications) == 1 and auth_outside == [True]
    for value in (bundle, *bundle.heads, *bundle.revisions):
        assert repr(value) == type(value).__name__ + "(<content-free>)"
        with pytest.raises(FrozenInstanceError):
            setattr(value, fields(value)[0].name, None)
    detached = json.loads(bundle.batch_bytes)
    detached["state"] = "tampered"
    again = store.read_subject_bound_accepted_migration_bundle(principal, project_id="harbour-one")
    assert again == bundle and json.loads(again.batch_bytes)["state"] == "accepted"
    assert len(verifications) == 2


def test_subject_bound_bundle_validates_each_exact_payload_once_per_read(
    durable_planning_store, monkeypatch,
):
    fixture = durable_planning_store
    store, principal = fixture["store"], fixture["principal"]
    revision_count = len(json.loads(store._path.read_bytes())["revisions"])
    original_validate = durable_module.validate_planning_definition
    validations = []

    def counted(document):
        validations.append(durable_module._canonical_planning_bytes(document))
        return original_validate(document)

    monkeypatch.setattr(durable_module, "validate_planning_definition", counted)
    first = store.read_subject_bound_accepted_migration_bundle(
        principal, project_id="harbour-one",
    )
    assert len(validations) == revision_count == len(first.revisions)
    second = store.read_subject_bound_accepted_migration_bundle(
        principal, project_id="harbour-one",
    )
    assert second == first
    assert len(validations) == revision_count * 2


def test_dual_principal_bundle_authenticates_both_and_materializes_one_image(
    durable_planning_store, monkeypatch,
):
    fixture = durable_planning_store
    store = fixture["store"]
    cookie_principal = fixture["principal"]
    mcp_principal = fixture["issuer"].issue(
        subject_id=cookie_principal.subject_id,
        owner_id=cookie_principal.owner_id,
        auth_context_digest="sha256:" + "7" * 64,
        ttl_seconds=3600,
        nonce="dual-principal-mcp-0001",
    )
    original_authenticate = store._authenticate
    original_materialize = store._materialize_accepted_migration_locked
    authenticated = []
    materialized = []

    def authenticate(principal):
        authenticated.append(principal)
        return original_authenticate(principal)

    def materialize(**kwargs):
        materialized.append(kwargs["batch"]["batch_id"])
        return original_materialize(**kwargs)

    monkeypatch.setattr(store, "_authenticate", authenticate)
    monkeypatch.setattr(store, "_materialize_accepted_migration_locked", materialize)
    bundle = store._read_dual_subject_bound_accepted_migration_bundle(
        cookie_principal, mcp_principal, project_id="harbour-one",
    )

    assert authenticated == [cookie_principal, mcp_principal]
    assert materialized == [json.loads(bundle.batch_bytes)["batch_id"]]
    assert bundle.owner_id == cookie_principal.owner_id
    assert bundle.project_id == "harbour-one"
    with pytest.raises(DurablePlanningStoreError) as reused:
        store._read_dual_subject_bound_accepted_migration_bundle(
            cookie_principal, cookie_principal, project_id="harbour-one",
        )
    assert reused.value.code == "validation_failed"


def test_dual_principal_bundle_rejects_each_invalid_principal_slot(
    durable_planning_store, monkeypatch,
):
    from src.harbour_authority import TrustedPrincipal

    class ScalarString(str):
        pass

    fixture = durable_planning_store
    store = fixture["store"]
    cookie = fixture["principal"]
    valid_mcp = fixture["issuer"].issue(
        subject_id=cookie.subject_id,
        owner_id=cookie.owner_id,
        auth_context_digest="sha256:" + "6" * 64,
        ttl_seconds=3600,
        nonce="dual-principal-valid-mcp-0001",
    )
    foreign_issuer = AuthenticatedPrincipalIssuer(
        issuer_id="dual-principal-foreign-issuer",
        key_id="dual-principal-foreign-key",
        secret=b"dual-principal-foreign-secret-0001",
    )
    foreign = foreign_issuer.issue(
        subject_id=cookie.subject_id,
        owner_id=cookie.owner_id,
        auth_context_digest="sha256:" + "5" * 64,
        ttl_seconds=3600,
        nonce="dual-principal-foreign-0001",
    )

    def clone(**changes):
        result = object.__new__(TrustedPrincipal)
        for name in TrustedPrincipal.__slots__:
            object.__setattr__(
                result, name, changes.get(name, getattr(cookie, name)),
            )
        return result

    forged = clone(_attestation="hmac-sha256:" + "0" * 64)
    scalar_subclass = clone(_owner_id=ScalarString(cookie.owner_id))
    materialized = []
    original_materialize = store._materialize_accepted_migration_locked

    def materialize(**kwargs):
        materialized.append(kwargs["batch"]["batch_id"])
        return original_materialize(**kwargs)

    monkeypatch.setattr(store, "_materialize_accepted_migration_locked", materialize)

    cases = (
        (forged, valid_mcp),
        (foreign, valid_mcp),
        (scalar_subclass, valid_mcp),
        (cookie, forged),
        (cookie, foreign),
        (cookie, scalar_subclass),
    )
    before = store._path.read_bytes()
    for left, right in cases:
        with pytest.raises(DurablePlanningStoreError) as rejected:
            store._read_dual_subject_bound_accepted_migration_bundle(
                left, right, project_id="harbour-one",
            )
        assert rejected.value.code == "unauthenticated"
        assert store._path.read_bytes() == before
    assert materialized == []


def test_dual_principal_bundle_is_detached_and_fences_retained_image_mutation(
    durable_planning_store, monkeypatch,
):
    fixture = durable_planning_store
    store = fixture["store"]
    cookie = fixture["principal"]
    mcp = fixture["issuer"].issue(
        subject_id=cookie.subject_id,
        owner_id=cookie.owner_id,
        auth_context_digest="sha256:" + "2" * 64,
        ttl_seconds=3600,
        nonce="dual-principal-retained-image-0001",
    )
    first = store._read_dual_subject_bound_accepted_migration_bundle(
        cookie, mcp, project_id="harbour-one",
    )
    detached = json.loads(first.batch_bytes)
    detached["state"] = "tampered"
    assert json.loads(first.batch_bytes)["state"] == "accepted"

    original_assert = store._assert_retained_image_locked

    def mutate_then_assert(entry_bytes, entry_digest):
        candidate = json.loads(store._path.read_bytes())
        candidate["updated_at"] = "2099-01-01T00:00:00Z"
        store._path.write_bytes(durable_module._canonical_planning_bytes(candidate))
        return original_assert(entry_bytes, entry_digest)

    monkeypatch.setattr(store, "_assert_retained_image_locked", mutate_then_assert)
    with pytest.raises(DurablePlanningStoreError) as mutated:
        store._read_dual_subject_bound_accepted_migration_bundle(
            cookie, mcp, project_id="harbour-one",
        )
    assert mutated.value.code in {"stale_authority", "validation_failed"}


def test_pair_scope_reuses_pure_payload_validation_but_replays_both_fresh_images(
    durable_planning_store, monkeypatch,
):
    fixture = durable_planning_store
    store, principal = fixture["store"], fixture["principal"]
    revision_count = len(json.loads(store._path.read_bytes())["revisions"])
    original_validate = durable_module.validate_planning_definition
    original_verify = durable_module._verify_harbour_state
    original_materialize = store._materialize_accepted_migration_locked
    original_read = Path.read_bytes
    validations, verifications, materializations, physical_reads = [], [], [], []

    def counted_validate(document):
        validations.append(1)
        return original_validate(document)

    def counted_verify(state):
        verifications.append(1)
        return original_verify(state)

    def counted_materialize(**kwargs):
        materializations.append(1)
        return original_materialize(**kwargs)

    def counted_read(path):
        if path == store._path:
            physical_reads.append(1)
        return original_read(path)

    monkeypatch.setattr(durable_module, "validate_planning_definition", counted_validate)
    monkeypatch.setattr(durable_module, "_verify_harbour_state", counted_verify)
    monkeypatch.setattr(store, "_materialize_accepted_migration_locked", counted_materialize)
    monkeypatch.setattr(Path, "read_bytes", counted_read)
    with store._planning_payload_validation_pair():
        first = store.read_subject_bound_accepted_migration_bundle(
            principal, project_id="harbour-one",
        )
        final = store.read_subject_bound_accepted_migration_bundle(
            principal, project_id="harbour-one",
        )
    assert final == first
    assert len(validations) == revision_count
    assert len(verifications) == 2
    assert len(materializations) == 2
    assert len(physical_reads) == 4

    with store._planning_payload_validation_pair():
        repeated_first = store.read_subject_bound_accepted_migration_bundle(
            principal, project_id="harbour-one",
        )
        repeated_final = store.read_subject_bound_accepted_migration_bundle(
            principal, project_id="harbour-one",
        )
    assert repeated_first == repeated_final == first
    assert len(validations) == revision_count * 2
    assert len(verifications) == 4
    assert len(materializations) == 4
    assert len(physical_reads) == 8

    standalone = store.read_subject_bound_accepted_migration_bundle(
        principal, project_id="harbour-one",
    )
    assert standalone == first
    assert len(validations) == revision_count * 3
    assert len(verifications) == 5
    assert len(materializations) == 5
    assert len(physical_reads) == 10


def test_pair_scope_rejects_forged_foreign_nested_cross_thread_third_read_and_exited_use(
    durable_planning_store,
):
    fixture = durable_planning_store
    store, principal = fixture["store"], fixture["principal"]
    errors = []
    with store._planning_payload_validation_pair() as entered:
        assert entered is None
        scope = store._planning_validation_pair_local.scope
        with pytest.raises(DurablePlanningStoreError, match="validation_failed"):
            with store._planning_payload_validation_pair():
                pytest.fail("nested validation pair scope opened")

        def cross_thread_use():
            try:
                store._planning_validation_pair_local.scope = scope
                store.read_subject_bound_accepted_migration_bundle(
                    principal, project_id="harbour-one",
                )
            except DurablePlanningStoreError as error:
                errors.append(error.code)
            finally:
                if hasattr(store._planning_validation_pair_local, "scope"):
                    del store._planning_validation_pair_local.scope

        thread = Thread(target=cross_thread_use)
        thread.start()
        thread.join(timeout=10)
        assert not thread.is_alive() and errors == ["validation_failed"]

        foreign = DurablePlanningRevisionStore(
            store.root, trusted_issuers=(fixture["issuer"],), clock=store._clock,
        )
        foreign._planning_validation_pair_local.scope = scope
        try:
            with pytest.raises(DurablePlanningStoreError, match="validation_failed"):
                foreign.read_subject_bound_accepted_migration_bundle(
                    principal, project_id="harbour-one",
                )
        finally:
            del foreign._planning_validation_pair_local.scope
        store.read_subject_bound_accepted_migration_bundle(
            principal, project_id="harbour-one",
        )
        store.read_subject_bound_accepted_migration_bundle(
            principal, project_id="harbour-one",
        )
        with pytest.raises(DurablePlanningStoreError, match="validation_failed"):
            store.read_subject_bound_accepted_migration_bundle(
                principal, project_id="harbour-one",
            )

    assert getattr(store._planning_validation_pair_local, "scope", None) is None
    assert not hasattr(store, "_planning_validation_pair_issuer")
    for name in (
        "_PlanningPayloadValidationPair",
        "_issue_planning_payload_validation_pair",
        "_register_planning_payload_validation_pair",
        "_begin_planning_payload_validation_pair_read",
        "_finish_planning_payload_validation_pair",
        "_close_planning_payload_validation_pair",
    ):
        assert not hasattr(durable_module, name)
    forged = type(scope)()
    for stale in (scope, forged):
        store._planning_validation_pair_local.scope = stale
        try:
            with pytest.raises(DurablePlanningStoreError, match="validation_failed"):
                store.read_subject_bound_accepted_migration_bundle(
                    principal, project_id="harbour-one",
                )
        finally:
            del store._planning_validation_pair_local.scope


def test_pair_scope_clears_after_baseexception_corruption_and_dependency_replacement(
    durable_planning_store, monkeypatch,
):
    fixture = durable_planning_store
    store, principal = fixture["store"], fixture["principal"]
    with pytest.raises(RuntimeError, match="interrupt pair"):
        with store._planning_payload_validation_pair():
            abandoned = store._planning_validation_pair_local.scope
            store.read_subject_bound_accepted_migration_bundle(
                principal, project_id="harbour-one",
            )
            raise RuntimeError("interrupt pair")
    assert getattr(store._planning_validation_pair_local, "scope", None) is None
    store._planning_validation_pair_local.scope = abandoned
    try:
        with pytest.raises(DurablePlanningStoreError, match="validation_failed"):
            store.read_subject_bound_accepted_migration_bundle(
                principal, project_id="harbour-one",
            )
    finally:
        del store._planning_validation_pair_local.scope

    with pytest.raises(KeyboardInterrupt):
        with store._planning_payload_validation_pair():
            store.read_subject_bound_accepted_migration_bundle(
                principal, project_id="harbour-one",
            )
            store._planning_validation_pair_local.scope = object()
            raise KeyboardInterrupt
    assert getattr(store._planning_validation_pair_local, "scope", None) is None

    with pytest.raises(SystemExit):
        with store._planning_payload_validation_pair():
            store.read_subject_bound_accepted_migration_bundle(
                principal, project_id="harbour-one",
            )
            store.read_subject_bound_accepted_migration_bundle(
                principal, project_id="harbour-one",
            )
            raise SystemExit
    assert getattr(store._planning_validation_pair_local, "scope", None) is None

    with pytest.raises(DurablePlanningStoreError, match="validation_failed"):
        with store._planning_payload_validation_pair():
            store.read_subject_bound_accepted_migration_bundle(
                principal, project_id="harbour-one",
            )
            del store._planning_validation_pair_local.scope
            store.read_subject_bound_accepted_migration_bundle(
                principal, project_id="harbour-one",
            )
    assert getattr(store._planning_validation_pair_local, "scope", None) is None

    with pytest.raises(DurablePlanningStoreError, match="validation_failed"):
        with store._planning_payload_validation_pair():
            store.read_subject_bound_accepted_migration_bundle(
                principal, project_id="harbour-one",
            )
            store._planning_validation_pair_local.scope = object()
            store.read_subject_bound_accepted_migration_bundle(
                principal, project_id="harbour-one",
            )
    assert getattr(store._planning_validation_pair_local, "scope", None) is None

    original_validate = durable_module.validate_planning_definition
    with pytest.raises(DurablePlanningStoreError, match="validation_failed"):
        with store._planning_payload_validation_pair():
            store.read_subject_bound_accepted_migration_bundle(
                principal, project_id="harbour-one",
            )
            monkeypatch.setattr(
                durable_module, "validate_planning_definition",
                lambda document: original_validate(document),
            )
            store.read_subject_bound_accepted_migration_bundle(
                principal, project_id="harbour-one",
            )
    assert getattr(store._planning_validation_pair_local, "scope", None) is None
    assert store.available


@pytest.mark.parametrize(
    "limit_name",
    ("_PLANNING_VALIDATION_PAIR_MAX_ENTRIES", "_PLANNING_VALIDATION_PAIR_MAX_BYTES"),
)
def test_pair_scope_capacity_falls_back_to_uncached_validation(
    durable_planning_store, monkeypatch, limit_name,
):
    fixture = durable_planning_store
    store, principal = fixture["store"], fixture["principal"]
    revision_count = len(json.loads(store._path.read_bytes())["revisions"])
    original_validate = durable_module.validate_planning_definition
    validations = []

    def counted(document):
        validations.append(1)
        return original_validate(document)

    monkeypatch.setattr(durable_module, "validate_planning_definition", counted)
    monkeypatch.setattr(durable_module, limit_name, 0)
    with store._planning_payload_validation_pair():
        first = store.read_subject_bound_accepted_migration_bundle(
            principal, project_id="harbour-one",
        )
        final = store.read_subject_bound_accepted_migration_bundle(
            principal, project_id="harbour-one",
        )
    assert final == first
    assert len(validations) == revision_count * 2


def test_pair_scope_changed_exact_payload_bytes_are_revalidated(
    durable_planning_store, monkeypatch,
):
    store = durable_planning_store["store"]
    first_document = definition_fixture(PROJECT, ROADMAP, include_draft=False)
    second_document = deepcopy(first_document)
    second_roadmap = second_document["roadmaps"][0]
    second_roadmap["title"] = "Different exact immutable payload bytes"
    second_roadmap["content_hash"] = compute_roadmap_content_hash(second_roadmap)
    second_document["project"]["latest_approved_revision"] = {
        ROADMAP: {
            "revision": second_roadmap["revision"],
            "content_hash": second_roadmap["content_hash"],
        }
    }
    first_payload = durable_module._canonical_planning_bytes(first_document)
    second_payload = durable_module._canonical_planning_bytes(second_document)
    assert first_payload != second_payload
    original_validate = durable_module.validate_planning_definition
    validations = []

    def counted(document):
        validations.append(1)
        return original_validate(document)

    monkeypatch.setattr(durable_module, "validate_planning_definition", counted)
    with store._planning_payload_validation_pair():
        with store._activate_planning_payload_validation_pair_read():
            first_run = durable_module._PlanningPayloadValidationRun()
            durable_module._validated_planning_payload(
                first_payload, PROJECT, ROADMAP, _validation_run=first_run,
            )
        with store._activate_planning_payload_validation_pair_read():
            second_run = durable_module._PlanningPayloadValidationRun()
            durable_module._validated_planning_payload(
                second_payload, PROJECT, ROADMAP, _validation_run=second_run,
            )
    assert len(validations) == 2


def test_pair_scope_revalidates_canonical_contract_invalid_new_key(
    durable_planning_store, monkeypatch,
):
    store = durable_planning_store["store"]
    document = definition_fixture(PROJECT, ROADMAP, include_draft=False)
    valid_payload = durable_module._canonical_planning_bytes(document)
    invalid_document = deepcopy(document)
    del invalid_document["roadmaps"][0]["objective"]
    invalid_payload = durable_module._canonical_planning_bytes(invalid_document)
    original_validate = durable_module.validate_planning_definition
    validations = []

    def counted(candidate):
        validations.append(1)
        return original_validate(candidate)

    monkeypatch.setattr(durable_module, "validate_planning_definition", counted)
    with pytest.raises(DurablePlanningStoreError, match="validation_failed"):
        with store._planning_payload_validation_pair():
            with store._activate_planning_payload_validation_pair_read():
                first_run = durable_module._PlanningPayloadValidationRun()
                durable_module._validated_planning_payload(
                    valid_payload, PROJECT, ROADMAP, _validation_run=first_run,
                )
            with store._activate_planning_payload_validation_pair_read():
                final_run = durable_module._PlanningPayloadValidationRun()
                durable_module._validated_planning_payload(
                    invalid_payload, PROJECT, ROADMAP, _validation_run=final_run,
                )
    assert len(validations) == 2
    assert getattr(store._planning_validation_pair_local, "scope", None) is None


def test_pair_scope_hides_masters_and_detaches_both_returned_aliases(
    durable_planning_store, monkeypatch,
):
    store = durable_planning_store["store"]
    document = definition_fixture(PROJECT, ROADMAP, include_draft=False)
    payload = durable_module._canonical_planning_bytes(document)
    original_validate = durable_module.validate_planning_definition
    validations = []

    def counted(candidate):
        validations.append(1)
        return original_validate(candidate)

    monkeypatch.setattr(durable_module, "validate_planning_definition", counted)
    with store._planning_payload_validation_pair():
        with store._activate_planning_payload_validation_pair_read():
            first_run = durable_module._PlanningPayloadValidationRun()
            _raw, first, first_roadmap = durable_module._validated_planning_payload(
                payload, PROJECT, ROADMAP, _validation_run=first_run,
            )
        scope = store._planning_validation_pair_local.scope
        assert not hasattr(scope, "_memo")
        for name, value in (("_memo", {}), ("_dependencies", ()), ("_store", store)):
            with pytest.raises(AttributeError):
                setattr(scope, name, value)
        first["project"]["project_id"] = "mutated-returned-document"
        first_roadmap["title"] = "mutated-returned-nested-roadmap"
        with store._activate_planning_payload_validation_pair_read():
            final_run = durable_module._PlanningPayloadValidationRun()
            _raw, final, final_roadmap = durable_module._validated_planning_payload(
                payload, PROJECT, ROADMAP, _validation_run=final_run,
            )
    assert final == document
    assert final_roadmap == document["roadmaps"][0]
    assert len(validations) == 1
    assert getattr(store._planning_validation_pair_local, "scope", None) is None


def test_pair_scope_dependency_identity_rejects_eq_hash_and_reentry_spoof(
    durable_planning_store, monkeypatch,
):
    fixture = durable_planning_store
    store, principal = fixture["store"], fixture["principal"]
    calls = {"eq": 0, "hash": 0, "call": 0}

    class MaliciousCallable:
        def __eq__(self, other):
            calls["eq"] += 1
            store.read_subject_bound_accepted_migration_bundle(
                principal, project_id="harbour-one",
            )
            return True

        def __hash__(self):
            calls["hash"] += 1
            return 0

        def __call__(self, document):
            calls["call"] += 1
            store.read_subject_bound_accepted_migration_bundle(
                principal, project_id="harbour-one",
            )

    with pytest.raises(DurablePlanningStoreError, match="validation_failed"):
        with store._planning_payload_validation_pair():
            store.read_subject_bound_accepted_migration_bundle(
                principal, project_id="harbour-one",
            )
            monkeypatch.setattr(
                durable_module, "validate_planning_definition", MaliciousCallable(),
            )
            store.read_subject_bound_accepted_migration_bundle(
                principal, project_id="harbour-one",
            )
    assert calls == {"eq": 0, "hash": 0, "call": 0}
    assert getattr(store._planning_validation_pair_local, "scope", None) is None


def test_pair_scope_revalidates_same_id_changed_physical_payload(
    durable_planning_store, monkeypatch,
):
    fixture = durable_planning_store
    store, principal = fixture["store"], fixture["principal"]
    original_read = Path.read_bytes
    original_bytes = original_read(store._path)
    changed_state = json.loads(original_bytes)
    revision_id = next(iter(changed_state["revisions"]))
    record = changed_state["revisions"][revision_id]
    document = json.loads(durable_module.base64.b64decode(record["payload_b64"]))
    roadmap = document["roadmaps"][0]
    roadmap["title"] = "Same revision identity with different payload bytes"
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    latest = document["project"]["latest_approved_revision"].get(roadmap["roadmap_id"])
    if latest is not None and latest["revision"] == roadmap["revision"]:
        latest["content_hash"] = roadmap["content_hash"]
    changed_payload = durable_module._canonical_planning_bytes(document)
    record["payload_b64"] = durable_module.base64.b64encode(changed_payload).decode("ascii")
    record["payload_sha256"] = durable_module._bytes_digest(changed_payload)
    changed_bytes = (
        json.dumps(changed_state, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    original_validate = durable_module.validate_planning_definition
    validations, reads = [], []

    def counted(document):
        validations.append(1)
        return original_validate(document)

    def changed_second_entry(path):
        if path != store._path:
            return original_read(path)
        reads.append(1)
        return changed_bytes if len(reads) == 3 else original_bytes

    monkeypatch.setattr(durable_module, "validate_planning_definition", counted)
    monkeypatch.setattr(Path, "read_bytes", changed_second_entry)
    with pytest.raises(DurablePlanningStoreError, match="validation_failed"):
        with store._planning_payload_validation_pair():
            store.read_subject_bound_accepted_migration_bundle(
                principal, project_id="harbour-one",
            )
            first_count = len(validations)
            store.read_subject_bound_accepted_migration_bundle(
                principal, project_id="harbour-one",
            )
    assert len(reads) == 3
    assert len(validations) > first_count
    assert revision_id in changed_state["revisions"]


def test_pair_scope_public_pair_rejects_physical_aba_image(
    durable_planning_store, monkeypatch,
):
    fixture = durable_planning_store
    store, principal = fixture["store"], fixture["principal"]
    original_read = Path.read_bytes
    original_bytes = original_read(store._path)
    equivalent_bytes = b" " + original_bytes
    reads = []

    def aba_read(path):
        if path != store._path:
            return original_read(path)
        reads.append(1)
        return equivalent_bytes if len(reads) == 3 else original_bytes

    monkeypatch.setattr(Path, "read_bytes", aba_read)
    with pytest.raises(DurablePlanningStoreError, match="stale_authority"):
        with store._planning_payload_validation_pair():
            store.read_subject_bound_accepted_migration_bundle(
                principal, project_id="harbour-one",
            )
            store.read_subject_bound_accepted_migration_bundle(
                principal, project_id="harbour-one",
            )
    assert len(reads) == 4
    assert equivalent_bytes != original_bytes
    assert getattr(store._planning_validation_pair_local, "scope", None) is None


@pytest.mark.parametrize("changed", ("head", "receipt", "parent", "owner"))
def test_pair_scope_replays_nonpayload_authority_on_second_image(
    durable_planning_store, monkeypatch, changed,
):
    fixture = durable_planning_store
    store, principal = fixture["store"], fixture["principal"]
    original_verify = durable_module._verify_harbour_state
    verifications = []

    def changed_second_image(state):
        verifications.append(1)
        if len(verifications) == 1:
            return original_verify(state)
        candidate = deepcopy(state)
        if changed == "head":
            next(iter(candidate["heads"].values()))["generation"] += 1
        elif changed == "receipt":
            next(iter(candidate["receipts"].values()))["producer"] = "forged-producer"
        elif changed == "parent":
            record = next(
                value for value in candidate["revisions"].values()
                if value["parent_revision_id"] is not None
            )
            record["parent_revision_id"] = "forged-parent"
        else:
            next(iter(candidate["revisions"].values()))["owner_id"] = "forged-owner"
        return original_verify(candidate)

    monkeypatch.setattr(durable_module, "_verify_harbour_state", changed_second_image)
    with pytest.raises(DurablePlanningStoreError, match="validation_failed"):
        with store._planning_payload_validation_pair():
            store.read_subject_bound_accepted_migration_bundle(
                principal, project_id="harbour-one",
            )
            store.read_subject_bound_accepted_migration_bundle(
                principal, project_id="harbour-one",
            )
    assert len(verifications) == 2
    assert getattr(store._planning_validation_pair_local, "scope", None) is None


@pytest.mark.parametrize("changed", ("disappeared", "truncated", "same-length"))
def test_pair_scope_second_physical_read_failure_has_no_partial_result(
    durable_planning_store, monkeypatch, changed,
):
    fixture = durable_planning_store
    store, principal = fixture["store"], fixture["principal"]
    original_read = Path.read_bytes
    state_bytes = original_read(store._path)
    reads = []

    def changed_second_bundle(path):
        if path != store._path:
            return original_read(path)
        reads.append(1)
        if len(reads) == 3:
            if changed == "disappeared":
                raise FileNotFoundError()
            if changed == "truncated":
                return state_bytes.rstrip()[:-1]
            return bytes([state_bytes[0] ^ 1]) + state_bytes[1:]
        return state_bytes

    monkeypatch.setattr(Path, "read_bytes", changed_second_bundle)
    with pytest.raises(DurablePlanningStoreError, match="validation_failed"):
        with store._planning_payload_validation_pair():
            store.read_subject_bound_accepted_migration_bundle(
                principal, project_id="harbour-one",
            )
            store.read_subject_bound_accepted_migration_bundle(
                principal, project_id="harbour-one",
            )
    assert len(reads) == 3
    assert getattr(store._planning_validation_pair_local, "scope", None) is None


def test_validation_run_keys_exact_bytes_and_bindings_and_detaches_private_master(monkeypatch):
    document = definition_fixture(PROJECT, ROADMAP, include_draft=False)
    payload = durable_module._canonical_planning_bytes(document)
    noncanonical = json.dumps(document, ensure_ascii=False, indent=1).encode("utf-8")
    original_validate = durable_module.validate_planning_definition
    validations = []

    def counted(value):
        validations.append(1)
        return original_validate(value)

    monkeypatch.setattr(durable_module, "validate_planning_definition", counted)
    run = durable_module._PlanningPayloadValidationRun()
    _raw, first, first_roadmap = durable_module._validated_planning_payload(
        payload, PROJECT, ROADMAP, _validation_run=run,
    )
    first["project"]["project_id"] = "mutated-detached-result"
    first_roadmap["title"] = "mutated-detached-roadmap"
    _raw, second, second_roadmap = durable_module._validated_planning_payload(
        payload, PROJECT, ROADMAP, _validation_run=run,
    )
    assert second == document and second_roadmap == document["roadmaps"][0]
    assert len(validations) == 1

    durable_module._validated_planning_payload(
        noncanonical, PROJECT, ROADMAP, _validation_run=run,
    )
    assert len(validations) == 2
    for project_id, roadmap_id in (("other-project", ROADMAP), (PROJECT, "other-roadmap")):
        with pytest.raises(DurablePlanningStoreError) as denied:
            durable_module._validated_planning_payload(
                payload, project_id, roadmap_id, _validation_run=run,
            )
        assert denied.value.code == "validation_failed"
    assert len(validations) == 4

    assert not hasattr(run, "_memo")
    for name, value in (("_memo", {}), ("_dependencies", ()), ("_sealed", False)):
        with pytest.raises(AttributeError):
            setattr(run, name, value)


def test_validation_run_rejects_unsealed_wrong_image_reuse_and_dependency_change(monkeypatch):
    document = definition_fixture(PROJECT, ROADMAP, include_draft=False)
    payload = durable_module._canonical_planning_bytes(document)
    digest = "sha256:" + hashlib.sha256(b"retained-image").hexdigest()
    unsealed = durable_module._PlanningPayloadValidationRun()
    capture = durable_module._PlanningPayloadValidationCapture()
    with pytest.raises(DurablePlanningStoreError):
        durable_module._validated_planning_payload(
            payload, PROJECT, ROADMAP, _validation_run=object(),
        )
    with pytest.raises(DurablePlanningStoreError):
        capture.capture(unsealed, b"retained-image", digest)

    run = durable_module._PlanningPayloadValidationRun()
    durable_module._validated_planning_payload(
        payload, PROJECT, ROADMAP, _validation_run=run,
    )
    run.seal()
    capture.capture(run, b"retained-image", digest)
    with pytest.raises(DurablePlanningStoreError):
        capture.take(b"different-image", digest)
    assert capture.take(b"retained-image", digest) is run
    run.begin_materialization(b"retained-image", digest)
    with pytest.raises(DurablePlanningStoreError):
        run.begin_materialization(b"retained-image", digest)

    dependency_run = durable_module._PlanningPayloadValidationRun()
    monkeypatch.setattr(
        durable_module, "_canonical_planning_bytes", lambda _value: b"changed-dependency",
    )
    with pytest.raises(DurablePlanningStoreError) as changed:
        durable_module._validated_planning_payload(
            payload, PROJECT, ROADMAP, _validation_run=dependency_run,
        )
    assert changed.value.code == "validation_failed"


def _pretty_planning_payload(payload: bytes) -> bytes:
    document = json.loads(payload)
    pretty = json.dumps(
        document, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True,
    ).encode("utf-8")
    assert durable_module._canonical_planning_bytes(document) == payload
    assert pretty != payload
    return pretty


def test_valid_noncanonical_ordinary_revision_reopens_and_reads_exact_bytes(tmp_path: Path):
    store, issuer, principal, root = _durable_authority(tmp_path)
    created = _durable_create(store, principal, _durable_payload(1, "Canonical genesis"))
    pretty = _pretty_planning_payload(_durable_payload(2, "Pretty ordinary append"))
    appended = store.append_revision(
        principal,
        owner_id=OWNER,
        project_id=PROJECT,
        roadmap_id=ROADMAP,
        payload_bytes=pretty,
        expected_head=created.after_head,
        idempotency_key="pretty-ordinary-append-0001",
    )
    assert store.read_payload(
        principal, owner_id=OWNER, revision_id=appended.new_revision_id,
    ) == pretty

    reopened = DurablePlanningRevisionStore(
        root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW,
    )
    assert reopened.available
    assert reopened.read_payload(
        principal, owner_id=OWNER, revision_id=appended.new_revision_id,
    ) == pretty


def test_unrelated_noncanonical_revision_does_not_block_canonical_accepted_bundle(
    durable_planning_store,
):
    fixture = durable_planning_store
    store, principal = fixture["store"], fixture["principal"]
    project_id, roadmap_id = "unrelated-project", "unrelated-roadmap"
    document = definition_fixture(project_id, roadmap_id, include_draft=False)
    canonical = durable_module._canonical_planning_bytes(document)
    pretty = _pretty_planning_payload(canonical)
    store.create_project(
        principal,
        owner_id="fuzzy",
        project_id=project_id,
        roadmap_id=roadmap_id,
        payload_bytes=pretty,
        idempotency_key="unrelated-pretty-create-0001",
    )

    bundle = store.read_subject_bound_accepted_migration_bundle(
        principal, project_id="harbour-one",
    )
    assert bundle.project_id == "harbour-one"
    assert all(member.revision.project_id == "harbour-one" for member in bundle.revisions)


def test_selected_noncanonical_revision_is_denied_by_bundle_materialization(
    durable_planning_store,
):
    fixture = durable_planning_store
    store, principal = fixture["store"], fixture["principal"]
    _owner, _batch, records, heads = store.read_subject_bound_accepted_migration_records(
        principal, project_id="harbour-one",
    )
    binding = heads[0]
    current = store.get_head(
        principal,
        owner_id="fuzzy",
        project_id="harbour-one",
        roadmap_id=binding["roadmap_id"],
    )
    document = deepcopy(next(
        value for _owner, value, reference in records
        if reference == "durable:" + current.current_revision_id
    ))
    roadmap = document["roadmaps"][0]
    roadmap["revision"] += 1
    roadmap["title"] = "Selected pretty ordinary append"
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    document["project"]["latest_approved_revision"] = {
        roadmap["roadmap_id"]: {
            "revision": roadmap["revision"],
            "content_hash": roadmap["content_hash"],
        }
    }
    document["project"]["draft_refs"] = []
    canonical = durable_module._canonical_planning_bytes(document)
    pretty = _pretty_planning_payload(canonical)
    store.append_revision(
        principal,
        owner_id="fuzzy",
        project_id="harbour-one",
        roadmap_id=roadmap["roadmap_id"],
        payload_bytes=pretty,
        expected_head=current,
        idempotency_key="selected-pretty-append-0001",
    )

    with pytest.raises(DurablePlanningStoreError) as denied:
        store.read_subject_bound_accepted_migration_bundle(
            principal, project_id="harbour-one",
        )
    assert denied.value.code == "validation_failed"


def test_validation_run_revalidates_noncanonical_and_reuses_only_eligible_payloads(
    monkeypatch,
):
    canonical = _durable_payload(1, "Canonical memo candidate")
    noncanonical = _pretty_planning_payload(canonical)
    novel_canonical = _durable_payload(1, "Sealed canonical miss")
    original_validate = durable_module.validate_planning_definition
    validations = []

    def counted(document):
        validations.append(1)
        return original_validate(document)

    monkeypatch.setattr(durable_module, "validate_planning_definition", counted)
    run = durable_module._PlanningPayloadValidationRun()
    durable_module._validated_planning_payload(
        canonical, PROJECT, ROADMAP, _validation_run=run,
    )
    durable_module._validated_planning_payload(
        canonical, PROJECT, ROADMAP, _validation_run=run,
    )
    durable_module._validated_planning_payload(
        noncanonical, PROJECT, ROADMAP, _validation_run=run,
    )
    durable_module._validated_planning_payload(
        noncanonical, PROJECT, ROADMAP, _validation_run=run,
    )
    assert len(validations) == 3
    assert not hasattr(run, "_memo")

    run.seal()
    durable_module._validated_planning_payload(
        noncanonical, PROJECT, ROADMAP, _validation_run=run,
    )
    durable_module._validated_planning_payload(
        canonical, PROJECT, ROADMAP, _validation_run=run,
    )
    with pytest.raises(DurablePlanningStoreError) as sealed_miss:
        durable_module._validated_planning_payload(
            novel_canonical, PROJECT, ROADMAP, _validation_run=run,
        )
    assert sealed_miss.value.code == "validation_failed"
    assert len(validations) == 5


@pytest.mark.parametrize("change", ("replace", "delete", "terminal"))
def test_subject_bound_bundle_final_fence_returns_no_partial_result(durable_planning_store, monkeypatch, change):
    fixture = durable_planning_store
    store, principal = fixture["store"], fixture["principal"]
    original_read, reads = Path.read_bytes, []

    def controlled_read(path):
        if path != store._path:
            return original_read(path)
        reads.append(1)
        if len(reads) == 2:
            if change == "replace":
                return original_read(path) + b"\n"
            if change == "delete":
                raise FileNotFoundError()
            store._runtime.publish("validation_failed")
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", controlled_read)
    with pytest.raises(DurablePlanningStoreError) as error:
        store.read_subject_bound_accepted_migration_bundle(principal, project_id="harbour-one")
    assert error.value.code in {"stale_authority", "validation_failed"}
    assert error.value.__cause__ is None and error.value.__context__ is None
    assert len(reads) == 2


def test_subject_bound_bundle_rejects_mutable_malformed_or_forged_members(durable_planning_store):
    from dataclasses import replace
    from src.planning_revision_store import PlanningContextRevisionMember
    fixture = durable_planning_store
    bundle = fixture["store"].read_subject_bound_accepted_migration_bundle(fixture["principal"], project_id="harbour-one")
    member, head = bundle.revisions[0], bundle.heads[0]

    class DerivedMember(PlanningContextRevisionMember):
        pass

    invalid = (
        lambda: replace(member, payload=bytearray(member.payload)),
        lambda: replace(member, payload=member.payload + b"\n"),
        lambda: replace(head, generation=True),
        lambda: replace(bundle, heads=list(bundle.heads)),
        lambda: replace(bundle, revisions=bundle.revisions + (member,)),
        lambda: replace(bundle, revisions=(DerivedMember(member.revision, member.payload), *bundle.revisions[1:])),
        lambda: replace(bundle, state_digest="invalid"),
        lambda: replace(bundle, batch_bytes=b"{}"),
    )
    for operation in invalid:
        with pytest.raises((ValueError, TypeError)):
            operation()


def test_subject_bound_bundle_retains_fence_until_official_writer_then_observes_generation(durable_planning_store, monkeypatch):
    fixture = durable_planning_store
    store, principal = fixture["store"], fixture["principal"]
    _owner, _batch, records, heads = store.read_subject_bound_accepted_migration_records(principal, project_id="harbour-one")
    binding = heads[0]
    current = store.get_head(principal, owner_id="fuzzy", project_id="harbour-one", roadmap_id=binding["roadmap_id"])
    document = deepcopy(next(document for _owner, document, reference in records if reference == "durable:" + current.current_revision_id))
    roadmap = document["roadmaps"][0]
    roadmap["revision"] += 1
    roadmap["title"] = "Concurrent verified Planning append"
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    document["project"]["latest_approved_revision"] = {roadmap["roadmap_id"]: {"revision": roadmap["revision"], "content_hash": roadmap["content_hash"]}}
    document["project"]["draft_refs"] = []
    payload = json.dumps(document, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
    attempted, completed, errors = Event(), Event(), []
    original_lock = store._lock

    class LockWitness:
        def __enter__(self):
            if __import__("threading").current_thread() is worker:
                attempted.set()
            original_lock.acquire()
            return self

        def __exit__(self, *args):
            original_lock.release()

    def write():
        try:
            store.append_revision(principal, owner_id="fuzzy", project_id="harbour-one", roadmap_id=roadmap["roadmap_id"], payload_bytes=payload, expected_head=current, idempotency_key="bundle-concurrent-append")
        except Exception as error:
            errors.append(type(error).__name__)
        finally:
            completed.set()

    worker = Thread(target=write, daemon=True)
    monkeypatch.setattr(store, "_lock", LockWitness())
    original_read, reads = Path.read_bytes, []

    def controlled_read(path):
        if path == store._path:
            reads.append(1)
            if len(reads) == 2:
                worker.start()
                assert attempted.wait(5)
                assert not completed.is_set()
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", controlled_read)
    first = store.read_subject_bound_accepted_migration_bundle(principal, project_id="harbour-one")
    worker.join(15)
    assert completed.is_set() and not worker.is_alive() and errors == []
    second = store.read_subject_bound_accepted_migration_bundle(principal, project_id="harbour-one")
    first_head = next(head for head in first.heads if head.roadmap_id == roadmap["roadmap_id"])
    second_head = next(head for head in second.heads if head.roadmap_id == roadmap["roadmap_id"])
    assert second_head.generation > first_head.generation
    assert second_head.current_revision == first_head.current_revision + 1
    assert len(second.revisions) == len(first.revisions) + 1


OWNER = "alice"
PROJECT = "project-a"
ROADMAP = "roadmap-a"


def _repository(tmp_path: Path) -> tuple[PlanningRevisionRepository, Path, dict]:
    root = tmp_path / "temporary-planning-repository"
    root.mkdir()
    (root / TEMPORARY_REPOSITORY_MARKER).write_text(
        TEMPORARY_REPOSITORY_SCHEMA + "\n",
        encoding="utf-8",
    )
    document = definition_fixture(PROJECT, ROADMAP, include_draft=False)
    source = root / "definition.json"
    source.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    repository = PlanningRevisionRepository(
        root,
        owner=OWNER,
        cursor_secret=b"0123456789abcdef0123456789abcdef",
    )
    return repository, source, document


def _base(document: dict) -> tuple[int, str]:
    roadmap = document["roadmaps"][0]
    return roadmap["revision"], roadmap["content_hash"]


def _create(
    repository: PlanningRevisionRepository,
    document: dict,
    *,
    key: str = "create-key-0001",
    changes: dict | None = None,
) -> dict:
    revision, content_hash = _base(document)
    return repository.create_draft(
        OWNER,
        PROJECT,
        ROADMAP,
        base_revision=revision,
        base_hash=content_hash,
        idempotency_key=key,
        changes=changes
        or {"operation": "update", "set": {"title": "Accepted title"}},
    )


def _validate(repository: PlanningRevisionRepository, draft: dict) -> dict:
    return repository.validate_draft(
        OWNER,
        PROJECT,
        ROADMAP,
        draft["draft_id"],
        expected_draft_version=draft["draft_version"],
    )


def _accept(
    repository: PlanningRevisionRepository,
    draft: dict,
    validation: dict,
    *,
    key: str = "accept-key-0001",
) -> dict:
    return repository.act_on_draft(
        OWNER,
        PROJECT,
        ROADMAP,
        draft["draft_id"],
        action="accept",
        expected_draft_version=validation["draft_version"],
        idempotency_key=key,
    )


def _read(source: Path) -> dict:
    return json.loads(source.read_text(encoding="utf-8"))


def test_repository_requires_exact_temporary_marker_and_root_file(tmp_path: Path) -> None:
    root = tmp_path / "not-authorized"
    root.mkdir()
    (root / "definition.json").write_text("{}", encoding="utf-8")

    with pytest.raises(PlanningRevisionRepositoryError) as missing_marker:
        PlanningRevisionRepository(root, owner=OWNER)
    assert missing_marker.value.code == "planning_write_gate_required"

    (root / TEMPORARY_REPOSITORY_MARKER).write_text("wrong", encoding="utf-8")
    with pytest.raises(PlanningRevisionRepositoryError) as wrong_marker:
        PlanningRevisionRepository(root, owner=OWNER)
    assert wrong_marker.value.code == "planning_write_gate_required"

    (root / TEMPORARY_REPOSITORY_MARKER).write_text(
        TEMPORARY_REPOSITORY_SCHEMA,
        encoding="utf-8",
    )
    with pytest.raises(PlanningRevisionRepositoryError) as traversal:
        PlanningRevisionRepository(root, owner=OWNER, definition_file="../definition.json")
    assert traversal.value.code == "invalid_definition_file"


def test_create_validate_accept_readback_and_undo_restore_exact_previous_hash(
    tmp_path: Path,
) -> None:
    repository, source, initial = _repository(tmp_path)
    initial_bytes = source.read_bytes()
    initial_hash = initial["roadmaps"][0]["content_hash"]

    draft = _create(repository, initial)
    validation = _validate(repository, draft)
    assert source.read_bytes() == initial_bytes

    accepted = _accept(repository, draft, validation)
    applied = _read(source)
    validate_planning_definition(applied)
    latest = applied["project"]["latest_approved_revision"][ROADMAP]
    accepted_roadmap = next(
        item for item in applied["roadmaps"] if item["revision"] == accepted["accepted_revision"]
    )

    assert accepted["readback_verified"] is True
    assert accepted["accepted_revision"] == 2
    assert accepted["accepted_hash"] == latest["content_hash"]
    assert accepted_roadmap["title"] == "Accepted title"
    assert accepted_roadmap["content_hash"] == accepted["accepted_hash"]

    undone = repository.undo_apply(
        OWNER,
        accepted["undo_id"],
        idempotency_key="undo-key-0001",
    )
    restored = _read(source)
    retained = next(
        item for item in restored["roadmaps"] if item["revision"] == accepted["accepted_revision"]
    )

    validate_planning_definition(restored)
    assert undone["restored_hash"] == initial_hash
    assert restored["project"]["latest_approved_revision"][ROADMAP]["content_hash"] == initial_hash
    assert retained["content_hash"] == accepted["accepted_hash"]
    assert undone["accepted_hash_retained"] == accepted["accepted_hash"]


def test_create_action_and_undo_idempotency_return_original_receipt_across_restart(
    tmp_path: Path,
) -> None:
    repository, _source, initial = _repository(tmp_path)
    draft = _create(repository, initial)
    duplicate_draft = _create(repository, initial)
    validation = _validate(repository, draft)
    accepted = _accept(repository, draft, validation)
    duplicate_accept = _accept(repository, draft, validation)

    restarted = PlanningRevisionRepository(
        repository.root,
        owner=OWNER,
        cursor_secret=b"0123456789abcdef0123456789abcdef",
    )
    duplicate_after_restart = _accept(restarted, draft, validation)
    undone = restarted.undo_apply(
        OWNER,
        accepted["undo_id"],
        idempotency_key="undo-key-0002",
    )
    duplicate_undo = restarted.undo_apply(
        OWNER,
        accepted["undo_id"],
        idempotency_key="undo-key-0002",
    )

    assert duplicate_draft == draft
    assert duplicate_accept == accepted
    assert duplicate_after_restart == accepted
    assert duplicate_undo == undone


def test_reused_idempotency_key_with_different_request_is_rejected(tmp_path: Path) -> None:
    repository, _source, initial = _repository(tmp_path)
    _create(repository, initial, key="same-key-0001")

    with pytest.raises(PlanningRevisionRepositoryError) as raised:
        _create(
            repository,
            initial,
            key="same-key-0001",
            changes={"operation": "update", "set": {"title": "Different"}},
        )

    assert raised.value.code == "idempotency_conflict"


def test_failed_validation_leaves_definition_source_byte_identical(tmp_path: Path) -> None:
    repository, source, initial = _repository(tmp_path)
    before = source.read_bytes()
    invalid_nodes = deepcopy(initial["roadmaps"][0]["nodes"])
    invalid_nodes[0]["run_id"] = "runtime-leak"
    draft = _create(
        repository,
        initial,
        changes={"operation": "update", "set": {"nodes": invalid_nodes}},
    )

    with pytest.raises(PlanningRevisionRepositoryError) as raised:
        _validate(repository, draft)

    assert raised.value.code == "runtime_field_forbidden"
    assert source.read_bytes() == before


def test_concurrent_drafts_conflict_after_first_atomic_accept(tmp_path: Path) -> None:
    repository, source, initial = _repository(tmp_path)
    first = _create(repository, initial, key="create-first-0001")
    second = _create(
        repository,
        initial,
        key="create-second-0001",
        changes={"operation": "update", "set": {"title": "Second title"}},
    )
    first_validation = _validate(repository, first)
    second_validation = _validate(repository, second)
    second_instance = PlanningRevisionRepository(
        repository.root,
        owner=OWNER,
        cursor_secret=b"0123456789abcdef0123456789abcdef",
    )

    def attempt(selected_repository, draft, validation, key):
        try:
            return _accept(selected_repository, draft, validation, key=key)
        except PlanningRevisionRepositoryError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda args: attempt(*args),
                (
                    (repository, first, first_validation, "accept-first-0001"),
                    (second_instance, second, second_validation, "accept-second-0001"),
                ),
            )
        )

    accepted = [item for item in results if isinstance(item, dict)]
    rejected = [item for item in results if isinstance(item, PlanningRevisionRepositoryError)]
    assert len(accepted) == 1
    assert len(rejected) == 1
    assert rejected[0].code == "base_revision_conflict"
    assert _read(source)["project"]["latest_approved_revision"][ROADMAP]["content_hash"] == accepted[0]["accepted_hash"]


def test_draft_version_conflict_and_discard_never_mutate_source(tmp_path: Path) -> None:
    repository, source, initial = _repository(tmp_path)
    before = source.read_bytes()
    draft = _create(repository, initial)

    with pytest.raises(PlanningRevisionRepositoryError) as wrong_version:
        repository.validate_draft(
            OWNER,
            PROJECT,
            ROADMAP,
            draft["draft_id"],
            expected_draft_version=99,
        )
    assert wrong_version.value.code == "draft_version_conflict"

    discarded = repository.act_on_draft(
        OWNER,
        PROJECT,
        ROADMAP,
        draft["draft_id"],
        action="discard",
        expected_draft_version=1,
        idempotency_key="discard-key-0001",
    )
    duplicate = repository.act_on_draft(
        OWNER,
        PROJECT,
        ROADMAP,
        draft["draft_id"],
        action="discard",
        expected_draft_version=1,
        idempotency_key="discard-key-0001",
    )

    assert discarded == duplicate
    assert discarded["source_mutated"] is False
    assert source.read_bytes() == before


def test_tombstone_then_restore_creates_immutable_definition_revisions(tmp_path: Path) -> None:
    repository, source, initial = _repository(tmp_path)
    tombstone = _create(
        repository,
        initial,
        key="create-tombstone-0001",
        changes={"operation": "tombstone", "set": {}},
    )
    tombstone_validation = _validate(repository, tombstone)
    tombstone_receipt = _accept(
        repository,
        tombstone,
        tombstone_validation,
        key="accept-tombstone-0001",
    )
    tombstoned = _read(source)
    assert ROADMAP not in tombstoned["project"]["latest_approved_revision"]
    tombstone_revision = next(
        item for item in tombstoned["roadmaps"] if item["revision"] == 2
    )
    assert tombstone_revision["revision_state"] == "tombstoned"

    restore = repository.create_draft(
        OWNER,
        PROJECT,
        ROADMAP,
        base_revision=tombstone_receipt["accepted_revision"],
        base_hash=tombstone_receipt["accepted_hash"],
        idempotency_key="create-restore-0001",
        changes={"operation": "restore", "set": {}, "restore_revision": 1},
    )
    restore_validation = _validate(repository, restore)
    restored_receipt = _accept(
        repository,
        restore,
        restore_validation,
        key="accept-restore-0001",
    )
    restored = _read(source)

    validate_planning_definition(restored)
    assert restored_receipt["accepted_revision"] == 3
    assert restored["project"]["latest_approved_revision"][ROADMAP]["revision"] == 3
    assert next(item for item in restored["roadmaps"] if item["revision"] == 2)["content_hash"] == tombstone_receipt["accepted_hash"]
    assert next(item for item in restored["roadmaps"] if item["revision"] == 3)["revision_state"] == "approved"


def test_state_persistence_failure_rolls_back_atomic_source_apply(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository, source, initial = _repository(tmp_path)
    draft = _create(repository, initial)
    validation = _validate(repository, draft)
    before = source.read_bytes()

    def fail_state(_state):
        raise OSError("simulated state persistence failure")

    monkeypatch.setattr(repository, "_save_state", fail_state)
    with pytest.raises(OSError):
        _accept(repository, draft, validation)

    assert source.read_bytes() == before
    validate_planning_definition(_read(source))


def test_owner_scope_is_indistinguishable_from_missing_project(tmp_path: Path) -> None:
    repository, _source, initial = _repository(tmp_path)
    revision, content_hash = _base(initial)

    with pytest.raises(PlanningRevisionRepositoryError) as raised:
        repository.create_draft(
            "bob",
            PROJECT,
            ROADMAP,
            base_revision=revision,
            base_hash=content_hash,
            idempotency_key="owner-scope-0001",
            changes={"operation": "update", "set": {"title": "No access"}},
        )

    assert raised.value.code == "project_not_found"


@pytest.mark.parametrize(
    "changes",
    [
        {"operation": "update", "set": {}},
        {"operation": "tombstone", "set": {"title": "not allowed"}},
        {"operation": "restore", "set": {}, "restore_revision": 0},
        {"operation": "update", "set": {"revision": 2}},
        {"operation": "run", "set": {}},
    ],
)
def test_structural_diff_contract_is_bounded_and_closed(tmp_path: Path, changes: dict) -> None:
    repository, _source, initial = _repository(tmp_path)

    with pytest.raises(PlanningRevisionRepositoryError):
        _create(repository, initial, changes=changes)


AUTH_NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
AUTH_CONTEXT = "sha256:" + "2" * 64


def _durable_authority(tmp_path: Path):
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="odysseus-auth",
        key_id="planning-key-1",
        secret=b"durable-planning-test-secret-0001",
        clock=lambda: AUTH_NOW,
    )
    principal = issuer.issue(
        subject_id="alice-user",
        owner_id=OWNER,
        auth_context_digest=AUTH_CONTEXT,
        ttl_seconds=3600,
        nonce="planning-nonce-0001",
    )
    root = tmp_path / "durable-planning"
    store = DurablePlanningRevisionStore(
        root,
        trusted_issuers=(issuer,),
        clock=lambda: AUTH_NOW,
    )
    return store, issuer, principal, root


def _durable_payload(revision: int, title: str, *, state: str = "approved") -> bytes:
    document = definition_fixture(PROJECT, ROADMAP, include_draft=False)
    roadmap = document["roadmaps"][0]
    roadmap["revision"] = revision
    roadmap["revision_state"] = state
    roadmap["title"] = title
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    document["project"]["latest_approved_revision"] = (
        {
            ROADMAP: {
                "revision": revision,
                "content_hash": roadmap["content_hash"],
            }
        }
        if state == "approved"
        else {}
    )
    document["project"]["draft_refs"] = []
    validate_planning_definition(document)
    return json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _durable_payload_exact_size(revision: int, title: str, size: int) -> bytes:
    document = json.loads(_durable_payload(revision, title).decode("utf-8"))
    roadmap = document["roadmaps"][0]
    roadmap["constraints"] = []

    def encode() -> bytes:
        roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
        document["project"]["latest_approved_revision"][ROADMAP]["content_hash"] = roadmap["content_hash"]
        return json.dumps(
            document, ensure_ascii=False, allow_nan=False,
            separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")

    baseline = encode()
    deficit = size - len(baseline)
    assert deficit > 0
    item_count = (deficit + 4003) // 4003
    total_text_bytes = deficit - 3 * item_count + 1
    width, remainder = divmod(total_text_bytes, item_count)
    constraints = []
    for index in range(item_count):
        item_length = width + (1 if index < remainder else 0)
        prefix = f"store-over-old-cap-{index:05d}-"
        assert len(prefix) <= item_length <= 4000
        constraints.append(prefix + "x" * (item_length - len(prefix)))
    roadmap["constraints"] = constraints
    payload = encode()
    assert len(payload) == size
    validate_planning_definition(document)
    return payload


def test_durable_documents_over_old_cap_preserve_atomic_replay_and_rejection_state(tmp_path):
    store, issuer, principal, root = _durable_authority(tmp_path)
    size = 4 * 1024 * 1024 + 128 * 1024
    created_payload = _durable_payload_exact_size(1, "Large durable create", size)
    created = _durable_create(
        store, principal, created_payload, key="durable-over-old-cap-create-0001",
    )
    assert store.read_payload(
        principal, owner_id=OWNER, revision_id=created.new_revision_id,
    ) == created_payload
    assert created.after_head.current_content_sha256 == json.loads(
        created_payload.decode("utf-8")
    )["roadmaps"][0]["content_hash"]

    appended_payload = _durable_payload_exact_size(2, "Large durable append", size + 1)
    appended = store.append_revision(
        principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
        payload_bytes=appended_payload, expected_head=created.after_head,
        idempotency_key="durable-over-old-cap-append-0001",
    )
    state_path = root / durable_module._HARBOUR_STATE_FILE
    accepted_bytes = state_path.read_bytes()
    restarted = DurablePlanningRevisionStore(
        root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW,
    )
    assert restarted.read_payload(
        principal, owner_id=OWNER, revision_id=appended.new_revision_id,
    ) == appended_payload
    replay = restarted.append_revision(
        principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
        payload_bytes=appended_payload, expected_head=created.after_head,
        idempotency_key="durable-over-old-cap-append-0001",
    )
    assert replay == appended
    assert state_path.read_bytes() == accepted_bytes

    changed = _durable_payload_exact_size(2, "Large conflicting append", size + 2)
    foreign = issuer.issue(
        subject_id="foreign-user", owner_id="foreign-owner",
        auth_context_digest=AUTH_CONTEXT, ttl_seconds=3600,
        nonce="durable-over-old-cap-foreign-0001",
    )
    for call, code in (
        (lambda: restarted.append_revision(
            principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
            payload_bytes=changed, expected_head=created.after_head,
            idempotency_key="durable-over-old-cap-append-0001",
        ), "idempotency_conflict"),
        (lambda: restarted.append_revision(
            principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
            payload_bytes=changed, expected_head=created.after_head,
            idempotency_key="durable-over-old-cap-stale-0001",
        ), "stale_authority"),
        (lambda: restarted.create_project(
            principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
            payload_bytes=created_payload,
            idempotency_key="durable-over-old-cap-zero-head-0001",
            expected_generation=0,
        ), "stale_authority"),
        (lambda: restarted.read_payload(
            foreign, owner_id=OWNER, revision_id=appended.new_revision_id,
        ), "owner_scope_denied"),
    ):
        _assert_detached_store_error(call, code)
        assert state_path.read_bytes() == accepted_bytes


def _durable_create(store, principal, payload: bytes, *, key: str = "durable-create-0001"):
    return store.create_project(
        principal,
        owner_id=OWNER,
        project_id=PROJECT,
        roadmap_id=ROADMAP,
        payload_bytes=payload,
        idempotency_key=key,
        expected_generation=0,
    )


def _cross_process_create(
    root: str,
    payload: bytes,
    idempotency_key: str,
    ready_queue,
    start_event,
    result_queue,
) -> None:
    import time as process_time
    import src.planning_revision_store as planning_store_module

    atomic_write = planning_store_module._atomic_write_json

    def delayed_atomic_write(path, value) -> None:
        # Widen the post-refresh/pre-replace race. Without the OS lock both
        # processes acknowledge their private candidate and one receipt is lost.
        process_time.sleep(0.35)
        atomic_write(path, value)

    planning_store_module._atomic_write_json = delayed_atomic_write
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="odysseus-auth",
        key_id="planning-key-1",
        secret=b"durable-planning-test-secret-0001",
        clock=lambda: AUTH_NOW,
    )
    principal = issuer.issue(
        subject_id="alice-user",
        owner_id=OWNER,
        auth_context_digest=AUTH_CONTEXT,
        ttl_seconds=3600,
        nonce="planning-nonce-0001",
    )
    store = DurablePlanningRevisionStore(
        Path(root), trusted_issuers=(issuer,), clock=lambda: AUTH_NOW
    )
    ready_queue.put("ready")
    if not start_event.wait(timeout=60):
        result_queue.put(("process_error", "start_timeout"))
        return
    try:
        receipt = _durable_create(
            store, principal, payload, key=idempotency_key
        )
        result_queue.put(("ok", receipt.receipt_id))
    except DurablePlanningStoreError as exc:
        result_queue.put(("error", exc.code))
    except Exception:
        result_queue.put(("process_error", "unexpected_exception"))


def test_durable_create_append_approve_and_exact_head_readback(tmp_path: Path) -> None:
    store, _issuer, principal, _root = _durable_authority(tmp_path)
    first_payload = _durable_payload(1, "First immutable revision")
    created = _durable_create(store, principal, first_payload)

    assert created.after_head.generation == 1
    assert created.after_head.approved_revision_id is None
    assert store.read_payload(
        principal, owner_id=OWNER, revision_id=created.new_revision_id
    ) == first_payload

    approved = store.approve_revision(
        principal,
        owner_id=OWNER,
        project_id=PROJECT,
        roadmap_id=ROADMAP,
        revision_id=created.new_revision_id,
        expected_head=created.after_head,
        idempotency_key="durable-approve-0001",
    )
    assert approved.after_head.generation == 2
    assert approved.after_head.approved_revision_id == created.new_revision_id

    second_payload = _durable_payload(2, "Second immutable revision")
    appended = store.append_revision(
        principal,
        owner_id=OWNER,
        project_id=PROJECT,
        roadmap_id=ROADMAP,
        payload_bytes=second_payload,
        expected_head=approved.after_head,
        idempotency_key="durable-append-0001",
    )
    head = store.get_head(
        principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP
    )
    assert head == appended.after_head
    assert head.generation == 3
    assert head.current_revision_id == appended.new_revision_id
    assert head.current_content_sha256 == json.loads(second_payload)["roadmaps"][0]["content_hash"]
    assert head.approved_revision_id == created.new_revision_id


def test_durable_idempotency_replays_across_restart_and_conflict_writes_nothing(
    tmp_path: Path,
) -> None:
    store, issuer, principal, root = _durable_authority(tmp_path)
    payload = _durable_payload(1, "Stable command")
    original = _durable_create(store, principal, payload, key="durable-replay-0001")
    state_path = next(root.glob("harbour-planning-state.*.json"))
    before = state_path.read_bytes()

    restarted = DurablePlanningRevisionStore(
        root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW
    )
    duplicate = _durable_create(
        restarted, principal, payload, key="durable-replay-0001"
    )
    assert duplicate == original
    assert state_path.read_bytes() == before

    with pytest.raises(DurablePlanningStoreError) as conflict:
        _durable_create(
            restarted,
            principal,
            _durable_payload(1, "Altered command"),
            key="durable-replay-0001",
        )
    assert conflict.value.code == "idempotency_conflict"
    assert conflict.value.args == ("idempotency_conflict",)
    assert state_path.read_bytes() == before


def test_durable_revision_bundle_reads_one_fresh_owner_bound_verified_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, issuer, principal, root = _durable_authority(tmp_path)
    payload = _durable_payload(1, "Atomic immutable bundle")
    created = _durable_create(store, principal, payload, key="atomic-bundle-create-0001")
    refreshes = []
    store_type = type(store)
    original_refresh = store_type._refresh_locked

    def counted_refresh(actual_store, *args, **kwargs):
        refreshes.append(actual_store)
        return original_refresh(actual_store, *args, **kwargs)

    monkeypatch.setattr(store_type, "_refresh_locked", counted_refresh)
    member = store.read_revision_bundle(
        principal, owner_id=OWNER, revision_id=created.new_revision_id,
    )
    assert len(refreshes) == 1
    assert member.revision.revision_id == created.new_revision_id
    assert member.revision.owner_id == OWNER
    assert member.payload == payload
    assert type(member.payload) is bytes

    foreign = issuer.issue(
        subject_id="atomic-bundle-foreign-user", owner_id="atomic-bundle-foreign-owner",
        auth_context_digest=AUTH_CONTEXT, ttl_seconds=3600,
        nonce="atomic-bundle-foreign-0001",
    )
    _assert_detached_store_error(lambda: store.read_revision_bundle(
        foreign, owner_id=OWNER, revision_id=created.new_revision_id,
    ), "owner_scope_denied")
    assert len(refreshes) == 1

    state_path = root / durable_module._HARBOUR_STATE_FILE
    state = json.loads(state_path.read_bytes())
    record = state["revisions"][created.new_revision_id]
    record["payload_b64"] = "A" + record["payload_b64"][1:]
    corrupted = (json.dumps(
        state, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True,
    ) + "\n").encode("utf-8")
    state_path.write_bytes(corrupted)
    _assert_detached_store_error(lambda: store.read_revision_bundle(
        principal, owner_id=OWNER, revision_id=created.new_revision_id,
    ), "validation_failed")
    assert len(refreshes) == 2
    assert store.available is False
    assert state_path.read_bytes() == corrupted


def test_revision_bundle_validation_only_revalidates_each_read_without_memo_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _issuer, principal, _root = _durable_authority(tmp_path)
    payload = _durable_payload(1, "Validation-only bundle")
    created = _durable_create(
        store, principal, payload, key="validation-only-bundle-create-0001",
    )
    original_parse = durable_module._strict_json_object
    original_validate = durable_module.validate_planning_definition
    original_canonical = durable_module._canonical_planning_bytes
    original_deepcopy = durable_module.deepcopy
    payload_parses = []
    payload_validations = []
    payload_canonicalizations = []
    payload_copies = []
    run_creations = []

    def parse(raw):
        if raw == payload:
            payload_parses.append(raw)
        return original_parse(raw)

    def validate(document):
        if document.get("roadmaps", [{}])[0].get("title") == "Validation-only bundle":
            payload_validations.append(document)
        return original_validate(document)

    def canonical(document):
        if document.get("roadmaps", [{}])[0].get("title") == "Validation-only bundle":
            payload_canonicalizations.append(document)
        return original_canonical(document)

    def copy(value):
        if (
            type(value) is dict
            and value.get("roadmaps", [{}])[0].get("title") == "Validation-only bundle"
        ):
            payload_copies.append(value)
        return original_deepcopy(value)

    def reject_run_creation(*_args, **_kwargs):
        run_creations.append(True)
        raise AssertionError("validation-only read created a memo run")

    monkeypatch.setattr(durable_module, "_strict_json_object", parse)
    monkeypatch.setattr(durable_module, "validate_planning_definition", validate)
    monkeypatch.setattr(durable_module, "_canonical_planning_bytes", canonical)
    monkeypatch.setattr(durable_module, "deepcopy", copy)
    monkeypatch.setattr(durable_module, "_PlanningPayloadValidationRun", reject_run_creation)

    first = store.read_revision_bundle(
        principal, owner_id=OWNER, revision_id=created.new_revision_id,
    )
    second = store.read_revision_bundle(
        principal, owner_id=OWNER, revision_id=created.new_revision_id,
    )

    assert first == second
    assert first.payload == payload
    assert payload_parses == [payload, payload]
    assert len(payload_validations) == 2
    assert payload_canonicalizations == []
    assert payload_copies == []
    assert run_creations == []


def test_revision_bundle_validation_only_rejects_malformed_and_hash_binding_tamper(
    tmp_path: Path,
) -> None:
    malformed_store, _issuer, malformed_principal, malformed_root = _durable_authority(
        tmp_path / "malformed",
    )
    malformed_created = _durable_create(
        malformed_store,
        malformed_principal,
        _durable_payload(1, "Malformed validation-only source"),
        key="validation-only-malformed-create-0001",
    )
    malformed_path = malformed_root / durable_module._HARBOUR_STATE_FILE
    malformed_state = json.loads(malformed_path.read_bytes())
    malformed_record = malformed_state["revisions"][malformed_created.new_revision_id]
    malformed_payload = b"{"
    malformed_record["payload_b64"] = durable_module.base64.b64encode(
        malformed_payload,
    ).decode("ascii")
    malformed_record["payload_sha256"] = durable_module._bytes_digest(malformed_payload)
    malformed_path.write_bytes(json.dumps(
        malformed_state, ensure_ascii=False, allow_nan=False,
        separators=(",", ":"), sort_keys=True,
    ).encode("utf-8"))
    _assert_detached_store_error(lambda: malformed_store.read_revision_bundle(
        malformed_principal,
        owner_id=OWNER,
        revision_id=malformed_created.new_revision_id,
    ), "validation_failed")
    assert malformed_store.available is False

    binding_store, _issuer, binding_principal, binding_root = _durable_authority(
        tmp_path / "binding",
    )
    binding_created = _durable_create(
        binding_store,
        binding_principal,
        _durable_payload(1, "Hash-bound validation-only source"),
        key="validation-only-binding-create-0001",
    )
    binding_path = binding_root / durable_module._HARBOUR_STATE_FILE
    binding_state = json.loads(binding_path.read_bytes())
    binding_record = binding_state["revisions"][binding_created.new_revision_id]
    binding_record["payload_sha256"] = "sha256:" + "0" * 64
    binding_path.write_bytes(json.dumps(
        binding_state, ensure_ascii=False, allow_nan=False,
        separators=(",", ":"), sort_keys=True,
    ).encode("utf-8"))
    _assert_detached_store_error(lambda: binding_store.read_revision_bundle(
        binding_principal,
        owner_id=OWNER,
        revision_id=binding_created.new_revision_id,
    ), "validation_failed")
    assert binding_store.available is False


def test_revision_bundle_validation_only_preserves_noncanonical_payload_bytes(
    tmp_path: Path,
) -> None:
    store, _issuer, principal, _root = _durable_authority(tmp_path)
    document = json.loads(_durable_payload(1, "Noncanonical validation-only bundle"))
    payload = json.dumps(
        document, ensure_ascii=False, allow_nan=False, indent=1, sort_keys=True,
    ).encode("utf-8")
    assert payload != durable_module._canonical_planning_bytes(document)
    created = _durable_create(
        store, principal, payload, key="validation-only-noncanonical-create-0001",
    )

    member = store.read_revision_bundle(
        principal, owner_id=OWNER, revision_id=created.new_revision_id,
    )

    assert member.revision.revision_id == created.new_revision_id
    assert member.payload == payload


def test_read_validation_only_mode_rejects_capture_append_and_nonboolean_before_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _issuer, principal, root = _durable_authority(tmp_path)
    payload = _durable_payload(1, "Validation-only isolation source")
    _durable_create(
        store, principal, payload, key="validation-only-isolation-create-0001",
    )
    state_path = root / durable_module._HARBOUR_STATE_FILE
    original_read = Path.read_bytes
    reads = []

    def counted_read(path):
        if path == state_path:
            reads.append(path)
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", counted_read)
    capture = durable_module._PlanningPayloadValidationCapture()
    with store._lock:
        _assert_private_store_error(lambda: store._refresh_locked(
            _read_validation_only=1,
        ), "validation_failed")
        _assert_private_store_error(lambda: store._refresh_locked(
            _validation_run_capture=capture,
            _read_validation_only=True,
        ), "validation_failed")
        with durable_module._append_planning_payload_validation_scope(store) as append_run:
            _assert_private_store_error(lambda: store._refresh_locked(
                _append_validation_run=append_run,
                _read_validation_only=True,
            ), "validation_failed")
            _assert_private_store_error(lambda: store._refresh_locked(
                _read_validation_only=True,
            ), "validation_failed")
            validated, _document, _roadmap = durable_module._validated_planning_payload(
                payload, PROJECT, ROADMAP, _validation_run=append_run,
            )
            assert validated == payload
    assert reads == []
    assert store.available is True

    with store._lock:
        retained = store._refresh_locked(_validation_run_capture=capture)
        retained_digest = durable_module._canonical_digest(store._state)
    assert reads == [state_path]
    captured_run = capture.take(retained, retained_digest)
    durable_module._discard_planning_payload_validation_run(captured_run)


def test_durable_append_replay_precedes_cas_after_head_advance_and_restart(
    tmp_path: Path,
) -> None:
    store, issuer, principal, root = _durable_authority(tmp_path)
    created = _durable_create(
        store, principal, _durable_payload(1, "Replay ordering source"),
        key="append-order-create-0001",
    )
    payload = _durable_payload(2, "Replay ordering append")
    appended = store.append_revision(
        principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
        payload_bytes=payload, expected_head=created.after_head,
        idempotency_key="append-order-append-0001",
    )
    state_path = root / durable_module._HARBOUR_STATE_FILE
    accepted_bytes = state_path.read_bytes()
    restarted = DurablePlanningRevisionStore(
        root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW,
    )
    assert restarted.append_revision(
        principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
        payload_bytes=payload, expected_head=created.after_head,
        idempotency_key="append-order-append-0001",
    ) == appended
    assert state_path.read_bytes() == accepted_bytes
    _assert_detached_store_error(lambda: restarted.append_revision(
        principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
        payload_bytes=_durable_payload(2, "Replay ordering conflict"),
        expected_head=created.after_head,
        idempotency_key="append-order-append-0001",
    ), "idempotency_conflict")
    assert state_path.read_bytes() == accepted_bytes


@pytest.mark.parametrize(
    "changed_field",
    ["revision_id", "content_hash", "approved_revision", "generation"],
    ids=["revision-id", "content-hash", "approved-revision", "generation"],
)
def test_durable_append_well_formed_mismatching_head_is_stale_before_revision_lookup(
    tmp_path: Path, changed_field: str,
) -> None:
    store, _issuer, principal, root = _durable_authority(tmp_path)
    created = _durable_create(
        store, principal, _durable_payload(1, "CAS mismatch source"),
        key="append-cas-mismatch-create-0001",
    )
    head = created.after_head
    values = {
        "owner_id": head.owner_id,
        "project_id": head.project_id,
        "roadmap_id": head.roadmap_id,
        "current_revision_id": head.current_revision_id,
        "current_content_sha256": head.current_content_sha256,
        "approved_revision_id": head.approved_revision_id,
        "generation": head.generation,
    }
    if changed_field == "revision_id":
        values["current_revision_id"] = "planning-revision:" + "f" * 64
    elif changed_field == "content_hash":
        values["current_content_sha256"] = "sha256:" + "0" * 64
    elif changed_field == "approved_revision":
        values["approved_revision_id"] = head.current_revision_id
    else:
        values["generation"] = head.generation + 1
    fabricated = PlanningHead(**values)
    state_path = root / durable_module._HARBOUR_STATE_FILE
    before = state_path.read_bytes()
    _assert_detached_store_error(lambda: store.append_revision(
        principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
        payload_bytes=_durable_payload(2, f"CAS mismatch {changed_field}"),
        expected_head=fabricated,
        idempotency_key=f"append-cas-mismatch-{changed_field}-0001",
    ), "stale_authority")
    assert state_path.read_bytes() == before


def test_durable_append_matching_head_rejects_wrong_next_revision_without_write(
    tmp_path: Path,
) -> None:
    store, _issuer, principal, root = _durable_authority(tmp_path)
    created = _durable_create(
        store, principal, _durable_payload(1, "Next revision source"),
        key="append-next-create-0001",
    )
    state_path = root / durable_module._HARBOUR_STATE_FILE
    before = state_path.read_bytes()
    _assert_detached_store_error(lambda: store.append_revision(
        principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
        payload_bytes=_durable_payload(3, "Skipped revision"),
        expected_head=created.after_head,
        idempotency_key="append-next-skipped-0001",
    ), "validation_failed")
    assert state_path.read_bytes() == before


def test_durable_concurrent_appends_share_locked_cas_and_only_one_wins(
    tmp_path: Path,
) -> None:
    first, issuer, principal, root = _durable_authority(tmp_path)
    created = _durable_create(
        first, principal, _durable_payload(1, "Concurrent append source"),
        key="concurrent-append-create-0001",
    )
    second = DurablePlanningRevisionStore(
        root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW,
    )
    start = Event()

    def attempt(candidate_store, title: str, key: str):
        assert start.wait(timeout=5)
        try:
            return ("ok", candidate_store.append_revision(
                principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
                payload_bytes=_durable_payload(2, title),
                expected_head=created.after_head, idempotency_key=key,
            ))
        except DurablePlanningStoreError as exc:
            return ("error", exc.code)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(attempt, first, "Concurrent winner A", "concurrent-append-a-0001"),
            executor.submit(attempt, second, "Concurrent winner B", "concurrent-append-b-0001"),
        )
        start.set()
        results = [future.result(timeout=20) for future in futures]
    assert sorted(result[0] for result in results) == ["error", "ok"]
    assert [result[1] for result in results if result[0] == "error"] == ["stale_authority"]
    winner = next(result[1] for result in results if result[0] == "ok")
    restarted = DurablePlanningRevisionStore(
        root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW,
    )
    assert restarted.get_head(
        principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
    ) == winner.after_head
    state = json.loads((root / durable_module._HARBOUR_STATE_FILE).read_bytes())
    assert len(state["revisions"]) == 2


@pytest.mark.parametrize("operation", ["create_project", "append_revision", "approve_revision", "revert_head"])
def test_durable_fresh_same_session_principals_replay_after_restart_and_key_rotation(tmp_path, operation):
    store, issuer, principal, root = _durable_authority(tmp_path)
    args = dict(owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
                idempotency_key="fresh-session-retry-0001")
    if operation == "create_project":
        args.update(payload_bytes=_durable_payload(1, "Fresh create"), expected_generation=0)
    else:
        created = _durable_create(store, principal, _durable_payload(1, "Fresh source"))
        args["expected_head"] = created.after_head
        if operation == "append_revision":
            args["payload_bytes"] = _durable_payload(2, "Fresh append")
        elif operation == "approve_revision":
            args["revision_id"] = created.new_revision_id
        else:
            args["ancestor_revision_id"] = created.new_revision_id
    first = getattr(store, operation)(principal, **args)
    state_path = root / durable_module._HARBOUR_STATE_FILE
    before = state_path.read_bytes()
    original_ref = (store.read_revision(principal, owner_id=OWNER, revision_id=first.new_revision_id).principal_ref
                    if first.new_revision_id else None)

    fresh = issuer.issue(subject_id=principal.subject_id, owner_id=OWNER,
                         auth_context_digest=AUTH_CONTEXT)
    assert fresh.nonce != principal.nonce
    assert fresh.attestation != principal.attestation
    assert getattr(store, operation)(fresh, **args) == first

    later = AUTH_NOW + timedelta(seconds=10)
    rotated = AuthenticatedPrincipalIssuer(
        issuer_id=issuer.issuer_id, key_id="planning-key-rotated",
        secret=b"rotated-planning-test-secret-0002", clock=lambda: later,
    )
    restarted = DurablePlanningRevisionStore(root, trusted_issuers=(rotated,), clock=lambda: later)
    renewed = rotated.issue(subject_id=principal.subject_id, owner_id=OWNER,
                            auth_context_digest=AUTH_CONTEXT)
    assert renewed.nonce not in {principal.nonce, fresh.nonce}
    assert renewed.issued_at != principal.issued_at
    assert getattr(restarted, operation)(renewed, **args) == first
    assert restarted.get_head(renewed, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP) == first.after_head
    if original_ref:
        assert restarted.read_revision(renewed, owner_id=OWNER, revision_id=first.new_revision_id).principal_ref == original_ref
    assert state_path.read_bytes() == before


@pytest.mark.parametrize("operation", ["create_project", "append_revision", "approve_revision", "revert_head"])
@pytest.mark.parametrize("changed", ["issuer", "subject", "session", "expired", "forged", "owner"])
def test_durable_retry_still_authenticates_and_binds_stable_principal_identity(tmp_path, operation, changed):
    store, issuer, principal, root = _durable_authority(tmp_path)
    args = dict(owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
                idempotency_key="identity-conflict-0001")
    if operation == "create_project":
        args["payload_bytes"] = _durable_payload(1, "Bound create")
    else:
        created = _durable_create(store, principal, _durable_payload(1, "Bound source"))
        args["expected_head"] = created.after_head
        args[{"append_revision": "payload_bytes", "approve_revision": "revision_id",
              "revert_head": "ancestor_revision_id"}[operation]] = (
            _durable_payload(2, "Bound append") if operation == "append_revision" else created.new_revision_id
        )
    first = getattr(store, operation)(principal, **args)
    state_path = root / durable_module._HARBOUR_STATE_FILE
    before = state_path.read_bytes()
    other_issuer = AuthenticatedPrincipalIssuer(
        issuer_id="other-issuer" if changed == "issuer" else issuer.issuer_id,
        key_id=issuer.key_id,
        secret=b"forged-planning-test-secret-0002" if changed == "forged" else b"durable-planning-test-secret-0001",
        clock=lambda: AUTH_NOW - timedelta(seconds=600) if changed == "expired" else AUTH_NOW,
    )
    if changed == "issuer":
        store = DurablePlanningRevisionStore(root, trusted_issuers=(issuer, other_issuer), clock=lambda: AUTH_NOW)
    renewed = other_issuer.issue(
        subject_id="other-user" if changed == "subject" else principal.subject_id,
        owner_id="other-owner" if changed == "owner" else OWNER,
        auth_context_digest="sha256:" + "3" * 64 if changed == "session" else AUTH_CONTEXT,
    )
    expected_code = ("unauthenticated" if changed in {"forged", "expired"} else
                     "owner_scope_denied" if changed == "owner" else "idempotency_conflict")
    with pytest.raises(DurablePlanningStoreError) as failure:
        getattr(store, operation)(renewed, **args)
    assert failure.value.code == expected_code
    assert state_path.read_bytes() == before
    assert store.get_head(principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP) == first.after_head


def test_durable_stale_complete_head_rejects_without_pointer_movement(tmp_path: Path) -> None:
    store, _issuer, principal, root = _durable_authority(tmp_path)
    created = _durable_create(store, principal, _durable_payload(1, "First"))
    approved = store.approve_revision(
        principal,
        owner_id=OWNER,
        project_id=PROJECT,
        roadmap_id=ROADMAP,
        revision_id=created.new_revision_id,
        expected_head=created.after_head,
        idempotency_key="durable-approve-0002",
    )
    appended = store.append_revision(
        principal,
        owner_id=OWNER,
        project_id=PROJECT,
        roadmap_id=ROADMAP,
        payload_bytes=_durable_payload(2, "Second"),
        expected_head=approved.after_head,
        idempotency_key="durable-append-0002",
    )
    state_path = next(root.glob("harbour-planning-state.*.json"))
    before = state_path.read_bytes()

    with pytest.raises(DurablePlanningStoreError) as stale:
        store.append_revision(
            principal,
            owner_id=OWNER,
            project_id=PROJECT,
            roadmap_id=ROADMAP,
            payload_bytes=_durable_payload(2, "Competing second"),
            expected_head=approved.after_head,
            idempotency_key="durable-stale-0001",
        )
    assert stale.value.code == "stale_authority"
    assert store.get_head(
        principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP
    ) == appended.after_head
    assert state_path.read_bytes() == before


def test_durable_revert_appends_new_revision_and_preserves_history(tmp_path: Path) -> None:
    store, _issuer, principal, _root = _durable_authority(tmp_path)
    first_payload = _durable_payload(1, "Restorable first")
    created = _durable_create(store, principal, first_payload)
    approved_first = store.approve_revision(
        principal,
        owner_id=OWNER,
        project_id=PROJECT,
        roadmap_id=ROADMAP,
        revision_id=created.new_revision_id,
        expected_head=created.after_head,
        idempotency_key="durable-approve-first",
    )
    second = store.append_revision(
        principal,
        owner_id=OWNER,
        project_id=PROJECT,
        roadmap_id=ROADMAP,
        payload_bytes=_durable_payload(2, "Replaceable second"),
        expected_head=approved_first.after_head,
        idempotency_key="durable-append-second",
    )
    approved_second = store.approve_revision(
        principal,
        owner_id=OWNER,
        project_id=PROJECT,
        roadmap_id=ROADMAP,
        revision_id=second.new_revision_id,
        expected_head=second.after_head,
        idempotency_key="durable-approve-second",
    )

    reverted = store.revert_head(
        principal,
        owner_id=OWNER,
        project_id=PROJECT,
        roadmap_id=ROADMAP,
        ancestor_revision_id=created.new_revision_id,
        expected_head=approved_second.after_head,
        idempotency_key="durable-revert-0001",
    )
    restored_document = json.loads(
        store.read_payload(principal, owner_id=OWNER, revision_id=reverted.new_revision_id)
    )
    assert restored_document["roadmaps"][0]["revision"] == 3
    assert restored_document["roadmaps"][0]["title"] == "Restorable first"
    assert reverted.restored_revision_id == created.new_revision_id
    assert reverted.after_head.current_revision_id == reverted.new_revision_id
    assert reverted.after_head.approved_revision_id == reverted.new_revision_id
    assert store.read_payload(
        principal, owner_id=OWNER, revision_id=created.new_revision_id
    ) == first_payload
    assert store.read_payload(
        principal, owner_id=OWNER, revision_id=second.new_revision_id
    ) == _durable_payload(2, "Replaceable second")


def test_durable_owner_payload_alias_and_raw_error_boundaries_fail_closed(tmp_path: Path) -> None:
    store, _issuer, principal, root = _durable_authority(tmp_path)
    payload = _durable_payload(1, "Owner-bound")

    with pytest.raises(DurablePlanningStoreError) as cross_owner:
        store.create_project(
            principal,
            owner_id="bob",
            project_id=PROJECT,
            roadmap_id=ROADMAP,
            payload_bytes=payload,
            idempotency_key="durable-owner-0001",
        )
    assert cross_owner.value.code == "owner_scope_denied"

    with pytest.raises(DurablePlanningStoreError) as mutable_alias:
        store.create_project(
            principal,
            owner_id=OWNER,
            project_id=PROJECT,
            roadmap_id=ROADMAP,
            payload_bytes=bytearray(payload),
            idempotency_key="durable-alias-0001",
        )
    assert mutable_alias.value.code == "validation_failed"

    substituted = json.loads(payload)
    substituted["project"]["project_id"] = "bob-project"
    substituted["roadmaps"][0]["project_id"] = "bob-project"
    substituted["roadmaps"][0]["content_hash"] = compute_roadmap_content_hash(
        substituted["roadmaps"][0]
    )
    substituted["project"]["latest_approved_revision"][ROADMAP]["content_hash"] = substituted["roadmaps"][0]["content_hash"]
    hostile_payload = json.dumps(substituted, separators=(",", ":"), sort_keys=True).encode()
    with pytest.raises(DurablePlanningStoreError) as payload_owner:
        _durable_create(store, principal, hostile_payload, key="durable-substitute-0001")
    assert payload_owner.value.code == "validation_failed"
    assert payload_owner.value.__cause__ is None
    assert list(root.glob("harbour-planning-state.*.json")) == []


def test_durable_restart_replay_corruption_is_unavailable_and_write_disabled(tmp_path: Path) -> None:
    store, issuer, principal, root = _durable_authority(tmp_path)
    _durable_create(store, principal, _durable_payload(1, "Replay source"))
    state_path = next(root.glob("harbour-planning-state.*.json"))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["transitions"][0]["after_head"]["generation"] = 99
    state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

    restarted = DurablePlanningRevisionStore(
        root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW
    )
    assert restarted.available is False
    with pytest.raises(DurablePlanningStoreError) as read_failure:
        restarted.get_head(
            principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP
        )
    assert read_failure.value.code == "validation_failed"
    with pytest.raises(DurablePlanningStoreError) as write_failure:
        _durable_create(
            restarted,
            principal,
            _durable_payload(1, "Must not replace corrupt state"),
            key="durable-disabled-0001",
        )
    assert write_failure.value.code == "validation_failed"
    assert json.loads(state_path.read_text(encoding="utf-8"))["transitions"][0]["after_head"]["generation"] == 99


def test_durable_two_instances_use_filesystem_cas_not_stale_memory(tmp_path: Path) -> None:
    first, issuer, principal, root = _durable_authority(tmp_path)
    second = DurablePlanningRevisionStore(
        root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW
    )
    first_receipt = _durable_create(
        first, principal, _durable_payload(1, "CAS winner"), key="durable-cas-first"
    )
    with pytest.raises(DurablePlanningStoreError) as stale:
        _durable_create(
            second,
            principal,
            _durable_payload(1, "CAS loser"),
            key="durable-cas-second",
        )
    assert stale.value.code == "stale_authority"
    restarted = DurablePlanningRevisionStore(
        root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW
    )
    assert restarted.get_head(
        principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP
    ) == first_receipt.after_head


def test_durable_two_processes_serialize_refresh_cas_and_replace(tmp_path: Path) -> None:
    root = tmp_path / "durable-planning-cross-process"
    context = multiprocessing.get_context("spawn")
    ready_queue = context.Queue()
    result_queue = context.Queue()
    start_event = context.Event()
    processes = [
        context.Process(
            target=_cross_process_create,
            args=(
                str(root),
                _durable_payload(1, title),
                key,
                ready_queue,
                start_event,
                result_queue,
            ),
        )
        for title, key in (
            ("Cross-process first", "durable-process-first"),
            ("Cross-process second", "durable-process-second"),
        )
    ]
    for process in processes:
        process.start()
    assert [ready_queue.get(timeout=60) for _ in processes] == ["ready", "ready"]
    start_event.set()
    results = [result_queue.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    assert sorted(result[0] for result in results) == ["error", "ok"]
    assert [result[1] for result in results if result[0] == "error"] == [
        "stale_authority"
    ]
    state = json.loads(
        next(root.glob("harbour-planning-state.*.json")).read_text(encoding="utf-8")
    )
    assert len(state["revisions"]) == 1
    assert len(state["transitions"]) == 1
    assert len(state["receipts"]) == 1
    assert len(state["idempotency"]) == 1


def test_durable_state_persists_no_attestation_nonce_or_key_material(tmp_path: Path) -> None:
    store, _issuer, principal, root = _durable_authority(tmp_path)
    _durable_create(store, principal, _durable_payload(1, "No credentials"))
    state_text = next(root.glob("harbour-planning-state.*.json")).read_text(encoding="utf-8")
    assert principal.attestation not in state_text
    assert principal.nonce not in state_text
    assert principal.key_id not in state_text
    assert "principal_digest" in state_text


def test_durable_v1_state_upgrades_only_in_memory_until_next_command(tmp_path: Path) -> None:
    store, issuer, principal, root = _durable_authority(tmp_path)
    created = _durable_create(store, principal, _durable_payload(1, "Legacy v1"))
    state_path = next(root.glob("harbour-planning-state.*.json"))
    legacy = json.loads(state_path.read_text(encoding="utf-8"))
    legacy["schema"] = "odysseus.harbour.planning_store.v1"
    legacy.pop("head_fences")
    legacy.pop("migration_batches")
    state_path.write_text(json.dumps(legacy, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    before = state_path.read_bytes()

    restarted = DurablePlanningRevisionStore(root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)
    assert restarted.available is True
    assert restarted.get_head(
        principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP
    ) == created.after_head
    assert state_path.read_bytes() == before

    restarted.approve_revision(
        principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
        revision_id=created.new_revision_id, expected_head=created.after_head,
        idempotency_key="v1-upgrade-next-command",
    )
    upgraded = json.loads(state_path.read_text(encoding="utf-8"))
    assert upgraded["schema"] == "odysseus.harbour.planning_store.v2"
    assert upgraded["head_fences"]
    assert upgraded["migration_batches"] == {}


def _quarantine_manifest(*, colon_roadmap: bool = False) -> dict:
    """The store accepts only the reviewed H1R03 source identity and census."""
    manifest = build_h1r03_migration_manifest(Path(__file__).resolve().parents[1])
    if colon_roadmap:
        manifest["source_items"][0]["roadmap_id"] += ":legacy"
    return manifest


def _accept_migration(store, principal, manifest):
    return store.accept_migration(
        principal,
        batch_id=manifest["batch_id"],
        current_manifest=lambda: deepcopy(manifest),
    )


def _historical_v1_manifest():
    manifest = deepcopy(build_h1r03_migration_manifest(Path(__file__).resolve().parents[1]))
    manifest["schema"] = MIGRATION_MANIFEST_SCHEMA_V1
    manifest.pop("authority_map_id")
    manifest["authority_pins"] = deepcopy(HISTORICAL_AUTHORITY_PINS)
    identity = durable_module._migration_manifest_identity(manifest)
    digest = durable_module._migration_digest(identity)
    manifest["manifest_digest"] = digest
    manifest["batch_id"] = "planning-migration-batch:" + digest[7:]
    return manifest


def _successor_v2_manifest():
    from src.planning_roadmap_migration import SUCCESSOR_AUTHORITY_MAP_ID, SUCCESSOR_AUTHORITY_PINS

    manifest = deepcopy(_historical_vector_manifest())
    manifest["authority_map_id"] = SUCCESSOR_AUTHORITY_MAP_ID
    manifest["authority_pins"] = deepcopy(SUCCESSOR_AUTHORITY_PINS)
    identity = durable_module._migration_manifest_identity(manifest)
    digest = durable_module._migration_digest(identity)
    manifest["manifest_digest"] = digest
    manifest["batch_id"] = "planning-migration-batch:" + digest[7:]
    return manifest


def _docs_only_v3_manifest():
    manifest = deepcopy(build_h1r03_migration_manifest(Path(__file__).resolve().parents[1]))
    manifest["authority_map_id"] = DOCS_ONLY_AUTHORITY_MAP_ID
    manifest["authority_pins"] = deepcopy(DOCS_ONLY_AUTHORITY_PINS)
    identity = durable_module._migration_manifest_identity(manifest)
    manifest["manifest_digest"] = durable_module._migration_digest(identity)
    manifest["batch_id"] = "planning-migration-batch:" + manifest["manifest_digest"][7:]
    return manifest


def _repo_only_v4_manifest():
    manifest = deepcopy(build_h1r03_migration_manifest(Path(__file__).resolve().parents[1]))
    manifest["authority_map_id"] = REPO_ONLY_AUTHORITY_MAP_ID
    manifest["authority_pins"] = deepcopy(REPO_ONLY_AUTHORITY_PINS)
    identity = durable_module._migration_manifest_identity(manifest)
    manifest["manifest_digest"] = durable_module._migration_digest(identity)
    manifest["batch_id"] = "planning-migration-batch:" + manifest["manifest_digest"][7:]
    return manifest


@pytest.mark.parametrize(
    "manifest_factory",
    (_historical_v1_manifest, _docs_only_v3_manifest, _repo_only_v4_manifest),
    ids=("historical-v1", "docs-only-v3", "repo-only-v4"),
)
def test_historical_manifest_reopens_and_replays_without_current_builder_or_writes(
    tmp_path: Path, monkeypatch, manifest_factory,
):
    store, issuer, _alice_principal, root = _durable_authority(tmp_path)
    principal = issuer.issue(
        subject_id="fuzzy", owner_id="fuzzy", auth_context_digest=AUTH_CONTEXT,
        ttl_seconds=3600, nonce="historical-v1-replay",
    )
    manifest = manifest_factory()
    state_path = root / durable_module._HARBOUR_STATE_FILE
    prepared = store.prepare_migration(principal, manifest=manifest)
    prepared_bytes = state_path.read_bytes()

    reopened = DurablePlanningRevisionStore(root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)
    assert reopened.prepare_migration(principal, manifest=manifest) == prepared
    assert state_path.read_bytes() == prepared_bytes

    accepted = reopened.accept_migration(
        principal, batch_id=manifest["batch_id"], current_manifest=lambda: deepcopy(manifest),
    )
    accepted_bytes = state_path.read_bytes()
    reopened_again = DurablePlanningRevisionStore(root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)

    def current_builder_must_not_run():
        pytest.fail("completed historical accept replay rebuilt current authority")

    assert reopened_again.accept_migration(
        principal, batch_id=manifest["batch_id"], current_manifest=current_builder_must_not_run,
    ) == accepted
    assert state_path.read_bytes() == accepted_bytes

    rolled_back = reopened_again.rollback_migration(principal, batch_id=manifest["batch_id"])
    rolled_back_bytes = state_path.read_bytes()
    reopened_final = DurablePlanningRevisionStore(root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)
    assert reopened_final.rollback_migration(principal, batch_id=manifest["batch_id"]) == rolled_back
    assert state_path.read_bytes() == rolled_back_bytes


def test_successor_v2_manifest_keeps_fixed_digest_and_replays_without_latest_builder(tmp_path: Path):
    store, issuer, _principal, root = _durable_authority(tmp_path)
    principal = issuer.issue(
        subject_id="fuzzy", owner_id="fuzzy", auth_context_digest=AUTH_CONTEXT,
        ttl_seconds=3600, nonce="successor-v2-replay",
    )
    manifest = _successor_v2_manifest()
    assert manifest["manifest_digest"] == (
        "sha256:39e847b0350b7efcac5e38b3212fd3ffa8a4bab65ff26a36b3249cb9879f7068"
    )
    state_path = root / durable_module._HARBOUR_STATE_FILE
    prepared = store.prepare_migration(principal, manifest=manifest)
    prepared_bytes = state_path.read_bytes()
    reopened = DurablePlanningRevisionStore(root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)
    assert reopened.prepare_migration(principal, manifest=manifest) == prepared
    assert state_path.read_bytes() == prepared_bytes
    accepted = reopened.accept_migration(
        principal, batch_id=manifest["batch_id"], current_manifest=lambda: deepcopy(manifest),
    )
    accepted_bytes = state_path.read_bytes()
    accepted_image = json.loads(accepted_bytes)
    accepted_batch = accepted_image["migration_batches"][manifest["batch_id"]]
    accepted_receipt = reopened.read_migration_receipt(
        principal, owner_id="fuzzy", receipt_id=accepted.receipt_id,
    )
    prepared_receipt = reopened.read_migration_receipt(
        principal, owner_id="fuzzy", receipt_id=accepted_batch["prepared_receipt_id"],
    )
    revision_ids = list(accepted_receipt["new_revision_ids"])
    assert len(revision_ids) == len(manifest["source_items"]) == 6
    accepted_heads = {}
    accepted_payloads = {}
    for item, revision_id in zip(manifest["source_items"], revision_ids):
        roadmap_id = item["roadmap_id"]
        head = reopened.get_head(
            principal, owner_id="fuzzy", project_id="harbour-one", roadmap_id=roadmap_id,
        )
        assert head.current_revision_id == revision_id
        payload = reopened.read_payload(
            principal, owner_id="fuzzy", revision_id=revision_id,
        )
        assert payload == manifest["normalized_payloads"][roadmap_id].encode("utf-8")
        accepted_heads[roadmap_id] = head
        accepted_payloads[roadmap_id] = payload
    accepted_record_view = reopened.read_accepted_migration_records(
        principal, owner_id="fuzzy", project_id="harbour-one",
    )
    reopened_again = DurablePlanningRevisionStore(root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)
    assert reopened_again.accept_migration(
        principal, batch_id=manifest["batch_id"],
        current_manifest=lambda: pytest.fail("completed successor-v2 replay rebuilt latest authority"),
    ) == accepted
    assert state_path.read_bytes() == accepted_bytes
    replayed_image = json.loads(state_path.read_bytes())
    assert replayed_image["receipts"] == accepted_image["receipts"]
    assert replayed_image["heads"] == accepted_image["heads"]
    assert replayed_image["revisions"] == accepted_image["revisions"]
    assert reopened_again.read_accepted_migration_records(
        principal, owner_id="fuzzy", project_id="harbour-one",
    ) == accepted_record_view
    for roadmap_id, head in accepted_heads.items():
        assert reopened_again.get_head(
            principal, owner_id="fuzzy", project_id="harbour-one", roadmap_id=roadmap_id,
        ) == head
        assert reopened_again.read_payload(
            principal, owner_id="fuzzy", revision_id=head.current_revision_id,
        ) == accepted_payloads[roadmap_id]
    assert reopened_again.read_migration_receipt(
        principal, owner_id="fuzzy", receipt_id=accepted.receipt_id,
    ) == accepted_receipt
    assert reopened_again.read_migration_receipt(
        principal, owner_id="fuzzy", receipt_id=accepted_batch["prepared_receipt_id"],
    ) == prepared_receipt

    rolled_back = reopened_again.rollback_migration(
        principal, batch_id=manifest["batch_id"],
    )
    rolled_back_bytes = state_path.read_bytes()
    rolled_back_image = json.loads(rolled_back_bytes)
    assert rolled_back_image["migration_batches"][manifest["batch_id"]]["state"] == "rolled_back"
    assert rolled_back_image["heads"] == {}
    assert rolled_back_image["revisions"] == accepted_image["revisions"]
    terminal_receipt = reopened_again.read_migration_receipt(
        principal, owner_id="fuzzy", receipt_id=rolled_back.receipt_id,
    )
    reopened_final = DurablePlanningRevisionStore(root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)
    assert reopened_final.rollback_migration(
        principal, batch_id=manifest["batch_id"],
    ) == rolled_back
    assert state_path.read_bytes() == rolled_back_bytes
    assert reopened_final.read_migration_receipt(
        principal, owner_id="fuzzy", receipt_id=rolled_back.receipt_id,
    ) == terminal_receipt


@pytest.mark.parametrize(
    "manifest_factory",
    (_historical_v1_manifest, _successor_v2_manifest, _docs_only_v3_manifest, _repo_only_v4_manifest),
    ids=("historical-v1", "successor-v2", "docs-only-v3", "repo-only-v4"),
)
def test_historical_prepared_batch_cannot_first_accept_against_current_v5(
    tmp_path: Path, manifest_factory,
):
    store, issuer, _alice_principal, root = _durable_authority(tmp_path)
    principal = issuer.issue(
        subject_id="fuzzy", owner_id="fuzzy", auth_context_digest=AUTH_CONTEXT,
        ttl_seconds=3600, nonce="historical-v1-prepared-stale",
    )
    historical = manifest_factory()
    successor = build_h1r03_migration_manifest(Path(__file__).resolve().parents[1])
    store.prepare_migration(principal, manifest=historical)
    state_path = root / durable_module._HARBOUR_STATE_FILE
    before = state_path.read_bytes()
    reopened = DurablePlanningRevisionStore(root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)
    with pytest.raises(DurablePlanningStoreError) as stale:
        reopened.accept_migration(
            principal, batch_id=historical["batch_id"], current_manifest=lambda: deepcopy(successor),
        )
    assert stale.value.code == "stale_authority"
    assert state_path.read_bytes() == before


def test_exact_product_readiness_v5_prepares_and_accepts(tmp_path: Path):
    store, issuer, foreign_principal, root = _durable_authority(tmp_path)
    manifest = build_h1r03_migration_manifest(Path(__file__).resolve().parents[1])
    assert manifest["authority_map_id"] == PRODUCT_READINESS_AUTHORITY_MAP_ID
    assert manifest["authority_pins"] == PRODUCT_READINESS_AUTHORITY_PINS
    state_path = root / durable_module._HARBOUR_STATE_FILE
    before = state_path.read_bytes() if state_path.exists() else None
    with pytest.raises(DurablePlanningStoreError) as denied:
        store.prepare_migration(foreign_principal, manifest=manifest)
    assert denied.value.code == "owner_scope_denied"
    assert (state_path.read_bytes() if state_path.exists() else None) == before
    principal = issuer.issue(
        subject_id="fuzzy", owner_id="fuzzy", auth_context_digest=AUTH_CONTEXT,
        ttl_seconds=3600, nonce="product-readiness-v5-positive",
    )
    prepared = store.prepare_migration(principal, manifest=manifest)
    accepted = store.accept_migration(
        principal, batch_id=manifest["batch_id"], current_manifest=lambda: deepcopy(manifest),
    )
    assert prepared.after_state == "prepared"
    assert accepted.after_state == "accepted"
    assert json.loads((root / durable_module._HARBOUR_STATE_FILE).read_bytes())[
        "migration_batches"
    ][manifest["batch_id"]]["state"] == "accepted"


def test_successor_v2_prepared_batch_cannot_new_accept_against_docs_only_v3(tmp_path: Path):
    store, issuer, _principal, root = _durable_authority(tmp_path)
    principal = issuer.issue(
        subject_id="fuzzy", owner_id="fuzzy", auth_context_digest=AUTH_CONTEXT,
        ttl_seconds=3600, nonce="successor-v2-prepared-stale",
    )
    successor_v2 = _successor_v2_manifest()
    docs_only_v3 = _docs_only_v3_manifest()
    store.prepare_migration(principal, manifest=successor_v2)
    state_path = root / durable_module._HARBOUR_STATE_FILE
    before = state_path.read_bytes()
    reopened = DurablePlanningRevisionStore(root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)
    with pytest.raises(DurablePlanningStoreError) as stale:
        reopened.accept_migration(
            principal, batch_id=successor_v2["batch_id"],
            current_manifest=lambda: deepcopy(docs_only_v3),
        )
    assert stale.value.code == "stale_authority"
    assert state_path.read_bytes() == before


def test_docs_only_v3_prepared_batch_cannot_new_accept_against_repo_only_v4(tmp_path: Path):
    store, issuer, _principal, root = _durable_authority(tmp_path)
    principal = issuer.issue(
        subject_id="fuzzy", owner_id="fuzzy", auth_context_digest=AUTH_CONTEXT,
        ttl_seconds=3600, nonce="docs-v3-prepared-stale",
    )
    previous = _docs_only_v3_manifest()
    latest = build_h1r03_migration_manifest(Path(__file__).resolve().parents[1])
    store.prepare_migration(principal, manifest=previous)
    before = (root / durable_module._HARBOUR_STATE_FILE).read_bytes()
    with pytest.raises(DurablePlanningStoreError) as stale:
        store.accept_migration(principal, batch_id=previous["batch_id"], current_manifest=lambda: deepcopy(latest))
    assert stale.value.code == "stale_authority"
    assert (root / durable_module._HARBOUR_STATE_FILE).read_bytes() == before


@pytest.mark.parametrize("corruption", [
    "v1_extra_map_id", "v1_successor_pins", "v2_missing_map_id", "v2_historical_pins", "unknown_schema",
    "v2_historical_map_id", "v2_unknown_map_id", "v2_docs_only_with_v2_pins", "v2_docs_only_with_extra_pin",
])
def test_migration_manifest_schema_dispatch_rejects_hybrids_before_persistence(tmp_path: Path, corruption: str):
    from src.planning_roadmap_migration import (
        DOCS_ONLY_AUTHORITY_MAP_ID, DOCS_ONLY_AUTHORITY_PINS,
        HISTORICAL_AUTHORITY_MAP_ID, SUCCESSOR_AUTHORITY_PINS,
    )

    store, _issuer, principal, root = _durable_authority(tmp_path)
    manifest = deepcopy(build_h1r03_migration_manifest(Path(__file__).resolve().parents[1]))
    if corruption == "v1_extra_map_id":
        manifest["schema"] = MIGRATION_MANIFEST_SCHEMA_V1
    elif corruption == "v1_successor_pins":
        manifest["schema"] = MIGRATION_MANIFEST_SCHEMA_V1
        manifest.pop("authority_map_id")
        manifest["authority_pins"] = deepcopy(SUCCESSOR_AUTHORITY_PINS)
    elif corruption == "v2_missing_map_id":
        manifest.pop("authority_map_id")
    elif corruption == "v2_historical_pins":
        manifest["authority_pins"] = deepcopy(HISTORICAL_AUTHORITY_PINS)
    elif corruption == "v2_historical_map_id":
        manifest["authority_map_id"] = HISTORICAL_AUTHORITY_MAP_ID
        manifest["authority_pins"] = deepcopy(HISTORICAL_AUTHORITY_PINS)
    elif corruption == "v2_unknown_map_id":
        manifest["authority_map_id"] = "unknown-map"
    elif corruption == "v2_docs_only_with_v2_pins":
        manifest["authority_map_id"] = DOCS_ONLY_AUTHORITY_MAP_ID
        manifest["authority_pins"] = deepcopy(SUCCESSOR_AUTHORITY_PINS)
    elif corruption == "v2_docs_only_with_extra_pin":
        manifest["authority_map_id"] = DOCS_ONLY_AUTHORITY_MAP_ID
        manifest["authority_pins"] = dict(DOCS_ONLY_AUTHORITY_PINS, **{"unknown/path": "0" * 40})
    else:
        manifest["schema"] = "odysseus.harbour.planning_migration_manifest.v3"
    before = deepcopy(manifest)
    with pytest.raises(DurablePlanningStoreError) as invalid:
        store.prepare_migration(principal, manifest=manifest)
    assert invalid.value.code == "validation_failed"
    assert manifest == before
    assert not (root / durable_module._HARBOUR_STATE_FILE).exists()


@pytest.mark.parametrize("map_id,pins", [
    (PRODUCT_READINESS_AUTHORITY_MAP_ID, REPO_ONLY_AUTHORITY_PINS),
    (REPO_ONLY_AUTHORITY_MAP_ID, PRODUCT_READINESS_AUTHORITY_PINS),
    (PRODUCT_READINESS_AUTHORITY_MAP_ID, {key: value for key, value in PRODUCT_READINESS_AUTHORITY_PINS.items() if key != "docs/plans/harbour-one-product-roadmap.json"}),
    (PRODUCT_READINESS_AUTHORITY_MAP_ID, {**PRODUCT_READINESS_AUTHORITY_PINS, "unknown/product-readiness": "0" * 40}),
    (REPO_ONLY_AUTHORITY_MAP_ID, DOCS_ONLY_AUTHORITY_PINS),
    (DOCS_ONLY_AUTHORITY_MAP_ID, REPO_ONLY_AUTHORITY_PINS),
    (REPO_ONLY_AUTHORITY_MAP_ID, {key: value for key, value in REPO_ONLY_AUTHORITY_PINS.items() if key != "docs/plans/evidence/harbour-one-h1r05-repo-acceptance-20261003.receipt"}),
    (REPO_ONLY_AUTHORITY_MAP_ID, {**REPO_ONLY_AUTHORITY_PINS, "unknown/receipt": "0" * 40}),
])
def test_v5_and_historical_identity_hybrids_reject_before_persistence(tmp_path: Path, map_id, pins):
    store, _issuer, principal, root = _durable_authority(tmp_path)
    manifest = deepcopy(build_h1r03_migration_manifest(Path(__file__).resolve().parents[1]))
    manifest["authority_map_id"] = map_id
    manifest["authority_pins"] = deepcopy(pins)
    before = deepcopy(manifest)
    with pytest.raises(DurablePlanningStoreError) as rejected:
        store.prepare_migration(principal, manifest=manifest)
    assert rejected.value.code == "validation_failed"
    assert manifest == before
    assert not (root / durable_module._HARBOUR_STATE_FILE).exists()


def _legacy_colon_migration_manifest() -> dict:
    """Build the historical, pre-H1R03 shape used only to seed quarantine tests."""
    manifest = build_h1r03_migration_manifest(Path(__file__).resolve().parents[1])
    item = manifest["source_items"][0]
    old_roadmap_id = item["roadmap_id"]
    roadmap_id = old_roadmap_id + ":legacy"
    document = definition_fixture("harbour-one", roadmap_id, include_draft=False)
    roadmap = document["roadmaps"][0]
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    payload = durable_module._canonical_planning_bytes(document)
    manifest["source_items"][0] = {
        **item, "roadmap_id": roadmap_id,
        "payload_sha256": durable_module._bytes_digest(payload),
        "content_hash": roadmap["content_hash"],
    }
    manifest["normalized_payloads"].pop(old_roadmap_id)
    manifest["normalized_payloads"][roadmap_id] = payload.decode("utf-8")
    identity = {key: value for key, value in manifest.items() if key not in {"supporting_observation", "manifest_digest", "batch_id"}}
    digest = durable_module._migration_digest(identity)
    manifest["manifest_digest"] = digest
    manifest["batch_id"] = "planning-migration-batch:" + digest[7:]
    return manifest


def _accepted_h1r03_store(tmp_path: Path, *, subject_id: str | None = None):
    store, issuer, _principal, root = _durable_authority(tmp_path)
    principal = issuer.issue(
        subject_id=subject_id or "owner_" + hashlib.sha256(b"accepted-h1r03-admin").hexdigest()[:32],
        owner_id="fuzzy", auth_context_digest=AUTH_CONTEXT,
        ttl_seconds=3600, nonce="accepted-h1r03-integrity-test",
    )
    manifest = build_h1r03_migration_manifest(Path(__file__).resolve().parents[1])
    store.prepare_migration(principal, manifest=manifest)
    _accept_migration(store, principal, manifest)
    return store, issuer, principal, root, manifest


def _legacy_quarantine_image(tmp_path: Path, monkeypatch, component: str = "idempotency_key"):
    store, issuer, principal, root = _durable_authority(tmp_path)
    values = {"owner_id": OWNER, "project_id": PROJECT, "roadmap_id": ROADMAP,
              "idempotency_key": "legacy-create-0001"}
    values[component] += ":legacy"
    if component == "owner_id":
        principal = issuer.issue(
            subject_id="legacy-owner", owner_id=values["owner_id"],
            auth_context_digest=AUTH_CONTEXT, ttl_seconds=3600, nonce="legacy-nonce",
        )
    payload = durable_module._canonical_planning_bytes(definition_fixture(
        values["project_id"], values["roadmap_id"], include_draft=False,
    ))
    # Reproduce the old writer's admitted inputs, without relaxing replay.
    with monkeypatch.context() as legacy:
        legacy.setattr(durable_module, "_require_unambiguous_component", lambda value: None)
        store.create_project(principal, **values, payload_bytes=payload)
    path = root / durable_module._HARBOUR_STATE_FILE
    image = json.loads(path.read_bytes())
    durable_module._verify_harbour_state(image)
    return store, issuer, principal, path, image


def _assert_detached_store_error(operation, code: str) -> None:
    try:
        raise RuntimeError("private ambient failure")
    except RuntimeError:
        with pytest.raises(DurablePlanningStoreError) as caught:
            operation()
    assert type(caught.value) is DurablePlanningStoreError
    assert caught.value.args == (code,)
    assert caught.value.code == code
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


def _assert_private_store_error(operation, code: str) -> None:
    with pytest.raises(DurablePlanningStoreError) as caught:
        operation()
    assert type(caught.value) is DurablePlanningStoreError
    assert caught.value.args == (code,)
    assert caught.value.code == code


@pytest.mark.parametrize("component", ["owner_id", "project_id", "roadmap_id", "idempotency_key"])
@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("load_path", ["constructor", "refresh"])
def test_legacy_composite_key_quarantine_preserves_verified_state_bytes(
    tmp_path: Path, monkeypatch, component: str, version: int, load_path: str,
) -> None:
    store, issuer, principal, path, image = _legacy_quarantine_image(tmp_path, monkeypatch, component)
    if version == 1:
        image["schema"] = durable_module._HARBOUR_PLANNING_STATE_SCHEMA_V1
        image.pop("head_fences")
        image.pop("migration_batches")
    path.write_text(json.dumps(image, indent=3) + "\n", encoding="utf-8")
    before = path.read_bytes()
    if load_path == "constructor":
        store = DurablePlanningRevisionStore(path.parent, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)
        assert not store.available
    else:
        assert store.available
    # Valid safe query components cannot expose another subject through legacy keys.
    _assert_detached_store_error(lambda: store.get_head(
        principal, owner_id=principal.owner_id, project_id=PROJECT, roadmap_id=ROADMAP,
    ) if component != "owner_id" else store.read_revision(
        principal, owner_id=principal.owner_id, revision_id=next(iter(image["revisions"])),
    ), "migration_required")
    assert not store.available
    assert store._state == durable_module._new_harbour_state()
    _assert_detached_store_error(lambda: _durable_create(
        store, principal, _durable_payload(1, "Never overwrite legacy state"),
    ), "migration_required")
    assert path.read_bytes() == before


def test_h1r03_migration_rejects_legacy_colon_subject_before_write(tmp_path: Path, monkeypatch) -> None:
    store, issuer, _principal, root = _durable_authority(tmp_path)
    principal = issuer.issue(subject_id="migration-operator", owner_id="fuzzy",
                             auth_context_digest=AUTH_CONTEXT, ttl_seconds=3600, nonce="migration-nonce")
    manifest = _quarantine_manifest(colon_roadmap=True)
    touched = []
    monkeypatch.setattr(store, "_authenticate", lambda *args, **kwargs: touched.append("auth"))
    with pytest.raises(DurablePlanningStoreError) as rejected:
        store.prepare_migration(principal, manifest=manifest)
    assert rejected.value.code == "validation_failed"
    assert touched == []
    assert store.available
    assert not (root / durable_module._HARBOUR_STATE_FILE).exists()


@pytest.mark.parametrize("load_path", ["constructor", "refresh"])
@pytest.mark.parametrize("corruption", [
    "manifest_digest", "principal_identity", "manifest_snapshot", "duplicated_projection",
    "receipt_input", "transition_command_digest", "transition_idempotency_key",
])
def test_accepted_h1r03_single_field_corruption_fails_closed_and_preserves_bytes(
    tmp_path: Path, load_path: str, corruption: str,
) -> None:
    store, issuer, principal, root, manifest = _accepted_h1r03_store(tmp_path)
    path = root / durable_module._HARBOUR_STATE_FILE
    image = json.loads(path.read_bytes())
    batch = image["migration_batches"][manifest["batch_id"]]
    if corruption == "manifest_digest":
        batch["manifest_digest"] = "sha256:" + "0" * 64
    elif corruption == "principal_identity":
        batch["principal_identity"]["subject_id"] = "different-admin"
    elif corruption == "manifest_snapshot":
        batch["manifest_identity"]["source_items"][0]["sha256"] = "0" * 64
    elif corruption == "duplicated_projection":
        batch["source_items"][0]["blob"] = "0" * 40
    elif corruption == "receipt_input":
        image["receipts"][batch["prepared_receipt_id"]]["input_digest"] = "sha256:" + "0" * 64
    else:
        transition = next(
            item for item in image["transitions"]
            if item.get("batch_id") == manifest["batch_id"] and item["kind"] == "migration_prepare"
        )
        if corruption == "transition_command_digest":
            transition["command_digest"] = "sha256:" + "0" * 64
        else:
            transition["idempotency_key"] = "h1r03.prepare." + "a" * 48
    path.write_bytes(json.dumps(image, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    before = path.read_bytes()

    reopened = (
        DurablePlanningRevisionStore(root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)
        if load_path == "constructor" else store
    )
    if load_path == "refresh":
        _assert_detached_store_error(lambda: reopened.read_accepted_migration_records(
            principal, owner_id="fuzzy", project_id="harbour-one",
        ), "validation_failed")
    assert not reopened.available
    assert path.read_bytes() == before


def test_pre_identity_migration_batch_is_validated_as_legacy_but_never_projected(
    tmp_path: Path,
) -> None:
    store, issuer, principal, root, manifest = _accepted_h1r03_store(tmp_path)
    path = root / durable_module._HARBOUR_STATE_FILE
    image = json.loads(path.read_bytes())
    batch = image["migration_batches"][manifest["batch_id"]]
    batch.pop("manifest_identity")
    path.write_bytes(json.dumps(image, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    before = path.read_bytes()

    restarted = DurablePlanningRevisionStore(root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)
    assert restarted.available
    assert restarted.get_migration_batch(
        principal, owner_id="fuzzy", batch_id=manifest["batch_id"],
    )["state"] == "accepted"
    _assert_detached_store_error(lambda: restarted.read_accepted_migration_records(
        principal, owner_id="fuzzy", project_id="harbour-one",
    ), "validation_failed")
    assert path.read_bytes() == before


def test_accepted_h1r03_canonical_read_is_independent_of_current_authority_constants(
    tmp_path: Path, monkeypatch,
) -> None:
    import src.planning_roadmap_migration as migration_module

    _store, issuer, principal, root, _manifest = _accepted_h1r03_store(tmp_path)
    path = root / durable_module._HARBOUR_STATE_FILE
    before = path.read_bytes()
    changed_pins = {name: "0" * 40 for name in migration_module.AUTHORITY_PINS}
    monkeypatch.setattr(migration_module, "AUTHORITY_PINS", changed_pins)

    restarted = DurablePlanningRevisionStore(root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)
    assert restarted.available
    _batch, records, head_bindings = restarted.read_accepted_migration_records(
        principal, owner_id="fuzzy", project_id="harbour-one",
    )
    assert len({document["roadmaps"][0]["roadmap_id"] for _owner, document, _source in records}) == 6
    assert len(head_bindings) == 6
    assert all(
        binding["accepted_revision"] == binding["current_revision"] == binding["approved_revision"] == 1
        for binding in head_bindings
    )
    assert path.read_bytes() == before


def test_subject_bound_accepted_read_uses_stable_owner_and_rejects_same_alias_recreation(tmp_path):
    store, issuer, _principal, _root, manifest = _accepted_h1r03_store(tmp_path)
    owner = manifest["owner_id"]
    project = manifest["project_id"]
    subject = "owner_" + hashlib.sha256(b"accepted-h1r03-admin").hexdigest()[:32]
    renamed = issuer.issue(
        subject_id=subject, owner_id="fuzzy-renamed", auth_context_digest=AUTH_CONTEXT,
        ttl_seconds=3600, nonce="subject-bound-renamed-alias",
    )
    stable_owner, batch, records, bindings = store.read_subject_bound_accepted_migration_records(
        renamed, project_id=project,
    )
    assert stable_owner == owner
    assert batch["principal_identity"]["issuer_id"] == issuer.issuer_id
    assert batch["principal_identity"]["subject_id"] == subject
    assert len(records) == len(bindings) == 6
    records[0][1]["roadmaps"][0]["title"] = "caller mutation"
    bindings[0]["owner_id"] = "caller mutation"
    batch["source_items"].clear()
    _stable_owner, reread_batch, reread_records, reread_bindings = (
        store.read_subject_bound_accepted_migration_records(renamed, project_id=project)
    )
    assert len(reread_batch["source_items"]) == len(reread_records) == len(reread_bindings) == 6
    assert reread_records[0][1]["roadmaps"][0]["title"] != "caller mutation"
    assert reread_bindings[0]["owner_id"] == owner
    duplicated = deepcopy(reread_batch)
    duplicated["batch_id"] += "-duplicate"
    store._state["migration_batches"][duplicated["batch_id"]] = duplicated
    refresh = store._refresh_locked
    store._refresh_locked = lambda: None
    try:
        with pytest.raises(DurablePlanningStoreError) as ambiguous:
            store.read_subject_bound_accepted_migration_records(renamed, project_id=project)
        assert ambiguous.value.code == "validation_failed"
    finally:
        store._refresh_locked = refresh
        store._state["migration_batches"].pop(duplicated["batch_id"])
    recreated = issuer.issue(
        subject_id="owner_" + hashlib.sha256(b"different-admin-subject").hexdigest()[:32],
        owner_id=owner, auth_context_digest=AUTH_CONTEXT,
        ttl_seconds=3600, nonce="same-alias-new-subject",
    )
    with pytest.raises(DurablePlanningStoreError) as denied:
        store.read_subject_bound_accepted_migration_records(recreated, project_id=project)
    assert denied.value.code == "migration_required"


def test_legacy_accepted_subject_requires_internal_append_only_binding_and_replay(tmp_path):
    store, issuer, _old_principal, root, manifest = _accepted_h1r03_store(tmp_path, subject_id="fuzzy")
    owner = manifest["owner_id"]
    project = manifest["project_id"]
    image = json.loads(store._path.read_bytes())
    batch = image["migration_batches"][manifest["batch_id"]]
    batch.pop("accepted_subject_binding", None)
    batch.pop("subject_binding_receipt_id", None)
    store._path.write_bytes(json.dumps(image, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    legacy = DurablePlanningRevisionStore(root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)
    subject = "owner_" + hashlib.sha256(b"bootstrapped-admin-subject").hexdigest()[:32]
    principal = issuer.issue(
        subject_id=subject, owner_id=owner, auth_context_digest=AUTH_CONTEXT,
        ttl_seconds=3600, nonce="explicit-internal-subject-bootstrap",
    )
    with pytest.raises(DurablePlanningStoreError) as denied:
        legacy.read_subject_bound_accepted_migration_records(principal, project_id=project)
    assert denied.value.code == "migration_required"
    pristine = legacy._path.read_bytes()
    with pytest.raises(DurablePlanningStoreError):
        legacy.bind_accepted_migration_subject(
            _old_principal, batch_id=manifest["batch_id"], owner_id=owner, project_id=project,
            manifest_digest=manifest["manifest_digest"], accepted_receipt_id=batch["accepted_receipt_id"],
            idempotency_key="internal-subject-bind-0001",
        )
    assert legacy._path.read_bytes() == pristine
    second_issuer = AuthenticatedPrincipalIssuer(
        issuer_id="second-trusted-planning-issuer", key_id="planning-key-2",
        secret=b"durable-planning-test-secret-0002", clock=lambda: AUTH_NOW,
    )
    second_trusted = DurablePlanningRevisionStore(
        root, trusted_issuers=(issuer, second_issuer), clock=lambda: AUTH_NOW,
    )
    wrong_provenance = second_issuer.issue(
        subject_id=subject, owner_id=owner, auth_context_digest=AUTH_CONTEXT,
        ttl_seconds=3600, nonce="trusted-wrong-provenance",
    )
    with pytest.raises(DurablePlanningStoreError) as wrong_read:
        second_trusted.read_subject_bound_accepted_migration_records(
            wrong_provenance, project_id=project,
        )
    assert wrong_read.value.code == "migration_required"
    trusted_before = second_trusted._path.read_bytes()
    with pytest.raises(DurablePlanningStoreError):
        second_trusted.bind_accepted_migration_subject(
            wrong_provenance, batch_id=manifest["batch_id"], owner_id=owner, project_id=project,
            manifest_digest=manifest["manifest_digest"], accepted_receipt_id=batch["accepted_receipt_id"],
            idempotency_key="trusted-wrong-provenance-0001",
        )
    assert second_trusted._path.read_bytes() == trusted_before
    wrong_issuer = AuthenticatedPrincipalIssuer(
        issuer_id="other-planning-issuer", key_id="planning-key-1",
        secret=b"durable-planning-test-secret-0001", clock=lambda: AUTH_NOW,
    ).issue(subject_id=subject, owner_id=owner, auth_context_digest=AUTH_CONTEXT)
    attempts = [
        (wrong_issuer, project, manifest["manifest_digest"], batch["accepted_receipt_id"]),
        (principal, "different-project", manifest["manifest_digest"], batch["accepted_receipt_id"]),
        (principal, project, "sha256:" + "0" * 64, batch["accepted_receipt_id"]),
        (principal, project, manifest["manifest_digest"], "different-accept-receipt"),
    ]
    for actor, project, manifest_digest, accepted_receipt_id in attempts:
        with pytest.raises(DurablePlanningStoreError):
            legacy.bind_accepted_migration_subject(
                actor, batch_id=manifest["batch_id"], owner_id=owner, project_id=project,
                manifest_digest=manifest_digest, accepted_receipt_id=accepted_receipt_id,
                idempotency_key="internal-subject-bind-0001",
            )
        assert legacy._path.read_bytes() == pristine
    renamed_before_binding = issuer.issue(
        subject_id=subject, owner_id="fuzzy-renamed", auth_context_digest=AUTH_CONTEXT,
        ttl_seconds=3600, nonce="bootstrap-alias-mismatch",
    )
    with pytest.raises(DurablePlanningStoreError):
        legacy.bind_accepted_migration_subject(
            renamed_before_binding, batch_id=manifest["batch_id"], owner_id=owner, project_id=project,
            manifest_digest=manifest["manifest_digest"], accepted_receipt_id=batch["accepted_receipt_id"],
            idempotency_key="internal-subject-bind-0001",
        )
    assert legacy._path.read_bytes() == pristine
    receipt = legacy.bind_accepted_migration_subject(
        principal, batch_id=manifest["batch_id"], owner_id=owner, project_id=project,
        manifest_digest=manifest["manifest_digest"], accepted_receipt_id=batch["accepted_receipt_id"],
        idempotency_key="internal-subject-bind-0001",
    )
    assert receipt["receipt_type"] == "planning_subject_binding"
    after_bind = legacy._path.read_bytes()
    changed_alias = issuer.issue(
        subject_id=subject, owner_id="fuzzy-renamed", auth_context_digest=AUTH_CONTEXT,
        ttl_seconds=3600, nonce="post-bind-renamed-alias",
    )
    stable_owner, _bound_batch, records, _heads = legacy.read_subject_bound_accepted_migration_records(
        changed_alias, project_id=project,
    )
    assert stable_owner == owner and len(records) == 6
    assert legacy.bind_accepted_migration_subject(
        principal, batch_id=manifest["batch_id"], owner_id=owner, project_id=project,
        manifest_digest=manifest["manifest_digest"], accepted_receipt_id=batch["accepted_receipt_id"],
        idempotency_key="internal-subject-bind-0001",
    ) == receipt
    assert legacy._path.read_bytes() == after_bind
    changed_context = issuer.issue(
        subject_id=subject, owner_id=owner,
        auth_context_digest="sha256:" + "9" * 64,
        ttl_seconds=3600, nonce="same-subject-changed-context",
    )
    with pytest.raises(DurablePlanningStoreError) as conflict:
        legacy.bind_accepted_migration_subject(
            changed_context, batch_id=manifest["batch_id"], owner_id=owner, project_id=project,
            manifest_digest=manifest["manifest_digest"], accepted_receipt_id=batch["accepted_receipt_id"],
            idempotency_key="internal-subject-bind-0001",
        )
    assert conflict.value.code == "idempotency_conflict"
    assert legacy._path.read_bytes() == after_bind
    for target_owner, key in ((owner, "internal-subject-bind-reassignment-01"),
                              ("other-owner", "internal-subject-bind-reassignment-02")):
        with pytest.raises(DurablePlanningStoreError):
            legacy.bind_accepted_migration_subject(
                principal, batch_id=manifest["batch_id"], owner_id=target_owner, project_id=project,
                manifest_digest=manifest["manifest_digest"],
                accepted_receipt_id=batch["accepted_receipt_id"], idempotency_key=key,
            )
        assert legacy._path.read_bytes() == after_bind
    restarted = DurablePlanningRevisionStore(root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)
    assert restarted.read_subject_bound_accepted_migration_records(
        changed_alias, project_id=project,
    )[0] == owner
    legacy.rollback_migration(_old_principal, batch_id=manifest["batch_id"])
    rolled_back = DurablePlanningRevisionStore(root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)
    with pytest.raises(DurablePlanningStoreError):
        rolled_back.read_subject_bound_accepted_migration_records(changed_alias, project_id=project)


def test_legacy_subject_binding_refuses_preidentity_and_damaged_accepted_projection(tmp_path):
    preidentity_root = tmp_path / "preidentity"
    preidentity_root.mkdir()
    store, issuer, _old, root, manifest = _accepted_h1r03_store(preidentity_root, subject_id="fuzzy")
    owner, project = manifest["owner_id"], manifest["project_id"]
    path = root / durable_module._HARBOUR_STATE_FILE
    image = json.loads(path.read_bytes())
    batch = image["migration_batches"][manifest["batch_id"]]
    batch.pop("manifest_identity")
    path.write_bytes(json.dumps(image, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    before = path.read_bytes()
    legacy = DurablePlanningRevisionStore(root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)
    principal = issuer.issue(
        subject_id="owner_" + hashlib.sha256(b"preidentity-bind").hexdigest()[:32],
        owner_id=owner, auth_context_digest=AUTH_CONTEXT, ttl_seconds=3600,
        nonce="preidentity-bind-denial",
    )
    with pytest.raises(DurablePlanningStoreError) as preidentity:
        legacy.bind_accepted_migration_subject(
            principal, batch_id=manifest["batch_id"], owner_id=owner, project_id=project,
            manifest_digest=manifest["manifest_digest"], accepted_receipt_id=batch["accepted_receipt_id"],
            idempotency_key="preidentity-bind-0001",
        )
    assert preidentity.value.code == "validation_failed"
    assert path.read_bytes() == before

    damaged_root = tmp_path / "damaged"
    damaged_root.mkdir()
    damaged, damaged_issuer, _old, damaged_root, damaged_manifest = _accepted_h1r03_store(
        damaged_root, subject_id="fuzzy",
    )
    damaged_owner, damaged_project = damaged_manifest["owner_id"], damaged_manifest["project_id"]
    damaged_principal = damaged_issuer.issue(
        subject_id="owner_" + hashlib.sha256(b"damaged-bind").hexdigest()[:32],
        owner_id=damaged_owner, auth_context_digest=AUTH_CONTEXT, ttl_seconds=3600,
        nonce="damaged-bind-denial",
    )
    damaged_bytes = damaged._path.read_bytes()
    accepted_revision = next(iter(damaged._state["revisions"]))
    damaged._state["revisions"][accepted_revision]["content_sha256"] = "sha256:" + "0" * 64
    damaged._refresh_locked = lambda: None
    with pytest.raises(DurablePlanningStoreError) as rejected:
        damaged.bind_accepted_migration_subject(
            damaged_principal, batch_id=damaged_manifest["batch_id"], owner_id=damaged_owner,
            project_id=damaged_project, manifest_digest=damaged_manifest["manifest_digest"],
            accepted_receipt_id=damaged._state["migration_batches"][damaged_manifest["batch_id"]]["accepted_receipt_id"],
            idempotency_key="damaged-bind-0001",
        )
    assert rejected.value.code == "validation_failed"
    assert damaged._path.read_bytes() == damaged_bytes


def test_persisted_subject_binding_receipt_corruption_fails_closed(tmp_path):
    store, issuer, _old, root, manifest = _accepted_h1r03_store(tmp_path, subject_id="fuzzy")
    owner = manifest["owner_id"]
    image = json.loads(store._path.read_bytes())
    batch = image["migration_batches"][manifest["batch_id"]]
    batch.pop("accepted_subject_binding", None)
    batch.pop("subject_binding_receipt_id", None)
    store._path.write_bytes(json.dumps(image, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    legacy = DurablePlanningRevisionStore(root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)
    principal = issuer.issue(
        subject_id="owner_" + hashlib.sha256(b"persisted-bind").hexdigest()[:32],
        owner_id=owner, auth_context_digest=AUTH_CONTEXT, ttl_seconds=3600,
        nonce="persisted-bind",
    )
    legacy.bind_accepted_migration_subject(
        principal, batch_id=manifest["batch_id"], owner_id=owner, project_id=manifest["project_id"],
        manifest_digest=manifest["manifest_digest"],
        accepted_receipt_id=batch["accepted_receipt_id"], idempotency_key="persisted-binding-0001",
    )
    bound_image = json.loads(legacy._path.read_bytes())
    receipt_id = bound_image["migration_batches"][manifest["batch_id"]]["subject_binding_receipt_id"]
    bound_image["receipts"][receipt_id]["output_digest"] = "sha256:" + "0" * 64
    legacy._path.write_bytes(json.dumps(bound_image, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    corrupted_bytes = legacy._path.read_bytes()
    reopened = DurablePlanningRevisionStore(root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)
    assert not reopened.available
    assert reopened._path.read_bytes() == corrupted_bytes

    binding_root = tmp_path / "binding-corruption"
    binding_root.mkdir()
    bound_store, bound_issuer, _legacy_actor, binding_root, binding_manifest = _accepted_h1r03_store(
        binding_root, subject_id="fuzzy",
    )
    binding_owner = binding_manifest["owner_id"]
    binding_image = json.loads(bound_store._path.read_bytes())
    binding_batch = binding_image["migration_batches"][binding_manifest["batch_id"]]
    binding_batch.pop("accepted_subject_binding", None)
    binding_batch.pop("subject_binding_receipt_id", None)
    bound_store._path.write_bytes(json.dumps(
        binding_image, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8"))
    unbound = DurablePlanningRevisionStore(
        binding_root, trusted_issuers=(bound_issuer,), clock=lambda: AUTH_NOW,
    )
    binding_principal = bound_issuer.issue(
        subject_id="owner_" + hashlib.sha256(b"binding-corruption").hexdigest()[:32],
        owner_id=binding_owner, auth_context_digest=AUTH_CONTEXT, ttl_seconds=3600,
        nonce="binding-corruption",
    )
    unbound.bind_accepted_migration_subject(
        binding_principal, batch_id=binding_manifest["batch_id"], owner_id=binding_owner,
        project_id=binding_manifest["project_id"], manifest_digest=binding_manifest["manifest_digest"],
        accepted_receipt_id=binding_batch["accepted_receipt_id"],
        idempotency_key="binding-corruption-0001",
    )
    binding_image = json.loads(unbound._path.read_bytes())
    binding_record = binding_image["migration_batches"][binding_manifest["batch_id"]]["accepted_subject_binding"]
    binding_record["subject_id"] = binding_owner
    unbound._path.write_bytes(json.dumps(
        binding_image, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8"))
    corrupted_binding_bytes = unbound._path.read_bytes()
    reopened_binding = DurablePlanningRevisionStore(
        binding_root, trusted_issuers=(bound_issuer,), clock=lambda: AUTH_NOW,
    )
    assert not reopened_binding.available
    assert reopened_binding._path.read_bytes() == corrupted_binding_bytes


def test_trusted_migration_head_binding_preserves_durable_approval_view() -> None:
    first = definition_fixture(PROJECT, ROADMAP, include_draft=False)
    second = deepcopy(first)
    roadmap = second["roadmaps"][0]
    roadmap["revision"] = 2
    roadmap["title"] = "Unapproved current head"
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    second["project"]["latest_approved_revision"][ROADMAP] = {
        "revision": 2,
        "content_hash": roadmap["content_hash"],
    }
    accepted_id = "planning-revision:accepted-0001"
    current_id = "planning-revision:current-0002"
    records = [
        (OWNER, first, "durable:" + accepted_id),
        (OWNER, second, "durable:" + current_id),
    ]
    binding = {
        "owner_id": OWNER,
        "project_id": PROJECT,
        "roadmap_id": ROADMAP,
        "accepted_revision_id": accepted_id,
        "accepted_revision": 1,
        "accepted_content_sha256": first["roadmaps"][0]["content_hash"],
        "current_revision_id": current_id,
        "current_revision": 2,
        "current_content_sha256": roadmap["content_hash"],
        "approved_revision_id": accepted_id,
        "approved_revision": 1,
        "approved_content_sha256": first["roadmaps"][0]["content_hash"],
    }

    view = PlanningRevisionStore(records, trusted_head_bindings=[binding])
    assert view.get_roadmap(OWNER, PROJECT, ROADMAP)["roadmap"]["revision"] == 1
    assert view.get_roadmap(OWNER, PROJECT, ROADMAP, revision=2)["roadmap"]["revision"] == 2
    summary = view.get_project(OWNER, PROJECT)["roadmaps"][0]
    assert summary["newest_revision"] == 2
    assert summary["latest_approved_revision"] == 1

    malformed = deepcopy(binding)
    malformed["approved_revision_id"] = "planning-revision:foreign-0001"
    with pytest.raises(PlanningRevisionStoreError):
        PlanningRevisionStore(records, trusted_head_bindings=[malformed])

    huge_revision = deepcopy(binding)
    huge_revision["current_revision"] = 2**63 - 1
    with pytest.raises(PlanningRevisionStoreError):
        PlanningRevisionStore(records, trusted_head_bindings=[huge_revision])

    with pytest.raises(PlanningRevisionStoreError):
        PlanningRevisionStore(
            records,
            trusted_head_bindings=(item for item in [binding]),
        )


@pytest.mark.parametrize("generation", [True, 0, 2**63])
def test_augmented_trusted_migration_head_binding_requires_exact_generation(generation) -> None:
    first = definition_fixture(PROJECT, ROADMAP, include_draft=False)
    accepted_id = "planning-revision:accepted-0001"
    binding = {
        "owner_id": OWNER,
        "project_id": PROJECT,
        "roadmap_id": ROADMAP,
        "accepted_revision_id": accepted_id,
        "accepted_revision": 1,
        "accepted_content_sha256": first["roadmaps"][0]["content_hash"],
        "current_revision_id": accepted_id,
        "current_revision": 1,
        "current_content_sha256": first["roadmaps"][0]["content_hash"],
        "approved_revision_id": accepted_id,
        "approved_revision": 1,
        "approved_content_sha256": first["roadmaps"][0]["content_hash"],
        "generation": generation,
    }
    records = [(OWNER, first, "durable:" + accepted_id)]
    with pytest.raises(PlanningRevisionStoreError):
        PlanningRevisionStore(records, trusted_head_bindings=[binding])

    class IntSubclass(int):
        pass

    binding["generation"] = IntSubclass(1)
    with pytest.raises(PlanningRevisionStoreError):
        PlanningRevisionStore(records, trusted_head_bindings=[binding])

    binding["generation"] = 1
    PlanningRevisionStore(records, trusted_head_bindings=[binding])
    binding["unknown"] = "rejected"
    with pytest.raises(PlanningRevisionStoreError):
        PlanningRevisionStore(records, trusted_head_bindings=[binding])


def test_subject_bound_generation_is_actual_and_mcp_capture_requires_augmented_heads(
    tmp_path: Path, monkeypatch,
) -> None:
    store, issuer, principal, _root, manifest = _accepted_h1r03_store(tmp_path)
    roadmap_id = manifest["source_items"][0]["roadmap_id"]
    before = store.get_head(
        principal, owner_id="fuzzy", project_id="harbour-one", roadmap_id=roadmap_id,
    )
    approved = store.approve_revision(
        principal, owner_id="fuzzy", project_id="harbour-one", roadmap_id=roadmap_id,
        revision_id=before.current_revision_id, expected_head=before,
        idempotency_key="subject-generation-approval-0001",
    )
    assert approved.after_head.generation == 2
    assert approved.after_head.current_revision_id == before.current_revision_id

    _batch, records, legacy_heads = store.read_accepted_migration_records(
        principal, owner_id="fuzzy", project_id="harbour-one",
    )
    stable_owner, batch, subject_records, subject_heads = (
        store.read_subject_bound_accepted_migration_records(
            principal, project_id="harbour-one",
        )
    )
    assert stable_owner == "fuzzy"
    assert subject_records == records
    assert all(set(head) == {
        "owner_id", "project_id", "roadmap_id", "accepted_revision_id",
        "accepted_revision", "accepted_content_sha256", "current_revision_id",
        "current_revision", "current_content_sha256", "approved_revision_id",
        "approved_revision", "approved_content_sha256",
    } for head in legacy_heads)
    augmented = next(head for head in subject_heads if head["roadmap_id"] == roadmap_id)
    assert augmented["generation"] == 2
    assert augmented["current_revision"] == 1

    identity = McpAuthenticatedIdentity(
        token_id="planning-generation-test-token", owner_alias="fuzzy",
        owner_subject_id=principal.subject_id, cache_generation=1,
        security_action_revision=1,
    )
    runtime = AppOwnedPlanningRuntime(
        repo_root=str(tmp_path), legacy_store=PlanningRevisionStore([]),
        durable_store=store, issuer=issuer,
    )
    snapshot = runtime.capture_inbox_snapshot(identity=identity, limit=100)
    assert snapshot is not None

    def legacy_reader(*_args, **_kwargs):
        return stable_owner, batch, subject_records, legacy_heads

    monkeypatch.setattr(store, "read_subject_bound_accepted_migration_records", legacy_reader)
    with pytest.raises(McpDispatchFailure) as missing:
        runtime.capture_inbox_snapshot(identity=identity, limit=100)
    assert missing.value.code == "planning_failed"


def test_pre_identity_prepared_batch_cannot_accept_without_persisted_manifest_authority(
    tmp_path: Path,
) -> None:
    store, issuer, _principal, root = _durable_authority(tmp_path)
    principal = issuer.issue(
        subject_id="fuzzy", owner_id="fuzzy", auth_context_digest=AUTH_CONTEXT,
        ttl_seconds=3600, nonce="legacy-prepared-h1r03",
    )
    manifest = build_h1r03_migration_manifest(Path(__file__).resolve().parents[1])
    store.prepare_migration(principal, manifest=manifest)
    path = root / durable_module._HARBOUR_STATE_FILE
    image = json.loads(path.read_bytes())
    image["migration_batches"][manifest["batch_id"]].pop("manifest_identity")
    path.write_bytes(json.dumps(image, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    before = path.read_bytes()
    restarted = DurablePlanningRevisionStore(root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)

    _assert_detached_store_error(lambda: restarted.accept_migration(
        principal, batch_id=manifest["batch_id"], current_manifest=lambda: deepcopy(manifest),
    ), "migration_required")
    assert restarted.available
    assert path.read_bytes() == before


@pytest.mark.parametrize("phase", ["prepare", "accept", "abort", "rollback"])
@pytest.mark.parametrize("load_path", ["constructor", "refresh"])
def test_legacy_migration_subject_quarantine_including_head_free_batches(
    tmp_path: Path, monkeypatch, phase: str, load_path: str,
) -> None:
    """Historical ambiguous batches remain byte-preserving quarantine evidence."""
    store, issuer, principal, root = _durable_authority(tmp_path)
    operator = issuer.issue(
        subject_id="migration-operator", owner_id="fuzzy",
        auth_context_digest=AUTH_CONTEXT, ttl_seconds=3600, nonce="legacy-migration-nonce",
    )
    manifest = _legacy_colon_migration_manifest()
    # Recreate the prior admission contract only while producing a verified
    # persisted image. The production H1R03 validator and classifier are restored
    # before either constructor or refresh is exercised.
    with monkeypatch.context() as legacy_writer:
        legacy_writer.setattr(durable_module, "_validated_migration_manifest", lambda _value: deepcopy(manifest))
        legacy_writer.setattr(durable_module, "_legacy_key_quarantine_required", lambda _value: False)
        prepared = store.prepare_migration(operator, manifest=manifest)
        if phase in {"accept", "rollback"}:
            accepted = _accept_migration(store, operator, manifest)
            if phase == "rollback":
                store.rollback_migration(operator, batch_id=manifest["batch_id"])
        elif phase == "abort":
            store.abort_migration(operator, batch_id=manifest["batch_id"])

    path = root / durable_module._HARBOUR_STATE_FILE
    before = path.read_bytes()
    assert before
    if phase in {"prepare", "abort", "rollback"}:
        # These persisted phases carry migration subjects but no current heads.
        image = json.loads(before)
        assert not image["heads"]

    if load_path == "constructor":
        quarantined = DurablePlanningRevisionStore(root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)
        assert not quarantined.available
    else:
        quarantined = store
        assert quarantined.available

    _assert_detached_store_error(lambda: quarantined.get_head(
        operator, owner_id="fuzzy", project_id="harbour-one", roadmap_id="safe-query-roadmap",
    ), "migration_required")
    assert not quarantined.available
    assert quarantined._state == durable_module._new_harbour_state()
    assert path.read_bytes() == before


@pytest.mark.parametrize("location", [
    "heads", "head_fences", "idempotency", "expected_subjects", "accepted_subjects",
    "before_subjects", "after_subjects",
])
def test_quarantine_raw_key_classifier_covers_each_legacy_location(location: str) -> None:
    # Isolate each raw-key location; complete replay couples these locations in real images.
    for key, expected in (("owner:project:roadmap", False), ("owner:project:roadmap:extra", True)):
        image = durable_module._new_harbour_state()
        if location == "idempotency":
            key = "owner:key:extra" if expected else "owner:key"
        if location in {"heads", "head_fences", "idempotency"}:
            image[location][key] = None
        elif location in {"expected_subjects", "accepted_subjects"}:
            image["migration_batches"]["batch"] = {"expected_subjects": [], "accepted_subjects": []}
            image["migration_batches"]["batch"][location] = [{"subject_key": key}]
        else:
            image["receipts"]["receipt"] = {
                "receipt_type": "planning_migration", "before_subjects": [], "after_subjects": [],
            }
            image["receipts"]["receipt"][location] = [{"subject_key": key}]
        assert durable_module._legacy_key_quarantine_required(image) is expected


@pytest.mark.parametrize("load_path", ["constructor", "refresh"])
@pytest.mark.parametrize("corruption", ["heads", "head_fences", "idempotency", "receipt", "schema"])
def test_invalid_legacy_image_precedes_quarantine(
    tmp_path: Path, monkeypatch, load_path: str, corruption: str,
) -> None:
    store, issuer, principal, path, image = _legacy_quarantine_image(tmp_path, monkeypatch)
    if corruption in {"heads", "head_fences", "idempotency"}:
        previous = next(iter(image[corruption]))
        image[corruption]["missing-delimiters"] = image[corruption].pop(previous)
    elif corruption == "receipt":
        next(iter(image["receipts"].values()))["output_digest"] = "sha256:" + "0" * 64
    else:
        image["schema"] = "unsupported"
    path.write_text(json.dumps(image), encoding="utf-8")
    before = path.read_bytes()
    if load_path == "constructor":
        store = DurablePlanningRevisionStore(path.parent, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)
    _assert_detached_store_error(lambda: store.get_head(
        principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
    ), "validation_failed")
    assert not store.available
    assert path.read_bytes() == before


def _quarantine_public_operation(name: str, store, principal):
    shared = {"owner_id": OWNER, "project_id": PROJECT, "roadmap_id": ROADMAP}
    payload = _durable_payload(1, "Public boundary")
    # Use an exact, structurally valid head: malformed input now fails before
    # locked refresh and need not discover unpublished disk quarantine.
    expected = PlanningHead(OWNER, PROJECT, ROADMAP, "missing", "sha256:" + "0" * 64, None, 1)
    if name == "create_project":
        return lambda: store.create_project(principal, **shared, payload_bytes=payload, idempotency_key="public-create-0001")
    if name == "append_revision":
        return lambda: store.append_revision(principal, **shared, payload_bytes=payload, expected_head=expected, idempotency_key="public-append-0001")
    if name == "approve_revision":
        return lambda: store.approve_revision(principal, **shared, revision_id="missing", expected_head=expected, idempotency_key="public-approve-0001")
    if name == "revert_head":
        return lambda: store.revert_head(principal, **shared, ancestor_revision_id="missing", expected_head=expected, idempotency_key="public-revert-0001")
    if name == "prepare_migration":
        manifest = _quarantine_manifest()
        return lambda: store.prepare_migration(principal, manifest=manifest)
    if name in {"accept_migration", "abort_migration", "rollback_migration"}:
        args = {"batch_id": "missing"}
        if name == "accept_migration":
            args["current_manifest"] = lambda: {}
        return lambda: getattr(store, name)(principal, **args)
    if name == "get_migration_batch":
        return lambda: store.get_migration_batch(principal, owner_id=OWNER, batch_id="missing")
    if name == "get_head":
        return lambda: store.get_head(principal, **shared)
    return lambda: getattr(store, name)(principal, owner_id=OWNER, revision_id="missing")


@pytest.mark.parametrize("operation", [
    "create_project", "append_revision", "approve_revision", "revert_head", "prepare_migration",
    "accept_migration", "abort_migration", "rollback_migration", "get_migration_batch",
    "get_head", "read_revision", "read_payload",
])
@pytest.mark.parametrize("load_path", ["constructor", "refresh"])
def test_quarantine_blocks_every_public_operation_with_detached_error(
    tmp_path: Path, monkeypatch, operation: str, load_path: str,
) -> None:
    store, issuer, principal, path, _image = _legacy_quarantine_image(tmp_path, monkeypatch)
    before = path.read_bytes()
    if operation == "prepare_migration":
        principal = issuer.issue(subject_id="migration-operator", owner_id="fuzzy",
                                 auth_context_digest=AUTH_CONTEXT, ttl_seconds=3600, nonce="migration-nonce")
    if load_path == "constructor":
        store = DurablePlanningRevisionStore(path.parent, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)
    call = _quarantine_public_operation(operation, store, principal)
    _assert_detached_store_error(call, "migration_required")
    assert not store.available
    assert path.read_bytes() == before
    # Quarantine remains sticky even if a different writer later replaces the image.
    path.write_text(json.dumps(durable_module._new_harbour_state()), encoding="utf-8")
    replacement = path.read_bytes()
    _assert_detached_store_error(call, "migration_required")
    assert path.read_bytes() == replacement
    assert before != replacement


@pytest.mark.parametrize("component", ["owner_id", "project_id", "roadmap_id", "idempotency_key"])
@pytest.mark.parametrize("operation", ["create_project", "append_revision", "approve_revision", "revert_head"])
def test_new_durable_commands_reject_colon_components_before_lookup_or_digest(
    tmp_path: Path, monkeypatch, component: str, operation: str,
) -> None:
    store, _issuer, principal, root = _durable_authority(tmp_path)
    created = _durable_create(store, principal, _durable_payload(1, "Safe existing state"))
    path = root / durable_module._HARBOUR_STATE_FILE
    before = path.read_bytes()
    args = {"owner_id": OWNER, "project_id": PROJECT, "roadmap_id": ROADMAP,
            "idempotency_key": "new-command-0001"}
    args[component] += ":ambiguous"
    if operation in {"create_project", "append_revision"}:
        args["payload_bytes"] = b"deliberately invalid payload"
    if operation != "create_project":
        args["expected_head"] = created.after_head
    if operation == "approve_revision":
        args["revision_id"] = created.new_revision_id
    if operation == "revert_head":
        args["ancestor_revision_id"] = created.new_revision_id

    def forbidden(*args, **kwargs):
        pytest.fail("colon input reached authentication, lookup, digest, or persistence")

    for name in ("_authenticate", "_refresh_locked", "_revision"):
        monkeypatch.setattr(store, name, forbidden)
    for name in ("_canonical_digest", "_migration_digest", "_atomic_write_json"):
        monkeypatch.setattr(durable_module, name, forbidden)
    _assert_detached_store_error(lambda: getattr(store, operation)(principal, **args), "validation_failed")
    assert store.available
    assert path.read_bytes() == before


@pytest.mark.parametrize("component", ["owner_id", "project_id", "roadmap_id"])
def test_new_migration_rejects_colon_components_before_authentication_or_digest(
    tmp_path: Path, monkeypatch, component: str,
) -> None:
    store, _issuer, principal, root = _durable_authority(tmp_path)
    manifest = _quarantine_manifest(colon_roadmap=component == "roadmap_id")
    if component != "roadmap_id":
        manifest[component] += ":ambiguous"

    def forbidden(*args, **kwargs):
        pytest.fail("colon manifest reached authentication, lookup, digest, or persistence")

    monkeypatch.setattr(store, "_authenticate", forbidden)
    monkeypatch.setattr(store, "_refresh_locked", forbidden)
    monkeypatch.setattr(durable_module, "_migration_digest", forbidden)
    monkeypatch.setattr(durable_module, "_atomic_write_json", forbidden)
    _assert_detached_store_error(lambda: store.prepare_migration(principal, manifest=manifest), "validation_failed")
    assert store.available
    assert not (root / durable_module._HARBOUR_STATE_FILE).exists()


@pytest.mark.parametrize("location", [
    "safe", "expected_subjects", "accepted_subjects", "before_subjects", "after_subjects",
])
@pytest.mark.parametrize("load_path", ["constructor", "refresh"])
def test_safe_migration_remains_available_and_malformed_subjects_fail_validation(
    tmp_path: Path, location: str, load_path: str,
) -> None:
    store, issuer, _principal, root = _durable_authority(tmp_path)
    principal = issuer.issue(subject_id="migration-operator", owner_id="fuzzy",
                             auth_context_digest=AUTH_CONTEXT, ttl_seconds=3600, nonce="migration-nonce")
    manifest = _quarantine_manifest()
    store.prepare_migration(principal, manifest=manifest)
    accepted = _accept_migration(store, principal, manifest)
    path = root / durable_module._HARBOUR_STATE_FILE
    image = json.loads(path.read_bytes())
    if location in {"expected_subjects", "accepted_subjects"}:
        image["migration_batches"][manifest["batch_id"]][location][0]["subject_key"] = "too:few"
    elif location != "safe":
        image["receipts"][accepted.receipt_id][location][0]["subject_key"] = "too:few"
    path.write_text(json.dumps(image, indent=3), encoding="utf-8")
    before = path.read_bytes()
    if load_path == "constructor":
        store = DurablePlanningRevisionStore(root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)
    call = lambda: store.get_migration_batch(principal, owner_id="fuzzy", batch_id=manifest["batch_id"])
    if location == "safe":
        assert call()["state"] == "accepted"
        assert store.available
        assert _accept_migration(store, principal, manifest) == accepted
    else:
        _assert_detached_store_error(call, "validation_failed")
        assert not store.available
    assert path.read_bytes() == before


@pytest.mark.parametrize("operation", [
    "read_payload", "get_head", "create_project", "append_revision", "approve_revision", "revert_head",
])
def test_inflight_operation_observes_quarantine_before_consuming_state(
    tmp_path: Path, monkeypatch, operation: str,
) -> None:
    store, _issuer, principal, root = _durable_authority(tmp_path)
    created = _durable_create(store, principal, _durable_payload(1, "Concurrent source"))
    path = root / durable_module._HARBOUR_STATE_FILE
    legacy = json.loads(path.read_bytes())
    transition = legacy["transitions"][0]
    prior_scope = OWNER + ":" + transition["idempotency_key"]
    transition["idempotency_key"] += ":legacy"
    legacy["idempotency"][prior_scope + ":legacy"] = legacy["idempotency"].pop(prior_scope)
    durable_module._verify_harbour_state(legacy)
    quarantined_bytes = json.dumps(legacy).encode("utf-8")

    args = {"owner_id": OWNER, "project_id": PROJECT, "roadmap_id": ROADMAP}
    if operation == "read_payload":
        args = {"owner_id": OWNER, "revision_id": created.new_revision_id}
    elif operation != "get_head":
        args["idempotency_key"] = "concurrent-command-0001"
        if operation in {"create_project", "append_revision"}:
            args["payload_bytes"] = _durable_payload(2 if operation == "append_revision" else 1, "Pending command")
        if operation != "create_project":
            args["expected_head"] = created.after_head
        if operation == "approve_revision":
            args["revision_id"] = created.new_revision_id
        if operation == "revert_head":
            args["ancestor_revision_id"] = created.new_revision_id

    paused, resume = Event(), Event()
    hook_name = "read_revision" if operation == "read_payload" else "_read_prelude"
    original = getattr(store, hook_name)

    def pause_after_initial_read(*args, **kwargs):
        result = original(*args, **kwargs)
        paused.set()
        assert resume.wait(10), "test did not release the pending operation"
        return result

    monkeypatch.setattr(store, hook_name, pause_after_initial_read)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(_assert_detached_store_error,
                              lambda: getattr(store, operation)(principal, **args), "migration_required")
        try:
            assert paused.wait(10), "operation did not reach the state-consumption boundary"
            path.write_bytes(quarantined_bytes)
            _assert_detached_store_error(lambda: store.accept_migration(
                principal, batch_id="missing", current_manifest=lambda: {},
            ), "migration_required")
            assert store._state == durable_module._new_harbour_state()
        finally:
            resume.set()
        pending.result(timeout=10)
    assert not store.available
    assert path.read_bytes() == quarantined_bytes


@pytest.mark.parametrize("operation", [
    "create_project", "append_revision", "approve_revision", "revert_head", "prepare_migration",
    "accept_migration", "abort_migration", "rollback_migration", "get_migration_batch",
    "get_head", "read_revision", "read_payload",
])
def test_pending_refresh_cannot_downgrade_migration_required_after_corrupt_replacement(
    tmp_path: Path, monkeypatch, operation: str,
) -> None:
    store, issuer, principal, path, _image = _legacy_quarantine_image(tmp_path, monkeypatch)
    if operation == "prepare_migration":
        principal = issuer.issue(subject_id="migration-operator", owner_id="fuzzy",
                                 auth_context_digest=AUTH_CONTEXT, ttl_seconds=3600, nonce="migration-nonce")
    paused, resume = Event(), Event()
    original_authenticate = store._authenticate

    def pause_first_authenticated_operation(*args, **kwargs):
        result = original_authenticate(*args, **kwargs)
        if not paused.is_set():
            paused.set()
            assert resume.wait(10), "test did not release pending refresh"
        return result

    monkeypatch.setattr(store, "_authenticate", pause_first_authenticated_operation)
    call = _quarantine_public_operation(operation, store, principal)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(_assert_detached_store_error, call, "migration_required")
        try:
            assert paused.wait(10), "operation did not pass its initial availability check"
            _assert_detached_store_error(lambda: store.accept_migration(
                principal, batch_id="missing", current_manifest=lambda: {},
            ), "migration_required")
            path.write_bytes(b"{}")
        finally:
            resume.set()
        pending.result(timeout=10)
    _assert_detached_store_error(call, "migration_required")
    assert not store.available
    assert store._runtime.terminal_code == "migration_required"
    assert store._state == durable_module._new_harbour_state()
    assert path.read_bytes() == b"{}"


@pytest.mark.parametrize("heads", [[], None, "invalid", 7])
@pytest.mark.parametrize("load_path", ["constructor", "refresh"])
def test_malformed_v1_heads_fail_closed_without_raw_attribute_error(
    tmp_path: Path, heads, load_path: str,
) -> None:
    store, issuer, principal, root = _durable_authority(tmp_path)
    _durable_create(store, principal, _durable_payload(1, "Malformed v1 source"))
    path = root / durable_module._HARBOUR_STATE_FILE
    legacy = json.loads(path.read_bytes())
    legacy["schema"] = durable_module._HARBOUR_PLANNING_STATE_SCHEMA_V1
    legacy.pop("head_fences")
    legacy.pop("migration_batches")
    legacy["heads"] = heads
    path.write_text(json.dumps(legacy), encoding="utf-8")
    before = path.read_bytes()
    if load_path == "constructor":
        store = DurablePlanningRevisionStore(root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)
        assert not store.available
    _assert_detached_store_error(lambda: store.get_head(
        principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
    ), "validation_failed")
    _assert_detached_store_error(lambda: _durable_create(
        store, principal, _durable_payload(1, "Never replace malformed state"),
    ), "validation_failed")
    assert not store.available
    assert path.read_bytes() == before


def _pc_callback(store, hook, callback, monkeypatch):
    """Install one caller-owned action in a real dependency boundary."""
    armed = True

    def invoke():
        nonlocal armed
        if armed:
            armed = False
            assert not store._lock._is_owned(), "dependency ran under root lock"
            callback()

    if hook == "clock":
        def clock():
            invoke()
            return AUTH_NOW
        monkeypatch.setattr(store, "_clock", clock)
    elif hook == "timezone":
        class CallbackZone(tzinfo):
            def utcoffset(self, value):
                invoke()
                return timedelta(0)

            def dst(self, value):
                return timedelta(0)
        monkeypatch.setattr(store, "_clock", lambda: AUTH_NOW.replace(tzinfo=CallbackZone()))
    elif hook == "auth":
        original = durable_module.authenticate_principal

        def authenticate(*args, **kwargs):
            invoke()
            return original(*args, **kwargs)
        monkeypatch.setattr(durable_module, "authenticate_principal", authenticate)
    else:
        original = AuthenticatedPrincipalIssuer._verify

        def verify(*args, **kwargs):
            invoke()
            return original(*args, **kwargs)
        monkeypatch.setattr(AuthenticatedPrincipalIssuer, "_verify", verify)


@pytest.mark.parametrize("hook", ["clock", "timezone", "auth", "issuer"])
@pytest.mark.parametrize("other_instance", [False, True])
@pytest.mark.parametrize("cross_thread", [False, True])
@pytest.mark.parametrize("access", ["read", "write_cas", "write_replay"])
def test_parallel_dependency_access_uses_normal_cas_and_replay(
    tmp_path, monkeypatch, hook, other_instance, cross_thread, access,
):
    store, issuer, principal, root = _durable_authority(tmp_path)
    created = _durable_create(store, principal, _durable_payload(1, "Parallel source"))
    peer = DurablePlanningRevisionStore(root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW) if other_instance else store
    writes = []
    atomic_write = durable_module._atomic_write_json
    original_lock = durable_module._interprocess_repository_lock
    held = []

    @contextmanager
    def lock(target):
        with original_lock(target):
            held.append(target)
            try:
                yield
            finally:
                held.pop()

    def write(*args):
        writes.append(args[0])
        return atomic_write(*args)

    monkeypatch.setattr(durable_module, "_interprocess_repository_lock", lock)
    monkeypatch.setattr(durable_module, "_atomic_write_json", write)
    args = dict(owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
                revision_id=created.new_revision_id, expected_head=created.after_head,
                idempotency_key="parallel-approve-outer")
    inner_results = []
    inner_errors = []

    def inner():
        try:
            if access == "read":
                inner_results.append(peer.read_payload(principal, owner_id=OWNER, revision_id=created.new_revision_id))
            else:
                inner_args = dict(args)
                if access == "write_cas":
                    inner_args["idempotency_key"] = "parallel-approve-inner"
                inner_results.append(peer.approve_revision(principal, **inner_args))
        except BaseException as error:
            inner_errors.append(error)

    def callback():
        assert not held, "dependency ran under OS lock"
        if cross_thread:
            thread = Thread(target=inner, daemon=True)
            thread.start()
            thread.join(5)
            assert not thread.is_alive(), "same-root command could not make progress"
        else:
            inner()

    _pc_callback(store, hook, callback, monkeypatch)
    if access == "write_cas":
        _assert_detached_store_error(lambda: store.approve_revision(principal, **args), "stale_authority")
    else:
        result = store.approve_revision(principal, **args)
        if access == "write_replay":
            assert result == inner_results[0]
    assert not inner_errors
    assert len(inner_results) == 1
    assert len(writes) == 1
    head = peer.get_head(principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP)
    assert head.generation == created.after_head.generation + 1
    assert head.approved_revision_id == created.new_revision_id


def _pc_mutation(tmp_path, phase):
    store, issuer, principal, root = _durable_authority(tmp_path)
    if "migration" in phase:
        principal = issuer.issue(subject_id="migration-operator", owner_id="fuzzy",
                                 auth_context_digest=AUTH_CONTEXT, ttl_seconds=3600, nonce="parallel-migration")
        manifest = _quarantine_manifest()
        if phase != "prepare_migration":
            store.prepare_migration(principal, manifest=manifest)
        if phase == "rollback_migration":
            _accept_migration(store, principal, manifest)
        if phase == "prepare_migration":
            call = lambda: store.prepare_migration(principal, manifest=manifest)
        elif phase == "accept_migration":
            call = lambda: _accept_migration(store, principal, manifest)
        else:
            call = lambda: getattr(store, phase)(principal, batch_id=manifest["batch_id"])
    elif phase == "create_project":
        call = lambda: _durable_create(store, principal, _durable_payload(1, "Sample once"))
    else:
        created = _durable_create(store, principal, _durable_payload(1, "Sample source"))
        args = dict(owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
                    expected_head=created.after_head, idempotency_key="sample-command-0001")
        if phase == "append_revision":
            args["payload_bytes"] = _durable_payload(2, "Sample appended")
        elif phase == "approve_revision":
            args["revision_id"] = created.new_revision_id
        else:
            args["ancestor_revision_id"] = created.new_revision_id
        call = lambda: getattr(store, phase)(principal, **args)
    return store, issuer, principal, root, call


_PC_MUTATIONS = ["create_project", "append_revision", "approve_revision", "revert_head",
                 "prepare_migration", "accept_migration", "abort_migration", "rollback_migration"]


@pytest.mark.parametrize("phase", _PC_MUTATIONS)
def test_every_mutation_samples_once_before_lock_and_writes_once_or_replays(
    tmp_path, monkeypatch, phase,
):
    store, _issuer, _principal, _root, call = _pc_mutation(tmp_path, phase)
    samples, authentications, writes = [], [], []
    original_auth = durable_module.authenticate_principal
    original_write = durable_module._atomic_write_json

    def clock():
        assert not store._lock._is_owned()
        samples.append(True)
        return AUTH_NOW

    def authenticate(*args, **kwargs):
        assert not store._lock._is_owned()
        authentications.append(True)
        return original_auth(*args, **kwargs)

    def write(*args):
        writes.append(True)
        return original_write(*args)

    monkeypatch.setattr(store, "_clock", clock)
    monkeypatch.setattr(durable_module, "authenticate_principal", authenticate)
    monkeypatch.setattr(durable_module, "_atomic_write_json", write)
    receipt = call()
    assert len(samples) == len(authentications) == len(writes) == 1
    replay = call()
    assert receipt == replay
    assert len(samples) == len(authentications) == 2
    assert len(writes) == 1


@pytest.mark.parametrize("phase", _PC_MUTATIONS)
@pytest.mark.parametrize("hook", ["clock", "timezone", "auth"])
def test_callback_terminal_from_peer_blocks_every_write_phase(
    tmp_path, monkeypatch, phase, hook,
):
    store, issuer, principal, root, call = _pc_mutation(tmp_path, phase)
    # Build a structurally verified legacy image independently of the target.
    fixture = tmp_path / "legacy-fixture"
    fixture.mkdir()
    _legacy_store, _issuer, _principal, legacy_path, _image = _legacy_quarantine_image(fixture, monkeypatch)
    legacy_bytes = legacy_path.read_bytes()
    path = root / durable_module._HARBOUR_STATE_FILE
    peers = []

    def publish():
        path.write_bytes(legacy_bytes)
        peer = DurablePlanningRevisionStore(root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)
        assert peer.available is False
        peers.append(peer)

    def reject_write(*args):
        pytest.fail("terminal command reached atomic writer")

    monkeypatch.setattr(durable_module, "_atomic_write_json", reject_write)
    _pc_callback(store, hook, publish, monkeypatch)
    _assert_detached_store_error(call, "migration_required")
    assert store.available is False
    assert len(peers) == 1
    assert peers[0]._runtime is store._runtime
    assert path.read_bytes() == legacy_bytes
    # Once published, replacing the file cannot heal or downgrade the root.
    path.write_bytes(b"{}")
    _assert_detached_store_error(call, "migration_required")
    reopened = DurablePlanningRevisionStore(root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)
    assert reopened.available is False
    assert reopened._runtime.terminal_code == "migration_required"
    assert path.read_bytes() == b"{}"


@pytest.mark.parametrize("failure", ["raised_terminal", "raised_runtime", "naive", "bad_timezone"])
def test_dependency_failure_is_detached_without_publishing_terminal(tmp_path, monkeypatch, failure):
    store, _issuer, principal, root = _durable_authority(tmp_path)

    class BadZone(tzinfo):
        def utcoffset(self, value):
            raise RuntimeError("private timezone failure")

    def clock():
        if failure == "raised_terminal":
            raise DurablePlanningStoreError("migration_required")
        if failure == "raised_runtime":
            raise RuntimeError("private dependency failure")
        if failure == "naive":
            return AUTH_NOW.replace(tzinfo=None)
        return AUTH_NOW.replace(tzinfo=BadZone())

    monkeypatch.setattr(store, "_clock", clock)
    _assert_detached_store_error(lambda: _durable_create(store, principal, _durable_payload(1, "Never written")), "unauthenticated")
    assert store.available is True
    assert not (root / durable_module._HARBOUR_STATE_FILE).exists()
    monkeypatch.setattr(store, "_clock", lambda: AUTH_NOW)
    assert _durable_create(store, principal, _durable_payload(1, "Still available"))


@pytest.mark.parametrize("field", ["owner_id", "project_id", "roadmap_id", "current_revision_id",
                                  "current_content_sha256", "approved_revision_id", "generation"])
def test_hostile_head_scalars_are_rejected_before_hooks_or_locked_refresh(tmp_path, monkeypatch, field):
    store, _issuer, principal, _root = _durable_authority(tmp_path)
    created = _durable_create(store, principal, _durable_payload(1, "Exact head"))
    touched = []

    class HostileStr(str):
        def __eq__(self, other):
            touched.append("equality")
            raise RuntimeError("private comparison")

    class HostileInt(int):
        def __eq__(self, other):
            touched.append("equality")
            raise RuntimeError("private comparison")

    fields = durable_module._head_dict(created.after_head)
    fields[field] = HostileInt(1) if field == "generation" else HostileStr(fields[field] or "approved")
    head = PlanningHead(**fields)

    def reject_refresh():
        pytest.fail("hostile head reached locked refresh")

    monkeypatch.setattr(store, "_refresh_locked", reject_refresh)
    _assert_detached_store_error(lambda: store.append_revision(
        principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
        payload_bytes=_durable_payload(2, "Never written"), expected_head=head,
        idempotency_key="hostile-head-0001",
    ), "validation_failed")
    assert not touched
    assert store.available is True


@pytest.mark.parametrize("kind", ["mapping", "sequence", "string", "key", "cycle"])
def test_hostile_manifest_values_are_rejected_before_serialization(tmp_path, monkeypatch, kind):
    store, _issuer, principal, _root = _durable_authority(tmp_path)
    manifest = _quarantine_manifest()
    touched = []

    class HostileDict(dict):
        def items(self):
            touched.append("items")
            raise RuntimeError("private mapping")

    class HostileList(list):
        def __iter__(self):
            touched.append("iteration")
            raise RuntimeError("private sequence")

    class HostileStr(str):
        def __hash__(self):
            return str.__hash__(self)

        def __eq__(self, other):
            touched.append("equality")
            raise RuntimeError("private string")

    if kind == "mapping":
        manifest["source_items"][0] = HostileDict(manifest["source_items"][0])
    elif kind == "sequence":
        manifest["source_items"] = HostileList(manifest["source_items"])
    elif kind == "string":
        manifest["source_items"][0]["role"] = HostileStr("primary")
    elif kind == "key":
        manifest["source_items"][0][HostileStr("hostile-key")] = "value"
    else:
        manifest["source_items"].append(manifest)

    def reject(*args, **kwargs):
        pytest.fail("hostile manifest reached authentication or serialization")

    monkeypatch.setattr(store, "_authenticate", reject)
    monkeypatch.setattr(durable_module, "_canonical_planning_bytes", reject)
    _assert_detached_store_error(lambda: store.prepare_migration(principal, manifest=manifest), "validation_failed")
    assert not touched


@pytest.mark.parametrize("field", ["issuer_id", "subject_id", "owner_id", "auth_context_digest", "principal_digest", "subclass"])
def test_authentication_result_is_frozen_before_state_consumption(tmp_path, monkeypatch, field):
    store, _issuer, principal, _root = _durable_authority(tmp_path)
    ref = durable_module.authenticate_principal(principal, trusted_issuers=store._trusted_issuers, now=AUTH_NOW)
    values = {name: getattr(ref, name) for name in ("issuer_id", "subject_id", "owner_id", "auth_context_digest", "principal_digest")}
    touched = []

    class HostileStr(str):
        def __eq__(self, other):
            touched.append(True)
            raise RuntimeError("private principal comparison")

    class RefSubclass(durable_module.PrincipalRef):
        pass

    if field == "subclass":
        ref = RefSubclass(**values)
    else:
        values[field] = HostileStr(values[field])
        ref = durable_module.PrincipalRef(**values)
    monkeypatch.setattr(durable_module, "authenticate_principal", lambda *args, **kwargs: ref)

    def reject():
        pytest.fail("hostile principal reached state consumption")

    monkeypatch.setattr(store, "_refresh_locked", reject)
    _assert_detached_store_error(lambda: _durable_create(store, principal, _durable_payload(1, "Never written")), "validation_failed")
    assert not touched
    assert store.available is True


@pytest.mark.parametrize("operation", _PC_MUTATIONS + ["get_head", "get_migration_batch", "read_revision", "read_payload"])
def test_peer_constructor_terminal_is_shared_by_all_public_operations(tmp_path, monkeypatch, operation):
    store, issuer, principal, path, _image = _legacy_quarantine_image(tmp_path, monkeypatch)
    before = path.read_bytes()
    peer = DurablePlanningRevisionStore(store.root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)
    assert peer.available is False
    assert store.available is False
    assert store._runtime is peer._runtime
    _assert_detached_store_error(_quarantine_public_operation(operation, store, principal), "migration_required")
    assert path.read_bytes() == before


@pytest.mark.parametrize("phase", _PC_MUTATIONS)
def test_terminal_rechecked_after_candidate_verification_before_atomic_write(tmp_path, monkeypatch, phase):
    store, issuer, _principal, root, call = _pc_mutation(tmp_path, phase)
    fixture = tmp_path / "final-boundary-legacy"
    fixture.mkdir()
    _old, _issuer, _principal, old_path, _image = _legacy_quarantine_image(fixture, monkeypatch)
    legacy_bytes = old_path.read_bytes()
    target = root / durable_module._HARBOUR_STATE_FILE
    old_count = len(store._state["transitions"])
    original_verify = durable_module._verify_harbour_state
    original_ensure = store._ensure_available
    candidate_verified = []
    published = []

    def verify(state):
        result = original_verify(state)
        if len(state["transitions"]) > old_count:
            candidate_verified.append(True)
        return result

    def ensure_available():
        if candidate_verified and not published:
            # An internal test seam at the final boundary, not a configured
            # dependency callback. The shared RLock permits this nested replay.
            published.append(True)
            target.write_bytes(legacy_bytes)
            peer = DurablePlanningRevisionStore(root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)
            assert peer.available is False
        return original_ensure()

    def reject_write(*args):
        pytest.fail("published terminal reached atomic write")

    monkeypatch.setattr(durable_module, "_verify_harbour_state", verify)
    monkeypatch.setattr(store, "_ensure_available", ensure_available)
    monkeypatch.setattr(durable_module, "_atomic_write_json", reject_write)
    _assert_detached_store_error(call, "migration_required")
    assert published == [True]
    assert target.read_bytes() == legacy_bytes
    assert store.available is False


def _deep_planning_json(container):
    prefix, suffix = (b"[", b"]") if container == "array" else (b'{"x":', b"}")
    payload = b'{"x":' + prefix * 1500 + b"0" + suffix * 1500 + b"}"
    assert len(payload) < 4 * 1024 * 1024
    return payload


@pytest.mark.parametrize("container", ["array", "object"])
@pytest.mark.parametrize("operation", ["create_project", "append_revision", "prepare_migration"])
def test_deep_json_input_is_detached_without_poisoning_root(tmp_path, monkeypatch, container, operation):
    store, _issuer, principal, root = _durable_authority(tmp_path)
    payload = _deep_planning_json(container)
    path = root / durable_module._HARBOUR_STATE_FILE
    if operation == "append_revision":
        created = _durable_create(store, principal, _durable_payload(1, "Deep input source"))
        call = lambda: store.append_revision(
            principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
            expected_head=created.after_head, payload_bytes=payload,
            idempotency_key="deep-input-append",
        )
    elif operation == "prepare_migration":
        manifest = _quarantine_manifest()
        roadmap = manifest["source_items"][0]["roadmap_id"]
        manifest["normalized_payloads"][roadmap] = payload.decode("utf-8")
        call = lambda: store.prepare_migration(principal, manifest=manifest)
    else:
        call = lambda: _durable_create(store, principal, payload)
    before = path.read_bytes() if path.exists() else None

    def reject_write(*args):
        pytest.fail("deep invalid input reached writer")

    monkeypatch.setattr(durable_module, "_atomic_write_json", reject_write)
    _assert_detached_store_error(call, "validation_failed")
    assert store.available is True
    assert (path.read_bytes() if path.exists() else None) == before


@pytest.mark.parametrize("container", ["array", "object"])
@pytest.mark.parametrize("location", ["outer_image", "revision_payload", "migration_payload"])
@pytest.mark.parametrize("load_path", ["constructor", "get_head", "refresh"])
def test_deep_persisted_json_publishes_shared_terminal_and_preserves_bytes(
    tmp_path, monkeypatch, container, location, load_path,
):
    store, issuer, principal, root = _durable_authority(tmp_path)
    payload = _deep_planning_json(container)
    if location == "migration_payload":
        operator = issuer.issue(subject_id="migration-operator", owner_id="fuzzy",
                                auth_context_digest=AUTH_CONTEXT, ttl_seconds=3600, nonce="deep-migration")
        manifest = _quarantine_manifest()
        store.prepare_migration(operator, manifest=manifest)
    else:
        _durable_create(store, principal, _durable_payload(1, "Deep replay source"))
    path = root / durable_module._HARBOUR_STATE_FILE
    state = json.loads(path.read_bytes())
    peer = DurablePlanningRevisionStore(root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)
    if location == "outer_image":
        corrupted = payload
    else:
        if location == "revision_payload":
            revision = next(iter(state["revisions"].values()))
            revision["payload_b64"] = durable_module.base64.b64encode(payload).decode("ascii")
            revision["payload_sha256"] = durable_module._bytes_digest(payload)
        else:
            batch = next(iter(state["migration_batches"].values()))
            roadmap = next(iter(batch["normalized_payloads"]))
            batch["normalized_payloads"][roadmap] = payload.decode("utf-8")
        corrupted = json.dumps(state).encode("utf-8")
    path.write_bytes(corrupted)

    def reject_write(*args):
        pytest.fail("malformed replay reached writer")

    monkeypatch.setattr(durable_module, "_atomic_write_json", reject_write)
    if load_path == "constructor":
        reopened = DurablePlanningRevisionStore(root, trusted_issuers=(issuer,), clock=lambda: AUTH_NOW)
        assert reopened.available is False
    elif load_path == "get_head":
        _assert_detached_store_error(lambda: store.get_head(
            principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
        ), "validation_failed")
    else:
        with store._lock:
            _assert_detached_store_error(store._refresh_locked, "validation_failed")
    assert store.available is False
    assert peer.available is False
    assert store._runtime.terminal_code == "validation_failed"
    for operation in _PC_MUTATIONS + ["get_head", "get_migration_batch", "read_revision", "read_payload"]:
        _assert_detached_store_error(_quarantine_public_operation(operation, peer, principal), "validation_failed")
    assert path.read_bytes() == corrupted


def test_append_transaction_validates_new_and_old_payload_once_across_both_images(
    tmp_path, monkeypatch,
):
    store, _issuer, principal, root = _durable_authority(tmp_path)
    old_payload = _durable_payload(1, "Append transaction source")
    created = _durable_create(
        store, principal, old_payload, key="append-transaction-count-create-0001",
    )
    new_payload = _durable_payload(2, "Append transaction candidate")
    original_parse = durable_module._strict_json_object
    original_validate = durable_module.validate_planning_definition
    original_canonical = durable_module._canonical_planning_bytes
    original_verify = durable_module._verify_harbour_state
    payload_parses = []
    validations = []
    canonicalizations = []
    verified_images = []

    def parse(raw):
        if raw in {old_payload, new_payload}:
            payload_parses.append(raw)
        return original_parse(raw)

    def validate(document):
        validations.append(document["roadmaps"][0]["title"])
        return original_validate(document)

    def canonical(document):
        encoded = original_canonical(document)
        if encoded in {old_payload, new_payload}:
            canonicalizations.append(encoded)
        return encoded

    def verify(state):
        result = original_verify(state)
        verified_images.append((
            len(state["revisions"]),
            len(state["transitions"]),
            durable_module._canonical_digest(state),
        ))
        return result

    monkeypatch.setattr(durable_module, "_strict_json_object", parse)
    monkeypatch.setattr(durable_module, "validate_planning_definition", validate)
    monkeypatch.setattr(durable_module, "_canonical_planning_bytes", canonical)
    monkeypatch.setattr(durable_module, "_verify_harbour_state", verify)
    appended = store.append_revision(
        principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
        payload_bytes=new_payload, expected_head=created.after_head,
        idempotency_key="append-transaction-count-append-0001",
    )

    assert payload_parses.count(old_payload) == payload_parses.count(new_payload) == 1
    assert validations.count("Append transaction source") == 1
    assert validations.count("Append transaction candidate") == 1
    assert len(validations) == 2
    assert canonicalizations.count(old_payload) == canonicalizations.count(new_payload) == 1
    assert len(canonicalizations) == 2
    assert [(revisions, transitions) for revisions, transitions, _digest in verified_images] == [
        (1, 1), (2, 2),
    ]
    assert verified_images[0][2] != verified_images[1][2]
    persisted = json.loads((root / durable_module._HARBOUR_STATE_FILE).read_bytes())
    assert persisted["heads"][f"{OWNER}:{PROJECT}:{ROADMAP}"]["current_revision_id"] == appended.new_revision_id


@pytest.mark.parametrize(
    "target",
    ["persisted-head", "candidate-metadata", "candidate-head", "candidate-receipt", "candidate-transition"],
    ids=["persisted-head", "candidate-metadata", "candidate-head", "candidate-receipt", "candidate-transition"],
)
def test_append_transaction_rejects_full_image_integrity_tamper_before_write(
    tmp_path, monkeypatch, target,
):
    store, _issuer, principal, root = _durable_authority(tmp_path)
    created = _durable_create(
        store, principal, _durable_payload(1, "Append integrity source"),
        key="append-integrity-create-0001",
    )
    path = root / durable_module._HARBOUR_STATE_FILE
    before = path.read_bytes()
    original_verify = durable_module._verify_harbour_state
    calls = []

    def verify(state):
        calls.append(len(state["transitions"]))
        is_candidate = len(calls) == 2
        if target == "persisted-head" and not is_candidate:
            next(iter(state["heads"].values()))["generation"] += 1
        elif target == "candidate-metadata" and is_candidate:
            revision_id = state["transitions"][-1]["new_revision_id"]
            state["revisions"][revision_id]["principal_ref"]["owner_id"] = "mallory"
        elif target == "candidate-head" and is_candidate:
            next(iter(state["heads"].values()))["current_content_sha256"] = "sha256:" + "0" * 64
        elif target == "candidate-receipt" and is_candidate:
            receipt_id = state["transitions"][-1]["receipt_id"]
            state["receipts"][receipt_id]["output_digest"] = "sha256:" + "0" * 64
        elif target == "candidate-transition" and is_candidate:
            state["transitions"][-1]["command_digest"] = "sha256:" + "0" * 64
        return original_verify(state)

    def reject_write(*_args):
        pytest.fail("invalid append candidate reached atomic write")

    monkeypatch.setattr(durable_module, "_verify_harbour_state", verify)
    monkeypatch.setattr(durable_module, "_atomic_write_json", reject_write)
    _assert_detached_store_error(lambda: store.append_revision(
        principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
        payload_bytes=_durable_payload(2, f"Append integrity {target}"),
        expected_head=created.after_head,
        idempotency_key=f"append-integrity-{target}-0001",
    ), "validation_failed")
    assert calls == ([1] if target == "persisted-head" else [1, 2])
    assert path.read_bytes() == before


@pytest.mark.parametrize("wire_form", ["canonical", "noncanonical"], ids=["canonical", "noncanonical"])
def test_append_transaction_binds_exact_payload_key_and_returns_only_detached_documents(
    tmp_path, monkeypatch, wire_form,
):
    store, _issuer, principal, _root = _durable_authority(tmp_path)
    old_payload = _durable_payload(1, "Append exact key source")
    created = _durable_create(
        store, principal, old_payload, key="append-exact-key-create-0001",
    )
    canonical = _durable_payload(2, "Append exact key candidate")
    payload = canonical if wire_form == "canonical" else json.dumps(
        json.loads(canonical), ensure_ascii=False, allow_nan=False, indent=1,
    ).encode("utf-8")
    assert (payload == canonical) is (wire_form == "canonical")
    original_checkout = durable_module._checkout_planning_payload_validation_run
    original_validate = durable_module.validate_planning_definition
    checkouts = []
    validations = []

    def validate(document):
        validations.append(durable_module._canonical_planning_bytes(document))
        return original_validate(document)

    def checkout(run, value, project_id, roadmap_id):
        result = original_checkout(run, value, project_id, roadmap_id)
        checkouts.append((run, value, project_id, roadmap_id, result[1], result[2]))
        return result

    monkeypatch.setattr(durable_module, "validate_planning_definition", validate)
    monkeypatch.setattr(durable_module, "_checkout_planning_payload_validation_run", checkout)
    store.append_revision(
        principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
        payload_bytes=payload, expected_head=created.after_head,
        idempotency_key=f"append-exact-key-{wire_form}-0001",
    )

    assert [item[1] for item in checkouts] == [payload, old_payload, old_payload, payload]
    assert all(item[2:4] == (PROJECT, ROADMAP) for item in checkouts)
    assert len({id(item[0]) for item in checkouts}) == 1
    assert len({id(item[4]) for item in checkouts}) == len(checkouts)
    assert all(item[5] is item[4]["roadmaps"][0] for item in checkouts)
    checkouts[0][4]["roadmaps"][0]["title"] = "detached mutation"
    assert all(
        item[4]["roadmaps"][0]["title"] != "detached mutation"
        for item in checkouts[1:]
    )
    expected_new_validations = 1 if wire_form == "canonical" else 2
    assert validations.count(canonical) == expected_new_validations
    captured_run = checkouts[0][0]
    _assert_private_store_error(
        lambda: captured_run.checkout(payload, PROJECT, ROADMAP), "validation_failed",
    )


@pytest.mark.parametrize(
    "outcome",
    ["success", "replay", "stale", "conflict", "candidate-failure", "write-failure"],
    ids=["success", "replay", "stale", "conflict", "candidate-failure", "write-failure"],
)
def test_append_transaction_discards_private_run_on_every_exit(
    tmp_path, monkeypatch, outcome,
):
    store, _issuer, principal, root = _durable_authority(tmp_path)
    created = _durable_create(
        store, principal, _durable_payload(1, "Append discard source"),
        key="append-discard-create-0001",
    )
    payload = _durable_payload(2, "Append discard candidate")
    key = "append-discard-target-0001"
    expected_head = created.after_head
    if outcome in {"replay", "conflict"}:
        committed = store.append_revision(
            principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
            payload_bytes=payload, expected_head=expected_head, idempotency_key=key,
        )
    elif outcome == "stale":
        store.append_revision(
            principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
            payload_bytes=payload, expected_head=expected_head,
            idempotency_key="append-discard-advance-0001",
        )
        payload = _durable_payload(3, "Append discard stale")
        key = "append-discard-stale-0001"

    path = root / durable_module._HARBOUR_STATE_FILE
    before = path.read_bytes()
    original_checkout = durable_module._checkout_planning_payload_validation_run
    original_seal = durable_module._seal_planning_payload_validation_run
    captured = []
    seals = []

    def checkout(run, value, project_id, roadmap_id):
        if not captured:
            captured.append(run)
        return original_checkout(run, value, project_id, roadmap_id)

    def seal(run):
        seals.append(run)
        return original_seal(run)

    monkeypatch.setattr(durable_module, "_checkout_planning_payload_validation_run", checkout)
    monkeypatch.setattr(durable_module, "_seal_planning_payload_validation_run", seal)
    if outcome == "conflict":
        payload = _durable_payload(2, "Append discard conflict")
    if outcome == "candidate-failure":
        original_verify = durable_module._verify_harbour_state
        verify_calls = []

        def reject_candidate(state):
            verify_calls.append(1)
            if len(verify_calls) == 2:
                next(iter(state["heads"].values()))["generation"] += 1
            return original_verify(state)

        monkeypatch.setattr(durable_module, "_verify_harbour_state", reject_candidate)
    if outcome == "write-failure":
        monkeypatch.setattr(
            durable_module, "_atomic_write_json",
            lambda *_args: (_ for _ in ()).throw(OSError("private write failure")),
        )

    call = lambda: store.append_revision(
        principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
        payload_bytes=payload, expected_head=expected_head, idempotency_key=key,
    )
    if outcome == "success":
        assert call().command_kind == "append_revision"
    elif outcome == "replay":
        assert call() == committed
    else:
        code = {
            "stale": "stale_authority",
            "conflict": "idempotency_conflict",
            "candidate-failure": "validation_failed",
            "write-failure": "validation_failed",
        }[outcome]
        _assert_detached_store_error(call, code)

    assert len(captured) == 1
    assert len(seals) == (1 if outcome in {"success", "write-failure"} else 0)
    if outcome != "success":
        assert path.read_bytes() == before
    run = captured[0]
    _assert_private_store_error(
        lambda: run.checkout(payload, PROJECT, ROADMAP), "validation_failed",
    )
    _assert_private_store_error(run.seal, "validation_failed")
    durable_module._discard_planning_payload_validation_run(run)
    durable_module._discard_planning_payload_validation_run(run)


@pytest.mark.parametrize(
    "misuse", ["registry", "cross-store", "thread", "nested-call", "dependencies"],
    ids=["registry", "cross-store", "thread", "nested-call", "dependencies"],
)
def test_append_transaction_rejects_foreign_registry_store_thread_call_and_dependencies(
    tmp_path, monkeypatch, misuse,
):
    store, _issuer, principal, _root = _durable_authority(tmp_path / "primary")
    other_store, _other_issuer, _other_principal, _other_root = _durable_authority(
        tmp_path / "secondary",
    )
    created = _durable_create(
        store, principal, _durable_payload(1, "Append binding source"),
        key="append-binding-create-0001",
    )
    payload = _durable_payload(2, "Append binding candidate")
    original_checkout = durable_module._checkout_planning_payload_validation_run
    original_validate = durable_module.validate_planning_definition
    observed = []

    def checkout(run, value, project_id, roadmap_id):
        if observed:
            return original_checkout(run, value, project_id, roadmap_id)
        observed.append(run)
        if misuse == "dependencies":
            monkeypatch.setattr(
                durable_module, "validate_planning_definition",
                lambda document: original_validate(document),
            )
            return original_checkout(run, value, project_id, roadmap_id)
        result = original_checkout(run, value, project_id, roadmap_id)
        if misuse == "registry":
            ordinary = durable_module._PlanningPayloadValidationRun()
            for foreign in (object(), ordinary):
                _assert_private_store_error(lambda foreign=foreign: (
                    durable_module._commit_append_planning_payload_validation(
                        foreign, store, value, project_id, roadmap_id,
                    )
                ), "validation_failed")
            durable_module._discard_planning_payload_validation_run(ordinary)
        elif misuse == "cross-store":
            _assert_private_store_error(lambda: durable_module._commit_append_planning_payload_validation(
                run, other_store, value, project_id, roadmap_id,
            ), "validation_failed")
        elif misuse == "thread":
            errors = []

            def cross_thread():
                try:
                    run.checkout(value, project_id, roadmap_id)
                except DurablePlanningStoreError as error:
                    errors.append(error.code)

            worker = Thread(target=cross_thread)
            worker.start()
            worker.join(timeout=5)
            assert not worker.is_alive() and errors == ["validation_failed"]
        else:
            with pytest.raises(DurablePlanningStoreError) as nested:
                with durable_module._append_planning_payload_validation_scope(store):
                    pytest.fail("nested append validation scope opened")
            assert nested.value.code == "validation_failed"
        return result

    monkeypatch.setattr(durable_module, "_checkout_planning_payload_validation_run", checkout)
    call = lambda: store.append_revision(
        principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
        payload_bytes=payload, expected_head=created.after_head,
        idempotency_key=f"append-binding-{misuse}-0001",
    )
    if misuse == "dependencies":
        _assert_detached_store_error(call, "validation_failed")
    else:
        assert call().command_kind == "append_revision"
    assert len(observed) == 1
    _assert_private_store_error(
        lambda: observed[0].checkout(payload, PROJECT, ROADMAP), "validation_failed",
    )


def test_append_transaction_does_not_consume_surrounding_read_pair(durable_planning_store):
    fixture = durable_planning_store
    store, principal = fixture["store"], fixture["principal"]
    initial = store.read_subject_bound_accepted_migration_bundle(
        principal, project_id="harbour-one",
    )
    head_binding = initial.heads[0]
    head = store.get_head(
        principal, owner_id=head_binding.owner_id, project_id=head_binding.project_id,
        roadmap_id=head_binding.roadmap_id,
    )
    member = next(
        item for item in initial.revisions
        if item.revision.revision_id == head.current_revision_id
    )
    document = json.loads(member.payload)
    roadmap = document["roadmaps"][0]
    roadmap["revision"] = member.revision.revision + 1
    roadmap["title"] = "Append inside surrounding read pair"
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    document["project"]["latest_approved_revision"] = {
        roadmap["roadmap_id"]: {
            "revision": roadmap["revision"],
            "content_hash": roadmap["content_hash"],
        },
    }
    document["project"]["draft_refs"] = []
    payload = json.dumps(
        document, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")

    with store._planning_payload_validation_pair():
        first = store.read_subject_bound_accepted_migration_bundle(
            principal, project_id="harbour-one",
        )
        appended = store.append_revision(
            principal, owner_id=head.owner_id, project_id=head.project_id,
            roadmap_id=head.roadmap_id, payload_bytes=payload,
            expected_head=head, idempotency_key="append-read-pair-isolation-0001",
        )
        final = store.read_subject_bound_accepted_migration_bundle(
            principal, project_id="harbour-one",
        )

    assert first == initial
    assert appended.after_head.current_revision_id == appended.new_revision_id
    assert any(
        item.revision.revision_id == appended.new_revision_id
        and item.payload == payload
        for item in final.revisions
    )
