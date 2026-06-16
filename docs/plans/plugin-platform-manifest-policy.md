# Plugin Platform Manifest Policy

## Ziel

Odysseus behandelt Plugins als eigenen, nachvollziehbaren Feature-Bereich. Bevor ein Plugin im UI auftaucht, installiert wird oder spaeter als Runtime-Erweiterung arbeitet, muss sein Manifest oder Registry-Eintrag offline auditierbar sein.

Dieser Slice ist bewusst keine Plugin-Ausfuehrung und kein Installer-Umbau. Er legt eine kleine Policy-Schicht fest, die plain JSON-/Dict-Daten prueft, ohne Plugin-Code zu importieren oder Netzwerkeffekte auszulösen.

## Scope

- Registry-Dokumente muessen eine Liste von Plugin-Eintraegen enthalten.
- Registry-Eintraege brauchen sichere IDs, Name, Version, Kategorie, Beschreibung, HTTPS-Download und SHA256.
- Lokale `PLUGIN`-Manifeste brauchen mindestens `name`; `version` wird empfohlen.
- `permission` ist nur `admin` oder `user`; Default bleibt `admin`.
- `ui.open` muss ein sicherer absoluter App-Pfad sein.
- `requires` ist rein informativ, aber muss als Liste von Strings modelliert sein.

## Nicht-Ziele

- Kein Download.
- Keine Installation.
- Kein Import fremder Plugin-Dateien.
- Keine Ausfuehrung von Host-Kommandos.
- Keine Plugin-Sandbox-Policy fuer Runtime-Code.

## Alice/Bob/Charlie Matrix

| Slice | Alice | Bob | Charlie | Parallelregel |
| --- | --- | --- | --- | --- |
| `PLUGIN1-manifest-policy-contract` | Begriffe, Nutzerhinweise und Review-Regeln | technische Machbarkeit read-only pruefen | Contract in Roadmap einsortieren | ja, docs/read-only |
| `PLUGIN2-manifest-policy-model` | keine Codearbeit | offline Validatoren fuer Registry und lokale Manifeste | Tests, Scope, keine Runtime-Hooks | ja |
| `PLUGIN3-plugin-ui-policy-surface` | UI-Texte fuer Warnungen/Fehler | spaeter Browse-/Settings-Anbindung | nur nach Hotfile-Freigabe | nein |

## Acceptance Criteria

- Validierung importiert keinen Plugin-Code.
- Lokale Plugin-Ordner koennen statisch auditiert werden, ohne `setup()` oder Top-Level-Code auszufuehren.
- Bundled `plugins/registry.json` ist policy-konform.
- Unsichere Downloads, doppelte IDs, falsche Digests und gefaehrliche `ui.open` Werte werden geblockt.
- Policy-Reports liefern maschinenlesbare Fehlercodes fuer spaetere UI-/Installer-Gates.
- Die Plugin-Platform bleibt ein eigener Track und wird nicht in Lens, Image Tools oder Security versteckt.

## Umsetzungshinweis

`src/plugin_manifest_policy.py` validiert einzelne Manifeste und Registry-Dokumente.
`src/plugin_local_audit.py` scannt lokale Plugin-Schnittstellen per AST und ruft diese Policy auf. Beide Module sind reine Vorbereitungsschichten: keine Downloads, keine Imports, keine Runtime-Hooks.
