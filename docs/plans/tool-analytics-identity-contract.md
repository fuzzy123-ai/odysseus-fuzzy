# Tool Analytics Identity Contract v1

Status: repo contract implemented; productive capture and UI activation remain off.

Contract ID: `odysseus.tool_analytics_identity.v1`

## Purpose

TAX is the only authority for the three tool identity fields consumed by TUA:
`tool_analytics_id`, `tool_family`, and `tool_source`. Consumers call
`build_tool_analytics_identity_contract()` or
`resolve_tool_analytics_identity()` from `src.builtin_tool_catalog`; they do
not maintain a second alias or family map.

## Known and historical identities

A canonical built-in name resolves to its Descriptor-v2 `analytics_id`,
`family`, and `source`. Historical names resolve through the versioned alias
map. In v1, `manage_rag` maps to `manage_personal_docs`. Both inputs therefore
produce the same event fields and cannot create two usage series.

The resolution packet may state that an alias was applied and name the safe
canonical tool ID. It never returns the raw input separately. Historical
aliases cannot become canonical IDs, and `retired_analytics_ids` are rejected
if a later catalog attempts to reuse them for another capability.

## Unreviewed dynamic identities

An unreviewed runtime name is never copied or hashed into persistent identity.
It maps only to one bounded source bucket:

| Source | Analytics ID | Family |
| --- | --- | --- |
| Plugin | `dynamic.plugin.unclassified` | `unclassified_dynamic` |
| MCP | `dynamic.mcp.unclassified` | `unclassified_dynamic` |
| Provider | `dynamic.provider.unclassified` | `unclassified_dynamic` |
| Legacy/unknown | `legacy.unclassified` | `unclassified_dynamic` |

This deliberately sacrifices per-tool statistics for unreviewed sources. A
source becomes individually measurable only after it receives a reviewed TAX
descriptor in the contract catalog.

## Privacy boundary

The identity contract accepts no owner, session, run, correlation, argument,
result, prompt, path, provider account, or content field. Its event adapter
returns exactly the three TAX-owned fields. Runtime tool names and dynamic
source IDs are absent from fallback packets, so names containing account or
session fragments cannot become analytics dimensions.

TUA continues to own pseudonymous event references, persistence gating,
retention, aggregation, and capture activation. This contract neither enables
capture nor writes events.
