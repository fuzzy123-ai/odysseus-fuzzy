"""Content-free, post-commit source-change signals for USI rediscovery.

The signal is deliberately a small *hint*, not a source of domain truth.  It
can be duplicated, delayed, or lost without changing the domain commit:
periodic adapter discovery remains the correctness mechanism.  The contract
therefore accepts only opaque USI identities and a bounded revision hint; it
has no field capable of carrying source content, a filesystem path, or a
provider response.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import re
from typing import Any, Mapping

from src.unified_source_index_owner_scope import OwnerScope, OwnerScopeError


CHANGE_SIGNAL_SCHEMA = "odysseus.unified_source_index.change_signal.v1"
MAX_REVISION_HINT_CHARS = 128

_DOMAIN_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_SOURCE_REF_RE = re.compile(r"^(?:usi_source|source_ref)_[0-9a-f]{64}$")
_REVISION_HINT_RE = re.compile(
    r"^(?:usi_version_[0-9a-f]{64}|sha256:[0-9a-f]{64}|rev_[0-9a-f]{8,64})$"
)
_IDEMPOTENCY_KEY_RE = re.compile(r"^usi_change_[0-9a-f]{64}$")


class SourceChangeSignalError(ValueError):
    """Raised when a change signal is incomplete, ambiguous, or unsafe."""


class SourceChangeKind(StrEnum):
    """The full closed set of source observations emitted after a domain commit."""

    CREATED = "created"
    CHANGED = "changed"
    DELETED = "deleted"
    ACCESS_CHANGED = "access_changed"


@dataclass(frozen=True, slots=True)
class SourceChangeSignal:
    """One replay-safe, content-free request for bounded adapter rediscovery."""

    change_kind: SourceChangeKind
    owner_scope: str
    domain: str
    source_ref: str
    revision_hint: str
    idempotency_key: str = ""

    def __post_init__(self) -> None:
        try:
            owner_scope = str(OwnerScope(self.owner_scope))
        except OwnerScopeError as exc:
            raise SourceChangeSignalError("owner_scope must be an immutable opaque owner scope") from exc
        kind = _change_kind(self.change_kind)
        domain = _domain(self.domain)
        source_ref = _source_ref(self.source_ref)
        revision_hint = _revision_hint(self.revision_hint)
        expected_key = _idempotency_key(
            change_kind=kind,
            owner_scope=owner_scope,
            domain=domain,
            source_ref=source_ref,
            revision_hint=revision_hint,
        )
        if self.idempotency_key not in ("", expected_key):
            raise SourceChangeSignalError("idempotency_key does not match the signal identity")

        object.__setattr__(self, "change_kind", kind)
        object.__setattr__(self, "owner_scope", owner_scope)
        object.__setattr__(self, "domain", domain)
        object.__setattr__(self, "source_ref", source_ref)
        object.__setattr__(self, "revision_hint", revision_hint)
        object.__setattr__(self, "idempotency_key", expected_key)

    @classmethod
    def create(
        cls,
        *,
        change_kind: SourceChangeKind | str,
        owner_scope: str,
        domain: str,
        source_ref: str,
        revision_hint: str,
    ) -> "SourceChangeSignal":
        return cls(change_kind, owner_scope, domain, source_ref, revision_hint)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SourceChangeSignal":
        if not isinstance(value, Mapping):
            raise SourceChangeSignalError("signal payload must be a mapping")
        allowed = {
            "schema",
            "change_kind",
            "owner_scope",
            "domain",
            "source_ref",
            "revision_hint",
            "idempotency_key",
        }
        data = dict(value)
        if set(data) != allowed:
            raise SourceChangeSignalError("signal payload fields are not an exact content-free schema")
        if data.get("schema") != CHANGE_SIGNAL_SCHEMA:
            raise SourceChangeSignalError("signal payload schema is invalid")
        return cls(
            change_kind=data["change_kind"],
            owner_scope=data["owner_scope"],
            domain=data["domain"],
            source_ref=data["source_ref"],
            revision_hint=data["revision_hint"],
            idempotency_key=data["idempotency_key"],
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "schema": CHANGE_SIGNAL_SCHEMA,
            "change_kind": self.change_kind.value,
            "owner_scope": self.owner_scope,
            "domain": self.domain,
            "source_ref": self.source_ref,
            "revision_hint": self.revision_hint,
            "idempotency_key": self.idempotency_key,
        }


def _change_kind(value: SourceChangeKind | str) -> SourceChangeKind:
    try:
        return SourceChangeKind(value)
    except (TypeError, ValueError) as exc:
        raise SourceChangeSignalError("change_kind must be one accepted source observation") from exc


def _domain(value: object) -> str:
    if not isinstance(value, str) or not _DOMAIN_RE.fullmatch(value):
        raise SourceChangeSignalError("domain must be a bounded canonical identifier")
    return value


def _source_ref(value: object) -> str:
    if not isinstance(value, str) or not _SOURCE_REF_RE.fullmatch(value):
        raise SourceChangeSignalError("source_ref must be an opaque source identity")
    return value


def _revision_hint(value: object) -> str:
    if not isinstance(value, str) or len(value) > MAX_REVISION_HINT_CHARS or not _REVISION_HINT_RE.fullmatch(value):
        raise SourceChangeSignalError("revision_hint must be a bounded opaque reference")
    return value


def _idempotency_key(
    *,
    change_kind: SourceChangeKind,
    owner_scope: str,
    domain: str,
    source_ref: str,
    revision_hint: str,
) -> str:
    material = {
        "schema": CHANGE_SIGNAL_SCHEMA,
        "change_kind": change_kind.value,
        "owner_scope": owner_scope,
        "domain": domain,
        "source_ref": source_ref,
        "revision_hint": revision_hint,
    }
    encoded = json.dumps(material, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    return "usi_change_" + hashlib.sha256(encoded).hexdigest()


__all__ = [
    "CHANGE_SIGNAL_SCHEMA",
    "MAX_REVISION_HINT_CHARS",
    "SourceChangeKind",
    "SourceChangeSignal",
    "SourceChangeSignalError",
]
