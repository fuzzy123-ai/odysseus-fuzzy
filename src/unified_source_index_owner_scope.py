"""Stable opaque owner scopes and versioned legacy-alias resolution for USI.

This is a pure contract module.  It neither reads the auth registry nor mutates
domain data: callers supply accepted immutable auth subject IDs and the alias
history that their canonical owner has already committed.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


OWNER_SCOPE_KIND = "owner"
OWNER_SCOPE_VERSION = 1
_AUTH_SUBJECT_ID_RE = re.compile(r"^owner_[a-z0-9]{32}$")
_OWNER_SCOPE_RE = re.compile(r"^owner:owner_[a-z0-9]{32}$")
_ALIAS_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_LEGACY_OWNER_SCOPE_RE = re.compile(r"^user:([a-z][a-z0-9_-]{0,63})$")


class OwnerScopeError(ValueError):
    """Raised when owner-scope or alias evidence is ambiguous or unsafe."""


@dataclass(frozen=True, slots=True)
class OwnerScope:
    """An opaque USI owner scope derived from an immutable auth subject ID."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not _OWNER_SCOPE_RE.fullmatch(self.value):
            raise OwnerScopeError("owner_scope must be an opaque immutable owner scope")

    @classmethod
    def for_subject_id(cls, subject_id: str) -> "OwnerScope":
        if not isinstance(subject_id, str) or not _AUTH_SUBJECT_ID_RE.fullmatch(subject_id):
            raise OwnerScopeError("subject_id must be an accepted immutable opaque ID")
        return cls(f"{OWNER_SCOPE_KIND}:{subject_id}")

    @property
    def subject_id(self) -> str:
        """Return the opaque subject component; never a username or display alias."""

        return self.value.removeprefix(f"{OWNER_SCOPE_KIND}:")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class OwnerAlias:
    """One immutable display/login alias observation for a stable owner scope.

    Versions are supplied by the canonical account owner. ``previous_alias``
    records a rename chain, but is never followed for lookup: every historical
    alias remains a direct, same-owner bridge for deterministic migration.
    """

    alias: str
    owner_scope: OwnerScope
    version: int
    previous_alias: str | None = None

    def __post_init__(self) -> None:
        _validate_alias(self.alias)
        if not isinstance(self.owner_scope, OwnerScope):
            raise OwnerScopeError("owner_scope must be an OwnerScope")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise OwnerScopeError("alias version must be a positive integer")
        if self.previous_alias is not None:
            _validate_alias(self.previous_alias)
            if self.previous_alias == self.alias:
                raise OwnerScopeError("alias cannot precede itself")


@dataclass(frozen=True, slots=True)
class OwnerScopedSourceRef:
    """A legacy source locator rebound to an opaque owner scope.

    ``locator`` is deliberately opaque to this contract.  Migration does not
    inspect or normalize source content, paths, chunks, entities, or metrics.
    """

    owner_scope: OwnerScope
    locator: str

    def __post_init__(self) -> None:
        if not isinstance(self.owner_scope, OwnerScope):
            raise OwnerScopeError("owner_scope must be an OwnerScope")
        if not isinstance(self.locator, str) or not self.locator:
            raise OwnerScopeError("locator must be explicit")


class OwnerScopeRegistry:
    """Deterministic, content-free lookup table for a supplied alias history."""

    def __init__(self, aliases: Iterable[OwnerAlias]) -> None:
        entries = tuple(aliases)
        if not all(isinstance(entry, OwnerAlias) for entry in entries):
            raise OwnerScopeError("aliases must contain OwnerAlias entries")

        by_alias: dict[str, OwnerAlias] = {}
        alias_owners: dict[str, OwnerScope] = {}
        owner_versions: set[tuple[OwnerScope, int]] = set()
        for entry in entries:
            existing_owner = alias_owners.setdefault(entry.alias, entry.owner_scope)
            if existing_owner != entry.owner_scope:
                raise OwnerScopeError("alias cannot be reused across owner scopes")
            if entry.alias in by_alias:
                raise OwnerScopeError("duplicate alias")
            version_key = (entry.owner_scope, entry.version)
            if version_key in owner_versions:
                raise OwnerScopeError("duplicate owner alias version")
            owner_versions.add(version_key)
            by_alias[entry.alias] = entry

        _reject_alias_cycles(by_alias)
        for entry in entries:
            if entry.previous_alias is None:
                continue
            previous = by_alias.get(entry.previous_alias)
            if previous is None:
                raise OwnerScopeError("alias predecessor is unknown")
            if previous.owner_scope != entry.owner_scope:
                raise OwnerScopeError("alias predecessor crosses owner scope")
            if previous.version >= entry.version:
                raise OwnerScopeError("alias predecessor version must be older")
        self._aliases = dict(by_alias)

    def resolve_alias(self, alias: str, *, expected_owner_scope: OwnerScope | None = None) -> OwnerScope:
        """Resolve an exact alias with optional same-owner fencing.

        There is intentionally no case folding, trimming, Unicode conversion,
        or fallback scan.  Callers must provide an already accepted alias.
        """

        _validate_alias(alias)
        if expected_owner_scope is not None and not isinstance(expected_owner_scope, OwnerScope):
            raise OwnerScopeError("expected_owner_scope must be an OwnerScope")
        entry = self._aliases.get(alias)
        if entry is None:
            raise OwnerScopeError("unknown or stale owner alias")
        if expected_owner_scope is not None and entry.owner_scope != expected_owner_scope:
            raise OwnerScopeError("alias resolves outside expected owner scope")
        return entry.owner_scope

    def alias_version(self, alias: str) -> int:
        """Return the latest committed version for one exact alias."""

        _validate_alias(alias)
        entry = self._aliases.get(alias)
        if entry is None:
            raise OwnerScopeError("unknown or stale owner alias")
        return entry.version

    def migrate_legacy_owner_scope(self, legacy_owner_scope: str) -> OwnerScope:
        """Map a legacy ``user:<username>`` scope through accepted alias history.

        A raw username is accepted only as a bounded input to this bridge.  The
        returned value is always an opaque owner scope, so the username cannot
        become a durable USI source/chunk/entity identity.
        """

        if not isinstance(legacy_owner_scope, str):
            raise OwnerScopeError("legacy owner scope must be text")
        matched = _LEGACY_OWNER_SCOPE_RE.fullmatch(legacy_owner_scope)
        if matched is None:
            raise OwnerScopeError("legacy owner scope must be exact user:<alias>")
        return self.resolve_alias(matched.group(1))

    def migrate_legacy_source_ref(self, legacy_owner_scope: str, locator: str) -> OwnerScopedSourceRef:
        """Rebind one locator without inspecting or rewriting the locator."""

        return OwnerScopedSourceRef(self.migrate_legacy_owner_scope(legacy_owner_scope), locator)


def _validate_alias(alias: object) -> None:
    if not isinstance(alias, str) or not _ALIAS_RE.fullmatch(alias):
        raise OwnerScopeError("alias must be exact lowercase ASCII; normalization is forbidden")


def _reject_alias_cycles(aliases: dict[str, OwnerAlias]) -> None:
    """Reject cycles explicitly even though lookup itself never traverses them."""

    for alias in aliases:
        seen: set[str] = set()
        current = alias
        while current is not None:
            if current in seen:
                raise OwnerScopeError("alias predecessor cycle")
            seen.add(current)
            entry = aliases.get(current)
            current = entry.previous_alias if entry is not None else None
