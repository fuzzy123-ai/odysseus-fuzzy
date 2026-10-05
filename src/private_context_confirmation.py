"""Process-local, fail-closed confirmation grants for private context.

This module deliberately knows nothing about HTTP, cookies, database schemas, or
private payloads.  Application composition supplies a transaction-owning
repository only after its route guard has established a trusted binding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from typing import Callable, Protocol


_OWNER_RE = re.compile(r"owner_[0-9a-f]{32}\Z")
_OPAQUE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}\Z")
_NONCE_RE = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_HEX_RE = re.compile(r"[0-9a-f]{64}\Z")
_MAX_INT64 = (1 << 63) - 1
_MANIFEST_SCHEMA = "odysseus.private_context_manifest.v1"
_AUDIENCE = "authenticated_context_download"
_ACKNOWLEDGEMENT_CONTENT = "Private context disclosure confirmed."
_ERROR_CODES = frozenset(
    {
        "invalid_manifest",
        "invalid_binding",
        "invalid_limits",
        "invalid_context",
        "clock_invalid",
        "challenge_capacity",
        "challenge_unavailable",
        "invalid_challenge",
        "challenge_context_mismatch",
        "challenge_expired",
        "challenge_spent",
        "seal_reused",
        "invalid_message_ids",
        "persistence_failed",
        "readback_failed",
        "disclosure_expired",
        "invalid_disclosure",
        "disclosure_unverified",
    }
)


class ConfirmationError(Exception):
    """A deliberately non-diagnostic error boundary for confirmation flows."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or code not in _ERROR_CODES:
            raise ValueError("invalid confirmation error code") from None
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise ConfirmationError(code) from None


class _SealerFailure(Exception):
    """Private transport for validation failures crossing an untrusted repository."""


def _ascii_opaque(value: object, minimum: int, maximum: int) -> bool:
    return (
        type(value) is str
        and minimum <= len(value) <= maximum
        and value.isascii()
        and all("!" <= character <= "~" for character in value)
    )


def _digest(value: object) -> str:
    if type(value) is not str:
        _fail("invalid_manifest")
    raw = value[7:] if value.startswith("sha256:") else value
    if not _HEX_RE.fullmatch(raw):
        _fail("invalid_manifest")
    return raw


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, separators=(",", ":"), sort_keys=True, ensure_ascii=True
    ).encode("utf-8")


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


@dataclass(frozen=True, slots=True)
class DisclosureBinding:
    owner_sid: str = field(repr=False)
    chat_session_id: str = field(repr=False)
    auth_session_binding: str = field(repr=False)
    security_revision: int = field(repr=False)
    audience: str = field(default=_AUDIENCE, repr=False)

    def __post_init__(self) -> None:
        if type(self.owner_sid) is not str or not _OWNER_RE.fullmatch(self.owner_sid):
            _fail("invalid_binding")
        if not _ascii_opaque(self.chat_session_id, 1, 128):
            _fail("invalid_binding")
        if not _ascii_opaque(self.auth_session_binding, 1, 256):
            _fail("invalid_binding")
        if type(self.security_revision) is not int or not 0 <= self.security_revision <= _MAX_INT64:
            _fail("invalid_binding")
        if type(self.audience) is not str or self.audience != _AUDIENCE:
            _fail("invalid_binding")

    def _canonical(self) -> dict[str, object]:
        return {
            "audience": self.audience,
            "auth_session_binding": self.auth_session_binding,
            "chat_session_id": self.chat_session_id,
            "owner_sid": self.owner_sid,
            "security_revision": self.security_revision,
        }


@dataclass(frozen=True, slots=True)
class ManifestItem:
    source: str = field(repr=False)
    selection_ref: str = field(repr=False)
    revision_ref: str = field(repr=False)
    boundary_sha256: str = field(repr=False)
    record_sha256: str = field(repr=False)
    payload_sha256: str = field(repr=False)
    classification: str = field(repr=False)
    provenance_ref: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.source) is not str or self.source not in {"forge", "planning", "knowledge"}:
            _fail("invalid_manifest")
        if type(self.classification) is not str or self.classification not in {"public", "private"}:
            _fail("invalid_manifest")
        for value in (self.selection_ref, self.revision_ref, self.provenance_ref):
            if type(value) is not str or not _OPAQUE_RE.fullmatch(value):
                _fail("invalid_manifest")
        object.__setattr__(self, "boundary_sha256", _digest(self.boundary_sha256))
        object.__setattr__(self, "record_sha256", _digest(self.record_sha256))
        object.__setattr__(self, "payload_sha256", _digest(self.payload_sha256))

    def _canonical(self) -> dict[str, str]:
        return {
            "boundary_sha256": self.boundary_sha256,
            "classification": self.classification,
            "payload_sha256": self.payload_sha256,
            "provenance_ref": self.provenance_ref,
            "record_sha256": self.record_sha256,
            "revision_ref": self.revision_ref,
            "selection_ref": self.selection_ref,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class ContextManifest:
    items: tuple[ManifestItem, ...] = field(repr=False)
    schema: str = field(default=_MANIFEST_SCHEMA, repr=False)
    _canonical_value: bytes = field(init=False, repr=False, compare=False)
    _digest_value: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self.schema) is not str or self.schema != _MANIFEST_SCHEMA or type(self.items) is not tuple:
            _fail("invalid_manifest")
        copied = tuple(item for item in self.items)
        if not 1 <= len(copied) <= 300 or any(type(item) is not ManifestItem for item in copied):
            _fail("invalid_manifest")
        canonical_items = tuple(item._canonical() for item in copied)
        if canonical_items != tuple(sorted(canonical_items, key=_canonical_bytes)):
            _fail("invalid_manifest")
        if len({item["selection_ref"] for item in canonical_items}) != len(canonical_items):
            _fail("invalid_manifest")
        if not any(item["classification"] == "private" for item in canonical_items):
            _fail("invalid_manifest")
        encoded = _canonical_bytes({"items": canonical_items, "schema": self.schema})
        if len(encoded) > 262_144:
            _fail("invalid_manifest")
        object.__setattr__(self, "items", copied)
        object.__setattr__(self, "_canonical_value", encoded)
        object.__setattr__(self, "_digest_value", hashlib.sha256(encoded).hexdigest())

    @property
    def digest(self) -> str:
        return self._digest_value


@dataclass(frozen=True, slots=True)
class Challenge:
    token: str = field(repr=False)
    issued_at: datetime = field(repr=False)
    expires_at: datetime = field(repr=False)


@dataclass(frozen=True, slots=True)
class SignedConfirmationPair:
    user_envelope: str = field(repr=False)
    acknowledgement_envelope: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ConfirmationInsert:
    sid: str = field(repr=False)
    chat_session_id: str = field(repr=False)
    user_content: str = field(repr=False)
    acknowledgement_content: str = field(repr=False)
    binding: DisclosureBinding = field(repr=False)
    manifest_digest: str = field(repr=False)
    nonce: str = field(repr=False)
    issued_at: datetime = field(repr=False)
    expires_at: datetime = field(repr=False)
    provenance: str = field(default="fresh_chat_command", repr=False)


@dataclass(frozen=True, slots=True)
class StoredConfirmationPair:
    sid: str = field(repr=False)
    chat_session_id: str = field(repr=False)
    user_message_id: int = field(repr=False)
    acknowledgement_message_id: int = field(repr=False)
    user_role: str = field(repr=False)
    acknowledgement_role: str = field(repr=False)
    user_content: str = field(repr=False)
    acknowledgement_content: str = field(repr=False)
    user_envelope: str = field(repr=False)
    acknowledgement_envelope: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class VerifiedDisclosure:
    disclosure_message_id: int = field(repr=False)
    acknowledgement_message_id: int = field(repr=False)
    manifest_digest: str = field(repr=False)


class ConfirmationRepository(Protocol):
    def insert_confirmation(
        self,
        request: ConfirmationInsert,
        seal_pair: Callable[[int, int], SignedConfirmationPair],
    ) -> None: ...

    def load_confirmation_rows(
        self,
        sid: str,
        chat_session_id: str,
        user_message_id: int,
        acknowledgement_message_id: int,
    ) -> StoredConfirmationPair | None: ...


@dataclass(slots=True)
class _Entry:
    nonce: str
    binding: DisclosureBinding
    manifest: ContextManifest
    issued_at: datetime
    expires_at: datetime
    monotonic_deadline: float
    state: str = "pending"
    verified: VerifiedDisclosure | None = None


class PrivateContextConfirmation:
    """Issue and verify one-use process-local confirmation grants."""

    def __init__(
        self,
        repository: ConfirmationRepository,
        *,
        ttl_seconds: int = 300,
        max_entries: int = 1024,
    ) -> None:
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 900:
            _fail("invalid_limits")
        if type(max_entries) is not int or not 1 <= max_entries <= 4096:
            _fail("invalid_limits")
        self._repository = repository
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._key = secrets.token_bytes(32)
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.RLock()
        self._wall_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
        self._monotonic_clock: Callable[[], float] = time.monotonic

    def issue_challenge(
        self, binding: DisclosureBinding, manifest: ContextManifest
    ) -> Challenge:
        self._validated_inputs(binding, manifest)
        now = self._now()
        monotonic_now = self._monotonic_clock()
        frozen_binding = self._copy_binding(binding)
        frozen_manifest = self._copy_manifest(manifest)
        with self._lock:
            self._prune(now, monotonic_now)
            if len(self._entries) >= self._max_entries:
                _fail("challenge_capacity")
            for _ in range(8):
                nonce = secrets.token_urlsafe(32)
                if _NONCE_RE.fullmatch(nonce) and nonce not in self._entries:
                    expires_at = now + timedelta(seconds=self._ttl_seconds)
                    entry = _Entry(
                        nonce=nonce,
                        binding=frozen_binding,
                        manifest=frozen_manifest,
                        issued_at=now,
                        expires_at=expires_at,
                        monotonic_deadline=monotonic_now + self._ttl_seconds,
                    )
                    self._entries[nonce] = entry
                    return Challenge(self._challenge_token(nonce), now, expires_at)
            _fail("challenge_unavailable")

    def consume_challenge(
        self,
        token: str,
        binding: DisclosureBinding,
        manifest: ContextManifest,
    ) -> VerifiedDisclosure:
        self._validated_inputs(binding, manifest)
        nonce = self._verified_nonce(token)
        now = self._now()
        monotonic_now = self._monotonic_clock()
        with self._lock:
            self._prune(now, monotonic_now)
            entry = self._entries.get(nonce)
            if entry is None:
                _fail("invalid_challenge")
            if self._entry_expired(entry, now, monotonic_now):
                entry.state = "spent"
                _fail("challenge_expired")
            if entry.binding != binding or entry.manifest.digest != manifest.digest:
                _fail("challenge_context_mismatch")
            if entry.state != "pending":
                _fail("challenge_spent")
            entry.state = "spent"

        sealed: tuple[int, int, SignedConfirmationPair] | None = None
        sealer_lock = threading.Lock()
        sealer_claimed = False
        sealer_failure_code: str | None = None

        def seal_pair(user_message_id: int, acknowledgement_message_id: int) -> SignedConfirmationPair:
            nonlocal sealed, sealer_claimed, sealer_failure_code
            with sealer_lock:
                if sealer_claimed:
                    sealer_failure_code = "seal_reused"
                    raise _SealerFailure() from None
                sealer_claimed = True
                try:
                    self._valid_message_ids(user_message_id, acknowledgement_message_id)
                except ConfirmationError:
                    sealer_failure_code = "invalid_message_ids"
                    raise _SealerFailure() from None
                try:
                    pair = self._signed_pair(entry, user_message_id, acknowledgement_message_id)
                except Exception:
                    sealer_failure_code = "persistence_failed"
                    raise _SealerFailure() from None
                sealed = (user_message_id, acknowledgement_message_id, pair)
                return pair

        request = ConfirmationInsert(
            sid=entry.binding.owner_sid,
            chat_session_id=entry.binding.chat_session_id,
            user_content=f"/context-disclose {token}",
            acknowledgement_content=_ACKNOWLEDGEMENT_CONTENT,
            binding=entry.binding,
            manifest_digest=entry.manifest.digest,
            nonce=nonce,
            issued_at=entry.issued_at,
            expires_at=entry.expires_at,
        )
        try:
            self._repository.insert_confirmation(request, seal_pair)
        except _SealerFailure:
            raise ConfirmationError(sealer_failure_code or "persistence_failed") from None
        except Exception:
            raise ConfirmationError("persistence_failed") from None
        if sealed is None:
            _fail("persistence_failed")
        user_id, acknowledgement_id, _ = sealed
        try:
            rows = self._repository.load_confirmation_rows(
                entry.binding.owner_sid, entry.binding.chat_session_id, user_id, acknowledgement_id
            )
        except Exception:
            raise ConfirmationError("readback_failed") from None
        now = self._now()
        monotonic_now = self._monotonic_clock()
        if self._entry_expired(entry, now, monotonic_now):
            _fail("challenge_expired")
        verified = self._verify_rows(entry, rows, user_id, acknowledgement_id)

        with self._lock:
            now = self._now()
            monotonic_now = self._monotonic_clock()
            if self._entry_expired(entry, now, monotonic_now):
                entry.state = "spent"
                _fail("challenge_expired")
            entry.state = "active"
            entry.verified = verified
        return verified

    def verify_disclosure(
        self,
        disclosure_message_id: int,
        binding: DisclosureBinding,
        manifest: ContextManifest,
    ) -> VerifiedDisclosure:
        self._validated_inputs(binding, manifest)
        if type(disclosure_message_id) is not int or not 1 <= disclosure_message_id <= _MAX_INT64:
            _fail("invalid_disclosure")
        now = self._now()
        monotonic_now = self._monotonic_clock()
        with self._lock:
            self._prune(now, monotonic_now)
            entry = next(
                (
                    value
                    for value in self._entries.values()
                    if value.state == "active"
                    and value.verified is not None
                    and value.verified.disclosure_message_id == disclosure_message_id
                ),
                None,
            )
            if entry is None or entry.binding != binding or entry.manifest.digest != manifest.digest:
                _fail("disclosure_unverified")
            if self._entry_expired(entry, now, monotonic_now):
                entry.state = "spent"
                _fail("disclosure_expired")
            verified = entry.verified
        assert verified is not None
        try:
            rows = self._repository.load_confirmation_rows(
                entry.binding.owner_sid,
                entry.binding.chat_session_id,
                verified.disclosure_message_id,
                verified.acknowledgement_message_id,
            )
        except Exception:
            with self._lock:
                entry.state = "spent"
                entry.verified = None
            raise ConfirmationError("disclosure_unverified") from None
        try:
            now = self._now()
            monotonic_now = self._monotonic_clock()
            if self._entry_expired(entry, now, monotonic_now):
                _fail("disclosure_expired")
            checked = self._verify_rows(
                entry,
                rows,
                verified.disclosure_message_id,
                verified.acknowledgement_message_id,
            )
        except ConfirmationError:
            with self._lock:
                entry.state = "spent"
                entry.verified = None
            raise
        except Exception:
            with self._lock:
                entry.state = "spent"
                entry.verified = None
            raise ConfirmationError("disclosure_unverified") from None
        return checked

    def _validated_inputs(self, binding: object, manifest: object) -> None:
        if type(binding) is not DisclosureBinding or type(manifest) is not ContextManifest:
            _fail("invalid_context")

    @staticmethod
    def _copy_binding(binding: DisclosureBinding) -> DisclosureBinding:
        return DisclosureBinding(
            binding.owner_sid,
            binding.chat_session_id,
            binding.auth_session_binding,
            binding.security_revision,
            binding.audience,
        )

    @staticmethod
    def _copy_manifest(manifest: ContextManifest) -> ContextManifest:
        return ContextManifest(
            tuple(
                ManifestItem(
                    item.source,
                    item.selection_ref,
                    item.revision_ref,
                    item.boundary_sha256,
                    item.record_sha256,
                    item.payload_sha256,
                    item.classification,
                    item.provenance_ref,
                )
                for item in manifest.items
            ),
            manifest.schema,
        )

    def _now(self) -> datetime:
        now = self._wall_clock()
        if type(now) is not datetime or now.tzinfo is None:
            _fail("clock_invalid")
        return now.astimezone(timezone.utc)

    def _prune(self, now: datetime, monotonic_now: float) -> None:
        expired = [
            nonce
            for nonce, entry in self._entries.items()
            if self._entry_expired(entry, now, monotonic_now)
        ]
        for nonce in expired:
            del self._entries[nonce]

    @staticmethod
    def _entry_expired(entry: _Entry, now: datetime, monotonic_now: float) -> bool:
        return now < entry.issued_at or now >= entry.expires_at or monotonic_now >= entry.monotonic_deadline

    def _challenge_token(self, nonce: str) -> str:
        signature = hmac.new(
            self._key, b"private-context-challenge.v1\x00" + nonce.encode("ascii"), hashlib.sha256
        ).hexdigest()
        return f"pc1.{nonce}.{signature}"

    def _verified_nonce(self, token: object) -> str:
        if type(token) is not str:
            _fail("invalid_challenge")
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != "pc1" or not _NONCE_RE.fullmatch(parts[1]) or not _HEX_RE.fullmatch(parts[2]):
            _fail("invalid_challenge")
        if not hmac.compare_digest(parts[2], self._challenge_token(parts[1]).rsplit(".", 1)[1]):
            _fail("invalid_challenge")
        return parts[1]

    @staticmethod
    def _valid_message_ids(user_message_id: int, acknowledgement_message_id: int) -> None:
        if (
            type(user_message_id) is not int
            or type(acknowledgement_message_id) is not int
            or not 1 <= user_message_id <= _MAX_INT64
            or not 1 <= acknowledgement_message_id <= _MAX_INT64
            or user_message_id == acknowledgement_message_id
        ):
            _fail("invalid_message_ids")

    def _envelope(self, entry: _Entry, user_id: int, acknowledgement_id: int, role: str) -> str:
        user_hash = hashlib.sha256(
            f"/context-disclose {self._challenge_token(entry.nonce)}".encode("utf-8")
        ).hexdigest()
        acknowledgement_hash = hashlib.sha256(_ACKNOWLEDGEMENT_CONTENT.encode("utf-8")).hexdigest()
        payload = {
            "acknowledgement_content_sha256": acknowledgement_hash,
            "acknowledgement_message_id": acknowledgement_id,
            "binding": entry.binding._canonical(),
            "expires_at": _utc_text(entry.expires_at),
            "issued_at": _utc_text(entry.issued_at),
            "manifest_digest": entry.manifest.digest,
            "nonce": entry.nonce,
            "provenance": "fresh_chat_command",
            "role": role,
            "user_content_sha256": user_hash,
            "user_message_id": user_id,
        }
        signature = hmac.new(
            self._key, b"private-context-envelope.v1\x00" + _canonical_bytes(payload), hashlib.sha256
        ).hexdigest()
        return f"pce1.{signature}"

    def _signed_pair(self, entry: _Entry, user_id: int, acknowledgement_id: int) -> SignedConfirmationPair:
        return SignedConfirmationPair(
            user_envelope=self._envelope(entry, user_id, acknowledgement_id, "user"),
            acknowledgement_envelope=self._envelope(entry, user_id, acknowledgement_id, "assistant"),
        )

    def _verify_rows(
        self,
        entry: _Entry,
        rows: StoredConfirmationPair | None,
        user_id: int,
        acknowledgement_id: int,
    ) -> VerifiedDisclosure:
        self._valid_message_ids(user_id, acknowledgement_id)
        if type(rows) is not StoredConfirmationPair:
            _fail("readback_failed")
        self._valid_message_ids(rows.user_message_id, rows.acknowledgement_message_id)
        if any(
            type(value) is not str
            for value in (
                rows.sid,
                rows.chat_session_id,
                rows.user_role,
                rows.acknowledgement_role,
                rows.user_content,
                rows.acknowledgement_content,
                rows.user_envelope,
                rows.acknowledgement_envelope,
            )
        ):
            _fail("readback_failed")
        if (
            rows.sid != entry.binding.owner_sid
            or rows.chat_session_id != entry.binding.chat_session_id
            or rows.user_message_id != user_id
            or rows.acknowledgement_message_id != acknowledgement_id
            or rows.user_role != "user"
            or rows.acknowledgement_role != "assistant"
            or rows.user_content != f"/context-disclose {self._challenge_token(entry.nonce)}"
            or rows.acknowledgement_content != _ACKNOWLEDGEMENT_CONTENT
        ):
            _fail("readback_failed")
        expected = self._signed_pair(entry, user_id, acknowledgement_id)
        if not (
            hmac.compare_digest(rows.user_envelope, expected.user_envelope)
            and hmac.compare_digest(rows.acknowledgement_envelope, expected.acknowledgement_envelope)
        ):
            _fail("readback_failed")
        return VerifiedDisclosure(user_id, acknowledgement_id, entry.manifest.digest)
