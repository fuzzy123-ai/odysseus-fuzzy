# Agent Context Transparency Contract

Status: ACT-1A repository contract, schema version 1
Scope: backend-facing transparency payloads; no UI, live provider or policy mutation
Sources: [Agent Context Transparency Roadmap](agent-context-transparency-roadmap.json), [AI Lens Master Roadmap](ai-lens-master-roadmap.json), [AI Lens Technical Roadmap](ai-lens-technical-roadmap.json), [Data Classification Policy](data-classification-policy-contract.md) and [Secure Data Mode Contract](secure-data-mode-contract.md)

## Purpose

This contract defines four bounded payloads shared by Agent Context Transparency and AI Lens:

- `ContextItem`: one source, rule or evidence item considered for an answer;
- `AnswerPackSummary`: a readable inventory of included, clipped and excluded context;
- `MemoryInfluenceRecord`: evidence that a memory or project fact entered retrieval, context or answer support;
- `UserContextFeedback`: a user correction that may produce a learnable rule candidate.

Transparency explains observable selection and influence. It does not expose hidden reasoning, full prompts, provider internals or a fictional neural state. The default product copy is plain language such as `Using now`, `Why this`, `Learned` and `Needs review`.

## Shared envelope and normalization

Every payload has these required fields:

| Field | Type | Rule |
| --- | --- | --- |
| `schema_version` | integer | Exactly `1`; unknown versions fail closed. |
| `kind` | enum string | One of the four payload kinds defined below. |
| primary ID | string | Stable opaque ID for the record; never derived from display text. |
| `created_at` | RFC 3339 string | UTC timestamp. |
| `truth_level` | enum string | Truth semantics defined below. |
| `classification` | enum string | Effective data classification after propagation. |
| `redaction_state` | enum string | Public rendering state. |
| `review` | object | Deterministic review decision and reason codes. |

Normalization rules:

- IDs are lower-case opaque ASCII strings matching `[a-z0-9][a-z0-9_-]{0,127}`.
- IDs are immutable and never silently reused. A selection instance has a new `context_id`; its stable source keeps the same `SourceRef`.
- Enums are lower-case exact values. Unknown aliases and invalid capitalization are rejected, not guessed.
- User-visible strings are Unicode NFC, trimmed and free of NUL/control characters except ordinary line breaks where explicitly allowed.
- Timestamps are UTC RFC 3339. Percentages and confidence scores are finite numbers in the closed interval `0..1`.
- Missing information is represented by a documented `unknown` state or `null`, never an invented value.
- Unknown fields may be preserved internally for forward compatibility but are not forwarded to UI, MCP or AI Lens without an explicit allowlist.

## Shared enums and value objects

### Truth level

The shared `truth_level` values are:

- `runtime_trace`: directly observed selection, exclusion, context assembly, memory reference or feedback event;
- `semantic_projection`: a derived relevance, similarity or graph interpretation based on real source evidence;
- `local_model_internals`: sampled local-model internals behind a separate live/privacy gate;
- `visual_effect`: rendering-only state that must never be treated as evidence.

ACT payloads normally use `runtime_trace`. A retrieval reason may use `semantic_projection` when its basis is explicit. `local_model_internals` and `visual_effect` are not valid evidence for selecting a Context Item and cannot support a Memory Influence Record.

### Data classification

`classification` is one of:

- `public`
- `private`
- `sensitive`
- `secret`
- `unknown`

For mixed sources, the strictest classification wins. Derived summaries and feedback may not silently downgrade their source. `unknown` is allowed for a local administrative listing, but any policy-relevant context use, provider path or writeback must block or request review.

### Redaction state

`redaction_state` is one of:

- `none`: the allowlisted public fields are safe to display;
- `summary_only`: only a bounded safe summary may be displayed;
- `metadata_only`: only type, state and opaque refs may be displayed;
- `fully_redacted`: presence may be disclosed, content may not;
- `blocked`: the item was not loaded or used because policy denied access.

`blocked` must not carry a preview. `fully_redacted` must not carry source body, label derived from secret content or a revealing path.

### SourceRef

A `SourceRef` is an object with:

| Field | Required | Rule |
| --- | --- | --- |
| `ref_type` | yes | `system_rule`, `user_turn`, `document`, `repo_file`, `project`, `roadmap`, `gate`, `todo`, `memory`, `rag_chunk`, `tool_result`, `import_summary` or `other`. |
| `ref_id` | yes | Stable opaque ID. |
| `section_ref` | no | Opaque section or fragment ID, maximum 128 characters. |
| `repo_rel_path` | no | Normalized allowlisted repository-relative path only. |

A SourceRef never contains an absolute path, URI credentials, raw query string, chat transport ID or source body. Path normalization rejects `..`, drive-qualified paths, UNC paths, NUL bytes and symlink/junction escape from the configured repository root.

### Plain-language reason

`why_selected` is an object with:

- `code`: `user_pinned`, `explicit_mention`, `active_project`, `active_roadmap`, `recently_updated`, `semantic_match`, `memory_preference`, `tool_evidence`, `system_requirement`, `conversation_continuity` or `other`;
- `summary`: a plain-language sentence, maximum 240 characters;
- `evidence_refs`: up to 8 stable event or SourceRefs;
- `truth_level`: `runtime_trace` or `semantic_projection`.

The summary explains the observed reason, for example `Pinned by you` or `Matches the active roadmap`. It must not expose embeddings, hidden chain-of-thought, raw ranking traces or unsupported certainty.

### Freshness

`freshness` is an object with:

- `state`: `current`, `recent`, `stale`, `expired` or `unknown`;
- `observed_at`: nullable UTC timestamp;
- `source_updated_at`: nullable UTC timestamp;
- `age_seconds`: nullable non-negative integer;
- `reason`: nullable plain-language text, maximum 160 characters.

Thresholds are source-policy inputs, not hard-coded by UI. If the source time or policy is unavailable, the state is `unknown`.

### Confidence

`confidence` is an object with:

- `level`: `high`, `medium`, `low` or `unknown`;
- `score`: nullable number from `0` to `1`;
- `basis`: `direct`, `rule`, `retrieval_score`, `multiple_sources`, `user_confirmed` or `unknown`;
- `summary`: optional plain-language text, maximum 160 characters.

Confidence describes evidence quality for selection or influence, not the model's private subjective certainty. A score without a documented basis is invalid.

### Review decision

`review` contains:

- `required`: boolean;
- `reason_codes`: a deduplicated array containing only `uncertainty`, `conflict`, `policy_risk` or `user_visible_writeback`;
- `summary`: optional plain-language text, maximum 240 characters.

`required=true` requires at least one reason code. `required=false` requires an empty reason list. Routine successful selection, reads, feedback capture and derived summaries remain quiet.

## Payload budgets

Budgets apply after UTF-8 JSON serialization:

| Payload/value | Maximum |
| --- | ---: |
| `ContextItem` | 8 KiB |
| `AnswerPackSummary` | 128 KiB |
| `MemoryInfluenceRecord` | 32 KiB |
| `UserContextFeedback` | 8 KiB |
| ID or typed ref ID | 128 characters |
| Label | 120 characters |
| General summary | 500 characters |
| Redacted preview | 300 characters |
| SourceRefs per record | 64 |
| Context items per answer pack | 64 total |
| Evidence refs per reason/influence | 32 |

Arrays reject overflow instead of truncating silently. A producer may return aggregate counts plus `truncated=true`, but must not claim the payload is complete.

## ContextItem schema

`kind` is `agent.context_item` and the primary ID is `context_id`.

Required fields:

- `context_id`
- `context_kind`: `system_rule`, `user_message`, `pinned_document`, `project`, `repo`, `roadmap`, `memory`, `rag`, `tool_evidence`, `import_summary` or `other`
- `label`
- `source_ref`: one SourceRef
- `selection_state`: `candidate`, `included`, `excluded`, `clipped` or `blocked`
- `scope`: `turn`, `conversation`, `project`, `workspace` or `global`
- `why_selected`
- `freshness`
- `confidence`
- `pinned`: boolean
- `removable`: boolean
- shared envelope fields

Optional fields:

- `summary`
- `redacted_preview`
- `exclusion_reason`: required when state is `excluded`, `clipped` or `blocked`
- `token_estimate`: nullable non-negative integer
- `source_revision_ref`: stable opaque revision/hash reference
- `parent_context_id`: for a bounded derived item

Constraints:

- `included` means the item entered the answer pack; it does not claim the model causally used every token.
- `blocked` means content was not loaded. The payload may explain the policy category without leaking a title, preview or path.
- `redacted_preview` is forbidden for `fully_redacted` and `blocked` states.
- `pinned=true` does not bypass classification, Secure Mode or provider policy.
- `removable=false` is reserved for required system/policy context and needs a plain-language reason.

## AnswerPackSummary schema

`kind` is `agent.answer_pack_summary` and the primary ID is `pack_id`.

Required fields:

- `pack_id`
- `conversation_ref`: opaque internal conversation ID; never a Telegram/provider chat ID
- `turn_ref`: opaque turn ID
- `phase`: `pre_generation` or `post_generation`
- `model_route`: object with `model_ref`, `locality` (`local` or `api`) and immutable `security_mode` (`normal` or `secure`)
- `token_budget`: object with nullable non-negative `total`, `used`, `remaining` and `unit="tokens"`
- `context_used_ratio`: nullable number from `0` to `1`
- `items`: up to 64 validated ContextItems or stable `context_id` refs
- `included_count`, `excluded_count`, `clipped_count`, `stale_count`, `sensitive_count`
- `excluded_items`: bounded records with `context_id`, reason code and safe reason summary
- `complete`: boolean
- shared envelope fields; `truth_level` must be `runtime_trace`

Optional fields:

- `response_ref` for a post-generation pack
- `missing_expected_source_types`
- `conflict_count`
- `truncated`: boolean

Counts describe the full known pack, while arrays describe the bounded returned page. `complete=false` or `truncated=true` must be explicit when they differ. In Secure Mode, model locality must be `local`; any API route is invalid and blocked.

## MemoryInfluenceRecord schema

`kind` is `agent.memory_influence_record` and the primary ID is `influence_id`.

Required fields:

- `influence_id`
- `response_ref`
- `pack_id`
- `context_ids`
- `memory_refs`: typed SourceRefs with `ref_type=memory`
- `project_refs`: bounded project SourceRefs, possibly empty
- `source_refs`: supporting SourceRefs
- `influence_type`: `retrieved`, `selected_for_context`, `cited_support`, `conflict`, `excluded` or `writeback_candidate`
- `reason_summary`
- `confidence`
- `evidence_event_refs`
- shared envelope fields

Optional fields:

- `answer_segment_refs`: opaque segment IDs, never full answer text
- `rank`: positive integer
- `relevance_score`: normalized `0..1` score with documented basis
- `freshness`

Influence means observable retrieval, selection, citation support, exclusion or conflict. It does not claim access to hidden model causality. The record contains refs, safe summaries and scores only; raw memory bodies are forbidden.

## UserContextFeedback schema

`kind` is `agent.user_context_feedback` and the primary ID is `feedback_id`.

Required fields:

- `feedback_id`
- `context_id`
- `target_ref`: ContextItem or SourceRef
- `action`: `pin`, `remove`, `approve`, `hide` or `rename`
- `scope`: `turn`, `conversation`, `project`, `workspace` or `global`
- `actor_ref`: opaque local actor/role reference
- `result`: `recorded`, `candidate_created`, `review_required` or `rejected`
- `policy_effect`: must be `none` in this contract
- shared envelope fields; `truth_level` must be `runtime_trace`

Optional fields:

- `reason`: maximum 500 characters after redaction
- `proposed_label`: required only for `rename`, maximum 120 characters
- `learned_rule_candidate`

Action semantics:

- `pin`: request stronger future consideration in the chosen scope; never bypass policy.
- `remove`: exclude from the current pack or scope; does not delete the source.
- `approve`: confirm a presented selection, correction or writeback proposal; it does not auto-approve unrelated future actions.
- `hide`: propose suppressing the source from default transparency/retrieval in scope; the source remains auditable.
- `rename`: propose a display-label correction; it does not rename or mutate the underlying source.

### Learnable rule candidate

Feedback may create, but never automatically apply, a `learned_rule_candidate` with:

- `candidate_id`
- `status`: always `proposed` at creation
- `candidate_type`: `prefer`, `exclude`, `confirm`, `hide` or `display_label`
- `scope`
- `target_ref`
- `summary`: bounded plain-language rule
- `evidence_feedback_refs`
- `requires_review`: boolean

The candidate cannot contain executable code, arbitrary prompt text, credentials, raw private content or a classification downgrade. Applying it to durable context policy is a separate validated operation. Global rules, classification changes, conflicts and user-visible writebacks require review.

## Review classification

Create a review item only when at least one condition holds:

1. `uncertainty`: confidence/freshness/classification is unknown or below the responsible policy threshold;
2. `conflict`: sources or user feedback disagree in a way that changes context selection;
3. `policy_risk`: classification, Secure Mode, provider/tool boundary or requested scope may be unsafe;
4. `user_visible_writeback`: an operation would persistently change memory, project, roadmap or durable context policy.

Do not create review items for routine reads, successful selection, ordinary answer-pack inspection, redacted telemetry or recording feedback without applying it. A blocked sensitive source may create one policy-risk review record, but the record must not reveal the source body.

## Privacy and Secure Mode rules

- Forbidden everywhere: secrets, credentials, tokens, cookies, authorization headers, raw private bodies, full prompts, raw provider output, private absolute paths and external chat IDs.
- A normal conversation may use `public` and policy-allowed `private` context. It blocks `sensitive` and `secret` before loading or previewing them.
- A Secure conversation is immutable, local-only and rejects API models, external embeddings/providers and unsafe tools.
- `unknown` or invalid classification never degrades to `public` or `private`; policy-relevant use blocks or requests review.
- Derived payloads inherit the strictest source classification unless an explicit reviewed policy allows otherwise.
- Redaction occurs before serialization, persistence, logging, AI Lens emission or error formatting.
- Validation errors identify field and rule categories, not rejected values.
- Feedback cannot downgrade classification, reveal hidden content or convert a blocked source into usable context.

## AI Lens compatibility mapping

ACT is the canonical context explanation model. AI Lens consumes ACT IDs and bounded payload projections instead of defining a second Context Item schema.

| ACT state/payload | AI Lens event or information product |
| --- | --- |
| ContextItem becomes `included` | `context_item_selected` |
| ContextItem becomes `excluded`, `clipped` or `blocked` | `context_item_excluded` |
| AnswerPackSummary created | `context_pack_composed` |
| Answer-pack token counts change | `context_budget_updated` |
| MemoryInfluenceRecord for retrieval | `memory_hit`, `rag_hit` or `retrieval_ranking_summary`, according to source type |
| Influence conflict | `source_conflict_detected` |
| Bounded provenance summary | `answer_provenance_summary` |
| Policy block/review | `safety_gate_triggered` |

Mapping rules:

- `context_id`, `pack_id`, `influence_id`, SourceRefs and supporting event IDs remain stable across ACT and AI Lens.
- AI Lens event payloads carry bounded projections or refs, not duplicated raw context bodies.
- ACT `truth_level`, classification and redaction state propagate to emitted events; AI Lens may only make them stricter.
- Runtime selection is `runtime_trace`; similarity layouts are `semantic_projection`; decorative UI state is `visual_effect` and never evidence.
- Feedback without a matching AI Lens event remains an ACT audit record. It must not be misreported as an unrelated event merely to appear in the Lens.

## Backend validation invariants

1. Every record validates its schema version, kind, ID, enums, size budget and cross-field constraints.
2. Stable refs are typed, bounded and path-safe; display labels are never used as identity.
3. Included/excluded counts and item states agree or the pack is marked incomplete.
4. No sensitive/secret content enters a normal conversation payload, preview, log or Lens event.
5. Secure Mode never permits a non-local model route.
6. Redaction and classification propagate monotonically toward stricter protection.
7. Influence claims cite observable event/source evidence and never imply hidden model causality.
8. Feedback creates at most a proposed rule candidate and has no automatic durable policy effect.
9. Review is quiet by default and limited to uncertainty, conflict, policy risk or user-visible writeback.
10. Invalid and unknown policy values fail closed.

## ACT-1A done and verification

This docs-first slice is complete when:

- all four payload schemas define required/optional fields, enums, refs and budgets;
- why-selected, freshness, confidence and truth semantics are plain-language and evidence-bound;
- classification, Secure Mode and redaction behavior fail closed;
- all five feedback actions can produce a proposed learnable rule without automatic policy mutation;
- review classification is limited to the four approved reasons;
- AI Lens mappings reuse ACT identities and payloads;
- all source links resolve locally and no private path, secret or real external identifier appears in this file.

## ACT-1B backend handoff

Implement the four schemas in one focused validation module and tests, without UI wiring. The first tests should cover:

- valid minimal and maximal payloads plus JSON round trips;
- invalid enums, IDs, timestamps, scores, counts and oversize strings/arrays;
- SourceRef traversal, absolute/UNC path and ID normalization rejection;
- strict classification propagation and unknown/invalid fail-closed behavior;
- normal-versus-Secure model/source policy;
- blocked/fully-redacted preview rejection and forbidden-field scanning;
- answer-pack count/completeness consistency;
- influence evidence requirements and hidden-causality wording boundaries;
- all five feedback actions, proposed-only learned rules and rename constraints;
- review classification truth table;
- deterministic AI Lens event projections preserving stable refs and redaction.
