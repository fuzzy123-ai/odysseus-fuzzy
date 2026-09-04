from __future__ import annotations

from dataclasses import dataclass

from src.harbour_knowledge_store import KnowledgeHeadMetadata, KnowledgeMigrationSnapshot
from src.unified_source_index_sources.knowledge import project_harbour_knowledge_metadata


_SCHEMA_ID = "odysseus.harbour.snapshot_comparison.v1"
_SNAPSHOT_SCHEMA = "odysseus.harbour.knowledge_migration_snapshot.v1"
_MAX_HEADS = 10_000
_MAX_BYTES = 16_777_216
_HEX = frozenset("0123456789abcdef")
_STATES = frozenset(("active", "tombstoned", "purge_pending", "purged"))
_KINDS = frozenset((
    "personal_memory", "document", "note", "imported_record", "derived_abstraction", "causal_claim",
))


class HarbourSnapshotComparisonError(ValueError):
    __slots__ = ("code",)

    def __init__(self, code: object = "invalid_comparison") -> None:
        self.code = "invalid_comparison"
        super().__init__(self.code)

    def __repr__(self) -> str:
        return "HarbourSnapshotComparisonError(<invalid>)"


@dataclass(frozen=True, slots=True, repr=False)
class HarbourSnapshotDelta:
    label: str
    missing_knowledge_ids: tuple[str, ...]
    extra_knowledge_ids: tuple[str, ...]
    changed_knowledge_ids: tuple[str, ...]

    def __repr__(self) -> str:
        return "HarbourSnapshotDelta(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class HarbourSnapshotComparison:
    schema_id: str
    owner_ref_digest: str
    source_snapshot_digest: str
    target_snapshot_digest: str
    post_snapshot_digest: str
    source_to_target: HarbourSnapshotDelta
    target_to_post: HarbourSnapshotDelta

    def __repr__(self) -> str:
        return "HarbourSnapshotComparison(<redacted>)"


def _invalid() -> None:
    error = HarbourSnapshotComparisonError()
    try:
        raise error
    except HarbourSnapshotComparisonError as caught:
        BaseException.__context__.__set__(caught, None)
        BaseException.__cause__.__set__(caught, None)
        caught.__suppress_context__ = True
        raise


def _text(value: object, *, digest: bool = False) -> str:
    if type(value) is not str or len(value) > 128:
        _invalid()
    try:
        value.encode("ascii")
    except BaseException:
        _invalid()
    if digest:
        if len(value) != 71 or value[:7] != "sha256:" or any(char not in _HEX for char in value[7:]):
            _invalid()
    return value


def _count_bytes(values: tuple[str, ...], total: int) -> int:
    total += sum(len(value) for value in values)
    if total > _MAX_BYTES:
        _invalid()
    return total


def _clone_head(value: object) -> tuple[KnowledgeHeadMetadata, int]:
    if type(value) is not KnowledgeHeadMetadata:
        _invalid()
    raw = (
        value.knowledge_id_digest, value.revision_id_digest, value.revision, value.generation,
        value.state, value.knowledge_kind, value.payload_sha256, value.classification_digest,
        value.retention_digest, value.provenance_digest, value.source_revisions_digest,
        value.references_digest, value.revision_metadata_digest, value.head_metadata_digest, value.record_digest,
    )
    digests = tuple(_text(item, digest=True) for item in (raw[0], raw[1], raw[6], raw[7], raw[8], raw[9], raw[10], raw[11], raw[12], raw[13], raw[14]))
    revision, generation = raw[2], raw[3]
    if type(revision) is not int or type(generation) is not int or not 1 <= revision <= 2**63 - 1 or not revision <= generation <= 2**63 - 1:
        _invalid()
    state, kind = _text(raw[4]), _text(raw[5])
    if state not in _STATES or kind not in _KINDS:
        _invalid()
    head = KnowledgeHeadMetadata(
        digests[0], digests[1], revision, generation, state, kind, digests[2], digests[3], digests[4],
        digests[5], digests[6], digests[7], digests[8], digests[9], digests[10],
    )
    return head, _count_bytes((state, kind) + digests, 0)


def _clone_snapshot(value: object) -> KnowledgeMigrationSnapshot:
    if type(value) is not KnowledgeMigrationSnapshot:
        _invalid()
    raw = (
        value.schema_id, value.owner_ref_digest, value.captured_head_boundary, value.heads, value.snapshot_digest,
    )
    schema = _text(raw[0])
    if schema != _SNAPSHOT_SCHEMA:
        _invalid()
    owner, boundary, digest = _text(raw[1], digest=True), _text(raw[2], digest=True), _text(raw[4], digest=True)
    heads = raw[3]
    if type(heads) is not tuple or len(heads) > _MAX_HEADS:
        _invalid()
    copied: list[KnowledgeHeadMetadata] = []
    total = _count_bytes((schema, owner, boundary, digest), 0)
    previous: str | None = None
    for item in heads:
        head, size = _clone_head(item)
        total += size
        if total > _MAX_BYTES or (previous is not None and head.knowledge_id_digest <= previous):
            _invalid()
        previous = head.knowledge_id_digest
        copied.append(head)
    return KnowledgeMigrationSnapshot(schema, owner, boundary, tuple(copied), digest)


def _delta(label: str, left: tuple[KnowledgeHeadMetadata, ...], right: tuple[KnowledgeHeadMetadata, ...]) -> HarbourSnapshotDelta:
    left_records = {head.knowledge_id_digest: head.record_digest for head in left}
    right_records = {head.knowledge_id_digest: head.record_digest for head in right}
    return HarbourSnapshotDelta(
        label,
        tuple(identity for identity in left_records if identity not in right_records),
        tuple(identity for identity in right_records if identity not in left_records),
        tuple(identity for identity in left_records if identity in right_records and left_records[identity] != right_records[identity]),
    )


def compare_harbour_snapshot_fixtures(
    source: KnowledgeMigrationSnapshot,
    target: KnowledgeMigrationSnapshot,
    post: KnowledgeMigrationSnapshot,
) -> HarbourSnapshotComparison:
    try:
        source_copy = _clone_snapshot(source)
        target_copy = _clone_snapshot(target)
        post_copy = _clone_snapshot(post)
        project_harbour_knowledge_metadata(source_copy)
        project_harbour_knowledge_metadata(target_copy)
        project_harbour_knowledge_metadata(post_copy)
        if source_copy.schema_id != target_copy.schema_id or source_copy.schema_id != post_copy.schema_id:
            _invalid()
        if source_copy.owner_ref_digest != target_copy.owner_ref_digest or source_copy.owner_ref_digest != post_copy.owner_ref_digest:
            _invalid()
        return HarbourSnapshotComparison(
            _SCHEMA_ID,
            source_copy.owner_ref_digest,
            source_copy.snapshot_digest,
            target_copy.snapshot_digest,
            post_copy.snapshot_digest,
            _delta("source_to_target", source_copy.heads, target_copy.heads),
            _delta("target_to_post", target_copy.heads, post_copy.heads),
        )
    except BaseException:
        _invalid()
__all__ = [
    "HarbourSnapshotComparison",
    "HarbourSnapshotComparisonError",
    "HarbourSnapshotDelta",
    "compare_harbour_snapshot_fixtures",
]
