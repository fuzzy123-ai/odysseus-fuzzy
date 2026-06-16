# Odysseus Plugin for Obsidian

Obsidian vault integration for the [Odysseus](https://github.com/fuzzy123-ai/odysseus-fuzzy) local AI workspace.

This is a **standalone plugin** designed to be dynamically loaded into the Odysseus workspace via the new Odysseus Plugin System API. It adds a complete vault workspace to Odysseus, offering a dockable editor UI, secure per-user vault isolation, graph and tag intelligence, AI-assisted project planning, memory review workflows, and a full suite of agent-callable tools for reading, writing, searching, organizing, exporting, and importing Markdown notes.

It also registers a read-only context provider for the Odysseus context-orchestrator. This allows Odysseus to preload relevant vault context through a generic plugin API while keeping all Obsidian-specific rules self-contained inside this plugin.

## Release Candidate `0.10.0-rc.1`

This RC is the current internal release line for the Obsidian plugin on the `dev` branch.

Included in this RC:

- Dockable vault UI, standalone app page, Markdown editing, search, tags, graph, history, import/export, and agent tools.
- Read-only context-provider integration for the Odysseus context orchestrator.
- Non-destructive project-planning previews plus confirmed apply flows.
- Memory review previews plus confirmed apply flows.

Not included in this RC:

- Vault-at-rest encryption for existing plaintext Markdown files on disk.
- RAPTOR rebuild/write workflows.
- Freshness Gate or quarantine write-side automation by default.

Release metadata rule:

- `plugins/obsidian/plugin.py` and `plugins/obsidian/plugin.json` must carry the same version string for every RC or release build.

## Feature Overview

### Vault Workspace UI

- Right-docked Obsidian panel, overlay window, fullscreen mode, and standalone app page at `/api/plugins/obsidian/app`.
- Resizable panel and sidebar widths with persisted local preferences.
- File tree browsing with folder expansion, folder-first sorting, inline selection, drag-and-drop Markdown import, and virtual project-planning session nodes.
- Note creation, editing, rename/move, delete, folder creation, and empty-folder deletion.
- Split Markdown editor with live rendered preview.
- Markdown toolbar actions for headings, bold, italic, quote, lists, code, links, wiki links, and tags.
- Autosave for open notes with toast feedback.
- Search panel with full-text matches, per-result open, rename, and delete actions.
- Standalone serving of frontend assets through `/api/plugins/obsidian/web/{filename}`.

### Markdown Preview and Navigation

- Wiki-link preprocessing for `[[Note]]` and `[[Note|Label]]` links.
- Clickable wiki links that open existing notes or create missing notes after confirmation.
- Markdown file links resolve relative to the current note when possible.
- Mermaid rendering through the shared Odysseus Markdown renderer.
- Hashtag normalization and clickable tag badges in preview.
- Tag detail popovers showing linked notes and one-click creation/opening of tag meta notes under `Tags/<tag>.md`.

### Tags, Graph, and Relationships

- Vault indexer extracts explicit hashtags while ignoring headings, code blocks, inline code, and URL fragments.
- Implicit tags are generated from note filenames.
- Graph payload includes Markdown links, filename mentions, shared-tag relationships, folders, and manually curated relationships.
- Graph view supports Cytoscape rendering with SVG fallback.
- Focused graph mode centers the current note or selected project folder.
- Lens presets support a whole-vault overview, a current-source view, and a review-queue view without requiring Derived Memory writes back into source notes.
- Published Views are intended to appear as generated Lens output and should stay visually distinct from ordinary source notes.
- Edge filtering by relationship type.
- Manual relationship storage in `.obsidian/relationships.json`.
- Supported manual relationship types include `manual`, `relates_to`, `depends_on`, `blocks`, and `supports`.

### Vault Security and Portability

- Per-user vault isolation when Odysseus authentication is enabled.
- Default vault location: `data/obsidian_vaults/<owner>`.
- Optional custom vault root through `OBSIDIAN_VAULT_DIR`, including `{owner}` interpolation.
- Path traversal protection for every file, folder, asset, archive, and tool path.
- Optional vault password protection with lock, unlock, and password removal flows.
- Password-derived vault metadata is stored under `.obsidian`; plaintext password values are not rendered back into the UI.
- ZIP export of the whole vault or a relative subtree.
- Optional encrypted ZIP exports.
- ZIP import with archive member validation to block escape paths and reserved internal files.

### RC Limitations

- Plugin password protection gates access through the plugin and tool APIs. It is not full vault-at-rest encryption for plaintext Markdown files already present on disk.
- RAPTOR status, lineage, dirty/tainted source metadata, and write-gate diagnostics are read-only in this release candidate. RAPTOR rebuild/write workflows remain disabled until explicitly enabled and tested.
- Freshness Gate quarantine and isolation workflows are audit/readiness surfaces by default. Hybrid retrieval filtering remains feature-flagged and backward-compatible with the existing context-provider contract.
- Browser smoke verification covers the standalone app shell, CSP-compatible external bootstrap, plugin web assets, unauthenticated data-route gating, authenticated vault loading, visible Cytoscape graph layout, focused graph refresh after selecting a note from the tree, visible current-node behavior, isolated graph filter classes, and a full-app graph-filter flow with real plugin routes. The full-app graph-filter smoke verifies that the filter panel remains open across graph re-renders and that `highlight` and `hide` search modes apply the expected Cytoscape classes.

### History and Undo

- Vault changes are recorded in `.obsidian/history.json`.
- History entries include action, owner, tool/source, paths, timestamps, reversible state, and before/after snapshots where useful.
- Safe undo is available for reversible create, update, rename, relationship-add, and relationship-delete actions.
- Undo refuses to overwrite newer user edits when file contents no longer match the recorded after-state.

### AI Project Planning

- Non-destructive project-plan previews for new or existing vault folders.
- Project templates for `software`, `research`, `writing`, `sec_ops`, `generic`, `teaching`, and `game_dev`.
- Aliases such as `ops`, `Unterricht`, `Education`, `GameDev`, and `game-dev` normalize to supported project kinds.
- Optional AI prompt improvement before preview generation.
- Optional sequential AI content generation for every planned Markdown file.
- Streaming preview endpoint with Server-Sent Events for file-by-file progress.
- Recoverable planning sessions stored under `.obsidian/project_planning_sessions.json`.
- Session list, load, delete/cancel, preview-stream, and apply endpoints.
- GameDev concept draft gate: content generation for game projects requires an editable approved concept first.
- Generated plans include frontmatter, schema tags, warnings, conflicts, project files, and suggested graph relationships.
- Apply flow requires confirmation and records created files and relationships in vault history.

### Memory Review

- Save-to-Obsidian preview workflow for reviewed memories, decisions, ideas, references, resources, meetings, and project notes.
- Actions: `save_to_obsidian`, `append_to_note`, `review_queue`, `memory_only`, and `discard`.
- Destination picker for folders or existing notes.
- Tag picker with autocomplete from existing vault tags and support for new tags.
- Link suggestions based on selected notes, requested links, and vault content.
- Conflict detection before writing new or appended note content.
- Apply flow requires confirmation for vault writes and records file/relationship changes in history.
- `review_queue` stages unresolved or duplicate-prone items under `AI Memory/Review Queue/...` so they stay out of settled canonical memory until someone reviews them.
- Queueing is a staging action in the Lens, not an implicit canonical promotion; it does not require a manual folder or note destination in the UI.

### Agent Tools

The plugin registers Odysseus agent tools through `ctx.register_tool(...)` so AI workflows can work with the vault directly:

- Core notes: `obsidian_list_notes`, `obsidian_tree`, `obsidian_read_note`, `obsidian_write_note`, `obsidian_search_notes`.
- Organization: `obsidian_create_folder`, `obsidian_rename_item`, `obsidian_delete_note`, `obsidian_delete_folder`.
- Tags and graph: `obsidian_list_tags`, `obsidian_graph`, `obsidian_list_relationships`, `obsidian_add_relationship`, `obsidian_delete_relationship`.
- History: `obsidian_history`, `obsidian_undo`.
- Vault security and portability: `obsidian_vault_status`, `obsidian_vault_set_password`, `obsidian_vault_lock`, `obsidian_vault_unlock`, `obsidian_vault_remove_password`, `obsidian_vault_export`, `obsidian_vault_import`.
- Project planning: `obsidian_project_plan_templates`, `obsidian_project_plan_improve_description`, `obsidian_project_plan_gamedev_draft`, `obsidian_project_plan_preview`, `obsidian_project_plan_apply`.
- Memory review: `obsidian_memory_review_preview`, `obsidian_memory_review_apply`.
- Release evidence: `obsidian_external_upgrade_proof_status`, `obsidian_external_upgrade_proof_run`.

Destructive or overwriting tool operations require explicit `confirm: true`.

### Vault Writing Rules

Odysseus maintains a visible rules note at `AI Memory/Canonical/Vault Writing Rules.md`.

- Markdown files have a softcap of 600 lines per file.
- The 600-line limit keeps each note small enough for manageable AI context during retrieval, review, and follow-up edits.
- If content would exceed 600 lines, external AI clients should split it by topic, phase, date, or subcomponent and connect the parts with links or an index note.
- Write responses may include `line_count`, `line_soft_cap`, and `warning`; agents should treat that warning as a request to split or reorganize future writes.

### Context Provider

The plugin registers `obsidian.vault_context` through `ctx.register_context_provider(...)` when the host Odysseus fork exposes that API.

Provider contract:

- Input: `owner`, `query`, `budget`, and `mode`.
- Output: `structured_state`, `snippets`, `sources`, `warnings`, `cache_key`, and read-only `memory` diagnostics.
- Capabilities: `chat`, `agent`, `vault`, `markdown`, `memory`, `readiness`, `freshness_gate`, `raptor`, and `hybrid_retrieval`.
- Frontmatter/properties are returned as structured state for machine-readable facts.
- Markdown body excerpts are returned as untrusted snippets.
- Sources include note path, title, tags, score, and match reason.
- Memory diagnostics include `readiness_gate`, `retrieval_policy`, `freshness_isolation_flags`, `raptor_lineage_flags`, and `raptor_write_gate`.
- Odysseus core forwards those diagnostics through a separate `Provider diagnostics` context message so readiness and isolation state are visible without duplicating note body content.
- Provider failures or vault-state warnings are forwarded through a separate compact `Provider warnings` context message.
- `obsidian_memory_status` also returns compact read-only `warnings`; Odysseus mission status can surface them as memory warnings without storing note content.
- Identical vault/query/budget output produces a stable cache key.
- Locked vaults return no note content and include a warning.

The provider does not add a public HTTP route. It uses plugin-internal vault services directly, so the Odysseus core can remain generic.

## API Surface

All routes are registered under `/api/plugins/obsidian`.

### UI and Status

- `GET /app` - standalone plugin app page.
- `GET /ai-status` - resolved Odysseus AI endpoint role/model for project planning.
- `GET /status` - vault password-protection and lock status.
- `GET /web/{filename}` - frontend assets.

### Vault Files

- `GET /files` - file tree.
- `GET /file?path=<path>` - read text files as JSON or serve binary assets.
- `POST /file` - create a file.
- `PUT /file` - update a file.
- `DELETE /file?path=<path>` - delete a file.
- `POST /folder` - create a folder.
- `DELETE /folder?path=<path>` - delete an empty folder.
- `POST /rename` - rename or move a file or folder.
- `GET /search?q=<query>` - full-text Markdown search.

### Vault Model

- `GET /tags` - explicit and implicit tag index.
- `GET /graph` - note graph with optional `focus` or `tag` query filters.
- `GET /relationships` - manual graph relationships.
- `POST /relationships` - add a manual relationship.
- `DELETE /relationships` - remove a manual relationship.

### Vault Security

- `POST /vault/password` - set or replace vault password protection.
- `POST /vault/lock` - lock the vault.
- `POST /vault/unlock` - unlock the vault.
- `DELETE /vault/password` - remove password protection.
- `POST /vault/export` - export base64 ZIP archive data.
- `POST /vault/import` - import base64 ZIP archive data.

### Project Planning

- `GET /project-plan/templates` - available planning templates.
- `GET /project-plan/sessions` - list visible planning sessions.
- `POST /project-plan/sessions` - create a planning session.
- `GET /project-plan/sessions/{session_id}` - load one session.
- `DELETE /project-plan/sessions/{session_id}` - cancel/delete one session.
- `POST /project-plan/sessions/{session_id}/preview-stream` - stream session preview progress.
- `POST /project-plan/sessions/{session_id}/apply` - apply a confirmed session.
- `POST /project-plan/improve-description` - AI-improve a project description.
- `POST /project-plan/gamedev-draft` - create an editable GameDev concept draft.
- `POST /project-plan/preview` - create a non-destructive plan preview.
- `POST /project-plan/preview-stream` - stream a non-destructive plan preview.
- `POST /project-plan/apply` - apply a confirmed plan.

### Memory Review

- `POST /memory-review/preview` - create a non-destructive memory review plan.
- `POST /memory-review/apply` - apply a confirmed memory review plan.

### Memory Query

- `GET /memory/query/status` - query-layer readiness, gate, warnings, and source/chunk counts.
- `GET /memory/query?q=<query>&top_k=<n>` - grounded answer text with citations, confidence, and readiness metadata.
- `GET /memory/rebuild-proof` - latest persisted ledger/index/query rebuild-proof status.
- `POST /memory/rebuild-proof/run` - run and persist a full ledger/index/query rebuild proof.
- `GET /memory/external-upgrade-proof` - external distribution/version-sync release-evidence status.
- `POST /memory/external-upgrade-proof/run` - run export/import/rebuild release evidence for an external plugin upgrade.

Planned pre-`1.0.0` answer-mode contract:

- The Answer Lens should surface whether a result came from `cloud`, `local`, or `extractive` mode.
- `extractive` is the safe grounded reading mode, not a disguised error state.
- Cloud mode may send only the retrieved snippets, source labels, and minimal metadata needed for the answer, not the whole vault.
- Model choices should resolve against Odysseus' existing model registry and defaults rather than a plugin-only provider list.
- Once the backend payload is stable, user-facing wording should keep `default`, explicit model choices, and fallback chains legible.

### History

- `GET /history` - recent vault actions.
- `POST /history/undo` - undo the latest safe reversible action for the current user.

## Repository Split

This repository contains only the Obsidian plugin.

Core Odysseus changes belong in [`fuzzy123-ai/odysseus-fuzzy`](https://github.com/fuzzy123-ai/odysseus-fuzzy), not in this plugin repository and not in upstream repositories owned by other projects.

The plugin expects the Odysseus core plugin manager to support:

- Dynamic plugin discovery from `plugins/<plugin-name>/plugin.py`.
- `ctx.add_router(...)`.
- `ctx.register_tool(...)` for agent-controllable vault actions.
- `ctx.register_context_provider(...)` for read-only vault context.
- `ctx.register_consolidation_job(...)` for planned background consolidation jobs.
- Manifest UI entries such as `PLUGIN["ui"]["open"]`.

Core Odysseus must not import this plugin directly. This plugin owns vault path resolution, lock checks, owner isolation, Frontmatter parsing, tags, graph relationships, and snippet selection.

## Current Implementation Status

Implemented in the active Fuzzy/Odysseus branch:

- Phase 0: current Obsidian UI/graph stabilization and release-candidate preparation.
- Phase 1: generic Core plugin API for context providers and consolidation-job specs.
- Phase 2: plugin-internal vault service layer reused by routes and agent tools.
- Phase 3: `obsidian.vault_context` read-only provider with Frontmatter-first structured state, untrusted snippets, sources, warnings, stable cache key, memory/readiness diagnostics, and locked-vault safety.
- Phase 4: Core Context-Orchestrator for chat and agent mode with provider preloading, provider capability metadata, provider diagnostics, stable prompt prefix, token budgeting, and final overflow guard.
- Phase 5: preventive history compaction and persistent task-state blocks.
- Phase 6: background consolidation jobs. The Obsidian job writes `.obsidian/consolidation_report.json` with duplicate-title candidates, orphan-note candidates, and frontmatter suggestions. It never deletes or rewrites notes.
- Phase 7: rollout docs, feature flags, and regression tests. Core feature flags are `context_provider_preload` and `consolidation_jobs`.

## Install

From the root of an Odysseus checkout:

```powershell
git clone -b dev https://github.com/fuzzy123-ai/Odysseus-plugin-obsidian.git plugins/obsidian
```

Restart Odysseus after cloning. The plugin manager imports `plugins/obsidian/plugin.py`, registers the API routes, and exposes the UI entry at `/api/plugins/obsidian/app`.

The panel can be opened from the Odysseus plugin settings UI when the plugin is enabled.

Host compatibility for this RC:

- Supported host line: the `dev` branch of [`fuzzy123-ai/odysseus-fuzzy`](https://github.com/fuzzy123-ai/odysseus-fuzzy).
- Expected install path: `plugins/obsidian`.
- The plugin is loaded dynamically by the Odysseus plugin manager; no core code copy step is required.

## Upgrade

For an existing checkout where the plugin already lives in `plugins/obsidian`:

1. Keep your local Odysseus worktree clean or commit your changes first.
2. Change into `plugins/obsidian`.
3. Fetch the latest refs and tags from origin.
4. Update the plugin checkout to the target commit on `dev`.
5. Confirm that `plugin.py` and `plugin.json` still advertise the same version string.
6. Restart Odysseus so the plugin manager reloads `plugin.py` and the frontend assets.
7. Re-run the focused RC checks that matter for your slice.

Example fast-forward upgrade:

```powershell
cd plugins/obsidian
git fetch --tags origin
git checkout dev
git pull --ff-only
```

For the current RC line, the minimum focused verification is:

```powershell
python -m pytest plugins/obsidian/tests/test_plugin_obsidian.py tests/test_obsidian_memory_mission_contract.py tests/test_obsidian_sidebar_static.py tests/test_plugin_obsidian_load.py tests/test_plugin_system.py
node --check plugins/obsidian/frontend/main.js
python -m pytest tests/test_agent_run_ledger.py tests/test_context_orchestrator.py tests/test_session_status_indicators.py tests/test_shell_policy.py tests/test_shell_routes.py
```

## RC Manual Checks

Before treating `0.10.0-rc.1` as an internal release candidate, manually confirm:

- Fresh install path works from a clean Odysseus checkout with `git clone -b dev ... plugins/obsidian`.
- Existing `plugins/obsidian` checkout upgrades cleanly and still loads after restart.
- `plugin.py` and `plugin.json` advertise the same RC version string.
- A small vault export and import work without unexpected path leakage or reserved-file writes.
- The standalone app page, authenticated vault load, and graph filter flows still match the documented smoke results.
- Any release archive places the plugin files at the archive root so extracting it yields `plugin.py`, `plugin.json`, `README.md`, `frontend/`, and `backend/` directly.

Current RC residual risks to keep visible:

- Vault password protection is an access-control layer for the plugin and tools, not full disk encryption for existing plaintext Markdown files.
- RAPTOR/readiness surfaces remain read-only in this RC; rebuild/write proof is tracked separately from the plugin shell and Lens UX.
- Import, project-plan apply, memory-review apply, and destructive note operations still depend on confirmation-gate discipline.
- `AI Memory/Review Queue/` contains staged review items and duplicates-under-investigation, not settled canonical memory.
- The standalone app shell may load before login, but authenticated data routes must remain protected.
- Memory-first `1.0.0` still depends on separate ledger/index/query/rebuild evidence; the RC docs should not blur that boundary.

## RC Distribution Runbook

Use this as the short manual distribution pass for the current RC line.

### 1. Fresh install

From a clean Odysseus checkout:

```powershell
git clone -b dev https://github.com/fuzzy123-ai/Odysseus-plugin-obsidian.git plugins/obsidian
```

Then:

1. Restart Odysseus.
2. Open `/api/plugins/obsidian/app`.
3. Confirm the plugin UI shell loads without missing asset errors.

Evidence to record:

- Host checkout commit SHA.
- Plugin checkout commit SHA.
- Observed plugin version string.
- Pass/fail for plugin app load.

### 2. Upgrade existing checkout

From an Odysseus checkout where the plugin already exists:

```powershell
cd plugins/obsidian
git fetch --tags origin
git checkout dev
git pull --ff-only
```

Then:

1. Restart Odysseus.
2. Re-open `/api/plugins/obsidian/app`.
3. Confirm `plugin.py` and `plugin.json` still advertise the same version string.

Evidence to record:

- Previous plugin commit SHA if known.
- New plugin commit SHA.
- Version string from `plugin.py`.
- Version string from `plugin.json`.
- Pass/fail for post-upgrade app load.

### 3. Release ZIP layout check

If distributing a ZIP outside git:

1. Extract the archive into a temp folder.
2. Confirm the extraction root contains `plugin.py`, `plugin.json`, `README.md`, `frontend/`, and `backend/` directly.
3. Confirm there is no extra wrapper directory above those files.
4. Confirm local-only artifacts such as `__pycache__/`, `.obsidian/`, and temporary smoke-output files are absent.

Evidence to record:

- Archive filename under review.
- Pass/fail for root layout.
- Pass/fail for local-artifact absence.
- Notes on any unexpected extra files.

### 4. Export / import smoke

Before wider RC use, run one small export/import loop:

1. Create or choose a small vault with a few Markdown notes.
2. Export the vault once without password protection and once with password protection if that path is in scope for the release cut.
3. Import the archive into a clean test vault.
4. Confirm the imported vault opens, expected notes are present, and no reserved-file or path-leak warnings appear unexpectedly.

Evidence to record:

- Vault fixture or test folder used.
- Pass/fail for plain export/import.
- Pass/fail for password-protected export/import if exercised.
- Notes on any path, reserved-file, or restore anomalies.

### 5. Minimal release evidence template

Record the result in this shape:

```text
RC line: 0.10.0-rc.1
Host commit: <sha>
Plugin commit: <sha>
Version sync: pass|fail
Fresh install: pass|fail
Upgrade: pass|fail
Export/Import: pass|fail
ZIP layout: pass|fail
Notes: <short note>
```

## RC Checklist Sync

Keep these documents aligned for each RC checkpoint:

1. `README.md`: install path, upgrade path, RC runbook summary, and top-level restrictions.
2. `plugins/obsidian/README.md`: RC scope, manual checks, distribution runbook, archive layout, and plugin-specific residual risks.
3. `plugins/obsidian/SECURITY.md`: supported RC line, security notes, and vulnerability-reporting path.
4. `plugins/obsidian/plugin.py` plus `plugins/obsidian/plugin.json`: identical version string for the active RC line.

## Memory-first 1.0 Readiness

The Obsidian plugin is now tracked as a Lens surface inside the broader Memory-first `1.0.0` plan.

Lens-side evidence currently in place:

- Obsidian is documented as a Lens for sources, review, graph exploration, and published views rather than the only memory core.
- Memory Review can stage unresolved items into `AI Memory/Review Queue/...` instead of implicitly promoting them.
- Graph Lens presets support whole-vault overview, current-source focus, and review-queue focus.
- Published Views are intended to stay visibly distinct from ordinary source notes in the Lens UI.

Evidence now in place for the internal Memory-first `1.0.0` package:

- Source/index ledger evidence.
- Derived index and query-layer evidence with provenance, citations, and confidence.
- Background automation evidence showing rebuildable Derived Data without silent rewrites to source Markdown.
- External upgrade/rebuild proof, version sync, and broader safety/regression cut.

DeepSeek / graceful degradation status for the current pre-`1.0.0` gate:

- The Lens-side contract is documented: `cloud`, `local`, and `extractive` should be explained calmly and explicitly.
- `extractive` remains the final safe fallback and should read like a grounded mode, not panic text.
- Cloud privacy wording stays narrow: only retrieved snippets plus minimal citation metadata may leave the host.
- The Answer Lens UI labels remain blocked until the backend hands off stable payload fields for mode, model/provider, fallback reason, and warnings.

Before an external release, keep one manual fresh-install/upgrade pass on a true target environment as a release approval step. The DeepSeek/model-router gate must also be implemented and evidenced before claiming final `1.0.0`.

### Memory-first Demo Runbook

Use this as the short Lens-side demo path once backend evidence is ready enough to support it.

1. Place a source into the vault or a documented sync/archive source location.
2. Show the relevant status transition for ledger, index, or automation work.
3. Ask a memory question that should depend on that source.
4. Verify that the answer shows sources and confidence rather than acting like unsupported free text.
5. Jump into a Lens surface such as Source View, Graph Lens, or Review Queue to explain why the answer is grounded.
6. Optionally show a staged review or published-view step without rewriting the original source artifact.

Record the demo outcome in this shape:

- Source used.
- Status transition or trigger shown.
- Question asked.
- Answer surface shown.
- Lens surface used for traceability.
- `real`, `mock/spec`, `deferred`, or `release manual` for each critical step.

### Source View Lens Contract

The next Lens contract layer is `Source Views`: the UI should make it obvious where retrieved knowledge came from and how strongly it is supported.

Lens-side contract:

- A **Source Card** should identify whether the evidence comes from a Markdown source, chat/capture, document, or attachment metadata.
- A **Chunk Card** should show the relevant excerpt or unit of evidence instead of only naming the source file.
- A **Provenance breadcrumb** should read like `answer -> chunk -> source -> optional graph jump`.
- **Confidence** should be shown with explanation text, not as an unexplained magic score.
- `stale`, `dirty`, `failed`, and similar backend states should be translated into user-facing status text rather than raw internal errors.

Stable fields in the current backend/Lens line:

- A readable source type.
- A readable source path or source label.
- A title and excerpt.
- Some form of confidence signal.

Future Source View UI followups:

- Exact chunk identity field.
- Final hash/version field name.
- Final `indexed_at` field shape.
- Stable mapping for backend freshness/failure states.
- Final graph-jump payload for source-to-graph navigation.

### Query Answer Lens Contract

The Answer Lens should make Memory-first retrieval legible instead of presenting unsupported text as if it were a final truth source.

Lens-side contract:

- The answer card should show the answer together with readable readiness state, confidence, and uncertainty wording.
- Citations should stay attached to the answer and expose both source path and snippet-level evidence.
- A provenance breadcrumb should read like `answer -> citation snippet -> source note -> graph jump`.
- Graph jumps and source opens are Lens navigation features; they do not imply source mutation or canonical promotion.
- If the query layer is blocked, stale, or empty, the Lens should explain that derived memory is not ready instead of pretending the source data vanished.

Current UI shape in this RC line:

- `KI Spark -> Answer Lens` provides a read-only query surface with readiness, gate, confidence, citations, and per-citation source/graph jumps.
- The Lens reads from `/memory/query/status` and `/memory/query` and keeps failure states user-visible.
- Low-confidence or citation-free answers are framed as uncertainty, not as silent success.
- The Answer Lens now also surfaces `answer_mode`, selected role/model/endpoint, fallback reason, context-token visibility, and model-capability warnings from the stable M6 payload.

### Automation Review Lens Contract

Background automation should become understandable in the Lens before it becomes more powerful.

Lens-side contract:

- The UI should explain what ran automatically, what only touched rebuildable Derived Data, and what still requires human review.
- `needs_review` means "staged or suggested, waiting for a person", not "auto-approved".
- `dirty` or `stale` means data may be out of date, not that user-authored source files were silently modified.
- `failed` means a background run did not complete; it should not imply source corruption without separate evidence.
- Slow, opportunistic MiniPC behavior is acceptable when the wording makes that expectation clear.

User-facing status language:

- `not_run` - not started yet.
- `running` - currently updating in the background.
- `ready` - usable for the current Lens view.
- `dirty` - source changes mean a refresh is needed.
- `failed` - the latest background run did not finish successfully.
- `needs_review` - a result exists, but promotion or publication is intentionally waiting for a person.

Resolved automation payloads in the current backend line:

- Final automation status payload.
- Safe-to-show cost, cooldown, and backoff fields.
- Stable timestamps, last-run summaries, failures, and warnings.
- Future UI followup: richer visual boundary between `ready` and `needs_review`.

### Nextcloud / Archive Source Lens Contract

External sync folders and file archives should appear in the Lens as source providers, not as silently promoted memory.

Lens-side contract:

- A synced Nextcloud folder should be explainable as a source location the Memory system can index.
- Archive-backed files should remain source artifacts until a separate review, staging, or publication step says otherwise.
- The Lens may later show source provider, readable path, and index status for those files without implying ownership over the original file.
- Discovery tooling such as filesystem or archive search is a helper for finding and repairing sources, not the product core by itself.

Deferred until the Nextcloud source bridge is activated:

- Final source-provider identifier for external files.
- Stable external-source status fields.
- Final read-only vs staged-write boundary for synced/archive material.

### Integration Readiness Audit (2026-06-16)

Current audit buckets:

- `real`: `GET /memory/query/status` and `GET /memory/query` now expose readiness, gate, citations, confidence, `confidence_score`, `path_prefix`, warnings, and query-cache metadata; focused query-layer tests pass.
- `real`: `GET /memory/automation/status` and `POST /memory/automation/run` now expose pending actions, cooldown/backoff cost controls, last-run summaries, and explicit `source_note_writes: false` safety; focused automation tests pass.
- `real`: `KI Spark -> Answer Lens` is wired against the live query endpoints and keeps blocked/low-confidence states user-visible instead of pretending success.
- `mock/spec`: Source View remains a contract-level surface for richer source-type/chunk/version drilldown; the separate runtime Lens for those fields is not fully materialized yet.
- `blocked/deferred`: external-source/Nextcloud provider identifiers and stable external-file status fields are intentionally out of scope until the Nextcloud instance exists.
- `real`: `GET /memory/rebuild-proof`, `POST /memory/rebuild-proof/run`, `GET /memory/external-upgrade-proof`, and `POST /memory/external-upgrade-proof/run` now expose persisted rebuild proof plus external distribution/version-sync evidence; focused external-proof tests pass for plain/encrypted export-import and citation-bearing rebuilds.
- `release manual`: fresh install/upgrade evidence on a truly external target environment remains a manual release approval step, not a Bob implementation blocker.
- `real`: broader `1.0` regression/evidence closure is complete for the current internal package: Memory/External-Proof `52 passed`; Obsidian/Static/Context `70 passed`.

Evidence verified in this audit:

- `plugins/obsidian/tests/test_query_layer_backend.py`: focused query contract smoke passes.
- `plugins/obsidian/tests/test_memory_automation_backend.py`: focused automation contract smoke passes.
- `plugins/obsidian/tests/test_memory_rebuild_proof_backend.py`: focused rebuild-proof smoke passes.
- `plugins/obsidian/tests/test_external_upgrade_proof_backend.py`: focused external distribution/export-import/rebuild smoke passes.

## Release Archive Layout

If the plugin is distributed as a ZIP outside a git checkout, the archive root should contain the plugin files directly:

```text
plugin.py
plugin.json
README.md
SECURITY.md
CONTRIBUTING.md
frontend/
backend/
tests/
```

The archive should not introduce an extra top-level wrapper directory if the intended install target is already `plugins/obsidian`.

Before packaging a release archive, remove generated local-only artifacts such as `__pycache__/`, `.obsidian/`, or any temporary smoke/output files. They are not part of the distributable plugin layout.

## Configuration

By default, vaults are stored per user under Odysseus' data directory:

```text
data/obsidian_vaults/<owner>
```

To point to an existing vault path, set:

```text
OBSIDIAN_VAULT_DIR=C:\path\to\vaults\{owner}
```

`{owner}` is replaced with the authenticated username, or `default` when auth is disabled.

The plugin resolves AI calls through Odysseus' endpoint resolver. Project planning first tries the `utility` role and falls back to `default`.

## Development

Use the `dev` branch for active work and open pull requests against `dev`.

Run the plugin tests from an Odysseus checkout after cloning this repository into `plugins/obsidian`:

```powershell
python -m pytest plugins/obsidian/tests/test_plugin_obsidian.py
```

When testing against the Fuzzy/Odysseus fork, also run the plugin-manager and context-provider integration tests:

```powershell
python -m pytest tests/test_plugin_obsidian_load.py tests/test_plugin_system.py
```

The host Odysseus checkout also contains static sidebar contract tests:

```powershell
python -m pytest tests/test_obsidian_sidebar_static.py
```

For a quick frontend syntax check:

```powershell
node --check plugins/obsidian/frontend/main.js
```

## Files

- `plugin.py` - Odysseus plugin manifest, setup hook, and agent tool handlers.
- `backend/context_provider.py` - read-only vault context provider for the Odysseus context-orchestrator API, including memory/readiness diagnostics.
- `backend/consolidation_job.py` - non-destructive vault consolidation report job.
- `backend/vault_service.py` - shared vault path, file, search, tree, and mutation helpers used by routes, tools, and the context provider.
- `backend/routes.py` - FastAPI routes and request models.
- `backend/vault_model.py` - tag extraction, vault indexing, graph construction, and manual relationships.
- `backend/vault_security.py` - password status, lock/unlock, ZIP export/import, and archive validation.
- `backend/vault_history.py` - history storage and undo metadata.
- `backend/project_planning.py` - project templates, preview/apply validation, AI content generation, and GameDev concept flow.
- `backend/memory_review.py` - reviewed-memory preview/apply planning.
- `frontend/main.js` - dockable UI, editor, search, graph, project planner, memory review, import/export, and settings interactions.
- `frontend/style.css` - plugin panel, editor, graph, project planning, memory review, responsive, and standalone styles.
- `frontend/cytoscape.min.js` - bundled graph renderer dependency.
