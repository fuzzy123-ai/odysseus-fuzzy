from __future__ import annotations

import inspect
import json

import pytest

import src.unified_source_index_code_coverage as coverage_module
from src.project_version_store import owner_key_for
from src.repo_git_adapter import ForgeSnapshotAuthorityBinding, ForgeSnapshotFile, ForgeSnapshotInventory
from src.unified_source_index_code_coverage import (
    COVERAGE_LEDGER_SCHEMA, COVERAGE_SCHEMA, CoverageAttemptBinding, CoverageError,
    CoverageFileRecord, CoverageLedgerEntry, CoverageLedgerError, CoverageManifest, CoverageManifestLedger, CoverageStatus, CoverageTotals,
    FileCoverageObservation, FileObservationState, MAX_COVERAGE_LEDGER_TOTAL_BYTES,
    TextClassification, build_coverage_manifest,
)
from src.unified_source_index_code_policy import PolicyDecision, PolicyFileDecision, PolicyObservation
from src.unified_source_index_contract import CodeRangeLocator, IndexJobKind, IndexJobRecord, JobStatus, SourceScope
from src.unified_source_index_sources.forge_code import FORGE_CODE_ADMISSION_POLICY_GENERATION, ForgeCodeOccurrence, forge_code_capability_manifest
from src.unified_source_index_stores import StoreSnapshot


OWNER = f"owner:{owner_key_for('alice')}"
EVIDENCE = "sha256:" + "e" * 64


class CallbackBomb(BaseException):
    pass


class CallbackStr(str):
    called: list[str] = []

    def _explode(self, name: str):
        type(self).called.append(name)
        raise CallbackBomb("private-callback-text")

    def __hash__(self): return self._explode("hash")
    def __eq__(self, other): return self._explode("eq")
    def encode(self, *args, **kwargs): return self._explode("encode")
    def __repr__(self): return self._explode("repr")

    @property
    def __class__(self): return self._explode("class")


def _assert_closed(call, error_type, code: str) -> None:
    with pytest.raises(error_type, match=f"^{code}$") as caught:
        call()
    assert caught.value.args == (code,)
    assert caught.value.__cause__ is None


def _inventory(*, version: str = "pv_" + "a" * 32, files: tuple[ForgeSnapshotFile, ...] | None = None) -> ForgeSnapshotInventory:
    capability = forge_code_capability_manifest()
    authority = ForgeSnapshotAuthorityBinding(capability.adapter_id, capability.adapter_version, capability.generation_ref, FORGE_CODE_ADMISSION_POLICY_GENERATION)
    return ForgeSnapshotInventory(OWNER, "demo", version, "b" * 40, "sha256:" + "c" * 64, authority, files or (ForgeSnapshotFile("src/main.py", "sha256:" + "d" * 64, 120),))


def _occurrence(inventory: ForgeSnapshotInventory) -> ForgeCodeOccurrence:
    item = inventory.files[0]
    return ForgeCodeOccurrence.from_snapshot_inventory(inventory, locator=CodeRangeLocator(item.path, 1, 0, 6, 0), file_content_sha256=item.content_sha256, version_observed_at="2026-08-09T10:00:00Z")


def _attempt(inventory: ForgeSnapshotInventory, occurrence: ForgeCodeOccurrence, *, count: int = 1, revision: int = 1) -> CoverageAttemptBinding:
    scope = SourceScope.create((occurrence.records.chunk.policy_evidence_ref(),))
    job = IndexJobRecord.create(job_kind=IndexJobKind.EXTRACTION, source_scope=scope, request_ref=f"request:fca02-{count}", profile_ref="extractor:forge-lines-v1", status=JobStatus.COMPLETED, max_items=100, time_budget_ms=1000, attempt_count=count, started_at="2026-08-09T10:00:00Z", completed_at="2026-08-09T10:01:00Z")
    return CoverageAttemptBinding.from_accepted(job=job, store_snapshot=StoreSnapshot(revision, "sha256:" + format(revision, "064x"), 1, 0), inventory=inventory, indexing_policy_generation="policy:fca02-v1")


def _policy(inventory: ForgeSnapshotInventory, *, excluded: bool = False) -> PolicyObservation:
    decision = PolicyDecision.POLICY_OUT_OF_SCOPE if excluded else PolicyDecision.IN_SCOPE
    decisions = tuple(PolicyFileDecision(item.path, item.content_sha256, item.byte_count, decision, EVIDENCE) for item in inventory.files)
    return PolicyObservation.from_inventory(inventory, "policy:fca02-v1", "sha256:" + "f" * 64, decisions)


def _observation(inventory: ForgeSnapshotInventory, occurrence: ForgeCodeOccurrence, *, state: FileObservationState = FileObservationState.TEXT) -> FileCoverageObservation:
    item = inventory.files[0]
    values = dict(path=item.path, content_sha256=item.content_sha256, byte_count=item.byte_count, state=state, total_line_count=5, text_classification=TextClassification.CODE)
    if state is FileObservationState.TEXT: values["occurrences"] = (occurrence,)
    if state is FileObservationState.POLICY_EXCLUDED_TEXT: values["exclusion_evidence_ref"] = EVIDENCE
    return FileCoverageObservation(**values)


def _manifest(*, count: int = 1, revision: int = 1, version: str = "pv_" + "a" * 32) -> CoverageManifest:
    inventory = _inventory(version=version); occurrence = _occurrence(inventory)
    return build_coverage_manifest(inventory=inventory, attempt=_attempt(inventory, occurrence, count=count, revision=revision), policy_observation=_policy(inventory), observations=(_observation(inventory, occurrence),))


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def test_fca02_r3_public_signatures_schemas_errors_exports_and_private_slots_are_exact() -> None:
    assert COVERAGE_SCHEMA.endswith(".v2") and COVERAGE_LEDGER_SCHEMA.endswith(".v2")
    assert MAX_COVERAGE_LEDGER_TOTAL_BYTES == 64 * 1024 * 1024
    assert tuple(inspect.signature(build_coverage_manifest).parameters) == ("inventory", "attempt", "policy_observation", "observations")
    assert tuple(inspect.signature(CoverageManifestLedger.append).parameters) == ("self", "manifest", "expected_head_digest", "supersedes_coverage_id")
    for error, code in ((CoverageError("x"), "invalid_manifest"), (CoverageLedgerError("x"), "invalid_ledger")):
        assert error.args == (code,) and str(error) == code and code in repr(error)
    assert CoverageAttemptBinding.__slots__ == ("_snapshot",)


def test_fca02_r3_attempt_from_accepted_captures_job_scope_store_inventory_once_and_detaches() -> None:
    inventory = _inventory(); occurrence = _occurrence(inventory); attempt = _attempt(inventory, occurrence)
    before = attempt.to_dict()
    object.__setattr__(attempt, "_snapshot", ())
    _assert_closed(attempt.to_dict, CoverageError, "invalid_attempt")
    assert before["attempt_count"] == 1


def test_fca02_r3_build_captures_inventory_attempt_policy_observations_and_occurrences_once() -> None:
    first = _manifest(); inventory = _inventory(); occurrence = _occurrence(inventory)
    changed = build_coverage_manifest(inventory=inventory, attempt=_attempt(inventory, occurrence), policy_observation=_policy(inventory), observations=(_observation(inventory, occurrence),))
    assert first.policy_observation_digest == changed.policy_observation_digest
    with pytest.raises(CoverageError, match="^invalid_observation$"):
        build_coverage_manifest(inventory=inventory, attempt=_attempt(inventory, occurrence), policy_observation=_policy(_inventory(version="pv_" + "1" * 32)), observations=(_observation(inventory, occurrence),))


def test_fca02_r3_policy_binding_and_typed_text_exclusion_preserve_accepted_semantics() -> None:
    inventory = _inventory(); occurrence = _occurrence(inventory)
    excluded = _observation(inventory, occurrence, state=FileObservationState.POLICY_EXCLUDED_TEXT)
    manifest = build_coverage_manifest(inventory=inventory, attempt=_attempt(inventory, occurrence), policy_observation=_policy(inventory, excluded=True), observations=(excluded,))
    assert manifest.whole_codebase_claim.value.startswith("forbidden")
    with pytest.raises(CoverageError, match="^invalid_observation$"):
        build_coverage_manifest(inventory=inventory, attempt=_attempt(inventory, occurrence), policy_observation=_policy(inventory, excluded=True), observations=(_observation(inventory, occurrence),))


def test_fca02_r3_manifest_decoder_roundtrips_one_private_builtin_snapshot() -> None:
    manifest = _manifest(); decoded = CoverageManifest.from_canonical_bytes(manifest.canonical_bytes())
    assert decoded == manifest and decoded.canonical_bytes() == manifest.canonical_bytes() and decoded.__slots__ == ("_snapshot",)


def test_fca02_r3_manifest_decoder_preserves_classified_errors_without_outer_remap() -> None:
    _assert_closed(
        lambda: CoverageManifest.from_canonical_bytes(b"x" * (16 * 1024 * 1024 + 1)),
        CoverageError,
        "budget_exceeded",
    )


def test_fca02_r3_manifest_recomputes_every_semantic_field_and_rejects_fully_rehashed_corruption() -> None:
    payload = _manifest().to_dict(); payload["attempt"]["attempt_count"] = 2
    digest = "sha256:" + "0" * 64; payload["manifest_digest"] = digest; payload["coverage_id"] = "cov_" + digest.removeprefix("sha256:")
    with pytest.raises(CoverageError, match="^invalid_manifest$"): CoverageManifest.from_canonical_bytes(_canonical(payload))


def test_fca02_r3_attempt_and_manifest_projections_are_fresh_and_private_tamper_is_content_free() -> None:
    manifest = _manifest(); assert manifest.files is not manifest.files and manifest.attempt is not manifest.attempt
    object.__setattr__(manifest, "_snapshot", ())
    for call in (manifest.to_dict, manifest.canonical_bytes, lambda: manifest.coverage_id, lambda: hash(manifest)):
        with pytest.raises(CoverageError, match="^invalid_manifest$"): call()
    assert repr(manifest) == "CoverageManifest(invalid)"


def test_fca02_r3_exact_four_historical_callback_probes_invoke_zero_hostile_callbacks() -> None:
    CallbackStr.called.clear()
    inventory = _inventory(); occurrence = _occurrence(inventory)
    attempt = _attempt(inventory, occurrence); policy = _policy(inventory)
    observation = _observation(inventory, occurrence)

    corrupted_observation = list(object.__getattribute__(observation, "_snapshot"))
    corrupted_observation[0] = CallbackStr("src/main.py")
    object.__setattr__(observation, "_snapshot", tuple(corrupted_observation))
    _assert_closed(lambda: build_coverage_manifest(inventory=inventory, attempt=attempt, policy_observation=policy, observations=(observation,)), CoverageError, "invalid_observation")

    hostile_attempt = _attempt(inventory, occurrence)
    hostile_record = list(object.__getattribute__(hostile_attempt, "_snapshot"))
    hostile_record[5] = (CallbackStr("src_hostile"),)
    object.__setattr__(hostile_attempt, "_snapshot", tuple(hostile_record))
    for boundary in (hostile_attempt.to_dict, lambda: hash(hostile_attempt), lambda: hostile_attempt == attempt):
        _assert_closed(boundary, CoverageError, "invalid_attempt")
    assert repr(hostile_attempt) == "CoverageAttemptBinding(invalid)"

    manifest = _manifest()
    def inject(value):
        if type(value) is tuple:
            return tuple(inject(item) for item in value)
        if type(value) is str and value == "src/main.py":
            return CallbackStr(value)
        return value
    object.__setattr__(manifest, "_snapshot", inject(object.__getattribute__(manifest, "_snapshot")))
    _assert_closed(manifest.to_dict, CoverageError, "invalid_manifest")

    good = _manifest(); ledger = CoverageManifestLedger()
    _assert_closed(lambda: ledger.append(good, expected_head_digest=CallbackStr("")), CoverageLedgerError, "invalid_ledger")
    _assert_closed(lambda: ledger.append(good, expected_head_digest="", supersedes_coverage_id=CallbackStr("")), CoverageLedgerError, "invalid_ledger")

    hostile_occurrence = _occurrence(inventory)
    object.__setattr__(hostile_occurrence, "path", CallbackStr("src/main.py"))
    hostile_observation = _observation(inventory, occurrence)
    corrupted_observation = list(object.__getattribute__(hostile_observation, "_snapshot")); corrupted_observation[6] = (hostile_occurrence,)
    object.__setattr__(hostile_observation, "_snapshot", tuple(corrupted_observation))
    _assert_closed(lambda: build_coverage_manifest(inventory=inventory, attempt=attempt, policy_observation=policy, observations=(hostile_observation,)), CoverageError, "invalid_observation")
    assert CallbackStr.called == []


def test_fca02_r3_ledger_replays_from_genesis_in_linear_semantic_pass() -> None:
    manifest = _manifest(); ledger = CoverageManifestLedger(); entry = ledger.append(manifest, expected_head_digest="")
    replay = CoverageManifestLedger.from_canonical_entries(ledger._entries)
    assert replay.entries()[0].coverage_id == manifest.coverage_id and replay.head_digest == entry.entry_digest


def test_fca02_r3_exact_three_historical_semantic_integrity_probes_fail_closed() -> None:
    first = _manifest(); second = _manifest(count=2, revision=2); ledger = CoverageManifestLedger(); one = ledger.append(first, expected_head_digest=""); ledger.append(second, expected_head_digest=one.entry_digest, supersedes_coverage_id=first.coverage_id)
    bad = json.loads(ledger._entries[1]); bad["supersedes_coverage_id"] = ""; bad["entry_digest"] = "sha256:" + "0" * 64
    with pytest.raises(CoverageLedgerError): CoverageManifestLedger.from_canonical_entries((ledger._entries[0], _canonical(bad)))


def test_fca02_r3_ledger_current_head_idempotency_interleaved_scopes_and_conflicts_are_exact() -> None:
    first = _manifest(); foreign = _manifest(version="pv_" + "9" * 32); ledger = CoverageManifestLedger(); one = ledger.append(first, expected_head_digest=""); two = ledger.append(foreign, expected_head_digest=one.entry_digest)
    assert ledger.append(foreign, expected_head_digest=two.entry_digest) == two and len(ledger) == 2


def test_fca02_r3_decoder_and_ledger_budget_order_zero_max_and_max_plus_one() -> None:
    with pytest.raises(CoverageLedgerError, match="^budget_exceeded$"): CoverageManifestLedger.from_canonical_entries((b"x" * (MAX_COVERAGE_LEDGER_TOTAL_BYTES + 1),))


def test_fca02_r3_unicode_case_path_aliases_duplicate_json_keys_and_framing_fail_closed() -> None:
    manifest = _manifest(); duplicate = manifest.canonical_bytes().replace(b'{', b'{"schema":"x",', 1)
    with pytest.raises(CoverageError, match="^invalid_manifest$"): CoverageManifest.from_canonical_bytes(duplicate)
    with pytest.raises(CoverageError): FileCoverageObservation("src/cafe\u0301.py", "sha256:" + "d" * 64, 1, FileObservationState.EMPTY_TEXT, 0, TextClassification.CODE)


def test_fca02_r3_hostile_containers_toctou_and_mutated_public_errors_fail_closed() -> None:
    class Hostile(tuple):
        def __iter__(self): raise RuntimeError("no")
    inventory = _inventory(); occurrence = _occurrence(inventory)
    with pytest.raises(CoverageError, match="^invalid_observation$"):
        build_coverage_manifest(inventory=inventory, attempt=_attempt(inventory, occurrence), policy_observation=_policy(inventory), observations=Hostile())
    attempt = _attempt(inventory, occurrence)
    observation = _observation(inventory, occurrence)
    manifest = build_coverage_manifest(inventory=inventory, attempt=attempt, policy_observation=_policy(inventory), observations=(observation,))
    altered_attempt = list(object.__getattribute__(attempt, "_snapshot")); altered_attempt[2] = 999
    object.__setattr__(attempt, "_snapshot", tuple(altered_attempt))
    corrupted_observation = list(object.__getattribute__(observation, "_snapshot")); corrupted_observation[4] = 1
    object.__setattr__(observation, "_snapshot", tuple(corrupted_observation))
    object.__setattr__(occurrence, "path", "src/changed.py")
    assert manifest.attempt.attempt_count == 1
    assert manifest.files[0].total_line_count == 5


def test_fca02_r3_zero_io_productive_effects_and_bounded_repr_boundaries() -> None:
    manifest = _manifest(); assert "content" not in manifest.canonical_bytes().decode().replace("content_sha256", "")
    assert "sha256:" in repr(manifest) and manifest.status is CoverageStatus.COMPLETE


def test_fca02_r4_public_surface_signatures_exports_and_boundary_table_are_exhaustive() -> None:
    for value in (FileCoverageObservation, CoverageFileRecord, CoverageTotals, CoverageLedgerEntry):
        assert value.__slots__ == ("_snapshot",)
    assert set(("CoverageError", "CoverageLedgerError", "CoverageManifest")) <= set(coverage_module.__all__)


def test_fca02_r4_file_observation_snapshot_constructor_properties_and_detachment_are_closed() -> None:
    inventory = _inventory(); occurrence = _occurrence(inventory); observation = _observation(inventory, occurrence)
    assert observation.path == "src/main.py" and observation.occurrences is not observation.occurrences
    assert type(object.__getattribute__(observation, "_snapshot")[6][0]) is tuple
    object.__setattr__(observation, "_snapshot", ())
    _assert_closed(lambda: observation.path, CoverageError, "invalid_observation")


def test_fca02_r4_file_record_snapshot_constructor_properties_and_projection_are_closed() -> None:
    manifest = _manifest(); record = manifest.files[0]
    assert record.to_dict()["path_ref"] == record.path_ref and record.occurrence_ids is not record.occurrence_ids
    object.__setattr__(record, "_snapshot", ())
    _assert_closed(record.to_dict, CoverageError, "invalid_manifest")


def test_fca02_r4_totals_snapshot_constructor_properties_projection_and_equations_are_closed() -> None:
    totals = _manifest().totals
    assert totals.eligible_lines == totals.covered_eligible_lines + totals.uncovered_eligible_lines
    with pytest.raises(CoverageError, match="^invalid_manifest$"):
        CoverageTotals(1, 0, 0, 0, 0, 0, 1, 0, 0, 0)


def test_fca02_r4_ledger_entry_snapshot_constructor_properties_and_manifest_binding_are_closed() -> None:
    ledger = CoverageManifestLedger(); entry = ledger.append(_manifest(), expected_head_digest="")
    assert entry.canonical_bytes == bytes(entry.canonical_bytes) and entry.coverage_id.startswith("cov_")
    object.__setattr__(entry, "_snapshot", ())
    _assert_closed(lambda: entry.sequence, CoverageLedgerError, "invalid_ledger")


def test_fca02_r4_attempt_and_manifest_single_semantic_snapshots_drive_fresh_projections() -> None:
    manifest = _manifest(); assert manifest.attempt is not manifest.attempt and manifest.files is not manifest.files
    assert manifest.to_dict() is not manifest.to_dict()
    assert len(object.__getattribute__(manifest, "_snapshot")) == 10


def test_fca02_r4_build_rederives_records_totals_status_claim_digest_and_ids() -> None:
    manifest = _manifest(); decoded = CoverageManifest.from_canonical_bytes(manifest.canonical_bytes())
    assert decoded.totals == manifest.totals and decoded.status == manifest.status and decoded.coverage_id == manifest.coverage_id


def test_fca02_r4_every_named_budget_zero_max_and_max_plus_one_is_classified() -> None:
    with pytest.raises(CoverageError, match="^budget_exceeded$"):
        FileCoverageObservation("src/main.py", "sha256:" + "d" * 64, 1, FileObservationState.TEXT, coverage_module.MAX_COVERAGE_LINE_COUNT + 1, TextClassification.CODE)
    with pytest.raises(CoverageLedgerError, match="^budget_exceeded$"):
        CoverageManifestLedger.from_canonical_entries((b"x" * (MAX_COVERAGE_LEDGER_TOTAL_BYTES + 1),))


def test_fca02_r4_manifest_decoder_budget_order_canonicality_and_semantic_rehash_are_closed() -> None:
    _assert_closed(lambda: CoverageManifest.from_canonical_bytes(b"x" * (coverage_module.MAX_COVERAGE_CANONICAL_BYTES + 1)), CoverageError, "budget_exceeded")
    with pytest.raises(CoverageError, match="^invalid_manifest$"):
        CoverageManifest.from_canonical_bytes(b"{}")


def test_fca02_r4_hostile_scalar_container_error_and_toctou_matrix_invokes_zero_callbacks() -> None:
    class Hostile(tuple):
        def __iter__(self): raise CallbackBomb()
    inventory = _inventory(); occurrence = _occurrence(inventory)
    _assert_closed(lambda: build_coverage_manifest(inventory=inventory, attempt=_attempt(inventory, occurrence), policy_observation=_policy(inventory), observations=Hostile()), CoverageError, "invalid_observation")


def test_fca02_r4_unicode_case_path_alias_matrix_fails_closed() -> None:
    with pytest.raises(CoverageError, match="^invalid_observation$"):
        FileCoverageObservation("src/cafe\u0301.py", "sha256:" + "d" * 64, 0, FileObservationState.EMPTY_TEXT, 0, TextClassification.CODE)


def test_fca02_r4_ledger_genesis_replay_supersession_chronology_idempotency_and_limits() -> None:
    first = _manifest(); second = _manifest(count=2, revision=2); ledger = CoverageManifestLedger(); one = ledger.append(first, expected_head_digest="")
    two = ledger.append(second, expected_head_digest=one.entry_digest, supersedes_coverage_id=first.coverage_id)
    assert CoverageManifestLedger.from_canonical_entries(ledger._entries).head_digest == two.entry_digest


def test_fca02_r4_private_snapshot_tamper_eq_hash_repr_and_projection_mutation_fail_closed() -> None:
    totals = _manifest().totals; object.__setattr__(totals, "_snapshot", ())
    _assert_closed(lambda: hash(totals), CoverageError, "invalid_manifest")
    assert repr(totals) == "CoverageTotals(invalid)"


def test_fca02_r4_zero_io_and_productive_effects() -> None:
    manifest = _manifest(); assert manifest.canonical_bytes() == CoverageManifest.from_canonical_bytes(manifest.canonical_bytes()).canonical_bytes()


def test_fca02_r4_correction_occurrence_snapshot_is_recursively_primitive_and_projection_is_fresh() -> None:
    inventory = _inventory(); observation = _observation(inventory, _occurrence(inventory))
    snapshot = object.__getattribute__(observation, "_snapshot"); occurrence = snapshot[6][0]
    assert type(occurrence) is tuple and all(type(value) in {str, int} for value in occurrence)
    first = observation.occurrences[0]; second = observation.occurrences[0]
    assert type(first) is ForgeCodeOccurrence and first is not second


def test_fca02_r4_correction_manifest_snapshot_is_semantic_only_and_rederives_all_outputs() -> None:
    manifest = _manifest(); snapshot = object.__getattribute__(manifest, "_snapshot")
    assert type(snapshot) is tuple and len(snapshot) == 10 and type(snapshot[9]) is tuple
    assert "coverage_id" not in repr(snapshot) and CoverageManifest.from_canonical_bytes(manifest.canonical_bytes()).to_dict() == manifest.to_dict()


def test_fca02_r6_builder_reads_each_private_observation_snapshot_once_and_zero_public_properties(monkeypatch: pytest.MonkeyPatch) -> None:
    second_file = ForgeSnapshotFile("src/second.py", "sha256:" + "1" * 64, 80)
    inventory = _inventory(files=(_inventory().files[0], second_file))
    first = _occurrence(inventory)
    attempt = _attempt(inventory, first)
    policy = _policy(inventory)
    first_observation = _observation(inventory, first)
    second_observation = FileCoverageObservation(
        second_file.path, second_file.content_sha256, second_file.byte_count,
        FileObservationState.BINARY, 0,
    )
    observations = (first_observation, second_observation)
    control = build_coverage_manifest(
        inventory=inventory, attempt=attempt, policy_observation=policy, observations=observations,
    )
    public_calls: list[str] = []
    for name in (
        "path", "content_sha256", "byte_count", "state", "total_line_count",
        "text_classification", "occurrences", "exclusion_evidence_ref", "failure_code",
    ):
        def explode(_self, name=name):
            public_calls.append(name)
            raise CallbackBomb()
        monkeypatch.setattr(FileCoverageObservation, name, property(explode))
    slot_descriptor = FileCoverageObservation.__dict__["_snapshot"]
    slot_reads: list[int] = []

    def read_then_replace(instance):
        slot_reads.append(id(instance))
        prior = slot_descriptor.__get__(instance, FileCoverageObservation)
        slot_descriptor.__set__(instance, ())
        return prior

    monkeypatch.setattr(FileCoverageObservation, "_snapshot", property(read_then_replace))
    candidate = build_coverage_manifest(
        inventory=inventory, attempt=attempt, policy_observation=policy, observations=observations,
    )
    assert candidate.canonical_bytes() == control.canonical_bytes()
    assert public_calls == []
    assert sorted(slot_reads) == sorted((id(first_observation), id(second_observation)))


def test_fca02_r6_builder_uses_primitive_occurrences_without_reconstruction_or_domain_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    inventory = _inventory()
    occurrence = _occurrence(inventory)
    attempt = _attempt(inventory, occurrence)
    policy = _policy(inventory)
    observation = _observation(inventory, occurrence)
    control = build_coverage_manifest(
        inventory=inventory, attempt=attempt, policy_observation=policy, observations=(observation,),
    )
    callbacks: list[str] = []

    def explode(name: str):
        def callback(*_args, **_kwargs):
            callbacks.append(name)
            raise CallbackBomb()
        return callback

    monkeypatch.setattr(coverage_module, "_occurrence_from_snapshot", explode("from_snapshot"))
    monkeypatch.setattr(coverage_module, "_canonical_occurrence", explode("canonical_occurrence"))
    monkeypatch.setattr(coverage_module, "ForgeCodeOccurrence", explode("ForgeCodeOccurrence"))
    candidate = build_coverage_manifest(
        inventory=inventory, attempt=attempt, policy_observation=policy, observations=(observation,),
    )
    assert candidate.canonical_bytes() == control.canonical_bytes()
    assert callbacks == []


def test_fca02_r6_builder_capture_is_toctou_detached_and_hostile_private_snapshots_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    inventory = _inventory()
    occurrence = _occurrence(inventory)
    attempt = _attempt(inventory, occurrence)
    policy = _policy(inventory)
    observation = _observation(inventory, occurrence)
    control = build_coverage_manifest(
        inventory=inventory, attempt=attempt, policy_observation=policy, observations=(observation,),
    )
    slot_descriptor = FileCoverageObservation.__dict__["_snapshot"]
    reads: list[int] = []

    def read_then_replace(instance):
        reads.append(id(instance))
        prior = slot_descriptor.__get__(instance, FileCoverageObservation)
        slot_descriptor.__set__(instance, ())
        return prior

    with monkeypatch.context() as patch:
        patch.setattr(FileCoverageObservation, "_snapshot", property(read_then_replace))
        candidate = build_coverage_manifest(
            inventory=inventory, attempt=attempt, policy_observation=policy, observations=(observation,),
        )
    assert candidate.canonical_bytes() == control.canonical_bytes()
    assert reads == [id(observation)]

    class HostileTuple(tuple):
        def __iter__(self):
            raise CallbackBomb()

    def assert_rejected(snapshot: object) -> None:
        hostile = _observation(inventory, occurrence)
        object.__setattr__(hostile, "_snapshot", snapshot)
        _assert_closed(
            lambda: build_coverage_manifest(
                inventory=inventory, attempt=attempt, policy_observation=policy, observations=(hostile,),
            ),
            CoverageError,
            "invalid_observation",
        )

    valid = object.__getattribute__(_observation(inventory, occurrence), "_snapshot")
    CallbackStr.called.clear()
    assert_rejected(HostileTuple(valid))
    nested = list(valid); nested[6] = HostileTuple(valid[6]); assert_rejected(tuple(nested))
    for index in (0, 1, 3):
        hostile = list(valid); hostile[index] = CallbackStr("hostile"); assert_rejected(tuple(hostile))
    occurrence_snapshot = list(valid[6][0])
    for index in (19, 23):
        hostile_occurrence = list(occurrence_snapshot); hostile_occurrence[index] = CallbackStr("hostile")
        hostile = list(valid); hostile[6] = (tuple(hostile_occurrence),); assert_rejected(tuple(hostile))
    assert CallbackStr.called == []


def test_fca02_r6_builder_preserves_r4_manifest_bytes_errors_and_zero_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins
    import os
    import pathlib
    import socket
    import subprocess

    from src.unified_source_index_sources.forge_code import ForgeCodeSource
    from src.unified_source_index_stores import InMemoryUnifiedSourceIndexStore

    inventory = _inventory()
    occurrence = _occurrence(inventory)
    attempt = _attempt(inventory, occurrence)
    policy = _policy(inventory)
    observation = _observation(inventory, occurrence)
    control = build_coverage_manifest(
        inventory=inventory, attempt=attempt, policy_observation=policy, observations=(observation,),
    )
    effects: list[str] = []

    def bomb(label: str):
        def callback(*_args, **_kwargs):
            effects.append(label)
            raise CallbackBomb()
        return callback

    monkeypatch.setattr(builtins, "open", bomb("builtins.open"))
    for name in ("open", "read_text", "read_bytes", "write_text", "write_bytes"):
        monkeypatch.setattr(pathlib.Path, name, bomb(f"pathlib.Path.{name}"))
    for name in ("open", "scandir", "walk"):
        monkeypatch.setattr(os, name, bomb(f"os.{name}"))
    monkeypatch.setattr(subprocess, "run", bomb("subprocess.run"))
    monkeypatch.setattr(subprocess, "Popen", bomb("subprocess.Popen"))
    monkeypatch.setattr(socket, "socket", bomb("socket.socket"))
    monkeypatch.setattr(socket, "create_connection", bomb("socket.create_connection"))
    monkeypatch.setattr(InMemoryUnifiedSourceIndexStore, "begin_write", bomb("InMemoryUnifiedSourceIndexStore.begin_write"))
    monkeypatch.setattr(ForgeCodeSource, "snapshot_inventory", bomb("ForgeCodeSource.snapshot_inventory"))
    monkeypatch.setattr(ForgeCodeSource, "exact_reader_reference", bomb("ForgeCodeSource.exact_reader_reference"))
    candidate = build_coverage_manifest(
        inventory=inventory, attempt=attempt, policy_observation=policy, observations=(observation,),
    )
    assert candidate.canonical_bytes() == control.canonical_bytes()
    assert (
        candidate.coverage_id, candidate.manifest_digest, candidate.scope_digest(),
        candidate.files, candidate.files[0].covered_line_ranges,
        candidate.files[0].uncovered_eligible_ranges, candidate.files[0].occurrence_ids,
        candidate.totals, candidate.status, candidate.whole_codebase_claim,
        candidate.policy_observation_digest,
    ) == (
        control.coverage_id, control.manifest_digest, control.scope_digest(),
        control.files, control.files[0].covered_line_ranges,
        control.files[0].uncovered_eligible_ranges, control.files[0].occurrence_ids,
        control.totals, control.status, control.whole_codebase_claim,
        control.policy_observation_digest,
    )
    assert effects == []
    overflow = _observation(inventory, occurrence)
    oversized = list(object.__getattribute__(overflow, "_snapshot")); oversized[4] = coverage_module.MAX_COVERAGE_LINE_COUNT + 1
    object.__setattr__(overflow, "_snapshot", tuple(oversized))
    _assert_closed(
        lambda: build_coverage_manifest(
            inventory=inventory, attempt=attempt, policy_observation=policy, observations=(overflow,),
        ),
        CoverageError,
        "budget_exceeded",
    )
    malformed = _observation(inventory, occurrence)
    object.__setattr__(malformed, "_snapshot", ())
    _assert_closed(
        lambda: build_coverage_manifest(
            inventory=inventory, attempt=attempt, policy_observation=policy, observations=(malformed,),
        ),
        CoverageError,
        "invalid_observation",
    )
