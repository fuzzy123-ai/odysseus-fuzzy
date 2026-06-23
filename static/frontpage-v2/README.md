# ABC Frontpage V2

Isolierter Neubau der Odysseus/ABC-Frontseite.

## Ziel

Ab jetzt bauen wir nicht mehr am grossen Mockup weiter, sondern setzen die neue
Frontpage elementweise neu auf.

## Workflow

- First build the UI mockup for a feature in this V2 slice.
- Then wire the behavior once the visual structure is approved.
- Default working mode is `Agent`; `Plan` is a visible planning-mode mockup state.

## Enthalten

- animierter blauer Grid-Hintergrund
- alternativer Network-Hintergrund
- right-click toolwheel
- `Alt+Space` zum Oeffnen/Schliessen
- Live-Pfeil Richtung Cursor
- Center-New menu with options
- empty outer buttons for `Projects`, `Knowledge`, `Tools`, `Settings`
- keyboard selection `1-4`
- main chat window at roughly 60% viewport width and near-full height
- draggable and edge/corner-resizable floating window shell
- borderless minimize, maximize/restore, and close controls
- minimized windows appear as centered dock bubbles at the bottom edge
- vertical AI workline with blue response dots and connector lines
- three-line composer that grows upward without an internal scrollbar
- 3D tile icon for composer tools
- composer tools dropdown with raster buttons for Deep Research, Plan, Attachments, Skills, Hooks, and Project
- vertically centered send button inside the composer
- edge nodges appear only when another chat exists on that side
- first chat shows only the right nodge, last chat only the left nodge, middle chats both
- compact 3D chat carousel below the ABC mark, visible only when multiple chats exist
- carousel tiles use numbers/icons only and can be clicked or rotated with the mouse wheel
- small Agent/Plan mode switch inside the composer next to the tools dropdown, defaulting to Agent

## Naechste moegliche Elemente

1. Model chip
2. Window carousel
3. Composer tool dropdown
4. Chat workline hover tooltips
5. Floating window snap previews
6. Active chat switching

Oeffnen:

```text
static/frontpage-v2/index.html
```

Hintergrundvarianten:

```text
static/frontpage-v2/index.html
static/frontpage-v2/index.html?bg=network
```
