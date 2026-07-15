# Tool Descriptor V2 Contract

Status: repository contract, implemented by TAX1, not product-activated
Schema: `odysseus.tool_descriptor.v2`

## Purpose

Descriptor V2 gives built-in, plugin, MCP, provider, legacy, and dynamic tools
one deterministic identity and policy vocabulary. It does not register a tool,
grant a permission, expose a schema to a model, or enable the Catalog V2 read
path. Runtime projections and activation remain later TAX slices.

## Fields

Every descriptor is an immutable value with these fields:

| Field | Contract |
| --- | --- |
| `schema_version` | Exactly `odysseus.tool_descriptor.v2`. |
| `tool_id` | Canonical runtime ID; safely normalized and unique in an index. |
| `analytics_id` | Pre-normalized lowercase hyphenated slug; immutable and unique in an index. Aliases resolve to this same identity. |
| `display_name`, `description` | Static human-facing text. Neither is included in audit serialization. |
| `family` | One controlled `ToolFamily` value. |
| `source` | One controlled `ToolSource` value. |
| `lifecycle` | One controlled `ToolLifecycle` value. |
| `availability` | One controlled `ToolAvailability` value. |
| `default_enabled` | Strict boolean. Deferred, experimental, deprecated, blocked, unavailable, and hidden tools cannot start enabled. |
| `default_visibility` | Existing controlled `ToolVisibility` value, independent of registration and availability. |
| `risk_level` | Existing controlled `ToolRiskLevel` value. |
| `permission` | One controlled `ToolPermission` value. This metadata never bypasses runtime enforcement. |
| `effect_class` | One controlled `ToolEffectClass` value. External-write and destructive effects require confirmation. |
| `requires_confirmation` | Strict boolean. Dangerous tools also require confirmation. |
| `schema_ref`, `handler_ref`, `prompt_ref` | Optional static projection references. TAX2 validates catalog-wide projection completeness and uniqueness. |
| `aliases` | Sorted canonical runtime IDs; cannot repeat their own canonical ID or collide anywhere in a descriptor index. |
| `feature_flag` | Optional static rollout reference. Its presence does not activate the feature. |
| `introduced_in`, `deprecated_in` | Static lifecycle history. `deprecated_in` is required only for deprecated descriptors. |

## Controlled Values

Families:

`code_filesystem`, `search_web`, `knowledge_memory`, `documents_media`,
`model_ops`, `projects_repositories`, `orchestration_sessions`,
`planning_communication`, `admin_system`, `plugins_mcp`,
`external_providers`, `experimental`, `unclassified_dynamic`.

Sources:

`builtin`, `plugin`, `mcp`, `provider`, `legacy`, `dynamic`.

Lifecycle:

`active`, `contextual`, `deferred`, `experimental`, `deprecated`, `blocked`.

Availability:

`available`, `unavailable`, `unconfigured`, `disabled`, `blocked`, `unknown`.

Effects:

`read`, `local_write`, `external_write`, `destructive`, `control`.

Permissions:

`public`, `owner`, `admin`, `system`.

Risk and visibility retain their existing controlled values:

- risk: `safe`, `elevated`, `dangerous`;
- visibility: `visible`, `hidden`, `blocked`, `requires_approval`, `unavailable`.

No free-form fallback such as `Other` is valid. An unreviewed dynamic tool uses
`unclassified_dynamic` and the conservative contract below.

## Fail-Closed Policy

- Unknown enum values, unsafe IDs, non-boolean policy flags, and self-aliases
  are rejected.
- An enabled descriptor must be available, visible or approval-gated, and have
  an active or contextual lifecycle.
- Dangerous, external-write, and destructive descriptors require confirmation.
- Deprecated descriptors require lifecycle metadata and cannot return to an
  active lifecycle. Blocked descriptors cannot leave blocked.
- An index rejects duplicate canonical IDs, analytics IDs, aliases, and aliases
  that shadow a canonical ID.
- The immutable descriptor and collision checks prevent an alias from creating
  a second analytics identity.

## Dynamic And Legacy Reads

An unknown dynamic tool is represented as `unclassified_dynamic`, source
`dynamic`, lifecycle `blocked`, availability `unknown`, hidden, default-off,
elevated risk, admin-only, control effect, and confirmation-required. This is a
diagnostic state, not an activation route.

A V1 `ToolManifest` is deterministically readable as V2. Its legacy family,
capabilities, visibility, risk, and schema reference map to controlled values;
the result remains default-off and uses source `legacy`. This is a compatibility
read only. It does not mutate V1 data or enable Catalog V2.

## Audit Serialization

`audit_summary()` contains only stable IDs, controlled policy values, static
references, lifecycle metadata, and aggregate flags. It omits display text and
descriptions and explicitly reports:

- `raw_content_visible=false`;
- `callable_visible=false`;
- `tool_arguments_visible=false`;
- `tool_results_visible=false`;
- `secret_values_visible=false`.

No callables, raw schemas, arguments, results, prompts, tokens, credentials,
provider responses, private paths, or document contents belong in the
descriptor audit contract.

## Activation Boundary

TAX1 defines and tests a repository-only contract. Feature activation, runtime
projection changes, capture, backfill, provider writes, deployment, and service
restart remain disabled and out of scope.
