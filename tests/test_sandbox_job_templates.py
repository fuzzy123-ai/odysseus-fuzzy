import pytest

from src.agent_sandbox_contract import evaluate_sandbox_job
from src.sandbox_job_templates import (
    SandboxJobTemplateError,
    build_sandbox_job_from_template,
    list_sandbox_job_template_specs,
    list_sandbox_job_templates,
)


def test_sandbox_templates_create_safe_jobs():
    assert "python_pytest" in list_sandbox_job_templates()
    job = build_sandbox_job_from_template("python_pytest", job_id="pytest_smoke", extra_args=("tests/test_x.py",))

    assert job.network_mode == "none"
    assert job.secrets_attached is False
    assert job.capabilities == ("python", "pytest", "read_repo", "artifact_reports")
    assert evaluate_sandbox_job(job).allowed is True


def test_sandbox_template_specs_map_to_profiles_without_enabling_writes():
    specs = {spec["template_id"]: spec for spec in list_sandbox_job_template_specs()}

    assert specs["python_pytest"]["profile_id"] == "python"
    assert specs["node_check"]["profile_id"] == "node"
    assert specs["browser_smoke"]["profile_id"] == "webdev_playwright"
    assert specs["browser_smoke"]["network_mode"] == "none"
    assert "playwright" in specs["browser_smoke"]["capabilities"]

    for spec in specs.values():
        assert spec["secrets_attached"] is False
        assert spec["write_action_enabled"] is False


def test_sandbox_template_rejects_unknown_template():
    with pytest.raises(SandboxJobTemplateError):
        build_sandbox_job_from_template("shell", job_id="bad")
