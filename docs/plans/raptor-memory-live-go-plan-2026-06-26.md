# RAPTOR Memory Live-Go Plan

Stand: 2026-06-26

## Ziel

RAPTOR Memory gilt als live, wenn Odysseus aus einem freigegebenen Vault reproduzierbare RAPTOR-Artefakte bauen, lesen und fuer Retrieval/Memory-Diagnostik verwenden kann, ohne Quellnotizen, private Inhalte, Secrets, absolute Host-Pfade oder Provider-Ausgaben in abgeleitete Artefakte zu schreiben.

## Aktueller Befund

- RAPTOR-Code, Statusrouten, Rebuild-Backend und Tests sind vorhanden.
- Lokaler Status fuer `vault/`:
  - `obsidian_raptor_enabled=false`
  - `obsidian_raptor_rebuild_enabled=false`
  - `index_present=false`
  - `summaries_present=false`
  - `readiness.state=not_configured`
  - `readiness.gaps=["raptor_index_missing"]`
  - `write_gate.state=blocked`
  - `write_gate.gaps=["raptor_feature_flag_disabled", "raptor_rebuild_feature_flag_disabled"]`
- Gesamt-Memory-Readiness ist ebenfalls blockiert:
  - `ledger_empty`
  - `derived_index_missing`
  - `query_index_missing`
  - `query_index_not_ready`
  - `query_index_empty`
  - `somt_issues_present`
  - `freshness_filtering_not_active`
  - `needs_review_items`
  - `raptor_index_missing`
- RAPTOR-Teststand ist gruen:
  - `60 passed` fuer RAPTOR/ORCA/Obsidian-Memory-nahe Tests.

## Done Definition

RAPTOR Memory ist live, wenn alle Punkte erfuellt sind:

- Beide Feature-Flags sind in der Zielumgebung gesetzt:
  - `ODYSSEUS_OBSIDIAN_RAPTOR_ENABLED=true`
  - `ODYSSEUS_OBSIDIAN_RAPTOR_REBUILD_ENABLED=true`
- Der verwendete Vault ist der richtige Ziel-Vault und ist entsperrt.
- Der Rebuild-Caller hat `vault:write`.
- Rebuild schreibt ausschliesslich:
  - `.obsidian/odysseus/raptor/index.json`
  - `.obsidian/odysseus/raptor/summaries.json`
  - `.obsidian/odysseus/raptor/rebuild_report.json`
- RAPTOR-Status nach Rebuild:
  - `enabled=true`
  - `configured=true`
  - `index_present=true`
  - `summaries_present=true`
  - `readiness.ready=true`
  - `write_gate.state=ready`
- Gesamt-Memory-Readiness ist entweder `ready` oder alle nicht-RAPTOR-Gaps sind als bewusst deferred dokumentiert.
- Artefakte enthalten keine Raw-Note-Bodies, keine privaten Volltexte, keine Secrets, keine absoluten Host-Pfade und keine Provider-Ausgaben.
- Rollback ist getestet: Flags wieder aus, Artefaktordner archivieren/loeschen, Status blockiert sauber statt inkonsistent.

## Arbeitsplan

### Phase 1 - Live-Kontext festlegen

Status: abgeschlossen fuer den lokalen, git-ignorierten Zielkontext `vault/`.

Ziel: Kein falscher Vault, keine privaten Inhalte im Repo, kein unklarer Operator-Kontext.

Tasks:
- Ziel-Vault festlegen: lokaler Test-Vault, echter Obsidian-Vault oder Nextcloud-abgeleiteter Vault. Ergebnis Phase 1: `vault/` ist der aktuelle lokale RAPTOR-Zielkontext fuer die naechsten sicheren Schritte.
- Klar entscheiden, ob `vault/` im Repo nur Testdaten oder produktive Daten enthaelt. Ergebnis Phase 1: `vault/` ist git-ignoriert und darf als private/lokale Datenflaeche behandelt werden; Inhalte werden nicht gelesen oder ins Repo geschrieben.
- Keine Vault-Inhalte, Artefakte oder Rebuild-Reports ins Repo committen. Ergebnis Phase 1: `vault/` und `backups/` sind in `.gitignore`; RAPTOR-Artefakte bleiben ausserhalb des Git-Scopes.
- Vor Live-Rebuild Backup/Snapshot des Vaults erstellen. Ergebnis Phase 1: lokaler Snapshot wurde ausserhalb des Repos erstellt; der genaue Pfad steht nur im Operator-Handoff, nicht als persistierte Repo-Metadaten.

Exit:
- Zielpfad ist bekannt.
- Backup/Snapshot existiert.
- Datenschutzgrenzen sind klar.

Phase-1-Handoff:
- Zielpfad: lokaler git-ignorierter Vault unter `vault/`.
- Tracking-Status: keine Vault-Dateien werden von Git getrackt; `vault/` ist ignoriert.
- Datenschutz: keine Vault-Inhalte gelesen, keine Inhalte oder Artefakte ins Repo geschrieben.
- Snapshot: erstellt ausserhalb des Repos im lokalen Temp-Bereich.
- Offener Hinweis: Falls spaeter ein anderer produktiver Obsidian-/Nextcloud-Vault verwendet werden soll, muss Phase 1 fuer diesen Zielpfad erneut wiederholt werden.

### Phase 2 - Memory-Grundlagen vor RAPTOR herstellen

Status: abgeschlossen fuer den lokalen, git-ignorierten Zielkontext `vault/`.

Ziel: RAPTOR nicht auf leerem/ungeordnetem Memory-Unterbau starten.

Tasks:
- Memory Ledger aufbauen, sodass `ledger_empty` verschwindet. Ergebnis Phase 2: Ledger ist `ready`, 1 Quelle, 10 Chunks, 0 pending/stale/failed.
- Derived Index bauen, sodass `derived_index_missing` verschwindet. Ergebnis Phase 2: Derived Index ist `ready`, 1 Quelle, 10 Chunks, 1 Graph Node, 0 Edges, 0 Warnings.
- Query Layer bauen, sodass `query_index_missing`, `query_index_not_ready` und `query_index_empty` verschwinden. Ergebnis Phase 2: Query Layer ist `ready`, 1 Quelle, 10 Chunks, 0 Warnings.
- SOMT-Issues pruefen und bereinigen oder als bewusst blockierend dokumentieren.
- Freshness/Review Queue pruefen:
  - `needs_review_items` abbauen.
  - entscheiden, ob Hybrid Retrieval/Freshness Filtering live gehen soll.

Exit:
- `memory_status()` zeigt keine leeren Kernindizes mehr.
- Nicht-RAPTOR-Gaps sind entweder behoben oder bewusst deferred.

Phase-2-Handoff:
- Ausgefuehrt auf dem lokalen, git-ignorierten `vault/`.
- Geschriebene Artefakte liegen nur unter `vault/.obsidian/odysseus/memory/` und bleiben ausserhalb Git.
- Keine Quellnotizen geschrieben.
- Keine Vault-Inhalte, Snippets, privaten Pfade oder Artefakte im Repo persistiert.
- Vorher blockierten `ledger`, `derived_index` und `query_layer`; nach Phase 2 sind diese drei Familien `ready`.
- Gesamt-Memory-Gate bleibt blockiert durch `freshness`, `somt` und `raptor`.
- Verbleibende Gaps: `somt_issues_present`, `freshness_filtering_not_active`, `needs_review_items`, `raptor_index_missing`.
- Verifikation: `venv\\Scripts\\python.exe -m pytest plugins\\obsidian\\tests\\test_memory_readiness_layers.py plugins\\obsidian\\tests\\test_derived_index_backend.py plugins\\obsidian\\tests\\test_query_layer_backend.py plugins\\obsidian\\tests\\test_memory_ledger_backend.py tests\\test_obsidian_memory_mission_contract.py` -> 46 passed.

### Phase 3 - RAPTOR Flags nur in Zielumgebung aktivieren

Ziel: Rebuild-Gate gezielt oeffnen, nicht global/versehentlich.

Tasks:
- In der Zielumgebung setzen:
  - `ODYSSEUS_OBSIDIAN_RAPTOR_ENABLED=true`
  - `ODYSSEUS_OBSIDIAN_RAPTOR_REBUILD_ENABLED=true`
- Falls ORCA-Aliase verwendet werden, konsistent setzen oder bewusst nicht setzen:
  - `ODYSSEUS_ORCA_RAPTOR_ENABLED`
  - `ODYSSEUS_ORCA_RAPTOR_REBUILD_ENABLED`
- Odysseus neu starten, damit Env-Flags sicher geladen sind.
- Status pruefen:
  - Feature-Gate sollte nicht mehr `raptor_feature_flag_disabled` melden.
  - Rebuild-Gate sollte nicht mehr `raptor_rebuild_feature_flag_disabled` melden.

Exit:
- `write_gate.state=ready` fuer die Schreibberechtigung, aber RAPTOR kann weiterhin `not_configured` sein, bis Artefakte gebaut wurden.

### Phase 4 - Kontrollierter Rebuild

Ziel: RAPTOR-Artefakte einmal bounded und reproduzierbar bauen.

Tasks:
- Rebuild ueber die vorgesehene Route oder Tool-Surface ausfuehren, nicht per manueller JSON-Bastelei.
- Sicherstellen:
  - Caller hat `vault:write`.
  - Vault ist unlocked.
  - Kein Provider/LLM wird fuer Summary-Text aufgerufen.
  - Keine Netzwerkaktion ist erforderlich.
- Nach Rebuild pruefen:
  - `index.json` existiert.
  - `summaries.json` existiert.
  - optional `rebuild_report.json` existiert.
  - `raptor_status()` ist `configured=true`.

Exit:
- RAPTOR-Artefakte sind gebaut.
- Rebuild meldet `success=true`.

### Phase 5 - Privacy- und Artifact-Audit

Ziel: Sicherstellen, dass keine privaten Vollinhalte oder Maschinenpfade persistiert wurden.

Tasks:
- Artefakte strukturell pruefen, ohne private Inhalte in Chat/Repo zu kopieren.
- Erlaubt sind relative Pfade, Hashes, Status, Counts, bounded Edges, Cluster, Budgets.
- Nicht erlaubt:
  - volle Markdown Bodies
  - extrahierte Dokumentvolltexte
  - Provider/LLM-Ausgaben
  - Tokens, Passwoerter, Chat-IDs
  - absolute Pfade wie `C:\Users\...`
  - unbounded Graph-Dumps

Exit:
- Artifact-Audit ist sauber.
- Falls nicht sauber: Flags aus, Artefaktordner archivieren/loeschen, Rebuild-Code/Policy reparieren.

### Phase 6 - Readiness-Gaps nach Rebuild schliessen

Ziel: Aus "Artefakte existieren" wird "RAPTOR ist einsatzbereit".

Tasks:
- `raptor_status()` erneut auswerten.
- Moegliche verbleibende Gaps beheben:
  - `source_hash_changed`: Rebuild erneut nach finalem Source-Stand.
  - `source_missing`: alte Artefakte enthalten Quellen, die nicht mehr existieren; Rebuild aus aktuellen Quellen.
  - `source_isolated_from_default_retrieval`: Review/Status-Policy klaeren.
  - `raptor_metadata_dirty`: Rebuild aus aktuellem Stand.
  - `raptor_metadata_tainted`: isolierte/deprecated/quarantined Quellen pruefen.
  - `raptor_index_invalid` / `raptor_summaries_invalid`: Artefakte loeschen/archivieren und neu bauen.
- Achtung: Der aktuelle Code bewertet `tainted=true` als nicht ready. Wenn der Vault bewusst isolierte/deprecated Quellen enthalten darf, brauchen wir vor Live-Go eine klare Produktentscheidung oder Code-Anpassung, ob das `ready_with_isolated_sources` statt blockierend sein soll.

Exit:
- `raptor_status().readiness.ready=true` oder klare Entscheidung, welches Gap bewusst blockiert/deferred bleibt.

### Phase 7 - Retrieval/Memory Integration verifizieren

Ziel: RAPTOR ist nicht nur gebaut, sondern in der Memory-Diagnostik sinnvoll nutzbar.

Tasks:
- `memory_status()` pruefen:
  - RAPTOR-Familie ready.
  - Gesamt-Gate nicht durch RAPTOR blockiert.
- Bounded Graph View pruefen:
  - Edge/Page-Limits greifen.
  - Cursor/Clipping-Metadaten vorhanden.
  - Keine Vollgraph-Ausgabe.
- ORCA/Obsidian Statusrouten pruefen:
  - `/raptor/status`
  - `/raptor/graph`
  - Memory status dashboard payload.
- Keine UI-Neugestaltung in diesem Schritt; nur API/Status/Diagnostik.

Exit:
- RAPTOR kann sicher gelesen werden.
- Bounded Graph View funktioniert.

### Phase 8 - Live-Betriebsregeln

Ziel: RAPTOR bleibt kontrollierbar.

Tasks:
- Rebuild nicht dauerhaft automatisch laufen lassen, bevor wir Delta-/Trigger-Policy geklaert haben.
- Manuelle Rebuild-Regel definieren:
  - nach grossem Import
  - nach Nextcloud-Ingestion
  - nach Review-Queue-Abschluss
  - nach Source-Status-Aenderungen
- Monitoring:
  - readiness state
  - source_count
  - isolated_sources
  - graph_clipped
  - warnings
  - cache hits/misses
- Rollback:
  - `ODYSSEUS_OBSIDIAN_RAPTOR_REBUILD_ENABLED=false`
  - optional `ODYSSEUS_OBSIDIAN_RAPTOR_ENABLED=false`
  - Artefaktordner archivieren/loeschen
  - Odysseus neu starten

Exit:
- Operator weiss, wann Rebuild erlaubt ist.
- Rollback ist dokumentiert.

## Empfohlene Reihenfolge fuer uns

1. Einen kleinen Test-Vault oder freigegebenen Ausschnitt verwenden.
2. Memory Ledger / Derived Index / Query Layer fuer diesen Ausschnitt aufbauen.
3. RAPTOR-Flags nur lokal/testweise setzen.
4. RAPTOR-Rebuild laufen lassen.
5. Artifact-Audit ausfuehren.
6. Readiness-Gaps auswerten.
7. Erst danach echten Vault/Nextcloud-Ausschnitt live schalten.

## Offene Entscheidung

Wichtigste Produktentscheidung vor Live-Go:

Soll ein Vault mit isolierten/deprecated/quarantined Quellen RAPTOR komplett blockieren (`tainted`) oder als `ready_with_isolated_sources` gelten, solange diese Quellen aus Default Retrieval ausgeschlossen bleiben?

Der aktuelle Code blockiert bei `tainted`. Fuer echte produktive Vaults ist das wahrscheinlich zu streng, weil Review-/Archiv-/Quarantaene-Quellen normal sind.
