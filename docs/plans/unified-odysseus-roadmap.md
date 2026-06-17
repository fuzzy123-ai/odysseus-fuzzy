# Unified Odysseus Roadmap

Stand: 2026-06-16

Status: **aktive Master-Roadmap fuer den Fork**

Dieses Dokument ist der zentrale Einstiegspunkt fuer die naechsten Odysseus-Arbeiten. Die aelteren Roadmaps bleiben als Detail- und Archivplaene erhalten, aber neue Alice/Bob/Charlie-Beauftragungen sollen von hier ausgehen.

## Leitentscheidung

Odysseus entwickelt sich in drei Schritten weiter:

1. **Stabilisieren**: aktuellen Fork nach Upstream-Sync testen, Bugs bereinigen, 1.0-Evidence nicht verwischen.
2. **Orchestrieren**: den manuell bewiesenen Alice/Bob-Prozess als native Development-Orchestration bauen.
3. **Skalieren**: Memory, Graph, Query, Jobs und UI so strukturieren, dass grosse Datenmengen fluessig bleiben.

Die aktuell wichtigste Produktlinie ist nicht "noch mehr Obsidian-Features", sondern:

```text
Memory-first + kontrollierte Multi-Agent-Orchestration + klare Zustandsgrenzen
```

## Bestehende Detailplaene

| Plan | Rolle ab jetzt |
| --- | --- |
| `docs/obsidian/00-priorisierte-roadmap.md` | Archiv und Detailplan fuer Memory-first/Obsidian-Lens bis M6 |
| `docs/plans/deepseek-model-router-graceful-degradation.md` | Detail- und Evidence-Plan fuer M6 Model Router |
| `docs/plans/1.0-evidence-release-checklist.md` | aktive 1.0-Go/No-Go-Checkliste fuer Evidence, manuelle Release-Gates und Bugfix-Fenster |
| `docs/plans/odysseus-lens-ui-memory-interaction.md` | neuer Detailplan fuer Lens UI, Memory Lesen/Pflegen, Insights, Diagnostics und Activity |
| `docs/plans/image-tools-worker-contract.md` | neuer Stabilisierungstrack fuer isolierte Background-Removal/Image-Tools statt harter Core-Dependencies |
| `docs/plans/secure-data-mode-contract.md` | Security-/DSGVO-Foundation fuer sensible Quellen, Secure Chats und local-only Policy |
| `docs/plans/secure-data-mode-audit-runbook.md` | Readiness-Runbook fuer Secure Data Mode vor Runtime-Hooks |
| `docs/plans/plugin-platform-manifest-policy.md` | eigener Plugin-Platform-Plan fuer Manifest-/Registry-Policy, bevor Plugins in UI/Installer/Runtime erweitert werden |
| `docs/plans/system-health-checker-plugin.md` | Plugin-Track fuer Homeserver Health: Host-Agent, Podman-first Runtime Adapter, Telegram Status/Alerts |
| `docs/plans/system-health-checker-ops-runbook.md` | Ops-/Security-Runbook fuer den spaeteren Homeserver Host-Agent |
| `docs/plans/development-orchestration-foundation-roadmap.md` | Detailplan fuer Orchestration v1 |
| `docs/plans/development-orchestration-plan-graph.md` | Produktkonzept fuer Planning Canvas und Plan Graph |
| `docs/plans/automated-agent-handoff-orchestration-mvp.md` | neuer Runtime-Track fuer vollautomatisches Agent-Handoff und verified Orchestration |
| `docs/plans/automated-agent-handoff-e2e-smoke-runbook.md` | AUTO7-Runbook fuer deterministischen Zwei-Agenten-Smoke ohne echte Thread-Sends |
| `docs/plans/automated-agent-n-scaling-design.md` | AUTO8-Design fuer registrierte Agent-Pools, Budgets, Queueing und Locks |
| `docs/plans/memory-scale-foundation-roadmap.md` | Detailplan fuer Postgres/pgvector und Scale Foundation |
| `docs/plans/nextcloud-source-bridge.md` | pausierter Source-Provider-Plan, erst aktiv wenn Nextcloud laeuft |
| `docs/plans/vault-longterm-memory.md` | aelterer Langzeitgedaechtnis-Plan, nur noch historischer Kontext |

Wenn Plaene kollidieren, gilt diese Master-Roadmap.

## Versionslinie

| Version | Name | Ziel | Status |
| --- | --- | --- | --- |
| `0.10.x` | Memory-first RC Closure | aktueller Obsidian/Memory/Model-Router-Stand stabil, getestet, dokumentiert | abgeschlossen als Pre-Scale-Basis |
| `0.11.x` | Agent State & Architecture Hygiene | Zustandstrennung, Context Capsules, Tool Truth, Backend-Grenzen | abgeschlossen als Foundation-Schnitt |
| `0.12.x` | Development Orchestration v1 | Plan Graph Store, Agent Runs, Heartbeat Coordinator, Quality Gates, Mini Dashboard | abgeschlossen mit OR7-Smoke |
| `0.13.x` | Memory Scale Foundation | Store-Interfaces, Diagnostics, Query Budgets, Postgres/pgvector-Design | abgeschlossen mit MS7-Ops-Readiness |
| `0.14.x` | Lightweight Memory Maintenance | RAPTOR/GraphRAG-Maintenance mit kleinem Modell unter 2 GB RAM, Engine bleibt algorithmisch/budgetiert | abgeschlossen mit `LM7-fallback-routing`, Test-Suite `64 passed, 1 warning` |
| `0.15.x` | Odysseus Lens UI & Memory Interaction | Lens als klare Arbeitsoberflaeche ueber Memory: Lesen, Pflegen, Insights, Diagnostics, Activity | weitgehend umgesetzt, harte Rename-Stufe bleibt freigabepflichtig |
| `0.16.x` | Isolated Image Tools Worker | Background Removal und spaetere Image-AI-Tools laufen isoliert statt in der Core-venv | Worker/Client/Route-MVP umgesetzt, finaler manueller Image-Smoke offen |
| `0.17.x` | Secure Data Mode & Local-Only Policy | sensible Quellen, immutable Secure Chats und zentrale Policy Gates vorbereiten | Foundation SEC1-SEC8 umgesetzt, Runtime-Hooks separat |
| `0.18.x` | Automated Agent Handoff & Orchestration MVP | aus Plan Graph, Agent Runs, Thread Bridge, Heartbeat und Quality Gates wird echte Runtime | AUTO1-AUTO8 vorbereitet; echte Thread-/Git-/Test-Hooks bleiben Runtime-Follow-up |
| `0.19.x` | Plugin Platform: System Health Checker | Homeserver-Monitoring als eigener Plugin-Track mit Debian Host-Agent, Podman-first Runtime Adapter und Telegram Status/Alerts | SHC0-SHC9 Foundation abgeschlossen, Manifest-Policy, lokales Plugin-Audit und Release-Gate ergänzt, Host-Agent bleibt Follow-up |
| `0.20.x` | Source Provider Expansion | Nextcloud/File Archive als Source Provider, sobald Infrastruktur laeuft | pausiert bis Nextcloud laeuft |
| `1.0.0` | Evidence Release | reproduzierbarer Install-/Upgrade-/Provider-/Rebuild-Nachweis, saubere Known-Limits | aktuelle naechste Phase |

## Fortschrittsformat

Wenn Fortschritt gemeldet wird:

```text
Gesamtfortschritt: XX %
Alice-Pfad: XX %
Bob-Pfad: XX %

Rueckmeldung:
...
```

Charlie nutzt dieses Format fuer Statusberichte, waehrend Alice und Bob arbeiten.

## Rollen

| Rolle | Aufgabe | Was sie nicht tut |
| --- | --- | --- |
| Alice | Produktvertrag, UI/Lens, Nutzertexte, Dashboard, Runbooks, Release-Evidence | keine tiefen Backend-Refactors ohne Handoff |
| Bob | Backend, Stores, APIs, Query, Tool/Agent Runtime, Tests | keine UI-/Doku-Hotfiles ohne Handoff |
| Charlie | Master/Koordinator: Roadmap, Slice-Zuschnitt, Worktree, Konfliktkontrolle, Tests, Merge, Push, Abschluss | keine parallele Feature-Implementierung in Alice/Bob-Dateien, solange beide aktiv sind |

## Token-Schaetzung

Die Tokenwerte sind grobe Arbeitsbudget-Schaetzungen fuer Agent-Kontext plus Review, nicht harte Limits. Sie helfen nur beim Planen:

- `S`: ca. 10k-25k Tokens
- `M`: ca. 25k-60k Tokens
- `L`: ca. 60k-120k Tokens
- `XL`: 120k+ Tokens oder mehrere Sessions

Wenn reale Tests, Merge-Konflikte oder UI-Smokes dazukommen, kann der Bedarf deutlich steigen.

## Priorisierte Feature-Matrix

| Prio | Version | Feature | Warum jetzt sinnvoll | Umfang | Tokens | Alice | Bob | Charlie | Parallel? |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| P0 | `0.10.x` | Post-merge Bugfix/Test Sweep | Nach Upstream-Sync muessen Regressionen gefunden werden, bevor neue Agenten loslaufen. | M | M | UI-/Doku-Smokes, Nutzerpfade | Backend-/Pytest-Regressions | Testplan, Worktree, Konflikttriage, finale Freigabe | ja, nach Dateisplit |
| P0 | `0.10.x` | 1.0 Evidence Stabilisierung | M6 ist implementiert, aber Release braucht klare Evidence und Known-Limits. | S-M | S | Release Notes, Provider-/Fallback-Erklaerung | Backend-Evidence pruefen | Abschlussnotiz, Tests, Push | ja |
| P0 | `0.11.x` | Agent State Isolation | Verhindert Context Bleeding zwischen Master, Alice, Bob, Reviewer, Projekten. | L | L | UX-Vertrag fuer Rollen/Sichtbarkeit | `agent_id`, `role_id`, Namespace-/Scope-Modell | Schnittstellenentscheidung, Migration ohne Big Bang | bedingt |
| P0 | `0.11.x` | Agent Context Capsules | Subagents bekommen kleine, klare Slices statt globalen Chat- und Tool-Kontext. | M | M | Capsule-Wording, Handoff-Template | Capsule-Payload, Runtime-Injection | Slice-Standards, Review-Gate | ja |
| P0 | `0.11.x` | Dynamic Tool/Skill Loading | Reduziert Prompt-Bloat und macht lokale Modelle brauchbarer. | M-L | L | sichtbare Tool-/Skill-Erklaerung | Tool-Auswahl, Schema-Filter, Progressive Disclosure | Prompt-Budget-Audit | ja, mit API-Handoff |
| P0 | `0.11.x` | Tool Result Truth Layer | Keine stillen Erfolgshalluzinationen bei lokalen Modellen oder Tool-Parsing-Fehlern. | M | M | Nutzertexte fuer Toolfehler | strukturierte Tool-Resultate, success/evidence contracts | Failure-Matrix, Regressionstests | ja |
| P1 | `0.11.x` | Workspace Sandbox v2 | Agenten duerfen fremde Codebases bearbeiten, ohne Odysseus-Systemdateien zu treffen. | M | M | UI/Runbook fuer Workspace-Auswahl | Workspace Policy, Locks, Tests | Risk Review, Git-/Path-Gates | bedingt |
| P1 | `0.11.x` | Backend Canonical Boundaries | `src/` vs `services/` Drift muss kontrolliert werden, bevor Memory/Search weiter wachsen. | XL | XL | Dokumentiert Nutzer-/API-Verhalten | Interfaces, Modulgrenzen, Deprecation-Pfade | Refactor-Plan, Merge-Guardian | nein, nur sequenziell |
| P1 | `0.12.x` | Plan Graph Store v1 | Orchestration braucht persistierbare Plans, Slices und Agent Runs. | M | M | Dashboard-Contract | Store/API/Tests | Contract Freeze, Review | ja |
| P1 | `0.12.x` | Thread Lifecycle Bridge | Alice/Bob-Threads muessen eindeutig gelesen und angestossen werden koennen. | M | M | Status-Wording | Read/Send/Handoff-Bridge | Eindeutigkeits-Gate | ja |
| P1 | `0.12.x` | Heartbeat Coordinator v1 | Charlie kann laufende Pfade ueberwachen und Folgeauftraege verteilen. | M-L | L | sichtbare Run-Zustaende | Automation Lifecycle | Live-Koordination, Stop-Kriterien | ja |
| P1 | `0.12.x` | Quality Gates v1 | `claimed done` darf nicht `verified done` sein. Tests, Commits und Evidence zaehlen. | M | M | Gate-Lens | Test/Git/Evidence Backend | Verifikation, Merge-Entscheidung | ja |
| P1 | `0.12.x` | Mini Orchestration Dashboard | Nutzer sieht Fortschritt, Blocker, naechste Aktion ohne Thread-Hopping. | M | M | UI/UX | Status API | Integrations-Smoke | ja, nach Contract |
| P1 | `0.13.x` | Memory Store Interfaces | Vor Postgres braucht es klare Memory/Source/Chunk/Graph/Job-Interfaces. | L | L | keine UI ausser Erklaertexte | Store Interfaces, Adapter Tests | Scope-Kontrolle | bedingt |
| P1 | `0.13.x` | Diagnostics Layer | Skalierung ohne Messdaten ist Blindflug. | M-L | L | Health-/Lens-Texte | Metrics, timings, counts | Gate-Definition | ja |
| P1 | `0.13.x` | Query Budgets & Performance Gates | Keine unbounded Graph-/Memory-/Query-Pfade. | M | M | UI fuer clipped/partial results | Limits, cursors, perf tests | Regression-Budget | ja |
| P2 | `0.13.x` | Postgres + pgvector Design | Zentrale Wahrheit fuer Memory/Graph/Jobs, pgvector als integrierte Semantik. | L | L | Migrations-/Ops-Runbook | Schema, Import/Export Proof | Architekturentscheidung | bedingt |
| P2 | `0.13.x` | Progressive Graph API | UI darf nie 100k/1M Nodes laden, sondern Ausschnitte und Aggregate. | M-L | L | Graph-Lens/Clipping UX | serverseitige Graph Budgets | Browser-/Payload-Smokes | ja |
| P2 | `0.14.x` | Lightweight Memory Maintenance | Kleine Maintenance-Modelle duerfen RAPTOR/GraphRAG pflegen, aber nie globale Wahrheit entscheiden. | L | L | Review-/Evidence-Sprache | bounded Jobs, K-Means Proof, Summary Worker | Drift-/Fallback-Gates | ja |
| P1 | `0.15.x` | Odysseus Lens UI & Memory Interaction | Die Memory-Foundation braucht eine klare Nutzeroberflaeche: Lesen/Pflegen trennen, Review/Insights/Diagnostics ordnen, Shell stabilisieren. | L | L | UX-Vertraege, Navigation, Zustaende, Texte | fokussierte UI-/Static-Implementierung nach Handoff | Hotfile-Sperren, Browser-/Static-Smokes | bedingt |
| P1 | `0.16.x` | Isolated Image Tools Worker | `rembg` passt nicht sauber in die Python-3.14-Core-venv; Background Removal braucht einen isolierten Worker, bevor Telegram/Image-Actions stabil werden. | M | M | Worker-Contract, UI/Cookbook-Setup-Texte | Worker Client, Route-Adapter, isolierter Worker-MVP | Roadmap, Hotfile-Gates, finaler Remove-BG-Smoke | bedingt |
| P1 | `0.17.x` | Secure Data Mode & Local-Only Policy | Sensible Daten duerfen nicht in API-Modelle, externe Embeddings oder unsichere Tools geraten. | M-L | M | UX-/Policy-Vertraege, Nutzertexte, Secure-Flow | Klassifikation, Chat-State, Policy Gates, spaeter Routing Guards | Stop-Regeln, Test-Gates, keine Hotfile-Integration ohne Freigabe | bedingt |
| P1 | `0.18.x` | Automated Agent Handoff & Orchestration MVP | Der manuell bewiesene Alice/Bob/Charlie-Prozess soll nativ laufen: Approved Plan -> Dispatch -> Handoff -> Gates -> verified done. | L | L | UX/Safety-Vertraege, Dashboard-Sprache, Handoff-Texte | Runtime Store/API, Thread Registry, Parser, Loop, Gates | Stop-Regeln, Hotfiles, E2E-Smoke, Push-Gates | bedingt |
| P1 | `0.19.x` | System Health Checker Plugin | Odysseus braucht einen nachvollziehbaren Plugin-Track fuer Homeserver Health statt versteckter Core-/Lens-Kommandos. | L | L | NDD-/Plugin-Vertrag, Statussprache, Telegram UX | Host-Agent-Modelle, Collectors, Rule Engine, Runtime Adapter | Plugin-Grenzen, Rechte, Hotfile-Gates, finaler Ops-Smoke | bedingt |
| P2 | `0.20.x` | Nextcloud Source Bridge MVP | Erst aktiv, wenn Homeserver/Nextcloud laeuft; dann als Source Provider, nicht als Memory-Kern. | M-L | M-L | Source-/Review-Lens | Sync-Ordner Scanner/Provider | Sicherheitsmodell, Rechte | bedingt |
| P3 | `post-1.0` | Qdrant Accelerator | Nur wenn pgvector real zu langsam ist. | L | XL | kaum | rebuildbarer Vector-Accelerator | Diagnoseentscheidung | nein |
| P3 | `post-1.0` | Kuzu Accelerator | Nur wenn Postgres-Graph real zu langsam ist. | L | XL | Graph-UX | rebuildbarer Graph-Accelerator | Diagnoseentscheidung | nein |
| P3 | `post-1.0` | UMAP/GMM/adRAP Research | Zukunftsmusik, erst nach K-Means/Bisecting-K-Means, Diagnostics und belegter Qualitaetsluecke. | XL | XL | Evaluation UX | Experimente/Evaluation | Forschungs-Gate | nein |

## Aktuelle Phase: `1.0.0` Evidence Release & Bugfix-Fenster

`0.14.x` ist technisch abgeschlossen und auf den Fork gepusht. Die naechste Arbeit ist kein neuer grosser Feature-Track, sondern ein kontrolliertes Release-/Evidence-Fenster: Nutzer testet reale Pfade, Alice/Bob bekommen nur konkrete Bugfix- oder Evidence-Slices, und Charlie haelt Worktree, Tests, Roadmap und Push-Status sauber.

Aktive Checkliste: `docs/plans/1.0-evidence-release-checklist.md`.

### Ziele

- Reproduzierbaren 1.0-Evidence-Stand herstellen.
- Install-/Start-/Provider-/Rebuild-/Fallback-Pfade pruefen.
- Known Limits klar dokumentieren, statt still als Feature-Defizite zu verstecken.
- Nur kleine Bugfix-Slices schneiden, wenn reale Tests Probleme zeigen.
- Keine neuen Post-1.0-Research-Tracks starten.
- Nextcloud bleibt pausiert, bis die Homeserver-/Nextcloud-Infrastruktur laeuft.

### Alice-Pfad `1.0.0`

| Slice | Ziel | Dateien | Exit |
| --- | --- | --- | --- |
| `REL1-release-evidence-notes` | Nutzerverstaendliche Release-Evidence, Known Limits und Testpfade aufraeumen | Release-/Plan-Doku nach Charlie-Handoff | 1.0-Stand ist erklaerbar |
| `REL2-user-test-bug-notes` | Auffaellige Testbefunde in kleine, eindeutige Bug-Slices uebersetzen | neue/aktualisierte Bug-/Evidence-Notizen | Alice/Bob koennen ohne Ratespiel arbeiten |

### Bob-Pfad `1.0.0`

| Slice | Ziel | Dateien | Exit |
| --- | --- | --- | --- |
| `REL1-regression-smoke` | Fokussierte Backend-/Plugin-/Memory-Smokes ausfuehren und Evidence sammeln | keine Feature-Dateien ohne Bug | rote Pfade sind reproduzierbar oder gruen belegt |
| `REL2-bugfix-slices` | Nur konkrete, reproduzierte Bugs beheben | betroffene Runtime-/Testdateien je Bug | Fix + Test + Commit pro Bug |

### Charlie-Pfad `1.0.0`

| Slice | Ziel | Exit |
| --- | --- | --- |
| `REL0-roadmap-closeout` | Roadmap nach `0.14.x` aktualisieren und naechsten Fokus festlegen | erledigt, wenn `0.14.x` als abgeschlossen markiert ist |
| `REL1-release-gate` | Tests, Worktree, Push und Evidence pruefen | 1.0-Go/No-Go statt Bauchgefuehl |
| `REL2-slice-router` | Nutzer-Bugs in Alice/Bob-Slices schneiden | keine parallelen Hot-File-Konflikte |
| `REL4-evidence-snapshot-model` | automatisierte und manuelle Release-Gates maschinenlesbar trennen | externes 1.0-Go bleibt No-Go, solange Pflicht-Evidence pending ist |
| `REL5-readiness-report-model` | Release-, Plugin- und manuelle Evidence-Gates zu einer kompakten Go/No-Go-Sicht aggregieren | naechste Aktionen sind maschinenlesbar, ohne Runtime-Hooks |
| `REL6-manual-evidence-model` | manuelle Evidence-Eintraege typisieren und Partial/No-Go ausdruecklich modellieren | offene Provider-/Test-Vault-Gates koennen nicht versehentlich als Go gelten |
| `REL7-release-slice-router-model` | Readiness-Blocker in Alice/Bob/Charlie-Folgeaufgaben uebersetzen | Blocker werden deterministisch geschnitten, aber nicht automatisch dispatched |
| `REL8-readiness-pipeline-snapshot` | automatisierte Gates, manuelle Evidence, Plugin-Gates und Folge-Slices als read-only Snapshot zusammenfuehren | aktueller 1.0-Status ist maschinenlesbar, ohne Live-Aktionen |
| `REL9-followup-matrix-model` | Folge-Slices nach Alice/Bob/Charlie und Parallel-Sicherheit gruppieren | Matrix ist bereit fuer Orchestration, aber kein Dispatch |
| `REL10-orchestration-status-model` | Pipeline und Matrix zu einem kompakten Dashboard-/Runbook-Status verdichten | aktive Owner und Parallel-/Sequenz-Gates sind sichtbar, aber kein Dispatch |
| `REL11-status-markdown-renderer` | Orchestration-Status als kompakten Markdown-Block fuer Runbook/Chat rendern | Statusmeldungen bleiben stabil, kurz und ohne Live-Aktionen |
| `REL12-current-status-markdown-entrypoint` | aktuellen dokumentierten 1.0-Status direkt als Markdown rendern | nutzt nur Snapshots, keine Live-Checks |
| `REL13-followup-markdown-renderer` | Folge-Slices als Markdown-Tabelle rendern | Aufgaben sind lesbar, aber werden nicht dispatched |
| `REL14-release-handoff-markdown` | Status und Folge-Slices zu einem Handoff-Block kombinieren | Morgenstatus ist direkt renderbar, ohne Live-Aktionen |
| `REL15-local-readiness-bundle` | lokale Plugin-Registry/Audit-Gates in den Release-Handoff einspeisen | lokaler Status bleibt read-only: keine Downloads, Imports oder Dispatches |
| `REL16-artifact-manifest` | Release-/Plugin-/Handoff-Artefakte als Traceability-Manifest pruefen | benoetigte Statusquellen sind lokal nachvollziehbar |
| `REL17-release-morning-brief` | Handoff und Artefakt-Traceability zu einem Tagesstart-Block kombinieren | morgens ist direkt sichtbar, was blockiert, wer dran ist und welche Dateien den Nachweis tragen |
| `REL18-release-morning-summary` | Morgenbrief-Status als maschinenlesbaren Snapshot verdichten | Dashboard/Automation kann Status, Plugin-Gate, Artefakt-Gate und naechste Aktionen ohne Live-Checks lesen |
| `REL19-release-morning-payload` | Summary und Morgenbrief zu einem stabilen Dashboard-/Handoff-Payload kombinieren | spaetere UI/Automation liest einen read-only Payload, ohne Live-Aktionen auszufuehren |
| `REL20-release-morning-payload-contract` | Morning-Payload-Dicts statisch validierbar machen | gespeicherte/uebergebene Payloads koennen ohne Live-Checks auf Pflichtfelder und Typen geprueft werden |
| `REL21-release-morning-payload-json` | Morning-Payload deterministisch als JSON rendern | UI/Automation kann denselben read-only Payload speichern, diffen und validieren |
| `REL22-release-morning-payload-diff` | gespeicherte Morning-Payloads deterministisch vergleichen | Automation erkennt Status-, Followup- und Artefakt-Aenderungen ohne Live-Checks |
| `REL23-release-morning-payload-diff-markdown` | Payload-Diffs als kompakten Markdown-Block rendern | Handoff zeigt Aenderungen zwischen gespeicherten Morning-Payloads ohne Logsuche |
| `REL24-local-plugin-audit-summary` | lokale Plugin-Auditdetails im Morning Summary Snapshot sichtbar machen | UI/Automation sieht lokale Plugin-Audit-OK/Fails ohne Markdown zu parsen |
| `REL25-local-plugin-failure-diff` | lokale Plugin-Fails in Morning-Payload-Diffs ausweisen | Handoff erkennt neue und geloeste lokale Plugin-Probleme ohne Report-Vergleich |
| `REL26-release-morning-payload-diff-json` | Payload-Diffs deterministisch als JSON rendern | UI/Automation kann Diff-Ergebnisse speichern, diffen und validieren |
| `REL27-release-morning-payload-digest` | Morning-Payload deterministisch hashen | Automation erkennt identische Snapshots guenstig, ohne JSON erneut zu vergleichen |
| `REL28-release-morning-snapshot-envelope` | Morning-Payload, JSON und Digest als Snapshot buendeln | UI/Automation liest einen stabilen Envelope statt mehrere Helfer selbst zu verdrahten |
| `REL29-release-morning-envelope-contract` | Snapshot-Envelopes statisch validierbar machen | gespeicherte Envelopes pruefen Payload, JSON und Digest-Konsistenz ohne Live-Checks |
| `REL30-release-morning-envelope-diff` | Snapshot-Envelopes deterministisch vergleichen | Automation nutzt Digest fuer schnelle No-Change-Erkennung und Payload-Diff fuer Details |
| `REL31-release-morning-envelope-diff-markdown` | Envelope-Diffs als kompakten Markdown-Block rendern | Handoff zeigt Envelope-/Payload-Aenderungen ohne JSON-Vergleich |
| `REL32-release-morning-envelope-diff-json` | Envelope-Diffs deterministisch als JSON rendern | UI/Automation kann Envelope-Diff-Ergebnisse speichern und weiterverarbeiten |
| `REL33-release-morning-snapshot-history` | mehrere Snapshot-Envelopes in-memory auswerten | Automation bekommt latest/previous/diff ohne Dateisystem- oder Live-Checks |
| `REL34-release-morning-snapshot-history-markdown` | Snapshot-History als kompakten Markdown-Block rendern | Morgen-Handoff zeigt Verlauf und latest Diff ohne eigene JSON-Auswertung |
| `REL35-release-morning-snapshot-history-json` | Snapshot-History deterministisch als JSON rendern | UI/Automation kann Verlauf und latest Diff speichern, validieren und diffen |

## Abgeschlossene Phase: `0.14.x` Lightweight Memory Maintenance

`0.14.x` ist abgeschlossen. Der Arbeitszyklus hat bewiesen, dass kleine lokale Modelle unter 2 GB RAM spaeter RAPTOR-/GraphRAG-Maintenance-Pakete bearbeiten koennen, ohne globale Wahrheit zu entscheiden oder riesige Kontexte zu laden.

Evidence:

- Abschlusscommit: `0386f405 Add fallback routing gate`
- Push: `fuzzy/dev`
- Abschluss-Suite: `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_lightweight_memory_maintenance.py tests\test_derived_cluster_runs.py tests\test_kmeans_clustering_proof.py tests\test_evidence_bound_summary_worker.py tests\test_graph_maintenance_worker.py tests\test_small_model_evaluation_gates.py tests\test_fallback_routing.py`
- Ergebnis: `64 passed, 1 warning`

### Ziele

- Worktree sauber halten.
- Zuerst Worker-Vertrag und bounded Task-Modell, dann Derived Data.
- Kein echter LLM-Call, kein RAPTOR-Fullbuild, kein Graph-Rebuild in `LM1`.
- Kein Maintenance-Job darf unbounded Memory, Graph oder Cluster laden.
- Kleine Modelle sind Worker; Fallback-/Reviewer-Modelle entscheiden nur ueber klare Gates.
- Jede Summary oder Graph-Aenderung braucht Quellen-, Chunk- oder Evidence-Refs.

### Alice-Pfad `0.14.x`

| Slice | Ziel | Dateien | Exit |
| --- | --- | --- | --- |
| `LM1A-maintenance-worker-contract` | Produkt-/Sicherheitsvertrag fuer kleine Maintenance-Modelle klaeren | `docs/plans/maintenance-worker-contract.md` | done |
| `LM2A-derived-cluster-run-contract` | Derived Cluster Runs, Versionen und Rebuild-Sprache erklaeren | `docs/plans/derived-cluster-run-contract.md` | done |
| `LM3A-kmeans-clustering-proof-runbook` | K-Means/Bisecting-K-Means Proof beschreiben | `docs/plans/kmeans-clustering-proof-runbook.md` | done |
| `LM4A-evidence-bound-summary-contract` | Summary-Review-Sprache und Quellenpflicht definieren | `docs/plans/evidence-bound-summary-worker-contract.md` | done |
| `LM5A-graph-maintenance-worker-contract` | Regeln gegen halluzinierte Kanten definieren | `docs/plans/graph-maintenance-worker-contract.md` | done |
| `LM6A-small-model-evaluation-gates-contract` | Kriterien fuer "kleines Modell reicht" definieren | `docs/plans/small-model-evaluation-gates-contract.md` | done |
| `LM7A-fallback-routing-contract` | Produktlogik fuer groesseres Modell/Fallback definieren | `docs/plans/fallback-routing-contract.md` | done |

### Bob-Pfad `0.14.x`

| Slice | Ziel | Dateien | Exit |
| --- | --- | --- | --- |
| `LM1B-maintenance-worker-model-spike` | Dataclasses/Enums fuer bounded Maintenance Tasks und Worker Readiness | `src/lightweight_memory_maintenance.py`, `tests/test_lightweight_memory_maintenance.py` | done |
| `LM2B-derived-cluster-run-model` | Cluster Run/Node/Membership-Modell vorbereiten | `src/derived_cluster_runs.py`, `tests/test_derived_cluster_runs.py` | done |
| `LM3B-kmeans-clustering-proof-model-spike` | isolierter K-Means/Bisecting-K-Means Proof | `src/kmeans_clustering_proof.py`, `tests/test_kmeans_clustering_proof.py` | done |
| `LM4B-evidence-bound-summary-worker` | Summary Task mit Quellenpflicht und Review-Status modellieren | `src/evidence_bound_summary_worker.py`, `tests/test_evidence_bound_summary_worker.py` | done |
| `LM5B-graph-maintenance-worker-model` | Entity-/Edge-Kandidaten mit Provenance/Dedupe/Review modellieren | `src/graph_maintenance_worker.py`, `tests/test_graph_maintenance_worker.py` | done |
| `LM6B-small-model-evaluation-gates-model` | JSON/Evidence/Drift/Confidence Gates modellieren | `src/small_model_evaluation_gates.py`, `tests/test_small_model_evaluation_gates.py` | done |
| `LM7B-fallback-routing-model` | Routing, Retry/Backoff, Fallback und Kostenbudget modellieren | `src/fallback_routing.py`, `tests/test_fallback_routing.py` | done |

### Charlie-Pfad `0.14.x`

| Slice | Ziel | Exit |
| --- | --- | --- |
| `LM1C-contract-model-alignment` | Alice-Contract und Bob-Modell abgleichen | done |
| `LM2C-derived-vs-truth-gate` | Derived Data strikt von Truth Store trennen | done |
| `LM3C-kmeans-quality-budget-review` | K-Means Proof gegen Budgets pruefen | done |
| `LM4C-evidence-and-drift-gate` | Summary- und Drift-Gates fuer kleine Modelle definieren | done |
| `LM5C-review-queue-gate` | Graph Maintenance nur als Review-Kandidaten zulassen | done |
| `LM6C-fallback-decision-gate` | Fallback-Entscheidung aus Evaluation Gates pruefen | done |
| `LM7C-routing-cost-readiness-gate` | Routing-/Kosten-/Fallback-Abschluss pruefen | done |

## Version `0.11.x`: Agent State & Architecture Hygiene

Diese Version ist die wichtigste Grundlage fuer spaetere Automatisierung. Sie sollte vor grosser Orchestration-UI kommen.

### Reihenfolge

1. `AS1-agent-state-model`
2. `AS2-context-capsules`
3. `AS3-tool-truth-layer`
4. `AS4-dynamic-tool-loading`
5. `AS5-workspace-sandbox-v2`
6. `AS6-backend-boundary-map`

### Alice/Bob/Charlie Matrix

| Slice | Alice | Bob | Charlie | Parallelregel |
| --- | --- | --- | --- | --- |
| `AS1-agent-state-model` | Rollen-/Agent-Sichtbarkeit und UX-Begriffe | Datenmodell fuer `agent_id`, `role_id`, `project_id`, `memory_scope` | entscheidet Migrationspfad und Nicht-Ziele | ja, Alice nur Doku/Contract |
| `AS2-context-capsules` | Handoff-/Capsule-Template | Capsule Payload, injection points, tests | Review: keine globalen Kontextlecks | ja |
| `AS3-tool-truth-layer` | Fehlertexte und Evidence-UX | strukturierte Tool Results, parse failure contracts | rote Tests fuer Erfolgshalluzination | ja |
| `AS4-dynamic-tool-loading` | UI/Docs fuer aktivierte Tools/Skills | Tool selection, schema thinning, budget metrics | Prompt-Budget-Vergleich | bedingt |
| `AS5-workspace-sandbox-v2` | Workspace-Auswahl/Runbook | Policy, locks, path tests | Security Review | bedingt |
| `AS6-backend-boundary-map` | Nutzerverhalten dokumentieren | `src`/`services` Kanonisierung planen | fuehrt Vertrag + Inventar in `docs/plans/backend-boundary-sequencing-plan.md` zusammen | nein, erst Plan, dann Slices |

### Definition of Done `0.11.x`

- Agenten haben explizite Scope-Identitaet, nicht nur Persona-Text.
- Jeder Agent Run kann als Capsule reproduziert werden.
- Tool-Erfolg ist maschinenlesbar belegt.
- Prompt-/Tool-Kontext ist budgetierbar.
- Workspace-Schreibzugriffe sind projektbezogen und getestet.
- `src`/`services` Drift ist kartiert und hat eine Sequenz, statt weiter zufaellig zu wachsen.
- AS6-Refactors bleiben bewusst Folgearbeit; `0.11.x` endet mit Boundary-Vertrag, Backend-Inventar und Sequencing-Plan.

## Version `0.12.x`: Development Orchestration v1

Diese Version macht aus dem manuellen Alice/Bob/Charlie-Prozess ein Produktfundament.

Aus dem `0.11.x`-Durchlauf ist die wichtigste Produktluecke klar: Alice und Bob koennen kleine Slices sauber abarbeiten, aber der Master-/Heartbeat-Layer muss `done`, `blocked`, `handoff` und naechste Slices maschinenlesbar weiterfuehren, statt nach jedem erfolgreichen Slice wieder manuell angeschoben zu werden.

### Reihenfolge

1. `OR1-plan-graph-store`
2. `OR2-agent-run-store`
3. `OR3-thread-lifecycle-bridge`
4. `OR4-heartbeat-coordinator`
5. `OR5-quality-gates`
6. `OR6-mini-dashboard`
7. `OR7-e2e-two-agent-smoke`

### Alice/Bob/Charlie Matrix

| Slice | Alice | Bob | Charlie | Parallelregel |
| --- | --- | --- | --- | --- |
| `OR1-plan-graph-store` | Status-/Dashboard-Feldvertrag | Plan/Slice Store + validation | Contract Freeze | ja |
| `OR2-agent-run-store` | Agent Status UX | AgentRun Modell, role/model/sandbox fields | prueft Agent-Isolation aus `0.11` | ja |
| `OR3-thread-lifecycle-bridge` | Handoff-Texte | read/send/resolve bridge | blockt mehrdeutige Threads | ja |
| `OR4-heartbeat-coordinator` | sichtbare Run-Zustaende | Automation Lifecycle | ueberwacht erste echte Runs | bedingt |
| `OR5-quality-gates` | Gate-Lens | Test/Git/Evidence Backend | entscheidet `claimed` vs `verified` | ja |
| `OR6-mini-dashboard` | UI | Status API | Browser-/API-Smoke | ja nach API Contract |
| `OR7-e2e-two-agent-smoke` | Demo/Runbook | Dummy/real thread smoke | Abschluss, Commit, Push | nein, Abschluss gemeinsam |

### Definition of Done `0.12.x`

- Plan Graph kann gespeichert und exportiert werden.
- Alice/Bob-aehnliche Runs sind Agent Runs, nicht Sonderlogik.
- Heartbeat kann weiter anstossen und sich selbst beenden.
- Quality Gates speichern Tests, Commits, Evidence.
- Nutzer sieht Fortschritt, Blocker und naechsten Schritt.

## Version `0.13.x`: Memory Scale Foundation

Diese Version sorgt dafuer, dass grosse Datenmengen fluessig bleiben.

### Reihenfolge

1. `MS1-store-interfaces`
2. `MS2-diagnostics-layer`
3. `MS3-query-budgets`
4. `MS4-postgres-pgvector-schema`
5. `MS5-import-export-migration-proof`
6. `MS6-progressive-graph-api`
7. `MS7-ops-homeserver-runbook`

### Alice/Bob/Charlie Matrix

| Slice | Alice | Bob | Charlie | Parallelregel |
| --- | --- | --- | --- | --- |
| `MS1-store-interfaces` | keine UI, nur Begriffe pruefen | Store Interfaces und Adapter | verhindert Big-Bang-Migration | bedingt |
| `MS2-diagnostics-layer` | Health/Lens-Wording | Metrics: ingest/query/job/ui/storage | definiert Performance-Gates | ja |
| `MS3-query-budgets` | Partial-/Clipped-UX | Limits, cursors, timeout behavior | Regression-Gates | ja |
| `MS4-postgres-pgvector-schema` | Backup/Migration-Erklaerung | Schema und Migration Draft | Architekturentscheidung | bedingt |
| `MS5-import-export-migration-proof` | Nutzer-/Ops-Runbook | Export/Import Vergleich | Go/No-Go fuer Runtime Switch | nein |
| `MS6-progressive-graph-api` | Graph-Lens fuer Ausschnitte | Graph endpoints mit Budgets | Payload-/Browser-Smokes | ja |
| `MS7-ops-homeserver-runbook` | Homeserver-Doku | Docker/Postgres/Backup Tests | Risiko-/Restore-Pruefung | ja |

### Definition of Done `0.13.x`

- Keine Memory-/Graph-API laedt unbegrenzt alles.
- Jede teure Query meldet Timing, Counts und Clipping.
- Postgres ist als Wahrheit entworfen; Accelerators bleiben optional.
- Migration ist export/import-basiert, nicht dauerhaft Dual-Write.
- MiniPC/Homeserver-Betrieb hat klare Grenzen und Backups.

## Version `0.14.x`: Lightweight Memory Maintenance

Diese Version startet nach `0.13.x`, bevor weitere Source Provider oder Research-Tracks groesser werden.

Ziel: RAPTOR/GraphRAG-Maintenance funktioniert mit einem kleinen lokalen Maintenance-Modell unter 2 GB RAM, weil die Engine alle schweren Arbeiten ueber Postgres/pgvector, Jobs, Budgets, K-Means/Bisecting-K-Means und Validatoren erledigt. Das kleine Modell bekommt nur kleine, vorbereitete Aufgaben und entscheidet nie globale Wahrheit.

### Leitregeln

- Das kleine Modell ist Worker, nicht Denkzentrale.
- Clustering laeuft algorithmisch, nicht im LLM.
- K-Means oder Bisecting K-Means ist der erste produktionsnahe Derived-Layer.
- Cluster, Summaries und Graph-Maintenance sind Derived Data und rebuildbar.
- Jede Summary braucht Quellen- oder Chunk-Refs.
- Jede unsichere Aenderung erzeugt ein Review Item.
- Groessere Modelle sind Fallback/Reviewer, nicht Default.
- Obsidian bleibt Visualisierungsschicht, nicht Wahrheit.

### Reihenfolge

1. `LM1-maintenance-worker-contract`
2. `LM2-derived-cluster-run-model`
3. `LM3-kmeans-clustering-proof`
4. `LM4-evidence-bound-summary-worker`
5. `LM5-graph-maintenance-worker`
6. `LM6-small-model-evaluation-gates`
7. `LM7-fallback-routing`

### Alice/Bob/Charlie Matrix

| Slice | Alice | Bob | Charlie | Parallelregel |
| --- | --- | --- | --- | --- |
| `LM1-maintenance-worker-contract` | Erklaert, was kleine Modelle duerfen/nicht duerfen | Job-/Task-Schema fuer bounded Maintenance | Stop-Regeln fuer globale Wahrheit | ja |
| `LM2-derived-cluster-run-model` | Cluster Runs, Rebuilds, Versionen erklaeren | `cluster_runs`, `cluster_nodes`, `cluster_memberships` Modell | Derived-vs-Truth-Gate | ja |
| `LM3-kmeans-clustering-proof` | K-Means/Bisecting-K-Means Runbook | isolierter K-Means Proof mit kleinen Fixtures | Qualitaets-/Budget-Review | bedingt |
| `LM4-evidence-bound-summary-worker` | Review-Sprache fuer Quellenpflicht | Summary Task: max chunks/tokens/source refs/`needs_review` | Drift-/Evidence-Gate | ja |
| `LM5-graph-maintenance-worker` | Regeln gegen halluzinierte Kanten | Entity/Edge Task mit confidence, provenance, dedupe | Review-Queue-Gate | ja |
| `LM6-small-model-evaluation-gates` | Kriterien fuer "kleines Modell reicht" | Evaluation fuer drift, evidence, JSON, confidence | Fallback-Entscheidung | ja |
| `LM7-fallback-routing` | Produktlogik fuer groesseres Modell | Routing: maintenance model, fallback model, retry/backoff, cost budget | Abschluss- und Kosten-Gate | bedingt |

### Definition of Done `0.14.x`

- Kein Maintenance-Job laedt unbounded Memory, Graph oder Cluster.
- Ein Modell unter 2 GB RAM kann produktiv kleine Maintenance-Pakete bearbeiten.
- Cluster und Summaries sind versioniert, rebuildbar und evidence-bound.
- Unsicherheit fuehrt zu Review oder Fallback, nicht zu stillen Writes.
- K-Means/Bisecting-K-Means ist als erster RAPTOR-kompatibler Produktionspfad nachweisbar; GMM/UMAP bleibt Research.

## Version `0.15.x`: Odysseus Lens UI & Memory Interaction

Detailplan: `docs/plans/odysseus-lens-ui-memory-interaction.md`.

Ziel: Odysseus Lens wird von einer Sammlung einzelner Tool-Buttons zu einer klaren Arbeitsoberflaeche ueber dem Memory-System. Graph/Lens bleibt ein Modus innerhalb des aktuellen Dokuments und wird kein neuer Hauptbutton. Memory wird sichtbar in Lesen/Auslesen und Eintragen/Pflegen getrennt.

### Reihenfolge

1. `LENS0-ux-contract`
2. `LENS1-shell-stability`
3. `LENS2-memory-read-write-tabs`
4. `LENS3-tag-chip-system`
5. `LENS4-document-intelligence-bar`
6. `LENS5-review-audit-spark-redesign`
7. `LENS6-odysseus-lens-rename-plan`

### Alice/Bob/Charlie Matrix

| Slice | Alice | Bob | Charlie | Parallelregel |
| --- | --- | --- | --- | --- |
| `LENS0-ux-contract` | Navigation, Farbregel, Button-Hierarchie, 8px-Raster, Zustaende | keine Codearbeit | Worktree pruefen, Hotfiles sperren, Akzeptanzkriterien finalisieren | ja, Alice/Charlie |
| `LENS1-shell-stability` | Fullscreen, Minimize, New Chat, Overlay-Verhalten beschreiben | Z-Index, Fullscreen Toggle, New-Chat-Minimize, Audit-Close-Reflow | Static/UI-Tests, Regression gegen Obsidian-Smokes | nein |
| `LENS2-memory-read-write-tabs` | Texte und Flow fuer `Gedaechtnis Lesen` und `Gedaechtnis Pflegen` | Tabs/Panel-State, alte Review/Audit/Spark-Zugaenge einsortieren | Routen-/Tool-Kompatibilitaet pruefen | bedingt |
| `LENS3-tag-chip-system` | Chip-Verhalten, Normalisierung, Duplikate, Tastatur | Autocomplete und wiederverwendbare Chips in Memory/Spark/Header | Tag-UI-Vertraege pruefen | nein |
| `LENS4-document-intelligence-bar` | Metadatenmodell: Typ, Projekt, Status, Datum, Tags, Beziehungen, Memory-State | kompakte Header-Bar, Frontmatter/Statusdaten anbinden | keine erfundenen RAPTOR-/GraphRAG-Signale | bedingt |
| `LENS5-review-audit-spark-redesign` | Review Queue, Insights und Diagnostics sprachlich/visuell definieren | Memory Review -> Pflegen, Spark -> Insights, Audit -> Diagnostics | Backward Compatibility pruefen | nein |
| `LENS6-odysseus-lens-rename-plan` | Naming-/Migrationsvertrag | Aliasstrategie vorbereiten, keine harte Umbenennung | Rename in separaten Gate schneiden | nein |

### Definition of Done `0.15.x`

- Memory Lesen und Memory Pflegen sind klar getrennte Nutzerzustaende.
- Insights, Diagnostics und Activity sind eigene Zustaende statt verstreute Tool-Buttons.
- Graph/Lens bleibt View-Mode/Schieberegler innerhalb des Dokuments.
- Pro Ansicht gibt es genau einen Primaerbutton.
- Component States, Labels, Inline-Validierung, 44px Klickziele und 8px-Raster sind in den UX-Vertraegen und UI-Smokes beruecksichtigt.
- Bestehende Backend-Routen und Tools bleiben kompatibel.
- `plugins/obsidian/frontend/main.js`, `plugins/obsidian/frontend/style.css` und `tests/test_obsidian_sidebar_static.py` wurden nicht parallel bearbeitet.

## Version `0.16.x`: Isolated Image Tools Worker

Detailplaene:

- `docs/plans/image-tools-worker-contract.md`
- `docs/plans/image-tools-worker-route-integration-contract.md`
- `docs/plans/image-tools-worker-ui-cookbook-contract.md`

Ziel: Background Removal und spaetere Image-AI-Tools destabilisieren den Python-3.14-Core nicht. Schwere oder fragile Dependencies wie `rembg` laufen isoliert in einem Worker oder einer separaten Python-3.12/Docker-Umgebung. Odysseus Core spricht nur eine stabile Worker-API und zeigt klare Setup-Fehler statt Serverfehler.

### Reihenfolge

1. `ITW1-image-tools-worker-contract`
2. `ITW2-image-tools-worker-client`
3. `ITW3-route-integration`
4. `ITW4-isolated-image-tools-worker-mvp`
5. `ITW5-cookbook-ui-alignment`
6. `ITW6-telegram-readiness`

### Alice/Bob/Charlie Matrix

| Slice | Alice | Bob | Charlie | Parallelregel |
| --- | --- | --- | --- | --- |
| `ITW1-image-tools-worker-contract` | Worker-Modi, Config, Fehlersemantik, Security beschreiben | keine Codearbeit | Scope und Roadmap-Gate | ja, docs-only |
| `ITW2-image-tools-worker-client` | UI-/Setup-Erwartungen reviewen | `ImageToolsWorkerClient`, Result/Errors, Tests | fokussierte Tests | ja nach Contract |
| `ITW3-route-integration` | Route-Vertrag und stabile Response-Form beschreiben | `/api/image/remove-bg` nutzt Worker-Client statt Core-Dependency | Route-/Regressionstest, Hotfile-Schutz | nein, Route-Hotfile |
| `ITW4-isolated-image-tools-worker-mvp` | Install-/Ops-Hinweise reviewen | isolierter Worker unter `workers/image_tools_worker/` | Compile-/Static-Smoke | ja, isolierter Scope |
| `ITW5-cookbook-ui-alignment` | Cookbook-/Editor-Texte fuer Worker-Setup | keine Frontend-Hotfiles ohne Handoff | UI-Texte gegen Runtime pruefen | bedingt |
| `ITW6-telegram-readiness` | Telegram-Bildaktionssprache | gleicher Worker-Client fuer Telegram spaeter | erst starten, wenn Telegram-Track aktiv ist | nein |

### Definition of Done `0.16.x`

- Odysseus startet ohne `rembg`, `transformers` oder Worker.
- Core-venv bleibt frei von harter `rembg`-Dependency.
- `/api/image/remove-bg` kann einen konfigurierten Worker nutzen und gibt stabile Editor-Antworten zurueck.
- Fehlender Worker fuehrt zu klarer Setup-/Not-configured-Meldung.
- Isolierter Worker-MVP ist dokumentiert und mindestens statisch pruefbar.
- Telegram/Image-Actions nutzen spaeter dieselbe Worker-Client-Schicht statt Route-Scraping.

## Version `0.17.x`: Secure Data Mode & Local-Only Policy

Detailplaene:

- `docs/plans/secure-data-mode-contract.md`
- `docs/plans/data-classification-policy-contract.md`
- `docs/plans/chat-security-state-contract.md`
- `docs/plans/secure-policy-gate-contract.md`

Ziel: Odysseus kann sensible Quellen verarbeiten, ohne dass Inhalte in API-Modelle, externe Embeddings, externe Provider, unsichere Tools oder ungeschuetzte Exporte geraten. Secure Mode ist eine Chat-/Thread-Eigenschaft ab Start und kann nicht nachtraeglich umgeschaltet werden.

Evidence Foundation:

- `SEC1-security-mode-contract`: `a3bde239`
- `SEC2-data-classification-model`: Alice `6bc7c2df`, Bob `d2f5b7b2`, Test `tests/test_data_classification.py` -> `14 passed, 1 warning`
- `SEC3-chat-security-state-model`: Alice `3b63af4b`, Bob `d8156e1f`, Test `tests/test_chat_security_state.py` -> `12 passed, 1 warning`
- `SEC4-policy-gate-model`: Alice `d9d7e613`, Bob `813eee75`, Security-Suite `tests/test_data_classification.py tests/test_chat_security_state.py tests/test_secure_policy_gate.py` -> `36 passed, 1 warning`
- `SEC5-local-only-model-routing`: isoliertes Routing-Gate, Security-Suite inkl. `tests/test_secure_model_routing.py` -> `46 passed, 1 warning`
- `SEC6-sensitive-retrieval-guard`: isolierter Pre-Retrieval-Guard, Security-Suite inkl. `tests/test_sensitive_retrieval_guard.py` -> `56 passed, 1 warning`
- `SEC7-telegram-secure-policy`: isolierte Channel-Policy, Security-Suite inkl. `tests/test_secure_channel_policy.py` -> `66 passed, 1 warning`
- `SEC8-security-audit-runbook`: `docs/plans/secure-data-mode-audit-runbook.md`, Runtime-Hooks bleiben separate sequenzielle Slices

### Reihenfolge

1. `SEC1-security-mode-contract`
2. `SEC2-data-classification-model`
3. `SEC3-chat-security-state-model`
4. `SEC4-policy-gate-model`
5. `SEC5-local-only-model-routing`
6. `SEC6-sensitive-retrieval-guard`
7. `SEC7-telegram-secure-policy`
8. `SEC8-security-audit-runbook`

### Alice/Bob/Charlie Matrix

| Slice | Alice | Bob | Charlie | Parallelregel |
| --- | --- | --- | --- | --- |
| `SEC1-security-mode-contract` | Secure Chat, kein Toggle, Nutzertexte, Stop-Regeln | keine Codearbeit | Akzeptanzkriterien finalisieren | ja |
| `SEC2-data-classification-model` | `public/private/sensitive/secret`, Overrides, Propagation | Klassifikationsmodell und Tests | prueft, dass Vault nicht pauschal sensibel wird | ja nach Contract |
| `SEC3-chat-security-state-model` | Chat-Start, immutable Mode, local-only UX | immutable State-Modell und Tests | Integration ohne bestehende Chats zu brechen | bedingt |
| `SEC4-policy-gate-model` | zentrale Gate-Sprache, Blockgruende, Nutzeroptionen | Decision Layer fuer Sources, Provider, Tools, Export/Logs | Security-Test-Suite | ja |
| `SEC5-local-only-model-routing` | Settings-/UI-Vertrag fuer lokale Modelle und Fallbacks | isoliertes Routing-Gate; keine Provider-Hotfiles ohne Freigabe | done als Vorbereitungsslice, echte Integration separat | bedingt |
| `SEC6-sensitive-retrieval-guard` | Block-/Secure-Chat-Upgrade-Flow | isolierter Pre-Retrieval-Guard ohne Memory/RAG-Hotfiles | done als Vorbereitungsslice, echte Integration separat | nein |
| `SEC7-telegram-secure-policy` | Telegram-Flows und Blocktexte | isolierte Channel-Policy ohne Bot-/Route-Hotfiles | done als Vorbereitungsslice, echter Telegram Hook separat | bedingt |
| `SEC8-security-audit-runbook` | Audit-Runbook, Known Limits, Betriebsregeln | fokussierte Tests/Evidence | done, Runtime-Hooks separat | nein |

### Definition of Done `0.17.x`

- Datenklassifikation fuer Quellen und abgeleitete Artefakte ist modelliert.
- Chat Security State ist immutable und unterscheidet `normal` und `secure`.
- Secure Chats erzwingen local-only Provider-/Embedding-/Tool-Regeln.
- Policy Gate blockiert sensitive Quellen in normalen Chats und externe Pfade in Secure Chats.
- Runtime-Integration in Provider, Retrieval und Telegram erfolgt nur nach separatem Hotfile-Gate.
- Unklare Defaults fuehren zu Block oder Review, nicht zu stiller Freigabe.

## Version `0.18.x`: Automated Agent Handoff & Orchestration MVP

Detailplan: `docs/plans/automated-agent-handoff-orchestration-mvp.md`.

Ziel: Der manuell bewiesene Alice/Bob/Charlie-Prozess wird native Odysseus-Runtime. `0.12.x` hat die Modelle und Contracts vorbereitet; `0.18.x` verdrahtet sie mit echter Persistenz, Thread-Zuordnung, Handoff-Parsing, Heartbeat-Ausfuehrung, Quality Gates und Dashboard-Sicht.

### MVP-Pfad

```text
Approved Plan Graph -> Agent Run created -> Thread assigned -> Heartbeat reads status -> Dispatches next safe slice -> Handoff parsed -> Quality Gates run -> Dashboard shows verified status
```

### Reihenfolge

1. `AUTO0-roadmap-integration`
2. `AUTO1-persistent-orchestration-store`
3. `AUTO2-thread-registry-and-bridge`
4. `AUTO3-handoff-parser-and-mailbox`
5. `AUTO4-heartbeat-runtime-loop`
6. `AUTO5-git-test-quality-gates`
7. `AUTO6-mini-orchestration-dashboard-v2`
8. `AUTO7-end-to-end-two-agent-smoke`
9. `AUTO8-n-agent-scaling-design`

### Alice/Bob/Charlie Matrix

| Slice | Alice | Bob | Charlie | Parallelregel |
| --- | --- | --- | --- | --- |
| `AUTO0-roadmap-integration` | Review, ob Nutzerfluss verstaendlich ist | technische Reihenfolge gegen vorhandene Modelle pruefen | Roadmap aktualisieren, Worktree/aktive Slices pruefen | ja, nur Doku |
| `AUTO1-persistent-orchestration-store` | sichtbare Plan-/Run-Zustaende definieren | JSON Registry fuer PlanGraph/AgentRun, keine Runtime-Hooks | done als Vorbereitungsslice | ja, Contract zuerst |
| `AUTO2-thread-registry-and-bridge` | Handoff-/Statussprache fuer unklare Threads | Thread Registry fuer eindeutige Run/Thread-Zuordnung, keine echten Sends | done als Vorbereitungsslice | bedingt |
| `AUTO3-handoff-parser-and-mailbox` | Handoff-Template finalisieren | done: Parser/Validator, Mailbox/Dispatch-Queue, Pflichtfeldfehler | echte Beispiel-Handoffs testen | ja |
| `AUTO4-heartbeat-runtime-loop` | Nutzertexte fuer laufend/wartend/blockiert/gestoppt | done als trockener Tick-Planer mit injizierten Snapshots und Mailbox-Queue; echte Scheduler-/Thread-/Git-Hooks offen | prueft, dass Automation letzter operativer Schritt bleibt | nein, kritisch |
| `AUTO5-git-test-quality-gates` | Gate-Lens fuer rot/gelb/gruen | done als Snapshot-Evaluator fuer Git, Tests, Evidence, Scope und Hotfiles; echte Command-Runner offen | Block/Warn/Pass entscheiden, keine destruktiven Git-Aktionen | bedingt |
| `AUTO6-mini-orchestration-dashboard-v2` | Dashboard-Contract: Fortschritt, Slices, Blocker, naechste Aktion, Gates | done als Backend-Snapshot-Builder aus Registry, Heartbeat, Mailbox und Gates; UI/API-Hook offen | UI-Smoke, Store/Gate-Abgleich | ja nach API-Contract |
| `AUTO7-end-to-end-two-agent-smoke` | done: Demo-Runbook und Known Limits | done: deterministischer Smoke mit Fake-ThreadRefs und injected Evidence | Abschluss-Tests, Go/No-Go dokumentiert | nein |
| `AUTO8-n-agent-scaling-design` | Rollen, Pools, Budgets, Locks UX | done: Agent Pool, Queueing, Budgetfelder, Lock-Modell als Design/Spike | entscheidet, was post-MVP bleibt; keine Agentenfabrik | ja, Planung |

### Definition of Done `0.18.x`

- Plan Graph und Agent Runs sind persistent oder ueber eine Runtime-Registry eindeutig erreichbar.
- ThreadRefs sind eindeutig; Odysseus sendet nie blind in einen unklaren Thread.
- Handoffs werden maschinenlesbar validiert.
- Heartbeat-Loop fuehrt nur sichere Dispatches aus und stoppt bei Ambiguitaet.
- Quality Gates pruefen Git, Tests, Evidence, Scope und Hotfiles real.
- Dashboard zeigt aktive Slices, Blocker, Gate-Status und naechste Aktion.
- E2E-Smoke belegt mindestens zwei Agenten von Plan bis `verified done`.
- N-Agent-Skalierung ist entworfen, aber nicht als unbegrenzte Agentenfabrik freigegeben.

## Version `0.19.x`: Plugin Platform - System Health Checker

Detailplaene:

- `docs/plans/plugin-platform-manifest-policy.md`
- `docs/plans/system-health-checker-plugin.md`
- `docs/plans/system-health-checker-ops-runbook.md`

Ziel: Odysseus bekommt einen eigenen Plugin-Track fuer Homeserver Health. Die
Codebase bleibt nachvollziehbar, weil Host-Monitoring nicht in Lens, Security
oder Image Tools versteckt wird.

Produktentscheidung:

- Odysseus fuehrt keine Host-Kommandos aus dem Container oder der Lens UI aus.
- Ein kleiner Debian Host-Agent sammelt Metriken und stellt bereinigte Snapshots bereit.
- Odysseus konsumiert Snapshots und zeigt Status, Alerts und Telegram-/UI-Zustaende.
- Podman-first, Docker-compatible; kein Docker-only Design.

### Reihenfolge

1. `SHC0-narrative-and-architecture-contract`
2. `SHC1-health-agent-interface`
3. `SHC2-debian-basic-collectors`
4. `SHC3-rule-engine-alert-model`
5. `SHC4-telegram-pull-status`
6. `SHC5-auto-alerting`
7. `SHC6-podman-docker-runtime-adapter`
8. `SHC7-advanced-debian-collectors`
9. `SHC8-odysseus-health-ui`
10. `SHC9-security-and-ops-runbook`

### Alice/Bob/Charlie Matrix

| Slice | Alice | Bob | Charlie | Parallelregel |
| --- | --- | --- | --- | --- |
| `PLUGIN2-manifest-policy-model` | Policy-Text und Review-Regeln | done: Offline-Validatoren fuer Registry und lokale Plugin-Manifeste; statisches lokales Plugin-Audit; Plugin-Release-Gate | Tests, Scope, keine Runtime-Hooks | ja |
| `PLUGIN3-capability-boundary-model` | Plugin-Arten und erlaubte Capabilities als statische Grenze definieren | done: UI/Core/Host-Agent-Grenzen offline pruefbar und in lokales Plugin-Audit eingebunden; direkte Host-/Socket-Zugriffe bleiben verboten | Tests, keine Imports, keine Host-Kommandos | ja |
| `PLUGIN4-local-audit-markdown` | lokale Plugin-Auditdetails fuer Reports lesbar machen | done: statischer Audit als Markdown, ohne Plugin-Import oder Host-Kommandos | Tests, Report-Fundament | ja |
| `PLUGIN5-local-audit-release-brief` | lokale Plugin-Auditdetails im Release-Morgenbrief sichtbar machen | done: Bundle und Morning Brief enthalten lokale Plugin-Auditsektion | Tests, keine Plugin-Ausfuehrung | ja |
| `SHC0-narrative-and-architecture-contract` | NDD-Contract, Nutzerfluesse, Begriffe, Statussprache | Debian/Podman/Docker Machbarkeit read-only pruefen | Roadmap einordnen, aktive Slices/Worktree pruefen | ja, docs/read-only |
| `SHC1-health-agent-interface` | UX-Vertrag fuer Health-Zustaende und UI-Snapshots | `HealthSnapshot`, `CollectorStatus`, `AlertSummary` Modelle | done: Plugin-Scaffold + offline Snapshot | ja |
| `SHC2-debian-basic-collectors` | Setup-/Unknown-State Texte | CPU/RAM/Load/Uptime/Disk Collector-Modelle mit Mockable Inputs | done: keine Host-Kommandos, nur Normalisierung | ja nach Contract |
| `SHC3-rule-engine-alert-model` | Alert-Texte und Handlungsempfehlungen | Rule Engine, Severity, Cooldown, Dedupe, Recovery | done: active/cooldown/recovered Events | ja |
| `SHC4-telegram-pull-status` | Telegram Command-Vertrag, Allowlist-Texte | parse/authorize/render Adapter ohne Token oder Netzwerk | done als Vorbereitungsslice, echter Bot separat | bedingt |
| `SHC5-auto-alerting` | Alert Copy, Recovery Copy, Eskalationslogik | Dispatch Plans fuer Active/Cooldown/Recovery ohne Netzwerk-Send | done als Vorbereitungsslice | bedingt |
| `SHC6-podman-docker-runtime-adapter` | Runtime unknown/offline Nutzertexte | Podman-first/Docker-fallback Command Plans ohne Socket oder CLI-Ausfuehrung | done als Vorbereitungsslice | ja |
| `SHC7-advanced-debian-collectors` | Setup-Hinweise fuer fehlende Pakete/Rechte | Temperatur, SMART, Updates, Reboot Normalisierung ohne CLI-Ausfuehrung | done als Vorbereitungsslice | bedingt |
| `SHC8-odysseus-health-ui` | Ampel, Alerts, Collector unknown/offline UI-Contract | Plugin-Seite liest `/health` Snapshot, ohne Host-Kommandos | done als Offline-UI-Vorstufe | bedingt |
| `SHC9-security-and-ops-runbook` | Runbook, Betriebsnarrativ, Nutzerregeln | Ops-/Security-Runbook fuer Host-Agent-Follow-up | done | nein |

### Definition of Done `0.19.x`

- System Health Checker ist als eigener Plugin-Track nachvollziehbar.
- Plugin-Manifeste und Registry-Eintraege sind offline policy-pruefbar, ohne Plugin-Code zu importieren.
- Lokale Plugin-Ordner koennen statisch auditiert werden, ohne `setup()` oder Top-Level-Code auszufuehren.
- Plugin-Registry und lokale Plugin-Ordner koennen als gemeinsames Release-Gate bewertet werden.
- `plugins/system_health_checker/` existiert als eigener Plugin-Scope.
- Host-Agent und Odysseus-Container sind strikt entkoppelt.
- Debian Basic Health Snapshot ist schema-stabil und offline-/unknown-sicher.
- Rule Engine bewertet Thresholds mit Cooldown, Dedupe und Recovery.
- Telegram `/status` und `/alerts` sind sicher geplant oder implementiert.
- Podman-first Runtime Adapter ist vorbereitet, Docker bleibt kompatibler Fallback.
- Keine Root-/Socket-Abkuerzung landet im Odysseus-Core.
- Host-Agent-Installation und echte Host-Reads sind als Follow-up geplant, nicht im Core versteckt.

## Version `0.20.x`: Nextcloud Source Provider

Diese Version startet erst, wenn Nextcloud auf dem Homeserver laeuft.

### Reihenfolge

1. `NC1-source-policy`
2. `NC2-local-sync-provider`
3. `NC3-ledger-integration`
4. `NC4-review-generated-published-folders`
5. `NC5-optional-nextcloud-bridge-decision`

### Alice/Bob/Charlie Matrix

| Slice | Alice | Bob | Charlie | Parallelregel |
| --- | --- | --- | --- | --- |
| `NC1-source-policy` | Rechte-/Ordner-/Review-UX | technische Provider-Annahmen | Security-Modell | ja |
| `NC2-local-sync-provider` | Nutzerpfad dokumentieren | lokaler Sync Source Provider | Pfad-/Delete-Risiko pruefen | bedingt |
| `NC3-ledger-integration` | Source Cards | Ledger Scanner | Rebuild-/Staleness-Gate | ja |
| `NC4-review-generated-published-folders` | UI/Runbook | write-limited Staging/Generated/Published | Policy Review | ja |
| `NC5-optional-nextcloud-bridge-decision` | Bedarf sammeln | Bridge/App-Prototyp nur bei Bedarf | Entscheidung gegen Overengineering | nein |

## Post-1.0 Research Tracks

| Track | Startbedingung | Warum nicht jetzt |
| --- | --- | --- |
| Qdrant | pgvector ist laut Diagnostics wiederholt zu langsam | sonst mehr Infrastruktur ohne Messproblem |
| Kuzu | Postgres-Graph reicht laut Diagnostics nicht | Graph-Accelerator ohne Query-Budget waere falsch |
| UMAP/GMM/adRAP | Basis-Retrieval hat belegte Qualitaetsluecke | zu schwer als erster Schritt, Derived Data muss zuerst stabil sein |
| Frontend Framework Migration | Vanilla-JS blockiert konkret neue UI-Arbeit | grosser Umbau, erst nach klaren Modulgrenzen |

## Arbeitsregeln fuer parallele Agents

### Harte Regeln

- Kein Agent bearbeitet dieselbe Datei parallel ohne ausdruecklichen Handoff.
- Keine destruktiven Git-Kommandos ohne Freigabe.
- Jeder Slice nennt erlaubte Dateien, verbotene Dateien, Tests und Handoff.
- Alice und Bob committen ihre Slices; Charlie merged, prueft und pusht nur nach sauberem Status.
- Wenn Charlie waehrend Alice/Bob arbeitet, implementiert Charlie nicht in deren Hot Files.

### Hot Files

Aktuell besonders vorsichtig behandeln:

- `plugins/obsidian/frontend/main.js`
- `plugins/obsidian/frontend/style.css`
- `tests/test_obsidian_sidebar_static.py`
- `plugins/obsidian/backend/routes.py`
- `plugins/obsidian/backend/query_layer.py`
- `plugins/obsidian/backend/model_router.py`
- `README.md`
- `docs/obsidian/00-priorisierte-roadmap.md`
- `docs/plans/*.md`

### Handoff-Format

```text
Agent: Alice|Bob|Charlie
Slice: <id>
Status: done|blocked|failed|handoff
Commit: <sha oder none>
Geaenderte Dateien:
Tests:
Evidence:
Blocker:
Naechster Slice:
Handoff fuer:
```

## Charlie Operating Loop

Charlie ueberwacht, waehrend Alice und Bob arbeiten:

1. Threads lesen und Status erfassen.
2. Worktree pruefen.
3. Hot-File-Konflikte erkennen.
4. Wenn ein Agent nach einem Slice stoppt und naechster Slice eindeutig ist, Agent weiter anstossen.
5. Wenn Handoff fehlt oder mehrdeutig ist, stoppen und Nutzer informieren.
6. Nach beiden Pfaden: Tests laufen lassen, Worktree saeubern, Merge/Commit/Push.
7. Fortschritt im Nutzerformat melden.

Charlie implementiert nur selbst, wenn:

- ein Cross-Cutting-Glue-Slice explizit Charlie gehoert,
- Alice/Bob nicht in denselben Dateien arbeiten,
- oder die Agenten fertig sind und Abschlussintegration noetig ist.

## Naechste konkrete Sequenz

Jetzt:

1. Die zwei offenen externen 1.0-Gates bleiben zuerst sichtbar: modellgestuetzter Provider-/Fallback-Antwortlauf und Export/Import/Rebuild mit kleinem Test-Vault.
2. Keine schreibenden Vault-Aktionen gegen echte Nutzerartefakte ohne expliziten Test-Vault.
3. Charlie haelt Roadmap, Worktree und Testplan sauber.

Danach:

1. Alice bekommt `LENS0-ux-contract`.
2. Bob wartet auf den UX-Vertrag oder bekommt nur einen klaren, nicht kollidierenden Shell-Smoke.
3. Nach `LENS0` startet `LENS1-shell-stability` sequenziell, weil `plugins/obsidian/frontend/main.js`, `plugins/obsidian/frontend/style.css` und `tests/test_obsidian_sidebar_static.py` Hotfiles sind.
4. Nach Lens-Stabilisierung startet `AUTO1-persistent-orchestration-store`; `AUTO0-roadmap-integration` ist mit dieser Master-Roadmap erledigt.

Nicht jetzt:

- keine neue Postgres-Migration
- keine Nextcloud-Implementierung
- keine Qdrant/Kuzu/UMAP/GMM-Arbeit
- keine grosse Frontend-Framework-Migration
- keine harte Obsidian-Plugin-Umbenennung
- keine autonome Agentenfabrik ohne Approval, Gates und Stop-Regeln

## Master Definition of Done

Diese Roadmap ist erfuellt, wenn:

- `0.10.x` stabil und evidence-ready ist.
- `0.11.x` Agent State Isolation und Context Capsules als Fundament bereitstellt.
- `0.12.x` Alice/Bob/Charlie-Orchestration als Produktfunktion beweist.
- `0.13.x` Memory-Scale-Foundation mit Diagnostics und Budgets vorbereitet.
- `0.14.x` Lightweight Memory Maintenance mit kleinem Modell, bounded Jobs und evidence-bound Summaries beweist.
- `0.15.x` Odysseus Lens UI & Memory Interaction die Memory-Oberflaeche in Lesen, Pflegen, Insights, Diagnostics und Activity ordnet.
- `0.16.x` Isolated Image Tools Worker Background Removal und spaetere Image-AI-Tools vom Core entkoppelt.
- `0.17.x` Secure Data Mode sensible Quellen, Secure Chats und local-only Policy mit Gates absichert.
- `0.18.x` Automated Agent Handoff & Orchestration den Alice/Bob/Charlie-Prozess von Approved Plan bis `verified done` nativ ausfuehrt.
- `0.19.x` System Health Checker als eigenen Plugin-Track mit Host-Agent-Grenze vorbereitet.
- `0.20.x` Nextcloud erst nach Infrastruktur-Readiness sauber als Source Provider anschliesst.
- `1.0.0` nicht nach Bauchgefuehl, sondern nach Evidence freigegeben wird.
