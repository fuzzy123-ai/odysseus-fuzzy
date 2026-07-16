# Tool Taxonomy Dormant Live Activation Packet

Status: repository-ready, live activation gated

Gate ID: `TAX-LIVE-ACTIVATION`

This packet materializes the dormant contract only after TAX0-TAX12 repository
acceptance. It does not authorize activation, deployment, restart, provider
access, external writes, destructive actions, analytics capture, or backfill.

## Version And Environment Template

- Catalog feature: `tool-catalog-v2`
- Descriptor contract: `odysseus.tool_descriptor.v2`
- Rollout acceptance: `odysseus.tool_catalog_rollout_acceptance.v1`
- Default: `off`
- Release version: `<release-version>`
- Target environment: `<environment>`
- Feature-source owner: `<owner>`
- Previous projection version: `<previous-projection-version>`
- Evidence destination: `<redacted-evidence-destination>`

The template contains no credentials or real infrastructure target. Those
values require a separate, action-specific live authorization.

## Synthetic Repository Evidence

The verification harness performs an in-memory `off -> on -> off` sequence. It
dual-reads the legacy-compatible security projection and Catalog v2, proves an
exact rollback to the first projection, and fingerprints synthetic settings
before and after. The same run checks the settings alias ledger and the
analytics alias and reservation ledgers for loss.

The dual-read has one documented intentional difference: the established
runtime security projection strengthens `owner` to `admin` for
`cancel_download`, `download_model`, `manage_embeddings`,
`manage_github_issues`, `manage_personal_docs`, `manage_presets`,
`manage_repos`, `recent_changes`, `serve_model`, `serve_preset`, and
`stop_served_model`. Effect, confirmation, lifecycle, availability, enabled
state, and runtime availability remain identical. No security field is
weakened; any other drift fails acceptance.

Machine-readable evidence is emitted by:

```powershell
venv\Scripts\python.exe scripts\verify_tool_catalog_rollout.py --mode synthetic --assert-default-off --assert-rollback
```

Required acceptance fields are `status=passed`,
`materialization_ready=true`, and `activation_authorized=false`.

## Pre-Activation Checks

Before any later live GO, the operator must record the exact release version,
environment, previous projection version, feature-source owner, evidence
destination, and rollback owner. Then run:

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_tool_catalog_rollout.py
venv\Scripts\python.exe scripts\verify_tool_catalog_rollout.py --mode synthetic --assert-default-off --assert-rollback
venv\Scripts\python.exe -m pytest -q tests\test_tool_catalog_acceptance.py tests\test_tool_catalog.py tests\test_tool_registry.py tests\test_tool_index_schema_parity.py tests\test_runtime_tool_status.py tests\test_effectful_tool_matrix.py tests\test_tool_policy.py
```

Acceptance requires zero catalog, alias, analytics-ID, parser, handler,
permission, effect, or confirmation drift. Catalog v2 must still be off before
the authorized change.

## Monitoring And Budgets

Only aggregate, redacted operational measurements are permitted:

- projection build latency and serialized projection size;
- catalog projection error count;
- unknown technical ID count;
- parser, handler, permission, effect, and confirmation mismatch counts;
- rollback selection result and aggregate disabled-tool count.

Synthetic budget: 25 deterministic projections in at most 2,500 ms, each
below 256,000 bytes, with zero rollout errors. Do not record raw arguments,
results, prompts, document content, private paths, provider payloads, chat
identifiers, credentials, or secret values.

## Abort Criteria

Abort or immediately roll back if any of the following occurs:

- an unknown or colliding tool, alias, or analytics ID;
- parser, schema, handler, index, API, Admin, or analytics divergence;
- a weaker permission, effect, confirmation, owner, or session boundary;
- any deferred tool becoming enabled;
- any projection error or a breached latency or size budget;
- missing redaction, monitoring, rollback owner, or previous version;
- any environment or release value differing from the authorized GO.

No partial activation is acceptable after an abort criterion is observed.

## Rollback

1. Select the recorded previous catalog read projection.
2. Keep migrated settings, alias history, and analytics-ID reservations intact.
3. Do not delete or rewrite operator settings and do not reactivate deferred
   tools.
4. Re-run the synthetic acceptance command and the focused Catalog acceptance
   matrix.
5. Confirm aggregate projection errors return to zero and all deferred tools
   remain disabled.
6. Persist only the release version, environment label, aggregate checks,
   abort reason code, and rollback result.

Rollback changes the catalog read projection only. It grants no tool action or
provider permission.

## Final Deferred Tools

These 14 built-ins remain disabled through activation and rollback:

- `archive_email`
- `bulk_email`
- `delete_email`
- `list_email_accounts`
- `list_emails`
- `manage_assistant`
- `manage_calendar`
- `manage_contact`
- `manage_presets`
- `mark_email_read`
- `read_email`
- `reply_to_email`
- `resolve_contact`
- `send_email`

Email, calendar, and contact provider actions remain separately gated.

## Dormant GO Contract

The only later activation phrase is:

```text
GO TAX-LIVE: Aktiviere Tool Taxonomy/Registry <Version> in <Umgebung>;
E-Mail, Kalender und Kontakte bleiben deaktiviert; Rollback erfolgt über
<Feature-Schalter/Version>.
```

Until an operator supplies that exact, fully instantiated authorization,
Catalog v2 remains off and no live step may run.
