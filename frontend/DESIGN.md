# Frontend Design System

The single source of truth for UI elements. **Before adding a button, modal,
panel, input, card, badge, toggle, or color, find it here and reuse the
documented class.** Do not invent new one-off styles; if something genuinely new
is needed, add it here first so it stays consistent.

All values live as CSS custom properties in [`src/styles/app.css`](src/styles/app.css)
`:root`. Reference the tokens — never hardcode hex, px, rem, or ms.

Repo-level conventions and the wider architecture live in
[../AGENTS.md](../AGENTS.md) (see its **Frontend** notes). This file is the
authority for anything visual.

---

## 1. Design tokens

Everything below is a `var(--token)`. The token table *is* the contract.

### Color

| Token | Use |
|---|---|
| `--color-bg`, `--color-bg-secondary` | Page background |
| `--color-surface`, `--color-surface-hover` | Card / control fills |
| `--color-border`, `--color-border-hover` | 1px borders (default / hover) |
| `--color-primary`, `--color-primary-hover`, `--color-primary-glow` | Primary actions, focus glow |
| `--color-secondary`, `--color-accent` | Scrollbar ramp, small accents |
| `--color-text`, `--color-text-secondary`, `--color-text-muted` | Text hierarchy (high → low) |
| `--color-success` / `--color-warning` / `--color-error` / `--color-info` | Status |
| `--color-success-glow` / `--color-warning-glow` / `--color-error-glow` / `--color-muted-glow` | Soft status halos (shadows, badges, dots) |

### Gradients

- `--gradient-primary` — primary buttons and brand accents (135° blue ramp).
- `--gradient-surface` — header / footer / large surfaces.
- `--gradient-card` — card and panel backgrounds.

Use gradients only for primary CTAs and surfaces. Body buttons stay flat
(`--color-surface`).

### Radius

| Token | px | Use |
|---|---|---|
| `--radius-sm` | 6 | Inputs, selects, icon-only buttons, small chips, log lines |
| `--radius-md` | 12 | **Text buttons**, badges, inline cards, file rows |
| `--radius-lg` | 16 | Modals, panels, cards |
| `--radius-xl` | 24 | Hero / large containers |
| `--radius-pill` | 999 | Pills, status chips, toggle tracks, status dots |

Two-tier rule: **controls (inputs/selects/icon buttons) = `sm`; text buttons =
`md`; containers (cards/panels/modals) = `lg`.** Don't mix.

### Control height (sizing scale)

| Token | px | Use |
|---|---|---|
| `--control-height-sm` | 36 | **Base `.btn` floor**, compact buttons, `.btn-sm`, icon-only |
| `--control-height` | 44 | Form controls (input/select); buttons promoted for more presence |
| `--control-height-lg` | 56 | Prominent action rows (prompt actions, developer-tools tiles) |
| `--control-height-xl` | 72 | Reserved tier — defined but not currently in use |

Every `.btn` floors at `--control-height-sm` (36) so unrelated buttons never
render at jittery 32/33/35 px. Larger tiers are opt-in **per group**.

**Consistency rule:** every button in the same visual group uses the **same
height tier**. A row of related buttons must share one tier (don't mix 36 and
56). Set the tier on the container (e.g. `.prompt-actions-row > .btn`), not
per-button.

### Spacing

`--spacing-xs|sm|md|lg|xl|2xl` (0.25 → 3rem) plus semantic
`--panel-padding` (1.25rem), `--panel-gap` (1rem), `--section-gap` (1.5rem),
`--layout-gap` (1.5rem). Use the semantic ones for panel internals and the
grid gaps; use the scale for everything else.

### Shadow & blur

- `--shadow-sm|md|lg` (elevation), `--shadow-glow` (primary halo).
- `--blur-sm|md|lg` (8 / 16 / 24px) for backdrop blur. Modal backdrops use
  `--blur-sm`; the header uses `--blur-md`. The page-behind-modal blur is a
  fixed `6px` (see §3).

### Motion

| Token | ms | Use |
|---|---|---|
| `--transition-fast` | 150 | Buttons, hovers, inputs, connectors |
| `--transition-normal` | 250 | Modals, panels, cards, page blur |
| `--transition-slow` | 400 | Section entrances |

Animate only `opacity`, `transform`, `background-color`, `border-color`,
`box-shadow`, `color`, `filter`. Never animate `all` on layout-affecting
properties. See §11 for the full motion catalog.

### Layout

- `--site-max-width` — `1760px`. Header, main, and footer content all center
  within this width.

---

## 2. Theme

There is **one theme**: a dark, blue-accented surface, defined entirely by the
`:root` tokens above. There is no light mode and no runtime theme switch.

- "Theme" in the UI copy refers to the **question theme** dropdown
  (`.prompt-theme-row` / `.prompt-dropdown-select`), not a color scheme.
- To re-theme the whole app, change the token values in `:root` — never patch
  colors at the component level. Because every surface, border, status, and
  shadow reads from a token, one edit propagates everywhere.
- Status meaning is carried by the semantic colors (`--color-success`, etc.)
  and their `*-glow` halos. Never reach for a raw hex to signal status.

---

## 3. Page layout & scaffold

The page is a single vertical column: **outage banner → header → main → footer**,
with `#root` and `body` as `flex` columns filling `min-height: 100vh`.

| Region | Class | Notes |
|---|---|---|
| Outage notice | `.outage-banner` (+ `.outage-banner-detail`) | Full-width red gradient strip above the header; only rendered during an outage |
| Header | `.header` → `.header-content` | Sticky (`top: 0`, `z-index: 100`), `--gradient-surface`, `--blur-md`, bottom border; holds `.logo` + `.uconn-mark` |
| Main | `.main-content` → `.content-shell` | Centered to `--site-max-width`; `clamp()` padding; `.content-shell` stacks sections with `--section-gap` |
| Footer | `.footer` → `.footer-content` | `--gradient-surface`, top border, centered |

Primary content grids inside `.content-shell`:

- `.top-panels` — two equal columns (`1fr 1fr`, `--layout-gap`) for the prompt
  card + developer-tools card. Collapses to one column at **≤1100px**.
- `.pipeline-split` — workflow panel + log panel (`~1.9fr` / `minmax(300px,1fr)`).
  Collapses to one column at **≤1100px**.

**Modal-open dimming:** when any modal is open the body gets `.modal-open`,
which applies `filter: blur(6px)` to `.header`, `.main-content`, and `.footer`
(transition `--transition-normal`) and locks page scroll (see §8).

**Responsive breakpoints** (mobile-last, `max-width`): `1200` (downloads → one
column), `1100` (top-panels & pipeline → one column), `900` (prompt actions &
dev-tools wrap), `820` (eval modal stacks), `768` (header stacks), `700`
(modal footers, create-data rows, agent modal stack). Reuse these — don't add
new arbitrary breakpoints.

---

## 4. Buttons

Base class `.btn` provides layout, `--radius-md`, `--transition-fast`, a tactile
`:active` press (`translateY(1px)`), a `:focus-visible` ring, and `:disabled`
(opacity 0.6). **Always combine `.btn` with exactly one variant.**

| Class | Looks like | Use for |
|---|---|---|
| `.btn .btn-primary` | Gradient fill, white text, glow on hover | The one main action of a view |
| `.btn .btn-secondary` | Surface fill, primary text, border | Default / neutral actions |
| `.btn .btn-ghost` | Transparent, border | Low-emphasis inline actions |
| `.btn .btn-danger` | Red-tinted fill + border | Destructive (delete, stop) |
| `.btn-icon-only` | 36×36 square, `--radius-sm` | Icon-only controls (no `.btn` needed) |
| `.btn-feedback` / `.btn-error-feedback` | Locked green/red state | Transient post-click confirmation (success/error) |

Sub-variants that already exist (reuse, don't re-create): `.btn-remove`
(outlined error), `.help-btn` (outlined primary), `.btn-download` /
`.btn-preview` / `.btn-eval-results` (compact 36px row actions),
`.test-file-toggle-btn`.

Modifiers (stack on top of a variant):

- `.btn-sm` — compact (`--control-height-sm`, 0.8rem).
- `.btn-fixed-width` — fixed 144px for equal-width groups.
- `.btn-icon` — sets the 18×18 leading icon inside any `.btn`.
- `.btn-label` — keeps the text label from shrinking.

Size tiers are applied by **container context**, per the consistency rule:

- Prompt action row → `--control-height-lg` (56) via `.prompt-actions-row > .btn`.
- Developer-tools tiles → `--control-height-lg` (56) via `.developer-tools-row`.
- Everything else → default `--control-height` (44), or `.btn-sm` (36).

Never create a new `*-btn` class with bespoke padding/font/height. Reuse a
variant + the container tier.

---

## 5. Form controls

Inputs, selects, and textareas share: `--color-surface` fill, `--color-border`,
`--radius-sm`, and the focus ring
`box-shadow: 0 0 0 3px var(--color-primary-glow)` with
`border-color: var(--color-primary)`.

- Single-line controls (`input`, `select`, `.prompt-dropdown-select`,
  `.input-to-agent-select`): height `--control-height` (44).
- `textarea` (`.prompt-input`): `min-height: 180px`, `resize: vertical`,
  `--radius-md` (the one intentional exception — large text areas read better
  with the button radius).
- Placeholder text: `--color-text-muted`.
- Disabled controls: `opacity ~0.55`, `cursor: not-allowed`.

---

## 6. Toggle switches

For boolean settings (e.g. the developer-tools options), use
`.developer-toggle` — a label-wrapped checkbox styled as a switch, **not** a
raw checkbox:

```
.developer-toggle              → pill-shaped surface control, hidden native <input> overlay
  .developer-toggle-track      → 38×22 pill; grey off → green (+inset ring) on :checked
  .developer-toggle-thumb      → 16×16 dot; slides translateX(16) on :checked
  .developer-toggle-label      → text
```

It exposes the standard `:focus-visible` primary ring and a disabled state
(`:has(input:disabled)` → 0.6 opacity). Reuse this pattern for any on/off
control rather than hand-rolling a switch.

---

## 7. Cards & panels

- `.card` → `--gradient-card`, `--color-border`, `--radius-lg`, `--shadow-md`,
  `--blur-md`; transitions border/shadow/background on `--transition-normal`.
  **Cards do not lift or scale on hover** (`transform: none` is intentional) —
  only color/shadow may shift.
- `.card-header` → header band with bottom border. Title is `h2`
  (`--color-primary`, soft glow); optional `.subtitle` (`--color-text-secondary`)
  and `.card-header-row` / `.header-buttons` for a title + actions row.
- `.card-body` → `--panel-padding`.
- Status panel variants: `.completed-card` (success border + glow),
  `.failed-card` (error border + glow), `.main-card`, `.developer-tools-card`,
  `.workflow-panel`, `.log-panel-card`. These restyle the border/glow of `.card`
  — they don't replace it.
- Inner sections use `--panel-padding` and `--panel-gap`.

---

## 8. Modals

**One shell for every dialog**, built by
[`components/modals/Modal.jsx`](src/components/modals/Modal.jsx). Never
hand-roll an overlay.

### Structure

```
.modal                → fixed overlay, centers content, fade via .is-visible (250ms)
  .modal-backdrop     → rgba(0,0,0,.72) + blur(--blur-sm); click closes
  .modal-content      → gradient surface, --radius-lg, --shadow-lg
                        enters with translateY(-16px) scale(.97) → 0/1
    .modal-header     → title (h3) + .btn-icon-only close, bottom border
    .modal-body       → scrolls, flex:1 (use .modal-body-flush for zero padding,
                        .modal-body-stack to stack children with --panel-gap)
    .modal-footer     → right-aligned actions, top border, gap --spacing-sm
```

### `Modal.jsx` API

| Prop | Effect |
|---|---|
| `isOpen` | Toggles `.is-visible`; drives mount/scroll-lock |
| `onClose` | Called on backdrop click and Escape |
| `title` | Rendered in `.modal-header` h3 (wired to `aria-labelledby`) |
| `large` | Adds `.modal-large` (800px) |
| `size` | Adds `.modal-{size}` (e.g. `full`); takes precedence over `large` |
| `modalClassName` | Extra class on `.modal` (per-modal theming hooks) |
| `contentClassName` | Extra class on `.modal-content` |
| `modalBodyClassName` | Extra class on `.modal-body` |
| `bodyClassName` | Class toggled on `document.body` while open |
| `footer` | Renders `.modal-footer`; omit for no footer |

Built-in behaviors (don't reimplement): portal into `document.body`, backdrop
click + Escape to close, **focus trap** (Tab/Shift-Tab cycle), autofocus first
focusable, focus restored to the trigger on close, and **page scroll lock**
(pins the window, still allows scrolling inside an overflowing `.modal-body`).
`role="dialog"` / `aria-modal` are set while open.

### Width / size variants

| Class | Width |
|---|---|
| default | 500px |
| `.modal-large` | 800px |
| `.modal-full` | `min(96vw, 1500px)` |

Specialized fulls extend `.modal-full`: `.pdf-preview-modal` (`min(98vw,1600px)`,
tall), `.eval-results-modal` (`min(98vw,1680px)`). All still flow through
`Modal.jsx`.

Footer actions are `.btn` (typically one `.btn-primary` + one `.btn-secondary`).
For a split footer (info left, actions right) use `.modal-footer-row` /
`.agent-modal-footer` + `.modal-footer-actions`.

---

## 9. Agent pipeline

The workflow visualization is a horizontal track of fixed agent cards with an
SVG connector layer behind them.

- `.agent-card` — fixed **220×224**, `--gradient-card`; status drives the border
  + glow via `[data-status]` (`queued`/`thinking`/`warning`,
  `completed`/`success`, `errored`/`error`, `cancelled`). Cards reveal with
  `.is-revealed` (fade + rise + scale).
- `.agent-icon` cross-fades four stacked SVGs (default / thinking / success /
  error) by status; the thinking icon pulses (`iconPulse`).
- `.agent-connector-path` (inside `.agent-turn-connector`) recolors to the
  status color and fades in via `data-visible` / `data-status`.
- `.agent-placeholder` (with pulsing `.agent-placeholder-brain`s) holds the
  empty state until the first card reveals (`.is-hidden` fades it out).

Reuse the `[data-status]` convention for any new pipeline state rather than
inventing per-state classes.

---

## 10. Status & feedback

Status is expressed with the semantic color + its glow, never a raw hex:

- **Log lines** (`.log-item`) derive their left-edge accent from `--log-accent`,
  set by `.log-success` / `.log-warning` / `.log-error` / `.log-info`.
- **Status dots** (`.status-indicator.{inactive|thinking|completed|errored|cancelled}`)
  — 12px circles with the matching `--color-*` + `--color-*-glow`; `thinking`
  pulses.
- **Eval chips** — `.eval-status-chip` and `.eval-score-chip` (`.is-pass` /
  `.is-fail` / `.is-neutral`) carry pass/fail/neutral coloring; same green/red/
  grey token families.
- **Create-data pills** — `.create-data-pill` (+ `.is-running` / `.is-completed`
  / `.is-errored` / `.is-cancelled` / idle states); the running pill pulses.
- **Badges / step chips** — `.eval-source-id`, `.create-data-step-chip`,
  `.rubric-index` / `.rubric-category`, `.eval-reference-kind`: pill or `sm`
  rounded tags on a tinted surface.

Empty states: `.log-empty-state` / `.modal-empty-state` (dashed border, muted
text); error variant `.modal-empty-state-error`.

---

## 11. Motion & transitions

Use the motion tokens (§1) for durations and only animate the safe properties.
The named keyframes already in the system (reuse before adding new ones):

| Keyframe | Where |
|---|---|
| `fadeInSlide` | Section entrance (`.agents-section`) |
| `pulse` | Thinking status dot |
| `iconPulse` | Thinking agent icon |
| `brainIdlePulse` | Idle placeholder brains |
| `createDataStatusPulse` / `createDataRowPulse` | Running create-data pill / row |
| `logoPulse` | Logo glow (defined; currently opt-in) |

Component-level motion rules:

- Modal: backdrop `opacity`, content `opacity` + `translateY`/`scale`, on
  `--transition-normal`.
- Buttons / inputs / connectors: `--transition-fast`.
- Cards & page-blur: `--transition-normal`; cards never transform on hover.
- Honor reduced-motion intent by keeping animations to opacity/transform/filter
  so they degrade gracefully.

---

## 12. Scrollbars, selection, focus

These are global and themed — don't override per component:

- Custom WebKit scrollbar: 10px, gradient thumb (`--color-primary` →
  `--color-secondary`), rounded. `html`/`body` also set
  `scrollbar-color`/`scrollbar-width` for Firefox.
- `::selection` → `--color-primary` background, white text.
- Global `:focus-visible` → `2px solid var(--color-primary)` outline with
  offset; `.btn` / controls additionally show the 3px `--color-primary-glow`
  ring.

---

## 13. Checklist before shipping UI

1. Reuse a documented `.btn` variant — no new `*-btn` classes.
2. Buttons in one group share a height tier (set on the container).
3. Inputs/selects use `--control-height` + `--radius-sm` + the standard focus ring.
4. Boolean settings use `.developer-toggle`, not a raw checkbox.
5. Modals go through `Modal.jsx`; pick a size variant, don't hand-roll widths.
6. Panels/cards use `.card` + a documented status variant; no hover lift.
7. Status uses the semantic color + glow (chips/pills/dots), never a raw hex.
8. Colors/spacing/radius/motion/layout come from tokens, not literals.
9. Transitions use the motion tokens and only animate safe properties; reuse the
   named keyframes.
10. New responsive rules reuse the existing breakpoints (§3).
