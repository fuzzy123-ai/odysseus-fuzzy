# ABC Zero-Sidebar Frontpage Plan

Status: planning baseline for the first coded mockup.

## Zielbild

ABC soll als ruhiger, futuristischer AI-Desktop funktionieren: keine Sidebar,
kein sichtbares Odysseus-Branding, keine klassische Landingpage. Der erste
Eindruck ist eine leere, kontrollierte Arbeitsflaeche mit blauem animiertem
Hintergrund, einem grossen Core-Chatfenster und kontextuellen Werkzeugen, die
erst bei Bedarf erscheinen.

Das Produktgefuehl: minimalistisches Sci-Fi-Cockpit, aber operator-grade und
lesbar. Animationen zeigen Zustand, Uebergang oder Aktivitaet, nicht Dekoration.

## Leitpfeiler: User First

ABC muss fuer normale Nutzer verstaendlich sein, nicht nur fuer Entwickler.
Technische Tiefe darf sichtbar sein, aber die primaere Sprache der UI muss
praezise, kurz und handlungsnah bleiben.

Regeln:

- Keine Haeufung von Fachbegriffen, wenn ein normales Verb oder eine klare
  Alltagsbezeichnung reicht.
- Labels beschreiben, was etwas fuer den Nutzer tut, nicht welche technische
  Implementierung dahintersteht.
- Komplexe Funktionen werden in nachvollziehbare Schritte und progressive
  Ebenen zerlegt.
- Die UI fuehrt den Nutzer durch die Arbeitslogik: erst Orientierung, dann
  Entscheidung, dann Handlung, dann Rueckmeldung.
- Systemzustaende werden klar erklaert: arbeitet, wartet, braucht Eingabe,
  hat Fehler, ist fertig.
- Fachbegriffe sind nur erlaubt, wenn die Zielgruppe sie wirklich erwartet
  oder wenn sie durch Kontext sofort verstaendlich werden.
- Typografie folgt dem gleichen Prinzip: technische Mono-Schrift fuer Titel,
  Status, Metriken, Terminal und Working-Signale; gut lesbare Leseschrift fuer
  Chatnachrichten, Erklaertexte und Fensterinhalte.

Beispielrichtung:

- Statt `RAG`: `Wissen durchsuchen`
- Statt `Provider`: `Quelle`
- Statt `Context Window`: `verfuegbarer Kontext`
- Statt `Agent Orchestration`: `Projektplanung`
- Statt `Vector Database`: `Wissensspeicher`
- Statt `Embedding Sync`: `Wissen aktualisieren`

Dieser Pfeiler ist verbindlich fuer Navigation, Toolwheel, Modellwahl,
Fenstertitel, Tooltips, Fehlermeldungen und leere Zustaende.

## Layout-Prinzipien

- Keine Sidebar.
- Die Mitte gehoert dem Core-Window: Chatverlauf, Session-Metadaten, Composer.
- Header, Footer, Chat und Toolwheel uebernehmen die ehemaligen Sidebar-Aufgaben.
- Alle zusaetzlichen Werkzeuge erscheinen als Floating Windows.
- Fenster sind frei verschiebbar, skalierbar und koennen per Snap-Assist
  angeordnet werden.
- Das Core-Window fuellt standardmaessig fast die gesamte Y-Achse und etwa 60%
  der Bildschirmbreite. Es bleibt wie jedes andere Fenster beweglich,
  skalierbar und separat maximierbar.

## Header

Der Header ist eine Session-Metadatenleiste, keine Navigation.

- Oben mittig steht der aktuelle Chat-Titel.
- Doppelklick auf den Titel oeffnet ein Rename-Feld an gleicher Stelle.
- Rechts neben dem Titel steht das aktuell verwendete Modell.
- Hover auf das Modell zeigt einen Sci-Fi-Tooltip mit:
  - Modellname
  - Lokal/API
  - Tokenverbrauch
  - Kontextgroesse
  - verfuegbarer Kontext in Prozent
  - Last/Status
  - ggf. Kosten- oder Privacy-Hinweis

## Neuer Chat und Modellwahl

In einem neuen Chat ohne Nachrichten sitzt die Modellauswahl nicht im Header.
Sie haengt stattdessen als zentrierte Tabkarte direkt ueber dem Composer.

- Klick auf die Tabkarte oeffnet ein Dropdown mit verfuegbaren Modellen.
- Rechts neben jedem Modell zeigen Statuspunkte den Zustand:
  - Gruen: verfuegbar und gesund
  - Gelb: verfuegbar, aber ausgelastet oder eingeschraenkt
  - Rot: Problem, offline, API-Fehler oder nicht erreichbar
- Sobald die erste Eingabe abgeschickt wurde, wandert das Modell als kompakter
  Status in den Header.

## Footer und History

Der Footer ist das Chat-Eingabefenster.

- Der Composer sitzt unten im Core-Window.
- Ein Pfeil-nach-unten-Icon dient als History-Schalter.
- Bei Aktivierung oeffnet sich zuerst ein kleines Icon-Raster mit Tooltips.
- Das Raster enthaelt schnelle Composer-Aktionen wie Verlauf, Datei, Bild,
  Sprache, Notiz, Suche, Schnellstart, Werkzeug und Wichtig.
- Der Verlauf kann aus diesem Raster heraus geoeffnet werden; dann rutscht der
  Composer nach oben und darunter erscheint eine Liste vergangener Chats.
- Auf Mobile soll die History vertikal swipebar sein.

## Chat-Arbeitslinie

Der Chatbereich uebernimmt die starke Timeline-Idee aus dem bisherigen
Odysseus, aber reduzierter und user-first.

- Eine feine vertikale Linie haelt den Chat optisch zusammen.
- Abgeschlossene KI-Arbeitsschritte erscheinen als kleine rote Punkte an der
  Linie.
- Die Punkte zeigen keine langen Labels im Normalzustand.
- Hover oder Fokus auf einen Punkt zeigt einen kompakten Tooltip mit dem
  konkreten Arbeitsschritt und einer kurzen Erklaerung.
- Waehrend die KI aktuell arbeitet, bleibt der sichtbare Working-State mit
  Pixelanimation im Chat erhalten.
- Mehrere aufeinanderfolgende Arbeitsschritte werden als Punktfolge gezeigt,
  nicht als laute Statusliste.

## Toolwheel

Das Toolwheel ist der Ersatz fuer Navigation, Quicktools und grosse Teile der
Sidebar. Es ist nicht als App-Launcher gedacht, sondern als anpassbarer
Sci-Fi-Skilltree.

- Rechtsklick oeffnet das Toolwheel zentriert am Core.
- Wenn das Toolwheel offen ist, schliesst ein weiterer Rechtsklick es wieder.
- Ein leuchtender Richtungspfeil im Core zeigt zur Mausposition, die das
  Toolwheel geoeffnet hat.
- In der Mitte sitzt kein Schliessen-Button. Dort sitzt ein dick umrandeter
  Plus-Core; Klick erstellt einen neuen Chat-Space im gleichen App-Fenster.
- Keine Speichenlinien vom Zentrum zu jedem Tool. Die Struktur entsteht durch
  Ringe, klare Bereiche und Hover-Baeume.
- Der Hintergrund wird beim Oeffnen abgedimmt, damit das Toolwheel als aktive
  Entscheidungsebene klar im Vordergrund steht.
- `Alt+Space` oeffnet/schliesst das Toolwheel per Tastatur.
- Auf Touch: Longpress, ziehen, loslassen zum Bestaetigen.
- Zahlen waehlen Nodes oder Subaktionen.
- `Esc` schliesst oder geht eine Ebene zurueck.
- `Enter` bestaetigt den fokussierten Node.
- Pfeiltasten/Tab bewegen den Fokus.
- Das Toolwheel muss spaeter voll anpassbar sein:
  - Nodes verschieben
  - Kategorien aendern
  - Tools ausblenden
  - Favoriten pinnen
  - Shortcuts setzen
  - Presets importieren/exportieren

Finale Richtung im Mockup:

- Hybrid aus Skilltree und ruhiger Radialkarte.
- Hauptbereiche: Projektplanung, Wissen, Werkzeuge, Fenster, Einstellungen,
  Sicherheit.
- `Neu`/neuer Chat liegt nur im Plus-Core in der Mitte, nicht noch einmal als
  Aussen-Node.
- Hover auf den Plus-Core zeigt die Neu-Varianten wie neuer Chat, neue Aufgabe
  und neuer Arbeitsraum als kleinen cyanfarbenen Baum, der nach unten droppt.
- Kleine Quick-Action-Blasen entfallen.
- Hover auf einen Hauptbereich klappt einen farblich passenden Baum aus. Die
  Subaktionen fuellen sich von oben nach unten, technisch und ruhig animiert.
- Erweiterte Befehle sind vor Hover nicht sichtbar.
- Subaktionen wirken wie kompakte Listen, nicht wie grosse Button-Blasen.
- Die Optionsliste bleibt sichtbar, wenn der Nutzer vom Hauptbutton in die
  ausgeklappte Liste faehrt.
- Listeneintraege haben einen eigenen Hover-State, damit klar ist, welche
  Option gerade angewählt wird.
- Der Richtungspfeil im Zentrum folgt dem Cursor live, solange das Toolwheel
  offen ist.
- Projektplanung ist rot codiert und klappt nach links aus.
- Bezeichnungen bleiben user-first und beschreiben die Handlung, nicht die
  technische Implementierung.

## Kategorien und Farbcodierung

Moegliche Haupttypen:

- Core: Chat, neue Session, Suche, Verlauf
- Projektplanung: Projekt planen, Aufgaben verteilen, Fortschritt pruefen,
  Blocker klaeren
- Wissen: Wissen suchen, Quellen oeffnen, Notizen merken, Wissen erneuern
- Werkzeuge: Kalender, Mail, Dateien, Galerie
- Fenster: Fenster oeffnen, nebeneinander legen, 4er Ansicht, alle ordnen
- Einstellungen: Modell, Ansicht, Shortcuts, Farben
- Sicherheit: Privatmodus, Rechte, Freigaben, Spuren loeschen

Farbcodierung:

- Cyan: Core und neutrale Aktionen
- Blau: Navigation und Workspace-Bewegung
- Teal: Knowledge, Memory, Daten
- Gruen: laufend, Automation, Erfolg
- Amber: Aufmerksamkeit, Review, Pending
- Rot: destruktiv, Permission, Fehler

Farbe muss Bedeutung tragen, nicht nur Stimmung.

## Floating Windows

Alle Nebenfunktionen existieren als Floating Overlays.

Unter dem `ABC`-Logo sitzt auf Desktop eine sehr kleine Uebersicht der offenen
Chats/Fenster als 3D-Karussell. Die Kacheln zeigen nur Nummer und Status-Icon;
arbeitende Chats bekommen eine Pixelanimation. Klick auf eine Kachel springt
direkt zum jeweiligen Chat-Space. Solange die Maus ueber dem Karussell liegt,
rotiert das Scrollrad durch die offenen Spaces. Auf Mobile entfaellt das
Karussell, weil dort per Swipe gewechselt wird.

Beispiele:

- Memory
- Notes
- Calendar
- Research
- Models
- Settings
- Plugins
- Agent-/Task-Fenster
- Tool-Ausgaben

Fensterverhalten:

- Frei beweglich.
- Frei skalierbar an allen Kanten und Ecken.
- Jedes Fenster hat vertraute Fensteraktionen: minimieren, maximieren/
  wiederherstellen und schliessen.
- Vordergrund durch Klick.
- Kein Andocken in die Sidebar.
- Snap-Assist nach Windows-11-Logik:
  - links/rechts halbseitig
  - maximiert
  - 2-Spalten-Layout
  - 4er-Kachel-Layout
  - Vorschlag fuer andere Fenster daneben

## Aktive Chat-Spaces

Mehrere aktive Chats werden als horizontale Workspaces gedacht.

- `Ctrl+Tab`: naechster Chat rechts
- `Ctrl+Shift+Tab`: vorheriger Chat links
- `Ctrl+1` bis `Ctrl+9`: direkten aktiven Chat waehlen
- Links/rechts am Rand zeigen kleine Nodges, ob ein benachbarter Chat existiert.

Nodge-Zustaende:

- Blau: Chat existiert, keine neue Aktivitaet
- Cyan pulsierend: Chat arbeitet gerade
- Heller Punkt/Badge: neue Ausgabe ungelesen
- Amber/Frage: User-Input erforderlich
- Rot: Fehler oder abgebrochener Lauf

Standardannahme: Floating Windows sind chatgebunden. Spaeter kann es ein
`pin to all chats`-Verhalten geben.

## Motion Primitives

Die bestehende Odysseus-DNA aus Pixel-/ASCII-Wellen und Working-Animationen
wird als konsistentes System uebernommen.

- `boot`: Start/leerere Session, kleine blaue Pixelwelle oder initializing grid
- `busy`: KI arbeitet, laufende Pixel-Signatur statt normalem Spinner
- `wave`: subtile ASCII-/Pixelwelle fuer Denk- und Ladezustaende
- `scan`: Kanten-/Drawer-Bewegung, z.B. beim History-Auszug
- `trace`: Toolwheel-Verbindungslinien laden segmentweise wie Skilltree-Pfade
- `pulse`: Modellstatus, aktive Nodes, laufende Jobs
- `error`: rote kurze Stoerung/Signalunterbrechung statt plumper Alert

Hintergrundprinzip:

- Das Grid bleibt ruhig, bekommt aber stellenweise Aktivitaet.
- Kleine leuchtende Punkte bewegen sich entlang der Grid-Linien, wie Elektronen
  auf Leiterbahnen oder Datenpakete in einem Netzwerk.
- Die Punkte haben nur eine kurze Spur und ein leichtes Glimmen.
- Keine grossen Flaechen-Halos und kein starkes Highlighting.
- Der Effekt soll wie ruhige Systemaktivitaet wirken, nicht wie ein
  Partikelscreen.

Reduced-motion muss spaeter fuer alle Animationen beruecksichtigt werden.

## Erstes Mockup

Das erste Mockup soll ein isolierter, interaktiver Screen sein.

Enthalten:

- ABC als kleiner Systemname, kein Odysseus-Branding.
- Animierter blauer Hintergrund.
- Grosses Core-Window mit Beispiel-Chatverlauf.
- Header mit Titel, Rename per Doppelklick und Modellstatus-Tooltip.
- Neuer-Chat-State mit Modell-Tab ueber dem Composer.
- Footer/Composer mit History-Drawer.
- Rechtsklick- und `Alt+Space`-Toolwheel am Core.
- Tastaturnavigation im Toolwheel mit Zahlen.
- Toggle zwischen Constellation- und Skilltree-Toolwheel.
- Mehrere Floating Windows.
- Drag, Resize und Snap-Assist als Prototyp.
- Drei aktive Chat-Spaces mit Ctrl-Tab, Ctrl-Shift-Tab und Ctrl+Zahl.
- Rand-Nodges mit Statussignalen.
- Pixel-/Motion-Primitives sichtbar eingesetzt.

Nicht Ziel des ersten Mockups:

- Vollstaendige Datenintegration.
- Echte Modellabfragen.
- Finale Tool-Konfiguration.
- Produktive Migration der vorhandenen Frontseite.
