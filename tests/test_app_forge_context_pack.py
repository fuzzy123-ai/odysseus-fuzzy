"""Offline, actual-app coverage for the strict Forge context producers."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


_CHILD = r'''
import hashlib
import json
import os
from pathlib import Path

_PHASES = frozenset({
    "child_start", "client_entered", "setup_complete", "login_complete",
    "migration_prepare_complete", "migration_accept_complete", "head_read_complete",
    "project_register_complete", "version_retain_complete", "pack_complete",
    "auth_negative_checks_complete", "client_exited", "result_written", "child_exception",
})
_STATUS_KEYS = (
    "setup", "login", "prepared", "accepted", "registered", "retained", "packed", "no_origin", "bearer",
)

def phase(value):
    marker = value if value in _PHASES else "child_exception"
    Path(os.environ["ODYSSEUS_TEST_PHASE_FILE"]).write_text(
        json.dumps({"schema": "odysseus.forge_context_app.phase.v1", "phase": marker}, sort_keys=True),
        encoding="utf-8",
    )

phase("child_start")
from fastapi.testclient import TestClient
import app as application
from src.planning_roadmap_migration import build_h1r03_migration_manifest, canonical_json_bytes

def write(statuses, ok):
    safe_status = {
        name: statuses.get(name) if type(statuses.get(name)) is int and 100 <= statuses.get(name) <= 599 else 599
        for name in _STATUS_KEYS
    }
    Path(os.environ["ODYSSEUS_TEST_RESULT_FILE"]).write_text(
        json.dumps({"schema": "odysseus.forge_context_app.result.v1", "ok": bool(ok), "status": safe_status}, sort_keys=True),
        encoding="utf-8",
    )

statuses = {}
try:
    source = Path(os.environ["ODYSSEUS_TEST_FORGE_SOURCE"])
    root = Path(os.environ["ODYSSEUS_TEST_REPO_ROOT"])
    migration = "/api/planning/durable/migrations/h1r03"
    origin = {"Origin": "http://testserver"}
    with TestClient(application.app) as client:
        phase("client_entered")
        setup = client.post("/api/auth/setup", json={"username": "fuzzy", "password": "context-pack-test-password-83!"})
        statuses["setup"] = setup.status_code
        phase("setup_complete")
        login = client.post("/api/auth/login", json={"username": "fuzzy", "password": "context-pack-test-password-83!"})
        statuses["login"] = login.status_code
        phase("login_complete")
        manifest = build_h1r03_migration_manifest(root)
        census = "sha256:" + hashlib.sha256(canonical_json_bytes(manifest["structural_census"])).hexdigest()
        prepared = client.post(migration + "/prepare", headers=origin, json={
            "expected_batch_id": manifest["batch_id"], "expected_manifest_digest": manifest["manifest_digest"],
            "expected_structural_census_digest": census,
        })
        statuses["prepared"] = prepared.status_code
        phase("migration_prepare_complete")
        accepted = client.post(migration + "/accept", headers=origin, json={
            "batch_id": manifest["batch_id"], "expected_manifest_digest": manifest["manifest_digest"],
            "expected_structural_census_digest": census,
            "prior_receipt_id": prepared.json().get("receipt", {}).get("receipt_id", ""),
        })
        statuses["accepted"] = accepted.status_code
        phase("migration_accept_complete")
        roadmap_id = manifest["source_items"][0]["roadmap_id"]
        durable = f"/api/planning/durable/projects/harbour-one/roadmaps/{roadmap_id}"
        head = client.get(durable + "/head").json()
        phase("head_read_complete")
        registered = client.post("/api/project-versioning/subject-projects", headers=origin, json={
            "confirmed": True, "title": "Actual offline Forge", "path": str(source), "privacy_class": "public",
        })
        statuses["registered"] = registered.status_code
        phase("project_register_complete")
        repo_id = registered.json().get("repo_id", "")
        commit = os.environ["ODYSSEUS_TEST_FORGE_COMMIT"]
        retained = client.post(f"/api/project-versioning/{repo_id}/subject-versions", headers=origin,
                               json={"confirmed": True, "commit_sha": commit})
        statuses["retained"] = retained.status_code
        phase("version_retain_complete")
        retained_value = retained.json() if retained.status_code == 200 else {}
        pack_body = {
            "schema": "odysseus.forge_context_pack.request.v1",
            "version_id": retained_value.get("version_id", ""), "commit_sha": commit,
            "manifest_sha256": retained_value.get("manifest_sha256", ""), "code_paths": ["public.py", "more.py"],
            "planning": {"project_id": "harbour-one", "roadmap_id": roadmap_id,
                         "revision_id": head.get("current_revision_id", ""),
                         "content_sha256": head.get("current_content_sha256", ""),
                         "head_generation": head.get("generation")},
            "page_bytes": 3, "cursor": None,
        }
        packed = client.post(f"/api/project-versioning/{repo_id}/context-pack", headers=origin, json=pack_body)
        value = packed.json() if packed.status_code == 200 else {}
        statuses["packed"] = packed.status_code
        phase("pack_complete")
        no_origin = client.post("/api/project-versioning/subject-projects", json={
            "confirmed": True, "title": "No origin", "path": str(source), "privacy_class": "public",
        })
        statuses["no_origin"] = no_origin.status_code
        bearer = client.post("/api/project-versioning/subject-projects", headers={
            **origin, "Authorization": "Bearer rejected-for-cookie-route"
        }, json={"confirmed": True, "title": "Bearer", "path": str(source), "privacy_class": "public"})
        statuses["bearer"] = bearer.status_code
        phase("auth_negative_checks_complete")
        success = setup.status_code == 200 and login.status_code == 200 and prepared.status_code == 200
        success = success and accepted.status_code == 200 and registered.status_code == 200
        success = success and retained.status_code == 200 and no_origin.status_code == 403
        success = success and bearer.status_code == 401 and packed.status_code == 200
        success = success and value.get("read_only") is True and value.get("send_performed") is False
        success = success and value.get("complete") is False and value.get("planning", {}).get("refs")
        success = success and value.get("code", [{}])[0].get("path") == "public.py"
    phase("client_exited")
    write(statuses, success)
    phase("result_written")
except Exception:
    phase("child_exception")
    write(statuses, False)
'''


_RESULT_SCHEMA = "odysseus.forge_context_app.result.v1"
_PHASE_SCHEMA = "odysseus.forge_context_app.phase.v1"
_RESULT_KEYS = frozenset({"schema", "ok", "status"})
_PHASE_KEYS = frozenset({"schema", "phase"})
_ALLOWED_PHASES = frozenset({
    "child_start", "client_entered", "setup_complete", "login_complete",
    "migration_prepare_complete", "migration_accept_complete", "head_read_complete",
    "project_register_complete", "version_retain_complete", "pack_complete",
    "auth_negative_checks_complete", "client_exited", "result_written", "child_exception",
})
_ALLOWED_STATUS_KEYS = (
    "setup", "login", "prepared", "accepted", "registered", "retained", "packed", "no_origin", "bearer",
)


def _safe_json_object(path: Path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return {}
    return value if type(value) is dict else {}


def _safe_child_projection(result_path: Path, phase_path: Path):
    result = _safe_json_object(result_path)
    phase = _safe_json_object(phase_path)
    phase_valid = (
        frozenset(phase.keys()) == _PHASE_KEYS
        and phase.get("schema") == _PHASE_SCHEMA
        and type(phase.get("phase")) is str
        and phase.get("phase") in _ALLOWED_PHASES
    )
    safe_phase = phase.get("phase") if phase_valid else "child_exception"
    raw_status = result.get("status")
    status_valid = type(raw_status) is dict and frozenset(raw_status.keys()) == frozenset(_ALLOWED_STATUS_KEYS)
    safe_status = {
        name: raw_status.get(name) if status_valid and type(raw_status.get(name)) is int
        and 100 <= raw_status.get(name) <= 599 else 599
        for name in _ALLOWED_STATUS_KEYS
    }
    result_valid = (
        frozenset(result.keys()) == _RESULT_KEYS
        and result.get("schema") == _RESULT_SCHEMA
        and type(result.get("ok")) is bool
        and status_valid
        and all(type(raw_status[name]) is int and 100 <= raw_status[name] <= 599 for name in _ALLOWED_STATUS_KEYS)
    )
    return {
        "ok": bool(result_valid and result.get("ok") is True),
        "status": safe_status,
        "phase": safe_phase,
    }


def _terminate_child_tree(process):
    pid = getattr(process, "pid", None)
    helper_completed = False
    helper_returncode = None
    if os.name == "nt" and type(pid) is int and pid > 0:
        try:
            helper = subprocess.run(
                ("taskkill", "/PID", str(pid), "/T", "/F"),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False, timeout=5,
            )
            helper_completed = True
            helper_returncode = helper.returncode
        except (OSError, subprocess.TimeoutExpired):
            return False
    else:
        return False
    if not helper_completed or type(helper_returncode) is not int or helper_returncode != 0:
        return False
    try:
        process.kill()
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except (OSError, ProcessLookupError):
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return False
    return process.poll() is not None


_PARENT_PHASES = frozenset({
    "body_entered", "allocation_before", "allocation_after", "git_before", "git_after",
    "child_allocation_before", "child_allocation_after", "setup_before", "setup_after",
    "spawn_before", "spawn_after", "wait_before", "wait_after", "projection_before", "projection_after",
})


def _parent_phase(tmp_path, phase):
    if type(phase) is not str or phase not in _PARENT_PHASES:
        raise ValueError("invalid_parent_phase") from None
    (tmp_path / "parent-phase.json").write_text(
        json.dumps({"schema": "odysseus.forge_context_app.parent_phase.v1", "phase": phase}, sort_keys=True),
        encoding="utf-8",
    )


def _fixture_git(source, *arguments, read_commit=False):
    try:
        result = subprocess.run(
            ("git", "-c", "core.hooksPath=" + str(source / ".no-hooks"),
             "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", "-c", "core.fsmonitor=false",
             *arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if read_commit else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise AssertionError("fixture_git_unavailable") from None
    if result.returncode != 0:
        raise AssertionError("fixture_git_failed") from None
    if read_commit:
        raw = result.stdout
        if type(raw) is not bytes or len(raw) > 128 or re.fullmatch(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})\r?\n", raw) is None:
            raise AssertionError("fixture_commit_invalid") from None
        return raw.strip().decode("ascii")


def test_actual_default_app_cookie_sid_admin_origin_registers_retains_and_composes(tmp_path, request):
    _parent_phase(tmp_path, "body_entered")
    repo_root = Path(__file__).resolve().parents[1]
    # The retained Forge store keeps per-ref locks.  A short temp parent is
    # required on Windows so this real application path proves behavior rather
    # than tripping MAX_PATH before the reader is reached.
    _parent_phase(tmp_path, "allocation_before")
    short_data = tempfile.TemporaryDirectory(prefix="fa-", dir=repo_root / ".abc-test-tmp")
    _parent_phase(tmp_path, "allocation_after")
    request.addfinalizer(short_data.cleanup)
    data_root = Path(short_data.name) / "data"
    source = data_root / "server_projects" / "actual-forge"
    source.mkdir(parents=True)
    _parent_phase(tmp_path, "git_before")
    (source / ".no-hooks").mkdir()
    _fixture_git(source, "init", str(source))
    _fixture_git(source, "-C", str(source), "config", "user.email", "fixture@example.invalid")
    _fixture_git(source, "-C", str(source), "config", "user.name", "Fixture")
    (source / "public.py").write_text("print('public')\n", encoding="utf-8")
    (source / "more.py").write_text("print('more')\n", encoding="utf-8")
    _fixture_git(source, "-C", str(source), "add", "public.py", "more.py")
    _fixture_git(source, "-C", str(source), "commit", "-m", "fixture")
    commit = _fixture_git(source, "-C", str(source), "rev-parse", "HEAD", read_commit=True)
    _parent_phase(tmp_path, "git_after")

    _parent_phase(tmp_path, "child_allocation_before")
    child_tmp = tempfile.TemporaryDirectory(prefix="fc-", dir=repo_root / ".abc-test-tmp")
    _parent_phase(tmp_path, "child_allocation_after")
    request.addfinalizer(child_tmp.cleanup)
    child_root = Path(child_tmp.name)
    child_temp = child_root / "temp"
    child_temp.mkdir()
    profile = child_root / "profile"
    result = child_root / "result.json"
    phase = tmp_path / "child-phase.json"
    _parent_phase(tmp_path, "setup_before")
    env = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "WINDIR") if name in os.environ}
    env.update({
        "PYTHONPATH": str(repo_root), "PYTHONUTF8": "1", "HOME": str(profile), "USERPROFILE": str(profile),
        "APPDATA": str(profile / "roaming"), "LOCALAPPDATA": str(profile / "local"), "TEMP": str(child_temp),
        "TMP": str(child_temp), "ODYSSEUS_STARTUP_OFFLINE": "true", "ODYSSEUS_DATA_DIR": str(data_root),
        "DATABASE_URL": f"sqlite:///{(data_root / 'app.db').as_posix()}", "ODYSSEUS_PLUGINS_DIR": str(child_root / "plugins"),
        "ODYSSEUS_DISABLE_MCP": "1", "ODYSSEUS_DISABLE_USER_MCP": "1", "ODYSSEUS_INPROCESS_TASKS": "0",
        "ODYSSEUS_TRANSCRIPTION_ENABLED": "false", "ODYSSEUS_LEGACY_STT_ENABLED": "false",
        "ODYSSEUS_USI_RUNTIME_ENABLED": "false", "ODYSSEUS_USI_RUNTIME_MODE": "disabled",
        "ODYSSEUS_PLANNING_ISSUER_SECRET_B64": "eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg=",
        "ODYSSEUS_PLANNING_ISSUER_KEY_ID": "offline-test-key", "AUTH_ENABLED": "true", "LOCALHOST_BYPASS": "false",
        "SECURE_COOKIES": "false", "ODYSSEUS_TEST_RESULT_FILE": str(result), "ODYSSEUS_TEST_PHASE_FILE": str(phase),
        "ODYSSEUS_TEST_FORGE_SOURCE": str(source), "ODYSSEUS_TEST_FORGE_COMMIT": commit,
        "ODYSSEUS_TEST_REPO_ROOT": str(repo_root),
    })
    _parent_phase(tmp_path, "setup_after")
    _parent_phase(tmp_path, "spawn_before")
    completed = subprocess.Popen([sys.executable, "-c", _CHILD], cwd=repo_root, env=env,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _parent_phase(tmp_path, "spawn_after")
    timed_out = False
    _parent_phase(tmp_path, "wait_before")
    try:
        completed.wait(timeout=180)
    except subprocess.TimeoutExpired:
        timed_out = True
        cleanup_complete = _terminate_child_tree(completed)
    else:
        cleanup_complete = True
    _parent_phase(tmp_path, "wait_after")
    _parent_phase(tmp_path, "projection_before")
    evidence = _safe_child_projection(result, phase)
    _parent_phase(tmp_path, "projection_after")
    safe_receipt = {
        "schema": "odysseus.forge_context_app.safe_phase_receipt.v1",
        "ok": evidence["ok"],
        "status": evidence["status"],
        "phase": evidence["phase"],
        "cleanup_complete": cleanup_complete is True,
        "timed_out": timed_out is True,
    }
    (tmp_path / "safe-phase-receipt.json").write_text(
        json.dumps(safe_receipt, sort_keys=True), encoding="utf-8"
    )
    assert cleanup_complete is True, safe_receipt
    assert timed_out is False, safe_receipt
    assert completed.returncode == 0, safe_receipt
    assert evidence["phase"] == "result_written", safe_receipt
    assert evidence["ok"] is True, safe_receipt
