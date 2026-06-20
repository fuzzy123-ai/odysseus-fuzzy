# Telegram Plugin Rich Streaming Roadmap

## Summary

This roadmap extends the Telegram plugin from a gated text bridge into a faster, better formatted personal-assistant channel with true Telegram rich-message draft previews.

The current Telegram path sends bot replies through classic `sendMessage` as plain text, so Markdown is visible as raw formatting. Webhook and polling paths also wait for the full agent turn before the user sees a final answer. The target state is:

- Telegram replies render with safe rich formatting.
- The bot understands the user goal before selecting tools, without requiring explicit tool names.
- Long-running turns show a real streaming preview through Telegram rich message drafts.
- Final answers are persisted through rich messages, with classic text delivery as a safe fallback.

Telegram Bot API references:

- `sendMessage` supports `parse_mode` and `entities`.
- `InputRichMessage` accepts exactly one of `html` or `markdown`.
- `sendRichMessageDraft` streams an ephemeral 30-second preview and must be followed by a final `sendRichMessage`.

## Roadmap

### 1. Baseline And Observability

- Add redacted timing metadata for Telegram turns:
  - `received_at`
  - `session_bound_at`
  - `agent_started_at`
  - `tool_selection_ms`
  - `first_delta_at`
  - `first_draft_sent_at`
  - `final_sent_at`
- Store only redacted status and timing metadata in `telegram_history.json`.
- Extend `/api/plugins/telegram/status` with:
  - `rich_messages_enabled`
  - `rich_drafts_enabled`
  - `formatting_mode`
  - `last_delivery_mode`
  - `last_delivery_status`
- Keep token, raw chat ID, sender ID, voice IDs, and raw rich payload details out of persisted history.

### 2. Telegram-Safe Formatting

- Add a backend renderer, likely `src/telegram_formatting.py`, that converts assistant Markdown into Telegram-safe HTML.
- Support the v1 formatting surface:
  - bold, italic, underline, strikethrough
  - spoiler text
  - inline links for `http` and `https`
  - inline code and fenced code blocks
  - block quotes
  - headings
  - simple ordered and unordered lists
  - simple tables where Telegram rich messages can render them safely
- Escape all raw text first, then emit only Telegram-supported tags.
- Validate generated HTML against the Telegram allowlist before sending.
- If validation fails, fall back to escaped plaintext.
- Update classic delivery so `send_telegram_text` can use `parse_mode="HTML"` when rich delivery is disabled or unavailable.
- Chunk classic messages at Telegram's 4096-character post-entity limit.

### 3. Better Tool Understanding For Telegram Turns

- Add a Telegram channel instruction around agent turns:
  - First infer the user goal.
  - Then select the right tool family.
  - Ask the user only when the missing detail materially changes the action.
  - Do not require the user to name tools directly.
- Pass a compact personal-assistant toolset into `stream_agent_loop` for Telegram turns.
- Start from existing assistant-safe tools and include the practical Telegram domains:
  - web search/fetch
  - contacts
  - calendar, tasks, and notes
  - email
  - settings
  - notifications
  - `ask_user`
- Do not add broad shell or arbitrary file-write tools solely because the channel is Telegram.
- Preserve the original Telegram user text as the stored user message; keep channel instructions separate so history and retrieval stay clean.

### 4. Async Reply Flow

- Decouple webhook acknowledgment from long-running agent execution.
- Return quickly from webhook and polling handlers after:
  - update validation
  - redacted persistence
  - session binding
  - background turn registration
- Maintain a typing heartbeat while the agent is working.
- Track background turn completion and failures in redacted history.
- Keep final reply delivery idempotent per Telegram update/message pair.
- Avoid leaking raw model output in public webhook responses.

### 5. Rich Message Draft Streaming

- Add env-gated helpers:
  - `send_telegram_rich_draft(chat_id, draft_id, partial_markdown)`
  - `send_telegram_rich_message(chat_id, final_markdown)`
- Add env flags:
  - `TELEGRAM_RICH_MESSAGES_ENABLED=false`
  - `TELEGRAM_RICH_DRAFTS_ENABLED=false`
  - `TELEGRAM_DRAFT_INTERVAL_MS=750`
- During `stream_agent_loop`:
  - accumulate visible assistant deltas
  - render accumulated text into Telegram-safe rich Markdown or HTML
  - send a draft update every configured interval or after meaningful text growth
- Use a stable nonzero `draft_id` derived from redacted chat/message context.
- Include `<tg-thinking>` only in drafts.
- Never include draft-only thinking blocks in the final persisted message.
- On completion:
  - send final content with `sendRichMessage`
  - if final rich delivery fails, fall back to classic `sendMessage` with safe HTML/plaintext
- Treat drafts as preview-only; final answers must always be persisted through a final message path.

## Public Interfaces

- Existing plugin routes keep their external shape:
  - `POST /api/plugins/telegram/webhook`
  - `POST /api/plugins/telegram/poll`
  - `POST /api/plugins/telegram/reply`
  - `GET /api/plugins/telegram/status`
  - `GET /api/plugins/telegram/history`
- Existing registered tools keep their contract:
  - `telegram_reply`
  - `odysseus_notify_user`
- New behavior is controlled by environment gates and readiness flags, not by breaking route payloads.
- Rich delivery should be internal to Telegram sending helpers unless a later UI needs explicit manual testing routes.

## Test Plan

- Renderer tests:
  - Markdown formatting converts to Telegram-safe HTML.
  - Raw HTML and active content are escaped or removed.
  - Invalid rich output falls back to plaintext.
  - Long classic messages are chunked safely.
- Plugin delivery tests:
  - Classic `sendMessage` includes `parse_mode="HTML"` when safe.
  - `sendRichMessage` receives a valid `rich_message` payload.
  - `sendRichMessageDraft` uses a nonzero stable draft ID.
  - Draft delivery failure does not prevent final delivery.
  - Final rich delivery failure falls back to classic delivery.
- Agent behavior tests:
  - Vague German requests surface the correct tool families without explicit tool names.
  - Telegram channel instruction does not overwrite the raw user prompt in session history.
  - Background turn failures are recorded without exposing raw output.
- Redaction tests:
  - Token, chat ID, sender ID, voice IDs, draft IDs, and raw delivery errors do not leak into persisted history or public responses.
- Offline smoke:
  - Add a dry-run rich-message payload smoke test with network disabled.
  - Keep live Telegram rich draft/final smoke manual and operator-gated.

## Assumptions

- Rich messages and rich drafts remain disabled by default until manual live smoke passes.
- `sendRichMessageDraft` is private-chat scoped, so group/forum support requires classic typing and final rich/classic delivery until proven safe.
- The first implementation prefers generated Telegram-safe HTML because MarkdownV2 escaping is error-prone.
- No new dependency is required unless tests show the existing Markdown/HTML tooling is insufficient.
