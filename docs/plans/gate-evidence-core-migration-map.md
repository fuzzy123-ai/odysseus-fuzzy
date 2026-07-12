# Gate Evidence Core Migration Map

Status: GEC5 docs-only migration note

Scope: additive compatibility map for Gate Evidence Core. This note does not
approve breaking route-shape changes, live probes, provider calls, Telegram
sends, Nextcloud writes, deploys, host actions or raw evidence capture.

## Current Canonical Surface

- `src/gate_evidence_core.py` defines canonical gate/evidence models,
  redaction assertions and the `what_can_safely_happen_now` aggregate.
- `src/gate_evidence_adapters.py` adapts release readiness, live affordances,
  review gates, plugin release gates and quality/runtime gates into canonical
  gates.
- `/api/live-affordances/readiness`, `/api/review-gates/status` and
  `/api/version-one/readiness` now expose additive canonical fields:
  `canonical_gate_evidence` and `canonical_safe_now`.

## Compatibility Rule

- Legacy keys remain authoritative for existing UI, automation and route
  consumers until each consumer is explicitly migrated.
- Canonical keys are additive and read-only.
- A consumer may read canonical fields only after its legacy-contract tests
  still pass with the new fields present.
- Any proposed cleanup that removes or renames old keys is blocked by
  `GEC-ROUTE-SHAPE-GO`.

## Migration Order

| Order | Consumer | Current canonical path | Next safe action |
| -: | --- | --- | --- |
| 1 | Live Affordances | route payload has canonical gate list and safe-now aggregate | UI/automation may read canonical fields behind legacy fallback |
| 2 | Review Gates | route payload has canonical review/write gates and safe-now aggregate | Review queue can render canonical blockers without changing approval commands |
| 3 | Version One Readiness | route payload has canonical release-readiness gate and safe-now aggregate | Dashboard can use canonical blocker/safe-action summary |
| 4 | Plugin Release Gate | adapter exists, route not wired here | Add canonical fields where plugin readiness is surfaced |
| 5 | Quality/Runtime Gates | adapter exists, runtime routes not wired here | Use adapter in Charlie verification summaries after scoped tests |
| 6 | Release Readiness Pipeline | adapter exists for release-style payloads | Add route-level canonical fields when a public readiness endpoint is touched |
| 7 | Security/Ops/System Health | inventory exists, no dedicated adapter yet | Add adapter after current security/system-health payload shapes are characterized |

## Redaction Requirements

- Evidence refs stay symbolic or summarized.
- Raw provider output, raw private content, private host paths, tokens, chat IDs
  and secrets remain No-Go for canonical evidence.
- Adapter output must pass `assert_redaction_safe` before route exposure.
- Route tests should keep checking the legacy redaction booleans and add at
  least one canonical-field assertion.

## Deferred Cleanup

- Do not remove legacy route fields yet.
- Do not collapse local status vocabularies into canonical-only status until
  dependent UI and automation consumers have a fallback-free migration.
- Do not treat `canonical_safe_now.can_proceed=false` as a failure by itself;
  for live/operator-gated surfaces it is the expected safe outcome.
