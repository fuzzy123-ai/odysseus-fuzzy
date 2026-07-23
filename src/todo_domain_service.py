"""Owner-isolated, content-safe Todo operations over ``Note.items`` JSON.

No ``core.database`` import happens at module load: callers inject both the
session factory and model, keeping this domain service independent of any live
application database.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re
from typing import Any, Callable, Optional, Sequence
from uuid import UUID, uuid5

from sqlalchemy import update

_ID_MAX, _TEXT_MAX, _KEY_MAX = 256, 5000, 256
_LEGACY_NS = UUID("8c8b9c31-43fa-4eb9-824b-aec71630c7a2")
_IDEMPOTENCY_NS = UUID("f2ff8136-965b-4fe4-a1cc-8e523f6d69cb")
_FRONTEND_ITEM_REF = re.compile(r"[a-z0-9]{8}", flags=re.ASCII)


class TodoDomainError(Exception):
    """Public errors deliberately never include private item text."""


class TodoValidationError(TodoDomainError): pass
class TodoNotFoundError(TodoDomainError): pass
class TodoConflictError(TodoDomainError): pass
class TodoDataError(TodoDomainError): pass
class TodoIdempotencyConflictError(TodoDomainError): pass


class TodoAmbiguousMatchError(TodoDomainError):
    def __init__(self, candidate_refs: Sequence[str]):
        self.candidate_refs = tuple(candidate_refs)
        super().__init__("item text selector is ambiguous; use an exact item_ref")


@dataclass(frozen=True)
class TodoItemSnapshot:
    item_ref: Optional[str]
    completed: bool


@dataclass(frozen=True)
class TodoListSnapshot:
    """A list read is content-free; legacy rows are read without changing them."""
    list_ref: str
    items: tuple[TodoItemSnapshot, ...]
    open_count: int
    def to_dict(self) -> dict[str, Any]: return asdict(self)


@dataclass(frozen=True)
class TodoReceipt:
    """Typed, content-free receipt for a mutation."""
    list_ref: str
    item_ref: str
    operation: str
    previous_state: Optional[bool]
    current_state: Optional[bool]
    open_count: int
    transaction_status: str
    verified: bool
    evidence_refs_redacted: tuple[str, ...]
    def to_dict(self) -> dict[str, Any]: return asdict(self)


class TodoDomainService:
    """Bounded CAS mutations. A CAS miss rolls back and re-reads fresh state."""
    def __init__(self, session_factory: Callable[[], Any], note_model: Any, *, max_retries: int = 3):
        if not callable(session_factory) or note_model is None:
            raise TodoValidationError("session_factory and note_model are required")
        if not isinstance(max_retries, int) or not 1 <= max_retries <= 10:
            raise TodoValidationError("max_retries must be between 1 and 10")
        self._session_factory, self._Note, self._max_retries = session_factory, note_model, max_retries

    @classmethod
    def from_core_database(cls, *, max_retries: int = 3) -> "TodoDomainService":
        """Opt-in convenience constructor; core is imported only at this call site."""
        from core.database import Note, SessionLocal
        return cls(SessionLocal, Note, max_retries=max_retries)

    def list(self, *, owner: Optional[str], list_ref: str) -> TodoListSnapshot:
        owner, list_ref = _owner(owner), _ident(list_ref, "list_ref")
        session = self._session_factory()
        try:
            note = self._note(session, owner, list_ref)
            if note is None: raise TodoNotFoundError("todo list not found")
            items = _decode(note.items)
            _validate_read_items(items)
            snapshots = tuple(TodoItemSnapshot(_stored_ref(i), _state(i)) for i in items)
            return TodoListSnapshot(list_ref, snapshots, sum(not i.completed for i in snapshots))
        except TodoDomainError:
            raise
        except Exception:
            raise TodoDomainError("todo list read failed") from None
        finally:
            session.close()

    def add(self, *, owner: Optional[str], list_ref: str, text: str, idempotency_key: str) -> TodoReceipt:
        owner, list_ref, text, key = _owner(owner), _ident(list_ref, "list_ref"), _text(text), _key(idempotency_key)
        digest = sha256(json.dumps({"text": text}, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        idempotency_ref = _idempotency_ref(owner, list_ref, key)
        item_ref = _idempotent_item_ref(owner, list_ref, idempotency_ref)
        def apply(items):
            prior = [i for i in items if i.get("idempotency_ref") == idempotency_ref or i.get("id") == item_ref]
            if prior:
                prior_item = prior[0] if len(prior) == 1 else None
                if (prior_item is None or prior_item.get("text") != text or
                    (prior_item.get("idempotency_payload_hash") is not None and
                     prior_item.get("idempotency_payload_hash") != digest)):
                    raise TodoIdempotencyConflictError("idempotency key conflicts with existing request")
                return prior_item["id"], None, _state(prior_item), True
            items.append({"id": item_ref, "text": text, "done": False,
                          "idempotency_ref": idempotency_ref, "idempotency_payload_hash": digest})
            return item_ref, None, False, False
        return self._mutate(owner, list_ref, "add", apply)

    def complete(self, *, owner: Optional[str], list_ref: str, item_ref: Optional[str] = None, text: Optional[str] = None) -> TodoReceipt:
        return self._set(owner, list_ref, item_ref, text, True, "complete")

    def reopen(self, *, owner: Optional[str], list_ref: str, item_ref: Optional[str] = None, text: Optional[str] = None) -> TodoReceipt:
        return self._set(owner, list_ref, item_ref, text, False, "reopen")

    def remove(self, *, owner: Optional[str], list_ref: str, item_ref: Optional[str] = None, text: Optional[str] = None) -> TodoReceipt:
        owner, list_ref, selector = _owner(owner), _ident(list_ref, "list_ref"), _selector(item_ref, text)
        def apply(items):
            item = items.pop(_find(items, selector))
            return item["id"], _state(item), None, False
        return self._mutate(owner, list_ref, "remove", apply)

    def _set(self, owner, list_ref, item_ref, text, value, operation):
        owner, list_ref, selector = _owner(owner), _ident(list_ref, "list_ref"), _selector(item_ref, text)
        def apply(items):
            item = items[_find(items, selector)]
            previous = _state(item)
            item["done"] = value
            return item["id"], previous, value, False
        return self._mutate(owner, list_ref, operation, apply)

    def _mutate(self, owner, list_ref, operation, apply) -> TodoReceipt:
        for attempt in range(self._max_retries):
            session = self._session_factory()
            rolled_back = False
            try:
                note = self._note(session, owner, list_ref)
                if note is None: raise TodoNotFoundError("todo list not found")
                old = note.items
                items = _normalise(_decode(old), list_ref)
                item_ref, previous, current, idempotent = apply(items)
                if idempotent:
                    session.rollback()
                    rolled_back = True
                    return _receipt(owner, list_ref, item_ref, operation, previous, current, _open(items), "idempotent_noop")
                new = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
                result = session.execute(update(self._Note).where(
                    self._Note.id == list_ref, self._owner_clause(owner),
                    self._Note.archived == False,  # noqa: E712 -- matches read gate
                    self._Note.note_type == "checklist",
                    self._Note.items.is_(None) if old is None else self._Note.items == old,
                ).values(items=new))
                if result.rowcount == 1:
                    session.commit()
                    return _receipt(owner, list_ref, item_ref, operation, previous, current, _open(items), "committed")
                session.rollback()
                rolled_back = True
                session.expire_all()
                if attempt + 1 == self._max_retries:
                    raise TodoConflictError("todo list changed concurrently; retry the request")
            except TodoDomainError:
                if not rolled_back:
                    session.rollback()
                raise
            except Exception:
                session.rollback()
                raise TodoDomainError("todo mutation failed") from None
            finally:
                session.close()
        raise TodoConflictError("todo list changed concurrently; retry the request")

    def _note(self, session, owner, list_ref):
        return session.query(self._Note).filter(
            self._Note.id == list_ref,
            self._owner_clause(owner),
            self._Note.archived == False,  # noqa: E712 -- explicit canonical list predicate
            self._Note.note_type == "checklist",
        ).one_or_none()

    def _owner_clause(self, owner):
        # None is NULL-owner only, deliberately never a wildcard.
        return self._Note.owner.is_(None) if owner is None else self._Note.owner == owner


def _ident(value, field):
    if not isinstance(value, str) or not value or len(value) > _ID_MAX or value.strip() != value:
        raise TodoValidationError(f"{field} must be a non-empty exact identifier")
    return value

def _owner(value): return None if value is None else _ident(value, "owner")

def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > _TEXT_MAX:
        raise TodoValidationError("text must be a non-empty string within the size limit")
    return value

def _key(value):
    if not isinstance(value, str) or not value or len(value) > _KEY_MAX or value.strip() != value:
        raise TodoValidationError("idempotency_key must be a non-empty exact identifier")
    return value

def _selector(item_ref, text):
    if (item_ref is None) == (text is None): raise TodoValidationError("provide exactly one of item_ref or text")
    return ("ref", _opaque_item_ref(item_ref, "item_ref")) if item_ref is not None else ("text", _text(text))

def _decode(raw):
    if raw is None or raw == "": return []
    if not isinstance(raw, str): raise TodoDataError("todo items must be JSON text")
    try: value = json.loads(raw)
    except (TypeError, ValueError): raise TodoDataError("todo items contain invalid JSON") from None
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise TodoDataError("todo items must be a JSON array of objects")
    return value

def _legacy_ref(list_ref, index): return str(uuid5(_LEGACY_NS, f"{list_ref}:{index}"))

def _normalise(items, list_ref):
    out, seen = [], set()
    for index, original in enumerate(items):
        item = dict(original)  # Retains unknown frontend fields, including indent.
        if not isinstance(item.get("text"), str): raise TodoDataError("todo item text must be a string")
        if "done" in item and not isinstance(item["done"], bool): raise TodoDataError("todo item completion state must be boolean")
        item.setdefault("done", False)
        item_ref = item.get("id")
        if item_ref is None:
            item_ref = _legacy_ref(list_ref, index)
            item["id"] = item_ref
        else: _opaque_item_ref(item_ref, "stored item id")
        if item_ref in seen: raise TodoDataError("todo item identifiers must be unique")
        seen.add(item_ref); out.append(item)
    return out

def _stored_ref(item):
    value = item.get("id")
    return value if isinstance(value, str) and value else None

def _state(item):
    value = item.get("done", False)
    if not isinstance(value, bool): raise TodoDataError("todo item completion state must be boolean")
    return value

def _find(items, selector):
    kind, value = selector
    matches = [i for i, item in enumerate(items) if (item["id"] if kind == "ref" else item["text"]) == value]
    if not matches: raise TodoNotFoundError("todo item not found")
    if len(matches) != 1: raise TodoAmbiguousMatchError([items[i]["id"] for i in matches])
    return matches[0]

def _open(items): return sum(not _state(item) for item in items)

def _validate_read_items(items):
    seen = set()
    for item in items:
        if not isinstance(item.get("text"), str): raise TodoDataError("todo item text must be a string")
        if "done" in item and not isinstance(item["done"], bool): raise TodoDataError("todo item completion state must be boolean")
        item_ref = item.get("id")
        if item_ref is None: continue  # Legacy IDs remain absent on read.
        _opaque_item_ref(item_ref, "stored item id")
        if item_ref in seen: raise TodoDataError("todo item identifiers must be unique")
        seen.add(item_ref)

def _opaque_item_ref(value, field):
    value = _ident(value, field)
    if _FRONTEND_ITEM_REF.fullmatch(value):
        return value
    try:
        parsed = UUID(value)
    except (TypeError, ValueError):
        raise TodoValidationError(f"{field} must be an opaque UUID") from None
    if str(parsed) != value:
        raise TodoValidationError(f"{field} must be an opaque UUID")
    return value

def _idempotency_ref(owner, list_ref, key):
    material = json.dumps({"owner": owner, "list_ref": list_ref, "key": key}, sort_keys=True, separators=(",", ":"))
    return sha256(material.encode()).hexdigest()

def _idempotent_item_ref(owner, list_ref, idempotency_ref):
    material = json.dumps({"owner": owner, "list_ref": list_ref, "idempotency_ref": idempotency_ref}, sort_keys=True, separators=(",", ":"))
    return str(uuid5(_IDEMPOTENCY_NS, material))

def _redact(kind, value): return f"{kind}:{sha256((value if value is not None else '<null>').encode()).hexdigest()[:16]}"

def _receipt(owner, list_ref, item_ref, operation, previous, current, open_count, status):
    return TodoReceipt(list_ref, item_ref, operation, previous, current, open_count, status, True,
                       (_redact("owner", owner), _redact("list", list_ref), _redact("item", item_ref), f"operation:{operation}"))
