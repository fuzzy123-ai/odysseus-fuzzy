# Central ABC Masterplan

Date: 2026-06-29

Status: active planning baseline

## Goal

Integrate the currently open Odysseus roadmaps into one execution plan so ABC
can work in parallel where safe, while keeping live Nextcloud, Telegram,
provider, host, deploy, backup, restore and destructive actions behind explicit
operator gates.

This is an integration plan, not a replacement for the source roadmaps. The
source roadmaps remain the detailed contracts for their domains.

## Source Roadmaps

Primary sources:

- `docs/plans/nextcloud-import-preparation-roadmap.md`
- `docs/plans/nextcloud-source-bridge.md`
- `docs/plans/universal-inbox-live-readiness-runbook.md`
- `docs/plans/universal-inbox-nextcloud-raptorgraph-contract.md`
- `docs/plans/workflow-skills-universal-inbox-handoff.md`
- `docs/plans/workflow-skills-universal-inbox-roadmap.md`
- `docs/plans/universal-file-io-roadmap.md`
- `docs/plans/pdf-long-document-extraction-roadmap.md`
- `docs/plans/dsgvo-security-gate-roadmap.md`
- `docs/plans/coding-agent-backend-handoff.md`
- `docs/plans/repo-control-roadmap.md`
- `docs/plans/server-project-runner-roadmap.md`
- `docs/plans/odysseus-mcp-server-roadmap.md`
- `docs/plans/mcp-workbench-evidence-plan.md`
- `docs/mcp-server-runbook.md`
- `docs/plans/large-file-refactoring-abc-plan.md`
- `docs/plans/large-file-refactoring-overview.md`
- `docs/plans/unified-odysseus-roadmap.md`
- `docs/plans/mvp-master-roadmap.md`
- `docs/plans/mvp-roadmap-runner-state.json`

Important vocabulary note:

- "Obsidian" is legacy vocabulary in older docs. The active target is
  Universal Inbox + Telegram/Nextcloud/local sync + Memory Write Intent +
  RaptorGraph/provenance.
- Runtime operations use Podman/pods, not Docker. Docker may only remain where
  file names or historical formats require it.

## Execution Mode

Default mode: `Standard ABC`.

Reason:

- The active plan contains live gates and product decisions.
- Several tracks can be prepared in parallel, but live writes and deploy-like
  actions need bounded operator Go.
- Large refactors must not run unattended while feature tracks still touch
  adjacent files.

Allowed without extra live Go:

- `safe_offline`: models, validators, dry-run plans, redaction checks.
- `repo_only`: backend logic, contracts, fake clients, route tests, docs.

Needs explicit operator Go:

- Live Nextcloud writes, WebDAV upload, tag/comment/write smoke.
- Live Telegram sendDocument/sendPhoto/sendAudio delivery smoke.
- Provider calls that may expose private document content.
- Host, Podman, deploy, Cloudflare, backup, restore, rebuild or migration
  actions.
- Pushes are allowed only when scope is clean and target is `fuzzy/dev`; never
  push to `origin`.

## Parallelization Model

Run at most three active implementation lanes at the same time.

| Lane | Priority | Parallel? | Reason |
| --- | ---: | --- | --- |
| L3 MCP Workbench + Podman Read-only Checks | P0 bootstrap | Yes | Verification/control infrastructure should be finished first so later lanes can be checked through stable evidence paths. |
| L1 Nextcloud Live Write + Universal Inbox | P0 product | Limited | Highest user value, but touches Inbox, Telegram, policy and memory gates. |
| L2 Coding Agent + Repo Control + Project Runner | P1 | Yes | Mostly isolated backend/project-control surface. |
| L4 Memory/RaptorGraph Stabilization | P1/P2 | Limited | Safe when scoped away from active Inbox memory-write files. |
| L5 Universal File IO / Export Plans | P1/P2 | Limited | Safe offline plans can run; live converters/delivery need gates. |
| L6 Long PDF Extraction + RAG/Ingestion Reliability | P1 | Limited | Cross-cutting backend reliability for Inbox, Nextcloud import, Personal Docs/RAG and chat PDF flows. |
| L7 Large File Refactoring | P2 | Carefully | Broad hotfiles; start only after P0 path is stable or on disjoint files. |
| L8 UI/V2 Integration | P2/P3 | No in this backend run | UI agent owns placement and visual decisions. |

Integration rule:

- Parallel workers must have disjoint allowed paths.
- If two lanes need the same file, Charlie serializes them.
- Refactoring never runs on files with active feature changes unless the
  feature slice is already committed and pushed.
- The first active lane is L3 until the local MCP smoke, tool-policy evidence
  and read-only verification path are done or explicitly deferred. L3 must stay
  narrow: no `expose_all`, no shell/python/file-write tools, no remote
  exposure, no Cloudflare route and no live host mutation.

## Active Lane L1: Nextcloud Live Write + Universal Inbox

Goal:

Telegram/local/Nextcloud files can flow through Universal Inbox, receive a
safe placement decision, be reviewed or confirmed, and then be copied/uploaded
into the designated Nextcloud target through the dedicated automation user.

Done state:

- Incoming Telegram file without prompt is acknowledged and enters Inbox.
- Inbox classifies file type, sensitivity and extraction capability.
- PDF and common document types are supported at least for safe text extraction
  or review-required handling.
- Odysseus proposes a Nextcloud target path with confidence and reasons.
- User receives Telegram feedback when review/confirmation is needed.
- On explicit confirmation, copy/upload writes to Nextcloud live path.
- No delete, move, overwrite or admin operation is part of MVP.
- Memory/RaptorGraph writes remain governed by Memory Write Intent and privacy
  gates.

Current evidence:

- 2026-06-29: WebDAV client has an env-based runtime factory that performs no
  network IO during construction and redacts missing secret values to config
  keys only.
- 2026-06-29: Telegram `/review ok` now keeps the default Dry-run path, but can
  execute a WebDAV copy only when both runtime gates are explicit:
  `UNIVERSAL_INBOX_NEXTCLOUD_LIVE_WRITE_ENABLED=true` and
  `UNIVERSAL_INBOX_NEXTCLOUD_OPERATOR_LIVE_GO=true`.
- 2026-06-29: Successful live-gated copy returns a Telegram success message only
  after size verification; missing gates continue to report Dry-run/operator-Go.
- 2026-06-29 Focused tests passed:
  `python -m pytest tests/test_nextcloud_webdav_client.py tests/test_universal_inbox_nextcloud_transfer.py tests/test_universal_inbox_extraction.py tests/test_universal_inbox_memory_write_intent.py tests/test_universal_inbox_worker.py tests/test_telegram_plugin.py -q`
  returned `103 passed, 1 warning`.

Primary allowed paths:

- `src/universal_inbox*.py`
- `src/nextcloud_*.py`
- `plugins/telegram/plugin.py`
- `routes/*inbox*`
- `routes/*nextcloud*`
- `tests/test_universal_inbox*.py`
- `tests/test_nextcloud*.py`
- `tests/test_telegram*.py`
- `docs/plans/nextcloud-import-preparation-roadmap.md`
- `docs/plans/nextcloud-source-bridge.md`
- `docs/plans/universal-inbox-live-readiness-runbook.md`

Slice queue:

| Slice | Class | Owner | Goal |
| --- | --- | --- | --- |
| L1-0-state-check | safe_offline | Charlie | Done: existing Inbox/Nextcloud modules and tests reconciled; unrelated dirty files ignored. |
| L1-1-live-write-roadmap | repo_only | Alice | Done: live-write gates and evidence are recorded in this masterplan and Nextcloud roadmap. |
| L1-2-webdav-upload-gate | repo_only | Bob | Done: WebDAV env factory plus transfer executor keep copy-only, no-overwrite, dry-run and redacted behavior. |
| L1-3-telegram-review-loop | repo_only | Bob | Done: `/review ok` confirms review, reports dry-run/operator gate, and reports verified live copy only when both gates are set. |
| L1-4-pdf-and-doc-evidence | repo_only | Bob | Done: focused extraction tests cover PDF, DOCX and common text-like documents without raw persistence. |
| L1-5-memory-intent-link | repo_only | Bob | Done: worker tests cover placement, extraction and Memory Write Intent/RaptorGraph provenance linkage without writes. |
| L1-6-live-upload-smoke | needs_live_go | Charlie | Perform bounded Nextcloud write smoke after explicit Go. |

Gate queue:

| Gate | Class | Blocks | Decision needed |
| --- | --- | --- | --- |
| NC-LIVE-USER | needs_live_go | L1-6 | Confirm dedicated Nextcloud automation user and target root. |
| NC-LIVE-WRITE | needs_live_go | L1-6 | Allow one bounded copy-only upload smoke. |
| TG-LIVE-REPLY | needs_live_go | Telegram delivery proof | Allow bounded Telegram reply/delivery smoke if not already covered. |
| MEMORY-WRITE | needs_live_go | RaptorGraph/native memory writes | Allow reviewed memory writes, or keep dry-run. |

## Active Lane L2: Coding Agent + Repo Control + Project Runner

Goal:

Odysseus can manage approved repositories and project workspaces safely enough
for later project UI usage: plan tasks, inspect repo state, apply bounded work,
run checks, prepare commits/pushes and keep deploy/exposure gated.

Current evidence:

- `repo-control-roadmap.md` says RC1-RC10 backend slices are implemented.
- `server-project-runner-roadmap.md` says non-UI backend/API slices are done
  while visual UI and provider repo creation remain gated.
- `coding-agent-backend-handoff.md` says backend foundation is implemented and
  UI integration is pending.
- 2026-06-30: L2 state check verified the Coding Agent backend, route and test
  files as an in-scope addition. `app.py` registers the router under
  `/api/coding-agent`; the diff only adds this route registration.
- 2026-06-30: Focused L2 verification passed:
  `python -m pytest tests/test_coding_agent_backend.py tests/test_repo_registry.py tests/test_repo_git_adapter.py tests/test_repo_commit_runner.py tests/test_repo_push_runner.py tests/test_repo_routes.py tests/test_manage_repos_read_tool.py tests/test_server_project_runner.py tests/test_server_project_registry.py tests/test_server_project_routes.py -q`
  returned `100 passed, 1 warning`.
- 2026-06-30: Coding Agent, Repo Control and Project Runner contracts are now
  consolidated as one backend lane: registered repos only, isolated worktrees,
  scope-checked exact patches, quality/review gates, fuzzy-only publish plans
  and live/provider/deploy actions held behind explicit gates.

Primary allowed paths:

- `src/coding_agent_backend.py`
- `routes/coding_agent_routes.py`
- `tests/test_coding_agent_backend.py`
- `src/repo_control*.py`
- `src/server_project_runner*.py`
- `tests/test_repo_control*.py`
- `tests/test_server_project_runner*.py`
- `docs/plans/coding-agent-backend-handoff.md`
- `docs/plans/repo-control-roadmap.md`
- `docs/plans/server-project-runner-roadmap.md`

Slice queue:

| Slice | Class | Owner | Goal |
| --- | --- | --- | --- |
| L2-0-state-check | safe_offline | Charlie | Done: current Coding Agent files are in scope and focused tests pass. |
| L2-1-contract-consolidation | repo_only | Alice | Done: Coding Agent, Repo Control and Project Runner are documented as one backend contract lane. |
| L2-2-route-consistency | repo_only | Bob | Done: `/api/coding-agent` is registered, admin-protected and route-tested. |
| L2-3-repo-policy-link | repo_only | Bob | Done: Coding Agent worktree/publish gates reuse RepoRegistry permissions and fuzzy-only push policy. |
| L2-4-ui-handoff | repo_only | Alice | Done: UI-agent handoff lists required states/actions without deciding layout or placement. |

Gates:

- Provider repo creation needs explicit operator Go.
- Deploy/Cloudflare tunnel needs explicit exposure Go.
- Production project execution on server needs host/deploy Go.

L2 backend status:

- Backend complete for the current masterplan scope.
- UI/v2 project-cockpit integration, provider repository creation, live server
  execution and Cloudflare exposure remain gated follow-up tracks, not blockers
  for the safe backend lane.

## Bootstrap Lane L3: MCP Workbench + Podman Read-only Checks

Goal:

Codex/Odysseus gets a small, auditable MCP workbench for local checks, UI smoke
evidence, GitHub/CI context and Podman read-only status, without broad
dangerous control surfaces.

Codex setup note:

- When the corresponding MCP services, plugins or connectors become available
  in this Codex environment, configure them here as the preferred verification
  workbench and test them before relying on them for roadmap evidence.
- Candidate services are the local Odysseus MCP endpoint, Playwright/browser
  MCP, GitHub connector or GitHub MCP, documentation MCPs such as Context7 or
  OpenAI Docs where appropriate, optional Chrome DevTools MCP and the narrow
  Podman read-only check path.
- Setup must remain opt-in, minimal and auditable. Do not install or enable
  broad filesystem, shell, Docker or generic remote-control MCP surfaces just
  because they are available.

Why first:

- Later Nextcloud, Telegram, server-project and deployment-adjacent work needs
  better evidence than local unit tests alone.
- A local MCP smoke can prove which Odysseus tools are visible before any
  stronger automation is trusted.
- Playwright evidence and Podman read-only checks give Charlie a safer way to
  verify UI/runtime state without turning MCP into a general remote-control
  surface.

Current evidence:

- Local MCP server path exists under `plugins/mcp_server`.
- Server is disabled by default and gated by config/env.
- Risky tools are excluded by policy.
- New handoff prefers Podman read-only checks, not Docker MCP.
- 2026-06-29 Bootstrap evidence: `docs/mcp-server-runbook.md` now documents
  the Codex MCP workbench setup gate and Podman-only read-only stance.
- 2026-06-29 Workbench evidence plan:
  `docs/plans/mcp-workbench-evidence-plan.md` defines Codex-side MCP setup
  gates, Playwright evidence, GitHub context policy and Podman read-only checks.
- 2026-06-29 Focused tests passed:
  `python -m pytest tests/test_mcp_server_tool_policy.py tests/test_mcp_server_plugin.py -q`
  returned `16 passed, 1 warning`.
- 2026-06-30 Focused tests passed:
  `python -m pytest tests/test_mcp_server_tool_policy.py tests/test_mcp_server_plugin.py -q`
  returned `16 passed, 1 warning`.
- 2026-06-30 Safe L3 bootstrap is complete in repo-only scope. Codex-side MCP
  service setup, local live MCP activation and host Podman probes remain gated
  actions; they do not block later safe backend lanes.

Primary allowed paths:

- `plugins/mcp_server/plugin.py`
- `src/mcp_server_tool_policy.py`
- `src/builtin_mcp.py`
- `tests/test_mcp_server_tool_policy.py`
- `docs/mcp-server-runbook.md`
- `docs/setup.md`
- future narrow `src/podman_*` or `ops/*` read-only helper paths only after
  selected explicitly.

Slice queue:

| Slice | Class | Owner | Goal |
| --- | --- | --- | --- |
| L3-0-runbook-reconcile | repo_only | Alice | Done: reconciled runbook with Codex MCP workbench setup gate and Docker MCP non-goal. |
| L3-1-local-mcp-smoke-contract | repo_only | Bob | Done: initialize, tools/list, readiness, prompts/resources and redacted audit have focused route tests. |
| L3-2-safe-tool-policy-evidence | repo_only | Bob | Done: high-risk registered tools remain absent, `expose_all` is ignored and generic API stays hidden by default. |
| L3-3-codex-mcp-service-setup | needs_live_go | Charlie | Gated: install/configure corresponding MCP services or connectors in Codex when available, then run bounded setup tests. |
| L3-4-playwright-evidence-plan | safe_offline | Charlie | Done: `mcp-workbench-evidence-plan.md` defines UI smoke targets, artifacts and privacy gates. |
| L3-5-github-context-policy | safe_offline | Alice | Done: `mcp-workbench-evidence-plan.md` defines GitHub read/write boundaries. |
| L3-6-podman-readonly-plan | repo_only | Bob | Done as plan: `mcp-workbench-evidence-plan.md` maps read-only Podman checks to existing health foundations; implementation can remain future-scoped. |

Gates:

- Enabling MCP live on server needs operator Go.
- Installing or enabling Codex-side MCP services/connectors needs availability
  in this Codex environment plus explicit operator Go for each non-bundled or
  networked service.
- Any remote/LAN/Cloudflare MCP exposure is out of scope until separately
  approved.
- Podman restart/recreate remains approval-only.
- If a later lane wants to use the MCP workbench, it may only rely on the
  completed local/read-only checks from this lane. It must not use MCP as a
  shortcut around existing route, repo, privacy or live-action gates.

## Lane L4: Memory/RaptorGraph Stabilization

Goal:

Keep Memory/RaptorGraph usable and auditable while Inbox and Nextcloud writes
grow: maintenance logs, agent/user interaction audit, provenance, rebuild
readiness and dry-run/live gates.

Primary source docs:

- `docs/plans/memory-scale-foundation-roadmap.md`
- `docs/plans/progressive-graph-api-contract.md`
- `docs/plans/raptor-memory-live-go-plan-2026-06-26.md`
- `docs/plans/memory-read-write-tabs-contract.md`
- `docs/plans/odysseus-lens-ui-memory-interaction.md`

Current evidence:

- 2026-06-30: L4 readiness check found existing repo-only foundations for AI
  activity ledger, agent-run ledger, graph maintenance worker, graph review
  gate, graph-memory release evidence map, Memory Provenance Ledger,
  Universal Inbox RaptorGraph store and Nextcloud/RaptorGraph provenance.
- 2026-06-30: Memory Provenance Ledger coverage now explicitly includes
  `memory_user_interaction` events with model and agent stamps, while keeping
  `raw_content_visible=false` and rejecting unsafe payloads.
- 2026-06-30 Focused tests passed:
  `python -m pytest tests/test_ai_activity_ledger.py tests/test_ai_activity_audit_p2_contract.py tests/test_ai_activity_audit_p3_contract.py tests/test_ai_activity_diagnostics.py tests/test_agent_run_ledger.py tests/test_graph_maintenance_worker.py tests/test_graph_maintenance_review_gate.py tests/test_graph_memory_release_evidence_map.py tests/test_memory_provenance_ledger.py tests/test_universal_inbox_raptorgraph_store.py tests/test_nextcloud_raptorgraph_provenance.py -q`
  returned `72 passed, 1 warning`.

Slice queue:

| Slice | Class | Owner | Goal |
| --- | --- | --- | --- |
| L4-0-readiness-reconcile | safe_offline | Charlie | Done: current RaptorGraph/Memory readiness evidence is reconciled from existing contracts and tests. |
| L4-1-ai-activity-audit | repo_only | Bob | Done: AI activity ledger, diagnostics and agent-run linkage are covered by focused tests. |
| L4-2-graph-maintenance-evidence | repo_only | Bob | Done: graph maintenance worker, review gate and release evidence map are bounded, review-first and truth-write-disabled. |
| L4-3-memory-provenance-events | repo_only | Bob | Done: maintenance, retrieval, RaptorGraph mutation, write intent and user-interaction provenance events are redacted and queryable. |
| L4-4-live-graph-writes | needs_live_go | Charlie | Gated: reviewed Memory Write Intent may write to live/native memory or graph stores only after explicit live operator Go. |
| L4-5-rebuild/fullbuild/runtime-migration | needs_live_go | Charlie | Gated: live graph rebuild, RAPTOR fullbuild, Postgres migration or accelerator setup remain separate operator decisions. |

Parallel rule:

- Do not edit active Universal Inbox memory-write files while L1 is running
  unless Charlie serializes the slice.

L4 backend status:

- Backend/audit readiness is complete for the current masterplan scope.
- Live graph writes, rebuild/fullbuild, runtime migration and accelerator setup
  remain gated operational tracks, not blockers for the safe backend lane.

## Lane L5: Universal File IO / Export Plans

Goal:

Odysseus can understand common file families and create safe export plans such
as DOCX to PDF, PDF to images, image conversion, audio conversion and later
game asset conversion.

Current evidence:

- `universal-file-io-roadmap.md` defines capability registry, export intent,
  export plans and live converter gates.
- 2026-06-30: `src/universal_file_io.py` implements common document, PDF,
  image, audio, video and 2D/3D asset capability planning, redacted export
  intent parsing, and deterministic export plans without executing converters.
- 2026-06-30: DSGVO mode forces local-only converter selection in export plans;
  output refs are new derived refs and originals are never overwritten.
- 2026-06-30: Universal Inbox worker maps parser-broken PDFs to review-partial
  at orchestration level, preventing malformed PDFs from becoming hard No-Go
  when review placement is safer.
- 2026-06-30 Focused tests passed:
  `python -m pytest tests/test_universal_file_io.py tests/test_universal_inbox_worker.py tests/test_universal_inbox_extraction.py tests/test_pdf_extraction.py -q`
  returned `46 passed, 1 warning`.

Parallel rule:

- Safe export plans can run in parallel with L1.
- Live converters, Telegram file delivery and Nextcloud writes share L1 gates
  and must not bypass them.

Slice queue:

| Slice | Class | Owner | Goal |
| --- | --- | --- | --- |
| L5-0-status-check | safe_offline | Charlie | Done: UFIO roadmap and existing Inbox capabilities reconciled. |
| L5-1-file-capability-registry | repo_only | Bob | Done: common document/media/game asset families expose conversion-relevant capabilities. |
| L5-2-export-intent-contract | repo_only | Bob | Done: natural-language follow-ups produce redacted export intents linked to recent Inbox refs. |
| L5-3-export-capability-plan | repo_only | Bob | Done: deterministic, redacted plans cover document, image, audio, PDF-page-image and 3D asset conversions without execution. |
| L5-4-telegram-delivery-prep | safe_offline | Charlie | Planned: delivery contract before live sendDocument/sendPhoto/sendAudio. |
| L5-5-live-converters | needs_live_go | Charlie | Gated: LibreOffice/Pandoc/WeasyPrint, Pillow, ffmpeg, OCR, Blender/assimp execution needs explicit operator Go and tool checks. |

L5 backend status:

- UFIO1-UFIO3 are backend complete for the current masterplan scope.
- Live converter execution, Telegram delivery and Nextcloud export writes remain
  gated operational tracks, not blockers for safe export planning.

## Lane L6: Long PDF Extraction + RAG/Ingestion Reliability

Goal:

Odysseus handles long, partially broken, image-heavy and oversized PDFs as a
normal backend case across chat attachments, document viewer, Personal
Docs/RAG, Universal Inbox and Nextcloud ingestion.

Masterplan integration:

- Source roadmap `docs/plans/pdf-long-document-extraction-roadmap.md` is now
  explicitly integrated here as Lane L6.
- L6 is backend-complete for the current ABC scope. Follow-up visibility for
  review reasons, re-extract actions and operator controls belongs to L8/UI.
- L1 and L5 may depend on L6 status/warning contracts instead of reimplementing
  PDF parsing behavior.

Why this is high priority:

- Nextcloud import will inevitably encounter large PDFs, scans, invoices,
  contracts and mixed text/image documents.
- The current risk is silent loss: PDFs can appear accepted while no usable RAG
  chunks or review reason are produced.
- This lane strengthens L1 without requiring live writes, because most work is
  repo-only extraction, status mapping and regression tests.

Primary source doc:

- `docs/plans/pdf-long-document-extraction-roadmap.md`

Done state:

- A shared `src/pdf_extraction.py` contract provides page-wise, budgeted PDF
  extraction with explicit statuses: `completed`, `partial`, `metadata_only`,
  `needs_review`, `failed`.
- `src.personal_docs.extract_pdf_text`, RAG indexing, Universal Inbox,
  Nextcloud chunking and document processor flows use the same extraction
  semantics or compatibility wrappers.
- Partial PDFs produce usable chunks plus warning metadata instead of silently
  disappearing.
- Oversized PDFs remain metadata-only/review-gated where policy requires it.
- OCR/Vision fallback is optional, bounded and policy-aware; sensitive or
  local-only contexts must not send page images to external providers.
- Ledgers and reports store status, hashes, warning codes, chunk refs and
  review reasons only, never full extracted PDF text.

Current evidence:

- 2026-06-30: `src/pdf_extraction.py` provides the shared PDF contract,
  budget defaults, rawtext-free `to_dict()` reports and page-wise `pypdf`
  extraction.
- 2026-06-30: `src.personal_docs.extract_pdf_text` now delegates to the shared
  extractor as a compatibility wrapper.
- 2026-06-30 Focused tests passed:
  `python -m pytest tests/test_pdf_extraction.py tests/test_personal_docs_pdf_index.py tests/test_universal_inbox_extraction.py -q`
  returned `27 passed, 1 warning`.
- 2026-06-30: Personal Docs now includes PDF extraction status and warning
  codes in local index entries, and VectorRAG indexes partial PDFs while
  reporting skipped/review/failed PDFs with rawtext-free warning metadata.
- 2026-06-30 Focused tests passed:
  `python -m pytest tests/test_rag_pdf_partial_index.py tests/test_pdf_extraction.py tests/test_personal_docs_pdf_index.py tests/test_nextcloud_ingestion_integration.py tests/test_rag_manager_owner_compat.py tests/test_universal_inbox_extraction.py -q`
  returned `36 passed, 1 warning`.
- 2026-06-30: Universal Inbox maps shared PDF statuses and warning codes
  directly into extraction packets, and the Nextcloud chunk lane preserves PDF
  warning codes while keeping raw runtime text out of ledgers.
- 2026-06-30 Focused tests passed:
  `python -m pytest tests/test_universal_inbox_extraction.py tests/test_nextcloud_chunked_extraction.py tests/test_pdf_extraction.py tests/test_rag_pdf_partial_index.py tests/test_personal_docs_pdf_index.py -q`
  returned `40 passed, 1 warning`.
- 2026-06-30: `src.document_processor._process_pdf` now wraps the shared
  extractor while preserving chat/document markers, parser-failure handling and
  inline truncation behavior. OCR/Vision fallback remains deferred to L6-5.
- 2026-06-30 Focused tests passed:
  `python -m pytest tests/test_document_processor_pdf_extraction.py tests/test_build_user_content_pdf_marker.py tests/test_vision_owner_scope.py tests/test_pdf_extraction.py tests/test_universal_inbox_extraction.py tests/test_nextcloud_chunked_extraction.py tests/test_rag_pdf_partial_index.py tests/test_personal_docs_pdf_index.py -q`
  returned `50 passed, 1 warning`.
- 2026-06-30: The shared PDF extractor now has an optional mockable OCR
  adapter contract that is gated by `PdfExtractionBudget`, local-only policy
  context and OCR page limits before any OCR adapter can run.
- 2026-06-30 Focused tests passed:
  `python -m pytest tests/test_pdf_extraction.py tests/test_document_processor_pdf_extraction.py tests/test_universal_inbox_extraction.py tests/test_nextcloud_chunked_extraction.py tests/test_rag_pdf_partial_index.py tests/test_personal_docs_pdf_index.py -q`
  returned `48 passed, 1 warning`.
- 2026-06-30 Release-gate tests passed:
  `python -m pytest tests/test_universal_inbox_extraction.py tests/test_nextcloud_chunked_extraction.py tests/test_personal_docs_pdf_index.py tests/test_rag_manager_owner_compat.py tests/test_pdf_extraction.py tests/test_rag_pdf_partial_index.py tests/test_document_processor_pdf_extraction.py -q`
  returned `49 passed, 1 warning`.

Primary allowed paths:

- `src/pdf_extraction.py`
- `src/personal_docs.py`
- `src/rag_vector.py`
- `src/rag_manager.py`
- `src/universal_inbox_extraction.py`
- `src/nextcloud_chunked_extraction.py`
- `src/document_processor.py`
- `routes/*document*`
- `tests/test_pdf_extraction.py`
- `tests/test_rag_pdf_partial_index.py`
- `tests/test_document_processor_pdf_extraction.py`
- `tests/test_personal_docs_pdf_index.py`
- `tests/test_universal_inbox_extraction.py`
- `tests/test_nextcloud_chunked_extraction.py`
- `docs/plans/pdf-long-document-extraction-roadmap.md`

Slice queue:

| Slice | Class | Owner | Goal |
| --- | --- | --- | --- |
| L6-0-contract-and-budget | repo_only | Alice | Done: shared PDF status/warning/budget contract and rawtext-free reports are implemented and tested. |
| L6-1-pagewise-pypdf-extractor | repo_only | Bob | Done: page-wise `pypdf` extraction handles partial success, failed/empty classification and deterministic budget stops. |
| L6-2-personal-rag-integration | repo_only | Bob | Done: Personal Docs/RAG are partial-aware and report failed/skipped/review PDFs with warning metadata. |
| L6-3-inbox-nextcloud-integration | repo_only | Bob | Done: PDF statuses are mapped into Universal Inbox and Nextcloud chunk lanes without persisting raw extracted text. |
| L6-4-document-processor-wrapper | repo_only | Alice | Done: chat/document viewer output markers are preserved while PDF handling routes through the shared extractor. |
| L6-5-ocr-vision-policy-gate | repo_only, provider-gated | Charlie | Done: optional OCR hooks are gated by local-only/security policy and hard budgets before adapter execution. |
| L6-6-release-gates | safe_offline | Charlie | Done: focused regression suite passed and manual/UI visibility follow-ups are recorded. |

Parallel rule:

- L6 can run in parallel with L2/L3 docs or isolated backend work.
- L6 must serialize with L1 if both touch `src/universal_inbox_extraction.py`,
  `src/nextcloud_chunked_extraction.py` or shared memory/write-intent paths.
- L6 must run before broad L7 refactoring touches the same files.

Gates:

- External OCR/Vision provider use needs explicit policy clearance and bounded
  operator Go.
- Sensitive/local-only PDFs may only use local processing.
- UI review placement belongs to the UI agent; backend may expose status and
  reason contracts only.

## Lane L7: Large File Refactoring

Goal:

Reduce large runtime files without behavior redesign or visual drift.

Current evidence:

- `large-file-refactoring-overview.md` found 37 production/runtime files above
  2000 lines.
- `large-file-refactoring-abc-plan.md` recommends R0 guardrail/allowlist and
  R1 CSS ownership map first.
- 2026-06-30: L7 R0 is implemented. `scripts/large_file_report.py` produces an
  advisory JSON/Markdown report using the agreed bands `600-800`, `801-2000`
  and `>2000`, distinguishes source-like from production/runtime files, and
  marks generated/minified/planning artifacts as allowlisted without hiding
  them.
- 2026-06-30 current report summary: source-like 49 monitor, 81 warning, 42
  candidate; production/runtime 36 monitor, 54 warning, 39 candidate; 37
  non-allowlisted production candidates; 3 allowlisted large files.
- 2026-06-30 Focused tests passed:
  `python -m pytest tests/tools -q` returned `5 passed, 1 warning`.
- 2026-06-30: L7 R1 CSS ownership map is complete in
  `docs/plans/large-file-refactoring-css-map.md`. `static/style.css` was left
  untouched; the map defines target CSS bundles, risky global selectors, mobile
  cascade risks, split order and R2 verification gates.
- 2026-06-30: L7 R7 backend domain map is prepared in
  `docs/plans/large-file-refactoring-tool-implementations-map.md`.
  `src/tool_implementations.py` was left untouched; the next code step is a
  facade-first split into `src/tool_domains/`.
- 2026-06-30: L7 R7A/R7B are implemented. `src/tool_domains/common.py` now
  owns shared argument parsing, `src/tool_domains/repo_skills.py` owns
  chat-search, skills, recent changes and repo-management tools, and
  `src.tool_implementations` remains import-compatible.
- 2026-06-30 focused tests passed:
  `python -m pytest tests/test_manage_repos_read_tool.py tests/test_manage_skills_confirmation.py -q`
  returned `18 passed, 1 warning`; import smoke returned `imports ok`.
- 2026-06-30 broader R7 smoke passed:
  `python -m pytest tests/test_app_api_admin_mutation_blocklist.py tests/test_manage_repos_read_tool.py tests/test_manage_settings_service_v2.py tests/test_calendar_batch_events.py tests/test_cookbook_agent_tool_ssh_validation.py tests/test_owned_document_query.py tests/test_vault_password_not_in_argv.py -q`
  returned `188 passed, 1 warning`.
- 2026-06-30 large-file report after R7B: `src/tool_implementations.py` is
  reduced to 5631 lines and remains a candidate; `src/tool_domains/repo_skills.py`
  is 858 lines in the warning band.

Parallel rule:

- Do not start broad refactors while L1 hotfiles are active.
- First acceptable slice is docs/guardrail only, then disjoint backend files.
- Avoid `app.py`, active Inbox files, Telegram plugin and Project Runner files
  until their feature slices are committed.

Slice queue:

| Slice | Class | Owner | Goal |
| --- | --- | --- | --- |
| L7-R0-guardrail-allowlist | repo_only | Charlie | Done: repeatable oversized-file report and allowlist are implemented and tested. |
| L7-R1-css-ownership-map | repo_only | Alice | Done: `static/style.css` domains, risky selectors and target bundles are mapped before moving CSS. |
| L7-R2-css-split | repo_only | Charlie | Deferred: split CSS only after ownership map and visual smoke path are available. |
| L7-R7-tool-implementations-domain-map | repo_only | Bob | Done: public tool surface, direct callers, target domains and focused tests are mapped before moving code. |
| L7-R7A/R7B-tool-implementations-repo-skills | repo_only | Bob | Done: common parser and repo/skills/recent-changes/search tools moved behind the compatibility facade. |
| L7-R7C-tool-implementations-personal-workspace | repo_only | Bob | Next: move notes/calendar together behind the compatibility facade. |

Next safe slice:

- L7-R7C facade-first backend split for notes/calendar in
  `src/tool_implementations.py`, if the file is still clean. L7-R2 CSS split
  should wait until visual smoke coverage is available because
  `static/style.css` controls shell/chat/modal cascade.

## Lane L8: UI/V2 Integration

Goal:

Expose the completed backend contracts in v2 UI windows without deciding
placement inside backend tracks.

Ownership:

- UI agent owns visual layout, placement and component composition.
- Backend ABC only provides stable route contracts, diagnostics and handoff
  docs.

Current known UI gates:

- DSGVO global toggle placement.
- Project/Coding Agent window.
- Runner State window.
- Nextcloud/Inbox review queue window.
- Memory/RaptorGraph diagnostics window.

## Global Stop Rules

Stop or defer the active slice if:

- There are unrelated staged files.
- A slice would touch unrelated dirty user/agent changes.
- Secrets, tokens, chat IDs, private document contents, raw provider output or
  absolute private host paths would be persisted.
- A live write/network/provider/Telegram/Nextcloud/host/deploy/backup/restore
  action is needed without explicit bounded Go.
- A destructive git command would be needed.
- The implementation would bypass DSGVO/local-only rules.
- A required workflow skill would be triggered from untrusted document/user
  content instead of trusted runtime metadata.

## Commit And Push Policy

- Commit per completed slice when scope is clean and focused checks pass.
- Push only to `fuzzy/dev`.
- Never push to `origin`.
- Do not force push.
- Do not stage unrelated dirty/untracked files.
- If a slice is docs-only, focused tests may be omitted with a clear note.

## Recommended Execution Order

1. L3-0 through L3-2: done; local MCP smoke, runbook reconciliation and safe
   tool-policy evidence are recorded.
2. L3-4 through L3-6: done as plan; Playwright, GitHub and Podman read-only
   evidence paths are documented. L3-3 remains a Codex-side setup gate.
3. L1-0 through L1-5: done; safe backend path for Inbox -> Nextcloud proposal,
   review, optional WebDAV copy gate and memory intent is implemented and
   tested.
4. Decide whether to run L1-6 bounded live upload smoke after runtime env is
   configured on the server.
5. L2-0 and L2-1 can run in parallel if coding-agent dirty files are intentionally in
   scope.
6. Run L6-0 and L6-1 before broad Nextcloud import or RAG expansion, so long
   PDFs and partial PDFs stop disappearing silently.
7. Then choose between L2 route/policy consolidation, L5 export-plan completion
   or L4 graph/memory stabilization.
8. Start L7 refactoring only after feature hotfiles are quiet and L6 has either
   landed or explicitly deferred.

## Current Master Status

| Lane | Status | Why not complete |
| --- | --- | --- |
| L3 MCP Workbench + Podman Checks | partial, gated | L3-0 through L3-2 are done offline and L3-4 through L3-6 are planned; Codex-side service setup/live smoke remains gated. |
| L1 Nextcloud Live Write + Universal Inbox | partial, live-gated | Safe backend path is implemented and tested; bounded live upload smoke still needs operator Go plus runtime env. |
| L2 Coding Agent + Repo Control + Project Runner | partial | Backend pieces exist, but contracts need consolidation and UI handoff remains. |
| L4 Memory/RaptorGraph Stabilization | partial | Core memory work exists, but graph maintenance/audit/readiness needs reconciliation. |
| L5 Universal File IO | partial | Safe export plans exist as roadmap; live converters/delivery are gated. |
| L6 Long PDF Extraction + RAG/Ingestion Reliability | backend complete | L6-0 through L6-6 are implemented and tested; UI/operator visibility is tracked in L8 rather than this backend lane. |
| L7 Large File Refactoring | partial | R0 guardrail/allowlist, R1 CSS map, R7 domain map and R7A/R7B backend split are complete; more extraction waves remain, with CSS split waiting on visual smoke. |
| L8 UI/V2 Integration | gated | UI agent owns placement; backend must deliver stable contracts first. |

Recommended next human decision:

- Decide whether to run L1-6 as a bounded live upload smoke on the server. This
  requires the dedicated Nextcloud automation user, WebDAV runtime env and both
  live-write gates. If not, continue with L2 route/policy consolidation or L4
  graph/memory stabilization; L6 is backend-complete.
