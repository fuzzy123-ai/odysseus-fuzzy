# Telegram Plugin Route and Command Inventory

Status: TGR1 inventory

Source scope: current working tree observations from
`docs/plans/telegram-plugin-refactor-roadmap.md`, `plugins/telegram/plugin.py`
and package-level Telegram env-name search. This document intentionally records
surface names, gates and migration checks only. It must not contain token
values, chat ids, file ids, private message text or raw Telegram payloads.

## Route Inventory

All routes are registered under `APIRouter(prefix="/api/plugins/telegram")`
and call the admin gate before doing work.

| Route | Method | Current responsibility | Primary gates and safety notes | Refactor target |
| --- | --- | --- | --- | --- |
| `/status` | `GET` | Returns `build_telegram_readiness(ctx.data_dir)`. | Admin-only. Readiness reports presence/booleans, not secret values. | `routes_status.py` or admin/status service. |
| `/history` | `GET` | Returns local redacted inbox/history from `TelegramInboxStore.history`, optionally filtered by chat id and limit. | Admin-only. Keep redacted store contract; do not expose raw identifiers beyond existing public history shape. | `routes_history.py` or admin/history service. |
| `/poll` | `POST` | Runs `run_telegram_polling_cycle` in a worker thread with context-provided providers and handlers. | Admin-only. `TELEGRAM_POLLING_ENABLED` is enforced in polling implementation via callback. Outbound replies still pass through reply/document gates. | `routes_polling.py` plus polling composition service. |
| `/webhook` | `POST` | Parses Telegram update, appends redacted inbound message, runs voice/image/attachment pipelines, handles control commands, export/project-intake flows, agent turn, typing pulse and gated replies. | Admin-only. Must keep parse redaction, allowlist check, default-off live/download/send behavior, and `token_value_visible: False`. This is the highest-risk split surface. | `routes_webhook.py` orchestrating smaller inbound services. |
| `/reply` | `POST` | Sends a text reply through `_reply_with_gate`. | Admin-only. Requires `TELEGRAM_AGENT_REPLY_ENABLED` and allowed chat id. Also applies secure-channel policy and truth gate. | `routes_outbound.py` / outbound delivery service. |
| `/document-reply` | `POST` | Sends reviewed local artifact or server-side Nextcloud artifact through `_telegram_document_reply_tool`. | Admin-only. Requires reply gate and allowed chat id for live dispatch. Artifact refs are constrained to allowed repo-relative roots; Nextcloud path is server-side. | `routes_document_reply.py` / artifact delivery service. |
| `/document-reply/preview` | `POST` | Builds redacted delivery preview without live Telegram dispatch. | Admin-only. Preview returns redacted dispatch readiness and must not fetch/send unless current tool logic explicitly requires non-preview path. | `routes_document_reply.py` / preview service. |
| `/document-reply/live-gate` | `POST` | Builds screenshot/photo live-gate packet from preview delivery packet. | Admin-only. Requires a photo artifact delivery packet. Does not itself dispatch to Telegram. | `routes_document_reply.py` / live-gate packet service. |
| `/app` | `GET` | Returns Telegram plugin UI HTML. | Admin-only. Uses CSP nonce from request state. | `routes_app.py` or admin app route. |

## Registered Tools

Tool registration is best-effort inside `setup(ctx)` and logs a warning if the
tool registry is unavailable.

| Tool | Permission | Required inputs | Current behavior | Gates and redaction checks |
| --- | --- | --- | --- | --- |
| `telegram_reply` | `admin` | `chat_id`, `text` | Sends text via `_telegram_reply_tool` and `_reply_with_gate`. | Requires `TELEGRAM_AGENT_REPLY_ENABLED` and allowed chat id. Applies optional classification/security mode, truth gate, and avoids token exposure. |
| `telegram_document_reply` | `admin` | `chat_id` plus one of `artifact_ref` or `nextcloud_path` | Sends a reviewed artifact as document/photo or produces preview when `preview_only` is true. | `artifact_ref` must stay under allowed repo roots; `nextcloud_path` is fetched server-side; preview redacts target/path values; live dispatch uses reply gate and allowed chat id. |
| `odysseus_notify_user` | `admin` | `message` | Builds a user notification decision and optionally dispatches through Telegram. | Defaults to dry-run contract behavior. Rejects token/secret/chat-target arguments. Live dispatch depends on `TELEGRAM_AGENT_REPLY_ENABLED` and server-side target configuration. |

## Command Families

Commands are detected before normal agent-turn handling. The webhook and polling
surfaces both route through `_telegram_control_command` and
`_handle_telegram_control_command`.

| Family | Canonical internal commands observed | Current responsibility | Migration checks |
| --- | --- | --- | --- |
| DSGVO/privacy | `dsgvo_enable`, `dsgvo_disable`, `dsgvo_toggle`, `dsgvo_status`, `dsgvo_help` | Toggles or reports local DSGVO mode; may sync Telegram privacy pin state. | Preserve forced-active behavior, local-only wording, pin/unpin side effects and pin-store events. |
| Agent task control | `agent_task_help`, `agent_task_status`, `agent_task_pause`, `agent_task_resume`, `agent_task_cancel` | Reads latest task ledger record and records pause/resume/cancel requests. | Preserve public task record shape and avoid exposing raw task content. |
| Calendar/reminders/todo | `calendar_readiness`, `calendar_agenda`, `calendar_reminders_status`, `calendar_todo_status`, `calendar_reminder_create`, `calendar_reminder_update`, `calendar_todo_digest_create` | Reads calendar readiness/agenda and writes reminder or todo-digest requests. | Characterize tail parsing, owner propagation, write result wording and blocked/error statuses. |
| Universal Inbox readiness | `universal_inbox_status` | Returns Universal Inbox readiness formatted for Telegram. | Preserve readiness snapshot shape and reply dispatch through gate only. |
| Universal Inbox review | `universal_inbox_review_status`, `universal_inbox_review_confirm` | Reports latest attachment review or confirms dry-run/live Nextcloud transfer plan. | Preserve review lookup, event append fields, dry-run default, live-write dual gate and no credential-in-Telegram wording. |
| Universal Inbox memory review | `universal_inbox_memory_review_status`, `universal_inbox_memory_review_confirm` | Reports/latest memory review or executes redacted memory write confirmation. | Preserve redacted write intent, event counts, raw-content flags and blocked reason handling. |
| Project intake | `project_intake_review_confirm`, `project_intake_review_hold`, project-intake status fallback | Applies or holds latest project intake review. | Preserve registry path injection, apply/hold event fields and no raw project text in public response. |
| Session control | `new_chat` | Rebinds a Telegram chat to a new agent session. | Preserve session alias/scope behavior and pending-bridge status. |
| Attachment export and project intake text triggers | Text-driven handlers after control commands | `execute_recent_telegram_attachment_export` and `build_telegram_project_intake_preview` can intercept text messages before the agent turn. | Characterize exact trigger phrases in focused tests before moving; keep their precedence after control commands and before normal agent turns. |

## Env Gates And Limits

Document names only, never values.

Core routing/readiness gates:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `TELEGRAM_ALLOWED_CHAT_IDS`
- `TELEGRAM_AGENT_CHAT_ENABLED`
- `TELEGRAM_AGENT_REPLY_ENABLED`
- `TELEGRAM_POLLING_ENABLED`

Formatting/UI gates:
- `TELEGRAM_RICH_MESSAGES_ENABLED`
- `TELEGRAM_RICH_DRAFTS_ENABLED`
- `TELEGRAM_DRAFT_INTERVAL_MS`
- `TELEGRAM_MAX_REPLY_CHUNKS`
- `TELEGRAM_TYPING_KEEPALIVE_SECONDS`

Voice gates and limits:
- `TELEGRAM_VOICE_DOWNLOAD_ENABLED`
- `TELEGRAM_VOICE_STT_ENABLED`
- `TELEGRAM_STT_ENABLED`
- `TELEGRAM_VOICE_MAX_BYTES`

Attachment, image and document gates/limits:
- `TELEGRAM_IMAGE_ACTIONS_ENABLED`
- `TELEGRAM_ATTACHMENT_MAX_BYTES`
- `TELEGRAM_ATTACHMENT_CONTEXT_TTL_SECONDS`
- `TELEGRAM_ATTACHMENT_CONTEXT_MAX_CHARS`
- `TELEGRAM_ATTACHMENT_CONTEXT_MAX_EXTRACT_BYTES`
- `TELEGRAM_NEXTCLOUD_MAX_FILE_BYTES`

Privacy, notification and write gates:
- `TELEGRAM_PRIVACY_PIN_DISABLED`
- `TELEGRAM_NOTIFICATION_CHAT_ID`
- `UNIVERSAL_INBOX_NEXTCLOUD_LIVE_WRITE_ENABLED`
- `UNIVERSAL_INBOX_NEXTCLOUD_OPERATOR_LIVE_GO`
- `TELEGRAM_NEXTCLOUD_LIVE_WRITE_ENABLED`
- `TELEGRAM_MEMORY_AUTO_WRITE_ENABLED`

Notes:
- The plugin entry currently uses the `UNIVERSAL_INBOX_NEXTCLOUD_*` dual gate
  for the review-confirmed Nextcloud transfer path.
- Package status/readiness code also reports `TELEGRAM_NEXTCLOUD_LIVE_WRITE_ENABLED`
  and `TELEGRAM_MEMORY_AUTO_WRITE_ENABLED`; verify actual call sites before
  changing behavior.

## Migration Checklist

Before moving any surface:
- Add or identify characterization tests for route status codes, public payload
  keys and event append shape.
- Confirm the target module receives dependencies explicitly instead of reading
  broad context state ad hoc.
- Preserve admin gate calls on every route.
- Preserve chat allowlist behavior and never log or return raw chat ids, file
  ids, tokens, private message text or raw Telegram update payloads.
- Preserve default-off behavior for polling, outbound replies, rich messages,
  voice download/STT, image actions and live Nextcloud writes.
- Preserve `token_value_visible: False`, raw-content flags and raw-identifier
  flags in public packets.
- Keep route paths, methods, tool names, tool permissions and required
  parameters stable until a separate behavior-change Go exists.
- Keep control-command precedence: parse/store, run safe intake processors,
  handle control commands, then export/project-intake intercepts, then agent
  turn.
- Run focused static or test verification after each move, with no live Telegram
  sends/downloads unless the corresponding operator Go is granted.

Per route/tool:
- `/status`: compare readiness keys before/after; ensure env values remain
  boolean/presence-only.
- `/history`: compare redacted message shape and limit/filter handling.
- `/poll`: preserve `TELEGRAM_POLLING_ENABLED` refusal behavior and injected
  fake providers for tests.
- `/webhook`: split last; first characterize invalid update, normal text, voice,
  attachment, control command, export, project-intake and agent-turn branches.
- `/reply`: preserve reply gate, allowlist failure events, secure-channel policy,
  truth gate and rich-message fallback.
- `/document-reply`: preserve artifact path validation, preview-only behavior,
  photo-vs-document selection and server-side Nextcloud handling.
- `/document-reply/preview`: ensure preview cannot expose raw target/path values.
- `/document-reply/live-gate`: ensure non-photo packets remain blocked and live
  gate packet stays dispatch-free.
- `/app`: preserve CSP nonce and admin gate.
- `telegram_reply`: keep tool schema stable and route through the same gate as
  `/reply`.
- `telegram_document_reply`: keep schema stable; characterize artifact and
  Nextcloud preview branches separately.
- `odysseus_notify_user`: preserve dry-run default, server-side target lookup
  and rejection of secret/target arguments.

Handoff risks:
- `plugins/telegram/plugin.py` currently remains a broad orchestrator; the
  webhook branch has many implicit ordering dependencies.
- Exact user-facing slash aliases live behind parsing/export helpers and should
  be frozen by TGR2 tests before code movement.
- There are parallel working-tree changes in this repository; future slices
  should re-check hot files and staged files before editing.
