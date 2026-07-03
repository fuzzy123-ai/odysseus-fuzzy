# Legacy Chat New Functions Integration Roadmap

Status: active
Date: 2026-07-02
Owner: operator + Codex

## Current Evidence

- LC0 endpoint and UI-hook inventory is captured in
  `docs/plans/legacy-chat-new-functions-endpoint-inventory.md`.
- Machine-readable slice overview is captured in
  `docs/plans/legacy-chat-new-functions-master-roadmap.json`.
- LC1 backend route contract is available for UI wiring:
  `GET /api/security/dsgvo/status`, `POST /api/security/dsgvo/toggle`, and
  `POST /api/security/dsgvo`.
- LC2 backend route contract is available for UI wiring:
  `GET /api/universal-inbox/items/{source_ref}/status`. It returns redacted
  attachment status metadata only: file family, category, review requirement,
  extraction readiness and blocked state, without names, paths, hashes, chat IDs
  or raw contents.
- LC3 backend route contract is available for UI wiring:
  `GET /api/internal-refs/resolve?ref={internal_ref}`. It resolves canonical
  `odysseus://memory/...`, `odysseus://raptor/node/...`, `odysseus://raptor/edge/...`,
  chat anchors and shorthand refs into redacted open targets. Memory refs point
  to the existing Memory route/modal without returning text; RaptorGraph refs
  return a redacted event summary or the memory-provenance diagnostics fallback.
- MVP Roadmap Runner reports roadmaps 1-10 at 100%; Version 1.0 is still gated
  by the new UI going live.
- No legacy or V2 UI code has been changed for LC0-LC3 backend work.

## Goal

Expose the new backend capabilities in the existing legacy chat and lightweight
legacy modules where no large new UI is required. The legacy chat should make
state, review gates and small actions visible enough for day-to-day use while
the V2 UI remains owned by the UI agent.

This roadmap is deliberately not a redesign. It adds small controls, status
chips, slash commands and message-level affordances to existing surfaces.

## Product Rules

- Do not edit `static/frontpage-v2/*`.
- Do not redesign the legacy shell, sidebar, memory modal, document viewer or
  chat layout.
- Prefer existing components: chat bubbles, sources/details blocks, toasts,
  compact buttons, slash commands, modals already used by the legacy UI.
- No nested card stacks, no new large windows, no hero or marketing sections.
- Keep actions explicit when they mutate data, send files, write Nextcloud,
  run providers, deploy, or touch the host.
- Never render raw private document text in status panels, logs or sidecars.
- Legacy labels must use current product language: Universal Inbox, Memory,
  RaptorGraph, DSGVO/Secure Data Mode, Tasks, MCP, Nextcloud. Avoid old
  Obsidian wording except for backward-compatible route names.

## Integration Principle

New backend capabilities should appear in the legacy chat as:

1. A small status signal when the chat has enough context to act.
2. A direct command or message action for the obvious next step.
3. A redacted result block explaining what happened and what still needs a
   human decision.

If a feature needs a full workbench, it belongs to V2 or a later module, not
this roadmap.

## Current Legacy Hooks

- `static/js/chat.js` already handles streaming, tool-result bubbles,
  attachment carry-over, RAG sources, background research, message actions and
  stalled-stream recovery.
- `static/js/slashCommands.js` already centralizes chat commands and setup
  replies.
- `static/js/fileHandler.js` already owns pending attachment chips and upload
  status.
- `static/js/memory.js` already owns the legacy Memory modal and can refresh
  after `manage_memory`.
- Existing APIs already expose task, diagnostics, MCP, coding-agent, research,
  model/cookbook, Telegram, memory and document status surfaces.

## Priority Queue

| Prio | Slice | Scope | Legacy Surface | Why |
| ---: | --- | --- | --- | --- |
| 1 | LC0 endpoint and UI-hook inventory | safe_offline | docs only | Prevent guessing before touching large legacy files. |
| 2 | LC1 secure mode chat indicator | repo_only | chat header/status/toast/slash | DSGVO mode must be visible before file or memory actions. |
| 3 | LC2 attachment processing status | repo_only | attachment chips + user message footer | Users need to know whether Inbox/PDF/OCR/Memory intent ran. |
| 4 | LC3 memory/Raptor clickable refs | repo_only | AI message renderer + memory list | Internal links to Memory/RaptorGraph entries should be clickable in Odysseus. |
| 5 | LC4 review and write gates | repo_only | compact inline action row | Nextcloud copy, Memory write and export delivery need clear approve/blocked states. |
| 6 | LC5 task/reminder feedback | repo_only | slash commands + task result block | Recurring tasks, especially cron/weekday tasks, should summarize as one rule. |
| 7 | LC6 file export intent preview | repo_only | attachment follow-up result block | "Make this a PDF/image/audio" should show a safe plan before converters run. |
| 8 | LC7 MCP/system health quick status | repo_only | slash command result block | Useful for operators without opening a separate workbench. |
| 9 | LC8 coding-agent lightweight entry | repo_only | slash command + chat task card | Start/status/handoff for coding tasks without building the Projects UI. |
| 10 | LC9 diagnostics surfaces | repo_only | slash command summaries | AI activity, memory provenance and service health should be visible in chat. |
| 11 | LC10 live delivery/converter affordances | needs_live_go | gated buttons only | Real Telegram file delivery/converters stay disabled until gates are green. |

## Slice Details

### LC0 Endpoint And UI-Hook Inventory

Class: `safe_offline`

Allowed paths:

- `docs/plans/legacy-chat-new-functions-integration-roadmap.md`
- optional inventory doc under `docs/plans/`

Tasks:

- Map exact read/write endpoints for each target feature.
- Confirm where legacy chat can attach status without layout churn.
- Mark features as `inline`, `slash`, `message_action`, `modal_existing`, or
  `defer_to_v2`.

Done when:

- Every planned slice has a concrete endpoint list and UI hook.
- Any missing backend endpoint is named before frontend work begins.

### LC1 Secure Mode Chat Indicator

Class: `repo_only`

Target:

- Legacy chat header or composer-adjacent status chip.
- Slash command help entry, e.g. `/dsgvo` status/toggle if backend already
  supports it.

Behavior:

- Show DSGVO/Secure Data Mode state in chat.
- On mode change, show a compact confirmation toast and a short chat system
  note when triggered from chat.
- Make local-only consequences clear: external provider use may be blocked or
  delegated to local analysis.

Do not:

- Add a settings page.
- Persist secrets or private text.

### LC2 Attachment Processing Status

Class: `repo_only`

Target:

- `static/js/fileHandler.js`
- `static/js/chat.js`
- existing upload/user-message attachment rendering

Behavior:

- After upload or resend/regenerate, show a redacted status line:
  `uploaded`, `queued`, `processed`, `partial`, `needs review`, `blocked`.
- Display recognized family: PDF, image/OCR, Office, text, audio, archive,
  unsupported.
- If OCR/PDF extraction is partial or blocked, explain why without raw content.
- Keep attachments attached across edit/resend/regenerate.

Done when:

- A Telegram/local-upload style file has an obvious legacy-chat status.
- Parser-broken PDFs and OCR-needed images do not look like silent failure.

### LC3 Memory And RaptorGraph Clickable References

Class: `repo_only`

Target:

- `static/js/chatRenderer.js`
- `static/js/markdown.js`
- `static/js/memory.js`

Behavior:

- Render internal Odysseus refs as clickable links in the app, not in Telegram:
  `memory:<id>`, `raptor:<id>`, `source:<id>`, or the canonical backend URL if
  one already exists.
- Open the existing Memory modal/list for Memory refs.
- For RaptorGraph refs, use the smallest existing read/status route first; if
  no UI exists, open a compact details popover or route to a diagnostics view.
- Show unavailable/deleted refs as disabled but understandable.

Done when:

- AI-created Memory/Raptor references can be clicked internally.
- No raw private source body is injected into the link itself.

### LC4 Review And Write Gates

Class: `repo_only`

Target:

- Chat tool-result block and inline action row.

Behavior:

- For Nextcloud copy, Memory Write Intent, RaptorGraph write and file export,
  show one compact gate block:
  - planned action
  - safety mode
  - dry-run/live state
  - required human decision
  - approved/done/blocked result
- Reuse existing confirmation modal/toast components.

Done when:

- The user no longer needs to infer whether `/review ok` or another approval is
  still required.

### LC5 Task And Reminder Feedback

Class: `repo_only`

Target:

- `static/js/slashCommands.js`
- `static/js/chat.js`
- existing Tasks API/read blocks

Behavior:

- Add a compact task summary renderer for tool results:
  - once/daily/weekly/monthly/cron
  - next run
  - target: session, notification, Telegram, email
  - paused/active
- Weekday cron rules must be rendered as one rule, e.g. `Mo-Fr 09:00`, not five
  separate tasks.
- Provide slash helpers:
  - `/tasks`
  - `/tasks today`
  - `/tasks telegram`

Done when:

- "Erinnere mich Mo-Fr um 9" reads back as one recurring task in chat.

### LC6 File Export Intent Preview

Class: `repo_only`

Target:

- Follow-up chat result block after a recent attachment.

Behavior:

- When the backend returns a Universal File IO plan, render:
  - source family
  - target format
  - required local tool
  - DSGVO/local-only state
  - whether execution is available, gated, or unsupported
- Do not execute converters in this slice.

Done when:

- The user can see whether "mach daraus ein PDF" is understood and what gate
  blocks execution.

### LC7 MCP And System Health Quick Status

Class: `repo_only`

Target:

- Slash command result blocks.

Behavior:

- Add status summaries for:
  - MCP workbench readiness
  - System health checker plugin
  - Telegram poll status
  - model/cookbook health
- Keep output compact and redacted.

Done when:

- Operator can ask from legacy chat: "status telegram/mcp/system" and get a
  useful summary without a new UI.

### LC8 Coding-Agent Lightweight Entry

Class: `repo_only`

Target:

- Legacy chat slash command and result card.

Behavior:

- Provide a lightweight entry for:
  - repo snapshot
  - task plan preview
  - sandbox-check plan/result
  - handoff/publish plan status
- Do not build Projects UI here.
- Do not start host/sandbox work without existing backend gates.

Done when:

- The chat can show "coding task accepted / waiting / blocked / evidence ready"
  using existing backend routes.

### LC9 Diagnostics Surfaces

Class: `repo_only`

Target:

- Slash command summaries and compact diagnostics blocks.

Behavior:

- Show redacted summaries from:
  - AI activity ledger
  - memory provenance diagnostics
  - service diagnostics
- Use time, surface, model/provider, status and error class; never raw prompts,
  private docs, base64, tokens or chat IDs.

Done when:

- A failed AI/tool action can be diagnosed from legacy chat without reading raw
  logs.

### LC10 Live Delivery And Converter Affordances

Class: `needs_live_go`

Target:

- Disabled/gated buttons only.

Behavior:

- Show "Send file", "Run converter", or "Copy to Nextcloud" only when the
  backend reports readiness.
- Buttons must remain disabled with a reason until live gates are satisfied.
- Execution requires explicit Go and backend confirmation.

Done when:

- Legacy chat can expose the action safely without making accidental live
  writes or sends possible.

## Deferred To V2 / UI Agent

- Project Cockpit / Projects window.
- Runner State standalone window.
- Full Nextcloud browser/review workbench.
- Full MCP workbench.
- Full RaptorGraph visual explorer.
- Updates & Backups settings panel.
- Major layout, sidebar, theme, or frontpage changes.

## Verification

Focused checks per implementation slice:

- `node --check static/js/chat.js`
- `node --check static/js/chatRenderer.js`
- `node --check static/js/slashCommands.js`
- `node --check static/js/fileHandler.js`
- affected Python tests only when backend route contracts change
- one browser smoke against legacy chat after every visible UI slice

Manual smoke targets:

- Chat loads without console errors.
- Attachment upload still works.
- Existing message actions still work.
- Memory modal still opens and refreshes.
- Slash command menu still responds.
- Mobile composer does not overlap status chips.

## Recommended Execution Order

1. LC0 inventory.
2. LC1 secure mode indicator.
3. LC2 attachment processing status.
4. LC5 task/reminder feedback.
5. LC3 clickable Memory/Raptor refs.
6. LC4 review/write gates.
7. LC6 export intent preview.
8. LC7 and LC9 compact diagnostics.
9. LC8 coding-agent lightweight entry.
10. LC10 only after explicit live Go.

## Recommended Next Human Decision

Start with LC0-LC2 in one small ABC pass. These give the biggest day-to-day
benefit in legacy chat without stepping on the V2 UI work.
