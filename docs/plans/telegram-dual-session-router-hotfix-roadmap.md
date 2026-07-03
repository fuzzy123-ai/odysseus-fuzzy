# Telegram Dual Session Router Hotfix Roadmap

Status: **repo implemented / live smoke gated**

Mode: **Standard ABC, backend/logik-first**

Priority: **high**

Related:

- `plugins/telegram/stores.py`
- `plugins/telegram/plugin.py`
- `plugins/telegram/polling.py`
- `app.py`
- `src/sensitivity_delegation_gate.py`
- `src/secure_provider_runtime.py`
- `tests/test_telegram_plugin.py`

## Problem

Telegram can currently return the unhelpful block message:

> DSGVO-Modus ist aktiv. Telegram kann diese Anfrage nur mit einem lokalen
> Modell verarbeiten. Die aktuelle Telegram-Session ist nicht local-only...

That message is safe, but it is a bad runtime experience. The bot should route
the request to the correct session automatically whenever possible.

## Goal

Each Telegram chat gets two persistent Odysseus session slots:

- `normal_session_id`: default/API-capable session for normal requests.
- `secure_session_id`: local-only session for DSGVO, sensitive attachments,
  raw voice transcripts, and local-only workflows.

The bot routes each incoming Telegram request to the correct slot and should not
show the current "session is not DSGVO conform" block message unless the local
model/session cannot be created or is unavailable.

## Non-Goals

- No UI work.
- No DeepSeek sensitive-subagent orchestration in this hotfix.
- No live Telegram mutation without explicit Go.
- No raw Telegram chat IDs, private message content, host paths, tokens, or
  secrets in tests/docs/logs.
- No rewrite of the Universal Inbox pipeline.

## Current Evidence

- `build_agent_bridge_request()` already computes `local_only_required` and
  `sensitivity_delegation`.
- `_telegram_agent_turn_handler()` already enforces the provider gate before
  model calls.
- `_telegram_rebind_local_session()` tries to repair a non-local current
  session, but the store still has only one effective active session per chat.
- The current failure path still returns a manual local-model instruction.
- 2026-07-03: `TelegramSessionBridgeStore` now keeps separate
  `normal_session_id` and `secure_session_id` slots with legacy `session_id`
  compatibility. Telegram bridge payloads carry `desired_session_scope`, and
  webhook/polling session binding selects `secure` whenever DSGVO or sensitivity
  metadata requires local-only handling.
- Focused verification passed:
  `venv\Scripts\python.exe -m pytest tests\test_telegram_plugin.py tests\test_secure_provider_runtime.py tests\test_sensitivity_delegation_gate.py tests\test_telegram_attachment_ocr.py -q`
  -> `99 passed, 1 warning`.

## Proposed Design

### Session Slots

Extend `TelegramSessionBridgeStore` mapping to support:

```json
{
  "chat_handle": "redacted",
  "normal_session_id": "session...",
  "secure_session_id": "session...",
  "active_session_id": "session...",
  "last_selected_scope": "normal|secure",
  "session_alias": "telegram:...",
  "recommended_session_name": "Telegram ..."
}
```

Backward compatibility:

- Existing `session_id` is treated as `normal_session_id` until migrated.
- Existing callers that read `session_id` continue to receive the selected
  `active_session_id`.

### Routing Rule

`desired_session_scope = secure` when any of these is true:

- global DSGVO mode is active;
- `local_only_required` is true;
- `sensitivity_delegation.local_worker_required` is true;
- recent attachment policy requires local-only;
- raw voice transcript is part of the turn.

Otherwise:

- `desired_session_scope = normal`.

### Creation/Repair

When the selected slot is missing:

- create the required session automatically;
- normal slot uses configured Telegram/default model policy;
- secure slot uses configured local Telegram model, e.g. `telegram_model_spec`;
- record only redacted slot/scope status.

When the secure slot exists but fails provider gate:

- create/repair a new secure session;
- retry provider gate once;
- if still failing, return a useful local-model readiness message, not a generic
  "current session is not DSGVO conform" message.

## Slice Queue

| Slice | Class | Owner | Goal | Done Criteria |
| --- | --- | --- | --- | --- |
| TDS-1 Store Contract | repo_only | Bob | Add dual-slot mapping with backward compatibility | done: Store can create/read/rebind normal and secure sessions; legacy `session_id` still works |
| TDS-2 Scope Resolver | repo_only | Bob | Compute `desired_session_scope` from DSGVO/sensitivity metadata | done: Text, voice, attachment, and DSGVO cases resolve deterministically |
| TDS-3 Session Selection | repo_only | Bob | Bridge request carries selected session and scope | done: Agent handler receives already-selected session_id plus scope metadata |
| TDS-4 Secure Auto-Repair | repo_only | Bob | Replace manual block path with automatic secure-session repair | done: Provider-gate failure creates/repairs the secure slot and retries once |
| TDS-5 Helpful Failure State | repo_only | Alice/Bob | Replace current block message for unrecoverable local model failure | done for normal recovery path; unrecoverable local-model failures still return explicit local readiness guidance |
| TDS-6 Regression Tests | safe_offline | Charlie | Prove normal/secure routing and no raw leak | done: Focused Telegram and secure-routing tests pass |
| TDS-7 Live Telegram Smoke | needs_live_go | Charlie | Confirm with a real Telegram message | Requires explicit live Go; no private content in logs |

## Tests

Required focused tests:

- Existing single-slot mapping migrates to normal slot.
- Normal text request chooses `normal_session_id`.
- DSGVO text request chooses `secure_session_id`.
- Sensitive recent attachment follow-up chooses `secure_session_id`.
- Voice transcript in DSGVO mode chooses `secure_session_id`.
- Secure session missing triggers automatic creation.
- Secure session with external provider triggers repair/retry once.
- External/API session never receives raw sensitive attachment context.
- Failure reply does not contain "aktuelle Telegram-Session ist nicht local-only"
  unless no safe local route exists and the error is diagnostic-only.

## Gate Queue

Gate: `live-telegram-dual-session-smoke`

Class: `needs_live_go`

Blocks: Real Telegram verification after repo tests.

Decision needed: Explicit Go to send or process a real Telegram test message.

Safe preparation done: Store, routing, and tests can be completed offline.

Risk if bypassed: Repo tests prove routing logic, not bot-token/live polling
behavior.

## Done Definition

- Telegram mapping supports normal and secure session slots.
- Session selection is automatic per request.
- DSGVO/sensitive requests do not reuse API/default session.
- Normal requests do not inherit secure/local-only history unless required.
- The current "session is not DSGVO conform" reply no longer appears as the
  normal recovery path.
- Focused Telegram tests are green.
- Live smoke is still gated until a real Telegram test message is sent and
  redacted evidence is recorded.

## Recommended First Implementation Slice

Run `TDS-7 Live Telegram Smoke` after the next Debian deploy or scheduled update
brings this backend patch live. Test one normal Telegram text turn and one DSGVO
or sensitive-file follow-up; evidence must show only redacted session scope and
delivery status, not message content or chat ids.
