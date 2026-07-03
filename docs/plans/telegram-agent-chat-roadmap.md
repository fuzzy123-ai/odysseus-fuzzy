# Telegram Agent Chat Roadmap

Status: backend complete; live smoke gated for the standalone Telegram plugin.

Goal: make Telegram a real external Odysseus agent-chat channel. A Telegram user
can send a message to the bot, Odysseus routes it into an agent chat session, the
agent responds through Telegram, and the plugin keeps a local redacted history.

For diagnostics language, "local redacted history" means persisted artifacts may
use stable redacted handles only. Raw chat IDs, sender IDs, file IDs, and token
values are out of bounds for stored diagnostic evidence.

Out of scope for this roadmap:
- Nextcloud or Obsidian archival. That stays a later integration phase.
- Video processing.
- Plugin marketplace or broad plugin-system refactors.
- Any token value in repo files, logs, tests, automation prompts, or handoffs.

## Current Baseline

- `plugins/telegram/plugin.py` exists as a standalone plugin.
- Local readiness, inbox/history, webhook ingest, bridge payloads, gated
  `telegram_reply`, and voice metadata intake are in place.
- Persisted Telegram diagnostics use stable redacted handles for chat, sender,
  voice, image and document identifiers. Raw identifier values are not
  acceptable in stored diagnostic evidence.
- Current test focus: `tests/test_telegram_plugin.py`,
  `tests/test_plugin_local_audit.py`, `tests/test_plugin_manifest_policy.py`.
- `v0.99.4` is the latest pushed baseline.
- 2026-07-03 verification:
  `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_telegram_plugin.py tests\test_plugin_local_audit.py tests\test_plugin_manifest_policy.py -q`
  returned `101 passed, 1 warning`.

## Done Definition

Telegram is considered implementation-ready when all are true:

1. The plugin can receive Telegram text messages by a configured safe intake
   mode.
2. Each allowed Telegram chat maps to one durable Odysseus session.
3. Text messages are passed to Odysseus agent chat processing without creating a
   second agent runtime.
4. Agent replies can be sent back through Telegram only when explicit local env
   gates are enabled.
5. Incoming, outgoing, blocked, failed, and voice-message events are recorded in
   persisted diagnostics only through stable redacted handles, not through raw
   Telegram identifiers.
6. Voice messages are accepted, stored as metadata, and marked for STT
   processing without blocking text-chat readiness.
7. Focused tests pass and no secret values are emitted.

## Environment Gates

- `TELEGRAM_BOT_TOKEN`: local bot token, never displayed.
- `TELEGRAM_ALLOWED_CHAT_IDS`: comma-separated allowlist for real operation.
- `TELEGRAM_AGENT_CHAT_ENABLED=true`: enables Telegram intake processing.
- `TELEGRAM_AGENT_REPLY_ENABLED=true`: allows outgoing Telegram replies.
- `TELEGRAM_POLLING_ENABLED=true`: optional polling service gate.
- `TELEGRAM_VOICE_ENABLED=true`: optional voice intake gate.

The plugin must stay useful in dry-run/test mode when gates are absent.

## Alice / Bob / Charlie Split

Alice owns operator language, runbooks, and Go/No-Go documentation only.

Allowed Alice paths:
- `docs/plans/telegram-agent-chat-roadmap.md`
- `docs/plans/telegram-agent-chat-operator-runbook.md`
- Optional links from existing Telegram docs under `docs/plans/telegram-*.md`

Primary operator doc:
- `docs/plans/telegram-agent-chat-operator-runbook.md`

Bob owns implementation models, plugin code, and focused tests.

Allowed Bob paths:
- `plugins/telegram/plugin.py`
- `tests/test_telegram_plugin.py`
- Optional helper module under `plugins/telegram/` if the plugin file becomes
  too large.

Charlie owns scope control, integration review, test execution, commits, tags,
pushes, and stop decisions.

Charlie allowed paths:
- Same as Alice/Bob only when integrating their work.
- `src/constants.py` for patch-version bumps.

## Slices

### TAI0 - Roadmap and Work Split

Status: done

Owner: Charlie.

Deliverables:
- This roadmap.
- Alice/Bob task prompts.
- One-minute monitor automation.

Tests:
- No runtime tests required unless code changes are made.

### TAI1 - Telegram Intake Runner

Status: done

Owner: Bob implementation, Alice operator docs, Charlie integration.

Goal:
- Add a safe intake runner that can process Telegram updates from either the
  existing webhook route or a gated polling loop.

Requirements:
- Polling must be off unless `TELEGRAM_POLLING_ENABLED=true`.
- Do not run polling in tests against the network.
- Store update offset locally.
- Reject or quarantine chat IDs not in `TELEGRAM_ALLOWED_CHAT_IDS`.
- Record local history for accepted, duplicate, blocked, and unsupported updates.

Expected paths:
- Bob: `plugins/telegram/plugin.py`, `tests/test_telegram_plugin.py`
- Alice: `docs/plans/telegram-agent-chat-operator-runbook.md`

### TAI2 - Odysseus Session Bridge

Status: done

Owner: Bob implementation, Alice operator docs, Charlie integration.

Goal:
- Map each Telegram chat ID to a durable Odysseus session alias and route text
  messages into the existing Odysseus chat/agent path.

Requirements:
- Do not create a parallel agent runtime.
- Keep a `telegram_chat_id -> session_id` mapping in plugin data.
- Create a session when needed, or reuse the existing one.
- Return a structured bridge result: session id, agent input, readiness, and
  whether a reply is expected.
- Tests must use fakes/stubs for session and agent calls.

Expected paths:
- Bob: `plugins/telegram/plugin.py`, `tests/test_telegram_plugin.py`
- Alice: runbook language for how the operator validates session mapping.

### TAI3 - Telegram Reply End-to-End

Status: done

Owner: Bob implementation, Alice operator docs, Charlie integration.

Goal:
- Complete the reply path from agent response to Telegram `sendMessage`.

Requirements:
- Reply sends only when `TELEGRAM_AGENT_REPLY_ENABLED=true`.
- Chat ID must be allowed.
- Token and chat ID values must not appear in outputs or logs.
- Store outbound successes and failures in local history.
- Add retry/backoff only as bounded logic; no unbounded loops.
- Tests must monkeypatch the HTTP sender and verify no real network is used.

Expected paths:
- Bob: `plugins/telegram/plugin.py`, `tests/test_telegram_plugin.py`
- Alice: operator checklist for enabling/disabling reply mode.

### TAI4 - Voice Intake and STT Handoff

Status: done

Owner: Bob implementation, Alice operator docs, Charlie integration.

Goal:
- Accept Telegram voice messages as first-class inbox entries and prepare them
  for later transcription.

Requirements:
- Voice intake is gated by `TELEGRAM_VOICE_ENABLED=true` for processing beyond
  metadata.
- Store file metadata and a `pending_stt` state.
- Do not download voice files by default unless a later explicit gate is added.
- Text chat readiness must not depend on voice/STT completion.

Expected paths:
- Bob: `plugins/telegram/plugin.py`, `tests/test_telegram_plugin.py`
- Alice: user-facing note that voice is accepted but may be pending STT until
  the operator enables the next processing stage.

### TAI5 - Manual Live Smoke Evidence

Status: live-gated

Owner: Charlie, with user Go.

Goal:
- Verify real Telegram chat loop after local env is set and the user gives
  explicit Go.

Manual evidence:
- Bot receives one text message from an allowed chat.
- Odysseus creates or reuses the mapped session.
- Odysseus agent answer is sent back to Telegram.
- Local history shows inbound and outbound entries without token leakage.

Do not run this automatically.

## Stop Rules

- Stop on any token/secret persistence or display.
- Stop if raw chat IDs, sender IDs, or file IDs are described as acceptable
  persisted diagnostics.
- Stop on plugin-system-wide changes outside the allowed plugin scope.
- Stop on unbounded polling, unbounded retry, or unbounded history payloads.
- Stop if tests require real Telegram network access.
- Stop on foreign staged files or unrelated dirty worktree conflicts.
- Stop on red tests without a focused fix.
