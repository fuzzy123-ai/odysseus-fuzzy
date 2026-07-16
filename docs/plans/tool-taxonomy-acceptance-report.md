# Tool Taxonomy Acceptance Report

Status: PASSED

Scope: repository-only TAX11 integration barrier

## Test Evidence

| Matrix | Result | Status |
| --- | ---: | --- |
| Required TAX11 acceptance matrix | 99 passed | passed |
| Direct API, Admin, Dynamic, MCP, default and analytics projections | 54 passed | passed |
| Admin JavaScript syntax | 1 check | passed |
| Independent TAX inventory regression `REG-20260716-014` | 20 passed, 1 snapshot check | fixed |

One SQLAlchemy deprecation warning is pre-existing and does not change the
acceptance result.

## Aggregate Parity

| Contract | Count | Status |
| --- | ---: | --- |
| Canonical built-ins | 84 | passed |
| Runtime tags | 78 | passed |
| Native function schemas | 83 | passed |
| Searchable index entries | 84 | passed |
| Dedicated prompt sections | 68 | passed |
| Agent handlers | 27 | passed |
| Dispatcher condition IDs | 80 | passed |
| Canonical Admin catalog entries | 84 | passed |
| Runtime IDs missing from Admin catalog | 0 | passed |
| Stale Admin IDs | 0 | passed |
| Analytics identities | 84 | passed |
| Analytics ID reservations | 84 | passed |

The 48 previous generic Admin fallbacks are closed: current missing coverage is
zero. The stale `manage_rag` presentation entry is absent.

## Registration Gap Disposition

| IDs | Count | Status |
| --- | ---: | --- |
| `manage_embeddings`, `manage_personal_docs`, `manage_plugins` | 3 | confirmed route only; blocked catalog identity |
| `manage_assistant`, `manage_presets` | 2 | deferred; default off |
| `tail_serve_output` | 1 | security blocked; Admin only |

All six identities remain explicit. None is silently activated.

## Policy Matrix

| Class | Count | Status |
| --- | ---: | --- |
| Read | 23 | passed |
| Local write | 10 | confirmation required |
| External write | 9 | confirmation required; default off |
| Destructive | 1 | confirmation required; default off |
| Control | 41 | confirmation required |
| Owner permission | 74 | passed |
| Admin permission | 10 | passed |
| Deferred defaults | 14 | disabled |

## Integration Status

| Boundary | Status |
| --- | --- |
| Catalog, tag, schema, index, prompt, handler and dispatcher parity | passed |
| Static parser aliases and canonical fenced-tool catalog wiring | passed |
| Descriptor-V2 API and Admin family/state projection | passed |
| Analytics identity, alias deduplication and anti-recycling reservations | passed |
| Dynamic registry replacement, unregister and stale-generation regression | passed |
| Lightweight import and startup boundary | passed |
| Deterministic projection size and local build budget | passed |
| Content-free aggregate report boundary | passed |

Feature activation, analytics capture, backfill, deployment and live validation
remain disabled or not applicable at this repository-only barrier.
