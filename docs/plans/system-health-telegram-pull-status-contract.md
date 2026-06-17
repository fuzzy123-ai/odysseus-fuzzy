# System Health Telegram Pull Status Contract

Stand: 2026-06-17

Status: **SHC4A Docs-Contract fuer System Health Checker Telegram Pull Status**

Quellen:

- `docs/plans/system-health-agent-interface-contract.md`
- `docs/plans/system-health-rule-engine-alert-contract.md`
- `docs/plans/system-health-container-runtime-adapter-contract.md`
- `docs/plans/system-health-checker-plugin.md`

Dieser Contract definiert sichere Pull-Kommandos fuer spaetere Telegram-Statusabfragen des System Health Checkers. Er beschreibt nur Anfrage-/Antwortmodelle, Allowlist-Regeln und konservative Response-Copy gegen `HealthSnapshot`- und Alert-Daten. Der Slice fuehrt bewusst keine Bot-Library, kein Long Polling, kein Webhook, keine Netzwerkausfuehrung und keine Token-Nutzung aus.

## Ziel

Odysseus braucht eine sichere, kleine Telegram-Pull-Semantik fuer Health-Status.

Diese Semantik soll spaeter:

- bekannte Pull-Kommandos klar modellieren
- unbekannte oder nicht autorisierte Anfragen sauber blockieren
- ruhige, knappe und handlungsorientierte Antworten liefern
- keine Secrets oder Host-Details leaken

## Leitregel

Telegram Pull Status ist read-only Anfrage/Antwort, keine Aktion.

Das bedeutet:

- keine Push-Alerts in diesem Slice
- keine Bot-Token-Nutzung
- keine Netzwerk- oder Polling-Ausfuehrung
- keine Reparatur- oder Host-Aktion

## Pull-Kommandos

Die spaetere Command-Semantik soll mindestens diese Pull-Kommandos kennen:

- `/status`
- `/alerts`
- `/disk`
- `/updates`
- `/containers`

## Bedeutung der Pull-Kommandos

### `/status`

Gibt eine kompakte Gesamtsicht auf den aktuellen `HealthSnapshot`.

Typische Inhalte:

- Gesamtstatus
- auffaelligste Ursache
- naechste sichere Handlung

### `/alerts`

Gibt die aktuell relevanten Alert-Zustaende kompakt wieder.

Typische Inhalte:

- aktive Warnungen
- kritische Eintraege
- recovery/cooldown-nahe Hinweise spaeter nur wenn im Snapshot modelliert

### `/disk`

Gibt die bereinigte Disk-Lage aus Snapshot/Alert-Sicht wieder.

### `/updates`

Gibt spaeter die Update-Lage nur dann wieder, wenn dafuer Daten im Snapshot enthalten sind.

Wichtig:

- wenn keine Update-Daten vorliegen, bleibt die Antwort konservativ `no_data` oder `unknown`

### `/containers`

Gibt die bereinigte Container-Runtime-Lage wieder.

Typische Inhalte:

- Runtime-Typ
- Container-Anzahl
- unhealthy_count
- setup_hint falls relevant

## Allowlist-Regel

Telegram-Anfragen duerfen spaeter nur fuer explizit erlaubte Telegram User IDs beantwortet werden.

Das bedeutet:

- allowlist-basiert
- unbekannter User wird blockiert
- keine stille Teilantwort fuer nicht autorisierte Anfragen

## Token-Sicherheit

Bot-Tokens bleiben in diesem gesamten Track ausserhalb von Logs, Repo und Response-Texten.

Harte Regeln:

- Token nie loggen
- Token nie im Repo
- Token nie in Command-/Response-Modellen

## Unknown User Block

Ein unbekannter oder nicht erlaubter Telegram User fuehrt zu:

- `blocked_unauthorized`

Wichtig:

- keine Snapshot-Details leaken
- keine Alert-Inhalte leaken
- keine Host- oder Runtime-Informationen leaken

## Response-States

Die spaetere Telegram Pull Response soll mindestens diese Zustaende kennen:

- `allowed`
- `blocked_unauthorized`
- `unsupported_command`
- `no_data`
- `ok`
- `warn`
- `critical`

## Bedeutung der Response-States

### `allowed`

Die Anfrage stammt von einer erlaubten User ID und kann grundsaetzlich beantwortet werden.

### `blocked_unauthorized`

Die Anfrage wird wegen fehlender Allowlist-Freigabe blockiert.

### `unsupported_command`

Das Telegram-Kommando ist nicht Teil der erlaubten Pull-Semantik.

### `no_data`

Es liegen aktuell keine passenden Snapshot-Daten fuer diese konkrete Abfrage vor.

### `ok`

Die Antwort zeigt einen gesunden oder unauffaelligen Snapshot-Bereich.

### `warn`

Die Antwort zeigt eine warnende Lage.

### `critical`

Die Antwort zeigt eine kritische Lage mit klarer Handlungsempfehlung.

## Mapping zu Snapshot und Alerts

Die Pull-Antworten sollen spaeter nur aus bereits uebergebenen oder verfuegbaren Health-Daten gerendert werden.

Typische Quellen:

- `HealthSnapshot`
- `CollectorStatus`
- `AlertSummary`
- `RuntimeStatus`

Wichtig:

- kein Nachladen neuer Host-Daten
- keine Netzwerkanfrage fuer Runtime-Details

## Keine Secrets im Response-Text

Antworttexte duerfen keine Secrets enthalten.

Nicht enthalten:

- Bot-Token
- Host-Credentials
- rohe Logs
- unbereinigte CLI-Ausgaben
- sensible Pfade oder Debug-Dumps

## Copy-Regeln

Telegram-Responses sollen ruhig, knapp und handlungsorientiert bleiben.

Mindestens enthalten:

- Status
- Ursache
- naechste Handlung

Nicht enthalten:

- Panik-Sprache
- Reparaturbehauptung
- unsichere Behauptungen als Tatsachen

Empfohlene Form:

- `Status: warn`
- `Cause: disk usage on / is high`
- `Next action: review disk usage on host`

## Kommandospezifische konservative Antwortlogik

### `/status`

Soll spaeter eine knappe Gesamtsicht liefern:

- `ok`, `warn`, `critical` oder `no_data`
- eine Hauptursache
- eine naechste Handlung

### `/alerts`

Soll aktive Alerts knapp listen oder konservativ mitteilen:

- `no_data`
- `no active alerts`

Wichtig:

- keine Spam-Listen
- keine rohen History-Dumps

### `/disk`

Soll nur bereinigte Disk-Infos und ggf. Disk-bezogene Alerts ausgeben.

### `/updates`

Wenn keine Update-Daten vorhanden sind:

- `no_data`

Keine erfundene Update-Lage.

### `/containers`

Soll nur bereinigte Runtime-/Container-Infos ausgeben.

Wenn Runtime unbekannt oder unsupported ist:

- klar sagen, statt einen gesunden Zustand zu erfinden

## Unsupported Command

Nicht erkannte Kommandos fuehren zu:

- `unsupported_command`

Wichtig:

- keine stillschweigende Umdeutung auf ein anderes Kommando

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf nur reine Modelle und Renderer bauen fuer:

- Telegram-Command-Dataclasses
- Telegram-Response-Dataclasses
- Renderer gegen Mock-`HealthSnapshot`

Wichtig:

- keine Bot-Library
- keine Netzwerkaktion
- keine Telegram-Session
- keine Token-Nutzung

## Beispiel fuer spaetere sichere Antworten

Zulaessig:

- `/status` -> `warn`, `cause: memory usage is high`, `next_action: review memory pressure on host`
- `/containers` -> `ok`, `cause: podman runtime detected, no unhealthy containers`, `next_action: none`
- unbekannter User -> `blocked_unauthorized`
- unbekanntes Kommando -> `unsupported_command`

Nicht zulaessig:

- Token im Fehlertext
- rohe Host-Logs
- `system repaired`
- Bot-Aktion direkt im Response-Modell

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keine Telegram-Bot-Implementierung
- kein Long Polling
- kein Webhook
- keine Netzwerk- oder Token-Integration
- keine Push-Alerts

Er legt nur fest, wie spaetere Telegram-Pull-Kommandos sicher, allowlist-basiert und read-only gegen bestehende Health-Snapshots und Alerts modelliert werden sollen.
