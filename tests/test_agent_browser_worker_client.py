from src.agent_browser_worker_client import BrowserObservationJob, submit_browser_observation
from src.agent_web_crawl_policy import AgentWebCrawlPolicy


class FakeTransport:
    def __init__(self):
        self.payload = None

    def submit(self, payload):
        self.payload = payload
        return {
            "job_id": payload["job_id"],
            "status": "done",
            "evidence": {
                "target_url": payload["target_url"],
                "captured_at": "2026-07-02T12:00:00Z",
                "text_summary": "summary",
            },
        }


def test_browser_worker_client_submits_redacted_policy_payload():
    policy = AgentWebCrawlPolicy.create(allowed_domains=["example.test"], external_network_go=True)
    job = BrowserObservationJob.create(job_id="job1", target_url="https://example.test", policy=policy)
    transport = FakeTransport()

    response = submit_browser_observation(job=job, transport=transport)

    assert transport.payload["secrets_attached"] is False
    assert transport.payload["browser_profile"] == "ephemeral"
    assert response.to_dict()["raw_content_visible"] is False
    assert response.evidence is not None
