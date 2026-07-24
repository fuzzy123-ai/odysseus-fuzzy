"""Incremental, watcher-free synchronization for a rebuildable CBM projection.

Only externally supplied, bounded and typed change sets are accepted.  The
module keeps active and working generations separate and performs no source
discovery, filesystem access, process/network activity, canonical-store write,
configuration mutation, hook installation, or live action.
"""

from __future__ import annotations

from dataclasses import InitVar, dataclass
from enum import StrEnum
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from src.code_intelligence_contract import CodeFileMapping
from src.project_version_store import validate_repo_id
from src.unified_source_index_contract import (
    EvidenceRef,
    LineageReason,
    LineageRecord,
    RecordKind,
)


CBM_SYNC_SCHEMA = "odysseus.codebase_memory.sync.v1"
MAX_CHANGE_SET_ITEMS = 10_000
MAX_COMPLETED_CHANGE_SETS = 10_000

_GENERATION_RE = re.compile(r"^cbm_generation_[0-9a-f]{64}$")
_SNAPSHOT_RE = re.compile(r"^usi_snapshot_[0-9a-f]{64}$")
_CHANGE_RE = re.compile(r"^cbm_change_[0-9a-f]{64}$")
_CHANGE_SET_RE = re.compile(r"^cbm_changeset_[0-9a-f]{64}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class CodebaseMemorySyncError(ValueError):
    """Raised when an incremental update is ambiguous, unbounded, or unsafe."""


class FileChangeKind(StrEnum):
    ADD = "add"
    MODIFY = "modify"
    DELETE = "delete"
    RENAME = "rename"


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _identifier(prefix: str, value: Any) -> str:
    return f"cbm_{prefix}_{_digest(value).split(':', 1)[1]}"


def _generation(value: str) -> str:
    if not isinstance(value, str) or not _GENERATION_RE.fullmatch(value):
        raise CodebaseMemorySyncError("generation reference is invalid")
    return value


def _snapshot(value: str) -> str:
    if not isinstance(value, str) or not _SNAPSHOT_RE.fullmatch(value):
        raise CodebaseMemorySyncError("snapshot reference is invalid")
    return value


def _change_kind(value: FileChangeKind | str) -> FileChangeKind:
    if isinstance(value, FileChangeKind):
        return value
    try:
        return FileChangeKind(value)
    except (TypeError, ValueError) as exc:
        raise CodebaseMemorySyncError("change kind is invalid") from exc


def _sequence(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_CHANGE_SET_ITEMS:
        raise CodebaseMemorySyncError("change sequence is outside its bound")
    return value


def _version_evidence(mapping: CodeFileMapping, evidence: EvidenceRef, label: str) -> EvidenceRef:
    if not isinstance(mapping, CodeFileMapping):
        raise CodebaseMemorySyncError(f"{label} mapping must be typed")
    if not isinstance(evidence, EvidenceRef) or evidence.record_kind is not RecordKind.SOURCE_VERSION:
        raise CodebaseMemorySyncError(f"{label} evidence must name a source version")
    if (
        evidence.record_id != mapping.source_version_id
        or evidence.source_id != mapping.source_id
        or evidence.source_version_id != mapping.source_version_id
        or evidence.locator is not None
    ):
        raise CodebaseMemorySyncError(f"{label} evidence does not match the file mapping")
    return evidence


def _mapping_payload(mapping: CodeFileMapping | None) -> Mapping[str, Any] | None:
    return mapping.to_dict() if mapping is not None else None


def _evidence_payload(evidence: EvidenceRef | None) -> Mapping[str, Any] | None:
    return evidence.to_dict() if evidence is not None else None


@dataclass(frozen=True, slots=True)
class CodeFileChange:
    sequence: int
    kind: FileChangeKind
    old_mapping: CodeFileMapping | None = None
    new_mapping: CodeFileMapping | None = None
    old_evidence: EvidenceRef | None = None
    new_evidence: EvidenceRef | None = None
    rename_lineage: tuple[LineageRecord, ...] = ()
    change_id: str = ""

    def __post_init__(self) -> None:
        sequence = _sequence(self.sequence)
        kind = _change_kind(self.kind)
        if not isinstance(self.rename_lineage, tuple) or len(self.rename_lineage) > 256:
            raise CodebaseMemorySyncError("rename_lineage must be a bounded tuple")
        if not all(isinstance(item, LineageRecord) for item in self.rename_lineage):
            raise CodebaseMemorySyncError("rename_lineage must contain USI LineageRecord values")
        old_mapping = self.old_mapping
        new_mapping = self.new_mapping
        old_evidence = self.old_evidence
        new_evidence = self.new_evidence
        if kind is FileChangeKind.ADD:
            if old_mapping is not None or old_evidence is not None or new_mapping is None or new_evidence is None:
                raise CodebaseMemorySyncError("add requires only a new mapping and evidence")
        elif kind is FileChangeKind.DELETE:
            if new_mapping is not None or new_evidence is not None or old_mapping is None or old_evidence is None:
                raise CodebaseMemorySyncError("delete requires only an old mapping and evidence")
        else:
            if any(item is None for item in (old_mapping, new_mapping, old_evidence, new_evidence)):
                raise CodebaseMemorySyncError("modify/rename requires complete old and new evidence")
        if old_mapping is not None and old_evidence is not None:
            old_evidence = _version_evidence(old_mapping, old_evidence, "old")
        if new_mapping is not None and new_evidence is not None:
            new_evidence = _version_evidence(new_mapping, new_evidence, "new")
        mappings = tuple(item for item in (old_mapping, new_mapping) if item is not None)
        if len({item.repo_id for item in mappings}) != 1:
            raise CodebaseMemorySyncError("one change cannot cross repositories")
        if kind is FileChangeKind.MODIFY:
            assert old_mapping is not None and new_mapping is not None
            if old_mapping.relative_path != new_mapping.relative_path:
                raise CodebaseMemorySyncError("modify must preserve the repository-relative path")
            if old_mapping.source_id != new_mapping.source_id:
                raise CodebaseMemorySyncError("modify must preserve canonical source identity")
            if old_mapping.source_version_id == new_mapping.source_version_id:
                raise CodebaseMemorySyncError("modify must advance source-version evidence")
        if kind is FileChangeKind.RENAME:
            assert old_mapping is not None and new_mapping is not None
            if old_mapping.relative_path == new_mapping.relative_path:
                raise CodebaseMemorySyncError("rename must change the repository-relative path")
            if old_mapping.source_id == new_mapping.source_id:
                raise CodebaseMemorySyncError("rename must advance canonical path/source identity")
            if not self.rename_lineage:
                raise CodebaseMemorySyncError("rename requires USI lineage evidence")
            for item in self.rename_lineage:
                if item.reason not in {LineageReason.RENAMED, LineageReason.MOVED}:
                    raise CodebaseMemorySyncError("rename lineage must use RENAMED or MOVED")
                if (
                    item.previous.source_id != old_mapping.source_id
                    or item.previous.source_version_id != old_mapping.source_version_id
                    or item.current.source_id != new_mapping.source_id
                    or item.current.source_version_id != new_mapping.source_version_id
                ):
                    raise CodebaseMemorySyncError("rename lineage ancestry conflicts with mappings")
        elif self.rename_lineage:
            raise CodebaseMemorySyncError("only rename may carry rename_lineage")
        core = {
            "schema": f"{CBM_SYNC_SCHEMA}.change",
            "sequence": sequence,
            "kind": kind.value,
            "old_mapping": _mapping_payload(old_mapping),
            "new_mapping": _mapping_payload(new_mapping),
            "old_evidence": _evidence_payload(old_evidence),
            "new_evidence": _evidence_payload(new_evidence),
            "rename_lineage_ids": [item.lineage_id for item in self.rename_lineage],
        }
        expected = _identifier("change", core)
        if self.change_id not in {"", expected}:
            raise CodebaseMemorySyncError("change_id does not match canonical change")
        object.__setattr__(self, "sequence", sequence)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "old_evidence", old_evidence)
        object.__setattr__(self, "new_evidence", new_evidence)
        object.__setattr__(self, "change_id", expected)

    @property
    def repo_id(self) -> str:
        mapping = self.new_mapping or self.old_mapping
        assert mapping is not None
        return mapping.repo_id

    @property
    def touched_paths(self) -> tuple[str, ...]:
        values = {
            item.relative_path
            for item in (self.old_mapping, self.new_mapping)
            if item is not None
        }
        return tuple(sorted(values))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": f"{CBM_SYNC_SCHEMA}.change",
            "sequence": self.sequence,
            "kind": self.kind.value,
            "change_id": self.change_id,
            "old_mapping_ref": self.old_mapping.fallback_key if self.old_mapping else "",
            "new_mapping_ref": self.new_mapping.fallback_key if self.new_mapping else "",
            "old_source_version_id": self.old_mapping.source_version_id if self.old_mapping else "",
            "new_source_version_id": self.new_mapping.source_version_id if self.new_mapping else "",
            "rename_lineage_ids": [item.lineage_id for item in self.rename_lineage],
        }


@dataclass(frozen=True, slots=True)
class CodeFileChangeSet:
    repo_id: str
    base_generation_ref: str
    target_generation_ref: str
    input_snapshot_ref: str
    changes: tuple[CodeFileChange, ...]
    change_set_id: str = ""

    def __post_init__(self) -> None:
        repo_id = validate_repo_id(self.repo_id)
        base = _generation(self.base_generation_ref)
        target = _generation(self.target_generation_ref)
        snapshot = _snapshot(self.input_snapshot_ref)
        if base == target:
            raise CodebaseMemorySyncError("change set must advance the projection generation")
        if not isinstance(self.changes, tuple) or not 1 <= len(self.changes) <= MAX_CHANGE_SET_ITEMS:
            raise CodebaseMemorySyncError("changes must be a non-empty bounded tuple")
        if not all(isinstance(item, CodeFileChange) for item in self.changes):
            raise CodebaseMemorySyncError("changes must contain typed CodeFileChange values")
        ordered = tuple(sorted(self.changes, key=lambda item: item.sequence))
        if tuple(item.sequence for item in ordered) != tuple(range(1, len(ordered) + 1)):
            raise CodebaseMemorySyncError("change sequences must be unique and contiguous")
        if len({item.change_id for item in ordered}) != len(ordered):
            raise CodebaseMemorySyncError("change ids must be unique")
        if any(item.repo_id != repo_id for item in ordered):
            raise CodebaseMemorySyncError("change set crosses repository scope")
        core = {
            "schema": f"{CBM_SYNC_SCHEMA}.change_set",
            "repo_id": repo_id,
            "base_generation_ref": base,
            "target_generation_ref": target,
            "input_snapshot_ref": snapshot,
            "change_ids": [item.change_id for item in ordered],
        }
        expected = _identifier("changeset", core)
        if self.change_set_id not in {"", expected}:
            raise CodebaseMemorySyncError("change_set_id does not match canonical change set")
        object.__setattr__(self, "repo_id", repo_id)
        object.__setattr__(self, "base_generation_ref", base)
        object.__setattr__(self, "target_generation_ref", target)
        object.__setattr__(self, "input_snapshot_ref", snapshot)
        object.__setattr__(self, "changes", ordered)
        object.__setattr__(self, "change_set_id", expected)


def _file_entry_hash(mapping: CodeFileMapping) -> int:
    return int(_digest(mapping.to_dict()).split(":", 1)[1], 16)


class ProjectionFileIndex:
    """Persistent path index with O(change-depth) lookup and O(1) digest update."""

    __slots__ = ("_root", "_parent", "_path", "_value", "repo_id", "count", "_xor")

    def __init__(
        self,
        *,
        root: Mapping[str, CodeFileMapping] | None = None,
        parent: "ProjectionFileIndex | None" = None,
        path: str = "",
        value: CodeFileMapping | None = None,
        repo_id: str,
        count: int,
        xor_value: int,
    ) -> None:
        self._root = MappingProxyType(dict(root or {})) if parent is None else None
        self._parent = parent
        self._path = path
        self._value = value
        self.repo_id = validate_repo_id(repo_id)
        self.count = count
        self._xor = xor_value

    @classmethod
    def create(cls, repo_id: str, files: Iterable[CodeFileMapping]) -> "ProjectionFileIndex":
        repo_id = validate_repo_id(repo_id)
        values: list[CodeFileMapping] = []
        for item in files:
            if len(values) >= 1_000_000 or not isinstance(item, CodeFileMapping):
                raise CodebaseMemorySyncError("initial file mappings must be typed and bounded")
            values.append(item)
        root: dict[str, CodeFileMapping] = {}
        xor_value = 0
        for item in values:
            if item.repo_id != repo_id or item.relative_path in root:
                raise CodebaseMemorySyncError("initial file mappings cross scope or duplicate a path")
            root[item.relative_path] = item
            xor_value ^= _file_entry_hash(item)
        return cls(
            root=root,
            repo_id=repo_id,
            count=len(root),
            xor_value=xor_value,
        )

    def get(self, path: str) -> CodeFileMapping | None:
        current: ProjectionFileIndex | None = self
        while current is not None and current._parent is not None:
            if current._path == path:
                return current._value
            current = current._parent
        assert current is not None and current._root is not None
        return current._root.get(path)

    def set(self, path: str, mapping: CodeFileMapping) -> "ProjectionFileIndex":
        if not isinstance(mapping, CodeFileMapping) or mapping.repo_id != self.repo_id:
            raise CodebaseMemorySyncError("file index update crosses repository scope")
        if path != mapping.relative_path:
            raise CodebaseMemorySyncError("file index path does not match mapping")
        previous = self.get(path)
        xor_value = self._xor
        count = self.count
        if previous is not None:
            xor_value ^= _file_entry_hash(previous)
        else:
            count += 1
        xor_value ^= _file_entry_hash(mapping)
        return ProjectionFileIndex(
            parent=self,
            path=path,
            value=mapping,
            repo_id=self.repo_id,
            count=count,
            xor_value=xor_value,
        )

    def remove(self, path: str) -> "ProjectionFileIndex":
        previous = self.get(path)
        if previous is None:
            raise CodebaseMemorySyncError("cannot remove an absent projected file")
        return ProjectionFileIndex(
            parent=self,
            path=path,
            value=None,
            repo_id=self.repo_id,
            count=self.count - 1,
            xor_value=self._xor ^ _file_entry_hash(previous),
        )

    @property
    def digest(self) -> str:
        return _digest(
            {
                "schema": f"{CBM_SYNC_SCHEMA}.file_index",
                "repo_id": self.repo_id,
                "count": self.count,
                "xor_sha256": f"{self._xor:064x}",
            }
        )

    def materialize(self) -> tuple[CodeFileMapping, ...]:
        deltas: dict[str, CodeFileMapping | None] = {}
        current: ProjectionFileIndex | None = self
        while current is not None and current._parent is not None:
            deltas.setdefault(current._path, current._value)
            current = current._parent
        assert current is not None and current._root is not None
        values = dict(current._root)
        for path, mapping in deltas.items():
            if mapping is None:
                values.pop(path, None)
            else:
                values[path] = mapping
        return tuple(values[path] for path in sorted(values))


def _unique_evidence(values: Iterable[EvidenceRef]) -> tuple[EvidenceRef, ...]:
    selected: dict[str, EvidenceRef] = {}
    for item in values:
        if not isinstance(item, EvidenceRef) or item.record_kind is not RecordKind.SOURCE_VERSION:
            raise CodebaseMemorySyncError("retained evidence must name source versions")
        current = selected.get(item.record_id)
        if current is not None and current != item:
            raise CodebaseMemorySyncError("one source version has conflicting evidence")
        selected[item.record_id] = item
    return tuple(selected[key] for key in sorted(selected))


def _unique_lineage(values: Iterable[LineageRecord]) -> tuple[LineageRecord, ...]:
    selected: dict[str, LineageRecord] = {}
    for item in values:
        if not isinstance(item, LineageRecord):
            raise CodebaseMemorySyncError("rename history must contain LineageRecord values")
        current = selected.get(item.lineage_id)
        if current is not None and current != item:
            raise CodebaseMemorySyncError("one lineage id has conflicting evidence")
        selected[item.lineage_id] = item
    return tuple(selected[key] for key in sorted(selected))


@dataclass(frozen=True, slots=True)
class ProjectionSyncState:
    repo_id: str
    active_generation_ref: str
    input_snapshot_ref: str
    files: ProjectionFileIndex
    retained_version_evidence: tuple[EvidenceRef, ...]
    rename_lineage: tuple[LineageRecord, ...] = ()
    completed_change_set_ids: tuple[str, ...] = ()
    _validated_incremental_transition: InitVar[bool] = False

    def __post_init__(self, _validated_incremental_transition: bool) -> None:
        repo_id = validate_repo_id(self.repo_id)
        generation = _generation(self.active_generation_ref)
        snapshot = _snapshot(self.input_snapshot_ref)
        if not isinstance(self.files, ProjectionFileIndex) or self.files.repo_id != repo_id:
            raise CodebaseMemorySyncError("projection file index crosses repository scope")
        evidence = _unique_evidence(self.retained_version_evidence)
        lineage = _unique_lineage(self.rename_lineage)
        if not isinstance(self.completed_change_set_ids, tuple) or len(self.completed_change_set_ids) > MAX_COMPLETED_CHANGE_SETS:
            raise CodebaseMemorySyncError("completed change-set history is unbounded")
        completed = tuple(sorted(set(self.completed_change_set_ids)))
        if len(completed) != len(self.completed_change_set_ids) or any(
            not _CHANGE_SET_RE.fullmatch(item) for item in completed
        ):
            raise CodebaseMemorySyncError("completed change-set history is invalid")
        if not isinstance(_validated_incremental_transition, bool):
            raise CodebaseMemorySyncError("incremental transition validation marker is invalid")
        if not _validated_incremental_transition:
            evidence_by_version = {item.record_id: item for item in evidence}
            for mapping in self.files.materialize():
                item = evidence_by_version.get(mapping.source_version_id)
                if item is None or item.source_id != mapping.source_id:
                    raise CodebaseMemorySyncError("active file mapping lacks retained version evidence")
        object.__setattr__(self, "repo_id", repo_id)
        object.__setattr__(self, "active_generation_ref", generation)
        object.__setattr__(self, "input_snapshot_ref", snapshot)
        object.__setattr__(self, "retained_version_evidence", evidence)
        object.__setattr__(self, "rename_lineage", lineage)
        object.__setattr__(self, "completed_change_set_ids", completed)

    @classmethod
    def create(
        cls,
        *,
        repo_id: str,
        active_generation_ref: str,
        input_snapshot_ref: str,
        files: Iterable[CodeFileMapping],
        version_evidence: Iterable[EvidenceRef],
    ) -> "ProjectionSyncState":
        file_items = tuple(files)
        evidence_items = _unique_evidence(version_evidence)
        evidence_by_version = {item.record_id: item for item in evidence_items}
        for mapping in file_items:
            item = evidence_by_version.get(mapping.source_version_id)
            if item is None or item.source_id != mapping.source_id:
                raise CodebaseMemorySyncError("active file mapping lacks retained version evidence")
        return cls(
            repo_id,
            active_generation_ref,
            input_snapshot_ref,
            ProjectionFileIndex.create(repo_id, file_items),
            evidence_items,
            _validated_incremental_transition=True,
        )

    @property
    def projection_digest(self) -> str:
        return _digest(
            {
                "schema": f"{CBM_SYNC_SCHEMA}.state",
                "repo_id": self.repo_id,
                "active_generation_ref": self.active_generation_ref,
                "input_snapshot_ref": self.input_snapshot_ref,
                "file_index_digest": self.files.digest,
                "version_evidence_ids": [item.record_id for item in self.retained_version_evidence],
                "rename_lineage_ids": [item.lineage_id for item in self.rename_lineage],
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": f"{CBM_SYNC_SCHEMA}.state",
            "repo_id": self.repo_id,
            "active_generation_ref": self.active_generation_ref,
            "input_snapshot_ref": self.input_snapshot_ref,
            "file_count": self.files.count,
            "file_index_digest": self.files.digest,
            "retained_version_count": len(self.retained_version_evidence),
            "rename_lineage_count": len(self.rename_lineage),
            "completed_change_set_count": len(self.completed_change_set_ids),
            "projection_digest": self.projection_digest,
            "canonical_writes": 0,
        }


@dataclass(frozen=True, slots=True)
class IncrementalSyncCheckpoint:
    base_state: ProjectionSyncState
    change_set_id: str
    target_generation_ref: str
    target_snapshot_ref: str
    working_files: ProjectionFileIndex
    retained_version_evidence: tuple[EvidenceRef, ...]
    rename_lineage: tuple[LineageRecord, ...]
    applied_change_ids: tuple[str, ...]
    touched_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.base_state, ProjectionSyncState):
            raise CodebaseMemorySyncError("checkpoint base state must be typed")
        if not _CHANGE_SET_RE.fullmatch(self.change_set_id):
            raise CodebaseMemorySyncError("checkpoint change_set_id is invalid")
        _generation(self.target_generation_ref)
        _snapshot(self.target_snapshot_ref)
        if not isinstance(self.working_files, ProjectionFileIndex) or self.working_files.repo_id != self.base_state.repo_id:
            raise CodebaseMemorySyncError("checkpoint working files cross repository scope")
        if len(self.applied_change_ids) > MAX_CHANGE_SET_ITEMS or any(
            not _CHANGE_RE.fullmatch(item) for item in self.applied_change_ids
        ):
            raise CodebaseMemorySyncError("checkpoint applied changes are invalid")
        if len(set(self.applied_change_ids)) != len(self.applied_change_ids):
            raise CodebaseMemorySyncError("checkpoint applied changes contain duplicates")
        if not isinstance(self.touched_paths, tuple) or len(set(self.touched_paths)) != len(self.touched_paths):
            raise CodebaseMemorySyncError("checkpoint touched paths are invalid")
        object.__setattr__(self, "retained_version_evidence", _unique_evidence(self.retained_version_evidence))
        object.__setattr__(self, "rename_lineage", _unique_lineage(self.rename_lineage))
        object.__setattr__(self, "touched_paths", tuple(sorted(self.touched_paths)))

    @property
    def next_sequence(self) -> int:
        return len(self.applied_change_ids) + 1


@dataclass(frozen=True, slots=True)
class IncrementalSyncReceipt:
    change_set_id: str
    completed: bool
    active_state: ProjectionSyncState
    checkpoint: IncrementalSyncCheckpoint | None
    applied_change_count: int
    pending_change_count: int
    examined_file_count: int
    touched_path_count: int
    full_rebuild: bool = False
    canonical_writes: int = 0
    watcher_events: int = 0

    def __post_init__(self) -> None:
        if not _CHANGE_SET_RE.fullmatch(self.change_set_id):
            raise CodebaseMemorySyncError("receipt change_set_id is invalid")
        if not isinstance(self.completed, bool) or not isinstance(self.active_state, ProjectionSyncState):
            raise CodebaseMemorySyncError("receipt completion/state is invalid")
        if self.completed != (self.checkpoint is None):
            raise CodebaseMemorySyncError("receipt checkpoint does not match completion state")
        for name in (
            "applied_change_count",
            "pending_change_count",
            "examined_file_count",
            "touched_path_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise CodebaseMemorySyncError(f"receipt {name} is invalid")
        if self.full_rebuild or self.canonical_writes != 0 or self.watcher_events != 0:
            raise CodebaseMemorySyncError("incremental receipt cannot report rebuild/writes/watchers")

    @property
    def resume_from(self) -> ProjectionSyncState | IncrementalSyncCheckpoint:
        return self.active_state if self.completed else self.checkpoint  # type: ignore[return-value]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": f"{CBM_SYNC_SCHEMA}.receipt",
            "change_set_id": self.change_set_id,
            "completed": self.completed,
            "active_generation_ref": self.active_state.active_generation_ref,
            "active_projection_digest": self.active_state.projection_digest,
            "applied_change_count": self.applied_change_count,
            "pending_change_count": self.pending_change_count,
            "examined_file_count": self.examined_file_count,
            "touched_path_count": self.touched_path_count,
            "full_rebuild": self.full_rebuild,
            "canonical_writes": self.canonical_writes,
            "watcher_events": self.watcher_events,
        }


class IncrementalProjectionSynchronizer:
    def apply(
        self,
        current: ProjectionSyncState | IncrementalSyncCheckpoint,
        change_set: CodeFileChangeSet,
        *,
        max_changes: int | None = None,
    ) -> IncrementalSyncReceipt:
        if not isinstance(change_set, CodeFileChangeSet):
            raise CodebaseMemorySyncError("change_set must be typed")
        if max_changes is not None and (
            isinstance(max_changes, bool) or not isinstance(max_changes, int) or max_changes < 1
        ):
            raise CodebaseMemorySyncError("max_changes must be a positive integer")
        if isinstance(current, ProjectionSyncState):
            if change_set.change_set_id in current.completed_change_set_ids:
                if current.active_generation_ref != change_set.target_generation_ref:
                    raise CodebaseMemorySyncError("completed change set conflicts with active generation")
                return IncrementalSyncReceipt(
                    change_set.change_set_id,
                    True,
                    current,
                    None,
                    0,
                    0,
                    0,
                    0,
                )
            if (
                current.repo_id != change_set.repo_id
                or current.active_generation_ref != change_set.base_generation_ref
            ):
                raise CodebaseMemorySyncError("change set does not extend the active projection")
            checkpoint = IncrementalSyncCheckpoint(
                current,
                change_set.change_set_id,
                change_set.target_generation_ref,
                change_set.input_snapshot_ref,
                current.files,
                current.retained_version_evidence,
                current.rename_lineage,
                (),
                (),
            )
        elif isinstance(current, IncrementalSyncCheckpoint):
            checkpoint = current
            if (
                checkpoint.change_set_id != change_set.change_set_id
                or checkpoint.base_state.repo_id != change_set.repo_id
                or checkpoint.base_state.active_generation_ref != change_set.base_generation_ref
                or checkpoint.target_generation_ref != change_set.target_generation_ref
                or checkpoint.target_snapshot_ref != change_set.input_snapshot_ref
            ):
                raise CodebaseMemorySyncError("resume change set does not match checkpoint")
            expected_applied = tuple(
                item.change_id for item in change_set.changes[: checkpoint.next_sequence - 1]
            )
            if checkpoint.applied_change_ids != expected_applied:
                raise CodebaseMemorySyncError("checkpoint applied prefix conflicts with change set")
        else:
            raise CodebaseMemorySyncError("current sync state must be active state or checkpoint")

        remaining = change_set.changes[checkpoint.next_sequence - 1 :]
        selected = remaining if max_changes is None else remaining[:max_changes]
        files = checkpoint.working_files
        evidence = checkpoint.retained_version_evidence
        lineage = checkpoint.rename_lineage
        applied = list(checkpoint.applied_change_ids)
        touched = set(checkpoint.touched_paths)
        examined = 0
        for change in selected:
            files, examined_delta = self._apply_change(files, change)
            examined += examined_delta
            evidence = _unique_evidence(
                (
                    *evidence,
                    *(item for item in (change.old_evidence, change.new_evidence) if item is not None),
                )
            )
            lineage = _unique_lineage((*lineage, *change.rename_lineage))
            applied.append(change.change_id)
            touched.update(change.touched_paths)

        pending = len(change_set.changes) - len(applied)
        if pending == 0:
            completed_ids = tuple(
                sorted({*checkpoint.base_state.completed_change_set_ids, change_set.change_set_id})
            )
            completed_state = ProjectionSyncState(
                change_set.repo_id,
                change_set.target_generation_ref,
                change_set.input_snapshot_ref,
                files,
                evidence,
                lineage,
                completed_ids,
                _validated_incremental_transition=True,
            )
            return IncrementalSyncReceipt(
                change_set.change_set_id,
                True,
                completed_state,
                None,
                len(selected),
                0,
                examined,
                len({path for change in selected for path in change.touched_paths}),
            )
        next_checkpoint = IncrementalSyncCheckpoint(
            checkpoint.base_state,
            checkpoint.change_set_id,
            checkpoint.target_generation_ref,
            checkpoint.target_snapshot_ref,
            files,
            evidence,
            lineage,
            tuple(applied),
            tuple(sorted(touched)),
        )
        return IncrementalSyncReceipt(
            change_set.change_set_id,
            False,
            checkpoint.base_state,
            next_checkpoint,
            len(selected),
            pending,
            examined,
            len({path for change in selected for path in change.touched_paths}),
        )

    @staticmethod
    def _apply_change(
        files: ProjectionFileIndex,
        change: CodeFileChange,
    ) -> tuple[ProjectionFileIndex, int]:
        if change.kind is FileChangeKind.ADD:
            assert change.new_mapping is not None
            if files.get(change.new_mapping.relative_path) is not None:
                raise CodebaseMemorySyncError("add target already exists in working projection")
            return files.set(change.new_mapping.relative_path, change.new_mapping), 1
        assert change.old_mapping is not None
        current = files.get(change.old_mapping.relative_path)
        if current != change.old_mapping:
            raise CodebaseMemorySyncError("change old mapping does not match working projection")
        if change.kind is FileChangeKind.DELETE:
            return files.remove(change.old_mapping.relative_path), 1
        assert change.new_mapping is not None
        if change.kind is FileChangeKind.MODIFY:
            return files.set(change.new_mapping.relative_path, change.new_mapping), 1
        if files.get(change.new_mapping.relative_path) is not None:
            raise CodebaseMemorySyncError("rename target already exists in working projection")
        removed = files.remove(change.old_mapping.relative_path)
        return removed.set(change.new_mapping.relative_path, change.new_mapping), 2


__all__ = [
    "CBM_SYNC_SCHEMA",
    "CodeFileChange",
    "CodeFileChangeSet",
    "CodebaseMemorySyncError",
    "FileChangeKind",
    "IncrementalProjectionSynchronizer",
    "IncrementalSyncCheckpoint",
    "IncrementalSyncReceipt",
    "ProjectionFileIndex",
    "ProjectionSyncState",
]
