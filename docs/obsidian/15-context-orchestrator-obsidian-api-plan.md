# Context-Orchestrator und Obsidian-API-Plan

## Zielbild

Odysseus bleibt der generische KI-Orchestrator. Obsidian bleibt eine klar getrennte Plugin-Domaene, auch wenn wir aktuell im selben Arbeitsordner arbeiten. Der Core kennt keine Vault-Pfade, keine Frontmatter-Regeln und keine Obsidian-Interna. Das Obsidian-Plugin liefert Kontext, Tools, Routes und spaetere Konsolidierungsjobs ueber explizite Plugin-Schnittstellen.

Der Umbau soll drei Dinge gleichzeitig erreichen:

- Kontextverlust verhindern: feste Token-Budgets, kontrollierte History-Kompaktierung und persistenter Task-State.
- Prefix-/KV-Caching beguenstigen: stabiler frueher Prompt-Prefix ohne volatile Werte.
- Obsidian tief integrieren, aber sauber getrennt halten: Context-Provider kommt vollstaendig aus dem Plugin.

## Architekturregeln

- `src/` darf keine direkten Imports aus `plugins/obsidian` enthalten.
- Odysseus ruft nur generische Plugin-Provider ab.
- Obsidian besitzt Vault-Aufloesung, Locking, Owner-Isolation, Frontmatter, Tags, Wikilinks, Graph und Snippet-Auswahl.
- Schreibende Vault-Aktionen bleiben explizite UI- oder Tool-Aktionen mit Confirm-Mechanik.
- Read-only Kontext-Preload darf niemals eine gesperrte Vault lesen.
- Volatile Daten wie Uhrzeit, Request-IDs, Message-IDs oder Tokenmetriken duerfen nicht im fruehen Prompt-Prefix stehen.

## Phase 0: Stabilisierung der aktuellen Obsidian-UI und Graph-Bugs

Diese Phase passiert vor dem groesseren Orchestrator-Umbau, damit wir nicht auf wackligem Plugin-Verhalten aufbauen.

### Bug 1: Obsidian blockiert Odysseus nach Reload

Ist-Zustand: Nach einem Seitenreload blockiert Obsidian die Odysseus-Oberflaeche, obwohl das Panel nicht sichtbar geoeffnet wurde. Einmal oeffnen und schliessen repariert den Zustand.

Soll-Zustand:

- Beim Initialisieren ist Obsidian geschlossen, wenn nicht explizit Standalone- oder Open-State aktiv ist.
- `#obsidian-panel`, `#obsidian-modal` und Backdrop duerfen im geschlossenen Zustand keine Klicks abfangen.
- Body-Klassen wie `obsidian-open`, `obsidian-surface-overlay` und `obsidian-fullscreen` werden beim Boot konsistent gesetzt oder entfernt.

Tests:

- Static/frontend contract fuer geschlossenes Reload-Szenario.
- Browser-Pruefung: Reload, Klicks auf Odysseus UI funktionieren ohne Obsidian vorher zu oeffnen.

### Bug 2: Sidebar-Hoehe endet unten zu frueh

Ist-Zustand: Die Obsidian-Sidebar reicht nicht sauber von unterhalb der Odysseus-Kopfbar bis zum unteren Rand.

Soll-Zustand:

- Sidebar und Panel nutzen die verfuegbare App-Hoehe, nicht blind `100vh`, wenn Odysseus Chrome/Kopfbar Platz belegt.
- Sidebar, Editor, Graph und Project-/Memory-Panels bleiben intern scrollfaehig, ohne unten abgeschnitten zu werden.
- Sidebar-, Overlay- und Fullscreen-Modus werden getrennt behandelt.

Tests:

- CSS/DOM-Vertrag fuer `top`, `bottom`, `height` und `min-height: 0`.
- Browser-Screenshot in normaler Odysseus-App, nicht nur Standalone.

### Bug 3: Automatisch generierte Markdown-Dateien zu stark verknuepft

Ist-Zustand/Sorge: AI-generierte Vault-Dateien duerfen nicht als Mesh entstehen, bei dem alle Dateien miteinander verlinkt sind.

Soll-Zustand:

- Pro Thema oder Projekt gibt es einen zentralen Hub-Knoten, z. B. `00 Projektuebersicht`.
- Thematische Dateien linken primaer zum Hub.
- Direkte Querverbindungen entstehen nur bei begruendeter Beziehung.
- Graph-Anzeige soll diese Hub-and-spoke-Struktur direkt sichtbar machen.

Tests:

- Project-Planning-Test: alle Nicht-Hub-Dateien linken zum Hub.
- Kein vollstaendiges Mesh zwischen automatisch generierten Dateien.
- Externe Beziehungen nur bei nachvollziehbarem Score/Grund.

## Phase 1: Core-Plugin-Schnittstelle fuer Context-Provider

Odysseus bekommt eine generische Schnittstelle, ohne Obsidian zu kennen.

Geplante Aenderungen:

- `src/plugin_system.py` erweitert `PluginContext` um `register_context_provider(spec)`.
- Provider werden beim Plugin-Teardown sauber deregistriert.
- Provider-Spec enthaelt mindestens:
  - `id`
  - `label`
  - `priority`
  - `capabilities`
  - `retrieve(owner, query, budget, mode)`
- Optional vorbereiten: `register_consolidation_job(spec)` fuer spaetere Hintergrundjobs.

Akzeptanzkriterien:

- Fake-Provider kann im Test registriert und wieder entfernt werden.
- Core funktioniert ohne installiertes Obsidian-Plugin.
- Kein direkter Core-Import aus `plugins/obsidian`.

Commit-Schnitt:

- Ein Commit nur fuer Core-Plugin-API und Tests.

## Phase 2: Gemeinsame Obsidian-Service-Schicht im Plugin

Die aktuelle Obsidian-API ist auf Route-Ebene gut getrennt, aber intern duplizieren HTTP-Routes und Agent-Tools noch Logik. Diese Phase zieht eine plugin-interne Service-Schicht darunter.

Geplante Aenderungen:

- Neuer plugin-interner Service fuer:
  - Vault-Pfad-Aufloesung
  - Owner-Isolation
  - Lock/Unlock-Pruefung
  - sichere relative Pfade
  - Markdown-Dateien, Suche, Tags, Graph, Relationships
  - Frontmatter-Parsing fuer einfache YAML-Properties
- `backend/routes.py` und `plugin.py` nutzen diese Services statt paralleler Implementierungen.
- Bestehende HTTP-Endpunkte bleiben kompatibel.
- Bestehende Agent-Tool-Namen bleiben kompatibel.

Akzeptanzkriterien:

- Routes und Tools liefern weiterhin dieselben Ergebnisse.
- Locked Vault blockiert Routes, Tools und spaeter Provider konsistent.
- Tests fuer Pfadschutz, Locking, Search, Graph und Relationships laufen weiter.

Commit-Schnitt:

- Ein Commit fuer plugin-interne Services.
- Ein Commit fuer Umstellung von Routes/Tools, falls der Diff gross wird.

## Phase 3: Obsidian Context-Provider

Das Obsidian-Plugin registriert seinen eigenen read-only Context-Provider ueber die neue Core-Schnittstelle.

Provider-Verhalten:

- Provider-ID: `obsidian.vault_context`.
- Input: Owner, Query, Token-Budget, Mode (`chat`, `agent`, optional spaeter `background`).
- Output:
  - `structured_state`: maschinenlesbare Fakten aus Frontmatter und stabilen Projekt-Metadaten.
  - `snippets`: relevante Markdown-Auschnitte.
  - `sources`: Pfade, Titel, Tags, Score/Grund.
  - `warnings`: z. B. locked vault, keine Treffer.
  - `cache_key`: stabiler Hash ueber relevante Provider-Ausgabe.

Retrieval-Regeln:

- Frontmatter/Properties haben Vorrang vor Body-Fliesstext fuer harte Fakten.
- Body-Snippets bleiben untrusted context.
- Treffer werden stabil sortiert: Score, Pfad, Abschnitt.
- Locked Vault liefert keine Inhalte.
- Kontext bleibt innerhalb des vom Core uebergebenen Budgets.

Akzeptanzkriterien:

- Provider kann ohne HTTP-Route von Odysseus abgerufen werden.
- Provider nutzt ausschliesslich plugin-interne Services.
- Identische Vault/Query/Budget-Ergebnisse erzeugen identische Provider-Ausgaben.

Commit-Schnitt:

- Ein Commit fuer Obsidian-Provider und Provider-Tests.

## Phase 4: Core Context-Orchestrator

Status: umgesetzt. `src/context_orchestrator.py` enthaelt den generischen Budget-Split, Provider-Preload, stabile Provider-Systembloecke und finalen Trim-Guard. Chat und Agent nutzen den generischen Provider-Preload ohne Obsidian-Import im Core.

Der Core baut eine zentrale Kontext-Pipeline, die normalen Chat und Agent-Mode gemeinsam versorgt.

Pipeline:

1. `token_check()`
2. `provider_preload()`
3. `state_inject()`
4. `history_assemble()`
5. `final_trim_guard()`

Budget-Default:

- 20 Prozent Systemregeln
- 20 Prozent Provider-Kontext, inklusive Obsidian
- 40 Prozent Working-History
- 20 Prozent Antwortreserve

Prompt-Reihenfolge:

1. Statische Systemregeln
2. Stabiler strukturierter Provider-State
3. Stabil sortierte Provider-Snippets
4. Dynamische Turn-Informationen
5. Chat-History
6. Aktuelle User-Query

Akzeptanzkriterien:

- Chat und Agent-Mode nutzen denselben Orchestrator.
- Der fruehe Prefix bleibt byte-stabil, wenn Provider-Ausgaben identisch sind.
- Uhrzeit und andere volatile Werte erscheinen nicht im fruehen Prefix.
- Finaler Guard verhindert Overflow vor jedem LLM-Request.

Commit-Schnitt:

- Ein Commit fuer `src/context_orchestrator.py`.
- Ein Commit fuer Integration in Chat/Agent/LLM-Pfade.

## Phase 5: Praeventive History-Kompaktierung und persistenter Task-State

Status: umgesetzt. `src/context_compactor.py` erzeugt nach erfolgreicher Kompaktierung zusaetzlich einen persistenten Task-State-Systemblock mit `CURRENT_TASK`, `COMPLETED_STEPS`, `KNOWN_CONSTRAINTS` und `OPEN_QUESTIONS`.

Bestehende Module `src/context_budget.py`, `src/context_compactor.py` und `src/model_context.py` werden weiterverwendet, aber ueber den Orchestrator gesteuert.

Geplante Aenderungen:

- History wird am 40-Prozent-Slot gemessen.
- Ueberschreitung loest Kompaktierung aus, statt stumpf alte Turns zu entfernen.
- Kompaktierte History bleibt als versteckter Snapshot erhalten.
- Persistenter State-Block pro Session:
  - `CURRENT_TASK`
  - `COMPLETED_STEPS`
  - `KNOWN_CONSTRAINTS`
  - `OPEN_QUESTIONS`

Akzeptanzkriterien:

- Aktuelle User-Message bleibt immer erhalten.
- Alte relevante Ziele verschwinden nicht beim Trimming.
- Kompaktierung nutzt Utility-Modell, falls verfuegbar.
- Bei Kompaktierungsfehlern wird sicher getrimmt, aber kein stiller Kontextbruch erzeugt.

Commit-Schnitt:

- Ein Commit fuer Task-State und Kompaktierungsintegration.

## Phase 6: Background Consolidation

Status: umgesetzt. `src/consolidation_runner.py` fuehrt registrierte Plugin-Consolidation-Jobs fehlerisoliert aus. Chat-Abschluss triggert Jobs mit Capability `chat_completed`, sofern das Feature-Flag aktiv ist. Das Obsidian-Plugin registriert `obsidian.vault_consolidation` und schreibt nur einen nicht-destruktiven Report unter `.obsidian/consolidation_report.json`.

Gedachtnispflege laeuft nicht im heissen Chat-Pfad, sondern asynchron.

Core-Aufgabe:

- Generischer Runner fuer Plugin-Consolidation-Jobs.
- Trigger: nach Chat-Abschluss, periodisch, spaeter Idle-Fenster.

Obsidian-Plugin-Aufgabe:

- Eigene Jobs fuer:
  - Dedupe zwischen Memories, Session-Snapshots und Vault-Notizen
  - Konfliktmarkierung
  - Vorschlaege fuer strukturierte Frontmatter-Fakten
  - Archivierung/deaktivierter Kontext

V1-Sicherheitsregel:

- Keine automatische destruktive Loeschung.
- Forgetting bedeutet Markieren oder Archivieren, z. B. `active_context: false`, `archived: true`, `superseded_by`.

Akzeptanzkriterien:

- Hintergrundjob blockiert Chat nicht.
- Fehler im Job brechen Odysseus nicht.
- Obsidian bleibt Owner- und Lock-aware.

Commit-Schnitt:

- Ein Commit fuer Core-Runner.
- Ein Commit fuer Obsidian-Consolidation-Job.

## Phase 7: Dokumentation, Tests und Rollout

Status: umgesetzt fuer den aktuellen Branch. Root-README, Obsidian-README und diese Plan-Datei beschreiben Core/Plugin-Grenzen, Provider, Consolidation-Jobs und Feature-Flags. Die Regressionstests decken Plugin-API, Provider-Preload, Agent/Chat-Prompt-Injektion, Task-State-Kompaktierung, Consolidation-Runner und Obsidian-Locked-Vault-Verhalten ab.

Dokumentation:

- Plugin-API-Doku fuer Context-Provider.
- Obsidian-README um Context-Provider und Service-Grenze erweitern.
- Architekturhinweis: Core orchestriert, Plugin liefert.

Testabdeckung:

- Boundary-Test: keine `plugins.obsidian`-Imports aus `src/`.
- Fake-Provider-Core-Test.
- Obsidian-Provider-Test.
- Locked-Vault-Security-Test.
- Prefix-Stability-Test.
- Chat/Agent-Integration mit kleinem Kontextfenster.
- Frontend-Browser-Smoke fuer die drei UI/Graph-Bugs.

Rollout:

- Feature-Flag fuer Provider-Preload: `context_provider_preload`.
- Feature-Flag fuer Background-Consolidation: `consolidation_jobs`.
- Fallback auf bestehenden Kontextpfad, falls Provider-Preload deaktiviert ist.
- Metriken/Logs fuer Provider-Treffer, Token-Budgets, Kompaktierung und Prefix-Stability-Warnings.

## Arbeits- und Commit-Zuordnung

Aktuell arbeiten wir im selben Ordner:

```text
C:\Users\nkatz\odysseus
```

Branch:

```text
feat/obsidian-plugin
```

Commits sollen trotzdem fachlich getrennt bleiben:

1. Obsidian UI/Graph Bugfixes
2. Core Plugin-API
3. Obsidian Service-Schicht
4. Obsidian Context-Provider
5. Core Context-Orchestrator
6. History/Task-State
7. Background Consolidation
8. Tests und Dokumentation

Falls spaeter Worktrees genutzt werden, gehoeren Core-Commits in den Odysseus-Core-Worktree und Plugin-Commits in den Obsidian-Plugin-Worktree. Solange wir in einem Ordner arbeiten, erzwingen wir die Trennung ueber Dateien, API-Grenzen und Commit-Schnitt.
