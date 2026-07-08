# Memory Legacy Naming Migration Map

Date: 2026-07-06

Status: MEM6 docs-only migration map

## Goal

Map current Obsidian, RAPTOR, RAG, ORCA/Lens and Universal Inbox vocabulary to
the canonical Memory/RaptorGraph lifecycle terms without removing compatibility
surfaces or implying live migration.

## Scope

This is a safe_offline documentation slice. It does not rename routes, tools,
plugins, data directories, environment variables, memories, graph events,
collections or user-facing UI text. Removal or broad wording changes remain
behind `MEM-LEGACY-REMOVAL-GO`.

## Canonical Terms

| Canonical term | Meaning | First new contract |
| --- | --- | --- |
| Memory lifecycle | End-to-end state from source metadata through rebuild dry-run. | `docs/plans/memory-lifecycle-contract.md` |
| Memory write intent | Durable pre-write boundary for derived memories. | `src/universal_inbox_memory_write_intent.py` |
| Memory record | Bounded memory item planned or written after policy allows it. | `src/memory_lifecycle.py` |
| Provenance event | Redacted explanation for why a memory or graph event exists. | `src/memory_provenance_ledger.py` |
| Graph event | RaptorGraph/ORCA/Lens mutation or candidate evidence. | `src/memory_provenance_alignment.py` |
| Diagnostics budget | Readiness, bounded query, freshness, graph and rebuild evidence. | `src/memory_diagnostics_consolidation.py` |
| Rebuild dry-run | Read-only preview for reindex/rebuild/migration. | `src/rag_reindex_dry_run.py` |

## Legacy-To-Canonical Map

| Current / legacy surface | Canonical term | Status | Migration rule |
| --- | --- | --- | --- |
| `plugins/obsidian` plugin | ORCA Local Markdown Vault compatibility surface | keep | Keep `obsidian_*` routes/tools until explicit compatibility-period decision. |
| `/api/plugins/obsidian/*` routes | ORCA/Memory vault routes | keep | New ORCA route aliases must be additive and separately tested. |
| `obsidian_*` tools | ORCA tool aliases plus legacy compatibility tools | keep | Existing tools stay authoritative until `orca_*` aliases pass focused tests. |
| `orca_*` tool aliases | ORCA canonical aliases | additive | Treat as aliases, not replacements, until removal Go. |
| Obsidian vault | Local Markdown Vault | compatibility wording | Use "Local Markdown Vault" in new docs when not naming the plugin. |
| Save-to-Obsidian | Memory review export/apply | compatibility wording | New contracts should say export/apply; UI may keep legacy wording. |
| `obsidian_memory_review_*` | Memory review candidate/apply flow | keep | Do not remove until memory review UI and route aliases are proven. |
| `obsidian_memory_capture_*` | Memory capture candidate/apply flow | keep | Keep preview/apply confirmation semantics unchanged. |
| RAPTOR / `raptor_*` | RaptorGraph graph event and diagnostics | compatibility wording | New backend contracts should prefer `raptorgraph_*`; legacy tools stay. |
| `obsidian_raptor_status` | RaptorGraph diagnostics status | keep | Feed future status into diagnostics consolidation rather than inventing a second summary. |
| `obsidian_raptor_graph_view` | bounded graph event view | keep | Maintain bounded cursor/query semantics. |
| `obsidian_raptor_rebuild` | graph rebuild dry-run/live gated rebuild | keep gated | Live rebuild remains behind explicit feature flags and `MEM-LIVE-REINDEX-GO`. |
| RAG import | memory write intent plus rebuild dry-run | rename in docs | Reindex plans must remain read-only until Go. |
| RAG chunk metadata | deterministic chunk refs | canonicalize | Use `source_hash`, splitter version, chunk index and ranges as the stable ID input. |
| Memory diagnostics | diagnostics budget | canonicalize | Consolidate around lifecycle, alignment, store budget, graph and rebuild metrics. |
| Universal Inbox memory abstraction | extracted abstraction | canonicalize | Keep UIX naming at the edge; lifecycle docs should use extracted abstraction. |
| Universal Inbox RaptorGraph provenance | graph event plus provenance event | canonicalize | Keep source hashes and memory record IDs aligned through MEM4. |
| ORCA/Lens graph mutation | graph event | additive | Use graph-event language for backend evidence; Lens remains UX wording. |
| Source View / Answer Lens / Atlas | Lens UX over ORCA/Memory evidence | design-gated | Do not let UX labels imply automatic source mutation or canonical promotion. |

## Compatibility Classes

| Class | Meaning | Examples | Rule |
| --- | --- | --- | --- |
| keep | Existing name remains a compatibility surface. | `obsidian_*`, `/api/plugins/obsidian/*` | Do not rename or remove in repo-only slices. |
| additive | New canonical name may be added beside legacy name. | `orca_*`, lifecycle adapters | Must preserve old tests and add compatibility tests. |
| canonicalize | New docs/contracts should prefer canonical vocabulary. | diagnostics budget, graph event | Avoid new duplicate terms. |
| design-gated | User-facing wording needs product decision. | Lens/Atlas/Source View | Park behind `MEM-LEGACY-REMOVAL-GO` or a design gate. |
| live-gated | Name is tied to live rebuild/reindex/write action. | Raptor rebuild, RAG reindex | Prepare dry-run evidence only until Go. |

## Rename Order

1. Keep current route/tool names stable.
2. Add canonical backend contracts and diagnostics first.
3. Add aliases only when tests prove legacy and canonical names return
   equivalent redacted payloads.
4. Update docs and operator wording to prefer canonical terms.
5. Update UI labels only after design approval.
6. Remove legacy names only after `MEM-LEGACY-REMOVAL-GO`, migration notes and
   compatibility tests exist.

## Stop Rules

- Do not remove `obsidian_*` tools, routes, data paths or docs in MEM6.
- Do not claim `/api/plugins/orca/*` or `orca_*` parity unless tests prove it.
- Do not rename data directories or collection names in docs as if migration
  has already happened.
- Do not run rebuild, reindex, import/export, vault write or graph mutation.
- Do not include raw note contents, private vault paths, source text, tokens or
  chat IDs in evidence.

## MEM6 Done Definition

- Canonical and legacy names are mapped.
- Compatibility classes and rename order are explicit.
- Live rebuild/reindex and legacy removal remain gated.
- Later implementation slices can choose additive aliases without guessing
  whether a legacy name should be removed.
