# Image Tools Worker Route Integration Contract

Stand: 2026-06-16

Status: **ITW3A Route-/Integrationsvertrag fuer `0.16.x Image Tools Worker Route Integration`**

Quellen:

- `docs/plans/image-tools-worker-contract.md`
- `docs/plans/image-tools-worker-ui-cookbook-contract.md`

Dieser Vertrag definiert die sichere Integration des spaeteren `ImageToolsWorkerClient` in die bestehende Route `/api/image/remove-bg`. Der Slice fuehrt bewusst keine Backend-, Route-, UI-, Worker-, Test-, Docker- oder Dependency-Aenderungen aus. Er friert nur Prioritaeten, Antwortformat-Erwartungen, Fehler-Mapping, Sicherheitsgrenzen und Akzeptanzkriterien ein, damit `ITW3-route-integration` spaeter sequenziell und ohne Core-Regression umgesetzt werden kann.

## Ziel

`/api/image/remove-bg` soll spaeter zuerst den `ImageToolsWorkerClient` nutzen, wenn ein Worker konfiguriert ist.

Dabei gilt:

- bestehende Editor-Erwartungen duerfen nicht still brechen
- der Core darf keine neuen harten Image-Dependencies aufnehmen
- das bestehende Berechtigungsgate bleibt unveraendert vor dem Worker-Aufruf aktiv

## Leitregel

Die Route bleibt stabil, waehrend die eigentliche Bildverarbeitung in den isolierten Worker wandert.

Das bedeutet:

- die Route bleibt der bekannte Einstiegspunkt
- die eigentliche Remove-BG-Logik wird nicht mehr im Core-Importpfad verankert
- Worker-Fehler werden klar und strukturiert an UI und Editor durchgereicht

## Route-Zielbild

Die Zielroute bleibt:

- `/api/image/remove-bg`

Das Zielverhalten lautet:

- wenn Worker konfiguriert und erreichbar ist, nutzt die Route zuerst den `ImageToolsWorkerClient`
- wenn Worker nicht konfiguriert oder nicht erreichbar ist, liefert die Route einen strukturierten Fehler statt eines kryptischen Serverfehlers
- ein Legacy-Fallback darf nur explizit und bewusst zugelassen werden, nie implizit

## Prioritaet der Ausfuehrung

Die spaetere Reihenfolge lautet:

1. Berechtigung pruefen
2. Payload und Eingabedaten validieren
3. Worker-Konfiguration und Modus pruefen
4. `ImageToolsWorkerClient` aufrufen, wenn erlaubt und sinnvoll
5. Erfolg oder strukturierten Fehler zurueckgeben
6. optionaler Legacy-Fallback nur bei expliziter Freigabe

Nicht erlaubt:

- zuerst schwere Core-Imports versuchen und erst spaeter auf Worker umschwenken
- stiller Fallback auf fragile Core-Dependencies

## Antwortformat-Stabilitaet

Die bestehende Editor-Erwartung muss stabil bleiben.

Das bedeutet:

- Erfolg liefert weiter eine Bild- oder Base64-kompatible Payload
- die Response bleibt fuer den bestehenden Editor-Layer-Flow konsumierbar
- Fehler werden strukturiert und UI-verstaendlich geliefert

## Erfolgsantwort

Bei Erfolg soll die Route weiter eine Payload liefern, die:

- mit dem bestehenden Editor-Layer-Flow kompatibel ist
- als freigestellte Bildausgabe verarbeitet werden kann
- keine neue unerwartete Semantik einfuehrt

Der Contract verlangt keine exakte JSON-Form hier neu zu definieren, solange die bestehende Success-Erwartung erhalten bleibt.

## Fehlerantwort

Bei Fehler soll die Route:

- keinen HTML-Fehler oder Roh-Trace liefern
- einen strukturierten Fehlercode liefern
- eine kurze UI-verstaendliche Nachricht oder klar ableitbare Fehlerlage haben

## Privilege-Gate

Das bestehende `can_generate_images`-Gate bleibt unveraendert vor der Worker-Nutzung aktiv.

Das bedeutet:

- keine Worker-Anfrage ohne bestehende Berechtigungspruefung
- `permission_denied` bleibt ein gueltiger Ausgang vor jeder spaeteren Bildverarbeitung
- der Worker wird nicht als Sonderpfad ausserhalb des Sicherheitsmodells behandelt

## Legacy-Fallback-Regel

Ein Legacy-Fallback ist nur explizit erlaubt, nicht Default.

Das bedeutet:

- kein harter Core-Import von `rembg`
- kein harter Core-Import von `transformers`
- kein harter Core-Import von `PIL` nur fuer den Remove-BG-Pfad
- keine implizite Wiederbelebung alter Dependency-Pfade

Wenn ein Fallback existiert, dann nur:

- bewusst konfiguriert
- klar dokumentiert
- testbar deaktivierbar

## Hint-Mask- und Crop-Erwartung

Vorhandene Hint-Mask-, Crop- oder aehnliche Editor-Semantik darf nicht still geaendert werden.

Stattdessen gilt:

- bestehende semantische Eingaben bleiben erhalten
- sie werden entweder vor dem Worker-Aufruf korrekt vorbereitet
- oder als saubere Worker-Option durchgereicht

Nicht erlaubt:

- Hint-Mask still ignorieren
- Crop-Semantik veraendern, ohne dass UI oder Editor das weiss
- Payload-Felder heimlich umdeuten

## Fehler-Mapping

Die Route muss spaeter mindestens diese Fehler sauber auf die HTTP-/UI-Schicht abbilden:

- `not_configured`
- `worker_unreachable`
- `timeout`
- `invalid_image`
- `payload_too_large`
- `permission_denied`
- `dependency_missing`

## Bedeutungen im Route-Kontext

### `not_configured`

Der Worker ist deaktiviert oder unvollstaendig konfiguriert.

Erwartung:

- strukturierter Fehler
- UI kann Setup-Hinweis anzeigen

### `worker_unreachable`

Der konfigurierte Worker ist nicht erreichbar.

Erwartung:

- strukturierter Fehler
- kein kryptischer Gateway- oder Connection-Trace im Nutzerpfad

### `timeout`

Der Worker antwortet nicht innerhalb des Zeitbudgets.

Erwartung:

- strukturierter Fehler
- Retry- oder Performance-Hinweis moeglich

### `invalid_image`

Die Eingabe ist ungueltig oder nicht verarbeitbar.

Erwartung:

- strukturierter Fehler
- Editor kann klares Nutzerfeedback geben

### `payload_too_large`

Die Eingabe ueberschreitet das Limit.

Erwartung:

- fruehe und klare Ablehnung
- kein unnötiger Worker-Aufruf

### `permission_denied`

Die Anfrage scheitert am bestehenden Berechtigungsgate.

Erwartung:

- konsistente Sicherheitsantwort
- kein Worker-Aufruf

### `dependency_missing`

Der Worker-Modus wurde angefordert, aber seine Runtime oder Dependency-Zone fehlt.

Erwartung:

- strukturierter Fehler
- nicht als Core-Dependency-Problem verschleiern

## Security

## Payload-Limit

Die Route soll vor oder spaetestens beim Worker-Aufruf ein klares Payload-Limit respektieren.

Regeln:

- keine unbounded Bildnutzlast
- `payload_too_large` als sauberer Ausgang
- keine unnötige Weiterleitung uebergrosser Daten

## Transport und Zieladresse

Regeln:

- lokaler oder kontrollierter Worker-Zugriff ist Default
- `localhost` oder gleichwertige lokale Bindung bleibt die sichere Standardannahme
- kein offener Remote-Default

## Logging

Regeln:

- keine sensiblen Bilddaten in Logs
- keine Base64-Dumps in Fehlermeldungen
- keine unkontrollierte Protokollierung von Masken-, Crop- oder Rohbilddaten

## Akzeptanzkriterien fuer spaeteres `ITW3-route-integration`

`ITW3A` ist nur dann sauber abgeschlossen, wenn Bob und Charlie daraus den eigentlichen Route-Slice ohne neue Grundsatzdebatte bauen koennen.

Mindestens klar sein muss:

- `/api/image/remove-bg` bleibt der stabile Einstiegspunkt
- die Route nutzt zuerst den `ImageToolsWorkerClient`, wenn Worker konfiguriert ist
- das bestehende `can_generate_images`-Gate bleibt unveraendert davor
- Success bleibt fuer den Editor-Layer-Flow kompatibel
- Fehler werden strukturiert und UI-verstaendlich geliefert
- Hint-Mask- und Crop-Semantik wird nicht still veraendert
- Legacy-Fallback ist optional und standardmaessig aus
- der Core fuehrt keine neuen harten `rembg`-, `transformers`- oder `PIL`-Imports fuer diesen Pfad ein

## Tests, die spaeter Bob und Charlie brauchen

Mindestens folgende Testfaelle sollen spaeter abgedeckt werden:

- configured success
- disabled oder `not_configured`
- `worker_unreachable`
- `permission_denied`
- `payload_too_large`
- legacy fallback off by default

Optional zusaetzlich sinnvoll:

- `timeout`
- `invalid_image`
- `dependency_missing`
- Hint-Mask- oder Crop-Durchreichung ohne Semantikverlust

## Hotfile-Risiken

Besonders sensibel fuer den spaeteren Route-Slice sind:

- `routes/gallery_routes.py`
- bestehende remove-bg-Response-Erwartung
- Editor-Layer-Flow
- Privilege-Decorator oder Berechtigungsgate

Risiken:

- Route-Refactor aendert versehentlich das erwartete Editor-Antwortformat
- Worker-Fehler schlagen als generische Serverfehler durch
- Legacy-Fallback wird still zur neuen Default-Logik
- Sicherheitspruefung passiert zu spaet oder gar erst nach dem Worker-Aufruf

## Nicht-Ziele

`ITW3A` fuehrt bewusst nicht aus:

- keine Implementierung
- keinen Worker-MVP
- keine UI- oder Cookbook-Aenderung
- keine Telegram-Aktion
- keine Tests
- keine Backend- oder Route-Dateien
- keine Worker-App-, Requirements- oder Docker-Aenderungen

Der Vertrag beschreibt nur die sichere Route-, Fehler- und Sicherheitsintegration fuer den spaeteren Worker-gestuetzten Remove-BG-Pfad.
