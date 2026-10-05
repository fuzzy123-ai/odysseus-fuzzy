from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


_TRIPWIRE = r'''
import inspect
import socket
import subprocess
import threading

attempted = {
    "network": False,
    "process": False,
    "process_origin": "",
    "platform_probe": False,
    "local_git_runs": 0,
}
_socket_connect = socket.socket.connect
_socket_connect_ex = socket.socket.connect_ex
_socketpair = socket.socketpair
_socketpair_scope = threading.local()
_socket_sendmsg = getattr(socket.socket, "sendmsg", None)

def guarded_connect(sock, address):
    if getattr(_socketpair_scope, "active", False):
        return _socket_connect(sock, address)
    attempted["network"] = True
    raise RuntimeError("offline_test_network_blocked")

def guarded_connect_ex(sock, address):
    if getattr(_socketpair_scope, "active", False):
        return _socket_connect_ex(sock, address)
    attempted["network"] = True
    raise RuntimeError("offline_test_network_blocked")

def guarded_socketpair(*args, **kwargs):
    previous = getattr(_socketpair_scope, "active", False)
    _socketpair_scope.active = True
    try:
        return _socketpair(*args, **kwargs)
    finally:
        _socketpair_scope.active = previous

def guarded_create_connection(*_args, **_kwargs):
    attempted["network"] = True
    raise RuntimeError("offline_test_network_blocked")

def guarded_getaddrinfo(*_args, **_kwargs):
    attempted["network"] = True
    raise RuntimeError("offline_test_network_blocked")

def guarded_sendto(self, *_args, **_kwargs):
    attempted["network"] = True
    raise RuntimeError("offline_test_network_blocked")

def guarded_sendmsg(self, *_args, **_kwargs):
    attempted["network"] = True
    raise RuntimeError("offline_test_network_blocked")

_popen_init = subprocess.Popen.__init__
def guarded_process(self, *_args, **_kwargs):
    frame = inspect.currentframe().f_back
    platform_probe = False
    recent_changes_git = False
    origin = ""
    for _ in range(16):
        if frame is None:
            break
        module = frame.f_globals.get("__name__", "")
        function = frame.f_code.co_name
        if module == "platform" and function == "_syscmd_ver":
            platform_probe = True
            break
        if module == "src.recent_changes" and function == "_run_git":
            recent_changes_git = True
            break
        if not origin and module not in {"subprocess", "asyncio.subprocess"}:
            origin = (module + "." + function)[:120]
        frame = frame.f_back
    command = _args[0] if _args else _kwargs.get("args")
    if platform_probe and command in {"ver", "command /c ver", "cmd /c ver"}:
        attempted["platform_probe"] = True
        return _popen_init(self, *_args, **_kwargs)
    expected_git_commands = {
        ("git", "diff", "--name-status"),
        ("git", "diff", "--numstat"),
        ("git", "diff", "--stat"),
        ("git", "ls-files", "--others", "--exclude-standard"),
    }
    exact_git_log = (
        isinstance(command, (list, tuple))
        and len(command) == 5
        and command[0] == "git"
        and command[1] == "log"
        and isinstance(command[2], str)
        and command[2].startswith("--since=")
        and tuple(command[3:]) == ("--date=iso", "--pretty=format:%h%x09%ad%x09%an%x09%s")
    )
    exact_git_command = (
        isinstance(command, (list, tuple))
        and (tuple(command) in expected_git_commands or exact_git_log)
        and _kwargs.get("shell") is False
        and _kwargs.get("cwd") == os.environ.get("ODYSSEUS_TEST_REPO_ROOT")
    )
    if recent_changes_git and exact_git_command:
        attempted["local_git_runs"] += 1
        return _popen_init(self, *_args, **_kwargs)
    attempted["process"] = True
    attempted["process_origin"] = origin
    raise RuntimeError("offline_test_process_blocked")

socket.socket.connect = guarded_connect
socket.socket.connect_ex = guarded_connect_ex
socket.socketpair = guarded_socketpair
socket.create_connection = guarded_create_connection
socket.getaddrinfo = guarded_getaddrinfo
socket.socket.sendto = guarded_sendto
if _socket_sendmsg is not None:
    socket.socket.sendmsg = guarded_sendmsg
subprocess.Popen.__init__ = guarded_process
'''


_CHILD = _TRIPWIRE + r'''
import json
import os
from pathlib import Path

def write_result(value):
    Path(os.environ["ODYSSEUS_TEST_RESULT_FILE"]).write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

stage = "import"
try:
    from fastapi.testclient import TestClient
    import app as application

    stage = "startup_and_routes"
    checks = {
        "offline_mode": application.STARTUP_OFFLINE is True,
        "rag_unavailable": application.rag_manager is None,
        "memory_vector_unavailable": application.memory_vector is None,
    }
    with TestClient(application.app) as client:
        recent_changes_task = application.app.state._startup_recent_changes_task
        recent_changes_stop_event = application.app.state._startup_recent_changes_stop_event
        startup_tasks = tuple(application.app.state._startup_tasks)
        checks["recent_changes_task_created"] = recent_changes_task is not None
        checks["recent_changes_stop_event_created"] = recent_changes_stop_event is not None
        checks["generic_startup_tasks_created"] = bool(startup_tasks)
        from src.agent_tools import knowledge_tools

        checks["knowledge_owner_resolver_bound_to_auth_manager"] = (
            getattr(knowledge_tools._owner_resolver, "__self__", None)
            is application.auth_manager
        )
        unauthenticated_migration = client.get("/api/planning/durable/migrations/h1r03/status")
        setup = client.post(
            "/api/auth/setup",
            json={"username": "fuzzy", "password": "offline-startup-test-password-83!"},
        )
        login = client.post(
            "/api/auth/login",
            json={"username": "fuzzy", "password": "offline-startup-test-password-83!"},
        )
        auth_status = client.get("/api/auth/status")
        planning = client.get("/api/planning/projects")
        migration_status = client.get("/api/planning/durable/migrations/h1r03/status")
        forge = client.get("/api/project-versioning/offline-smoke/versions")
        knowledge = client.get("/api/diagnostics/unified-source-index/runtime-status")

        auth_projection = auth_status.json() if auth_status.status_code == 200 else {}
        login_projection = login.json() if login.status_code == 200 else {}
        checks.update({
            "protected_route_denies_anonymous": unauthenticated_migration.status_code in {401, 403},
            "auth_setup": setup.status_code == 200,
            "auth_login": login.status_code == 200 and login_projection.get("username") == "fuzzy",
            "auth_status": (
                auth_status.status_code == 200
                and auth_projection.get("authenticated") is True
                and auth_projection.get("username") == "fuzzy"
            ),
            "planning_projects": planning.status_code == 200 and isinstance(planning.json(), dict),
            "h1r03_status": migration_status.status_code == 200,
            "forge_versions": forge.status_code == 200 and isinstance(forge.json(), dict),
            "knowledge_status": knowledge.status_code == 200,
        })
        if knowledge.status_code == 200:
            projected = knowledge.json().get("knowledge", {})
            checks["knowledge_reports_disabled"] = (
                projected.get("state") == "disabled"
                and projected.get("mode") == "disabled"
            )
        else:
            checks["knowledge_reports_disabled"] = False

    from src.agent_tools import knowledge_tools
    checks["knowledge_owner_resolver_cleaned"] = knowledge_tools._owner_resolver is None
    checks["recent_changes_stop_event_signaled"] = recent_changes_stop_event.is_set()
    checks["recent_changes_task_done"] = recent_changes_task.done()
    checks["recent_changes_task_not_cancelled"] = not recent_changes_task.cancelled()
    checks["recent_changes_task_cleared"] = application.app.state._startup_recent_changes_task is None
    checks["recent_changes_stop_event_cleared"] = application.app.state._startup_recent_changes_stop_event is None
    checks["generic_startup_tasks_done"] = all(task.done() for task in startup_tasks)
    checks["generic_startup_task_registry_cleared"] = application.app.state._startup_tasks == []

    async def stubborn_task(release):
        while not release.is_set():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass

    async def exercise_bounded_startup_cleanup():
        release = asyncio.Event()
        task = asyncio.create_task(stubborn_task(release))
        application.app.state._startup_tasks = [task]
        await asyncio.sleep(0)
        await application._shutdown_registered_startup_tasks(timeout_seconds=0.01)
        retained = application.app.state._startup_tasks == [task] and not task.done()
        release.set()
        await asyncio.wait((task,), timeout=1)
        await application._shutdown_registered_startup_tasks(timeout_seconds=0.01)
        return retained, task.done(), application.app.state._startup_tasks == []

    async def exercise_recent_snapshot_and_upload_ownership():
        snapshot_release = asyncio.Event()
        snapshot_stop = threading.Event()
        snapshot_task = asyncio.create_task(snapshot_release.wait())
        application.app.state._startup_recent_changes_task = snapshot_task
        application.app.state._startup_recent_changes_stop_event = snapshot_stop
        application.app.state._startup_tasks = []
        await asyncio.sleep(0)
        completed = await application._await_recent_changes_snapshot_shutdown(
            snapshot_stop, snapshot_task, timeout_seconds=0.01,
        )
        retained_snapshot = (
            not completed and snapshot_stop.is_set()
            and application.app.state._startup_recent_changes_task is snapshot_task
            and application.app.state._startup_recent_changes_stop_event is snapshot_stop
            and application.app.state._startup_tasks == [snapshot_task]
            and not snapshot_task.cancelled()
        )
        replacement_stop = threading.Event()
        replacement_task = asyncio.create_task(asyncio.sleep(0))
        await asyncio.sleep(0)
        application.app.state._startup_recent_changes_task = replacement_task
        application.app.state._startup_recent_changes_stop_event = replacement_stop
        snapshot_release.set()
        await asyncio.wait((snapshot_task,), timeout=1)
        await asyncio.sleep(0)
        snapshot_identity_guarded = (
            snapshot_task.done() and not snapshot_task.cancelled()
            and application.app.state._startup_recent_changes_task is replacement_task
            and application.app.state._startup_recent_changes_stop_event is replacement_stop
            and snapshot_task not in application.app.state._startup_tasks
        )
        application.app.state._startup_recent_changes_task = None
        application.app.state._startup_recent_changes_stop_event = None

        upload_release = asyncio.Event()
        upload_task = asyncio.create_task(upload_release.wait())
        application.upload_cleanup_task = upload_task
        application.app.state._startup_tasks = [upload_task]
        await asyncio.sleep(0)
        await application._shutdown_registered_startup_tasks(
            excluded=(upload_task,), timeout_seconds=0.01,
        )
        upload_excluded = application.app.state._startup_tasks == [upload_task] and not upload_task.done()
        upload_stopped = await application._stop_dedicated_startup_task(upload_task, label="test-upload")
        application.upload_cleanup_task = None
        return retained_snapshot, snapshot_identity_guarded, upload_excluded, upload_stopped

    import asyncio
    retained, released, registry_cleared = asyncio.run(exercise_bounded_startup_cleanup())
    snapshot_retained, snapshot_identity_guarded, upload_excluded, upload_stopped = asyncio.run(
        exercise_recent_snapshot_and_upload_ownership(),
    )
    checks["stubborn_startup_task_retained"] = retained
    checks["stubborn_startup_task_released"] = released
    checks["stubborn_startup_task_registry_cleared"] = registry_cleared
    checks["recent_snapshot_timeout_retained"] = snapshot_retained
    checks["recent_snapshot_completion_identity_guarded"] = snapshot_identity_guarded
    checks["upload_task_excluded_from_generic_shutdown"] = upload_excluded
    checks["upload_task_stopped_by_dedicated_shutdown"] = upload_stopped

    checks["tool_index_unavailable"] = __import__("src.tool_index", fromlist=["get_tool_index"]).get_tool_index() is None
    checks["network_attempted"] = attempted["network"]
    checks["process_attempted"] = attempted["process"]
    checks["process_origin"] = attempted["process_origin"]
    checks["platform_probe"] = attempted["platform_probe"]
    checks["local_git_runs"] = attempted["local_git_runs"]
    checks["platform_probe_valid"] = (
        type(attempted["platform_probe"]) is bool
        and (os.name == "nt" or attempted["platform_probe"] is False)
    )
    checks["local_git_runs_valid"] = (
        type(attempted["local_git_runs"]) is int
        and 0 <= attempted["local_git_runs"] <= 5
    )
    checks["ok"] = (
        all(value is True for key, value in checks.items() if key not in {"network_attempted", "process_attempted", "process_origin", "platform_probe", "local_git_runs"})
        and not attempted["network"]
        and not attempted["process"]
    )
    write_result(checks)
    raise SystemExit(0 if checks["ok"] else 2)
except Exception as exc:
    write_result({
        "ok": False,
        "stage": stage,
        "error_class": type(exc).__name__,
        "network_attempted": attempted["network"],
        "process_attempted": attempted["process"],
        "process_origin": attempted["process_origin"],
        "platform_probe": attempted["platform_probe"],
        "local_git_runs": attempted["local_git_runs"],
    })
    raise SystemExit(1)
'''


def test_actual_app_starts_offline_and_reads_composed_routes(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    data_root = tmp_path / "data"
    plugin_root = tmp_path / "plugins"
    profile_root = tmp_path / "profile"
    appdata_root = profile_root / "roaming"
    local_appdata_root = profile_root / "local"
    temp_root = tmp_path / "temp"
    cache_root = tmp_path / "cache"
    data_root.mkdir()
    plugin_root.mkdir()
    for path in (profile_root, appdata_root, local_appdata_root, temp_root, cache_root):
        path.mkdir(parents=True, exist_ok=True)
    db_path = (data_root / "app.db").as_posix()

    # Construct a deliberately small environment. No caller provider values,
    # dotenv file, MCP configuration, or host service settings enter the child.
    env = {
        name: os.environ[name]
        for name in (
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
        )
        if name in os.environ
    }
    env.update({
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "HOME": str(profile_root),
        "USERPROFILE": str(profile_root),
        "APPDATA": str(appdata_root),
        "LOCALAPPDATA": str(local_appdata_root),
        "TEMP": str(temp_root),
        "TMP": str(temp_root),
        "XDG_CACHE_HOME": str(cache_root),
        "HF_HOME": str(cache_root / "huggingface"),
        "TRANSFORMERS_CACHE": str(cache_root / "transformers"),
        "PYTHON_DOTENV_DISABLED": "1",
        "PYTHONPATH": str(repo_root),
        "ODYSSEUS_STARTUP_OFFLINE": "true",
        "ODYSSEUS_DATA_DIR": str(data_root),
        "DATABASE_URL": f"sqlite:///{db_path}",
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
    })

    result_path = tmp_path / "child-result.json"
    env["ODYSSEUS_TEST_RESULT_FILE"] = str(result_path)
    env["ODYSSEUS_TEST_REPO_ROOT"] = str(repo_root)
    result = subprocess.run(
        [sys.executable, "-c", _CHILD],
        cwd=repo_root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=180,
        check=False,
    )

    returncode = result.returncode
    assert result_path.is_file(), "offline full-app smoke child did not return a bounded projection"
    projection = json.loads(result_path.read_text(encoding="utf-8"))
    assert returncode == 0, json.dumps(projection, sort_keys=True)
    assert projection == {
        "auth_login": True,
        "auth_setup": True,
        "auth_status": True,
        "forge_versions": True,
        "generic_startup_task_registry_cleared": True,
        "generic_startup_tasks_created": True,
        "generic_startup_tasks_done": True,
        "h1r03_status": True,
        "knowledge_owner_resolver_bound_to_auth_manager": True,
        "knowledge_owner_resolver_cleaned": True,
        "knowledge_reports_disabled": True,
        "knowledge_status": True,
        "memory_vector_unavailable": True,
        "local_git_runs": projection["local_git_runs"],
        "local_git_runs_valid": True,
        "network_attempted": False,
        "offline_mode": True,
        "ok": True,
        "planning_projects": True,
        "platform_probe": projection["platform_probe"] if os.name == "nt" else False,
        "platform_probe_valid": True,
        "process_attempted": False,
        "process_origin": "",
        "protected_route_denies_anonymous": True,
        "rag_unavailable": True,
        "recent_changes_stop_event_cleared": True,
        "recent_changes_stop_event_created": True,
        "recent_changes_stop_event_signaled": True,
        "recent_changes_task_cleared": True,
        "recent_changes_task_created": True,
        "recent_changes_task_done": True,
        "recent_changes_task_not_cancelled": True,
        "recent_snapshot_completion_identity_guarded": True,
        "recent_snapshot_timeout_retained": True,
        "stubborn_startup_task_registry_cleared": True,
        "stubborn_startup_task_released": True,
        "stubborn_startup_task_retained": True,
        "tool_index_unavailable": True,
        "upload_task_excluded_from_generic_shutdown": True,
        "upload_task_stopped_by_dedicated_shutdown": True,
    }


def test_tripwire_blocks_any_connection_outside_socketpair_scope(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    result_path = tmp_path / "tripwire-result.json"
    child = _TRIPWIRE + r'''
import json
import os
from pathlib import Path
import socket
import subprocess
import types

blocked = False
sock = socket.socket()
try:
    sock.connect(("127.0.0.1", 49152))
except RuntimeError:
    blocked = True
finally:
    sock.close()
blocked_process = False
app_module = types.ModuleType("app")
app_module.__dict__["subprocess"] = subprocess
try:
    exec("subprocess.Popen(['cmd', '/c', 'ver'])", app_module.__dict__)
except RuntimeError:
    blocked_process = True
Path(os.environ["ODYSSEUS_TEST_RESULT_FILE"]).write_text(
    json.dumps({
        "blocked": blocked,
        "network_attempted": attempted["network"],
        "blocked_process": blocked_process,
        "process_attempted": attempted["process"],
        "process_origin": attempted["process_origin"],
    }, sort_keys=True),
    encoding="utf-8",
)
'''
    env = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "WINDIR") if name in os.environ}
    env["ODYSSEUS_TEST_RESULT_FILE"] = str(result_path)
    result = subprocess.run(
        [sys.executable, "-c", child],
        cwd=repo_root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0
    assert json.loads(result_path.read_text(encoding="utf-8")) == {
        "blocked": True,
        "blocked_process": True,
        "network_attempted": True,
        "process_attempted": True,
        "process_origin": "app.<module>",
    }
