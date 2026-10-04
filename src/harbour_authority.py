"""Authenticated, owner-scoped authority values for Harbour One.

``TrustedPrincipal`` is deliberately not deserializable.  Authenticated
composition creates an ``AuthenticatedPrincipalIssuer`` and passes the exact
issuer object to domain stores as an allowlisted trust root.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
import secrets
from typing import Callable, Iterable


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ATTESTATION_RE = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")
_ISSUANCE_TOKEN = object()
_MAX_TTL_SECONDS = 86_400


class HarbourAuthorityError(ValueError):
    """Bounded public failure from the authenticated authority boundary."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class TrustedPrincipal:
    """Sealed principal that can only be minted by an authenticated issuer."""

    __slots__ = (
        "_issuer_id",
        "_key_id",
        "_subject_id",
        "_owner_id",
        "_auth_context_digest",
        "_issued_at",
        "_expires_at",
        "_nonce",
        "_attestation",
        "_sealed",
    )

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("TrustedPrincipal is sealed")

    def __init__(
        self,
        *,
        issuer_id: str,
        key_id: str,
        subject_id: str,
        owner_id: str,
        auth_context_digest: str,
        issued_at: str,
        expires_at: str,
        nonce: str,
        attestation: str,
        _token: object = None,
    ) -> None:
        if _token is not _ISSUANCE_TOKEN:
            raise HarbourAuthorityError("unauthenticated")
        values = (
            issuer_id,
            key_id,
            subject_id,
            owner_id,
            auth_context_digest,
            issued_at,
            expires_at,
            nonce,
            attestation,
        )
        if any(type(value) is not str for value in values):
            raise HarbourAuthorityError("unauthenticated")
        object.__setattr__(self, "_issuer_id", issuer_id)
        object.__setattr__(self, "_key_id", key_id)
        object.__setattr__(self, "_subject_id", subject_id)
        object.__setattr__(self, "_owner_id", owner_id)
        object.__setattr__(self, "_auth_context_digest", auth_context_digest)
        object.__setattr__(self, "_issued_at", issued_at)
        object.__setattr__(self, "_expires_at", expires_at)
        object.__setattr__(self, "_nonce", nonce)
        object.__setattr__(self, "_attestation", attestation)
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("TrustedPrincipal is immutable")

    @property
    def issuer_id(self) -> str:
        return self._issuer_id

    @property
    def key_id(self) -> str:
        return self._key_id

    @property
    def subject_id(self) -> str:
        return self._subject_id

    @property
    def owner_id(self) -> str:
        return self._owner_id

    @property
    def auth_context_digest(self) -> str:
        return self._auth_context_digest

    @property
    def issued_at(self) -> str:
        return self._issued_at

    @property
    def expires_at(self) -> str:
        return self._expires_at

    @property
    def nonce(self) -> str:
        return self._nonce

    @property
    def attestation(self) -> str:
        return self._attestation

    def __repr__(self) -> str:
        return "TrustedPrincipal(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class PrincipalRef:
    issuer_id: str
    subject_id: str
    owner_id: str
    auth_context_digest: str
    principal_digest: str

    def __repr__(self) -> str:
        return "PrincipalRef(<content-free>)"


class AuthenticatedPrincipalIssuer:
    """Composition-owned trust root and sole principal factory."""

    __slots__ = ("issuer_id", "key_id", "_secret", "_clock")

    def __init__(
        self,
        *,
        issuer_id: str,
        key_id: str,
        secret: bytes,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.issuer_id = _identifier(issuer_id)
        self.key_id = _identifier(key_id)
        if type(secret) is not bytes or len(secret) < 32:
            raise ValueError("issuer secret must be at least 32 bytes")
        self._secret = bytes(secret)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def issue(
        self,
        *,
        subject_id: str,
        owner_id: str,
        auth_context_digest: str,
        ttl_seconds: int = 300,
        nonce: str | None = None,
    ) -> TrustedPrincipal:
        subject = _identifier(subject_id)
        owner = _identifier(owner_id)
        context_digest = _digest(auth_context_digest)
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= _MAX_TTL_SECONDS:
            raise ValueError("ttl_seconds is invalid")
        issued = _utc(self._clock())
        expires = issued + timedelta(seconds=ttl_seconds)
        nonce_value = _identifier(nonce) if nonce is not None else secrets.token_hex(16)
        fields = (
            self.issuer_id,
            self.key_id,
            subject,
            owner,
            context_digest,
            _timestamp(issued),
            _timestamp(expires),
            nonce_value,
        )
        attestation = "hmac-sha256:" + hmac.new(
            self._secret, _principal_bytes(fields), hashlib.sha256
        ).hexdigest()
        return TrustedPrincipal(
            issuer_id=fields[0],
            key_id=fields[1],
            subject_id=fields[2],
            owner_id=fields[3],
            auth_context_digest=fields[4],
            issued_at=fields[5],
            expires_at=fields[6],
            nonce=fields[7],
            attestation=attestation,
            _token=_ISSUANCE_TOKEN,
        )

    def _verify(self, fields: tuple[str, ...], attestation: str) -> bool:
        expected = "hmac-sha256:" + hmac.new(
            self._secret, _principal_bytes(fields), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, attestation)


def authenticate_principal(
    principal: object,
    *,
    trusted_issuers: Iterable[AuthenticatedPrincipalIssuer],
    now: datetime | None = None,
) -> PrincipalRef:
    """Verify one exact sealed principal and return its persistence-safe ref."""

    if type(principal) is not TrustedPrincipal:
        raise HarbourAuthorityError("unauthenticated")
    try:
        fields = (
            principal.issuer_id,
            principal.key_id,
            principal.subject_id,
            principal.owner_id,
            principal.auth_context_digest,
            principal.issued_at,
            principal.expires_at,
            principal.nonce,
        )
        attestation = principal.attestation
        if any(type(value) is not str for value in fields) or type(attestation) is not str:
            raise ValueError
        for value in fields[:4]:
            _identifier(value)
        _digest(fields[4])
        _identifier(fields[7])
        if not _ATTESTATION_RE.fullmatch(attestation):
            raise ValueError
        issued = _parse_timestamp(fields[5])
        expires = _parse_timestamp(fields[6])
        current = _utc(now or datetime.now(timezone.utc))
        if expires <= issued or current < issued or current >= expires:
            raise ValueError
        issuers = tuple(trusted_issuers)
        issuer = next(
            (
                candidate
                for candidate in issuers
                if type(candidate) is AuthenticatedPrincipalIssuer
                and candidate.issuer_id == fields[0]
                and candidate.key_id == fields[1]
            ),
            None,
        )
        if issuer is None or not issuer._verify(fields, attestation):
            raise ValueError
        principal_digest = "sha256:" + hashlib.sha256(
            _principal_bytes(fields + (attestation,))
        ).hexdigest()
        return PrincipalRef(fields[0], fields[2], fields[3], fields[4], principal_digest)
    except (AttributeError, TypeError, ValueError, OverflowError):
        raise HarbourAuthorityError("unauthenticated") from None


def require_owner(principal_ref: PrincipalRef, owner_id: object) -> str:
    if type(owner_id) is not str or not _ID_RE.fullmatch(owner_id):
        raise HarbourAuthorityError("owner_scope_denied")
    if owner_id != principal_ref.owner_id:
        raise HarbourAuthorityError("owner_scope_denied")
    return owner_id


def _principal_bytes(fields: tuple[str, ...]) -> bytes:
    return json.dumps(fields, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def _identifier(value: object) -> str:
    if type(value) is not str or not _ID_RE.fullmatch(value):
        raise ValueError("invalid identifier")
    return value


def _digest(value: object) -> str:
    if type(value) is not str or not _DIGEST_RE.fullmatch(value):
        raise ValueError("invalid digest")
    return value


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None:
        raise ValueError("clock must return an aware datetime")
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        raise ValueError("invalid timestamp")
    return _utc(datetime.fromisoformat(value[:-1] + "+00:00"))


__all__ = [
    "AuthenticatedPrincipalIssuer",
    "HarbourAuthorityError",
    "PrincipalRef",
    "TrustedPrincipal",
    "authenticate_principal",
    "require_owner",
]
