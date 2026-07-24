"""Owner-scoped, atomic Todo operations on the existing Notes store.

This module is deliberately independent from tool routing.  Notes remain the
only domain authority; callers get stable, redacted references and content-free
postcondition evidence that later slices can turn into semantic receipts.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence
from urllib.parse import quote, unquote

from sqlalchemy import update
from sqlalchemy.exc import OperationalError

from core.database import Note, SessionLocal


_ITEM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_LIST_REF_PREFIX = "todo-list:v1:"
_ITEM_REF_PREFIX = "todo-item:v1:"


class TodoDomainError(RuntimeError):
    """Base class for fail-closed Todo domain errors."""


class TodoValidationError(TodoDomainError):
    """The caller supplied an invalid owner, reference, or mutation payload."""


class TodoListNotFoundError(TodoDomainError):
    """No active checklist exists for the owner-scoped list reference."""


class TodoDataIntegrityError(TodoDomainError):
    """Stored checklist data cannot be safely normalized or mutated."""


class TodoIdempotencyConflictError(TodoDomainError):
    """An idempotency key was replayed with a different add payload."""


class TodoConcurrencyError(TodoDomainError):
    """The optimistic mutation retry budget was exhausted."""


@dataclass(frozen=True)
class TodoItemView:
    item_ref: str
    text: str
    done: bool

    def as_dict(self) -> dict:
        return {
            "item_ref": self.item_ref,
            "text": self.text,
            "done": self.done,
        }


@dataclass(frozen=True)
class TodoListSnapshot:
    list_ref: str
    title: str
    items: tuple[TodoItemView, ...]
    open_count: int
    version: str

    def as_dict(self) -> dict:
        return {
            "list_ref": self.list_ref,
            "title": self.title,
            "items": [item.as_dict() for item in self.items],
            "open_count": self.open_count,
            "version": self.version,
        }


@dataclass(frozen=True)
class TodoMutationOutcome:
    operation: str
    list_ref: str
    item_ref: str | None
    transaction_status: str
    previous_state: dict
    current_state: dict
    open_count: int
    verified: bool
    candidate_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    @property
    def mutated(self) -> bool:
        return self.transaction_status == "committed"

    def as_dict(self) -> dict:
        return {
            "operation": self.operation,
            "list_ref": self.list_ref,
            "item_ref": self.item_ref,
            "transaction_status": self.transaction_status,
            "previous_state": dict(self.previous_state),
            "current_state": dict(self.current_state),
            "open_count": self.open_count,
            "verified": self.verified,
            "candidate_refs": list(self.candidate_refs),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class _StoredItem:
    item_id: str
    text: str
    done: bool
    persisted_id: bool
    has_unknown_fields: bool

    @property
    def item_ref(self) -> str:
        return f"{_ITEM_REF_PREFIX}{self.item_id}"


def _owner_scope_ref(owner: str) -> str:
    return hashlib.sha256(f"todo-owner:v1\0{owner}".encode("utf-8")).hexdigest()[:16]


def make_list_ref(owner: str, note_id: str) -> str:
    """Build a stable list ref without exposing the owner identifier."""
    owner = _require_nonempty(owner, "owner", max_length=512)
    note_id = _require_nonempty(note_id, "note_id", max_length=512)
    return f"{_LIST_REF_PREFIX}{_owner_scope_ref(owner)}:{quote(note_id, safe='-._~')}"


def _parse_list_ref(owner: str, list_ref: str) -> str:
    owner = _require_nonempty(owner, "owner", max_length=512)
    list_ref = _require_nonempty(list_ref, "list_ref", max_length=1200)
    if not list_ref.startswith(_LIST_REF_PREFIX):
        raise TodoValidationError("Invalid Todo list reference")
    remainder = list_ref[len(_LIST_REF_PREFIX):]
    scope_ref, separator, encoded_note_id = remainder.partition(":")
    if (
        separator != ":"
        or scope_ref != _owner_scope_ref(owner)
        or not encoded_note_id
    ):
        # Do not reveal whether a list exists in a different owner scope.
        raise TodoListNotFoundError("Todo list not found")
    note_id = unquote(encoded_note_id)
    if not note_id or quote(note_id, safe="-._~") != encoded_note_id:
        raise TodoValidationError("Invalid Todo list reference")
    return note_id


def _require_nonempty(value: str, field: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise TodoValidationError(f"{field} must be a non-empty string")
    return value


def _item_id_for_add(list_ref: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(
        f"todo-add:v1\0{list_ref}\0{idempotency_key}".encode("utf-8")
    ).hexdigest()[:32]
    return f"itm_{digest}"


def _legacy_item_id(note_id: str, index: int, text: str, done: bool) -> str:
    payload = json.dumps(
        {"note_id": note_id, "index": index, "text": text, "done": done},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"itm_{hashlib.sha256(f'todo-legacy:v1\0{payload}'.encode('utf-8')).hexdigest()[:32]}"


def _version_for(raw_items: str | None) -> str:
    return hashlib.sha256((raw_items or "").encode("utf-8")).hexdigest()[:24]


def _normalize_match_text(value: str) -> str:
    return " ".join(value.split()).casefold()


def _decode_items(raw_items: str | None, note_id: str) -> tuple[list[_StoredItem], str]:
    if raw_items in (None, ""):
        payload = []
    else:
        try:
            payload = json.loads(raw_items)
        except (TypeError, ValueError) as exc:
            raise TodoDataIntegrityError("Checklist items are not valid JSON") from exc
    if not isinstance(payload, list):
        raise TodoDataIntegrityError("Checklist items must be a JSON array")

    items: list[_StoredItem] = []
    persisted_flags: list[bool] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise TodoDataIntegrityError("Checklist item must be an object")
        text = item.get("text")
        done = item.get("done", False)
        if not isinstance(text, str) or not text.strip() or not isinstance(done, bool):
            raise TodoDataIntegrityError("Checklist item has invalid text or done state")
        persisted_id = "id" in item
        item_id = item.get("id") if persisted_id else _legacy_item_id(note_id, index, text, done)
        if not isinstance(item_id, str) or not _ITEM_ID_RE.fullmatch(item_id):
            raise TodoDataIntegrityError("Checklist item has an invalid stable id")
        if item_id in seen_ids:
            raise TodoDataIntegrityError("Checklist item ids must be unique")
        seen_ids.add(item_id)
        persisted_flags.append(persisted_id)
        items.append(
            _StoredItem(
                item_id=item_id,
                text=text,
                done=done,
                persisted_id=persisted_id,
                has_unknown_fields=bool(set(item) - {"id", "text", "done"}),
            )
        )

    if not persisted_flags:
        shape = "empty"
    elif all(persisted_flags):
        shape = "canonical"
    elif any(persisted_flags):
        shape = "mixed"
    else:
        shape = "legacy"
    return items, shape


def _encode_canonical_items(items: Iterable[_StoredItem]) -> str:
    return json.dumps(
        [{"id": item.item_id, "text": item.text, "done": item.done} for item in items],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _state(item: _StoredItem | None) -> dict:
    return {
        "exists": item is not None,
        "done": item.done if item is not None else None,
    }


class TodoDomainService:
    """Atomic owner-scoped Todo operations backed by ``core.database.Note``."""

    def __init__(
        self,
        session_factory: Callable = SessionLocal,
        *,
        max_retries: int = 8,
        retry_backoff_seconds: float = 0.005,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_retries < 1:
            raise ValueError("max_retries must be at least one")
        self._session_factory = session_factory
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._sleep = sleep

    def list_items(self, *, owner: str, list_ref: str) -> TodoListSnapshot:
        owner = _require_nonempty(owner, "owner", max_length=512)
        note_id = _parse_list_ref(owner, list_ref)
        db = self._session_factory()
        try:
            note = self._find_note(db, owner=owner, note_id=note_id)
            return self._snapshot(note, list_ref)
        finally:
            db.close()

    def add_item(
        self,
        *,
        owner: str,
        list_ref: str,
        text: str,
        idempotency_key: str,
    ) -> TodoMutationOutcome:
        text = _require_nonempty(text, "text", max_length=10_000)
        return self._mutate(
            owner=owner,
            list_ref=list_ref,
            operation="add_item",
            idempotency_key=idempotency_key,
            add_text=text,
        )

    def complete_item(
        self,
        *,
        owner: str,
        list_ref: str,
        idempotency_key: str,
        item_ref: str | None = None,
        text: str | None = None,
    ) -> TodoMutationOutcome:
        return self._mutate(
            owner=owner,
            list_ref=list_ref,
            operation="complete_item",
            idempotency_key=idempotency_key,
            item_ref=item_ref,
            match_text=text,
        )

    def reopen_item(
        self,
        *,
        owner: str,
        list_ref: str,
        idempotency_key: str,
        item_ref: str | None = None,
        text: str | None = None,
    ) -> TodoMutationOutcome:
        return self._mutate(
            owner=owner,
            list_ref=list_ref,
            operation="reopen_item",
            idempotency_key=idempotency_key,
            item_ref=item_ref,
            match_text=text,
        )

    def remove_item(
        self,
        *,
        owner: str,
        list_ref: str,
        idempotency_key: str,
        item_ref: str | None = None,
        text: str | None = None,
    ) -> TodoMutationOutcome:
        return self._mutate(
            owner=owner,
            list_ref=list_ref,
            operation="remove_item",
            idempotency_key=idempotency_key,
            item_ref=item_ref,
            match_text=text,
        )

    @staticmethod
    def _find_note(db, *, owner: str, note_id: str):
        note = (
            db.query(Note)
            .filter(
                Note.id == note_id,
                Note.owner == owner,
                Note.note_type == "checklist",
                Note.archived.is_(False),
            )
            .first()
        )
        if note is None:
            raise TodoListNotFoundError("Todo list not found")
        return note

    @staticmethod
    def _snapshot(note, list_ref: str) -> TodoListSnapshot:
        items, _shape = _decode_items(note.items, note.id)
        views = tuple(
            TodoItemView(item_ref=item.item_ref, text=item.text, done=item.done)
            for item in items
        )
        return TodoListSnapshot(
            list_ref=list_ref,
            title=note.title or "",
            items=views,
            open_count=sum(not item.done for item in items),
            version=_version_for(note.items),
        )

    @staticmethod
    def _resolve_target(
        items: Sequence[_StoredItem],
        *,
        item_ref: str | None,
        match_text: str | None,
    ) -> tuple[_StoredItem | None, tuple[str, ...]]:
        if (item_ref is None) == (match_text is None):
            raise TodoValidationError("Provide exactly one of item_ref or text")
        if item_ref is not None:
            item_ref = _require_nonempty(item_ref, "item_ref", max_length=256)
            if not item_ref.startswith(_ITEM_REF_PREFIX):
                raise TodoValidationError("Invalid Todo item reference")
            item_id = item_ref[len(_ITEM_REF_PREFIX):]
            if not _ITEM_ID_RE.fullmatch(item_id):
                raise TodoValidationError("Invalid Todo item reference")
            matches = [item for item in items if item.item_id == item_id]
        else:
            match_text = _require_nonempty(match_text, "text", max_length=10_000)
            normalized = _normalize_match_text(match_text)
            matches = [item for item in items if _normalize_match_text(item.text) == normalized]
        if len(matches) == 1:
            return matches[0], ()
        return None, tuple(item.item_ref for item in matches)

    @staticmethod
    def _readback_evidence(snapshot: TodoListSnapshot) -> tuple[str, ...]:
        return (f"notes-readback:v1:{snapshot.version}",)

    def _mutate(
        self,
        *,
        owner: str,
        list_ref: str,
        operation: str,
        idempotency_key: str,
        add_text: str | None = None,
        item_ref: str | None = None,
        match_text: str | None = None,
    ) -> TodoMutationOutcome:
        owner = _require_nonempty(owner, "owner", max_length=512)
        note_id = _parse_list_ref(owner, list_ref)
        idempotency_key = _require_nonempty(
            idempotency_key, "idempotency_key", max_length=1024
        )

        for attempt in range(self._max_retries):
            db = self._session_factory()
            try:
                note = self._find_note(db, owner=owner, note_id=note_id)
                original_raw = note.items
                items, shape = _decode_items(original_raw, note.id)
                if shape == "mixed":
                    raise TodoDataIntegrityError(
                        "Mixed legacy and canonical item ids require an explicit repair"
                    )
                if any(item.has_unknown_fields for item in items):
                    raise TodoDataIntegrityError(
                        "Checklist item contains fields the canonical writer cannot preserve"
                    )

                before_open_count = sum(not item.done for item in items)
                evidence_refs: tuple[str, ...] = ()
                if shape == "legacy":
                    evidence_refs = (
                        f"notes-pre-upgrade:v1:{_version_for(original_raw)}",
                    )

                if operation == "add_item":
                    target_id = _item_id_for_add(list_ref, idempotency_key)
                    existing = next((item for item in items if item.item_id == target_id), None)
                    if existing is not None:
                        if existing.text != add_text:
                            raise TodoIdempotencyConflictError(
                                "Idempotency key was already used with a different Todo item"
                            )
                        return TodoMutationOutcome(
                            operation=operation,
                            list_ref=list_ref,
                            item_ref=existing.item_ref,
                            transaction_status="idempotent",
                            previous_state=_state(existing),
                            current_state=_state(existing),
                            open_count=before_open_count,
                            verified=True,
                            evidence_refs=(f"notes-readback:v1:{_version_for(original_raw)}",),
                        )
                    previous_state = _state(None)
                    target = _StoredItem(
                        item_id=target_id,
                        text=add_text or "",
                        done=False,
                        persisted_id=True,
                        has_unknown_fields=False,
                    )
                    new_items = [*items, target]
                    current_state = _state(target)
                else:
                    target, candidates = self._resolve_target(
                        items, item_ref=item_ref, match_text=match_text
                    )
                    if target is None:
                        status = "ambiguous" if len(candidates) > 1 else "not_found"
                        return TodoMutationOutcome(
                            operation=operation,
                            list_ref=list_ref,
                            item_ref=None,
                            transaction_status=status,
                            previous_state=_state(None),
                            current_state=_state(None),
                            open_count=before_open_count,
                            verified=False,
                            candidate_refs=candidates,
                        )
                    previous_state = _state(target)
                    target_index = next(
                        index for index, candidate in enumerate(items)
                        if candidate.item_id == target.item_id
                    )
                    new_items = list(items)
                    if operation in {"complete_item", "reopen_item"}:
                        desired_done = operation == "complete_item"
                        if target.done == desired_done:
                            return TodoMutationOutcome(
                                operation=operation,
                                list_ref=list_ref,
                                item_ref=target.item_ref,
                                transaction_status="idempotent",
                                previous_state=previous_state,
                                current_state=previous_state,
                                open_count=before_open_count,
                                verified=True,
                                evidence_refs=(
                                    f"notes-readback:v1:{_version_for(original_raw)}",
                                ),
                            )
                        target = _StoredItem(
                            item_id=target.item_id,
                            text=target.text,
                            done=desired_done,
                            persisted_id=True,
                            has_unknown_fields=False,
                        )
                        new_items[target_index] = target
                        current_state = _state(target)
                    elif operation == "remove_item":
                        del new_items[target_index]
                        current_state = _state(None)
                    else:
                        raise TodoValidationError("Unsupported Todo operation")

                new_raw = _encode_canonical_items(new_items)
                statement = (
                    update(Note)
                    .where(
                        Note.id == note_id,
                        Note.owner == owner,
                        Note.note_type == "checklist",
                        Note.archived.is_(False),
                        Note.items == original_raw,
                    )
                    .values(items=new_raw)
                )
                result = db.execute(statement)
                if result.rowcount != 1:
                    db.rollback()
                    self._backoff(attempt)
                    continue
                db.commit()
                target_ref = target.item_ref
            except OperationalError as exc:
                db.rollback()
                if not self._retryable_operational_error(exc) or attempt + 1 >= self._max_retries:
                    raise
                self._backoff(attempt)
                continue
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

            snapshot = self.list_items(owner=owner, list_ref=list_ref)
            persisted = next(
                (entry for entry in snapshot.items if entry.item_ref == target_ref),
                None,
            )
            if operation == "remove_item":
                verified = persisted is None
            else:
                verified = (
                    persisted is not None
                    and persisted.done == current_state["done"]
                )
            return TodoMutationOutcome(
                operation=operation,
                list_ref=list_ref,
                item_ref=target_ref,
                transaction_status="committed",
                previous_state=previous_state,
                current_state=current_state,
                open_count=snapshot.open_count,
                verified=verified,
                evidence_refs=(*evidence_refs, *self._readback_evidence(snapshot)),
            )

        raise TodoConcurrencyError("Todo list changed too often; no mutation was applied")

    @staticmethod
    def _retryable_operational_error(exc: OperationalError) -> bool:
        message = str(exc).casefold()
        return "locked" in message or "serialization" in message or "deadlock" in message

    def _backoff(self, attempt: int) -> None:
        if self._retry_backoff_seconds > 0:
            self._sleep(self._retry_backoff_seconds * (2 ** min(attempt, 6)))


__all__ = [
    "TodoConcurrencyError",
    "TodoDataIntegrityError",
    "TodoDomainError",
    "TodoDomainService",
    "TodoIdempotencyConflictError",
    "TodoItemView",
    "TodoListNotFoundError",
    "TodoListSnapshot",
    "TodoMutationOutcome",
    "TodoValidationError",
    "make_list_ref",
]
