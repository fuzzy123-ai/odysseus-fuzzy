# ABC UI Feature Inventory

Status: living inventory for the new zero-sidebar frontend.

Purpose: every product feature needs a clear UI home. Not every feature should
be visible all the time. Some belong in the header, composer, toolwheel,
floating windows, hover tooltips, or progressive menus.

Core principle: user first. Labels must describe what the user can do, not the
technical implementation behind it. Developer terms are allowed only when they
are genuinely useful for the target user.

## Feature Status Legend

- `Mocked`: visible in `static/frontpage-v2/`
- `Partly wired`: interactive prototype exists, real backend still missing
- `Needs mockup`: visual direction still needed
- `Needs wiring`: visual exists, behavior/data still missing
- `Later`: intentionally not a current build slice

## Global Shell

| Feature | UI home | Visibility | Status | Notes |
|---|---|---:|---|---|
| Zero-sidebar layout | Whole app shell | Always | Mocked | No left sidebar. Navigation moves into header, composer, toolwheel, carousel, and floating windows. |
| `ABC` brand mark | Top left | Always | Mocked | Odysseus should not be visible in this UI direction. |
| Animated blue grid background | Background layer | Always | Mocked | Subtle data points move along grid lines. Keep calm, not decorative noise. |
| Network background variant | Background layer | Optional | Mocked | Available as an alternate background for later comparison. |
| First-start animation | Background / core window | Contextual | Needs mockup | Should reuse the Odysseus pixel-animation DNA. |
| AI busy animation | Chat / carousel / nodges | Contextual | Partly mocked | Pixel-style activity, not generic spinner. |
| Reduced motion mode | Global setting | Hidden in Settings | Later | Every animation needs a reduced-motion alternative. |

## Chat Spaces And Navigation

| Feature | UI home | Visibility | Status | Notes |
|---|---|---:|---|---|
| Multiple active chats | App state | Contextual | Partly wired | Each chat is a horizontal workspace/page. |
| `Ctrl+Tab` next chat | Keyboard | Hidden | Partly wired | Moves to chat on the right. |
| `Ctrl+Shift+Tab` previous chat | Keyboard | Hidden | Partly wired | Moves to chat on the left. |
| `Ctrl+1...9` direct chat select | Keyboard | Hidden | Needs wiring | Direct jump to numbered chat. |
| Edge nodges | Left/right screen edge | Contextual | Mocked | Only shown when another chat exists on that side. |
| Nodge working state | Edge nodges | Contextual | Needs wiring | Show when neighboring chat is currently working. |
| Nodge unread state | Edge nodges | Contextual | Needs wiring | Show when neighboring chat has unread output. |
| Nodge question state | Edge nodges | Contextual | Needs wiring | Show when neighboring chat needs user input. |
| Nodge error state | Edge nodges | Contextual | Later | Red/error state for failed runs. |
| Compact chat carousel | Top left below brand | Contextual | Mocked | Small 3D curved carousel with numbers/icons only. |
| Carousel click to switch | Carousel | Contextual | Partly wired | Click tile jumps to chat. |
| Carousel wheel rotate | Carousel hover | Contextual | Partly wired | Mouse wheel rotates while hovered. |
| Mobile swipe between chats | Touch gesture | Hidden | Later | Mobile does not need carousel. |

## Floating Window System

| Feature | UI home | Visibility | Status | Notes |
|---|---|---:|---|---|
| Core chat window | Main workspace | Always | Mocked | Default near full height and about 60% viewport width. |
| Free window dragging | Window header | Contextual | Partly wired | All windows should be movable. |
| Resize all edges/corners | Window frame | Contextual | Partly wired | Similar to desktop windows. |
| Minimize | Window controls | Always on windows | Partly wired | Borderless cyan icon. |
| Maximize / restore | Window controls | Always on windows | Partly wired | Icon must show current action. |
| Close | Window controls | Always on windows | Partly wired | Borderless cyan icon. |
| Minimized dock bubbles | Bottom center | Contextual | Mocked | Minimized windows appear as bubbles at lower screen edge. |
| Window focus / z-index | Floating windows | Contextual | Needs wiring | Click should bring a window forward. |
| Windows 11-style snap assist | Screen edges | Contextual | Needs mockup | Top/edge drag should propose half, max, and 4-tile layouts. |
| Layout persistence | Settings / app state | Hidden | Later | Save window position, size, and active workspace. |
| Global floating windows | Floating window system | Contextual | Decision captured | All windows are global; none are chat-bound for now. |

## Header

| Feature | UI home | Visibility | Status | Notes |
|---|---|---:|---|---|
| Chat title | Header center | Contextual | Needs mockup in V2 | Visible after chat becomes active/contentful. |
| Rename chat title | Header title double-click | Hidden until action | Needs wiring | Double-click opens inline rename field. |
| Model chip | Header next to title | Contextual | Needs mockup | Appears in header after first message. |
| Model info tooltip | Model chip hover/focus | Hidden | Needs mockup | Shows model name, local/API, tokens, context, available context %, load, privacy/cost hints. |
| Header-free empty chat state | Header | Contextual | Needs mockup | New empty chat keeps model selector near composer instead of header. |
| Chat history icon | Header top left | Always | Needs mockup | Opens the chat-history sidebar with current and past chats. |

## Composer And Footer

| Feature | UI home | Visibility | Status | Notes |
|---|---|---:|---|---|
| Main text input | Bottom of core window | Always | Mocked | Default 3 lines, text top-aligned, grows upward. |
| No internal input scrollbar | Composer | Always | Mocked | Composer expands instead of scrolling internally. |
| Send button | Right inside composer | Always | Mocked | Vertically centered with proper right offset. |
| Composer tools button | Left inside composer | Always | Mocked | Uses a 3D tile icon, not a dropdown arrow. |
| Composer tools menu | Above composer button | Contextual | Mocked | Raster/grid menu with tooltips. |
| Deep Research | Composer tools menu | Hidden | Mocked | User-facing label can stay if user understands it; otherwise consider `Research deeply`. |
| Plan | Composer tools menu | Hidden | Mocked | Quick action, not the same as global Plan mode. |
| Attachments | Composer tools menu | Hidden | Mocked | File/image/input attachment entry. |
| Mount Folder | Composer tools menu | Hidden | Mocked | Creates a temporary folder mount for the current chat context. |
| Skills | Composer tools menu | Hidden | Mocked | Consider whether this should be renamed to something more user-first later. |
| Hooks | Composer tools menu | Hidden | Mocked | Needs user-first naming later; technical term may be unclear. |
| Project | Composer tools menu | Hidden | Mocked | Attach or choose project context. |
| Agent/Plan mode switch | Composer beside tools | Always | Mocked | Default is `Agent`; `Plan` is left, `Agent` right. |
| Voice input mode | Composer right controls | Contextual | Mocked | STT mode should allow speech input instead of text input. |
| Context nodges | Attached to composer | Contextual | Needs mockup | Active attachments, mounts, projects, and other context appear as horizontal nodges. Multiple contexts create multiple nodges. |

## Empty Chat Model Selection

| Feature | UI home | Visibility | Status | Notes |
|---|---|---:|---|---|
| Last selected model default | Empty chat state | Hidden | Decision captured | A new empty chat uses the model that was selected last. |
| Model tab card | Centered above composer | Contextual | Needs mockup | Optional/manual model selector for a new chat with no messages. |
| Model dropdown | Model tab card | Hidden until clicked | Needs mockup | Shows available models. |
| Local/API status dots | Model dropdown rows | Contextual | Needs mockup | Green available, yellow overloaded/limited, red problem/offline. |
| Move model to header after first input | Header/composer transition | Contextual | Needs wiring | Once a user sends something, model chip moves to header. |
| Model availability explanation | Tooltip | Hidden | Needs mockup | Explain local/API status in normal language. |

## AI Response And Workline

| Feature | UI home | Visibility | Status | Notes |
|---|---|---:|---|---|
| Vertical workline | Chat content | Always in chat | Mocked | Holds AI replies together visually. |
| AI response dot | Workline | Contextual | Mocked | Blue point per AI answer with horizontal connector to answer box. |
| AI response meta tooltip | Blue workline dot hover/focus | Hidden | Mocked | Shows time and low-priority meta stats such as model, token usage, context, and latency. |
| Running work step display | Chat content | Contextual | Needs mockup | While AI works, show live readable state with pixel animation. |
| Collapsed completed steps | Workline dots | Contextual | Needs mockup | After completion, detailed step labels collapse into dots. |
| Step tooltip | Workline dot hover/focus | Hidden | Needs mockup | Shows action name and short explanation. |
| Needs-input question marker | Workline / nodge / carousel | Contextual | Needs mockup | Clear state when AI asks user to decide or clarify. |
| Error marker | Workline / nodge | Contextual | Later | Brief, understandable failure state. |

## Toolwheel

| Feature | UI home | Visibility | Status | Notes |
|---|---|---:|---|---|
| Right-click open | Workspace | Hidden | Partly wired | Opens centered at core/window context. |
| Right-click close | Workspace | Hidden | Partly wired | Second right-click closes. |
| `Alt+Space` open/close | Keyboard | Hidden | Partly wired | Keyboard access to toolwheel. |
| Dimmed overlay | Behind toolwheel | Contextual | Mocked | Makes wheel readable and foregrounded. |
| Live cursor arrow | Toolwheel center | Contextual | Mocked | Glow arrow follows cursor while open. |
| Center plus core | Toolwheel center | Always when open | Mocked | Thick outlined plus, not filled. |
| Center plus options | Plus hover/click | Hidden | Mocked | New chat/task/workspace style options drop down. |
| New chat from plus | Plus core | Contextual | Partly wired | Creates new chat space in same app window. |
| Outer category nodes | Toolwheel ring | Contextual | Mocked | Current V2: Projects, Knowledge, Tools, Settings. |
| Hover option trees | Category hover | Hidden | Mocked | Options unfold only on hover. |
| Option tree animation | Category hover | Contextual | Mocked | Should fill from top to bottom, with individual hover states. |
| Hit area continuity | Category + option tree | Contextual | Needs hardening | Options must stay visible when moving from button to list. |
| Keyboard number selection | Toolwheel | Hidden | Partly wired | Current `1-4`; should extend into sub-options. |
| Arrow/Enter navigation | Toolwheel | Hidden | Needs wiring | Required for full keyboard use. |
| Touch longpress/drag/release | Touch gesture | Hidden | Later | Longpress opens, drag chooses, release confirms. |
| Full customization | Settings / toolwheel edit mode | Hidden | Later | Move nodes, hide tools, pin favorites, shortcuts, import/export presets. |
| Toolwheel customize mode | Toolwheel | Hidden | Needs mockup | Customization happens inside the toolwheel: move commands, hide/show commands, and later add available commands back into the wheel. |

## Toolwheel Categories

| Category | Color role | UI home | Status | Notes |
|---|---|---:|---|---|
| Projects | Red | Toolwheel | Mocked | Replaces `Projektplanung`; user-facing project planning and task organization. |
| Knowledge | Teal | Toolwheel | Mocked | Search, sources, notes, memory, knowledge updates. |
| Tools | Blue | Toolwheel | Mocked | External tools, research, files, terminal, plugins, integrations. |
| Settings | Blue/cyan | Toolwheel | Mocked | Models, appearance, shortcuts, local/API setup, privacy. |
| Security | Amber/red | Not top-level now | Later | Removed for now; may return inside Settings if needed. |
| Windows | Blue | Not top-level now | Removed | Window management should be direct via floating-window UI, not a toolwheel category. |

## Planning And Execution Modes

| Feature | UI home | Visibility | Status | Notes |
|---|---|---:|---|---|
| Agent mode | Composer switch | Always | Mocked | Default mode; means direct execution/durchfuehrung. |
| Plan mode | Composer switch | Always | Mocked | User can ask the system to plan first. |
| Plan artifact view | Chat / floating panel | Contextual | Needs mockup | Shows plan steps clearly before execution. |
| Approve plan | Plan artifact | Contextual | Needs mockup | User confirms before execution if Plan mode requires it. |
| Convert plan to action | Plan artifact / toolwheel | Contextual | Needs wiring | Transition from planning into execution. |
| Mode-specific composer behavior | Composer | Contextual | Needs wiring | Placeholder, send label, and feedback can adapt to current mode. |

## Projects

| Feature | UI home | Visibility | Status | Notes |
|---|---|---:|---|---|
| Project list | Projects floating window | Hidden until opened | Needs mockup | Human label: `Projects`. |
| Project planning | Projects toolwheel tree / panel | Hidden | Needs mockup | Former `Agent Orchestration`; must stay user-first. |
| Tasks | Projects panel | Contextual | Needs mockup | Create, assign, progress, blockers. |
| Project context attach | Composer tools / project panel | Contextual | Needs mockup | Add a project to current chat. |
| Progress overview | Projects panel / chat summary | Contextual | Later | Current state of a project. |
| Blockers/questions | Projects panel / nodges | Contextual | Later | Surface what needs human input. |

## Knowledge

| Feature | UI home | Visibility | Status | Notes |
|---|---|---:|---|---|
| Search knowledge | Knowledge toolwheel tree / floating window | Hidden | Needs mockup | User-facing replacement for technical memory/RAG language. |
| Sources | Knowledge window | Contextual | Needs mockup | Show where information came from. |
| Notes | Knowledge window / composer tools | Contextual | Needs mockup | Save or recall notes. |
| Memory graph | Knowledge/statistics window | Contextual | Needs mockup | User liked this typography style in mockup. |
| Update knowledge | Knowledge window | Hidden | Later | Better label than embedding/vector sync. |
| Knowledge status | Tooltip / settings | Contextual | Later | Explain indexing, freshness, and failures plainly. |

## Tools

| Feature | UI home | Visibility | Status | Notes |
|---|---|---:|---|---|
| Deep research | Composer menu / Tools tree | Hidden | Mocked in composer | Needs real behavior later. |
| Terminal | Tools floating window | Hidden | Needs mockup | User liked terminal typography. |
| Files | Tools window / composer attachment | Hidden | Needs mockup | File browsing/attachment. |
| Attachments | Composer tools | Hidden | Mocked | Upload/select input files. |
| Mount folder | Composer tools / Tools tree | Hidden | Mocked | Temporary chat-scoped mount; later other mount locations can get their own UI. |
| Plugins | Tools / Settings | Hidden | Later | May be too technical as a label unless audience expects it. |
| Skills | Composer / Tools | Hidden | Mocked | Needs clearer naming if aimed at normal users. |
| Hooks | Composer / Tools | Hidden | Mocked | Needs clearer naming; likely advanced. |
| External integrations | Tools / Settings | Hidden | Later | Calendar, mail, browser, apps. |

## Settings

| Feature | UI home | Visibility | Status | Notes |
|---|---|---:|---|---|
| Model settings | Settings window / model chip | Hidden | Needs mockup | Choose models, local/API, defaults. |
| Local/API setup | Settings window | Hidden | Needs mockup | Should avoid provider jargon where possible. |
| Resource status | Settings / model tooltip | Contextual | Needs mockup | Memory, load, availability, context. |
| Appearance | Settings window | Hidden | Later | Background variant, density, motion. |
| Keyboard shortcuts | Settings / help overlay | Hidden | Later | Toolwheel, chat switching, composer. |
| Toolwheel customization | Settings / edit mode | Hidden | Later | Rearrange, hide, pin, export/import. |
| Privacy/security | Settings window | Hidden | Later | Local/API privacy, permissions, destructive actions. |

## History And Session Management

| Feature | UI home | Visibility | Status | Notes |
|---|---|---:|---|---|
| Chat history sidebar | Header history icon | Hidden until opened | Needs mockup | Lists current and past chats. Toolwheel must overlay it when both are open. |
| Chat recency labels | History sidebar | Contextual | Needs mockup | Show how long ago the last answer happened using min, h, d, or W. |
| Search previous chats | History sidebar | Hidden | Later | Useful once history grows. |
| Resume chat | History sidebar | Contextual | Needs wiring | Selecting an old chat opens it. |
| Rename chat | Header / history item | Contextual | Needs wiring | Header double-click is primary. |
| Delete/archive chat | History item menu | Hidden | Later | Needs careful confirmation. |
| Unread tracking | Carousel / nodges / history | Contextual | Needs wiring | Shared state for navigation indicators. |

## User Feedback And System States

| Feature | UI home | Visibility | Status | Notes |
|---|---|---:|---|---|
| Working | Chat, carousel, nodges | Contextual | Partly mocked | Pixel motion preferred. |
| Waiting for user | Chat, nodges | Contextual | Needs mockup | Must be obvious without shouting. |
| Done | Workline / chat | Contextual | Needs mockup | Quiet completion state. |
| Error | Chat / tooltip / nodge | Contextual | Later | Plain-language error with next step. |
| Offline/API problem | Model selector / tooltip | Contextual | Needs mockup | Red status dot plus explanation. |
| Overloaded/local busy | Model selector / tooltip | Contextual | Needs mockup | Yellow status dot plus explanation. |

## Responsive And Accessibility

| Feature | UI home | Visibility | Status | Notes |
|---|---|---:|---|---|
| Mobile layout | Whole app | Always on mobile | Partly mocked | Carousel hidden, swipe navigation later. |
| Touch toolwheel | Touch gesture | Hidden | Later | Longpress, drag, release. |
| Keyboard focus states | All controls | Contextual | Needs hardening | Toolwheel, composer menu, windows, carousel. |
| Tooltips on focus | Tooltips | Hidden | Needs hardening | Hover-only is not enough. |
| Screen-reader labels | Interactive controls | Hidden | Needs hardening | Icons need accessible names. |
| Contrast check | Whole UI | Always | Needs hardening | Especially muted cyan/blue text on dark surfaces. |
| Reduced motion | Global | Hidden | Later | Required before production. |

## Removed Or Not Top-Level

| Item | Decision | Reason |
|---|---|---|
| Sidebar | Removed | Replaced by header, composer, toolwheel, carousel, floating windows. |
| Visible advanced commands by default | Removed | Advanced options appear only through hover/menu/progressive disclosure. |
| Security as top-level toolwheel node | Deferred | Can live inside Settings until it earns top-level status. |
| Windows as top-level toolwheel node | Removed | Window actions should be direct window behavior, not navigation. |
| Odysseus visible brand | Removed for V2 | Frontpage uses `ABC` only for now. |

## Highest-Value Next Mockups

1. Model selector empty-chat state with local/API availability dots.
2. Header model chip with token/context tooltip.
3. Header chat-history sidebar.
4. AI workline tooltip behavior for completed steps.
5. Windows-style snap assist overlay.
6. Planning mode artifact view: plan first, then execute.
