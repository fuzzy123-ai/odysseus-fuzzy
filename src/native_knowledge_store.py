"""Standalone, in-memory Native Knowledge domain store for synthetic fixtures.

This module intentionally has no runtime, provider, filesystem, Personal Docs,
USI, adapter, or plugin dependency.  It is not registered for production use.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re


MAX_CONTENT_CHARS = 16_384
MAX_EXPORT_LIMIT = 100
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}\Z")
_VERSION_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SECRET_LIKE_IDENTIFIER_PARTS = ("secret", "token", "password", "credential", "api_key", "apikey", "bearer")


class KnowledgeStoreError(ValueError):
    """Base error for bounded Native Knowledge store operations."""


class KnowledgeNotFound(KnowledgeStoreError):
    """Raised when an owner-scoped knowledge record or version is absent."""


class KnowledgeAccessDenied(KnowledgeStoreError):
    """Raised when an owner or policy does not authorize the operation."""


class KnowledgeTombstoned(KnowledgeStoreError):
    """Raised when a tombstoned record is accessed or changed."""


@dataclass(frozen=True)
class KnowledgeVersion:
    """An immutable, owner- and policy-bound synthetic Knowledge version."""

    knowledge_id: str
    owner_id: str
    policy: str
    version: int
    version_id: str
    content: str

    def __post_init__(self) -> None:
        _require_identifier(self.knowledge_id, "knowledge_id")
        _require_identifier(self.owner_id, "owner_id")
        _require_identifier(self.policy, "policy")
        _require_content(self.content)
        if type(self.version) is not int or self.version < 1:
            raise KnowledgeStoreError("version must be a positive integer")
        expected_version_id = _version_id(
            self.owner_id, self.knowledge_id, self.policy, self.version, self.content
        )
        if not isinstance(self.version_id, str) or not _VERSION_ID.fullmatch(self.version_id):
            raise KnowledgeStoreError("version_id must use canonical sha256 format")
        if self.version_id != expected_version_id:
            raise KnowledgeStoreError("version_id does not match immutable version fields")


def _require_identifier(value: str, name: str) -> None:
    if (
        not isinstance(value, str)
        or not _IDENTIFIER.fullmatch(value)
        or any(part in value for part in _SECRET_LIKE_IDENTIFIER_PARTS)
    ):
        raise KnowledgeStoreError(f"{name} must be a bounded public identifier")


def _require_content(content: str) -> None:
    if not isinstance(content, str) or not content or len(content) > MAX_CONTENT_CHARS:
        raise KnowledgeStoreError("content must be a bounded non-empty synthetic value")


def _version_id(owner_id: str, knowledge_id: str, policy: str, version: int, content: str) -> str:
    payload = json.dumps(
        {
            "content": content,
            "knowledge_id": knowledge_id,
            "owner_id": owner_id,
            "policy": policy,
            "version": version,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return "sha256:" + sha256(payload.encode("utf-8")).hexdigest()


class NativeKnowledgeStore:
    """Bounded in-memory store with no persistence or integration side effects."""

    def __init__(self) -> None:
        self._versions: dict[tuple[str, str], list[KnowledgeVersion]] = {}
        self._tombstones: set[tuple[str, str]] = set()

    def create(self, *, owner_id: str, knowledge_id: str, policy: str, content: str) -> KnowledgeVersion:
        """Create an immutable version; identical current content returns that version."""
        for value, name in ((owner_id, "owner_id"), (knowledge_id, "knowledge_id"), (policy, "policy")):
            _require_identifier(value, name)
        _require_content(content)
        key = (owner_id, knowledge_id)
        if key in self._tombstones:
            raise KnowledgeTombstoned("tombstoned knowledge cannot be recreated")
        versions = self._versions.setdefault(key, [])
        if versions and versions[-1].policy == policy and versions[-1].content == content:
            return versions[-1]
        version = len(versions) + 1
        created = KnowledgeVersion(
            knowledge_id=knowledge_id,
            owner_id=owner_id,
            policy=policy,
            version=version,
            version_id=_version_id(owner_id, knowledge_id, policy, version, content),
            content=content,
        )
        versions.append(created)
        return created

    def read_exact(self, *, owner_id: str, knowledge_id: str, policy: str, version: int) -> KnowledgeVersion:
        """Return exactly one immutable authorized version, or fail closed."""
        key = (owner_id, knowledge_id)
        if key in self._tombstones:
            raise KnowledgeTombstoned("knowledge is tombstoned")
        versions = self._versions.get(key)
        if not versions:
            raise KnowledgeNotFound("knowledge record is not available to this owner")
        if type(version) is not int or version < 1 or version > len(versions):
            raise KnowledgeNotFound("knowledge version is not available")
        selected = versions[version - 1]
        if selected.policy != policy:
            raise KnowledgeAccessDenied("policy does not authorize exact read")
        return selected

    def tombstone(self, *, owner_id: str, knowledge_id: str, policy: str, version: int, version_id: str) -> None:
        """Tombstone only the current exact version after an exact policy check."""
        current_versions = self._versions.get((owner_id, knowledge_id))
        if not current_versions:
            raise KnowledgeNotFound("knowledge record is not available to this owner")
        current = current_versions[-1]
        if current.policy != policy:
            raise KnowledgeAccessDenied("policy does not authorize tombstone")
        selected = self.read_exact(
            owner_id=owner_id, knowledge_id=knowledge_id, policy=policy, version=version
        )
        if selected != current or version_id != current.version_id:
            raise KnowledgeStoreError("tombstone requires the current exact version")
        self._tombstones.add((owner_id, knowledge_id))

    def export(self, *, owner_id: str, policy: str, limit: int) -> tuple[KnowledgeVersion, ...]:
        """Return bounded current versions for exactly one owner and policy."""
        _require_identifier(owner_id, "owner_id")
        _require_identifier(policy, "policy")
        if type(limit) is not int or not 1 <= limit <= MAX_EXPORT_LIMIT:
            raise KnowledgeStoreError("limit must be within the bounded export range")
        exported = [
            versions[-1]
            for (record_owner, knowledge_id), versions in sorted(self._versions.items())
            if record_owner == owner_id
            and (record_owner, knowledge_id) not in self._tombstones
            and versions[-1].policy == policy
        ]
        return tuple(exported[:limit])
