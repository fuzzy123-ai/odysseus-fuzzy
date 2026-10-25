"""Internal exact-body adapter for explicitly approved Planning Markdown sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import ntpath
import re
import unicodedata

from src.planning_mcp_service import (
    MAX_OWNER_MARKDOWN_BODY_BYTES,
    MAX_OWNER_MARKDOWN_METADATA_BYTES,
    MAX_OWNER_MARKDOWN_SELECTIONS,
    OWNER_MARKDOWN_CAPTURE_SCHEMA_ID,
    OWNER_MARKDOWN_DESCRIPTOR_SCHEMA_ID,
    OWNER_MARKDOWN_POLICY_REF,
    OWNER_MARKDOWN_READ_SCHEMA_ID,
    PlanningMcpService,
    PlanningServiceError,
)
from src.rag_text_chunking import (
    EXACT_CHARACTER_SPLITTER_VERSION,
    split_text_into_exact_character_ranges,
)
from src.unified_source_index_contract import (
    MAX_TEXT_CHARS,
    ChunkRecord,
    Classification,
    ContentPolicy,
    EntityRecord,
    PolicyEvidence,
    RecordKind,
    RelationRecord,
    SourceKind,
    SourceRecord,
    SourceVersionRecord,
    TextRangeLocator,
    content_hash,
    make_source_id,
    make_source_version_id,
)
from src.unified_source_index_owner_scope import OwnerScope
from src.unified_source_index_source_capability import (
    OwnerScopeRequirement,
    ProviderConstraint,
    QueryCapability,
    SourceAdapterCapabilityManifest,
    SourceAdapterOperation,
)


PLANNING_MARKDOWN_SOURCE_ADAPTER_ID = "planning.approved_markdown_body"
PLANNING_MARKDOWN_SOURCE_ADAPTER_VERSION = "v1"
PLANNING_MARKDOWN_SOURCE_DOMAIN_ID = "planning"
PLANNING_MARKDOWN_SOURCE_EXACT_READER_BOUNDARY = "planning.owner_markdown_body_reader"
PLANNING_MARKDOWN_SOURCE_EXTRACTOR_PROFILE = "planning.approved_markdown_body.exact.v1"

PLANNING_MARKDOWN_SOURCE_IDENTITY_SCHEMA = "odysseus.usi.planning_markdown_source_identity.v1"
PLANNING_MARKDOWN_SOURCE_REVISION_SCHEMA = "odysseus.usi.planning_markdown_source_revision.v1"
PLANNING_MARKDOWN_SOURCE_BINDING_SCHEMA = "odysseus.usi.planning_markdown_source_authority_binding.v1"
PLANNING_MARKDOWN_SOURCE_EVIDENCE_SCHEMA = "odysseus.usi.planning_markdown_source_evidence.v1"
PLANNING_MARKDOWN_BODY_LOCATOR_SCHEMA = "odysseus.usi.planning_markdown_body_locator.v1"
PLANNING_MARKDOWN_CHARACTER_RANGE_REQUEST_SCHEMA = "odysseus.usi.planning_markdown_character_range_request.v1"

MAX_MARKDOWN_BODY_BYTES = MAX_OWNER_MARKDOWN_BODY_BYTES
MAX_PLANNING_MARKDOWN_SOURCES = MAX_OWNER_MARKDOWN_SELECTIONS
MAX_PLANNING_MARKDOWN_CHUNKS = 8
MAX_MARKDOWN_METADATA_BYTES = MAX_OWNER_MARKDOWN_METADATA_BYTES

_SHA_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_GENERATION_RE = re.compile(r"^usi_generation_[0-9a-f]{64}$")
_LOGICAL_REF_RE = re.compile(r"^planning:markdown:sha256:[0-9a-f]{64}$")
_RECORD_ID_RE = re.compile(r"^usi_(source|version)_[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_WINDOWS_FORBIDDEN = frozenset('<>:"\\|?*')
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
     *(f"COM{index}" for index in range(1, 10)),
     *(f"LPT{index}" for index in range(1, 10)),
     "COM\u00b9", "COM\u00b2", "COM\u00b3", "LPT\u00b9", "LPT\u00b2", "LPT\u00b3"}
)
_ERROR_CODES = frozenset({
    "invalid_authority", "invalid_binding", "invalid_snapshot", "invalid_request",
    "record_not_found", "unavailable_state_unproven", "stale_authority",
    "source_limit_exceeded", "body_limit_exceeded",
    "planning_markdown_source_adapter_failed",
})
_CAPTURE_KEYS = frozenset({
    "schema_id", "owner_ref", "selection_mode", "selection_digest",
    "planning_snapshot_digest", "descriptors", "eligible_count", "classification",
    "content_policy", "owner_policy_ref", "body_exact", "canonical_truth",
    "read_only", "writes_performed", "snapshot_digest",
})
_DESCRIPTOR_KEYS = frozenset({
    "schema_id", "owner_ref", "project_id", "roadmap_id", "approved_revision",
    "domain_content_hash", "canonical_projection_hash", "planning_source_ref",
    "planning_snapshot_digest", "selection_digest", "source_ref", "body_sha256",
    "byte_count", "classification", "content_policy", "owner_policy_ref",
    "body_exact", "canonical_truth", "version_digest",
})
_READ_KEYS = frozenset({
    "schema_id", "owner_ref", "snapshot_digest", "selection_digest",
    "planning_snapshot_digest", "descriptor", "content", "body_sha256",
    "byte_count", "classification", "content_policy", "owner_policy_ref",
    "body_exact", "canonical_truth", "read_only", "writes_performed",
})


def _planning_markdown_manifest() -> SourceAdapterCapabilityManifest:
    return SourceAdapterCapabilityManifest(
        adapter_id=PLANNING_MARKDOWN_SOURCE_ADAPTER_ID,
        adapter_version=PLANNING_MARKDOWN_SOURCE_ADAPTER_VERSION,
        domain_id=PLANNING_MARKDOWN_SOURCE_DOMAIN_ID,
        source_kind=SourceKind.PLANNING,
        content_policy=ContentPolicy.INLINE_LOCAL,
        classification_ceiling=Classification.PRIVATE,
        owner_scope_requirement=OwnerScopeRequirement.IMMUTABLE_OPAQUE,
        provider_constraint=ProviderConstraint.LOCAL_ACCEPTED_BOUNDARY,
        query_capability=QueryCapability.EXACT_READER,
        operations=tuple(SourceAdapterOperation),
        exact_reader_boundary=PLANNING_MARKDOWN_SOURCE_EXACT_READER_BOUNDARY,
        productive_default_enabled=False,
    )


PLANNING_MARKDOWN_SOURCE_ADAPTER_GENERATION = _planning_markdown_manifest().generation_ref


class PlanningMarkdownSourceAdapterError(ValueError):
    """A fixed-code error that never includes Markdown or upstream details."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        safe = code if type(code) is str and code in _ERROR_CODES else "planning_markdown_source_adapter_failed"
        self.code = safe
        super().__init__(safe)

    def __repr__(self) -> str:
        return f"PlanningMarkdownSourceAdapterError(code={self.code!r})"


def _fail(code: str) -> None:
    raise PlanningMarkdownSourceAdapterError(code) from None


def _canonical_bytes(value: object) -> bytes:
    failure = False
    result = b""
    try:
        result = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        failure = True
    if failure:
        _fail("invalid_snapshot")
    return result


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _exact_text(value: object, maximum: int, code: str, *, allow_empty: bool = False) -> str:
    if (type(value) is not str or (not allow_empty and not value) or len(value) > maximum
            or (value and value != value.strip()) or any(ord(ch) < 32 for ch in value)):
        _fail(code)
    encoding_failed = False
    try:
        value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        encoding_failed = True
    if encoding_failed:
        _fail(code)
    return value


def _owner_ref(value: object, code: str) -> str:
    return _exact_text(value, 128, code)


def _identifier(value: object, code: str) -> str:
    if type(value) is not str or _IDENTIFIER_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _sha(value: object, code: str) -> str:
    if type(value) is not str or _SHA_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _generation(value: object, code: str) -> str:
    if type(value) is not str or _GENERATION_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _logical_ref_value(value: object, code: str) -> str:
    if type(value) is not str or _LOGICAL_REF_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _record_id(value: object, kind: str, code: str) -> str:
    if type(value) is not str:
        _fail(code)
    match = _RECORD_ID_RE.fullmatch(value)
    if match is None or match.group(1) != kind:
        _fail(code)
    return value


def _timestamp(value: object, code: str) -> str:
    text = _exact_text(value, 64, code)
    failure = False
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            failure = True
    except (TypeError, ValueError, OverflowError):
        failure = True
    if failure:
        _fail(code)
    return text


def _safe_windows_component(component: str) -> bool:
    if (not component or component in {".", ".."} or component.endswith((".", " "))
            or any(ord(ch) < 32 or ch in _WINDOWS_FORBIDDEN for ch in component)):
        return False
    return component.partition(".")[0].rstrip(" ").upper() not in _WINDOWS_RESERVED


def _repo_ref(value: object, code: str, *, markdown: bool) -> str:
    if (type(value) is not str or not value or value != value.strip()
            or unicodedata.normalize("NFC", value) != value or "\\" in value
            or value.startswith(("/", "~")) or ntpath.splitdrive(value)[0]
            or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value)
            or any(ord(ch) < 32 for ch in value)):
        _fail(code)
    failure = False
    encoded = b""
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        failure = True
    if failure:
        _fail(code)
    parts = tuple(value.split("/"))
    roots = tuple(root for root in ("docs/plans", "specs/roadmaps") if value.startswith(root + "/"))
    suffixes = (".md", ".markdown") if markdown else (".json",)
    if (len(encoded) > 4_096 or len(parts) > 32 or any(not _safe_windows_component(part) for part in parts)
            or len(roots) != 1 or not value.lower().endswith(suffixes)):
        _fail(code)
    return value


def _logical_ref(owner: str, project: str, roadmap: str, source_ref: str) -> str:
    return "planning:markdown:" + _digest({
        "schema": PLANNING_MARKDOWN_SOURCE_IDENTITY_SCHEMA,
        "owner_ref": owner, "project_id": project, "roadmap_id": roadmap,
        "source_ref": source_ref,
    })


@dataclass(frozen=True, slots=True, repr=False)
class _CapturedDescriptor:
    owner_ref: str
    project_id: str
    roadmap_id: str
    approved_revision: int
    domain_content_hash: str
    canonical_projection_hash: str
    planning_source_ref: str
    planning_snapshot_digest: str
    selection_digest: str
    source_ref: str
    body_sha256: str
    byte_count: int
    version_digest: str
    logical_ref: str


@dataclass(frozen=True, slots=True, repr=False)
class _CapturedSnapshot:
    owner_ref: str
    selection_digest: str
    planning_snapshot_digest: str
    descriptors: tuple[_CapturedDescriptor, ...]
    snapshot_digest: str


@dataclass(frozen=True, slots=True, repr=False)
class _AdapterContext:
    service: PlanningMcpService
    owner_ref: str
    observed_at: str
    binding_fields: tuple[str, ...]
    snapshot: _CapturedSnapshot


def _captured_descriptor_is_primitive(value: object) -> bool:
    return (
        type(value) is _CapturedDescriptor
        and type(value.owner_ref) is str
        and type(value.project_id) is str
        and type(value.roadmap_id) is str
        and type(value.approved_revision) is int
        and type(value.domain_content_hash) is str
        and type(value.canonical_projection_hash) is str
        and type(value.planning_source_ref) is str
        and type(value.planning_snapshot_digest) is str
        and type(value.selection_digest) is str
        and type(value.source_ref) is str
        and type(value.body_sha256) is str
        and type(value.byte_count) is int
        and type(value.version_digest) is str
        and type(value.logical_ref) is str
    )


def _captured_snapshot_is_primitive(value: object) -> bool:
    return (
        type(value) is _CapturedSnapshot
        and type(value.owner_ref) is str
        and type(value.selection_digest) is str
        and type(value.planning_snapshot_digest) is str
        and type(value.descriptors) is tuple
        and len(value.descriptors) <= MAX_PLANNING_MARKDOWN_SOURCES
        and all(_captured_descriptor_is_primitive(item) for item in value.descriptors)
        and type(value.snapshot_digest) is str
    )


def _detached_context_snapshot(value: _CapturedSnapshot) -> _CapturedSnapshot:
    if not _captured_snapshot_is_primitive(value):
        _fail("invalid_authority")
    descriptors = tuple(
        _CapturedDescriptor(
            item.owner_ref, item.project_id, item.roadmap_id,
            item.approved_revision, item.domain_content_hash,
            item.canonical_projection_hash, item.planning_source_ref,
            item.planning_snapshot_digest, item.selection_digest,
            item.source_ref, item.body_sha256, item.byte_count,
            item.version_digest, item.logical_ref,
        )
        for item in value.descriptors
    )
    return _CapturedSnapshot(
        value.owner_ref, value.selection_digest,
        value.planning_snapshot_digest, descriptors, value.snapshot_digest,
    )


def _revision_digest(item: _CapturedDescriptor, markdown_snapshot_digest: str) -> str:
    return _digest({
        "schema": PLANNING_MARKDOWN_SOURCE_REVISION_SCHEMA,
        "approved_revision": item.approved_revision,
        "domain_content_hash": item.domain_content_hash,
        "canonical_projection_hash": item.canonical_projection_hash,
        "planning_source_ref": item.planning_source_ref,
        "planning_snapshot_digest": item.planning_snapshot_digest,
        "selection_digest": item.selection_digest,
        "source_ref": item.source_ref,
        "descriptor_version_digest": item.version_digest,
        "markdown_snapshot_digest": markdown_snapshot_digest,
        "body_sha256": item.body_sha256,
        "byte_count": item.byte_count,
    })


def _authority_binding_digest(owner_scope: str, owner_ref: str, snapshot_digest: str) -> str:
    return _digest({
        "adapter_generation": PLANNING_MARKDOWN_SOURCE_ADAPTER_GENERATION,
        "adapter_id": PLANNING_MARKDOWN_SOURCE_ADAPTER_ID,
        "adapter_version": PLANNING_MARKDOWN_SOURCE_ADAPTER_VERSION,
        "owner_ref": owner_ref,
        "owner_scope": owner_scope,
        "schema": PLANNING_MARKDOWN_SOURCE_BINDING_SCHEMA,
        "snapshot_digest": snapshot_digest,
    })


def _service_descriptor_digest(item: _CapturedDescriptor) -> str:
    return _digest({
        "schema_id": OWNER_MARKDOWN_DESCRIPTOR_SCHEMA_ID,
        "owner_ref": item.owner_ref,
        "project_id": item.project_id,
        "roadmap_id": item.roadmap_id,
        "approved_revision": item.approved_revision,
        "domain_content_hash": item.domain_content_hash,
        "canonical_projection_hash": item.canonical_projection_hash,
        "planning_source_ref": item.planning_source_ref,
        "planning_snapshot_digest": item.planning_snapshot_digest,
        "selection_digest": item.selection_digest,
        "source_ref": item.source_ref,
        "body_sha256": item.body_sha256,
        "byte_count": item.byte_count,
        "classification": "private",
        "content_policy": "inline_local",
        "owner_policy_ref": OWNER_MARKDOWN_POLICY_REF,
        "body_exact": True,
        "canonical_truth": False,
    })


@dataclass(frozen=True, slots=True, repr=False)
class PlanningMarkdownSourceAuthorityBinding:
    owner_scope: OwnerScope
    owner_ref: str
    snapshot_digest: str
    adapter_id: str
    adapter_version: str
    adapter_generation: str
    binding_digest: str = ""
    schema: str = PLANNING_MARKDOWN_SOURCE_BINDING_SCHEMA

    def __post_init__(self) -> None:
        failure: str | None = None
        try:
            if type(self.owner_scope) is not OwnerScope:
                _fail("invalid_binding")
            scope = OwnerScope(_exact_text(self.owner_scope.value, 64, "invalid_binding"))
            owner = _owner_ref(self.owner_ref, "invalid_binding")
            snapshot = _sha(self.snapshot_digest, "invalid_binding")
            adapter_id = _exact_text(self.adapter_id, 64, "invalid_binding")
            version = _exact_text(self.adapter_version, 64, "invalid_binding")
            generation = _generation(self.adapter_generation, "invalid_binding")
            schema = _exact_text(self.schema, 128, "invalid_binding")
            if (adapter_id != PLANNING_MARKDOWN_SOURCE_ADAPTER_ID
                    or version != PLANNING_MARKDOWN_SOURCE_ADAPTER_VERSION
                    or generation != PLANNING_MARKDOWN_SOURCE_ADAPTER_GENERATION
                    or schema != PLANNING_MARKDOWN_SOURCE_BINDING_SCHEMA
                    or type(self.binding_digest) is not str):
                _fail("invalid_binding")
            expected = _authority_binding_digest(scope.value, owner, snapshot)
            if self.binding_digest and self.binding_digest != expected:
                _fail("invalid_binding")
            object.__setattr__(self, "owner_scope", scope)
            object.__setattr__(self, "owner_ref", owner)
            object.__setattr__(self, "snapshot_digest", snapshot)
            object.__setattr__(self, "binding_digest", expected)
        except PlanningMarkdownSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "invalid_binding"
        if failure is not None:
            _fail(failure)

    def __repr__(self) -> str:
        return f"PlanningMarkdownSourceAuthorityBinding(binding_digest={self.binding_digest!r})"

@dataclass(frozen=True, slots=True, repr=False)
class PlanningMarkdownSourceEvidence:
    logical_ref: str
    owner_scope: str
    owner_ref: str
    project_id: str
    roadmap_id: str
    approved_revision: int
    domain_content_hash: str
    canonical_projection_hash: str
    planning_source_ref: str
    planning_snapshot_digest: str
    selection_digest: str
    source_ref: str
    body_sha256: str
    byte_count: int
    descriptor_version_digest: str
    markdown_snapshot_digest: str
    binding_digest: str
    revision_digest: str
    owner_policy_ref: str
    source_id: str
    source_version_id: str
    schema: str = PLANNING_MARKDOWN_SOURCE_EVIDENCE_SCHEMA

    def __post_init__(self) -> None:
        failure: str | None = None
        try:
            logical = _logical_ref_value(self.logical_ref, "invalid_snapshot")
            scope = _exact_text(self.owner_scope, 64, "invalid_snapshot")
            owner = _owner_ref(self.owner_ref, "invalid_snapshot")
            project = _identifier(self.project_id, "invalid_snapshot")
            roadmap = _identifier(self.roadmap_id, "invalid_snapshot")
            revision = self.approved_revision
            byte_count = self.byte_count
            if type(revision) is not int or not 1 <= revision <= 2**63 - 1:
                _fail("invalid_snapshot")
            if type(byte_count) is not int or not 0 <= byte_count <= MAX_MARKDOWN_BODY_BYTES:
                _fail("invalid_snapshot")
            domain_hash = _sha(self.domain_content_hash, "invalid_snapshot")
            projection_hash = _sha(self.canonical_projection_hash, "invalid_snapshot")
            planning_ref = _repo_ref(self.planning_source_ref, "invalid_snapshot", markdown=False)
            planning_snapshot = _sha(self.planning_snapshot_digest, "invalid_snapshot")
            selection = _sha(self.selection_digest, "invalid_snapshot")
            source_ref = _repo_ref(self.source_ref, "invalid_snapshot", markdown=True)
            body_hash = _sha(self.body_sha256, "invalid_snapshot")
            descriptor_digest = _sha(self.descriptor_version_digest, "invalid_snapshot")
            markdown_snapshot = _sha(self.markdown_snapshot_digest, "invalid_snapshot")
            binding = _sha(self.binding_digest, "invalid_snapshot")
            revision_digest = _sha(self.revision_digest, "invalid_snapshot")
            policy = _exact_text(self.owner_policy_ref, 256, "invalid_snapshot")
            source_id = _record_id(self.source_id, "source", "invalid_snapshot")
            version_id = _record_id(self.source_version_id, "version", "invalid_snapshot")
            schema = _exact_text(self.schema, 128, "invalid_snapshot")
            item = _CapturedDescriptor(
                owner, project, roadmap, revision, domain_hash, projection_hash,
                planning_ref, planning_snapshot, selection, source_ref, body_hash,
                byte_count, descriptor_digest, logical,
            )
            expected_ref = _logical_ref(owner, project, roadmap, source_ref)
            expected_revision = _revision_digest(item, markdown_snapshot)
            revision_ref = f"approved-markdown:{revision}:{revision_digest}"
            expected_binding = _authority_binding_digest(scope, owner, markdown_snapshot)
            expected_descriptor = _service_descriptor_digest(item)
            if (schema != PLANNING_MARKDOWN_SOURCE_EVIDENCE_SCHEMA
                    or policy != OWNER_MARKDOWN_POLICY_REF
                    or logical != expected_ref or revision_digest != expected_revision
                    or binding != expected_binding or descriptor_digest != expected_descriptor
                    or source_id != make_source_id(scope, SourceKind.PLANNING, logical)
                    or version_id != make_source_version_id(source_id, revision_ref, body_hash)):
                _fail("invalid_snapshot")
            object.__setattr__(self, "logical_ref", logical)
            object.__setattr__(self, "owner_scope", scope)
            object.__setattr__(self, "owner_ref", owner)
            object.__setattr__(self, "project_id", project)
            object.__setattr__(self, "roadmap_id", roadmap)
            object.__setattr__(self, "planning_source_ref", planning_ref)
            object.__setattr__(self, "source_ref", source_ref)
            object.__setattr__(self, "source_id", source_id)
            object.__setattr__(self, "source_version_id", version_id)
        except PlanningMarkdownSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "invalid_snapshot"
        if failure is not None:
            _fail(failure)

    def __repr__(self) -> str:
        return f"PlanningMarkdownSourceEvidence(logical_ref={self.logical_ref!r})"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningMarkdownBodyLocator:
    logical_ref: str
    source_id: str
    source_version_id: str
    body_sha256: str
    byte_count: int
    descriptor_version_digest: str
    markdown_snapshot_digest: str
    binding_digest: str
    locator_digest: str = ""
    schema: str = PLANNING_MARKDOWN_BODY_LOCATOR_SCHEMA

    def __post_init__(self) -> None:
        failure: str | None = None
        try:
            logical = _logical_ref_value(self.logical_ref, "invalid_request")
            source_id = _record_id(self.source_id, "source", "invalid_request")
            version_id = _record_id(self.source_version_id, "version", "invalid_request")
            body_hash = _sha(self.body_sha256, "invalid_request")
            byte_count = self.byte_count
            if type(byte_count) is not int or not 0 <= byte_count <= MAX_MARKDOWN_BODY_BYTES:
                _fail("invalid_request")
            descriptor_digest = _sha(self.descriptor_version_digest, "invalid_request")
            snapshot = _sha(self.markdown_snapshot_digest, "invalid_request")
            binding = _sha(self.binding_digest, "invalid_request")
            schema = _exact_text(self.schema, 128, "invalid_request")
            if schema != PLANNING_MARKDOWN_BODY_LOCATOR_SCHEMA or type(self.locator_digest) is not str:
                _fail("invalid_request")
            expected = _digest({
                "binding_digest": binding, "body_sha256": body_hash,
                "byte_count": byte_count, "descriptor_version_digest": descriptor_digest,
                "logical_ref": logical, "markdown_snapshot_digest": snapshot,
                "schema": schema, "source_id": source_id,
                "source_version_id": version_id,
            })
            if self.locator_digest and self.locator_digest != expected:
                _fail("invalid_request")
            object.__setattr__(self, "logical_ref", logical)
            object.__setattr__(self, "source_id", source_id)
            object.__setattr__(self, "source_version_id", version_id)
            object.__setattr__(self, "locator_digest", expected)
        except PlanningMarkdownSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "invalid_request"
        if failure is not None:
            _fail(failure)

    def __repr__(self) -> str:
        return f"PlanningMarkdownBodyLocator(locator_digest={self.locator_digest!r})"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningMarkdownCharacterRangeRequest:
    body_locator: PlanningMarkdownBodyLocator
    text_range: TextRangeLocator
    request_digest: str = ""
    schema: str = PLANNING_MARKDOWN_CHARACTER_RANGE_REQUEST_SCHEMA

    def __post_init__(self) -> None:
        failure: str | None = None
        try:
            locator = _copy_locator(self.body_locator)
            text_range = _copy_text_range(self.text_range, "invalid_request")
            if (locator.byte_count == 0 or text_range.end_char > MAX_MARKDOWN_BODY_BYTES
                    or text_range.end_char > locator.byte_count):
                _fail("invalid_request")
            schema = _exact_text(self.schema, 128, "invalid_request")
            if schema != PLANNING_MARKDOWN_CHARACTER_RANGE_REQUEST_SCHEMA or type(self.request_digest) is not str:
                _fail("invalid_request")
            expected = _digest({
                "body_locator_digest": locator.locator_digest,
                "schema": schema,
                "text_range": {"end_char": text_range.end_char, "start_char": text_range.start_char},
            })
            if self.request_digest and self.request_digest != expected:
                _fail("invalid_request")
            object.__setattr__(self, "body_locator", locator)
            object.__setattr__(self, "text_range", text_range)
            object.__setattr__(self, "request_digest", expected)
        except PlanningMarkdownSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "invalid_request"
        if failure is not None:
            _fail(failure)

    def __repr__(self) -> str:
        return f"PlanningMarkdownCharacterRangeRequest(request_digest={self.request_digest!r})"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningMarkdownSourceDescriptor:
    source: SourceRecord
    source_version: SourceVersionRecord
    body_locator: PlanningMarkdownBodyLocator
    evidence: PlanningMarkdownSourceEvidence

    def __post_init__(self) -> None:
        failure: str | None = None
        try:
            source = _copy_source(self.source, "invalid_snapshot")
            version = _copy_version(self.source_version, "invalid_snapshot")
            locator = _copy_locator(self.body_locator)
            evidence = _copy_evidence(self.evidence)
            revision_ref = f"approved-markdown:{evidence.approved_revision}:{evidence.revision_digest}"
            if (source.source_kind is not SourceKind.PLANNING
                    or source.classification is not Classification.PRIVATE
                    or source.content_policy is not ContentPolicy.INLINE_LOCAL
                    or source.provider_ref != PLANNING_MARKDOWN_SOURCE_ADAPTER_ID
                    or source.owner_scope != evidence.owner_scope
                    or source.canonical_ref != evidence.logical_ref
                    or version.source_id != source.source_id
                    or version.provider_ref != source.provider_ref
                    or version.owner_scope != source.owner_scope
                    or version.classification is not Classification.PRIVATE
                    or version.content_policy is not ContentPolicy.INLINE_LOCAL
                    or version.policy_evidence != source.policy_evidence()
                    or version.content_hash != evidence.body_sha256
                    or version.revision_ref != revision_ref
                    or locator.logical_ref != evidence.logical_ref
                    or locator.source_id != source.source_id
                    or locator.source_version_id != version.source_version_id
                    or locator.body_sha256 != evidence.body_sha256
                    or locator.byte_count != evidence.byte_count
                    or locator.descriptor_version_digest != evidence.descriptor_version_digest
                    or locator.markdown_snapshot_digest != evidence.markdown_snapshot_digest
                    or locator.binding_digest != evidence.binding_digest
                    or evidence.source_id != source.source_id
                    or evidence.source_version_id != version.source_version_id):
                _fail("invalid_snapshot")
            object.__setattr__(self, "source", source)
            object.__setattr__(self, "source_version", version)
            object.__setattr__(self, "body_locator", locator)
            object.__setattr__(self, "evidence", evidence)
        except PlanningMarkdownSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "invalid_snapshot"
        if failure is not None:
            _fail(failure)

    @property
    def projection_locator(self) -> PlanningMarkdownBodyLocator:
        return self.body_locator

    def __repr__(self) -> str:
        return f"PlanningMarkdownSourceDescriptor(logical_ref={self.evidence.logical_ref!r})"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningMarkdownDiscoveryPage:
    items: tuple[PlanningMarkdownSourceDescriptor, ...]
    next_cursor: int | None
    snapshot_digest: str
    binding_digest: str

    def __post_init__(self) -> None:
        failure: str | None = None
        try:
            if type(self.items) is not tuple or len(self.items) > MAX_PLANNING_MARKDOWN_SOURCES:
                _fail("invalid_snapshot")
            if self.next_cursor is not None and (
                type(self.next_cursor) is not int
                or not 0 <= self.next_cursor <= MAX_PLANNING_MARKDOWN_SOURCES
            ):
                _fail("invalid_snapshot")
            snapshot = _sha(self.snapshot_digest, "invalid_snapshot")
            binding = _sha(self.binding_digest, "invalid_snapshot")
            items = tuple(_copy_descriptor(item) for item in self.items)
            if any(item.evidence.markdown_snapshot_digest != snapshot
                   or item.evidence.binding_digest != binding for item in items):
                _fail("invalid_snapshot")
            object.__setattr__(self, "items", items)
        except PlanningMarkdownSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "invalid_snapshot"
        if failure is not None:
            _fail(failure)

    def __repr__(self) -> str:
        return f"PlanningMarkdownDiscoveryPage(item_count={len(self.items)}, next_cursor={self.next_cursor!r})"

@dataclass(frozen=True, slots=True, repr=False)
class PlanningMarkdownSourceOccurrence:
    source: SourceRecord
    source_version: SourceVersionRecord
    chunks: tuple[ChunkRecord, ...]
    entities: tuple[EntityRecord, ...]
    relations: tuple[RelationRecord, ...]
    body_locator: PlanningMarkdownBodyLocator
    evidence: PlanningMarkdownSourceEvidence
    splitter_version: str

    def __post_init__(self) -> None:
        failure: str | None = None
        try:
            descriptor = PlanningMarkdownSourceDescriptor(
                self.source, self.source_version, self.body_locator, self.evidence
            )
            if (type(self.chunks) is not tuple or len(self.chunks) > MAX_PLANNING_MARKDOWN_CHUNKS
                    or type(self.entities) is not tuple or self.entities != ()
                    or type(self.relations) is not tuple or self.relations != ()
                    or type(self.splitter_version) is not str
                    or self.splitter_version != EXACT_CHARACTER_SPLITTER_VERSION):
                _fail("invalid_snapshot")
            chunks = tuple(_copy_chunk(item, "invalid_snapshot") for item in self.chunks)
            if (descriptor.evidence.byte_count == 0) != (len(chunks) == 0):
                _fail("invalid_snapshot")
            if descriptor.evidence.byte_count == 0 and descriptor.evidence.body_sha256 != content_hash(""):
                _fail("invalid_snapshot")
            ordered_start = 0
            total_characters = 0
            total_bytes = 0
            body_parts: list[str] = []
            for chunk in chunks:
                if (chunk.source_id != descriptor.source.source_id
                        or chunk.source_version_id != descriptor.source_version.source_version_id
                        or chunk.extractor_profile_ref != PLANNING_MARKDOWN_SOURCE_EXTRACTOR_PROFILE
                        or chunk.owner_scope != descriptor.source.owner_scope
                        or chunk.classification is not Classification.PRIVATE
                        or chunk.content_policy is not ContentPolicy.INLINE_LOCAL
                        or chunk.policy_evidence != descriptor.source_version.policy_evidence_ref()
                        or type(chunk.locator) is not TextRangeLocator
                        or type(chunk.content) is not str
                        or "\x00" in chunk.content
                        or chunk.locator.start_char != ordered_start
                        or chunk.locator.end_char - chunk.locator.start_char != len(chunk.content)):
                    _fail("invalid_snapshot")
                encoding_failed = False
                encoded = b""
                try:
                    encoded = chunk.content.encode("utf-8", "strict")
                except UnicodeEncodeError:
                    encoding_failed = True
                if encoding_failed:
                    _fail("invalid_snapshot")
                total_characters += len(chunk.content)
                total_bytes += len(encoded)
                if total_characters > MAX_MARKDOWN_BODY_BYTES or total_bytes > MAX_MARKDOWN_BODY_BYTES:
                    _fail("body_limit_exceeded")
                body_parts.append(chunk.content)
                ordered_start = chunk.locator.end_char
            if ordered_start != total_characters:
                _fail("invalid_snapshot")
            body = "".join(body_parts)
            if (total_bytes != descriptor.evidence.byte_count
                    or content_hash(body) != descriptor.evidence.body_sha256
                    or descriptor.source_version.content_hash != descriptor.evidence.body_sha256
                    or descriptor.body_locator.body_sha256 != descriptor.evidence.body_sha256):
                _fail("invalid_snapshot")
            object.__setattr__(self, "source", descriptor.source)
            object.__setattr__(self, "source_version", descriptor.source_version)
            object.__setattr__(self, "chunks", chunks)
            object.__setattr__(self, "entities", ())
            object.__setattr__(self, "relations", ())
            object.__setattr__(self, "body_locator", descriptor.body_locator)
            object.__setattr__(self, "evidence", descriptor.evidence)
        except PlanningMarkdownSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "invalid_snapshot"
        if failure is not None:
            _fail(failure)

    def __repr__(self) -> str:
        return f"PlanningMarkdownSourceOccurrence(logical_ref={self.evidence.logical_ref!r}, chunk_count={len(self.chunks)})"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningMarkdownExactRead:
    logical_ref: str
    content: str = field(repr=False)
    content_hash: str
    request: PlanningMarkdownCharacterRangeRequest
    evidence: PlanningMarkdownSourceEvidence

    def __post_init__(self) -> None:
        failure: str | None = None
        try:
            logical = _logical_ref_value(self.logical_ref, "invalid_snapshot")
            content = self.content
            if type(content) is not str or not content or "\x00" in content:
                _fail("invalid_snapshot")
            encoding_failed = False
            encoded = b""
            try:
                encoded = content.encode("utf-8", "strict")
            except UnicodeEncodeError:
                encoding_failed = True
            if encoding_failed or len(encoded) > MAX_MARKDOWN_BODY_BYTES:
                _fail("invalid_snapshot")
            supplied_hash = _sha(self.content_hash, "invalid_snapshot")
            request = _copy_request(self.request)
            evidence = _copy_evidence(self.evidence)
            locator = request.body_locator
            if (supplied_hash != content_hash(content)
                    or logical != locator.logical_ref or logical != evidence.logical_ref
                    or locator.source_id != evidence.source_id
                    or locator.source_version_id != evidence.source_version_id
                    or locator.body_sha256 != evidence.body_sha256
                    or locator.byte_count != evidence.byte_count
                    or locator.descriptor_version_digest != evidence.descriptor_version_digest
                    or locator.markdown_snapshot_digest != evidence.markdown_snapshot_digest
                    or locator.binding_digest != evidence.binding_digest
                    or request.text_range.end_char - request.text_range.start_char != len(content)):
                _fail("invalid_snapshot")
            object.__setattr__(self, "logical_ref", logical)
            object.__setattr__(self, "request", request)
            object.__setattr__(self, "evidence", evidence)
        except PlanningMarkdownSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "invalid_snapshot"
        if failure is not None:
            _fail(failure)

    def __repr__(self) -> str:
        return f"PlanningMarkdownExactRead(logical_ref={self.logical_ref!r}, request_digest={self.request.request_digest!r})"


def _exact_keys(value: object, keys: frozenset[str]) -> bool:
    return type(value) is dict and all(type(key) is str for key in value) and frozenset(value) == keys


def _capture_descriptor(value: object, owner: str) -> _CapturedDescriptor:
    if not _exact_keys(value, _DESCRIPTOR_KEYS):
        _fail("invalid_snapshot")
    assert type(value) is dict
    string_fields = _DESCRIPTOR_KEYS - {"approved_revision", "byte_count", "body_exact", "canonical_truth"}
    if (any(type(value[field]) is not str for field in string_fields)
            or type(value["approved_revision"]) is not int
            or type(value["byte_count"]) is not int
            or type(value["body_exact"]) is not bool
            or type(value["canonical_truth"]) is not bool):
        _fail("invalid_snapshot")
    if (value["schema_id"] != OWNER_MARKDOWN_DESCRIPTOR_SCHEMA_ID
            or value["owner_ref"] != owner
            or value["classification"] != "private"
            or value["content_policy"] != "inline_local"
            or value["owner_policy_ref"] != OWNER_MARKDOWN_POLICY_REF
            or value["body_exact"] is not True or value["canonical_truth"] is not False):
        _fail("invalid_snapshot")
    project = _identifier(value["project_id"], "invalid_snapshot")
    roadmap = _identifier(value["roadmap_id"], "invalid_snapshot")
    revision = value["approved_revision"]
    byte_count = value["byte_count"]
    if type(revision) is not int or not 1 <= revision <= 2**63 - 1:
        _fail("invalid_snapshot")
    if type(byte_count) is not int or byte_count < 0:
        _fail("invalid_snapshot")
    if byte_count > MAX_MARKDOWN_BODY_BYTES:
        _fail("body_limit_exceeded")
    domain_hash = _sha(value["domain_content_hash"], "invalid_snapshot")
    projection_hash = _sha(value["canonical_projection_hash"], "invalid_snapshot")
    planning_ref = _repo_ref(value["planning_source_ref"], "invalid_snapshot", markdown=False)
    planning_snapshot = _sha(value["planning_snapshot_digest"], "invalid_snapshot")
    selection = _sha(value["selection_digest"], "invalid_snapshot")
    source_ref = _repo_ref(value["source_ref"], "invalid_snapshot", markdown=True)
    body_hash = _sha(value["body_sha256"], "invalid_snapshot")
    version_digest = _sha(value["version_digest"], "invalid_snapshot")
    base = {key: value[key] for key in value if key != "version_digest"}
    if version_digest != _digest(base):
        _fail("invalid_snapshot")
    logical = _logical_ref(owner, project, roadmap, source_ref)
    return _CapturedDescriptor(
        owner, project, roadmap, revision, domain_hash, projection_hash,
        planning_ref, planning_snapshot, selection, source_ref, body_hash,
        byte_count, version_digest, logical,
    )


def _capture_snapshot(value: object, owner: str) -> _CapturedSnapshot:
    if not _exact_keys(value, _CAPTURE_KEYS):
        _fail("invalid_snapshot")
    assert type(value) is dict
    if (type(value["schema_id"]) is not str or type(value["owner_ref"]) is not str
            or type(value["selection_mode"]) is not str or type(value["descriptors"]) is not list
            or type(value["eligible_count"]) is not int or type(value["classification"]) is not str
            or type(value["content_policy"]) is not str or type(value["owner_policy_ref"]) is not str
            or type(value["body_exact"]) is not bool or type(value["canonical_truth"]) is not bool
            or type(value["read_only"]) is not bool or type(value["writes_performed"]) is not bool):
        _fail("invalid_snapshot")
    if len(value["descriptors"]) > MAX_PLANNING_MARKDOWN_SOURCES:
        _fail("source_limit_exceeded")
    snapshot_digest = _sha(value["snapshot_digest"], "invalid_snapshot")
    selection = _sha(value["selection_digest"], "invalid_snapshot")
    planning_snapshot = _sha(value["planning_snapshot_digest"], "invalid_snapshot")
    if (value["schema_id"] != OWNER_MARKDOWN_CAPTURE_SCHEMA_ID or value["owner_ref"] != owner
            or value["selection_mode"] != "explicit_approved_ref_and_expected_bytes"
            or value["eligible_count"] != len(value["descriptors"])
            or value["classification"] != "private" or value["content_policy"] != "inline_local"
            or value["owner_policy_ref"] != OWNER_MARKDOWN_POLICY_REF
            or value["body_exact"] is not True or value["canonical_truth"] is not False
            or value["read_only"] is not True or value["writes_performed"] is not False):
        _fail("invalid_snapshot")
    # Validate and detach every nested row before any canonical serializer can
    # observe caller-owned mapping behavior.
    descriptors = tuple(_capture_descriptor(item, owner) for item in value["descriptors"])
    expected_order = tuple(sorted(descriptors, key=lambda item: (item.project_id, item.roadmap_id, item.source_ref)))
    logical_refs = tuple(item.logical_ref for item in descriptors)
    if (descriptors != expected_order or len(set(logical_refs)) != len(logical_refs)
            or any(item.selection_digest != selection
                   or item.planning_snapshot_digest != planning_snapshot for item in descriptors)):
        _fail("invalid_snapshot")
    detached_base = {
        "schema_id": OWNER_MARKDOWN_CAPTURE_SCHEMA_ID,
        "owner_ref": owner,
        "selection_mode": "explicit_approved_ref_and_expected_bytes",
        "selection_digest": selection,
        "planning_snapshot_digest": planning_snapshot,
        "descriptors": [_descriptor_payload(item) for item in descriptors],
        "eligible_count": len(descriptors),
        "classification": "private",
        "content_policy": "inline_local",
        "owner_policy_ref": OWNER_MARKDOWN_POLICY_REF,
        "body_exact": True,
        "canonical_truth": False,
        "read_only": True,
        "writes_performed": False,
    }
    if snapshot_digest != _digest(detached_base):
        _fail("invalid_snapshot")
    detached_capture = {**detached_base, "snapshot_digest": snapshot_digest}
    if len(_canonical_bytes(detached_capture)) > MAX_MARKDOWN_METADATA_BYTES:
        _fail("body_limit_exceeded")
    return _CapturedSnapshot(owner, selection, planning_snapshot, descriptors, snapshot_digest)


def _descriptor_payload(item: _CapturedDescriptor) -> dict[str, object]:
    return {
        "schema_id": OWNER_MARKDOWN_DESCRIPTOR_SCHEMA_ID,
        "owner_ref": item.owner_ref, "project_id": item.project_id,
        "roadmap_id": item.roadmap_id, "approved_revision": item.approved_revision,
        "domain_content_hash": item.domain_content_hash,
        "canonical_projection_hash": item.canonical_projection_hash,
        "planning_source_ref": item.planning_source_ref,
        "planning_snapshot_digest": item.planning_snapshot_digest,
        "selection_digest": item.selection_digest, "source_ref": item.source_ref,
        "body_sha256": item.body_sha256, "byte_count": item.byte_count,
        "classification": "private", "content_policy": "inline_local",
        "owner_policy_ref": OWNER_MARKDOWN_POLICY_REF, "body_exact": True,
        "canonical_truth": False, "version_digest": item.version_digest,
    }


def _capture_read(value: object, snapshot: _CapturedSnapshot, item: _CapturedDescriptor) -> str:
    if not _exact_keys(value, _READ_KEYS):
        _fail("invalid_snapshot")
    assert type(value) is dict
    if (type(value["schema_id"]) is not str or type(value["owner_ref"]) is not str
            or type(value["snapshot_digest"]) is not str or type(value["selection_digest"]) is not str
            or type(value["planning_snapshot_digest"]) is not str or type(value["content"]) is not str
            or type(value["body_sha256"]) is not str or type(value["byte_count"]) is not int
            or type(value["classification"]) is not str or type(value["content_policy"]) is not str
            or type(value["owner_policy_ref"]) is not str or type(value["body_exact"]) is not bool
            or type(value["canonical_truth"]) is not bool or type(value["read_only"]) is not bool
            or type(value["writes_performed"]) is not bool):
        _fail("invalid_snapshot")
    descriptor = _capture_descriptor(value["descriptor"], snapshot.owner_ref)
    if (value["schema_id"] != OWNER_MARKDOWN_READ_SCHEMA_ID
            or value["owner_ref"] != snapshot.owner_ref
            or value["snapshot_digest"] != snapshot.snapshot_digest
            or value["selection_digest"] != snapshot.selection_digest
            or value["planning_snapshot_digest"] != snapshot.planning_snapshot_digest
            or descriptor != item or value["body_sha256"] != item.body_sha256
            or value["byte_count"] != item.byte_count or value["classification"] != "private"
            or value["content_policy"] != "inline_local"
            or value["owner_policy_ref"] != OWNER_MARKDOWN_POLICY_REF
            or value["body_exact"] is not True or value["canonical_truth"] is not False
            or value["read_only"] is not True or value["writes_performed"] is not False):
        _fail("invalid_snapshot")
    content = value["content"]
    encoding_failed = False
    raw = b""
    try:
        raw = content.encode("utf-8", "strict")
    except UnicodeEncodeError:
        encoding_failed = True
    if encoding_failed or "\x00" in content:
        _fail("invalid_snapshot")
    if len(raw) > MAX_MARKDOWN_BODY_BYTES:
        _fail("body_limit_exceeded")
    if len(raw) != item.byte_count or content_hash(content) != item.body_sha256:
        _fail("invalid_snapshot")
    return content


def _copy_policy(value: object, code: str) -> PolicyEvidence:
    if type(value) is not PolicyEvidence:
        _fail(code)
    if (type(value.parent_kind) is not RecordKind
            or type(value.parent_id) is not str
            or type(value.source_id) is not str
            or type(value.source_version_id) is not str
            or type(value.owner_scope) is not str
            or type(value.classification) is not Classification
            or type(value.content_policy) is not ContentPolicy
            or type(value.evidence_id) is not str):
        _fail(code)
    failure = False
    copied: PolicyEvidence | None = None
    try:
        copied = PolicyEvidence(
            value.parent_kind, value.parent_id, value.source_id,
            value.source_version_id, value.owner_scope, value.classification,
            value.content_policy, value.evidence_id,
        )
    except Exception:
        failure = True
    if failure or copied is None:
        _fail(code)
    return copied


def _copy_source(value: object, code: str) -> SourceRecord:
    if type(value) is not SourceRecord:
        _fail(code)
    if (type(value.owner_scope) is not str
            or type(value.source_kind) is not SourceKind
            or type(value.canonical_ref) is not str
            or type(value.classification) is not Classification
            or type(value.content_policy) is not ContentPolicy
            or type(value.provider_ref) is not str
            or type(value.source_created_at) is not str
            or type(value.first_seen_at) is not str
            or type(value.source_modified_at) is not str
            or type(value.valid_from) is not str
            or type(value.valid_to) is not str
            or type(value.source_id) is not str):
        _fail(code)
    failure = False
    copied: SourceRecord | None = None
    try:
        copied = SourceRecord(
            owner_scope=value.owner_scope,
            source_kind=value.source_kind,
            canonical_ref=value.canonical_ref,
            classification=value.classification,
            content_policy=value.content_policy,
            provider_ref=value.provider_ref,
            source_created_at=value.source_created_at,
            first_seen_at=value.first_seen_at,
            source_modified_at=value.source_modified_at,
            valid_from=value.valid_from,
            valid_to=value.valid_to,
            source_id=value.source_id,
        )
    except Exception:
        failure = True
    if failure or copied is None:
        _fail(code)
    return copied


def _copy_version(value: object, code: str) -> SourceVersionRecord:
    if type(value) is not SourceVersionRecord:
        _fail(code)
    if (type(value.source_id) is not str
            or type(value.revision_ref) is not str
            or type(value.content_hash) is not str
            or type(value.provider_ref) is not str
            or type(value.version_observed_at) is not str
            or type(value.owner_scope) is not str
            or type(value.classification) is not Classification
            or type(value.content_policy) is not ContentPolicy
            or type(value.policy_evidence) is not PolicyEvidence
            or type(value.source_created_at) is not str
            or type(value.first_seen_at) is not str
            or type(value.source_modified_at) is not str
            or type(value.indexed_at) is not str
            or type(value.valid_from) is not str
            or type(value.valid_to) is not str
            or type(value.source_version_id) is not str):
        _fail(code)
    policy = _copy_policy(value.policy_evidence, code)
    failure = False
    copied: SourceVersionRecord | None = None
    try:
        copied = SourceVersionRecord(
            source_id=value.source_id,
            revision_ref=value.revision_ref,
            content_hash=value.content_hash,
            provider_ref=value.provider_ref,
            version_observed_at=value.version_observed_at,
            owner_scope=value.owner_scope,
            classification=value.classification,
            content_policy=value.content_policy,
            policy_evidence=policy,
            source_created_at=value.source_created_at,
            first_seen_at=value.first_seen_at,
            source_modified_at=value.source_modified_at,
            indexed_at=value.indexed_at,
            valid_from=value.valid_from,
            valid_to=value.valid_to,
            source_version_id=value.source_version_id,
        )
    except Exception:
        failure = True
    if failure or copied is None:
        _fail(code)
    return copied


def _copy_chunk(value: object, code: str) -> ChunkRecord:
    if type(value) is not ChunkRecord:
        _fail(code)
    if (type(value.source_id) is not str
            or type(value.source_version_id) is not str
            or type(value.locator) is not TextRangeLocator
            or type(value.extractor_profile_ref) is not str
            or type(value.content_hash) is not str
            or type(value.owner_scope) is not str
            or type(value.classification) is not Classification
            or type(value.content_policy) is not ContentPolicy
            or type(value.policy_evidence) is not PolicyEvidence
            or (value.content is not None and type(value.content) is not str)
            or type(value.indexed_at) is not str
            or type(value.valid_from) is not str
            or type(value.valid_to) is not str
            or type(value.chunk_id) is not str):
        _fail(code)
    locator = _copy_text_range(value.locator, code)
    policy = _copy_policy(value.policy_evidence, code)
    failure = False
    copied: ChunkRecord | None = None
    try:
        copied = ChunkRecord(
            source_id=value.source_id,
            source_version_id=value.source_version_id,
            locator=locator,
            extractor_profile_ref=value.extractor_profile_ref,
            content_hash=value.content_hash,
            owner_scope=value.owner_scope,
            classification=value.classification,
            content_policy=value.content_policy,
            policy_evidence=policy,
            content=value.content,
            indexed_at=value.indexed_at,
            valid_from=value.valid_from,
            valid_to=value.valid_to,
            chunk_id=value.chunk_id,
        )
    except Exception:
        failure = True
    if failure or copied is None:
        _fail(code)
    return copied


def _copy_text_range(value: object, code: str) -> TextRangeLocator:
    failure = False
    copied: TextRangeLocator | None = None
    try:
        if (type(value) is not TextRangeLocator or type(value.start_char) is not int
                or type(value.end_char) is not int or value.start_char < 0
                or value.end_char <= value.start_char):
            failure = True
        else:
            copied = TextRangeLocator(value.start_char, value.end_char)
    except Exception:
        failure = True
    if failure or copied is None:
        _fail(code)
    return copied


def _copy_locator(value: object) -> PlanningMarkdownBodyLocator:
    if type(value) is not PlanningMarkdownBodyLocator:
        _fail("invalid_request")
    return PlanningMarkdownBodyLocator(
        value.logical_ref, value.source_id, value.source_version_id,
        value.body_sha256, value.byte_count, value.descriptor_version_digest,
        value.markdown_snapshot_digest, value.binding_digest,
        value.locator_digest, value.schema,
    )


def _copy_request(value: object) -> PlanningMarkdownCharacterRangeRequest:
    if type(value) is not PlanningMarkdownCharacterRangeRequest:
        _fail("invalid_request")
    return PlanningMarkdownCharacterRangeRequest(
        value.body_locator, value.text_range, value.request_digest, value.schema
    )


def _copy_evidence(value: object) -> PlanningMarkdownSourceEvidence:
    if type(value) is not PlanningMarkdownSourceEvidence:
        _fail("invalid_snapshot")
    return PlanningMarkdownSourceEvidence(
        value.logical_ref, value.owner_scope, value.owner_ref, value.project_id,
        value.roadmap_id, value.approved_revision, value.domain_content_hash,
        value.canonical_projection_hash, value.planning_source_ref,
        value.planning_snapshot_digest, value.selection_digest, value.source_ref,
        value.body_sha256, value.byte_count, value.descriptor_version_digest,
        value.markdown_snapshot_digest, value.binding_digest, value.revision_digest,
        value.owner_policy_ref, value.source_id, value.source_version_id, value.schema,
    )


def _copy_descriptor(value: object) -> PlanningMarkdownSourceDescriptor:
    if type(value) is not PlanningMarkdownSourceDescriptor:
        _fail("invalid_snapshot")
    return PlanningMarkdownSourceDescriptor(
        value.source, value.source_version, value.body_locator, value.evidence
    )


def _binding_from_fields(fields: tuple[str, ...]) -> PlanningMarkdownSourceAuthorityBinding:
    if type(fields) is not tuple or len(fields) != 7:
        _fail("invalid_binding")
    failure = False
    scope: OwnerScope | None = None
    try:
        scope = OwnerScope(fields[0])
    except Exception:
        failure = True
    if failure or scope is None:
        _fail("invalid_binding")
    return PlanningMarkdownSourceAuthorityBinding(
        scope, fields[1], fields[2], fields[3], fields[4], fields[5], fields[6]
    )


def _descriptor(binding: PlanningMarkdownSourceAuthorityBinding,
                item: _CapturedDescriptor, observed_at: str) -> PlanningMarkdownSourceDescriptor:
    source = SourceRecord(
        owner_scope=binding.owner_scope.value,
        source_kind=SourceKind.PLANNING,
        canonical_ref=item.logical_ref,
        classification=Classification.PRIVATE,
        content_policy=ContentPolicy.INLINE_LOCAL,
        provider_ref=PLANNING_MARKDOWN_SOURCE_ADAPTER_ID,
    )
    revision_digest = _revision_digest(item, binding.snapshot_digest)
    revision_ref = f"approved-markdown:{item.approved_revision}:{revision_digest}"
    version = SourceVersionRecord.create(
        source, revision_ref=revision_ref, content_hash=item.body_sha256,
        version_observed_at=observed_at, classification=Classification.PRIVATE,
        content_policy=ContentPolicy.INLINE_LOCAL,
    )
    locator = PlanningMarkdownBodyLocator(
        logical_ref=item.logical_ref, source_id=source.source_id,
        source_version_id=version.source_version_id, body_sha256=item.body_sha256,
        byte_count=item.byte_count, descriptor_version_digest=item.version_digest,
        markdown_snapshot_digest=binding.snapshot_digest,
        binding_digest=binding.binding_digest,
    )
    evidence = PlanningMarkdownSourceEvidence(
        logical_ref=item.logical_ref, owner_scope=binding.owner_scope.value,
        owner_ref=item.owner_ref, project_id=item.project_id, roadmap_id=item.roadmap_id,
        approved_revision=item.approved_revision, domain_content_hash=item.domain_content_hash,
        canonical_projection_hash=item.canonical_projection_hash,
        planning_source_ref=item.planning_source_ref,
        planning_snapshot_digest=item.planning_snapshot_digest,
        selection_digest=item.selection_digest, source_ref=item.source_ref,
        body_sha256=item.body_sha256, byte_count=item.byte_count,
        descriptor_version_digest=item.version_digest,
        markdown_snapshot_digest=binding.snapshot_digest,
        binding_digest=binding.binding_digest, revision_digest=revision_digest,
        owner_policy_ref=OWNER_MARKDOWN_POLICY_REF, source_id=source.source_id,
        source_version_id=version.source_version_id,
    )
    return PlanningMarkdownSourceDescriptor(source, version, locator, evidence)


def _descriptor_by_ref(snapshot: _CapturedSnapshot, logical_ref: str) -> _CapturedDescriptor:
    matches = tuple(item for item in snapshot.descriptors if item.logical_ref == logical_ref)
    if len(matches) != 1:
        _fail("record_not_found")
    return matches[0]



def _captured_snapshot_payload(snapshot: _CapturedSnapshot) -> dict[str, object]:
    return {
        "schema_id": OWNER_MARKDOWN_CAPTURE_SCHEMA_ID,
        "owner_ref": snapshot.owner_ref,
        "selection_mode": "explicit_approved_ref_and_expected_bytes",
        "selection_digest": snapshot.selection_digest,
        "planning_snapshot_digest": snapshot.planning_snapshot_digest,
        "descriptors": [_descriptor_payload(item) for item in snapshot.descriptors],
        "eligible_count": len(snapshot.descriptors),
        "classification": "private",
        "content_policy": "inline_local",
        "owner_policy_ref": OWNER_MARKDOWN_POLICY_REF,
        "body_exact": True,
        "canonical_truth": False,
        "read_only": True,
        "writes_performed": False,
        "snapshot_digest": snapshot.snapshot_digest,
    }


def _validated_adapter_context(value: object) -> _AdapterContext:
    failure = False
    result: _AdapterContext | None = None
    try:
        if (
            type(value) is not _AdapterContext
            or type(value.service) is not PlanningMcpService
            or type(value.owner_ref) is not str
            or type(value.observed_at) is not str
            or type(value.binding_fields) is not tuple
            or len(value.binding_fields) != 7
            or any(type(item) is not str for item in value.binding_fields)
            or not _captured_snapshot_is_primitive(value.snapshot)
        ):
            _fail("stale_authority")
        owner = _owner_ref(value.owner_ref, "stale_authority")
        observed = _timestamp(value.observed_at, "stale_authority")
        fields = tuple(value.binding_fields)
        scope = _exact_text(fields[0], 64, "stale_authority")
        snapshot = _detached_context_snapshot(value.snapshot)
        rebuilt_snapshot = _capture_snapshot(
            _captured_snapshot_payload(snapshot), owner
        )
        expected_binding = _authority_binding_digest(
            scope, owner, snapshot.snapshot_digest
        )
        if (
            rebuilt_snapshot != snapshot
            or fields[1] != owner
            or fields[2] != snapshot.snapshot_digest
            or fields[3] != PLANNING_MARKDOWN_SOURCE_ADAPTER_ID
            or fields[4] != PLANNING_MARKDOWN_SOURCE_ADAPTER_VERSION
            or fields[5] != PLANNING_MARKDOWN_SOURCE_ADAPTER_GENERATION
            or fields[6] != expected_binding
        ):
            _fail("stale_authority")
        result = _AdapterContext(
            value.service, owner, observed, fields, snapshot
        )
    except Exception:
        failure = True
    if failure or result is None:
        _fail("stale_authority")
    return result


def _same_adapter_context(left: _AdapterContext, right: _AdapterContext) -> bool:
    return (
        left.service is right.service
        and left.owner_ref == right.owner_ref
        and left.observed_at == right.observed_at
        and left.binding_fields == right.binding_fields
        and left.snapshot == right.snapshot
    )


class PlanningMarkdownSourceAdapter:
    """Read-only internal adapter over one approved owner-Markdown snapshot."""

    __slots__ = (
        "_service", "_bound_service", "_expected_owner_ref", "_observed_at",
        "_snapshot", "_binding_fields", "_context",
    )

    def __init__(self, *, service: PlanningMcpService, owner_scope: OwnerScope,
                 expected_owner_ref: str, observed_at: str) -> None:
        failure: str | None = None
        try:
            if type(service) is not PlanningMcpService or type(owner_scope) is not OwnerScope:
                _fail("invalid_authority")
            scope = OwnerScope(_exact_text(owner_scope.value, 64, "invalid_authority"))
            owner = _owner_ref(expected_owner_ref, "invalid_authority")
            observed = _timestamp(observed_at, "invalid_authority")
            service_object = service
            raw = service_object.capture_owner_markdown_snapshot(owner)
            if service is not service_object or type(service_object) is not PlanningMcpService:
                _fail("invalid_authority")
            snapshot = _capture_snapshot(raw, owner)
            fields = (
                scope.value, owner, snapshot.snapshot_digest,
                PLANNING_MARKDOWN_SOURCE_ADAPTER_ID,
                PLANNING_MARKDOWN_SOURCE_ADAPTER_VERSION,
                PLANNING_MARKDOWN_SOURCE_ADAPTER_GENERATION,
            )
            binding = PlanningMarkdownSourceAuthorityBinding(
                owner_scope=scope, owner_ref=owner,
                snapshot_digest=snapshot.snapshot_digest, adapter_id=fields[3],
                adapter_version=fields[4], adapter_generation=fields[5],
            )
            self._service = service_object
            self._bound_service = service_object
            self._expected_owner_ref = owner
            self._observed_at = observed
            self._snapshot = snapshot
            self._binding_fields = fields + (binding.binding_digest,)
            self._context = _AdapterContext(
                service_object,
                owner,
                observed,
                tuple(self._binding_fields),
                _detached_context_snapshot(snapshot),
            )
            self._pin_context()
        except PlanningMarkdownSourceAdapterError as exc:
            failure = exc.code if exc.code in {"source_limit_exceeded", "body_limit_exceeded"} else "invalid_authority"
        except PlanningServiceError:
            failure = "invalid_authority"
        except Exception:
            failure = "planning_markdown_source_adapter_failed"
        if failure is not None:
            _fail(failure)

    def _current_context(self) -> _AdapterContext:
        failure = False
        retained: _AdapterContext | None = None
        stored: _AdapterContext | None = None
        try:
            service = object.__getattribute__(self, "_service")
            bound_service = object.__getattribute__(self, "_bound_service")
            owner = object.__getattribute__(self, "_expected_owner_ref")
            observed = object.__getattribute__(self, "_observed_at")
            binding_fields = object.__getattribute__(self, "_binding_fields")
            snapshot = object.__getattribute__(self, "_snapshot")
            context = object.__getattribute__(self, "_context")
            if (
                type(service) is not PlanningMcpService
                or type(bound_service) is not PlanningMcpService
                or service is not bound_service
            ):
                _fail("stale_authority")
            retained = _validated_adapter_context(
                _AdapterContext(service, owner, observed, binding_fields, snapshot)
            )
            stored = _validated_adapter_context(context)
        except Exception:
            failure = True
        if (
            failure
            or retained is None
            or stored is None
            or not _same_adapter_context(retained, stored)
        ):
            _fail("stale_authority")
        return retained

    def _pin_context(self) -> _AdapterContext:
        current = self._current_context()
        return _AdapterContext(
            current.service,
            current.owner_ref,
            current.observed_at,
            tuple(current.binding_fields),
            _detached_context_snapshot(current.snapshot),
        )

    def _assert_context(self, pin: _AdapterContext) -> None:
        expected = _validated_adapter_context(pin)
        current = self._current_context()
        if not _same_adapter_context(current, expected):
            _fail("stale_authority")

    def _checked(self, pin: _AdapterContext, value: object):
        self._assert_context(pin)
        return value

    @staticmethod
    def logical_ref(owner_ref: str, project_id: str, roadmap_id: str, source_ref: str) -> str:
        failure: str | None = None
        try:
            return _logical_ref(
                _owner_ref(owner_ref, "invalid_request"),
                _identifier(project_id, "invalid_request"),
                _identifier(roadmap_id, "invalid_request"),
                _repo_ref(source_ref, "invalid_request", markdown=True),
            )
        except PlanningMarkdownSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "invalid_request"
        if failure is not None:
            _fail(failure)

    @property
    def binding(self) -> PlanningMarkdownSourceAuthorityBinding:
        pin = self._pin_context()
        failure: str | None = None
        try:
            return self._checked(pin, _binding_from_fields(pin.binding_fields))
        except PlanningMarkdownSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "invalid_binding"
        if failure is not None:
            _fail(failure)

    def discover(self, *, expected_binding_digest: str, expected_snapshot_digest: str,
                 cursor: int = 0,
                 limit: int = MAX_PLANNING_MARKDOWN_SOURCES) -> PlanningMarkdownDiscoveryPage:
        pin = self._pin_context()
        failure: str | None = None
        try:
            if (type(cursor) is not int or cursor < 0 or cursor > MAX_PLANNING_MARKDOWN_SOURCES
                    or cursor > len(pin.snapshot.descriptors) or type(limit) is not int
                    or not 1 <= limit <= MAX_PLANNING_MARKDOWN_SOURCES):
                _fail("invalid_request")
            binding, snapshot = self._operation_capture(
                pin, expected_binding_digest, expected_snapshot_digest
            )
            if cursor > len(snapshot.descriptors):
                _fail("invalid_request")
            selected = snapshot.descriptors[cursor : cursor + limit]
            items = tuple(_descriptor(binding, item, pin.observed_at) for item in selected)
            end = cursor + len(selected)
            return self._checked(pin, PlanningMarkdownDiscoveryPage(
                items, end if end < len(snapshot.descriptors) else None,
                snapshot.snapshot_digest, binding.binding_digest,
            ))
        except PlanningMarkdownSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "planning_markdown_source_adapter_failed"
        if failure is not None:
            _fail(failure)

    def observe_version(self, logical_ref: str, *, expected_binding_digest: str,
                        expected_snapshot_digest: str) -> PlanningMarkdownSourceDescriptor:
        pin = self._pin_context()
        failure: str | None = None
        try:
            ref = _logical_ref_value(logical_ref, "invalid_request")
            binding, snapshot = self._operation_capture(
                pin, expected_binding_digest, expected_snapshot_digest
            )
            return self._checked(
                pin, _descriptor(binding, _descriptor_by_ref(snapshot, ref), pin.observed_at)
            )
        except PlanningMarkdownSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "planning_markdown_source_adapter_failed"
        if failure is not None:
            _fail(failure)

    def extract(self, logical_ref: str, *, expected_binding_digest: str,
                expected_snapshot_digest: str) -> PlanningMarkdownSourceOccurrence:
        pin = self._pin_context()
        failure: str | None = None
        try:
            ref = _logical_ref_value(logical_ref, "invalid_request")
            binding, snapshot = self._operation_capture(
                pin, expected_binding_digest, expected_snapshot_digest
            )
            item = _descriptor_by_ref(snapshot, ref)
            descriptor = _descriptor(binding, item, pin.observed_at)
            body = self._read_body(pin, item)
            ranges = split_text_into_exact_character_ranges(
                body, max_chars=MAX_TEXT_CHARS, max_ranges=MAX_PLANNING_MARKDOWN_CHUNKS
            )
            if (len(ranges) > MAX_PLANNING_MARKDOWN_CHUNKS or (body and not ranges)
                    or (not body and ranges)
                    or (ranges and (ranges[0].char_start != 0
                                    or ranges[-1].char_end != len(body)
                                    or any(left.char_end != right.char_start
                                           for left, right in zip(ranges, ranges[1:]))))):
                _fail("invalid_snapshot")
            chunks = tuple(
                ChunkRecord.create(
                    descriptor.source_version,
                    locator=TextRangeLocator(rng.char_start, rng.char_end),
                    extractor_profile_ref=PLANNING_MARKDOWN_SOURCE_EXTRACTOR_PROFILE,
                    content_hash=content_hash(body[rng.char_start : rng.char_end]),
                    content=body[rng.char_start : rng.char_end],
                    classification=Classification.PRIVATE,
                    content_policy=ContentPolicy.INLINE_LOCAL,
                )
                for rng in ranges
            )
            return self._checked(pin, PlanningMarkdownSourceOccurrence(
                descriptor.source, descriptor.source_version, chunks, (), (),
                descriptor.body_locator, descriptor.evidence,
                EXACT_CHARACTER_SPLITTER_VERSION,
            ))
        except PlanningMarkdownSourceAdapterError as exc:
            failure = exc.code
        except ValueError:
            failure = "body_limit_exceeded"
        except Exception:
            failure = "planning_markdown_source_adapter_failed"
        if failure is not None:
            _fail(failure)

    def read_exact(self, request: PlanningMarkdownCharacterRangeRequest, *,
                   expected_binding_digest: str,
                   expected_snapshot_digest: str) -> PlanningMarkdownExactRead:
        pin = self._pin_context()
        failure: str | None = None
        try:
            request_copy = _copy_request(request)
            binding, snapshot = self._operation_capture(
                pin, expected_binding_digest, expected_snapshot_digest
            )
            item = _descriptor_by_ref(snapshot, request_copy.body_locator.logical_ref)
            descriptor = _descriptor(binding, item, pin.observed_at)
            if request_copy.body_locator != descriptor.body_locator:
                _fail("invalid_request")
            body = self._read_body(pin, item)
            start = request_copy.text_range.start_char
            end = request_copy.text_range.end_char
            if end > len(body):
                _fail("invalid_request")
            selected = body[start:end]
            return self._checked(pin, PlanningMarkdownExactRead(
                item.logical_ref, selected, content_hash(selected),
                request_copy, descriptor.evidence,
            ))
        except PlanningMarkdownSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "planning_markdown_source_adapter_failed"
        if failure is not None:
            _fail(failure)

    def observe_unavailable(self, logical_ref: str, *, expected_binding_digest: str,
                            expected_snapshot_digest: str) -> None:
        pin = self._pin_context()
        failure: str | None = None
        try:
            _logical_ref_value(logical_ref, "invalid_request")
            self._operation_capture(pin, expected_binding_digest, expected_snapshot_digest)
            self._assert_context(pin)
            failure = "unavailable_state_unproven"
        except PlanningMarkdownSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "planning_markdown_source_adapter_failed"
        if failure is not None:
            _fail(failure)

    def _operation_capture(self, pin: _AdapterContext, expected_binding_digest: str,
                           expected_snapshot_digest: str
                           ) -> tuple[PlanningMarkdownSourceAuthorityBinding, _CapturedSnapshot]:
        self._assert_context(pin)
        expected_binding = _sha(expected_binding_digest, "invalid_request")
        expected_snapshot = _sha(expected_snapshot_digest, "invalid_request")
        binding = _binding_from_fields(pin.binding_fields)
        if expected_binding != binding.binding_digest or expected_snapshot != binding.snapshot_digest:
            _fail("stale_authority")
        self._assert_context(pin)
        service_failure: str | None = None
        raw: object = None
        try:
            raw = pin.service.capture_owner_markdown_snapshot(pin.owner_ref)
        except PlanningServiceError as exc:
            if type(exc) is not PlanningServiceError or type(exc.code) is not str:
                service_failure = "planning_markdown_source_adapter_failed"
            elif exc.code == "owner_markdown_limit_exceeded":
                service_failure = "body_limit_exceeded"
            else:
                service_failure = "stale_authority"
        except Exception:
            service_failure = "stale_authority"
        self._assert_context(pin)
        if service_failure is not None:
            _fail(service_failure)
        snapshot_failure = False
        snapshot: _CapturedSnapshot | None = None
        try:
            snapshot = _capture_snapshot(raw, pin.owner_ref)
        except PlanningMarkdownSourceAdapterError:
            snapshot_failure = True
        if snapshot_failure or snapshot is None:
            _fail("stale_authority")
        if snapshot != pin.snapshot or snapshot.snapshot_digest != expected_snapshot:
            _fail("stale_authority")
        self._assert_context(pin)
        return binding, pin.snapshot

    def _read_body(self, pin: _AdapterContext, item: _CapturedDescriptor) -> str:
        self._assert_context(pin)
        snapshot = pin.snapshot
        if not _captured_snapshot_is_primitive(snapshot) or not _captured_descriptor_is_primitive(item):
            _fail("invalid_snapshot")
        service_failure: str | None = None
        raw: object = None
        self._assert_context(pin)
        try:
            raw = pin.service.read_owner_markdown_body(
                _descriptor_payload(item), expected_owner_ref=pin.owner_ref,
                expected_snapshot_digest=snapshot.snapshot_digest,
            )
        except PlanningServiceError as exc:
            if type(exc) is not PlanningServiceError or type(exc.code) is not str:
                service_failure = "planning_markdown_source_adapter_failed"
            elif exc.code == "owner_markdown_limit_exceeded":
                service_failure = "body_limit_exceeded"
            else:
                service_failure = "stale_authority"
        except Exception:
            service_failure = "planning_markdown_source_adapter_failed"
        self._assert_context(pin)
        if service_failure is not None:
            _fail(service_failure)
        content = _capture_read(raw, snapshot, item)
        self._assert_context(pin)
        return content


__all__ = [
    "MAX_MARKDOWN_BODY_BYTES",
    "MAX_PLANNING_MARKDOWN_CHUNKS",
    "MAX_PLANNING_MARKDOWN_SOURCES",
    "PLANNING_MARKDOWN_BODY_LOCATOR_SCHEMA",
    "PLANNING_MARKDOWN_CHARACTER_RANGE_REQUEST_SCHEMA",
    "PLANNING_MARKDOWN_SOURCE_ADAPTER_GENERATION",
    "PLANNING_MARKDOWN_SOURCE_ADAPTER_ID",
    "PLANNING_MARKDOWN_SOURCE_ADAPTER_VERSION",
    "PLANNING_MARKDOWN_SOURCE_BINDING_SCHEMA",
    "PLANNING_MARKDOWN_SOURCE_EVIDENCE_SCHEMA",
    "PLANNING_MARKDOWN_SOURCE_EXACT_READER_BOUNDARY",
    "PLANNING_MARKDOWN_SOURCE_EXTRACTOR_PROFILE",
    "PLANNING_MARKDOWN_SOURCE_IDENTITY_SCHEMA",
    "PLANNING_MARKDOWN_SOURCE_REVISION_SCHEMA",
    "PlanningMarkdownBodyLocator",
    "PlanningMarkdownCharacterRangeRequest",
    "PlanningMarkdownDiscoveryPage",
    "PlanningMarkdownExactRead",
    "PlanningMarkdownSourceAdapter",
    "PlanningMarkdownSourceAdapterError",
    "PlanningMarkdownSourceAuthorityBinding",
    "PlanningMarkdownSourceDescriptor",
    "PlanningMarkdownSourceEvidence",
    "PlanningMarkdownSourceOccurrence",
]
