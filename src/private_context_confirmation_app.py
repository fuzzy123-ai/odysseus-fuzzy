"""Constructor-owned application composition for private-context confirmation.

This module deliberately stops at the authenticated, paired-row boundary.  It
does not register routes, inspect request objects, read source systems, or
populate any application cache.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from typing import Callable, Iterator, Protocol

from core.database import ChatMessage as DbChatMessage
from core.database import Session as DbSession
from core.database import utcnow_naive
from src.private_context_confirmation import (
    Challenge,
    ConfirmationError,
    ConfirmationInsert,
    ConfirmationRepository,
    ContextManifest,
    DisclosureBinding,
    ManifestItem,
    PrivateContextConfirmation,
    SignedConfirmationPair,
    StoredConfirmationPair,
    VerifiedDisclosure,
)
from src.project_version_store import validate_repo_id, validate_version_id


_OWNER_RE = re.compile(r"owner_[0-9a-f]{32}\Z")
_OPAQUE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}\Z")
_MAX_JSON_INT = (1 << 53) - 1
_ID_ALLOCATION_ATTEMPTS = 32
_AUTH_BINDING_DOMAIN = b"private-context-cookie-binding.v1\x00"
_METADATA_KEY = "private_context_envelope"
_FORGE_PATH_RE = re.compile(r"^[A-Za-z0-9._/@+ -]{1,240}$")
_FORGE_BLOCKED_PATH_PARTS = frozenset({
    ".git", ".env", ".ssh", "id_rsa", "id_dsa", "id_ed25519",
})


def _fail(code: str) -> None:
    raise ConfirmationError(code) from None


def _opaque(value: object, maximum: int) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= maximum
        and value.isascii()
        and all("!" <= character <= "~" for character in value)
    )


def _valid_message_id(value: object) -> bool:
    return type(value) is int and 1 <= value <= _MAX_JSON_INT


def _forge_id(value: object, validator: Callable[[object], str]) -> str:
    if type(value) is not str or not value.isascii():
        _fail("invalid_context")
    try:
        normalized = validator(value)
    except Exception:
        _fail("invalid_context")
    if normalized != value:
        _fail("invalid_context")
    return normalized


def _forge_path(value: object) -> str:
    if type(value) is not str or not value.isascii() or _FORGE_PATH_RE.fullmatch(value) is None:
        _fail("invalid_context")
    if value.startswith(("/", "~")) or "\\" in value:
        _fail("invalid_context")
    parts = value.split("/")
    if (
        any(part in {"", ".", ".."} or part.lower() in _FORGE_BLOCKED_PATH_PARTS for part in parts)
        or any(ord(character) < 32 for character in value)
    ):
        _fail("invalid_context")
    return value


@dataclass(frozen=True, slots=True)
class SourceSelection:
    """One exact producer selection triple, safe to retain process-locally."""

    source: str = field(repr=False)
    selection_ref: str = field(repr=False)
    revision_ref: str = field(repr=False)
    forge_locator: "ForgeSelectionLocator | None" = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if type(self.source) is not str or self.source not in {"forge", "planning", "knowledge"}:
            _fail("invalid_context")
        if (
            type(self.selection_ref) is not str
            or type(self.revision_ref) is not str
            or not _OPAQUE_RE.fullmatch(self.selection_ref)
            or not _OPAQUE_RE.fullmatch(self.revision_ref)
        ):
            _fail("invalid_context")
        if self.forge_locator is not None and (
            self.source != "forge" or type(self.forge_locator) is not ForgeSelectionLocator
        ):
            _fail("invalid_context")


@dataclass(frozen=True, slots=True, repr=False)
class ForgeSelectionLocator:
    """Redacted request locator for one exact retained Forge path."""

    repo_id: str = field(repr=False)
    version_id: str = field(repr=False)
    path: str = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "repo_id", _forge_id(self.repo_id, validate_repo_id))
        object.__setattr__(self, "version_id", _forge_id(self.version_id, validate_version_id))
        object.__setattr__(self, "path", _forge_path(self.path))

    def __repr__(self) -> str:
        return "ForgeSelectionLocator(<redacted>)"


@dataclass(frozen=True, slots=True)
class ContextSelection:
    """Deterministically ordered, bounded immutable source selection."""

    items: tuple[SourceSelection, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.items) is not tuple or not 1 <= len(self.items) <= 300:
            _fail("invalid_context")
        if any(type(item) is not SourceSelection for item in self.items):
            _fail("invalid_context")
        keys = tuple((item.source, item.selection_ref, item.revision_ref) for item in self.items)
        if keys != tuple(sorted(keys)) or len({item.selection_ref for item in self.items}) != len(self.items):
            _fail("invalid_context")


class ContextManifestProducer(Protocol):
    def capture(self, binding: DisclosureBinding, selection: ContextSelection) -> ContextManifest: ...


@dataclass(frozen=True, slots=True)
class _CookieScope:
    token: str = field(repr=False)
    token_binding: str = field(repr=False)
    binding: DisclosureBinding = field(repr=False)
    owner_alias: str = field(repr=False)


class CookieConfirmationAuthority:
    """Bind one server-issued cookie to one current owner and chat SID."""

    def __init__(self, auth_manager: object) -> None:
        if auth_manager is None:
            _fail("invalid_binding")
        self._auth_manager = auth_manager
        self._key = secrets.token_bytes(32)
        self._local = threading.local()

    def fork(self) -> "CookieConfirmationAuthority":
        """Create an independent holder in this authority's binding family."""
        if type(self) is not CookieConfirmationAuthority:
            _fail("invalid_context")
        authority = object.__new__(CookieConfirmationAuthority)
        authority._auth_manager = self._auth_manager
        authority._key = self._key
        authority._local = threading.local()
        return authority

    @contextmanager
    def hold(self, token: str, chat_session_id: str) -> Iterator[DisclosureBinding]:
        if not _opaque(token, 4096) or not _opaque(chat_session_id, 128):
            _fail("invalid_binding")
        if getattr(self._local, "scope", None) is not None:
            _fail("invalid_binding")
        guard = getattr(self._auth_manager, "guard_current_session", None)
        admin_guard = getattr(self._auth_manager, "guard_current_admin_subject", None)
        strict_identity = getattr(self._auth_manager, "strict_session_identity", None)
        is_admin = getattr(self._auth_manager, "is_admin", None)
        configured = getattr(self._auth_manager, "is_configured", False)
        if (
            not configured
            or not callable(guard)
            or not callable(admin_guard)
            or not callable(strict_identity)
            or not callable(is_admin)
        ):
            _fail("invalid_binding")
        try:
            identity = strict_identity(token)
        except Exception:
            _fail("invalid_binding")
        if (
            type(identity) is not tuple
            or len(identity) != 2
            or not _opaque(identity[0], 128)
            or type(identity[1]) is not str
            or _OWNER_RE.fullmatch(identity[1]) is None
        ):
            _fail("invalid_binding")
        owner_alias, owner_sid = identity
        subject_for_owner = getattr(self._auth_manager, "subject_id_for_username", None)
        security_revision = getattr(self._auth_manager, "security_action_revision", None)
        if not callable(subject_for_owner) or not callable(security_revision):
            _fail("invalid_binding")
        try:
            initial_revision = security_revision()
        except ConfirmationError:
            raise
        except Exception:
            _fail("invalid_binding")
        if type(initial_revision) is not int or not 0 <= initial_revision <= (1 << 63) - 1:
            _fail("invalid_binding")
        body_error: Exception | None = None
        try:
            with guard(token, owner_sid) as guarded_alias:
                if type(guarded_alias) is not str or guarded_alias != owner_alias:
                    _fail("invalid_binding")
                with admin_guard(owner_sid) as guarded_admin_alias:
                    if type(guarded_admin_alias) is not str or guarded_admin_alias != owner_alias:
                        _fail("invalid_binding")
                    try:
                        current_identity = strict_identity(token)
                        current_revision = security_revision()
                        current_subject = subject_for_owner(owner_alias)
                    except ConfirmationError:
                        raise
                    except Exception:
                        _fail("invalid_binding")
                    if (
                        current_identity != (owner_alias, owner_sid)
                        or is_admin(owner_alias) is not True
                        or current_subject != owner_sid
                        or current_revision != initial_revision
                        or type(current_revision) is not int
                        or not 0 <= current_revision <= (1 << 63) - 1
                    ):
                        _fail("invalid_binding")
                    binding = DisclosureBinding(
                        owner_sid=owner_sid,
                        chat_session_id=chat_session_id,
                        auth_session_binding=self._token_binding(token),
                        security_revision=current_revision,
                    )
                    scope = _CookieScope(token, self._token_binding(token), binding, owner_alias)
                    self._local.scope = scope
                    try:
                        self.recheck(binding)
                        try:
                            yield binding
                        except Exception as error:
                            body_error = error
                            raise
                    finally:
                        self._local.scope = None
        except Exception as error:
            if body_error is not None and error is body_error:
                raise
            _fail("invalid_binding")

    def current_binding(self) -> DisclosureBinding:
        scope = getattr(self._local, "scope", None)
        if type(scope) is not _CookieScope:
            _fail("invalid_binding")
        return scope.binding

    def require_current(self, binding: DisclosureBinding) -> str:
        scope = getattr(self._local, "scope", None)
        if type(scope) is not _CookieScope or type(binding) is not DisclosureBinding:
            _fail("invalid_binding")
        if scope.binding != binding:
            _fail("invalid_binding")
        return scope.owner_alias

    def recheck(self, binding: DisclosureBinding) -> None:
        scope = getattr(self._local, "scope", None)
        if type(scope) is not _CookieScope or scope.binding != binding:
            _fail("invalid_binding")
        strict_identity = getattr(self._auth_manager, "strict_session_identity", None)
        security_revision = getattr(self._auth_manager, "security_action_revision", None)
        is_admin = getattr(self._auth_manager, "is_admin", None)
        subject_for_owner = getattr(self._auth_manager, "subject_id_for_username", None)
        if (
            not callable(strict_identity)
            or not callable(security_revision)
            or not callable(is_admin)
            or not callable(subject_for_owner)
        ):
            _fail("invalid_binding")
        try:
            identity = strict_identity(scope.token)
        except Exception:
            _fail("invalid_binding")
        if type(identity) is not tuple or len(identity) != 2:
            _fail("invalid_binding")
        try:
            revision = security_revision()
        except Exception:
            _fail("invalid_binding")
        try:
            admin_current = is_admin(identity[0]) is True
            subject_current = subject_for_owner(identity[0])
        except Exception:
            _fail("invalid_binding")
        if (
            not admin_current
            or
            type(identity[0]) is not str
            or type(identity[1]) is not str
            or identity[0] != scope.owner_alias
            or identity[1] != binding.owner_sid
            or subject_current != binding.owner_sid
            or revision != binding.security_revision
            or type(revision) is not int
            or not 0 <= revision <= (1 << 63) - 1
        ):
            _fail("invalid_binding")

    def _token_binding(self, token: str) -> str:
        return hmac.new(self._key, _AUTH_BINDING_DOMAIN + token.encode("ascii"), hashlib.sha256).hexdigest()

class ChatConfirmationRepository(ConfirmationRepository):
    """Persist paired confirmation messages through fresh SQLAlchemy sessions."""

    def __init__(self, session_factory: Callable[[], object], authority: CookieConfirmationAuthority) -> None:
        if not callable(session_factory) or type(authority) is not CookieConfirmationAuthority:
            _fail("invalid_context")
        self._session_factory = session_factory
        self._authority = authority

    def insert_confirmation(
        self,
        request: ConfirmationInsert,
        seal_pair: Callable[[int, int], SignedConfirmationPair],
    ) -> None:
        if type(request) is not ConfirmationInsert or not callable(seal_pair):
            _fail("persistence_failed")
        owner_alias = self._authority.require_current(request.binding)
        self._authority.recheck(request.binding)
        db = self._new_session()
        try:
            parent = db.query(DbSession).filter(DbSession.id == request.chat_session_id).one_or_none()
            if parent is None or type(parent.owner) is not str or parent.owner != owner_alias:
                _fail("invalid_binding")
            if request.sid != request.binding.owner_sid:
                _fail("invalid_binding")
            user_id, acknowledgement_id = self._allocate_ids(db)
        except ConfirmationError:
            self._rollback(db)
            self._close(db)
            raise
        except Exception:
            self._rollback(db)
            self._close(db)
            _fail("persistence_failed")
        try:
            pair = seal_pair(user_id, acknowledgement_id)
        except Exception:
            self._rollback(db)
            self._close(db)
            raise
        if type(pair) is not SignedConfirmationPair:
            self._rollback(db)
            self._close(db)
            _fail("persistence_failed")
        try:
            now = utcnow_naive()
            metadata_user = json.dumps({_METADATA_KEY: pair.user_envelope}, separators=(",", ":"), sort_keys=True)
            metadata_ack = json.dumps({_METADATA_KEY: pair.acknowledgement_envelope}, separators=(",", ":"), sort_keys=True)
            current_count = db.query(DbChatMessage).filter(DbChatMessage.session_id == request.chat_session_id).count()
            db.add(DbChatMessage(
                id=str(user_id), session_id=request.chat_session_id, role="user",
                content=request.user_content, meta_data=metadata_user, timestamp=now,
            ))
            db.add(DbChatMessage(
                id=str(acknowledgement_id), session_id=request.chat_session_id, role="assistant",
                content=request.acknowledgement_content, meta_data=metadata_ack, timestamp=now,
            ))
            parent.message_count = current_count + 2
            parent.last_accessed = now
            parent.last_message_at = now
            parent.updated_at = now
            self._authority.recheck(request.binding)
            db.commit()
        except ConfirmationError:
            self._rollback(db)
            raise
        except Exception:
            self._rollback(db)
            _fail("persistence_failed")
        finally:
            self._close(db)

    def load_confirmation_rows(
        self,
        sid: str,
        chat_session_id: str,
        user_message_id: int,
        acknowledgement_message_id: int,
    ) -> StoredConfirmationPair | None:
        binding = self._authority.current_binding()
        owner_alias = self._authority.require_current(binding)
        if (
            sid != binding.owner_sid
            or chat_session_id != binding.chat_session_id
            or not _valid_message_id(user_message_id)
            or not _valid_message_id(acknowledgement_message_id)
            or user_message_id == acknowledgement_message_id
        ):
            _fail("readback_failed")
        db = self._new_session()
        try:
            parent = db.query(DbSession).filter(DbSession.id == chat_session_id).one_or_none()
            if parent is None or parent.owner != owner_alias:
                return None
            user_row = db.query(DbChatMessage).filter(
                DbChatMessage.id == str(user_message_id), DbChatMessage.session_id == chat_session_id
            ).one_or_none()
            acknowledgement_row = db.query(DbChatMessage).filter(
                DbChatMessage.id == str(acknowledgement_message_id), DbChatMessage.session_id == chat_session_id
            ).one_or_none()
            if user_row is None or acknowledgement_row is None:
                return None
            user_envelope = self._metadata_envelope(user_row.meta_data)
            acknowledgement_envelope = self._metadata_envelope(acknowledgement_row.meta_data)
            if user_envelope is None or acknowledgement_envelope is None:
                return None
            self._authority.recheck(binding)
            return StoredConfirmationPair(
                sid=sid,
                chat_session_id=chat_session_id,
                user_message_id=user_message_id,
                acknowledgement_message_id=acknowledgement_message_id,
                user_role=user_row.role,
                acknowledgement_role=acknowledgement_row.role,
                user_content=user_row.content,
                acknowledgement_content=acknowledgement_row.content,
                user_envelope=user_envelope,
                acknowledgement_envelope=acknowledgement_envelope,
            )
        except ConfirmationError:
            raise
        except Exception:
            _fail("readback_failed")
        finally:
            self._close(db)

    def assert_chat_owned(self, binding: DisclosureBinding) -> None:
        owner_alias = self._authority.require_current(binding)
        self._authority.recheck(binding)
        db = self._new_session()
        try:
            parent = db.query(DbSession).filter(DbSession.id == binding.chat_session_id).one_or_none()
            if parent is None or type(parent.owner) is not str or parent.owner != owner_alias:
                _fail("invalid_binding")
        except ConfirmationError:
            raise
        except Exception:
            _fail("invalid_binding")
        finally:
            self._close(db)

    def _new_session(self) -> object:
        try:
            db = self._session_factory()
        except Exception:
            _fail("persistence_failed")
        if db is None:
            _fail("persistence_failed")
        return db

    @staticmethod
    def _allocate_ids(db: object) -> tuple[int, int]:
        selected: list[int] = []
        for _ in range(_ID_ALLOCATION_ATTEMPTS):
            candidate = secrets.randbelow(_MAX_JSON_INT) + 1
            if not _valid_message_id(candidate) or candidate in selected:
                continue
            collision = db.query(DbChatMessage.id).filter(DbChatMessage.id == str(candidate)).one_or_none()
            if collision is not None:
                continue
            selected.append(candidate)
            if len(selected) == 2:
                return selected[0], selected[1]
        _fail("persistence_failed")

    @staticmethod
    def _metadata_envelope(value: object) -> str | None:
        if type(value) is not str:
            return None
        try:
            def reject_duplicate_keys(pairs: list[tuple[object, object]]) -> dict[object, object]:
                decoded: dict[object, object] = {}
                for key, item in pairs:
                    if key in decoded:
                        raise ValueError("duplicate metadata key")
                    decoded[key] = item
                return decoded

            decoded = json.loads(value, object_pairs_hook=reject_duplicate_keys)
        except Exception:
            return None
        if type(decoded) is not dict or set(decoded) != {_METADATA_KEY} or type(decoded[_METADATA_KEY]) is not str:
            return None
        return decoded[_METADATA_KEY]

    @staticmethod
    def _rollback(db: object) -> None:
        try:
            db.rollback()
        except Exception:
            pass

    @staticmethod
    def _close(db: object) -> None:
        try:
            db.close()
        except Exception:
            pass


@dataclass(slots=True)
class _PendingSelection:
    selection: ContextSelection
    issued_at: datetime
    expires_at: datetime
    monotonic_deadline: float


class PrivateContextConfirmationApp:
    """Compose cookie authority, real-row repository, and foundation grants."""

    def __init__(
        self,
        session_factory: Callable[[], object],
        auth_manager: object,
        manifest_producer: ContextManifestProducer | None = None,
        *,
        ttl_seconds: int = 300,
        max_entries: int = 1024,
    ) -> None:
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 900:
            _fail("invalid_limits")
        if type(max_entries) is not int or not 1 <= max_entries <= 4096:
            _fail("invalid_limits")
        if manifest_producer is not None and not callable(getattr(manifest_producer, "capture", None)):
            _fail("invalid_context")
        self._authority = CookieConfirmationAuthority(auth_manager)
        self._repository = ChatConfirmationRepository(session_factory, self._authority)
        self._manager = PrivateContextConfirmation(self._repository, ttl_seconds=ttl_seconds, max_entries=max_entries)
        self._producer = manifest_producer
        self._max_entries = max_entries
        self._pending: dict[str, _PendingSelection] = {}
        self._lock = threading.RLock()
        self._wall_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
        self._monotonic_clock: Callable[[], float] = time.monotonic

    def issue_for_selection(
        self, cookie: str, chat_session_id: str, selection: ContextSelection
    ) -> Challenge:
        self._validate_selection(selection)
        producer = self._producer
        if producer is None:
            _fail("invalid_context")
        now = self._now()
        monotonic_now = self._monotonic_clock()
        with self._lock:
            self._prune(now, monotonic_now)
            if len(self._pending) >= self._max_entries:
                _fail("challenge_capacity")
            with self._authority.hold(cookie, chat_session_id) as binding:
                self._repository.assert_chat_owned(binding)
                manifest = self._capture_manifest(binding, selection, producer)
                challenge = self._manager.issue_challenge(binding, manifest)
            self._pending[challenge.token] = _PendingSelection(
                selection, challenge.issued_at, challenge.expires_at, monotonic_now + (challenge.expires_at - challenge.issued_at).total_seconds()
            )
            return challenge

    def consume_challenge(self, cookie: str, chat_session_id: str, challenge: Challenge) -> VerifiedDisclosure:
        if type(challenge) is not Challenge or not _opaque(challenge.token, 4096):
            _fail("invalid_challenge")
        producer = self._producer
        if producer is None:
            _fail("invalid_context")
        with self._lock:
            pending = self._pending.get(challenge.token)
            if pending is None:
                _fail("invalid_challenge")
            now = self._now()
            monotonic_now = self._monotonic_clock()
            if self._expired(pending, now, monotonic_now):
                self._pending.pop(challenge.token, None)
                _fail("challenge_expired")
            try:
                with self._authority.hold(cookie, chat_session_id) as binding:
                    manifest = self._capture_manifest(binding, pending.selection, producer)
                    verified = self._manager.consume_challenge(challenge.token, binding, manifest)
            except ConfirmationError as error:
                if error.code in {"challenge_expired", "challenge_spent", "invalid_challenge", "persistence_failed", "readback_failed"}:
                    self._pending.pop(challenge.token, None)
                raise
            self._pending.pop(challenge.token, None)
            return verified

    def verify_disclosure(
        self,
        cookie: str,
        chat_session_id: str,
        disclosure_message_id: int,
        selection: ContextSelection,
        *,
        expected_subset: ContextManifest | None = None,
    ) -> VerifiedDisclosure:
        self._validate_selection(selection)
        if expected_subset is not None and type(expected_subset) is not ContextManifest:
            _fail("invalid_context")
        producer = self._producer
        if producer is None:
            _fail("invalid_context")
        with self._authority.hold(cookie, chat_session_id) as binding:
            manifest = self._capture_manifest(binding, selection, producer)
            if expected_subset is not None:
                self._verify_manifest_subset(manifest, expected_subset)
            return self._manager.verify_disclosure(disclosure_message_id, binding, manifest)

    @staticmethod
    def _verify_manifest_subset(
        full_manifest: ContextManifest,
        expected_subset: ContextManifest,
    ) -> None:
        if type(full_manifest) is not ContextManifest or type(expected_subset) is not ContextManifest:
            _fail("invalid_context")
        full_by_triple: dict[tuple[str, str, str], list[ManifestItem]] = {}
        for item in full_manifest.items:
            triple = (item.source, item.selection_ref, item.revision_ref)
            full_by_triple.setdefault(triple, []).append(item)
        for item in expected_subset.items:
            triple = (item.source, item.selection_ref, item.revision_ref)
            matches = full_by_triple.get(triple, ())
            if len(matches) != 1 or matches[0]._canonical() != item._canonical():
                _fail("invalid_context")

    def _capture_manifest(
        self,
        binding: DisclosureBinding,
        selection: ContextSelection,
        producer: ContextManifestProducer,
    ) -> ContextManifest:
        try:
            manifest = producer.capture(binding, selection)
        except Exception:
            _fail("invalid_context")
        if type(manifest) is not ContextManifest:
            _fail("invalid_context")
        expected = tuple((item.source, item.selection_ref, item.revision_ref) for item in selection.items)
        actual = tuple((item.source, item.selection_ref, item.revision_ref) for item in manifest.items)
        if sorted(actual) != sorted(expected):
            _fail("invalid_context")
        return manifest

    @staticmethod
    def _validate_selection(selection: object) -> None:
        if type(selection) is not ContextSelection:
            _fail("invalid_context")

    def _now(self) -> datetime:
        now = self._wall_clock()
        if type(now) is not datetime or now.tzinfo is None:
            _fail("clock_invalid")
        return now.astimezone(timezone.utc)

    def _prune(self, now: datetime, monotonic_now: float) -> None:
        for token, pending in tuple(self._pending.items()):
            if self._expired(pending, now, monotonic_now):
                self._pending.pop(token, None)

    @staticmethod
    def _expired(pending: _PendingSelection, now: datetime, monotonic_now: float) -> bool:
        return now < pending.issued_at or now >= pending.expires_at or monotonic_now >= pending.monotonic_deadline
