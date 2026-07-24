"""Redacted runtime tool inventory and gate status packets."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Mapping

from src.builtin_tool_catalog import (
    build_builtin_descriptors,
    builtin_spec,
    catalog_call_allowed,
)
from src.effectful_tool_matrix import tool_effect_category
from src.planning_definition_projection import build_agent_maintenance_handoff
from src.tool_catalog import (
    ToolAvailability,
    ToolDescriptorV2,
    ToolEffectClass,
    ToolFamily,
    ToolLifecycle,
    ToolPermission,
    ToolRiskLevel,
    ToolSource,
    ToolVisibility,
)
from src.tool_security import runtime_tool_security_profile


RUNTIME_TOOL_STATUS_SCHEMA = "odysseus.runtime_tool_status.v1"
TOOL_CATALOG_PROJECTION_SCHEMA = "odysseus.tool_catalog_projection.v1"
AGENT_MAINTENANCE_BOOTSTRAP_SCHEMA = "odysseus.agent_maintenance_bootstrap.v1"

_MAINTENANCE_ROADMAP_PATH = Path(
    "docs/plans/agent-maintenance-safety-harness-roadmap.json"
)
_MAINTENANCE_RUN_STATE_PATH = Path(
    "docs/plans/telegram-todo-domain-truth-run-state.json"
)
_MAINTENANCE_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]
_MAINTENANCE_VERIFY_COMMAND = (
    "python -B scripts/verify.py --lane guards-only --receipt"
)
_MAINTENANCE_STATE_MAX_BYTES = 512 * 1024
_MAINTENANCE_HOOK_INPUT_MAX_BYTES = 16 * 1024
_MAINTENANCE_JSON_MAX_DEPTH = 24
_MAINTENANCE_JSON_MAX_NODES = 30_000
_MAINTENANCE_JSON_MAX_STRING = 8_192
_MAINTENANCE_GIT_TIMEOUT_SECONDS = 1.0
_MAINTENANCE_GIT_OUTPUT_MAX_BYTES = 64 * 1024
_MAINTENANCE_DIRTY_COUNT_MAX = 99
_MAINTENANCE_START_SOURCES = frozenset({"startup", "resume", "clear", "compact"})
_MAINTENANCE_GIT_READS = {
    "branch": (
        "git",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "rev-parse",
        "--abbrev-ref",
        "HEAD",
    ),
    "status": (
        "git",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "status",
        "--porcelain=v1",
        "--untracked-files=normal",
        "--ignore-submodules=all",
    ),
}
_MAINTENANCE_BRANCH_RE = re.compile(r"^[^\x00-\x20~^:?*\\\[\]]{1,160}$")

_LIVE_NETWORK_TOOLS = {
    "web_search",
    "web_fetch",
    "trigger_research",
    "api_call",
    "download_model",
    "serve_model",
    "serve_preset",
    "search_hf_models",
}
_USER_GATE_TOOLS = {"ask_user", "update_plan"}
_LOCAL_MUTATION_PREFIXES = ("manage_", "create_", "edit_", "update_", "delete_", "write_")
_SECRET_RE = re.compile(
    r"(?i)(authorization|cookie|api[_-]?key|password|passwd|secret|token|bearer\s+[A-Za-z0-9._-]{8,})"
)
_DYNAMIC_TOOL_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")


class _MaintenanceBootstrapError(Exception):
    """Internal fixed-code failure; its text is never returned to a caller."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_agent_maintenance_bootstrap(
    *,
    roadmap: object,
    run_state: object,
    branch_state: object,
    expected_branch_match: object,
    dirty: object,
    dirty_entry_count: object,
    dirty_count_capped: object = False,
    input_warning_code: object = "none",
) -> dict[str, Any]:
    """Build the deterministic, authority-free AMH session projection.

    This function is pure.  It never reads the repository and never mutates its
    arguments.  ``build_agent_maintenance_handoff`` remains the single
    authority for goal, slice, claim, blocker and owner-question semantics.
    The wrapper adds only bounded repository observations and hard-coded false
    action-authority fields.
    """

    warning_code = _maintenance_warning_code(input_warning_code)
    roadmap_value = roadmap if type(roadmap) is dict else {}
    run_state_value = run_state if type(run_state) is dict else {}
    if type(roadmap) is not dict or type(run_state) is not dict:
        warning_code = "state_authority_invalid"
    else:
        try:
            _validate_maintenance_json_tree(roadmap_value)
            _validate_maintenance_json_tree(run_state_value)
        except _MaintenanceBootstrapError:
            roadmap_value = {}
            run_state_value = {}
            warning_code = "state_authority_invalid"

    gate_queue = roadmap_value.get("gate_queue", [])
    clarifications = run_state_value.get("pending_user_requests", [])
    if type(gate_queue) is not list or type(clarifications) is not list:
        gate_queue = []
        clarifications = []
        warning_code = "state_authority_invalid"

    handoff = build_agent_maintenance_handoff(
        roadmap=roadmap_value,
        run_state=run_state_value,
        gate_queue=gate_queue,
        clarifications=clarifications,
        receipt=None,
    )
    try:
        _validate_maintenance_json_tree(handoff)
    except _MaintenanceBootstrapError:
        # The canonical projector currently emits only exact JSON primitives.
        # If that invariant ever changes, retain a canonical fail-closed packet
        # rather than serializing an unfamiliar object.
        handoff = build_agent_maintenance_handoff(roadmap={}, run_state={})
        warning_code = "state_authority_invalid"

    branch_value = branch_state if type(branch_state) is str else "unknown"
    if branch_value not in {"named", "detached", "unknown"}:
        branch_value = "unknown"
        warning_code = "git_read_failed"
    branch_matches = (
        expected_branch_match if type(expected_branch_match) is bool else False
    )
    dirty_value = dirty if type(dirty) is bool else False
    count_is_valid = bool(
        type(dirty_entry_count) is int
        and 0 <= dirty_entry_count <= _MAINTENANCE_DIRTY_COUNT_MAX
    )
    count_value = dirty_entry_count if count_is_valid else 0
    capped_value = dirty_count_capped if type(dirty_count_capped) is bool else False
    if (
        type(expected_branch_match) is not bool
        or type(dirty) is not bool
        or not count_is_valid
        or type(dirty_count_capped) is not bool
    ):
        warning_code = "git_read_failed"

    maintenance_status = "ready"
    if warning_code not in {"none", "dirty_worktree"}:
        maintenance_status = "stop"
    elif branch_value != "named":
        maintenance_status = "stop"
        warning_code = "detached_head" if branch_value == "detached" else "git_read_failed"
    elif not branch_matches:
        maintenance_status = "stop"
        warning_code = "branch_mismatch"
    elif handoff.get("status") not in {"active", "ready"}:
        maintenance_status = "stop"
        warning_code = {
            "waiting_on_user": "owner_input_required",
            "blocked_conflict": "authority_conflict",
            "blocked": "authority_blocked",
        }.get(str(handoff.get("status") or ""), "authority_blocked")
    elif dirty_value:
        warning_code = "dirty_worktree"

    core: dict[str, Any] = {
        "schema": AGENT_MAINTENANCE_BOOTSTRAP_SCHEMA,
        "maintenance_status": maintenance_status,
        "warning_code": warning_code,
        "handoff": handoff,
        "repository": {
            "branch_state": branch_value,
            "expected_branch_match": branch_matches,
            "dirty": dirty_value,
            "dirty_entry_count": count_value,
            "dirty_count_capped": capped_value,
        },
        "verification": {
            "entrypoint": _MAINTENANCE_VERIFY_COMMAND,
            "receipt_required_for_completion": True,
        },
        "authority": {
            "read_only": True,
            "idempotent": True,
            "execution_authorized": False,
            "write_authorized": False,
            "commit_authorized": False,
            "push_authorized": False,
            "live_authorized": False,
        },
        "read_only": True,
        "idempotent": True,
        "execution_authorized": False,
        "write_authorized": False,
        "commit_authorized": False,
        "push_authorized": False,
        "live_authorized": False,
        "raw_output_visible": False,
        "private_paths_visible": False,
        "raw_evidence_visible": False,
        "raw_reasons_visible": False,
    }
    core["projection_digest"] = _maintenance_projection_digest(core)
    return core


def collect_agent_maintenance_bootstrap(
    *, repo_root: Path | str | None = None
) -> dict[str, Any]:
    """Collect the fixed repo files and two allowlisted Git observations."""

    root = (
        Path(repo_root)
        if repo_root is not None
        else _MAINTENANCE_DEFAULT_REPO_ROOT
    )
    try:
        roadmap = _load_maintenance_json(root, _MAINTENANCE_ROADMAP_PATH)
        run_state = _load_maintenance_json(root, _MAINTENANCE_RUN_STATE_PATH)
    except _MaintenanceBootstrapError as exc:
        return build_agent_maintenance_bootstrap(
            roadmap={},
            run_state={},
            branch_state="unknown",
            expected_branch_match=False,
            dirty=False,
            dirty_entry_count=0,
            input_warning_code=exc.code,
        )

    branch_read = _run_fixed_git_read(root, "branch")
    git_failure = _git_read_failure_code(branch_read)
    if git_failure != "none":
        return build_agent_maintenance_bootstrap(
            roadmap=roadmap,
            run_state=run_state,
            branch_state="unknown",
            expected_branch_match=False,
            dirty=False,
            dirty_entry_count=0,
            input_warning_code=git_failure,
        )
    status_read = _run_fixed_git_read(root, "status")
    git_failure = _git_read_failure_code(status_read)
    if git_failure != "none":
        return build_agent_maintenance_bootstrap(
            roadmap=roadmap,
            run_state=run_state,
            branch_state="unknown",
            expected_branch_match=False,
            dirty=False,
            dirty_entry_count=0,
            input_warning_code=git_failure,
        )

    branch_state, branch_name = _classify_git_branch(branch_read["stdout"])
    expected_branch = run_state.get("branch")
    expected_match = bool(
        branch_state == "named"
        and type(expected_branch) is str
        and _MAINTENANCE_BRANCH_RE.fullmatch(expected_branch)
        and branch_name == expected_branch
    )
    status_bytes = status_read["stdout"]
    status_lines = tuple(line for line in status_bytes.splitlines() if line)
    dirty_count_capped = len(status_lines) > _MAINTENANCE_DIRTY_COUNT_MAX
    dirty_count = min(len(status_lines), _MAINTENANCE_DIRTY_COUNT_MAX)
    return build_agent_maintenance_bootstrap(
        roadmap=roadmap,
        run_state=run_state,
        branch_state=branch_state,
        expected_branch_match=expected_match,
        dirty=bool(status_bytes),
        dirty_entry_count=dirty_count,
        dirty_count_capped=dirty_count_capped,
    )


def render_agent_maintenance_bootstrap(projection: Mapping[str, Any]) -> str:
    """Render only the bounded projection used by hooks and internal agents."""

    value = projection if type(projection) is dict else {}
    handoff = value.get("handoff") if type(value.get("handoff")) is dict else {}
    goal = handoff.get("goal") if type(handoff.get("goal")) is dict else {}
    slice_value = handoff.get("slice") if type(handoff.get("slice")) is dict else {}
    claim = handoff.get("claim") if type(handoff.get("claim")) is dict else {}
    repository = (
        value.get("repository") if type(value.get("repository")) is dict else {}
    )
    authority = value.get("authority") if type(value.get("authority")) is dict else {}
    maintenance_status = _maintenance_id_for_render(
        value.get("maintenance_status"), "stop"
    )
    next_action = _maintenance_id_for_render(handoff.get("next_action"), "stop")
    ordinary_work = "may_continue"
    return (
        "## Odysseus maintenance bootstrap (read-only)\n"
        f"- schema: {AGENT_MAINTENANCE_BOOTSTRAP_SCHEMA}\n"
        f"- maintenance_status: {maintenance_status}\n"
        f"- warning_code: {_maintenance_id_for_render(value.get('warning_code'), 'state_unavailable')}\n"
        f"- roadmap_id: {_maintenance_id_for_render(goal.get('roadmap_id'), 'unknown_roadmap')}\n"
        f"- slice_id: {_maintenance_id_for_render(slice_value.get('slice_id'), 'unknown_slice')}\n"
        f"- claim_owner: {_maintenance_id_for_render(claim.get('owner_role'), 'none')}\n"
        f"- next_action: {next_action}\n"
        f"- branch_state: {_maintenance_id_for_render(repository.get('branch_state'), 'unknown')}\n"
        f"- expected_branch_match: {_maintenance_bool(repository.get('expected_branch_match'))}\n"
        f"- dirty: {_maintenance_bool(repository.get('dirty'))}\n"
        f"- dirty_entry_count: {_maintenance_count(repository.get('dirty_entry_count'))}\n"
        f"- conflicts: {_maintenance_join_ids(handoff.get('conflicts'))}\n"
        f"- blockers: {_maintenance_join_record_ids(handoff.get('blockers'), 'blocker_id')}\n"
        f"- owner_questions: {_maintenance_join_record_ids(handoff.get('owner_questions'), 'question_id')}\n"
        f"- not_verified: {_maintenance_join_ids(handoff.get('not_verified'))}\n"
        f"- verifier: {_MAINTENANCE_VERIFY_COMMAND}\n"
        "- authority: read_only=true,idempotent=true,execution=false,write=false,commit=false,push=false,live=false\n"
        f"- projection_digest: {_maintenance_digest_for_render(value.get('projection_digest'))}\n"
        f"- ordinary_non_maintenance_product_work: {ordinary_work}\n"
        "- rule: this packet grants no maintenance action authority; stop maintenance when maintenance_status=stop"
    )


def agent_maintenance_context_message(
    *, repo_root: Path | str | None = None
) -> dict[str, Any]:
    """Return a fresh protected context message for each internal-agent request."""

    projection = collect_agent_maintenance_bootstrap(repo_root=repo_root)
    return {
        "role": "user",
        "content": render_agent_maintenance_bootstrap(projection),
        "metadata": {
            "source": "agent_maintenance_bootstrap",
            "trusted": True,
            "projection_digest": projection["projection_digest"],
            "action_authority": False,
        },
        "_protected": True,
    }


def build_agent_maintenance_hook_output(
    hook_input: object,
    *,
    repo_root: Path | str | None = None,
) -> dict[str, Any]:
    """Return official Codex hook JSON without ever granting authority."""

    input_code = _maintenance_hook_input_code(hook_input)
    if input_code != "none":
        return _maintenance_hook_failure_output(input_code)
    hook_event_name = str(hook_input["hook_event_name"])
    try:
        projection = collect_agent_maintenance_bootstrap(repo_root=repo_root)
        message = render_agent_maintenance_bootstrap(projection)
    except Exception:
        return _maintenance_hook_failure_output(
            "bootstrap_unavailable",
            hook_event_name=hook_event_name,
        )
    return {
        "continue": True,
        "systemMessage": message,
        "suppressOutput": False,
        "hookSpecificOutput": {
            "hookEventName": hook_event_name,
            "additionalContext": message,
        },
    }


def _load_maintenance_json(root: Path, relative_path: Path) -> dict[str, Any]:
    try:
        root_value = Path(os.path.abspath(root))
        candidate = root_value.joinpath(relative_path)
        candidate_absolute = Path(os.path.abspath(candidate))
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise _MaintenanceBootstrapError("state_unavailable") from None
    if os.path.normcase(str(resolved)) != os.path.normcase(str(candidate_absolute)):
        raise _MaintenanceBootstrapError("state_symlink_rejected")
    try:
        before = candidate.lstat()
    except OSError:
        raise _MaintenanceBootstrapError("state_unavailable") from None
    if candidate.is_symlink() or not stat.S_ISREG(before.st_mode):
        raise _MaintenanceBootstrapError("state_symlink_rejected")
    if before.st_size > _MAINTENANCE_STATE_MAX_BYTES:
        raise _MaintenanceBootstrapError("state_oversized")
    if before.st_size < 2:
        raise _MaintenanceBootstrapError("state_malformed")
    try:
        with candidate.open("rb") as handle:
            raw = handle.read(_MAINTENANCE_STATE_MAX_BYTES + 1)
        after = candidate.lstat()
    except OSError:
        raise _MaintenanceBootstrapError("state_unavailable") from None
    if len(raw) > _MAINTENANCE_STATE_MAX_BYTES:
        raise _MaintenanceBootstrapError("state_oversized")
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ino != after.st_ino
    ):
        raise _MaintenanceBootstrapError("state_changed_during_read")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_maintenance_json_object,
            parse_constant=_reject_maintenance_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, _MaintenanceBootstrapError):
        raise _MaintenanceBootstrapError("state_malformed") from None
    if type(value) is not dict:
        raise _MaintenanceBootstrapError("state_malformed")
    _validate_maintenance_json_tree(value)
    return value


def _maintenance_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if type(key) is not str or key in value:
            raise _MaintenanceBootstrapError("state_malformed")
        value[key] = item
    return value


def _reject_maintenance_json_constant(_: str) -> None:
    raise _MaintenanceBootstrapError("state_malformed")


def _validate_maintenance_json_tree(value: object) -> None:
    stack: list[tuple[object, int]] = [(value, 0)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > _MAINTENANCE_JSON_MAX_NODES or depth > _MAINTENANCE_JSON_MAX_DEPTH:
            raise _MaintenanceBootstrapError("state_malformed")
        if current is None or type(current) is bool:
            continue
        if type(current) is int:
            if abs(current) > 9_223_372_036_854_775_807:
                raise _MaintenanceBootstrapError("state_malformed")
            continue
        if type(current) is float:
            if not math.isfinite(current):
                raise _MaintenanceBootstrapError("state_malformed")
            continue
        if type(current) is str:
            if len(current) > _MAINTENANCE_JSON_MAX_STRING:
                raise _MaintenanceBootstrapError("state_malformed")
            continue
        if type(current) is list:
            stack.extend((item, depth + 1) for item in current)
            continue
        if type(current) is dict:
            for key, item in current.items():
                if type(key) is not str or len(key) > 160:
                    raise _MaintenanceBootstrapError("state_malformed")
                stack.append((item, depth + 1))
            continue
        raise _MaintenanceBootstrapError("state_malformed")


def _run_fixed_git_read(root: Path, operation: str) -> dict[str, Any]:
    command = _MAINTENANCE_GIT_READS.get(operation)
    if command is None:
        return _git_read_result(returncode=-1)
    stdout_buffer = bytearray()
    stderr_buffer = bytearray()
    try:
        process = subprocess.Popen(
            list(command),
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            env=_maintenance_git_environment(),
        )
    except OSError:
        return _git_read_result(returncode=-1)
    stdout_thread = threading.Thread(
        target=_drain_bounded_pipe,
        args=(process.stdout, stdout_buffer, _MAINTENANCE_GIT_OUTPUT_MAX_BYTES),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_drain_bounded_pipe,
        args=(process.stderr, stderr_buffer, 4_096),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    timed_out = False
    try:
        returncode = process.wait(timeout=_MAINTENANCE_GIT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            process.kill()
        except OSError:
            pass
        try:
            returncode = process.wait(timeout=0.25)
        except (OSError, subprocess.TimeoutExpired):
            returncode = -1
            for pipe in (process.stdout, process.stderr):
                try:
                    if pipe is not None:
                        pipe.close()
                except (OSError, ValueError):
                    pass
    stdout_thread.join(timeout=0.25)
    stderr_thread.join(timeout=0.25)
    oversized = len(stdout_buffer) > _MAINTENANCE_GIT_OUTPUT_MAX_BYTES
    return _git_read_result(
        returncode=returncode,
        stdout=bytes(stdout_buffer[:_MAINTENANCE_GIT_OUTPUT_MAX_BYTES]),
        timed_out=timed_out,
        oversized=oversized,
        stderr_present=bool(stderr_buffer),
    )


def _maintenance_git_environment() -> dict[str, str]:
    environment = dict(os.environ)
    safe_directories: list[str] = []
    raw_count = environment.get("GIT_CONFIG_COUNT", "")
    if raw_count.isdigit() and 0 <= int(raw_count) <= 16:
        for index in range(int(raw_count)):
            if environment.get(f"GIT_CONFIG_KEY_{index}") == "safe.directory":
                safe_value = environment.get(f"GIT_CONFIG_VALUE_{index}")
                if type(safe_value) is str and len(safe_value) <= 1_024:
                    safe_directories.append(safe_value)
    for key in tuple(environment):
        if key.upper().startswith("GIT_"):
            environment.pop(key, None)
    if safe_directories:
        environment["GIT_CONFIG_COUNT"] = str(len(safe_directories))
        for index, safe_value in enumerate(safe_directories):
            environment[f"GIT_CONFIG_KEY_{index}"] = "safe.directory"
            environment[f"GIT_CONFIG_VALUE_{index}"] = safe_value
    environment.update(
        {
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_PAGER": "cat",
        }
    )
    return environment


def _drain_bounded_pipe(
    pipe: BinaryIO | None,
    sink: bytearray,
    limit: int,
) -> None:
    if pipe is None:
        return
    try:
        while True:
            chunk = pipe.read(4_096)
            if not chunk:
                break
            remaining = limit + 1 - len(sink)
            if remaining > 0:
                sink.extend(chunk[:remaining])
    except (OSError, ValueError):
        return
    finally:
        try:
            pipe.close()
        except (OSError, ValueError):
            pass


def _git_read_result(
    *,
    returncode: int,
    stdout: bytes = b"",
    timed_out: bool = False,
    oversized: bool = False,
    stderr_present: bool = False,
) -> dict[str, Any]:
    return {
        "returncode": int(returncode),
        "stdout": bytes(stdout),
        "timed_out": bool(timed_out),
        "oversized": bool(oversized),
        "stderr_present": bool(stderr_present),
    }


def _git_read_failure_code(*results: object) -> str:
    for result in results:
        if type(result) is not dict:
            return "git_read_failed"
        if result.get("timed_out") is True:
            return "git_read_timeout"
        if result.get("oversized") is True:
            return "git_output_oversized"
        if type(result.get("stdout")) is not bytes:
            return "git_read_failed"
        if type(result.get("returncode")) is not int or result.get("returncode") != 0:
            return "git_read_failed"
        if result.get("stderr_present") is not False:
            return "git_read_failed"
    return "none"


def _classify_git_branch(raw: bytes) -> tuple[str, str]:
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeError:
        return "unknown", ""
    if len(lines) != 1:
        return "unknown", ""
    name = lines[0].strip()
    if name == "HEAD":
        return "detached", ""
    if not _MAINTENANCE_BRANCH_RE.fullmatch(name):
        return "unknown", ""
    return "named", name


def _maintenance_warning_code(value: object) -> str:
    allowed = {
        "none",
        "dirty_worktree",
        "state_unavailable",
        "state_symlink_rejected",
        "state_oversized",
        "state_malformed",
        "state_changed_during_read",
        "state_authority_invalid",
        "git_read_timeout",
        "git_output_oversized",
        "git_read_failed",
        "detached_head",
        "branch_mismatch",
        "owner_input_required",
        "authority_conflict",
        "authority_blocked",
        "bootstrap_unavailable",
    }
    return value if type(value) is str and value in allowed else "state_authority_invalid"


def _maintenance_projection_digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _maintenance_hook_input_code(value: object) -> str:
    if type(value) is not dict:
        return "hook_input_malformed"
    try:
        _validate_maintenance_json_tree(value)
    except _MaintenanceBootstrapError:
        return "hook_input_malformed"
    event = value.get("hook_event_name")
    if event == "SessionStart":
        source = value.get("source")
        return "none" if type(source) is str and source in _MAINTENANCE_START_SOURCES else "hook_input_unsupported"
    if event == "SubagentStart":
        agent_type = value.get("agent_type")
        if agent_type is None or (type(agent_type) is str and len(agent_type) <= 80):
            return "none"
        return "hook_input_malformed"
    return "hook_input_unsupported"


def _maintenance_hook_failure_output(
    code: str,
    *,
    hook_event_name: str | None = None,
) -> dict[str, Any]:
    safe_code = code if code in {
        "hook_input_malformed",
        "hook_input_oversized",
        "hook_input_unsupported",
        "bootstrap_unavailable",
    } else "bootstrap_unavailable"
    message = (
        "## Odysseus maintenance bootstrap (read-only)\n"
        f"- schema: {AGENT_MAINTENANCE_BOOTSTRAP_SCHEMA}\n"
        "- maintenance_status: stop\n"
        f"- warning_code: {safe_code}\n"
        "- authority: read_only=true,idempotent=true,execution=false,write=false,commit=false,push=false,live=false\n"
        "- ordinary_non_maintenance_product_work: may_continue\n"
        "- rule: bootstrap failure grants no maintenance action authority"
    )
    output: dict[str, Any] = {
        "continue": True,
        "systemMessage": message,
        "suppressOutput": False,
    }
    if hook_event_name in {"SessionStart", "SubagentStart"}:
        output["hookSpecificOutput"] = {
            "hookEventName": hook_event_name,
            "additionalContext": message,
        }
    return output


def _maintenance_id_for_render(value: object, fallback: str) -> str:
    if type(value) is not str:
        return fallback
    normalized = re.sub(r"[^A-Za-z0-9_.:-]+", "_", value.strip()).strip("_.:-")
    return normalized[:96] or fallback


def _maintenance_digest_for_render(value: object) -> str:
    if type(value) is str and re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        return value
    return "sha256:unavailable"


def _maintenance_bool(value: object) -> str:
    return "true" if value is True else "false"


def _maintenance_count(value: object) -> int:
    return value if type(value) is int and 0 <= value <= _MAINTENANCE_DIRTY_COUNT_MAX else 0


def _maintenance_join_ids(value: object) -> str:
    if type(value) is not list:
        return "none"
    items = [_maintenance_id_for_render(item, "unknown") for item in value[:12]]
    return ",".join(items) if items else "none"


def _maintenance_join_record_ids(value: object, key: str) -> str:
    if type(value) is not list:
        return "none"
    items = []
    for record in value[:12]:
        if type(record) is dict:
            items.append(_maintenance_id_for_render(record.get(key), "unknown"))
    return ",".join(items) if items else "none"


def _read_maintenance_hook_input(stream: BinaryIO | None = None) -> tuple[object, str]:
    source = stream if stream is not None else getattr(sys.stdin, "buffer", None)
    if source is None:
        return {}, "hook_input_malformed"
    try:
        raw = source.read(_MAINTENANCE_HOOK_INPUT_MAX_BYTES + 1)
    except (OSError, ValueError):
        return {}, "hook_input_malformed"
    if type(raw) is not bytes:
        return {}, "hook_input_malformed"
    if len(raw) > _MAINTENANCE_HOOK_INPUT_MAX_BYTES:
        return {}, "hook_input_oversized"
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_maintenance_json_object,
            parse_constant=_reject_maintenance_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, _MaintenanceBootstrapError):
        return {}, "hook_input_malformed"
    return value, _maintenance_hook_input_code(value)


def main(argv: Iterable[str] | None = None) -> int:
    """Read one Codex hook event from stdin and emit one bounded JSON object."""

    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if arguments:
        output = _maintenance_hook_failure_output("hook_input_unsupported")
    else:
        hook_input, input_code = _read_maintenance_hook_input()
        output = (
            build_agent_maintenance_hook_output(hook_input, repo_root=Path.cwd())
            if input_code == "none"
            else _maintenance_hook_failure_output(input_code)
        )
    try:
        serialized = json.dumps(
            output,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        sys.stdout.write(serialized + "\n")
    except (OSError, ValueError, TypeError):
        return 1
    return 0


def build_tool_catalog_projection(
    *,
    disabled_tools: Iterable[str] = (),
    builtin_descriptions: Mapping[str, str],
    plugin_tools: Iterable[Any] = (),
    mcp_tools: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Return deterministic, redacted descriptor projections for API consumers."""

    disabled = {str(item) for item in disabled_tools}
    descriptors = build_builtin_descriptors(builtin_descriptions).descriptors
    rows: dict[str, dict[str, Any]] = {}
    for descriptor in descriptors:
        spec = builtin_spec(descriptor.tool_id)
        profile = runtime_tool_security_profile(descriptor.tool_id)
        runtime_enabled = bool(
            descriptor.tool_id not in disabled
            and spec is not None
            and catalog_call_allowed(descriptor.tool_id)
            and descriptor.availability == ToolAvailability.AVAILABLE
        )
        settings_mutable = bool(
            spec is not None
            and catalog_call_allowed(descriptor.tool_id)
            and descriptor.availability == ToolAvailability.AVAILABLE
        )
        row = _descriptor_projection_row(
            descriptor,
            enabled=runtime_enabled,
            runtime_availability=(
                "disabled_by_settings"
                if descriptor.tool_id in disabled
                else "enabled"
                if runtime_enabled
                else "blocked_by_catalog"
            ),
            settings_mutable=settings_mutable,
        )
        row.update(
            permission=profile.permission.value,
            risk_level=profile.risk_level.value,
            effect_class=profile.effect_class.value,
            requires_confirmation=profile.requires_confirmation,
            policy_projection_source=profile.source,
            registration_disposition=(
                spec.registration_disposition.value if spec is not None else "unknown"
            ),
            default_policy=spec.default_policy.value if spec is not None else "unknown",
        )
        rows[descriptor.tool_id] = row

    for tool in sorted(plugin_tools, key=lambda item: str(getattr(item, "name", ""))):
        name = str(getattr(tool, "name", "") or "").strip()
        if not _DYNAMIC_TOOL_ID_RE.fullmatch(name) or name in rows:
            continue
        from src.tool_registry import descriptor_for_tool

        descriptor = descriptor_for_tool(tool)
        catalog_mutable = bool(
            descriptor.family != ToolFamily.UNCLASSIFIED_DYNAMIC
            and descriptor.lifecycle in {ToolLifecycle.ACTIVE, ToolLifecycle.CONTEXTUAL}
            and descriptor.availability == ToolAvailability.AVAILABLE
        )
        row = _descriptor_projection_row(
            descriptor,
            enabled=catalog_mutable and name not in disabled,
            runtime_availability=(
                "blocked_by_catalog"
                if not catalog_mutable
                else "disabled_by_settings"
                if name in disabled
                else "enabled"
            ),
            settings_mutable=catalog_mutable,
        )
        row.update(
            cat="Plugins",
            ctx="~plugin",
            source_id=str(getattr(tool, "source_id", "plugin:local")),
            policy_projection_source="dynamic_explicit_or_admin_default",
        )
        rows[name] = row

    for tool in sorted(mcp_tools, key=lambda item: str(item.get("qualified_name") or "")):
        name = str(tool.get("qualified_name") or "").strip()
        if not _DYNAMIC_TOOL_ID_RE.fullmatch(name) or name in rows:
            continue
        family, lifecycle, availability, catalog_blocked = _mcp_catalog_metadata(tool)
        descriptor = _dynamic_descriptor(
            tool_id=name,
            description=tool.get("description", ""),
            source=ToolSource.MCP,
            permission=ToolPermission.ADMIN,
            family=family,
            lifecycle=lifecycle,
            availability=availability,
        )
        catalog_capable = bool(
            not catalog_blocked
            and lifecycle in {ToolLifecycle.ACTIVE, ToolLifecycle.CONTEXTUAL}
            and availability in {ToolAvailability.AVAILABLE, ToolAvailability.DISABLED}
        )
        mcp_disabled = bool(tool.get("is_disabled"))
        row = _descriptor_projection_row(
            descriptor,
            enabled=(
                catalog_capable
                and availability == ToolAvailability.AVAILABLE
                and not mcp_disabled
            ),
            runtime_availability=(
                "blocked_by_catalog"
                if not catalog_capable
                else "disabled_by_mcp_policy"
                if mcp_disabled
                else "blocked_by_catalog"
                if availability != ToolAvailability.AVAILABLE
                else "enabled"
            ),
            settings_mutable=False,
        )
        row.update(
            cat="Plugins",
            ctx="~mcp",
            source_id=str(tool.get("source_id") or "mcp:unknown"),
            policy_authority=str(tool.get("policy_authority") or "mcp_runtime_policy"),
            policy_projection_source="dynamic_conservative",
        )
        rows[name] = row

    ordered = tuple(rows[name] for name in sorted(rows))
    mutable_rows = tuple(item for item in ordered if item["settings_mutable"])
    return {
        "schema": TOOL_CATALOG_PROJECTION_SCHEMA,
        "descriptor_schema": ToolDescriptorV2.SCHEMA_VERSION,
        "tool_count": len(ordered),
        "mutable_tool_count": len(mutable_rows),
        "sources": tuple(sorted({item["source"] for item in ordered})),
        "tools": mutable_rows,
        "descriptors": ordered,
        "raw_schema_visible": False,
        "tool_arguments_visible": False,
        "tool_results_visible": False,
        "secret_values_visible": False,
        "raw_content_visible": False,
    }


def _descriptor_projection_row(
    descriptor: ToolDescriptorV2,
    *,
    enabled: bool,
    runtime_availability: str,
    settings_mutable: bool,
) -> dict[str, Any]:
    row = descriptor.audit_summary()
    row.update(
        id=descriptor.tool_id,
        name=descriptor.display_name,
        desc=descriptor.description,
        display_name=descriptor.display_name,
        description=descriptor.description,
        enabled=enabled,
        runtime_availability=runtime_availability,
        settings_mutable=settings_mutable,
    )
    return row


def _dynamic_descriptor(
    *,
    tool_id: str,
    description: object,
    source: ToolSource,
    permission: ToolPermission,
    family: ToolFamily = ToolFamily.UNCLASSIFIED_DYNAMIC,
    lifecycle: ToolLifecycle = ToolLifecycle.CONTEXTUAL,
    availability: ToolAvailability = ToolAvailability.AVAILABLE,
) -> ToolDescriptorV2:
    source_label = "plugin" if source == ToolSource.PLUGIN else "MCP"
    return ToolDescriptorV2.create(
        tool_id=tool_id,
        analytics_id=_dynamic_analytics_id(tool_id),
        display_name=" ".join(part for part in re.split(r"[_:.-]+", tool_id) if part)[:80],
        description=_safe_dynamic_description(description, source_label=source_label),
        family=family,
        source=source,
        lifecycle=lifecycle,
        availability=availability,
        default_enabled=False,
        default_visibility=(
            ToolVisibility.BLOCKED
            if lifecycle == ToolLifecycle.BLOCKED
            or availability != ToolAvailability.AVAILABLE
            else ToolVisibility.REQUIRES_APPROVAL
        ),
        risk_level=ToolRiskLevel.ELEVATED,
        permission=permission,
        effect_class=ToolEffectClass.CONTROL,
        requires_confirmation=True,
        introduced_in="dynamic",
    )


def _mcp_catalog_metadata(
    tool: Mapping[str, Any],
) -> tuple[ToolFamily, ToolLifecycle, ToolAvailability, bool]:
    raw_family = tool.get("family")
    try:
        family = ToolFamily(str(raw_family or ToolFamily.PLUGINS_MCP.value).lower())
    except ValueError:
        family = ToolFamily.UNCLASSIFIED_DYNAMIC
    try:
        lifecycle = ToolLifecycle(
            str(tool.get("lifecycle") or ToolLifecycle.CONTEXTUAL.value).lower()
        )
    except ValueError:
        lifecycle = ToolLifecycle.BLOCKED
    try:
        availability = ToolAvailability(
            str(tool.get("availability") or ToolAvailability.AVAILABLE.value).lower()
        )
    except ValueError:
        availability = ToolAvailability.BLOCKED
    blocked = bool(tool.get("catalog_blocked")) or (
        family == ToolFamily.UNCLASSIFIED_DYNAMIC
        or lifecycle == ToolLifecycle.BLOCKED
        or availability in {
            ToolAvailability.BLOCKED,
            ToolAvailability.UNAVAILABLE,
            ToolAvailability.UNKNOWN,
        }
    )
    if blocked:
        family = ToolFamily.UNCLASSIFIED_DYNAMIC
        lifecycle = ToolLifecycle.BLOCKED
        availability = ToolAvailability.BLOCKED
    return family, lifecycle, availability, blocked


def _safe_dynamic_description(
    description: object,
    *,
    source_label: str,
    limit: int = 160,
) -> str:
    text = " ".join(str(description or "").split())
    if not text or _SECRET_RE.search(text) or "/" in text or "\\" in text or "://" in text:
        return f"Discovered {source_label} capability with conservative runtime policy."
    return text[:limit]


def _dynamic_analytics_id(tool_id: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", tool_id.lower()).strip("-")
    if normalized:
        return normalized
    return "dynamic-" + hashlib.sha256(tool_id.encode("utf-8")).hexdigest()[:12]


def build_runtime_tool_status(
    *,
    disabled_tools: Iterable[str] = (),
    builtin_descriptions: Mapping[str, str] | None = None,
    function_schemas: Iterable[Mapping[str, Any]] = (),
    plugin_tools: Iterable[Any] = (),
    mcp_tools: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build a compact live inventory without exposing raw schemas or secrets."""

    disabled = {str(item) for item in disabled_tools}
    builtin_descriptions = dict(builtin_descriptions or {})
    schemas = {_schema_name(schema): dict(schema) for schema in function_schemas if _schema_name(schema)}
    rows: list[dict[str, Any]] = []
    names = set(builtin_descriptions) | set(schemas)
    for name in sorted(names):
        rows.append(
            _tool_row(
                name,
                source="builtin",
                description=builtin_descriptions.get(name, ""),
                schema=schemas.get(name),
                disabled=disabled,
                description_registered=name in builtin_descriptions,
                schema_registered=name in schemas,
            )
        )
    seen = set(names)
    for tool in sorted(plugin_tools, key=lambda item: str(getattr(item, "name", ""))):
        name = str(getattr(tool, "name", "") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        rows.append(
            _tool_row(
                name,
                source="plugin",
                description=str(getattr(tool, "description", "") or ""),
                schema={"function": {"parameters": getattr(tool, "parameters", {}) or {}}},
                permission=str(getattr(tool, "permission", "") or "admin"),
                disabled=disabled,
                description_registered=True,
                schema_registered=True,
            )
        )
    for tool in sorted(mcp_tools, key=lambda item: str(item.get("qualified_name") or "")):
        name = str(tool.get("qualified_name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        rows.append(
            _tool_row(
                name,
                source="mcp",
                description=str(tool.get("description") or ""),
                schema={"parameters": tool.get("input_schema") or {}},
                permission="admin",
                disabled={name} if tool.get("is_disabled") else set(),
                description_registered=True,
                schema_registered=True,
            )
        )
    rows.sort(key=lambda item: item["tool_id"])
    drift_count = sum(1 for item in rows if item["drift_codes"])
    return {
        "schema": RUNTIME_TOOL_STATUS_SCHEMA,
        "tool_count": len(rows),
        "enabled_count": sum(1 for item in rows if item["availability"] == "enabled"),
        "disabled_count": sum(1 for item in rows if item["availability"] == "disabled"),
        "effectful_count": sum(1 for item in rows if item["side_effect_class"] != "read_only_or_planning"),
        "drift_count": drift_count,
        "sources": tuple(sorted({item["source"] for item in rows})),
        "tools": tuple(rows),
        "raw_schema_visible": False,
        "secret_values_visible": False,
        "raw_content_visible": False,
    }


def _tool_row(
    name: str,
    *,
    source: str,
    description: str,
    schema: Mapping[str, Any] | None,
    disabled: set[str],
    permission: str = "",
    description_registered: bool = False,
    schema_registered: bool = False,
) -> dict[str, Any]:
    params = _parameters(schema or {})
    side_effect_class = _side_effect_class(name)
    spec = builtin_spec(name) if source == "builtin" else None
    profile = runtime_tool_security_profile(
        name,
        dynamic_permission=permission if source != "builtin" else None,
    )
    drift_codes = _runtime_drift_codes(
        spec=spec,
        source=source,
        description_registered=description_registered,
        schema_registered=schema_registered,
    )
    gate_status = _gate_status(name, side_effect_class, disabled=disabled)
    if name not in disabled and spec is not None and spec.availability != ToolAvailability.AVAILABLE:
        gate_status = "blocked_by_catalog"
    elif (
        name not in disabled
        and profile.effect_class != ToolEffectClass.READ
        and gate_status == "available"
    ):
        gate_status = "evidence_or_confirmation_required"
    runtime_availability = (
        "disabled"
        if name in disabled
        else "blocked"
        if spec is not None and spec.availability != ToolAvailability.AVAILABLE
        else "enabled"
    )
    return {
        "tool_id": name,
        "source": source,
        "availability": runtime_availability,
        "permission": profile.permission.value,
        "risk_level": profile.risk_level.value,
        "effect_class": profile.effect_class.value,
        "requires_confirmation": profile.requires_confirmation,
        "policy_projection_source": profile.source,
        "lifecycle": spec.lifecycle.value if spec is not None else "contextual",
        "catalog_availability": spec.availability.value if spec is not None else "available",
        "registration_disposition": (
            spec.registration_disposition.value if spec is not None else "dynamic"
        ),
        "default_policy": spec.default_policy.value if spec is not None else "dynamic_conservative",
        "runtime_registered": spec.runtime_registered if spec is not None else True,
        "schema_registered": schema_registered,
        "description_registered": description_registered,
        "drift_codes": drift_codes,
        "side_effect_class": side_effect_class,
        "gate_status": gate_status,
        "schema_fingerprint": _schema_hash(params),
        "parameter_names": tuple(sorted(_properties(params))),
        "required_parameters": tuple(str(item) for item in params.get("required") or ()),
        "description_hash": _hash_text(_redact_description(description)),
        "raw_schema_visible": False,
        "secret_values_visible": False,
        "raw_content_visible": False,
    }


def _runtime_drift_codes(
    *,
    spec: Any,
    source: str,
    description_registered: bool,
    schema_registered: bool,
) -> tuple[str, ...]:
    if source != "builtin":
        return ()
    if spec is None:
        return ("runtime_or_schema_not_in_catalog",)
    codes: list[str] = []
    if not description_registered:
        codes.append("missing_description_projection")
    if spec.native_schema and not schema_registered:
        codes.append("missing_native_schema_projection")
    if not spec.runtime_registered:
        codes.append(f"catalog_{spec.registration_disposition.value}")
    return tuple(sorted(codes))


def _schema_name(schema: Mapping[str, Any]) -> str:
    function = schema.get("function") if isinstance(schema, Mapping) else None
    return str((function or {}).get("name") or schema.get("name") or "")


def _parameters(schema: Mapping[str, Any]) -> dict[str, Any]:
    function = schema.get("function") if isinstance(schema, Mapping) else None
    params = (function or {}).get("parameters") or schema.get("parameters") or {}
    return dict(params) if isinstance(params, Mapping) else {"type": "object", "properties": {}, "required": []}


def _properties(params: Mapping[str, Any]) -> tuple[str, ...]:
    properties = params.get("properties") if isinstance(params, Mapping) else None
    if not isinstance(properties, Mapping):
        return ()
    return tuple(str(key) for key in properties)


def _side_effect_class(name: str) -> str:
    category = tool_effect_category(name)
    if category:
        return category
    if name in _USER_GATE_TOOLS:
        return "user_or_plan_control"
    if name in _LIVE_NETWORK_TOOLS or name.startswith(("send_", "reply_to_", "download_", "serve_")):
        return "live_or_network"
    if name.startswith(_LOCAL_MUTATION_PREFIXES):
        return "stateful_or_filesystem_control"
    return "read_only_or_planning"


def _gate_status(name: str, side_effect_class: str, *, disabled: set[str]) -> str:
    if name in disabled:
        return "disabled_by_settings"
    if side_effect_class == "read_only_or_planning":
        return "available"
    if side_effect_class == "user_or_plan_control":
        return "turn_or_plan_gated"
    if side_effect_class in {"telegram_outbound", "git_remote_state", "live_or_network"}:
        return "operator_or_live_gate_required"
    return "evidence_or_confirmation_required"


def _redact_description(description: str) -> str:
    text = " ".join(str(description or "").split())
    if _SECRET_RE.search(text):
        return "[redacted]"
    return text[:300]


def _schema_hash(params: Mapping[str, Any]) -> str:
    payload = {
        "properties": sorted(_properties(params)),
        "required": tuple(str(item) for item in params.get("required") or ()),
        "type": params.get("type") or "object",
    }
    return _hash_text(json.dumps(payload, sort_keys=True))


def _hash_text(value: Any) -> str:
    return "sha256:" + hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:32]


if __name__ == "__main__":
    raise SystemExit(main())
