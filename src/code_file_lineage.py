"""Pure, evidence-bound file lineage construction.

This module consumes the bounded, already-read ``GitHistorySlice`` from
``code_lineage_git_adapter`` and USI evidence supplied by its caller.  It does
not open repositories, inspect source content, invoke Git, or use similarity
matching.  A Git rename with a non-exact score, a copy, a case-only rename, or
an incomplete history is deliberately represented as uncertainty rather than
as an absolute creation claim.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import PurePosixPath
import re
from typing import Iterable, Mapping

from src.code_lineage_contract import (
    CodeOccurrenceRef,
    CommitEvidenceRef,
    FileEvent,
    FileEventKind,
    GitBlobEvidenceRef,
    HistoryState,
    LineageBundle,
    LineageLink,
    LineageLinkKind,
    LineageMethod,
    LinkStatus,
    SourceVersionEvidenceBinding,
    UncertaintyReason,
    UncertaintyRecord,
)
from src.code_lineage_git_adapter import GitHistorySlice, GitPathChange
from src.unified_source_index_contract import (
    CodeRangeLocator,
    EvidenceRef,
    RecordKind,
    SourceKind,
    SourceVersionRecord,
    make_source_id,
)


class CodeFileLineageError(ValueError):
    """Raised when supplied history evidence cannot support a safe lineage."""


_BLOB_RE = re.compile(r"^[0-9a-f]{40}$")
_PATH_RE = re.compile(r"^(?!/|~|[A-Za-z]:)(?!.*(?:^|/)\.{1,2}(?:/|$))(?!.*//)[^\x00-\x1f\x7f\\]{1,1024}$")
_NULL_BLOB = "0" * 40


@dataclass(frozen=True, slots=True)
class FileSourcePolicy:
    """Non-content policy labels supplied by the canonical source owner."""

    generated: bool = False
    vendored: bool = False
    import_suspected: bool = False

    def __post_init__(self) -> None:
        if not all(isinstance(value, bool) for value in (self.generated, self.vendored, self.import_suspected)):
            raise CodeFileLineageError("file source policy flags must be boolean")


@dataclass(frozen=True, slots=True)
class SourceOccurrenceInput:
    """Typed, unkeyed USI evidence for one Git after-image.

    The caller cannot choose a `(commit, path)` association.  The association
    is derived from the bounded Git history and then checked against both the
    source version revision and the typed code-range locator.
    """

    source_version: SourceVersionRecord
    evidence: EvidenceRef

    def __post_init__(self) -> None:
        if not isinstance(self.source_version, SourceVersionRecord):
            raise CodeFileLineageError("source occurrence requires a SourceVersionRecord")
        if not isinstance(self.evidence, EvidenceRef):
            raise CodeFileLineageError("source occurrence requires an EvidenceRef")
        if self.evidence.record_kind not in {RecordKind.CHUNK, RecordKind.ENTITY} or not isinstance(self.evidence.locator, CodeRangeLocator):
            raise CodeFileLineageError("source occurrence evidence must be chunk/entity CodeRange evidence")
        if (self.evidence.source_id, self.evidence.source_version_id) != (self.source_version.source_id, self.source_version.source_version_id):
            raise CodeFileLineageError("source occurrence evidence must match its source version")


@dataclass(frozen=True, slots=True)
class FileLineageResult:
    """Canonical bundle plus the deterministic current-file projection."""

    bundle: LineageBundle
    current_occurrences: tuple[CodeOccurrenceRef, ...]
    lineage_ids_by_path: tuple[tuple[str, str], ...]

    def lineage_id_for_path(self, relative_path: str) -> str:
        for path, lineage_id in self.lineage_ids_by_path:
            if path == relative_path:
                return lineage_id
        raise KeyError(relative_path)


@dataclass(frozen=True, slots=True)
class _TrackedFile:
    occurrence: CodeOccurrenceRef
    blob_id: str
    lineage_id: str


def build_file_lineage(
    history: GitHistorySlice,
    *,
    occurrence_inputs: Iterable[SourceOccurrenceInput],
    source_policies: Mapping[str, FileSourcePolicy] | None = None,
) -> FileLineageResult:
    """Build a deterministic lineage bundle from supplied synthetic/history facts.

    One typed ``SourceOccurrenceInput`` is required for every after-image in
    the supplied change sets. Commit/path/blob facts remain exclusively in the
    ``GitHistorySlice``; input evidence is associated only after exact revision
    and locator checks. No path or blob similarity is inferred here.
    """

    if not isinstance(history, GitHistorySlice):
        raise CodeFileLineageError("history must be a GitHistorySlice")
    if history.missing_ref:
        raise CodeFileLineageError("history with a missing ref cannot support file lineage")
    policies = dict(source_policies or {})
    if not all(isinstance(path, str) and isinstance(policy, FileSourcePolicy) for path, policy in policies.items()):
        raise CodeFileLineageError("source_policies must map paths to FileSourcePolicy")
    commit_ids = tuple(commit.commit_id for commit in history.commits)
    commit_refs = tuple(commit.commit_ref for commit in history.commits)
    if len(set(commit_ids)) != len(commit_ids) or len(set(commit_refs)) != len(commit_refs):
        raise CodeFileLineageError("history contains duplicate commit evidence")
    commits = {commit.commit_id: commit for commit in history.commits}
    if history.head_commit_id not in commits:
        raise CodeFileLineageError("history head must be present in commits")
    change_sets = {change_set.commit_id: change_set for change_set in history.change_sets}
    if len(change_sets) != len(history.change_sets) or set(change_sets) != set(commits):
        raise CodeFileLineageError("history commits and change sets must be exact and unique")
    if any(commit.repo_id != history.repo_id for commit in commits.values()):
        raise CodeFileLineageError("history commit repo does not match history repo")
    _validate_history_boundaries(history, commits)
    inputs_by_slot = _index_occurrence_inputs(history, occurrence_inputs)
    for commit_id, change_set in change_sets.items():
        commit = commits[commit_id]
        if (
            change_set.commit_id != commit.commit_id
            or change_set.commit_ref != commit.commit_ref
            or tuple(change_set.parent_ids) != commit.parent_ids
        ):
            raise CodeFileLineageError("change set provenance does not match commit evidence")

    ordered = _topological_commits(commits)
    occurrences: list[CodeOccurrenceRef] = []
    events: list[FileEvent] = []
    uncertainties: list[UncertaintyRecord] = []
    links: list[LineageLink] = []
    blobs: list[GitBlobEvidenceRef] = []
    bindings: list[SourceVersionEvidenceBinding] = []
    snapshots: dict[str, dict[str, _TrackedFile]] = {}
    tombstone_snapshots: dict[str, dict[str, _TrackedFile]] = {}

    def observe(commit: CommitEvidenceRef, path: str, blob_id: str, *, first_observed_at: str) -> CodeOccurrenceRef:
        source_input = inputs_by_slot.pop((commit.commit_id, path), None)
        if source_input is None:
            raise CodeFileLineageError("every after-image requires exact typed USI occurrence evidence")
        evidence = source_input.evidence
        occurrence = CodeOccurrenceRef(
            evidence=evidence,
            repo_id=history.repo_id,
            commit_ref=commit.commit_ref,
            relative_path=path,
            first_seen_at=commit.indexed_at,
            history_first_observed_at=first_observed_at,
        )
        occurrences.append(occurrence)
        blob = GitBlobEvidenceRef(history.repo_id, commit.commit_ref, commit.commit_id, path, blob_id)
        blobs.append(blob)
        bindings.append(
            SourceVersionEvidenceBinding(
                history.repo_id,
                commit.commit_ref,
                commit.commit_id,
                path,
                blob_id,
                blob.git_blob_ref,
                occurrence.occurrence_ref,
                source_input.source_version.source_id,
                source_input.source_version.source_version_id,
                source_input.source_version,
                evidence,
            )
        )
        return occurrence

    def uncertain(
        reason: UncertaintyReason,
        occurrence: CodeOccurrenceRef,
        commit: CommitEvidenceRef,
        detail: str,
        *,
        blocks_absolute_creation_claim: bool = True,
    ) -> UncertaintyRecord:
        record = UncertaintyRecord(
            reason=reason,
            occurrence_refs=(occurrence.occurrence_ref,),
            commit_refs=(commit.commit_ref,),
            detail_code=detail,
            blocks_absolute_creation_claim=blocks_absolute_creation_claim,
        )
        uncertainties.append(record)
        return record

    for commit in ordered:
        base, ambiguous_parent = _base_snapshot(commit, snapshots)
        current = dict(base)
        inherited_tombstones, _ = _base_snapshot(commit, tombstone_snapshots)
        tombstones = dict(inherited_tombstones)
        changes = change_sets[commit.commit_id].changes
        _validate_change_set(changes, active_before=current)
        for change in changes:
            _apply_change(
                change,
                commit=commit,
                current=current,
                tombstones=tombstones,
                observe=observe,
                uncertain=uncertain,
                events=events,
                links=links,
                inherited_parent_uncertainty=ambiguous_parent or _partial_commit(commit, history),
            )
        snapshots[commit.commit_id] = current
        tombstone_snapshots[commit.commit_id] = tombstones
    if inputs_by_slot:
        raise CodeFileLineageError("source occurrence inputs include unused after-image evidence")

    current = snapshots[history.head_commit_id]
    commits_by_ref = {commit.commit_ref: commit for commit in commits.values()}
    for occurrence in occurrences:
        commit = commits_by_ref[occurrence.commit_ref]
        if _partial_commit(commit, history):
            uncertain(
                UncertaintyReason.SHALLOW_HISTORY if history.shallow_repository or commit.shallow_boundary else UncertaintyReason.MISSING_OBJECTS,
                occurrence,
                commit,
                "history_partial_boundary",
            )
    for path, tracked in sorted(current.items()):
        policy = policies.get(path, FileSourcePolicy())
        commit = commits_by_ref[tracked.occurrence.commit_ref]
        if policy.generated:
            uncertain(UncertaintyReason.GENERATED_CODE, tracked.occurrence, commit, "source_policy_generated")
        if policy.vendored:
            uncertain(UncertaintyReason.VENDORED_CODE, tracked.occurrence, commit, "source_policy_vendored")
        if policy.import_suspected:
            uncertain(UncertaintyReason.IMPORTED_OLD_CODE, tracked.occurrence, commit, "source_policy_import_suspected")

    bundle = LineageBundle(
        history.repo_id,
        tuple(commits.values()),
        tuple(occurrences),
        tuple(events),
        tuple(uncertainties),
        tuple(links),
        tuple(blobs),
        tuple(bindings),
    )
    current_occurrences = tuple(sorted((item.occurrence for item in current.values()), key=lambda item: item.relative_path))
    lineage_ids = tuple(sorted((path, item.lineage_id) for path, item in current.items()))
    return FileLineageResult(bundle, current_occurrences, lineage_ids)


def _index_occurrence_inputs(
    history: GitHistorySlice,
    occurrence_inputs: Iterable[SourceOccurrenceInput],
) -> dict[tuple[str, str], SourceOccurrenceInput]:
    """Derive, then validate, the only permitted input association.

    The caller provides a sequence of typed records, never a user-keyed map.
    We use only the history after-images as slots and require a bijection.
    """

    if isinstance(occurrence_inputs, Mapping):
        raise CodeFileLineageError("occurrence_inputs must be an unkeyed iterable of SourceOccurrenceInput")
    try:
        supplied = tuple(occurrence_inputs)
    except TypeError as exc:
        raise CodeFileLineageError("occurrence_inputs must be an iterable of SourceOccurrenceInput") from exc
    if not all(isinstance(item, SourceOccurrenceInput) for item in supplied):
        raise CodeFileLineageError("occurrence_inputs must contain only SourceOccurrenceInput")
    expected: dict[tuple[str, str], str] = {}
    for change_set in history.change_sets:
        for change in change_set.changes:
            if change.new_path:
                slot = (change_set.commit_id, change.new_path)
                if slot in expected:
                    raise CodeFileLineageError("Git history contains duplicate target after-image slots")
                expected[slot] = change.new_blob_id
    indexed: dict[tuple[str, str], SourceOccurrenceInput] = {}
    for item in supplied:
        revision = item.source_version.revision_ref
        if not revision.startswith("git:"):
            raise CodeFileLineageError("source occurrence revision must be an exact git commit reference")
        commit_id = revision.removeprefix("git:")
        path = item.evidence.locator.path
        expected_source_id = make_source_id(
            item.source_version.owner_scope,
            SourceKind.CODE,
            f"repo:{history.repo_id}/{path}",
        )
        if item.source_version.source_id != expected_source_id:
            raise CodeFileLineageError("source occurrence has a foreign canonical repository source identity")
        slot = (commit_id, path)
        if slot not in expected:
            raise CodeFileLineageError("source occurrence input does not match a Git after-image")
        if slot in indexed:
            raise CodeFileLineageError("source occurrence inputs duplicate a Git after-image")
        indexed[slot] = item
    if set(indexed) != set(expected):
        raise CodeFileLineageError("every Git after-image requires one source occurrence input")
    return indexed


def _apply_change(
    change: GitPathChange,
    *,
    commit: CommitEvidenceRef,
    current: dict[str, _TrackedFile],
    tombstones: dict[str, _TrackedFile],
    observe,
    uncertain,
    events: list[FileEvent],
    links: list[LineageLink],
    inherited_parent_uncertainty: bool,
) -> None:
    kind = change.event_kind
    old = current.get(change.old_path)
    first_observed = old.occurrence.history_first_observed_at if old else commit.committed_at

    if kind is FileEventKind.DELETED:
        if old is None:
            raise CodeFileLineageError("delete lacks an exact active predecessor")
        event = FileEvent(FileEventKind.DELETED, commit.commit_ref, (old.occurrence.occurrence_ref,), ())
        events.append(event)
        links.append(_accepted_link(LineageLinkKind.DELETED, old, None, commit, event, LineageMethod.SAME_BLOB_SAME_PATH))
        current.pop(change.old_path)
        tombstones[change.old_path] = old
        return

    if kind is FileEventKind.COPIED:
        if old is None or not change.new_path:
            raise CodeFileLineageError("copy lacks an exact active predecessor or target")
        if change.new_path in current:
            raise CodeFileLineageError("copy target already has active lineage")
        after = observe(commit, change.new_path, change.new_blob_id, first_observed_at=commit.committed_at)
        target = _TrackedFile(after, change.new_blob_id, _new_lineage_id(after))
        event = FileEvent(FileEventKind.COPIED, commit.commit_ref, (old.occurrence.occurrence_ref,), (after.occurrence_ref,))
        events.append(event)
        uncertainty = uncertain(UncertaintyReason.COPY_OR_RENAME, after, commit, "git_copy_not_identity")
        links.append(
            LineageLink(
                LineageLinkKind.COPIED,
                LinkStatus.CANDIDATE,
                (old.occurrence.occurrence_ref,),
                (after.occurrence_ref,),
                LineageMethod.COPY_CANDIDATE,
                _candidate_confidence(change.score),
                (commit.commit_ref,),
                (event.event_ref,),
                (uncertainty.uncertainty_ref,),
                commit.committed_at,
            )
        )
        current[change.new_path] = target
        return

    if kind in {FileEventKind.RENAMED, FileEventKind.MOVED}:
        if old is None or not change.new_path:
            raise CodeFileLineageError("rename/move lacks an exact active predecessor or target")
        if change.new_path in current and change.new_path != change.old_path:
            raise CodeFileLineageError("rename/move target already has active lineage")
        after = observe(commit, change.new_path, change.new_blob_id, first_observed_at=first_observed)
        event_kind = _rename_kind(change.old_path, change.new_path)
        event = FileEvent(event_kind, commit.commit_ref, (old.occurrence.occurrence_ref,), (after.occurrence_ref,))
        events.append(event)
        candidate_reason = (
            UncertaintyReason.PATH_CASE_AMBIGUITY
            if change.old_path.lower() == change.new_path.lower() and change.old_path != change.new_path
            else UncertaintyReason.COPY_OR_RENAME
        )
        is_exact = (
            change.score == 100
            and change.old_blob_id == change.new_blob_id
            and not inherited_parent_uncertainty
            and candidate_reason is not UncertaintyReason.PATH_CASE_AMBIGUITY
        )
        target = _TrackedFile(after, change.new_blob_id, old.lineage_id if is_exact else _new_lineage_id(after))
        if is_exact:
            links.append(_accepted_link(_link_kind(event_kind), old, target, commit, event, LineageMethod.SAME_BLOB_RENAMED_PATH))
        else:
            uncertainty = uncertain(candidate_reason, after, commit, "case_only_rename" if candidate_reason is UncertaintyReason.PATH_CASE_AMBIGUITY else "rename_score_or_history_partial")
            links.append(
                LineageLink(
                    _link_kind(event_kind),
                    LinkStatus.CANDIDATE,
                    (old.occurrence.occurrence_ref,),
                    (after.occurrence_ref,),
                    LineageMethod.GIT_RENAME_DETECTION,
                    _candidate_confidence(change.score),
                    (commit.commit_ref,),
                    (event.event_ref,),
                    (uncertainty.uncertainty_ref,),
                    commit.committed_at,
                )
            )
        current.pop(change.old_path)
        current[change.new_path] = target
        return

    if kind is FileEventKind.ADDED:
        if not change.new_path or change.new_path in current:
            raise CodeFileLineageError("add must create one previously inactive path")
        after = observe(commit, change.new_path, change.new_blob_id, first_observed_at=commit.committed_at)
        resurrected = tombstones.get(change.new_path)
        if resurrected is not None and resurrected.blob_id == change.new_blob_id:
            event = FileEvent(FileEventKind.RESURRECTED, commit.commit_ref, (resurrected.occurrence.occurrence_ref,), (after.occurrence_ref,))
            target = _TrackedFile(after, change.new_blob_id, resurrected.lineage_id)
            links.append(_accepted_link(LineageLinkKind.RESURRECTED, resurrected, target, commit, event, LineageMethod.SAME_BLOB_SAME_PATH))
        else:
            event = FileEvent(FileEventKind.ADDED, commit.commit_ref, (), (after.occurrence_ref,))
            target = _TrackedFile(after, change.new_blob_id, _new_lineage_id(after))
        events.append(event)
        current[change.new_path] = target
        return

    if kind is FileEventKind.MODIFIED:
        if old is None or not change.new_path:
            raise CodeFileLineageError("modification lacks an exact active predecessor")
        after = observe(commit, change.new_path, change.new_blob_id, first_observed_at=first_observed)
        event = FileEvent(FileEventKind.MODIFIED, commit.commit_ref, (old.occurrence.occurrence_ref,), (after.occurrence_ref,))
        events.append(event)
        target = _TrackedFile(after, change.new_blob_id, old.lineage_id)
        if old.blob_id == change.new_blob_id:
            links.append(_accepted_link(LineageLinkKind.CONTINUED, old, target, commit, event, LineageMethod.SAME_BLOB_SAME_PATH))
        current[change.new_path] = target
        return
    raise CodeFileLineageError("unsupported file event kind")


def _accepted_link(kind: LineageLinkKind, old: _TrackedFile, new: _TrackedFile | None, commit: CommitEvidenceRef, event: FileEvent, method: LineageMethod) -> LineageLink:
    return LineageLink(
        kind,
        LinkStatus.ACCEPTED,
        (old.occurrence.occurrence_ref,),
        () if new is None else (new.occurrence.occurrence_ref,),
        method,
        1.0,
        (commit.commit_ref,),
        (event.event_ref,),
        (),
        commit.committed_at,
    )


def _topological_commits(commits: Mapping[str, CommitEvidenceRef]) -> tuple[CommitEvidenceRef, ...]:
    remaining = dict(commits)
    ordered: list[CommitEvidenceRef] = []
    while remaining:
        ready = [commit for commit in remaining.values() if all(parent not in remaining for parent in commit.parent_ids)]
        if not ready:
            raise CodeFileLineageError("history parent graph contains a cycle")
        for commit in sorted(ready, key=lambda item: item.commit_id):
            ordered.append(commit)
            remaining.pop(commit.commit_id)
    return tuple(ordered)


def _base_snapshot(commit: CommitEvidenceRef, snapshots: Mapping[str, dict[str, _TrackedFile]]) -> tuple[dict[str, _TrackedFile], bool]:
    parents = [snapshots[parent] for parent in commit.parent_ids if parent in snapshots]
    if not parents:
        return {}, bool(commit.parent_ids)
    if len(parents) == 1:
        return dict(parents[0]), False
    common_paths = set.intersection(*(set(parent) for parent in parents))
    base = {
        path: parents[0][path]
        for path in common_paths
        if all(parent[path].lineage_id == parents[0][path].lineage_id for parent in parents[1:])
    }
    return base, any(parent != base for parent in parents)


def _validate_change_set(changes: tuple[GitPathChange, ...], *, active_before: Mapping[str, _TrackedFile]) -> None:
    if not isinstance(changes, tuple) or not all(isinstance(change, GitPathChange) for change in changes):
        raise CodeFileLineageError("change set changes must be typed and immutable")
    targets = [change.new_path for change in changes if change.new_path]
    if len(targets) != len(set(targets)):
        raise CodeFileLineageError("change set has ambiguous duplicate target paths")
    sources = [change.old_path for change in changes if change.old_path]
    if len(sources) != len(set(sources)):
        raise CodeFileLineageError("change set has ambiguous duplicate source paths")
    for change in changes:
        _validate_change_shape(change)
        if change.event_kind is not FileEventKind.ADDED:
            predecessor = active_before.get(change.old_path)
            if predecessor is None:
                raise CodeFileLineageError("change source lacks an exact pre-commit lineage")
            if change.old_blob_id != predecessor.blob_id:
                raise CodeFileLineageError("change old blob does not match active predecessor evidence")
        if change.event_kind is FileEventKind.MODIFIED:
            continue
        if change.new_path and change.new_path in active_before:
            raise CodeFileLineageError("change target already has pre-commit lineage")


def _validate_change_shape(change: GitPathChange) -> None:
    kind = change.event_kind
    if not isinstance(kind, FileEventKind):
        raise CodeFileLineageError("change event kind must be canonical")
    old_path = _change_path(change.old_path, allow_empty=True)
    new_path = _change_path(change.new_path, allow_empty=True)
    old_blob = _change_blob(change.old_blob_id)
    new_blob = _change_blob(change.new_blob_id)
    score = change.score
    if score is not None and (isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100):
        raise CodeFileLineageError("change score must be an integer between 0 and 100")

    if kind is FileEventKind.ADDED:
        valid = not old_path and bool(new_path) and old_blob == _NULL_BLOB and new_blob != _NULL_BLOB and score is None
    elif kind is FileEventKind.DELETED:
        valid = bool(old_path) and not new_path and old_blob != _NULL_BLOB and new_blob == _NULL_BLOB and score is None
    elif kind is FileEventKind.MODIFIED:
        valid = bool(old_path) and old_path == new_path and old_blob != _NULL_BLOB and new_blob != _NULL_BLOB and score is None
    elif kind is FileEventKind.COPIED:
        valid = bool(old_path) and bool(new_path) and old_path != new_path and old_blob != _NULL_BLOB and new_blob != _NULL_BLOB and score is not None
    elif kind in {FileEventKind.RENAMED, FileEventKind.MOVED}:
        valid = (
            bool(old_path)
            and bool(new_path)
            and old_path != new_path
            and old_blob != _NULL_BLOB
            and new_blob != _NULL_BLOB
            and score is not None
            and (kind is FileEventKind.RENAMED or _rename_kind(old_path, new_path) is FileEventKind.MOVED)
        )
    else:
        valid = False
    if not valid:
        raise CodeFileLineageError(f"{kind.value} change does not have its canonical path/blob/score shape")


def _change_path(value: object, *, allow_empty: bool) -> str:
    if not isinstance(value, str):
        raise CodeFileLineageError("change paths must be strings")
    if not value and allow_empty:
        return ""
    if not _PATH_RE.fullmatch(value):
        raise CodeFileLineageError("change path must be relative and normalized")
    return value


def _change_blob(value: object) -> str:
    if not isinstance(value, str) or not _BLOB_RE.fullmatch(value):
        raise CodeFileLineageError("change blob IDs must be lowercase 40-character hashes")
    return value


def _validate_history_boundaries(history: GitHistorySlice, commits: Mapping[str, CommitEvidenceRef]) -> None:
    if history.history_state is HistoryState.COMPLETE and history.shallow_repository:
        raise CodeFileLineageError("complete history cannot be shallow")
    reachable: set[str] = set()
    pending = [history.head_commit_id]
    while pending:
        commit_id = pending.pop()
        if commit_id in reachable or commit_id not in commits:
            continue
        reachable.add(commit_id)
        pending.extend(parent for parent in commits[commit_id].parent_ids if parent in commits)
    if reachable != set(commits):
        raise CodeFileLineageError("all supplied commits must be reachable from history head")
    for commit in commits.values():
        absent = {parent for parent in commit.parent_ids if parent not in commits}
        recorded_missing = set(commit.missing_parent_ids)
        if absent != recorded_missing:
            raise CodeFileLineageError("unknown parents must be recorded exactly as missing evidence")
        if absent and commit.history_state is HistoryState.COMPLETE:
            raise CodeFileLineageError("complete commit cannot have unknown parents")
        if history.history_state is HistoryState.COMPLETE and commit.history_state is not HistoryState.COMPLETE:
            raise CodeFileLineageError("complete history cannot contain partial commits")
        if history.history_state is HistoryState.COMPLETE and (commit.shallow_boundary or recorded_missing):
            raise CodeFileLineageError("complete history cannot contain missing boundaries")


def _partial_commit(commit: CommitEvidenceRef, history: GitHistorySlice) -> bool:
    return history.history_state is not HistoryState.COMPLETE or commit.history_state is not HistoryState.COMPLETE or commit.shallow_boundary or bool(commit.missing_parent_ids)


def _rename_kind(old_path: str, new_path: str) -> FileEventKind:
    return FileEventKind.MOVED if PurePosixPath(old_path).name == PurePosixPath(new_path).name else FileEventKind.RENAMED


def _link_kind(event_kind: FileEventKind) -> LineageLinkKind:
    return LineageLinkKind.MOVED if event_kind is FileEventKind.MOVED else LineageLinkKind.RENAMED


def _candidate_confidence(score: int | None) -> float:
    if score is None:
        return 0.5
    return min(0.99, max(0.01, score / 100.0))


def _new_lineage_id(occurrence: CodeOccurrenceRef) -> str:
    return "clt_file_lineage_" + hashlib.sha256(occurrence.occurrence_ref.encode("ascii")).hexdigest()
