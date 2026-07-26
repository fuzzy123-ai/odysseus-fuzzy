# Telegram Session Rollover Transaction Contract

Stand: 2026-07-24

Status: `TTD-07A0` accepted at `4fb9ba62`; `TTD-07A1` accepted at
`daca1dc4`; `TTD-07A2` accepted at `090ede31`; `TTD-07A3` accepted at
`53109195`; `TTD-07A4` accepted at `c48f47c8`; the fresh architecture handoff
is accepted, `TTD-07A5A` is accepted at `70466cf1`, and `TTD-07A5B` is active

Authority:

- This document is the implementation contract for `TTD-07A`.
- The queue and live-gate authority remains
  `docs/plans/open-work-completion-master-roadmap.json`.
- `docs/plans/telegram-todo-domain-truth-roadmap.md` remains the human feature
  roadmap.
- Divergent commit `2cb685d8` is prior art only. It must not be cherry-picked
  or used as acceptance evidence.

## 1. Outcome and boundary

The first enabled Telegram polling cycle after the configured local boundary
must rotate every due bound Telegram Session at most once per owner, chat,
scope, and local rollover day. This includes a polling cycle with zero Telegram
updates.

The operation must preserve these invariants:

1. A model turn is never split across two Sessions.
2. The old binding remains usable until one replacement is fully committed.
3. Concurrent polls, webhook turns, process restarts, and missed polling cycles
   cannot create two visible replacements for one rollover day.
4. Session creation, binding publication, rollover completion, and old-Session
   archive are one SQLite transaction.
5. Notes plus validated domain receipts remain the only current Todo truth.
6. The default does not delete, truncate, compact, send, call a provider,
   deploy, or mutate a productive host.

The base rollover does not require continuity content. Continuity is a later,
default-off child slice and cannot weaken the base transaction.

## 2. One authority, one compatibility projection

SQLite is the only authoritative binding and rollover ledger after the
repository migration.

`telegram_session_bridge.json` becomes a compatibility projection:

- it may be imported once into SQLite under the migration rules below;
- it is never consulted to override an existing SQLite binding;
- projection writes use temp-file, flush, fsync, atomic replace, and
  directory-fsync where supported;
- every binding mutation commits `projection_status=stale` and the target
  generation in SQLite first;
- after a successful atomic projection write, a second conditional transaction
  marks `projection_status=current` only if the binding generation still
  matches;
- a failed projection write leaves durable `stale`; the next owner-scoped
  poll-start reconciliation retries it without rolling back the already
  committed SQLite binding;
- all current-code reads use SQLite after the import boundary;
- rollback means disabling future rollover while retaining the DB-aware bridge
  reader, not restoring the JSON file as authority.

Malformed legacy JSON fails closed for import. It must not be normalized to an
empty mapping and written back. Existing productive files are never deleted.

## 3. Canonical identity

The durable natural binding key is:

```text
(owner_ref, chat_handle_ref, scope)
```

The durable rollover idempotency key is:

```text
(binding_id, rollover_local_day)
```

Rules:

- `scope` is exactly `normal` or `secure`.
- `normalized_owner` is stripped and lowercased exactly like the canonical auth
  identity. Empty owner remains invalid.
- Enabling rollover requires
  `TELEGRAM_SESSION_ROLLOVER_REFERENCE_KEY`, a secret of at least 32 bytes.
  Missing, short, or changed keys fail closed before any binding lookup or
  legacy import; the key and its fingerprint are never logged or returned.
  Key rotation is a separately gated migration.
- `owner_ref`, `chat_handle_ref`, and evidence-only Session refs use
  `h1_ + HMAC-SHA256(reference_key, domain + "\0" + normalized_value)[:32]`
  with distinct domains `ttd07a-owner`, `ttd07a-chat-handle`, and
  `ttd07a-session`.
- `transport_update_ref` uses the same construction with domain
  `ttd07a-update` and the strict normalized tuple
  `"<update_id>:<message_id>"`; at least one bounded integer component must be
  present and neither component is stored raw in the intake table.
- `chat_handle_ref` hashes the existing stable Telegram handle, not the raw
  chat ID. Runtime callers derive the stable handle first; legacy import rules
  below handle old raw-key files without persisting that key.
- Raw owner values remain only where the existing Session schema requires them
  and never enter rollover evidence.
- Empty or malformed refs fail closed; no fallback global owner scope exists.
- Raw chat IDs, raw owner names, Telegram tokens, endpoint URLs, model names,
  Session IDs, prompts, messages, headers, secrets, and provider output are
  forbidden in rollover evidence and normal logs.

## 4. Database contract

### 4.1 `telegram_session_bindings`

One row owns the current binding:

| Field | Contract |
| --- | --- |
| `id` | opaque primary key |
| `owner_ref` | non-empty pseudonymous ref |
| `chat_handle_ref` | non-empty stable Telegram handle |
| `scope` | `normal` or `secure` |
| `active_session_id` | non-empty FK/reference to `sessions.id` |
| `active_rollover_local_day` | ISO local date last established for this binding |
| `generation` | non-negative monotonic integer |
| `turn_lease_ref` | nullable opaque lease reference |
| `active_turn_ref` | nullable opaque intake reference |
| `turn_lease_expires_at` | nullable UTC timestamp |
| `turn_started_at` | nullable UTC timestamp |
| `projection_status` | `current`, `stale`, or `blocked_multi_owner` |
| `projection_generation` | generation targeted by the compatibility projection |
| timestamps | creation and update timestamps |

Required constraints:

- unique `(owner_ref, chat_handle_ref, scope)`;
- `active_session_id` is never published empty;
- lease reference, active-turn reference, expiry, and start are all null or all
  populated;
- the binding generation increments exactly once with a committed replacement.
- binding rows are immutable identities: rollover, `/new`, and secure rebind
  update the same row; deletion/recreation is forbidden and referenced rows
  use `ON DELETE RESTRICT`.

### 4.2 `telegram_session_rollovers`

One row records the daily reservation and terminal result:

| Field | Contract |
| --- | --- |
| `id` | opaque primary key |
| `binding_id` | binding FK/reference |
| `rollover_local_day` | ISO local date |
| `status` | allowlisted state below |
| `old_session_id` | original active Session reference |
| `new_session_id` | nullable until the commit transaction builds it |
| `attempt_count` | bounded non-negative integer |
| `retry_after` | nullable UTC timestamp |
| `reason_code` | nullable allowlisted content-free code |
| timestamps | creation, update, and optional commit timestamps |

Required constraints:

- unique `(binding_id, rollover_local_day)`;
- one of the allowlisted state transitions only;
- committed rows have a non-empty `new_session_id`;
- no row contains prompt, message, summary, Todo state, provider data, or raw
  identity.

### 4.3 `telegram_turn_intakes`

One content-free row makes a ready-for-agent Telegram update durably
replayable without treating the JSON inbox as transaction authority:

| Field | Contract |
| --- | --- |
| `id` | opaque primary key and `telegram_turn_ref` |
| `owner_ref` | binding owner ref |
| `chat_handle_ref` | binding chat ref |
| `transport_update_ref` | keyed HMAC of stable update/message identity |
| `scope` | `normal` or `secure` |
| `binding_id` | immutable binding reference |
| `expected_session_id` | internal Session reference selected for the turn |
| `status` | `pending`, `lease_retry`, `running`, `reply_pending`, `completed`, `indeterminate_turn`, or allowlisted permanent block |
| `retry_count` | bounded non-negative count |
| `next_retry_at` | nullable UTC timestamp |
| `reason_code` | nullable content-free code |
| timestamps | creation and update timestamps |

Required constraints:

- unique `(owner_ref, chat_handle_ref, transport_update_ref)`;
- no raw update ID, message ID, chat ID, owner, prompt, reply, or provider data;
- binding deletion is restricted while an intake row exists;
- completed and indeterminate rows are immutable;
- the row, not a mutable JSON-only flag, decides whether a duplicate update is
  eligible for model-turn retry.

### 4.4 `telegram_rollover_metadata`

One singleton row prevents a changed HMAC key from silently creating a second
identity namespace:

| Field | Contract |
| --- | --- |
| `id` | fixed `reference_key_v1` primary key |
| `schema_version` | fixed positive integer |
| `reference_key_fingerprint` | full SHA-256 of `ttd07a-key-fingerprint\0` plus key bytes |
| timestamps | creation and update timestamps |

Before any ref derivation, lookup, import, binding write, lease, or sweep:

1. derive the fingerprint in memory;
2. create the singleton only when it is absent and no TTD-07A binding, rollover,
   or intake row exists;
3. compare it in constant time with the stored value;
4. on mismatch or ambiguous missing metadata with existing rows, return
   `reference_key_mismatch` and perform no mutation.

The fingerprint is stored only for equality detection. It is forbidden in
logs, evidence, public APIs, compatibility JSON, and exception text.

All four tables must be created through SQLAlchemy metadata plus the existing
idempotent startup-migration mechanism. Upgrade and fresh-install paths must
produce the same constraints.

## 5. Configuration and local-day policy

All behavior is fail-closed and default-off:

| Setting | Default | Validation |
| --- | --- | --- |
| `TELEGRAM_SESSION_ROLLOVER_ENABLED` | false | explicit boolean only |
| `TELEGRAM_SESSION_ROLLOVER_REFERENCE_KEY` | none | secret, at least 32 bytes when enabled |
| `TELEGRAM_SESSION_ROLLOVER_TIMEZONE` | `Europe/Berlin` | valid `ZoneInfo` |
| `TELEGRAM_SESSION_ROLLOVER_BOUNDARY` | `04:00` | strict `HH:MM` |
| `TELEGRAM_SESSION_ROLLOVER_MAX_ATTEMPTS` | 8 | integer 1..24 |
| `TELEGRAM_SESSION_ROLLOVER_RETRY_SECONDS` | 300 | integer 60..3600 |
| `TELEGRAM_SESSION_TURN_LEASE_SECONDS` | 7200 | integer 60..14400 |
| `TELEGRAM_SESSION_CONTINUITY_ENABLED` | false | separate later gate |

`rollover_local_day` is derived from an aware injected clock:

1. convert `now` to the configured zone;
2. compare the observed local wall clock with the configured boundary;
3. use the observed local date at or after the boundary, otherwise the
   previous local date.

No UTC-date comparison and no elapsed-24-hours approximation is allowed.
Ambiguous or skipped DST wall times must still map each observed instant to one
local day. Multiple missed days create one replacement for the current
effective local day, not one replacement per missed day.

A newly created or newly imported binding starts at the current effective local
day. Enabling the feature therefore does not immediately rotate every existing
chat; the first due day is the next boundary.

## 6. State machine

Allowlisted transitions:

```text
absent
  -> deferred_active_turn | deferred_exhausted
  -> blocked_invalid_binding | blocked_security_policy
  -> committed

deferred_active_turn
  -> deferred_active_turn | deferred_exhausted | committed

deferred_exhausted
  -> deferred_exhausted | committed
```

Rules:

- `absent -> committed` is the normal transaction.
- A valid active-turn lease causes
  `absent|deferred_active_turn -> deferred_active_turn`, increments the bounded
  attempt count only on or after `retry_after`, sets the next `retry_after` to
  the earlier of lease expiry or `now + retry_seconds`, and leaves the binding
  unchanged. Polls before `retry_after` return the existing row without an
  attempt increment.
- Reaching the configured bound causes `deferred_exhausted`. The binding stays
  unchanged and further active-lease polls do not increment or spin. Exact turn
  release makes the row immediately eligible; a crashed turn becomes eligible
  after its renewed lease finally expires. Either state may then transition to
  `committed`, so bounded retries never permanently suppress rollover.
- Invalid binding/session configuration produces `blocked_invalid_binding`.
- A secure scope whose local-only policy cannot be validated produces
  `blocked_security_policy`.
- `blocked_invalid_binding` and `blocked_security_policy` are terminal for that
  local day. A later local day receives a new idempotency row.
- `committed` is immutable except for content-free projection/reconciliation
  metadata kept outside the authoritative state.

No state named `pending` or `recovery_required` is needed across commits:
reservation, replacement Session, binding swap, archive, and committed status
are one database transaction. A process crash before commit rolls back all of
them; a crash after commit observes the complete committed state.

## 7. Atomic rollover transaction

For one due binding, the coordinator performs this sequence under the shared
process mutex and a SQLite `BEGIN IMMEDIATE` write transaction:

1. Re-read the immutable binding identity and get the daily rollover row.
2. If no row exists, insert the unique row inside this transaction before any
   Session insert with uncommitted `deferred_active_turn` and attempt count 0.
   The transaction must decide a committed allowlisted outcome before commit.
   A uniqueness loser rolls back and reloads the winner.
3. Return the immutable committed or terminal-blocked result when already
   present.
4. If `retry_after` is in the future, return the existing deferred state
   without mutation.
5. If a renewed or in-process active-turn lease exists, transition the same
   daily row to `deferred_active_turn` or `deferred_exhausted`, commit only that
   deferral, and leave the binding unchanged.
6. If an expired lease exists and no matching in-process turn remains, clear it
   in the same transaction and use reason code
   `expired_turn_lease_recovered`.
7. Validate that the active old Session exists, belongs to the expected owner,
   and satisfies scope/security requirements. Transition the same daily row to
   the appropriate blocked state on failure.
8. Construct exactly one new `sessions` row with a precomputed opaque ID.
9. Update the binding to that ID, current local day, generation + 1, and
   `projection_status=stale`.
10. Mark the old Session archived.
11. Transition the same daily rollover row from absent/deferred to `committed`
    with old/new references and commit all four changes together.

Database uniqueness is the cross-process arbiter. A unique-conflict loser rolls
back and reads the winning committed/deferred row once. A busy or unavailable
database returns the allowlisted `database_busy` outcome and leaves the old
binding untouched; no unbounded retry loop is allowed.

The replacement Session copies only:

- endpoint URL;
- model identifier;
- owner;
- the `secure` scope policy after validation.

It deliberately resets or omits:

- messages and summaries;
- headers and credentials, which the existing safe runtime resolver must
  reconstruct;
- RAG state;
- folder, importance, tools, task state, Todo claims, Memory, and continuity
  content.

The Session name is generic and bounded. It must not contain raw chat or owner
identity.

After commit, in-memory Session caches are refreshed from SQLite. Cache refresh
is not part of authority: a crash before refresh remains recoverable because
`get_session` must load the committed row.

## 8. Turn lease and concurrency

One module-owned non-reentrant `threading.Lock` plus an in-memory active-turn registry is
shared by polling, webhook, bridge, and turn callbacks in the supported
single-Uvicorn-worker deployment.

The DB lease remains required because the lock does not survive restart and
does not protect a future multi-process runtime.

All locked coordinator methods are synchronous and contain no `await`.
Asynchronous webhook callers enter them through `asyncio.to_thread`; therefore
two event-loop coroutines never share a reentrant same-thread critical section.
The lock is released before every model, provider, Telegram, or other network
call.

Before a Telegram model turn:

1. Re-resolve the current binding.
2. Atomically acquire a lease with one conditional DB update only when
   `expected_session_id` still equals the active binding and the previous lease
   is null or expired; exactly one affected row proves acquisition.
3. If the binding changed, rebuild the bridge once and do not call the model on
   the stale Session.
4. Run the model turn.
5. Release the exact matching lease in `finally`, after the existing final
   Session message writes.

Lease tokens are random in memory; only an opaque reference is persisted.
Release with a mismatched token is rejected. While a turn runs, a bounded
background renewer refreshes its DB expiry at least every
`min(60 seconds, lease_seconds / 3)` and stops in `finally`. The process mutex
is held only for short state transitions, never during a model/provider call.
A crashed turn is recovered only after renewal stops and the bounded expiry
passes. A rollover never interprets expiry as proof that a reply succeeded.

Acquire, renew, and release are fenced by binding ID, generation,
`active_turn_ref`, and lease ref. Renewal with any stale fence affects zero
rows. Exact release clears all lease fields and makes the current day's
`deferred_active_turn|deferred_exhausted` row immediately eligible in the same
transaction.

Every binding mutation uses the same guarded API and re-checks the lease in its
write transaction. This includes daily rollover, ordinary `bind_chat`,
`rebind_chat`, Telegram `/new`, and the secure/local fallback currently called
inside the agent handler. A secure fallback must run before turn acquisition or
present the exact matching lease and atomically transfer it; no unleased
mid-turn rebind is permitted.

The polling route and webhook route must share this coordinator. Route-level
locks must not be instantiated per request.

## 9. Poll-start and webhook behavior

Polling order:

1. validate admin and polling-enabled gates;
2. inject the owner resolved by the application Telegram owner boundary; never
   reuse the unrelated `memory_owner` fallback;
3. when rollover is enabled, perform the bounded legacy import for that owner
   and then run the due-binding sweep only for the same `owner_ref`, never
   across every tenant;
4. fetch Telegram updates, even when the sweep found nothing;
5. process updates through the same binding and turn-lease service;
6. return only content-free rollover counts/statuses.

The sweep runs before `fetch_updates`, so a successful empty first poll after
the boundary satisfies the feature contract.

Webhook behavior:

- it does not replace the periodic sweep;
- before an agent turn it performs the same current-binding resolution and
  turn-lease acquisition;
- when a rollover won the race, it rebuilds the bridge once;
- when a turn lease cannot be acquired safely, it follows the durable retry
  contract below without invoking the model or sending a fabricated success.

## 10. Lossless busy-turn retry

The SQLite `telegram_turn_intakes` row owns the content-free agent-turn
lifecycle:

```text
pending -> lease_retry -> running -> reply_pending -> completed
running -> indeterminate_turn
```

It stores only opaque refs, status, bounded retry count, next retry time, and
reason code. Raw message content remains only in the existing inbound record
under its existing privacy classification; JSON is payload storage, not retry
authority.

The same transport hold/503 behavior applies to a busy binding-mutating
control command such as `/new`; it must not be consumed with a false success
while another lease owns the binding.

Polling rules:

1. A temporary lease/database-busy result commits `lease_retry` in SQLite,
   sets
   `hold_offset_for_retry=true`, and stops processing later updates in that
   cycle.
2. The duplicate intake path queries the SQLite lifecycle and returns
   `retry_pending_agent_turn=true`; that makes the stored update processable
   even when JSON deduplication returns `stored=false`.
3. Polling never advances past the held update until its lifecycle is
   `completed` or an allowlisted permanent blocked outcome has produced its
   explicit safe response.
4. After lease acquisition, mark `running`. The handler adds the same opaque
   `telegram_turn_ref` to its existing user/assistant Session message metadata.
   After both writes, mark `reply_pending`; after the existing reply gate has a
   durable outbound `sent` record, mark `completed` and allow offset advance.

Webhook rules:

1. A temporary lease/database-busy result commits `lease_retry` in SQLite and raises the
   dedicated HTTP 503 retry outcome with a bounded `Retry-After`; it never
   returns a normal 2xx payload.
2. A redelivered duplicate with `lease_retry` is processed again rather than
   discarded by deduplication.
3. A `running` retry first reconciles the Session message pair by
   `telegram_turn_ref`: a persisted assistant result is reused for
   `reply_pending` rather than invoking the model again.
4. A completed lifecycle returns content-free `duplicate_completed` without
   invoking the model or sending a second reply. A `reply_pending` retry checks
   the existing outbound `sent` record for the source message before any send.
5. No local busy-wait or unbounded in-process retry loop is allowed; Telegram
   redelivery plus the durable marker owns transport retry.

Crash reconciliation is fail-closed:

- an exact user-plus-assistant pair moves to `reply_pending` and reuses the
  assistant result;
- no pair, user-only, assistant-only, duplicate, or malformed pairs move to
  terminal `indeterminate_turn`;
- `indeterminate_turn` never invokes the model, tools, or a mutation again; it
  retains the durable intake and sends at most one non-claiming safe response
  that asks for explicit retry/review;
- polling may advance only after that safe response is durably recorded, and
  webhook may return 2xx only with the same terminal content-free outcome;
- automatic retry from `indeterminate_turn` is forbidden because model/tool
  effects may already have occurred before the crash.

Focused tests must force the lease-busy path for polling and webhook and prove:
the model is not called while busy, the poll offset is held, the webhook is
non-2xx, the duplicate is eligible, one later lease acquisition completes the
turn, a persisted `running` result is reconciled without a second model call,
partial or absent message pairs become `indeterminate_turn`, and a completed
duplicate invokes neither model nor reply again.

## 11. Legacy import and cutover

The first DB-aware bridge read or enabled poll-start sweep handles legacy
state:

1. If the DB binding exists, use it and do not import JSON.
2. The poll-start importer enumerates the valid legacy stable-handle keys for
   the injected owner before querying due bindings. If no DB binding exists
   and legacy JSON is valid, import only a non-empty normal/secure Session ID
   that exists and matches the current owner.
3. Scope rows are imported separately.
4. Set the imported binding local day to the current effective local day and
   generation 0.
5. A legacy key matching `chat_[0-9a-f]{12}` is used as the stable handle.
   Otherwise, only a bounded legacy raw-ID key matching
   `^-?[1-9][0-9]{0,19}$` is converted in memory with the existing
   `_chat_handle` function; a valid `mapping.chat_handle` wins when present.
   Raw keys are never logged, emitted, or copied into SQLite.
6. After all imported slots are committed, the next atomic compatibility
   projection contains stable handles only. Until that succeeds, the original
   file remains untouched.
7. If JSON is malformed, a Session is missing, or owner validation fails,
   leave JSON untouched, create no binding, emit a content-free blocked
   reason, and keep rollover disabled for that binding.
8. Concurrent imports rely on the binding uniqueness constraint and return the
   winning row.

The compatibility JSON cannot represent multiple owners for one stable chat
handle. Projection is therefore generated only for the injected Telegram
owner. If a conflicting owner-scoped binding exists, set
`projection_status=blocked_multi_owner`, retain SQLite authority, and never
overwrite either binding or the existing file. No bulk destructive migration
is allowed.

## 12. Continuity child slice

Continuity is not stored in either ledger table.

When separately enabled, the first eligible user turn in the new Session may
receive a copied tail from the archived old Session through the accepted
`TTD-07` context policy:

- at most 6 historical messages and 4,000 characters;
- explicitly re-enveloped as untrusted user data;
- consumed for one turn only and never persisted as a system message;
- no system summaries, task-state artifacts, Tool results, IDs, or secrets;
- never evidence for Todo, calendar, file, send, provider, or mutation claims.

Every current domain claim still requires the canonical domain read and any
required validated receipt/postcondition.

## 13. Evidence and logging

Allowed evidence fields:

- owner/chat/binding/session opaque refs;
- scope;
- local day;
- state and reason code;
- generation;
- attempt and due/committed/deferred counts;
- booleans proving raw content and raw identity are absent.

Forbidden evidence:

- raw owner, chat ID, Session ID, endpoint, model, token, headers;
- prompt, message, summary, continuity text, Todo text;
- provider response or exception body.

Errors are mapped to allowlisted reason codes. Arbitrary exception strings are
not persisted or returned in public rollout evidence.

## 14. Serial implementation DAG

Only one child is writable at a time. Every transition requires a committed
claim and explicit root handoff.

### TTD-07A1 - Pure policy and state-machine contract

Depends on: accepted `TTD-07A0`

Allowed paths:

- `src/telegram_session_rollover.py`
- `tests/test_telegram_session_rollover.py`

Acceptance:

- strict default-off configuration and limits;
- aware local-day/DST policy;
- allowlisted state transitions and content-free evidence;
- no DB, Session, plugin, network, provider, send, or live mutation.

### TTD-07A2 - Durable ledger schema and repository

Depends on: accepted `TTD-07A1`

Allowed paths:

- `core/database.py`
- `core/database_migrations.py`
- `src/telegram_session_rollover.py`
- `tests/test_telegram_session_rollover.py`

Acceptance:

- fresh and upgraded DBs create all four tables and uniqueness constraints;
- concurrent reservation/import operations return one winner;
- invalid identity and malformed rows fail closed;
- no application route is activated.

### TTD-07A3 - DB-authoritative bridge and legacy import

Depends on: accepted `TTD-07A2`

Allowed paths:

- `plugins/telegram/stores.py`
- `src/telegram_session_rollover.py`
- `tests/test_telegram_plugin.py`
- `tests/test_telegram_session_rollover.py`

Acceptance:

- a DB-aware bridge mode requires explicit injected owner and DB binding wins
  over JSON in that mode;
- valid legacy slots import once;
- malformed JSON is never overwritten;
- JSON projection is atomic and non-authoritative;
- normal and secure scopes remain separate.
- existing production call sites remain on the legacy adapter until A5; A3
  does not silently infer an owner or partially cut over runtime routing.

### TTD-07A4 - Atomic Session lifecycle

Depends on: accepted `TTD-07A3`

Allowed paths:

- `app.py`
- `core/session_manager.py`
- `src/telegram_session_rollover.py`
- `tests/test_telegram_session_rollover.py`
- `tests/test_telegram_context_policy.py`

Acceptance:

- replacement Session, binding swap, old archive, and committed row share one
  transaction;
- crash before commit leaves old binding active and no orphan;
- crash after commit loads the one replacement from DB;
- clone allowlist and secure-scope validation pass;
- no continuity or route activation yet.

### TTD-07A5 - Poll/webhook sweep and turn lease

Depends on: accepted `TTD-07A4`

Current gate:

- Two bounded implementation attempts were rejected on 2026-07-24.
- The rejected partial coordinator diff is not an implementation commit and
  must not be deployed or resumed as a third routine attempt.
- Re-entry requires a fresh read-only architecture handoff that freezes the
  stable owner/chat/transport-update identity, caller-owned per-operation
  database/session lifetime, atomic intake-plus-binding-lease fencing, polling
  offset hold, webhook unacknowledged retry, and explicit owner cutover.
- A fresh durable implementation claim is required after that handoff.

Architecture handoff accepted on 2026-07-26:

1. **Stable authority and identity.** A5 resolves one non-empty
   `telegram_owner` at the application boundary and injects it through poll,
   webhook, control, bind, rebind, secure fallback, Session creation, agent
   execution, and Todo-digest scheduling. When A5 is enabled, a missing or
   ambiguous owner fails closed; `memory_owner`, the literal `telegram`, raw
   chat identity, and first-admin or first-user selection are not substitutes.
   The durable intake natural key is exactly
   `(owner_ref, chat_handle_ref, transport_update_ref)`. The update ref is
   derived from the strict bounded `(update_id, message_id)` pair and at least
   one component is required. Only keyed opaque refs are persisted or emitted.
2. **Per-operation database lifetime.** One synchronous coordinator receives a
   `SessionLocal`-compatible factory. Every ledger operation obtains its own
   clean SQLAlchemy Session, verifies the reference-key fingerprint, begins the
   required short transaction, commits or rolls back, and closes in `finally`.
   No database Session crosses model, provider, tool, reply, or Telegram I/O.
   One shared non-reentrant process mutex covers only the short ledger
   transition; the SQLite write fence remains `BEGIN IMMEDIATE`.
3. **Atomic intake and lease fence.** Intake get-or-create, immutable binding
   ID, generation, active Session, owner/chat/scope, and expected Session are
   validated before model execution. Binding lease acquisition and
   `pending|lease_retry -> running` commit together and require the prior lease
   to be null or expired. A loser persists `lease_retry` and never invokes the
   model, tools, mutation, or reply. Renewal and release compare the complete
   binding/generation/intake/lease tuple; stale operations update zero rows.
   Release clears all lease fields in one transaction and immediately exposes
   a deferred rollover to the accepted state machine.
4. **Turn completion and recovery.** The opaque turn ref is written into both
   Session message markers. Exactly one durable user-plus-assistant pair moves
   `running -> reply_pending` and reuses the assistant result. Any absent,
   partial, duplicate, or malformed pair becomes terminal
   `indeterminate_turn` and is never automatically replayed. Only an existing
   durable outbound `sent` marker moves `reply_pending -> completed`. A
   completed duplicate invokes neither model nor reply.
5. **Polling acknowledgement point.** An enabled poll resolves the owner,
   performs the bounded owner-scoped legacy import and due-binding sweep, and
   only then fetches the current offset. A temporary database or lease-busy
   outcome commits `lease_retry`, holds the exact current update offset, stops
   later update processing, and returns retryable status. The offset advances
   only after `completed` or an allowlisted terminal outcome with its durable
   safe response. JSON inbox deduplication remains payload/audit assistance and
   never overrides the SQLite intake lifecycle.
6. **Webhook acknowledgement point.** Webhook uses the same coordinator and
   intake identity. Temporary busy persists `lease_retry` and returns HTTP 503
   with bounded `Retry-After`, never 2xx. Redelivery of `lease_retry` remains
   eligible even when the JSON inbox reports a duplicate. `completed` returns
   a content-free duplicate outcome; `running`, `reply_pending`, and
   `indeterminate_turn` follow the recovery rules above.
7. **Binding and owner cutover.** New bind, `/new`, rebind, secure fallback,
   and rollover use one guarded mutation API. DB authority wins when a valid
   binding exists. Legacy JSON may seed one owner-validated slot only; malformed
   or cross-owner state is left untouched. Secure fallback happens before lease
   acquisition or transfers the exact matching lease atomically. An unleased
   mid-turn rebind is forbidden. Busy mutations are retryable and cannot return
   a success claim.
8. **Activation and rollback.** A5 remains strictly default off through all
   repository slices. Disabled behavior performs no new ledger intake, import,
   sweep, rollover, provider call, send, or productive mutation. Repository
   rollback disables A5 while preserving DB bindings, Sessions, ledger rows,
   and compatibility projection; JSON is never restored as authority and no
   productive Session is repaired, archived, or deleted.

The rejected eleven-path implementation is replaced by three serial claims:

- `TTD-07A5A-ledger-turn-coordinator`: only
  `src/telegram_session_rollover.py` and
  `tests/test_telegram_session_rollover.py`; implement per-operation factory
  lifetime, stable identity, fenced acquire/renew/release, intake transitions,
  and crash reconciliation without route wiring. Accepted at `70466cf1` after
  13 focused tests, including the bounded two-thread file-SQLite winner/busy
  lane, plus deep Sol lease and recovery review.
- `TTD-07A5B-owner-binding-cutover`: only after A5A acceptance; wire the
  mandatory injected owner and guarded binding/rebind/secure-fallback mutation
  seam through `app.py`, `plugins/telegram/stores.py`,
  `plugins/telegram/control_service.py`, and focused existing tests.
- `TTD-07A5C-poll-webhook-transport`: only after A5B acceptance; wire the
  owner-scoped pre-fetch sweep, exact offset hold, webhook 503 retry, duplicate
  recovery, and final integration through polling, routes, plugin, and webhook
  service. The original five A5 acceptance nodes remain the final gate.

Each child requires a fresh durable claim and deep Sol review. No child may
resume the rejected stash or divergent prior art, and no child may enable live
Telegram behavior.

Allowed paths:

- `plugins/telegram/polling.py`
- `plugins/telegram/plugin.py`
- `plugins/telegram/routes_polling.py`
- `plugins/telegram/webhook_service.py`
- `plugins/telegram/control_service.py`
- `plugins/telegram/stores.py`
- `app.py`
- `src/telegram_session_rollover.py`
- `tests/test_telegram_plugin.py`
- `tests/test_telegram_webhook_service.py`
- `tests/test_telegram_session_rollover.py`

Acceptance:

- first empty poll after the boundary sweeps due bindings;
- poll and webhook share one coordinator and durable turn lease;
- two concurrent polls create one replacement;
- active turn defers boundedly and is never split;
- `/new`, secure fallback, bind, rebind, and rollover all honor the same lease;
- busy polling holds the offset and busy webhook delivery remains retryable;
- injected Telegram owner is mandatory at the A5 cutover and `memory_owner`
  never substitutes for it;
- feature remains default-off.

### TTD-07A6 - Optional one-turn continuity

Depends on: accepted `TTD-07A5`

Allowed paths:

- `app.py`
- `src/telegram_context_policy.py`
- `src/telegram_session_rollover.py`
- `tests/test_telegram_context_policy.py`
- `tests/test_telegram_session_rollover.py`

Acceptance:

- default-off, one-turn, bounded untrusted tail;
- no persistent summary or domain authority;
- a false historical success claim cannot satisfy a current mutation claim.

Concurrent workers may work only on paths proven disjoint from the active child.
There is currently no active `TTD-07A5` implementation child.
The files listed in multiple children are serial hotfiles, not parallel claims.

## 15. Focused acceptance suite

The final base feature requires focused nodes proving:

- parallel due polls reserve and commit one replacement;
- an empty first poll after the Berlin boundary performs the sweep;
- active-turn deferral does not advance the binding and stops at the bound;
- crashes before and after commit expose old-or-new, never empty-or-duplicate;
- normal and secure scopes rotate independently;
- fresh-install and upgrade constraints match;
- DST, restart, and missed-day behavior use the injected clock;
- malformed legacy JSON remains untouched;
- current Todo state after rollover comes from `manage_todos`;
- evidence contains no raw identity or conversation content.

No broad suite is required for an individual child. Each claim declares its
exact nodes; root runs the strongest affected subset during deep review.

## 16. Rollback and live gates

Repository rollback:

- keep the DB-aware bridge reader;
- set `TELEGRAM_SESSION_ROLLOVER_ENABLED=false`;
- stop future reservations and sweeps;
- retain all Sessions, bindings, ledger rows, and legacy projections.

Forbidden without separate action-specific GO:

- Git push;
- Debian deployment or host mutation;
- productive DB migration;
- Telegram send or write smoke;
- provider call;
- productive rollover activation;
- productive Session repair, archive, rebind, delete, or truncate.

Repository acceptance does not grant any of these live actions.
