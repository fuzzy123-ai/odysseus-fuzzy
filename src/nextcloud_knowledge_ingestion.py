"""Inactive owner-bound Nextcloud inventory to durable Knowledge adapter.

The adapter accepts only a completed synthetic text inventory record and the
existing process-local Personal Docs reader capability.  It performs no
provider calls, ledger writes, source-filesystem writes, grants, routing, or
application activation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
from types import MappingProxyType
from typing import Any

from src.bigdata_ledger_contract import BigDataLedgerItem, BigDataLedgerRecord
from src.harbour_authority import TrustedPrincipal
from src.harbour_knowledge_store import (
    HarbourKnowledgeStore,
    HarbourKnowledgeStoreError,
    KnowledgeHead,
    KnowledgeReceipt,
    KnowledgeRevision,
)
from src.personal_docs_owner_reader import (
    PERSONAL_DOCS_TEXT_PROFILE,
    PersonalDocsExactRead,
    PersonalDocsFileDescriptor,
    PersonalDocsOwnerReader,
    PersonalDocsOwnerReaderError,
    PersonalDocsSnapshot,
    PersonalDocsWholeFileLocator,
)


PAYLOAD_SCHEMA_ID = "odysseus.personal_document.utf8.v1"
SOURCE_KIND = "personal_document"
AUTHORITY_DOMAIN = "personal_docs"
KNOWLEDGE_KIND = "document"
CLASSIFICATION = "sensitive"
RETENTION = MappingProxyType({
    "retention_class": "user_managed",
    "deletion_mode": "tombstone_only",
    "retain_until": None,
    "grace_until": None,
    "legal_hold": False,
})

_EXTENSION_MEDIA = MappingProxyType({
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
})
_DESCRIPTOR_MIME = MappingProxyType({
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".json": "application/json",
})
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_OWNER_METADATA_KEYS = frozenset(
    {"owner", "owner_id", "owner_ref", "principal", "principal_ref", "subject_id"}
)
_SOURCE_FIELDS = frozenset(
    {
        "source_revision_id",
        "source_kind",
        "source_id",
        "revision_ref",
        "source_content_sha256",
        "disposition",
        "locator_digest",
        "authority_domain",
        "authority_revision",
        "authority_digest",
        "predecessor_source_revision_id",
        "observed_at",
    }
)
_PROVENANCE_FIELDS = frozenset(
    {
        "role",
        "source_revision_id",
        "knowledge_revision_id",
        "transformation_digest",
        "admission_receipt_digest",
    }
)
_ERROR_CODES = frozenset(
    {
        "invalid_request",
        "authentication_denied",
        "inventory_denied",
        "source_denied",
        "existing_state_denied",
        "lifecycle_denied",
        "stale_state",
        "store_failed",
        "ingestion_failed",
    }
)


class NextcloudKnowledgeIngestionError(ValueError):
    """Fixed, content-free failure from the offline ingestion boundary."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        safe = code if type(code) is str and code in _ERROR_CODES else "ingestion_failed"
        self.code = safe
        super().__init__(safe)

    def __repr__(self) -> str:
        return f"NextcloudKnowledgeIngestionError({self.code!r})"


@dataclass(frozen=True, slots=True, repr=False)
class NextcloudKnowledgeIngestionResult:
    outcome: str
    knowledge_id: str
    source_revision_id: str
    head: KnowledgeHead = field(repr=False)
    receipt: KnowledgeReceipt | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.outcome) is not str
            or self.outcome not in {"created", "appended", "unchanged"}
            or type(self.knowledge_id) is not str
            or _IDENTIFIER.fullmatch(self.knowledge_id) is None
            or type(self.source_revision_id) is not str
            or _IDENTIFIER.fullmatch(self.source_revision_id) is None
            or type(self.head) is not KnowledgeHead
            or (self.receipt is not None and type(self.receipt) is not KnowledgeReceipt)
            or (self.outcome == "unchanged") != (self.receipt is None)
        ):
            raise NextcloudKnowledgeIngestionError("ingestion_failed") from None

    def __repr__(self) -> str:
        return f"NextcloudKnowledgeIngestionResult(outcome={self.outcome!r}, <content-free>)"


@dataclass(frozen=True, slots=True, repr=False)
class _DetachedDescriptorAuthority:
    relative_path: str
    locator_source_ref: str
    locator_revision_ref: str
    locator_start_char: int
    locator_end_char: int
    locator_digest: str
    mime_type: str
    extractor_profile: str
    byte_size: int
    char_size: int
    source_ref: str
    revision_ref: str
    extracted_content_hash: str
    binding_digest: str
    descriptor_digest: str


@dataclass(frozen=True, slots=True, repr=False)
class _DetachedSnapshotAuthority:
    descriptors: tuple[_DetachedDescriptorAuthority, ...]
    binding_digest: str
    snapshot_digest: str
    owner_ref: str
    unsupported_count: int
    unsupported_digest: str
    selected_index: int


def _fail(code: str) -> None:
    raise NextcloudKnowledgeIngestionError(code) from None


def _descriptor_authority_fields(
    descriptor: object,
) -> _DetachedDescriptorAuthority:
    if (
        type(descriptor) is not PersonalDocsFileDescriptor
        or type(descriptor.locator) is not PersonalDocsWholeFileLocator
    ):
        _fail("source_denied")
    locator = descriptor.locator
    return _DetachedDescriptorAuthority(
        descriptor.relative_path,
        locator.source_ref,
        locator.revision_ref,
        locator.start_char,
        locator.end_char,
        locator.locator_digest,
        descriptor.mime_type,
        descriptor.extractor_profile,
        descriptor.byte_size,
        descriptor.char_size,
        descriptor.source_ref,
        descriptor.revision_ref,
        descriptor.extracted_content_hash,
        descriptor.binding_digest,
        descriptor.descriptor_digest,
    )


def _rebuild_descriptor_authority(
    fields: _DetachedDescriptorAuthority,
) -> PersonalDocsFileDescriptor:
    locator = PersonalDocsWholeFileLocator(
        source_ref=fields.locator_source_ref,
        revision_ref=fields.locator_revision_ref,
        start_char=fields.locator_start_char,
        end_char=fields.locator_end_char,
        locator_digest=fields.locator_digest,
    )
    return PersonalDocsFileDescriptor(
        relative_path=fields.relative_path,
        locator=locator,
        mime_type=fields.mime_type,
        extractor_profile=fields.extractor_profile,
        byte_size=fields.byte_size,
        char_size=fields.char_size,
        source_ref=fields.source_ref,
        revision_ref=fields.revision_ref,
        extracted_content_hash=fields.extracted_content_hash,
        binding_digest=fields.binding_digest,
        descriptor_digest=fields.descriptor_digest,
    )


def _detach_authority(
    snapshot: object,
    selected_descriptor: object,
) -> _DetachedSnapshotAuthority:
    try:
        if type(snapshot) is not PersonalDocsSnapshot or type(snapshot.descriptors) is not tuple:
            _fail("source_denied")
        descriptors = snapshot.descriptors
        selected = tuple(
            index for index, descriptor in enumerate(descriptors)
            if descriptor is selected_descriptor
        )
        if len(selected) != 1:
            _fail("source_denied")
        detached_descriptors = tuple(
            _descriptor_authority_fields(descriptor) for descriptor in descriptors
        )
        detached = _DetachedSnapshotAuthority(
            detached_descriptors,
            snapshot.binding_digest,
            snapshot.snapshot_digest,
            snapshot.owner_ref,
            snapshot.unsupported_count,
            snapshot.unsupported_digest,
            selected[0],
        )
        rebuilt_descriptors = tuple(
            _rebuild_descriptor_authority(fields) for fields in detached.descriptors
        )
        rebuilt = PersonalDocsSnapshot(
            descriptors=rebuilt_descriptors,
            binding_digest=detached.binding_digest,
            snapshot_digest=detached.snapshot_digest,
            owner_ref=detached.owner_ref,
            unsupported_count=detached.unsupported_count,
            unsupported_digest=detached.unsupported_digest,
        )
        if (
            tuple(_descriptor_authority_fields(item) for item in rebuilt.descriptors)
            != detached.descriptors
            or rebuilt.binding_digest != detached.binding_digest
            or rebuilt.snapshot_digest != detached.snapshot_digest
            or rebuilt.owner_ref != detached.owner_ref
            or rebuilt.unsupported_count != detached.unsupported_count
            or rebuilt.unsupported_digest != detached.unsupported_digest
        ):
            _fail("source_denied")
        return detached
    except NextcloudKnowledgeIngestionError:
        raise
    except BaseException:
        _fail("source_denied")


def _require_unchanged_authority(
    snapshot: PersonalDocsSnapshot,
    descriptor: PersonalDocsFileDescriptor,
    expected: _DetachedSnapshotAuthority,
) -> None:
    if _detach_authority(snapshot, descriptor) != expected:
        _fail("source_denied")


def _frame(*values: bytes) -> bytes:
    return b"".join(len(value).to_bytes(8, "big") + value for value in values)


def _digest(domain: str, *values: bytes) -> str:
    return "sha256:" + hashlib.sha256(
        _frame(domain.encode("ascii"), *values)
    ).hexdigest()


def _opaque_id(prefix: str, domain: str, *values: str) -> str:
    return prefix + hashlib.sha256(
        _frame(domain.encode("ascii"), *(value.encode("utf-8") for value in values))
    ).hexdigest()


def canonical_personal_docs_owner_ref(owner_id: str) -> str:
    """Return the published Personal Docs v1 owner-reference framing."""

    if type(owner_id) is not str or not owner_id:
        _fail("invalid_request")
    try:
        encoded = owner_id.encode("utf-8", "strict")
    except UnicodeError:
        _fail("invalid_request")
    if len(encoded) > 256 or any(ord(character) < 32 or ord(character) == 127 for character in owner_id):
        _fail("invalid_request")
    return _digest("personal_docs.owner.v1", encoded)


def _metadata_copy(value: object, *, depth: int = 0) -> object:
    if depth > 8:
        _fail("inventory_denied")
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is list:
        if len(value) > 64:
            _fail("inventory_denied")
        return (
            "list",
            tuple(_metadata_copy(item, depth=depth + 1) for item in value),
        )
    if type(value) is dict:
        if len(value) > 64:
            _fail("inventory_denied")
        result: list[tuple[str, object]] = []
        for key, child in value.items():
            if (
                type(key) is not str
                or not key
                or key.casefold() in _OWNER_METADATA_KEYS
            ):
                _fail("inventory_denied")
            result.append((key, _metadata_copy(child, depth=depth + 1)))
        result.sort(key=lambda pair: pair[0].encode("utf-8", "strict"))
        return ("dict", tuple(result))
    _fail("inventory_denied")


def _record_snapshot(record: BigDataLedgerRecord) -> tuple[object, ...]:
    if (
        type(record) is not BigDataLedgerRecord
        or type(record.item) is not BigDataLedgerItem
        or type(record.metadata) is not dict
    ):
        _fail("inventory_denied")
    item = record.item
    return (
        item.provider,
        item.source_id,
        item.relative_path,
        item.size_bytes,
        item.mtime,
        item.content_hash,
        item.etag,
        item.item_id,
        record.stage,
        record.status,
        record.updated_at,
        _metadata_copy(record.metadata),
    )


def _metadata_value(snapshot: object, key: str) -> object:
    if (
        type(snapshot) is not tuple
        or len(snapshot) != 2
        or snapshot[0] != "dict"
        or type(snapshot[1]) is not tuple
    ):
        _fail("inventory_denied")
    for pair in snapshot[1]:
        if type(pair) is not tuple or len(pair) != 2:
            _fail("inventory_denied")
        candidate, value = pair
        if candidate == key:
            return value
    _fail("inventory_denied")


def _validate_inventory(
    record: BigDataLedgerRecord,
    record_snapshot: tuple[object, ...],
    descriptor: PersonalDocsFileDescriptor,
) -> tuple[str, str]:
    if (
        record_snapshot[:2] != ("nextcloud", record.item.source_id)
        or record_snapshot[2] != descriptor.relative_path
        or type(record_snapshot[3]) is not int
        or record_snapshot[3] != descriptor.byte_size
        or record_snapshot[8:10] != ("inventory", "completed")
        or type(record_snapshot[10]) is not str
    ):
        _fail("inventory_denied")
    metadata = record_snapshot[11]
    extension = _metadata_value(metadata, "extension")
    category = _metadata_value(metadata, "file_category")
    privacy = _metadata_value(metadata, "privacy")
    candidate = _metadata_value(privacy, "memory_write_candidate")
    path_suffix = (
        "." + descriptor.relative_path.rsplit(".", 1)[1].lower()
        if "." in descriptor.relative_path.rsplit("/", 1)[-1]
        else ""
    )
    if (
        type(extension) is not str
        or extension not in _EXTENSION_MEDIA
        or extension != path_suffix
        or category != "text_extractable"
        or type(candidate) is not bool
        or candidate is not True
        or descriptor.mime_type != _DESCRIPTOR_MIME[extension]
        or descriptor.extractor_profile != PERSONAL_DOCS_TEXT_PROFILE
    ):
        _fail("inventory_denied")
    return extension, record_snapshot[10]


def _descriptor_for_record(
    snapshot: PersonalDocsSnapshot,
    record: BigDataLedgerRecord,
) -> PersonalDocsFileDescriptor:
    if type(snapshot) is not PersonalDocsSnapshot or type(snapshot.descriptors) is not tuple:
        _fail("source_denied")
    matches = tuple(
        descriptor
        for descriptor in snapshot.descriptors
        if type(descriptor) is PersonalDocsFileDescriptor
        and descriptor.relative_path == record.item.relative_path
    )
    if len(matches) != 1:
        _fail("inventory_denied")
    return matches[0]


def _knowledge_id(owner_id: str, source_ref: str) -> str:
    return _opaque_id("nki-", "nextcloud_knowledge.knowledge.v1", owner_id, source_ref)


def _source_revision_id(revision_ref: str, payload_digest: str) -> str:
    return _opaque_id(
        "nksr-", "nextcloud_knowledge.source_revision.v1", revision_ref, payload_digest
    )


def _idempotency_key(
    operation: str,
    owner_id: str,
    knowledge_id: str,
    source_revision_id: str,
) -> str:
    return _opaque_id(
        f"nki-{operation}-",
        f"nextcloud_knowledge.{operation}_idempotency.v1",
        owner_id,
        knowledge_id,
        source_revision_id,
    )


def _source_vector(
    descriptor: _DetachedDescriptorAuthority,
    *,
    snapshot_digest: str,
    payload_digest: str,
    predecessor: str | None,
    observed_at: str,
) -> tuple[dict[str, Any], ...]:
    source_revision_id = _source_revision_id(descriptor.revision_ref, payload_digest)
    return ({
        "source_revision_id": source_revision_id,
        "source_kind": SOURCE_KIND,
        "source_id": descriptor.source_ref,
        "revision_ref": descriptor.revision_ref,
        "source_content_sha256": payload_digest,
        "disposition": "imported",
        "locator_digest": descriptor.locator_digest,
        "authority_domain": AUTHORITY_DOMAIN,
        "authority_revision": snapshot_digest,
        "authority_digest": descriptor.descriptor_digest,
        "predecessor_source_revision_id": predecessor,
        "observed_at": observed_at,
    },)


def _provenance(source_revision_id: str, snapshot_digest: str) -> tuple[dict[str, Any], ...]:
    return ({
        "role": "origin",
        "source_revision_id": source_revision_id,
        "knowledge_revision_id": None,
        "transformation_digest": None,
        "admission_receipt_digest": snapshot_digest,
    },)


def _copy_head(value: object, *, owner_id: str, knowledge_id: str) -> KnowledgeHead:
    if type(value) is not KnowledgeHead:
        _fail("store_failed")
    fields = (
        value.owner_id,
        value.knowledge_id,
        value.knowledge_kind,
        value.current_revision_id,
        value.current_payload_sha256,
        value.generation,
        value.state,
        value.tombstoned_at,
        value.purge_requested_at,
    )
    if (
        fields[0] != owner_id
        or fields[1] != knowledge_id
        or fields[2] != KNOWLEDGE_KIND
        or type(fields[3]) is not str
        or _IDENTIFIER.fullmatch(fields[3]) is None
        or type(fields[4]) is not str
        or _DIGEST.fullmatch(fields[4]) is None
        or type(fields[5]) is not int
        or fields[5] < 1
        or fields[6] not in {"active", "tombstoned", "purge_pending", "purged"}
        or (fields[7] is not None and type(fields[7]) is not str)
        or (fields[8] is not None and type(fields[8]) is not str)
    ):
        _fail("existing_state_denied")
    return KnowledgeHead(*fields)


def _copy_receipt(
    value: object,
    *,
    operation: str,
    owner_id: str,
    knowledge_id: str,
    before_head: KnowledgeHead | None,
    payload_digest: str,
) -> KnowledgeReceipt:
    if type(value) is not KnowledgeReceipt:
        _fail("store_failed")
    after = _copy_head(value.after_head, owner_id=owner_id, knowledge_id=knowledge_id)
    before = None if value.before_head is None else _copy_head(
        value.before_head, owner_id=owner_id, knowledge_id=knowledge_id
    )
    if (
        value.command_kind != operation
        or value.owner_id != owner_id
        or value.knowledge_id != knowledge_id
        or before != before_head
        or after.state != "active"
        or after.current_payload_sha256 != payload_digest
        or after.current_revision_id != value.new_revision_id
        or after.generation != (1 if before_head is None else before_head.generation + 1)
        or type(value.receipt_id) is not str
        or _IDENTIFIER.fullmatch(value.receipt_id) is None
        or type(value.input_digest) is not str
        or _DIGEST.fullmatch(value.input_digest) is None
        or type(value.output_digest) is not str
        or _DIGEST.fullmatch(value.output_digest) is None
        or type(value.new_revision_id) is not str
        or _IDENTIFIER.fullmatch(value.new_revision_id) is None
        or type(value.occurred_at) is not str
        or type(value.producer) is not str
    ):
        _fail("store_failed")
    return KnowledgeReceipt(
        value.receipt_id,
        value.command_kind,
        value.owner_id,
        value.knowledge_id,
        value.input_digest,
        value.output_digest,
        before,
        after,
        value.new_revision_id,
        value.occurred_at,
        value.producer,
    )


def _current_source(
    revision: object,
    *,
    owner_id: str,
    knowledge_id: str,
    descriptor: _DetachedDescriptorAuthority,
    head: KnowledgeHead,
) -> dict[str, Any]:
    if (
        type(revision) is not KnowledgeRevision
        or revision.owner_id != owner_id
        or revision.knowledge_id != knowledge_id
        or revision.knowledge_kind != KNOWLEDGE_KIND
        or revision.revision_id != head.current_revision_id
        or revision.payload_sha256 != head.current_payload_sha256
        or revision.payload_schema_id != PAYLOAD_SCHEMA_ID
        or revision.classification != CLASSIFICATION
        or type(revision.source_revisions) is not tuple
        or len(revision.source_revisions) != 1
        or type(revision.provenance) is not tuple
        or len(revision.provenance) != 1
        or type(revision.references) is not tuple
        or len(revision.references) != 0
        or type(revision.retention) is not dict
        or revision.retention != RETENTION
    ):
        _fail("existing_state_denied")
    source = revision.source_revisions[0]
    provenance = revision.provenance[0]
    if (
        type(source) is not dict
        or frozenset(source) != _SOURCE_FIELDS
        or source["source_kind"] != SOURCE_KIND
        or source["source_id"] != descriptor.source_ref
        or source["authority_domain"] != AUTHORITY_DOMAIN
        or source["disposition"] != "imported"
        or type(source["source_revision_id"]) is not str
        or type(source["revision_ref"]) is not str
        or _DIGEST.fullmatch(source["revision_ref"]) is None
        or type(source["source_content_sha256"]) is not str
        or _DIGEST.fullmatch(source["source_content_sha256"]) is None
        or type(source["locator_digest"]) is not str
        or _DIGEST.fullmatch(source["locator_digest"]) is None
        or type(source["authority_revision"]) is not str
        or _DIGEST.fullmatch(source["authority_revision"]) is None
        or type(source["authority_digest"]) is not str
        or _DIGEST.fullmatch(source["authority_digest"]) is None
        or (
            source["predecessor_source_revision_id"] is not None
            and (
                type(source["predecessor_source_revision_id"]) is not str
                or _IDENTIFIER.fullmatch(source["predecessor_source_revision_id"]) is None
            )
        )
        or type(source["observed_at"]) is not str
        or type(provenance) is not dict
        or frozenset(provenance) != _PROVENANCE_FIELDS
    ):
        _fail("existing_state_denied")
    if (
        source["source_revision_id"] != _source_revision_id(
            source["revision_ref"], source["source_content_sha256"]
        )
        or source["source_content_sha256"] != revision.payload_sha256
        or revision.payload_media_type != descriptor.mime_type.split(";", 1)[0]
        or provenance != {
            "role": "origin",
            "source_revision_id": source["source_revision_id"],
            "knowledge_revision_id": None,
            "transformation_digest": None,
            "admission_receipt_digest": source["authority_revision"],
        }
    ):
        _fail("existing_state_denied")
    return dict(source)


def _load_existing(
    store: HarbourKnowledgeStore,
    principal: TrustedPrincipal,
    *,
    owner_id: str,
    knowledge_id: str,
    descriptor: _DetachedDescriptorAuthority,
) -> tuple[KnowledgeHead | None, dict[str, Any] | None]:
    try:
        raw_head = store.get_head(principal, owner_id=owner_id, knowledge_id=knowledge_id)
    except HarbourKnowledgeStoreError as error:
        if error.code == "not_found":
            return None, None
        _fail("store_failed")
    except BaseException:
        _fail("store_failed")
    head = _copy_head(raw_head, owner_id=owner_id, knowledge_id=knowledge_id)
    if head.state != "active":
        _fail("lifecycle_denied")
    try:
        revision = store.read_revision(
            principal, owner_id=owner_id, revision_id=head.current_revision_id
        )
    except BaseException:
        _fail("store_failed")
    return head, _current_source(
        revision,
        owner_id=owner_id,
        knowledge_id=knowledge_id,
        descriptor=descriptor,
        head=head,
    )


def _read_exact(
    reader: PersonalDocsOwnerReader,
    descriptor: PersonalDocsFileDescriptor,
    authority: _DetachedDescriptorAuthority,
) -> tuple[bytes, str]:
    try:
        result = reader.read_exact(descriptor)
    except PersonalDocsOwnerReaderError:
        _fail("source_denied")
    except BaseException:
        _fail("source_denied")

    try:
        if type(result) is not PersonalDocsExactRead:
            _fail("source_denied")
        locator = result.locator
        if type(locator) is not PersonalDocsWholeFileLocator:
            _fail("source_denied")
        result_fields = (
            result.source_ref,
            result.revision_ref,
            result.content,
            result.extracted_content_hash,
            result.byte_size,
            result.char_size,
        )
        locator_fields = (
            locator.source_ref,
            locator.revision_ref,
            locator.start_char,
            locator.end_char,
            locator.locator_digest,
        )
    except NextcloudKnowledgeIngestionError:
        raise
    except BaseException:
        _fail("source_denied")

    source_ref, revision_ref, content, content_hash, byte_size, char_size = result_fields
    (
        locator_source_ref,
        locator_revision_ref,
        locator_start_char,
        locator_end_char,
        locator_digest,
    ) = locator_fields
    if (
        type(source_ref) is not str
        or type(revision_ref) is not str
        or type(content) is not str
        or type(content_hash) is not str
        or type(byte_size) is not int
        or type(char_size) is not int
        or type(locator_source_ref) is not str
        or type(locator_revision_ref) is not str
        or type(locator_start_char) is not int
        or type(locator_end_char) is not int
        or type(locator_digest) is not str
        or source_ref != authority.source_ref
        or revision_ref != authority.revision_ref
        or content_hash != authority.extracted_content_hash
        or locator_source_ref != authority.locator_source_ref
        or locator_revision_ref != authority.locator_revision_ref
        or locator_start_char != authority.locator_start_char
        or locator_end_char != authority.locator_end_char
        or locator_digest != authority.locator_digest
        or byte_size != authority.byte_size
        or char_size != authority.char_size
    ):
        _fail("source_denied")
    try:
        payload = content.encode("utf-8", "strict")
    except UnicodeError:
        _fail("source_denied")
    payload_digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    if (
        not payload
        or len(payload) != authority.byte_size
        or len(content) != authority.char_size
        or payload_digest != authority.revision_ref
        or payload_digest != authority.extracted_content_hash
    ):
        _fail("source_denied")
    return payload, payload_digest


def ingest_nextcloud_personal_document(
    *,
    reader: PersonalDocsOwnerReader,
    inventory_record: BigDataLedgerRecord,
    store: HarbourKnowledgeStore,
    principal: TrustedPrincipal,
    owner_id: str,
) -> NextcloudKnowledgeIngestionResult:
    """Ingest one sealed synthetic UTF-8 text file into durable Knowledge."""

    try:
        if (
            type(reader) is not PersonalDocsOwnerReader
            or type(inventory_record) is not BigDataLedgerRecord
            or type(store) is not HarbourKnowledgeStore
            or type(principal) is not TrustedPrincipal
            or type(owner_id) is not str
        ):
            _fail("invalid_request")

        # Native store authentication is deliberately the first capability I/O.
        try:
            store.validate_principal_binding(
                principal,
                owner_id=owner_id,
                expected_auth_context_digest=principal.auth_context_digest,
            )
        except BaseException:
            _fail("authentication_denied")

        initial_record = _record_snapshot(inventory_record)
        try:
            snapshot = reader.capture()
        except PersonalDocsOwnerReaderError:
            _fail("source_denied")
        except BaseException:
            _fail("source_denied")
        if (
            type(snapshot) is not PersonalDocsSnapshot
            or snapshot.owner_ref != canonical_personal_docs_owner_ref(owner_id)
        ):
            _fail("source_denied")

        descriptor = _descriptor_for_record(snapshot, inventory_record)
        extension, observed_at = _validate_inventory(
            inventory_record, initial_record, descriptor
        )
        if _record_snapshot(inventory_record) != initial_record:
            _fail("inventory_denied")

        detached_snapshot = _detach_authority(snapshot, descriptor)
        detached_descriptor = detached_snapshot.descriptors[
            detached_snapshot.selected_index
        ]

        knowledge_id = _knowledge_id(owner_id, detached_descriptor.source_ref)
        prior_head, prior_source = _load_existing(
            store,
            principal,
            owner_id=owner_id,
            knowledge_id=knowledge_id,
            descriptor=detached_descriptor,
        )
        if _record_snapshot(inventory_record) != initial_record:
            _fail("inventory_denied")

        payload, payload_digest = _read_exact(
            reader, descriptor, detached_descriptor
        )
        _require_unchanged_authority(snapshot, descriptor, detached_snapshot)
        if _record_snapshot(inventory_record) != initial_record:
            _fail("inventory_denied")
        source_revision_id = _source_revision_id(
            detached_descriptor.revision_ref, payload_digest
        )

        if prior_head is not None and prior_source is not None:
            if (
                prior_source["revision_ref"] == detached_descriptor.revision_ref
                and prior_source["source_content_sha256"] == payload_digest
                and prior_head.current_payload_sha256 == payload_digest
            ):
                try:
                    reread = store.get_head(
                        principal, owner_id=owner_id, knowledge_id=knowledge_id
                    )
                except BaseException:
                    _fail("store_failed")
                stable = _copy_head(reread, owner_id=owner_id, knowledge_id=knowledge_id)
                if stable != prior_head:
                    _fail("stale_state")
                return NextcloudKnowledgeIngestionResult(
                    "unchanged", knowledge_id, source_revision_id, stable, None
                )

        predecessor = None if prior_source is None else prior_source["source_revision_id"]
        sources = _source_vector(
            detached_descriptor,
            snapshot_digest=detached_snapshot.snapshot_digest,
            payload_digest=payload_digest,
            predecessor=predecessor,
            observed_at=observed_at,
        )
        provenance = _provenance(
            source_revision_id, detached_snapshot.snapshot_digest
        )
        common = {
            "owner_id": owner_id,
            "knowledge_id": knowledge_id,
            "payload_bytes": payload,
            "payload_schema_id": PAYLOAD_SCHEMA_ID,
            "payload_media_type": _EXTENSION_MEDIA[extension],
            "classification": CLASSIFICATION,
            "source_revisions": sources,
            "provenance": provenance,
            "references": (),
            "retention": dict(RETENTION),
        }
        try:
            if prior_head is None:
                operation = "create_object"
                raw_receipt = store.create_object(
                    principal,
                    knowledge_kind=KNOWLEDGE_KIND,
                    idempotency_key=_idempotency_key(
                        "create", owner_id, knowledge_id, source_revision_id
                    ),
                    expected_generation=0,
                    **common,
                )
                outcome = "created"
            else:
                operation = "append_revision"
                raw_receipt = store.append_revision(
                    principal,
                    expected_head=prior_head,
                    idempotency_key=_idempotency_key(
                        "append", owner_id, knowledge_id, source_revision_id
                    ),
                    **common,
                )
                outcome = "appended"
        except BaseException:
            _fail("store_failed")
        receipt = _copy_receipt(
            raw_receipt,
            operation=operation,
            owner_id=owner_id,
            knowledge_id=knowledge_id,
            before_head=prior_head,
            payload_digest=payload_digest,
        )
        return NextcloudKnowledgeIngestionResult(
            outcome, knowledge_id, source_revision_id, receipt.after_head, receipt
        )
    except NextcloudKnowledgeIngestionError as error:
        public_error_code = error.code
    except BaseException:
        public_error_code = "ingestion_failed"
    raise NextcloudKnowledgeIngestionError(public_error_code) from None


__all__ = [
    "AUTHORITY_DOMAIN",
    "CLASSIFICATION",
    "KNOWLEDGE_KIND",
    "PAYLOAD_SCHEMA_ID",
    "RETENTION",
    "SOURCE_KIND",
    "NextcloudKnowledgeIngestionError",
    "NextcloudKnowledgeIngestionResult",
    "canonical_personal_docs_owner_ref",
    "ingest_nextcloud_personal_document",
]
