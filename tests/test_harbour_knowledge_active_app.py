from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


_CHILD = r'''
import asyncio
import concurrent.futures
from dataclasses import replace
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import re
import sys
import threading

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from src.constants import APP_KEY_FILE, AUTH_FILE, DATA_DIR
phase = "fixture"
current_alias = "fuzzy"
def bounded_child_exception(exc_type, _exc_value, traceback_value):
    current = traceback_value
    last = None
    while current is not None:
        last = current.tb_frame
        current = current.tb_next
    allowed_files = {"<string>", "app.py", "app_initializer.py", "harbour_knowledge_active_runtime.py", "knowledge_tools.py", "test_harbour_knowledge_active_app.py", "knowledge-planning-app-child.py"}
    location = None
    if last is not None:
        file_name = Path(last.f_code.co_filename).name
        if file_name in allowed_files:
            location = [file_name, last.f_lineno]
    allowed_types = {"AssertionError", "ValueError", "TypeError", "NameError", "AttributeError", "RuntimeError", "KeyError", "HarbourKnowledgeStoreError", "LexicalRetrievalError"}
    Path(os.environ["ODYSSEUS_ACTIVE_APP_RESULT"]).write_text(
        json.dumps({"fatal_type": exc_type.__name__ if exc_type.__name__ in allowed_types else "unclassified", "phase": phase,
                    "location": location}, sort_keys=True), encoding="utf-8",
    )
sys.excepthook = bounded_child_exception
DATA_DIR = Path(DATA_DIR)
DATA_DIR.mkdir(parents=True, exist_ok=True)
APP_KEY_FILE = Path(APP_KEY_FILE)
APP_KEY_FILE.write_bytes(Fernet.generate_key())
if os.name != "nt":
    APP_KEY_FILE.chmod(0o600)
from core.auth import AuthManager
auth = AuthManager(str(AUTH_FILE))
assert auth.create_user(current_alias, "Active-App-Password-931!", is_admin=True)
subject = auth.subject_id_for_username(current_alias)
from src.unified_source_index_owner_scope import OwnerScope
owner_scope = OwnerScope.for_subject_id(subject).value
from src.unified_source_index_sources.knowledge import knowledge_source_capability_manifest
from src.causal_claim_store import CausalClaimStore
from src.harbour_authority import AuthenticatedPrincipalIssuer
from src.harbour_knowledge_migration import CausalMigrationSource, prepare_current_knowledge_migration, migrate_prepared_knowledge_migration
from src.harbour_knowledge_staging_destination import HarbourKnowledgeStagingDestinationService, _CONTEXT_SCHEMA, _sha
from src.harbour_knowledge_store import HarbourKnowledgeStore
from src.harbour_knowledge_store import HarbourKnowledgeStoreError
from src.unified_source_index_adapters import AdapterScope, ExtractionProfile
from src.unified_source_index_contract import Classification, RecordKind, RecordRef
from src.unified_source_index_sqlite import SQLiteUnifiedSourceIndexStore
from src.unified_source_index_sources.knowledge import KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE, KnowledgeSourceAdapter
from src.unified_source_index_sources.knowledge import KnowledgeSourceAdapterError, knowledge_source_capability_manifest
from src.unified_source_index_lexical import LexicalRetrievalError
from src.unified_source_index_runtime_config import UnifiedSourceIndexRuntimeConfig
from src.unified_source_index_query import FederatedQuery, FederatedQueryBudget, QueryMode
from src.unified_source_index_query_contract import LexicalMatchMode, LexicalQuery
from src.harbour_knowledge_active_runtime import ActiveKnowledgeComposition
import src.harbour_knowledge_active_runtime as runtime_module
key = __import__("base64").urlsafe_b64decode(APP_KEY_FILE.read_bytes())
issuer = AuthenticatedPrincipalIssuer(
    issuer_id="harbour-knowledge-runtime", key_id="app-key-v1",
    secret=hmac.new(key, b"odysseus.harbour.knowledge.issuer.v1\0", hashlib.sha256).digest(),
)
causal = CausalClaimStore(sqlite3.connect(DATA_DIR / "causal.sqlite"), initialize=True)
for index in range(2):
    causal.create_claim({
        "schema": "odysseus.causal_claim.v1", "claim_id": f"app-orbital-{index}",
        "owner": subject, "policy": "reviewed-local", "source_version": "sha256:" + str(index + 1) * 64,
        "claim_version": 1, "candidate_actor": "orbital", "evidence_refs": ["sha256:" + str(index + 3) * 64],
    })
plan = prepare_current_knowledge_migration(
    causal_sources=(CausalMigrationSource(subject, causal, subject),),
    owner_map={subject: subject}, retention_by_scope={subject: {
        "retention_class": "user_managed", "deletion_mode": "tombstone_only",
        "retain_until": None, "grace_until": None, "legal_hold": False,
    }}, observed_at="2026-10-01T12:00:00Z",
)
staging = HarbourKnowledgeStagingDestinationService(
    DATA_DIR / "harbour-knowledge-staging", trusted_issuers=(issuer,),
)
principal = issuer.issue(subject_id=subject, owner_id=subject, auth_context_digest=_sha({
    "migration_id": plan.migration_id, "owner_id": subject, "schema_id": _CONTEXT_SCHEMA,
    "snapshot_digest": plan.source_manifest_digest,
}))
completion = migrate_prepared_knowledge_migration(plan, staging=staging, principals={subject: principal}).completion_reference
initial_selection_digest = staging.select_generation(completion, expected_selection_digest="sha256:" + "0" * 64)
store = staging.selected_runtime(subject)[4]
writer = issuer.issue(subject_id=subject, owner_id=subject, auth_context_digest=_sha({"fixture": "writer"}))
store.create_object(
    writer, owner_id=subject, knowledge_id="app-active-note", knowledge_kind="note",
    payload_bytes=b"orbital active application evidence", payload_schema_id="harbour.note.v1",
    payload_media_type="text/plain", classification="private", idempotency_key="app-active-create",
)
sentinel_payload = b"orbital private sentinel continuity"
store.create_object(
    writer, owner_id=subject, knowledge_id="app-private-sentinel", knowledge_kind="note",
    payload_bytes=sentinel_payload, payload_schema_id="harbour.note.v1",
    payload_media_type="text/plain", classification="private", idempotency_key="app-sentinel-create",
)
native = KnowledgeSourceAdapter(
    store=store, principal=writer, owner_id=subject, cursor_signing_key=b"c" * 32,
    classification_ceiling=Classification.SENSITIVE,
)
sources = native.discover(AdapterScope(owner_scope, Classification.SENSITIVE), cursor="", limit=100, time_budget_ms=5000).items
projected = []
projected_chunks = []
for item in sources:
    source = item.source
    version = native.observe_version(RecordRef(RecordKind.SOURCE, source.source_id))
    chunks = native.extract(version, ExtractionProfile(KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE, 100, 100000, 5000)).chunks
    projected_chunks.extend(chunks)
    projected.extend((source, version, *chunks))
private_source_id = next(source.source_id for source in (item.source for item in sources)
                         if any("active application evidence" in (chunk.content or "")
                                for chunk in projected_chunks if getattr(chunk, "source_id", None) == source.source_id))
source_ids = tuple(item.source.source_id for item in sources)
sentinel_source_id = next(source.source_id for source in (item.source for item in sources)
                          if any("private sentinel continuity" in (chunk.content or "")
                                 for chunk in projected_chunks if getattr(chunk, "source_id", None) == source.source_id))
cursor_source_ids = tuple(source_id for source_id in source_ids if source_id not in {private_source_id, sentinel_source_id})
usi = SQLiteUnifiedSourceIndexStore(DATA_DIR / "usi.sqlite")
write = usi.begin_write(usi.current_snapshot())
for record in projected:
    write.put(record)
write.commit()
os.environ.update({
    "ODYSSEUS_USI_RUNTIME_ENABLED": "true", "ODYSSEUS_USI_RUNTIME_MODE": "read_only",
    "ODYSSEUS_USI_SELECTED_GENERATION": knowledge_source_capability_manifest().generation_ref,
    "ODYSSEUS_USI_ALLOWED_OWNERS": subject, "ODYSSEUS_USI_ALLOWED_SOURCES": ",".join(source_ids),
    "ODYSSEUS_USI_ALLOWED_DOMAINS": "native_knowledge", "ODYSSEUS_USI_SQLITE_PATH": "usi.sqlite",
    "ODYSSEUS_USI_QUERY_MAX_RESULTS": "8", "ODYSSEUS_USI_QUERY_TIMEOUT_SECONDS": "5",
})
staging.close()
native_instances = []
native_cursors = []
lexical_instances = []
lexical_cursors = []
lexical_queries = []
original_discover = KnowledgeSourceAdapter.discover
original_search = runtime_module._OwnerSnapshotLexicalRetriever.search
def capture_discover(adapter, *args, **kwargs):
    native_instances.append(adapter)
    page = original_discover(adapter, *args, **kwargs)
    if page.next_cursor:
        native_cursors.append((page.next_cursor, args[0]))
    elif page.items:
        probe = original_discover(adapter, args[0], cursor="", limit=1, time_budget_ms=5000)
        if probe.next_cursor:
            native_cursors.append((probe.next_cursor, args[0]))
    return page
def capture_search(retriever, query):
    lexical_instances.append(retriever)
    page = original_search(retriever, query)
    if query.cursor == "":
        probe_query = replace(query, limit=1, max_candidates=max(2, query.max_candidates))
        cursor_page = original_search(retriever, probe_query)
        if cursor_page.next_cursor:
            lexical_cursors.append(cursor_page.next_cursor)
            lexical_queries.append(probe_query)
    return page
KnowledgeSourceAdapter.discover = capture_discover
runtime_module._OwnerSnapshotLexicalRetriever.search = capture_search
import app as application
from src.agent_tools import knowledge_tools
phase = "app_start_a"
checks = {"composition_active": application.app.state.knowledge_composition is not None}
from src.planning_roadmap_migration import build_h1r03_migration_manifest, canonical_json_bytes
import src.plugin_system as plugin_system
repo_root = Path.cwd()
manifest = build_h1r03_migration_manifest(repo_root)
roadmap_id = manifest["source_items"][0]["roadmap_id"]
headers = {"Origin": "http://testserver"}
planning_token = ""
accepted_origin = None
sentinel_body = json.dumps({"query": "orbital", "domain": "native_knowledge", "mode": "lexical", "scope": [sentinel_source_id], "classification": "private", "budget": {"limit": 4, "max_candidates": 8, "time_budget_ms": 1000}})

def planning_pair(client, bearer):
    return [client.post("/api/plugins/mcp", headers={"Authorization": "Bearer " + bearer}, json={
        "jsonrpc": "2.0", "id": 50 + index, "method": "tools/call", "params": {
            "name": name, "arguments": {"project_id": "harbour-one", "roadmap_id": roadmap_id},
        },
    }) for index, name in enumerate(("planning_read_gate_definitions", "planning_create_agent_handoff"))]

def tool_projection(response):
    try:
        return json.loads(response.json()["result"]["content"][0]["text"])
    except (ValueError, KeyError, IndexError, TypeError):
        return {}

def combined_positive(client, alias):
    global accepted_origin
    definition, handoff = planning_pair(client, planning_token)
    d, h = tool_projection(definition), tool_projection(handoff)
    origin = d.get("origin", {})
    identity = (origin.get("revision"), origin.get("content_hash"))
    if accepted_origin is None:
        accepted_origin = identity
    sentinel = asyncio.run(knowledge_tools.query_knowledge(sentinel_body, {"owner": alias}))
    return (
        definition.status_code == handoff.status_code == 200
        and d.get("schema") == h.get("schema") == "odysseus.mcp.tool_result.v1"
        and origin.get("kind") == "accepted_durable_definition" and origin.get("source_of_truth") is True
        and type(identity[0]) is int and str(identity[1]).startswith("sha256:")
        and identity == accepted_origin
        and h.get("origin", {}).get("derived_from_accepted_durable") is True
        and h.get("origin", {}).get("source_of_truth") is False
        and (h.get("origin", {}).get("revision"), h.get("origin", {}).get("content_hash")) == identity
        and h.get("payload", {}).get("launch_authorized") is False
        and sentinel.get("exit_code") == 0 and sentinel.get("result_count", 0) >= 1
        and "orbital private sentinel continuity" in json.dumps(sentinel)
        and application.auth_manager.subject_id_for_username(alias) == subject
        and application.app.state.knowledge_composition._staging.current_selection()[2] == initial_selection_digest
    )

def sentinel_denied(alias):
    result = asyncio.run(knowledge_tools.query_knowledge(sentinel_body, {"owner": alias}))
    return result.get("exit_code") == 1 and "orbital private sentinel continuity" not in json.dumps(result)

def planning_unavailable(client, bearer):
    responses = planning_pair(client, bearer)
    return all(response.status_code == 200 and response.json().get("error") == {"code": -32010, "message": "Planning read is unavailable"} for response in responses)

with TestClient(application.app) as client:
    login = client.post("/api/auth/login", json={"username": current_alias, "password": "Active-App-Password-931!"})
    status = client.get("/api/diagnostics/unified-source-index/runtime-status")
    token = client.cookies.get("odysseus_session")
    username = application.auth_manager._sessions[token]["username"] if token else ""
    checks["authenticated_session"] = login.status_code == 200 and application.auth_manager.validate_token(token) and username == current_alias
    checks["active_status"] = status.status_code == 200 and status.json().get("knowledge", {}).get("mode") == "read_only"
    body = json.dumps({"query": "orbital", "domain": "native_knowledge", "mode": "lexical", "scope": [private_source_id], "classification": "private", "budget": {"limit": 4, "max_candidates": 8, "time_budget_ms": 1000}})
    query_result = asyncio.run(knowledge_tools.query_knowledge(body, {"owner": username}))
    checks["authenticated_query"] = bool(checks["authenticated_session"] and query_result.get("exit_code") == 0 and query_result.get("result_count", 0) >= 1)
    if checks["authenticated_session"]:
        scope = OwnerScope.for_subject_id(application.auth_manager.subject_id_for_username(username)).value
        cursor_query = FederatedQuery(
            owner_scope=scope, query_text="orbital", classification_ceiling=Classification.SENSITIVE,
            modes=(QueryMode.LEXICAL,), limit=2, source_ids=cursor_source_ids,
            domain="native_knowledge", budget=FederatedQueryBudget(
                max_providers=1, max_candidates_per_provider=8, max_total_candidates=20,
                time_budget_ms=1000, per_provider_timeout_ms=750,
            ),
        )
        app_composition = application.app.state.knowledge_composition
        cursor_page = app_composition.execute(cursor_query)
        checks["authenticated_native_query"] = bool(cursor_page.items)
        active_store = app_composition._staging.selected_runtime(subject)[4]
        active_principal = native_instances[-1]._principal
        active_snapshot = active_store.capture_index_snapshot(active_principal, owner_id=subject)
        migrated_sensitive = [
            record for record in active_snapshot.records
            if record.knowledge_kind == "causal_claim"
            and record.classification == "sensitive" and record.state == "active"
        ]
        active_native = native_instances[-1]
        migrated_source_ids = tuple(active_native._sid(record) for record in migrated_sensitive)
        migrated_page = app_composition.execute(replace(
            cursor_query, source_ids=migrated_source_ids, limit=8,
        ))
        returned_migrated = {
            item.evidence.source_id: item.evidence.policy_evidence.classification
            for item in migrated_page.items
        }
        checks["both_migrated_sensitive_records_via_app_state"] = (
            len(migrated_source_ids) == 2
            and set(returned_migrated) == set(migrated_source_ids)
            and all(value is Classification.SENSITIVE for value in returned_migrated.values())
        )

        application.auth_manager.create_user("bob", "Active-App-Other-Owner-931!")
        other_subject = application.auth_manager.subject_id_for_username("bob")
        try:
            app_composition.execute(replace(cursor_query, domain="memory"))
        except ValueError as error:
            checks["domain_denial_typed"] = type(error) is ValueError and error.args == ("knowledge query denied",)
        else:
            checks["domain_denial_typed"] = False
        try:
            app_composition.execute(replace(
                cursor_query, owner_scope=OwnerScope.for_subject_id(other_subject).value,
            ))
        except ValueError as error:
            checks["owner_denial_typed"] = type(error) is ValueError and error.args == ("knowledge owner unavailable",)
        else:
            checks["owner_denial_typed"] = False
    else:
        checks["authenticated_native_query"] = False
        checks["both_migrated_sensitive_records_via_app_state"] = False
        checks["domain_denial_typed"] = False
        checks["owner_denial_typed"] = False
    checks["captured_native_cursor"] = bool(native_cursors)
    checks["captured_guarded_lexical_cursor"] = bool(lexical_cursors)
    census_digest = "sha256:" + hashlib.sha256(canonical_json_bytes(manifest["structural_census"])).hexdigest()
    migration_path = "/api/planning/durable/migrations/h1r03"
    prepared = client.post(migration_path + "/prepare", headers=headers, json={
        "expected_batch_id": manifest["batch_id"], "expected_manifest_digest": manifest["manifest_digest"],
        "expected_structural_census_digest": census_digest,
    })
    accepted = client.post(migration_path + "/accept", headers=headers, json={
        "batch_id": manifest["batch_id"], "expected_manifest_digest": manifest["manifest_digest"],
        "expected_structural_census_digest": census_digest,
        "prior_receipt_id": prepared.json().get("receipt", {}).get("receipt_id", ""),
    })
    config = client.post("/api/plugins/mcp/config", headers=headers, json={"enabled": True, "allow_planning_reads": True})
    minted = client.post("/api/tokens", headers=headers, data={"name": "combined-private-planning", "scopes": "mcp:planning:read"})
    planning_token = minted.json().get("token", "")
    planning_token_id = minted.json().get("id")
    checks["combined_public_setup"] = prepared.status_code == accepted.status_code == config.status_code == minted.status_code == 200 and accepted.json().get("receipt", {}).get("after_state") == "accepted" and isinstance(planning_token, str) and bool(planning_token)
    checks["combined_initial_positive"] = combined_positive(client, current_alias)
checks["resolver_cleaned"] = knowledge_tools._owner_resolver is None
checks["closed_query_denied"] = asyncio.run(knowledge_tools.query_knowledge(body, {"owner": current_alias})).get("exit_code") == 1
import importlib
phase = "app_start_b"
plugin_system.MANAGER = None
application = importlib.reload(application)
with TestClient(application.app) as restarted_client:
    restarted_login = restarted_client.post("/api/auth/login", json={"username": current_alias, "password": "Active-App-Password-931!"})
    restarted_status = restarted_client.get("/api/diagnostics/unified-source-index/runtime-status")
    restart_page = application.app.state.knowledge_composition.execute(cursor_query)
    checks["same_selection_app_restart"] = (
        restarted_login.status_code == 200 and restarted_status.status_code == 200
        and bool(restart_page.items)
    )
    restart_native = native_instances[-1]
    resumed_native = restart_native.discover(
        AdapterScope(owner_scope, Classification.SENSITIVE, cursor_source_ids),
        cursor=native_cursors[0][0], limit=1, time_budget_ms=1000,
    )
    checks["native_cursor_resumed"] = bool(resumed_native.items)
    resumed_lexical = lexical_instances[-1].search(replace(lexical_queries[0], cursor=lexical_cursors[0]))
    checks["lexical_cursor_resumed"] = bool(resumed_lexical.items)
    restarted_private_result = asyncio.run(knowledge_tools.query_knowledge(body, {"owner": current_alias}))
    checks["postselection_append_survives_app_restart"] = (
        restarted_private_result.get("exit_code") == 0
        and restarted_private_result.get("result_count", 0) >= 1
    )
    listed_tokens = restarted_client.get("/api/tokens")
    checks["combined_persisted_token_subject_generation"] = combined_positive(restarted_client, current_alias) and listed_tokens.status_code == 200 and any(item.get("id") == planning_token_id and item.get("is_active") is True and item.get("scopes") == ["mcp:planning:read"] for item in listed_tokens.json())
    import routes.auth_user_rename as rename_ops
    original_sql_rename = rename_ops._rename_sql_owner_references
    sql_entered, sql_release = threading.Event(), threading.Event()
    def barrier_sql_rename(old_owner, new_owner):
        sql_entered.set()
        if not sql_release.wait(10):
            raise RuntimeError("bounded rename barrier expired")
        return original_sql_rename(old_owner, new_owner)
    rename_ops._rename_sql_owner_references = barrier_sql_rename
    rename_probe_client = TestClient(application.app)
    phase = "combined_rename_window"
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            rename_future = pool.submit(restarted_client.put, "/api/auth/users/fuzzy/rename", headers=headers, json={"username": "fuzzy-window"})
            try:
                entered = sql_entered.wait(10)
                during = planning_pair(rename_probe_client, planning_token) if entered else []
            finally:
                sql_release.set()
            renamed = rename_future.result(timeout=15)
    finally:
        sql_release.set()
        rename_ops._rename_sql_owner_references = original_sql_rename
        rename_probe_client.close()
    checks["combined_rename_window_denied"] = entered and len(during) == 2 and all(response.status_code == 401 for response in during)
    current_alias = "fuzzy-window"
    old_alias_client = TestClient(application.app)
    try:
        old_login = old_alias_client.post("/api/auth/login", json={"username": "fuzzy", "password": "Active-App-Password-931!"})
    finally:
        old_alias_client.close()
    checks["combined_rename_converged"] = renamed.status_code == 200 and old_login.status_code != 200 and sentinel_denied("fuzzy") and combined_positive(restarted_client, current_alias)
    selected_store = application.app.state.knowledge_composition._staging.selected_runtime(subject)[4]
    active_head = selected_store.get_head(writer, owner_id=subject, knowledge_id="app-active-note")
    selected_store.tombstone(
        writer, owner_id=subject, knowledge_id="app-active-note", expected_head=active_head,
        idempotency_key="app-active-tombstone",
    )
    tombstoned_query = asyncio.run(knowledge_tools.query_knowledge(body, {"owner": current_alias}))
    tombstone_status = restarted_client.get("/api/diagnostics/unified-source-index/runtime-status")
    checks["postselection_tombstone_no_stale_snippet"] = (
        tombstone_status.status_code == 200
        and tombstone_status.json().get("knowledge", {}).get("mode") == "read_only"
        and tombstoned_query.get("exit_code") == 0
        and tombstoned_query.get("result_count") == 0
        and "orbital active application evidence" not in json.dumps(tombstoned_query)
    )
phase = "post_tombstone_restart"
plugin_system.MANAGER = None
application = importlib.reload(application)
with TestClient(application.app) as tombstone_restart_client:
    phase = "tombstone_restart_login"
    tombstone_login = tombstone_restart_client.post("/api/auth/login", json={"username": current_alias, "password": "Active-App-Password-931!"})
    phase = "tombstone_restart_status"
    tombstone_status = tombstone_restart_client.get("/api/diagnostics/unified-source-index/runtime-status")
    phase = "tombstone_restart_query"
    tombstone_result = asyncio.run(knowledge_tools.query_knowledge(body, {"owner": current_alias}))
    checks["tombstone_restart_denies_stale_snippet"] = (
        tombstone_login.status_code == 200
        and tombstone_status.status_code == 200
        and tombstone_status.json().get("knowledge", {}).get("mode") == "read_only"
        and tombstone_result.get("exit_code") == 0
        and tombstone_result.get("result_count") == 0
        and "orbital active application evidence" not in json.dumps(tombstone_result)
    )
causal.create_claim({
    "schema": "odysseus.causal_claim.v1", "claim_id": "app-orbital-2",
    "owner": subject, "policy": "reviewed-local", "source_version": "sha256:" + "4" * 64,
    "claim_version": 1, "candidate_actor": "orbital", "evidence_refs": ["sha256:" + "5" * 64],
})
next_plan = prepare_current_knowledge_migration(
    causal_sources=(CausalMigrationSource(subject, causal, subject),),
    owner_map={subject: subject}, retention_by_scope={subject: {
        "retention_class": "user_managed", "deletion_mode": "tombstone_only",
        "retain_until": None, "grace_until": None, "legal_hold": False,
    }}, observed_at="2026-10-01T12:01:00Z",
)
staging_next = HarbourKnowledgeStagingDestinationService.open_existing(
    DATA_DIR / "harbour-knowledge-staging", trusted_issuers=(issuer,),
)
next_context = _sha({"migration_id": next_plan.migration_id, "owner_id": subject,
                     "schema_id": _CONTEXT_SCHEMA,
                     "snapshot_digest": next_plan.source_manifest_digest})
next_principal = issuer.issue(subject_id=subject, owner_id=subject, auth_context_digest=next_context)
next_completion = migrate_prepared_knowledge_migration(
    next_plan, staging=staging_next, principals={subject: next_principal},
).completion_reference
prior_selection = staging_next.current_selection()[2]
selection_b_digest = staging_next.select_generation(next_completion, expected_selection_digest=prior_selection)
next_store = staging_next.selected_runtime(subject)[4]
next_native = KnowledgeSourceAdapter(
    store=next_store, principal=next_principal, owner_id=subject, cursor_signing_key=b"c" * 32,
    classification_ceiling=Classification.SENSITIVE,
)
next_sources = next_native.discover(
    AdapterScope(owner_scope, Classification.SENSITIVE), cursor="", limit=100, time_budget_ms=5000,
).items
selected_current_ids = tuple(item.source.source_id for item in next_sources)
next_projection = []
for item in next_sources:
    version = next_native.observe_version(RecordRef(RecordKind.SOURCE, item.source.source_id))
    chunks = next_native.extract(
        version, ExtractionProfile(KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE, 100, 100000, 5000),
    ).chunks
    next_projection.extend((item.source, version, *chunks))
usi_writer = SQLiteUnifiedSourceIndexStore(DATA_DIR / "usi.sqlite")
next_write = usi_writer.begin_write(usi_writer.current_snapshot())
for projected_record in next_projection:
    next_write.put(projected_record)
next_write.commit()
os.environ["ODYSSEUS_USI_ALLOWED_SOURCES"] = ",".join(sorted(set(source_ids) | set(selected_current_ids)))
staging_next.close()
phase = "new_selection_restart"
plugin_system.MANAGER = None
application = importlib.reload(application)
with TestClient(application.app) as selection_restart_client:
    phase = "selection_login"
    selection_login = selection_restart_client.post("/api/auth/login", json={"username": current_alias, "password": "Active-App-Password-931!"})
    phase = "selection_initial_query"
    current_source_ids = selected_current_ids
    phase = "selection_build_federated_query"
    current_federated = replace(cursor_query, source_ids=current_source_ids)
    phase = "selection_query"
    phase = "selection_execute_query"
    current_page = application.app.state.knowledge_composition.execute(current_federated)
    try:
        native_instances[-1].discover(
            AdapterScope(owner_scope, Classification.SENSITIVE, cursor_source_ids),
            cursor=native_cursors[0][0], limit=1, time_budget_ms=1000,
        )
    except KnowledgeSourceAdapterError as error:
        checks["changed_selection_rejects_native_cursor"] = error.code == "invalid_request"
    else:
        checks["changed_selection_rejects_native_cursor"] = False
    try:
        lexical_instances[-1].search(replace(lexical_queries[0], cursor=lexical_cursors[0]))
    except LexicalRetrievalError:
        checks["changed_selection_rejects_lexical_cursor"] = True
    else:
        checks["changed_selection_rejects_lexical_cursor"] = False
    checks["changed_selection_app_startup"] = selection_login.status_code == 200 and bool(current_page.items)
    phase = "selection_cursor_positive"
    current_native_cursor = native_cursors[-1][0]
    current_lexical_cursor = lexical_cursors[-1]
    current_lexical_query = lexical_queries[-1]
    try:
        current_native_positive = native_instances[-1].discover(
            AdapterScope(owner_scope, Classification.SENSITIVE, current_source_ids),
            cursor=current_native_cursor, limit=1, time_budget_ms=1000,
        )
        checks["current_native_cursor_accepted"] = bool(current_native_positive.items)
    except KnowledgeSourceAdapterError:
        current_native_positive = None
        checks["current_native_cursor_accepted"] = False
    try:
        current_lexical_positive = lexical_instances[-1].search(
            replace(current_lexical_query, cursor=current_lexical_cursor),
        )
        checks["current_lexical_cursor_accepted"] = bool(current_lexical_positive.items)
    except LexicalRetrievalError:
        current_lexical_positive = None
        checks["current_lexical_cursor_accepted"] = False
    checks["current_generation_cursors_work_before_key_change"] = (
        current_native_positive is not None and bool(current_native_positive.items)
        and current_lexical_positive is not None and bool(current_lexical_positive.items)
    )
    b_native_cursor = current_native_cursor
    b_lexical_cursor = current_lexical_cursor
    rollback_context = application.app.state.knowledge_composition._staging.rollback_authority_context_digest(
        selection_b_digest, initial_selection_digest,
    )
    rollback_principal = issuer.issue(
        subject_id=subject, owner_id=subject, auth_context_digest=rollback_context,
    )
    rollback_receipt = application.app.state.knowledge_composition._staging.rollback_generation(
        completion, expected_selection_digest=selection_b_digest,
        target_selection_digest=initial_selection_digest, principals={subject: rollback_principal},
    )
    checks["app_rollback_committed"] = (
        rollback_receipt.outcome == "rolled_back"
        and application.app.state.knowledge_composition._staging.current_selection()[2]
        == rollback_receipt.rollback_selection_digest
    )
    old_principal = native_instances[-1]._principal
    phase = "selection_principal_positive"
    current_store = application.app.state.knowledge_composition._staging.selected_runtime(subject)[4]
    checks["old_principal_valid_before_key_change"] = current_store.capture_index_snapshot(
        old_principal, owner_id=subject,
    ).owner_id == subject
phase = "rollback_restart"
plugin_system.MANAGER = None
application = importlib.reload(application)
with TestClient(application.app) as rollback_restart_client:
    rollback_login = rollback_restart_client.post("/api/auth/login", json={"username": current_alias, "password": "Active-App-Password-931!"})
    rollback_status = rollback_restart_client.get("/api/diagnostics/unified-source-index/runtime-status")
    current_federated = replace(cursor_query, source_ids=source_ids)
    rollback_page = application.app.state.knowledge_composition.execute(current_federated)
    checks["rollback_restart_fresh_aprime_query"] = (
        rollback_login.status_code == 200 and rollback_status.status_code == 200
        and bool(rollback_page.items)
        and application.app.state.knowledge_composition._staging.current_selection()[2]
        == rollback_receipt.rollback_selection_digest
    )
    rollback_native = native_instances[-1]
    current_native_cursor, current_native_scope = native_cursors[-1]
    current_lexical_cursor = lexical_cursors[-1]
    current_lexical_query = lexical_queries[-1]
    try:
        rollback_native.discover(
            AdapterScope(owner_scope, Classification.SENSITIVE, selected_current_ids),
            cursor=b_native_cursor, limit=1, time_budget_ms=1000,
        )
        checks["rollback_rejects_b_native_cursor"] = False
    except KnowledgeSourceAdapterError:
        checks["rollback_rejects_b_native_cursor"] = True
    try:
        rollback_native.discover(
            AdapterScope(owner_scope, Classification.SENSITIVE, cursor_source_ids),
            cursor=native_cursors[0][0], limit=1, time_budget_ms=1000,
        )
        checks["rollback_rejects_a_native_cursor"] = False
    except KnowledgeSourceAdapterError:
        checks["rollback_rejects_a_native_cursor"] = True
    try:
        lexical_instances[-1].search(replace(lexical_queries[-1], cursor=b_lexical_cursor))
    except LexicalRetrievalError:
        checks["rollback_rejects_b_lexical_cursor"] = True
    else:
        checks["rollback_rejects_b_lexical_cursor"] = False
    try:
        lexical_instances[-1].search(replace(lexical_queries[0], cursor=lexical_cursors[0]))
    except LexicalRetrievalError:
        checks["rollback_rejects_a_lexical_cursor"] = True
    else:
        checks["rollback_rejects_a_lexical_cursor"] = False
    try:
        checks["rollback_fresh_aprime_native_cursor"] = bool(native_instances[-1].discover(
            current_native_scope, cursor=current_native_cursor, limit=1, time_budget_ms=1000,
        ).items)
    except KnowledgeSourceAdapterError:
        checks["rollback_fresh_aprime_native_cursor"] = False
    try:
        checks["rollback_fresh_aprime_lexical_cursor"] = bool(lexical_instances[-1].search(
            replace(current_lexical_query, cursor=current_lexical_cursor),
        ).items)
    except LexicalRetrievalError:
        checks["rollback_fresh_aprime_lexical_cursor"] = False
old_key = APP_KEY_FILE.read_bytes()
selected_before_key_change = rollback_receipt.rollback_selection_digest
APP_KEY_FILE.write_bytes(Fernet.generate_key())
phase = "changed_key_restart"
plugin_system.MANAGER = None
application = importlib.reload(application)
with TestClient(application.app) as key_restart_client:
    key_login = key_restart_client.post("/api/auth/login", json={"username": current_alias, "password": "Active-App-Password-931!"})
    application.app.state.knowledge_composition.execute(current_federated)
    selected_after_key_restart = application.app.state.knowledge_composition._staging.current_selection()[2]
    checks["key_only_restart_preserves_selection"] = selected_after_key_restart == selected_before_key_change
    fresh_native_cursor, fresh_native_scope = native_cursors[-1]
    fresh_lexical_cursor = lexical_cursors[-1]
    fresh_lexical_query = lexical_queries[-1]
    try:
        fresh_native_page = native_instances[-1].discover(
            fresh_native_scope, cursor=fresh_native_cursor, limit=1, time_budget_ms=1000,
        )
        checks["fresh_native_cursor_after_key_only_restart"] = bool(fresh_native_page.items)
    except KnowledgeSourceAdapterError:
        checks["fresh_native_cursor_after_key_only_restart"] = False
    try:
        fresh_lexical_page = lexical_instances[-1].search(
            replace(fresh_lexical_query, cursor=fresh_lexical_cursor),
        )
        checks["fresh_lexical_cursor_after_key_only_restart"] = bool(fresh_lexical_page.items)
    except LexicalRetrievalError:
        checks["fresh_lexical_cursor_after_key_only_restart"] = False
    current_store = application.app.state.knowledge_composition._staging.selected_runtime(subject)[4]
    try:
        current_store.capture_index_snapshot(old_principal, owner_id=subject)
    except HarbourKnowledgeStoreError as error:
        checks["changed_key_rejects_old_principal"] = error.code == "unauthenticated"
    else:
        checks["changed_key_rejects_old_principal"] = False
    try:
        native_instances[-1].discover(
            AdapterScope(owner_scope, Classification.SENSITIVE, current_source_ids),
            cursor=current_native_cursor, limit=1, time_budget_ms=1000,
        )
    except KnowledgeSourceAdapterError:
        checks["changed_key_rejects_native_cursor"] = True
    else:
        checks["changed_key_rejects_native_cursor"] = False
    try:
        lexical_instances[-1].search(replace(current_lexical_query, cursor=current_lexical_cursor))
    except LexicalRetrievalError:
        checks["changed_key_rejects_lexical_cursor"] = True
    else:
        checks["changed_key_rejects_lexical_cursor"] = False
    checks["changed_key_app_startup"] = key_login.status_code == 200
    APP_KEY_FILE.write_bytes(old_key)
phase = "combined_before_delete"
plugin_system.MANAGER = None
application = importlib.reload(application)
with TestClient(application.app) as delete_client:
    owner_login = delete_client.post("/api/auth/login", json={"username": current_alias, "password": "Active-App-Password-931!"})
    before_pair = planning_pair(delete_client, planning_token)
    before_sentinel = asyncio.run(knowledge_tools.query_knowledge(sentinel_body, {"owner": current_alias}))
    composition = application.app.state.knowledge_composition
    retained_selection = composition._staging.current_selection()[2]
    retained_store = composition._staging.selected_runtime(subject)[4]
    retained_principal = native_instances[-1]._principal
    retained_head = retained_store.get_head(retained_principal, owner_id=subject, knowledge_id="app-private-sentinel")
    retained_bytes = retained_store.read_payload(retained_principal, owner_id=subject, revision_id=retained_head.current_revision_id)
    checks["combined_aprime_positive_before_delete"] = (
        owner_login.status_code == 200 and retained_selection == rollback_receipt.rollback_selection_digest
        and retained_bytes == sentinel_payload
        and before_sentinel.get("exit_code") == 0 and before_sentinel.get("result_count", 0) >= 1
        and "orbital private sentinel continuity" in json.dumps(before_sentinel)
        and all(item.status_code == 200 and tool_projection(item).get("schema") == "odysseus.mcp.tool_result.v1" for item in before_pair)
        and (tool_projection(before_pair[0]).get("origin", {}).get("revision"), tool_projection(before_pair[0]).get("origin", {}).get("content_hash")) == accepted_origin
    )
    promoted = delete_client.put("/api/auth/users/bob/admin", headers=headers, json={"is_admin": True})
    guardian_login = delete_client.post("/api/auth/login", json={"username": "bob", "password": "Active-App-Other-Owner-931!"})
    phase = "combined_owner_delete"
    deleted = delete_client.request("DELETE", "/api/auth/users", headers=headers, json={"username": current_alias})
    checks["combined_public_owner_delete"] = promoted.status_code == guardian_login.status_code == deleted.status_code == 200
    checks["combined_deleted_immediate_denied"] = all(item.status_code == 401 for item in planning_pair(delete_client, planning_token)) and sentinel_denied(current_alias)
    checks["combined_deleted_payload_retained"] = composition._staging.current_selection()[2] == retained_selection and retained_store.read_payload(retained_principal, owner_id=subject, revision_id=retained_head.current_revision_id) == retained_bytes
phase = "combined_deleted_restart"
plugin_system.MANAGER = None
application = importlib.reload(application)
with TestClient(application.app) as recreated_client:
    checks["combined_deleted_restart_denied"] = all(item.status_code == 401 for item in planning_pair(recreated_client, planning_token)) and sentinel_denied(current_alias)
    guardian_login = recreated_client.post("/api/auth/login", json={"username": "bob", "password": "Active-App-Other-Owner-931!"})
    recreated = recreated_client.post("/api/auth/users", headers=headers, json={"username": current_alias, "password": "Active-App-Password-931!", "is_admin": True})
    recreated_subject = application.auth_manager.subject_id_for_username(current_alias)
    recreated_login = recreated_client.post("/api/auth/login", json={"username": current_alias, "password": "Active-App-Password-931!"})
    recreated_mint = recreated_client.post("/api/tokens", headers=headers, data={"name": "combined-recreated", "scopes": "mcp:planning:read"})
    recreated_token = recreated_mint.json().get("token", "")
    checks["combined_same_alias_new_subject"] = guardian_login.status_code == recreated.status_code == recreated_login.status_code == recreated_mint.status_code == 200 and isinstance(recreated_subject, str) and recreated_subject != subject and isinstance(recreated_token, str) and bool(recreated_token)
    checks["combined_recreated_immediate_denied"] = planning_unavailable(recreated_client, recreated_token) and sentinel_denied(current_alias)
phase = "combined_recreated_restart"
plugin_system.MANAGER = None
application = importlib.reload(application)
with TestClient(application.app) as final_client:
    final_login = final_client.post("/api/auth/login", json={"username": current_alias, "password": "Active-App-Password-931!"})
    checks["combined_recreated_restart_denied"] = final_login.status_code == 200 and planning_unavailable(final_client, recreated_token) and sentinel_denied(current_alias)
    checks["combined_old_bearer_still_denied"] = all(item.status_code == 401 for item in planning_pair(final_client, planning_token))
    composition = application.app.state.knowledge_composition
    retained_store = composition._staging.selected_runtime(subject)[4]
    fresh_retained_principal = writer
    checks["combined_final_selection_payload_retained"] = composition._staging.current_selection()[2] == retained_selection and retained_store.read_payload(fresh_retained_principal, owner_id=subject, revision_id=retained_head.current_revision_id) == retained_bytes and hashlib.sha256(retained_bytes).digest() == hashlib.sha256(sentinel_payload).digest()
causal.close()
checks["ok"] = all(checks.values())
Path(os.environ["ODYSSEUS_ACTIVE_APP_RESULT"]).write_text(json.dumps(checks, sort_keys=True), encoding="utf-8")
raise SystemExit(0 if checks["ok"] else 2)
'''


def test_actual_application_active_read_only_query_auth_status_and_shutdown(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    data_root = tmp_path / "data"
    plugin_root = tmp_path / "plugins"
    profile_root = tmp_path / "profile"
    data_root.mkdir()
    plugin_root.mkdir()
    (plugin_root / "mcp_server").mkdir()
    shutil.copyfile(repo_root / "plugins/mcp_server/plugin.py", plugin_root / "mcp_server/plugin.py")
    for path in (profile_root, profile_root / "roaming", profile_root / "local", tmp_path / "temp", tmp_path / "cache"):
        path.mkdir(parents=True, exist_ok=True)
    result_path = tmp_path / "active-app-result.json"
    env = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "WINDIR") if name in os.environ}
    env.update({
        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(repo_root),
        "PYTHON_DOTENV_DISABLED": "1", "HOME": str(profile_root), "USERPROFILE": str(profile_root),
        "APPDATA": str(profile_root / "roaming"), "LOCALAPPDATA": str(profile_root / "local"),
        "TEMP": str(tmp_path / "temp"), "TMP": str(tmp_path / "temp"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"), "ODYSSEUS_DATA_DIR": str(data_root),
        "DATABASE_URL": "sqlite:///" + (data_root / "app.db").as_posix(),
        "ODYSSEUS_PLUGINS_DIR": str(plugin_root), "ODYSSEUS_DISABLE_MCP": "1",
        "ODYSSEUS_DISABLE_USER_MCP": "1", "ODYSSEUS_STARTUP_OFFLINE": "true",
        "ODYSSEUS_INPROCESS_TASKS": "0", "ODYSSEUS_SECURITY_PUBLIC_EGRESS_DISCOVERY_DISABLED": "true",
        "ODYSSEUS_SECURITY_INCIDENT_DELIVERY_ENABLED": "false", "ODYSSEUS_TRANSCRIPTION_ENABLED": "false",
        "ODYSSEUS_LEGACY_STT_ENABLED": "false", "ODYSSEUS_PLANNING_ISSUER_SECRET_B64": "eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg=",
        "ODYSSEUS_PLANNING_ISSUER_KEY_ID": "active-app-test-key", "AUTH_ENABLED": "true",
        "LOCALHOST_BYPASS": "false", "SECURE_COOKIES": "false",
        "ODYSSEUS_ACTIVE_APP_RESULT": str(result_path),
    })
    child_script = tmp_path / "knowledge-planning-app-child.py"
    with child_script.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(_CHILD)
    result = subprocess.run(
        [sys.executable, str(child_script)], cwd=repo_root, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180, check=False,
    )
    if not result_path.is_file():
        assert False, {"child_result_available": False, "child_returncode": result.returncode if type(result.returncode) is int and 0 <= result.returncode <= 255 else "unavailable"}
    projection = json.loads(result_path.read_text(encoding="utf-8"))
    expected = {
        "active_status": True, "authenticated_query": True, "authenticated_session": True,
        "authenticated_native_query": True, "captured_guarded_lexical_cursor": True,
        "captured_native_cursor": True, "same_selection_app_restart": True,
        "native_cursor_resumed": True, "lexical_cursor_resumed": True,
        "postselection_tombstone_no_stale_snippet": True,
        "tombstone_restart_denies_stale_snippet": True,
        "postselection_append_survives_app_restart": True,
        "app_rollback_committed": True, "rollback_restart_fresh_aprime_query": True,
        "rollback_rejects_a_native_cursor": True, "rollback_rejects_b_native_cursor": True,
        "rollback_rejects_a_lexical_cursor": True, "rollback_rejects_b_lexical_cursor": True,
        "rollback_fresh_aprime_native_cursor": True, "rollback_fresh_aprime_lexical_cursor": True,
        "changed_selection_rejects_native_cursor": True,
        "changed_selection_rejects_lexical_cursor": True,
        "changed_selection_app_startup": True,
        "changed_key_rejects_old_principal": True,
        "current_generation_cursors_work_before_key_change": True,
        "current_native_cursor_accepted": True,
        "current_lexical_cursor_accepted": True,
        "old_principal_valid_before_key_change": True,
        "changed_key_rejects_native_cursor": True,
        "changed_key_rejects_lexical_cursor": True,
        "changed_key_app_startup": True,
        "both_migrated_sensitive_records_via_app_state": True,
        "owner_denial_typed": True, "domain_denial_typed": True,
        "key_only_restart_preserves_selection": True,
        "fresh_native_cursor_after_key_only_restart": True,
        "fresh_lexical_cursor_after_key_only_restart": True,
        "closed_query_denied": True, "composition_active": True, "ok": True, "resolver_cleaned": True,
        "combined_public_setup": True, "combined_initial_positive": True,
        "combined_persisted_token_subject_generation": True, "combined_rename_window_denied": True,
        "combined_rename_converged": True, "combined_aprime_positive_before_delete": True,
        "combined_public_owner_delete": True, "combined_deleted_immediate_denied": True,
        "combined_deleted_payload_retained": True, "combined_deleted_restart_denied": True,
        "combined_same_alias_new_subject": True, "combined_recreated_immediate_denied": True,
        "combined_recreated_restart_denied": True, "combined_old_bearer_still_denied": True,
        "combined_final_selection_payload_retained": True,
    }
    if "fatal_type" in projection:
        allowed_types = {"AssertionError", "ValueError", "TypeError", "NameError", "AttributeError", "RuntimeError", "KeyError", "HarbourKnowledgeStoreError", "LexicalRetrievalError", "unclassified"}
        allowed_phases = {"fixture", "app_start_a", "app_start_b", "post_tombstone_restart", "tombstone_restart_login", "tombstone_restart_status", "tombstone_restart_query", "new_selection_restart", "selection_login", "selection_initial_query", "selection_build_federated_query", "selection_query", "selection_execute_query", "selection_cursor_positive", "selection_principal_positive", "rollback_restart", "changed_key_restart", "combined_rename_window", "combined_before_delete", "combined_owner_delete", "combined_deleted_restart", "combined_recreated_restart"}
        assert set(projection) == {"fatal_type", "phase", "location"} and projection["fatal_type"] in allowed_types and projection["phase"] in allowed_phases, "invalid_child_diagnostic"
        location = projection["location"]
        assert location is None or (type(location) is list and len(location) == 2 and location[0] in {"<string>", "app.py", "app_initializer.py", "harbour_knowledge_active_runtime.py", "knowledge_tools.py", "test_harbour_knowledge_active_app.py", "knowledge-planning-app-child.py"} and type(location[1]) is int and 1 <= location[1] <= 100000), "invalid_child_location"
        assert False, projection
    assert type(projection) is dict and set(projection) == set(expected) and all(type(value) is bool for value in projection.values()), "invalid_child_boolean_shape"
    assert result.returncode == 0, {"failed_checks": sorted(key for key in expected if projection[key] is False)}
    assert projection == expected, sorted(key for key, value in expected.items() if projection.get(key) != value)
