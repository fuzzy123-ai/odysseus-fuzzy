"""Deterministic one-way projection plans for Codebase Memory.

Repo Registry, Project Versioning, and USI records are inputs only.  This
module owns rebuildable in-memory projection generations and has no filesystem,
engine, process, network, or canonical-store write path.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import hashlib
import json
import re
from threading import RLock
from typing import Any, Iterable, Mapping

from src.code_intelligence_contract import CodeFileMapping
from src.codebase_memory_process import CBM_LOCKED_COMMIT, CBM_LOCKED_VERSION
from src.project_version_store import (
    StoredProjectVersion,
    VERSION_MANIFEST_SCHEMA,
    canonical_json_bytes,
    owner_key_for,
    validate_commit_sha,
    validate_repo_id,
    validate_transaction_id,
    validate_version_id,
)
from src.repo_registry import RepoRecord, RepoRegistry
from src.unified_source_index_contract import (
    ContentPolicy,
    EvidenceRef,
    ProjectionKind,
    ProjectionManifest,
    canonical_json,
)


CBM_PROJECTION_SCHEMA = "odysseus.codebase_memory.projection.v1"
MAX_FILE_MAPPINGS = 100_000
MAX_INPUT_EVIDENCE = 4096

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SNAPSHOT_RE = re.compile(r"^usi_snapshot_[0-9a-f]{64}$")
_GENERATION_RE = re.compile(r"^cbm_generation_[0-9a-f]{64}$")
_PLAN_RE = re.compile(r"^cbm_plan_[0-9a-f]{64}$")
_PROJECT_RE = re.compile(r"^cbm_project_[0-9a-f]{64}$")
_ERROR_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class CodebaseMemoryProjectionError(ValueError):
    """Raised when projection state attempts ambiguity or reverse authority."""


class GenerationState(StrEnum):
    PREPARED = "prepared"
    ACTIVE = "active"
    STALE = "stale"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class EngineProjectConfig:
    repo_id: str
    project_key: str
    source_root_ref: str
    checkout_commit: str
    input_snapshot_ref: str
    input_record_ids: tuple[str, ...]
    file_mapping_keys: tuple[str, ...]
    locked_engine_version: str
    locked_engine_commit: str
    auto_watch: bool
    auto_index: bool
    ui_enabled: bool
    update_check: bool
    network_egress: bool
    source_writes: bool
    config_writes: bool
    hook_writes: bool
    shared_graph_export: bool
    semantic_model: bool
    config_hash: str = ""

    def __post_init__(self) -> None:
        repo_id = validate_repo_id(self.repo_id)
        if not _PROJECT_RE.fullmatch(self.project_key):
            raise CodebaseMemoryProjectionError("project_key is invalid")
        if not re.fullmatch(r"path_sha256:[0-9a-f]{64}", self.source_root_ref):
            raise CodebaseMemoryProjectionError("source_root_ref is invalid")
        commit = validate_commit_sha(self.checkout_commit)
        snapshot = _snapshot_ref(self.input_snapshot_ref)
        input_ids = _sorted_unique(self.input_record_ids, "input_record_ids", MAX_INPUT_EVIDENCE)
        mapping_keys = _sorted_unique(
            self.file_mapping_keys, "file_mapping_keys", MAX_FILE_MAPPINGS
        )
        if not input_ids or not mapping_keys:
            raise CodebaseMemoryProjectionError("engine config inputs must be non-empty")
        if self.locked_engine_version != CBM_LOCKED_VERSION or self.locked_engine_commit != CBM_LOCKED_COMMIT:
            raise CodebaseMemoryProjectionError("engine config does not match the vendor lock")
        controls = (
            self.auto_watch,
            self.auto_index,
            self.ui_enabled,
            self.update_check,
            self.network_egress,
            self.source_writes,
            self.config_writes,
            self.hook_writes,
            self.shared_graph_export,
            self.semantic_model,
        )
        if any(not isinstance(value, bool) for value in controls) or any(controls):
            raise CodebaseMemoryProjectionError("every mutating or external engine control must be false")
        core = {
            "schema": f"{CBM_PROJECTION_SCHEMA}.engine_config",
            "repo_id": repo_id,
            "project_key": self.project_key,
            "source_root_ref": self.source_root_ref,
            "checkout_commit": commit,
            "input_snapshot_ref": snapshot,
            "input_record_ids": input_ids,
            "file_mapping_keys": mapping_keys,
            "locked_engine_version": self.locked_engine_version,
            "locked_engine_commit": self.locked_engine_commit,
            "controls": {
                "auto_watch": False,
                "auto_index": False,
                "ui_enabled": False,
                "update_check": False,
                "network_egress": False,
                "source_writes": False,
                "config_writes": False,
                "hook_writes": False,
                "shared_graph_export": False,
                "semantic_model": False,
            },
        }
        expected = _digest(core)
        if self.config_hash not in ("", expected):
            raise CodebaseMemoryProjectionError("config_hash does not match canonical engine config")
        object.__setattr__(self, "repo_id", repo_id)
        object.__setattr__(self, "checkout_commit", commit)
        object.__setattr__(self, "input_snapshot_ref", snapshot)
        object.__setattr__(self, "input_record_ids", input_ids)
        object.__setattr__(self, "file_mapping_keys", mapping_keys)
        object.__setattr__(self, "config_hash", expected)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": f"{CBM_PROJECTION_SCHEMA}.engine_config",
            "repo_id": self.repo_id,
            "project_key": self.project_key,
            "source_root_ref": self.source_root_ref,
            "checkout_commit": self.checkout_commit,
            "input_snapshot_ref": self.input_snapshot_ref,
            "input_record_ids": list(self.input_record_ids),
            "file_mapping_keys": list(self.file_mapping_keys),
            "locked_engine_version": self.locked_engine_version,
            "locked_engine_commit": self.locked_engine_commit,
            "controls": {
                "auto_watch": self.auto_watch,
                "auto_index": self.auto_index,
                "ui_enabled": self.ui_enabled,
                "update_check": self.update_check,
                "network_egress": self.network_egress,
                "source_writes": self.source_writes,
                "config_writes": self.config_writes,
                "hook_writes": self.hook_writes,
                "shared_graph_export": self.shared_graph_export,
                "semantic_model": self.semantic_model,
            },
            "config_hash": self.config_hash,
        }


@dataclass(frozen=True, slots=True)
class ProjectionPlan:
    repo_id: str
    owner_key: str
    project_version_id: str
    commit_sha: str
    registry_digest: str
    version_manifest_digest: str
    usi_input_digest: str
    authority_digest: str
    generation_ref: str
    plan_id: str
    engine_config: EngineProjectConfig
    usi_manifest: ProjectionManifest

    def __post_init__(self) -> None:
        validate_repo_id(self.repo_id)
        if not re.fullmatch(r"own_[0-9a-f]{32}", self.owner_key):
            raise CodebaseMemoryProjectionError("owner_key is invalid")
        validate_version_id(self.project_version_id)
        validate_commit_sha(self.commit_sha)
        for value, label in (
            (self.registry_digest, "registry_digest"),
            (self.version_manifest_digest, "version_manifest_digest"),
            (self.usi_input_digest, "usi_input_digest"),
            (self.authority_digest, "authority_digest"),
        ):
            if not _SHA256_RE.fullmatch(value):
                raise CodebaseMemoryProjectionError(f"{label} is invalid")
        if not _GENERATION_RE.fullmatch(self.generation_ref) or not _PLAN_RE.fullmatch(self.plan_id):
            raise CodebaseMemoryProjectionError("plan generation identity is invalid")
        if not isinstance(self.engine_config, EngineProjectConfig):
            raise CodebaseMemoryProjectionError("engine_config must be typed")
        if not isinstance(self.usi_manifest, ProjectionManifest):
            raise CodebaseMemoryProjectionError("usi_manifest must be typed")
        if (
            self.engine_config.repo_id != self.repo_id
            or self.engine_config.checkout_commit != self.commit_sha
            or self.engine_config.config_hash != self.usi_manifest.config_hash
            or self.engine_config.input_snapshot_ref != self.usi_manifest.input_snapshot_ref
            or self.usi_manifest.output_generation_ref != self.generation_ref
        ):
            raise CodebaseMemoryProjectionError("plan components do not share one identity")
        expected_authority = _digest(
            {
                "registry_digest": self.registry_digest,
                "version_manifest_digest": self.version_manifest_digest,
                "usi_input_digest": self.usi_input_digest,
            }
        )
        if self.authority_digest != expected_authority:
            raise CodebaseMemoryProjectionError("authority_digest does not match canonical inputs")
        expected_project = _id(
            "project", {"owner_key": self.owner_key, "repo_id": self.repo_id}
        )
        if self.engine_config.project_key != expected_project:
            raise CodebaseMemoryProjectionError("project_key does not match registry identity")
        expected_generation = _id(
            "generation",
            {
                "repo_id": self.repo_id,
                "project_version_id": self.project_version_id,
                "commit_sha": self.commit_sha,
                "config_hash": self.engine_config.config_hash,
                "usi_input_digest": self.usi_input_digest,
            },
        )
        if self.generation_ref != expected_generation:
            raise CodebaseMemoryProjectionError("generation_ref does not match canonical inputs")
        manifest_ids = tuple(sorted(item.record_id for item in self.usi_manifest.input_evidence))
        if manifest_ids != self.engine_config.input_record_ids:
            raise CodebaseMemoryProjectionError("USI manifest inputs do not match engine config")
        expected_plan = _id(
            "plan",
            {
                "repo_id": self.repo_id,
                "project_version_id": self.project_version_id,
                "authority_digest": self.authority_digest,
                "generation_ref": self.generation_ref,
                "projection_id": self.usi_manifest.projection_id,
            },
        )
        if self.plan_id != expected_plan:
            raise CodebaseMemoryProjectionError("plan_id does not match canonical identity")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": f"{CBM_PROJECTION_SCHEMA}.plan",
            "repo_id": self.repo_id,
            "owner_key": self.owner_key,
            "project_version_id": self.project_version_id,
            "commit_sha": self.commit_sha,
            "registry_digest": self.registry_digest,
            "version_manifest_digest": self.version_manifest_digest,
            "usi_input_digest": self.usi_input_digest,
            "authority_digest": self.authority_digest,
            "generation_ref": self.generation_ref,
            "plan_id": self.plan_id,
            "engine_config": self.engine_config.to_dict(),
            "usi_manifest": self.usi_manifest.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ProjectionGeneration:
    plan: ProjectionPlan
    state: GenerationState
    previous_generation_ref: str = ""
    error_code: str = ""
    state_revision: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.plan, ProjectionPlan):
            raise CodebaseMemoryProjectionError("generation plan must be typed")
        if not isinstance(self.state, GenerationState):
            raise CodebaseMemoryProjectionError("generation state must be typed")
        if self.previous_generation_ref and not _GENERATION_RE.fullmatch(self.previous_generation_ref):
            raise CodebaseMemoryProjectionError("previous_generation_ref is invalid")
        if self.error_code and not _ERROR_RE.fullmatch(self.error_code):
            raise CodebaseMemoryProjectionError("error_code is invalid")
        if self.state is GenerationState.FAILED and not self.error_code:
            raise CodebaseMemoryProjectionError("failed generation requires error_code")
        if self.state is not GenerationState.FAILED and self.error_code:
            raise CodebaseMemoryProjectionError("only failed generation may carry error_code")
        if isinstance(self.state_revision, bool) or self.state_revision < 1:
            raise CodebaseMemoryProjectionError("state_revision must be positive")

    @property
    def generation_ref(self) -> str:
        return self.plan.generation_ref

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": f"{CBM_PROJECTION_SCHEMA}.generation",
            "generation_ref": self.generation_ref,
            "plan_id": self.plan.plan_id,
            "projection_id": self.plan.usi_manifest.projection_id,
            "state": self.state.value,
            "previous_generation_ref": self.previous_generation_ref,
            "error_code": self.error_code,
            "state_revision": self.state_revision,
        }


@dataclass(frozen=True, slots=True)
class ProjectionDeletionReceipt:
    deleted_generation_count: int
    deleted_generation_digest: str
    active_generation_before: str
    canonical_writes: int = 0

    def __post_init__(self) -> None:
        if (
            isinstance(self.deleted_generation_count, bool)
            or self.deleted_generation_count < 0
            or self.canonical_writes != 0
        ):
            raise CodebaseMemoryProjectionError("deletion receipt is invalid")
        if not _SHA256_RE.fullmatch(self.deleted_generation_digest):
            raise CodebaseMemoryProjectionError("deleted_generation_digest is invalid")
        if self.active_generation_before and not _GENERATION_RE.fullmatch(
            self.active_generation_before
        ):
            raise CodebaseMemoryProjectionError("active_generation_before is invalid")


class OneWayRepositoryBridge:
    """Read canonical authorities and emit an immutable derivative plan."""

    def create_plan(
        self,
        registry: RepoRegistry,
        *,
        repo_id: str,
        project_version: StoredProjectVersion,
        input_snapshot_ref: str,
        file_mappings: Iterable[CodeFileMapping],
        input_evidence: Iterable[EvidenceRef],
    ) -> ProjectionPlan:
        if not isinstance(registry, RepoRegistry):
            raise CodebaseMemoryProjectionError("registry must be RepoRegistry")
        repo = registry.get(repo_id)
        before_registry = _registry_digest(registry)
        owner_key, version_id, commit, version_digest = _validated_version(repo, project_version)
        snapshot = _snapshot_ref(input_snapshot_ref)
        mappings = _file_mappings(file_mappings, repo, commit)
        evidence = _input_evidence(input_evidence, mappings)
        usi_digest = _digest(
            {
                "input_snapshot_ref": snapshot,
                "file_mappings": [item.to_dict() for item in mappings],
                "evidence": [item.to_dict() for item in evidence],
            }
        )
        authority_digest = _digest(
            {
                "registry_digest": before_registry,
                "version_manifest_digest": version_digest,
                "usi_input_digest": usi_digest,
            }
        )
        project_key = _id("project", {"owner_key": owner_key, "repo_id": repo.repo_id})
        config = EngineProjectConfig(
            repo_id=repo.repo_id,
            project_key=project_key,
            source_root_ref=_path_ref(repo.path_ref),
            checkout_commit=commit,
            input_snapshot_ref=snapshot,
            input_record_ids=tuple(item.record_id for item in evidence),
            file_mapping_keys=tuple(item.fallback_key for item in mappings),
            locked_engine_version=CBM_LOCKED_VERSION,
            locked_engine_commit=CBM_LOCKED_COMMIT,
            auto_watch=False,
            auto_index=False,
            ui_enabled=False,
            update_check=False,
            network_egress=False,
            source_writes=False,
            config_writes=False,
            hook_writes=False,
            shared_graph_export=False,
            semantic_model=False,
        )
        generation_ref = _id(
            "generation",
            {
                "repo_id": repo.repo_id,
                "project_version_id": version_id,
                "commit_sha": commit,
                "config_hash": config.config_hash,
                "usi_input_digest": usi_digest,
            },
        )
        manifest = ProjectionManifest.create(
            projection_kind=ProjectionKind.CODE_GRAPH,
            projection_profile_ref="cbm-code-graph@0.9.0",
            input_snapshot_ref=snapshot,
            config_hash=config.config_hash,
            input_evidence=evidence,
            implementation_ref="codebase-memory",
            implementation_version=CBM_LOCKED_VERSION,
            output_generation_ref=generation_ref,
            content_policy=ContentPolicy.METADATA_ONLY,
            indexed_at="",
        )
        plan_id = _id(
            "plan",
            {
                "repo_id": repo.repo_id,
                "project_version_id": version_id,
                "authority_digest": authority_digest,
                "generation_ref": generation_ref,
                "projection_id": manifest.projection_id,
            },
        )
        if _registry_digest(registry) != before_registry:
            raise CodebaseMemoryProjectionError("registry changed while building projection plan")
        return ProjectionPlan(
            repo.repo_id,
            owner_key,
            version_id,
            commit,
            before_registry,
            version_digest,
            usi_digest,
            authority_digest,
            generation_ref,
            plan_id,
            config,
            manifest,
        )

    def verify_authority_unchanged(
        self,
        plan: ProjectionPlan,
        registry: RepoRegistry,
        *,
        project_version: StoredProjectVersion,
        file_mappings: Iterable[CodeFileMapping],
        input_evidence: Iterable[EvidenceRef],
    ) -> bool:
        if not isinstance(plan, ProjectionPlan) or not isinstance(registry, RepoRegistry):
            raise CodebaseMemoryProjectionError("authority verification requires typed inputs")
        repo = registry.get(plan.repo_id)
        _owner, _version, commit, version_digest = _validated_version(repo, project_version)
        mappings = _file_mappings(file_mappings, repo, commit)
        evidence = _input_evidence(input_evidence, mappings)
        usi_digest = _digest(
            {
                "input_snapshot_ref": plan.engine_config.input_snapshot_ref,
                "file_mappings": [item.to_dict() for item in mappings],
                "evidence": [item.to_dict() for item in evidence],
            }
        )
        return (
            _registry_digest(registry) == plan.registry_digest
            and version_digest == plan.version_manifest_digest
            and usi_digest == plan.usi_input_digest
        )

    def reject_engine_registration(self, _engine_payload: Mapping[str, Any]) -> None:
        raise CodebaseMemoryProjectionError(
            "reverse authority is forbidden: engine projects cannot register canonical repositories"
        )


class ProjectionGenerationStore:
    """In-memory transactional pointer over independently deletable derivatives."""

    def __init__(self) -> None:
        self._records: dict[str, ProjectionGeneration] = {}
        self._active_ref = ""
        self._lock = RLock()

    @property
    def active_generation_ref(self) -> str:
        with self._lock:
            return self._active_ref

    def prepare(self, plan: ProjectionPlan) -> ProjectionGeneration:
        if not isinstance(plan, ProjectionPlan):
            raise CodebaseMemoryProjectionError("plan must be ProjectionPlan")
        with self._lock:
            current = self._records.get(plan.generation_ref)
            if current is not None:
                if current.plan != plan:
                    raise CodebaseMemoryProjectionError("generation identity collision")
                return current
            record = ProjectionGeneration(plan, GenerationState.PREPARED)
            self._records[plan.generation_ref] = record
            return record

    def activate(
        self,
        generation_ref: str,
        *,
        expected_active_ref: str,
    ) -> ProjectionGeneration:
        generation_ref = _generation_ref(generation_ref)
        if expected_active_ref:
            expected_active_ref = _generation_ref(expected_active_ref)
        with self._lock:
            if self._active_ref != expected_active_ref:
                raise CodebaseMemoryProjectionError("active generation compare-and-switch conflict")
            candidate = self._required(generation_ref)
            if candidate.state not in {GenerationState.PREPARED, GenerationState.ACTIVE}:
                raise CodebaseMemoryProjectionError("only a prepared generation may activate")
            if candidate.state is GenerationState.ACTIVE:
                return candidate
            previous = self._active_ref
            if previous:
                old = self._required(previous)
                self._records[previous] = replace(
                    old,
                    state=GenerationState.STALE,
                    error_code="",
                    state_revision=old.state_revision + 1,
                )
            active = replace(
                candidate,
                state=GenerationState.ACTIVE,
                previous_generation_ref=previous,
                state_revision=candidate.state_revision + 1,
            )
            self._records[generation_ref] = active
            self._active_ref = generation_ref
            return active

    def mark_stale(self, generation_ref: str) -> ProjectionGeneration:
        generation_ref = _generation_ref(generation_ref)
        with self._lock:
            current = self._required(generation_ref)
            if current.state not in {GenerationState.ACTIVE, GenerationState.PREPARED}:
                raise CodebaseMemoryProjectionError("generation cannot become stale from its current state")
            stale = replace(
                current,
                state=GenerationState.STALE,
                error_code="",
                state_revision=current.state_revision + 1,
            )
            self._records[generation_ref] = stale
            return stale

    def mark_failed(self, generation_ref: str, *, error_code: str) -> ProjectionGeneration:
        generation_ref = _generation_ref(generation_ref)
        code = _error_code(error_code)
        with self._lock:
            current = self._required(generation_ref)
            if current.state is not GenerationState.PREPARED:
                raise CodebaseMemoryProjectionError("only a prepared generation may fail")
            failed = replace(
                current,
                state=GenerationState.FAILED,
                error_code=code,
                state_revision=current.state_revision + 1,
            )
            self._records[generation_ref] = failed
            return failed

    def get(self, generation_ref: str) -> ProjectionGeneration:
        with self._lock:
            return self._required(_generation_ref(generation_ref))

    def list(self) -> tuple[ProjectionGeneration, ...]:
        with self._lock:
            return tuple(self._records[key] for key in sorted(self._records))

    def delete_all(self, *, expected_active_ref: str) -> ProjectionDeletionReceipt:
        if expected_active_ref:
            expected_active_ref = _generation_ref(expected_active_ref)
        with self._lock:
            if self._active_ref != expected_active_ref:
                raise CodebaseMemoryProjectionError("delete compare-and-switch conflict")
            refs = tuple(sorted(self._records))
            receipt = ProjectionDeletionReceipt(
                len(refs),
                _digest({"generation_refs": refs}),
                self._active_ref,
            )
            self._records.clear()
            self._active_ref = ""
            return receipt

    def _required(self, generation_ref: str) -> ProjectionGeneration:
        try:
            return self._records[generation_ref]
        except KeyError as exc:
            raise CodebaseMemoryProjectionError("unknown projection generation") from exc


def _validated_version(
    repo: RepoRecord, stored: StoredProjectVersion
) -> tuple[str, str, str, str]:
    if not isinstance(repo, RepoRecord) or not isinstance(stored, StoredProjectVersion):
        raise CodebaseMemoryProjectionError("repository version inputs must be typed")
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
    if not required.issubset(manifest) or set(manifest) - (
        required | {"version_label", "change_notes"}
    ):
        raise CodebaseMemoryProjectionError("project version manifest fields are invalid")
    if manifest.get("schema") != VERSION_MANIFEST_SCHEMA:
        raise CodebaseMemoryProjectionError("project version schema is invalid")
    owner_key = owner_key_for(repo.owner)
    if manifest.get("owner_key") != owner_key or manifest.get("repo_id") != repo.repo_id:
        raise CodebaseMemoryProjectionError("project version does not belong to registry repository")
    validate_transaction_id(manifest.get("transaction_id"))
    version_id = validate_version_id(manifest.get("version_id"))
    commit = validate_commit_sha(manifest.get("commit_sha"))
    raw = canonical_json_bytes(manifest)
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    if stored.manifest_sha256 != digest:
        raise CodebaseMemoryProjectionError("project version manifest digest is invalid")
    return owner_key, version_id, commit, digest


def _file_mappings(
    values: Iterable[CodeFileMapping], repo: RepoRecord, commit: str
) -> tuple[CodeFileMapping, ...]:
    try:
        items = tuple(values)
    except TypeError as exc:
        raise CodebaseMemoryProjectionError("file_mappings must be iterable") from exc
    if not items or len(items) > MAX_FILE_MAPPINGS or not all(
        isinstance(item, CodeFileMapping) for item in items
    ):
        raise CodebaseMemoryProjectionError("file_mappings must be non-empty typed and bounded")
    by_key = {item.fallback_key: item for item in items}
    if len(by_key) != len(items):
        raise CodebaseMemoryProjectionError("file_mappings contain duplicate identities")
    expected_revision = f"git:{commit}"
    for item in items:
        if item.repo_id != repo.repo_id or item.owner_scope != _owner_scope_for_repo(repo):
            raise CodebaseMemoryProjectionError("file mapping escapes registry repository owner")
        if item.revision_ref != expected_revision:
            raise CodebaseMemoryProjectionError("file mapping revision does not match project version")
    return tuple(by_key[key] for key in sorted(by_key))


def _input_evidence(
    values: Iterable[EvidenceRef], mappings: tuple[CodeFileMapping, ...]
) -> tuple[EvidenceRef, ...]:
    try:
        items = tuple(values)
    except TypeError as exc:
        raise CodebaseMemoryProjectionError("input_evidence must be iterable") from exc
    if not items or len(items) > MAX_INPUT_EVIDENCE or not all(
        isinstance(item, EvidenceRef) for item in items
    ):
        raise CodebaseMemoryProjectionError("input_evidence must be non-empty typed and bounded")
    by_id = {item.record_id: item for item in items}
    if len(by_id) != len(items):
        raise CodebaseMemoryProjectionError("input_evidence contains duplicate identities")
    ancestry = {(item.source_id, item.source_version_id) for item in mappings}
    if any((item.source_id, item.source_version_id) not in ancestry for item in items):
        raise CodebaseMemoryProjectionError("input evidence escapes mapped source versions")
    evidenced_ancestry = {(item.source_id, item.source_version_id) for item in items}
    if ancestry - evidenced_ancestry:
        raise CodebaseMemoryProjectionError("every mapped source version requires input evidence")
    return tuple(by_id[key] for key in sorted(by_id))


def _registry_digest(registry: RepoRegistry) -> str:
    return _digest(registry.to_dict())


def _digest(value: Any) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return "sha256:" + hashlib.sha256(rendered).hexdigest()


def _id(kind: str, key: Mapping[str, Any]) -> str:
    return f"cbm_{kind}_" + hashlib.sha256(
        canonical_json(
            {"schema": CBM_PROJECTION_SCHEMA, "kind": kind, "key": key}
        ).encode("utf-8")
    ).hexdigest()


def _path_ref(value: str) -> str:
    normalized = value.replace("\\", "/")
    return "path_sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _owner_scope_for_repo(repo: RepoRecord) -> str:
    if re.fullmatch(r"[a-z][a-z0-9_-]{0,31}:[A-Za-z0-9][A-Za-z0-9._/@+-]{0,159}", repo.owner):
        return repo.owner
    return f"user:{repo.owner}"


def _snapshot_ref(value: Any) -> str:
    if not isinstance(value, str) or not _SNAPSHOT_RE.fullmatch(value):
        raise CodebaseMemoryProjectionError("input_snapshot_ref is invalid")
    return value


def _generation_ref(value: Any) -> str:
    if not isinstance(value, str) or not _GENERATION_RE.fullmatch(value):
        raise CodebaseMemoryProjectionError("generation_ref is invalid")
    return value


def _error_code(value: Any) -> str:
    if not isinstance(value, str) or not _ERROR_RE.fullmatch(value):
        raise CodebaseMemoryProjectionError("error_code is invalid")
    return value


def _sorted_unique(values: Iterable[Any], field_name: str, maximum: int) -> tuple[str, ...]:
    try:
        items = tuple(values)
    except TypeError as exc:
        raise CodebaseMemoryProjectionError(f"{field_name} must be iterable") from exc
    if not items or len(items) > maximum or not all(isinstance(item, str) and item for item in items):
        raise CodebaseMemoryProjectionError(f"{field_name} must be non-empty and bounded")
    unique = tuple(sorted(set(items)))
    if len(unique) != len(items):
        raise CodebaseMemoryProjectionError(f"{field_name} contains duplicates")
    return unique
