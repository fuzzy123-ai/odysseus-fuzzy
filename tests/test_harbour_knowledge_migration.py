from __future__ import annotations

from datetime import datetime, timezone
import copy
import json
import sqlite3
from pathlib import Path

import pytest

from src.causal_claim_store import CausalClaimStore
from src.harbour_authority import AuthenticatedPrincipalIssuer
from src.harbour_knowledge_migration import (
    CausalMigrationSource,
    KnowledgeMigrationError,
    MemoryMigrationSource,
    PersonalDocsMigrationSource,
    migrate_prepared_knowledge_migration,
    prepare_authenticated_zero_census_knowledge_migration,
    prepare_current_knowledge_migration,
)
from src.harbour_knowledge_staging_destination import (
    HarbourKnowledgeStagingDestinationService,
    StagingDestinationError,
    StagingMigrationCompletionReference,
    _CONTEXT_SCHEMA,
    _sha,
)
from src.memory_owner_eligibility import (
    MEMORY_ELIGIBILITY_SCHEMA,
    capture_memory_owner_eligibility_snapshot,
)
from src.personal_docs_owner_reader import PersonalDocsOwnerReader
from src.unified_source_index_owner_scope import OwnerScope
from src.unified_source_index_sources.memory import (
    MemorySourceAdapter,
    create_memory_source_authority_binding,
)


NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
OBSERVED = "2026-10-01T12:00:00Z"
RETENTION = {
    "retention_class": "user_managed",
    "deletion_mode": "tombstone_only",
    "retain_until": None,
    "grace_until": None,
    "legal_hold": False,
}


def _ref(character: str) -> str:
    return "sha256:" + character * 64


def _claim() -> dict[str, object]:
    return {
        "schema": "odysseus.causal_claim.v1",
        "claim_id": "claim_a",
        "owner": "alice",
        "policy": "policy_a",
        "source_version": _ref("1"),
        "claim_version": 1,
        "candidate_actor": "actor_a",
        "evidence_refs": [_ref("2")],
    }


def _source(tmp_path: Path, *, reviewed: bool = True) -> CausalClaimStore:
    store = CausalClaimStore(sqlite3.connect(tmp_path / "legacy-causal.sqlite"), initialize=True)
    report = store.create_claim(_claim())
    if reviewed:
        store.append_review_transition({
            "schema": "odysseus.causal_review_transition.v1",
            "event_id": "event_a",
            "claim_ref": report["claim_ref"],
            "expected_head_digest": report["head_digest"],
            "from_state": "candidate",
            "to_state": "under_review",
            "reviewer": "reviewer_a",
            "evidence_refs": [_ref("3")],
            "relation_kind": "none",
            "related_claim_ref": None,
            "prior_claim_version": None,
            "reason_code": None,
        })
    return store


def _plan(source: CausalClaimStore):
    return prepare_current_knowledge_migration(
        causal_sources=(CausalMigrationSource("alice", source, "alice"),),
        owner_map={"alice": "alice"},
        retention_by_scope={"alice": dict(RETENTION)},
        observed_at=OBSERVED,
    )


def _service(tmp_path: Path, issuer: AuthenticatedPrincipalIssuer):
    return HarbourKnowledgeStagingDestinationService(
        tmp_path / "staging", trusted_issuers=(issuer,), clock=lambda: NOW,
    )


def _principal(plan, issuer):
    context = _sha({
        "migration_id": plan.migration_id,
        "owner_id": "alice",
        "schema_id": _CONTEXT_SCHEMA,
        "snapshot_digest": plan.source_manifest_digest,
    })
    return issuer.issue(subject_id="alice", owner_id="alice", auth_context_digest=context)


def test_authenticated_zero_census_requires_complete_real_source_and_can_be_selected(tmp_path):
    source = CausalClaimStore(sqlite3.connect(tmp_path / "empty-causal.sqlite"), initialize=True)
    plan = prepare_authenticated_zero_census_knowledge_migration(
        causal_sources=(CausalMigrationSource("alice", source, "alice"),),
        owner_map={"alice": "alice"}, retention_by_scope={"alice": dict(RETENTION)},
        observed_at=OBSERVED,
    )
    assert plan.zero_census and plan.source_count == 1 and plan.commands == ()
    issuer = _issuer()
    service = _service(tmp_path, issuer)
    principal = _principal(plan, issuer)
    completion = migrate_prepared_knowledge_migration(
        plan, staging=service, principals={"alice": principal},
    ).completion_reference
    selected = service.select_generation(
        completion, expected_selection_digest="sha256:" + "0" * 64,
    )
    assert selected != "sha256:" + "0" * 64
    assert service.current_selection() is not None
    service.close()


def _issuer():
    return AuthenticatedPrincipalIssuer(
        issuer_id="migration_issuer", key_id="migration_key", secret=b"m" * 32,
        clock=lambda: NOW,
    )


def _fixture_database_corruption(connection: sqlite3.Connection, table: str, statement: str, parameters):
    """Simulate external at-rest corruption while restoring production triggers."""
    triggers = connection.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name=? ORDER BY name", (table,),
    ).fetchall()
    try:
        for name, _ in triggers:
            connection.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
        connection.execute(statement, parameters)
        connection.commit()
    finally:
        for _, sql in triggers:
            if sql:
                connection.execute(sql)
        connection.commit()


def _memory_source(tmp_path: Path, *, ineligible=False):
    evidence = {
        "schema": MEMORY_ELIGIBILITY_SCHEMA,
        "source_status": "active",
        "acceptance_status": "accepted",
        "incognito": False,
        "policy_status": "go",
        "policy_evidence_ref": _ref("4"),
        "review_status": "accepted",
        "review_evidence_ref": _ref("5"),
    }
    if ineligible:
        evidence["review_status"] = "pending"
    record = {
        "id": "memory-one", "owner": "legacy-owner", "text": "memory golden",
        "timestamp": 1_700_000_000, "source": "synthetic", "category": "fixture",
        "uses": 0, "metadata": {"memory_eligibility": evidence},
    }
    path = tmp_path / "memory.json"
    path.write_text(json.dumps([record]), encoding="utf-8")
    eligibility = capture_memory_owner_eligibility_snapshot(path, owner="legacy-owner")
    scope = OwnerScope.for_subject_id("owner_0123456789abcdef0123456789abcdef")
    binding = create_memory_source_authority_binding(owner_scope=scope, snapshot=eligibility)
    adapter = MemorySourceAdapter(binding=binding, snapshot=eligibility)
    source = MemoryMigrationSource(
        scope.value, adapter, eligibility, binding.binding_digest, eligibility.snapshot_digest,
    )
    return source, scope.value


def _docs_source(tmp_path: Path, *, unsupported=False):
    root = tmp_path / "personal-docs"
    root.mkdir()
    (root / "notes.md").write_text("document golden", encoding="utf-8")
    if unsupported:
        (root / "notes.bin").write_bytes(b"unsupported")
    reader = PersonalDocsOwnerReader(owner_id="docs-owner", root_ref=_ref("6"), root_path=str(root))
    snapshot = reader.capture()
    source = PersonalDocsMigrationSource(
        snapshot.owner_ref, reader, snapshot,
    )
    return source


def test_prepare_requires_exact_owner_and_retention_decisions_before_staging(tmp_path):
    source = _source(tmp_path)
    with pytest.raises(KnowledgeMigrationError) as raised:
        prepare_current_knowledge_migration(
            causal_sources=(CausalMigrationSource("alice", source, "alice"),),
            owner_map={}, retention_by_scope={"alice": dict(RETENTION)}, observed_at=OBSERVED,
        )
    assert raised.value.code == "invalid_plan"
    assert "alice" not in repr(raised.value)
    with pytest.raises(KnowledgeMigrationError) as raised:
        prepare_current_knowledge_migration(
            causal_sources=(CausalMigrationSource("alice", source, "alice"),),
            owner_map={"alice": "alice"}, retention_by_scope={}, observed_at=OBSERVED,
        )
    assert raised.value.code == "invalid_plan"


def test_memory_and_personal_docs_sources_preserve_golden_payload_and_owner_bindings(tmp_path):
    memory, memory_scope = _memory_source(tmp_path)
    docs = _docs_source(tmp_path)
    memory_path = tmp_path / "memory.json"
    docs_path = tmp_path / "personal-docs" / "notes.md"
    memory_before = memory_path.read_bytes()
    docs_before = docs_path.read_bytes()
    scopes = (memory_scope, docs.scope)
    plan = prepare_current_knowledge_migration(
        memory_sources=(memory,), personal_docs_sources=(docs,),
        owner_map={memory_scope: "alice", docs.scope: "bob"},
        retention_by_scope={scope: dict(RETENTION) for scope in scopes}, observed_at=OBSERVED,
    )

    assert plan.owners == ("alice", "bob")
    assert plan.source_count == 2
    assert len(plan.commands) == 2
    memory_command, docs_command = plan.commands
    assert memory_command.owner_id == "alice"
    assert memory_command.payload == b"memory golden"
    assert memory_command.payload_schema_id == "odysseus.legacy_memory.text.v1"
    assert memory_command.source_revisions[0]["source_kind"] == "memory"
    assert memory_command.source_revisions[0]["source_content_sha256"].startswith("sha256:")
    assert docs_command.owner_id == "bob"
    assert docs_command.payload == b"document golden"
    assert docs_command.payload_schema_id == "odysseus.personal_document.utf8.v1"
    assert docs_command.source_revisions[0]["source_kind"] == "personal_document"
    for command in plan.commands:
        assert command.provenance[0]["role"] == "origin"
        assert command.retention == RETENTION
    assert memory_path.read_bytes() == memory_before
    assert docs_path.read_bytes() == docs_before
    assert "memory golden" not in repr(plan)
    assert "document golden" not in repr(plan)


@pytest.mark.parametrize("missing", ["owner", "retention"])
def test_memory_and_docs_missing_decision_fails_preflight(tmp_path, missing):
    memory, memory_scope = _memory_source(tmp_path)
    docs = _docs_source(tmp_path)
    owner_map = {memory_scope: "alice", docs.scope: "bob"}
    retention_map = {memory_scope: dict(RETENTION), docs.scope: dict(RETENTION)}
    if missing == "owner":
        owner_map.pop(docs.scope)
    else:
        retention_map.pop(memory_scope)
    with pytest.raises(KnowledgeMigrationError) as raised:
        prepare_current_knowledge_migration(
            memory_sources=(memory,), personal_docs_sources=(docs,), owner_map=owner_map,
            retention_by_scope=retention_map, observed_at=OBSERVED,
        )
    assert raised.value.code == "invalid_plan"
    assert "document golden" not in repr(raised.value)


def test_memory_ineligibility_census_blocks_migration_without_silent_drop(tmp_path):
    source, scope = _memory_source(tmp_path, ineligible=True)

    with pytest.raises(KnowledgeMigrationError) as raised:
        prepare_current_knowledge_migration(
            memory_sources=(source,), owner_map={scope: "alice"},
            retention_by_scope={scope: dict(RETENTION)}, observed_at=OBSERVED,
        )
    assert raised.value.code == "source_incomplete"
    assert raised.value.exclusion_counts == (("memory_ineligible", 1),)


def test_personal_docs_unsupported_census_blocks_before_any_reservation(tmp_path):
    docs = _docs_source(tmp_path, unsupported=True)
    issuer = _issuer()
    service = _service(tmp_path, issuer)
    with pytest.raises(KnowledgeMigrationError) as raised:
        prepare_current_knowledge_migration(
            personal_docs_sources=(docs,), owner_map={docs.scope: "bob"},
            retention_by_scope={docs.scope: dict(RETENTION)}, observed_at=OBSERVED,
        )
    assert raised.value.code == "source_incomplete"
    assert raised.value.exclusion_counts == (("personal_docs_unsupported", 1),)
    assert service._connection.execute("SELECT COUNT(*) FROM staging_reservations").fetchone()[0] == 0


def test_personal_docs_snapshot_from_another_reader_owner_is_rejected(tmp_path):
    docs = _docs_source(tmp_path)
    other_root = tmp_path / "other-docs"
    other_root.mkdir()
    (other_root / "notes.md").write_text("other document", encoding="utf-8")
    other_reader = PersonalDocsOwnerReader(owner_id="other-owner", root_ref=_ref("7"), root_path=str(other_root))
    other_snapshot = other_reader.capture()
    borrowed = PersonalDocsMigrationSource(docs.scope, other_reader, other_snapshot)

    with pytest.raises(KnowledgeMigrationError) as raised:
        prepare_current_knowledge_migration(
            personal_docs_sources=(borrowed,), owner_map={docs.scope: "bob"},
            retention_by_scope={docs.scope: dict(RETENTION)}, observed_at=OBSERVED,
        )
    assert raised.value.code == "source_incomplete"
    assert raised.value.exclusion_counts == ()


def test_causal_bases_are_ordered_before_any_review_history(tmp_path):
    source = _source(tmp_path)
    source.create_claim({**_claim(), "claim_id": "claim_z"})
    plan = _plan(source)

    kinds = [command.command_kind for command in plan.commands]
    first_history = kinds.index("append_revision")
    assert all(kind == "create_object" for kind in kinds[:first_history])
    assert all(kind == "append_revision" for kind in kinds[first_history:])
    assert kinds.count("create_object") == 2
    assert kinds.count("append_revision") == 1


def test_plan_tampering_is_rejected_before_reservation(tmp_path):
    source = _source(tmp_path)
    plan = _plan(source)
    issuer = _issuer()
    service = _service(tmp_path, issuer)
    with pytest.raises(KnowledgeMigrationError) as raised:
        type(plan)(plan.migration_id, plan.plan_digest, plan.source_manifest_digest,
            plan.census_digest, plan.owners, plan.commands, plan.source_count, plan.exclusion_counts)
    assert raised.value.code == "invalid_plan"

    forged = object.__new__(type(plan))
    object.__setattr__(forged, "_summary_bytes", plan._summary_bytes)
    with pytest.raises(KnowledgeMigrationError) as raised:
        migrate_prepared_knowledge_migration(
            forged, staging=service, principals={"alice": _principal(plan, issuer)},
        )
    assert raised.value.code == "invalid_plan"
    assert service._connection.execute("SELECT COUNT(*) FROM staging_reservations").fetchone()[0] == 0


def test_public_command_readouts_are_detached_from_execution_snapshot(tmp_path):
    source = _source(tmp_path, reviewed=False)
    plan = _plan(source)
    expected = copy.deepcopy(plan.commands)
    exposed = plan.commands[0]
    exposed.source_revisions[0]["source_revision_id"] = "forged-source"
    exposed.provenance[0]["source_revision_id"] = "forged-source"
    exposed.retention["legal_hold"] = True
    object.__setattr__(exposed, "payload", b"forged payload")

    issuer = _issuer()
    service = _service(tmp_path, issuer)
    result = migrate_prepared_knowledge_migration(
        plan, staging=service, principals={"alice": _principal(plan, issuer)},
    )
    generation = service._connection.execute(
        "SELECT generation_id FROM staging_reservations WHERE migration_id=?", (plan.migration_id,),
    ).fetchone()[0]
    store = service._stores[generation]
    row = store._connection.execute(
        "SELECT source_revisions_json,provenance_json,retention_json FROM knowledge_revisions",
    ).fetchone()
    assert json.loads(row[0]) == list(expected[0].source_revisions)
    assert json.loads(row[1]) == list(expected[0].provenance)
    assert json.loads(row[2]) == expected[0].retention
    assert result.write_count == len(expected)


@pytest.mark.parametrize("reviewed", [False, True])
def test_causal_migration_persists_exact_original_lineage_and_source_membership(tmp_path, reviewed):
    source = _source(tmp_path, reviewed=reviewed)
    snapshot = source.capture_owner_snapshot("alice")
    report, events = snapshot.claims[0]
    plan = _plan(source)
    commands = plan.commands
    expected_claim_digest = _sha({"report": report, "events": events})
    expected_initial_digest = events[0]["prior_digest"] if events else report["head_digest"]
    assert commands[0].source_revisions[0]["revision_ref"] == expected_claim_digest
    assert len(commands) == (2 if reviewed else 1)
    for index, command in enumerate(commands):
        body = json.loads(command.payload)
        assert body["legacy_claim_digest"] == expected_initial_digest
        if index == 0:
            assert body["legacy_head_digest"] == expected_initial_digest
        source_revision_id = command.source_revisions[0]["source_revision_id"]
        assert command.provenance[0]["source_revision_id"] == source_revision_id

    issuer = _issuer()
    service = _service(tmp_path, issuer)
    migrate_prepared_knowledge_migration(
        plan, staging=service, principals={"alice": _principal(plan, issuer)},
    )
    generation = service._connection.execute(
        "SELECT generation_id FROM staging_reservations WHERE migration_id=?", (plan.migration_id,),
    ).fetchone()[0]
    rows = service._stores[generation]._connection.execute(
        "SELECT source_revisions_json,provenance_json FROM knowledge_revisions ORDER BY revision",
    ).fetchall()
    assert len(rows) == len(commands)
    for row in rows:
        source_rows, provenance = json.loads(row[0]), json.loads(row[1])
        assert provenance[0]["source_revision_id"] == source_rows[0]["source_revision_id"]


def test_repreparing_same_real_source_reconnects_authenticated_completion(tmp_path):
    source = _source(tmp_path, reviewed=False)
    first_plan = _plan(source)
    issuer = _issuer()
    first_service = _service(tmp_path, issuer)
    first = migrate_prepared_knowledge_migration(
        first_plan, staging=first_service, principals={"alice": _principal(first_plan, issuer)},
    )
    second_plan = _plan(source)
    assert second_plan.migration_id == first_plan.migration_id
    assert second_plan.source_manifest_digest == first_plan.source_manifest_digest

    restarted = _service(tmp_path, issuer)
    resumed = migrate_prepared_knowledge_migration(
        second_plan, staging=restarted, principals={"alice": _principal(second_plan, issuer)},
    )
    assert resumed.write_count == first.write_count
    assert restarted._connection.execute("SELECT COUNT(*) FROM staging_operations").fetchone()[0] == 1


def test_causal_migration_persists_history_and_restart_returns_verified_completion(tmp_path):
    source = _source(tmp_path)
    before = source.capture_owner_snapshot("alice").snapshot_digest
    plan = _plan(source)
    issuer = _issuer()
    service = _service(tmp_path, issuer)
    principal = _principal(plan, issuer)
    result = migrate_prepared_knowledge_migration(
        plan, staging=service, principals={"alice": principal},
    )
    assert result.admitted_count == 1
    assert result.write_count == 2
    assert "alice" not in repr(result)
    assert source.capture_owner_snapshot("alice").snapshot_digest == before

    restarted = _service(tmp_path, issuer)
    resumed = restarted.resume_or_reserve(
        plan.migration_id, plan.owners, plan.source_manifest_digest, 300,
    )
    assert type(resumed) is StagingMigrationCompletionReference
    verified = restarted.verify_completion(resumed)
    assert verified.source_manifest_digest == plan.source_manifest_digest
    assert verified.write_count == 2
    assert "alice" not in repr(verified)

    restarted._connection.execute(
        "UPDATE staging_completions SET evidence_json='{}' WHERE generation_id=(SELECT generation_id FROM staging_reservations WHERE migration_id=?)",
        (plan.migration_id,),
    )
    with pytest.raises(StagingDestinationError) as raised:
        restarted.verify_completion(resumed)
    assert raised.value.code == "staging_destination_denied"
    assert "alice" not in repr(raised.value)


@pytest.mark.parametrize("target", ["staging_receipt", "store_receipt", "payload", "head", "count"])
def test_persisted_completion_rechecks_receipts_payload_heads_and_count_after_restart(tmp_path, target):
    source = _source(tmp_path, reviewed=False)
    plan = _plan(source)
    issuer = _issuer()
    service = _service(tmp_path, issuer)
    principal = _principal(plan, issuer)
    result = migrate_prepared_knowledge_migration(
        plan, staging=service, principals={"alice": principal},
    )
    generation = service._connection.execute(
        "SELECT generation_id FROM staging_reservations WHERE migration_id=?", (plan.migration_id,),
    ).fetchone()[0]
    service._stores[generation].close()

    restarted = _service(tmp_path, issuer)
    reservation = restarted._connection.execute(
        "SELECT * FROM staging_reservations WHERE generation_id=?", (generation,),
    ).fetchone()
    store = restarted._store(reservation)
    event = store._connection.execute(
        "SELECT receipt_id,new_revision_id FROM knowledge_events ORDER BY event_seq LIMIT 1",
    ).fetchone()
    if target == "staging_receipt":
        restarted._connection.execute(
            "UPDATE staging_operations SET receipt_json='{}' WHERE generation_id=?", (generation,),
        )
    elif target == "store_receipt":
        _fixture_database_corruption(store._connection, "knowledge_receipts",
            "UPDATE knowledge_receipts SET receipt_json='{}' WHERE receipt_id=?", (event[0],),
        )
    elif target == "payload":
        _fixture_database_corruption(store._connection, "knowledge_blobs",
            "UPDATE knowledge_blobs SET payload_bytes=? WHERE blob_id=(SELECT blob_id FROM knowledge_revisions WHERE revision_id=?)",
            (b"tampered payload", event[1]),
        )
    elif target == "head":
        store._connection.execute(
            "UPDATE knowledge_heads SET current_payload_sha256=? WHERE knowledge_id=?",
            (_ref("9"), plan.commands[0].knowledge_id),
        )
    else:
        restarted._connection.execute(
            "UPDATE staging_reservations SET write_count=write_count+1 WHERE generation_id=?", (generation,),
        )

    with pytest.raises(StagingDestinationError) as raised:
        restarted.verify_completion(result.completion_reference)
    assert raised.value.code == "staging_destination_denied"
    assert "alice" not in repr(raised.value)


def test_forged_completion_reference_and_caller_constructed_result_are_not_authority(tmp_path):
    source = _source(tmp_path, reviewed=False)
    plan = _plan(source)
    issuer = _issuer()
    service = _service(tmp_path, issuer)
    principal = _principal(plan, issuer)
    result = migrate_prepared_knowledge_migration(
        plan, staging=service, principals={"alice": principal},
    )
    forged = StagingMigrationCompletionReference("f" * 64, "e" * 64)

    with pytest.raises(StagingDestinationError):
        service.verify_completion(forged)
    assert service.verify_completion(result.completion_reference).write_count == result.write_count


def test_store_commit_before_staging_ledger_commit_recovers_append_without_duplicate(tmp_path, monkeypatch):
    source = _source(tmp_path)
    plan = _plan(source)
    issuer = _issuer()
    service = _service(tmp_path, issuer)
    principal = _principal(plan, issuer)
    original_receipt = service._receipt
    injected = {"done": False}

    def fail_after_store_commit(row, outcome, operation, status, store, writes):
        if outcome == "append_revision" and not injected["done"]:
            injected["done"] = True
            raise RuntimeError("simulated interruption")
        return original_receipt(row, outcome, operation, status, store, writes)

    monkeypatch.setattr(service, "_receipt", fail_after_store_commit)
    with pytest.raises(KnowledgeMigrationError) as raised:
        migrate_prepared_knowledge_migration(
            plan, staging=service, principals={"alice": principal},
        )
    assert raised.value.code == "staging_failed"
    assert injected["done"]
    generation = service._connection.execute(
        "SELECT generation_id FROM staging_reservations WHERE migration_id=?", (plan.migration_id,),
    ).fetchone()[0]
    store = service._stores[generation]
    assert store._connection.execute("SELECT COUNT(*) FROM knowledge_revisions").fetchone()[0] == 2
    assert service._connection.execute("SELECT COUNT(*) FROM staging_operations").fetchone()[0] == 1
    monkeypatch.setattr(service, "_receipt", original_receipt)
    store.close()

    restarted = _service(tmp_path, issuer)
    result = migrate_prepared_knowledge_migration(
        plan, staging=restarted, principals={"alice": principal},
    )
    assert result.write_count == 2
    recovered_generation = restarted._connection.execute(
        "SELECT generation_id FROM staging_reservations WHERE migration_id=?", (plan.migration_id,),
    ).fetchone()[0]
    recovered_store = restarted._stores[recovered_generation]
    assert recovered_store._connection.execute("SELECT COUNT(*) FROM knowledge_revisions").fetchone()[0] == 2
    assert restarted._connection.execute("SELECT COUNT(*) FROM staging_operations").fetchone()[0] == 2
    assert restarted.verify_completion(result.completion_reference).write_count == 2


def test_store_commit_before_staging_ledger_commit_recovers_create_after_restart(tmp_path, monkeypatch):
    source = _source(tmp_path, reviewed=False)
    plan = _plan(source)
    issuer = _issuer()
    service = _service(tmp_path, issuer)
    principal = _principal(plan, issuer)
    original_receipt = service._receipt
    injected = {"done": False}

    def fail_after_store_commit(row, outcome, operation, status, store, writes):
        if outcome == "create_object" and not injected["done"]:
            injected["done"] = True
            raise RuntimeError("simulated interruption")
        return original_receipt(row, outcome, operation, status, store, writes)

    monkeypatch.setattr(service, "_receipt", fail_after_store_commit)
    with pytest.raises(KnowledgeMigrationError) as raised:
        migrate_prepared_knowledge_migration(
            plan, staging=service, principals={"alice": principal},
        )
    assert raised.value.code == "staging_failed"
    assert injected["done"]
    generation = service._connection.execute(
        "SELECT generation_id FROM staging_reservations WHERE migration_id=?", (plan.migration_id,),
    ).fetchone()[0]
    store = service._stores[generation]
    assert store._connection.execute("SELECT COUNT(*) FROM knowledge_heads").fetchone()[0] == 1
    assert service._connection.execute("SELECT COUNT(*) FROM staging_operations").fetchone()[0] == 0
    store.close()

    restarted = _service(tmp_path, issuer)
    result = migrate_prepared_knowledge_migration(
        plan, staging=restarted, principals={"alice": principal},
    )
    assert result.write_count == 1
    recovered_generation = restarted._connection.execute(
        "SELECT generation_id FROM staging_reservations WHERE migration_id=?", (plan.migration_id,),
    ).fetchone()[0]
    recovered_store = restarted._stores[recovered_generation]
    assert recovered_store._connection.execute("SELECT COUNT(*) FROM knowledge_heads").fetchone()[0] == 1
    assert recovered_store._connection.execute("SELECT COUNT(*) FROM knowledge_revisions").fetchone()[0] == 1
    assert restarted._connection.execute("SELECT COUNT(*) FROM staging_operations").fetchone()[0] == 1
    assert restarted.verify_completion(result.completion_reference).write_count == 1
