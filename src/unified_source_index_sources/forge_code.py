"""Default-off, content-free Forge snapshot authority for the USI code lane.

This module intentionally does not instantiate a Forge client, inspect a local
worktree, invoke Git, or return source text.  It only validates immutable
snapshot inventory and future exact-reader references supplied by the accepted
authenticated Forge boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.repo_git_adapter import (
    MAX_FORGE_EXACT_READ_BYTES,
    MAX_FORGE_SNAPSHOT_FILES,
    ForgeExactReaderReference,
    ForgeSnapshotAuthorityBinding,
    ForgeSnapshotError,
    ForgeSnapshotFile,
    ForgeSnapshotInventory,
    ForgeSnapshotReader,
    ForgeSnapshotRequest,
)
from src.repo_registry import RepoRecord, RepoRegistry, RepoRegistryError
from src.project_version_store import ProjectVersionStoreError, owner_key_for
from src.unified_source_index_contract import (
    ChunkRecord,
    Classification,
    CodeOccurrenceRecords,
    CodeRangeLocator,
    ContentPolicy,
    ForgeCodeOccurrenceEvidence,
    SourceKind,
    SourceRecord,
    SourceVersionRecord,
)
from src.unified_source_index_source_capability import (
    OwnerScopeRequirement,
    ProviderConstraint,
    QueryCapability,
    SourceAdapterCapabilityManifest,
    SourceAdapterOperation,
)
from src.unified_source_index_source_registry import (
    SourceAdapterRegistry,
    SourceAdapterRegistryError,
)


FORGE_CODE_ADAPTER_ID = "forge.code"
FORGE_CODE_DOMAIN_ID = "project_forge"
FORGE_CODE_EXACT_READER_BOUNDARY = "forge.code_exact_reader"
FORGE_CODE_ADMISSION_POLICY_GENERATION = "fca.forge_code.admission.v1"


class ForgeCodeSourceError(ValueError):
    """Raised when a request crosses Forge, repository, or snapshot authority."""


def forge_code_capability_manifest() -> SourceAdapterCapabilityManifest:
    """Return the sole default-off capability declaration for Forge code.

    Registration is intentionally separate.  Constructing this declaration
    cannot discover repositories, load source bodies, or activate indexing.
    """

    return SourceAdapterCapabilityManifest(
        adapter_id=FORGE_CODE_ADAPTER_ID,
        adapter_version="v1",
        domain_id=FORGE_CODE_DOMAIN_ID,
        source_kind=SourceKind.CODE,
        content_policy=ContentPolicy.REFERENCE_ONLY,
        classification_ceiling=Classification.SENSITIVE,
        owner_scope_requirement=OwnerScopeRequirement.IMMUTABLE_OPAQUE,
        provider_constraint=ProviderConstraint.NONE,
        query_capability=QueryCapability.EXACT_READER,
        operations=(
            SourceAdapterOperation.DISCOVER,
            SourceAdapterOperation.OBSERVE_VERSION,
            SourceAdapterOperation.EXTRACT,
            SourceAdapterOperation.READ_EXACT,
            SourceAdapterOperation.OBSERVE_UNAVAILABLE,
        ),
        exact_reader_boundary=FORGE_CODE_EXACT_READER_BOUNDARY,
        productive_default_enabled=False,
    )


@dataclass(frozen=True, slots=True)
class ForgeCodeSnapshotRequest:
    """Typed access context for one authenticated, immutable Forge version."""

    owner_scope: str
    authorization_ref: str
    repo_id: str
    version_id: str
    commit_sha: str
    authority_binding: ForgeSnapshotAuthorityBinding

    def to_forge_request(self) -> ForgeSnapshotRequest:
        return ForgeSnapshotRequest(
            owner_scope=self.owner_scope,
            authorization_ref=self.authorization_ref,
            repo_id=self.repo_id,
            version_id=self.version_id,
            commit_sha=self.commit_sha,
            authority_binding=self.authority_binding,
        )


@dataclass(frozen=True, slots=True)
class ForgeCodeOccurrence:
    """Reference-only USI occurrence bound to one immutable Forge file revision."""

    owner_scope: str
    repo_id: str
    version_id: str
    commit_sha: str
    snapshot_digest: str
    authority_binding: ForgeSnapshotAuthorityBinding
    path: str
    file_content_sha256: str
    locator: CodeRangeLocator
    extractor_profile_ref: str
    records: CodeOccurrenceRecords
    occurrence_id: str = ""

    def __post_init__(self) -> None:
        if type(self.occurrence_id) is not str:
            raise ForgeCodeSourceError("occurrence_id must use the exact string scalar type")
        if type(self.locator) is not CodeRangeLocator:
            raise ForgeCodeSourceError("Forge code occurrence requires an exact CodeRangeLocator")
        if type(self.records) is not CodeOccurrenceRecords:
            raise ForgeCodeSourceError("Forge code occurrence records must use the exact aggregate type")
        try:
            canonical_reference = _canonical_reference(
                ForgeExactReaderReference(
                    owner_scope=self.owner_scope,
                    repo_id=self.repo_id,
                    version_id=self.version_id,
                    commit_sha=self.commit_sha,
                    snapshot_digest=self.snapshot_digest,
                    path=self.path,
                    content_sha256=self.file_content_sha256,
                    max_bytes=1,
                    authority_binding=self.authority_binding,
                )
            )
            locator = CodeRangeLocator(
                canonical_reference.path,
                self.locator.start_line,
                self.locator.start_column,
                self.locator.end_line,
                self.locator.end_column,
            )
            records = CodeOccurrenceRecords(
                self.records.source,
                self.records.source_version,
                self.records.chunk,
                self.records.forge_evidence,
            )
        except (ForgeSnapshotError, TypeError, ValueError):
            raise ForgeCodeSourceError("Forge code occurrence contains invalid snapshot evidence") from None
        if _authority_primitives(canonical_reference.authority_binding) != _expected_authority_values():
            raise ForgeCodeSourceError("Forge code occurrence has foreign adapter or admission authority")
        if locator != self.locator or locator.path != canonical_reference.path:
            raise ForgeCodeSourceError("Forge code occurrence locator is not the canonical snapshot path")
        expected_records = _forge_occurrence_records(
            owner_scope=canonical_reference.owner_scope,
            repo_id=canonical_reference.repo_id,
            version_id=canonical_reference.version_id,
            commit_sha=canonical_reference.commit_sha,
            snapshot_digest=canonical_reference.snapshot_digest,
            authority_binding=canonical_reference.authority_binding,
            path=canonical_reference.path,
            file_content_sha256=canonical_reference.content_sha256,
            locator=locator,
            extractor_profile_ref=self.extractor_profile_ref,
            version_observed_at=records.source_version.version_observed_at,
            indexed_at=records.chunk.indexed_at,
        )
        if (
            records.source.to_json(),
            records.source_version.to_json(),
            records.chunk.to_json(),
        ) != (
            expected_records.source.to_json(),
            expected_records.source_version.to_json(),
            expected_records.chunk.to_json(),
        ):
            raise ForgeCodeSourceError("Forge code occurrence records cross snapshot or parent authority")
        expected_id = _forge_occurrence_id(
            canonical_reference,
            locator,
            records.chunk.extractor_profile_ref,
        )
        if self.occurrence_id and self.occurrence_id != expected_id:
            raise ForgeCodeSourceError("occurrence_id does not match immutable Forge identity")
        object.__setattr__(self, "owner_scope", canonical_reference.owner_scope)
        object.__setattr__(self, "repo_id", canonical_reference.repo_id)
        object.__setattr__(self, "version_id", canonical_reference.version_id)
        object.__setattr__(self, "commit_sha", canonical_reference.commit_sha)
        object.__setattr__(self, "snapshot_digest", canonical_reference.snapshot_digest)
        object.__setattr__(self, "authority_binding", canonical_reference.authority_binding)
        object.__setattr__(self, "path", canonical_reference.path)
        object.__setattr__(self, "file_content_sha256", canonical_reference.content_sha256)
        object.__setattr__(self, "locator", locator)
        object.__setattr__(self, "extractor_profile_ref", records.chunk.extractor_profile_ref)
        object.__setattr__(self, "records", records)
        object.__setattr__(self, "occurrence_id", expected_id)

    @classmethod
    def from_snapshot_inventory(
        cls,
        inventory: ForgeSnapshotInventory,
        *,
        locator: CodeRangeLocator,
        file_content_sha256: str,
        version_observed_at: str,
        extractor_profile_ref: str = "forge-code-lines-v1",
        indexed_at: str = "",
    ) -> "ForgeCodeOccurrence":
        """Create an occurrence from accepted FCA-00 inventory without reading bytes."""

        if type(locator) is not CodeRangeLocator:
            raise ForgeCodeSourceError("Forge code occurrence requires an exact CodeRangeLocator")
        try:
            accepted = _canonical_inventory(inventory)
            snapshot_file = accepted.file(locator.path)
        except (ForgeSnapshotError, TypeError, ValueError):
            raise ForgeCodeSourceError("Forge code occurrence is outside the accepted snapshot") from None
        if type(file_content_sha256) is not str or snapshot_file.content_sha256 != file_content_sha256:
            raise ForgeCodeSourceError("Forge code occurrence file digest differs from snapshot inventory")
        if locator.path != snapshot_file.path:
            raise ForgeCodeSourceError(
                "Forge code occurrence locator must exactly match the inventory's canonical path"
            )
        canonical_locator = CodeRangeLocator(
            snapshot_file.path,
            locator.start_line,
            locator.start_column,
            locator.end_line,
            locator.end_column,
        )
        records = _forge_occurrence_records(
            owner_scope=accepted.owner_scope,
            repo_id=accepted.repo_id,
            version_id=accepted.version_id,
            commit_sha=accepted.commit_sha,
            snapshot_digest=accepted.snapshot_digest,
            authority_binding=accepted.authority_binding,
            path=snapshot_file.path,
            file_content_sha256=snapshot_file.content_sha256,
            locator=canonical_locator,
            extractor_profile_ref=extractor_profile_ref,
            version_observed_at=version_observed_at,
            indexed_at=indexed_at,
        )
        return cls(
            owner_scope=accepted.owner_scope,
            repo_id=accepted.repo_id,
            version_id=accepted.version_id,
            commit_sha=accepted.commit_sha,
            snapshot_digest=accepted.snapshot_digest,
            authority_binding=accepted.authority_binding,
            path=snapshot_file.path,
            file_content_sha256=snapshot_file.content_sha256,
            locator=canonical_locator,
            extractor_profile_ref=extractor_profile_ref,
            records=records,
        )


class ForgeCodeSource:
    """Validate a Forge-only snapshot without creating a source-reading path."""

    def __init__(
        self,
        *,
        adapter_registry: SourceAdapterRegistry,
        repo_registry: RepoRegistry,
        snapshot_reader: ForgeSnapshotReader,
    ) -> None:
        if type(adapter_registry) is not SourceAdapterRegistry:
            raise ForgeCodeSourceError("adapter_registry must be a SourceAdapterRegistry")
        if type(repo_registry) is not RepoRegistry:
            raise ForgeCodeSourceError("repo_registry must be a RepoRegistry")
        if not isinstance(snapshot_reader, ForgeSnapshotReader):
            raise ForgeCodeSourceError("snapshot_reader must implement the Forge snapshot boundary")
        try:
            manifest = adapter_registry.select(FORGE_CODE_ADAPTER_ID).manifest
        except SourceAdapterRegistryError:
            raise ForgeCodeSourceError("Forge code capability is not registered") from None
        expected_manifest = forge_code_capability_manifest()
        if _manifest_primitives(manifest) != _manifest_primitives(expected_manifest):
            raise ForgeCodeSourceError("registered Forge code capability differs from the default-off contract")
        self._authority_values = (
            expected_manifest.adapter_id,
            expected_manifest.adapter_version,
            expected_manifest.generation_ref,
            FORGE_CODE_ADMISSION_POLICY_GENERATION,
        )
        _new_authority_binding(self._authority_values)
        self._repo_registry = repo_registry
        self._snapshot_reader = snapshot_reader

    @property
    def manifest(self) -> SourceAdapterCapabilityManifest:
        """Return fresh capability evidence; this does not activate the adapter."""

        return forge_code_capability_manifest()

    @property
    def authority_binding(self) -> ForgeSnapshotAuthorityBinding:
        """Return a fresh copy of the registry- and policy-derived authority."""

        return _new_authority_binding(self._authority_values)

    def snapshot_inventory(self, request: ForgeCodeSnapshotRequest) -> ForgeSnapshotInventory:
        """Return one exact immutable inventory after all authority checks pass."""

        canonical_request = self._authorize_request(request)
        return self._snapshot_inventory_for(canonical_request)

    def exact_reader_reference(
        self,
        request: ForgeCodeSnapshotRequest,
        *,
        path: str,
        max_bytes: int,
    ) -> ForgeExactReaderReference:
        """Return a bounded reference only; it deliberately never reads content."""

        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_FORGE_EXACT_READ_BYTES:
            raise ForgeCodeSourceError("max_bytes exceeds the bounded exact-reader limit")
        canonical_request = self._authorize_request(request)
        canonical_request_values = _request_primitives(canonical_request)
        inventory = self._snapshot_inventory_for(canonical_request)
        try:
            expected_file = inventory.file(path)
        except ForgeSnapshotError:
            raise ForgeCodeSourceError("exact-reader path is not present in the immutable Forge snapshot") from None
        try:
            untrusted_reference = self._snapshot_reader.exact_reader_reference(
                canonical_request,
                path=expected_file.path,
                max_bytes=max_bytes,
            )
            if _request_primitives(canonical_request) != canonical_request_values:
                raise ForgeCodeSourceError("Forge reader mutated the canonical request")
            reference = _canonical_reference(untrusted_reference)
        except Exception:
            raise ForgeCodeSourceError("Forge exact-reader reference is unavailable or invalid") from None
        expected_reference_values = (
            inventory.owner_scope,
            inventory.repo_id,
            inventory.version_id,
            inventory.commit_sha,
            inventory.snapshot_digest,
            expected_file.path,
            expected_file.content_sha256,
            max_bytes,
            self._authority_values,
        )
        actual_reference_values = (
            reference.owner_scope,
            reference.repo_id,
            reference.version_id,
            reference.commit_sha,
            reference.snapshot_digest,
            reference.path,
            reference.content_sha256,
            reference.max_bytes,
            _authority_primitives(reference.authority_binding),
        )
        if actual_reference_values != expected_reference_values:
            raise ForgeCodeSourceError("exact-reader reference crosses immutable Forge snapshot authority")
        return reference

    def _snapshot_inventory_for(self, canonical_request: ForgeSnapshotRequest) -> ForgeSnapshotInventory:
        canonical_request_values = _request_primitives(canonical_request)
        if canonical_request_values[-1] != self._authority_values:
            raise ForgeCodeSourceError("canonical Forge request has foreign authority")
        try:
            untrusted_inventory = self._snapshot_reader.inventory(canonical_request)
            if _request_primitives(canonical_request) != canonical_request_values:
                raise ForgeCodeSourceError("Forge reader mutated the canonical request")
            inventory = _canonical_inventory(untrusted_inventory)
        except Exception:
            raise ForgeCodeSourceError("Forge snapshot inventory is unavailable or invalid") from None
        expected_request_values = (
            canonical_request_values[0],
            canonical_request_values[2],
            canonical_request_values[3],
            canonical_request_values[4],
            self._authority_values,
        )
        actual_inventory_values = (
            inventory.owner_scope,
            inventory.repo_id,
            inventory.version_id,
            inventory.commit_sha,
            _authority_primitives(inventory.authority_binding),
        )
        if actual_inventory_values != expected_request_values:
            raise ForgeCodeSourceError("Forge snapshot inventory crosses immutable request authority")
        return inventory

    def _authorize_request(self, request: Any) -> ForgeSnapshotRequest:
        if type(request) is not ForgeCodeSnapshotRequest:
            raise ForgeCodeSourceError("Forge snapshot request must be the exact typed request")
        raw_owner_scope = request.owner_scope
        raw_authorization_ref = request.authorization_ref
        raw_repo_id = request.repo_id
        raw_version_id = request.version_id
        raw_commit_sha = request.commit_sha
        raw_authority_binding = request.authority_binding
        try:
            record = self._repo_registry.get(raw_repo_id)
        except RepoRegistryError:
            raise ForgeCodeSourceError("Forge snapshot request does not name a registered repository") from None
        if type(record) is not RepoRecord:
            raise ForgeCodeSourceError("Forge snapshot repository record must be canonical")
        if type(record.repo_id) is not str or type(raw_repo_id) is not str or raw_repo_id != record.repo_id:
            raise ForgeCodeSourceError("Forge snapshot repo_id must exactly match its canonical registry identity")
        if type(record.repo_kind) is not str or record.repo_kind != "project":
            raise ForgeCodeSourceError("Forge code snapshots require a registered project repository")
        if type(record.owner) is not str:
            raise ForgeCodeSourceError("repository owner cannot be bound to Forge authority")
        self._assert_repo_owner_scope(record.owner, raw_owner_scope)
        try:
            request_authority_values = _authority_primitives(raw_authority_binding)
        except ForgeCodeSourceError:
            raise ForgeCodeSourceError("Forge snapshot request has foreign adapter or admission authority") from None
        if request_authority_values != self._authority_values:
            raise ForgeCodeSourceError("Forge snapshot request has foreign adapter or admission authority")
        try:
            return ForgeSnapshotRequest(
                owner_scope=raw_owner_scope,
                authorization_ref=raw_authorization_ref,
                repo_id=raw_repo_id,
                version_id=raw_version_id,
                commit_sha=raw_commit_sha,
                authority_binding=_new_authority_binding(self._authority_values),
            )
        except ForgeSnapshotError:
            raise ForgeCodeSourceError("Forge snapshot request is invalid") from None

    @staticmethod
    def _assert_repo_owner_scope(repo_owner: str, owner_scope: str) -> None:
        """Bind RepoRecord.owner to its PVF-compatible opaque owner key.

        FCA-00 uses exactly ``owner:{owner_key_for(RepoRecord.owner)}``.  It
        never guesses aliases, lowercases an email, or exposes the registry's
        raw owner token in snapshot evidence.
        """

        if type(repo_owner) is not str or type(owner_scope) is not str:
            raise ForgeCodeSourceError("Forge snapshot owner scope does not match repository authority")
        try:
            owner_key = owner_key_for(repo_owner)
        except ProjectVersionStoreError:
            raise ForgeCodeSourceError("repository owner cannot be bound to Forge authority") from None
        if type(owner_key) is not str or owner_scope != f"owner:{owner_key}":
            raise ForgeCodeSourceError("Forge snapshot owner scope does not match repository authority")


def validate_forge_code_occurrence_records(
    records: CodeOccurrenceRecords,
) -> CodeOccurrenceRecords:
    """Revalidate a durable Forge chain at a persistence trust boundary."""

    if type(records) is not CodeOccurrenceRecords or type(records.forge_evidence) is not ForgeCodeOccurrenceEvidence:
        raise ForgeCodeSourceError("Forge occurrence records require inspectable canonical evidence")
    canonical = CodeOccurrenceRecords(
        records.source,
        records.source_version,
        records.chunk,
        records.forge_evidence,
    )
    evidence = canonical.forge_evidence
    if evidence.authority_binding != _expected_authority_values():
        raise ForgeCodeSourceError("Forge occurrence records have foreign adapter or admission authority")
    try:
        reference = _canonical_reference(
            ForgeExactReaderReference(
                owner_scope=evidence.owner_scope,
                repo_id=evidence.repo_id,
                version_id=evidence.version_id,
                commit_sha=evidence.commit_sha,
                snapshot_digest=evidence.snapshot_digest,
                path=evidence.path,
                content_sha256=evidence.file_content_sha256,
                max_bytes=1,
                authority_binding=_new_authority_binding(evidence.authority_binding),
            )
        )
    except (ForgeSnapshotError, TypeError, ValueError):
        raise ForgeCodeSourceError("Forge occurrence evidence is not canonical Forge evidence") from None
    if (
        reference.owner_scope,
        reference.repo_id,
        reference.version_id,
        reference.commit_sha,
        reference.snapshot_digest,
        _authority_primitives(reference.authority_binding),
        reference.path,
        reference.content_sha256,
    ) != (
        evidence.owner_scope,
        evidence.repo_id,
        evidence.version_id,
        evidence.commit_sha,
        evidence.snapshot_digest,
        evidence.authority_binding,
        evidence.path,
        evidence.file_content_sha256,
    ):
        raise ForgeCodeSourceError("Forge occurrence evidence is not canonical Forge evidence")
    return canonical


def _manifest_primitives(manifest: object) -> tuple[object, ...]:
    if type(manifest) is not SourceAdapterCapabilityManifest:
        raise ForgeCodeSourceError("registered Forge code capability must use the exact manifest type")
    string_fields = (
        manifest.adapter_id,
        manifest.adapter_version,
        manifest.domain_id,
        manifest.exact_reader_boundary,
    )
    if any(type(value) is not str for value in string_fields):
        raise ForgeCodeSourceError("registered Forge code capability contains noncanonical strings")
    enum_fields = (
        (manifest.source_kind, SourceKind),
        (manifest.content_policy, ContentPolicy),
        (manifest.classification_ceiling, Classification),
        (manifest.owner_scope_requirement, OwnerScopeRequirement),
        (manifest.provider_constraint, ProviderConstraint),
        (manifest.query_capability, QueryCapability),
    )
    if any(type(value) is not expected_type for value, expected_type in enum_fields):
        raise ForgeCodeSourceError("registered Forge code capability contains noncanonical enums")
    if type(manifest.operations) is not tuple or any(
        type(operation) is not SourceAdapterOperation for operation in manifest.operations
    ):
        raise ForgeCodeSourceError("registered Forge code capability contains noncanonical operations")
    if type(manifest.productive_default_enabled) is not bool:
        raise ForgeCodeSourceError("registered Forge code capability has noncanonical activation state")
    return (
        *string_fields,
        *(value.value for value, _ in enum_fields),
        tuple(operation.value for operation in manifest.operations),
        manifest.productive_default_enabled,
    )


def _authority_primitives(binding: object) -> tuple[str, str, str, str]:
    if type(binding) is not ForgeSnapshotAuthorityBinding:
        raise ForgeCodeSourceError("Forge authority binding must use the exact base type")
    try:
        canonical = ForgeSnapshotAuthorityBinding(
            adapter_id=binding.adapter_id,
            adapter_version=binding.adapter_version,
            adapter_generation=binding.adapter_generation,
            admission_policy_generation=binding.admission_policy_generation,
        )
    except ForgeSnapshotError:
        raise ForgeCodeSourceError("Forge authority binding contains noncanonical values") from None
    return (
        canonical.adapter_id,
        canonical.adapter_version,
        canonical.adapter_generation,
        canonical.admission_policy_generation,
    )


def _new_authority_binding(values: tuple[str, str, str, str]) -> ForgeSnapshotAuthorityBinding:
    if type(values) is not tuple or len(values) != 4 or any(type(value) is not str for value in values):
        raise ForgeCodeSourceError("internal Forge authority values are invalid")
    return ForgeSnapshotAuthorityBinding(
        adapter_id=values[0],
        adapter_version=values[1],
        adapter_generation=values[2],
        admission_policy_generation=values[3],
    )


def _expected_authority_values() -> tuple[str, str, str, str]:
    manifest = forge_code_capability_manifest()
    return (
        manifest.adapter_id,
        manifest.adapter_version,
        manifest.generation_ref,
        FORGE_CODE_ADMISSION_POLICY_GENERATION,
    )


def _forge_occurrence_records(
    *,
    owner_scope: str,
    repo_id: str,
    version_id: str,
    commit_sha: str,
    snapshot_digest: str,
    authority_binding: ForgeSnapshotAuthorityBinding,
    path: str,
    file_content_sha256: str,
    locator: CodeRangeLocator,
    extractor_profile_ref: str,
    version_observed_at: str,
    indexed_at: str,
) -> CodeOccurrenceRecords:
    authority = _authority_primitives(authority_binding)
    if authority != _expected_authority_values():
        raise ForgeCodeSourceError("Forge code occurrence has foreign adapter or admission authority")
    if type(extractor_profile_ref) is not str or not extractor_profile_ref.startswith("forge-code-"):
        raise ForgeCodeSourceError(
            "Forge occurrence extractor profile must carry the forge-code marker"
        )
    evidence = ForgeCodeOccurrenceEvidence(
        owner_scope=owner_scope,
        repo_id=repo_id,
        version_id=version_id,
        commit_sha=commit_sha,
        snapshot_digest=snapshot_digest,
        authority_binding=authority,
        path=path,
        file_content_sha256=file_content_sha256,
        locator=locator,
    )
    source = SourceRecord(
        owner_scope=owner_scope,
        source_kind=SourceKind.CODE,
        canonical_ref=evidence.source_ref(),
        classification=Classification.SENSITIVE,
        content_policy=ContentPolicy.REFERENCE_ONLY,
        provider_ref=FORGE_CODE_ADAPTER_ID,
    )
    source_version = SourceVersionRecord.create(
        source,
        revision_ref=evidence.revision_ref(),
        content_hash=file_content_sha256,
        version_observed_at=version_observed_at,
        indexed_at=indexed_at,
    )
    chunk = ChunkRecord.create(
        source_version,
        locator=locator,
        extractor_profile_ref=extractor_profile_ref,
        content_hash=file_content_sha256,
        content=None,
        indexed_at=indexed_at,
    )
    return CodeOccurrenceRecords(source, source_version, chunk, evidence)


def _forge_occurrence_id(
    reference: ForgeExactReaderReference,
    locator: CodeRangeLocator,
    extractor_profile_ref: str,
) -> str:
    evidence = ForgeCodeOccurrenceEvidence(
        owner_scope=reference.owner_scope,
        repo_id=reference.repo_id,
        version_id=reference.version_id,
        commit_sha=reference.commit_sha,
        snapshot_digest=reference.snapshot_digest,
        authority_binding=_authority_primitives(reference.authority_binding),
        path=reference.path,
        file_content_sha256=reference.content_sha256,
        locator=locator,
    )
    return evidence.occurrence_ref(extractor_profile_ref)


def _request_primitives(value: object) -> tuple[str, str, str, str, str, tuple[str, str, str, str]]:
    if type(value) is not ForgeSnapshotRequest:
        raise ForgeCodeSourceError("canonical Forge request must use the exact base type")
    try:
        canonical = ForgeSnapshotRequest(
            owner_scope=value.owner_scope,
            authorization_ref=value.authorization_ref,
            repo_id=value.repo_id,
            version_id=value.version_id,
            commit_sha=value.commit_sha,
            authority_binding=_new_authority_binding(_authority_primitives(value.authority_binding)),
        )
    except ForgeSnapshotError:
        raise ForgeCodeSourceError("canonical Forge request contains noncanonical values") from None
    return (
        canonical.owner_scope,
        canonical.authorization_ref,
        canonical.repo_id,
        canonical.version_id,
        canonical.commit_sha,
        _authority_primitives(canonical.authority_binding),
    )


def _canonical_file(value: object) -> ForgeSnapshotFile:
    if type(value) is not ForgeSnapshotFile:
        raise ForgeCodeSourceError("Forge snapshot file must use the exact base type")
    return ForgeSnapshotFile(
        path=value.path,
        content_sha256=value.content_sha256,
        byte_count=value.byte_count,
    )


def _canonical_inventory(value: object) -> ForgeSnapshotInventory:
    if type(value) is not ForgeSnapshotInventory:
        raise ForgeCodeSourceError("Forge snapshot inventory must use the exact base type")
    if type(value.files) is not tuple or len(value.files) > MAX_FORGE_SNAPSHOT_FILES:
        raise ForgeCodeSourceError("Forge snapshot files are invalid or unbounded")
    files = tuple(_canonical_file(item) for item in value.files)
    binding = _new_authority_binding(_authority_primitives(value.authority_binding))
    return ForgeSnapshotInventory(
        owner_scope=value.owner_scope,
        repo_id=value.repo_id,
        version_id=value.version_id,
        commit_sha=value.commit_sha,
        manifest_sha256=value.manifest_sha256,
        authority_binding=binding,
        files=files,
        snapshot_digest=value.snapshot_digest,
    )


def _canonical_reference(value: object) -> ForgeExactReaderReference:
    if type(value) is not ForgeExactReaderReference:
        raise ForgeCodeSourceError("Forge exact-reader reference must use the exact base type")
    binding = _new_authority_binding(_authority_primitives(value.authority_binding))
    return ForgeExactReaderReference(
        owner_scope=value.owner_scope,
        repo_id=value.repo_id,
        version_id=value.version_id,
        commit_sha=value.commit_sha,
        snapshot_digest=value.snapshot_digest,
        path=value.path,
        content_sha256=value.content_sha256,
        max_bytes=value.max_bytes,
        authority_binding=binding,
    )


__all__ = [
    "FORGE_CODE_ADAPTER_ID",
    "FORGE_CODE_ADMISSION_POLICY_GENERATION",
    "FORGE_CODE_DOMAIN_ID",
    "FORGE_CODE_EXACT_READER_BOUNDARY",
    "ForgeCodeSnapshotRequest",
    "ForgeCodeOccurrence",
    "ForgeCodeSource",
    "ForgeCodeSourceError",
    "validate_forge_code_occurrence_records",
    "forge_code_capability_manifest",
]
