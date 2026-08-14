# Odysseus Master Handoff

Snapshot: 2026-08-14, Europe/Berlin
Continuity deadline: 2026-08-16 00:00 Europe/Berlin
Canonical authority at snapshot: Open Work / Round 7 Revision 286
Verified progress: 173 accepted of 245 canonical units
Open units: 72 (19 repo, 19 design, 30 live, 4 operator)

This file is a redacted continuation snapshot for an agent that has no chat or
thread history. It is descriptive evidence, not mutation authority. If this
file conflicts with current structured state, use the authority order below,
record the discrepancy here, and follow the current structured authority.

## 1. Authority order

Use the newest applicable source in this order:

1. Latest user steering in the active task.
2. `AGENTS.md` safety and evidence rules.
3. `docs/plans/open-work-completion-master-roadmap.json` (Open Work).
4. `docs/plans/parallel-roadmap-execution-round7.json` (Round 7).
5. `docs/plans/parallel-roadmap-execution-round6.json` for normalized frontier
   rows and their dependency/blocker detail.
6. `docs/plans/multi-agent-execution-guidance.json` and repository validators.
7. Slice roadmaps, architecture packets, evidence receipts, commits, and this
   handoff.
8. Old prose, chat summaries, and historical receipts are context only.

Newer accepted/released records override stale earlier `planned`, `blocked`, or
`dependency_ready` prose. Never infer a writer claim from this document.

## 2. Non-negotiable operating rules

- Preserve all foreign work. The canonical checkout is intentionally dirty.
- Work only under one current, path-scoped, dependency-ready claim whose
  owner, lease, baselines, pins, hotfiles, commands, acceptance, and stop rule
  have been read back from durable authority.
- Central routers are Root/Sol-owned and must be serialized.
- Workers may not stage, commit, push, deploy, send, or mutate a host unless an
  exact action-specific authority grants that operation.
- Push only to `fuzzy/dev`; never push to `origin`.
- Do not publish terminal, rejected, superseded, or mixed candidates.
- Do not expose private paths, task/thread identifiers, credentials, provider
  responses, raw service environments, or private data.
- The private Debian configuration archive is read-only. Use it only to locate
  redacted repository-owned probes or deployment facts; never copy its secrets
  into logs, commits, evidence, or this file.
- For Debian readiness, use only the fixed redacted probe declared in
  `AGENTS.md`. Never print a complete environment or inspect raw container
  environment values.
- One repeated invariant failure after the slice's bounded correction budget
  is terminal for that candidate. Release it and require fresh architecture.
- An ordinary blocker is not an instruction to stop the run. Run an
  evidence-based `$reflect`, preserve the blocked candidate, and continue a
  disjoint runnable slice, architecture/recovery predecessor, verification,
  release, or documentation task. Do not repeat an identical failed mechanism.

## 3. Current workspace map

### Canonical checkout

- Purpose: complete structured authority, accepted product work, foreign work,
  and historical evidence.
- Branch at snapshot: `codex/implemenetaion`.
- HEAD before the next documentation-only handoff commit:
  `4a0619d2af8ad83e7dec770bdb9cef42a1f83eed`
  (`docs: finalize roadmap continuation handoff`), parent
  `3f84d422b1c645cbe6cff0042fee6645cec88d58`. Never use a handoff commit
  itself as product authority; use the exact accepted claim tuples instead.
- State before the next documentation-only handoff commit: 214 dirty paths
  (55 tracked-modified, 159 untracked), index empty.
- Planning subtree: 0 staged entries, 22 tracked-modified entries, and 126
  untracked entries. The central authorities
  are intentionally modified; the terminal R7-A2 packet and freeze Evidence
  are untracked but exactly hash-pinned by durable authority. Untrackedness
  alone does not invalidate a registered artifact.
- Rule: never clean, reset, broadly stage, or absorb this tree. Select exact
  accepted tuples only.

### Clean accepted release clone

- Purpose: reviewed, thematic accepted commits isolated from the canonical
  dirty tree.
- Default locator relative to the canonical checkout:
  `../Documents/odysseus-release-standalone` when the canonical checkout is in
  its usual workspace, or discover it read-only by matching branch `dev`, HEAD
  `4b59a975a24a9c69a02d216cf56e39e0c2fbcfec`, and a clean status among sibling
  `odysseus-*` directories. Never select a directory by name alone.
- Branch: `dev`.
- HEAD at snapshot: `4b59a975a24a9c69a02d216cf56e39e0c2fbcfec`.
- State at snapshot: clean.
- Latest commit: `fix(stt): align container lock with Python 3.11`.
- This clone contains private control-plane history and must not be pushed as a
  history chain to the public remote.

### Public-safe projection clone

- Purpose: one public-safe squash projection on the current public `dev` base.
- Default locator relative to the canonical checkout:
  `../Documents/odysseus-public-release` when the canonical checkout is in its
  usual workspace, or discover it read-only by matching branch
  `codex/public-release`, HEAD `ea181f52e72a24d91480757c55a298fbb77249b1`,
  parent `d691cebc0b850e13b7087ab3866d7902940ead67`, and a clean status among sibling
  `odysseus-*` directories. Never select a directory by name alone.
- Branch: `codex/public-release`.
- Public base at snapshot: `d691cebc0b850e13b7087ab3866d7902940ead67`.
- Public release commit: `ea181f52e72a24d91480757c55a298fbb77249b1`
  (`feat: publish accepted runtime features`). It is exactly one commit above
  the public base and its only parent is that base.
- State at snapshot: clean index and worktree. The committed projection has
  exactly 161 paths and numstat `+49354/-536`.
- Binary base-to-commit diff SHA-256 at the final independent privacy audit:
  `2a4078f51c7ca1334103c96871c08f895047d6f1db19d440a1471cf2e039d7c9`.
- Changed retained tests: 73 files.
- The projection is committed locally but not yet pushed, deployed, or
  live-validated.
- It intentionally excludes private ABC/control-plane roadmaps, evidence,
  rejected candidates, private paths/identifiers, ops secrets, and static UI
  mockups.

The public projection includes accepted runtime, coding, temporal, Telegram,
transcription, native knowledge, USI, and audit changes. It includes the Python
3.11 STT container/lock alignment and its tests.

If either release checkout cannot be uniquely found with the exact identity
above, stop release mutation but continue other repo-only work. Do not rebuild
it from the canonical dirty tree. A replacement public projection must start
from a fresh readback of `fuzzy/dev` and re-materialize only an independently
reviewed public-safe manifest.

## 4. Current release evidence

Completed on the public-safe projection:

- Five product inventory/contract audits pass.
- Gemma offline acceptance passes (5 tests).
- Python `compileall` passes.
- JavaScript syntax checks pass for retained static JavaScript.
- All three Compose configurations parse successfully.
- `git diff --cached --check` passes.
- Independent staged-diff privacy review passes: no new real username, host or
  Codex path, task/thread identifier, credential, private data, rejected
  product record, unsafe default, symlink, submodule, or binary payload.
- STT focused test passes; full STT adapter suite passes (22 tests); app wiring
  passes (30 tests); a CPython 3.11 Linux wheel-resolution dry run succeeds for
  the pinned STT lock.
- The exact final staged Python test-file lane passes with `1671 passed, 4
  skipped, 1 warning, 17 subtests passed` in 103.57 seconds.
- The final deterministic STT lease-heartbeat regression passes 20/20 repeated
  runs and the complete transcription-pipeline file passes 13/13.
- All staged Python files compile in memory. The final confinement/temp-root
  corridor passes `60 passed, 2 skipped` after closing the Windows system-temp
  root widening case.
- Privacy fixtures no longer contain the real local Windows username; the
  final exact 161-path staged tuple passed an independent privacy QA scan.
- Independent final Deep-Sol release review passes on the exact committed
  digest: scope, self-containment, privacy, default-off behavior, parsability,
  dependency closure, and rejected/live-path exclusions are all green.
- The latest full Windows repository run, on the immediately preceding
  near-final tuple, produced `12738 passed, 34 skipped, 12 failed, 82 warnings,
  17 subtests passed`. Five failures were unchanged Windows symlink-privilege
  cases, two were unchanged native-Windows AMD/Bash harness cases, four
  encoding assumptions and one temp-root defect were fixed afterward. The
  seven unchanged Windows-only cases pass `7 passed` in a Linux temporary
  environment. Do not rewrite this as an uninterrupted all-green final Windows
  full-suite result.

Historical clean-release evidence only (do not attribute it to a changed
projection): 12,827 tests passed, 149 skipped, 17 subtests passed on the earlier
`94b2eb27` release state.

Completed before the local public commit:

1. The STT projection contains only the newer accepted TRP-03..06 lineage
   and the reviewed Python 3.11 container/lock repair. The older 2026-07-27
   OWM-24 deferral sentence is superseded by the structured 2026-08-02 TRP-05
   and TRP-06 acceptance records. Exclude every TRP-07-only backup/deploy/live
   packet path because TRP-07 is terminally unaccepted.
2. The green exact 73-file changed-test lane and the final frozen
   staged digest. A fresh full-suite rerun is optional strengthening, not a
   substitute for the recorded Windows/Linux platform classification.
3. Compile/static/product audits, all three central validators, staged-diff
   privacy scan, and `git diff --cached --check` passed on the frozen tuple.
4. The local commit has exactly 161 intended paths, no unstaged or staged diff,
   one parent, and no private control-plane history.

Still required externally:

5. Push exact commit `ea181f52e72a24d91480757c55a298fbb77249b1`
   only to `fuzzy/dev`, then require exact terminal CI.

Do not call the release public or live before step 5 has both pushed the exact
commit and produced a terminal Required CI result bound to that SHA.

## 5. Publication and Debian deployment route

The repository-derived canonical route is:

1. Create the single reviewed public-safe commit on the public `dev` base.
2. Push only that exact commit to `fuzzy/dev`.
3. Bind the automatic `CI / Required quality gate` result to that exact SHA.
   `.github/workflows/ci.yml` runs on pushes to `dev`. The OCI publication
   workflow is manual/optional and explicitly not the Debian production image
   source; do not invent an image-publication prerequisite for deployment.
4. Run the fixed redacted Debian readiness probe from `AGENTS.md`.
5. Before deployment, obtain a fresh backup snapshot, run repository-owned
   restic check/restore-smoke evidence, and retain the snapshot identifier only
   in the bounded deployment receipt.
6. Build a transactional app-only deployment packet with old SHA, new SHA,
   manifest digest, resource limits, `delivery_disabled=true`, health checks,
   and rollback target.
7. Deploy through the repository's rootless Podman/systemd route to the
   canonical application checkout under `/opt/odysseus`.
8. Read back redacted health and exact revision. Delivery, remediation,
   provider writes, and other live effects remain separate actions.

Rollback rule: one automatic rollback to the named old revision after a
post-switch failure. If state is unknown, stop and do not retry blindly.

Current external blockers:

- Local Docker Desktop engine is unavailable, so local OCI builds cannot be
  claimed green. Use exact CI or an authorized Debian build route.
- The latest redacted backup diagnostic reports backup credential readiness is
  incomplete. A one-time credential migration/repair and fresh redacted
  readback are required before a deployment snapshot. Never expose the
  credential or repair it through an unredacted command.
- The exact `git push fuzzy HEAD:dev` for `ea181f52...` was rejected by platform
  action review because it requires direct end-user confirmation of this exact
  payload and destination. No workaround or alternate remote is permitted.
- The repository-owned redacted Debian probe failed before connection because
  the canonical configured SSH identity file is absent. Private archived
  configuration may be used only to restore the approved access route through
  an explicit secret-safe migration; do not print, copy ad hoc, hash, or
  substitute private key material. No server read or write occurred.
- Push and deployment remain subject to platform action review even though the
  product goal is authorized. Preserve the exact payload and continue
  repo-only documentation or verification when an external action is rejected.

## 6. Global roadmap state

- Open Work and Round 7 are at authority Revision 286.
- The canonical denominator is verified and published: 173 accepted of 245
  unique units, leaving 72 open (19 repo, 19 design, 30 live, 4 operator).
- The published snapshot binds all 30 active source-roadmap blobs, the exact
  alias/exclusion rules, the current authority tuple, and the independently
  reviewed deterministic candidate. Do not recompute or replace the
  denominator from stale prose; re-open denominator work only if a current
  source or authority byte changes and the routers explicitly invalidate it.
- No central repo claim or lease is active. `ABC-R7A2-R3R1-REV280` was
  terminally released blocked at Revision 283 after correction 1/1. Freeze
  Evidence remains `docs/plans/evidence/abc-r7a2-r3r1-rev282-freeze.json`
  blob `a86820f61d9a952a42f6082c382287cd11b212ad` and binds the exact four
  rejected candidate blobs plus set/command/node/diff digests. Exact14 and the
  475-test dependency corridor passed, but independent QA reproduced five
  malformed A1/A2T/reviewer resolver returns classified as `invalid_type`
  instead of `stale_authority`. Independent Deep-Sol additionally reproduced
  a fully rehashed semantic ledger forgery, ambient exception-context leakage,
  and incorrect budget precedence. The failure class is exhausted; never patch
  this candidate again or open R7-A3 against it.
- One packet-local, router-disjoint source-normalization claim is accepted and
  released: `ABC-HWA-SOURCE-NORM-REV283`. It is recorded in
  `docs/plans/abc-hwa-source-reconciliation-normalization-architecture.json`,
  final blob `c0075c15f86a53642ebccd8cd00e4734a126958d`, semantic digest
  `sha256:ccf05a15010df328d10f18a141d177239b82001704c211c69cad6e02d7cf3e23`.
  Accepted candidate blobs are HWA `77318983c9dcc5b124b1c1ac9408f27ce08c09d1`
  and Temporal `441d2f335b1aebd1876b015846e39057bd5bc89b`.
  JSON, Guidance, `diff --check`, isolated planning-boundary pytest (10/10),
  Root review, and independent Sol review pass. It grants no router, Evidence,
  product, stage, commit, push, deploy, provider, host, UI, or live authority.
  Root/Sol persisted its accepted release mirror at Revision 284 and later
  consumed it as one input to the completed Rev286 denominator transaction.
- `ABC-RMT8-V2-REV278` remains
  `released_accepted` at authority Revision 279 on frozen source/test blobs
  `df90fa7203d1a0c94b23ee194647cd5ca1779be7` and
  `3e013ec247b335b4debb5f9ba20e9fd6d6f773b1`, Evidence blob
  `935ee9486fa85dc20684fd6074b6cc06998322ed`, and correction 1/1. Exact13,
  the full persisted dependency corridor, static checks, all three central
  validators, independent hostile QA, and Deep-Sol passed on the unchanged
  frozen tuple.
- Revision 286 post-publication raw `git hash-object --no-filters` readback:
  Open Work `8cff54b199c2005fd5cb45394400ed3977168950`, Round 6
  `696af4ae82f33582ae4dd68a3edc9c93b1367296`, Round 7
  `eb4c865fe725e81b8b4c120597da205d4ed11c88`, Guidance
  `7d4b43dc7f91c1f9cfc9837d3b7bb425f50fc3f7`, R7-A2 packet
  `c4d2974f7cef38c636c960b5e641a26202cf4345`, and freeze Evidence
  `a86820f61d9a952a42f6082c382287cd11b212ad`. Do not compare authority
  pins with filtered `git hash-object` output: `text:auto` normalizes Round 6
  CRLF bytes to a different LF-only blob even though the parsed JSON is equal.
- A filename or untracked Evidence receipt that contains a larger revision
  number does not advance authority. The exact RMT8-v2 packet/evidence pair is
  now authoritative only through the durable Rev279 terminal claim; other
  higher-numbered untracked receipts remain descriptive until their own valid
  Root-owned router transaction and readback.
- The broad discovery audit currently reports `safe_open_count=1`, but its sole
  member is `/target` inside the unregistered
  `abc-lcl01-observation-hypothesis-contract-v5-architecture.json` artifact.
  It is not a durable roadmap slice or writer claim. The normalized Round 6
  frontier remains 0/30 claimable. Therefore no existing row is directly
  dispatchable without a valid registration, architecture/recovery predecessor,
  live action, or dependency transition.
- The last accepted overlay additions include planning-source memory inventory,
  big-data ledger contract, TWP-01 repair, ACPR-13D, FCA-03A v3, NMG-02A,
  UDA-05 v4, NMG-03 native-adapter acceptance, and RMT8-v2 effective-input
  state-matrix recovery.
- Independent context-free continuity review: PASS after correcting checkout
  discovery, the newer TRP-05/TRP-06 acceptance precedence, TAX0/TAX1/TAX7
  status, and the terminal TRP-07 stop rule. Independent privacy review of this
  handoff also passes.
- Fresh native-Python readback on the current Revision 286 bytes passes all
  three central validators: Integration Spine `ok:true`, Round 6 `valid:true`
  with 30/30 rows and zero claimable frontiers, and Guidance `valid:true` with
  139/139 inventory entries. The exact dependency corridor also ran through
  the Windows venv with exit 0. Direct Windows launch from the sandboxed WSL
  path can still hit a local Vsock bridge error; this is an interpreter-launch
  limitation, not a product or validator failure.
- Heartbeat automation `odysseus-open-work-continuous-abc-run` is ACTIVE every
  minute until `2026-08-16T00:00:00+02:00`. It names the canonical checkout and
  the public-safe projection clone identified by the exact branch/HEAD/base/
  clean-status tuple in section 3, requires one bounded
  progress unit or an evidence-backed no-op, invokes `$reflect` on ordinary
  blockers, forbids identical retries, and keeps external-action approval
  blockers from stopping disjoint safe work.

## 7. Open roadmap lanes

The table gives a context-free safe continuation. Exact gates and `done_when`
remain in Open Work. Re-read the current row before action because this table is
a snapshot.

| Lane | Snapshot status | Safe next action |
|---|---|---|
| OWM-1 Version 1.0 | UI/release decision open | Do not claim 1.0 until the new UI is live, `/api/version-one/readiness` is Go, the rollback target is named, and an exact release packet is approved. |
| OWM-2 Calendar/reminders | Operator-deferred | Preserve repo readiness. Do not schedule, send, or ask for configuration until explicitly reprioritized. |
| OWM-3 Autonomous coding | Runtime/config blocked | Keep dry-run. Establish a bounded runner/Podman and dispatch configuration, then execute only an exact sandboxed job packet. |
| OWM-3A GUI tooling | Runtime/UI handoff blocked | Keep observations read-only. UI owner must place tools; screenshot/network/Telegram actions remain separately gated. |
| OWM-4 MCP Workbench | Service availability needed | Configure and classify available services in safe order; keep local MCP disabled/read-only until bounded smoke evidence exists. |
| OWM-5 Nextcloud pilot | Config and subset missing | Select a bounded subset and policy, then local-only extract/review. No Memory/Raptor write without a separate decision. |
| OWM-6 Debian observability | Live-gated | After exact action authority, collect redacted host/Podman metrics and logs, prove retention/exposure, and avoid remediation. |
| OWM-7 SIRP | Offline core accepted; live completion open | Publish the accepted fixed redacted D0A wrapper first. Then perform only the separately authorized one-invocation observe/delivery packet. CrowdSec, session invalidation, deploy, remediation, and temporal closure remain separate gates. |
| OWM-8 UI handoff | Design-owned | UI agent wires existing contracts and proves UI readiness; backend ABC does not edit legacy/v2 UI hotfiles. |
| OWM-9 Memory scale | Historical/nonclaimable | Keep USI SQLite authoritative. Collect native scale evidence; create a new migration roadmap only if measured evidence justifies it. |
| OWM-10 GitHub intelligence | Post-MVP live-gated | Keep local/read-only. A bounded repo sync may be approved later; writes remain separately previewed and confirmed. |
| OWM-11 Tool taxonomy | TAX0/TAX1 complete; later queue/acceptance remains | Do not repeat TAX0 or TAX1. Re-audit the first later unaccepted TAX dependency. TAX7 is already repo-implemented with automated gates green and waits on user/UI acceptance, not another repo writer. Keep live activation off. |
| OWM-12 Tool analytics | Waits on TAX1 and shared TAX milestones | After TAX1, build the disjoint content-free invocation/store baseline. No production capture or backfill until final activation. |
| OWM-13 Gemma isolation | Repo packet accepted; revalidation required | Revalidate against native UIR-13 observability. Historical GRO activation is not authority; keep Gemma maintenance lane disabled until its exact final packet. |
| OWM-14 GraphRAG/RAPTOR observability | Historical plugin evidence | Do not activate legacy GRO. Route reusable native evidence through NMG-04A/04B, UIR-13, and USI-15. |
| OWM-15 USI foundation | USI-00..14 accepted; selected native chain advanced | Treat UDA-05 v4 and NMG-03 verification as accepted at Revision 277. Re-audit the next native frontier; USI-15 remains blocked by its declared lifecycle/runtime/scope dependencies. Keep Obsidian excluded and productive sources off. |
| OWM-16 Codebase Memory | CBM-08 accepted; CBM-09 blocked | Close the declared CBM-09 dependencies before a claim. Keep engine process, hooks, auto-config, productive projection, and direct upstream tools disabled. |
| OWM-17 Code lineage | CLT-00 accepted; CLT-01 contract next | Use synthetic Git fixtures and read-only history. Preserve first-observable/uncertainty semantics and defer real backfill. |
| OWM-18 Lens Code Graph | Waits on bounded graph API | Start LCG-00 only after the bounded CBM graph API and one UI-hotfile owner. Fixtures/backend first; no productive graph or placement. |
| OWM-19 USI runtime | UIR core advanced; runtime default-off | UIR-03A Go-A R2 is accepted despite stale blocked prose. Reconcile the exact next UIR-03 hotfile handoff, then follow UIR-12 -> NMG-07 -> UIR-12A. Do not resurrect rejected UIR-03A candidates. |
| OWM-20 Domain adapters | UDA-05 v4 and NMG-03 accepted; productive adapters off | Re-audit selected-scope adapter dependencies and continue only an exact fresh claim. Deferred communication domains and Obsidian remain excluded. |
| OWM-21 Lifecycle/operations | ULO-00/01 accepted; later chain open | Continue the existing ULO dependency chain with synthetic owners and temporary databases. Serialize Auth/backup/wipe hotfiles; every productive lifecycle effect has its own gate. |
| OWM-22 Telegram Todo truth | Through A5 accepted; A6 unselected | Preserve Notes as Todo truth. Do not use rejected/divergent candidates. A6 may be selected after fresh collision/authority readback; productive send/repair/rollover stay disabled. |
| OWM-23 Maintenance harness | Roadmap registered, goal not started | After explicit Open Work reprioritization, open only disjoint AMH-01..03 claims. Extend existing policy/gate/Planning authorities; no parallel state store. |
| OWM-24 Local transcription | TRP-03..06 accepted; TRP-07 terminal; live off | Newer structured records accept TRP-05 (2026-08-02, independent QA and Deep-Sol) and TRP-06 (`released_accepted`, commit `871ffe64`), overriding the older deferral sentence. Publish only that accepted/default-off lineage plus the reviewed Python 3.11 repair. TRP-07 remained unaccepted after fresh architecture plus two recovery corrections; no third recovery without a new architecture and claim, and no live audio/deploy claim without exact evidence. |
| OWM-25 Coding loop consolidation | Many CAO/NMG slices accepted | Do not blindly follow stale `ACPR-11 next` prose: current history already contains later ACPR-13D acceptance. Re-run the current frontier audit and claim only the first genuinely unaccepted disjoint predecessor. |
| OWM-26 Subagent context/delegation | Bounded local/fake contracts accepted | Continue only after current ACPR dependency readback. Real adapter activation and external dispatch remain separately gated. |
| OWM-27 Live contextual layer | LCL-00 accepted; LCL-01 future frontier | Fresh Root/Sol architecture and exact disjoint claim are required for LCL-01. Hypotheses remain advisory and cannot override Planning/domain/USI/claim authority. |
| OWM-28 Harbor integration | R7-A2 R3R1 terminally released blocked at Rev283 after correction 1/1; RMT8-v2 and the HWA/Temporal source normalization remain accepted; Rev286 denominator verified | Preserve the rejected four-blob tuple. Do not patch it or open A3. Reflect against the 72 published open units and select only a fresh disjoint repo-only architecture/recovery predecessor. |

`OWM-24` through `OWM-28` above are continuation overlays derived from later
structured acceptance and Round 7 state; the current Open Work
`completion_lanes` array contains only OWM-1 through OWM-23. The overlay rows
are descriptive and cannot themselves authorize a claim. A future agent must
use their cited newer structured records or register an explicit roadmap row.

### Rolling-inventory crosswalk (30/30)

This appendix maps every active Guidance source exactly once. Round 6 pointers
are zero-based JSON array indexes. A disposition is descriptive; only current
structured authority can open a writer claim.

| Source roadmap | Round 6 frontier / ID | Lane | Disposition | Safe next action |
|---|---|---|---|---|
| `agent-maintenance-safety-harness-roadmap.json` | `[0]` / `AMH-01-root-policy-and-runbook` | OWM-23 | `waiting_on_user` | Wait for explicit goal or Open Work reprioritization. |
| `autonomous-coding-production-readiness-roadmap.json` | `[1]` / `ACPR-12-transactional-worktree-and-safe-tool-capsules` | OWM-25 | `accepted_released_round6_a` | Preserve; do not reclaim. |
| `coding-agent-orchestration-consolidation-roadmap.md` | `[2]` / `CAO-08G-verification-repair-independent-review` | OWM-25 | `blocked_by_dependency_and_normalization` | Reconcile exact paths, hotfiles, and tests read-only. |
| `diagnostics-legacy-fix-roadmap.md` | `[3]` / `DLF1B-telegram-history-redaction-fix` | Diagnostics | `normalization_blocked` | Perform only its fresh read-only audit. |
| `headless-write-agent-orchestration-roadmap.md` | `[4]` / `HWA-2-canonical-subagent-commit-bridge` | OWM-3 support | `normalization_blocked_fresh_architecture_required` | `HWA2-...` is the non-counting historical spelling of this one unit. Any product recovery needs materially fresh architecture and must not revive HWA2A v2. |
| `system-assurance-runtime-hardening-roadmap.json` | `[5]` / `SAR-02-fresh-release-evidence` | Assurance gate | `blocked_environment` | Wait for the required symlink-capable environment. |
| `temporal-light-agent-execution-roadmap.json` | `[6]` / `TLR-10-12h-wall-clock-soak` | Temporal gate | `waiting_on_user` | Wait for exact workload, window, limits, and restart injection. |
| `anti-hallucination-evidence-roadmap.md` | `[7]` / `ANTI-HALLUCINATION-next-domain-audit` | Evidence audit | `normalization_blocked_readonly_audit_first` | Run only the required unsupported-domain audit. |
| `live-contextual-layer-roadmap.json` | `[8]` / `LCL-01-observation-and-hypothesis-contract` | OWM-27 | `waiting_on_explicit_implementation_goal` | Wait for explicit implementation goal and claim. |
| `gemma3-memory-ops-optimization-roadmap.md` | `[9]` / `GMI-LIVE-ACTIVATION` | OWM-13 | `waiting_on_user_live_gate` | Keep disabled; revalidate only against native UIR-13 evidence. |
| `graphrag-raptor-observability-prometheus-grafana-roadmap.md` | `[10]` / `GRO-native-successor-routing` | OWM-14 | `superseded_no_repo_slice` | Preserve evidence; route reusable proof to native successors. |
| `memory-raptorgraph-consolidation-roadmap.md` | `[11]` / `NMG-05A-causal-store-and-review` | OWM-15 | `normalization_blocked_fresh_architecture_required` | Require fresh Root/Sol architecture; do not patch terminal lineage. |
| `forge-usi-code-source-adapter-roadmap.md` | `[12]` / `FCA-03A-R2-public-page-value-object-validation` | OWM-15 | `normalization_blocked_fresh_architecture_required` | Require materially fresh architecture before a claim. |
| `rag-memory-tokenization-roadmap.md` | `[13]` / `RMT9-P0A-offline-tokenizer-asset-manifest-contract` | RMT recovery | `normalization_blocked_fresh_architecture_required` | Preserve terminal evidence; require fresh architecture. |
| `codebase-memory-integration-roadmap.md` | `[14]` / `CBM-09-canonical-tool-and-agent-routing` | OWM-16 | `normalization_blocked_dependency_reconciliation` | Reconcile TAX/USI dependencies and serialized handoff read-only. |
| `code-lineage-timeline-roadmap.md` | `[15]` / `CLT-03-file-introduction-rename-and-move-lineage` | OWM-17 | `accepted_released_round6_c_recovery` | Preserve; re-audit only the next unaccepted frontier. |
| `unified-source-index-implementation-roadmap.md` | `[16]` / `USI-15-synthetic-staging-and-activation-packet` | OWM-15 | `blocked_by_dependency_root_only` | Wait for UIR-14, ULO-14, and UDA-18. |
| `unified-source-index-domain-adapter-rollout-roadmap.md` | `[17]` / `UDA-01-adapter-registry-and-capability-manifest` | OWM-20 | `accepted_released_round6_b` | Preserve; select later scope only through a fresh claim. |
| `unified-source-index-data-lifecycle-operations-roadmap.md` | `[18]` / `ULO-01-stable-owner-scope-and-alias-contract` | OWM-21 | `accepted_released_round6_a` | Preserve; continue only through its dependency chain. |
| `abc-frontpage-v2-build-plan.md` | `[19]` / `FRONTPAGE-V2-structured-slice-normalization` | OWM-8 | `needs_design_and_normalization` | Wait for approved UI/design structure. |
| `ai-lens-design-roadmap.json` | `[20]` / `AIL-DESIGN-1-visual-system` | OWM-8 | `needs_design` | Wait for fixed UI paths and independent reviewer. |
| `odysseus-harbor-one-autonomous-workspace-roadmap.md` | `[21]` / `H1-29-canonical-planning-projection` | OWM-28 | `normalization_blocked` | The denominator predecessor is complete at Rev286. H1-29 still lacks exact allowed paths, hotfiles, and tests; close those read-only before any claim. |
| `planning-definition-editor-roadmap.json` | `[22]` / `PDE-08-real-save-undo-smoke` | Planning UI | `waiting_on_user` | Wait for user authorization. |
| `product-planning-skills-integration-roadmap.md` | `[23]` / `PPS-1-skill-import-compatibility` | Planning skills | `waiting_on_user` | Wait for user authorization. |
| `privacy-safe-tool-analytics-roadmap.md` | `[24]` / `TUA4R-agent-streaming-regression-guard` | OWM-12 | `accepted_released_round6_b` | Preserve; keep capture/backfill disabled. |
| `telegram-todo-domain-truth-roadmap.md` | `[25]` / `TTD-07A5-poll-webhook-sweep-and-turn-lease` | OWM-22 | `accepted_released_fd2340a6_live_default_off` | Preserve the one accepted parent and its five child-release evidence records; TTD-07A6 is dependency-ready but unselected. |
| `faster-whisper-transcription-protocol-roadmap.json` | `[26]` / `TRP-07-offline-integration-backup-and-live-packet` | OWM-24 | `blocked_after_fresh_architecture_and_two_recovery_corrections_not_accepted` | Preserve TRP-03..06; do not revive TRP-07 without fresh architecture. |
| `tool-taxonomy-registration-roadmap.md` | `[27]` / `TAX7-existing-admin-ui-consumes-the-catalog` | OWM-11 | `repo_implemented_automated_gates_pass_user_ui_acceptance_open` | Wait for user/UI acceptance; keep activation off. |
| `lens-code-graph-roadmap.md` | `[28]` / `LCG-00-ux-truth-and-navigation-contract` | OWM-18 | `waiting_on_explicit_goal_and_ui_owner` | Wait for explicit goal and UI owner. |
| `unified-source-index-runtime-integration-roadmap.md` | `[29]` / `UIR-03-composition-root-and-dependency-injection` | OWM-19 | `accepted_released_superseded_aggregate` | Count only accepted UIR-03A Go-A R2 and UIR-03B R2; the generic aggregate and recovery suffixes are non-counting aliases. |

All paths above are under `docs/plans/`. Count proof: Guidance active-profile
sources = 30; Round 6 frontier rows = 30; both unique path sets are identical.

### Canonical open-unit inventory (72 IDs; denominator published at Rev286)

This is the context-free, lexically sorted open-unit inventory reconstructed
from all 30 active sources after the accepted HWA/Temporal normalization. It
is the open remainder of the verified Rev286 global denominator. Do not delete
blocked, design, live, or operator units to make the count close.

The per-ID continuation ledger is
`docs/plans/open-work-continuation-inventory.json`, raw Git blob
`3237c7311481a0c1d7d165c323c1d880326ccbe5`, raw SHA-256
`942602eb9386bbacb39425cb13c351c968b412cb9039feeb4b054e9f2a5a37d3`,
34,055 bytes. It accounts for all 72 IDs exactly once and gives each ID its
class, authority locator, current status, immediate action or literal
`not_declared`, hard stop, verification route, and dependencies. Its state is
`verified_revision286_reference_not_claim_or_live_authority`: use it to find
the current source, never to manufacture a claim, gate approval, or live
authority. Its one unresolved record is the explicit baseline-only
`not_declared` metadata limitation; do not infer missing actions or gates from
feature similarity. The independently reviewed pre-transaction inventory blob
was `30561b1c22165f4cbc27d4981039fecd7e0cb283`; the current blob above records
the successful Rev286 publication without self-pinning post-transaction router
bytes.

Repo-local or repo-recovery candidates (19):

```text
AMH-01-root-policy-and-runbook
ANTI-HALLUCINATION-next-domain-audit
CAO-08G-verification-repair-independent-review
CBM-09-canonical-tool-and-agent-routing
DLF1B-telegram-history-redaction-fix
H1-29-canonical-planning-projection
HWA-10-end-to-end-fake-pipeline
HWA-2-canonical-subagent-commit-bridge
HWA-6-provider-delivery-worker
HWA-7-merge-promotion-queue
HWA-8-deploy-promotion-queue
HWA-9B-delivery-merge-deploy-projection
LCG-00-ux-truth-and-navigation-contract
LCL-01-observation-and-hypothesis-contract
NMG-05A-causal-store-and-review
PPS-1-skill-import-compatibility
SAR-02-fresh-release-evidence
TAX7-existing-admin-ui-consumes-the-catalog
USI-15-synthetic-staging-and-activation-packet
```

Design/UI candidates (19):

```text
AI Lens Design Roadmap
AIL-DESIGN-1-visual-system
AIL-DESIGN-2-static-cockpit-prototype
AIL-DESIGN-3-jspace-animation
AIL-DESIGN-4-flow-and-timeline
AIL-DESIGN-5-detail-drawers
AIL-DESIGN-5A-information-panels
AIL-DESIGN-6-responsive-and-accessibility
AIL-DESIGN-7-live-data-wiring-polish
AIL-DESIGN-GATE-live-data-wiring
AIL-DESIGN-GATE-placement
AIL-DESIGN-GATE-visual-intensity
FRONTPAGE-V2-DESIGN-ACCEPTANCE
FRONTPAGE-V2-structured-slice-normalization
H1-GATE-INBOX-SEMANTIC-UX-ACCEPTANCE
LCG-UI-LIVE
LCL-06-harbor-and-act-advisory-surfaces
LCL-PRODUCT-POLICY-ACCEPTANCE
TAX7-UI-ACCEPTANCE
```

Live/external candidates (30):

```text
AGENT-12H-LIVE-GO
CBM-LIVE-ACTIVATION
CLT-LIVE-ACTIVATION
GMI-LIVE-ACTIVATION
HWA-LOCAL-COMMIT-LIVE-GO
LCL-03A-local-background-worker-activation
LCL-08-temporal-soak
LCL-09-production-activation
LCL-LOCAL-BACKGROUND-PROCESS-GO
LCL-PRODUCTION-ACTIVATION-GO
LCL-TEMPORAL-SOAK-GO
PDE-08-real-save-undo-smoke
PLANNING-WRITE-GO
RMT-LIVE-REINDEX
RMT9-TOKENIZER-PROVENANCE-IMPORT-GO
TAX-LIVE-ACTIVATION
TEMPORAL-PRODUCTION-ACTIVATION-GO
TLR-10-12h-wall-clock-soak
TLR-11-production-activation
TRP-08-debian-live-test
TRP-LEGACY-BACKUP-PROOF-RECONCILIATION
TRP-LIVE-DEPLOY-MODEL-BACKUP-SMOKE
TRP-MODEL-MANIFEST-METADATA
TRP-PRODUCTIVE-PRIVATE-AUDIO
TUA4R-DEPLOY-AND-AUTHENTICATED-SMOKE-GO
USI-LIVE-ACTIVATION
coding-loop-publication-and-live-go
coding-loop-user-acceptance
deploy-live-go
mcp-service-availability
```

Operator-selection candidates (4):

```text
AMH-GATE-EXPLICIT-GOAL-OR-REPRIORITIZATION
LCG-EXPLICIT-GOAL
LCL-EXPLICIT-IMPLEMENTATION-GOAL
PPS-G0
```

Explicit deduplication aliases, applied once and only once:

```text
HWA-4-temporal-activity-execution-backend -> TLR-04-activities-claims-heartbeats
HWA-5A-temporal-workflow-coordinator -> TLR-03-deterministic-workflow
HWA-5B-temporal-worker-coordinator -> TLR-05-signals-updates-idempotency
HWA-9A-agent-status-projection -> TLR-06-history-agent-projection-api
```

The HWA record expressly forbids merging HWA-2, HWA-6/7/8, HWA-9B, or
HWA-10 into Temporal units. HWA-10 is not an alias for TLR-08/09.

Revision 285 identity resolutions imported into Rev286:

1. Open Work lists the accepted RMT8-v2 recovery as the exact ninth overlay
   addition; the completed full rebaseline counts it inside Rev286's 173
   accepted units.
2. `HWA2-canonical-subagent-commit-bridge` is the non-counting historical
   spelling of the one canonical open unit
   `HWA-2-canonical-subagent-commit-bridge`.
3. `TTD-07A5-poll-webhook-sweep-and-turn-lease` is one accepted default-off
   unit at `fd2340a6`; R0 and A5A through A5C3 are architecture/execution
   evidence, not additional denominator units.
4. The generic UIR-03 aggregate is superseded by exactly two accepted units:
   UIR-03A Go-A R2 and UIR-03B R2. Recovery suffixes remain historical claim
   identities, not separate units.
5. Exact raw comparison against the invalidated revision-2 source map found 26
   unchanged active sources and four intentionally changed sources: accepted
   HWA/Temporal normalization plus the TTD/UIR projection normalization above.

The `mcp-service-availability` provenance is resolved to the exact current
source path `docs/plans/autonomous-coding-production-readiness-roadmap.json`.
The deterministic candidate
format is `odysseus.open_work_denominator_candidate`: sorted 30 source paths and
raw blobs, authority blobs, sorted alias/exclusion records, sorted unit objects
with class/accepted/source/authority refs, and sorted contradiction records;
hash compact recursively sorted-key UTF-8 JSON with the digest field omitted.

The current deterministic candidate is
`docs/plans/open-work-denominator-candidate-rev285.json`, raw Git blob
`a678fd2290acf032ed5747e4fe424d5874bfc30b`, raw SHA-256
`1721cf5c70967e27f667d6c7dd984a1e17692dfc0a74e1154915cce31f1f5fc4`,
semantic digest
`sha256:5c921e5297fd5b7b3e852770094dbf04b4db92f67a264bb07b56137d120f3eee`.
It contains 245 unique candidate units: 173 accepted and 72 open
(19 repo, 19 design, 30 live, 4 operator). Its derivation is 150 accepted
baseline IDs after the count-neutral UIR replacement, plus nine accepted
overlay IDs, plus 14 unique accepted HWA/Temporal source-normalization IDs.
Independent reviewer `Banach` verified this exact blob, all 30 source blobs,
the 245 unique IDs, 173 accepted dispositions, 72 open dispositions, aliases,
and the compare-before authority tuple. Root/Sol then imported it atomically at
Rev286. The candidate remains historical pre-transaction evidence; Open Work
and Round 7 are the current denominator authority.

Reproducible open-inventory checksum for this snapshot:

```json
{"authority_blobs_git_no_filters":{"guidance":"7d4b43dc7f91c1f9cfc9837d3b7bb425f50fc3f7","hwa_packet":"c0075c15f86a53642ebccd8cd00e4734a126958d","hwa_source":"77318983c9dcc5b124b1c1ac9408f27ce08c09d1","open_work":"a08e49b97dcb1eb9749bfd8aa561c0c6011933c8","round6":"0cb4c79ca47c1302038e4c9f0849c0284cf327f0","round7":"8126a645155c57e4ddbf98203725938249310694","temporal_source":"441d2f335b1aebd1876b015846e39057bd5bc89b"},"authority_revision":286,"current_inventory_blob_git_no_filters":"3237c7311481a0c1d7d165c323c1d880326ccbe5","open_counts":{"design":19,"live":30,"operator":4,"repo":19,"total":72},"published_progress":"173/245","verification_state":"verified_revision286_reference_not_claim_or_live_authority"}
```

The authority blobs in this compact receipt are the independently reviewed
Rev285 compare-before tuple. The current post-transaction router blobs are the
Rev286 tuple recorded in section 6. This avoids an impossible self-pin while
making the publication transition auditable.

### Overlay authority anchors

- OWM-24: `faster-whisper-transcription-protocol-roadmap.json`
  `$.latest_trp05_acceptance`, `$.latest_trp06_acceptance`,
  `$.latest_trp07_recovery_rejection`; Round 6 `$.roadmap_frontiers[26].slice`.
- OWM-25: Round 7
  `$.architecture_packets["ACPR-13D-windows-canonical-device-alias-normalization"]`;
  Round 6 `$.roadmap_frontiers[1].slice`, `[2].slice`, and `[11].slice`.
- OWM-26: `autonomous-coding-production-readiness-roadmap.json`
  `$.subagent_context_and_delegation_contract`; Open Work
  `$.recommended_execution_order[17]`.
- OWM-27: `live-contextual-layer-roadmap.json $.slice_queue[1]`; Round 6
  `$.roadmap_frontiers[8].slice`.
- OWM-28: Round 7 `$.waves[0].slices[2].execution_claim` now holds the terminal
  R7-A2 R3R1 release; the long-run and Open Work active-claim arrays are empty.

## 8. Normalized blocked/recovery precursors

These identifiers appear in current/historical normalized frontiers. They are
not direct writer permission. Before touching any of them, read the latest
Round 6 row, Open Work history, and any newer accepted record.

### Fresh architecture or explicit normalization required

- `NMG-05A`: prior causal-store candidates are terminal/rejected. A fresh
  architecture must close the full stored-row lookup/error boundary.
- `FCA-03A-R2`: old R2 candidates are terminal. The later FCA-03A v3 accepted
  record is authoritative; do not reopen R2.
- `RMT9-P0A`: correction budget exhausted and provenance/import action remains
  separately gated.
- `HWA2` / HWA2A historical candidates: exact raw SQLite prerequisite and
  identity handling failed after bounded corrections. Fresh architecture only.
- `UIR-03`: use the accepted Go-A R2 lineage and normalize stale projections
  before any new runtime claim.
- `DLF1B`, `TAX-serialized-handoff`, and mixed-genesis normalization work:
  control-plane predecessor first; product writer only after central green.

### Dependency-blocked or unselected

- `AMH-01`, `CAO-08G`, `SAR-01`, `SAR-02`, `TLR-10-12h`,
  `ANTI-HALLUCINATION`, `LCL-01`, `USI-15`, `CBM-09`, `TTD-07A5` successor
  work, and `LCG-00` require their declared predecessors or fresh selection.
- `TAX7` is not a repo dependency frontier: implementation and automated gates
  are complete; only its user/UI acceptance remains open.
- `GMI-LIVE-ACTIVATION`, `FRONTPAGE-V2`, `AIL-DESIGN-1`, `H1-29`,
  `PDE-07`, `PDE-08`, and `PPS-1` are UI/live/design decisions, not repo-only
  implementation claims.
- Old Round 6 rows saying `TTD-07A5` or `FCA-02` are unclaimed are stale; later
  accepted records win.

### Current Round 7 planned dependency chain

- `R7-A2-execution-authority-ledger-and-snapshot` is terminally blocked under
  R3R1 packet `ABC-R7A2-R3R1-CLOSED-TYPED-COMMAND-AUTHORITY-SOL-ARCH-v1`
  (raw blob `c4d2974f7cef38c636c960b5e641a26202cf4345`, semantic digest
  `sha256:92c075623caee644804d8f90f80939bf33734ca20930c22c4854cb11a24a1ab2`).
  The unregistered first R3 draft was superseded before claim because its
  budget maxima and API/schema detail were contradictory. Never patch R2.
  Registration Revision 281 added the two omitted packet pins, adopted the
  packet's exact `.abc-test-tmp/ABC-R7A2-R3` command roots, and materializes
  packet/evidence blobs. Revision 283 records terminal independent QA and
  Deep-Sol failure after correction 1/1. Fresh architecture is required.
- `R7-A3` depends on an accepted A2 and therefore remains closed; `R7-A4`
  remains closed behind A3.
- Runtime wave: B1 depends on A4, UIR-03, UIR-06, FCA-01; B2 on B1, UIR-06,
  UIR-09, CBM-07A, LCG-03A; B3 on B2; B4 on B2 plus UIR-09/10 and
  FCA-00/01/02/05/06/06A.
- Worker/gate wave: C1 depends on A2, B4, CAO-08C1; C2 on C1; C3 on C2; C4
  on A2, B4, ACPR-12; C5 on C3 and C4.
- Lifecycle wave: D1 depends on ULO-02; D2 on D1/ULO-03; D3 on D2 and
  ULO-04/05; D4 on D3 and ULO-08/09; D5 on D4 and ULO-10..14.
- Golden wave E1/E2 remains planned and nonclaimable until the preceding
  authority/runtime/worker/lifecycle waves supply the exact flow contracts;
  E3 depends on E1/E2 and E4 on E3. Central router validators do not substitute
  for future claim-specific tests.

### Special Round 6 and preclaim stops

- `AMH-01` waits on `AMH-GATE-EXPLICIT-GOAL-OR-REPRIORITIZATION`; after
  selection its focused gate is `tests/test_agent_maintenance_policy.py`.
- `CAO-08G` needs exact paths, hotfiles, and slice tests registered after
  ACPR-12. `ANTI-HALLUCINATION-next-domain-audit` first needs one unsupported
  claim domain with exact IDs, owner, paths, QA, and tests.
- `SAR-02` remains parked until a symlink-capable environment can run its
  unchanged required suite. `TLR-10` remains unscheduled until workload,
  12-hour window, limits, restart injection, and evidence path are exact.
- Open Work preclaim boundaries: FCA-03 needs an accepted resumable,
  revision-bound Forge content reader; UIR-06 needs generation-fenced
  cancellable query execution and owner decisions; USI-07A needs owner and
  lifecycle contracts; CBM-09 needs the runtime and generation contracts.
  Reconcile these read-only and never invent a parallel API or authority.
- Historical `queue_status` fields may still carry older labels and
  `next_parallel_wave_registration.active_claims` contains terminal historical
  entries. The older checkpoint copies at Open Work
  `$.current_position.full_goal_progress.long_run_checkpoint` and Round 7
  `$.long_run_state.global_goal_progress.long_run_checkpoint` were normalized
  at Revision 286 to `173/245`, the current deadline, and the verified
  denominator state. Current Rev286 authority has empty active-claim and
  lease arrays; its terminal history includes the accepted RMT8-v2 claim from
  Revision 279 and the blocked R7-A2 R3R1 claim from Revision 283.

## 9. Candidates that must not be revived

- TRS-01A rejected observability candidates.
- FCA-02 R2 through R5 terminal `released_blocked` candidates. Only the later
  FCA-02 R6 accepted tuple may be used as current dependency evidence.
- FCA-03A Rev210 and R2 rejected candidates.
- HWA2A Rev227, v2, and later rejected/frozen candidates.
- NMG-05A v2 through v6 rejected candidates.
- RMT8 v1 token-inventory rejected candidate. Rev279 accepts only the
  materially different v2 effective-input state-matrix recovery from the exact
  two frozen candidate blobs; it does not revive or patch v1.
- RMT9-P0A rejected candidate.
- Historical TRP-07 drafts and rejected checkout/stash variants. The final
  stabilized recovery was also rejected after fresh architecture and two
  bounded correction cycles; no third repair without a new architecture and
  claim.
- Any terminal candidate whose correction budget is already 1/1.

Keep these bytes as evidence. Do not patch, commit, publish, or use them as a
baseline for a new claim without a materially fresh architecture transaction.

## 10. Immediate continuation sequence

1. Read newest user steering, `AGENTS.md`, Open Work, Round 7, and this file.
2. Read `git status --short` and the path-scoped staged/unstaged diff in every
   checkout before any write.
3. Recompute Open Work/Round 6/Round 7 raw blobs and confirm no central claim or
   lease is active. Verify the packet-local HWA normalization is released on
   the exact packet/source blobs recorded in section 6. Rebind the 72-record
   continuation ledger before using any of its authority locators.
4. Preserve accepted `ABC-RMT8-V2-REV278` exactly as frozen and released at
   Revision 279. Do not reopen its exhausted correction budget or reconstruct
   its product bytes from prose.
5. Preserve terminal R7-A2 R3R1 evidence without another patch. Preserve the
   accepted source and identity normalizations and the verified Rev286
   denominator. Run `$reflect` over the 19 repo-open units, select the smallest
   dependency-ready architecture/recovery predecessor, and register a writer
   only after exact paths, pins, tests, lease, and central readback exist.
6. The public independent scope review and one-parent/one-commit ancestry are
   complete. The exact remaining platform request is:
   `Push commit ea181f52e72a24d91480757c55a298fbb77249b1 nach fuzzy/dev.`
   After that exact approval, push only this commit and monitor exact CI.
7. After CI, use only the redacted Debian probe. Repair backup readiness through
   the repository's bounded route, obtain backup/restore evidence, then deploy
   the exact revision transactionally if platform action review permits it.
8. Update this file after every acceptance/release, authority revision,
   commit/push/CI result, deployment/readback, or materially changed blocker.

The denominator predecessor is packet-locally accepted and released:
`docs/plans/abc-hwa-source-reconciliation-normalization-architecture.json`,
raw blob `c0075c15f86a53642ebccd8cd00e4734a126958d`, semantic digest
`sha256:ccf05a15010df328d10f18a141d177239b82001704c211c69cad6e02d7cf3e23`.
Its accepted source blobs are HWA `77318983c9dcc5b124b1c1ac9408f27ce08c09d1`
and Temporal `441d2f335b1aebd1876b015846e39057bd5bc89b`.
The slice itself did not infer or persist the denominator. Root/Sol completed
that separate atomic transaction at Rev286 after independent review.

## 11. Verification commands

Run commands from the applicable checkout and use fresh deterministic temporary
directories. Exact claim packets may specify stronger commands; those win.

```text
git status --short
git diff --check
git diff --cached --check
python -m json.tool docs/plans/open-work-completion-master-roadmap.json
python -m json.tool docs/plans/parallel-roadmap-execution-round7.json
python scripts/roadmap_safe_queue_audit.py --format json
python -B scripts/validate_integration_spine_convergence.py --format json
python -B scripts/validate_parallel_roadmap_execution.py --format json
python -B scripts/roadmap_multi_agent_guidance.py --format json
```

For the public projection, retain the exact staged tree during tests. Run the
changed retained tests first, then the full suite, then repeat the staged diff
and privacy readback. Do not use an earlier full-suite result after any byte
change.

## 12. `$reflect` recovery protocol

On an ordinary blocker:

1. State the exact failed invariant or unavailable capability.
2. Rebind current bytes, claims, lease, pins, and strongest counterexample.
3. Distinguish product failure, control-plane failure, test contradiction,
   infrastructure failure, and external-action review.
4. Preserve the candidate and stop only that mechanism/slice.
5. Release/defer it if its stop rule requires that outcome.
6. Continue with a disjoint runnable slice, architecture predecessor,
   verification, release preparation, or handoff maintenance.
7. Never retry the same ACL/helper/claim/external mutation through disguised
   variants.

## 13. Handoff maintenance checklist

Before the continuity deadline, a fresh context-free agent must be able to
answer all of the following from repository files alone:

- What is the latest authority revision and accepted/denominator status?
- Which claims/leases are active and which paths do they own?
- Which exact public release candidate is tested, committed, pushed, in CI,
  deployed, or live?
- What remains unverified?
- What is the current safe next repo-only action?
- Which open lanes are dependency-, design-, UI-, operator-, or live-blocked?
- Which terminal candidates must never be resumed?
- What exact redacted deployment, backup, rollback, and health route applies?
- Which single external action, if any, is still awaiting platform approval?

When this snapshot changes, update the snapshot date, revision, workspace
identities, release evidence, open-lane rows, immediate sequence, and
`Not verified` statements. Do not copy raw private diagnostics into this file.

## 14. Not verified at this snapshot

- A single uninterrupted all-repository Windows run on the exact final public
  commit. The exact final 73-file lane is green with 1671 passed; the near-final
  full run and classified Linux rerun are recorded in section 4.
- Push to `fuzzy/dev` and exact automatic Required CI result. Optional manual
  OCI publication was not requested and is not a Debian deploy prerequisite.
- Local OCI build (local Docker engine unavailable).
- Fresh backup snapshot, restore smoke, and backup credential readiness.
- Debian access recovery through the approved SSH identity path, deployment,
  health/revision readback, or live STT/runtime smoke.
- UI-live readiness and Version 1.0 release.
