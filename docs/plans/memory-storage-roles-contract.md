# Memory Storage Roles Contract

Stand: 2026-06-17

Status: pre-1.0 operator contract for memory storage roles and cleanup wording

Dieses Dokument klaert die Rollen der Memory-Speicher vor `1.0`, ohne
Migration, Loeschen, Rebuild oder Datenzugriff. Es beschreibt nur, welche
Schicht kanonisch ist, welche Schichten abgeleitete Indizes sind und welche
Schichten nicht zu Personal Memory gehoeren.

## Zweck

Vor `1.0` muessen Nutzer und Admins klar unterscheiden koennen:

- was die Primaerwahrheit fuer Personal Memory ist
- was nur ein rebuildbarer Suchindex ist
- was ein Dokument-/Vault-/Upload-Wissensindex ist
- was eine spaetere Graph-/Summary-Ebene ist

Dieses Dokument ist ein Rollenvertrag, kein Migrationsplan.

## Nicht-Ziele

Dieser Slice deckt bewusst nicht ab:

- kein Loeschen von Memory-Daten
- keine Migration zwischen Speichern
- kein automatischer oder manueller Rebuild
- keine Graph-, RAPTOR-, Postgres-, Plugin-, Telegram-, Nextcloud-,
  Obsidian- oder Routenarbeit
- kein Lesen, Zitieren oder Loggen von Inhalten aus `data/memory.json`
- keine Hardcodierung echter Counts, Groessen oder Betreiberbeobachtungen

## Rolle Von `memory.json`

`data/memory.json` ist der kanonische Personal-Memory-Store.

Das bedeutet:

- `memory.json` ist die Primaerwahrheit fuer Personal Memory.
- `memory.json` ist nicht bloss Legacy oder Altlast.
- Aenderungen an Personal Memory muessen semantisch von dieser kanonischen
  Schicht her gedacht werden.
- Vor `1.0` darf aus diesem Vertrag keine Aufraeum-, Loesch- oder
  Migrationspflicht abgeleitet werden.

Operator-Sprache:

- "canonical personal memory store"
- "primary truth for personal memory"

Nicht sagen:

- "legacy file that should be replaced soon"
- "stale copy"
- "safe to delete before rebuild"

## Rolle Von Vector Memory / Chroma

Vector Memory oder Chroma ist ein abgeleiteter semantischer Suchindex ueber
Personal Memory.

Das bedeutet:

- der Vector Index ist nicht die Primaerwahrheit
- der Vector Index ist aus dem kanonischen Personal Memory konzeptionell
  rebuildbar
- der Vector Index dient der semantischen Suche, nicht der finalen
  Wahrheitsdefinition
- Vor `1.0` wird daraus kein automatischer Rebuild und keine Bereinigung
  abgeleitet

Operator-Sprache:

- "derived semantic index over personal memory"
- "rebuildable from canonical personal memory"

Nicht sagen:

- "new source of truth"
- "authoritative memory store"
- "must be rebuilt now"

## Rolle Von Knowledge / RAG

RAG oder Knowledge Index ist der Dokument-, Vault- und Upload-Wissensindex.

Das bedeutet:

- RAG ist nicht Personal Memory
- RAG ist fuer Dokumente, Vaults und Uploads gedacht
- RAG darf sprachlich nicht mit `memory.json` oder dem Vector Memory Index
  vermischt werden
- RAG-Statistiken duerfen spaeter read-only gemessen werden, aber nicht als
  Personal-Memory-Counts verkauft werden

Operator-Sprache:

- "knowledge index"
- "document/vault/upload retrieval index"
- "not personal memory"

Nicht sagen:

- "same as memory"
- "personal memory backend"
- "canonical memory"

## Rolle Von Graph / RAPTOR Memory

Graph-/RAPTOR-Memory ist eine separate Relations-, Cluster- oder Summary-Ebene.

Das bedeutet:

- Graph/RAPTOR ist keine Ersatzwahrheit fuer Personal Memory
- Graph/RAPTOR ist nicht dasselbe wie RAG
- Graph/RAPTOR ist nicht dasselbe wie der Vector Index ueber Personal Memory
- Graph/RAPTOR bleibt eine eigene erweiterte Schicht und darf vor `1.0`
  sprachlich nicht in die Personal-Memory-Aufraeumlogik hineingezogen werden

Operator-Sprache:

- "separate relation/summary layer"
- "future or extended memory layer"
- "do not conflate with personal memory"

Nicht sagen:

- "the real memory graph"
- "replacement for memory.json"
- "required for 1.0 cleanup"

## Read-Only Messsprache

Falls Counts, Groessen oder Health-Werte spaeter angezeigt werden, muessen sie
read-only und schichtklar beschrieben werden.

Zulaessig:

- Personal Memory entries
- `memory.json` bytes or path
- Vector index health
- Vector index count
- RAG document count
- optional bounded artifact sizes

Nicht zulaessig:

- Memory-Inhalte
- personenbezogene Beispiele
- implizite Schlussfolgerung, dass ein hoher oder niedriger Count Loeschen,
  Migration oder Rebuild erzwingt

Dieses Dokument gibt keine echten Zahlen vor. Solche Werte duerfen spaeter nur
read-only gemessen und operator-seitig bewertet werden.

## Cleanup-Sprache Vor 1.0

Vor `1.0` bedeutet "cleanup" in diesem Track:

- Rollen sauber benennen
- Speicher semantisch trennen
- spaetere read-only Messpunkte vorbereiten
- irrefuehrende Begriffe vermeiden

Vor `1.0` bedeutet "cleanup" in diesem Track nicht:

- Daten entfernen
- Speicher migrieren
- Indizes automatisch rebuilden
- Schichten zusammenlegen

## Go / Partial / No-Go

### Go

Go ist angemessen, wenn:

- `memory.json` klar als kanonischer Personal-Memory-Store beschrieben ist
- Vector Memory klar als abgeleiteter, rebuildbarer semantischer Index
  beschrieben ist
- RAG klar als Dokument-/Vault-/Upload-Wissensindex beschrieben ist
- Graph/RAPTOR klar als separate Relation-/Summary-Ebene beschrieben ist
- keine Cleanup-Sprache Loeschen, Migration oder Rebuild impliziert

### Partial

Partial ist angemessen, wenn:

- die Rollen sprachlich klar dokumentiert sind
- read-only Stats-Modelle oder Admin-Stats-Routen noch separat ausstehen
- keine riskante Aktion vorgeschlagen oder gestartet wird

### No-Go

No-Go ist angemessen, wenn:

- `memory.json` als bloss Legacy oder als loeschbar beschrieben wird
- Vector Memory als Primaerwahrheit verkauft wird
- RAG und Personal Memory vermischt werden
- Graph/RAPTOR als Pflichtschritt fuer `1.0` Cleanup dargestellt wird
- Loeschen, Migration, automatischer Rebuild oder Content-Leakage vorgeschlagen
  oder ausgefuehrt werden

## Nutzer- Und Admin-Sprache

Empfohlene kurze Sprache:

- Personal Memory: canonical store
- Vector Memory: derived semantic index
- Knowledge / RAG: document knowledge index
- Graph / RAPTOR Memory: separate relation/summary layer

Vermeiden:

- "alles ist dasselbe Memory"
- "alte Schicht kann weg"
- "Index ist Wahrheit"
- "Cleanup bedeutet Rebuild"

## Rollen

### Alice

Alice liefert die klare Nutzer- und Operator-Sprache fuer die vier
Speicherrollen und die Go/Partial/No-Go-Logik.

### Bob

Bob kann spaeter read-only Stats-Modelle bauen, die Schichten messen, ohne
Memory-Inhalte preiszugeben und ohne Rebuild oder Migration auszufuehren.

### Charlie

Charlie kontrolliert Scope, Tests, Worktree, Integration und stoppt jede
Verschiebung in Loeschen, Migration, Rebuild oder Content-Leakage.

## Stop-Regeln

Sofort stoppen, wenn:

- Inhalte aus `data/memory.json` gelesen, kopiert, zitiert oder geloggt werden
- Loeschen, Migration oder Rebuild vorgeschlagen oder als Voraussetzung fuer
  `1.0` dargestellt werden
- RAG, Graph/RAPTOR oder Vector Index als kanonische Personal-Memory-Wahrheit
  beschrieben werden
- Counts oder Groessen als harte Release-Zahlen fest verdrahtet werden
- Scope in Runtime-, Route-, Test- oder Secret-Arbeit abgleitet

## Abschluss

Vor `1.0` gilt: `memory.json` bleibt die kanonische Primaerwahrheit fuer
Personal Memory. Vector Memory bleibt ein abgeleiteter semantischer Index
darueber. RAG bleibt der Wissensindex fuer Dokumente, Vaults und Uploads.
Graph/RAPTOR bleibt eine separate Relations- und Summary-Ebene. Dieser Track
klaert Rollen und Readiness-Sprache, nicht Datenumbauten.
