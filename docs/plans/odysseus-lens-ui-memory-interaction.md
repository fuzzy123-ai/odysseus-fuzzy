# Odysseus Lens UI & Memory Interaction

Stand: 2026-06-16

Status: **neuer koordinierter Track fuer Lens-/Memory-UI nach dem 1.0-Evidence-Fenster**

Dieser Plan uebernimmt den Side-Conversation-Handoff in die Hauptkoordination. Ziel ist keine weitere Sammlung einzelner Tool-Buttons, sondern eine klare Arbeitsoberflaeche ueber dem Memory-System.

UX-Contract fuer den Start: `docs/plans/lens-ui-ux-contract.md`

Shell-Stability-Contract fuer `LENS1`: `docs/plans/lens-shell-stability-contract.md`

Read/Write-Tabs-Contract fuer `LENS2`: `docs/plans/memory-read-write-tabs-contract.md`

Tag-Chip-System-Contract fuer `LENS3`: `docs/plans/tag-chip-system-contract.md`

Document-Intelligence-Bar-Contract fuer `LENS4`: `docs/plans/document-intelligence-bar-contract.md`

Review-Audit-Spark-Redesign-Contract fuer `LENS5`: `docs/plans/review-audit-spark-redesign-contract.md`

## Produktentscheidung

Odysseus Lens wird als Arbeitsoberflaeche ueber Memory verstanden:

- Dokument: Lesen, Schreiben, Metadaten, Tags, Beziehungen.
- Graph/Lens: Modus innerhalb des aktuellen Dokuments, nicht weiterer Hauptbutton.
- Gedaechtnis Lesen: Fragen stellen, Quellen sehen, Confidence, Graph-Jump.
- Gedaechtnis Pflegen: Review Queue fuer Captures, Tag-Vorschlaege, Kanten, Summaries, unsichere Aenderungen.
- Insights: Spark-artige Uebersicht, Auffaelligkeiten, Vorschlaege, wachsende Themen.
- Diagnostics: Knowledge Audit, Qualitaet, fehlende Quellen, kaputte Links, veraltete Knoten.
- Activity: laufende Jobs, letzte Automationen, Fehler.

Buttons verschwinden nicht komplett, werden aber umgedeutet. Nutzer bedienen nicht einzelne Tools, sondern wechseln zwischen klaren Zustaenden: Lesen, Pflegen, Insights, Diagnostics und Activity. Automatisierung erledigt den Normalfall im Hintergrund; UI zeigt Review, Vertrauen, Kontrolle und Ausnahmefaelle.

## UI-Regeln

- 60-30-10 Farbregel.
- klare visuelle Hierarchie.
- 8px-Raster.
- maximal zwei Fonts; Systemschrift ist erlaubt.
- feste Typostufen.
- 44px Mindest-Klickziele.
- genau ein Primaerbutton pro Ansicht.
- vollstaendige Component States: default, hover, active, focus, disabled, loading, error, empty.
- Labels ueber Eingaben, keine Placeholder-only-Formulare.
- Inline-Validierung direkt am Feld.
- plattformkonform und erwartungskonform statt ueberraschender Tool-UI.

## Arbeitsprinzip

Alice definiert zuerst UX-Vertraege. Bob implementiert danach fokussiert in UI und Tests. Charlie koordiniert Hotfiles, prueft Worktree, testet und integriert.

Hotfiles:

- `plugins/obsidian/frontend/main.js`
- `plugins/obsidian/frontend/style.css`
- `tests/test_obsidian_sidebar_static.py`

Diese Dateien werden nicht parallel bearbeitet.

## Alice/Bob/Charlie Matrix

| Slice | Ziel | Alice | Bob | Charlie | Parallel? |
| --- | --- | --- | --- | --- | --- |
| `LENS0-ux-contract` | Zielbild festziehen: Lens, Lesen/Pflegen, Insights, Diagnostics, Graph-Modus | UX-Contract schreiben: Navigation, Farben, Button-Hierarchie, 8px-Raster, Zustaende | keine Codearbeit | Worktree pruefen, Hotfiles sperren, Akzeptanzkriterien finalisieren | ja, Alice/Charlie |
| `LENS1-shell-stability` | UI-Grundlage stabilisieren | Erwartetes Verhalten fuer Fullscreen, Minimize, New Chat, Overlay beschreiben | Z-Index-Fix, Fullscreen-Toggle zwischen `-` und `x`, New Chat minimiert Lens, Audit-Close reflowt Graph | Static/UI-Tests pruefen, Regression gegen bestehende Obsidian-Tests | nein |
| `LENS2-memory-read-write-tabs` | Gedaechtnis in zwei klare Bereiche teilen | Texte und Flow fuer `Gedaechtnis Lesen` und `Gedaechtnis Pflegen` | Tabs/Panel-State einbauen, alte Review/Audit/Spark-Zugaenge sauber einsortieren | Pruefen, dass bestehende Routen/Tools nicht brechen | bedingt |
| `LENS3-tag-chip-system` | Tags ueberall gleich nutzbar machen | Chip-Verhalten definieren: Vorschlaege, Enter, Backspace, Duplikate, Normalisierung | Autocomplete aus bestehenden Tags, Chips in Memory/Spark/Header wiederverwenden | Tests fuer Tag-UI-Vertraege ergaenzen/pruefen | nein |
| `LENS4-document-intelligence-bar` | Dokumentheader kompakt und memory-tauglich machen | Metadatenmodell: Typ, Projekt, Status, Datum, Tags, Beziehungen, Memory-State | kompakte Header-Bar rendern, vorhandene Frontmatter/Statusdaten anbinden | Pruefen, ob RAPTOR-/GraphRAG-Signale nur angezeigt, nicht erfunden werden | bedingt |
| `LENS5-review-audit-spark-redesign` | Alte Tools neu ordnen | Review Queue, Insights und Diagnostics sprachlich/visuell definieren | Memory Review -> Pflegen, Spark -> Insights, Knowledge Audit -> Diagnostics | Backward Compatibility und alte Buttons/Routen pruefen | nein |
| `LENS6-odysseus-lens-rename-plan` | Obsidian als Host, Lens als Produkt | Naming-/Migrationsvertrag | technische Aliasstrategie vorbereiten, noch keine harte Umbenennung ohne Gate | Monorepo-/Plugin-Rename in separaten sicheren Slice schneiden | nein |

## Empfohlene Reihenfolge

1. `LENS0-ux-contract`
2. `LENS1-shell-stability`
3. `LENS2-memory-read-write-tabs`
4. `LENS3-tag-chip-system`
5. `LENS4-document-intelligence-bar`
6. `LENS5-review-audit-spark-redesign`
7. `LENS6-odysseus-lens-rename-plan`

Ohne stabile Shell lohnt sich kein groesseres Redesign, weil Fullscreen, Overlay und Layout-Reflow sonst alle weiteren UX-Entscheidungen verfaelschen.

## Definition of Done

- Graph/Lens ist als View-Mode/Schieberegler erhalten und kein weiterer Hauptbutton.
- Memory Lesen und Memory Pflegen sind visuell, sprachlich und zustandslogisch getrennt.
- Review, Spark und Audit sind in Pflegen, Insights und Diagnostics einsortiert.
- Pro Ansicht gibt es genau einen Primaerbutton.
- Keine unklaren Tool-Button-Gruppen ohne Nutzerziel.
- Bestehende Backend-Routen und Tool-Endpunkte bleiben kompatibel.
- Static/UI-Smokes laufen gruen oder dokumentieren konkrete Bug-Slices.
- Keine RAPTOR-/GraphRAG-Signale werden im UI erfunden; nur echte Payloads oder klare Empty/Unknown States.

## Nicht-Ziele

- keine harte Plugin-Umbenennung ohne separaten Rename-Gate.
- keine neue Graph-Engine.
- keine Postgres-/Qdrant-/Kuzu-Arbeit.
- keine Nextcloud-Anbindung.
- keine neue Memory-Wahrheitsschicht.
- keine schreibenden Automationspfade ohne Review-/Confidence-Gate.
