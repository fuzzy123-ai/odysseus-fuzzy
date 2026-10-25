"""Default-off composite for accepted Planning JSON and Markdown sources."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import TypeAlias

from src.planning_mcp_service import PlanningMcpService
from src.unified_source_index_contract import Classification, ContentPolicy, SourceKind
from src.unified_source_index_owner_scope import OwnerScope
from src.unified_source_index_source_capability import (
    OwnerScopeRequirement, ProviderConstraint, QueryCapability,
    SourceAdapterCapabilityManifest, SourceAdapterOperation,
)
from src.unified_source_index_source_registry import SourceAdapterRegistration
from src.unified_source_index_sources.planning import (
    MAX_DISCOVERY_LIMIT, PLANNING_SOURCE_ADAPTER_ID,
    PlanningCharacterRangeRequest, PlanningExactRead, PlanningSourceAdapter,
    PlanningSourceAdapterError, PlanningSourceAuthorityBinding,
    PlanningDiscoveryPage, PlanningSourceDescriptor, PlanningSourceOccurrence,
    PlanningUnavailableObservation, planning_source_capability_manifest,
)
from src.unified_source_index_sources.planning_markdown import (
    MAX_PLANNING_MARKDOWN_SOURCES, PLANNING_MARKDOWN_SOURCE_ADAPTER_GENERATION,
    PLANNING_MARKDOWN_SOURCE_ADAPTER_ID, PLANNING_MARKDOWN_SOURCE_ADAPTER_VERSION,
    PlanningMarkdownCharacterRangeRequest, PlanningMarkdownExactRead,
    PlanningMarkdownSourceAdapter, PlanningMarkdownSourceAdapterError,
    PlanningMarkdownDiscoveryPage, PlanningMarkdownSourceAuthorityBinding, PlanningMarkdownSourceDescriptor,
    PlanningMarkdownSourceOccurrence,
)

PLANNING_COMPOSITE_SOURCE_ADAPTER_ID = "planning.approved_sources"
PLANNING_COMPOSITE_SOURCE_ADAPTER_VERSION = "v1"
PLANNING_COMPOSITE_SOURCE_DOMAIN_ID = "planning"
PLANNING_COMPOSITE_SOURCE_EXACT_READER_BOUNDARY = "planning.approved_sources.exact_reader"
PLANNING_COMPOSITE_SOURCE_BINDING_SCHEMA = "odysseus.usi.planning_composite_source_authority_binding.v1"
PLANNING_COMPOSITE_SOURCE_SNAPSHOT_SCHEMA = "odysseus.usi.planning_composite_source_snapshot.v1"
PLANNING_COMPOSITE_SOURCE_ROUTE_SCHEMA = "odysseus.usi.planning_composite_source_route.v1"
MAX_PLANNING_COMPOSITE_SOURCES = MAX_DISCOVERY_LIMIT + MAX_PLANNING_MARKDOWN_SOURCES

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ERROR_CODES = frozenset({
    "invalid_authority", "invalid_binding", "invalid_snapshot", "invalid_request",
    "record_not_found", "unavailable_state_unproven", "stale_authority",
    "source_limit_exceeded", "body_limit_exceeded",
    "planning_composite_source_adapter_failed",
})


class PlanningCompositeSourceAdapterError(ValueError):
    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        safe = code if type(code) is str and code in _ERROR_CODES else "planning_composite_source_adapter_failed"
        self.code = safe
        ValueError.__init__(self, safe)

    def __repr__(self) -> str:
        return f"PlanningCompositeSourceAdapterError(code={self.code!r})"


def _fail(code: str) -> None:
    raise PlanningCompositeSourceAdapterError(code) from None


def _text(value: object, maximum: int, code: str) -> str:
    if (type(value) is not str or not value or len(value) > maximum
            or any(char in value for char in ("\x00", "\r", "\n"))):
        _fail(code)
    return value


def _sha(value: object, code: str) -> str:
    value = _text(value, 71, code)
    if _SHA256_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _digest(value: object) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":"), sort_keys=True).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        _fail("invalid_binding")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def planning_composite_source_capability_manifest() -> SourceAdapterCapabilityManifest:
    return SourceAdapterCapabilityManifest(
        adapter_id=PLANNING_COMPOSITE_SOURCE_ADAPTER_ID,
        adapter_version=PLANNING_COMPOSITE_SOURCE_ADAPTER_VERSION,
        domain_id=PLANNING_COMPOSITE_SOURCE_DOMAIN_ID,
        source_kind=SourceKind.PLANNING,
        content_policy=ContentPolicy.INLINE_LOCAL,
        classification_ceiling=Classification.PRIVATE,
        owner_scope_requirement=OwnerScopeRequirement.IMMUTABLE_OPAQUE,
        provider_constraint=ProviderConstraint.LOCAL_ACCEPTED_BOUNDARY,
        query_capability=QueryCapability.EXACT_READER,
        operations=tuple(SourceAdapterOperation),
        exact_reader_boundary=PLANNING_COMPOSITE_SOURCE_EXACT_READER_BOUNDARY,
        productive_default_enabled=False,
    )


def planning_composite_source_registration() -> SourceAdapterRegistration:
    return SourceAdapterRegistration(planning_composite_source_capability_manifest(), factory=None)


_JSON_MANIFEST = planning_source_capability_manifest()
_COMPOSITE_GENERATION = planning_composite_source_capability_manifest().generation_ref


def _copy_json_binding(value: object) -> PlanningSourceAuthorityBinding:
    if type(value) is not PlanningSourceAuthorityBinding:
        _fail("invalid_binding")
    try:
        return PlanningSourceAuthorityBinding(
            value.owner_scope, value.owner_ref, value.snapshot_digest,
            value.adapter_id, value.adapter_version, value.adapter_generation,
            value.binding_digest, value.schema,
        )
    except PlanningSourceAdapterError:
        _fail("invalid_binding")


def _copy_markdown_binding(value: object) -> PlanningMarkdownSourceAuthorityBinding:
    if type(value) is not PlanningMarkdownSourceAuthorityBinding:
        _fail("invalid_binding")
    try:
        return PlanningMarkdownSourceAuthorityBinding(
            value.owner_scope, value.owner_ref, value.snapshot_digest,
            value.adapter_id, value.adapter_version, value.adapter_generation,
            value.binding_digest, value.schema,
        )
    except PlanningMarkdownSourceAdapterError:
        _fail("invalid_binding")


def _children(json_binding, markdown_binding) -> list[dict[str, str]]:
    return [
        {"adapter_id": json_binding.adapter_id,
         "adapter_version": json_binding.adapter_version,
         "adapter_generation": json_binding.adapter_generation,
         "binding_digest": json_binding.binding_digest,
         "snapshot_digest": json_binding.snapshot_digest},
        {"adapter_id": markdown_binding.adapter_id,
         "adapter_version": markdown_binding.adapter_version,
         "adapter_generation": markdown_binding.adapter_generation,
         "binding_digest": markdown_binding.binding_digest,
         "snapshot_digest": markdown_binding.snapshot_digest},
    ]


def _snapshot(scope, owner, json_binding, markdown_binding) -> str:
    return _digest({
        "schema": PLANNING_COMPOSITE_SOURCE_SNAPSHOT_SCHEMA,
        "owner_scope": scope.value, "owner_ref": owner,
        "adapter_id": PLANNING_COMPOSITE_SOURCE_ADAPTER_ID,
        "adapter_version": PLANNING_COMPOSITE_SOURCE_ADAPTER_VERSION,
        "adapter_generation": _COMPOSITE_GENERATION,
        "children": _children(json_binding, markdown_binding),
    })


@dataclass(frozen=True, slots=True, repr=False)
class PlanningCompositeSourceAuthorityBinding:
    owner_scope: OwnerScope
    owner_ref: str
    json_binding: PlanningSourceAuthorityBinding
    markdown_binding: PlanningMarkdownSourceAuthorityBinding
    adapter_id: str = PLANNING_COMPOSITE_SOURCE_ADAPTER_ID
    adapter_version: str = PLANNING_COMPOSITE_SOURCE_ADAPTER_VERSION
    adapter_generation: str = _COMPOSITE_GENERATION
    snapshot_digest: str = ""
    binding_digest: str = ""
    schema: str = PLANNING_COMPOSITE_SOURCE_BINDING_SCHEMA

    def __post_init__(self) -> None:
        failure = None
        try:
            if type(self.owner_scope) is not OwnerScope:
                _fail("invalid_binding")
            scope = OwnerScope(_text(self.owner_scope.value, 64, "invalid_binding"))
            owner = _text(self.owner_ref, 512, "invalid_binding")
            json_binding = _copy_json_binding(self.json_binding)
            markdown_binding = _copy_markdown_binding(self.markdown_binding)
            adapter_id = _text(self.adapter_id, 64, "invalid_binding")
            version = _text(self.adapter_version, 64, "invalid_binding")
            generation = _text(self.adapter_generation, 96, "invalid_binding")
            schema = _text(self.schema, 128, "invalid_binding")
            if (adapter_id != PLANNING_COMPOSITE_SOURCE_ADAPTER_ID
                    or version != PLANNING_COMPOSITE_SOURCE_ADAPTER_VERSION
                    or generation != _COMPOSITE_GENERATION
                    or schema != PLANNING_COMPOSITE_SOURCE_BINDING_SCHEMA
                    or json_binding.owner_scope != scope
                    or markdown_binding.owner_scope != scope
                    or json_binding.owner_ref != owner
                    or markdown_binding.owner_ref != owner
                    or json_binding.adapter_id != PLANNING_SOURCE_ADAPTER_ID
                    or json_binding.adapter_version != _JSON_MANIFEST.adapter_version
                    or json_binding.adapter_generation != _JSON_MANIFEST.generation_ref
                    or markdown_binding.adapter_id != PLANNING_MARKDOWN_SOURCE_ADAPTER_ID
                    or markdown_binding.adapter_version != PLANNING_MARKDOWN_SOURCE_ADAPTER_VERSION
                    or markdown_binding.adapter_generation != PLANNING_MARKDOWN_SOURCE_ADAPTER_GENERATION
                    or type(self.snapshot_digest) is not str
                    or type(self.binding_digest) is not str):
                _fail("invalid_binding")
            snapshot = _snapshot(scope, owner, json_binding, markdown_binding)
            if self.snapshot_digest and self.snapshot_digest != snapshot:
                _fail("invalid_binding")
            expected = _digest({
                "schema": schema, "owner_scope": scope.value, "owner_ref": owner,
                "adapter_id": adapter_id, "adapter_version": version,
                "adapter_generation": generation, "snapshot_digest": snapshot,
                "children": _children(json_binding, markdown_binding),
            })
            if self.binding_digest and self.binding_digest != expected:
                _fail("invalid_binding")
            object.__setattr__(self, "owner_scope", scope)
            object.__setattr__(self, "owner_ref", owner)
            object.__setattr__(self, "json_binding", json_binding)
            object.__setattr__(self, "markdown_binding", markdown_binding)
            object.__setattr__(self, "snapshot_digest", snapshot)
            object.__setattr__(self, "binding_digest", expected)
        except PlanningCompositeSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "invalid_binding"
        if failure is not None:
            _fail(failure)

    def __repr__(self) -> str:
        return f"PlanningCompositeSourceAuthorityBinding(binding_digest={self.binding_digest!r})"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningCompositeSourceRoute:
    child_adapter_id: str
    logical_ref: str
    schema: str = PLANNING_COMPOSITE_SOURCE_ROUTE_SCHEMA

    def __post_init__(self) -> None:
        failure = None
        try:
            child = _text(self.child_adapter_id, 64, "invalid_request")
            logical = _text(self.logical_ref, 2048, "invalid_request")
            schema = _text(self.schema, 128, "invalid_request")
            if (child not in {PLANNING_SOURCE_ADAPTER_ID, PLANNING_MARKDOWN_SOURCE_ADAPTER_ID}
                    or schema != PLANNING_COMPOSITE_SOURCE_ROUTE_SCHEMA):
                _fail("invalid_request")
            object.__setattr__(self, "child_adapter_id", child)
            object.__setattr__(self, "logical_ref", logical)
        except PlanningCompositeSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "invalid_request"
        if failure is not None:
            _fail(failure)

    def __repr__(self) -> str:
        return f"PlanningCompositeSourceRoute(child_adapter_id={self.child_adapter_id!r})"


ChildDescriptor: TypeAlias = PlanningSourceDescriptor | PlanningMarkdownSourceDescriptor
ChildOccurrence: TypeAlias = PlanningSourceOccurrence | PlanningMarkdownSourceOccurrence
ChildExactRead: TypeAlias = PlanningExactRead | PlanningMarkdownExactRead
ChildExactRequest: TypeAlias = PlanningCharacterRangeRequest | PlanningMarkdownCharacterRangeRequest


def _copy_route(value: object) -> PlanningCompositeSourceRoute:
    if type(value) is not PlanningCompositeSourceRoute:
        _fail("invalid_request")
    return PlanningCompositeSourceRoute(value.child_adapter_id, value.logical_ref, value.schema)


def _copy_descriptor(value: object) -> ChildDescriptor:
    try:
        if type(value) is PlanningSourceDescriptor:
            return PlanningSourceDescriptor(
                value.source, value.source_version, value.projection_locator, value.evidence)
        if type(value) is PlanningMarkdownSourceDescriptor:
            return PlanningMarkdownSourceDescriptor(
                value.source, value.source_version, value.body_locator, value.evidence)
    except (PlanningSourceAdapterError, PlanningMarkdownSourceAdapterError):
        _fail("invalid_snapshot")
    _fail("invalid_snapshot")


@dataclass(frozen=True, slots=True, repr=False)
class PlanningCompositeSourceDescriptor:
    route: PlanningCompositeSourceRoute
    descriptor: ChildDescriptor

    def __post_init__(self) -> None:
        failure = None
        try:
            route = _copy_route(self.route)
            descriptor = _copy_descriptor(self.descriptor)
            if (descriptor.source.provider_ref != route.child_adapter_id
                    or descriptor.source.canonical_ref != route.logical_ref
                    or descriptor.evidence.logical_ref != route.logical_ref
                    or (route.child_adapter_id == PLANNING_SOURCE_ADAPTER_ID
                        and type(descriptor) is not PlanningSourceDescriptor)
                    or (route.child_adapter_id == PLANNING_MARKDOWN_SOURCE_ADAPTER_ID
                        and type(descriptor) is not PlanningMarkdownSourceDescriptor)):
                _fail("invalid_snapshot")
            object.__setattr__(self, "route", route)
            object.__setattr__(self, "descriptor", descriptor)
        except PlanningCompositeSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "invalid_snapshot"
        if failure is not None:
            _fail(failure)

    def __repr__(self) -> str:
        return f"PlanningCompositeSourceDescriptor(route={self.route!r})"


def _copy_composite(value: object) -> PlanningCompositeSourceDescriptor:
    if type(value) is not PlanningCompositeSourceDescriptor:
        _fail("invalid_snapshot")
    return PlanningCompositeSourceDescriptor(value.route, value.descriptor)


@dataclass(frozen=True, slots=True, repr=False)
class PlanningCompositeDiscoveryPage:
    items: tuple[PlanningCompositeSourceDescriptor, ...]
    next_cursor: int | None
    snapshot_digest: str
    binding_digest: str

    def __post_init__(self) -> None:
        failure = None
        try:
            if type(self.items) is not tuple or len(self.items) > MAX_PLANNING_COMPOSITE_SOURCES:
                _fail("invalid_snapshot")
            if self.next_cursor is not None and (
                    type(self.next_cursor) is not int
                    or not 0 <= self.next_cursor <= MAX_PLANNING_COMPOSITE_SOURCES):
                _fail("invalid_snapshot")
            items = tuple(_copy_composite(item) for item in self.items)
            route_ids = {(item.route.child_adapter_id, item.route.logical_ref) for item in items}
            source_ids = {item.descriptor.source.source_id for item in items}
            version_ids = {item.descriptor.source_version.source_version_id for item in items}
            owners = {
                (item.descriptor.source.owner_scope, item.descriptor.evidence.owner_ref)
                for item in items
            }
            json_authorities = {
                (item.descriptor.evidence.snapshot_digest,
                 item.descriptor.evidence.binding_digest)
                for item in items
                if type(item.descriptor) is PlanningSourceDescriptor
            }
            markdown_authorities = {
                (item.descriptor.evidence.markdown_snapshot_digest,
                 item.descriptor.evidence.binding_digest)
                for item in items
                if type(item.descriptor) is PlanningMarkdownSourceDescriptor
            }
            if (len(route_ids) != len(items) or len(source_ids) != len(items)
                    or len(version_ids) != len(items) or len(owners) > 1
                    or len(json_authorities) > 1
                    or len(markdown_authorities) > 1):
                _fail("invalid_snapshot")
            object.__setattr__(self, "items", items)
            object.__setattr__(self, "snapshot_digest", _sha(self.snapshot_digest, "invalid_snapshot"))
            object.__setattr__(self, "binding_digest", _sha(self.binding_digest, "invalid_snapshot"))
        except PlanningCompositeSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "invalid_snapshot"
        if failure is not None:
            _fail(failure)

    def __repr__(self) -> str:
        return f"PlanningCompositeDiscoveryPage(item_count={len(self.items)}, next_cursor={self.next_cursor!r})"


@dataclass(frozen=True, slots=True)
class _CompositeContext:
    service: PlanningMcpService
    json_adapter: PlanningSourceAdapter
    markdown_adapter: PlanningMarkdownSourceAdapter
    owner_scope: OwnerScope
    owner_ref: str
    observed_at: str
    binding: PlanningCompositeSourceAuthorityBinding


def _copy_binding(value: object) -> PlanningCompositeSourceAuthorityBinding:
    if type(value) is not PlanningCompositeSourceAuthorityBinding:
        _fail("invalid_binding")
    return PlanningCompositeSourceAuthorityBinding(
        value.owner_scope, value.owner_ref, value.json_binding,
        value.markdown_binding, value.adapter_id, value.adapter_version,
        value.adapter_generation, value.snapshot_digest,
        value.binding_digest, value.schema,
    )


def _child_code(error: object) -> str:
    if type(error) is PlanningSourceAdapterError or type(error) is PlanningMarkdownSourceAdapterError:
        code = error.code
        return code if type(code) is str and code in _ERROR_CODES else "planning_composite_source_adapter_failed"
    return "planning_composite_source_adapter_failed"


def _copy_occurrence(value: object, child: str) -> ChildOccurrence:
    try:
        if child == PLANNING_SOURCE_ADAPTER_ID and type(value) is PlanningSourceOccurrence:
            return PlanningSourceOccurrence(
                value.source, value.source_version, value.chunks, value.entities,
                value.relations, value.projection_locator, value.evidence, value.splitter_version)
        if child == PLANNING_MARKDOWN_SOURCE_ADAPTER_ID and type(value) is PlanningMarkdownSourceOccurrence:
            return PlanningMarkdownSourceOccurrence(
                value.source, value.source_version, value.chunks, value.entities,
                value.relations, value.body_locator, value.evidence, value.splitter_version)
    except (PlanningSourceAdapterError, PlanningMarkdownSourceAdapterError):
        _fail("invalid_snapshot")
    _fail("invalid_snapshot")


def _copy_read(value: object, child: str) -> ChildExactRead:
    try:
        if child == PLANNING_SOURCE_ADAPTER_ID and type(value) is PlanningExactRead:
            return PlanningExactRead(value.logical_ref, value.content, value.content_hash,
                                     value.request, value.evidence)
        if child == PLANNING_MARKDOWN_SOURCE_ADAPTER_ID and type(value) is PlanningMarkdownExactRead:
            return PlanningMarkdownExactRead(value.logical_ref, value.content, value.content_hash,
                                             value.request, value.evidence)
    except (PlanningSourceAdapterError, PlanningMarkdownSourceAdapterError):
        _fail("invalid_snapshot")
    _fail("invalid_snapshot")


def _copy_request(value: object) -> ChildExactRequest:
    try:
        if type(value) is PlanningCharacterRangeRequest:
            return PlanningCharacterRangeRequest(
                value.projection_locator, value.text_range,
                value.request_digest, value.schema)
        if type(value) is PlanningMarkdownCharacterRangeRequest:
            return PlanningMarkdownCharacterRangeRequest(
                value.body_locator, value.text_range,
                value.request_digest, value.schema)
    except (PlanningSourceAdapterError, PlanningMarkdownSourceAdapterError):
        _fail("invalid_request")
    _fail("invalid_request")


def _copy_unavailable(value: object) -> PlanningUnavailableObservation:
    if type(value) is not PlanningUnavailableObservation:
        _fail("invalid_snapshot")
    try:
        return PlanningUnavailableObservation(
            value.logical_ref, value.project_id, value.roadmap_id, value.reason,
            value.snapshot_digest, value.binding_digest, value.observation_digest, value.schema)
    except PlanningSourceAdapterError:
        _fail("invalid_snapshot")


def _validated_json_page(value: object, binding: PlanningSourceAuthorityBinding) -> PlanningDiscoveryPage:
    if type(value) is not PlanningDiscoveryPage:
        _fail("invalid_snapshot")
    items = value.items
    next_cursor = value.next_cursor
    snapshot = value.snapshot_digest
    bound = value.binding_digest
    if (type(items) is not tuple or len(items) > MAX_DISCOVERY_LIMIT
            or next_cursor is not None
            or type(snapshot) is not str or type(bound) is not str):
        _fail("invalid_snapshot")
    try:
        page = PlanningDiscoveryPage(items, next_cursor, snapshot, bound)
    except PlanningSourceAdapterError:
        _fail("invalid_snapshot")
    if (page.snapshot_digest != binding.snapshot_digest
            or page.binding_digest != binding.binding_digest):
        _fail("stale_authority")
    return page


def _validated_markdown_page(value: object, binding: PlanningMarkdownSourceAuthorityBinding) -> PlanningMarkdownDiscoveryPage:
    if type(value) is not PlanningMarkdownDiscoveryPage:
        _fail("invalid_snapshot")
    items = value.items
    next_cursor = value.next_cursor
    snapshot = value.snapshot_digest
    bound = value.binding_digest
    if (type(items) is not tuple or len(items) > MAX_PLANNING_MARKDOWN_SOURCES
            or next_cursor is not None
            or type(snapshot) is not str or type(bound) is not str):
        _fail("invalid_snapshot")
    try:
        page = PlanningMarkdownDiscoveryPage(items, next_cursor, snapshot, bound)
    except PlanningMarkdownSourceAdapterError:
        _fail("invalid_snapshot")
    if (page.snapshot_digest != binding.snapshot_digest
            or page.binding_digest != binding.binding_digest):
        _fail("stale_authority")
    return page


class PlanningCompositeSourceAdapter:
    """Read-only composition preserving each accepted child's exact identities."""

    __slots__ = (
        "_service", "_json_adapter", "_markdown_adapter", "_owner_scope",
        "_owner_ref", "_observed_at", "_binding",
    )

    def __init__(self, *, service: PlanningMcpService, owner_scope: OwnerScope,
                 expected_owner_ref: str, observed_at: str) -> None:
        failure = None
        try:
            if type(service) is not PlanningMcpService or type(owner_scope) is not OwnerScope:
                _fail("invalid_authority")
            service_object = service
            json_adapter = PlanningSourceAdapter(
                service=service_object, owner_scope=owner_scope,
                expected_owner_ref=expected_owner_ref, observed_at=observed_at)
            markdown_adapter = PlanningMarkdownSourceAdapter(
                service=service_object, owner_scope=owner_scope,
                expected_owner_ref=expected_owner_ref, observed_at=observed_at)
            if service is not service_object or type(service_object) is not PlanningMcpService:
                _fail("invalid_authority")
            binding = PlanningCompositeSourceAuthorityBinding(
                owner_scope, expected_owner_ref,
                json_adapter.binding, markdown_adapter.binding)
            self._service = service_object
            self._json_adapter = json_adapter
            self._markdown_adapter = markdown_adapter
            self._owner_scope = binding.owner_scope
            self._owner_ref = binding.owner_ref
            self._observed_at = _text(observed_at, 64, "invalid_authority")
            self._binding = binding
            pin = self._pin()
            first = self._fence(pin)
            if self._fence(pin) != first:
                _fail("stale_authority")
        except PlanningCompositeSourceAdapterError as exc:
            failure = exc.code
        except (PlanningSourceAdapterError, PlanningMarkdownSourceAdapterError) as exc:
            failure = _child_code(exc)
        except Exception:
            failure = "planning_composite_source_adapter_failed"
        if failure is not None:
            _fail(failure)

    @property
    def manifest(self) -> SourceAdapterCapabilityManifest:
        return planning_composite_source_capability_manifest()

    @property
    def binding(self) -> PlanningCompositeSourceAuthorityBinding:
        failure = None
        try:
            return _copy_binding(self._pin().binding)
        except PlanningCompositeSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "invalid_binding"
        if failure is not None:
            _fail(failure)

    def _pin(self) -> _CompositeContext:
        try:
            service = self._service
            json_adapter = self._json_adapter
            markdown_adapter = self._markdown_adapter
            if (type(service) is not PlanningMcpService
                    or type(json_adapter) is not PlanningSourceAdapter
                    or type(markdown_adapter) is not PlanningMarkdownSourceAdapter
                    or type(self._owner_scope) is not OwnerScope):
                _fail("stale_authority")
            if type(self._owner_scope) is not OwnerScope:
                _fail("stale_authority")
            retained_scope = OwnerScope(
                _text(self._owner_scope.value, 64, "stale_authority"))
            retained_owner = _text(self._owner_ref, 512, "stale_authority")
            retained_observed = _text(
                self._observed_at, 64, "stale_authority")
            binding = _copy_binding(self._binding)
            try:
                json_binding = _copy_json_binding(json_adapter.binding)
                markdown_binding = _copy_markdown_binding(markdown_adapter.binding)
            except PlanningCompositeSourceAdapterError:
                _fail("stale_authority")
            if (binding.owner_scope != retained_scope
                    or binding.owner_ref != retained_owner
                    or json_binding != binding.json_binding
                    or markdown_binding != binding.markdown_binding):
                _fail("stale_authority")
            return _CompositeContext(
                service, json_adapter, markdown_adapter, retained_scope,
                retained_owner, retained_observed, binding)
        except PlanningCompositeSourceAdapterError:
            raise
        except Exception:
            _fail("stale_authority")

    def _assert_pin(self, pin: _CompositeContext) -> None:
        if type(pin) is not _CompositeContext:
            _fail("stale_authority")
        current = self._pin()
        if (current.service is not pin.service
                or current.json_adapter is not pin.json_adapter
                or current.markdown_adapter is not pin.markdown_adapter
                or current.owner_scope != pin.owner_scope
                or current.owner_ref != pin.owner_ref
                or current.observed_at != pin.observed_at
                or current.binding != pin.binding):
            _fail("stale_authority")

    def _expected(self, pin, expected_binding_digest, expected_snapshot_digest) -> None:
        expected_binding = _sha(expected_binding_digest, "invalid_request")
        expected_snapshot = _sha(expected_snapshot_digest, "invalid_request")
        if (expected_binding != pin.binding.binding_digest
                or expected_snapshot != pin.binding.snapshot_digest):
            _fail("stale_authority")

    def _fence(self, pin: _CompositeContext) -> tuple[PlanningCompositeSourceDescriptor, ...]:
        self._assert_pin(pin)
        try:
            json_binding = _copy_json_binding(pin.json_adapter.binding)
            markdown_binding = _copy_markdown_binding(pin.markdown_adapter.binding)
        except PlanningCompositeSourceAdapterError:
            _fail("stale_authority")
        if (json_binding != pin.binding.json_binding
                or markdown_binding != pin.binding.markdown_binding):
            _fail("stale_authority")
        try:
            json_page = pin.json_adapter.discover(
                expected_binding_digest=json_binding.binding_digest,
                expected_snapshot_digest=json_binding.snapshot_digest,
                cursor=0, limit=MAX_DISCOVERY_LIMIT)
            markdown_page = pin.markdown_adapter.discover(
                expected_binding_digest=markdown_binding.binding_digest,
                expected_snapshot_digest=markdown_binding.snapshot_digest,
                cursor=0, limit=MAX_PLANNING_MARKDOWN_SOURCES)
        except (PlanningSourceAdapterError, PlanningMarkdownSourceAdapterError) as exc:
            code = _child_code(exc)
            if code in {"source_limit_exceeded", "body_limit_exceeded"}:
                _fail(code)
            _fail("stale_authority")
        self._assert_pin(pin)
        json_page = _validated_json_page(json_page, json_binding)
        markdown_page = _validated_markdown_page(markdown_page, markdown_binding)
        wrapped = [
            PlanningCompositeSourceDescriptor(
                PlanningCompositeSourceRoute(PLANNING_SOURCE_ADAPTER_ID, item.evidence.logical_ref),
                item)
            for item in json_page.items
        ]
        wrapped.extend(
            PlanningCompositeSourceDescriptor(
                PlanningCompositeSourceRoute(
                    PLANNING_MARKDOWN_SOURCE_ADAPTER_ID, item.evidence.logical_ref),
                item)
            for item in markdown_page.items
        )
        wrapped.sort(key=lambda item: (
            item.descriptor.source.canonical_ref,
            item.descriptor.source.provider_ref,
            item.descriptor.source.source_id,
            item.descriptor.source_version.source_version_id,
        ))
        page = PlanningCompositeDiscoveryPage(
            tuple(wrapped), None, pin.binding.snapshot_digest, pin.binding.binding_digest)
        if any(item.descriptor.source.owner_scope != pin.owner_scope.value for item in page.items):
            _fail("invalid_snapshot")
        return page.items

    def _begin(self, expected_binding_digest, expected_snapshot_digest):
        pin = self._pin()
        self._expected(pin, expected_binding_digest, expected_snapshot_digest)
        return pin, self._fence(pin)

    def _finish(self, pin, before) -> None:
        if self._fence(pin) != before:
            _fail("stale_authority")
        self._assert_pin(pin)

    @staticmethod
    def _find(items, route) -> PlanningCompositeSourceDescriptor:
        matches = [item for item in items if item.route == route]
        if len(matches) != 1:
            _fail("record_not_found")
        return matches[0]

    def discover(self, *, expected_binding_digest: str, expected_snapshot_digest: str,
                 cursor: int = 0, limit: int = MAX_PLANNING_COMPOSITE_SOURCES
                 ) -> PlanningCompositeDiscoveryPage:
        failure = None
        try:
            if (type(cursor) is not int or cursor < 0
                    or cursor > MAX_PLANNING_COMPOSITE_SOURCES
                    or type(limit) is not int
                    or not 1 <= limit <= MAX_PLANNING_COMPOSITE_SOURCES):
                _fail("invalid_request")
            pin, before = self._begin(expected_binding_digest, expected_snapshot_digest)
            if cursor > len(before):
                _fail("invalid_request")
            self._finish(pin, before)
            selected = before[cursor:cursor + limit]
            end = cursor + len(selected)
            return PlanningCompositeDiscoveryPage(
                selected, end if end < len(before) else None,
                pin.binding.snapshot_digest, pin.binding.binding_digest)
        except PlanningCompositeSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "planning_composite_source_adapter_failed"
        if failure is not None:
            _fail(failure)

    def observe_version(self, route: PlanningCompositeSourceRoute, *,
                        expected_binding_digest: str,
                        expected_snapshot_digest: str) -> ChildDescriptor:
        failure = None
        try:
            route = _copy_route(route)
            pin, before = self._begin(expected_binding_digest, expected_snapshot_digest)
            expected = self._find(before, route)
            result = None
            child_failure = None
            try:
                if route.child_adapter_id == PLANNING_SOURCE_ADAPTER_ID:
                    result = pin.json_adapter.observe_version(
                        route.logical_ref,
                        expected_binding_digest=pin.binding.json_binding.binding_digest,
                        expected_snapshot_digest=pin.binding.json_binding.snapshot_digest)
                else:
                    result = pin.markdown_adapter.observe_version(
                        route.logical_ref,
                        expected_binding_digest=pin.binding.markdown_binding.binding_digest,
                        expected_snapshot_digest=pin.binding.markdown_binding.snapshot_digest)
            except (PlanningSourceAdapterError, PlanningMarkdownSourceAdapterError) as exc:
                child_failure = _child_code(exc)
            self._finish(pin, before)
            if child_failure is not None:
                _fail(child_failure)
            result = _copy_descriptor(result)
            if result != expected.descriptor:
                _fail("invalid_snapshot")
            return result
        except PlanningCompositeSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "planning_composite_source_adapter_failed"
        if failure is not None:
            _fail(failure)

    def extract(self, route: PlanningCompositeSourceRoute, *,
                expected_binding_digest: str,
                expected_snapshot_digest: str) -> ChildOccurrence:
        failure = None
        try:
            route = _copy_route(route)
            pin, before = self._begin(expected_binding_digest, expected_snapshot_digest)
            expected = self._find(before, route)
            result = None
            child_failure = None
            try:
                if route.child_adapter_id == PLANNING_SOURCE_ADAPTER_ID:
                    result = pin.json_adapter.extract(
                        route.logical_ref,
                        expected_binding_digest=pin.binding.json_binding.binding_digest,
                        expected_snapshot_digest=pin.binding.json_binding.snapshot_digest)
                else:
                    result = pin.markdown_adapter.extract(
                        route.logical_ref,
                        expected_binding_digest=pin.binding.markdown_binding.binding_digest,
                        expected_snapshot_digest=pin.binding.markdown_binding.snapshot_digest)
            except (PlanningSourceAdapterError, PlanningMarkdownSourceAdapterError) as exc:
                child_failure = _child_code(exc)
            self._finish(pin, before)
            if child_failure is not None:
                _fail(child_failure)
            result = _copy_occurrence(result, route.child_adapter_id)
            if (result.source != expected.descriptor.source
                    or result.source_version != expected.descriptor.source_version):
                _fail("invalid_snapshot")
            return result
        except PlanningCompositeSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "planning_composite_source_adapter_failed"
        if failure is not None:
            _fail(failure)

    def read_exact(self, request: ChildExactRequest, *,
                   expected_binding_digest: str,
                   expected_snapshot_digest: str) -> ChildExactRead:
        failure = None
        try:
            request_copy = _copy_request(request)
            if type(request_copy) is PlanningCharacterRangeRequest:
                child = PLANNING_SOURCE_ADAPTER_ID
                logical_ref = request_copy.projection_locator.logical_ref
            else:
                child = PLANNING_MARKDOWN_SOURCE_ADAPTER_ID
                logical_ref = request_copy.body_locator.logical_ref
            route = PlanningCompositeSourceRoute(child, logical_ref)
            pin, before = self._begin(expected_binding_digest, expected_snapshot_digest)
            self._find(before, route)
            result = None
            child_failure = None
            try:
                if child == PLANNING_SOURCE_ADAPTER_ID:
                    result = pin.json_adapter.read_exact(
                        request_copy,
                        expected_binding_digest=pin.binding.json_binding.binding_digest,
                        expected_snapshot_digest=pin.binding.json_binding.snapshot_digest)
                else:
                    result = pin.markdown_adapter.read_exact(
                        request_copy,
                        expected_binding_digest=pin.binding.markdown_binding.binding_digest,
                        expected_snapshot_digest=pin.binding.markdown_binding.snapshot_digest)
            except (PlanningSourceAdapterError, PlanningMarkdownSourceAdapterError) as exc:
                child_failure = _child_code(exc)
            self._finish(pin, before)
            if child_failure is not None:
                _fail(child_failure)
            return _copy_read(result, child)
        except PlanningCompositeSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "planning_composite_source_adapter_failed"
        if failure is not None:
            _fail(failure)

    def observe_unavailable(self, route: PlanningCompositeSourceRoute, *,
                            expected_binding_digest: str,
                            expected_snapshot_digest: str
                            ) -> PlanningUnavailableObservation:
        failure = None
        try:
            route = _copy_route(route)
            pin, before = self._begin(expected_binding_digest, expected_snapshot_digest)
            result = None
            child_failure = None
            try:
                if route.child_adapter_id == PLANNING_SOURCE_ADAPTER_ID:
                    result = pin.json_adapter.observe_unavailable(
                        route.logical_ref,
                        expected_binding_digest=pin.binding.json_binding.binding_digest,
                        expected_snapshot_digest=pin.binding.json_binding.snapshot_digest)
                else:
                    pin.markdown_adapter.observe_unavailable(
                        route.logical_ref,
                        expected_binding_digest=pin.binding.markdown_binding.binding_digest,
                        expected_snapshot_digest=pin.binding.markdown_binding.snapshot_digest)
            except (PlanningSourceAdapterError, PlanningMarkdownSourceAdapterError) as exc:
                child_failure = _child_code(exc)
            self._finish(pin, before)
            if child_failure is not None:
                _fail(child_failure)
            if route.child_adapter_id != PLANNING_SOURCE_ADAPTER_ID:
                _fail("unavailable_state_unproven")
            result = _copy_unavailable(result)
            if result.logical_ref != route.logical_ref:
                _fail("invalid_snapshot")
            return result
        except PlanningCompositeSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "planning_composite_source_adapter_failed"
        if failure is not None:
            _fail(failure)


__all__ = [
    "MAX_PLANNING_COMPOSITE_SOURCES",
    "PLANNING_COMPOSITE_SOURCE_ADAPTER_ID",
    "PLANNING_COMPOSITE_SOURCE_ADAPTER_VERSION",
    "PLANNING_COMPOSITE_SOURCE_BINDING_SCHEMA",
    "PLANNING_COMPOSITE_SOURCE_DOMAIN_ID",
    "PLANNING_COMPOSITE_SOURCE_EXACT_READER_BOUNDARY",
    "PLANNING_COMPOSITE_SOURCE_ROUTE_SCHEMA",
    "PLANNING_COMPOSITE_SOURCE_SNAPSHOT_SCHEMA",
    "PlanningCompositeDiscoveryPage",
    "PlanningCompositeSourceAdapter",
    "PlanningCompositeSourceAdapterError",
    "PlanningCompositeSourceAuthorityBinding",
    "PlanningCompositeSourceDescriptor",
    "PlanningCompositeSourceRoute",
    "planning_composite_source_capability_manifest",
    "planning_composite_source_registration",
]
