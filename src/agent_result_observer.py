"""Normalized result artifacts for agent verification."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Iterable


_KINDS = {"command_output", "log_tail", "screenshot", "dom_snapshot", "console", "network", "trace"}
_SECRET_RE = re.compile(r"(authorization|cookie|api[_-]?key|password|bearer\s+[A-Za-z0-9._-]{8,})", re.IGNORECASE)


class ResultObserverError(ValueError):
    """Raised when an observer artifact is invalid."""


@dataclass(frozen=True, slots=True)
class ResultArtifact:
    kind: str
    artifact_ref: str
    summary: str
    content_hash: str
    status: str = "ok"
    raw_content_visible: bool = False

    @classmethod
    def create(cls, *, kind: Any, artifact_ref: Any, summary: Any, status: Any = "ok") -> "ResultArtifact":
        safe_kind = str(kind or "").strip().lower()
        if safe_kind not in _KINDS:
            raise ResultObserverError("unsupported artifact kind")
        safe_summary = _summary(summary)
        return cls(
            kind=safe_kind,
            artifact_ref=_artifact_ref(artifact_ref),
            summary=safe_summary,
            content_hash=_hash_text(safe_summary),
            status=_token(status, default="ok"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "artifact_ref": self.artifact_ref,
            "summary": self.summary,
            "content_hash": self.content_hash,
            "status": self.status,
            "raw_content_visible": False,
        }


@dataclass(frozen=True, slots=True)
class ResultEvidenceBundle:
    run_id: str
    artifacts: tuple[ResultArtifact, ...]
    verdict: str

    @classmethod
    def create(cls, *, run_id: Any, artifacts: Iterable[ResultArtifact | dict[str, Any]]) -> "ResultEvidenceBundle":
        items = tuple(item if isinstance(item, ResultArtifact) else ResultArtifact.create(**item) for item in artifacts)
        if not items:
            raise ResultObserverError("at least one artifact is required")
        verdict = "failed" if any(item.status == "failed" for item in items) else "warning" if any(item.status == "warning" for item in items) else "passed"
        return cls(run_id=_token(run_id, default="run"), artifacts=items[:100], verdict=verdict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "odysseus.agent.result_evidence_bundle.v1",
            "run_id": self.run_id,
            "verdict": self.verdict,
            "artifacts": tuple(item.to_dict() for item in self.artifacts),
            "artifact_count": len(self.artifacts),
            "raw_content_visible": False,
        }


def _artifact_ref(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text or text.startswith("/") or re.match(r"^[A-Za-z]:", text) or ".." in text.split("/"):
        raise ResultObserverError("artifact_ref must be safe repo-relative")
    return text[:180]


def _summary(value: Any) -> str:
    text = " ".join(str(value or "").split())[:500]
    if _SECRET_RE.search(text):
        raise ResultObserverError("summary appears to contain secrets")
    return text


def _token(value: Any, *, default: str) -> str:
    text = str(value or default).strip().lower().replace("-", "_").replace(" ", "_")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", text):
        raise ResultObserverError("token is unsafe")
    return text


def _hash_text(value: Any) -> str:
    return "sha256:" + hashlib.sha256(str(value or "").encode("utf-8", errors="replace")).hexdigest()
