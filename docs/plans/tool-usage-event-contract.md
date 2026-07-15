# Privacy-Safe Tool Usage Event Contract

Status: repository contract implemented by TUA1; persistence and capture are not active
Schema: `odysseus.tool_usage_event.v1`

## Boundary

This contract defines content-free values for a future tool-usage writer. It
does not add a store, database migration, runtime hook, API, metric, capture,
backfill, export, or feature activation. The future primary signal remains the
shared `execute_tool_block` boundary and must preserve tool behavior when
telemetry fails.

## Persistent Allowlist

The event value contains only these fields:

| Field | Rule |
| --- | --- |
| `schema_version` | Constant `odysseus.tool_usage_event.v1`. |
| `event_id` | Opaque bounded ID with no embedded domain value. |
| `invocation_id` | Opaque bounded correlation for at most one started and one terminal event. |
| `event_kind` | `started` or `terminal`. |
| `occurred_at` | Timezone-aware timestamp normalized to UTC. |
| `duration_ms` | Bounded non-negative integer, terminal only. |
| `tool_analytics_id` | Canonical lowercase TAX analytics slug. |
| `tool_family` | Controlled TAX `ToolFamily`. |
| `tool_source` | Controlled TAX `ToolSource`. |
| `surface` | `chat`, `agent`, `scheduler`, `api`, `mcp`, or `system`. |
| `status` | Terminal-only `succeeded`, `failed`, `blocked`, `cancelled`, or `rejected`. |
| `error_class` | Bounded class only; no exception message or traceback. |
| `blocked_reason_code` | Bounded policy, permission, disabled, unknown-tool, unavailable, or rate-limit reason. |
| `retry_ordinal` | Integer from 0 through 100; never a free retry identifier. |
| `argument_size_bucket` | `none`, `xs`, `s`, `m`, `l`, or `xl`. |
| `result_size_bucket` | The same bounded size buckets; never a raw size or result. |
| `result_shape_bucket` | `none`, `scalar`, `mapping`, `sequence`, `binary`, or `unknown`; never field names. |
| `owner_ref`, `session_ref`, `run_ref`, `correlation_ref` | Nullable, namespaced keyed-HMAC references only. |
| `model_scope` | `local`, `remote`, `mixed`, or `unknown`; no provider or model name. |
| `agent_mode` | `chat`, `agent`, `background`, or `system`. |
| `app_version` | Bounded path-free version token. |

The safe audit serialization adds only
`raw_content_visible=false`. There is no generic metadata, payload, argument,
result, label, or extension map.

## Event Invariants

A started event has no status, duration, error, blocked reason, result size, or
result shape. A terminal event has a status and bounded duration.

- `succeeded` has no error or blocked reason;
- `failed` has one bounded error class and no blocked reason;
- `blocked` and `rejected` have one bounded reason and no error class;
- `cancelled` has neither error nor blocked reason.

`unknown` is an error or shape data-quality class, never a terminal success
status. Invalid combinations fail closed before an event value exists.

## Error Classes

The controlled error classes are `execution_error`, `timeout`,
`dependency_error`, `validation_error`, `policy_error`, `unavailable`, and
`unknown`. A free exception message, stack trace, command preview, or error
payload is not accepted and cannot be serialized.

## Pseudonymous References

`pseudonymize_reference` uses HMAC-SHA-256 with a separate namespace for owner,
session, run, and correlation references. The output contains the schema prefix,
namespace, and a bounded digest. It never contains the input value or key.

If the installation-local key is absent, the helper returns no reference. It
does not hash without a key, copy the source value, or use another identifier as
a fallback. A short or invalid key fails closed. The event builder accepts only
well-formed HMAC references in the matching namespace.

## Incognito And Nobody

The builder receives trusted `incognito` and `owner_is_nobody` policy flags.
Either state returns a suppression decision with no event and
`persistence_allowed=false`. This is an early contract boundary for later
instrumentation and store work; TUA4 will propagate the trusted runtime context
and TUA2 will enforce the writer boundary.

## Size Buckets

`size_bucket_for_count` reduces a non-negative count to a bounded enum:

- `none`: 0;
- `xs`: 1 through 128;
- `s`: 129 through 1,024;
- `m`: 1,025 through 8,192;
- `l`: 8,193 through 65,536;
- `xl`: above 65,536.

The event retains only the bucket. Content, raw size, field names, and values
are outside the contract.

## Explicit Denylist

The builder has named parameters only. Unknown fields fail with a programming
error. The following never belong in an event or its audit serialization:

- prompts, arguments, results, commands, code, diffs, or shell output;
- exception messages, tracebacks, or previews;
- filenames, paths, URLs, hostnames, or provider responses;
- document, memory, mail, calendar, contact, or chat content;
- keys, tokens, cookies, headers, credentials, or binary data;
- raw owner, session, run, task, chat, or external identifiers;
- arbitrary labels or unbounded metadata maps.

## Deferred Work And Activation Boundary

TUA2 owns persistence, migration, uniqueness, retention, and failure isolation.
TUA3 and TUA4 own runtime instrumentation and trusted context propagation.
Capture, database writes, legacy reads, backfill, metrics, API/UI projection,
external export, deployment, and live activation remain disabled. The dormant
`TUA-LIVE-ACTIVATION` contract is not materialized by TUA1.
