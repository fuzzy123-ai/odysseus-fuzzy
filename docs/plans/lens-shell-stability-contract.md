# Lens Shell Stability Contract

Stand: 2026-06-16

Status: **LENS1A UX-/Verhaltensvertrag fuer `0.15.x Lens Shell Stability`**

Quellen:

- `docs/plans/unified-odysseus-roadmap.md`
- `docs/plans/odysseus-lens-ui-memory-interaction.md`
- `docs/plans/lens-ui-ux-contract.md`

Dieser Vertrag definiert die Shell-Stabilitaet der Odysseus Lens, bevor `LENS1B` die Frontend-Hotfiles anfasst. Der Slice fuehrt bewusst keine UI-, Backend-, Runtime- oder Test-Aenderungen aus. Er friert nur die erwarteten Shell-Zustaende, Uebergaenge, Reflow-Regeln, Close-Pfade und Akzeptanzkriterien ein, damit Bob anschliessend fokussiert implementieren und Charlie sequenziell pruefen kann.

## Ziel

Die Lens-Shell soll sich verlässlich, vorhersagbar und zustandsklar verhalten:

- Fullscreen, Sidebar, Overlay und geschlossener Zustand duerfen nicht gegeneinander driften.
- Minimize, Restore, Close und New Chat muessen eine eindeutige Wirkung haben.
- Overlay, Backdrop, Pointer-Events und Escape duerfen keine Zombie-Surfaces hinterlassen.
- Graph-Reflow muss nach Surface-Wechseln robust bleiben.
- stale Body-Klassen, falsche Z-Index-Lagen und kaputte Resize-Zustaende muessen ausgeschlossen werden.

## Leitregel

Es gibt genau eine aktive Shell-Wahrheit pro Zeitpunkt.

Das bedeutet:

- Surface-Mode und Panel-Status duerfen sich nicht widersprechen.
- Schliessen bedeutet wirklich geschlossen, nicht nur visuell versteckt.
- Minimieren bedeutet erreichbar und nicht blockierend, nicht halb geschlossen.
- Reopen oder Restore muessen in einen gueltigen, vollstaendigen UI-Zustand zurueckkehren.

## Terminologie

### Surface Modes

Die Lens kennt vier Shell-Hauptzustaende:

- `sidebar`
- `overlay`
- `fullscreen`
- `closed`

Diese Zustaende sind gegenseitig exklusiv.

### Panel Actions

Die Shell kennt mindestens diese Nutzeraktionen:

- `open`
- `close`
- `minimize`
- `restore`
- `toggle`

### Shell Hotspots

Die nachgelagerte Implementierung wird sich voraussichtlich auf diese Hotspots stuetzen:

- `applyObsidianSurfaceMode`
- `initializeClosedObsidianSurface`
- `changeObsidianSurfaceMode`
- `togglePanel`
- `openPanel`
- `closePanel`
- `minimizePanel`
- Resizer
- Settings-Menue
- Graph-Renderer und Reflow
- Shell- und Surface-CSS-Klassen

`LENS1A` beschreibt das erwartete Verhalten fuer diese Bereiche, ohne ihre technische Loesung festzulegen.

## Surface-Zustaende

### Sidebar

`sidebar` bedeutet:

- Lens ist angedockt und sichtbar
- Dokument bleibt weiterhin erkennbarer Host-Kontext
- kein Backdrop blockiert die restliche App
- Pointer-Events ausserhalb der Lens bleiben normal

### Overlay

`overlay` bedeutet:

- Lens liegt als bewusst geoeffnete Flaeche ueber dem Dokument
- ein Backdrop oder Fokuskontext kann aktiv sein
- Pointer-Events hinter dem Overlay sind nur dann blockiert, wenn der Overlay-Zustand dies explizit braucht

### Fullscreen

`fullscreen` bedeutet:

- Lens ist dominante Arbeitsflaeche
- Layout und Reflow sind fuer den Vollzustand stabil
- keine Rest-Offsets, veralteten Sidebar-Breiten oder Overlay-Schatten bleiben aktiv

### Closed

`closed` bedeutet:

- Lens ist nicht sichtbar
- keine unsichtbare Interaktionsflaeche blockiert Klicks
- keine stale Body-Klasse behauptet weiter Overlay, Fullscreen oder Sidebar
- Graph, Dokument und restliche Shell laufen ohne Lens-Blockade weiter

## Kernverhalten

### Fullscreen

Beim Wechsel in `fullscreen` gilt:

- Fullscreen ersetzt den vorherigen sichtbaren Shell-Modus
- aktive Overlay-/Sidebar-Reste werden sauber aufgeloest
- Graph und Inhalt bekommen nach dem Wechsel einen stabilen Reflow
- Resize-Handles oder Sidebar-spezifische UI duerfen nicht halb aktiv bleiben

### Minimize

`minimize` bedeutet:

- Lens bleibt logisch vorhanden
- Lens blockiert den Dokumentfluss nicht
- Nutzer kann spaeter `restore` ausfuehren
- minimierter Zustand darf nicht wie `closed` oder `overlay` gleichzeitig wirken

### Restore

`restore` bedeutet:

- die Lens kehrt in den letzten gueltigen sichtbaren Modus zurueck
- dabei werden stale Klassen und falsche Z-Index-Zustaende bereinigt
- Graph oder Panels reflowen erneut, falls vorher minimiert oder versteckt wurde

### Close

`close` bedeutet:

- sichtbare Surface ist vollstaendig geschlossen
- Backdrop verschwindet
- Pointer-Blockaden verschwinden
- stale Resize- oder Settings-Zustaende verschwinden
- es bleibt kein halboffener Overlay- oder Minimize-Rest

## Zustandswechsel

### Wechsel zwischen Sidebar, Overlay, Fullscreen und Closed

Jeder Wechsel muss atomar wirken:

- ein alter Zustand wird verlassen
- der neue Zustand wird voll gesetzt
- veraltete Klassen und Nebenwirkungen werden aufgeraeumt

Verboten sind Zwischenlagen wie:

- `fullscreen` visuell aktiv, aber `overlay`-Backdrop bleibt
- `closed`, aber Pointer-Events bleiben blockiert
- `sidebar`, aber Fullscreen-Body-Klasse ist noch gesetzt

## New Chat

`New Chat` darf die Lens nicht in einen widerspruechlichen Zustand zwingen.

Produktregel:

- New Chat soll die Lens minimieren oder nicht blockierend im Hintergrund lassen
- New Chat soll die Lens nicht hart schliessen, wenn dadurch laufender Kontext oder Shell-Status verloren wirkt
- New Chat darf nicht dazu fuehren, dass eine unsichtbare Lens weiterhin Fokus oder Pointer-Events abfaengt

Erwartetes Verhalten:

- Dokument oder neuer Chat wird benutzbar
- Lens bleibt spaeter sauber wiederherstellbar

## Overlay, Backdrop, Pointer Events und Escape

### Overlay und Backdrop

Im Overlay-Zustand gilt:

- Backdrop ist nur aktiv, wenn wirklich Overlay-Fokus gilt
- Backdrop verschwindet beim Close sauber
- Backdrop duerfte nicht in Sidebar- oder Closed-Zustaenden haengen bleiben

### Pointer Events

Pointer-Events muessen zum Surface-Zustand passen:

- `closed`: nichts blockiert
- `sidebar`: App bleibt normal klickbar
- `overlay`: nur der beabsichtigte Fokusbereich blockiert
- `fullscreen`: Lens ist dominant, aber es gibt keine unsichtbaren Blockflaechen ausserhalb des Modells

### Escape

Escape braucht eine klare Prioritaet:

1. offenes untergeordnetes Menue oder transienter Zustand
2. Overlay-nahe Surface schliessen oder verlassen
3. keine globale chaotische Mehrfachreaktion

Escape darf nicht:

- gleichzeitig Surface und Unterdialog unklar behandeln
- Lens schliessen, waehrend Menues offen haengen bleiben
- stale Overlay-Zustaende hinterlassen

## Close-Verhalten fuer Teiloberflaechen

### Audit / MemoryTree / Spark / Insights / Diagnostics

Diese Oberflaechen oder Modi brauchen ein konsistentes Close-Verhalten.

Regel:

- Close beendet den aktuellen Unterzustand
- Shell bleibt in einem gueltigen uebergeordneten Zustand
- der Nutzer landet an einem nachvollziehbaren Ort zurueck

Das bedeutet:

- Audit/Diagnostics-Close darf nicht das gesamte Shell-Layout zerreissen
- MemoryTree-Close darf keine stale Navigation hinterlassen
- Spark/Insights-Close darf keine Sonderklassen aktiv lassen
- Teiloberflaechen duerfen Graph-Reflow oder Dokumentbreite nicht kaputt hinterlassen

## Graph-Reflow

Graph-Reflow ist nach diesen Ereignissen verpflichtend:

- Wechsel des Surface-Modes
- Resize
- Close -> Reopen
- Minimize -> Restore
- Fullscreen -> Sidebar oder Overlay

Produktregel:

- der Graph soll nach Shell-Wechseln sichtbar stabil und korrekt eingepasst sein
- kein abgeschnittener, leerer oder falsch skalierter Graph-Zustand darf still akzeptiert werden

## Body-Klassen, Z-Index, Resize und Settings

### Stale Body-Klassen

Body- oder Root-Klassen fuer Shell-Zustaende muessen synchron sein.

Verboten sind:

- stale Fullscreen-Klassen nach Close
- stale Overlay-Klassen nach Sidebar
- stale Minimize-Marker nach Restore

### Z-Index

Z-Index-Regeln muessen klar priorisieren:

- Lens
- Overlay/Backdrop
- Settings-Menue
- untergeordnete Menues oder Popovers

Keine Ebene darf unbeabsichtigt hinter dem falschen Layer verschwinden oder unbedienbar werden.

### Resize-Handles

Resize-Handles sind nur dann aktiv, wenn der aktuelle Surface-Mode sie sinnvoll nutzt.

Sie duerfen nicht:

- im Fullscreen sichtbar stoeren
- nach Close Pointer-Events abfangen
- nach Mode-Wechsel inkonsistent weiterlaufen

### Settings-Menue

Das Settings-Menue muss sich shell-konform verhalten:

- es oeffnet auf der aktiven Surface
- es schliesst sauber bei Close, Escape oder Mode-Wechsel, wenn sein Kontext verschwindet
- es bleibt nicht als losgeloestes Overlay im falschen Z-Index haengen

## Mobile / Hamburger-right

Auf mobilen oder hamburger-rechten Layouts gilt:

- die Lens bleibt erreichbar, ohne den Host-Kontext unvorhersagbar zu zerstoeren
- Overlay und Close muessen touch-tauglich sein
- Klickziele bleiben mindestens 44px
- Escape-Logik wird dort als Close- oder Back-Verhalten nachvollziehbar gespiegelt
- Minimize oder Restore duerfen nicht von Desktop-Annahmen ueberlagert werden

## Component States

Die Shell und ihre Schalter muessen mindestens diese Zustaende sauber tragen:

- `default`
- `hover`
- `active`
- `focus`
- `disabled`
- `loading`
- `error`
- `empty`

Mindestens betroffen:

- Surface-Mode-Schalter
- Open/Close/Minimize/Restore-Controls
- Settings-Menue-Ausloeser
- Resize-bezogene Interaktionsgriffe
- Diagnostics-, Insights- oder Audit-Einstiege

## Akzeptanzkriterien

`LENS1A` ist nur dann sauber erfuellt, wenn daraus eine konkrete `LENS1B-shell-stability-fix`-Arbeit ableitbar ist.

Mindestens klar sein muss:

- welche Surface-Modes existieren
- wie Fullscreen, Minimize, Restore und Close wirken
- wie New Chat mit der Lens interagiert
- wie Overlay, Backdrop, Pointer-Events und Escape priorisiert werden
- wie Audit, MemoryTree, Spark, Insights und Diagnostics geschlossen werden
- wann Graph-Reflow verpflichtend ist
- dass stale Body-Klassen, Z-Index-Fehler, Resize-Reste und Settings-Haenger als Bugs gelten
- welche mobilen Erwartungen gelten

## Klarer Bob-Scope fuer `LENS1B-shell-stability-fix`

Bob darf in `LENS1B` fokussiert an diesen Themen arbeiten:

- `applyObsidianSurfaceMode`
- `initializeClosedObsidianSurface`
- `changeObsidianSurfaceMode`
- `togglePanel`
- `openPanel`
- `closePanel`
- `minimizePanel`
- Resizer-Verhalten
- Settings-Menue-Verhalten
- Graph-Renderer/Reflow
- zugehoerige Shell-/Surface-CSS-Klassen

Bob soll in `LENS1B` nicht:

- `LENS2` vorwegnehmen
- neue Informationsarchitektur erfinden
- neue GraphRAG-/RAPTOR-Signale einfuehren
- die Lens hart umbenennen

## Fokussierte Tests fuer Bob und Charlie

Bob soll spaeter fokussiert pruefen oder ergaenzen:

- Static/UI-Tests fuer Surface-Mode-Klassen
- Tests fuer Open/Close/Minimize/Restore-Transitions
- Tests gegen stale Body-Klassen
- Tests fuer New-Chat-Minimize statt hartem Blockadezustand
- Tests fuer Settings-Menue-Close bei Surface-Wechsel
- Tests fuer Overlay-/Backdrop-/Pointer-Event-Rueckbau
- Tests fuer Graph-Reflow nach Resize, Close/Reopen und Mode-Wechsel
- Mobile-nahe Smoke-Checks fuer Hamburger-right und Touch-Zielgroessen

Charlie soll spaeter besonders verifizieren:

- kein Hotfile-Overlap
- Regression gegen bestehende Obsidian-Smokes
- keine Zombie-Overlay- oder Pointer-Blocker
- Graph bleibt nach Shell-Wechseln stabil

## Nicht-Ziele

`LENS1A` fuehrt bewusst nicht aus:

- keinen UI-Code
- keine Tests
- keine Backend-Aenderungen
- keine Runtime-Aenderungen
- keinen Start von `LENS2`

Der Vertrag beschreibt nur das erwartete Verhalten der Shell als Grundlage fuer `LENS1B`.
