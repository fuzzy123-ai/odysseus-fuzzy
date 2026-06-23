---
name: Odysseus Design System
description: Operator-grade self-hosted AI workspace UI with dark-first terminal influences, compact controls, and themeable tokens.
colors:
  bg-dark: "#282c34"
  fg-cyan: "#9cdef2"
  panel-dark: "#111111"
  border-cyan: "#355a66"
  accent-red: "#e06c75"
  accent-blue: "#00aaff"
  success-green: "#4caf50"
  warning-amber: "#f0ad4e"
  light-bg: "#f5f5f5"
  light-fg: "#2b2b2b"
typography:
  display:
    fontFamily: "Inter, system-ui, sans-serif"
    fontWeight: 600
    lineHeight: 1.12
  headline:
    fontFamily: "Inter, system-ui, sans-serif"
    fontWeight: 600
    lineHeight: 1.2
  body:
    fontFamily: "Fira Code, monospace"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Fira Code, monospace"
    fontWeight: 600
    lineHeight: 1.25
rounded:
  xs: "3px"
  sm: "5px"
  md: "7px"
  lg: "10px"
  xl: "14px"
spacing:
  xxs: "3px"
  xs: "5px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
components:
  button-quiet:
    backgroundColor: "transparent"
    textColor: "{colors.fg-cyan}"
    rounded: "{rounded.md}"
    padding: "4px 8px"
  button-accent:
    backgroundColor: "{colors.accent-red}"
    textColor: "{colors.panel-dark}"
    rounded: "{rounded.md}"
    padding: "6px 10px"
  panel:
    backgroundColor: "{colors.panel-dark}"
    textColor: "{colors.fg-cyan}"
    rounded: "{rounded.lg}"
    padding: "{spacing.md}"
---

## Overview

**Creative North Star: "The Calm Control Room."** Odysseus should feel like a dense, capable workspace for people steering real systems. The visual language is dark-first, token-driven, and compact, with enough personality in color and iconography to feel owned rather than generic.

**Key Characteristics:**
- Dark operational canvas with cyan text and red accent as the inherited identity.
- Compact controls, sidebars, modals, tabs, and dense lists built for repeated use.
- Themeable tokens first; hardcoded one-off styling should be folded back into the token layer when touched.

**The Native Surface Rule.** Every new panel, plugin, and agent view should look like it belongs to the same workspace before it expresses its own feature identity.

## Colors

The core palette is dark graphite, cyan foreground, deep black panels, muted cyan borders, and a warm red action accent. Blue is a supporting interactive accent; green, amber, and red carry semantic state. Light mode exists and should preserve the same hierarchy rather than becoming a separate product.

**The Accent Rarity Rule.** Red and bright blue are signals, not wallpaper. Use them for selected state, calls to action, warnings, links, and focused moments.

**The Theme Token Rule.** Prefer `--bg`, `--fg`, `--panel`, `--border`, `--red`, `--accent`, and semantic color tokens over new literals.

## Typography

**Display Font:** Inter with system sans fallback for UI headings and larger labels.
**Body Font:** Fira Code with monospace fallback for the default workspace voice.
**Label/Mono Font:** Fira Code for compact controls, status lines, code, and operational metadata.

Type should stay compact but not cramped. Use stronger weight, color, and spacing to create hierarchy before increasing size. Long prose should avoid full-width lines; dense data can remain compact when rows and controls have stable dimensions.

**The No Shouting Rule.** Product screens do not need hero-scale headings. Reserve large type for true first-run or empty-state moments.

## Elevation

Odysseus is mostly tonal rather than shadow-heavy. Panels separate through background, border, and opacity. Elevation appears for modals, popovers, toasts, and drag/drop affordances, with restrained shadows and clear z-index order.

**The State Over Decoration Rule.** Shadows and glow should communicate focus, hover, modal depth, or live system state. They should not be ambient decoration.

## Components

### Buttons
- **Shape:** small to gently curved (5px to 7px) for compact controls.
- **Color:** quiet buttons inherit foreground; destructive or primary actions use the accent token.
- **States:** hover should increase contrast or accent commitment, not resize the button.

### Panels and Modals
- **Shape:** restrained rounded corners (7px to 10px). Avoid nested card stacks.
- **Color:** panel surfaces use the theme panel token with border and subtle tonal separation.
- **Behavior:** modals need explicit close affordances, keyboard access, and scroll-safe bodies.

### Navigation
- **Style:** dense side/top navigation with icon support, current-state clarity, and compact labels.
- **States:** active state should be visible by color and structure, not only by low-contrast opacity.

### Inputs and Editors
- **Style:** stable height, clear border, theme-aware background, and visible focus.
- **Behavior:** long content, generated text, and code should wrap or scroll intentionally without shifting surrounding controls.

## Do's and Don'ts

Do use existing theme tokens, compact hierarchy, explicit state, and readable contrast. Do preserve operational density while making primary actions and risk boundaries obvious.

Don't use generic SaaS marketing treatments, purple-blue gradients, glassmorphism, decorative nested cards, or oversized hero layouts inside the application shell. Don't introduce a new typeface, radius language, or color family for a feature unless the design system is being intentionally refreshed.
