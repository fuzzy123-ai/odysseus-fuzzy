# Legacy Chat New Functions Endpoint Inventory

Status: LC0 done
Date: 2026-07-03
Scope: safe_offline inventory only

## Guardrails

- Do not edit `static/frontpage-v2/*`.
- Do not redesign legacy chat or build V2 windows here.
- Legacy chat may add compact status chips, slash replies, existing modals,
  message actions and redacted result blocks only.
- Raw private document text, chat ids, tokens, absolute host paths and provider
  outputs must not be rendered in status panels or persisted in docs.
- Use current product language: Universal Inbox, Memory, RaptorGraph,
  DSGVO/Secure Data Mode, Tasks, MCP, Nextcloud.

## Existing Legacy Hooks

| Hook | File | Existing capability | Reuse for |
| - | - | - | - |
| Composer/status area | `static/js/chat.js` | streaming, stop/resume, attachment carry-over, tool events | LC1 secure mode, LC2 attachment state, LC4 gate blocks |
| Slash command dispatcher | `static/js/slashCommands.js` | command registry, slash replies, tool-panel openers | LC1, LC5, LC7, LC8, LC9 |
| Attachment strip | `static/js/fileHandler.js` | pending chips, upload spinner, upload error toast, last upload metadata | LC2 pre-send/upload feedback |
| Message renderer/footer | `static/js/chatRenderer.js` | message actions, memory-used pill, sources boxes, tool output blocks | LC3 clickable refs, LC4/LC6 result cards |
| Markdown renderer | `static/js/markdown.js` | safe links, internal anchor handling, sanitized HTML | LC3 internal `memory:`/`raptor:` refs |
| Memory modal | `static/js/memory.js` | load/list/search/edit/delete/pin existing memories | LC3 memory ref open target |

## Endpoint Map By Slice

| Slice | Surface | Existing endpoints/contracts | Preferred legacy hook | Missing backend endpoint before UI work |
| - | - | - | - | - |
| LC1 secure mode indicator | inline + slash | Telegram `/dsgvo` command path in `plugins/telegram/plugin.py`; Telegram readiness includes `privacy_boundary`; preferences via `/api/prefs/{key}` exist but a dedicated legacy-chat DSGVO read/toggle route was not confirmed. | `static/js/chat.js` composer/header chip plus `static/js/slashCommands.js` `/dsgvo` status/toggle. | Add or confirm a browser-safe `/api/security/dsgvo/status` and `/api/security/dsgvo/toggle` contract before frontend wiring. |
| LC2 attachment processing status | inline | Upload: `POST /api/upload`, read: `GET /api/upload/{id}`; chat stream emits attachment events; Universal Inbox pipeline modules expose status objects, extraction families and OCR warnings but no compact browser status route was confirmed. | `static/js/fileHandler.js` chips and `static/js/chat.js` user-message footer. | Add or confirm `/api/universal-inbox/items/{source_ref}/status` or include redacted inbox status in chat attachment events. |
| LC3 Memory/Raptor clickable refs | message_action + modal_existing | Memory: `GET /api/memory`, `GET /api/memory/{id}`, `GET /api/memory/timeline`; RaptorGraph candidates are present in Universal Inbox memory intent payloads as internal refs; memory provenance diagnostics via `/api/diagnostics/memory-provenance`. | `static/js/markdown.js`, `static/js/chatRenderer.js`, `static/js/memory.js`. | Confirm canonical read route for `raptor:<id>` / `raptor_node` detail; otherwise route Raptor refs to diagnostics/provenance summary first. |
| LC4 review and write gates | inline action row | Memory write intent/executor contracts in `src/universal_inbox_memory_write_intent.py` and `src/universal_inbox_memory_write_executor.py`; write gate probe in `src/universal_inbox_write_gate.py`; Tasks and Memory mutation endpoints exist but should remain gated. | `static/js/chatRenderer.js` tool-result block/action row and existing confirm/toast helpers. | Add or confirm a redacted review-state endpoint for pending Memory/Nextcloud/export decisions. |
| LC5 task/reminder feedback | slash + result block | Tasks: `GET/POST /api/tasks`, `GET /api/tasks/{id}`, `PUT /api/tasks/{id}`, pause/resume/run/stop, `GET /api/tasks/runs/recent`, `POST /api/tasks/parse`, metadata endpoints. | `static/js/slashCommands.js` `/tasks*`; `static/js/chatRenderer.js` task result renderer. | No blocker. Improve grouping of weekday cron into one readable recurrence rule. |
| LC6 file export intent preview | inline result block | Export planning in `src/universal_file_io.py`; document export preview/render routes: `/api/document/{doc_id}/export-pdf/preview`, `/api/document/{doc_id}/render-pdf`, `/api/document/{doc_id}/export-pdf`; zip export `/api/documents/export-zip`. | `static/js/chatRenderer.js` follow-up result block after recent attachment. | Add or confirm browser/API route that returns Universal File IO plan without executing converter. |
| LC7 MCP/system health quick status | slash | MCP manager: `/api/mcp/servers`, `/api/mcp/tools`, `/api/mcp/servers/{id}/tools`; local MCP plugin: `/api/plugins/mcp/info`, `/api/plugins/mcp/config`; system health plugin: `/api/plugins/system_health_checker/health`; app health `/api/health`, `/api/ready`, `/api/version`; diagnostics services `/api/diagnostics/services`. | `static/js/slashCommands.js` `/mcp`, `/status` style slash replies. | No blocker for read-only summaries. Mutating MCP/server config remains out of scope. |
| LC8 coding-agent lightweight entry | slash + task card | Projects: `/api/projects`, intake preview/apply/merge, project task-run/planner-task-run/commit-run/push-run; Sandbox worker: `/api/sandbox-worker/submit`, `/status/{job_id}`, `/artifacts/{job_id}`, `/cancel/{job_id}`. | `static/js/slashCommands.js` and compact chat task card in `static/js/chatRenderer.js`. | Confirm which project/sandbox actions are read-only preview vs operator-gated mutation before exposing buttons. |
| LC9 diagnostics surfaces | slash | Diagnostics: `/api/diagnostics/services`, `/api/diagnostics/logs`, `/api/diagnostics/ai-activity`, `/api/diagnostics/memory-provenance`, `/api/diagnostics/tool-capabilities`, `/api/db/stats`, `/api/rag/stats`. | `static/js/slashCommands.js` compact summaries. | No blocker for redacted read-only summaries. Logs must stay redacted and bounded. |
| LC10 live delivery/converter affordances | gated buttons | Telegram status/history/poll/reply under `/api/plugins/telegram/*`; Universal Export/File IO contracts; Nextcloud transfer and write gate modules exist. | Disabled buttons only in `static/js/chatRenderer.js` until backend readiness says go. | Needs explicit live Go plus backend readiness route for each send/copy/convert action. |

## Backend Contracts Worth Reusing

- DSGVO/Secure Data Mode:
  - `plugins/telegram/plugin.py` handles `/dsgvo`, `/privacy`, `/gdpr`,
    `/datenschutz`, local-only routing and pinned Telegram status.
  - `src/privacy_runtime.py`, `src.secure_model_routing.py` and
    `src.sensitivity_delegation_gate.py` are the backend policy layer.
- Universal Inbox:
  - `src/universal_inbox_file_types.py` classifies families and review needs.
  - `src/universal_inbox_extraction.py` produces redacted extraction packets
    with OCR/PDF warnings.
  - `src/universal_inbox_pipeline.py` combines discovery, ledger, extraction,
    routing and memory abstraction into status objects.
  - `src/universal_inbox_memory_write_intent.py` produces Memory and
    RaptorGraph write intents with internal references.
  - `src/universal_inbox_memory_write_executor.py` performs writes only after
    review confirmation and writers are supplied.
  - `src/universal_inbox_write_gate.py` probes scoped live write capability.
- File export:
  - `src/universal_file_io.py` can build safe export and Telegram delivery
    plans without conversion execution.
- Diagnostics:
  - `routes/diagnostics_routes.py` already exposes redacted AI activity,
    memory provenance and tool capability diagnostics.

## Open Backend Contracts

These should be resolved before touching the corresponding legacy UI slice:

1. Browser-safe DSGVO status/toggle route.
2. Redacted Universal Inbox item status/readiness route for uploaded files.
3. Canonical RaptorGraph detail/read route for internal `raptor:` refs.
4. Review-state route that summarizes pending Memory/Nextcloud/export gates.
5. Universal File IO plan endpoint for "make this file a PDF/image/audio"
   without executing converters.
6. Per-action live readiness payload for Telegram delivery, Nextcloud copy and
   converter execution.

## Recommended Next Slice

LC1 and LC2 can start once the browser-safe DSGVO status route and attachment
status contract are confirmed or added. They are the highest-value legacy
chat changes because they explain Secure Data Mode and file processing state
without requiring V2 UI work.
