# Image Tools Worker UI Cookbook Contract

Stand: 2026-06-16

Status: **ITW5A UX-/Copy-/Cookbook-Vertrag fuer `0.16.x Image Tools Worker UI Alignment`**

Quellen:

- `docs/plans/image-tools-worker-contract.md`

Dieser Vertrag definiert die Nutzertexte, Setup-Erwartungen und Cookbook-/Editor-Kommunikation fuer den spaeteren Image Tools Worker. Der Slice fuehrt bewusst keine UI-, Backend-, Route-, Worker-, Test-, Docker- oder Dependency-Aenderungen aus. Er friert nur Statussprache, Setup-Story, Windows-Formulierung, Primaeraktionen und Akzeptanzkriterien ein, damit `ITW5` spaeter fokussiert umgesetzt werden kann.

## Ziel

Background Removal darf ohne konfigurierten Worker nicht kryptisch scheitern.

Stattdessen gilt:

- Nutzer sehen einen klaren Status
- Nutzer bekommen genau eine naechste Aktion
- Cookbook und Editor erklaeren den Setup-Pfad ohne Core-Debugging
- Windows wird nicht pauschal als unsupported beschrieben

## Leitregel

Der Core bleibt stabil, der Worker ist optional, und die UI muss diesen Unterschied klar erklaeren.

Das bedeutet:

- Fehlertexte beschreiben den Worker-Zustand, nicht interne Tracebacks
- Cookbook und Editor unterscheiden zwischen Core, Worker und Berechtigung
- erfolgreiche Background Removal erzeugt ein Ergebnis-Layer, Fehler erzeugen stattdessen eine klare Rueckmeldung mit naechstem Schritt

## Cookbook-Story

Die zentrale Story lautet:

- `rembg` ist keine normale Core-Dependency
- der Image Tools Worker wird separat eingerichtet
- der Worker kann lokal ueber eine Python-3.12-venv oder ueber Docker betrieben werden
- der Odysseus-Core selbst muss dafuer nicht mit fragilen Image-Dependencies ueberladen werden

## Nutzererklaerung fuer Setup

Empfohlene Grundformulierung:

`Background Removal laeuft ueber einen separaten Image Tools Worker. Der Odysseus-Core bleibt dabei frei von rembg und aehnlichen Image-Dependencies.`

Ergaenzender Setup-Hinweis:

`Richte den Worker separat ein, entweder lokal in einer Python-3.12-venv oder isoliert per Docker.`

## Windows-Formulierung

Windows soll nicht pauschal als unsupported erscheinen.

Stattdessen gilt:

- die Core-venv ist nicht der richtige Ort fuer `rembg`, besonders nicht in der Python-3.14-Hauptumgebung
- Windows kann fuer den Worker weiterhin unterstuetzt sein, wenn der Worker isoliert ueber Python 3.12 oder Docker laeuft

Empfohlene Formulierung:

`Die Python-3.14-Core-venv ist kein unterstuetzter Ort fuer rembg. Der Image Tools Worker kann unter Windows separat ueber Python 3.12 oder Docker eingerichtet werden.`

Nicht erlaubt:

- pauschales `Windows unsupported`
- pauschales `Background Removal unsupported`
- unerklaerte rembg- oder pip-Fehlermeldungen im Nutzertext

## Status- und Fehlertexte

Jeder Zustand braucht einen klaren Nutzertext und genau eine Primaeraktion.

## `disabled`

Bedeutung:

- der Worker ist absichtlich ausgeschaltet

Empfohlener Nutzertext:

`Background Removal ist derzeit deaktiviert. Richte den Image Tools Worker ein, um freigestellte PNGs zu erzeugen.`

Primaeraktion:

- `Worker einrichten`

## `not_configured`

Bedeutung:

- der Worker ist noch nicht vollstaendig konfiguriert

Empfohlener Nutzertext:

`Background removal worker is not configured. Pruefe den Worker-Modus und die Setup-Anleitung.`

Primaeraktion:

- `Worker einrichten`

## `worker_unreachable`

Bedeutung:

- der konfigurierte Worker antwortet nicht oder ist nicht erreichbar

Empfohlener Nutzertext:

`Der Image Tools Worker ist aktuell nicht erreichbar. Pruefe, ob der Worker laeuft und unter der konfigurierten Adresse erreichbar ist.`

Primaeraktion:

- `Erneut versuchen`

## `timeout`

Bedeutung:

- der Worker hat nicht innerhalb des Zeitbudgets geantwortet

Empfohlener Nutzertext:

`Die Background-Removal-Anfrage hat das Zeitlimit ueberschritten. Versuche es erneut oder pruefe die Worker-Performance.`

Primaeraktion:

- `Erneut versuchen`

## `invalid_image`

Bedeutung:

- die Eingabe ist kein gueltiges oder unterstuetztes Bild

Empfohlener Nutzertext:

`Dieses Bild kann nicht fuer Background Removal verarbeitet werden. Verwende eine gueltige Bilddatei und versuche es erneut.`

Primaeraktion:

- `Anderes Bild waehlen`

## `payload_too_large`

Bedeutung:

- das Bild ueberschreitet das konfigurierte Groessenlimit

Empfohlener Nutzertext:

`Das Bild ist zu gross fuer den Image Tools Worker. Verkleinere die Datei oder nutze eine kleinere Vorlage.`

Primaeraktion:

- `Bild verkleinern`

## `permission_denied`

Bedeutung:

- der Aufruf ist durch das bestehende Berechtigungsgate nicht erlaubt

Empfohlener Nutzertext:

`Du darfst Background Removal in diesem Kontext gerade nicht ausfuehren. Pruefe die Bildberechtigung oder den aktuellen Modus.`

Primaeraktion:

- `Berechtigung pruefen`

## Editor-Erwartung

Fuer den Editor gilt:

- ein neues Ergebnis-Layer wird nur bei erfolgreicher Background Removal erzeugt
- bei Fehler wird kein leeres oder defektes Ergebnis-Layer angelegt
- die Rueckmeldung erscheint klar und kurz
- bei Setup- oder Worker-Fehlern wird ein Setup-Link oder Cookbook-Hinweis angeboten

## Editor-Verhalten bei Erfolg

Bei Erfolg:

- Background Removal erzeugt ein neues Ergebnis-Layer
- das Ergebnis ist als verarbeitete Ausgabe erkennbar
- kein zusaetzlicher Fehlertext bleibt sichtbar

## Editor-Verhalten bei Fehler

Bei Fehler:

- kein Ergebnis-Layer
- keine kryptische Serverfehlermeldung
- klare, statusabhaengige Meldung
- genau eine Primaeraktion

## Cookbook-Erwartung

Das Cookbook soll nicht wie ein Troubleshooting fuer kaputte Core-Dependencies klingen.

Stattdessen soll es:

- den Worker als separates Setup erklaeren
- zwischen `local-venv` und `docker` als gueltige Betriebswege unterscheiden
- den Windows-Pfad sauber erklaeren
- auf spaetere Setup-Schritte verweisen, ohne Implementation in diesem Slice vorwegzunehmen

## Empfohlene Cookbook-Struktur

- Was ist der Image Tools Worker?
- Warum ist `rembg` keine Core-Dependency?
- Welche Betriebsmodi gibt es?
- Wie richtet man den Worker lokal oder per Docker ein?
- Welche Fehlermeldungen kann der Nutzer sehen?
- Welche naechste Aktion passt pro Fehler?

## Primaeraktions-Regel

Pro Zustand gibt es genau eine sichtbare Primaeraktion.

Erlaubte Primaeraktionen in diesem Scope:

- `Worker einrichten`
- `Erneut versuchen`
- `Anderes Bild waehlen`
- `Bild verkleinern`
- `Berechtigung pruefen`

Nicht erlaubt:

- zwei gleich starke Hauptbuttons im selben Fehlerzustand
- technische Debug-Aufforderungen als Primaeraktion

## Akzeptanzkriterien fuer spaeteres `ITW5-cookbook-ui-alignment`

`ITW5A` ist nur dann sauber abgeschlossen, wenn der spaetere UI-/Cookbook-Slice ohne neue Copy-Grundsatzdebatte umgesetzt werden kann.

Mindestens klar sein muss:

- `rembg` wird als separate Worker-Dependency erklaert, nicht als normale Core-Dependency
- Windows wird nicht pauschal als unsupported beschrieben
- `disabled`, `not_configured`, `worker_unreachable`, `timeout`, `invalid_image`, `payload_too_large` und `permission_denied` haben klare Nutzertexte
- jeder Zustand hat genau eine Primaeraktion
- der Editor erzeugt nur bei Erfolg ein Ergebnis-Layer
- bei Fehlern erscheint eine klare Meldung plus Setup-Hinweis oder passende Folgeaktion
- Cookbook und Editor verwenden dieselbe Grundsprache fuer Setup und Fehler

## Hotfile-Risiken fuer spaeter

Besonders sensibel fuer `ITW5` oder verwandte Folgearbeit sind:

- `static/js/cookbook.js`
- `routes/shell_routes.py`
- `static/js/editor/ai-rembg.js`
- `static/js/editor/ai-tool-runner.js`
- Gallery- und Editor-Copy

Risiken:

- Cookbook- und Editor-Texte laufen sprachlich auseinander
- Editor zeigt weiter rohe Server- oder Importfehler
- Shell-Route oder Cookbook-Backend liefert Copy, die den Worker irrtuemlich als Core-Dependency darstellt
- Erfolg und Fehler erzeugen dieselbe oder eine verwirrende UI-Reaktion

## Nicht-Ziele

`ITW5A` fuehrt bewusst nicht aus:

- keine UI-Implementierung
- keine API-Aenderung
- keinen Worker-Code
- keine Telegram-Aktion
- keine Tests
- keine Frontend- oder Backend-Dateien
- keine Requirements- oder Docker-Aenderungen

Der Vertrag beschreibt nur die Nutzerkommunikation und Cookbook-/Editor-Erwartung fuer einen spaeter isolierten Image Tools Worker.
