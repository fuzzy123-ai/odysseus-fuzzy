# ABC UI Traction Map

Status: working map for staying oriented while redesigning the UI.

Purpose: reduce overwhelm. The product can contain many features, but the team
only works on one small visible slice at a time.

Rule: no feature is forgotten if it has either a UI home, a queue position, or a
parking-lot entry.

## Emotional Operating Rules

- We are not building the whole UI at once.
- We are building one reliable product path first.
- A parked feature is not lost.
- A feature without a final design can still have a temporary home.
- Reversible decisions are preferred during mockup work.
- The active build queue may contain at most 5 items.
- New ideas go into the inventory first, not directly into the code.

## Product Core

The first complete product path is:

1. Open ABC.
2. Type or speak into the composer.
3. Choose `Agent` or `Plan`.
4. Add context if needed: attachment, mount, project, research.
5. Send.
6. Read the response.
7. Inspect workline dots only when needed.
8. Use the toolwheel for navigation or larger actions.
9. Switch chats/windows only when the work expands.

If a feature does not support this path, it is probably `Next`, `Later`, or
`Parked`.

## Terminology

- `Workline` means the vertical line running down the chat.
- Blue dots on the workline represent AI responses and can show low-priority
  metadata on hover/focus.
- Red dots on the workline represent internal AI work steps. They should stay
  collapsed until the user hovers or focuses them.
- `Workline` is a planning term. The visible UI should use simpler labels if a
  label is ever needed.

## Now

These are the current core UI pieces. They are allowed to receive design polish
and light behavior wiring.

| Feature | UI home | Why now | Current state |
|---|---|---|---|
| Network background | Background | Sets visual identity | Mocked, still tuning density/depth |
| Core chat window | Main floating window | Primary work surface | Mocked |
| Composer text input | Composer | Main user action | Mocked |
| Send button | Composer right | Main commit action | Mocked |
| Agent / Plan switch | Composer right | Core work mode | Mocked |
| Voice input button | Composer right | New STT path | Mocked, not wired to real STT |
| Composer tools menu | Composer left | Context/actions without sidebar | Mocked |
| Mount Folder | Composer tools menu | Temporary chat-scoped mount | Mocked |
| Context nodges | Attached to composer | Show active mounts, attachments, and project context | Needs mockup |
| Workline response dots | Chat timeline | Keeps AI output understandable | Mocked |
| Blue dot meta tooltip | Workline | Hides low-priority metadata | Mocked |
| Right-click toolwheel | Workspace | Sidebar replacement | Partly wired |
| Toolwheel depth overlay | Toolwheel backdrop | Focus and readability | Mocked with `?wheel=dim` fallback |

## Next

These should be designed after the core composer/toolwheel flow feels stable.

| Feature | UI home | Next decision |
|---|---|---|
| Empty-chat model selector | Above composer | How model choice looks before first message |
| Header model chip | Header center/right | What minimal model status belongs in header |
| Model tooltip | Header model chip | Which stats matter and which are noise |
| Header chat history sidebar | Header history icon | How past and current chats appear without the old main sidebar |
| Red work-step dots | Workline | How completed internal steps collapse into hover-only tooltips |
| Planning artifact view | Chat or floating panel | How Plan mode produces a readable plan |
| Project context panel | Floating window | How Projects stops being a vague category |
| Knowledge search panel | Floating window | How memory/search is named for normal users |

## Later

These are important, but they should not block the current UI core.

| Feature | UI home | Reason to delay |
|---|---|---|
| Snap assist | Window edges | Big interaction system, intentionally later |
| Full toolwheel customization | Settings / edit mode | Requires stable default toolwheel first |
| Layout persistence | App state / settings | Needs final window model first |
| Touch longpress toolwheel | Touch gesture | Needs desktop toolwheel behavior to settle first |
| Mobile swipe between chats | Mobile navigation | Needs chat-space model to settle first |
| Reduced motion settings | Settings | Important before production, not while motion language is changing |
| Privacy/security surface | Settings | Needs local/API model story first |
| External integrations | Tools/settings | Too broad until core context tools are stable |

## Parked

Parked means safe, not discarded.

| Item | Current decision |
|---|---|
| Sidebar | Removed from V2 direction |
| `Windows` as toolwheel category | Removed; window behavior should be direct |
| `Security` as top-level toolwheel category | Deferred into Settings unless it earns top-level status |
| Visible advanced commands by default | Removed; advanced actions use progressive disclosure |
| Odysseus visible brand | Removed from V2 mockup; `ABC` only for now |
| Final names for `Skills` and `Hooks` | Parked until user-facing naming pass |

## UI Placement Map

| UI area | Belongs here | Does not belong here |
|---|---|---|
| Header | Chat history icon, chat title, rename, model chip, compact status | Dense tools, long explanations |
| Composer | Input, send, Agent/Plan, voice, quick context tools, context nodges | Large settings, project dashboards |
| Composer tools menu | Attachments, Mount Folder, Deep Research, Project, quick actions | Full tool catalog |
| Chat workline | AI response dots, step dots, hover-only metadata | Permanent verbose logs |
| Toolwheel | Workspace actions, Projects, Knowledge, Tools, Settings | Every advanced command at once |
| Floating windows | Global Projects, Knowledge, Terminal, Settings, tool output | Chat-bound private panes |
| Carousel/nodges | Active chat navigation and status | Full chat history |
| Settings | Models, local/API setup, privacy, shortcuts, appearance | Primary workflow actions |

## Current Build Queue

The queue is intentionally small.

1. Stabilize toolwheel focus/depth overlay.
2. Polish composer right controls: Agent/Plan, voice, send spacing.
3. Design composer context nodges for mounts, attachments, and projects.
4. Design header chat-history icon and sidebar.
5. Design empty-chat model selector and header model chip.

No item 6 until one of these is done, removed, or parked.

## Feature Intake

When a new idea appears, answer these in order:

1. What user action does it support?
2. Is it needed in the first product path?
3. Where does it live?
4. Is it always visible, contextual, hidden, or advanced?
5. Does it need a mockup first?
6. Is it `Now`, `Next`, `Later`, or `Parked`?

## Decision Log Format

Use this mini-format whenever a design decision starts feeling heavy:

```text
Decision:
Chosen:
Fallback:
Why:
Review after:
```

Example:

```text
Decision: Toolwheel backdrop
Chosen: Depth Lens
Fallback: ?wheel=dim
Why: More depth than black dimming, but needs readability
Review after: composer and toolwheel polish pass
```

## Decisions Captured From Current Planning

Preview:

- `static/mockups/abc-toolwheel-actions-preview.html`
- `docs/plans/abc-toolwheel-actions-matrix.md`

```text
Decision: Toolwheel top-level categories
Chosen: Projects, Knowledge, Tools, Settings
Fallback: Add Security later inside Settings if needed
Why: Enough structure without rebuilding a sidebar
Review after: first pass on actions under each category
```

```text
Decision: Toolwheel customization
Chosen: Customize happens inside the toolwheel
Fallback: Settings can expose advanced import/export later
Why: Users should rearrange the wheel where they use it
Review after: default wheel actions are stable
```

```text
Decision: Toolwheel command visibility
Chosen: Every command can be hidden from the wheel and restored later
Fallback: Advanced command library in Settings
Why: Prevents clutter while keeping commands discoverable
Review after: command catalog exists
```

```text
Decision: STT mode
Chosen: Voice mode changes the composer from text input focus to speech input
Fallback: Keep voice as a text-dictation toggle if full voice mode is too heavy
Why: STT should feel like an input mode, not just another attachment
Review after: STT integration is available
```

```text
Decision: Composer context display
Chosen: Active attachments, mounts, projects, and similar context appear as horizontal nodges attached to the composer
Fallback: Collapse into a context summary chip if too many nodges appear
Why: Context should be visible at send time without filling the chat
Review after: Mount Folder flow is mocked
```

```text
Decision: Empty chat model
Chosen: A new empty chat uses the last selected model automatically
Fallback: Show model selector only when user opens the model chip/tab
Why: Avoids forcing a model decision for every new chat
Review after: model chip/header mockup
```

```text
Decision: Chat history entry point
Chosen: History icon in the top-left header opens a chat-history sidebar
Fallback: Composer history drawer can be removed or repurposed
Why: Past/current chat navigation is global, not part of message composition
Review after: sidebar mockup
```

```text
Decision: Chat history sidebar layering
Chosen: Toolwheel always overlays the history sidebar, even when the sidebar is open
Fallback: Toolwheel closes sidebar on open if layering becomes confusing
Why: Toolwheel is the highest-priority command layer
Review after: interaction test with sidebar open
```

```text
Decision: Chat history recency labels
Chosen: Show last answer age in min, h, d, or W
Fallback: Full timestamp only in tooltip
Why: Recency matters more than exact date in the list
Review after: list density pass
```

```text
Decision: Floating window scope
Chosen: All floating windows are global
Fallback: Add explicit per-chat pinning later only if needed
Why: Simpler mental model; no hidden chat-bound panes
Review after: multiple-window workflow exists
```

```text
Decision: Chat switch feedback
Chosen: No extra visual feedback for keyboard switching yet
Fallback: Add a small transient index badge later
Why: Current nodges/carousel are enough for now
Review after: real multi-chat testing
```
