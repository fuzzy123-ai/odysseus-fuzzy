# Provider Proof Operator Runbook

Stand: 2026-06-17

Status: **REL40A operator-taugliches Runbook fuer den offenen Provider Proof**

Quellen:

- `docs/plans/1.0-evidence-release-checklist.md`
- `docs/plans/1.0-manual-release-evidence-runbook.md`

Dieses Runbook beschreibt den noch offenen manuellen Provider Proof fuer `1.0.0`. Es ist bewusst keine automatische Testanweisung und kein Ersatz fuer echte Beobachtung. Ziel ist, Default-Modell, Fallback-Modell und lokales oder DeepSeek-Szenario nachvollziehbar zu belegen oder sauber als nicht verfuegbar zu markieren, ohne Secrets, sensible Prompts oder komplette Providerantworten zu loggen.

## Ziel

Der Provider Proof ist manuelle Release-Evidence.

Er ist:

- kein Unit-Test
- kein automatischer Go-Generator
- keine Einladung, echte Secrets in Doku oder Logs zu kopieren

Ein externes `1.0.0` bleibt `No-Go`, bis dieser manuelle Gate-Lauf entweder erfolgreich dokumentiert oder sauber als weiterhin offen markiert ist.

## No-Go-Hinweis

Dieses Runbook erzeugt selbst kein `Go`.

Ein `Go` entsteht erst, wenn:

- der Lauf wirklich beobachtet wurde
- die Evidence mit Datum, Commit, Ergebnis und Blockern eingetragen wurde
- keine Stop-Regel ausgelöst hat

## Voraussetzungen

Vor dem Lauf muss mindestens folgendes klar sein:

- Server laeuft
- authentifizierte Session ist verfuegbar
- Query-Index ist `ready` oder bewusst als `nicht ready` dokumentiert
- Ziel-Commit auf `fuzzy/dev` ist bekannt
- keine sensiblen Quellen oder Prompts werden fuer den Proof verwendet

## Sichere Datenerfassung

Nie loggen oder in Doku kopieren:

- API Keys
- Bearer Tokens
- Session Secrets
- Prompts mit sensiblen Daten
- komplette Providerantworten
- vollstaendige Antworttexte mit moeglich sensiblen Quellen

Nur festhalten:

- Datum
- Commit
- verwendeter Modus
- beobachtete technische Felder
- harmlose Testfrage
- kompakte Beobachtung
- Go, Partial oder No-Go

## Empfohlene harmlose Testfrage

Die Testfrage soll:

- keine sensiblen Daten enthalten
- keine produktiven Geheimnisse referenzieren
- fuer Default-, Fallback- und lokales Szenario gleichartig sein

Beispiel:

`Worum geht es in diesem Testeintrag?`

Oder:

`Nenne den Titel der harmlosen Testnotiz.`

Wenn kein sicherer Testeintrag existiert:

- Lauf stoppen
- erst kleinen Test-Vault oder harmlose Testquelle bereitstellen

## Vorbereitung

Vor jedem Teilpfad kurz dokumentieren:

- Branch
- Commit
- Query-Index-Status
- aktiver Provider- oder Endpoint-Kontext
- ob echte lokale Modelle oder DeepSeek aktuell ueberhaupt verfuegbar sind

## Schrittfolge fuer Default-Modell

## 1. Status pruefen

Statusroute aufrufen und kompakt notieren:

- Modellstatus
- Endpoint-Status
- ob ein Query- oder Memory-Pfad grundsaetzlich verfuegbar wirkt

Nicht kopieren:

- rohe Secrets aus Config oder Responses

## 2. Modell-/Endpoint-/Role-Kontext festhalten

Nur harmlose Metadaten erfassen:

- `selected_model`
- `selected_endpoint_id`
- `selected_role`, falls sichtbar
- relevante Warnings

## 3. Harmlose Testfrage stellen

Eine harmlose Testfrage gegen den Memory- oder Query-Pfad stellen.

Zu erfassen:

- wurde geantwortet
- `answer_mode`
- `selected_model`
- `selected_endpoint_id`
- `fallback_reason`
- `model_capability_warnings`

Nicht erfassen:

- komplette Providerantwort
- komplette Quellenliste

## 4. Ergebnis bewerten

`Go`, wenn:

- Antwortpfad funktioniert
- Modellwahl nachvollziehbar ist
- keine unerwarteten Provider auftauchen

`Partial`, wenn:

- Antwort kommt, aber Warnings oder Indexlage nicht sauber erklaerbar sind

`No-Go`, wenn:

- unerwarteter Provider benutzt wird
- Antwortpfad unklar ist
- Serverlogs rot laufen

## Schrittfolge fuer Fallback

## 1. Primaeren Provider kontrolliert unbrauchbar machen

Nur kontrolliert und reversibel:

- primaeren Provider temporär nicht verfuegbar machen
- oder den Fallback-Lauf bewusst als aktuell nicht verfuegbar markieren, wenn ein kontrollierter Test nicht sicher oder nicht zulaessig ist

Wichtig:

- keine produktiven Secrets loeschen oder offenlegen
- keine dauerhafte Konfigurationsschaedigung erzeugen

## 2. Harmlose Testfrage erneut stellen

Wieder dieselbe harmlose Frage verwenden.

Zu erfassen:

- ob Fallback ueberhaupt einsetzt
- `fallback_reason`
- `selected_model`
- `selected_endpoint_id`
- relevante Warnings

## 3. Ergebnis bewerten

`Go`, wenn:

- Fallback aktiv und erklaerbar ist
- `fallback_reason` zur beobachteten Lage passt

`Partial`, wenn:

- Fallback absichtlich nicht verfuegbar ist und das sauber dokumentiert wurde

`No-Go`, wenn:

- Fallback-Verhalten nicht erklaerbar ist
- anderer als erwarteter Provider auftaucht
- keine klare technische Spur fuer das beobachtete Verhalten existiert

## Schrittfolge fuer lokal oder DeepSeek

## 1. Lokalen oder DeepSeek-Pfad identifizieren

Nur wenn wirklich vorhanden:

- lokales Modell
- DeepSeek-Endpoint

Wenn keiner verfuegbar ist:

- sauber als `nicht verfuegbar` dokumentieren
- kein Schein-Go erzeugen

## 2. Harmlose Testfrage stellen

Wieder dieselbe harmlose Frage verwenden.

Zu erfassen:

- ob Antwortpfad funktioniert
- `answer_mode`
- `selected_model`
- `selected_endpoint_id`
- Warnings

## 3. Ergebnis bewerten

`Go`, wenn:

- lokaler oder DeepSeek-Pfad real antwortet
- Modell- und Endpoint-Auswahl nachvollziehbar ist

`Partial`, wenn:

- Szenario aktuell nicht verfuegbar ist, aber sauber und ehrlich dokumentiert wurde

`No-Go`, wenn:

- lokaler oder DeepSeek-Pfad behauptet wird, aber nicht belegbar ist
- externer Provider statt lokalem Pfad verwendet wird, ohne dass dies erklaert ist

## Ergebnis-Matrix

## Go

`Go` fuer den Provider Proof nur, wenn:

- Default-Modell real belegt ist
- Fallback real belegt oder sauber als kontrolliert nicht verfuegbar markiert ist
- lokales oder DeepSeek-Szenario real belegt oder sauber als nicht verfuegbar markiert ist
- keine Stop-Regel ausgelöst wurde

## Partial

`Partial`, wenn:

- ein Teilpfad nicht real ausgefuehrt werden konnte, aber ehrlich dokumentiert wurde
- Indexlage oder Endpoint-Verfuegbarkeit aktuell eingeschraenkt ist
- keine Aussage als vollstaendiges `Go` verkauft wird

## No-Go

`No-Go`, wenn:

- Providerverhalten nicht erklaerbar ist
- Fallback nicht nachvollziehbar ist
- unerwartete externe Provider oder rote Serverlogs auftauchen
- sensible Daten oder Secrets im Lauf sichtbar werden

## Copy/Paste Evidence-Block

Diesen Block fuer `docs/plans/1.0-manual-release-evidence-log.md` verwenden:

```text
Gate: Provider Proof
Datum:
Tester:
Branch: fuzzy/dev
Commit:
Umgebung:
Query-Index: ready | not-ready | unclear
Ergebnis: Go | Partial | No-Go
Blocker:
Evidence-Link:
Kurznotiz:
- Default-Modell geprueft: ja | nein
- selected_model:
- selected_endpoint_id:
- selected_role:
- answer_mode:
- fallback_reason:
- model_capability_warnings:
- Fallback real geprueft: ja | nein
- Fallback bewusst nicht verfuegbar markiert: ja | nein
- lokales/DeepSeek-Szenario real geprueft: ja | nein
- lokales/DeepSeek-Szenario bewusst nicht verfuegbar markiert: ja | nein
- unerwarteter Provider gesehen: ja | nein
- sensitive Daten im Prompt/Log vermieden: ja | nein
```

## Stop-Regeln

Sofort stoppen und nicht als `Go` dokumentieren, wenn:

- Secrets sichtbar werden
- Query-Index unklar ist und nicht sauber markiert werden kann
- sensitive Quelle verwendet werden muesste
- unerwarteter Provider auftaucht
- Fallback nicht erklaerbar ist
- Serverlogs rot laufen

## Nachbereitung

Nach jedem Teilpfad:

- nur kompakte Beobachtungen notieren
- keine Geheimnisse in Screenshots oder Copy/Paste-Blöcken lassen
- veraenderte Provider-Konfiguration wieder in sicheren Ausgangszustand bringen

## Nicht-Ziele

Dieses Runbook fuehrt bewusst nicht aus:

- keine automatische Testfreigabe
- keine Provider-Umbauten
- keine RAG- oder Query-Code-Aenderung
- keine Secret-Offenlegung
- keinen Eintrag in das echte Evidence-Log ohne echten beobachteten Lauf

Das Runbook beschreibt nur, wie der offene manuelle Provider Proof sicher und nachvollziehbar belegt werden soll.
