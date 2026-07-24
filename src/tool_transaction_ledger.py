"""Machine-readable ledger records for effectful tool transactions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
from pathlib import PurePosixPath
import re
from typing import Any, Iterable, Mapping

from src.todo_transaction_receipts import TODO_TOOL_NAME, validated_todo_semantic_receipt_from_event


TOOL_TRANSACTION_LEDGER_SCHEMA = "odysseus.tool_transaction_ledger.v1"

_MAX_TOKEN = 96
_MAX_REF = 180
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,180}$")
_SECRET_RE = re.compile(r"(?i)(authorization|cookie|api[_-]?key|password|passwd|secret|token|bearer\s+[A-Za-z0-9._-]{8,})")
_HOST_PATH_RE = re.compile(r"(?i)(^[a-z]:[\\/]|^/|/home/|/opt/|/users/|~[\\/])")
_ERROR_RE = re.compile(r"\b(error|failed|exception|traceback|fehler|gescheitert|abgelehnt)\b", re.IGNORECASE)
_BLOCKED_RE = re.compile(r"\b(blocked|refused|denied|not allowed|abgelehnt|gesperrt|verboten)\b", re.IGNORECASE)
_TEST_RE = re.compile(r"\b(pytest|unittest|npm\s+test|npm\s+run\s+test|pnpm\s+test|yarn\s+test|vitest|playwright\s+test)\b", re.IGNORECASE)
_TELEGRAM_RE = re.compile(r"\btelegram\b", re.IGNORECASE)
_SANDBOX_RE = re.compile(r"\bsandbox\b", re.IGNORECASE)
_FILE_WRITE_TOOL_RE = re.compile(r"^(create_document|update_document|edit_document|write_file|create_file|edit_file)$", re.IGNORECASE)
_PATH_TOKEN_RE = re.compile(
    r"`([^`]{1,220})`|(?:^|[\s(\"'])([A-Za-z0-9][A-Za-z0-9._/-]{0,180}\.[A-Za-z0-9]{1,12})(?=$|[\s),.!?\"'])"
)


class ToolTransactionError(ValueError):
    """Raised when a transaction would persist unsafe or invalid data."""


class ToolTransactionStatus(StrEnum):
    PLANNED = "planned"
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    VERIFIED = "verified"


@dataclass(frozen=True, slots=True)
class ToolTransaction:
    transaction_id: str
    surface: str
    tool: str
    claim_type: str
    status: ToolTransactionStatus
    evidence_refs: tuple[str, ...]
    exit_code: int | None
    artifact_refs: tuple[str, ...]
    command_hash: str
    raw_content_visible: bool = False
    schema: str = TOOL_TRANSACTION_LEDGER_SCHEMA

    @classmethod
    def create(
        cls,
        *,
        surface: Any,
        tool: Any,
        claim_type: Any,
        status: ToolTransactionStatus | str,
        evidence_refs: Iterable[Any] = (),
        exit_code: Any = None,
        artifact_refs: Iterable[Any] = (),
        command: Any = "",
        transaction_id: Any = "",
    ) -> "ToolTransaction":
        normalized_status = status if isinstance(status, ToolTransactionStatus) else ToolTransactionStatus(str(status))
        parsed_exit_code = _exit_code(exit_code)
        safe_surface = _safe_token(surface, "surface")
        safe_tool = _safe_token(tool, "tool")
        safe_claim = _safe_token(claim_type, "claim_type")
        safe_evidence = _safe_ref_tuple(evidence_refs, field="evidence_ref", allow_colon=True)
        safe_artifacts = _safe_ref_tuple(artifact_refs, field="artifact_ref", allow_colon=False)
        command_hash = _hash_text(command)
        tx_id = _safe_transaction_id(
            transaction_id,
            fallback=f"{safe_surface}:{safe_tool}:{safe_claim}:{normalized_status}:{command_hash}:{parsed_exit_code}",
        )
        return cls(
            transaction_id=tx_id,
            surface=safe_surface,
            tool=safe_tool,
            claim_type=safe_claim,
            status=normalized_status,
            evidence_refs=safe_evidence,
            exit_code=parsed_exit_code,
            artifact_refs=safe_artifacts,
            command_hash=command_hash,
            raw_content_visible=False,
        )

    @property
    def verified_done(self) -> bool:
        return self.status in {ToolTransactionStatus.SUCCEEDED, ToolTransactionStatus.VERIFIED} and bool(
            self.evidence_refs or self.artifact_refs
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "transaction_id": self.transaction_id,
            "surface": self.surface,
            "tool": self.tool,
            "claim_type": self.claim_type,
            "status": self.status.value,
            "evidence_refs": self.evidence_refs,
            "exit_code": self.exit_code,
            "artifact_refs": self.artifact_refs,
            "command_hash": self.command_hash,
            "verified_done": self.verified_done,
            "raw_content_visible": False,
        }


def transactions_from_tool_events(
    tool_events: Iterable[Mapping[str, Any]],
    *,
    surface: str = "agent",
) -> tuple[ToolTransaction, ...]:
    transactions: list[ToolTransaction] = []
    seen: set[str] = set()
    for index, event in enumerate(item for item in tool_events if isinstance(item, Mapping)):
        for claim_type in _claim_types_for_event(event):
            try:
                tx = transaction_from_tool_event(event, surface=surface, claim_type=claim_type, index=index)
            except ToolTransactionError:
                continue
            if tx.transaction_id not in seen:
                seen.add(tx.transaction_id)
                transactions.append(tx)
    return tuple(transactions)


def transaction_from_tool_event(
    event: Mapping[str, Any],
    *,
    surface: str = "agent",
    claim_type: str | None = None,
    index: int = 0,
) -> ToolTransaction:
    todo_receipt = validated_todo_semantic_receipt_from_event(event)
    if event.get("tool") == TODO_TOOL_NAME and todo_receipt is None:
        raise ToolTransactionError("Todo event requires a valid semantic receipt")
    if todo_receipt is not None:
        requested_claim = claim_type or todo_receipt["claim_type"]
        if requested_claim != todo_receipt["claim_type"]:
            raise ToolTransactionError("Todo claim does not match its semantic receipt")
        return ToolTransaction.create(
            surface=surface,
            tool=TODO_TOOL_NAME,
            claim_type=todo_receipt["claim_type"],
            status=ToolTransactionStatus.VERIFIED,
            evidence_refs=todo_receipt["evidence_refs"],
            exit_code=0,
            command="",
            transaction_id=f"{surface}:{index}:{TODO_TOOL_NAME}:{todo_receipt['claim_type']}",
        )
    tool = str(event.get("tool") or "tool").strip() or "tool"
    command = str(event.get("command") or "")
    output = str(event.get("output") or event.get("stdout") or event.get("stderr") or "")
    inferred_claim = claim_type or _claim_types_for_event(event)[0]
    evidence_refs = _evidence_refs_for_event(event, inferred_claim)
    artifact_refs = _artifact_refs_for_event(event)
    status = _status_for_event(event, output=output, evidence_refs=evidence_refs, artifact_refs=artifact_refs)
    return ToolTransaction.create(
        surface=surface,
        tool=tool,
        claim_type=inferred_claim,
        status=status,
        evidence_refs=evidence_refs,
        exit_code=event.get("exit_code"),
        artifact_refs=artifact_refs,
        command=command,
        transaction_id=f"{surface}:{index}:{tool}:{inferred_claim}:{_hash_text(command)[:20]}",
    )


def transaction_evidence_for_claim(
    transactions: Iterable[Mapping[str, Any] | ToolTransaction],
    claim_type: str,
    needles: Iterable[str] = (),
) -> tuple[str, ...]:
    wanted = str(claim_type or "").strip()
    lowered_needles = tuple(str(needle or "").lower() for needle in needles if str(needle or "").strip())
    refs: list[str] = []
    seen: set[str] = set()
    for item in transactions:
        tx = item if isinstance(item, ToolTransaction) else _transaction_from_mapping(item)
        if tx is None or tx.claim_type != wanted:
            continue
        if not tx.verified_done:
            continue
        haystack = " ".join((*tx.evidence_refs, *tx.artifact_refs)).lower()
        if lowered_needles and not any(needle in haystack for needle in lowered_needles):
            continue
        for ref in (*tx.artifact_refs, *tx.evidence_refs, tx.transaction_id):
            if ref and ref not in seen:
                seen.add(ref)
                refs.append(ref)
    return tuple(refs)


def _transaction_from_mapping(item: Mapping[str, Any]) -> ToolTransaction | None:
    try:
        return ToolTransaction.create(
            surface=item.get("surface"),
            tool=item.get("tool"),
            claim_type=item.get("claim_type"),
            status=item.get("status"),
            evidence_refs=item.get("evidence_refs") or (),
            exit_code=item.get("exit_code"),
            artifact_refs=item.get("artifact_refs") or (),
            command=item.get("command_hash") or "",
            transaction_id=item.get("transaction_id") or "",
        )
    except (ToolTransactionError, ValueError):
        return None


def _claim_types_for_event(event: Mapping[str, Any]) -> tuple[str, ...]:
    todo_receipt = validated_todo_semantic_receipt_from_event(event)
    if todo_receipt is not None:
        return (todo_receipt["claim_type"],)
    if event.get("tool") == TODO_TOOL_NAME:
        return ()
    text = _event_text(event)
    tool = str(event.get("tool") or "").strip()
    claims: list[str] = []
    if _TEST_RE.search(text):
        claims.append("command_passed")
    if _TELEGRAM_RE.search(text) or tool.startswith("telegram_"):
        claims.append("telegram_sent")
    if _SANDBOX_RE.search(text):
        claims.append("sandbox_succeeded")
    if _FILE_WRITE_TOOL_RE.search(tool) or event.get("doc_id") or event.get("diff") or _extract_repo_paths(text):
        claims.append("file_changed")
    if any(str(ref).lower().endswith((".png", ".jpg", ".jpeg", ".webp")) for ref in _artifact_refs_for_event(event)):
        claims.append("artifact_exists")
    if not claims:
        claims.append("tool_execution")
    return tuple(dict.fromkeys(claims))


def _status_for_event(
    event: Mapping[str, Any],
    *,
    output: str,
    evidence_refs: tuple[str, ...],
    artifact_refs: tuple[str, ...],
) -> ToolTransactionStatus:
    exit_code = _exit_code(event.get("exit_code"))
    text = _event_text(event)
    if _BLOCKED_RE.search(text):
        return ToolTransactionStatus.BLOCKED
    if exit_code not in (None, 0) or _ERROR_RE.search(output):
        return ToolTransactionStatus.FAILED
    if artifact_refs:
        return ToolTransactionStatus.VERIFIED
    if evidence_refs:
        return ToolTransactionStatus.SUCCEEDED
    return ToolTransactionStatus.STARTED


def _evidence_refs_for_event(event: Mapping[str, Any], claim_type: str) -> tuple[str, ...]:
    refs: list[str] = []
    exit_code = _exit_code(event.get("exit_code"))
    if exit_code is not None:
        refs.append(f"exit_code:{exit_code}")
    command = str(event.get("command") or "")
    if command.strip():
        refs.append(f"command:{_hash_text(command)}")
    if event.get("doc_id"):
        refs.append(f"doc:{_safe_token(event.get('doc_id'), 'doc_id')}")
    if event.get("image_url"):
        refs.append(f"image:{_hash_text(event.get('image_url'))}")
    for path in _extract_repo_paths(_event_text(event)):
        refs.append(f"path:{path}")
    if claim_type == "tool_execution" and not refs:
        refs.append(f"tool:{_safe_token(event.get('tool') or 'tool', 'tool')}")
    return _safe_ref_tuple(refs, field="evidence_ref", allow_colon=True)


def _artifact_refs_for_event(event: Mapping[str, Any]) -> tuple[str, ...]:
    raw_refs: list[Any] = []
    for key in ("artifact_ref", "file_path", "path"):
        if event.get(key):
            raw_refs.append(event[key])
    for key in ("artifact_refs", "artifacts"):
        values = event.get(key) or ()
        if isinstance(values, (list, tuple)):
            raw_refs.extend(values)
    for path in _extract_repo_paths(str(event.get("output") or "")):
        if path.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".log", ".json", ".txt")):
            raw_refs.append(path)
    return _safe_ref_tuple(raw_refs, field="artifact_ref", allow_colon=False, skip_unsafe=True)


def _event_text(event: Mapping[str, Any]) -> str:
    return " ".join(str(event.get(key) or "") for key in ("tool", "command", "output", "stdout", "stderr"))


def _extract_repo_paths(text: str) -> tuple[str, ...]:
    paths: list[str] = []
    seen: set[str] = set()
    for match in _PATH_TOKEN_RE.finditer(str(text or "")):
        raw = (match.group(1) or match.group(2) or "").strip()
        path = _safe_repo_path(raw)
        if path and path not in seen:
            seen.add(path)
            paths.append(path)
    return tuple(paths)


def _safe_repo_path(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text or text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        return ""
    parts = PurePosixPath(text).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return ""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,220}", text):
        return ""
    return "/".join(parts)


def _safe_ref_tuple(
    values: Iterable[Any],
    *,
    field: str,
    allow_colon: bool,
    skip_unsafe: bool = False,
) -> tuple[str, ...]:
    refs: list[str] = []
    seen: set[str] = set()
    for value in values:
        try:
            ref = _safe_ref(value, field=field, allow_colon=allow_colon)
        except ToolTransactionError:
            if skip_unsafe:
                continue
            raise
        if ref and ref not in seen:
            seen.add(ref)
            refs.append(ref)
    return tuple(refs)


def _safe_ref(value: Any, *, field: str, allow_colon: bool) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    if len(text) > _MAX_REF:
        raise ToolTransactionError(f"{field} exceeds max length")
    if _SECRET_RE.search(text) or _HOST_PATH_RE.search(text):
        raise ToolTransactionError(f"{field} contains unsafe content")
    pattern = _SAFE_REF_RE if allow_colon else re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,180}$")
    if not pattern.fullmatch(text):
        raise ToolTransactionError(f"{field} contains unsafe characters")
    return text


def _safe_token(value: Any, field: str) -> str:
    text = str(value or "").strip().replace(" ", "_")
    if not text:
        raise ToolTransactionError(f"{field} must not be empty")
    if len(text) > _MAX_TOKEN or not _SAFE_TOKEN_RE.fullmatch(text):
        raise ToolTransactionError(f"{field} is not a safe token")
    return text


def _safe_transaction_id(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip()
    if text and len(text) <= 140 and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,139}", text):
        return text
    return f"tx:{_hash_text(fallback)}"


def _hash_text(value: Any) -> str:
    return "sha256:" + hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:32]


def _exit_code(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return max(0, min(int(value), 255))
