# Chat Security State Contract

Stand: 2026-06-16

Status: **SEC3A UX-/API-/Policy-Vertrag fuer Chat Security State**

Quellen:

- `docs/plans/secure-data-mode-contract.md`
- `docs/plans/data-classification-policy-contract.md`

Dieser Vertrag definiert den Chat Security State fuer normale und sichere Chats oder Threads. Der Slice fuehrt bewusst keine Backend-, Runtime-, Frontend-, Test-, Provider-, Retrieval-, Telegram- oder Verschluesselungs-Aenderungen aus. Er friert nur Zustandsmodell, API-Felder, UX-States, Edge Cases, Block-Regeln und Akzeptanzkriterien ein, damit `SEC3B-chat-security-state-model` spaeter ohne Policy-Mehrdeutigkeit gebaut werden kann.

## Ziel

Ein Chat startet entweder als `normal` oder als `secure`.

Dieser Zustand bleibt fuer die gesamte Lebensdauer des Chats oder Threads unveraenderlich.

## Produktentscheidung

Chat Security State ist immutable bis zum Chat-Ende.

Das bedeutet:

- ein Chat wird als `normal` erstellt oder als `secure`
- der Sicherheitszustand wird nicht spaeter umgeschaltet
- ein neuer Sicherheitsbedarf fuehrt zu einem neuen Chat, nicht zu einem Toggle im laufenden Kontext

## Leitregel

Sicherheitsgrenzen muessen vor dem ersten Kontextaufbau klar sein.

Das bedeutet:

- keine automatische Hochstufung in laufenden Chats
- keine stille Herabstufung, wenn sichere Ressourcen fehlen
- kein Umschalten der Modell- oder Providerpolitik mitten im Verlauf

## Grundzustaende

Die zwei Kernzustaende lauten:

- `normal`
- `secure`

## `normal`

Bedeutung:

- Standardchat ohne Secure-Policy
- darf `public` und `private` Quellen nutzen
- blockiert `sensitive` und `secret`

## `secure`

Bedeutung:

- Secure Chat von Anfang an
- local-only Ausfuehrungspfad
- erlaubt `sensitive` und `secret`, aber nur unter lokalen Grenzen

## Kein Toggle im laufenden Chat

Ein laufender Chat darf seinen Security State nicht wechseln.

Nicht erlaubt:

- normal -> secure innerhalb desselben Chatverlaufs
- secure -> normal innerhalb desselben Chatverlaufs
- automatische Hochstufung, wenn spaeter eine sensitive Quelle auftaucht
- stille Herabstufung, wenn lokale Secure-Voraussetzungen fehlen

Erlaubt ist nur:

- bestehenden Chat beenden oder verlassen
- neuen Chat mit passendem Security State starten

## Quellenzugriff nach Chat-Zustand

## Normaler Chat

Ein normaler Chat darf:

- `public`
- `private`

Ein normaler Chat blockiert:

- `sensitive`
- `secret`

Wenn spaeter im Verlauf eine sensitive oder geheime Quelle angefragt wird:

- Anfrage blockieren
- keinen Kontext laden
- keinen Snippet-Leak erzeugen
- neuen Secure Chat als naechsten Schritt anbieten

## Secure Chat

Ein Secure Chat darf:

- `public`
- `private`
- `sensitive`
- `secret`

Aber nur unter local-only Bedingungen:

- lokale Modelle
- lokale Embeddings
- local-only Fallback
- keine externen Provider

## Local-only Regel im Secure Chat

Secure Chat ist local-only.

Das bedeutet:

- API-Modelle sind blockiert
- externe Provider sind blockiert
- externer Fallback ist blockiert
- externer Embedding-Pfad ist blockiert

Wenn local-only nicht erfuellt werden kann:

- Verarbeitung blockieren
- Nutzer klar auf fehlende lokale Voraussetzungen hinweisen

## Modellwechsel-Regel

Auch im laufenden Chat bleibt Modellwechsel restriktiv.

## Im normalen Chat

Modellwechsel darf nicht still neue Security-Grenzen behaupten.

Das bedeutet:

- normal bleibt normal
- ein spaeterer Wechsel auf ein lokales Modell macht den bestehenden Chat nicht automatisch secure

## Im Secure Chat

Im Secure Chat sind nur lokale Modelle sichtbar und zulaessig.

Das bedeutet:

- keine API-Modelle in der Auswahl
- kein externer Fallback
- keine implizite Sichtbarkeit unsicherer Provider

## API- und State-Felder fuer spaeteres Modell

Das spaetere Modell soll mindestens diese Felder tragen:

- `chat_id`
- `thread_id`
- `security_mode`
- `created_at`
- `requested_by`
- `allowed_provider_scope`
- `local_only_required`
- `immutable_reason`

## Feldbedeutungen

### `chat_id`

Eindeutige Chat-Identitaet.

### `thread_id`

Zuordnung zu Thread oder Host-Kontext.

### `security_mode`

Erlaubte Kernwerte:

- `normal`
- `secure`

### `created_at`

Zeitpunkt der Erstellung, damit die Unveraenderlichkeit zeitlich klar ist.

### `requested_by`

Wer den Chat in diesem Modus gestartet hat oder welcher Startimpuls dafuer verantwortlich war.

### `allowed_provider_scope`

Definiert den Provider-Rahmen.

Mindestens erwartbar:

- `default`
- `local_only`

### `local_only_required`

Boolescher oder aequivalenter Indikator, ob local-only verpflichtend ist.

Im Secure Chat ist dieser Wert effektiv verpflichtend.

### `immutable_reason`

Dokumentiert, warum der Security State nicht waehrend des Chatverlaufs umschaltbar ist.

## UX-States und Copy

Mindestens diese sichtbaren States muessen spaeter sauber unterscheidbar sein.

## `normal-active`

Bedeutung:

- normaler Chat ist aktiv

Empfohlener Nutzertext:

`Normaler Chat ist aktiv. Oeffentliche und private Quellen sind erlaubt, sensible Quellen bleiben blockiert.`

## `sensitive-source-blocked`

Bedeutung:

- eine sensitive oder geheime Quelle wurde im normalen Chat angefragt

Empfohlener Nutzertext:

`Diese Quelle ist nur in einem Secure Chat verfuegbar. Im aktuellen Chat werden keine sensiblen Inhalte geladen.`

## `create-secure-chat`

Bedeutung:

- sicherer Folgepfad wird angeboten

Empfohlener Nutzertext:

`Starte einen neuen Secure Chat, um mit lokalen Modellen und gesperrten externen Pfaden weiterzuarbeiten.`

## `secure-active`

Bedeutung:

- Secure Chat ist aktiv

Empfohlener Nutzertext:

`Secure Chat ist aktiv. Dieser Chat bleibt local-only und blockiert externe Provider und API-Modelle.`

## `secure-local-model-required`

Bedeutung:

- Secure Chat hat kein zulaessiges lokales Modell verfuegbar

Empfohlener Nutzertext:

`Secure Chat benoetigt ein lokales Modell. Externe Modelle sind in diesem Modus nicht erlaubt.`

## `external-provider-blocked`

Bedeutung:

- externer Provider oder API-Modell wurde im Secure Chat angefragt

Empfohlener Nutzertext:

`Externe Provider sind im Secure Chat blockiert. Waehle ein lokales Modell oder starte einen normalen Chat fuer nicht-sensible Arbeit.`

## `secure-chat-ended`

Bedeutung:

- Secure Chat wurde beendet und sein lokaler Sicherheitsrahmen gilt nicht automatisch fuer neue Chats

Empfohlener Nutzertext:

`Secure Chat wurde beendet. Fuer weitere sensible Arbeit ist ein neuer Secure Chat erforderlich.`

## Edge Cases

## Bestehender normaler Chat trifft spaeter sensitive Quelle

Regel:

- blockieren
- nichts laden
- keinen bestehenden Verlauf umstufen
- neuen Secure Chat anbieten

## Bestehender Secure Chat soll API-Modell nutzen

Regel:

- blockieren
- nicht auf externen Provider ausweichen
- local-only Hinweis zeigen

## Fallback waere extern

Regel:

- blockieren
- keinen stillen Downgrade auf externen Fallback
- lokalen Modellbedarf klar anzeigen

## Export und Logs

Export und Logs duerfen keine sensiblen Snippets verlieren oder leaken.

Das bedeutet:

- keine stille Aufnahme sensibler Ausschnitte in unsichere Logs
- kein unsicherer Exportpfad fuer Secure-Kontext
- Metadaten ueber Security State duerfen sichtbarer sein als sensible Inhaltsfragmente

## Stop-Regeln

Mindestens diese Stop-Regeln gelten:

- ambiguous security mode -> blockieren
- API-Modell in secure -> blockieren
- Toggle-Anforderung im laufenden Chat -> neuen Chat anbieten
- sensitive oder secret in normalem Kontext -> blockieren
- externer Fallback im Secure Chat -> blockieren

## Akzeptanzkriterien fuer `SEC3B-chat-security-state-model`

`SEC3A` ist nur dann sauber abgeschlossen, wenn `SEC3B` daraus ohne neue Policy-Grundsatzdebatte modellieren kann.

Mindestens klar sein muss:

- Chat startet entweder `normal` oder `secure`
- Security State bleibt immutable bis Chat-Ende
- kein Toggle, keine automatische Hochstufung und keine stille Herabstufung
- normaler Chat darf `public/private`, blockiert `sensitive/secret`
- Secure Chat ist local-only
- im Secure Chat sind nur lokale Modelle und local-only Fallbacks erlaubt
- die benoetigten API- und State-Felder sind definiert
- UX-States und Copy sind beschrieben
- Edge Cases fuer sensitive Quelle, API-Modell, externen Fallback und Logs sind festgelegt
- unklare Sicherheitslage fuehrt zu Block statt zu implizitem Verhalten

## Nicht-Ziele

`SEC3A` fuehrt bewusst nicht aus:

- keine echte Runtime-Integration
- keine Retrieval-Gates
- keine Provider-Routing-Implementierung
- keine Verschluesselung
- keine Frontend- oder Backend-Dateien
- keine Tests
- keine Telegram-Code-Aenderungen

Der Vertrag beschreibt nur das UX-, API- und Policy-Modell fuer normale und sichere Chats als unveraenderliche Chat-Eigenschaft.
