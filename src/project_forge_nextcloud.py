"""Offline-first Nextcloud adapter for immutable, readable Forge versions.

The adapter deliberately receives a path-free :class:`ForgeSyncRequest` and a
payload source that resolves only validated request identifiers.  It never
walks a local filesystem, invokes Git, deletes remote data, or overwrites an
existing WebDAV resource.

PVF5 publishes a readable tree below ``Versions/<version>/Tree``.  Replacing
the project-wide ``Current`` pointer is intentionally not implemented here;
that mutable operation remains behind the PVF10 live/policy gate.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable

from src.project_forge_contract import ProjectForgeContractError, validate_persisted_text
from src.project_forge_sync import ForgeSyncOutcome, ForgeSyncRequest
from src.project_version_store import canonical_json_bytes


NEXTCLOUD_FORGE_MANIFEST_SCHEMA = "odysseus.nextcloud_forge_manifest.v1"
DEFAULT_MAX_FILES = 5_000
DEFAULT_MAX_FILE_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 250 * 1024 * 1024
DEFAULT_MAX_MANIFEST_BYTES = 2 * 1024 * 1024

_SHA256_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_SECRET_TEXT_RE = re.compile(
    r"(?i)(?:\bbearer\s+[A-Za-z0-9._~+/=-]{8,}|"
    r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----|"
    r"\b(?:access[_-]?token|auth[_-]?token|token|secret|password|passwd|"
    r"api[_-]?key|private[_-]?key)\b\s*[:=]\s*\S+|"
    r"(?:https?|webdav)://[^\s/:]+:[^\s/@]+@)"
)
_BLOCKED_EXACT_SEGMENTS = frozenset(
    {
        ".git",
        ".ssh",
        "node_modules",
        "venv",
        ".venv",
        "__pycache__",
        ".cache",
        "cache",
        "caches",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        "tmp",
        ".tmp",
        "temp",
        ".temp",
        "id_rsa",
        "id_dsa",
        "id_ed25519",
        "id_ecdsa",
        "credentials",
        "credential",
    }
)
_BLOCKED_FILE_SUFFIXES = (".pem", ".key", ".p12", ".pfx")


class NextcloudForgeError(RuntimeError):
    """Base error for the offline Nextcloud Forge adapter."""


class NextcloudForgePayloadError(NextcloudForgeError):
    """Raised before WebDAV mutation when prepared payload evidence is unsafe."""


class _RemoteReadError(NextcloudForgeError):
    pass


class _StageConflictError(NextcloudForgeError):
    pass


class _StageWriteError(NextcloudForgeError):
    pass


class _StageVerifyError(NextcloudForgeError):
    pass


@dataclass(frozen=True, slots=True)
class NextcloudForgePayloadFile:
    """One already-prepared file; the adapter never resolves a local path."""

    relative_path: str
    content: bytes
    sha256: str
    size_bytes: int

    @classmethod
    def from_bytes(cls, relative_path: str, content: bytes) -> "NextcloudForgePayloadFile":
        if not isinstance(content, bytes):
            raise TypeError("content must be bytes")
        return cls(
            relative_path=relative_path,
            content=content,
            sha256="sha256:" + hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
        )


# Short alias for callers that do not need the Payload qualifier.
NextcloudForgeFile = NextcloudForgePayloadFile


@dataclass(frozen=True, slots=True)
class NextcloudForgePayload:
    """Readable project payload supplied from verified local Forge evidence."""

    files: tuple[NextcloudForgePayloadFile, ...]
    description: str = ""
    version_label: str = ""
    change_notes: tuple[str, ...] = ()
    artifacts: tuple[NextcloudForgePayloadFile, ...] = ()
    repository_bundle: NextcloudForgePayloadFile | None = None
    include_readable_tree: bool = True
    client_side_encryption: bool = False

    @property
    def tree_files(self) -> tuple[NextcloudForgePayloadFile, ...]:
        return self.files


@runtime_checkable
class NextcloudForgePayloadSource(Protocol):
    """Resolve prepared bytes using identifiers only, never a caller path."""

    def load_payload(
        self,
        *,
        owner_key: str,
        operation_id: str,
        repo_id: str,
        transaction_id: str,
        version_id: str,
    ) -> NextcloudForgePayload:
        ...


@runtime_checkable
class NextcloudForgeWebDAVClient(Protocol):
    """The narrow create-only WebDAV surface needed by PVF5."""

    def stat(self, relative_path: str) -> Mapping[str, Any] | None:
        ...

    def get_file_bytes(self, relative_path: str, *, max_bytes: int) -> bytes:
        ...

    def put_bytes_create_only(
        self,
        relative_path: str,
        content: bytes,
        *,
        max_bytes: int,
    ) -> Mapping[str, Any]:
        ...

    def move_create_only(
        self,
        source_relative: str,
        destination_relative: str,
    ) -> Mapping[str, Any]:
        ...


@dataclass(frozen=True, slots=True)
class _Upload:
    stage_path: str
    content: bytes
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class _Plan:
    stage_root: str
    final_root: str
    final_manifest_path: str
    uploads: tuple[_Upload, ...]
    manifest_bytes: bytes
    manifest_sha256: str


class NextcloudForgeSyncAdapter:
    """Create-only readable Nextcloud delivery for one immutable version."""

    def __init__(
        self,
        *,
        webdav_client: NextcloudForgeWebDAVClient,
        payload_source: NextcloudForgePayloadSource,
        root: str = "Odysseus/Projects",
        max_files: int = DEFAULT_MAX_FILES,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
        max_manifest_bytes: int = DEFAULT_MAX_MANIFEST_BYTES,
    ) -> None:
        if webdav_client is None or payload_source is None:
            raise ValueError("webdav_client and payload_source are required")
        self._client = webdav_client
        self._payload_source = payload_source
        self._root = _validate_root(root)
        self._max_files = _positive_limit(max_files, "max_files")
        self._max_file_bytes = _positive_limit(max_file_bytes, "max_file_bytes")
        self._max_total_bytes = _positive_limit(max_total_bytes, "max_total_bytes")
        self._max_manifest_bytes = _positive_limit(max_manifest_bytes, "max_manifest_bytes")

    def sync(self, request: ForgeSyncRequest) -> ForgeSyncOutcome:
        """Stage, verify and create-only promote one prepared version."""

        if not isinstance(request, ForgeSyncRequest):
            return _bare_outcome("blocked", error_code="invalid_request")
        if request.provider != "nextcloud" or request.operation_id != request.idempotency_key:
            return self._outcome(request, "blocked", error_code="provider_mismatch")

        try:
            payload = self._payload_source.load_payload(
                owner_key=request.owner_key,
                operation_id=request.operation_id,
                repo_id=request.repo_id,
                transaction_id=request.transaction_id,
                version_id=request.version_id,
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            return self._outcome(request, "retryable_failure", error_code="payload_source_failed")

        try:
            plan = self._build_plan(request, payload)
        except NextcloudForgePayloadError as exc:
            code = "payload_policy_blocked" if str(exc) == "policy" else "payload_invalid"
            return self._outcome(request, "blocked" if code.endswith("blocked") else "permanent_failure", error_code=code)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            return self._outcome(request, "permanent_failure", error_code="payload_invalid")

        try:
            existing = self._read_manifest_state(
                plan.final_manifest_path,
                plan.manifest_bytes,
                collection_path=plan.final_root,
            )
        except _RemoteReadError:
            return self._outcome(request, "retryable_failure", error_code="remote_manifest_unreadable")
        if existing is not None:
            matches, fingerprint = existing
            if matches:
                return self._outcome(
                    request,
                    "already_synced",
                    provider_fingerprint=fingerprint,
                )
            return self._outcome(request, "diverged", provider_fingerprint=fingerprint)

        for upload in plan.uploads:
            try:
                self._stage_upload(upload)
            except _StageConflictError:
                return self._outcome(request, "permanent_failure", error_code="staging_conflict")
            except _StageWriteError:
                return self._outcome(request, "retryable_failure", error_code="webdav_upload_failed")
            except _StageVerifyError:
                return self._outcome(request, "retryable_failure", error_code="remote_verify_failed")

        try:
            self._client.move_create_only(plan.stage_root, plan.final_root)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            # MOVE may have succeeded even when the response was lost.  Read
            # the immutable destination before scheduling a safe retry.
            try:
                promoted = self._read_manifest_state(
                    plan.final_manifest_path,
                    plan.manifest_bytes,
                    collection_path=plan.final_root,
                )
            except _RemoteReadError:
                promoted = None
            if promoted is not None:
                matches, fingerprint = promoted
                if matches:
                    return self._outcome(request, "already_synced", provider_fingerprint=fingerprint)
                return self._outcome(request, "diverged", provider_fingerprint=fingerprint)
            return self._outcome(request, "retryable_failure", error_code="promotion_failed")

        try:
            promoted = self._read_manifest_state(
                plan.final_manifest_path,
                plan.manifest_bytes,
                collection_path=plan.final_root,
            )
        except _RemoteReadError:
            return self._outcome(request, "retryable_failure", error_code="promotion_unverified")
        if promoted is None:
            return self._outcome(request, "retryable_failure", error_code="promotion_unverified")
        matches, fingerprint = promoted
        if not matches:
            return self._outcome(request, "diverged", provider_fingerprint=fingerprint)
        return self._outcome(request, "synced", provider_fingerprint=fingerprint)

    def _build_plan(self, request: ForgeSyncRequest, payload: Any) -> _Plan:
        if not isinstance(payload, NextcloudForgePayload):
            raise NextcloudForgePayloadError("type")
        if type(payload.client_side_encryption) is not bool or type(payload.include_readable_tree) is not bool:
            raise NextcloudForgePayloadError("policy")
        if payload.client_side_encryption or not payload.include_readable_tree:
            raise NextcloudForgePayloadError("policy")
        if not isinstance(payload.files, tuple) or not payload.files:
            raise NextcloudForgePayloadError("files")
        if not isinstance(payload.artifacts, tuple) or not isinstance(payload.change_notes, tuple):
            raise NextcloudForgePayloadError("collections")
        supplied_file_count = len(payload.files) + len(payload.artifacts) + int(payload.repository_bundle is not None)
        if supplied_file_count > self._max_files:
            raise NextcloudForgePayloadError("file_count")

        description = _validate_metadata_text(payload.description, field_name="description", max_len=4_000)
        version_label = _validate_metadata_text(payload.version_label, field_name="version_label", max_len=100)
        notes = tuple(
            _validate_metadata_text(note, field_name="change_note", max_len=500)
            for note in payload.change_notes
        )
        if len(notes) > 100:
            raise NextcloudForgePayloadError("notes")

        tree = self._validate_files(payload.files, area="Tree")
        artifacts = self._validate_files(payload.artifacts, area="Artifacts")
        bundle: tuple[NextcloudForgePayloadFile, ...] = ()
        if payload.repository_bundle is not None:
            if payload.repository_bundle.relative_path != "repository.bundle":
                raise NextcloudForgePayloadError("bundle")
            bundle = self._validate_files((payload.repository_bundle,), area="")

        all_files = (*tree, *artifacts, *bundle)
        total_bytes = sum(item.size_bytes for item in all_files)
        if total_bytes > self._max_total_bytes:
            raise NextcloudForgePayloadError("total_size")

        project_root = f"{self._root}/{request.repo_id}"
        stage_root = f"{project_root}/.odysseus/staging/{request.operation_id}"
        final_root = f"{project_root}/Versions/{request.version_id}"

        manifest: dict[str, Any] = {
            "schema": NEXTCLOUD_FORGE_MANIFEST_SCHEMA,
            "provider": "nextcloud",
            "operation_id": request.operation_id,
            "repo_id": request.repo_id,
            "transaction_id": request.transaction_id,
            "version_id": request.version_id,
            "commit_sha": request.commit_sha,
            "description": description,
            "version_label": version_label,
            "change_notes": list(notes),
            "client_side_encryption": False,
            "readable_tree": True,
            "project_current_promoted": False,
            "local_manifest_evidence": dict(request.manifest_evidence),
            "files": [_manifest_file(item, prefix="Tree") for item in tree],
            "artifacts": [_manifest_file(item, prefix="Artifacts") for item in artifacts],
            "repository_bundle": _manifest_file(bundle[0], prefix="") if bundle else None,
        }
        manifest_bytes = canonical_json_bytes(manifest)
        if len(manifest_bytes) > self._max_manifest_bytes:
            raise NextcloudForgePayloadError("manifest_size")
        manifest_sha256 = _digest(manifest_bytes)

        uploads = [
            _Upload(
                stage_path=f"{stage_root}/{area}/{item.relative_path}" if area else f"{stage_root}/{item.relative_path}",
                content=item.content,
                sha256=item.sha256,
                size_bytes=item.size_bytes,
            )
            for area, items in (("Tree", tree), ("Artifacts", artifacts), ("", bundle))
            for item in items
        ]
        uploads.append(
            _Upload(
                stage_path=f"{stage_root}/manifest.json",
                content=manifest_bytes,
                sha256=manifest_sha256,
                size_bytes=len(manifest_bytes),
            )
        )
        return _Plan(
            stage_root=stage_root,
            final_root=final_root,
            final_manifest_path=f"{final_root}/manifest.json",
            uploads=tuple(uploads),
            manifest_bytes=manifest_bytes,
            manifest_sha256=manifest_sha256,
        )

    def _validate_files(
        self,
        files: Iterable[NextcloudForgePayloadFile],
        *,
        area: str,
    ) -> tuple[NextcloudForgePayloadFile, ...]:
        validated: list[NextcloudForgePayloadFile] = []
        seen: set[str] = set()
        for item in files:
            if not isinstance(item, NextcloudForgePayloadFile):
                raise NextcloudForgePayloadError("file_type")
            path = _validate_payload_path(item.relative_path)
            folded = path.casefold()
            if folded in seen:
                raise NextcloudForgePayloadError("duplicate")
            seen.add(folded)
            if not isinstance(item.content, bytes):
                raise NextcloudForgePayloadError("bytes")
            if type(item.size_bytes) is not int or item.size_bytes < 0:
                raise NextcloudForgePayloadError("size")
            if item.size_bytes != len(item.content):
                raise NextcloudForgePayloadError("size")
            if item.size_bytes > self._max_file_bytes:
                raise NextcloudForgePayloadError("file_size")
            digest = _normalize_digest(item.sha256)
            if digest != _digest(item.content):
                raise NextcloudForgePayloadError("hash")
            validated.append(
                NextcloudForgePayloadFile(
                    relative_path=path,
                    content=item.content,
                    sha256=digest,
                    size_bytes=item.size_bytes,
                )
            )
        return tuple(sorted(validated, key=lambda entry: entry.relative_path.casefold()))

    def _stage_upload(self, upload: _Upload) -> None:
        try:
            existing = self._client.stat(upload.stage_path)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise _StageWriteError("stat") from exc
        if existing is not None:
            try:
                if self._verify_file(upload):
                    return
            except _StageVerifyError as exc:
                raise _StageConflictError("existing stage differs") from exc
            raise _StageConflictError("existing stage differs")
        try:
            self._client.put_bytes_create_only(
                upload.stage_path,
                upload.content,
                max_bytes=max(self._max_file_bytes, self._max_manifest_bytes),
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise _StageWriteError("put") from exc
        if not self._verify_file(upload):
            raise _StageVerifyError("mismatch")

    def _verify_file(self, upload: _Upload) -> bool:
        try:
            metadata = self._client.stat(upload.stage_path)
            if metadata is None or bool(metadata.get("is_collection")):
                return False
            size = metadata.get("size_bytes")
            if type(size) is not int or size != upload.size_bytes:
                return False
            content = self._client.get_file_bytes(
                upload.stage_path,
                max_bytes=max(1, upload.size_bytes),
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise _StageVerifyError("read") from exc
        return len(content) == upload.size_bytes and _digest(content) == upload.sha256

    def _read_manifest_state(
        self,
        relative_path: str,
        expected: bytes,
        *,
        collection_path: str | None = None,
    ) -> tuple[bool, str] | None:
        try:
            metadata = self._client.stat(relative_path)
            if metadata is None:
                if collection_path is not None and self._client.stat(collection_path) is not None:
                    return False, _digest(b"version_without_manifest")
                return None
            if bool(metadata.get("is_collection")):
                return False, _digest(b"collection")
            declared_size = metadata.get("size_bytes")
            if type(declared_size) is int and declared_size > self._max_manifest_bytes:
                evidence = canonical_json_bytes(
                    {"kind": "oversize_manifest", "size_bytes": declared_size}
                )
                return False, _digest(evidence)
            content = self._client.get_file_bytes(relative_path, max_bytes=self._max_manifest_bytes)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise _RemoteReadError("manifest") from exc
        fingerprint = _digest(content)
        return content == expected, fingerprint

    @staticmethod
    def _outcome(
        request: ForgeSyncRequest,
        status: str,
        *,
        error_code: str = "",
        provider_fingerprint: str = "",
    ) -> ForgeSyncOutcome:
        return ForgeSyncOutcome(
            status=status,
            error_code=error_code,
            idempotency_key=request.idempotency_key,
            version_id=request.version_id,
            commit_sha=request.commit_sha,
            provider_fingerprint=provider_fingerprint,
        )


# Explicit and short aliases make provider registration unambiguous.
NextcloudProjectForgeAdapter = NextcloudForgeSyncAdapter
NextcloudForgeAdapter = NextcloudForgeSyncAdapter


def _bare_outcome(status: str, *, error_code: str) -> ForgeSyncOutcome:
    return ForgeSyncOutcome(status=status, error_code=error_code)


def _positive_limit(value: Any, field_name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _validate_root(value: Any) -> str:
    candidate = str(value or "").strip()
    if candidate.startswith(("/", "~")):
        raise ValueError("root must be relative")
    raw = candidate.strip("/")
    if not raw:
        raise ValueError("root must not be empty")
    parts = raw.split("/")
    if "\\" in raw or "\x00" in raw:
        raise ValueError("root must be relative")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("root must not contain traversal")
    if any(any(ord(character) < 32 for character in part) for part in parts):
        raise ValueError("root contains control characters")
    return "/".join(parts)


def _validate_payload_path(value: Any) -> str:
    raw = str(value or "")
    if not raw or raw != raw.strip() or len(raw) > 500:
        raise NextcloudForgePayloadError("path")
    if "\x00" in raw or "\\" in raw or raw.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", raw):
        raise NextcloudForgePayloadError("path")
    parts = raw.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise NextcloudForgePayloadError("path")
    for part in parts:
        lowered = part.casefold()
        if any(ord(character) < 32 for character in part):
            raise NextcloudForgePayloadError("path")
        if lowered in _BLOCKED_EXACT_SEGMENTS or lowered.startswith(".env"):
            raise NextcloudForgePayloadError("path")
        if lowered.startswith(("id_rsa.", "id_dsa.", "id_ed25519.", "id_ecdsa.")):
            raise NextcloudForgePayloadError("path")
        if "credential" in lowered or "private_key" in lowered or "private-key" in lowered:
            raise NextcloudForgePayloadError("path")
        if lowered.endswith(_BLOCKED_FILE_SUFFIXES):
            raise NextcloudForgePayloadError("path")
    return "/".join(parts)


def _validate_metadata_text(value: Any, *, field_name: str, max_len: int) -> str:
    try:
        text = validate_persisted_text(
            value,
            field_name=field_name,
            allow_empty=True,
            max_len=max_len,
            multiline=True,
        )
    except ProjectForgeContractError as exc:
        raise NextcloudForgePayloadError("metadata") from exc
    if _SECRET_TEXT_RE.search(text):
        raise NextcloudForgePayloadError("metadata")
    return text


def _normalize_digest(value: Any) -> str:
    raw = str(value or "").strip()
    if not _SHA256_RE.fullmatch(raw):
        raise NextcloudForgePayloadError("hash")
    return raw if raw.startswith("sha256:") else "sha256:" + raw


def _digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _manifest_file(item: NextcloudForgePayloadFile, *, prefix: str) -> Mapping[str, Any]:
    path = f"{prefix}/{item.relative_path}" if prefix else item.relative_path
    return {"path": path, "sha256": item.sha256, "size_bytes": item.size_bytes}
