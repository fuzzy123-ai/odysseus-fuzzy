# Security Incident Response Runbook

Stand: 2026-07-03

Status: SIR-6 operator runbook contract

Dieses Runbook beschreibt den defensiven Incident-Response-Ablauf fuer Odysseus. Es ist bewusst prepare-only: keine Live-Remediation, keine Host-Kommandos, keine Token, keine Chat-IDs und keine privaten Inhalte.

## Ziel

Odysseus soll Sicherheitsereignisse strukturiert behandeln:

- redigierte Evidenz sammeln
- Incident-Level bestimmen
- Debug-Bundle vorbereiten
- Operator knapp benachrichtigen
- nur erlaubte Read-only-Diagnostik automatisch ausfuehren
- jede Remediation als Gate behandeln

## Incident-Level

| Level | Name | Bedeutung | Auto erlaubt | Operator |
| - | - | - | - | - |
| 0 | normal | Keine relevante Anomalie | Monitoring | keine Nachricht |
| 1 | watch | Niedrige Sicherheit, kleiner Spike | Kandidat, Dedupe, Korrelation | Digest moeglich |
| 2 | alert | Wiederholte Fehlversuche, Probe, sicherheitsrelevanter Ausfall | Incident, Debug-Bundle, MCP Read-only | sofort informieren |
| 3 | contain | Aktiver Missbrauch oder Stoerung | Empfehlung vorbereiten | approve/deny erforderlich |
| 4 | lockdown | Secret-Leak, Admin-Missbrauch, Exfiltration-Verdacht | Incident Mode, lokale Analyse, Risky Tools blocken | urgent |
| 5 | recovery | Rueckkehr in vertrauenswuerdigen Zustand | Recovery-Plan, Health-Check | Review |

## Standardablauf

1. Redigiertes Ereignis oder Summary erfassen.
2. Incident mit `raw_content_visible=false` erzeugen.
3. `security_recent_anomalies` oder passende Klassifikation ausfuehren.
4. `security_policy_readiness` pruefen, falls Policy-Zustand unklar ist.
5. `security_recommend_next_action` nutzen, um Policy und Operator-Notification vorzubereiten.
6. Optional ein redigiertes Debug-Bundle vorbereiten.
7. Operator informieren, aber keine Remediation ausfuehren.
8. Approve/Deny-Entscheidung abwarten, wenn eine Gate-Aktion vorgeschlagen wird.
9. Nach Abschluss Incident auf Recovery oder Closed setzen.

## Automatisch erlaubt

- Read-only MCP-Diagnostik
- redigierte Debug-Bundles
- Incident-Kandidaten
- Alert-Dedupe
- lokale sensitive Analyse im DSGVO/Incident Mode
- Operator-Notification mit redigierten Fakten

## Gate-pflichtig

- CrowdSec Ban/Unban
- Firewall- oder Reverse-Proxy-Regel
- Service-Restart
- Scheduler Pause/Retry
- RaptorGraph Maintenance Restart
- Nextcloud Import Retry
- Token Rotation
- Session Invalidation
- Cloudflare Tunnel Aenderung
- Deploy Rollback
- Log-Level Erhoehung mit Privacy-Risiko

## Niemals erlaubt

- Hackback
- Third-party Exploit
- Secret-Ausgabe
- Upload privater Evidenz an externe Modelle im DSGVO/Incident Mode
- destruktives Cleanup vor Evidenzsicherung
- arbitrary shell ueber MCP
- `expose_all` fuer MCP

## Evidence-Regeln

Zulaessig:

- Hashes
- redigierte Event-IDs
- redigierte Correlation-IDs
- Surface, Component, Event Type, Status, Severity
- Debug-Bundle-ID
- Action-ID

Nicht zulaessig:

- Tokens
- Chat-IDs
- Authorization Header
- Cookies
- private Dokumenttexte
- E-Mail-Volltexte
- Bild-Base64
- absolute Hostpfade
- rohe Provider-Ausgaben

## Operator-Entscheidung

Eine Operator-Notification muss enthalten:

- Incident-ID
- Level, Severity, Confidence, Status
- betroffene Surfaces
- Policy-Entscheidung
- Debug-Bundle-Referenz, falls vorhanden
- Action-IDs
- klare Approve/Deny-Anweisung

Die Notification darf keine Zielwerte wie Telegram Chat-ID oder Token enthalten. Zustellung ist serverseitige Konfiguration.

## Go / No-Go

Go fuer prepare-only Response:

- Incident ist strukturiert und redigiert
- Policy bewertet jede Action
- Notification ist redigiert
- `writes_performed=false`
- Gate-Actions sind nicht ausgefuehrt

No-Go:

- Raw Content sichtbar
- Secret-Marker in Evidenz oder Notification
- Remediation ohne Gate
- Host-Kommandos aus Odysseus-Core
- nicht erklaerter Zugriff auf Live-Systeme

## Recovery

Recovery beginnt erst, wenn:

- keine neuen korrelierten Alert-Events auftreten
- betroffene Services Health-ok oder bewusst unknown sind
- alle vorbereiteten Remediation-Actions approved, denied oder expired sind
- ein kurzer Post-Incident Summary ohne private Inhalte vorliegt

Recovery darf keine Beweise loeschen. Cleanup ist ein separater, gated Schritt.
