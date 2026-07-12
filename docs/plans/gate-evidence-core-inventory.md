# Gate Evidence Core Inventory

Status: GEC1 docs-only inventory

Scope: local vocabulary inventory only. No live checks, provider calls, raw
payload captures, tokens, chat IDs, host paths or private content are recorded.

## Compatibility Rule

- Existing route and module payloads stay stable.
- Canonical Gate Evidence Core fields must be additive only until each consumer
  is migrated and compatibility-tested.
- Canonical fields should normalize meaning, not rename or delete legacy keys.
- Any payload that needs raw content, tokens, chat IDs, absolute host paths or
  provider output is No-Go for GEC adoption.

## Consumer Families

| Family | Local status values | Evidence fields | Next-action fields | Live-Go / operator signals | Redaction risks |
| --- | --- | --- | --- | --- | --- |
| Release | Gate: `pass`, `warn`, `pending`, `blocked`, `not_applicable`; snapshot: `go`, `go_with_warnings`, `manual_pending`, `pending`, `pending_with_warnings`, `blocked`; pipeline baseline: `baseline_evidence_green` | `evidence_refs`, `blocking_gate_ids`, `pending_manual_gate_ids`, `warning_gate_ids`, `automated_ok`, `manual_ok` | mostly implicit via pending/blocking gate IDs | `external_release_go`, `required_for_external_release`, manual gate distinction | Evidence refs must stay symbolic; do not embed manual logs, provider proof output, install paths or release operator notes. |
| Review Gates | Route status: `pending`, `blocked`, `clear`; gate state: `no_pending`, `pending_review`, `ready_to_write`, `ready_to_execute`, `blocked`, `done`; source statuses include `review`, `ready`, `blocked`, `exported`, `sent`, `failed`, `unsupported` | `reason`, `source_ref`, safe `metadata` counts/booleans/tokens, `pending_count`, `blocked_count`, `gate_count` | `approval_command` | `review_required`, explicit export/live gate wording, write flags such as `writes_performed` | Must keep `raw_content_visible`, `path_values_visible`, `chat_id_value_visible`, `token_value_visible` false; source refs must remain hashes, not message IDs or chat handles. |
| Live Affordances | Overall/action: `ready`, `partial`, `blocked`; gate: `go`, `blocked` | `readiness_gap_names`, per-gate `summary`, `blocked_live_actions`; redaction booleans for live/network/send/write/process visibility | gap names imply next setup step; no dedicated next-action key | `manual_review_required`, `live_go_required`, operator-live-go env gates, blocked live action list | Never expose env values. Token/chat/path/raw-content flags must stay false, and tool discovery must not become process execution. |
| Live Integration / Dashboard | Gate: `go`, `blocked`, `needs_manual_evidence`, `deferred`; decision: `integration_readiness_ready`, `needs_manual_evidence`, `blocked`, `deferred`; dashboard: `ready_for_operator_review`, `needs_manual_evidence`, `blocked`, `deferred` | per-gate `summary`, `next_allowed_actions`, dashboard `tiles`, `blocked_live_actions` | `decision.next_action`, `next_actions` | `external_release_ready` stays false, `operator_review_required`, explicit disabled runtime/network/plugin gates | Unsafe if runtime, network, plugin import, host access, token presence, auto-approval or unsafe evidence logging is claimed/enabled. |
| Version One / MVP | Version: `ready`, `mvp_roadmaps_incomplete`, `backend_contracts_incomplete`, `ui_live_required`, `blocked`; MVP: `complete`, `incomplete`; legacy chat: `backend_ready`, `blocked`; UI: `live`, `required` | `version_1_0_ready`, `mvp`, `legacy_chat`, `ui`, `blocking_reasons`, percent/count summaries | `next_human_decision` | `external_release_allowed`, `tag_allowed`, `deploy_allowed`, `ui_live` gate | Roadmap evidence keys may be counted, but raw roadmap/provider/private values and host paths must not be mirrored. |
| Plugin | `ok` booleans with `registry_ok`, `local_plugins_ok`; issues separated into `errors`, `warnings` | registry/local plugin counts, policy issue codes, local audit codes | no dedicated field; errors/warnings drive action | offline release gate only; no install/import/download | Plugin IDs and policy codes are safe; avoid raw manifests if they contain private URLs, tokens, local paths or provider output. |
| Quality / Runtime | Quality statuses: `pending`, `pass`, `warn`, `block`, `fail`, `skip`; gate types: `tests`, `git`, `evidence`, `scope`, `hot_file`, `handoff`, `manual`; runtime snapshots: `passed`, exit codes, clean/dirty git flags | `evidence`, `verified_at`, `verified_by`, `block_reason`, `blocking_gate_ids`, `warning_gate_ids`, git/test snapshots, command output summaries | `next_action` | handoff status `done` gates verified-done; hot-file and scope gates block dispatch | Repo-relative paths only, bounded command summaries only. Do not store full command output, absolute paths, shell metacharacters, secrets or unscoped file lists. |
| Orchestration | Runtime readiness: `ready`, `dry_run_only`, `requires_operator`, `blocked`; activation index: `ready`, `review_required`, `blocked`, `deferred`, `not_started` | `capabilities`, `gaps`, `open_gap_count`, per-item summaries/detail counts, blocked runtime capability sections | `next_safe_action`, operator next steps sections | `live_hook`, `requires_operator`, blocked runtime capabilities, operator approval/deferred/cancelled audit event signals | Thread sends, git/test runners and scheduler hooks must stay dry-run/operator-gated unless an explicit later gate approves live execution. |
| System / Security | System ops: `pass`, `warn`, `fail`, `unknown` with overall `go`, `warn`, `no_go`; plugin readiness: `ready_for_manual_review`, `review_required`, `blocked`, `deferred`; security mode: `normal`, `secure`; provider scope: `default`, `local_only`; DSGVO route: `active`, `inactive` | ops `items`, readiness dimensions/scores, `blocker_count`, `block_reason`, policy booleans such as `local_only_required` | `next_action`; security decisions use `continue_existing_chat`, `start_new_chat`, `choose_local_provider` | `runtime_ready` false until manual review, `external_io_allowed`, immutable security state, local-only provider requirement | Security routes must expose policy metadata only. Do not persist raw settings values, provider URLs, tokens, chat IDs, private content or incident details in canonical evidence. |

## Canonical Field Candidates

- Identity: `family`, `gate_id`, `legacy_id`, `subject_ref`, `source_ref`.
- Status: `canonical_status` with local `legacy_status` preserved.
- Evidence: `evidence_refs`, `evidence_summary`, `evidence_count`,
  `verified_at`, `verified_by`, `redaction_flags`.
- Decision: `next_action`, `next_safe_action`, `next_human_decision`,
  `operator_decision_required`, `manual_review_required`.
- Live boundary: `live_go_required`, `live_hook`, `blocked_live_actions`,
  `external_release_go`, `external_release_ready`.
- Risk: `block_reason`, `warning_ids`, `blocking_ids`, `redaction_risk`.

## Migration Notes

- Map local terminal states to canonical `go`, `partial`, `deferred`,
  `blocked`, `no_go` without removing local values.
- Keep `blocked` for remediable blockers; use `no_go` only for unsafe evidence,
  raw private data, token/chat/path exposure or unauthorized live action.
- Prefer symbolic evidence refs and sanitized summaries over raw outputs.
- Canonical adapters should assert redaction flags before exposing aggregate
  "what can safely happen now" decisions.
