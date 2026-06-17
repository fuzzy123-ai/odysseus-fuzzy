# Live Telegram Status Dry Run Contract

Stand: 2026-06-17

Status: **LIVE8A Docs-Contract fuer das Gate `live_telegram_status_dry_run_plan`**

Quellen:

- `docs/plans/live-system-health-local-api-consumer-contract.md`
- `docs/plans/system-health-telegram-pull-status-contract.md`
- `docs/plans/system-health-agent-interface-contract.md`
- `docs/plans/live-release-evidence-closeout-contract.md`

Dieser Contract definiert die Operator- und Nutzersprache fuer einen spaeteren Telegram-Status-Dry-Run im Offline-/Fixture-/Redaction-/Review-Modus. Der Dry-Run darf nur kompakte, sanitisierte Status-Payloads oder Fixtures lesen, um spaeter die Telegram-Statusform zu planen. Er startet keine echten Telegram-Tokens, keine Netzwerkaufrufe, keine Sends und keine Scheduler- oder Runtime-Aktivierung. Das Gate bleibt read-only und erzeugt kein externes Release-Go.

## Purpose

`LIVE8A` ist die Vorbereitung fuer einen spaeteren Telegram-Status-Plan nach Local API Consumer und Host-Agent-Plan.

Der Contract soll beantworten:

- wie ein spaeterer Telegram-Statusfluss nur als Offline-/Fixture-/Preview-Schicht gedacht ist
- welche Status-Payload-Grenzen gelten
- wie Tokens, Secrets und Chat-Identitaeten redigiert bleiben
- wie Operator-Review vor jeder spaeteren Telegram-Nutzung bestehen bleibt
- wie Alice, Bob und Charlie vor jeder echten Send- oder Polling-Aktivierung getrennt bleiben

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keine echten Telegram-Tokens
- keine Netzwerkaufrufe
- keine Sends
- keine Scheduler- oder Runtime-Aktivierung
- keine Provider-, Export-/Import-/Rebuild-, Nextcloud- oder Host-Aktionen
- kein externes `1.0`-Go

## Dry Run Scope And Status Payload Boundaries

Die Section `dry_run_scope_and_status_payload_boundaries` soll den erlaubten Umfang des spaeteren Dry-Runs begrenzen.

Erlaubt spaeter im Dry-Run:

- sanitisierte Status-Payloads lesen
- Fixture- oder Preview-Daten verdichten
- Telegram-Statusformen im Plan beschreiben
- Blocker, Unknown- oder Offline-Zustaende strukturieren

Nicht erlaubt:

- echte Token-Nutzung
- echte Chat- oder Bot-Sends
- Polling oder Webhook-Aktivierung
- direkte Host-, Netzwerk- oder Socket-Aktionen

Wichtig:

- der Dry-Run bleibt strikt preview-orientiert
- selbst gueltige Status-Payloads duerfen keinen realen Telegram-Pfad ausloesen

## Token And Secret Rules

Die Section `token_and_secret_rules` muss die Geheimnisgrenzen hart setzen.

Mindestens:

- keine echten Tokens
- keine Secrets
- keine rohen Chat-IDs
- keine privaten Pfade
- keine Token-, Session- oder Credential-Spuren in Artefakten

Wichtig:

- fehlende oder unsichere Redaktion fuehrt zu `blocked`
- dieser Slice arbeitet nur mit redigierten oder fiktiven IDs

## Redaction And Logging Rules

Die Section `redaction_and_logging_rules` soll die spaetere Logging- und Preview-Sprache begrenzen.

Zulaessig:

- kompakte Statuswerte
- redigierte oder fiktive Chat-Referenzen
- kurze Ursachen- und Next-Action-Hinweise
- Fixture-Hinweise

Nicht zulaessig:

- rohe Logs
- komplette Payload-Dumps
- private IDs
- sensible Debug-Ausgaben

Wichtig:

- Logging bleibt kompakt und redigiert
- kein Rohdump als Standard-Preview

## Offline Fixture Preview Flow

Die Section `offline_fixture_preview_flow` soll beschreiben, wie ein spaeterer Preview-Flow ohne Netzwerk aussehen darf.

Mindestens:

- sanitisierte Fixture lesen
- Dry-Run-Status ableiten
- Preview-Text ohne Send erzeugen
- Offline-, Unknown- oder Error-Sicht erklaeren

Wichtig:

- keine Verbindung zu Telegram
- kein Polling
- kein Retry-Loop

## Operator Approval Flow

Die Section `operator_approval_flow` soll beschreiben, wie spaeter vor jeder echten Telegram-Nutzung ein Mensch dazwischen bleiben muss.

Mindestens:

- Preview- oder Fixture-Ergebnis lesen
- Redaktions- und Geheimnisregeln pruefen
- blocked oder deferred Gruende bestaetigen
- nur dann ueber spaetere echte Telegram-Gates nachdenken, wenn kein Dry-Run-Grenzbruch sichtbar ist

Wichtig:

- ohne Operator-Approval bleibt alles Dry-Run-only
- kein automatischer Send aus Preview-Ergebnissen

## Blocked Conditions

Die Section `blocked_conditions` muss die harten No-Go-Bedingungen fuer Telegram-Dry-Run festsetzen.

Mindestens:

- echte Tokens oder Secrets tauchen auf
- rohe Chat-IDs tauchen auf
- Netzwerkaufruf wird gefordert
- Send, Polling, Webhook oder Scheduler-Aktivierung wird impliziert
- Host-, Provider-, Export-/Import-/Rebuild-, Nextcloud- oder Socket-Aktionen werden verlangt

Wichtig:

- diese Bedingungen fuehren zu `blocked`
- ein Telegram-Dry-Run darf nie in einen halb-live Zustand rutschen

## Alice Bob Charlie Roles

Die Section `alice_bob_charlie_roles` soll die Aufteilung fuer dieses Gate klar ziehen.

### Alice

Alice verantwortet:

- Nutzer- und Operator-Sprache
- Redaction- und Boundary-Texte
- Dry-Run- und Stop-Regeln

### Bob

Bob verantwortet:

- isoliertes read-only Telegram-Status-Planmodell
- `src/live_telegram_status_dry_run.py`
- `tests/test_live_telegram_status_dry_run.py`
- reine Bewertung von Fixture-, Preview- und Redaction-Signalen

Wichtig:

- Bob darf keine Tokens laden
- Bob darf keine Netzwerkaufrufe starten
- Bob darf keine Sends, Polling- oder Scheduler-Aktivierung ausloesen

### Charlie

Charlie verantwortet:

- Scope-Kontrolle
- Testauswahl fuer read-only Modelle
- Worktree- und Push-Pruefung
- Stop-Entscheidung bei riskanter Scope-Verschiebung oder unklaren Redaktionsgrenzen

## Stop Rules

Die Section `stop_rules` muss das Abrutschen in echte Telegram- oder Runtime-Aktionen verhindern.

Mindestens:

- wenn echte Tokens, Secrets oder rohe Chat-IDs auftauchen: stoppen
- wenn Netzwerk, Send, Polling oder Webhook gefordert wird: stoppen
- wenn Scheduler oder andere Runtime-Aktivierung impliziert wird: stoppen
- wenn Host-, Provider-, Export-/Import-/Rebuild-, Nextcloud- oder Socket-Aktionen verlangt werden: stoppen
- wenn Payload-, Redaction- oder Approval-Grenzen unklar sind: `needs_operator_review` oder `blocked`, nicht `ready`

## Handoff To Bob

Die Section `handoff_to_bob` soll klar machen, was Bob auf dieser Basis bauen darf.

Erlaubt:

- read-only Telegram-Status-Planmodell
- Bewertung von Offline-/Fixture-/Preview-Signalen
- Statusableitung fuer `dry_run_plan_ready`, `needs_operator_review`, `blocked`, `deferred`
- Tests mit mockten Status-Payloads

Nicht erlaubt:

- echte Telegram-APIs
- Tokens
- Netzwerkaufrufe
- Sends oder Polling

Pflicht-Gate-ID:

- `live_telegram_status_dry_run_plan`

Pflicht-Statuswerte:

- `dry_run_plan_ready`
- `needs_operator_review`
- `blocked`
- `deferred`

## Example Safe Dry Run Status

Zulaessig:

- `status_payload = sanitized snapshot summary only`
- `preview_flow = fixture -> preview text -> manual review`
- `token_and_secret_rules = no tokens, no raw chat ids`
- `status = dry_run_plan_ready`
- `handoff_to_bob = read-only telegram preview model only`

Nicht zulaessig:

- `send_now = true`
- `start_polling = true`
- `load_bot_token = true`
- kompletter Chat- oder Payload-Dump

## Akzeptanz Fuer Bob

Ein spaeterer Bob-Slice darf nur ein isoliertes read-only Telegram-Status-Planmodell in `src/live_telegram_status_dry_run.py` und `tests/test_live_telegram_status_dry_run.py` bauen, das Fixture-, Preview- und Redaction-Signale bewertet und niemals Telegram-Sends oder Netzwerkaufrufe startet.

Wichtig:

- keine IO ausser read-only Artefakt-/Modelleingaben
- kein Netzwerk
- keine Tokens
- keine Polling- oder Scheduler-Aktivierung

## Abschluss

Dieser Slice liefert nur die sichere Plan- und Vertragssprache fuer einen spaeteren Telegram-Status-Dry-Run. Er ist keine Bot-Integration, keine Notification-Runtime und keine Live-Freigabe.
