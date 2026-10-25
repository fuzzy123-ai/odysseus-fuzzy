from __future__ import annotations

import base64
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from core.auth import AuthManager
from core.database import Base, ChatMessage, Session
from src.context_manifest_producer import (
    CombinedContextManifestProducer,
    ContextManifestProducerError,
)
from src.forge_context_metadata import ForgeContextMetadata
from src.harbour_authority import AuthenticatedPrincipalIssuer
from src.harbour_knowledge_active_runtime import (
    ActiveKnowledgeComposition,
    KnowledgeContextPageConsumer,
    KnowledgeContextPageRequest,
    KnowledgeContextManifestProducer,
)
from src.harbour_knowledge_migration import (
    CausalMigrationSource,
    migrate_prepared_knowledge_migration,
    prepare_authenticated_zero_census_knowledge_migration,
)
from src.harbour_knowledge_staging_destination import (
    HarbourKnowledgeStagingDestinationService,
    _CONTEXT_SCHEMA,
    _sha,
)
from src.harbour_knowledge_store import HarbourKnowledgeStore
from src.planning_context_reader import (
    PlanningContextManifestProducer,
    PlanningContextPageConsumer,
    PlanningContextPageRequest,
    PlanningContextReaderError,
)
from src.planning_definition_contract import compute_roadmap_content_hash
from src.planning_mcp_service import PlanningMcpService
from src.planning_revision_store import (
    DurablePlanningRevisionStore,
    DurablePlanningStoreError,
    PlanningRevisionStore,
)
from src.planning_roadmap_migration import (
    FROZEN_MEMBERS,
    PRODUCT_READINESS_AUTHORITY_PINS,
    SUPPORTING_PATH,
    accept_h1r03_migration,
    build_h1r03_migration_manifest,
    prepare_h1r03_migration,
)
from src.mcp_authenticated_dispatch import AppOwnedPlanningRuntime
from src.private_context_confirmation import ConfirmationError
from src.private_context_confirmation_app import (
    ContextSelection,
    CookieConfirmationAuthority,
    ForgeSelectionLocator,
    PrivateContextConfirmationApp,
    SourceSelection,
)
from src.project_forge_local import LocalProjectForge
from src.repo_registry import RepoRecord, RepoRegistry
from src.unified_source_index_owner_scope import OwnerScope
from src.unified_source_index_runtime_config import UnifiedSourceIndexRuntimeConfig
from src.unified_source_index_sources.knowledge import (
    KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE,
    KnowledgeSourceAdapter,
    knowledge_source_capability_manifest,
    project_harbour_knowledge_index_records,
)
from src.unified_source_index_adapters import AdapterScope, ExtractionProfile
from src.unified_source_index_contract import RecordKind, RecordRef
from src.unified_source_index_contract import Classification
from src.unified_source_index_sources.planning import PlanningSourceAdapter
import src.harbour_knowledge_active_runtime as knowledge_runtime_module
from src.causal_claim_store import CausalClaimStore


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _run_git(work: Path, *arguments: str, stdout=subprocess.DEVNULL) -> None:
    hook_dir = work / ".no-hooks"
    hook_dir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            (
                "git", "-c", f"core.hooksPath={hook_dir}",
                "-c", "core.fsmonitor=false", "-c", "commit.gpgSign=false",
                "-c", "tag.gpgSign=false", "-C", str(work), *arguments,
            ),
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except Exception:
        raise AssertionError("bounded git fixture command failed") from None


def _commit_sha(work: Path, output_path: Path) -> str:
    with output_path.open("wb") as output:
        _run_git(work, "rev-parse", "HEAD", stdout=output)
    raw = output_path.read_bytes()
    if len(raw) > 128 or re.fullmatch(rb"[0-9a-f]{40}(?:[0-9a-f]{24})?\r?\n", raw) is None:
        raise AssertionError("git commit identity was not a closed SHA")
    return raw.strip().decode("ascii")


def _compact_planning_fixture_root(
    destination: Path,
) -> Path:
    source = Path(__file__).resolve().parents[1]
    fixture_root = Path(destination).resolve()
    required_paths = (
        set(PRODUCT_READINESS_AUTHORITY_PINS)
        | {path for path, *_rest in FROZEN_MEMBERS}
        | {SUPPORTING_PATH}
    )
    for relative in sorted(required_paths):
        target = fixture_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((source / relative).read_bytes())
    return fixture_root


def _knowledge_issuer(key: bytes) -> AuthenticatedPrincipalIssuer:
    return AuthenticatedPrincipalIssuer(
        issuer_id="harbour-knowledge-runtime",
        key_id="app-key-v1",
        secret=hmac.new(key, b"odysseus.harbour.knowledge.issuer.v1\0", hashlib.sha256).digest(),
    )


def _build_knowledge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    auth: AuthManager,
    owner_sid: str,
    cookie: str,
) -> tuple[ActiveKnowledgeComposition, SourceSelection]:
    encoded_key = Fernet.generate_key()
    key = base64.urlsafe_b64decode(encoded_key)
    key_path = tmp_path / "app.key"
    key_path.write_bytes(encoded_key)
    monkeypatch.setattr(knowledge_runtime_module, "APP_KEY_FILE", key_path)
    monkeypatch.setattr(knowledge_runtime_module, "DATA_DIR", tmp_path)
    causal = CausalClaimStore(sqlite3.connect(tmp_path / "causal.sqlite"), initialize=True)
    plan = prepare_authenticated_zero_census_knowledge_migration(
        causal_sources=(CausalMigrationSource(owner_sid, causal, owner_sid),),
        owner_map={owner_sid: owner_sid},
        retention_by_scope={owner_sid: {
            "retention_class": "user_managed",
            "deletion_mode": "tombstone_only",
            "retain_until": None,
            "grace_until": None,
            "legal_hold": False,
        }},
        observed_at="2026-10-01T12:00:00Z",
    )
    issuer = _knowledge_issuer(key)
    staging = HarbourKnowledgeStagingDestinationService(
        tmp_path / "harbour-knowledge-staging",
        trusted_issuers=(issuer,),
    )
    migration_context = _sha({
        "migration_id": plan.migration_id,
        "owner_id": owner_sid,
        "schema_id": _CONTEXT_SCHEMA,
        "snapshot_digest": plan.source_manifest_digest,
    })
    migration_principal = issuer.issue(
        subject_id=owner_sid,
        owner_id=owner_sid,
        auth_context_digest=migration_context,
    )
    completion = migrate_prepared_knowledge_migration(
        plan,
        staging=staging,
        principals={owner_sid: migration_principal},
    ).completion_reference
    staging.select_generation(completion, expected_selection_digest="sha256:" + "0" * 64)
    selected = staging.selected_runtime(owner_sid)
    assert selected is not None
    store = selected[4]
    runtime_principal = issuer.issue(
        subject_id=owner_sid,
        owner_id=owner_sid,
        auth_context_digest=_sha({"owner": owner_sid, "purpose": "fixture-write"}),
    )
    store.create_object(
        runtime_principal,
        owner_id=owner_sid,
        knowledge_id="active-note",
        knowledge_kind="note",
        payload_bytes="orbital mémoire privée".encode("utf-8"),
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
        idempotency_key="active-note-create",
    )
    native = KnowledgeSourceAdapter(
        store=store,
        principal=runtime_principal,
        owner_id=owner_sid,
        cursor_signing_key=b"c" * 32,
        classification_ceiling=Classification.SENSITIVE,
    )
    page = native.discover(
        AdapterScope(OwnerScope.for_subject_id(owner_sid).value, Classification.SENSITIVE),
        cursor="",
        limit=100,
        time_budget_ms=5000,
    )
    records = []
    for source in (item.source for item in page.items):
        version = native.observe_version(RecordRef(RecordKind.SOURCE, source.source_id))
        extracted = native.extract(
            version,
            ExtractionProfile(KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE, 100, 100_000, 5000),
        )
        records.extend((source, version, *extracted.chunks))
    from src.unified_source_index_sqlite import SQLiteUnifiedSourceIndexStore
    usi = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.sqlite")
    write = usi.begin_write(usi.current_snapshot())
    for record in records:
        write.put(record)
    write.commit()
    generation = knowledge_source_capability_manifest().generation_ref
    config = UnifiedSourceIndexRuntimeConfig.from_environment({
        "ODYSSEUS_USI_RUNTIME_ENABLED": "true",
        "ODYSSEUS_USI_RUNTIME_MODE": "read_only",
        "ODYSSEUS_USI_SELECTED_GENERATION": generation,
        "ODYSSEUS_USI_ALLOWED_OWNERS": owner_sid,
        "ODYSSEUS_USI_ALLOWED_SOURCES": ",".join(item.source.source_id for item in page.items),
        "ODYSSEUS_USI_ALLOWED_DOMAINS": "native_knowledge",
        "ODYSSEUS_USI_SQLITE_PATH": "usi.sqlite",
        "ODYSSEUS_USI_QUERY_MAX_RESULTS": "8",
        "ODYSSEUS_USI_QUERY_TIMEOUT_SECONDS": "5",
    }, data_root=tmp_path)
    composition = knowledge_runtime_module.ActiveKnowledgeComposition.open_existing(config, auth)
    staging.close()
    causal.close()
    with CookieConfirmationAuthority(auth).hold(cookie, "chat-owned") as binding:
        reopened_subject, reopened_store, reopened_principal, *_ = composition._context_runtime(binding)
        assert reopened_subject == owner_sid
        snapshot = reopened_store.capture_index_snapshot(reopened_principal, owner_id=owner_sid)
        record = snapshot.records[0]
        projection = project_harbour_knowledge_index_records(OwnerScope.for_subject_id(owner_sid).value, record)
    target = SourceSelection(
        "knowledge",
        projection.source.source_id,
        projection.source_version.source_version_id,
    )
    return composition, target


def _real_three_domain_context_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    planning_repo_root: Path | None = None,
):
    repo_root = (
        Path(__file__).resolve().parents[1]
        if planning_repo_root is None
        else Path(planning_repo_root).resolve(strict=True)
    )
    auth = AuthManager(str(tmp_path / "auth.json"))
    assert auth.create_user("fuzzy", "Password-123", is_admin=True)
    cookie = auth.create_session("fuzzy", "Password-123")
    assert cookie
    owner_sid = auth.subject_id_for_username("fuzzy")
    assert owner_sid

    workspace = tmp_path / "workspace"
    server_projects = tmp_path / "server-projects"
    source = workspace / "repos" / "demo"
    source.mkdir(parents=True)
    server_projects.mkdir()
    _run_git(source, "init", "-b", "main")
    _run_git(source, "config", "user.email", "context@example.invalid")
    _run_git(source, "config", "user.name", "Context Fixture")
    readme = "Public retained project — café\n".encode("utf-8")
    (source / "README.md").write_bytes(readme)
    _run_git(source, "add", "README.md")
    _run_git(source, "commit", "-m", "retained public context fixture")
    commit = _commit_sha(source, tmp_path / "commit.sha")
    local_forge = LocalProjectForge(root=tmp_path / "versions", source_roots=(workspace, server_projects))
    stored = local_forge.store_existing_commit(
        owner_id=owner_sid,
        repo_id="demo",
        source_repo=source,
        commit_sha=commit,
        idempotency_key="context-retain-0001",
        policy_snapshot={"forge_context": "public_registered"},
        version_label="Public retained fixture",
        artifacts=(),
    )
    registry_path = tmp_path / "repo_registry.json"
    registry = RepoRegistry()
    registry.add(RepoRecord.create(
        repo_id="demo", title="Public Demo", repo_kind="project", owner=owner_sid,
        path_ref="workspace/repos/demo", workspace_root="workspace",
        project_root="workspace/repos/demo", default_branch="main", current_branch="main",
        privacy_class="public", provider_scope="default", remotes=(),
        created_at="2026-10-04T00:00:00Z",
    ))
    registry.save_json(registry_path)
    forge = ForgeContextMetadata(
        local_forge=local_forge,
        registry_path=registry_path,
        auth_manager=auth,
        workspace_root=workspace,
        server_projects_root=server_projects,
    )
    with CookieConfirmationAuthority(auth).hold(cookie, "chat-owned") as binding:
        forge_manifest = forge.capture_version_metadata(binding, "demo", stored.version_id, ("README.md",))
    forge_selection = SourceSelection(
        "forge",
        forge_manifest.descriptor.source_id,
        forge_manifest.descriptor.source_version_id,
        ForgeSelectionLocator("demo", stored.version_id, "README.md"),
    )

    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="odysseus-auth", key_id="planning-key-1",
        secret=b"real-planning-reader-test-secret-0001",
    )
    durable_store = DurablePlanningRevisionStore(tmp_path / "durable-planning", trusted_issuers=(issuer,))
    principal = issuer.issue(
        subject_id=owner_sid, owner_id="fuzzy", auth_context_digest="sha256:" + "4" * 64,
        ttl_seconds=3600, nonce="combined-planning-accepted",
    )
    accepted = build_h1r03_migration_manifest(repo_root)
    durable_store.prepare_migration(principal, manifest=accepted)
    durable_store.accept_migration(
        principal, batch_id=accepted["batch_id"], current_manifest=lambda: deepcopy(accepted),
    )
    owner, _batch, records, _heads = durable_store.read_subject_bound_accepted_migration_records(
        principal, project_id="harbour-one",
    )
    selected_roadmap = "abc-roadmap-reconciliation-import"
    accepted_document = deepcopy(next(
        document for _record_owner, document, _revision_ref in records
        if document["roadmaps"][0]["roadmap_id"] == selected_roadmap
    ))
    roadmap = accepted_document["roadmaps"][0]
    roadmap["revision"] = 2
    roadmap["title"] = "Übersicht der Prüfung"
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    accepted_document["project"]["latest_approved_revision"] = {
        selected_roadmap: {"revision": 2, "content_hash": roadmap["content_hash"]},
    }
    head = durable_store.get_head(principal, owner_id=owner, project_id="harbour-one", roadmap_id=selected_roadmap)
    appended = durable_store.append_revision(
        principal, owner_id=owner, project_id="harbour-one", roadmap_id=selected_roadmap,
        payload_bytes=json.dumps(accepted_document, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        expected_head=head, idempotency_key="combined-unicode-append",
    )
    durable_store.approve_revision(
        principal, owner_id=owner, project_id="harbour-one", roadmap_id=selected_roadmap,
        revision_id=appended.new_revision_id, expected_head=appended.after_head,
        idempotency_key="combined-unicode-approve",
    )
    owner, _batch, records, _heads = durable_store.read_subject_bound_accepted_migration_records(
        principal, project_id="harbour-one",
    )
    source_paths = {item["roadmap_id"]: item["path"] for item in accepted["source_items"]}
    legacy_records = [
        (owner, document, source_paths[document["roadmaps"][0]["roadmap_id"]])
        for _record_owner, document, _revision_ref in records
        if document["roadmaps"][0]["roadmap_id"] == selected_roadmap
        and document["roadmaps"][0]["revision"] == 2
    ]
    planning_source = tmp_path / "planning-source"
    planning_source.mkdir()
    for _record_owner, document, source_ref in legacy_records:
        destination = (planning_source / Path(source_ref)).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(document, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    legacy = PlanningRevisionStore(legacy_records, cursor_secret=b"combined-planning-legacy-secret")
    planning_service = PlanningMcpService(planning_source, definition_store=legacy, definition_owner="fuzzy")
    planning_runtime = AppOwnedPlanningRuntime(
        repo_root=str(repo_root), legacy_store=legacy, durable_store=durable_store, issuer=issuer,
    )
    planning = PlanningContextManifestProducer(planning_runtime, auth, planning_service, "harbour-one")
    adapter = PlanningSourceAdapter(
        service=planning_service, owner_scope=OwnerScope.for_subject_id(owner_sid),
        expected_owner_ref="fuzzy", observed_at="2026-10-04T00:00:00Z", max_sources=100,
    )
    discovered = adapter.discover(
        expected_binding_digest=adapter.binding.binding_digest,
        expected_snapshot_digest=adapter.binding.snapshot_digest,
        cursor=0, limit=100,
    )
    planning_descriptor = next(item for item in discovered.items if item.evidence.roadmap_id == selected_roadmap)
    planning_target = SourceSelection(
        "planning", planning_descriptor.source.source_id, planning_descriptor.source_version.source_version_id,
    )

    knowledge_composition, knowledge_target = _build_knowledge(tmp_path, monkeypatch, auth, owner_sid, cookie)
    knowledge = KnowledgeContextManifestProducer(knowledge_composition)
    selection = ContextSelection(tuple(sorted((forge_selection, planning_target, knowledge_target), key=lambda item: (item.source, item.selection_ref, item.revision_ref))))
    combined = CombinedContextManifestProducer(
        auth_manager=auth, forge=forge, planning=planning, knowledge=knowledge,
    )
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = session_factory()
    db.add(Session(
        id="chat-owned", name="Owned", endpoint_url="local", model="test", owner="fuzzy", message_count=0,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
    ))
    db.commit()
    db.close()
    try:
        yield {
            "auth": auth, "cookie": cookie, "chat_id": "chat-owned", "session_factory": session_factory,
            "combined": combined, "forge": forge, "local_forge": local_forge,
            "forge_manifest": forge_manifest, "forge_selection": forge_selection,
            "selection": selection, "knowledge_producer": knowledge,
            "knowledge_composition": knowledge_composition, "planning_producer": planning,
            "planning_target": planning_target, "knowledge_target": knowledge_target,
        }
    finally:
        try:
            knowledge_composition.close()
        finally:
            engine.dispose()


@pytest.fixture
def real_three_domain_context(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    scratch_root = Path(__file__).resolve().parents[1] / ".abc-test-tmp"
    scratch_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ctx-", dir=scratch_root) as raw:
        yield from _real_three_domain_context_body(Path(raw), monkeypatch)


def test_compact_planning_fixture_preserves_semantic_migration_inputs(
    tmp_path: Path,
) -> None:
    actual_root = Path(__file__).resolve().parents[1]
    compact_root = _compact_planning_fixture_root(tmp_path / "compact")

    actual = build_h1r03_migration_manifest(actual_root)
    compact = build_h1r03_migration_manifest(compact_root)

    for field in (
        "normalized_payloads",
        "source_items",
        "authority_pins",
        "supporting_relations",
    ):
        assert compact[field] == actual[field]
    assert len(compact["structural_census"]) == 14
    assert len(actual["structural_census"]) > len(compact["structural_census"])
    assert compact["structural_census"] != actual["structural_census"]
    assert compact["supporting_observation"] != actual["supporting_observation"]
    assert compact["manifest_digest"] != actual["manifest_digest"]
    assert compact["batch_id"] != actual["batch_id"]


def test_compact_planning_roots_and_accepted_stores_are_independent(
    tmp_path: Path,
) -> None:
    roots = (
        _compact_planning_fixture_root(tmp_path / "first-input"),
        _compact_planning_fixture_root(tmp_path / "second-input"),
    )
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="odysseus-auth",
        key_id="compact-fixture-key",
        secret=b"compact-planning-fixture-secret-0001",
    )
    principals = tuple(
        issuer.issue(
            subject_id=f"owner_{index:032x}",
            owner_id="fuzzy",
            auth_context_digest="sha256:" + str(index) * 64,
            ttl_seconds=3600,
            nonce=f"compact-fixture-{index}",
        )
        for index in (1, 2)
    )
    store_roots = tuple(tmp_path / f"store-{index}" for index in (1, 2))
    stores = tuple(
        DurablePlanningRevisionStore(
            store_root,
            trusted_issuers=(issuer,),
        )
        for store_root in store_roots
    )
    manifests = tuple(build_h1r03_migration_manifest(root) for root in roots)
    assert manifests[0] == manifests[1]
    for store, principal, root, manifest in zip(
        stores, principals, roots, manifests, strict=True,
    ):
        prepared = prepare_h1r03_migration(store, principal, repo_root=root)
        accepted = accept_h1r03_migration(
            store,
            principal,
            repo_root=root,
            batch_id=manifest["batch_id"],
        )
        assert prepared.batch_id == accepted.batch_id == manifest["batch_id"]
        assert prepared.phase == "prepare"
        assert accepted.phase == "accept"

    second_bundle_before = stores[1].read_subject_bound_accepted_migration_bundle(
        principals[1], project_id="harbour-one",
    )
    second_state_before = stores[1].get_migration_batch(
        principals[1],
        owner_id="fuzzy",
        batch_id=manifests[1]["batch_id"],
    )
    first_item = manifests[0]["source_items"][0]
    roadmap_id = first_item["roadmap_id"]
    next_document = json.loads(manifests[0]["normalized_payloads"][roadmap_id])
    next_roadmap = next_document["roadmaps"][0]
    next_roadmap["revision"] = 2
    next_roadmap["title"] = "Independent compact store mutation"
    next_roadmap["content_hash"] = compute_roadmap_content_hash(next_roadmap)
    next_document["project"]["latest_approved_revision"][roadmap_id] = {
        "revision": 2,
        "content_hash": next_roadmap["content_hash"],
    }
    first_head = stores[0].get_head(
        principals[0],
        owner_id="fuzzy",
        project_id="harbour-one",
        roadmap_id=roadmap_id,
    )
    appended = stores[0].append_revision(
        principals[0],
        owner_id="fuzzy",
        project_id="harbour-one",
        roadmap_id=roadmap_id,
        payload_bytes=json.dumps(
            next_document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8"),
        expected_head=first_head,
        idempotency_key="compact-store-one-append",
    )

    drifted = roots[0] / next(iter(PRODUCT_READINESS_AUTHORITY_PINS))
    drifted.write_bytes(drifted.read_bytes() + b"\n")
    with pytest.raises(DurablePlanningStoreError) as stale:
        build_h1r03_migration_manifest(roots[0])
    assert stale.value.code == "stale_authority"
    assert build_h1r03_migration_manifest(roots[1]) == manifests[1]
    reopened_stores = tuple(
        DurablePlanningRevisionStore(
            store_root,
            trusted_issuers=(issuer,),
        )
        for store_root in store_roots
    )
    reopened_first_head = reopened_stores[0].get_head(
        principals[0],
        owner_id="fuzzy",
        project_id="harbour-one",
        roadmap_id=roadmap_id,
    )
    assert reopened_first_head.current_revision_id == appended.new_revision_id
    assert reopened_first_head.generation == first_head.generation + 1
    first_bundle_after = reopened_stores[0].read_subject_bound_accepted_migration_bundle(
        principals[0], project_id="harbour-one",
    )
    assert len(first_bundle_after.revisions) == len(second_bundle_before.revisions) + 1
    assert any(
        member.revision.revision_id == appended.new_revision_id
        and json.loads(member.payload)["roadmaps"][0]["title"]
        == "Independent compact store mutation"
        for member in first_bundle_after.revisions
    )
    assert reopened_stores[1].read_subject_bound_accepted_migration_bundle(
        principals[1], project_id="harbour-one",
    ) == second_bundle_before
    assert reopened_stores[1].get_migration_batch(
        principals[1],
        owner_id="fuzzy",
        batch_id=manifests[1]["batch_id"],
    ) == second_state_before


@pytest.mark.parametrize(
    ("relative", "mutation"),
    [
        pytest.param(
            next(iter(PRODUCT_READINESS_AUTHORITY_PINS)),
            "missing",
            id="missing-authority",
        ),
        pytest.param(FROZEN_MEMBERS[0][0], "drift", id="drifted-frozen-member"),
    ],
)
def test_compact_planning_fixture_rejects_missing_or_drifted_pinned_bytes(
    tmp_path: Path,
    relative: str,
    mutation: str,
) -> None:
    compact_root = _compact_planning_fixture_root(tmp_path / "compact")
    target = compact_root / relative
    if mutation == "missing":
        target.unlink()
    else:
        target.write_bytes(target.read_bytes() + b"\n")

    with pytest.raises(DurablePlanningStoreError) as stale:
        build_h1r03_migration_manifest(compact_root)
    assert stale.value.code == "stale_authority"


def test_real_three_domain_metadata_and_private_foundation(real_three_domain_context) -> None:
    fixture = real_three_domain_context
    auth = fixture["auth"]
    with CookieConfirmationAuthority(auth).hold(fixture["cookie"], fixture["chat_id"]) as binding:
        metadata = fixture["combined"].capture_metadata(binding, fixture["selection"])
        manifest = fixture["combined"].capture(binding, fixture["selection"])
    assert {item.source for item in metadata} == {"forge", "planning", "knowledge"}
    assert len(metadata) == 3
    assert any(item.classification == "private" for item in metadata)
    assert len(manifest.digest) == 64
    assert {item.selection_ref for item in metadata} == {
        item.selection_ref for item in fixture["selection"].items
    }


def test_public_only_metadata_is_valid_but_foundation_is_denied(real_three_domain_context) -> None:
    fixture = real_three_domain_context
    selection = ContextSelection((fixture["forge_selection"],))
    with CookieConfirmationAuthority(fixture["auth"]).hold(fixture["cookie"], fixture["chat_id"]) as binding:
        metadata = fixture["combined"].capture_metadata(binding, selection)
        with pytest.raises(ContextManifestProducerError) as error:
            fixture["combined"].capture(binding, selection)
    assert len(metadata) == 1
    assert metadata[0].classification == "public"
    assert error.value.code == "foundation_unavailable"


def test_locator_and_constructor_identity_are_bound(real_three_domain_context, tmp_path: Path) -> None:
    fixture = real_three_domain_context
    forge_item = fixture["forge_selection"]
    wrong = SourceSelection(
        "forge", forge_item.selection_ref, forge_item.revision_ref,
        ForgeSelectionLocator("demo", fixture["forge_manifest"].descriptor.version_id, "missing.txt"),
    )
    with CookieConfirmationAuthority(fixture["auth"]).hold(fixture["cookie"], fixture["chat_id"]) as binding:
        with pytest.raises(ContextManifestProducerError) as error:
            fixture["combined"].capture_metadata(binding, ContextSelection((wrong,)))
    assert error.value.code in {"context_stale", "capture_unavailable"}
    other_auth = AuthManager(str(tmp_path / "other-auth.json"))
    with pytest.raises(ContextManifestProducerError) as constructor_error:
        CombinedContextManifestProducer(
            auth_manager=other_auth,
            forge=fixture["forge"],
            planning=fixture["planning_producer"],
            knowledge=fixture["knowledge_producer"],
        )
    assert constructor_error.value.code == "invalid_provider"


def test_real_paired_full_selection_confirmation_round_trip(real_three_domain_context) -> None:
    fixture = real_three_domain_context
    confirmation = PrivateContextConfirmationApp(
        fixture["session_factory"], fixture["auth"], fixture["combined"],
    )
    challenge = confirmation.issue_for_selection(
        fixture["cookie"], fixture["chat_id"], fixture["selection"],
    )
    verified = confirmation.consume_challenge(
        fixture["cookie"], fixture["chat_id"], challenge,
    )
    assert len(fixture["selection"].items) == 3
    assert type(verified.disclosure_message_id) is int


def test_real_private_pages_cover_full_selection_and_paired_tamper(
    real_three_domain_context,
) -> None:
    fixture = real_three_domain_context
    confirmation = PrivateContextConfirmationApp(
        fixture["session_factory"], fixture["auth"], fixture["combined"],
    )
    challenge = confirmation.issue_for_selection(
        fixture["cookie"], fixture["chat_id"], fixture["selection"],
    )
    verified = confirmation.consume_challenge(
        fixture["cookie"], fixture["chat_id"], challenge,
    )
    assert len(fixture["selection"].items) == 3
    planning_consumer = PlanningContextPageConsumer(
        fixture["planning_producer"], fixture["auth"], confirmation,
        fixture["session_factory"],
    )
    knowledge_consumer = KnowledgeContextPageConsumer(
        fixture["knowledge_composition"], fixture["auth"], confirmation,
        fixture["session_factory"],
    )
    with CookieConfirmationAuthority(fixture["auth"]).hold(
        fixture["cookie"], fixture["chat_id"],
    ) as binding:
        planning_manifest = fixture["planning_producer"].capture(
            binding, fixture["selection"],
        )
        knowledge_manifest = fixture["knowledge_producer"].capture_metadata(
            binding, ContextSelection((fixture["knowledge_target"],)),
        )
    planning_request = PlanningContextPageRequest(
        fixture["selection"], fixture["planning_target"], fixture["chat_id"],
        planning_manifest.digest, 0, 1_048_576, verified.disclosure_message_id,
    )
    knowledge_request = KnowledgeContextPageRequest(
        fixture["selection"], fixture["knowledge_target"], fixture["chat_id"],
        knowledge_manifest.digest, 0, 1_048_576, verified.disclosure_message_id,
    )
    planning_page = planning_consumer.read_page(fixture["cookie"], planning_request)
    knowledge_page = knowledge_consumer.read_page(fixture["cookie"], knowledge_request)
    assert planning_page.page_bytes
    assert knowledge_page.payload_bytes
    assert planning_page.total_bytes == len(planning_page.page_bytes)
    assert knowledge_page.total == len(knowledge_page.payload_bytes)
    planning_page_sha = "sha256:" + hashlib.sha256(planning_page.page_bytes).hexdigest()
    knowledge_page_sha = "sha256:" + hashlib.sha256(knowledge_page.payload_bytes).hexdigest()
    planning_item = next(
        item for item in planning_manifest.items
        if item.source == "planning"
        and item.selection_ref == fixture["planning_target"].selection_ref
        and item.revision_ref == fixture["planning_target"].revision_ref
    )
    knowledge_item = next(
        item for item in knowledge_manifest.items
        if item.selection_ref == fixture["knowledge_target"].selection_ref
        and item.revision_ref == fixture["knowledge_target"].revision_ref
    )
    assert planning_page.manifest_digest == planning_manifest.digest
    assert knowledge_page.manifest_digest == knowledge_manifest.digest
    assert planning_page.page_sha256 == planning_page.whole_sha256 == planning_page_sha == (
        "sha256:" + planning_item.payload_sha256
    )
    assert knowledge_page.page_sha256 == knowledge_page.whole_sha256 == knowledge_page_sha == (
        knowledge_item.payload_sha256
    )

    db = fixture["session_factory"]()
    try:
        row = db.query(ChatMessage).filter(
            ChatMessage.id == str(verified.disclosure_message_id),
            ChatMessage.session_id == fixture["chat_id"],
        ).one()
        row.content = "tampered confirmation"
        db.commit()
    finally:
        db.close()
    with pytest.raises(PlanningContextReaderError) as planning_error:
        planning_consumer.read_page(fixture["cookie"], planning_request)
    assert planning_error.value.code in {"invalid_context", "disclosure_unverified"}
    with pytest.raises(ConfirmationError) as knowledge_error:
        knowledge_consumer.read_page(fixture["cookie"], knowledge_request)
    assert knowledge_error.value.code == "disclosure_unverified"


def test_combined_capture_rejects_native_knowledge_drift(
    real_three_domain_context, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = real_three_domain_context
    knowledge_type = type(fixture["knowledge_producer"])
    real_capture = knowledge_type.capture_metadata
    mutation = {"done": False}

    def capture_and_drift(self, binding, selection):
        result = real_capture(self, binding, selection)
        if not mutation["done"]:
            _subject, store, principal, *_ = fixture["knowledge_composition"]._context_runtime(binding)
            store.create_object(
                principal,
                owner_id=binding.owner_sid,
                knowledge_id="between-pass-drift",
                knowledge_kind="note",
                payload_bytes=b"between pass drift",
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
                idempotency_key="between-pass-drift-create",
            )
            mutation["done"] = True
        return result

    monkeypatch.setattr(knowledge_type, "capture_metadata", capture_and_drift)
    with CookieConfirmationAuthority(fixture["auth"]).hold(
        fixture["cookie"], fixture["chat_id"],
    ) as binding:
        with pytest.raises(ContextManifestProducerError) as error:
            fixture["combined"].capture_metadata(binding, fixture["selection"])
    assert mutation["done"] is True
    assert error.value.code in {"context_stale", "capture_unavailable"}


def test_dual_snapshot_mandates_mixed_selection_and_reads_one_planning_image(
    real_three_domain_context, monkeypatch,
):
    from src.planning_durable_context_source import (
        DEFAULT_DURABLE_PLANNING_CONTEXT_POLICY,
        DurablePlanningContextSource,
    )
    from tests.test_mcp_context_metadata import _install_real_mcp_authority, _lease
    from tests.test_planning_durable_context_source import _append_clean_descendants

    fixture = real_three_domain_context
    auth = fixture["auth"]
    runtime = fixture["planning_producer"]._runtime
    owner_sid = auth.subject_id_for_username("fuzzy")
    principal = runtime.issuer.issue(
        subject_id=owner_sid,
        owner_id="fuzzy",
        auth_context_digest="sha256:" + "9" * 64,
        ttl_seconds=3600,
        nonce="combined-dual-sample-0001",
    )
    with CookieConfirmationAuthority(auth).hold(
        fixture["cookie"], fixture["chat_id"],
    ) as setup_binding:
        _append_clean_descendants(
            {"store": runtime.durable_store, "principal": principal}, setup_binding,
        )
        durable_source = DurablePlanningContextSource(
            runtime=runtime,
            auth_manager=auth,
            project_id="harbour-one",
            policy=DEFAULT_DURABLE_PLANNING_CONTEXT_POLICY,
        )
        planning_items = durable_source.discover(setup_binding)
    native_planning = PlanningContextManifestProducer(
        runtime, auth, None, "harbour-one", durable_source=durable_source,
    )
    authority = _install_real_mcp_authority(fixture)
    fixture["authority"] = authority
    lease = _lease(fixture, "combined-dual-sample")
    combined = CombinedContextManifestProducer(
        auth_manager=auth,
        forge=fixture["forge"],
        planning=native_planning,
        knowledge=fixture["knowledge_producer"],
        mcp_authority=authority,
    )
    selection = ContextSelection(tuple(sorted(
        (fixture["forge_selection"], *planning_items, fixture["knowledge_target"]),
        key=lambda item: (item.source, item.selection_ref, item.revision_ref),
    )))
    original_materialize = runtime.durable_store._materialize_accepted_migration_locked
    forge_type = type(fixture["forge"])
    knowledge_type = type(fixture["knowledge_producer"])
    original_cookie_forge = forge_type.capture_version_metadata
    original_mcp_forge = forge_type.capture_version_metadata_mcp
    original_cookie_knowledge = knowledge_type.capture_metadata
    original_mcp_knowledge = knowledge_type.capture_metadata_mcp
    original_admin_guard = CombinedContextManifestProducer.__dict__["_admin_guard"]
    original_native_dual = CombinedContextManifestProducer.__dict__["_capture_native_dual"]
    original_reader_dual = PlanningContextManifestProducer.__dict__["capture_metadata_dual"]
    materializations, callbacks, transient_calls = [], [], []

    def hostile_native_dual(*_args, **_kwargs):
        transient_calls.append("capture_native_dual")
        raise AssertionError("transient hostile native helper executed")

    def hostile_reader_dual(*_args, **_kwargs):
        transient_calls.append("capture_metadata_dual")
        raise AssertionError("transient hostile reader helper executed")

    @contextmanager
    def admin_guard_with_transient_replacement(self, binding):
        with original_admin_guard(self, binding) as alias:
            monkeypatch.setattr(
                CombinedContextManifestProducer,
                "_capture_native_dual",
                hostile_native_dual,
            )
            yield alias

    def materialize(**kwargs):
        if (
            PlanningContextManifestProducer.__dict__.get("capture_metadata_dual")
            is hostile_reader_dual
        ):
            monkeypatch.setattr(
                PlanningContextManifestProducer,
                "capture_metadata_dual",
                original_reader_dual,
            )
        materializations.append(kwargs["batch"]["batch_id"])
        return original_materialize(**kwargs)

    def cookie_forge(self, *args, **kwargs):
        if (
            CombinedContextManifestProducer.__dict__.get("_capture_native_dual")
            is hostile_native_dual
        ):
            monkeypatch.setattr(
                CombinedContextManifestProducer,
                "_capture_native_dual",
                original_native_dual,
            )
        callbacks.append("cookie_forge")
        return original_cookie_forge(self, *args, **kwargs)

    def cookie_knowledge(self, *args, **kwargs):
        callbacks.append("cookie_knowledge")
        result = original_cookie_knowledge(self, *args, **kwargs)
        monkeypatch.setattr(
            PlanningContextManifestProducer,
            "capture_metadata_dual",
            hostile_reader_dual,
        )
        return result

    def mcp_forge(self, *args, **kwargs):
        callbacks.append("mcp_forge")
        return original_mcp_forge(self, *args, **kwargs)

    def mcp_knowledge(self, *args, **kwargs):
        callbacks.append("mcp_knowledge")
        return original_mcp_knowledge(self, *args, **kwargs)

    monkeypatch.setattr(
        runtime.durable_store, "_materialize_accepted_migration_locked", materialize,
    )
    monkeypatch.setattr(forge_type, "capture_version_metadata", cookie_forge)
    monkeypatch.setattr(knowledge_type, "capture_metadata", cookie_knowledge)
    monkeypatch.setattr(forge_type, "capture_version_metadata_mcp", mcp_forge)
    monkeypatch.setattr(knowledge_type, "capture_metadata_mcp", mcp_knowledge)
    monkeypatch.setattr(
        CombinedContextManifestProducer,
        "_admin_guard",
        admin_guard_with_transient_replacement,
    )
    cookie_authority = CookieConfirmationAuthority(auth)
    try:
        with cookie_authority.hold(
            fixture["cookie"], fixture["chat_id"],
        ) as binding:
            cookie, mcp = combined.capture_dual_snapshot(binding, lease, selection)
            with pytest.raises(ContextManifestProducerError) as nonmixed:
                combined.capture_dual_snapshot(
                    binding, lease, ContextSelection((planning_items[0],)),
                )
        assert len(materializations) == 1
        assert callbacks == [
            "cookie_forge", "cookie_knowledge", "mcp_forge", "mcp_knowledge",
        ]
        assert transient_calls == []
        assert {item.source for item in cookie.items} == {
            "forge", "planning", "knowledge",
        }
        selected_keys = tuple(
            (item.source, item.selection_ref, item.revision_ref)
            for item in selection.items
        )
        cookie_by_key = {
            (item.source, item.selection_ref, item.revision_ref): item
            for item in cookie.items
        }
        mcp_by_key = {
            (item.source, item.selection_ref, item.revision_ref): item
            for item in mcp.items
        }
        assert len(cookie.items) == len(mcp.items) == len(selected_keys)
        assert len(cookie_by_key) == len(mcp_by_key) == len(selected_keys)
        assert set(cookie_by_key) == set(mcp_by_key) == set(selected_keys)
        assert len(cookie.target_manifest_digests) == len(selected_keys)
        assert len(mcp.target_manifest_digests) == len(selected_keys)
        for key in selected_keys:
            cookie_item = cookie_by_key[key]
            mcp_item = mcp_by_key[key]
            assert (
                cookie_item.payload_sha256,
                cookie_item.classification,
                cookie_item.provenance_ref,
            ) == (
                mcp_item.payload_sha256,
                mcp_item.classification,
                mcp_item.provenance_ref,
            )
            assert cookie_item.boundary_sha256 != mcp_item.boundary_sha256
            assert cookie_item.record_sha256 != mcp_item.record_sha256
        assert nonmixed.value.code == "invalid_context"

        target = planning_items[0]
        with cookie_authority.hold(
            fixture["cookie"], fixture["chat_id"],
        ) as content_binding:
            content_cookie, content_mcp, cookie_payload, mcp_payload = (
                combined.capture_dual_snapshot_content(
                    content_binding, lease, selection, target,
                )
            )
            expected_payload = durable_source.read_document(content_binding, target)
        assert cookie_payload == mcp_payload == expected_payload
        assert content_cookie == cookie
        assert content_mcp == mcp
        assert content_cookie.__slots__ == (
            "items", "target_manifest_digests", "mcp_metadata_binding",
        )
        assert all(
            type(getattr(snapshot, name)) is not bytes
            for snapshot in (content_cookie, content_mcp)
            for name in snapshot.__slots__
        )
        assert hashlib.sha256(cookie_payload).hexdigest() == next(
            item.payload_sha256
            for item in content_mcp.items
            if item.source == "planning"
            and item.selection_ref == target.selection_ref
            and item.revision_ref == target.revision_ref
        )

        monkeypatch.setattr(
            CombinedContextManifestProducer, "_admin_guard", original_admin_guard,
        )
        context_operation = type(fixture["knowledge_composition"]).__dict__["_context_operation"]
        provider_assertion = CombinedContextManifestProducer.__dict__[
            "_assert_mcp_provider_pins"
        ]
        context_fake_calls, assertion_calls = [], []

        def hostile_context_operation(*_args, **_kwargs):
            context_fake_calls.append("context_operation")
            raise AssertionError("transient hostile context operation executed")

        def transient_provider_assertion(self):
            assertion_calls.append("assert")
            if len(assertion_calls) == 2:
                monkeypatch.setattr(
                    type(fixture["knowledge_composition"]),
                    "_context_operation",
                    context_operation,
                )
            provider_assertion(self)
            if len(assertion_calls) == 1:
                monkeypatch.setattr(
                    type(fixture["knowledge_composition"]),
                    "_context_operation",
                    hostile_context_operation,
                )

        with monkeypatch.context() as transient:
            transient.setattr(
                CombinedContextManifestProducer,
                "_assert_mcp_provider_pins",
                transient_provider_assertion,
            )
            with CookieConfirmationAuthority(auth).hold(
                fixture["cookie"], fixture["chat_id"],
            ) as binding:
                with combined._dual_sample_pair():
                    combined.capture_dual_snapshot(binding, lease, selection)
                    combined.capture_dual_snapshot(binding, lease, selection)
        assert context_fake_calls == []
        assert len(assertion_calls) >= 2

        fake_calls = []

        def hostile_dual(*_args, **_kwargs):
            fake_calls.append("capture_native_dual")
            raise AssertionError("hostile dual helper executed")

        with monkeypatch.context() as hostile:
            hostile.setattr(
                CombinedContextManifestProducer,
                "_capture_native_dual",
                hostile_dual,
            )
            with pytest.raises(ContextManifestProducerError) as substituted:
                combined.capture_dual_snapshot(binding, lease, selection)
            assert substituted.value.code == "invalid_provider"
            assert fake_calls == []

        content_fake_calls = []

        def hostile_content_adapter(*_args, **_kwargs):
            content_fake_calls.append("capture_metadata_dual_content")
            raise AssertionError("hostile content adapter executed")

        with monkeypatch.context() as hostile:
            hostile.setattr(
                PlanningContextManifestProducer,
                "capture_metadata_dual_content",
                hostile_content_adapter,
            )
            with pytest.raises(ContextManifestProducerError) as substituted:
                combined.capture_dual_snapshot_content(
                    binding, lease, selection, planning_items[0],
                )
            assert substituted.value.code == "invalid_provider"
            assert content_fake_calls == []
    finally:
        authority.release_read_lease(lease)
        authority.close()
