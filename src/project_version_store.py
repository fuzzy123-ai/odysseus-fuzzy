"""Persistent, provider-neutral records for the local Odysseus Forge.

The immutable manifest deliberately contains no provider delivery state.  It is
the local version fact; mutable transaction and current-pointer records live
beside it and may be repaired independently.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from src.constants import DATA_DIR
from src.project_forge_contract import (
    ProjectForgeContractError,
    validate_persisted_text,
    validate_repo_relative_path,
)


VERSION_MANIFEST_SCHEMA = "odysseus.project_version_manifest.v1"
VERSION_TRANSACTION_SCHEMA = "odysseus.project_version_transaction.v1"
VERSION_POINTER_SCHEMA = "odysseus.project_version_pointer.v1"
VERSION_IDEMPOTENCY_SCHEMA = "odysseus.project_version_idempotency.v1"

_REPO_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_OWNER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._+-]{0,255}$")
_TRANSACTION_ID_RE = re.compile(r"^pct_[0-9a-f]{32}$")
_VERSION_ID_RE = re.compile(r"^pv_[0-9a-f]{32}$")
_COMMIT_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_FORBIDDEN_POLICY_KEY_RE = re.compile(
    r"(?:credential|password|passwd|secret|token|api[_-]?key|private[_-]?key|"
    r"endpoint|url|path|response|result|provider[_-]?status|raw)",
    re.IGNORECASE,
)
_ARTIFACT_FIELDS = frozenset({"path", "sha256", "size", "media_type", "kind"})


class ProjectVersionStoreError(ProjectForgeContractError):
    """Raised when a local Forge record is unsafe or inconsistent."""


class ProjectVersionConflictError(ProjectVersionStoreError):
    """Raised for an idempotency collision or immutable-version overwrite."""


class ProjectVersionIntegrityError(ProjectVersionStoreError):
    """Raised when persisted version evidence fails closed verification."""


@dataclass(frozen=True, slots=True)
class VersionReservation:
    owner_key: str
    repo_id: str
    transaction_id: str
    version_id: str
    created_at: str
    request_fingerprint: str
    idempotency_digest: str
    replay: bool = False


@dataclass(frozen=True, slots=True)
class StoredProjectVersion:
    """A verified immutable manifest plus its separately persisted digest."""

    manifest: Mapping[str, Any]
    manifest_sha256: str

    @property
    def owner_key(self) -> str:
        return str(self.manifest["owner_key"])

    @property
    def repo_id(self) -> str:
        return str(self.manifest["repo_id"])

    @property
    def transaction_id(self) -> str:
        return str(self.manifest["transaction_id"])

    @property
    def version_id(self) -> str:
        return str(self.manifest["version_id"])

    @property
    def commit_sha(self) -> str:
        return str(self.manifest["commit_sha"])

    @property
    def created_at(self) -> str:
        return str(self.manifest["created_at"])

    def to_dict(self) -> dict[str, Any]:
        return {"manifest": dict(self.manifest), "manifest_sha256": self.manifest_sha256}


def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    """Return the exact UTF-8 representation used for durable Forge records."""

    try:
        encoded = json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProjectVersionStoreError("record must contain canonical JSON values") from exc
    return encoded + b"\n"


def owner_key_for(owner_id: Any) -> str:
    """Derive a stable, non-identifying storage key from a validated owner id."""

    owner = validate_persisted_text(owner_id, field_name="owner_id", max_len=256)
    if not _OWNER_ID_RE.fullmatch(owner):
        raise ProjectVersionStoreError("owner_id contains unsupported characters")
    # 128 hash bits keep the key collision-resistant while leaving enough path
    # budget for Git loose objects on legacy Windows MAX_PATH installations.
    return "own_" + hashlib.sha256(owner.encode("utf-8")).hexdigest()[:32]


def validate_repo_id(value: Any) -> str:
    repo_id = validate_persisted_text(value, field_name="repo_id", max_len=100)
    if repo_id in (".", "..") or not _REPO_ID_RE.fullmatch(repo_id):
        raise ProjectVersionStoreError("repo_id contains unsupported characters")
    return repo_id


def validate_transaction_id(value: Any) -> str:
    transaction_id = str(value or "").strip()
    if not _TRANSACTION_ID_RE.fullmatch(transaction_id):
        raise ProjectVersionStoreError("transaction_id must be pct_ followed by 32 lowercase hex characters")
    return transaction_id


def validate_version_id(value: Any) -> str:
    version_id = str(value or "").strip()
    if not _VERSION_ID_RE.fullmatch(version_id):
        raise ProjectVersionStoreError("version_id must be pv_ followed by 32 lowercase hex characters")
    return version_id


def validate_commit_sha(value: Any) -> str:
    commit_sha = str(value or "").strip()
    if not _COMMIT_SHA_RE.fullmatch(commit_sha):
        raise ProjectVersionStoreError("commit_sha must be 40 or 64 lowercase hexadecimal characters")
    return commit_sha


class ProjectVersionStore:
    """Owner-scoped immutable manifest and transaction persistence."""

    def __init__(
        self,
        *,
        root: str | Path | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        configured_root = Path(root) if root is not None else Path(DATA_DIR) / "project_forge"
        self.root = configured_root.expanduser().resolve(strict=False)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def project_root(self, *, owner_id: Any, repo_id: Any) -> Path:
        owner_key = owner_key_for(owner_id)
        repo = validate_repo_id(repo_id)
        path = self.root / "owners" / owner_key / "projects" / repo
        return self._checked_store_path(path)

    def repository_path(self, *, owner_id: Any, repo_id: Any) -> Path:
        return self._checked_store_path(self.project_root(owner_id=owner_id, repo_id=repo_id) / "repository.git")

    def reserve_version(
        self,
        *,
        owner_id: Any,
        repo_id: Any,
        idempotency_key: Any,
        request_payload: Mapping[str, Any],
    ) -> VersionReservation:
        owner_key = owner_key_for(owner_id)
        repo = validate_repo_id(repo_id)
        key = validate_persisted_text(idempotency_key, field_name="idempotency_key", max_len=256)
        safe_request = _normalize_safe_json(request_payload, field_name="request")
        if not isinstance(safe_request, dict):
            raise ProjectVersionStoreError("request_payload must be a mapping")
        fingerprint = _digest(canonical_json_bytes(safe_request))
        key_digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        paths = self._ensure_project_layout(owner_key=owner_key, repo_id=repo)
        record_path = paths["idempotency"] / f"{key_digest}.json"

        if record_path.exists():
            record = self._read_json(record_path, field_name="idempotency record")
            self._validate_idempotency_identity(
                record,
                owner_key=owner_key,
                repo_id=repo,
                key_digest=key_digest,
                fingerprint=fingerprint,
            )
            return VersionReservation(
                owner_key=owner_key,
                repo_id=repo,
                transaction_id=validate_transaction_id(record.get("transaction_id")),
                version_id=validate_version_id(record.get("version_id")),
                created_at=_validate_timestamp(record.get("created_at")),
                request_fingerprint=fingerprint,
                idempotency_digest=key_digest,
                replay=record.get("status") == "stored",
            )

        reservation = VersionReservation(
            owner_key=owner_key,
            repo_id=repo,
            transaction_id="pct_" + uuid.uuid4().hex,
            version_id="pv_" + uuid.uuid4().hex,
            created_at=self._timestamp(),
            request_fingerprint=fingerprint,
            idempotency_digest=key_digest,
        )
        idempotency_record = {
            "schema": VERSION_IDEMPOTENCY_SCHEMA,
            "owner_key": owner_key,
            "repo_id": repo,
            "idempotency_digest": key_digest,
            "request_fingerprint": fingerprint,
            "transaction_id": reservation.transaction_id,
            "version_id": reservation.version_id,
            "created_at": reservation.created_at,
            "status": "created",
        }
        try:
            self._exclusive_atomic_write(record_path, idempotency_record)
        except FileExistsError:
            return self.reserve_version(
                owner_id=owner_id,
                repo_id=repo,
                idempotency_key=key,
                request_payload=safe_request,
            )
        self._atomic_write(
            paths["transactions"] / f"{reservation.transaction_id}.json",
            self._transaction_record(reservation=reservation, status="created"),
        )
        return reservation

    def persist_version(
        self,
        *,
        reservation: VersionReservation,
        commit_sha: Any,
        policy_snapshot: Mapping[str, Any] | None = None,
        version_label: Any = "",
        change_notes: Iterable[Any] = (),
        artifacts: Iterable[Mapping[str, Any]] = (),
    ) -> StoredProjectVersion:
        reservation = self._validate_reservation(reservation)
        commit = validate_commit_sha(commit_sha)
        metadata = self.normalize_version_metadata(
            version_label=version_label,
            change_notes=change_notes,
            policy_snapshot=policy_snapshot,
            artifacts=artifacts,
        )
        label = metadata["version_label"]
        notes = metadata["change_notes"]
        policy = metadata["policy_snapshot"]
        artifact_records = metadata["artifacts"]
        request_payload = {
            "commit_sha": commit,
            "version_label": label,
            "change_notes": notes,
            "policy_snapshot": policy,
            "artifacts": artifact_records,
        }
        if _digest(canonical_json_bytes(request_payload)) != reservation.request_fingerprint:
            raise ProjectVersionConflictError("version metadata does not match the reserved request")
        paths = self._ensure_project_layout(owner_key=reservation.owner_key, repo_id=reservation.repo_id)
        final_dir = self._checked_store_path(paths["versions"] / reservation.version_id)
        staging_dir = self._checked_store_path(paths["staging"] / reservation.transaction_id)

        manifest: dict[str, Any] = {
            "schema": VERSION_MANIFEST_SCHEMA,
            "owner_key": reservation.owner_key,
            "repo_id": reservation.repo_id,
            "transaction_id": reservation.transaction_id,
            "version_id": reservation.version_id,
            "commit_sha": commit,
            "created_at": reservation.created_at,
            "policy_snapshot": policy,
            "artifacts": artifact_records,
        }
        if label:
            manifest["version_label"] = label
        if notes:
            manifest["change_notes"] = notes
        manifest_bytes = canonical_json_bytes(manifest)
        manifest_digest = _digest(manifest_bytes)

        if final_dir.exists():
            final_manifest = self._checked_store_path(final_dir / "manifest.json")
            if not final_manifest.is_file() or final_manifest.read_bytes() != manifest_bytes:
                raise ProjectVersionConflictError(f"version already exists with different content: {reservation.version_id}")
        else:
            if staging_dir.exists():
                staged_manifest = self._checked_store_path(staging_dir / "manifest.json")
                if not staged_manifest.is_file() or staged_manifest.read_bytes() != manifest_bytes:
                    raise ProjectVersionConflictError(
                        f"incomplete staging contains different content: {reservation.transaction_id}"
                    )
            else:
                staging_dir.mkdir(parents=False, exist_ok=False)
                try:
                    self._atomic_write_bytes(staging_dir / "manifest.json", manifest_bytes)
                except Exception:
                    self.mark_failed(reservation=reservation, failure_code="manifest_persist_failed")
                    raise
            try:
                os.replace(staging_dir, final_dir)
                _fsync_directory(paths["versions"])
            except Exception:
                self.mark_failed(reservation=reservation, failure_code="manifest_promote_failed")
                raise

        transaction = self._transaction_record(
            reservation=reservation,
            status="stored",
            manifest_sha256=manifest_digest,
            commit_sha=commit,
        )
        self._atomic_write(paths["transactions"] / f"{reservation.transaction_id}.json", transaction)
        self._atomic_write(
            paths["idempotency"] / f"{reservation.idempotency_digest}.json",
            {
                "schema": VERSION_IDEMPOTENCY_SCHEMA,
                "owner_key": reservation.owner_key,
                "repo_id": reservation.repo_id,
                "idempotency_digest": reservation.idempotency_digest,
                "request_fingerprint": reservation.request_fingerprint,
                "transaction_id": reservation.transaction_id,
                "version_id": reservation.version_id,
                "created_at": reservation.created_at,
                "status": "stored",
                "manifest_sha256": manifest_digest,
            },
        )
        # current.json is a replaceable cache and is deliberately written last.
        self._atomic_write(
            paths["project"] / "current.json",
            {
                "schema": VERSION_POINTER_SCHEMA,
                "owner_key": reservation.owner_key,
                "repo_id": reservation.repo_id,
                "transaction_id": reservation.transaction_id,
                "version_id": reservation.version_id,
                "commit_sha": commit,
                "created_at": reservation.created_at,
                "manifest_sha256": manifest_digest,
            },
        )
        return StoredProjectVersion(manifest=dict(manifest), manifest_sha256=manifest_digest)

    # Friendly alias for direct store users.
    store_version = persist_version

    def normalize_version_metadata(
        self,
        *,
        policy_snapshot: Mapping[str, Any] | None = None,
        version_label: Any = "",
        change_notes: Iterable[Any] = (),
        artifacts: Iterable[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        """Validate metadata before any Git side effect and return JSON values."""

        return {
            "version_label": validate_persisted_text(
                version_label,
                field_name="version_label",
                allow_empty=True,
                max_len=100,
            ),
            "change_notes": _normalize_change_notes(change_notes),
            "policy_snapshot": _normalize_policy_snapshot(policy_snapshot or {}),
            "artifacts": _normalize_artifacts(artifacts),
        }

    def load_version(self, *, owner_id: Any, repo_id: Any, version_id: Any) -> StoredProjectVersion:
        owner_key = owner_key_for(owner_id)
        repo = validate_repo_id(repo_id)
        version = validate_version_id(version_id)
        project = self._project_root_from_keys(owner_key=owner_key, repo_id=repo)
        manifest_path = self._checked_store_path(project / "versions" / version / "manifest.json")
        if not manifest_path.is_file():
            raise ProjectVersionIntegrityError(f"version is incomplete or missing: {version}")
        raw = manifest_path.read_bytes()
        manifest = self._decode_json(raw, field_name="version manifest")
        transaction_id = validate_transaction_id(manifest.get("transaction_id"))
        transaction_path = self._checked_store_path(project / "transactions" / f"{transaction_id}.json")
        if not transaction_path.is_file():
            raise ProjectVersionIntegrityError("version transaction evidence is missing")
        transaction = self._read_json(transaction_path, field_name="version transaction")
        expected_hash = str(transaction.get("manifest_sha256") or "")
        if not _SHA256_RE.fullmatch(expected_hash):
            raise ProjectVersionIntegrityError("version transaction has no valid manifest hash")
        return StoredProjectVersion(manifest=manifest, manifest_sha256=expected_hash)

    def verify_version(self, *, owner_id: Any, repo_id: Any, version_id: Any) -> StoredProjectVersion:
        owner_key = owner_key_for(owner_id)
        repo = validate_repo_id(repo_id)
        version = validate_version_id(version_id)
        stored = self.load_version(owner_id=owner_id, repo_id=repo, version_id=version)
        manifest = dict(stored.manifest)
        required = {
            "schema",
            "owner_key",
            "repo_id",
            "transaction_id",
            "version_id",
            "commit_sha",
            "created_at",
            "policy_snapshot",
            "artifacts",
        }
        if not required.issubset(manifest):
            raise ProjectVersionIntegrityError("version manifest is missing required fields")
        if set(manifest) - (required | {"version_label", "change_notes"}):
            raise ProjectVersionIntegrityError("version manifest contains unknown fields")
        if manifest.get("schema") != VERSION_MANIFEST_SCHEMA:
            raise ProjectVersionIntegrityError("version manifest schema is invalid")
        if manifest.get("owner_key") != owner_key or manifest.get("repo_id") != repo or manifest.get("version_id") != version:
            raise ProjectVersionIntegrityError("version manifest path identity does not match its payload")
        transaction_id = validate_transaction_id(manifest.get("transaction_id"))
        validate_commit_sha(manifest.get("commit_sha"))
        _validate_timestamp(manifest.get("created_at"))
        _normalize_policy_snapshot(manifest.get("policy_snapshot"))
        _normalize_artifacts(manifest.get("artifacts", []))
        if "version_label" in manifest:
            validate_persisted_text(manifest["version_label"], field_name="version_label", max_len=100)
        if "change_notes" in manifest:
            _normalize_change_notes(manifest["change_notes"])

        project = self._project_root_from_keys(owner_key=owner_key, repo_id=repo)
        manifest_path = self._checked_store_path(project / "versions" / version / "manifest.json")
        raw = manifest_path.read_bytes()
        if raw != canonical_json_bytes(manifest):
            raise ProjectVersionIntegrityError("version manifest is not canonical JSON")
        if _digest(raw) != stored.manifest_sha256:
            raise ProjectVersionIntegrityError("version manifest hash does not match transaction evidence")
        transaction = self._read_json(
            project / "transactions" / f"{transaction_id}.json",
            field_name="version transaction",
        )
        if (
            transaction.get("schema") != VERSION_TRANSACTION_SCHEMA
            or transaction.get("status") != "stored"
            or transaction.get("owner_key") != owner_key
            or transaction.get("repo_id") != repo
            or transaction.get("version_id") != version
            or transaction.get("commit_sha") != manifest.get("commit_sha")
            or transaction.get("manifest_sha256") != stored.manifest_sha256
        ):
            raise ProjectVersionIntegrityError("version transaction does not match manifest identity")
        return stored

    def iter_verified_versions(self, *, owner_id: Any, repo_id: Any) -> tuple[StoredProjectVersion, ...]:
        """Return all complete versions through the public fail-closed verifier."""

        owner_key = owner_key_for(owner_id)
        repo = validate_repo_id(repo_id)
        versions_root = self._checked_store_path(
            self._project_root_from_keys(owner_key=owner_key, repo_id=repo) / "versions"
        )
        if not versions_root.exists():
            return ()
        if not versions_root.is_dir():
            raise ProjectVersionIntegrityError("versions root is not a directory")
        versions: list[StoredProjectVersion] = []
        for entry in versions_root.iterdir():
            if not entry.is_dir():
                raise ProjectVersionIntegrityError("versions root contains an unexpected entry")
            try:
                version_id = validate_version_id(entry.name)
            except ProjectVersionStoreError as exc:
                raise ProjectVersionIntegrityError("versions root contains an invalid version identity") from exc
            versions.append(self.verify_version(owner_id=owner_id, repo_id=repo, version_id=version_id))
        return tuple(sorted(versions, key=lambda item: (item.created_at, item.version_id)))

    def mark_failed(self, *, reservation: VersionReservation, failure_code: Any = "local_store_failed") -> None:
        reservation = self._validate_reservation(reservation)
        code = validate_persisted_text(failure_code, field_name="failure_code", max_len=80)
        paths = self._ensure_project_layout(owner_key=reservation.owner_key, repo_id=reservation.repo_id)
        self._atomic_write(
            paths["transactions"] / f"{reservation.transaction_id}.json",
            self._transaction_record(reservation=reservation, status="failed", failure_code=code),
        )

    def _timestamp(self) -> str:
        value = self._clock()
        if not isinstance(value, datetime):
            raise ProjectVersionStoreError("clock must return a datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ProjectVersionStoreError("clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _project_root_from_keys(self, *, owner_key: str, repo_id: str) -> Path:
        if not re.fullmatch(r"own_[0-9a-f]{32,64}", owner_key):
            raise ProjectVersionStoreError("owner_key is invalid")
        repo = validate_repo_id(repo_id)
        return self._checked_store_path(self.root / "owners" / owner_key / "projects" / repo)

    def _ensure_project_layout(self, *, owner_key: str, repo_id: str) -> dict[str, Path]:
        project = self._project_root_from_keys(owner_key=owner_key, repo_id=repo_id)
        paths = {
            "project": project,
            "transactions": project / "transactions",
            "idempotency": project / "idempotency",
            "versions": project / "versions",
            "staging": project / ".staging",
        }
        for path in paths.values():
            checked = self._checked_store_path(path)
            checked.mkdir(parents=True, exist_ok=True)
            self._checked_store_path(checked)
        return {key: self._checked_store_path(path) for key, path in paths.items()}

    def _checked_store_path(self, path: Path) -> Path:
        resolved = path.expanduser().resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ProjectVersionStoreError("Forge path escapes configured storage root") from exc
        return resolved

    def _validate_reservation(self, reservation: VersionReservation) -> VersionReservation:
        if not isinstance(reservation, VersionReservation):
            raise ProjectVersionStoreError("reservation must be a VersionReservation")
        if not re.fullmatch(r"own_[0-9a-f]{32,64}", reservation.owner_key):
            raise ProjectVersionStoreError("reservation owner_key is invalid")
        validate_repo_id(reservation.repo_id)
        validate_transaction_id(reservation.transaction_id)
        validate_version_id(reservation.version_id)
        _validate_timestamp(reservation.created_at)
        if not _SHA256_RE.fullmatch(reservation.request_fingerprint):
            raise ProjectVersionStoreError("reservation request_fingerprint is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", reservation.idempotency_digest):
            raise ProjectVersionStoreError("reservation idempotency digest is invalid")
        return reservation

    def _transaction_record(
        self,
        *,
        reservation: VersionReservation,
        status: str,
        manifest_sha256: str = "",
        commit_sha: str = "",
        failure_code: str = "",
    ) -> dict[str, Any]:
        if status not in ("created", "stored", "failed"):
            raise ProjectVersionStoreError("unsupported transaction status")
        payload: dict[str, Any] = {
            "schema": VERSION_TRANSACTION_SCHEMA,
            "owner_key": reservation.owner_key,
            "repo_id": reservation.repo_id,
            "transaction_id": reservation.transaction_id,
            "version_id": reservation.version_id,
            "created_at": reservation.created_at,
            "request_fingerprint": reservation.request_fingerprint,
            "status": status,
        }
        if manifest_sha256:
            if not _SHA256_RE.fullmatch(manifest_sha256):
                raise ProjectVersionStoreError("manifest_sha256 is invalid")
            payload["manifest_sha256"] = manifest_sha256
        if commit_sha:
            payload["commit_sha"] = validate_commit_sha(commit_sha)
        if failure_code:
            payload["failure_code"] = validate_persisted_text(failure_code, field_name="failure_code", max_len=80)
        return payload

    def _validate_idempotency_identity(
        self,
        record: Mapping[str, Any],
        *,
        owner_key: str,
        repo_id: str,
        key_digest: str,
        fingerprint: str,
    ) -> None:
        if (
            record.get("schema") != VERSION_IDEMPOTENCY_SCHEMA
            or record.get("owner_key") != owner_key
            or record.get("repo_id") != repo_id
            or record.get("idempotency_digest") != key_digest
        ):
            raise ProjectVersionIntegrityError("idempotency record identity is invalid")
        if record.get("request_fingerprint") != fingerprint:
            raise ProjectVersionConflictError("idempotency key was already used for a different request")
        if record.get("status") not in ("created", "stored"):
            raise ProjectVersionIntegrityError("idempotency record status is invalid")

    def _read_json(self, path: Path, *, field_name: str) -> dict[str, Any]:
        checked = self._checked_store_path(path)
        if not checked.is_file():
            raise ProjectVersionIntegrityError(f"{field_name} is missing")
        return self._decode_json(checked.read_bytes(), field_name=field_name)

    @staticmethod
    def _decode_json(raw: bytes, *, field_name: str) -> dict[str, Any]:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProjectVersionIntegrityError(f"{field_name} is not valid UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise ProjectVersionIntegrityError(f"{field_name} must be a JSON object")
        return payload

    def _atomic_write(self, path: Path, payload: Mapping[str, Any]) -> None:
        self._atomic_write_bytes(path, canonical_json_bytes(payload))

    def _atomic_write_bytes(self, path: Path, payload: bytes) -> None:
        checked = self._checked_store_path(path)
        checked.parent.mkdir(parents=True, exist_ok=True)
        temp = self._checked_store_path(checked.parent / f".{checked.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temp.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, checked)
            _fsync_directory(checked.parent)
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass

    def _exclusive_atomic_write(self, path: Path, payload: Mapping[str, Any]) -> None:
        checked = self._checked_store_path(path)
        checked.parent.mkdir(parents=True, exist_ok=True)
        temp = self._checked_store_path(checked.parent / f".{checked.name}.{uuid.uuid4().hex}.exclusive.tmp")
        try:
            with temp.open("xb") as handle:
                handle.write(canonical_json_bytes(payload))
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temp, checked)
            _fsync_directory(checked.parent)
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass


def _normalize_change_notes(values: Iterable[Any]) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise ProjectVersionStoreError("change_notes must be a list")
    notes: list[str] = []
    for value in values:
        note = validate_persisted_text(value, field_name="change_note", max_len=500, multiline=True)
        if note not in notes:
            notes.append(note)
    if len(notes) > 80:
        raise ProjectVersionStoreError("change_notes exceeds max length 80")
    return notes


def _normalize_policy_snapshot(value: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _normalize_safe_json(value, field_name="policy_snapshot", block_policy_keys=True)
    if not isinstance(normalized, dict):
        raise ProjectVersionStoreError("policy_snapshot must be a mapping")
    return normalized


def _normalize_safe_json(
    value: Any,
    *,
    field_name: str,
    block_policy_keys: bool = False,
    depth: int = 0,
) -> Any:
    if depth > 8:
        raise ProjectVersionStoreError(f"{field_name} exceeds maximum nesting")
    if value is None or type(value) in (bool, int):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ProjectVersionStoreError(f"{field_name} contains a non-finite number")
        return value
    if isinstance(value, str):
        return validate_persisted_text(value, field_name=field_name, allow_empty=True, max_len=1000, multiline=True)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = validate_persisted_text(raw_key, field_name=f"{field_name} key", max_len=80)
            if block_policy_keys and _FORBIDDEN_POLICY_KEY_RE.search(key):
                raise ProjectVersionStoreError(f"{field_name} contains unsafe provider or secret data")
            if key in result:
                raise ProjectVersionStoreError(f"{field_name} contains duplicate keys")
            result[key] = _normalize_safe_json(
                raw_value,
                field_name=f"{field_name}.{key}",
                block_policy_keys=block_policy_keys,
                depth=depth + 1,
            )
        return result
    if isinstance(value, (list, tuple)):
        if len(value) > 200:
            raise ProjectVersionStoreError(f"{field_name} exceeds max length 200")
        return [
            _normalize_safe_json(
                item,
                field_name=f"{field_name} item",
                block_policy_keys=block_policy_keys,
                depth=depth + 1,
            )
            for item in value
        ]
    raise ProjectVersionStoreError(f"{field_name} contains unsupported JSON values")


def _normalize_artifacts(values: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(values, (str, bytes, Mapping)):
        raise ProjectVersionStoreError("artifacts must be a list")
    artifacts: list[dict[str, Any]] = []
    paths: set[str] = set()
    for index, value in enumerate(values):
        if not isinstance(value, Mapping):
            raise ProjectVersionStoreError("artifact metadata must be a mapping")
        data = dict(value)
        unknown = sorted(set(data) - _ARTIFACT_FIELDS)
        if unknown:
            raise ProjectVersionStoreError(f"artifact metadata contains unknown fields: {', '.join(unknown)}")
        if not {"path", "sha256", "size"}.issubset(data):
            raise ProjectVersionStoreError("artifact metadata requires path, sha256, and size")
        path = validate_repo_relative_path(data["path"], field_name=f"artifacts[{index}].path")
        if path in paths:
            raise ProjectVersionStoreError(f"duplicate artifact path: {path}")
        paths.add(path)
        digest = str(data["sha256"] or "").strip()
        if re.fullmatch(r"[0-9a-f]{64}", digest):
            digest = "sha256:" + digest
        if not _SHA256_RE.fullmatch(digest):
            raise ProjectVersionStoreError("artifact sha256 must be sha256: followed by 64 lowercase hex characters")
        size = data["size"]
        if type(size) is not int or size < 0 or size > 2**63 - 1:
            raise ProjectVersionStoreError("artifact size must be a non-negative integer")
        item: dict[str, Any] = {"path": path, "sha256": digest, "size": size}
        for optional, limit in (("media_type", 120), ("kind", 80)):
            if optional in data:
                item[optional] = validate_persisted_text(
                    data[optional],
                    field_name=f"artifact.{optional}",
                    max_len=limit,
                )
        artifacts.append(item)
    if len(artifacts) > 500:
        raise ProjectVersionStoreError("artifacts exceeds max length 500")
    return artifacts


def _validate_timestamp(value: Any) -> str:
    timestamp = str(value or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", timestamp):
        raise ProjectVersionStoreError("created_at must be UTC RFC3339 seconds ending in Z")
    try:
        parsed = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ProjectVersionStoreError("created_at is not a valid UTC timestamp") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != timestamp:
        raise ProjectVersionStoreError("created_at is not canonical UTC RFC3339")
    return timestamp


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
