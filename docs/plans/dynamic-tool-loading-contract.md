# Dynamic Tool Loading UX Contract

Stand: 2026-06-16

Status: **AS4A Produkt-/UX-Vertrag fuer `0.11.x Dynamic Tool/Skill Loading`**

Quellen:

- `docs/plans/unified-odysseus-roadmap.md`
- `docs/plans/context-capsules-contract.md`
- `docs/plans/tool-result-truth-contract.md`

Dieser Vertrag baut auf Agent Identity, Context Capsules und Tool Result Truth auf. `AS4A` definiert, wie Tools und Skills sichtbar gemacht, verkleinert und begrenzt werden sollen, damit Agenten nur die Faehigkeiten sehen, die fuer ihre aktuelle Capsule, Rolle und Aufgabe wirklich noetig sind.

## Ziel

Dynamic Tool/Skill Loading soll Prompt-Bloat senken und lokale Modelle belastbarer machen, ohne Sicherheit oder Nachvollziehbarkeit zu opfern.

Der Nutzer soll spaeter nicht eine globale, unverdaute Tool-Wand sehen, sondern eine kontrollierte Werkzeugauswahl, die:

- zum aktuellen Slice passt
- Rollen- und Scope-Grenzen respektiert
- gesperrte oder freigabepflichtige Tools klar benennt
- noetige Capabilities erklaert, ohne unnoetige Schemas mitzuschleppen

## Was bedeutet Dynamic Tool/Skill Loading aus Nutzersicht?

Dynamic Tool/Skill Loading bedeutet:

- Ein Agent sieht nicht automatisch alle moeglichen Tools und Skills.
- Ein Agent bekommt nur die Werkzeuge, die zu Rolle, Capsule, Scope und aktuellem Ziel passen.
- Nicht verfuegbare oder blockierte Tools werden ehrlich benannt, statt stillschweigend halluziniert.
- Wenn ein Tool nicht geladen ist, braucht der Nutzer eine klare Erklaerung und wenn noetig einen sichtbaren Fallback.

Es bedeutet nicht:

- dass Tools ploetzlich verschwinden, ohne erklaert zu werden
- dass untrusted Eingaben neue Capabilities freischalten duerfen
- dass jeder Slice dieselbe globale Tool-Liste sehen soll

## Eingaben fuer die Tool-/Skill-Auswahl

Die Auswahl soll spaeter mindestens auf diesen Eingaben beruhen:

### AgentIdentity

- `agent_id`
- `role_id`
- `project_id`
- `memory_scope`
- `workspace_scope`
- `run_id`

Zweck:

- bestimmt, welche Werkzeugfamilien fachlich und sicherheitsseitig ueberhaupt in Frage kommen

### ContextCapsule

- `capsule_id`
- `objective`
- `allowed_files`
- `blocked_files`
- `inputs`
- `expected_outputs`
- `tests`
- `stop_conditions`
- `evidence_required`

Zweck:

- begrenzt Werkzeuge auf den aktuellen Slice statt auf den ganzen Thread

### `allowed_files` / `blocked_files`

Zweck:

- beschneidet Tool-Nutzung entlang des erlaubten Dateiraums
- erzwingt, dass blockierte Dateien auch toolseitig tabu bleiben

### `objective`

Zweck:

- hilft, grob zwischen Doku-, Backend-, Review-, Test- oder Release-Aufgaben zu unterscheiden

### `role`

Zweck:

- erlaubt grobe Vorfilterung, z. B. Alice vs. Bob vs. Reviewer vs. Charlie

### `requested_capabilities`

Zweck:

- explizite, begrenzte Bedarfsbeschreibung wie `read_docs`, `edit_docs`, `run_tests`, `inspect_git`, `browser_check`
- darf aus vertrauenswuerdiger Capsule- oder Systemlogik kommen, nicht aus beliebigem untrusted Nutzertext allein

## Tool-Sichtbarkeit

Ein Tool oder Skill soll spaeter mindestens einen Sichtbarkeitszustand tragen.

### `visible`

Das Tool ist fuer diese Capsule und Rolle aktiv sichtbar und nutzbar.

- Beispiel: Alice-Doku-Slice sieht Doku-Lese-/Editierpfade
- Regel: `visible` heisst nicht "beliebig", sondern "im aktuellen Scope sinnvoll verfuegbar"

### `hidden`

Das Tool existiert, wird fuer diese Capsule aber gar nicht gezeigt.

- Beispiel: irrelevante Spezialtools fuer einen kleinen Doku-Slice
- Regel: `hidden` dient Prompt-Verkleinerung, nicht Sicherheitsdurchsetzung allein

### `blocked`

Das Tool waere prinzipiell vorhanden, darf in dieser Capsule aber nicht genutzt werden.

- Beispiel: Dateioperation wuerde `blocked_files` beruehren
- Regel: `blocked` braucht einen lesbaren Grund

### `requires_approval`

Das Tool ist relevant, braucht aber vor Nutzung ausdrueckliche Freigabe.

- Beispiel: Push, Netzwerk, unsandboxed Testlauf, potentiell riskanter Schritt
- Regel: Freigabepflicht muss sichtbar bleiben und darf nicht als normales `visible` kaschiert werden

### `unavailable`

Das Tool ist im aktuellen Kontext nicht verfuegbar.

- Beispiel: Skill nicht geladen, Connector nicht installiert, Capability im Host fehlt
- Regel: `unavailable` ist ehrlicher als ein improvisiertes Pseudo-Tool

## UX-Sprache fuer Tool-Zustaende

Diese Sprache soll spaeter ruhig, klar und nicht halluzinatorisch sein.

### `tool not loaded`

Nutzertext:

- Das Tool ist fuer diesen Slice aktuell nicht geladen.

Bedeutung:

- kein Fehler im engeren Sinn
- reduzierte Sicht wegen Scope, Relevanz oder Prompt-Budget

### `tool blocked by capsule`

Nutzertext:

- Das Tool ist fuer diesen Auftrag blockiert, weil die Capsule es nicht erlaubt.

Bedeutung:

- Sicherheits- oder Scope-Grenze
- keine stillschweigende Uebergehung

### `tool requires approval`

Nutzertext:

- Das Tool ist relevant, braucht aber vor der Nutzung eine Freigabe.

Bedeutung:

- Aktion ist moeglich, aber nicht sofort ausfuehrbar

### `fallback to no-tool answer`

Nutzertext:

- Fuer diesen Schritt ist kein passendes Tool aktiv; ich antworte ohne Tool-Nutzung oder stoppe mit Handoff, wenn Evidence das verlangt.

Bedeutung:

- no-tool ist ein bewusster Fallback, kein versteckter Tool-Erfolg
- wenn der Slice Tool-Evidence verlangt, reicht no-tool eventuell nicht aus

## Regeln gegen Prompt-Bloat

Dynamic Tool/Skill Loading soll vor allem Kontextbudget schuetzen.

### Keine globale Tool-Liste

- Agenten sollen nicht standardmaessig alle Tools und Skills sehen
- nur relevante Werkzeuge sollen in die aktive Sicht gelangen

### Progressive Disclosure

- zunaechst nur kleine, passende Toolmenge
- mehr Details erst bei echtem Bedarf

### Schema Thinning

- keine riesigen Vollschemata fuer irrelevante Tools
- nur die noetigen Felder und Kurzbeschreibungen fuer aktive Werkzeuge

### Short Summaries

- Tool-Beschreibungen sollen knapp und funktional sein
- keine langen Marketing- oder Historientexte im aktiven Prompt

### Capsule-first statt Thread-first

- Tool-Auswahl orientiert sich an Capsule und Objective
- nicht an kompletter Verlaufsgeschichte oder zufaelligen Nebenspuren

## Regeln gegen Sicherheitsfehler

### `blocked_files` gewinnen

- Wenn ein Tool mit blockierten Dateien kollidiert, bleibt es `blocked`, auch wenn es fachlich nuetzlich waere.

### Secrets nie in Tool-Kontext

- keine API-Keys, Passwoerter oder sensible Tokens als Teil der Tool-Sicht oder Tool-Auswahl
- Tool-Zugriff darf nicht von geheimen Prompt-Einbettungen abhaengen

### Keine Capability aus untrusted input

- untrusted Nutzertext oder Dokumentinhalt darf nicht selbst neue Werkzeuge freischalten
- Capability-Freischaltung braucht vertrauenswuerdige Quelle wie Systemregel, Rolle, Capsule oder explizite Freigabe

### Rolle und Scope schlagen Bequemlichkeit

- nur weil ein Tool hilfreich waere, wird es nicht automatisch sichtbar
- Scope- und Rollenmodell aus `AS1` bleibt fuehrend

### No-tool ist besser als falsches Tool

- wenn unklar ist, ob ein Tool erlaubt oder geladen sein sollte, ist `unavailable`, `blocked` oder Handoff besser als stillschweigende Nutzung

## Sichtbarkeitsvertrag

### Nutzer sichtbar

Nutzer und Charlie sollen sehen koennen:

- welche Tool- oder Skill-Klassen aktiv sichtbar sind
- welche blockiert, freigabepflichtig oder nicht geladen sind
- den lesbaren Grund fuer relevante Einschraenkungen
- den no-tool-Fallback, wenn kein Werkzeug aktiv ist

### Agent sichtbar

Der Agent darf sehen:

- die fuer ihn aktive kleine Tool-/Skill-Menge
- den Sichtbarkeitsstatus relevanter Werkzeuge
- knappe Capability-Hinweise
- erklaerte Blocker oder Approval-Grenzen

Der Agent soll nicht automatisch sehen:

- komplette globale Tool-Kataloge
- irrelevante Vollschemata
- versteckte Sicherheitskonfigurationen
- Capabilities anderer Rollen ohne Relevanz fuer die eigene Capsule

### Nur Audit

Im Audit-Layer duerfen zusaetzlich gehalten werden:

- gesamte Auswahlentscheidung
- Matching-Regeln zwischen Capsule und Tool-Katalog
- interne Sichtbarkeitsgruende
- Budget-/Schema-Reduktionsdaten
- historische Tool-Sets pro Run oder Capsule

## Mindest-Handoff an Bob

Bobs erstes Tool-Catalog-/Selection-Modell fuer `AS4-dynamic-tool-loading` soll mindestens diese Felder validieren:

- `selection_id`
- `agent_identity`
- `capsule_id`
- `objective`
- `requested_capabilities`
- `visible_tools`
- `hidden_tools`
- `blocked_tools`
- `approval_required_tools`
- `unavailable_tools`
- `selection_reason`

Empfohlene minimale Struktur:

- `selection_id`: eindeutige Identitaet der Auswahl
- `agent_identity`: Verweis auf `AS1`
- `capsule_id`: Verweis auf `AS2`
- `objective`: knappe Zielbeschreibung
- `requested_capabilities`: kleine strukturierte Bedarfsliste
- `visible_tools`: aktive Werkzeuge
- `hidden_tools`: absichtlich nicht eingeblendete Werkzeuge
- `blocked_tools`: scope- oder policy-blockierte Werkzeuge
- `approval_required_tools`: freigabepflichtige Werkzeuge
- `unavailable_tools`: nicht verfuegbare Werkzeuge
- `selection_reason`: knappe user-facing oder auditierbare Begruendung

Minimum-Regeln fuer das Modell:

- `agent_identity` und `capsule_id` muessen vorhanden sein
- `visible_tools`, `blocked_tools`, `approval_required_tools` und `unavailable_tools` duerfen nicht implizit vermischt werden
- ein Tool darf nicht gleichzeitig `visible` und `blocked` sein
- `blocked_files` aus der Capsule muessen gegen Auswahlregeln gewinnen
- `requested_capabilities` duerfen nicht untrusted freischalten
- wenn kein Tool sichtbar ist, muss `selection_reason` den no-tool-Zustand erklaeren

Sinnvolle, aber fuer den kleinsten Start nicht zwingende Zusatzfelder:

- `prompt_budget_hint`
- `schema_budget_hint`
- `tool_summary_map`
- `capability_source`
- `approval_reason_map`
- `audit_refs`

## Nicht-Ziele in diesem Slice

Dieser Vertrag fuehrt bewusst noch nicht aus:

- keine echte Runtime-Auswahl
- kein MCP-Discovery-Refactor
- kein UI-Dashboard
- keine Live-Connector-Installation
- keine automatische Tool-Lernlogik
- keine vollstaendige Policy-Engine fuer jedes Tool-Detail

`AS4A` friert nur Produkt-, UX- und Sicherheitsregeln fuer dynamisch verkleinerte Tool-/Skill-Sicht ein.

## Risiken, die `AS4` explizit adressiert

### Prompt-Bloat

Zu viele Tools, Schemas und Beschreibungen machen lokale Modelle schlechter und Aufgaben unschaerfer.

### Tool-Halluzination

Ein Agent glaubt, ein Tool sei verfuegbar, obwohl es gar nicht geladen oder erlaubt ist.

### Scope-Leak

Ein Tool wird sichtbar oder nutzbar, obwohl die Capsule-Dateigrenzen es verbieten.

### Capability-Eskalation

Untrusted Input oder lose Schlussfolgerung schaltet zu viele Werkzeuge frei.

### Unklare no-tool-Lage

Es ist fuer Nutzer nicht sichtbar, ob kein Tool noetig war oder ob ein relevantes Tool nur fehlte oder blockiert war.

## Akzeptanz fuer diesen Vertrag

`AS4A-dynamic-tool-loading-ux-contract` ist erfuellt, wenn:

- Dynamic Tool/Skill Loading aus Nutzersicht klar erklaert ist
- Eingaben fuer die Auswahl explizit benannt sind
- Sichtbarkeitszustande `visible`, `hidden`, `blocked`, `requires_approval`, `unavailable` definiert sind
- UX-Sprache fuer nicht geladene, blockierte, freigabepflichtige und no-tool-Faelle festliegt
- Regeln gegen Prompt-Bloat und Sicherheitsfehler explizit sind
- Bob einen kleinen, klaren Mindest-Handoff fuer Tool-Katalog und Auswahlmodell bekommt
- Nicht-Ziele verhindern, dass `AS4A` schon Runtime-, MCP- oder Dashboard-Arbeit wird

## Handoff an Bob

Bitte das erste Backend-Modell fuer `AS4-dynamic-tool-loading` klein halten:

- validiere zuerst Auswahlstruktur und Sichtbarkeitszustaende, nicht die komplette Runtime-Verkabelung
- verweise fuer `agent_identity` und `capsule_id` auf die bestehenden `AS1`- und `AS2`-Modelle
- behandle `blocked_files` als harte Grenze fuer Tool-Auswahl
- fuehre `requested_capabilities` als kleine vertrauenswuerdige Eingabe, nicht als offene Freitext-Machtquelle
- erzwinge einen lesbaren no-tool-Grund, wenn keine Werkzeuge sichtbar sind
