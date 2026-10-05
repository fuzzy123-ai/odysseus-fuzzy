"""Cookie-authenticated exact page reads for retained local Forge content."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any, Callable

from core.auth import AuthManager
from src.context_manifest_producer import CombinedContextManifestProducer
from src.forge_context_metadata import ForgeContextMetadata, ForgeMetadataManifest
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
from src.repo_git_adapter import ForgeSnapshotAuthorityBinding, ForgeSnapshotRequest
from src.unified_source_index_owner_scope import OwnerScope


_SHA_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_OPAQUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
_MAX_PAGE = 1_000_000
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
            native = self._capture_native(binding, request)
            target_item = self._target_item(first_items, request.target)
            self._match_native(native, target_item, request.target)
            private = any(item.classification == "private" for item in first_items)
            first_full = self._verify_private_if_needed(cookie, request, binding, private)
            range_value = self._read_range(binding, request, native)
            final_items = _manifest_items(self._producer.capture_metadata(binding, request.selection))
            final_native = self._capture_native(binding, request)
            final_target = self._target_item(final_items, request.target)
            self._match_native(final_native, final_target, request.target)
            if first_items != final_items or native != final_native:
                _fail("forge_context_stale")
            self._verify_private_if_needed(cookie, request, binding, private)
            self._authority.recheck(binding)
            self._repository.assert_chat_owned(binding)
            content = range_value.content
            total = range_value.file_byte_count
            if request.offset > total:
                _fail("range_invalid")
            end = request.offset + len(content)
            if end > total:
                _fail("read_unavailable")
            whole = "sha256:" + _sha(range_value.file_content_sha256)
            page = "sha256:" + hashlib.sha256(content).hexdigest()
            return ForgeContextPage(
                payload_bytes=content,
                total=total,
                offset=request.offset,
                end=end,
                whole_sha256=whole,
                page_sha256=page,
                manifest_digest=_sha(request.expected_forge_manifest_digest),
            )

    def _capture_native(
        self, binding: DisclosureBinding, request: ForgeContextPageRequest,
    ) -> ForgeMetadataManifest:
        locator = request.target.forge_locator
        if type(locator) is not ForgeSelectionLocator:
            _fail("invalid_context")
        native = self._metadata.capture_version_metadata(
            binding, locator.repo_id, locator.version_id, (locator.path,),
        )
        if type(native) is not ForgeMetadataManifest or _sha(native.digest) != _sha(request.expected_forge_manifest_digest):
            _fail("forge_context_stale")
        return native

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

    @staticmethod
    def _match_native(
        native: ForgeMetadataManifest, item: ManifestItem, target: SourceSelection,
    ) -> None:
        locator = target.forge_locator
        descriptor = native.descriptor
        if (
            native.item != item
            or descriptor.source_id != target.selection_ref
            or descriptor.source_version_id != target.revision_ref
            or descriptor.repo_id != locator.repo_id
            or descriptor.version_id != locator.version_id
            or descriptor.selected_paths != (locator.path,)
        ):
            _fail("forge_context_stale")

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

    def _read_range(
        self, binding: DisclosureBinding, request: ForgeContextPageRequest,
        native: ForgeMetadataManifest,
    ) -> Any:
        locator = request.target.forge_locator
        stored = self._local_forge.verify_version(
            owner_id=binding.owner_sid,
            repo_id=locator.repo_id,
            version_id=locator.version_id,
        )
        repository = self._local_forge.store.repository_path(
            owner_id=binding.owner_sid, repo_id=locator.repo_id,
        )
        owner_scope = OwnerScope.for_subject_id(binding.owner_sid)
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
        descriptor_file = native.descriptor.selected_files[0]
        if (
            _sha(inventory.snapshot_digest) != native.descriptor.inventory_sha256
            or selected.path != locator.path
            or _sha(selected.content_sha256) != _sha(descriptor_file.content_sha256)
            or selected.byte_count != descriptor_file.byte_count
        ):
            _fail("forge_context_stale")
        if selected.byte_count == 0:
            if request.offset != 0:
                _fail("range_invalid")
            max_bytes = 0
        else:
            max_bytes = request.limit
        result = reader.read_exact_range(
            request_ref, path=locator.path,
            offset=request.offset, max_bytes=max_bytes,
        )
        if (
            result.owner_scope != request_ref.owner_scope
            or result.repo_id != request_ref.repo_id
            or result.version_id != request_ref.version_id
            or result.commit_sha != request_ref.commit_sha
            or result.authority_binding.to_dict() != request_ref.authority_binding.to_dict()
            or result.snapshot_digest != inventory.snapshot_digest
            or result.path != locator.path
            or result.file_content_sha256 != selected.content_sha256
            or result.file_byte_count != selected.byte_count
            or result.offset != request.offset
            or type(result.content) is not bytes
        ):
            _fail("read_unavailable")
        return result


__all__ = [
    "ForgeContextPage",
    "ForgeContextPageError",
    "ForgeContextPageConsumer",
    "ForgeContextPageRequest",
]
