# Odysseus Clarification Request v2 Contract

Status: frozen contract for OAW-ASK-1
Schema: `odysseus.clarification_request.v2`
Created: 2026-07-12

## Goal

Odysseus must collect material missing intent before planning, coding, tool
execution or publish preparation. The model may propose questions, but the
server owns ids, state, answer correlation and plan-unlock decisions.

## State Flow

`intent_received -> context_inspection -> clarifying -> understanding_review -> ready_for_plan -> planning`

Terminal states are `paused`, `cancelled`, `blocked` and `expired`.
`planning` is unreachable while unresolved required questions exist.

Context inspection is read-only and must answer discoverable questions before
asking the user. A complete prompt may move directly to
`understanding_review` with zero questions.

## Request Envelope

Minimum request:

- `schema`: `odysseus.clarification_request.v2`
- `scope`: `conversation`, `project` or `coding_task`
- `intent_summary`: bounded current understanding
- `questions`: stable semantic question keys, types, required flags and reasons
- `batch`: visible section label, index, total and max visible questions
- `defaults_visible`: true when recommended defaults are shown to the user

The server assigns:

- `clarification_id`
- `session_id`
- `owner`
- `version`
- `created_at`
- `updated_at`

The model must not invent these ids.

## Question Types

Allowed `questions[].type` values:

- `single_select`
- `multi_select`
- `boolean`
- `short_text`
- `long_text`
- `number`
- `date`
- `resource_ref`

`resource_ref` can only point at server-provided safe resources. Secrets,
tokens, passwords, chat ids, raw provider output and private host paths are not
valid answers.

## Events

Append-only events:

- `request_created`
- `question_answered`
- `answer_revised`
- `question_skipped`
- `default_approved`
- `batch_completed`
- `understanding_confirmed`
- `ready_for_plan`
- `run_reopened`
- `run_paused`
- `run_cancelled`
- `run_expired`
- `run_blocked`

Every answer write uses:

- `clarification_id`
- `question_id`
- `expected_version`
- `idempotency_key`

Conflicting versions return a conflict event and the current run version.

## Legacy Normalization

Legacy `ask_user` calls with `{question, options, multi}` normalize into a
single-question v2 request:

- `scope`: `conversation`
- `questions[0].key`: deterministic hash of the question text
- `questions[0].type`: `multi_select` when `multi=true`, otherwise `single_select`
- `batch.total`: `1`
- `status`: `clarifying`

Legacy labels are never treated as durable answers without a server-issued
`clarification_id` and `question_id`.

## Plan-Unlock Invariant

Planning may start only when all are true:

- no unresolved required question remains;
- the user confirmed the understanding summary or approved visible defaults;
- no answer conflict is pending;
- no required answer is marked secret/private-path/raw-content;
- the server emits `ready_for_plan`.

Planner prompts, coding-task creation and mutating tool execution must read the
server state, not infer readiness from chat text.

## Memory Boundary

Clarification answers are scoped to session, project or coding task. They do not
automatically become global memory.

Stable user preferences may become memory candidates only through a separate
review path. Secrets and credentials must use secure handoff, never
clarification answers.

## UI Requirements

The Agent screen must show:

- active phase;
- visible batch progress;
- unanswered required count;
- saved partial answers;
- conditional follow-up state;
- understanding summary;
- plan-unlock state and blocker reason.

Historical answered questions must render read-only unless the run is reopened.

## Acceptance

OAW-ASK-1 is complete when this contract and schema examples exist and all
later ask-tool/runtime/frontend work references them instead of the legacy
single-question behavior.
