"""Offline actual-app coverage for the private context confirmation routes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


_CHILD = r'''
import json
import os
from pathlib import Path

_PHASES = frozenset({
    "child_start", "client_entered", "setup_complete", "login_complete",
    "session_persisted", "nonstream_complete", "stream_complete",
    "cache_readback_complete", "negative_checks_complete", "client_exited",
    "result_written", "child_exception",
})
_STATUS_KEYS = (
    "setup", "login", "nonstream", "stream", "cache", "no_origin", "bearer",
    "forwarded", "incognito", "reuse",
)

def phase(value):
    marker = value if value in _PHASES else "child_exception"
    Path(os.environ["ODYSSEUS_TEST_PHASE_FILE"]).write_text(
        json.dumps({"schema": "odysseus.private_context_app.phase.v1", "phase": marker}, sort_keys=True),
        encoding="utf-8",
    )

phase("child_start")
from fastapi.testclient import TestClient
import app as application
from core.database import ChatMessage, SessionLocal
from src.private_context_confirmation_app import (
    ContextManifest, ContextSelection, ManifestItem, PrivateContextConfirmationApp, SourceSelection,
)
import routes.chat_routes as chat_routes

def write(statuses, ok):
    safe_status = {
        name: statuses.get(name) if type(statuses.get(name)) is int and 100 <= statuses.get(name) <= 599 else 599
        for name in _STATUS_KEYS
    }
    Path(os.environ["ODYSSEUS_TEST_RESULT_FILE"]).write_text(
        json.dumps({"schema": "odysseus.private_context_app.result.v1", "ok": bool(ok), "status": safe_status}, sort_keys=True),
        encoding="utf-8",
    )

class SyntheticProducer:
    def capture(self, binding, selection):
        item = ManifestItem(
            source="planning", selection_ref="selection-1", revision_ref="revision-1",
            boundary_sha256="a" * 64, record_sha256="b" * 64, payload_sha256="c" * 64,
            classification="private", provenance_ref="offline-test",
        )
        return ContextManifest((item,))

statuses = {}
try:
    origin = {"Origin": "http://testserver"}
    with TestClient(application.app) as client:
        phase("client_entered")
        setup = client.post("/api/auth/setup", json={"username": "fuzzy", "password": "context-confirmation-test-password-83!"})
        statuses["setup"] = setup.status_code
        phase("setup_complete")
        login = client.post("/api/auth/login", json={"username": "fuzzy", "password": "context-confirmation-test-password-83!"})
        statuses["login"] = login.status_code
        phase("login_complete")

        service = PrivateContextConfirmationApp(SessionLocal, application.auth_manager, SyntheticProducer())
        application.app.state.private_context_confirmation = service
        application.app.state.private_context_confirmation_auth_manager = application.auth_manager
        session_id = "private-confirmation-real-app-session"
        application.session_manager.create_session(
            session_id=session_id, name="Private confirmation", endpoint_url="http://offline.invalid",
            model="offline-model", owner="fuzzy",
        )
        token = client.cookies.get("odysseus_session")
        selection = ContextSelection((SourceSelection("planning", "selection-1", "revision-1"),))
        phase("session_persisted")

        challenge = service.issue_for_selection(token, session_id, selection)
        command = "/context-disclose " + challenge.token
        no_origin = client.post("/api/chat", json={"message": command, "session": session_id})
        statuses["no_origin"] = no_origin.status_code
        bearer = client.post("/api/chat", headers={**origin, "Authorization": "Bearer rejected"}, json={"message": command, "session": session_id})
        statuses["bearer"] = bearer.status_code
        forwarded = client.post("/api/chat", headers={**origin, "Forwarded": "for=127.0.0.1"}, json={"message": command, "session": session_id})
        statuses["forwarded"] = forwarded.status_code
        incognito = client.post("/api/chat", headers=origin, json={"message": command, "session": session_id, "incognito": True})
        statuses["incognito"] = incognito.status_code

        def fail_on_call(*args, **kwargs):
            raise AssertionError("provider_called")
        async def fail_on_call_async(*args, **kwargs):
            raise AssertionError("provider_called")
        chat_routes.build_chat_context = fail_on_call_async
        chat_routes.llm_call_async = fail_on_call_async
        chat_routes.stream_llm = fail_on_call
        chat_routes.stream_llm_with_fallback = fail_on_call
        chat_routes.stream_agent_loop = fail_on_call
        application.chat_handler.handle_memory_command = fail_on_call_async

        nonstream = client.post("/api/chat", headers=origin, json={"message": command, "session": session_id})
        statuses["nonstream"] = nonstream.status_code
        value = nonstream.json() if nonstream.headers.get("content-type", "").startswith("application/json") else {}
        phase("nonstream_complete")

        db = SessionLocal()
        try:
            rows = db.query(ChatMessage).filter(ChatMessage.session_id == session_id).order_by(ChatMessage.id).all()
            first_pair = (
                len(rows) == 2
                and sum(row.role == "user" for row in rows) == 1
                and sum(row.role == "assistant" for row in rows) == 1
            )
        finally:
            db.close()
        cached = application.session_manager.get_session(session_id)
        first_cache_ok = first_pair and len(cached.history) == 2 and cached.message_count == 2
        statuses["cache"] = 200 if first_cache_ok else 599
        phase("cache_readback_complete")

        challenge_stream = service.issue_for_selection(token, session_id, selection)
        stream_command = "/context-disclose " + challenge_stream.token
        stream = client.post("/api/chat_stream", headers=origin, data={"message": stream_command, "session": session_id})
        statuses["stream"] = stream.status_code
        stream_body = stream.content
        stream_events = [line[6:] for line in stream_body.splitlines() if line.startswith(b"data: ")]
        stream_payload = {}
        if stream_events and stream_events[-1] == b"[DONE]":
            try:
                stream_payload = json.loads(stream_events[0].decode("utf-8"))
            except (UnicodeError, ValueError, json.JSONDecodeError):
                stream_payload = {}
        stream_ok = (
            stream.status_code == 200
            and b"Private context disclosure confirmed." in stream_body
            and stream_events[-1:] == [b"[DONE]"]
            and frozenset(stream_payload) == frozenset({
                "response", "disclosure_message_id", "acknowledgement_message_id", "audience", "mode",
            })
            and stream_payload.get("response") == "Private context disclosure confirmed."
            and stream_payload.get("audience") == "authenticated_context_download"
            and stream_payload.get("mode") == "read_only"
            and type(stream_payload.get("disclosure_message_id")) is int
            and type(stream_payload.get("acknowledgement_message_id")) is int
            and stream_payload.get("disclosure_message_id") != stream_payload.get("acknowledgement_message_id")
            and challenge_stream.token.encode("ascii") not in stream_body
        )
        phase("stream_complete")

        db = SessionLocal()
        try:
            fresh_rows = db.query(ChatMessage).filter(ChatMessage.session_id == session_id).order_by(ChatMessage.id).all()
            four_rows = (
                len(fresh_rows) == 4
                and sum(row.role == "user" for row in fresh_rows) == 2
                and sum(row.role == "assistant" for row in fresh_rows) == 2
            )
        finally:
            db.close()
        fresh_ids = {int(row.id) for row in fresh_rows if type(row.id) is str and row.id.isdecimal()}
        stream_ids = {
            stream_payload.get("disclosure_message_id"),
            stream_payload.get("acknowledgement_message_id"),
        }
        stream_ok = stream_ok and len(fresh_ids) == 4 and stream_ids <= fresh_ids
        fresh_cached = application.session_manager.get_session(session_id)
        four_cached = four_rows and len(fresh_cached.history) == 4 and fresh_cached.message_count == 4
        cache_ok = first_cache_ok and four_cached
        statuses["cache"] = 200 if cache_ok else 599

        challenge_reuse = service.issue_for_selection(token, session_id, selection)
        reuse_command = "/context-disclose " + challenge_reuse.token
        first_reuse = client.post("/api/chat", headers=origin, json={"message": reuse_command, "session": session_id})
        second_reuse = client.post("/api/chat", headers=origin, json={"message": reuse_command, "session": session_id})
        statuses["reuse"] = second_reuse.status_code
        phase("negative_checks_complete")

        success = (
            setup.status_code == 200 and login.status_code == 200
            and statuses["no_origin"] == 403 and statuses["bearer"] == 401
            and statuses["forwarded"] == 401 and statuses["incognito"] == 403
            and statuses["nonstream"] == 200 and value.get("audience") == "authenticated_context_download"
            and type(value.get("disclosure_message_id")) is int
            and type(value.get("acknowledgement_message_id")) is int
            and value.get("disclosure_message_id") != value.get("acknowledgement_message_id")
            and stream_ok and first_reuse.status_code == 200 and statuses["reuse"] in {400, 409}
            and cache_ok
        )
    phase("client_exited")
    write(statuses, success)
    phase("result_written")
except Exception:
    phase("child_exception")
    write(statuses, False)
'''


_RESULT_SCHEMA = "odysseus.private_context_app.result.v1"
_PHASE_SCHEMA = "odysseus.private_context_app.phase.v1"
_RESULT_KEYS = frozenset({"schema", "ok", "status"})
_PHASE_KEYS = frozenset({"schema", "phase"})
_ALLOWED_PHASES = frozenset({
    "parent_setup", "child_start", "client_entered", "setup_complete", "login_complete", "session_persisted",
    "nonstream_complete", "stream_complete", "cache_readback_complete", "negative_checks_complete",
    "client_exited", "result_written", "child_exception",
})
_ALLOWED_STATUS_KEYS = (
    "setup", "login", "nonstream", "stream", "cache", "no_origin", "bearer", "forwarded", "incognito", "reuse",
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
        frozenset(phase.keys()) == _PHASE_KEYS and phase.get("schema") == _PHASE_SCHEMA
        and type(phase.get("phase")) is str and phase.get("phase") in _ALLOWED_PHASES
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
        frozenset(result.keys()) == _RESULT_KEYS and result.get("schema") == _RESULT_SCHEMA
        and type(result.get("ok")) is bool and status_valid
        and all(type(raw_status[name]) is int and 100 <= raw_status[name] <= 599 for name in _ALLOWED_STATUS_KEYS)
    )
    return {"ok": bool(result_valid and result.get("ok") is True), "status": safe_status, "phase": safe_phase}


def _terminate_child_tree(process):
    pid = getattr(process, "pid", None)
    if os.name != "nt" or type(pid) is not int or pid <= 0:
        return False
    helper_ok = False
    try:
        helper = subprocess.run(
            ("taskkill", "/PID", str(pid), "/T", "/F"), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=5,
        )
        helper_ok = helper.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        process.kill()
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        return False
    return helper_ok and process.poll() is not None


_PARENT_PHASES = frozenset({
    "body_entered", "allocation_before", "allocation_after", "setup_before", "setup_after",
    "spawn_before", "spawn_after", "wait_before", "wait_after", "reap_before", "reap_after",
    "projection_before", "projection_after", "receipt_before", "receipt_after",
})


def _write_parent_phase(tmp_path: Path, value: str):
    if type(value) is not str or value not in _PARENT_PHASES:
        raise ValueError("invalid_parent_phase")
    (tmp_path / "parent-phase.json").write_text(
        json.dumps({"schema": "odysseus.private_context_app.parent_phase.v1", "phase": value}, sort_keys=True),
        encoding="utf-8",
    )


def test_actual_default_app_private_confirmation_routes(tmp_path, request):
    _write_parent_phase(tmp_path, "body_entered")
    repo_root = Path(__file__).resolve().parents[1]
    _write_parent_phase(tmp_path, "allocation_before")
    child_tmp = tempfile.TemporaryDirectory(prefix="ac-", dir=repo_root / ".abc-test-tmp")
    _write_parent_phase(tmp_path, "allocation_after")
    request.addfinalizer(child_tmp.cleanup)
    _write_parent_phase(tmp_path, "setup_before")
    child_root = Path(child_tmp.name)
    phase = tmp_path / "child-phase.json"
    phase.write_text(
        json.dumps({"schema": "odysseus.private_context_app.phase.v1", "phase": "parent_setup"}, sort_keys=True),
        encoding="utf-8",
    )
    child_temp = child_root / "temp"
    child_temp.mkdir()
    profile = child_root / "profile"
    data_root = child_root / "data"
    result = child_root / "result.json"
    env = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "WINDIR") if name in os.environ}
    env.update({
        "PYTHONPATH": str(repo_root), "PYTHONUTF8": "1", "HOME": str(profile), "USERPROFILE": str(profile),
        "APPDATA": str(profile / "roaming"), "LOCALAPPDATA": str(profile / "local"), "TEMP": str(child_temp), "TMP": str(child_temp),
        "ODYSSEUS_STARTUP_OFFLINE": "true", "ODYSSEUS_DATA_DIR": str(data_root),
        "DATABASE_URL": f"sqlite:///{(data_root / 'app.db').as_posix()}", "ODYSSEUS_PLUGINS_DIR": str(child_root / "plugins"),
        "ODYSSEUS_DISABLE_MCP": "1", "ODYSSEUS_DISABLE_USER_MCP": "1", "ODYSSEUS_INPROCESS_TASKS": "0",
        "ODYSSEUS_TRANSCRIPTION_ENABLED": "false", "ODYSSEUS_LEGACY_STT_ENABLED": "false",
        "ODYSSEUS_USI_RUNTIME_ENABLED": "false", "ODYSSEUS_USI_RUNTIME_MODE": "disabled",
        "AUTH_ENABLED": "true", "LOCALHOST_BYPASS": "false", "SECURE_COOKIES": "false",
        "ODYSSEUS_TEST_RESULT_FILE": str(result), "ODYSSEUS_TEST_PHASE_FILE": str(phase),
    })
    _write_parent_phase(tmp_path, "setup_after")
    _write_parent_phase(tmp_path, "spawn_before")
    completed = subprocess.Popen(
        [sys.executable, "-c", _CHILD], cwd=repo_root, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    _write_parent_phase(tmp_path, "spawn_after")
    timed_out = False
    _write_parent_phase(tmp_path, "wait_before")
    try:
        completed.wait(timeout=120)
    except subprocess.TimeoutExpired:
        timed_out = True
        _write_parent_phase(tmp_path, "reap_before")
        cleanup_complete = _terminate_child_tree(completed)
        _write_parent_phase(tmp_path, "reap_after")
    else:
        cleanup_complete = True
    _write_parent_phase(tmp_path, "wait_after")
    _write_parent_phase(tmp_path, "projection_before")
    evidence = _safe_child_projection(result, phase)
    _write_parent_phase(tmp_path, "projection_after")
    safe_receipt = {
        "schema": "odysseus.private_context_app.safe_phase_receipt.v1", "ok": evidence["ok"],
        "status": evidence["status"], "phase": evidence["phase"],
        "cleanup_complete": cleanup_complete is True, "timed_out": timed_out is True,
    }
    _write_parent_phase(tmp_path, "receipt_before")
    (tmp_path / "safe-phase-receipt.json").write_text(json.dumps(safe_receipt, sort_keys=True), encoding="utf-8")
    _write_parent_phase(tmp_path, "receipt_after")
    assert cleanup_complete is True, safe_receipt
    assert timed_out is False, safe_receipt
    assert completed.returncode == 0, safe_receipt
    assert evidence["phase"] == "result_written", safe_receipt
    assert evidence["ok"] is True, safe_receipt
