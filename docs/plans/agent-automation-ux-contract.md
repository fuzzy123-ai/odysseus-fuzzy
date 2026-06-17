# Agent Automation UX Contract

Stand: 2026-06-17

Status: pre-implementation user and operator contract for agent timer, watch,
clock, and overlay behavior

Dieses Dokument definiert die Nutzer- und Operator-Sprache fuer Agent-Timer,
Watch-Modi, die Timer-Uhr und ein read-only Overlay. Es aktiviert keinen
Scheduler, startet keine Agenten und fuehrt keine Live-Runtime-Aktion aus.

## Zweck

Der Main Agent soll spaeter einfache Nutzerbefehle in sichere
Automation-Spezifikationen vorbereiten koennen.

Vor `1.0` soll ein Nutzer oder Operator verstehen:

- wie ein Agent-Timer beschrieben wird
- wie ein Watch-Modus beschrieben wird
- was die Uhr am Agenten bedeutet
- welche Daten ein Overlay zeigen darf
- wann etwas nur Preview ist
- wann ausdrueckliches Nutzer-Go fuer echte Persistenz oder Live-Aktivierung
  noetig ist

## Nicht-Ziele

Dieser Slice deckt bewusst nicht ab:

- keine echte Scheduler-Aktivierung
- keine ScheduledTask-Persistenz
- keine Thread-Sends
- keine Agentenstarts
- keine Hidden-Worker-Ausfuehrung
- keine Netzwerk-, Provider-, Telegram-, Plugin- oder UI-Hotfile-Arbeit
- keine Behauptung, dass AAF2 oder AAF3 bereits implementiert sind

## Beziehung Zu Bestehenden Fundamenten

Die Agent-Automation-Sprache baut auf bestehenden Fundamenten auf:

- `AgentProfile` beschreibt `timer_policy`
- die Team-Card-Surface darf einen Timer-Hinweis tragen
- Scheduler-Fundamente existieren technisch bereits, werden in diesem Slice
  aber nicht angesprochen oder aktiviert

Wichtig:

- Eine Automation-Spec ist nicht automatisch ein aktiver Scheduler-Job.
- Die Uhr ist zunaechst Sichtbarkeits- und Preview-Sprache.
- Safety Rules und Parent-Beziehungen bleiben hoeherwertig als Automation-Wuensche.

## Einfache Nutzerbefehle

Die Nutzerinteraktion soll einfache Sprache bevorzugen und keine `/agent`-Pflicht
verlangen.

Erwuenschte Befehlsformen:

- `Charlie, watch alle 30 Minuten`
- `Alice, erinnere mich morgen`
- `Bob, pruef das taeglich um 9`
- `Charlie, wiederhole das jeden Montag`
- `Alice, mach das am 25. Juni um 14 Uhr`
- `Bob, stell das fuer den naechsten Intervall auf 2 Stunden`

Diese Sprache beschreibt zunaechst:

- was der Nutzer will
- welchen Agenten es betrifft
- welche Automation-Spezifikation daraus vorbereitet werden soll

Sie beschreibt nicht automatisch:

- dass schon ein Scheduler-Job gespeichert wurde
- dass bereits Threads gesendet werden
- dass Live-Automation schon aktiv ist

## Automation-Modi

Die UX-Sprache soll mindestens diese Modi unterscheiden:

- `manual`
- `watch`
- `interval`
- `once`
- `recurring`

### `manual`

Der Agent ist manuell. Es gibt keine aktive Automation, aber Profil- oder
Overlay-Sprache kann trotzdem sichtbar sein.

### `watch`

Ein Watch beschreibt einen beobachtenden Modus ohne harten Timer-Dispatch in
diesem Track.

Der Watch-Modus bedeutet:

- Agent oder Main Agent haelt einen Bedarf oder eine Beobachtung im Blick
- spaetere Meldung an Parent ist konzeptionell erlaubt
- es gibt in diesem Slice keine implizite Live-Ausfuehrung

Nicht sagen:

- "watch means polling is running"
- "watch means scheduler is active"

### `interval`

Ein Interval beschreibt eine wiederholte Ausfuehrung in Minuten, Stunden oder
Tagen, aber vorerst nur als Spezifikation oder Preview.

### `once`

Ein einmaliger Lauf zu festem Datum oder Datum/Uhrzeit.

### `recurring`

Eine wiederkehrende Regel wie taeglich, woechentlich oder monatlich. Auch diese
bleibt in diesem Slice eine Spezifikation oder Preview.

## Timer-Uhr

Die Timer-Uhr ist ein Sichtbarkeits- und Kontrollsignal am Agenten.

Die Uhr darf bedeuten:

- dieser Agent hat einen Automation-Hinweis
- dieser Agent hat eine vorbereitete Spezifikation
- dieser Agent hat einen Watch-, Timer- oder geplanten Modus

Die Uhr darf nicht bedeuten:

- Live-Runtime ist sicher aktiv
- Scheduler wurde schon geschrieben
- Thread-Sends wurden bereits freigegeben

Operator-Sprache:

- "clock indicates automation spec or watch hint"
- "clock is not proof of live scheduling"

## Overlay-Felder

Das Overlay zur Uhr soll spaeter mindestens diese Felder zeigen:

- Modus
- Intervall
- Einheit Minuten / Stunden / Tage
- fixes Datum oder wiederkehrende Regel
- naechster Lauf
- Zeitzone
- Status
- Parent-Agent
- letzter Handoff
- Aenderung fuer den naechsten Intervall

### Modus

Zeigt, ob die Spezifikation `manual`, `watch`, `interval`, `once` oder
`recurring` ist.

### Intervall

Zeigt die numerische Wiederholungsangabe, wenn ein Intervall vorliegt.

### Einheit

Zulaessige Einheiten:

- Minuten
- Stunden
- Tage

### fixes Datum oder wiederkehrende Regel

Das Overlay darf ein konkretes Datum oder eine wiederkehrende Regel anzeigen,
ohne zu behaupten, dass diese schon live persistiert wurde.

### naechster Lauf

Zeigt den geplanten oder vorbereiteten naechsten Lauf aus der Spezifikation.

Wichtig:

- "naechster Lauf" darf als Preview oder berechneter Hinweis erscheinen
- es darf nicht automatisch als bestaetigter Live-Scheduler-Zustand verkauft
  werden

### Zeitzone

Zeigt die relevante Zeitzone der Spezifikation oder des Nutzerkontexts, damit
Zeitangaben nicht missverstanden werden.

### Status

Der Status soll kurze, kontrollierte Zustands-Sprache tragen, zum Beispiel:

- `manual`
- `watch_only`
- `spec_ready`
- `needs_user_go`
- `deferred`
- `blocked`

### Parent-Agent

Zeigt, welchem Parent die Spezifikation oder der Watch-Kontext zugeordnet ist.

### letzter Handoff

Zeigt einen kompakten Hinweis auf den letzten relevanten Handoff oder Report.

Nicht erlauben:

- komplette Logs
- vertrauliche Inhalte
- rohe IDs

### Aenderung fuer den naechsten Intervall

Das Overlay soll spaeter erlauben, die Spezifikation fuer den naechsten
Intervall zu aendern.

Wichtig:

- Diese Aenderung ist in diesem Track zunaechst eine Preview- oder
  Vorschlagssprache.
- Sie ist kein automatischer Persistenzschritt.

## Read-Only Preview Boundary

Die Spezifikation oder Preview ist read-only.

Das bedeutet:

- kein Scheduler-Write
- kein Job-Start
- kein Thread-Send
- kein Agentenstart
- kein Hidden-Worker-Dispatch

Operator-Sprache:

- "prepared automation spec"
- "preview only"
- "requires explicit user go for persistence or live activation"

Nicht sagen:

- "saved automation"
- "live timer active"
- "watch already running in runtime"

## Ausdrueckliches Nutzer-Go

Echte Persistenz, Scheduler-Start oder Thread-Send brauchen ausdrueckliches
Nutzer-Go.

Das bedeutet:

- Main Agent darf einen Vorschlag oder eine Preview vorbereiten
- Main Agent darf die Spezifikation erklaeren
- Main Agent darf ohne Go keine Live-Automation behaupten oder ausloesen

Gute Sprache:

- "Ich kann diese Automation als Spezifikation vorbereiten."
- "Fuer echtes Speichern oder Aktivieren brauche ich dein Go."

## Watch Ohne Timer

Ein Watch ohne Timer ist eine legitime eigene Form.

Das bedeutet:

- Der Agent oder Main Agent haelt einen Bedarf im Blick
- Parent/Handoff-Sprache kann vorbereitet werden
- es gibt keine implizite Live-Ausfuehrung
- ein Watch kann sichtbar sein, ohne einen festen Intervall zu haben

Nicht sagen:

- "watch means background loop"
- "watch means polling already enabled"

## Parent-Agent Und Handoff

Jede Automation-Spec oder Watch-Sicht soll ihren Parent-Agenten lesbar machen.

Wichtig:

- Automationskontext bleibt an Parent und Safety gebunden
- Handoff-Sprache soll kompakt bleiben
- letzte Reports duerfen nur als kurze, leak-freie Zusammenfassung erscheinen

## Go / Partial / No-Go Fuer AAF1-AAF3

### Go

Go ist angemessen, wenn:

- AAF1 die Nutzer- und Operator-Sprache fuer Timer, Watch, Uhr und Overlay
  sauber definiert
- AAF2 ein leak-freies, read-only Spec-Modell liefert
- AAF3 hoechstens einen read-only Timer-Hinweis an bestehende Team-Card-Sicht
  anschliesst
- kein Teilbereich Live-Automation ohne Nutzer-Go impliziert

### Partial

Partial ist angemessen, wenn:

- UX-Vertrag und Spec-Modell stehen
- Team-Card-Anschluss oder Preview-Route bewusst deferred sind
- die Read-Only- und Go-Gates bereits klar dokumentiert sind

### No-Go

No-Go ist angemessen, wenn:

- Scheduler, Tasks oder Threads ohne Nutzer-Go gestartet oder behauptet werden
- Overlay-Sprache Live-Runtime suggeriert
- Secrets, Tokens oder Chat-IDs sichtbar werden
- Watch als impliziter aktiver Hintergrundprozess verkauft wird

## Nutzer- Und Operator-Sprache

Bevorzugte kurze Sprache:

- timer spec
- watch hint
- next run preview
- timezone aware
- needs user go
- parent-linked automation
- change for next interval

Zu vermeiden:

- "automation already live"
- "watch is running in background"
- "scheduler saved automatically"
- "thread dispatch is active"

## Rollen

### Alice

Alice definiert die Nutzer- und Operator-Sprache fuer Befehle, Timer-Uhr,
Overlay-Felder, Watch-Bedeutung und Go-Gates.

### Bob

Bob baut spaeter das kleine Spec-Modell und die leak-freie Overlay-Payload,
ohne Scheduler oder Runtime zu starten.

### Charlie

Charlie kontrolliert Scope, Tests, Integration und stoppt jede Verschiebung in
Live-Scheduler, Thread-Sends, Agentenstarts oder Scope-Drift.

## Stop-Regeln

Sofort stoppen, wenn:

- Scheduler, Thread-Send oder Agentenstart fuer diesen Slice noetig werden
- Overlay oder Preview Secrets, Tokens, Chat-IDs oder private Runtime-Daten
  zeigen sollen
- Watch als echte aktive Hintergrundausfuehrung verkauft wird
- Scope in Code-, Route-, UI-, Test-, Plugin-, Telegram- oder Netzwerk-Arbeit
  abgleitet

## Abschluss

Die Agent-Automation-UX soll vor `1.0` eine sichere Spezifikations- und
Preview-Sprache liefern: einfache Nutzerbefehle, Uhr am Agenten, Overlay mit
klaren Feldern, Watch ohne implizite Live-Ausfuehrung und ein hartes Go-Gate
vor jeder echten Persistenz oder Scheduler-Aktivierung.
