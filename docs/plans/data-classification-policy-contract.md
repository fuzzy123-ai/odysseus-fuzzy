# Data Classification Policy Contract

Stand: 2026-06-16

Status: **SEC2A Datenklassifikationsvertrag fuer Odysseus Memory und Sources**

Quellen:

- `docs/plans/secure-data-mode-contract.md`
- `docs/plans/unified-odysseus-roadmap.md`

Dieser Vertrag definiert die Datenklassifikation fuer Quellen, Dokumente und abgeleitete Memory-Artefakte in Odysseus. Der Slice fuehrt bewusst keine Backend-, Runtime-, Frontend-, Test-, Migrations-, Klassifikations- oder Verschluesselungs-Aenderungen aus. Er friert nur Klassen, Defaults, Propagation, Override-Regeln, Mixed-Source-Policy und Akzeptanzkriterien ein, damit `SEC2B-data-classification-model` spaeter ohne Policy-Mehrdeutigkeit gebaut werden kann.

## Ziel

Odysseus braucht eine explizite, reviewbare und policy-relevante Datenklassifikation.

Wichtig ist:

- nicht jede Vault-Quelle ist automatisch sensibel
- Klassifikation ist kein impliziter Dateisystemeffekt
- Secure- und Chat-Policies haengen von dieser Klassifikation ab

## Klassen

Die vier Produktklassen lauten:

- `public`
- `private`
- `sensitive`
- `secret`

## Leitregel

Klassifikation ist explizit, reviewbar und fuer Policy verbindlich.

Das bedeutet:

- Dateityp, Speicherort oder Vault-Zugehoerigkeit entscheiden nicht allein ueber Sensitivitaet
- unklare Faelle duerfen nicht still als harmlos behandelt werden
- abgeleitete Artefakte erben mindestens die Strenge ihrer Quellen, solange keine explizite Review etwas anderes freigibt

## Defaults

Vault, Nextcloud oder Dateiarchiv sind nicht pauschal sensibel.

Das bedeutet:

- ein Dokument in einem Vault ist nicht automatisch `sensitive`
- eine Nextcloud-Quelle ist nicht automatisch `secret`
- ein Dateiarchiv ist nicht automatisch `private`

Die Klassifikation muss explizit vorliegen oder konservativ behandelt werden.

## Unknown-Default

Wenn die Klassifikation unbekannt ist:

- Standard ist konservativ, aber nutzbar
- fuer unkritische lokale Organisation darf eine Quelle als `unknown` oder ungeprueft markiert sein
- sobald Chat-, Provider- oder Secure-Policy relevant wird, fuehrt unklare Einstufung zu Block oder Review

Empfohlene Produktregel:

- unknown fuer rein lesende Verwaltungsansicht moeglich
- unknown fuer policy-relevanten Zugriff nicht vertrauenswuerdig genug

## Bedeutung je Klasse

## `public`

Bedeutung:

- fuer breite oder offene Nutzung gedacht
- keine erhoehte Schutzannahme

Zugriff:

- normaler Chat erlaubt
- Secure Chat erlaubt

## `private`

Bedeutung:

- nicht oeffentlich, aber nicht automatisch sensibel
- persoenlich, intern oder eingeschraenkt nutzbar

Zugriff:

- normaler Chat erlaubt
- Secure Chat erlaubt

## `sensitive`

Bedeutung:

- erhoehter Schutzbedarf
- darf nicht in normale externe oder unsichere Flows geraten

Zugriff:

- normaler Chat blockiert
- Secure Chat erlaubt, aber nur local-only

## `secret`

Bedeutung:

- hoechster Schutzbedarf in diesem Modell
- nur streng kontrollierte lokale Verarbeitung

Zugriff:

- normaler Chat blockiert
- Secure Chat erlaubt, aber nur local-only

## Zugriffspolicy

Die Zugriffspolicy lautet:

- normaler Chat darf `public`
- normaler Chat darf `private`
- normaler Chat blockiert `sensitive`
- normaler Chat blockiert `secret`
- Secure Chat darf `sensitive`, aber nur local-only
- Secure Chat darf `secret`, aber nur local-only

## Vererbung und Propagation

Klassifikation propagiert von der Quelle in abgeleitete Artefakte.

Mindestens betroffen sind:

- Dokument
- Chunk
- Node
- Summary
- Edge

## Grundregel

Abgeleitete Artefakte duerfen nicht weniger streng sein als ihre Quelle, ausser eine explizite Review erlaubt dies spaeter bewusst.

Das bedeutet:

- `sensitive` Dokument -> `sensitive` oder strengerer Chunk
- `secret` Quelle -> `secret` Summary, solange keine explizite Review anders entscheidet
- `private` Quelle darf nicht still zu `public` Summary werden

## Strengste Klassifikation gewinnt

Wenn mehrere Quellen beteiligt sind:

- die strengste beteiligte Klassifikation bestimmt die Policy

Beispiele:

- `public` + `private` -> mindestens `private`
- `private` + `sensitive` -> mindestens `sensitive`
- `sensitive` + `secret` -> `secret`

## Mixed-Source Antworten

Wenn eine Antwort auf mehreren Quellen beruht:

- die hoechste beteiligte Klassifikation bestimmt die gesamte Policy der Antwort
- Antwortpfad, Modellwahl und Provider-Erlaubnis richten sich nach der strengsten Quelle

Das bedeutet:

- eine einzelne `sensitive` Quelle macht eine Mixed-Source-Antwort fuer normale Chats unzulaessig
- eine einzelne `secret` Quelle verhindert normale Verarbeitung ebenfalls vollstaendig

## Manuelle Overrides

Manuelle Overrides sind erlaubt, aber nicht still.

## Grundregeln fuer Overrides

- Override muss dokumentiert sein
- Override braucht `wer`
- Override braucht `warum`
- Override braucht `wann`

## Herabstufung

Eine Herabstufung braucht immer einen Reason.

Das bedeutet:

- `sensitive` -> `private` ohne Begruendung ist nicht erlaubt
- `secret` -> `sensitive` ohne dokumentierten Grund ist nicht erlaubt
- fehlender Reason fuehrt zu Block

## Hochstufung

Hochstufung darf konservativ einfacher sein als Herabstufung, aber soll ebenfalls nachvollziehbar bleiben.

Das bedeutet:

- lieber zu streng als zu locker
- auch konservative Hochstufung sollte reviewbar bleiben

## Normalisierung

Klassifikation soll spaeter stabil als String oder Enum normalisiert werden.

Erwartung:

- keine freien Synonyme im Policy-Kern
- keine uneinheitliche Gross-/Kleinschreibung
- kein Mischzustand aus lokalen Sonderwerten

Zulaessige Kernwerte:

- `public`
- `private`
- `sensitive`
- `secret`

## Unknown- und Invalid-Handling

Wenn ein Wert unbekannt oder ungueltig ist:

- stilles Herunterstufen ist nicht erlaubt
- fuer policy-relevante Nutzung gilt Block oder Review

Das bedeutet:

- `unknown` ist nicht automatisch `public`
- invalide Strings sind nicht automatisch `private`
- ungueltige oder fehlende Klassifikation darf keinen externen oder normalen Sensitive-Bypass erzeugen

## Review Queue

Unsichere oder unklare Klassifikation gehoert in eine Review Queue.

Das bedeutet:

- keine automatische Sensitivitaetsklassifikation ohne Review in diesem Track
- unklare Einstufungen bleiben sichtbar und bearbeitbar
- Policy darf bei Unsicherheit blockieren, statt zu raten

## Stop-Regeln

Mindestens diese Stop-Regeln gelten:

- unklarer Default -> blockieren oder Review
- sensitive in normalem Kontext -> blockieren
- secret in normalem Kontext -> blockieren
- Herabstufung ohne Reason -> blockieren
- invalid classification in policy-relevant flow -> blockieren oder Review

## Akzeptanzkriterien fuer `SEC2B-data-classification-model`

`SEC2A` ist nur dann sauber abgeschlossen, wenn `SEC2B` daraus ohne neue Policy-Grundsatzdebatte modellieren kann.

Mindestens klar sein muss:

- die Kernklassen heissen `public`, `private`, `sensitive`, `secret`
- Vault/Nextcloud/Dateiarchiv sind nicht pauschal sensibel
- unknown oder invalid handling ist konservativ und policy-relevant definiert
- normale Chats duerfen `public/private`, aber nicht `sensitive/secret`
- Secure Chats duerfen `sensitive/secret` nur local-only
- Propagation von Dokument zu Chunk/Node/Summary/Edge ist beschrieben
- abgeleitete Artefakte duerfen nicht still schwächer klassifiziert werden
- strengste Klassifikation gewinnt
- Mixed-Source-Antworten folgen der hoechsten beteiligten Klasse
- Herabstufung braucht immer dokumentierten Reason
- unklare Faelle landen in Block oder Review Queue, nicht in stiller Auto-Freigabe

## Nicht-Ziele

`SEC2A` fuehrt bewusst nicht aus:

- keine Verschluesselung
- keine automatische DLP-Engine
- keine DSGVO-Rechtsberatung
- keine Migration bestehender Daten
- keine automatische Klassifikation ohne Review
- keinen Frontend- oder Backend-Code
- keine Tests

Der Vertrag beschreibt nur die Produkt- und Policy-Regeln fuer Datenklassifikation in Odysseus Memory und Sources.
