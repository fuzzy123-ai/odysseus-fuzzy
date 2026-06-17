# Live System Health Local API Consumer Contract

Stand: 2026-06-17

Status: **LIVE7A Docs-Contract fuer das Gate `live_system_health_local_api_consumer_plan`**

Quellen:

- `docs/plans/live-system-health-host-agent-mvp-contract.md`
- `docs/plans/system-health-agent-interface-contract.md`
- `docs/plans/system-health-security-ops-runbook.md`
- `docs/plans/system-health-plugin-audit-index-contract.md`

Dieser Contract definiert die Operator- und Nutzersprache fuer einen spaeteren lokalen System-Health-API-Consumer im Offline-/Fixture-/Contract-Mode. Der Consumer darf nur sanitisierte Snapshot-Payloads lesen oder simulieren, um spaeter eine lokale API-Anbindung im Plan zu bewerten. Er fuehrt keine echten Netzwerkaufrufe aus, startet keine Host-Kommandos, oeffnet keine Sockets und aktiviert kein Runtime-Polling. Das Gate bleibt read-only und erzeugt kein externes Release-Go.

## Purpose

`LIVE7A` ist die Vorbereitung fuer einen spaeteren lokalen API-Consumer nach dem Host-Agent-MVP-Plan.

Der Contract soll beantworten:

- wie der Consumer nur als Offline-/Fixture-/Contract-Schicht gedacht ist
- welche sanitisierte Snapshot-Payload er spaeter auf hoher Ebene akzeptieren darf
- wie Offline-, Unknown- und Error-Zustaende sichtbar bleiben
- welche Operator-Inputs vor einer spaeteren manuellen Freigabe noetig sind
- wie Alice, Bob und Charlie vor jeder echten API- oder Polling-Aktivierung getrennt bleiben

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keine echten Netzwerkaufrufe
- keine Host-Kommandos
- keine Socket-Zugriffe
- kein Runtime-Polling
- keine Telegram-, Provider-, Export-/Import-/Rebuild-, Nextcloud- oder Scheduler-Aktivierung
- kein externes `1.0`-Go

## Local API Scope And Boundary

Die Section `local_api_scope_and_boundary` soll die harte Trennung zwischen Consumer und Host festsetzen.

Mindestens:

- nur sanitisierte Snapshot-Payloads
- kein aktiver Hostzugriff
- kein Zugriff auf rohe Host-Command-Ausgaben
- keine direkte Abhaengigkeit von Docker-, Podman- oder anderen Sockets
- kein stiller Uebergang von Fixture-Mode zu echter API-Nutzung

Wichtig:

- der Consumer liest spaeter nur bereinigte Daten
- er fuehrt nie selbst Health-Reads auf dem Host aus

## Offline Unknown Error States

Die Section `offline_unknown_error_states` soll die spaeteren Consumer-Zustaende konservativ definieren.

Mindestens:

- `offline`
- `unknown`
- `error`
- `unsupported`

Bedeutung:

- `offline`: kein lokaler API-Zugriff oder bewusst kein laufender Agent
- `unknown`: Zustand nicht verlaesslich bestimmbar
- `error`: Eingabe oder Transport waere fehlerhaft, ohne dass der Consumer versucht zu heilen
- `unsupported`: Quelle oder Snapshot-Typ ist in diesem Kontext nicht unterstuetzt

Wichtig:

- keiner dieser Zustaende darf zu Crash oder improvisierter Live-Aktivierung fuehren

## Required Operator Inputs

Die Section `required_operator_inputs` soll beschreiben, was vor einem spaeteren manuellen API-Freigabeschritt bekannt sein muss.

Mindestens:

- Zielkontext fuer lokale Snapshot-Payloads
- bekannte Snapshot-Version oder Payload-Familie
- klare Operator-Freigabe fuer spaetere API-Nutzung
- definierte Fixture- oder Offline-Testdaten
- Redaktionsregeln fuer spaetere manuelle Evidence

Wichtig:

- fehlende Inputs fuehren spaeter zu `needs_operator_input` statt zu improvisierten API-Aufrufen

## Snapshot Payload Contract

Die Section `snapshot_payload_contract` soll auf hoher Ebene festlegen, welche Payload-Signale der Consumer spaeter akzeptieren darf.

Mindestens:

- Snapshot-Version
- erzeugt-am-Zeitpunkt
- Gesamtstatus
- bereinigte Collector-Zustaende
- kompakte Alert- oder Runtime-Zusammenfassung

Wichtig:

- keine rohen Host-Outputs
- keine Tokens
- keine privaten Pfade
- keine transportnahe Debug-Dumps als Pflichtfeld

## Consumer Boundaries

Die Section `consumer_boundaries` muss die Sicherheitsgrenzen fuer den spaeteren Consumer hart setzen.

Mindestens:

- keine Tokens
- keine Secrets
- kein unsicheres Logging
- keine privaten Pfade
- kein stilles Polling
- keine Netzwerkausfuehrung ohne separates Operator-Gate

Wichtig:

- der Consumer bleibt read-only
- auch ein valider Payload-Plan ist kein Startsignal fuer Live-API-Zugriffe

## Timeout And Error Language

Die Section `timeout_and_error_language` soll die spaetere Fehlersprache beschreiben, ohne echte Calls zu machen.

Mindestens:

- Zeitlimit wird nur als Plan-/Review-Feld beschrieben
- Fehler bleiben kompakt und redigiert
- keine Rohlogs als Standardantwort
- `offline`, `unknown` oder `error` fuehren zu manueller Pruefung statt zu Wiederholungssturm

Wichtig:

- dieser Contract startet keine Timeouts
- er friert nur die spaetere Sprache ein

## Alice Bob Charlie Roles

Die Section `alice_bob_charlie_roles` soll die Aufteilung fuer dieses Gate klar ziehen.

### Alice

Alice verantwortet:

- Nutzer- und Operator-Sprache
- Offline-/Unknown-/Error-Texte
- Boundary- und Stop-Regeln

### Bob

Bob verantwortet:

- isoliertes read-only Consumer-Planmodell
- `src/live_system_health_local_api_consumer.py`
- `tests/test_live_system_health_local_api_consumer.py`
- reine Bewertung von Operator-Inputs und Snapshot-Payload-Signalen

Wichtig:

- Bob darf keine Netzwerkaufrufe starten
- Bob darf keine Host-Kommandos ausloesen
- Bob darf keine Sockets oder Polling aktivieren

### Charlie

Charlie verantwortet:

- Scope-Kontrolle
- Testauswahl fuer read-only Modelle
- Worktree- und Push-Pruefung
- Stop-Entscheidung bei riskanter Scope-Verschiebung oder unklaren Payload-Grenzen

## Stop Rules

Die Section `stop_rules` muss das Abrutschen in echte API- oder Host-Aktivierung verhindern.

Mindestens:

- wenn echte Netzwerkaufrufe gefordert werden: stoppen
- wenn Host-Kommandos oder Sockets verlangt werden: stoppen
- wenn Tokens, Secrets, private Pfade oder rohe Logs auftauchen: stoppen
- wenn Polling oder Live-Retry impliziert wird: stoppen
- wenn Operator-Inputs oder Payload-Grenzen unklar sind: `needs_operator_input` oder `blocked`, nicht `ready`

## Handoff To Bob

Die Section `handoff_to_bob` soll klar machen, was Bob auf dieser Basis bauen darf.

Erlaubt:

- read-only Consumer-Planmodell
- Bewertung von Offline-/Fixture-/Payload-Signalen
- Statusableitung fuer `consumer_plan_ready`, `needs_operator_input`, `blocked`, `deferred`
- Tests mit mockten Snapshot-Payloads

Nicht erlaubt:

- echte API-Calls
- Host-Kommandos
- Socket-Zugriffe
- Polling oder Retry-Mechanismen

Pflicht-Gate-ID:

- `live_system_health_local_api_consumer_plan`

Pflicht-Statuswerte:

- `consumer_plan_ready`
- `needs_operator_input`
- `blocked`
- `deferred`

## Handoff To Next Live Slice

Die Section `handoff_to_next_live_slice` soll beschreiben, wie spaetere Folge-Slices anknuepfen duerfen.

Mindestens:

- Consumer-Plan bleibt Offline-/Fixture-only
- echte lokale API-Anbindung braucht spaeter separates Operator-Gate
- offene externen `1.0`-Gates bleiben unberuehrt
- naechste Live-Slices duerfen nur auf explizit freigegebenen Plan-Artefakten aufbauen

Wichtig:

- auch ein gutes Consumer-Planmodell hebt `provider_fallback_answer_run` und `test_vault_export_import_rebuild` nicht auf
- externes `1.0` bleibt `No-Go`, bis diese manuellen Gates belegt sind

## No-Secrets And No-Raw-Logs

Dieser Consumer-Contract und alle Folge-Artefakte duerfen nicht enthalten:

- Secrets
- Tokens
- private Pfade
- rohe Logs
- komplette Host- oder Payload-Dumps

Zulaessig sind:

- kompakte Statuswerte
- kurze Review- und Stop-Hinweise
- read-only Snapshot- oder Fixture-Referenzen

## Beispiel Fuer Spaeteren Sicheren Consumer-Plan-Status

Zulaessig:

- `snapshot_payload_contract = snapshot version, overall status, collectors`
- `offline_unknown_error_states = offline, unknown, error, unsupported`
- `consumer_boundaries = no tokens, no secrets, no unsafe logging`
- `status = consumer_plan_ready`
- `handoff_to_bob = read-only offline consumer model only`

Nicht zulaessig:

- `call_local_api_now = true`
- `start_polling = true`
- `open_socket = true`
- kompletter Host- oder Payload-Dump

## Akzeptanz Fuer Bob

Ein spaeterer Bob-Slice darf nur ein isoliertes read-only Consumer-Planmodell in `src/live_system_health_local_api_consumer.py` und `tests/test_live_system_health_local_api_consumer.py` bauen, das Operator-Inputs bewertet und niemals Netzwerkaufrufe oder Host-Kommandos startet.

Wichtig:

- keine IO ausser read-only Artefakt-/Modelleingaben
- kein Netzwerk
- keine Host-Kommandos
- keine Sockets oder Polling-Aktivierung

## Nicht-Ziele Abschluss

Dieser Slice liefert nur die sichere Plan- und Vertragssprache fuer einen spaeteren lokalen System-Health-API-Consumer. Er ist kein Consumer-Deployment, keine API-Integration und keine Live-Freigabe.
