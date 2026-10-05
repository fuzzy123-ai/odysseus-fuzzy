from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import threading
import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from cryptography.fernet import Fernet

from core.auth import AuthManager
from src.causal_claim_store import CausalClaimStore
from src.harbour_authority import AuthenticatedPrincipalIssuer
from src.harbour_knowledge_migration import (
    CausalMigrationSource,
    migrate_prepared_knowledge_migration,
    prepare_authenticated_zero_census_knowledge_migration,
    prepare_current_knowledge_migration,
)
from src.harbour_knowledge_staging_destination import HarbourKnowledgeStagingDestinationService, _CONTEXT_SCHEMA, _sha
from src.harbour_knowledge_store import HarbourKnowledgeStore, HarbourKnowledgeStoreError
from src.unified_source_index_adapters import AdapterScope, ExtractionProfile, UnavailableObservation
from src.unified_source_index_contract import (
    ChunkRecord, Classification, ContentPolicy, RecordKind, RecordRef,
    SourceKind, SourceRecord, SourceVersionRecord, TextRangeLocator, content_hash,
)
from src.unified_source_index_owner_scope import OwnerScope
from src.unified_source_index_runtime_config import UnifiedSourceIndexRuntimeConfig
from src.unified_source_index_sqlite import SQLiteUnifiedSourceIndexStore
from src.unified_source_index_sources.knowledge import KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE, KnowledgeSourceAdapter
from src.unified_source_index_sources.knowledge import knowledge_source_capability_manifest
import src.agent_tools.knowledge_tools as knowledge_tools
import src.harbour_knowledge_active_runtime as runtime_module


NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
RETENTION = {
    "retention_class": "user_managed", "deletion_mode": "tombstone_only",
    "retain_until": None, "grace_until": None, "legal_hold": False,
}


def _runtime_issuer(key: bytes, *, clock=None) -> AuthenticatedPrincipalIssuer:
    if clock is None:
        clock = lambda: NOW
    return AuthenticatedPrincipalIssuer(
        issuer_id="harbour-knowledge-runtime", key_id="app-key-v1",
        secret=hmac.new(key, b"odysseus.harbour.knowledge.issuer.v1\0", hashlib.sha256).digest(),
        clock=clock,
    )


def _complete_fixture(tmp_path: Path, monkeypatch, *, nonzero_records: int = 0):
    encoded_key = Fernet.generate_key()
    key = base64.urlsafe_b64decode(encoded_key)
    key_path = tmp_path / "app.key"
    key_path.write_bytes(encoded_key)
    if os.name != "nt":
        key_path.chmod(0o600)
    monkeypatch.setattr(runtime_module, "APP_KEY_FILE", key_path)
    monkeypatch.setattr(runtime_module, "DATA_DIR", tmp_path)

    auth = AuthManager(str(tmp_path / "auth.json"))
    assert auth.create_user("alice", "Test-Password-930!", is_admin=True)
    subject = auth.subject_id_for_username("alice")
    owner_scope = OwnerScope.for_subject_id(subject).value

    causal = CausalClaimStore(sqlite3.connect(tmp_path / "causal.sqlite"), initialize=True)
    if nonzero_records:
        for index in range(nonzero_records):
            causal.create_claim({
                "schema": "odysseus.causal_claim.v1",
                "claim_id": f"orbital-claim-{index}", "owner": subject,
                "policy": "local-reviewed", "source_version": "sha256:" + str(index + 1) * 64,
                "claim_version": 1, "candidate_actor": "orbital",
                "evidence_refs": ["sha256:" + str(index + 3) * 64],
            })
        plan = prepare_current_knowledge_migration(
            causal_sources=(CausalMigrationSource(subject, causal, subject),),
            owner_map={subject: subject}, retention_by_scope={subject: dict(RETENTION)},
            observed_at="2026-10-01T12:00:00Z",
        )
    else:
        plan = prepare_authenticated_zero_census_knowledge_migration(
            causal_sources=(CausalMigrationSource(subject, causal, subject),),
            owner_map={subject: subject}, retention_by_scope={subject: dict(RETENTION)},
            observed_at="2026-10-01T12:00:00Z",
        )
    issuer = _runtime_issuer(key)
    staging = HarbourKnowledgeStagingDestinationService(
        tmp_path / "harbour-knowledge-staging", trusted_issuers=(issuer,), clock=lambda: NOW,
    )
    migration_context = _sha({
        "migration_id": plan.migration_id, "owner_id": subject,
        "schema_id": _CONTEXT_SCHEMA, "snapshot_digest": plan.source_manifest_digest,
    })
    migration_principal = issuer.issue(
        subject_id=subject, owner_id=subject, auth_context_digest=migration_context,
    )
    completion = migrate_prepared_knowledge_migration(
        plan, staging=staging, principals={subject: migration_principal},
    ).completion_reference
    staging.select_generation(completion, expected_selection_digest="sha256:" + "0" * 64)

    # A legitimate descendant is allowed after the selected historical
    # zero-census completion and is the source of the actual native projection.
    selected = staging.selected_runtime(subject)
    assert selected is not None
    store = selected[4]
    if not nonzero_records:
        runtime_principal = issuer.issue(
            subject_id=subject, owner_id=subject,
            auth_context_digest=_sha({"owner": subject, "purpose": "fixture-write"}),
        )
        store.create_object(
            runtime_principal, owner_id=subject, knowledge_id="active-note",
            knowledge_kind="note", payload_bytes=b"orbital blue memory",
            payload_schema_id="harbour.note.v1", payload_media_type="text/plain",
            classification="private", retention=dict(RETENTION), idempotency_key="active-note-create",
        )
    else:
        runtime_principal = migration_principal
    native = KnowledgeSourceAdapter(
        store=store, principal=runtime_principal, owner_id=subject,
        cursor_signing_key=b"c" * 32,
        classification_ceiling=Classification.SENSITIVE,
    )
    capability = native.describe_capability()
    page = native.discover(
        AdapterScope(owner_scope, Classification.SENSITIVE), cursor="", limit=100, time_budget_ms=5000,
    )
    sources = [item.source for item in page.items]
    records = []
    for source in sources:
        version = native.observe_version(RecordRef(RecordKind.SOURCE, source.source_id))
        extracted = native.extract(
            version, ExtractionProfile(KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE, 100, 100_000, 5000),
        )
        records.extend((source, version, *extracted.chunks))
    usi_path = tmp_path / "usi.sqlite"
    usi = SQLiteUnifiedSourceIndexStore(usi_path)
    write = usi.begin_write(usi.current_snapshot())
    for record in records:
        write.put(record)
    write.commit()

    generation = knowledge_source_capability_manifest().generation_ref
    config = UnifiedSourceIndexRuntimeConfig.from_environment({
        "ODYSSEUS_USI_RUNTIME_ENABLED": "true",
        "ODYSSEUS_USI_RUNTIME_MODE": "read_only",
        "ODYSSEUS_USI_SELECTED_GENERATION": generation,
        "ODYSSEUS_USI_ALLOWED_OWNERS": subject,
        "ODYSSEUS_USI_ALLOWED_SOURCES": ",".join(source.source_id for source in sources),
        "ODYSSEUS_USI_ALLOWED_DOMAINS": "native_knowledge",
        "ODYSSEUS_USI_SQLITE_PATH": "usi.sqlite",
        "ODYSSEUS_USI_QUERY_MAX_RESULTS": "8",
        "ODYSSEUS_USI_QUERY_TIMEOUT_SECONDS": "5",
    }, data_root=tmp_path)
    composition = runtime_module.ActiveKnowledgeComposition.open_existing(config, auth)
    staging.close()
    causal.close()
    return auth, subject, owner_scope, tuple(source.source_id for source in sources), composition, key_path


def _content(source_id: str | tuple[str, ...], *, query: str = "orbital", limit: int = 4) -> str:
    sources = (source_id,) if isinstance(source_id, str) else source_id
    return json.dumps({
        "query": query, "domain": "native_knowledge", "mode": "lexical",
        "scope": list(sources), "classification": "private",
        "budget": {"limit": limit, "max_candidates": 20, "time_budget_ms": 1000},
    })


def test_real_read_only_composition_projects_and_queries_through_fastapi(tmp_path, monkeypatch):
    auth, _subject, owner_scope, source_ids, composition, _key_path = _complete_fixture(tmp_path, monkeypatch)
    previous_planner = knowledge_tools._planner
    previous_resolver = knowledge_tools._owner_resolver
    knowledge_tools.set_query_knowledge_planner(composition)
    knowledge_tools.set_query_knowledge_owner_resolver(auth.subject_id_for_username)
    app = FastAPI()

    @app.post("/knowledge/query")
    async def query(body: dict):
        return await knowledge_tools.query_knowledge(json.dumps(body), {"owner": "alice"})

    payload = json.loads(_content(source_ids))
    parsed = knowledge_tools._request(_content(source_ids), {"owner": "alice"})
    composition.execute(parsed.federated_query())
    with TestClient(app) as client:
        response = client.post("/knowledge/query", json=payload)
    assert response.status_code == 200
    assert response.json()["exit_code"] == 0, response.json()
    assert response.json()["result_count"] >= 1
    assert response.json()["results"][0]["evidence"]["source_id"] in source_ids
    assert owner_scope.startswith("owner:")
    composition.close()
    try:
        query_page = asyncio.run(knowledge_tools.query_knowledge(_content(source_ids), {"owner": "alice"}))
        assert query_page["exit_code"] == 1
    finally:
        knowledge_tools.set_query_knowledge_planner(previous_planner)
        knowledge_tools.set_query_knowledge_owner_resolver(previous_resolver)


def test_deleted_current_account_is_denied_before_usi_or_harbour_calls(tmp_path, monkeypatch):
    auth, subject, _scope, source_ids, composition, _key_path = _complete_fixture(tmp_path, monkeypatch)
    calls = []
    original_snapshot = composition._usi.current_snapshot
    original_selected = composition._staging.selected_runtime
    composition._usi.current_snapshot = lambda: (calls.append("usi"), original_snapshot())[1]
    composition._staging.selected_runtime = lambda owner: (calls.append("harbour"), original_selected(owner))[1]
    with auth._config_lock:
        auth._config["users"].pop("alice")
    from src.unified_source_index_query import FederatedQuery, FederatedQueryBudget, QueryMode

    query = FederatedQuery(
        owner_scope=OwnerScope.for_subject_id(subject).value, query_text="orbital",
        classification_ceiling=Classification.PRIVATE, modes=(QueryMode.LEXICAL,),
        limit=2, source_ids=source_ids, domain="native_knowledge",
        budget=FederatedQueryBudget(max_providers=1, max_candidates_per_provider=4,
                                    max_total_candidates=4, time_budget_ms=1000, per_provider_timeout_ms=500),
    )
    try:
        import pytest
        with pytest.raises(ValueError, match="account unavailable"):
            composition.execute(query)
        assert calls == []
    finally:
        composition.close()


def test_read_only_startup_failure_and_disabled_path_do_not_create_state(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_module, "APP_KEY_FILE", tmp_path / "missing-key")
    monkeypatch.setattr(runtime_module, "DATA_DIR", tmp_path)
    auth = AuthManager(str(tmp_path / "auth.json"))
    disabled = UnifiedSourceIndexRuntimeConfig.for_test(tmp_path / "absent.sqlite")
    assert runtime_module.build_active_knowledge_composition(disabled, auth) is None
    assert not (tmp_path / "harbour-knowledge-staging").exists()
    assert not (tmp_path / "absent.sqlite").exists()
    active = UnifiedSourceIndexRuntimeConfig.from_environment({
        "ODYSSEUS_USI_RUNTIME_ENABLED": "true", "ODYSSEUS_USI_RUNTIME_MODE": "read_only",
        "ODYSSEUS_USI_SELECTED_GENERATION": knowledge_source_capability_manifest().generation_ref,
        "ODYSSEUS_USI_ALLOWED_OWNERS": "owner_fixture",
        "ODYSSEUS_USI_ALLOWED_SOURCES": "source_fixture",
        "ODYSSEUS_USI_ALLOWED_DOMAINS": "native_knowledge",
        "ODYSSEUS_USI_SQLITE_PATH": "absent.sqlite",
    }, data_root=tmp_path)
    assert runtime_module.build_active_knowledge_composition(active, auth) is None
    assert not (tmp_path / "absent.sqlite").exists()
    assert not (tmp_path / "harbour-knowledge-staging").exists()


def test_nonzero_migration_cursors_resume_after_runtime_restart_and_bind_key_and_selection(tmp_path, monkeypatch):
    from dataclasses import replace
    import pytest
    from src.unified_source_index_query import FederatedQuery, FederatedQueryBudget, QueryMode
    from src.unified_source_index_query_contract import LexicalMatchMode, LexicalQuery

    auth, subject, owner_scope, source_ids, first, key_path = _complete_fixture(
        tmp_path, monkeypatch, nonzero_records=2,
    )
    selection_a_reference, _owner_set_digest, selection_a_digest = first._staging.current_selection()
    assert len(source_ids) == 2
    native_instances = []
    lexical_instances = []
    original_discover = runtime_module.KnowledgeSourceAdapter.discover
    original_search = runtime_module._OwnerSnapshotLexicalRetriever.search

    def capture_discover(adapter, *args, **kwargs):
        native_instances.append(adapter)
        page = original_discover(adapter, *args, **kwargs)
        if page.next_cursor:
            native_cursors.append(page.next_cursor)
        return page

    def capture_search(retriever, query):
        lexical_instances.append(retriever)
        return original_search(retriever, query)

    native_cursors = []
    monkeypatch.setattr(runtime_module.KnowledgeSourceAdapter, "discover", capture_discover)
    monkeypatch.setattr(runtime_module._OwnerSnapshotLexicalRetriever, "search", capture_search)
    federated = FederatedQuery(
        owner_scope=owner_scope, query_text="orbital",
        classification_ceiling=Classification.SENSITIVE, modes=(QueryMode.LEXICAL,),
        limit=2, source_ids=source_ids, domain="native_knowledge",
        budget=FederatedQueryBudget(max_providers=1, max_candidates_per_provider=8,
                                    max_total_candidates=20, time_budget_ms=5000,
                                    per_provider_timeout_ms=750),
    )
    try:
        result_a = first.execute(federated)
        native = native_instances[-1]
        first_discovery = native.discover(
            AdapterScope(owner_scope, Classification.SENSITIVE, source_ids),
            cursor="", limit=1, time_budget_ms=1000,
        )
        assert first_discovery.next_cursor
        native_cursors.append(first_discovery.next_cursor)
        assert len(result_a.items) == 2 and native_cursors and native_instances
        principal_a = native_instances[0]._principal
        lexical_query = LexicalQuery(
            owner_scope=owner_scope, query_text="orbital", match_mode=LexicalMatchMode.TOKEN,
            classification_ceiling=Classification.SENSITIVE, limit=1, max_candidates=8,
            source_ids=source_ids, domain="native_knowledge",
        )
        lexical_page_a = lexical_instances[-1].search(lexical_query)
        assert lexical_page_a.next_cursor
        unrelated_source = SourceRecord(
            owner_scope=owner_scope, source_kind=SourceKind.CODE,
            canonical_ref="repo:owner-snapshot-unrelated.py",
            classification=Classification.PRIVATE,
            content_policy=ContentPolicy.INLINE_LOCAL, provider_ref="local-git",
            first_seen_at="2026-10-01T12:00:00Z", source_modified_at="2026-10-01T12:00:00Z",
            valid_from="2026-10-01T12:00:00Z",
        )
        unrelated_version = SourceVersionRecord.create(
            unrelated_source, revision_ref="git:owner-snapshot-unrelated",
            content_hash=content_hash("owner-snapshot-unrelated"),
            version_observed_at="2026-10-01T12:00:00Z", indexed_at="2026-10-01T12:00:00Z",
        )
        unrelated_content = "orbital dominant terms from unrelated same-owner data"
        unrelated_chunk = ChunkRecord.create(
            unrelated_version,
            locator=TextRangeLocator(0, len(unrelated_content)),
            extractor_profile_ref="text-v1", content_hash=content_hash(unrelated_content),
            content=unrelated_content, indexed_at="2026-10-01T12:00:00Z",
        )
        writable_usi = SQLiteUnifiedSourceIndexStore(first._usi._path)
        unrelated_write = writable_usi.begin_write(writable_usi.current_snapshot())
        for record in (unrelated_source, unrelated_version, unrelated_chunk):
            unrelated_write.put(record)
        unrelated_write.commit()
        resumed_same_snapshot = lexical_instances[-1].search(
            replace(lexical_query, cursor=lexical_page_a.next_cursor),
        )
        assert len(resumed_same_snapshot.items) == 1

        # Simulate a process restart against the same authenticated selection,
        # app key, config and on-disk USI snapshot.
        first.close()
        second = runtime_module.ActiveKnowledgeComposition.open_existing(first._config, auth)
        result_b = second.execute(federated)
        assert len(result_b.items) == 2 and len(native_instances) > 2
        native = native_instances[-1]
        resumed_native = native.discover(
            AdapterScope(owner_scope, Classification.SENSITIVE, source_ids),
            cursor=native_cursors[0], limit=1, time_budget_ms=1000,
        )
        assert len(resumed_native.items) == 1
        resumed_lexical = lexical_instances[-1].search(
            replace(lexical_query, cursor=lexical_page_a.next_cursor),
        )
        assert len(resumed_lexical.items) == 1
        second.close()

        # Select a genuinely new canonical generation under the same key. Old
        # native and lexical cursors must no longer resume against this selection.
        key_bytes = base64.urlsafe_b64decode(key_path.read_bytes())
        causal = CausalClaimStore(sqlite3.connect(tmp_path / "causal-next.sqlite"), initialize=True)
        for index in range(3):
            causal.create_claim({
                "schema": "odysseus.causal_claim.v1", "claim_id": f"orbital-claim-{index}",
                "owner": subject, "policy": "local-reviewed", "source_version": "sha256:" + str(index + 1) * 64,
                "claim_version": 1, "candidate_actor": "orbital", "evidence_refs": ["sha256:" + str(index + 3) * 64],
            })
        new_plan = prepare_current_knowledge_migration(
            causal_sources=(CausalMigrationSource(subject, causal, subject),),
            owner_map={subject: subject}, retention_by_scope={subject: dict(RETENTION)},
            observed_at="2026-10-01T12:01:00Z",
        )
        issuer = _runtime_issuer(key_bytes)
        staging = HarbourKnowledgeStagingDestinationService.open_existing(
            tmp_path / "harbour-knowledge-staging", trusted_issuers=(issuer,), clock=lambda: NOW,
        )
        context = _sha({"migration_id": new_plan.migration_id, "owner_id": subject,
                        "schema_id": _CONTEXT_SCHEMA,
                        "snapshot_digest": new_plan.source_manifest_digest})
        migration_principal = issuer.issue(
            subject_id=subject, owner_id=subject, auth_context_digest=context,
        )
        new_completion = migrate_prepared_knowledge_migration(
            new_plan, staging=staging, principals={subject: migration_principal},
        ).completion_reference
        prior_selection = staging.current_selection()[2]
        selection_b_digest = staging.select_generation(new_completion, expected_selection_digest=prior_selection)
        selected_b = staging.selected_runtime(subject)
        assert selected_b is not None
        b_store = selected_b[4]
        b_principal = issuer.issue(
            subject_id=subject, owner_id=subject,
            auth_context_digest=_sha({"owner": subject, "purpose": "test-b-projection"}),
        )
        b_native = KnowledgeSourceAdapter(
            store=b_store, principal=b_principal, owner_id=subject,
            cursor_signing_key=b"b" * 32,
            classification_ceiling=Classification.SENSITIVE,
        )
        selected_current_ids = []
        b_items = []
        b_cursor = ""
        while True:
            b_page = b_native.discover(
                AdapterScope(owner_scope, Classification.SENSITIVE),
                cursor=b_cursor, limit=100, time_budget_ms=1000,
            )
            selected_current_ids.extend(item.source.source_id for item in b_page.items)
            b_items.extend(b_page.items)
            if not b_page.next_cursor:
                break
            b_cursor = b_page.next_cursor
        assert selected_current_ids
        b_records = []
        for item in b_items:
            version = b_native.observe_version(RecordRef(RecordKind.SOURCE, item.source.source_id))
            assert not isinstance(version, UnavailableObservation)
            extracted = b_native.extract(
                version, ExtractionProfile(KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE, 100, 1_000_000, 5000),
            )
            b_records.extend((item.source, version, *extracted.chunks))
        writable_usi = SQLiteUnifiedSourceIndexStore(first._usi._path)
        b_write = writable_usi.begin_write(writable_usi.current_snapshot())
        for record in b_records:
            b_write.put(record)
        b_write.commit()
        with writable_usi.begin_read() as b_read:
            b_current_sources = {
                source_id: b_read.require(RecordKind.SOURCE, source_id, owner_scope=owner_scope).record.to_json()
                for source_id in selected_current_ids
            }
        first._config = replace(
            first._config,
            allowed_sources=tuple(sorted(set(source_ids) | set(selected_current_ids))),
        )
        staging.close()
        causal.close()
        third = runtime_module.ActiveKnowledgeComposition.open_existing(first._config, auth)
        result_c = third.execute(federated)
        assert len(native_instances) > 4
        b_query = replace(federated, source_ids=tuple(selected_current_ids))
        b_result = third.execute(b_query)
        assert b_result.items
        assert {item.evidence.source_id for item in b_result.items} <= set(selected_current_ids)
        with pytest.raises(Exception):
            native_instances[-1].discover(
                AdapterScope(owner_scope, Classification.SENSITIVE, source_ids),
                cursor=native_cursors[0], limit=1, time_budget_ms=1000,
            )
        with pytest.raises(Exception):
            lexical_instances[-1].search(replace(lexical_query, cursor=lexical_page_a.next_cursor))

        # A rollback during planner execution invalidates the in-flight B result
        # at the runtime's final selection read.
        original_planner_execute = runtime_module.UnifiedSourceIndexQueryPlanner.execute
        rollback_receipts = []
        rollback_issuer = AuthenticatedPrincipalIssuer(
            issuer_id="harbour-knowledge-runtime", key_id="app-key-v1",
            secret=hmac.new(
                key_bytes, b"odysseus.harbour.knowledge.issuer.v1\0", hashlib.sha256,
            ).digest(),
        )

        def rollback_during_planner(planner, bounded_query):
            result = original_planner_execute(planner, bounded_query)
            rollback_context = third._staging.rollback_authority_context_digest(
                selection_b_digest, selection_a_digest,
            )
            rollback_principal = rollback_issuer.issue(
                subject_id=subject, owner_id=subject, auth_context_digest=rollback_context,
            )
            rollback_receipts.append(third._staging.rollback_generation(
                selection_a_reference, expected_selection_digest=selection_b_digest,
                target_selection_digest=selection_a_digest, principals={subject: rollback_principal},
            ))
            return result

        monkeypatch.setattr(runtime_module.UnifiedSourceIndexQueryPlanner, "execute", rollback_during_planner)
        with pytest.raises(ValueError) as postflight_denied:
            third.execute(b_query)
        assert postflight_denied.value.args == ("knowledge selection changed",)
        assert len(rollback_receipts) == 1
        assert third._staging.current_selection()[2] == rollback_receipts[0].rollback_selection_digest
        monkeypatch.setattr(runtime_module.UnifiedSourceIndexQueryPlanner, "execute", original_planner_execute)
        third.close()

        # The A target can serve new cursors after a fresh process binds Aprime.
        rollback_restart = runtime_module.ActiveKnowledgeComposition.open_existing(first._config, auth)
        rollback_page = rollback_restart.execute(federated)
        assert len(rollback_page.items) == 2
        b_readback = SQLiteUnifiedSourceIndexStore(rollback_restart._usi._path)
        with b_readback.begin_read() as readback:
            assert {
                source_id: readback.require(RecordKind.SOURCE, source_id, owner_scope=owner_scope).record.to_json()
                for source_id in selected_current_ids
            } == b_current_sources
        rollback_native = native_instances[-1]
        fresh_native_page = rollback_native.discover(
            AdapterScope(owner_scope, Classification.SENSITIVE, source_ids),
            cursor="", limit=1, time_budget_ms=1000,
        )
        fresh_a_cursor = fresh_native_page.next_cursor
        assert fresh_a_cursor
        fresh_a_positive = rollback_native.discover(
            AdapterScope(owner_scope, Classification.SENSITIVE, source_ids),
            cursor=fresh_a_cursor, limit=1, time_budget_ms=1000,
        )
        assert fresh_a_positive.items
        rollback_lexical = lexical_instances[-1].search(lexical_query)
        assert rollback_lexical.next_cursor
        assert lexical_instances[-1].search(
            replace(lexical_query, cursor=rollback_lexical.next_cursor),
        ).items
        rollback_restart.close()

        # A key change also creates a fresh binding and rejects the old principal.
        old_key = key_path.read_bytes()
        changed_encoded_key = Fernet.generate_key()
        key_path.write_bytes(changed_encoded_key)
        fourth = runtime_module.ActiveKnowledgeComposition.open_existing(first._config, auth)
        result_d = fourth.execute(federated)
        assert len(result_d.items) <= 2 and native_instances and lexical_instances
        new_store = fourth._staging.selected_runtime(subject)[4]
        with pytest.raises(HarbourKnowledgeStoreError) as denied:
            new_store.capture_index_snapshot(principal_a, owner_id=subject)
        assert denied.value.code == "unauthenticated"
        assert new_store.capture_index_snapshot(native_instances[-1]._principal, owner_id=subject).owner_id == subject
        with pytest.raises(Exception):
            native_instances[-1].discover(
                AdapterScope(owner_scope, Classification.SENSITIVE, source_ids),
                cursor=native_cursors[0], limit=1, time_budget_ms=1000,
            )
        with pytest.raises(Exception):
            lexical_instances[-1].search(replace(lexical_query, cursor=lexical_page_a.next_cursor))
        key_path.write_bytes(old_key)
        fourth.close()
        third.close()
    finally:
        first.close()


def test_selected_generation_mismatch_fails_before_secret_or_database_io(tmp_path, monkeypatch):
    auth = AuthManager(str(tmp_path / "auth.json"))
    config = UnifiedSourceIndexRuntimeConfig.from_environment({
        "ODYSSEUS_USI_RUNTIME_ENABLED": "true", "ODYSSEUS_USI_RUNTIME_MODE": "read_only",
        "ODYSSEUS_USI_SELECTED_GENERATION": "usi_generation_" + "b" * 64,
        "ODYSSEUS_USI_ALLOWED_OWNERS": "owner_fixture",
        "ODYSSEUS_USI_ALLOWED_SOURCES": "source_fixture",
        "ODYSSEUS_USI_ALLOWED_DOMAINS": "native_knowledge",
        "ODYSSEUS_USI_SQLITE_PATH": "absent.sqlite",
    }, data_root=tmp_path)
    monkeypatch.setattr(runtime_module, "_app_secret", lambda: (_ for _ in ()).throw(AssertionError("secret read")))
    assert runtime_module.build_active_knowledge_composition(config, auth) is None
    assert not (tmp_path / "absent.sqlite").exists()
    assert not (tmp_path / "harbour-knowledge-staging").exists()


@pytest.mark.parametrize("mutation, expected_error", [
    ("replace", "knowledge key changed"),
    ("malformed", "application key unavailable"),
    ("delete", "application key unavailable"),
])
def test_key_currentness_is_rechecked_after_planner_execution(
    tmp_path, monkeypatch, mutation, expected_error,
):
    import pytest
    from src.unified_source_index_query import FederatedQuery, FederatedQueryBudget, QueryMode

    _auth, _subject, owner_scope, source_ids, composition, key_path = _complete_fixture(
        tmp_path, monkeypatch, nonzero_records=2,
    )
    original_execute = runtime_module.UnifiedSourceIndexQueryPlanner.execute
    changed = []

    def execute_then_mutate(planner, query):
        result = original_execute(planner, query)
        assert result.items
        if not changed:
            changed.append(True)
            if mutation == "replace":
                key_path.write_bytes(Fernet.generate_key())
            elif mutation == "malformed":
                key_path.write_bytes(b"not-a-valid-application-key")
            else:
                key_path.unlink()
        return result

    monkeypatch.setattr(runtime_module.UnifiedSourceIndexQueryPlanner, "execute", execute_then_mutate)
    query = FederatedQuery(
        owner_scope=owner_scope, query_text="orbital",
        classification_ceiling=Classification.SENSITIVE, modes=(QueryMode.LEXICAL,),
        limit=2, source_ids=source_ids, domain="native_knowledge",
        budget=FederatedQueryBudget(max_providers=1, max_candidates_per_provider=8,
                                    max_total_candidates=20, time_budget_ms=1000,
                                    per_provider_timeout_ms=750),
    )
    try:
        with pytest.raises(ValueError) as denied:
            composition.execute(query)
        assert denied.value.args == (expected_error,)
        assert changed == [True]
    finally:
        composition.close()


def test_native_page_timeout_cannot_be_reset_between_discovery_steps(tmp_path, monkeypatch):
    import time
    import pytest
    from src.unified_source_index_query import FederatedQuery, FederatedQueryBudget, QueryMode
    from src.unified_source_index_sources.knowledge import KnowledgeSourceAdapterError

    _auth, _subject, owner_scope, source_ids, composition, _key_path = _complete_fixture(tmp_path, monkeypatch)
    original = runtime_module.KnowledgeSourceAdapter.discover

    def slow_discover(adapter, *args, **kwargs):
        time.sleep(0.025)
        return original(adapter, *args, **kwargs)

    monkeypatch.setattr(runtime_module.KnowledgeSourceAdapter, "discover", slow_discover)
    query = FederatedQuery(
        owner_scope=owner_scope, query_text="orbital", classification_ceiling=Classification.PRIVATE,
        modes=(QueryMode.LEXICAL,), limit=1, source_ids=source_ids,
        domain="native_knowledge",
        budget=FederatedQueryBudget(max_providers=1, max_candidates_per_provider=4,
                                    max_total_candidates=8, time_budget_ms=10,
                                    per_provider_timeout_ms=5),
    )
    try:
        try:
            composition.execute(query)
        except KnowledgeSourceAdapterError as raised:
            assert raised.code == "budget_exceeded"
        except ValueError as raised:
            assert raised.args == ("knowledge query timed out",)
        else:
            pytest.fail("absolute query deadline was not enforced")
    finally:
        composition.close()


def test_final_selection_read_cannot_return_results_after_absolute_deadline(tmp_path, monkeypatch):
    import time
    from src.unified_source_index_query import FederatedQuery, FederatedQueryBudget, QueryMode

    _auth, _subject, owner_scope, source_ids, composition, _key_path = _complete_fixture(
        tmp_path, monkeypatch,
    )
    original_current_selection = composition._staging.current_selection
    original_planner_execute = runtime_module.UnifiedSourceIndexQueryPlanner.execute
    selection_reads = 0
    planner_results = []

    def delay_final_selection_read():
        nonlocal selection_reads
        selection_reads += 1
        if selection_reads == 2:
            time.sleep(1.1)
        return original_current_selection()

    def capture_planner_result(planner, bounded_query):
        result = original_planner_execute(planner, bounded_query)
        planner_results.append(result)
        return result

    monkeypatch.setattr(composition._staging, "current_selection", delay_final_selection_read)
    monkeypatch.setattr(runtime_module.UnifiedSourceIndexQueryPlanner, "execute", capture_planner_result)
    query = FederatedQuery(
        owner_scope=owner_scope, query_text="orbital", classification_ceiling=Classification.PRIVATE,
        modes=(QueryMode.LEXICAL,), limit=1, source_ids=source_ids,
        domain="native_knowledge",
        budget=FederatedQueryBudget(max_providers=1, max_candidates_per_provider=4,
                                    max_total_candidates=8, time_budget_ms=1000,
                                    per_provider_timeout_ms=750),
    )
    try:
        with pytest.raises(ValueError) as denied:
            composition.execute(query)
        assert denied.value.args == ("knowledge query timed out",)
        assert selection_reads == 2
        assert len(planner_results) == 1 and planner_results[0].items
    finally:
        composition.close()


def test_partial_startup_and_failed_usi_close_still_close_staging(tmp_path, monkeypatch):
    import pytest

    from types import SimpleNamespace
    config = UnifiedSourceIndexRuntimeConfig.from_environment({
        "ODYSSEUS_USI_RUNTIME_ENABLED": "true", "ODYSSEUS_USI_RUNTIME_MODE": "read_only",
        "ODYSSEUS_USI_SELECTED_GENERATION": knowledge_source_capability_manifest().generation_ref,
        "ODYSSEUS_USI_ALLOWED_OWNERS": "owner_fixture",
        "ODYSSEUS_USI_ALLOWED_SOURCES": "source_fixture",
        "ODYSSEUS_USI_ALLOWED_DOMAINS": "native_knowledge",
        "ODYSSEUS_USI_SQLITE_PATH": "usi.sqlite",
    }, data_root=tmp_path)
    closed = []
    staging = SimpleNamespace(current_selection=lambda: (_ for _ in ()).throw(ValueError("bounded")),
                              close=lambda: closed.append("staging"))
    usi = SimpleNamespace(close=lambda: closed.append("usi"))
    monkeypatch.setattr(runtime_module, "_app_secret", lambda: b"k" * 32)
    monkeypatch.setattr(runtime_module.HarbourKnowledgeStagingDestinationService,
                        "open_existing", lambda *_args, **_kwargs: staging)
    monkeypatch.setattr(runtime_module, "_ReadOnlySQLiteUSI", lambda *_args, **_kwargs: usi)
    with pytest.raises(ValueError, match="bounded"):
        runtime_module.ActiveKnowledgeComposition.open_existing(config, object())
    assert closed == ["usi", "staging"]

    closed.clear()
    staging.current_selection = lambda: (object(), "sha256:" + "a" * 64, "sha256:" + "b" * 64)
    monkeypatch.setattr(runtime_module.ActiveKnowledgeComposition, "__init__",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("constructor")))
    with pytest.raises(ValueError, match="constructor"):
        runtime_module.ActiveKnowledgeComposition.open_existing(config, object())
    assert closed == ["usi", "staging"]

    closed.clear()
    composition = object.__new__(runtime_module.ActiveKnowledgeComposition)
    composition._condition = threading.Condition(threading.RLock())
    composition._closed = False
    composition._inflight = 0
    composition._usi = SimpleNamespace(close=lambda: (_ for _ in ()).throw(ValueError("usi close")))
    composition._staging = SimpleNamespace(close=lambda: closed.append("staging"))
    with pytest.raises(ValueError, match="cleanup failed"):
        composition.close()
    assert closed == ["staging"]
    with pytest.raises(ValueError, match="runtime closed"):
        composition.execute(object())


def test_inbox_guard_reserves_active_composition_until_exit():
    composition = object.__new__(runtime_module.ActiveKnowledgeComposition)
    composition._condition = threading.Condition(threading.RLock())
    composition._closed = False
    composition._inflight = 0
    with composition.guard_inbox_read():
        assert composition._inflight == 1
    assert composition._inflight == 0


def test_close_waits_for_an_inflight_inbox_reservation_before_closing_dependencies():
    composition = object.__new__(runtime_module.ActiveKnowledgeComposition)
    composition._condition = threading.Condition(threading.RLock())
    composition._closed = False
    composition._inflight = 0
    closed = []
    class Closeable:
        def __init__(self, name): self._name = name
        def close(self): closed.append(self._name)
    composition._usi = Closeable("usi")
    composition._staging = Closeable("staging")
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    def reader():
        with composition.guard_inbox_read():
            entered.set()
            assert release.wait(2)
    def closer():
        composition.close()
        finished.set()
    reader_thread = threading.Thread(target=reader)
    reader_thread.start()
    assert entered.wait(2)
    close_thread = threading.Thread(target=closer)
    close_thread.start()
    assert not finished.wait(0.05) and closed == []
    release.set()
    reader_thread.join(2)
    close_thread.join(2)
    assert not reader_thread.is_alive() and not close_thread.is_alive()
    assert finished.is_set() and closed == ["usi", "staging"]


def test_actual_inbox_capture_is_frozen_revalidated_and_bound_to_the_selected_store(tmp_path, monkeypatch):
    auth, subject, _scope, _sources, composition, key_path = _complete_fixture(tmp_path, monkeypatch)
    try:
        key = base64.urlsafe_b64decode(key_path.read_bytes())
        issuer = _runtime_issuer(key, clock=lambda: datetime.now(timezone.utc))
        store = composition._staging.selected_runtime(subject)[4]
        writer = issuer.issue(
            subject_id=subject, owner_id=subject,
            auth_context_digest=_sha({"fixture": "inbox-capture"}),
        )
        retention = {
            "retention_class": "user_managed", "deletion_mode": "purge_eligible",
            "retain_until": None, "grace_until": None, "legal_hold": False,
        }
        store.create_object(
            writer, owner_id=subject, knowledge_id="inbox-purge-note", knowledge_kind="note",
            payload_bytes=b"private fixture", payload_schema_id="harbour.note.v1",
            payload_media_type="text/plain", classification="private", retention=retention,
            idempotency_key="inbox-capture-create",
        )
        first = store.get_head(writer, owner_id=subject, knowledge_id="inbox-purge-note")
        store.tombstone(writer, owner_id=subject, knowledge_id="inbox-purge-note", expected_head=first,
                        idempotency_key="inbox-capture-tombstone")
        second = store.get_head(writer, owner_id=subject, knowledge_id="inbox-purge-note")
        store.request_purge(writer, owner_id=subject, knowledge_id="inbox-purge-note", expected_head=second,
                            idempotency_key="inbox-capture-request")
        identity = type("InboxIdentity", (), {"owner_subject_id": subject, "owner_alias": "alice"})()
        captured = composition.capture_inbox_snapshot(identity=identity, limit=100)
        assert type(captured) is runtime_module.KnowledgeInboxSnapshot
        assert type(captured.items) is tuple and captured.items and captured.clipped is False
        assert all(type(item).__name__ == "KnowledgeInboxSignal" for item in captured.items)
        assert all(type(item.generation) is int and item.generation >= 1 for item in captured.items)
        assert composition.capture_inbox_snapshot(
            identity=identity, limit=100, expected_capture=captured,
        ) == captured
        store.create_object(
            writer, owner_id=subject, knowledge_id="inbox-purge-note-2", knowledge_kind="note",
            payload_bytes=b"private fixture two", payload_schema_id="harbour.note.v1",
            payload_media_type="text/plain", classification="private", retention=retention,
            idempotency_key="inbox-capture-create-two",
        )
        with pytest.raises(ValueError):
            composition.capture_inbox_snapshot(identity=identity, limit=100, expected_capture=captured)
    finally:
        composition.close()


def test_actual_inbox_capture_stays_reserved_by_the_outer_service_guard_until_revalidation_finishes(tmp_path, monkeypatch):
    _auth, subject, _scope, _sources, composition, _key_path = _complete_fixture(tmp_path, monkeypatch)
    identity = type("InboxIdentity", (), {"owner_subject_id": subject, "owner_alias": "alice"})()
    close_done = threading.Event()
    captured = []
    try:
        with composition.guard_inbox_read():
            captured.append(composition.capture_inbox_snapshot(
                identity=identity, limit=1, _reservation_held=True,
            ))
            closer = threading.Thread(target=lambda: (composition.close(), close_done.set()))
            closer.start()
            assert not close_done.wait(.05)
        closer.join(2)
        assert not closer.is_alive() and close_done.is_set()
        assert type(captured[0]) is runtime_module.KnowledgeInboxSnapshot
    finally:
        if not close_done.is_set():
            composition.close()
