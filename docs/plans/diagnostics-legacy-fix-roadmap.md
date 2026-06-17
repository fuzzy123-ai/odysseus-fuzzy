# Diagnostics and Legacy Fix Roadmap

## Goal

Bring Odysseus diagnostics, measurement, and legacy boundaries back to a release-safe state: no misleading readiness signals, no raw Telegram identifiers in persisted diagnostic artifacts, and no high-risk legacy tool-dispatch ambiguity.

## Current Evidence

- Latest pushed integration state: `v0.99.6` on `fuzzy/dev`.
- Charlie tool/plugin verification: `95 passed, 39 warnings`.
- Alice diagnostic review status: `needs_fix`.
- Bob legacy audit status: `needs_fix`.
- No P0 issue was reported, but Telegram redaction and tool-dispatch ambiguity are priority fixes before further release confidence claims.

## Non-Goals

- No live Telegram sends, Bot API polling, provider calls, export/import/rebuild runs, host commands, or network actions.
- No Nextcloud, Obsidian archival, video, or STT runtime work.
- No broad plugin-system rewrite.
- No destructive git or history rewrite.

## Stop Rules

- Stop on real token, chat ID, provider secret, or raw private identifier being added to docs, tests, automation prompts, logs, or persisted diagnostic examples.
- Stop on unrelated dirty files or foreign staged files.
- Stop on plugin/runtime/network activation without explicit user approval.
- Stop on red tests unless the fix is focused and inside the active slice.
- Stop on architecture changes larger than the current slice.

## Slices

### DLF0-roadmap-and-monitor

Owner: Charlie

Scope:
- `docs/plans/diagnostics-legacy-fix-roadmap.md`

Exit:
- Roadmap exists.
- Alice and Bob receive complete path-scoped prompts.
- A 1-minute Charlie monitor is active.

### DLF1A-operator-diagnostics-language

Owner: Alice

Scope:
- `docs/plans/diagnostics-legacy-fix-roadmap.md`
- `docs/plans/telegram-agent-chat-operator-runbook.md`
- `docs/plans/telegram-agent-chat-roadmap.md`
- optionally `docs/plans/1.0-release-decision-bundle.md`

Goal:
- Tighten operator-facing wording so "redacted history", "ready", and "green automated gate" never overstate the current implementation.

Exit:
- Docs clearly distinguish stable redacted handles from raw identifiers.
- Static release baselines are described as documented baselines, not fresh live measurements.
- Next actions point to DLF1B and later DLF2/DLF3.

### DLF1B-telegram-history-redaction-fix

Owner: Bob

Scope:
- `plugins/telegram/plugin.py`
- `tests/test_telegram_plugin.py`
- optional small helper under `plugins/telegram/`

Goal:
- Ensure persisted Telegram history/session artifacts do not store raw `chat_id`, `sender.id`, `voice.file_id`, or `voice.file_unique_id`.

Exit:
- Persisted history uses stable redacted handles for operator correlation.
- Reply gating can still use runtime chat IDs from the current request, but stored artifacts remain redacted.
- Tests prove raw identifiers do not survive persistence.

### DLF2-plugin-tool-typeerror-boundary

Owner: Bob

Scope:
- `src/tool_registry.py`
- `tests/test_tool_registry.py`

Goal:
- Prevent plugin tool `TypeError` bugs from being misclassified as legacy args-dict signature mismatches.

Exit:
- Legacy fallback remains only for genuine old-style handlers.
- A tool body raising `TypeError` fails once and exposes the real failure.
- Tests cover both paths.

### DLF3-release-readiness-language-binding

Owner: Charlie with Alice support

Scope:
- `src/release_readiness_pipeline.py`
- `tests/test_release_readiness_pipeline.py`
- relevant release docs if wording is needed

Goal:
- Make static automated gates visibly documented baselines unless they are bound to fresh dated evidence.

Exit:
- Operators cannot confuse static PASS records with fresh automated measurements.
- Existing release tests remain green with updated semantics.

### DLF4-plugin-audit-metric-rename

Owner: Charlie

Scope:
- `src/plugin_local_audit.py`
- `tests/test_plugin_local_audit.py`

Goal:
- Rename or clarify `loaded_count` so it does not imply runtime-loaded plugin count when it means entrypoint/discoverable count.

Exit:
- Internal and operator-facing names no longer suggest false runtime state.
- Backward compatibility is preserved if existing tests or consumers require it.

### DLF5-legacy-tool-stack-consolidation-plan

Owner: Bob

Scope:
- `src/tool_registry.py`
- `src/tool_schemas.py`
- `src/agent_tools/__init__.py`
- `src/tool_execution.py`
- relevant tool tests

Goal:
- Produce a focused consolidation plan for the legacy tool stack without broad rewrites.

Exit:
- Canonical source of truth and compatibility shims are documented in code/tests.
- Any implementation work is split into later small slices.

## Verification

Run after DLF1-DLF4:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_telegram_plugin.py tests\test_tool_registry.py tests\test_plugin_local_audit.py tests\test_release_readiness_pipeline.py tests\test_plugin_system.py tests\test_plugin_manifest_policy.py
```

Run before final push:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_tool_registry.py tests\test_tool_rag_keyword_hints.py tests\test_tool_policy.py tests\test_delegate_tool.py tests\test_update_plan_tool.py tests\test_plugin_system.py tests\test_plugin_local_audit.py tests\test_plugin_manifest_policy.py tests\test_telegram_plugin.py tests\test_release_readiness_pipeline.py
```

## Go Language

- `go`: DLF1-DLF4 complete, wording no longer overstates readiness, Telegram diagnostics promise only stable redacted handles, and documented baseline gates are clearly labeled as non-live.
- `partial`: DLF1 wording is corrected, but one or more implementation slices such as DLF1B-DLF4 are still pending and operators must treat readiness as constrained.
- `no_go`: raw Telegram identifiers still persist, plugin tool failures can still be misclassified or double-run, static baselines are still presented like fresh green measurements, or focused tests are red.
- `deferred`: DLF5 consolidation plan remains planned-only and intentionally does not block the immediate diagnostic safety fix.
