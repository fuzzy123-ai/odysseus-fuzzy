"""Redacted public metadata for one retained local Forge version.

This module composes the existing registry, local retained-version store, and
immutable Git inventory.  It creates public context identities in their own
namespace and never reclassifies the existing ForgeCodeSource records.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
from typing import Iterator

from core.auth import AuthManager
from src.forge_context_reader import ImmutableForgeContextReader, RetainedForgeVersion
from src.private_context_confirmation import DisclosureBinding, ManifestItem
from src.context_metadata_binding import McpContextMetadataAdmissionError, _mcp_recipient_digest, _require_mcp_metadata_admission
from src.mcp_context_authority import McpContextAuthorityError
from src.project_forge_local import LocalProjectForge
from src.project_version_store import StoredProjectVersion, owner_key_for, validate_repo_id, validate_version_id
from src.repo_git_adapter import ForgeSnapshotAuthorityBinding, ForgeSnapshotRequest
from src.repo_registry import RepoRecord, RepoRegistry
from src.unified_source_index_contract import SourceKind, make_source_id, make_source_version_id
from src.unified_source_index_owner_scope import OwnerScope


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_OPAQUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/+@~-]{0,255}$")
_PATH_RE = re.compile(r"^[A-Za-z0-9._/@+ -]{1,240}$")
_ERROR_CODES = frozenset({
    "invalid_binding",
    "invalid_context",
    "forge_context_unavailable",
    "forge_context_policy",
    "forge_context_integrity",
    "forge_context_stale",
})


class ForgeContextMetadataError(ValueError):
    """Fixed-code error boundary for public Forge metadata."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code if type(code) is str and code in _ERROR_CODES else "forge_context_unavailable"
        super().__init__(self.code)

    def __repr__(self) -> str:
        return f"ForgeContextMetadataError(code={self.code!r})"


def _fail(code: str) -> None:
    raise ForgeContextMetadataError(code) from None


def _sha(value: object) -> str:
    if type(value) is not str:
        _fail("forge_context_integrity")
    raw = value[7:] if value.startswith("sha256:") else value
    if _SHA256_RE.fullmatch(raw) is None:
        _fail("forge_context_integrity")
    return raw


def _digest(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except Exception:
        _fail("forge_context_integrity")
    return hashlib.sha256(encoded).hexdigest()


def _opaque(value: object, *, code: str = "invalid_context") -> str:
    if type(value) is not str or _OPAQUE_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _path(value: object) -> str:
    if type(value) is not str or _PATH_RE.fullmatch(value) is None:
        _fail("invalid_context")
    if "\\" in value or value.startswith(("/", "~")) or ".." in value.split("/"):
        _fail("invalid_context")
    parts = value.split("/")
    if any(part in {"", ".", "..", ".git", ".env", ".ssh", "id_rsa", "id_dsa", "id_ed25519"} for part in parts):
        _fail("invalid_context")
    return "/".join(parts)


def _commit(value: object) -> str:
    if type(value) is not str or _COMMIT_RE.fullmatch(value) is None:
        _fail("forge_context_integrity")
    return value


def _manifest_item_canonical(item: ManifestItem) -> dict[str, str]:
    return {
        "boundary_sha256": item.boundary_sha256,
        "classification": item.classification,
        "payload_sha256": item.payload_sha256,
        "provenance_ref": item.provenance_ref,
        "record_sha256": item.record_sha256,
        "revision_ref": item.revision_ref,
        "selection_ref": item.selection_ref,
        "source": item.source,
    }


@dataclass(frozen=True, slots=True, repr=False)
class ForgeSelectedPath:
    path: str = field(repr=False)
    content_sha256: str = field(repr=False)
    byte_count: int = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _path(self.path))
        object.__setattr__(self, "content_sha256", _sha(self.content_sha256))
        if type(self.byte_count) is not int or isinstance(self.byte_count, bool) or self.byte_count < 0:
            _fail("forge_context_integrity")

    def canonical(self) -> dict[str, object]:
        return {"byte_count": self.byte_count, "content_sha256": self.content_sha256, "path": self.path}


@dataclass(frozen=True, slots=True, repr=False)
class ForgeSelectionDescriptor:
    """A content-free, public identity for one selected retained version."""

    owner_scope: str = field(repr=False)
    repo_id: str = field(repr=False)
    version_id: str = field(repr=False)
    commit_sha: str = field(repr=False)
    source_id: str = field(repr=False)
    source_version_id: str = field(repr=False)
    selected_files: tuple[ForgeSelectedPath, ...] = field(repr=False)
    inventory_sha256: str = field(repr=False)
    payload_sha256: str = field(repr=False)
    boundary_sha256: str = field(repr=False)
    record_sha256: str = field(repr=False)
    provenance_ref: str = field(repr=False)

    def __post_init__(self) -> None:
        try:
            scope = OwnerScope(self.owner_scope)
            repo = validate_repo_id(self.repo_id)
            version = validate_version_id(self.version_id)
        except Exception:
            _fail("invalid_context")
        object.__setattr__(self, "owner_scope", scope.value)
        object.__setattr__(self, "repo_id", repo)
        object.__setattr__(self, "version_id", version)
        object.__setattr__(self, "commit_sha", _commit(self.commit_sha))
        object.__setattr__(self, "source_id", _opaque(self.source_id, code="forge_context_integrity"))
        object.__setattr__(self, "source_version_id", _opaque(self.source_version_id, code="forge_context_integrity"))
        if type(self.selected_files) is not tuple or not self.selected_files:
            _fail("invalid_context")
        if any(type(item) is not ForgeSelectedPath for item in self.selected_files):
            _fail("invalid_context")
        files = tuple(sorted(self.selected_files, key=lambda item: item.path))
        if files != self.selected_files or len({item.path.casefold() for item in files}) != len(files):
            _fail("invalid_context")
        object.__setattr__(self, "inventory_sha256", _sha(self.inventory_sha256))
        object.__setattr__(self, "payload_sha256", _sha(self.payload_sha256))
        object.__setattr__(self, "boundary_sha256", _sha(self.boundary_sha256))
        object.__setattr__(self, "record_sha256", _sha(self.record_sha256))
        object.__setattr__(self, "provenance_ref", _opaque(self.provenance_ref, code="forge_context_integrity"))

    @property
    def selected_paths(self) -> tuple[str, ...]:
        return tuple(item.path for item in self.selected_files)

    def canonical(self) -> dict[str, object]:
        return {
            "boundary_sha256": self.boundary_sha256,
            "commit_sha": self.commit_sha,
            "inventory_sha256": self.inventory_sha256,
            "owner_scope": self.owner_scope,
            "payload_sha256": self.payload_sha256,
            "provenance_ref": self.provenance_ref,
            "record_sha256": self.record_sha256,
            "repo_id": self.repo_id,
            "selected_files": [item.canonical() for item in self.selected_files],
            "source_id": self.source_id,
            "source_version_id": self.source_version_id,
            "version_id": self.version_id,
        }

    def __repr__(self) -> str:
        return f"ForgeSelectionDescriptor(selected_count={len(self.selected_files)})"


@dataclass(frozen=True, slots=True, repr=False)
class ForgeMetadataManifest:
    """Public metadata that can later be composed with private manifests."""

    descriptor: ForgeSelectionDescriptor = field(repr=False)
    item: ManifestItem = field(repr=False)
    manifest_digest: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.descriptor) is not ForgeSelectionDescriptor or type(self.item) is not ManifestItem:
            _fail("invalid_context")
        if (
            self.item.source != "forge"
            or self.item.classification != "public"
            or self.item.selection_ref != self.descriptor.source_id
            or self.item.revision_ref != self.descriptor.source_version_id
        ):
            _fail("forge_context_integrity")
        object.__setattr__(self, "manifest_digest", _sha(self.manifest_digest))
        expected = _digest({"descriptor": self.descriptor.canonical(), "item": _manifest_item_canonical(self.item)})
        if self.manifest_digest != expected:
            _fail("forge_context_integrity")

    @property
    def digest(self) -> str:
        return self.manifest_digest

    def __repr__(self) -> str:
        return f"ForgeMetadataManifest(selected_count={len(self.descriptor.selected_files)})"


@dataclass(frozen=True, slots=True)
class _AuthoritySnapshot:
    record: RepoRecord
    stored: StoredProjectVersion
    repository: Path
    fence: str


class ForgeContextMetadata:
    """Capture and revalidate public metadata for retained local Forge code."""

    __slots__ = (
        "_local_forge",
        "_registry_path",
        "_auth_manager",
        "_roots",
        "_timeout_seconds",
    )

    def __init__(
        self,
        *,
        local_forge: LocalProjectForge,
        registry_path: str | Path,
        auth_manager: AuthManager,
        workspace_root: str | Path,
        server_projects_root: str | Path,
        timeout_seconds: int = 8,
    ) -> None:
        if type(local_forge) is not LocalProjectForge or type(auth_manager) is not AuthManager:
            _fail("invalid_context")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 8:
            _fail("invalid_context")
        self._local_forge = local_forge
        self._registry_path = Path(registry_path).expanduser().resolve(strict=False)
        self._auth_manager = auth_manager
        self._roots = (
            Path(workspace_root).expanduser().resolve(strict=False),
            Path(server_projects_root).expanduser().resolve(strict=False),
        )
        if any(not root.is_absolute() for root in self._roots):
            _fail("invalid_context")
        self._timeout_seconds = timeout_seconds

    def capture_version_metadata(
        self,
        binding: DisclosureBinding,
        repo_id: str,
        version_id: str,
        paths: tuple[str, ...],
    ) -> ForgeMetadataManifest:
        if type(paths) is not tuple or not paths:
            _fail("invalid_context")
        with self._authority(binding) as owner:
            manifest, fence = self._capture(binding, owner, repo_id, version_id, paths)
            self._recheck(binding, owner, repo_id, version_id, fence)
            return manifest

    def capture_version_metadata_mcp(self, admission: object, metadata_binding: object,
                                     repo_id: str, version_id: str, paths: tuple[str, ...]) -> tuple[ForgeMetadataManifest, str]:
        """Separate recipient-bound metadata capture; it never admits a cookie."""
        foundation = _require_mcp_metadata_admission(admission, metadata_binding)
        try:
            repo = validate_repo_id(repo_id); version = validate_version_id(version_id)
            if type(paths) is not tuple or len(paths) != 1: _fail("invalid_context")
            selected = tuple(_path(path) for path in paths)
            initial = self._authority_snapshot(foundation.owner_subject_id, repo, version)
            owner_scope = OwnerScope.for_subject_id(foundation.owner_subject_id)
            authority = self._authority_binding(foundation.owner_subject_id, repo, version, initial.stored)
            request = ForgeSnapshotRequest(owner_scope.value, "forge_public_registry", repo, version, initial.stored.commit_sha, authority)
            retained = RetainedForgeVersion(initial.repository, owner_scope.value, repo, version, initial.stored.commit_sha, initial.stored.manifest_sha256, authority)
            inventory = ImmutableForgeContextReader(retained, timeout_seconds=self._timeout_seconds).inventory(request, paths=selected)
            files = tuple(ForgeSelectedPath(item.path, item.content_sha256, item.byte_count) for item in inventory.files)
            if files != tuple(sorted(files, key=lambda item: item.path)) or tuple(item.path for item in files) != selected: _fail("forge_context_integrity")
            self._verify_artifacts(initial.stored, files)
            if self._authority_snapshot(foundation.owner_subject_id, repo, version).fence != initial.fence: _fail("forge_context_stale")
            native_boundary = _digest({"authority": authority.to_dict(), "config_roots": [str(root) for root in self._roots], "owner_sid": foundation.owner_subject_id, "security_revision": foundation.security_action_revision, "project": initial.record.to_dict(), "selection": [item.canonical() for item in files], "stored_manifest": dict(initial.stored.manifest)})
            boundary = _mcp_recipient_digest("odysseus.context.mcp_metadata.forge.boundary.v1", metadata_binding, {"native": native_boundary, "repo_id": repo, "version_id": version, "files": [item.canonical() for item in files]})
            payload = files[0].content_sha256
            native_record = _digest({"boundary_sha256": native_boundary, "commit_sha": initial.stored.commit_sha, "manifest_sha256": _sha(initial.stored.manifest_sha256), "payload_sha256": payload, "repo_id": repo, "version_id": version})
            record = _mcp_recipient_digest("odysseus.context.mcp_metadata.forge.record.v1", metadata_binding, {"native": native_record, "boundary": boundary, "payload": payload, "commit": initial.stored.commit_sha})
            provenance = "forge.public.provenance." + _digest({"inventory": _sha(inventory.snapshot_digest), "path": files[0].path, "repo_id": repo, "version_id": version})
            source_id = make_source_id(owner_scope.value, SourceKind.CODE, "forge.context_public.v1:" + repo + ":" + files[0].path)
            revision_ref = "forge.context_public.v1:" + version + ":" + initial.stored.commit_sha + ":" + files[0].path
            source_version_id = make_source_version_id(source_id, revision_ref, files[0].content_sha256)
            descriptor = ForgeSelectionDescriptor(owner_scope.value, repo, version, initial.stored.commit_sha, source_id, source_version_id, files, _sha(inventory.snapshot_digest), payload, boundary, record, provenance)
            item = ManifestItem("forge", source_id, source_version_id, boundary, record, payload, "public", provenance)
            target = _mcp_recipient_digest("odysseus.context.mcp_metadata.forge.target.v1", metadata_binding, {"descriptor": descriptor.canonical(), "item": _manifest_item_canonical(item)})
            _require_mcp_metadata_admission(admission, metadata_binding)
            if self._authority_snapshot(foundation.owner_subject_id, repo, version).fence != initial.fence: _fail("forge_context_stale")
            manifest_digest = _digest({"descriptor": descriptor.canonical(), "item": _manifest_item_canonical(item)})
            return ForgeMetadataManifest(descriptor, item, manifest_digest), target
        except McpContextAuthorityError: raise
        except McpContextMetadataAdmissionError: raise
        except ForgeContextMetadataError: raise
        except ValueError: raise
        except Exception: _fail("forge_context_unavailable")

    def resolve_selection(
        self,
        binding: DisclosureBinding,
        descriptor: ForgeSelectionDescriptor,
    ) -> ForgeSelectionDescriptor:
        if type(descriptor) is not ForgeSelectionDescriptor:
            _fail("invalid_context")
        fresh = self.capture_version_metadata(
            binding,
            descriptor.repo_id,
            descriptor.version_id,
            descriptor.selected_paths,
        )
        if fresh.descriptor != descriptor:
            _fail("forge_context_stale")
        return fresh.descriptor

    def revalidate_metadata(
        self,
        binding: DisclosureBinding,
        expected: ForgeMetadataManifest,
    ) -> ForgeMetadataManifest:
        if type(expected) is not ForgeMetadataManifest:
            _fail("invalid_context")
        fresh = self.capture_version_metadata(
            binding,
            expected.descriptor.repo_id,
            expected.descriptor.version_id,
            expected.descriptor.selected_paths,
        )
        if fresh != expected:
            _fail("forge_context_stale")
        return fresh

    @contextmanager
    def _authority(self, binding: DisclosureBinding) -> Iterator[str]:
        if type(binding) is not DisclosureBinding:
            _fail("invalid_binding")
        guard = getattr(self._auth_manager, "guard_current_admin_subject", None)
        if not callable(guard):
            _fail("invalid_binding")
        try:
            with guard(binding.owner_sid) as owner:
                identity = self._auth_manager.active_admin_subject_identity(subject_id=binding.owner_sid)
                revision = self._auth_manager.security_action_revision()
                if identity != (owner, binding.owner_sid) or revision != binding.security_revision:
                    _fail("invalid_binding")
                yield owner
                identity = self._auth_manager.active_admin_subject_identity(subject_id=binding.owner_sid)
                revision = self._auth_manager.security_action_revision()
                if identity != (owner, binding.owner_sid) or revision != binding.security_revision:
                    _fail("invalid_binding")
        except ForgeContextMetadataError:
            raise
        except Exception:
            _fail("invalid_binding")

    def _capture(
        self,
        binding: DisclosureBinding,
        owner_alias: str,
        repo_id: str,
        version_id: str,
        paths: tuple[str, ...],
    ) -> tuple[ForgeMetadataManifest, str]:
        try:
            repo = validate_repo_id(repo_id)
            version = validate_version_id(version_id)
        except Exception:
            _fail("invalid_context")
        if type(paths) is not tuple or len(paths) != 1:
            _fail("invalid_context")
        selected = tuple(_path(path) for path in paths)
        try:
            initial = self._authority_snapshot(binding.owner_sid, repo, version)
            owner_scope = OwnerScope.for_subject_id(binding.owner_sid)
            authority = self._authority_binding(binding.owner_sid, repo, version, initial.stored)
            request = ForgeSnapshotRequest(
                owner_scope.value,
                "forge_public_registry",
                repo,
                version,
                initial.stored.commit_sha,
                authority,
            )
            retained = RetainedForgeVersion(
                initial.repository,
                owner_scope.value,
                repo,
                version,
                initial.stored.commit_sha,
                initial.stored.manifest_sha256,
                authority,
            )
            inventory = ImmutableForgeContextReader(
                retained,
                timeout_seconds=self._timeout_seconds,
            ).inventory(request, paths=selected)
            files = tuple(
                ForgeSelectedPath(item.path, item.content_sha256, item.byte_count)
                for item in inventory.files
            )
            if files != tuple(sorted(files, key=lambda item: item.path)) or tuple(item.path for item in files) != selected:
                _fail("forge_context_integrity")
            self._verify_artifacts(initial.stored, files)
            latest = self._authority_snapshot(binding.owner_sid, repo, version)
            if latest.fence != initial.fence:
                _fail("forge_context_stale")
            boundary = _digest({
                "authority": authority.to_dict(),
                "config_roots": [str(root) for root in self._roots],
                "owner_sid": binding.owner_sid,
                "security_revision": binding.security_revision,
                "project": initial.record.to_dict(),
                "selection": [item.canonical() for item in files],
                "stored_manifest": dict(initial.stored.manifest),
            })
            payload = files[0].content_sha256
            record = _digest({
                "boundary_sha256": boundary,
                "commit_sha": initial.stored.commit_sha,
                "manifest_sha256": _sha(initial.stored.manifest_sha256),
                "payload_sha256": payload,
                "repo_id": repo,
                "version_id": version,
            })
            provenance = "forge.public.provenance." + _digest({
                "inventory": _sha(inventory.snapshot_digest),
                "path": files[0].path,
                "repo_id": repo,
                "version_id": version,
            })
            source_id = make_source_id(
                owner_scope.value,
                SourceKind.CODE,
                "forge.context_public.v1:" + repo + ":" + files[0].path,
            )
            revision_ref = "forge.context_public.v1:" + version + ":" + initial.stored.commit_sha + ":" + files[0].path
            source_version_id = make_source_version_id(source_id, revision_ref, files[0].content_sha256)
            descriptor = ForgeSelectionDescriptor(
                owner_scope.value,
                repo,
                version,
                initial.stored.commit_sha,
                source_id,
                source_version_id,
                files,
                _sha(inventory.snapshot_digest),
                payload,
                boundary,
                record,
                provenance,
            )
            item = ManifestItem(
                "forge",
                source_id,
                source_version_id,
                boundary,
                record,
                payload,
                "public",
                provenance,
            )
            manifest_digest = _digest({"descriptor": descriptor.canonical(), "item": _manifest_item_canonical(item)})
            return ForgeMetadataManifest(descriptor, item, manifest_digest), initial.fence
        except ForgeContextMetadataError:
            raise
        except Exception:
            _fail("forge_context_unavailable")

    def _authority_snapshot(self, owner_sid: str, repo_id: str, version_id: str) -> _AuthoritySnapshot:
        try:
            registry = RepoRegistry.load_or_empty(self._registry_path)
            record = registry.get(repo_id)
            if (
                type(record) is not RepoRecord
                or record.owner != owner_sid
                or record.repo_kind != "project"
                or record.privacy_class != "public"
                or record.remotes
            ):
                _fail("forge_context_policy")
            source = self._source_for_record(record)
            stored = self._local_forge.verify_version(
                owner_id=owner_sid,
                repo_id=repo_id,
                version_id=version_id,
            )
            if stored.owner_key != owner_key_for(owner_sid) or stored.repo_id != repo_id or stored.version_id != version_id:
                _fail("forge_context_integrity")
            repository = self._local_forge.store.repository_path(owner_id=owner_sid, repo_id=repo_id)
            if repository.resolve(strict=True) != repository or not repository.is_dir():
                _fail("forge_context_integrity")
            fence = _digest({
                "record": record.to_dict(),
                "source": str(source),
                "stored": dict(stored.manifest),
                "stored_manifest_sha256": stored.manifest_sha256,
            })
            return _AuthoritySnapshot(record, stored, repository, fence)
        except ForgeContextMetadataError:
            raise
        except Exception:
            _fail("forge_context_unavailable")

    def _source_for_record(self, record: RepoRecord) -> Path:
        if (
            record.path_ref != record.project_root
            or "/" not in record.path_ref
            or record.workspace_root not in {"workspace", "server-projects"}
        ):
            _fail("forge_context_policy")
        tag, relative = record.path_ref.split("/", 1)
        if tag == "workspace" and record.workspace_root == "workspace":
            root = self._roots[0]
        elif tag == "server-projects" and record.workspace_root == "server-projects":
            root = self._roots[1]
        else:
            _fail("forge_context_policy")
        try:
            target = (root / relative).resolve(strict=True)
            target.relative_to(root.resolve(strict=True))
        except Exception:
            _fail("forge_context_policy")
        if not target.is_dir():
            _fail("forge_context_policy")
        dot_git = target / ".git"
        if dot_git.is_file():
            try:
                git_target = dot_git.resolve(strict=True)
                git_target.relative_to(target)
            except Exception:
                _fail("forge_context_policy")
        elif not dot_git.is_dir() and not ((target / "HEAD").is_file() and (target / "objects").is_dir()):
            _fail("forge_context_policy")
        return target

    @staticmethod
    def _verify_artifacts(stored: StoredProjectVersion, files: tuple[ForgeSelectedPath, ...]) -> None:
        if "artifacts" not in stored.manifest:
            return
        artifacts = stored.manifest.get("artifacts")
        if type(artifacts) is not list:
            _fail("forge_context_integrity")
        if not artifacts:
            return
        by_path: dict[str, dict[str, object]] = {}
        for raw in artifacts:
            if type(raw) is not dict or type(raw.get("path")) is not str or raw["path"] in by_path:
                _fail("forge_context_integrity")
            try:
                artifact_path = _path(raw["path"])
            except ForgeContextMetadataError:
                _fail("forge_context_integrity")
            if (
                artifact_path != raw["path"]
                or type(raw.get("size")) is not int
                or isinstance(raw["size"], bool)
                or raw["size"] < 0
            ):
                _fail("forge_context_integrity")
            by_path[artifact_path] = {
                "sha256": _sha(raw.get("sha256")),
                "size": raw["size"],
            }
        for item in files:
            raw = by_path.get(item.path)
            if raw is None or _sha(raw.get("sha256")) != item.content_sha256 or raw.get("size") != item.byte_count:
                _fail("forge_context_integrity")

    @staticmethod
    def _authority_binding(owner_sid: str, repo_id: str, version_id: str, stored: StoredProjectVersion) -> ForgeSnapshotAuthorityBinding:
        return ForgeSnapshotAuthorityBinding(
            "forge.context_public.v1",
            "v1",
            "usi_generation_" + _digest({
                "owner_sid": owner_sid,
                "repo_id": repo_id,
                "version_id": version_id,
                "manifest_sha256": stored.manifest_sha256,
            }),
            "fca.forge_context_public.v1",
        )

    def _recheck(self, binding: DisclosureBinding, owner_alias: str, repo_id: str, version_id: str, fence: str) -> None:
        if self._authority_snapshot(binding.owner_sid, repo_id, version_id).fence != fence:
            _fail("forge_context_stale")
        identity = self._auth_manager.active_admin_subject_identity(subject_id=binding.owner_sid)
        revision = self._auth_manager.security_action_revision()
        if identity != (owner_alias, binding.owner_sid) or revision != binding.security_revision:
            _fail("invalid_binding")


ForgeContextMetadataProducer = ForgeContextMetadata


__all__ = [
    "ForgeContextMetadata",
    "ForgeContextMetadataError",
    "ForgeMetadataManifest",
    "ForgeContextMetadataProducer",
    "ForgeSelectedPath",
    "ForgeSelectionDescriptor",
]
