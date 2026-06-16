# Secure Policy Gate Contract

Stand: 2026-06-16

Status: **SEC4A zentraler Secure Policy Gate Vertrag**

Quellen:

- `docs/plans/secure-data-mode-contract.md`
- `docs/plans/data-classification-policy-contract.md`
- `docs/plans/chat-security-state-contract.md`

Dieser Vertrag definiert den zentralen Secure Policy Gate Layer fuer Odysseus. Das Gate entscheidet spaeter an einer Stelle, ob ein Chat eine Quelle, ein Modell oder Provider, ein Tool sowie Export- oder Log-Verhalten nutzen darf. Der Slice fuehrt bewusst keine Runtime-, Retrieval-, Provider-, Telegram-, Frontend-, Backend-, Test- oder Verschluesselungs-Aenderungen aus. Er friert nur Eingaben, Entscheidungen, Blockgruende, Tool- und Exportregeln, Stop-Regeln und Akzeptanzkriterien ein, damit `SEC4B-policy-gate-model` spaeter ohne Policy-Mehrdeutigkeit gebaut werden kann.

## Ziel

Odysseus braucht einen zentralen Decision Layer fuer Security-Policy.

Das Gate verbindet:

- Datenklassifikation aus `SEC2`
- Chat Security State aus `SEC3`
- Provider- und Modellscope
- Tool-Sicherheitsklasse
- Export- und Logging-Intent

## Leitregel

Wenn Klassifikation, Chat-State oder Provider-Lage unklar oder unzulaessig ist, entscheidet das Gate konservativ mit Block oder Review.

Das bedeutet:

- keine verteilten Sonderregeln in mehreren Subsystemen
- keine stille Hochstufung normaler Chats
- keine stille Herabstufung sicherer Chats
- keine externen oder unsicheren Pfade fuer sensitive Daten im Secure-Kontext

## Gate-Zielbild

Das Gate ist spaeter die zentrale Policy-Entscheidungsstelle fuer:

- Source-Nutzung
- Modell- und Provider-Nutzung
- Tool-Nutzung
- Export- und Log-Nutzung

Nicht Ziel ist:

- selbst Retrieval auszufuehren
- selbst Provider-Routing zu implementieren
- selbst Encryption oder DLP zu leisten

## Eingaben fuer spaeteres Modell

Das spaetere Gate-Modell soll mindestens diese Eingaben bekommen:

- `ChatSecurityState`
- source classifications
- provider scope
- tool safety class
- export/log intent

## Eingabebedeutungen

### `ChatSecurityState`

Enthaelt mindestens:

- `normal` oder `secure`
- local-only-Anforderung
- immutable Chat-Context

### source classifications

Enthaelt die relevante Klassifikation beteiligter Quellen, Chunks, Nodes, Summaries oder anderer Artefakte.

Mindestens relevant:

- `public`
- `private`
- `sensitive`
- `secret`

Die strengste beteiligte Klassifikation zaehlt.

### provider scope

Beschreibt den Modell- und Providerrahmen.

Mindestens relevant:

- lokal
- extern
- local-only erforderlich

### tool safety class

Beschreibt, ob ein Tool fuer Secure-Kontexte local-only, sicher lokal, extern oder unsicher ist.

### export/log intent

Beschreibt, ob gerade:

- Export angefragt wird
- Logging stattfindet
- ein sensibler Inhalt in potenziell unsichere Ausgaben gelangen koennte

## Entscheidungen

Das Gate soll spaeter mindestens diese Entscheidungen treffen koennen:

- `allow`
- `block`
- `require_secure_chat`
- `require_local_model`
- `require_review`
- `unsupported`

## Bedeutungen der Entscheidungen

### `allow`

Der angefragte Zugriff oder Pfad ist in diesem Kontext erlaubt.

### `block`

Der Pfad ist in diesem Kontext nicht erlaubt und darf nicht ausgefuehrt werden.

### `require_secure_chat`

Der normale Chat reicht nicht; ein neuer Secure Chat ist erforderlich.

### `require_local_model`

Die Anfrage ist nur mit lokalem Modell- und Providerpfad zulaessig.

### `require_review`

Die Lage ist nicht sicher genug fuer automatische Freigabe und braucht Review.

### `unsupported`

Die angefragte Kombination wird in diesem Track oder Kontext bewusst nicht unterstuetzt.

## Blockgruende und User Copy

Mindestens diese Blockgruende muessen spaeter eindeutig unterscheidbar sein.

## `sensitive_source_in_normal_chat`

Bedeutung:

- normale Unterhaltung will `sensitive` oder `secret` Quelle nutzen

Empfohlene Nutzerkopie:

`Diese Quelle ist im aktuellen Chat nicht verfuegbar. Fuer sensible Daten ist ein neuer Secure Chat erforderlich.`

## `external_provider_in_secure_chat`

Bedeutung:

- Secure Chat wuerde externes Modell oder externen Provider nutzen

Empfohlene Nutzerkopie:

`Externe Provider sind im Secure Chat blockiert. Waehle ein lokales Modell oder starte einen normalen Chat fuer nicht-sensible Arbeit.`

## `external_embedding_in_secure_chat`

Bedeutung:

- Secure Chat wuerde externen Embedding-Pfad verwenden

Empfohlene Nutzerkopie:

`Externe Embeddings sind im Secure Chat nicht erlaubt. Der sichere Modus bleibt local-only.`

## `unsafe_tool_in_secure_chat`

Bedeutung:

- ein Tool ist fuer Secure-Kontext extern oder unsicher

Empfohlene Nutzerkopie:

`Dieses Tool ist im Secure Chat nicht erlaubt. Verwende nur sichere lokale Tools oder wechsle in einen passenden Kontext.`

## `export_contains_sensitive_data`

Bedeutung:

- Export oder Log wuerde sensitive oder geheime Inhalte preisgeben koennen

Empfohlene Nutzerkopie:

`Dieser Export ist in diesem Sicherheitskontext nicht erlaubt. Sensible Inhalte brauchen einen eigenen Review- oder Schutzpfad.`

## `classification_unknown_requires_review`

Bedeutung:

- Quellenklassifikation ist unklar oder unvollstaendig

Empfohlene Nutzerkopie:

`Die Datenklassifikation ist noch unklar. Bitte pruefe die Quelle, bevor dieser Pfad freigegeben wird.`

## `ambiguous_security_mode`

Bedeutung:

- Chat- oder Thread-Sicherheitszustand ist nicht eindeutig

Empfohlene Nutzerkopie:

`Der Sicherheitszustand dieses Chats ist nicht eindeutig. Starte einen neuen Chat mit klarem Modus oder pruefe den Kontext.`

## Quellenpolitik im Gate

## Normaler Chat

Normaler Chat darf:

- `public`
- `private`

Normaler Chat blockiert:

- `sensitive`
- `secret`

Wenn `sensitive` oder `secret` angefragt wird:

- `block` oder `require_secure_chat`
- keinen Kontext laden
- keinen Snippet-Leak
- neuen Secure Chat als Folgeschritt anbieten

## Secure Chat

Secure Chat darf:

- `public`
- `private`
- `sensitive`
- `secret`

Aber nur unter local-only Bedingungen:

- lokale Modelle
- lokale Embeddings
- local-only Fallback
- sichere lokale Tools

## Keine stille Hoch- oder Herabstufung

Das Gate darf keine stillen Security-Umschaltungen vornehmen.

Nicht erlaubt:

- normalen Chat intern zu Secure hochstufen
- Secure Chat still auf normalen oder externen Pfad herabstufen
- Nutzer in einen unsicheren Fallback laufen lassen, nur weil lokale Voraussetzungen fehlen

## Tool-Policy

Das Gate bewertet spaeter auch Tools.

## Im normalen Chat

Tools duerfen entsprechend allgemeiner Policy nutzbar sein, solange Datenklassifikation und restlicher Kontext es erlauben.

## Im Secure Chat

Nur sichere lokale Tools sind optional erlaubt.

Blockiert werden:

- externe Tools
- unsichere Tools
- Tools, die sensible Daten an nicht-lokale Pfade weitergeben

## Logging

Auch im Toolkontext gilt:

- keine sensiblen Snippets in Logs
- keine Rohdaten in unsicheren Debug-Ausgaben
- lieber Block oder Review als Leak

## Export- und Log-Policy

Sensitive oder geheime Inhalte brauchen fuer Export oder Logging einen eigenen Review- oder Schutzpfad.

In diesem Track gilt:

- lieber `block` oder `require_review`
- nicht still exportieren
- nicht still in Logs ausgeben

Das bedeutet:

- `sensitive` oder `secret` Export ohne Folgekonzept ist nicht automatisch erlaubt
- Log-Intents fuer sensitive Inhalte muessen geblockt oder gesondert reviewed werden

## Stop-Regeln

Mindestens diese Stop-Regeln gelten:

- unklarer Default -> `block` oder `require_review`
- API oder external in secure -> `block`
- sensitive in normal -> `block`
- secret in normal -> `block`
- unknown classification in policy-relevant Zugriff -> `require_review` oder `block`
- unsicheres Tool in secure -> `block`
- sensibler Export ohne Schutzkonzept -> `block` oder `require_review`

## Akzeptanzkriterien fuer `SEC4B-policy-gate-model`

`SEC4A` ist nur dann sauber abgeschlossen, wenn `SEC4B` daraus ohne neue Policy-Grundsatzdebatte modellieren kann.

Mindestens klar sein muss:

- das Gate ist der zentrale Decision Layer fuer Sources, Models/Providers, Tools und Export/Logs
- die benoetigten Eingaben sind definiert
- die Entscheidungen `allow`, `block`, `require_secure_chat`, `require_local_model`, `require_review`, `unsupported` sind definiert
- Blockgruende und User Copy sind benannt
- normaler Chat darf `public/private`, blockiert `sensitive/secret`
- Secure Chat erlaubt `sensitive/secret` nur local-only
- externe Provider, Embeddings und Fallbacks sind im Secure-Kontext blockiert
- sichere lokale Tools koennen optional erlaubt sein; unsichere Tools in Secure werden blockiert
- sensitive oder geheime Exporte/Logs brauchen Block oder Review statt stiller Freigabe
- unklare Defaults fuehren zu Block oder Review, nicht zu implizitem Verhalten

## Nicht-Ziele

`SEC4A` fuehrt bewusst nicht aus:

- keine echte Runtime-Integration
- keine Telegram-Code-Integration
- keine Encryption
- keine DLP-Engine
- keine Migration
- keine Frontend- oder Backend-Dateien
- keine Tests
- keine Retrieval-, Provider- oder RAG-Implementierung

Der Vertrag beschreibt nur den zentralen Secure Policy Gate Layer als spaetere Entscheidungsstelle zwischen Chat-State, Datenklassifikation, Modellen, Tools und Export-/Log-Intents.
