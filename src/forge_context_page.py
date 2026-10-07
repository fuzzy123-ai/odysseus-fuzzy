"""Cookie-authenticated exact page reads for retained local Forge content."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Callable

from core.auth import AuthManager
from src.context_manifest_producer import CombinedContextManifestProducer
from src.forge_context_metadata import (
    ForgeContextMetadata,
    ForgeMetadataManifest,
    ForgeSelectedPath,
    ForgeSelectionDescriptor,
)
from src.forge_context_reader import ImmutableForgeContextReader, RetainedForgeVersion
from src.private_context_confirmation import (
    ConfirmationError,
    ContextManifest,
    DisclosureBinding,
    ManifestItem,
    VerifiedDisclosure,
)
from src.private_context_confirmation_app import (
    ChatConfirmationRepository,
    ContextSelection,
    CookieConfirmationAuthority,
    ForgeSelectionLocator,
    PrivateContextConfirmationApp,
    SourceSelection,
)
from src.project_forge_local import LocalProjectForge
from src.repo_git_adapter import (
    ForgeContentRange,
    ForgeSnapshotAuthorityBinding,
    ForgeSnapshotRequest,
)
from src.unified_source_index_owner_scope import OwnerScope


_SHA_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_OPAQUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
_MAX_PAGE = 1_000_000
_MAX_NATIVE_PAGE = 16_384
_MAX_JSON_INT = (1 << 53) - 1
_ERROR_CODES = frozenset({
    "invalid_context",
    "invalid_binding",
    "forge_context_unavailable",
    "forge_context_stale",
    "disclosure_unavailable",
    "range_invalid",
    "read_unavailable",
})


class ForgeContextPageError(Exception):
    """Closed, content-free error boundary for Forge page reads."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or code not in _ERROR_CODES:
            raise ValueError("invalid Forge page error code") from None
        self.code = code
        super().__init__(code)

    def __repr__(self) -> str:
        return "ForgeContextPageError(<redacted>)"


def _fail(code: str) -> None:
    raise ForgeContextPageError(code) from None


def _sha(value: object) -> str:
    if type(value) is not str or _SHA_RE.fullmatch(value) is None:
        _fail("invalid_context")
    return value[7:] if value.startswith("sha256:") else value


def _opaque(value: object) -> str:
    if type(value) is not str or _OPAQUE_RE.fullmatch(value) is None:
        _fail("invalid_context")
    return value


def _digest(value: object) -> str:
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except Exception:
        _fail("forge_context_unavailable")
    return hashlib.sha256(encoded).hexdigest()


def _manifest_items(value: object) -> tuple[ManifestItem, ...]:
    if type(value) is not tuple or any(type(item) is not ManifestItem for item in value):
        _fail("forge_context_unavailable")
    return value


def _exact_manifest_item_scalars(item: ManifestItem) -> bool:
    return all(type(value) is str for value in (
        item.source,
        item.selection_ref,
        item.revision_ref,
        item.boundary_sha256,
        item.record_sha256,
        item.payload_sha256,
        item.classification,
        item.provenance_ref,
    ))


@dataclass(frozen=True, slots=True, repr=False)
class ForgeContextPageRequest:
    """Immutable request bound to the complete original selection."""

    selection: ContextSelection = field(repr=False)
    target: SourceSelection = field(repr=False)
    chat_session_id: str = field(repr=False)
    expected_forge_manifest_digest: str = field(repr=False)
    offset: int = field(repr=False)
    limit: int = field(repr=False)
    disclosure_message_id: int | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if type(self.selection) is not ContextSelection or type(self.target) is not SourceSelection:
            _fail("invalid_context")
        if (
            self.target.source != "forge"
            or self.target not in self.selection.items
            or type(self.target.forge_locator) is not ForgeSelectionLocator
        ):
            _fail("invalid_context")
        if type(self.chat_session_id) is not str or _OPAQUE_RE.fullmatch(self.chat_session_id) is None:
            _fail("invalid_context")
        _sha(self.expected_forge_manifest_digest)
        if type(self.offset) is not int or isinstance(self.offset, bool) or self.offset < 0:
            _fail("invalid_context")
        if type(self.limit) is not int or isinstance(self.limit, bool) or not 1 <= self.limit <= _MAX_PAGE:
            _fail("invalid_context")
        if self.disclosure_message_id is not None and (
            type(self.disclosure_message_id) is not int
            or isinstance(self.disclosure_message_id, bool)
            or not 1 <= self.disclosure_message_id <= _MAX_JSON_INT
        ):
            _fail("invalid_context")

    @property
    def expected_manifest_digest(self) -> str:
        return self.expected_forge_manifest_digest

    def __repr__(self) -> str:
        return "ForgeContextPageRequest(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class ForgeContextPage:
    payload_bytes: bytes = field(repr=False)
    total: int
    offset: int
    end: int
    whole_sha256: str = field(repr=False)
    page_sha256: str = field(repr=False)
    manifest_digest: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.payload_bytes) is not bytes:
            _fail("read_unavailable")
        if (
            type(self.total) is not int or isinstance(self.total, bool) or self.total < 0
            or type(self.offset) is not int or isinstance(self.offset, bool) or self.offset < 0
            or type(self.end) is not int or isinstance(self.end, bool)
            or self.end < self.offset or self.end > self.total
            or self.end - self.offset != len(self.payload_bytes)
        ):
            _fail("read_unavailable")
        _sha(self.whole_sha256)
        _sha(self.page_sha256)
        _sha(self.manifest_digest)

    @property
    def payload(self) -> bytes:
        return self.payload_bytes

    @property
    def byteform(self) -> str:
        return "git_blob"

    def __repr__(self) -> str:
        return "ForgeContextPage(<redacted>)"


class ForgeContextPageConsumer:
    """Read one verified range from one retained Forge version."""

    def __init__(
        self,
        *,
        metadata_provider: ForgeContextMetadata,
        local_forge: LocalProjectForge,
        auth_manager: AuthManager,
        session_factory: Callable[[], object],
        confirmation_app: PrivateContextConfirmationApp,
        manifest_producer: CombinedContextManifestProducer,
    ) -> None:
        confirmation_authority = getattr(confirmation_app, "_authority", None)
        if (
            type(metadata_provider) is not ForgeContextMetadata
            or type(local_forge) is not LocalProjectForge
            or type(auth_manager) is not AuthManager
            or not callable(session_factory)
            or type(confirmation_app) is not PrivateContextConfirmationApp
            or type(manifest_producer) is not CombinedContextManifestProducer
            or getattr(metadata_provider, "_local_forge", None) is not local_forge
            or getattr(metadata_provider, "_auth_manager", None) is not auth_manager
            or getattr(manifest_producer, "_auth_manager", None) is not auth_manager
            or getattr(manifest_producer, "_forge", None) is not metadata_provider
            or getattr(confirmation_authority, "_auth_manager", None) is not auth_manager
            or getattr(confirmation_app, "_producer", None) is not manifest_producer
        ):
            _fail("invalid_context")
        self._metadata = metadata_provider
        self._local_forge = local_forge
        self._auth = auth_manager
        self._session_factory = session_factory
        self._confirmation = confirmation_app
        self._producer = manifest_producer
        self._authority = confirmation_authority.fork()
        if (
            type(self._authority) is not CookieConfirmationAuthority
            or self._authority._auth_manager is not auth_manager
        ):
            _fail("invalid_context")
        self._repository = ChatConfirmationRepository(session_factory, self._authority)

    def read_page(self, cookie: str, request: ForgeContextPageRequest) -> ForgeContextPage:
        if type(request) is not ForgeContextPageRequest:
            _fail("invalid_context")
        try:
            return self._read_page(cookie, request)
        except ConfirmationError:
            raise
        except ForgeContextPageError:
            raise
        except Exception:
            raise ForgeContextPageError("read_unavailable") from None

    def _read_page(self, cookie: str, request: ForgeContextPageRequest) -> ForgeContextPage:
        with self._authority.hold(cookie, request.chat_session_id) as binding:
            self._repository.assert_chat_owned(binding)
            first_items = _manifest_items(self._producer.capture_metadata(binding, request.selection))
            locator = request.target.forge_locator
            if type(locator) is not ForgeSelectionLocator:
                _fail("invalid_context")
            manifest_digest = _sha(request.expected_forge_manifest_digest)
            initial_native = _capture_native_metadata(
                self._metadata, binding, locator, manifest_digest,
            )
            target_item = self._target_item(first_items, request.target)
            if not _exact_native_inputs(binding, request.target, locator, target_item):
                _fail("invalid_context")
            _match_native_metadata(
                initial_native,
                target_item,
                request.target,
                locator,
                OwnerScope.for_subject_id(binding.owner_sid).value,
            )
            private = any(item.classification == "private" for item in first_items)
            self._verify_private_if_needed(cookie, request, binding, private)
            if (
                type(request.offset) is not int
                or request.offset < 0
                or type(request.limit) is not int
                or not 1 <= request.limit <= _MAX_PAGE
            ):
                _fail("invalid_context")
            page = _read_verified_forge_context_page_from_initial(
                metadata_provider=self._metadata,
                local_forge=self._local_forge,
                binding=binding,
                selection=request.target,
                locator=request.target.forge_locator,
                expected_item=target_item,
                expected_manifest_digest=request.expected_forge_manifest_digest,
                offset=request.offset,
                page_bytes=request.limit,
                initial=initial_native,
            )
            final_items = _manifest_items(self._producer.capture_metadata(binding, request.selection))
            self._target_item(final_items, request.target)
            if first_items != final_items:
                _fail("forge_context_stale")
            self._verify_private_if_needed(cookie, request, binding, private)
            self._authority.recheck(binding)
            self._repository.assert_chat_owned(binding)
            return page

    @staticmethod
    def _target_item(items: tuple[ManifestItem, ...], target: SourceSelection) -> ManifestItem:
        matched = tuple(
            item for item in items
            if item.source == target.source
            and item.selection_ref == target.selection_ref
            and item.revision_ref == target.revision_ref
        )
        if len(matched) != 1:
            _fail("forge_context_stale")
        return matched[0]

    def _verify_private_if_needed(
        self, cookie: str, request: ForgeContextPageRequest,
        binding: DisclosureBinding, private: bool,
    ) -> ContextManifest | None:
        if not private:
            return None
        if request.disclosure_message_id is None:
            raise ConfirmationError("invalid_context") from None
        full = self._producer.capture(binding, request.selection)
        if type(full) is not ContextManifest:
            raise ConfirmationError("invalid_context") from None
        verified = self._confirmation.verify_disclosure(
            cookie,
            request.chat_session_id,
            request.disclosure_message_id,
            request.selection,
            expected_subset=full,
        )
        if type(verified) is not VerifiedDisclosure:
            raise ConfirmationError("invalid_context") from None
        return full



def read_native_forge_context_page(
    *,
    metadata_provider: ForgeContextMetadata,
    local_forge: LocalProjectForge,
    binding: DisclosureBinding,
    selection: SourceSelection,
    locator: ForgeSelectionLocator,
    expected_item: ManifestItem,
    expected_manifest_digest: str,
    offset: int,
    page_bytes: int,
) -> ForgeContextPage:
    """Read a bounded retained-Git range under authority held by the caller.

    This seam validates no cookie, grant, or source-selection authority.  The
    exact ``DisclosureBinding`` must already be verified and held by its caller,
    which must keep the source context authenticated for the whole call.
    """
    try:
        if type(page_bytes) is not int or page_bytes > _MAX_NATIVE_PAGE:
            _fail("invalid_context")
        manifest_digest = _validate_native_read_inputs(
            metadata_provider=metadata_provider,
            local_forge=local_forge,
            binding=binding,
            selection=selection,
            locator=locator,
            expected_item=expected_item,
            expected_manifest_digest=expected_manifest_digest,
            offset=offset,
            page_bytes=page_bytes,
        )
        initial = _capture_native_metadata(
            metadata_provider, binding, locator, manifest_digest,
        )
        return _read_verified_forge_context_page_from_initial(
            metadata_provider=metadata_provider,
            local_forge=local_forge,
            binding=binding,
            selection=selection,
            locator=locator,
            expected_item=expected_item,
            expected_manifest_digest=expected_manifest_digest,
            offset=offset,
            page_bytes=page_bytes,
            initial=initial,
        )
    except ForgeContextPageError:
        raise
    except Exception:
        raise ForgeContextPageError("read_unavailable") from None


def _exact_native_inputs(
    binding: DisclosureBinding,
    selection: SourceSelection,
    locator: ForgeSelectionLocator,
    expected_item: ManifestItem,
) -> bool:
    if (
        type(binding) is not DisclosureBinding
        or type(selection) is not SourceSelection
        or type(locator) is not ForgeSelectionLocator
        or type(expected_item) is not ManifestItem
        or type(binding.owner_sid) is not str
        or type(binding.chat_session_id) is not str
        or type(binding.auth_session_binding) is not str
        or type(binding.security_revision) is not int
        or type(binding.audience) is not str
        or type(selection.source) is not str
        or type(selection.selection_ref) is not str
        or type(selection.revision_ref) is not str
        or type(selection.forge_locator) is not ForgeSelectionLocator
        or type(locator.repo_id) is not str
        or type(locator.version_id) is not str
        or type(locator.path) is not str
        or not _exact_manifest_item_scalars(expected_item)
    ):
        return False
    selected_locator = selection.forge_locator
    if any(type(value) is not str for value in (
        selected_locator.repo_id,
        selected_locator.version_id,
        selected_locator.path,
    )):
        return False
    return (
        selection.source == "forge"
        and expected_item.source == "forge"
        and expected_item.classification == "public"
        and (
            selected_locator.repo_id,
            selected_locator.version_id,
            selected_locator.path,
        ) == (locator.repo_id, locator.version_id, locator.path)
    )


def _validate_native_read_inputs(
    *,
    metadata_provider: ForgeContextMetadata,
    local_forge: LocalProjectForge,
    binding: DisclosureBinding,
    selection: SourceSelection,
    locator: ForgeSelectionLocator,
    expected_item: ManifestItem,
    expected_manifest_digest: str,
    offset: int,
    page_bytes: int,
) -> str:
    if (
        type(metadata_provider) is not ForgeContextMetadata
        or type(local_forge) is not LocalProjectForge
        or getattr(metadata_provider, "_local_forge", None) is not local_forge
        or type(binding) is not DisclosureBinding
        or type(selection) is not SourceSelection
        or type(locator) is not ForgeSelectionLocator
        or type(expected_item) is not ManifestItem
        or type(expected_manifest_digest) is not str
        or type(offset) is not int
        or offset < 0
        or type(page_bytes) is not int
        or page_bytes < 1
        or not _exact_native_inputs(binding, selection, locator, expected_item)
    ):
        _fail("invalid_context")
    return _sha(expected_manifest_digest)


def _read_verified_forge_context_page_from_initial(
    *,
    metadata_provider: ForgeContextMetadata,
    local_forge: LocalProjectForge,
    binding: DisclosureBinding,
    selection: SourceSelection,
    locator: ForgeSelectionLocator,
    expected_item: ManifestItem,
    expected_manifest_digest: str,
    offset: int,
    page_bytes: int,
    initial: ForgeMetadataManifest,
) -> ForgeContextPage:
    manifest_digest = _validate_native_read_inputs(
        metadata_provider=metadata_provider,
        local_forge=local_forge,
        binding=binding,
        selection=selection,
        locator=locator,
        expected_item=expected_item,
        expected_manifest_digest=expected_manifest_digest,
        offset=offset,
        page_bytes=page_bytes,
    )
    if type(initial) is not ForgeMetadataManifest:
        _fail("invalid_context")
    if _sha(initial.digest) != manifest_digest:
        _fail("forge_context_stale")
    owner_scope = OwnerScope.for_subject_id(binding.owner_sid).value
    _match_native_metadata(
        initial, expected_item, selection, locator, owner_scope,
    )
    range_value = _read_retained_range(
        local_forge, binding, locator, initial, offset, page_bytes,
    )
    final = _capture_native_metadata(
        metadata_provider, binding, locator, manifest_digest,
    )
    _match_native_metadata(
        final, expected_item, selection, locator, owner_scope,
    )
    if initial != final:
        _fail("forge_context_stale")
    content = range_value.content
    total = range_value.file_byte_count
    end = offset + len(content)
    if end > total:
        _fail("read_unavailable")
    return ForgeContextPage(
        payload_bytes=content,
        total=total,
        offset=offset,
        end=end,
        whole_sha256="sha256:" + _sha(range_value.file_content_sha256),
        page_sha256="sha256:" + hashlib.sha256(content).hexdigest(),
        manifest_digest=manifest_digest,
    )


def _capture_native_metadata(
    metadata_provider: ForgeContextMetadata,
    binding: DisclosureBinding,
    locator: ForgeSelectionLocator,
    expected_manifest_digest: str,
) -> ForgeMetadataManifest:
    native = metadata_provider.capture_version_metadata(
        binding, locator.repo_id, locator.version_id, (locator.path,),
    )
    if (
        type(native) is not ForgeMetadataManifest
        or _sha(native.digest) != expected_manifest_digest
    ):
        _fail("forge_context_stale")
    return native


def _match_native_metadata(
    native: ForgeMetadataManifest,
    expected_item: ManifestItem,
    selection: SourceSelection,
    locator: ForgeSelectionLocator,
    owner_scope: str,
) -> None:
    descriptor = native.descriptor
    if (
        type(descriptor) is not ForgeSelectionDescriptor
        or type(native.item) is not ManifestItem
        or not _exact_manifest_item_scalars(native.item)
        or type(descriptor.selected_files) is not tuple
        or len(descriptor.selected_files) != 1
        or type(descriptor.selected_files[0]) is not ForgeSelectedPath
    ):
        _fail("forge_context_stale")
    descriptor_file = descriptor.selected_files[0]
    if (
        any(type(value) is not str for value in (
            descriptor.owner_scope,
            descriptor.repo_id,
            descriptor.version_id,
            descriptor.commit_sha,
            descriptor.source_id,
            descriptor.source_version_id,
            descriptor.inventory_sha256,
            descriptor.payload_sha256,
            descriptor.boundary_sha256,
            descriptor.record_sha256,
            descriptor.provenance_ref,
            descriptor_file.path,
            descriptor_file.content_sha256,
        ))
        or type(descriptor_file.byte_count) is not int
    ):
        _fail("forge_context_stale")
    if (
        native.item != expected_item
        or descriptor.owner_scope != owner_scope
        or descriptor.source_id != selection.selection_ref
        or descriptor.source_version_id != selection.revision_ref
        or descriptor.repo_id != locator.repo_id
        or descriptor.version_id != locator.version_id
        or descriptor.selected_paths != (locator.path,)
        or descriptor_file.path != locator.path
        or _sha(descriptor_file.content_sha256) != _sha(expected_item.payload_sha256)
        or descriptor.payload_sha256 != expected_item.payload_sha256
        or descriptor.boundary_sha256 != expected_item.boundary_sha256
        or descriptor.record_sha256 != expected_item.record_sha256
        or descriptor.provenance_ref != expected_item.provenance_ref
    ):
        _fail("forge_context_stale")


def _read_retained_range(
    local_forge: LocalProjectForge,
    binding: DisclosureBinding,
    locator: ForgeSelectionLocator,
    native: ForgeMetadataManifest,
    offset: int,
    page_bytes: int,
) -> ForgeContentRange:
    stored = local_forge.verify_version(
        owner_id=binding.owner_sid,
        repo_id=locator.repo_id,
        version_id=locator.version_id,
    )
    repository = local_forge.store.repository_path(
        owner_id=binding.owner_sid, repo_id=locator.repo_id,
    )
    owner_scope = OwnerScope.for_subject_id(binding.owner_sid)
    descriptor = native.descriptor
    descriptor_file = descriptor.selected_files[0]
    if (
        descriptor.owner_scope != owner_scope.value
        or descriptor.repo_id != locator.repo_id
        or descriptor.version_id != locator.version_id
        or descriptor.commit_sha != stored.commit_sha
        or descriptor_file.path != locator.path
    ):
        _fail("forge_context_stale")
    authority = ForgeSnapshotAuthorityBinding(
        "forge.context_public.v1",
        "v1",
        "usi_generation_" + _digest({
            "owner_sid": binding.owner_sid,
            "repo_id": locator.repo_id,
            "version_id": locator.version_id,
            "manifest_sha256": stored.manifest_sha256,
        }),
        "fca.forge_context_public.v1",
    )
    request_ref = ForgeSnapshotRequest(
        owner_scope.value,
        "forge_public_registry",
        locator.repo_id,
        locator.version_id,
        stored.commit_sha,
        authority,
    )
    retained = RetainedForgeVersion(
        repository,
        owner_scope.value,
        locator.repo_id,
        locator.version_id,
        stored.commit_sha,
        stored.manifest_sha256,
        authority,
    )
    reader = ImmutableForgeContextReader(retained)
    inventory = reader.inventory(request_ref, paths=(locator.path,))
    selected = inventory.file(locator.path)
    if (
        inventory.owner_scope != request_ref.owner_scope
        or inventory.repo_id != request_ref.repo_id
        or inventory.version_id != request_ref.version_id
        or inventory.commit_sha != request_ref.commit_sha
        or _sha(inventory.manifest_sha256) != _sha(stored.manifest_sha256)
        or inventory.authority_binding.to_dict() != request_ref.authority_binding.to_dict()
        or _sha(inventory.snapshot_digest) != descriptor.inventory_sha256
        or selected.path != locator.path
        or _sha(selected.content_sha256) != _sha(descriptor_file.content_sha256)
        or selected.byte_count != descriptor_file.byte_count
    ):
        _fail("forge_context_stale")
    if selected.byte_count == 0:
        if offset != 0:
            _fail("range_invalid")
        max_bytes = 0
    else:
        if offset >= selected.byte_count:
            _fail("range_invalid")
        max_bytes = page_bytes
    result = reader.read_exact_range(
        request_ref, path=locator.path, offset=offset, max_bytes=max_bytes,
    )
    if (
        type(result) is not ForgeContentRange
        or result.owner_scope != request_ref.owner_scope
        or result.repo_id != request_ref.repo_id
        or result.version_id != request_ref.version_id
        or result.commit_sha != request_ref.commit_sha
        or result.authority_binding.to_dict() != request_ref.authority_binding.to_dict()
        or result.snapshot_digest != inventory.snapshot_digest
        or result.path != locator.path
        or result.file_content_sha256 != selected.content_sha256
        or result.file_byte_count != selected.byte_count
        or result.offset != offset
        or type(result.content) is not bytes
        or len(result.content) != min(page_bytes, selected.byte_count - offset)
    ):
        _fail("read_unavailable")
    return result


__all__ = [
    "ForgeContextPage",
    "ForgeContextPageError",
    "ForgeContextPageConsumer",
    "ForgeContextPageRequest",
]
