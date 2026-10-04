# Odysseus Master Handoff

Current-status pointer (2026-10-04): this document preserves dated execution
snapshots, not the current queue. Read `docs/plans/harbour-one-product-roadmap.json`
and `docs/plans/open-work-completion-master-roadmap.json`, then the latest
applicable scoped handoff. See `docs/plans/evidence/harbour-one-plan-reconciliation-20260905.json`
for the planning reconciliation. Historical next-action text below does not
reopen accepted or released work.

## Resume checkpoint: 2026-10-04

The subsequent local commits are `7bb77c4b` (frozen-source LF portability),
`eb3631ec` (cooperative snapshot deadline/stop checks), `84c17e35` (handoff)
and `8a117ee9` (opt-in lazy offline guards). The snapshot and lazy guards reuse
exact hash-matching accepted evidence; their current whole-component reviews
passed. They do not establish full app shutdown or app-wide offline startup.

The post-census read-race repair maps failures on either later primary or
supporting reads to fixed `stale_authority`, suppressing raw exception context.
All fourteen injected missing/unreadable second-read cases passed, followed by
one terminal migration-module run with 71 passing cases. Corpus authority,
actual Debian checkout and migration prepare/accept remain separate gates.

Local integration update: commit `2d9029a3` contains the independently reviewed
Planning Python component (twelve changed paths; the thirteenth reviewed
contract file was already unchanged in HEAD). The separate Inbox test module
remains outside that commit. The bounded HEAD-Python overlay passed 24 Planning
and graph cases before an independently reviewed EOF-only whitespace fix.

The frozen-source LF portability candidate allows only nine literal SHA256
values for six historical members: the existing bytes plus the three known LF
checkout variants. Historical source-item hashes, manifest identity and strict
successor authority pins remain unchanged. Four focused cases and the complete
migration module (57 cases, one terminal run) passed. Independent review and
local integration of this candidate are tracked in the execution record.
These fixture checks do not establish an actual Debian checkout, clean corpus
authority or permission to prepare/accept a migration.

The resumed execution frame is local development without a newly invented
sprint deadline. Continue scoped ready packets, independent review, focused
checks and local commits. Stop affected work for overlapping ownership or
missing owner authority; pause all work on the user's pause request. Push,
Debian changes, deployment, backup and restore retain their concrete gates.

The current execution record is
`docs/plans/handoffs/abc-open-work-5h-run-20260905.json`. Its structured claims,
candidate hashes and verification records take precedence over the historical
next-action text below. The user renewed implementation, independent review,
documentation and local commit authority on 2026-10-04. Publication and live
operations retain their separate action gates.

- Planning durable recovery R8 removes the child-side stale-head precheck for
  approval and revert. Explicit retries with the same idempotency key reach the
  store's existing locked replay-before-CAS boundary. A fresh key with an old
  head still fails as `stale_authority`. Three focused process tests passed in
  the repository virtual environment, `py_compile` passed, and independent
  static review passed. The four-module lane passed 488 tests on the frozen pre-import-isolation
  candidate. That result remains tied to its recorded source hashes; it does
  not verify the later dispatch or fixture edits.
- Planning MCP graph depth now limits the returned projection: depth 0 returns
  the roadmap, depth 1 includes its direct slices/gates and containment edges,
  and depths 2/3 add known dependency/blocked edges between selected endpoints.
  Depth 3 is explicitly non-recursive. Two focused tests, `py_compile`, scoped
  diff-check and independent static review passed. A full-limit depth-3 edge
  equivalence assertion was not added; the review records this coverage limit.
- The process graph wrapper now returns a bounded, empty, incomplete summary
  for the producer's valid unavailable-lens state, instead of `child_failed`.
  Malformed available or unavailable lenses still fail closed. Three graph
  contract tests and the previously failing real-process namespace test passed;
  independent delta review passed. The four-module cumulative lane passed 488 tests before the later
  import-isolation repair; its exact hash binding remains in the execution
  record rather than being relabeled as a PASS for later revisions.
- Planning dispatch now avoids loading the Inbox/Knowledge family for Planning
  requests. Inbox type imports occur only within guarded Inbox paths; failures
  cannot escape the fixed error mapping. Independent R2 review passed, as did
  its two isolated import regressions and 33 dispatch cases. The same 33 cases
  are now separated into 22 Planning and 11 Inbox cases with the seven moved
  functions/decorators unchanged; independent split review passed.
- The store-only fixture removes an unused native/private-context dependency
  from four store tests without weakening their assertions. It retains six
  accepted heads and twelve revisions. All 369 store tests and independent
  review passed. Six focused bundle cases also passed against an overlay using
  HEAD Python dependencies with zero Inbox-import attempts. The overlay still
  uses the working-tree roadmap corpus: complete clean-checkout migration and
  corpus authority are not verified by that result.
- Local reviewed commits are `d9fe9505` (handoff and MCP runbook), `0823f7bc`
  (four runtime-readiness probe files; 76 current tests passed), and `a6243fc6`
  (eleven backup-helper/installer/transport source and test files; 95 current
  tests passed). Each package received independent review and an exact staged
  content check. These commits do not cover every remaining dirty file.
- The backup unit now uses `TimeoutStartSec=1860s` for its `Type=oneshot`
  activation, with the existing five-second stop bound and control-group kill.
  The installer embeds the same unit. The new candidate installation transport
  is digest-bound and explicitly gated by `execute=True`; the published pins
  remain unchanged from its recorded starting point. Live installation,
  systemd execution, backup and restore have not been performed.
- The migration structural-census contract remains `waiting_on_user`. Aggregate
  counts from the 2026-08-29 freeze do not establish a complete immutable path
  inventory, and the current mutable working tree cannot replace that authority
  implicitly.
- Existing Luna/Terra work is being inventoried for coherent, reviewed local
  commits. Generated test directories and temporary utilities are excluded from
  product commit packages. A terminal writer claim alone does not establish
  acceptance of every later change on the same path.

Not verified at this checkpoint: the current cumulative Planning lane, full
App/MCP/context integration, production deployment, live Debian maintenance,
external MCP clients and temporal stability. Refer to the execution record for
the latest results and commit identities as each package is completed.

Snapshot: 2026-08-24T12:22:00+02:00, Europe/Berlin
Continuity deadline: 2026-08-24 23:20:34 Europe/Berlin
Canonical authority at snapshot: Open Work / Round 7 Revision 377; the Round6
operator-deferral projection is accepted at 28/28 with zero active claims or
leases. LCG-00 is released before writer dispatch on a current raw-Round6 versus
Guidance projection-blob mismatch; PPS-1A remains at exact clean origin and
requires fresh architecture, PPS-2 v3 waits on canonical PPS-1 runtime, and
PPS-4 v1 architecture is released
Last historical denominator snapshot: Revision 286 verified 173 accepted of 245
Current full-goal progress: 173/?; the post-PPS and post-operator-steering
canonical member set requires reconciliation before another x/y ratio is valid

## Current 12h closure: Revisions 376-377

- **LCG-00 registration was attempted and terminally released before product
  work.** Root/Sol froze the two-new-path materialized-genesis architecture at
  `docs/plans/abc-lcg00-lens-code-graph-ux-contract-materialized-genesis-architecture.json`.
  Luna worker `01a03336-0306-7d92-bc94-a1f6d9be2e2e` verified both product
  paths absent and changed nothing. The first full `repo_claim_v2` registration
  correctly mirrored 11/11 external claims, but Integration rejected the
  current foreign dirty Round6 raw blob
  `e5f4e1a93c358874203cba3b00b9b9afc74e5b1f` against the Guidance-bound blob
  `2b96aeb7510e59776b1bba42721804c0c3052dfe`; the initial Evidence receipt also
  lacked the rolling inventory receipt. `$reflect` forbade normalizing or
  rebinding that foreign authority inside the feature claim. Revision 377
  releases `ABC-LCG00-REV376` with no candidate and both product paths absent.
  Terminal evidence:
  `docs/plans/evidence/abc-lcg00-rev377-terminal-registration-release.json`,
  raw blob `83ff699c40e15ec4b6eceef3ea738090d60159ec`; the current terminal Claim
  evidence is raw blob `45e38a736b6d8843973695b7b505e7b2822b8ef2`.
  Product correction remains unused 0/1; a fresh registration may reuse only
  the unchanged frozen architecture after the external authority converges.
  The exact four-file architecture/claim/terminal/public-preflight Evidence set
  is committed locally at `0c6c0a1f`; no router, handoff or foreign dirty path
  was included and no publication followed.
- **Control-plane readback is green after release.** Integration Spine, Round6
  and Guidance all pass; Round6 reports 28/28 roadmaps, three waves and zero
  claimable frontiers. Open Work and Round7 external-claim projections are
  identical and active claims/leases are empty.
- **Public projection is clean and locally preflighted.** The clean clone
  `C:\Users\nkatz\Documents\odysseus-public-release` remains on
  `codex/public-release` at
  `ea181f52e72a24d91480757c55a298fbb77249b1`. A read-only local runtime ran
  graph projection, runtime status, release manifest and CI workflow contract:
  **52 passed in 38.76s**. A second ten-file release-contract lane covering
  manifest/status/artifact/readiness/decision/handoff/follow-up/orchestration/
  evidence/router contracts passed **52 tests in 77.45s**. Final status and
  diff-check remained clean. Evidence:
  `docs/plans/evidence/abc-public-release-ea181f52-20260824-preflight.json`.
  No push, deploy or live action was attempted.
- **Exact next action:** first converge the foreign Round6 raw/projection byte
  authority without overwriting foreign work, or wait for its owning change to
  land. Then register a fresh LCG-00 claim with an exact rolling inventory
  receipt and repeat tests-first. Stop on another raw/projection mismatch,
  third product path, source-before-RED, UI/source expansion, or any external
  action. If authority cannot converge safely, continue public-release
  verification/documentation or another disjoint recovery precursor.

## Current closure: Revisions 362-375

- **PPS-1A Revision 374 terminal v2 architecture release:** two independent
  read-only reviews rejected corrected architecture blob
  `fec200c7bd7ac6e657b6a4fc78119d18d01f132b`. One proved that an emitter value
  whose first line is more indented than a later line cannot round-trip under
  the fixed-first-indent parser. The other proved that clean origin treats a
  block-list item `|+` as an ordinary string while the packet also required
  block-looking list items to fail, contradicting its legacy-list guarantee.
  The Revision 371-to-372 correction budget was already consumed, so no second
  patch or product claim is allowed. Evidence:
  `docs/plans/evidence/abc-pps1a-v2-rev374-terminal-architecture-release.json`.
- **PPS-1 Revision 375 safe pause:** after the user's explicit reversal request,
  Root restored all five modified PPS files to their exact raw clean-origin
  blobs and removed only `tests/test_skill_format.py` plus the late v6 freeze
  evidence. The seven-path status is empty and the known baseline reproduces
  15 passes with two existing warnings. The Rev373 recovery claim is released;
  there are zero active PPS claims. Resume by freezing a materially fresh
  Root/Sol PPS-1A architecture and obtaining independent PASS. Only then may a
  DeepSeek tests-first implementation experiment receive the exact packet;
  Root/Sol remains acceptance authority. Evidence:
  `docs/plans/evidence/abc-pps1-rev375-stale-v6-recovery-complete.json`.

- **PPS-1 Revision 373 late-write quarantine and exact recovery:** `$reflect`
  detected that a previously terminally released v6 worker wrote the exact six
  PPS product/test paths after Revision 370 despite having no active claim or
  router authority. Its own late evidence says router registration was absent
  and identifies the complete candidate tuple. Claimed green tests cannot mint
  authority, so the tuple is quarantined and must not be reused, reviewed,
  staged or committed. `ABC-PPS1-STALE-V6-RECOVERY-REV373` owns only those six
  paths for exact restoration to the recorded Rev334/370 clean origins;
  `tests/test_skill_format.py` must become absent. In parallel, independent Sol
  reviews only fresh corrected PPS-1A v2 architecture blob
  `fec200c7bd7ac6e657b6a4fc78119d18d01f132b`; it grants no product authority.
  Evidence: `docs/plans/evidence/abc-pps1-rev373-stale-v6-write-recovery-claim.json`.

- **PPS-1A architecture correction and acceptance:** Revision 362 records the
  first independent Sol `FAIL`: implicit block-scalar indentation was
  contradictory for a four-space first content line followed by a two-space
  line. `$reflect` limited the correction to first-nonblank fixed indentation
  plus one exact RED node. Fresh independent Sol review then passed unchanged
  architecture blob `aea2fa5d7e043c00a3712869796febb42ca0bf29` at Revision
  363. Root-level `SKILL.md` is a valid zero-component bundle root and all
  selection/path failures must precede destination creation. Evidence:
  `docs/plans/evidence/abc-pps1a-rev362-architecture-review-fail.json` and
  `docs/plans/evidence/abc-pps1a-rev363-architecture-acceptance.json`.
- **PPS-1A Revision 364 active claim:** `ABC-PPS1A-REV364` owns only
  `services/memory/skill_format.py`, `services/memory/skills.py`,
  `tests/test_skill_format.py`, and
  `tests/test_skills_manager_owner_isolation.py` through
  `2026-08-23T21:19:30+02:00`. Critical Sol worker
  `01a02f69-aa95-7811-acf4-b35598b109e9` must materialize and run every named
  behavioral RED node before either source edit. Read-only importer and route
  pins remain exact. Stop on a fifth writer path, source-before-RED, residual
  write, raw error, regression, stage/commit/push/deploy/live action, or any
  attempt to call this canonical PPS-1 completion. PPS-1B remains mandatory
  before PPS-1 acceptance. Claim evidence:
  `docs/plans/evidence/abc-pps1a-rev364-tests-first-claim.json`.
- **PPS-2 test-runtime blocker removed:** the unchanged preserved PPS-2 v3
  candidate passes `tests/test_product_planning_skills.py` 4/4 on the declared
  Linux namespace-bootstrap route. It remains unfrozen and unaccepted, waiting
  only on a frozen or accepted PPS-1 runtime tuple.
- **PPS-1A Revision 365 verified RED and claim transfer:** the initial writer
  stayed silent after activation; its shutdown raced with completion of exactly
  the two allowed test edits. Root verified all ten named nodes, both unchanged
  source blobs and the complete focused result: 27 expected behavioral failures,
  17 passes, 3 warnings, zero collection/bootstrap errors. The exact test blobs
  are `70ef7a9ebb605b14b2d02f8c7452d14feba0af38` and
  `d40f76275c51edc0638efacd74dca215d9eaccd8`. The claim transfers once to
  replacement critical Sol worker `01a02f71-c9c5-7112-beea-5c084b560d61`,
  which may now change either source path within the same four-path scope. The
  replacement budget is exhausted. Evidence:
  `docs/plans/evidence/abc-pps1a-rev365-red-and-claim-transfer.json`.
- **PPS-1A Revision 366 frozen candidate:** before freeze Root reproduced a
  case-sensitive GitHub-root escape (`Pkg/SKILL.md` plus sibling `pkg/...`).
  One bounded RED/correction changed only the exact prefix comparison. Final
  immutable blobs are `b64c88971413e2de165fc87b8fe4c63ef3cef782`,
  `06269a064ed91d2118da2ec835868a90a457acf1`,
  `70ef7a9ebb605b14b2d02f8c7452d14feba0af38`, and
  `fce4df6f7465a99fa9d77bbb058049aaeb6c4771`. Root reproduced 45 focused
  and 20 dependency passes, compile and scoped diff-check. The writer claim is
  released; no active PPS claim exists. Exact next action: fresh independent
  Sol review of only this tuple. Even PASS accepts PPS-1A only as an internal
  baseline; PPS-1B remains mandatory before canonical PPS-1 acceptance.
  Evidence: `docs/plans/evidence/abc-pps1a-rev366-candidate-freeze.json`.
- **PPS-1A Revision 367 terminal candidate release:** fresh independent Sol
  review rejected the immutable tuple because `json.loads()` was applied
  recursively to inline-list elements. Clean origin preserves the characters
  backslash+n in `tags: ["line\\nvalue"]`; the rejected candidate produces an
  actual newline. Root reproduced the mismatch. The only correction budget was
  already used for the case-sensitive root escape, so no patch or reuse is
  allowed. Exact next action: recover the four clean-origin states, reproduce
  the 15-test baseline, then require a fresh architecture that limits canonical
  escape decoding to the top-level multiline round-trip without changing legacy
  recursive list parsing. Evidence:
  `docs/plans/evidence/abc-pps1a-rev367-terminal-candidate-release.json`.
- **PPS-1A Revision 368 active clean-origin recovery:**
  `ABC-PPS1A-CLEAN-ORIGIN-REV368` assigns only the four PPS-1A paths to Luna
  replacement worker `01a02fa5-3cdf-7241-8599-6dbd38ceff5c` through
  `2026-08-23T21:14:26+02:00`. Restore three exact clean blobs and remove only
  `tests/test_skill_format.py`; no product implementation or rejected-candidate
  reuse is allowed. Root must read back the tuple and reproduce the 15-test
  baseline before release. Evidence:
  `docs/plans/evidence/abc-pps1a-rev368-clean-origin-recovery-claim.json`.
- **PPS-1A Revision 369 recovery transfer:** the initial Luna channel made no
  target edit and returned no blocker after activation, bounded waits and one
  checkpoint. The unchanged recovery claim transfers once to replacement Luna
  worker `01a02fa5-3cdf-7241-8599-6dbd38ceff5c`; replacement budget is
  exhausted. Evidence:
  `docs/plans/evidence/abc-pps1a-rev369-recovery-claim-transfer.json`.
- **PPS-1A Revision 370 recovery complete:** all three raw blobs match the
  recorded clean origins, `tests/test_skill_format.py` is absent, scoped Git
  status is empty and Root reproduced 15 passes with 2 existing warnings.
  Integration, parallel-roadmap and 140/140 Guidance validation pass. The
  recovery claim is released with no accepted product change. Exact next
  action: fresh architecture separating direct top-level canonical escape
  decoding from recursive legacy list parsing. Evidence:
  `docs/plans/evidence/abc-pps1a-rev370-clean-origin-recovery-complete.json`.

## Historical closure: Revisions 336-348

This section supersedes older USI and Round6 continuation statements below.
It is descriptive evidence only and creates no product, commit, publication or
live authority.

- **PPS-1 Rev348 reflect release:** `$reflect` found that Rev347 changed
  `services/memory/skill_format.py` before the complete architecture-required
  RED body existed; the missing nodes include storage provenance, nested-root
  rebasing, collision families, post-serialization budgets and zero-residual
  failure checks. Durable Rev347 acquisition/recovery/router timestamps were
  also future-dated relative to local Europe/Berlin readback, and its router
  pins did not match the observed router blobs. Rev347 is therefore released
  without a candidate or accepted product change; its partial tuple must not be
  reused, frozen, reviewed, staged or committed. Evidence is
  `docs/plans/evidence/abc-pps1-rev348-reflect-protocol-release.json`. There are
  now zero active PPS claims. Exact next action: register one six-path recovery
  claim, restore the Rev334 clean-origin tuple including absence of
  `tests/test_skill_format.py`, verify exact blobs/static baseline, then start a
  fresh v6 tests-first claim with every named RED test present before source
  edits. Stop on a seventh path, foreign overlap, baseline mismatch or any
  external/live action. Authority: accepted v6 architecture plus Rev348 release
  evidence; no push, deployment or live authority exists.
- **PPS-1 Rev349-350 exact recovery:** Root registered a three-writer-path,
  six-hotfile recovery claim, reversed only the rejected Rev347 partial diff,
  removed only the materialized untracked `tests/test_skill_format.py`, and
  restored the exact Rev334 six-path tuple. All five materialized raw blobs and
  the required absence match; `py_compile`, scoped `git diff --check`,
  Integration, Round6 (28/28, zero claimable, three waves) and Guidance
  (140/140) pass. The exact focused baseline also reproduces `15 passed, 2
  warnings` under Linux Python using existing pure packages plus a process-local
  namespace bootstrap; no repository test harness was added and the broken
  Windows-interop route was not retried. Rev349 is released complete with no product acceptance and
  zero active claims. Completion evidence is
  `docs/plans/evidence/abc-pps1-rev350-clean-origin-recovery-complete.json`.
  Exact next action: one fresh six-path tests-first PPS-1 v6 claim against this
  tuple; every named RED test must exist before the first product edit.
- **PPS-1 Rev351-352 active implementation:** initial critical Sol worker
  `01a02f0b-749d-79d0-92dd-94ba150acfe5` changed no claimed path and returned
  no blocker after activation, one nudge and a final bounded wait. Root closed
  it with the clean-origin tuple unchanged and transferred the claim to the
  one permitted replacement, critical Sol worker
  `01a02f13-5b18-7831-8253-661dda77b6be`, which owns exactly the six accepted PPS-1
  product/test paths through `2026-08-23T19:34:03+02:00`. Claim evidence is
  `docs/plans/evidence/abc-pps1-rev351-v6-tests-first-claim.json`, raw blob
  claim record. The worker must first add and run
  the complete three-module RED body; any product edit before that invalidates
  the claim. Only after valid RED may it implement source-kind/provenance,
  complete prepublication bundle validation, atomic staging/publication and the
  bounded block-scalar subset. Root alone owns routers/evidence. No candidate,
  acceptance, commit, push, deployment or live authority exists yet. The
  replacement budget is exhausted; another silent channel releases the claim
  and shifts the run to disjoint safe work rather than spawning again.
- **PPS-1 Rev353 worker-channel release:** the one permitted replacement also
  changed no claimed path and returned no blocker after activation, checkpoint
  and final bounded wait. Root closed it and released Rev351 without a
  candidate. Exact raw readback proves all five materialized PPS paths still
  equal Rev334 and `tests/test_skill_format.py` remains absent, so this is a
  worker-channel failure rather than a target or architecture failure. Evidence
  is `docs/plans/evidence/abc-pps1-rev353-v6-worker-channel-release.json`.
  Do not respawn PPS-1 again in this channel cycle. Continue disjoint read-only
  denominator reconciliation, PPS-2 pin preflight and clean public-release/
  deployment readiness. A later eligible fresh critical Sol channel may claim
  the unchanged v6 packet. No product acceptance, commit, push, deployment or
  live authority exists.
- **Revision 354 denominator truth correction:** the Revision 286 `173/245`
  tuple remains valid historical evidence, but it predates the activated PPS
  roadmap and later operator steering (including deferred post-release
  maintenance/security work, productized code-graph UI and deferred live-context
  testing). It therefore cannot be presented as the complete current-goal
  denominator. Current router mirrors now report `173/?` while preserving the
  exact Revision 286 snapshot. Exact next action: construct one sorted atomic
  post-steering membership snapshot, independently review IDs, aliases,
  acceptance and class, then publish a new denominator in one transaction.
  Stop on unresolved PPS membership, deferred-vs-cancelled ambiguity, source
  drift or any attempt to turn this descriptive correction into product/live
  authority.
- **Read-only post-steering denominator proposal:** a fresh bounded Terra audit
  retained all 245 historical IDs and nine aliases, reclassified existing
  `PPS-G0` as accepted from the recorded operator Go, and identified eleven
  additional canonical members: `PPS-2` through `PPS-7` and `PPS-G1` through
  `PPS-G5`. That would yield a provisional eventual-full-goal arithmetic of
  `174/256`, but it is deliberately **not published**. `PPS-0` counting,
  `LCG-EXPLICIT-GOAL` versus `LCG-UI-OWNER`, old versus new LCL goal identity,
  and the non-inferred AMH member expansion still require exact authority
  resolution. AMH/LCL remain deferred release-projection work, not cancelled
  full-goal work; Lens Code Graph remains a product feature; PPS-6 remains in
  the full goal but outside the release-core projection. Root then rejected the
  proposed `174/256` arithmetic as a publication candidate: Revision 286 counts
  current open frontiers plus selected gates, while the overlay would count all
  PPS successors but continue omitting comparable dependency-blocked AMH and
  LCG successors. Evidence is
  `docs/plans/evidence/abc-denominator-rev358-membership-method-reflect.json`.
  Because the active objective is the complete canonical roadmap, the next
  denominator must be a fresh full-eventual atomic enumeration across every
  current source, not a rolling-frontier overlay. Current authoritative
  progress remains `173/?` until that complete inventory and independent review
  are atomically published.
- **Revision 359 eventual-unit source census:** Root bound the exact 30
  full-goal sources to current raw blobs (source-map digest
  `sha256:4fb449cce439c6aef1134483aa0d73381756dd40b96d2e758bc1e96d8e31dcc9`).
  Twenty-six match the Rev285 source blobs; AMH, Lens Code Graph, LCL and PPS
  have current steering changes. A prefix-constrained read-only schema probe
  found 390 unique **pre-alias candidates** across 27 machine-extractable
  sources and no cross-source duplicate before semantic alias review.
  Frontpage v2, Anti-Hallucination and RMT lack one uniform source-native ID
  grammar and require exact mapping normalization. Evidence is
  `docs/plans/evidence/abc-denominator-rev359-source-schema-census.json`.
  Never report 390 as the denominator: revisions, aggregate rows, historical
  recovery IDs and non-unit headings still require classification. Exact next
  action is only those three source mappings, followed by candidate-wide
  canonical/gate/alias/non-unit classification and independent review.
- **Revision 360 feature-first reflect correction:** `$reflect` challenged the
  denominator route against the user's actual completion goal. Candidate-wide
  semantic classification of 390 pre-alias rows is not a dependency of PPS-1
  or another product slice and would create a new control-plane mega-task.
  Current progress therefore remains truthfully `173/?`, but denominator
  reconciliation is nonblocking and deferred until the final complete-roadmap
  claim or a concrete identity ambiguity blocks a feature. Do not edit source
  roadmaps merely to make prose countable. Exact next feature route is a later
  materially fresh independent Sol terminal review of the unchanged PPS-1 v6
  architecture and clean-origin tuple; only PASS permits a fresh six-path
  tests-first claim. If that channel is temporarily unavailable, perform only a
  read-only PPS-2 runtime-pin drift preflight or another disjoint feature
  precursor. Evidence is
  `docs/plans/evidence/abc-denominator-rev360-feature-first-reflect.json`.
- **Revision 361 PPS-1 terminal architecture release and PPS-2 pin preflight:**
  the fresh read-only architecture reviewer returned terminal `FAIL` on the
  exact v6 blob. Its skill-root contract selects the containing directory of
  `SKILL.md` but also rejects an empty root, leaving a valid root-level
  `SKILL.md` undefined. Root verified the strongest counterevidence: the current
  importer and focused tests explicitly support repository-root `SKILL.md`.
  All six PPS-1 clean origins remain exact and no product attempt exists. The
  pre-writer architecture-churn circuit breaker is active: do not generate v7
  or register another PPS-1 claim in this cycle. A later materially simpler
  packet must model root-level selection as a zero-component bundle root and
  add the matching RED node. Terminal evidence is
  `docs/plans/evidence/abc-pps1-rev361-v6-terminal-architecture-release.json`.
  In parallel, exact readback confirmed all eight preserved PPS-2 candidate
  blobs, five architecture/attribution pins and three clean runtime pins still
  match Rev327. `py_compile` and scoped diff-check pass. Fresh pytest did not
  start: system Python lacks pytest and the distinct bundled Python route hit
  the known WSL interop socket failure before process start; do not repeat that
  route. PPS-2 remains unaccepted and waits on a frozen/accepted PPS-1 runtime
  plus a working declared test runtime. Evidence is
  `docs/plans/evidence/abc-pps2-rev361-preserved-candidate-pin-preflight.json`.
- **PPS-1 Revision 355 active tests-first claim:** fresh critical Sol worker
  `01a02f27-0a1e-7072-94e2-d935c874d642` owns exactly the six PPS-1 runtime/test
  paths through `2026-08-23T20:04:57+02:00` under
  `ABC-PPS1-V6-TESTS-FIRST-REV355`. Root re-read the accepted v6 architecture
  blob `cc3ec2056b3681eca4b6813e6e0c90d13b436ad9`, its acceptance and the exact
  Rev350 clean-origin tuple before registration. The worker must author and run
  every named three-module RED family before changing any source path. Stop on
  source-before-RED, a seventh path, baseline/architecture drift, unavailable
  RED, router/evidence edits, external effects or live actions. Candidate
  acceptance still requires focused, hostile, central and independent fresh Sol
  review; this claim grants no commit, push, publication, deployment or live
  authority.
- **PPS-1 Revision 356 chronology release:** Root's pre-edit review found that
  the Rev343 correction freeze declares `18:31 +02:00` and Rev344 architecture
  acceptance declares `18:35 +02:00`, while current local readback was only
  `17:11:42 +02:00`. This is the same future-dated control-plane authority class
  previously rejected by `$reflect`; local filesystem times corroborate an
  inconsistency but are not substituted as authority. Root stopped the writer
  before the first claimed-path edit, re-read all six clean origins unchanged
  and released Rev355 with no candidate. Evidence is
  `docs/plans/evidence/abc-pps1-rev356-v6-authority-chronology-release.json`.
  Exact next action: fresh independent review-only Sol PASS on the unchanged
  architecture and clean-origin tuple, followed by a new current-time
  acceptance record; only then may another tests-first product claim exist.
  No commit, push, publication, deployment or live authority exists.
- **PPS-1 Revision 357 review-channel release:** one fresh read-only Sol channel
  was assigned the unchanged v6 architecture blob and exact Rev350 clean-origin
  tuple. It returned no terminal PASS or FAIL after the initial bounded window,
  one checkpoint request and one final interrupt requiring immediate
  conclusion. Root closed the channel; absence of a verdict is not PASS and no
  current-time acceptance was created. All six product/test origins remain
  exact and no candidate exists. Evidence is
  `docs/plans/evidence/abc-pps1-rev357-current-time-review-channel-release.json`.
  Do not retry the identical reviewer channel in this cycle. Continue the
  bounded denominator identity resolution; a later eligible fresh independent
  Sol terminal PASS remains prerequisite to any new PPS-1 product claim.
- **Public-release projection read-only preflight:**
  `C:\Users\nkatz\Documents\odysseus-public-release` is locally clean on
  `codex/public-release` at `ea181f52e72a24d91480757c55a298fbb77249b1`.
  Its local `origin/dev` is `4b59a975a24a9c69a02d216cf56e39e0c2fbcfec`;
  merge-base is `d691cebc0b850e13b7087ab3866d7902940ead67` and
  `rev-list --left-right --count HEAD...origin/dev` reports `1 119`. The one
  projection commit spans 161 files (`49,354` insertions, `536` deletions), so
  it must not be rebased, merged, pushed or published while PPS and the final
  accepted-source set are still moving. Exact next action after repo-only
  feature closure: create a fresh clean projection from the then-current
  release base, replay only accepted clean commits, run the declared release
  test matrix, inspect the complete diff, define rollback/readback, and request
  payload-specific publication authority. Stop on mixed foreign changes,
  rejected candidates, secret/private paths, failing tests or remote/branch
  ambiguity. No release-clone mutation or network action occurred in this
  preflight.

- **USI-07A PRE1A terminal architecture release (Rev336):** the expired
  six-path claim `ABC-USI07A-PRE1A-REV304` is `released_blocked` with no
  candidate and no product change. Fresh Sol review proved that the packet
  simultaneously required bounded cleanup and guaranteed wait/join with zero
  residual resources even when wait can hang; it also could not enforce the
  product-registration boundary within its six paths. Terminal evidence is
  `docs/plans/evidence/abc-usi07a-pre1a-rev336-terminal-architecture-release.json`,
  raw blob `8af24314b46b84281987aa37a55738bca68e40ce`. Do not reuse the packet.
- **Round6 v1 scope release (Rev337):** the original four-path 30-to-28
  normalization candidate was preserved but unaccepted because
  `tests/test_parallel_roadmap_execution.py` contained a directly coupled
  literal `30` outside the claim. Scope-release evidence is
  `docs/plans/evidence/abc-round6-operator-deferral-projection-rev337-scope-release.json`,
  raw blob `bd10a81aa35a51e0c9a296a4bcc07427ee195797`.
- **Round6 v2 acceptance (Rev338-340):** the corrected five-path candidate is
  released and accepted after independent fresh Sol PASS. Guidance and Round6
  now agree on exactly 28 active roadmaps/frontiers; only AMH and LCL were
  removed because current operator steering classifies both `gate_only`.
  Claimable frontiers remain 0, three waves and seven scheduled slices are
  unchanged. Candidate digest is
  `sha256:3fddb5989126a0819ddf1a59c4b1d3589cdd61a723a58c73dd32d79322adc270`;
  acceptance evidence is
  `docs/plans/evidence/abc-round6-operator-deferral-projection-v2-rev340-acceptance.json`,
  raw blob `991d3c58849eb5b93668d01fb053fed5ac4c9b71`.
- **Verification:** focused `1 passed`, Integration module `144 passed`, and
  the complete parallel module `18 passed` plus exactly one registered
  pre-existing unresolved-dependency-message failure. Integration, Round6 and
  Guidance validators pass; current Linux readback reports 28/28, 0 claimable,
  three waves and Guidance 140/140. A later Windows-Python rerun could not
  start because WSL interop failed before test startup; this is not a test
  failure. The independent reviewer found no candidate defect.
- **Current control plane (Rev341):** Open Work and Round7 external claim
  projections are byte-equivalent, current active repo claims are empty, the
  old USI claim is released, and no live action is authorized. The exact next
  product precursor is one materially fresh PPS-1 source-kind architecture
  against the verified Rev334 clean origin. Require independent Sol
  architecture PASS before registering any tests-first PPS product claim.
- **PPS-1 v6 architecture freeze (Rev342):** the materially fresh source-kind
  packet is frozen at
  `docs/plans/abc-pps1-skill-import-compatibility-v6-source-kind-architecture.json`,
  raw blob `3847d24635ebe9457fc1c971b6e4614d6dcee28b`. All five Rev334 clean-origin
  blobs and the absence of `tests/test_skill_format.py` match exactly using
  `git hash-object --no-filters`. The packet preserves distinct repository-root,
  tree-directory, blob-file and raw-file identities, omits skills.sh provenance
  without a second request, and requires complete in-memory bundle validation
  before any write. One bounded fresh Sol architecture reviewer did not return
  a terminal verdict; no defect was reported. No product claim exists. Next
  action is one review-only gate on the unchanged packet and clean-origin tuple;
  only terminal PASS permits an exact six-path tests-first claim. Freeze evidence
  is `docs/plans/evidence/abc-pps1-rev342-v6-source-kind-architecture-freeze.json`,
  raw blob `39f5645cd2834af5c3bf9281f36dc3f38dc6bcb5`.
- **PPS-1 v6 correction and active product claim (Rev343-345):** independent
  Sol initially rejected the unchanged-route provenance handoff because `_src`
  is discarded. Correction 1/1 now permits strict pure no-I/O reclassification
  of direct GitHub URLs in storage and host-only skills.sh recognition with no
  provenance or second fetch. The corrected architecture blob
  `cc3ec2056b3681eca4b6813e6e0c90d13b436ad9` received terminal PASS.
  Exact tests-first claim `ABC-PPS1-V6-REV345` was registered for only the six
  clean-origin product/test paths, but its worker changed no claimed path and
  returned no blocker after bounded checkpoints. Rev346 therefore released the
  lease with no candidate and no product change. All clean-origin blobs still
  match and `tests/test_skill_format.py` remains absent. Release evidence is
  `docs/plans/evidence/abc-pps1-rev346-v6-worker-channel-release.json`, raw blob
  `94664ce2ba00b01e839f2cfc179b4bd0a305bdea`. Next register one fresh transfer
  claim for a replacement critical Sol writer. No seventh path, route edit,
  stage, commit, push, publication, deployment, provider mutation or live
  action is authorized.
- **Known pre-existing control-plane debt:** one parallel-roadmap test expects
  an unresolved-dependency diagnostic that the current validator does not emit;
  one separate Guidance test still hardcodes 139 while current Guidance
  correctly discovers/lists 140. Neither was changed by the accepted Round6
  slice. Maintenance/security harness work remains deferred until after
  release and must not be used to absorb these fixes before reprioritization.
- **Commit/publication state:** no stage, commit, push, deployment, publication
  or live action was performed. All five accepted candidate files differ
  materially from HEAD through preserved foreign prerequisites; staging whole
  files would create a mixed commit. The index remains empty. Commit only after
  a clean-origin integration route can include the exact prerequisites without
  absorbing unrelated work.

## Steering update: 2026-08-22

This update supersedes the older operator-decision rows below, but does not
revive any expired claim or grant a commit, push, deployment, provider, host,
private-data, UI-live, or other external action.

- **AMH / maintenance and security harness:** deferred until after release.
  Keep every AMH slice unclaimed; a later post-release reprioritization still
  needs fresh architecture, collision checks, claim and evidence.
- **Lens Code Graph:** approved as a product feature. The remaining prerequisite
  is one named Frontpage/Lens UI owner plus the bounded graph API; no UI claim or
  real-project placement is authorized yet.
- **Live Contextual Layer:** deferred until the remaining system is complete
  enough for system testing. Do not claim LCL-01 or later LCL work; re-enter via
  a fresh architecture and claim at that time.
- **Audit caveat:** the generic safe-queue audit still lists the frozen
  `abc-lcl01-...-v5-architecture.json` target because that historical packet
  contains a `repo_only` target class. Treat it as read-only architecture
  evidence only: the newer Open Work decision log, LCL source roadmap and
  guidance defer LCL; the packet does not create a claim or writer authority.
- **PPS:** `GO PPS repo-only implementation` received. Run only fresh,
  read-only preflight first; then claim one dependency-ready, disjoint
  safe-offline/repo-only PPS slice. All live/UI/publication actions remain off.
- **PPS-1 active claim:** `ABC-PPS1-REV305` is registered tests-first through
  `2026-08-23T00:53:26+02:00` for exactly
  `services/memory/{skill_format.py,skill_importer.py,skills.py}` and
  `tests/{test_skill_importer.py,test_skill_format.py,test_skills_manager_owner_isolation.py}`.
  Root froze `PPS-1-SKILL-IMPORT-COMPATIBILITY-SOL-ARCH-v1`; registration
  evidence is `docs/plans/evidence/abc-pps1-rev305-registration.json`.
  The fresh `gpt-5.6-sol` writer must add tests first, then implement only the
  six paths. Stop on baseline/pin/claim drift, a seventh path, weaker import
  limits, script execution, publication or any external/live effect. Verify
  the focused importer/format/owner suites, hostile path/budget cases,
  `tests/test_workflow_skills.py`, static/diff checks and an independent fresh
  Deep-Sol review before acceptance. Candidate state is `not_frozen`; no
  commit, push, deploy, publication or live authority exists.
- **PPS-1 close-of-session override:** Revision 307 releases the claim without
  product acceptance. The exact Rev306 six-blob candidate remains preserved
  and unchanged in `docs/plans/evidence/abc-pps1-rev306-freeze.json` (blob
  `8fdcef7bbb1187eb36d6e622cb7c36f0cd8cdd6a`). Root reproduced `54 passed`
  focused and `16 passed` dependency checks, but the initial independent
  Deep-Sol reviewer and its one replacement both failed to return a terminal
  verdict after one bounded nudge. Release evidence is
  `docs/plans/evidence/abc-pps1-rev307-review-channel-release.json`, blob
  `ded845bcbc07d525539fc76c9d3798ec0d6a13ad`.
  Next action is one fresh review-only claim on the unchanged Rev306 tuple;
  any candidate or pin drift after the recorded Rev307 release transition
  invalidates the freeze and requires fresh PPS-1 preflight. Do not commit,
  publish or call PPS-1 accepted before that review.
- **PPS-1 terminal Rev309 override:** the fresh Rev308 independent Deep-Sol
  review matched all six raw candidate blobs and all read-only pins, then
  rejected the candidate. Distinct keys such as `plugin/SKILL.md` and
  `plugin\\SKILL.md` normalize to one path and silently overwrite by mapping
  order; Windows reserved names, ADS colons and trailing dot/space segments
  also remain open. This recurs in the declared untrusted bundle-boundary
  class after correction 1/1. Product acceptance is false, the claim is
  released, and no commit is allowed. Terminal evidence is
  `docs/plans/evidence/abc-pps1-rev309-terminal-rejection.json`, blob
  `602b6b73118b15ec74cb4b2a3ab6c748d298d839`. Next action is
  a fresh compact Root/Sol architecture plus fresh baselines and claim; never
  patch, stage, commit or publish the Rev306 candidate.
- **PPS-1 v2 recovery architecture:**
  `docs/plans/abc-pps1-skill-import-compatibility-v2-architecture.json`
  (raw Git blob `0ebaaaafebd1ed39057383f843006a4c6256c158`) supersedes v1 and is
  frozen only for recovery. Before any new claim, restore exactly the six PPS
  writer paths to the original raw baselines recorded in that packet, verify
  the five blobs plus absence of `tests/test_skill_format.py`, and reproduce
  the 15-test baseline. Then register one fresh tests-first critical Sol claim.
  The v2 boundary rejects normalized/case-fold aliases, slash/backslash
  collisions, Windows ADS colons, trailing dot/space aliases and DOS device
  names before any write. No claim, candidate, commit, publication or live
  authority currently exists.
- **PPS-1 v2 recovery gate passed:** all five restored raw blobs match the
  architecture packet byte-for-byte, `tests/test_skill_format.py` is absent,
  and the clean baseline reproduced `15 passed, 3 warnings`. Recovery evidence
  is `docs/plans/evidence/abc-pps1-rev310-v2-recovery.json`, raw Git blob
  `e7465154b5e3ac64bdcee8a1dd42f45faf7e6cb0`. The rejected Rev306 candidate is
  no longer present in the six PPS writer paths. No claim exists yet; the only
  next writer action is one fresh tests-first critical Sol claim bound to the
  v2 architecture and these restored baselines.
- **PPS-1 v2 Rev311 active claim:** `ABC-PPS1-V2-REV311` is registered at
  `docs/plans/evidence/abc-pps1-rev311-v2-claim-registration.json`, raw Git
  blob `f553ea42524360a0451f6b71e4c2cb4cd11ea248`, for fresh Sol worker
  `01a02dc8-906b-79f1-b095-4730b996aa0f`. The claim owns exactly the six PPS
  product/test paths, is bound to Open Work raw blob
  `1f6464e793f5bff3290813db15fd187ad67ee425`, Round7 raw blob
  `1914f6a7796b773f1ce2a3016bb64d580e1a2a15` and architecture raw blob
  `0ebaaaafebd1ed39057383f843006a4c6256c158`, and expires at
  `2026-08-23T12:42:17+02:00`. Candidate state is `not_frozen`; no commit,
  push, deployment, publication or live authority exists.
- **PPS-1 v2 Rev314 frozen candidate:** the fresh Sol worker returned exactly
  six claimed paths after valid tests-first RED (`45` intended nodes), `61`
  focused, `16` dependency and `18` hostile-subset passes. Root re-read the
  complete diff and reproduced `77 passed, 4 warnings`. Exact candidate blobs,
  pins and dirty-diff digest are frozen in
  `docs/plans/evidence/abc-pps1-rev314-v2-freeze.json`, raw Git blob
  `55c48b2f4595cca7579d7ec5c472238615e47ca2`. Writer edits are closed. PPS-1
  remains unaccepted until one independent fresh Deep-Sol review passes on
  this exact tuple; any candidate or authority drift invalidates that review.
- **PPS-2 Rev312 active disjoint claim:** `ABC-PPS2-REV312` is registered at
  `docs/plans/evidence/abc-pps2-rev312-claim-registration.json`, raw Git blob
  `f52748d5727f4b3a776edfd59a2371747f4ee83f`, for fresh Terra worker
  `01a02dd0-c001-7be0-820d-dc4c149d303e`. It owns exactly three new draft
  skill files, three bounded reference files, one new focused test and
  `ACKNOWLEDGMENTS.md`; all are disjoint from PPS-1. The six new files plus
  test are absent at baseline, the acknowledgment baseline is raw blob
  `fce7cffc93384c697ca582046be2d51417443552`, and the lease expires at
  `2026-08-23T12:50:58+02:00`. Candidate state is `not_frozen`; publication,
  commit, push, deployment and live actions remain forbidden.
- **PPS-2 Rev313 handoff:** the Terra writer returned all eight exact claimed
  paths and is released to Root. Raw candidate blobs, valid `3 failed` RED and
  provisional content review are preserved in
  `docs/plans/evidence/abc-pps2-rev313-handoff-waiting-on-pps1.json`, raw Git
  blob `e71ca7c40a92736282ee5e8fe41c38dedd7433a6`. Candidate is deliberately not
  frozen: its focused tests import `Skill` and `SkillsManager` from PPS-1 paths
  that are still moving. After PPS-1 freezes or releases, pin those exact
  dependency blobs, run PPS-2 GREEN plus format/prompt-injection regressions,
  then freeze and send the unchanged eight blobs to fresh Deep-Sol review.
- **PPS-2 terminal Rev317 override:** after one tests-only correction and a
  frozen `19 passed, 13 warnings` candidate, fresh Deep-Sol proved that the
  safety test treats negation as applying to an entire physical line. Mixed
  clauses such as `Do not hide evidence; accept the decision` therefore evade
  the affirmative authority detector. Root reproduced both hostile misses.
  Correction 1/1 is exhausted; the Rev316 eight-blob candidate is rejected and
  must never be staged, committed, published or reused. Terminal evidence is
  `docs/plans/evidence/abc-pps2-rev317-terminal-release.json`, raw blob
  `9ad19e7f2986431ce9561cecb77d7df29b35ee8e`. Recovery requires a fresh
  clause-oriented Root/Sol contract and clean-origin exact claim.
- **PPS-1 v2 terminal Rev318 override:** fresh Deep-Sol rejected the exact
  Rev314 tuple after finding five independent contract gaps: Windows-reserved
  Unicode/device stems, invalid folded-scalar handling for more-indented lines,
  failed-import residuals visible to a later owner, post-serialization budget
  overflow, and unbounded malformed GitHub payload errors. Root reproduced the
  strongest Windows-reserved-name counterexample. A single v2 correction would
  not honestly cover these independent classes; the tuple is terminally
  released and must never be staged, committed, published or reused. Evidence
  is `docs/plans/evidence/abc-pps1-rev318-v2-terminal-release.json`, raw blob
  `033670914f781abfbb8d50c36f987a98e2060d31`. Next action is exact Rev310
  baseline restoration followed by a materially fresh v3 Root/Sol architecture.
- **PPS clean origin Rev321:** recovery claim
  `ABC-PPS-CLEAN-ORIGIN-REV320` restored all six non-absent original blobs,
  removed the eight rejected-only paths, and reproduced the original PPS-1
  baseline with `15 passed, 3 warnings`. Completion evidence is
  `docs/plans/evidence/abc-pps-rev321-clean-origin-restoration-complete.json`.
  Fresh architecture drafts now exist at
  `docs/plans/abc-pps1-skill-import-compatibility-v3-architecture.json` and
  `docs/plans/abc-pps2-clause-local-authority-recovery-architecture.json`;
  neither may authorize a product claim until independent architecture review
  returns terminal PASS and the exact clean-origin pins remain unchanged.
- **PPS-1 v4 Rev324 active claim:** independent fresh Sol review passed the
  materially fresh v3 contract plus its v4 generated-destination correction.
  Acceptance evidence is
  `docs/plans/evidence/abc-pps1-rev323-v4-architecture-acceptance.json`, raw
  blob `fc77037ff6d92436e5121f69aeee8f3fd8752377`. Exact six-path tests-first
  claim `ABC-PPS1-V4-REV324` is registered in
  `docs/plans/evidence/abc-pps1-rev324-v4-claim-registration.json`, raw blob
  `0a88294c543dd236e9239c35c19bfb807cbcefef`, through
  `2026-08-23T14:44:03+02:00`. Fresh critical Sol worker
  `01a02e35-fe47-77d2-ab07-cf513f92350a` owns only the recorded six paths.
  Candidate state is `not_frozen`; no stage, commit, publication, push,
  deployment or live authority exists.
- **PPS-2 v3 Rev326 active claim:** independent fresh Sol review passed the
  clause-local authority-recovery architecture, including exact inflections,
  direct clause negation and a durable Kimchi attribution pin. Acceptance
  evidence is `docs/plans/evidence/abc-pps2-rev325-v3-architecture-acceptance.json`,
  raw blob `f76be7b77e1b54591a37ff1f033b1e516bcddf84`. Exact eight-path tests-first
  claim `ABC-PPS2-V3-REV326` is registered in
  `docs/plans/evidence/abc-pps2-rev326-v3-claim-registration.json`, raw blob
  `2ba95735b2eb8c035f37f60989019e9af3cb1acd`, through
  `2026-08-23T14:49:29+02:00`. Fresh Terra worker
  `01a02e3b-a88d-71b0-90b6-51ba9000c638` owns only the recorded eight paths.
  Root independently completed the interrupted prospective preflight: all new
  paths are absent, all source and acknowledgment baselines match, the scope is
  disjoint from PPS-1, and path-scoped status is empty. Candidate state is
  `not_frozen`; no stage, commit, publication, push, deployment, provider or
  live authority exists.
- **PPS-2 v3 Rev327 dependency handoff override:** the Terra writer recorded
  valid RED, produced eight materially fresh blobs and passed its focused suite
  with `4 passed, 1 warning`. PPS-1 then legitimately changed all three
  read-only runtime dependency blobs. Root `$reflect` first distinguished a
  harmless raw/filtered Git hash mismatch from the later actual raw drift, then
  released the PPS-2 claim once instead of repeating verification against a
  moving dependency. The exact unfrozen candidate and manifest are preserved in
  `docs/plans/evidence/abc-pps2-rev327-v3-handoff-waiting-on-pps1.json`, raw
  blob `ac27e538fe5dc4000adaabfb1f6b24c4b9b775aa`. No candidate path may change.
  After PPS-1 freezes or is accepted, register one fresh verification-and-freeze
  claim bound to the exact runtime blobs, reproduce focused/dependency/hostile
  checks, then freeze for independent fresh Sol review. No acceptance, stage,
  commit, publication, push, deployment, provider or live authority exists.
- **PPS-4 Rev328 architecture precursor:** independent fresh Sol rejected the
  exact v1 architecture blob `0eb8a03bf63be85f82762604f82877d30d520595`.
  It left schema/Python partition, empty-evidence `holds`, hostile containers,
  executable unsafe-content grammars, disjoint evidence refs and deterministic
  JSON/test-first semantics materially open. Root `$reflect` deferred the one
  correction because PPS-4 still depends on accepted PPS-2 and a replacement
  must pin that final candidate. Release evidence is
  `docs/plans/evidence/abc-pps4-rev328-v1-architecture-release.json`, raw blob
  `7dcbe1da6d24be076e5df0aa03063fa14e10c259`. V1 is rejected evidence only;
  no PPS-4 product claim, edit, stage, commit or live authority exists.
- **PPS-1 v4 Rev329 immutable verification/freeze claim:** the Rev324 writer
  completed valid RED and returned an exact six-blob candidate after `113`
  focused, `19` dependency, `98` hostile-subset and `3,624` generated PyYAML
  parity checks. Its final handoff correctly stopped because Root's descriptive
  Rev327/328 router updates changed the old pins. Root `$reflect` found no
  candidate failure and independently reproduced `113` focused, `18`
  workflow/tag and `3` prompt-injection passes. New verification-only claim
  `ABC-PPS1-V4-FREEZE-REV329` is registered at
  `docs/plans/evidence/abc-pps1-rev329-v4-verification-freeze-registration.json`,
  raw blob `b16c98da3d758a183c5725b36540b0ca58252cb9`, through
  `2026-08-23T15:19:27+02:00`. It pins Open Work
  `d3fdc9c850d797d0de5ed94a7a2fa54e394fec23` and Round7
  `68d18a5e3eda49c8138900502a1acc70c10373da`; all six candidate blobs are
  immutable. Only readback, test reruns and Root freeze evidence are allowed;
  no candidate edit, stage, commit, publication, push, deployment or live
  authority exists.
- **PPS-1 Rev331 review failure and one correction route:** independent fresh
  Sol rejected Rev330 because a file `a` plus child `a/b.txt` is not detected as
  an ancestor-prefix collision before staging; the candidate leaks an unbounded
  `FileExistsError` only after its first write. Root reproduced that class and
  separately proved with a synthetic marker that userinfo from the original
  GitHub URL is persisted verbatim in the generated source note. Evidence is
  `docs/plans/evidence/abc-pps1-rev331-v4-review-failure-correction-route.json`,
  raw blob `4cd82425e833f9fe109a3ecbc3f1e314a0000c81`. Rev330 is rejected and cannot
  be accepted or committed. One final bounded correction architecture exists
  at `docs/plans/abc-pps1-skill-import-compatibility-v5-architecture.json`, raw
  blob `db14de49a59585712ea42b910c2b21f939572e69`, covering only complete
  pre-write prefix validation and credential-free deterministic provenance.
  It is in independent architecture review; no correction claim or candidate
  edit is authorized. Any subsequent candidate-review failure terminally
  releases PPS-1 to clean-origin redesign.
- **PPS-1 Rev332 terminal architecture release:** independent fresh Sol rejected
  v5 because repository-root `https://github.com/o/r` and tree-root
  `https://github.com/o/r/tree/main` collapse to the same current
  `ResolvedSource(owner=o, repo=r, ref=main, path="")`, while the frozen
  contract requires different canonical provenance. The correction therefore
  is not unambiguously implementable. Terminal evidence is
  `docs/plans/evidence/abc-pps1-rev332-v5-architecture-terminal-release.json`,
  raw blob `c7027d0a0138f045c21c2e68028dd067b8c0710c`. No active PPS claim exists.
  Never patch or reuse Rev330/v5. Next register one exact six-path recovery
  claim, restore Rev321 clean origins, reproduce the `15`-test baseline, then
  require a materially fresh source-kind-aware architecture and independent
  PASS before any product claim. Keep PPS-2 unchanged and unfrozen; do not
  retry PPS-4 before PPS-2 acceptance.
- **PPS-1 Rev333 exact recovery claim:** `ABC-PPS1-CLEAN-ORIGIN-REV333`
  belongs to fresh Luna worker `01a02e7f-9f0d-78f0-8715-dbef9a986635`
  through `2026-08-23T14:47:18+02:00` and owns only the six PPS-1 runtime/test
  paths. It may restore the five Rev321 blobs and remove
  `tests/test_skill_format.py`; it may not implement product behavior or touch
  a seventh path. Registration evidence is
  `docs/plans/evidence/abc-pps1-rev333-clean-origin-recovery-registration.json`.
  Root must read back all six targets and reproduce the original `15`-node
  baseline before releasing recovery. No stage, commit, publication, push,
  deployment or live authority exists.
- **PPS-1 Rev334 recovery complete:** Root stopped the mechanical worker after
  its text patch produced non-exact intermediate bytes, reconstructed the five
  files from the preserved Git objects, restored the recorded CRLF bytes and
  removed `tests/test_skill_format.py`. All six targets now match Rev321 exactly;
  the original baseline reproduced `15 passed, 3 warnings`. Completion evidence
  is `docs/plans/evidence/abc-pps1-rev334-clean-origin-recovery-complete.json`,
  raw blob `cb9ae3964555dc28ae9ad8382f21fedfb374353a`. The recovery claim is released
  and no PPS claim is active. Next action is one materially fresh architecture
  with explicit source-kind semantics, independent Sol PASS, then a new exact
  tests-first claim. Rev330/v5 remain terminal evidence only.
- **Current 12h run:** `ABC-OPEN-WORK-GO-20260823T1028+0200` targets
  `2026-08-23T22:28:48+02:00`, max three disjoint product workers, repo-only by
  default. The existing heartbeat id is
  `odysseus-open-work-continuous-abc-run`, but both a read and one bounded
  update attempt timed out in the Codex app on 2026-08-23; no duplicate
  scheduler was created and ACTIVE status is not verified. Continue current
  safe claims normally. If the task resumes without an active lease, reconcile
  exact blobs and claim expiry before dispatch; never infer live authority.

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
- Authority/control-plane base HEAD: `01c0ee45`. Revision 304 is materialized
  and centrally valid in the worktree but not committed because the platform
  currently denies the required `.git` write after exhausting its approval
  usage budget. The validated unstaged Rev304 transaction registers exactly
  one PRE1A claim and lease; it must be committed intact when Git metadata
  writes become available. Do not reconstruct or restage it from prose.
- Latest AMH-01 lifecycle commits: Rev299 terminal router `c9d6f601`, terminal
  Evidence `2e68ebf7`, Rev298 claim registration `06f65fb4`, registration
  Evidence `7e91d2e5`, and closed materialized-genesis architecture `07715e94`.
- Latest committed continuity artifacts before this Master Handoff update:
  PRE1A Rev303 pin rebind `5a7c3821`, PRE1A architecture and Rev303 router
  projection `b8fb58e0`, blocked USI-07A architecture plus Guidance inventory
  registration `43859d4b`, Rev302 handoff refresh `a1dc427e`,
  H1-29 Rev297 terminal-router commit `b9d8012c`, terminal-Evidence commit
  `38b612b8`, Rev296 freeze-router commit `997edad7`, freeze-Evidence commit
  `09b821cb`, Rev295 registration commit
  `5f70e03eed20beeec14e8d1884e4fc6c456f6859`, registration-Evidence commit
  `4c4216f7714ce400252611113ee5fcc4badfcd50`, closed architecture commit
  `66fbab3e04fe2fc394525f0f978cbda9d02bb01a`, AHE-GHISS01 terminal-release
  Evidence and continuation-inventory commit
  `4702a57f`, AHE-GHISS01 architecture commit
  `79e52bbcf92dd6860cc1334e8926b9b0a8e52c60`, Rev292 terminal-release
  Evidence commit `4efda592376080e8afd11709ab38e04db29a5ba5`, Rev291 native-execution
  Evidence commit `aa90d35a6e5009ba1fd2c525bcae240ff4d8fe28`, context-free inventory commit
  `e8292e77388bab06389f18e6d31bbbfcc5b06d0c` and HWA2A-v5 architecture
  commit `59c5f76aa2532ec120e91cd111480f4a690ed068`; their predecessor is
  `d15cdd7b1cf01a5be1ced846f26abb9fe9404cf7`. Never use a handoff commit
  itself as product authority; use the exact accepted claim tuples instead.
- Diagnostic state at the Rev303 handoff audit remains intentionally dirty;
  re-read the exact status and path-scoped diff at every
  resume; it is evidence, not a stable invariant. Exact path-scoped claims
  remain authoritative.
- Planning subtree: 0 staged entries, 17 tracked-modified entries, and 126
  untracked entries. The central authorities
  are intentionally modified; the terminal R7-A2 packet and freeze Evidence
  are untracked but exactly hash-pinned by durable authority. Untrackedness
  alone does not invalidate a registered artifact.
- Rule: never clean, reset, broadly stage, or absorb this tree. Select exact
  accepted tuples only.

### Preserved dirty auxiliary worktrees

- `Documents/odysseus-abc-workspaces/AHE-GHISS01-REV293`: four modified
  rejected candidate paths plus test-temporary remnants. Preserve for forensic
  recovery only; do not clean, merge, publish, or resume without fresh
  architecture and claim.
- `Documents/odysseus-release-dev`: HEAD `30fdce90`, two foreign roadmap
  modifications. Keep isolated from the clean accepted/public release clones.
- `/tmp/odysseus-hwa2a-v5-rev290`: one modified rejected HWA test path. Preserve
  only while the local session exists; it is not accepted product evidence.
- These entries are descriptive locators relative to their workspace roots,
  not claim or cleanup authority. Re-read `git worktree list --porcelain` and
  each status before any consolidation.

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

5. No exact action-specific push phrase is present in the current authority
   readback. Keep the reviewed public projection local and do not push or
   deploy until a matching exact authority is durably supplied. The single
   immediate payload is: `GO: push exactly
   ea181f52e72a24d91480757c55a298fbb77249b1 to fuzzy/dev.` This line records
   the required approval payload; it is not itself authority.

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
- The configured SSH identity metadata exists in the private read-only archive,
  but its referenced local identity file was absent at the latest redacted
  audit. No SSH or server contact was attempted. Access repair, backup proof,
  deployment and live smoke therefore remain later action-specific steps even
  after a successful push and Required CI result.
- No exact action-specific push phrase is present in the current authority
  readback. Push therefore remains unauthorized; no workaround or alternate
  remote is permitted.
- The repository-owned redacted Debian probe failed before connection because
  the canonical configured SSH identity file is absent. Private archived
  configuration may be used only to restore the approved access route through
  an explicit secret-safe migration; do not print, copy ad hoc, hash, or
  substitute private key material. No server read or write occurred.
- Push and deployment remain unauthorized. Preserve the reviewed release
  payload and continue repo-only documentation or verification; do not deploy
  or call any live target from this handoff.

## 6. Global roadmap state

- Open Work and Round 7 are at authority Revision 304.
- The canonical denominator is verified and published: 173 accepted of 245
  unique units, leaving 72 open (19 repo, 19 design, 30 live, 4 operator).
- The published snapshot binds all 30 active source-roadmap blobs, the exact
  alias/exclusion rules, the current authority tuple, and the independently
  reviewed deterministic candidate. Do not recompute or replace the
  denominator from stale prose; re-open denominator work only if a current
  source or authority byte changes and the routers explicitly invalidate it.
- Exactly one active claim and one lease exist at Revision 304:
  `ABC-USI07A-PRE1A-REV304`, owner
  `Sol PRE1A critical lifecycle tests-first writer`, thread
  `01a00419-85fb-71b3-85f1-cbdf6b0eb611`, lease through
  `2026-08-15T23:50:00+02:00`. Its exact six writer/hotfile paths are the
  three CBM process/client/query source modules and their three matching test
  modules. Candidate state is `not_frozen`; no candidate blobs, publication
  or live authority exist yet. The earlier terminal AHE
  claim `ABC-AHE-GHISS01-RECOVERY-REV302` is evidence only; it grants no writer,
  product, stage, commit, push, deploy, provider, host, UI, or live authority.
  Terminal Evidence is
  `docs/plans/evidence/abc-ahe-ghiss01-recovery-rev302-terminal-release.json`,
  raw blob `d0e216ff5393d90cee275e1eb6feeda66a1ffcb4`, state
  `released_blocked`, correction `1/1`, and product acceptance is false.
  The frozen tuple is
  `{src/agent_loop.py:36c47d2c1ce0d2414cefb47903ec92a6b72c5511,
  src/claim_evidence_gate.py:77c45db5d47d7a2ad2f55841c3072ee99de3d2b3,
  src/tool_domains/github_issues.py:db9e1a1df5a8b43019842a7e15ff0fbc43ddbdc6,
  tests/test_claim_evidence_gate.py:e42e36220f8d607a822cb2fbb4e6f5e0fd94994b}`
  with candidate-set digest
  `sha256:b90b8635ceba1c206e57be78b08b299b2e0b3cd722ffc4a9f8470bac3411bcda`.
  The corrected tuple is
  `{src/agent_loop.py:36c47d2c1ce0d2414cefb47903ec92a6b72c5511,
  src/claim_evidence_gate.py:0ae4198c3110e3aca14fe67e296cc8fd5b6210ca,
  src/tool_domains/github_issues.py:db9e1a1df5a8b43019842a7e15ff0fbc43ddbdc6,
  tests/test_claim_evidence_gate.py:f187c2628a02f7525baa9ea0abf0141846229ff1}`
  with candidate-set digest
  `sha256:d6146654c166679bcf1e6f4f2e182df34d7cf97b5a6f655ee5600fa64e13728f`
  and corrected diff digest
  `sha256:8df08259ccc0868bd335ae72092b1a1f0e98480bed23bbce9c8a516b7c920023`.
  Independent QA and Deep-Sol found that the frozen grammar still supported a
  bare contracted contradiction after an exact positive clause; the sole
  correction misspelled the contraction as `did['â€™]t`, so the same class
  remained RED. Preserve both tuples only at the preservation-only locator
  `../Documents/odysseus-preserved-ahe-main-prefreeze-rev302`.
- The non-denominator `ABC-TTD07A6-REV301` claim remains terminally
  `released_blocked` after correction 1/1. Terminal Evidence is
  `docs/plans/evidence/abc-ttd07a6-rev301-terminal-release.json`, blob
  `6c98365320916e382e8c24b4d860647c91a32793`; both frozen and corrected
  five-path tuples remain rejected and have no product acceptance or commit.
- `ABC-R7-PYRUN01-REV300` is terminally `released_blocked` at Revision 300.
  Its frozen source/test blobs are
  `b997cb1203e752d41721fc1aef442ac15f579bf4` and
  `33646ace370944bf53e21d2e7479483ab0f85073`. Five exact nodes, the full
  144-test Integration module, pycompile, diff-check and all three central
  validators passed; the declared three-file dependency tier ended 189 PASS / 1
  FAIL because a stale unresolved-dependency assertion lives in the unowned,
  pinned `tests/test_parallel_roadmap_execution.py`. Fixing it requires a
  forbidden third writer path. No product was accepted, correction remains
  0/1, and terminal Evidence is
  `docs/plans/evidence/abc-r7-pyrun01-rev300-terminal-release.json`, blob
  `300b7518286be2f71ce4cf41025f06bdfddea791`. Do not publish or resume this
  frozen pair without fresh architecture and scope.
  Before this active claim, Revision 299 had zero active claims and leases.
  `ABC-AMH01-REV298` was verification-first and released blocked before freeze.
  Its four materialized policy baselines remained byte-identical and no writer
  path changed. The exact policy suite passes 5/5 through the existing Linux
  Python 3.11 environment, JSON/pycompile/diff-check and all three central
  validators pass, but the persisted Windows pytest process did not start from
  the WSL context and the pinned broader dependency lane produced 50 PASS / 1
  FAIL in the foreign current Round6 validator test. Candidate bytes remained
  `{}`, correction stayed 0/1, and no independent acceptance QA was claimed.
  Terminal Evidence is
  `docs/plans/evidence/abc-amh01-rev299-terminal-release.json`, blob
  `487f90171ac6c884cb84616ad833d9883f0f6eea`. Do not call AMH-01 accepted;
  repair/reselect its executable command/dependency contract through fresh
  authority before another verification claim.
  `ABC-H1-29-REV295` was terminally released blocked; its former scope was only
  `src/harbor_planning_projection.py` and
  `tests/test_harbor_planning_projection.py`, both absent at registration.
  Revision 296 freezes the exact candidate blobs
  `7aa96988a1860a3d95f93f273e041c10e0d70661` and
  `ea6bdb3b3f6b5fff7533b48d45ab18af09aad545`, candidate-set digest
  `sha256:0109cde0cf5af7c323f9d32ea52b4b830ab4164049f93d83b592d190353fcc1d`,
  command digest `sha256:44e93df3436fdf409bb4638a6bbef90fa02da826b252b5e7403fc945f1ffcdd2`
  and node digest `sha256:91c7f6cce23646cadf2fde52ef75503b1585eca03e23519d02e2317fd6b85f02`.
  Tests-first RED was exact 8/8 due only to the absent module. Focused is 8/8,
  dependencies 311/311, pycompile and diff-check pass. The one correction was
  the Planning-JSON list-versus-execution-tuple boundary; no same-class
  recurrence followed during writer verification. Final fresh Deep-Sol then
  found a same-class recurrence: `advisory_refs[*].kind` is compared with `!=`
  before exact built-in string validation, allowing a hostile `str` subclass
  to execute `__ne__` callback code. Correction 1/1 was exhausted, so Revision
  297 rejected the frozen pair and removed the claim and lease. Terminal
  Evidence is `docs/plans/evidence/abc-h1-29-rev297-terminal-release.json`, blob
  `360c38d84cbfadb320a3e476a1a4fa1c2093b050`. Never patch or publish the pair.
  `ABC-HWA2A-V5-REV290` is terminally `released_blocked` before freeze; no
  candidate bytes were accepted. The rejected identity records source
  `f2b3e80e611fd64447b2829f5af77d36317a83d0` and test
  `f2aff64152488f26d9a5ab0a650819fb30041161`, with dirty-diff SHA-256
  `3c19a8bf7f72d6d1868d220354fdc8f5479c9defc27a7098e5dfdb7840d1f6ce`.
  The source blob is not present in the canonical Git object store and no
  current worktree retains that exact pair; this is terminal identity evidence,
  not a recoverable candidate. Never claim local recoverability or revive it.
  Its exact tests-first baseline produced 12/12 RED solely because the loader
  API was absent. The first source attempt produced 9 PASS / 3 FAIL; the single
  correction was consumed without a complete GREEN. Independent Deep-Sol
  classified the strongest failure as an architecture/test contradiction:
  SQLite INTEGER affinity makes bound TEXT `"1"`, REAL `1.0`, and canonical
  INTEGER `1` persist identically, so their unpersisted origins cannot be
  distinguished without the forbidden schema/history expansion. The other
  two failures are test defects: a reused clock creates an expired fixture and
  one generated evidence identifier contains non-hexadecimal `g`. Dependency,
  central and independent-QA product lanes did not run after the mandatory
  stop. Terminal Evidence is
  `docs/plans/evidence/abc-hwa2a-v5-rev292-terminal-release.json`, blob
  `7f72ea51e5bcf6019b7cd4c026db7be53d1d83fe`. The private native checkout
  locator remains only in structured control-plane history and must not be
  copied into public artifacts. Any future HWA2A attempt needs materially
  fresh architecture and clean accepted baselines; never patch or promote the
  v5 candidate. `ABC-HWA2A-V4-REV287` was released
  blocked before freeze at Revision 289 with candidate `{}`, no product write,
  and correction 0/1. The clean baselines
  `7ee17276551b09336d2166b058a67c2dbcb87d18` and
  `7558562705f6dfc648bcc287985b240964aa7d51` contain neither the claimed
  existing `CommitAuthoritySnapshot`/`load_commit_authority` API nor the twelve
  claimed existing nodes, while v4 forbids adding them. Never resume v4.
  Registration Evidence remains
  `docs/plans/evidence/abc-hwa2a-v4-rev287-registration.json`, blob
  `0326c67bbd644d0144c2b1d8db1b0c28db9a67bb`, as terminal provenance only.
  `ABC-R7A2-R3R1-REV280` was
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
- Revision 304 raw `git hash-object --no-filters` readback:
  Open Work `239206f189f6d69324882bf76d323fc32592f6b1`, Round 6
  `284dbe55bf6efac90fc606c29ce40a437875a9f8`, Round 7
  `1914f6a7796b773f1ce2a3016bb64d580e1a2a15`, Guidance
  `84fad05c10dc93431e19b94502595e5282088d14`, AHE architecture packet
  `7164d0317f6bd2d280412af3513d9f52d21a9c44`, AHE registration Evidence
  `2f616d23fb60a8f9a745b44000edcd0d0e354916`, TTD terminal Evidence
  `6c98365320916e382e8c24b4d860647c91a32793`, HWA2A-v5 packet
  `03cb39a51f8212b6f0581d0254b38ed804e7b00b`, Rev292 terminal Evidence
  `7f72ea51e5bcf6019b7cd4c026db7be53d1d83fe`, R7-A2 packet
  `c4d2974f7cef38c636c960b5e641a26202cf4345`, and freeze Evidence
  `a86820f61d9a952a42f6082c382287cd11b212ad`. The terminal AHE Evidence is
  `d0e216ff5393d90cee275e1eb6feeda66a1ffcb4`. The blocked USI packet is
  `79ceedc73cd38c136b031d73f61fb21263d3a55d`; PRE1A is
  `6186172ed362e1bdafe2da469d81011f8fce2ba2`. Do not compare authority
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
- Two derived accepted clean baselines are explicit current dependency
  evidence, not writer claims. `HWA3A-TLR04-accepted-clean-baseline` binds
  `src/headless_write_agent_state.py` `7ee17276551b09336d2166b058a67c2dbcb87d18`
  and `tests/test_headless_write_agent_state.py`
  `7558562705f6dfc648bcc287985b240964aa7d51`.
  `AHE-GHISS01-accepted-clean-baseline` binds `src/agent_loop.py`
  `f05964f03c1280f51493300911cf0adbe434976d`,
  `src/claim_evidence_gate.py` `bc082142f8ac6dfe284f656f1392fd6219dca7b9`,
  `src/tool_domains/github_issues.py`
  `263ca48c8898133bed2515d85911938ba0fed68e`, and
  `tests/test_claim_evidence_gate.py`
  `978f3907161f819ef353a8a17dcd253cfb391c4a`. Their later failed candidates
  remain rejected and must not replace these clean baselines.
- The earlier Rev289 72-record continuation inventory at raw blob
  `b6ddc3b05263c7ab3b7d0ae0e87823a92755fa1f` established the first complete
  scalar-status, dependency, safe-action, stop-rule, verification-route and
  source-authority ledger. Its 144/144 locator and privacy checks remain useful
  historical evidence, but that blob is superseded by the enriched Rev302
  inventory identified in the canonical open-unit section below. Do not use
  the Rev289 blob for current continuation or claim decisions.
- The durable Rev302 terminal readback binds the exact frozen and corrected
  tuples, terminal Evidence, correction budget and no-product-acceptance state
  above. Neither tuple may be resumed, patched, frozen, accepted, staged,
  committed, pushed, deployed, or used as a product baseline.
- The committed USI-07A packet is architecture-only and independently
  `PASS_AS_BLOCKED_ONLY`: synchronous `provider.query()` plus a timeout check
  after return proves late-result discard, not interruption or terminal join.
  It grants no claim or product authority. PRE1A is separately committed and
  independently `PASS_AS_ARCHITECTURE_ONLY`; it freezes exactly six CBM
  process/client/query source/test paths and an internal start/interrupt-ack/
  terminate-kill/bounded-wait/join/terminal-readback lifecycle. The next
  permitted repo-only action is the now-active tests-first PRE1A
  implementation under `ABC-USI07A-PRE1A-REV304`. PRE1B immutable UIR
  context/provider admission may start only after PRE1A is released accepted;
  a fresh six-path USI-07A sufficiency architecture comes only after both.
- PRE1A packet details: path
  `docs/plans/abc-usi07a-pre1a-cancellable-cbm-process-provider-lifecycle-architecture.json`,
  raw blob `6186172ed362e1bdafe2da469d81011f8fce2ba2`, semantic digest
  `sha256:3418a839f88db251aace961d22272b4252b34fb46a1bd76081627bf2b96b97e9`.
  It freezes baselines `{process:c4bcc81cbd77f2d5922a9362e5b106c7af4853e8,
  client:a439767c1771f574090e00c30279803f66d3f2f5,
  query:52d8b7df3b54834bc23c7399890a5c7fa3b60466,
  process_test:25034e3d0183a4db7bdbf0dba294afb91beb2b4a,
  client_test:805618aa047afe930bc307dd647369d4d474cdbb,
  query_test:9f755bef9f3fb8c903150eed3043f23141018e6e}` and fifteen
  tests-first nodes. Implementation must preserve the synchronous legacy
  provider as fixture-only, allow one private handle, use one monotonic cleanup
  budget across terminate/kill/wait/join, publish payload at most once only
  after uninterrupted terminal join, and parse no bytes arriving after cancel.
  A seventh path or product registration is a hard stop, not scope expansion.
- `$reflect` after the terminal HWA2A-v5 release selected exactly one disjoint
  repo-only architecture precursor: `AHE-GHISS-01-local-duplicate-search-evidence`.
  Its independently reviewed packet is
  `docs/plans/abc-ahe-ghiss01-local-duplicate-search-evidence-architecture.json`,
  raw blob `7925944f25a5764158f233312ab9ed4c273272a0`, raw SHA-256
  `da37fc2830596f93ca75ed1a78d8f362d429c46e6517884ebcf1ce1895b44984`.
  It is 14,247 bytes,
  binds exactly four clean writer paths and six exact tests, has semantic seal
  digest `sha256:58ab90191f20fbafd60de505c4b8709b7dd409f2722fb42fd03a0d0489c1cc7f`,
  and grants no writer or live authority. It supports only a content-free local
  `duplicate_search` receipt with `persist=false`; GitHub create, field update,
  sync, provider access, token use and every live action remain unsupported.
  Independent Deep-Sol passed the exact blob, 4/4 canonical baselines, 4/4
  primary raw baselines, 15/15 read-only pins and all authority pins. A
  Root registered `ABC-AHE-GHISS01-REV293`, but Revision 294 terminally released
  it blocked and removed its claim/lease. Tests-first RED, exact six-node GREEN,
  focused 95, dependency 173, static and all central lanes passed. The first
  Full GREEN preceded a single-snapshot receipt/report correction, consuming
  correction 1/1. Root then reproduced the same-class counterexample
  `I checked no GitHub issue duplicates locally.` as `supported`: the grammar
  checks negation only before the completion verb. Candidate blobs
  `a0883f67b2ff3f98498d5d03316a7b4189f0f0ab`,
  `69b3f1f43e0cb8f153e99437ce8bed54408e2c8e`,
  `049ebfc51cc02743f8b9f07b8d00719eafe6702d`, and
  `a3b81508251e6e635060d1d6225078257ce3a4de` are materialized Git objects but
  rejected evidence only. Diff digest is
  `sha256:aef26a618f06e5267cbc0014da1b3130affeaec232b5d60261f507341d415792`;
  terminal Evidence is `841a71e215471c33823dde2f22d8bbb8b48d14cd`.
  Never patch or publish these bytes. A future attempt requires materially fresh
  architecture for whole-sentence negation and mixed-clause attribution.
- `$reflect` after AHE-GHISS01 selected H1-29. Its committed v4 architecture is
  `docs/plans/abc-h1-29-canonical-planning-projection-architecture.json`, raw
  blob `ba3ce9892c7bf5214b386bd7f45bd5798fb8c16e`, raw SHA-256
  `d7ffc572ebe67753620ae6f6817588bfe59be928a1f96bd223c71eee2c93b6e4`,
  semantic digest
  `sha256:e793ab4c6f936e4413a5d920a4c34069dbf3bbe32fd5887660d0882e9f85f345`.
  Independent Deep-Sol passed the exact 25,255-byte architecture packet,
  26/26 pins, eight
  nodes, reciprocal node/gate binding, snapshot/fact/policy digests, precedence,
  detached output and zero-effect boundary. The Rev296 active freeze Evidence
  is blob `a31ea3f7e658fb87d237d2985e82dd58ff1fc613`; the Rev295 origin digest is
  `sha256:9e0d34be3e3213fb9f340e242e37bd52442a190e73271c7768c0fe59dc5d986a`.
  The Rev296 freeze Evidence remains immutable, but Revision 297 terminally
  rejected the candidate on the hostile `str.__ne__` callback counterexample.
  No H1 product result was accepted.
- A fresh Root/Sol AHE recovery architecture was attempted at Rev302, but its
  terminal Evidence records the same-class contracted-contradiction recurrence
  after correction 1/1. The four AHE paths are released and unowned. The old Rev294 candidate
  blobs `a0883f67b2ff3f98498d5d03316a7b4189f0f0ab`,
  `69b3f1f43e0cb8f153e99437ce8bed54408e2c8e`,
  `049ebfc51cc02743f8b9f07b8d00719eafe6702d`, and
  `a3b81508251e6e635060d1d6225078257ce3a4de` are permanently rejected and
  cannot be rebound. Rev302 allows only local/read-only evidence handling;
  provider, persistence, GitHub network and live actions remain outside scope.
- The time-bounded continuity monitor recorded by structured authority grants no
  claim, lease, writer, push, deploy, provider, host, UI, or live authority.
- The terminal HWA2A-v5 predecessor is
  `docs/plans/abc-hwa2a-typed-commit-authority-reconstruction-v5-architecture.json`,
  raw blob `03cb39a51f8212b6f0581d0254b38ed804e7b00b`, raw SHA-256
  `4deae4b3fd7b445f1bafb22f3dfc83a86cb78a51f6fdf86e5ef6642bd15a5779`,
  semantic digest
  `sha256:62674fe9741f7820caaf8baf1fa6aec1bb024e3b66b356b75a4b8360b4ec2996`.
  It is 22,301 bytes, canonical JSON+LF, binds 12 new tests and 23 exact
  read-only pins. Revision 291 adds a validator-closed legacy-terminal source
  projection for the accepted clean HWA3A/TLR04 baseline and a digest-bound
  v5 origin packet; this repaired the absent v4 source reference from the first
  Rev290 registration without changing product scope. V5 expressly permitted only frozen-slots
  `CommitAuthoritySnapshot(capability, evidence, capability_status)` and
  `HeadlessWriteAgentStateStore.load_commit_authority(scope, *, claim_id,
  fence, capability_id, evidence_ref)`. No other public API, schema, commit,
  push, deploy or live authority exists. Its architecture packet remains valid
  historical evidence, but Rev292 terminally rejects the attempted product
  realization and releases both writer paths. No HWA writer may resume until a
  fresh architecture and durable claim are registered from accepted baselines.
  The actual implementation and review runtime model identities were not
  authoritatively exposed; record them as `surface_default/unknown`, not as a
  verified model selection.

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
| OWM-18 Lens Code Graph | Product feature approved; waits on bounded graph API and UI owner | After those two prerequisites, start LCG-00 with fixtures/backend first. No productive graph, placement, or UI-live cutover. |
| OWM-19 USI runtime | UIR core advanced; runtime default-off | The blocked USI-07A parent packet and PRE1A CBM lifecycle packet are committed and independently reviewed. Next register exactly one PRE1A claim on its six frozen paths; after acceptance close PRE1B immutable UIR context/admission, then issue a fresh parent sufficiency architecture. |
| OWM-20 Domain adapters | UDA-05 v4 and NMG-03 accepted; productive adapters off | Re-audit selected-scope adapter dependencies and continue only an exact fresh claim. Deferred communication domains and Obsidian remain excluded. |
| OWM-21 Lifecycle/operations | ULO-00/01 accepted; later chain open | Continue the existing ULO dependency chain with synthetic owners and temporary databases. Serialize Auth/backup/wipe hotfiles; every productive lifecycle effect has its own gate. |
| OWM-22 Telegram Todo truth | Through A5 accepted; A6 Rev301 released blocked | Preserve Notes as Todo truth. `ABC-TTD07A6-REV301` is a terminal non-denominator release after correction 1/1; the original frozen and corrected tuples are rejected, with no product commit or acceptance. Continue only the disjoint PRE1A serialized registration/implementation route; no parent USI claim is permitted. |
| OWM-23 Maintenance harness | Deferred until after release; AMH-01 remains terminally released blocked at Rev299 | Keep AMH unclaimed. After release, a fresh post-release reprioritization must repair/narrow the executable command/dependency contract before any new claim. Extend existing policy/gate/Planning authorities; no parallel state store. |
| OWM-24 Local transcription | TRP-03..06 accepted; TRP-07 terminal; live off | Newer structured records accept TRP-05 (2026-08-02, independent QA and Deep-Sol) and TRP-06 (`released_accepted`, commit `871ffe64`), overriding the older deferral sentence. Publish only that accepted/default-off lineage plus the reviewed Python 3.11 repair. TRP-07 remained unaccepted after fresh architecture plus two recovery corrections; no third recovery without a new architecture and claim, and no live audio/deploy claim without exact evidence. |
| OWM-25 Coding loop consolidation | Many CAO/NMG slices accepted | Do not blindly follow stale `ACPR-11 next` prose: current history already contains later ACPR-13D acceptance. Re-run the current frontier audit and claim only the first genuinely unaccepted disjoint predecessor. |
| OWM-26 Subagent context/delegation | Bounded local/fake contracts accepted | Continue only after current ACPR dependency readback. Real adapter activation and external dispatch remain separately gated. |
| OWM-27 Live contextual layer | Deferred until the remaining system is complete enough for system testing | Keep LCL-01 and successors unclaimed. At later reactivation, require fresh Root/Sol architecture and exact disjoint claim; hypotheses remain advisory and cannot override Planning/domain/USI/claim authority. |
| OWM-28 Harbor integration | R7-A2 R3R1 terminally released blocked at Rev283 after correction 1/1; RMT8-v2 and HWA/Temporal source normalization accepted; Rev286 denominator verified; HWA2A-v4 released blocked before write at Rev289; HWA2A-v5 released blocked before freeze at Rev292 | Preserve every rejected tuple. Do not patch or promote v5. Run `$reflect` across disjoint repo units; a future HWA2A attempt requires materially fresh architecture from the clean accepted baseline. |

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
| `agent-maintenance-safety-harness-roadmap.json` | `[0]` / `AMH-01-root-policy-and-runbook` | OWM-23 | `deferred_until_after_release` | Preserve the Rev299 terminal evidence; do not reuse Rev298. After release, a fresh executable/dependency authority and post-release reprioritization are required. |
| `autonomous-coding-production-readiness-roadmap.json` | `[1]` / `ACPR-12-transactional-worktree-and-safe-tool-capsules` | OWM-25 | `accepted_released_round6_a` | Preserve; do not reclaim. |
| `coding-agent-orchestration-consolidation-roadmap.md` | `[2]` / `CAO-08G-verification-repair-independent-review` | OWM-25 | `blocked_by_dependency_and_normalization` | Reconcile exact paths, hotfiles, and tests read-only. |
| `diagnostics-legacy-fix-roadmap.md` | `[3]` / `DLF1B-telegram-history-redaction-fix` | Diagnostics | `normalization_blocked` | Perform only its fresh read-only audit. |
| `headless-write-agent-orchestration-roadmap.md` | `[4]` / `HWA-2-canonical-subagent-commit-bridge` | OWM-3 support | `normalization_blocked_fresh_architecture_required` | `HWA2-...` is the non-counting historical spelling of this one unit. Any product recovery needs materially fresh architecture and must not revive HWA2A v2. |
| `system-assurance-runtime-hardening-roadmap.json` | `[5]` / `SAR-02-fresh-release-evidence` | Assurance gate | `blocked_environment` | Wait for the required symlink-capable environment. |
| `temporal-light-agent-execution-roadmap.json` | `[6]` / `TLR-10-12h-wall-clock-soak` | Temporal gate | `waiting_on_user` | Wait for exact workload, window, limits, and restart injection. |
| `anti-hallucination-evidence-roadmap.md` | `[7]` / `ANTI-HALLUCINATION-next-domain-audit` | Evidence audit | `Rev302 terminal released_blocked, correction 1/1` | Preserve the frozen/corrected tuples and terminal Evidence; permit only the disjoint serialized PRE1Aâ†’PRE1Bâ†’fresh-USI route, never an AHE product claim. |
| `live-contextual-layer-roadmap.json` | `[8]` / `LCL-01-observation-and-hypothesis-contract` | OWM-27 | `deferred_until_system_completion_and_testability` | Do not claim LCL work until the remaining system is complete enough for system testing; then require fresh activation, architecture and claim. |
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
| `odysseus-harbor-one-autonomous-workspace-roadmap.md` | `[21]` / `H1-29-canonical-planning-projection` | OWM-28 | `released_blocked_same_class_recurrence` | Rev297 rejected the frozen two-blob candidate after Deep-Sol proved a hostile `str.__ne__` callback before exact-type validation with correction 1/1. Preserve the bytes; require materially fresh architecture. |
| `planning-definition-editor-roadmap.json` | `[22]` / `PDE-08-real-save-undo-smoke` | Planning UI | `waiting_on_user` | Wait for user authorization. |
| `product-planning-skills-integration-roadmap.md` | `[23]` / `PPS-1-skill-import-compatibility` | Planning skills | `PPS-G0_received_pending_fresh_preflight` | Perform only read-only dependency/path-collision/test-environment preflight, then register one dependency-ready disjoint repo-only claim. |
| `privacy-safe-tool-analytics-roadmap.md` | `[24]` / `TUA4R-agent-streaming-regression-guard` | OWM-12 | `accepted_released_round6_b` | Preserve; keep capture/backfill disabled. |
| `telegram-todo-domain-truth-roadmap.md` | `[25]` / `TTD-07A5-poll-webhook-sweep-and-turn-lease` | OWM-22 | `accepted_released_fd2340a6_live_default_off`; A6 terminal Rev301 | Preserve the accepted parent. Rev301 terminally released the disjoint A6 successor after correction 1/1; both candidate tuples are rejected, no product commit or acceptance exists, and any successor requires materially fresh architecture. |
| `faster-whisper-transcription-protocol-roadmap.json` | `[26]` / `TRP-07-offline-integration-backup-and-live-packet` | OWM-24 | `blocked_after_fresh_architecture_and_two_recovery_corrections_not_accepted` | Preserve TRP-03..06; do not revive TRP-07 without fresh architecture. |
| `tool-taxonomy-registration-roadmap.md` | `[27]` / `TAX7-existing-admin-ui-consumes-the-catalog` | OWM-11 | `repo_implemented_automated_gates_pass_user_ui_acceptance_open` | Wait for user/UI acceptance; keep activation off. |
| `lens-code-graph-roadmap.md` | `[28]` / `LCG-00-ux-truth-and-navigation-contract` | OWM-18 | `registration_released_before_writer_on_round6_raw_projection_blob_mismatch` | CBM-08 and the existing Knowledge/Lens owner route are verified. Converge the foreign Round6 raw/projection authority, then register a fresh two-path tests-first claim with an exact rolling inventory receipt. Stop before product bytes on repeat mismatch. |
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
`b65a2c63cc1f217d955f99b7378cfd5b5bfadd7d`, raw SHA-256
`150acd0dacf4106ea54a49510e8ac42d144e289d0a5452675e59d4ac7c6366dd`.
It accounts for all 72 IDs exactly once and gives every record its class,
authority locator, current status, dependencies, concrete immediate action,
hard stop, verification route, `writer_paths`, `tests`, `evidence`,
`authority_state`, and `drift_notes`. Unknown writer/test details are explicitly
`unresolved` with the required fresh architecture or decision, never guessed.
Design/live/operator records use `not_applicable` plus their exact decision,
target or gate receipt. Its state is
`context_free_continuation_enriched_revision302_ahe_ghiss01_recovery_claimed_ttd07a6_terminal_non_denominator_pyrun01_terminal_rejected_descriptive_only`.
All 72 records are resolved and retain the exact 19 repo / 19 design / 30 live /
4 operator split. Use the inventory to find current sources, never to
manufacture a claim, gate approval, or live authority. Its top-level
`authority_revision: 302` is descriptive evidence; current Open Work and Round
7 still win if their bytes later drift.

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

Historical Rev286 denominator-publication checksum (not current authority):

```json
{"authority_blobs_git_no_filters":{"guidance":"7d4b43dc7f91c1f9cfc9837d3b7bb425f50fc3f7","hwa_packet":"c0075c15f86a53642ebccd8cd00e4734a126958d","hwa_source":"77318983c9dcc5b124b1c1ac9408f27ce08c09d1","open_work":"a08e49b97dcb1eb9749bfd8aa561c0c6011933c8","round6":"0cb4c79ca47c1302038e4c9f0849c0284cf327f0","round7":"8126a645155c57e4ddbf98203725938249310694","temporal_source":"441d2f335b1aebd1876b015846e39057bd5bc89b"},"authority_revision":286,"current_inventory_blob_git_no_filters":"3237c7311481a0c1d7d165c323c1d880326ccbe5","open_counts":{"design":19,"live":30,"operator":4,"repo":19,"total":72},"published_progress":"173/245","verification_state":"verified_revision286_reference_not_claim_or_live_authority"}
```

The authority blobs in this compact receipt are the independently reviewed
Rev285 compare-before tuple. The block is historical publication evidence only;
  the current Rev303 router tuple is recorded in section 6. This avoids an
impossible self-pin without mixing authority epochs.

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
- OWM-28: Round 7 `$.waves[0].slices[2].execution_claim` holds the terminal
  R7-A2 R3R1 release. At Revision 303 the long-run and Open Work projections
  contain zero active claims and zero leases; the AHE-GHISS01 recovery is
  terminal Evidence only;
  `ABC-HWA2A-V5-REV290` is terminally released blocked and retained only as
  rejected evidence.

## 8. Normalized blocked/recovery precursors

These identifiers appear in current/historical normalized frontiers. They are
not direct writer permission. Before touching any of them, read the latest
Round 6 row, Open Work history, and any newer accepted record.

### Fresh architecture or explicit normalization required

- `NMG-05A`: prior causal-store candidates are terminal/rejected. A fresh
  architecture must close the full stored-row lookup/error boundary. The
  recoverable paused candidate is descriptive evidence only: R6 authority 219,
  state `paused_recoverable_candidate_main_worktree`, source blob
  `5beb5f71d7109cd9b4a8d8d8be82c2fb49fd143c`, test blob
  `889f68100b6395054363090962c0674e40579b1c`, and exactly the two untracked
  paths `src/causal_claim_store.py` and `tests/test_causal_claim_store.py`.
  Preserve those bytes; do not resume the old claim or treat them as accepted.
  No clean, disjoint NMG writer is selectable while the old worktree contains
  rejected lineage bytes.
- `FCA-03A-R2`: old R2 candidates are terminal. The later FCA-03A v3 accepted
  record is authoritative; do not reopen R2.
- `RMT9-P0A`: correction budget exhausted and provenance/import action remains
  separately gated.
- `HWA2` / HWA2A historical candidates: exact raw SQLite prerequisite and
  identity handling failed after bounded corrections, and v5 is terminal at
  Rev292 because its matrix was contradictory. Never revive them. A future
  attempt requires fresh architecture from the clean HWA3A/TLR04 baselines.
- `UIR-03`: use the accepted Go-A R2 lineage and normalize stale projections
  before any new runtime claim.
- `DLF1B`, `TAX-serialized-handoff`, and mixed-genesis normalization work:
  control-plane predecessor first; product writer only after central green.

### Dependency-blocked or unselected

- `CAO-08G`, `SAR-01`, `SAR-02`, `TLR-10-12h`,
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

- `AMH-01` no longer waits on `AMH-GATE-EXPLICIT-GOAL-OR-REPRIORITIZATION`:
  the standing full-roadmap goal selected it. Rev299 released its verification
  claim blocked before freeze with no product diff and correction 0/1 because
  the persisted Windows runner was unavailable from WSL and the pinned broader
  dependency lane had one foreign Round6-test failure. Any retry needs fresh
  executable/dependency authority; its focused policy gate remains
  `tests/test_agent_maintenance_policy.py`.
- `CAO-08G` needs exact paths, hotfiles, and slice tests registered after
  ACPR-12. `ANTI-HALLUCINATION-next-domain-audit` first needs one unsupported
  claim domain with exact IDs, owner, paths, QA, and tests.
- `SAR-02` remains parked until a symlink-capable environment can run its
  unchanged required suite. `TLR-10` remains unscheduled until workload,
  12-hour window, limits, restart injection, and evidence path are exact.
- Open Work preclaim boundaries: FCA-03 needs an accepted resumable,
  revision-bound Forge content reader; UIR-06 needs generation-fenced
  cancellable query execution and owner decisions; USI-07A is the exact next
  Root/Sol architecture precursor and needs runtime, lexical and SQLite owner
  lifecycle contracts. The blocked parent packet and PRE1A lifecycle packet are
  now committed and independently reviewed; PRE1A claim-origin registration is
  next, then PRE1B after PRE1A acceptance. CBM-09 still needs the runtime and
  generation contracts.
  Reconcile these read-only and never invent a parallel API or authority.
- Historical `queue_status` fields may still carry older labels and
  `next_parallel_wave_registration.active_claims` contains terminal historical
  entries. The older checkpoint copies at Open Work
  `$.current_position.full_goal_progress.long_run_checkpoint` and Round 7
  `$.long_run_state.global_goal_progress.long_run_checkpoint` were normalized
  at Revision 286 to `173/245`, the current deadline, and the verified
  denominator state. Current Rev303 authority has zero active claims and zero
  leases. The AHE-GHISS01 recovery claim described in section 6 is terminal
  evidence only. TTD-07A6 is a terminal non-denominator released-blocked claim after
  correction 1/1, as described in section 6. Its terminal history includes the
  blocked PYRUN frozen candidate, the blocked-before-freeze AMH-01 verification
  claim, the blocked H1-29 frozen candidate, the blocked
  AHE-GHISS01 candidate from Rev294,
  the blocked pre-write HWA2A-v4 claim, the
  blocked-before-freeze HWA2A-v5 claim, and the accepted RMT8-v2 claim from
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
3. Recompute Open Work/Round 6/Round 7 raw blobs with `--no-filters`; filtered
   Round 6 hashes are invalid because of CRLF projection. Confirm Revision 304,
   the single PRE1A claim/lease and its exact six paths, the terminal AHE Evidence and exact
   frozen/corrected tuples, plus terminal TTD-07A6, PYRUN, AMH-01,
   H1-29, AHE-GHISS01 and HWA2A-v5 records and all packet/Evidence identities
   from section 6 before any new writer work.
4. Preserve accepted `ABC-RMT8-V2-REV278` exactly as frozen and released at
   Revision 279. Do not reopen its exhausted correction budget or reconstruct
   its product bytes from prose.
5. Do not resume TTD-07A6: its original frozen and corrected tuples are
   terminally rejected after correction 1/1, with terminal Evidence blob
   `6c98365320916e382e8c24b4d860647c91a32793` and no product acceptance. The
   current safe repo-only action is tests-first PRE1A implementation against
   claim `ABC-USI07A-PRE1A-REV304` and packet blob
   `6186172ed362e1bdafe2da469d81011f8fce2ba2`;
   preserve terminal PYRUN, R7-A2 R3R1, HWA2A-v4/v5, AHE-GHISS01,
   H1-29, AMH-01 and TTD evidence without another patch or candidate reuse.
   Do not resume the AHE product mechanism or register the six-path parent USI
   product claim. PRE1A writes are limited to the active exact durable claim;
   PRE1B and the fresh parent architecture remain serialized after acceptance.
6. The public independent scope review and one-parent/one-commit ancestry are
   complete. No exact action-specific push phrase is currently present, so
   keep push and CI-triggering publication closed.
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

Current checklist status: **PASS for context-free Rev377 continuity with zero
active claims or leases**. The terminal LCG registration, AHE Evidence and rejected frozen
and corrected tuples are described above; the enriched 72-record inventory
contains every required writer-path, test, evidence, authority and drift field,
preserves all counts and IDs, and is canonical sorted-key JSON+LF. This handoff
and inventory are descriptive continuity evidence; only OW/R7 register claims.

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
- LCG-00 product acceptance. Revision 377 released the registration before
  writer dispatch because current foreign Round6 raw bytes do not match the
  Guidance-bound projection blob and the initial registration Evidence omitted
  the rolling inventory receipt. Both product paths remain absent; no tests-first
  RED, candidate, product commit, publication or live result exists.
- HWA2A-v5 product acceptance. Rev292 terminally released the claim blocked
  before freeze; no product bytes were accepted. Valid 12/12 tests-first RED,
  py_compile, diff-check and terminal Deep-Sol are verified. Complete focused
  GREEN, dependency, product-central and independent-QA evidence do not exist.
- AHE-GHISS-01 product acceptance. Revision 302 terminally released the exact
  frozen and corrected four-path tuples blocked after the same-class contracted
  contradiction recurrence at correction 1/1. Terminal Evidence is
  `docs/plans/evidence/abc-ahe-ghiss01-recovery-rev302-terminal-release.json`
  with raw blob `d0e216ff5393d90cee275e1eb6feeda66a1ffcb4`; no product was
  accepted. GitHub provider, persistence and live behavior remain outside scope.
- USI-07A product acceptance. No parent product claim exists. The blocked parent
  packet and PRE1A architecture are committed and independently reviewed, but
  PRE1A has no registered claim/candidate/tests yet; PRE1B has no materialized
  packet; the fresh post-PRE1A/PRE1B six-path parent architecture does not yet
  exist.
- H1-29 product acceptance. Rev297 terminally rejected the exact frozen
  candidate after valid RED, focused 8/8, dependency 311/311, static and central
  PASS because Deep-Sol proved same-class hostile callback execution with
  correction 1/1. No product commit, publication or live result exists.
- AMH-01 product acceptance. Rev299 released the verification-first claim
  blocked before freeze with unchanged four-path baselines and correction 0/1.
  Policy 5/5 and central validators pass, but the persisted Windows command did
  not start from WSL and its pinned broader dependency lane ended 50 PASS / 1
  foreign Round6-validator-test FAIL. No candidate, independent acceptance QA,
  product commit, publication, or live result exists.
- R7-PYRUN-01 product acceptance. Rev300 rejected the exact frozen pair after
  189 dependency tests passed and one stale unowned Round6 assertion failed;
  satisfying the declared dependency tier requires a forbidden third path.
  No product commit, publication or live result exists.
- TTD-07A6 product acceptance. Rev301 is terminally `released_blocked` after
  correction 1/1. Terminal Evidence is
  `docs/plans/evidence/abc-ttd07a6-rev301-terminal-release.json`, blob
  `6c98365320916e382e8c24b4d860647c91a32793`. The original frozen and
  corrected tuples were rejected for the workflow-eligibility and
  body-budget/projected-envelope findings; no product commit, publication or
  live activation exists. A materially fresh architecture is required before
  any successor claim.
