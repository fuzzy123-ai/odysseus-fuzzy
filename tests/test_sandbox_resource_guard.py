from src.sandbox_job_templates import build_sandbox_job_from_template
from src.sandbox_resource_guard import evaluate_sandbox_resource_guard


def test_resource_guard_allows_scoped_default_screenshot_artifact_mount():
    job = build_sandbox_job_from_template("browser_smoke", job_id="browser_smoke")
    decision = evaluate_sandbox_resource_guard(job)

    assert decision["allowed"] is True
    assert "rw_mount_not_allowed" not in decision["reasons"]
    assert decision["network_mode"] == "none"
    assert decision["raw_content_visible"] is False


def test_resource_guard_blocks_non_artifact_rw_mount_without_approval():
    template = build_sandbox_job_from_template("browser_smoke", job_id="unsafe_rw")
    job = type(template).create(
        job_id=template.job_id,
        argv=template.argv,
        image=template.image,
        mounts=[{"source": "src", "target": "/workspace/repo/src", "mode": "rw"}],
        limits=template.limits,
        network_mode=template.network_mode,
        network_allowlist=template.network_allowlist,
        secrets_attached=template.secrets_attached,
        capabilities=template.capabilities,
    )
    decision = evaluate_sandbox_resource_guard(job)

    assert decision["allowed"] is False
    assert "rw_mount_not_allowed" in decision["reasons"]


def test_resource_guard_allows_template_with_matching_policy():
    job = build_sandbox_job_from_template("python_pytest", job_id="pytest_smoke")
    decision = evaluate_sandbox_resource_guard(job)

    assert decision["allowed"] is True
    assert decision["raw_content_visible"] is False
