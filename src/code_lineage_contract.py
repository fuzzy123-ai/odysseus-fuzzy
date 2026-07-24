"""Pure value records for code lineage timelines.

This module performs no filesystem, Git, subprocess, engine, network, or
runtime access.  It only validates immutable records that other adapters may
produce later.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timezone
from enum import Enum, StrEnum
import hashlib
import json
import math
import re
from typing import Any, ClassVar, Iterable, Mapping

from src.project_version_store import ProjectVersionStoreError, validate_commit_sha, validate_repo_id
from src.unified_source_index_contract import EvidenceRef, RecordKind, canonical_json


CODE_LINEAGE_CONTRACT_SCHEMA = "odysseus.code_lineage.contract.v1"
MAX_COMMITS = 4096
MAX_OCCURRENCES = 100_000
MAX_FILE_EVENTS = 100_000
MAX_UNCERTAINTIES = 100_000
MAX_LINKS = 100_000
MAX_REFS_PER_RECORD = 256

_CLT_ID_RE = re.compile(r"^clt_(commit|occurrence|file_event|uncertainty|link|bundle)_[0-9a-f]{64}$")
_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_PATH_RE = re.compile(r"^(?!/|~|[A-Za-z]:)(?!.*(?:^|/)\.{1,2}(?:/|$))(?!.*//)[^\x00-\x1f\x7f\\]{1,1024}$")


class CodeLineageContractError(ValueError):
    """Raised when code-lineage evidence is ambiguous, unsafe, or inconsistent."""


class HistoryState(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class FileEventKind(StrEnum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    MOVED = "moved"
    COPIED = "copied"
    RESURRECTED = "resurrected"


class LineageLinkKind(StrEnum):
    CONTINUED = "continued"
    RENAMED = "renamed"
    MOVED = "moved"
    COPIED = "copied"
    SPLIT = "split"
    MERGED = "merged"
    DELETED = "deleted"
    RESURRECTED = "resurrected"


class LinkStatus(StrEnum):
    ACCEPTED = "accepted"
    CANDIDATE = "candidate"


class LineageMethod(StrEnum):
    SAME_BLOB_SAME_PATH = "same_blob_same_path"
    SAME_BLOB_RENAMED_PATH = "same_blob_renamed_path"
    GIT_RENAME_DETECTION = "git_rename_detection"
    STABLE_SYMBOL_SIGNATURE = "stable_symbol_signature"
    AST_NORMALIZED_MATCH = "ast_normalized_match"
    BOUNDED_DIFF_OVERLAP = "bounded_diff_overlap"
    COPY_CANDIDATE = "copy_candidate"
    SEMANTIC_CANDIDATE = "semantic_candidate"
    MANUAL_ASSERTION = "manual_assertion"


class UncertaintyReason(StrEnum):
    SHALLOW_HISTORY = "shallow_history"
    MISSING_OBJECTS = "missing_objects"
    REWRITTEN_HISTORY = "rewritten_history"
    IMPORTED_OLD_CODE = "imported_old_code"
    VENDORED_CODE = "vendored_code"
    GENERATED_CODE = "generated_code"
    AMBIGUOUS_PARENT = "ambiguous_parent"
    COPY_OR_RENAME = "copy_or_rename"
    SPLIT_OR_MERGE = "split_or_merge"
    CLOCK_SKEW = "clock_skew"
    PATH_CASE_AMBIGUITY = "path_case_ambiguity"


class _CanonicalRecord:
    SCHEMA: ClassVar[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            **{item.name: _primitive(getattr(self, item.name)) for item in fields(self)},
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    @classmethod
    def from_json(cls, value: str | bytes):
        return cls.from_dict(_json_mapping(value))


def _primitive(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, Mapping):
        return {str(key): _primitive(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_primitive(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise CodeLineageContractError(f"unsupported canonical value: {type(value).__name__}")


def _canonical_json(value: Any) -> str:
    return json.dumps(_primitive(value), ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _json_mapping(value: str | bytes) -> Mapping[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                raise CodeLineageContractError(f"duplicate JSON field: {key}")
            result[key] = item
        return result

    try:
        payload = json.loads(value, object_pairs_hook=pairs, parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CodeLineageContractError("invalid canonical JSON") from exc
    if not isinstance(payload, Mapping):
        raise CodeLineageContractError("canonical record JSON must contain an object")
    return payload


def _payload(value: Mapping[str, Any], *, schema: str, allowed: set[str], required: set[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CodeLineageContractError("record payload must be a mapping")
    data = dict(value)
    supplied_schema = data.pop("schema", None)
    if supplied_schema is not None and supplied_schema != schema:
        raise CodeLineageContractError(f"expected schema {schema}")
    unknown = set(data) - allowed
    if unknown:
        raise CodeLineageContractError(f"unknown fields: {', '.join(sorted(unknown))}")
    missing = required - set(data)
    if missing:
        raise CodeLineageContractError(f"missing fields: {', '.join(sorted(missing))}")
    return data


def _enum(value: Any, enum_type: type[Enum], field_name: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise CodeLineageContractError(f"{field_name} must be a {enum_type.__name__}")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise CodeLineageContractError(f"invalid {field_name}: {value!r}") from exc


def _token(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value.strip()):
        raise CodeLineageContractError(f"{field_name} must be a bounded token")
    return value.strip()


def _repo_id(value: Any) -> str:
    try:
        return validate_repo_id(value)
    except ProjectVersionStoreError as exc:
        raise CodeLineageContractError(str(exc)) from exc


def _commit_sha(value: Any) -> str:
    try:
        return validate_commit_sha(value)
    except ProjectVersionStoreError as exc:
        raise CodeLineageContractError(str(exc)) from exc


def _timestamp(value: Any, field_name: str, *, required: bool = False) -> str:
    if value in (None, "") and not required:
        return ""
    if not isinstance(value, str) or len(value.strip()) > 40:
        raise CodeLineageContractError(f"{field_name} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip()[:-1] + "+00:00" if value.strip().endswith("Z") else value.strip())
    except ValueError as exc:
        raise CodeLineageContractError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CodeLineageContractError(f"{field_name} must include a timezone")
    utc = parsed.astimezone(timezone.utc)
    timespec = "microseconds" if utc.microsecond else "seconds"
    return utc.isoformat(timespec=timespec).replace("+00:00", "Z")


def _time_window(valid_from: str, valid_to: str) -> None:
    if not (valid_from and valid_to):
        return
    start = datetime.fromisoformat(valid_from.replace("Z", "+00:00"))
    end = datetime.fromisoformat(valid_to.replace("Z", "+00:00"))
    if end < start:
        raise CodeLineageContractError("valid_to must not precede valid_from")


def _stable_id(kind: str, key: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(
        canonical_json({"schema": CODE_LINEAGE_CONTRACT_SCHEMA, "kind": kind, "key": _primitive(key)}).encode("utf-8")
    ).hexdigest()
    return f"clt_{kind}_{digest}"


def _set_id(instance: Any, field_name: str, expected: str) -> None:
    supplied = getattr(instance, field_name)
    if supplied and supplied != expected:
        raise CodeLineageContractError(f"{field_name} does not match canonical identity")
    object.__setattr__(instance, field_name, expected)


def _record_ref(value: Any, kind: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise CodeLineageContractError(f"{field_name} is not a {kind} reference")
    result = value.strip()
    match = _CLT_ID_RE.fullmatch(result)
    if not match or match.group(1) != kind:
        raise CodeLineageContractError(f"{field_name} is not a {kind} reference")
    return result


def _path(value: Any) -> str:
    if not isinstance(value, str) or not _PATH_RE.fullmatch(value.strip()):
        raise CodeLineageContractError("relative_path must be relative and use forward slashes")
    return value.strip()


def _tuple_refs(values: Iterable[Any], *, kind: str, field_name: str, allow_empty: bool = False) -> tuple[str, ...]:
    try:
        items = tuple(values)
    except TypeError as exc:
        raise CodeLineageContractError(f"{field_name} must be iterable") from exc
    if (not allow_empty and not items) or len(items) > MAX_REFS_PER_RECORD:
        raise CodeLineageContractError(f"{field_name} must be non-empty and bounded")
    refs = tuple(_record_ref(item, kind, field_name) for item in items)
    if len(set(refs)) != len(refs):
        raise CodeLineageContractError(f"{field_name} contains duplicates")
    return tuple(sorted(refs))


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CodeLineageContractError("confidence must be numeric")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise CodeLineageContractError("confidence must be between 0 and 1")
    return result


def _commit_tuple(values: Iterable[Any], field_name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    try:
        items = tuple(values)
    except TypeError as exc:
        raise CodeLineageContractError(f"{field_name} must be iterable") from exc
    if (not allow_empty and not items) or len(items) > MAX_REFS_PER_RECORD:
        raise CodeLineageContractError(f"{field_name} must be non-empty and bounded")
    normalized = tuple(_commit_sha(item) for item in items)
    if len(set(normalized)) != len(normalized):
        raise CodeLineageContractError(f"{field_name} contains duplicates")
    return normalized


def _same_repo(record_repo: str, expected_repo: str, label: str) -> None:
    if record_repo != expected_repo:
        raise CodeLineageContractError(f"{label} escapes lineage bundle repo")


def _valid_event_cardinality(kind: FileEventKind, before: tuple[str, ...], after: tuple[str, ...]) -> bool:
    return {
        FileEventKind.ADDED: len(before) == 0 and len(after) >= 1,
        FileEventKind.MODIFIED: len(before) >= 1 and len(after) >= 1,
        FileEventKind.DELETED: len(before) >= 1 and len(after) == 0,
        FileEventKind.RENAMED: len(before) == 1 and len(after) == 1,
        FileEventKind.MOVED: len(before) == 1 and len(after) == 1,
        FileEventKind.COPIED: len(before) >= 1 and len(after) >= 1,
        FileEventKind.RESURRECTED: len(before) >= 1 and len(after) >= 1,
    }[kind]


def _valid_link_cardinality(kind: LineageLinkKind, sources: tuple[str, ...], targets: tuple[str, ...]) -> bool:
    return {
        LineageLinkKind.CONTINUED: len(sources) == 1 and len(targets) == 1,
        LineageLinkKind.RENAMED: len(sources) == 1 and len(targets) == 1,
        LineageLinkKind.MOVED: len(sources) == 1 and len(targets) == 1,
        LineageLinkKind.COPIED: len(sources) >= 1 and len(targets) >= 1,
        LineageLinkKind.SPLIT: len(sources) == 1 and len(targets) >= 2,
        LineageLinkKind.MERGED: len(sources) >= 2 and len(targets) == 1,
        LineageLinkKind.DELETED: len(sources) >= 1 and len(targets) == 0,
        LineageLinkKind.RESURRECTED: len(sources) >= 1 and len(targets) >= 1,
    }[kind]


@dataclass(frozen=True, slots=True)
class CommitEvidenceRef(_CanonicalRecord):
    repo_id: str
    commit_id: str
    parent_ids: tuple[str, ...]
    authored_at: str
    committed_at: str
    indexed_at: str
    history_state: HistoryState
    shallow_boundary: bool = False
    missing_parent_ids: tuple[str, ...] = ()
    commit_ref: str = ""
    SCHEMA: ClassVar[str] = f"{CODE_LINEAGE_CONTRACT_SCHEMA}.commit_evidence"

    def __post_init__(self) -> None:
        repo = _repo_id(self.repo_id)
        commit = _commit_sha(self.commit_id)
        parents = _commit_tuple(self.parent_ids, "parent_ids", allow_empty=True)
        missing = _commit_tuple(self.missing_parent_ids, "missing_parent_ids", allow_empty=True)
        if any(item not in parents for item in missing):
            raise CodeLineageContractError("missing_parent_ids must be a subset of parent_ids")
        state = _enum(self.history_state, HistoryState, "history_state")
        if state is HistoryState.COMPLETE and (self.shallow_boundary or missing):
            raise CodeLineageContractError("complete history cannot have shallow or missing parents")
        object.__setattr__(self, "repo_id", repo)
        object.__setattr__(self, "commit_id", commit)
        object.__setattr__(self, "parent_ids", parents)
        object.__setattr__(self, "authored_at", _timestamp(self.authored_at, "authored_at", required=True))
        object.__setattr__(self, "committed_at", _timestamp(self.committed_at, "committed_at", required=True))
        object.__setattr__(self, "indexed_at", _timestamp(self.indexed_at, "indexed_at", required=True))
        if not isinstance(self.shallow_boundary, bool):
            raise CodeLineageContractError("shallow_boundary must be boolean")
        object.__setattr__(self, "history_state", state)
        object.__setattr__(self, "shallow_boundary", self.shallow_boundary)
        object.__setattr__(self, "missing_parent_ids", missing)
        _set_id(self, "commit_ref", _stable_id("commit", {"repo_id": repo, "commit_id": commit}))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CommitEvidenceRef":
        names = {item.name for item in fields(cls)}
        required = names - {"shallow_boundary", "missing_parent_ids", "commit_ref"}
        data = _payload(value, schema=cls.SCHEMA, allowed=names, required=required)
        data["parent_ids"] = tuple(data["parent_ids"])
        data["missing_parent_ids"] = tuple(data.get("missing_parent_ids", ()))
        return cls(**data)


@dataclass(frozen=True, slots=True)
class CodeOccurrenceRef(_CanonicalRecord):
    evidence: EvidenceRef
    repo_id: str
    commit_ref: str
    relative_path: str
    first_seen_at: str
    history_first_observed_at: str = ""
    occurrence_ref: str = ""
    SCHEMA: ClassVar[str] = f"{CODE_LINEAGE_CONTRACT_SCHEMA}.occurrence"

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, EvidenceRef):
            raise CodeLineageContractError("evidence must be a USI EvidenceRef")
        if self.evidence.record_kind not in {RecordKind.SOURCE_VERSION, RecordKind.CHUNK, RecordKind.ENTITY}:
            raise CodeLineageContractError("occurrence evidence must name version, chunk, or entity")
        repo = _repo_id(self.repo_id)
        commit_ref = _record_ref(self.commit_ref, "commit", "commit_ref")
        path = _path(self.relative_path)
        first_seen = _timestamp(self.first_seen_at, "first_seen_at", required=True)
        history_first = _timestamp(self.history_first_observed_at, "history_first_observed_at")
        object.__setattr__(self, "repo_id", repo)
        object.__setattr__(self, "commit_ref", commit_ref)
        object.__setattr__(self, "relative_path", path)
        object.__setattr__(self, "first_seen_at", first_seen)
        object.__setattr__(self, "history_first_observed_at", history_first)
        _set_id(
            self,
            "occurrence_ref",
            _stable_id("occurrence", {"repo_id": repo, "commit_ref": commit_ref, "record_id": self.evidence.record_id, "relative_path": path}),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CodeOccurrenceRef":
        names = {item.name for item in fields(cls)}
        required = names - {"history_first_observed_at", "occurrence_ref"}
        data = _payload(value, schema=cls.SCHEMA, allowed=names, required=required)
        data["evidence"] = EvidenceRef.from_dict(data["evidence"])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class FileEvent(_CanonicalRecord):
    event_kind: FileEventKind
    commit_ref: str
    before_occurrence_refs: tuple[str, ...]
    after_occurrence_refs: tuple[str, ...]
    event_ref: str = ""
    SCHEMA: ClassVar[str] = f"{CODE_LINEAGE_CONTRACT_SCHEMA}.file_event"

    def __post_init__(self) -> None:
        kind = _enum(self.event_kind, FileEventKind, "event_kind")
        commit = _record_ref(self.commit_ref, "commit", "commit_ref")
        before = _tuple_refs(self.before_occurrence_refs, kind="occurrence", field_name="before_occurrence_refs", allow_empty=True)
        after = _tuple_refs(self.after_occurrence_refs, kind="occurrence", field_name="after_occurrence_refs", allow_empty=True)
        if not _valid_event_cardinality(kind, before, after):
            raise CodeLineageContractError("file event cardinality does not match event kind")
        object.__setattr__(self, "event_kind", kind)
        object.__setattr__(self, "commit_ref", commit)
        object.__setattr__(self, "before_occurrence_refs", before)
        object.__setattr__(self, "after_occurrence_refs", after)
        _set_id(self, "event_ref", _stable_id("file_event", {"kind": kind.value, "commit_ref": commit, "before": before, "after": after}))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FileEvent":
        names = {item.name for item in fields(cls)}
        required = names - {"event_ref"}
        data = _payload(value, schema=cls.SCHEMA, allowed=names, required=required)
        data["before_occurrence_refs"] = tuple(data["before_occurrence_refs"])
        data["after_occurrence_refs"] = tuple(data["after_occurrence_refs"])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class UncertaintyRecord(_CanonicalRecord):
    reason: UncertaintyReason
    occurrence_refs: tuple[str, ...]
    commit_refs: tuple[str, ...]
    detail_code: str
    blocks_absolute_creation_claim: bool
    uncertainty_ref: str = ""
    SCHEMA: ClassVar[str] = f"{CODE_LINEAGE_CONTRACT_SCHEMA}.uncertainty"

    def __post_init__(self) -> None:
        reason = _enum(self.reason, UncertaintyReason, "reason")
        occurrences = _tuple_refs(self.occurrence_refs, kind="occurrence", field_name="occurrence_refs", allow_empty=True)
        commits = _tuple_refs(self.commit_refs, kind="commit", field_name="commit_refs", allow_empty=True)
        if not occurrences and not commits:
            raise CodeLineageContractError("uncertainty must scope at least one occurrence or commit")
        if not isinstance(self.blocks_absolute_creation_claim, bool):
            raise CodeLineageContractError("blocks_absolute_creation_claim must be boolean")
        detail = _token(self.detail_code, "detail_code")
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "occurrence_refs", occurrences)
        object.__setattr__(self, "commit_refs", commits)
        object.__setattr__(self, "detail_code", detail)
        _set_id(self, "uncertainty_ref", _stable_id("uncertainty", {"reason": reason.value, "occurrences": occurrences, "commits": commits, "detail_code": detail}))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "UncertaintyRecord":
        names = {item.name for item in fields(cls)}
        required = names - {"uncertainty_ref"}
        data = _payload(value, schema=cls.SCHEMA, allowed=names, required=required)
        data["occurrence_refs"] = tuple(data["occurrence_refs"])
        data["commit_refs"] = tuple(data["commit_refs"])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class LineageLink(_CanonicalRecord):
    link_kind: LineageLinkKind
    status: LinkStatus
    source_occurrence_refs: tuple[str, ...]
    target_occurrence_refs: tuple[str, ...]
    method: LineageMethod
    confidence: float
    supporting_commit_refs: tuple[str, ...]
    supporting_event_refs: tuple[str, ...]
    uncertainty_refs: tuple[str, ...]
    valid_from: str
    valid_to: str = ""
    link_ref: str = ""
    SCHEMA: ClassVar[str] = f"{CODE_LINEAGE_CONTRACT_SCHEMA}.lineage_link"

    def __post_init__(self) -> None:
        kind = _enum(self.link_kind, LineageLinkKind, "link_kind")
        status = _enum(self.status, LinkStatus, "status")
        method = _enum(self.method, LineageMethod, "method")
        sources = _tuple_refs(self.source_occurrence_refs, kind="occurrence", field_name="source_occurrence_refs", allow_empty=True)
        targets = _tuple_refs(self.target_occurrence_refs, kind="occurrence", field_name="target_occurrence_refs", allow_empty=True)
        uncertainties = _tuple_refs(self.uncertainty_refs, kind="uncertainty", field_name="uncertainty_refs", allow_empty=True)
        confidence = _confidence(self.confidence)
        if status is LinkStatus.ACCEPTED and not _valid_link_cardinality(kind, sources, targets):
            raise CodeLineageContractError("accepted lineage cardinality does not match link kind")
        if status is LinkStatus.CANDIDATE and not uncertainties:
            raise CodeLineageContractError("candidate lineage requires uncertainty evidence")
        if status is LinkStatus.CANDIDATE and confidence >= 1.0:
            raise CodeLineageContractError("candidate lineage confidence must remain below 1")
        if status is LinkStatus.ACCEPTED and method is LineageMethod.SEMANTIC_CANDIDATE:
            raise CodeLineageContractError("semantic candidates cannot become accepted lineage")
        object.__setattr__(self, "link_kind", kind)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "source_occurrence_refs", sources)
        object.__setattr__(self, "target_occurrence_refs", targets)
        object.__setattr__(self, "method", method)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "supporting_commit_refs", _tuple_refs(self.supporting_commit_refs, kind="commit", field_name="supporting_commit_refs"))
        object.__setattr__(self, "supporting_event_refs", _tuple_refs(self.supporting_event_refs, kind="file_event", field_name="supporting_event_refs", allow_empty=True))
        object.__setattr__(self, "uncertainty_refs", uncertainties)
        object.__setattr__(self, "valid_from", _timestamp(self.valid_from, "valid_from", required=True))
        object.__setattr__(self, "valid_to", _timestamp(self.valid_to, "valid_to"))
        _time_window(self.valid_from, self.valid_to)
        _set_id(self, "link_ref", _stable_id("link", {"kind": kind.value, "status": status.value, "sources": sources, "targets": targets, "method": method.value, "commits": self.supporting_commit_refs, "events": self.supporting_event_refs, "uncertainties": uncertainties}))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LineageLink":
        names = {item.name for item in fields(cls)}
        required = names - {"valid_to", "link_ref"}
        data = _payload(value, schema=cls.SCHEMA, allowed=names, required=required)
        for name in ("source_occurrence_refs", "target_occurrence_refs", "supporting_commit_refs", "supporting_event_refs", "uncertainty_refs"):
            data[name] = tuple(data[name])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class LineageBundle(_CanonicalRecord):
    repo_id: str
    commits: tuple[CommitEvidenceRef, ...]
    occurrences: tuple[CodeOccurrenceRef, ...]
    file_events: tuple[FileEvent, ...]
    uncertainties: tuple[UncertaintyRecord, ...]
    links: tuple[LineageLink, ...]
    bundle_ref: str = ""
    SCHEMA: ClassVar[str] = f"{CODE_LINEAGE_CONTRACT_SCHEMA}.bundle"

    def __post_init__(self) -> None:
        repo = _repo_id(self.repo_id)
        commits = _sorted_records(self.commits, "commit_ref", CommitEvidenceRef, MAX_COMMITS)
        occurrences = _sorted_records(self.occurrences, "occurrence_ref", CodeOccurrenceRef, MAX_OCCURRENCES)
        events = _sorted_records(self.file_events, "event_ref", FileEvent, MAX_FILE_EVENTS)
        uncertainties = _sorted_records(self.uncertainties, "uncertainty_ref", UncertaintyRecord, MAX_UNCERTAINTIES)
        links = _sorted_records(self.links, "link_ref", LineageLink, MAX_LINKS)
        commit_map = {item.commit_ref: item for item in commits}
        occurrence_map = {item.occurrence_ref: item for item in occurrences}
        event_refs = {item.event_ref for item in events}
        uncertainty_map = {item.uncertainty_ref: item for item in uncertainties}
        for commit in commits:
            _same_repo(commit.repo_id, repo, "commit")
        for occurrence in occurrences:
            _same_repo(occurrence.repo_id, repo, "occurrence")
            if occurrence.commit_ref not in commit_map:
                raise CodeLineageContractError("occurrence references unknown commit")
        for event in events:
            if event.commit_ref not in commit_map:
                raise CodeLineageContractError("file event references unknown commit")
            event_occurrences = event.before_occurrence_refs + event.after_occurrence_refs
            if any(ref not in occurrence_map for ref in event_occurrences):
                raise CodeLineageContractError("file event references unknown occurrence")
            _validate_event_paths(event, occurrence_map)
        for uncertainty in uncertainties:
            if any(ref not in occurrence_map for ref in uncertainty.occurrence_refs):
                raise CodeLineageContractError("uncertainty references unknown occurrence")
            if any(ref not in commit_map for ref in uncertainty.commit_refs):
                raise CodeLineageContractError("uncertainty references unknown commit")
        scoped_creation_uncertainty = {
            occurrence_ref
            for uncertainty in uncertainties
            if uncertainty.blocks_absolute_creation_claim
            for occurrence_ref in uncertainty.occurrence_refs
        }
        for occurrence in occurrences:
            if not occurrence.history_first_observed_at and occurrence.occurrence_ref not in scoped_creation_uncertainty:
                raise CodeLineageContractError("occurrence without history_first_observed_at requires uncertainty")
        for link in links:
            if any(ref not in occurrence_map for ref in link.source_occurrence_refs + link.target_occurrence_refs):
                raise CodeLineageContractError("lineage link references unknown occurrence")
            if any(ref not in commit_map for ref in link.supporting_commit_refs):
                raise CodeLineageContractError("lineage link references unknown commit")
            if any(ref not in event_refs for ref in link.supporting_event_refs):
                raise CodeLineageContractError("lineage link references unknown file event")
            if any(ref not in uncertainty_map for ref in link.uncertainty_refs):
                raise CodeLineageContractError("lineage link references unknown uncertainty")
        object.__setattr__(self, "repo_id", repo)
        object.__setattr__(self, "commits", commits)
        object.__setattr__(self, "occurrences", occurrences)
        object.__setattr__(self, "file_events", events)
        object.__setattr__(self, "uncertainties", uncertainties)
        object.__setattr__(self, "links", links)
        _set_id(self, "bundle_ref", _stable_id("bundle", {"repo_id": repo, "links": tuple(item.link_ref for item in links), "events": tuple(item.event_ref for item in events)}))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LineageBundle":
        names = {item.name for item in fields(cls)}
        required = names - {"bundle_ref"}
        data = _payload(value, schema=cls.SCHEMA, allowed=names, required=required)
        data["commits"] = tuple(CommitEvidenceRef.from_dict(item) for item in data["commits"])
        data["occurrences"] = tuple(CodeOccurrenceRef.from_dict(item) for item in data["occurrences"])
        data["file_events"] = tuple(FileEvent.from_dict(item) for item in data["file_events"])
        data["uncertainties"] = tuple(UncertaintyRecord.from_dict(item) for item in data["uncertainties"])
        data["links"] = tuple(LineageLink.from_dict(item) for item in data["links"])
        return cls(**data)


def _sorted_records(values: Iterable[Any], identity_field: str, expected_type: type[Any], maximum: int) -> tuple[Any, ...]:
    items = tuple(values)
    if len(items) > maximum or not all(isinstance(item, expected_type) for item in items):
        raise CodeLineageContractError(f"{identity_field} records must be typed and bounded")
    by_id = {getattr(item, identity_field): item for item in items}
    if len(by_id) != len(items):
        raise CodeLineageContractError(f"{identity_field} records contain duplicates")
    return tuple(by_id[key] for key in sorted(by_id))


def _validate_event_paths(event: FileEvent, occurrences: Mapping[str, CodeOccurrenceRef]) -> None:
    before_paths = {occurrences[ref].relative_path for ref in event.before_occurrence_refs}
    after_paths = {occurrences[ref].relative_path for ref in event.after_occurrence_refs}
    if event.event_kind in {FileEventKind.RENAMED, FileEventKind.MOVED, FileEventKind.COPIED} and before_paths == after_paths:
        raise CodeLineageContractError("path-changing file events require changed paths")


_RECORD_BY_SCHEMA = {
    cls.SCHEMA: cls
    for cls in (
        CommitEvidenceRef,
        CodeOccurrenceRef,
        FileEvent,
        UncertaintyRecord,
        LineageLink,
        LineageBundle,
    )
}


def record_from_dict(value: Mapping[str, Any]) -> _CanonicalRecord:
    schema = value.get("schema") if isinstance(value, Mapping) else None
    record_type = _RECORD_BY_SCHEMA.get(schema)
    if record_type is None:
        raise CodeLineageContractError("unknown or missing code-lineage record schema")
    return record_type.from_dict(value)


def record_from_json(value: str | bytes) -> _CanonicalRecord:
    return record_from_dict(_json_mapping(value))
