"""Background job execution for the agent's `bash` tool.

Long commands (installs, ffmpeg, model downloads) should NOT block the chat
stream — a multi-minute held SSE connection is fragile (model-stops-early,
timeouts, tab suspend). Instead we launch them **detached** and let an
always-on monitor re-invoke the agent when they finish ("auto-continue").

Design goals:
  * Restart-safe: status is derived from an on-disk exit-code file, not a live
    PID, so a uvicorn restart never loses a job or its result.
  * Idempotent follow-up: a job stays {done, followed_up: False} until the
    agent has actually been re-invoked, so completion can never silently
    "do nothing" — the monitor retries on the next tick.
  * Bounded: a hard max-runtime marks a runaway job failed and STILL triggers
    a follow-up ("timed out"), so you always hear back.

This module only owns launch + state. The monitor / agent re-invocation lives
in the caller (so this stays import-light and unit-testable).

Concurrency scope is deliberately one Odysseus web process. A process-wide
reentrant lock protects the JSON transactions below; it is not a multi-process
or 12–24-hour Agent coordination primitive. Durable continuation, history and
heartbeat authority belong to Temporal Light, not this local shell-job store.
"""

from __future__ import annotations

import copy
import json
import os
import shlex
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.atomic_io import atomic_write_json
from core.platform_compat import (
    detached_popen_kwargs,
    find_bash,
    git_bash_path,
    kill_process_tree,
    pid_alive,
)

from src.constants import BG_JOBS_DIR, BG_JOBS_FILE

_JOBS_DIR = Path(BG_JOBS_DIR)
_STORE = Path(BG_JOBS_FILE)
_STORE_LOCK = threading.RLock()

# A job that runs longer than this is presumed stuck and reaped (the agent
# still gets a "timed out" follow-up so nothing hangs forever).
DEFAULT_MAX_RUNTIME_S = 3600  # 1 hour
MAX_LOCAL_DETACHED_RUNTIME_S = DEFAULT_MAX_RUNTIME_S
# Cap how much captured output we keep / feed back to the model.
_MAX_OUTPUT_CHARS = 16000
# How long a finished-and-followed-up job (record + its .sh/.cmd.sh/.log/.exit
# files) is kept before pruning, so neither the store nor data/bg_jobs/ grows
# without bound. The agent has already consumed the result by then.
_RETENTION_S = 3600  # 1 hour after follow-up
_FOLLOWUP_LEASE_S = 900


def _revision(rec: Dict[str, Any]) -> int:
    value = rec.get("revision", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _bump_revision(rec: Dict[str, Any]) -> None:
    rec["revision"] = _revision(rec) + 1


def _ensure_followup_identity(job_id: str, rec: Dict[str, Any]) -> str:
    identity = f"bg-followup:{job_id}"
    rec["followup_id"] = identity
    return identity


def _validated_max_runtime(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("max_runtime_s must be a positive integer no greater than 3600")
    if value > MAX_LOCAL_DETACHED_RUNTIME_S:
        raise ValueError("max_runtime_s exceeds the 3600-second local detached-job limit")
    return value


def _effective_max_runtime(rec: Dict[str, Any]) -> int:
    value = rec.get("max_runtime_s", DEFAULT_MAX_RUNTIME_S)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return DEFAULT_MAX_RUNTIME_S
    return min(int(value), MAX_LOCAL_DETACHED_RUNTIME_S)


def _lease_is_active(rec: Dict[str, Any], now: float) -> bool:
    lease = rec.get("followup_lease")
    if not isinstance(lease, dict) or not str(lease.get("token") or ""):
        return False
    try:
        return float(lease.get("expires_at") or 0) > now
    except (TypeError, ValueError):
        return False


def _load() -> Dict[str, Dict[str, Any]]:
    with _STORE_LOCK:
        try:
            if _STORE.exists():
                data = json.loads(_STORE.read_text(encoding="utf-8")) or {}
                if not isinstance(data, dict):
                    return {}
                return {str(job_id): rec for job_id, rec in data.items() if isinstance(rec, dict)}
        except Exception:
            pass
        return {}


def _save(jobs: Dict[str, Dict[str, Any]]) -> None:
    with _STORE_LOCK:
        atomic_write_json(str(_STORE), jobs, indent=2)


def _pid_alive(pid: Optional[int]) -> bool:
    # Delegates to the platform-safe probe. NB: a bare os.kill(pid, 0) is unsafe
    # on Windows — CPython routes it to TerminateProcess, which would KILL the
    # job we're only trying to check. core.platform_compat.pid_alive handles
    # both OSes correctly.
    return pid_alive(pid)


def launch(command: str, session_id: str, cwd: Optional[str] = None,
           max_runtime_s: int = DEFAULT_MAX_RUNTIME_S) -> Dict[str, Any]:
    """Launch `command` detached. Returns the job record (status='running').

    Output + the final exit code are written to files so status survives a
    server restart. The process is put in its own session (setsid) so it
    outlives the request/stream that started it.
    """
    max_runtime_s = _validated_max_runtime(max_runtime_s)
    _JOBS_DIR.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex[:12]
    log_path = _JOBS_DIR / f"{job_id}.log"
    exit_path = _JOBS_DIR / f"{job_id}.exit"

    # The user command goes in its OWN script file, run as a child `bash`. This
    # is what isolates it: an `exit` inside it only ends that child (so the
    # wrapper still records the exit code), and — unlike textually wrapping the
    # command in `( … )` — the wrapper can't be broken by an unbalanced paren or
    # a trailing line-continuation in the command. `$?` is the child's real
    # exit status.
    bash = find_bash()
    if bash:
        # POSIX, or Windows with Git Bash/WSL. The user command goes in its OWN
        # script file, run as a child `bash` — an `exit` inside it only ends
        # that child (so the wrapper still records the exit code), and an
        # unbalanced paren / trailing line-continuation in the command can't
        # break the wrapper. `$?` is the child's real exit status. Paths are
        # emitted as POSIX (forward-slash) + shell-quoted so Git Bash on Windows
        # handles drive paths and spaces correctly.
        cmd_path = _JOBS_DIR / f"{job_id}.cmd.sh"
        cmd_path.write_text(command + "\n", encoding="utf-8")
        lp, xp, cp = (shlex.quote(git_bash_path(p)) for p in (log_path, exit_path, cmd_path))
        script_path = _JOBS_DIR / f"{job_id}.sh"
        script_path.write_text(
            f"bash {cp} > {lp} 2>&1\n"
            f"echo $? > {xp}\n",
            encoding="utf-8",
        )
        argv = [bash, str(script_path)]
    else:
        # Windows without any bash installed: cmd.exe wrapper. The command runs
        # in its own child .cmd so %ERRORLEVEL% is the command's real exit code.
        child_path = _JOBS_DIR / f"{job_id}.child.cmd"
        child_path.write_text("@echo off\r\n" + command + "\r\n", encoding="utf-8")
        script_path = _JOBS_DIR / f"{job_id}.cmd"
        script_path.write_text(
            "@echo off\r\n"
            f'call "{child_path}" > "{log_path}" 2>&1\r\n'
            f'echo %ERRORLEVEL%> "{exit_path}"\r\n',
            encoding="utf-8",
        )
        argv = [os.environ.get("ComSpec", "cmd.exe"), "/c", str(script_path)]

    proc = subprocess.Popen(
        argv,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        cwd=cwd or None,
        **detached_popen_kwargs(),  # detach from the request lifecycle (setsid / DETACHED_PROCESS)
    )

    rec = {
        "id": job_id,
        "session_id": session_id,
        "command": command,
        "status": "running",       # running | done | failed
        "pid": proc.pid,
        "started_at": time.time(),
        "ended_at": None,
        "exit_code": None,
        "max_runtime_s": max_runtime_s,
        "followed_up": False,       # has the agent been re-invoked with the result?
        "followup_id": f"bg-followup:{job_id}",
        "revision": 1,
        "log_path": str(log_path),
        "exit_path": str(exit_path),
    }
    with _STORE_LOCK:
        jobs = _load()
        jobs[job_id] = rec
        _save(jobs)
    return copy.deepcopy(rec)


def _read_output(rec: Dict[str, Any]) -> str:
    try:
        txt = Path(rec["log_path"]).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    if len(txt) > _MAX_OUTPUT_CHARS:
        # Keep head + tail — the interesting bits are usually at both ends.
        head = txt[: _MAX_OUTPUT_CHARS // 2]
        tail = txt[-_MAX_OUTPUT_CHARS // 2:]
        txt = head + "\n…[truncated]…\n" + tail
    return txt


def _prune(jobs: Dict[str, Dict[str, Any]], now: float) -> bool:
    """Drop records (and their on-disk files) for jobs that finished, were
    followed up, and are older than the retention window. Mutates `jobs`."""
    stale = [jid for jid, rec in jobs.items()
             if rec.get("followed_up") and rec.get("ended_at")
             and (now - rec["ended_at"]) > _RETENTION_S]
    for jid in stale:
        jobs.pop(jid, None)
        for p in _JOBS_DIR.glob(f"{jid}.*"):   # .sh .cmd.sh .log .exit
            try:
                p.unlink()
            except Exception:
                pass
    return bool(stale)


def refresh() -> Dict[str, Dict[str, Any]]:
    """Reconcile every running job against disk. Marks done/failed (incl.
    timeout). Idempotent — safe to call from a poll loop. Returns the store."""
    with _STORE_LOCK:
        jobs = _load()
        changed = False
        now = time.time()
        for job_id, rec in jobs.items():
            if rec.get("status") != "running":
                continue
            exit_path_value = str(rec.get("exit_path") or "").strip()
            exit_path = Path(exit_path_value) if exit_path_value else None
            if exit_path is not None and exit_path.is_file():
                try:
                    code = int(exit_path.read_text(encoding="utf-8", errors="replace").strip() or "1")
                except Exception:
                    code = 1
                rec["exit_code"] = code
                rec["status"] = "done" if code == 0 else "failed"
                rec["ended_at"] = now
                _ensure_followup_identity(job_id, rec)
                _bump_revision(rec)
                changed = True
            elif (now - rec.get("started_at", now)) > _effective_max_runtime(rec):
                # Runaway / stuck — reap it but STILL surface a follow-up.
                _kill(rec.get("pid"))
                rec["status"] = "failed"
                rec["exit_code"] = -1
                rec["ended_at"] = now
                rec["timed_out"] = True
                _ensure_followup_identity(job_id, rec)
                _bump_revision(rec)
                changed = True
            elif not _pid_alive(rec.get("pid")) and not (
                exit_path is not None and exit_path.exists()
            ):
                # Process vanished without writing an exit code (killed, OOM,
                # crash). Don't leave it "running" forever.
                rec["status"] = "failed"
                rec["exit_code"] = -1
                rec["ended_at"] = now
                rec["died"] = True
                _ensure_followup_identity(job_id, rec)
                _bump_revision(rec)
                changed = True
        if _prune(jobs, now):
            changed = True
        if changed:
            _save(jobs)
        return copy.deepcopy(jobs)


def _kill(pid: Optional[int]) -> None:
    # Cross-platform process-tree teardown (POSIX killpg / Windows taskkill /T).
    kill_process_tree(pid)


def pending_followups() -> List[Dict[str, Any]]:
    """Finished jobs the agent hasn't been re-invoked for yet. The monitor
    can inspect these; only lease_pending_followups() grants execution."""
    jobs = refresh()
    return [copy.deepcopy(r) for r in jobs.values()
            if r.get("status") in ("done", "failed") and not r.get("followed_up")]


def lease_pending_followups(
    *,
    lease_owner: str,
    lease_s: int = _FOLLOWUP_LEASE_S,
    limit: int | None = None,
) -> List[Dict[str, Any]]:
    """Atomically lease terminal jobs for one same-process monitor attempt."""

    if not str(lease_owner or "").strip():
        raise ValueError("lease_owner is required")
    if isinstance(lease_s, bool) or not isinstance(lease_s, int) or lease_s <= 0:
        raise ValueError("lease_s must be a positive integer")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0):
        raise ValueError("limit must be a positive integer or None")

    with _STORE_LOCK:
        jobs = refresh()
        now = time.time()
        leased: List[Dict[str, Any]] = []
        changed = False
        for job_id in sorted(jobs):
            rec = jobs[job_id]
            if rec.get("status") not in ("done", "failed") or rec.get("followed_up"):
                continue
            if _lease_is_active(rec, now):
                continue
            identity = _ensure_followup_identity(job_id, rec)
            rec["followup_lease"] = {
                "token": uuid.uuid4().hex,
                "owner": str(lease_owner),
                "acquired_at": now,
                "expires_at": now + lease_s,
                "followup_id": identity,
            }
            _bump_revision(rec)
            leased.append(copy.deepcopy(rec))
            changed = True
            if limit is not None and len(leased) >= limit:
                break
        if changed:
            _save(jobs)
        return leased


def release_followup_lease(job_id: str, lease_token: str) -> bool:
    """Release one matching process-local lease so a failed attempt can retry."""

    with _STORE_LOCK:
        jobs = _load()
        rec = jobs.get(job_id)
        if rec is None:
            return False
        lease = rec.get("followup_lease")
        if not isinstance(lease, dict) or str(lease.get("token") or "") != str(lease_token or ""):
            return False
        rec.pop("followup_lease", None)
        _bump_revision(rec)
        _save(jobs)
        return True


def mark_followed_up(job_id: str, *, lease_token: str | None = None) -> bool:
    """Persist the stable completion marker for a matching follow-up lease."""

    with _STORE_LOCK:
        jobs = _load()
        rec = jobs.get(job_id)
        if rec is None:
            return False
        identity = _ensure_followup_identity(job_id, rec)
        if rec.get("followed_up") and rec.get("followup_completed_id") == identity:
            return True
        lease = rec.get("followup_lease")
        if isinstance(lease, dict):
            if str(lease.get("token") or "") != str(lease_token or ""):
                return False
        elif lease_token is not None:
            return False
        rec["followed_up"] = True
        rec["followup_completed_id"] = identity
        rec["followed_up_at"] = time.time()
        rec.pop("followup_lease", None)
        _bump_revision(rec)
        _save(jobs)
        return True


def get(job_id: str) -> Optional[Dict[str, Any]]:
    with _STORE_LOCK:
        refresh()  # reconcile against disk so status/exit_code are current
        rec = _load().get(job_id)
        if rec:
            rec = copy.deepcopy(rec)
            rec["output"] = _read_output(rec)
        return rec


def list_for_session(session_id: str) -> List[Dict[str, Any]]:
    return [r for r in refresh().values() if r.get("session_id") == session_id]


def kill(job_id: str) -> Optional[Dict[str, Any]]:
    """Terminate a running job's process tree and mark it killed. Returns the
    updated record, or None if the id is unknown. Idempotent: a job that already
    finished is returned unchanged. Sets followed_up so the monitor does not also
    fire an auto-continue for a job the agent deliberately stopped."""
    with _STORE_LOCK:
        jobs = _load()
        rec = jobs.get(job_id)
        if rec is None:
            return None
        if rec.get("status") == "running":
            _kill(rec.get("pid"))
            rec["status"] = "failed"
            rec["exit_code"] = -1
            rec["ended_at"] = time.time()
            rec["killed"] = True
            rec["followed_up"] = True
            identity = _ensure_followup_identity(job_id, rec)
            rec["followup_completed_id"] = identity
            rec["followed_up_at"] = rec["ended_at"]
            rec.pop("followup_lease", None)
            _bump_revision(rec)
            _save(jobs)
        return copy.deepcopy(rec)


def result_text(rec: Dict[str, Any]) -> str:
    """Human/agent-readable summary of a finished job, for the follow-up."""
    out = _read_output(rec)
    if rec.get("killed"):
        head = "Background job was killed."
    elif rec.get("timed_out"):
        head = f"Background job timed out after {rec.get('max_runtime_s')}s."
    elif rec.get("died"):
        head = "Background job process died unexpectedly (no exit code)."
    else:
        head = f"Background job finished with exit code {rec.get('exit_code')}."
    return f"{head}\nCommand: {rec.get('command')}\n\nOutput:\n{out or '(no output)'}"
