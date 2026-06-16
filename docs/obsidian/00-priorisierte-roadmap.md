# Odysseus Memory System: Memory-first Roadmap mit Obsidian Lens

Stand: 2026-06-16

Dieses Dokument ist die einzige aktive Planungsquelle fuer das Odysseus-Memory-System und die Obsidian-Lens. Die frueheren Einzelplaene zu Import/Export, Tags, Graph, Dateibaum, Editor, Settings, KI-Steuerung, Tests, Migration und Phase-Status wurden hier konsolidiert. Alte Planungsdateien sollen nicht wiederbelebt werden; neue Erkenntnisse gehoeren in diese Roadmap.

## Strategischer Beschluss

Der neue Produkt-Nordstern ist **Memory-first**:

- Das fragbare, korrekt zitierende und visualisierbare Langzeitgedaechtnis ist der Produktkern.
- Obsidian ist nicht mehr alleinige Source of Truth, sondern eine Lens fuer Quellen, Review, Kuratierung, Graphen und publizierte Views.
- RAPTOR-/GraphRAG-/Embedding-/Entity-Graph-Daten sind abgeleitete Indexdaten und duerfen automatisch gebaut, geloescht, repariert und neu aufgebaut werden.
- Menschliche Markdown-Quellen bleiben geschuetzt: kein stilles Append, Merge oder Rewrite in Nutzerartefakte ohne Policy-Gate, Staging oder Review.

### Schichtenmodell

| Schicht | Rolle | Schreibrechte | Verlust-/Rebuild-Modell |
| --- | --- | --- | --- |
| Source Layer | User-Markdown, Chats, Dokumente, Captures, Attachments | Nutzer oder explizite Capture-Policy | kritisch, wird gesichert |
| Derived Memory Layer | Ledger, Chunks, Embeddings, Entity Graph, Summaries, Retrieval-Indizes | Hintergrundsystem | rekonstruierbar |
| Query Layer | Hybrid Retrieval, postQFRAP-light, Quellenpflicht, Confidence | read-only zur Query-Zeit | fluechtig oder cachebar |
| Obsidian Lens | Graph, Review Queue, Source Views, publizierte Notizen | UI/Review/Policy | darf aus Index abgeleitet sein |

### Neue 1.0-Definition

`1.0.0` bedeutet nicht mehr "Obsidian-Plugin hat alle Komfortfeatures". `1.0.0` bedeutet:

- Der Server baut automatisch einen abgeleiteten Memory-Index aus Quellen.
- Fragen werden aus dem Memory-Index mit Quellen und Confidence beantwortet.
- Obsidian visualisiert Quellen, Graph, Cluster, Review Queue und publizierte Views.
- Background-Jobs koennen kosteneffizient laufen, ohne echte Nutzer-Notizen riskant umzuschreiben.
- Auto-Apply ist nur fuer sichere, policy-erlaubte Aktionen moeglich; riskante Promotionen landen in Review/Staging.
- Der Query Layer kann mit DeepSeek oder kompatiblem Modellpfad antworten und faellt bei Modellproblemen kontrolliert auf lokale oder extractive Antworten zurueck.

Aktueller Abstand zum neuen `1.0.0`: **ca. 90-95%**.

Grund: Die Memory-first Backend-Spur ist in Ledger, Derived Index, Query Layer, Answer Lens, Automation und External/Rebuild Evidence gebaut und getestet. Die breiteren Obsidian-/Static-/Context-Regressionen sind ebenfalls gruen. Neu vor `1.0.0` ist der explizite DeepSeek-/Model-Router-Gate mit Graceful Degradation; danach bleibt die echte manuelle Distributions-/Upgrade-Freigabe auf frischem Zielsetup als Release-Handlung.

Pre-1.0 Feature-Gate: `docs/plans/deepseek-model-router-graceful-degradation.md`.

## Neue 1.0-Roadmap

### M0: Pivot Stabilisieren

Ziel: Die bestehende Obsidian-Arbeit als Lens/Foundation sichern und laufende Agenten nicht kollidieren lassen.

- Roadmap und Aufgabenmatrix auf Memory-first ausrichten.
- Laufende Alice-/Bob-Slices abschliessen lassen.
- Obsidian-first P0s nicht wegwerfen, sondern als Lens-/Safety-Foundation einordnen.
- Keine neuen automatischen Writes in menschliche Notizen einfuehren.

Exit:

- Diese Roadmap benennt Memory-first als massgebliche Richtung.
- Alice und Bob haben klare, nicht ueberlappende Spuren.

### M1: Memory Ledger und Source Tracking

Ziel: Odysseus weiss automatisch, welche Quellen existieren, was sich geaendert hat und was neu indiziert werden muss.

- SQLite-Ledger fuer Quellen, Hashes, mtime, indexed_at, status, chunk_count.
- Source-Typen: Markdown, Chat/Capture, Dokument, Attachment-Metadaten.
- Change Detection fuer create/update/delete.
- Rebuild-/repair-faehiger Status: `pending`, `indexed`, `failed`, `stale`.
- Kein LLM-Writeback in Source-Dateien.

Exit:

- Geaenderte Quellen werden deterministisch erkannt.
- geloeschte Quellen entfernen abgeleitete Indexdaten.
- Ledger kann komplett aus Quellen neu aufgebaut werden.

### M2: Derived Memory Index

Ziel: Aus Quellen entsteht automatisch ein abgeleiteter Such- und Graphindex.

- Chunking mit stabilen Chunk-IDs.
- Embeddings pro Chunk.
- Hybrid Retrieval: keyword + vector.
- Entity-/Relationship-Extraktion als low-cost Background-Job.
- Derived Graph mit Provenance zu Quellen und Chunks.
- Indexdaten liegen ausserhalb menschlicher Notizen.

Exit:

- Query kann relevante Chunks mit Quellen holen.
- Graph kann aus Derived Data statt nur aus Markdown-Links gerendert werden.
- Index kann ohne Nutzerinteraktion neu gebaut werden.

### M3: Query Layer mit postQFRAP-light

Ziel: Fragen werden korrekt, belegbar und kontextsparend aus dem Memory beantwortet.

- Retrieval holt initial groessere Kandidatenmenge.
- Query-time Verdichtung ueber gefundene Chunks statt vollem RAPTOR-Rebuild.
- Antwort erfordert Quellenliste und Confidence.
- Cache fuer wiederkehrende Queries oder stabile Subtrees.
- Keine adRAP-/UMAP-/GMM-Pflicht fuer 1.0.

Exit:

- Fragen liefern belegte Antworten mit anklickbaren Quellen.
- Falsche oder schwache Quellen werden als Unsicherheit sichtbar.
- Dynamic Notes koennen ohne teuren Full-Rebuild abgefragt werden.

### M4: Obsidian Lens

Ziel: Obsidian zeigt das Memory, statt selbst der einzige Memory-Kern zu sein.

- Graph-Ansicht kann Memory-Graph-Knoten, Quellen und abgeleitete Cluster anzeigen.
- Review Queue zeigt Capture-/Promotion-Kandidaten aus dem Index.
- Source Views zeigen Ursprung, Chunk, Summary und Confidence.
- Published Views koennen nach Policy als Markdown materialisiert werden.
- Menschliche Notizen bleiben klar von generierten Views unterscheidbar.

Exit:

- Nutzer kann Memory visuell erkunden.
- Nutzer kann aus Review/Staging bewusst kuratieren.
- Obsidian-Lens funktioniert auch, wenn Derived Data neu aufgebaut wurde.

### M5: Automation, Safety und 1.0 Evidence

Ziel: Background-Automation spart Arbeit, bleibt aber kontrollierbar.

- Nacht-/Idle-Jobs fuer Ledger Sync, Indexing, Spark/Health, low-risk Summaries.
- Kostencontroller: Modellrouting, Concurrency-Limits, Backoff, Akku-/CPU-Drosselung.
- Auto-Apply nur fuer create-only, low-risk, review- oder derived-data Aktionen.
- Riskante Aktionen: Append, Merge, Rewrite, Canonical Promotion bleiben policy-gated.
- 1.0 Evidence: externer Install-/Upgrade-Pfad, Rebuild-Test, Query-Accuracy-Smokes, Safety-Gates.

Exit:

- System kann ohne manuelles Ingest aktuell bleiben.
- Auto-Jobs koennen parallel laufen, ohne Nutzerquellen zu beschaedigen.
- 1.0-Go/No-Go basiert auf Evidence, nicht Bauchgefuehl.

### M6: DeepSeek Model Router und Graceful Degradation

Ziel: Memory-Fragen laufen mit DeepSeek oder einem kompatiblen Modellpfad, bleiben quellenpflichtig und fallen bei Providerproblemen auf lokale oder extractive Antworten zurueck.

- Antwortmodi: `auto`, `cloud`, `local`, `extractive`.
- Cloud-Modus sendet nur retrieved Snippets, Quellenlabels und minimale Metadaten, nicht den ganzen Vault.
- Providerfehler, Timeouts und Rate-Limits erzeugen keinen harten 500er, sondern einen sichtbaren Fallback.
- Jede Antwort nennt `answer_mode`, Provider/Modell, Fallback-Grund, Citations, Confidence und Warnungen.
- Tests beweisen den Router mit Fake-/Monkeypatch-Clients ohne echte Netz- oder DeepSeek-Pflicht.

Exit:

- Eine konfigurierte DeepSeek-Query liefert eine synthetisierte, zitierte Antwort.
- Ohne funktionierenden Provider bleibt der heutige sichere extractive Antwortpfad verfuegbar.
- Keine Secrets landen in Status, Response, Cache, Logs oder UI.
- Details und Alice/Bob-Slices stehen im Feature-Plan `docs/plans/deepseek-model-router-graceful-degradation.md`.

## Aufgabenverteilung Alice / Bob

Alice und Bob arbeiten parallel, aber auf unterschiedlichen Schichten. Alice owned die Lens-, UX-, Review- und Produktvertragsschicht. Bob owned Ledger, Index, Query, Background Jobs und Safety.

### Arbeitsmatrix

| Reihenfolge | Alice | Dateien/Scope | Bob | Dateien/Scope | Parallel? |
| --- | --- | --- | --- | --- | --- |
| 0 | Aktiven Testsplit abschliessen | `plugins/obsidian/tests/test_locked_vault_surfaces.py` | Laufende Backend-Slices abschliessen | aktuelle Backend-/Testdateien | ja, bestehende Ownership respektieren |
| 1 | Lens-Produktvertrag schreiben | `docs/obsidian/00-priorisierte-roadmap.md`, Plugin-README spaeter | `M1-memory-ledger` | neue Backend-Module, z. B. `plugins/obsidian/backend/memory_ledger.py` | ja |
| 2 | Review Queue als Lens definieren | `frontend/main.js` spaeter, README/UX-Vertrag | `M2-derived-index` | Chunking, embeddings, derived graph | ja, wenn Frontend/Backend getrennt bleiben |
| 3 | Graph-/Source-Views fuer Memory entwerfen | Obsidian UI/Lens, keine Indexlogik | `M3-query-layer` | retrieval, postQFRAP-light, source citations | ja |
| 4 | Published Views und Staging UX | UI/Review/Docs | `M5-background-jobs` | scheduler, cost controller, queue, safety | ja |
| 5 | 1.0 Release Readiness | release notes, risks, evidence summary | External/Rebuild Proof | install/upgrade, rebuild, query gates | ja mit klarer Evidence-Aufteilung |

### Alice-Slices

| Slice | Ziel | Primaere Dateien | Nicht-Ziele | Testgate |
| --- | --- | --- | --- | --- |
| `A0-finish-active-testsplit` | Aktuelle Locked-Vault-Testarbeit sauber abschliessen | `plugins/obsidian/tests/test_locked_vault_surfaces.py` | kein Memory-Ledger, keine Query Engine | fokussierte Pytests fuer betroffene Tests |
| `A1-memory-lens-contract` | Obsidian als Lens/Review/Visualisierung produktvertraglich beschreiben | `docs/obsidian/00-priorisierte-roadmap.md`, spaeter `plugins/obsidian/README.md` | keine Backend-Indexlogik | Doku-Konsistenz |
| `A2-review-queue-lens` | Review Queue UX fuer Captures/Promotions aus Derived Memory definieren | `plugins/obsidian/frontend/main.js`, `plugins/obsidian/README.md` | kein Auto-Merge in Source Notes | UI/static/browser smoke |
| `A3-memory-graph-lens` | Memory-Graph, Quellen und Cluster visuell bedienbar machen | `plugins/obsidian/frontend/main.js`, ggf. Style | kein Entity Extraction Backend | Node/edge contract + browser smoke |
| `A4-published-views` | Generierte Views klar von Nutzerquellen unterscheiden | Frontend, README, ggf. routes fuer read-only views | keine stillen Writes | UI contract + docs |
| `A5-1.0-release-readiness` | Go/No-Go, bekannte Grenzen, Evidence zusammenziehen | Roadmap, README, release notes | keine Produktlogik | Doku/Evidence-Review |
| `A6-source-view-lens-contract` | Quellen-, Chunk-, Confidence- und Provenance-Ansichten als UI-Vertrag definieren | `docs/obsidian/00-priorisierte-roadmap.md`, `plugins/obsidian/README.md` | keine Backend-Routen, keine Indexlogik | Doku-Konsistenz |
| `A7-query-answer-lens` | Antwortkarte fuer Memory-Fragen entwerfen: Antwort, Quellen, Confidence, Unsicherheit, Graph-Jump | `plugins/obsidian/frontend/main.js` nach Handoff, README | keine Query-Engine | UI/static/browser smoke |
| `A8-automation-review-lens` | Nacht-/Idle-Job-Ergebnisse fuer Nutzer verstaendlich machen: was lief, was braucht Review, was ist safe | README, spaeter Frontend | kein Scheduler, kein Cost Controller | Doku + UI contract |
| `A9-nextcloud-source-lens` | AUSGELAGERT: Nextcloud/Dateiarchiv erst nach laufender Nextcloud-Instanz als Source Provider konkretisieren | `docs/plans/nextcloud-source-bridge.md` | kein aktueller 1.0-Scope | Nextcloud laeuft + Source-Provider-Entscheidung |
| `A10-memory-demo-runbook` | Reproduzierbaren 1.0-Demoablauf fuer Memory-first vorbereiten | README, Release Notes, Evidence-Abschnitt | keine Feature-Erweiterung | manueller Demo-Smoke |
| `A11-integration-readiness-audit` | Alice prueft nach Bobs Commit die Lens-/Payload-/Demo-Evidence gegen den echten Backend-Stand | Roadmap, README, Evidence-Notiz | keine Backend-Edits, kein Testfile-Umbau | Doku/Evidence-Review + fokussierte Smokes nach Handoff |
| `A12-deepseek-lens-contract` | Antwortmodi, Fallback-Wording, Datenschutz- und Evidence-Vertrag fuer DeepSeek klaeren | Roadmap, `docs/plans/deepseek-model-router-graceful-degradation.md`, spaeter README | keine Backend-Routen, keine Modellkonfiguration | Doku konsistent mit Bobs Payload-Vertrag |
| `A13-answer-mode-ui` | Answer Lens zeigt Modus, Provider, Fallback-Grund und Warnungen | `plugins/obsidian/frontend/main.js`, statische UI-Tests nach Handoff | keine Backend-Implementierung, keine Secrets im DOM | UI/static smoke nach stabilem B7-Payload |

### A1 Lens-Produktvertrag

`A1-memory-lens-contract` beschreibt den Produktvertrag fuer Obsidian, bevor weitere Lens-UI gebaut wird.

#### Rolle der Obsidian-Lens

- Obsidian ist fuer `1.0.0` keine alleinige Source of Truth mehr.
- Obsidian dient als Lens fuer Quellen, Review, Visualisierung, Kuration und publizierte Views.
- Der Query- und Retrieval-Kern lebt im Derived Memory, nicht in manuell kuratierten Markdown-Links allein.

#### Was die Lens zeigen muss

- Quellen muessen als echte Urspruenge sichtbar bleiben: Datei, Capture, Dokument oder Attachment-Metadaten.
- Derived-Memory-Treffer muessen mit Provenance lesbar sein: mindestens Quelle, relevanter Abschnitt oder Chunk und ein erklaerbarer Match-Kontext.
- Review Queue, Source Views, Graph und spaeter Published Views sind Lens-Surfaces ueber demselben Memory-System, keine voneinander getrennten Sonderwelten.
- Wenn Derived Data neu aufgebaut wurde, darf die Lens kurz leer oder stale sein, aber nicht so tun, als seien Nutzerquellen verschwunden oder veraendert worden.

#### Klare Schreibgrenzen

- Lens-Interaktionen duerfen Derived Data, Review-Status oder explizit materialisierte Views beeinflussen.
- Lens-Interaktionen duerfen menschliche Quellnotizen nicht still appenden, mergen, umschreiben oder "kanonisieren".
- Promotion in langlebige Markdown-Artefakte bleibt ein bewusster, policy-gateter Schritt mit Review oder expliziter Nutzeraktion.
- Auto-Apply ist nur fuer create-only, low-risk oder klar derived-data-bezogene Aktionen zulaessig, nicht fuer riskante Source-Rewrites.

#### UX-Vertrag fuer spaetere Alice-Slices

- `A2-review-queue-lens` baut auf diesem Vertrag auf und behandelt Review Queue als Staging-Surface, nicht als versteckten Apply-Pfad.
- `A3-memory-graph-lens` visualisiert nicht nur Markdown-Links, sondern auch Memory-Knoten, Quellennaehe und abgeleitete Cluster.
- `A4-published-views` muss generierte Views klar von Nutzerquellen unterscheiden, visuell und begrifflich.
- `A5-1.0-release-readiness` prueft die Lens gegen diesen Vertrag und nicht gegen den frueheren "Obsidian ist der Kern"-Stand.

#### Done fuer A1

- Der neue Alice-Pfad `A0 -> A1 -> A2 -> A3 -> A4 -> A5` ist die aktive Lens-Spur.
- Alte Obsidian-first `S...`-Slices bleiben nur als Foundation-/Archivkontext sichtbar.
- Bobs Infrastrukturarbeit und Alices Lens-Vertrag widersprechen sich nicht.

### Alice-Folgeplan nach Abschluss

Alice ist nach dem aktuellen Handoff wieder frei. Damit ihre Geschwindigkeit weiter hilft, ohne Bobs Backend-Spur zu kreuzen, arbeitet Alice ab jetzt in zwei Modi:

- Sofort moeglich: read-only Produktvertrag, Lens-Spezifikation, README-/Roadmap-Klarheit und Demo-/Evidence-Vorbereitung.
- Erst nach Bob-Handoff: Frontend-Integration gegen stabile Endpoints fuer Ledger, Derived Index, Query Layer und Automation.

#### Naechste sichere Alice-Queue

1. `A6-source-view-lens-contract`
2. `A8-automation-review-lens`
3. `A10-memory-demo-runbook`
4. `A7-query-answer-lens` erst starten, wenn Bob Query-Layer-Contract und Routen stabil gruen hat.

Ausgelagert:

- `A9-nextcloud-source-lens` bleibt pausiert, bis die Nextcloud-Instanz tatsaechlich laeuft. Details liegen im eigenstaendigen Plan `docs/plans/nextcloud-source-bridge.md`.

#### A6 Source-View-Lens-Contract

Ziel: Alice beschreibt, wie Nutzer sehen, wo Wissen herkommt und wie stark es belegt ist.

Scope:

- Source Card fuer Datei, Capture, Dokument und Attachment-Metadaten.
- Chunk Card mit Quelle, Titel, Auszug, Hash/Version, Indexed-at und Confidence.
- Provenance-Breadcrumb: Antwort -> Chunk -> Quelle -> optional Graph-Knoten.
- Stale-/Dirty-/Failed-Zustaende als Nutzertext, nicht als technische Rohfehler.

Grenzen:

- Keine Aenderung an `memory_ledger.py`, `derived_index.py`, `query_layer.py`, `memory_status.py` oder `routes.py`.
- Keine neuen Backend-Kontrakte einfuehren; nur auf die committed Backend-Payloads referenzieren oder offene Felder als spaetere UI-Followups markieren.

Lens-Vertrag:

- **Source Card** zeigt den Ursprungstyp klar sichtbar: Markdown-Datei, Chat/Capture, Dokument oder Attachment-Metadaten.
- **Chunk Card** zeigt den verwendeten Ausschnitt als belegbare Arbeitseinheit statt nur "die KI hat das irgendwo her".
- **Confidence** bleibt eine erklaerte Nutzeranzeige, kein magischer Prozentwert ohne Kontext.
- **Provenance Breadcrumb** macht die Kette sichtbar: Antwort -> Chunk -> Quelle -> optional Graph-Sprung.
- **State Text** ersetzt rohe Technikfehler durch lesbare Nutzerzustaende wie `stale`, `dirty`, `failed` oder `needs review`.

Stabile Felder im aktuellen Backend-/Lens-Stand:

- `source_type`
- `source_path` oder ein vergleichbarer lesbarer Quellenbezeichner
- `title`
- `excerpt`
- `confidence`

Spaetere UI-Vertiefung:

- `chunk_id`
- `hash` oder `version`
- `indexed_at`
- Graph-/cluster-spezifische Provenance-Felder
- Antwort-zu-Chunk-Matchgruende ueber mehrere Retrieval-Stufen

Open markers fuer spaetere UI, nicht fuer Bob-Implementierung:

- `ui followup: deeper chunk identity display`
- `ui followup: richer confidence wording`
- `ui followup: stale/dirty/failed mapping as dedicated Source View`
- `ui followup: richer graph jump target format`

#### A7 Query-Answer-Lens

Ziel: Die Memory-Antwort wird user-facing kontrollierbar statt magisch.

Scope:

- Antwortkarte mit Kurzantwort, Quellenliste, Confidence und Unsicherheitsgruenden.
- "Warum diese Quelle?"-Ansicht mit Match-Kontext.
- Absprung in Source View und Graph View.
- Leerer Zustand fuer "nicht genug Evidenz" statt halluzinierter Antwort.

Startbedingung:

- Bob hat Query-Layer-Tests gruen und die Antwort-/Retrieval-Payload stabil dokumentiert.

#### A8 Automation-Review-Lens

Ziel: Automatische Nacht-/Idle-Jobs werden nachvollziehbar, ohne Nutzerartefakte riskant zu veraendern.

Scope:

- Job-Status-Texte: `not_run`, `running`, `ready`, `dirty`, `failed`, `needs_review`.
- Review-Liste fuer sichere Vorschlaege, Staging-Artefakte und blockierte riskante Aktionen.
- Erklaerung, welche Dinge automatisch rebuildbar sind und welche menschliche Freigabe brauchen.
- Kosten-/Ressourcenhinweis fuer MiniPC-Betrieb: Jobs duerfen langsam und opportunistisch laufen.

Grenzen:

- Alice baut keine Scheduler-, Queue- oder Cost-Control-Logik.
- Alice formuliert keine Auto-Apply-Regel, die Source Markdown still appenden, mergen oder rewrite'n wuerde.

Lens-Vertrag:

- Nutzer sollen sehen koennen, **was automatisch lief**, **was nur rebuildbare Derived Data betraf** und **was menschliche Review braucht**.
- Job-Status bleibt ein Nutzertext, kein nackter Worker- oder Queue-Interna-Dump.
- `needs_review` bedeutet: hier existiert ein sicherer Staging-, Queue- oder Vorschlagszustand, aber keine automatische Promotion in menschliche Quellen.
- `dirty` oder `stale` bedeutet: Daten koennen veraltet sein, nicht dass Nutzerquellen beschaedigt wurden.
- `failed` bedeutet: ein Hintergrundlauf ist gescheitert; daraus folgt nicht automatisch, dass Quellen verloren oder korrumpiert sind.

Nutzertexte fuer spaetere UI:

- `not_run`: Noch nicht gelaufen.
- `running`: Wird gerade im Hintergrund aktualisiert.
- `ready`: Letzter Lauf ist fuer diese Ansicht verwendbar.
- `dirty`: Quellen haben sich geaendert; ein neuer Lauf wird gebraucht.
- `failed`: Letzter Lauf konnte nicht erfolgreich abgeschlossen werden.
- `needs_review`: Es gibt Ergebnisse oder Vorschlaege, die bewusst vor menschlicher Freigabe gestoppt wurden.

Safe-vs-review-Erklaerung:

- Rebuildbar und automatisch: Ledger-Sync, Derived Index, Graph-/Chunk-/Embedding-Rebuild, low-risk Status-/Health-Arbeit.
- Review-pflichtig: Promotionen, Append/Merge/Rewrite-nahe Aktionen, Published-View-Freigabe und alles, was menschliche Quellen beruehrt.
- Die Lens muss diese Trennung erklaeren, statt "Automation" als stillen Freifahrtschein erscheinen zu lassen.

Resolved Backend-Payloads:

- `real`: final automation status payload exists.
- `real`: cost/cooldown/backoff signals are exposed.
- `real`: last-run summaries, failures and warnings are exposed.
- `ui followup`: exact visual boundary between `ready` and `needs_review` artifacts can be improved without blocking this roadmap.

#### A9 Nextcloud-Source-Lens

Status: **ausgelagert / pausiert**.

Ziel: Nextcloud und Dateiarchiv werden spaeter als Source Layer verstaendlich in Memory-first eingeordnet, aber erst wenn die Nextcloud-Instanz real laeuft.

Startbedingung:

- Nextcloud laeuft auf dem Homeserver.
- Der Sync- oder Bridge-Zugriff ist praktisch entscheidbar.
- Ein eigener Odysseus-/KI-User oder ein klares Rechtekonzept ist festgelegt.

Scope:

- Nextcloud-Sync-Ordner als Source Provider beschreiben.
- AI-User-/Bridge-Grenzen dokumentieren: read-only Archiv, write-only Staging/Generated/Published nach Policy.
- Source View zeigt spaeter Dateipfad, Sync-Ursprung und Indexzustand.
- Everything/Filesystem-Suche bleibt Optimierung fuer Discovery/Repair, nicht Produktkern.

Grenzen:

- Nicht Teil des aktuellen `1.0.0`-Finalisierungsschnitts.
- Kein Nextcloud-Plugin-Backend in diesem Slice.
- Keine Loesch-, Move- oder Rewrite-Flows fuer echte Nutzerdateien.

Lens-Vertrag:

- Ein Nextcloud-Sync-Ordner ist aus Sicht der Lens ein **Source Provider**, nicht automatisch ein Published- oder Canonical-Bereich.
- Die Lens darf Sync-Ursprung, lesbaren Dateipfad und spaeteren Indexzustand sichtbar machen, ohne Besitz ueber die Datei zu behaupten.
- Dateiarchiv und Sync-Speicher bleiben primaer Discovery-/Source-Layer; Kuration, Review und Published Views bleiben getrennte Schichten.
- "Im Archiv gefunden" bedeutet nicht automatisch "in Memory beantwortbar" und auch nicht "von der KI freigegeben".

Safe-vs-non-goals:

- Sicher fuer diesen Slice: Nutzertexte, README-Klarheit, spaetere Source-View-Platzhalter, Demo-Sprache.
- Nicht Ziel: bidirektionale Sync-Logik, Dateikonflikt-Aufloesung, stilles Verschieben von Nutzerdateien oder automatische Promotion aus Nextcloud nach Canonical.

Ausgelagert bis Nextcloud-Source-Bridge aktiv ist:

- `deferred: source provider identifier for synced/archive sources`
- `deferred: stable source status fields for external files`
- `deferred: exact permission model for read-only archive vs staged writes`

#### A10 Memory-Demo-Runbook

Ziel: Eine spaetere 1.0-Demo kann zeigen, dass Memory-first wirklich funktioniert.

Demo-Pfad:

1. Quelle in den Vault legen; Nextcloud-Sync bleibt ein spaeterer ausgelagerter Source-Provider.
2. Ledger/Index/Query-Status aktualisieren lassen oder manuell triggern.
3. Frage stellen.
4. Antwort mit Quellen und Confidence pruefen.
5. Quelle im Obsidian-Lens-Graph oder Source View nachvollziehen.
6. Optional: Review-/Published-View ohne stillen Source-Rewrite erzeugen.

Exit:

- Runbook unterscheidet klar zwischen fertiger Funktion, Mock/Spec, ausgelagertem Scope und manueller Release-Handlung.
- Demo kann als interne 1.0-Evidence genutzt werden; externe Release-Freigabe bleibt ein manueller Distributionscheck.

Demo-Notiz fuer spaeter:

- `Step 1 - Source appears`: Nutzer sieht, dass eine neue Quelle im Vault- oder Sync-Kontext existiert.
- `Step 2 - Memory updates`: Ledger/Index/Automation zeigen einen nachvollziehbaren Statuswechsel statt "irgendwas KI".
- `Step 3 - Answer is grounded`: Antwort nennt Quellen, Confidence und Unsicherheitsgruende.
- `Step 4 - Lens explains`: Source View, Graph Lens oder Review Queue machen sichtbar, warum die Antwort plausibel ist.
- `Step 5 - Safe publication`: Optionaler Published-View- oder Review-Schritt bleibt von menschlichen Quellen getrennt.

Evidence-Shape:

- Quelle, die verwendet wurde
- gezeigter Statuswechsel oder Trigger
- Frage, die gestellt wurde
- Antwort mit Quellen-/Confidence-Sichtbarkeit
- Lens-Surface, die zur Nachvollziehbarkeit gezeigt wurde
- Markierung, ob der Schritt heute echt, mock-basiert, ausgelagert oder manuelle Release-Handlung war

#### A11 Integration-Readiness-Audit

Ziel: Alice hat den kontrollierten Abschluss der Lens-Seite gegen Bobs finalen Backend-Stand dokumentiert.

Startbedingung:

- Bob hat seine offenen Automation-Aenderungen committed.
- Der interne Evidence-Stand ist gruen.

Scope:

- README/Roadmap gegen echte Backend-Payloads pruefen: Query Answer, Sources, Confidence, Automation Status, Rebuild Proof.
- Alle Handoff-Marker entweder als erledigt, ausgelagert oder manuelle Release-Handlung markieren.
- Demo-Runbook als `real`, `mock/spec` oder `blocked` klassifizieren.
- finalen Alice-Handoff fuer `1.0` schreiben: Lens-ready, nicht ready, Risiken, benoetigte Bob-Evidence.

Grenzen:

- Keine Edits in `memory_automation.py`, `memory_ledger.py`, `derived_index.py`, `query_layer.py`, `memory_status.py`, `routes.py` oder Bobs Tests.
- Keine neuen Features mehr in diese Roadmap ziehen.
- Externes Release-Go bleibt an den manuellen Distributions-/Upgrade-Check gebunden.

Audit-Stand 2026-06-16:

- `real`: Query Layer Payload ist ueber `/memory/query/status` und `/memory/query` live belegt, inklusive `citations`, `confidence`, `confidence_score`, `readiness_gate`, `path_prefix` und Cache-Metadaten; fokussierte Tests `plugins/obsidian/tests/test_query_layer_backend.py` sind gruen.
- `real`: Automation Payload ist ueber `/memory/automation/status` und `/memory/automation/run` live belegt, inklusive `pending_actions`, `cost_controller`, `safety`, `last_run` und Backoff-/Cooldown-Feldern; fokussierte Tests `plugins/obsidian/tests/test_memory_automation_backend.py` sind gruen.
- `real`: Die Lens-Seite fuer Query Answers ist umgesetzt und nutzt die echten Query-Endpunkte read-only.
- `mock/spec`: Source View als eigene UI fuer `source_type`, `chunk_id`, `indexed_at` und tieferen Chunk-Provenance-Drilldown bleibt weiterhin Vertrags-/README-Ebene, nicht final integrierte Runtime-Oberflaeche.
- `blocked`: Nextcloud-spezifische Source-Provider-Felder sind ausgelagert, bis die Nextcloud-Instanz laeuft.
- `real`: External/Rebuild Proof ist jetzt ueber `/memory/rebuild-proof`, `/memory/rebuild-proof/run`, `/memory/external-upgrade-proof` und `/memory/external-upgrade-proof/run` samt fokussiertem Backend-Test belegt; Plain/Encrypted Export-Import plus Rebuild liefern Query-Citations.
- `release manual`: Frische Install-/Upgrade-Evidence auf echter externer Zielumgebung bleibt als manuelle Release-Handlung offen, nicht als Bob-Implementierungsblocker.
- `real`: Breitere `1.0`-Regressionen sind am 2026-06-16 gruen gelaufen: Memory/External-Proof `52 passed`, Obsidian/Static/Context `70 passed`.
- `real`: Interner Evidence-Schnitt ist abgeschlossen: Query, Automation, Rebuild, External Proof und breitere Regressionen sind belegt.

### A5 Release-Readiness-Rahmen

`A5-1.0-release-readiness` zieht keine Produktlogik vor und erklaert keine unfertige Infrastruktur fuer "bereit". Der Slice sammelt nur den Go/No-Go-Rahmen fuer die Lens-Seite und trennt ihn sauber von Bobs Infrastruktur-Evidence.

#### Alice-Evidence fuer 1.0

- Lens-Produktvertrag ist dokumentiert und bleibt die Referenz fuer UI-/Review-/Published-View-Arbeit.
- Review Queue ist in der Lens als Staging-Surface sichtbar und wird nicht als versteckter Apply-Pfad verkauft.
- Graph-Lens zeigt heute mindestens Overview, Current Source und Review Queue als bedienbare Presets.
- Published Views werden in der Lens begrifflich und visuell von Nutzerquellen getrennt.
- Bekannte Lens-Grenzen, Restrisiken und Freigabetexte bleiben in README/Roadmap konsistent.

#### Bob-Evidence fuer 1.0

- Source-/Index-Ledger muss nachweisbar create/update/delete/stale sauber abbilden.
- Derived Index und Query Layer muessen Quellenpflicht, Provenance und Confidence praktisch belegen.
- Background-Automation muss zeigen, dass Derived Data rebuildbar bleibt und Nutzerquellen nicht still umgeschrieben werden.
- External/Rebuild Proof muss Install-/Upgrade-/Repair-/Rebuild-Evidence liefern.
- DeepSeek-/Model-Router muss Modellantworten und kontrollierte Fallbacks ohne Secret-Leaks belegen.

#### Aktueller Go/No-Go-Stand

- `Lens UX`: weitgehend gruen. Alice hat Source View, Automation Review, Nextcloud Source, Demo Runbook und Query Answer Lens vorbereitet.
- `Memory Infrastructure`: gruen im aktuellen Testfenster. Ledger, Derived Index, Query Layer, Automation und External/Rebuild Proof sind gebaut; Query-, Automation- und Rebuild-/Upgrade-Payloads sind gegen fokussierte Tests belegt.
- `Safety`: gruen im aktuellen Evidence-Fenster. Bestehende Obsidian-Sicherheitsgates, Automation-Safety und der External/Rebuild-Proof zeigen derzeit keine stillen Source-Writes; der frische manuelle Distributions-/Upgrade-Pfad bleibt eine Release-Freigabehandlung.
- `Regressionen`: gruen am 2026-06-16. Memory/External-Proof `52 passed`; Obsidian/Static/Context `70 passed`.
- `DeepSeek/Graceful Degradation`: neu offen. Dieser Gate muss vor `1.0.0` zeigen, dass `auto -> cloud -> local -> extractive` kontrolliert funktioniert oder ehrlich auf extractive fallbackt.
- `1.0.0`: **internes Memory-/Evidence-Go fuer den bisherigen Scope**, aber noch kein finaler 1.0-Go. Vor einem externen Release muessen M6 und danach die manuelle Distributions-/Upgrade-Freigabe auf frischem Setup durch sein.

#### Was A5 bei Freigabe zeigen muss

- eine kurze Go/No-Go-Notiz mit Datum, Commit-Stand und offenem/geschlossenem Evidence-Status
- klare Trennung zwischen Lens-ready, Foundation-ready und vollstaendigem Memory-first 1.0
- keine Formulierung, die Published Views, Queue-Artefakte oder Derived Data als menschliche Primarquellen missverstaendlich macht

### Bob-Slices

| Slice | Ziel | Primaere Dateien | Nicht-Ziele | Testgate |
| --- | --- | --- | --- | --- |
| `B0-finish-current-backend-slices` | Aktuelle Auth/Import/Project/Memory Backend-Slices abschliessen | laufende Backend-Dateien | kein Frontend-Lens-Umbau | fokussierte Pytests |
| `B1-memory-ledger` | SQLite-Ledger fuer Sources und Indexstatus bauen | neues `memory_ledger.py`, tests | keine LLM-Summaries | unit tests fuer create/update/delete/stale |
| `B2-derived-index` | Chunking, embeddings, hybrid retrieval und Derived Graph vorbereiten | neue Indexmodule, `context_provider.py` spaeter | keine UI | retrieval/index tests |
| `B3-query-layer-postqfrap-light` | Query-time Verdichtung mit Quellenpflicht | retrieval/query modules | kein adRAP/UMAP/GMM | answer contract tests |
| `B4-background-automation` | Idle/Nacht-Jobs, Queue, Cost Controller | scheduler/job modules, config | keine riskanten Source Writes | job/safety tests |
| `B5-external-rebuild-proof` | Rebuild, Repair, Install/Upgrade, Evidence fuer 1.0 | scripts/docs/tests | keine Feature-Erweiterung | rebuild + install evidence |
| `B6-model-router-core` | DeepSeek-/kompatibles Modellrouting mit Status, Timeout und Fallback bauen | `plugins/obsidian/backend/model_router.py`, Query-/Route-Tests | kein Frontend, kein Model-Install | Fake-Provider-, Timeout- und Secret-Leak-Tests |
| `B7-query-synthesis-integration` | Query Layer um `answer_mode=auto|cloud|local|extractive` erweitern | `plugins/obsidian/backend/query_layer.py`, `plugins/obsidian/backend/routes.py`, Backend-Tests | kein UI-Umbau, kein Indexschema-Rewrite | Cloud success, cloud->local, cloud/local->extractive |

### Parallel-Regeln

- Alice schreibt nicht in Ledger/Indexer/Query-Engine-Dateien.
- Bob schreibt nicht in Lens-Frontend-Dateien, ausser nach explizitem Handoff.
- `plugins/obsidian/tests/` wird pro Slice dateiweise owned; keine zwei Agents in derselben Testdatei.
- Derived Data darf automatisiert veraendert werden; Source Markdown nur ueber Policy, Review oder explizite Nutzeraktion.
- adRAP/UMAP/GMM ist nicht Teil des ersten Memory-first 1.0, sondern spaetere Optimierung.

### Abschluss-Auftraege

Alice und Bob sind fuer den bisherigen Memory-first Abschluss fertig. Der neue Pre-1.0-Gate ist jetzt der erste offene Block.

- Bob startet `B6-model-router-core` und danach `B7-query-synthesis-integration`.
- Alice startet parallel `A12-deepseek-lens-contract`; `A13-answer-mode-ui` erst nach Bobs Payload-Handoff.
- Keine anderen neuen Feature-Followups in diese Roadmap ziehen, bis M6 gruen ist.
- Offene spaetere UI-Vertiefungen bleiben als Followups sichtbar, blockieren aber nicht M6.
- Nextcloud bleibt ausgelagert, bis die Instanz laeuft.
- Postgres/pgvector, Diagnostics, Qdrant/Kuzu und UMAP/GMM gehoeren in die Nachfolge-Roadmaps.

## Bestehende Obsidian-Foundation

Die bisherige Obsidian-Plugin-Arbeit bleibt die Grundlage fuer Source Management, Lens, Review und Visualisierung. Sie ist kein Fundament-Prototyp mehr, sondern ein natives Odysseus-Drop-in-Plugin mit:

- eigenem Plugin-Manifest und FastAPI-Routen unter `/api/plugins/obsidian/...`
- dockbarer, als Overlay nutzbarer und fullscreen-faehiger UI
- Markdown-Dateibaum, Editor, Preview, Suche, Tags und Wiki-Links
- Vault-Passwortschutz, ZIP-Import/Export, History und Undo
- Agent-Tools fuer Vault-, Graph-, Projektplanungs- und Memory-Review-Aktionen
- KI-Projektplanung mit Preview, Streaming, Sessions und Apply
- Memory Review mit Save-to-Obsidian-Preview und Apply
- Cytoscape-basiertem Graph-Renderer mit SVG-Fallback

Der fruehere Obsidian-first RC bleibt als Foundation-Meilenstein relevant. Seine offenen Punkte werden ab jetzt danach bewertet, ob sie die Memory-first 1.0-Richtung stuetzen: Lens, Safety, Source Management, Review und Visualisierung.

## Aktueller Status

### Erledigt

- Plugin-Struktur liegt unter `plugins/obsidian/`.
- `plugin.py` enthaelt Manifest, `setup(ctx)`, Router-Registrierung und Agent-Tool-Registrierung.
- `plugin.json` und `plugin.py` beschreiben Name, Version `0.10.0-rc.1`, Frontend und UI-Entry.
- UI-Entry ist `/api/plugins/obsidian/app`.
- Frontend-Assets werden ueber `/api/plugins/obsidian/web/{filename:path}` ausgeliefert.
- Der alte direkte Plugin-Loader-Ansatz ist nicht mehr Zielarchitektur.
- Benutzerbezogene Vaults liegen standardmaessig unter `data/obsidian_vaults/<owner>`.
- `OBSIDIAN_VAULT_DIR` kann ein eigenes Vault-Template inklusive `{owner}` setzen.
- Pfad-Traversal wird fuer Vault-Dateien, Assets und Archive blockiert.
- Passwortschutz kann gesetzt, entfernt, gelockt und entsperrt werden.
- ZIP-Export und ZIP-Import sind vorhanden; Export kann passwortgeschuetzt sein.
- Dateien und Ordner koennen gelistet, gelesen, erstellt, aktualisiert, geloescht, verschoben und umbenannt werden.
- Markdown-Suche existiert.
- Mutierende und riskante Agent-Aktionen verlangen `confirm: true`.
- History wird in `.obsidian/history.json` geschrieben.
- Undo existiert fuer sichere Einzelaktionen: Datei erstellen, Datei aktualisieren, Rename/Move, Relationship add/delete.
- Dateibaum, Editor, Autosave, Toolbar, Preview, Suche, Settings-Menue, Import/Export und Passwortaktionen sind in der UI verdrahtet.
- Panel-Modi: Sidebar, Overlay, Fullscreen und Standalone.
- Panel- und Sidebar-Breiten sind resizable und lokal persistiert.
- Autocomplete fuer `[[...]]` und `#...` ist caret-positioniert und wird in Code/URL-Kontexten unterdrueckt.
- Tags werden aus Markdown extrahiert; Datei-Slugs werden als implizite Tags berechnet.
- Hierarchische Tags wie `#project/demo`, `#type/project`, `#status/draft` werden normalisiert.
- Tag-Badges in der Preview sind klickbar und koennen Tag-Meta-Notizen oeffnen oder erstellen.
- Graph-Daten enthalten Markdown-Knoten, Ordnerknoten und Kanten fuer Wiki-Links, Dateinamen-Erwaehnungen, gemeinsame Tags und manuelle Relationships.
- Manuelle Relationships liegen vault-lokal unter `.obsidian/relationships.json`.
- Unterstuetzte Relationship-Typen: `manual`, `relates_to`, `depends_on`, `blocks`, `supports`.
- Cytoscape ist als lokales Asset gebuendelt; SVG bleibt Fallback.
- Graph-Klick auf eine Markdown-Node oeffnet die Notiz; Klick auf die aktuelle Node wechselt zur Dokumentansicht.
- Dateibaum-Klicks im Graph-Modus koennen den Graph neu fokussieren und die aktuelle Notiz hervorheben; isolierte und Full-App-Browser-Smokes fuer diesen Pfad sind dokumentiert gruen. Weitere UX-Haertung wie Zoom/Pan und Root-/Ordner-Sonderfaelle bleibt ein Restthema, aber kein offener Nachweis-Gap mehr.
- Projektplanung unterstuetzt Templates, Prompt-Verbesserung, GameDev-Concept-Draft, Preview, Streaming, Sessions, Konflikterkennung und Apply.
- Memory Review unterstuetzt `memory_only`, `save_to_obsidian`, `append_to_note` und `discard`.
- Agent-Tools decken Kernaktionen, Graph, Relationships, History/Undo, Vault-Sicherheit, Projektplanung und Memory Review ab.

### Teilweise erledigt

- Graph-Filter existieren als kompaktes Panel fuer Node-Typen, Edge-Typen, Tags, Suchbegriffe und Hide/Show/Highlight-Modi.
- Graph-Fokus und aktueller Knoten sind technisch begonnen, aber noch nicht als fertiger UX-Vertrag abgesichert: Tree-Klick soll im Graph bleiben, den Knoten highlighten, optional dorthin zoomen/pannen und Nachbarschaft sichtbar halten. Isolierte Chrome-Smokes bestaetigen sichtbaren Graph-View, Cytoscape-Canvas-Dimensionen, Node-/Edge-Payload und Tree-Klick-im-Graph-Modus mit echter Login-Session. Ein isolierter Browser-Harness bestaetigt, dass Graph-Filter `hidden`/`highlighted` setzen und die aktuelle Node nicht ausblenden oder dimmen. Der Full-App-Graph-Filter-Smoke ist gruen fuer echte App, echte Vault-Daten, Cytoscape, offenes Filter-Panel, Highlight-Suche und Hide-Suche.
- Auth-Verhalten fuer Plugin-UI und Plugin-API ist technisch gepinnt: UI-Loader, App-Shell und Plugin-Web-Assets duerfen unauthentifiziert laden; Plugin-Datenrouten laufen weiter durch AuthMiddleware, damit `request.state.current_user` fuer `require_user()` vorhanden ist. Chrome-Smokes fuer App-Shell, CSP-kompatiblen Bootstrap, unauthentifizierte Datenroute-401, echten Browser-Login und authentifizierte Vault-Datenroute sind gruen.
- Large-Vault-Performance hat Fixtures, Baselines und jetzt auch ein quantifiziertes RC-Gate fuer den Graph-Build auf einer deterministischen 120-Note-Fixture. Offene Restfrage bleibt die Uebertragbarkeit auf deutlich groessere reale Vaults.
  Beobachtete 2026-06-16-Baseline auf der RC-Fixture: Median `418.35 ms`, Worst Sample `452.65 ms`, Gate `700/1200 ms`, Headroom `281.65/747.35 ms`, fokussierter Test `plugins/obsidian/tests/test_vault_performance_baseline.py` gruen.
- Mobile UI ist abgesichert fuer Header/Settings/Graph-Grundbedienung, aber nicht fuer volle Vault-Navigation und Drag-and-drop.
- Projektplanung kann bestehende Zielkonflikte erkennen, aber noch nicht mergen, ueberschreiben oder selektiv einzelne Preview-Dateien anwenden.
- Memory Review kann einzelne Kandidaten verarbeiten, aber noch keine Queue und keine klare Core-Memory-vs-Obsidian-Produktentscheidung.

### Noch offen fuer Feature-Ready

- API-/Tool-/UI-Vertragsmatrix, damit jede relevante UI-Aktion einem Route- und Tool-Weg zugeordnet ist.
- Release-Blocker-Liste als pruefbare Checkliste.
- Import-Dry-Run und Konfliktvorschau.
- Projektplanung: Merge/Overwrite/selektiver Apply.
- Memory Review: Queue, Duplikaterkennung und klare Speicherentscheidung.
- Release-Dokumentation fuer Installation, Update, Versionierung und bekannte Einschraenkungen.
- Manuelle RC-Checks ausserhalb der bestehenden Browser-Smokes: frische Installation, Upgrade-Pfad, Export/Import in leerem Vault und Release-Zip-Struktur.

## Bisheriges Obsidian-Release-Ziel

### Zielversion

Naechster sinnvoller Obsidian-Foundation-Schnitt bleibt `0.10.0-rc.1` als interner RC-Checkpoint. `0.10.0` sollte erst nach bestaetigter manueller Distributionspruefung und expliziter Release-Freigabe folgen.

Das neue Memory-first `1.0.0` wird oben in der "Neue 1.0-Roadmap" definiert. Die folgenden Obsidian-first Kriterien bleiben als Foundation-Gates erhalten, sind aber nicht mehr allein die komplette 1.0-Definition.

### Pfad bis 1.0

Der Weg bis `1.0.0` wird nicht als einzelner grosser Restblock behandelt, sondern als vier kontrollierte Abschlussphasen:

1. `RC-Closure`
   - P0-Gates schliessen
   - internen RC-Checkpoint stabilisieren
   - keine neuen breiten Features anfangen
2. `0.10.0`
   - Release-Dokumentation, Distribution und reproduzierbare manuelle Checks abschliessen
   - interne Freigabe fuer den ersten oeffentlichen stabilen Releasekandidaten-Ersatz
3. `0.11.x Hardening`
   - P1-Funktionalitaet fuer Projektplanung, Memory Review, Vault-UX und Import/Export gezielt haerten
   - Test-Layering und kleinere Testdateien nachziehen, damit weiterer Ausbau nicht teurer wird
4. `1.0.0`
   - externer Installations-/Upgrade-Pfad bestaetigt
   - wiederholbare Release-Runbooks vorhanden
   - offene Produktkanten nur noch bewusst als Post-1.0-Themen vorhanden

### Exit-Kriterien je Phase

#### Phase A: RC-Closure

- `P0.1` bis `P0.5` sind gruen oder explizit mit akzeptierter Restrisiko-Notiz abgeschlossen.
- Release-Checklist, Vertragsmatrix und Restrisikostand widersprechen sich nicht.
- Graph-, Auth- und Vault-Sicherheits-Smokes sind reproduzierbar.

#### Phase B: `0.10.0`

- frische Installation, Upgrade, Export/Import und Release-Zip wurden mindestens einmal mit Evidence dokumentiert
- Versionsstand, README und Security-Hinweise sind synchron
- kein offener P0-Blocker bleibt uebrig

#### Phase C: `0.11.x Hardening`

- Projektplanung kann bestehende Ziele sicher erweitern
- Memory Review hat Queue-/Dedupe-/Entscheidungslogik
- Import/Export hat Dry-Run-/Konfliktvorschau oder einen klaren gleichwertigen sicheren Nutzerpfad
- die Obsidian-Tests wachsen nicht weiter als Catch-all, sondern sind in Policy-, Contract- und Smoke-Schichten aufgeteilt

#### Phase D: `1.0.0`

- mindestens ein kompletter externer Installations- und Updatepfad wurde erfolgreich nachvollzogen
- Sicherheitsgates fuer Auth, Locked Vault, Import und Apply-Fallbacks sind wiederholbar gruen
- bekannte Einschraenkungen sind fuer `1.0.0` bewusst akzeptabel und dokumentiert, nicht versehentlich offen
- es gibt keinen verbleibenden "muss vor 1.0" Punkt mehr in P0 oder P1

### Feature-ready Definition

Feature-ready bedeutet hier:

- Plugin kann aus einem frischen Odysseus-Checkout heraus installiert und geoeffnet werden.
- Auth, UI-Loader und Plugin-API arbeiten zusammen, ohne Datenrouten unauthentifiziert freizugeben.
- Ein Nutzer kann einen Vault alltaeglich lesen, schreiben, suchen, organisieren, importieren und exportieren.
- Graphansicht ist nicht nur sichtbar, sondern bedienbar: filtern, hervorheben, fokussieren, erklaeren.
- Projektplanung und Memory Review schreiben nur nach Preview und Bestaetigung.
- Agent-Tools koennen dieselben Kernaktionen ausfuehren wie die UI und halten dieselben Sicherheitsgrenzen ein.
- Gesperrte Vaults leaken keine Inhalte ueber Datei-, Tag-, Graph-, Projekt- oder Memory-Routen.
- Performance fuer grosse Vaults hat dokumentierte Messpunkte und einen expliziten RC-Schwellwert statt nur gefuehlter "noch okay"-Einschaetzungen.
- Browser-, Backend-, statische UI- und Sicherheits-Smokes sind dokumentiert und reproduzierbar.
- Keine veralteten Planungsdokumente widersprechen dieser Roadmap.

## P0 Release-Gates

Diese Punkte blockieren den Release Candidate.

### Teststrategie fuer den RC

Die aktuelle Obsidian-Implementierung ist sicherheits- und vertragslastig. Deshalb bleibt hohe Testabdeckung wichtig, aber die Abdeckung soll nicht weiter durch dieselbe Invariante auf zu vielen Ebenen wachsen.

Leitlinien fuer neue und geaenderte RC-Tests:

- Sicherheits- und Datenintegritaetsregeln zuerst als zentrale Policy-/Backend-Tests absichern.
- Pro kritischem Surface nur schlanke Vertragschecks behalten: wenige kanonische Tool-/Route-/UI-Smokes statt dieselbe Invariante an jeder einzelnen Eintrittsstelle erneut voll auszuformulieren.
- `tests/test_obsidian_sidebar_static.py` nur fuer statische oder runtime-schwer testbare Vertraege verwenden; neues Verhalten bevorzugt ueber echte Runtime-Tests absichern.
- `plugins/obsidian/tests/test_plugin_obsidian.py` nicht weiter als Catch-all aufblasen; neue Abdeckung soll bevorzugt in fachlich engeren Dateien landen, sobald ein Split moeglich ist.
- Wenn dieselbe Policy bereits direkt gegen `vault_security.py`, Lock-Guards oder Apply-Guards getestet wird, braucht nicht jeder Handler nochmals einen vollstaendigen Langtest.

Bevorzugte Testpyramide fuer Obsidian:

1. Zentrale Unit-/Service-Tests fuer Policy und Validierung.
2. Schlanke Route-/Tool-Contract-Tests fuer wenige reprasentative Surfaces.
3. Kleine Zahl von Browser- oder Full-App-Smokes fuer echte End-to-End-Vertraege.

### P0.1 Auth und Plugin-Routing

Ist-Anschluss:

- `app.py`
- `plugins/obsidian/backend/routes.py`
- `tests/test_obsidian_sidebar_static.py`
- Plugin-UI-Loader in `static/index.html`

Sollstand:

- `/api/plugins/ui-loader.js` und statische Shell duerfen frueh genug laden.
- `/api/plugins/obsidian/app` darf die App-Seite laden, ohne Daten preiszugeben.
- Datenrouten wie `/files`, `/file`, `/graph`, `/tags`, `/search`, `/project-plan/...`, `/memory-review/...` muessen weiterhin `require_user()` respektieren.
- AuthMiddleware muss Plugin-API-Routen sehen, damit `request.state.current_user` gesetzt ist.

Arbeit:

1. Aktuellen Auth-Exempt-Stand finalisieren.
2. TestClient-Faelle fuer unauthentifizierte App-Seite, Datenroute und Assetroute ergaenzen.
3. Authentifizierten Browser-Smoke mit echter Session ausfuehren.
4. In README/Release Notes klar beschreiben, welche Routen UI-Shell und welche Datenrouten sind.

Testgate:

- `tests/test_obsidian_sidebar_static.py`
- neuer API/Auth-Test fuer Plugin-Datenrouten
- manueller oder automatisierter Browser-Smoke mit Login

### P0.2 Graph-Fokus aus Dateibaum

Ist-Anschluss:

- `plugins/obsidian/frontend/main.js`
- `selectTreeItem(...)`
- `openNote(...)`
- `renderGraphView(...)`
- `graphFocusPath(...)`
- Cytoscape-Klasse `obsidian-current-node`
- SVG-Klasse `current`

Sollstand:

- Wenn die Graphansicht aktiv ist und der Nutzer im Dateibaum eine Markdown-Datei anklickt, bleibt die Graphansicht aktiv.
- Die neue Datei wird im Graph als aktuelle Node hervorgehoben.
- Die lokale Graphsicht nutzt diese Datei als Fokus.
- Optionaler UX-Bonus: Cytoscape zoomt/pannt zur Node, statt nur neu zu rendern.
- Klick auf Vault-Root zeigt wieder den Gesamtgraphen.

Arbeit:

1. Vorhandenen Arbeitsbaum-Ansatz finalisieren.
2. Tree-Klick, Wiki-Link-Klick und Graph-Node-Klick als einheitlichen Fokusvertrag definieren.
3. Cytoscape nach Render auf `currentNotePath` zentrieren oder wenigstens sicher fitten.
4. Static-Contract-Test fuer Tree-Klick-im-Graph-Modus beibehalten/erweitern.
5. Browser-Smoke: Graph oeffnen, Datei im Tree anklicken, sichtbare Node-Markierung pruefen.

Testgate:

- `node --check plugins/obsidian/frontend/main.js`
- `python -m pytest tests/test_obsidian_sidebar_static.py`
- Browser-Smoke mit kleiner Test-Vault

### P0.3 Dynamische Graph-Filter und Highlighting

Ist-Anschluss:

- `prepareGraphData(...)`
- `renderGraphShell(...)`
- `renderCytoscapeGraph(...)`
- `renderSvgGraphFallback(...)`
- `graph_payload(...)`
- `vault_model.py`

Sollstand:

- Graph-Filter koennen anzeigen, ausblenden oder nur hervorheben.
- Filterachsen:
  - Node-Typ: `markdown`, `folder`, spaeter `tag`, `project`, `memory`.
  - Edge-Typ: `wiki_link`, `filename_mention`, `shared_tag`, `manual`, `relates_to`, `depends_on`, `blocks`, `supports`.
  - Tags: vorhandene Tags aus Graph-/Tag-Payload.
  - Ordner/Subtree.
  - Suchbegriff in Label, Pfad oder Tags.
  - Fokus: aktuelle Notiz, Nachbarn, aktueller Projektordner, gesamter Vault.
- UI braucht klare Modi:
  - `show`: nur passende Elemente bleiben sichtbar.
  - `highlight`: passende Elemente werden betont, andere bleiben blasser.
  - `hide`: passende Elemente werden ausgeblendet.
- Filterzustand soll lokal persistiert werden, aber pro Vault/Session nicht verwirrend wirken.
- Keine Toolbar-Ueberladung: Filter als kompaktes Panel/Popover in der Graphansicht.
- Altlasten wie separate globale Filtervariablen duerfen nicht parallel zu einem zentralen Graph-State weiterleben, weil sonst Filterkombinationen inkonsistent werden.

Nicht-Ziel fuer RC:

- Semantische Embedding-Cluster.
- Vollstaendige Tag-Knoten als dauerhaft sichtbare eigene Node-Klasse.
- Graph-Export als Bild oder JSON, ausser wenn schnell risikoarm.

Arbeit:

1. Graph-State-Objekt einfuehren statt einzelner `graphEdgeTypeFilter` Variable.
2. `prepareGraphData` so erweitern, dass Node-/Edge-/Tag-Metadaten fuer Filter erhalten bleiben.
3. Cytoscape-Klassen fuer hidden/dimmed/highlighted/current/neighbor definieren.
4. SVG-Fallback mit denselben sichtbaren/gedimmten Klassen versehen.
5. Graph-Filter-Panel bauen: Checkboxes fuer Edge/Node, Tag-Suche, Suchfeld, Modus-Schalter.
6. "Reset graph filters" im Settings-Menue auf neuen State umstellen.
7. Agent-Tool/Route pruefen: Reicht `obsidian_graph` mit Query-Parametern oder braucht es einen Filter-Preview-Toolvertrag?
8. Legacy-Globals fuer Graph-Filter konsequent entfernen oder in den zentralen State migrieren.
9. Tests fuer State, DOM-Contracts und Backend-Payload ergaenzen.

Testgate:

- Static UI contract fuer Filter-Panel und Klassen.
- Backend-Test fuer `graph_payload(..., tag=...)` bleibt gruen.
- Browser-Harness: Filter setzt Node/Edge sichtbar/gedimmt/hidden mit echtem Cytoscape-Asset; Full-App-Browser-Smoke bleibt Release-Gate.

### P0.4 Sicherheits- und Datenintegritaetsgate

Sollstand:

- Kein Pfad kann aus dem Vault ausbrechen.
- Import blockiert `../`, absolute Pfade, reservierte interne Dateien und unerwartete Archivformen.
- Passwortwerte werden nicht in DOM, Toasts, Logs oder History geschrieben.
- Gesperrte Vaults blockieren Tags, Graph, Datei, Projektplanung und Memory Review.
- Apply-Flows ueberschreiben keine bestehenden Dateien still.
- Undo verweigert unsichere Ruecksetzungen, wenn Inhalte nachtraeglich geaendert wurden.
- Das Sicherheitsmodell wird nicht nur in Doku, sondern auch im UI klar erklaert: Passwortschutz sperrt Plugin-Zugriff, verschluesselt aber bestehende Vault-Dateien auf Platte nicht automatisch at-rest.

Arbeit:

1. Sicherheits-Testmatrix aus den alten Planungsdokumenten als Tests/Release-Checklist abbilden.
2. Gesperrte-Vault-Leak-Test fuer Graph/Tags/Search/Project/Memory auf zentrale Policy plus wenige kanonische Surface-Contracts konsolidieren; keine weitere ungebremste Vervielfachung derselben Sperrlogik in jeder Schicht.
3. Import-Dry-Run als P1 planen, aber RC mindestens mit sicherem Import-Verhalten dokumentieren.
4. Passwort-Setzen/Ersetzen im UI ist mit unmissverstaendlicher At-Rest-Warnung versehen; ein blosses README-/Release-Notes-Statement reicht fuer RC nicht aus.
5. Release Notes mit klarer Einschraenkung: aktueller Passwortschutz schuetzt den Zugriff im Plugin, ist aber kein vollstaendig verschluesselter Vault-at-rest, falls Daten unverschluesselt auf Platte liegen.
6. Projektplanung-/Memory-Apply-Fallback ist explizit als "hart blockieren mit 409, kein stilles Overwrite" abgesichert und in Route-/Tool-Tests sichtbar gehalten.

Testgate:

- Plugin-Backend-Tests fuer Vault Security.
- Neuer Leak-Test, falls noch nicht abgedeckt.
- Manuelle Review von DOM/Toast/History fuer Passwortstrings.
- Manueller UI-Check: Passwortdialog nennt die Nicht-Verschluesselung-at-rest explizit vor dem Speichern. Der statische Frontend-Vertrag ist bereits gruen; ein weiterer Live-Klicknachweis bleibt optional.
- Konflikt-Check: Projektplanung/Memory-Apply bei vorhandenen Dateien bleibt strikt blockiert und schreibt nichts. Tool- und Route-Level-Tests sind gruen.

### P0.5 Release-Dokumentation und Distribution

Sollstand:

- Plugin-README beschreibt Installation, Konfiguration, Features, API, Tools, Tests und Grenzen.
- `SECURITY.md` beschreibt Meldeweg und unterstuetzte Versionen ausreichend fuer RC.
- `CONTRIBUTING.md` ist ASCII-sauber und enthaelt keine kaputten Encoding-Zeichen.
- Release-Zip/Repository-Struktur ist klar: Plugin-Dateien liegen am Archivroot, wenn als Plugin-Release verteilt.
- Version in `plugin.py` und `plugin.json` ist konsistent.

Arbeit:

1. Dokumentationsdateien vor Release auf Encoding-Artefakte pruefen. `plugins/obsidian/CONTRIBUTING.md` wurde in diesem Cleanup bereinigt.
2. `plugin.py` und `plugin.json` Version bei Release-Schnitt synchron halten; aktuell beide `0.10.0-rc.1`.
3. Bekannte Einschraenkungen in README oder Release Notes ergaenzen.
4. Installationspfad in Root-README und Plugin-README konsistent halten; der aktuelle Clone-Pfad verwendet bereits `Odysseus-plugin-obsidian` und sollte vor Release nicht wieder regressieren.

## P1 Feature-Haertung

Diese Punkte sollten direkt nach P0 oder parallel umgesetzt werden, wenn sie P0 nicht destabilisieren.

### P1.1 Projektplanung produktiv machen

Aktuell:

- Plan-vor-Schreiben funktioniert.
- Templates und Projekttypen existieren.
- Preview kann Inhalte generieren.
- Sessions koennen wiederhergestellt und angewendet werden.
- Konflikte brechen Apply ab.

Offen:

1. Merge-/Overwrite-Flow fuer bestehende Projektordner.
2. Einzelne Preview-Dateien selektiv anwenden.
3. Preview-Edits vor Apply noch staerker validieren.
4. Projekt-Nachpflege: neue ADR, neue Decision, neue Task, Statuswechsel, Roadmap-Update.
5. Analyse-Tool: bestehende Projektstruktur lesen und naechste sinnvolle Notizen vorschlagen.
6. Template-Qualitaet pruefen: Research, Writing, Teaching, SecOps, GameDev, Software.

Akzeptanz:

- Nutzer kann vorhandenes Projekt erweitern, ohne Dateien zu verlieren.
- Konflikte zeigen Ziel, Grund und sichere Auswahloptionen.
- Agent kann denselben Plan als Tool vorschlagen und erst nach Bestaetigung anwenden.
- Bis diese P1-Arbeit erledigt ist, bleibt der P0-Fallback unveraendert streng: bestehende Dateien fuehren zu Konfliktlisten und einem blockierenden `409`, nie zu implizitem Merge oder Overwrite.

### P1.2 Memory Review produktreif machen

Aktuell:

- Einzelne Memory-Kandidaten koennen als neue Notiz gespeichert, an bestehende Notiz angehaengt, nur im Core-Memory belassen oder verworfen werden.
- Tags, Links und Relationships werden vorgeschlagen.
- Apply ist bestaetigungspflichtig.

Offen:

1. Entscheidungsmatrix: Memory-only, Obsidian-only, beides, discard.
2. Core-Memory-Anbindung sauber definieren, ohne Obsidian zum globalen Memory-System zu machen.
3. Review-Queue fuer mehrere Kandidaten.
4. Duplikat- und Aehnlichkeitspruefung verbessern.
5. Quellen-, Risiko- und Vertrauensanzeige staerken.
6. Nach Apply optional Graph-Fokus auf neue/geaenderte Notiz.
7. Append-Abschnitte mit stabilen Ueberschriften und Quellenankern normalisieren.

Akzeptanz:

- Der Nutzer versteht vor Apply, was wohin geschrieben wird und warum.
- Obsidian bleibt kuratierter Wissensraum; Core Memory bleibt systemweite Memory-Schicht.

### P1.3 Vault-UX fuer Alltag

Aktuell:

- File tree, Rename, Delete, Drag/drop Markdown Import, Suche und Editor funktionieren.
- Mobile Grundkontrollen sind abgesichert.

Offen:

1. Mobile Vault-Navigation.
2. Mobile Move-Flow per Long-Press oder alternatives Kontextmenue.
3. Keyboard-Shortcuts und Command-Palette pruefen.
4. Bessere Konfliktmeldungen fuer Rename, Move, Import und Apply.
5. Tag-Farbverwaltung als eigenes UI.
6. Multi-file Import mit Preview.

Akzeptanz:

- Laengere Nutzung fuehlt sich nicht wie ein Admin-Panel, sondern wie ein Schreib- und Denkraum an.

### P1.4 Import/Export und Schutz vertiefen

Aktuell:

- ZIP Import/Export existiert.
- Optionaler passwortgeschuetzter Export existiert.
- Passwortschutz fuer Plugin-Zugriff existiert.

Offen:

1. Import-Dry-Run mit Dateiliste, Konflikten und Zielvorschau.
2. Teilimport und Teilexport fuer Ordner/Notizen.
3. Backup-/Restore-Protokoll als History-Ereignis.
4. Klareres Verschluesselungsmodell: entsperrt auf Platte vs. echte at-rest Verschluesselung.
5. Suchindex/Tagindex/Graph-Metadaten bei geschuetzten Vaults auf Leaks pruefen.

Akzeptanz:

- Nutzer verliert keine Daten durch Import/Export und versteht das Sicherheitsmodell.

## Muss / Sollte / Nach 1.0

### Muss vor 1.0

- P0.1 Auth und Plugin-Routing
- P0.2 Graph-Fokus aus Dateibaum
- P0.3 Dynamische Graph-Filter und Highlighting in stabilem RC-Vertrag
- P0.4 Sicherheits- und Datenintegritaetsgate
- P0.5 Release-Dokumentation und Distribution
- externe Installations-/Upgrade-Bestaetigung
- wiederholbare Locked-Vault-, Import- und Apply-Sicherheitsgates

### Sollte vor 1.0

- P1.1 Projektplanung produktiv machen
- P1.2 Memory Review produktreif machen
- P1.4 Import/Export und Schutz vertiefen
- S15 Test-Layering-Refactor mindestens soweit, dass keine weitere ungeordnete Catch-all-Ausweitung stattfindet

### Kann nach 1.0

- P1.3 Vault-UX fuer Alltag, soweit es mobile oder Komfort-Themen ohne Sicherheits-/Datenrisiko betrifft
- P2-Erweiterungen wie Graph-Export, semantische Relationship-Vorschlaege, Per-Vault-Themes, Projektgraph-Modi, Inbox/Review-Ordner, Sync-Konzept

## P2 Spaetere Erweiterungen

- Graph-Export als PNG/SVG/JSON und Graph-Zusammenfassung fuer Agenten.
- Semantische Relationship-Vorschlaege durch LLM oder Embeddings.
- Tag-Governance mit Warnen/Bestaetigen/Blockieren bei neuen Tags.
- Per-Vault UI-Themen fuer Tags und Graph.
- Projektgraph-Modi fuer Software, Research, Writing, Teaching und GameDev.
- Inbox/Review-Ordner als eigene erste Klasse.
- Sync-Konzept mit externem Obsidian, Nextcloud oder Git.

## Slice-Matrix fuer Mehragenten-Umsetzung

Diese Matrix ist die operative Grundlage, um mehrere Agenten parallel zu beauftragen, ohne sich gegenseitig in dieselben Hot Files zu schicken.

Grundsatz:

- Ein Agent pro Slice.
- Keine parallele Schreibarbeit auf denselben Hot Files ohne explizite Ownership-Entscheidung.
- Wenn bereits ein anderer Agent im Projekt arbeitet, gilt dessen Slice/File-Bereich bis zur Klaerung als belegt.
- Bei unvermeidbarer Ueberschneidung zuerst read-only analysieren, dann neu aufteilen.

### Slice-Board

| Slice | Ziel | Hauptdateien | Abhaengigkeiten | Parallel sicher? | Empfohlene Reihenfolge |
| --- | --- | --- | --- | --- | --- |
| `S1-auth-routing` | Auth-/Routing-Gate absichern | `app.py`, `plugins/obsidian/backend/routes.py`, `tests/test_obsidian_sidebar_static.py` | keine harte fachliche Abhaengigkeit | ja | 1 |
| `S2-security-ui-docs` | Vault-Sicherheitsklarheit im UI und in den Plugin-Dokumenten | `plugins/obsidian/frontend/main.js`, `plugins/obsidian/README.md`, `plugins/obsidian/SECURITY.md` | darf das aktuelle Sicherheitsmodell nicht aendern | ja | 1 |
| `S3-release-docs` | Release-Doku, Install-/Upgrade-Pfad, Version-Sync | `README.md`, `plugins/obsidian/README.md`, `plugin.py`, `plugin.json` | finaler Release-Schnitt und aktuelle Produktgrenzen | ja | 1-2 |
| `S4-graph-focus` | Graph-Fokusvertrag aus Dateibaum finalisieren | `plugins/obsidian/frontend/main.js`, `tests/test_obsidian_sidebar_static.py` | gemeinsamer Frontend-Vertrag mit `S5` | bedingt | 2 |
| `S5-graph-filter-state` | Graph-Filter-State konsolidieren und Legacy-Globals entfernen | `plugins/obsidian/frontend/main.js`, `plugins/obsidian/backend/vault_model.py`, `tests/test_obsidian_sidebar_static.py` | gemeinsamer Frontend-Vertrag mit `S4` | bedingt | 2 |
| `S6-performance-gate` | Large-Vault-Performance messen, Grenzwerte dokumentieren, Testgate pflegen | `tests/`, `docs/obsidian/00-priorisierte-roadmap.md`, ggf. `plugins/obsidian/frontend/main.js` | stabile Messbasis; moeglichst kein paralleler Graph-Umbau | ja, wenn measurement-only | 1-2 |
| `S7-project-plan-conflicts` | Projektplanung: Merge/Overwrite/selektiver Apply nach P0 | `plugins/obsidian/backend/project_planning.py`, `plugins/obsidian/backend/routes.py`, `plugins/obsidian/frontend/main.js` | P0-Fallback `409` bleibt waehrenddessen aktiv | ja | 3 |
| `S8-memory-review-productization` | Memory Review Queue, Dedupe, Produktisierung | `plugins/obsidian/backend/memory_review.py`, `plugins/obsidian/frontend/main.js` | darf Konflikt-/Apply-Schutz nicht lockern | ja | 3 |
| `S9-rc-checklist-sync` | RC-Checkliste, Vertragsmatrix und Restrisiken auf denselben Stand ziehen | `docs/obsidian/00-priorisierte-roadmap.md`, `README.md`, `plugins/obsidian/README.md`, `SECURITY.md` | sollte auf `S1` und `S3` aufsetzen, nicht davor divergieren | ja | 2 |
| `S10-manual-distribution-runbook` | Frische Installation, Upgrade, Release-Zip und Evidence-Template als reproduzierbaren Runbook-Slice ausformulieren | `docs/obsidian/00-priorisierte-roadmap.md`, `README.md`, `plugins/obsidian/README.md` | profitiert von fertigeren Release-Docs aus `S3` | ja | 2 |
| `S11-locked-vault-regression-matrix` | Gesperrter Vault: Leak-Regressionen fuer Files, Tags, Graph, Search, Project, Memory vervollstaendigen | `plugins/obsidian/tests/test_plugin_obsidian.py`, ggf. `plugins/obsidian/backend/routes.py` | sollte die Auth-/Lock-Basis aus `S1` nicht aufweichen | bedingt | 2 |
| `S12-import-archive-hardening` | Import-Sicherheitsmatrix und reservierte Archivfaelle vor dem spaeteren Dry-Run haerten | `plugins/obsidian/backend/import_export.py`, `plugins/obsidian/tests/test_plugin_obsidian.py`, `docs/obsidian/00-priorisierte-roadmap.md` | kann parallel zu reinem Release-/Doc-Work laufen | ja | 2-3 |
| `S13-project-plan-backend-apply-options` | Selektives Apply sowie kontrollierte Merge/Overwrite-Entscheidungen zuerst backendseitig vorbereiten | `plugins/obsidian/backend/project_planning.py`, `plugins/obsidian/tests/test_plugin_obsidian.py` | P0-Konfliktblockade bleibt bis Abschluss unveraendert aktiv | ja | 3 |
| `S14-memory-review-queue-backend` | Queue-, Dedupe- und Quellen-Normalisierung fuer Memory Review zunaechst im Backend stabilisieren | `plugins/obsidian/backend/memory_review.py`, `plugins/obsidian/tests/test_plugin_obsidian.py` | darf bestaetigungspflichtige Apply-Grenzen nicht lockern | ja | 3 |
| `S15-test-layering-refactor` | Obsidian-Teststrategie auf zentrale Policy-Tests, schlanke Surface-Contracts und kleinere Testdateien umstellen | `plugins/obsidian/tests/test_plugin_obsidian.py`, `tests/test_obsidian_sidebar_static.py`, ggf. neue `plugins/obsidian/tests/test_*` Dateien, `tests/TESTING_STANDARD.md` | sollte keine Produktlogik aendern; erst nach RC-P0-Haertung oder in kleinen rein-testbezogenen Slices | bedingt | 3 |

### Empfohlene Parallel-Batches

Batch 1: sofort parallelisierbar

- `S1-auth-routing`
- `S2-security-ui-docs`
- `S3-release-docs`
- `S6-performance-gate`

Batch 2: kontrolliert parallelisierbar

- `S4-graph-focus`
- `S5-graph-filter-state`
- `S9-rc-checklist-sync`
- `S10-manual-distribution-runbook`
- `S11-locked-vault-regression-matrix`
- `S12-import-archive-hardening`

Batch 3: nach P0-Stabilisierung

- `S7-project-plan-conflicts`
- `S8-memory-review-productization`
- `S13-project-plan-backend-apply-options`
- `S14-memory-review-queue-backend`
- `S15-test-layering-refactor`

### Wo Parallelisierung keine gute Idee ist

Die folgenden Kombinationen sollten nicht parallel laufen, auch wenn sie auf dem Papier thematisch verwandt wirken:

- `S4-graph-focus` nicht parallel zu `S5-graph-filter-state`
  - beide schreiben an `plugins/obsidian/frontend/main.js`
  - beide koennen dieselben Static-Contracts und Browser-Smokes destabilisieren
- `S1-auth-routing` nicht parallel zu jedem Slice, der frei an `tests/test_obsidian_sidebar_static.py` mitschreibt
  - sonst werden Auth- und Shell-Vertraege gleichzeitig umdefiniert
- `S3-release-docs` nicht parallel zu `S9-rc-checklist-sync` oder `S10-manual-distribution-runbook`
  - sonst drohen doppelte Editoren in `README.md` und `plugins/obsidian/README.md`
- `S11-locked-vault-regression-matrix`, `S12-import-archive-hardening`, `S13-project-plan-backend-apply-options` und `S14-memory-review-queue-backend` nicht blind parallel untereinander
  - alle koennen `plugins/obsidian/tests/test_plugin_obsidian.py` beruehren
  - hier gilt: entweder klare Testblock-Aufteilung oder nacheinander
- `S15-test-layering-refactor` nicht parallel zu produktiven Security-/Memory-/Project-Slices, die dieselben Obsidian-Testfiles umbauen
  - sonst mischen sich Teststruktur-Rewrite und Fachlogik-Aenderung
- `S7-project-plan-conflicts` nicht parallel zu `S13-project-plan-backend-apply-options`
  - `S13` ist bewusst der risikoaermere Backend-Vorbau fuer dasselbe Thema
- `S8-memory-review-productization` nicht parallel zu `S14-memory-review-queue-backend`
  - `S14` ist bewusst der backendlastige Vorbau fuer dasselbe Thema

Praktische Regel:

- Sobald zwei Slices dasselbe Testfile oder denselben Frontend-Hot-File aendern, ist Parallelisierung standardmaessig nein, bis ein expliziter Split dokumentiert wurde.

### Zuschnitt fuer Graph-Frontend-Arbeit

`S4` und `S5` duerfen nur parallel laufen, wenn vorher ein kurzer Arbeitsvertrag feststeht:

- `currentNotePath` bleibt Fokusquelle fuer `S4`.
- `graphFilterState` wird die einzige Filterquelle fuer `S5`.
- keine neuen Legacy-Globals
- ein Agent owned Fokus-/Navigationsthemen
- ein Agent owned Filter-State-/Panel-/Reset-Themen
- Browser-Smokes und Static-Contract-Tests werden nicht blind parallel umgeschrieben

### Beauftragungsregeln fuer den Master-Agent

Wenn mehrere Agenten parallel arbeiten sollen, sollte der orchestrierende Master-Agent fuer jede Beauftragung festhalten:

- Slice-Name
- explizite Hauptdateien
- Nicht-Ziele
- bekannte Ueberschneidungen
- erwartetes Testgate

Empfohlene minimale Delegate-Form:

```json
{
  "task": "Own slice S2-security-ui-docs. Update only the password-protection UI wording and related docs. Do not touch graph code or backend auth. If you see overlap with another active agent, stop and report the overlap instead of editing.",
  "context_query": "obsidian password protection at-rest warning release candidate",
  "budget": 1200
}
```

### Aktuelle Hot-File-Warnung

Die groesste Kollisionszone bleibt aktuell:

- `plugins/obsidian/frontend/main.js`
- `tests/test_obsidian_sidebar_static.py`

Auf diesen Dateien sollten nicht mehrere Agenten gleichzeitig frei arbeiten. Fuer diese Hot Files ist Slice-Ownership wichtiger als maximale Parallelitaet.

### Aktuelle Ownership-Annahme am 2026-06-16

Alice arbeitet aktuell aktiv. Der sichtbare Working Tree deutet auf laufende Obsidian-Testarbeit hin:

- aktive sichtbare Datei: `plugins/obsidian/tests/test_locked_vault_surfaces.py`

Bis Alice explizit uebergibt, sollte diese Spur als belegt gelten. Der sicherste Schluss ist:

- kein paralleler Slice auf denselben Obsidian-Testsplit oder benachbarte Locked-Vault-Surface-Dateien
- Test-Layering- oder Locked-Vault-Arbeit nur read-only planen, solange die betroffenen Testdateien nicht klar uebergeben sind
- Roadmap-, Runbook- und Release-Planungsarbeit bleibt konfliktarm

Direkte Konsequenz fuer neue Arbeit:

- keine neue parallele Schreibarbeit in `plugins/obsidian/tests/` ohne klaren Dateisplit
- `tests/test_obsidian_sidebar_static.py` und `plugins/obsidian/frontend/main.js` bleiben weiterhin Hot Files
- fuer sofortige Zusatzarbeit eignen sich eher Roadmap-/Runbook-/Release-Slices oder klar getrennte Backend-Dateien ohne Test-Overlap

### Archivierte Obsidian-first Foundation-Queue

Die folgenden `S...`-Slices bleiben nur als Foundation-/Archivspur erhalten.

- Sie dokumentieren den frueheren Obsidian-first RC-Plan und erklaeren, warum bestimmte Release-, Graph- und Security-Arbeit bereits existiert.
- Sie sind **nicht** mehr die aktive Prioritaets- oder Reihenfolgeliste fuer Alice oder Bob.
- Wenn der neue Memory-first Plan und diese Altspur kollidieren, gilt der Abschnitt `Aufgabenverteilung Alice / Bob` mit den `A...`- und `B...`-Slices weiter oben.

### Rest-Roadmap als Alice/Bob-Queue

Damit die restliche Roadmap ohne Neu-Sortierung abgearbeitet werden kann, wird sie fuer zwei Agents in zwei Spuren zerlegt. Alice ist aktuell schneller; deshalb bekommt sie die kuerzeren und frueher dokumentationsnahen Handoffs.

#### Alice-Track

Primaer fuer schnellere Durchlaeufe, Dokumentation, Runbooks und spaeter einen klar begrenzten Frontend-Slice.

1. `S3-release-docs`
2. `S10-manual-distribution-runbook`
3. `S9-rc-checklist-sync`
4. `S2-security-ui-docs`
5. `S4-graph-focus`
6. `S8-memory-review-productization`
7. `S15-test-layering-refactor` nur fuer UI-/Contract-Testsplit oder read-only Vorarbeit
8. `1.0-release-readiness` als Abschluss- und Go/No-Go-Slice fuer Release Notes, offene Risiken und externe Evidence

Regeln fuer Alice:

- `S10` erst starten, wenn `S3` sauber uebergeben ist.
- `S9` nicht parallel zu `S10`, weil beide dieselben README-Dateien anfassen koennen.
- `S2` erst starten, wenn Alice nicht mehr an `README.md` oder `plugins/obsidian/README.md` aus `S3`, `S9` oder `S10` arbeitet.
- `S4` erst starten, wenn Bob nicht mehr an `tests/test_obsidian_sidebar_static.py` arbeitet und kein anderer Agent `plugins/obsidian/frontend/main.js` owned.
- `S8` erst nach P0-Stabilisierung und nicht parallel zu `S14`.
- `S15` nur dann uebernehmen, wenn Alice keine konkurrierenden Aenderungen an `tests/test_obsidian_sidebar_static.py` oder den betroffenen Obsidian-Testfiles mehr offen hat.
- `1.0-release-readiness` erst starten, wenn die davor liegenden Release-/Runbook-Slices uebergeben sind.

#### Bob-Track

Primaer fuer Auth, Backend-Haertung, Testmatrizen und spaeter die backendlastigen Produktisierungsslices.

1. `S1-auth-routing`
2. `S11-locked-vault-regression-matrix`
3. `S12-import-archive-hardening`
4. `S6-performance-gate`
5. `S13-project-plan-backend-apply-options`
6. `S14-memory-review-queue-backend`
7. `S15-test-layering-refactor` nur fuer Backend-/Policy-Testsplit oder read-only Vorarbeit
8. `external-upgrade-proof` als spaeter Slice fuer externen Install-/Upgrade-Nachweis und harte 1.0-Freigabechecks

Regeln fuer Bob:

- `S11` erst starten, wenn `S1` uebergeben ist oder die Testgrenzen explizit getrennt wurden.
- `S12`, `S13` und `S14` nicht parallel untereinander, solange sie alle `plugins/obsidian/tests/test_plugin_obsidian.py` anfassen.
- `S6` ist der beste Lueckenfueller, wenn Bob auf einen Handoff warten muss und measurement-only bleiben kann.
- `S13` erst nach P0-Stabilisierung und nicht parallel zu `S7`.
- `S14` erst nach P0-Stabilisierung und nicht parallel zu `S8`.
- `S15` nur dann uebernehmen, wenn Bob keine konkurrierenden Aenderungen an `plugins/obsidian/tests/test_plugin_obsidian.py` mehr offen hat.
- `external-upgrade-proof` erst starten, wenn `0.10.0` intern freigabereif ist und keine offenen P0-Blocker mehr da sind.

#### Gemeinsame Cross-Track-Regeln

- Alice zieht vor Bob auf den naechsten Slice weiter, wenn sie frueher fertig ist; Bob bleibt auf seinem aktuellen Backend-/Test-Korridor und wird nicht in Alices Doku- oder Frontend-Files umgelenkt.
- Bob zieht nicht in `README.md`, `plugin.py`, `plugin.json` oder `plugins/obsidian/README.md`.
- Alice zieht nicht in `app.py`, `plugins/obsidian/backend/routes.py` oder `tests/test_obsidian_sidebar_static.py`, solange Bob dort aktiv ist.
- Die echten Stoppschilder bleiben `plugins/obsidian/frontend/main.js`, `tests/test_obsidian_sidebar_static.py` und `plugins/obsidian/tests/test_plugin_obsidian.py`.
- Teststrategie-Aenderungen selbst sind ein eigener Slice und werden nicht "nebenbei" in Produkt-Slices mitgezogen.

### Zwei-Agenten-Plan nach Phase

Zur schnellen Orientierung fuer den Master-Agent gilt fuer den Rest der Roadmap diese Standardaufteilung:

| Phase | Alice | Bob | Parallel sinnvoll? | Kommentar |
| --- | --- | --- | --- | --- |
| laufend | `S3-release-docs` | `S1-auth-routing` | ja | bereits getrennte Dateibereiche |
| direkt danach | `S10-manual-distribution-runbook` | `S11-locked-vault-regression-matrix` | ja | Doku-Track vs. Backend-/Test-Track |
| danach | `S9-rc-checklist-sync` | `S12-import-archive-hardening` | ja, mit Vorsicht | Alice bleibt in Doku, Bob in Import-/Backend-Tests |
| spaeter | `S2-security-ui-docs` | `S6-performance-gate` | ja | nur wenn Alice keine README-Handoffs offen hat |
| Graph-Phase | `S4-graph-focus` | Pause oder read-only Review | nein als Doppel-Implementierung | Frontend-Hot-File solo ownen |
| P0+ Produktisierung | `S8-memory-review-productization` | `S13-project-plan-backend-apply-options` | ja | getrennte Fachbereiche |
| Abschluss P1 | Pause oder UI-Followups zu Memory | `S14-memory-review-queue-backend` | bedingt | nicht parallel zu `S8`, falls dieselbe Memory-Logik aktiv geaendert wird |
| Test-Refactor | UI-/Contract-Testsplit fuer `S15` | Backend-/Policy-Testsplit fuer `S15` | nein ohne expliziten Dateisplit | erst nach stabilen Produkt-Slices |
| Release auf 1.0 | `1.0-release-readiness` | `external-upgrade-proof` | bedingt | erst nach P0 gruen und mit klarer Evidence-Aufteilung |

### Vordefinierte Agenten

Damit mehrere Agenten sofort beauftragt werden koennen, ohne jedes Mal den Slice neu zu erklaeren, sind diese zwei Start-Agenten vordefiniert.

Wegen des bereits laufenden anderen Agenten sind hier bewusst zwei relativ konfliktarme Spuren vorbelegt. Falls der aktive Fremd-Agent nachweislich genau dieselben Dateien bearbeitet, muss der Master-Agent zuerst neu zuschneiden statt parallel weiterzuschreiben.

#### Agent Bob

- Slice: `S1-auth-routing`
- Ziel: Auth-/Routing-Gate fuer Plugin-UI vs. Plugin-Datenrouten absichern
- Primaere Dateien:
  - `app.py`
  - `plugins/obsidian/backend/routes.py`
  - `tests/test_obsidian_sidebar_static.py`
- Erwartete Arbeit:
  - Auth-Exempt-Regeln fuer UI-Shell klein und sauber halten
  - Datenrouten weiter durch echte Auth laufen lassen
  - API-/Static-/Auth-Tests ergaenzen oder haerten
- Nicht-Ziele:
  - keine Graph-UI-Aenderungen
  - keine Passwort-UX-Texte
  - keine Release-Doku-Arbeit
- Testgate:
  - `python -m pytest tests/test_obsidian_sidebar_static.py`
  - relevante Auth-/Route-Tests
- Beauftragungsform:

```text
Du bist Bob. Own slice S1-auth-routing. Arbeite nur an app.py, plugins/obsidian/backend/routes.py und den dazugehoerigen Auth-/Static-Tests. Nicht an Graph-UI, Passwort-UX oder Release-Doku arbeiten. Wenn ein anderer aktiver Agent diese Dateien bereits veraendert, stoppe und melde den Overlap statt weiter zu editieren.
```

- Naechster empfohlener Handoff-Slice nach `S1`: `S11-locked-vault-regression-matrix`
- Warum als Folge-Slice:
  - baut fachlich direkt auf Auth-/Lock-Verhalten auf
  - bleibt ueberwiegend im Test-/Backend-Korridor
  - kollidiert weniger mit Alices Release-Doku-Spur als Graph- oder README-Arbeit

Empfohlene Folge-Beauftragung:

```text
Du bist Bob. Own slice S11-locked-vault-regression-matrix. Arbeite primaer an plugins/obsidian/tests/test_plugin_obsidian.py und nur falls noetig minimal an plugins/obsidian/backend/routes.py. Haerte Leak-Regressionen fuer gesperrte Vaults ueber Files, Tags, Graph, Search, Project und Memory. Nicht an README, Plugin-Manifesten oder Graph-Frontend arbeiten. Wenn S1-Dateien noch offen oder in Konflikt sind, zuerst Handoff melden statt parallel in dieselben Tests zu schreiben.
```

#### Agent Alice

- Slice: `S3-release-docs`
- Ziel: Release-Doku, Install-/Upgrade-Pfad und Version-Sync vorbereiten
- Primaere Dateien:
  - `README.md`
  - `plugins/obsidian/README.md`
  - `plugins/obsidian/plugin.py`
  - `plugins/obsidian/plugin.json`
- Erwartete Arbeit:
  - Installations-/Upgrade-Pfad konsistent halten
  - bekannte RC-Grenzen klar dokumentieren
  - Versionskonsistenz zwischen Manifest und Python-Plugin pruefen
- Nicht-Ziele:
  - keine Backend-Auth-Logik
  - keine Graph-Frontend-Arbeit
  - keine Projektplan-/Memory-Apply-Logik
- Testgate:
  - Doku-Konsistenz
  - Versionskonsistenz zwischen `plugin.py` und `plugin.json`
- Beauftragungsform:

```text
Du bist Alice. Own slice S3-release-docs. Arbeite nur an README.md, plugins/obsidian/README.md, plugins/obsidian/plugin.py und plugins/obsidian/plugin.json. Nicht an Backend-Auth, Graph-Frontend oder Projektplan-/Memory-Logik arbeiten. Wenn ein anderer aktiver Agent diese Dateien bereits veraendert, stoppe und melde den Overlap statt weiter zu editieren.
```

- Naechster empfohlener Handoff-Slice nach `S3`: `S10-manual-distribution-runbook`
- Warum als Folge-Slice:
  - bleibt im Release-/Dokumentationskontext
  - macht aus den RC-Hinweisen eine tatsaechlich reproduzierbare Checkliste
  - vermeidet die Hot Files von Bob und dem Graph-Frontend

Empfohlene Folge-Beauftragung:

```text
Du bist Alice. Own slice S10-manual-distribution-runbook. Arbeite an docs/obsidian/00-priorisierte-roadmap.md, README.md und plugins/obsidian/README.md. Formuliere frische Installation, Upgrade, Release-Zip-Check und Evidence-Sammlung als kurze, reproduzierbare Runbook-Schritte aus. Nicht an Backend-Auth, Graph-Frontend, Import-Implementierung oder Memory-/Project-Apply-Logik arbeiten. Wenn S3-Dokumente noch nicht sauber uebergeben sind, zuerst Handoff statt Vermischung melden.
```

### Empfohlene Fortsetzung fuer zwei laufende Agenten

Wenn Alice und Bob bereits parallel arbeiten, ist die risikoaermste Fortsetzung:

1. Bob schliesst `S1-auth-routing` ab.
2. Alice schliesst `S3-release-docs` ab.
3. Danach Bob -> `S11-locked-vault-regression-matrix`.
4. Danach Alice -> `S10-manual-distribution-runbook`.
5. Wenn Alice wieder frueher frei wird: Alice -> `S9-rc-checklist-sync`.
6. Wenn Bob danach frei wird: Bob -> `S12-import-archive-hardening`.
7. Graph-Arbeit erst, wenn `tests/test_obsidian_sidebar_static.py` und `plugins/obsidian/frontend/main.js` klar einem einzigen aktiven Owner gehoeren.
8. Nach RC-Closure: Alice zieht in `1.0-release-readiness`, Bob in `external-upgrade-proof`, sofern keine Testdatei-Kollision mehr besteht.

Diese Reihenfolge haelt beide Agents aus derselben Kollisionszone heraus, nutzt ihr bereits begonnenes Kontextfenster weiter und schiebt die Graph-Hot-Files bewusst nach hinten.

## 1.0-Abschlussplan

### Phase A: Jetzt bis RC-Closure

- P0-Gates schliessen
- Release-Doku und Runbook vervollstaendigen
- Alice nicht aus aktiver Testspur reissen; Testsplit nur mit explizitem Handoff

### Phase B: RC-Closure bis `0.10.0`

- frische Install-, Upgrade-, Export-/Import- und Release-Zip-Evidence sammeln
- Restrisiken auf "bekannt und akzeptiert" oder "geschlossen" reduzieren
- keinen neuen grossen Feature-Scope mehr aufmachen

### Phase C: `0.10.0` bis `0.11.x Hardening`

- Projektplanung, Memory Review und Import/Export gezielt haerten
- Catch-all-Tests in kleinere Policy-/Contract-/Smoke-Dateien ueberfuehren
- mobile und Komfortthemen nur dann vorziehen, wenn sie keinen Sicherheits- oder Release-Pfad blockieren

### Phase D: `0.11.x Hardening` bis `1.0.0`

- externer Installations- und Updatepfad nachweisen
- Sicherheitsgates wiederholt gruene Runs liefern lassen
- finale Go/No-Go-Notiz fuer `1.0.0` aus Release Evidence, offenen Risiken und Supportgrenzen ableiten

## Architektur- und Vertragsmatrix

### UI zu Route zu Tool

| Bereich | UI | Route | Tool |
| --- | --- | --- | --- |
| Dateien listen | File tree | `GET /files` | `obsidian_tree`, `obsidian_list_notes` |
| Datei lesen | Editor/Open | `GET /file` | `obsidian_read_note` |
| Datei schreiben | Autosave/Create | `POST /file`, `PUT /file` | `obsidian_write_note` |
| Datei loeschen | Tree/Search action | `DELETE /file` | `obsidian_delete_note` |
| Ordner erstellen | New folder | `POST /folder` | `obsidian_create_folder` |
| Ordner loeschen | Tree action | `DELETE /folder` | `obsidian_delete_folder` |
| Rename/Move | Tree/Search/Drag | `POST /rename` | `obsidian_rename_item` |
| Suche | Search panel | `GET /search` | `obsidian_search_notes` |
| Tags | Preview/Autocomplete | `GET /tags` | `obsidian_list_tags` |
| Graph | Graph view | `GET /graph` | `obsidian_graph` |
| Relationships | Graph/Apply flows | `GET/POST/DELETE /relationships` | `obsidian_list_relationships`, `obsidian_add_relationship`, `obsidian_delete_relationship` |
| History/Undo | Undo action | `GET /history`, `POST /history/undo` | `obsidian_history`, `obsidian_undo` |
| Vault security | Settings | `/vault/...` | `obsidian_vault_*` |
| Project planning | Project panel | `/project-plan/...` | `obsidian_project_plan_*` |
| Memory Review | Memory panel | `/memory-review/...` | `obsidian_memory_review_*` |

### Sicherheitsregeln

- Lesende Inhaltsrouten brauchen einen entsperrten Vault.
- Schreibende Routen brauchen einen entsperrten Vault.
- Pfade werden relativ zum Vault normalisiert.
- `.obsidian` wird in Datei-Tree und Importhygiene besonders behandelt.
- Riskante KI-Tools benoetigen Bestaetigung.
- Apply-Flows muessen zuerst Preview/Plan liefern.
- Passwoerter duerfen nicht in URLs, DOM, Logs, Toasts oder History landen.

## Test- und Release-Checkliste

### Automatisch

- `node --check plugins/obsidian/frontend/main.js`
- `python -m pytest plugins/obsidian/tests/test_plugin_obsidian.py`
- `python -m pytest tests/test_obsidian_sidebar_static.py`
- Plugin-System-/Load-Tests, falls fuer den Release-Schnitt relevant.
- Sicherheitsregressionen fuer Pfadschutz, Import, Passwortschutz, gesperrte Vaults, Confirm-Gates und Undo.

### Browser

- Obsidian-App mit authentifizierter Session oeffnen.
- Panel, Overlay, Fullscreen und Standalone testen.
- File tree: Datei oeffnen, Ordner oeffnen, Rename, Delete, Drag/drop.
- Editor: Markdown schreiben, Autosave, Preview, Wiki-Link, Tag-Badge.
- Graph: Cytoscape sichtbar, SVG-Fallback erzwingbar, aktuelle Node markiert.
- Graph: Datei im Tree anklicken waehrend Graph aktiv ist; Node bleibt/ wird hervorgehoben.
- Graph: Filter hide/show/highlight fuer mindestens Edge-Type und Tag. Isolierter Harness und Full-App-Smoke sind gruen.
- Passwortschutz: Set-/Replace-Dialog zeigt die Nicht-Verschluesselung-at-rest explizit an.
- Project planning: Preview, Streaming, Session Reload, Apply.
- Memory Review: Preview, Save-to-Obsidian, Append-to-Note, Apply.
- Vault lock: Nach Lock keine Inhalte ueber Files/Tags/Graph/Search/Project/Memory sichtbar.

### Manuell

- Frische Installation aus Plugin-Repository.
- Upgrade von bestehendem Plugin-Ordner.
- Export eines kleinen Vaults und Import in leeren Vault.
- Passwortgeschuetzter Export/Import.
- Release-Zip-Struktur pruefen.
- README-Installationspfad pruefen.
- Performance-Messung mit definierter Vault-Groesse gegen die RC-Grenzwerte dokumentieren. Die deterministische 120-Note-Fixture ist als Testgate hinterlegt.

Kompaktes Evidence-Template fuer diesen manuellen RC-Schnitt:

```text
RC line: 0.10.0-rc.1
Host commit: <sha>
Plugin commit: <sha>
Version sync: pass|fail
Fresh install: pass|fail
Upgrade: pass|fail
Export/Import: pass|fail
ZIP layout: pass|fail
Notes: <short note>
```

## Bekannte Risiken und Code-Probleme

- Graph-Filter-State ist teilweise zentralisiert, aber noch nicht vollstaendig konsolidiert; verbleibende globale Altlasten wie separate Filtervariablen sind Technical Debt und duerfen fuer den RC nicht weiter anwachsen.
- Graph-Fokus basiert aktuell stark auf `currentNotePath`; Ordnerselektion und Vault-Root brauchen klare Sonderregeln.
- Cytoscape-Layout kann bei grossen Vaults teuer werden; fuer die RC-Fixture existieren jetzt harte Grenzwerte, aber fuer deutlich groessere reale Vaults fehlen noch weitergehende Erfahrungswerte.
- Static-Contract-Tests sind wertvoll, ersetzen aber keine Browser-Smokes.
- Full-App-Graph-Filter-Smoke ist gruen mit frischem Browser-Harness; verbleibende Browser-Warnungen betreffen Favicon-404 und Cytoscape-Wheel-Sensitivity, nicht den Filtervertrag.
- Auth-Exempt-Regeln fuer Plugin-Shell vs. Plugin-Datenrouten sind sicherheitsrelevant und muessen klein bleiben.
- Passwortschutz darf nicht als vollstaendige Verschluesselung-at-rest verkauft werden; die Einschraenkung ist in README/SECURITY bereits dokumentiert, aber im UI-Set-Password-Flow noch nicht deutlich genug.
- Projektplanung und Memory Review haben viele Schreibpfade; Konflikt- und Preview-UX muss vor Release glasklar bleiben.
- Root-README nutzt bereits den korrigierten Repositorynamen `Odysseus-plugin-obsidian`; die Roadmap darf hier keinen veralteten Unsicherheitsstand mehr spiegeln.

## Konsolidierte Alt-Dokumente

Die folgenden Inhalte wurden in diese Roadmap uebernommen und sollen als einzelne aktive Planungsdokumente entfernt bleiben:

- `docs/plugins/obsidian-plugin-migration-plan.md`: Plugin-System-Migration, Zielvertrag, Sicherheitsregeln und erste Fachrouten.
- `docs/obsidian/01-vault-import-export-security.md`: Import/Export, Passwortschutz, UX, Sicherheitsanforderungen und Risiken.
- `docs/obsidian/02-tags-highlighting-autolinks.md`: Tag-Regeln, implizite Dateitags, Farben, Highlighting, Autocomplete und offene Tag-Governance.
- `docs/obsidian/03-graph-visual-model.md`: Graph-Zielbild, Cytoscape-Entscheidung, Node-/Edge-Typen, Modi, Filter, KI-Steuerbarkeit und Akzeptanzkriterien.
- `docs/obsidian/04-file-tree-drag-drop-hierarchy.md`: Vault Explorer, Drag/drop, Hierarchie, riskante Aktionen und Obsidian-Feel.
- `docs/obsidian/05-editor-tools-autocomplete.md`: Markdown-Editor, Toolbar, Preview, Wiki-Link-/Tag-Autocomplete und Schreibfluss.
- `docs/obsidian/06-ui-settings-menu.md`: Settings-Menue, Import/Export, Passwortschutz, Graph-Reset und UI-Kontrakte.
- `docs/obsidian/07-ai-project-planning.md`: Phase-4-Projektplanung, Datenmodelle, Templates, Preview, Apply, Sicherheit und Tests.
- `docs/obsidian/08-ai-control-surface.md`: Mensch-KI-Paritaet, Tool-Vertraege, Bestaetigungsregeln und KI-Sicherheitstests.
- `docs/obsidian/09-test-und-sicherheitsplan.md`: Testprioritaeten, Sicherheitsmatrix, Browser-Smokes und Release-Gate.
- `docs/obsidian/10-phase1-implementation-status.md` bis `14-phase5-memory-review-save-to-obsidian-plan.md`: historische Umsetzungsstaende, Testlaeufe, Sicherheitsstand und Folgepunkte.

## Naechste konkrete Sequenz

1. Interne RC-Readiness-Notiz mit aktuellem Commit, Teststand und Restrisiken aktuell halten.
2. Release-Checkliste fuer README/SECURITY/CONTRIBUTING/Version synchron halten.
3. Manuelle RC-Checks fuer frische Installation, Upgrade, Export/Import und Release-Zip priorisieren.
4. Nur bei konkreter Regression weitere fokussierte Tests oder einen bounded Browser-Smoke starten.
5. Push/PR/Tag erst nach expliziter Nutzerfreigabe.
