# PDF Long Document Extraction Roadmap

Status: active
Owner: operator + Odysseus
Date: 2026-06-29
Scope: PDF extraction for chat attachments, document viewer, Personal Docs/RAG,
Universal Inbox, and Nextcloud ingestion

## 1. Ausgangslage

PDF-Verarbeitung ist aktuell nicht einheitlich:

- `src.personal_docs.extract_pdf_text` nutzt `pypdf` auf das ganze Dokument und
  gibt bei Fehlern oder leerem Text `""` zurueck.
- `src.rag_vector.VectorRAG.index_personal_documents` ueberspringt PDFs ohne
  extrahierten Text faktisch still.
- `src.universal_inbox_extraction` hat bereits klare Statuswerte:
  `completed`, `partial`, `metadata_only`, `unsupported`, `failed`, `blocked`.
  Grosse Dateien werden ueber `max_extract_bytes` zu `metadata_only`.
- `src.nextcloud_chunked_extraction` kann Runtime-Text in begrenzte Chunk-Refs
  umwandeln und persistiert keine Rohinhalte.
- `src.document_processor._process_pdf` extrahiert seitenweise, versucht bei
  bildlastigen Seiten Vision/OCR und kappt Inline-Kontext, ist aber noch kein
  allgemeiner, resumable Extractor.

Das Hauptrisiko: grosse oder kaputte PDFs verschwinden im RAG-Pfad zu leise,
waehrend andere Pfade schon Review- und Metadata-only-Verhalten kennen.

## 2. Zielbild

Odysseus behandelt grosse PDFs als normales Szenario:

- Extraktion laeuft seitenweise und budgetiert.
- Einzelne kaputte Seiten machen das Dokument nicht komplett unbrauchbar.
- Ergebnisse koennen `completed`, `partial`, `metadata_only`, `needs_review`
  oder `failed` sein.
- RAG bekommt verwertbare Chunks, auch wenn nur ein Teil des PDFs lesbar ist.
- Ledgers und Reports speichern keine Rohtexte, sondern Status, Warnungen,
  Offsets, Chunk-Refs, Hashes und Review-Gruende.
- OCR/Vision bleibt optional, lokal-only-policy-aware und budgetiert.
- Nutzer und Operator sehen klar, warum ein PDF nicht voll indexiert wurde.

## 3. Nicht-Ziele

- Kein automatisches Entpacken oder Reparieren beliebiger kaputter PDFs.
- Keine Pflichtabhaengigkeit auf PyMuPDF im MIT-Core.
- Keine unbounded OCR/Vision-Laeufe.
- Keine Speicherung vollstaendiger PDF-Rohtexte in Ledgers oder Sidecars.
- Kein automatisches Senden sensibler Dokumentseiten an externe Vision-Modelle.

## 4. Architekturentscheidung

Ein neuer gemeinsamer PDF-Extractor wird eingefuehrt, zunaechst als interne
Backend-Komponente:

- Modul: `src/pdf_extraction.py`
- Primaerer Adapter: `pypdf`
- Optionaler OCR/Vision-Adapter: bestehende Vision-Runtime aus
  `src.document_processor`, policy-gated
- Ergebnisobjekte:
  - `PdfExtractionResult`
  - `PdfPageExtraction`
  - `PdfExtractionWarning`
  - `PdfExtractionBudget`
- Statusmodell:
  - `completed`
  - `partial`
  - `metadata_only`
  - `needs_review`
  - `failed`

Der Extractor liefert entweder Seiten-/Chunk-Text an Runtime-Callers oder
metadata-only Reports. Persistenzentscheidungen bleiben bei den bestehenden
Pipelines.

## 5. Phasenuebersicht

| Phase | Ziel | Ergebnis |
| --- | --- | --- |
| P0 | Verträge und Budgets definieren | Done 2026-06-30: Statusmodell, Warning-Codes, Budgettypen |
| P1 | Gemeinsamen Extractor bauen | Done 2026-06-30: Seitenweise pypdf-Extraktion mit Partial-Erfolg |
| P2 | RAG/Personal Docs angleichen | Done 2026-06-30: Kein stilles Verschwinden grosser PDFs |
| P3 | Universal Inbox/Nextcloud integrieren | Done 2026-06-30: Status- und Chunk-Lane bleiben rohtextfrei |
| P4 | Chat/Document Processor umstellen | Ein Extractor statt Sonderlogik |
| P5 | OCR/Vision-Fallback absichern | Optional, lokal-only, bounded |
| P6 | UI/Operator-Sichtbarkeit herstellen | Review-Gruende und Re-Extract-Aktionen sichtbar |
| P7 | Tests und Release-Gates | Regressionen fuer grosse/kaputte/scanned PDFs |

## 6. P0: Contracts und Budgets

Lieferumfang:

- `PdfExtractionBudget` mit:
  - `max_file_bytes`
  - `max_pages`
  - `max_chars`
  - `max_chars_per_page`
  - `max_seconds`
  - `max_images_per_page`
  - `ocr_enabled`
  - `ocr_max_pages`
- Warning-Codes:
  - `pdf_size_limit_exceeded`
  - `pdf_page_limit_exceeded`
  - `pdf_char_limit_exceeded`
  - `pdf_page_extract_failed`
  - `pdf_text_empty`
  - `pdf_ocr_required`
  - `pdf_ocr_blocked_by_policy`
  - `pdf_ocr_budget_exceeded`
  - `pdf_parser_failed`
  - `pdf_encrypted`
  - `pdf_partial_text`
- Einheitliche Mapping-Regeln:
  - Voller Erfolg ohne Warnungen: `completed`
  - Mindestens ein verwertbarer Textblock plus Warnung: `partial`
  - Budget vor Parserstart ueberschritten: `metadata_only`
  - Kein Text, aber OCR/Review sinnvoll: `needs_review`
  - Parser komplett unbrauchbar: `failed`

Akzeptanzkriterien:

- Status- und Warning-Codes sind in Tests fixiert.
- Keine Rohtexte erscheinen in `to_dict()`-Reports.
- Defaults passen zu bestehenden Limits:
  - Universal Inbox `max_extract_bytes`: 2 MiB
  - Chat upload default: 10 MiB
  - Personal upload default: 25 MiB

## 7. P1: Gemeinsamer PDF-Extractor

Lieferumfang:

- `extract_pdf_pages(path, budget, owner=None, policy_context=None)`
- Seitenweise `pypdf`-Iteration mit try/except pro Seite.
- Harte Abbrueche bei Budgetueberschreitung.
- Textnormalisierung pro Seite.
- Result-Objekt mit:
  - `status`
  - `page_count`
  - `processed_pages`
  - `char_count`
  - `warnings`
  - `pages`
  - `metadata`

Wichtig:

- Ein kaputter Page-Object darf nicht die vorherigen Seiten verwerfen.
- Ein komplett fehlerhaftes PDF muss als `failed` oder `needs_review`
  klassifiziert werden, nicht als leerer String.
- Der alte `extract_pdf_text` bleibt als Kompatibilitaetswrapper erhalten,
  ruft aber den neuen Extractor.

Tests:

- normales PDF mit Text
- leeres/scanned PDF
- Parser-Exception beim Oeffnen
- Page-Exception nach bereits extrahierten Seiten
- `max_pages` und `max_chars` greifen deterministisch

## 8. P2: Personal Docs und RAG

Lieferumfang:

- `src.personal_docs.load_personal_index` nutzt den neuen Extractor.
- `src.rag_vector.VectorRAG.index_personal_documents` indexiert partial PDFs.
- Leere/fehlgeschlagene PDFs werden mit Zaehlern und Warning-Metadata
  gemeldet.
- Rueckgabe von `index_personal_documents` wird erweitert:
  - `indexed_count`
  - `failed_count`
  - `skipped_count`
  - `partial_count`
  - `review_count`
  - `warnings_by_file`

Akzeptanzkriterien:

- Grosse PDFs werden nicht mehr still uebersprungen.
- Partial-Text erzeugt Chunks mit Metadaten:
  - `source`
  - `filename`
  - `type`
  - `chunk_id`
  - `pdf_status`
  - `pdf_page_start`
  - `pdf_page_end`
  - `pdf_warning_codes`
- Bestehende Search- und Owner-Isolation bleibt unveraendert.

Tests:

- PDF mit Partial-Erfolg wird indexiert.
- PDF ohne Text wird als Review/Skipped gemeldet.
- Owner-Metadata bleibt erhalten.
- Symlink-/Path-Confinement-Tests bleiben gruen.

## 9. P3: Universal Inbox und Nextcloud

Lieferumfang:

- `src.universal_inbox_extraction._read_pdf` nutzt den neuen Extractor.
- `metadata.extractor` wird differenzierter:
  - `pypdf_page_stream`
  - optional `pypdf_plus_vision`
- Warnings aus `PdfExtractionResult` werden in
  `UniversalInboxExtractionWarning` gemappt.
- Runtime-Text geht weiterhin nur ephemeral in die Pipeline.
- `src.nextcloud_chunked_extraction` bleibt rohtextfrei und nutzt nur
  Runtime-Text plus Warning-Codes.

Akzeptanzkriterien:

- Oversized bleibt `metadata_only`.
- Partial-PDFs erzeugen `partial` statt generischem `pdf_text_empty`.
- Chunk-Limit wird weiterhin als `needs_review` mit
  `chunk_limit_exceeded` persistiert.
- Ledger enthaelt keine extrahierten PDF-Inhalte.

Tests:

- oversized PDF
- partial PDF
- failed PDF
- chunk-limit fuer langes Runtime-Ergebnis
- JSONL-Ledger enthaelt keine Rohtexte

## 10. P4: Chat und Document Processor

Lieferumfang:

- `src.document_processor._process_pdf` wird Wrapper um den neuen Extractor.
- Bestehende Markdown-Ausgabe bleibt kompatibel:
  - `[PDF content]:`
  - `[Page N text]:`
  - Truncation-Hinweise
- Document Viewer speichert volleren Body, Inline-Chat bleibt budgetiert.
- `/api/document/{doc_id}/extract-pdf-text` nutzt denselben Extractor.

Akzeptanzkriterien:

- Chat-Antworten verlieren keine vorhandene PDF-Funktionalitaet.
- Lange PDFs bekommen klare Marker:
  - inline gekappt
  - full text im Document Viewer
  - partial extraction bei Fehlern
- Re-Extract zeigt verwertbare Gruende bei leerem Ergebnis.

Tests:

- Plain PDF auto-doc creation
- Form-PDF intro text
- Re-Extract endpoint
- Inline truncation marker
- Privacy block bei externem Vision-Fallback

## 11. P5: OCR/Vision-Fallback

Lieferumfang:

- OCR/Vision ist opt-in per Budget/Settings.
- Nur Seiten ohne genug Text werden kandidiert.
- Page images werden hart begrenzt.
- Secure/local-only Runtime entscheidet vor dem Lesen/Encoding von Bilddaten.
- Warnings unterscheiden:
  - OCR erforderlich
  - OCR deaktiviert
  - OCR policy-blocked
  - OCR budget exceeded
  - OCR failed

Akzeptanzkriterien:

- Sensitive/local-only Sessions senden keine Seitenbilder an externe Provider.
- Ohne Vision-Modell bleibt das Ergebnis `needs_review` oder `partial`, nicht
  hard failed, wenn pypdf-Text teilweise vorhanden ist.
- OCR-Kosten sind durch Seiten- und Bildlimits kontrolliert.

Tests:

- scanned PDF ohne OCR
- scanned PDF mit mock OCR
- policy-blocked OCR
- OCR-Budget erreicht

## 12. P6: UI und Operator-Sichtbarkeit

Lieferumfang:

- Personal/RAG Upload Response zeigt:
  - indexiert
  - partial
  - review required
  - failed/skipped
- Document Viewer zeigt Extraktionsstatus und Re-Extract-Hinweis.
- Universal Inbox Review zeigt PDF-Warning-Codes.
- Nextcloud Import Report zaehlt:
  - `pdf_completed`
  - `pdf_partial`
  - `pdf_metadata_only`
  - `pdf_needs_review`
  - `pdf_failed`

Akzeptanzkriterien:

- Ein Nutzer kann erkennen, ob ein PDF nicht voll lesbar war.
- Operator kann grosse PDFs gezielt spaeter mit hoeherem Budget oder OCR
  erneut laufen lassen.
- Keine Warnung enthaelt private Rohtextauszuege.

## 13. P7: Release-Gates

Pflichttests:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_universal_inbox_extraction.py tests\test_nextcloud_chunked_extraction.py tests\test_personal_docs_pdf_index.py tests\test_rag_manager_owner_compat.py
```

Neue Tests:

- `tests/test_pdf_extraction.py`
- `tests/test_rag_pdf_partial_index.py`
- `tests/test_document_processor_pdf_extraction.py`

Manuelle Smoke-Szenarien:

- 1 kleines Text-PDF
- 1 langes Text-PDF
- 1 gescanntes PDF
- 1 bewusst kaputtes PDF
- 1 grosses PDF ueber `max_extract_bytes`
- 1 PDF mit gemischten Text- und Bildseiten

Release-Definition:

- Keine stillen PDF-Skips im Personal/RAG-Pfad.
- Universal Inbox bleibt metadata-only fuer oversized Dokumente.
- Nextcloud Ledger bleibt rohtextfrei.
- Chat/Document PDF-Flows bleiben kompatibel.
- OCR/Vision ist optional, budgetiert und privacy-gated.

## 14. Empfohlene Umsetzungsslices

### Slice A: Extractor Contract

- Done 2026-06-30: `src/pdf_extraction.py` angelegt.
- Done 2026-06-30: Result-Dataclasses und Budget-Defaults definiert.
- Done 2026-06-30: `pypdf`-Adapter ohne OCR implementiert.
- Done 2026-06-30: `tests/test_pdf_extraction.py` angelegt.
- Evidence 2026-06-30:
  `python -m pytest tests/test_pdf_extraction.py tests/test_personal_docs_pdf_index.py tests/test_universal_inbox_extraction.py -q`
  returned `27 passed, 1 warning`.

### Slice B: Personal/RAG Fix

- Done 2026-06-30: `src.personal_docs.extract_pdf_text` auf neuen Extractor umgestellt.
- Done 2026-06-30: `src.personal_docs.load_personal_index` gibt PDF-Status,
  Warncodes, Seitenanzahl und verarbeitete Seiten fuer PDF-Dateien zurueck.
- Done 2026-06-30: `src.rag_vector.VectorRAG.index_personal_documents`
  indexiert Partial-PDFs und meldet `skipped_count`, `partial_count`,
  `review_count`, `indexed_files_count` und `warnings_by_file`.
- Evidence 2026-06-30:
  `python -m pytest tests/test_rag_pdf_partial_index.py tests/test_pdf_extraction.py tests/test_personal_docs_pdf_index.py tests/test_nextcloud_ingestion_integration.py tests/test_rag_manager_owner_compat.py tests/test_universal_inbox_extraction.py -q`
  returned `36 passed, 1 warning`.

### Slice C: Universal Inbox Integration

- Done 2026-06-30: `_read_pdf`/PDF-Packet-Pfad auf neuen Extractor umgestellt.
- Done 2026-06-30: Status-/Warning-Mapping getestet fuer completed,
  partial, needs_review und failed PDFs.
- Done 2026-06-30: Nextcloud Chunk-Lane bleibt rohtextfrei und uebernimmt
  PDF-Warning-Codes.
- Evidence 2026-06-30:
  `python -m pytest tests/test_universal_inbox_extraction.py tests/test_nextcloud_chunked_extraction.py tests/test_pdf_extraction.py tests/test_rag_pdf_partial_index.py tests/test_personal_docs_pdf_index.py -q`
  returned `40 passed, 1 warning`.

### Slice D: Document Processor Wrapper

- `_process_pdf` auf Extractor-Ergebnis abbilden.
- Bestehende Chat- und Document-Marker erhalten.
- Re-Extract endpoint pruefen.

### Slice E: OCR/Vision

- Nur nach den vorherigen Slices.
- Policy-Gate vor Bilddatenzugriff.
- Mockbare OCR/Vision-Schnittstelle.

## 15. Offene Entscheidungen

- Sollen grosse PDFs im Personal/RAG-Pfad standardmaessig partial indexiert
  werden, auch wenn sie ueber dem Universal-Inbox-Limit liegen?
- Brauchen wir separate Limits fuer lokale Admin-Importe und Chat-Uploads?
- Soll PyMuPDF als optionaler Reparatur-/Rendering-Fallback erlaubt werden,
  oder bleibt es wegen AGPL strikt Viewer/Form-only?
- Wo soll der Nutzer Re-Extract mit hoeherem Budget ausloesen koennen:
  Document Viewer, Personal Docs, Universal Inbox Review oder nur Operator CLI?

## 16. Naechster konkreter Schritt

Mit Slice A starten:

1. `src/pdf_extraction.py` mit Dataclasses, Budget und pypdf-Seitenextraktion.
2. Kompatibilitaetswrapper in `src.personal_docs.extract_pdf_text`.
3. Tests fuer normal, leer, partial, failed und budget-limited PDFs.

Danach erst RAG und Universal Inbox anschliessen.
