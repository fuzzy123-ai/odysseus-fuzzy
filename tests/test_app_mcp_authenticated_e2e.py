from __future__ import annotations

import json
import os
from pathlib import Path
import importlib.util
import subprocess
import sys

_harness_path = Path(__file__).with_name("test_app_offline_startup.py")
_harness_spec = importlib.util.spec_from_file_location("offline_mcp_app_harness", _harness_path)
assert _harness_spec is not None and _harness_spec.loader is not None
_harness = importlib.util.module_from_spec(_harness_spec)
_harness_spec.loader.exec_module(_harness)
_TRIPWIRE = _harness._TRIPWIRE


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
    def assert_typed(value):
        rendered = json.dumps(value, sort_keys=True)
        assert set(value) == {"schema", "items", "count", "sources", "complete", "clipped", "authority", "freshness", "cross_source_atomic"}
        assert value["schema"] == "odysseus.harbour_inbox_unified_list.v3"
        assert value["complete"] is True and value["clipped"] is False and value["count"] == 5
        assert [(row.get("source"), row.get("status")) for row in value["sources"]] == [
            ("clarification", "captured_revalidated"), ("planning", "captured_revalidated"),
            ("knowledge", "captured_revalidated"), ("document", "captured_revalidated"),
        ]
        common = {"item_handle", "source", "owner", "signal_type", "status", "required_action", "due_state", "authority", "binding_digest", "receipt_ref", "redaction_flags"}
        extras = {"clarification": {"clarification_id", "expected_version", "ready_for_plan", "updated_at"}, "planning": {"current_revision", "approved_revision"}, "knowledge": {"knowledge_kind", "generation", "revision", "occurred_at"}, "document": {"current_revision"}}
        assert all(set(item) == common | extras[item["source"]] for item in value["items"])
        assert all("due" not in item and "receipt_handle" not in item for item in value["items"])
        assert all(item["authority"]["access"] == "read" and item["owner"] == "self" for item in value["items"])
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
        assert client.post("/api/plugins/mcp/config", headers=headers, json={"enabled": True, "allow_planning_reads": True}).status_code == 200
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
    stage = "restart"
    import src.plugin_system as plugin_system
    plugin_system.MANAGER = None
    application = importlib.reload(application)
    setup_plugin()
    with TestClient(application.app) as client:
        assert client.post("/api/auth/login", json={"username": "fuzzy", "password": password}).status_code == 200
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
        assert_typed(payload(typed_restarted))
        restarted_documents = [item for item in payload(typed_restarted)["items"] if item["source"] == "document"]
        assert frozenset(
            (item["item_handle"], item["binding_digest"], item["current_revision"])
            for item in restarted_documents
        ) == public_document_descriptors
    result_file.write_text(json.dumps({"ok": True, "selection": bool(selection), "stage": "complete"}, sort_keys=True), encoding="utf-8")
except Exception:
    result_file.write_text(json.dumps({
        "ok": False, "selection": False,
        "stage": stage if stage in {"fixture", "app_import", "app_actions", "restart"} else "failed",
    }, sort_keys=True), encoding="utf-8")
    raise SystemExit(1)
'''


def test_actual_default_app_unified_inbox_captures_real_clarification_planning_and_knowledge_after_restart(tmp_path):
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
