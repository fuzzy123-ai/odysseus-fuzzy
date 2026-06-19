# Large-Vault Performance Release Gate

Stand: 2026-06-19

Status: **offline release-claim guard for `ABC3A-performance-gate`**

## Decision

The existing Obsidian graph fixture is useful release-candidate evidence, but it is not large-vault evidence by itself.

Current known automated baseline:

- Fixture scale: `120` synthetic notes
- Median threshold: `700 ms`
- Worst-run threshold: `1200 ms`
- Scope: graph build baseline only

This supports a small/medium synthetic-vault claim, not a broad 10k-file or 1GB vault claim.

## Large-Vault Claim Threshold

Large-vault release language needs at least one redacted, repeatable record that includes:

- at least `10,000` Markdown files or at least `1024 MB` of redacted/plain synthetic text
- approximate link/reference count
- machine class or resource envelope
- indexing/rebuild maximum duration
- interactive query/search p95
- filter interaction p95
- graph load p95

## Guard

`src/large_vault_performance_gate.py` and `tests/test_large_vault_performance_gate.py` make this boundary machine-checkable:

- small/medium evidence may pass only for a small/medium claim
- a large-vault claim is `no_go` when the evidence scale is only RC-sized
- a large-vault claim is `no_go` when p95 or rebuild budgets exceed threshold
- payloads contain only scale summaries, not private vault content

## Go / Partial / No-Go

Go:
- requested release claim matches the measured scale
- all default p95 and rebuild budgets are within threshold
- evidence names machine class and workload without private paths or secrets

Partial:
- evidence is useful internally, but public wording must be downgraded to the measured scale

No-Go:
- large-vault wording is requested from small/medium evidence
- budget overrun is hidden
- evidence depends on private vault content that cannot be safely summarized

## Next Action

Keep current public language limited to the measured synthetic scale until a real large synthetic performance packet exists. Do not promote the 120-note fixture into a large-vault claim.
