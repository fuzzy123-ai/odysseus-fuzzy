# Live Release Evidence Closeout Contract

Stand: 2026-06-17

Status: **LIVE0A Docs-Contract fuer den Start der Phase `Live Integration & Plugin Enablement`**

Quellen:

- `docs/plans/1.0-evidence-release-checklist.md`
- `docs/plans/1.0-manual-release-evidence-runbook.md`
- `docs/plans/provider-proof-operator-runbook.md`
- `docs/plans/export-import-rebuild-operator-runbook.md`
- `docs/plans/live-provider-proof-run-contract.md`

Dieser Contract definiert die Abschluss- und Freigabesprache fuer den Start der Phase `Live Integration & Plugin Enablement`. Er klaert, was intern bereits release-candidate-ready ist, welche Gates ein externes `1.0` weiter blockieren und welche naechsten sicheren Slices in `LIVE1` und `LIVE2` erlaubt sind. Der Slice schaltet nichts live, fuehrt keine Provider-, Export-, Host-, Telegram- oder Netzwerkaktionen aus und erzeugt kein externes Release-Go.

## Purpose

`LIVE0` ist die Bruecke zwischen internem RC-Status und spaeterer Live-Integration.

Der Contract soll beantworten:

- was aktuell als interne RC-Evidence gilt
- welche externen Go-Blocker noch offen sind
- welche Entry Gates fuer die neue Live-Phase gelten
- wie Alice, Bob und Charlie in der Closeout-Phase getrennt arbeiten
- welche Folge-Slices sicher erlaubt sind
- welche Stop-Regeln ein versehentliches externes Go oder Runtime Enablement verhindern

## Leitregel

`LIVE0` ist kein Runtime Enablement und kein externes Release-Go.

Das bedeutet:

- intern release-candidate-ready darf nicht als externes `1.0` verkauft werden
- offene manuelle Gates bleiben explizit `No-Go`
- Live-Phase bedeutet hier nur vorbereitete Freigabesprache und sichere Folge-Slices

## Current Internal RC Evidence

Die Section `current_internal_rc_evidence` soll den dokumentierten internen Stand knapp zusammenfassen.

Mindestens:

- automatisierte REL1-Gates frisch gruen
- Memory-first / Obsidian Lens intern gruen
- M6 Model Router / DeepSeek Graceful Degradation intern gruen
- `0.13.x` Memory Scale Foundation abgeschlossen
- `0.14.x` Lightweight Memory Maintenance abgeschlossen
- Fresh Install und Upgrade Path manuell belegt
- Known Limits Review manuell belegt

Wichtig:

- diese Section beschreibt nur internen RC-Stand
- sie ersetzt keine offenen externen Gates

## External Go Blockers

Die Section `external_go_blockers` muss die noch offenen externen Freigabegates klar benennen.

Pflichtfelder:

- `provider_fallback_answer_run`
- `test_vault_export_import_rebuild`

Bedeutung:

- `provider_fallback_answer_run` bleibt offen, bis Default-Modell, Fallback-Modell und lokales/DeepSeek-Szenario manuell mit echtem Antwortlauf belegt oder sauber als nicht verfuegbar markiert sind
- `test_vault_export_import_rebuild` bleibt offen, bis Export/Import/Rebuild gegen einen kleinen Test-Vault manuell mit Sicherheitspruefung belegt ist

Wichtig:

- beide Gates blockieren externes `1.0`
- kein automatisierter Testlauf darf diese Gates still auf `Go` setzen

## Live Phase Entry Gates

Die Section `live_phase_entry_gates` soll beschreiben, was `LIVE1` und `LIVE2` ueberhaupt sicher tun duerfen.

Mindestens:

- nur read-only oder docs-/modellzentrierte Folge-Slices
- keine echten Provider-Aufrufe
- keine echten Export-/Import-/Rebuild-Aktionen
- keine Host-, Telegram- oder Netzwerkaktionen
- keine Tokens, Secrets oder privaten Pfade in Artefakten
- Charlie prueft Scope, Worktree und Stop-Regeln vor jedem Folge-Slice

Wichtig:

- Entry Gates sind Startregeln fuer sichere Folgearbeit
- sie sind keine Freigabe fuer Live-Runtime oder externes Release

## Alice Bob Charlie Roles

Die Section `alice_bob_charlie_roles` soll die Aufteilung fuer die Closeout-Phase klar machen.

### Alice

Alice verantwortet:

- Operator- und Nutzertexte
- Runbooks
- Go-/No-Go-Sprache
- Known-Limits- und Freigabe-Formulierungen

### Bob

Bob verantwortet:

- read-only Checker
- Modelle
- statische oder fokussierte Tests
- Closeout-Helfer ohne echte Provider-, Export-, Host- oder Netzwerkaktionen

Wichtig:

- Bob darf keine echten Provider-, Export-/Import- oder Host-Aktionen ausfuehren

### Charlie

Charlie verantwortet:

- Scope-Kontrolle
- Testauswahl und Testlauf
- Worktree-Pruefung
- Push
- Stop-Entscheidung bei unklaren Gates oder riskanter Scope-Verschiebung

## Allowed Next Slices

Die Section `allowed_next_slices` soll nur sichere Folge-Slices erlauben.

Typische Inhalte:

- read-only Closeout-Checker
- manuelle Evidence-Indexe oder Handoff-Hilfen
- Modell- und Contract-Slices fuer Live-Phase-Gates
- klar begrenzte Support-Slices fuer spaetere manuelle Operator-Laeufe

Nicht erlaubt:

- echte Provider-Live-Laeufe
- echte Export-/Import-/Rebuild-Laeufe
- echte Host-Agent-, Telegram- oder Netzwerk-Aktivierung

## Stop Rules

Die Section `stop_rules` muss das versehentliche Abrutschen in ein externes Go oder Runtime Enablement verhindern.

Mindestens:

- wenn `provider_fallback_answer_run` nicht belegt ist: kein externes `1.0`-Go
- wenn `test_vault_export_import_rebuild` nicht belegt ist: kein externes `1.0`-Go
- wenn Secrets, Tokens, private Pfade oder rohe Logs auftauchen: stoppen
- wenn ein Slice echte Provider-, Export-, Host-, Telegram- oder Netzwerkaktionen verlangt: stoppen oder separaten manuellen Operator-Flow verlangen
- wenn Worktree oder Scope unklar sind: Charlie stoppt

## No-Secrets und No-Raw-Logs

Dieser Closeout-Contract und alle Folge-Artefakte duerfen nicht enthalten:

- Secrets
- Tokens
- private Pfade
- rohe Logs
- komplette Providerantworten

Zulaessig sind:

- kompakte Statuslabels
- kurze Go-/No-Go-Hinweise
- kurze Evidence- und Runbook-Referenzen

## Beispiel fuer spaeteren sicheren Closeout-Status

Zulaessig:

- `current_internal_rc_evidence = automated gates green, fresh install go, upgrade go`
- `external_go_blockers = provider_fallback_answer_run, test_vault_export_import_rebuild`
- `live_phase_entry_gates = read-only only until manual evidence lands`
- `allowed_next_slices = closeout checker, evidence index, support model`
- `stop_rules = no external go without both manual gates`

Nicht zulaessig:

- `external_1_0_go = true`
- `provider gate implicitly passed`
- `run export/import now automatically`
- kompletter Provider- oder Logdump

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf nur einen isolierten read-only Closeout-Checker oder ein Summary-Modell bauen, das externes Go nur bei belegten manuellen Gates erlaubt.

Zulaessige Inputs:

- `1.0 Evidence Release Checklist`
- manuelle Evidence-Modelle oder Snapshots
- Operator-Runbooks
- dokumentierte Gate-Statuswerte

Wichtig:

- keine IO ausser read-only Artefakt-/Modelleingaben
- kein Netzwerk
- keine Host-Kommandos
- keine echten Provider- oder Export-Aktionen

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- kein externes `1.0`-Release-Go
- kein Runtime Enablement
- keine Provider-, Export-/Import-, Host-, Telegram- oder Netzwerk-Aktivierung
- keine Push- oder Deployment-Schritte
- keine erfundene manuelle Evidence

Er legt nur fest, wie die neue Live-Phase sprachlich und prozessual sauber startet, ohne die letzten offenen externen Release-Gates zu verwischen.
