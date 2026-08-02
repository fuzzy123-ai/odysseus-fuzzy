#!/usr/bin/env python3
"""Validate the deterministic ULO-00 lifecycle-owner inventory without side effects."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


INVENTORY_NAME = "unified-source-index-lifecycle-owner-inventory.json"
OBSOLETE_NAMES = frozenset({"unified-source-index-lifecycle-inventory.json"})
ACTION_FIELDS = (
    "action_id", "canonical_action_owner", "authorization_boundary", "scope",
    "usi_participation", "rollback_failure", "execution_authorized", "synthetic_only",
    "evidence_refs",
)
ARTIFACT_FIELDS = (
    "artifact_id", "artifact_class", "persistent_members", "lifecycle_policy_owner",
    "owner_scope", "current_eligibility", "retain_purge_rebuild",
    "generation_tombstone_semantics", "rollback_failure", "evidence_refs",
)
EFFECT_FLAGS = {
    "execution_authorized": False,
    "synthetic_only": True,
    "productive_source_access": False,
    "provider_access": False,
    "adapter_registration": False,
    "index_write": False,
    "domain_mutation": False,
    "live_action": False,
}
UNIMPLEMENTED_REQUIREMENT_IDS = (
    "account_domain_erasure_usi_integration",
    "personal_docs_physical_delete_or_access_revocation",
    "system_restore_endpoint_and_generation_swap",
    "factory_reset_mapping",
    "usi_retention_and_gc",
)


def _action(
    action_id: str, owner: str, authorization: str, scope: str, participation: str,
    rollback_failure: str, evidence_refs: tuple[str, ...],
) -> dict[str, Any]:
    return dict(zip(
        ACTION_FIELDS,
        (action_id, owner, authorization, scope, participation, rollback_failure, False, True, list(evidence_refs)),
        strict=True,
    ))


def _artifact(
    artifact_id: str, artifact_class: str, members: tuple[str, ...], owner: str,
    owner_scope: str, eligibility: str, retain_purge_rebuild: str, semantics: str,
    rollback_failure: str, evidence_refs: tuple[str, ...],
) -> dict[str, Any]:
    return dict(zip(
        ARTIFACT_FIELDS,
        (artifact_id, artifact_class, list(members), owner, owner_scope, eligibility,
         retain_purge_rebuild, semantics, rollback_failure, list(evidence_refs)),
        strict=True,
    ))


# This is deliberately a hard-coded contract. The auditor never discovers, invokes,
# or authorizes lifecycle owners; it only rejects drift in the accepted inventory.
EXPECTED_INVENTORY: dict[str, Any] = {
    "schema_version": 1,
    "kind": "odysseus.unified_source_index.lifecycle_owner_inventory",
    "action_fields": list(ACTION_FIELDS),
    "actions": [
        _action("auth_user_rename", "routes.auth_routes.rename_user to AuthManager.rename_user to migrate_renamed_user_references", "require_admin and existing auth policy", "auth identity plus existing owner-reference migrations", "future ULO-04 stable owner-scope alias integration only", "SQL-owner failure attempts auth rollback; later helper failures are non-atomic and must report degraded/compensated state", ("routes/auth_routes.py:rename_user", "routes/auth_user_rename.py:migrate_renamed_user_references")),
        _action("auth_user_delete", "routes.auth_routes.admin_delete_user to AuthManager.delete_user", "require_admin and no self-delete", "auth entry, API tokens and sessions only; not domain erasure or USI cleanup", "future ULO-05 after exact owner/domain erasure contract", "token-store failure fails closed before auth removal; no complete cross-domain rollback exists", ("routes/auth_routes.py:admin_delete_user", "core/auth.py:AuthManager.delete_user")),
        _action("personal_memory_record_delete", "src.memory_provider.NativeMemoryProvider.delete", "caller-provided owner scope", "one Personal Memory record plus best-effort vector projection removal", "observe committed unavailability only; never delete Memory truth", "Memory truth is saved before vector removal, so projection failure is degraded and non-transactional", ("src/memory_provider.py:NativeMemoryProvider.delete",)),
        _action("personal_docs_exclude_file", "src.personal_docs.PersonalDocsManager.exclude_file", "not enforced by this class", "exclusion registry and in-memory index only; not source deletion", "future UDA/ULO access-loss observation after owner handoff", "no transactional rollback exists", ("src/personal_docs.py:PersonalDocsManager.exclude_file",)),
        _action("personal_docs_remove_directory", "src.personal_docs.PersonalDocsManager.remove_directory", "not enforced by this class", "registered directory and best-effort legacy RAG chunk removal; not physical owner-file deletion", "future deletion/access observation after owner handoff", "RAG failure is logged/swallowed and no compensation exists", ("src/personal_docs.py:PersonalDocsManager.remove_directory",)),
        _action("user_portability_export", "routes.backup_routes.export_data", "require_admin and current-user scope", "selected domain data and shared settings; never raw shared USI/Chroma database", "future ULO-07 redacted coverage manifest only", "read-only output has no rollback", ("routes/backup_routes.py:export_data",)),
        _action("user_portability_import", "routes.backup_routes.import_data", "require_admin and current-user merge scope", "domain truth merge; never shared USI database import", "future ULO-07 rediscovery after domain restore", "no global transaction/rollback across imported domains", ("routes/backup_routes.py:import_data",)),
        _action("system_backup_now", "routes.system_update_routes.backup_now to start_system_update_action(backup_now)", "require_admin plus existing service/capability gate", "existing system backup workflow; no current USI participation", "future ULO-08 handoff to USI-13 primitives", "existing action reports blocked, failed or started", ("routes/system_update_routes.py:backup_now", "src/system_update_status.py:start_system_update_action")),
        _action("admin_category_wipe", "routes.admin_wipe_routes.wipe", "require_admin and explicit supported category", "named category only; existing direct DB/file/projection actions", "future ULO-06 exact category scope and USI fence", "no current cross-store rollback exists", ("routes/admin_wipe_routes.py:wipe",)),
        _action("usi13_sqlite_backup", "src.unified_source_index_backup.backup_sqlite_store", "explicit USI SQLite store and fresh contained temporary target only", "self-contained snapshot/WAL verified SQLite copy; not system backup integration", "USI-13 primitive", "fresh target child is removed on failure", ("src/unified_source_index_backup.py:backup_sqlite_store",)),
        _action("usi13_sqlite_restore", "src.unified_source_index_backup.restore_sqlite_backup", "typed receipt plus fresh contained temporary target only", "validated isolated restored store; not production selection or system restore endpoint", "USI-13 primitive", "fresh target child is removed on failure; no generation swap is claimed", ("src/unified_source_index_backup.py:restore_sqlite_backup",)),
        _action("usi13_projection_rebuild", "src.unified_source_index_backup.rebuild_projections", "explicit SQLite store plus injected rebuilders", "FTS at one stable snapshot plus explicitly injected external rebuilders", "USI-13 primitive", "missing/failed external rebuilders yield incomplete, never success or core-truth mutation", ("src/unified_source_index_backup.py:rebuild_projections",)),
    ],
    "unimplemented_requirement_ids": list(UNIMPLEMENTED_REQUIREMENT_IDS),
    "artifact_fields": list(ARTIFACT_FIELDS),
    "artifacts": [
        _artifact("usi_store_state_and_snapshots", "truth", ("usi_store_state", "usi_snapshots"), "USI core SQLite store", "all selected USI owner scopes", "snapshot fence and committed store state", "retain as store/snapshot fence; never purge from a source-domain action", "monotonic snapshot revision and state hash", "USI-13 backup/restore only; stale or missing snapshot fails closed", ("src/unified_source_index_migrations.py", "src/unified_source_index_sqlite.py")),
        _artifact("usi_index_truth_records", "truth", ("usi_sources", "usi_source_versions", "usi_chunks", "usi_entities", "usi_relations", "usi_lineage"), "USI core transactional record store", "record owner_scope plus selected source policy", "source policy and owner eligibility", "retain until explicit lifecycle tombstone/retention policy; never rebuild domain truth", "source/version/chunk identity is stable; typed tombstone reserves identity and explicit restore is required", "failed cleanup remains pending/degraded; no domain mutation", ("src/unified_source_index_contract.py:RecordKind", "src/unified_source_index_stores.py")),
        _artifact("usi_jobs", "truth", ("usi_jobs",), "USI JobStore", "job owner_scope and selected UDA domain scope", "accepted domain/source policy only", "future ULO-10 retention/retry; never silently discard pending cleanup", "lease/retry/completion state remains canonical job evidence", "failed cleanup remains pending/degraded rather than complete", ("src/unified_source_index_contract.py:IndexJobRecord", "src/unified_source_index_stores.py:JobStore")),
        _artifact("usi_tombstones_and_record_history", "truth", ("usi_tombstones", "usi_record_history"), "USI core transactional store", "record owner_scope", "content-free convergence and audit evidence only", "retention follows source/domain erasure policy; never reconstruct erased content", "tombstone reserves record identity with previous/current revision and typed restore", "erasure is never reversed from derived data", ("src/unified_source_index_stores.py:TombstoneRecord", "src/unified_source_index_migrations.py")),
        _artifact("usi_projection_manifest_records", "truth", ("usi_projection_manifests",), "USI core record store", "manifest owner_scope and input snapshot", "accepted provider/policy evidence only", "retain with corresponding generation/evidence; output itself remains rebuildable", "manifest binds projection kind, generation and input snapshot", "manifest never implies provider selection or successful external rebuild", ("src/unified_source_index_contract.py:ProjectionManifest",)),
        _artifact("usi_derived_run_records", "truth", ("usi_derived_runs",), "USI/RAPTOR DerivedRun record store", "derived-run owner_scope and input snapshot", "accepted bounded derived-run evidence", "retain input/evidence identity; derived output may be deleted/rebuilt without source truth drift", "run kind/version/input snapshot and evidence refs are immutable", "failed/incomplete run cannot become accepted projection evidence", ("src/unified_source_index_contract.py:DerivedRunRecord",)),
        _artifact("usi_chunk_fts", "rebuildable", ("usi_chunk_fts", "usi_chunks_ai", "usi_chunks_ad", "usi_chunks_au"), "USI-13 projection rebuild primitive", "same owner/source eligibility as core chunk records", "fixed core snapshot only", "rebuild only from one stable core snapshot", "no independent source/version identity", "failed rebuild is failed/incomplete and cannot alter core truth", ("src/unified_source_index_migrations.py", "src/unified_source_index_backup.py:rebuild_projections")),
        _artifact("usi_sqlite_backup_copy", "truth", ("source-index.sqlite3", "USIBackupReceipt", "USIBackupProof"), "USI-13 backup/restore primitive", "database snapshot with selected owner/source coverage", "fresh contained temporary target only", "external system-backup retention applies only after ULO-08 integration", "receipt binds store snapshot, schema and contained artifact hash", "self-contained/WAL or snapshot mismatch fails closed and removes fresh target child", ("src/unified_source_index_backup.py:backup_sqlite_store/restore_sqlite_backup",)),
    ],
    "effect_flags": EFFECT_FLAGS,
}


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key: {key}")
        value[key] = item
    return value


def canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def load_inventory(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes().decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid lifecycle owner inventory: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("lifecycle owner inventory root must be an object")
    return value


def validate_inventory(value: dict[str, Any]) -> None:
    if value != EXPECTED_INVENTORY:
        raise ValueError("lifecycle owner inventory differs from the exact ULO-00 recovery contract")


def _default_inventory_path() -> Path:
    return Path(__file__).resolve().parents[1] / "docs" / "plans" / INVENTORY_NAME


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="require exact canonical committed bytes")
    parser.add_argument("--path", type=Path, default=_default_inventory_path())
    args = parser.parse_args(argv)
    if args.path.name in OBSOLETE_NAMES:
        raise SystemExit("obsolete lifecycle inventory filename is not an accepted ULO-00 target")
    try:
        actual = load_inventory(args.path)
        validate_inventory(actual)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    rendered = canonical_json(EXPECTED_INVENTORY)
    if args.check and args.path.read_bytes() != rendered.encode("utf-8"):
        raise SystemExit("lifecycle owner inventory is semantically valid but not byte-canonical")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
