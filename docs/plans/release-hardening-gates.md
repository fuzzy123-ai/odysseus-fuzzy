# Release Hardening Gates

Stand: 2026-06-19

Status: **operator contract for `ABC3-release-hardening-critique`**

## Purpose

This document turns five P0/P1 release-hardening critiques into explicit operator gates. It is meant to make release language safer, more measurable, and easier to stop when evidence is missing.

This document does **not** replace the external `1.0.0` release decision. External `1.0.0` remains **No-Go** until the separate Provider Proof and Test-Vault Export/Import/Rebuild gates are recorded as Go with redacted evidence.

## Global Contract

- Go means the release claim may be made for the named scope only, with evidence linked or recorded.
- Partial means the work is useful internally, but the public/operator claim must include the named limitation.
- No-Go means the claim must not be made, and the release must either stop or downgrade language.
- Evidence must be redacted. Do not record secrets, tokens, chat IDs, private vault paths, private provider outputs, or host-sensitive output.
- Green unit tests, docs, or mocked checks are not live-provider, live-host, or external-release permission.

## Gate 1 - Measurable Large-Vault Performance

### Ziel

Odysseus must not imply large-vault readiness unless performance is measured against a named vault size, machine class, and workload. The operator needs an evidence-based answer to: "How large was the vault, what was measured, and what stayed within budget?"

### Go

Go only when a redacted performance record exists for the release target and includes:

- Vault scale: file count, approximate total text size, and approximate link/reference count.
- Workload: indexing or rebuild path, representative search/query path, graph load, and filter interaction.
- Environment: machine class or resource envelope, without private host paths or secrets.
- Thresholds: p95 interactive query and filter responses are within the documented operator budget, long-running rebuild/index operations have a documented maximum duration, and failure behavior is visible instead of silent.

Recommended minimum release claim threshold: at least 10,000 Markdown files or at least 1 GB of redacted/plain test content, unless the release notes explicitly claim a smaller supported limit.

### Partial

Partial when small or medium vault checks are green, but no large-vault evidence exists. Public language may say "validated for small/medium vaults" only if the tested scale is named.

### No-Go

No-Go for any large-vault claim when performance evidence is missing, unrepeatable, secret-tainted, based only on developer intuition, or measured on private data that cannot be safely summarized.

### Evidence

- Redacted performance log or table.
- Exact command or manual procedure used, without private paths.
- Release note wording that matches the measured scale.

### Stop-Regeln

- Stop if evidence requires persisting private vault content, private paths, tokens, or provider output.
- Stop if large-vault failure causes data loss, silent truncation, or unbounded runtime.
- Stop if a green small-vault test is being used as large-vault proof.

### Empfohlene Tests/Checks

- Deterministic synthetic-vault performance check.
- Rebuild/index timing check with redacted scale summary.
- Representative search/query latency check.
- Graph load and filter latency check.
- `git diff --check -- docs/plans/release-hardening-gates.md` for this docs slice.

## Gate 2 - Graph-/Filter-State-Isolation

### Ziel

Graph and filter behavior must not depend on fragile global state, stale UI selections, or cross-project leakage. The operator needs confidence that applying a filter in one project or graph view cannot silently affect another unrelated context.

### Go

Go only when graph and filter state are scoped to the active project/view/session boundary and reset rules are documented. Switching projects, changing filters, and reloading views must preserve only intended state and discard stale state safely.

### Partial

Partial when the UI is usable but state isolation is not fully proven. Release language must describe the limitation and avoid claims like "independent project views" or "safe multi-project filtering."

### No-Go

No-Go when stale filters, graph selections, or cached state can affect another project, hide results without visible indication, or cause an apply/merge operation to use the wrong context.

### Evidence

- Test or manual evidence for project switch, filter reset, graph reload, and back/forward or refresh behavior.
- Redacted screenshots or notes showing visible active-filter state.
- A short operator note describing how to clear or verify active filters.

### Stop-Regeln

- Stop if cross-project state leakage is observed.
- Stop if hidden filters can change release-critical output.
- Stop if fixing the issue requires runtime/UI hotfile edits outside the approved docs-only scope.

### Empfohlene Tests/Checks

- Project A to Project B switch with active graph filters.
- Clear-filter and reload behavior.
- Multi-tab or refresh behavior if supported.
- Manual UI smoke with only synthetic or redacted project data.

## Gate 3 - At-Rest-Security Disclosure

### Ziel

Operator and UI language must clearly distinguish password protection, access control, local storage, and true at-rest encryption. Users must not infer that data is encrypted at rest unless that is actually implemented and evidenced.

### Go

Go only when release/UI/operator language says plainly whether stored vault data, indexes, caches, logs, and derived metadata are encrypted at rest. If they are not encrypted, the UI or operator docs must say that password protection does not equal at-rest encryption.

### Partial

Partial when the technical behavior is known but the UI/operator language is incomplete. Internal use may continue with a documented limitation, but public security claims must be downgraded.

### No-Go

No-Go when the product could be interpreted as providing at-rest encryption without evidence, or when docs imply secrets, vault content, indexes, cache files, logs, or metadata are protected more strongly than they are.

### Evidence

- Redacted UI copy or operator text.
- Release note entry that avoids ambiguous security wording.
- Known-limits section covering local files, derived indexes, caches, and logs.

### Stop-Regeln

- Stop if real secrets, tokens, passwords, chat IDs, private vault content, or provider outputs would be needed as examples.
- Stop if marketing or release language overstates the protection model.
- Stop if live security configuration changes are requested under this docs-only slice.

### Empfohlene Tests/Checks

- Copy review for the words "encrypted", "secure", "protected", "password", "secret", "token", and "private".
- Manual release-note review against the implemented protection model.
- Focused secret-pattern scan on changed docs if available.

## Gate 4 - Strict Blocking On Project Apply-/Merge-Konflikten

### Ziel

Project apply and merge flows must fail closed. A conflict must block the operation instead of overwriting, silently merging, or producing ambiguous state.

### Go

Go only when conflict behavior is documented and evidenced as fail-closed: the operation stops, the operator sees a clear conflict message, no unrelated files are modified, and no automatic overwrite occurs without explicit follow-up approval.

### Partial

Partial when conflicts are detected in some paths but not all apply/merge paths. Release language must say which flows are guarded and which remain blocked or experimental.

### No-Go

No-Go when any apply/merge conflict can silently overwrite local content, auto-resolve without traceability, leave partial state without a recovery note, or continue after an unresolved conflict.

### Evidence

- Synthetic conflict scenario with before/after summary.
- Redacted operator message showing the block reason.
- Recovery/handoff note explaining the safe next action.

### Stop-Regeln

- Stop if resolving the conflict would require destructive Git commands, force writes, or reverting someone else's changes.
- Stop if foreign staged files or hotfile conflicts are present.
- Stop if the operation would touch runtime/provider/host files outside the approved scope.

### Empfohlene Tests/Checks

- Synthetic project apply conflict.
- Synthetic merge conflict.
- Partial-apply rollback or no-write verification.
- Operator-message review for clear "blocked" language.

## Gate 5 - Repository-/Link-Hygiene Including Legacy-Typo Risk

### Ziel

Release docs must send operators to the correct repository, branch, artifact, and support links. Legacy repository-name typos or stale links must be treated as release risks because they can send users to old code, wrong forks, or unsafe instructions.

### Go

Go only when release-facing docs and operator handoffs use the intended repository name, branch, and link targets consistently. Known legacy typo risks must be listed with the expected replacement or an explicit "do not use" note.

### Partial

Partial when core links are correct but historical docs still contain stale names or typo-prone references. Release language may proceed internally only if the stale references are not part of the operator path.

### No-Go

No-Go when an operator could reasonably follow a stale link, typo, wrong fork, wrong branch, outdated artifact, or obsolete setup instruction during release, install, update, backup, restore, or support work.

### Evidence

- Link audit summary for release-facing docs.
- List of corrected or intentionally retained legacy names.
- Redacted handoff note identifying the canonical repository and branch by relative/operator-safe wording.

### Stop-Regeln

- Stop if link verification would require network access under this docs-only slice.
- Stop if a stale link points to instructions involving secrets, deploys, providers, hosts, backup, restore, export/import, rebuild, or live operations.
- Stop if correcting links would require editing files outside the approved scope.

### Empfohlene Tests/Checks

- Offline text search for repository names, legacy typos, stale branch names, and release URLs.
- Markdown link syntax review.
- Operator-path dry read from release note to install/update handoff, without executing commands.

## Release Decision Summary

For `ABC3-release-hardening-critique`, the safe default is:

- Large-vault performance: **No-Go for large-vault claims** until measured evidence exists.
- Graph/filter state isolation: **Partial or No-Go** until isolation evidence is recorded.
- At-rest-security disclosure: **No-Go for security claims** until UI/operator language is explicit.
- Project apply/merge conflict blocking: **No-Go for unsafe apply/merge flows** until fail-closed behavior is evidenced.
- Repository/link hygiene: **Partial or No-Go** depending on whether stale links are on the operator path.

These gates can harden internal RC language, but they do not change the external `1.0.0` blocker. External `1.0.0` still requires Provider Proof and Test-Vault Export/Import/Rebuild evidence.

## Handoff

Alice owns the operator wording and release-language downgrade rules in this document. Bob or Charlie can later add read-only validators or tests, but only within a separately approved scope. Charlie remains responsible for deciding whether these gates become release blockers, follow-up items, or explicit known limits.
