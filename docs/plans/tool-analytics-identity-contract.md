# Tool Analytics Identity Contract

Status: repository contract implemented by TAX10; analytics capture and backfill remain disabled

Schema: `odysseus.tool_analytics_identity.v1`

## Boundary

This is the only public tool-identity projection for TUA consumers. It is
derived from the existing `ToolDescriptorV2Index`; it is not a registry and
does not discover, register, enable or execute tools. TUA must resolve a
runtime tool name through this contract and persist only the returned
`analytics_id`, `family` and `source` values.

## Public Identity

Each canonical descriptor produces exactly one immutable identity with these
fields:

| Field | Rule |
| --- | --- |
| `schema_version` | Constant `odysseus.tool_analytics_identity.v1`. |
| `tool_id` | Canonical technical runtime identifier. |
| `analytics_id` | Stable lowercase counting key. |
| `family` | Controlled `ToolFamily` value from TAX. |
| `source` | Controlled `ToolSource` value from TAX. |
| `aliases` | Historical technical names that resolve to this identity. |
| `retired` | Bounded deprecation state; it never releases the identity. |

Aliases are resolution edges, not additional identities. Resolving a
canonical `tool_id` or any of its aliases returns the same identity and the
same `analytics_id`; alias use therefore cannot create a second catalog count.
Unknown names return no identity and TUA must fail closed instead of inventing
a name. Every next contract version must carry the previous alias-target
snapshot forward. A historical alias cannot be removed, reassigned or promoted
to a new canonical ID; its retired canonical identity remains as a tombstone.

## Source Classification

Built-ins, Plugins, MCP tools, Providers and Legacy descriptors retain their
controlled TAX source enum. A dynamic tool without reviewed metadata uses
`source=dynamic` and `family=unclassified_dynamic`. The identity has no
`source_id`, owner label, installation label, provider name or free-form
metadata, so an unknown dynamic source cannot introduce personal data.

## Permanent Reservations

Every published identity reserves `analytics_id -> tool_id` from its first
contract version onward. The contract exports the sorted reservation snapshot.
The next version must pass the previous reservation and alias snapshots back
when projecting the catalog. Reusing a reserved `analytics_id` for another `tool_id` fails closed,
including after the original descriptor is deprecated or removed. Retirement
never deletes a reservation, and aliases never own separate reservations.

The reservation ledger contains technical IDs only. It is not an event store,
usage counter or mutable analytics database.

## Privacy And Consumer Rules

The public projection contains no display text, description, schema, handler,
prompt, callable, arguments, results or arbitrary metadata. It also contains
no owner, session, run, correlation, document, memory, mail, calendar, contact,
path, URL, hostname, provider payload, token or secret value.

TUA may consume only:

- the versioned identity returned by canonical/alias resolution;
- its `analytics_id`, `family` and `source` fields;
- the reservation snapshot for anti-recycling validation.

TUA must not infer identity from raw runtime names, create a parallel alias
table or treat `manage_rag` as a runtime capability. TAX9 keeps that stale UI
identifier quarantined unless a later evidence-backed alias points to a real
canonical capability.

## Deferred Activation

TAX10 performs no event capture, database/settings mutation, retention job,
legacy read, backfill, API/UI projection, metric export or feature activation.
Those actions remain owned by their TUA slices and the dormant action-specific
live gate.
