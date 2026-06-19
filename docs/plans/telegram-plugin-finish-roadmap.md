# Telegram Plugin Finish Roadmap

Status: offline implementation-ready after operator release of the dirty
`plugins/telegram/plugin.py` baseline; manual live smoke remains gated by an
explicit operator Go.

Goal: finish Telegram as a standalone Odysseus plugin so an allowed Telegram
chat can send text to Odysseus, receive the agent answer through the bot, keep a
redacted local history, and expose clear readiness/live-smoke evidence without
persisting secrets or raw Telegram identifiers.

## Current Evidence

- `plugins/telegram/plugin.py` exists as a standalone plugin with manifest,
  local UI, readiness route, webhook intake, polling cycle support, session
  bridge store, gated reply tool, redacted history, and voice metadata intake.
- `tests/test_telegram_plugin.py` covers manifest visibility, redacted
  readiness, route setup, file-loader import, text parsing, voice pending STT,
  history redaction, webhook bridge, gated replies, session reuse, polling, and
  outbound success/failure history.
- The dirty `plugins/telegram/plugin.py` baseline was explicitly released by
  the operator for the finish fix.
- `plugins/telegram/plugin.py` now keeps Telegram as standalone plugin version
  `0.2.1`, calls an explicit admin gate for plugin routes, handles malformed
  updates without raw payload leakage, records blocked chats with redacted
  handles, and supports an injected Odysseus agent-turn hook with gated replies.
- Focused verification: `tests/test_telegram_plugin.py`,
  `tests/test_plugin_local_audit.py`, and
  `tests/test_plugin_manifest_policy.py` -> `40 passed, 1 warning`.
- Current app version observed in `src/constants.py`: `0.99.6`.

## Non-Goals

- No Nextcloud or Obsidian archival in this track.
- No video processing.
- No broad plugin-system refactor.
- No automatic live Telegram send without explicit user Go.
- No token, chat ID, sender ID, file ID, or private provider output in docs,
  tests, logs, automation prompts, or persisted diagnostics.

## Stop Rules

- Stop on hotfile conflict around `plugins/telegram/plugin.py`.
- Stop on foreign staged files or unclear ownership of existing dirty changes.
- Stop if a real token, chat ID, sender ID, or Telegram file ID would be
  persisted, logged, or copied into docs/tests/prompts.
- Stop on unbounded polling, retry loops, history dumps, or network calls in
  tests.
- Stop on plugin-system-wide changes outside the Telegram plugin boundary.
- Stop on red tests without a narrow fix.
- Never use destructive git commands.

## ABC Ownership

Alice owns operator language, runbooks, Go/No-Go wording, live-smoke evidence
language, and secret-hygiene instructions.

Allowed Alice paths:
- `docs/plans/telegram-plugin-finish-roadmap.md`
- `docs/plans/telegram-agent-chat-operator-runbook.md`
- Optional links in `docs/plans/telegram-*.md`

Bob owns read-only gap analysis first. Code implementation is blocked until the
dirty Telegram hotfile is released.

Allowed Bob paths for the first slice:
- `tests/test_telegram_plugin.py` read-only
- `plugins/telegram/plugin.py` read-only
- `docs/plans/telegram-plugin-finish-roadmap.md` read-only

Future Bob write paths after hotfile release:
- `plugins/telegram/plugin.py`
- `tests/test_telegram_plugin.py`
- Optional small helper under `plugins/telegram/`

Charlie owns scope control, current dirty-state assessment, integration, focused
tests, commits, version bump decisions, push, live-smoke stop decision, and
automation lifecycle.

## Slices

### TPF0 - Finish Roadmap and Hotfile Guard

Owner: Charlie.

Done when:
- This roadmap exists.
- Alice and Bob receive scoped tasks.
- A one-minute Charlie monitor is active.
- The monitor knows not to let Bob edit `plugins/telegram/plugin.py` while it is
  already dirty.

Tests: none, docs-only.

### TPF1 - Operator Finish Contract

Owner: Alice.

Goal:
- Make the operator-facing finish criteria explicit.

Requirements:
- Define "Telegram ready", "manual live-smoke ready", "No-Go", and "Deferred".
- Document required local env gates without values:
  `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_IDS`,
  `TELEGRAM_AGENT_CHAT_ENABLED`, `TELEGRAM_AGENT_REPLY_ENABLED`,
  `TELEGRAM_POLLING_ENABLED`, and optional `TELEGRAM_VOICE_ENABLED`.
- Explain that Telegram appears as a standalone plugin UI entry and that agent
  chat replies are only live after explicit reply-gate enablement.
- Keep Nextcloud/Obsidian archival as future work.

Tests: none, docs-only.

### TPF2 - Implementation Gap Audit

Owner: Bob.

Goal:
- Produce a precise gap list between the existing plugin/tests and the finish
  definition before any code changes.

Requirements:
- Read `plugins/telegram/plugin.py` and `tests/test_telegram_plugin.py`.
- Do not edit files while `plugins/telegram/plugin.py` is dirty.
- Check whether these are covered:
  plugin list visibility, admin-gated UI, webhook/polling intake, allowed-chat
  gate, durable session reuse, real agent-turn bridge hook, reply tool/route,
  outbound history redaction, failure handling, voice pending STT, file-loader
  import, and no network in tests.
- Return exact missing test names or code paths.

Tests: none in the read-only slice.

### TPF3 - Hotfile Release Gate

Owner: Charlie.

Status: done.

Goal:
- Decide whether the existing dirty Telegram plugin state is operator-owned,
  already intended, or needs separate commit/review before implementation.

Go:
- The dirty Telegram plugin state is committed, explicitly accepted as baseline,
  or replaced by a known safe patch.

No-Go:
- The dirty state is unclear, contains secrets, or overlaps with unrelated
  plugin/runtime changes.

### TPF4 - Agent Chat Roundtrip Closure

Owner: Bob implementation, Charlie integration.

Status: done offline with injected agent-turn handler and gated reply tests.

Goal:
- Ensure a text Telegram update can become one Odysseus session turn and that
  the selected agent response can be sent back via the gated Telegram reply path.

Requirements:
- No parallel agent runtime.
- Existing session/chat mechanisms must be reused through a narrow bridge hook.
- Tests must use fakes/stubs, not real Telegram network.
- Raw chat IDs may exist transiently in request handling but not in persisted
  history or diagnostic payloads.

Focused tests:
- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_telegram_plugin.py`

### TPF5 - Plugin Surface and Readiness Evidence

Owner: Bob implementation, Alice docs, Charlie integration.

Status: done offline; route-level admin gate and manifest/local audit tests are
green.

Goal:
- Confirm the plugin shows up in the plugin list/UI with a clear readiness
  surface and no misleading "ready" text.

Requirements:
- Manifest remains standalone under Communications.
- UI route remains admin-scoped.
- Readiness distinguishes token present, allowed chat marker present, intake
  enabled, reply enabled, polling enabled, and history counts.
- No token/chat values visible.

Focused tests:
- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_telegram_plugin.py tests\test_plugin_local_audit.py tests\test_plugin_manifest_policy.py`

### TPF6 - Voice Pending STT Boundary

Owner: Bob implementation, Alice docs, Charlie integration.

Status: done offline; voice remains metadata-only and pending STT.

Goal:
- Keep voice messages accepted as metadata and explicitly pending STT without
  blocking text-chat readiness.

Requirements:
- No file download by default.
- No transcription network/provider call.
- Voice file identifiers are redacted in persisted history.
- Operator wording says voice is accepted but not transcribed until a later STT
  gate is implemented.

Focused tests:
- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_telegram_plugin.py`

### TPF7 - Manual Live Smoke Evidence

Owner: Charlie, only after explicit user Go.

Status: pending explicit operator Go.

Goal:
- Verify one real allowed Telegram text roundtrip.

Manual evidence:
- One inbound Telegram message is received.
- The mapped Odysseus session is created or reused.
- One agent answer is sent through Telegram.
- Local history contains inbound/outbound records without raw token/chat values.

Never run this automatically.

## Verification

Minimum automated verification before declaring implementation-ready:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_telegram_plugin.py tests\test_plugin_local_audit.py tests\test_plugin_manifest_policy.py
```

Final integration verification after any bridge or plugin-surface changes:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_telegram_plugin.py tests\test_plugin_local_audit.py tests\test_plugin_manifest_policy.py tests\test_agent_team_card_api.py
```

## Release Language

Go:
- Text Telegram agent chat works through an allowed chat, redacted history, and
  gated reply path; focused tests pass; manual live smoke has user-approved
  evidence.

Partial:
- Plugin and offline tests are ready, but live send-smoke remains pending.

No-Go:
- Secret leakage, raw persisted identifiers, plugin not visible, no durable
  session bridge, reply path bypasses gate, or tests require real network.

Deferred:
- Voice transcription, video processing, Nextcloud/Obsidian archival, and broad
  plugin marketplace/system improvements.
