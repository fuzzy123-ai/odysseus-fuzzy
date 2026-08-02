# Native Knowledge / Personal Docs Boundary Contract

Status: `NMG-01 contract; migration and activation disabled`

## Current Rule

Personal Docs is the sole current truth, write owner and exact-read owner for
owner-scoped Personal Docs. Native Knowledge is not selected as a current
domain and cannot mirror, fall back to, import from, dual-write with, or
mixed-query with Personal Docs.

USI owns index identity and provenance only; it is not a domain truth or
writer. Native Knowledge exact reads remain blocked until `NMG-02`. `UDA-05`
is the sole future Native Knowledge adapter, and is conditional on `NMG-02`
and explicit activation-scope selection. Obsidian/ORCA remains excluded.

## Canonical Content-Free Manifest

<!-- NMG-01-CONTRACT:BEGIN -->
```json
{
  "content_policy": "identifiers_only_no_source_content_or_runtime_data",
  "contract_version": "odysseus.native_knowledge_personal_docs_boundary.v1",
  "current_personal_docs": {
    "exact_read_owner": "personal_docs_owner_reader",
    "truth_owner": "personal_docs_sole_current_truth",
    "write_owner": "personal_docs_sole_current_writer"
  },
  "future_native_knowledge": {
    "adapter": "UDA-05_sole_future_adapter_after_NMG-02_and_explicit_activation_scope",
    "exact_read": "blocked_until_NMG-02_exact_reader",
    "state": "not_selected_no_current_truth"
  },
  "legacy_plugin": {
    "disposition": "excluded",
    "identifiers": [
      "plugin.obsidian.memory",
      "plugin.obsidian.orca",
      "plugin.obsidian.raptor"
    ]
  },
  "migration_cutover": {
    "authorization": "separately_explicit_migration_and_live_gate_required",
    "default_state": "disabled_no_migration_selected",
    "required_evidence": "exact_source_scope_version_locator_owner_policy_parity_tombstone_delete_rollback_cutover_state"
  },
  "operation_gates": {
    "adapter_registration": "prohibited_until_NMG-02_and_UDA-05_dependency",
    "migration_cutover": "disabled_until_separately_explicit_migration_and_USI-LIVE-ACTIVATION",
    "native_knowledge_activation": "disabled_until_NMG-02_and_explicit_activation_scope",
    "productive_indexing": "disabled_until_USI-LIVE-ACTIVATION",
    "productive_source_access": "prohibited_by_this_contract"
  },
  "prohibitions": [
    "automatic_import",
    "dual_write",
    "mirror",
    "mixed_query_peer",
    "native_knowledge_fallback",
    "parallel_truth"
  ],
  "usi": {
    "domain_truth": "prohibited",
    "role": "index_identity_and_provenance_only",
    "writer": "prohibited"
  }
}
```
<!-- NMG-01-CONTRACT:END -->

## Cutover Boundary

This contract does not authorize migration, import, backfill, deletion, rename,
adapter registration, productive indexing, productive source access or runtime
activation. A later migration must be separately explicit, scope-bound and
reversible; it cannot create parallel truth, dual writes, or mixed-result
semantics.
