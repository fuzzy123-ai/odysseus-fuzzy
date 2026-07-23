# Universal Inbox Document Workbench Handoff

Stand: 2026-07-15

Status: ready after explicit design confirmation; implementation not started

Roadmap: `docs/plans/universal-inbox-abc-roadmap.md`, continuation UIX-ABC13 bis UIX-ABC24

## Purpose

Dieser Handoff setzt die bestehende Universal-Inbox-Roadmap fort. Er baut keinen Fork von JDEworks File Viewer und importiert kein fremdes Repository. Er verwendet nur klar ausgewaehlte Codeideen oder kleine MIT-lizenzierte Helfer und integriert sie in das bestehende Odysseus-Dokument- und Harbor-One-System.

## Confirmed Direction

- Zielmodus: pruefen, routen, bearbeiten, exportieren.
- Formatstufe: Dokument-Fokus.
- P0: Markdown/Text, PDF, DOCX als extrahierte Arbeitskopie.
- Bearbeitung: nur in einer versionierten Arbeitskopie; das Inbox-Original bleibt unveraendert.
- Routing: zuerst erklaerbarer Dry Run; kein Live-Copy/Move/Delete/Overwrite.
- Export: zuerst lokaler Browser-Download; Nextcloud-/Provider-Export bleibt live-gated.
- UI: fokussierter Harbor-One-V3-Arbeitsbereich; keine Portierung des V2-Demo-Viewers.
- Backend-Policy ist autoritativ; Browser-Erkennung bleibt advisory.

## Repo Truth To Recheck

- `/harbor-one` liefert `static/frontpage-v3/index.html`.
- Die V3-Inbox ist aktuell statische Fixture-Oberflaeche und hat keinen Live-Viewer-Controller.
- `routes/universal_inbox_routes.py` bietet owner-gepruefte, content-free Status- und Flow-State-Routen fuer Upload-Quellen.
- `src/universal_inbox_flow_state.py` ist der kanonische redigierte Neun-Stufen-Flow und bleibt content-free.
- `routes/workspace_snapshot_routes.py` hat einen optionalen Inbox-Provider; `app.py` verdrahtet ihn derzeit nicht.
- `core/database.py`, `routes/document_routes.py` und `static/js/document.js` bieten bereits Owner-Gating, Versionen, Diff, PDF-Funktionen, Bearbeitung und Export.
- Das allgemeine Document-Modell hat noch keine Universal-Inbox-Provenance.
- Zum Handoff-Zeitpunkt sind `app.py`, `src/upload_handler.py`, `static/frontpage-v3/api.js`, `app.js`, `index.html`, `v3-fixed.css` und `tests/test_workspace_snapshot.py` fremd belegt oder unintegriert. Vor jeder Bearbeitung Status und Path-Ownership erneut pruefen.

## External Reference Decision

Quelle: <https://github.com/JDEworks/file-viewer>, inspizierter Commit `b99b6767a9b9caa7dca7924e66aa0af4cb822094`, MIT Copyright 2026 jdeworks.

Moegliche kleine Codeadaptionen:

- `docs/core/detect.js`: Confidence-Clamping, Ranking und Fallback.
- `docs/core/content-signature.js`: ausgewaehlte Magic-Byte-/Claim-Mismatch-Pruefungen.
- `docs/core/intake.js`: BOM-/Encoding-Helfer, Parser-vs-Source-Text, Byte-Grenzen und editable-paste guard.
- `docs/core/generic-metadata.js`: Unicode-Control-, Zero-Width- und Double-Extension-Risiken.

Nur als Architekturidee:

- kleiner Capability-Descriptor statt der vollstaendigen Registry;
- opaque-origin, sanitisiertes Preview mit begrenzter Message-Bridge;
- Original/Arbeitskopie/Diff als klar getrennte Modi.

Nicht uebernehmen:

- App-Shell, File Tree, Settings, PWA, Companion;
- PDF-/Markdown-/Office-Editoren und Renderer;
- `docs/vendor/**`, Monaco, pdf.js, Mammoth, SheetJS, JSZip oder PPTX-Viewer;
- rund 140 Typmodule und deren statische Registry;
- fremde Styles oder das komplette Repository.

Bei echter Codeadaption sind `licenses/jde-file-viewer-MIT-LICENSE.txt` und Source-Kommentare mit URL, Commit und MIT-Hinweis Pflicht. Vendor-Lizenzen werden dadurch nicht mit uebernommen.

## Ready-To-Paste Execution Prompt

```text
Arbeite im Odysseus-Workspace an der bestehenden Roadmap
`docs/plans/universal-inbox-abc-roadmap.md`, Abschnitt "Document Workbench
Continuation (2026-07-15)".

Verwende `$abc` als einzigen Roadmap-Einstiegspunkt und `impeccable` fuer die
spaetere UI-Arbeit. Oeffne die bestehende benannte Roadmap; erstelle keine
zweite konkurrierende Roadmap. Reconcile den aktuellen Repo-Status gegen den
Handoff, bevor du einen Slice beanspruchst.

Das Produktziel ist bestaetigt:
- pruefen, routen, bearbeiten, exportieren;
- Dokument-Fokus;
- P0: Markdown/Text, PDF, DOCX als extrahierte Arbeitskopie;
- Original immer unveraendert;
- Routing nur Dry Run;
- Export zunaechst nur lokaler Browser-Download;
- Nextcloud-/Provider- und Memory-Schreibvorgaenge bleiben hinter bestehenden
  Live-Gates.

Design Brief:
- Harbor One V3 erhaelt einen fokussierten Dokument-Arbeitsbereich.
- Desktop: Inbox-Liste links, Dokument in der Mitte, Flow/Provenance/Aktionen
  rechts; schmal: Dokument primaer, Seitenbereiche als Tabs/Drawer.
- Modi: Original, Extraktion, Arbeitskopie, Differenz, jeweils nur wenn der
  serverseitige Capability-Vertrag sie erlaubt.
- Aktionsfolge: Pruefen -> Route vorschlagen -> Arbeitskopie
  erstellen/bearbeiten -> Exportieren.
- Bestehende Harbor-One-Tokens verwenden. Keine neue Palette, UI-Library oder
  portierte V2-Fixture-Viewer-Logik.

Vorbedingungen:
1. Pruefe `git status --short --branch` und den Diff aller vorgesehenen Pfade.
2. Behandle bestehende Aenderungen als fremd. Insbesondere waren beim Handoff
   `app.py`, `src/upload_handler.py`, `static/frontpage-v3/api.js`, `app.js`,
   `index.html`, `v3-fixed.css` und `tests/test_workspace_snapshot.py` belegt.
3. Bearbeite keinen belegten Hotfile ohne expliziten Path-Handoff. Backend- und
   Pure-Module-Slices koennen auf neuen/sauberen Pfaden fortgesetzt werden.
4. Verifiziere, dass der Nutzer den Design Brief bzw.
   `UIX-WORKBENCH-DESIGN-ACCEPTANCE` bestaetigt hat. Ohne Bestaetigung keine
   UI-Implementierung; sichere Backend-Analyse bleibt erlaubt.
5. Kein Commit und kein Push, sofern dies nicht im aktuellen Auftrag
   ausdruecklich autorisiert wurde.

Beginne ausschliesslich mit UIX-ABC13:
"Authoritative Workbench Capability Contract".

Bevorzugte Dateien:
- `src/universal_inbox_workbench.py`
- `tests/test_universal_inbox_workbench.py`
- `licenses/jde-file-viewer-MIT-LICENSE.txt` nur wenn tatsaechlich Code
  adaptiert wird

Nicht anfassen in UIX-ABC13:
- `app.py`
- `src/upload_handler.py`
- `routes/universal_inbox_routes.py`
- `core/database.py` und Migrationen
- `static/frontpage-v3/**`
- bestehende Document-Routen oder `static/js/document.js`
- Live-Nextcloud, WebDAV, Provider, Memory/RaptorGraph writer

Implementiere einen reinen, serverseitigen Vertrag, der aus der bestehenden
Universal-Inbox-Dateitypentscheidung einen browser-sicheren Action-State
ableitet. Der Vertrag soll mindestens enthalten:
- Schema-Version;
- Format-/Familienkennung ohne Pfad oder Rohinhalt;
- `original_mutable=false`;
- `working_copy_required_for_edit=true`;
- Action States fuer `inspect`, `route_dry_run`, `create_working_copy`,
  `edit_working_copy`, `download_original`, `export_working_copy`;
- pro Action `state` aus `allowed`, `review`, `blocked`, `not_supported`,
  `live_gate_required` plus stabile `reason_codes`;
- `server_authoritative=true`, `browser_detection_advisory=true`;
- P0/P1/P2 und explizite Grenzen gemaess Formatmatrix der Roadmap;
- keine Datei-, Netzwerk-, Provider- oder Datenbankzugriffe.

Wichtige Semantik:
- Markdown/Text: Inspect, Dry-Run-Route und Text-Arbeitskopie erlaubt.
- PDF: vorhandenen PDF-Pfad als Ziel deklarieren, keinen zweiten Renderer.
- DOCX: extrahierte Markdown-Arbeitskopie, kein DOCX-Roundtrip.
- XLSX/PPTX/ODF/RTF/EPUB: abgestufte Preview-/Original-Download-Faehigkeit,
  aber kein Voll-Editor im MVP.
- Executables: blocked; keine Preview oder Ausfuehrung.
- Browser-Download darf nicht als Nextcloud-/Provider-Write modelliert werden.
- Fehlende Live-Gates duerfen nie durch Frontend-Flags aufgeweicht werden.

Tests fuer UIX-ABC13:
- Tabellengetriebene P0/P1/P2-Cases;
- dangerous/unsupported/review-required;
- server authority und stable reason codes;
- keine sensiblen Felder im serialisierten Payload;
- Originalmutation immer false;
- Live writes immer false bzw. live_gate_required.

Verifikation:
`venv\Scripts\python.exe -m pytest tests\test_universal_inbox_workbench.py tests\test_universal_inbox_file_types.py`

Danach:
- `git diff --check` nur fuer den Slice-Scope;
- geaenderte Dateien und Testresultate melden;
- keine privaten Inhalte, absoluten Hostpfade oder Uploadnamen in Evidence;
- UIX-ABC13 abschliessen und einen kleinen Handoff fuer UIX-ABC14 erzeugen;
- nicht automatisch in UIX-ABC14 oder UI-Hotfiles weiterlaufen, solange kein
  neuer Slice-Claim/Path-Handoff vorliegt.

Stop-Regeln:
- Hotfile-Konflikt, fremde staged files im Scope oder unklarer Path-Owner;
- Rohinhalt, Dateipfad, Token, Chat-ID oder private Dokumentdaten wuerden in
  Snapshot, Flow State, Logs, Tests oder Handoff gelangen;
- Originaldatei wuerde veraendert;
- Copy/Move/Delete/Overwrite, Provider-/Nextcloud- oder Memory-Live-Write waere
  erforderlich;
- neue schwere Viewer-/Office-Abhaengigkeit waere erforderlich;
- Testfix wuerde den Slice-Scope verlassen;
- destruktive Git-Aktion waere erforderlich.

Abschlussformat:
- Slice und Status
- geaenderte Dateien
- Tests mit exaktem Ergebnis
- Redaction-/No-Mutation-Evidence
- offene Gates/Risiken
- naechster sicherer Slice und dessen erlaubte Pfade
```

## Expected First Handoff Card

```text
Roadmap: Universal Inbox ABC Roadmap
Completed slice: UIX-ABC13 Authoritative Workbench Capability Contract
Next slice: UIX-ABC14 Owner-Scoped Inbox Browse And Aggregate Snapshot
Safe next paths: new `src/universal_inbox_items.py`, clean portions of
`routes/universal_inbox_routes.py`, focused route tests
Still gated: app.py/workspace snapshot wiring and all V3 hotfiles until path
handoff; UI shell until design acceptance; all Nextcloud/provider/memory live
writes
Invariant: original_mutable=false; raw content absent from snapshot/flow/evidence
```
