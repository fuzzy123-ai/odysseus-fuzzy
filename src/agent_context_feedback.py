"""Durable, redacted storage for validated Agent Context feedback.

The store persists feedback signals and proposed rule candidates only.  It has
no policy-apply, memory-runtime, UI, notification, or provider behavior.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Iterator, Mapping, Sequence

from src.agent_context_transparency import (
    AgentContextContractError,
    ContextItem,
    ReviewDecision,
    SourceRef,
    UserContextFeedback,
    classify_review,
    validate_payload,
)


FEEDBACK_STORE_SCHEMA = "odysseus.agent_context_feedback_store.v1"
STORE_FILE_NAME = "feedback-store.json"
LOCK_FILE_NAME = ".feedback-store.lock"
MAX_STORE_RECORDS = 5_000
MAX_LIST_LIMIT = 1_000
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024

_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CANONICAL_ACTIONS = {"pin", "remove", "approve", "hide", "rename"}
_ACTION_ALIASES = {"useful": "approve", "not_useful": "remove"}
_CANDIDATE_SUMMARIES = {
    "prefer": "Prefer this context source in the selected scope.",
    "exclude": "Exclude this context source in the selected scope.",
    "confirm": "Treat this context source as user-confirmed in the selected scope.",
    "hide": "Hide this context source by default in the selected scope.",
    "display_label": "Use the proposed safe display label in the selected scope.",
}

_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.RLock] = {}


class AgentContextFeedbackStoreError(ValueError):
    """Raised when feedback storage is invalid, unsafe, or conflicting."""


@dataclass(frozen=True, slots=True)
class FeedbackWriteResult:
    feedback_id: str
    content_hash: str
    created: bool
    idempotent: bool
    record: Mapping[str, Any]


def normalize_feedback_action(value: Any) -> str:
    """Map bounded UI aliases to canonical contract actions."""

    if not isinstance(value, str):
        raise AgentContextFeedbackStoreError("feedback action must be text")
    if value in _CANONICAL_ACTIONS:
        return value
    canonical = _ACTION_ALIASES.get(value)
    if canonical is None:
        raise AgentContextFeedbackStoreError("feedback action is invalid")
    return canonical


def adapt_feedback_action_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a shallow payload copy with only the action alias normalized."""

    if not isinstance(value, Mapping):
        raise AgentContextFeedbackStoreError("feedback payload must be an object")
    result = dict(value)
    result["action"] = normalize_feedback_action(result.get("action"))
    return result


class AgentContextFeedbackStore:
    """Atomic snapshot store for redacted, validated feedback signals."""

    def __init__(self, root: str | Path):
        self.root = _prepare_root(root)
        self.path = self._contained_path(STORE_FILE_NAME)
        self.lock_path = self._contained_path(LOCK_FILE_NAME)
        with _LOCKS_GUARD:
            self._thread_lock = _THREAD_LOCKS.setdefault(str(self.root), threading.RLock())

    def append(
        self,
        feedback: UserContextFeedback | Mapping[str, Any],
        *,
        disagreement: bool = False,
        durable_apply_requested: bool = False,
    ) -> FeedbackWriteResult:
        normalized = _validated_feedback(feedback)
        disagreement_flag = _strict_bool(disagreement, "disagreement")
        apply_flag = _strict_bool(durable_apply_requested, "durable_apply_requested")
        observations = ["feedback_recording"]
        if disagreement_flag:
            observations.append("feedback_disagreement")
        if apply_flag:
            observations.append("policy_writeback")
        recording_review = classify_review(observations)

        logical_hash = _hash_json({
            "feedback": normalized.to_dict(),
            "recording_review": recording_review.to_dict(),
        })
        persisted_feedback = _redacted_feedback(normalized)
        persisted_body = {
            "feedback": persisted_feedback,
            "recording_review": recording_review.to_dict(),
        }
        persisted_hash = _hash_json(persisted_body)
        stored_at = _now()
        record = {
            "feedback_id": normalized.feedback_id,
            "content_hash": logical_hash,
            "persisted_hash": persisted_hash,
            "stored_at": stored_at,
            **persisted_body,
        }
        _validate_record(record)

        with self._exclusive():
            snapshot = self._read_snapshot_unlocked()
            existing = next(
                (item for item in snapshot["records"] if item.get("feedback_id") == normalized.feedback_id),
                None,
            )
            if existing is not None:
                _validate_record(existing)
                if existing.get("content_hash") != logical_hash:
                    raise AgentContextFeedbackStoreError("feedback_id is already used by different content")
                return FeedbackWriteResult(
                    feedback_id=normalized.feedback_id,
                    content_hash=logical_hash,
                    created=False,
                    idempotent=True,
                    record=_copy_json(existing),
                )
            if len(snapshot["records"]) >= MAX_STORE_RECORDS:
                raise AgentContextFeedbackStoreError("feedback store record budget is exhausted")
            records = list(snapshot["records"])
            records.append(record)
            records.sort(key=lambda item: str(item.get("feedback_id") or ""))
            replacement = {
                "schema": FEEDBACK_STORE_SCHEMA,
                "revision": int(snapshot["revision"]) + 1,
                "records": records,
            }
            self._write_snapshot_unlocked(replacement)
        return FeedbackWriteResult(
            feedback_id=normalized.feedback_id,
            content_hash=logical_hash,
            created=True,
            idempotent=False,
            record=_copy_json(record),
        )

    def get(self, feedback_id: str) -> dict[str, Any] | None:
        safe_id = _safe_id(feedback_id)
        with self._exclusive():
            snapshot = self._read_snapshot_unlocked()
        for record in snapshot["records"]:
            if record.get("feedback_id") == safe_id:
                _validate_record(record)
                return _copy_json(record)
        return None

    def list(
        self,
        *,
        limit: int = 100,
        action: str | None = None,
        candidate_status: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        capped = _bounded_limit(limit)
        action_filter = normalize_feedback_action(action) if action is not None else None
        if candidate_status not in {None, "proposed"}:
            raise AgentContextFeedbackStoreError("candidate_status is invalid")
        with self._exclusive():
            snapshot = self._read_snapshot_unlocked()
        records: list[dict[str, Any]] = []
        for record in snapshot["records"]:
            _validate_record(record)
            payload = record["feedback"]
            if action_filter is not None and payload.get("action") != action_filter:
                continue
            candidate = payload.get("learned_rule_candidate")
            status = candidate.get("status") if isinstance(candidate, Mapping) else None
            if candidate_status is not None and status != candidate_status:
                continue
            records.append(_copy_json(record))
        records.sort(key=lambda item: (str(item.get("stored_at") or ""), str(item.get("feedback_id") or "")))
        return tuple(records[-capped:])

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        _assert_no_link(self.root)
        _assert_safe_store_file(self.path, self.root)
        _assert_safe_store_file(self.lock_path, self.root)
        with self._thread_lock:
            with _file_lock(self.lock_path):
                yield

    def _contained_path(self, name: str) -> Path:
        if name not in {STORE_FILE_NAME, LOCK_FILE_NAME}:
            raise AgentContextFeedbackStoreError("store path name is invalid")
        candidate = self.root / name
        try:
            candidate.resolve(strict=False).relative_to(self.root)
        except ValueError as exc:
            raise AgentContextFeedbackStoreError("store path escapes its root") from exc
        return candidate

    def _read_snapshot_unlocked(self) -> dict[str, Any]:
        _assert_safe_store_file(self.path, self.root)
        if not self.path.exists():
            return {"schema": FEEDBACK_STORE_SCHEMA, "revision": 0, "records": []}
        try:
            raw = self.path.read_bytes()
        except OSError as exc:
            raise AgentContextFeedbackStoreError("feedback snapshot cannot be read") from exc
        if len(raw) > MAX_SNAPSHOT_BYTES:
            raise AgentContextFeedbackStoreError("feedback snapshot exceeds its byte budget")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AgentContextFeedbackStoreError("feedback snapshot is invalid JSON") from exc
        return _validate_snapshot(payload)

    def _write_snapshot_unlocked(self, snapshot: Mapping[str, Any]) -> None:
        normalized = _validate_snapshot(snapshot)
        encoded = _canonical_json(normalized).encode("utf-8")
        if len(encoded) > MAX_SNAPSHOT_BYTES:
            raise AgentContextFeedbackStoreError("feedback snapshot exceeds its byte budget")
        temp = self._contained_temp_path()
        try:
            with temp.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            _assert_safe_store_file(temp, self.root)
            parsed = json.loads(temp.read_text(encoding="utf-8"))
            _validate_snapshot(parsed)
            os.replace(temp, self.path)
            _assert_safe_store_file(self.path, self.root)
            _validate_snapshot(json.loads(self.path.read_text(encoding="utf-8")))
        except AgentContextFeedbackStoreError:
            raise
        except Exception as exc:
            raise AgentContextFeedbackStoreError("feedback snapshot atomic write failed") from exc
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass

    def _contained_temp_path(self) -> Path:
        name = f".{STORE_FILE_NAME}.{os.getpid()}.{threading.get_ident()}.tmp"
        candidate = self.root / name
        try:
            candidate.resolve(strict=False).relative_to(self.root)
        except ValueError as exc:
            raise AgentContextFeedbackStoreError("temporary store path escapes its root") from exc
        _assert_safe_store_file(candidate, self.root)
        return candidate


def _validated_feedback(value: UserContextFeedback | Mapping[str, Any]) -> UserContextFeedback:
    try:
        normalized = validate_payload(value)
    except AgentContextContractError as exc:
        raise AgentContextFeedbackStoreError(str(exc)) from exc
    if not isinstance(normalized, UserContextFeedback):
        raise AgentContextFeedbackStoreError("store accepts only UserContextFeedback payloads")
    if normalized.action not in _CANONICAL_ACTIONS:
        raise AgentContextFeedbackStoreError("feedback action is invalid")
    if normalized.policy_effect != "none":
        raise AgentContextFeedbackStoreError("feedback cannot mutate policy")
    candidate = normalized.learned_rule_candidate
    if candidate is not None and candidate.status != "proposed":
        raise AgentContextFeedbackStoreError("feedback candidate must remain proposed")
    return normalized


def _redacted_feedback(feedback: UserContextFeedback) -> dict[str, Any]:
    payload = feedback.to_dict()
    target = feedback.target_ref.source_ref if isinstance(feedback.target_ref, ContextItem) else feedback.target_ref
    payload["target_ref"] = _short_ref(target)
    payload["reason"] = None
    if feedback.classification in {"sensitive", "secret", "unknown"} or feedback.redaction_state in {
        "metadata_only", "fully_redacted", "blocked",
    }:
        if payload.get("proposed_label") is not None:
            payload["proposed_label"] = "Redacted label"
    candidate = feedback.learned_rule_candidate
    if candidate is not None:
        candidate_payload = candidate.to_dict()
        candidate_payload["target_ref"] = _short_ref(candidate.target_ref)
        candidate_payload["summary"] = _CANDIDATE_SUMMARIES[candidate.candidate_type]
        payload["learned_rule_candidate"] = candidate_payload
    try:
        normalized = validate_payload(payload)
    except AgentContextContractError as exc:
        raise AgentContextFeedbackStoreError(str(exc)) from exc
    if not isinstance(normalized, UserContextFeedback):
        raise AgentContextFeedbackStoreError("redacted feedback type is invalid")
    return normalized.to_dict()


def _short_ref(value: SourceRef) -> dict[str, str]:
    return {"ref_type": value.ref_type, "ref_id": value.ref_id}


def _validate_snapshot(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AgentContextFeedbackStoreError("feedback snapshot must be an object")
    if set(value) != {"schema", "revision", "records"}:
        raise AgentContextFeedbackStoreError("feedback snapshot fields are invalid")
    if value.get("schema") != FEEDBACK_STORE_SCHEMA:
        raise AgentContextFeedbackStoreError("feedback snapshot schema is invalid")
    revision = value.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise AgentContextFeedbackStoreError("feedback snapshot revision is invalid")
    records = value.get("records")
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise AgentContextFeedbackStoreError("feedback snapshot records must be an array")
    if len(records) > MAX_STORE_RECORDS:
        raise AgentContextFeedbackStoreError("feedback snapshot exceeds its record budget")
    normalized_records: list[dict[str, Any]] = []
    ids: set[str] = set()
    for record in records:
        normalized = _validate_record(record)
        feedback_id = normalized["feedback_id"]
        if feedback_id in ids:
            raise AgentContextFeedbackStoreError("feedback snapshot contains duplicate ids")
        ids.add(feedback_id)
        normalized_records.append(normalized)
    if [item["feedback_id"] for item in normalized_records] != sorted(ids):
        raise AgentContextFeedbackStoreError("feedback snapshot record order is invalid")
    return {"schema": FEEDBACK_STORE_SCHEMA, "revision": revision, "records": normalized_records}


def _validate_record(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AgentContextFeedbackStoreError("feedback record must be an object")
    expected = {"feedback_id", "content_hash", "persisted_hash", "stored_at", "feedback", "recording_review"}
    if set(value) != expected:
        raise AgentContextFeedbackStoreError("feedback record fields are invalid")
    feedback_id = _safe_id(value.get("feedback_id"))
    content_hash = _safe_hash(value.get("content_hash"), "content_hash")
    persisted_hash = _safe_hash(value.get("persisted_hash"), "persisted_hash")
    stored_at = _safe_timestamp(value.get("stored_at"))
    try:
        feedback = validate_payload(value.get("feedback"))
        recording_review = ReviewDecision.from_dict(value.get("recording_review"))
    except AgentContextContractError as exc:
        raise AgentContextFeedbackStoreError(str(exc)) from exc
    if not isinstance(feedback, UserContextFeedback):
        raise AgentContextFeedbackStoreError("stored payload is not UserContextFeedback")
    if feedback.feedback_id != feedback_id:
        raise AgentContextFeedbackStoreError("feedback record identity mismatch")
    if feedback.policy_effect != "none":
        raise AgentContextFeedbackStoreError("stored feedback cannot mutate policy")
    if feedback.reason is not None:
        raise AgentContextFeedbackStoreError("stored feedback reason must be redacted")
    candidate = feedback.learned_rule_candidate
    if candidate is not None:
        if candidate.status != "proposed":
            raise AgentContextFeedbackStoreError("stored candidate must remain proposed")
        if candidate.summary != _CANDIDATE_SUMMARIES[candidate.candidate_type]:
            raise AgentContextFeedbackStoreError("stored candidate summary is not canonical")
    computed = _hash_json({"feedback": feedback.to_dict(), "recording_review": recording_review.to_dict()})
    if computed != persisted_hash:
        raise AgentContextFeedbackStoreError("feedback persisted hash mismatch")
    return {
        "feedback_id": feedback_id,
        "content_hash": content_hash,
        "persisted_hash": persisted_hash,
        "stored_at": stored_at,
        "feedback": feedback.to_dict(),
        "recording_review": recording_review.to_dict(),
    }


def _prepare_root(value: str | Path) -> Path:
    if not isinstance(value, (str, Path)):
        raise AgentContextFeedbackStoreError("feedback store root is invalid")
    raw = Path(value)
    if not raw.is_absolute():
        raise AgentContextFeedbackStoreError("feedback store root must be an injected absolute path")
    if ".." in raw.parts:
        raise AgentContextFeedbackStoreError("feedback store root contains traversal")
    _assert_no_link(raw)
    try:
        raw.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AgentContextFeedbackStoreError("feedback store root cannot be created") from exc
    _assert_no_link(raw)
    resolved = raw.resolve(strict=True)
    if resolved == Path(resolved.anchor):
        raise AgentContextFeedbackStoreError("feedback store root cannot be a filesystem root")
    if not resolved.is_dir():
        raise AgentContextFeedbackStoreError("feedback store root must be a directory")
    return resolved


def _assert_no_link(path: Path) -> None:
    current = Path(path.anchor) if path.is_absolute() else Path()
    parts = path.parts[1:] if path.is_absolute() else path.parts
    for part in parts:
        current = current / part
        if not current.exists():
            continue
        is_junction = bool(getattr(current, "is_junction", lambda: False)())
        if current.is_symlink() or is_junction:
            raise AgentContextFeedbackStoreError("feedback store path contains a link or junction")


def _assert_safe_store_file(path: Path, root: Path) -> None:
    try:
        path.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise AgentContextFeedbackStoreError("feedback store path escapes its root") from exc
    if path.exists():
        is_junction = bool(getattr(path, "is_junction", lambda: False)())
        if path.is_symlink() or is_junction:
            raise AgentContextFeedbackStoreError("feedback store file cannot be a link or junction")
        if path.is_dir():
            raise AgentContextFeedbackStoreError("feedback store file path is a directory")


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    try:
        with path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except AgentContextFeedbackStoreError:
        raise
    except OSError as exc:
        raise AgentContextFeedbackStoreError("feedback store lock failed") from exc


def _safe_id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", value):
        raise AgentContextFeedbackStoreError("feedback_id is invalid")
    return value


def _safe_hash(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise AgentContextFeedbackStoreError(f"{field} is invalid")
    return value


def _safe_timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise AgentContextFeedbackStoreError("stored_at is invalid")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise AgentContextFeedbackStoreError("stored_at is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise AgentContextFeedbackStoreError("stored_at must be UTC")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _bounded_limit(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > MAX_LIST_LIMIT:
        raise AgentContextFeedbackStoreError("list limit is invalid")
    return value


def _strict_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise AgentContextFeedbackStoreError(f"{field} must be boolean")
    return value


def _hash_json(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _copy_json(value: Any) -> Any:
    return json.loads(_canonical_json(value))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "AgentContextFeedbackStore", "AgentContextFeedbackStoreError", "FEEDBACK_STORE_SCHEMA",
    "FeedbackWriteResult", "adapt_feedback_action_payload", "normalize_feedback_action",
]
