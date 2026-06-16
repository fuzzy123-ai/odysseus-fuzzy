# Backend Boundary User Contract

Stand: 2026-06-16

Status: **AS6A Nutzer-/API-/Produktvertrag fuer `0.11.x Backend Canonical Boundaries`**

Quellen:

- `docs/plans/unified-odysseus-roadmap.md`
- `docs/plans/agent-state-isolation-contract.md`
- `docs/plans/context-capsules-contract.md`
- `docs/plans/tool-result-truth-contract.md`
- `docs/plans/dynamic-tool-loading-contract.md`
- `docs/plans/workspace-sandbox-v2-contract.md`

Dieser Vertrag definiert, was aus Nutzer-, Operator- und Produkt-Sicht stabil bleiben muss, auch wenn Odysseus intern spaeter `src/`, `services`, Routes und Plugins neu ordnet. `AS6A` ist bewusst noch kein Refactor- oder Migrationsslice. Es friert nur die Aussenperspektive und die Inventurfragen fuer die spaetere sequenzielle Boundary-Arbeit ein.

## Ziel

Odysseus soll nach aussen verstaendlich und stabil bleiben, auch wenn intern mehrere Backend-Schichten historisch gewachsen sind oder sich spaeter verschieben.

Der Vertrag soll sicherstellen:

- oeffentliche APIs und sichtbares Verhalten brechen nicht still
- Nutzer und Operatoren muessen nicht wissen, welche interne Datei gerade "die wahre" ist
- legacy Pfade bleiben erklaert, statt unsichtbar weiterzuleben
- Deprecation, Error-Sprache und Evidence bleiben konsistent

## Begriffe

### `public API`

Die vertraglich relevante Aussenflaeche, die Nutzer, Operatoren, Plugins oder dokumentierte Clients verwenden duerfen.

- Beispiele: dokumentierte HTTP-Routes, stabile Plugin-Vertraege, oeffentliche Host-Schnittstellen
- Regel: `public API` darf nicht still gebrochen werden

### `internal service`

Eine interne Implementierungsschicht, die fachliche Logik traegt, aber nicht direkt als oeffentliche Produktoberflaeche verkauft wird.

- Beispiele: Helfermodule, interne Serviceklassen, interne Adapter
- Regel: interne Dienste duerfen sich aendern, solange das sichtbare Verhalten stabil bleibt oder sauber migriert wird

### `route layer`

Die Schicht, die Requests oder externe Aufrufe annimmt und in interne Dienste oder Plugins weiterleitet.

- Zweck: Aussenverhalten und interne Logik voneinander trennen
- Regel: Route Layer ist kein Freibrief fuer doppelte Fachlogik

### `plugin boundary`

Die Grenze zwischen Host-Odysseus und einzelnen Plugins oder Subsystemen.

- Zweck: Plugin-spezifisches Verhalten kapseln, ohne Host-Vertraege unklar zu machen
- Regel: Plugin-Grenzen muessen dokumentiert sein, auch wenn intern Services geteilt werden

### `legacy service`

Ein weiterhin existierender interner Pfad, der aus Kompatibilitaets-, Migrations- oder Risiko-Gruenden noch nicht entfernt wurde.

- Regel: `legacy` heisst nicht "unwichtig", aber auch nicht "neuer kanonischer Zielpfad"
- Legacy muss sichtbar markiert werden, nicht zufaellig mitkanonisiert

### `canonical module`

Der spaeter bevorzugte, dokumentierte Zielpfad fuer ein fachliches Backend-Thema.

- Regel: Kanonisch ist eine Produkt- und Architekturentscheidung, nicht nur "die neueste Datei"
- Ein kanonischer Pfad muss nach aussen erklaerbar sein

## Nutzer- und Operator-Sicht: was muss stabil bleiben?

Auch wenn intern umgebaut wird, muessen diese Dinge stabil oder bewusst migriert bleiben:

- dokumentierte API-Endpunkte und deren Kernverhalten
- erklaerte Plugin-Grenzen
- Error-Sprache fuer bekannte Failure-Faelle
- Evidence- und Readiness-Sprache
- Handoff-/Runbook-Verweise fuer Operatoren und Reviewer
- Deprecation-Hinweise, wenn sich ein Pfad aendert

Nutzer und Operatoren sollen nicht mit internen Pfadnamen raten muessen:

- ob jetzt `src/` oder `services/` "wichtiger" ist
- welche Datei die echte Wahrheit fuer ein Verhalten traegt
- ob ein API-Unterschied Absicht, Drift oder Bug ist

## Regeln fuer Verhalten

### Keine stillen API-Brueche

- dokumentierte oder vertraglich genutzte APIs duerfen nicht ohne sichtbaren Handoff, Deprecation oder Migrationspfad brechen
- "intern war es einfacher" ist keine ausreichende Rechtfertigung fuer stille Aussenbrueche

### Deprecation statt harter Schnitt

- wenn ein Pfad spaeter ersetzt werden soll, muss zuerst erklaert werden:
  - was legacy ist
  - was canonical wird
  - wie lange oder unter welchen Bedingungen die Altspur noch lebt

### Error-Sprache bleibt konsistent

- Nutzerfehler, Scope-Fehler, Permission-Probleme und Backend-Fehler brauchen konsistente Oberflaechen
- interne Modulverschiebungen duerfen Error-Texte nicht willkuerlich neu zerfasern

### Evidence-Sprache bleibt konsistent

- Readiness-, Quality-, Handoff- und Truth-Layer-Sprache muss ueber Boundary-Aenderungen hinweg wiedererkennbar bleiben
- Ein Boundary-Refactor darf Evidence nicht unlesbar machen

### Plugin-Grenzen bleiben erklaerbar

- Wenn Fachlogik hostnah und pluginnah verteilt ist, muss trotzdem klar sein:
  - was der Host garantiert
  - was das Plugin garantiert
  - wo die oeffentliche Verantwortung liegt

## UX-/Doku-Sicht: wie erklaeren wir canonical vs legacy?

Spaeter sollen Nutzer, Operatoren und Reviewer die Boundary-Lage mit einfacher Sprache verstehen koennen.

### Canonical erklaeren

- "Das ist der bevorzugte dokumentierte Pfad fuer dieses Verhalten."
- Der kanonische Pfad ist der Referenzpunkt fuer neue Arbeit, neue Tests und neue Erklaertexte.

### Legacy erklaeren

- "Dieser Pfad existiert noch aus Kompatibilitaets- oder Uebergangsgruenden."
- Legacy wird nicht als unsichtbare Schattenwahrheit behandelt.

### Keine Dateinamen als Produktsprache

- Nutzertexte und Runbooks sollen nicht voraussetzen, dass Menschen interne Modulnamen auswendig kennen
- Dateinamen koennen in Doku oder Audit vorkommen, aber nicht als alleinige Produkt-Erklaerung

### Boundary-Notizen statt Refactor-Mythos

- Wenn etwas doppelt, ueberlappt oder alt wirkt, soll die Doku den Zustand als Boundary-Thema benennen
- Nicht jede Ueberlappung braucht sofort einen Umbau im selben Slice

## Nicht-Ziele in diesem Slice

Dieser Vertrag fuehrt bewusst noch nicht aus:

- kein Refactor
- keine Datei-Verschiebung
- keine Migration
- kein Import-Umbau
- keine Route- oder Service-Loeschung
- keine Testumschichtung

`AS6A` ist reine Plan- und Vertragsarbeit, damit `AS6` spaeter sequenziell und bewusst statt ad hoc umgesetzt wird.

## Handoff an Bob: Inventurfragen fuer `AS6B`

Das spaetere Backend-Boundary-Inventar soll mindestens diese Fragen beantworten:

1. Welche dokumentierten `public API`-Pflaechen existieren heute?
2. Welche internen Services oder Module tragen dafuer faktisch Fachlogik?
3. Wo liegen offensichtliche Ueberlappungen zwischen `src/`, `services`, Route Layer und Plugin-Grenzen?
4. Welche Pfade wirken heute schon canonical, welche nur legacy, und wo ist das noch unklar?
5. Welche Fehler-, Status- oder Evidence-Texte haengen an mehreren konkurrierenden Pfaden?
6. Welche Clients oder Plugins wuerden von einem stillen Umbau betroffen?
7. Welche Stellen waeren reine Umbenennung und welche tragen echtes Verhaltensrisiko?

Empfohlene minimale Inventurfelder:

- `boundary_area`
- `public_api_surface`
- `internal_modules`
- `route_layer_refs`
- `plugin_boundary_refs`
- `legacy_candidates`
- `canonical_candidates`
- `risk_notes`

Minimum-Regeln fuer das Inventar:

- Es muss verhaltenorientiert sein, nicht nur dateilistenorientiert.
- Es darf offene Unklarheit markieren statt vorschnell eine kanonische Wahrheit zu behaupten.
- Es muss Risiko fuer Nutzer-/Operator-Verhalten explizit benennen.

## Handoff an Charlie: Punkte fuer spaeteren sequenziellen `AS6C`-Boundary-Plan

Charlie soll spaeter aus `AS6A` plus Bobs Inventar mindestens diese Planfragen zusammenfuehren:

1. Welche Boundary-Themen brauchen zuerst nur Dokumentation oder Deprecation-Notizen?
2. Welche Boundary-Themen brauchen spaeter echte Refactor-Slices?
3. Welche oeffentlichen APIs muessen vor jedem Umbau durch Regressionstests oder Quality Gates geschuetzt werden?
4. Welche Legacy-Pfade duerfen zunaechst leben, weil Risiko oder Kosten eines Sofortumbaus zu hoch sind?
5. Welche Reihenfolge vermeidet Big-Bang-Umbauten zwischen `src/`, `services`, routes und plugins?
6. Wo sind Glue-Slices fuer Charlie sinnvoll und wo muessen Alice oder Bob exklusiv ownen?

## Regeln fuer spaetere Boundary-Arbeit

Wenn aus `AS6A` spaeter echte Folgearbeit entsteht, soll sie:

- sequenziell statt parallel in denselben Kernbereichen laufen
- oeffentliche Behaviour-Contracts vor interner Schoenheit priorisieren
- legacy gegen canonical explizit markieren
- mit Deprecation- und Evidence-Sprache arbeiten
- nicht heimlich als "kleine Cleanup-Arbeit" in andere Slices eingeschmuggelt werden

## Risiken, die `AS6` explizit adressiert

### API-Drift

Intern verschiebt sich Logik, aber nach aussen wird nicht sauber erklaert, welcher Vertrag noch gilt.

### Shadow Canonical

Es gibt faktisch einen bevorzugten Pfad, aber niemand dokumentiert ihn; neue Arbeit verteilt sich weiter zufaellig.

### Legacy-Verdeckung

Alte Pfade bleiben aktiv, ohne als legacy markiert zu sein, und erzeugen still doppelte Wahrheiten.

### Error-Sprache zerfasert

Mehrere konkurrierende Pfade liefern unterschiedliche Fehler- oder Statussprachen fuer aehnliches Verhalten.

### Big-Bang-Refactor-Risiko

Ein zu grosser Boundary-Umbau wuerde gleichzeitig APIs, Imports, Tests und Plugins treffen und dadurch die Foundation destabilisieren.

## Akzeptanz fuer diesen Vertrag

`AS6A-backend-boundary-user-contract` ist erfuellt, wenn:

- Nutzer-, Operator- und Produkt-Sicht auf stabile Backend-Grenzen klar erklaert ist
- die Begriffe `public API`, `internal service`, `route layer`, `plugin boundary`, `legacy service`, `canonical module` definiert sind
- Regeln fuer Verhalten, Deprecation und konsistente Error-/Evidence-Sprache festliegen
- canonical vs legacy spaeter dokumentierbar erklaert werden kann
- Bob klare Inventurfragen fuer `AS6B` bekommt
- Charlie klare Sammelpunkte fuer einen spaeteren sequenziellen `AS6C`-Plan bekommt
- Nicht-Ziele verhindern, dass `AS6A` bereits zum Refactor-Slice wird

## Handoff an Bob

Bitte das erste Backend-Boundary-Inventar klein und beobachtend halten:

- beantworte zuerst Boundary-Fragen statt schon Dateien umzubauen
- markiere Unklarheit offen, statt vorschnell canonical festzuschreiben
- erfasse Nutzer- und API-Risiko mit jedem Boundary-Kandidaten
- fuehre noch keine Import- oder Pfadmigration durch

## Handoff an Charlie

Bitte `AS6` weiter strikt sequenziell schneiden:

- zuerst Vertrag
- dann Inventar
- erst danach ein eigener Boundary-Plan

Kein paralleler Refactor in Kern-Backend-Dateien, solange die Boundary-Lage nicht zuerst sauber kartiert und priorisiert ist.
