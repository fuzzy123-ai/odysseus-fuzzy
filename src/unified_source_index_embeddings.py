"""Rebuildable semantic projection contract and fake Chroma bridge for USI.

The module deliberately contains no Chroma import or live collection access.
USI chunk occurrences remain canonical truth; embedding generations are
bounded, replaceable accelerators with explicit manifests and health states.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import math
import re
from types import MappingProxyType
from typing import Mapping, Protocol, runtime_checkable

from src.unified_source_index_contract import (
    ChunkRecord,
    ContentPolicy,
    ProjectionKind,
    ProjectionManifest,
    RecordKind,
    canonical_json,
)
from src.unified_source_index_stores import (
    MAX_PAGE_SIZE,
    StoreSnapshot,
    StoredRecord,
    TransactionalStore,
    _owner_scope,
)


MAX_EMBEDDING_DIMENSIONS = 4_096
MAX_EMBEDDING_BATCH = 256
MAX_EMBEDDING_INPUTS = 999
MAX_EMBEDDING_RETRIES = 8

_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_GENERATION_RE = re.compile(r"^embedding:[0-9a-f]{64}$")
_METADATA_KEYS = frozenset(
    {
        "chunk_id",
        "source_id",
        "source_version_id",
        "owner_scope",
        "content_hash",
        "record_revision",
        "generation_ref",
        "config_hash",
    }
)


class EmbeddingProjectionError(RuntimeError):
    """Raised for invalid semantic projection contracts or inputs."""


class EmbeddingSinkUnavailable(EmbeddingProjectionError):
    """Raised by a semantic sink that cannot currently serve requests."""


class EmbeddingProjectionStatus(StrEnum):
    READY = "ready"
    PARTIAL = "partial"
    EMPTY = "empty"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class EmbeddingGenerationHealth(StrEnum):
    CURRENT = "current"
    STALE_GENERATION = "stale_generation"
    COUNT_MISMATCH = "count_mismatch"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    profile_ref: str
    model_ref: str
    model_version: str
    dimensions: int
    normalize: bool = True

    def __post_init__(self) -> None:
        for field_name in ("profile_ref", "model_ref", "model_version"):
            object.__setattr__(self, field_name, _token(getattr(self, field_name), field_name))
        object.__setattr__(
            self,
            "dimensions",
            _integer(
                self.dimensions,
                "dimensions",
                minimum=1,
                maximum=MAX_EMBEDDING_DIMENSIONS,
            ),
        )
        if not isinstance(self.normalize, bool):
            raise EmbeddingProjectionError("normalize must be boolean")

    @property
    def config_hash(self) -> str:
        return _fingerprint(
            {
                "profile_ref": self.profile_ref,
                "model_ref": self.model_ref,
                "model_version": self.model_version,
                "dimensions": self.dimensions,
                "normalize": self.normalize,
            }
        )


@dataclass(frozen=True, slots=True)
class EmbeddingPoint:
    point_id: str
    vector: tuple[float, ...]
    metadata: Mapping[str, str | int]

    def __post_init__(self) -> None:
        if not isinstance(self.point_id, str):
            raise EmbeddingProjectionError("point_id must identify a USI chunk")
        # Reuse the frozen record validator without importing backend types.
        from src.unified_source_index_contract import RecordRef

        point_id = RecordRef(RecordKind.CHUNK, self.point_id).record_id
        object.__setattr__(self, "point_id", point_id)
        if (
            not isinstance(self.vector, tuple)
            or not self.vector
            or len(self.vector) > MAX_EMBEDDING_DIMENSIONS
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in self.vector
            )
        ):
            raise EmbeddingProjectionError("embedding vector is invalid or unbounded")
        object.__setattr__(self, "vector", tuple(float(value) for value in self.vector))
        if not isinstance(self.metadata, Mapping) or set(self.metadata) != _METADATA_KEYS:
            raise EmbeddingProjectionError("embedding metadata fields are incomplete or unknown")
        metadata = dict(self.metadata)
        if metadata["chunk_id"] != point_id:
            raise EmbeddingProjectionError("embedding point identity differs from chunk metadata")
        if not isinstance(metadata["record_revision"], int) or metadata["record_revision"] < 1:
            raise EmbeddingProjectionError("embedding record revision must be positive")
        for field_name in (
            "source_id",
            "source_version_id",
            "owner_scope",
            "content_hash",
            "generation_ref",
            "config_hash",
        ):
            if not isinstance(metadata[field_name], str):
                raise EmbeddingProjectionError(f"embedding metadata {field_name} must be text")
        from src.unified_source_index_contract import RecordRef

        RecordRef(RecordKind.SOURCE, metadata["source_id"])
        RecordRef(RecordKind.SOURCE_VERSION, metadata["source_version_id"])
        _owner_scope(metadata["owner_scope"])
        _sha256(metadata["content_hash"], "content_hash")
        _generation(metadata["generation_ref"])
        _sha256(metadata["config_hash"], "config_hash")
        object.__setattr__(self, "metadata", MappingProxyType(metadata))


@dataclass(frozen=True, slots=True)
class EmbeddingProjectionResult:
    status: EmbeddingProjectionStatus
    input_snapshot: StoreSnapshot
    generation_ref: str
    projected_count: int
    skipped_count: int
    manifest: ProjectionManifest | None = None
    error_code: str = ""
    fallback_required: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _enum(self.status, EmbeddingProjectionStatus, "status"))
        if not isinstance(self.input_snapshot, StoreSnapshot):
            raise EmbeddingProjectionError("input_snapshot must be typed")
        if self.generation_ref:
            object.__setattr__(self, "generation_ref", _generation(self.generation_ref))
        object.__setattr__(
            self,
            "projected_count",
            _integer(
                self.projected_count,
                "projected_count",
                minimum=0,
                maximum=MAX_EMBEDDING_INPUTS,
            ),
        )
        object.__setattr__(
            self,
            "skipped_count",
            _integer(
                self.skipped_count,
                "skipped_count",
                minimum=0,
                maximum=MAX_EMBEDDING_INPUTS,
            ),
        )
        if self.manifest is not None and not isinstance(self.manifest, ProjectionManifest):
            raise EmbeddingProjectionError("manifest must be typed")
        if self.error_code:
            object.__setattr__(self, "error_code", _token(self.error_code, "error_code"))
        if not isinstance(self.fallback_required, bool):
            raise EmbeddingProjectionError("fallback_required must be boolean")


@runtime_checkable
class EmbeddingEncoder(Protocol):
    @property
    def implementation_ref(self) -> str: ...

    @property
    def implementation_version(self) -> str: ...

    def encode(
        self,
        contents: tuple[str, ...],
        config: EmbeddingConfig,
    ) -> tuple[tuple[float, ...], ...]: ...


@runtime_checkable
class EmbeddingGenerationSink(Protocol):
    @property
    def sink_ref(self) -> str: ...

    @property
    def sink_version(self) -> str: ...

    def begin_generation(
        self,
        generation_ref: str,
        *,
        owner_scope: str,
        config_hash: str,
    ) -> None: ...

    def upsert_batch(
        self,
        generation_ref: str,
        points: tuple[EmbeddingPoint, ...],
    ) -> None: ...

    def activate_generation(
        self,
        *,
        owner_scope: str,
        profile_ref: str,
        generation_ref: str,
    ) -> None: ...

    def mark_failed(self, generation_ref: str, error_code: str) -> None: ...

    def delete_generation(self, generation_ref: str) -> None: ...

    def active_generation(self, *, owner_scope: str, profile_ref: str) -> str: ...

    def generation_count(self, generation_ref: str) -> int | None: ...


class DeterministicFakeEmbeddingEncoder:
    implementation_ref = "fake.embedding"
    implementation_version = "v1"

    def encode(
        self,
        contents: tuple[str, ...],
        config: EmbeddingConfig,
    ) -> tuple[tuple[float, ...], ...]:
        if not isinstance(contents, tuple) or len(contents) > MAX_EMBEDDING_BATCH:
            raise EmbeddingProjectionError("embedding contents must be a bounded tuple")
        vectors: list[tuple[float, ...]] = []
        for content in contents:
            if not isinstance(content, str):
                raise EmbeddingProjectionError("embedding content must be text")
            seed = hashlib.sha256(
                (config.config_hash + "\x00" + content).encode("utf-8")
            ).digest()
            values = [
                ((seed[index % len(seed)] / 255.0) * 2.0) - 1.0
                for index in range(config.dimensions)
            ]
            if config.normalize:
                norm = math.sqrt(sum(value * value for value in values)) or 1.0
                values = [value / norm for value in values]
            vectors.append(tuple(values))
        return tuple(vectors)


class FakeChromaGenerationSink:
    """In-memory fake with generation cutover and failure injection."""

    sink_ref = "fake.chroma"
    sink_version = "v1"

    def __init__(self) -> None:
        self.available = True
        self.fail_upserts = 0
        self.upsert_attempts = 0
        self._generations: dict[str, dict[str, EmbeddingPoint]] = {}
        self._generation_meta: dict[str, tuple[str, str]] = {}
        self._active: dict[tuple[str, str], str] = {}
        self._failed: dict[str, str] = {}

    def begin_generation(
        self,
        generation_ref: str,
        *,
        owner_scope: str,
        config_hash: str,
    ) -> None:
        self._require_available()
        generation = _generation(generation_ref)
        owner = _owner_scope(owner_scope)
        config = _sha256(config_hash, "config_hash")
        self._generations.setdefault(generation, {})
        self._generation_meta[generation] = (owner, config)
        self._failed.pop(generation, None)

    def upsert_batch(
        self,
        generation_ref: str,
        points: tuple[EmbeddingPoint, ...],
    ) -> None:
        self._require_available()
        generation = _generation(generation_ref)
        if generation not in self._generations:
            raise EmbeddingProjectionError("embedding generation was not begun")
        if not isinstance(points, tuple) or not 1 <= len(points) <= MAX_EMBEDDING_BATCH:
            raise EmbeddingProjectionError("embedding batch is empty or unbounded")
        self.upsert_attempts += 1
        if self.fail_upserts > 0:
            self.fail_upserts -= 1
            raise EmbeddingProjectionError("synthetic_upsert_failure")
        owner, config_hash = self._generation_meta[generation]
        for point in points:
            if not isinstance(point, EmbeddingPoint):
                raise EmbeddingProjectionError("embedding batch contains an invalid point")
            if (
                point.metadata["owner_scope"] != owner
                or point.metadata["config_hash"] != config_hash
                or point.metadata["generation_ref"] != generation
            ):
                raise EmbeddingProjectionError("embedding point escapes generation metadata")
            self._generations[generation][point.point_id] = point

    def activate_generation(
        self,
        *,
        owner_scope: str,
        profile_ref: str,
        generation_ref: str,
    ) -> None:
        self._require_available()
        owner = _owner_scope(owner_scope)
        profile = _token(profile_ref, "profile_ref")
        generation = _generation(generation_ref)
        if generation not in self._generations or generation in self._failed:
            raise EmbeddingProjectionError("embedding generation is not activatable")
        self._active[(owner, profile)] = generation

    def mark_failed(self, generation_ref: str, error_code: str) -> None:
        generation = _generation(generation_ref)
        self._failed[generation] = _token(error_code, "error_code")

    def delete_generation(self, generation_ref: str) -> None:
        self._require_available()
        generation = _generation(generation_ref)
        self._generations.pop(generation, None)
        self._generation_meta.pop(generation, None)
        self._failed.pop(generation, None)
        for key, active in tuple(self._active.items()):
            if active == generation:
                del self._active[key]

    def active_generation(self, *, owner_scope: str, profile_ref: str) -> str:
        self._require_available()
        return self._active.get(
            (_owner_scope(owner_scope), _token(profile_ref, "profile_ref")),
            "",
        )

    def generation_count(self, generation_ref: str) -> int | None:
        self._require_available()
        generation = _generation(generation_ref)
        points = self._generations.get(generation)
        return len(points) if points is not None else None

    def points(self, generation_ref: str) -> tuple[EmbeddingPoint, ...]:
        generation = _generation(generation_ref)
        return tuple(
            self._generations.get(generation, {}).get(point_id)
            for point_id in sorted(self._generations.get(generation, {}))
        )

    def _require_available(self) -> None:
        if not self.available:
            raise EmbeddingSinkUnavailable("fake Chroma sink is unavailable")


class UnifiedSourceIndexEmbeddingProjector:
    def __init__(
        self,
        store: TransactionalStore,
        encoder: EmbeddingEncoder,
        sink: EmbeddingGenerationSink,
    ) -> None:
        if not isinstance(store, TransactionalStore):
            raise EmbeddingProjectionError("store must implement TransactionalStore")
        if not isinstance(encoder, EmbeddingEncoder):
            raise EmbeddingProjectionError("encoder must implement EmbeddingEncoder")
        if not isinstance(sink, EmbeddingGenerationSink):
            raise EmbeddingProjectionError("sink must implement EmbeddingGenerationSink")
        self._store = store
        self._encoder = encoder
        self._sink = sink

    def rebuild(
        self,
        config: EmbeddingConfig,
        *,
        owner_scope: str,
        source_ids: tuple[str, ...] = (),
        batch_size: int = 64,
        max_chunks: int = 500,
        max_retries: int = 2,
        indexed_at: str = "",
    ) -> EmbeddingProjectionResult:
        if not isinstance(config, EmbeddingConfig):
            raise EmbeddingProjectionError("config must be typed")
        owner = _owner_scope(owner_scope)
        batch = _integer(
            batch_size,
            "batch_size",
            minimum=1,
            maximum=MAX_EMBEDDING_BATCH,
        )
        chunk_limit = _integer(
            max_chunks,
            "max_chunks",
            minimum=1,
            maximum=MAX_EMBEDDING_INPUTS,
        )
        retries = _integer(
            max_retries,
            "max_retries",
            minimum=0,
            maximum=MAX_EMBEDDING_RETRIES,
        )
        allowed_sources = _source_ids(source_ids)
        snapshot = self._store.current_snapshot()
        chunks, candidate_clipped = self._read_chunks(
            snapshot,
            owner,
            allowed_sources,
            chunk_limit,
        )
        eligible = tuple(
            item
            for item in chunks
            if item.record.content_policy is ContentPolicy.INLINE_LOCAL
            and item.record.content is not None
        )
        skipped = len(chunks) - len(eligible)
        generation = _generation_ref(snapshot, owner, config)
        if not eligible:
            return EmbeddingProjectionResult(
                EmbeddingProjectionStatus.EMPTY,
                snapshot,
                generation,
                0,
                skipped,
                fallback_required=True,
            )

        try:
            self._sink.begin_generation(
                generation,
                owner_scope=owner,
                config_hash=config.config_hash,
            )
            for offset in range(0, len(eligible), batch):
                selected = eligible[offset : offset + batch]
                contents = tuple(item.record.content or "" for item in selected)
                vectors = self._encoder.encode(contents, config)
                if len(vectors) != len(selected):
                    raise EmbeddingProjectionError("encoder output count differs from inputs")
                points = tuple(
                    _point(item, vector, generation, config)
                    for item, vector in zip(selected, vectors, strict=True)
                )
                _retry_upsert(self._sink, generation, points, retries)
            if self._store.current_snapshot() != snapshot:
                self._sink.mark_failed(generation, "input_snapshot_changed")
                return EmbeddingProjectionResult(
                    EmbeddingProjectionStatus.STALE,
                    snapshot,
                    generation,
                    len(eligible),
                    skipped,
                    error_code="input_snapshot_changed",
                    fallback_required=True,
                )
            self._sink.activate_generation(
                owner_scope=owner,
                profile_ref=config.profile_ref,
                generation_ref=generation,
            )
            manifest = ProjectionManifest.create(
                projection_kind=ProjectionKind.EMBEDDING,
                projection_profile_ref=config.profile_ref,
                input_snapshot_ref=snapshot.snapshot_ref,
                config_hash=config.config_hash,
                input_evidence=tuple(item.record.evidence_ref() for item in eligible),
                implementation_ref=f"{self._sink.sink_ref}:{self._encoder.implementation_ref}",
                implementation_version=(
                    f"{self._sink.sink_version}:{self._encoder.implementation_version}"
                ),
                output_generation_ref=generation,
                indexed_at=indexed_at,
            )
            _persist_manifest(self._store, manifest)
            partial = candidate_clipped or skipped > 0
            return EmbeddingProjectionResult(
                EmbeddingProjectionStatus.PARTIAL
                if partial
                else EmbeddingProjectionStatus.READY,
                snapshot,
                generation,
                len(eligible),
                skipped,
                manifest,
                fallback_required=partial,
            )
        except EmbeddingSinkUnavailable:
            return EmbeddingProjectionResult(
                EmbeddingProjectionStatus.UNAVAILABLE,
                snapshot,
                generation,
                0,
                skipped,
                error_code="semantic_sink_unavailable",
                fallback_required=True,
            )
        except Exception as exc:
            try:
                self._sink.mark_failed(generation, _exception_code(exc))
            except Exception:
                pass
            return EmbeddingProjectionResult(
                EmbeddingProjectionStatus.FAILED,
                snapshot,
                generation,
                0,
                skipped,
                error_code=_exception_code(exc),
                fallback_required=True,
            )

    def _read_chunks(
        self,
        snapshot: StoreSnapshot,
        owner_scope: str,
        source_ids: tuple[str, ...],
        limit: int,
    ) -> tuple[tuple[StoredRecord[ChunkRecord], ...], bool]:
        with self._store.begin_read(snapshot) as read:
            page = read.list_records(
                RecordKind.CHUNK,
                owner_scope=owner_scope,
                limit=limit + 1,
            )
        selected: list[StoredRecord[ChunkRecord]] = []
        for item in page.items:
            if not isinstance(item, StoredRecord) or not isinstance(item.record, ChunkRecord):
                continue
            if source_ids and item.record.source_id not in source_ids:
                continue
            selected.append(item)
        clipped = page.clipped or len(selected) > limit
        return tuple(selected[:limit]), clipped


def inspect_embedding_generation(
    manifest: ProjectionManifest,
    sink: EmbeddingGenerationSink,
) -> EmbeddingGenerationHealth:
    if not isinstance(manifest, ProjectionManifest) or manifest.projection_kind is not ProjectionKind.EMBEDDING:
        raise EmbeddingProjectionError("embedding manifest must be typed")
    if not isinstance(sink, EmbeddingGenerationSink):
        raise EmbeddingProjectionError("sink must implement EmbeddingGenerationSink")
    try:
        count = sink.generation_count(manifest.output_generation_ref)
        if count is None:
            return EmbeddingGenerationHealth.MISSING
        active = sink.active_generation(
            owner_scope=manifest.owner_scope,
            profile_ref=manifest.projection_profile_ref,
        )
    except EmbeddingSinkUnavailable:
        return EmbeddingGenerationHealth.UNAVAILABLE
    if active != manifest.output_generation_ref:
        return EmbeddingGenerationHealth.STALE_GENERATION
    if count != len(manifest.input_evidence):
        return EmbeddingGenerationHealth.COUNT_MISMATCH
    return EmbeddingGenerationHealth.CURRENT


def _point(
    item: StoredRecord[ChunkRecord],
    vector: tuple[float, ...],
    generation_ref: str,
    config: EmbeddingConfig,
) -> EmbeddingPoint:
    chunk = item.record
    if len(vector) != config.dimensions:
        raise EmbeddingProjectionError("embedding vector dimension differs from config")
    return EmbeddingPoint(
        chunk.chunk_id,
        vector,
        {
            "chunk_id": chunk.chunk_id,
            "source_id": chunk.source_id,
            "source_version_id": chunk.source_version_id,
            "owner_scope": chunk.owner_scope,
            "content_hash": chunk.content_hash,
            "record_revision": item.revision,
            "generation_ref": generation_ref,
            "config_hash": config.config_hash,
        },
    )


def _retry_upsert(
    sink: EmbeddingGenerationSink,
    generation_ref: str,
    points: tuple[EmbeddingPoint, ...],
    max_retries: int,
) -> None:
    for attempt in range(max_retries + 1):
        try:
            sink.upsert_batch(generation_ref, points)
            return
        except EmbeddingSinkUnavailable:
            raise
        except Exception:
            if attempt >= max_retries:
                raise


def _persist_manifest(
    store: TransactionalStore,
    manifest: ProjectionManifest,
) -> StoreSnapshot:
    snapshot = store.current_snapshot()
    with store.begin_read(snapshot) as read:
        current = read.get(
            RecordKind.PROJECTION,
            manifest.projection_id,
            owner_scope=manifest.owner_scope,
            include_tombstone=True,
        )
    if isinstance(current, StoredRecord):
        if current.record != manifest:
            raise EmbeddingProjectionError("projection manifest identity conflicts")
        return snapshot
    if current is not None:
        raise EmbeddingProjectionError("projection manifest identity is tombstoned")
    write = store.begin_write(snapshot)
    write.put(manifest)
    return write.commit()


def _generation_ref(
    snapshot: StoreSnapshot,
    owner_scope: str,
    config: EmbeddingConfig,
) -> str:
    digest = hashlib.sha256(
        canonical_json(
            {
                "snapshot_ref": snapshot.snapshot_ref,
                "owner_scope": owner_scope,
                "config_hash": config.config_hash,
            }
        ).encode("utf-8")
    ).hexdigest()
    return f"embedding:{digest}"


def _source_ids(values: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(values, tuple) or len(values) > MAX_PAGE_SIZE:
        raise EmbeddingProjectionError("source_ids must be a bounded tuple")
    from src.unified_source_index_contract import RecordRef

    return tuple(
        sorted({RecordRef(RecordKind.SOURCE, value).record_id for value in values})
    )


def _exception_code(exc: Exception) -> str:
    normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", type(exc).__name__).lower()
    return normalized if _TOKEN_RE.fullmatch(normalized) else "embedding_projection_error"


def _fingerprint(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _generation(value: str) -> str:
    if not isinstance(value, str) or not _GENERATION_RE.fullmatch(value):
        raise EmbeddingProjectionError("generation_ref is invalid")
    return value


def _sha256(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise EmbeddingProjectionError(f"{field_name} must be sha256 text")
    normalized = value.lower()
    if not normalized.startswith("sha256:"):
        normalized = "sha256:" + normalized
    if not _HASH_RE.fullmatch(normalized):
        raise EmbeddingProjectionError(f"{field_name} must be sha256 text")
    return normalized


def _token(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        raise EmbeddingProjectionError(f"{field_name} must be a bounded token")
    return value


def _integer(
    value: int,
    field_name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise EmbeddingProjectionError(
            f"{field_name} must be between {minimum} and {maximum}"
        )
    return value


def _enum(value, enum_type, field_name: str):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise EmbeddingProjectionError(f"{field_name} is invalid") from exc


__all__ = [
    "DeterministicFakeEmbeddingEncoder",
    "EmbeddingConfig",
    "EmbeddingEncoder",
    "EmbeddingGenerationHealth",
    "EmbeddingGenerationSink",
    "EmbeddingPoint",
    "EmbeddingProjectionError",
    "EmbeddingProjectionResult",
    "EmbeddingProjectionStatus",
    "EmbeddingSinkUnavailable",
    "FakeChromaGenerationSink",
    "MAX_EMBEDDING_BATCH",
    "MAX_EMBEDDING_DIMENSIONS",
    "MAX_EMBEDDING_INPUTS",
    "UnifiedSourceIndexEmbeddingProjector",
    "inspect_embedding_generation",
]
