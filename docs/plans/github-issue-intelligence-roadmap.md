# GitHub Issue Intelligence Roadmap

Stand: 2026-06-20

Status: **GHISS0-GHISS8 repo slices complete; live GitHub sync/write rollout remains gated**

## Goal

Odysseus soll GitHub Issues als strukturierte Arbeitsobjekte verstehen:

- moegliche Duplicate Issues vor dem Erstellen erkennen;
- Issues mit konsistenten Feldern wie Priority, Effort, Area und Dates
  triagieren;
- GitHub Issue Fields nutzen, wenn sie im Ziel-Org verfuegbar sind;
- auf Labels oder lokale Felder zurueckfallen, wenn GitHub Issue Fields nicht
  verfuegbar sind;
- die Funktion spaeter ueber Odysseus Tools und den Odysseus MCP Server
  bereitstellen.

## External Signal

GitHub hat am 2026-06-18 im Changelog eine Public Preview fuer Duplicate Issue
Detection und GitHub MCP Server Support fuer Issue Fields angekuendigt.

Relevante Produkt-Richtung:

- Duplicate Detection waehrend der Issue-Erstellung, mit bis zu drei
  Inline-Vorschlaegen.
- Issue Fields als org-weite, typisierte Metadaten fuer Issues.
- GitHub MCP Server kann Issue Fields lesen, schreiben und danach filtern.

## Why Odysseus Should Own A Field Model

Odysseus sollte nicht direkt ueberall GitHub Labels oder GitHub Field IDs in
Tool-, UI- und Memory-Code verstreuen. Stattdessen bekommt Odysseus ein
provider-neutrales internes Feldmodell.

Das interne Modell ist die kanonische Sprache:

```json
{
  "type": "bug",
  "priority": "high",
  "effort": "medium",
  "area": "cookbook",
  "status": "triage",
  "start_date": null,
  "target_date": "2026-07-01",
  "duplicate_of": null
}
```

GitHub ist dann nur ein Backend:

- Wenn GitHub Issue Fields verfuegbar sind, schreibt Odysseus echte Fields.
- Wenn nicht, nutzt Odysseus Labels wie `priority/high` oder `area/cookbook`.
- Fuer lokale Drafts, Obsidian, Tasks oder Universal Inbox bleibt dieselbe
  Feldsprache verwendbar.

## Architecture Fit

Bestehende Odysseus-Bausteine:

- `core/database.py`: zentrale SQLAlchemy-Modelle fuer persistente Objekte.
- `src/mcp_manager.py` und `routes/mcp_routes.py`: externer MCP-Client-Manager.
- `plugins/mcp_server/plugin.py`: Odysseus kann eigene Tools als MCP Server
  policy-gated exponieren.
- `src/tool_schemas.py` und `src/tool_implementations.py`: Agent Tools.
- FastEmbed/Chroma/RAG: vorhandene semantische Suche fuer Duplicate Detection.
- Scheduled Tasks: spaeter fuer periodisches Issue-Sync und Reindexing nutzbar.

Neue Schicht:

```text
GitHub API / GitHub MCP
  -> Issue Sync Adapter
  -> IssueRecord + IssueFieldValue
  -> Issue Embedding Index
  -> Duplicate Candidate Service
  -> Triage Field Mapper
  -> Tools / Routes / UI / MCP exposure
```

## Data Model Sketch

### IssueRecord

Provider-neutrale Kopie eines externen oder lokalen Issues.

Fields:

- `id`
- `owner`
- `provider`: `github`, `local`, `obsidian`
- `repository`: e.g. `fuzzy123-ai/odysseus-fuzzy`
- `external_id`: GitHub issue number or provider ID
- `external_node_id`
- `title`
- `body`
- `state`: `open`, `closed`, `draft`
- `url`
- `labels_json`
- `author`
- `created_at`, `updated_at`, `last_synced_at`

### IssueFieldDefinition

Kanonische Felddefinition, optional mit Provider-Mapping.

Fields:

- `id`
- `owner`
- `name`: `priority`, `effort`, `area`, `start_date`, `target_date`
- `field_type`: `single_select`, `text`, `number`, `date`
- `allowed_values_json`
- `visibility`: `public`, `private`
- `provider`: optional
- `provider_field_id`: optional GitHub field ID
- `label_prefix`: optional fallback label prefix, e.g. `priority/`

### IssueFieldValue

Konkreter Feldwert auf einem Issue.

Fields:

- `id`
- `issue_id`
- `field_name`
- `value_json`
- `source`: `user`, `agent`, `github`, `inferred`, `migration`
- `confidence`
- `updated_at`

### IssueDuplicateCandidate

Auditierbare Duplicate-Erkennung.

Fields:

- `id`
- `source_issue_id`
- `candidate_issue_id`
- `score`
- `reason`
- `decision`: `pending`, `accepted`, `rejected`
- `created_at`

## Default Internal Fields

Initiale Felder:

- `type`: `bug`, `task`, `feature`, `question`, `docs`
- `priority`: `urgent`, `high`, `medium`, `low`
- `effort`: `high`, `medium`, `low`
- `area`: freie oder konfigurierbare Komponente, z. B. `cookbook`,
  `mcp`, `telegram`, `obsidian`, `memory`, `ui`, `ops`
- `status`: `triage`, `ready`, `blocked`, `in_progress`, `done`
- `start_date`: date
- `target_date`: date
- `duplicate_of`: issue reference

Diese Felder bleiben klein. Weitere Felder brauchen konkrete Workflows, nicht
nur Vollstaendigkeit.

## Duplicate Detection

### Input Text

Indexiert wird eine kompakte Issue-Repraesentation:

```text
title
body summary
labels
area
type
recent comments summary, optional later
```

### Ranking

Erste Version:

- semantische Aehnlichkeit via FastEmbed/Chroma;
- optional keyword boost fuer identische Tokens aus Titel/Labels;
- Status-Filter: bevorzugt offene Issues, geschlossene aber nicht verstecken;
- Top 3 Kandidaten fuer Create-Preview.

### Output

Jeder Kandidat enthaelt:

- issue number and title;
- url;
- state;
- score;
- short reason;
- matching terms or fields;
- recommended action: `review`, `link_duplicate`, `continue_create`.

## GitHub Field Mapping

Odysseus prueft pro Repository/Org:

1. Sind GitHub Issue Fields verfuegbar?
2. Gibt es passende Felder fuer Odysseus-Defaults?
3. Darf der Token diese Felder lesen/schreiben?

Mapping-Strategie:

- `priority` -> GitHub Issue Field `Priority`
- `effort` -> GitHub Issue Field `Effort`
- `start_date` -> GitHub Issue Field `Start date`
- `target_date` -> GitHub Issue Field `Target date`
- `area` -> GitHub Issue Field `Area` oder Label `area/<value>`
- `type` -> GitHub Issue type if available, otherwise label `type/<value>`

Fallback-Strategie:

- Wenn Field write scheitert, Issue trotzdem erstellen.
- Fallback labels nur nach expliziter Konfiguration oder sicherem Prefix.
- Write-Ergebnis muss pro Feld reporten: `field`, `method`, `status`,
  `error_redacted`.

## Tools

Neue Agent Tools:

### `github_issue_sync`

Sync issues from one repo into Odysseus.

Actions:

- `sync_repo`
- `sync_issue`
- `status`

### `github_issue_find_duplicates`

Find likely duplicate issues for a title/body draft.

Inputs:

- `repository`
- `title`
- `body`
- `limit`

### `github_issue_create_triaged`

Create a GitHub issue with duplicate preview and internal fields.

Inputs:

- `repository`
- `title`
- `body`
- `fields`
- `confirm_create`
- `duplicate_decision`

Stop rule:

- If high-confidence duplicates exist and `confirm_create` is not true, do not
  create.

### `github_issue_set_fields`

Set or update Odysseus issue fields and project them to GitHub.

Inputs:

- `repository`
- `issue_number`
- `fields`
- `confirm_write`

## Routes And UI

Backend routes:

- `GET /api/github-issues/config`
- `POST /api/github-issues/sync`
- `POST /api/github-issues/duplicates`
- `POST /api/github-issues/create-draft`
- `POST /api/github-issues/create`
- `PATCH /api/github-issues/{id}/fields`

UI surface:

- Admin/Integrations page for GitHub Issue Intelligence config.
- Issue draft panel with duplicate preview.
- Field chips/editor: type, priority, effort, area, dates.
- Sync status and last indexed timestamp.

Keep the first UI utilitarian: a compact tool surface, not a landing page.

## MCP Exposure

Odysseus MCP Server can expose read-only issue tools by default once the feature
is stable:

- `github_issue_find_duplicates`
- `github_issue_sync_status`

Write tools remain gated:

- `github_issue_create_triaged`
- `github_issue_set_fields`

Policy requirements:

- no token values in MCP responses;
- no raw GitHub error payloads if they may contain secrets;
- write tools require owner-scoped write permission;
- no generic GitHub API passthrough as a default MCP tool.

## Safety And Privacy

- Store GitHub tokens through existing secret/token mechanisms, not roadmap
  files.
- Redact issue bodies in logs where repo privacy is unknown.
- Do not index private repos into shared ownerless collections.
- Duplicate results must be owner/repo scoped.
- High-confidence duplicate detection is advisory first; no auto-close in v1.
- No bulk migration from labels to fields without preview and explicit confirm.

## Implementation Slices

### GHISS0 Contract And Tests

Goal:
- Define internal issue field contract and mapping rules.

Expected files:

- `src/github_issue_fields.py`
- `tests/test_github_issue_fields.py`
- `docs/plans/github-issue-intelligence-roadmap.md`

Done when:

- Field definitions validate type/value/date constraints.
- GitHub-field and label-fallback mapping are deterministic.
- Unknown fields fail closed unless explicitly configured.

Status: done.

Evidence:

- `src/github_issue_fields.py` defines the provider-neutral field contract,
  default field definitions, validation, GitHub Issue Field projection,
  deterministic label fallback and redacted write-report planning.
- `tests/test_github_issue_fields.py` covers default normalization,
  unknown-field fail-closed behavior, configured custom fields, invalid
  value/date/ref rejection, secret marker rejection, GitHub-field preference,
  label fallback, local-only projection and structured write reports.
- Verification 2026-07-03:
  `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_github_issue_fields.py -q`
  -> `7 passed, 1 warning`.

### GHISS1 Persistence

Goal:
- Add issue tables and migrations/backfill helpers.

Expected files:

- `core/database.py`
- migration helper in existing startup migration style
- `tests/test_github_issue_models.py`

Done when:

- Issue rows are owner-scoped.
- Field values round-trip as JSON.
- Duplicate candidate rows can be accepted/rejected without deleting evidence.

Status: done.

Evidence:

- `core/database.py` defines `GitHubIssueRecord`,
  `GitHubIssueFieldValue` and `GitHubIssueDuplicateCandidate` as owner-scoped
  SQLAlchemy models.
- New tables are created through the existing startup migration path
  `Base.metadata.create_all(bind=engine)`; no destructive migration or live
  GitHub action is involved.
- `tests/test_github_issue_models.py` verifies owner-scoped issue rows, JSON
  label and field-value roundtrips, unique external issue identity per
  owner/provider/repository, duplicate candidate accept/reject decisions, and
  evidence preservation.
- Verification 2026-07-03:
  `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_github_issue_fields.py tests\test_github_issue_models.py -q`
  -> `11 passed, 1 warning`.

### GHISS2 Sync Adapter

Goal:
- Read issues from GitHub into IssueRecord.

Expected files:

- `src/github_issue_sync.py`
- `tests/test_github_issue_sync.py`

Done when:

- Fake GitHub client tests cover pagination, updates, closed issues, labels.
- Token errors are redacted.
- Sync can be run incrementally.

Status: done.

Evidence:

- `src/github_issue_sync.py` defines a read-only, client-supplied sync adapter
  that upserts provider issues into owner/repo-scoped `GitHubIssueRecord`
  rows. It does not handle tokens, perform network calls or write to GitHub.
- The adapter supports pagination, local update detection, closed issue state,
  label persistence and an incremental `last_synced_at` watermark.
- Provider/client failures are rolled back and reported through
  `GitHubIssueSyncError` with secret markers redacted.
- `tests/test_github_issue_sync.py` verifies pagination, updates, closed issue
  handling, label roundtrips, explicit and implicit incremental watermarks,
  token-error redaction and no partial commits after client failure.
- Verification 2026-07-03:
  `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_github_issue_fields.py tests\test_github_issue_models.py tests\test_github_issue_sync.py -q`
  -> `15 passed, 1 warning`.

### GHISS3 Embedding Index

Goal:
- Index issues for semantic duplicate search.

Expected files:

- `src/github_issue_index.py`
- `tests/test_github_issue_index.py`

Done when:

- Issue text is normalized and bounded.
- Owner/repo filters are applied during query.
- Reindexing is idempotent.

Status: done.

Evidence:

- `src/github_issue_index.py` defines issue index documents, matches, a backend
  protocol, a deterministic in-memory backend and reindex/query helpers.
- `build_issue_index_document()` normalizes issue title/body/state/labels into
  bounded text and owner/repo/provider metadata without provider calls.
- `reindex_github_issues()` reindexes only the requested owner/repository and
  uses backend upsert semantics so repeated runs are idempotent.
- `query_github_issue_index()` applies owner/repository and closed-issue filters
  at query time.
- `tests/test_github_issue_index.py` verifies text normalization/bounding,
  owner/repo scoping, closed filtering and idempotent reindexing.
- Verification 2026-07-03:
  `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_github_issue_fields.py tests\test_github_issue_models.py tests\test_github_issue_sync.py tests\test_github_issue_index.py -q`
  -> `19 passed, 1 warning`.

### GHISS4 Duplicate Candidate Service

Goal:
- Return top duplicate candidates for a draft.

Expected files:

- `src/github_issue_duplicates.py`
- `tests/test_github_issue_duplicates.py`

Done when:

- Top 3 candidates include score and reason.
- Closed and open issues are ranked sensibly.
- High-confidence candidates block auto-create unless confirmed.

Status: done.

Evidence:

- `src/github_issue_duplicates.py` defines a draft duplicate service that reads
  from the repo-local issue index and returns compact candidate previews. It
  does not create, close, label or update GitHub issues.
- Candidate previews include external issue id, integer score, reason, state,
  labels, URL and `blocks_auto_create`.
- Similar open issues are ranked ahead of equivalent closed issues, while closed
  issues remain visible when requested.
- High-confidence candidates block auto-create until a future confirmed write
  tool explicitly proceeds.
- Existing source issues can persist pending duplicate evidence in
  `GitHubIssueDuplicateCandidate` without overwriting accepted/rejected
  decisions.
- `src/github_issue_index.py` and `tests/test_github_issue_index.py` were also
  normalized to ASCII truncation suffixes (`...`).
- Verification 2026-07-03:
  `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_github_issue_fields.py tests\test_github_issue_models.py tests\test_github_issue_sync.py tests\test_github_issue_index.py tests\test_github_issue_duplicates.py -q`
  -> `24 passed, 1 warning`.

### GHISS5 Tools

Goal:
- Add agent tools for sync, duplicate search, create-triaged, set-fields.

Expected files:

- `src/tool_schemas.py`
- `src/tool_implementations.py`
- `src/tool_index.py`
- `src/tool_security.py`
- `tests/test_github_issue_tools.py`
- `tests/test_tool_index_schema_parity.py`

Done when:

- Tool discovery routes "duplicate issue", "github issue fields", and
  "triaged issue" requests correctly.
- Write tools require explicit confirmation.
- MCP policy classifies write tools as gated.

Status: done.

Evidence:

- `src/tool_domains/github_issues.py` implements the conservative
  `manage_github_issues` backend surface.
- `duplicate_search` runs local/read-only duplicate preview against already
  synced `GitHubIssueRecord` rows.
- `sync` returns a bounded live-read gate and never accepts provider tokens in
  chat.
- `create_triaged` and `set_fields` require confirmation and then return
  explicit live/auth gates instead of writing to GitHub in this repo-only slice.
- Tool wiring is registered in `src/agent_tools/__init__.py`,
  `src/tool_schema_definitions.py`, `src/tool_schemas.py`,
  `src/tool_execution.py`, `src/tool_implementations.py`, `src/tool_index.py`,
  `src/tool_security.py`, `src/mcp_server_tool_policy.py`,
  `src/agent_loop_intent.py`, `src/agent_loop_system_prompt.py`,
  `src/agent_loop_prompts.py` and `src/chat_agent_tool_discovery_map.py`.
- `tests/test_github_issue_tools.py` verifies schema/index/security/MCP wiring,
  function-call conversion, dispatcher execution, local duplicate search,
  confirmation/live gates and high-confidence duplicate blocking.
- Verification 2026-07-03:
  `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_github_issue_fields.py tests\test_github_issue_models.py tests\test_github_issue_sync.py tests\test_github_issue_index.py tests\test_github_issue_duplicates.py tests\test_github_issue_tools.py tests\test_tool_index_schema_parity.py tests\test_mcp_server_tool_policy.py -q`
  -> `38 passed, 1 warning`.

### GHISS6 Route Contracts

Goal:
- Add compact backend route contracts for issue sync readiness, local duplicate
  preview and write-plan gates. UI placement remains a separate UI-agent task.

Expected files:

- `routes/github_issue_routes.py`
- route registration in app setup
- focused route tests

Done when:

- API caller can request sync readiness without live provider action.
- API caller can draft an issue and see top 3 duplicate candidates.
- API caller can request internal field/write plans without provider writes.
- UI placement, text fit and visual behavior are tracked outside backend ABC.

Status: done.

Evidence:

- `routes/github_issue_routes.py` defines backend-only route contracts under
  `/api/github-issues`.
- `GET /api/github-issues/readiness` reports owner/repo-local issue counts plus
  sync/write live gates without provider calls.
- `POST /api/github-issues/duplicates` returns local duplicate previews from
  already synced issue records.
- `POST /api/github-issues/write-plan` returns confirmation/live-gated
  `set_fields` and `create_triaged` plans without provider writes and blocks
  high-confidence duplicate creation until explicitly acknowledged.
- `app.py` registers the router; no `static/`, legacy UI or V2 UI files are part
  of this backend slice.
- `tests/test_github_issue_routes.py` verifies readiness counts/gates, duplicate
  previews, field write plans and duplicate blocking.
- Verification 2026-07-03:
  `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_github_issue_fields.py tests\test_github_issue_models.py tests\test_github_issue_sync.py tests\test_github_issue_index.py tests\test_github_issue_duplicates.py tests\test_github_issue_tools.py tests\test_github_issue_routes.py tests\test_tool_index_schema_parity.py tests\test_mcp_server_tool_policy.py -q`
  -> `42 passed, 1 warning`.

### GHISS7 GitHub Issue Fields Projection

Goal:
- Write internal fields to GitHub Issue Fields when available.

Expected files:

- `src/github_issue_projection.py`
- `tests/test_github_issue_projection.py`

Done when:

- GitHub field IDs are cached per repo/org.
- Missing field support falls back cleanly.
- Per-field write result is visible and redacted.

Status: done.

Evidence:

- `src/github_issue_projection.py` defines a token-free, injected-client
  projection adapter for GitHub Issue Fields.
- Field IDs are cached by owner/repository through
  `InMemoryGitHubIssueFieldCache`; callers can force refresh without persisting
  provider secrets.
- `prepare_github_issue_projection()` maps canonical Odysseus fields to GitHub
  Issue Fields when IDs are available and falls back to labels or `local_only`
  plans when support is missing.
- `apply_github_issue_projection()` supports dry-run previews by default and
  explicit injected-client applies, with per-field status and redacted errors.
- `tests/test_github_issue_projection.py` verifies cache scoping, field writes,
  label fallback, local-only skips and redaction of token-like upstream errors.
- Verification 2026-07-03:
  `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_github_issue_projection.py -q`
  -> `5 passed, 1 warning`.

### GHISS8 MCP Exposure

Goal:
- Expose stable read tools and gated write tools through Odysseus MCP Server.

Expected files:

- `src/mcp_server_tool_policy.py`
- `plugins/mcp_server/plugin.py` if needed
- `tests/test_mcp_server_tool_policy.py`

Done when:

- Read-only duplicate lookup appears when policy allows.
- Write tools remain absent unless owner-scoped writes are enabled.
- Generic raw GitHub passthrough remains absent.

Status: done.

Evidence:

- `src/mcp_server_tool_policy.py` exposes only the narrow
  `github_issue_find_duplicates` read-only MCP tool for GitHub Issue
  Intelligence; the mixed `manage_github_issues` tool remains high-risk hidden.
- `plugins/mcp_server/plugin.py` adds a synthetic MCP-only
  `github_issue_find_duplicates` schema and routes calls internally to
  `duplicate_search`, without provider sync, issue creation, field writes or
  token handling.
- Raw/generic GitHub passthrough remains absent; generic Odysseus API remains
  hidden unless separately enabled by MCP policy.
- `tests/test_mcp_server_tool_policy.py` verifies the read-only GitHub issue
  MCP policy and hidden write/raw surfaces.
- `tests/test_mcp_server_plugin.py` verifies the read-only tool appears in
  `tools/list`, write/mixed tools are absent and `tools/call` routes only to
  the safe duplicate-search action.
- Verification 2026-07-03:
  `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_mcp_server_tool_policy.py tests\test_mcp_server_plugin.py tests\test_github_issue_fields.py tests\test_github_issue_models.py tests\test_github_issue_sync.py tests\test_github_issue_index.py tests\test_github_issue_duplicates.py tests\test_github_issue_tools.py tests\test_github_issue_routes.py tests\test_github_issue_projection.py -q`
  -> `57 passed, 1 warning`.

## Verification Bundle

Initial focused suite:

```text
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_github_issue_fields.py tests\test_github_issue_sync.py tests\test_github_issue_index.py tests\test_github_issue_duplicates.py tests\test_github_issue_tools.py
```

Broader safety suite:

```text
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_tool_index_schema_parity.py tests\test_mcp_server_tool_policy.py tests\test_null_owner_gates.py
```

## Rollout

1. Offline contract and fake GitHub client tests.
2. Read-only sync for one configured repo.
3. Duplicate preview only.
4. Confirmed issue creation with fallback labels.
5. GitHub Issue Fields projection.
6. MCP exposure after policy tests.
7. Optional scheduled sync.

## Non-Goals

- No auto-close or auto-mark-duplicate in v1.
- No bulk label migration without a separate preview and explicit operator Go.
- No raw GitHub MCP passthrough in Odysseus MCP Server.
- No cross-owner or cross-private-repo duplicate search.
- No production token setup documented with real token values.
