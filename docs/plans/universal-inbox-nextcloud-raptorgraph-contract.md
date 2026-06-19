# Universal Inbox Nextcloud RaptorGraph Contract

Stand: 2026-06-18

Status: planning contract for the Nextcloud-backed universal inbox intake pipeline

## Zweck

Die Universal Inbox ist der zentrale Eingang fuer beliebige Nutzerdateien. Der Nutzer soll eine Datei in eine Nextcloud-Inbox legen koennen, ohne vorher entscheiden zu muessen, ob es ein Dokument, eine Rechnung, ein Vertrag, ein Projektartefakt, eine Memory-Quelle, eine Research-Quelle, ein Bild, ein Export oder ein sonstiger Anhang ist.

Odysseus verarbeitet diese Datei kontrolliert:

1. Datei entdecken.
2. Typ, Name und Inhalt analysieren.
3. Sinnvollen Ablageort bestimmen.
4. Datei sicher ablegen oder fuer Review markieren.
5. Tags und Metadaten schreiben.
6. RaptorGraph-/Derived-Memory-Eintraege mit Provenance erzeugen.
7. Auditierbaren Status im Ledger hinterlassen.

## Grundprinzip

Nextcloud ist der private Datei- und Sync-Layer. Odysseus ist die Intake-, Analyse-, Routing- und Memory-Schicht.

Die Originaldatei bleibt Quelle. RaptorGraph, Vektorindex, Zusammenfassungen, Tags und Klassifikationen sind abgeleitete Daten. Sie duerfen repariert, neu gebaut oder korrigiert werden, aber sie ersetzen nicht die Originaldatei.

## Security Boundary

Odysseus greift auf Nextcloud nur ueber einen designierten Nextcloud-User zu.

Empfohlener Benutzer:

```text
odysseus-intake
```

Der designierte User:

- ist nicht der menschliche Admin-User.
- nutzt ein eigenes App-Passwort.
- bekommt nur die benoetigten Shares.
- hat initial keine Loeschrechte.
- darf lesen, kopieren, neue Dateien erzeugen, Tags/Metadaten setzen und Review-Artefakte schreiben, soweit Nextcloud-Rechte das erlauben.
- darf keine Originaldateien endgueltig loeschen.

No-Delete-Regel:

- Keine automatische Loeschung durch Odysseus.
- Keine stillen Moves, solange Move/Rename technisch eine Delete-Semantik haben kann.
- Initiale Ablage erfolgt per Kopie oder durch Schreiben in neue Zielbereiche.
- Das Original bleibt in der Inbox oder wird nur durch einen spaeteren, separaten Operator-Cleanup mit Review bewegt.

## Speicherrollen

Dieses Dokument folgt dem Memory Storage Roles Contract:

- Originaldatei: Primaerquelle fuer Dokumentwissen.
- Nextcloud-Tags und Sidecars: sichtbare und maschinenlesbare Metadaten.
- Intake Ledger: operative Wahrheit fuer Status, Hashes, Routing und Fehler.
- Knowledge/RAG Index: abgeleiteter Dokument-Wissensindex.
- RaptorGraph: separate Relations-/Summary-Ebene, nicht Primaerwahrheit.
- Personal Memory: getrennt vom Dokument-/Inbox-Wissen.

RaptorGraph darf Aussagen ueber Dokumente, Beziehungen, Tags und Ablageorte speichern, aber nicht als Ersatz fuer die Datei oder fuer Personal Memory verkauft werden.

## Zielordner

Empfohlene Nextcloud-Struktur:

```text
AI Inbox/
  Incoming/
  Needs Review/
  Processed/
  Failed/
  Metadata/

Documents/
  Projects/
  People/
  Organizations/
  Contracts/
  Invoices/
  Research/
  Manuals/
  Archive/

AI Memory/
  Inbox/
  Review Queue/
  Generated/
  Published/
  Canonical/
```

Ordnerrollen:

- `AI Inbox/Incoming/`: Drop-Zone fuer neue Dateien.
- `AI Inbox/Needs Review/`: Routing unsicher oder Policy-Gate erforderlich.
- `AI Inbox/Processed/`: optionaler spaeterer Original-Ablagebereich nach menschlicher Freigabe.
- `AI Inbox/Failed/`: Dateien, die technisch nicht verarbeitet werden konnten.
- `AI Inbox/Metadata/`: optionale Sidecar-Dateien und Intake-Berichte.
- `Documents/...`: menschlich nutzbare Ablage.
- `AI Memory/Inbox/`: Memory-Kandidaten, nicht automatisch kanonisch.
- `AI Memory/Review Queue/`: Review- und Promotion-Kandidaten.
- `AI Memory/Canonical/`: read-only fuer Odysseus-Intake im MVP.

## Pipeline

```text
Nextcloud Inbox Drop
-> Intake Discovery
-> Intake Ledger Entry
-> File Fingerprint
-> Content Extraction
-> Type Classification
-> Semantic Analysis
-> Routing Decision
-> Policy Gate
-> Safe Placement
-> Metadata/Tag Write
-> RaptorGraph/Knowledge Write
-> Audit/Event Record
```

## Phase 1: Discovery

Der Worker scannt oder empfaengt neue Dateien aus:

```text
AI Inbox/Incoming/
```

MVP-Discovery:

- periodischer Poll ueber WebDAV/API oder lokalen Sync.
- Datei muss stabil sein, bevor sie verarbeitet wird: mtime/size zweimal gleich oder Mindestalter.
- temporaere Upload-Dateien werden ignoriert.
- versteckte Systemdateien werden ignoriert.

Ledger-Ersteintrag:

```json
{
  "provider": "nextcloud_inbox",
  "status": "pending",
  "original_path": "AI Inbox/Incoming/example.pdf",
  "sha256": "sha256:...",
  "size_bytes": 123456,
  "mtime": "2026-06-18T20:00:00Z",
  "actor": "nextcloud_user:odysseus-intake",
  "permission_scope": "no_delete"
}
```

## Phase 2: Content Extraction

Der Worker extrahiert Inhalt so weit wie moeglich.

MVP-Dateitypen:

- `.pdf`: Text aus PDF; OCR nur spaeter oder als optionaler Fallback.
- `.docx`: Text und Basis-Metadaten.
- `.doc`: best effort, ggf. Konvertierungsservice.
- `.txt`, `.md`, `.markdown`: direkt.
- `.html`, `.htm`: sichtbarer Text plus Titel.
- `.csv`, `.tsv`, `.json`: Strukturprofil, Spalten/Keys, Beispielzeilen.
- `.rtf`: Text.

Spaetere Dateitypen:

- Bilder: OCR, Bildbeschreibung, EXIF.
- Audio: Transkription.
- Video: Metadaten, Keyframes, Transkription.
- E-Mail: `.eml`, `.msg`, Attachments.
- ZIP/Archive: Manifest und optional entpackte Child-Sources.

Extraktionsergebnis:

```json
{
  "extraction_status": "ok|partial|failed",
  "mime_type": "application/pdf",
  "detected_extension": ".pdf",
  "text_length": 42000,
  "language": "de",
  "page_count": 12,
  "extractor": "pdf_text",
  "warnings": []
}
```

## Phase 3: Analyse

Der Worker analysiert:

- Typ: Rechnung, Vertrag, Anleitung, Research, Brief, Scan, Projektartefakt, Meetingnotiz, Export, unbekannt.
- Name: sinnvoller Titel und optional Dateiname.
- Inhalt: Zusammenfassung, Schluesselthemen, Entitaeten, Daten, Fristen, Betrage, Referenzen.
- Projektbezug: vorhandenes Projekt, neues Projekt, unsicher.
- Datenschutzklasse: normal, privat, sensibel, geheim.
- Routing-Confidence.
- Indexierbarkeit.

Wichtig: Inhalt bedeutet nicht nur Dateiname oder Metadaten. Der Worker muss den extrahierten Dokumenttext auswerten und seine Confidence festhalten.

Analyseergebnis:

```json
{
  "document_type": "invoice",
  "suggested_title": "Rechnung Stadtwerke Juni 2026",
  "summary": "Rechnung fuer Stromlieferung im Juni 2026 mit Zahlungsziel ...",
  "tags": ["rechnung", "stadtwerke", "strom", "2026"],
  "entities": [
    {"type": "organization", "name": "Stadtwerke Beispielstadt"},
    {"type": "date", "value": "2026-06-30"}
  ],
  "sensitivity": "private",
  "routing_confidence": 0.91
}
```

## Phase 4: Routing

Routing ist eine Entscheidung mit Begruendung, nicht bloss ein Pfadstring.

Die Ablageregeln liegen getrennt von der Implementierung in:

```text
config/universal_inbox_routing_rules.json
```

Das MVP-Rahmensystem wird durch `src/universal_inbox_routing.py` geplant:

- nur metadata- und analysebasierte Routing-Entscheidungen.
- keine Dateioperationen im Planner.
- private und work sind getrennte Domaenen in der Rules-Datei.
- Zielpfade, Review-Grund, Sidecar-Pfad, Ledger-Status und RaptorGraph-Provenance werden strukturiert ausgegeben.

Routing-Inputs:

- Dateiinhalt
- Dateityp
- bestehende Projekt-/Archivstruktur
- Nutzerregeln
- Confidence
- Sensitivitaet
- Duplikate
- vorhandene aehnliche Dateien

Routing-Output:

```json
{
  "decision": "copy_to_target",
  "target_path": "Documents/Invoices/2026/Stadtwerke/Rechnung Juni 2026.pdf",
  "reason": "Document classified as invoice with high confidence and matching organization/date.",
  "confidence": 0.91,
  "requires_review": false
}
```

Review-Gate:

- Confidence unter Schwellwert.
- Sensible oder geheime Datei.
- moegliches Duplikat.
- Zielpfad unsicher.
- Dateityp nur teilweise extrahiert.
- Konflikt mit existierender Datei.

Bei Review:

```text
AI Inbox/Needs Review/
```

und ein Review-Artefakt:

```text
AI Memory/Review Queue/<date>-<slug>.md
```

oder spaeter eine Deck-Karte.

## Phase 5: Safe Placement

MVP erlaubt:

- Datei in Zielordner kopieren.
- Zielordner erstellen, wenn erlaubt.
- Datei unter konfliktfreiem Namen schreiben.
- Sidecar-Metadaten schreiben.
- Nextcloud-Tags setzen, wenn API/Rechte vorhanden.
- Original in `Incoming` unangetastet lassen.

MVP verbietet:

- Original loeschen.
- Original still verschieben.
- existierende Datei ueberschreiben.
- Canonical Memory automatisch veraendern.
- menschliche Archivordner restrukturieren.

Konfliktstrategie:

- Wenn Ziel existiert und Hash gleich: als Duplikat markieren, keine zweite Kopie.
- Wenn Ziel existiert und Hash anders: neuen Namen mit Suffix oder Review.
- Wenn Zielordner nicht existiert und Policy unsicher ist: Review.

## Phase 6: Metadaten, Tags Und Tag Governance

Metadaten werden mehrschichtig geschrieben:

### Grundregel

Nextcloud-Tags und RaptorGraph-Tags duerfen nicht zwei unabhaengige Tag-Welten werden.

Die Regel lautet:

- Ein kanonisches Odysseus-Tag-Vokabular entscheidet, welche Tags existieren.
- Nextcloud-Tags sind die menschlich sichtbare Projektion dieses Vokabulars.
- RaptorGraph speichert zusaetzlich semantische Tags, Entitaeten, Beziehungen und Confidence.
- Der Intake Ledger haelt fest, welche Tags aus welcher Quelle kamen, wohin sie projiziert wurden und wann sie geschrieben wurden.
- Manuelle Nextcloud-Tags des Nutzers werden respektiert und nicht automatisch entfernt.
- Frei generierte LLM-Tags duerfen nie direkt ungeprueft in Nextcloud geschrieben werden.

Damit bleibt Nextcloud praktisch durchsuchbar, waehrend RaptorGraph die tiefere Bedeutungsebene tragen kann.

### Tag-Klassen

```text
user
  Manuell in Nextcloud gesetzte Tags. Odysseus darf sie lesen und im Graph referenzieren,
  aber nicht automatisch entfernen oder umbenennen.

system
  Operative Odysseus-Tags, z. B. odysseus-indexed, odysseus-needs-review,
  odysseus-routed, odysseus-duplicate.

semantic
  Kuratierte Inhalts- und Projekt-Tags, z. B. rechnung, vertrag, projekt-odysseus,
  stadtwerke, steuer, versicherung.

graph_only
  Reiche interne Graph-Begriffe, Entitaeten oder Beziehungen, die nicht automatisch
  als Nextcloud-Tag sichtbar werden.
```

### Kanonisches Tag-Vokabular

Ein Tag darf erst als Nextcloud-Tag geschrieben werden, wenn es im Vokabular oder in einer Mapping-Regel erlaubt ist.

Beispiel:

```json
{
  "canonical_tag": "invoice",
  "label_de": "rechnung",
  "nextcloud_tag": "rechnung",
  "tag_class": "semantic",
  "graph_label": "document_type:invoice",
  "aliases": ["invoice", "rechnung", "faktura"],
  "min_confidence_for_nextcloud": 0.82,
  "requires_review_when_sensitive": true
}
```

Mapping-Regeln:

- Synonyme werden auf ein kanonisches Tag normalisiert.
- Nextcloud-Tags bekommen kurze, nutzbare Namen.
- RaptorGraph kann praezisere Labels, Kanten und Entitaeten speichern.
- Niedrige Confidence erzeugt `odysseus-needs-review`, aber keine neuen semantischen Nextcloud-Tags.
- Sensible Dokumente duerfen Tags nur nach Policy oder Review bekommen.

### Nextcloud Tags

Fuer menschlich sichtbare Klassifikation:

- `rechnung`
- `vertrag`
- `projekt-<name>`
- `odysseus-needs-review`
- `odysseus-indexed`
- `odysseus-routed`
- `sensibel`

Nextcloud-Tag-Regeln:

- Nur erlaubte Tags aus dem kanonischen Vokabular werden geschrieben.
- Bestehende manuelle Tags bleiben erhalten.
- Odysseus darf eigene `odysseus-*` Systemtags aktualisieren.
- Entfernen von Tags ist im MVP verboten, ausser fuer eindeutig eigene temporare Systemtags nach expliziter Policy.
- Konflikte oder unsichere Klassifikationen landen in Review.

### RaptorGraph Tags Und Beziehungen

RaptorGraph darf mehr speichern als Nextcloud:

- dokumenttypische Tags
- erkannte Entitaeten
- Projektbeziehungen
- Fristen, Betraege, Orte, Personen, Organisationen
- Klassifikations-Confidence
- Tag-Herkunft und Mapping

Beispiel:

```text
document -> tagged_with -> tag:rechnung
document -> classified_as -> document_type:invoice
document -> mentions -> organization:Stadtwerke Beispielstadt
document -> has_due_date -> date:2026-06-30
document -> has_tag_projection -> nextcloud_tag:rechnung
```

### Tag Mapping Im Ledger

Der Ledger verbindet Nextcloud und RaptorGraph:

```json
{
  "tag_state": {
    "source_user_tags": ["privat"],
    "proposed_tags": [
      {
        "canonical_tag": "invoice",
        "nextcloud_tag": "rechnung",
        "graph_label": "document_type:invoice",
        "confidence": 0.91,
        "source": "content_analysis"
      }
    ],
    "applied_nextcloud_tags": ["rechnung", "odysseus-indexed"],
    "graph_tags": ["tag:rechnung", "document_type:invoice"],
    "review_required": false,
    "updated_at": "2026-06-18T20:00:00Z"
  }
}
```

Warum das nicht doppelt gemoppelt ist:

- Nextcloud-Tags helfen beim Finden, Filtern und manuellen Arbeiten in Nextcloud.
- RaptorGraph erklaert Bedeutung, Beziehungen und Provenance.
- Das Mapping verhindert Mischmasch und macht Rebuilds moeglich.

### Sidecar

Maschinenlesbar, neben der Datei oder unter `AI Inbox/Metadata/`:

```text
<sha256>.odysseus.json
```

Sidecar-Inhalt:

```json
{
  "schema": "odysseus.nextcloud_inbox.v1",
  "source_hash": "sha256:...",
  "original_path": "...",
  "current_path": "...",
  "analysis": {},
  "routing": {},
  "tag_state": {},
  "created_at": "...",
  "updated_at": "..."
}
```

### Ledger

Der Ledger ist operative Wahrheit fuer Status:

- `pending`
- `extracting`
- `analyzed`
- `needs_review`
- `routed`
- `metadata_written`
- `indexed`
- `routed_indexed`
- `failed`
- `duplicate`
- `permission_denied`

## Phase 7: RaptorGraph / Derived Memory

Der Worker schreibt RaptorGraph-/Derived-Memory-Eintraege erst nach erfolgreicher Analyse und mit Provenance.

Dokument-Knoten:

```json
{
  "node_type": "document",
  "source_provider": "nextcloud_inbox",
  "actor": "nextcloud_user:odysseus-intake",
  "permission_scope": "no_delete",
  "original_path": "AI Inbox/Incoming/example.pdf",
  "current_path": "Documents/Invoices/2026/example.pdf",
  "sha256": "sha256:...",
  "document_type": "invoice",
  "title": "Rechnung Stadtwerke Juni 2026",
  "summary": "...",
  "tags": ["invoice", "stadtwerke", "2026"],
  "confidence": 0.91,
  "extracted_at": "...",
  "routed_at": "..."
}
```

Kanten:

```text
document -> stored_at -> nextcloud_path
document -> derived_from -> file_hash
document -> classified_as -> document_type
document -> tagged_with -> tag
document -> mentions -> person|organization|project|date
document -> belongs_to -> project
document -> routed_by -> routing_policy
document -> needs_review -> review_item
```

Graph-Regeln:

- RaptorGraph ist abgeleitet, nicht kanonisch.
- Jeder Knoten hat Provenance zur Originaldatei.
- Jeder Inhaltsclaim braucht Quellverweis: Pfad, Hash, Extractor, Zeitpunkt.
- Unsichere Claims werden mit Confidence und Review-Status markiert.
- Rebuild muss moeglich sein, ohne Originaldatei zu veraendern.

## Phase 8: Audit Trail

Jeder Worker-Lauf schreibt ein Audit-Event:

```json
{
  "event": "file_routed",
  "actor": "odysseus-intake",
  "source": "AI Inbox/Incoming/example.pdf",
  "target": "Documents/Invoices/2026/example.pdf",
  "sha256": "sha256:...",
  "decision_id": "...",
  "confidence": 0.91,
  "timestamp": "..."
}
```

Audit darf keine geheimen Inhalte loggen. Kurze Zusammenfassungen sind nur erlaubt, wenn sie auch im Sidecar/Graph als abgeleitete Daten vorgesehen sind.

## Automation Ohne Deck

Deck ist nicht Kern der ersten Automation.

MVP-Automation:

- Worker scannt Inbox.
- Worker analysiert und routet.
- Worker schreibt Ledger, Tags, Sidecar und RaptorGraph.
- Worker markiert Review-Faelle.

Deck spaeter:

- Review-Karten fuer unsichere Dateien.
- Projektboards aus Routing-Ergebnissen.
- Aufgaben aus Dokumenten mit Fristen.
- Operator-Queue fuer Cleanup oder Promotion.

Deck darf die Pipeline sichtbar machen, aber nicht die Pipeline ersetzen.

## Nextcloud-Zugriffsoptionen

### WebDAV/API

Vorteile:

- Kein vollstaendiger lokaler Sync noetig.
- Direkte Nextcloud-Tags und Metadaten moeglich.
- Gute Passung zum designierten App-Passwort.

Risiken:

- API-/WebDAV-Fehler muessen sauber retrybar sein.
- Rechtegranularitaet muss getestet werden.
- Grosse Dateien brauchen Streaming.

### Lokaler Sync

Vorteile:

- Dateien wirken lokal.
- Scanner/Extractor einfacher.
- Weniger Nextcloud-spezifische Logik.

Risiken:

- lokale Speicherlast.
- Sync-Latenz.
- Tags/Kommentare/Nextcloud-Metadaten schwieriger.
- No-Delete-Semantik haengt am Sync-Client und Dateisystemverhalten.

## MVP-Slices

### UIX1: Inbox Policy Contract

Ziel: Ordner, Rechte, Status, No-Delete-Regeln und Review-Gates festlegen.

Output:

- finaler User-/Share-Name
- Ordnerstruktur
- erlaubte Operationen
- verbotene Operationen
- Statusmodell

### UIX2: Intake Ledger

Ziel: Jede Inbox-Datei bekommt einen Status- und Provenance-Eintrag.

Scope:

- provider `nextcloud_inbox`
- sha256, size, mtime
- original_path, current_path
- status
- last_error
- confidence
- actor/permission_scope

### UIX3: Content Extraction MVP

Ziel: PDF, DOCX, Text, Markdown, HTML, CSV/JSON in standardisierte Text-/Metadatenpakete bringen.

Scope:

- Streaming/size limits
- extractor registry
- partial/failed states
- keine OCR-Pflicht im MVP

### UIX4: Classification And Routing

Ziel: Dokumenttyp, Titel, Inhaltssummary, Tags, Entities und Zielpfad vorschlagen.

Scope:

- deterministic rules first
- optional model-assisted classification
- confidence
- review gate

### UIX5: Safe Placement And Metadata

Ziel: Datei sicher ablegen und Metadaten sichtbar/maschinenlesbar schreiben.

Scope:

- copy only
- no overwrite
- no delete
- Nextcloud tags
- sidecar
- ledger update

### UIX6: RaptorGraph Write

Ziel: Dokumentknoten und Beziehungen mit Provenance erzeugen.

Scope:

- document nodes
- path/hash provenance
- tags/entities/projects
- no Personal Memory conflation
- rebuildable derived graph

### UIX7: Review Flow

Ziel: Unsichere Faelle in Review Queue sichtbar machen.

Scope:

- Review Queue Markdown
- status `needs_review`
- spaeter optional Deck-Karte
- kein Auto-Promotion

## Nicht-Ziele Fuer MVP

- keine automatischen Deletes
- keine stillen Moves
- keine komplette Archiv-Reorganisation
- keine automatische Canonical Promotion
- keine Pflicht zu OCR/Audio/Video
- keine serverseitige Nextcloud-App
- keine Deck-Abhaengigkeit
- keine Nutzung des menschlichen Admin-Users
- keine Veraenderung von Originaldateien ohne explizite Policy

## Offene Entscheidungen

- Finaler Name des Nextcloud-Users: `odysseus-intake` oder anderer Name?
- Welche Ordner teilt der menschliche User mit Odysseus?
- Wie erzwingen wir "keine Loeschrechte" technisch in Nextcloud?
- WebDAV/API zuerst oder lokaler Sync zuerst?
- Welche Tags sind global erlaubt?
- Wie wird das kanonische Tag-Vokabular gepflegt: Datei, Datenbank, Admin-UI oder Nextcloud-seitige Liste?
- Welche Nextcloud-Tag-Schreibweise gilt fuer Systemtags: `odysseus-indexed` oder `odysseus:indexed`?
- Ab welcher Confidence darf ein semantisches Tag automatisch nach Nextcloud projiziert werden?
- Welche Tag-Entfernung ist spaeter erlaubt, ohne manuelle Nutzer-Tags zu gefaehrden?
- Welche Dateigroessen werden im MVP verarbeitet?
- Welche Dateitypen bekommen nur Metadaten statt Inhaltsanalyse?
- Wo liegen Sidecars: neben Datei oder zentral unter `AI Inbox/Metadata/`?
- Darf der Worker Zielordner automatisch erstellen?
- Welche Confidence-Schwellwerte gelten fuer Auto-Routing?

## Definition Of Done

- Designierter Nextcloud-User existiert und hat nur begrenzte Rechte.
- Inbox-Ordnerstruktur ist angelegt.
- Worker kann neue Datei entdecken.
- Ledger erfasst Datei mit Hash, Pfad und Status.
- Mindestens PDF, DOCX und Text werden inhaltlich analysiert.
- Routing erzeugt Zielpfad, Tags, Summary und Confidence.
- Unsichere Datei landet in Review, nicht im Zielarchiv.
- Sichere Datei wird per Kopie abgelegt, ohne Original zu loeschen.
- Nextcloud-Tags, RaptorGraph-Tags, Sidecar und Ledger sind ueber ein kanonisches Tag-Mapping konsistent.
- Manuelle Nextcloud-Tags bleiben erhalten und werden nicht automatisch geloescht.
- Frei generierte LLM-Tags werden nicht ungeprueft in Nextcloud geschrieben.
- RaptorGraph-Eintraege enthalten Dokumenttyp, Name, Inhaltszusammenfassung, Ablageort und Metadaten.
- Jede Graph-Aussage verweist auf Originalpfad und Hash.
- Keine Aktion nutzt den menschlichen Admin-User.
- Keine Aktion loescht Originaldateien.

## Abschlussprinzip

Die Inbox darf maechtig sein, aber nicht magisch. Jede Entscheidung braucht Status, Confidence, Provenance und einen sicheren Rueckweg. Nextcloud bleibt die Ablage und Rechteebene; Odysseus liefert Analyse, Routing und abgeleitetes Memory.
