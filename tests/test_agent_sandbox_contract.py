import pytest

from src.agent_sandbox_contract import (
    SandboxContractError,
    SandboxJobRequest,
    SandboxMount,
    evaluate_sandbox_job,
)


def test_sandbox_job_allows_argv_with_scoped_mounts():
    job = SandboxJobRequest.create(
        job_id="smoke",
        argv=["python", "-m", "pytest", "tests/test_example.py"],
        image="localhost/odysseus-agent:dev",
        mounts=[SandboxMount.create(source="tests", target="/workspace/repo/tests", mode="ro")],
        network_mode="none",
    )

    decision = evaluate_sandbox_job(job)

    assert decision.allowed is True
    assert job.to_dict()["secrets_attached"] is False


def test_sandbox_job_blocks_shell_and_forbidden_executable():
    job = SandboxJobRequest.create(job_id="bad", argv=["rm", "-rf", "data"], image="image:dev")

    assert evaluate_sandbox_job(job).allowed is False

    with pytest.raises(SandboxContractError):
        SandboxJobRequest.create(job_id="bad2", argv=["sh", "-lc", "echo hi && rm -rf x"], image="image:dev")


def test_sandbox_job_blocks_fullweb_without_gate_and_absolute_mounts():
    with pytest.raises(SandboxContractError):
        SandboxJobRequest.create(job_id="web", argv=["echo", "hi"], image="image:dev", network_mode="fullweb")

    with pytest.raises(SandboxContractError):
        SandboxMount.create(source="C:/Users/nkatz/odysseus", target="/workspace/repo")
