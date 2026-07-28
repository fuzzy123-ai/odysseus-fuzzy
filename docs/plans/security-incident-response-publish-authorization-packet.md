# OPS-ALERT D0 publication authorization packet

Run: `ABC-SEC125-20260728-OPS-ALERT-D0PUBLISH-PACKET`

Status: `NOT_AUTHORIZED_FUTURE_GO_REQUESTABLE_AFTER_MANIFEST`

This packet authorizes nothing. No stage, commit, push, fetch, SSH, deploy,
send, backup observation, or live observation occurred.

## Bound Git facts

- Local branch: `dev`.
- Local HEAD: `4240aeb9ef34351a02a45736e4eb4b43f1e85177`.
- Locally known `refs/remotes/fuzzy/dev` without fetch:
  `4240aeb9ef34351a02a45736e4eb4b43f1e85177`.
- Staged paths: zero.
- Required destination: exactly `fuzzy/dev`; `origin` is forbidden.
- Current 73-path pre-packet inventory content fingerprint:
  `sha256:092b4c0b52883f1611397eb86dfc1d774469f9509a3795edf9d0a7afe79af756`.
- Current tracked-candidate binary-diff fingerprint:
  `sha256:c3c2d4dcc7503f9484e3e1ea50483885a3ef7a59957d4dff81853cce14080110`.

These are inventory fingerprints, not an executable staging digest. The final
candidate must be rebuilt from exact reviewed hunks and receive a new digest.

## Proposed paths

Every `untracked` row below has accepted SIRP/D0/D0A claim provenance and is
safe for whole-file staging only after its tracked dependencies are split and
the final combined candidate passes the required tests.

| Path | State | Provenance | Whole-file staging |
|---|---|---|---|
| `docs/plans/security-incident-response-activation-packet.md` | untracked | SIRP-11, D0 | conditional yes |
| `docs/plans/security-incident-response-live-observe-delivery-evidence.md` | untracked | D0 | conditional yes |
| `docs/plans/security-incident-response-production-completion-evidence.json` | untracked | SIRP shared evidence | conditional yes |
| `docs/plans/security-incident-response-production-completion-roadmap.json` | untracked | SIRP-00/shared evidence | conditional yes |
| `docs/plans/security-incident-response-production-test-matrix.md` | untracked | SIRP-10 | conditional yes |
| `ops/homeserver/redacted_predeploy_observation.py` | untracked | D0A | conditional yes |
| `plugins/telegram/security_incident_commands.py` | untracked | SIRP-05 | conditional yes |
| `src/security_action_authorization.py` | untracked | SIRP-05 | conditional yes |
| `src/security_crowdsec_contracts.py` | untracked | SIRP-07 | conditional yes |
| `src/security_evidence_broker.py` | untracked | SIRP-02 | conditional yes |
| `src/security_evidence_sources.py` | untracked | SIRP-02 | conditional yes |
| `src/security_executor_contracts.py` | untracked | SIRP-06 | conditional yes |
| `src/security_executor_kernel.py` | untracked | SIRP-06 | conditional yes |
| `src/security_executors/crowdsec.py` | untracked | SIRP-07 | conditional yes |
| `src/security_executors/session_invalidation.py` | untracked | SIRP-08 | conditional yes |
| `src/security_incident_audit.py` | untracked | SIRP-09 | conditional yes |
| `src/security_incident_commands.py` | untracked | SIRP-05 | conditional yes |
| `src/security_incident_delivery.py` | untracked | SIRP-05, OPS-ALERT B/C | conditional yes |
| `src/security_incident_explanations.py` | untracked | SIRP-03 | conditional yes |
| `src/security_incident_service.py` | untracked | SIRP-02 | conditional yes |
| `src/security_incident_store.py` | untracked | SIRP-01 | conditional yes |
| `src/security_incident_store_migrations.py` | untracked | SIRP-01 | conditional yes |
| `src/security_incident_telegram_transport.py` | untracked | OPS-ALERT B/C | conditional yes |
| `src/security_post_action_verification.py` | untracked | SIRP-09 | conditional yes |
| `src/security_rollback.py` | untracked | SIRP-09 | conditional yes |
| `src/security_session_contracts.py` | untracked | SIRP-08 | conditional yes |
| `tests/test_homeserver_redacted_predeploy_observation.py` | untracked | D0A | conditional yes |
| `tests/test_security_action_authorization.py` | untracked | SIRP-05 | conditional yes |
| `tests/test_security_crowdsec_executor.py` | untracked | SIRP-07 | conditional yes |
| `tests/test_security_evidence_broker.py` | untracked | SIRP-02 | conditional yes |
| `tests/test_security_evidence_sources.py` | untracked | SIRP-02 | conditional yes |
| `tests/test_security_executor_kernel.py` | untracked | SIRP-06 | conditional yes |
| `tests/test_security_incident_attack_matrix.py` | untracked | SIRP-10 | conditional yes |
| `tests/test_security_incident_commands.py` | untracked | SIRP-05 | conditional yes |
| `tests/test_security_incident_delivery.py` | untracked | SIRP-05, OPS-ALERT B/C | conditional yes |
| `tests/test_security_incident_explanations.py` | untracked | SIRP-03 | conditional yes |
| `tests/test_security_incident_production_flow.py` | untracked | SIRP-10 | conditional yes |
| `tests/test_security_incident_response_production_completion_roadmap.py` | untracked | SIRP shared evidence | conditional yes |
| `tests/test_security_incident_store.py` | untracked | SIRP-01 | conditional yes |
| `tests/test_security_incident_telegram_transport.py` | untracked | OPS-ALERT B/C | conditional yes |
| `tests/test_security_post_action_verification.py` | untracked | SIRP-09 | conditional yes |
| `tests/test_security_rollback.py` | untracked | SIRP-09 | conditional yes |
| `tests/test_security_session_invalidation.py` | untracked | SIRP-08 | conditional yes |
| `tests/test_telegram_incident_controls.py` | untracked | SIRP-05 | conditional yes |

The following tracked files contain accepted SIRP changes, but whole-file
staging is not safe from current evidence. Their exact SIRP hunks must be
reviewed and staged individually. The two shared queue authorities are
definitively mixed with unrelated roadmap work.

| Path | State | Provenance | Whole-file staging |
|---|---|---|---|
| `core/auth.py` | tracked modified | SIRP-05/SIRP-08 | no; reviewed hunks only |
| `docs/plans/multi-agent-execution-guidance.json` | tracked modified | shared multi-roadmap authority | no; regenerate SIRP-only hunks |
| `docs/plans/open-work-completion-master-roadmap.json` | tracked modified | shared multi-roadmap authority | no; regenerate SIRP-only hunks |
| `docs/plans/ops-security-console-live-runbook.md` | tracked modified | SIRP-11 | no; reviewed hunks only |
| `docs/runbooks/crowdsec-remediation.md` | tracked modified | SIRP-11 | no; reviewed hunks only |
| `docs/runbooks/security-incident-response.md` | tracked modified | SIRP-11 | no; reviewed hunks only |
| `docs/runbooks/telegram-security-incident.md` | tracked modified | SIRP-11 | no; reviewed hunks only |
| `mcp_servers/debug_server.py` | tracked modified | SIRP-04/SIRP-06 | no; reviewed hunks only |
| `plugins/telegram/control_service.py` | tracked modified | SIRP-05 | no; reviewed hunks only |
| `plugins/telegram/parsing.py` | tracked modified | SIRP-05 | no; reviewed hunks only |
| `plugins/telegram/plugin.py` | tracked modified | SIRP-05 | no; reviewed hunks only |
| `plugins/telegram/stores.py` | tracked modified | SIRP-05 | no; reviewed hunks only |
| `routes/auth_routes.py` | tracked modified | SIRP-05/SIRP-08 | no; reviewed hunks only |
| `routes/ops_console_routes.py` | tracked modified | SIRP-04 | no; reviewed hunks only |
| `routes/security_routes.py` | tracked modified | SIRP-04/OPS-ALERT C | no; reviewed hunks only |
| `src/mcp_server_tool_policy.py` | tracked modified | SIRP-06 | no; reviewed hunks only |
| `src/ops_console_snapshot.py` | tracked modified | SIRP-04 | no; reviewed hunks only |
| `src/ops_timeline_adapters.py` | tracked modified | SIRP-04/SIRP-09 | no; reviewed hunks only |
| `src/security_anomaly_classifier.py` | tracked modified | SIRP-03 | no; reviewed hunks only |
| `src/security_incident_notifications.py` | tracked modified | SIRP-05/OPS-ALERT B | no; reviewed hunks only |
| `src/security_remediation_actions.py` | tracked modified | SIRP-06 | no; reviewed hunks only |
| `src/security_response_policy.py` | tracked modified | SIRP-03/SIRP-06 | no; reviewed hunks only |
| `tests/test_mcp_debug_server.py` | tracked modified | SIRP-04/SIRP-06 | no; reviewed hunks only |
| `tests/test_mcp_server_tool_policy.py` | tracked modified | SIRP-06 | no; reviewed hunks only |
| `tests/test_ops_console_snapshot.py` | tracked modified | SIRP-04 | no; reviewed hunks only |
| `tests/test_ops_timeline_adapters.py` | tracked modified | SIRP-04 | no; reviewed hunks only |
| `tests/test_security_incident_notifications.py` | tracked modified | OPS-ALERT B | no; reviewed hunks only |
| `tests/test_security_routes.py` | tracked modified | SIRP-04/OPS-ALERT C | no; reviewed hunks only |
| `tests/test_telegram_plugin.py` | tracked modified | SIRP-05 | no; reviewed hunks only |

`app.py` reports a worktree stat change but has no normalized Git diff; it is
excluded. All transcription, STT, LLM, local-model, mockup, pytest-artifact,
and unrelated roadmap paths are excluded.

## Required safe split

1. Reconstruct each tracked path from `HEAD` plus only its accepted SIRP hunks.
2. Reconstruct the two shared JSON authorities from `HEAD` plus only the exact
   SIRP queue/gate records; do not whole-file stage their large mixed diffs.
3. Stage the reviewed hunks and conditional whole files by exact path, inspect
   `git diff --cached`, and prove the staged path set contains no excluded path.
4. Run the full accepted SIRP/OPS-ALERT focused and adjacent lanes against that
   staged candidate, then recompute a final staged-diff and content digest.
5. Only then replace this blocked packet with an executable single-use packet.

## Future executable packet fields

- Exact commit message:
  `feat(security): publish incident response and predeploy observation`
- Exact push: local `dev` to `fuzzy/dev`, never `origin`.
- Maximum: one stage transaction, one commit, one push, zero retries.
- Expiry: run end; total Git action timeout 300 seconds.
- Proposed future phrase:
  `GO ABC-SEC125 D0PUBLISH STAGE COMMIT PUSH FUZZY DEV ONCE EXPIRES RUN_END`
- Expected post-push readback: new local `HEAD` is the created commit;
  locally updated `refs/remotes/fuzzy/dev` equals that commit; branch remains
  `dev`; staged set is empty; all unrelated worktree paths remain untouched.
- Before push failure: stop and retain the reviewed staged set or local commit;
  do not reset, amend, retry, or absorb other work.
- After push uncertainty: record `unknown`, do not retry, force-push, rewrite,
  or revert automatically. Any remote verification or revert needs a separate
  packet.

The future phrase is currently ineffective because the exact reviewed staged
candidate and final digest do not exist. Authorization remains `not_go`.
Backup-readiness observation does not precede publication: publication is a
repository action. D0 live observation remains unavailable until the reviewed
revision is published; deployment remains blocked afterward while backup
readiness is false.

## Reconstruction stop handoff

Run `ABC-SEC126-20260728-OPS-ALERT-D0PUBLISH-RECONSTRUCTION` stopped at the
end-of-day boundary before broad hunk reconstruction. Both read-only Terra
reviews were interrupted before handoff; no partial attribution is accepted.
The temporary alternate index at
`C:/tmp/odysseus-sirp-publish-reconstruction/index` contains only the bound
HEAD baseline. Zero candidate paths were added. No patch or reconstruction
manifest was created.

The real index was not mutated; its before/after SHA-256 is
`16a1ca884573513fb2b3f3e15d146313b6b2d9f34718aa5fd95a6da49af07de0`.
The exact next frontier remains the two disjoint tracked-hunk reviews, followed
by Sol deep review, verification of the 44 untracked whole-file candidates,
alternate-tree tests, and only then a final staged-tree digest. The future Go
phrase remains ineffective and authorization remains `not_go`.

## Reconstruction R2 exact blocker

Run `ABC-SEC126-20260728-OPS-ALERT-D0PUBLISH-RECONSTRUCTION-R2`
completed both disjoint read-only reviews and Sol deep review. All 23 tracked
code/test paths are SIRP/OPS-ALERT attributable, all six documentation paths
were reviewed, and all 44 accepted untracked whole-file candidates are present.
No candidate path was added to the alternate index because the shared queue
authorities contain atomic SIRP-plus-foreign hunks.

The exact blockers are:

- `docs/plans/multi-agent-execution-guidance.json`
  `@@ -579,2 +581,2 @@`
  (`sha256:e52571bffb44050cdc8293275b6f20acd7a2afc322a3e3c14341bcc95f265b28`):
  one replacement removes TTD current-state guidance and inserts the SIRP
  frontier.
- `docs/plans/open-work-completion-master-roadmap.json`:
  `@@ -4,2 +4,2 @@`, `@@ -13 +13 @@`, `@@ -17,2 +17,2 @@`,
  `@@ -25,2 +25,2 @@`, `@@ -44 +44,3 @@`, `@@ -50,3 +52,4 @@`,
  `@@ -87,6 +90,19 @@`, and `@@ -12516 +13153,1086 @@`.
  These atomic global status, strategy, goal, registry, audit, frontier and
  central-array hunks combine accepted SIRP state with TRP, TTD, PMCP or other
  global-roadmap content.

Per the no-absorption rule, no deterministic patch or manifest was created.
Owner authority is required either to define an exact semantic split of these
hunks or to explicitly accept their mixed content. The real index was not
mutated. Authorization remains `not_go`, and the future stage/commit/push phrase
remains ineffective because no complete candidate or final digest exists.

## SEC127 semantic closure

`ABC-SEC127-20260728-D0PUBLISH-SEMANTIC-JSON-SPLIT` resolves the mixed-hunk
blocker by reconstructing the two shared JSON authorities from their exact HEAD
blobs with 45 unique key-addressed SIRP/OPS-ALERT operations. TTD, TRP, PMCP,
transcription/faster-whisper, LLM, mockup and unrelated global-roadmap changes
are excluded. The mixed working-tree authorities and real index remain
untouched.

The original payload remains exactly 73 paths: 29 tracked paths and 44
untracked whole-file candidates. For durable publication evidence, the packet
and semantic manifest join that payload before the transport patch is
generated, producing a 75-path patch scope. The generated patch artifact then
joins the durable candidate as path 76. The patch intentionally does not encode
itself; the final external handoff binds its digest and the final 76-path
candidate tree/diff digest.

The deterministic artifacts are:

- `docs/plans/security-incident-response-publish-semantic-manifest.json`
- `docs/plans/security-incident-response-publish-semantic.patch`

That 76-path candidate was superseded by the accepted SEC128 backup-snapshot
observation contract. Its former exact future phrase is now stale and
ineffective:

`GO ABC-SEC125 D0PUBLISH STAGE COMMIT PUSH FUZZY DEV ONCE EXPIRES RUN_END`

It grants nothing and must not be replayed.

## SEC128 expanded semantic closure

`ABC-SEC128-20260728-BACKUP-ROLLBACK-READINESS` adds exactly three reviewed
repo-only paths to the prior semantic payload:

- `docs/plans/security-incident-response-backup-snapshot-observation-packet.md`
- `ops/homeserver/redacted_backup_snapshot_observation.py`
- `tests/test_homeserver_redacted_backup_snapshot_observation.py`

The expanded payload is exactly 76 paths: 29 tracked paths and 47 untracked
whole-file candidates. The packet and semantic manifest join that payload
before the transport patch is generated, producing a 78-path patch scope. The
generated patch artifact then joins the durable candidate as path 79 and is
intentionally not encoded inside itself.

That 79-path candidate was superseded by the accepted SEC129 predeploy backup
creation contract. Its former exact future phrase is now stale and
ineffective:

`GO ABC-SEC128 D0PUBLISH EXPANDED STAGE COMMIT PUSH FUZZY DEV ONCE EXPIRES RUN_END`

It grants nothing and must not be replayed.

## SEC129 expanded semantic closure

`ABC-SEC129-20260728-PREDEPLOY-BACKUP-CREATION-CONTRACT` adds exactly three
reviewed repo-only paths to the SEC128-expanded semantic payload:

- `docs/plans/security-incident-response-backup-creation-packet.md`
- `ops/homeserver/redacted_predeploy_backup_creation.py`
- `tests/test_homeserver_redacted_predeploy_backup_creation.py`

The expanded payload is exactly 79 paths: 29 tracked paths and 50 untracked
whole-file candidates. The packet and semantic manifest join that payload
before the transport patch is generated, producing an 81-path patch scope.
The generated patch artifact then joins the durable candidate as path 82 and
is intentionally not encoded inside itself.

After final detached validation and final manifest/digest readback, only this
new exact future phrase may be requested:

`GO ABC-SEC129 D0PUBLISH EXPANDED STAGE COMMIT PUSH FUZZY DEV ONCE EXPIRES RUN_END`

It is not granted by this packet. It would authorize only the exact
path-scoped real-index stage, one commit, and one push to `fuzzy/dev` under the
packet's existing preflight and stop rules. It would not authorize SSH,
network access, the D0 or SEC128 observations, backup creation, restore,
restic check, deploy, send, or any other external or live action. The distinct
future backup phrase remains separately gated by publication plus valid D0 and
SEC128 evidence:

`GO ABC-SEC129 PREDEPLOY BACKUP CREATE ONCE <=1860S EXPIRES RUN_END`

That backup phrase is requestable only after its controller preconditions are
met and is not granted here.

## SEC130 unchanged-count expanded semantic closure

`ABC-SEC130-20260728-D0-BACKUP-EVIDENCE-COMPOSITION` updates exactly three
paths already present in the SEC129-expanded semantic payload:

- `docs/plans/security-incident-response-activation-packet.md`
- `ops/homeserver/redacted_predeploy_observation.py`
- `tests/test_homeserver_redacted_predeploy_observation.py`

The D0 wrapper now composes the accepted SEC128 backup-snapshot observation
in-process exactly once, independently validates its exact source-redacted
schema and canonical digest, and binds the validated snapshot ID, source
identity, age, freshness and source evidence digest into D0's canonical
evidence. Nine fixed base commands at at most one second each plus the one
SEC128 component at at most twenty seconds fit within the thirty-second D0
outer bound. Invalid, stale, unknown, malformed, mismatched, visible, timed
out or exceptional source evidence produces only fixed D0 `blocked` output,
without partial readiness fields, retry or a duplicate restic invocation.

SEC130 adds no path. The expanded payload therefore remains exactly 79 paths:
29 tracked paths and 50 untracked whole-file candidates. The packet and
semantic manifest still produce an 81-path patch scope, and the generated
transport patch still joins the durable candidate as path 82 without encoding
itself.

The SEC129 publication phrase is stale and ineffective after these content
changes:

`GO ABC-SEC129 D0PUBLISH EXPANDED STAGE COMMIT PUSH FUZZY DEV ONCE EXPIRES RUN_END`

It grants nothing and must not be replayed. After final detached validation
and final manifest/digest readback, only this new exact future phrase may be
requested:

`GO ABC-SEC130 D0PUBLISH EXPANDED STAGE COMMIT PUSH FUZZY DEV ONCE EXPIRES RUN_END`

It is not granted by this packet. It would authorize only the exact
path-scoped real-index stage, one commit and one push to `fuzzy/dev` under the
existing preflight and stop rules. D0, SEC128 observation, SEC129 backup
creation, restore, restic check, deploy, delivery, SSH and network access all
remain separate gates.

## SEC131 needs-live-observation expanded semantic closure

`ABC-SEC131-20260728-TRANSACTIONAL-DEPLOY-ROLLBACK` adds exactly three
reviewed repo-only paths:

- `docs/plans/security-incident-response-transactional-deploy-packet.md`
- `ops/homeserver/redacted_podman_compose_capability_observation.py`
- `tests/test_homeserver_redacted_podman_compose_capability_observation.py`

The incident-response runbook is also updated in place. SEC131 records a
deliberate `needs_live_observation` stop: repository sources cannot prove the
installed Podman Compose 1.3.0 service-only/no-dependency build, switch and
rollback behavior. The fixed observer reads only exact version/help commands
and a bounded AST-scoped projection of the installed public package source.
It emits no raw help, source, environment, path, exception, hostname or
secret. Help flags alone cannot produce `ok`, and any unrecognized source
shape remains `needs_live_observation`. No deploy executor was implemented.

The expanded payload is exactly 82 paths: 29 tracked paths and 53 untracked
whole-file candidates. The packet and semantic manifest join that payload to
produce an 84-path patch scope. The generated transport patch then joins the
durable candidate as path 85 without encoding itself.

An initial SEC131 readback rejected the candidate because its versioned
transport patch still represented the preceding SEC130 closure even though
the manifest advertised the expanded counts. The repaired candidate is
acceptable only when the on-disk transport patch is regenerated directly
from exact HEAD, contains all 84 closure paths including the three SEC131
additions above, and independently replays through a fresh alternate index
to the same 84-path closure tree. The patch then joins that replayed closure
as durable path 85. Manifest counts alone are never sufficient evidence.

The SEC130 publication phrase is stale and ineffective:

`GO ABC-SEC130 D0PUBLISH EXPANDED STAGE COMMIT PUSH FUZZY DEV ONCE EXPIRES RUN_END`

It grants nothing and must not be replayed. After final detached validation
and manifest/digest readback, only this new publication phrase may be
requested:

`GO ABC-SEC131 D0PUBLISH EXPANDED STAGE COMMIT PUSH FUZZY DEV ONCE EXPIRES RUN_END`

It is not granted here. Publication would not authorize the separately gated
capability observation, whose exact future phrase is:

`GO ABC-SEC131 PODMAN COMPOSE CAPABILITY READ-ONLY OBSERVATION ONCE <=15S EXPIRES RUN_END`

That observation phrase is also not granted here and is not effective until
the reviewed wrapper is published. Even an accepted `ok` observation creates
no deploy authority. Transactional executor implementation/review, fresh D0
and SEC129 evidence, exact owner-bound old/new revisions and snapshot
provenance, and a later action-specific deploy Go remain mandatory. Backup,
restore, restic check, cleanup, prune and delivery remain separate gates.
