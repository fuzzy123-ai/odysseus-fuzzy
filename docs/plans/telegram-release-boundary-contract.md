# Telegram Release Boundary Contract

Stand: 2026-06-17

Status: **TLG0A Docs-Contract fuer das Gate `telegram_release_boundary`**

Quellen:

- `docs/plans/release-runtime-readiness-roadmap.md`
- `docs/plans/telegram-offline-smoke-plan-contract.md`
- `docs/plans/system-health-telegram-pull-status-contract.md`
- `docs/plans/live-telegram-status-dry-run-contract.md`

Dieser Contract definiert die Release-Grenze fuer Telegram vor externem `1.0.0`. Er beschreibt keine echten Netzwerkaufrufe, keine Sends, keine Bot-API-Nutzung und keine Runtime-Aktivierung. Der Slice friert nur ein, wie Telegram releasefaehig ausschliesslich ueber Secret-Rotation ausserhalb des Repos, Environment-only Loading, Offline-/Dry-Run-Evidence und einen spaeteren manuellen Live-Smoke mit Operator-Freigabe beschrieben werden darf.

## Purpose

`TLG0A` ist die operator-taugliche Release-Grenze fuer Telegram.

Der Contract soll beantworten:

- wann Telegram vor externem `1.0.0` ueberhaupt als sicher vorbereitet beschrieben werden darf
- wie mit einem bereits kompromittierten Bot-Secret umzugehen ist, ohne es erneut zu notieren
- welche Secret-, Dry-Run-, Send- und Rollback-Evidence fuer die Release-Grenze Pflicht ist
- welche Blocker sofort zum Stop fuehren
- wie Alice, Bob und Charlie die Gate-Sprache strikt ohne echte Secrets und ohne Live-Aktivierung halten

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keinen echten Token-Wert
- keine Chat-ID
- keine Bot-API-URL mit Secret
- keine Netzwerkaufrufe
- keine Sends
- keine Scheduler- oder Runtime-Aktivierung
- keine Plugin-Arbeit, keine Plugin-Imports, kein `setup()`, keine Plugin-Runtime
- keine Provider-, Export-/Import-/Rebuild- oder Host-Aktionen

## Release-Bedeutung Von Telegram

Die Section `telegram_release_meaning` soll die zentrale Leitplanke festhalten.

Releasefaehig vorbereitet bedeutet:

- ein kompromittiertes Secret wird als unbrauchbar behandelt
- ein neues Secret wird nur ausserhalb des Repos rotiert und verwaltet
- Secret-Loading bleibt env-only und operator-kontrolliert
- Offline-/Dry-Run-Evidence kommt vor jedem Live-Smoke
- Live-Smoke bleibt manuell, redigiert und ausdruecklich freigegeben

Nicht releasefaehig ist:

- persistiertes Secret im Repo oder in Doku
- Netzwerk oder Send als Default
- automatisches Live-Verhalten ohne Operator-Gate

## Pflicht-Evidence

Die Section `required_evidence` soll die minimalen Belege fuer dieses Release-Gate festziehen.

Pflicht-Evidence:

- `token_rotated_out_of_band`
- `token_not_persisted`
- `env_only_secret_loading`
- `dry_run_plan_recorded`
- `no_network_default`
- `no_send_default`
- `operator_live_smoke_required`
- `rollback_instruction_recorded`

### `token_rotated_out_of_band`

Ein kompromittiertes Secret gilt als unbrauchbar. Ein neues Secret darf nur ausserhalb des Repos und ausserhalb dieser Dokumentation rotiert werden.

### `token_not_persisted`

Das neue Secret darf nicht im Repo, in Doku, in Logs, in Fixtures, in Tests oder in Prompts stehen.

### `env_only_secret_loading`

Ein spaeteres Live-Gate darf Secrets nur aus lokaler Operator-Umgebung laden, nicht aus hart codierten Quellen.

### `dry_run_plan_recorded`

Telegram muss zuerst ueber einen tokenfreien Dry-Run- oder Offline-Plan beschrieben und belegt werden.

### `no_network_default`

Netzwerk darf fuer Telegram nicht standardmaessig aktiviert sein.

### `no_send_default`

Senden darf fuer Telegram nicht standardmaessig aktiviert sein.

### `operator_live_smoke_required`

Ein echter Live-Smoke darf nur nach bewusster Operator-Freigabe und nur redigiert stattfinden.

### `rollback_instruction_recorded`

Es muss eine klare Anweisung geben, wie Telegram nach einem fehlgeschlagenen oder unsicheren Live-Smoke wieder deaktiviert oder stillgelegt wird.

## Secret-Rotation Boundary

Die Section `secret_rotation_boundary` soll die Sicherheitsgrenze rund um kompromittierte Secrets festziehen.

Pflichtregeln:

- kompromittierte Secrets werden nicht weiterverwendet
- kompromittierte Secrets werden nicht wiederholt, zitiert oder archiviert
- neues Secret nur ausserhalb des Repos und ausserhalb dieses Chats
- jede spaetere Operator-Evidence muss redigiert und secret-frei bleiben

Wichtig:

- Secret-Rotation ist Pflicht, nicht optional
- dieses Dokument darf nie einen echten Secret-Wert enthalten

## Operator-Taugliche Gate-Regeln

Die Section `operator_facing_gate_rules` soll die Freigabeentscheidung auf kurze, pruefbare Regeln verdichten.

Pflichtregeln:

- erst Secret-Rotation ausserhalb des Repos
- dann tokenfreier Dry-Run
- dann optionaler manueller Live-Smoke
- kein Netzwerk und kein Send als Default
- jede Live-Nutzung bleibt operator-kontrolliert
- jede Evidence bleibt redigiert und ohne Rohsecret

## Verbotene Aktionen

Die Section `forbidden_actions` muss die harten Release-Blocker nennen.

Mindestens:

- `raw_token_persisted`
- `raw_token_logged`
- `token_in_tests`
- `token_in_docs`
- `token_in_automation_prompt`
- `network_enabled_by_default`
- `send_enabled_by_default`
- `plugin_scope_touched`
- `unsafe_secret_handling_enabled`

Wichtig:

- jede dieser Aktionen fuehrt zu `blocked`
- Plugin-Modul bleibt eingefroren und wird in diesem Gate nicht beruehrt

## Statussprache

Pflicht-Gate-ID:

- `telegram_release_boundary`

Pflicht-Statuswerte:

- `telegram_boundary_ready`
- `needs_secret_rotation`
- `blocked`
- `deferred`

### `telegram_boundary_ready`

Telegram kann als sicher vorbereitet beschrieben werden: rotiertes Secret ausserhalb des Repos, env-only Loading, Dry-Run zuerst, kein Netzwerk- oder Send-Default, manueller Live-Smoke nur mit Freigabe.

### `needs_secret_rotation`

Ein bereits kompromittiertes oder unklar behandeltes Secret blockiert jede ehrliche Release-Aussage, bis eine Rotation ausserhalb des Repos erfolgt ist.

### `blocked`

Mindestens ein harter Verstoss liegt vor, zum Beispiel persistiertes Secret, Token in Tests/Doku, Default-Send oder unsichere Secret-Behandlung.

### `deferred`

Die Gate-Entscheidung ist bewusst verschoben und bleibt ausserhalb dieses Slices offen.

## Alice Bob Charlie Roles

Die Section `alice_bob_charlie_roles` soll die sichere Aufteilung fuer dieses Release-Gate festhalten.

### Alice

Alice verantwortet:

- Nutzer- und Operator-Sprache
- Secret-Rotation- und Dry-Run-Erklaerung
- Release-Ehrlichkeit: vorbereitet ja, live nur manuell

### Bob

Bob verantwortet:

- ein isoliertes read-only Boundary-Modell oder Summary ueber Telegram-Secret-, Dry-Run- und Send-Gates
- Validierung der Pflicht-Evidence und Gate-Statuswerte
- keine echten Tokens, keine Netzwerkaufrufe, keine Sends

### Charlie

Charlie verantwortet:

- Scope-Kontrolle
- fokussierte Tests oder Checks gegen Secret-Leaks, Default-Network und Default-Send
- Stop-Entscheidung bei Secret- oder Scope-Verstoss

## Stop Rules

Die Section `stop_rules` muss das Abrutschen in falsche Release-Claims verhindern.

Mindestens:

- wenn ein echtes Secret irgendwo persistiert, geloggt oder zitiert wird: stoppen
- wenn Tests, Doku oder Automationsprompts ein Secret enthalten: stoppen
- wenn Netzwerk oder Send standardmaessig aktiv werden: stoppen
- wenn Plugin-Scope beruehrt wird: stoppen
- wenn Dry-Run fehlt oder Rollback-Anweisung fehlt: `blocked`
- wenn Secret-Rotation nicht ausserhalb des Repos bestaetigt ist: `needs_secret_rotation`

## Handoff To Bob

Die Section `handoff_to_bob` soll klar begrenzen, was Bob spaeter bauen darf.

Erlaubt:

- ein isoliertes read-only Telegram-Release-Boundary-Modell
- Validierung der Pflicht-Evidence
- Statusableitung fuer `telegram_boundary_ready`, `needs_secret_rotation`, `blocked`, `deferred`
- Tests mit rein redigierten, tokenfreien Fixtures

Nicht erlaubt:

- echte Tokens
- echte Chat-IDs
- Bot-API-URLs mit Secret
- Netzwerkaufrufe
- Sends
- Plugin-, Host- oder Runtime-Aktivierung

## Example Safe Boundary Reading

Zulaessig:

- `token_rotated_out_of_band = true`
- `token_not_persisted = true`
- `env_only_secret_loading = true`
- `dry_run_plan_recorded = true`
- `no_network_default = true`
- `no_send_default = true`
- `operator_live_smoke_required = true`
- `rollback_instruction_recorded = true`
- `status = telegram_boundary_ready`

Nicht zulaessig:

- `raw_token_persisted = true`
- `token_in_docs = true`
- `send_enabled_by_default = true`
- `network_enabled_by_default = true`

## Abschluss

Dieser Slice liefert nur die sichere Release-Sprache fuer die Telegram-Grenze. Er macht Secret-Rotation ausserhalb des Repos, env-only Secret-Handling, tokenfreien Dry-Run und manuell freigegebenen Live-Smoke releasefaehig lesbar, ohne irgendein Secret zu wiederholen, ohne Netzwerk-Default und ohne Plugin-Scope.
