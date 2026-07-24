# Tool Descriptor V2 Contract

Status: repository contract implemented for TAX1 on 2026-07-17; productive
catalog projection and activation remain disabled until their later slices.

Contract ID: `odysseus.tool_descriptor.v2`

## Purpose

`ToolDescriptorV2` in `src/tool_catalog.py` is the normalization contract for
built-in, plugin, MCP, provider and legacy tool identities. It does not replace
the dynamic adapter in `src/tool_registry.py`, execute tools, discover live MCP
servers or activate any catalog projection.

The contract keeps registration, technical availability, default enablement,
UI/prompt visibility and per-call confirmation separate. A registered tool can
therefore remain unavailable, disabled or hidden without being represented as a
second identity.

## Controlled Values

| Field | Values |
| --- | --- |
| `family` | `code_filesystem`, `search_web`, `knowledge_memory`, `documents_media`, `model_ops`, `projects_repositories`, `orchestration_sessions`, `planning_communication`, `admin_system`, `plugins_mcp`, `external_providers`, `experimental`, internal fail-closed `unclassified_dynamic` |
| `source` | `builtin`, `plugin`, `mcp`, `provider`, `legacy` |
| `lifecycle` | `active`, `contextual`, `deferred`, `experimental`, `deprecated`, `blocked` |
| `availability` | `available`, `unavailable`, `not_configured`, `disabled`, `degraded` |
| `risk_level` | `safe`, `elevated`, `dangerous` |
| `permission` | `public`, `user`, `owner`, `admin`, `system` |
| `effect_class` | `read`, `local_write`, `external_write`, `destructive`, `control` |
| `default_visibility` | `visible`, `hidden`, `blocked`, `requires_approval`, `unavailable` |

No `Other`, arbitrary family, guessed permission or unknown risk string is
accepted. Plugin, MCP and provider descriptors require a redacted stable
`source_id`; built-ins cannot persist one.

## Identity And Alias Invariants

- `tool_id`, `analytics_id` and aliases are exact lowercase technical IDs. The
  normalizer rejects unsafe or silently changed values.
- A frozen descriptor never rewrites `analytics_id` during lifecycle changes.
- `ToolDescriptorCatalogV2` rejects duplicate canonical IDs, duplicate
  analytics IDs, aliases shared by two descriptors and aliases colliding with
  any canonical ID.
- Alias lookup resolves directly to one canonical descriptor. Alias chains and
  cycles cannot enter the catalog.
- Versions use a machine-readable `major.minor[.patch|.x]` form.

## Availability, Lifecycle And Effects

- Non-available descriptors carry a content-free reason code and default
  disabled. Available descriptors carry no unavailable reason.
- `deferred`, `experimental`, `deprecated` and `blocked` default disabled.
- Deferred, experimental and deprecated descriptors cannot default visible;
  blocked descriptors use `blocked` or `unavailable` visibility.
- External-write and destructive effects always require per-call confirmation.
- Active and contextual descriptors require a prompt/index reference.
- Native Function tools require one `schema_ref`. A non-native projection has
  no schema reference and must name a content-free exception reason.
- Lifecycle transitions use the allowlist in `src/tool_catalog.py`.
  `deprecated -> blocked` is terminal; a blocked identity cannot silently
  return to active.

These descriptor checks complement, but never replace, runtime owner, role,
policy and confirmation enforcement.

## Conservative Dynamic Default

`ToolDescriptorV2.conservative_dynamic(...)` creates an unclassified plugin,
MCP or provider descriptor as:

- `family=unclassified_dynamic`;
- `lifecycle=experimental` and `availability=unavailable`;
- default disabled and unavailable in projections;
- dangerous, admin-only and confirmation-required.

Classification and activation therefore require an explicit later catalog
decision. Discovery alone grants no execution authority.

## V1 Migration

`ToolDescriptorV2.from_v1_manifest(...)` and
`ToolDescriptorCatalogV2.from_v1_manifests(...)` provide a deterministic,
side-effect-free read path for existing `ToolManifest` objects or their compact
mappings. The adapter:

- maps every known v1 family to a controlled v2 family and rejects unknown
  families;
- preserves the tool ID as its initial analytics ID;
- derives effect and conservative permission from v1 capability/risk metadata;
- keeps v1 visibility distinct from lifecycle and enablement;
- binds only content-free schema, handler and prompt references.

The adapter does not import runtime registries, inspect credentials or enable a
tool. TAX2 owns the later canonical built-in dataset and projections.

## Safe Audit Serialization

`audit_dict()` and `audit_json()` are deterministic and expose only normalized
descriptor metadata. They explicitly emit:

```json
{
  "arguments_visible": false,
  "callable_visible": false,
  "raw_content_visible": false,
  "secret_value_visible": false
}
```

Descriptor text rejects callable values, secret-assignment patterns and private
host paths. References accept only bounded content-free identifiers; no schema
arguments, tool results, provider output, credentials or raw prompts are
serialized.
