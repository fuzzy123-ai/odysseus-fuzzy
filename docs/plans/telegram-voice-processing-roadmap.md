# Telegram Voice Processing Roadmap

Status: backend complete, live smoke gated. Text Telegram agent chat is treated
as the existing baseline. This track adds safe voice message processing in
small gates.

Goal: Telegram voice messages can be received from an allowed chat, stored with
redacted metadata, transcribed through an explicitly enabled STT boundary, and
then forwarded as an Odysseus agent-chat turn with a gated Telegram text reply.

## Current Evidence

- Telegram is a standalone plugin under `plugins/telegram/plugin.py`.
- The plugin already supports text intake, local redacted history, session
  bridge payloads, an injected agent-turn hook, gated Telegram replies, and
  metadata-only voice intake marked `pending_stt`.
- Voice download, fakeable STT, transcript-to-agent-turn, retry handling and
  Telegram reply handoff are implemented with tests and stay default-off behind
  gates.
- `TELEGRAM_VOICE_STT_ENABLED` is the canonical STT gate. The older
  `TELEGRAM_STT_ENABLED` operator wording is accepted as a compatibility alias.
- Focused Telegram tests exist in `tests/test_telegram_plugin.py`.
- Manual live Telegram voice smoke remains explicit-Go only.

## Non-Goals

- No Nextcloud or Obsidian archival in this track.
- No video processing.
- No broad plugin-system refactor.
- No automatic live Telegram send without explicit operator Go.
- No automatic voice download, STT provider call, or network action without a
  dedicated local gate and focused tests.
- No raw Telegram token, chat id, sender id, file id, or file unique id in docs,
  tests, persisted history, diagnostics, or automation prompts.

## Stop Rules

- Stop on foreign staged files or unclear ownership of dirty Telegram files.
- Stop if a real token, chat id, sender id, Telegram file id, or provider output
  would be persisted, logged, copied into docs, or placed in test fixtures.
- Stop on unbounded polling, retry loops, audio downloads, or network calls in
  automated tests.
- Stop on plugin-system-wide changes outside the Telegram plugin boundary.
- Stop on red tests without a narrow focused fix.
- Never use destructive git commands.

## ABC Ownership

Alice owns operator language, runbooks, Go/No-Go wording, voice status language,
manual smoke instructions, and secret-hygiene instructions.

Allowed Alice paths:
- `docs/plans/telegram-voice-processing-roadmap.md`
- `docs/plans/telegram-agent-chat-operator-runbook.md`
- Optional links in `docs/plans/telegram-*.md`

Bob owns models, tests, and focused Telegram plugin implementation slices.

Allowed Bob paths for the first slice:
- `plugins/telegram/plugin.py`
- `tests/test_telegram_plugin.py`
- Optional small helpers under `plugins/telegram/`

Charlie owns scope, integration, focused tests, commits, push, live-smoke stop
decisions, and automation lifecycle.

## Slices

### TVP0 - Roadmap and Delegation

Owner: Charlie.

Done when:
- This roadmap exists.
- Alice and Bob have scoped tasks.
- A one-minute Charlie monitor is active.

Tests: none, docs-only setup.

### TVP1 - Voice Operator Contract

Owner: Alice.

Goal:
- Define the operator-facing status language for Telegram voice.

Requirements:
- Define `voice_received`, `pending_download`, `download_blocked`,
  `pending_stt`, `transcribed`, `agent_ready`, and `failed`.
- Document local gates by name only:
  `TELEGRAM_VOICE_ENABLED`, `TELEGRAM_VOICE_DOWNLOAD_ENABLED`,
  `TELEGRAM_VOICE_STT_ENABLED` / legacy alias `TELEGRAM_STT_ENABLED`,
  `TELEGRAM_AGENT_REPLY_ENABLED`,
  `TELEGRAM_ALLOWED_CHAT_IDS`, and `TELEGRAM_BOT_TOKEN`.
- Explain that voice is accepted as metadata first.
- Explain that file download, transcription, and outbound replies are separate
  gates.
- Keep Nextcloud/Obsidian archival and video as deferred future work.

Tests: none, docs-only.

Operator language:
- `voice_received`
  Eine erlaubte Voice-Nachricht wurde als redacted Metadata angenommen. Es
  liegt noch kein Download, keine Transkription und keine Agent-Antwort vor.
- `pending_download`
  Voice-Metadata ist vorhanden, aber ein lokaler Download wurde noch nicht
  freigegeben oder noch nicht bewusst geplant.
- `download_blocked`
  Voice wurde erkannt, aber Download bleibt absichtlich gesperrt, weil
  `TELEGRAM_VOICE_DOWNLOAD_ENABLED` fehlt, der Chat nicht erlaubt ist oder der
  Operator noch kein Go gegeben hat.
- `pending_stt`
  Ein lokaler Dateibezug darf vorliegen, aber die Sprach-zu-Text-Stufe ist noch
  nicht freigegeben oder noch nicht abgeschlossen.
- `transcribed`
  Eine redigierte Transkriptionsausgabe liegt vor. Das bedeutet noch nicht,
  dass bereits ein Agent-Turn oder eine Telegram-Antwort freigegeben ist.
- `agent_ready`
  Das Transkript ist fuer einen internen Agent-Chat-Turn vorbereitet. Ein
  echter Outbound-Reply bleibt trotzdem am separaten Reply-Gate haengen.
- `failed`
  Die Voice-Kette ist an Download-, STT-, Policy- oder Sicherheitsgrenzen
  gescheitert und braucht redigierte Operator-Evidence statt automatischer
  Wiederholung.

Go:
- Voice bleibt zunaechst metadata-first.
- Download, STT und Reply sind sichtbar getrennte Gates.
- Nur Gate-Namen werden dokumentiert:
  `TELEGRAM_VOICE_ENABLED`, `TELEGRAM_VOICE_DOWNLOAD_ENABLED`,
  `TELEGRAM_VOICE_STT_ENABLED` / legacy alias `TELEGRAM_STT_ENABLED`,
  `TELEGRAM_AGENT_REPLY_ENABLED`,
  `TELEGRAM_ALLOWED_CHAT_IDS`, `TELEGRAM_BOT_TOKEN`.
- Persistierte Diagnostik nutzt nur redacted Handles statt roher Chat-, Sender-
  oder File-Identifier.

Partial:
- `voice_received` und `pending_stt` sind sauber beschrieben, aber Download,
  STT oder Reply bleiben absichtlich deaktiviert oder pending.

No-Go:
- Rohe Tokens, Chat-IDs, Sender-IDs, File-IDs oder Provider-Ausgaben wuerden
  in Doku, Logs, Persistenz oder Evidence landen.
- Voice-Download, STT oder Reply wuerden ohne eigenes Gate oder ohne
  Operator-Go als live-faehig verkauft.

Deferred:
- Nextcloud-/Obsidian-Archivierung.
- Video.
- Automatische Media-Pipelines ausserhalb der Telegram-Voice-Gates.

### TVP2 - Voice Intake Boundary

Owner: Bob.

Goal:
- Strengthen current voice intake so voice messages remain metadata-only,
  redacted, counted, and ready for later download/STT gates.

Requirements:
- Keep voice file ids transient only.
- Persist only redacted handles and safe metadata such as duration, MIME type,
  and size.
- Add a stable voice status payload for local history/readiness.
- No file download.
- No STT provider call.
- No network action.

Focused tests:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_telegram_plugin.py
```

### TVP3 - Voice Download Gate

Owner: Bob implementation, Charlie integration.

Goal:
- Add a disabled-by-default local download boundary for allowed chats only.

Requirements:
- Requires `TELEGRAM_VOICE_DOWNLOAD_ENABLED`.
- Enforce bounded size and timeout.
- Store files only under the Telegram plugin data area with hashed names.
- Tests must use fakes and never call Telegram.
- Persist no raw Telegram file ids.

Status: done in backend. Live download remains bounded and gate-controlled.

### TVP4 - STT Provider Boundary

Owner: Bob implementation, Alice docs, Charlie integration.

Goal:
- Add a provider-agnostic STT boundary with a fake provider for tests.

Requirements:
- Requires `TELEGRAM_VOICE_STT_ENABLED`; `TELEGRAM_STT_ENABLED` is accepted as a
  legacy alias.
- STT provider receives a local safe file reference, not raw Telegram ids.
- Transcripts are stored as text history only after redaction checks.
- Tests use fake STT, no network/provider calls.

Status: done in backend. Provider execution remains gate-controlled and tests
use fake STT.

### TVP5 - Voice Agent Turn

Owner: Bob implementation, Charlie integration.

Goal:
- Convert a successful transcript into an Odysseus agent-chat turn and return a
  gated Telegram text reply if the reply gate is enabled.

Requirements:
- Reuse the existing session bridge and injected agent-turn hook.
- Mark the prompt as originating from Telegram voice.
- Preserve inbound voice metadata and transcript state.
- Gated reply path remains the same as text replies.

Status: done in backend. Live Telegram reply still requires the existing reply
gate.

### TVP6 - Manual Voice Live Smoke

Owner: Charlie, only after explicit operator Go. Status: deferred/live-gated.

Goal:
- Verify one real allowed Telegram voice roundtrip.

Manual evidence:
- One voice message is received.
- Metadata is redacted.
- Download gate and STT gate behavior are visible.
- Agent sees transcript, not raw audio ids.
- One gated Telegram text reply is sent.

Never run this automatically.

## Verification

Minimum automated verification for TVP2:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_telegram_plugin.py
```

Integration verification before manual voice live smoke:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_telegram_plugin.py tests\test_plugin_local_audit.py tests\test_plugin_manifest_policy.py
```

## Release Language

Go:
- Voice intake, download gate, STT boundary, transcript-to-agent turn, and gated
  reply path are tested offline; manual live-smoke has explicit operator
  evidence.

Partial:
- Voice metadata intake is ready, but download/STT/live voice smoke remains
  gated or pending.

No-Go:
- Raw identifiers leak, tests require real Telegram/network, voice download is
  unbounded, STT runs without a gate, or replies bypass the existing gate.

Deferred:
- Video, Nextcloud/Obsidian archival, broad plugin marketplace work, and
  automatic media cleanup policies.
