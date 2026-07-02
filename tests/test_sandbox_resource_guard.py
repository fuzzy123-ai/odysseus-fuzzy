from src.sandbox_job_templates import build_sandbox_job_from_template
from src.sandbox_resource_guard import evaluate_sandbox_resource_guard


def test_resource_guard_blocks_rw_mount_without_approval():
    job = build_sandbox_job_from_template("browser_smoke", job_id="browser_smoke")
    decision = evaluate_sandbox_resource_guard(job)

    assert decision["allowed"] is False
    assert "rw_mount_not_allowed" in decision["reasons"]


def test_resource_guard_allows_template_with_matching_policy():
    job = build_sandbox_job_from_template("python_pytest", job_id="pytest_smoke")
    decision = evaluate_sandbox_resource_guard(job)

    assert decision["allowed"] is True
    assert decision["raw_content_visible"] is False
