from src.agent_sandbox_worker_api import build_sandbox_worker_status


def test_sandbox_worker_status_redacts_secret_preview():
    status = build_sandbox_worker_status(
        {
            "job_id": "job1",
            "status": "failed",
            "stdout_preview": "Authorization: bearer abcdefghijk",
            "stderr_preview": "plain error",
            "artifact_count": 2,
        }
    )

    payload = status.to_dict()

    assert payload["stdout_preview"] == "[redacted]"
    assert payload["stderr_preview"] == "plain error"
    assert payload["raw_content_visible"] is False
