from __future__ import annotations

import copy
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import src.agent_loop_system_prompt as system_prompt
import src.runtime_tool_status as runtime_status
from src.agent_loop_prompts import (
    _MAINTENANCE_SAFETY_INVARIANTS,
    _assemble_prompt,
)
from src.runtime_tool_status import (
    AGENT_MAINTENANCE_BOOTSTRAP_SCHEMA,
    agent_maintenance_context_message,
    build_agent_maintenance_bootstrap,
    build_agent_maintenance_hook_output,
    collect_agent_maintenance_bootstrap,
    render_agent_maintenance_bootstrap,
)


BRANCH = "codex/amh07-synthetic"


def _roadmap() -> dict:
    return {
        "roadmap_id": "agent-maintenance-safety-harness",
        "goal_id": "agent-maintenance-safety-harness",
        "status": "running",
        "gate_queue": [],
    }


def _run_state(*, branch: str = BRANCH) -> dict:
    return {
        "revision": 7,
        "state": "running",
        "branch": branch,
        "route": {
            "slice_id": "AMH-07-read-only-session-bootstrap",
            "state": "running",
        },
        "active_claims": [
            {
                "claim_id": "amh07-bob",
                "slice_id": "AMH-07-read-only-session-bootstrap",
                "owner": "Bob",
                "state": "active",
            }
        ],
        "known_blockers": [],
        "pending_user_requests": [],
        "next_runnable_slices": ["AMH-07-read-only-session-bootstrap"],
    }


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        timeout=5,
    )


def _write_authorities(
    repo: Path,
    *,
    roadmap: object | None = None,
    run_state: object | None = None,
) -> None:
    plan_dir = repo / "docs" / "plans"
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "agent-maintenance-safety-harness-roadmap.json").write_text(
        json.dumps(_roadmap() if roadmap is None else roadmap, sort_keys=True),
        encoding="utf-8",
    )
    (plan_dir / "telegram-todo-domain-truth-run-state.json").write_text(
        json.dumps(_run_state() if run_state is None else run_state, sort_keys=True),
        encoding="utf-8",
    )


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", f"--initial-branch={BRANCH}")
    _git(repo, "config", "user.email", "amh07@example.invalid")
    _git(repo, "config", "user.name", "AMH07 Synthetic")
    _write_authorities(repo)
    _git(repo, "add", "docs/plans")
    _git(repo, "commit", "-m", "synthetic authority")
    return repo


def _pure_projection(**overrides: object) -> dict:
    values = {
        "roadmap": _roadmap(),
        "run_state": _run_state(),
        "branch_state": "named",
        "expected_branch_match": True,
        "dirty": False,
        "dirty_entry_count": 0,
        "dirty_count_capped": False,
    }
    values.update(overrides)
    return build_agent_maintenance_bootstrap(**values)


def _valid_hook_input(source: str = "startup") -> dict:
    return {
        "session_id": "synthetic-session",
        "cwd": "ignored-private-input",
        "hook_event_name": "SessionStart",
        "source": source,
        "model": "synthetic-model",
        "permission_mode": "default",
    }


def _assert_no_action_authority(projection: dict) -> None:
    authority = projection["authority"]
    assert authority["read_only"] is True
    assert authority["idempotent"] is True
    assert projection["read_only"] is True
    assert projection["idempotent"] is True
    for key in (
        "execution_authorized",
        "write_authorized",
        "commit_authorized",
        "push_authorized",
        "live_authorized",
    ):
        assert type(authority[key]) is bool
        assert authority[key] is False
        assert type(projection[key]) is bool
        assert projection[key] is False


def test_pure_projection_is_fixed_bounded_nonmutating_and_authority_free():
    roadmap = _roadmap()
    run_state = _run_state()
    roadmap_before = copy.deepcopy(roadmap)
    run_state_before = copy.deepcopy(run_state)

    first = build_agent_maintenance_bootstrap(
        roadmap=roadmap,
        run_state=run_state,
        branch_state="named",
        expected_branch_match=True,
        dirty=False,
        dirty_entry_count=0,
    )
    second = build_agent_maintenance_bootstrap(
        roadmap=roadmap,
        run_state=run_state,
        branch_state="named",
        expected_branch_match=True,
        dirty=False,
        dirty_entry_count=0,
    )

    assert roadmap == roadmap_before
    assert run_state == run_state_before
    assert first == second
    assert json.dumps(first, sort_keys=True, separators=(",", ":")) == json.dumps(
        second, sort_keys=True, separators=(",", ":")
    )
    assert first["schema"] == AGENT_MAINTENANCE_BOOTSTRAP_SCHEMA
    assert first["maintenance_status"] == "ready"
    assert first["handoff"]["status"] == "active"
    assert first["projection_digest"].startswith("sha256:")
    _assert_no_action_authority(first)
    assert first["raw_output_visible"] is False
    assert first["private_paths_visible"] is False
    assert first["raw_evidence_visible"] is False
    assert first["raw_reasons_visible"] is False


def test_canonical_deferred_debt_and_per_slice_gate_do_not_block_active_claim():
    roadmap = _roadmap()
    roadmap["gate_queue"] = [
        {
            "gate_id": "shared-hotfile-handoff",
            "state": "deferred",
            "disposition": "per_slice_gate",
            "original_state": "pending_per_slice",
        }
    ]
    run_state = _run_state()
    run_state["known_blockers"] = [
        {
            "id": "legacy-consumer-migration",
            "state": "deferred",
            "disposition": "nonblocking_debt",
            "original_state": "open_out_of_scope_migration_debt_nonblocking_amh07",
        }
    ]
    roadmap_before = copy.deepcopy(roadmap)
    run_state_before = copy.deepcopy(run_state)

    projection = build_agent_maintenance_bootstrap(
        roadmap=roadmap,
        run_state=run_state,
        branch_state="named",
        expected_branch_match=True,
        dirty=False,
        dirty_entry_count=0,
    )

    assert projection["maintenance_status"] == "ready"
    assert projection["warning_code"] == "none"
    assert projection["handoff"]["status"] == "active"
    assert projection["handoff"]["next_action"] == "continue_claim"
    assert projection["handoff"]["blockers"] == []
    assert projection["handoff"]["conflicts"] == []
    assert projection["handoff"]["active_claim_count"] == 1
    _assert_no_action_authority(projection)

    assert roadmap == roadmap_before
    assert run_state == run_state_before
    assert roadmap["gate_queue"][0]["disposition"] == "per_slice_gate"
    assert roadmap["gate_queue"][0]["original_state"] == "pending_per_slice"
    assert run_state["known_blockers"][0]["disposition"] == "nonblocking_debt"
    assert (
        run_state["known_blockers"][0]["original_state"]
        == "open_out_of_scope_migration_debt_nonblocking_amh07"
    )


def test_clean_dirty_and_detached_synthetic_repositories(tmp_path: Path):
    repo = _repo(tmp_path)
    clean = collect_agent_maintenance_bootstrap(repo_root=repo)
    assert clean["maintenance_status"] == "ready"
    assert clean["warning_code"] == "none"
    assert clean["repository"] == {
        "branch_state": "named",
        "expected_branch_match": True,
        "dirty": False,
        "dirty_entry_count": 0,
        "dirty_count_capped": False,
    }

    (repo / "untracked-secret-name-canary.txt").write_text("not inspected", encoding="utf-8")
    dirty = collect_agent_maintenance_bootstrap(repo_root=repo)
    assert dirty["maintenance_status"] == "ready"
    assert dirty["warning_code"] == "dirty_worktree"
    assert dirty["repository"]["dirty"] is True
    assert dirty["repository"]["dirty_entry_count"] == 1
    assert "untracked-secret-name-canary" not in repr(dirty)

    _git(repo, "checkout", "--detach")
    detached = collect_agent_maintenance_bootstrap(repo_root=repo)
    assert detached["maintenance_status"] == "stop"
    assert detached["warning_code"] == "detached_head"
    assert detached["repository"]["branch_state"] == "detached"
    assert detached["repository"]["expected_branch_match"] is False
    assert BRANCH not in repr(detached)


def test_branch_mismatch_projects_only_boolean_not_names(tmp_path: Path):
    repo = _repo(tmp_path)
    state = _run_state(branch="another/private/branch-name")
    _write_authorities(repo, run_state=state)
    projection = collect_agent_maintenance_bootstrap(repo_root=repo)
    assert projection["maintenance_status"] == "stop"
    assert projection["warning_code"] == "branch_mismatch"
    assert projection["repository"]["branch_state"] == "named"
    assert projection["repository"]["expected_branch_match"] is False
    assert "another/private/branch-name" not in repr(projection)
    assert BRANCH not in repr(projection)


@pytest.mark.parametrize(
    ("mutation", "expected_warning"),
    [
        (lambda state: state.update(stale=True), "authority_conflict"),
        (
            lambda state: state["route"].update(slice_id="AMH-08-other"),
            "authority_conflict",
        ),
        (
            lambda state: state["active_claims"].append(
                {
                    "claim_id": "second",
                    "slice_id": "AMH-07-read-only-session-bootstrap",
                    "owner": "Alice",
                    "state": "active",
                }
            ),
            "authority_conflict",
        ),
    ],
)
def test_stale_conflicting_and_multiple_claims_fail_closed(
    tmp_path: Path, mutation, expected_warning: str
):
    repo = _repo(tmp_path)
    state = _run_state()
    mutation(state)
    _write_authorities(repo, run_state=state)
    projection = collect_agent_maintenance_bootstrap(repo_root=repo)
    assert projection["maintenance_status"] == "stop"
    assert projection["warning_code"] == expected_warning
    _assert_no_action_authority(projection)


@pytest.mark.parametrize("payload", ["{", "[]", "null", '{"x":NaN}', '{"x":1,"x":2}'])
def test_malformed_non_object_and_ambiguous_json_fail_closed(
    tmp_path: Path, payload: str
):
    repo = _repo(tmp_path)
    state_path = repo / runtime_status._MAINTENANCE_RUN_STATE_PATH
    state_path.write_text(payload, encoding="utf-8")
    projection = collect_agent_maintenance_bootstrap(repo_root=repo)
    assert projection["maintenance_status"] == "stop"
    assert projection["warning_code"] == "state_malformed"
    _assert_no_action_authority(projection)


def test_missing_and_oversized_state_fail_closed(tmp_path: Path):
    missing_repo = tmp_path / "missing"
    missing_repo.mkdir()
    missing = collect_agent_maintenance_bootstrap(repo_root=missing_repo)
    assert missing["warning_code"] == "state_unavailable"
    assert missing["maintenance_status"] == "stop"

    repo = _repo(tmp_path / "oversized-parent")
    state_path = repo / runtime_status._MAINTENANCE_RUN_STATE_PATH
    state_path.write_bytes(b" " * (runtime_status._MAINTENANCE_STATE_MAX_BYTES + 1))
    oversized = collect_agent_maintenance_bootstrap(repo_root=repo)
    assert oversized["warning_code"] == "state_oversized"
    assert oversized["maintenance_status"] == "stop"


def test_state_symlink_is_rejected_when_supported(tmp_path: Path):
    repo = _repo(tmp_path)
    state_path = repo / runtime_status._MAINTENANCE_RUN_STATE_PATH
    target = state_path.with_name("target.json")
    target.write_text(json.dumps(_run_state()), encoding="utf-8")
    state_path.unlink()
    try:
        state_path.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable on this host")
    projection = collect_agent_maintenance_bootstrap(repo_root=repo)
    assert projection["maintenance_status"] == "stop"
    assert projection["warning_code"] == "state_symlink_rejected"


def test_hostile_python_objects_fail_closed_without_invocation():
    class HostileMapping(dict):
        def get(self, *_args, **_kwargs):
            raise AssertionError("hostile object was invoked")

    hostile = HostileMapping(_roadmap())
    projection = build_agent_maintenance_bootstrap(
        roadmap=hostile,
        run_state=_run_state(),
        branch_state="named",
        expected_branch_match=True,
        dirty=False,
        dirty_entry_count=0,
    )
    assert projection["maintenance_status"] == "stop"
    assert projection["warning_code"] == "state_authority_invalid"
    _assert_no_action_authority(projection)

    nested = _roadmap()
    nested["hostile"] = object()
    projection = _pure_projection(roadmap=nested)
    assert projection["warning_code"] == "state_authority_invalid"


def test_git_timeout_nonzero_and_raw_canaries_never_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo = _repo(tmp_path)
    canary = b"Bearer-secret-CANARY C:\\private\\owner\\file.txt"

    def timeout_read(_root: Path, operation: str) -> dict:
        if operation == "branch":
            return runtime_status._git_read_result(
                returncode=-1, stdout=canary, timed_out=True
            )
        return runtime_status._git_read_result(returncode=0)

    monkeypatch.setattr(runtime_status, "_run_fixed_git_read", timeout_read)
    timed_out = collect_agent_maintenance_bootstrap(repo_root=repo)
    assert timed_out["warning_code"] == "git_read_timeout"
    assert "CANARY" not in repr(timed_out)
    assert "C:\\private\\owner\\file.txt" not in repr(timed_out)

    def nonzero_read(_root: Path, operation: str) -> dict:
        if operation == "branch":
            return runtime_status._git_read_result(returncode=0, stdout=(BRANCH + "\n").encode())
        return runtime_status._git_read_result(returncode=7, stdout=canary)

    monkeypatch.setattr(runtime_status, "_run_fixed_git_read", nonzero_read)
    nonzero = collect_agent_maintenance_bootstrap(repo_root=repo)
    assert nonzero["warning_code"] == "git_read_failed"
    assert "CANARY" not in repr(nonzero)

    def canary_read(_root: Path, operation: str) -> dict:
        if operation == "branch":
            return runtime_status._git_read_result(returncode=0, stdout=(BRANCH + "\n").encode())
        return runtime_status._git_read_result(returncode=0, stdout=b"?? " + canary + b"\n")

    monkeypatch.setattr(runtime_status, "_run_fixed_git_read", canary_read)
    redacted = collect_agent_maintenance_bootstrap(repo_root=repo)
    assert redacted["repository"]["dirty"] is True
    assert redacted["repository"]["dirty_entry_count"] == 1
    assert "CANARY" not in repr(redacted)
    assert "Bearer" not in repr(redacted)


def test_git_runner_uses_only_fixed_reads_shell_false_and_bounded_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls = []

    class FakeProcess:
        def __init__(self, stdout: bytes):
            self.stdout = io.BytesIO(stdout)
            self.stderr = io.BytesIO(b"")
            self.timeouts = []

        def wait(self, timeout=None):
            self.timeouts.append(timeout)
            return 0

        def kill(self):
            raise AssertionError("clean fake process must not be killed")

    processes = []

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        stdout = b"synthetic\n" if "rev-parse" in command else b""
        process = FakeProcess(stdout)
        processes.append(process)
        return process

    monkeypatch.setattr(runtime_status.subprocess, "Popen", fake_popen)
    branch = runtime_status._run_fixed_git_read(tmp_path, "branch")
    status = runtime_status._run_fixed_git_read(tmp_path, "status")
    invalid = runtime_status._run_fixed_git_read(tmp_path, "fetch")

    assert branch["returncode"] == status["returncode"] == 0
    assert invalid["returncode"] == -1
    assert len(calls) == 2
    assert tuple(calls[0][0]) == runtime_status._MAINTENANCE_GIT_READS["branch"]
    assert tuple(calls[1][0]) == runtime_status._MAINTENANCE_GIT_READS["status"]
    assert all(call[1]["shell"] is False for call in calls)
    assert all(process.timeouts == [runtime_status._MAINTENANCE_GIT_TIMEOUT_SECONDS] for process in processes)
    assert runtime_status._MAINTENANCE_GIT_TIMEOUT_SECONDS <= 2


def test_git_environment_strips_inherited_redirection_and_trace_canaries(
    monkeypatch: pytest.MonkeyPatch,
):
    canary = "C:/private/git-redirection-CANARY"
    inherited = {
        "GIT_DIR": canary,
        "GIT_WORK_TREE": canary,
        "GIT_INDEX_FILE": canary,
        "GIT_COMMON_DIR": canary,
        "GIT_OBJECT_DIRECTORY": canary,
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": canary,
        "GIT_CONFIG_PARAMETERS": canary,
        "GIT_TRACE": canary,
        "GIT_TRACE2": canary,
        "GIT_TRACE2_EVENT": canary,
        "GIT_SSH_COMMAND": canary,
    }
    for key, value in inherited.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "safe.directory")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "C:/synthetic-safe-repo")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "core.worktree")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", canary)

    environment = runtime_status._maintenance_git_environment()

    assert canary not in repr(environment)
    assert environment["GIT_CONFIG_COUNT"] == "1"
    assert environment["GIT_CONFIG_KEY_0"] == "safe.directory"
    assert environment["GIT_CONFIG_VALUE_0"] == "C:/synthetic-safe-repo"
    assert environment["GIT_OPTIONAL_LOCKS"] == "0"
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["GIT_PAGER"] == "cat"
    allowed = {
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
        "GIT_OPTIONAL_LOCKS",
        "GIT_TERMINAL_PROMPT",
        "GIT_PAGER",
    }
    assert {
        key for key in environment if key.upper().startswith("GIT_")
    } == allowed


def test_hook_and_internal_context_have_identical_projection_and_digest(tmp_path: Path):
    repo = _repo(tmp_path)
    projection = collect_agent_maintenance_bootstrap(repo_root=repo)
    rendered = render_agent_maintenance_bootstrap(projection)
    internal = agent_maintenance_context_message(repo_root=repo)
    hook = build_agent_maintenance_hook_output(_valid_hook_input(), repo_root=repo)

    assert internal["content"] == rendered == hook["systemMessage"]
    assert internal["metadata"]["projection_digest"] == projection["projection_digest"]
    assert internal["metadata"]["action_authority"] is False
    assert internal["_protected"] is True
    assert hook == {
        "continue": True,
        "systemMessage": rendered,
        "suppressOutput": False,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": rendered,
        },
    }
    assert "execution=false" in rendered
    assert "ordinary_non_maintenance_product_work: may_continue" in rendered


def test_internal_default_root_is_stable_when_process_cwd_is_outside_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo = _repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(runtime_status, "_MAINTENANCE_DEFAULT_REPO_ROOT", repo)
    monkeypatch.chdir(outside)

    default_projection = collect_agent_maintenance_bootstrap()
    explicit_projection = collect_agent_maintenance_bootstrap(repo_root=repo)
    internal = agent_maintenance_context_message()
    hook = build_agent_maintenance_hook_output(
        _valid_hook_input(),
        repo_root=repo,
    )

    assert default_projection == explicit_projection
    assert internal["content"] == hook["hookSpecificOutput"]["additionalContext"]
    assert hook["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "state_unavailable" not in internal["content"]


@pytest.mark.parametrize("source", ["startup", "resume", "clear", "compact"])
def test_session_start_sources_are_supported(
    tmp_path: Path, source: str
):
    repo = _repo(tmp_path)
    output = build_agent_maintenance_hook_output(
        _valid_hook_input(source), repo_root=repo
    )
    assert output["continue"] is True
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert output["hookSpecificOutput"]["additionalContext"] == output["systemMessage"]
    assert AGENT_MAINTENANCE_BOOTSTRAP_SCHEMA.split(".")[-2] in output["systemMessage"] or "maintenance bootstrap" in output["systemMessage"]
    assert "execution=false" in output["systemMessage"]


def test_subagent_start_parity_is_supported(tmp_path: Path):
    repo = _repo(tmp_path)
    output = build_agent_maintenance_hook_output(
        {
            "hook_event_name": "SubagentStart",
            "agent_type": "worker",
            "cwd": "ignored-private-input",
        },
        repo_root=repo,
    )
    assert output["continue"] is True
    assert output["hookSpecificOutput"]["hookEventName"] == "SubagentStart"
    assert output["hookSpecificOutput"]["additionalContext"] == output["systemMessage"]
    assert "projection_digest: sha256:" in output["systemMessage"]
    assert "ignored-private-input" not in repr(output)


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {"hook_event_name": "SessionStart", "source": "unknown"},
        {"hook_event_name": "PreToolUse", "source": "startup"},
        {"hook_event_name": "SessionStart", "source": object()},
    ],
)
def test_unsupported_and_hostile_hook_inputs_fail_closed_without_authority(value):
    output = build_agent_maintenance_hook_output(value)
    assert output["continue"] is True
    assert "hookSpecificOutput" not in output
    assert "maintenance_status: stop" in output["systemMessage"]
    assert "execution=false" in output["systemMessage"]
    assert "commit=false" in output["systemMessage"]
    assert "live=false" in output["systemMessage"]


def test_hook_collector_failure_is_fixed_and_does_not_block_ordinary_work(
    monkeypatch: pytest.MonkeyPatch,
):
    def fail(**_kwargs):
        raise RuntimeError("private exception text CANARY")

    monkeypatch.setattr(runtime_status, "collect_agent_maintenance_bootstrap", fail)
    output = build_agent_maintenance_hook_output(_valid_hook_input())
    assert output["continue"] is True
    assert "bootstrap_unavailable" in output["systemMessage"]
    assert output["hookSpecificOutput"] == {
        "hookEventName": "SessionStart",
        "additionalContext": output["systemMessage"],
    }
    assert "ordinary_non_maintenance_product_work: may_continue" in output["systemMessage"]
    assert "CANARY" not in repr(output)


def test_cli_malformed_and_oversized_input_emit_official_fixed_hook_json(tmp_path: Path):
    repo = _repo(tmp_path)
    project_root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(project_root)

    malformed = subprocess.run(
        [sys.executable, "-B", "-m", "src.runtime_tool_status"],
        cwd=repo,
        input=b"{",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        timeout=8,
        env=environment,
        check=False,
    )
    assert malformed.returncode == 0
    assert malformed.stderr == b""
    malformed_output = json.loads(malformed.stdout)
    assert set(malformed_output) == {
        "continue",
        "systemMessage",
        "suppressOutput",
    }
    assert "hook_input_malformed" in malformed_output["systemMessage"]
    assert "execution=false" in malformed_output["systemMessage"]

    oversized = subprocess.run(
        [sys.executable, "-B", "-m", "src.runtime_tool_status"],
        cwd=repo,
        input=b" " * (runtime_status._MAINTENANCE_HOOK_INPUT_MAX_BYTES + 1),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        timeout=8,
        env=environment,
        check=False,
    )
    oversized_output = json.loads(oversized.stdout)
    assert oversized.returncode == 0
    assert oversized.stderr == b""
    assert "hook_input_oversized" in oversized_output["systemMessage"]
    assert "write=false" in oversized_output["systemMessage"]


def test_cli_valid_output_is_byte_identical_for_stable_repo(tmp_path: Path):
    repo = _repo(tmp_path)
    project_root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(project_root)
    payload = json.dumps(_valid_hook_input(), sort_keys=True).encode()

    outputs = []
    for _ in range(2):
        completed = subprocess.run(
            [sys.executable, "-B", "-m", "src.runtime_tool_status"],
            cwd=repo,
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            timeout=8,
            env=environment,
            check=False,
        )
        assert completed.returncode == 0
        assert completed.stderr == b""
        outputs.append(completed.stdout)
    assert outputs[0] == outputs[1]
    output = json.loads(outputs[0])
    assert output["continue"] is True
    assert output["hookSpecificOutput"] == {
        "hookEventName": "SessionStart",
        "additionalContext": output["systemMessage"],
    }


def test_project_hooks_preserve_impeccable_handler_and_use_short_read_only_commands():
    config = json.loads((Path(".codex") / "hooks.json").read_text(encoding="utf-8"))
    impeccable = config["hooks"]["PostToolUse"]
    assert impeccable == [
        {
            "matcher": "Edit|Write|apply_patch",
            "hooks": [
                {
                    "type": "command",
                    "command": 'node "$(git rev-parse --show-toplevel)/.agents/skills/impeccable/scripts/hook.mjs"',
                    "timeout": 5,
                    "statusMessage": "Checking UI changes",
                }
            ],
        }
    ]

    for event in ("SessionStart", "SubagentStart"):
        handlers = config["hooks"][event][0]["hooks"]
        assert len(handlers) == 1
        handler = handlers[0]
        assert handler["type"] == "command"
        assert handler["timeout"] <= 5
        for field in ("command", "commandWindows"):
            command = handler[field]
            assert "rev-parse --show-toplevel" in command
            assert "python -B -m src.runtime_tool_status" in command
            assert "safe.directory=*" in command
            for git_variable in (
                "GIT_DIR",
                "GIT_WORK_TREE",
                "GIT_INDEX_FILE",
                "GIT_CONFIG_COUNT",
                "GIT_CONFIG_PARAMETERS",
                "GIT_TRACE",
                "GIT_TRACE2",
                "GIT_TRACE2_EVENT",
                "GIT_TRACE2_PERF",
                "GIT_TRACE_REDACT",
                "GIT_TRACE_SETUP",
                "GIT_TRACE_PERFORMANCE",
                "GIT_TRACE_PACKET",
                "GIT_TRACE_CURL",
                "GIT_TRACE_CURL_NO_DATA",
                "GIT_FLUSH",
            ):
                assert git_variable in command
            assert ">" not in command
            lowered = command.lower()
            for forbidden in (
                "pip install",
                "npm install",
                "start-process",
                "systemctl",
                "docker ",
                "podman ",
            ):
                assert forbidden not in lowered
            assert re.search(r"(?<![a-z0-9_])(curl|wget)(?:\.exe)?\s", lowered) is None
    assert config["hooks"]["SessionStart"][0]["matcher"] == "startup|resume|clear|compact"


def test_compact_and_full_prompts_receive_same_fixed_safety_invariants():
    compact = _assemble_prompt({"ask_user", "manage_todos"}, compact=True)
    full = _assemble_prompt({"ask_user", "manage_todos"}, compact=False)
    for prompt in (compact, full):
        assert prompt.count(_MAINTENANCE_SAFETY_INVARIANTS) == 1
        assert "never action authority" in prompt
        assert "ordinary non-maintenance product work may continue" in prompt
        assert "manage_todos" in prompt
    assert "odysseus.clarification_request.v2" in full


def test_internal_prompt_injects_fresh_uncached_context_per_request(
    monkeypatch: pytest.MonkeyPatch,
):
    counter = {"value": 0}

    def fresh_context():
        counter["value"] += 1
        return {
            "role": "user",
            "content": f"fresh-maintenance-{counter['value']} execution=false",
            "metadata": {
                "source": "agent_maintenance_bootstrap",
                "trusted": True,
                "action_authority": False,
            },
            "_protected": True,
        }

    monkeypatch.setattr(system_prompt, "agent_maintenance_context_message", fresh_context)
    monkeypatch.setattr(system_prompt, "runtime_snapshot_context_message", lambda: None)
    monkeypatch.setattr(system_prompt, "set_active_model", lambda _model: None)
    monkeypatch.setattr(
        system_prompt,
        "_build_base_prompt",
        lambda *_args, **_kwargs: ("base-system-prompt", ""),
    )
    import src.user_time as user_time

    monkeypatch.setattr(user_time, "current_datetime_context_message", lambda: None)
    system_prompt._cached_base_prompt = None
    system_prompt._cached_base_prompt_key = None

    first, _ = system_prompt._build_system_prompt(
        [{"role": "user", "content": "ordinary request"}],
        "model",
        None,
        None,
    )
    second, _ = system_prompt._build_system_prompt(
        [{"role": "user", "content": "ordinary request"}],
        "model",
        None,
        None,
    )
    assert counter["value"] == 2
    assert any(message.get("content", "").startswith("fresh-maintenance-1") for message in first)
    assert any(message.get("content", "").startswith("fresh-maintenance-2") for message in second)
    assert any(message.get("content") == "ordinary request" for message in first)
    assert any(message.get("content") == "ordinary request" for message in second)


def test_internal_prompt_collector_failure_keeps_ordinary_product_request(
    monkeypatch: pytest.MonkeyPatch,
):
    def unavailable():
        raise RuntimeError("private CANARY")

    monkeypatch.setattr(system_prompt, "agent_maintenance_context_message", unavailable)
    monkeypatch.setattr(system_prompt, "runtime_snapshot_context_message", lambda: None)
    monkeypatch.setattr(system_prompt, "set_active_model", lambda _model: None)
    monkeypatch.setattr(
        system_prompt,
        "_build_base_prompt",
        lambda *_args, **_kwargs: ("base-system-prompt", ""),
    )
    import src.user_time as user_time

    monkeypatch.setattr(user_time, "current_datetime_context_message", lambda: None)
    messages, _ = system_prompt._build_system_prompt(
        [{"role": "user", "content": "ordinary request remains"}],
        "model",
        None,
        None,
    )
    assert any(message.get("content") == "ordinary request remains" for message in messages)
    assert "CANARY" not in repr(messages)


def test_bootstrap_source_has_no_write_network_install_or_service_operations():
    source = Path(runtime_status.__file__).read_text(encoding="utf-8")
    assert "requests." not in source
    assert "urllib" not in source
    assert "socket" not in source
    assert "pip install" not in source
    assert "npm install" not in source
    assert "Start-Process" not in source
    assert "systemctl" not in source
    assert "write_text(" not in source
    assert "write_bytes(" not in source
    assert "open(\"w" not in source
    assert "shell=False" in source
