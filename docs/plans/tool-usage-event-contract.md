# Privacy-Safe Tool Usage Event Contract

Status: TUA1 repository contract implemented on 2026-07-17. Persistence,
runtime capture, backfill, metrics and UI activation remain disabled and belong
to later slices.

Schema: `odysseus.tool_usage_event.v1`

## Boundary

`src/tool_usage_events.py` defines an allowlist-only event and builder. The
builder accepts a validated TAX `ToolDescriptorV2`; callers cannot provide a
second free-form tool family, source or analytics identity. It performs no
database write and has no generic metadata, payload, args or result input.

## Event Semantics

- `started` contains no terminal status, duration, error, blocked reason or
  result bucket.
- `terminal` uses exactly one of `succeeded`, `failed`, `blocked`, `cancelled`
  or `rejected`.
- Failure carries a bounded error class, never an exception message.
- Blocked/rejected carries a bounded reason code, never policy text.
- Duration and retry ordinal are bounded integers.
- Argument and result sizes are persisted only as `none/xs/s/m/l/xl`.
- Result shape is a bounded enum (`none`, `scalar`, `mapping`, `sequence`,
  `binary`, `unknown`), never a field list or value.

The IDs use opaque random prefixes (`tue_`, `tui_`) and cannot embed owner,
session, path or provider values. Timestamps normalize to UTC.

## Pseudonymous References

Owner, session, run and correlation inputs are domain-separated with keyed
HMAC-SHA256 and stored only as bounded `h1_...` references. A key must contain
at least 32 bytes. When the installation-local key is absent, the builder emits
no raw fallback and records the reference state as `unavailable`.

The key is excluded from builder representation and event serialization.

## Persistence Decision

The builder records `persistence_allowed=false` with reason `incognito` or
`nobody` before any persistence layer exists. A later store must reject such an
event; it must not reinterpret the flag as advisory.

## Explicitly Absent

The serialized field allowlist has no prompt, message, command, code, diff,
arguments, result, output, exception message, traceback, file path, URL,
hostname, provider response, token, credential, raw owner/session/run ID,
binary data or free metadata map.

Unknown serialized fields fail closed. `raw_content_visible` is required and
must be `false`; serialization is canonical JSON with sorted keys.
