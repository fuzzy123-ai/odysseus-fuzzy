"""Durable, redacted ledger for sandbox worker jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Iterable, Mapping

from src.constants import DATA_DIR


SANDBOX_JOB_LEDGER_SCHEMA = "odysseus.sandbox_job_ledger.v1"

_SECRET_RE = re.compile(
    r"(?i)(authorization|cookie|api[_-]?key|password|passwd|secret|token|bearer\s+[A-Za-z0-9._-]{8,})"
)
_HOST_PATH_RE = re.compile(r"(?i)([a-z]:[\\/]|/home/|/opt/|/users/|~[\\/])")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,180}$")


class SandboxJobLedgerError(ValueError):
    """Raised when a sandbox ledger record would be unsafe."""


@dataclass(frozen=True, slots=True)
class SandboxJobLedgerEvent:
    job_id: str
    status: str
    event_type: str
    created_at: str
    correlation_id: str
    payload_hash: str
    artifact_refs: tuple[str, ...]
    preview: str = ""
    exit_code: int | None = None
    raw_content_visible: bool = False
    schema: str = SANDBOX_JOB_LEDGER_SCHEMA

    @classmethod
    def create(
        cls,
        *,
        job_id: Any,
        status: Any,
        event_type: Any,
        correlation_id: Any = "",
        payload: Mapping[str, Any] | None = None,
        artifact_refs: Iterable[Any] = (),
        preview: Any = "",
        exit_code: Any = None,
        created_at: Any = "",
    ) -> "SandboxJobLedgerEvent":
        payload_dict = dict(payload or {})
        refs = tuple(_safe_artifact_ref(item) for item in artifact_refs)
        safe_preview = _redacted_preview(preview)
        _reject_unsafe_payload(payload_dict)
        if exit_code is None:
            parsed_exit_code = None
        else:
            parsed_exit_code = max(0, min(int(exit_code), 255))
        event = cls(
            job_id=_safe_token(job_id, "job_id"),
            status=_safe_token(status, "status"),
            event_type=_safe_token(event_type, "event_type"),
            created_at=_timestamp(created_at),
            correlation_id=_correlation_id(correlation_id, payload_dict),
            payload_hash=_hash_payload(payload_dict),
            artifact_refs=refs,
            preview=safe_preview,
            exit_code=parsed_exit_code,
        )
        _reject_unsafe_payload(event.to_dict())
        return event

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "job_id": self.job_id,
            "status": self.status,
            "event_type": self.event_type,
            "created_at": self.created_at,
            "correlation_id": self.correlation_id,
            "payload_hash": self.payload_hash,
            "artifact_refs": self.artifact_refs,
            "preview": self.preview,
            "exit_code": self.exit_code,
            "raw_content_visible": False,
        }


class SandboxJobLedger:
    """Append-only JSONL ledger with redacted job lifecycle events."""

    def __init__(self, root: str | Path | None = None):
        base = Path(root) if root is not None else Path(DATA_DIR) / "sandbox_job_ledger"
        self.root = base
        self.root.mkdir(parents=True, exist_ok=True)

    def append(self, event: SandboxJobLedgerEvent | Mapping[str, Any]) -> SandboxJobLedgerEvent:
        normalized = event if isinstance(event, SandboxJobLedgerEvent) else SandboxJobLedgerEvent.create(**dict(event))
        path = self._path_for(normalized.created_at)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(normalized.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
        return normalized

    def record(
        self,
        *,
        job_id: Any,
        status: Any,
        event_type: Any,
        correlation_id: Any = "",
        payload: Mapping[str, Any] | None = None,
        artifact_refs: Iterable[Any] = (),
        preview: Any = "",
        exit_code: Any = None,
    ) -> SandboxJobLedgerEvent:
        return self.append(
            SandboxJobLedgerEvent.create(
                job_id=job_id,
                status=status,
                event_type=event_type,
                correlation_id=correlation_id,
                payload=payload,
                artifact_refs=artifact_refs,
                preview=preview,
                exit_code=exit_code,
            )
        )

    def events(self, *, job_id: str | None = None, limit: int = 100) -> tuple[dict[str, Any], ...]:
        rows: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if job_id is not None and payload.get("job_id") != job_id:
                    continue
                _reject_unsafe_payload(payload)
                rows.append(payload)
        return tuple(rows[-max(1, min(int(limit or 100), 1000)):])

    def latest(self, job_id: str) -> dict[str, Any] | None:
        rows = self.events(job_id=job_id, limit=1)
        return rows[-1] if rows else None

    def artifacts(self, job_id: str) -> tuple[str, ...]:
        refs: list[str] = []
        for event in self.events(job_id=job_id, limit=1000):
            for ref in event.get("artifact_refs") or ():
                safe_ref = _safe_artifact_ref(ref)
                if safe_ref not in refs:
                    refs.append(safe_ref)
        return tuple(refs)

    def _path_for(self, created_at: str) -> Path:
        day = str(created_at or "")[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.root / f"{day}.jsonl"


def _timestamp(value: Any) -> str:
    text = str(value or "").strip()
    if text and re.fullmatch(r"\d{4}-\d{2}-\d{2}T[0-9:.+-]+Z?", text):
        return text
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_token(value: Any, field: str) -> str:
    text = str(value or "").strip().replace(" ", "_")
    if not _SAFE_TOKEN_RE.fullmatch(text):
        raise SandboxJobLedgerError(f"{field} is unsafe")
    return text


def _safe_artifact_ref(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    parts = PurePosixPath(text).parts
    if not text or text.startswith("/") or ".." in parts or not _SAFE_REF_RE.fullmatch(text):
        raise SandboxJobLedgerError("artifact_ref is unsafe")
    if _HOST_PATH_RE.search(text) or _SECRET_RE.search(text):
        raise SandboxJobLedgerError("artifact_ref contains forbidden material")
    return text


def _redacted_preview(value: Any) -> str:
    text = " ".join(str(value or "").split())[:500]
    if _SECRET_RE.search(text) or _HOST_PATH_RE.search(text):
        return "[redacted]"
    return text


def _hash_payload(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(_redact_payload(payload), ensure_ascii=False, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8", errors="replace")).hexdigest()


def _correlation_id(value: Any, payload: Mapping[str, Any]) -> str:
    raw = str(value or "").strip()
    if raw:
        return _safe_token(raw, "correlation_id")
    digest = hashlib.sha256(repr(sorted(payload.items())).encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"sandbox_job_{digest}"


def _redact_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _redact_payload(v) for k, v in value.items() if str(k).lower() not in {"raw", "content", "text"}}
    if isinstance(value, (list, tuple)):
        return [_redact_payload(item) for item in value[:100]]
    text = str(value)
    if _SECRET_RE.search(text) or _HOST_PATH_RE.search(text):
        return "[redacted]"
    return value


def _reject_unsafe_payload(payload: Any) -> None:
    _walk_forbidden(payload)


def _walk_forbidden(value: Any, *, key: str = "") -> None:
    lowered_key = key.lower()
    if lowered_key == "raw_content_visible" and bool(value):
        raise SandboxJobLedgerError("ledger payload exposes raw content")
    if lowered_key in {"authorization", "cookie", "password", "passwd", "api_key", "api-key", "token", "secret"} and value:
        raise SandboxJobLedgerError("ledger payload contains forbidden material")
    if isinstance(value, Mapping):
        for child_key, child_value in value.items():
            _walk_forbidden(child_value, key=str(child_key))
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _walk_forbidden(item, key=key)
        return
    text = str(value or "")
    if _SECRET_RE.search(text) or _HOST_PATH_RE.search(text):
        raise SandboxJobLedgerError("ledger payload contains forbidden material")
