"""Standalone, in-memory Native Knowledge domain store for synthetic fixtures.

This module intentionally has no runtime, provider, filesystem, Personal Docs,
USI, adapter, or plugin dependency.  It is not registered for production use.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from threading import RLock


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


class KnowledgeGenerationMismatch(KnowledgeStoreError):
    """Raised when a generation-fenced operation observes stale authority."""

    def __init__(self) -> None:
        super().__init__("stale_authority")


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
        if type(self) is not KnowledgeVersion:
            raise KnowledgeStoreError("knowledge version must use the exact public type")
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


@dataclass(frozen=True, slots=True, repr=False)
class KnowledgeStoreSnapshot:
    """Detached owner-policy projection bound to one exact store generation."""

    generation: int
    owner_id: str
    policy: str
    records: tuple[KnowledgeVersion, ...]

    def __post_init__(self) -> None:
        if type(self) is not KnowledgeStoreSnapshot:
            raise KnowledgeStoreError("knowledge snapshot must use the exact public type")
        if type(self.generation) is not int or self.generation < 0:
            raise KnowledgeStoreError("snapshot generation must be a non-negative integer")
        _require_identifier(self.owner_id, "owner_id")
        _require_identifier(self.policy, "policy")
        if type(self.records) is not tuple or len(self.records) > MAX_EXPORT_LIMIT:
            raise KnowledgeStoreError("snapshot records must be a bounded exact tuple")

        detached = []
        for record in self.records:
            copied = _detach_version(record)
            if copied.owner_id != self.owner_id or copied.policy != self.policy:
                raise KnowledgeStoreError("snapshot record does not match owner and policy")
            detached.append(copied)
        object.__setattr__(self, "records", tuple(detached))


def _require_identifier(value: str, name: str) -> None:
    if (
        type(value) is not str
        or not _IDENTIFIER.fullmatch(value)
        or any(part in value for part in _SECRET_LIKE_IDENTIFIER_PARTS)
    ):
        raise KnowledgeStoreError(f"{name} must be a bounded public identifier")


def _require_content(content: str) -> None:
    if type(content) is not str or not content or len(content) > MAX_CONTENT_CHARS:
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


def _detach_version(value: KnowledgeVersion) -> KnowledgeVersion:
    """Revalidate and copy one exact version without retaining caller aliases."""
    if type(value) is not KnowledgeVersion:
        raise KnowledgeStoreError("knowledge version must use the exact public type")
    try:
        return KnowledgeVersion(
            knowledge_id=value.knowledge_id,
            owner_id=value.owner_id,
            policy=value.policy,
            version=value.version,
            version_id=value.version_id,
            content=value.content,
        )
    except KnowledgeStoreError:
        raise
    except BaseException:
        raise KnowledgeStoreError("knowledge version is not structurally valid") from None


def _require_export_limit(limit: int) -> None:
    if type(limit) is not int or not 1 <= limit <= MAX_EXPORT_LIMIT:
        raise KnowledgeStoreError("limit must be within the bounded export range")


def _require_generation(value: int) -> None:
    if type(value) is not int or value < 0:
        raise KnowledgeGenerationMismatch()


class NativeKnowledgeStore:
    """Bounded in-memory store with no persistence or integration side effects."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._versions: dict[tuple[str, str], list[KnowledgeVersion]] = {}
        self._tombstones: set[tuple[str, str]] = set()
        self._generation = 0

    @property
    def generation(self) -> int:
        """Return the exact current material-state generation."""
        with self._lock:
            return self._generation

    def create(self, *, owner_id: str, knowledge_id: str, policy: str, content: str) -> KnowledgeVersion:
        """Create an immutable version; identical current content returns that version."""
        for value, name in ((owner_id, "owner_id"), (knowledge_id, "knowledge_id"), (policy, "policy")):
            _require_identifier(value, name)
        _require_content(content)
        with self._lock:
            key = (owner_id, knowledge_id)
            if key in self._tombstones:
                raise KnowledgeTombstoned("tombstoned knowledge cannot be recreated")
            versions = self._versions.get(key)
            if versions and versions[-1].policy == policy and versions[-1].content == content:
                return _detach_version(versions[-1])

            version = len(versions) + 1 if versions else 1
            created = KnowledgeVersion(
                knowledge_id=knowledge_id,
                owner_id=owner_id,
                policy=policy,
                version=version,
                version_id=_version_id(owner_id, knowledge_id, policy, version, content),
                content=content,
            )
            if versions is None:
                self._versions[key] = [created]
            else:
                versions.append(created)
            self._generation += 1
            return _detach_version(created)

    def read_exact(self, *, owner_id: str, knowledge_id: str, policy: str, version: int) -> KnowledgeVersion:
        """Return exactly one immutable authorized version, or fail closed."""
        for value, name in ((owner_id, "owner_id"), (knowledge_id, "knowledge_id"), (policy, "policy")):
            _require_identifier(value, name)
        with self._lock:
            return _detach_version(
                self._read_exact_locked(
                    owner_id=owner_id,
                    knowledge_id=knowledge_id,
                    policy=policy,
                    version=version,
                )
            )

    def _read_exact_locked(
        self, *, owner_id: str, knowledge_id: str, policy: str, version: int
    ) -> KnowledgeVersion:
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

    def read_exact_at_generation(
        self,
        *,
        owner_id: str,
        knowledge_id: str,
        policy: str,
        version: int,
        expected_generation: int,
    ) -> KnowledgeVersion:
        """Return one exact detached version only at the expected generation."""
        _require_generation(expected_generation)
        for value, name in ((owner_id, "owner_id"), (knowledge_id, "knowledge_id"), (policy, "policy")):
            _require_identifier(value, name)
        with self._lock:
            if self._generation != expected_generation:
                raise KnowledgeGenerationMismatch()
            return _detach_version(
                self._read_exact_locked(
                    owner_id=owner_id,
                    knowledge_id=knowledge_id,
                    policy=policy,
                    version=version,
                )
            )

    def tombstone(self, *, owner_id: str, knowledge_id: str, policy: str, version: int, version_id: str) -> None:
        """Tombstone only the current exact version after an exact policy check."""
        for value, name in ((owner_id, "owner_id"), (knowledge_id, "knowledge_id"), (policy, "policy")):
            _require_identifier(value, name)
        version_is_valid = type(version) is int and version >= 1
        version_id_is_valid = type(version_id) is str and _VERSION_ID.fullmatch(version_id) is not None

        with self._lock:
            key = (owner_id, knowledge_id)
            current_versions = self._versions.get(key)
            if not current_versions:
                raise KnowledgeNotFound("knowledge record is not available to this owner")
            current = current_versions[-1]
            if current.policy != policy:
                raise KnowledgeAccessDenied("policy does not authorize tombstone")
            if key in self._tombstones:
                raise KnowledgeTombstoned("knowledge is tombstoned")
            if not version_is_valid or version > len(current_versions):
                raise KnowledgeNotFound("knowledge version is not available")
            selected = current_versions[version - 1]
            if (
                not version_id_is_valid
                or selected != current
                or version_id != current.version_id
            ):
                raise KnowledgeStoreError("tombstone requires the current exact version")
            self._tombstones.add(key)
            self._generation += 1

    def export(self, *, owner_id: str, policy: str, limit: int) -> tuple[KnowledgeVersion, ...]:
        """Return bounded current versions for exactly one owner and policy."""
        _require_identifier(owner_id, "owner_id")
        _require_identifier(policy, "policy")
        _require_export_limit(limit)
        with self._lock:
            return tuple(
                _detach_version(version)
                for version in self._export_locked(owner_id=owner_id, policy=policy, limit=limit)
            )

    def _export_locked(
        self, *, owner_id: str, policy: str, limit: int
    ) -> tuple[KnowledgeVersion, ...]:
        exported = [
            versions[-1]
            for (record_owner, knowledge_id), versions in sorted(self._versions.items())
            if record_owner == owner_id
            and (record_owner, knowledge_id) not in self._tombstones
            and versions[-1].policy == policy
        ]
        return tuple(exported[:limit])

    def capture(self, *, owner_id: str, policy: str, limit: int) -> KnowledgeStoreSnapshot:
        """Capture one detached owner-policy snapshot and its exact generation."""
        _require_identifier(owner_id, "owner_id")
        _require_identifier(policy, "policy")
        _require_export_limit(limit)
        with self._lock:
            return self._capture_locked(owner_id=owner_id, policy=policy, limit=limit)

    def capture_exact_successor(
        self, *, owner_id: str, policy: str, limit: int, prior_generation: int
    ) -> KnowledgeStoreSnapshot:
        """Capture only when exactly one material transition followed prior authority."""
        _require_generation(prior_generation)
        _require_identifier(owner_id, "owner_id")
        _require_identifier(policy, "policy")
        _require_export_limit(limit)
        with self._lock:
            if self._generation != prior_generation + 1:
                raise KnowledgeGenerationMismatch()
            return self._capture_locked(owner_id=owner_id, policy=policy, limit=limit)

    def _capture_locked(
        self, *, owner_id: str, policy: str, limit: int
    ) -> KnowledgeStoreSnapshot:
        return KnowledgeStoreSnapshot(
            generation=self._generation,
            owner_id=owner_id,
            policy=policy,
            records=self._export_locked(owner_id=owner_id, policy=policy, limit=limit),
        )
