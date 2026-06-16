# Image Tools Worker Contract

Stand: 2026-06-16

Status: **ITW1 Produkt-/Runtime-Vertrag fuer `0.16.x Isolated Image Tools Worker`**

Quellen:

- `docs/plans/unified-odysseus-roadmap.md`

Dieser Vertrag definiert die Produkt- und Runtime-Grenzen fuer einen isolierten Image Tools Worker. Ziel ist, Background Removal und spaetere Image-AI-Werkzeuge von der Odysseus-Core-Laufzeit zu entkoppeln. Der Slice fuehrt bewusst keine Runtime-, Worker-, UI-, Docker-, Dependency- oder Test-Aenderungen aus. Er friert nur die Zielentscheidung, Capabilities, Modi, Config-Schluessel, Fehlersemantik, Sicherheitsgrenzen und Akzeptanzkriterien ein.

## Zielentscheidung

`rembg` und vergleichbar fragile Image-Dependencies werden nicht in die Odysseus-Core-venv gepresst.

Stattdessen gilt:

- der Odysseus-Core bleibt stabil und frei von schwerer Image-Tool-Last
- Background Removal laeuft ueber einen isolierten Worker
- der Worker darf lokal in einer separaten Python-3.12-venv, in Docker oder spaeter remote laufen
- die Haupt-venv bleibt nicht der Ort fuer experimentelle oder systemnahe Image-AI-Abhaengigkeiten

## Leitregel

Schwere Image-Tools sind optionale Worker-Capabilities, keine verpflichtende Core-Dependency.

Das bedeutet:

- fehlende Worker-Konfiguration darf den Core nicht instabil machen
- die Gallery oder spaetere Image-Actions duerfen klare, strukturierte Fehler liefern statt kryptische Import- oder Serverfehler
- ein deaktivierter Worker ist ein gueltiger Betriebszustand

## Capabilities

Der erste verpflichtende Capability-Scope ist:

- `remove_background`

Spaeter optional zulaessig, aber nicht Teil dieses Slice:

- `sharpen`
- `upscale`
- `inpaint`

Diese spaeteren Capabilities muessen derselben Isolationslogik folgen und duerfen nicht still in den Core einsickern.

## Runtime-Modi

Der Worker kennt vier Betriebsmodi.

## `disabled`

Der Worker ist bewusst ausgeschaltet.

Erwartung:

- Core startet normal
- Aufrufe liefern einen strukturierten `not_configured`- oder gleichwertigen Status
- UI zeigt eine klare Setup-Meldung statt eines Stacktraces

## `local-venv`

Der Worker laeuft lokal in einer separaten, fuer Image-Tools reservierten Python-3.12-venv.

Erwartung:

- eigene Dependency-Zone
- kein Zwang, dass die Core-venv dieselben Pakete installiert
- klarer Lokalpfaad fuer spaetere, isolierte Ops-Doku

## `docker`

Der Worker laeuft isoliert in einem Container.

Erwartung:

- Dependencies sind aus der Core-venv ausgelagert
- Transport bleibt lokal oder kontrolliert erreichbar
- keine Annahme, dass Docker fuer alle Nutzer Default sein muss

## `remote`

`remote` ist nur spaeter optional.

Erwartung:

- nicht Default
- nur mit expliziter Konfiguration
- dieselbe Fehler- und Sicherheitssemantik wie lokal

## Config Keys

Der Contract reserviert folgende Konfigurationsschluessel:

- `IMAGE_TOOLS_WORKER_MODE`
- `IMAGE_TOOLS_WORKER_URL`
- `IMAGE_TOOLS_WORKER_TIMEOUT_SEC`
- `IMAGE_TOOLS_WORKER_MAX_MB`
- optional `IMAGE_TOOLS_WORKER_LEGACY_FALLBACK`

## Bedeutung der Config

### `IMAGE_TOOLS_WORKER_MODE`

Definiert den Betriebsmodus.

Erlaubte Werte:

- `disabled`
- `local-venv`
- `docker`
- spaeter `remote`

### `IMAGE_TOOLS_WORKER_URL`

Definiert die Zieladresse fuer einen HTTP- oder vergleichbaren Worker-Zugriff.

Regel:

- fuer `disabled` darf sie leer sein
- fuer `local-venv` oder `docker` zeigt sie idealerweise auf localhost oder eine gleichwertig lokale Bindung
- `remote` braucht spaeter ein explizites Sicherheits-Gate

### `IMAGE_TOOLS_WORKER_TIMEOUT_SEC`

Definiert das harte Zeitbudget pro Worker-Request.

Regel:

- Timeouts muessen klar begrenzt sein
- UI und Core bekommen bei Ueberschreitung einen strukturierten `timeout`-Fehler

### `IMAGE_TOOLS_WORKER_MAX_MB`

Definiert die maximale erlaubte Nutzlastgroesse fuer Input-Bilder.

Regel:

- uebergrosse Dateien werden frueh und klar abgewiesen
- keine unbounded Payloads an lokalen oder remote Worker

### `IMAGE_TOOLS_WORKER_LEGACY_FALLBACK`

Optionaler Uebergangsschalter.

Regel:

- nur fuer kontrollierte Migrations- oder Legacy-Pfade
- kein dauerhafter Default
- darf nicht dazu fuehren, dass fragiler Core-Fallback still wieder zur Norm wird

## Stabile Core-API-Idee

Die stabile Core-Idee fuer Background Removal lautet:

`remove_background(image, hint_mask=None) -> png`

oder bei Fehler:

- strukturierter Fehler
- kein unstrukturierter HTML- oder Stacktrace-Blob

## API-Grundsaetze

- Input ist ein Bildobjekt oder aequivalente Bildnutzlast
- optionaler `hint_mask` bleibt erlaubt, aber nicht verpflichtend
- Erfolgsformat fuer den ersten Scope ist PNG
- Fehler werden in klarer, maschinenlesbarer Form gemeldet

## Fehlersemantik

Mindestens diese Fehler muessen spaeter stabil unterscheidbar sein:

- `not_configured`
- `dependency_missing`
- `worker_unreachable`
- `timeout`
- `invalid_image`
- `payload_too_large`
- `permission_denied`

## Bedeutungen

### `not_configured`

Der Worker ist nicht aktiviert oder nicht vollstaendig konfiguriert.

### `dependency_missing`

Der isolierte Worker-Modus wurde angefordert, aber die benoetigte Runtime oder Dependency-Umgebung fehlt.

### `worker_unreachable`

Der konfigurierte Worker konnte nicht erreicht werden.

### `timeout`

Der Worker hat nicht innerhalb des konfigurierten Zeitbudgets geantwortet.

### `invalid_image`

Die Eingabedatei ist ungueltig, kaputt oder kein unterstuetztes Bildformat.

### `payload_too_large`

Die Eingabe ueberschreitet die erlaubte Nutzlastgroesse.

### `permission_denied`

Der Aufruf ist vom bestehenden Sicherheits- oder Berechtigungsgate nicht erlaubt.

## Security

## Default-Haltung

Sicherer Default ist:

- Worker standardmaessig `disabled`
- kein offener Remote-Default
- lokale Adressen bevorzugen

## Transport

Regeln:

- `localhost` oder gleichwertig lokale Bindung ist Default fuer lokale Worker
- kein impliziter externer Endpunkt
- spaetere Remote-Nutzung braucht explizite Konfiguration

## Payload-Grenzen

Regeln:

- `IMAGE_TOOLS_WORKER_MAX_MB` ist verpflichtend wirksam
- grosse Bilder werden vor dem Worker-Aufruf abgefangen
- keine unbounded Uploads an lokale oder entfernte Worker

## Berechtigung

Das bestehende `can_generate_images`-Gate bleibt erhalten.

Das bedeutet:

- Image-Tools-Worker hebelt bestehende Bildberechtigungen nicht aus
- `permission_denied` bleibt ein gueltiger Ausgang
- UI darf den Worker nicht wie eine ungated Sonderroute behandeln

## UI- und Cookbook-Erwartung

Die Nutzererfahrung darf bei fehlendem Worker nicht in einem kryptischen Serverfehler enden.

Mindesterwartung:

- klare Meldung: `Background removal worker is not configured`
- kurzer Setup-Hinweis fuer die spaetere lokale, Docker- oder Remote-Konfiguration
- keine verwirrende rembg-Importfehlermeldung in Nutzertexten
- Cookbook und Editor duerfen den Nutzer auf Setup statt auf Core-Debugging verweisen

## Akzeptanzkriterien fuer spaetere Slices `ITW2` bis `ITW5`

Der Contract ist nur dann brauchbar, wenn die Folge-Slices ohne Grundsatzdebatte darauf aufbauen koennen.

Mindestens klar sein muss:

- `rembg` ist kein verpflichtender Teil der Core-venv
- `remove_background` ist die erste verpflichtende Worker-Capability
- `disabled`, `local-venv`, `docker` und spaeter `remote` sind als Modi definiert
- die Config Keys und ihre Bedeutungen sind festgelegt
- die Core-API-Idee fuer `remove_background` ist klar
- die Fehlersemantik ist stabil benannt
- `localhost` und Payload-Limits sind Sicherheitsdefault
- `can_generate_images` bleibt das bestehende Berechtigungsgate
- UI/Cookbook muessen klare Setup-Meldungen statt Servertraces liefern

## Erwartete Folge-Slices

### `ITW2`

Worker-Readiness- oder Client-Contract auf Backend-Seite.

### `ITW3`

Isolierter Worker-MVP oder Adapter fuer `remove_background`.

### `ITW4`

UI-/Cookbook-Integration mit klarer Setup- und Fehlerdarstellung.

### `ITW5`

End-to-End-Smoke oder sicherer Gallery-/Editor-Nachweis gegen den isolierten Worker.

## Risiken und Hotfiles fuer Bob und Charlie

Besonders sensibel fuer die Folgearbeit sind:

- `routes/gallery_routes.py`
- `static/js/editor/ai-rembg.js`
- `static/js/editor/ai-tool-runner.js`
- `static/js/cookbook.js`
- `routes/shell_routes.py`
- spaeteres Worker-Verzeichnis

Risiken:

- Gallery-Route koppelt sich zu frueh an konkrete Worker-Details
- Editor-JS zeigt weiter kryptische Fehler oder Core-Importfehler
- Shell- oder Route-Schicht behandelt Worker-Konfiguration nicht als optionalen Zustand
- ein Legacy-Fallback schleust fragile Core-Dependencies wieder unkontrolliert ein

## Nicht-Ziele

`ITW1` fuehrt bewusst nicht aus:

- keinen Worker-Code
- keine `rembg`-Installation
- kein Dockerfile
- keine Gallery-Route-Integration
- keine Telegram-Aktion
- keine Tests
- keine Requirements-, venv- oder Docker-Aenderungen
- keine Runtime-Implementierung

Der Vertrag beschreibt nur die sichere Isolations-, Konfigurations- und Fehlerstrategie fuer einen spaeteren Image Tools Worker.
