# DSGVO Security Gate Roadmap

Goal: Odysseus can run in a global DSGVO mode where sensitive or unknown-private data stays on local/LAN models, local embeddings, safe local tools, and approved channels unless the operator explicitly reviews or defers a gate.

Current evidence:
- Secure chat state, policy gate, model routing, retrieval guard, provider runtime guard, and channel policy already exist.
- Session creation already checks secure provider runtime before model probing.
- Embedding lanes, RAG, memory vectors, document attachments, Vision OCR, web search/fetch, and tool calls still need full runtime wiring.

Mode: Standard ABC.

Non-goals:
- No UI redesign in this roadmap.
- No live provider, Telegram, Nextcloud, host, deploy, backup, restore, or destructive action.
- No secrets, private document contents, chat IDs, or raw provider outputs in docs/tests.

Stop rules:
- Stop on unrelated staged files, secrets, private content persistence, destructive git need, broad frontend drift, or live Go requirement.
- Park design and live verification as gates instead of bypassing them.

Slice queue:

| Slice | Class | Owner | Allowed paths | Done when | Tests |
| --- | --- | --- | --- | --- | --- |
| S1 Global runtime + local embeddings | repo_only | Bob/Charlie | `src/settings.py`, `src/privacy_runtime.py`, `src/embedding_lanes.py`, `tests/test_privacy_runtime.py`, `tests/test_embedding_lanes.py` | Global DSGVO setting exists and embedding lanes skip external/custom HTTP lanes when local-only runtime is active. | `python -m pytest tests/test_privacy_runtime.py tests/test_embedding_lanes.py` |
| S2 Request runtime context | repo_only | Bob | `src/privacy_runtime.py`, `src/secure_provider_runtime.py`, `routes/session_routes.py`, tests | Chat/session code can derive an effective secure/local-only runtime from global DSGVO mode plus per-chat mode. | Focused security/runtime tests |
| S3 Retrieval gates | repo_only | Bob | `src/chat_processor.py`, `src/memory_provider.py`, `src/rag_vector.py`, tests | Memory/RAG/graph context is guarded before injection into prompts. | Focused retrieval tests |
| S4 Attachment and Vision gates | repo_only | Bob | `src/document_processor.py`, `routes/document_routes.py`, tests | Images/audio/PDF Vision OCR cannot reach external providers under DSGVO or sensitive/unknown context. | Focused document tests |
| S5 Web and tool gates | repo_only | Bob | `src/agent_tools/`, `services/search/`, relevant routes/tests | Sensitive queries and unsafe tools are blocked or require review under DSGVO. | Focused tool/search tests |
| S6 Nextcloud ingestion integration | repo_only | Alice/Bob | `src/nextcloud_*`, `src/rag_vector.py`, tests | Nextcloud privacy partition feeds classification/local-only requirements into extraction, memory, and RAG paths. | Focused Nextcloud tests |
| S7 Observability contract | repo_only | Alice/Charlie | service health/admin diagnostics tests/docs | Runtime shows why data/model/tool/channel was allowed, blocked, local-only, or review-required without leaking contents. | Focused health tests |

Gate queue:

Gate: G1 UI toggle placement
Class: needs_design
Blocks: UI control for global DSGVO mode
Decision needed: Where in v2 settings/project shell should the global DSGVO toggle live?
Safe preparation done: Backend key and runtime helpers can be built without UI.
Risk if bypassed: Toggle may be hard to discover or inconsistent with v2 window model.
Next safe slice: S1

Gate: G2 Live secure-channel behavior
Class: needs_live_go
Blocks: Telegram/API/live provider proof
Decision needed: Which channels are allowed to return sensitive responses once secure flow exists?
Safe preparation done: Channel policy exists and can be tested offline.
Risk if bypassed: Sensitive answers could leave over a channel the operator did not intend.
Next safe slice: S1

Paths:
- Runtime core: S1, S2.
- Data ingress and retrieval: S3, S4, S6.
- Outbound effects: S5, S7.

Verification:
- Run focused tests per slice.
- Add regression tests for every blocked leak path.
- Keep docs free of private content and private metadata.

Go language:
- Go: backend gate passes focused tests and does not require live action.
- Partial: safe backend contract exists, but live/UI proof is still gated.
- No-Go: sensitive data can still be sent to external provider/embedding/tool/channel by the tested path.
- Deferred: UI or live verification explicitly parked.
- Blocked: unsafe state, secrets, or unclear worktree ownership.
