# Automated Agent N-Scaling Design

Stand: 2026-06-17

Status: **AUTO8 model prepared**

## Ziel

Odysseus soll perspektivisch mehr als Alice und Bob koordinieren koennen, ohne daraus eine unkontrollierte Agentenfabrik zu machen. Skalierung bedeutet hier:

- bekannte Agenten aus einem Pool auswaehlen,
- klare Rollen und Datei-Scope-Regeln respektieren,
- Token-/Parallelitaetsbudgets einhalten,
- File-Locks und Hotfile-Kollisionen blockieren,
- bei Unklarheit warten oder blockieren statt neue Agents zu erfinden.

## Modellgrenze

`src/agent_pool_scaling.py` ist eine Policy- und Planungs-Schicht. Sie erzeugt Assignment-Entscheidungen, aber:

- erstellt keine Threads,
- startet keine Agents,
- sendet keine Nachrichten,
- fuehrt keine Git- oder Test-Kommandos aus,
- hebt keine Locks automatisch auf,
- trifft keine Architekturentscheidung ohne Nutzerfreigabe.

## Kernobjekte

- `AgentPoolMember`: registrierter Agent mit Rolle, Kapazitaet, Restbudget und erlaubten Datei-Roots.
- `WorkItem`: wartender Slice mit Rolle, Dateien, Token-Schaetzung und Prioritaet.
- `FileLock`: aktive Datei-/Slice-Sperre durch einen Agenten.
- `AgentPoolPolicy`: globale Parallelgrenze, Token-Reserve und Regel, dass Agents registriert sein muessen.
- `AssignmentPlan`: `assign`, `wait` oder `block` pro WorkItem.

## Entscheidungsregeln

- File-Lock gewinnt immer und fuehrt zu `block`.
- Globale Parallelgrenze fuehrt zu `wait`.
- Fehlende Rolle, Budget, Kapazitaet oder Datei-Erlaubnis fuehrt bei registrierungspflichtigem Betrieb zu `block`.
- Arbeit wird nach Prioritaet sortiert, damit hoch priorisierte Slices zuerst Kapazitaet erhalten.
- Budget und Kapazitaet werden pro geplanter Zuweisung verbraucht.

## Akzeptanzkriterien

- Ein passender registrierter Agent bekommt Arbeit zugewiesen.
- Globales Parallelbudget erzeugt `wait`.
- File-Locks erzeugen `block`.
- Fehlender registrierter Agent erzeugt `block`.
- Tokenreserve wird respektiert.
- Unsichere Pfade werden abgelehnt.

## Naechste sichere Schritte

Erst nach Nutzerfreigabe:

- echte Agent-Pool Registry/API,
- UI fuer Rollen, Pools, Budgets und Locks,
- Integration in Heartbeat Runtime,
- echte Thread-Erzeugung oder Agent-Spawn.

Nicht automatisch:

- unbegrenzte Agentenzahl,
- automatische Agent-Erstellung,
- Lock-Aufhebung ohne Gate,
- Dispatch an unbekannte Threads.
