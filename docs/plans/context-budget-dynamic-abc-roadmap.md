# Dynamic Context Budget — ABC Execution Roadmap

## Goal

Odysseus resolves a safe input-token budget per model and provider, never creates
a negative prompt budget, preserves the immediately preceding dialog turn, and
exposes the overrides as an admin setting.

## Current evidence

- Production persisted `agent_input_token_budget=900` and
  `agent_input_token_hard_max=2500`.
- Agent mode passed the 900-token input cap to `trim_for_context` and then
  subtracted a 1024/2048-token output reserve again.
- Production logs recorded usable budgets of `-124` and `-1148`, followed by
  removal of the immediately preceding assistant response.
- The immediate production mitigation is active: the 6000 auto sentinel with a
  32000 auto hard maximum.
- The general MVP runner queue is exhausted at 100%; this is an independent
  Standard ABC track.

## Mode

Standard ABC.

## Non-goals

- No broad settings redesign.
- No unrelated model routing changes.
- No changes to existing user-owned worktree edits outside the declared paths.
- No provider calls or secret-bearing live tests.

## Stop rules

- Stop on overlapping edits inside the context-budget block or unrelated staged
  files.
- Never revert foreign changes in `src/agent_loop.py` or `static/style.css`.
- Stop before destructive Git operations, secret handling, or an unapproved
  production deploy.
- A failing broad test outside the touched scope is recorded and isolated; it is
  not fixed opportunistically.

## Configuration contract

`agent_input_token_budget_overrides` is an object with optional `providers` and
`models` maps. Map values are positive integer prompt caps.

```json
{
  "providers": {"deepseek": 64000, "ollama": 8000},
  "models": {"deepseek-v4-pro": 128000, "gemma3:4b": 6000}
}
```

Resolution precedence is exact normalized model, provider, legacy explicit
global input cap, then auto global hard maximum. The resolved value is an input
cap. Output reserve is applied once against the model context window, never
subtracted from the input cap a second time.

## Slice queue

### CB-A — Alice: admin setting surface

- Class: `repo_only`
- Allowed paths: `static/index.html`, `static/js/settings.js`
- Deliverable: compact accessible editor for provider/model input caps in the
  existing Tools settings panel, with validation, empty state, save/load, and
  explanatory copy.
- Verification: `node --check static/js/settings.js` plus focused DOM/static
  assertions.

### CB-B — Bob: budget runtime and model metadata

- Class: `repo_only`
- Allowed paths: `src/context_budget.py`, `src/context_compactor.py`,
  `src/model_context.py`, `src/settings.py`, `src/settings_registry.py`, and only
  the context-trim block in `src/agent_loop.py`.
- Deliverable: hierarchical resolution, one-time reserve accounting, DeepSeek V4
  context metadata, negative-budget guard, last-dialog protection, and structured
  trim diagnostics.
- Verification: focused context budget, compactor, model context, and agent-loop
  tests.

### CB-C — Charlie: integration and regression evidence

- Class: `safe_offline`, initially explorer; worker only after the backend API is
  stable.
- Allowed paths when promoted: new `tests/test_dynamic_context_budget.py` and
  `tests/test_context_dialog_preservation.py` only.
- Deliverable: regression coverage for the production 900/1024 failure, policy
  precedence, reserve semantics, DeepSeek V4 metadata, invalid settings, and
  previous-answer preservation.

### CB-D — Root: integration and rollout

- Class: `repo_only`, followed by `needs_live_go` for deploy.
- Deliverable: reconcile handoffs, run focused and relevant aggregate tests,
  inspect the final diff, and deploy only after explicit live authorization is
  already present or newly confirmed.

## Gate queue

- `CB-LIVE-DEPLOY`: `needs_live_go`; code deployment and production smoke require
  a clean scoped commit and explicit authorization. The earlier settings-only
  mitigation is already complete and is not this code-deploy gate.

## Verification

```powershell
node --check static/js/settings.js
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_context_budget.py tests\test_context_compactor.py tests\test_model_context.py tests\test_dynamic_context_budget.py tests\test_context_dialog_preservation.py -q
```

Then run the relevant agent-loop/settings tests selected from the final diff.

## Go language

- **Go:** focused tests pass, latest dialog pair is present, no negative budget is
  possible, and the diff contains only declared paths.
- **Partial:** backend safety is complete but admin UI or live smoke is pending.
- **No-Go:** prompt reserve is still double-counted or the latest answer can be
  silently removed.
- **Deferred:** production deployment awaits a separate live gate.
- **Blocked:** foreign hotfile conflict prevents a scoped integration.

## Execution result — 2026-07-13

- CB-A complete: admin UI loads, validates, and saves provider/model caps.
- CB-B complete: hierarchical runtime policy, one-time reserve accounting,
  DeepSeek V4 metadata, dialog-pair protection, tool-call repair, and structured
  diagnostics implemented.
- CB-C complete: production regression and integration coverage added.
- Root hardening complete: nested settings values are rejected unless both maps
  contain non-empty keys and positive JSON integers.
- Verification: 147 focused budget/settings/tool/UI tests plus 100 agent/chat/
  context tests passed; JS/Python syntax and `git diff --check` passed.
- DeepSeek V4 metadata source: official model table documents a 1M context
  window for Flash and Pro:
  <https://api-docs.deepseek.com/quick_start/pricing/>.
- Browser visual QA was unavailable because this execution environment exposed
  no browser backend. Static DOM/XSS, accessibility markup, syntax, and design
  detector checks passed for the new surface; detector findings elsewhere in
  `settings.js` predated this slice and were left untouched.
