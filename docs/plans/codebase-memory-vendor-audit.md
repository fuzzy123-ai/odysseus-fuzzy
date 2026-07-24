# Codebase Memory Vendor Audit

Stand: 2026-07-18

Status: `CBM-00 contract-only pin / no artifact acquired / engine disabled`

## Decision

Odysseus pins `codebase-memory-mcp` release `v0.9.0` at commit
`b637e3330c96cfe452da623db068c241aaa3ec01` for contract and later sandbox
evaluation only. The pin is not an installation decision, not an endorsement of
upstream performance claims and not permission to index a repository.

The release page marks `v0.9.0` as the latest immutable release observed on
2026-07-18 and links the verified commit. Its release checksum manifest has
SHA-256 `b7294616f22050124c8f2cf029cc9943e0b7d6e426fb9a0b95b1de9815c76e57`.
Neither the manifest nor an executable was downloaded in CBM-00.

Primary evidence:

- [v0.9.0 release](https://github.com/DeusData/codebase-memory-mcp/releases/tag/v0.9.0)
- [pinned commit](https://github.com/DeusData/codebase-memory-mcp/commit/b637e3330c96cfe452da623db068c241aaa3ec01)
- [tag tree](https://github.com/DeusData/codebase-memory-mcp/tree/v0.9.0)
- [MIT license at the tag](https://github.com/DeusData/codebase-memory-mcp/blob/v0.9.0/LICENSE)
- [security policy at the tag](https://github.com/DeusData/codebase-memory-mcp/blob/v0.9.0/SECURITY.md)
- [upstream README](https://github.com/DeusData/codebase-memory-mcp)

## License And Provenance

The repository license is MIT with copyright attributed to DeusData. An
evaluation or distribution must retain the copyright and permission notice.
This is a technical compatibility observation, not legal advice.

Upstream publishes checksums, Sigstore bundles, GitHub attestations, antivirus
receipts and an SBOM for releases. Those are useful inputs but not substitutes
for verifying the exact artifact locally. A future evaluation must select one
headless artifact or one pinned source build, verify all applicable receipts,
review `THIRD_PARTY_NOTICES.md` and the SBOM, and record the result without
committing an executable.

Source build is preferred for auditability. Upstream declares C/C++ compilers,
zlib development headers and Git as prerequisites and vendors the parsing and
storage implementation into the resulting binary. CBM-00 did not clone, build,
install or execute anything.

## Capability Freeze

The frozen public documentation advertises structural parsing, persistent
SQLite graphs, incremental indexing, call/import/route/data-flow relations,
read-only Cypher queries, code and semantic search, impact analysis, an optional
3D graph UI and 15 MCP tools.

The README tool table names these 14 tools:

1. `index_repository`
2. `list_projects`
3. `delete_project`
4. `index_status`
5. `search_graph`
6. `trace_path`
7. `detect_changes`
8. `query_graph`
9. `get_graph_schema`
10. `get_code_snippet`
11. `get_architecture`
12. `search_code`
13. `manage_adr`
14. `ingest_traces`

The same README also names `check_index_coverage` and `semantic_query` outside
that table while continuing to advertise 15 tools. Therefore the exact runtime
surface is deliberately marked unresolved. No direct upstream tool is exposed
through Odysseus until a pinned-artifact protocol probe freezes the actual list.
The four clearly stateful tools (`index_repository`, `delete_project`,
`manage_adr`, `ingest_traces`) stay especially blocked.

The paper baseline is release `v0.5.5`; the locked engine is `v0.9.0`. Newer
language, semantic, cross-repository, Windows, resilience and UI behavior is
unvalidated by the paper and must pass the Odysseus three-way evaluation.

## Runtime And Data Surfaces

| Surface | Upstream behavior | Odysseus CBM-00 decision |
| --- | --- | --- |
| MCP | stdio server | no process and no public tools |
| Graph UI | optional embedded UI on loopback port 9749 | UI binary forbidden |
| Store | per-project SQLite under a user cache directory | only a temporary evaluation directory later |
| Watcher | background Git-based watcher; `auto_watch` documented true by default | forced off |
| Auto-index | optional indexing on MCP session start | forced off |
| Update check | background HTTPS request to GitHub after MCP initialize | egress denied; unresolved disable switch blocks runtime acceptance |
| Project files | `.codebase-memory.json` and `.cbmignore` may affect behavior | no project writes |
| Shared graph | optional `.codebase-memory/graph.db.zst` plus `.gitattributes` rule | import/export forbidden |
| Diagnostics | optional rotating files in the OS temp directory | disabled |
| Agent integration | installer can edit MCP, instruction, skill and hook surfaces | installer and `install` command forbidden |

The update request documented by upstream targets
`https://api.github.com/repos/DeusData/codebase-memory-mcp/releases/latest`.
Upstream describes it as content-free and best-effort, but it is still network
egress. A future adapter must either discover a real disable mechanism or prove
the process remains contained in an egress-denied sandbox.

## Threat-Oriented Controls

- `CBM-R01 critical`: never run installer/setup commands. Odysseus later passes
  an explicit executable path to an isolated child process.
- `CBM-R02 high`: deny egress and verify zero successful network calls before
  any engine acceptance.
- `CBM-R03 high`: require one resolved allowed root and an explicit selected
  repository; keep watcher and auto-index off.
- `CBM-R04 high`: disable shared-graph import/export so CBM cannot modify source
  truth or `.gitattributes`.
- `CBM-R05 medium`: resolve the documentation version/tool mismatch with an
  exact version/capability/protocol probe.
- `CBM-R06 medium`: re-test all behavior beyond paper release `v0.5.5`.
- `CBM-R07 medium`: review the exact release SBOM, license notices and optional
  bundled embedding model before execution.

All engine, process, productive indexing, auto-index, watcher, UI, update,
network, installer, agent-config, hook, instruction, direct-MCP, semantic-model
and shared-export flags remain false in the machine-readable lock.

## Evaluation And Rollback Boundary

A future local evaluation still needs separate execution/dependency approval.
It may use only a content-free synthetic fixture, a temporary contained data
directory, a headless artifact, explicit environment variables and an
egress-denied process. It may not touch this repository's hooks, MCP config,
agent instructions, source files or project registration.

Rollback stops the isolated process, path-checks and removes only the temporary
evaluation directory, retains a content-free failure receipt and falls back to
USI lexical retrieval plus existing exact readers. No user configuration needs
restoration because config mutation is prohibited from the start.

`CBM-LIVE-ACTIVATION` remains dormant and is the only future gate for a
productive process or projection.
