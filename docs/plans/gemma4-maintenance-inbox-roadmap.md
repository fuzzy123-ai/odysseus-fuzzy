# Gemma4 E4B Maintenance / Universal Inbox Roadmap

Status: **in Umsetzung**

Modus: **Standard ABC, backend/logik-first**

## Ziel

Gemma4 E4B ist Odysseus' lokales Maintenance-Modell fuer Universal Inbox,
Memory-Write-Intent und RaptorGraph-Vorbereitung. Es verwaltet kleine,
vorbereitete Arbeitspakete, entscheidet DSGVO/API-Gates und erzeugt redigierte
Abstraktionen. Es ist nicht das Chat-Hauptmodell und schreibt nie direkt
kanonische Wahrheit.

## Leitregeln

- Default ist lokal: `gemma4:e4b` via `local_ollama`.
- Budget fuer Maintenance-Prompts: maximal 1200 Tokens.
- Queue-Concurrency: 1, damit der Server nicht durch Inbox-Jobs blockiert.
- Keine Rohdokumente, Chat-IDs, Hostpfade oder Secrets in Reports.
- RaptorGraph bekommt nur Abstraktionen und Write-Intent, keine Rohinhalte.
- DSGVO/sensitive/secret bleibt local-only; API-Eskalation ist dort gesperrt.
- API/Fallback ist Ausnahme, braucht Gate-Grund, Budget und Review-Pfad.

## ABC Slice Queue

| Slice | Klasse | Owner | Ziel | Status |
| --- | --- | --- | --- | --- |
| M1 Policy-Profil | repo_only | Bob | Gemma4-E4B-Profil, Budgets, lokale Rolle und Truth-Write-Verbot maschinenlesbar machen | done |
| M2 Routing-Gate | repo_only | Bob | Entscheiden: Maintenance-Modell, kleineres Paket, Review oder gated Fallback | done |
| M3 Inbox-Integration | repo_only | Bob | Universal-Inbox-Reports enthalten redigierte Maintenance-Route pro Item | done |
| M4 Raptor-Schutz | repo_only | Bob | Raptor-Pfade nutzen dieselbe Budget-/Local-only-Policy vor Write-Intent | done |
| M5 Cookbook/Settings Contract | repo_only | Alice/Bob | Backend-Settings fuer manuelle Modellsteuerung vorbereiten, ohne UI zu bauen | done |
| M6 Live Evidence | needs_live_go | Charlie | Server deployen und mit Live-Gemma E4B einen Inbox/Raptor-Maintenance-Smoke laufen lassen | gated |
| T1 Telegram Attachment Intake | repo_only | Bob | Telegram-Dateien automatisch in Universal Inbox pruefen und mit Maintenance-/Queue-Status beantworten | done |
| T2 Telegram Review Commands | repo_only | Bob | `/review ok` und `/review memory ok` bestaetigen Ablage bzw. Memory/Raptor-Intent redigiert | done |
| T3 Nextcloud Copy Gate | needs_live_go | Charlie | Nach Review copy-only in Nextcloud schreiben, no-delete/no-overwrite, live nur mit Gates | existing, live-gated |
| T4 Raptor Write Executor | repo_only | Bob | Ready/review Intents schreiben nur redigierte Abstraktionen in Memory/RaptorGraph | done |
| T5 Queue/Worker Status | repo_only | Bob | Telegram-Events enthalten Queue-Status, Concurrency und geplante Memory/Raptor-Arbeit | done |

## Gate Queue

Gate: `gemma4-live-maintenance-smoke`

Class: `needs_live_go`

Blocks: Live-Evidence fuer echte Server-Latenz, Queue-Verhalten und Ollama-Readiness.

Decision needed: Explizites Go fuer Deploy/Live-Smoke auf Debian.

Safe preparation done: Policy, Worker-Integration und Tests koennen repo-only
abgeschlossen werden.

Risk if bypassed: Lokale Tests beweisen Contract und Redaction, aber nicht
Server-Durchsatz unter echter Ollama-Latenz.

## Done Definition

- Maintenance-Policy ist zentral und getestet.
- Universal Inbox zeigt redigiert, welches Modell/Gate verwendet wurde.
- DSGVO/sensitive Faelle koennen nicht still zur API eskalieren.
- Uebergrosse Pakete gehen in Smaller-Packet/Review statt direkt zu Gemma.
- RaptorGraph Write-Intent bleibt abstraction-only.
- Telegram-Datei-Eingang liefert sofort Status, Maintenance-Action und Review-Hinweis.
- Telegram-Follow-up kann den letzten Anhang ephemeral kontextualisieren.
- Telegram-Review kann Nextcloud-Copy und Memory/Raptor-Write getrennt bestaetigen.
- Focused Tests sind gruen.
