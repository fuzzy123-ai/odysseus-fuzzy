from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


_OFFLINE_TEST_PATH = Path(__file__).with_name("test_app_offline_startup.py")
_OFFLINE_SPEC = importlib.util.spec_from_file_location("offline_startup_test_harness", _OFFLINE_TEST_PATH)
assert _OFFLINE_SPEC is not None and _OFFLINE_SPEC.loader is not None
_OFFLINE_MODULE = importlib.util.module_from_spec(_OFFLINE_SPEC)
_OFFLINE_SPEC.loader.exec_module(_OFFLINE_MODULE)
_TRIPWIRE = _OFFLINE_MODULE._TRIPWIRE


_CHILD = _TRIPWIRE + r'''
import base64
import hashlib
import json
import os
from pathlib import Path
import threading
import time

phase = os.environ["ODYSSEUS_TEST_PHASE"]
started = time.monotonic()
stage = "import"
checkpoint_lock = threading.Lock()

def checkpoint(stage):
    with checkpoint_lock:
        elapsed_ms = min(180000, max(0, int((time.monotonic() - started) * 1000)))
        try:
            with Path(os.environ["ODYSSEUS_TEST_CHECKPOINT_FILE"]).open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({
                    "phase": phase,
                    "stage": stage,
                    "elapsed_ms": elapsed_ms,
                }, sort_keys=True, separators=(",", ":")) + "\n")
        except OSError:
            pass

checkpoint("app_import_before")

def write_result(value):
    Path(os.environ["ODYSSEUS_TEST_RESULT_FILE"]).write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )

try:
    from fastapi.testclient import TestClient
    from cryptography.fernet import Fernet
    from src.constants import APP_KEY_FILE, DATA_DIR
    if phase != "no_issuer":
        import hmac
        import sqlite3
        from core.auth import AuthManager
        from src.causal_claim_store import CausalClaimStore
        from src.harbour_authority import AuthenticatedPrincipalIssuer
        from src.harbour_knowledge_migration import (
            CausalMigrationSource,
            migrate_prepared_knowledge_migration,
            prepare_current_knowledge_migration,
        )
        from src.harbour_knowledge_staging_destination import (
            HarbourKnowledgeStagingDestinationService,
            _CONTEXT_SCHEMA,
            _sha,
        )
        from src.unified_source_index_adapters import AdapterScope, ExtractionProfile
        from src.unified_source_index_contract import Classification, RecordKind, RecordRef
        from src.unified_source_index_owner_scope import OwnerScope
        from src.unified_source_index_sources.knowledge import (
            KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE,
            KnowledgeSourceAdapter,
            knowledge_source_capability_manifest,
        )
        from src.unified_source_index_sqlite import SQLiteUnifiedSourceIndexStore

        data_root = Path(DATA_DIR)
        app_key_path = Path(APP_KEY_FILE)
        if not app_key_path.exists():
            app_key_path.write_bytes(Fernet.generate_key())
        fixture_meta_path = data_root / "planning-knowledge-fixture.json"
        fixture_auth = AuthManager(str(data_root / "planning-knowledge-fixture-auth.json"))
        if not fixture_auth.is_configured:
            assert fixture_auth.create_user(
                "planning-fixture", "planning-fixture-password-83!", is_admin=True
            )
        fixture_subject = fixture_auth.subject_id_for_username("planning-fixture")
        if phase == "first":
            fixture_key = __import__("base64").urlsafe_b64decode(app_key_path.read_bytes())
            fixture_issuer = AuthenticatedPrincipalIssuer(
                issuer_id="harbour-knowledge-runtime",
                key_id="app-key-v1",
                secret=hmac.new(
                    fixture_key,
                    b"odysseus.harbour.knowledge.issuer.v1\0",
                    hashlib.sha256,
                ).digest(),
            )
            fixture_causal = CausalClaimStore(
                sqlite3.connect(data_root / "planning-knowledge-causal.sqlite"),
                initialize=True,
            )
            for index in range(2):
                fixture_causal.create_claim({
                    "schema": "odysseus.causal_claim.v1",
                    "claim_id": f"planning-fixture-{index}",
                    "owner": fixture_subject,
                    "policy": "reviewed-local",
                    "source_version": "sha256:" + str(index + 1) * 64,
                    "claim_version": 1,
                    "candidate_actor": "planning-fixture",
                    "evidence_refs": ["sha256:" + str(index + 3) * 64],
                })
            fixture_plan = prepare_current_knowledge_migration(
                causal_sources=(CausalMigrationSource(
                    fixture_subject, fixture_causal, fixture_subject,
                ),),
                owner_map={fixture_subject: fixture_subject},
                retention_by_scope={fixture_subject: {
                    "retention_class": "user_managed",
                    "deletion_mode": "tombstone_only",
                    "retain_until": None,
                    "grace_until": None,
                    "legal_hold": False,
                }},
                observed_at="2026-10-01T12:00:00Z",
            )
            fixture_staging = HarbourKnowledgeStagingDestinationService(
                data_root / "harbour-knowledge-staging",
                trusted_issuers=(fixture_issuer,),
            )
            fixture_principal = fixture_issuer.issue(
                subject_id=fixture_subject,
                owner_id=fixture_subject,
                auth_context_digest=_sha({
                    "migration_id": fixture_plan.migration_id,
                    "owner_id": fixture_subject,
                    "schema_id": _CONTEXT_SCHEMA,
                    "snapshot_digest": fixture_plan.source_manifest_digest,
                }),
            )
            fixture_completion = migrate_prepared_knowledge_migration(
                fixture_plan,
                staging=fixture_staging,
                principals={fixture_subject: fixture_principal},
            ).completion_reference
            fixture_staging.select_generation(
                fixture_completion,
                expected_selection_digest="sha256:" + "0" * 64,
            )
            fixture_store = fixture_staging.selected_runtime(fixture_subject)[4]
            fixture_writer = fixture_issuer.issue(
                subject_id=fixture_subject,
                owner_id=fixture_subject,
                auth_context_digest=_sha({"fixture": "planning"}),
            )
            fixture_store.create_object(
                fixture_writer,
                owner_id=fixture_subject,
                knowledge_id="planning-fixture-note",
                knowledge_kind="note",
                payload_bytes=b"planning fixture evidence",
                payload_schema_id="harbour.note.v1",
                payload_media_type="text/plain",
                classification="private",
                idempotency_key="planning-fixture-create",
            )
            fixture_owner_scope = OwnerScope.for_subject_id(fixture_subject).value
            fixture_native = KnowledgeSourceAdapter(
                store=fixture_store,
                principal=fixture_writer,
                owner_id=fixture_subject,
                cursor_signing_key=b"p" * 32,
                classification_ceiling=Classification.SENSITIVE,
            )
            fixture_sources = fixture_native.discover(
                AdapterScope(fixture_owner_scope, Classification.SENSITIVE),
                cursor="",
                limit=100,
                time_budget_ms=5000,
            ).items
            fixture_projected = []
            for item in fixture_sources:
                source = item.source
                version = fixture_native.observe_version(
                    RecordRef(RecordKind.SOURCE, source.source_id)
                )
                chunks = fixture_native.extract(
                    version,
                    ExtractionProfile(
                        KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE, 100, 100000, 5000
                    ),
                ).chunks
                fixture_projected.extend((source, version, *chunks))
            fixture_source_ids = tuple(item.source.source_id for item in fixture_sources)
            fixture_usi = SQLiteUnifiedSourceIndexStore(data_root / "usi.sqlite")
            fixture_write = fixture_usi.begin_write(fixture_usi.current_snapshot())
            for record in fixture_projected:
                fixture_write.put(record)
            fixture_write.commit()
            fixture_staging.close()
            fixture_meta_path.write_text(
                json.dumps({
                    "subject": fixture_subject,
                    "source_ids": fixture_source_ids,
                }, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
        fixture_metadata = json.loads(fixture_meta_path.read_text(encoding="utf-8"))
        os.environ.update({
            "ODYSSEUS_USI_RUNTIME_ENABLED": "true",
            "ODYSSEUS_USI_RUNTIME_MODE": "read_only",
            "ODYSSEUS_USI_SELECTED_GENERATION": knowledge_source_capability_manifest().generation_ref,
            "ODYSSEUS_USI_ALLOWED_OWNERS": fixture_metadata["subject"],
            "ODYSSEUS_USI_ALLOWED_SOURCES": ",".join(fixture_metadata["source_ids"]),
            "ODYSSEUS_USI_ALLOWED_DOMAINS": "native_knowledge",
            "ODYSSEUS_USI_SQLITE_PATH": "usi.sqlite",
            "ODYSSEUS_USI_QUERY_MAX_RESULTS": "8",
            "ODYSSEUS_USI_QUERY_TIMEOUT_SECONDS": "5",
        })
    import app as application
    from src.planning_roadmap_migration import build_h1r03_migration_manifest, canonical_json_bytes
    from src.planning_durable_context_source import DEFAULT_DURABLE_PLANNING_CONTEXT_POLICY

    stage = "app_lifespan_and_routes"
    checkpoint("app_import_after")
    original_shutdown_event = application._shutdown_event

    async def observed_shutdown_event(*args, **kwargs):
        checkpoint("shutdown_event_before")
        try:
            return await original_shutdown_event(*args, **kwargs)
        finally:
            checkpoint("shutdown_event_after")

    application._shutdown_event = observed_shutdown_event

    import src.recent_changes as recent_changes
    original_recent_changes_snapshot = recent_changes.maybe_record_startup_snapshot

    def observed_recent_changes_snapshot(*args, **kwargs):
        checkpoint("recent_changes_before")
        try:
            return original_recent_changes_snapshot(*args, **kwargs)
        finally:
            checkpoint("recent_changes_after")

    recent_changes.maybe_record_startup_snapshot = observed_recent_changes_snapshot
    repo_root = Path(os.environ["ODYSSEUS_TEST_REPO_ROOT"])
    migration_path = "/api/planning/durable/migrations/h1r03"
    checks = {
        "offline_mode": application.STARTUP_OFFLINE is True,
        "rag_unavailable": application.rag_manager is None,
        "memory_vector_unavailable": application.memory_vector is None,
        "phase": phase,
    }
    planning_runtime = getattr(application._planning_definition_router, "_planning_mcp_runtime", None)
    context_bundle = getattr(application, "context_sources", None)
    context_status = getattr(application.app.state, "context_source_status", {})
    context_routes_present = any(
        route.path.startswith("/api/context/") for route in application.app.routes
    )
    if phase != "no_issuer":
        planning_source = getattr(context_bundle.planning, "_durable_source", None)
        checks.update({
            "planning_runtime_identity": planning_source._runtime is planning_runtime,
            "planning_auth_identity": planning_source._auth_manager is application.auth_manager,
            "planning_store_identity": planning_source._store is planning_runtime.durable_store,
            "planning_issuer_identity": planning_source._issuer is planning_runtime.issuer,
            "planning_policy_identity": planning_source._policy is DEFAULT_DURABLE_PLANNING_CONTEXT_POLICY,
            "no_approved_legacy_source": context_bundle.planning._approved_source_service is None,
            "actual_confirmation_producer": (
                application.app.state.private_context_confirmation._producer is context_bundle.producer
            ),
            "context_sources_configured": context_status.get("configured") is True,
            "context_sources_runtime_verified": context_status.get("runtime_verified") is False,
            "context_routes_present": context_routes_present,
        })

    checkpoint("lifespan_entry_before")
    with TestClient(application.app) as client:
        checkpoint("lifespan_entry_after")
        if phase == "first":
            checkpoint("migration_status_before")
            anonymous = client.get(migration_path + "/status")
            checkpoint("migration_status_after")
            checkpoint("login_before")
            setup = client.post(
                "/api/auth/setup",
                json={"username": "fuzzy", "password": "offline-startup-test-password-83!"},
            )
            login = client.post(
                "/api/auth/login",
                json={"username": "fuzzy", "password": "offline-startup-test-password-83!"},
            )
            checkpoint("login_after")
            auth_status = client.get("/api/auth/status")
            checks.update({
                "anonymous_denied": anonymous.status_code in {401, 403},
                "setup_succeeded": setup.status_code == 200,
                "login_succeeded": login.status_code == 200 and login.json().get("username") == "fuzzy",
                "authenticated_cookie_session": (
                    auth_status.status_code == 200
                    and auth_status.json().get("authenticated") is True
                    and auth_status.json().get("username") == "fuzzy"
                    and bool(client.cookies)
                ),
            })

            manifest = build_h1r03_migration_manifest(repo_root)
            census_digest = "sha256:" + hashlib.sha256(
                canonical_json_bytes(manifest["structural_census"])
            ).hexdigest()
            prepare_body = {
                "expected_batch_id": manifest["batch_id"],
                "expected_manifest_digest": manifest["manifest_digest"],
                "expected_structural_census_digest": census_digest,
            }
            csrf_denied = client.post(migration_path + "/prepare", json=prepare_body)
            after_denial = client.get(migration_path + "/status")
            checks["csrf_denied_without_origin"] = csrf_denied.status_code == 403
            checks["csrf_denial_did_not_prepare"] = (
                after_denial.status_code == 200 and after_denial.json().get("state") == "not_started"
            )

            origin = {"Origin": "http://testserver"}
            prepared = client.post(migration_path + "/prepare", headers=origin, json=prepare_body)
            prepared_value = prepared.json() if prepared.status_code == 200 else {}
            prepare_replay = client.post(migration_path + "/prepare", headers=origin, json=prepare_body)
            checks["prepare_succeeded"] = prepared.status_code == 200
            checks["prepare_exact_replay"] = (
                prepare_replay.status_code == 200 and prepare_replay.json() == prepared_value
            )

            accept_body = {
                "batch_id": manifest["batch_id"],
                "expected_manifest_digest": manifest["manifest_digest"],
                "expected_structural_census_digest": census_digest,
                "prior_receipt_id": prepared_value.get("receipt", {}).get("receipt_id", ""),
            }
            accepted = client.post(migration_path + "/accept", headers=origin, json=accept_body)
            accepted_value = accepted.json() if accepted.status_code == 200 else {}
            checkpoint("migration_status_before")
            status = client.get(migration_path + "/status")
            checkpoint("migration_status_after")
            status_value = status.json() if status.status_code == 200 else {}
            checks["accept_succeeded"] = (
                accepted.status_code == 200
                and accepted_value.get("receipt", {}).get("after_state") == "accepted"
                and status_value.get("state") == "accepted"
            )

            checkpoint("planning_list_before")
            projects = client.get("/api/planning/projects")
            roadmaps = client.get("/api/planning/projects/harbour-one/roadmaps")
            checkpoint("planning_list_after")
            project_rows = projects.json().get("items", []) if projects.status_code == 200 else []
            harbour = next((row for row in project_rows if row.get("project_id") == "harbour-one"), {})
            roadmap_rows = roadmaps.json().get("items", []) if roadmaps.status_code == 200 else []
            roadmap_ids = sorted(item["roadmap_id"] for item in manifest["source_items"])
            checks["default_planning_projection"] = (
                harbour.get("roadmap_count") == 6
                and len(roadmap_rows) == 6
                and sorted(row.get("roadmap_id") for row in roadmap_rows) == roadmap_ids
            )
            advertised = status_value.get("exports", [])
            checks["exact_six_exports_advertised"] = (
                status_value.get("state") == "accepted"
                and sorted(row.get("roadmap_id", "") for row in advertised) == roadmap_ids
                and all(row.get("formats") == ["json", "md"] for row in advertised)
            )

            inventory_response = client.get(migration_path + "/inventory")
            inventory_value = inventory_response.json() if inventory_response.status_code == 200 else {}
            inventory_sources = inventory_value.get("sources", [])
            primary_paths = sorted(item["path"] for item in manifest["source_items"])
            census_markdown_paths = sorted({
                row["path"] for row in manifest["structural_census"]
                if str(row.get("path", "")).lower().endswith((".md", ".markdown"))
            })
            census_markdown_sha256 = hashlib.sha256(canonical_json_bytes(census_markdown_paths)).hexdigest()
            primary_paths_sha256 = hashlib.sha256(canonical_json_bytes(primary_paths)).hexdigest()
            markdown_sources = [item for item in inventory_sources
                                if str(item.get("path", "")).lower().endswith((".md", ".markdown"))]
            historical_markdown_paths = {
                item.get("path") for item in markdown_sources
                if item.get("authority_disposition") == "historical_evidence"
            }
            disposition_counts = inventory_value.get("migration_projection", {}).get(
                "authority_disposition_counts", {}
            )
            checks["inventory_exact_migration_projection"] = (
                inventory_response.status_code == 200
                and inventory_value.get("read_only") is True
                and inventory_value.get("writes_supported") is False
                and inventory_value.get("migration_projection", {}).get("migrated_read_only_count") == 6
                and {item.get("path") for item in inventory_sources
                     if item.get("authority_disposition") == "migrated_read_only"} == set(primary_paths)
                and len([item for item in inventory_sources
                         if item.get("authority_disposition") == "migrated_read_only"]) == 6
                and all(item.get("authority_disposition") == "historical_evidence"
                        for item in markdown_sources)
                and bool(census_markdown_paths)
                and set(census_markdown_paths) <= historical_markdown_paths
                and disposition_counts.get("active_import_candidate", 0) == 0
                and disposition_counts.get("current_source_of_truth", 0) == 0
                and disposition_counts.get("migrated_read_only", 0) == 6
            )

            roadmap_roundtrips = []
            for roadmap_id in roadmap_ids:
                detail_path = f"/api/planning/projects/harbour-one/roadmaps/{roadmap_id}"
                checkpoint("planning_detail_before")
                detail = client.get(detail_path)
                checkpoint("planning_detail_after")
                detail_value = detail.json() if detail.status_code == 200 else {}
                projection_sha256 = hashlib.sha256(canonical_json_bytes(detail_value)).hexdigest()
                detail_ok = (
                    detail.status_code == 200
                    and detail_value.get("origin", {}).get("source") == "h1r03_durable_migration_accepted"
                )

                checkpoint("json_export_before")
                exported = client.get(migration_path + f"/exports/{roadmap_id}.json")
                checkpoint("json_export_after")
                checkpoint("markdown_export_before")
                markdown = client.get(migration_path + f"/exports/{roadmap_id}.md")
                checkpoint("markdown_export_after")
                export_value = json.loads(exported.content) if exported.status_code == 200 else {}
                export_sha256 = hashlib.sha256(exported.content).hexdigest() if exported.status_code == 200 else ""
                json_ok = exported.status_code == 200 and exported.content == canonical_json_bytes(export_value)
                markdown_ok = (
                    markdown.status_code == 200
                    and markdown.text.startswith("<!-- GENERATED READ-ONLY PLANNING VIEW; DO NOT EDIT -->")
                )

                durable_path = f"/api/planning/durable/projects/harbour-one/roadmaps/{roadmap_id}"
                head_response = client.get(durable_path + "/head")
                head_value = head_response.json() if head_response.status_code == 200 else {}
                revision_id = head_value.get("current_revision_id", "")
                revision_response = client.get(durable_path + f"/revisions/{revision_id}") if revision_id else None
                revision_value = revision_response.json() if revision_response is not None and revision_response.status_code == 200 else {}
                payload_b64 = revision_value.get("payload_b64", "")
                head_ok = (
                    head_response.status_code == 200
                    and bool(payload_b64)
                    and base64.b64decode(payload_b64, validate=True) == exported.content
                )
                checks[f"roadmap_roundtrip_{len(roadmap_roundtrips)}"] = (
                    detail_ok and json_ok and markdown_ok and head_ok
                )
                roadmap_roundtrips.append({
                    "roadmap_id": roadmap_id,
                    "content_hash": export_value.get("roadmaps", [{}])[0].get("content_hash", ""),
                    "export_sha256": export_sha256,
                    "projection_sha256": projection_sha256,
                    "revision_id": revision_id,
                })
            checks["canonical_json_export"] = len(roadmap_roundtrips) == 6 and all(
                checks[f"roadmap_roundtrip_{index}"] for index in range(6)
            )
            checks["readonly_markdown_export"] = checks["canonical_json_export"]
            checks["canonical_head_matches_export"] = checks["canonical_json_export"]
            checks.update({"roadmap_roundtrips": roadmap_roundtrips})
            checks["census_markdown_count"] = len(census_markdown_paths)
            checks["census_markdown_sha256"] = census_markdown_sha256
            checks["primary_path_count"] = len(primary_paths)
            checks["primary_paths_sha256"] = primary_paths_sha256

            receipts = status_value.get("receipts", [])
            accepted_receipt_id = accepted_value.get("receipt", {}).get("receipt_id", "")
            checks["accepted_receipt_persisted"] = (
                bool(accepted_receipt_id)
                and any(row.get("receipt_id") == accepted_receipt_id and row.get("phase") == "accept"
                        for row in receipts)
            )
            checks.update({
                "batch_id": manifest["batch_id"],
                "manifest_digest": manifest["manifest_digest"],
                "accepted_receipt_id": accepted_receipt_id,
                "roadmap_id": roadmap_roundtrips[0]["roadmap_id"] if roadmap_roundtrips else "",
                "export_sha256": roadmap_roundtrips[0]["export_sha256"] if roadmap_roundtrips else "",
                "roadmap_roundtrips": roadmap_roundtrips,
            })
        elif phase == "restart":
            checkpoint("login_before")
            login = client.post(
                "/api/auth/login",
                json={"username": "fuzzy", "password": "offline-startup-test-password-83!"},
            )
            checkpoint("login_after")
            auth_status = client.get("/api/auth/status")
            checkpoint("migration_status_before")
            status = client.get(migration_path + "/status")
            checkpoint("migration_status_after")
            status_value = status.json() if status.status_code == 200 else {}
            checkpoint("planning_list_before")
            projects = client.get("/api/planning/projects")
            roadmaps = client.get("/api/planning/projects/harbour-one/roadmaps")
            checkpoint("planning_list_after")
            project_rows = projects.json().get("items", []) if projects.status_code == 200 else []
            harbour = next((row for row in project_rows if row.get("project_id") == "harbour-one"), {})
            roadmap_rows = roadmaps.json().get("items", []) if roadmaps.status_code == 200 else []
            expected_roundtrips = json.loads(os.environ["ODYSSEUS_TEST_ROADMAP_ROUNDTRIPS"])
            expected_roadmap_ids = sorted(item["roadmap_id"] for item in expected_roundtrips)
            roadmap_ids = sorted(row.get("roadmap_id", "") for row in roadmap_rows)
            restart_manifest = build_h1r03_migration_manifest(repo_root)
            primary_paths = sorted(item["path"] for item in restart_manifest["source_items"])
            census_markdown_paths = sorted({
                row["path"] for row in restart_manifest["structural_census"]
                if str(row.get("path", "")).lower().endswith((".md", ".markdown"))
            })
            census_markdown_sha256 = hashlib.sha256(canonical_json_bytes(census_markdown_paths)).hexdigest()
            primary_paths_sha256 = hashlib.sha256(canonical_json_bytes(primary_paths)).hexdigest()
            checks["restart_manifest_inventory_identity"] = (
                bool(census_markdown_paths)
                and len(census_markdown_paths) == int(os.environ["ODYSSEUS_TEST_CENSUS_MARKDOWN_COUNT"])
                and census_markdown_sha256 == os.environ["ODYSSEUS_TEST_CENSUS_MARKDOWN_SHA256"]
                and len(primary_paths) == int(os.environ["ODYSSEUS_TEST_PRIMARY_PATH_COUNT"])
                and primary_paths_sha256 == os.environ["ODYSSEUS_TEST_PRIMARY_PATHS_SHA256"]
            )
            receipts = status_value.get("receipts", [])
            advertised = status_value.get("exports", [])
            checks["exact_six_exports_advertised_after_restart"] = (
                sorted(row.get("roadmap_id", "") for row in advertised) == expected_roadmap_ids
                and all(row.get("formats") == ["json", "md"] for row in advertised)
            )
            inventory_response = client.get(migration_path + "/inventory")
            inventory_value = inventory_response.json() if inventory_response.status_code == 200 else {}
            inventory_sources = inventory_value.get("sources", [])
            markdown_sources = [item for item in inventory_sources
                                if str(item.get("path", "")).lower().endswith((".md", ".markdown"))]
            historical_markdown_paths = {
                item.get("path") for item in markdown_sources
                if item.get("authority_disposition") == "historical_evidence"
            }
            disposition_counts = inventory_value.get("migration_projection", {}).get(
                "authority_disposition_counts", {}
            )
            checks["inventory_exact_migration_projection_after_restart"] = (
                inventory_response.status_code == 200
                and inventory_value.get("read_only") is True
                and inventory_value.get("writes_supported") is False
                and inventory_value.get("migration_projection", {}).get("migrated_read_only_count") == 6
                and {item.get("path") for item in inventory_sources
                     if item.get("authority_disposition") == "migrated_read_only"}
                    == set(primary_paths)
                and len([item for item in inventory_sources
                         if item.get("authority_disposition") == "migrated_read_only"]) == 6
                and all(item.get("authority_disposition") == "historical_evidence"
                        for item in markdown_sources)
                and bool(census_markdown_paths)
                and set(census_markdown_paths) <= historical_markdown_paths
                and disposition_counts.get("active_import_candidate", 0) == 0
                and disposition_counts.get("current_source_of_truth", 0) == 0
                and disposition_counts.get("migrated_read_only", 0) == 6
            )
            roadmap_roundtrips = []
            for index, roadmap_id in enumerate(expected_roadmap_ids):
                expected = next(item for item in expected_roundtrips if item["roadmap_id"] == roadmap_id)
                checkpoint("planning_detail_before")
                detail = client.get(f"/api/planning/projects/harbour-one/roadmaps/{roadmap_id}")
                checkpoint("planning_detail_after")
                detail_value = detail.json() if detail.status_code == 200 else {}
                projection_sha256 = hashlib.sha256(canonical_json_bytes(detail_value)).hexdigest()
                checkpoint("json_export_before")
                exported = client.get(migration_path + f"/exports/{roadmap_id}.json")
                checkpoint("json_export_after")
                checkpoint("markdown_export_before")
                markdown = client.get(migration_path + f"/exports/{roadmap_id}.md")
                checkpoint("markdown_export_after")
                export_value = json.loads(exported.content) if exported.status_code == 200 else {}
                export_sha256 = hashlib.sha256(exported.content).hexdigest() if exported.status_code == 200 else ""
                markdown_ok = (
                    markdown.status_code == 200
                    and markdown.text.startswith("<!-- GENERATED READ-ONLY PLANNING VIEW; DO NOT EDIT -->")
                )
                durable_path = f"/api/planning/durable/projects/harbour-one/roadmaps/{roadmap_id}"
                head_response = client.get(durable_path + "/head")
                head_value = head_response.json() if head_response.status_code == 200 else {}
                revision_id = head_value.get("current_revision_id", "")
                revision_response = client.get(durable_path + f"/revisions/{revision_id}") if revision_id else None
                revision_value = revision_response.json() if revision_response is not None and revision_response.status_code == 200 else {}
                payload_b64 = revision_value.get("payload_b64", "")
                roundtrip_ok = (
                    detail.status_code == 200
                    and detail_value.get("origin", {}).get("source") == "h1r03_durable_migration_accepted"
                    and exported.status_code == 200
                    and exported.content == canonical_json_bytes(export_value)
                    and hashlib.sha256(exported.content).hexdigest() == expected["export_sha256"]
                    and export_value.get("roadmaps", [{}])[0].get("content_hash") == expected["content_hash"]
                    and projection_sha256 == expected["projection_sha256"]
                    and markdown_ok
                    and head_response.status_code == 200
                    and revision_id == expected["revision_id"]
                    and bool(payload_b64)
                    and base64.b64decode(payload_b64, validate=True) == exported.content
                )
                checks[f"roadmap_restart_roundtrip_{index}"] = roundtrip_ok
                roadmap_roundtrips.append({
                    "roadmap_id": roadmap_id,
                    "content_hash": export_value.get("roadmaps", [{}])[0].get("content_hash", ""),
                    "export_sha256": export_sha256,
                    "projection_sha256": projection_sha256,
                    "revision_id": revision_id,
                })
            checks.update({
                "login_existing_user": login.status_code == 200 and login.json().get("username") == "fuzzy",
                "reauthenticated_cookie_session": (
                    auth_status.status_code == 200
                    and auth_status.json().get("authenticated") is True
                    and auth_status.json().get("username") == "fuzzy"
                    and bool(client.cookies)
                ),
                "accepted_state_survived_restart": (
                    status_value.get("state") == "accepted"
                    and status_value.get("batch_id") == os.environ["ODYSSEUS_TEST_BATCH_ID"]
                    and status_value.get("manifest_digest") == os.environ["ODYSSEUS_TEST_MANIFEST_DIGEST"]
                    and any(row.get("receipt_id") == os.environ["ODYSSEUS_TEST_RECEIPT_ID"]
                            and row.get("phase") == "accept" for row in receipts)
                ),
                "default_planning_survived_restart": (
                    harbour.get("roadmap_count") == 6
                    and roadmap_ids == expected_roadmap_ids
                ),
                "canonical_export_survived_restart": len(roadmap_roundtrips) == 6 and all(
                    checks[f"roadmap_restart_roundtrip_{index}"] for index in range(6)
                ),
                "readonly_markdown_survived_restart": len(roadmap_roundtrips) == 6 and all(
                    checks[f"roadmap_restart_roundtrip_{index}"] for index in range(6)
                ),
                "batch_id": status_value.get("batch_id", ""),
                "manifest_digest": status_value.get("manifest_digest", ""),
                "accepted_receipt_id": os.environ["ODYSSEUS_TEST_RECEIPT_ID"],
                "roadmap_id": roadmap_roundtrips[0]["roadmap_id"] if roadmap_roundtrips else "",
                "export_sha256": roadmap_roundtrips[0]["export_sha256"] if roadmap_roundtrips else "",
                "roadmap_roundtrips": roadmap_roundtrips,
                "census_markdown_count": len(census_markdown_paths),
                "census_markdown_sha256": census_markdown_sha256,
                "primary_path_count": len(primary_paths),
                "primary_paths_sha256": primary_paths_sha256,
            })
        elif phase == "no_issuer":
            checks.update({
                "no_issuer_context_sources_absent": context_bundle is None,
                "no_issuer_confirmation_producer_absent": (
                    application.app.state.private_context_confirmation._producer is None
                ),
                "no_issuer_configured_false": context_status.get("configured") is False,
                "no_issuer_runtime_verified_false": context_status.get("runtime_verified") is False,
                "no_issuer_context_routes_absent": not context_routes_present,
            })
        else:
            raise RuntimeError("invalid_test_phase")

        checkpoint("lifespan_exit_before")
    checkpoint("lifespan_exit_after")
    checks["network_attempted"] = attempted["network"]
    checks["process_attempted"] = attempted["process"]
    checks["platform_probe"] = attempted["platform_probe"]
    checks["local_git_runs"] = attempted["local_git_runs"]
    checks["ok"] = (
        all(value is True for key, value in checks.items()
            if key not in {"phase", "batch_id", "manifest_digest", "accepted_receipt_id", "roadmap_id", "export_sha256", "roadmap_roundtrips", "census_markdown_count", "census_markdown_sha256", "primary_path_count", "primary_paths_sha256",
                           "network_attempted", "process_attempted", "platform_probe", "local_git_runs"})
        and not attempted["network"]
        and not attempted["process"]
    )
    checkpoint("final_result_before")
    write_result(checks)
    checkpoint("final_result_after")
    raise SystemExit(0 if checks["ok"] else 2)
except Exception as exc:
    checkpoint("child_exception")
    write_result({
        "ok": False,
        "phase": phase,
        "stage": stage,
        "error_class": type(exc).__name__,
        "network_attempted": attempted["network"],
        "process_attempted": attempted["process"],
        "platform_probe": attempted["platform_probe"],
        "local_git_runs": attempted["local_git_runs"],
    })
    raise SystemExit(1)
'''


_CHECKPOINT_PHASES = {"first", "restart", "no_issuer"}
_CHECKPOINT_STAGES = {
    "app_import_before",
    "app_import_after",
    "lifespan_entry_before",
    "lifespan_entry_after",
    "login_before",
    "login_after",
    "migration_status_before",
    "migration_status_after",
    "planning_list_before",
    "planning_list_after",
    "planning_detail_before",
    "planning_detail_after",
    "json_export_before",
    "json_export_after",
    "markdown_export_before",
    "markdown_export_after",
    "recent_changes_before",
    "recent_changes_after",
    "shutdown_event_before",
    "shutdown_event_after",
    "lifespan_exit_before",
    "lifespan_exit_after",
    "final_result_before",
    "final_result_after",
    "child_exception",
}
_CHECKPOINT_MAX_ROWS = 64
_CHECKPOINT_MAX_ROW_BYTES = 80
_CHECKPOINT_MAX_BYTES = _CHECKPOINT_MAX_ROWS * _CHECKPOINT_MAX_ROW_BYTES


def _validated_checkpoint_sequence(
    data: bytes,
    expected_phase: str,
) -> list[dict[str, object]] | None:
    if expected_phase not in _CHECKPOINT_PHASES or not data or len(data) > _CHECKPOINT_MAX_BYTES:
        return None
    try:
        lines = data.decode("utf-8").splitlines()
        if not lines or len(lines) > _CHECKPOINT_MAX_ROWS:
            return None
        sequence: list[dict[str, object]] = []
        for line in lines:
            item = json.loads(line)
            if (
                not isinstance(item, dict)
                or set(item) != {"phase", "stage", "elapsed_ms"}
                or item["phase"] != expected_phase
                or item["phase"] not in _CHECKPOINT_PHASES
                or item["stage"] not in _CHECKPOINT_STAGES
                or type(item["elapsed_ms"]) is not int
                or not 0 <= item["elapsed_ms"] <= 180000
            ):
                return None
            if sequence and item["elapsed_ms"] < sequence[-1]["elapsed_ms"]:
                return None
            sequence.append(item)
        return sequence
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None


def _validated_checkpoint(data: bytes, expected_phase: str) -> dict[str, object] | None:
    sequence = _validated_checkpoint_sequence(data, expected_phase)
    return sequence[-1] if sequence else None


def _read_checkpoint(path: Path, expected_phase: str) -> dict[str, object] | None:
    try:
        with path.open("rb") as stream:
            data = stream.read(_CHECKPOINT_MAX_BYTES + 1)
    except OSError:
        return None
    return _validated_checkpoint(data, expected_phase)


def _read_checkpoint_sequence(
    path: Path,
    expected_phase: str,
) -> list[dict[str, object]] | None:
    try:
        with path.open("rb") as stream:
            data = stream.read(_CHECKPOINT_MAX_BYTES + 1)
    except OSError:
        return None
    return _validated_checkpoint_sequence(data, expected_phase)


def test_checkpoint_projection_is_fixed_schema_and_bounded(tmp_path: Path) -> None:
    valid = b'{"elapsed_ms":1200,"phase":"restart","stage":"login_after"}\n'
    schema_row_sizes = [
        len((
            json.dumps(
                {"elapsed_ms": 180000, "phase": phase, "stage": stage},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8"))
        for phase in _CHECKPOINT_PHASES
        for stage in _CHECKPOINT_STAGES
    ]
    assert max(schema_row_sizes) <= _CHECKPOINT_MAX_ROW_BYTES
    assert _validated_checkpoint(valid, "restart") == {
        "elapsed_ms": 1200,
        "phase": "restart",
        "stage": "login_after",
    }
    assert _validated_checkpoint(valid, "first") is None
    assert _validated_checkpoint(valid.replace(b"login_after", b"provider_text"), "restart") is None
    assert _validated_checkpoint(valid.replace(b"1200", b"true"), "restart") is None
    assert _validated_checkpoint(valid + b"x" * _CHECKPOINT_MAX_BYTES, "restart") is None
    assert _validated_checkpoint_sequence(valid * _CHECKPOINT_MAX_ROWS, "restart") is not None
    assert _validated_checkpoint_sequence(valid * (_CHECKPOINT_MAX_ROWS + 1), "restart") is None
    byte_limit_row = valid[:-1] + b" " * (_CHECKPOINT_MAX_BYTES - len(valid)) + b"\n"
    assert len(byte_limit_row) == _CHECKPOINT_MAX_BYTES
    assert _validated_checkpoint_sequence(byte_limit_row, "restart") is not None
    assert _validated_checkpoint_sequence(byte_limit_row + b" ", "restart") is None
    assert _validated_checkpoint_sequence(
        valid + b'{"elapsed_ms":1200,"phase":"restart","stage":"shutdown_event_after"}\n',
        "restart",
    ) is not None
    assert _validated_checkpoint_sequence(
        valid.replace(b"1200", b"1201") + valid,
        "restart",
    ) is None


def _assert_recent_changes_finishes_before_lifespan_exit(
    tmp_path: Path,
    phase: str,
) -> None:
    sequence = _read_checkpoint_sequence(tmp_path / f"{phase}-checkpoint.jsonl", phase)
    assert sequence is not None
    stages = [item["stage"] for item in sequence]
    recent_done = stages.index("recent_changes_after")
    shutdown_done = stages.index("shutdown_event_after")
    lifespan_done = stages.index("lifespan_exit_after")
    assert recent_done < shutdown_done < lifespan_done
    assert (
        sequence[recent_done]["elapsed_ms"]
        <= sequence[shutdown_done]["elapsed_ms"]
        <= sequence[lifespan_done]["elapsed_ms"]
    )


def _child_environment(tmp_path: Path, repo_root: Path) -> dict[str, str]:
    data_root = tmp_path / "data"
    profile_root = tmp_path / "profile"
    plugin_root = tmp_path / "plugins"
    for path in (
        data_root,
        profile_root,
        profile_root / "roaming",
        profile_root / "local",
        tmp_path / "temp",
        tmp_path / "cache",
        plugin_root,
    ):
        path.mkdir(parents=True, exist_ok=True)

    env = {
        name: os.environ[name]
        for name in ("PATH", "SYSTEMROOT", "WINDIR")
        if name in os.environ
    }
    env.update({
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "HOME": str(profile_root),
        "USERPROFILE": str(profile_root),
        "APPDATA": str(profile_root / "roaming"),
        "LOCALAPPDATA": str(profile_root / "local"),
        "TEMP": str(tmp_path / "temp"),
        "TMP": str(tmp_path / "temp"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "HF_HOME": str(tmp_path / "cache" / "huggingface"),
        "TRANSFORMERS_CACHE": str(tmp_path / "cache" / "transformers"),
        "PYTHON_DOTENV_DISABLED": "1",
        "PYTHONPATH": str(repo_root),
        "ODYSSEUS_STARTUP_OFFLINE": "true",
        "ODYSSEUS_DATA_DIR": str(data_root),
        "DATABASE_URL": f"sqlite:///{(data_root / 'app.db').as_posix()}",
        "ODYSSEUS_PLUGINS_DIR": str(plugin_root),
        "ODYSSEUS_DISABLE_MCP": "1",
        "ODYSSEUS_DISABLE_USER_MCP": "1",
        "ODYSSEUS_INPROCESS_TASKS": "0",
        "ODYSSEUS_SECURITY_PUBLIC_EGRESS_DISCOVERY_DISABLED": "true",
        "ODYSSEUS_SECURITY_INCIDENT_DELIVERY_ENABLED": "false",
        "ODYSSEUS_TRANSCRIPTION_ENABLED": "false",
        "ODYSSEUS_LEGACY_STT_ENABLED": "false",
        "ODYSSEUS_USI_RUNTIME_ENABLED": "false",
        "ODYSSEUS_USI_RUNTIME_MODE": "disabled",
        "ODYSSEUS_PLANNING_ISSUER_SECRET_B64": "eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg=",
        "ODYSSEUS_PLANNING_ISSUER_KEY_ID": "offline-test-key",
        "AUTH_ENABLED": "true",
        "LOCALHOST_BYPASS": "false",
        "SECURE_COOKIES": "false",
        "ODYSSEUS_TEST_REPO_ROOT": str(repo_root),
    })
    return env


def _run_child(tmp_path: Path, repo_root: Path, phase: str, **extra_env: str) -> dict[str, object]:
    env = _child_environment(tmp_path, repo_root)
    result_path = tmp_path / f"{phase}-result.json"
    checkpoint_path = tmp_path / f"{phase}-checkpoint.jsonl"
    child_path = tmp_path / f"{phase}-child.py"
    child_path.write_bytes(_CHILD.encode("utf-8"))
    env.update(extra_env)
    env["ODYSSEUS_TEST_PHASE"] = phase
    env["ODYSSEUS_TEST_RESULT_FILE"] = str(result_path)
    env["ODYSSEUS_TEST_CHECKPOINT_FILE"] = str(checkpoint_path)
    try:
        result = subprocess.run(
            [sys.executable, "-X", "utf8", str(child_path)],
            cwd=repo_root,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired:
        checkpoint = _read_checkpoint(checkpoint_path, phase)
        if checkpoint is None:
            raise AssertionError(f"offline full-app {phase} child timed out; checkpoint unavailable or invalid") from None
        raise AssertionError(
            f"offline full-app {phase} child timed out at {checkpoint['stage']} "
            f"after {checkpoint['elapsed_ms']} ms"
        ) from None

    assert result_path.is_file(), f"offline full-app {phase} child returned no bounded projection"
    projection = json.loads(result_path.read_text(encoding="utf-8"))
    assert result.returncode == 0, json.dumps(projection, sort_keys=True)
    assert projection.get("ok") is True, json.dumps(projection, sort_keys=True)
    return projection


def test_real_app_migrates_h1r03_reads_exports_and_survives_restart(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    first = _run_child(tmp_path, repo_root, "first")
    _assert_recent_changes_finishes_before_lifespan_exit(tmp_path, "first")
    assert first["phase"] == "first"
    assert first["anonymous_denied"] is True
    assert first["authenticated_cookie_session"] is True
    assert first["planning_runtime_identity"] is True
    assert first["planning_auth_identity"] is True
    assert first["planning_store_identity"] is True
    assert first["planning_issuer_identity"] is True
    assert first["planning_policy_identity"] is True
    assert first["no_approved_legacy_source"] is True
    assert first["actual_confirmation_producer"] is True
    assert first["context_sources_configured"] is True
    assert first["context_sources_runtime_verified"] is True
    assert first["context_routes_present"] is True
    assert first["csrf_denied_without_origin"] is True
    assert first["csrf_denial_did_not_prepare"] is True
    assert first["prepare_succeeded"] is True
    assert first["prepare_exact_replay"] is True
    assert first["accept_succeeded"] is True
    assert first["default_planning_projection"] is True
    assert first["exact_six_exports_advertised"] is True
    assert first["inventory_exact_migration_projection"] is True
    assert first["canonical_json_export"] is True
    assert first["readonly_markdown_export"] is True
    assert first["canonical_head_matches_export"] is True
    assert first["accepted_receipt_persisted"] is True
    assert first["network_attempted"] is False
    assert first["process_attempted"] is False

    second = _run_child(
        tmp_path,
        repo_root,
        "restart",
        ODYSSEUS_TEST_BATCH_ID=str(first["batch_id"]),
        ODYSSEUS_TEST_MANIFEST_DIGEST=str(first["manifest_digest"]),
        ODYSSEUS_TEST_RECEIPT_ID=str(first["accepted_receipt_id"]),
        ODYSSEUS_TEST_ROADMAP_ROUNDTRIPS=json.dumps(
            first["roadmap_roundtrips"], sort_keys=True, separators=(",", ":")
        ),
        ODYSSEUS_TEST_CENSUS_MARKDOWN_COUNT=str(first["census_markdown_count"]),
        ODYSSEUS_TEST_CENSUS_MARKDOWN_SHA256=str(first["census_markdown_sha256"]),
        ODYSSEUS_TEST_PRIMARY_PATH_COUNT=str(first["primary_path_count"]),
        ODYSSEUS_TEST_PRIMARY_PATHS_SHA256=str(first["primary_paths_sha256"]),
    )
    _assert_recent_changes_finishes_before_lifespan_exit(tmp_path, "restart")
    assert second["login_existing_user"] is True
    assert second["reauthenticated_cookie_session"] is True
    assert second["planning_runtime_identity"] is True
    assert second["planning_auth_identity"] is True
    assert second["planning_store_identity"] is True
    assert second["planning_issuer_identity"] is True
    assert second["planning_policy_identity"] is True
    assert second["no_approved_legacy_source"] is True
    assert second["actual_confirmation_producer"] is True
    assert second["context_sources_configured"] is True
    assert second["context_sources_runtime_verified"] is True
    assert second["context_routes_present"] is True
    assert second["accepted_state_survived_restart"] is True
    assert second["default_planning_survived_restart"] is True
    assert second["exact_six_exports_advertised_after_restart"] is True
    assert second["inventory_exact_migration_projection_after_restart"] is True
    assert second["canonical_export_survived_restart"] is True
    assert second["readonly_markdown_survived_restart"] is True
    assert second["network_attempted"] is False
    assert second["process_attempted"] is False
    assert second["batch_id"] == first["batch_id"]
    assert second["manifest_digest"] == first["manifest_digest"]
    assert second["accepted_receipt_id"] == first["accepted_receipt_id"]
    assert second["export_sha256"] == first["export_sha256"]
    assert second["roadmap_roundtrips"] == first["roadmap_roundtrips"]
    assert second["restart_manifest_inventory_identity"] is True
    assert second["census_markdown_count"] == first["census_markdown_count"]
    assert second["census_markdown_sha256"] == first["census_markdown_sha256"]
    assert second["primary_path_count"] == first["primary_path_count"]
    assert second["primary_paths_sha256"] == first["primary_paths_sha256"]


def test_real_app_without_planning_issuer_fails_closed(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    result = _run_child(
        tmp_path,
        repo_root,
        "no_issuer",
        ODYSSEUS_PLANNING_ISSUER_SECRET_B64="",
        ODYSSEUS_PLANNING_ISSUER_KEY_ID="",
    )
    assert result["phase"] == "no_issuer"
    assert result["no_issuer_context_sources_absent"] is True
    assert result["no_issuer_confirmation_producer_absent"] is True
    assert result["no_issuer_configured_false"] is True
    assert result["no_issuer_runtime_verified_false"] is True
    assert result["no_issuer_context_routes_absent"] is True
    assert result["network_attempted"] is False
    assert result["process_attempted"] is False
