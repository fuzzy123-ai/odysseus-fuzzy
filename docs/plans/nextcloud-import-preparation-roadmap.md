# Nextcloud Import Preparation Roadmap

Status: safe backend path partial; bounded local inventory smoke passed; live upload smoke remains operator-gated; P0/P1/P3 backend prep advanced
Owner: operator + Odysseus
Scope: `C:\Users\nkatz\Nextcloud` local synced source
Mode: dry-run first, review-gated execution, no delete by default

## 0. Aktueller L1 Live-Write-Stand

Stand: 2026-06-29

Der sichere Backend-Pfad fuer Universal Inbox -> Nextcloud ist vorbereitet:

- Telegram-Dateianhaenge laufen in die Universal Inbox.
- `/review ok` bestaetigt die Review der letzten offenen Inbox-Datei.
- Ohne Live-Gates bleibt die Nextcloud-Ablage ein Dry-run und meldet dem Nutzer,
  dass Live-Copy auf Operator-Go wartet.
- Mit beiden expliziten Runtime-Gates kann der Telegram-Review-Pfad eine
  WebDAV-Copy ausfuehren:
  - `UNIVERSAL_INBOX_NEXTCLOUD_LIVE_WRITE_ENABLED=true`
  - `UNIVERSAL_INBOX_NEXTCLOUD_OPERATOR_LIVE_GO=true`
- Die WebDAV-Konfiguration kommt aus Runtime-Env:
  - `NEXTCLOUD_WEBDAV_BASE_URL`
  - `NEXTCLOUD_WEBDAV_USERNAME`
  - `NEXTCLOUD_WEBDAV_APP_PASSWORD`
  - optional `NEXTCLOUD_WEBDAV_ROOT`
- Die Env-Factory baut nur den Client und fuehrt dabei keine Netzwerkaktion aus.
- Copy bleibt review-gated, operator-gated, copy-only, no-delete,
  no-overwrite und sidecar-redacted.
- Erfolgreiche Live-Copy meldet Telegram erst nach Size-Verifikation:
  `Nextcloud-Ablage wurde kopiert und verifiziert`.

Fokussierte Evidence:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_nextcloud_webdav_client.py tests\test_universal_inbox_nextcloud_transfer.py tests\test_universal_inbox_extraction.py tests\test_universal_inbox_memory_write_intent.py tests\test_universal_inbox_worker.py tests\test_telegram_plugin.py -q
```

Ergebnis am 2026-06-29: `103 passed, 1 warning`.

Lokaler Inventory-Smoke 2026-07-03:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe scripts\nextcloud_import_dry_run.py --root <runtime-nextcloud-root> --ledger-path .tmp\nextcloud-dryrun\odysseus-nextcloud-import-dryrun-20260703.jsonl --batch-limit 25 --scan-profile documents_only --pilot-batch-limit 10 --max-samples 0 --format markdown
```

Ergebnis: `25` Dateien metadata-only gescannt, `20` Inventory-Datensaetze,
`20` Review-Kandidaten, `0` Dokument-Pilot-Auswahl, `0` Long Paths. Der
Report enthielt keine Samples, Rohinhalte oder Secrets. Das temporaere Ledger
wurde nach dem Smoke entfernt, damit keine privaten Pfad-Metadaten im Repo
oder Arbeitsbaum verbleiben.

Offen:

- Ein bounded Live-Smoke gegen die echte Nextcloud ist noch nicht ausgefuehrt.
- Live-Smoke braucht konkrete Runtime-Env auf dem Server, dedizierten
  Nextcloud-User und explizites Operator-Go.
- Der ZIP-Executor ist als review-gated Backend-Baustein vorhanden; ein
  Live-run gegen echte Nextcloud-/Nutzerdateien bleibt operator-gated.
- Delete, Move, Rename, Overwrite, Tag-Write, Sidecar-Write ausserhalb des
  Transfer-Sidecars und Memory/RaptorGraph-Live-Writes bleiben verboten oder
  separat gegated.

## 1. Ausgangslage

Die lokale Nextcloud-Struktur umfasst aktuell ungefaehr:

- 59.186 Dateien
- 6.818 Ordner
- 120,61 GiB
- 281 unterschiedliche Dateiendungen
- 1.436 Pfade ueber 240 Zeichen
- 2.137 Dateien ohne Endung
- 123 Zero-byte-Dateien
- keine erkannten Offline-/Cloud-Placeholder-Dateien
- keine erkannten Reparse Points/Symlinks

Grobe Dateigruppen:

- Bilder: 19.365 Dateien, 43,34 GiB
- Videos: 451 Dateien, 27,04 GiB
- Archive: 120 Dateien, 31,13 GiB
- Dokumente: 6.686 Dateien, 9,59 GiB
- Code/Web/Config: 21.695 Dateien, 0,52 GiB
- Praesentationen: 295 Dateien, 2,77 GiB
- Tabellen: 136 Dateien, 0,05 GiB
- Installer/Apps: 311 Dateien, 0,50 GiB

Wichtige Top-Level-Bereiche:

- `Daten`: 49.323 Dateien, 84,09 GiB
- `Schule`: 2.913 Dateien, 3,24 GiB
- `Niklas&Maaike`: 2.178 Dateien, 19,75 GiB
- `GoDot`: 1.522 Dateien, 0,50 GiB
- `Privat`: 948 Dateien, 1,58 GiB
- `Python`: 865 Dateien, 0,07 GiB
- `Camera Uploads`: 684 Dateien, 2,10 GiB

## 2. Zielbild

Die Nextcloud-Importpipeline soll die Quelle nicht blind in Memory/RAG kippen, sondern pro Datei oder Ordner eine sichere Entscheidung treffen:

- semantisch extrahieren und indexieren
- nur Metadaten merken
- als Software-Bundle erkennen, ZIP planen und Profil merken
- als Medienbestand nur katalogisieren
- als Archiv separat reviewen
- als sensitive/private Bereich lokal-only behandeln
- als unsupported oder riskant in Review Queue geben

Originaldateien werden im Standardpfad nicht geloescht, nicht verschoben und nicht ueberschrieben. Live-Aktionen brauchen explizites Operator-Go.

## 3. Grundprinzipien

- Dry-run vor jeder Mutation.
- Copy-only vor move/delete.
- Kein Import von Binaries ins Gedächtnis.
- Keine Rohinhalte in Ledgers, Sidecars oder Review-Artefakten.
- Lange Pfade werden als normales Szenario behandelt, nicht als Ausnahme.
- Sensitive Roots werden konservativ behandelt.
- Software-/Toolchain-Ordner werden als Kontextprofil + ZIP-Plan behandelt.
- Medien werden nicht automatisch OCR/transkribiert.
- Archive werden nicht automatisch entpackt.
- Jeder Schritt muss resumable sein.

## 4. Phasenuebersicht

| Phase | Ziel | Ergebnis |
|---|---|---|
| P0 | Scope und Safety einfrieren | Importregeln, Exclusions, sensitive Roots |
| P1 | Metadaten-Inventar validieren | reproduzierbarer Dry-run-Scan |
| P2 | Dateityp- und Ordnerklassifizierung | Entscheidungen pro Datei/Ordner |
| P3 | Software-Bundle-Pfad fertigstellen | ZIP + Sidecar + Memory-Profil |
| P4 | Dokumentextraktion vorbereiten | sichere Extractor-Lanes |
| P5 | Privacy-/Memory-Gates schaerfen | local-only, review, memory-write |
| P6 | Pilotimport | kleiner kontrollierter Batch |
| P7 | Vollimport in Wellen | resumable, beobachtbar, abbrechbar |
| P8 | Nachpflege | Review Queue, Reindex, Cleanup-Pläne |

## 5. P0 - Scope, Policies, Exclusions

### Aufgaben

- Importwurzel bestaetigen: `C:\Users\nkatz\Nextcloud`.
- Ledger- und Output-Ziele ausserhalb der Nextcloud festlegen.
- Sensitive Roots definieren.
- Exclusion-Regeln fuer technische Dateien festlegen.
- Decide: lokale Sync-Quelle zuerst, WebDAV spaeter.

### Empfohlene Sensitive Roots

Initial konservativ:

- `Privat`
- `Niklas&Maaike`
- `Photos`
- `Camera Uploads`
- optional `Schule`, falls Schueler-/Kollegiumsdaten enthalten sind

Diese Roots sollten standardmaessig `local_model_only=True` erhalten und nicht automatisch in externe Modelle oder unreviewte Memory-Writes gehen.

### Harte Exclusions

Immer ausschliessen:

- `.sync_*.db`
- `.sync_*.db-shm`
- `.sync_*.db-wal`
- `.nextcloudsync.log`
- `Desktop.ini`
- `Thumbs.db`
- `.DS_Store`
- temporaere Dateien: `.tmp`, `.temp`, `.part`, `.partial`, `.crdownload`, `.download`, `~$*`
- Zero-byte-Dateien, ausser explizit fuer Audit erwuenscht

### Binaries und Software

Nicht semantisch indexieren:

- `.exe`, `.dll`, `.msi`, `.bat`, `.cmd`, `.ps1`, `.sh`, `.scr`, `.com`, `.jar`

Stattdessen:

- Software-Bundle erkennen
- Metadatenprofil erstellen
- ZIP-Erstellung planen
- Sidecar `.odysseus.json` planen
- Memory bekommt nur das Profil

### Akzeptanzkriterien

- Exclusion-Liste ist versioniert.
- Sensitive Roots sind explizit konfiguriert.
- Importlauf kann mit `dry_run=True` gestartet werden.
- Ledger liegt nicht innerhalb der gescannten Nextcloud.

## 6. P1 - Inventar-Scan

### Bestehender Stand

Vorhanden:

- `src/nextcloud_resumable_scanner.py`
- Append-only BigData Ledger
- Metadaten-Fingerprint aus Pfad, Groesse, mtime
- keine Inhaltslesung
- resumable durch vorhandene Inventory-Records

### Anpassungen

- Scanner um Dateityp-Klassifizierung erweitern.
- Exclusion-Regeln bereits im Scanner anwenden.
- Long-Path-Metriken pro Record oder Summary erfassen.
- Hidden/System-Dateien markieren oder ausschliessen.
- Optional `scan_profile` speichern: `full`, `documents_only`, `software_detection`, `media_catalog`.

### Output

Inventory-Records mit:

- relative path
- size
- mtime
- extension
- file category
- privacy class
- exclusion status
- long path flag
- top-level root

### Akzeptanzkriterien

- Voller Dry-run auf 59k Dateien laeuft ohne Fehler.
- Ausgeschlossene Dateien erscheinen entweder gar nicht oder als `skipped` mit Grund.
- Kein Dateihash ueber Inhalt im Metadaten-Scan.
- Scan ist wiederholbar und ueberspringt vorhandene Records.

## 7. P2 - Klassifizierung

### Dateiklassen

Die Pipeline soll unterscheiden:

- `text_extractable`
- `document_extractable`
- `office_pending`
- `media_metadata`
- `audio_transcribable_review`
- `video_metadata`
- `archive_review`
- `software_bundle_candidate`
- `dangerous_or_binary`
- `unsupported`
- `empty`
- `sync_metadata`

### Ordnerklassen

Ordner sollen aggregiert klassifiziert werden:

- `software_bundle`
- `toolchain_bundle`
- `node_dependency_bundle`
- `installer_package`
- `media_collection`
- `document_collection`
- `mixed_review`
- `sensitive_collection`
- `archive_collection`

### Heuristiken fuer Software-Bundles

Marker:

- viele `.exe/.dll`
- `bin`, `lib`, `tools`, `drivers`, `jre`, `jdk`, `node_modules`, `venv`
- Namen wie `msp430`, `energia`, `arduino`, `setup`, `installer`, `pointofix`, `bridgebuilder`
- Manifests wie `package.json`, `requirements.txt`, `pyproject.toml`, `setup.py`

### Akzeptanzkriterien

- `Daten/Referendariat/NWT von Timo/MSP430` wird als Toolchain erkannt.
- `Python/mobile-game-engine/node_modules` wird als Node Dependency Bundle erkannt.
- Einzelne Installer werden als `installer_package` erkannt.
- Dokumentordner mit einer einzelnen `.exe` werden nicht versehentlich komplett als Software-Bundle wegklassifiziert.

## 8. P3 - Software-Bundle ZIP-Pfad

### Bestehender Stand

Neu angelegt:

- `src/nextcloud_software_archives.py`
- `tests/test_nextcloud_software_archives.py`

Der Baustein erkennt Software-Bundles aus Inventory-Metadaten und erzeugt einen Dry-run-Plan:

- `create_zip`
- `write_sidecar`
- `memory_profile`

Noch nicht enthalten:

- echte ZIP-Erstellung gegen Nutzerdateien oder Nextcloud-Zielpfade
- Review-UI/Operator-Freigabe

Backend-Stand 2026-06-30:

- `src/nextcloud_software_archive_executor.py` fuehrt review-gated ZIP,
  Sidecar und Manifest-Erstellung fuer Scratch-/lokale Output-Roots aus.
- Live-Ausfuehrung blockiert ohne `review_approved=True` und
  `operator_live_go=True`.
- Overwrite bleibt verboten, Originaldateien werden nicht geloescht.
- Sidecar und Manifest enthalten nur redigierte Metadaten, keine Rohinhalte und
  keine absoluten Source-Pfade.

### Zielverhalten

Beispiel:

Quelle:

`Daten/Referendariat/NWT von Timo/MSP430`

Ziel:

`Software Archives/daten-referendariat-nwt-von-timo-msp430.zip`

Sidecar:

`Software Archives/daten-referendariat-nwt-von-timo-msp430.zip.odysseus.json`

Memory-Profil:

`Software bundle at ... classified as toolchain_bundle; 214 files, 214 executable/library-like files, 149.17 MiB. Archive plan is metadata-only and review-gated.`

### Sidecar-Inhalt

Das Sidecar sollte enthalten:

- schema
- source folder
- archive path
- archive created at
- file count
- byte size
- executable suffix counts
- top extensions
- sample paths
- bundle kind
- reason codes
- privacy classification
- memory summary
- original files retained
- deletion not performed
- operator approval metadata

### ZIP-Manifest

Optional, aber empfohlen:

- `ODYSSEUS_MANIFEST.json` im ZIP
- gleicher Inhalt wie Sidecar, aber ohne absolute Hostpfade
- keine Rohinhalte
- kein Secret-Material

### Akzeptanzkriterien

- ZIP-Executor kann Dry-run und Live-run. Backend: done fuer lokale
  Scratch-/Output-Roots.
- Live-run ist ohne `review_approved=True` blockiert. Backend: done.
- ZIP-Executor loescht keine Originale. Backend: done.
- ZIP-Executor ueberschreibt keine existierenden ZIPs. Backend: done.
- Sidecar und Manifest enthalten keine Rohdateiinhalte. Backend: done.
- Software-Bundles werden nicht an RAG-Textindex uebergeben.

## 9. P4 - Dokumentextraktion

### Prioritaet 1

Sicher extrahieren:

- `.txt`
- `.md`
- `.json`
- `.csv`
- `.tsv`
- `.html`
- `.htm`
- `.xml`
- `.pdf`
- `.docx`

### Prioritaet 2

Review oder spaeter:

- `.odt`
- `.rtf`
- `.pptx`
- `.xlsx`
- `.xls`
- `.epub`

### Nicht automatisch extrahieren

- Bilder
- Videos
- Audio
- Archive
- Binaries
- grosse unbekannte Dateien

### Grenzen

Empfohlen fuer ersten Import:

- `max_extract_bytes`: 2 MiB bis 10 MiB je nach Dateityp
- PDF-Seitenlimit oder Textlimit
- Chunklimit pro Datei
- Timeout pro Datei
- Fehler als `retryable` oder `needs_review`

### Akzeptanzkriterien

- `.pdf` und `.docx` liefern Text oder Review-Grund.
- Grosse Dateien werden metadata-only behandelt.
- Extrahierter Rohtext wird nicht im Ledger persistiert.
- Chunked Extraction speichert nur Chunk-Refs/Hashes.

## 10. P5 - Privacy, Memory und RAG Gates

### Privacy-Regeln

Jeder Record braucht:

- `privacy_class`
- `classification`
- `local_model_only`
- `required_model_scope`
- `memory_write_candidate`
- `rag_index_candidate`
- `review_required`

### Memory-Strategie

RAG bekommt:

- extrahierte Chunks nur aus erlaubten Dokumenten
- owner metadata
- source provider metadata
- privacy metadata

Memory bekommt:

- keine Binaries
- keine Roharchive
- keine unreviewten sensitiven Inhalte
- Software nur als Profil
- Medien nur als Katalogprofil, bis OCR/STT freigegeben ist

### Akzeptanzkriterien

- Sensitive Roots gehen nicht automatisch in API-Modelle.
- `default_unknown_private=True` kann unbekannte Bereiche blocken.
- Memory-Writes sind review-gated.
- RAG-Metadaten enthalten Privacy-Flags.

## 11. P6 - Pilotimport

### Pilotumfang

Kleine Welle, z.B.:

- 100 bis 500 Dateien
- nur Dokumenttypen
- keine Medien
- keine Archive
- keine Software live zippen, nur ZIP-Pläne

### Pilotbereiche

Geeignet:

- ein kleiner Unterordner aus `Daten`
- ein klarer Dokumentordner ohne sensible Daten
- ein Software-Bundle als ZIP-Dry-run-Probe

Nicht geeignet fuer ersten Pilot:

- `Privat`
- `Photos`
- `Camera Uploads`
- grosse Video-/Bildsammlungen

### Metriken

Erfassen:

- scanned
- skipped
- planned software archives
- extracted
- metadata-only
- needs_review
- failed
- retryable
- indexed chunks
- elapsed time
- top failure reasons

### Akzeptanzkriterien

- Pilot ist wiederholbar.
- Keine Originaldatei wurde veraendert.
- Review Queue ist nachvollziehbar.
- Memory/RAG enthalten nur erwartete Eintraege.
- Rollback fuer Index-Eintraege ist moeglich.

## 12. P7 - Vollimport in Wellen

### Wellenplanung

Welle 1:

- technische Exclusions
- Software-Bundle-Pläne
- Dokumente aus nicht-sensitiven Bereichen

Welle 2:

- `Schule`, falls freigegeben
- Office-Formate mit Review
- groessere PDFs

Welle 3:

- sensitive Roots lokal-only
- manuell freigegebene Teilbereiche

Welle 4:

- Medienkatalog
- Bilder/Videos nur Metadaten
- OCR/STT nur nach gesondertem Go

Welle 5:

- Archive
- nur mit expliziter Entpack-/Katalogstrategie

### Batch-Regeln

- Batch-Groesse begrenzen.
- Pro Batch Summary erzeugen.
- Bei Fehlerquote ueber Grenzwert abbrechen.
- Bei unbekannter Dateiklasse abbrechen oder Review.
- Keine destructive actions.

### Akzeptanzkriterien

- Import kann jederzeit angehalten und fortgesetzt werden.
- Keine doppelten Indexeintraege.
- Fehlerhafte Dateien blockieren nicht den ganzen Lauf.
- Operator kann Review Queue abarbeiten.

## 13. P8 - Nachpflege

### Review Queue

Nach dem Import:

- `needs_review` gruppieren nach Ursache
- Software-ZIP-Pläne freigeben oder verwerfen
- sensitive Items manuell freigeben
- unsupported Typen entscheiden

### Indexpflege

- RAG-Stats pruefen
- owner scope pruefen
- Quellen entfernen koennen
- Reindex einzelner Ordner
- Rename-/Move-Mapping vorbereiten

### Nextcloud-Aufraeumen

Erst nach erfolgreichem Import:

- manuelle Loeschliste erstellen
- ZIP-Archive pruefen
- Originale niemals automatisch loeschen
- optional: separate Cleanup-Roadmap

## 14. Technische Umsetzungsliste

### Muss

- Scanner-Exclusions konfigurieren.
- Dateitypentscheidung in Nextcloud-Inventory aufnehmen.
- Software-Bundle-Planner in Pipeline verdrahten.
- ZIP-Executor als review-gated Dry-run/Live-run bauen.
- Sidecar-Writer bauen.
- Manifest-in-ZIP bauen.
- Dokumentextraktion batchfaehig machen.
- Review Queue fuer Software/Profile erweitern.
- Import-Konfiguration als Datei versionieren.

### Sollte

- Long-path handling explizit testen.
- Windows-Pfadnormalisierung testen.
- Batch-Reports als Markdown und JSON schreiben.
- Operator-Summary pro Welle erzeugen.
- Memory-/RAG-Dedupe verbessern.
- Medien-Katalogprofil bauen.
- Archiv-Katalogprofil bauen.

### Spaeter

- OCR fuer ausgewaehlte Bilder/PDF-Seiten.
- STT fuer Audio/Video.
- WebDAV-Provider statt lokaler Sync-Quelle.
- UI fuer Nextcloud Import Control.
- Live Nextcloud Tag Projection.

## 15. Konfigurationsvorschlag

```json
{
  "schema": "odysseus.nextcloud_import_config.v1",
  "source_root": "C:/Users/nkatz/Nextcloud",
  "source_id": "nextcloud-main",
  "mode": "dry_run",
  "default_unknown_private": true,
  "sensitive_roots": [
    "Privat",
    "Niklas&Maaike",
    "Photos",
    "Camera Uploads"
  ],
  "exclude_names": [
    "Desktop.ini",
    "Thumbs.db",
    ".DS_Store",
    ".nextcloudsync.log"
  ],
  "exclude_globs": [
    ".sync_*.db",
    ".sync_*.db-shm",
    ".sync_*.db-wal",
    "~$*",
    "*.tmp",
    "*.temp",
    "*.part",
    "*.partial",
    "*.crdownload",
    "*.download"
  ],
  "binary_extensions": [
    ".exe",
    ".dll",
    ".msi",
    ".bat",
    ".cmd",
    ".ps1",
    ".sh",
    ".scr",
    ".com",
    ".jar"
  ],
  "document_extensions_initial": [
    ".txt",
    ".md",
    ".json",
    ".csv",
    ".tsv",
    ".html",
    ".htm",
    ".xml",
    ".pdf",
    ".docx"
  ],
  "software_archives": {
    "enabled": true,
    "dry_run": true,
    "target_root": "Software Archives",
    "write_sidecar": true,
    "write_manifest_inside_zip": true,
    "delete_original": false,
    "overwrite_existing": false,
    "review_required": true
  },
  "extraction": {
    "max_extract_bytes": 2097152,
    "max_chunk_chars": 4000,
    "max_chunks_per_item": 256
  }
}
```

## 16. Erste konkrete Arbeitsreihenfolge

1. Import-Konfigurationsdatei anlegen. Done.
2. Scanner um Exclusions und File-Type-Metadaten erweitern. Done.
3. Dry-run Inventory gegen die lokale Nextcloud-Quelle laufen lassen. Done fuer
   bounded metadata-only Batch-Smoke am 2026-07-03; Vollinventar bleibt
   operator-gated.
4. Software-Bundle-Planner gegen das Inventory laufen lassen. Done fuer
   vorhandene Ledger/Dry-run-Pipeline.
5. Report erzeugen: Software ZIP-Kandidaten, Dokumentkandidaten,
   Review-Kandidaten. Done fuer Dry-run-Pipeline.
6. ZIP-Executor im Dry-run bauen. Done.
7. ZIP-Executor mit Mini-Fixture testen. Done.
8. Sidecar/Manifest-Format finalisieren. Backend-MVP done; UI/Operator-Texte
   separat.
9. Pilotimport fuer Dokumente starten. Offen, live/local-source-gated.
10. Danach erst Vollimport-Wellen planen. Offen.

## 17. Go/No-Go Gates

### Go fuer Inventory

- Source root bestaetigt
- Ledger ausserhalb der Source
- Exclusions aktiv
- Dry-run aktiv

### Go fuer ZIP Live-run

- Software plan reviewed
- target path reviewed
- no overwrite
- no delete
- sidecar enabled
- manifest enabled
- operator_live_go=True

### Go fuer RAG/Memory

- file type extractable
- privacy gate erlaubt
- source owner gesetzt
- raw text nicht im Ledger
- batch summary plausibel

### No-Go

- Ledger in Nextcloud-Root
- unbekannter sensitive Root
- destructive action
- overwrite enabled
- unreviewed binary import
- content persisted in metadata/ledger
- hohe Fehlerquote im Pilot

## 18. Offene Entscheidungen

- Soll `Schule` standardmaessig sensitive sein?
- Sollen Bilder nur katalogisiert oder spaeter OCR-fähig gemacht werden?
- Sollen Videos komplett ignoriert oder nur Metadatenprofile bekommen?
- Sollen grosse ZIPs nur katalogisiert oder irgendwann entpackt werden?
- Wie gross darf ein Dokument fuer automatische Extraktion sein?
- Wo sollen Software-ZIP-Archive physisch liegen?
- Soll nach erfolgreichem ZIP eine manuelle Loeschliste erzeugt werden?

## 19. Aktueller naechster Schritt

Als naechstes sollte der lokale Inventory-Dry-run bewusst auf ein groesseres,
aber weiter metadata-only Batch-Fenster erweitert werden. Danach koennen
Dokument-Pilotimport und Live-Upload-Smoke separat freigegeben werden. UI
bleibt ausserhalb dieser Backend-Roadmap.
