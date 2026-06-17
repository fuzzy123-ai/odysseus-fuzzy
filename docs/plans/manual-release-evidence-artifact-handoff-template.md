# Manual Release Evidence Artifact Handoff Template

Stand: 2026-06-17

Status: **REL47A Copy/Paste-Handoff-Template fuer Manual-Release-Evidence-Artefakte**

Quellen:

- `docs/plans/manual-release-evidence-artifact-contract.md`
- `docs/plans/manual-release-evidence-operator-index.md`

Dieses Template ist ein kompakter Morgen- oder Uebergabeblock fuer Charlie oder einen Operator. Es beschreibt nur den Status des manuellen Release-Evidence-Artefakts und behauptet keine erledigte manuelle Evidence. Provider Proof und Export / Import / Rebuild bleiben solange offen, bis echte beobachtete Evidence im Evidence-Log eingetragen wurde.

## Verwendung

- Copy/Paste fuer Morgenbrief, Handoff oder kurze Statusmeldung
- nur mit read-only Artefakt-Daten fuellen
- kein `Go` behaupten, wenn echte manuelle Evidence noch fehlt

## Template

```text
REL47 Manual Evidence Artifact Handoff
Datum:
Operator:
Branch: fuzzy/dev
Commit:

Artefakt-Label:
Generated At:
Artifact Path:
sha256:

Status:
- Gesamtstatus: pending | partial | blocked
- Externes 1.0: No-Go | weiterhin No-Go

Offene Gates:
- Provider Proof: pending | partial | blocked
- Export / Import / Rebuild Proof: pending | partial | blocked

Fehlende echte Evidence:
- Provider Proof beobachtet und im Evidence-Log dokumentiert: ja | nein
- Export / Import / Rebuild beobachtet und im Evidence-Log dokumentiert: ja | nein

Runbook-Referenzen:
- Provider Proof: docs/plans/provider-proof-operator-runbook.md
- Export / Import / Rebuild: docs/plans/export-import-rebuild-operator-runbook.md
- Operator Index: docs/plans/manual-release-evidence-operator-index.md

Stop/Go-Entscheidung:
- Go fuer externes 1.0: nein
- Stop, wenn Artefakt pending/partial/blocked zeigt: ja
- Naechster echter manueller Schritt:

Hinweis:
- Dieses Artefakt ist nur ein Gap-/Status-Snapshot.
- Echte manuelle Evidence gehoert ausschliesslich in docs/plans/1.0-manual-release-evidence-log.md.
```

## Leseregel

Das Template darf nur aus dem aktuellen Artefakt und beobachteten manuellen Facts gefuellt werden.

Nicht eintragen:

- Secrets
- komplette Providerantworten
- sensible Snippets
- implizite Go-Freigaben

## Go/Stop-Regel

Wenn das Artefakt oder die reale Lage zeigt:

- `pending`
- `partial`
- `blocked`
- fehlende echte Evidence

dann gilt:

- externes `1.0` bleibt `No-Go`
- Handoff meldet offen, was noch fehlt
- kein Snapshot wird als Freigabebeweis umgedeutet

## Nicht-Ziele

Dieses Template fuehrt bewusst nicht aus:

- keine echte Evidence-Erfassung
- keine Release-Freigabe
- keine Runtime- oder Testintegration

Das Template beschreibt nur den kompakten Handoff fuer das Manual-Release-Evidence-Artefakt.
