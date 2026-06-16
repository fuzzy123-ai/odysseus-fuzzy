# Secure Data Mode Audit Runbook

Stand: 2026-06-16

Status: **SEC8 Audit-/Readiness-Runbook**

Quellen:

- `docs/plans/secure-data-mode-contract.md`
- `docs/plans/data-classification-policy-contract.md`
- `docs/plans/chat-security-state-contract.md`
- `docs/plans/secure-policy-gate-contract.md`
- `src/data_classification.py`
- `src/chat_security_state.py`
- `src/secure_policy_gate.py`
- `src/secure_model_routing.py`
- `src/sensitive_retrieval_guard.py`
- `src/secure_channel_policy.py`

Dieses Runbook definiert den Readiness-Nachweis fuer Secure Data Mode. Es ist bewusst kein Runtime-Integrationsplan. Provider-, Retrieval-, Telegram- und UI-Hotfiles duerfen erst angebunden werden, wenn die untenstehenden Gates gruen sind.

## Ziel

Odysseus darf sensible Daten nur dann verarbeiten, wenn der gesamte Pfad local-only und policy-konform ist.

Der sichere End-to-End-Pfad lautet:

```text
Quelle ist sensitive oder secret
-> normaler Chat fragt an
-> Zugriff wird blockiert, ohne Kontext zu laden
-> Nutzer startet neuen Secure Chat
-> lokales Primaermodell, lokale Fallbacks und lokale Embeddings werden verifiziert
-> Retrieval darf Kontext laden
-> Antwort bleibt im sicheren Kanal
-> Export/Logs brauchen Review oder Block
```

## Bestehende Foundation

| Slice | Nachweis |
| --- | --- |
| `SEC2-data-classification-model` | `tests/test_data_classification.py` |
| `SEC3-chat-security-state-model` | `tests/test_chat_security_state.py` |
| `SEC4-policy-gate-model` | `tests/test_secure_policy_gate.py` |
| `SEC5-local-only-model-routing` | `tests/test_secure_model_routing.py` |
| `SEC6-sensitive-retrieval-guard` | `tests/test_sensitive_retrieval_guard.py` |
| `SEC7-telegram-secure-policy` | `tests/test_secure_channel_policy.py` |

## Pflicht-Gates vor Runtime-Integration

### Gate 1: Klassifikation

Akzeptanz:

- `public/private/sensitive/secret` sind die einzigen Kernwerte.
- Unknown oder invalid fuehrt bei policy-relevantem Zugriff zu Review oder Block.
- Abgeleitete Artefakte duerfen nicht ohne Review weniger streng sein als ihre Quellen.
- Mixed Sources uebernehmen die strengste Klassifikation.

Test:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_data_classification.py
```

### Gate 2: Chat Security State

Akzeptanz:

- Chat startet als `normal` oder `secure`.
- Der Zustand ist immutable.
- Secure Chat erzwingt `local_only`.
- Modellwechsel darf keinen laufenden Chat hoch- oder herunterstufen.

Test:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_chat_security_state.py
```

### Gate 3: Policy Gate

Akzeptanz:

- Normaler Chat blockiert `sensitive/secret`.
- Secure Chat blockiert externe Provider, externe Embeddings und unsichere Tools.
- Sensitive Exporte oder Logs brauchen Review.
- Ambiguous Security Mode blockiert.

Test:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_secure_policy_gate.py
```

### Gate 4: Local-only Model Routing

Akzeptanz:

- Secure Chat blockiert externe Primaermodelle.
- Secure Chat blockiert externe Fallbacks.
- Secure Chat blockiert externe Embeddings.
- Disabled Fallbacks werden nicht als sichere Route gezaehlt.

Test:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_secure_model_routing.py
```

### Gate 5: Sensitive Retrieval Guard

Akzeptanz:

- Normaler Chat mit sensibler Quelle laedt keine Context-Refs.
- Secure Chat braucht vor Retrieval eine verifizierte lokale Model Route.
- Blockierte Retrieval-Entscheidungen enthalten keine Snippets und keine Context-Refs.
- Memory, RAG und Graph werden als Oberflaechen explizit unterschieden.

Test:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_sensitive_retrieval_guard.py
```

### Gate 6: Channel Policy

Akzeptanz:

- Nicht allowlistete Kanaele werden blockiert.
- Sensible Daten im normalen Chat verlangen Secure Chat.
- Telegram ist fuer Secure Flow blockiert, solange kein expliziter Secure-Telegram-Flow existiert.
- Sensible Daten ueber unsicheren Kanal werden blockiert.

Test:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_secure_channel_policy.py
```

## Gesamttest

Vor jedem Runtime-Handoff:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_data_classification.py tests\test_chat_security_state.py tests\test_secure_policy_gate.py tests\test_secure_model_routing.py tests\test_sensitive_retrieval_guard.py tests\test_secure_channel_policy.py
```

Erwarteter Stand nach `SEC7`: `66 passed, 1 warning`.

## Runtime-Handoff-Regeln

Provider-/Routing-Integration darf erst starten, wenn:

- die Gesamtsuite gruen ist
- kein Worktree-Hotfile-Konflikt vorliegt
- lokale Modelle und lokale Embeddings im Zielsystem sichtbar sind
- externe Fallbacks im Secure Flow blockierbar sind
- Nutzertexte fuer Block/Review vorhanden sind

Retrieval-Integration darf erst starten, wenn:

- Retrieval vor dem Laden von Snippets, Chunks oder Graph-Kontext das Guard-Modell aufruft
- blockierte Entscheidungen keine Preview- oder Snippet-Daten enthalten
- normaler Chat nicht still zu Secure hochgestuft wird
- Secure Chat nicht still auf externen Fallback geht

Telegram-Integration darf erst starten, wenn:

- Telegram User Allowlist existiert
- Bot Token nicht geloggt wird
- sensibler Inhalt im normalen Telegram-Flow blockiert wird
- ein expliziter Secure-Telegram-Flow entweder existiert oder bewusst als unsupported blockiert bleibt

## Stop-Regeln

Stop bei:

- API-Modell oder externer Embedding-Pfad im Secure Flow
- sensibler Quelle im normalen Chat-Kontext
- Retrieval-Blocker, der trotzdem Context-Refs oder Snippets zurueckgibt
- Telegram-Antwort mit sensiblen Inhalten ohne expliziten Secure Flow
- Export oder Log mit sensiblen Inhalten ohne Review
- unbekannter Klassifikation im policy-relevanten Zugriff
- Hotfile-Overlap in Provider-, Retrieval-, RAG-, Graph- oder Telegram-Dateien

## Nicht-Ziele

Dieses Runbook implementiert nicht:

- App-Level-Verschluesselung
- DLP-Engine
- automatische Klassifikation ohne Review
- echte Provider-/RAG-/Telegram-Hooks
- Migration bestehender Daten
- DSGVO-Rechtsberatung

## Go/No-Go

Go fuer die naechste Stufe, wenn:

- die Gesamtsuite gruen ist
- alle Blockentscheidungen ohne Context-Leak bleiben
- Runtime-Hotfiles als eigene sequenzielle Slices geplant sind

No-Go, wenn:

- ein Secure Flow externes Modell, externes Embedding oder externen Fallback nutzen wuerde
- normaler Chat sensible Quellen laden kann
- Telegram sensible Inhalte im normalen Flow ausgibt
- Logs oder Exporte sensible Inhalte ohne Review enthalten koennen
