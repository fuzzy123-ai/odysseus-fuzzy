# System Health Auto-Alerting Contract

Stand: 2026-06-17

Status: **SHC5A Docs-Contract fuer System Health Checker Auto-Alerting**

Quellen:

- `docs/plans/system-health-rule-engine-alert-contract.md`
- `docs/plans/system-health-telegram-pull-status-contract.md`
- `docs/plans/system-health-checker-plugin.md`

Dieser Contract definiert Auto-Alerting nur als sichere Decision- und Queue-Semantik. Er beschreibt, wann ein spaeteres System einen Push-Alert senden duerfte, wann Cooldown oder Dedupe unterdruecken und wann ein Recovery-Hinweis zulaessig ist. Der Slice fuehrt bewusst keine Telegram-Auslieferung, keine Netzwerkausfuehrung, keine Token-Nutzung und keine Runtime-Aktion aus.

## Ziel

Odysseus braucht eine konservative Auto-Alerting-Logik, die Alerts priorisiert, Spam vermeidet und Recovery sauber trennt.

Diese Logik soll:

- kritische und warnende Alert-Entscheidungen bewerten
- Cooldown und Dedupe anwenden
- Recovery nur bei echter Entwarnung erlauben
- keine Pseudo-Aktionen oder implizite Pushes erzeugen

## Leitregel

Auto-Alerting in diesem Slice ist nur Entscheidung, nicht Auslieferung.

Das bedeutet:

- keine Telegram-Sends
- keine Netzwerkausfuehrung
- keine Token-Nutzung
- keine Host- oder Reparatur-Aktion

## Inputs

Das spaetere Auto-Alerting-Modell soll mindestens diese Inputs kennen:

- `AlertDecision`
- `RuleEvaluation`
- `previous_sent_keys`
- `cooldown_state`
- `recovery_state`

## Bedeutung der Inputs

### `AlertDecision`

Enthaelt die aktuelle konservative Alert-Bewertung fuer einen betroffenen Bereich.

Typische Inhalte spaeter:

- severity
- cause
- next_action
- dedupe_key

### `RuleEvaluation`

Enthaelt die zugrunde liegende Regel- oder Snapshot-Auswertung, die den Alert fachlich begruendet.

### `previous_sent_keys`

Sammlung oder Sicht auf bereits versandte beziehungsweise als versendet betrachtete Alert-Schluessel.

Wichtig:

- dient nur der Dedupe-/Cooldown-Entscheidung
- kein echter Versandnachweis in diesem Slice

### `cooldown_state`

Bereinigter Zustand, ob ein gleichartiger Alert aktuell unter Cooldown steht.

### `recovery_state`

Bereinigter Zustand, ob fuer einen zuvor aktiven Alert spaeter eine Recovery-Ausgabe zulaessig ist.

## Outputs

Das Auto-Alerting-Modell soll mindestens diese Output-Zustaende kennen:

- `send`
- `suppress_cooldown`
- `suppress_duplicate`
- `send_recovery`
- `no_action`

## Bedeutung der Outputs

### `send`

Die aktuelle Lage waere spaeter fuer eine Auslieferung geeignet.

Wichtig:

- nur Entscheidungszustand
- keine echte Auslieferung

### `suppress_cooldown`

Die Lage ist relevant, soll aber wegen aktivem Cooldown nicht erneut als frischer Push behandelt werden.

### `suppress_duplicate`

Die Lage entspricht einem bereits bekannten, identischen Alert und soll nicht erneut gepusht werden.

### `send_recovery`

Eine Recovery-Ausgabe waere spaeter zulaessig.

Wichtig:

- nur wenn vorheriger Alert wirklich aktiv war
- nur wenn jetzt eine echte Entwarnung vorliegt

### `no_action`

Es soll aktuell keine Push-Entscheidung entstehen.

Beispiele:

- Severity `ok`
- Datenlage `unknown`
- kein ausreichend begruendeter Alert

## Dedupe-Semantik

Auto-Alerting muss spaeter identische Alerts unterdruecken koennen.

Grundlage:

- `dedupe_key`
- `previous_sent_keys`

Die Kurzlogik lautet:

- gleicher `dedupe_key` plus noch aktive Lage -> kein neuer frischer Push

## Cooldown-Semantik

Cooldown verhindert Alert-Spam bei gleichbleibender Stoerung.

Die Kurzlogik lautet:

- ein Alert kann fachlich weiter gueltig sein
- trotzdem wird ein neuer Push waehrend des Cooldowns unterdrueckt

Wichtig:

- Cooldown ist Auslieferungsdrosselung
- keine fachliche Entwarnung

## Recovery-Semantik

Recovery darf nur erzeugt werden, wenn:

- ein vorheriger Alert mit demselben `dedupe_key` wirklich aktiv war
- der Alert jetzt fachlich als resolved gilt
- keine unklare oder unbekannte Zwischenlage vorliegt

Recovery darf nicht erzeugt werden bei:

- fehlendem vorherigen aktiven Alert
- unvollstaendigen Zustandsdaten
- bloess verändertem Zeitstempel

## Copy-Regeln

Auto-Alert-Copy soll spaeter ruhig, konkret und handlungsorientiert bleiben.

Mindestens enthalten:

- Ursache
- Schwere
- naechste Handlung

Nicht enthalten:

- Reparaturbehauptung
- Panik-Sprache
- Secrets

Empfohlene Form:

- `Severity: critical`
- `Cause: disk usage on / remains above threshold`
- `Next action: review disk usage on host`

Nicht zulaessig:

- `system repaired`
- `panic`
- Bot-Token oder sensitive Daten im Text

## Keine Secrets

Auto-Alerting-Entscheidungen und spaetere Push-Texte duerfen keine Secrets enthalten.

Nicht enthalten:

- Bot-Tokens
- Host-Credentials
- rohe Logs
- unbereinigte CLI-Ausgaben
- sensible Debug-Dumps

## Konservative Entscheidungslogik

Die Kurzlogik lautet:

- `critical` oder relevante `warn`-Lage koennen spaeter `send` ergeben
- aktiver Cooldown fuehrt zu `suppress_cooldown`
- identischer aktiver Alert fuehrt zu `suppress_duplicate`
- echte Entwarnung nach aktivem Alert kann `send_recovery` ergeben
- sonst `no_action`

## Keine Pseudo-Aktionen

Dieser Contract erlaubt keine Aktion, die wie echte Auslieferung oder Reparatur wirkt.

Nicht zulaessig:

- Telegram-Nachricht direkt senden
- Netzwerkaufruf ausfuehren
- Host-Aktion anstossen
- Alert als automatisch geloest darstellen

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf nur reine Modelle bauen fuer:

- `AutoAlertDecision`
- `AutoAlertBatch`

Wichtig:

- keine IO
- keine Telegram-Bibliothek
- keine Netzwerkausfuehrung
- keine Token-Nutzung
- Tests nur mit Mock-Decisions und Mock-States

## Beispiel fuer spaetere sichere Entscheidungen

Zulaessig:

- `critical disk alert` + kein Cooldown + nicht gesendet -> `send`
- gleicher `dedupe_key` noch aktiv -> `suppress_duplicate`
- gleicher Alert innerhalb Cooldown -> `suppress_cooldown`
- vorher aktiver Alert jetzt resolved -> `send_recovery`
- `unknown` Lage ohne verlässliche Entscheidung -> `no_action`

Nicht zulaessig:

- `send_recovery` ohne vorherigen aktiven Alert
- `send` trotz fehlender Ursache oder unklarer Datenlage
- sofortige Telegram-Auslieferung im Modell

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keine Telegram-Push-Implementierung
- keine Netzwerkausfuehrung
- keine Token-Integration
- keine Host-Agent-Ausfuehrung
- keine UI-Implementierung

Er legt nur fest, wie spaetere Push-Alert-Entscheidungen konservativ, dedupliziert, cooldown-sicher und recovery-faehig modelliert werden sollen.
