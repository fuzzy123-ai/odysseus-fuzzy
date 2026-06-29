# Workflow-Bound Skills For Universal Inbox Roadmap

Status: implemented

Source of truth:
- `docs/plans/workflow-skills-universal-inbox-handoff.md`

Important framing:
- This is not an Obsidian track. "Obsidian" is legacy vocabulary here.
- Active target architecture: Universal Inbox + Telegram/Nextcloud/local sync + Memory Write Intent + RaptorGraph/provenance.

Goal:
- Odysseus deterministically applies mandatory workflow skills for trusted workflows such as Telegram document follow-up analysis/export, without relying on fuzzy skill retrieval or untrusted document contents.

Mode:
- Standard ABC, backend/logik-first.

Non-goals:
- No new UI design.
- No live Telegram, Nextcloud, provider, deploy, placement, memory write, RaptorGraph write or write-smoke action without explicit Go.
- No raw documents, raw extracted text, Telegram chat ids, tokens, private content or absolute host paths in persisted artifacts.
- Do not use document/user text to unlock required skills or tools.

Current evidence:
- Universal Inbox extraction/analysis/policy/pipeline/memory intent already redacts raw content and stays dry-run by default.
- Telegram already builds recent attachment context for follow-up prompts.
- `app.py` forwards Telegram bridge prompts into `stream_agent_loop(...)`.
- `src/agent_loop.py` currently injects skills by fuzzy relevance and wraps skill content as untrusted context, but there is no mandatory workflow skill rail.
- `services/memory/skills.py` exposes skill metadata, status, audit verdict and necessity sidecar data.

Stop rules:
- Stop if required-skill routing would inspect raw document text or user-provided document contents.
- Stop if a required skill is missing, draft, low-confidence, audit-failed, audit-unnecessary, teacher-escalation, or otherwise ineligible; surface a blocker instead.
- Stop if implementation would persist raw attachment context, absolute host paths, Telegram chat ids, tokens, bytes or secrets.
- Stop if Universal Inbox would perform live placement, export execution, memory write or RaptorGraph write without the existing explicit gates.
- Do not touch unrelated dirty/untracked files.

Slice queue:

1. `WSU-1-resolver-contract`
   - Status: done
   - Class: repo_only
   - Owner: Bob
   - Allowed paths: `src/workflow_skills.py`, `tests/test_workflow_skills.py`
   - Goal: implement deterministic `WorkflowSkillTrigger`, `WorkflowSkillBinding`, `WorkflowSkillResolution` and a resolver that consumes trusted context only.
   - Requirements:
     - Inputs are structured trusted metadata: channel, message kind, recent attachment family/suffix/status, memory intent status, intent, DSGVO/security mode.
     - No prompt/document text fields are accepted as trigger material.
     - Missing/ineligible required skills produce blockers.
   - Tests:
     - Telegram document follow-up resolves analysis workflow.
     - Export intent resolves export workflow.
     - No recent attachment resolves no required document skill.
     - Raw text/content keys are rejected or ignored as trigger sources.

2. `WSU-2-quality-gates`
   - Status: done
   - Class: repo_only
   - Owner: Bob
   - Allowed paths: `src/workflow_skills.py`, `tests/test_workflow_skills.py`, optional `services/memory/skills.py`
   - Goal: define required-skill eligibility from skill metadata and audit sidecar.
   - Requirements:
     - `status=published`.
     - `source` is allowed by binding policy.
     - teacher-escalation drafts and legacy/draft skills are not eligible.
     - audit-failed and necessity-unnecessary/redundant skills are not eligible.
     - optional metadata such as `eligible_for_required_workflows` / `trust_level` can be supported, but trigger policy remains outside SKILL.md.

3. `WSU-3-telegram-bridge-metadata`
   - Status: done
   - Class: repo_only
   - Owner: Charlie
   - Allowed paths: `plugins/telegram/plugin.py`, `tests/test_telegram_plugin.py`
   - Goal: add sanitized `workflow_context` to Telegram bridge requests.
   - Requirements:
     - Include trusted fields only: `channel`, `message_kind`, recent attachment present/family/suffix/universal_inbox_status/memory_write_intent_status, intent, DSGVO/security mode.
     - Do not include raw extracted text, prompt text, spool path, absolute host path, file bytes or chat ids.

4. `WSU-4-agent-loop-consumption`
   - Status: done
   - Class: repo_only
   - Owner: Bob
   - Allowed paths: `app.py`, `src/agent_loop.py`, `src/workflow_skills.py`, relevant agent-loop tests
   - Goal: resolve workflow skills in `_telegram_agent_turn_handler(...)`, pass resolution to `stream_agent_loop(...)`, inject required skills before fuzzy skills, and merge required `requires_toolsets`.
   - Requirements:
     - Required skill decision is a trusted app/system decision.
     - Skill body remains untrusted context data, not system authority.
     - Missing required skill blocks or warns clearly according to binding policy.

5. `WSU-5-admin-reviewed-skills`
   - Status: done
   - Class: repo_only
   - Owner: Alice
   - Allowed paths: `data/skills/workflows/**/SKILL.md`, tests
   - Goal: author first admin-reviewed workflow skills.
   - Skills:
     - `telegram-document-analysis-workflow`
     - `telegram-document-export-workflow`
     - `universal-inbox-routing-review-workflow`
   - Requirements:
     - `status: published`.
     - explicit trusted workflow eligibility metadata if adopted.
     - no learned/draft/teacher-escalation mandatory skills.

6. `WSU-6-final-verification`
   - Class: repo_only
   - Owner: Charlie
   - Status: done
   - Goal: focused tests pass, scoped commit and push to `fuzzy/dev`, deploy only after explicit Go or if user has already asked to keep backend changes live.
   - Tests:
     - `tests/test_workflow_skills.py`
     - relevant Telegram bridge tests
     - relevant agent-loop skill injection/toolset tests

Gate queue:
- UI placement for showing required workflow skills: `needs_design`.
- Live Telegram/Nextcloud/write-smoke validation: `needs_live_go`.
- Memory/RaptorGraph write execution: existing Universal Inbox review/write gates remain required.

Definition of done:
- Telegram document follow-up deterministically resolves a required workflow skill by exact name.
- Required skill is loaded by exact name before optional fuzzy skills.
- Missing/ineligible required skills produce a clear blocker.
- Required skills can add their declared toolsets to available tools.
- Tests prove untrusted attachment text cannot unlock skills/tools.
- Universal Inbox remains dry-run/copy-only unless existing explicit write gates are confirmed.
