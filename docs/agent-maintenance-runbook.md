# Agent Maintenance Runbook

This runbook is the reproducible repository workflow for maintenance agents.
It projects the stable rules in [AGENTS.md](../AGENTS.md); it is not a task
queue, evidence receipt, or source of mutable execution state.

This document grants no authority to commit, push, deploy, call a provider,
change a host, send data, read private data, or perform another live or
external mutation. Each such action needs its own exact authorization.

## 1. Establish current authority

1. Read the root `AGENTS.md` and any narrower instructions for the target
   path.
2. Read the selected versioned roadmap plus its current gate queue, claim, and
   clarification records. These structured records are authoritative; old chat
   and prose are context only.
3. Inspect `git status --short`, the path-scoped diff, and staged paths.
4. Confirm one dependency-ready slice, its mutation class, allowed paths,
   tests, evidence level, stop rules, and required owner decisions.
5. If the state is stale or contradictory, or a material owner choice is
   missing, stop and surface `waiting_on_user`. Do not guess.

Never create `STATE.md`, `OWNER_QUEUE.md`, or a parallel database to make a
conflict disappear. Repair or clarify the declared authority.

## 2. Claim the minimum paths

Record one durable claim containing:

- roadmap and slice identifier;
- owner and lease or heartbeat;
- exact allowed and coordination paths;
- dependencies and mutation class;
- focused tests and required evidence;
- current blocker, if any.

Compare the claim with current writers and foreign hunks. Continue only when
the paths are disjoint or an explicit handoff has released them. Preserve
unrelated modified, untracked, and staged files.

## 3. Make bounded changes

- Change only the minimum claimed paths needed for the acceptance contract.
- Reuse canonical Planning, claim, gate, clarification, verification, and Git
  policy modules instead of creating parallel authorities.
- Keep default-off features and external effects default-off.
- Do not use destructive Git, force push, history rewrite, broad cleanup, or
  automatic conflict resolution.
- Do not automatically stage, commit, push, deploy, or perform a live action.
  Verify the exact scope and authority immediately before each separately
  authorized operation.

## 4. Diagnose without exposing secrets

Use only repository-owned allowlisted diagnostic schemas. A safe result may
contain fixed-key presence booleans and bounded counts, but never a credential
value, prefix, suffix, length, hash, private payload, raw provider response, or
unredacted subprocess output.

Do not use `env`, `printenv`, credential-file reads, service-environment
dumps, container inspect environment output, or unredacted compose
configuration as evidence. Redact and validate before output reaches a model,
UI, history, log, receipt, or roadmap. If no narrow schema can answer the
question, stop and request a narrower evidence contract.

## 5. Verify at the declared level

Run focused checks first. Then run every stronger lane required by the slice:

- `static`: syntax, schema, formatting, or deterministic policy checks;
- `focused`: tests for the changed contract;
- `integration`: affected cross-module behavior;
- `ui`: browser and visual evidence when appearance or interaction changed;
- `live`: bounded real-target evidence only after an action-specific Go;
- `temporal`: the declared observation window when reliability over time is
  part of acceptance.

When `scripts/verify.py` is present, select its named `guards-only`,
`fast`, `full`, or `ui` lane. Until then, run the exact commands declared
by the active slice. Never install dependencies or start services implicitly.

Record the command identity, result, exact revision or dirty-diff digest, and
explicit limits. A weak lane, old receipt, mismatched revision, prose, or
agent-authored claim cannot substitute for required machine evidence.

## 6. Complete or hand off

A slice is complete only when dependencies and claim ownership are current,
acceptance is met, required evidence is current, owner questions are resolved
or explicitly deferred, and no stronger verification level is implied.

Use this bounded handoff:

```text
Roadmap:
Slice:
Owner and claim:
Status:
Changed paths:
Tests and results:
Evidence reference:
Next action:
Blockers or owner questions:
Not verified:
Publication or live authority:
```

Release the claim only after recording the next safe action. Keep separately
gated work listed under `Not verified`; do not describe it as complete.

## Stop immediately when

- a secret, private payload, raw diagnostic, or credential-derived detail may
  escape;
- a required claim, dependency, receipt, or owner decision is missing, stale,
  contradictory, or weaker than required;
- another writer or foreign hunk overlaps the target;
- safe progress would require destructive Git, deletion, overwrite, force
  push, history rewrite, or absorption of foreign staged content;
- work would create a second Planning, owner-question, scheduler, heartbeat,
  or orchestration authority;
- a commit, push, deploy, provider call, host change, send, backup, restore, or
  live action lacks exact authorization.
