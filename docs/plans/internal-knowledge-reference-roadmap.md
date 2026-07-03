# Internal Knowledge Reference Roadmap

Status: backend complete, UI handler deferred

Mode: Standard ABC

## Goal

Odysseus uses one canonical internal reference contract for Memory, RAG,
RaptorGraph and Graph objects, so generated answers can point at navigable
knowledge entities without leaking raw content or host paths.

## Current Evidence

- `src/internal_references.py` builds canonical `odysseus://...` refs and safe
  chat hrefs for Memory, RAG, RaptorGraph and Graph families, including
  base64url anchors for non-anchor-safe IDs.
- `routes/internal_reference_routes.py` resolves Memory refs to the existing
  Memory modal target, RaptorGraph refs to redacted provenance/event targets,
  and RAG/Graph refs to redacted `reference_only` targets instead of returning
  unsupported.
- Memory records, Universal Inbox Memory Write Intent, web-research memory
  intents, native `manage_memory` output and RaptorGraph candidates carry
  internal refs with no raw content.
- Focused tests cover helper round-trips, unsafe id rejection, owner-scoped
  Memory resolution, RaptorGraph fallback and RAG/Graph `reference_only`
  resolution.
- Telegram formatting is out of scope; this track is Odysseus-internal only.

## Non-Goals

- No Telegram deep links.
- No public HTTPS link contract.
- No v2 or legacy UI layout work in this slice.
- No live RaptorGraph, RAG, Nextcloud or Memory writes.
- No raw document text, private paths, chat IDs, tokens or provider output in
  references.

## Slice Queue

| Slice | Class | Owner | Goal | Allowed Paths | Tests |
| --- | --- | --- | --- | --- | --- |
| IKR-1 Contract | safe_offline | Alice | Done: canonical refs and UI/rendering boundary are defined. | `docs/plans/internal-knowledge-reference-roadmap.md` | docs-only |
| IKR-2 Reference Helper | repo_only | Bob | Done: `odysseus://` refs plus safe `#...` chat hrefs are implemented and tested. | `src/internal_references.py`, `tests/test_internal_references.py` | `pytest tests/test_internal_references.py` |
| IKR-3 Memory/Raptor Payloads | repo_only | Bob | Done: internal refs are attached to memory write intent, native `manage_memory` output and RaptorGraph candidates. | `src/universal_inbox_memory_write_intent.py`, `src/ai_interaction.py`, focused tests | focused tests |
| IKR-3B RAG/Graph Resolver Fallback | repo_only | Bob | Done: RAG and Graph refs resolve as redacted `reference_only` targets until concrete UI/data targets exist. | `routes/internal_reference_routes.py`, `tests/test_internal_reference_routes.py` | focused tests |
| IKR-4 UI Handler | needs_design | UI Agent | Deferred: open the correct window/panel for memory, rag, raptor and graph links. | UI files only after design handoff | UI tests |

## Reference Contract

Canonical durable form:

- `odysseus://memory/<id>`
- `odysseus://raptor/node/<id>`
- `odysseus://raptor/edge/<id>`
- `odysseus://rag/source/<id>`
- `odysseus://rag/chunk/<id>`
- `odysseus://graph/node/<id>`
- `odysseus://graph/edge/<id>`
- `odysseus://graph/query/<id>`

Rendered internal chat form:

- `#memory-<safe_id>`
- `#raptor-node-<safe_id>`
- `#raptor-edge-<safe_id>`
- `#rag-source-<safe_id>`
- `#rag-chunk-<safe_id>`
- `#graph-node-<safe_id>`
- `#graph-edge-<safe_id>`
- `#graph-query-<safe_id>`

Unsafe anchor IDs are base64url encoded with a `b64-` marker. The resolver must
decode them before opening the target entity.

## Gate Queue

Gate: `internal-knowledge-ui-handler`

Class: `needs_design`

Blocks: clickable window opening in the new UI.

Decision needed: The UI agent decides where each entity type opens and how the
selected object is highlighted.

Safe preparation done: Backend refs can be emitted and tested without touching
UI hotfiles.

Risk if bypassed: Generated links would be inconsistent across Memory, RAG,
RaptorGraph and Graph surfaces.

Next safe slice: none

## Verification

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_internal_references.py tests\test_universal_inbox_memory_write_intent.py -q
```
