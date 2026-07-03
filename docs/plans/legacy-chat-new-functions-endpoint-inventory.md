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
| LC1 secure mode indicator | inline + slash | Telegram `/dsgvo` command path in `plugins/telegram/plugin.py`; Telegram readiness includes `privacy_boundary`; browser-safe DSGVO contract now exists: `GET /api/security/dsgvo/status`, `POST /api/security/dsgvo/toggle`, `POST /api/security/dsgvo`. | `static/js/chat.js` composer/header chip plus `static/js/slashCommands.js` `/dsgvo` status/toggle. | No backend blocker. UI wiring remains intentionally separate. |
| LC2 attachment processing status | inline | Upload: `POST /api/upload`, read: `GET /api/upload/{id}`; compact redacted status: `GET /api/universal-inbox/items/{source_ref}/status`; Universal Inbox file-type policy classifies extraction families and review requirements. | `static/js/fileHandler.js` chips and `static/js/chat.js` user-message footer. | No backend blocker. UI wiring remains intentionally separate. |
| LC3 Memory/Raptor clickable refs | message_action + modal_existing | Memory: `GET /api/memory`, `GET /api/memory/{id}`, `GET /api/memory/timeline`; canonical internal ref resolver: `GET /api/internal-refs/resolve?ref={internal_ref}`; RaptorGraph candidates resolve to redacted event summaries or `/api/diagnostics/memory-provenance?event_type=raptorgraph_mutation`. | `static/js/markdown.js`, `static/js/chatRenderer.js`, `static/js/memory.js`. | No backend blocker. UI wiring remains intentionally separate. |
| LC4 review and write gates | inline action row | Redacted gate summary: `GET /api/review-gates/status`; Memory write intent/executor contracts in `src/universal_inbox_memory_write_intent.py` and `src/universal_inbox_memory_write_executor.py`; write gate probe in `src/universal_inbox_write_gate.py`; Tasks and Memory mutation endpoints exist but should remain gated. | `static/js/chatRenderer.js` tool-result block/action row and existing confirm/toast helpers. | No backend blocker. UI wiring remains intentionally separate. |
| LC5 task/reminder feedback | slash + result block | Compact redacted summary: `GET /api/tasks/summary`; Tasks: `GET/POST /api/tasks`, `GET /api/tasks/{id}`, `PUT /api/tasks/{id}`, pause/resume/run/stop, `GET /api/tasks/runs/recent`, `POST /api/tasks/parse`, metadata endpoints. | `static/js/slashCommands.js` `/tasks*`; `static/js/chatRenderer.js` task result renderer. | No backend blocker. UI wiring remains intentionally separate. |
| LC6 file export intent preview | inline result block | Export planning in `src/universal_file_io.py`; browser-safe plan routes: `GET /api/universal-file-io/capabilities`, `POST /api/universal-file-io/export-plan`; document export preview/render routes: `/api/document/{doc_id}/export-pdf/preview`, `/api/document/{doc_id}/render-pdf`, `/api/document/{doc_id}/export-pdf`; zip export `/api/documents/export-zip`. | `static/js/chatRenderer.js` follow-up result block after recent attachment. | No backend blocker. UI wiring remains intentionally separate. |
| LC7 MCP/system health quick status | slash | Compact redacted quick status: `GET /api/diagnostics/quick-status`; MCP manager: `/api/mcp/servers`, `/api/mcp/tools`, `/api/mcp/servers/{id}/tools`; local MCP plugin: `/api/plugins/mcp/info`, `/api/plugins/mcp/config`; system health plugin: `/api/plugins/system_health_checker/health`; app health `/api/health`, `/api/ready`, `/api/version`; diagnostics services `/api/diagnostics/services`. | `static/js/slashCommands.js` `/mcp`, `/status` style slash replies. | No backend blocker. UI wiring remains intentionally separate. |
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

1. Per-action live readiness payload for Telegram delivery, Nextcloud copy and
   converter execution.

## Recommended Next Slice

LC1 UI wiring can start from the browser-safe DSGVO status route. LC2 UI wiring
can start from the redacted Universal Inbox attachment status contract. LC3 UI
wiring can resolve `memory:` and `raptor:` links through
`/api/internal-refs/resolve`. LC4 UI wiring can render pending review/write
states from `/api/review-gates/status`. LC5 UI wiring can render task/reminder
summaries from `/api/tasks/summary`. LC6 UI wiring can render safe export
plans from `/api/universal-file-io/export-plan`. LC7 UI wiring can render
MCP/system quick status from `/api/diagnostics/quick-status`. The next
backend-first slice is LC8 or LC9 unless the UI agent wants LC1-LC7 integration
support.
