# Security Disclosure Release Gate

Stand: 2026-06-19

Status: **operator wording contract for `ABC3C-security-ui-docs`**

## Goal

Give operators a release-safe, UI-safe, and runbook-safe way to describe the current protection model without implying at-rest encryption that has not been implemented or evidenced.

This document is intentionally narrow:

- no runtime change
- no secret handling change
- no key-management claim
- no storage migration
- no encryption implementation claim

## Decision Boundary

Password protection, login protection, UI access control, or local-only processing do **not** automatically mean at-rest encryption.

For this release gate, operators must treat these questions separately:

- Can a user open or operate the app without the password?
- Are local files encrypted at rest on disk?
- Are derived indexes, caches, logs, and metadata encrypted at rest on disk?
- Are derived artifacts disposable, rebuildable, or still sensitive despite being derived?

If these questions do not all have the same answer, the release language must say so plainly.

## Protection Model Baseline

The safe default release assumption is:

- password protection may restrict interactive access or plugin access
- password protection is **not** evidence of at-rest encryption
- local vault files must be evaluated separately from derived storage
- derived indexes, caches, logs, and metadata must be evaluated separately from source files
- derived artifacts may still expose sensitive content, structure, filenames, tags, embeddings, graph relations, provenance, or usage traces even when they are rebuildable

## Storage Classes That Must Be Named Separately

Operators must not compress all local persistence into one generic statement like "your data is encrypted" or "your data is protected."

At minimum, release notes, operator docs, or UI disclosure must separate:

1. Source content
   Examples: vault files, imported documents, attachments, exported bundles, local databases containing original or near-original text.

2. Derived indexes and embeddings
   Examples: vector indexes, search indexes, chunk stores, graph nodes/edges, provenance records, cached summaries.

3. Caches and temporary artifacts
   Examples: query caches, rebuild caches, temp files, previews, sync work files, transient local mirrors.

4. Logs and operational traces
   Examples: application logs, audit traces, diagnostics, crash output, command history, operator notes generated from local runs.

5. Metadata
   Examples: filenames, paths, tags, timestamps, source IDs, routing state, review state, classification labels, graph topology, job history.

Derived, cached, logged, or metadata-only does **not** mean harmless. Each class needs its own protection statement or explicit "not separately evidenced" limitation.

## Required Operator Language

Minimum acceptable wording for current Partial state:

`Password protection limits access to the app or plugin flow. It is not the same as encryption at rest. Stored source files, derived indexes, caches, logs, and metadata must be assessed separately and should not be assumed to be encrypted unless that protection is explicitly documented.`

Short UI-safe variant:

`Password protection is not the same as encryption at rest. Local files and derived data may have different protection levels.`

Known-limits variant:

`Current protection language must not be read as a claim that vault files, indexes, caches, logs, or metadata are encrypted at rest.`

## Forbidden Release Claims

Do not ship release, UI, setup, or operator language that implies any of the following without evidence:

- "data is encrypted at rest"
- "vault is encrypted"
- "local data is secure on disk"
- "password protection encrypts your stored data"
- "indexes and caches are protected the same way as source files"
- "metadata is safe because it contains no raw content"

Also not allowed:

- collapsing source files and derived artifacts into one blanket security claim
- treating rebuildable artifacts as non-sensitive by default
- implying logs are safe to share because they are "only operational"

## Go / Partial / No-Go

### Go

Go only when all of the following are true:

- release and operator language explicitly distinguishes password protection from at-rest encryption
- source files, derived indexes, caches, logs, and metadata are each covered by explicit protection wording or an explicit limitation
- no release-facing text implies stronger at-rest protection than has been evidenced
- known limits are present wherever security claims could otherwise be over-read

### Partial

Partial when the operator understanding is clear in internal docs, but one or more public or UI-facing paths still compress the protection model too much.

Allowed Partial language:

- "password-gated access is present"
- "local-only or offline-first boundaries exist where documented"
- "at-rest encryption is not claimed"

Required Partial limitation:

- source files and derived local artifacts are not yet fully covered by one consistent user-facing disclosure surface

### No-Go

No-Go when any release-facing path could reasonably cause an operator or user to infer that:

- password protection equals at-rest encryption
- encrypted access control equals encrypted storage
- derived indexes, caches, logs, or metadata inherit stronger protection automatically
- metadata-only storage is non-sensitive by default

No-Go also applies when known limits are missing from the same operator path that makes the security claim.

## Operator Release Decision

Use this gate as follows:

- Go: release language may say exactly what is protected and exactly what is not claimed.
- Partial: internal release or controlled operator rollout may proceed only with explicit limitation text.
- No-Go: public-facing security wording must stop or be downgraded before release.

If there is any doubt, downgrade to Partial or No-Go instead of relying on intent, architecture assumptions, or future encryption plans.

## Evidence Expectations

Acceptable evidence for this docs slice:

- redacted UI copy
- redacted release-note wording
- operator runbook wording
- known-limits wording that names source content and derived artifacts separately

Not acceptable as evidence:

- a password field existing in the UI
- a local-only mode existing somewhere else in the product
- a future encryption roadmap
- an unstated assumption that rebuildable data is low risk

## Verification

Docs-only checks for this slice:

- `git diff --check -- docs/plans/security-disclosure-release-gate.md docs/plans/release-hardening-gates.md`
- copy review for `password`, `encrypted`, `at rest`, `index`, `cache`, `log`, and `metadata`
- focused secret-pattern scan on changed docs if available

## Risks

- A later UI or release-note pass could shorten the wording and accidentally remove the distinction between access control and encrypted storage.
- A derived store may be rebuildable but still sensitive enough to require separate handling; operators must not treat disposability as confidentiality.
- Logs and metadata are easy to under-classify because they may reveal filenames, structure, usage patterns, or identifiers without copying full content.

## Handoff

Alice owns the wording contract in this file.

Bob may later add read-only validation or fixture-backed copy checks in a separate scope.

Charlie decides whether this gate is satisfied by docs alone for an internal release, or whether additional UI disclosure is still required before public release language can move from Partial to Go.
