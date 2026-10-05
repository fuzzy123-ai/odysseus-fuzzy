"""Offline proof of the real app.py three source context-pack lifecycle."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


_CHILD = r'''
import base64
import hashlib
import hmac
import importlib
import json
import os
from pathlib import Path

_PHASES = frozenset({
    "child_start", "client_entered", "setup_complete", "login_complete",
    "forge_complete", "planning_complete", "knowledge_seeded", "app_reloaded",
    "metadata_complete", "challenge_complete", "ack_complete", "pack_complete",
    "negative_checks_complete", "client_exited", "result_written", "child_exception",
    "context_missing_prerequisites", "context_construction_unavailable",
})
_STATUS_KEYS = (
    "setup", "login", "forge_register", "forge_retain", "planning_prepare",
    "planning_accept", "metadata", "challenge", "denied_pack", "ack", "pack",
    "no_origin", "bearer", "route_count", "knowledge_ready", "planning_ready",
)

_last_success_phase = "child_start"

def phase(value):
    global _last_success_phase
    marker = value if value in _PHASES else "child_exception"
    if marker != "child_exception":
        _last_success_phase = marker
    Path(os.environ["ODYSSEUS_TEST_PHASE_FILE"]).write_text(
        json.dumps({"schema": "odysseus.actual_context_pack_app.phase.v1", "phase": marker}, sort_keys=True),
        encoding="utf-8",
    )

def write(statuses, ok, error="none", failure_line=0):
    if not ok and error == "none":
        error = "child_failure"
    safe = {
        name: statuses.get(name) if type(statuses.get(name)) is int and 100 <= statuses.get(name) <= 599 else 599
        for name in _STATUS_KEYS
    }
    Path(os.environ["ODYSSEUS_TEST_RESULT_FILE"]).write_text(
        json.dumps({
            "schema": "odysseus.actual_context_pack_app.result.v1", "ok": bool(ok),
            "status": safe, "last_success_phase": _last_success_phase,
            "failure_line": failure_line if type(failure_line) is int and 0 <= failure_line <= 1000 else 0,
            "error": error if error in {"none", "child_failure"} else "child_failure",
        }, sort_keys=True),
        encoding="utf-8",
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
        roadmap["title"] = "Actual context fixture " + roadmap["roadmap_id"]
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
    assert len(heads) == len(retained_heads) and len(heads) > 0
    for head in retained_heads:
        chain = [
            document for _record_owner, document, _revision_ref in retained_records
            if document["roadmaps"][0]["roadmap_id"] == head["roadmap_id"]
        ]
        assert len(chain) >= 2
        assert head["current_revision"] == head["approved_revision"]

phase("child_start")
statuses = {}
try:
    import sqlite3
    import re
    from src.planning_definition_contract import compute_roadmap_content_hash
    from src.planning_mcp_service import _contains_sensitive_value
    from fastapi.testclient import TestClient
    from cryptography.fernet import Fernet
    from src.constants import APP_KEY_FILE, DATA_DIR
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
    Path(APP_KEY_FILE).write_bytes(Fernet.generate_key())
    if os.name != "nt":
        Path(APP_KEY_FILE).chmod(0o600)
    import app as application
    from src.planning_roadmap_migration import build_h1r03_migration_manifest, canonical_json_bytes

    origin = {"Origin": "http://testserver"}
    password = "actual-context-pack-test-password-83!"
    session_id = "actual-three-source-context-chat"
    data_root = Path(os.environ["ODYSSEUS_DATA_ROOT"])
    phase("client_entered")
    with TestClient(application.app) as client:
        setup = client.post("/api/auth/setup", json={"username": "fuzzy", "password": password})
        statuses["setup"] = setup.status_code
        phase("setup_complete")
        login = client.post("/api/auth/login", json={"username": "fuzzy", "password": password})
        statuses["login"] = login.status_code
        cookie = client.cookies.get("odysseus_session")
        application.session_manager.create_session(
            session_id=session_id, name="actual context pack", endpoint_url="http://offline.invalid",
            model="offline-model", owner="fuzzy",
        )
        phase("login_complete")

        root = Path(os.environ["ODYSSEUS_TEST_REPO_ROOT"])
        manifest = build_h1r03_migration_manifest(root)
        census = "sha256:" + hashlib.sha256(canonical_json_bytes(manifest["structural_census"])).hexdigest()
        migration = "/api/planning/durable/migrations/h1r03"
        prepared = client.post(migration + "/prepare", headers=origin, json={
            "expected_batch_id": manifest["batch_id"], "expected_manifest_digest": manifest["manifest_digest"],
            "expected_structural_census_digest": census,
        })
        statuses["planning_prepare"] = prepared.status_code
        assert prepared.status_code == 200
        accepted = client.post(migration + "/accept", headers=origin, json={
            "batch_id": manifest["batch_id"], "expected_manifest_digest": manifest["manifest_digest"],
            "expected_structural_census_digest": census,
            "prior_receipt_id": prepared.json().get("receipt", {}).get("receipt_id", ""),
        })
        statuses["planning_accept"] = accepted.status_code
        assert accepted.status_code == 200
        phase("planning_complete")

        registered = client.post("/api/project-versioning/subject-projects", headers=origin, json={
            "confirmed": True, "title": "Actual context Forge", "path": os.environ["ODYSSEUS_TEST_FORGE_SOURCE"],
            "privacy_class": "public",
        })
        statuses["forge_register"] = registered.status_code
        repo_id = registered.json().get("repo_id", "")
        commit = os.environ["ODYSSEUS_TEST_FORGE_COMMIT"]
        retained = client.post(
            f"/api/project-versioning/{repo_id}/subject-versions", headers=origin,
            json={"confirmed": True, "commit_sha": commit},
        )
        statuses["forge_retain"] = retained.status_code
        version_id = retained.json().get("version_id", "")
        phase("forge_complete")
    assert statuses["setup"] == statuses["login"] == 200

    # Seed the app's own Knowledge storage for the subject created by /setup.
    from core.auth import AuthManager
    from src.constants import APP_KEY_FILE, DATA_DIR
    from src.causal_claim_store import CausalClaimStore
    from src.harbour_authority import AuthenticatedPrincipalIssuer
    from src.harbour_knowledge_migration import (
        CausalMigrationSource, migrate_prepared_knowledge_migration,
        prepare_authenticated_zero_census_knowledge_migration,
    )
    from src.harbour_knowledge_staging_destination import (
        HarbourKnowledgeStagingDestinationService, _CONTEXT_SCHEMA, _sha,
    )
    from src.harbour_knowledge_store import HarbourKnowledgeStore
    from src.unified_source_index_adapters import AdapterScope, ExtractionProfile
    from src.unified_source_index_contract import Classification, RecordKind, RecordRef
    from src.unified_source_index_owner_scope import OwnerScope
    from src.unified_source_index_sources.knowledge import (
        KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE, KnowledgeSourceAdapter,
        knowledge_source_capability_manifest,
    )
    from src.unified_source_index_sqlite import SQLiteUnifiedSourceIndexStore

    auth = application.auth_manager
    subject = auth.subject_id_for_username("fuzzy")
    key = base64.urlsafe_b64decode(Path(APP_KEY_FILE).read_bytes())
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="harbour-knowledge-runtime", key_id="app-key-v1",
        secret=hmac.new(key, b"odysseus.harbour.knowledge.issuer.v1\0", hashlib.sha256).digest(),
    )
    causal = CausalClaimStore(sqlite3.connect(data_root / "actual-context-causal.sqlite"), initialize=True)
    plan = prepare_authenticated_zero_census_knowledge_migration(
        causal_sources=(CausalMigrationSource(subject, causal, subject),),
        owner_map={subject: subject},
        retention_by_scope={subject: {
            "retention_class": "user_managed", "deletion_mode": "tombstone_only",
            "retain_until": None, "grace_until": None, "legal_hold": False,
        }},
        observed_at="2026-10-01T12:00:00Z",
    )
    staging = HarbourKnowledgeStagingDestinationService(
        data_root / "harbour-knowledge-staging", trusted_issuers=(issuer,),
    )
    principal = issuer.issue(subject_id=subject, owner_id=subject, auth_context_digest=_sha({
        "migration_id": plan.migration_id, "owner_id": subject, "schema_id": _CONTEXT_SCHEMA,
        "snapshot_digest": plan.source_manifest_digest,
    }))
    completion = migrate_prepared_knowledge_migration(
        plan, staging=staging, principals={subject: principal},
    ).completion_reference
    staging.select_generation(completion, expected_selection_digest="sha256:" + "0" * 64)
    store = staging.selected_runtime(subject)[4]
    writer = issuer.issue(subject_id=subject, owner_id=subject, auth_context_digest=_sha({"fixture": "actual-app"}))
    store.create_object(
        writer, owner_id=subject, knowledge_id="actual-app-private-note", knowledge_kind="note",
        payload_bytes=b"actual app owned knowledge context", payload_schema_id="harbour.note.v1",
        payload_media_type="text/plain", classification="private", idempotency_key="actual-app-private-note",
    )
    native = KnowledgeSourceAdapter(
        store=store, principal=writer, owner_id=subject,
        cursor_signing_key=b"c" * 32,
        classification_ceiling=Classification.SENSITIVE,
    )
    discovered = native.discover(
        AdapterScope(OwnerScope.for_subject_id(subject).value, Classification.SENSITIVE),
        cursor="", limit=100, time_budget_ms=5000,
    )
    records = []
    for item in discovered.items:
        source = item.source
        version = native.observe_version(RecordRef(RecordKind.SOURCE, source.source_id))
        chunks = native.extract(
            version, ExtractionProfile(KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE, 100, 100000, 5000),
        ).chunks
        records.extend((source, version, *chunks))
    usi = SQLiteUnifiedSourceIndexStore(data_root / "usi.sqlite")
    write_tx = usi.begin_write(usi.current_snapshot())
    for record in records:
        write_tx.put(record)
    write_tx.commit()
    staging.close()
    causal.close()
    os.environ.update({
        "ODYSSEUS_USI_RUNTIME_ENABLED": "true", "ODYSSEUS_USI_RUNTIME_MODE": "read_only",
        "ODYSSEUS_USI_SELECTED_GENERATION": knowledge_source_capability_manifest().generation_ref,
        "ODYSSEUS_USI_ALLOWED_OWNERS": subject,
        "ODYSSEUS_USI_ALLOWED_SOURCES": ",".join(item.source.source_id for item in discovered.items),
        "ODYSSEUS_USI_ALLOWED_DOMAINS": "native_knowledge", "ODYSSEUS_USI_SQLITE_PATH": "usi.sqlite",
        "ODYSSEUS_USI_QUERY_MAX_RESULTS": "8", "ODYSSEUS_USI_QUERY_TIMEOUT_SECONDS": "5",
    })
    phase("knowledge_seeded")
    import src.plugin_system as plugin_system
    plugin_system.MANAGER = None
    application = importlib.reload(application)
    phase("app_reloaded")

    from src.context_manifest_producer import CombinedContextManifestProducer
    from src.context_source_app import ContextSourceServices
    from src.harbour_knowledge_active_runtime import ActiveKnowledgeComposition, KnowledgeContextManifestProducer
    from src.planning_context_reader import PlanningContextManifestProducer
    from src.private_context_confirmation_app import (
        ContextSelection, CookieConfirmationAuthority, ForgeSelectionLocator,
        PrivateContextConfirmationApp, SourceSelection,
    )
    from src.planning_durable_context_source import DurablePlanningContextSource
    from src.unified_source_index_sources.knowledge import project_harbour_knowledge_index_records
    import routes.chat_routes as chat_routes

    paths = [route.path for route in application.app.routes]
    endpoint_counts = {path: paths.count(path) for path in (
        "/api/context/metadata", "/api/context/challenge",
        "/api/context/page", "/api/context/pack",
    )}
    statuses["route_count"] = 200 if all(value == 1 for value in endpoint_counts.values()) else 599
    bundle = application.context_sources
    planning_router = application._planning_definition_router
    forge_router = application._project_versioning_router
    runtime = planning_router._planning_mcp_runtime
    planning_source = bundle.planning._durable_source if bundle is not None else None
    composition = application.app.state.knowledge_composition
    statuses["knowledge_ready"] = 200 if type(composition) is ActiveKnowledgeComposition else 599
    statuses["planning_ready"] = 200 if type(application.durable_planning_source) is DurablePlanningContextSource else 599
    if bundle is None:
        reason = application.app.state.context_source_status.get("reason")
        if reason in {"missing_prerequisites", "construction_unavailable"}:
            phase("context_" + reason)
    assert type(bundle) is ContextSourceServices
    assert type(bundle.producer) is CombinedContextManifestProducer
    assert type(bundle.planning) is PlanningContextManifestProducer
    assert type(bundle.knowledge) is KnowledgeContextManifestProducer
    assert type(bundle.confirmation) is PrivateContextConfirmationApp
    assert type(planning_source) is DurablePlanningContextSource
    assert planning_source._runtime is runtime
    assert planning_source._auth_manager is application.auth_manager
    assert planning_source._store is runtime.durable_store
    assert planning_source._issuer is runtime.issuer
    assert application.app.state.context_sources is bundle
    assert bundle.confirmation._producer is bundle.producer
    assert bundle.producer._auth_manager is application.auth_manager
    assert bundle.producer._forge is bundle.forge
    assert bundle.producer._planning is bundle.planning
    assert bundle.producer._knowledge is bundle.knowledge
    assert bundle.confirmation is application.app.state.private_context_confirmation
    assert application.app.state.private_context_confirmation_auth_manager is application.auth_manager
    assert type(composition) is ActiveKnowledgeComposition
    assert bundle.knowledge._composition is composition
    assert composition._auth is application.auth_manager
    assert bundle.forge._local_forge is forge_router._forge_context_local_forge
    assert forge_router._forge_context_service._local_forge is forge_router._forge_context_local_forge
    with TestClient(application.app) as client:
        login = client.post("/api/auth/login", json={"username": "fuzzy", "password": password})
        cookie = client.cookies.get("odysseus_session")
        with CookieConfirmationAuthority(application.auth_manager).hold(cookie, session_id) as binding:
            fixture_principal = runtime.issuer.issue(
                subject_id=binding.owner_sid, owner_id="fuzzy",
                auth_context_digest="sha256:" + hashlib.sha256(b"actual-app-clean-planning-fixture").hexdigest(),
                ttl_seconds=3600,
            )
            _append_clean_descendants({"store": runtime.durable_store, "principal": fixture_principal}, binding)
            planning_target = bundle.planning._durable_source.discover(binding)[0]
            forge_meta = bundle.forge.capture_version_metadata(
                binding, repo_id, version_id, ("public.py",),
            )
            forge_target = SourceSelection(
                "forge", forge_meta.item.selection_ref, forge_meta.item.revision_ref,
                ForgeSelectionLocator(repo_id, version_id, "public.py"),
            )
            _subject, _store, _principal, *_ = application.app.state.knowledge_composition._context_runtime(binding)
            snapshot = _store.capture_index_snapshot(_principal, owner_id=subject)
            projection = project_harbour_knowledge_index_records(
                OwnerScope.for_subject_id(subject).value, snapshot.records[0],
            )
            knowledge_target = SourceSelection(
                "knowledge", projection.source.source_id, projection.source_version.source_version_id,
            )
        selection = [
            {"source": target.source, "selection_ref": target.selection_ref, "revision_ref": target.revision_ref}
            for target in (forge_target, knowledge_target, planning_target)
        ]
        selection[0]["forge_locator"] = {"repo_id": repo_id, "version_id": version_id, "path": "public.py"}
        expected_payloads = {
            0: b"print('actual')\n",
            1: b"actual app owned knowledge context",
            2: bundle.planning._durable_source.read_document(binding, planning_target),
        }
        pack_page_bytes = 65536
        expected_byteforms = {0: "git_blob", 1: "knowledge_payload", 2: "durable_revision_document"}
        expected_sha = {index: hashlib.sha256(payload).hexdigest() for index, payload in expected_payloads.items()}
        metadata_body = {"chat_session_id": session_id, "selection": selection}
        no_origin_bodies = {
            "/api/context/metadata": metadata_body,
            "/api/context/challenge": metadata_body,
            "/api/context/page": {
                "chat_session_id": session_id, "selection": selection, "target_index": 0,
                "expected_manifest_digest": "sha256:" + "0" * 64, "offset": 0, "limit": 4096,
            },
            "/api/context/pack": {
                "chat_session_id": session_id, "selection": selection, "page_bytes": pack_page_bytes, "cursor": None,
            },
        }
        no_origin_codes = [
            client.post(endpoint, json=body).status_code
            for endpoint, body in no_origin_bodies.items()
        ]
        bearer_responses = [
            client.post(endpoint, headers={**origin, "Authorization": "Bearer rejected"}, json=body)
            for endpoint, body in no_origin_bodies.items()
        ]
        bearer_codes = [response.status_code for response in bearer_responses]
        bearer_denied = bearer_codes == [403] * 4 and all(
            response.json() == {"error": {"code": "invalid_transport"}}
            for response in bearer_responses
        )
        statuses["no_origin"] = 200 if no_origin_codes == [403] * 4 else 599
        statuses["bearer"] = 200 if bearer_denied else 599
        metadata = client.post("/api/context/metadata", headers=origin, json=metadata_body)
        statuses["metadata"] = metadata.status_code
        metadata_value = metadata.json() if metadata.status_code == 200 else {}
        metadata_items = metadata_value.get("items", [])
        metadata_by_target = {
            (item.get("source"), item.get("selection_ref"), item.get("revision_ref")): item
            for item in metadata_items if type(item) is dict
        }
        metadata_ok = (
            metadata.status_code == 200
            and len(metadata_items) == 3
            and all(
                metadata_by_target.get((target["source"], target["selection_ref"], target["revision_ref"]), {}).get("payload_sha256")
                == expected_sha[index]
                for index, target in enumerate(selection)
            )
        )
        assert metadata_ok
        assert set(metadata_value) == {"selection", "items", "targets", "requires_private_confirmation", "manifest_digest"}
        assert metadata_value["selection"] == selection
        assert metadata_value["requires_private_confirmation"] is True
        assert type(metadata_value["manifest_digest"]) is str and len(metadata_value["manifest_digest"]) == 64
        assert all(c in "0123456789abcdef" for c in metadata_value["manifest_digest"])
        assert metadata_items == sorted(metadata_items, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
        item_keys = {"source", "selection_ref", "revision_ref", "boundary_sha256", "record_sha256", "payload_sha256", "classification", "provenance_ref"}
        assert all(set(item) == item_keys for item in metadata_items)
        assert all(item["classification"] in {"public", "private"} for item in metadata_items)
        assert all(type(item[key]) is str and len(item[key]) == 64 and all(c in "0123456789abcdef" for c in item[key]) for item in metadata_items for key in ("boundary_sha256", "record_sha256", "payload_sha256"))
        assert len(metadata_value["targets"]) == len(selection)
        for index, target in enumerate(metadata_value["targets"]):
            assert set(target) == {"target_index", "manifest_digest"} and target["target_index"] == index
            digest = target["manifest_digest"]
            prefix = "sha256:" if selection[index]["source"] == "knowledge" else ""
            assert type(digest) is str and digest.startswith(prefix)
            raw = digest[len(prefix):]
            assert len(raw) == 64 and all(c in "0123456789abcdef" for c in raw)
        selection = metadata_value["selection"]
        phase("metadata_complete")
        challenge = client.post("/api/context/challenge", headers=origin, json={"chat_session_id": session_id, "selection": selection})
        statuses["challenge"] = challenge.status_code
        assert challenge.status_code == 200
        phase("challenge_complete")
        denied = client.post("/api/context/pack", headers=origin, json={
            "chat_session_id": session_id, "selection": selection, "page_bytes": pack_page_bytes, "cursor": None,
        })
        statuses["denied_pack"] = denied.status_code
        assert denied.status_code == 409 and denied.json() == {"error": {"code": "context_unavailable"}}
        token = challenge.json()["command"]
        async def fail_async(*args, **kwargs):
            raise AssertionError("provider_called")
        def fail_sync(*args, **kwargs):
            raise AssertionError("provider_called")
        chat_routes.build_chat_context = fail_async
        chat_routes.llm_call_async = fail_async
        chat_routes.stream_llm = fail_sync
        chat_routes.stream_llm_with_fallback = fail_sync
        chat_routes.stream_agent_loop = fail_sync
        application.chat_handler.handle_memory_command = fail_async
        acknowledgement = client.post(
            "/api/chat", headers=origin, json={"message": token, "session": session_id},
        )
        statuses["ack"] = acknowledgement.status_code
        ack_value = acknowledgement.json() if acknowledgement.status_code == 200 else {}
        disclosure_id = ack_value.get("disclosure_message_id")
        assert acknowledgement.status_code == 200 and type(disclosure_id) is int and disclosure_id > 0
        phase("ack_complete")
        cursor = None
        pages_by_target = {0: bytearray(), 1: bytearray(), 2: bytearray()}
        seen = set()
        final = None
        page_ok = True
        max_pages = sum((len(payload) + pack_page_bytes - 1) // pack_page_bytes for payload in expected_payloads.values()) + len(expected_payloads)
        for _ in range(max_pages):
            packed = client.post("/api/context/pack", headers=origin, json={
                "chat_session_id": session_id, "selection": selection, "page_bytes": pack_page_bytes,
                "cursor": cursor, "disclosure_message_id": disclosure_id,
            })
            statuses["pack"] = packed.status_code
            if packed.status_code != 200:
                page_ok = False
                break
            final = packed.json()
            if set(final) != {"schema", "pack_id", "items", "page", "next_cursor", "complete",
                              "read_only", "authority_granted", "send_performed"}:
                page_ok = False
                break
            if final["schema"] != "odysseus.context_pack.v1" or final["items"] != metadata_items:
                page_ok = False
                break
            page = final.get("page")
            if type(page) is not dict or set(page) != {
                "target_index", "offset", "end", "total", "whole_sha256",
                "page_sha256", "byteform", "content_b64",
            }:
                page_ok = False
                break
            index = page.get("target_index")
            try:
                content = base64.b64decode(page.get("content_b64"), validate=True)
            except (TypeError, ValueError):
                page_ok = False
                break
            if (
                index not in pages_by_target
                or page.get("offset") != len(pages_by_target[index])
                or page.get("end") != page.get("offset") + len(content)
                or page.get("total") != len(expected_payloads[index])
                or page.get("whole_sha256") != "sha256:" + expected_sha[index]
                or page.get("page_sha256") != "sha256:" + hashlib.sha256(content).hexdigest()
                or page.get("byteform") != expected_byteforms[index]
                or final.get("read_only") is not True
                or final.get("authority_granted") is not False
                or final.get("send_performed") is not False
            ):
                page_ok = False
                break
            item = next((
                item for item in final.get("items", [])
                if type(item) is dict
                and item.get("source") == selection[index]["source"]
                and item.get("selection_ref") == selection[index]["selection_ref"]
                and item.get("revision_ref") == selection[index]["revision_ref"]
            ), None)
            if item is None or item.get("payload_sha256") != expected_sha[index]:
                page_ok = False
                break
            pages_by_target[index].extend(content)
            seen.add(index)
            if final.get("complete") is True and final.get("next_cursor") == "":
                break
            cursor = final.get("next_cursor")
            if type(cursor) is not str or not cursor or len(cursor) > 16384 or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for c in cursor):
                page_ok = False
                break
        phase("pack_complete")
        pack_ok = (
            isinstance(final, dict)
            and final.get("complete") is True and final.get("next_cursor") == ""
            and seen == {0, 1, 2}
            and page_ok and all(bytes(pages_by_target[index]) == expected_payloads[index] for index in pages_by_target)
            and all(pages_by_target[index] for index in pages_by_target)
        )
        phase("negative_checks_complete")
        success = (
            login.status_code == 200 and statuses["route_count"] == 200
            and statuses["setup"] == statuses["login"] == 200
            and statuses["planning_prepare"] == statuses["planning_accept"] == 200
            and statuses["forge_register"] == statuses["forge_retain"] == 200
            and statuses["metadata"] == 200 and statuses["challenge"] == 200 and metadata_ok
            and statuses["no_origin"] == statuses["bearer"] == 200
            and statuses["denied_pack"] == 409 and statuses["ack"] == 200
            and statuses["pack"] == 200 and pack_ok
        )
    phase("client_exited")
    write(statuses, success)
    phase("result_written")
except Exception as exc:
    tb = exc.__traceback__
    failure_line = 0
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == __file__:
            failure_line = tb.tb_lineno
        tb = tb.tb_next
    phase("child_exception")
    write(statuses, False, error="child_failure", failure_line=failure_line)
'''


_RESULT_SCHEMA = "odysseus.actual_context_pack_app.result.v1"
_PHASE_SCHEMA = "odysseus.actual_context_pack_app.phase.v1"
_STATUS_KEYS = (
    "setup", "login", "forge_register", "forge_retain", "planning_prepare",
    "planning_accept", "metadata", "challenge", "denied_pack", "ack", "pack",
    "no_origin", "bearer", "route_count", "knowledge_ready", "planning_ready",
)
_PHASES = frozenset({
    "child_start", "client_entered", "setup_complete", "login_complete",
    "forge_complete", "planning_complete", "knowledge_seeded", "app_reloaded",
    "metadata_complete", "challenge_complete", "ack_complete", "pack_complete",
    "negative_checks_complete", "client_exited", "result_written", "child_exception",
    "context_missing_prerequisites", "context_construction_unavailable",
})
_RESULT_KEYS = frozenset({"schema", "ok", "status", "last_success_phase", "error", "failure_line"})


def _unique_json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_key")
        value[key] = item
    return value


def _safe_object(path: Path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_json_object)
    except (OSError, UnicodeError, ValueError):
        return {}
    return value if type(value) is dict else {}


def _projection(result_path: Path, phase_path: Path):
    result = _safe_object(result_path)
    phase = _safe_object(phase_path)
    raw = result.get("status")
    valid_status = type(raw) is dict and frozenset(raw) == frozenset(_STATUS_KEYS)
    status = {
        key: raw.get(key) if valid_status and type(raw.get(key)) is int and 100 <= raw.get(key) <= 599 else 599
        for key in _STATUS_KEYS
    }
    valid_result = (
        frozenset(result) == _RESULT_KEYS
        and result.get("schema") == _RESULT_SCHEMA and type(result.get("ok")) is bool
        and valid_status and all(type(raw[key]) is int and 100 <= raw[key] <= 599 for key in _STATUS_KEYS)
        and result.get("last_success_phase") in _PHASES
        and type(result.get("failure_line")) is int and 0 <= result["failure_line"] <= 1000
        and (not result["ok"] or result["failure_line"] == 0)
        and result.get("error") in {"none", "child_failure"}
        and (result["ok"] == (result["error"] == "none"))
        and (not result["ok"] or result["last_success_phase"] == "client_exited")
    )
    valid_phase = (
        frozenset(phase) == frozenset({"schema", "phase"})
        and phase.get("schema") == _PHASE_SCHEMA and phase.get("phase") in _PHASES
    )
    return {"ok": bool(valid_result and result.get("ok") is True), "status": status,
            "failure_line": result["failure_line"] if valid_result else 0,
            "phase": phase.get("phase") if valid_phase else "child_exception",
            "last_success_phase": result.get("last_success_phase") if valid_result else "child_start",
            "error": result.get("error") if valid_result else "child_failure"}


def _kill_tree(process):
    if process.poll() is not None:
        return True
    try:
        result = subprocess.run(
            ("taskkill", "/PID", str(process.pid), "/T", "/F"),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        if process.poll() is None:
            return False
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        return False
    return process.poll() is not None


def _git(source: Path, *args: str, capture=False):
    result = subprocess.run(
        ("git", "-c", "core.hooksPath=" + str(source / ".no-hooks"),
         "-c", "commit.gpgsign=false", "-C", str(source), *args),
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, check=False, timeout=15,
    )
    if result.returncode != 0:
        raise AssertionError("fixture_git_failed")
    return result.stdout.decode("ascii").strip() if capture else None


def test_actual_app_three_source_context_pack_lifecycle(tmp_path, request):
    repo_root = Path(__file__).resolve().parents[1]
    data = tempfile.TemporaryDirectory(prefix="acp-", dir=repo_root / ".abc-test-tmp")
    request.addfinalizer(data.cleanup)
    data = Path(data.name)
    data_root = data / "data"
    source = data_root / "server_projects" / "actual-forge"
    source.mkdir(parents=True)
    (source / ".no-hooks").mkdir()
    _git(source, "init", "-b", "main")
    _git(source, "config", "user.email", "actual@example.invalid")
    _git(source, "config", "user.name", "Actual Fixture")
    _git(source, "config", "core.autocrlf", "false")
    (source / "public.py").write_bytes(b"print('actual')\n")
    _git(source, "add", "public.py")
    _git(source, "commit", "-m", "actual context fixture")
    commit = _git(source, "rev-parse", "HEAD", capture=True)
    child = data / "child"
    child.mkdir()
    profile = child / "profile"
    temp = child / "temp"
    profile.mkdir()
    temp.mkdir()
    result_path = child / "result.json"
    phase_path = child / "phase.json"
    env = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "WINDIR") if name in os.environ}
    env.update({
        "PYTHONPATH": str(repo_root), "PYTHONUTF8": "1", "HOME": str(profile),
        "USERPROFILE": str(profile), "APPDATA": str(profile / "roaming"),
        "LOCALAPPDATA": str(profile / "local"), "TEMP": str(temp), "TMP": str(temp),
        "ODYSSEUS_STARTUP_OFFLINE": "true", "ODYSSEUS_DATA_DIR": str(data_root),
        "DATABASE_URL": f"sqlite:///{(data_root / 'app.db').as_posix()}",
        "ODYSSEUS_PLUGINS_DIR": str(child / "plugins"), "ODYSSEUS_DISABLE_MCP": "1",
        "ODYSSEUS_DISABLE_USER_MCP": "1", "ODYSSEUS_INPROCESS_TASKS": "0",
        "ODYSSEUS_TRANSCRIPTION_ENABLED": "false", "ODYSSEUS_LEGACY_STT_ENABLED": "false",
        "ODYSSEUS_USI_RUNTIME_ENABLED": "false", "ODYSSEUS_USI_RUNTIME_MODE": "disabled",
        "ODYSSEUS_PLANNING_ISSUER_SECRET_B64": base64.b64encode(b"x" * 32).decode("ascii"),
        "ODYSSEUS_PLANNING_ISSUER_KEY_ID": "offline-test-key", "AUTH_ENABLED": "true",
        "LOCALHOST_BYPASS": "false", "SECURE_COOKIES": "false",
        "ODYSSEUS_TEST_RESULT_FILE": str(result_path), "ODYSSEUS_TEST_PHASE_FILE": str(phase_path),
        "ODYSSEUS_TEST_REPO_ROOT": str(repo_root), "ODYSSEUS_TEST_FORGE_SOURCE": str(source),
        "ODYSSEUS_TEST_FORGE_COMMIT": commit, "ODYSSEUS_DATA_ROOT": str(data_root),
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHON_DOTENV_DISABLED": "1",
    })
    child_script = child / "actual-context-pack-child.py"
    child_script.write_text(_CHILD, encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-B", str(child_script)], cwd=repo_root, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        process.wait(timeout=180)
    except subprocess.TimeoutExpired:
        assert _kill_tree(process) is True
        evidence = _projection(result_path, phase_path)
        raise AssertionError(json.dumps({"error": "actual_app_child_timeout", "phase": evidence["phase"], "process_reaped": process.poll() is not None}, sort_keys=True)) from None
    evidence = _projection(result_path, phase_path)
    assert process.returncode == 0, json.dumps(evidence, sort_keys=True)
    assert evidence["phase"] == "result_written", json.dumps(evidence, sort_keys=True)
    assert evidence["ok"] is True, json.dumps(evidence, sort_keys=True)
