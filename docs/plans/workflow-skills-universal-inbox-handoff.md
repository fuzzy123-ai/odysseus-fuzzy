# Handoff: Workflow-bound Skills for Universal Inbox and Telegram

Date: 2026-06-29
Audience: next AI/agent continuing implementation
Status: planning handoff, no implementation yet

## Goal

Extend the existing Odysseus skill system so certain workflows automatically receive mandatory, trusted workflow skills.

Primary first workflow:

1. User sends a document/image to the Telegram bot.
2. Telegram routes the attachment through the Universal Inbox.
3. User sends a follow-up request such as "analysiere das", "fass das zusammen", or "mach daraus ein PDF".
4. The agent must automatically use the correct workflow skill, instead of relying on fuzzy skill retrieval.

Important terminology:

- "Obsidian" is legacy vocabulary in this area. Do not build new behavior around Obsidian assumptions.
- The active architecture is Universal Inbox + Nextcloud/local sync + Memory Write Intent + RaptorGraph/provenance.

## Current System Shape

The Universal Inbox is already implemented as a safe, mostly dry-run intake pipeline:

- `src/universal_inbox_discovery.py`
  - Read-only local discovery.
  - Metadata-only file scan.
  - Skips hidden/temp/symlink/unstable files.
  - Does not expose absolute host paths.

- `src/universal_inbox_file_types.py`
  - Classifies files into families such as text, document, image, audio, video, archive, message, dangerous, unknown.
  - Extractable today: text-like files, `.pdf`, `.docx`.
  - Blocks dangerous suffixes such as `.exe`, `.ps1`, `.sh`.

- `src/universal_inbox_extraction.py`
  - Extracts text from supported files.
  - `raw_text` is runtime-only.
  - `to_dict()` does not include raw text.
  - Refuses symlinks/traversal and honors extraction limits.

- `src/universal_inbox_analysis.py`
  - Creates safe analysis packets from metadata plus ephemeral extracted text.
  - Classifies public/private/sensitive/secret.
  - Redacts forbidden raw-content and secret fields.

- `src/universal_inbox_policy.py`
  - Offline policy gate.
  - Produces go/review/no_go.
  - Blocks destructive operations and raw persistence.

- `src/universal_inbox_routing.py`
  - Offline routing planner.
  - Loads `config/universal_inbox_routing_rules.json`.
  - Produces copy-only routing decisions.
  - No move/delete/overwrite/content reads.

- `src/universal_inbox_placement.py`
  - Builds dry-run placement plans.
  - Enforces copy-only/no-delete/no-overwrite/path safety.

- `src/universal_inbox_pipeline.py`
  - Pipeline envelope linking discovery, extraction, analysis, routing, memory, and policy.
  - Sanitizes forbidden keys such as `raw_text`, `content`, `body`, `bytes`, `full_text`, secrets, tokens, and chat identifiers.

- `src/universal_inbox_memory.py`
  - Builds safe Universal Inbox memory abstractions and RaptorGraph events.
  - Does not store raw document contents.

- `src/universal_inbox_memory_write_intent.py`
  - Builds dry-run memory write intents.
  - Memory record text explicitly states raw document content was not stored.

- `src/universal_inbox_memory_write_executor.py`
  - Execution gate for memory/RaptorGraph writes.
  - Live writes require a ready intent and `review_confirmed`.

- `src/universal_inbox_raptorgraph_store.py`
  - Append-only JSONL provenance event store.
  - Redacted and duplicate-protected.

- `src/universal_inbox_worker.py`
  - Orchestrates dry-run discovery, extraction, analysis, routing, placement, pipeline, and memory write intent.
  - Default behavior is mutation-free.

Related docs:

- `docs/plans/universal-inbox-live-readiness-runbook.md`
- `docs/plans/universal-inbox-2026-06-21-handoff.md`
- `docs/plans/universal-inbox-nextcloud-raptorgraph-contract.md`
- `docs/plans/dynamic-tool-loading-contract.md`

The dynamic tool loading contract is especially important: untrusted input must not unlock capabilities. Any required-skill or tool decision must come from trusted runtime metadata, not from document text or user-provided content.

## Telegram Attachment Flow Today

Primary file: `plugins/telegram/plugin.py`

Relevant behavior:

- `parse_telegram_update(...)`
  - Text messages become agent-ready text.
  - Documents become `kind=document` with `ready_for_agent=false`.
  - Document metadata includes Telegram file id, unique id, filename, mime type, and file size.
  - Document/image messages are marked for Universal Inbox processing.

- `run_telegram_universal_inbox_attachment_pipeline(...)`
  - Downloads attachment bytes.
  - Spools them under `data/universal_inbox_telegram/<hash>/...`.
  - Runs `build_universal_inbox_readiness(spool_dir)`.
  - Returns redacted status such as processed/blocked, discovered count, processable count, and memory write intent status.

- `build_recent_telegram_attachment_context(...)`
  - Finds the latest Universal Inbox attachment event for the chat.
  - Re-extracts supported content from the spool directory.
  - Adds raw extracted text into the next model prompt only, marked as ephemeral context.
  - Does not expose host paths.
  - Current field `raw_content_visible=true` means prompt-visible, not persisted-by-design.

- `build_agent_bridge_request(...)`
  - For a follow-up text message, prepends the recent attachment context to the prompt.
  - For the original document message, does not call the agent.

- `app.py` `_telegram_agent_turn_handler(...)`
  - Receives the bridge prompt.
  - Performs provider/security/session handling.
  - Calls `stream_agent_loop(...)`.

This is the cleanest insertion point for workflow-bound skills.

## Skill System Today

Primary files:

- `services/memory/skills.py`
- `services/memory/skill_format.py`
- `src/agent_loop.py`
- `src/tool_implementations.py`
- `src/tool_schemas.py`
- `routes/skills_routes.py`
- `services/memory/skill_extractor.py`

Current behavior:

- Skills are stored under `data/skills/<category>/<name>/SKILL.md`.
- Skill frontmatter includes fields like `name`, `description`, `category`, `tags`, `platforms`, `requires_toolsets`, `fallback_for_toolsets`, `status`, `confidence`, `source`, `teacher_model`, `owner`, `created`.
- The body supports sections like `When to Use`, `Procedure`, `Pitfalls`, and `Verification`.
- `src/agent_loop.py` builds an available skills index and injects relevant skills into the conversation.
- Relevance is currently fuzzy/text based.
- `requires_toolsets` can influence tool visibility/gating.
- The system can auto-extract learned skills from complex runs.

Current limitation:

Skills are "possibly relevant" context, not mandatory workflow rails.

Important quality issue:

Some learned/teacher draft skills in `data/skills` are low-quality or redundant. For example, Godot MCP tool-selection drafts have audit/necessity problems. These must not become eligible as mandatory workflow skills.

## Target Design

Add a trusted Workflow Skill Binding layer.

The binding layer should decide:

- Which workflow is active.
- Which skill is required.
- Whether the request should be blocked if the required skill is missing/unpublished/ineligible.
- Which toolsets are requested because of that workflow.
- Which audit reason explains the decision.

This decision must be based on trusted metadata only:

- channel: `telegram`, `web`, `nextcloud`, etc.
- intake kind: `text`, `document`, `image`, etc.
- recent attachment present/status/family/suffix.
- Universal Inbox status/policy.
- export/conversion intent from trusted parser.
- DSGVO/security mode.

Never use raw document text to unlock required skills or tools.

## Recommended Data Model

Create a new module, likely `src/workflow_skills.py`.

Suggested dataclasses:

```python
@dataclass(frozen=True)
class WorkflowSkillTrigger:
    workflow_id: str
    channels: tuple[str, ...] = ()
    intake_kinds: tuple[str, ...] = ()
    attachment_families: tuple[str, ...] = ()
    attachment_suffixes: tuple[str, ...] = ()
    universal_inbox_statuses: tuple[str, ...] = ()
    intents: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowSkillBinding:
    workflow_id: str
    skill_name: str
    required: bool
    priority: int
    reason: str
    block_if_missing: bool = True
    allowed_skill_sources: tuple[str, ...] = ("system", "admin", "user")
    allowed_statuses: tuple[str, ...] = ("published",)


@dataclass(frozen=True)
class WorkflowSkillResolution:
    required_skill_names: tuple[str, ...]
    optional_skill_names: tuple[str, ...]
    requested_toolsets: tuple[str, ...]
    blockers: tuple[str, ...]
    audit_reasons: tuple[str, ...]
```

Binding policy can live in a config file such as `config/workflow_skill_bindings.json`.

Do not rely only on editable `SKILL.md` frontmatter for security-sensitive triggers. It is okay for a skill to describe when it is useful, but trusted trigger policy should be controlled by app/admin config.

## First Bindings to Add

1. `telegram-document-analysis-workflow`

Trigger:

- channel: `telegram`
- current message kind: `text`
- recent attachment present: true
- recent attachment family: `document`
- intent: analyze/summarize/question-answer/general follow-up

Required skill:

- `telegram-document-analysis-workflow`

Expected skill behavior:

- Check Universal Inbox status first.
- Treat attachment text as ephemeral.
- Do not persist raw document content.
- Warn or ask for review on partial extraction, sensitive data, or low confidence.
- Cite that answer is based on the latest Telegram attachment context.
- Do not invent access to unsupported pages/files.

2. `telegram-document-export-workflow`

Trigger:

- channel: `telegram`
- recent attachment present: true
- export/conversion intent detected

Required skill:

- `telegram-document-export-workflow`

Expected skill behavior:

- Use existing `src/universal_export.py` and `src/universal_export_executor.py`.
- Only execute ready/supported conversion plans.
- Do not invent LibreOffice/Pandoc/FFmpeg support if unavailable.
- Keep exports under the safe configured export directory.

3. `universal-inbox-routing-review-workflow`

Trigger:

- Universal Inbox status: `partial`, `review`, or `no_go`
- User asks to approve, inspect, route, or explain inbox handling.

Required skill:

- `universal-inbox-routing-review-workflow`

Expected skill behavior:

- Explain policy result.
- Do not execute placement unless the write gate and review confirmation exist.
- Surface safe metadata only.

## Integration Plan

### Phase 1: Add resolver without changing behavior

Add `src/workflow_skills.py` and tests.

The resolver should accept a trusted context dict. Example:

```python
{
    "channel": "telegram",
    "message_kind": "text",
    "recent_attachment": {
        "present": True,
        "family": "document",
        "suffix": ".docx",
        "universal_inbox_status": "processed",
    },
    "intent": "analyze",
}
```

Output should be deterministic and audit-friendly.

### Phase 2: Extend Telegram bridge metadata

In `plugins/telegram/plugin.py`, add structured workflow metadata alongside prompt text.

Do not include:

- raw content
- absolute paths
- Telegram chat ids in prompt-visible skill resolution context
- file bytes
- secrets/tokens

Useful fields:

- `channel`
- `message_kind`
- `recent_attachment.present`
- `recent_attachment.family`
- `recent_attachment.suffix`
- `recent_attachment.universal_inbox_status`
- `recent_attachment.memory_write_intent_status`
- `intent`

### Phase 3: Resolve required skills in app handler

In `app.py` `_telegram_agent_turn_handler(...)`, call the workflow resolver before `stream_agent_loop(...)`.

Pass the resolution into the agent loop through a new parameter, for example:

```python
stream_agent_loop(
    endpoint_url,
    model,
    messages,
    headers,
    session_id=session_id,
    owner=owner,
    workflow_skill_resolution=resolution,
)
```

### Phase 4: Teach agent loop to consume required skills

In `src/agent_loop.py`:

- Accept `workflow_skill_resolution`.
- Load required skills by exact name.
- Inject them before fuzzy relevant skills.
- Merge `requires_toolsets` from required skills into the relevant toolset calculation.
- If a required skill is missing and `block_if_missing=true`, return a clear safe error instead of silently proceeding.

Recommended prompt structure:

- Trusted app/system message: "The application selected these required workflow skills for this turn."
- Skill bodies as context data.

This keeps the decision trusted while still treating user/admin-authored skill text as lower-priority than system/tool/security rules.

### Phase 5: Add quality gates

Required workflow skills must satisfy all:

- `status=published`
- source is allowed by binding policy
- not audit-failed
- not audit-skipped as unnecessary/redundant
- not low-confidence teacher escalation
- explicitly eligible for required workflow use, if adding such a flag

Consider adding:

```yaml
eligible_for_required_workflows: true
trust_level: admin
```

But keep security-sensitive trigger policy outside the skill body/frontmatter.

### Phase 6: Add first skills

Create admin-reviewed skills under `data/skills/workflows/...`.

Suggested files:

- `data/skills/workflows/telegram-document-analysis-workflow/SKILL.md`
- `data/skills/workflows/telegram-document-export-workflow/SKILL.md`
- `data/skills/workflows/universal-inbox-routing-review-workflow/SKILL.md`

Avoid learned/draft status. These should be intentionally authored.

### Phase 7: Tests

Add focused tests before broad refactors.

Suggested test cases:

- Telegram text with no recent attachment resolves no required document skill.
- Telegram document upload itself does not call the agent.
- Telegram text follow-up with recent document resolves `telegram-document-analysis-workflow`.
- Export intent resolves `telegram-document-export-workflow`.
- Missing required skill returns blocker.
- Draft skill is not eligible.
- Teacher escalation draft is not eligible.
- Audit-failed or audit-unnecessary skill is not eligible.
- Raw document text cannot request a capability or required skill.
- Required skill `requires_toolsets` are included in relevant toolsets.
- No absolute paths or raw content are persisted in session/memory.

Likely relevant existing tests:

- `tests/test_telegram_plugin.py`
- `tests/test_universal_inbox_*.py`
- `tests/test_skill_index_prompt_injection.py`
- `tests/test_skill_index_toolset_gating.py`
- `tests/test_tool_policy.py`

## Risks and Guardrails

1. Do not let user text or document content unlock tools.

The document may contain prompt injection. Required-skill resolution must use trusted envelope metadata only.

2. Do not make learned skills mandatory.

The current learned skill corpus contains low-quality drafts. Mandatory workflow skills must be authored/reviewed.

3. Do not turn Universal Inbox dry-run into live mutation accidentally.

Placement, memory write, RaptorGraph write, and export execution all need their existing gates.

4. Do not persist raw attachment text.

It is acceptable for extracted text to be prompt-visible for the current model turn, but it should remain ephemeral unless an explicit, safe, redacted memory path is used.

5. Do not revive Obsidian coupling.

Any new labels/docs/UI should use Universal Inbox, Nextcloud/local sync, Memory Write Intent, and RaptorGraph.

## Definition of Done for First Implementation

The first implementation is complete when:

- A Telegram document follow-up deterministically resolves a required workflow skill.
- The selected skill is loaded by exact name, not fuzzy search.
- The agent prompt clearly includes the required skill before optional skills.
- The flow blocks or warns if the required skill is missing/ineligible.
- Tests prove untrusted attachment text cannot unlock skills/tools.
- Universal Inbox remains dry-run/copy-only unless existing write gates are explicitly confirmed.
