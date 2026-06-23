# ABC Chat Typography Overview

Status: Hybrid Prompt selected for the zero-sidebar mockup.

Visual preview:

- `static/mockups/abc-chat-typography-options.html`

## Problem

Der aktuelle Chattext wirkt zu sauber und zu glatt. Er ist lesbar, aber zu
nah an einer generischen Web-App. ABC soll menschlich verstaendlich bleiben,
aber sich eher wie ein praezises Arbeitswerkzeug anfuehlen: fokussiert,
leicht technisch, etwas rauer, nicht poliert wie SaaS.

## Ziel

Chattext soll:

- normal lesbar bleiben
- weniger clean wirken
- User-Eingaben klar von ABC-Antworten unterscheiden
- nicht in Entwickler-Jargon kippen
- zur Pixel-/Terminal-DNA passen
- trotzdem angenehm fuer lange Sessions sein

## Optionen

### 1. Hybrid Prompt Style

User-Nachrichten bekommen einen dezenten Prompt-Marker:

```text
> Pruefe bitte, warum die Memory-Suche bei langen Sessions langsamer wird.
```

Vorteil: Sofort mehr Werkzeug-/Terminal-Gefuehl, ohne die ganze UI technisch
zu ueberladen.

Nachteil: Kann bei sehr langen Nachrichten etwas befehlsartig wirken.

### 2. Mono fuer User, Sans fuer ABC

User-Eingaben laufen in einer ruhigen Mono-Schrift, ABC-Antworten in einer
lesbaren Sans.

Vorteil: Klare Rollenlogik. User gibt Anweisungen, ABC antwortet.

Nachteil: Zu viel Mono kann bei langen User-Nachrichten anstrengend werden.

### 3. Logbuch statt Chatbubble

Nachrichten werden als Arbeitsprotokoll dargestellt:

```text
USER / 20:41
Pruefe bitte, warum ...
```

Vorteil: Weniger Messenger-Gefuehl, mehr Arbeitsjournal.

Nachteil: Kann distanzierter und schwerer wirken.

### 4. Pixel-Terminal-Akzent

Die Schrift bleibt lesbar, aber jede Nachricht bekommt kleine technische
Akzente: Promptkante, Cursor, Scanline, Pixelmarker oder kurze Statuszeile.

Vorteil: Passt zur bestehenden Odysseus/ABC Motion-DNA.

Nachteil: Zu viele Akzente koennen schnell clutter erzeugen.

### 5. Dichtere Typografie

Schrift etwas kleiner, Zeilenhoehe kompakter, Farben weniger weich, Bubbles
kantiger.

Vorteil: Weniger polished, mehr Werkzeug.

Nachteil: Darf nicht auf Kosten langer Lesbarkeit gehen.

## Empfehlung

Ausgewaehlt: Hybrid Prompt Style.

Umsetzung:

- Hybrid Prompt Style fuer User-Nachrichten
- leicht mono-nahe User-Typografie
- lesbare, aber kompaktere ABC-Antworten
- weniger runde Bubbles
- kleine linke Prompt-/Scan-Kante statt grosser Dekoration

Damit bleibt ABC user-first, bekommt aber mehr Charakter und weniger
generische Sauberkeit.

## Designregel

Technische Anmutung ja, Fachbegriffe nein.

Die Textform darf nach Arbeitswerkzeug aussehen. Die Sprache selbst muss
weiterhin klar, kurz und normal verstaendlich bleiben.
