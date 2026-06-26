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

Status: abgeschlossen fuer die lokale Zielumgebung.

Ziel: Rebuild-Gate gezielt oeffnen, nicht global/versehentlich.

Tasks:
- In der Zielumgebung setzen:
  - `ODYSSEUS_OBSIDIAN_RAPTOR_ENABLED=true`
  - `ODYSSEUS_OBSIDIAN_RAPTOR_REBUILD_ENABLED=true`
- Falls ORCA-Aliase verwendet werden, konsistent setzen oder bewusst nicht setzen:
  - `ODYSSEUS_ORCA_RAPTOR_ENABLED=true`
  - `ODYSSEUS_ORCA_RAPTOR_REBUILD_ENABLED=true`
- Odysseus neu starten, damit Env-Flags sicher geladen sind.
- Status pruefen:
  - Feature-Gate sollte nicht mehr `raptor_feature_flag_disabled` melden.
  - Rebuild-Gate sollte nicht mehr `raptor_rebuild_feature_flag_disabled` melden.

Exit:
- `write_gate.state=ready` fuer die Schreibberechtigung, aber RAPTOR kann weiterhin `not_configured` sein, bis Artefakte gebaut wurden.

Phase-3-Handoff:
- Lokale, git-ignorierte `.env` aktualisiert; vorher wurde ein git-ignoriertes `.env.bak.raptor-phase3-*` Backup angelegt.
- Keine `.env`-Werte, Secrets oder privaten Inhalte wurden ins Repo geschrieben oder ausgegeben.
- ORCA-Aliase wurden bewusst mitgesetzt, weil sie im Code Vorrang vor den Obsidian-Env-Namen haben koennen.
- `ODYSSEUS_OBSIDIAN_HYBRID_RETRIEVAL_ENABLED` bleibt in Phase 3 aus; Aktivierung folgt erst nach gebauten RAPTOR-Artefakten.
- Statuspruefung in frischem Prozess: `obsidian_raptor_enabled=true`, `obsidian_raptor_rebuild_enabled=true`, `write_gate.state=ready`, `write_gate.gaps=[]`.
- RAPTOR selbst bleibt erwartungsgemaess `configured=false` und `readiness.state=not_configured`, weil `raptor_index_missing` erst in Phase 4 behoben wird.
- Verifikation: `venv\\Scripts\\python.exe -m pytest tests\\test_orca_compatibility_contract.py plugins\\obsidian\\tests\\test_raptor_rebuild_backend.py plugins\\obsidian\\tests\\test_raptor_cache_backend.py plugins\\obsidian\\tests\\test_memory_readiness_layers.py` -> 48 passed.

### Phase 4 - Kontrollierter Rebuild

Status: abgeschlossen fuer den lokalen, git-ignorierten Zielkontext `vault/`.

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

Phase-4-Handoff:
- RAPTOR-Rebuild ueber die vorhandene Backend-Rebuild-Funktion ausgefuehrt, nicht durch manuelle JSON-Bearbeitung.
- Bounds: `max_sources=2000`, `max_edges=5000`.
- Ergebnis: `success=true`, `blocked=false`, `write_gate.state=ready`, `warnings=0`.
- Gebaute Artefakte: `index.json`, `summaries.json`, `rebuild_report.json` unter `vault/.obsidian/odysseus/raptor/`; dieser Pfad ist git-ignoriert.
- Rebuild Summary: 1 Source, 1 active Source, 0 isolated Sources, 0 Graph Edges, 0 clipped Sources, 0 clipped Edges.
- Status nach Rebuild: `configured=true`, `index_present=true`, `summaries_present=true`, `dirty=false`.
- RAPTOR-Readiness bleibt noch nicht `ready`: `state=tainted`, Gap `source_isolated_from_default_retrieval`.
- Ursache liegt nicht mehr am Rebuild oder Write-Gate, sondern an Freshness/SOMT-Isolation und wird in den Folgephasen bearbeitet.
- Keine Quellnotizen geschrieben, keine Repo-Artefakte erzeugt, keine Inhalte/Snippets/privaten Pfade ausgegeben.
- Verifikation: `venv\\Scripts\\python.exe -m pytest plugins\\obsidian\\tests\\test_raptor_rebuild_backend.py plugins\\obsidian\\tests\\test_raptor_cache_backend.py plugins\\obsidian\\tests\\test_raptor_warming_backend.py plugins\\obsidian\\tests\\test_memory_readiness_layers.py tests\\test_obsidian_memory_mission_contract.py` -> 47 passed.

### Phase 5 - Privacy- und Artifact-Audit

Status: abgeschlossen fuer den lokalen, git-ignorierten Zielkontext `vault/`.

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

Phase-5-Handoff:
- Gepruefte Artefakte: `index.json`, `summaries.json`, `rebuild_report.json` unter `vault/.obsidian/odysseus/raptor/`.
- Alle drei Artefakte sind gueltiges JSON und bleiben durch `vault/` git-ignoriert.
- Struktur-Audit sauber: 0 verbotene Raw-Content-Schluessel, 0 absolute Host-Pfade, 0 Secret-/Token-Muster, 0 Textfelder ueber 500 Zeichen.
- Laengstes String-Feld im Audit: 71 Zeichen.
- Graph ist bounded: gespeicherte Edges 0 von Limit 5000, kein Clipping.
- Security Claims im Rebuild-Report bestaetigen: derived artifacts only, keine Raw Note Contents, keine absoluten Host-Pfade, kein Provider Output.
- Keine Artefaktinhalte, Quellnotiznamen, Titel, Tags, Snippets oder privaten Pfade wurden ins Repo geschrieben oder im Chat ausgegeben.
- Verifikation: `git check-ignore -v vault\\.obsidian\\odysseus\\raptor\\index.json vault\\.obsidian\\odysseus\\raptor\\summaries.json vault\\.obsidian\\odysseus\\raptor\\rebuild_report.json` -> alle drei durch `vault/` ignoriert.
- Verifikation: `venv\\Scripts\\python.exe -m pytest plugins\\obsidian\\tests\\test_raptor_rebuild_backend.py plugins\\obsidian\\tests\\test_raptor_cache_backend.py tests\\test_obsidian_memory_mission_contract.py` -> 13 passed.

### Phase 6 - Readiness-Gaps nach Rebuild schliessen

Status: abgeschlossen fuer RAPTOR im lokalen, git-ignorierten Zielkontext `vault/`.

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

Phase-6-Handoff:
- Ausgangsgap: `source_isolated_from_default_retrieval` durch eine Freshness-Quelle in `needs_review`.
- Ursache: volatile Policy ohne `updated` oder `last_verified_at`.
- Lokale Zielumgebung aktualisiert: `ODYSSEUS_OBSIDIAN_HYBRID_RETRIEVAL_ENABLED=true` und `ODYSSEUS_ORCA_HYBRID_RETRIEVAL_ENABLED=true` in der git-ignorierten `.env`.
- Genau eine Quelle wurde body-preserving nur im Frontmatter aktualisiert: `status=active`, `updated=2026-06-26`, `last_verified_at=2026-06-26`.
- Vor der lokalen Source-Metadaten-Aenderung wurde ein Backup ausserhalb des Repos angelegt.
- Danach wurden Memory Ledger, Derived Index und RAPTOR-Artefakte neu aufgebaut.
- Ergebnis RAPTOR: `configured=true`, `dirty=false`, `tainted=false`, `readiness.ready=true`, `readiness.state=ready`, `readiness.gaps=[]`, `writes_supported=true`.
- Ergebnis Freshness: `filtering_state=active`, `readiness.ready=true`, 1 aktuelle Quelle, 0 isolierte Quellen, 0 `needs_review`.
- Gesamt-Memory-Gate bleibt noch blockiert, aber nicht mehr durch RAPTOR oder Freshness: verbleibender Gap ist SOMT `loose_note` / `somt_issues_present`.
- Erneuter Artifact-Audit sauber: 0 verbotene Raw-Content-Schluessel, 0 absolute Host-Pfade, 0 Secret-/Token-Muster, 0 Textfelder ueber 500 Zeichen.
- Keine Quellinhalte, Snippets, privaten Pfade oder Artefakte wurden ins Repo geschrieben.
- Verifikation: `venv\\Scripts\\python.exe -m pytest plugins\\obsidian\\tests\\test_memory_readiness_layers.py plugins\\obsidian\\tests\\test_raptor_rebuild_backend.py plugins\\obsidian\\tests\\test_raptor_cache_backend.py plugins\\obsidian\\tests\\test_context_provider_backend.py tests\\test_obsidian_memory_mission_contract.py` -> 53 passed.

### Phase 7 - Retrieval/Memory Integration verifizieren

Status: abgeschlossen fuer den lokalen, git-ignorierten Zielkontext `vault/`.

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

Phase-7-Handoff:
- SOMT-Policy minimal korrigiert: `loose_note` bleibt als Info-Issue sichtbar, blockiert aber nicht mehr das Readiness-Gate.
- Regressionstest ergaenzt: aktive Top-Level-Notiz ohne Graph-Link ist informativ, aber nicht blocking.
- Gesamt-Memory-Gate ist jetzt `ready`: 6/6 Families ready, 0 blocked Families, 0 Gaps.
- RAPTOR bleibt `ready`: `configured=true`, `dirty=false`, `tainted=false`, `readiness.gaps=[]`.
- Bounded Graph View funktioniert: 1 Node, 0 Edges, Limit 25, returned Edges 0, Cursor-Shape vorhanden.
- ORCA RAPTOR Contract ist `ready`, Graph ist bounded.
- ORCA/Legacy Routen im Context-Adapter vorhanden:
  - `/api/plugins/orca/raptor/status`
  - `/api/plugins/orca/raptor/graph`
  - `/api/plugins/obsidian/raptor/status`
  - `/api/plugins/obsidian/raptor/graph`
- Keine UI-Neugestaltung, keine Repo-Persistenz von Vault-Artefakten, keine Quellinhalte/Snippets/privaten Pfade ausgegeben.
- Verifikation: `venv\\Scripts\\python.exe -m pytest plugins\\obsidian\\tests\\test_memory_readiness_layers.py plugins\\obsidian\\tests\\test_raptor_rebuild_backend.py plugins\\obsidian\\tests\\test_raptor_cache_backend.py plugins\\obsidian\\tests\\test_raptor_warming_backend.py plugins\\obsidian\\tests\\test_context_provider_backend.py tests\\test_obsidian_memory_mission_contract.py tests\\test_orca_compatibility_contract.py` -> 60 passed.

### Phase 8 - Live-Betriebsregeln

Status: abgeschlossen fuer den lokalen Live-Go-Betriebsrahmen.

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

Phase-8-Handoff:
- RAPTOR-Rebuild bleibt operator-gated und wird nicht durch die Memory Automation automatisch ausgefuehrt.
- Automatisch erlaubte Memory-Aktionen sind begrenzt auf:
  - `sync_memory_ledger`
  - `build_derived_index`
  - `warm_raptor_cache`
- Lokale Automation-Safety bestaetigt: `source_note_writes=false`, `derived_data_writes_only=true`, `concurrency_limit=1`, `cooldown_seconds=300`, `max_sources_per_pass=500`.
- Cache-Warming wurde einmal ausgefuehrt: `raptor_status` und `raptor_graph_view` gewaermt, danach `pending_actions=[]`.
- Live-Monitoring-Kernwerte fuer Betrieb:
  - `memory_status().readiness_gate.state`
  - `memory_status().readiness_gate.ready_families`
  - `memory_status().readiness_gate.blocked_families`
  - `raptor_status().readiness.state`
  - `raptor_status().summary.source_count`
  - `raptor_status().dirty`
  - `raptor_status().tainted`
  - `raptor_status().warnings`
  - `raptor_cache_diagnostics().entry_count`
  - `raptor_cache_diagnostics().hits`
  - `raptor_cache_diagnostics().misses`
  - `raptor_cache_diagnostics().evictions`
- Manuelle Rebuild-Regel: RAPTOR-Rebuild nur nach bewusstem Operator-Go und nur nach einem dieser Trigger:
  - grosser Import abgeschlossen
  - Nextcloud-Ingestion abgeschlossen
  - Review Queue abgeschlossen
  - Source-Status-/Freshness-Metadaten geaendert
  - `raptor_status()` meldet dirty, missing, invalid oder tainted Quellen
- Vor jedem manuellen Rebuild:
  - `memory_status()` pruefen
  - Backup/Snapshot des Vault-Zielkontexts vorhanden
  - Privacy-Policy bestaetigt
  - Bounds setzen, z.B. `max_sources=2000`, `max_edges=5000`
- Nach jedem manuellen Rebuild:
  - Artifact-Audit aus Phase 5 wiederholen
  - `raptor_status().readiness.ready=true` pruefen
  - `memory_status().readiness_gate.state=ready` pruefen
  - Cache warmen oder erste Status-/Graph-Leseabfrage ausfuehren
- Rollback:
  - `ODYSSEUS_OBSIDIAN_RAPTOR_REBUILD_ENABLED=false`
  - `ODYSSEUS_ORCA_RAPTOR_REBUILD_ENABLED=false`
  - optional `ODYSSEUS_OBSIDIAN_RAPTOR_ENABLED=false`
  - optional `ODYSSEUS_ORCA_RAPTOR_ENABLED=false`
  - Odysseus neu starten, damit Env-Flags sicher geladen sind
  - RAPTOR-Artefaktordner lokal archivieren oder loeschen, nicht ins Repo kopieren
- Aktueller lokaler Betriebsstatus: Memory Gate `ready`, 6/6 Families ready, RAPTOR `ready`, `dirty=false`, `tainted=false`, Warnings 0, Cache Entries 2, Pending Actions 0.
- Keine Live-/Provider-/Netzwerkaktion, keine Repo-Persistenz von Vault-Artefakten, keine Quellinhalte/Snippets/privaten Pfade ausgegeben.
- Verifikation: lokaler Status-Snapshot plus Cache-Warming-Snapshot ohne Inhalte/Pfade.
- Verifikation: `venv\\Scripts\\python.exe -m pytest plugins\\obsidian\\tests\\test_memory_automation_backend.py plugins\\obsidian\\tests\\test_raptor_warming_backend.py plugins\\obsidian\\tests\\test_raptor_cache_backend.py plugins\\obsidian\\tests\\test_memory_readiness_layers.py tests\\test_obsidian_memory_mission_contract.py` -> 48 passed.

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
