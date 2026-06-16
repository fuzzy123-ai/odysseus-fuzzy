# Query Budget UX Contract

Stand: 2026-06-16

Status: **MS3A Produkt-/UX-/Charlie-Vertrag fuer `0.13.x Query Budgets`**

Quellen:

- `docs/plans/memory-scale-foundation-roadmap.md`
- `docs/plans/memory-store-interface-contract.md`
- `docs/plans/memory-diagnostics-lens-contract.md`

Dieser Vertrag definiert die sichtbare Sprache fuer Query Budgets, Partial Results und Clipping. `MS3A` baut bewusst noch keine echte Query-Runtime, keinen Graph-Endpoint und kein Dashboard. Der Slice friert nur ein, wie begrenzte Ergebnisse spaeter produktiv nutzbar, ehrlich erklaert und fuer Charlie sicher auswertbar bleiben.

## Ziel

Odysseus soll bei grossen Datenmengen lieber kleine, erklaerte und fortsetzbare Ergebnisse liefern als unbounded zu laden oder still Daten zu verlieren.

Die Query Budget UX soll:

- Nutzer erkennen lassen, wenn Ergebnisse bewusst begrenzt oder teilweise sind
- Cursor, Zeitbudgets und Graph-Grenzen sichtbar und verstaendlich machen
- Clipping als kontrolliertes Verhalten statt als kaputtes Produkt erklaeren
- Charlie klar zeigen, wann ein partial oder clipped Result fuer Folgearbeit reicht und wann nicht
- Bob ein kleines, validierbares Modell fuer Query Budgets ermoeglichen

## Was ist ein Query Budget?

Ein Query Budget ist die kontrollierte Grenze fuer teure Query-, Memory-, Graph- oder UI-nahe Abrufpfade.

Es beschreibt nicht nur, wie viel geladen werden darf, sondern auch:

- wann eine Query teilweise zurueckkommt
- wann sie gekappt wurde
- wann ein Cursor oder Folgeabruf moeglich ist
- wann ein Budget erschoepft ist

Ein Query Budget ist:

- kleiner als eine komplette Query-Runtime
- strenger als freie "show more"-Prosa
- kompatibel mit bounded Store Interfaces und Diagnostics Lenses

## Begriffe

### `query_budget_id`

Stabile Kennung der Budget-Regel oder Budget-Instanz fuer eine Query-Lage.

- identifiziert den Budgetkontext

### `query_ref`

Referenz auf die konkrete Query, Query-Phase oder Retrieval-Lage.

- verbindet Budget und Ergebnis

### `limit`

Maximale Anzahl von Elementen in einer Rueckgabe.

### `cursor`

Fortsetzungsmarke fuer weitere, inkrementelle Ergebnisse.

### `time_budget_ms`

Maximales Zeitbudget fuer die Query oder Teilphase.

### `token_budget`

Maximales Text- oder Answer-Budget fuer Folgephasen.

### `max_nodes`

Maximale Anzahl von Graph-Knoten in einer Antwort oder Expansion.

### `max_edges`

Maximale Anzahl von Graph-Kanten in einer Antwort oder Expansion.

### `depth`

Maximale Graph-Tiefe oder Nachbarschaftstiefe.

### `partial`

Marker, dass das Ergebnis nutzbar, aber nicht vollstaendig ist.

### `clipped`

Marker, dass Ergebnisse bewusst abgeschnitten oder begrenzt wurden.

### `exhausted`

Marker, dass ein Budget aufgebraucht wurde und ohne anderen Folgepfad kein weiteres Ergebnis mehr aus diesem Lauf kommt.

### `next_cursor`

Die Fortsetzungsreferenz fuer den naechsten Ausschnitt, falls zulaessig.

### `reason`

Die kleinste lesbare Begruendung, warum ein Result partial, clipped, exhausted, blocked oder failed ist.

### `next_action`

Die kleinste konkrete Folgeaktion, die aus der Budgetlage resultiert.

- Beispiel: "mit Cursor fortsetzen", "Graph-Tiefe senken", "Dispatch stoppen"

## Nutzer-Sicht

Partial Results sollen fuer Nutzer nutzbar sein, aber niemals wie vollstaendige Wahrheit wirken.

Der Nutzer soll erkennen:

- dieses Ergebnis ist brauchbar
- es ist aber begrenzt
- warum es begrenzt ist
- ob und wie mehr geholt werden kann

### Nutzer-Grundsaetze

- `partial` ist erlaubt, wenn es ehrlich als Teilmenge gekennzeichnet ist
- `clipped` ist kein stiller Datenverlust
- `exhausted` bedeutet Budgetende, nicht notwendigerweise Fehler
- `blocked` oder `failed` muessen von `partial` sauber getrennt bleiben

Der Nutzer braucht nicht:

- tiefe Query-Debug-Logs
- rohe Cursor-Serien
- technische Datenbankdetails

## Charlie-Sicht

Charlie braucht eine praezisere Budget-Lage, damit Folgearbeit sicher bleibt.

Charlie soll erkennen koennen:

- reicht dieses Result fuer den naechsten Slice
- muss mit Cursor fortgesetzt werden
- ist das Budget nur teilweise ausgeschoepft oder wirklich erschoepft
- blockiert die Budgetlage sichere Folgearbeit

Charlie braucht pro Budget-Lage mindestens:

- `query_budget_id`
- `query_ref`
- `limit`
- `cursor`
- `time_budget_ms`
- `token_budget`
- `max_nodes`
- `max_edges`
- `depth`
- `partial`
- `clipped`
- `exhausted`
- `next_cursor`
- `reason`
- `next_action`

## Budget-Regeln

### Keine unbounded Query

Keine Query darf still "alles" laden.

Das gilt fuer:

- Memory-Antworten
- Query-Layer
- Graph-Expansionen
- UI-nahe Payloads

### Cursor oder Grenze

Jeder teure Abrufpfad braucht spaeter mindestens eine klare Begrenzung oder einen Fortsetzungsmechanismus.

### Zeitbudget

Wenn Zeitbudgets greifen, soll das Result lieber partial oder clipped werden als unendlich zu haengen.

### Graph-Budgets

`max_nodes`, `max_edges` und `depth` muessen als echte Nutzer- und Charlie-Lage lesbar sein.

### UI-Payload-Budgets

Auch UI-nahe Resultate duerfen keine stillen Vollmengen durchlassen.

Regel:

- Every expensive path has a limit or cursor.

## Statussprache

In `MS3A` ist die sichtbare Statusmenge:

- `within_budget`
- `partial`
- `clipped`
- `exhausted`
- `blocked`
- `failed`

### `within_budget`

Das Result ist im gesetzten Budget vollstaendig oder ausreichend ohne sichtbare Kappung.

### `partial`

Das Result ist teilweise, aber nutzbar und ehrlich als Teilmenge markiert.

### `clipped`

Das Result wurde bewusst an einer Grenze gekappt.

### `exhausted`

Das Budget wurde aufgebraucht; ohne anderen Folgepfad gibt es aus diesem Lauf nicht mehr.

### `blocked`

Die Budgetlage verhindert sichere oder sinnvolle Folgearbeit.

### `failed`

Die Query oder Teilphase ist fehlgeschlagen.

## Regeln fuer Partial und Clipping

### `partial`

- darf fuer Nutzer sichtbar brauchbar sein
- braucht eine lesbare Begruendung
- darf nicht als verborgenes `failed` missbraucht werden

### `clipped`

- braucht einen sichtbaren Hinweis auf Kappung
- muss nach Moeglichkeit eine Fortsetzung oder Alternativaktion anbieten
- darf nicht wie komplette Vollstaendigkeit dargestellt werden

### `exhausted`

- muss sagen, welches Budget zu Ende ging
- ist nicht automatisch ein Fehler
- kann fuer Charlie dennoch Stop oder Kurswechsel bedeuten

## Regeln fuer Charlie-Dispatch

Charlie darf trotz partial oder clipped weiterarbeiten lassen, wenn:

- das Result fuer die Folgeaufgabe fachlich ausreicht
- Clipping klar markiert ist
- `next_action` verstaendlich bleibt
- kein hartes Budgetende sicherheitskritische Teile versteckt

Charlie muss stoppen, wenn:

- das Result wesentliche Wahrheit nur noch spekulativ traegt
- `blocked` oder `failed` vorliegt
- `exhausted` ohne tragfaehigen Folgepfad eintritt
- kein Cursor oder keine alternative Folgeaktion vorhanden ist, obwohl mehr noetig waere

## Nutzertexte und Lens-Grundsaetze

- Begrenzung ist ehrlicher als versteckter Datenverlust.
- Partial Results sollen Vertrauen schaffen, nicht Verwirrung.
- Cursor oder "mehr laden" sind Produktverhalten, nicht Debug-Reste.
- `clipped` und `failed` duerfen sich sprachlich nicht vermischen.
- `within_budget` ist keine Aussage ueber Wahrheit aller Daten, sondern ueber den aktuellen Budgetpfad.

## Nicht-Ziele

`MS3A` baut bewusst noch nicht:

- keine echte Query-Runtime
- keinen Graph-Endpoint
- kein Dashboard
- keine DB-Migration
- keine Postgres-/Qdrant-/Kuzu-/UMAP-GMM-Implementierung

Der Slice friert nur die sichtbare Budget- und Result-Sprache ein, auf der Runtime, Graph und UI spaeter aufbauen koennen.

## Mindest-Handoff an Bob

Bobs erstes Backend-Modell fuer `MS3B-query-budget-model-spike` soll mindestens diese Felder validieren:

- `query_budget_id`
- `query_ref`
- `limit`
- `cursor`
- `time_budget_ms`
- `token_budget`
- `max_nodes`
- `max_edges`
- `depth`
- `partial`
- `clipped`
- `exhausted`
- `next_cursor`
- `reason`
- `next_action`

Minimum-Regeln fuer das Modell:

- jede teure Budgetlage darf nicht ohne Grenze oder Cursor auskommen
- `partial`, `clipped` und `exhausted` muessen explizit lesbar sein, nicht nur implizit
- `clipped` braucht eine lesbare Begruendung
- `exhausted` braucht Hinweis, welches Budget zu Ende ging
- `next_cursor` darf nur gesetzt sein, wenn echte Fortsetzung moeglich ist
- `blocked` oder `failed` muessen eine lesbare Folge- oder Stop-Information tragen
- das Modell darf keinen stillen `load_all`-Default erlauben

Sinnvolle, aber fuer den kleinsten Start optionale Zusatzfelder:

- `status`
- `summary`
- `payload_bytes`
- `returned_count`
- `budget_dimension`
- `can_continue`

## Akzeptanz fuer diesen Vertrag

`MS3A-query-budget-ux-contract` ist erfuellt, wenn:

- die Begriffe `query_budget_id`, `query_ref`, `limit`, `cursor`, `time_budget_ms`, `token_budget`, `max_nodes`, `max_edges`, `depth`, `partial`, `clipped`, `exhausted`, `next_cursor`, `reason`, `next_action` klar definiert sind
- Nutzer-Sicht partial/clipped Results als nutzbar, aber ehrlich begrenzt erklaert
- Charlie-Sicht klar macht, wann trotz partial/clipped weitergearbeitet werden darf und wann nicht
- Budget-Regeln unbounded Query-, Graph-, Memory- und UI-Pfade ausschliessen
- die Statussprache `within_budget`, `partial`, `clipped`, `exhausted`, `blocked`, `failed` festliegt
- Nicht-Ziele Runtime-, Graph-, Dashboard- und DB-Arbeit aus dem Slice heraushalten
- Bob einen kleinen, konkreten Validierungs-Handoff fuer sein QueryBudget-Modell bekommt
