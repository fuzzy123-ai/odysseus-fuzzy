from src.agent_sandbox_contract import SandboxJobRequest
from src.agent_sandbox_podman_plan import render_podman_sandbox_plan


def test_podman_plan_is_argv_only_and_not_executed():
    job = SandboxJobRequest.create(
        job_id="job1",
        argv=["python", "--version"],
        image="localhost/odysseus-agent:dev",
    )

    plan = render_podman_sandbox_plan(job)
    payload = plan.to_dict()

    assert payload["executes_live"] is False
    assert payload["pod_create_argv"][:3] == ("podman", "pod", "create")
    assert "--privileged" not in payload["run_argv"]
    assert payload["decision"]["allowed"] is True
