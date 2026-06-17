# Telegram Offline Smoke Plan Contract

Stand: 2026-06-17

Status: **TLG1A Docs-Contract fuer das Gate `telegram_offline_smoke_plan`**

Quellen:

- `docs/plans/telegram-release-boundary-contract.md`
- `docs/plans/release-runtime-readiness-roadmap.md`
- `docs/plans/live-telegram-status-dry-run-contract.md`

Dieser Contract definiert den Release-Plan fuer einen Telegram Offline-Smoke vor jedem echten Live-Smoke. Er beschreibt keine Bot-API-Aufrufe, keine Netzwerkverbindung, keine Sends und keine Runtime-Aktivierung. Der Slice friert nur ein, wie Operatoren mit redigierter Evidence pruefen koennen, dass Secret-Rotation, Environment-Loading, Dry-Run-Payload, Send-Disablement, Network-Disablement und Rollback bereit sind, bevor spaeter manuell ein neuer rotierter Token ausserhalb des Repos verwendet wird.

## Purpose

`TLG1A` ist der operator-taugliche Offline-Smoke-Plan fuer Telegram vor jedem Live-Schritt.

Der Contract soll beantworten:

- welche tokenfreien und redigierten Belege vor einem spaeteren Live-Smoke vorliegen muessen
- wie Dry-Run-Payload, Network-Disablement und Send-Disablement offline geprueft werden
- wie Operator-Bestaetigung und Rollback-Denke dokumentiert bleiben
- welche Blocker sofort zum Stop fuehren
- wie Alice, Bob und Charlie die Smoke-Sprache ohne echte Secrets, ohne Bot-Calls und ohne Netzwerknutzung halten

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keinen echten Token-Wert
- keine Chat-ID
- keine Bot-API-URL mit Secret
- keine Netzwerkaufrufe
- keine Sends
- keine Plugin-Arbeit, keine Plugin-Imports, kein `setup()`, keine Plugin-Runtime
- keine Provider-, Export-/Import-/Rebuild- oder Host-Aktionen
- keinen echten Live-Smoke

## Release-Bedeutung Des Offline-Smokes

Die Section `telegram_offline_smoke_meaning` soll die zentrale Leitplanke festhalten.

Releasefaehig vorbereitet bedeutet:

- ein redigierter Offline-Smoke-Plan existiert
- Secret-Rotation bleibt ausserhalb des Repos
- die verwendete Env-Variable ist dokumentiert, aber ohne Secret-Wert
- Dry-Run-Payload ist aufgezeichnet und tokenfrei
- Netzwerk und Send bleiben standardmaessig deaktiviert
- ein spaeterer Live-Smoke bleibt bewusst verschoben, bis manuelles Go vorliegt

Nicht releasefaehig ist:

- ein Plan, der bereits echte Netzwerk- oder Send-Schritte voraussetzt
- eine Doku, die Rohsecrets oder Rohchat-IDs speichert
- eine Offline-Pruefung ohne Rollback- oder Operator-Check

## Pflicht-Evidence

Die Section `required_evidence` soll die minimalen Belege fuer dieses Release-Gate festziehen.

Pflicht-Evidence:

- `redacted_secret_reference_recorded`
- `env_var_name_recorded`
- `dry_run_payload_recorded`
- `send_disabled_recorded`
- `network_disabled_recorded`
- `operator_confirmation_required`
- `rollback_command_documented`
- `live_smoke_deferred_until_manual_go`

### `redacted_secret_reference_recorded`

Das Secret darf nur als redigierte Referenz oder Betreiberhinweis erscheinen, nie als Wert.

### `env_var_name_recorded`

Die fuer spaeteres Operator-Setup benoetigte Environment-Variable darf benannt werden, solange kein Secret-Wert dokumentiert wird.

### `dry_run_payload_recorded`

Eine tokenfreie, redigierte Dry-Run-Payload oder Preview-Ausgabe muss beschrieben sein.

### `send_disabled_recorded`

Der Offline-Smoke muss festhalten, dass Send-Verhalten standardmaessig deaktiviert bleibt.

### `network_disabled_recorded`

Der Offline-Smoke muss festhalten, dass keine Netzwerknutzung standardmaessig aktiv ist.

### `operator_confirmation_required`

Vor jedem spaeteren Live-Smoke ist eine bewusste manuelle Bestaetigung noetig.

### `rollback_command_documented`

Es muss eine klare Ruecknahme- oder Disable-Anweisung fuer den spaeteren Live-Pfad geben.

### `live_smoke_deferred_until_manual_go`

Der Offline-Smoke ist nur release-ehrlich, wenn der echte Live-Smoke ausdruecklich auf spaeter und nur mit manuellem Go verschoben bleibt.

## Operator-Taugliche Smoke-Regeln

Die Section `operator_facing_smoke_rules` soll die Freigabeentscheidung auf kurze, pruefbare Regeln verdichten.

Pflichtregeln:

- Secret bleibt rotiert und ausserhalb des Repos
- nur redigierte Referenz statt Secret-Wert
- Dry-Run-Payload bleibt tokenfrei
- Netzwerk bleibt deaktiviert
- Send bleibt deaktiviert
- Live-Smoke bleibt manuell und spaeter
- Rollback-Schritt bleibt lesbar

## Verbotene Aktionen

Die Section `forbidden_actions` muss die harten Release-Blocker nennen.

Mindestens:

- `raw_token_persisted`
- `raw_token_logged`
- `raw_chat_id_persisted`
- `network_enabled`
- `send_enabled`
- `bot_api_called`
- `plugin_scope_touched`
- `unsafe_secret_handling_enabled`

Wichtig:

- jede dieser Aktionen fuehrt zu `blocked`
- Plugin-Modul bleibt eingefroren und wird in diesem Gate nicht beruehrt

## Statussprache

Pflicht-Gate-ID:

- `telegram_offline_smoke_plan`

Pflicht-Statuswerte:

- `telegram_offline_smoke_ready`
- `needs_offline_smoke_evidence`
- `blocked`
- `deferred`

### `telegram_offline_smoke_ready`

Der Offline-Smoke kann als sicher vorbereitet beschrieben werden: redigierte Secret-Referenz, dokumentierte Env-Variable, Dry-Run-Payload, deaktiviertes Netzwerk und Send, klarer Rollback-Schritt, Live-Smoke bewusst verschoben.

### `needs_offline_smoke_evidence`

Mindestens eine Pflicht-Evidence zu Dry-Run-Payload, Disablement, Rollback oder Operator-Bestaetigung fehlt noch oder ist zu unscharf.

### `blocked`

Mindestens ein harter Verstoss liegt vor, zum Beispiel Rohsecret, Rohchat-ID, Bot-API-Aufruf, aktives Netzwerk oder aktives Send-Verhalten.

### `deferred`

Die Gate-Entscheidung ist bewusst verschoben und bleibt ausserhalb dieses Slices offen.

## Alice Bob Charlie Roles

Die Section `alice_bob_charlie_roles` soll die sichere Aufteilung fuer dieses Release-Gate festhalten.

### Alice

Alice verantwortet:

- Nutzer- und Operator-Sprache
- Offline-Smoke-Reihenfolge und Redaction-Regeln
- Release-Ehrlichkeit: offline bereit, live spaeter nur manuell

### Bob

Bob verantwortet:

- ein isoliertes read-only Offline-Smoke-Planmodell oder Summary
- Validierung der Pflicht-Evidence und Gate-Statuswerte
- keine echten Tokens, keine Chat-IDs, keine Bot-API-Aufrufe, keine Sends

### Charlie

Charlie verantwortet:

- Scope-Kontrolle
- fokussierte Checks gegen Rohsecret-, Rohchat-ID-, Network- und Send-Verstoesse
- Stop-Entscheidung bei Secret- oder Scope-Verstoss

## Stop Rules

Die Section `stop_rules` muss das Abrutschen in falsche Release-Claims verhindern.

Mindestens:

- wenn Rohsecret oder Rohchat-ID persistiert, geloggt oder zitiert werden: stoppen
- wenn Netzwerk oder Send aktiv statt deaktiviert beschrieben werden: stoppen
- wenn Bot-API-Aufruf oder echter Live-Smoke in den Offline-Slice gezogen wird: stoppen
- wenn Plugin-Scope beruehrt wird: stoppen
- wenn Dry-Run-Payload, Operator-Bestaetigung oder Rollback-Schritt fehlen: `needs_offline_smoke_evidence`

## Handoff To Bob

Die Section `handoff_to_bob` soll klar begrenzen, was Bob spaeter bauen darf.

Erlaubt:

- ein isoliertes read-only Telegram-Offline-Smoke-Planmodell
- Validierung der Pflicht-Evidence
- Statusableitung fuer `telegram_offline_smoke_ready`, `needs_offline_smoke_evidence`, `blocked`, `deferred`
- Tests mit rein redigierten, tokenfreien und netzwerkfreien Fixtures

Nicht erlaubt:

- echte Tokens
- echte Chat-IDs
- Bot-API-URLs mit Secret
- Netzwerkaufrufe
- Sends
- Plugin-, Host- oder Runtime-Aktivierung

## Example Safe Offline Smoke Reading

Zulaessig:

- `redacted_secret_reference_recorded = true`
- `env_var_name_recorded = true`
- `dry_run_payload_recorded = true`
- `send_disabled_recorded = true`
- `network_disabled_recorded = true`
- `operator_confirmation_required = true`
- `rollback_command_documented = true`
- `live_smoke_deferred_until_manual_go = true`
- `status = telegram_offline_smoke_ready`

Nicht zulaessig:

- `raw_token_persisted = true`
- `raw_chat_id_persisted = true`
- `network_enabled = true`
- `send_enabled = true`
- `bot_api_called = true`

## Abschluss

Dieser Slice liefert nur die sichere Release-Sprache fuer einen Telegram Offline-Smoke-Plan. Er macht redigierte Secret-Referenz, Env-Variable, Dry-Run-Payload, Disablement von Netzwerk und Send sowie den spaeteren manuellen Live-Smoke releasefaehig lesbar, ohne irgendein Secret, eine Chat-ID oder einen Bot-Call zu verwenden.
