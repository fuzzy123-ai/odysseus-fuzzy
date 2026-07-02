from pathlib import Path

from src.agent_sandbox_worker import SandboxWorker
from src.coding_agent_backend import (
    CodingCheckCommand,
    build_coding_publish_plan,
    build_coding_task_plan,
    evaluate_coding_done_gate,
    evaluate_coding_quality_gate,
)
from src.coding_agent_memory_bridge import build_coding_agent_memory_write_intent
from src.coding_agent_sandbox_bridge import dispatch_coding_checks_to_sandbox, sandbox_status_to_coding_result
from src.evidence_storage import write_evidence_report
from src.repo_registry import RepoRecord, RepoRegistry
from src.sandbox_job_ledger import SandboxJobLedger
from src.telegram_task_orchestrator import build_telegram_task_intent


def _repo_root(tmp_path: Path) -> Path:
    repo = tmp_path / "repos" / "demo"
    (repo / ".git").mkdir(parents=True)
    return repo


def _registry() -> RepoRegistry:
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
            allowed_actions=["status", "log", "diff_stat", "changed_paths", "branch"],
            created_at="2026-07-02T10:00:00Z",
        )
    )
    return registry


def test_autonomous_coding_agent_dry_run_flow(tmp_path: Path):
    _repo_root(tmp_path)
    intent = build_telegram_task_intent({"kind": "text", "text": "Baue im Projekt demo ein Feature und teste es"})
    plan = build_coding_task_plan(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Add a small feature",
        allowed_paths=["src", "tests"],
        checks=[CodingCheckCommand.create(argv=["python", "-m", "pytest", "tests/test_demo.py", "-q"])],
        task_id="telegram-feature",
        worktree_base=tmp_path / "worktrees",
        live_enabled=True,
        operator_decision="go",
    )
    worker = SandboxWorker(ledger=SandboxJobLedger(tmp_path / "ledger"))

    dispatch = dispatch_coding_checks_to_sandbox(
        plan=plan,
        worker=worker,
        changed_paths=["src/feature.py", "tests/test_feature.py"],
    )
    check_results = tuple(sandbox_status_to_coding_result(status) for status in dispatch.statuses)
    quality = evaluate_coding_quality_gate(
        changed_paths=["src/feature.py", "tests/test_feature.py"],
        allowed_paths=plan.allowed_paths,
        blocked_paths=plan.blocked_paths,
        check_results=check_results,
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
        commit_message="feat: add small feature",
        commit_confirmed=True,
        push_confirmed=True,
        operator_go=True,
    )
    report = write_evidence_report(
        report_ref="autonomous_coding_agent/e2e-dry-run.json",
        payload={
            "task_type": intent.task_type,
            "dispatch": dispatch.to_dict(),
            "quality": quality.to_dict(),
            "publish_ready": publish.ready,
        },
        root=tmp_path / "reports",
    )
    memory_intent = build_coding_agent_memory_write_intent(
        {
            "title": "Autonomous coding dry run",
            "summary": "Coding task dry run reached sandbox dispatch, quality gate and publish plan.",
            "content_hash": report.content_hash,
            "confidence": 0.9,
        },
        model="gemma4:e4b",
    )

    assert intent.task_type == "coding_agent_task"
    assert dispatch.statuses[0].status == "dry_run"
    assert dispatch.quality_gate["verified"] is True
    assert quality.verified is True
    assert done.done is True
    assert publish.ready is True
    assert report.written is True
    assert memory_intent["policy"]["review_required"] is True
    assert memory_intent["raw_content_visible"] is False
