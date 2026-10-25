from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import importlib.util
import base64
import subprocess
import sys
import tempfile
import textwrap
import time

_harness_path = Path(__file__).with_name("test_app_offline_startup.py")
_harness_spec = importlib.util.spec_from_file_location("offline_mcp_app_harness", _harness_path)
assert _harness_spec is not None and _harness_spec.loader is not None
_harness = importlib.util.module_from_spec(_harness_spec)
_harness_spec.loader.exec_module(_harness)
_TRIPWIRE = _harness._TRIPWIRE

_context_pack_path = Path(__file__).with_name("test_app_context_pack.py")
_context_pack_spec = importlib.util.spec_from_file_location(
    "authenticated_mcp_context_pack_harness", _context_pack_path,
)
assert _context_pack_spec is not None and _context_pack_spec.loader is not None
_context_pack_harness = importlib.util.module_from_spec(_context_pack_spec)
_context_pack_spec.loader.exec_module(_context_pack_harness)


_CHILD = _TRIPWIRE + r'''
import json
import logging
import os
from pathlib import Path

phase = os.environ["ODYSSEUS_MCP_TEST_PHASE"]
result_file = Path(os.environ["ODYSSEUS_TEST_RESULT_FILE"])
repo_root = Path(os.environ["ODYSSEUS_TEST_REPO_ROOT"])
token_file = Path(os.environ["ODYSSEUS_MCP_TEST_TOKEN_FILE"])
token_id_file = Path(os.environ["ODYSSEUS_MCP_TEST_TOKEN_ID_FILE"])
stage = "imports"
_STAGES = {
    "imports", "app_setup", "plugin_setup", "testclient", "auth_setup", "login",
    "migration_prepare", "migration_accept", "plugin_config", "token_mint",
    "restart_login", "cache_fence", "mcp_protocol", "public_snapshot_rename",
    "public_rename_window", "public_rename_rollback", "token_revoke",
    "restart_revocation_check", "same_alias_recreation", "same_alias_restart",
    "complete", "unknown",
}
_ERROR_CLASSES = {
    "AssertionError", "AttributeError", "ImportError", "IndexError", "KeyError",
    "ModuleNotFoundError", "NameError", "OSError", "RuntimeError", "SyntaxError",
    "TimeoutError", "TypeError", "ValueError",
}
_PUBLIC_SOURCE_ROOTS = {"src", "core", "routes", "plugins", "mcp_servers"}

def _safe_public_frames(exc, root):
    try:
        resolved_root = root.resolve(strict=True)
        candidates = [resolved_root / "app.py"]
        for directory in _PUBLIC_SOURCE_ROOTS:
            source_root = resolved_root / directory
            if source_root.is_dir():
                try:
                    candidates.extend(source_root.rglob("*.py"))
                except OSError:
                    continue
        source_map = {}
        for candidate in candidates:
            try:
                resolved = candidate.resolve(strict=True)
                relative = resolved.relative_to(resolved_root)
                if relative.as_posix() != "app.py" and (
                    not relative.parts or relative.parts[0] not in _PUBLIC_SOURCE_ROOTS
                ):
                    continue
                if resolved.is_file():
                    source_map[resolved] = relative.as_posix()
            except (OSError, RuntimeError, ValueError):
                continue
    except (OSError, RuntimeError, ValueError):
        return []

    frames = []
    line_counts = {}
    current = exc.__traceback__
    while current is not None and len(frames) < 6:
        try:
            source = Path(current.tb_frame.f_code.co_filename).resolve(strict=True)
            public_path = source_map.get(source)
            line = current.tb_lineno
            if public_path is not None and type(line) is int and line >= 1:
                if source not in line_counts:
                    with source.open("r", encoding="utf-8") as handle:
                        line_counts[source] = sum(1 for _ in handle)
                if line <= line_counts[source]:
                    frames.append({"path": public_path, "line": line})
        except (OSError, RuntimeError, UnicodeError, ValueError, TypeError):
            pass
        current = current.tb_next
    return frames

class PluginContext:
    def __init__(self, app, data_dir):
        self.app = app
        self.data_dir = data_dir
        self.logger = logging.getLogger("offline.mcp.e2e")
    def add_router(self, router):
        self.app.include_router(router)

def _mcp_rejected(response):
    try:
        body = response.json()
        return isinstance(body.get("error"), dict) or body.get("result", {}).get("isError") is True
    except (ValueError, AttributeError, TypeError):
        return False

try:
    stage = "imports"
    from fastapi.testclient import TestClient
    stage = "app_setup"
    import app as application
    stage = "plugin_setup"
    from plugins.mcp_server.plugin import setup
    from src.planning_roadmap_migration import build_h1r03_migration_manifest

    setup(PluginContext(application.app, Path(os.environ["ODYSSEUS_DATA_DIR"]) / "mcp-plugin"))
    checks = {"offline": application.STARTUP_OFFLINE is True, "phase": phase}
    password = "offline-mcp-e2e-password-93!"
    stage = "testclient"
    with TestClient(application.app) as client:
        if phase == "first":
            stage = "auth_setup"
            setup_response = client.post("/api/auth/setup", json={"username": "fuzzy", "password": password})
            stage = "login"
            login = client.post("/api/auth/login", json={"username": "fuzzy", "password": password})
            headers = {"Origin": "http://testserver"}
            from src.planning_roadmap_migration import build_h1r03_migration_manifest, canonical_json_bytes
            import hashlib
            stage = "migration_prepare"
            manifest = build_h1r03_migration_manifest(repo_root)
            migration_path = "/api/planning/durable/migrations/h1r03"
            census_digest = "sha256:" + hashlib.sha256(
                canonical_json_bytes(manifest["structural_census"])
            ).hexdigest()
            prepared = client.post(migration_path + "/prepare", headers=headers, json={
                "expected_batch_id": manifest["batch_id"],
                "expected_manifest_digest": manifest["manifest_digest"],
                "expected_structural_census_digest": census_digest,
            })
            prepared_value = prepared.json() if prepared.status_code == 200 else {}
            stage = "migration_accept"
            accepted = client.post(migration_path + "/accept", headers=headers, json={
                "batch_id": manifest["batch_id"],
                "expected_manifest_digest": manifest["manifest_digest"],
                "expected_structural_census_digest": census_digest,
                "prior_receipt_id": prepared_value.get("receipt", {}).get("receipt_id", ""),
            })
            accepted_value = accepted.json() if accepted.status_code == 200 else {}
            stage = "plugin_config"
            config = client.post("/api/plugins/mcp/config", headers=headers, json={
                "enabled": True,
                "allow_planning_reads": True,
            })
            stage = "token_mint"
            minted = client.post("/api/tokens", headers=headers, data={
                "name": "offline-mcp-e2e",
                "scopes": "mcp:planning:read",
            })
            token_data = minted.json() if minted.status_code == 200 else {}
            token = token_data.get("token")
            if isinstance(token, str) and token:
                token_file.write_text(token, encoding="utf-8")
            token_id = token_data.get("id")
            if isinstance(token_id, str) and token_id:
                token_id_file.write_text(token_id, encoding="utf-8")
            checks.update({
                "setup_ok": setup_response.status_code == 200,
                "login_ok": login.status_code == 200,
                "durable_migration_accepted": (
                    prepared.status_code == 200
                    and accepted.status_code == 200
                    and accepted_value.get("receipt", {}).get("after_state") == "accepted"
                ),
                "config_ok": config.status_code == 200,
                "mint_ok": minted.status_code == 200 and token_data.get("scopes") == ["mcp:planning:read"],
                "token_persisted_privately": token_file.is_file() and token_id_file.is_file(),
            })
        elif phase == "second":
            stage = "restart_login"
            login = client.post("/api/auth/login", json={"username": "fuzzy", "password": password})
            token = token_file.read_text(encoding="utf-8")
            auth = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
            token_id = token_id_file.read_text(encoding="utf-8")
            from core.database import ApiToken, get_db_session
            stage = "cache_fence"
            with get_db_session() as db:
                token_row = db.query(ApiToken).filter(ApiToken.id == token_id).first()
                token_row.owner = "poisoned-cache-owner"
                db.commit()
            application.app.state.invalidate_token_cache()
            stage = "mcp_protocol"
            poisoned_cache_response = client.post("/api/plugins/mcp", headers=auth, json={
                "jsonrpc": "2.0", "id": 11, "method": "tools/list", "params": {},
            })
            cache_entries = [
                item
                for entries in application.app.state._token_cache.values()
                for item in entries
                if item[0] == token_id
            ]
            with get_db_session() as db:
                token_row = db.query(ApiToken).filter(ApiToken.id == token_id).first()
                token_row.owner = "fuzzy"
                db.commit()
            application.app.state.invalidate_token_cache()
            restored_cache_response = client.post("/api/plugins/mcp", headers=auth, json={
                "jsonrpc": "2.0", "id": 12, "method": "tools/list", "params": {},
            })
            checks.update({
                "poisoned_mcp_owner_not_cached": poisoned_cache_response.status_code == 401 and not cache_entries,
                "restored_mcp_owner_cached": restored_cache_response.status_code == 200,
            })
            initialized = client.post("/api/plugins/mcp", headers=auth, json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"},
            })
            listed = client.post("/api/plugins/mcp", headers=auth, json={
                "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
            })
            called = client.post("/api/plugins/mcp", headers=auth, json={
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "planning_list_roadmaps", "arguments": {"limit": 3}},
            })
            roadmap_id = ""
            try:
                from src.planning_roadmap_migration import build_h1r03_migration_manifest
                roadmap_id = build_h1r03_migration_manifest(repo_root)["source_items"][0]["roadmap_id"]
            except (IndexError, KeyError, TypeError):
                pass
            definition = client.post("/api/plugins/mcp", headers=auth, json={
                "jsonrpc": "2.0", "id": 4, "method": "tools/call",
                "params": {"name": "planning_read_gate_definitions", "arguments": {
                    "project_id": "harbour-one", "roadmap_id": roadmap_id,
                }},
            })
            handoff = client.post("/api/plugins/mcp", headers=auth, json={
                "jsonrpc": "2.0", "id": 5, "method": "tools/call",
                "params": {"name": "planning_create_agent_handoff", "arguments": {
                    "project_id": "harbour-one", "roadmap_id": roadmap_id,
                }},
            })
            init_value = initialized.json() if initialized.status_code == 200 else {}
            list_value = listed.json() if listed.status_code == 200 else {}
            call_value = called.json() if called.status_code == 200 else {}
            result_text = ""
            try:
                result_text = call_value["result"]["content"][0]["text"]
            except (KeyError, IndexError, TypeError):
                pass
            try:
                projection = json.loads(result_text)
            except (ValueError, TypeError):
                projection = {}
            names = [item.get("name") for item in list_value.get("result", {}).get("tools", []) if isinstance(item, dict)]
            def _tool_result(response):
                try:
                    value = response.json()
                    return json.loads(value["result"]["content"][0]["text"])
                except (ValueError, KeyError, IndexError, TypeError):
                    return {}
            definition_projection = _tool_result(definition)
            handoff_projection = _tool_result(handoff)
            token_id = token_id_file.read_text(encoding="utf-8")

            # Pause one real public MCP request immediately after its atomic
            # alias/subject snapshot. A public rename advances the security
            # epoch before changing that alias, so the paused request must be
            # rejected when it resumes. Renaming back also exercises the same
            # epoch fence on rollback-style convergence.
            import concurrent.futures
            import threading
            manager = application.app.state.auth_manager
            original_active_identity = manager.active_admin_subject_identity
            snapshot_entered = threading.Event()
            snapshot_release = threading.Event()
            paused = {"done": False}
            snapshot_subject = manager.subject_id_for_username("fuzzy")
            def pause_after_snapshot(*, username=None, subject_id=None):
                value = original_active_identity(username=username, subject_id=subject_id)
                if subject_id == snapshot_subject and not paused["done"]:
                    paused["done"] = True
                    snapshot_entered.set()
                    if not snapshot_release.wait(10):
                        raise RuntimeError("bounded identity snapshot barrier expired")
                return value
            manager.active_admin_subject_identity = pause_after_snapshot
            stage = "public_snapshot_rename"
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    stale_future = pool.submit(client.post, "/api/plugins/mcp", headers=auth, json={
                        "jsonrpc": "2.0", "id": 13, "method": "tools/list", "params": {},
                    })
                    snapshot_paused = snapshot_entered.wait(10)
                    if snapshot_paused:
                        snapshot_rename = client.put(
                            "/api/auth/users/fuzzy/rename",
                            headers={"Origin": "http://testserver"}, json={"username": "fuzzy-snapshot"},
                        )
                    else:
                        snapshot_rename = None
                    snapshot_release.set()
                    stale_snapshot_response = stale_future.result(timeout=15)
            finally:
                snapshot_release.set()
                manager.active_admin_subject_identity = original_active_identity
            snapshot_restored = client.put(
                "/api/auth/users/fuzzy-snapshot/rename",
                headers={"Origin": "http://testserver"}, json={"username": "fuzzy"},
            ) if snapshot_rename is not None and snapshot_rename.status_code == 200 else None
            after_snapshot_restore = client.post("/api/plugins/mcp", headers=auth, json={
                "jsonrpc": "2.0", "id": 14, "method": "tools/list", "params": {},
            })
            checks.update({
                "identity_snapshot_paused": snapshot_paused,
                "identity_snapshot_rename_succeeded": snapshot_rename is not None and snapshot_rename.status_code == 200,
                "stale_identity_snapshot_denied": stale_snapshot_response.status_code == 401,
                "identity_snapshot_rename_restored": snapshot_restored is not None and snapshot_restored.status_code == 200,
                "identity_snapshot_access_recovered": after_snapshot_restore.status_code == 200,
            })

            # Pause the actual public rename between its AuthManager commit and
            # SQL owner migration. The bearer token must fail closed in this
            # observable window, then recover after durable owner convergence.
            import routes.auth_user_rename as rename_ops
            original_sql_rename = rename_ops._rename_sql_owner_references
            sql_entered = threading.Event()
            sql_release = threading.Event()
            def barrier_sql_rename(old_owner, new_owner):
                sql_entered.set()
                if not sql_release.wait(10):
                    raise RuntimeError("bounded rename barrier expired")
                return original_sql_rename(old_owner, new_owner)
            rename_ops._rename_sql_owner_references = barrier_sql_rename
            stage = "public_rename_window"
            # A separate, non-context client creates an independent portal
            # without running another lifespan on the same application.
            rename_probe_client = TestClient(application.app)
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    rename_future = pool.submit(
                        client.put, "/api/auth/users/fuzzy/rename",
                        headers={"Origin": "http://testserver"}, json={"username": "fuzzy-window"},
                    )
                    try:
                        entered = sql_entered.wait(10)
                        during_rename = rename_probe_client.post("/api/plugins/mcp", headers=auth, json={
                            "jsonrpc": "2.0", "id": 6, "method": "tools/list", "params": {},
                        }) if entered else None
                    finally:
                        sql_release.set()
                    rename_response = rename_future.result(timeout=15)
            finally:
                sql_release.set()
                rename_ops._rename_sql_owner_references = original_sql_rename
                rename_probe_client.close()
            after_rename = client.post("/api/plugins/mcp", headers=auth, json={
                "jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {},
            })
            renamed_definition = client.post("/api/plugins/mcp", headers=auth, json={
                "jsonrpc": "2.0", "id": 15, "method": "tools/call",
                "params": {"name": "planning_read_gate_definitions", "arguments": {
                    "project_id": "harbour-one", "roadmap_id": roadmap_id,
                }},
            })
            renamed_handoff = client.post("/api/plugins/mcp", headers=auth, json={
                "jsonrpc": "2.0", "id": 16, "method": "tools/call",
                "params": {"name": "planning_create_agent_handoff", "arguments": {
                    "project_id": "harbour-one", "roadmap_id": roadmap_id,
                }},
            })
            renamed_browser_read = client.get(
                f"/api/planning/projects/harbour-one/roadmaps/{roadmap_id}"
            )
            renamed_browser_handoff = {}
            if renamed_browser_read.status_code == 200:
                renamed_roadmap = renamed_browser_read.json().get("roadmap", {})
                renamed_browser_handoff_response = client.post(
                    f"/api/planning/projects/harbour-one/roadmaps/{roadmap_id}/agent-handoff",
                    headers={"Origin": "http://testserver"},
                    json={"revision": renamed_roadmap.get("revision"),
                          "content_hash": renamed_roadmap.get("content_hash")},
                )
                renamed_browser_handoff = renamed_browser_handoff_response.json()
            renamed_definition_value = _tool_result(renamed_definition)
            renamed_handoff_value = _tool_result(renamed_handoff)
            checks.update({
                "rename_window_entered": entered,
                "rename_window_denied": during_rename is not None and during_rename.status_code == 401,
                "rename_sql_converged": rename_response.status_code == 200 and after_rename.status_code == 200,
                "accepted_definition_survives_rename": (
                    renamed_definition.status_code == 200
                    and renamed_definition_value.get("payload", {}).get("project_id") == "harbour-one"
                    and renamed_definition_value.get("payload", {}).get("roadmap_id") == roadmap_id
                    and renamed_definition_value.get("origin", {}).get("source_of_truth") is True
                    and renamed_definition_value.get("origin", {}).get("revision") == definition_projection.get("origin", {}).get("revision")
                    and renamed_definition_value.get("origin", {}).get("content_hash") == definition_projection.get("origin", {}).get("content_hash")
                ),
                "accepted_handoff_survives_rename": (
                    renamed_handoff.status_code == 200
                    and renamed_handoff_value.get("payload", {}).get("launch_authorized") is False
                    and renamed_handoff_value.get("origin", {}).get("derived_from_accepted_durable") is True
                    and renamed_handoff_value.get("origin", {}).get("revision") == renamed_definition_value.get("origin", {}).get("revision")
                    and renamed_handoff_value.get("origin", {}).get("content_hash") == renamed_definition_value.get("origin", {}).get("content_hash")
                ),
                "browser_accepted_read_survives_rename": (
                    renamed_browser_read.status_code == 200
                    and renamed_browser_read.json().get("origin", {}).get("source")
                    == "h1r03_durable_migration_accepted"
                ),
                "browser_handoff_survives_rename": (
                    renamed_browser_handoff.get("launch_authorized") is False
                ),
            })

            # Exercise both transaction failure outcomes through the public
            # route. A successful AuthManager rollback restores token access;
            # a failed rollback leaves the alias/SQL pair mismatched and denied.
            def fail_sql_rename(_old_owner, _new_owner):
                raise RuntimeError("synthetic SQL owner migration failure")
            manager = application.app.state.auth_manager
            real_rename_user = manager.rename_user
            rename_ops._rename_sql_owner_references = fail_sql_rename
            stage = "public_rename_rollback"
            try:
                rollback_ok = client.put(
                    "/api/auth/users/fuzzy-window/rename",
                    headers={"Origin": "http://testserver"}, json={"username": "fuzzy-rollback"},
                )
                after_rollback = client.post("/api/plugins/mcp", headers=auth, json={
                    "jsonrpc": "2.0", "id": 8, "method": "tools/list", "params": {},
                })
                rename_calls = {"count": 0}
                def refuse_rollback(old_name, new_name, requester):
                    rename_calls["count"] += 1
                    if rename_calls["count"] == 1:
                        return real_rename_user(old_name, new_name, requester)
                    return False
                manager.rename_user = refuse_rollback
                failed_rollback = client.put(
                    "/api/auth/users/fuzzy-window/rename",
                    headers={"Origin": "http://testserver"}, json={"username": "fuzzy-failed-rollback"},
                )
                manager.rename_user = real_rename_user
                denied_after_failed_rollback = client.post("/api/plugins/mcp", headers=auth, json={
                    "jsonrpc": "2.0", "id": 9, "method": "tools/list", "params": {},
                })
                restored = real_rename_user("fuzzy-failed-rollback", "fuzzy-window", "fuzzy-failed-rollback")
                recovered_after_restore = client.post("/api/plugins/mcp", headers=auth, json={
                    "jsonrpc": "2.0", "id": 10, "method": "tools/list", "params": {},
                })
                checks.update({
                    "sql_failure_rolled_back_and_denied_no_access": (
                        rollback_ok.status_code == 500
                        and after_rollback.status_code == 200
                    ),
                    "failed_rollback_stayed_denied": (
                        failed_rollback.status_code == 500
                        and denied_after_failed_rollback.status_code == 401
                        and rename_calls["count"] == 2
                    ),
                    "restored_alias_recovered": restored is True and recovered_after_restore.status_code == 200,
                })
            finally:
                manager.rename_user = real_rename_user
                rename_ops._rename_sql_owner_references = original_sql_rename
            stage = "token_revoke"
            revoked = client.delete("/api/tokens/" + token_id, headers={"Origin": "http://testserver"})
            checks.update({
                "login_ok": login.status_code == 200,
                "initialize_ok": init_value.get("result", {}).get("serverInfo", {}).get("name") == "odysseus",
                "list_ok": listed.status_code == 200 and "planning_list_roadmaps" in names,
                "call_ok": (
                    called.status_code == 200
                    and projection.get("schema") == "odysseus.mcp.tool_result.v1"
                    and projection.get("tool") == "planning_list_roadmaps"
                    and projection.get("origin") == {"kind": "repository_projection", "source_of_truth": False}
                    and projection.get("payload", {}).get("schema") == "odysseus.planning.roadmap_list.v1"
                ),
                "accepted_definition_ok": (
                    definition.status_code == 200
                    and definition_projection.get("schema") == "odysseus.mcp.tool_result.v1"
                    and definition_projection.get("origin", {}).get("kind") == "accepted_durable_definition"
                    and definition_projection.get("origin", {}).get("source_of_truth") is True
                    and type(definition_projection.get("origin", {}).get("revision")) is int
                    and str(definition_projection.get("origin", {}).get("content_hash", "")).startswith("sha256:")
                ),
                "handoff_is_derived_and_non_authoritative": (
                    handoff.status_code == 200
                    and handoff_projection.get("schema") == "odysseus.mcp.tool_result.v1"
                    and handoff_projection.get("origin", {}).get("derived_from_accepted_durable") is True
                    and handoff_projection.get("origin", {}).get("source_of_truth") is False
                    and handoff_projection.get("payload", {}).get("launch_authorized") is False
                ),
                "revoke_ok": revoked.status_code == 200 and revoked.json().get("status") == "deleted",
            })
        elif phase == "third":
            stage = "restart_revocation_check"
            token = token_file.read_text(encoding="utf-8")
            response = client.post("/api/plugins/mcp", headers={"Authorization": "Bearer " + token}, json={
                "jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {},
            })
            checks["revoked_after_restart_denied"] = response.status_code == 401
            stage = "same_alias_recreation"
            login = client.post("/api/auth/login", json={"username": "fuzzy-window", "password": password})
            manager = application.app.state.auth_manager
            previous_subject = manager.subject_id_for_username("fuzzy-window")
            guardian_create = client.post("/api/auth/users", headers={"Origin": "http://testserver"}, json={
                "username": "guardian", "password": "offline-mcp-guardian-password-93!", "is_admin": True,
            })
            old_roadmap_id = build_h1r03_migration_manifest(repo_root)["source_items"][0]["roadmap_id"]
            old_browser_read = client.get(f"/api/planning/projects/harbour-one/roadmaps/{old_roadmap_id}")
            old_browser_roadmap = old_browser_read.json().get("roadmap", {}) if old_browser_read.status_code == 200 else {}
            deletion_mint = client.post("/api/tokens", headers={"Origin": "http://testserver"}, data={
                "name": "offline-mcp-deletion-proof", "scopes": "mcp:planning:read",
            })
            deletion_token = deletion_mint.json().get("token") if deletion_mint.status_code == 200 else ""
            deletion_token = deletion_token if isinstance(deletion_token, str) else ""
            deletion_auth = {"Authorization": "Bearer " + deletion_token}
            with token_file.with_name("private-deletion-token").open("x", encoding="utf-8") as stream:
                stream.write(deletion_token)
            deletion_calls = [
                {"jsonrpc": "2.0", "id": 30 + index, "method": "tools/call", "params": {
                    "name": name, "arguments": {"project_id": "harbour-one", "roadmap_id": old_roadmap_id},
                }} for index, name in enumerate(("planning_read_gate_definitions", "planning_create_agent_handoff"))
            ]
            deletion_before = [client.post("/api/plugins/mcp", headers=deletion_auth, json=call) for call in deletion_calls]
            owner_session = client.cookies.get("odysseus_session")
            previous_owner_client = TestClient(application.app)
            if isinstance(owner_session, str):
                previous_owner_client.cookies.set("odysseus_session", owner_session)
            guardian_login = client.post("/api/auth/login", json={
                "username": "guardian", "password": "offline-mcp-guardian-password-93!",
            })
            deleted = client.request(
                "DELETE", "/api/auth/users", headers={"Origin": "http://testserver"},
                json={"username": "fuzzy-window"},
            )
            deletion_after = [client.post("/api/plugins/mcp", headers=deletion_auth, json=call) for call in deletion_calls]
            try:
                deleted_browser_read = previous_owner_client.get(
                    f"/api/planning/projects/harbour-one/roadmaps/{old_roadmap_id}"
                )
                deleted_browser_handoff = previous_owner_client.post(
                    f"/api/planning/projects/harbour-one/roadmaps/{old_roadmap_id}/agent-handoff",
                    headers={"Origin": "http://testserver"},
                    json={"revision": old_browser_roadmap.get("revision"),
                          "content_hash": old_browser_roadmap.get("content_hash")},
                )
            finally:
                previous_owner_client.close()
            checks.update({
                "deleted_owner_positive_before_delete": (
                    deletion_mint.status_code == 200 and bool(deletion_token) and old_browser_read.status_code == 200
                    and all(item.status_code == 200 and not _mcp_rejected(item) for item in deletion_before)
                ),
                "deleted_owner_mcp_both_denied_immediately": all(item.status_code == 401 for item in deletion_after),
                "deleted_owner_browser_read_denied_immediately": deleted_browser_read.status_code == 401,
                "deleted_owner_browser_handoff_denied_immediately": deleted_browser_handoff.status_code == 401,
            })
            recreated_user = client.post("/api/auth/users", headers={"Origin": "http://testserver"}, json={
                "username": "fuzzy-window", "password": password, "is_admin": True,
            })
            recreated_subject = manager.subject_id_for_username("fuzzy-window")
            recreated_login = client.post("/api/auth/login", json={
                "username": "fuzzy-window", "password": password,
            })
            minted = client.post("/api/tokens", headers={"Origin": "http://testserver"}, data={
                "name": "offline-mcp-recreated-admin", "scopes": "mcp:planning:read",
            })
            recreated_token_data = minted.json() if minted.status_code == 200 else {}
            recreated_token = recreated_token_data.get("token")
            if isinstance(recreated_token, str) and recreated_token:
                token_file.write_text(recreated_token, encoding="utf-8")
            recreated_token_id = recreated_token_data.get("id")
            if isinstance(recreated_token_id, str) and recreated_token_id:
                token_id_file.write_text(recreated_token_id, encoding="utf-8")
            manifest = build_h1r03_migration_manifest(repo_root)
            roadmap_id = manifest["source_items"][0]["roadmap_id"]
            browser_read = client.get(f"/api/planning/projects/harbour-one/roadmaps/{roadmap_id}")
            browser_handoff = client.post(
                f"/api/planning/projects/harbour-one/roadmaps/{roadmap_id}/agent-handoff",
                headers={"Origin": "http://testserver"},
                json={"revision": old_browser_roadmap.get("revision"),
                      "content_hash": old_browser_roadmap.get("content_hash")},
            )
            recreated_auth = {
                "Authorization": "Bearer " + recreated_token,
                "Content-Type": "application/json",
            } if isinstance(recreated_token, str) else {}
            definition_after_recreate = client.post("/api/plugins/mcp", headers=recreated_auth, json={
                "jsonrpc": "2.0", "id": 17, "method": "tools/call",
                "params": {"name": "planning_read_gate_definitions", "arguments": {
                    "project_id": "harbour-one", "roadmap_id": roadmap_id,
                }},
            })
            handoff_after_recreate = client.post("/api/plugins/mcp", headers=recreated_auth, json={
                "jsonrpc": "2.0", "id": 18, "method": "tools/call",
                "params": {"name": "planning_create_agent_handoff", "arguments": {
                    "project_id": "harbour-one", "roadmap_id": roadmap_id,
                }},
            })
            checks.update({
                "same_alias_deleted_and_recreated": (
                    login.status_code == 200 and guardian_create.status_code == 200
                    and guardian_login.status_code == 200 and deleted.status_code == 200
                    and recreated_user.status_code == 200 and recreated_login.status_code == 200
                    and isinstance(previous_subject, str) and isinstance(recreated_subject, str)
                    and previous_subject != recreated_subject
                ),
                "same_alias_mcp_definition_denied_immediately": (
                    minted.status_code == 200 and definition_after_recreate.status_code == 200
                    and _mcp_rejected(definition_after_recreate)
                ),
                "same_alias_mcp_handoff_denied_immediately": (
                    minted.status_code == 200 and handoff_after_recreate.status_code == 200
                    and _mcp_rejected(handoff_after_recreate)
                ),
                "same_alias_browser_read_denied_immediately": browser_read.status_code != 200,
                "same_alias_browser_handoff_denied_immediately": browser_handoff.status_code != 200,
            })
        elif phase == "fourth":
            stage = "same_alias_restart"
            login = client.post("/api/auth/login", json={"username": "fuzzy-window", "password": password})
            token = token_file.read_text(encoding="utf-8")
            auth = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
            manifest = build_h1r03_migration_manifest(repo_root)
            roadmap_id = manifest["source_items"][0]["roadmap_id"]
            deletion_token = token_file.with_name("private-deletion-token").read_text(encoding="utf-8")
            deleted_after_restart = [client.post("/api/plugins/mcp", headers={"Authorization": "Bearer " + deletion_token}, json={
                "jsonrpc": "2.0", "id": 40 + index, "method": "tools/call", "params": {
                    "name": name, "arguments": {"project_id": "harbour-one", "roadmap_id": roadmap_id},
                },
            }) for index, name in enumerate(("planning_read_gate_definitions", "planning_create_agent_handoff"))]
            checks["deleted_owner_mcp_both_denied_after_restart"] = all(item.status_code == 401 for item in deleted_after_restart)
            definition = client.post("/api/plugins/mcp", headers=auth, json={
                "jsonrpc": "2.0", "id": 19, "method": "tools/call",
                "params": {"name": "planning_read_gate_definitions", "arguments": {
                    "project_id": "harbour-one", "roadmap_id": roadmap_id,
                }},
            })
            handoff = client.post("/api/plugins/mcp", headers=auth, json={
                "jsonrpc": "2.0", "id": 20, "method": "tools/call",
                "params": {"name": "planning_create_agent_handoff", "arguments": {
                    "project_id": "harbour-one", "roadmap_id": roadmap_id,
                }},
            })
            browser_read = client.get(f"/api/planning/projects/harbour-one/roadmaps/{roadmap_id}")
            old_revision = json.loads(manifest["normalized_payloads"][roadmap_id])["roadmaps"][0]
            browser_handoff = client.post(
                f"/api/planning/projects/harbour-one/roadmaps/{roadmap_id}/agent-handoff",
                headers={"Origin": "http://testserver"},
                json={"revision": old_revision.get("revision"), "content_hash": old_revision.get("content_hash")},
            )
            checks.update({
                "same_alias_login_after_restart": login.status_code == 200,
                "same_alias_mcp_definition_denied_after_restart": (
                    definition.status_code == 200 and _mcp_rejected(definition)
                ),
                "same_alias_mcp_handoff_denied_after_restart": (
                    handoff.status_code == 200 and _mcp_rejected(handoff)
                ),
                "same_alias_browser_read_denied_after_restart": browser_read.status_code != 200,
                "same_alias_browser_handoff_denied_after_restart": browser_handoff.status_code != 200,
            })
        else:
            checks["unknown_phase"] = False
    stage = "complete"
    checks["stage"] = stage
    checks["network_attempted"] = attempted["network"]
    checks["process_attempted"] = attempted["process"]
    checks["ok"] = (
        all(value is True for key, value in checks.items() if key not in {"phase", "stage", "network_attempted", "process_attempted"})
        and not attempted["network"]
        and not attempted["process"]
    )
    result_file.write_text(json.dumps(checks, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    raise SystemExit(0 if checks["ok"] else 2)
except Exception as exc:
    result_file.write_text(json.dumps({
        "ok": False,
        "phase": phase,
        "stage": stage if stage in _STAGES else "unknown",
        "error_class": type(exc).__name__ if type(exc).__name__ in _ERROR_CLASSES else "other",
        "network_attempted": attempted["network"],
        "process_attempted": attempted["process"],
        "public_frames": _safe_public_frames(exc, repo_root),
    }, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    raise SystemExit(1)
'''


def test_actual_app_persists_authenticated_planning_mcp_token_across_restart_and_revoke(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    profile_root = tmp_path / "profile"
    data_root = tmp_path / "data"
    for path in (profile_root, data_root, profile_root / "roaming", profile_root / "local", tmp_path / "temp", tmp_path / "cache"):
        path.mkdir(parents=True, exist_ok=True)
    result_file = tmp_path / "child-result.json"
    token_file = tmp_path / "private-token"
    token_id_file = tmp_path / "token-id"
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
        "PYTHON_DOTENV_DISABLED": "1",
        "PYTHONPATH": str(repo_root),
        "ODYSSEUS_STARTUP_OFFLINE": "true",
        "ODYSSEUS_DATA_DIR": str(data_root),
        "DATABASE_URL": f"sqlite:///{(data_root / 'app.db').as_posix()}",
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
        "ODYSSEUS_TEST_RESULT_FILE": str(result_file),
        "ODYSSEUS_MCP_TEST_TOKEN_FILE": str(token_file),
        "ODYSSEUS_MCP_TEST_TOKEN_ID_FILE": str(token_id_file),
    })
    child_script = tmp_path / "mcp-app-child.py"
    with child_script.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(_CHILD)
    for phase in ("first", "second", "third", "fourth"):
        env["ODYSSEUS_MCP_TEST_PHASE"] = phase
        result = subprocess.run(
            [sys.executable, str(child_script)],
            cwd=repo_root,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=180,
            check=False,
        )
        assert result_file.is_file(), "offline MCP child did not return a bounded status projection"
        projection = json.loads(result_file.read_text(encoding="utf-8"))
        assert result.returncode == 0, json.dumps(projection, sort_keys=True)
        assert projection.get("ok") is True
        assert projection.get("stage") == "complete"


_INBOX_CHILD = _TRIPWIRE + r'''
import json
import logging
import os
from pathlib import Path

phase = os.environ["ODYSSEUS_MCP_TEST_PHASE"]
result_file = Path(os.environ["ODYSSEUS_TEST_RESULT_FILE"])
token_file = Path(os.environ["ODYSSEUS_MCP_TEST_TOKEN_FILE"])
token_id_file = Path(os.environ["ODYSSEUS_MCP_TEST_TOKEN_ID_FILE"])
stage = "imports"
checks = {"phase": phase}
try:
    from fastapi.testclient import TestClient
    import app as application
    from plugins.mcp_server.plugin import setup
    class PluginContext:
        def __init__(self, app, data_dir):
            self.app, self.data_dir = app, data_dir
            self.logger = logging.getLogger("offline.inbox.mcp.e2e")
        def add_router(self, router): self.app.include_router(router)
    setup(PluginContext(application.app, Path(os.environ["ODYSSEUS_DATA_DIR"]) / "mcp-plugin"))
    password = "offline-inbox-e2e-password-93!"
    request_body = {
        "schema": "odysseus.clarification_request.v2", "scope": "project",
        "intent_summary": "Choose one bounded action.", "questions": [],
        "batch": {"label": "Inbox", "index": 1, "total": 1, "max_visible_questions": 3},
        "defaults_visible": False,
    }
    with TestClient(application.app) as client:
        if phase == "first":
            stage = "auth_setup"
            setup_response = client.post("/api/auth/setup", json={"username": "fuzzy", "password": password})
            login = client.post("/api/auth/login", json={"username": "fuzzy", "password": password})
            headers = {"Origin": "http://testserver"}
            config = client.post("/api/plugins/mcp/config", headers=headers, json={
                "enabled": True, "allow_planning_reads": True,
            })
            created = client.post("/api/clarifications", headers=headers, json={
                "session_id": "private-inbox-session", "request": request_body,
                "project_slug": "private-inbox-project", "clarification_id": "clar-app-inbox-001",
            })
            foreign_create = client.post("/api/auth/users", headers=headers, json={
                "username": "inbox-foreign", "password": "offline-inbox-foreign-password-93!",
                "is_admin": False,
            })
            manager = application.app.state.auth_manager
            from src.auth_helpers import SessionPrincipal
            from src.clarification_store import ClarificationStore
            foreign_subject = manager.subject_id_for_username("inbox-foreign")
            exclusion_store = ClarificationStore()
            foreign_run = exclusion_store.create_run(
                principal=SessionPrincipal("inbox-foreign", foreign_subject, "browser_session"),
                session_id="private-foreign-session", request=request_body,
                clarification_id="clar-app-foreign-001",
            )
            local_run = exclusion_store.create_run(
                principal=SessionPrincipal("local", None, "local"),
                session_id="private-local-session", request=request_body,
                clarification_id="clar-app-local-001",
            )
            minted = client.post("/api/tokens", headers=headers, data={
                "name": "offline-inbox-e2e", "scopes": "mcp:harbour:inbox",
            })
            token_data = minted.json() if minted.status_code == 200 else {}
            token = token_data.get("token")
            token_id = token_data.get("id")
            if isinstance(token, str) and token: token_file.write_text(token, encoding="utf-8")
            if isinstance(token_id, str) and token_id: token_id_file.write_text(token_id, encoding="utf-8")
            revoked_mint = client.post("/api/tokens", headers=headers, data={
                "name": "offline-inbox-restart-revocation", "scopes": "mcp:harbour:inbox",
            })
            revoked_value = revoked_mint.json() if revoked_mint.status_code == 200 else {}
            revoked_token = revoked_value.get("token")
            revoked_id = revoked_value.get("id")
            revoked_file = token_file.with_name("private-revoked-inbox-token")
            if isinstance(revoked_token, str) and revoked_token:
                revoked_file.write_text(revoked_token, encoding="utf-8")
            pre_restart_revoke = client.delete(
                "/api/tokens/" + revoked_id, headers=headers,
            ) if isinstance(revoked_id, str) else None
            auth = {"Authorization": "Bearer " + token} if isinstance(token, str) else {}
            tools = client.post("/api/plugins/mcp", headers=auth, json={
                "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
            })
            listed = client.post("/api/plugins/mcp", headers=auth, json={
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "harbour_inbox_list", "arguments": {"limit": 5}},
            })
            unified = client.post("/api/plugins/mcp", headers=auth, json={
                "jsonrpc": "2.0", "id": 21, "method": "tools/call",
                "params": {"name": "harbour_inbox_list", "arguments": {"limit": 5, "view": "unified"}},
            })
            action = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                "name": "harbour_inbox_pause", "arguments": {
                    "clarification_id": "clar-app-inbox-001", "expected_version": 1,
                    "idempotency_key": "app-action-one",
                },
            }}
            paused = client.post("/api/plugins/mcp", headers=auth, json=action)
            replay = client.post("/api/plugins/mcp", headers=auth, json=action)
            enriched = client.post("/api/plugins/mcp", headers=auth, json={
                "jsonrpc": "2.0", "id": 31, "method": "tools/call",
                "params": {"name": "harbour_inbox_list", "arguments": {"view": "unified_receipts", "limit": 5}},
            })
            browser = client.get("/api/clarifications/clar-app-inbox-001")
            crossed = client.post("/api/plugins/mcp", headers=auth, json={
                "jsonrpc": "2.0", "id": 4, "method": "tools/call",
                "params": {"name": "planning_list_roadmaps", "arguments": {}},
            })
            planning_mint = client.post("/api/tokens", headers=headers, data={
                "name": "offline-planning-cross-scope", "scopes": "mcp:planning:read",
            })
            planning_value = planning_mint.json() if planning_mint.status_code == 200 else {}
            planning_token = planning_value.get("token")
            planning_crossed = client.post("/api/plugins/mcp", headers={
                "Authorization": "Bearer " + planning_token,
            } if isinstance(planning_token, str) else {}, json={
                "jsonrpc": "2.0", "id": 8, "method": "tools/call",
                "params": {"name": "harbour_inbox_list", "arguments": {}},
            })
            from core.database import ApiToken, get_db_session
            with get_db_session() as db:
                row = db.query(ApiToken).filter(ApiToken.id == token_id).one()
                original_hash = row.token_hash
                import bcrypt
                row.token_hash = bcrypt.hashpw(b"ody_private_replacement_fixture", bcrypt.gensalt()).decode("utf-8")
                db.commit()
            replaced = client.post("/api/plugins/mcp", headers=auth, json={
                "jsonrpc": "2.0", "id": 5, "method": "tools/list", "params": {},
            })
            with get_db_session() as db:
                row = db.query(ApiToken).filter(ApiToken.id == token_id).one()
                row.token_hash = original_hash
                db.commit()
            tool_names = [item.get("name") for item in tools.json().get("result", {}).get("tools", [])]
            listed_payload = json.loads(listed.json()["result"]["content"][0]["text"])
            unified_payload = json.loads(unified.json()["result"]["content"][0]["text"])
            paused_payload = json.loads(paused.json()["result"]["content"][0]["text"])
            replay_payload = json.loads(replay.json()["result"]["content"][0]["text"])
            enriched_payload = json.loads(enriched.json()["result"]["content"][0]["text"])
            receipt_ref = next((row.get("receipt_ref") for row in enriched_payload.get("items", [])
                                if row.get("source") == "clarification"), None)
            exact = client.post("/api/plugins/mcp", headers=auth, json={
                "jsonrpc": "2.0", "id": 32, "method": "tools/call",
                "params": {"name": "harbour_inbox_list", "arguments": {"view": "receipt", "receipt_ref": receipt_ref}},
            }) if isinstance(receipt_ref, dict) else None
            exact_payload = json.loads(exact.json()["result"]["content"][0]["text"]) if exact is not None and exact.status_code == 200 else {}
            browser_value = browser.json().get("clarification", {})
            checks.update({
                "setup_ok": setup_response.status_code == 200 and login.status_code == 200,
                "config_ok": config.status_code == 200,
                "create_and_mint_ok": created.status_code == 200 and minted.status_code == 200,
                "exact_tools": tool_names == ["harbour_inbox_list", "harbour_inbox_pause", "harbour_inbox_reopen", "harbour_inbox_cancel"],
                "closed_list": listed_payload.get("count") == 1 and listed_payload.get("items", [{}])[0].get("owner") == "self",
                "unified_opt_in_closed": (
                    unified_payload.get("schema") == "odysseus.harbour_inbox_unified_list.v1"
                    and [row.get("source") for row in unified_payload.get("sources", [])] == ["clarification", "planning", "knowledge"]
                    and unified_payload.get("complete") is False
                ),
                "foreign_sid_and_local_null_excluded": (
                    foreign_create.status_code == 200
                    and isinstance(foreign_subject, str)
                    and foreign_run.run.get("clarification_id") == "clar-app-foreign-001"
                    and local_run.run.get("clarification_id") == "clar-app-local-001"
                    and [item.get("clarification_id") for item in listed_payload.get("items", [])]
                    == ["clar-app-inbox-001"]
                ),
                "pause_replay": paused_payload.get("item", {}).get("version") == 2 and replay_payload.get("idempotent_replay") is True,
                "verified_receipt_read": (
                    enriched_payload.get("schema") == "odysseus.harbour_inbox_unified_list.v2"
                    and isinstance(receipt_ref, dict) and exact is not None and exact.status_code == 200
                    and exact_payload.get("source") == "clarification"
                    and exact_payload.get("operation") == "pause"
                    and exact_payload.get("authorizes_mutation") is False
                ),
                "browser_updated": browser_value.get("version") == 2 and browser_value.get("status") == "paused",
                "planning_cross_scope_denied": crossed.json().get("error", {}).get("message") == "Invalid params",
                "inbox_cross_scope_denied": planning_crossed.json().get("error", {}).get("message") == "Invalid params",
                "live_hash_replacement_denied": replaced.status_code == 401,
                "private_files_exist": token_file.is_file() and token_id_file.is_file(),
                "restart_revocation_prepared": (
                    revoked_mint.status_code == 200 and pre_restart_revoke is not None
                    and pre_restart_revoke.status_code == 200 and revoked_file.is_file()
                ),
            })
        elif phase == "second":
            stage = "restart_login"
            login = client.post("/api/auth/login", json={"username": "fuzzy", "password": password})
            token = token_file.read_text(encoding="utf-8")
            token_id = token_id_file.read_text(encoding="utf-8")
            auth = {"Authorization": "Bearer " + token}
            revoked_token = token_file.with_name("private-revoked-inbox-token").read_text(encoding="utf-8")
            restart_denied = client.post("/api/plugins/mcp", headers={
                "Authorization": "Bearer " + revoked_token,
            }, json={"jsonrpc": "2.0", "id": 9, "method": "tools/list", "params": {}})
            reopened = client.post("/api/plugins/mcp", headers=auth, json={
                "jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {
                    "name": "harbour_inbox_reopen", "arguments": {
                        "clarification_id": "clar-app-inbox-001", "expected_version": 2,
                        "idempotency_key": "app-action-two",
                    },
                },
            })
            browser = client.get("/api/clarifications/clar-app-inbox-001")
            revoked = client.delete("/api/tokens/" + token_id, headers={"Origin": "http://testserver"})
            denied = client.post("/api/plugins/mcp", headers=auth, json={
                "jsonrpc": "2.0", "id": 7, "method": "tools/call",
                "params": {"name": "harbour_inbox_list", "arguments": {"view": "typed"}},
            })
            reopened_payload = json.loads(reopened.json()["result"]["content"][0]["text"])
            manager = application.app.state.auth_manager
            old_subject = manager.subject_id_for_username("fuzzy")
            guardian_create = client.post("/api/auth/users", headers={"Origin": "http://testserver"}, json={
                "username": "inbox-guardian", "password": "offline-inbox-guardian-password-93!",
                "is_admin": True,
            })
            guardian_login = client.post("/api/auth/login", json={
                "username": "inbox-guardian", "password": "offline-inbox-guardian-password-93!",
            })
            deleted_owner = client.request(
                "DELETE", "/api/auth/users", headers={"Origin": "http://testserver"},
                json={"username": "fuzzy"},
            )
            recreated_owner = client.post("/api/auth/users", headers={"Origin": "http://testserver"}, json={
                "username": "fuzzy", "password": password, "is_admin": True,
            })
            recreated_subject = manager.subject_id_for_username("fuzzy")
            recreated_login = client.post("/api/auth/login", json={"username": "fuzzy", "password": password})
            recreated_mint = client.post("/api/tokens", headers={"Origin": "http://testserver"}, data={
                "name": "offline-inbox-recreated-owner", "scopes": "mcp:harbour:inbox",
            })
            recreated_value = recreated_mint.json() if recreated_mint.status_code == 200 else {}
            recreated_token = recreated_value.get("token")
            recreated_list = client.post("/api/plugins/mcp", headers={
                "Authorization": "Bearer " + recreated_token,
            } if isinstance(recreated_token, str) else {}, json={
                "jsonrpc": "2.0", "id": 10, "method": "tools/call",
                "params": {"name": "harbour_inbox_list", "arguments": {"limit": 10}},
            })
            recreated_payload = json.loads(
                recreated_list.json()["result"]["content"][0]["text"]
            ) if recreated_list.status_code == 200 and "result" in recreated_list.json() else {}
            checks.update({
                "restart_login": login.status_code == 200,
                "revoked_after_restart_denied": restart_denied.status_code == 401,
                "restart_reopen": reopened_payload.get("item", {}).get("version") == 3,
                "browser_reopened": browser.json().get("clarification", {}).get("version") == 3,
                "revoke_ok": revoked.status_code == 200,
                "revoked_denied": denied.status_code == 401,
                "same_alias_delete_recreate_excludes_old_sid": (
                    guardian_create.status_code == 200 and guardian_login.status_code == 200
                    and deleted_owner.status_code == 200 and recreated_owner.status_code == 200
                    and recreated_login.status_code == 200 and recreated_mint.status_code == 200
                    and isinstance(old_subject, str) and isinstance(recreated_subject, str)
                    and old_subject != recreated_subject and recreated_list.status_code == 200
                    and recreated_payload.get("count") == 0
                    and recreated_payload.get("items") == []
                ),
            })
        else:
            checks["known_phase"] = False
    checks["no_network_attempted"] = not attempted["network"]
    checks["no_process_attempted"] = not attempted["process"]
    checks["stage"] = "complete"
    checks["ok"] = all(value is True for key, value in checks.items() if key not in {"phase", "stage"})
    result_file.write_text(json.dumps(checks, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    raise SystemExit(0 if checks["ok"] else 2)
except Exception as exc:
    result_file.write_text(json.dumps({
        "ok": False, "phase": phase, "stage": stage,
        "error_class": type(exc).__name__ if type(exc).__name__ in {
            "AssertionError", "AttributeError", "ImportError", "IndexError", "KeyError",
            "NameError", "OSError", "RuntimeError", "TimeoutError", "TypeError", "ValueError",
        } else "other",
    }, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    raise SystemExit(1)
'''


def test_actual_app_cookie_to_inbox_mcp_actions_survive_restart_and_revoke(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    profile_root, data_root = tmp_path / "profile", tmp_path / "data"
    for path in (profile_root, data_root, profile_root / "roaming", profile_root / "local", tmp_path / "temp", tmp_path / "cache"):
        path.mkdir(parents=True, exist_ok=True)
    result_file = tmp_path / "inbox-result.json"
    token_file, token_id_file = tmp_path / "private-inbox-token", tmp_path / "private-inbox-token-id"
    env = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "WINDIR") if name in os.environ}
    env.update({
        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "HOME": str(profile_root),
        "USERPROFILE": str(profile_root), "APPDATA": str(profile_root / "roaming"),
        "LOCALAPPDATA": str(profile_root / "local"), "TEMP": str(tmp_path / "temp"), "TMP": str(tmp_path / "temp"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"), "HF_HOME": str(tmp_path / "cache" / "huggingface"),
        "PYTHON_DOTENV_DISABLED": "1", "PYTHONPATH": str(repo_root), "ODYSSEUS_STARTUP_OFFLINE": "true",
        "ODYSSEUS_DATA_DIR": str(data_root), "DATABASE_URL": f"sqlite:///{(data_root / 'app.db').as_posix()}",
        "ODYSSEUS_DISABLE_MCP": "1", "ODYSSEUS_DISABLE_USER_MCP": "1", "ODYSSEUS_INPROCESS_TASKS": "0",
        "ODYSSEUS_SECURITY_PUBLIC_EGRESS_DISCOVERY_DISABLED": "true", "ODYSSEUS_SECURITY_INCIDENT_DELIVERY_ENABLED": "false",
        "ODYSSEUS_TRANSCRIPTION_ENABLED": "false", "ODYSSEUS_LEGACY_STT_ENABLED": "false",
        "ODYSSEUS_USI_RUNTIME_ENABLED": "false", "ODYSSEUS_USI_RUNTIME_MODE": "disabled",
        "AUTH_ENABLED": "true", "LOCALHOST_BYPASS": "false", "SECURE_COOKIES": "false",
        "ODYSSEUS_PLANNING_ISSUER_SECRET_B64": "eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg=",
        "ODYSSEUS_PLANNING_ISSUER_KEY_ID": "offline-test-key", "ODYSSEUS_TEST_REPO_ROOT": str(repo_root),
        "ODYSSEUS_TEST_RESULT_FILE": str(result_file), "ODYSSEUS_MCP_TEST_TOKEN_FILE": str(token_file),
        "ODYSSEUS_MCP_TEST_TOKEN_ID_FILE": str(token_id_file),
    })
    child_script = tmp_path / "inbox-app-child.py"
    with child_script.open("x", encoding="utf-8", newline="\n") as stream: stream.write(_INBOX_CHILD)
    for phase in ("first", "second"):
        env["ODYSSEUS_MCP_TEST_PHASE"] = phase
        result = subprocess.run(
            [sys.executable, str(child_script)], cwd=repo_root, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180, check=False,
        )
        assert result_file.is_file()
        projection = json.loads(result_file.read_text(encoding="utf-8"))
        assert result.returncode == 0, json.dumps(projection, sort_keys=True)
        assert projection.get("ok") is True and projection.get("stage") == "complete"


# This deliberately has its own child.  `_INBOX_CHILD` remains the accepted-C
# compatibility fixture; this one brings up the default app with all three D2
# producers present before it imports `app`.
_UNIFIED_REAL_CHILD = _TRIPWIRE + r'''
import base64
import hashlib
import hmac
import importlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import types

def _mcp_rejected(response):
    try:
        body = response.json()
        return isinstance(body.get("error"), dict) or body.get("result", {}).get("isError") is True
    except (ValueError, AttributeError, TypeError):
        return False

result_file = Path(os.environ["ODYSSEUS_TEST_RESULT_FILE"])
stage = "fixture"
selection = False
try:
    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient
    from src.constants import APP_KEY_FILE, AUTH_FILE, DATA_DIR
    from core.auth import AuthManager
    from src.causal_claim_store import CausalClaimStore
    from src.harbour_authority import AuthenticatedPrincipalIssuer
    from src.harbour_knowledge_migration import (
        CausalMigrationSource, migrate_prepared_knowledge_migration,
        prepare_current_knowledge_migration,
    )
    from src.harbour_knowledge_staging_destination import (
        HarbourKnowledgeStagingDestinationService, _CONTEXT_SCHEMA, _sha,
    )
    from src.unified_source_index_sqlite import SQLiteUnifiedSourceIndexStore
    from src.unified_source_index_sources.knowledge import knowledge_source_capability_manifest

    data = Path(DATA_DIR)
    data.mkdir(parents=True, exist_ok=True)
    key_path = Path(APP_KEY_FILE)
    key_path.write_bytes(Fernet.generate_key())
    if os.name != "nt":
        key_path.chmod(0o600)
    auth = AuthManager(str(AUTH_FILE))
    password = "real-unified-inbox-password-93!"
    assert auth.create_user("fuzzy", password, is_admin=True)
    subject = auth.subject_id_for_username("fuzzy")
    assert isinstance(subject, str) and subject.startswith("owner_")
    key = base64.urlsafe_b64decode(key_path.read_bytes())
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="harbour-knowledge-runtime", key_id="app-key-v1",
        secret=hmac.new(key, b"odysseus.harbour.knowledge.issuer.v1\0", hashlib.sha256).digest(),
    )
    causal = CausalClaimStore(sqlite3.connect(data / "real-unified-causal.sqlite"), initialize=True)
    causal.create_claim({
        "schema": "odysseus.causal_claim.v1", "claim_id": "real-unified-causal-001",
        "owner": subject, "policy": "reviewed-local", "source_version": "sha256:" + "a" * 64,
        "claim_version": 1, "candidate_actor": "fixture", "evidence_refs": ["sha256:" + "b" * 64],
    })
    retention = {"retention_class": "user_managed", "deletion_mode": "purge_eligible",
                 "retain_until": None, "grace_until": None, "legal_hold": False}
    plan = prepare_current_knowledge_migration(
        causal_sources=(CausalMigrationSource(subject, causal, subject),), owner_map={subject: subject},
        retention_by_scope={subject: retention}, observed_at="2026-10-04T12:00:00Z",
    )
    staging = HarbourKnowledgeStagingDestinationService(data / "harbour-knowledge-staging", trusted_issuers=(issuer,))
    migration_principal = issuer.issue(subject_id=subject, owner_id=subject, auth_context_digest=_sha({
        "migration_id": plan.migration_id, "owner_id": subject, "schema_id": _CONTEXT_SCHEMA,
        "snapshot_digest": plan.source_manifest_digest,
    }))
    completion = migrate_prepared_knowledge_migration(
        plan, staging=staging, principals={subject: migration_principal},
    ).completion_reference
    selection = staging.select_generation(completion, expected_selection_digest="sha256:" + "0" * 64)
    store = staging.selected_runtime(subject)[4]
    writer = issuer.issue(subject_id=subject, owner_id=subject,
                          auth_context_digest=_sha({"fixture": "real-unified-purge"}))
    store.create_object(
        writer, owner_id=subject, knowledge_id="real-unified-purge-001", knowledge_kind="note",
        payload_bytes=b"real-unified-private-content-must-not-leak", payload_schema_id="harbour.note.v1",
        payload_media_type="text/plain", classification="private", retention=retention,
        idempotency_key="real-unified-create",
    )
    first_head = store.get_head(writer, owner_id=subject, knowledge_id="real-unified-purge-001")
    store.tombstone(writer, owner_id=subject, knowledge_id="real-unified-purge-001",
                    expected_head=first_head, idempotency_key="real-unified-tombstone")
    second_head = store.get_head(writer, owner_id=subject, knowledge_id="real-unified-purge-001")
    store.request_purge(writer, owner_id=subject, knowledge_id="real-unified-purge-001",
                        expected_head=second_head, idempotency_key="real-unified-request-purge")
    SQLiteUnifiedSourceIndexStore(data / "usi.sqlite")
    os.environ.update({
        "ODYSSEUS_USI_RUNTIME_ENABLED": "true", "ODYSSEUS_USI_RUNTIME_MODE": "read_only",
        "ODYSSEUS_USI_SELECTED_GENERATION": knowledge_source_capability_manifest().generation_ref,
        "ODYSSEUS_USI_ALLOWED_OWNERS": subject, "ODYSSEUS_USI_ALLOWED_SOURCES": "real-unified-source",
        "ODYSSEUS_USI_ALLOWED_DOMAINS": "native_knowledge", "ODYSSEUS_USI_SQLITE_PATH": "usi.sqlite",
    })
    staging.close()

    stage = "app_import"
    import app as application
    from plugins.mcp_server.plugin import setup
    from src.planning_definition_contract import compute_roadmap_content_hash
    from src.planning_roadmap_migration import build_h1r03_migration_manifest, canonical_json_bytes
    class Context:
        def __init__(self, app): self.app, self.data_dir, self.logger = app, data / "mcp-plugin", logging.getLogger("real.unified")
        def add_router(self, router): self.app.include_router(router)
    def setup_plugin(): setup(Context(application.app))
    def payload(response): return json.loads(response.json()["result"]["content"][0]["text"])
    def assert_real(value):
        rendered = json.dumps(value, sort_keys=True)
        sources = value.get("sources", [])
        items = value.get("items", [])
        assert value.get("schema") == "odysseus.harbour_inbox_unified_list.v1"
        assert value.get("complete") is True and value.get("clipped") is False and value.get("count") == 3
        assert [(row.get("source"), row.get("status")) for row in sources] == [
            ("clarification", "captured_revalidated"), ("planning", "captured_revalidated"), ("knowledge", "captured_revalidated"),
        ]
        assert [(row.get("source"), row.get("signal_type")) for row in items] == [
            ("clarification", "open_clarification"), ("planning", "revision_ahead_of_approval"), ("knowledge", "purge_requested"),
        ]
        planning = next(row for row in items if row.get("source") == "planning")
        knowledge = next(row for row in items if row.get("source") == "knowledge")
        assert set(planning) == {"item_handle", "source", "owner", "signal_type", "status", "required_action",
                                 "binding_digest", "due", "receipt_handle", "redaction_flags", "current_revision", "approved_revision"}
        assert type(knowledge.get("generation")) is int and knowledge["generation"] >= 1
        assert all(row.get("owner") == "self" and row.get("item_handle", "").startswith("hi1:") for row in items)
        assert all(row.get("redaction_flags") == {"content": True, "identity": True, "events": True} for row in items)
        assert "real-unified-private-content-must-not-leak" not in rendered and subject not in rendered and token not in rendered
    def assert_typed(value, *, upload_enabled=False):
        rendered = json.dumps(value, sort_keys=True)
        assert set(value) == {"schema", "items", "count", "sources", "complete", "clipped", "authority", "freshness", "cross_source_atomic"}
        assert value["schema"] == "odysseus.harbour_inbox_unified_list.v3"
        assert value["complete"] is True and value["clipped"] is False
        expected_sources = [
            ("clarification", "captured_revalidated"), ("planning", "captured_revalidated"),
            ("knowledge", "captured_revalidated"), ("document", "captured_revalidated"),
        ]
        if upload_enabled:
            expected_sources.append(("upload", "captured_revalidated"))
        assert [(row.get("source"), row.get("status")) for row in value["sources"]] == expected_sources
        common = {"item_handle", "source", "owner", "signal_type", "status", "required_action", "due_state", "authority", "binding_digest", "receipt_ref", "redaction_flags"}
        extras = {"clarification": {"clarification_id", "expected_version", "ready_for_plan", "updated_at"}, "planning": {"current_revision", "approved_revision"}, "knowledge": {"knowledge_kind", "generation", "revision", "occurred_at"}, "document": {"current_revision"}, "upload": set()}
        assert all(set(item) == common | extras[item["source"]] for item in value["items"])
        assert all("due" not in item and "receipt_handle" not in item for item in value["items"])
        assert all(item["authority"]["access"] == "read" and item["owner"] == "self" for item in value["items"])
        upload_rows = [item for item in value["items"] if item["source"] == "upload"]
        assert len(upload_rows) == (1 if upload_enabled else 0)
        assert value["count"] == 5 + len(upload_rows)
        assert "real-unified-private-content-must-not-leak" not in rendered and subject not in rendered and token not in rendered
    setup_plugin()
    manifest = build_h1r03_migration_manifest(Path.cwd())
    census = "sha256:" + hashlib.sha256(canonical_json_bytes(manifest["structural_census"])).hexdigest()
    headers = {"Origin": "http://testserver"}
    stage = "app_actions"
    with TestClient(application.app) as client:
        assert client.post("/api/auth/login", json={"username": "fuzzy", "password": password}).status_code == 200
        prepared = client.post("/api/planning/durable/migrations/h1r03/prepare", headers=headers, json={
            "expected_batch_id": manifest["batch_id"], "expected_manifest_digest": manifest["manifest_digest"],
            "expected_structural_census_digest": census,
        })
        assert prepared.status_code == 200
        accepted = client.post("/api/planning/durable/migrations/h1r03/accept", headers=headers, json={
            "batch_id": manifest["batch_id"], "expected_manifest_digest": manifest["manifest_digest"],
            "expected_structural_census_digest": census,
            "prior_receipt_id": prepared.json()["receipt"]["receipt_id"],
        })
        assert accepted.status_code == 200 and accepted.json()["receipt"]["after_state"] == "accepted"
        roadmap_id = manifest["source_items"][0]["roadmap_id"]
        durable_path = f"/api/planning/durable/projects/harbour-one/roadmaps/{roadmap_id}"
        exported = client.get(f"/api/planning/durable/migrations/h1r03/exports/{roadmap_id}.json")
        head_response = client.get(durable_path + "/head")
        assert exported.status_code == head_response.status_code == 200
        document = json.loads(exported.content)
        roadmap = document["roadmaps"][0]
        roadmap["revision"] = 2
        roadmap["title"] = "Real unified inbox pending review"
        roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
        document["project"]["latest_approved_revision"][roadmap_id] = {
            "revision": 2, "content_hash": roadmap["content_hash"],
        }
        appended = client.post(durable_path + "/append", headers=headers, json={
            "payload_b64": base64.b64encode(canonical_json_bytes(document)).decode("ascii"),
            "expected_head": head_response.json(), "idempotency_key": "real-unified-pending-append-001",
        })
        assert appended.status_code == 200
        summary_rows = client.get("/api/planning/projects/harbour-one/roadmaps").json().get("items", [])
        summary = next((row for row in summary_rows if row.get("roadmap_id") == roadmap_id), {})
        assert summary.get("newest_revision") == 2 and summary.get("latest_approved_revision") == 1
        default_config = client.post(
            "/api/plugins/mcp/config", headers=headers,
            json={"enabled": True, "allow_planning_reads": True},
        )
        assert default_config.status_code == 200
        assert default_config.json().get("allow_upload_inbox_reads") is False
        request = {"schema": "odysseus.clarification_request.v2", "scope": "project", "intent_summary": "Real unified fixture.",
                   "questions": [], "batch": {"label": "Real", "index": 1, "total": 1, "max_visible_questions": 3}, "defaults_visible": False}
        assert client.post("/api/clarifications", headers=headers, json={"session_id": "real-unified-session", "request": request,
                   "project_slug": "real-unified-project", "clarification_id": "real-unified-clar-001"}).status_code == 200
        upload = client.post("/api/upload", files={"files": ("typed-inbox.md", b"# typed inbox private working copy\n", "text/markdown")})
        assert upload.status_code == 200
        upload_id = upload.json()["files"][0]["id"]
        working_copy = client.post("/api/universal-inbox/items/upload:" + upload_id + "/working-copy", json={})
        assert working_copy.status_code == 201
        working_copy_id = working_copy.json()["id"]
        native_upload_handler = application.app.state.upload_handler
        upload_index_path = Path(native_upload_handler.upload_dir) / "uploads.json"
        upload_index = native_upload_handler._load_upload_index()
        assert len(upload_index) == 1
        current_upload_key, current_upload_row = next(iter(upload_index.items()))
        assert current_upload_row.get("creator_subject_id") == subject
        historical_subject = "owner_" + "c" * 32
        foreign_subject = "owner_" + "d" * 32
        excluded_upload_metadata = []
        for alias, row_subject, marker in (
            ("fuzzy", historical_subject, "b"),
            ("fuzzy", None, "c"),
            ("foreign", foreign_subject, "d"),
            ("fuzzy", "owner_" + "e" * 32, "e"),
        ):
            row = dict(current_upload_row)
            file_hash = marker * 64
            row.update({
                "id": marker * 32 + ".txt", "hash": file_hash, "owner": alias,
                "creator_subject_id": row_subject, "name": "excluded-" + marker + ".txt",
                "original_name": "excluded-private-" + marker + ".txt",
                "path": "excluded-private-path-" + marker,
            })
            key = f"{alias}:{row_subject}:{file_hash}" if row_subject is not None else f"{alias}:{file_hash}"
            upload_index[key] = row
            excluded_upload_metadata.extend((key, row["id"], row["name"], row["original_name"], row["path"]))
        native_upload_handler._atomic_write_json(str(upload_index_path), upload_index)
        from core.database import Document, get_db_session
        with get_db_session() as db:
            produced = db.get(Document, working_copy_id)
            assert produced is not None and produced.creator_subject_id == subject and produced.session_id is None
            stamped_session = None
            db.add_all([
                Document(id="typed-real-inactive", creator_subject_id=subject, session_id=stamped_session, version_count=2, is_active=False, archived=False, source_kind="working_copy", source_ref_hash="sha256:" + "1" * 64),
                Document(id="typed-real-archived", creator_subject_id=subject, session_id=stamped_session, version_count=1, is_active=True, archived=True, source_kind="working_copy", source_ref_hash="sha256:" + "2" * 64),
                Document(id="typed-real-foreign", creator_subject_id="owner_" + "b" * 32, session_id=stamped_session, version_count=1, is_active=True, archived=False, source_kind="working_copy", source_ref_hash="sha256:" + "3" * 64),
                Document(id="typed-real-null-creator", creator_subject_id=None, session_id=stamped_session, version_count=1, is_active=True, archived=False, source_kind="working_copy", source_ref_hash="sha256:" + "4" * 64),
            ])
            db.commit()
        minted = client.post("/api/tokens", headers=headers, data={"name": "real-unified-inbox", "scopes": "mcp:harbour:inbox"})
        token = minted.json().get("token", "")
        assert minted.status_code == 200 and isinstance(token, str) and token
        first = client.post("/api/plugins/mcp", headers={"Authorization": "Bearer " + token}, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "harbour_inbox_list", "arguments": {"view": "unified", "limit": 10}},
        })
        assert first.status_code == 200
        assert_real(payload(first))
        typed = client.post("/api/plugins/mcp", headers={"Authorization": "Bearer " + token}, json={
            "jsonrpc": "2.0", "id": 11, "method": "tools/call",
            "params": {"name": "harbour_inbox_list", "arguments": {"view": "typed", "limit": 10}},
        })
        assert typed.status_code == 200
        assert_typed(payload(typed))
        documents = [item for item in payload(typed)["items"] if item["source"] == "document"]
        assert sorted(item["current_revision"] for item in documents) == [1, 2]
        public_document_descriptors = frozenset(
            (item["item_handle"], item["binding_digest"], item["current_revision"])
            for item in documents
        )
        assert len(public_document_descriptors) == 2
        assert working_copy_id not in json.dumps(payload(typed), sort_keys=True)

        for non_boolean in ("true", 1, [], {}):
            rejected_config = client.post(
                "/api/plugins/mcp/config", headers=headers,
                json={"allow_upload_inbox_reads": non_boolean},
            )
            assert rejected_config.status_code == 200
            assert rejected_config.json().get("allow_upload_inbox_reads") is False
        config_path = data / "mcp-plugin" / "config.json"
        persisted_non_boolean = json.loads(config_path.read_text(encoding="utf-8"))
        persisted_non_boolean["allow_upload_inbox_reads"] = "true"
        config_path.write_text(json.dumps(persisted_non_boolean, indent=2), encoding="utf-8")
        assert client.get(
            "/api/plugins/mcp/config", headers=headers,
        ).json().get("allow_upload_inbox_reads") is False
        still_disabled = client.post("/api/plugins/mcp", headers={"Authorization": "Bearer " + token}, json={
            "jsonrpc": "2.0", "id": 12, "method": "tools/call",
            "params": {"name": "harbour_inbox_list", "arguments": {"view": "typed", "limit": 10}},
        })
        assert still_disabled.status_code == 200
        assert_typed(payload(still_disabled))

        enabled_config = client.post(
            "/api/plugins/mcp/config", headers=headers,
            json={"allow_upload_inbox_reads": True},
        )
        assert enabled_config.status_code == 200
        assert enabled_config.json().get("allow_upload_inbox_reads") is True

        application.app.state.upload_handler = None
        absent = client.post("/api/plugins/mcp", headers={"Authorization": "Bearer " + token}, json={
            "jsonrpc": "2.0", "id": 13, "method": "tools/call",
            "params": {"name": "harbour_inbox_list", "arguments": {"view": "typed", "limit": 10}},
        })
        assert absent.status_code == 200
        assert_typed(payload(absent))

        application.app.state.upload_handler = object()
        broken = client.post("/api/plugins/mcp", headers={"Authorization": "Bearer " + token}, json={
            "jsonrpc": "2.0", "id": 14, "method": "tools/call",
            "params": {"name": "harbour_inbox_list", "arguments": {"view": "typed", "limit": 10}},
        })
        assert broken.status_code == 200 and _mcp_rejected(broken)
        assert '"source":"upload"' not in json.dumps(broken.json(), sort_keys=True, separators=(",", ":"))

        application.app.state.upload_handler = native_upload_handler
        enabled = client.post("/api/plugins/mcp", headers={"Authorization": "Bearer " + token}, json={
            "jsonrpc": "2.0", "id": 15, "method": "tools/call",
            "params": {"name": "harbour_inbox_list", "arguments": {"view": "typed", "limit": 10}},
        })
        assert enabled.status_code == 200
        assert_typed(payload(enabled), upload_enabled=True)
        public_upload_descriptors = frozenset(
            (item["item_handle"], item["binding_digest"])
            for item in payload(enabled)["items"] if item["source"] == "upload"
        )
        assert len(public_upload_descriptors) == 1
        enabled_rendered = json.dumps(payload(enabled), sort_keys=True)
        assert current_upload_key not in enabled_rendered
        assert all(value not in enabled_rendered for value in excluded_upload_metadata)

        native_capture = native_upload_handler.capture_inbox_snapshot

        drift_calls = [0]
        def drift_during_capture(self, **kwargs):
            drift_calls[0] += 1
            captured = native_capture(**kwargs)
            if drift_calls[0] == 1:
                changed = self._load_upload_index()
                changed[current_upload_key]["last_accessed"] = "2026-10-08T23:59:51"
                self._atomic_write_json(str(upload_index_path), changed)
            return captured
        native_upload_handler.capture_inbox_snapshot = types.MethodType(
            drift_during_capture, native_upload_handler,
        )
        drifted = client.post("/api/plugins/mcp", headers={"Authorization": "Bearer " + token}, json={
            "jsonrpc": "2.0", "id": 16, "method": "tools/call",
            "params": {"name": "harbour_inbox_list", "arguments": {"view": "typed", "limit": 10}},
        })
        del native_upload_handler.capture_inbox_snapshot
        assert drift_calls == [2] and drifted.status_code == 200 and _mcp_rejected(drifted)
        assert '"source":"upload"' not in json.dumps(drifted.json(), sort_keys=True, separators=(",", ":"))

        disable_calls = [0]
        def disable_during_capture(self, **kwargs):
            disable_calls[0] += 1
            captured = native_capture(**kwargs)
            if disable_calls[0] == 1:
                persisted = json.loads(config_path.read_text(encoding="utf-8"))
                persisted["allow_upload_inbox_reads"] = False
                config_path.write_text(json.dumps(persisted, indent=2), encoding="utf-8")
            return captured
        native_upload_handler.capture_inbox_snapshot = types.MethodType(
            disable_during_capture, native_upload_handler,
        )
        disabled_mid_request = client.post("/api/plugins/mcp", headers={"Authorization": "Bearer " + token}, json={
            "jsonrpc": "2.0", "id": 17, "method": "tools/call",
            "params": {"name": "harbour_inbox_list", "arguments": {"view": "typed", "limit": 10}},
        })
        del native_upload_handler.capture_inbox_snapshot
        assert disable_calls == [2] and disabled_mid_request.status_code == 200 and _mcp_rejected(disabled_mid_request)
        assert '"source":"upload"' not in json.dumps(disabled_mid_request.json(), sort_keys=True, separators=(",", ":"))
        assert client.post(
            "/api/plugins/mcp/config", headers=headers, json={"allow_upload_inbox_reads": True},
        ).json().get("allow_upload_inbox_reads") is True

        replacement_calls = [0]
        def replace_during_recapture(self, **kwargs):
            replacement_calls[0] += 1
            captured = native_capture(**kwargs)
            if replacement_calls[0] == 2:
                application.app.state.upload_handler = object()
            return captured
        native_upload_handler.capture_inbox_snapshot = types.MethodType(
            replace_during_recapture, native_upload_handler,
        )
        replaced_mid_request = client.post("/api/plugins/mcp", headers={"Authorization": "Bearer " + token}, json={
            "jsonrpc": "2.0", "id": 18, "method": "tools/call",
            "params": {"name": "harbour_inbox_list", "arguments": {"view": "typed", "limit": 10}},
        })
        del native_upload_handler.capture_inbox_snapshot
        application.app.state.upload_handler = native_upload_handler
        assert replacement_calls == [2] and replaced_mid_request.status_code == 200 and _mcp_rejected(replaced_mid_request)
        assert '"source":"upload"' not in json.dumps(replaced_mid_request.json(), sort_keys=True, separators=(",", ":"))

        stable_enabled = client.post("/api/plugins/mcp", headers={"Authorization": "Bearer " + token}, json={
            "jsonrpc": "2.0", "id": 19, "method": "tools/call",
            "params": {"name": "harbour_inbox_list", "arguments": {"view": "typed", "limit": 10}},
        })
        assert stable_enabled.status_code == 200
        assert_typed(payload(stable_enabled), upload_enabled=True)
        public_upload_descriptors = frozenset(
            (item["item_handle"], item["binding_digest"])
            for item in payload(stable_enabled)["items"] if item["source"] == "upload"
        )
    stage = "restart"
    import src.plugin_system as plugin_system
    plugin_system.MANAGER = None
    application = importlib.reload(application)
    setup_plugin()
    with TestClient(application.app) as client:
        assert client.post("/api/auth/login", json={"username": "fuzzy", "password": password}).status_code == 200
        persisted_config = client.get("/api/plugins/mcp/config", headers={"Origin": "http://testserver"})
        assert persisted_config.status_code == 200
        assert persisted_config.json().get("allow_upload_inbox_reads") is True
        restarted = client.post("/api/plugins/mcp", headers={"Authorization": "Bearer " + token}, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "harbour_inbox_list", "arguments": {"view": "unified", "limit": 10}},
        })
        assert restarted.status_code == 200
        assert_real(payload(restarted))
        typed_restarted = client.post("/api/plugins/mcp", headers={"Authorization": "Bearer " + token}, json={
            "jsonrpc": "2.0", "id": 12, "method": "tools/call",
            "params": {"name": "harbour_inbox_list", "arguments": {"view": "typed", "limit": 10}},
        })
        assert typed_restarted.status_code == 200
        assert_typed(payload(typed_restarted), upload_enabled=True)
        restarted_documents = [item for item in payload(typed_restarted)["items"] if item["source"] == "document"]
        assert frozenset(
            (item["item_handle"], item["binding_digest"], item["current_revision"])
            for item in restarted_documents
        ) == public_document_descriptors
        restarted_uploads = [item for item in payload(typed_restarted)["items"] if item["source"] == "upload"]
        assert frozenset(
            (item["item_handle"], item["binding_digest"])
            for item in restarted_uploads
        ) == public_upload_descriptors
    result_file.write_text(json.dumps({"ok": True, "selection": bool(selection), "stage": "complete"}, sort_keys=True), encoding="utf-8")
except Exception:
    result_file.write_text(json.dumps({
        "ok": False, "selection": False,
        "stage": stage if stage in {"fixture", "app_import", "app_actions", "restart"} else "failed",
    }, sort_keys=True), encoding="utf-8")
    raise SystemExit(1)
'''


def test_actual_default_app_unified_inbox_captures_real_sources_and_opt_in_upload_after_restart(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    profile_root, data_root = tmp_path / "profile", tmp_path / "data"
    for path in (profile_root, data_root, profile_root / "roaming", profile_root / "local", tmp_path / "temp", tmp_path / "cache"):
        path.mkdir(parents=True, exist_ok=True)
    result_file = tmp_path / "real-unified-result.json"
    env = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "WINDIR") if name in os.environ}
    env.update({
        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "HOME": str(profile_root), "USERPROFILE": str(profile_root),
        "APPDATA": str(profile_root / "roaming"), "LOCALAPPDATA": str(profile_root / "local"),
        "TEMP": str(tmp_path / "temp"), "TMP": str(tmp_path / "temp"), "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "PYTHON_DOTENV_DISABLED": "1", "PYTHONPATH": str(repo_root), "ODYSSEUS_STARTUP_OFFLINE": "true",
        "ODYSSEUS_DATA_DIR": str(data_root), "DATABASE_URL": f"sqlite:///{(data_root / 'app.db').as_posix()}",
        "ODYSSEUS_DISABLE_MCP": "1", "ODYSSEUS_DISABLE_USER_MCP": "1", "ODYSSEUS_INPROCESS_TASKS": "0",
        "ODYSSEUS_SECURITY_PUBLIC_EGRESS_DISCOVERY_DISABLED": "true", "ODYSSEUS_SECURITY_INCIDENT_DELIVERY_ENABLED": "false",
        "ODYSSEUS_TRANSCRIPTION_ENABLED": "false", "ODYSSEUS_LEGACY_STT_ENABLED": "false",
        "AUTH_ENABLED": "true", "LOCALHOST_BYPASS": "false", "SECURE_COOKIES": "false",
        "ODYSSEUS_PLANNING_ISSUER_SECRET_B64": "eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg=",
        "ODYSSEUS_PLANNING_ISSUER_KEY_ID": "real-unified-test-key", "ODYSSEUS_TEST_RESULT_FILE": str(result_file),
    })
    child = tmp_path / "real-unified-app-child.py"
    with child.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(_UNIFIED_REAL_CHILD)
    result = subprocess.run([sys.executable, str(child)], cwd=repo_root, env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=240, check=False)
    assert result_file.is_file()
    assert 0 < result_file.stat().st_size <= 256
    try:
        projection = json.loads(result_file.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        projection = None
    safe_projection = {"ok": False, "selection": False, "stage": "failed"}
    allowed_stages = {"fixture", "app_import", "app_actions", "restart", "failed", "complete"}
    if type(projection) is dict and set(projection) == {"ok", "selection", "stage"}:
        candidate_ok, candidate_selection, candidate_stage = (
            projection.get("ok"), projection.get("selection"), projection.get("stage")
        )
        if (
            type(candidate_ok) is bool
            and type(candidate_selection) is bool
            and type(candidate_stage) is str
            and candidate_stage in allowed_stages
        ):
            safe_projection = {
                "ok": candidate_ok,
                "selection": candidate_selection,
                "stage": candidate_stage,
            }
    assert result.returncode == 0
    assert safe_projection == {"ok": True, "selection": True, "stage": "complete"}


_CONTEXT_POSITIVE_IN_LIFESPAN = r'''
        stage = "flow_activation"
        configured = client.post("/api/plugins/mcp/config", headers=origin, json={
            "enabled": True, "allow_context_reads": True,
        })
        stage = "activation_status"
        assert configured.status_code == 200
        stage = "activation_response"
        assert configured.json().get("allow_context_reads") is True
        stage = "activation_enabled"
        assert application.app.state.mcp_context_plugin_enabled is True
        stage = "activation_reads"
        assert application.app.state.allow_context_reads is True
        mcp_browser = {
            "Origin": "http://testserver", "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "",
        }
        stage = "activation_token_post"
        minted = client.post("/api/tokens", headers=origin, data={
            "name": "actual-three-source-context", "scopes": "mcp:harbour:context:read",
        })
        stage = "activation_token_status"
        assert minted.status_code == 200
        stage = "activation_token_json"
        context_token_row = minted.json()
        stage = "activation_token_value"
        context_token = context_token_row["token"]
        stage = "activation_token_scope"
        assert context_token_row["scopes"] == ["mcp:harbour:context:read"]

        stage = "flow_prepare_ack"
        h7_profile_selector_available = [globals().get("h7_profile_enabled", False) is True]
        def context_rpc(method, params, rpc_id, token=context_token):
            client.cookies.delete("odysseus_session")
            try:
                request_headers = {"Authorization": "Bearer " + token}
                if method == "harbour_context_page" and h7_profile_selector_available[0]:
                    request_headers["X-Odysseus-Test-Actual-Path-Profile"] = "first-content-page-v1"
                    h7_profile_selector_available[0] = False
                return client.post(
                    "/api/plugins/mcp",
                    headers=request_headers,
                    content=json.dumps({
                        "jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params,
                    }, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                )
            finally:
                client.cookies.set("odysseus_session", cookie)

        page_bytes = 1024
        stage = "prepare_call"
        try:
            import time as _prepare_observation_clock
            prepare_observation_started = _prepare_observation_clock.monotonic()
        except Exception:
            prepare_observation_started = None
        prepared = context_rpc("harbour_context_prepare", {
            "chat_session_id": session_id, "selection": selection, "page_bytes": page_bytes,
        }, "positive-prepare")
        try:
            prepare_observation_finished = _prepare_observation_clock.monotonic()
        except Exception:
            prepare_observation_finished = None
        prepare_observation_elapsed = (
            prepare_observation_finished - prepare_observation_started
            if type(prepare_observation_started) is float
            and type(prepare_observation_finished) is float
            else None
        )
        stage = "prepare_status"
        prepare_http_status = prepared.status_code
        if type(prepare_http_status) is int:
            if prepare_http_status == 200:
                pass
            elif prepare_http_status == 400:
                stage = "prepare_status_http_400"
            elif prepare_http_status == 401:
                stage = "prepare_status_http_401"
            elif prepare_http_status == 403:
                stage = "prepare_status_http_403"
            elif prepare_http_status == 408:
                stage = "prepare_status_http_408"
            elif prepare_http_status == 413:
                stage = "prepare_status_http_413"
            elif prepare_http_status == 503:
                stage = "prepare_status_http_503"
            elif prepare_http_status == 504:
                if (
                    type(prepare_observation_elapsed) is float
                    and 0.0 <= prepare_observation_elapsed < float("inf")
                ):
                    if prepare_observation_elapsed < 45.0:
                        stage = "prepare_status_http_504_under45"
                    else:
                        stage = "prepare_status_http_504_ge45"
                else:
                    stage = "prepare_status_http_504_time_unobserved"
            else:
                stage = "prepare_status_http_other"
        else:
            stage = "prepare_status_http_other"
        if type(prepare_http_status) is int and prepare_http_status == 403:
            composition = application.app.state.mcp_context_app
            graph = composition._graph
            if graph is None:
                stage = "prepare_403_graph_absent"
            else:
                graph_valid = True
                try:
                    composition._assert_graph(graph)
                    graph.runtime._assert_pins()
                    graph.runtime._assert_graph()
                except Exception:
                    graph_valid = False
                    stage = "prepare_403_graph_invalid"
                if graph_valid:
                    single_successes = []
                    probe_open = True
                    for single_source, single_prepare_rpc_id, single_cancel_rpc_id in (
                        ("forge", "prepare-403-forge", "prepare-403-forge-cancel"),
                        ("knowledge", "prepare-403-knowledge", "prepare-403-knowledge-cancel"),
                        ("planning", "prepare-403-planning", "prepare-403-planning-cancel"),
                    ):
                        single_selection = [
                            item for item in selection if item["source"] == single_source
                        ]
                        if len(single_selection) != 1:
                            stage = "prepare_403_probe_cleanup_failed"
                            probe_open = False
                            break
                        try:
                            single_prepared = context_rpc("harbour_context_prepare", {
                                "chat_session_id": session_id,
                                "selection": single_selection,
                                "page_bytes": page_bytes,
                            }, single_prepare_rpc_id)
                            single_status = single_prepared.status_code
                            if type(single_status) is int and single_status == 403:
                                single_successes.append(False)
                                continue
                            if type(single_status) is not int or single_status != 200:
                                stage = "prepare_403_singles_status_other"
                                probe_open = False
                                break
                            single_payload = single_prepared.json()
                            if (
                                type(single_payload) is not dict
                                or set(single_payload) != {"jsonrpc", "id", "result"}
                                or single_payload["jsonrpc"] != "2.0"
                                or single_payload["id"] != single_prepare_rpc_id
                                or type(single_payload["result"]) is not dict
                                or set(single_payload["result"]) != {
                                    "request_id", "selected_count",
                                    "requires_private_confirmation",
                                }
                                or type(single_payload["result"]["request_id"]) is not str
                                or len(single_payload["result"]["request_id"]) != 45
                                or not single_payload["result"]["request_id"].startswith("r.")
                                or not all(
                                    character in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
                                    for character in single_payload["result"]["request_id"][2:]
                                )
                                or type(single_payload["result"]["selected_count"]) is not int
                                or single_payload["result"]["selected_count"] != 1
                                or single_payload["result"]["requires_private_confirmation"] is not True
                            ):
                                stage = "prepare_403_probe_cleanup_failed"
                                probe_open = False
                                break
                            single_request_id = single_payload["result"]["request_id"]
                            single_cancelled = context_rpc("harbour_context_cancel", {
                                "request_id": single_request_id,
                            }, single_cancel_rpc_id)
                            single_cancel_status = single_cancelled.status_code
                            single_cancel_payload = single_cancelled.json()
                            if (
                                type(single_cancel_status) is not int
                                or single_cancel_status != 200
                                or type(single_cancel_payload) is not dict
                                or set(single_cancel_payload) != {"jsonrpc", "id", "result"}
                                or single_cancel_payload["jsonrpc"] != "2.0"
                                or single_cancel_payload["id"] != single_cancel_rpc_id
                                or type(single_cancel_payload["result"]) is not dict
                                or set(single_cancel_payload["result"]) != {"cancelled"}
                                or single_cancel_payload["result"]["cancelled"] is not True
                            ):
                                stage = "prepare_403_probe_cleanup_failed"
                                probe_open = False
                                break
                            single_successes.append(True)
                        except Exception:
                            stage = "prepare_403_probe_cleanup_failed"
                            probe_open = False
                            break
                    if probe_open:
                        if single_successes == [True, True, True]:
                            stage = "prepare_403_singles_all_200"
                        elif single_successes == [False, False, False]:
                            stage = "prepare_403_singles_none_200_unresolved_shared_or_all_providers"
                        elif single_successes == [True, False, False]:
                            stage = "prepare_403_singles_forge_only_200"
                        elif single_successes == [False, True, False]:
                            stage = "prepare_403_singles_knowledge_only_200"
                        elif single_successes == [False, False, True]:
                            stage = "prepare_403_singles_planning_only_200"
                        elif single_successes == [True, True, False]:
                            stage = "prepare_403_singles_forge_knowledge_200"
                        elif single_successes == [True, False, True]:
                            stage = "prepare_403_singles_forge_planning_200"
                        elif single_successes == [False, True, True]:
                            stage = "prepare_403_singles_knowledge_planning_200"
        assert prepared.status_code == 200
        stage = "prepare_result"
        request_id = prepared.json()["result"]["request_id"]
        stage = "challenge_call"
        challenge_response = client.post(
            "/api/context/mcp/challenge", headers=mcp_browser, json={"request_id": request_id},
        )
        stage = "challenge_status"
        assert challenge_response.status_code == 200
        stage = "challenge_result"
        challenge_token = challenge_response.json()["token"]
        stream_command = "/mcp-context-disclose " + challenge_token
        stage = "ack_call"
        acknowledgement = client.post("/api/chat_stream", headers=mcp_browser, json={
            "message": stream_command, "session": session_id,
        })
        stage = "ack_status"
        assert acknowledgement.status_code == 200
        stage = "ack_text"
        assert b"MCP private context disclosure confirmed." in acknowledgement.content
        stage = "ack_request_id"
        assert request_id.encode("ascii") in acknowledgement.content
        stage = "ack_db"
        from core.database import ChatMessage, SessionLocal
        db = SessionLocal()
        try:
            assert db.query(ChatMessage).filter(
                ChatMessage.session_id == session_id,
                ChatMessage.role == "user",
                ChatMessage.content == stream_command,
            ).count() == 1
        finally:
            db.close()

        stage = "flow_manifest"
        manifest_items = []
        manifest_cursor = None
        manifest_pack_id = None
        for manifest_page in range(1, 20):
            manifest_arguments = {"request_id": request_id}
            if manifest_cursor is not None:
                manifest_arguments["manifest_cursor"] = manifest_cursor
            stage = "flow_manifest_call"
            if manifest_page == 1 and h7_observation_requested:
                os.environ["ODYSSEUS_H7_MANIFEST_OBSERVATION"] = ""
            try:
                manifest_response = context_rpc(
                    "harbour_context_manifest", manifest_arguments,
                    "manifest-" + str(manifest_page),
                )
            finally:
                if (
                    manifest_page == 1
                    and "ODYSSEUS_H7_MANIFEST_OBSERVATION" in os.environ
                ):
                    try:
                        from src.mcp_context_causal_observation import (
                            write_child_projection_exclusive,
                        )
                        write_child_projection_exclusive(__file__)
                    except BaseException:
                        pass
                    finally:
                        os.environ.pop("ODYSSEUS_H7_MANIFEST_OBSERVATION", None)
            stage = "flow_manifest_status"
            manifest_http_status = manifest_response.status_code
            if type(manifest_http_status) is int:
                if manifest_http_status == 200:
                    pass
                elif manifest_http_status == 401:
                    stage = "flow_manifest_http_401"
                elif manifest_http_status == 403:
                    stage = "flow_manifest_http_403"
                elif manifest_http_status == 500:
                    stage = "flow_manifest_http_500"
                else:
                    stage = "flow_manifest_http_other"
                    if manifest_http_status == 400:
                        stage = "flow_manifest_http_400"
                    elif manifest_http_status == 408:
                        stage = "flow_manifest_http_408"
                    elif manifest_http_status == 413:
                        stage = "flow_manifest_http_413"
                    elif manifest_http_status == 503:
                        stage = "flow_manifest_http_503"
                    elif manifest_http_status == 202:
                        stage = "flow_manifest_http_202"
                    else:
                        stage = "flow_manifest_http_other_int"
            else:
                stage = "flow_manifest_http_other"
                stage = "flow_manifest_http_nonint"
            assert manifest_response.status_code == 200
            stage = "flow_manifest_result"
            manifest_result = manifest_response.json()["result"]
            stage = "flow_manifest_read_only"
            assert manifest_result["read_only"] is True
            stage = "flow_manifest_authority_granted"
            assert manifest_result["authority_granted"] is False
            stage = "flow_manifest_send_performed"
            assert manifest_result["send_performed"] is False
            stage = "flow_manifest_pack"
            if manifest_pack_id is None:
                manifest_pack_id = manifest_result["pack_id"]
            assert manifest_result["pack_id"] == manifest_pack_id
            stage = "flow_manifest_items"
            manifest_items.extend(manifest_result["items"])
            stage = "flow_manifest_complete"
            if manifest_result["complete"]:
                stage = "flow_manifest_eof"
                assert manifest_result["manifest_cursor"] is None
                break
            stage = "flow_manifest_cursor"
            manifest_cursor = manifest_result["manifest_cursor"]
            assert type(manifest_cursor) is str and manifest_cursor
        else:
            stage = "flow_manifest_limit"
            raise AssertionError("MCP context manifest EOF not reached")
        stage = "flow_manifest_selection"
        assert [
            (item["source"], item["selection_ref"], item["revision_ref"])
            for item in manifest_items
        ] == [
            (item["source"], item["selection_ref"], item["revision_ref"])
            for item in selection
        ]
        stage = "flow_content"
        stage = "flow_content_setup"
        expected_by_source = {
            "forge": expected_payloads[0],
            "knowledge": expected_payloads[1],
            "planning": expected_payloads[2],
        }
        expected_byteform = {
            "forge": "git_blob",
            "knowledge": "knowledge_payload",
            "planning": "durable_revision_document",
        }
        pages_by_source = {source: bytearray() for source in expected_by_source}
        stage = "flow_content_page_count_precondition"
        expected_content_pages = sum(
            (len(payload) + page_bytes - 1) // page_bytes
            for payload in expected_by_source.values()
        )
        assert expected_content_pages > len(expected_by_source)
        source_by_index = {index: item["source"] for index, item in enumerate(selection)}
        final_end_by_source = {}
        content_cursor = None
        content_page_count = 0
        for page_number in range(1, expected_content_pages + 1):
            stage = "flow_content_rpc_call"
            arguments = {"request_id": request_id}
            if content_cursor is not None:
                arguments["content_cursor"] = content_cursor
            page_source_class = next(
                source for source in ("forge", "knowledge", "planning")
                if len(pages_by_source[source]) < len(expected_by_source[source])
            )
            page_response = None
            if globals().get("_h7i_observer") is not None:
                _h7i_observer.begin(page_number, page_source_class)
            try:
                page_response = context_rpc(
                    "harbour_context_page", arguments, "page-" + str(page_number),
                )
            finally:
                if globals().get("_h7i_observer") is not None:
                    _h7i_observer.end(
                        page_response.status_code if page_response is not None else None,
                    )
            stage = "flow_content_http_status"
            stage = (
                "flow_content_http_nonint"
                if type(page_response.status_code) is not int
                else "flow_content_http_400" if page_response.status_code == 400
                else "flow_content_http_401" if page_response.status_code == 401
                else "flow_content_http_403" if page_response.status_code == 403
                else "flow_content_http_408" if page_response.status_code == 408
                else "flow_content_http_413" if page_response.status_code == 413
                else "flow_content_http_500" if page_response.status_code == 500
                else "flow_content_http_503" if page_response.status_code == 503
                else "flow_content_http_status" if page_response.status_code == 200
                else "flow_content_http_other_int"
            )
            assert page_response.status_code == 200
            stage = "flow_content_result_decode"
            page_result = page_response.json()["result"]
            stage = "flow_content_pack_id"
            assert page_result["pack_id"] == manifest_pack_id
            stage = "flow_content_read_only"
            assert page_result["read_only"] is True
            stage = "flow_content_authority_granted"
            assert page_result["authority_granted"] is False
            stage = "flow_content_send_performed"
            assert page_result["send_performed"] is False
            stage = "flow_content_page_extract"
            page = page_result["page"]
            content_page_count += 1
            stage = "flow_content_source_index"
            source = source_by_index[page["target_index"]]
            expected_payload = expected_by_source[source]
            stage = "flow_content_base64_decode"
            content = base64.b64decode(page["content_b64"], validate=True)
            stage = "flow_content_offset"
            assert page["offset"] == len(pages_by_source[source])
            stage = "flow_content_end"
            assert page["end"] == page["offset"] + len(content)
            stage = "flow_content_total"
            assert page["total"] == len(expected_payload)
            stage = "flow_content_whole_digest"
            assert page["whole_sha256"] == "sha256:" + hashlib.sha256(expected_payload).hexdigest()
            stage = "flow_content_page_digest"
            assert page["page_sha256"] == "sha256:" + hashlib.sha256(content).hexdigest()
            stage = "flow_content_byteform"
            assert page["byteform"] == expected_byteform[source]
            stage = "flow_content_accumulate"
            pages_by_source[source].extend(content)
            final_end_by_source[source] = (page["end"], page["total"])
            stage = "flow_content_completion_read"
            if page_result["complete"]:
                stage = "flow_content_eof_cursor"
                assert page_result["content_cursor"] is None
                break
            stage = "flow_content_continuation_cursor"
            content_cursor = page_result["content_cursor"]
            assert type(content_cursor) is str and content_cursor
        else:
            stage = "flow_content_loop_exhausted"
            raise AssertionError("MCP context EOF not reached")
        stage = "flow_content_page_count"
        assert content_page_count == expected_content_pages
        stage = "flow_content_final_ends"
        assert final_end_by_source == {
            source: (len(payload), len(payload))
            for source, payload in expected_by_source.items()
        }
        stage = "flow_content_reconstruction"
        assert {source: bytes(value) for source, value in pages_by_source.items()} == expected_by_source
        if globals().get("_h7i_observer") is not None:
            assert _h7i_observer.finalize_all_success() is True

        stage = "flow_preack_replay"
        negative_prepare = context_rpc("harbour_context_prepare", {
            "chat_session_id": session_id, "selection": selection, "page_bytes": 16384,
        }, "negative-prepare")
        assert negative_prepare.status_code == 200
        negative_request_id = negative_prepare.json()["result"]["request_id"]
        pre_ack = context_rpc(
            "harbour_context_manifest", {"request_id": negative_request_id}, "pre-ack",
        )
        assert pre_ack.status_code == 403 and pre_ack.content == b""
        negative_challenge_response = client.post(
            "/api/context/mcp/challenge", headers=mcp_browser,
            json={"request_id": negative_request_id},
        )
        assert negative_challenge_response.status_code == 200
        negative_challenge_token = negative_challenge_response.json()["token"]
        other_session = session_id + "-other"
        application.session_manager.create_session(
            session_id=other_session, name="wrong MCP context chat",
            endpoint_url="http://offline.invalid", model="offline-model", owner="fuzzy",
        )
        wrong_chat = client.post("/api/chat", headers=mcp_browser, json={
            "message": "/mcp-context-disclose " + negative_challenge_token,
            "session": other_session,
        })
        assert wrong_chat.status_code == 400
        negative_command = "/mcp-context-disclose " + negative_challenge_token
        negative_ack = client.post("/api/chat_stream", headers=mcp_browser, json={
            "message": negative_command, "session": session_id,
        })
        assert negative_ack.status_code == 200
        replay = client.post("/api/chat", headers=mcp_browser, json={
            "message": negative_command, "session": session_id,
        })
        assert replay.status_code == 400
        assert context_rpc(
            "harbour_context_cancel", {"request_id": negative_request_id}, "negative-cancel",
        ).json()["result"] == {"cancelled": True}

        stage = "flow_cancel"
        cancelled_prepare = context_rpc("harbour_context_prepare", {
            "chat_session_id": session_id, "selection": selection, "page_bytes": 16384,
        }, "cancel-prepare")
        cancelled_request_id = cancelled_prepare.json()["result"]["request_id"]
        cancelled = context_rpc(
            "harbour_context_cancel", {"request_id": cancelled_request_id}, "cancel",
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["result"] == {"cancelled": True}

        stage = "flow_nonstream"
        nonstream_prepare = context_rpc("harbour_context_prepare", {
            "chat_session_id": session_id, "selection": selection, "page_bytes": 16384,
        }, "nonstream-prepare")
        nonstream_request_id = nonstream_prepare.json()["result"]["request_id"]
        nonstream_challenge = client.post(
            "/api/context/mcp/challenge", headers=mcp_browser,
            json={"request_id": nonstream_request_id},
        ).json()["token"]
        nonstream_ack = client.post("/api/chat", headers=mcp_browser, json={
            "message": "/mcp-context-disclose " + nonstream_challenge,
            "session": session_id,
        })
        assert nonstream_ack.status_code == 200
        assert nonstream_ack.json()["request_id"] == nonstream_request_id
        assert context_rpc(
            "harbour_context_cancel", {"request_id": nonstream_request_id}, "nonstream-cancel",
        ).json()["result"] == {"cancelled": True}

        stage = "flow_revoke_prepare"
        revoke_prepare = context_rpc("harbour_context_prepare", {
            "chat_session_id": session_id, "selection": selection, "page_bytes": 16384,
        }, "browser-revoke-prepare")
        revoke_request_id = revoke_prepare.json()["result"]["request_id"]
        revoke_challenge = client.post(
            "/api/context/mcp/challenge", headers=mcp_browser, json={"request_id": revoke_request_id},
        ).json()["token"]
        stage = "flow_revoke_apply"
        browser_revoke = client.post(
            "/api/context/mcp/revoke", headers=mcp_browser, json={"request_id": revoke_request_id},
        )
        assert browser_revoke.status_code == 200
        assert browser_revoke.json() == {"status": "revoked"}
        stage = "flow_revoke_replay"
        assert client.post("/api/chat", headers=mcp_browser, json={
            "message": "/mcp-context-disclose " + revoke_challenge,
            "session": session_id,
        }).status_code == 400
        stage = "flow_revoke_cancel"
        assert context_rpc(
            "harbour_context_cancel", {"request_id": revoke_request_id}, "browser-revoke-cancel",
        ).json()["result"] == {"cancelled": True}

        stage = "flow_source_drift"
        drift_prepare = context_rpc("harbour_context_prepare", {
            "chat_session_id": session_id, "selection": selection, "page_bytes": 16384,
        }, "drift-prepare")
        drift_request_id = drift_prepare.json()["result"]["request_id"]
        borrowed_sources = application.app.state.context_sources
        application.app.state.context_sources = object()
        drifted = context_rpc(
            "harbour_context_manifest", {"request_id": drift_request_id}, "source-drift",
        )
        assert drifted.status_code == 401 and drifted.content == b""
        application.app.state.context_sources = borrowed_sources
        assert context_rpc(
            "harbour_context_cancel", {"request_id": drift_request_id}, "drift-cancel",
        ).status_code == 200

        stage = "flow_graph_replace"
        composition = application.app.state.mcp_context_app
        real_graph = composition._graph
        composition._graph = object()
        replaced = context_rpc(
            "harbour_context_manifest", {"request_id": "r." + "a" * 43}, "graph-replaced",
        )
        assert replaced.status_code == 401 and replaced.content == b""
        composition._graph = real_graph

        stage = "flow_aba"
        aba_prepare = context_rpc("harbour_context_prepare", {
            "chat_session_id": session_id, "selection": selection, "page_bytes": 16384,
        }, "aba-prepare")
        aba_request_id = aba_prepare.json()["result"]["request_id"]
        aba_challenge = client.post(
            "/api/context/mcp/challenge", headers=mcp_browser, json={"request_id": aba_request_id},
        ).json()["token"]
        old_epoch = application.app.state._process_epoch
        assert client.post("/api/plugins/mcp/config", headers=origin, json={
            "enabled": True, "allow_context_reads": False,
        }).status_code == 200
        assert application.app.state.mcp_context_plugin_enabled is False
        assert client.post("/api/plugins/mcp/config", headers=origin, json={
            "enabled": True, "allow_context_reads": True,
        }).status_code == 200
        assert application.app.state._process_epoch != old_epoch
        assert context_rpc(
            "harbour_context_manifest", {"request_id": aba_request_id}, "aba-stale-id",
        ).status_code == 403
        assert client.post("/api/chat", headers=mcp_browser, json={
            "message": "/mcp-context-disclose " + aba_challenge, "session": session_id,
        }).status_code == 400

        stage = "flow_mixed_credentials"
        mixed = client.post(
            "/api/plugins/mcp",
            headers={"Authorization": "Bearer " + context_token},
            content=b"must-not-be-read",
        )
        assert mixed.status_code == 401 and mixed.content == b""

        stage = "flow_restart_seed"
        restart_prepare = context_rpc("harbour_context_prepare", {
            "chat_session_id": session_id, "selection": selection, "page_bytes": page_bytes,
        }, "restart-prepare")
        stale_request_id = restart_prepare.json()["result"]["request_id"]
        restart_challenge = client.post(
            "/api/context/mcp/challenge", headers=mcp_browser, json={"request_id": stale_request_id},
        ).json()["token"]
        assert client.post("/api/chat", headers=mcp_browser, json={
            "message": "/mcp-context-disclose " + restart_challenge, "session": session_id,
        }).status_code == 200
        assert context_rpc(
            "harbour_context_manifest", {"request_id": stale_request_id}, "restart-manifest",
        ).status_code == 200
        restart_page = context_rpc(
            "harbour_context_page", {"request_id": stale_request_id}, "restart-page",
        )
        assert restart_page.status_code == 200
        stale_cursor = restart_page.json()["result"]["content_cursor"]
        assert type(stale_cursor) is str and stale_cursor

        stage = "flow_stale_challenge_seed"
        stale_challenge_prepare = context_rpc("harbour_context_prepare", {
            "chat_session_id": session_id, "selection": selection, "page_bytes": 16384,
        }, "stale-challenge-prepare")
        stale_challenge_request_id = stale_challenge_prepare.json()["result"]["request_id"]
        stale_challenge = client.post(
            "/api/context/mcp/challenge", headers=mcp_browser,
            json={"request_id": stale_challenge_request_id},
        ).json()["token"]
        stage = "flow_token_revoke"
        assert client.delete(
            "/api/tokens/" + context_token_row["id"], headers=origin,
        ).status_code == 200
        assert context_rpc(
            "harbour_context_manifest", {"request_id": stale_request_id}, "revoked-token",
        ).status_code == 401
        stage = "flow_shutdown"
'''


_CONTEXT_RESTART_BOOTSTRAP_DOMAIN_SOURCE = r'''
_CONTEXT_FIXTURE_MAX_BYTES = 1048576
_CONTEXT_RESTART_STALE_CHALLENGE_CHARS = 43
_CONTEXT_RESTART_STALE_CURSOR_MAX_CHARS = 2048
_CONTEXT_RESTART_STALE_REQUEST_ID_CHARS = 45
_CONTEXT_RESTART_ADDED_MEMBER_MAX_BYTES = (
    len(b',"stale_challenge":""') + _CONTEXT_RESTART_STALE_CHALLENGE_CHARS
    + len(b',"stale_cursor":""') + _CONTEXT_RESTART_STALE_CURSOR_MAX_CHARS
    + len(b',"stale_request_id":""') + _CONTEXT_RESTART_STALE_REQUEST_ID_CHARS
)
assert _CONTEXT_RESTART_ADDED_MEMBER_MAX_BYTES == 2197
_CONTEXT_RESTART_MAX_BYTES = (
    _CONTEXT_FIXTURE_MAX_BYTES + _CONTEXT_RESTART_ADDED_MEMBER_MAX_BYTES
)
assert _CONTEXT_RESTART_MAX_BYTES == 1050773
_CONTEXT_RESTART_URLSAFE = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
)
_CONTEXT_RESTART_CURSOR_ALPHABET = _CONTEXT_RESTART_URLSAFE | {"."}


def _context_restart_bootstrap_write(
    path, bootstrap, selection, stale_challenge, stale_cursor, stale_request_id,
):
    assert (
        type(stale_challenge) is str
        and len(stale_challenge) == _CONTEXT_RESTART_STALE_CHALLENGE_CHARS
        and all(character in _CONTEXT_RESTART_URLSAFE for character in stale_challenge)
    )
    assert (
        type(stale_cursor) is str
        and 1 <= len(stale_cursor) <= _CONTEXT_RESTART_STALE_CURSOR_MAX_CHARS
        and all(character in _CONTEXT_RESTART_CURSOR_ALPHABET for character in stale_cursor)
    )
    assert (
        type(stale_request_id) is str
        and len(stale_request_id) == _CONTEXT_RESTART_STALE_REQUEST_ID_CHARS
        and stale_request_id.startswith("r.")
        and all(character in _CONTEXT_RESTART_URLSAFE for character in stale_request_id[2:])
    )
    value = {
        "allowed_owners": bootstrap["allowed_owners"],
        "allowed_sources": bootstrap["allowed_sources"],
        "expected_b64": bootstrap["expected_b64"],
        "generation": bootstrap["generation"],
        "selection": selection,
        "stale_challenge": stale_challenge,
        "stale_cursor": stale_cursor,
        "stale_request_id": stale_request_id,
    }
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert 0 < len(raw) <= _CONTEXT_RESTART_MAX_BYTES
    path.write_bytes(raw)


def _context_restart_bootstrap_read(path):
    assert path.is_file() and 0 < path.stat().st_size <= _CONTEXT_RESTART_MAX_BYTES
    return json.loads(path.read_text(encoding="utf-8"))
'''


_CONTEXT_POSITIVE_AFTER_LIFESPAN = r'''
    _context_restart_bootstrap_write(
        Path(os.environ["ODYSSEUS_CONTEXT_RESTART_FILE"]), bootstrap, selection,
        stale_challenge, stale_cursor, stale_request_id,
    )
'''


_CONTEXT_FIXTURE_STAGE_RANGES = (
    ("post_reload_imports", 292, 303),
    ("composition_checks", 304, 344),
    ("fixture_lifespan", 345, 347),
    ("fixture_capture", 348, 370),
    ("fixture_payload", 371, 380),
    ("fixture_write", 381, 399),
)
_CONTEXT_FIXTURE_ERROR_KINDS = frozenset({
    "assertion", "attribute", "index", "key", "os", "runtime", "type", "value", "other",
})
_context_pack_child = _context_pack_harness._CHILD
_CONTEXT_FIXTURE_MARKER = '        pack_page_bytes = 65536'
assert _context_pack_child.count(_CONTEXT_FIXTURE_MARKER) == 1
_CONTEXT_FIXTURE_PREFIX = _context_pack_child.split(_CONTEXT_FIXTURE_MARKER, 1)[0]
_context_fixture_prefix_lines = _CONTEXT_FIXTURE_PREFIX.splitlines()
assert len(_context_fixture_prefix_lines) == 380
assert tuple((start, end) for _stage, start, end in _CONTEXT_FIXTURE_STAGE_RANGES) == (
    (292, 303), (304, 344), (345, 347), (348, 370), (371, 380), (381, 399),
)
assert tuple(_context_fixture_prefix_lines[line - 1].strip() for line in (
    292, 304, 344, 345, 347, 348, 370, 371, 380,
)) == (
    "from src.context_manifest_producer import CombinedContextManifestProducer",
    "paths = [route.path for route in application.app.routes]",
    "assert forge_router._forge_context_service._local_forge is forge_router._forge_context_local_forge",
    "with TestClient(application.app) as client:",
    'cookie = client.cookies.get("odysseus_session")',
    "with CookieConfirmationAuthority(application.auth_manager).hold(cookie, session_id) as binding:",
    ")", "selection = [", "}",
)
_CONTEXT_FIXTURE_CHILD = _CONTEXT_FIXTURE_PREFIX + r'''
        fixture_value = {
            "allowed_owners": subject,
            "allowed_sources": ",".join(item.source.source_id for item in discovered.items),
            "expected_b64": [
                base64.b64encode(expected_payloads[index]).decode("ascii")
                for index in range(3)
            ],
            "generation": knowledge_source_capability_manifest().generation_ref,
            "schema": "odysseus.actual_context_fixture.v1",
            "selection": selection,
        }
        Path(os.environ["ODYSSEUS_CONTEXT_FIXTURE_FILE"]).write_text(
            json.dumps(fixture_value, sort_keys=True, separators=(",", ":")), encoding="utf-8",
        )
        Path(os.environ["ODYSSEUS_CONTEXT_FIXTURE_RESULT_FILE"]).write_text(
            json.dumps({"ok": True, "stage": "complete"}, sort_keys=True), encoding="utf-8",
        )
        phase("fixture_written")
except Exception as exc:
    error_kind = {
        AssertionError: "assertion",
        AttributeError: "attribute",
        IndexError: "index",
        KeyError: "key",
        OSError: "os",
        RuntimeError: "runtime",
        TypeError: "type",
        ValueError: "value",
    }.get(type(exc), "other")
    failure_line = 0
    trace = exc.__traceback__
    while trace is not None:
        candidate_line = trace.tb_lineno
        if trace.tb_frame.f_code.co_filename == __file__ and 292 <= candidate_line <= 399:
            failure_line = candidate_line
            break
        trace = trace.tb_next
    failure_stage = "failed"
    for candidate_stage, start_line, end_line in ''' + repr(_CONTEXT_FIXTURE_STAGE_RANGES) + r''':
        if start_line <= failure_line <= end_line:
            failure_stage = candidate_stage
            break
    Path(os.environ["ODYSSEUS_CONTEXT_FIXTURE_RESULT_FILE"]).write_text(
        json.dumps({
            "ok": False,
            "stage": failure_stage,
            "error_kind": error_kind,
            "failure_line": failure_line,
        }, sort_keys=True, separators=(",", ":")), encoding="utf-8",
    )
    raise SystemExit(1)
'''
_context_fixture_child_lines = _CONTEXT_FIXTURE_CHILD.splitlines()
assert _context_fixture_child_lines[398].strip() == 'phase("fixture_written")'
assert _context_fixture_child_lines[399].strip() == "except Exception as exc:"


_CONTEXT_READINESS_IN_LIFESPAN = r'''
            stage = "readiness"
            from src.context_manifest_producer import CombinedContextManifestProducer
            from src.context_source_app import ContextSourceServices
            from src.forge_context_metadata import ForgeContextMetadata
            from src.harbour_knowledge_active_runtime import (
                ActiveKnowledgeComposition, KnowledgeContextManifestProducer,
            )
            from src.planning_context_reader import PlanningContextManifestProducer
            from src.planning_durable_context_source import DurablePlanningContextSource
            assert type(cookie) is str and cookie
            identity = application.auth_manager.strict_session_identity(cookie)
            assert type(identity) is tuple and identity[0] == "fuzzy"
            assert type(identity[1]) is str and identity[1]
            assert application.auth_manager.active_admin_subject_identity(
                subject_id=identity[1],
            ) == identity
            sources = application.app.state.context_sources
            assert type(sources) is ContextSourceServices
            assert sources is application.context_sources
            assert type(sources.producer) is CombinedContextManifestProducer
            assert type(sources.forge) is ForgeContextMetadata
            assert type(sources.planning) is PlanningContextManifestProducer
            assert type(sources.knowledge) is KnowledgeContextManifestProducer
            assert sources.producer._forge is sources.forge
            assert sources.producer._planning is sources.planning
            assert sources.producer._knowledge is sources.knowledge
            assert sources.producer._auth_manager is application.auth_manager
            assert sources.forge._auth_manager is application.auth_manager
            assert sources.planning._auth_manager is application.auth_manager
            assert type(sources.knowledge._composition) is ActiveKnowledgeComposition
            assert sources.knowledge._composition._auth is application.auth_manager
            assert type(sources.planning._durable_source) is DurablePlanningContextSource
            assert sources.planning._durable_source is application.durable_planning_source
            assert sources.confirmation._authority._auth_manager is application.auth_manager
            assert sources.confirmation._repository._session_factory is application.SessionLocal
            assert application.app.state.context_source_status == {
                "configured": True, "runtime_verified": False, "reason": None,
            }
            composition = application.app.state.mcp_context_app
            assert composition is application.mcp_context_app
            assert composition._auth is application.auth_manager
            assert composition._session_factory is application.SessionLocal
            assert composition._sources is sources
            assert composition._started is True
            assert composition._graph is None
            assert composition._effective_gates == (False, False)
            paths = [getattr(route, "path", None) for route in application.app.routes]
            assert sum(
                getattr(route, "path", None) == "/api/plugins/mcp"
                and "POST" in (getattr(route, "methods", None) or ())
                for route in application.app.routes
            ) == 1
            assert paths.count("/api/context/mcp/challenge") == 1
            assert paths.count("/api/context/mcp/revoke") == 1
            default_config = client.get("/api/plugins/mcp/config", headers=origin)
            assert default_config.status_code == 200
            assert default_config.json().get("allow_context_reads") is False
            assert application.app.state.mcp_context_plugin_enabled is False
            assert application.app.state.allow_context_reads is False
            minted = client.post("/api/tokens", headers=origin, data={
                "name": "configured-context-readiness", "scopes": "mcp:harbour:context:read",
            })
            assert minted.status_code == 200
            token_data = minted.json()
            assert type(token_data.get("token")) is str and token_data["token"]
            assert token_data.get("scopes") == ["mcp:harbour:context:read"]
            from core.database import ApiToken, SessionLocal
            assert SessionLocal is application.SessionLocal
            db = SessionLocal()
            try:
                token_row = db.query(ApiToken).filter(ApiToken.id == token_data.get("id")).one()
                assert token_row.owner == "fuzzy"
                assert token_row.owner_subject_id == identity[1]
                assert token_row.scopes == "mcp:harbour:context:read"
                assert token_row.is_active is True
            finally:
                db.close()
'''


_FORGE_NATIVE_READ_TRIPWIRE = r'''
from contextlib import contextmanager as _forge_contextmanager
import inspect as _forge_inspect
import os as _forge_os
from pathlib import Path as _ForgePath
import re as _forge_re
import subprocess as _forge_subprocess
import types as _forge_types
import src.project_forge_local as _forge_local_module


_forge_native_git_environment = _forge_local_module._git_environment
assert type(_forge_native_git_environment) is _forge_types.FunctionType
_forge_native_git_environment_code = _forge_native_git_environment.__code__
assert type(_forge_native_git_environment_code) is _forge_types.CodeType


@_forge_contextmanager
def _forge_native_read_scope(application):
    from src.forge_context_reader import ImmutableForgeContextReader, RetainedForgeVersion
    from src.project_forge_local import LocalProjectForge
    from src.project_version_store import ProjectVersionStore

    _forge_router = getattr(application, "_project_versioning_router", None)
    _forge = getattr(_forge_router, "_forge_context_local_forge", None)
    assert type(_forge) is LocalProjectForge
    assert type(_forge.store) is ProjectVersionStore
    assert _forge.git_executable == "git"
    assert _forge._run_git.__func__ is LocalProjectForge._run_git
    _forge_local_code = LocalProjectForge._run_git.__code__
    _forge_reader_code = ImmutableForgeContextReader._run_git_stream.__code__
    _forge_path_type = type(_ForgePath())
    assert type(_forge.store.root) is _forge_path_type
    _forge_data_root = _forge_os.environ.get("ODYSSEUS_DATA_ROOT")
    assert type(_forge_data_root) is str and _forge_data_root
    _forge_root = _forge.store.root.resolve(strict=True)
    assert _forge_root == (_ForgePath(_forge_data_root) / "project_forge").resolve(strict=True)
    _forge_owner_re = _forge_re.compile(r"own_[0-9a-f]{32}")
    _forge_repo_re = _forge_re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
    _forge_hex_re = _forge_re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
    _forge_ref_re = _forge_re.compile(r"refs/odysseus/versions/pv_[0-9a-f]{32}\^\{commit\}")

    def _forge_helper_contract(_forge_helper, _forge_helper_code):
        if (
            type(_forge_helper) is not _forge_types.FunctionType
            or type(_forge_helper_code) is not _forge_types.CodeType
        ):
            return False
        _forge_current_helper = getattr(_forge_local_module, "_git_environment", None)
        if type(_forge_current_helper) is not _forge_types.FunctionType:
            return False
        return (
            _forge_helper is _forge_native_git_environment
            and _forge_helper_code is _forge_native_git_environment_code
            and _forge_helper.__code__ is _forge_native_git_environment_code
            and _forge_current_helper is _forge_native_git_environment
            and _forge_current_helper.__code__ is _forge_native_git_environment_code
        )

    assert _forge_helper_contract(
        _forge_native_git_environment, _forge_native_git_environment_code,
    )

    def _forge_linklike(_forge_path):
        assert type(_forge_path) is _forge_path_type
        _forge_is_junction = getattr(_forge_path, "is_junction", None)
        return _forge_path.is_symlink() or (
            callable(_forge_is_junction) and _forge_is_junction()
        )

    def _forge_repository(_forge_text):
        if type(_forge_text) is not str or not _forge_text:
            return None
        try:
            _forge_candidate = _ForgePath(_forge_text)
            if not _forge_candidate.is_absolute():
                return None
            _forge_resolved = _forge_candidate.resolve(strict=True)
            if _forge_resolved != _forge_candidate or str(_forge_resolved) != _forge_text:
                return None
            _forge_parts = _forge_resolved.relative_to(_forge_root).parts
            if (
                len(_forge_parts) != 5
                or _forge_parts[0] != "owners"
                or _forge_owner_re.fullmatch(_forge_parts[1]) is None
                or _forge_parts[2] != "projects"
                or _forge_repo_re.fullmatch(_forge_parts[3]) is None
                or _forge_parts[4] != "repository.git"
            ):
                return None
            _forge_probe = _forge_root
            if _forge_linklike(_forge_probe):
                return None
            for _forge_part in _forge_parts:
                _forge_probe = _forge_probe / _forge_part
                if _forge_linklike(_forge_probe):
                    return None
            _forge_markers = (
                (_forge_resolved / "HEAD", "file"),
                (_forge_resolved / "config", "file"),
                (_forge_resolved / "objects", "dir"),
                (_forge_resolved / "refs", "dir"),
            )
            if not _forge_resolved.is_dir():
                return None
            for _forge_marker, _forge_kind in _forge_markers:
                if _forge_linklike(_forge_marker):
                    return None
                if _forge_kind == "file" and not _forge_marker.is_file():
                    return None
                if _forge_kind == "dir" and not _forge_marker.is_dir():
                    return None
        except (OSError, RuntimeError, ValueError):
            return None
        return _forge_resolved

    def _forge_detached_environment(_forge_environment, _forge_helper, _forge_helper_code):
        if (
            not _forge_helper_contract(_forge_helper, _forge_helper_code)
            or type(_forge_environment) is not dict
            or len(_forge_environment) > 10
        ):
            return None
        _forge_entries = []
        for _forge_key, _forge_value in _forge_environment.items():
            if type(_forge_key) is not str or type(_forge_value) is not str:
                return None
            _forge_entries.append((_forge_key, _forge_value))
        _forge_snapshot = tuple(_forge_entries)
        _forge_optional = {
            "PATH", "SYSTEMROOT", "COMSPEC", "HOME", "USERPROFILE", "TMP", "TEMP",
        }
        _forge_mandatory = {
            "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "GIT_CONFIG_NOSYSTEM": "1",
        }
        _forge_allowed = _forge_optional | set(_forge_mandatory)
        if (
            len(_forge_snapshot) != len(_forge_environment)
            or any(_forge_key not in _forge_allowed for _forge_key, _forge_value in _forge_snapshot)
        ):
            return None
        _forge_detached = dict(_forge_snapshot)
        if any(_forge_detached.get(_forge_key) != _forge_value for _forge_key, _forge_value in _forge_mandatory.items()):
            return None
        return _forge_detached

    def _forge_local_kwargs(
        _forge_kwargs, _forge_batch, _forge_helper, _forge_helper_code,
    ):
        if type(_forge_kwargs) is not dict:
            return None
        _forge_keys = {"shell", "stdout", "stderr", "text", "encoding", "errors", "env"}
        if _forge_batch:
            _forge_keys.add("stdin")
        if not (
            set(_forge_kwargs) == _forge_keys
            and _forge_kwargs["shell"] is False
            and _forge_kwargs["stdout"] is _forge_subprocess.PIPE
            and _forge_kwargs["stderr"] is _forge_subprocess.PIPE
            and _forge_kwargs["text"] is True
            and type(_forge_kwargs["encoding"]) is str
            and _forge_kwargs["encoding"] == "utf-8"
            and type(_forge_kwargs["errors"]) is str
            and _forge_kwargs["errors"] == "replace"
            and (not _forge_batch or _forge_kwargs["stdin"] is _forge_subprocess.PIPE)
        ):
            return None
        _forge_environment = _forge_detached_environment(
            _forge_kwargs["env"], _forge_helper, _forge_helper_code,
        )
        if _forge_environment is None:
            return None
        _forge_forwarded = dict(_forge_kwargs)
        _forge_forwarded["env"] = _forge_environment
        return _forge_forwarded

    def _forge_local_schema(
        _forge_command, _forge_kwargs, _forge_repo, _forge_code, _forge_owner,
        _forge_helper=_forge_native_git_environment,
        _forge_helper_code=_forge_native_git_environment_code,
    ):
        if (
            _forge_code is not _forge_local_code
            or _forge_owner is not _forge
            or type(_forge_command) is not list
            or any(type(_forge_item) is not str for _forge_item in _forge_command)
            or _forge_command[:7] != [
                "git", "-c", "protocol.allow=never", "-c", "protocol.file.allow=always",
                "--git-dir", _forge_repo,
            ]
        ):
            return None
        _forge_suffix = _forge_command[7:]
        _forge_batch = _forge_suffix == [
            "cat-file", "--batch-check=%(objectname) %(objecttype)",
        ]
        _forge_type = (
            len(_forge_suffix) == 3
            and _forge_suffix[:2] == ["cat-file", "-t"]
            and _forge_hex_re.fullmatch(_forge_suffix[2]) is not None
        )
        _forge_ref = (
            len(_forge_suffix) == 3
            and _forge_suffix[:2] == ["rev-parse", "--verify"]
            and _forge_ref_re.fullmatch(_forge_suffix[2]) is not None
        )
        if not (_forge_batch or _forge_type or _forge_ref):
            return None
        return _forge_local_kwargs(
            _forge_kwargs, _forge_batch, _forge_helper, _forge_helper_code,
        )

    def _forge_reader_schema(_forge_command, _forge_kwargs, _forge_repo, _forge_code, _forge_owner):
        if (
            _forge_code is not _forge_reader_code
            or type(_forge_owner) is not ImmutableForgeContextReader
            or type(_forge_command) is not tuple
            or any(type(_forge_item) is not str for _forge_item in _forge_command)
            or _forge_command[:4] != (
                "git", "--no-replace-objects", "--git-dir", _forge_repo,
            )
            or type(_forge_kwargs) is not dict
            or set(_forge_kwargs) != {"shell", "stdin", "stdout", "stderr"}
            or _forge_kwargs["shell"] is not False
            or _forge_kwargs["stdin"] is not _forge_subprocess.DEVNULL
            or _forge_kwargs["stdout"] is not _forge_subprocess.PIPE
            or _forge_kwargs["stderr"] is not _forge_subprocess.DEVNULL
        ):
            return False
        _forge_suffix = _forge_command[4:]
        return (
            len(_forge_suffix) == 3
            and _forge_suffix[:2] == ("cat-file", "-t")
            and _forge_hex_re.fullmatch(_forge_suffix[2]) is not None
        ) or (
            len(_forge_suffix) == 2
            and _forge_suffix[0] == "rev-parse"
            and _forge_suffix[1].endswith("^{tree}")
            and _forge_hex_re.fullmatch(_forge_suffix[1][:-7]) is not None
        ) or (
            len(_forge_suffix) == 5
            and _forge_suffix[:4] == ("ls-tree", "-r", "-z", "-l")
            and _forge_hex_re.fullmatch(_forge_suffix[4]) is not None
        ) or (
            len(_forge_suffix) == 3
            and _forge_suffix[:2] == ("cat-file", "blob")
            and _forge_hex_re.fullmatch(_forge_suffix[2]) is not None
        )

    _forge_schema_repo = str(
        _forge_root / "owners" / ("own_" + "0" * 32) / "projects" / "native-repo" / "repository.git"
    )
    _forge_local_prefix = [
        "git", "-c", "protocol.allow=never", "-c", "protocol.file.allow=always",
        "--git-dir", _forge_schema_repo,
    ]
    _forge_valid_environment = {
        "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "GIT_CONFIG_NOSYSTEM": "1",
    }
    _forge_local_kw = {
        "shell": False, "stdout": _forge_subprocess.PIPE, "stderr": _forge_subprocess.PIPE,
        "text": True, "encoding": "utf-8", "errors": "replace",
        "env": _forge_valid_environment,
    }
    _forge_environment_probe = dict(_forge_valid_environment)
    _forge_environment_copy = _forge_detached_environment(
        _forge_environment_probe,
        _forge_native_git_environment,
        _forge_native_git_environment_code,
    )
    assert type(_forge_environment_copy) is dict
    assert _forge_environment_copy is not _forge_environment_probe
    assert _forge_environment_copy == _forge_valid_environment
    _forge_environment_probe["GIT_ASKPASS"] = "changed-after-validation"
    assert _forge_environment_copy["GIT_ASKPASS"] == ""
    _forge_optional_environment = dict(_forge_valid_environment)
    _forge_optional_environment["PATH"] = "synthetic-path"
    assert type(_forge_detached_environment(
        _forge_optional_environment,
        _forge_native_git_environment,
        _forge_native_git_environment_code,
    )) is dict
    assert _forge_detached_environment(
        None, _forge_native_git_environment, _forge_native_git_environment_code,
    ) is None
    _forge_unknown_environment = dict(_forge_valid_environment)
    _forge_unknown_environment["GIT_CONFIG_GLOBAL"] = "synthetic"
    assert _forge_detached_environment(
        _forge_unknown_environment,
        _forge_native_git_environment,
        _forge_native_git_environment_code,
    ) is None
    for _forge_missing_key in tuple(_forge_valid_environment):
        _forge_missing_environment = dict(_forge_valid_environment)
        del _forge_missing_environment[_forge_missing_key]
        assert _forge_detached_environment(
            _forge_missing_environment,
            _forge_native_git_environment,
            _forge_native_git_environment_code,
        ) is None
    for _forge_fixed_key in tuple(_forge_valid_environment):
        _forge_wrong_environment = dict(_forge_valid_environment)
        _forge_wrong_environment[_forge_fixed_key] = "wrong-fixed-value"
        assert _forge_detached_environment(
            _forge_wrong_environment,
            _forge_native_git_environment,
            _forge_native_git_environment_code,
        ) is None

    class _ForgeEnvironmentMappingSubclass(dict):
        def __len__(self):
            raise AssertionError("mapping subclass accessed")

        def items(self):
            raise AssertionError("mapping subclass accessed")

    class _ForgeEnvironmentStringSubclass(str):
        def __eq__(self, other):
            raise AssertionError("string subclass compared")

        def __hash__(self):
            raise AssertionError("string subclass hashed")

        def upper(self):
            raise AssertionError("string subclass normalized")

    assert _forge_detached_environment(
        _ForgeEnvironmentMappingSubclass(_forge_valid_environment),
        _forge_native_git_environment,
        _forge_native_git_environment_code,
    ) is None
    _forge_string_subclass_environment = dict(_forge_valid_environment)
    _forge_string_subclass_environment["PATH"] = _ForgeEnvironmentStringSubclass("synthetic")
    assert _forge_detached_environment(
        _forge_string_subclass_environment,
        _forge_native_git_environment,
        _forge_native_git_environment_code,
    ) is None
    _forge_nonstring_key_environment = dict(_forge_valid_environment)
    _forge_nonstring_key_environment[1] = "synthetic"
    assert _forge_detached_environment(
        _forge_nonstring_key_environment,
        _forge_native_git_environment,
        _forge_native_git_environment_code,
    ) is None
    _forge_nonstring_value_environment = dict(_forge_valid_environment)
    _forge_nonstring_value_environment["PATH"] = 1
    assert _forge_detached_environment(
        _forge_nonstring_value_environment,
        _forge_native_git_environment,
        _forge_native_git_environment_code,
    ) is None
    _forge_oversized_environment = {
        "SYNTHETIC_" + str(_forge_index): "value" for _forge_index in range(11)
    }
    assert _forge_detached_environment(
        _forge_oversized_environment,
        _forge_native_git_environment,
        _forge_native_git_environment_code,
    ) is None
    assert _forge_detached_environment(
        _forge_valid_environment, object(), _forge_native_git_environment_code,
    ) is None
    assert _forge_detached_environment(
        _forge_valid_environment, _forge_native_git_environment, object(),
    ) is None
    _forge_batch_kw = dict(_forge_local_kw, stdin=_forge_subprocess.PIPE)
    _forge_hex40, _forge_hex64 = "0" * 40, "0" * 64
    _forge_ref = "refs/odysseus/versions/pv_" + "0" * 32 + "^{commit}"
    for _forge_suffix, _forge_kw in (
        (["cat-file", "--batch-check=%(objectname) %(objecttype)"], _forge_batch_kw),
        (["cat-file", "-t", _forge_hex40], _forge_local_kw),
        (["cat-file", "-t", _forge_hex64], _forge_local_kw),
        (["rev-parse", "--verify", _forge_ref], _forge_local_kw),
    ):
        assert _forge_local_schema(
            _forge_local_prefix + _forge_suffix, _forge_kw, _forge_schema_repo,
            _forge_local_code, _forge,
        )
    for _forge_suffix in (
        ["fetch", "https://example.invalid/repository"], ["update-ref", _forge_ref, _forge_hex40],
        ["init", "--bare"], ["checkout", _forge_hex40], ["cat-file", "--batch"],
        ["cat-file", "-t", "0" * 39], ["cat-file", "-t", "A" * 40],
        ["rev-parse", "--verify", "refs/odysseus/versions/pv_bad^{commit}"],
        ["remote", "-v"], ["status"],
    ):
        assert _forge_local_schema(
            _forge_local_prefix + _forge_suffix, _forge_local_kw, _forge_schema_repo,
            _forge_local_code, _forge,
        ) is None
    _forge_good_local = _forge_local_prefix + ["cat-file", "-t", _forge_hex40]
    _forge_alias_environment = dict(_forge_valid_environment)
    _forge_alias_kwargs = dict(_forge_local_kw, env=_forge_alias_environment)
    _forge_forwarded_probe = _forge_local_schema(
        _forge_good_local, _forge_alias_kwargs, _forge_schema_repo,
        _forge_local_code, _forge,
    )
    assert type(_forge_forwarded_probe) is dict
    assert _forge_forwarded_probe is not _forge_alias_kwargs
    assert type(_forge_forwarded_probe["env"]) is dict
    assert _forge_forwarded_probe["env"] is not _forge_alias_environment
    _forge_alias_environment["GIT_ASKPASS"] = "changed-after-admission"
    assert _forge_forwarded_probe["env"]["GIT_ASKPASS"] == ""
    assert _forge_local_schema(tuple(_forge_good_local), _forge_local_kw, _forge_schema_repo, _forge_local_code, _forge) is None
    assert _forge_local_schema(_forge_local_prefix + ["cat-file", "-t", object()], _forge_local_kw, _forge_schema_repo, _forge_local_code, _forge) is None
    for _forge_bad_kw in (
        dict(_forge_local_kw, shell=True), dict(_forge_local_kw, cwd=str(_forge_root)),
        dict(_forge_local_kw, executable="git"),
    ):
        assert _forge_local_schema(_forge_good_local, _forge_bad_kw, _forge_schema_repo, _forge_local_code, _forge) is None
    assert _forge_local_schema(_forge_good_local, _forge_local_kw, _forge_schema_repo, object(), _forge) is None
    assert _forge_local_schema(_forge_good_local, _forge_local_kw, _forge_schema_repo, _forge_local_code, object()) is None
    assert _forge_local_schema(
        _forge_good_local, _forge_local_kw, _forge_schema_repo, _forge_local_code, _forge,
        object(), _forge_native_git_environment_code,
    ) is None
    assert _forge_local_schema(
        _forge_good_local, _forge_local_kw, _forge_schema_repo, _forge_local_code, _forge,
        _forge_native_git_environment, object(),
    ) is None

    _forge_reader = object.__new__(ImmutableForgeContextReader)
    _forge_reader_prefix = ("git", "--no-replace-objects", "--git-dir", _forge_schema_repo)
    _forge_reader_kw = {
        "shell": False, "stdin": _forge_subprocess.DEVNULL,
        "stdout": _forge_subprocess.PIPE, "stderr": _forge_subprocess.DEVNULL,
    }
    for _forge_suffix in (
        ("cat-file", "-t", _forge_hex40), ("rev-parse", _forge_hex40 + "^{tree}"),
        ("ls-tree", "-r", "-z", "-l", _forge_hex64), ("cat-file", "blob", _forge_hex64),
    ):
        assert _forge_reader_schema(
            _forge_reader_prefix + _forge_suffix, _forge_reader_kw, _forge_schema_repo,
            _forge_reader_code, _forge_reader,
        )
    _forge_good_reader = _forge_reader_prefix + ("cat-file", "-t", _forge_hex40)
    for _forge_bad in (
        list(_forge_good_reader), _forge_reader_prefix + ("cat-file", "blob", "0" * 39),
        _forge_reader_prefix + ("fetch", "origin"), _forge_reader_prefix + ("cat-file", "--batch"),
    ):
        assert not _forge_reader_schema(_forge_bad, _forge_reader_kw, _forge_schema_repo, _forge_reader_code, _forge_reader)
    for _forge_bad_kw in (
        dict(_forge_reader_kw, shell=True), dict(_forge_reader_kw, cwd=str(_forge_root)),
        dict(_forge_reader_kw, executable="git"),
    ):
        assert not _forge_reader_schema(_forge_good_reader, _forge_bad_kw, _forge_schema_repo, _forge_reader_code, _forge_reader)
    assert not _forge_reader_schema(_forge_good_reader, _forge_reader_kw, _forge_schema_repo, object(), _forge_reader)
    assert not _forge_reader_schema(_forge_good_reader, _forge_reader_kw, _forge_schema_repo, _forge_reader_code, object())
    assert _forge_repository(str(_forge_root.parent)) is None
    assert _forge_repository(str(_forge_root / "owners" / ".." / "repository.git")) is None
    assert _forge_repository(object()) is None

    _forge_inherited_guard = _forge_subprocess.Popen.__init__
    assert _forge_inherited_guard is guarded_process
    _forge_native_init = _popen_init

    def _forge_origin():
        _forge_frame = _forge_inspect.currentframe()
        try:
            _forge_frame = _forge_frame.f_back
            for _forge_unused in range(16):
                if _forge_frame is None:
                    return None
                if _forge_frame.f_code is _forge_local_code:
                    _forge_locals = _forge_frame.f_locals
                    return (
                        "local", _forge_frame.f_code, _forge_locals.get("self"),
                        _forge_locals.get("source_path"), _forge_locals.get("store_path"),
                        _forge_locals.get("allow_failure"), _forge_locals.get("integrity_check"),
                        _forge_locals.get("input_text"),
                    )
                if _forge_frame.f_code is _forge_reader_code:
                    return ("reader", _forge_frame.f_code, _forge_frame.f_locals.get("self"))
                _forge_frame = _forge_frame.f_back
            return None
        finally:
            del _forge_frame

    def _forge_extended_guard(_forge_process, *_forge_args, **_forge_kwargs):
        if type(_forge_process) is _forge_subprocess.Popen and type(_forge_args) is tuple and len(_forge_args) == 1:
            _forge_info = _forge_origin()
            _forge_command = _forge_args[0]
            if _forge_info is not None and _forge_info[0] == "local" and type(_forge_info[4]) is _forge_path_type:
                _forge_repo_path = _forge_repository(str(_forge_info[4]))
                _forge_batch = (
                    type(_forge_command) is list and _forge_command[-2:] == [
                        "cat-file", "--batch-check=%(objectname) %(objecttype)",
                    ]
                )
                _forge_forwarded_kwargs = None
                if (
                    _forge_repo_path is not None
                    and _forge_info[3] is None
                    and _forge_info[5] is _forge_batch
                    and _forge_info[6] is True
                    and (type(_forge_info[7]) is str if _forge_batch else _forge_info[7] is None)
                ):
                    _forge_forwarded_kwargs = _forge_local_schema(
                        _forge_command, _forge_kwargs, str(_forge_repo_path), _forge_info[1], _forge_info[2],
                    )
                if type(_forge_forwarded_kwargs) is dict:
                    return _forge_native_init(
                        _forge_process, *_forge_args, **_forge_forwarded_kwargs,
                    )
            elif _forge_info is not None and _forge_info[0] == "reader" and type(_forge_info[2]) is ImmutableForgeContextReader:
                _forge_retained = getattr(_forge_info[2], "_retained", None)
                if type(_forge_retained) is RetainedForgeVersion and type(_forge_retained.repository) is _forge_path_type:
                    _forge_repo_path = _forge_repository(str(_forge_retained.repository))
                    if _forge_repo_path is not None and _forge_reader_schema(
                        _forge_command, _forge_kwargs, str(_forge_repo_path), _forge_info[1], _forge_info[2],
                    ):
                        return _forge_native_init(_forge_process, *_forge_args, **_forge_kwargs)
        return _forge_inherited_guard(_forge_process, *_forge_args, **_forge_kwargs)

    assert _forge_subprocess.Popen.__init__ is _forge_inherited_guard
    _forge_subprocess.Popen.__init__ = _forge_extended_guard
    try:
        yield
    finally:
        _forge_current_guard = _forge_subprocess.Popen.__init__
        _forge_subprocess.Popen.__init__ = _forge_inherited_guard
        assert _forge_current_guard is _forge_extended_guard
        assert _forge_subprocess.Popen.__init__ is _forge_inherited_guard
'''


_H7_CONTENT_FAILURE_CLASSIFICATION_SOURCE = r'''
from importlib import import_module as _h7c_import_module
from contextvars import ContextVar as _H7CContextVar
from types import CodeType as _H7CCodeType, FrameType as _H7CFrameType, FunctionType as _H7CFunctionType, ModuleType as _H7CModuleType, TracebackType as _H7CTracebackType
import json as _h7c_json
import sys as _h7c_sys
import threading as _h7c_threading

_H7C_SCHEMA = "odysseus.h7_content_exception_phase.v2"
_H7C_HEADER = b"x-odysseus-test-content-failure-classification"
_H7C_SELECTOR = b"content-pages-v1"
_H7C_MAXIMUM = 4096
_H7C_MAX_EXCEPTIONS = 8
_H7C_MAX_TRACEBACK_NODES = 16
_H7C_TOOL_IDS = (3, 4)
_H7C_TOOL_NAME = "odysseus.h7.local.exception.frontier.v2"
_H7C_OUTER_HANDLER_OFFSET = 1842
_H7C_BOUNDARY_LAYOUTS = {
    "coordinator": (2050, 1215, 1842),
    "runtime_existing": (25974, 2254, 24794),
    "runtime_handle": (1794, 3339, 1224),
    "transport_handle": (3316, 641, 2872),
}
_H7C_ENTRY_LAYOUTS = {
    "coordinator": (2050, 1215),
    "app_fixed_response": (32, 835),
}
_H7C_FRONTIER_STATUSES = frozenset({"exact", "unresolved", "invalid"})
_H7C_COORDINATOR_ENTRIES = frozenset({"absent", "present", "invalid"})
_H7C_GATE_FAMILIES = frozenset({
    "app_graph", "transport_pre_runtime", "transport_runtime_dispatch",
    "runtime_entry", "runtime_record_identity", "pristine_intent_pins",
    "binding_lease_recheck", "native_lease_state", "publication_open",
    "coordinator", "postpublication_validation", "publication_exit", "cleanup",
    "unknown",
})
_H7C_FRONTIER_LINES = {
    "runtime_existing": (
        (2263, 2265, "runtime_entry"),
        (2265, 2354, "pristine_intent_pins"),
        (2354, 2430, "runtime_record_identity"),
        (2430, 2460, "pristine_intent_pins"),
        (2460, 2526, "runtime_record_identity"),
        (2526, 3046, "binding_lease_recheck"),
        (3141, 3157, "publication_open"),
        (3157, 3202, "native_lease_state"),
        (3202, 3210, "binding_lease_recheck"),
        (3210, 3234, "publication_open"),
        (3234, 3242, "postpublication_validation"),
        (3242, 3257, "publication_exit"),
        (3280, 3334, "cleanup"),
    ),
    "runtime_handle": ((3339, 3385, "runtime_entry"),),
    "transport_handle": (
        (641, 820, "transport_pre_runtime"),
        (820, 838, "transport_runtime_dispatch"),
        (838, 858, "transport_pre_runtime"),
    ),
}
_H7C_PHASES = frozenset({
    "permit_issuance", "selection_validation", "cursor_verification",
    "forge_publication", "planning_publication", "knowledge_publication",
    "inner_page_contract", "content_cursor_issuance", "outer_response_encoding",
    "final_freshness_validation", "finish",
})
_H7C_CHAIN_STATUSES = frozenset({
    "complete", "unknown_site", "ambiguous", "cycle", "truncated", "invalid",
})
_H7C_SITES = frozenset({
    "permit_issue", "selection_validate", "cursor_verify", "forge_dispatch",
    "planning_dispatch", "knowledge_dispatch", "inner_page_contract",
    "content_cursor_issue", "outer_response_encode", "final_freshness_validate",
    "finish", "planning_execution", "planning_failure", "planning_consumption",
    "native_execution", "native_recovery", "native_consumption",
})
_h7c_request_owner = _H7CContextVar("h7_content_exception_boundary_owner", default=None)

_H7C_BASE_EXCEPTION = BaseException
_H7C_EXCEPTION_DESCRIPTORS = tuple(
    BaseException.__dict__[name] for name in ("__traceback__", "__cause__", "__context__")
)
_H7C_TRACEBACK_DESCRIPTORS = tuple(
    _H7CTracebackType.__dict__[name] for name in ("tb_frame", "tb_lasti", "tb_next")
)
_H7C_FRAME_CODE_DESCRIPTOR = _H7CFrameType.__dict__["f_code"]
_H7C_EXCEPTION_DESCRIPTOR_GET = type(_H7C_EXCEPTION_DESCRIPTORS[0]).__get__
_H7C_TRACEBACK_DESCRIPTOR_TYPES = tuple(
    type(descriptor) for descriptor in _H7C_TRACEBACK_DESCRIPTORS
)
_H7C_TRACEBACK_DESCRIPTOR_GETTERS = tuple(
    descriptor_type.__get__ for descriptor_type in _H7C_TRACEBACK_DESCRIPTOR_TYPES
)
_H7C_FRAME_DESCRIPTOR_GET = type(_H7C_FRAME_CODE_DESCRIPTOR).__get__

# The ranges are CPython 3.14 instruction offsets reconstructed from the pinned
# e044d29e coordinator source.  The sole monitored handler target is the target
# of the two outer try-region exception-table entries: 1842.
_H7C_COORDINATOR_RANGES = (
    (24, 288, "selection_validation", "selection_validate"),
    (288, 338, "permit_issuance", "permit_issue"),
    (338, 504, "selection_validation", "selection_validate"),
    (504, 876, "cursor_verification", "cursor_verify"),
    (876, 1022, "selection_validation", "selection_validate"),
    (1022, 1072, "forge_publication", "forge_dispatch"),
    (1074, 1140, "planning_publication", "planning_dispatch"),
    (1140, 1206, "knowledge_publication", "knowledge_dispatch"),
    (1206, 1226, "selection_validation", "selection_validate"),
    (1226, 1478, "inner_page_contract", "inner_page_contract"),
    (1612, 1670, "content_cursor_issuance", "content_cursor_issue"),
    (1714, 1738, "outer_response_encoding", "outer_response_encode"),
    (1738, 1812, "final_freshness_validation", "final_freshness_validate"),
    (1812, 1842, "finish", "finish"),
)
_H7C_PLANNING_RANGES = (
    (12, 304, None, "planning_execution"),
    (306, 330, None, "planning_consumption"),
    (332, 478, None, "planning_failure"),
    (484, 532, None, "planning_failure"),
)
_H7C_NATIVE_RANGES = (
    (20, 478, None, "native_execution"),
    (480, 506, None, "native_consumption"),
    (582, 746, None, "native_recovery"),
    (752, 800, None, "native_recovery"),
)

def _h7c_identity_get(mapping, value):
    entry = mapping.get(id(value))
    return entry[1] if entry is not None and entry[0] is value else None

def _h7c_nested_code(function, name):
    if type(function) is not _H7CFunctionType:
        return None
    matches = tuple(
        value for value in function.__code__.co_consts
        if type(value) is _H7CCodeType and value.co_name == name
    )
    return matches[0] if len(matches) == 1 else None

def _h7c_code_entry(function):
    return function.__code__ if type(function) is _H7CFunctionType else None

def _h7c_ranges_from_lines(code, contracts):
    if type(code) is not _H7CCodeType or type(contracts) is not tuple:
        return None
    ranges = []
    try:
        for start, end, line in code.co_lines():
            if line is None or start == end:
                continue
            matches = tuple(
                family for first, stop, family in contracts if first <= line < stop
            )
            if len(matches) > 1:
                return None
            if not matches:
                continue
            family = matches[0]
            if family not in _H7C_GATE_FAMILIES:
                return None
            if ranges and ranges[-1][1] == start and ranges[-1][2] == family:
                ranges[-1] = (ranges[-1][0], end, family)
            else:
                ranges.append((start, end, family))
    except BaseException:
        return None
    return tuple(ranges) if ranges else None

def _h7c_build_bindings(importer=_h7c_import_module):
    valid = True
    try:
        app_module = importer("src.mcp_context_app")
        coordinator = importer("src.mcp_context_pack_coordinator")
        native = importer("src.mcp_context_native_page")
        planning = importer("src.mcp_context_planning_page")
        runtime = importer("src.mcp_context_request_runtime")
        transport = importer("src.mcp_context_request_transport")
        if not all(type(module) is _H7CModuleType for module in (
            app_module, coordinator, native, planning, runtime, transport,
        )):
            raise TypeError()
        coordinator_class = coordinator.__dict__.get("McpContextPackCoordinator")
        coordinator_namespace = type.__getattribute__(coordinator_class, "__dict__")
        coordinator_function = coordinator_namespace.get("publish_content")
        runtime_class = runtime.__dict__.get("McpContextRequestRuntime")
        runtime_namespace = type.__getattribute__(runtime_class, "__dict__")
        runtime_existing_function = runtime_namespace.get("_existing")
        runtime_handle_function = runtime_namespace.get("handle")
        transport_class = transport.__dict__.get("McpContextRequestTransport")
        transport_namespace = type.__getattribute__(transport_class, "__dict__")
        transport_handle_function = transport_namespace.get("handle")
        app_class = app_module.__dict__.get("McpContextApp")
        app_namespace = type.__getattribute__(app_class, "__dict__")
        app_fixed_descriptor = app_namespace.get("_fixed_transport_response")
        app_fixed_function = staticmethod.__get__(app_fixed_descriptor, None, app_class)
        native_function = native.__dict__.get("_publish")
        planning_function = planning.__dict__.get("publish_planning_page")
        coordinator_code = _h7c_code_entry(coordinator_function)
        runtime_existing_code = _h7c_code_entry(runtime_existing_function)
        runtime_handle_code = _h7c_code_entry(runtime_handle_function)
        transport_handle_code = _h7c_code_entry(transport_handle_function)
        app_fixed_code = _h7c_code_entry(app_fixed_function)
        native_code = _h7c_code_entry(native_function)
        planning_code = _h7c_code_entry(planning_function)
        native_nested = _h7c_nested_code(native_function, "encode_detached")
        planning_nested = _h7c_nested_code(planning_function, "encode_detached")
        ignored = tuple(
            code for code in (
                _h7c_code_entry(native.__dict__.get("_fail")),
                _h7c_code_entry(planning.__dict__.get("_fail")),
            ) if type(code) is _H7CCodeType
        )
        if (
            type(coordinator_code) is not _H7CCodeType
            or any(type(code) is not _H7CCodeType for code in (
                runtime_existing_code, runtime_handle_code, transport_handle_code,
                app_fixed_code, native_code,
            ))
            or type(planning_code) is not _H7CCodeType
            or type(native_nested) is not _H7CCodeType
            or type(planning_nested) is not _H7CCodeType
            or len(ignored) != 2
            or _H7C_OUTER_HANDLER_OFFSET >= len(coordinator_code.co_code)
        ):
            valid = False
    except BaseException:
        coordinator_code = native_code = planning_code = None
        runtime_existing_code = runtime_handle_code = transport_handle_code = None
        app_fixed_code = None
        native_nested = planning_nested = None
        ignored = ()
        valid = False
    maps = {}
    for code, ranges in (
        (coordinator_code, _H7C_COORDINATOR_RANGES),
        (native_code, _H7C_NATIVE_RANGES),
        (planning_code, _H7C_PLANNING_RANGES),
        (native_nested, ((0, len(native_nested.co_code) if type(native_nested) is _H7CCodeType else 0, None, "native_execution"),)),
        (planning_nested, ((0, len(planning_nested.co_code) if type(planning_nested) is _H7CCodeType else 0, None, "planning_execution"),)),
    ):
        if type(code) is not _H7CCodeType:
            valid = False
            continue
        existing = maps.get(id(code))
        if existing is not None and existing[0] is not code:
            valid = False
        maps[id(code)] = (code, ranges)
    ignored_map = {id(code): (code, True) for code in ignored}
    boundary_codes = {
        "coordinator": coordinator_code,
        "runtime_existing": runtime_existing_code,
        "runtime_handle": runtime_handle_code,
        "transport_handle": transport_handle_code,
    }
    entry_codes = {"coordinator": coordinator_code, "app_fixed_response": app_fixed_code}
    frontier_maps = {}
    for label, code in boundary_codes.items():
        layout = _H7C_BOUNDARY_LAYOUTS[label]
        if (
            type(code) is not _H7CCodeType
            or len(code.co_code) != layout[0]
            or code.co_firstlineno != layout[1]
        ):
            valid = False
            continue
        if label != "coordinator":
            ranges = _h7c_ranges_from_lines(code, _H7C_FRONTIER_LINES[label])
            if ranges is None:
                valid = False
                continue
            frontier_maps[id(code)] = (code, ranges)
    for label, code in entry_codes.items():
        layout = _H7C_ENTRY_LAYOUTS[label]
        if (
            type(code) is not _H7CCodeType
            or len(code.co_code) != layout[0]
            or code.co_firstlineno != layout[1]
        ):
            valid = False
    if len({id(code) for code in boundary_codes.values() if type(code) is _H7CCodeType}) != 4:
        valid = False
    if len({id(code) for code in entry_codes.values() if type(code) is _H7CCodeType}) != 2:
        valid = False
    return boundary_codes, entry_codes, maps, frontier_maps, ignored_map, valid

def _h7c_descriptors_intact():
    try:
        traceback_descriptors = tuple(
            _H7CTracebackType.__dict__[name] for name in ("tb_frame", "tb_lasti", "tb_next")
        )
        return (
            tuple(BaseException.__dict__[name] for name in ("__traceback__", "__cause__", "__context__"))
                == _H7C_EXCEPTION_DESCRIPTORS
            and type(traceback_descriptors) is tuple
            and type(_H7C_TRACEBACK_DESCRIPTORS) is tuple
            and type(_H7C_TRACEBACK_DESCRIPTOR_TYPES) is tuple
            and type(_H7C_TRACEBACK_DESCRIPTOR_GETTERS) is tuple
            and len(traceback_descriptors) == len(_H7C_TRACEBACK_DESCRIPTORS)
                == len(_H7C_TRACEBACK_DESCRIPTOR_TYPES)
                == len(_H7C_TRACEBACK_DESCRIPTOR_GETTERS) == 3
            and all(
                fresh is cached
                and type(fresh) is descriptor_type
                and descriptor_type.__get__ is getter
                for fresh, cached, descriptor_type, getter in zip(
                    traceback_descriptors,
                    _H7C_TRACEBACK_DESCRIPTORS,
                    _H7C_TRACEBACK_DESCRIPTOR_TYPES,
                    _H7C_TRACEBACK_DESCRIPTOR_GETTERS,
                )
            )
            and _H7CFrameType.__dict__["f_code"] is _H7C_FRAME_CODE_DESCRIPTOR
            and type(_H7C_EXCEPTION_DESCRIPTORS[0]).__get__ is _H7C_EXCEPTION_DESCRIPTOR_GET
            and type(_H7C_FRAME_CODE_DESCRIPTOR).__get__ is _H7C_FRAME_DESCRIPTOR_GET
        )
    except BaseException:
        return False

def _h7c_exception_field(exception, index):
    return _H7C_EXCEPTION_DESCRIPTOR_GET(
        _H7C_EXCEPTION_DESCRIPTORS[index], exception, _H7C_BASE_EXCEPTION,
    )

def _h7c_traceback_field(traceback, index):
    return _H7C_TRACEBACK_DESCRIPTOR_GETTERS[index](
        _H7C_TRACEBACK_DESCRIPTORS[index], traceback, _H7CTracebackType,
    )

def _h7c_frame_code(frame):
    return _H7C_FRAME_DESCRIPTOR_GET(_H7C_FRAME_CODE_DESCRIPTOR, frame, _H7CFrameType)

def _h7c_site_for(code_maps, code, offset):
    ranges = _h7c_identity_get(code_maps, code)
    if ranges is None or type(offset) is not int:
        return None
    for start, end, phase, site in ranges:
        if start <= offset < end:
            return phase, site
    return None

def _h7c_exception_projection(exception, code_maps, ignored_codes):
    phase = None
    source_chain = []
    status = "complete"
    exception_seen = set()
    current = exception
    try:
        if not _h7c_descriptors_intact():
            raise TypeError()
        for exception_index in range(_H7C_MAX_EXCEPTIONS):
            if not isinstance(current, _H7C_BASE_EXCEPTION) or id(current) in exception_seen:
                status = "cycle" if id(current) in exception_seen else "invalid"
                break
            exception_seen.add(id(current))
            traceback = _h7c_exception_field(current, 0)
            traceback_seen = set()
            selected = None
            unknown = False
            for _node_index in range(_H7C_MAX_TRACEBACK_NODES):
                if traceback is None:
                    break
                if type(traceback) is not _H7CTracebackType or id(traceback) in traceback_seen:
                    status = "cycle" if id(traceback) in traceback_seen else "invalid"
                    break
                traceback_seen.add(id(traceback))
                frame = _h7c_traceback_field(traceback, 0)
                offset = _h7c_traceback_field(traceback, 1)
                if type(frame) is not _H7CFrameType or type(offset) is not int:
                    status = "invalid"
                    break
                code = _h7c_frame_code(frame)
                if type(code) is not _H7CCodeType:
                    status = "invalid"
                    break
                mapped = _h7c_site_for(code_maps, code, offset)
                if mapped is not None:
                    mapped_phase, site = mapped
                    if mapped_phase is not None:
                        phase = phase or mapped_phase
                    effective_phase = mapped_phase or phase
                    if effective_phase in _H7C_PHASES and site in _H7C_SITES:
                        selected = {"phase": effective_phase, "site": site}
                    else:
                        unknown = True
                elif _h7c_identity_get(ignored_codes, code) is None:
                    unknown = True
                traceback = _h7c_traceback_field(traceback, 2)
            else:
                if traceback is not None:
                    status = "truncated"
            if status in {"invalid", "cycle", "truncated"}:
                break
            if selected is not None:
                source_chain.append(selected)
            if unknown and status == "complete":
                status = "unknown_site"
            cause = _h7c_exception_field(current, 1)
            context = _h7c_exception_field(current, 2)
            if cause is not None and not isinstance(cause, _H7C_BASE_EXCEPTION):
                status = "invalid"
                break
            if context is not None and not isinstance(context, _H7C_BASE_EXCEPTION):
                status = "invalid"
                break
            if cause is not None and context is not None and cause is not context:
                status = "ambiguous"
                break
            following = cause if cause is not None else context
            if following is None:
                break
            if id(following) in exception_seen:
                status = "cycle"
                break
            current = following
        else:
            status = "truncated"
        if phase is not None and phase not in _H7C_PHASES:
            status = "invalid"
        if status not in _H7C_CHAIN_STATUSES or len(source_chain) > _H7C_MAX_EXCEPTIONS:
            status = "invalid"
        return phase, status, source_chain
    except BaseException:
        return phase if phase in _H7C_PHASES else None, "invalid", source_chain[:_H7C_MAX_EXCEPTIONS]
    finally:
        current = exception = None

def _h7c_frontier_projection(exception, expected_code, frontier_maps):
    matches = []
    traceback_seen = set()
    try:
        if not _h7c_descriptors_intact() or type(expected_code) is not _H7CCodeType:
            raise TypeError()
        traceback = _h7c_exception_field(exception, 0)
        for _node_index in range(_H7C_MAX_TRACEBACK_NODES):
            if traceback is None:
                break
            if type(traceback) is not _H7CTracebackType or id(traceback) in traceback_seen:
                return "invalid", "unknown"
            traceback_seen.add(id(traceback))
            frame = _h7c_traceback_field(traceback, 0)
            offset = _h7c_traceback_field(traceback, 1)
            if type(frame) is not _H7CFrameType or type(offset) is not int:
                return "invalid", "unknown"
            code = _h7c_frame_code(frame)
            if code is expected_code:
                ranges = _h7c_identity_get(frontier_maps, code)
                if ranges is None:
                    return "unresolved", "unknown"
                mapped = tuple(family for start, end, family in ranges if start <= offset < end)
                if len(mapped) != 1:
                    return ("invalid", "unknown") if len(mapped) > 1 else ("unresolved", "unknown")
                matches.append(mapped[0])
            traceback = _h7c_traceback_field(traceback, 2)
        else:
            if traceback is not None:
                return "invalid", "unknown"
        if len(matches) != 1:
            return ("invalid", "unknown") if len(matches) > 1 else ("unresolved", "unknown")
        return "exact", matches[0]
    except BaseException:
        return "invalid", "unknown"
    finally:
        exception = None

def _h7c_clean_headers(headers):
    clean, selected = [], False
    if type(headers) not in (list, tuple):
        return headers, False, False
    for item in headers:
        if type(item) in (list, tuple) and len(item) == 2 and type(item[0]) is bytes and type(item[1]) is bytes:
            name, value = item
            if name.lower() == _H7C_HEADER:
                selected = selected or value == _H7C_SELECTOR
                continue
        clean.append(item)
    removed = all(not (
        type(item) in (list, tuple) and len(item) == 2 and type(item[0]) is bytes
        and item[0].lower() == _H7C_HEADER
    ) for item in clean)
    return clean, selected, removed

def _h7c_zero_artifact(status="invalid"):
    return {
        "schema": _H7C_SCHEMA, "observer_status": status, "outcome": "invalid",
        "failing_request_ordinal": None, "response_class": "invalid",
        "boundary_status": "invalid", "phase": None, "chain_status": "invalid",
        "source_chain": [], "frontier_status": "invalid",
        "coordinator_entry": "invalid", "gate_family": "unknown", "same_thread": False,
        "observer_restored": False, "selector_headers_removed": False,
    }

class _H7ContentFailureClassificationObserver:
    def __init__(self, result_path, bindings):
        self.result_path = result_path
        (
            self.boundary_codes, self.entry_codes, self.code_maps,
            self.frontier_maps, self.ignored_codes, self.bindings_valid,
        ) = bindings
        if (
            type(self.boundary_codes) is not dict
            or set(self.boundary_codes) != set(_H7C_BOUNDARY_LAYOUTS)
            or type(self.entry_codes) is not dict
            or set(self.entry_codes) != set(_H7C_ENTRY_LAYOUTS)
        ):
            self.bindings_valid = False
        self.selected_requests = 0
        self.successful_requests = 0
        self.total_observed_events = 0
        self.same_thread = True
        self.observer_restored = True
        self.selector_headers_removed = True
        self.finalized = False
        self.write_collision = False

    def _write(self, payload):
        if self.finalized:
            return False
        self.finalized = True
        try:
            encoded = _h7c_json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
            ).encode("ascii")
            if not 0 < len(encoded) <= _H7C_MAXIMUM or self.result_path is None:
                return False
            with self.result_path.open("xb") as stream:
                stream.write(encoded)
            return True
        except FileExistsError:
            self.write_collision = True
            return False
        except BaseException:
            return False

    def _invalid(self, status="invalid"):
        payload = _h7c_zero_artifact(status)
        payload.update({
            "same_thread": self.same_thread, "observer_restored": self.observer_restored,
            "selector_headers_removed": self.selector_headers_removed,
        })
        return payload

    async def observe(self, app, scope, receive, send, clean_headers, header_removed):
        clean_scope = dict(scope)
        clean_scope["headers"] = clean_headers
        if self.finalized:
            await app(clean_scope, receive, send)
            return
        self.selected_requests += 1
        ordinal = self.selected_requests
        self.selector_headers_removed = self.selector_headers_removed and header_removed
        response_status = None
        response_starts = 0
        boundary_events = {}
        boundary_phase = None
        chain_status = "complete"
        source_chain = []
        frontier_status = "unresolved"
        gate_family = "unknown"
        runtime_existing_seen = False
        coordinator_entries = 0
        app_fixed_entries = 0
        overflow = False
        cross_thread = False
        thread_id = _h7c_threading.get_ident()
        previous_profile = previous_trace = None
        monitoring = getattr(_h7c_sys, "monitoring", None)
        handled_event = start_event = None
        tool_id = None
        acquisition_uncertain = False
        restoration_limited = False
        cleanup_state = "no_tool"
        installed = False
        handled_callback_owned = start_callback_owned = False
        handled_callback_attempted = start_callback_attempted = False
        handled_callback_foreign = start_callback_foreign = False
        global_mask_owned = coordinator_local_owned = app_local_owned = False
        owner_marker = object()
        owner_reset_token = None

        def local_handled(code, instruction_offset, exception):
            nonlocal boundary_phase, chain_status, source_chain, frontier_status, gate_family
            nonlocal runtime_existing_seen, overflow, cross_thread
            try:
                label = next((
                    name for name, expected in self.boundary_codes.items() if code is expected
                ), None)
                if label is None or _h7c_request_owner.get() is not owner_marker:
                    return
                if type(instruction_offset) is not int:
                    overflow = True
                    return
                if instruction_offset != _H7C_BOUNDARY_LAYOUTS[label][2]:
                    return
                if _h7c_threading.get_ident() != thread_id:
                    cross_thread = True
                    return
                if self.total_observed_events >= _H7C_MAXIMUM:
                    overflow = True
                    return
                self.total_observed_events += 1
                boundary_events[label] = boundary_events.get(label, 0) + 1
                if boundary_events[label] != 1:
                    overflow = True
                    return
                if label == "coordinator":
                    boundary_phase, chain_status, source_chain = _h7c_exception_projection(
                        exception, self.code_maps, self.ignored_codes,
                    )
                    frontier_status, gate_family = "exact", "coordinator"
                else:
                    if label == "runtime_existing":
                        runtime_existing_seen = True
                    status, family = _h7c_frontier_projection(
                        exception, self.boundary_codes[label], self.frontier_maps,
                    )
                    if (
                        gate_family != "coordinator"
                        and (
                            label == "runtime_existing"
                            or not runtime_existing_seen and gate_family == "unknown"
                            and label in {"runtime_handle", "transport_handle"}
                        )
                    ):
                        frontier_status, gate_family = status, family
            except BaseException:
                overflow = True
                chain_status = "invalid"
                frontier_status, gate_family = "invalid", "unknown"
            finally:
                exception = None

        def local_started(code, instruction_offset):
            nonlocal coordinator_entries, app_fixed_entries, overflow, cross_thread
            try:
                if _h7c_request_owner.get() is not owner_marker:
                    return
                if _h7c_threading.get_ident() != thread_id:
                    cross_thread = True
                    return
                if self.total_observed_events >= _H7C_MAXIMUM:
                    overflow = True
                    return
                if type(instruction_offset) is not int or instruction_offset != 0:
                    overflow = True
                    return
                self.total_observed_events += 1
                if code is self.entry_codes["coordinator"]:
                    coordinator_entries += 1
                    overflow = overflow or coordinator_entries > 1
                elif code is self.entry_codes["app_fixed_response"]:
                    app_fixed_entries += 1
                    overflow = overflow or app_fixed_entries > 1
            except BaseException:
                overflow = True

        async def observed_send(message):
            nonlocal response_status, response_starts
            if type(message) is dict and message.get("type") == "http.response.start":
                response_starts += 1
                if response_status is None:
                    response_status = message.get("status")
            await send(message)

        try:
            previous_profile = _h7c_sys.getprofile()
            previous_trace = _h7c_sys.gettrace()
            events = getattr(monitoring, "events", None)
            handled_event = getattr(events, "EXCEPTION_HANDLED", None)
            start_event = getattr(events, "PY_START", None)
            if (
                self.bindings_valid and previous_profile is None and previous_trace is None
                and monitoring is not None and type(handled_event) is int
                and type(start_event) is int and handled_event != start_event
            ):
                for candidate in _H7C_TOOL_IDS:
                    if monitoring.get_tool(candidate) is not None:
                        continue
                    tool_id = candidate
                    cleanup_state = "tool_acquisition_uncertain"
                    try:
                        monitoring.use_tool_id(candidate, _H7C_TOOL_NAME)
                    except BaseException:
                        acquisition_uncertain = True
                    cleanup_state = "tool_acquired_unverified"
                    try:
                        observed_tool = monitoring.get_tool(candidate)
                    except BaseException:
                        observed_tool = False
                    if observed_tool == _H7C_TOOL_NAME:
                        cleanup_state = "tool_acquired"
                    elif observed_tool is None:
                        tool_id = None
                        cleanup_state = "no_tool"
                        continue
                    else:
                        cleanup_state = "ownership_uncertain"
                        restoration_limited = True
                    break
                if (
                    cleanup_state == "tool_acquired" and not acquisition_uncertain
                    and monitoring.get_events(tool_id) == 0
                    and all(
                        monitoring.get_local_events(tool_id, code) == 0
                        for code in self.entry_codes.values()
                    )
                ):
                    cleanup_state = "callback_registration_uncertain"
                    handled_callback_attempted = True
                    prior_callback = monitoring.register_callback(
                        tool_id, handled_event, local_handled,
                    )
                    if prior_callback is None:
                        handled_callback_owned = True
                        start_callback_attempted = True
                        prior_start = monitoring.register_callback(tool_id, start_event, local_started)
                        if prior_start is None:
                            start_callback_owned = True
                            cleanup_state = "callbacks_owned_masks_inactive"
                            monitoring.set_events(tool_id, handled_event)
                            global_mask_owned = monitoring.get_events(tool_id) == handled_event
                            if global_mask_owned:
                                coordinator_code = self.entry_codes["coordinator"]
                                app_code = self.entry_codes["app_fixed_response"]
                                monitoring.set_local_events(tool_id, coordinator_code, start_event)
                                coordinator_local_owned = (
                                    monitoring.get_local_events(tool_id, coordinator_code) == start_event
                                )
                                if coordinator_local_owned:
                                    monitoring.set_local_events(tool_id, app_code, start_event)
                                    app_local_owned = (
                                        monitoring.get_local_events(tool_id, app_code) == start_event
                                    )
                                cleanup_state = "callbacks_owned_masks_active"
                        else:
                            start_callback_foreign = True
                            restoration_limited = True
                            replaced = monitoring.register_callback(tool_id, start_event, prior_start)
                            cleanup_state = (
                                "foreign_callback_restored"
                                if replaced is local_started else "ownership_uncertain"
                            )
                    else:
                        handled_callback_foreign = True
                        restoration_limited = True
                        replaced = monitoring.register_callback(
                            tool_id, handled_event, prior_callback,
                        )
                        cleanup_state = (
                            "foreign_callback_restored"
                            if replaced is local_handled
                            else "ownership_uncertain"
                        )
                    installed = (
                        cleanup_state == "callbacks_owned_masks_active"
                        and handled_callback_owned and start_callback_owned
                        and global_mask_owned and coordinator_local_owned and app_local_owned
                        and monitoring.get_tool(tool_id) == _H7C_TOOL_NAME
                        and monitoring.get_events(tool_id) == handled_event
                        and monitoring.get_local_events(
                            tool_id, self.entry_codes["coordinator"],
                        ) == start_event
                        and monitoring.get_local_events(
                            tool_id, self.entry_codes["app_fixed_response"],
                        ) == start_event
                        and _h7c_sys.getprofile() is previous_profile
                        and _h7c_sys.gettrace() is previous_trace
                    )
                    if installed:
                        owner_reset_token = _h7c_request_owner.set(owner_marker)
        except BaseException:
            installed = False
        app_returned = False
        try:
            await app(clean_scope, receive, observed_send)
            app_returned = True
        finally:
            restored = (
                _h7c_sys.getprofile() is previous_profile
                and _h7c_sys.gettrace() is previous_trace
                and not restoration_limited
            )
            try:
                if cleanup_state != "no_tool":
                    ownership_intact = monitoring.get_tool(tool_id) == _H7C_TOOL_NAME
                    if ownership_intact and cleanup_state in {
                        "tool_acquisition_uncertain", "tool_acquired_unverified", "tool_acquired",
                    }:
                        coordinator_code = self.entry_codes["coordinator"]
                        app_code = self.entry_codes["app_fixed_response"]
                        ownership_intact = (
                            monitoring.get_events(tool_id) == 0
                            and monitoring.get_local_events(tool_id, coordinator_code) == 0
                            and monitoring.get_local_events(tool_id, app_code) == 0
                        )
                        if ownership_intact:
                            monitoring.free_tool_id(tool_id)
                            ownership_intact = (
                                monitoring.get_tool(tool_id) is None
                                and monitoring.get_events(tool_id) == 0
                                and monitoring.get_local_events(tool_id, coordinator_code) == 0
                                and monitoring.get_local_events(tool_id, app_code) == 0
                            )
                    elif ownership_intact and cleanup_state in {
                        "callback_registration_uncertain", "callbacks_owned_masks_inactive",
                        "callbacks_owned_masks_active", "foreign_callback_restored",
                    }:
                        coordinator_code = self.entry_codes["coordinator"]
                        app_code = self.entry_codes["app_fixed_response"]
                        global_mask = monitoring.get_events(tool_id)
                        coordinator_mask = monitoring.get_local_events(tool_id, coordinator_code)
                        app_mask = monitoring.get_local_events(tool_id, app_code)
                        observed_handled = observed_start = None
                        handled_clearable = not handled_callback_attempted
                        start_clearable = not start_callback_attempted
                        if handled_callback_attempted and not handled_callback_foreign:
                            observed_handled = monitoring.register_callback(
                                tool_id, handled_event, local_handled,
                            )
                            handled_clearable = observed_handled in (None, local_handled)
                            if not handled_clearable:
                                monitoring.register_callback(tool_id, handled_event, observed_handled)
                        if start_callback_attempted and not start_callback_foreign:
                            observed_start = monitoring.register_callback(
                                tool_id, start_event, local_started,
                            )
                            start_clearable = observed_start in (None, local_started)
                            if not start_clearable:
                                monitoring.register_callback(tool_id, start_event, observed_start)
                        if start_clearable and app_mask == start_event:
                            monitoring.set_local_events(tool_id, app_code, 0)
                        if start_clearable and coordinator_mask == start_event:
                            monitoring.set_local_events(tool_id, coordinator_code, 0)
                        if handled_clearable and global_mask == handled_event:
                            monitoring.set_events(tool_id, 0)
                        masks_clear = (
                            app_mask in (0, start_event)
                            and coordinator_mask in (0, start_event)
                            and global_mask in (0, handled_event)
                            and monitoring.get_local_events(tool_id, app_code) == 0
                            and monitoring.get_local_events(tool_id, coordinator_code) == 0
                            and monitoring.get_events(tool_id) == 0
                        )
                        callbacks_clear = handled_clearable and start_clearable
                        if start_callback_attempted and start_clearable:
                            removed_start = (
                                monitoring.register_callback(tool_id, start_event, None)
                            )
                            callbacks_clear = callbacks_clear and removed_start is local_started
                        if handled_callback_attempted and handled_clearable:
                            removed_handled = (
                                monitoring.register_callback(tool_id, handled_event, None)
                            )
                            callbacks_clear = callbacks_clear and removed_handled is local_handled
                        ownership_intact = masks_clear and callbacks_clear
                        if ownership_intact:
                            monitoring.free_tool_id(tool_id)
                            ownership_intact = (
                                monitoring.get_tool(tool_id) is None
                                and monitoring.get_events(tool_id) == 0
                                and monitoring.get_local_events(tool_id, coordinator_code) == 0
                                and monitoring.get_local_events(tool_id, app_code) == 0
                            )
                    else:
                        ownership_intact = False
                    restored = restored and ownership_intact
            except BaseException:
                restored = False
            try:
                if owner_reset_token is not None:
                    _h7c_request_owner.reset(owner_reset_token)
            except BaseException:
                restored = False
            same_thread = (
                _h7c_threading.get_ident() == thread_id and not cross_thread
            )
            self.same_thread = self.same_thread and same_thread
            self.observer_restored = self.observer_restored and restored
            request_valid = (
                installed and same_thread and restored and header_removed and app_returned
                and response_starts == 1 and type(response_status) is int
                and ordinal <= _H7C_MAXIMUM and not overflow
                and coordinator_entries in {0, 1} and app_fixed_entries in {0, 1}
            )
            if request_valid and response_status == 200:
                self.successful_requests += 1
            elif request_valid:
                boundary_status = (
                    "missing" if not boundary_events
                    else "exact" if all(count == 1 for count in boundary_events.values())
                    else "invalid"
                )
                coordinator_entry = "present" if coordinator_entries == 1 else "absent"
                post_coordinator_families = {
                    "postpublication_validation", "publication_exit", "cleanup",
                }
                if coordinator_entry == "present" and boundary_events.get("coordinator") == 1:
                    frontier_status, gate_family = "exact", "coordinator"
                elif coordinator_entry == "absent" and boundary_events.get("coordinator", 0):
                    frontier_status, gate_family = "invalid", "unknown"
                elif (
                    coordinator_entry == "absent" and app_fixed_entries == 1
                    and response_status == 403 and not boundary_events
                ):
                    frontier_status, gate_family = "exact", "app_graph"
                elif (
                    coordinator_entry == "present"
                    and gate_family not in post_coordinator_families | {"unknown"}
                    or coordinator_entry == "absent"
                    and gate_family in post_coordinator_families
                ):
                    frontier_status, gate_family = "invalid", "unknown"
                elif frontier_status != "exact" or gate_family not in _H7C_GATE_FAMILIES:
                    frontier_status, gate_family = "unresolved", "unknown"
                if boundary_status == "missing":
                    boundary_phase, chain_status, source_chain = None, "complete", []
                elif boundary_status == "invalid":
                    boundary_phase, chain_status, source_chain = None, "invalid", []
                    frontier_status, gate_family = "invalid", "unknown"
                self._write({
                    "schema": _H7C_SCHEMA, "observer_status": "complete", "outcome": "failure",
                    "failing_request_ordinal": ordinal,
                    "response_class": "403" if response_status == 403 else "other_non_200",
                    "boundary_status": boundary_status, "phase": boundary_phase,
                    "chain_status": chain_status, "source_chain": source_chain,
                    "frontier_status": frontier_status,
                    "coordinator_entry": coordinator_entry, "gate_family": gate_family,
                    "same_thread": self.same_thread, "observer_restored": self.observer_restored,
                    "selector_headers_removed": self.selector_headers_removed,
                })
            elif not self.finalized:
                self._write(self._invalid("incomplete" if installed else "invalid"))

    def finalize_all_success(self):
        if self.finalized:
            return False
        valid = (
            self.bindings_valid and self.selected_requests > 0
            and self.selected_requests == self.successful_requests
            and self.selected_requests <= _H7C_MAXIMUM
            and self.total_observed_events <= _H7C_MAXIMUM
            and self.same_thread and self.observer_restored and self.selector_headers_removed
        )
        if not valid:
            return self._write(self._invalid())
        payload = {
            "schema": _H7C_SCHEMA, "observer_status": "complete", "outcome": "all_success",
            "failing_request_ordinal": None, "response_class": "all_200",
            "boundary_status": "missing", "phase": None, "chain_status": "complete",
            "source_chain": [], "frontier_status": "unresolved",
            "coordinator_entry": "absent", "gate_family": "unknown",
            "same_thread": self.same_thread, "observer_restored": self.observer_restored,
            "selector_headers_removed": self.selector_headers_removed,
        }
        return self._write(payload)

class _H7ContentFailureClassificationMiddleware:
    def __init__(self, app, observer):
        self.app, self.observer = app, observer

    async def __call__(self, scope, receive, send):
        headers = scope.get("headers", ()) if type(scope) is dict and scope.get("type") == "http" else ()
        clean_headers, selected, removed = _h7c_clean_headers(headers)
        if not (
            selected and type(scope) is dict and scope.get("method") == "POST"
            and scope.get("path") == "/api/plugins/mcp"
        ):
            await self.app(scope, receive, send)
            return
        await self.observer.observe(self.app, scope, receive, send, clean_headers, removed)

_h7c_observer = None
if globals().get("h7_classification_enabled") is True and globals().get("h7_classification_path") is not None:
    try:
        _h7c_bindings = _h7c_build_bindings()
        _h7c_observer = _H7ContentFailureClassificationObserver(h7_classification_path, _h7c_bindings)
        application.app.add_middleware(
            _H7ContentFailureClassificationMiddleware, observer=_h7c_observer,
        )
    except BaseException:
        _h7c_observer = None
'''


_H7_CONTENT_INNER_BOOTSTRAP_SOURCE = r'''
import sys as _h7b_sys

_h7b = {
    "schema": "odysseus.h7_content_inner_bootstrap.v1",
    "reason": "none", "ready": False, "observer": None, "tool": None,
    "register_calls": 0, "monitor_writes": 0, "flags_valid": False,
    "paths_ready": False,
}
try:
    if (
        _h7b_sys.flags.isolated != 1 or _h7b_sys.flags.no_site != 1
        or "site" in _h7b_sys.modules
    ):
        _h7b["reason"] = "bootstrap_flags"
        raise RuntimeError()
    _h7b["flags_valid"] = True
    _h7b_monitoring = _h7b_sys.monitoring
    _h7b.update({
        "monitoring": _h7b_monitoring,
        "sys_exception": _h7b_sys.exception,
        "get_ident": None,
        "use_tool_id": _h7b_monitoring.use_tool_id,
        "register_callback": _h7b_monitoring.register_callback,
        "set_local_events": _h7b_monitoring.set_local_events,
        "get_local_events": _h7b_monitoring.get_local_events,
        "get_events": _h7b_monitoring.get_events,
        "get_tool": _h7b_monitoring.get_tool,
        "line_event": _h7b_monitoring.events.LINE,
        "start_event": _h7b_monitoring.events.PY_START,
        "disable": _h7b_monitoring.DISABLE,
    })
    def _h7b_line(code, line):
        observer = _h7b["observer"] if _h7b["ready"] else None
        return observer._line_event(code, line) if observer is not None else None
    def _h7b_start(code, offset):
        observer = _h7b["observer"] if _h7b["ready"] else None
        return observer._start_event(code, offset) if observer is not None else None
    _h7b["line_callback"], _h7b["start_callback"] = _h7b_line, _h7b_start
    for _h7b_candidate in (3, 4):
        try:
            _h7b["monitor_writes"] += 1
            _h7b["use_tool_id"](_h7b_candidate, "odysseus.h7.inner.process.v2")
            _h7b["tool"] = _h7b_candidate
            break
        except ValueError:
            pass
    if _h7b["tool"] is None:
        _h7b["reason"] = "bootstrap_binding"
        raise RuntimeError()
    _h7b["register_calls"] += 1; _h7b["monitor_writes"] += 1
    if _h7b["register_callback"](
        _h7b["tool"], _h7b["line_event"], _h7b_line,
    ) is not None:
        _h7b["reason"] = "callback_ownership"
        raise RuntimeError()
    _h7b["register_calls"] += 1; _h7b["monitor_writes"] += 1
    if _h7b["register_callback"](
        _h7b["tool"], _h7b["start_event"], _h7b_start,
    ) is not None:
        _h7b["reason"] = "callback_ownership"
        raise RuntimeError()
except BaseException:
    if _h7b["reason"] == "none":
        _h7b["reason"] = "bootstrap_binding"

try:
    _h7b_os = __import__("os")
    _h7b_pathlib = __import__("pathlib")
    _h7b_repo = _h7b_pathlib.Path(
        _h7b_os.environ.pop("ODYSSEUS_H7_BOOTSTRAP_REPO_ROOT"),
    ).resolve(strict=True)
    _h7b_site = _h7b_pathlib.Path(
        _h7b_os.environ.pop("ODYSSEUS_H7_BOOTSTRAP_SITE_PACKAGES"),
    ).resolve(strict=True)
    if (
        not _h7b_repo.is_dir() or not (_h7b_repo / "app.py").is_file()
        or not _h7b_site.is_dir() or _h7b_site.name != "site-packages"
        or _h7b_site.parent.name != "Lib" or _h7b_repo in map(_h7b_pathlib.Path, _h7b_sys.path)
        or _h7b_site in map(_h7b_pathlib.Path, _h7b_sys.path)
    ):
        raise RuntimeError()
    _h7b_sys.path.insert(0, str(_h7b_repo))
    _h7b_sys.path.append(str(_h7b_site))
    _h7b["paths_ready"] = True
except BaseException:
    _h7b["reason"] = "bootstrap_path" if _h7b["reason"] == "none" else _h7b["reason"]

_h7i_bootstrap = _h7b
'''


_H7_CONTENT_INNER_REJECT_SOURCE = r'''
from importlib import import_module as _h7i_import_module
from types import CellType as _H7ICellType, CodeType as _H7ICodeType, FrameType as _H7IFrameType, FunctionType as _H7IFunctionType, ModuleType as _H7IModuleType, TracebackType as _H7ITracebackType
import json as _h7i_json
import sys as _h7i_sys
import threading as _h7i_threading

_H7I_SCHEMA = "odysseus.h7_content_inner_reject.v1"
_H7I_MAXIMUM = 4096
_H7I_MAX_EXCEPTIONS = 8
_H7I_MAX_TRACEBACK_NODES = 32
_H7I_TOOL_NAME = "odysseus.h7.inner.process.v2"
_H7I_TRANSPORT_HANDLER_LINES = frozenset({824, 828})
_H7I_SOURCE_CLASSES = frozenset({
    "runtime", "authority", "confirmation", "cursor", "coordinator",
    "native", "planning", "app_graph", "unknown", "invalid",
})
_H7I_REJECTION_REASONS = frozenset({
    "layout_pin_mismatch", "global_mask_conflict", "callback_ownership",
    "local_mask_conflict", "event_thread_mismatch", "event_overflow",
    "chain_cycle", "chain_truncated", "traceback_metadata_mismatch",
    "projection_shape", "artifact_collision", "teardown_incomplete",
    "bootstrap_flags", "bootstrap_path", "bootstrap_import", "bootstrap_binding",
    "none", "unknown",
})
_H7I_BRANCHES = frozenset({
    "runtime.content_preflight", "runtime.native_lease_acquire",
    "runtime.native_lease_shape", "runtime.native_lease_live", "runtime.record_live",
    "runtime.publication_open", "runtime.postpublication_validation",
    "runtime.publication_exit", "runtime.cleanup", "authority.native_lease_acquire",
    "authority.publication_open", "confirmation.permit_issue",
    "confirmation.permit_reject", "confirmation.publication_fence",
    "cursor.content_verify", "cursor.content_issue", "coordinator.selection_validate",
    "coordinator.permit_issue", "coordinator.cursor_verify", "coordinator.forge_dispatch",
    "coordinator.planning_dispatch", "coordinator.knowledge_dispatch",
    "coordinator.inner_page_contract", "coordinator.content_cursor_issue",
    "coordinator.response_encode", "coordinator.final_freshness",
    "coordinator.finish", "coordinator.active_guard", "coordinator.capture",
    "coordinator.fence", "native.execution", "native.consumption",
    "native.recovery", "native.detached_encode", "planning.execution",
    "planning.consumption", "planning.failure", "planning.detached_encode",
    "app.graph_absent", "none", "unknown", "invalid",
})

# Each range is frozen to the pinned source census.  The emitted locus is the
# literal label plus the exact co_lines source line (for example,
# authority.acquire.L1012); it never contains a filename or exception data.
_H7I_CODE_SPECS = (
    ("runtime.existing", "src.mcp_context_request_runtime", "McpContextRequestRuntime", "_existing", 2254, "runtime", (
        (3141, 3157, "runtime.content_preflight"),
        (3157, 3174, "runtime.native_lease_acquire"),
        (3174, 3195, "runtime.native_lease_shape"),
        (3195, 3202, "runtime.native_lease_live"),
        (3202, 3210, "runtime.record_live"),
        (3215, 3234, "runtime.publication_open"),
        (3234, 3242, "runtime.postpublication_validation"),
        (3242, 3256, "runtime.publication_exit"),
        (3280, 3334, "runtime.cleanup"),
    )),
    ("authority.acquire", "src.mcp_context_authority", "", "_acquire_native_lease_capability", 1006, "authority", ((1006, 1028, "authority.native_lease_acquire"),)),
    ("authority.state", "src.mcp_context_authority", "", "_read_native_lease_state", 399, "authority", ((399, 419, "authority.native_lease_acquire"),)),
    ("authority.open", "src.mcp_context_authority", "", "_open_mcp_content_publication", 2227, "authority", ((2227, 2505, "authority.publication_open"),)),
    ("confirmation.issue", "src.mcp_private_context_confirmation", "McpPrivateContextConfirmationService", "_issue_captured_permit", 1166, "confirmation", ((1166, 1369, "confirmation.permit_issue"),)),
    ("confirmation.reject", "src.mcp_private_context_confirmation", "McpPrivateContextConfirmationService", "_reject_native_permit", 1369, "confirmation", ((1369, 1630, "confirmation.permit_reject"),)),
    ("confirmation.fence", "src.mcp_private_context_confirmation", "McpPrivateContextConfirmationService", "_fence_disclosed_publication", 2185, "confirmation", ((2185, 2505, "confirmation.publication_fence"),)),
    ("cursor.issue_public", "src.mcp_context_cursor_authority", "McpContextCursorAuthority", "issue_content_cursor", 435, "cursor", ((435, 450, "cursor.content_issue"),)),
    ("cursor.issue", "src.mcp_context_cursor_authority", "McpContextCursorAuthority", "_issue", 363, "cursor", ((363, 418, "cursor.content_issue"),)),
    ("cursor.verify_public", "src.mcp_context_cursor_authority", "McpContextCursorAuthority", "verify_content_cursor", 550, "cursor", ((550, 567, "cursor.content_verify"),)),
    ("cursor.verify", "src.mcp_context_cursor_authority", "McpContextCursorAuthority", "_verify", 467, "cursor", ((467, 534, "cursor.content_verify"),)),
    ("coordinator.publish", "src.mcp_context_pack_coordinator", "McpContextPackCoordinator", "publish_content", 1215, "coordinator", (
        (1215, 1236, "coordinator.selection_validate"),
        (1236, 1254, "coordinator.permit_issue"),
        (1254, 1274, "coordinator.cursor_verify"),
        (1274, 1278, "coordinator.forge_dispatch"),
        (1278, 1283, "coordinator.planning_dispatch"),
        (1283, 1288, "coordinator.knowledge_dispatch"),
        (1288, 1307, "coordinator.inner_page_contract"),
        (1307, 1313, "coordinator.content_cursor_issue"),
        (1313, 1325, "coordinator.response_encode"),
        (1325, 1329, "coordinator.final_freshness"),
        (1329, 1342, "coordinator.finish"),
    )),
    ("coordinator.begin", "src.mcp_context_pack_coordinator", "McpContextPackCoordinator", "_begin", 1021, "coordinator", ((1021, 1042, "coordinator.selection_validate"),)),
    ("coordinator.active", "src.mcp_context_pack_coordinator", "McpContextPackCoordinator", "_check_active", 1042, "coordinator", ((1042, 1049, "coordinator.active_guard"),)),
    ("coordinator.finish_helper", "src.mcp_context_pack_coordinator", "McpContextPackCoordinator", "_finish", 1049, "coordinator", ((1049, 1063, "coordinator.finish"),)),
    ("coordinator.abort", "src.mcp_context_pack_coordinator", "McpContextPackCoordinator", "_abort", 1063, "coordinator", ((1063, 1073, "coordinator.finish"),)),
    ("coordinator.capture", "src.mcp_context_pack_coordinator", "McpContextPackCoordinator", "_capture", 1073, "coordinator", ((1073, 1090, "coordinator.capture"),)),
    ("coordinator.fence", "src.mcp_context_pack_coordinator", "McpContextPackCoordinator", "_fence", 1090, "coordinator", ((1090, 1098, "coordinator.fence"),)),
    ("coordinator.encode", "src.mcp_context_pack_coordinator", "McpContextPackCoordinator", "_encode", 1098, "coordinator", ((1098, 1105, "coordinator.response_encode"),)),
    ("native.publish", "src.mcp_context_native_page", "", "_publish", 298, "native", (
        (298, 343, "native.execution"), (343, 350, "native.recovery"),
        (350, 354, "native.consumption"),
    )),
    ("planning.publish", "src.mcp_context_planning_page", "", "publish_planning_page", 180, "planning", (
        (180, 228, "planning.execution"), (228, 240, "planning.failure"),
        (240, 246, "planning.consumption"),
    )),
)
_H7I_NESTED_SPECS = (
    ("native.detached", "src.mcp_context_native_page", "_publish", "encode_detached", "native", "native.detached_encode"),
    ("planning.detached", "src.mcp_context_planning_page", "publish_planning_page", "encode_detached", "planning", "planning.detached_encode"),
)
_H7I_WRAPPER_SPECS = (
    ("transport.handle", "src.mcp_context_request_transport", "McpContextRequestTransport", "handle", 641),
    ("runtime.handle", "src.mcp_context_request_runtime", "McpContextRequestRuntime", "handle", 3339),
)
_H7I_IGNORED_SPECS = (
    ("src.mcp_context_authority", "", "_fail"),
    ("src.mcp_private_context_confirmation", "", "_fail"),
    ("src.mcp_context_cursor_authority", "", "_fail"),
    ("src.mcp_context_pack_coordinator", "", "_fail"),
    ("src.mcp_context_native_page", "", "_fail"),
    ("src.mcp_context_planning_page", "", "_fail"),
    ("src.mcp_context_request_runtime", "", "_fail"),
)

_H7I_BASE_EXCEPTION = BaseException
_H7I_EXCEPTION_DESCRIPTORS = tuple(BaseException.__dict__[name] for name in ("__traceback__", "__cause__", "__context__"))
_H7I_TRACEBACK_DESCRIPTORS = tuple(_H7ITracebackType.__dict__[name] for name in ("tb_frame", "tb_lasti", "tb_lineno", "tb_next"))
_H7I_FRAME_CODE_DESCRIPTOR = _H7IFrameType.__dict__["f_code"]
_H7I_EXCEPTION_GET = type(_H7I_EXCEPTION_DESCRIPTORS[0]).__get__
_H7I_TRACEBACK_GETTERS = tuple(type(value).__get__ for value in _H7I_TRACEBACK_DESCRIPTORS)
_H7I_FRAME_GET = type(_H7I_FRAME_CODE_DESCRIPTOR).__get__

def _h7i_resolve(module, owner, name):
    value = _h7i_import_module(module)
    if owner:
        value = type.__getattribute__(value.__dict__[owner], "__dict__")[name]
        if type(value) is staticmethod:
            value = staticmethod.__get__(value, None, _h7i_import_module(module).__dict__[owner])
    else:
        value = value.__dict__[name]
    return value if type(value) is _H7IFunctionType else None

def _h7i_nested(function, name):
    matches = tuple(value for value in function.__code__.co_consts if type(value) is _H7ICodeType and value.co_name == name)
    return matches[0] if len(matches) == 1 else None

def _h7i_build_bindings():
    maps, wrappers, ignored, valid = {}, {}, {}, True
    try:
        modules = {_h7i_import_module(spec[1]) for spec in _H7I_CODE_SPECS}
        if any(type(module) is not _H7IModuleType for module in modules):
            raise TypeError()
        for label, module, owner, name, expected_firstline, source_class, ranges in _H7I_CODE_SPECS:
            function = _h7i_resolve(module, owner, name)
            if label == "authority.open":
                wrapper = function
                if type(wrapper) is not _H7IFunctionType:
                    raise TypeError()
                original = wrapper.__dict__.get("__wrapped__")
                closure = wrapper.__closure__
                if (
                    type(original) is not _H7IFunctionType
                    or wrapper.__code__.co_freevars != ("func",)
                    or type(closure) is not tuple or len(closure) != 1
                    or type(closure[0]) is not _H7ICellType
                    or closure[0].cell_contents is not original
                    or type(original.__code__) is not _H7ICodeType
                    or original.__code__.co_firstlineno != expected_firstline
                    or original.__code__.co_name != name
                    or original.__code__.co_flags & 0x20 != 0x20
                ):
                    raise TypeError()
                function = original
            if function is None or function.__code__.co_firstlineno != expected_firstline:
                raise TypeError()
            code = function.__code__
            line_map = {}
            for start, end, line in code.co_lines():
                if line is None or start == end:
                    continue
                matches = tuple(branch for first, stop, branch in ranges if first <= line < stop)
                if len(matches) > 1:
                    raise TypeError()
                if matches:
                    line_map[(start, end, line)] = (source_class, matches[0], label + ".L" + str(line))
            if not line_map or id(code) in maps:
                raise TypeError()
            maps[id(code)] = (code, line_map)
        for label, module, outer_name, nested_name, source_class, branch in _H7I_NESTED_SPECS:
            outer = _h7i_resolve(module, "", outer_name)
            code = _h7i_nested(outer, nested_name) if outer is not None else None
            if type(code) is not _H7ICodeType or id(code) in maps:
                raise TypeError()
            line_map = {
                (start, end, line): (source_class, branch, label + ".L" + str(line))
                for start, end, line in code.co_lines() if line is not None and start != end
            }
            if not line_map:
                raise TypeError()
            maps[id(code)] = (code, line_map)
        for label, module, owner, name, firstline in _H7I_WRAPPER_SPECS:
            function = _h7i_resolve(module, owner, name)
            if function is None or function.__code__.co_firstlineno != firstline:
                raise TypeError()
            wrappers[id(function.__code__)] = (function.__code__, label)
        for module, owner, name in _H7I_IGNORED_SPECS:
            function = _h7i_resolve(module, owner, name)
            if function is None:
                raise TypeError()
            ignored[id(function.__code__)] = (function.__code__, True)
        app_function = _h7i_resolve("src.mcp_context_app", "McpContextApp", "_fixed_transport_response")
        if app_function is None or app_function.__code__.co_firstlineno != 835:
            raise TypeError()
        app_code = app_function.__code__
        transport_code = wrappers[next(key for key, value in wrappers.items() if value[1] == "transport.handle")][0]
    except BaseException:
        maps, wrappers, ignored, app_code, transport_code, valid = {}, {}, {}, None, None, False
    return maps, wrappers, ignored, app_code, transport_code, valid

def _h7i_identity_get(mapping, value):
    item = mapping.get(id(value))
    return item[1] if item is not None and item[0] is value else None

def _h7i_exception_field(value, index):
    return _H7I_EXCEPTION_GET(_H7I_EXCEPTION_DESCRIPTORS[index], value, _H7I_BASE_EXCEPTION)

def _h7i_traceback_field(value, index):
    return _H7I_TRACEBACK_GETTERS[index](_H7I_TRACEBACK_DESCRIPTORS[index], value, _H7ITracebackType)

def _h7i_frame_code(value):
    return _H7I_FRAME_GET(_H7I_FRAME_CODE_DESCRIPTOR, value, _H7IFrameType)

def _h7i_locus(maps, code, offset, line):
    line_map = _h7i_identity_get(maps, code)
    if line_map is None or type(offset) is not int or type(line) is not int:
        return None
    matches = tuple(value for (start, end, mapped_line), value in line_map.items() if start <= offset < end and mapped_line == line)
    return matches[0] if len(matches) == 1 else None

def _h7i_project_exception(exception, bindings):
    maps, wrappers, ignored, _app_code, _transport_code, valid = bindings
    if not valid or not isinstance(exception, _H7I_BASE_EXCEPTION):
        return "invalid", "unknown", "unknown", "unknown", "layout_pin_mismatch"
    exception_seen, origin = set(), None
    corridor = False
    try:
        current = exception
        for _exception_index in range(_H7I_MAX_EXCEPTIONS):
            if id(current) in exception_seen:
                return "invalid", "unknown", "unknown", "unknown", "chain_cycle"
            exception_seen.add(id(current))
            traceback = _h7i_exception_field(current, 0)
            traceback_seen = set()
            for _node_index in range(_H7I_MAX_TRACEBACK_NODES):
                if traceback is None:
                    break
                if type(traceback) is not _H7ITracebackType or id(traceback) in traceback_seen:
                    return "invalid", "unknown", "unknown", "unknown", "chain_cycle"
                traceback_seen.add(id(traceback))
                frame = _h7i_traceback_field(traceback, 0)
                offset = _h7i_traceback_field(traceback, 1)
                line = _h7i_traceback_field(traceback, 2)
                if type(frame) is not _H7IFrameType or type(offset) is not int or type(line) is not int:
                    return "invalid", "unknown", "unknown", "unknown", "traceback_metadata_mismatch"
                code = _h7i_frame_code(frame)
                mapped = _h7i_locus(maps, code, offset, line)
                if mapped is not None:
                    corridor = True
                    origin = ("exact",) + mapped
                elif _h7i_identity_get(ignored, code) is not None:
                    pass
                elif _h7i_identity_get(wrappers, code) is not None:
                    corridor = True
                    origin = ("unmapped", "unknown", "unknown", "unknown")
                elif corridor:
                    origin = ("unmapped", "unknown", "unknown", "unknown")
                traceback = _h7i_traceback_field(traceback, 3)
            else:
                if traceback is not None:
                    return "invalid", "unknown", "unknown", "unknown", "chain_truncated"
            cause = _h7i_exception_field(current, 1)
            context = _h7i_exception_field(current, 2)
            if cause is not None and not isinstance(cause, _H7I_BASE_EXCEPTION) or context is not None and not isinstance(context, _H7I_BASE_EXCEPTION):
                return "invalid", "unknown", "unknown", "unknown", "traceback_metadata_mismatch"
            if cause is not None and context is not None and cause is not context:
                return "ambiguous", "unknown", "unknown", "unknown", "projection_shape"
            following = cause if cause is not None else context
            if following is None:
                break
            current = following
        else:
            return "invalid", "unknown", "unknown", "unknown", "chain_truncated"
        return origin + ("none",) if origin is not None else ("unmapped", "unknown", "unknown", "unknown", "none")
    except BaseException:
        return "invalid", "unknown", "unknown", "unknown", "unknown"
    finally:
        current = exception = None

def _h7i_zero(status="invalid"):
    return {
        "schema": _H7I_SCHEMA, "observer_status": status, "outcome": "invalid",
        "response_class": "invalid", "failing_request_ordinal": None,
        "page_source_class": "invalid", "route_class": "invalid",
        "inner_map_status": "invalid", "inner_source_class": "invalid",
        "inner_branch": "invalid", "inner_locus_id": "invalid",
        "observer_rejection_reason": "unknown",
        "event_thread_consistent": False, "observation_scope_closed": False,
        "release_scope": "unreleased",
        "global_mask_untouched": False,
    }

class _H7InnerRejectObserver:
    def __init__(self, result_path, bindings):
        self.result_path, self.bindings = result_path, bindings
        self.bootstrap = globals().get("_h7i_bootstrap")
        self.finalized = False
        self.write_collision = False
        self.requests = self.successes = 0
        self.event_thread_consistent = True
        self.observation_scope_closed = True
        self.global_mask_untouched = True
        self.observer_rejection_reason = "none"
        self._active = None
        self._install()

    def _reject(self, reason):
        if self.observer_rejection_reason == "none":
            self.observer_rejection_reason = reason if reason in _H7I_REJECTION_REASONS else "unknown"

    def _write(self, payload):
        if self.finalized:
            return False
        self.finalized = True
        try:
            encoded = _h7i_json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
            if self.result_path is None or not 0 < len(encoded) <= _H7I_MAXIMUM:
                return False
            with self.result_path.open("xb") as stream:
                return stream.write(encoded) == len(encoded)
        except FileExistsError:
            self.write_collision = True
            self._reject("artifact_collision")
            return False
        except BaseException:
            return False

    def _install(self):
        maps, wrappers, ignored, app_code, transport_code, valid = self.bindings
        bootstrap = self.bootstrap
        try:
            if type(bootstrap) is not dict or bootstrap.get("schema") != "odysseus.h7_content_inner_bootstrap.v1":
                self._reject("bootstrap_import")
                return False
            if bootstrap.get("reason") != "none":
                self._reject(bootstrap.get("reason"))
                return False
            if not valid or not bootstrap.get("paths_ready"):
                self._reject("layout_pin_mismatch" if not valid else "bootstrap_path")
                return False
            tool = bootstrap.get("tool")
            if (
                type(tool) is not int or bootstrap["get_tool"](tool) != _H7I_TOOL_NAME
                or bootstrap["get_events"](tool) != 0
                or bootstrap["get_local_events"](tool, transport_code) != 0
                or bootstrap["get_local_events"](tool, app_code) != 0
            ):
                self._reject(
                    "global_mask_conflict"
                    if type(tool) is int and bootstrap["get_events"](tool) != 0
                    else "local_mask_conflict"
                )
                return False
            if (
                type(bootstrap.get("line_event")) is not int
                or type(bootstrap.get("start_event")) is not int
                or bootstrap["line_event"] == bootstrap["start_event"]
            ):
                self._reject("layout_pin_mismatch")
                return False
            bootstrap["observer"] = self
            bootstrap["get_ident"] = _h7i_threading.get_ident
            bootstrap["monitor_writes"] += 1
            bootstrap["set_local_events"](tool, transport_code, bootstrap["line_event"])
            bootstrap["monitor_writes"] += 1
            bootstrap["set_local_events"](tool, app_code, bootstrap["start_event"])
            if (
                bootstrap["get_events"](tool) != 0
                or bootstrap["get_local_events"](tool, transport_code) != bootstrap["line_event"]
                or bootstrap["get_local_events"](tool, app_code) != bootstrap["start_event"]
            ):
                self._reject(
                    "global_mask_conflict"
                    if bootstrap["get_events"](tool) != 0 else "local_mask_conflict"
                )
                return False
            bootstrap["ready"] = True
            return True
        except BaseException:
            self._reject("bootstrap_binding")
            return False

    def _event_thread(self, state):
        ident = self.bootstrap["get_ident"]()
        if state["event_thread"] is None:
            state["event_thread"] = ident
        elif state["event_thread"] != ident:
            self.event_thread_consistent = False
            state["invalid"] = True
            self._reject("event_thread_mismatch")

    def _line_event(self, code, line):
        maps, wrappers, ignored, app_code, transport_code, valid = self.bindings
        if code is not transport_code or type(line) is not int:
            return self.bootstrap["disable"]
        if line not in _H7I_TRANSPORT_HANDLER_LINES:
            return self.bootstrap["disable"]
        state = self._active
        if type(state) is not dict:
            return None
        exception = None
        try:
            self._event_thread(state)
            if state["transport_events"]:
                state["invalid"] = True
                self._reject("event_overflow")
            else:
                exception = self.bootstrap["sys_exception"]()
                state["transport_events"] = 1
                state["projection"] = _h7i_project_exception(exception, self.bindings)
                if state["projection"][4] != "none":
                    self._reject(state["projection"][4])
        except BaseException:
            state["invalid"] = True
            self._reject("projection_shape")
        finally:
            exception = None
        return self.bootstrap["disable"]

    def _start_event(self, code, offset):
        maps, wrappers, ignored, app_code, transport_code, valid = self.bindings
        if code is not app_code or offset != 0:
            return self.bootstrap["disable"]
        state = self._active
        if type(state) is not dict:
            return None
        try:
            self._event_thread(state)
            if state["app_events"]:
                state["invalid"] = True
                self._reject("event_overflow")
            else:
                state["app_events"] = 1
        except BaseException:
            state["invalid"] = True
            self._reject("projection_shape")
        return self.bootstrap["disable"]

    def begin(self, ordinal, source_class):
        if self.finalized or self._active is not None or type(ordinal) is not int or not 1 <= ordinal <= _H7I_MAXIMUM or source_class not in {"forge", "knowledge", "planning"}:
            self._reject("projection_shape")
            self._active = {"invalid": True, "ordinal": None, "source": "invalid"}
            return False
        self.requests += 1
        ready = (
            type(self.bootstrap) is dict and self.bootstrap.get("ready") is True
            and self.bootstrap.get("observer") is self
        )
        self.observation_scope_closed = False
        state = {"invalid": not ready, "ordinal": ordinal, "source": source_class, "event_thread": _h7i_threading.get_ident(), "transport_events": 0, "app_events": 0, "projection": ("unmapped", "unknown", "unknown", "unknown", "none")}
        self._active = state
        return ready

    def end(self, response_status):
        state, self._active = self._active, None
        if type(state) is not dict:
            return False
        self.observation_scope_closed = self._active is None
        maps, wrappers, ignored, app_code, transport_code, bindings_valid = self.bindings
        visible = False
        try:
            tool = self.bootstrap["tool"]
            global_zero = self.bootstrap["get_events"](tool) == 0
            masks_live = (
                self.bootstrap["get_local_events"](tool, transport_code) == self.bootstrap["line_event"]
                and self.bootstrap["get_local_events"](tool, app_code) == self.bootstrap["start_event"]
            )
            tool_live = self.bootstrap["get_tool"](tool) == _H7I_TOOL_NAME
            visible = global_zero and masks_live and tool_live
            self.global_mask_untouched = self.global_mask_untouched and global_zero
            if not global_zero:
                self._reject("global_mask_conflict")
            elif not masks_live:
                self._reject("local_mask_conflict")
            elif not tool_live:
                self._reject("callback_ownership")
        except BaseException:
            self.global_mask_untouched = False
            self._reject("teardown_incomplete")
        valid = not state.get("invalid") and visible and self.event_thread_consistent and type(response_status) is int
        if valid and response_status == 200:
            self.successes += 1
            return True
        if not valid:
            payload = _h7i_zero("incomplete" if type(response_status) is int else "invalid")
            payload.update(event_thread_consistent=self.event_thread_consistent, observation_scope_closed=self.observation_scope_closed, release_scope="pending_child_exit" if self.observation_scope_closed else "unreleased", global_mask_untouched=self.global_mask_untouched)
            payload["observer_rejection_reason"] = self.observer_rejection_reason
            return self._write(payload)
        response_class = "403" if response_status == 403 else "other_non_200"
        route, map_status, source_class, branch, locus = "unresolved", "unmapped", "unknown", "unknown", "unknown"
        if response_status == 403 and state["app_events"] == 1 and state["transport_events"] == 0:
            route, map_status, source_class, branch, locus = "app_graph_absent", "exact", "app_graph", "app.graph_absent", "app.graph_absent.L853"
        elif state["transport_events"] == 1 and state["app_events"] == 0:
            route = "runtime_exception_conversion"
            map_status, source_class, branch, locus, _reason = state["projection"]
        elif state["transport_events"] or state["app_events"]:
            map_status = "ambiguous"
        return self._write({
            "schema": _H7I_SCHEMA, "observer_status": "complete", "outcome": "failure",
            "response_class": response_class, "failing_request_ordinal": state["ordinal"],
            "page_source_class": state["source"], "route_class": route,
            "inner_map_status": map_status, "inner_source_class": source_class,
            "inner_branch": branch, "inner_locus_id": locus,
            "observer_rejection_reason": self.observer_rejection_reason,
            "event_thread_consistent": self.event_thread_consistent,
            "observation_scope_closed": True, "release_scope": "pending_child_exit",
            "global_mask_untouched": self.global_mask_untouched,
        })

    def finalize_all_success(self):
        if self.finalized or self._active is not None:
            return False
        if not (self.bindings[-1] and self.requests > 0 and self.requests == self.successes and self.event_thread_consistent and self.observation_scope_closed and self.global_mask_untouched):
            return self._write(_h7i_zero())
        return self._write({
            "schema": _H7I_SCHEMA, "observer_status": "complete", "outcome": "all_success",
            "response_class": "all_200", "failing_request_ordinal": None,
            "page_source_class": "unknown", "route_class": "unresolved",
            "inner_map_status": "unmapped", "inner_source_class": "unknown",
            "inner_branch": "none", "inner_locus_id": "none",
            "observer_rejection_reason": "none",
            "event_thread_consistent": True, "observation_scope_closed": True,
            "release_scope": "pending_child_exit",
            "global_mask_untouched": True,
        })

_h7i_observer = None
if globals().get("h7_classification_enabled") is True and globals().get("h7_classification_path") is not None:
    try:
        _h7i_observer = _H7InnerRejectObserver(h7_classification_path, _h7i_build_bindings())
    except BaseException:
        _h7i_observer = None
'''


_CONTEXT_POSITIVE_CHILD = (
    _TRIPWIRE + _FORGE_NATIVE_READ_TRIPWIRE + r'''
import base64
import cProfile
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time

''' + _CONTEXT_RESTART_BOOTSTRAP_DOMAIN_SOURCE + r'''

result_file = Path(os.environ["ODYSSEUS_CONTEXT_POSITIVE_RESULT_FILE"])
mode = os.environ.get("ODYSSEUS_CONTEXT_POSITIVE_MODE", "full")
h7_observation_requested = "ODYSSEUS_H7_MANIFEST_OBSERVATION" in os.environ
os.environ.pop("ODYSSEUS_H7_MANIFEST_OBSERVATION", None)
h7_profile_enabled = os.environ.pop("ODYSSEUS_H7_ACTUAL_PATH_PROFILE", None) == "1"
h7_profile_path_raw = os.environ.pop("ODYSSEUS_H7_ACTUAL_PATH_PROFILE_RESULT", None)
h7_profile_path = (
    Path(h7_profile_path_raw)
    if h7_profile_enabled and type(h7_profile_path_raw) is str and h7_profile_path_raw
    else None
)
h7_classification_enabled = os.environ.pop(
    "ODYSSEUS_H7_CONTENT_FAILURE_CLASSIFICATION", None,
) == "1"
h7_classification_path_raw = os.environ.pop(
    "ODYSSEUS_H7_CONTENT_FAILURE_CLASSIFICATION_RESULT", None,
)
h7_classification_path = (
    Path(h7_classification_path_raw)
    if h7_classification_enabled
    and type(h7_classification_path_raw) is str
    and h7_classification_path_raw
    else None
)
stage = "bootstrap"
try:
    assert mode in {"full", "readiness"}
    fixture_path = Path(os.environ["ODYSSEUS_CONTEXT_FIXTURE_FILE"])
    assert fixture_path.is_file() and 0 < fixture_path.stat().st_size <= _CONTEXT_FIXTURE_MAX_BYTES
    bootstrap = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert type(bootstrap) is dict and set(bootstrap) == {
        "allowed_owners", "allowed_sources", "expected_b64", "generation", "schema", "selection",
    }
    assert bootstrap["schema"] == "odysseus.actual_context_fixture.v1"
    assert type(bootstrap["selection"]) is list and len(bootstrap["selection"]) == 3
    assert type(bootstrap["expected_b64"]) is list and len(bootstrap["expected_b64"]) == 3
    expected_payloads = {
        index: base64.b64decode(value, validate=True)
        for index, value in enumerate(bootstrap["expected_b64"])
    }
    selection = bootstrap["selection"]
    assert [item.get("source") for item in selection] == ["forge", "knowledge", "planning"]
    os.environ.update({
        "ODYSSEUS_USI_RUNTIME_ENABLED": "true",
        "ODYSSEUS_USI_RUNTIME_MODE": "read_only",
        "ODYSSEUS_USI_SELECTED_GENERATION": bootstrap["generation"],
        "ODYSSEUS_USI_ALLOWED_OWNERS": bootstrap["allowed_owners"],
        "ODYSSEUS_USI_ALLOWED_SOURCES": bootstrap["allowed_sources"],
        "ODYSSEUS_USI_ALLOWED_DOMAINS": "native_knowledge",
        "ODYSSEUS_USI_SQLITE_PATH": "usi.sqlite",
        "ODYSSEUS_USI_QUERY_MAX_RESULTS": "8",
        "ODYSSEUS_USI_QUERY_TIMEOUT_SECONDS": "5",
    })
    stage = "import"
    from fastapi.testclient import TestClient
    import app as application

    if h7_classification_enabled and h7_classification_path is not None:
        try:
            from src.mcp_context_causal_observation import (
                arm_content_rejection_observation as _h7i_arm,
                begin_content_rejection_observation as _h7i_begin,
                end_content_rejection_observation as _h7i_end,
                finalize_content_rejection_success as _h7i_finalize,
            )

            class _H7DirectRejectAdapter:
                write_collision = False

                @staticmethod
                def begin(ordinal, source):
                    return _h7i_begin(ordinal, source)

                @staticmethod
                def end(status):
                    return _h7i_end(status)

                @staticmethod
                def finalize_all_success():
                    return _h7i_finalize()

            _h7i_observer = (
                _H7DirectRejectAdapter()
                if _h7i_arm(str(h7_classification_path))
                else None
            )
        except BaseException:
            _h7i_observer = None

    try:
        from importlib import import_module as _h7_import_module
        _h7_targets = (
            ("context_app.handle_mcp_request", "src.mcp_context_app", "McpContextApp", "handle_mcp_request"),
            ("transport.handle", "src.mcp_context_request_transport", "McpContextRequestTransport", "handle"),
            ("authenticate_mcp_request", "src.mcp_authenticated_dispatch", "", "authenticate_mcp_request"),
            ("runtime.handle", "src.mcp_context_request_runtime", "McpContextRequestRuntime", "handle"),
            ("runtime._existing", "src.mcp_context_request_runtime", "McpContextRequestRuntime", "_existing"),
            ("coordinator.publish_content", "src.mcp_context_pack_coordinator", "McpContextPackCoordinator", "publish_content"),
            ("coordinator._capture", "src.mcp_context_pack_coordinator", "McpContextPackCoordinator", "_capture"),
            ("coordinator._fence", "src.mcp_context_pack_coordinator", "McpContextPackCoordinator", "_fence"),
            ("producer.capture_mcp_snapshot", "src.context_manifest_producer", "CombinedContextManifestProducer", "capture_mcp_snapshot"),
            ("service._fence_disclosed_publication", "src.mcp_private_context_confirmation", "McpPrivateContextConfirmationService", "_fence_disclosed_publication"),
            ("authority._row_and_chat", "src.mcp_context_authority", "McpContextAuthority", "_row_and_chat"),
            ("proof.matches_hash", "src.mcp_authenticated_dispatch", "McpBearerProof", "matches_hash"),
            ("publisher.forge", "src.mcp_context_native_page", "", "publish_forge_page"),
            ("publisher.knowledge", "src.mcp_context_native_page", "", "publish_knowledge_page"),
            ("publisher.planning", "src.mcp_context_planning_page", "", "publish_planning_page"),
        )
        _h7_codes = tuple((label, getattr(
            getattr(_h7_import_module(module), owner) if owner else _h7_import_module(module), method,
        ).__code__) for label, module, owner, method in _h7_targets) if h7_profile_enabled else ()
    except BaseException:
        _h7_codes = ()

    class _H7ActualPathProfileMiddleware:
        _header = b"x-odysseus-test-actual-path-profile"
        _selector = b"first-content-page-v1"
        _maximum = 5_400_000

        def __init__(self, app, result_path):
            self.app, self.result_path, self.selected = app, result_path, False

        def _rows(self, profiler):
            rows = []
            stats = profiler.getstats()
            for label, code in _h7_codes:
                calls = self_ms = cumulative_ms = 0
                for entry in stats:
                    if entry.code is code:
                        calls += entry.callcount
                        self_ms += int(entry.inlinetime * 1000)
                        cumulative_ms += int(entry.totaltime * 1000)
                rows.append({
                    "label": label,
                    "calls": min(max(calls, 0), self._maximum),
                    "self_ms": min(max(self_ms, 0), self._maximum),
                    "cumulative_ms": min(max(cumulative_ms, 0), self._maximum),
                })
            return rows

        async def __call__(self, scope, receive, send):
            headers = scope.get("headers", ()) if scope.get("type") == "http" else ()
            selected = (
                scope.get("method") == "POST" and scope.get("path") == "/api/plugins/mcp"
                and not self.selected and any(
                    name.lower() == self._header and value == self._selector
                    for name, value in headers
                )
            )
            if not selected:
                await self.app(scope, receive, send)
                return
            self.selected = True
            clean_scope = dict(scope)
            clean_scope["headers"] = [
                (name, value) for name, value in headers if name.lower() != self._header
            ]
            header_removed = all(name.lower() != self._header for name, _ in clean_scope["headers"])
            response_status = None
            async def profiled_send(message):
                nonlocal response_status
                if message.get("type") == "http.response.start" and response_status is None:
                    response_status = message.get("status")
                await send(message)

            profiler = original_profile = None
            profile_ready = app_returned = False
            thread_id = threading.get_ident()
            started = time.perf_counter_ns()
            try:
                original_profile = sys.getprofile()
                if original_profile is None and len(_h7_codes) == 15:
                    profiler = cProfile.Profile()
                    profiler.enable()
                    profile_ready = True
            except BaseException:
                profile_ready = False
            try:
                await self.app(clean_scope, receive, profiled_send)
                app_returned = True
            finally:
                try:
                    same_thread = threading.get_ident() == thread_id
                    if profiler is not None:
                        profiler.disable()
                    restored = sys.getprofile() is original_profile
                    wall_ms = min(max((time.perf_counter_ns() - started) // 1_000_000, 0), self._maximum)
                    rows = self._rows(profiler) if profile_ready else [
                        {"label": label, "calls": 0, "self_ms": 0, "cumulative_ms": 0}
                        for label, _module, _owner, _method in _h7_targets
                    ]
                    valid = profile_ready and same_thread and restored and header_removed and len(rows) == 15
                    status = "complete" if valid and app_returned and type(response_status) is int else "incomplete" if valid else "invalid"
                    response_class = "200" if response_status == 200 else "504" if response_status == 504 else "other" if type(response_status) is int else "unobserved"
                    payload = {"schema": "odysseus.h7_actual_content_profile.v1", "status": status,
                               "response_class": response_class, "same_thread": same_thread,
                               "profiler_restored": restored, "selector_header_removed": header_removed,
                               "selected_requests": 1, "wall_ms": wall_ms, "rows": rows}
                    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
                    if self.result_path is not None and len(encoded) <= 4096:
                        with self.result_path.open("xb") as stream:
                            stream.write(encoded)
                except BaseException:
                    pass

    if h7_profile_enabled and h7_profile_path is not None:
        try:
            application.app.add_middleware(
                _H7ActualPathProfileMiddleware, result_path=h7_profile_path,
            )
        except BaseException:
            pass
    origin = {"Origin": "http://testserver"}
    password = "actual-context-pack-test-password-83!"
    session_id = "actual-three-source-context-chat"
    stage = "lifespan"
    with _forge_native_read_scope(application), TestClient(application.app) as client:
        stage = "login"
        assert client.post(
            "/api/auth/login", json={"username": "fuzzy", "password": password},
        ).status_code == 200
        cookie = client.cookies.get("odysseus_session")
        if mode == "readiness":
''' + _CONTEXT_READINESS_IN_LIFESPAN + r'''
        else:
            stage = "flow"
''' + textwrap.indent(_CONTEXT_POSITIVE_IN_LIFESPAN, "    ") + r'''
    if mode == "readiness":
        stage = "shutdown"
        assert application.app.state.mcp_context_app._started is False
        assert application.app.state.mcp_context_app._graph is None
    else:
        stage = "restart_bootstrap"
''' + textwrap.indent(_CONTEXT_POSITIVE_AFTER_LIFESPAN, "    ") + r'''
    stage = "complete"
    result_file.write_text(
        json.dumps({"ok": True, "stage": stage}, sort_keys=True), encoding="utf-8",
    )
except Exception:
    result_file.write_text(json.dumps({
        "ok": False,
        "stage": stage if stage in {
            "bootstrap", "import", "lifespan", "login", "readiness", "shutdown",
            "flow", "restart_bootstrap",
            "activation_status", "activation_response", "activation_enabled", "activation_reads",
            "activation_token_post", "activation_token_status", "activation_token_json",
            "activation_token_value", "activation_token_scope",
            "prepare_call", "prepare_status", "prepare_status_http_400",
            "prepare_status_http_401", "prepare_status_http_403", "prepare_status_http_408",
            "prepare_status_http_413", "prepare_status_http_503", "prepare_status_http_other",
            "prepare_status_http_504_under45", "prepare_status_http_504_ge45",
            "prepare_status_http_504_time_unobserved",
            "prepare_403_graph_absent", "prepare_403_graph_invalid",
            "prepare_403_singles_all_200",
            "prepare_403_singles_none_200_unresolved_shared_or_all_providers",
            "prepare_403_singles_forge_only_200", "prepare_403_singles_knowledge_only_200",
            "prepare_403_singles_planning_only_200", "prepare_403_singles_forge_knowledge_200",
            "prepare_403_singles_forge_planning_200", "prepare_403_singles_knowledge_planning_200",
            "prepare_403_singles_status_other", "prepare_403_probe_cleanup_failed",
            "prepare_result", "challenge_call",
            "challenge_status", "challenge_result", "ack_call", "ack_status",
            "ack_text", "ack_request_id", "ack_db",
            "flow_activation", "flow_prepare_ack", "flow_manifest", "flow_content",
            "flow_content_setup", "flow_content_page_count_precondition",
            "flow_content_rpc_call", "flow_content_http_status", "flow_content_result_decode",
            "flow_content_http_400", "flow_content_http_401", "flow_content_http_403",
            "flow_content_http_408", "flow_content_http_413", "flow_content_http_500",
            "flow_content_http_503", "flow_content_http_other_int", "flow_content_http_nonint",
            "flow_content_pack_id", "flow_content_read_only", "flow_content_authority_granted",
            "flow_content_send_performed", "flow_content_page_extract", "flow_content_source_index",
            "flow_content_base64_decode", "flow_content_offset", "flow_content_end",
            "flow_content_total", "flow_content_whole_digest", "flow_content_page_digest",
            "flow_content_byteform", "flow_content_accumulate", "flow_content_completion_read",
            "flow_content_eof_cursor", "flow_content_continuation_cursor",
            "flow_content_loop_exhausted", "flow_content_page_count", "flow_content_final_ends",
            "flow_content_reconstruction",
            "flow_manifest_call", "flow_manifest_status", "flow_manifest_http_401",
            "flow_manifest_http_403", "flow_manifest_http_500", "flow_manifest_http_other",
            "flow_manifest_http_400", "flow_manifest_http_408", "flow_manifest_http_413",
            "flow_manifest_http_503", "flow_manifest_http_202",
            "flow_manifest_http_other_int", "flow_manifest_http_nonint",
            "flow_manifest_result", "flow_manifest_read_only",
            "flow_manifest_authority_granted", "flow_manifest_send_performed",
            "flow_manifest_pack", "flow_manifest_items", "flow_manifest_complete",
            "flow_manifest_eof", "flow_manifest_cursor", "flow_manifest_limit",
            "flow_manifest_selection",
            "flow_preack_replay", "flow_cancel", "flow_nonstream", "flow_revoke_prepare",
            "flow_revoke_apply", "flow_revoke_replay", "flow_revoke_cancel",
            "flow_source_drift", "flow_graph_replace", "flow_aba",
            "flow_mixed_credentials", "flow_restart_seed", "flow_stale_challenge_seed",
            "flow_token_revoke", "flow_shutdown",
        } else "failed",
    }, sort_keys=True), encoding="utf-8")
    raise SystemExit(
        86
        if globals().get("_h7i_observer") is not None
        and _h7i_observer.write_collision is True
        else 1
    )
'''
)


_CONTEXT_RESTART_CHILD = (
    _TRIPWIRE + _FORGE_NATIVE_READ_TRIPWIRE + r'''
import base64
import hashlib
import json
import os
from pathlib import Path

''' + _CONTEXT_RESTART_BOOTSTRAP_DOMAIN_SOURCE + r'''

result_file = Path(os.environ["ODYSSEUS_CONTEXT_RESTART_RESULT_FILE"])
stage = "bootstrap"
checks = 0
try:
    bootstrap_path = Path(os.environ["ODYSSEUS_CONTEXT_RESTART_FILE"])
    bootstrap = _context_restart_bootstrap_read(bootstrap_path)
    assert type(bootstrap) is dict and set(bootstrap) == {
        "allowed_owners", "allowed_sources", "expected_b64", "generation", "selection",
        "stale_challenge", "stale_cursor", "stale_request_id",
    }
    assert all(type(bootstrap[key]) is str and bootstrap[key] for key in (
        "allowed_owners", "allowed_sources", "generation", "stale_challenge",
        "stale_cursor", "stale_request_id",
    ))
    assert type(bootstrap["selection"]) is list and len(bootstrap["selection"]) == 3
    assert type(bootstrap["expected_b64"]) is list and len(bootstrap["expected_b64"]) == 3
    expected_payloads = {
        index: base64.b64decode(value, validate=True)
        for index, value in enumerate(bootstrap["expected_b64"])
    }
    selection = bootstrap["selection"]
    os.environ.update({
        "ODYSSEUS_USI_RUNTIME_ENABLED": "true",
        "ODYSSEUS_USI_RUNTIME_MODE": "read_only",
        "ODYSSEUS_USI_SELECTED_GENERATION": bootstrap["generation"],
        "ODYSSEUS_USI_ALLOWED_OWNERS": bootstrap["allowed_owners"],
        "ODYSSEUS_USI_ALLOWED_SOURCES": bootstrap["allowed_sources"],
        "ODYSSEUS_USI_ALLOWED_DOMAINS": "native_knowledge",
        "ODYSSEUS_USI_SQLITE_PATH": "usi.sqlite",
        "ODYSSEUS_USI_QUERY_MAX_RESULTS": "8",
        "ODYSSEUS_USI_QUERY_TIMEOUT_SECONDS": "5",
    })
    stage = "import"
    from fastapi.testclient import TestClient
    import app as application
    stage = "lifespan"
    origin = {"Origin": "http://testserver"}
    mcp_browser = {
        "Origin": "http://testserver", "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "",
    }
    password = "actual-context-pack-test-password-83!"
    session_id = "actual-three-source-context-chat"
    with _forge_native_read_scope(application), TestClient(application.app) as client:
        stage = "login"
        assert client.post(
            "/api/auth/login", json={"username": "fuzzy", "password": password},
        ).status_code == 200
        cookie = client.cookies.get("odysseus_session")
        assert application.app.state.mcp_context_plugin_enabled is True
        assert application.app.state.allow_context_reads is True
        fresh = client.post("/api/tokens", headers=origin, data={
            "name": "actual-three-source-context-restart",
            "scopes": "mcp:harbour:context:read",
        })
        assert fresh.status_code == 200
        fresh_row = fresh.json()
        token = fresh_row["token"]

        def rpc(method, params, rpc_id):
            client.cookies.delete("odysseus_session")
            try:
                return client.post(
                    "/api/plugins/mcp",
                    headers={"Authorization": "Bearer " + token},
                    content=json.dumps({
                        "jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params,
                    }, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                )
            finally:
                client.cookies.set("odysseus_session", cookie)

        stage = "stale_fences"
        assert rpc("harbour_context_manifest", {
            "request_id": bootstrap["stale_request_id"],
        }, "stale-id").status_code == 403
        checks += 1
        assert rpc("harbour_context_page", {
            "request_id": bootstrap["stale_request_id"],
            "content_cursor": bootstrap["stale_cursor"],
        }, "stale-cursor").status_code == 403
        checks += 1
        assert client.post("/api/chat", headers=mcp_browser, json={
            "message": "/mcp-context-disclose " + bootstrap["stale_challenge"],
            "session": session_id,
        }).status_code == 400
        checks += 1
        stage = "fresh_graph"
        page_bytes = 1024
        prepared = rpc("harbour_context_prepare", {
            "chat_session_id": session_id,
            "selection": selection,
            "page_bytes": page_bytes,
        }, "fresh-prepare")
        assert prepared.status_code == 200
        fresh_request_id = prepared.json()["result"]["request_id"]
        fresh_challenge_response = client.post(
            "/api/context/mcp/challenge", headers=mcp_browser,
            json={"request_id": fresh_request_id},
        )
        assert fresh_challenge_response.status_code == 200
        fresh_stream_command = "/mcp-context-disclose " + fresh_challenge_response.json()["token"]
        fresh_stream_ack = client.post("/api/chat_stream", headers=mcp_browser, json={
            "message": fresh_stream_command, "session": session_id,
        })
        assert fresh_stream_ack.status_code == 200
        assert b"MCP private context disclosure confirmed." in fresh_stream_ack.content
        assert fresh_request_id.encode("ascii") in fresh_stream_ack.content
        checks += 1

        stage = "fresh_manifest"
        manifest_items = []
        manifest_cursor = None
        manifest_pack_id = None
        for manifest_page in range(1, 20):
            manifest_arguments = {"request_id": fresh_request_id}
            if manifest_cursor is not None:
                manifest_arguments["manifest_cursor"] = manifest_cursor
            manifest_response = rpc(
                "harbour_context_manifest", manifest_arguments,
                "fresh-manifest-" + str(manifest_page),
            )
            assert manifest_response.status_code == 200
            manifest_result = manifest_response.json()["result"]
            assert manifest_result["read_only"] is True
            assert manifest_result["authority_granted"] is False
            assert manifest_result["send_performed"] is False
            if manifest_pack_id is None:
                manifest_pack_id = manifest_result["pack_id"]
            assert manifest_result["pack_id"] == manifest_pack_id
            manifest_items.extend(manifest_result["items"])
            if manifest_result["complete"]:
                assert manifest_result["manifest_cursor"] is None
                break
            manifest_cursor = manifest_result["manifest_cursor"]
            assert type(manifest_cursor) is str and manifest_cursor
        else:
            raise AssertionError("restart MCP context manifest EOF not reached")
        assert [
            (item["source"], item["selection_ref"], item["revision_ref"])
            for item in manifest_items
        ] == [
            (item["source"], item["selection_ref"], item["revision_ref"])
            for item in selection
        ]
        checks += 1

        stage = "fresh_content"
        expected_by_source = {
            item["source"]: expected_payloads[index]
            for index, item in enumerate(selection)
        }
        expected_byteform = {
            "forge": "git_blob",
            "knowledge": "knowledge_payload",
            "planning": "durable_revision_document",
        }
        assert set(expected_by_source) == set(expected_byteform)
        pages_by_source = {source: bytearray() for source in expected_by_source}
        expected_content_pages = sum(
            (len(payload) + page_bytes - 1) // page_bytes
            for payload in expected_by_source.values()
        )
        assert expected_content_pages > len(expected_by_source)
        source_by_index = {index: item["source"] for index, item in enumerate(selection)}
        final_end_by_source = {}
        content_cursor = None
        content_page_count = 0
        for page_number in range(1, expected_content_pages + 1):
            arguments = {"request_id": fresh_request_id}
            if content_cursor is not None:
                arguments["content_cursor"] = content_cursor
            page_response = rpc(
                "harbour_context_page", arguments, "fresh-page-" + str(page_number),
            )
            assert page_response.status_code == 200
            page_result = page_response.json()["result"]
            assert page_result["pack_id"] == manifest_pack_id
            assert page_result["read_only"] is True
            assert page_result["authority_granted"] is False
            assert page_result["send_performed"] is False
            page = page_result["page"]
            content_page_count += 1
            source = source_by_index[page["target_index"]]
            expected_payload = expected_by_source[source]
            content = base64.b64decode(page["content_b64"], validate=True)
            assert page["offset"] == len(pages_by_source[source])
            assert page["end"] == page["offset"] + len(content)
            assert page["total"] == len(expected_payload)
            assert page["whole_sha256"] == "sha256:" + hashlib.sha256(expected_payload).hexdigest()
            assert page["page_sha256"] == "sha256:" + hashlib.sha256(content).hexdigest()
            assert page["byteform"] == expected_byteform[source]
            pages_by_source[source].extend(content)
            final_end_by_source[source] = (page["end"], page["total"])
            if page_result["complete"]:
                assert page_result["content_cursor"] is None
                break
            content_cursor = page_result["content_cursor"]
            assert type(content_cursor) is str and content_cursor
        else:
            raise AssertionError("restart MCP context EOF not reached")
        assert content_page_count == expected_content_pages
        assert final_end_by_source == {
            source: (len(payload), len(payload))
            for source, payload in expected_by_source.items()
        }
        assert {source: bytes(value) for source, value in pages_by_source.items()} == expected_by_source
        checks += 1

        stage = "fresh_nonstream"
        second_prepare = rpc("harbour_context_prepare", {
            "chat_session_id": session_id,
            "selection": selection,
            "page_bytes": page_bytes,
        }, "fresh-nonstream-prepare")
        assert second_prepare.status_code == 200
        second_request_id = second_prepare.json()["result"]["request_id"]
        second_challenge_response = client.post(
            "/api/context/mcp/challenge", headers=mcp_browser,
            json={"request_id": second_request_id},
        )
        assert second_challenge_response.status_code == 200
        fresh_nonstream_command = "/mcp-context-disclose " + second_challenge_response.json()["token"]
        fresh_nonstream_ack = client.post("/api/chat", headers=mcp_browser, json={
            "message": fresh_nonstream_command, "session": session_id,
        })
        assert fresh_nonstream_ack.status_code == 200
        assert fresh_nonstream_ack.json()["request_id"] == second_request_id
        checks += 1

        stage = "fresh_persistence"
        from core.database import ChatMessage, SessionLocal
        db = SessionLocal()
        try:
            assert db.query(ChatMessage).filter(
                ChatMessage.session_id == session_id,
                ChatMessage.role == "user",
                ChatMessage.content == fresh_stream_command,
            ).count() == 1
            assert db.query(ChatMessage).filter(
                ChatMessage.session_id == session_id,
                ChatMessage.role == "user",
                ChatMessage.content == fresh_nonstream_command,
            ).count() == 1
        finally:
            db.close()
        checks += 1

        assert rpc("harbour_context_cancel", {
            "request_id": second_request_id,
        }, "fresh-nonstream-cancel").json()["result"] == {"cancelled": True}
        checks += 1
        assert client.delete("/api/tokens/" + fresh_row["id"], headers=origin).status_code == 200
        assert rpc("harbour_context_manifest", {
            "request_id": fresh_request_id,
        }, "fresh-revoked").status_code == 401
        checks += 1
    stage = "complete"
    result_file.write_text(json.dumps({
        "checks": checks, "ok": True, "stage": stage,
    }, sort_keys=True), encoding="utf-8")
except Exception:
    result_file.write_text(json.dumps({
        "checks": checks if type(checks) is int and 0 <= checks <= 10 else 0,
        "ok": False,
        "stage": stage if stage in {
            "bootstrap", "import", "lifespan", "login", "stale_fences", "fresh_graph",
            "fresh_manifest", "fresh_content", "fresh_nonstream", "fresh_persistence",
        } else "failed",
    }, sort_keys=True), encoding="utf-8")
    raise SystemExit(1)
'''
)


def _context_restart_projection(path: Path) -> dict[str, object]:
    safe = {"checks": 0, "ok": False, "stage": "missing"}
    try:
        if not path.is_file() or not 0 < path.stat().st_size <= 256:
            return safe
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return safe
    stages = {
        "bootstrap", "import", "lifespan", "login", "stale_fences", "fresh_graph",
        "fresh_manifest", "fresh_content", "fresh_nonstream", "fresh_persistence",
        "failed", "complete",
    }
    if (
        type(value) is dict and set(value) == {"checks", "ok", "stage"}
        and type(value.get("checks")) is int and 0 <= value["checks"] <= 10
        and type(value.get("ok")) is bool
        and type(value.get("stage")) is str and value["stage"] in stages
    ):
        return {"checks": value["checks"], "ok": value["ok"], "stage": value["stage"]}
    return safe


def _context_restart_observation(
    projection: dict[str, object], child_exit_class: object,
) -> dict[str, object]:
    if type(child_exit_class) is not str or child_exit_class not in {
        "zero", "nonzero", "timeout", "unobserved",
    }:
        child_exit_class = "unobserved"
    return {
        "schema": "odysseus.h7_restart_observation.v1",
        "child_exit_class": child_exit_class,
        "checks": projection["checks"],
        "ok": projection["ok"],
        "stage": projection["stage"],
    }


_CONTEXT_CONTENT_LOCUS_STAGES = (
    "flow_content_setup",
    "flow_content_page_count_precondition",
    "flow_content_rpc_call",
    "flow_content_http_status",
    "flow_content_http_400",
    "flow_content_http_401",
    "flow_content_http_403",
    "flow_content_http_408",
    "flow_content_http_413",
    "flow_content_http_500",
    "flow_content_http_503",
    "flow_content_http_other_int",
    "flow_content_http_nonint",
    "flow_content_result_decode",
    "flow_content_pack_id",
    "flow_content_read_only",
    "flow_content_authority_granted",
    "flow_content_send_performed",
    "flow_content_page_extract",
    "flow_content_source_index",
    "flow_content_base64_decode",
    "flow_content_offset",
    "flow_content_end",
    "flow_content_total",
    "flow_content_whole_digest",
    "flow_content_page_digest",
    "flow_content_byteform",
    "flow_content_accumulate",
    "flow_content_completion_read",
    "flow_content_eof_cursor",
    "flow_content_continuation_cursor",
    "flow_content_loop_exhausted",
    "flow_content_page_count",
    "flow_content_final_ends",
    "flow_content_reconstruction",
)
_CONTEXT_CONTENT_STATUS_UNOBSERVED = object()


def _context_content_locus(stage, status=_CONTEXT_CONTENT_STATUS_UNOBSERVED):
    if type(stage) is not str:
        return "not_content"
    if stage == "flow_content_http_status":
        if status is _CONTEXT_CONTENT_STATUS_UNOBSERVED:
            return "flow_content_http_status"
        if type(status) is not int:
            return "flow_content_http_nonint"
        if status == 400:
            return "flow_content_http_400"
        if status == 401:
            return "flow_content_http_401"
        if status == 403:
            return "flow_content_http_403"
        if status == 408:
            return "flow_content_http_408"
        if status == 413:
            return "flow_content_http_413"
        if status == 500:
            return "flow_content_http_500"
        if status == 503:
            return "flow_content_http_503"
        if status == 200:
            return "flow_content_http_status"
        return "flow_content_http_other_int"
    if stage in _CONTEXT_CONTENT_LOCUS_STAGES:
        return stage
    return "not_content"


_H7_ACTUAL_PROFILE_LABELS = (
    "context_app.handle_mcp_request", "transport.handle", "authenticate_mcp_request",
    "runtime.handle", "runtime._existing", "coordinator.publish_content",
    "coordinator._capture", "coordinator._fence", "producer.capture_mcp_snapshot",
    "service._fence_disclosed_publication", "authority._row_and_chat",
    "proof.matches_hash", "publisher.forge", "publisher.knowledge", "publisher.planning",
)


def _context_actual_path_profile_read(
    path: Path,
) -> tuple[dict[str, object], str, str]:
    empty_rows = [
        {"label": label, "calls": 0, "self_ms": 0, "cumulative_ms": 0}
        for label in _H7_ACTUAL_PROFILE_LABELS
    ]
    safe = {
        "schema": "odysseus.h7_actual_content_profile.v1", "status": "invalid",
        "response_class": "unobserved", "same_thread": False,
        "profiler_restored": False, "selector_header_removed": False,
        "selected_requests": 0, "wall_ms": 0, "rows": empty_rows,
    }

    def exact_object(pairs):
        if len({key for key, _ in pairs}) != len(pairs):
            raise ValueError("duplicate key")
        return dict(pairs)

    try:
        with path.open("rb") as stream:
            encoded = stream.read(4097)
    except FileNotFoundError:
        return safe, "missing", "none"
    except OSError:
        return safe, "malformed", "none"
    if not 0 < len(encoded) <= 4096:
        return safe, "malformed", "none"
    try:
        value = json.loads(
            encoded.decode("utf-8"), object_pairs_hook=exact_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("nonfinite")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, TypeError, ValueError):
        return safe, "malformed", "none"
    if (
        type(value) is not dict
        or set(value) != {
            "schema", "status", "response_class", "same_thread", "profiler_restored",
            "selector_header_removed", "selected_requests", "wall_ms", "rows",
        }
        or value["schema"] != "odysseus.h7_actual_content_profile.v1"
        or type(value["status"]) is not str
        or value["status"] not in {"complete", "invalid", "incomplete"}
        or type(value["response_class"]) is not str
        or value["response_class"] not in {"200", "504", "other", "unobserved"}
        or type(value["same_thread"]) is not bool
        or type(value["profiler_restored"]) is not bool
        or type(value["selector_header_removed"]) is not bool
        or type(value["selected_requests"]) is not int
        or value["selected_requests"] not in {0, 1}
        or type(value["wall_ms"]) is not int
        or not 0 <= value["wall_ms"] <= 5_400_000
    ):
        return safe, "invalid", "envelope"
    rows = value["rows"]
    if type(rows) is not list or len(rows) != 15:
        return safe, "invalid", "rows"
    for index, row in enumerate(rows):
        if (
            type(row) is not dict
            or set(row) != {"label", "calls", "self_ms", "cumulative_ms"}
            or row.get("label") != _H7_ACTUAL_PROFILE_LABELS[index]
            or any(type(row.get(key)) is not int or not 0 <= row[key] <= 5_400_000
                   for key in ("calls", "self_ms", "cumulative_ms"))
            or row["self_ms"] > row["cumulative_ms"]
        ):
            return safe, "invalid", "rows"
    if value["status"] in {"invalid", "incomplete"}:
        return value, value["status"], "none"
    if (
        not value["same_thread"] or not value["profiler_restored"]
        or not value["selector_header_removed"] or value["selected_requests"] != 1
        or value["response_class"] == "unobserved"
    ):
        return {**value, "status": "invalid"}, "invalid", "integrity"
    core = {row["label"]: row["calls"] for row in rows}
    if (
        any(core[label] < 1 for label in (
            "context_app.handle_mcp_request", "transport.handle",
        ))
        or any(core[label] != 1 for label in (
            "runtime.handle", "runtime._existing", "coordinator.publish_content",
        ))
    ):
        return {**value, "status": "invalid"}, "invalid", "core_counts"
    return value, "complete", "none"


def _context_actual_path_profile_projection(path: Path) -> dict[str, object]:
    return _context_actual_path_profile_read(path)[0]


_H7_CONTENT_FAILURE_SOURCE_LINES = {
    source_id: frozenset(lines)
    for source_id, _module, lines in (
        ("runtime@8981615b", "src.mcp_context_request_runtime", (573,577,587,595,606,613,619,629,635,643,646,649,659,670,680,700,706,712,714,860,866,874,1145,1148,1169,1192,1196,1210,1225,1247,1259,1288,1309,1350,1353,1369,1374,1380,1387,1400,1430,1458,1489,1501,1534,1543,1547,1555,1573,1589,1614,1674,1689,1717,1736,2214,2264,2294,2322,2353,2357,2400,2459,2474,2502,2521,2524,3047,3048,3123,3146,3156,3163,3173,3194,3201,3209,3247,3255,3265,3275,3279,3354,3363,3383)),
        ("authority@e60f7798", "src.mcp_context_authority", (470,472,479,480,486,499,503,509,511,517,787,828,836,842,844,871,889,898,903,910,914,917,960,965,973,986,989,993,1002,1012,1016,1023,1079,1085,1091,1096,1105,1107,1113,1133,1138,1142,1146,1148,1152,1155,1180,1184,1187,1197,1200,1227,1231,1236,1239,1295,1298,1352,1354,1358,1371,1373,1374,1378,1458,1462,1465,1487,1495,1499,1507,1509,1512,1525,1538,1542,1544,1558,1569,1573,1576,1591,1607,1608,1610,1618,1621,1637,1641,1652,1738,1765,1776,1789,1882,1885,1901,2030,2049,2119,2131,2135,2137,2148,2158,2166,2171,2178,2282,2290,2301,2307,2339,2347,2363,2410,2416,2446,2471,2474,2492)),
        ("confirmation@ddc4da2a", "src.mcp_private_context_confirmation", (401,404,407,414,428,481,486,498,501,512,515,522,526,540,549,555,563,566,571,583,595,616,618,635,658,660,664,673,702,734,740,750,761,763,765,770,771,801,822,827,830,835,840,843,847,850,855,857,878,882,885,890,894,896,900,904,906,986,992,1010,1013,1024,1028,1030,1037,1039,1046,1056,1067,1074,1078,1094,1100,1107,1116,1123,1128,1131,1136,1142,1152,1162,1179,1194,1202,1216,1228,1233,1237,1240,1246,1250,1251,1254,1261,1267,1275,1293,1319,1320,1322,1324,1336,1344,1350,1359,1360,1575,1578,1580,1584,1588,1591,1593,1606,1616,1622,1627,1638,1647,1662,1684,1688,1700,1706,1707,1709,1713,1715,1720,1722,1726,1733,1735,1737,1742,1754,1755,1757,1759,1760,1768,1778,1800,1815,1822,1828,1833,1835,1841,1845,1848,1851,1857,1861,1872,1886,1895,1896,1901,1905,1921,1943,1983,1998,2001,2011,2017,2024,2026,2047,2060,2072,2079,2088,2098,2104,2109,2112,2136,2148,2153,2165,2179,2199,2204,2224,2235,2257,2271,2284,2296,2306,2333,2340,2348,2388,2391,2394,2397,2412,2434,2438,2448,2471,2532,2538,2540,2550,2555,2567,2578,2581,2616,2673,2674,2676,2681,2687,2690,2691,2697,2700,2703,2707,2715,2716,2720,2756,2758,2760,2768,2770,2774,2777,2791,2799,2804,2812,2829,2844,2852,2857,2867,2884,2891,2894,2898,2901,2905,2906,2921,2925,2930,2932,2933,2934)),
        ("coordinator@e044d29e", "src.mcp_context_pack_coordinator", (259,262,265,272,292,350,354,363,372,381,391,399,404,415,419,432,438,442,451,463,466,480,482,491,543,563,578,588,591,594,611,615,620,626,633,653,660,665,667,672,674,693,826,850,856,860,989,994,1007,1012,1016,1018,1031,1047,1052,1057,1078,1102,1124,1153,1156,1158,1213,1235,1240,1265,1268,1271,1288,1299,1340,1341,1364)),
    )
}
_H7_CONTENT_FAILURE_ARTIFACT_KEYS = frozenset({
    "schema", "observer_status", "outcome", "failing_request_ordinal", "response_class",
    "boundary_status", "phase", "chain_status", "source_chain", "same_thread", "observer_restored",
    "selector_headers_removed", "frontier_status", "coordinator_entry", "gate_family",
})
_H7_CONTENT_EXCEPTION_PHASES = frozenset({
    "permit_issuance", "selection_validation", "cursor_verification",
    "forge_publication", "planning_publication", "knowledge_publication",
    "inner_page_contract", "content_cursor_issuance", "outer_response_encoding",
    "final_freshness_validation", "finish",
})
_H7_CONTENT_EXCEPTION_CHAIN_STATUSES = frozenset({
    "complete", "unknown_site", "ambiguous", "cycle", "truncated", "invalid",
})
_H7_CONTENT_EXCEPTION_SITES = frozenset({
    "permit_issue", "selection_validate", "cursor_verify", "forge_dispatch",
    "planning_dispatch", "knowledge_dispatch", "inner_page_contract",
    "content_cursor_issue", "outer_response_encode", "final_freshness_validate",
    "finish", "planning_execution", "planning_failure", "planning_consumption",
    "native_execution", "native_recovery", "native_consumption",
})
_H7_CONTENT_FRONTIER_STATUSES = frozenset({"exact", "unresolved", "invalid"})
_H7_CONTENT_COORDINATOR_ENTRIES = frozenset({"absent", "present", "invalid"})
_H7_CONTENT_GATE_FAMILIES = frozenset({
    "app_graph", "transport_pre_runtime", "transport_runtime_dispatch",
    "runtime_entry", "runtime_record_identity", "pristine_intent_pins",
    "binding_lease_recheck", "native_lease_state", "publication_open", "coordinator",
    "postpublication_validation", "publication_exit", "cleanup", "unknown",
})


def _context_content_failure_zero_artifact() -> dict[str, object]:
    return {
        "schema": "odysseus.h7_content_exception_phase.v2",
        "observer_status": "invalid", "outcome": "invalid", "failing_request_ordinal": None,
        "response_class": "invalid", "boundary_status": "invalid", "phase": None,
        "chain_status": "invalid", "source_chain": [], "frontier_status": "invalid",
        "coordinator_entry": "invalid", "gate_family": "unknown",
        "same_thread": False, "observer_restored": False, "selector_headers_removed": False,
    }


def _context_content_failure_artifact_valid(value: object) -> bool:
    if type(value) is not dict or set(value) != _H7_CONTENT_FAILURE_ARTIFACT_KEYS:
        return False
    if (
        value.get("schema") != "odysseus.h7_content_exception_phase.v2"
        or type(value.get("observer_status")) is not str
        or value["observer_status"] not in {"complete", "invalid", "incomplete"}
        or type(value.get("outcome")) is not str
        or value["outcome"] not in {"failure", "all_success", "invalid"}
        or type(value.get("response_class")) is not str
        or value["response_class"] not in {"403", "other_non_200", "all_200", "invalid"}
        or type(value.get("boundary_status")) is not str
        or value["boundary_status"] not in {"exact", "missing", "invalid"}
        or value.get("phase") is not None
        and (type(value["phase"]) is not str or value["phase"] not in _H7_CONTENT_EXCEPTION_PHASES)
        or type(value.get("chain_status")) is not str
        or value["chain_status"] not in _H7_CONTENT_EXCEPTION_CHAIN_STATUSES
        or type(value.get("source_chain")) is not list
        or len(value["source_chain"]) > 8
        or value.get("frontier_status") not in _H7_CONTENT_FRONTIER_STATUSES
        or value.get("coordinator_entry") not in _H7_CONTENT_COORDINATOR_ENTRIES
        or value.get("gate_family") not in _H7_CONTENT_GATE_FAMILIES
        or type(value.get("same_thread")) is not bool
        or type(value.get("observer_restored")) is not bool
        or type(value.get("selector_headers_removed")) is not bool
    ):
        return False
    for item in value["source_chain"]:
        if (
            type(item) is not dict or set(item) != {"phase", "site"}
            or type(item.get("phase")) is not str
            or item["phase"] not in _H7_CONTENT_EXCEPTION_PHASES
            or type(item.get("site")) is not str
            or item["site"] not in _H7_CONTENT_EXCEPTION_SITES
        ):
            return False
    ordinal = value.get("failing_request_ordinal")
    if ordinal is not None and (type(ordinal) is not int or not 1 <= ordinal <= 4096):
        return False
    if value["observer_status"] == "complete":
        if not (value["same_thread"] and value["observer_restored"] and value["selector_headers_removed"]):
            return False
        if value["outcome"] == "failure":
            if not (
                type(ordinal) is int
                and value["response_class"] in {"403", "other_non_200"}
            ):
                return False
            if (
                value["frontier_status"] == "exact" and value["gate_family"] == "unknown"
                or value["frontier_status"] != "exact" and value["gate_family"] != "unknown"
                or value["coordinator_entry"] == "invalid"
                or value["gate_family"] == "app_graph" and not (
                    value["response_class"] == "403"
                    and value["coordinator_entry"] == "absent"
                    and value["boundary_status"] == "missing"
                )
                or value["gate_family"] == "coordinator" and not (
                    value["coordinator_entry"] == "present"
                    and value["boundary_status"] == "exact"
                )
                or value["gate_family"] in {
                    "postpublication_validation", "publication_exit", "cleanup",
                } and value["coordinator_entry"] != "present"
                or value["coordinator_entry"] == "present"
                and value["gate_family"] not in {
                    "coordinator", "postpublication_validation", "publication_exit",
                    "cleanup", "unknown",
                }
            ):
                return False
            if value["boundary_status"] == "exact":
                return (
                    value["phase"] in _H7_CONTENT_EXCEPTION_PHASES
                    or value["phase"] is None
                    and value["chain_status"] in {"complete", "unknown_site", "invalid"}
                    and value["source_chain"] == []
                )
            if value["boundary_status"] == "missing":
                return (
                    value["phase"] is None and value["chain_status"] == "complete"
                    and value["source_chain"] == []
                )
            return (
                value["phase"] is None and value["chain_status"] == "invalid"
                and value["source_chain"] == []
            )
        elif value["outcome"] == "all_success":
            return (
                ordinal is None and value["response_class"] == "all_200"
                and value["boundary_status"] == "missing" and value["phase"] is None
                and value["chain_status"] == "complete" and value["source_chain"] == []
                and value["frontier_status"] == "unresolved"
                and value["coordinator_entry"] == "absent"
                and value["gate_family"] == "unknown"
            )
        return False
    return (
        value["outcome"] == "invalid" and ordinal is None
        and value["response_class"] == value["boundary_status"] == "invalid"
        and value["phase"] is None and value["chain_status"] == "invalid"
        and value["source_chain"] == [] and value["frontier_status"] == "invalid"
        and value["coordinator_entry"] == "invalid" and value["gate_family"] == "unknown"
    )


_H7_CONTENT_INNER_ARTIFACT_KEYS = frozenset({
    "schema", "observer_status", "outcome", "response_class",
    "failing_request_ordinal", "page_source_class", "route_class",
    "inner_map_status", "inner_source_class", "inner_branch", "inner_locus_id",
    "observer_rejection_reason",
    "event_thread_consistent", "observation_scope_closed", "release_scope",
    "global_mask_untouched",
})
_H7_CONTENT_INNER_SOURCE_CLASSES = frozenset({
    "runtime", "authority", "confirmation", "cursor", "coordinator",
    "native", "planning", "app_graph", "unknown", "invalid",
})
_H7_CONTENT_INNER_REJECTION_REASONS = frozenset({
    "layout_pin_mismatch", "global_mask_conflict", "callback_ownership",
    "local_mask_conflict", "event_thread_mismatch", "event_overflow",
    "chain_cycle", "chain_truncated", "traceback_metadata_mismatch",
    "projection_shape", "artifact_collision", "teardown_incomplete",
    "bootstrap_flags", "bootstrap_path", "bootstrap_import", "bootstrap_binding",
    "overlap", "replay", "traceback_truncated",
    "none", "unknown",
})
_H7_CONTENT_INNER_BRANCHES = frozenset({
    "runtime.content_preflight", "runtime.native_lease_acquire",
    "runtime.native_lease_shape", "runtime.native_lease_live", "runtime.record_live",
    "runtime.publication_open", "runtime.postpublication_validation",
    "runtime.publication_exit", "runtime.cleanup", "authority.native_lease_acquire",
    "authority.lease_recheck.snapshot", "authority.lease_recheck.state_lookup",
    "authority.lease_recheck.cancelled_marker", "authority.lease_recheck.missing",
    "authority.lease_recheck.cancelled_row", "authority.lease_recheck.clock_read",
    "authority.lease_recheck.clock_validity", "authority.lease_recheck.expiry",
    "authority.lease_recheck.binding", "authority.lease_recheck.propagation",
    "authority.binding_recheck.snapshot", "authority.binding_recheck.identity",
    "authority.binding_recheck.proof_ownership", "authority.binding_recheck.live_precheck",
    "authority.binding_recheck.proof_shape", "authority.binding_recheck.authority_state",
    "authority.binding_recheck.live_postcheck", "authority.binding_recheck.final_ownership",
    "authority.publication_open", "confirmation.permit_issue",
    "confirmation.permit_reject", "confirmation.publication_fence",
    "cursor.content_verify", "cursor.content_issue", "coordinator.selection_validate",
    "coordinator.permit_issue", "coordinator.cursor_verify", "coordinator.forge_dispatch",
    "coordinator.planning_dispatch", "coordinator.knowledge_dispatch",
    "coordinator.inner_page_contract", "coordinator.content_cursor_issue",
    "coordinator.response_encode", "coordinator.final_freshness",
    "coordinator.finish", "coordinator.active_guard", "coordinator.capture",
    "coordinator.fence", "native.execution", "native.consumption",
    "native.recovery", "native.detached_encode", "planning.execution",
    "planning.consumption", "planning.failure", "planning.detached_encode",
    "app.graph_absent", "none", "unknown", "invalid",
})
_H7_CONTENT_INNER_LOCUS_RANGES = (
    ("runtime.existing", "runtime", ((3141,3157,"runtime.content_preflight"),(3157,3174,"runtime.native_lease_acquire"),(3174,3195,"runtime.native_lease_shape"),(3195,3202,"runtime.native_lease_live"),(3202,3210,"runtime.record_live"),(3215,3234,"runtime.publication_open"),(3234,3242,"runtime.postpublication_validation"),(3242,3256,"runtime.publication_exit"),(3280,3334,"runtime.cleanup"))),
    ("authority.acquire", "authority", ((1006,1028,"authority.native_lease_acquire"),)),
    ("authority.state", "authority", ((399,419,"authority.native_lease_acquire"),)),
    ("authority.lease_recheck", "authority", ((1600,1602,"authority.lease_recheck.snapshot"),(1602,1607,"authority.lease_recheck.state_lookup"),(1607,1608,"authority.lease_recheck.cancelled_marker"),(1608,1609,"authority.lease_recheck.missing"),(1609,1611,"authority.lease_recheck.cancelled_row"),(1611,1617,"authority.lease_recheck.clock_read"),(1617,1619,"authority.lease_recheck.clock_validity"),(1619,1622,"authority.lease_recheck.expiry"),(1622,1624,"authority.lease_recheck.binding"),(1624,1630,"authority.lease_recheck.propagation"))),
    ("authority.binding_recheck", "authority", ((1490,1492,"authority.binding_recheck.snapshot"),(1492,1500,"authority.binding_recheck.identity"),(1500,1508,"authority.binding_recheck.proof_ownership"),(1508,1510,"authority.binding_recheck.live_precheck"),(1510,1511,"authority.binding_recheck.proof_shape"),(1511,1512,"authority.binding_recheck.authority_state"),(1512,1513,"authority.binding_recheck.live_postcheck"),(1513,1526,"authority.binding_recheck.final_ownership"))),
    ("authority.open", "authority", ((2227,2505,"authority.publication_open"),)),
    ("confirmation.issue", "confirmation", ((1166,1369,"confirmation.permit_issue"),)),
    ("confirmation.reject", "confirmation", ((1369,1630,"confirmation.permit_reject"),)),
    ("confirmation.fence", "confirmation", ((2185,2505,"confirmation.publication_fence"),)),
    ("cursor.issue_public", "cursor", ((435,450,"cursor.content_issue"),)),
    ("cursor.issue", "cursor", ((363,418,"cursor.content_issue"),)),
    ("cursor.verify_public", "cursor", ((550,567,"cursor.content_verify"),)),
    ("cursor.verify", "cursor", ((467,534,"cursor.content_verify"),)),
    ("coordinator.publish", "coordinator", ((1215,1236,"coordinator.selection_validate"),(1236,1254,"coordinator.permit_issue"),(1254,1274,"coordinator.cursor_verify"),(1274,1278,"coordinator.forge_dispatch"),(1278,1283,"coordinator.planning_dispatch"),(1283,1288,"coordinator.knowledge_dispatch"),(1288,1307,"coordinator.inner_page_contract"),(1307,1313,"coordinator.content_cursor_issue"),(1313,1325,"coordinator.response_encode"),(1325,1329,"coordinator.final_freshness"),(1329,1342,"coordinator.finish"))),
    ("coordinator.begin", "coordinator", ((1021,1042,"coordinator.selection_validate"),)),
    ("coordinator.active", "coordinator", ((1042,1049,"coordinator.active_guard"),)),
    ("coordinator.finish_helper", "coordinator", ((1049,1063,"coordinator.finish"),)),
    ("coordinator.abort", "coordinator", ((1063,1073,"coordinator.finish"),)),
    ("coordinator.capture", "coordinator", ((1073,1090,"coordinator.capture"),)),
    ("coordinator.fence", "coordinator", ((1090,1098,"coordinator.fence"),)),
    ("coordinator.encode", "coordinator", ((1098,1105,"coordinator.response_encode"),)),
    ("native.publish", "native", ((298,343,"native.execution"),(343,350,"native.recovery"),(350,354,"native.consumption"))),
    ("native.detached", "native", ((310,344,"native.detached_encode"),)),
    ("planning.publish", "planning", ((180,228,"planning.execution"),(228,240,"planning.failure"),(240,246,"planning.consumption"))),
    ("planning.detached", "planning", ((195,229,"planning.detached_encode"),)),
)
_H7_CONTENT_INNER_LOCUS_SOURCE = {
    label + ".L" + str(line): (source_class, branch, line)
    for label, source_class, ranges in _H7_CONTENT_INNER_LOCUS_RANGES
    for first, stop, branch in ranges for line in range(first, stop)
}
_H7_CONTENT_INNER_LOCUS_SOURCE.update({
    branch + ".L" + str(line): (source_class, branch, line)
    for _label, source_class, ranges in _H7_CONTENT_INNER_LOCUS_RANGES
    for first, stop, branch in ranges for line in range(first, stop)
})
_H7_CONTENT_INNER_LOCUS_SOURCE["app.handle_mcp_request.graph_absent"] = (
    "app_graph", "app.graph_absent", 0,
)


def _context_content_failure_zero_artifact() -> dict[str, object]:
    return {
        "schema": "odysseus.h7_content_inner_reject.v2",
        "observer_status": "invalid", "outcome": "invalid", "response_class": "invalid",
        "failing_request_ordinal": None, "page_source_class": "invalid",
        "route_class": "invalid", "inner_map_status": "invalid",
        "inner_source_class": "invalid", "inner_branch": "invalid",
        "inner_locus_id": "invalid", "event_thread_consistent": False,
        "observation_scope_closed": False,
        "observer_rejection_reason": "unknown",
        "release_scope": "observer_disarmed", "global_mask_untouched": True,
    }


def _context_content_failure_artifact_valid(value: object) -> bool:
    if type(value) is not dict or set(value) != _H7_CONTENT_INNER_ARTIFACT_KEYS:
        return False
    if (
        value.get("schema") != "odysseus.h7_content_inner_reject.v2"
        or value.get("observer_status") not in {"complete", "incomplete", "invalid"}
        or value.get("outcome") not in {"failure", "all_success", "invalid"}
        or value.get("response_class") not in {"403", "other_non_200", "all_200", "invalid"}
        or value.get("page_source_class") not in {"forge", "knowledge", "planning", "unknown", "invalid"}
        or value.get("route_class") not in {"app_graph_absent", "runtime_exception_conversion", "unresolved", "invalid"}
        or value.get("inner_map_status") not in {"exact", "unmapped", "ambiguous", "invalid"}
        or value.get("inner_source_class") not in _H7_CONTENT_INNER_SOURCE_CLASSES
        or value.get("inner_branch") not in _H7_CONTENT_INNER_BRANCHES
        or type(value.get("inner_locus_id")) is not str
        or value.get("observer_rejection_reason") not in _H7_CONTENT_INNER_REJECTION_REASONS
        or value.get("release_scope") != "observer_disarmed"
        or any(type(value.get(key)) is not bool for key in (
            "event_thread_consistent", "observation_scope_closed", "global_mask_untouched",
        ))
    ):
        return False
    ordinal = value.get("failing_request_ordinal")
    if ordinal is not None and (type(ordinal) is not int or not 1 <= ordinal <= 4096):
        return False
    exact = value["inner_map_status"] == "exact"
    if exact:
        mapped = _H7_CONTENT_INNER_LOCUS_SOURCE.get(value["inner_locus_id"])
        if mapped is None or mapped[:2] != (value["inner_source_class"], value["inner_branch"]):
            return False
    elif not (
        value["inner_source_class"] in {"unknown", "invalid"}
        and value["inner_branch"] in {"unknown", "invalid", "none"}
        and value["inner_locus_id"] in {"unknown", "invalid", "none"}
    ):
        return False
    if value["observer_status"] == "complete":
        if not (
            value["event_thread_consistent"] and value["observation_scope_closed"]
            and value["global_mask_untouched"]
            and value["release_scope"] == "observer_disarmed"
        ):
            return False
        if value["observer_rejection_reason"] != "none":
            return False
        if value["outcome"] == "all_success":
            return (
                ordinal is None and value["response_class"] == "all_200"
                and value["page_source_class"] == "unknown"
                and value["route_class"] == "unresolved"
                and value["inner_map_status"] == "unmapped"
                and value["inner_branch"] == value["inner_locus_id"] == "none"
            )
        if value["outcome"] != "failure" or type(ordinal) is not int or value["page_source_class"] not in {"forge", "knowledge", "planning"}:
            return False
        if value["route_class"] == "app_graph_absent":
            return value["response_class"] == "403" and exact and value["inner_locus_id"] == "app.handle_mcp_request.graph_absent"
        if value["route_class"] == "runtime_exception_conversion":
            return value["inner_map_status"] in {"exact", "unmapped", "ambiguous"}
        return value["route_class"] == "unresolved" and value["inner_map_status"] == "unmapped"
    return (
        value["outcome"] == "invalid" and ordinal is None
        and value["response_class"] == value["page_source_class"] == value["route_class"] == "invalid"
        and value["inner_map_status"] == value["inner_source_class"] == value["inner_branch"] == value["inner_locus_id"] == "invalid"
        and value["observer_rejection_reason"] != "none"
        and value["release_scope"] == "observer_disarmed"
    )


def _context_content_failure_read(
    path: Path | None, minimum_mtime_ns: int | None = None,
) -> tuple[dict[str, object], str]:
    safe = _context_content_failure_zero_artifact()

    def exact_object(pairs):
        result = {}
        for key, item in pairs:
            if type(key) is not str or key in result:
                raise ValueError("duplicate key")
            result[key] = item
        return result

    if type(path) is not type(Path(".")):
        return safe, "missing"
    try:
        before = path.lstat()
        if (
            before.st_mode & 0o170000 != 0o100000
            or before.st_nlink != 1
            or getattr(before, "st_file_attributes", 0) & 1024
            or type(minimum_mtime_ns) is int and before.st_mtime_ns < minimum_mtime_ns
        ):
            return safe, "invalid"
        with path.open("rb") as stream:
            encoded = stream.read(4097)
        after = path.lstat()
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or after.st_nlink != 1
        ):
            return safe, "invalid"
    except FileNotFoundError:
        return safe, "missing"
    except OSError:
        return safe, "malformed"
    if not 0 < len(encoded) <= 4096:
        return safe, "malformed"
    try:
        value = json.loads(
            encoded.decode("ascii"), object_pairs_hook=exact_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("nonfinite")),
        )
        if json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
        ).encode("ascii") != encoded:
            return safe, "malformed"
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, TypeError, ValueError):
        return safe, "malformed"
    if not _context_content_failure_artifact_valid(value):
        return safe, "invalid"
    artifact_class = (
        "valid" if value["observer_status"] == "complete"
        else "incomplete" if value["observer_status"] == "incomplete"
        else "invalid"
    )
    return value, artifact_class


def _context_content_failure_observation(
    path: Path | None, minimum_mtime_ns: int | None, evidence: object, child_exit_class: object,
    child_returncode: object = None,
) -> dict[str, object]:
    artifact, artifact_class = _context_content_failure_read(path, minimum_mtime_ns)
    if type(child_returncode) is int and child_returncode == 86:
        artifact, artifact_class = _context_content_failure_zero_artifact(), "invalid"
    if child_exit_class not in {"zero", "nonzero", "timeout", "unobserved"}:
        child_exit_class = "unobserved"
    terminal_child = (
        type(child_returncode) is int
        and (
            (child_exit_class == "zero" and child_returncode == 0)
            or (child_exit_class == "nonzero" and child_returncode != 0)
        )
    )
    allowed_stages = set(_CONTEXT_CONTENT_LOCUS_STAGES) | {
        "missing", "failed", "complete", "bootstrap", "import", "lifespan", "login",
        "flow", "restart_bootstrap",
    }
    child_ok = False
    child_stage = "missing"
    if (
        type(evidence) is dict and set(evidence) == {"ok", "stage"}
        and type(evidence.get("ok")) is bool and type(evidence.get("stage")) is str
        and evidence["stage"] in allowed_stages
    ):
        child_ok = evidence["ok"]
        child_stage = evidence["stage"]
    return {
        "schema": "odysseus.h7_content_inner_reject.observation.v2",
        "artifact_class": artifact_class, "child_exit_class": child_exit_class,
        "application_child_reaped": terminal_child,
        "child_ok": child_ok, "child_stage": child_stage, "classification": artifact,
    }


def _context_stage_projection(path: Path, allowed: set[str]) -> dict[str, object]:
    safe = {"ok": False, "stage": "missing"}
    try:
        if not path.is_file() or not 0 < path.stat().st_size <= 256:
            return safe
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return safe
    if (
        type(value) is dict and set(value) == {"ok", "stage"}
        and type(value.get("ok")) is bool
        and type(value.get("stage")) is str and value["stage"] in allowed
    ):
        return {"ok": value["ok"], "stage": value["stage"]}
    return safe


def _context_fixture_projection(path: Path) -> dict[str, object]:
    safe = {"ok": False, "stage": "failed", "error_kind": "other", "failure_line": 0}
    try:
        if not path.is_file() or not 0 < path.stat().st_size <= 256:
            return safe
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return safe
    if (
        type(value) is dict and set(value) == {"ok", "stage"}
        and value.get("ok") is True and value.get("stage") == "complete"
    ):
        return {"ok": True, "stage": "complete"}
    if not (
        type(value) is dict
        and set(value) == {"ok", "stage", "error_kind", "failure_line"}
        and value.get("ok") is False
        and type(value.get("stage")) is str
        and type(value.get("error_kind")) is str
        and value["error_kind"] in _CONTEXT_FIXTURE_ERROR_KINDS
        and type(value.get("failure_line")) is int
        and 0 <= value["failure_line"] <= 399
    ):
        return safe
    expected_stage = next((
        stage for stage, start, end in _CONTEXT_FIXTURE_STAGE_RANGES
        if start <= value["failure_line"] <= end
    ), None)
    if not (
        (value["failure_line"] == 0 and value["stage"] == "failed")
        or (expected_stage is not None and value["stage"] == expected_stage)
    ):
        return safe
    return {
        "ok": False,
        "stage": value["stage"],
        "error_kind": value["error_kind"],
        "failure_line": value["failure_line"],
    }


def test_content_rejection_original_child_start_is_plain_b_and_strict_v2():
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    target = next(
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "test_actual_default_app_authenticated_context_three_source_eof_and_restart_fences"
    )
    rendered = ast.unparse(target)
    assert "positive_argv = [sys.executable, '-B', str(child_script)]" in rendered
    assert "'-I'" not in rendered and "'-S'" not in rendered
    assert "_H7_CONTENT_INNER_BOOTSTRAP_SOURCE + _CONTEXT_POSITIVE_CHILD" not in rendered
    assert "child_script.write_text(_CONTEXT_POSITIVE_CHILD" in rendered
    assert '"odysseus.h7_content_inner_reject.v2"' in source
    assert '"odysseus_h7_content_inner_reject_v2"' in source
    assert '"release_scope": "observer_disarmed"' in source
    restart_builder = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_context_restart_observation"
    )
    restart_builder_source = ast.unparse(restart_builder)
    assert "'schema': 'odysseus.h7_restart_observation.v1'" in restart_builder_source
    assert "'child_exit_class': child_exit_class" in restart_builder_source
    assert "'checks': projection['checks']" in restart_builder_source
    assert "'ok': projection['ok']" in restart_builder_source
    assert "'stage': projection['stage']" in restart_builder_source
    restart_export = next(
        node for node in target.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "export_h7_restart_observation"
    )
    restart_export_source = ast.unparse(restart_export)
    assert "if not classification_enabled" in restart_export_source
    assert "'odysseus_h7_restart_observation_v1'" in restart_export_source
    assert "sort_keys=True" in restart_export_source
    assert "separators=(',', ':')" in restart_export_source
    assert "ensure_ascii=True" in restart_export_source
    assert "allow_nan=False" in restart_export_source
    restart_finally = [
        node for node in ast.walk(target)
        if isinstance(node, ast.Try)
        and any("export_h7_restart_observation" in ast.unparse(item) for item in node.finalbody)
    ]
    assert len(restart_finally) == 1
    restart_run_try = next(
        node for node in ast.walk(restart_finally[0])
        if isinstance(node, ast.Try)
        and len(node.handlers) == 1
        and "restarted = subprocess.run" in ast.unparse(node)
        and "restart_child_script" in ast.unparse(node)
    )
    assert len(restart_run_try.handlers) == 1
    timeout_handler = restart_run_try.handlers[0]
    assert ast.unparse(timeout_handler.type) == "subprocess.TimeoutExpired"
    assert any(
        isinstance(node, ast.Raise) and node.exc is None and node.cause is None
        for node in timeout_handler.body
    )
    restart_run = next(
        node for node in ast.walk(restart_run_try)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and ast.unparse(node.func) == "subprocess.run"
    )
    restart_keywords = {item.arg: ast.unparse(item.value) for item in restart_run.keywords}
    assert restart_keywords["env"] == "env"
    assert restart_keywords["timeout"] == "600"
    base_env_statements = [
        node for node in target.body
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(item, ast.Name) and item.id == "env" for item in node.targets)
        ) or (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and ast.unparse(node.value.func) == "env.update"
        )
    ]
    assert len(base_env_statements) == 2
    base_env_source = "\n".join(ast.unparse(node) for node in base_env_statements)
    assert "ODYSSEUS_H7_CONTENT_FAILURE_CLASSIFICATION" not in base_env_source
    assert "restart_evidence = {'checks': 0, 'ok': False, 'stage': 'missing'}" in rendered
    restart_export_guards = [
        node for node in restart_finally[0].finalbody
        if isinstance(node, ast.If)
        and any("export_h7_restart_observation" in ast.unparse(item) for item in node.body)
    ]
    assert len(restart_export_guards) == 1
    expected_restart_export_guard = ast.parse(
        "classification_enabled and type(process.returncode) is int "
        "and process.returncode == 0 and evidence == {'ok': True, 'stage': 'complete'}",
        mode="eval",
    ).body
    assert ast.dump(
        restart_export_guards[0].test, include_attributes=False,
    ) == ast.dump(expected_restart_export_guard, include_attributes=False)


def test_actual_default_app_authenticated_context_three_source_eof_and_restart_fences(tmp_path, request):
    repo_root = Path(__file__).resolve().parents[1]
    data = tempfile.TemporaryDirectory(prefix="h7-context-", dir=repo_root / ".abc-test-tmp")
    request.addfinalizer(data.cleanup)
    data = Path(data.name)
    data_root = data / "data"
    source = data_root / "server_projects" / "actual-forge"
    source.mkdir(parents=True)
    (source / ".no-hooks").mkdir()
    _context_pack_harness._git(source, "init", "-b", "main")
    _context_pack_harness._git(source, "config", "user.email", "actual@example.invalid")
    _context_pack_harness._git(source, "config", "user.name", "Actual Fixture")
    _context_pack_harness._git(source, "config", "core.autocrlf", "false")
    (source / "public.py").write_bytes(b"print('actual')\n")
    _context_pack_harness._git(source, "add", "public.py")
    _context_pack_harness._git(source, "commit", "-m", "actual context fixture")
    commit = _context_pack_harness._git(source, "rev-parse", "HEAD", capture=True)
    child = data / "child"
    child.mkdir()
    diagnostic_enabled = "ODYSSEUS_H7_MANIFEST_OBSERVATION" in os.environ
    actual_profile_enabled = os.environ.get("ODYSSEUS_H7_ACTUAL_PATH_PROFILE") == "1"
    classification_enabled = (
        os.environ.get("ODYSSEUS_H7_CONTENT_FAILURE_CLASSIFICATION") == "1"
    )
    classification_path_raw = os.environ.get(
        "ODYSSEUS_H7_CONTENT_FAILURE_CLASSIFICATION_RESULT",
    )
    classification_path = (
        Path(classification_path_raw)
        if classification_enabled
        and type(classification_path_raw) is str
        and classification_path_raw
        else None
    )
    classification_not_before_ns = None
    controller_projection = None
    if diagnostic_enabled:
        try:
            from src.mcp_context_causal_observation import controller_projection_path
            controller_projection = controller_projection_path(
                repo_root, request.config.option.xmlpath,
            )
        except BaseException:
            controller_projection = None

    def export_h7_content_locus(stage):
        if not diagnostic_enabled:
            return False
        request.node.user_properties.append((
            "odysseus_h7_content_locus", _context_content_locus(stage),
        ))
        return True

    def export_h7_companion_best_effort():
        if not diagnostic_enabled or controller_projection is None:
            return False
        try:
            from src.mcp_context_causal_observation import (
                read_child_projection,
                write_controller_projection_exclusive,
            )
            return write_controller_projection_exclusive(
                controller_projection, read_child_projection(child),
            )
        except BaseException:
            return False

    def export_h7_actual_profile(evidence, child_exit_class):
        projection, artifact_class, rejection_class = _context_actual_path_profile_read(
            actual_profile_path,
        )
        if child_exit_class not in {"zero", "nonzero", "timeout", "unobserved"}:
            child_exit_class = "unobserved"
        child_ok = False
        child_stage = "missing"
        if (
            type(evidence) is dict and set(evidence) == {"ok", "stage"}
            and type(evidence.get("ok")) is bool
            and type(evidence.get("stage")) is str
        ):
            child_ok = evidence["ok"]
            child_stage = evidence["stage"]
        if artifact_class not in {"missing", "malformed", "invalid", "incomplete", "complete"}:
            artifact_class = "invalid"
        if rejection_class not in {"none", "envelope", "rows", "integrity", "core_counts"}:
            rejection_class = "envelope"
        observation = {
            "schema": "odysseus.h7_actual_content_observation.v1",
            "child_exit_class": child_exit_class,
            "child_ok": child_ok,
            "child_stage": child_stage,
            "profile_artifact_class": artifact_class,
            "profile_rejection_class": rejection_class,
        }
        request.node.user_properties.append((
            "odysseus_h7_actual_path_profile_v1",
            json.dumps(projection, sort_keys=True, separators=(",", ":")),
        ))
        request.node.user_properties.append((
            "odysseus_h7_actual_path_observation_v1",
            json.dumps(observation, sort_keys=True, separators=(",", ":")),
        ))
        return projection

    def export_h7_content_failure_classification(evidence, child_exit_class, child_returncode=None):
        if not classification_enabled:
            return False
        observation = _context_content_failure_observation(
            classification_path, classification_not_before_ns, evidence, child_exit_class,
            child_returncode,
        )
        request.node.user_properties.append((
            "odysseus_h7_content_inner_reject_v2",
            json.dumps(
                observation, sort_keys=True, separators=(",", ":"),
                ensure_ascii=True, allow_nan=False,
            ),
        ))
        return True

    def export_h7_restart_observation(projection, child_exit_class):
        if not classification_enabled:
            return False
        request.node.user_properties.append((
            "odysseus_h7_restart_observation_v1",
            json.dumps(
                _context_restart_observation(projection, child_exit_class),
                sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
            ),
        ))
        return True
    profile, temp = child / "profile", child / "temp"
    profile.mkdir()
    temp.mkdir()
    result_path, phase_path = child / "result.json", child / "phase.json"
    fixture_path = child / "context-fixture.json"
    fixture_result_path = child / "context-fixture-result.json"
    positive_result_path = child / "context-positive-result.json"
    actual_profile_path = child / "actual-content-profile-v1.json"
    restart_path, restart_result_path = child / "restart.json", child / "restart-result.json"
    env = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "WINDIR") if name in os.environ}
    env.update({
        "PYTHONPATH": str(repo_root), "PYTHONUTF8": "1", "HOME": str(profile),
        "USERPROFILE": str(profile), "APPDATA": str(profile / "roaming"),
        "LOCALAPPDATA": str(profile / "local"), "TEMP": str(temp), "TMP": str(temp),
        "ODYSSEUS_STARTUP_OFFLINE": "true", "ODYSSEUS_DATA_DIR": str(data_root),
        "DATABASE_URL": f"sqlite:///{(data_root / 'app.db').as_posix()}",
        "ODYSSEUS_DISABLE_MCP": "1", "ODYSSEUS_DISABLE_USER_MCP": "1",
        "ODYSSEUS_INPROCESS_TASKS": "0", "ODYSSEUS_TRANSCRIPTION_ENABLED": "false",
        "ODYSSEUS_LEGACY_STT_ENABLED": "false", "ODYSSEUS_USI_RUNTIME_ENABLED": "false",
        "ODYSSEUS_USI_RUNTIME_MODE": "disabled",
        "ODYSSEUS_PLANNING_ISSUER_SECRET_B64": base64.b64encode(b"x" * 32).decode("ascii"),
        "ODYSSEUS_PLANNING_ISSUER_KEY_ID": "offline-test-key", "AUTH_ENABLED": "true",
        "LOCALHOST_BYPASS": "false", "SECURE_COOKIES": "false",
        "ODYSSEUS_TEST_RESULT_FILE": str(result_path), "ODYSSEUS_TEST_PHASE_FILE": str(phase_path),
        "ODYSSEUS_CONTEXT_RESTART_FILE": str(restart_path),
        "ODYSSEUS_CONTEXT_RESTART_RESULT_FILE": str(restart_result_path),
        "ODYSSEUS_CONTEXT_FIXTURE_FILE": str(fixture_path),
        "ODYSSEUS_CONTEXT_FIXTURE_RESULT_FILE": str(fixture_result_path),
        "ODYSSEUS_CONTEXT_POSITIVE_RESULT_FILE": str(positive_result_path),
        "ODYSSEUS_TEST_REPO_ROOT": str(repo_root), "ODYSSEUS_TEST_FORGE_SOURCE": str(source),
        "ODYSSEUS_TEST_FORGE_COMMIT": commit, "ODYSSEUS_DATA_ROOT": str(data_root),
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHON_DOTENV_DISABLED": "1",
    })
    fixture_script = child / "actual-authenticated-context-fixture-child.py"
    fixture_script.write_text(_CONTEXT_FIXTURE_CHILD, encoding="utf-8")
    fixture_process = subprocess.run(
        [sys.executable, "-B", str(fixture_script)], cwd=repo_root, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=600, check=False,
    )
    fixture_evidence = _context_fixture_projection(fixture_result_path)
    assert fixture_process.returncode == 0, json.dumps(fixture_evidence, sort_keys=True)
    assert fixture_evidence == {"ok": True, "stage": "complete"}
    positive_env = dict(env)
    if diagnostic_enabled:
        positive_env["ODYSSEUS_H7_MANIFEST_OBSERVATION"] = ""
    if actual_profile_enabled:
        assert not actual_profile_path.exists()
        positive_env.update({
            "ODYSSEUS_H7_ACTUAL_PATH_PROFILE": "1",
            "ODYSSEUS_H7_ACTUAL_PATH_PROFILE_RESULT": str(actual_profile_path),
        })
    if classification_enabled:
        assert actual_profile_enabled is False
        assert classification_path is not None and classification_path.is_absolute()
        assert classification_path.resolve().is_relative_to(
            (repo_root / ".abc-test-tmp").resolve(),
        )
        assert not classification_path.exists()
        positive_env.update({
            "ODYSSEUS_H7_CONTENT_FAILURE_CLASSIFICATION": "1",
            "ODYSSEUS_H7_CONTENT_FAILURE_CLASSIFICATION_RESULT": str(classification_path),
        })
    child_script = child / "actual-authenticated-context-child.py"
    child_script.write_text(_CONTEXT_POSITIVE_CHILD, encoding="utf-8")
    positive_argv = [sys.executable, "-B", str(child_script)]
    if classification_enabled:
        classification_not_before_ns = time.time_ns()
    process = subprocess.Popen(
        positive_argv, cwd=repo_root, env=positive_env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        process.wait(timeout=1800)
    except subprocess.TimeoutExpired:
        process_reaped = _context_pack_harness._kill_tree(process)
        evidence = _context_stage_projection(
            positive_result_path,
            {
                "bootstrap", "import", "lifespan", "login", "flow", "restart_bootstrap",
                "activation_status", "activation_response", "activation_enabled", "activation_reads",
                "activation_token_post", "activation_token_status", "activation_token_json",
                "activation_token_value", "activation_token_scope",
                "prepare_call", "prepare_status", "prepare_status_http_400",
                "prepare_status_http_401", "prepare_status_http_403", "prepare_status_http_408",
                "prepare_status_http_413", "prepare_status_http_503", "prepare_status_http_other",
                "prepare_status_http_504_under45", "prepare_status_http_504_ge45",
                "prepare_status_http_504_time_unobserved",
                "prepare_403_graph_absent", "prepare_403_graph_invalid",
                "prepare_403_singles_all_200",
                "prepare_403_singles_none_200_unresolved_shared_or_all_providers",
                "prepare_403_singles_forge_only_200", "prepare_403_singles_knowledge_only_200",
                "prepare_403_singles_planning_only_200", "prepare_403_singles_forge_knowledge_200",
                "prepare_403_singles_forge_planning_200", "prepare_403_singles_knowledge_planning_200",
                "prepare_403_singles_status_other", "prepare_403_probe_cleanup_failed",
                "prepare_result", "challenge_call",
                "challenge_status", "challenge_result", "ack_call", "ack_status",
                "ack_text", "ack_request_id", "ack_db",
                "flow_activation", "flow_prepare_ack", "flow_manifest", "flow_content",
                "flow_content_setup", "flow_content_page_count_precondition",
                "flow_content_rpc_call", "flow_content_http_status", "flow_content_result_decode",
                "flow_content_http_400", "flow_content_http_401", "flow_content_http_403",
                "flow_content_http_408", "flow_content_http_413", "flow_content_http_500",
                "flow_content_http_503", "flow_content_http_other_int", "flow_content_http_nonint",
                "flow_content_pack_id", "flow_content_read_only", "flow_content_authority_granted",
                "flow_content_send_performed", "flow_content_page_extract", "flow_content_source_index",
                "flow_content_base64_decode", "flow_content_offset", "flow_content_end",
                "flow_content_total", "flow_content_whole_digest", "flow_content_page_digest",
                "flow_content_byteform", "flow_content_accumulate", "flow_content_completion_read",
                "flow_content_eof_cursor", "flow_content_continuation_cursor",
                "flow_content_loop_exhausted", "flow_content_page_count", "flow_content_final_ends",
                "flow_content_reconstruction",
                "flow_manifest_call", "flow_manifest_status", "flow_manifest_http_401",
                "flow_manifest_http_403", "flow_manifest_http_500", "flow_manifest_http_other",
                "flow_manifest_http_400", "flow_manifest_http_408", "flow_manifest_http_413",
                "flow_manifest_http_503", "flow_manifest_http_202",
                "flow_manifest_http_other_int", "flow_manifest_http_nonint",
                "flow_manifest_result", "flow_manifest_read_only",
                "flow_manifest_authority_granted", "flow_manifest_send_performed",
                "flow_manifest_pack", "flow_manifest_items", "flow_manifest_complete",
                "flow_manifest_eof", "flow_manifest_cursor", "flow_manifest_limit",
                "flow_manifest_selection",
                "flow_preack_replay", "flow_cancel", "flow_nonstream", "flow_revoke_prepare",
                "flow_revoke_apply", "flow_revoke_replay", "flow_revoke_cancel",
                "flow_source_drift", "flow_graph_replace", "flow_aba",
                "flow_mixed_credentials", "flow_restart_seed", "flow_stale_challenge_seed",
                "flow_token_revoke", "flow_shutdown", "failed", "complete",
            },
        )
        if diagnostic_enabled:
            export_h7_content_locus(evidence.get("stage"))
            export_h7_companion_best_effort()
            positive_env.pop("ODYSSEUS_H7_MANIFEST_OBSERVATION", None)
        if actual_profile_enabled:
            export_h7_actual_profile(evidence, "timeout")
        if classification_enabled:
            export_h7_content_failure_classification(evidence, "timeout", process.returncode)
        assert process_reaped is True
        raise AssertionError(json.dumps({
            "error": "authenticated_context_child_timeout", "stage": evidence["stage"],
            "process_reaped": process.poll() is not None,
        }, sort_keys=True)) from None
    evidence = _context_stage_projection(
        positive_result_path,
        {
            "bootstrap", "import", "lifespan", "login", "flow", "restart_bootstrap",
            "activation_status", "activation_response", "activation_enabled", "activation_reads",
            "activation_token_post", "activation_token_status", "activation_token_json",
            "activation_token_value", "activation_token_scope",
            "prepare_call", "prepare_status", "prepare_status_http_400",
            "prepare_status_http_401", "prepare_status_http_403", "prepare_status_http_408",
            "prepare_status_http_413", "prepare_status_http_503", "prepare_status_http_other",
            "prepare_status_http_504_under45", "prepare_status_http_504_ge45",
            "prepare_status_http_504_time_unobserved",
            "prepare_403_graph_absent", "prepare_403_graph_invalid",
            "prepare_403_singles_all_200",
            "prepare_403_singles_none_200_unresolved_shared_or_all_providers",
            "prepare_403_singles_forge_only_200", "prepare_403_singles_knowledge_only_200",
            "prepare_403_singles_planning_only_200", "prepare_403_singles_forge_knowledge_200",
            "prepare_403_singles_forge_planning_200", "prepare_403_singles_knowledge_planning_200",
            "prepare_403_singles_status_other", "prepare_403_probe_cleanup_failed",
            "prepare_result", "challenge_call",
            "challenge_status", "challenge_result", "ack_call", "ack_status",
            "ack_text", "ack_request_id", "ack_db",
            "flow_activation", "flow_prepare_ack", "flow_manifest", "flow_content",
            "flow_content_setup", "flow_content_page_count_precondition",
            "flow_content_rpc_call", "flow_content_http_status", "flow_content_result_decode",
            "flow_content_http_400", "flow_content_http_401", "flow_content_http_403",
            "flow_content_http_408", "flow_content_http_413", "flow_content_http_500",
            "flow_content_http_503", "flow_content_http_other_int", "flow_content_http_nonint",
            "flow_content_pack_id", "flow_content_read_only", "flow_content_authority_granted",
            "flow_content_send_performed", "flow_content_page_extract", "flow_content_source_index",
            "flow_content_base64_decode", "flow_content_offset", "flow_content_end",
            "flow_content_total", "flow_content_whole_digest", "flow_content_page_digest",
            "flow_content_byteform", "flow_content_accumulate", "flow_content_completion_read",
            "flow_content_eof_cursor", "flow_content_continuation_cursor",
            "flow_content_loop_exhausted", "flow_content_page_count", "flow_content_final_ends",
            "flow_content_reconstruction",
            "flow_manifest_call", "flow_manifest_status", "flow_manifest_http_401",
            "flow_manifest_http_403", "flow_manifest_http_500", "flow_manifest_http_other",
            "flow_manifest_http_400", "flow_manifest_http_408", "flow_manifest_http_413",
            "flow_manifest_http_503", "flow_manifest_http_202",
            "flow_manifest_http_other_int", "flow_manifest_http_nonint",
            "flow_manifest_result", "flow_manifest_read_only",
            "flow_manifest_authority_granted", "flow_manifest_send_performed",
            "flow_manifest_pack", "flow_manifest_items", "flow_manifest_complete",
            "flow_manifest_eof", "flow_manifest_cursor", "flow_manifest_limit",
            "flow_manifest_selection",
            "flow_preack_replay", "flow_cancel", "flow_nonstream", "flow_revoke_prepare",
            "flow_revoke_apply", "flow_revoke_replay", "flow_revoke_cancel",
            "flow_source_drift", "flow_graph_replace", "flow_aba",
            "flow_mixed_credentials", "flow_restart_seed", "flow_stale_challenge_seed",
            "flow_token_revoke", "flow_shutdown", "failed", "complete",
        },
    )
    if diagnostic_enabled:
        export_h7_content_locus(evidence.get("stage"))
        export_h7_companion_best_effort()
        positive_env.pop("ODYSSEUS_H7_MANIFEST_OBSERVATION", None)
    actual_profile = None
    if actual_profile_enabled:
        child_exit_class = (
            "zero" if type(process.returncode) is int and process.returncode == 0
            else "nonzero" if type(process.returncode) is int
            else "unobserved"
        )
        actual_profile = export_h7_actual_profile(evidence, child_exit_class)
    if classification_enabled:
        child_exit_class = (
            "zero" if type(process.returncode) is int and process.returncode == 0
            else "nonzero" if type(process.returncode) is int
            else "unobserved"
        )
        export_h7_content_failure_classification(evidence, child_exit_class, process.returncode)
    restart_evidence = {"checks": 0, "ok": False, "stage": "missing"}
    restart_child_exit_class = "unobserved"
    try:
        assert process.returncode == 0, json.dumps(evidence, sort_keys=True)
        assert evidence == {"ok": True, "stage": "complete"}
        if actual_profile_enabled:
            assert actual_profile is not None and actual_profile["status"] == "complete", json.dumps(
                actual_profile, sort_keys=True, separators=(",", ":"),
            )
        restart_child_script = child / "actual-authenticated-context-restart-child.py"
        restart_child_script.write_text(_CONTEXT_RESTART_CHILD, encoding="utf-8")
        try:
            restarted = subprocess.run(
                [sys.executable, "-B", str(restart_child_script)], cwd=repo_root, env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=600, check=False,
            )
        except subprocess.TimeoutExpired:
            restart_child_exit_class = "timeout"
            restart_evidence = _context_restart_projection(restart_result_path)
            raise
        restart_child_exit_class = (
            "zero" if type(restarted.returncode) is int and restarted.returncode == 0
            else "nonzero" if type(restarted.returncode) is int
            else "unobserved"
        )
        restart_evidence = _context_restart_projection(restart_result_path)
        assert restarted.returncode == 0, json.dumps(restart_evidence, sort_keys=True)
        assert restart_evidence == {"checks": 10, "ok": True, "stage": "complete"}
    finally:
        if (
            classification_enabled
            and type(process.returncode) is int
            and process.returncode == 0
            and evidence == {"ok": True, "stage": "complete"}
        ):
            export_h7_restart_observation(restart_evidence, restart_child_exit_class)


_CONTEXT_DEFAULT_CHILD = _TRIPWIRE + r'''
import json
import os
from pathlib import Path

result_file = Path(os.environ["ODYSSEUS_TEST_RESULT_FILE"])
stage = "import"
try:
    from fastapi.testclient import TestClient
    import app as application
    stage = "lifespan"
    password = "context-default-app-password-93!"
    with TestClient(application.app) as client:
        stage = "auth"
        assert client.post("/api/auth/setup", json={"username": "fuzzy", "password": password}).status_code == 200
        assert client.post("/api/auth/login", json={"username": "fuzzy", "password": password}).status_code == 200
        cookie = client.cookies.get("odysseus_session")
        assert type(cookie) is str and cookie
        identity = application.auth_manager.strict_session_identity(cookie)
        assert type(identity) is tuple and identity[0] == "fuzzy"
        assert type(identity[1]) is str and identity[1]
        assert application.auth_manager.active_admin_subject_identity(
            subject_id=identity[1],
        ) == identity
        stage = "providers"
        sources = application.app.state.context_sources
        assert sources is None
        assert application.context_sources is None
        assert application.app.state.context_source_status == {
            "configured": False, "runtime_verified": False, "reason": "missing_prerequisites",
        }
        assert application.app.state.mcp_context_app is application.mcp_context_app
        assert application.app.state.mcp_context_app._sources is None
        assert application.app.state.mcp_context_app._started is True
        assert application.app.state.mcp_context_app._graph is None
        assert application.app.state.mcp_context_app._effective_gates == (False, False)
        headers = {"Origin": "http://testserver"}
        stage = "configuration"
        default_config = client.get("/api/plugins/mcp/config", headers=headers)
        assert default_config.status_code == 200
        assert default_config.json().get("allow_context_reads") is False
        assert application.app.state.mcp_context_plugin_enabled is False
        assert application.app.state.allow_context_reads is False
        paths = [getattr(route, "path", None) for route in application.app.routes]
        assert paths.count("/api/context/mcp/challenge") == 1
        assert paths.count("/api/context/mcp/revoke") == 1
        stage = "token"
        minted = client.post("/api/tokens", headers=headers, data={
            "name": "isolated-context-e2e",
            "scopes": "mcp:harbour:context:read",
        })
        token_data = minted.json() if minted.status_code == 200 else {}
        token = token_data.get("token", "")
        assert minted.status_code == 200
        assert token_data.get("scopes") == ["mcp:harbour:context:read"]
        from core.database import ApiToken, SessionLocal
        db = SessionLocal()
        try:
            token_row = db.query(ApiToken).filter(ApiToken.id == token_data.get("id")).one()
            assert token_row.owner == "fuzzy"
            assert token_row.owner_subject_id == identity[1]
            assert token_row.scopes == "mcp:harbour:context:read"
            assert token_row.is_active is True
        finally:
            db.close()
        client.cookies.delete("odysseus_session")
        stage = "default_closed"
        denied = client.post(
            "/api/plugins/mcp",
            headers={"Authorization": "Bearer " + token},
            content=b'{"jsonrpc":"2.0","id":1,"method":"harbour_context_prepare","params":{}}',
        )
        assert denied.status_code == 403 and denied.content == b""
        client.cookies.set("odysseus_session", cookie)
        stage = "one_gate"
        configured = client.post("/api/plugins/mcp/config", headers=headers, json={
            "enabled": True,
            "allow_context_reads": False,
            "allow_private_reads": True,
            "allow_filesystem_reads": True,
            "allow_generic_api": True,
        })
        assert configured.status_code == 200
        client.cookies.delete("odysseus_session")
        denied_other_switches = client.post(
            "/api/plugins/mcp",
            headers={"Authorization": "Bearer " + token},
            content=b'{"jsonrpc":"2.0","id":2,"method":"harbour_context_prepare","params":{}}',
        )
        assert denied_other_switches.status_code == 403 and denied_other_switches.content == b""
        assert application.app.state.mcp_context_plugin_enabled is False
        assert application.app.state.allow_context_reads is False
        client.cookies.set("odysseus_session", cookie)
        stage = "revoke"
        token_id = token_data.get("id", "")
        assert client.delete("/api/tokens/" + token_id, headers=headers).status_code == 200
        client.cookies.delete("odysseus_session")
        revoked = client.post(
            "/api/plugins/mcp",
            headers={"Authorization": "Bearer " + token},
            content=b'{"jsonrpc":"2.0","id":3,"method":"harbour_context_prepare","params":{}}',
        )
        assert revoked.status_code == 401
    stage = "shutdown"
    assert application.app.state.mcp_context_app._started is False
    assert application.app.state.mcp_context_app._graph is None
    result_file.write_text(json.dumps({"ok": True, "stage": "complete"}, sort_keys=True), encoding="utf-8")
except Exception:
    result_file.write_text(json.dumps({"ok": False, "stage": stage}, sort_keys=True), encoding="utf-8")
    raise SystemExit(1)
'''


def test_actual_default_app_context_scope_is_default_closed_and_revocable(tmp_path, request):
    repo_root = Path(__file__).resolve().parents[1]
    profile_root, data_root = tmp_path / "profile", tmp_path / "data"
    for path in (
        profile_root,
        data_root,
        profile_root / "roaming",
        profile_root / "local",
        tmp_path / "temp",
        tmp_path / "cache",
    ):
        path.mkdir(parents=True, exist_ok=True)
    result_file = tmp_path / "context-default-result.json"
    env = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "WINDIR") if name in os.environ}
    env.update({
        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "HOME": str(profile_root),
        "USERPROFILE": str(profile_root), "APPDATA": str(profile_root / "roaming"),
        "LOCALAPPDATA": str(profile_root / "local"), "TEMP": str(tmp_path / "temp"),
        "TMP": str(tmp_path / "temp"), "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "PYTHON_DOTENV_DISABLED": "1", "PYTHONPATH": str(repo_root),
        "ODYSSEUS_STARTUP_OFFLINE": "true", "ODYSSEUS_DATA_DIR": str(data_root),
        "DATABASE_URL": f"sqlite:///{(data_root / 'app.db').as_posix()}",
        "ODYSSEUS_DISABLE_MCP": "1", "ODYSSEUS_DISABLE_USER_MCP": "1",
        "ODYSSEUS_INPROCESS_TASKS": "0",
        "ODYSSEUS_SECURITY_PUBLIC_EGRESS_DISCOVERY_DISABLED": "true",
        "ODYSSEUS_SECURITY_INCIDENT_DELIVERY_ENABLED": "false",
        "ODYSSEUS_TRANSCRIPTION_ENABLED": "false", "ODYSSEUS_LEGACY_STT_ENABLED": "false",
        "ODYSSEUS_USI_RUNTIME_ENABLED": "false", "ODYSSEUS_USI_RUNTIME_MODE": "disabled",
        "ODYSSEUS_PLANNING_ISSUER_SECRET_B64": "eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg=",
        "ODYSSEUS_PLANNING_ISSUER_KEY_ID": "context-default-test-key",
        "AUTH_ENABLED": "true", "LOCALHOST_BYPASS": "false", "SECURE_COOKIES": "false",
        "ODYSSEUS_TEST_RESULT_FILE": str(result_file),
    })
    child = tmp_path / "context-default-app-child.py"
    with child.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(_CONTEXT_DEFAULT_CHILD)
    result = subprocess.run(
        [sys.executable, str(child)], cwd=repo_root, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=600, check=False,
    )
    assert result_file.is_file()
    safe_projection = {"ok": False, "stage": "failed"}
    try:
        if 0 < result_file.stat().st_size <= 256:
            projection = json.loads(result_file.read_text(encoding="utf-8"))
            allowed = {"import", "lifespan", "auth", "providers", "configuration", "token", "default_closed", "one_gate", "revoke", "shutdown", "complete", "failed"}
            if (
                type(projection) is dict and set(projection) == {"ok", "stage"}
                and type(projection.get("ok")) is bool
                and type(projection.get("stage")) is str and projection["stage"] in allowed
            ):
                safe_projection = {"ok": projection["ok"], "stage": projection["stage"]}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        pass
    assert result.returncode == 0, json.dumps(safe_projection, sort_keys=True)
    assert safe_projection == {"ok": True, "stage": "complete"}

    configured_root = tmp_path / "configured"
    storage = tempfile.TemporaryDirectory(prefix="h7-context-", dir=repo_root / ".abc-test-tmp")
    request.addfinalizer(storage.cleanup)
    configured_data = Path(storage.name) / "data"
    configured_source = configured_data / "server_projects" / "actual-forge"
    configured_source.mkdir(parents=True)
    (configured_source / ".no-hooks").mkdir()
    _context_pack_harness._git(configured_source, "init", "-b", "main")
    _context_pack_harness._git(configured_source, "config", "user.email", "actual@example.invalid")
    _context_pack_harness._git(configured_source, "config", "user.name", "Actual Fixture")
    _context_pack_harness._git(configured_source, "config", "core.autocrlf", "false")
    (configured_source / "public.py").write_bytes(b"print('actual')\n")
    _context_pack_harness._git(configured_source, "add", "public.py")
    _context_pack_harness._git(configured_source, "commit", "-m", "actual context fixture")
    configured_commit = _context_pack_harness._git(
        configured_source, "rev-parse", "HEAD", capture=True,
    )
    configured_child = configured_root / "child"
    configured_child.mkdir(parents=True)
    configured_profile = configured_child / "profile"
    configured_temp = configured_child / "temp"
    configured_profile.mkdir()
    configured_temp.mkdir()
    configured_result = configured_child / "result.json"
    configured_phase = configured_child / "phase.json"
    fixture_file = configured_child / "context-fixture.json"
    fixture_result = configured_child / "context-fixture-result.json"
    readiness_result = configured_child / "context-readiness-result.json"
    configured_env = {
        name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "WINDIR") if name in os.environ
    }
    configured_env.update({
        "PYTHONPATH": str(repo_root), "PYTHONUTF8": "1", "HOME": str(configured_profile),
        "USERPROFILE": str(configured_profile), "APPDATA": str(configured_profile / "roaming"),
        "LOCALAPPDATA": str(configured_profile / "local"), "TEMP": str(configured_temp),
        "TMP": str(configured_temp), "ODYSSEUS_STARTUP_OFFLINE": "true",
        "ODYSSEUS_DATA_DIR": str(configured_data),
        "DATABASE_URL": f"sqlite:///{(configured_data / 'app.db').as_posix()}",
        "ODYSSEUS_DISABLE_MCP": "1", "ODYSSEUS_DISABLE_USER_MCP": "1",
        "ODYSSEUS_INPROCESS_TASKS": "0", "ODYSSEUS_TRANSCRIPTION_ENABLED": "false",
        "ODYSSEUS_LEGACY_STT_ENABLED": "false", "ODYSSEUS_USI_RUNTIME_ENABLED": "false",
        "ODYSSEUS_USI_RUNTIME_MODE": "disabled",
        "ODYSSEUS_PLANNING_ISSUER_SECRET_B64": base64.b64encode(b"x" * 32).decode("ascii"),
        "ODYSSEUS_PLANNING_ISSUER_KEY_ID": "offline-test-key", "AUTH_ENABLED": "true",
        "LOCALHOST_BYPASS": "false", "SECURE_COOKIES": "false",
        "ODYSSEUS_TEST_RESULT_FILE": str(configured_result),
        "ODYSSEUS_TEST_PHASE_FILE": str(configured_phase),
        "ODYSSEUS_CONTEXT_FIXTURE_FILE": str(fixture_file),
        "ODYSSEUS_CONTEXT_FIXTURE_RESULT_FILE": str(fixture_result),
        "ODYSSEUS_CONTEXT_POSITIVE_RESULT_FILE": str(readiness_result),
        "ODYSSEUS_CONTEXT_POSITIVE_MODE": "readiness",
        "ODYSSEUS_TEST_REPO_ROOT": str(repo_root),
        "ODYSSEUS_TEST_FORGE_SOURCE": str(configured_source),
        "ODYSSEUS_TEST_FORGE_COMMIT": configured_commit,
        "ODYSSEUS_DATA_ROOT": str(configured_data),
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHON_DOTENV_DISABLED": "1",
    })
    fixture_script = configured_child / "actual-context-readiness-fixture-child.py"
    with fixture_script.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(_CONTEXT_FIXTURE_CHILD)
    fixture_process = subprocess.run(
        [sys.executable, "-B", str(fixture_script)], cwd=repo_root, env=configured_env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=600, check=False,
    )
    fixture_evidence = _context_fixture_projection(fixture_result)
    assert fixture_process.returncode == 0, json.dumps(fixture_evidence, sort_keys=True)
    assert fixture_evidence == {"ok": True, "stage": "complete"}
    readiness_script = configured_child / "actual-context-readiness-child.py"
    with readiness_script.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(_CONTEXT_POSITIVE_CHILD)
    readiness_process = subprocess.run(
        [sys.executable, "-B", str(readiness_script)], cwd=repo_root, env=configured_env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=600, check=False,
    )
    readiness_evidence = _context_stage_projection(
        readiness_result,
        {"bootstrap", "import", "lifespan", "login", "readiness", "shutdown", "failed", "complete"},
    )
    assert readiness_process.returncode == 0, json.dumps(readiness_evidence, sort_keys=True)
    assert readiness_evidence == {"ok": True, "stage": "complete"}
