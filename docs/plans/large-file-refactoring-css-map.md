# Large File Refactoring: CSS Ownership Map

Date: 2026-06-30
Status: R1 complete
Source: `static/style.css`
Line count observed: 38074

## Goal

Map the current global stylesheet into stable ownership domains before any CSS
rules are moved. This is a planning artifact for R2, not an implementation
slice.

## Non-Goals

- No visual redesign.
- No selector renaming.
- No HTML restructuring.
- No CSS movement in this slice.
- No edits to `static/style.css` while unrelated frontend work is dirty.

## Proposed Target Bundles

The R2 split should preserve current cascade order by making `static/style.css`
a compatibility facade that imports these bundles in this order:

| Order | Target bundle | Primary domain | Approximate source ranges |
| ---: | --- | --- | --- |
| 1 | `static/css/tokens-base.css` | CSS variables, themes, reset, fonts, base code blocks, scrollbars, density tokens | 1-188 |
| 2 | `static/css/app-shell.css` | app layout, sidebar, icon rail, bottom dock, modal frame, mobile shell overlays | 189-1923, 4224-5788 |
| 3 | `static/css/chat-composer.css` | welcome screen, chat input bar, model picker, message meta, agent/chat toggle, attachments | 1924-4214, 8083-8870 |
| 4 | `static/css/ui-controls.css` | generic controls, presets, toolbar, settings toggles, voice/search/theme/compare/print/syntax helpers | 5819-7981 |
| 5 | `static/css/library-documents.css` | document library, library tabs, document viewer, run output, document editor/export controls | 6711-7957, 10964-17634, 30696-31010 |
| 6 | `static/css/gallery-cookbook.css` | gallery grid/detail/editor, cookbook download/serve/settings/status surfaces | 17635-24212, 24363-26513, 37688-37719 |
| 7 | `static/css/settings-integrations.css` | settings modal layout, settings forms, integrations, contacts/CardDAV, server cards | 19816-24073 |
| 8 | `static/css/email.css` | email library, reader, inbox list, compose/editor, summary tables, email-specific mobile behavior | 1028-1216, 4763-4990, 14002-16180, 23067-23154, 28473-30695 |
| 9 | `static/css/notes-calendar.css` | notes, todos, draw mode, goals, checklist controls, calendar and event forms | 31011-36236 |
| 10 | `static/css/research.css` | research panel, research cards, reconnect/timer, mobile/fullscreen research overrides | 24200-24362, 36237-37079, 37374-37470 |
| 11 | `static/css/pdf-workspace-diagnostics.css` | PDF export/signature overlays, workspace picker, diagnostics terminal, late overrides | 37080-38074 |

The exact line ranges are intentionally approximate. R2 should move selectors by
domain and preserve cascade order, not mechanically split by line number.

## Ownership Domains

| Domain | Owner lane | Key selectors / surfaces | Split risk |
| --- | --- | --- | --- |
| Tokens/base | L7/R2 | `:root`, `:root.light`, theme variables, font imports, reset, scrollbar, density classes | Very high: every bundle depends on this first. |
| App shell | L7/R2 + UI agent review | `.app`, `.sidebar`, `.icon-rail`, `.hamburger`, `.modal`, `.modal-content`, bottom dock, mobile overlays | High: global positioning and modal z-index order affect every tool window. |
| Chat/composer | L7/R2 | welcome screen, `.chat-meta`, `.msg`, composer buttons, model picker, attachment strip, code runner output | High: chat is the stable root surface and must not regress while feature UIs move around it. |
| Generic controls | L7/R2 | buttons, presets, form controls, toggles, theme popup, syntax highlighting, print/PDF export helpers | Medium-high: selectors are shared by many feature windows. |
| Library/documents | L7/R2, document feature owners | document library, tabs, cards, document viewer, PDF iframe, run output, export buttons | High: overlaps with PDF and email/library mobile sheet behavior. |
| Gallery/cookbook | L7/R2, media/model owners | gallery grid/detail/editor, cookbook download/serve panels, model cards, served status | Medium: mostly feature-owned but inherits app-shell/modal primitives. |
| Settings/integrations | L7/R2, settings owners | settings modal, provider/server cards, CardDAV/contacts, advanced folds | Medium-high: settings surfaces reuse generic cards and controls. |
| Email | L7/R2, email owners | email modal, inbox list, reader, compose, summary tables, email document editor | High: email reuses library layout plus many mobile-specific overrides. |
| Notes/calendar | L7/R2, personal workspace owners | notes panel, todo/draw/goals, calendar picker/week/agenda/event forms | Medium-high: many token-level accents and shared segmented controls. |
| Research | L7/R2, research owners | research panel, metadata, reconnect/timer, fullscreen/mobile overrides | Medium: late rules intentionally match Library/Cookbook surfaces. |
| PDF/workspace/diagnostics | L7/R2, PDF/workspace owners | PDF export/signature modals, PDF view overlays, workspace picker, diagnostics terminal | Medium-high: late overrides can supersede earlier modal and document rules. |

## Risky Global Dependencies

These selectors or concepts must be audited before and after each move:

- `:root`, `:root.light`, density classes and theme variables.
- `html`, `body`, `*`, `button`, `input`, `select`, `textarea`, `pre`, `code`,
  `.hljs`.
- Modal primitives: `.modal`, `.modal-content`, `.modal-header`,
  `.modal-body`, `.modal-footer`, close/minimize buttons and snap/dock states.
- Shared cards/lists: `.list-item`, `.doclib-card`, `.admin-card`,
  `.section-title`, `.toolbar`, `.memory-toolbar-btn`.
- Chat primitives: `.msg`, `.chat-meta`, input/composer buttons and attachment
  indicators.
- Mobile `@media` blocks are interleaved with feature rules; moving desktop
  rules without their mobile counterparts is unsafe.
- Late glass/frosted/PDF/research overrides after line 37000 can override
  earlier modal, header and document-library styles.
- Some feature rules intentionally reference other domains, for example
  Research matching Library/Cookbook surfaces or Email inheriting Library modal
  behavior. Those cross-domain references should be kept in the feature bundle
  that owns the final override.

## Recommended R2 Split Plan

1. Create `static/css/` and convert `static/style.css` into an import facade.
2. Move `tokens-base.css` first; run static CSS/MIME tests.
3. Move `app-shell.css` and modal primitives; verify shell, sidebar, dock and
   mobile bottom-sheet behavior with screenshot smoke when available.
4. Move `chat-composer.css`; verify chat load, composer, model picker,
   attachment strip and code output.
5. Move generic controls only after chat still renders correctly.
6. Move feature bundles one at a time in this order:
   Library/Documents, Email, Gallery/Cookbook, Settings, Notes/Calendar,
   Research, PDF/Workspace/Diagnostics.
7. After each move, keep imports deterministic and avoid broad formatting-only
   churn.
8. Only after the facade is stable should `static/style.css` be reduced below
   the 2000-line candidate threshold.

## Verification For R2

Required before staging an implementation slice:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_app_static_mime.py tests\test_email_split_border_css.py tests\test_updates_backups_ui_static.py
```

Recommended when browser/MCP evidence is available:

- Main app shell screenshot.
- Chat composer screenshot with model picker open.
- Document/Library modal screenshot.
- Email modal screenshot.
- Settings modal screenshot.
- Notes/Calendar screenshot.
- Research panel screenshot.
- PDF export/signature modal screenshot.

Report after each R2 sub-slice:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe scripts\large_file_report.py --format json
```

## Stop Rules For R2

Stop or defer the CSS split if:

- `static/style.css`, `static/index.html` or a target CSS bundle has unrelated
  edits.
- Browser visual smoke is unavailable for a high-risk move and the move changes
  modal/shell/chat cascade.
- Any rule move requires selector redesign or HTML restructuring.
- A failing static/UI test cannot be fixed inside CSS split scope.
- Secrets, private data, live provider calls or deploy actions would become
  involved.
