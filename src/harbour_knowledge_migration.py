"""Deterministic, preflighted real-source migration into private staging.

Plans hold private payload bytes in detached commands; every public representation
is deliberately content free. This module never selects or activates a generation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Mapping, Sequence
from weakref import WeakKeyDictionary

from src.causal_claim_store import CausalClaimOwnerSnapshot, CausalClaimStore
from src.harbour_knowledge_store import CAUSAL_CLAIM_PAYLOAD_SCHEMA, KnowledgeHead
from src.harbour_knowledge_staging_destination import (
    HarbourKnowledgeStagingDestinationService,
    StagingDestinationLease,
    StagingDestinationReceipt,
    StagingMigrationCompletionReference,
    StagingVerifiedCompletion,
)
from src.memory_owner_eligibility import MemoryOwnerEligibilitySnapshot
from src.personal_docs_owner_reader import PersonalDocsOwnerReader, PersonalDocsSnapshot
from src.unified_source_index_sources.memory import MemorySourceAdapter

_SCHEMA = "odysseus.harbour.knowledge_real_migration.v1"
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_COMMANDS = 25_000
_MAX_BYTES = 64 * 1024 * 1024


class KnowledgeMigrationError(ValueError):
    """A bounded, content-free migration error."""

    __slots__ = ("code", "exclusion_counts")

    def __init__(self, code: str, exclusion_counts: tuple[tuple[str, int], ...] = ()) -> None:
        self.code = code if code in {"invalid_plan", "preflight_failed", "source_incomplete", "staging_failed", "verification_failed"} else "preflight_failed"
        self.exclusion_counts = tuple(exclusion_counts)
        super().__init__(self.code)

    def __repr__(self) -> str:
        return f"KnowledgeMigrationError({self.code!r})"


def _fail(code: str) -> None:
    raise KnowledgeMigrationError(code) from None


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("ascii")


def _sha_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _sha(value: object) -> str:
    return _sha_bytes(_canonical(value))


def _stable_id(prefix: str, value: object) -> str:
    return prefix + hashlib.sha256(_canonical(value)).hexdigest()


def _scope(value: object) -> str:
    if type(value) is not str or not value or len(value.encode("utf-8")) > 256 or any(ord(ch) < 32 for ch in value):
        _fail("invalid_plan")
    return value


def _retention(value: object) -> dict[str, Any]:
    fields = {"retention_class", "deletion_mode", "retain_until", "grace_until", "legal_hold"}
    if type(value) is not dict or set(value) != fields:
        _fail("invalid_plan")
    if (value["retention_class"] not in {"user_managed", "time_bound", "permanent", "legal_hold"}
            or value["deletion_mode"] not in {"tombstone_only", "purge_eligible"}
            or type(value["legal_hold"]) is not bool):
        _fail("invalid_plan")
    result = dict(value)
    for name in ("retain_until", "grace_until"):
        item = result[name]
        if item is not None:
            if type(item) is not str or not item.endswith("Z"):
                _fail("invalid_plan")
            try:
                parsed = datetime.fromisoformat(item[:-1] + "+00:00")
            except ValueError:
                _fail("invalid_plan")
            if parsed.tzinfo is None:
                _fail("invalid_plan")
            result[name] = parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if result["retention_class"] == "legal_hold" and not result["legal_hold"]:
        _fail("invalid_plan")
    return result


def _freeze_json(value: object) -> object:
    return json.loads(json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True))


@dataclass(frozen=True, slots=True, repr=False)
class MemoryMigrationSource:
    scope: str
    adapter: MemorySourceAdapter = field(repr=False)
    eligibility: MemoryOwnerEligibilitySnapshot = field(repr=False)
    binding_digest: str
    snapshot_digest: str


@dataclass(frozen=True, slots=True, repr=False)
class PersonalDocsMigrationSource:
    scope: str
    reader: PersonalDocsOwnerReader = field(repr=False)
    snapshot: PersonalDocsSnapshot = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class CausalMigrationSource:
    scope: str
    store: CausalClaimStore = field(repr=False)
    owner_id: str = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class _Command:
    owner_id: str = field(repr=False)
    knowledge_id: str
    command_kind: str
    payload: bytes = field(repr=False)
    payload_schema_id: str
    payload_media_type: str
    classification: str
    source_revisions: tuple[dict[str, Any], ...]
    provenance: tuple[dict[str, Any], ...]
    retention: dict[str, Any]
    idempotency_key: str
    expected_generation: int = 0


_PREPARED_PLANS: WeakKeyDictionary[object, tuple[bytes, bytes]] = WeakKeyDictionary()


class PreparedKnowledgeMigration:
    """Opaque, process-local handle for a factory-prepared migration plan."""

    __slots__ = ("_summary_bytes", "__weakref__")

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        _fail("invalid_plan")

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("prepared migration summaries are read-only")

    def _summary(self) -> dict[str, Any]:
        record = _PREPARED_PLANS.get(self)
        if record is None or self._summary_bytes != record[0]:
            _fail("invalid_plan")
        return json.loads(record[0])

    @property
    def migration_id(self) -> str:
        return self._summary()["migration_id"]

    @property
    def plan_digest(self) -> str:
        return self._summary()["plan_digest"]

    @property
    def source_manifest_digest(self) -> str:
        return self._summary()["source_manifest_digest"]

    @property
    def census_digest(self) -> str:
        return self._summary()["census_digest"]

    @property
    def owners(self) -> tuple[str, ...]:
        return tuple(self._summary()["owners"])

    @property
    def source_count(self) -> int:
        return self._summary()["source_count"]

    @property
    def zero_census(self) -> bool:
        return self._summary()["zero_census"]

    @property
    def zero_census(self) -> bool:
        return self._summary()["zero_census"]

    @property
    def exclusion_counts(self) -> tuple[tuple[str, int], ...]:
        return tuple(tuple(item) for item in self._summary()["exclusion_counts"])

    @property
    def commands(self) -> tuple[_Command, ...]:
        record = _PREPARED_PLANS.get(self)
        if record is None or self._summary_bytes != record[0]:
            _fail("invalid_plan")
        return _commands_from_snapshot(record[1])

    def __repr__(self) -> str:
        summary = self._summary()
        return (f"PreparedKnowledgeMigration(command_count={summary['command_count']}, "
                f"source_count={summary['source_count']}, plan_digest={summary['plan_digest']!r})")


def _command_snapshot(command: _Command) -> dict[str, Any]:
    return {
        "owner_id": command.owner_id, "knowledge_id": command.knowledge_id,
        "command_kind": command.command_kind, "payload_hex": command.payload.hex(),
        "payload_schema_id": command.payload_schema_id,
        "payload_media_type": command.payload_media_type, "classification": command.classification,
        "source_revisions": command.source_revisions, "provenance": command.provenance,
        "retention": command.retention, "idempotency_key": command.idempotency_key,
        "expected_generation": command.expected_generation,
    }


def _commands_snapshot(commands: tuple[_Command, ...]) -> bytes:
    return _canonical([_command_snapshot(command) for command in commands])


def _commands_from_snapshot(snapshot: bytes) -> tuple[_Command, ...]:
    rows = json.loads(snapshot)
    return tuple(_Command(
        row["owner_id"], row["knowledge_id"], row["command_kind"], bytes.fromhex(row["payload_hex"]),
        row["payload_schema_id"], row["payload_media_type"], row["classification"],
        tuple(row["source_revisions"]), tuple(row["provenance"]), row["retention"],
        row["idempotency_key"], row["expected_generation"],
    ) for row in rows)


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedKnowledgeMigration:
    migration_id_digest: str
    plan_digest: str
    source_manifest_digest: str
    completion_reference: StagingMigrationCompletionReference
    verification: StagingVerifiedCompletion = field(repr=False)
    admitted_count: int
    write_count: int

    def __repr__(self) -> str:
        return "VerifiedKnowledgeMigration(<content-free>)"


def _source_revision(*, source_kind: str, source_id: str, revision_ref: str, content: bytes,
                     locator_digest: str, authority_revision: str, authority_digest: str,
                     observed_at: str) -> dict[str, Any]:
    return {
        "source_revision_id": _stable_id("sr-", {"kind": source_kind, "id": source_id, "revision": revision_ref}),
        "source_kind": source_kind, "source_id": source_id, "revision_ref": revision_ref,
        "source_content_sha256": _sha_bytes(content), "disposition": "imported",
        "locator_digest": locator_digest, "authority_domain": "legacy_source",
        "authority_revision": authority_revision, "authority_digest": authority_digest,
        "predecessor_source_revision_id": None, "observed_at": observed_at,
    }


def _provenance(source_revision_id: str, authority_digest: str) -> tuple[dict[str, Any], ...]:
    return ({"role": "origin", "source_revision_id": source_revision_id,
             "knowledge_revision_id": None, "transformation_digest": None,
             "admission_receipt_digest": authority_digest},)


def _target(owner_id: str, source_kind: str, source_ref: str) -> str:
    return _stable_id("km-", {"schema": _SCHEMA, "owner_id": owner_id, "source_kind": source_kind, "source_ref": source_ref})


def _command_identity(command: _Command) -> dict[str, Any]:
    return {"owner": command.owner_id, "knowledge_id": command.knowledge_id,
        "command_kind": command.command_kind,
        "knowledge_kind": "causal_claim" if command.payload_schema_id == CAUSAL_CLAIM_PAYLOAD_SCHEMA else
            ("personal_memory" if command.payload_schema_id == "odysseus.legacy_memory.text.v1" else "document"),
        "payload": _sha_bytes(command.payload), "schema": command.payload_schema_id,
        "media": command.payload_media_type, "classification": command.classification,
        "sources": _freeze_json(command.source_revisions), "provenance": _freeze_json(command.provenance),
        "retention": _retention(command.retention), "key": command.idempotency_key}


def _plan_manifest(census_digest: str, owners: tuple[str, ...], commands: tuple[_Command, ...],
                   source_count: int, exclusions: tuple[tuple[str, int], ...]) -> str:
    if not _DIGEST.fullmatch(census_digest) or type(source_count) is not int or source_count < 1:
        _fail("invalid_plan")
    normalized_owners = tuple(sorted(owners))
    if normalized_owners != owners or len(set(owners)) != len(owners):
        _fail("invalid_plan")
    return _sha({"schema": _SCHEMA, "census_digest": census_digest, "owners": owners,
        "source_count": source_count, "exclusions": exclusions,
        "commands": [_command_identity(item) for item in commands]})


def prepare_current_knowledge_migration(
    *, memory_sources: Sequence[MemoryMigrationSource] = (),
    personal_docs_sources: Sequence[PersonalDocsMigrationSource] = (),
    causal_sources: Sequence[CausalMigrationSource] = (),
    owner_map: Mapping[str, str], retention_by_scope: Mapping[str, Mapping[str, Any]],
    observed_at: str, allow_zero_census: bool = False,
) -> PreparedKnowledgeMigration:
    """Census sources before staging; observed_at and source snapshots bind identity.

    Reprepare with the same observed_at, source snapshots, owner map, and retention
    decisions after a process restart to reconnect an existing authenticated ledger.
    A new observation or source census intentionally produces a new migration.
    """
    try:
        if (type(observed_at) is not str or not observed_at.endswith("Z")
                or type(allow_zero_census) is not bool
                or type(owner_map) is not dict or type(retention_by_scope) is not dict):
            _fail("invalid_plan")
        observed_at = datetime.fromisoformat(observed_at[:-1] + "+00:00").astimezone(timezone.utc).isoformat(
            timespec="microseconds").replace("+00:00", "Z")
        if any(type(key) is not str for key in owner_map) or any(type(key) is not str for key in retention_by_scope):
            _fail("invalid_plan")
        sources = tuple(memory_sources) + tuple(personal_docs_sources) + tuple(causal_sources)
        if not sources or len(sources) > 256:
            _fail("source_incomplete")
        scopes = tuple(_scope(item.scope) for item in sources)
        if len(set(scopes)) != len(scopes) or set(scopes) != set(owner_map) or set(scopes) != set(retention_by_scope):
            _fail("invalid_plan")
        owners_by_scope: dict[str, str] = {}
        retention: dict[str, dict[str, Any]] = {}
        for scope in scopes:
            owner = owner_map[scope]
            if type(owner) is not str or _ID.fullmatch(owner) is None:
                _fail("invalid_plan")
            owners_by_scope[scope] = owner
            retention[scope] = _retention(retention_by_scope[scope])
        commands: list[_Command] = []
        causal_bases: list[_Command] = []
        causal_histories: list[_Command] = []
        census: list[dict[str, Any]] = []
        excluded: list[tuple[str, int]] = []
        total_bytes = 0
        source_proofs: list[dict[str, Any]] = []

        def add(scope: str, source_kind: str, source_ref: str, revision_ref: str,
                content: bytes, locator_digest: str, authority_revision: str,
                authority_digest: str, schema: str, media: str) -> None:
            nonlocal total_bytes
            total_bytes += len(content)
            if len(commands) >= _MAX_COMMANDS or total_bytes > _MAX_BYTES or not content:
                _fail("source_incomplete")
            owner = owners_by_scope[scope]
            source_row = _source_revision(source_kind=source_kind, source_id=_stable_id("src-", source_ref),
                revision_ref=revision_ref, content=content, locator_digest=locator_digest,
                authority_revision=authority_revision, authority_digest=authority_digest, observed_at=observed_at)
            source_id = source_row["source_revision_id"]
            key = _stable_id("kmop-", {"owner": owner, "kind": source_kind, "source": source_ref, "command": len(commands)})
            commands.append(_Command(owner, _target(owner, source_kind, source_ref), "create_object",
                bytes(content), schema, media, "sensitive", (source_row,), _provenance(source_id, authority_digest),
                retention[scope], key))
            census.append({"scope_digest": _sha(scope), "owner_digest": _sha(owner), "kind": source_kind,
                "source_ref_digest": _sha(source_ref), "revision_ref": revision_ref,
                "payload_digest": _sha_bytes(content), "command_key_digest": _sha(key)})

        for item in memory_sources:
            scope = _scope(item.scope)
            if type(item.adapter) is not MemorySourceAdapter or type(item.eligibility) is not MemoryOwnerEligibilitySnapshot:
                _fail("source_incomplete")
            if item.eligibility.snapshot_digest != item.snapshot_digest:
                _fail("source_incomplete")
            if item.eligibility.ineligible_count or item.eligibility.eligible_count != item.eligibility.total_records:
                excluded.append(("memory_ineligible", item.eligibility.ineligible_count))
                raise KnowledgeMigrationError("source_incomplete", tuple(sorted(excluded)))
            if not _DIGEST.fullmatch(item.binding_digest) or not _DIGEST.fullmatch(item.snapshot_digest):
                _fail("source_incomplete")
            source_proofs.append({"kind": "memory", "scope_digest": _sha(scope),
                "binding_digest": item.binding_digest, "snapshot_digest": item.snapshot_digest,
                "total_records": item.eligibility.total_records,
                "eligible_count": item.eligibility.eligible_count,
                "ineligible_count": item.eligibility.ineligible_count})
            cursor = 0
            source_start = len(census)
            while True:
                page = item.adapter.discover(expected_binding_digest=item.binding_digest,
                    expected_snapshot_digest=item.snapshot_digest, cursor=cursor, limit=100)
                for descriptor in page.items:
                    if descriptor.source.owner_scope != scope:
                        _fail("source_incomplete")
                    occurrence = item.adapter.extract(descriptor.evidence.record_ref,
                        expected_binding_digest=item.binding_digest, expected_snapshot_digest=item.snapshot_digest)
                    if occurrence.evidence.record_digest != descriptor.evidence.record_digest:
                        _fail("source_incomplete")
                    raw = occurrence.chunk.content.encode("utf-8", "strict")
                    add(scope, "memory", descriptor.evidence.record_ref, descriptor.evidence.record_digest,
                        raw, descriptor.locator.locator_digest, item.eligibility.snapshot_digest,
                        item.eligibility.source_digest, "odysseus.legacy_memory.text.v1", "text/plain")
                if page.next_cursor is None:
                    break
                cursor = page.next_cursor
            if len(census) - source_start != item.eligibility.eligible_count:
                _fail("source_incomplete")

        for item in personal_docs_sources:
            scope = _scope(item.scope)
            if type(item.reader) is not PersonalDocsOwnerReader or type(item.snapshot) is not PersonalDocsSnapshot:
                _fail("source_incomplete")
            if not item.snapshot.owner_ref:
                _fail("source_incomplete")
            if item.snapshot.owner_ref != scope:
                _fail("source_incomplete")
            if item.snapshot.unsupported_count:
                excluded.append(("personal_docs_unsupported", item.snapshot.unsupported_count))
                raise KnowledgeMigrationError("source_incomplete", tuple(sorted(excluded)))
            source_proofs.append({"kind": "personal_docs", "scope_digest": _sha(scope),
                "owner_ref_digest": _sha(item.snapshot.owner_ref),
                "snapshot_digest": item.snapshot.snapshot_digest,
                "descriptor_count": len(item.snapshot.descriptors),
                "unsupported_count": item.snapshot.unsupported_count})
            for descriptor in item.snapshot.descriptors:
                exact = item.reader.read_exact(descriptor)
                payload = exact.content.encode("utf-8", "strict")
                add(scope, "personal_document", exact.source_ref, exact.revision_ref, payload,
                    descriptor.locator.locator_digest, item.snapshot.snapshot_digest,
                    item.snapshot.owner_ref, "odysseus.personal_document.utf8.v1", descriptor.mime_type.split(";")[0])

        for item in causal_sources:
            scope = _scope(item.scope)
            if type(item.store) is not CausalClaimStore or type(item.owner_id) is not str:
                _fail("source_incomplete")
            if item.owner_id != scope:
                _fail("source_incomplete")
            snapshot: CausalClaimOwnerSnapshot = item.store.capture_owner_snapshot(item.owner_id)
            source_proofs.append({"kind": "causal", "scope_digest": _sha(scope),
                "snapshot_digest": _sha({"claims": snapshot.claims}),
                "claim_count": len(snapshot.claims)})
            for report, events in snapshot.claims:
                claim_ref = report["claim_ref"]
                source_revision_digest = _sha({"report": report, "events": events})
                initial_claim_digest = events[0]["prior_digest"] if events else report["head_digest"]
                claim_id = report["claim_id"]
                common = {"legacy_claim_ref": claim_ref, "legacy_claim_digest": initial_claim_digest,
                    "claim_id": claim_id, "claim_version": report["claim_version"], "policy_ref": report["policy"],
                    "source_version": report["source_version"], "candidate_actor_ref": report["candidate_actor"],
                    "evidence_refs": list(report["evidence_refs"])}
                bodies = [{**common, "epistemic_state": "candidate", "legacy_head_sequence": 0,
                    "legacy_head_digest": initial_claim_digest, "transition": None}]
                for sequence, event in enumerate(events, 1):
                    body = {**common, "epistemic_state": event["to_state"], "legacy_head_sequence": sequence,
                        "legacy_head_digest": event["event_digest"], "transition": {
                            "event_id": event["event_id"], "from_state": event["from_state"], "to_state": event["to_state"],
                            "reviewer_ref": event["reviewer"], "evidence_refs": list(event["evidence_refs"]),
                            "relation_kind": event["relation_kind"], "related_claim_ref": event["related_claim_ref"],
                            "prior_claim_version": event["prior_claim_version"], "reason_code": event["reason_code"]}}
                    bodies.append(body)
                source_bytes = _canonical({"report": report, "events": list(events)})
                total_bytes += len(source_bytes)
                if total_bytes > _MAX_BYTES:
                    _fail("source_incomplete")
                source_row = _source_revision(source_kind="causal_claim", source_id=claim_id,
                    revision_ref=source_revision_digest, content=source_bytes, locator_digest=snapshot.snapshot_digest,
                    authority_revision=snapshot.snapshot_digest, authority_digest=snapshot.snapshot_digest,
                    observed_at=observed_at)
                target_owner = owners_by_scope[scope]
                kid = _target(target_owner, "causal_claim", claim_ref)
                idem = _stable_id("kmop-", {"owner": target_owner, "kind": "causal_claim", "source": claim_ref, "command": 0})
                base_payload = _canonical(bodies[0])
                total_bytes += len(base_payload)
                if (len(commands) + len(causal_bases) + len(causal_histories) >= _MAX_COMMANDS
                        or total_bytes > _MAX_BYTES):
                    _fail("source_incomplete")
                causal_bases.append(_Command(target_owner, kid, "create_object", base_payload,
                    CAUSAL_CLAIM_PAYLOAD_SCHEMA, "application/json", "sensitive", (source_row,),
                    _provenance(source_row["source_revision_id"], snapshot.snapshot_digest), retention[scope], idem))
                for index, body in enumerate(bodies[1:], 1):
                    key = _stable_id("kmop-", {"owner": target_owner, "kind": "causal_claim", "source": claim_ref, "command": index})
                    payload = _canonical(body)
                    total_bytes += len(payload)
                    if (len(commands) + len(causal_bases) + len(causal_histories) >= _MAX_COMMANDS
                            or total_bytes > _MAX_BYTES):
                        _fail("source_incomplete")
                    causal_histories.append(_Command(target_owner, kid, "append_revision", payload,
                        CAUSAL_CLAIM_PAYLOAD_SCHEMA, "application/json", "sensitive", (source_row,),
                        _provenance(source_row["source_revision_id"], snapshot.snapshot_digest), retention[scope], key))
                census.append({"scope_digest": _sha(scope), "owner_digest": _sha(target_owner),
                    "kind": "causal_claim", "source_ref_digest": _sha(claim_ref),
                    "revision_ref": source_revision_digest, "payload_digest": _sha_bytes(source_bytes),
                    "command_key_digest": _sha(idem), "history_count": len(events)})

        # Every immutable causal base exists before a dependent review history
        # is applied, even when the legacy owner snapshot sorts claims apart.
        commands.extend(causal_bases)
        commands.extend(causal_histories)
        if (not commands and not allow_zero_census) or len(commands) > _MAX_COMMANDS:
            _fail("source_incomplete")
        if not commands and census:
            _fail("source_incomplete")
        exclusions = tuple(sorted(excluded))
        owners = tuple(sorted(set(owners_by_scope.values())))
        census_source_count = len(census) if census else len(source_proofs)
        census_digest = _sha({"schema": _SCHEMA, "observed_at": observed_at, "census": census,
            "source_proofs": source_proofs, "zero_census": not census,
            "excluded": exclusions, "source_count": census_source_count})
        source_manifest = _plan_manifest(census_digest, owners, tuple(commands), census_source_count, exclusions)
        migration_id = "knowledge-migration:" + source_manifest.removeprefix("sha256:")
        summary = {"migration_id": migration_id, "plan_digest": source_manifest,
            "source_manifest_digest": source_manifest, "census_digest": census_digest,
            "owners": owners, "source_count": census_source_count, "zero_census": not census,
            "exclusion_counts": exclusions, "command_count": len(commands)}
        summary_bytes = _canonical(summary)
        command_bytes = _commands_snapshot(tuple(commands))
        handle = object.__new__(PreparedKnowledgeMigration)
        object.__setattr__(handle, "_summary_bytes", summary_bytes)
        _PREPARED_PLANS[handle] = (summary_bytes, command_bytes)
        return handle
    except KnowledgeMigrationError:
        raise
    except BaseException:
        _fail("preflight_failed")


def prepare_authenticated_zero_census_knowledge_migration(**kwargs: Any) -> PreparedKnowledgeMigration:
    """Explicitly attest complete real source snapshots that contain no eligible records."""
    plan = prepare_current_knowledge_migration(allow_zero_census=True, **kwargs)
    if plan.commands or not plan.source_count:
        _fail("source_incomplete")
    return plan


def migrate_prepared_knowledge_migration(
    plan: PreparedKnowledgeMigration, *,
    staging: HarbourKnowledgeStagingDestinationService,
    principals: Mapping[str, object], ttl_seconds: int = 300,
) -> VerifiedKnowledgeMigration:
    """Persist deterministic commands into a fresh private generation."""
    try:
        if type(plan) is not PreparedKnowledgeMigration or type(staging) is not HarbourKnowledgeStagingDestinationService:
            _fail("invalid_plan")
        registered = _PREPARED_PLANS.get(plan)
        if registered is None or plan._summary_bytes != registered[0]:
            _fail("invalid_plan")
        summary = json.loads(registered[0])
        commands = _commands_from_snapshot(registered[1])
        if len(commands) > _MAX_COMMANDS:
            _fail("invalid_plan")
        owners = tuple(summary["owners"])
        exclusions = tuple(tuple(item) for item in summary["exclusion_counts"])
        recomputed = _plan_manifest(summary["census_digest"], owners, commands,
            summary["source_count"], exclusions)
        if (recomputed != summary["plan_digest"] or recomputed != summary["source_manifest_digest"]
                or summary["migration_id"] != "knowledge-migration:" + recomputed.removeprefix("sha256:")
                or any(command.owner_id not in owners for command in commands)):
            _fail("invalid_plan")
        if type(principals) is not dict or set(principals) != set(owners):
            _fail("invalid_plan")
        resumed = staging.resume_or_reserve(summary["migration_id"], owners,
            summary["source_manifest_digest"], ttl_seconds)
        if type(resumed) is StagingMigrationCompletionReference:
            verification = staging.verify_completion(resumed)
            if (verification.migration_digest != _sha(summary["migration_id"])
                    or verification.source_manifest_digest != summary["source_manifest_digest"]):
                _fail("verification_failed")
            return VerifiedKnowledgeMigration(_sha(summary["migration_id"]), summary["plan_digest"],
                summary["source_manifest_digest"], resumed, verification, summary["source_count"], len(commands))
        session, lease = resumed
        receipts: list[StagingDestinationReceipt] = []
        expected_operations: list[dict[str, Any]] = []
        for command in commands:
            principal = principals[command.owner_id]
            kwargs = {"knowledge_id": command.knowledge_id, "payload_bytes": command.payload,
                "payload_schema_id": command.payload_schema_id, "payload_media_type": command.payload_media_type,
                "classification": command.classification, "source_revisions": command.source_revisions,
                "provenance": command.provenance, "retention": command.retention,
                "idempotency_key": command.idempotency_key}
            knowledge_kind = "causal_claim" if command.payload_schema_id == CAUSAL_CLAIM_PAYLOAD_SCHEMA else (
                "personal_memory" if command.payload_schema_id == "odysseus.legacy_memory.text.v1" else "document")
            expected_operations.append({"owner_id": command.owner_id, "knowledge_id": command.knowledge_id,
                "command_kind": command.command_kind, "knowledge_kind": knowledge_kind,
                "idempotency_key": command.idempotency_key, "payload_sha256": _sha_bytes(command.payload),
                "payload_schema_id": command.payload_schema_id, "payload_media_type": command.payload_media_type,
                "classification": command.classification, "source_revisions": list(command.source_revisions),
                "provenance": list(command.provenance), "retention": dict(command.retention)})
            if command.command_kind == "create_object":
                receipt = session.create_object(lease, principal, owner_id=command.owner_id,
                    knowledge_kind=knowledge_kind, **kwargs)
            else:
                current = _expected_append_head(
                    session, lease, principal, owner_id=command.owner_id,
                    knowledge_id=command.knowledge_id, idempotency_key=command.idempotency_key,
                )
                receipt = session.append_revision(lease, principal, owner_id=command.owner_id,
                    expected_head=current, **kwargs)
            receipts.append(receipt)
        completion = staging.complete_migration(lease, principals=principals,
            expected_operations=expected_operations)
        verification = staging.verify_completion(completion)
        return VerifiedKnowledgeMigration(_sha(summary["migration_id"]), summary["plan_digest"],
            summary["source_manifest_digest"], completion, verification,
            summary["source_count"], len(receipts))
    except KnowledgeMigrationError:
        raise
    except BaseException:
        _fail("staging_failed")


def _expected_append_head(
    session: Any, lease: StagingDestinationLease, principal: object, *,
    owner_id: str, knowledge_id: str, idempotency_key: str,
) -> KnowledgeHead:
    """Use the authenticated event's original before-head when resuming an append."""
    cursor = 0
    events_seen = 0
    matching: dict[str, Any] | None = None
    while True:
        page = session.changes_since(
            lease, principal, owner_id=owner_id, after_event_seq=cursor, limit=500,
        )
        if not page:
            break
        events_seen += len(page)
        if events_seen > _MAX_COMMANDS:
            _fail("staging_failed")
        for event in page:
            if (event.get("owner_id") == owner_id
                    and event.get("idempotency_key") == idempotency_key
                    and event.get("command_kind") == "append_revision"
                    and event.get("knowledge_id") == knowledge_id):
                if matching is not None:
                    _fail("staging_failed")
                matching = event
        cursor = page[-1]["event_seq"]
        if len(page) < 500:
            break
    if matching is None:
        return session.get_head(
            lease, principal, owner_id=owner_id, knowledge_id=knowledge_id,
        )
    before = matching.get("before_head")
    if type(before) is not dict:
        _fail("staging_failed")
    try:
        return KnowledgeHead(**before)
    except BaseException:
        _fail("staging_failed")


__all__ = [
    "MemoryMigrationSource", "PersonalDocsMigrationSource", "CausalMigrationSource",
    "PreparedKnowledgeMigration", "VerifiedKnowledgeMigration", "KnowledgeMigrationError",
    "prepare_current_knowledge_migration", "migrate_prepared_knowledge_migration",
]
