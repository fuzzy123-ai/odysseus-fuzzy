"""Inactive server-side composition for owner-bound Nextcloud ingestion.

The composition is deliberately not registered with an application, route, or
provider.  It turns the existing read-only runtime selection into one bounded,
SID-authorized call to the accepted native ingestion adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path
import re
import threading

from core.auth import AuthManager, SUBJECT_ID_PATTERN
from src.bigdata_ledger_contract import BigDataLedgerItem, BigDataLedgerRecord
from src.harbour_authority import AuthenticatedPrincipalIssuer
from src.harbour_knowledge_active_runtime import _app_secret
from src.harbour_knowledge_staging_destination import (
    HarbourKnowledgeStagingDestinationService,
    StagingMigrationCompletionReference,
)
from src.harbour_knowledge_store import HarbourKnowledgeStore
from src.nextcloud_knowledge_ingestion import ingest_nextcloud_personal_document
from src.personal_docs_owner_reader import PersonalDocsOwnerReader
from src.unified_source_index_runtime_config import (
    UnifiedSourceIndexRuntimeConfig,
    UnifiedSourceIndexRuntimeMode,
)
from src.unified_source_index_runtime_contract import RuntimeGeneration
from src.unified_source_index_source_capability import SourceAdapterCapabilityManifest
from src.unified_source_index_sources.knowledge import (
    KNOWLEDGE_SOURCE_ADAPTER_ID,
    KNOWLEDGE_SOURCE_DOMAIN_ID,
    knowledge_source_capability_manifest,
)


_ISSUER_ID = "harbour-knowledge-runtime"
_KEY_ID = "app-key-v1"
_ISSUER_DOMAIN = b"odysseus.harbour.knowledge.issuer.v1\0"
_PURPOSE = "nextcloud_knowledge_ingestion"
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_GENERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PATH_TYPE = type(Path())
_OPEN_TOKEN = object()
_CONFIG_BOOLEAN_FIELDS = (
    "runtime_enabled",
    "cbm_provider_enabled",
    "lineage_provider_enabled",
    "raptor_provider_enabled",
    "chroma_provider_enabled",
    "test_fixture_mode",
)
_CONFIG_INTEGER_FIELDS = (
    "query_max_results",
    "query_timeout_seconds",
    "worker_batch_size",
    "worker_max_concurrency",
    "worker_max_seconds",
    "shadow_sample_rate_percent",
    "stale_generation_max_age_seconds",
    "fallback_max_attempts",
    "circuit_breaker_failure_threshold",
    "circuit_breaker_reset_seconds",
)
_ERROR_CODES = frozenset(
    {"open_failed", "admission_denied", "pre_adapter_failed", "close_failed"}
)


class NextcloudKnowledgeIngestionCompositionError(ValueError):
    """Fresh, fixed, content-free composition failure."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        safe = code if type(code) is str and code in _ERROR_CODES else "pre_adapter_failed"
        self.code = safe
        super().__init__(safe)

    def __repr__(self) -> str:
        return f"NextcloudKnowledgeIngestionCompositionError({self.code!r})"


def _raise_composition(code: str) -> None:
    """Raise without retaining a caught provider, filesystem, or auth error."""

    error = NextcloudKnowledgeIngestionCompositionError(code)
    try:
        raise error
    except NextcloudKnowledgeIngestionCompositionError as caught:
        BaseException.__context__.__set__(caught, None)
        BaseException.__cause__.__set__(caught, None)
        caught.__suppress_context__ = False
        raise


@dataclass(frozen=True, slots=True, repr=False)
class _ConfigSnapshot:
    projection: tuple[object, ...]
    selected_generation: str
    allowed_owners: tuple[str, ...]


def _string_tuple(value: object) -> tuple[str, ...]:
    if type(value) is not tuple or any(type(item) is not str for item in value):
        raise ValueError("configuration unavailable")
    return value


def _config_projection(config: object) -> _ConfigSnapshot:
    if type(config) is not UnifiedSourceIndexRuntimeConfig:
        raise ValueError("configuration unavailable")
    data_root = config.data_root
    sqlite_path = config.sqlite_path
    mode = config.mode
    selected_generation = config.selected_generation
    allowed_owners = _string_tuple(config.allowed_owners)
    allowed_sources = _string_tuple(config.allowed_sources)
    allowed_domains = _string_tuple(config.allowed_domains)
    booleans = tuple(getattr(config, name) for name in _CONFIG_BOOLEAN_FIELDS)
    integers = tuple(getattr(config, name) for name in _CONFIG_INTEGER_FIELDS)
    boolean_values = dict(zip(_CONFIG_BOOLEAN_FIELDS, booleans, strict=True))
    integer_values = dict(zip(_CONFIG_INTEGER_FIELDS, integers, strict=True))
    if (
        type(data_root) is not _PATH_TYPE
        or type(sqlite_path) is not _PATH_TYPE
        or not data_root.is_absolute()
        or not sqlite_path.is_absolute()
        or type(mode) is not UnifiedSourceIndexRuntimeMode
        or mode is not UnifiedSourceIndexRuntimeMode.READ_ONLY
        or any(type(value) is not bool for value in booleans)
        or boolean_values["runtime_enabled"] is not True
        or boolean_values["test_fixture_mode"] is not False
        or any(
            value is not False
            for value in (
                boolean_values["cbm_provider_enabled"],
                boolean_values["lineage_provider_enabled"],
                boolean_values["raptor_provider_enabled"],
                boolean_values["chroma_provider_enabled"],
            )
        )
        or any(type(value) is not int or isinstance(value, bool) for value in integers)
        or type(selected_generation) is not str
        or not allowed_owners
        or any(SUBJECT_ID_PATTERN.fullmatch(owner) is None for owner in allowed_owners)
        or not allowed_sources
        or allowed_domains != ("native_knowledge",)
    ):
        raise ValueError("configuration unavailable")
    validated = UnifiedSourceIndexRuntimeConfig(
        data_root=data_root,
        sqlite_path=sqlite_path,
        mode=mode,
        runtime_enabled=boolean_values["runtime_enabled"],
        selected_generation=selected_generation,
        allowed_owners=allowed_owners,
        allowed_sources=allowed_sources,
        allowed_domains=allowed_domains,
        cbm_provider_enabled=boolean_values["cbm_provider_enabled"],
        lineage_provider_enabled=boolean_values["lineage_provider_enabled"],
        raptor_provider_enabled=boolean_values["raptor_provider_enabled"],
        chroma_provider_enabled=boolean_values["chroma_provider_enabled"],
        query_max_results=integer_values["query_max_results"],
        query_timeout_seconds=integer_values["query_timeout_seconds"],
        worker_batch_size=integer_values["worker_batch_size"],
        worker_max_concurrency=integer_values["worker_max_concurrency"],
        worker_max_seconds=integer_values["worker_max_seconds"],
        shadow_sample_rate_percent=integer_values["shadow_sample_rate_percent"],
        stale_generation_max_age_seconds=integer_values[
            "stale_generation_max_age_seconds"
        ],
        fallback_max_attempts=integer_values["fallback_max_attempts"],
        circuit_breaker_failure_threshold=integer_values[
            "circuit_breaker_failure_threshold"
        ],
        circuit_breaker_reset_seconds=integer_values[
            "circuit_breaker_reset_seconds"
        ],
        test_fixture_mode=boolean_values["test_fixture_mode"],
    )
    if (
        validated.data_root != data_root
        or validated.sqlite_path != sqlite_path
        or validated.allowed_owners != allowed_owners
        or validated.allowed_sources != allowed_sources
        or validated.allowed_domains != allowed_domains
    ):
        raise ValueError("configuration unavailable")
    capability = knowledge_source_capability_manifest()
    if (
        type(capability) is not SourceAdapterCapabilityManifest
        or capability.adapter_id != KNOWLEDGE_SOURCE_ADAPTER_ID
        or capability.domain_id != KNOWLEDGE_SOURCE_DOMAIN_ID
        or capability.productive_default_enabled is not False
        or capability.generation_ref != selected_generation
    ):
        raise ValueError("configuration unavailable")
    generation = RuntimeGeneration(selected_generation)
    projection = (
        str(data_root),
        str(sqlite_path),
        mode.value,
        booleans,
        selected_generation,
        allowed_owners,
        allowed_sources,
        allowed_domains,
        integers,
        generation.to_dict()["generation_ref"],
        generation.to_dict()["previous_generation_ref"],
    )
    return _ConfigSnapshot(projection, selected_generation, allowed_owners)


def _staging_root(value: object) -> Path:
    if type(value) is str:
        raw = value
    elif type(value) is _PATH_TYPE:
        raw = str(value)
    else:
        raise ValueError("staging root unavailable")
    if not raw or "~" in raw:
        raise ValueError("staging root unavailable")
    path = Path(raw)
    if not path.is_absolute():
        raise ValueError("staging root unavailable")
    return path


def _metadata_copy(value: object, *, depth: int = 0) -> object:
    if depth > 8:
        raise ValueError("inventory unavailable")
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is list:
        if len(value) > 64:
            raise ValueError("inventory unavailable")
        return [_metadata_copy(item, depth=depth + 1) for item in value]
    if type(value) is dict:
        if len(value) > 64:
            raise ValueError("inventory unavailable")
        copied: dict[str, object] = {}
        for key, child in value.items():
            if type(key) is not str or not key or key in copied:
                raise ValueError("inventory unavailable")
            copied[key] = _metadata_copy(child, depth=depth + 1)
        return copied
    raise ValueError("inventory unavailable")


def _detached_inventory(value: object) -> BigDataLedgerRecord:
    if type(value) is not BigDataLedgerRecord:
        raise ValueError("inventory unavailable")
    item = value.item
    if type(item) is not BigDataLedgerItem:
        raise ValueError("inventory unavailable")
    item_values = (
        item.provider,
        item.source_id,
        item.relative_path,
        item.size_bytes,
        item.mtime,
        item.content_hash,
        item.etag,
        item.item_id,
    )
    record_values = (
        value.stage,
        value.status,
        value.attempt_count,
        value.last_error,
        value.next_retry_at,
        value.locked_by,
        value.locked_at,
        value.updated_at,
    )
    raw_metadata = value.metadata
    if (
        any(type(field) is not str for field in item_values[:3])
        or type(item_values[3]) is not int
        or isinstance(item_values[3], bool)
        or item_values[3] < 0
        or any(type(field) is not str for field in item_values[4:])
        or any(type(field) is not str for field in record_values[:2])
        or type(record_values[2]) is not int
        or isinstance(record_values[2], bool)
        or record_values[2] < 0
        or any(type(field) is not str for field in record_values[3:])
        or item_values[0] != "nextcloud"
        or record_values[0] != "inventory"
        or record_values[1] != "completed"
        or type(raw_metadata) is not dict
    ):
        raise ValueError("inventory unavailable")
    metadata = _metadata_copy(raw_metadata)
    if type(metadata) is not dict:
        raise ValueError("inventory unavailable")
    detached_item = BigDataLedgerItem(
        provider=item_values[0],
        source_id=item_values[1],
        relative_path=item_values[2],
        size_bytes=item_values[3],
        mtime=item_values[4],
        content_hash=item_values[5],
        etag=item_values[6],
        item_id=item_values[7],
    )
    detached = BigDataLedgerRecord(
        item=detached_item,
        stage=record_values[0],
        status=record_values[1],
        attempt_count=record_values[2],
        last_error=record_values[3],
        next_retry_at=record_values[4],
        locked_by=record_values[5],
        locked_at=record_values[6],
        updated_at=record_values[7],
        metadata=metadata,
    )
    if (
        (
            detached.item.provider,
            detached.item.source_id,
            detached.item.relative_path,
            detached.item.size_bytes,
            detached.item.mtime,
            detached.item.content_hash,
            detached.item.etag,
            detached.item.item_id,
        )
        != item_values
        or (
            detached.stage,
            detached.status,
            detached.attempt_count,
            detached.last_error,
            detached.next_retry_at,
            detached.locked_by,
            detached.locked_at,
            detached.updated_at,
        )
        != record_values
        or _metadata_copy(detached.metadata) != metadata
    ):
        raise ValueError("inventory unavailable")
    return detached


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _issuer(key: bytes) -> AuthenticatedPrincipalIssuer:
    if type(key) is not bytes or len(key) != 32:
        raise ValueError("application key unavailable")
    return AuthenticatedPrincipalIssuer(
        issuer_id=_ISSUER_ID,
        key_id=_KEY_ID,
        secret=hmac.new(key, _ISSUER_DOMAIN, hashlib.sha256).digest(),
    )


class NextcloudKnowledgeIngestionComposition:
    """Unregistered composition for one server-side native ingestion call."""

    __slots__ = (
        "_config",
        "_config_snapshot",
        "_auth",
        "_key_digest",
        "_issuer",
        "_staging",
        "_reader",
        "_condition",
        "_active",
        "_state",
        "_inflight",
    )

    def __init_subclass__(cls, **_kwargs: object) -> None:
        raise TypeError("nextcloud knowledge ingestion composition is sealed")

    def __init__(
        self,
        *,
        config: UnifiedSourceIndexRuntimeConfig,
        config_snapshot: _ConfigSnapshot,
        auth_manager: AuthManager,
        key_digest: bytes,
        issuer: AuthenticatedPrincipalIssuer,
        staging: HarbourKnowledgeStagingDestinationService,
        reader: PersonalDocsOwnerReader,
        _token: object,
    ) -> None:
        if _token is not _OPEN_TOKEN:
            raise TypeError("use open_existing")
        self._config = config
        self._config_snapshot = config_snapshot
        self._auth = auth_manager
        self._key_digest = key_digest
        self._issuer = issuer
        self._staging = staging
        self._reader = reader
        self._condition = threading.Condition(threading.RLock())
        self._active = threading.local()
        self._state = "open"
        self._inflight = 0

    @classmethod
    def open_existing(
        cls,
        config: UnifiedSourceIndexRuntimeConfig,
        auth_manager: AuthManager,
        *,
        staging_root: str | Path,
        reader: PersonalDocsOwnerReader,
    ) -> "NextcloudKnowledgeIngestionComposition":
        if cls is not NextcloudKnowledgeIngestionComposition:
            _raise_composition("open_failed")
        staging: HarbourKnowledgeStagingDestinationService | None = None
        try:
            snapshot = _config_projection(config)
            if type(auth_manager) is not AuthManager or type(reader) is not PersonalDocsOwnerReader:
                raise ValueError("composition dependency unavailable")
            root = _staging_root(staging_root)
            key = _app_secret()
            runtime_issuer = _issuer(key)
            key_digest = hashlib.sha256(key).digest()
            staging = HarbourKnowledgeStagingDestinationService.open_existing(
                root,
                trusted_issuers=(runtime_issuer,),
            )
            if staging.current_selection() is None:
                raise ValueError("knowledge selection unavailable")
            return NextcloudKnowledgeIngestionComposition(
                config=config,
                config_snapshot=snapshot,
                auth_manager=auth_manager,
                key_digest=key_digest,
                issuer=runtime_issuer,
                staging=staging,
                reader=reader,
                _token=_OPEN_TOKEN,
            )
        except BaseException:
            if staging is not None:
                try:
                    staging.close()
                except BaseException:
                    pass
            _raise_composition("open_failed")

    def _admit(
        self,
        *,
        owner_subject_id: object,
        security_revision: object,
        inventory_record: object,
    ) -> tuple[str, int, BigDataLedgerRecord]:
        try:
            if getattr(self._active, "operation", False) is True:
                raise ValueError("operation in progress")
            if (
                type(owner_subject_id) is not str
                or SUBJECT_ID_PATTERN.fullmatch(owner_subject_id) is None
                or type(security_revision) is not int
                or isinstance(security_revision, bool)
                or security_revision < 0
            ):
                raise ValueError("request unavailable")
            current_config = _config_projection(self._config)
            if current_config.projection != self._config_snapshot.projection:
                raise ValueError("configuration changed")
            if owner_subject_id not in self._config_snapshot.allowed_owners:
                raise ValueError("owner unavailable")
            detached = _detached_inventory(inventory_record)
            with self._condition:
                if self._state != "open" or getattr(self._active, "operation", False) is True:
                    raise ValueError("composition unavailable")
                self._active.operation = True
                self._inflight += 1
            return owner_subject_id, security_revision, detached
        except NextcloudKnowledgeIngestionCompositionError:
            raise
        except BaseException:
            _raise_composition("admission_denied")

    def ingest(
        self,
        *,
        owner_subject_id: str,
        security_revision: int,
        inventory_record: BigDataLedgerRecord,
    ) -> object:
        subject, revision, detached_record = self._admit(
            owner_subject_id=owner_subject_id,
            security_revision=security_revision,
            inventory_record=inventory_record,
        )
        adapter_started = False
        adapter_result: object | None = None
        adapter_error: BaseException | None = None
        try:
            try:
                with self._auth.guard_current_admin_subject(subject) as alias:
                    identity = self._auth.active_admin_subject_identity(subject_id=subject)
                    current_revision = self._auth.security_action_revision()
                    if (
                        type(alias) is not str
                        or type(identity) is not tuple
                        or len(identity) != 2
                        or any(type(value) is not str for value in identity)
                        or identity != (alias, subject)
                        or type(current_revision) is not int
                        or isinstance(current_revision, bool)
                        or current_revision != revision
                    ):
                        raise ValueError("current identity unavailable")
                    current_key = _app_secret()
                    if (
                        type(current_key) is not bytes
                        or len(current_key) != 32
                        or not hmac.compare_digest(
                            hashlib.sha256(current_key).digest(), self._key_digest
                        )
                    ):
                        raise ValueError("application key changed")
                    with self._staging.guard_selected_runtime(subject) as selected:
                        if type(selected) is not tuple or len(selected) != 5:
                            raise ValueError("selection unavailable")
                        reference, owners, native_generation, selection_digest, store = selected
                        if (
                            type(reference) is not StagingMigrationCompletionReference
                            or type(owners) is not tuple
                            or not owners
                            or any(type(owner) is not str for owner in owners)
                            or subject not in owners
                            or type(native_generation) is not str
                            or _GENERATION_RE.fullmatch(native_generation) is None
                            or type(selection_digest) is not str
                            or _DIGEST_RE.fullmatch(selection_digest) is None
                            or type(store) is not HarbourKnowledgeStore
                        ):
                            raise ValueError("selection unavailable")
                        current_reader_owner = object.__getattribute__(
                            self._reader, "_owner_id"
                        )
                        if (
                            type(current_reader_owner) is not str
                            or current_reader_owner != subject
                        ):
                            raise ValueError("reader owner changed")
                        principal = self._issuer.issue(
                            subject_id=subject,
                            owner_id=subject,
                            auth_context_digest=_canonical_digest(
                                {
                                    "purpose": _PURPOSE,
                                    "subject_id": subject,
                                    "security_revision": revision,
                                    "native_generation": native_generation,
                                    "selection_digest": selection_digest,
                                    "usi_generation": RuntimeGeneration(
                                        self._config_snapshot.selected_generation
                                    ).to_dict(),
                                }
                            ),
                            ttl_seconds=60,
                        )
                        adapter_started = True
                        try:
                            adapter_result = ingest_nextcloud_personal_document(
                                reader=self._reader,
                                inventory_record=detached_record,
                                store=store,
                                principal=principal,
                                owner_id=subject,
                            )
                        except BaseException as error:
                            adapter_error = error
            except BaseException:
                if not adapter_started:
                    _raise_composition("pre_adapter_failed")
            if adapter_error is not None:
                raise adapter_error
            return adapter_result
        finally:
            self._active.operation = False
            with self._condition:
                self._inflight -= 1
                self._condition.notify_all()

    def close(self) -> None:
        if getattr(self._active, "operation", False) is True:
            _raise_composition("close_failed")
        owns_close = False
        try:
            with self._condition:
                while self._state == "closing":
                    self._condition.wait()
                if self._state == "closed":
                    return
                if self._state == "close_failed":
                    _raise_composition("close_failed")
                if self._state != "open":
                    raise ValueError("composition state unavailable")
                self._state = "closing"
                owns_close = True
                while self._inflight:
                    self._condition.wait()
            self._staging.close()
            with self._condition:
                self._state = "closed"
                self._condition.notify_all()
        except NextcloudKnowledgeIngestionCompositionError:
            raise
        except BaseException:
            if owns_close:
                with self._condition:
                    self._state = "close_failed"
                    self._condition.notify_all()
            _raise_composition("close_failed")

    def __repr__(self) -> str:
        return "NextcloudKnowledgeIngestionComposition(<private>)"


__all__ = [
    "NextcloudKnowledgeIngestionComposition",
    "NextcloudKnowledgeIngestionCompositionError",
]
