"""Client-side contract for browser observation jobs.

This module deliberately builds reviewable payloads only. Executing Playwright
or reaching the network belongs to a sandbox worker behind the crawl policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from src.agent_browser_evidence import BrowserEvidencePacket
from src.agent_web_crawl_policy import AgentWebCrawlPolicy


BROWSER_JOB_SCHEMA = "odysseus.agent.browser_observation_job.v1"


class BrowserWorkerTransport(Protocol):
    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Submit a payload to a worker and return a redacted response."""


@dataclass(frozen=True, slots=True)
class BrowserObservationJob:
    job_id: str
    target_url: str
    policy: AgentWebCrawlPolicy
    capture_dom: bool = True
    capture_accessibility: bool = True
    capture_screenshot: bool = True
    capture_console: bool = True
    capture_network: bool = True
    schema: str = BROWSER_JOB_SCHEMA

    @classmethod
    def create(
        cls,
        *,
        job_id: Any,
        target_url: Any,
        policy: AgentWebCrawlPolicy,
        capture_dom: bool = True,
        capture_accessibility: bool = True,
        capture_screenshot: bool = True,
        capture_console: bool = True,
        capture_network: bool = True,
    ) -> "BrowserObservationJob":
        if not isinstance(policy, AgentWebCrawlPolicy):
            raise ValueError("policy must be an AgentWebCrawlPolicy")
        job = str(job_id or "").strip()
        if not job:
            raise ValueError("job_id must not be empty")
        decision = policy.decide_url(target_url, depth=0, pages_seen=0)
        if not decision.allowed:
            raise ValueError(f"target_url blocked by crawl policy: {decision.reason}")
        return cls(
            job_id=job[:80],
            target_url=decision.normalized_url,
            policy=policy,
            capture_dom=bool(capture_dom),
            capture_accessibility=bool(capture_accessibility),
            capture_screenshot=bool(capture_screenshot),
            capture_console=bool(capture_console),
            capture_network=bool(capture_network),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "job_id": self.job_id,
            "target_url": self.target_url,
            "policy": self.policy.to_dict(),
            "capture": {
                "dom": self.capture_dom,
                "accessibility": self.capture_accessibility,
                "screenshot": self.capture_screenshot,
                "console": self.capture_console,
                "network": self.capture_network,
            },
            "secrets_attached": False,
            "browser_profile": "ephemeral",
        }


@dataclass(frozen=True, slots=True)
class BrowserWorkerResponse:
    job_id: str
    status: str
    evidence: BrowserEvidencePacket | None
    error_class: str = ""

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "BrowserWorkerResponse":
        evidence_payload = payload.get("evidence")
        evidence = BrowserEvidencePacket.create(**evidence_payload) if isinstance(evidence_payload, dict) else None
        return cls(
            job_id=str(payload.get("job_id") or ""),
            status=str(payload.get("status") or "unknown"),
            evidence=evidence,
            error_class=str(payload.get("error_class") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "evidence": self.evidence.to_dict() if self.evidence else None,
            "error_class": self.error_class,
            "raw_content_visible": False,
        }


def submit_browser_observation(
    *,
    job: BrowserObservationJob,
    transport: BrowserWorkerTransport,
) -> BrowserWorkerResponse:
    response = transport.submit(job.to_payload())
    return BrowserWorkerResponse.from_payload(response)
