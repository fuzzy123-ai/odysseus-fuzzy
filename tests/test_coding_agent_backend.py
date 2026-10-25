import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.coding_agent_routes import setup_coding_agent_routes
from src import agent_task_ledger
from src.coding_agent_backend import (
    CodingCheckCommand,
    CodingCommandResult,
    CodingPatchOperation,
    apply_coding_patch_transaction,
    apply_coding_patch_set,
    build_coding_publish_plan,
    build_coding_subagent_plan,
    build_coding_task_plan,
    build_coding_worktree_transaction_receipt,
    build_transactional_coding_handoff_plan,
    capture_coding_exact_read_receipt,
    create_coding_worktree,
    create_transactional_coding_worktree,
    evaluate_coding_done_gate,
    evaluate_coding_quality_gate,
    evaluate_coding_worktree_gate,
)
from src.repo_registry import RepoRecord, RepoRegistry


def _repo_root(tmp_path: Path) -> Path:
    repo = tmp_path / "repos" / "demo"
    (repo / ".git").mkdir(parents=True)
    return repo


def _registry(path: Path | None = None, *, branch_allowed: bool = True) -> RepoRegistry:
    registry = RepoRegistry()
    registry.add(
        RepoRecord.create(
            repo_id="demo",
            title="Demo Repo",
            repo_kind="project",
            owner="fuzzy123-ai",
            path_ref="repos/demo",
            workspace_root="repos",
            project_root="repos/demo",
            default_branch="main",
            allowed_actions=[
                "status",
                "log",
                "diff_stat",
                "changed_paths",
                *([] if not branch_allowed else ["branch"]),
            ],
            created_at="2026-06-29T10:00:00Z",
        )
    )
    if path is not None:
        registry.save_json(path)
    return registry


def _check() -> CodingCheckCommand:
    return CodingCheckCommand.create(argv=["python", "-m", "pytest", "tests/test_demo.py", "-q"])


def test_coding_task_plan_is_hold_until_operator_and_live_gates_are_open(tmp_path: Path):
    _repo_root(tmp_path)
    plan = build_coding_task_plan(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Add a route",
        checks=[_check()],
        worktree_base=tmp_path / "worktrees",
    )

    assert plan.decision == "hold"
    assert plan.can_create_worktree is False
    assert "operator decision is not go" in plan.blockers
    assert "ODYSSEUS_CODING_AGENT_LIVE_ENABLED is not enabled" in plan.blockers


def test_coding_task_plan_ready_with_branch_permission_checks_and_go(tmp_path: Path):
    _repo_root(tmp_path)
    plan = build_coding_task_plan(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Add a focused backend route",
        allowed_paths=["src", "tests"],
        checks=[_check()],
        task_id="backend-route",
        worktree_base=tmp_path / "worktrees",
        live_enabled=True,
        operator_decision="go",
    )

    assert plan.decision == "plan_ready"
    assert plan.can_create_worktree is True
    assert plan.worktree_ref == "coding-worktrees/demo/backend-route"
    assert plan.allowed_paths == ("src", "tests")
    assert str(tmp_path) not in json.dumps(plan.to_dict())


def test_coding_task_plan_blocks_until_clarification_ready_for_plan(tmp_path: Path):
    _repo_root(tmp_path)
    plan = build_coding_task_plan(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Add a focused backend route",
        allowed_paths=["src", "tests"],
        checks=[_check()],
        task_id="backend-route",
        worktree_base=tmp_path / "worktrees",
        live_enabled=True,
        operator_decision="go",
        clarification_ready_for_plan=False,
        clarification_id="clar-12345678",
    )

    assert plan.decision == "hold"
    assert plan.can_create_worktree is False
    assert "clarification must be ready_for_plan before coding plan execution (clar-12345678)" in plan.blockers


def test_coding_task_plan_requires_repo_branch_action(tmp_path: Path):
    _repo_root(tmp_path)
    plan = build_coding_task_plan(
        registry=_registry(branch_allowed=False),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Add a focused backend route",
        checks=[_check()],
        worktree_base=tmp_path / "worktrees",
        live_enabled=True,
        operator_decision="go",
    )

    assert plan.decision == "hold"
    assert "repo registry does not allow branch/worktree actions" in plan.blockers


def test_create_coding_worktree_uses_gate_and_internal_git_sequence(tmp_path: Path):
    _repo_root(tmp_path)
    calls: list[tuple[str, ...]] = []

    def fake_runner(argv, *, cwd, timeout_seconds, env):
        calls.append(tuple(argv))
        if argv == ("git", "status", "--porcelain"):
            return CodingCommandResult(exit_code=0, stdout="")
        if argv[:4] == ("git", "worktree", "add", "--detach"):
            return CodingCommandResult(exit_code=0, stdout="prepared")
        raise AssertionError(f"unexpected command: {argv!r}")

    report = create_coding_worktree(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Add a focused backend route",
        checks=[_check()],
        task_id="backend-route",
        worktree_base=tmp_path / "worktrees",
        live_enabled=True,
        operator_decision="go",
        command_runner=fake_runner,
    )

    assert report.status == "created"
    assert report.executed is True
    assert calls[0] == ("git", "status", "--porcelain")
    assert calls[1][:4] == ("git", "worktree", "add", "--detach")
    assert calls[1][-1] == "main"
    assert calls[2] == ("git", "status", "--porcelain")


def test_create_coding_worktree_blocks_dirty_source_repo(tmp_path: Path):
    _repo_root(tmp_path)

    def fake_runner(argv, *, cwd, timeout_seconds, env):
        return CodingCommandResult(exit_code=0, stdout=" M README.md\n")

    report = create_coding_worktree(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Add a focused backend route",
        checks=[_check()],
        task_id="backend-route",
        worktree_base=tmp_path / "worktrees",
        live_enabled=True,
        operator_decision="go",
        command_runner=fake_runner,
    )

    assert report.status == "blocked"
    assert "source repo has uncommitted changes" in report.blockers[0]


def test_transactional_worktree_creation_records_clean_revision_and_rollback_boundary(tmp_path: Path):
    repo = _repo_root(tmp_path)

    def fake_runner(argv, *, cwd, timeout_seconds, env):
        if argv == ("git", "status", "--porcelain"):
            return CodingCommandResult(exit_code=0, stdout="")
        if argv[:4] == ("git", "worktree", "add", "--detach"):
            (Path(argv[4]) / ".git").mkdir(parents=True)
            return CodingCommandResult(exit_code=0, stdout="prepared")
        if argv == ("git", "rev-parse", "HEAD"):
            return CodingCommandResult(exit_code=0, stdout="a" * 40 + "\n")
        raise AssertionError(f"unexpected command: {argv!r} in {cwd!s}")

    report = create_transactional_coding_worktree(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Add a transactional edit",
        implementer_id="bob",
        allowed_paths=["src"],
        checks=[_check()],
        task_id="transactional-edit",
        worktree_base=tmp_path / "worktrees",
        live_enabled=True,
        operator_decision="go",
        command_runner=fake_runner,
    )

    assert repo.exists()
    assert report.ready is True
    assert report.transaction is not None
    assert report.transaction.base_revision == "a" * 40
    assert report.transaction.to_dict()["rollback_ready"] is True
    assert report.transaction.to_dict()["raw_content_visible"] is False


def test_patch_set_applies_exact_replacement_inside_existing_worktree(tmp_path: Path):
    _repo_root(tmp_path)
    worktree_file = tmp_path / "worktrees" / "demo" / "backend-route" / "src" / "app.py"
    worktree_file.parent.mkdir(parents=True)
    worktree_file.write_text("def title():\n    return 'old'\n", encoding="utf-8")
    plan = build_coding_task_plan(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Update title helper",
        allowed_paths=["src"],
        checks=[_check()],
        task_id="backend-route",
        worktree_base=tmp_path / "worktrees",
        allow_existing_worktree=True,
        live_enabled=True,
        operator_decision="go",
    )

    results = apply_coding_patch_set(
        plan=plan,
        patches=[
            CodingPatchOperation.create(
                path="src/app.py",
                find="return 'old'",
                replace="return 'new'",
            )
        ],
        worktree_base=tmp_path / "worktrees",
        live_enabled=True,
        operator_decision="go",
    )

    assert len(results) == 1
    assert results[0].ok is True
    assert "return 'new'" in worktree_file.read_text(encoding="utf-8")
    assert "--- a/src/app.py" in results[0].diff


def test_transactional_patch_binds_exact_read_diff_rollback_and_independent_review(tmp_path: Path):
    _repo_root(tmp_path)
    worktree_file = tmp_path / "worktrees" / "demo" / "backend-route" / "src" / "app.py"
    worktree_file.parent.mkdir(parents=True)
    worktree_file.write_bytes(b"VALUE = 'old'\n")
    plan = build_coding_task_plan(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Update transaction helper",
        allowed_paths=["src"],
        checks=[_check()],
        task_id="backend-route",
        worktree_base=tmp_path / "worktrees",
        allow_existing_worktree=True,
        live_enabled=True,
        operator_decision="go",
    )
    transaction = build_coding_worktree_transaction_receipt(
        plan=plan,
        base_revision="a" * 40,
        source_status="",
        implementer_id="bob",
    )
    read = capture_coding_exact_read_receipt(
        plan=plan,
        transaction=transaction,
        repo_path="src/app.py",
        reader_id="bob",
        worktree_base=tmp_path / "worktrees",
    )
    patch = CodingPatchOperation.create(path="src/app.py", find="'old'", replace="'new'")
    result = apply_coding_patch_transaction(
        plan=plan,
        transaction=transaction,
        patches=[patch],
        exact_reads=[read],
        worktree_base=tmp_path / "worktrees",
        live_enabled=True,
        operator_decision="go",
    )
    quality = evaluate_coding_quality_gate(
        changed_paths=result.changed_paths,
        allowed_paths=plan.allowed_paths,
        check_results=[CodingCommandResult(exit_code=0)],
    )
    self_review = evaluate_coding_done_gate(
        quality_gate=quality,
        review_decision="approved",
        reviewed_by="bob",
        implemented_by="bob",
        content_reviewed=True,
    )
    done = evaluate_coding_done_gate(
        quality_gate=quality,
        review_decision="approved",
        reviewed_by="charlie",
        implemented_by="bob",
        content_reviewed=True,
    )
    handoff = build_transactional_coding_handoff_plan(
        plan=plan,
        transaction=transaction,
        patch_report=result,
        done_gate=done,
    )

    assert result.applied is True
    assert result.diff_digest.startswith("sha256:")
    assert result.rollback_ready is True
    assert "VALUE = 'new'" in worktree_file.read_text(encoding="utf-8")
    assert worktree_file.read_bytes() == b"VALUE = 'new'\n"
    assert self_review.done is False
    assert "reviewer identity must differ from implementer" in self_review.blockers
    assert handoff.decision == "plan_ready"


def test_transactional_patch_rejects_a_stale_exact_read_without_writing(tmp_path: Path):
    _repo_root(tmp_path)
    worktree_file = tmp_path / "worktrees" / "demo" / "backend-route" / "src" / "app.py"
    worktree_file.parent.mkdir(parents=True)
    worktree_file.write_text("VALUE = 'old'\n", encoding="utf-8")
    plan = build_coding_task_plan(
        registry=_registry(), repo_id="demo", workspace_base=tmp_path,
        objective="Keep exact reads fresh", allowed_paths=["src"], checks=[_check()],
        task_id="backend-route", worktree_base=tmp_path / "worktrees",
        allow_existing_worktree=True, live_enabled=True, operator_decision="go",
    )
    transaction = build_coding_worktree_transaction_receipt(
        plan=plan, base_revision="b" * 40, source_status="", implementer_id="bob"
    )
    read = capture_coding_exact_read_receipt(
        plan=plan, transaction=transaction, repo_path="src/app.py", reader_id="bob",
        worktree_base=tmp_path / "worktrees",
    )
    worktree_file.write_text("VALUE = 'foreign'\n", encoding="utf-8")

    result = apply_coding_patch_transaction(
        plan=plan, transaction=transaction,
        patches=[CodingPatchOperation.create(path="src/app.py", find="'old'", replace="'new'")],
        exact_reads=[read], worktree_base=tmp_path / "worktrees",
        live_enabled=True, operator_decision="go",
    )

    assert result.applied is False
    assert "exact read receipt is stale: src/app.py" in result.blockers
    assert "foreign" in worktree_file.read_text(encoding="utf-8")


def test_duplicate_patch_paths_are_rejected_before_transactional_or_legacy_writes(tmp_path: Path):
    _repo_root(tmp_path)
    worktree_file = tmp_path / "worktrees" / "demo" / "backend-route" / "src" / "app.py"
    worktree_file.parent.mkdir(parents=True)
    original = b"VALUE = 'old'\n"
    worktree_file.write_bytes(original)
    plan = build_coding_task_plan(
        registry=_registry(), repo_id="demo", workspace_base=tmp_path,
        objective="Reject duplicate patch paths", allowed_paths=["src"], checks=[_check()],
        task_id="backend-route", worktree_base=tmp_path / "worktrees",
        allow_existing_worktree=True, live_enabled=True, operator_decision="go",
    )
    transaction = build_coding_worktree_transaction_receipt(
        plan=plan, base_revision="c" * 40, source_status="", implementer_id="bob"
    )
    receipt = capture_coding_exact_read_receipt(
        plan=plan, transaction=transaction, repo_path="src/app.py", reader_id="bob",
        worktree_base=tmp_path / "worktrees",
    )
    duplicates = [
        CodingPatchOperation.create(path="src/app.py", find="'old'", replace="'new'"),
        CodingPatchOperation.create(path="src/app.py", find="'old'", replace="'other'"),
    ]

    strict = apply_coding_patch_transaction(
        plan=plan, transaction=transaction, patches=duplicates, exact_reads=[receipt],
        worktree_base=tmp_path / "worktrees", live_enabled=True, operator_decision="go",
    )
    legacy = apply_coding_patch_set(
        plan=plan, patches=duplicates, worktree_base=tmp_path / "worktrees",
        live_enabled=True, operator_decision="go",
    )

    assert strict.applied is False
    assert "patch transaction contains duplicate paths: src/app.py" in strict.blockers
    assert all(result.ok is False for result in legacy)
    assert all("patch transaction contains duplicate paths: src/app.py" == result.blocker for result in legacy)
    assert worktree_file.read_bytes() == original


def test_transactional_rollback_restores_the_partially_written_failing_target(tmp_path: Path, monkeypatch):
    _repo_root(tmp_path)
    worktree_root = tmp_path / "worktrees" / "demo" / "backend-route" / "src"
    first = worktree_root / "first.py"
    failing = worktree_root / "failing.py"
    worktree_root.mkdir(parents=True)
    originals = {first: b"VALUE = 'old'\n", failing: b"VALUE = 'old'\n"}
    for path, content in originals.items():
        path.write_bytes(content)
    plan = build_coding_task_plan(
        registry=_registry(), repo_id="demo", workspace_base=tmp_path,
        objective="Rollback partial transactional writes", allowed_paths=["src"], checks=[_check()],
        task_id="backend-route", worktree_base=tmp_path / "worktrees",
        allow_existing_worktree=True, live_enabled=True, operator_decision="go",
    )
    transaction = build_coding_worktree_transaction_receipt(
        plan=plan, base_revision="d" * 40, source_status="", implementer_id="bob"
    )
    reads = [
        capture_coding_exact_read_receipt(
            plan=plan, transaction=transaction, repo_path=f"src/{path.name}", reader_id="bob",
            worktree_base=tmp_path / "worktrees",
        )
        for path in (first, failing)
    ]
    original_write = Path.write_bytes
    failed_once = False

    def partial_write(path: Path, content: bytes):
        nonlocal failed_once
        if path == failing and not failed_once:
            failed_once = True
            original_write(path, b"partial-write")
            raise OSError("injected partial write")
        return original_write(path, content)

    monkeypatch.setattr(Path, "write_bytes", partial_write)
    report = apply_coding_patch_transaction(
        plan=plan, transaction=transaction,
        patches=[
            CodingPatchOperation.create(path="src/first.py", find="'old'", replace="'new'"),
            CodingPatchOperation.create(path="src/failing.py", find="'old'", replace="'new'"),
        ],
        exact_reads=reads, worktree_base=tmp_path / "worktrees",
        live_enabled=True, operator_decision="go",
    )

    assert report.status == "rolled_back"
    assert {path: path.read_bytes() for path in originals} == originals


def test_legacy_rollback_restores_the_partially_written_failing_target(tmp_path: Path, monkeypatch):
    _repo_root(tmp_path)
    worktree_root = tmp_path / "worktrees" / "demo" / "backend-route" / "src"
    first = worktree_root / "first.py"
    failing = worktree_root / "failing.py"
    worktree_root.mkdir(parents=True)
    originals = {first: b"VALUE = 'old'\n", failing: b"VALUE = 'old'\n"}
    for path, content in originals.items():
        path.write_bytes(content)
    plan = build_coding_task_plan(
        registry=_registry(), repo_id="demo", workspace_base=tmp_path,
        objective="Rollback partial legacy writes", allowed_paths=["src"], checks=[_check()],
        task_id="backend-route", worktree_base=tmp_path / "worktrees",
        allow_existing_worktree=True, live_enabled=True, operator_decision="go",
    )
    original_write = Path.write_bytes
    failed_once = False

    def partial_write(path: Path, content: bytes):
        nonlocal failed_once
        if path == failing and not failed_once:
            failed_once = True
            original_write(path, b"partial-write")
            raise OSError("injected partial write")
        return original_write(path, content)

    monkeypatch.setattr(Path, "write_bytes", partial_write)
    results = apply_coding_patch_set(
        plan=plan,
        patches=[
            CodingPatchOperation.create(path="src/first.py", find="'old'", replace="'new'"),
            CodingPatchOperation.create(path="src/failing.py", find="'old'", replace="'new'"),
        ],
        worktree_base=tmp_path / "worktrees", live_enabled=True, operator_decision="go",
    )

    assert all(result.ok is False for result in results)
    assert {path: path.read_bytes() for path in originals} == originals


def test_worktree_gate_derives_changed_paths_and_runs_checks(tmp_path: Path):
    _repo_root(tmp_path)
    (tmp_path / "worktrees" / "demo" / "backend-route" / ".git").mkdir(parents=True)
    calls: list[tuple[str, ...]] = []
    plan = build_coding_task_plan(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Update title helper",
        allowed_paths=["src"],
        checks=[_check()],
        task_id="backend-route",
        worktree_base=tmp_path / "worktrees",
        allow_existing_worktree=True,
        live_enabled=True,
        operator_decision="go",
    )

    def fake_runner(argv, *, cwd, timeout_seconds, env):
        calls.append(tuple(argv))
        if argv == ("git", "status", "--porcelain"):
            return CodingCommandResult(exit_code=0, stdout=" M src/app.py\n?? src/new.py\n")
        return CodingCommandResult(exit_code=0, stdout="passed")

    report = evaluate_coding_worktree_gate(
        plan=plan,
        worktree_base=tmp_path / "worktrees",
        command_runner=fake_runner,
    )

    assert report.verified is True
    assert report.changed_paths == ("src/app.py", "src/new.py")
    assert calls == [("git", "status", "--porcelain"), _check().argv]


def test_quality_gate_verifies_scope_and_checks():
    verified = evaluate_coding_quality_gate(
        changed_paths=["src/coding_agent_backend.py", "tests/test_coding_agent_backend.py"],
        allowed_paths=["src", "tests"],
        check_results=[CodingCommandResult(exit_code=0, stdout="ok")],
    )
    blocked = evaluate_coding_quality_gate(
        changed_paths=["src/coding_agent_backend.py", ".git/config"],
        allowed_paths=["routes"],
        check_results=[CodingCommandResult(exit_code=1, stderr="failed")],
    )

    assert verified.verified is True
    assert blocked.verified is False
    assert "changed path is outside allowed scope: src/coding_agent_backend.py" in blocked.blockers
    assert "blocked path changed: .git/config" in blocked.blockers
    assert "one or more quality checks failed" in blocked.blockers


def test_quality_gate_does_not_allow_parent_path_for_file_scoped_slice():
    blocked = evaluate_coding_quality_gate(
        changed_paths=["src"],
        allowed_paths=["src/foo.py"],
        check_results=[CodingCommandResult(exit_code=0, stdout="ok")],
    )

    assert blocked.verified is False
    assert "changed path is outside allowed scope: src" in blocked.blockers


def test_done_publish_and_subagent_contracts_chain_from_verified_gate(tmp_path: Path):
    _repo_root(tmp_path)
    plan = build_coding_task_plan(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Update title helper",
        allowed_paths=["src", "tests"],
        checks=[_check()],
        task_id="backend-route",
        worktree_base=tmp_path / "worktrees",
        live_enabled=True,
        operator_decision="go",
    )
    quality = evaluate_coding_quality_gate(
        changed_paths=["src/app.py", "tests/test_app.py"],
        allowed_paths=plan.allowed_paths,
        blocked_paths=plan.blocked_paths,
        check_results=[CodingCommandResult(exit_code=0, stdout="ok")],
    )
    done = evaluate_coding_done_gate(
        quality_gate=quality,
        review_decision="approved",
        reviewed_by="charlie",
        content_reviewed=True,
    )
    publish = build_coding_publish_plan(
        plan=plan,
        done_gate=done,
        changed_paths=quality.changed_paths,
        commit_message="feat: update title helper",
        commit_confirmed=True,
        push_confirmed=True,
        operator_go=True,
    )
    subagents = build_coding_subagent_plan(plan=plan, worker_agent_id="bob", reviewer_agent_id="charlie")

    assert done.done is True
    assert publish.ready is True
    assert publish.mutation_allowed is True
    assert publish.remote_name == "fuzzy"
    assert publish.to_dict()["operator_gate"]["operator_go"] is True
    assert publish.to_dict()["operator_gate"]["requires_separate_review"] is True
    assert publish.to_dict()["evidence_summary"]["done_gate_complete"] is True
    assert publish.to_dict()["raw_content_visible"] is False
    assert subagents.ready is True
    assert [contract.role for contract in subagents.contracts] == ["worker", "reviewer"]


def _client(registry_path: Path, *, workspace_base: Path, worktree_base: Path) -> TestClient:
    app = FastAPI()
    app.include_router(
        setup_coding_agent_routes(
            registry_path=registry_path,
            workspace_base=workspace_base,
            worktree_base=worktree_base,
            runner_state_dir=workspace_base / "runner-state",
        )
    )
    return TestClient(app)


def test_coding_agent_routes_require_admin_by_default(tmp_path: Path):
    _registry(tmp_path / "repos.json")
    client = _client(tmp_path / "repos.json", workspace_base=tmp_path, worktree_base=tmp_path / "worktrees")

    response = client.post("/api/coding-agent/repos/demo/task-plan", json={"objective": "Add route"})

    assert response.status_code == 403


def test_coding_agent_routes_create_plan_and_quality_gate(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr(agent_task_ledger, "AGENT_TASK_LEDGER_DIR", str(tmp_path / "agent-task-ledger"))
    _repo_root(tmp_path)
    _registry(tmp_path / "repos.json")
    client = _client(tmp_path / "repos.json", workspace_base=tmp_path, worktree_base=tmp_path / "worktrees")

    plan = client.post(
        "/api/coding-agent/repos/demo/task-plan",
        json={
            "objective": "Add route",
            "allowed_paths": ["src", "tests"],
            "checks": [{"argv": ["python", "-m", "pytest", "tests/test_demo.py", "-q"]}],
            "task_id": "add-route",
            "operator_decision": "go",
            "live_enabled": True,
        },
    )
    gate = client.post(
        "/api/coding-agent/quality-gate",
        json={
            "changed_paths": ["src/foo.py"],
            "allowed_paths": ["src"],
            "check_results": [{"exit_code": 0, "stdout": "ok"}],
        },
    )

    assert plan.status_code == 200
    assert plan.json()["success"] is True
    assert plan.json()["coding_task"]["worktree_ref"] == "coding-worktrees/demo/add-route"
    assert plan.json()["runner_state"]["phase"] == "blocked"
    assert plan.json()["runner_state"]["gates_waiting"] == ["planning_authority"]
    assert plan.json()["agent_task"]["surface"] == "workstation"
    assert plan.json()["agent_task"]["target_ref"] == "repo:demo"
    assert plan.json()["agent_task"]["status"] == "planned"
    tasks = client.get("/api/coding-agent/tasks")
    assert tasks.status_code == 200
    assert tasks.json()["records"][0]["task_id"] == "add-route"
    assert tasks.json()["records"][0]["task_type"] == "coding_agent_task"
    state = client.get("/api/coding-agent/runner-state/add-route")
    assert state.status_code == 200
    assert state.json()["runner_state"]["phase"] == "blocked"
    assert gate.status_code == 200
    assert gate.json()["success"] is True
    dumped = json.dumps({"plan": plan.json(), "gate": gate.json()})
    assert str(tmp_path) not in dumped


def test_coding_agent_routes_cover_patch_done_publish_and_subagents(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _repo_root(tmp_path)
    _registry(tmp_path / "repos.json")
    worktree_file = tmp_path / "worktrees" / "demo" / "add-route" / "src" / "foo.py"
    worktree_file.parent.mkdir(parents=True)
    worktree_file.write_text("VALUE = 'old'\n", encoding="utf-8")
    client = _client(tmp_path / "repos.json", workspace_base=tmp_path, worktree_base=tmp_path / "worktrees")
    base = {
        "objective": "Add route",
        "allowed_paths": ["src", "tests"],
        "checks": [{"argv": ["python", "-m", "pytest", "tests/test_demo.py", "-q"]}],
        "task_id": "add-route",
        "operator_decision": "go",
        "live_enabled": True,
    }

    patch = client.post(
        "/api/coding-agent/repos/demo/patch-set",
        json={
            **base,
            "patch_operator_decision": "go",
            "patch_live_enabled": True,
            "patches": [{"path": "src/foo.py", "find": "'old'", "replace": "'new'"}],
        },
    )
    done = client.post(
        "/api/coding-agent/done-gate",
        json={
            "changed_paths": ["src/foo.py"],
            "allowed_paths": ["src"],
            "check_results": [{"exit_code": 0, "stdout": "ok"}],
            "review_decision": "approved",
            "reviewed_by": "charlie",
            "content_reviewed": True,
        },
    )
    publish = client.post(
        "/api/coding-agent/repos/demo/publish-plan",
        json={
            **base,
            "changed_paths": ["src/foo.py"],
            "check_results": [{"exit_code": 0, "stdout": "ok"}],
            "review_decision": "approved",
            "reviewed_by": "charlie",
            "content_reviewed": True,
            "commit_message": "feat: add route",
            "commit_confirmed": True,
            "push_confirmed": True,
            "operator_go": True,
        },
    )
    subagents = client.post(
        "/api/coding-agent/repos/demo/subagents-plan",
        json={**base, "worker_agent_id": "bob", "reviewer_agent_id": "charlie"},
    )

    assert patch.status_code == 200
    assert patch.json()["success"] is True
    assert "VALUE = 'new'" in worktree_file.read_text(encoding="utf-8")
    assert done.status_code == 200
    assert done.json()["success"] is True
    assert publish.status_code == 200
    assert publish.json()["success"] is True
    assert publish.json()["runner_state"]["phase"] == "publish_ready"
    assert publish.json()["runner_state"]["gates_waiting"] == ["operator_publish_go"]
    assert subagents.status_code == 200
    assert subagents.json()["subagents_plan"]["contracts"][0]["role"] == "worker"
    dumped = json.dumps(
        {
            "patch": patch.json(),
            "done": done.json(),
            "publish": publish.json(),
            "subagents": subagents.json(),
        }
    )
    assert str(tmp_path) not in dumped
