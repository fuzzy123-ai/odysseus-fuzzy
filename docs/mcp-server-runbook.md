# Odysseus MCP Server Runbook

## Status

The MCP Server plugin is installed as `plugins/mcp_server` and is disabled by default.

This is intentional. Enabling the plugin creates a powerful automation surface for trusted MCP clients.

## Safe Defaults

- Endpoint: `POST /api/plugins/mcp`
- Admin setup page: `/api/plugins/mcp/app`
- Runtime gate: disabled unless the admin config or `ODYSSEUS_MCP_SERVER_ENABLED=true` enables it.
- Planning tools also require the separate `allow_planning_reads` admin setting.
- HTTP authentication has two isolated exact scopes: `mcp:planning:read` and
  `mcp:harbour:inbox`. Each is minted for the active administrator identity
  and must be issued alone; neither may be combined with another scope.
- Each HTTP request binds the middleware cache pair to the persisted token
  owner and the current active administrator alias/subject. A rename or cache
  refresh window fails closed until all three agree; retry after the owner
  rename transaction and token-cache invalidation have completed.
- Accepted durable Planning reads resolve the stable accepted owner through the
  authenticated immutable administrator subject and trusted issuer. New
  acceptances bind that subject in the accepted transition; older accepted
  batches without a verifiable subject binding fail closed. The internal
  legacy-binding operation is not exposed through MCP or browser routes, and
  production bootstrap requires a separate live approval and readback.
- `mcp:planning:read` exposes exactly the seven read-only Planning tools:
  `planning_list_roadmaps`, `planning_read_roadmap`,
  `planning_search_roadmaps`, `planning_get_context_pack`,
  `planning_graph_summary`, `planning_read_gate_definitions`, and
  `planning_create_agent_handoff`. `mcp:harbour:inbox` independently exposes
  exactly `harbour_inbox_list`, `harbour_inbox_pause`,
  `harbour_inbox_reopen`, and `harbour_inbox_cancel`. Cross-scope calls fail
  with invalid parameters. MCP scopes are issuance-only: scope-PATCH upgrades
  are forbidden. Generic APIs, provider tools, resources, prompts, debug
  methods and legacy state-changing Planning helpers are not dispatched.
- `expose_all`: not supported in this MVP.
- Generic API tool: hidden by default.
- Shell/Python/file-write/email-send/admin/settings/token/plugin-management tools: hidden by default.
- Docker MCP is not part of this deployment model. Runtime checks must assume Podman/pods and remain read-only unless a separate operator approval explicitly allows a mutation.

## Native private-context consent package

The local consent package in `src/mcp_private_context_confirmation.py` and
`src/mcp_private_context_confirmation_app.py` has passed local package acceptance.
Local package acceptance does not enable the plugin, mount its HTTP adapter, or
extend either bearer-token scope's tool inventory.

Private disclosure requires authenticated browser attachment, the exact human
confirmation command, and a signed acknowledgement pair before an opaque
execution permit can be issued. Native Planning execution revalidates the
accepted context, revisions, lease, and cookie binding; completion is available
only after the guarded page has been committed.

`src/mcp_context_planning_page.py` provides an inactive Planning-only publication
adapter. It encodes the complete bounded JSON-RPC page inside the guarded
execution and returns those bytes only after consuming the issued one-use
completion. Its page schema preserves exact ranges, hashes and Base64 bytes;
it does not mint a cursor or claim whole-pack completion. Dispatcher wiring
and default activation remain separate work.

`src/mcp_context_native_page.py` provides the inactive `publish_forge_page`
and `publish_knowledge_page` adapters accepted in commit `7e7a634`. They require
a service-issued permit and genuine guarded paired cookie/MCP captures. The
complete bounded JSON-RPC page is encoded and validated before its one-use
completion is consumed; successful consumption is followed only by returning
the already frozen bytes. Changed source or recipient evidence denies content
publication. Pre-execution rejection uses the permit's weak original-state
anchor to clear only its proven owned request; an unproven anchor fails closed
without mutating unrelated requests.

That component received 217 revision-bound local PASS results and independent
deep review. These results accept the inactive publication component only;
mixed-pack coordination has its separate acceptance described below.
Dispatcher wiring, default activation and live behavior still require their
own implementation and acceptance evidence.

## Inactive mixed-pack coordinator

Commit `fceb2a2487e85ab3b2ca584b6ab69b8da79744ec` accepts
`src/mcp_context_pack_coordinator.py` with its private-consent integration and
coordinator tests. The coordinator combines the guarded native page adapters
without activating an HTTP tool or adding an admission path.

Local acceptance at that commit comprises 23 freshly executed coordinator
cases plus 73 independently approved, unchanged regression results: 96 unique
cases. The 73 results are scoped reuse, not a fresh 96-case run. Independent
deep review checked the current coordinator, the bounded reuse proof and the
fresh runtime receipt. Failed cleanup-anchor or substituted execution-fence
evidence denies publication before caller-controlled comparisons.

This evidence belongs to the stated commit. The subsequent request-ID
compatibility change and direct JSON-RPC request runtime require their own
current checks and acceptance. App/dispatcher wiring, default activation,
live operation, temporal behavior and whole Harbour One package acceptance
remain separate gates.

### Inactive JSON-RPC request runtime

`src/mcp_context_request_runtime.py` implements the inactive
`McpContextRequestRuntime` boundary. Its constructor borrows one authority,
confirmation service, producer, cursor authority and pack coordinator graph.
`handle(raw, identity=..., bearer_proof=...)` accepts bounded raw JSON-RPC
bytes and an authenticated identity. Only preparation accepts a bearer proof;
manifest, content and cancellation reuse the owned request. Preparation
returns a request ID and bounded confirmation metadata. Published pages retain
the coordinator's original immutable bytes.

The runtime owns its bindings, read leases and request records. Content EOF,
cancellation and `close()` release those records without closing the borrowed
graph. Its fixed lease lifetime is 300 seconds, with limits of 64 records
globally and eight per owner.

The corrected request-runtime source has passed independent static deep
review. A separately reviewed isolated cancellation/encoder-failure case passed
with exit zero, exact one-case JUnit and an empty owned job. The subsequent R4
full lane collected all 11 cases but exhausted its 1,800-second runtime budget
without a final JUnit result; its owned job is empty and the sealed sources and
observer are unchanged. Its five redacted progress markers are not acceptance
of five cases. Actual 11-case acceptance and the specific timeout cause remain
unverified. The earlier R3 budget failure and genuine slotted-producer
constructor failure/correction remain recorded. Another unchanged full run is
not justified by these receipts; a bounded causal decision precedes recovery.
The shared dependency lane passed all 108 cases and independent receipt review
at commit `68146dc5`; its exact three-file commit also passed direct Git byte
readback. Those results cover the dependency, not this new runtime.
The new module has no App, dispatcher or plugin wiring. Default activation,
live operation and Harbour One package acceptance remain separate gates.

The accepted native seams `read_native_forge_context_page` and
`read_native_knowledge_context_page` require an already-held authenticated
`DisclosureBinding` and corresponding immutable manifest; they grant no MCP
admission, consent or permit. The native cap is 16,384 bytes, with exact byte
forms `git_blob` for Forge and `knowledge_payload` for Knowledge. Forge's
`expected_item` is cookie-native, while `expected_manifest_digest` is the
raw64hex native target digest. Knowledge's `expected_item` is a
recipient-bound MCP item matching six native invariants, with boundary and
record hashes distinct from native; its `expected_manifest_digest` is the
cookie-native `sha256:`-prefixed target digest, and all selected Knowledge
entries remain in original order. Legacy Forge and Knowledge caps remain
1,000,000 and 1,048,576 bytes.

Combined snapshots sort `items` by canonical JSON, independently of the
selection order. Resolve a selected item by its full
`(source, selection_ref, revision_ref)` key within each snapshot. The
`target_manifest_digests` tuple retains selection order; do not zip it with the
sorted snapshot items or use a selection index as a snapshot index.

Cookie and MCP captures preserve the same source facts: source, selection and
revision references, payload hash, classification, and provenance reference.
Their binding hashes have source-specific meaning. Forge cookie metadata binds
owner, security revision and source state without chat identity. Knowledge
cookie boundaries include chat identity, while their record hash remains the
original source-record hash. For both sources, MCP metadata binds boundary and
record hashes to the recipient. Compare the intended binding domain as well as
the source facts; Knowledge cookie items from distinct chats cannot be assumed
equal, and cookie items cannot substitute for recipient-bound MCP items.

`src/mcp_context_cursor_authority.py` signs manifest (`m1`) and content (`c1`)
cursors with separate internally generated ephemeral keys. Verification binds
the current native lease, recipient, chat, process epoch, selection, snapshot,
pack, fixed page size and expiry before returning continuation coordinates.
Signed-wire validation alone grants no access; each use still requires native
authority verification. Closing or replacing the cursor authority invalidates
its old tokens. Dispatcher wiring and full-pack pagination remain separate work.

Successful native page publication consumes its permit and completion once,
then retains the same disclosed consent request for subsequent pages. Each new
permit repeats native context and persisted acknowledgement checks against the
original selection, snapshots and deadlines; pagination never renews consent.
The retained request still occupies its capacity slot. Target EOF alone does
not end a mixed-source pack: its coordinator must explicitly revoke consent
after the final selected target, cancellation or shutdown. Failed execution,
revocation and expiry remain terminal. Full-pack coordination remains pending.

Clock sampling and rollback detection share one lock. Genuine invalid clocks
or rollback invalidate registered requests; ordinary concurrent sampling must
not cause a false rollback. Retained Forge versions use an exact two-record Git
batch probe, with the original object-type and retained-ref checks as fallback
for every untrusted or failed probe.

Local package acceptance requires current candidate-bound native, capacity,
focused, and compatibility results plus independent review. Native 4 MiB,
ancestry/EOF, owner-eight, and global-64 checks do not establish production
bcrypt timing, temporal behavior, default activation, or live readiness. Those
remain separate gates. No deployment or credential change follows from a local
package PASS.

## Codex MCP Workbench

When the corresponding MCP services, plugins or connectors are available in
this Codex environment, set them up as a small verification workbench before
using them as roadmap evidence.

Preferred Codex-side services:

- local Odysseus MCP endpoint for `initialize`, `tools/list`, readiness and
  redacted audit checks
- Playwright/browser MCP for UI smoke evidence
- GitHub connector for PR, Actions and issue context
- documentation MCPs such as Context7 or OpenAI Docs for current technical docs
- optional Chrome DevTools MCP for browser diagnostics when Playwright evidence
  is not enough
- a narrow Podman read-only check path for service status, ports, health and
  logs

Do not enable broad filesystem, shell, Docker, generic remote-control or
`expose_all` MCP surfaces just because they are available. Non-bundled,
networked or write-capable MCP services need explicit operator approval before
installation or activation.

This Codex-side workbench is separate from the Odysseus HTTP endpoint. The
endpoint inventory is scope-specific: the Planning scope exposes the seven
Planning tools listed below, while the Harbour scope exposes only its four
Inbox tools. GitHub and notification tools are not added to either inventory.

## Harbour Inbox Client

Mint `mcp:harbour:inbox` only through the existing browser-admin
cookie-authenticated `POST /api/tokens` route, supplying the verified form
fields `name`, `scopes`, and the exact standalone scope
`mcp:harbour:inbox`. The route does not accept a bearer token. Store the
returned token directly in the MCP client secret store; never print, echo,
paste, or save it in shell history, logs, or this runbook. No additional UI
controls are assumed.

Use these JSON-RPC messages with the bearer token in the MCP client:

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}
```

```json
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
```

The result must contain exactly `harbour_inbox_list`,
`harbour_inbox_pause`, `harbour_inbox_reopen`, and `harbour_inbox_cancel`.

```json
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"harbour_inbox_list","arguments":{"limit":10}}}
```

The list response contains only the current administrator's owned, redacted
metadata and has `freshness: "current"`.

### Typed Inbox v3

Use `harbour_inbox_list` with `{"view":"typed"}` or
`{"view":"typed","limit":10}` for the closed typed projection. `limit` is
an integer from 1 through 100 and defaults to 25. The request remains under
the standalone `mcp:harbour:inbox` scope and adds no tool. The response schema
is `odysseus.harbour_inbox_unified_list.v3`.

```json
{"jsonrpc":"2.0","id":10,"method":"tools/call","params":{"name":"harbour_inbox_list","arguments":{"view":"typed","limit":10}}}
```

The v3 response is a redacted, read-only projection of four mandatory source
captures: `clarification`, `planning`, `knowledge`, and `document`. Each source
is captured and fresh-revalidated, including an empty source; an unavailable
or stale source is a fixed error. The envelope reports
`authority: "mcp_subject"`, `freshness: "captured_revalidated"`, and
`cross_source_atomic: false`. Items expose opaque `item_handle` values and
redacted metadata with `redaction_flags`; titles and content are not exposed.
Document native IDs, creator SIDs, sessions, provenance details, receipts, and
actions are not exposed; clarification retains the existing native identity
needed by its explicit action handlers.

Clarification items retain the existing explicit native action handlers; the
typed view itself is read-only. Planning and Knowledge remain read-only and
retain their exact native authority mapping. Ownership is the active
administrator's native SID: foreign or null creators and archived documents
are excluded, inactive owned records remain eligible, and real working-copy
records may have a null session. Planning and Knowledge receipt references
are emitted only when the existing native receipt is available and matches
the opaque item reference. Planning's four-key `receipt_ref` is nullable;
Knowledge's four-key `receipt_ref` is mandatory. A clarification receipt link
is optional and a document item's `receipt_ref` is always `null`. Receipt
lookup uses the exact offered reference with `{"view":"receipt","receipt_ref":...}`
and never fabricates a receipt, authority, launch, or consent result.

Each producer contributes at most 100 items, then the unified result applies a
300-item global ceiling and a 32,768-byte encoded response ceiling. Therefore
`count` may be below the requested producer limit or global ceiling. When any
producer or byte/global bound clips the result, `clipped` is `true` and
`complete` is `false`; otherwise those flags are truthful. The omitted `view`
continues to use the default v1 behavior, and `unified_receipts` remains the
existing v2 view (legacy receipt clipping rejects while the typed prefix is
supported).

For an actual owned clarification, replace `clar-owned-0001` with the actual
owned ID. The following uses valid illustrative values and an initial version
of `1`:

```json
{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"harbour_inbox_pause","arguments":{"clarification_id":"clar-owned-0001","expected_version":1,"idempotency_key":"demo-pause-0001"}}}
```

Replay the exact same operation, retaining both the original expected version
and key:

```json
{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"harbour_inbox_pause","arguments":{"clarification_id":"clar-owned-0001","expected_version":1,"idempotency_key":"demo-pause-0001"}}}
```

Read current state again. In this illustrative sequence the pause produces
version `2`; in operation, use the integer actually returned by the fresh
list:

```json
{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"harbour_inbox_list","arguments":{"limit":10}}}
```

```json
{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"harbour_inbox_reopen","arguments":{"clarification_id":"clar-owned-0001","expected_version":2,"idempotency_key":"demo-reopen-0001"}}}
```

Read current state again. In this illustrative sequence reopen produces
version `3`; again use the integer actually returned by the fresh list:

```json
{"jsonrpc":"2.0","id":8,"method":"tools/call","params":{"name":"harbour_inbox_list","arguments":{"limit":10}}}
```

```json
{"jsonrpc":"2.0","id":9,"method":"tools/call","params":{"name":"harbour_inbox_cancel","arguments":{"clarification_id":"clar-owned-0001","expected_version":3,"idempotency_key":"demo-cancel-0001"}}}
```

Each action returns a closed committed-result response whose `item.freshness`
is `"committed_receipt"`; `idempotent_replay` is top-level. The
`item.event_receipt_present` flag only indicates that an event receipt is
present; it does not prove a verified receipt link. Replaying the same pause
key returns the original committed result and sets top-level
`idempotent_replay: true`, even after a later operation changes the run. Use a
fresh list for current state. Every changed operation requires its own
idempotency key.

Only action notifications and action-containing batches are rejected before
service dispatch. Ordinary non-action notifications retain normal JSON-RPC
behavior. Send each action as one identified `tools/call`.

The token is checked against the running process's configured `AuthManager`.
One process has one authority manager. Restart alone preserves valid
persisted token authority after revalidation; revoked, deleted, hash-mismatched,
or stale-subject tokens remain denied after restart. A same-SID rename
preserves identity after convergence, while in-flight stale owner/alias pairs
fail closed. Deleting and recreating the same alias creates a new SID and never
inherits the old token authority.

### Raw-upload ownership prerequisite

Commit `2a33dc66` accepts the new-upload SID capture prerequisite with 85 local
PASS results and independent deep review. Authenticated browser-cookie uploads
bind new private index entries to the current immutable user subject while the
principal guard remains held through file/index commit. Public upload responses
do not disclose that subject. Local uploads without that cookie binding remain
outside the trusted Inbox source.

Deduplication separates immutable subjects and historical entries. Renaming an
owner retains its subject binding; deleting and recreating the same alias does
not transfer old uploads. Historical or null-subject entries are excluded from
the trusted Inbox model until a separate data decision authorizes assignment.
This follows the human decision to bind new uploads and initially exclude history.

The subsequent opt-in typed upload projection is locally accepted at commit
`81a22af3`. Its independent source review and actual isolated runtime lane passed:
187 unique cases, zero failures/errors/skips, all frozen inputs unchanged and
the owned process job empty. Independent receipt acceptance, exact four-file
commit and direct Git byte readback also passed. This component acceptance
does not activate the plugin or satisfy live, cross-process recovery, UI or
temporal gates. If removal of a newly created file is refused, the upload
returns a fixed cleanup failure; physical cleanup and operational recovery
are not claimed.

The opt-in App composition is separately accepted at commit `13d645b7`.
Only an exact enabled `allow_upload_inbox_reads` flag composes the existing
native upload handler into typed Inbox MCP reads. Disabled or absent handlers
retain the four-source behavior; configured invalid handlers fail closed.
Authentication and handler identity are checked again before response egress.
The three actual application cases passed, including restart, revocation and
upload-source composition. Independent evidence review accepted their original
JUnit and owned-job cleanup without repeating runtime: the controller's
original accounting failure remains preserved, while reconciliation accounts
for 655 generated Python caches against 3,834 unchanged sealed source files.
The exact two-file commit and separate Root Git byte readback passed. This
closes that isolated App component, not whole Harbour One 09, the context
request transport, default activation or the remaining package and live gates.

## Local Smoke

Use placeholders only; never paste token values into docs or logs.

For the homeserver, the scripted activation entrypoint is:

```bash
ops/homeserver/activate-mcp-server.sh --execute
```

Run it first without `--execute` to review the planned remote/ref, backup gate,
fast-forward update, restart, enable, and smoke commands.

1. Confirm the plugin is visible in Settings -> Plugins.
2. Open `/api/plugins/mcp/app`.
3. Sign in as an active administrator and enable both the MCP server and
   Planning read gates at `/api/plugins/mcp/config`. This cookie-authenticated
   admin route does not accept an API bearer token:

```bash
curl -fsS -X POST http://127.0.0.1:7000/api/plugins/mcp/config \
  -b admin-cookie.txt -H "Origin: http://127.0.0.1:7000" \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "allow_planning_reads": true}'
```

Create a dedicated token in the admin token page with only the
`mcp:planning:read` scope. Copy its value directly into the MCP client's
secret store; do not put it in shell history or this runbook.

4. Initialize with that token:

```bash
curl -fsS http://127.0.0.1:7000/api/plugins/mcp \
  -H "Authorization: Bearer <MCP_PLANNING_READ_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}'
```

5. List tools and confirm high-risk tools are absent:

```bash
curl -fsS http://127.0.0.1:7000/api/plugins/mcp \
  -H "Authorization: Bearer <MCP_PLANNING_READ_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
```

Confirm the list contains only these Planning read tools:

- `planning_list_roadmaps`
- `planning_read_roadmap`
- `planning_search_roadmaps`
- `planning_get_context_pack`
- `planning_graph_summary`
- `planning_read_gate_definitions`
- `planning_create_agent_handoff`

The first five are bounded repository projections with `source_of_truth: false`.
The last two require accepted durable Planning definitions. A generated handoff
is read-only and never authorizes launch or execution.

The authenticated dispatcher loads Inbox-specific error and outcome types only
within guarded Inbox paths. Planning requests do not require the Inbox module;
missing Inbox dependencies yield a fixed error response rather than an import
exception. Timeouts and cancellation still join the worker before returning.
The Planning and Inbox dispatch tests remain separate families with all existing
assertions retained; this does not grant product or live acceptance.

The isolated process wrapper preserves a valid unavailable lens as an empty,
incomplete `graph_summary.v1` result with `available=false`; it does not invent
an active node. The unavailable producer shape must have empty nodes and edges,
`incomplete=true`, `clipped=false`, and no `active_node_id`. Malformed shapes
remain `child_failed`. This does not establish App/MCP integration readiness.

`planning_graph_summary` uses depth 0 for the roadmap node alone, depth 1 for
direct slice/gate nodes and `contains`/`has_gate` edges, and depths 2 or 3 for
known `depends_on`/`blocks` edges between selected nodes. Depth 3 does not
recursively discover additional nodes. The projection is limited to 100 nodes
and 200 edges; a smaller requested node limit can omit relation endpoints and
their edges. Source references remain bounded and raw roadmap content is
excluded. These repository changes have focused test and static-review evidence;
external client and production activation checks remain separate gates.

Generic or high-risk tools are absent, including:

- `bash`
- `python`
- `write_file`
- `edit_file`
- `app_api`
- `api_call`
- `send_email`
- `manage_tokens`
- `manage_settings`
- `manage_mcp`
- `manage_github_issues`
- `github_issue_create_triaged`
- raw or generic GitHub passthrough tools
- `odysseus_call`

6. Call one listed Planning tool, for example:

```bash
curl -fsS http://127.0.0.1:7000/api/plugins/mcp \
  -H "Authorization: Bearer <MCP_PLANNING_READ_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"planning_list_roadmaps","arguments":{"limit":10}}}'
```

Each result uses the fixed `odysseus.mcp.tool_result.v1` envelope. Requests are
bounded to 256 KiB, batches of 8, depth 16, 4096 JSON nodes, 32 KiB arguments,
64 KiB per result, 256 KiB aggregate output and a 10 second normal budget.
Repository adapter results are reconstructed from closed typed Planning
projection fields; unexpected fields, absolute paths, raw previews and
truncated raw content are rejected. The `planning_read_roadmap` HTTP tool only
accepts `include_raw_preview_chars: 0` (or an omitted value). Server and
Planning-read gates are rechecked for every HTTP message and around each tool
call, so disabling either gate stops an in-flight batch before later calls.
Synchronous repository I/O that blocks beyond the budget has no strict
cancellation guarantee.

7. Readiness is available only through the local admin/plugin UI; it is not an
HTTP MCP resource method:

```bash
curl -fsS http://127.0.0.1:7000/api/plugins/mcp/info \
  -b admin-cookie.txt
```

## Claude Code Example

Use the dedicated `mcp:planning:read` token for this client.

```bash
claude mcp add --transport http odysseus \
  http://127.0.0.1:7000/api/plugins/mcp \
  --header "Authorization: Bearer <MCP_PLANNING_READ_TOKEN>"
```

## Production Gates

Before LAN, tunnel, or reverse-proxy exposure:

- dedicated exact-scope Planning read token exists for the active admin
- token value is not stored in docs, shell history, or logs
- high-risk tools remain absent from `tools/list`
- `odysseus_call` remains absent unless a later positive endpoint allowlist exists
- accepted durable provenance is required for gate-definition and handoff reads
- redacted audit log is being written to the plugin data dir
- Podman/runtime checks remain read-only; restart/recreate/redeploy stays a
  separate gated action
- rollback command is known
- Cloudflare Tunnel exposure has a separate operator approval

## Rollback

Disable the runtime gate:

```bash
curl -fsS -X POST http://127.0.0.1:7000/api/plugins/mcp/config \
  -b admin-cookie.txt -H "Origin: http://127.0.0.1:7000" \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'
```

If needed, disable the plugin in Settings -> Plugins or remove the plugin folder and restart Odysseus.

## No-Go Conditions

- Any token, chat target, password, or provider secret appears in logs/docs.
- `bash`, `python`, raw file write, destructive email, settings, token, or plugin-management tools appear in default `tools/list`.
- `odysseus_call` is enabled without a dedicated endpoint allowlist.
- Remote exposure is requested before local smoke and audit are verified.
