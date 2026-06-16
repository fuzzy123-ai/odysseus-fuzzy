"""Handoff parsing and dispatch mailbox models for orchestration runtime.

AUTO3 keeps this layer deliberately offline: it validates agent handoffs and
prepares dispatch envelopes, but it never reads from or sends to real threads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import re
from typing import Any

from src.thread_lifecycle_bridge import ThreadRef
from src.thread_registry import ThreadRegistry, ThreadRegistryError


_MAX_TEXT = 400
_MAX_LIST_ITEMS = 40
_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
_ABS_WINDOWS_RE = re.compile(r"^[A-Za-z]:[\\/]")
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9._/ -]+$")


class HandoffMailboxError(ValueError):
    """Raised when a handoff or mailbox payload is invalid."""


class HandoffStatus(StrEnum):
    DONE = "done"
    BLOCKED = "blocked"
    HANDOFF = "handoff"
    FAILED = "failed"
    RUNNING = "running"
    UNKNOWN = "unknown"


class MailboxMessageStatus(StrEnum):
    QUEUED = "queued"
    ACKNOWLEDGED = "acknowledged"
    CANCELLED = "cancelled"


_FIELD_ALIASES = {
    "agent": "agent",
    "slice": "slice_id",
    "status": "status",
    "commit": "commit",
    "changed files": "changed_files",
    "geanderte dateien": "changed_files",
    "geaenderte dateien": "changed_files",
    "geänderte dateien": "changed_files",
    "files": "changed_files",
    "tests": "tests",
    "evidence": "evidence",
    "blocker": "blocker",
    "next slice": "next_slice",
    "nachster slice": "next_slice",
    "naechster slice": "next_slice",
    "nächster slice": "next_slice",
}

_STATUS_ALIASES = {
    "done": HandoffStatus.DONE,
    "fertig": HandoffStatus.DONE,
    "completed": HandoffStatus.DONE,
    "blocked": HandoffStatus.BLOCKED,
    "blockiert": HandoffStatus.BLOCKED,
    "handoff": HandoffStatus.HANDOFF,
    "ready-for-handoff": HandoffStatus.HANDOFF,
    "ready_for_handoff": HandoffStatus.HANDOFF,
    "failed": HandoffStatus.FAILED,
    "red": HandoffStatus.FAILED,
    "running": HandoffStatus.RUNNING,
    "active": HandoffStatus.RUNNING,
    "unknown": HandoffStatus.UNKNOWN,
    "unklar": HandoffStatus.UNKNOWN,
}


@dataclass(frozen=True, slots=True)
class ParsedHandoff:
    agent: str
    slice_id: str
    status: HandoffStatus
    commit: str
    changed_files: tuple[str, ...]
    tests: tuple[str, ...]
    evidence: tuple[str, ...]
    blocker: str
    next_slice: str

    @classmethod
    def create(
        cls,
        *,
        agent: Any,
        slice_id: Any,
        status: Any,
        commit: Any = "",
        changed_files: Any = (),
        tests: Any = (),
        evidence: Any = (),
        blocker: Any = "",
        next_slice: Any = "",
    ) -> "ParsedHandoff":
        normalized_status = _normalize_status(status)
        normalized_commit = _normalize_commit(commit)
        normalized_changed_files = _normalize_paths(changed_files)
        normalized_tests = _normalize_list(tests, field_name="tests")
        normalized_evidence = _normalize_list(evidence, field_name="evidence")
        normalized_blocker = _normalize_text(blocker, field_name="blocker", allow_empty=True)
        normalized_next_slice = _normalize_slug_text(next_slice, field_name="next_slice", allow_empty=True)

        handoff = cls(
            agent=_normalize_slug_text(agent, field_name="agent", allow_empty=False),
            slice_id=_normalize_slug_text(slice_id, field_name="slice_id", allow_empty=False),
            status=normalized_status,
            commit=normalized_commit,
            changed_files=normalized_changed_files,
            tests=normalized_tests,
            evidence=normalized_evidence,
            blocker=normalized_blocker,
            next_slice=normalized_next_slice,
        )
        handoff.validate()
        return handoff

    def validate(self) -> None:
        if self.status == HandoffStatus.DONE and not (self.commit or self.tests or self.evidence):
            raise HandoffMailboxError("done handoff requires commit, tests, or evidence")
        if self.status == HandoffStatus.BLOCKED and not self.blocker:
            raise HandoffMailboxError("blocked handoff requires blocker")
        if self.status == HandoffStatus.HANDOFF and not self.next_slice:
            raise HandoffMailboxError("handoff status requires next_slice")
        if self.status == HandoffStatus.FAILED and not (self.blocker or self.evidence):
            raise HandoffMailboxError("failed handoff requires blocker or evidence")

    @property
    def requires_charlie_action(self) -> bool:
        return self.status in {
            HandoffStatus.DONE,
            HandoffStatus.BLOCKED,
            HandoffStatus.HANDOFF,
            HandoffStatus.FAILED,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "slice_id": self.slice_id,
            "status": self.status.value,
            "commit": self.commit,
            "changed_files": list(self.changed_files),
            "tests": list(self.tests),
            "evidence": list(self.evidence),
            "blocker": self.blocker,
            "next_slice": self.next_slice,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ParsedHandoff":
        if not isinstance(payload, dict):
            raise HandoffMailboxError("payload must be a dict")
        return cls.create(
            agent=_required(payload, "agent"),
            slice_id=_required(payload, "slice_id"),
            status=_required(payload, "status"),
            commit=payload.get("commit", ""),
            changed_files=payload.get("changed_files", ()),
            tests=payload.get("tests", ()),
            evidence=payload.get("evidence", ()),
            blocker=payload.get("blocker", ""),
            next_slice=payload.get("next_slice", ""),
        )


@dataclass(frozen=True, slots=True)
class MailboxMessage:
    message_id: str
    thread_ref: ThreadRef
    prompt_summary: str
    allowed_action: str
    source_handoff: ParsedHandoff
    status: MailboxMessageStatus = MailboxMessageStatus.QUEUED

    @classmethod
    def create(
        cls,
        *,
        thread_ref: ThreadRef,
        prompt_summary: Any,
        allowed_action: Any,
        source_handoff: ParsedHandoff,
        status: MailboxMessageStatus | str = MailboxMessageStatus.QUEUED,
    ) -> "MailboxMessage":
        if not isinstance(thread_ref, ThreadRef):
            raise HandoffMailboxError("thread_ref must be a ThreadRef")
        if not isinstance(source_handoff, ParsedHandoff):
            raise HandoffMailboxError("source_handoff must be a ParsedHandoff")
        normalized_prompt = _normalize_text(prompt_summary, field_name="prompt_summary", allow_empty=False)
        normalized_action = _normalize_allowed_action(allowed_action)
        normalized_status = _normalize_message_status(status)
        message_id = _message_id(thread_ref, normalized_prompt, normalized_action, source_handoff)
        return cls(
            message_id=message_id,
            thread_ref=thread_ref,
            prompt_summary=normalized_prompt,
            allowed_action=normalized_action,
            source_handoff=source_handoff,
            status=normalized_status,
        )

    def with_status(self, status: MailboxMessageStatus | str) -> "MailboxMessage":
        return MailboxMessage(
            message_id=self.message_id,
            thread_ref=self.thread_ref,
            prompt_summary=self.prompt_summary,
            allowed_action=self.allowed_action,
            source_handoff=self.source_handoff,
            status=_normalize_message_status(status),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "thread_ref": {
                "thread_id": self.thread_ref.thread_id,
                "agent_id": self.thread_ref.agent_id,
                "agent_run_id": self.thread_ref.agent_run_id,
                "plan_id": self.thread_ref.plan_id,
                "node_id": self.thread_ref.node_id,
            },
            "prompt_summary": self.prompt_summary,
            "allowed_action": self.allowed_action,
            "source_handoff": self.source_handoff.to_dict(),
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MailboxMessage":
        if not isinstance(payload, dict):
            raise HandoffMailboxError("payload must be a dict")
        thread_payload = _required(payload, "thread_ref")
        if not isinstance(thread_payload, dict):
            raise HandoffMailboxError("thread_ref must be a dict")
        message = cls.create(
            thread_ref=ThreadRef.create(
                thread_id=_required(thread_payload, "thread_id"),
                agent_id=_required(thread_payload, "agent_id"),
                agent_run_id=_required(thread_payload, "agent_run_id"),
                plan_id=_required(thread_payload, "plan_id"),
                node_id=_required(thread_payload, "node_id"),
            ),
            prompt_summary=_required(payload, "prompt_summary"),
            allowed_action=_required(payload, "allowed_action"),
            source_handoff=ParsedHandoff.from_dict(_required(payload, "source_handoff")),
            status=payload.get("status", MailboxMessageStatus.QUEUED),
        )
        expected_message_id = _required(payload, "message_id")
        if message.message_id != expected_message_id:
            raise HandoffMailboxError("message_id does not match message payload")
        return message


@dataclass(slots=True)
class DispatchMailbox:
    messages: dict[str, MailboxMessage] = field(default_factory=dict)

    def queue(self, message: MailboxMessage) -> None:
        if not isinstance(message, MailboxMessage):
            raise HandoffMailboxError("message must be a MailboxMessage")
        if message.message_id in self.messages:
            raise HandoffMailboxError(f"message already queued: {message.message_id}")
        self.messages[message.message_id] = message

    def queue_for_run(
        self,
        *,
        registry: ThreadRegistry,
        agent_run_id: str,
        expected_agent_id: str,
        expected_node_id: str,
        prompt_summary: str,
        allowed_action: str,
        source_handoff: ParsedHandoff,
    ) -> MailboxMessage:
        if not isinstance(registry, ThreadRegistry):
            raise HandoffMailboxError("registry must be a ThreadRegistry")
        try:
            thread_ref = registry.dispatch_target(
                agent_run_id=agent_run_id,
                expected_agent_id=expected_agent_id,
                expected_node_id=expected_node_id,
            )
        except ThreadRegistryError as exc:
            raise HandoffMailboxError(str(exc)) from exc
        message = MailboxMessage.create(
            thread_ref=thread_ref,
            prompt_summary=prompt_summary,
            allowed_action=allowed_action,
            source_handoff=source_handoff,
        )
        self.queue(message)
        return message

    def update_status(self, message_id: str, status: MailboxMessageStatus | str) -> None:
        if message_id not in self.messages:
            raise HandoffMailboxError(f"unknown message: {message_id}")
        self.messages[message_id] = self.messages[message_id].with_status(status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "messages": [
                message.to_dict()
                for message in sorted(self.messages.values(), key=lambda item: item.message_id)
            ],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DispatchMailbox":
        if not isinstance(payload, dict):
            raise HandoffMailboxError("payload must be a dict")
        if payload.get("schema_version") != 1:
            raise HandoffMailboxError("schema_version must be 1")
        messages = payload.get("messages")
        if not isinstance(messages, list):
            raise HandoffMailboxError("messages must be a list")
        mailbox = cls()
        for message_payload in messages:
            mailbox.queue(MailboxMessage.from_dict(message_payload))
        return mailbox

    def audit_summary(self) -> dict[str, Any]:
        queued = sum(1 for message in self.messages.values() if message.status == MailboxMessageStatus.QUEUED)
        return {
            "message_count": len(self.messages),
            "queued_count": queued,
            "message_ids": tuple(sorted(self.messages)),
        }


def parse_handoff_text(text: str) -> ParsedHandoff:
    fields = _parse_fields(text)
    missing = [field for field in ("agent", "slice_id", "status") if field not in fields]
    if missing:
        raise HandoffMailboxError(f"missing required handoff field: {missing[0]}")
    return ParsedHandoff.create(
        agent=fields["agent"],
        slice_id=fields["slice_id"],
        status=fields["status"],
        commit=fields.get("commit", ""),
        changed_files=fields.get("changed_files", ""),
        tests=fields.get("tests", ""),
        evidence=fields.get("evidence", ""),
        blocker=fields.get("blocker", ""),
        next_slice=fields.get("next_slice", ""),
    )


def _parse_fields(text: str) -> dict[str, str]:
    if not isinstance(text, str) or not text.strip():
        raise HandoffMailboxError("handoff text must not be empty")
    fields: dict[str, list[str]] = {}
    current_key = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^([^:]{2,40}):\s*(.*)$", line)
        if match:
            alias = _normalize_key(match.group(1))
            field = _FIELD_ALIASES.get(alias)
            if field:
                current_key = field
                fields.setdefault(current_key, [])
                if match.group(2).strip():
                    fields[current_key].append(match.group(2).strip())
                continue
        if current_key:
            fields[current_key].append(line)
    return {key: "\n".join(value).strip() for key, value in fields.items()}


def _normalize_key(value: str) -> str:
    key = value.strip().lower()
    key = key.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    key = re.sub(r"\s+", " ", key)
    return key


def _normalize_status(value: Any) -> HandoffStatus:
    raw = str(value or "").strip().lower().replace("_", "-")
    if not raw:
        raise HandoffMailboxError("status must not be empty")
    try:
        return _STATUS_ALIASES[raw]
    except KeyError as exc:
        raise HandoffMailboxError("status is not supported") from exc


def _normalize_message_status(value: MailboxMessageStatus | str) -> MailboxMessageStatus:
    if isinstance(value, MailboxMessageStatus):
        return value
    raw = str(value or "").strip().lower().replace("-", "_")
    try:
        return MailboxMessageStatus(raw)
    except ValueError as exc:
        raise HandoffMailboxError("message status is not supported") from exc


def _normalize_allowed_action(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized not in {"send", "resolve", "read"}:
        raise HandoffMailboxError("allowed_action is not supported")
    return normalized


def _normalize_commit(value: Any) -> str:
    raw = str(value or "").strip()
    if raw.lower() in {"", "-", "none", "n/a", "no"}:
        return ""
    if not _COMMIT_RE.fullmatch(raw):
        raise HandoffMailboxError("commit must be a 7-40 character hex ref")
    return raw.lower()


def _normalize_paths(value: Any) -> tuple[str, ...]:
    paths = _normalize_list(value, field_name="changed_files")
    normalized: list[str] = []
    for path in paths:
        item = path.replace("\\", "/").strip()
        if _ABS_WINDOWS_RE.match(path) or item.startswith("/") or ".." in item.split("/"):
            raise HandoffMailboxError("changed_files must be safe repo-relative paths")
        if not _SAFE_PATH_RE.fullmatch(item):
            raise HandoffMailboxError("changed_files contains unsupported characters")
        normalized.append(item)
    return tuple(dict.fromkeys(normalized))


def _normalize_list(value: Any, *, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        raw_items = [str(item) for item in value]
    else:
        text = str(value or "").strip()
        if not text or text.lower() in {"-", "none", "n/a"}:
            return ()
        raw_items = []
        for line in text.splitlines():
            raw_items.extend(line.split(","))
    items: list[str] = []
    for raw in raw_items:
        item = re.sub(r"^[-*]\s*", "", str(raw).strip())
        if not item or item.lower() in {"-", "none", "n/a"}:
            continue
        items.append(_normalize_text(item, field_name=field_name, allow_empty=False))
    if len(items) > _MAX_LIST_ITEMS:
        raise HandoffMailboxError(f"{field_name} has too many items")
    return tuple(dict.fromkeys(items))


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool) -> str:
    text = " ".join(str(value or "").split())
    if not text and not allow_empty:
        raise HandoffMailboxError(f"{field_name} must not be empty")
    if len(text) > _MAX_TEXT:
        text = text[: _MAX_TEXT - 3] + "..."
    return text


def _normalize_slug_text(value: Any, *, field_name: str, allow_empty: bool) -> str:
    text = _normalize_text(value, field_name=field_name, allow_empty=allow_empty)
    if not text:
        return ""
    if len(text) > 120:
        raise HandoffMailboxError(f"{field_name} exceeds max length 120")
    return text


def _message_id(
    thread_ref: ThreadRef,
    prompt_summary: str,
    allowed_action: str,
    source_handoff: ParsedHandoff,
) -> str:
    seed = "|".join(
        (
            thread_ref.thread_id,
            thread_ref.agent_run_id,
            thread_ref.node_id,
            prompt_summary,
            allowed_action,
            source_handoff.agent,
            source_handoff.slice_id,
            source_handoff.status.value,
            source_handoff.commit,
            source_handoff.next_slice,
        )
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _required(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise HandoffMailboxError(f"missing required field: {key}")
    return payload[key]
