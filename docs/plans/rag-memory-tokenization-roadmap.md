# Roadmap: RAG Memory Tokenization und Semantic Chunking

Stand: 2026-07-01

Status: Post-MVP Detailroadmap, nicht Teil der abgeschlossenen Backend-MVP-Gates.

## Progress

| Slice | Status | Evidence |
| --- | --- | --- |
| RMT0 Baseline Audit | done | Ist-Stand und Degradation-Regeln in dieser Roadmap dokumentiert. |
| RMT1 Token Budget Adapter | done | `src/token_budget.py`, tokenbudgetfaehiger Splitter-Entry-Point, Tests gruen. |
| RMT2 Structure-Aware Splitter | done | Gemeinsamer strukturaware Splitter ist an RAG und Personal Docs angebunden; Struktur-/Duplicate-Tail-Tests gruen. |
| RMT3 Provenance-Rich Chunk Metadata | done | RAG- und Nextcloud-Chunk-Refs tragen Splitter-Version, Hashes, Section/Page-Spans, Offsets und Budget-Schaetzungen; Privacy-Tests gruen. |
| RMT4 Migration-Safe Reindex | done | Parallel-Generation-Dry-Run mit geplanten Writes, Rollback-Ziel und Live-Write-Gate ist getestet. |
| RMT5 Retrieval Quality Gates | done | Offline Quality-Gates fuer synthetischen Recall, Duplicate-Tails, Boundary-Coherence und Budget-Overflow sind getestet. |
| RMT6 Runtime Integration | done | Chat-RAG nutzt Budget-Einheiten und Source-Spans; Kontextinflation ist getestet. |
| RMT7 Brownian Graph Retrieval Research | done | Offline Research-Decision-Gate existiert; Runtime-Umschaltung bleibt explizit verboten. |

Roadmap status: abgeschlossen fuer repo-only Backend/Logik. Live-Reindex und
Runtime-Umschaltung echter Collections bleiben ueber `RMT-LIVE-REINDEX`
operator-gated.

## Ziel

Odysseus soll Memory- und Dokument-RAG nicht mehr nur nach Zeichenfenstern
schneiden, sondern nach token-, struktur- und retrieval-bewussten Grenzen. Die
Semantik soll zuerst beim Schneiden und Bewerten besser werden; Brownian- oder
Random-Walk-Verfahren bleiben eine spaetere Graph-Retrieval-Option.

## Current Evidence

Aktueller Ist-Stand aus Repo-Audit:

- Dokument-RAG nutzt sentence-aware, aber zeichenbasiertes Chunking in
  `src/rag_text_chunking.py`.
- Personal-Docs-Keyword-Index nutzt reines `size=1000`, `overlap=200` in
  `src/personal_docs.py`.
- Memory-Vector-Store bettet ganze Memory-Eintraege ein; er chunked nicht.
- Chat-Memory-Relevance nutzt BM25-aehnliche Token-Heuristik plus optionale
  Vektorsuche in `src/chat_processor.py`.
- Nextcloud chunked extraction persistiert Hash-/Offset-Refs, keine Rohtexte.
- Core-Abhaengigkeiten enthalten `chromadb` und `fastembed`, aber keinen echten
  Modell-Tokenizer fuer RAG-Chunking.
- ChromaDB ist fuer Live-Counts separat erforderlich; lokale Persistenz kann
  vorhanden sein, waehrend der HTTP-Dienst nicht erreichbar ist.

## Leitentscheidung

Die Reihenfolge ist bewusst konservativ:

```text
diagnose -> tokenizer abstraction -> deterministic chunking -> migration-safe
dual index -> quality gates -> graph/random-walk experiments
```

Brownian/Random-Walk-Retrieval ist kein Ersatz fuer saubere Chunk-Grenzen. Es
wird erst bewertet, wenn Chunks, Provenance, Budgets und Baseline-Metriken
stabil sind.

## Non-Goals

- Keine neue Vector-Datenbank einfuehren.
- Keine Chroma-Collection live migrieren ohne Rebuild-/Rollback-Plan.
- Keine privaten Inhalte, Pfade oder Chunks in Evidence-Dokumente schreiben.
- Keine UI bauen.
- Keine echten Nextcloud-/Corpus-Analysen ohne Operator-Go.
- Kein Brownian/Random-Walk als erster Schritt.

## Slice Queue

| Slice | Klasse | Owner | Ziel | Erlaubte Pfade | Tests |
| --- | --- | --- | --- | --- | --- |
| RMT0 Baseline Audit | safe_offline | Alice | Ist-Stand maschinenlesbar dokumentieren: Chunker, Token-Heuristiken, Collections, Degradation. | `docs/plans/rag-memory-tokenization-roadmap.md`, optional `tests/test_rag_text_chunking.py` | `pytest tests/test_rag_text_chunking.py tests/test_split_chunks_no_duplicate_tail.py tests/test_nextcloud_chunked_extraction.py` |
| RMT1 Token Budget Adapter | repo_only | Bob | Gemeinsamen TokenBudget-Service fuer RAG/Memory einfuehren, zuerst als Schaetzer mit kompatibler API. | `src/rag_text_chunking.py`, `src/model_context.py` oder neuer kleiner Helper, Tests | Focused Chunking/Model-Context Tests |
| RMT2 Structure-Aware Splitter | repo_only | Bob | Markdown, Code, PDF-Seiten und Tabellen an stabilen Strukturgrenzen schneiden. | `src/rag_text_chunking.py`, `src/personal_docs.py`, `src/document_processor.py`, Tests | Neue Splitter-Regressionen plus bestehende RAG-Chunking-Tests |
| RMT3 Provenance-Rich Chunk Metadata | repo_only | Alice/Bob | Chunk-Metadaten um source version, page/section, char/token offsets, splitter version erweitern. | `src/rag_vector.py`, `src/nextcloud_chunked_extraction.py`, Tests | Metadata- und Privacy-Regressionen |
| RMT4 Migration-Safe Reindex | repo_only | Charlie | Neue Chunk-Version parallel aufbauen, alte Collections nicht ueberschreiben, Rollback dokumentieren. | `src/embedding_lanes.py`, `src/rag_vector.py`, `docs/plans/*` | Embedding-lane und RAG-ID-Stability Tests |
| RMT5 Retrieval Quality Gates | safe_offline | Bob | Synthetische Query-Sets fuer Chunk-Qualitaet, Recall, Duplicate-Tails, Boundary-Coherence und Kosten bauen. | `tests/`, optional `src/memory_perf_suite_*` | Focused quality-gate Tests |
| RMT6 Runtime Integration | repo_only | Charlie | ChatProcessor/RAG Retrieval nutzt token budgets und erklaerbare Scores ohne Kontextinflation. | `src/chat_processor.py`, `src/context_orchestrator.py`, Tests | Memory/RAG/Context budget Tests |
| RMT7 Brownian Graph Retrieval Research | safe_offline | Alice/Bob | Nur offline pruefen, ob Random-Walk/Brownian Bridge ueber Chunk-/Memory-Graphen Recall verbessert. | neue Research-Doc/Tests, keine Runtime-Umschaltung | Synthetic evaluation only |

## Phase Plan

### RMT0: Baseline Audit

Ziel: Vor Umbau klar messen, was aktuell passiert.

Done:

- Dokumentierte Chunking-Pfade fuer Dokument-RAG, Personal Docs, Memory Vector,
  Chat-Memory-Relevance und Nextcloud extraction.
- Festgehaltene Degradation-Regel fuer Chroma nicht erreichbar.
- Keine privaten Inhalte in Audit-Artefakten.

### RMT1: Token Budget Adapter

Ziel: Ein gemeinsames Interface, das spaeter echte Tokenizer tragen kann.

Start:

- vorhandenes `estimate_tokens()` als kompatibler Fallback.
- API fuer `count_text_tokens(text, model_hint=None)` und
  `split_budget(max_tokens, overlap_tokens)`.

Done:

- bestehende Chunking-Tests bleiben gruen.
- Tokenbudget kann ohne neue Pflichtabhaengigkeit genutzt werden.
- Modell-spezifische Tokenizer koennen spaeter optional eingesteckt werden.

### RMT2: Structure-Aware Chunking

Ziel: Nicht mitten in Markdown-Bloecken, Code-Fences, Listen, Tabellen,
PDF-Seiten oder Ueberschriften schneiden, solange Budget es erlaubt.

Done:

- deterministische Chunks mit stabilen IDs.
- Overlap auf Satz-/Abschnittsebene, nicht blind auf Zeichen.
- Hard-split nur als letzter Fallback fuer ueberlange Bloecke.
- Tests fuer Markdown, Code, lange Saetze, Tabellen und PDFs.

### RMT3: Provenance und Chunk Versioning

Ziel: Jeder Chunk ist erklaerbar und rebuildbar.

Metadata:

- `splitter_version`
- `source_hash`
- `source_version`
- `section_path`
- `page_start`, `page_end`
- `char_start`, `char_end`
- `token_start_est`, `token_end_est`
- `overlap_from_previous`

Done:

- Retrieval kann Quellen sauber anzeigen.
- Reindex kann alte und neue Chunk-Versionen unterscheiden.
- Privacy-Gates behalten die bisherige Redaction-Disziplin.

### RMT4: Migration-Safe Reindex

Ziel: Neue Chunk-Strategie ohne Datenverlust und ohne Collection-Dimension-Mix.

Regeln:

- Keine bestehende Collection direkt ueberschreiben.
- Neue Chunker-Version als eigene Reindex-Generation.
- Rollback auf alte Generation moeglich.
- Chroma muss offline/degraded sauber erkannt werden.

Done:

- Reindex dry-run zeigt Counts und geplante Writes.
- Reindex writes sind operator-gated.
- Tests decken ID-Stabilitaet und Owner-Isolation ab.

### RMT5: Retrieval Quality Gates

Ziel: Verbesserung messbar machen, bevor Runtime umgeschaltet wird.

Metriken:

- Trefferquote fuer synthetische Query-Sets.
- Boundary-Coherence: Chunks enthalten vollstaendige Sinn-/Struktureinheiten.
- Context inflation: injizierte Tokens pro Treffer.
- Duplicate-tail Rate.
- Retrieval latency und Chroma failure fallback.

Done:

- Baseline gegen neuen Chunker vergleichbar.
- Schlechtere Scores blockieren Runtime-Umschaltung.
- Brownian/Graph-Experimente muessen gegen diese Baseline gewinnen.

### RMT6: Runtime Integration

Ziel: Chat/RAG nutzt bessere Chunks, ohne Kontextbudget zu sprengen.

Done:

- `ChatProcessor` respektiert Query-/Context-Budgets beim RAG-Kontext.
- Retrieval-Ergebnisse tragen erklaerbare Scores und Source-Spans.
- Secure retrieval guard bleibt unveraendert streng.
- Incognito und local-only Modi behalten bestehende Semantik.

### RMT7: Brownian Graph Retrieval Research

Ziel: Optionaler Research-Slice fuer Multi-hop Retrieval.

Moegliche Experimente:

- gewichtete Random Walks ueber Chunk-Aehnlichkeitsgraphen.
- Brownian-Bridge-artige Pfade zwischen Query-Embedding und Topic-/Source-Clustern.
- Vergleich gegen Hybrid-Retrieval ohne Graph-Expansion.

Startbedingung:

- RMT1-RMT5 sind abgeschlossen.
- Synthetische Evaluation zeigt eine konkrete Multi-hop-Luecke.
- Keine Runtime-Umschaltung ohne messbaren Gewinn.

Done:

- Research-Bericht mit Go/No-Go.
- Keine Produktivierung ohne klare Recall-/Qualitaetsverbesserung.

## Gate Queue

Gate: RMT-LIVE-REINDEX
Class: needs_live_go
Blocks: RMT4/RMT6 Runtime Umschaltung
Decision needed: Darf eine neue Chunk-Generation live fuer echte Dokumente
aufgebaut werden?
Safe preparation done: Splitter, Metadata, Tests und dry-run Plan.
Risk if bypassed: Falsche Chunk-Generation koennte Retrieval verschlechtern
oder privaten Content in falsche Collections schreiben.
Next safe slice: RMT5

Gate: RMT-BROWNIAN-PRODUCTIZE
Class: needs_design
Blocks: RMT7 Runtime-Integration
Decision needed: Soll Brownian/Random-Walk als Produktpfad sichtbar werden oder
Research-only bleiben?
Safe preparation done: Offline Evaluation gegen Baseline.
Risk if bypassed: Nicht-deterministische Retrieval-Pfade waeren schwerer zu
erklaeren und zu debuggen.
Next safe slice: none

## Verification

Minimum focused tests before any runtime switch:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_rag_text_chunking.py tests\test_split_chunks_no_duplicate_tail.py tests\test_nextcloud_chunked_extraction.py tests\test_embeddings.py
```

Expected extended tests as implementation grows:

- `tests/test_rag_vector_id_stability.py`
- `tests/test_rag_manager_owner_compat.py`
- `tests/test_memory_provider.py`
- `tests/test_sensitive_retrieval_guard.py`
- `tests/test_context_orchestrator.py`
- new tokenizer/chunker quality tests.

## Go Language

- Go: deterministic token-/structure-aware chunking, metadata versioning,
  rollback path and quality gates are green.
- Partial: splitter exists but only one source type is covered.
- Deferred: Brownian/Random-Walk remains research-only.
- No-Go: new chunking worsens retrieval quality, leaks metadata, or lacks
  rollback.
- Blocked: Chroma/reindex/live corpus action is needed without operator Go.

## Master-Roadmap Placement

This roadmap is Post-MVP priority 13. It follows the completed backend MVP and
the Memory Scale Foundation direction. It should not reopen MVP roadmaps 1-10.
