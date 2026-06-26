# ABC Toolwheel Actions Matrix

Status: draft matrix for the minimal V2 toolwheel.

Purpose: keep the toolwheel useful without becoming a sidebar in radial form.
The wheel should answer: "What do I want to do next?"

## Rules

- Four top-level categories for now: `Projects`, `Knowledge`, `Tools`,
  `Settings`.
- The center `+` is the only place for creating new chats/tasks/workspaces.
- Each category shows at most 4 visible actions by default.
- Extra actions go into `More`.
- Complex, rare, or technical actions go into `Advanced`.
- `Customize` happens inside the toolwheel, but should not be visible as a main
  action until the default wheel feels stable.
- Not every action opens a window.

## Action Types

| Type | Meaning | UI result |
|---|---|---|
| `Instant` | Changes state or starts a short action immediately | Small feedback, possible chat note |
| `Attach` | Adds context to the current composer/chat | Composer dropdown context nodge |
| `Open` | Opens a global floating window | Global window |
| `Mode` | Switches how input or execution behaves | Composer/window state changes |
| `More` | Opens a deeper menu/list | Toolwheel submenu |
| `Advanced` | Hidden unless user asks for advanced/customize | Advanced list or command library |

## Center Core

| Action | Type | Visibility | UI result | Notes |
|---|---|---|---|---|
| New Chat | Instant | Visible | Creates another chat space | Already partly wired |
| New Task | Open | Visible | Opens task/planning surface | Can start as mockup |
| New Workspace | Instant | Visible | Creates a clean workspace/chat group | Needs definition later |

## Projects

Default color: red.

| Action | Type | Visibility | UI result | Notes |
|---|---|---|---|---|
| Open Project | Open | Visible | Opens global Projects window | Main project entry |
| Plan Project | Instant/Open | Visible | Starts planning flow in chat or opens plan surface | Should use normal language, not orchestration |
| Tasks | Open | Visible | Opens global Tasks/Project window | Could become a tab inside Projects |
| Add to Chat / Add Project | Attach | Composer | Adds selected/current project as composer nodge | Attach actions live in composer dropdown |
| Timeline | Open | More | Opens project timeline view | Later |
| Blockers | Open | More | Opens blockers/questions view | Later |
| Export Plan | Instant | More | Exports current plan | Later |
| Project Settings | Open | Advanced | Opens settings for selected project | Later |

Recommendation: `Tasks` and `Open Project` may eventually merge into one
Projects window with tabs. Keep both labels visible in the prototype until the
actual window design exists.

## Knowledge

Default color: teal.

| Action | Type | Visibility | UI result | Notes |
|---|---|---|---|---|
| Search Knowledge | Open | Visible | Opens global Knowledge search window | Main knowledge entry |
| Add Source | Attach | Composer | Adds source/file/folder/link context to composer | Might branch into picker later |
| Remember Note | Instant | Visible | Saves a short note from current context | Needs confirmation feedback |
| Update Knowledge | Instant | Visible | Starts knowledge refresh/indexing | Must show status plainly |
| Memory Graph | Open | More | Opens visual memory graph | User likes memory graph typography |
| Sources | Open | More | Opens source list/audit surface | User-facing replacement for source internals |
| Source Audit | Open | More | Shows stale/failed sources | Could be later |
| Rebuild Index | Instant | Advanced | Technical maintenance action | Hide by default |
| Knowledge Settings | Open | Advanced | Opens knowledge settings | Later |

Naming note: `Knowledge` is acceptable for now, but visible action labels should
say what the user does: search, add, remember, update.

## Tools

Default color: blue.

| Action | Type | Visibility | UI result | Notes |
|---|---|---|---|---|
| Deep Research | Open | Visible | Opens or starts research flow | May create composer nodge while active |
| Mount Folder | Attach | Composer | Adds temporary chat-scoped mount as composer nodge | Important current feature |
| Attach File | Attach | Composer | Adds file attachment nodge | Composer action parity |
| Open Terminal | Open | Visible | Opens global terminal window | User likes terminal typography |
| Skills | Open/More | More | Opens skills/tools list | Name can stay for now |
| Hooks | Open/More | More | Opens hooks/actions list | Name can stay for now |
| Plugins | Open | More | Opens plugin manager | Later |
| Tool Logs | Open | Advanced | Opens technical logs | Hide by default |
| Developer Tools | Open | Advanced | Opens developer-focused tooling | Hide by default |

Recommendation: all `Attach` actions live in the composer dropdown and produce
the same visual language: composer context nodges.

## Settings

Default color: blue/cyan.

| Action | Type | Visibility | UI result | Notes |
|---|---|---|---|---|
| Model | Open | Visible | Opens model picker/settings | Also reflected in header chip |
| Appearance | Open | Visible | Opens theme/background/density controls | Includes network/grid and motion later |
| Shortcuts | Open | Visible | Opens keyboard shortcut reference/settings | Useful for toolwheel discoverability |
| Voice | Open | Visible | Opens STT/voice settings | Connects to composer mic |
| Local/API Setup | Open | More | Provider/model setup | Avoid `provider` jargon if possible |
| Privacy | Open | More | Privacy and data handling | Later |
| Toolwheel Customize | Mode | More | Enters customize mode inside wheel | Important but not first visible |
| Advanced Settings | Open | Advanced | Technical configuration | Hide by default |
| Developer Options | Open | Advanced | Debug/dev settings | Hide by default |

Recommendation: keep `Toolwheel Customize` in `More` at first. When the default
wheel is stable, it can become a small edit/customize affordance in the wheel
itself.

## Composer Context Nodges Needed

These are required because many wheel actions should attach context instead of
opening windows.

| Nodge | Created by | Meaning |
|---|---|---|
| Mount | Mount Folder | Temporary folder is available to current chat |
| File | Attach File / Attachments | File is part of the prompt context |
| Project | Add to Chat / Project | Project context is active |
| Source | Add Source | Knowledge source is attached |
| Research | Deep Research | Research mode/context is active |

Nodge behavior:

- Horizontal, attached to composer.
- Multiple contexts create multiple nodges.
- Each nodge needs an icon, short label, and remove action.
- Hover/focus shows details.
- Too many nodges should collapse into a compact context summary.

## Minimal V2 Implementation Target

Build this first:

1. Center core with `New Chat`, `New Task`, `New Workspace`.
2. Four category nodes.
3. Visible actions only.
4. `More` row per category, non-functional or mocked.
5. Action type stored in `data-action-type`.
6. Clicking composer `Attach` actions creates mock composer nodges.
7. Clicking `Open` actions announces/open-mocks a global floating window later.
8. Clicking `Instant` actions shows lightweight feedback.

Do not build full customization yet. Design it only after the default wheel
feels right.

## Open Questions

- Should `Open Project` and `Tasks` merge before implementation?
- Is `Deep Research` user-facing enough, or should it become `Research Deeply`?
- Should `Toolwheel Customize` be in `Settings > More`, or a tiny edit icon on
  the wheel edge?
- Does `New Workspace` mean a separate chat group, a layout preset, or a fresh
  working area?
