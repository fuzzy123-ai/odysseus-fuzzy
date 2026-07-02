"""Redacted API contract for a sandbox worker."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from src.agent_sandbox_contract import SandboxJobRequest


_SECRET_RE = re.compile(r"(authorization|cookie|api[_-]?key|password|bearer\s+[A-Za-z0-9._-]{8,})", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class SandboxWorkerSubmit:
    job: SandboxJobRequest

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "odysseus.agent.sandbox_worker.submit.v1",
            "job": self.job.to_dict(),
            "raw_content_visible": False,
        }


@dataclass(frozen=True, slots=True)
class SandboxWorkerStatus:
    job_id: str
    status: str
    exit_code: int | None = None
    stdout_preview: str = ""
    stderr_preview: str = ""
    artifact_count: int = 0

    @classmethod
    def create(cls, **kwargs: Any) -> "SandboxWorkerStatus":
        return cls(
            job_id=str(kwargs.get("job_id") or "")[:80],
            status=str(kwargs.get("status") or "unknown")[:40],
            exit_code=kwargs.get("exit_code") if kwargs.get("exit_code") is None else int(kwargs.get("exit_code")),
            stdout_preview=_preview(kwargs.get("stdout_preview")),
            stderr_preview=_preview(kwargs.get("stderr_preview")),
            artifact_count=max(0, int(kwargs.get("artifact_count") or 0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "odysseus.agent.sandbox_worker.status.v1",
            "job_id": self.job_id,
            "status": self.status,
            "exit_code": self.exit_code,
            "stdout_preview": self.stdout_preview,
            "stderr_preview": self.stderr_preview,
            "artifact_count": self.artifact_count,
            "raw_content_visible": False,
        }


def build_sandbox_worker_status(payload: Mapping[str, Any]) -> SandboxWorkerStatus:
    return SandboxWorkerStatus.create(**dict(payload))


def _preview(value: Any) -> str:
    text = " ".join(str(value or "").split())[:500]
    return "[redacted]" if _SECRET_RE.search(text) else text
