from __future__ import annotations

import ast
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from pathlib import Path
import sqlite3
import threading
import time

import pytest

from core.auth import AuthManager, SetAdminResult
from src.bigdata_ledger_contract import BigDataLedgerItem, BigDataLedgerRecord
from src.causal_claim_store import CausalClaimStore
from src.harbour_authority import AuthenticatedPrincipalIssuer
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
from src.nextcloud_knowledge_ingestion import (
    NextcloudKnowledgeIngestionError,
    NextcloudKnowledgeIngestionResult,
)
import src.nextcloud_knowledge_ingestion_composition as composition_module
from src.nextcloud_knowledge_ingestion_composition import (
    NextcloudKnowledgeIngestionComposition,
    NextcloudKnowledgeIngestionCompositionError,
)
from src.personal_docs_owner_reader import PersonalDocsOwnerReader
from src.unified_source_index_runtime_config import (
    UnifiedSourceIndexRuntimeConfig,
    UnifiedSourceIndexRuntimeMode,
)
from src.unified_source_index_sources.knowledge import (
    knowledge_source_capability_manifest,
)


RETENTION = {
    "retention_class": "user_managed",
    "deletion_mode": "tombstone_only",
    "retain_until": None,
    "grace_until": None,
    "legal_hold": False,
}
ROOT_REF = "sha256:" + "b" * 64
OBSERVED = "2026-10-09T06:59:00Z"
PASSWORD = "Test-Password-930!"


def _runtime_issuer(key: bytes) -> AuthenticatedPrincipalIssuer:
    return AuthenticatedPrincipalIssuer(
        issuer_id="harbour-knowledge-runtime",
        key_id="app-key-v1",
        secret=hmac.new(
            key,
            b"odysseus.harbour.knowledge.issuer.v1\0",
            hashlib.sha256,
        ).digest(),
    )


def _config(tmp_path: Path, owners: tuple[str, ...]) -> UnifiedSourceIndexRuntimeConfig:
    return UnifiedSourceIndexRuntimeConfig(
        data_root=tmp_path,
        sqlite_path=tmp_path / "unused-usi.sqlite",
        mode=UnifiedSourceIndexRuntimeMode.READ_ONLY,
        runtime_enabled=True,
        selected_generation=knowledge_source_capability_manifest().generation_ref,
        allowed_owners=owners,
        allowed_sources=("nextcloud-synthetic",),
        allowed_domains=("native_knowledge",),
        cbm_provider_enabled=False,
        lineage_provider_enabled=False,
        raptor_provider_enabled=False,
        chroma_provider_enabled=False,
        query_max_results=8,
        query_timeout_seconds=5,
        worker_batch_size=8,
        worker_max_concurrency=1,
        worker_max_seconds=30,
        shadow_sample_rate_percent=0,
        stale_generation_max_age_seconds=86400,
        fallback_max_attempts=1,
        circuit_breaker_failure_threshold=3,
        circuit_breaker_reset_seconds=60,
    )


def _inventory(relative_path: str, content: str) -> BigDataLedgerRecord:
    return BigDataLedgerRecord(
        item=BigDataLedgerItem(
            provider="nextcloud",
            source_id="nextcloud-synthetic",
            relative_path=relative_path,
            size_bytes=len(content.encode("utf-8")),
            mtime=OBSERVED,
        ),
        stage="inventory",
        status="completed",
        updated_at=OBSERVED,
        metadata={
            "file_category": "text_extractable",
            "extension": ".txt",
            "privacy": {
                "memory_write_candidate": True,
                "privacy_class": "archive_candidate",
            },
        },
    )


def _zero_completion(
    tmp_path: Path,
    service: HarbourKnowledgeStagingDestinationService,
    issuer: AuthenticatedPrincipalIssuer,
    name: str,
    owners: tuple[str, ...],
):
    connections: list[sqlite3.Connection] = []
    sources: list[CausalMigrationSource] = []
    try:
        for index, owner in enumerate(owners):
            connection = sqlite3.connect(tmp_path / f"{name}-{index}.sqlite")
            connections.append(connection)
            source = CausalClaimStore(connection, initialize=True)
            sources.append(CausalMigrationSource(owner, source, owner))
        observed_at = (
            datetime(2026, 10, 1, tzinfo=timezone.utc)
            + timedelta(seconds=int(hashlib.sha256(name.encode("ascii")).hexdigest()[:4], 16))
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        plan = prepare_authenticated_zero_census_knowledge_migration(
            causal_sources=tuple(sources),
            owner_map={owner: owner for owner in owners},
            retention_by_scope={owner: dict(RETENTION) for owner in owners},
            observed_at=observed_at,
        )
        principals = {
            owner: issuer.issue(
                subject_id=owner,
                owner_id=owner,
                auth_context_digest=_sha(
                    {
                        "migration_id": plan.migration_id,
                        "owner_id": owner,
                        "schema_id": _CONTEXT_SCHEMA,
                        "snapshot_digest": plan.source_manifest_digest,
                    }
                ),
            )
            for owner in owners
        }
        return migrate_prepared_knowledge_migration(
            plan,
            staging=service,
            principals=principals,
        ).completion_reference
    finally:
        for connection in connections:
            connection.close()


@dataclass
class _Fixture:
    tmp_path: Path
    auth: AuthManager
    subject: str
    config: UnifiedSourceIndexRuntimeConfig
    key: bytes
    issuer: AuthenticatedPrincipalIssuer
    staging_root: Path
    reader: PersonalDocsOwnerReader
    record: BigDataLedgerRecord
    content: str
    composition: NextcloudKnowledgeIngestionComposition
    selected_database: Path


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch):
    key = b"k" * 32
    issuer = _runtime_issuer(key)
    auth = AuthManager(str(tmp_path / "auth.json"))
    assert auth.create_user("alice", PASSWORD, is_admin=True)
    subject = auth.subject_id_for_username("alice")
    assert type(subject) is str

    staging_root = tmp_path / "harbour-knowledge-staging"
    staging = HarbourKnowledgeStagingDestinationService(
        staging_root,
        trusted_issuers=(issuer,),
    )
    completion = _zero_completion(tmp_path, staging, issuer, "initial", (subject,))
    staging.select_generation(
        completion,
        expected_selection_digest="sha256:" + "0" * 64,
    )
    selected = staging.selected_runtime(subject)
    assert selected is not None
    selected_database = selected[4].database_path
    staging.close()

    content = "private nextcloud note"
    docs = tmp_path / "personal-docs"
    docs.mkdir()
    (docs / "note.txt").write_text(content, encoding="utf-8", newline="")
    reader = PersonalDocsOwnerReader(
        owner_id=subject,
        root_ref=ROOT_REF,
        root_path=str(docs),
    )
    record = _inventory("note.txt", content)
    config = _config(tmp_path, (subject,))
    monkeypatch.setattr(composition_module, "_app_secret", lambda: key)
    composition = NextcloudKnowledgeIngestionComposition.open_existing(
        config,
        auth,
        staging_root=staging_root,
        reader=reader,
    )
    value = _Fixture(
        tmp_path,
        auth,
        subject,
        config,
        key,
        issuer,
        staging_root,
        reader,
        record,
        content,
        composition,
        selected_database,
    )
    yield value
    try:
        composition.close()
    except NextcloudKnowledgeIngestionCompositionError:
        pass


def _error(callback, code: str) -> NextcloudKnowledgeIngestionCompositionError:
    with pytest.raises(NextcloudKnowledgeIngestionCompositionError) as raised:
        callback()
    assert raised.value.code == code
    assert raised.value.args == (code,)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    return raised.value


def _ingest(fixture: _Fixture):
    return fixture.composition.ingest(
        owner_subject_id=fixture.subject,
        security_revision=fixture.auth.security_action_revision(),
        inventory_record=fixture.record,
    )


def _database_counts(path: Path) -> tuple[int, int, int]:
    connection = sqlite3.connect(path)
    try:
        return tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("knowledge_heads", "knowledge_revisions", "knowledge_receipts")
        )
    finally:
        connection.close()


def _reservation_state(
    composition: NextcloudKnowledgeIngestionComposition,
) -> tuple[bool, int, str]:
    return (
        getattr(composition._active, "operation", False) is True,
        composition._inflight,
        composition._state,
    )


def _forbidden(calls: list[str], name: str):
    def callback(*_args, **_kwargs):
        calls.append(name)
        raise AssertionError(f"unexpected {name} access")

    return callback


def _observe_reader_owner(monkeypatch, calls: list[object], callback=None) -> None:
    owner_slot = PersonalDocsOwnerReader.__dict__["_owner_id"]

    def owner(reader: PersonalDocsOwnerReader) -> str:
        calls.append(callback() if callback is not None else "reader")
        return owner_slot.__get__(reader, PersonalDocsOwnerReader)

    monkeypatch.setattr(PersonalDocsOwnerReader, "_owner_id", property(owner))


def _exact_readback(runtime: _Fixture, result: NextcloudKnowledgeIngestionResult) -> bytes:
    peer = HarbourKnowledgeStagingDestinationService.open_existing(
        runtime.staging_root,
        trusted_issuers=(runtime.issuer,),
    )
    try:
        selected = peer.selected_runtime(runtime.subject)
        assert selected is not None
        store = selected[4]
        principal = runtime.issuer.issue(
            subject_id=runtime.subject,
            owner_id=runtime.subject,
            auth_context_digest=_sha({"purpose": "composition-readback"}),
        )
        head = store.get_head(
            principal,
            owner_id=runtime.subject,
            knowledge_id=result.knowledge_id,
        )
        assert head == result.head
        return store.read_payload(
            principal,
            owner_id=runtime.subject,
            revision_id=head.current_revision_id,
        )
    finally:
        peer.close()


def test_composition_is_sealed_and_rebound_open_rejects_before_capabilities(
    runtime: _Fixture, monkeypatch
) -> None:
    with pytest.raises(TypeError):
        type(
            "EscapedNextcloudKnowledgeIngestionComposition",
            (NextcloudKnowledgeIngestionComposition,),
            {},
        )

    calls: list[str] = []
    monkeypatch.setattr(
        composition_module,
        "knowledge_source_capability_manifest",
        _forbidden(calls, "config capability"),
    )
    monkeypatch.setattr(
        composition_module,
        "_app_secret",
        _forbidden(calls, "application key"),
    )
    monkeypatch.setattr(
        HarbourKnowledgeStagingDestinationService,
        "open_existing",
        _forbidden(calls, "staging open"),
    )
    rebound = NextcloudKnowledgeIngestionComposition.open_existing.__func__
    _error(
        lambda: rebound(
            object(),
            runtime.config,
            runtime.auth,
            staging_root=runtime.staging_root,
            reader=runtime.reader,
        ),
        "open_failed",
    )
    assert calls == []


def test_real_create_uses_selected_native_store_and_supports_exact_readback(runtime: _Fixture) -> None:
    result = _ingest(runtime)
    assert type(result) is NextcloudKnowledgeIngestionResult
    assert result.outcome == "created"
    assert _exact_readback(runtime, result) == runtime.content.encode("utf-8")


def test_same_sid_rename_requires_fresh_security_revision_and_recreated_alias_is_denied(
    runtime: _Fixture, monkeypatch,
) -> None:
    stale = runtime.auth.security_action_revision()
    assert runtime.auth.rename_user("alice", "renamed", "alice")
    fresh = runtime.auth.security_action_revision()
    assert fresh > stale
    stale_calls: list[str] = []
    with monkeypatch.context() as stage:
        stage.setattr(
            composition_module,
            "_app_secret",
            _forbidden(stale_calls, "application key"),
        )
        stage.setattr(
            HarbourKnowledgeStagingDestinationService,
            "guard_selected_runtime",
            _forbidden(stale_calls, "staging"),
        )
        stage.setattr(
            HarbourKnowledgeStagingDestinationService,
            "_store",
            _forbidden(stale_calls, "selected store"),
        )
        _observe_reader_owner(stage, stale_calls)
        stage.setattr(
            composition_module,
            "ingest_nextcloud_personal_document",
            _forbidden(stale_calls, "adapter"),
        )
        _error(
            lambda: runtime.composition.ingest(
                owner_subject_id=runtime.subject,
                security_revision=stale,
                inventory_record=runtime.record,
            ),
            "pre_adapter_failed",
        )
    assert stale_calls == []
    result = runtime.composition.ingest(
        owner_subject_id=runtime.subject,
        security_revision=fresh,
        inventory_record=runtime.record,
    )
    assert result.outcome == "created"
    assert runtime.auth.create_user("alice", PASSWORD, is_admin=True)
    recreated = runtime.auth.subject_id_for_username("alice")
    assert type(recreated) is str and recreated != runtime.subject
    recreated_calls: list[str] = []
    with monkeypatch.context() as stage:
        stage.setattr(
            AuthManager,
            "guard_current_admin_subject",
            _forbidden(recreated_calls, "authentication"),
        )
        stage.setattr(
            composition_module,
            "_app_secret",
            _forbidden(recreated_calls, "application key"),
        )
        stage.setattr(
            HarbourKnowledgeStagingDestinationService,
            "guard_selected_runtime",
            _forbidden(recreated_calls, "staging"),
        )
        stage.setattr(
            HarbourKnowledgeStagingDestinationService,
            "_store",
            _forbidden(recreated_calls, "selected store"),
        )
        _observe_reader_owner(stage, recreated_calls)
        stage.setattr(
            composition_module,
            "ingest_nextcloud_personal_document",
            _forbidden(recreated_calls, "adapter"),
        )
        _error(
            lambda: runtime.composition.ingest(
                owner_subject_id=recreated,
                security_revision=runtime.auth.security_action_revision(),
                inventory_record=runtime.record,
            ),
            "admission_denied",
        )
    assert recreated_calls == []


def test_demoted_sid_is_denied_before_key_staging_reader_store_or_adapter(
    runtime: _Fixture, monkeypatch
) -> None:
    calls: list[str] = []
    assert runtime.auth.create_user("bob", PASSWORD, is_admin=True)
    assert runtime.auth.set_admin("alice", False, "bob") is SetAdminResult.OK
    monkeypatch.setattr(
        composition_module,
        "_app_secret",
        _forbidden(calls, "application key"),
    )
    monkeypatch.setattr(
        HarbourKnowledgeStagingDestinationService,
        "guard_selected_runtime",
        _forbidden(calls, "staging"),
    )
    monkeypatch.setattr(
        HarbourKnowledgeStagingDestinationService,
        "_store",
        _forbidden(calls, "selected store"),
    )
    _observe_reader_owner(monkeypatch, calls)
    monkeypatch.setattr(
        composition_module,
        "ingest_nextcloud_personal_document",
        _forbidden(calls, "adapter"),
    )
    _error(lambda: _ingest(runtime), "pre_adapter_failed")
    assert calls == []


@pytest.mark.parametrize("case", ("owner", "generation", "native_capability"))
def test_config_authority_mismatches_are_distinct_admission_denials_without_capability_io(
    case: str,
    runtime: _Fixture, monkeypatch
) -> None:
    calls: list[str] = []
    if case == "owner":
        object.__setattr__(runtime.config, "allowed_owners", ("owner_" + "0" * 32,))
    elif case == "generation":
        object.__setattr__(
            runtime.config,
            "selected_generation",
            runtime.config.selected_generation + "x",
        )
    else:
        monkeypatch.setattr(
            composition_module,
            "knowledge_source_capability_manifest",
            lambda: object(),
        )
    monkeypatch.setattr(
        AuthManager,
        "guard_current_admin_subject",
        _forbidden(calls, "authentication"),
    )
    monkeypatch.setattr(
        composition_module,
        "_app_secret",
        _forbidden(calls, "application key"),
    )
    monkeypatch.setattr(
        HarbourKnowledgeStagingDestinationService,
        "guard_selected_runtime",
        _forbidden(calls, "staging"),
    )
    monkeypatch.setattr(
        HarbourKnowledgeStagingDestinationService,
        "_store",
        _forbidden(calls, "selected store"),
    )
    _observe_reader_owner(monkeypatch, calls)
    monkeypatch.setattr(
        composition_module,
        "ingest_nextcloud_personal_document",
        _forbidden(calls, "adapter"),
    )
    _error(lambda: _ingest(runtime), "admission_denied")
    assert calls == []


def test_key_pending_record_and_selected_owner_denials_stop_at_their_actual_stage(
    runtime: _Fixture, monkeypatch
) -> None:
    key_calls: list[str] = []
    with monkeypatch.context() as stage:
        stage.setattr(composition_module, "_app_secret", lambda: b"z" * 32)
        stage.setattr(
            HarbourKnowledgeStagingDestinationService,
            "guard_selected_runtime",
            _forbidden(key_calls, "staging"),
        )
        stage.setattr(
            HarbourKnowledgeStagingDestinationService,
            "_store",
            _forbidden(key_calls, "selected store"),
        )
        _observe_reader_owner(stage, key_calls)
        stage.setattr(
            composition_module,
            "ingest_nextcloud_personal_document",
            _forbidden(key_calls, "adapter"),
        )
        _error(lambda: _ingest(runtime), "pre_adapter_failed")
    assert key_calls == []
    assert _reservation_state(runtime.composition) == (False, 0, "open")

    pending = BigDataLedgerRecord(
        item=runtime.record.item,
        stage="inventory",
        status="pending",
        updated_at=OBSERVED,
        metadata=dict(runtime.record.metadata),
    )
    pending_calls: list[str] = []
    with monkeypatch.context() as stage:
        stage.setattr(
            AuthManager,
            "guard_current_admin_subject",
            _forbidden(pending_calls, "authentication"),
        )
        stage.setattr(
            composition_module,
            "_app_secret",
            _forbidden(pending_calls, "application key"),
        )
        stage.setattr(
            HarbourKnowledgeStagingDestinationService,
            "guard_selected_runtime",
            _forbidden(pending_calls, "staging"),
        )
        stage.setattr(
            HarbourKnowledgeStagingDestinationService,
            "_store",
            _forbidden(pending_calls, "selected store"),
        )
        _observe_reader_owner(stage, pending_calls)
        stage.setattr(
            composition_module,
            "ingest_nextcloud_personal_document",
            _forbidden(pending_calls, "adapter"),
        )
        _error(
            lambda: runtime.composition.ingest(
                owner_subject_id=runtime.subject,
                security_revision=runtime.auth.security_action_revision(),
                inventory_record=pending,
            ),
            "admission_denied",
        )
    assert pending_calls == []
    assert _reservation_state(runtime.composition) == (False, 0, "open")

    assert runtime.auth.create_user("bob", PASSWORD, is_admin=True)
    bob = runtime.auth.subject_id_for_username("bob")
    assert type(bob) is str
    peer = HarbourKnowledgeStagingDestinationService.open_existing(
        runtime.staging_root,
        trusted_issuers=(runtime.issuer,),
    )
    completion = _zero_completion(runtime.tmp_path, peer, runtime.issuer, "bob-only", (bob,))
    current = peer.current_selection()
    assert current is not None
    peer.select_generation(completion, expected_selection_digest=current[2])
    peer.close()
    selected_calls: list[str] = []
    original_guard = HarbourKnowledgeStagingDestinationService.guard_selected_runtime

    @contextmanager
    def observed_guard(service, owner_id):
        selected_calls.append("staging")
        with original_guard(service, owner_id) as selected:
            yield selected

    with monkeypatch.context() as stage:
        stage.setattr(
            HarbourKnowledgeStagingDestinationService,
            "guard_selected_runtime",
            observed_guard,
        )
        stage.setattr(
            HarbourKnowledgeStagingDestinationService,
            "_store",
            _forbidden(selected_calls, "selected store"),
        )
        _observe_reader_owner(stage, selected_calls)
        stage.setattr(
            composition_module,
            "ingest_nextcloud_personal_document",
            _forbidden(selected_calls, "adapter"),
        )
        _error(lambda: _ingest(runtime), "pre_adapter_failed")
    assert selected_calls == ["staging"]
    assert _reservation_state(runtime.composition) == (False, 0, "open")


def test_hostile_object_setattr_config_and_record_drift_are_admission_denials(
    runtime: _Fixture, monkeypatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        composition_module,
        "ingest_nextcloud_personal_document",
        lambda **_kwargs: calls.append("adapter"),
    )
    object.__setattr__(runtime.config, "runtime_enabled", False)
    _error(lambda: _ingest(runtime), "admission_denied")
    object.__setattr__(runtime.config, "runtime_enabled", True)
    object.__setattr__(runtime.record, "status", "running")
    _error(lambda: _ingest(runtime), "admission_denied")
    assert calls == []


def test_non_open_lifecycle_states_deny_before_reader_auth_key_staging_store_or_adapter(
    runtime: _Fixture, monkeypatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        AuthManager,
        "guard_current_admin_subject",
        _forbidden(calls, "authentication"),
    )
    monkeypatch.setattr(
        composition_module,
        "_app_secret",
        _forbidden(calls, "application key"),
    )
    monkeypatch.setattr(
        HarbourKnowledgeStagingDestinationService,
        "guard_selected_runtime",
        _forbidden(calls, "staging"),
    )
    monkeypatch.setattr(
        HarbourKnowledgeStagingDestinationService,
        "_store",
        _forbidden(calls, "selected store"),
    )
    _observe_reader_owner(monkeypatch, calls)
    monkeypatch.setattr(
        composition_module,
        "ingest_nextcloud_personal_document",
        _forbidden(calls, "adapter"),
    )
    try:
        for state in ("closing", "closed", "close_failed"):
            with runtime.composition._condition:
                runtime.composition._state = state
            _error(lambda: _ingest(runtime), "admission_denied")
            assert _reservation_state(runtime.composition) == (False, 0, state)
    finally:
        with runtime.composition._condition:
            runtime.composition._state = "open"
    assert calls == []


def test_invalid_admission_and_postadmission_failures_release_reservation_for_valid_followup(
    runtime: _Fixture, monkeypatch
) -> None:
    _error(
        lambda: runtime.composition.ingest(
            owner_subject_id="",
            security_revision=runtime.auth.security_action_revision(),
            inventory_record=runtime.record,
        ),
        "admission_denied",
    )
    assert _reservation_state(runtime.composition) == (False, 0, "open")

    real_auth = runtime.composition._auth
    missing_auth = AuthManager(str(runtime.tmp_path / "missing-auth.json"))
    missing_calls: list[str] = []
    runtime.composition._auth = missing_auth
    try:
        with monkeypatch.context() as stage:
            stage.setattr(
                composition_module,
                "_app_secret",
                _forbidden(missing_calls, "application key"),
            )
            stage.setattr(
                HarbourKnowledgeStagingDestinationService,
                "guard_selected_runtime",
                _forbidden(missing_calls, "staging"),
            )
            stage.setattr(
                HarbourKnowledgeStagingDestinationService,
                "_store",
                _forbidden(missing_calls, "selected store"),
            )
            _observe_reader_owner(stage, missing_calls)
            stage.setattr(
                composition_module,
                "ingest_nextcloud_personal_document",
                _forbidden(missing_calls, "adapter"),
            )
            _error(lambda: _ingest(runtime), "pre_adapter_failed")
    finally:
        runtime.composition._auth = real_auth
    assert missing_calls == []
    assert _reservation_state(runtime.composition) == (False, 0, "open")

    key_calls: list[str] = []
    with monkeypatch.context() as stage:
        stage.setattr(composition_module, "_app_secret", lambda: b"z" * 32)
        stage.setattr(
            HarbourKnowledgeStagingDestinationService,
            "guard_selected_runtime",
            _forbidden(key_calls, "staging"),
        )
        stage.setattr(
            HarbourKnowledgeStagingDestinationService,
            "_store",
            _forbidden(key_calls, "selected store"),
        )
        _observe_reader_owner(stage, key_calls)
        stage.setattr(
            composition_module,
            "ingest_nextcloud_personal_document",
            _forbidden(key_calls, "adapter"),
        )
        _error(lambda: _ingest(runtime), "pre_adapter_failed")
    assert key_calls == []
    assert _reservation_state(runtime.composition) == (False, 0, "open")

    reader_slot = PersonalDocsOwnerReader.__dict__["_owner_id"]
    reader_states: list[tuple[bool, int, str]] = []

    def wrong_owner(_reader: PersonalDocsOwnerReader) -> str:
        reader_states.append(_reservation_state(runtime.composition))
        return "owner_" + "0" * 32

    with monkeypatch.context() as stage:
        stage.setattr(PersonalDocsOwnerReader, "_owner_id", property(wrong_owner))
        stage.setattr(
            composition_module,
            "ingest_nextcloud_personal_document",
            _forbidden([], "adapter"),
        )
        _error(lambda: _ingest(runtime), "pre_adapter_failed")
    assert reader_states == [(True, 1, "open")]
    assert _reservation_state(runtime.composition) == (False, 0, "open")
    assert reader_slot.__get__(runtime.reader, PersonalDocsOwnerReader) == runtime.subject

    result = _ingest(runtime)
    assert type(result) is NextcloudKnowledgeIngestionResult
    runtime.composition.close()
    assert _reservation_state(runtime.composition) == (False, 0, "closed")


def test_inventory_is_detached_before_auth_capability_io(
    runtime: _Fixture, monkeypatch
) -> None:
    seen: list[BigDataLedgerRecord] = []
    sentinel = object()

    def adapter(**kwargs):
        seen.append(kwargs["inventory_record"])
        return sentinel

    monkeypatch.setattr(composition_module, "ingest_nextcloud_personal_document", adapter)
    result = _ingest(runtime)
    assert result is sentinel
    assert seen and seen[0] is not runtime.record
    assert seen[0].to_dict() == runtime.record.to_dict()


def test_same_thread_ingest_and_close_reentry_are_denied_before_second_mutation(
    runtime: _Fixture, monkeypatch
) -> None:
    native = composition_module.ingest_nextcloud_personal_document
    nested: list[str] = []
    reader_states: list[tuple[bool, int, str]] = []
    _observe_reader_owner(
        monkeypatch,
        reader_states,
        lambda: _reservation_state(runtime.composition),
    )

    def adapter(**kwargs):
        assert _reservation_state(runtime.composition) == (True, 1, "open")
        nested.append(
            _error(lambda: _ingest(runtime), "admission_denied").code
        )
        assert _reservation_state(runtime.composition) == (True, 1, "open")
        nested.append(_error(runtime.composition.close, "close_failed").code)
        assert _reservation_state(runtime.composition) == (True, 1, "open")
        return native(**kwargs)

    monkeypatch.setattr(composition_module, "ingest_nextcloud_personal_document", adapter)
    result = _ingest(runtime)
    assert result.outcome == "created"
    assert nested == ["admission_denied", "close_failed"]
    assert reader_states
    assert all(state == (True, 1, "open") for state in reader_states)
    assert _reservation_state(runtime.composition) == (False, 0, "open")
    assert _database_counts(runtime.selected_database)[0] == 1


def test_adapter_exact_result_and_exception_are_preserved(
    runtime: _Fixture, monkeypatch
) -> None:
    sentinel = object()
    monkeypatch.setattr(
        composition_module,
        "ingest_nextcloud_personal_document",
        lambda **_kwargs: sentinel,
    )
    assert _ingest(runtime) is sentinel

    error = NextcloudKnowledgeIngestionError("store_failed")

    def fail(**_kwargs):
        raise error

    monkeypatch.setattr(composition_module, "ingest_nextcloud_personal_document", fail)
    with pytest.raises(NextcloudKnowledgeIngestionError) as raised:
        _ingest(runtime)
    assert raised.value is error


def test_stage_a_postcommit_cleanup_fault_preserves_real_result_and_exact_adapter_exception(
    runtime: _Fixture, monkeypatch
) -> None:
    original_guard = HarbourKnowledgeStagingDestinationService.guard_selected_runtime

    @contextmanager
    def cleanup_fault(service, owner_id):
        with original_guard(service, owner_id) as selected:
            yield selected
        raise OSError("private staging cleanup detail")

    with monkeypatch.context() as stage:
        stage.setattr(
            HarbourKnowledgeStagingDestinationService,
            "guard_selected_runtime",
            cleanup_fault,
        )
        result = _ingest(runtime)
    assert type(result) is NextcloudKnowledgeIngestionResult
    assert result.outcome == "created"
    assert _exact_readback(runtime, result) == runtime.content.encode("utf-8")
    assert _reservation_state(runtime.composition) == (False, 0, "open")

    adapter_error = NextcloudKnowledgeIngestionError("store_failed")

    def fail(**_kwargs):
        raise adapter_error

    with monkeypatch.context() as stage:
        stage.setattr(
            HarbourKnowledgeStagingDestinationService,
            "guard_selected_runtime",
            cleanup_fault,
        )
        stage.setattr(composition_module, "ingest_nextcloud_personal_document", fail)
        with pytest.raises(NextcloudKnowledgeIngestionError) as raised:
            _ingest(runtime)
    assert raised.value is adapter_error
    assert _reservation_state(runtime.composition) == (False, 0, "open")


def test_peer_selection_and_close_wait_for_real_adapter_commit_and_only_original_generation_writes(
    runtime: _Fixture, monkeypatch
) -> None:
    peer = HarbourKnowledgeStagingDestinationService.open_existing(
        runtime.staging_root,
        trusted_issuers=(runtime.issuer,),
    )
    completion = _zero_completion(
        runtime.tmp_path,
        peer,
        runtime.issuer,
        "peer-generation",
        (runtime.subject,),
    )
    current = peer.current_selection()
    assert current is not None
    expected_selection = current[2]
    old_database = runtime.selected_database
    native = composition_module.ingest_nextcloud_personal_document
    entered = threading.Event()
    release = threading.Event()
    results: list[object] = []
    failures: list[BaseException] = []

    def delayed_adapter(**kwargs):
        entered.set()
        assert release.wait(5)
        return native(**kwargs)

    monkeypatch.setattr(
        composition_module,
        "ingest_nextcloud_personal_document",
        delayed_adapter,
    )

    def ingest_worker():
        try:
            results.append(_ingest(runtime))
        except BaseException as error:
            failures.append(error)

    def close_worker():
        try:
            runtime.composition.close()
            close_done.set()
        except BaseException as error:
            failures.append(error)

    def select_worker():
        try:
            peer.select_generation(
                completion,
                expected_selection_digest=expected_selection,
            )
            select_done.set()
        except BaseException as error:
            failures.append(error)

    close_done = threading.Event()
    select_done = threading.Event()
    ingest_thread = threading.Thread(target=ingest_worker)
    close_thread = threading.Thread(target=close_worker)
    select_thread = threading.Thread(target=select_worker)
    ingest_thread.start()
    try:
        assert entered.wait(5)
        close_thread.start()
        select_thread.start()
        assert not close_done.wait(0.05)
        assert not select_done.wait(0.05)
    finally:
        release.set()
        for worker in (ingest_thread, close_thread, select_thread):
            if worker.ident is not None:
                worker.join(5)
    assert not ingest_thread.is_alive()
    assert not close_thread.is_alive()
    assert not select_thread.is_alive()
    assert failures == []
    assert len(results) == 1 and results[0].outcome == "created"
    assert close_done.is_set() and select_done.is_set()
    selected = peer.selected_runtime(runtime.subject)
    assert selected is not None
    new_database = selected[4].database_path
    peer.close()
    assert new_database != old_database
    assert _database_counts(old_database)[0] == 1
    assert _database_counts(new_database) == (0, 0, 0)


def test_close_coalesces_and_retains_one_sanitized_failure(
    runtime: _Fixture, monkeypatch
) -> None:
    original_close = HarbourKnowledgeStagingDestinationService.close
    entered = threading.Event()
    release = threading.Event()
    calls: list[int] = []
    errors: list[NextcloudKnowledgeIngestionCompositionError] = []

    def failing_close(_self):
        calls.append(1)
        entered.set()
        assert release.wait(5)
        raise OSError("private staging path")

    monkeypatch.setattr(HarbourKnowledgeStagingDestinationService, "close", failing_close)

    def close_worker():
        try:
            runtime.composition.close()
        except NextcloudKnowledgeIngestionCompositionError as error:
            errors.append(error)

    first = threading.Thread(target=close_worker)
    second = threading.Thread(target=close_worker)
    first.start()
    try:
        assert entered.wait(5)
        second.start()
        time.sleep(0.05)
        assert calls == [1] and second.is_alive()
    finally:
        release.set()
        for worker in (first, second):
            if worker.ident is not None:
                worker.join(5)
    assert not first.is_alive() and not second.is_alive()
    assert len(errors) == 2
    assert all(error.code == "close_failed" for error in errors)
    assert all(error.__cause__ is None and error.__context__ is None for error in errors)
    _error(runtime.composition.close, "close_failed")
    assert calls == [1]
    original_close(runtime.composition._staging)


def test_repeated_denials_are_fresh_fixed_errors_even_inside_an_active_exception(
    runtime: _Fixture,
) -> None:
    errors: list[NextcloudKnowledgeIngestionCompositionError] = []
    for _ in range(2):
        try:
            raise RuntimeError("private outer detail")
        except RuntimeError:
            errors.append(
                _error(
                    lambda: runtime.composition.ingest(
                        owner_subject_id="",
                        security_revision=runtime.auth.security_action_revision(),
                        inventory_record=runtime.record,
                    ),
                    "admission_denied",
                )
            )
    assert errors[0] is not errors[1]
    assert all(error.args == ("admission_denied",) for error in errors)
    assert all(error.__cause__ is None and error.__context__ is None for error in errors)


def test_open_and_path_failures_are_sanitized_and_create_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    auth = AuthManager(str(tmp_path / "auth.json"))
    assert auth.create_user("alice", PASSWORD, is_admin=True)
    subject = auth.subject_id_for_username("alice")
    assert type(subject) is str
    docs = tmp_path / "docs"
    docs.mkdir()
    reader = PersonalDocsOwnerReader(
        owner_id=subject,
        root_ref=ROOT_REF,
        root_path=str(docs),
    )
    config = _config(tmp_path, (subject,))
    key_reads: list[int] = []

    def key():
        key_reads.append(1)
        return b"k" * 32

    monkeypatch.setattr(composition_module, "_app_secret", key)
    for root in ("relative", "~/private", object()):
        _error(
            lambda root=root: NextcloudKnowledgeIngestionComposition.open_existing(
                config,
                auth,
                staging_root=root,
                reader=reader,
            ),
            "open_failed",
        )
    assert key_reads == []

    missing = tmp_path / "missing-staging"
    assert not missing.exists()
    _error(
        lambda: NextcloudKnowledgeIngestionComposition.open_existing(
            config,
            auth,
            staging_root=missing,
            reader=reader,
        ),
        "open_failed",
    )
    assert key_reads == [1]
    assert not missing.exists()

    monkeypatch.setattr(
        composition_module,
        "_app_secret",
        lambda: (_ for _ in ()).throw(OSError("private key path")),
    )
    _error(
        lambda: NextcloudKnowledgeIngestionComposition.open_existing(
            config,
            auth,
            staging_root=tmp_path,
            reader=reader,
        ),
        "open_failed",
    )


def test_import_is_inactive_and_has_no_root_discovery_or_registration_surface() -> None:
    path = Path(composition_module.__file__)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(
        name == "app"
        or name.startswith("routes")
        or name.startswith("plugins")
        or name.startswith("providers")
        for name in imports
    )
    assert "DATA_DIR" not in source
    assert "os.environ" not in source
    assert ".mkdir(" not in source
    assert "knowledge_ingestion_composition" not in Path("app.py").read_text(
        encoding="utf-8"
    )
