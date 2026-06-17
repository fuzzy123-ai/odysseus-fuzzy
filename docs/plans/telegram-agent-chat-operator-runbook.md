# Telegram Agent Chat Operator Runbook

Stand: 2026-06-17

Status: Operator-Runbook fuer Telegram als eigenstaendigen Odysseus Agent-Chat.

Dieses Runbook beschreibt nur den sicheren Betreiberpfad fuer Telegram-Textchat
als externen Odysseus-Kanal. Es fuehrt keine Telegram-, Netzwerk- oder
Plugin-Aktion aus und enthaelt keine Tokenwerte, keine Chat-IDs und keine
Live-Credentials.

Wichtig: Solange `DLF1B` nicht umgesetzt ist, beschreibt "redacted history" in
diesem Dokument das Zielbild fuer persistierte Diagnostik, nicht einen bereits
vollstaendig belegten Ist-Zustand.

## Zweck

Telegram darf als externer Odysseus Agent-Chat genutzt werden, wenn alle
lokalen Gates bewusst gesetzt sind:

- Telegram empfaengt Textnachrichten.
- Die Nachricht wird in eine bestehende oder neue Odysseus-Session geroutet.
- Der Agent antwortet ueber den Bot nur dann, wenn Reply explizit freigegeben
  ist.
- Die lokale Historie darf nur stabile redacted Handles und keine rohen
  Chat-IDs, Sender-IDs oder File-IDs speichern.

## Nicht-Ziele

Dieses Runbook deckt bewusst nicht ab:

- keine echten Tokenwerte oder Chat-IDs
- keine Live-Netzwerkfreigabe
- keine Video-Verarbeitung
- keine Nextcloud- oder Obsidian-Archivierung
- keine Speech-to-Text-Implementierung
- keine Plugin-System-Refactors

Video sowie Nextcloud-/Obsidian-Archivierung kommen spaeter in separaten
Slices. Voice bleibt in diesem Slice nur als Intake-/Pending-STT-Thema.

## Env Gates

Die folgenden Gates muessen als Betreiberentscheidung dokumentiert werden. Es
duerfen nur Variablennamen, nie echte Werte, in Evidence oder Doku auftauchen.

- `TELEGRAM_BOT_TOKEN`
  Lokales Secret fuer den Bot. Muss ausserhalb des Repos verwaltet werden.
- `TELEGRAM_ALLOWED_CHAT_IDS`
  Allowlist der erlaubten Chats. Keine Realwerte in Doku oder Logs notieren.
- `TELEGRAM_AGENT_CHAT_ENABLED`
  Aktiviert Telegram-Intake in Richtung Odysseus-Session.
- `TELEGRAM_AGENT_REPLY_ENABLED`
  Aktiviert ausgehende Agent-Antworten ueber Telegram.
- `TELEGRAM_POLLING_ENABLED`
  Aktiviert optional einen Polling-Pfad. Muss standardmaessig aus bleiben, wenn
  kein bewusster Operator-Need vorliegt.
- `TELEGRAM_VOICE_ENABLED`
  Aktiviert Voice-Intake ueber Metadaten/Pending-STT. Text-Readiness darf nicht
  davon abhaengen.

## Betriebsbild

Das sichere Zielbild fuer diesen Slice ist:

1. Telegram nimmt Text einer erlaubten Quelle an.
2. Die Nachricht wird einer dauerhaften Odysseus-Session zugeordnet.
3. Der bestehende Odysseus-Chat-/Agent-Pfad verarbeitet die Nachricht.
4. Eine Antwort wird nur bei explizit freigegebenem Reply-Gate ausgeliefert.
5. Inbound, Outbound, Blocked, Failed und Voice werden lokal nur ueber stabile
   redacted Handles festgehalten, nicht ueber rohe Telegram-Identifier.

Es wird kein zweiter Agent-Runtime-Pfad aufgebaut.

## Redaction Boundary

Persistierte Telegram-Diagnostik darf nur operator-taugliche, stabile redacted
Handles enthalten.

Nicht persistieren:

- rohe `chat_id`
- rohe Sender-ID
- rohe Voice- oder Datei-ID
- Tokenwerte

Zulaessig ist nur eine Form, die Korrelation fuer Operatoren erlaubt, aber
keinen Rueckschluss auf echte Telegram-Identifier aus dem Artefakt selbst
zulaesst.

## Go/No-Go Checkliste

### Intake

Go:

- `TELEGRAM_BOT_TOKEN` ist lokal gesetzt, aber nicht im Repo gespeichert.
- `TELEGRAM_ALLOWED_CHAT_IDS` ist lokal definiert.
- `TELEGRAM_AGENT_CHAT_ENABLED` ist bewusst gesetzt.
- Intake kann zwischen dry-run, webhook oder optional polling unterschieden
  werden.
- Blocked oder nicht erlaubte Chats werden redacted protokolliert.

No-Go:

- Secret steht in Repo, Prompt, Log oder Doku.
- Allowlist fehlt oder ist unklar.
- Intake waere ohne explizites Chat-Gate aktiv.

### Session Bridge

Go:

- Ein erlaubter Chat kann stabil einer Odysseus-Session zugeordnet werden.
- Bestehende Session wird wiederverwendet oder sauber neu angelegt.
- Bridge bleibt im bestehenden Odysseus-Chatpfad.
- Lokale Historie und Session-Diagnostik speichern nur redacted Bridge-Evidence
  mit stabilen Handles, nicht mit rohen Telegram-Identifiern.

No-Go:

- Telegram baut eine zweite Agent-Runtime auf.
- Session-Zuordnung ist nicht nachvollziehbar.
- Historie schreibt unredigierte Identifikatoren oder Secrets mit.

### Reply

Go:

- `TELEGRAM_AGENT_REPLY_ENABLED` ist bewusst gesetzt oder bewusst aus.
- Outbound-Antworten werden nur fuer erlaubte Chats versucht.
- Erfolgreiche und fehlgeschlagene Replies werden nur ueber redacted Handles
  dokumentiert.
- Retry bleibt begrenzt und erklaerbar.

No-Go:

- Reply ist default-on ohne Betreiberfreigabe.
- Chat-IDs oder Secret-Werte tauchen in Logs oder Evidence auf.
- Unbegrenzte Retry- oder Send-Schleifen werden benoetigt.

### Voice

Go:

- `TELEGRAM_VOICE_ENABLED` ist klar als optional dokumentiert.
- Voice kann als Metadaten-/Inbox-Ereignis angenommen werden.
- Voice bleibt `pending_stt`, solange kein spaeterer STT-Slice freigegeben ist.
- Textchat-Readiness bleibt voll nutzbar, auch wenn Voice aus oder pending ist.
- Persistierte Voice-Diagnostik darf keine rohen File-IDs oder Unique-IDs
  enthalten.

No-Go:

- Textchat haengt von STT oder Voice-Download ab.
- Voice wird als fertig transkribiert behauptet, obwohl nur Pending-STT
  vorbereitet ist.

## Empfohlene Operator-Pruefung

Der Betreiber sollte vor einem spaeteren Live-Smoke mindestens diese Punkte
lokal pruefen und nur redigiert notieren:

1. Intake-Gates sind bekannt und absichtlich gesetzt.
2. Allowlist ist vorhanden.
3. Session-Bridge ist als Telegram-zu-Odysseus-Session-Konzept verstanden.
4. Reply-Gate ist explizit an oder aus, nicht implizit.
5. Polling ist nur optional und nicht Standardannahme.
6. Voice ist als pending-STT und nicht als fertige Transkription dokumentiert.
7. Lokale Historie nutzt nur stabile redacted Handles.

## Evidence-Felder

Fuer spaetere manuelle Evidence sollen nur redigierte Betreiberangaben erfasst
werden:

- Datum
- Commit
- Intake-Modus geplant: webhook, polling oder dry-run
- `TELEGRAM_AGENT_CHAT_ENABLED`: ja oder nein
- `TELEGRAM_AGENT_REPLY_ENABLED`: ja oder nein
- `TELEGRAM_POLLING_ENABLED`: ja oder nein
- `TELEGRAM_VOICE_ENABLED`: ja oder nein
- Allowlist vorhanden: ja oder nein
- Session-Mapping nachvollziehbar: ja oder nein
- Redacted history uses stable handles only: ja oder nein
- Reply-Gate bewusst gesetzt: ja oder nein
- Voice pending STT dokumentiert: ja oder nein
- Ergebnis: Go, Partial oder No-Go
- Blocker oder offene Risiken

## Go / Partial / No-Go Sprache

### Go

Go ist nur angemessen, wenn:

- Intake, Session Bridge und Reply-Gate sauber verstanden und lokal vorbereitet
  sind
- persistierte Diagnostik nur stabile redacted Handles verspricht und belegt
  sind
- Voice korrekt als optional/pending-STT beschrieben ist
- keine Secrets, Realwerte oder unbounded Aktionen auftreten

### Partial

Partial ist angemessen, wenn:

- Textchat vorbereitet ist
- Reply oder Voice bewusst noch deaktiviert oder pending sind
- die Grenzen ehrlich dokumentiert sind
- die Dokumentation das Redaction-Zielbild bereits klarzieht, der zugehoerige
  Persistenz-Fix aber noch separat offen ist

### No-Go

No-Go ist angemessen, wenn:

- Secrets oder Chat-IDs in Evidence landen
- persistierte Diagnostik rohe Chat-IDs, Sender-IDs oder File-IDs speichert
- Session-Bridge unklar bleibt
- Reply implizit aktiv waere
- Voice als fertig dargestellt wird, obwohl STT noch fehlt
- Nextcloud-, Obsidian- oder Video-Themen in diesen Slice hineingezogen werden

## Bekannte Grenzen

- Voice ist nur pending STT, nicht fertige Transkription.
- Persistierte Diagnostik ist erst dann wirklich redaction-safe, wenn nur
  stabile redacted Handles gespeichert werden.
- Video ist nicht Teil dieses Slices.
- Nextcloud- und Obsidian-Archivierung sind ausdruecklich spaeter und separat.
- Telegram bleibt ein eigenstaendiger Plugin-Kanal, kein allgemeiner
  Plugin-System-Umbau.
- Live-Netzwerkverhalten braucht spaetere explizite Operator-Freigabe.

## Rollen

### Alice

Alice liefert Betreibertexte, Runbook-Sprache und Go/No-Go-Erklaerung.

### Bob

Bob liefert Plugin-Code, Session-Bridge, Reply-Pfad, Voice-Pending-STT und
fokussierte Tests.

### Charlie

Charlie kontrolliert Scope, Worktree, Tests, Integration, Commits, Pushes und
Stop-Entscheidungen.

## Stop-Regeln

Sofort stoppen, wenn:

- ein Tokenwert oder eine echte Chat-ID notiert, geloggt oder gespeichert wird
- rohe Sender-IDs oder File-IDs in persistierter Diagnostik landen oder als
  "redacted" verkauft werden
- Textchat nur mit Voice/STT funktionieren soll
- Nextcloud, Obsidian oder Video in diesen Slice hineingezogen werden
- ein zweiter Agent-Runtime-Pfad entsteht
- Reply oder Polling implizit statt bewusst freigegeben sind
- lokale Historie keine Redaction-Grenzen mehr haelt

## Abschluss

Dieses Runbook macht Telegram als eigenstaendigen externen Odysseus Agent-Chat
operator-tauglich beschreibbar: Text rein, Session Bridge in Odysseus, Antwort
ueber Bot nur mit explizitem Gate, persistierte Diagnostik nur ueber stabile
redacted Handles, Voice nur als pending STT. Nextcloud, Obsidian und Video
bleiben ausdruecklich Zukunftsmusik ausserhalb dieses Slices.
