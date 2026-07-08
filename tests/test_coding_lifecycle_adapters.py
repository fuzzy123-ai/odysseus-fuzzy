import json
from types import SimpleNamespace

import pytest

from src.coding_lifecycle_adapters import (
    CODING_LIFECYCLE_IDENTIFIER_MAP_SCHEMA,
    CodingLifecycleAdapterError,
    identifiers_from_coding_agent,
    identifiers_from_orchestration,
    identifiers_from_server_project,
    merge_identifier_maps,
)
from src.handoff_mailbox import MailboxMessage, ParsedHandoff
from src.server_project_registry import ServerProjectRegistry
from src.server_project_runner import build_server_project_runner_plan
from src.server_project_task_runner import ProjectTaskCheck, ProjectTaskFileWrite, build_project_task_plan
from src.thread_lifecycle_bridge import ThreadDispatchRequest, ThreadRef


def test_coding_agent_identifier_adapter_maps_core_ids_without_side_effects():
    plan = SimpleNamespace(
        repo_id="demo",
        task_id="task-alpha",
        objective="Private objective that must not leak",
        checks=[{"argv": ["python", "-m", "pytest", "tests/test_demo.py"]}],
    )
    dispatch = {
        "task_id": "task-alpha",
        "jobs": [{"job_id": "task-alpha-check-1"}],
        "quality_gate": {"verified": True, "blocking_gate_ids": [], "warning_gate_ids": ["review-attention"]},
    }
    handoff = {"task_id": "task-alpha", "agent": "bob", "slice_id": "CAO3", "status": "done"}
    publish = {"task_id": "task-alpha", "branch_name": "codex/demo-task-alpha", "commit_decision": "plan_ready", "push_decision": "plan_ready"}

    identifiers = identifiers_from_coding_agent(
        coding_plan=plan,
        runner_state={"task_id": "task-alpha", "repo_id": "demo"},
        sandbox_dispatch=dispatch,
        handoff=handoff,
        publish_plan=publish,
        orchestration_node_id="cao3-identifiers",
    )
    payload = identifiers.to_dict()

    assert payload["schema"] == CODING_LIFECYCLE_IDENTIFIER_MAP_SCHEMA
    assert payload["coding_task_id"] == "task-alpha"
    assert payload["repo_id"] == "demo"
    assert payload["orchestration_node_id"] == "cao3-identifiers"
    assert payload["check_job_ids"] == ("task-alpha-check-1",)
    assert payload["gate_ids"] == ("review-attention",)
    assert payload["handoff_ref"].startswith("handoff:")
    assert payload["publish_plan_id"].startswith("publish:")
    assert payload["runtime_event"]["side_effects"] == ("none",)
    dumped = json.dumps(payload, default=str)
    assert "Private objective" not in dumped


def test_server_project_identifier_adapter_maps_project_and_task_ids():
    registry = ServerProjectRegistry()
    record = registry.create_project(
        project_title="Kundenportal MVP",
        project_type="app",
        created_at="2026-07-06T10:00:00Z",
    )
    runner_plan = build_server_project_runner_plan(
        project_title="Kundenportal MVP",
        project_type="app",
        backup_evidence_green=True,
        smoke_target="tests/test_server_project_runner.py",
        rollback_plan="hold deployment",
    )
    task_plan = build_project_task_plan(
        record=record,
        objective="Add entrypoint",
        file_writes=(ProjectTaskFileWrite.create(path="src/app.py", content="print('hi')\n"),),
        checks=(ProjectTaskCheck.create(argv=("python", "-m", "pytest", "tests", "-q")),),
        live_enabled=True,
        operator_decision="go",
    )

    identifiers = identifiers_from_server_project(
        runner_plan=runner_plan,
        project_record=record,
        task_plan=task_plan,
    )
    payload = identifiers.to_dict()

    assert payload["server_project_id"] == "odysseus-server-project-runner"
    assert payload["repo_id"] == "kundenportal-mvp"
    assert payload["server_project_task_id"].startswith("server-project-task:")
    assert payload["check_job_ids"][0].endswith("-check-1")
    assert "quality_gate_1" in payload["gate_ids"]
    assert "server-project-live-go" in payload["gate_ids"]
    assert payload["source_surfaces"] == ("server_project",)


def test_orchestration_identifier_adapter_maps_node_run_and_handoff_refs():
    thread_ref = ThreadRef.create(
        thread_id="019-thread",
        agent_id="bob",
        agent_run_id="run-b",
        plan_id="auto4-plan",
        node_id="cao3-identifiers",
    )
    request = ThreadDispatchRequest.create(
        thread_ref=thread_ref,
        expected_agent_id="bob",
        expected_agent_run_id="run-b",
        expected_node_id="cao3-identifiers",
        prompt_summary="Continue CAO3",
        allowed_action="send",
    )
    handoff = ParsedHandoff.create(agent="alice", slice_id="CAO2", status="done", evidence=["tests passed"])
    message = MailboxMessage.create(
        thread_ref=thread_ref,
        prompt_summary="Continue CAO3",
        allowed_action="send",
        source_handoff=handoff,
    )

    identifiers = identifiers_from_orchestration(
        dispatch_request=request,
        mailbox_message=message,
    )
    payload = identifiers.to_dict()

    assert payload["orchestration_node_id"] == "cao3-identifiers"
    assert payload["agent_run_ids"] == ("run-b",)
    assert payload["handoff_ref"] == message.message_id
    assert payload["source_surfaces"] == ("orchestration",)


def test_identifier_maps_merge_and_reject_conflicts():
    coding = identifiers_from_coding_agent(coding_plan=SimpleNamespace(repo_id="demo", task_id="task-alpha", checks=[]))
    orchestration = identifiers_from_orchestration(thread_ref=ThreadRef.create(
        thread_id="019-thread",
        agent_id="bob",
        agent_run_id="run-b",
        plan_id="cao-plan",
        node_id="cao3-identifiers",
    ))

    merged = merge_identifier_maps(coding, orchestration).to_dict()

    assert merged["coding_task_id"] == "task-alpha"
    assert merged["repo_id"] == "demo"
    assert merged["orchestration_node_id"] == "cao3-identifiers"
    assert merged["agent_run_ids"] == ("run-b",)
    assert merged["source_surfaces"] == ("coding_agent", "orchestration")

    other_repo = identifiers_from_coding_agent(coding_plan=SimpleNamespace(repo_id="other", task_id="task-alpha", checks=[]))
    with pytest.raises(CodingLifecycleAdapterError, match="identifier conflict: repo_id"):
        merge_identifier_maps(coding, other_repo)


def test_identifier_adapter_redacts_host_paths_and_secret_material():
    identifiers = identifiers_from_coding_agent(
        coding_plan=SimpleNamespace(
            repo_id=r"C:\Users\nkatz\private",
            task_id="token=abc123",
            objective=r"C:\Users\nkatz\private token=abc123",
            checks=[{"argv": ["python", "-m", "pytest"]}],
        )
    )

    dumped = json.dumps(identifiers.to_dict(), default=str)

    assert r"C:\Users\nkatz" not in dumped
    assert "token=abc123" not in dumped
    assert "sha256:" in dumped
