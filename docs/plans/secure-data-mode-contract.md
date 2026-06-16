# Secure Data Mode Contract

Stand: 2026-06-16

Status: **SEC1 UX-/Policy-Vertrag fuer Secure Data Mode**

Quellen:

- `docs/plans/unified-odysseus-roadmap.md`

Dieser Vertrag definiert die Produkt-, UX- und Policy-Regeln fuer Secure Data Mode als Eigenschaft eines Chats oder Threads. Der Slice fuehrt bewusst keine Backend-, Runtime-, Frontend-, Test-, Migration-, Verschluesselungs- oder Telegram-Code-Aenderungen aus. Er friert nur Modusregeln, Nutzerzustaende, Blockierlogik, lokale Modellpflicht und Akzeptanzkriterien ein, damit spaetere `SEC2`-, `SEC3`- und `SEC4`-Slices ohne Policy-Mehrdeutigkeit umgesetzt werden koennen.

## Produktentscheidung

Secure Mode ist immutable pro Chat oder Thread.

Das bedeutet:

- ein normaler Chat bleibt normal
- ein sicherer Chat bleibt sicher
- es gibt keinen Toggle, der einen laufenden Chat spaeter in Secure Mode umwandelt
- es gibt keinen Toggle, der einen Secure Chat spaeter in einen normalen Chat zurueckschaltet

## Leitregel

Sensible Daten duerfen nicht durch spaetes Umschalten in einen bereits laufenden Kontext gemischt oder geleakt werden.

Das bedeutet:

- Context und Tooling eines normalen Chats bleiben nicht rueckwirkend sicher
- ein Secure Chat braucht von Beginn an einen eigenen, lokalen und abgeschotteten Ausfuehrungspfad
- wenn Unsicherheit besteht, wird blockiert statt improvisiert

## Kein Toggle im laufenden Chat

Ein laufender Chat darf seinen Sicherheitsmodus nicht aendern.

Regeln:

- `normal` bleibt `normal`
- `secure` bleibt `secure`
- Secure Mode ist keine Session-Schaltflaeche, sondern eine Startentscheidung

Nicht erlaubt:

- "Jetzt sicher machen" mitten im laufenden Chat
- bereits geladene Kontexte spaeter als sicher markieren
- Nutzer in falscher Sicherheit wiegen, obwohl schon externe Provider oder unsichere Tools im Spiel waren

## Quellenpolitik fuer normale Chats

Ein normaler Chat darf:

- `public` Quellen nutzen
- `private` Quellen nutzen, soweit dies vom restlichen Policy-Modell spaeter erlaubt ist

Ein normaler Chat darf nicht:

- `sensitive` Quellen nutzen
- `secret` Quellen nutzen

Wenn eine sensible oder geheime Quelle in einem normalen Chat angefragt wird:

- Zugriff wird blockiert
- kein Kontext wird geladen
- keine Inhaltsvorschau wird geleakt
- es wird ein neuer Secure Chat vorgeschlagen

## Quellenpolitik fuer Secure Chats

Ein Secure Chat ist local-only.

Das bedeutet:

- keine API-Modelle
- keine externen Embeddings
- keine externen Provider
- keine unsicheren Tools
- kein stiller Netzwerkpfad fuer sensible Inhalte

Secure Mode ist nicht nur ein UI-Label, sondern eine harte Produktregel fuer Modell- und Toolgrenzen.

## Verhalten bei sensibler Quelle im normalen Chat

Wenn ein normaler Chat eine `sensitive` oder `secret` Quelle nutzen will:

- Anfrage wird blockiert
- keine Quelle wird geladen
- keine Quelle wird zusammengefasst
- kein Teilkontext wird in den Chat injiziert
- ein neuer Secure Chat wird als naechster Schritt vorgeschlagen

## Nutzerhinweis in diesem Fall

Die Nutzerfuehrung soll klar machen:

- warum blockiert wurde
- dass nichts geleakt wurde
- welcher sichere naechste Schritt existiert

Empfohlene Grundsprache:

`Diese Quelle ist nur in einem Secure Chat verfuegbar. Der aktuelle Chat bleibt normal und kann keine sensiblen Daten laden.`

## Modellwechsel-Regel

In einem Secure Chat sind nur lokale Modelle erlaubt.

Das bedeutet:

- das Primaermodell muss lokal sein
- jedes Fallback-Modell muss ebenfalls local-only sein
- ein API-Modell darf im Secure Flow nicht als stiller Rueckfall einspringen

Nicht erlaubt:

- Secure Chat mit externem Modell
- lokales Primaermodell, aber externer Fallback
- implizites Umschalten auf Provider ausserhalb der lokalen Sicherheitsgrenze

## Nutzertexte und States

Mindestens diese sichtbaren Zustaende muessen spaeter klar unterscheidbar sein.

## `normal`

Bedeutung:

- normaler Chat ohne Secure Mode

Empfohlener Nutzertext:

`Dieser Chat nutzt den normalen Modus. Oeffentliche und private Quellen koennen verfuegbar sein, sensible Quellen bleiben blockiert.`

## `blocked-sensitive-source`

Bedeutung:

- eine sensible oder geheime Quelle wurde im normalen Chat angefragt

Empfohlener Nutzertext:

`Diese Quelle ist in diesem Chat nicht verfuegbar. Sensible Daten koennen nur in einem neuen Secure Chat verwendet werden.`

## `create-secure-chat`

Bedeutung:

- das System bietet den sicheren Folgepfad an

Empfohlener Nutzertext:

`Erstelle einen neuen Secure Chat, um mit lokalen Modellen und gesperrten externen Pfaden weiterzuarbeiten.`

## `secure-active`

Bedeutung:

- der Chat wurde von Anfang an als Secure Chat gestartet

Empfohlener Nutzertext:

`Secure Chat ist aktiv. Dieser Chat bleibt local-only und blockiert externe Modelle, Provider und unsichere Tools.`

## `secure-local-model-required`

Bedeutung:

- im Secure Chat steht kein zulaessiges lokales Modell bereit

Empfohlener Nutzertext:

`Secure Chat benoetigt ein lokales Modell. Externe Modelle sind in diesem Modus nicht erlaubt.`

## `external-provider-blocked`

Bedeutung:

- ein externer Provider oder externes Embedding waere noetig oder wurde angefordert

Empfohlener Nutzertext:

`Externe Provider sind im Secure Chat blockiert. Waehle ein lokales Modell oder kehre in einen normalen Chat zurueck.`

## Telegram-Regel

Fuer Telegram gilt dieselbe Policy.

Das bedeutet:

- sensible Daten werden ueber Telegram im normalen Flow blockiert
- es gibt keinen stillen Ausnahmeweg fuer Telegram
- ein spaeterer Secure-Flow fuer Telegram muesste explizit und separat entworfen werden

Bis dahin gilt:

- blockieren ist korrekt
- kein sensibler Inhalt wird an Telegram-Kontexte geleakt

## Stop-Regeln

Wenn Policy oder Ausfuehrungslage unklar ist, wird blockiert.

Mindestens diese Stop-Regeln gelten:

- unklarer Default -> blockieren
- API-Modell im Secure Flow -> blockieren
- sensible Quelle im normalen Kontext -> blockieren
- externer Provider im Secure Chat -> blockieren
- lokales Fallback fehlt im Secure Chat -> blockieren

## Nicht-Ziele

`SEC1` fuehrt bewusst nicht aus:

- keine App-Level-Verschluesselung
- keine automatische Klassifikation ohne Review
- keine DSGVO-Rechtsberatung
- keine DLP-Engine
- keine Telegram-Code-Aenderungen
- keine Migration oder Encryption-Implementierung
- keinen Frontend- oder Backend-Code

## Akzeptanzkriterien fuer spaetere `SEC2`-, `SEC3`- und `SEC4`-Slices

`SEC1` ist nur dann sauber abgeschlossen, wenn spaetere Slices ohne neue Policy-Grundsatzdebatte implementieren koennen.

Mindestens klar sein muss:

- Secure Mode ist immutable pro Chat oder Thread
- ein laufender Chat hat keinen Security-Toggle
- normale Chats duerfen `public/private`, aber keine `sensitive/secret` Quellen nutzen
- ein normaler Chat blockiert sensible Quellen ohne Kontext-Leak und schlaegt einen neuen Secure Chat vor
- Secure Chats sind local-only
- im Secure Chat sind externe Modelle, Embeddings, Provider und unsichere Tools blockiert
- Fallbacks im Secure Chat bleiben local-only
- die sichtbaren States und Nutzertexte sind definiert
- Telegram folgt derselben Policy oder bleibt blockiert, bis ein eigener Secure-Flow existiert
- unklare Sicherheitslagen fuehren zu Block statt zu stiller Degradierung

## Umgesetzte und erwartete Folge-Slices

### `SEC2-data-classification-model`

Quellen, Dokumente und abgeleitete Artefakte bekommen eine explizite Klassifikation:
`public`, `private`, `sensitive` oder `secret`.

### `SEC3-chat-security-state-model`

Chats und Threads tragen einen immutable Security State:
`normal` oder `secure`. Der Zustand wird beim Start gesetzt und nicht im laufenden Chat umgeschaltet.

### `SEC4-policy-gate-model`

Ein zentraler Decision Layer bewertet Sources, Provider, Embeddings, Tools und Export-/Log-Intents.

### `SEC5-local-only-model-routing`

Naechster sicherer Schritt: Modell- und Fallback-Auswahl so vorbereiten, dass Secure Chats nur lokale Modelle und lokale Embeddings zulassen. Dieser Slice darf bestehende Provider-/Routing-Hotfiles erst nach separatem Gate anfassen.

### `SEC6-sensitive-retrieval-guard`

Naechster kritischer Schritt: Retrieval vor Memory/RAG/Graph-Zugriff blockiert sensible Quellen in normalen Chats, ohne Snippets oder Kontext zu laden.

### `SEC7-telegram-secure-policy`

Telegram folgt derselben Policy. Sensible Daten werden im normalen Telegram-Flow blockiert, bis ein expliziter Secure-Flow entworfen ist.

### `SEC8-security-audit-runbook`

Abschlussnachweis fuer lokale Modelle, sensible Quellen, Provider-Blocker, Export/Log-Policy und Known Limits.

Der Vertrag beschreibt die UX- und Policy-Grenzen des Secure Data Mode. Runtime-Integration bleibt absichtlich in separaten Hotfile-Gates.
