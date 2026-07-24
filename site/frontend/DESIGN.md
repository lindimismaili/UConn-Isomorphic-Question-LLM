# Frontend Design System

The single source of truth for UI elements. **Before adding a button, modal,
panel, input, card, badge, toggle, or color, find it here and reuse the
documented class.** Do not invent new one-off styles; if something genuinely new
is needed, add it here first so it stays consistent.

**Templates-first (§14).** Feature code composes UI **only** from the reusable
primitives in [`src/components/ui/`](src/components/ui/) (the "templates") — not
from raw `<button className="btn …">` / `<input>` / ad-hoc class strings. If a
genuinely new element is needed, add it as a `ui/` primitive (and to the
showcase) **first**, then use it. The live catalog of every building block is the
**design-system showcase** at `design-system.html`
(`npm -C frontend run dev` → `/design-system.html`).

All values live as CSS custom properties in [`src/styles/app.css`](src/styles/app.css)
`:root`. Reference the tokens — never hardcode hex, px, rem, or ms.

Repo-level conventions and the wider architecture live in
[../../AGENTS.md](../../AGENTS.md) (see its **Frontend** notes). This file is the
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
| `--color-navy` (`#000E2F`) | UConn brand navy — masthead/footer surface, emphasis text, heading color |
| `--color-primary`, `--color-primary-hover`, `--color-primary-glow` | Primary actions, focus glow (UConn-blue accent `#0A5BC4`) |
| `--color-secondary`, `--color-accent` | Scrollbar ramp, small accents |
| `--color-text`, `--color-text-secondary`, `--color-text-muted` | Text hierarchy (high → low) |
| `--color-success` / `--color-warning` / `--color-error` / `--color-info` | Status |
| `--color-success-glow` / `--color-warning-glow` / `--color-error-glow` / `--color-muted-glow` | Soft status halos (shadows, badges, dots) |

**Tinted rgba come from `-rgb` channel tokens, not baked hex.** Every tinted
border/fill/glow uses `rgba(var(--color-primary-rgb), α)` (also
`--color-navy-rgb`, `--color-secondary-rgb`, `--color-accent-rgb`, and the
status `-rgb` tokens). This is why one accent edit re-themes the whole app — do
**not** reintroduce baked `rgba(91,141,238,…)`-style literals.

### Typography tokens

`--font-sans` (IBM Plex Sans), `--font-serif` (Source Serif 4 — academic
headings), `--font-mono` (code/IDs). A size scale `--font-size-xs|sm|md|base|lg|xl|2xl`
and `--line-height-tight|snug|normal`. Reference these — never re-hardcode a
font-family string or a raw `rem` size.

### Stacking (z-index)

`--z-base` (1), `--z-connector` (2), `--z-header` (100), `--z-modal` (1000).
Modal stacking still derives `1000 + depth` in `Modal.jsx` (see §8) — the token
is the header/base/connector baseline.

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
- `--blur-sm|md|lg` (8 / 16 / 24px) for supported surface effects such as the
  header's `--blur-md` treatment. Modal backdrops intentionally use no blur
  (see §3 and §8).

### Motion

| Token | ms | Use |
|---|---|---|
| `--transition-fast` | 150 | Buttons, hovers, inputs, connectors |
| `--transition-normal` | 250 | Modals, panels, cards |
| `--transition-slow` | 400 | Section entrances |

Animate only `opacity`, `transform`, `background-color`, `border-color`,
`box-shadow`, `color`, `filter`. Never animate `all` on layout-affecting
properties. See §11 for the full motion catalog.

### Layout

- `--site-max-width` — `1760px`. Header, main, and footer content all center
  within this width.

---

## 2. Theme

There is **one theme**: a **bright, academic UConn (Physics)** look — a light,
blue-tinted canvas with near-white surfaces, UConn navy (`#000E2F`) for the
masthead/footer + brand + emphasis text, and a vivid readable UConn-blue accent
(`#0A5BC4`) for interaction. Serif headings carry the academic signal; a subtle
physics motif sits behind the navy masthead. There is no dark mode and no
runtime theme switch. All text pairs are verified **WCAG-AA**.

- "Theme" in the UI copy refers to the **question theme** dropdown
  (`.prompt-theme-row` / `.prompt-dropdown-select`), not a color scheme.
- To re-theme the whole app, change the token values in `:root` — never patch
  colors at the component level. Because every surface, border, status, and
  shadow reads from a token (and every tint from a `-rgb` channel token), one
  edit propagates everywhere.
- Shadows are **soft navy-tinted elevation** (`rgba(var(--color-navy-rgb), …)`),
  not heavy black drops — the canvas is light. Glows are subtle tints, not neon
  halos. Keep new elevation on the `--shadow-*` tokens.
- Status meaning is carried by the semantic colors (`--color-success`, etc.)
  and their `*-glow` halos. Never reach for a raw hex to signal status.
- The navy masthead + footer are the two surfaces that carry **light text on
  navy** (white / `rgba(255,255,255,α)`); everywhere else uses the dark text
  tokens on the light canvas.

---

## 3. Page layout & scaffold

The page is a single vertical column: **outage banner → header → main → footer**,
with `#root` and `body` as `flex` columns filling `min-height: 100vh`.

| Region | Class | Notes |
|---|---|---|
| Outage notice | `.outage-banner` (+ `.outage-banner-detail`) | Full-width tinted-error strip above the header; only rendered during an outage. Render via the `Banner` primitive (§14) |
| Header / hero | `.header` (+ `.has-hero` + `.header-motif`) → `.header-content` | Sticky (`top: 0`, `z-index: var(--z-header)`), `--gradient-surface` (navy) masthead with a physics motif behind (`.header-motif`, pointer-inert); holds `.logo` (title + `.mode-select` site-mode dropdown, §15) + `.uconn-mark`. Render via the `PageHeader` primitive |
| Main | `.main-content` → `.content-shell` | Centered to `--site-max-width`; `clamp()` padding; `.content-shell` stacks sections with `--section-gap` |
| Footer | `.footer` → `.footer-content` | `--gradient-surface` (navy), top accent border, centered. Render via the `Footer` primitive |

The whole scaffold is composed by the **`AppShell`** primitive (§14):
`AppShell` takes `banner` / `header` / `footer` slots + children (main content).
`App.jsx` and the design-system showcase compose the **same** chrome from
`src/sections/SiteChrome.jsx` (`SiteHeader` / `SiteBanner` / `SiteFooter`) — the
header/footer/banner exist once, never as duplicated raw markup.

Primary content grids inside `.content-shell`:

- `.top-panels` — two equal columns (`1fr 1fr`, `--layout-gap`) for the prompt
  card + developer-tools card. Collapses to one column at **≤1100px**. The
  developer-tools card's flex body (`flex: 1 1 0`) gets its height from
  `align-items: stretch` matching the taller prompt card; **once stacked it has
  no sibling to stretch against**, so the ≤1100px rules give it a `min-height`
  floor + content-sized body (`flex: 1 1 auto`) + a log-area `min-height` — without
  these the body collapses and `overflow: hidden` clips the log entirely.
- `.pipeline-split` — workflow panel + log panel (`~1.9fr` / `minmax(300px,1fr)`).
  Collapses to one column at **≤1100px**.
- `.admin-dashboard` — protected operations view; composes existing cards, tabs,
  data tables, toggles, buttons, and modal shell. Its summary grid is four-up
  on desktop, two-up at ≤1100px, and one-up at ≤700px. It never introduces a
  separate theme or control style. It displays canonical logical storage paths
  (for example, `/site/backend/outputs`) rather than marketing labels, and its
  live cards refresh on the established 30-second interval. The key-replacement
  field is the documented `.admin-key-input`: a full-width standard-height,
  tokenized input with the usual primary focus ring; use it for operational
  numeric settings that need the same visual treatment. Health summary cards
  place the existing semantic `StatusDot` in their upper-right corner. File
  roots and download actions remain standard white secondary buttons; the file
  name itself opens a folder or preview, while the final table column performs
  the explicit download. Long audit histories use an internally scrollable
  table shell rather than extending the page indefinitely.

**Modal-open dimming:** when any modal is open the body gets `.modal-open` to
lock page scroll. The modal's own backdrop supplies the smoothly faded navy tint;
do not add a page-wide or `backdrop-filter` blur, which can make the opening
frame jump on consumer browsers (see §8).

**Responsive breakpoints** (mobile-last, `max-width`): `1200` (downloads → one
column), `1100` (top-panels & pipeline → one column), `900` (dev-tools tiles
wrap; prompt actions **shrink to stay on one row**, never stack — theme select,
Upload, and Send share the row at every width), `820` (eval modal stacks),
`768` (header stacks), `700` (modal footers, create-data rows, agent modal
stack). Reuse these — don't add new arbitrary breakpoints.

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

Developer-tools tiles get narrow (4 across on a half-width card), so inside
`.developer-tools-row` the icon is pinned left + vertically centered and the
`.btn-label` wraps to at most two lines then ellipsizes (`-webkit-line-clamp: 2`)
— text never overflows the button. This is the one place `.btn`'s default
`nowrap` / centered layout is intentionally overridden.

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

**Custom dropdown (`.dropdown*`).** For list selectors the native `<select>` is
replaced by the `Dropdown` primitive so the control *and* its option list are
fully site-themed. This is a genuinely new element, so it owns the `.dropdown*`
class family — the **documented exception** to the §14 "primitives add no new
CSS" rule:

- `.dropdown` (root) → `.dropdown-trigger` (the button; mirrors the `select`
  box, same `--color-surface` fill / `--color-border` / `--radius-sm` / focus
  ring), `.dropdown-trigger-label(.is-placeholder)`, `.dropdown-chevron`
  (rotates 180° on open).
- `.dropdown-menu` is **portaled to `<body>`** and `position: fixed` (computed
  from the trigger), so it is never clipped by an `overflow:hidden` shell or a
  scrolling modal body; it opens upward (`.is-up`) when there's no room below and
  sits at `--z-popover` (just above `--z-modal`) to clear stacked modals.
- `.dropdown-option(.is-highlighted/.is-selected/.is-disabled)` — the selected
  state ports `select option:checked`.
- `.dropdown-sm` matches the `--control-height-sm` tier; `.dropdown-onDark` is
  the navy-masthead trigger variant (the menu itself stays light).
- The native `Select` primitive remains for plain form selects.

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
control rather than hand-rolling a switch. Like the dev-tool tiles, the
`.developer-toggle-label` wraps to two lines then ellipsizes so a long label
never pushes the track out of the control.

---

## 7. Cards & panels

- `.card` → `--gradient-card`, `--color-border`, `--radius-lg`, `--shadow-md`,
  `--blur-md`; transitions border/shadow/background on `--transition-normal`.
  **Cards do not lift or scale on hover** (`transform: none` is intentional) —
  only color/shadow may shift. **One documented exception:** the clickable
  `.agent-card` (§9) lifts `translateY(-4px)` with a white outline on hover.
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
  .modal-backdrop     → navy tint rgba(var(--color-navy-rgb), .45), opacity fade; click closes
  .modal-content      → --gradient-card (light, same family as .card), --radius-lg, --shadow-lg
                        enters with a short upward translate + opacity → 0/1
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

### Stacking

Modals may open on top of other modals (e.g. a file viewer from inside the
Agents modal). `Modal.jsx` tracks a global open-modal stack and:

- assigns each open modal `z-index: 1000 + stackIndex`, so a modal always
  layers **above** the modal it was opened from;
- dims with the standard backdrop only on the **first (bottom)** modal; every
  stacked modal's backdrop gets `.is-stacked` (transparent) so layers do not
  compound the dimming;
- only the topmost backdrop handles click-to-close, and a closing modal keeps
  its depth through the exit transition.

This is automatic — never set z-index on a modal or restyle `.modal-backdrop`
per component.

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

### File viewers

Workflow output files are viewed through **four generic viewers**, not
per-file-type bespoke modals. Dispatch is by `getViewerType(fileName)` in
[`src/utils/files.js`](src/utils/files.js): `.pdf` → `PdfPreviewModal`, `.csv` →
`CsvViewerModal`, `.tex` → `TexViewerModal`, **everything else** →
`JsonTextViewerModal` (pretty-printed JSON/JSONL with syntax coloring, plain
text otherwise). Every file is therefore viewable.

Don't wire viewers by hand — use the
[`useFileViewer(workflowId)`](src/components/modals/useFileViewer.jsx) hook. It
owns the open state and returns `openFile`, `downloadFile`, `getFileUrl`, and a
`viewerModals` element you render once in the host component.

Code-style viewers (`.tex` and JSON) share one line-numbered shell:
`.tex-pre` → `.tex-line` (`.tex-line-num` + `.tex-line-content`). Token colors:
`.tex-command` / `.tex-comment` / `.tex-brace` for LaTeX, and
`.json-key` / `.json-string` / `.json-number` / `.json-bool` / `.json-null` /
`.json-punct` for JSON. Reuse these classes for any new code/source viewer.

The three **rich domain modals** — `FinalResultModal` ("Final Result"),
`EvalResultsModal` ("Evaluations"), `WorkflowAgentsModal` ("Agents") — are *not*
file-type dispatched. They are opened by the explicit View Results / View
Evaluations / View Agents buttons in `ResultsSection` and rendered alongside
`viewerModals`. Their full-size variants extend `.modal-full` (§8 width table).

---

## 9. Agent pipeline

The workflow visualization is a horizontal track of fixed agent cards with an
SVG connector layer behind them.

- `.agent-card` — fixed **220×224**, `--gradient-card`; status drives the border
  + glow via `[data-status]` (`queued`/`thinking`/`warning`,
  `completed`/`success`, `errored`/`error`, `cancelled`). Cards reveal with
  `.is-revealed` (fade + rise + scale). Because cards are clickable they are the
  one `.card` that **lifts on hover**: `translateY(-4px)` + white outline
  (`rgba(255,255,255,0.85)`) + `--shadow-lg`. The `-4px` lift stays inside the
  scroll container's 34px top padding, so it isn't clipped by
  `.agents-scroll { overflow-y: hidden }`.
- `.agent-icon` cross-fades four stacked SVGs (default / thinking / success /
  error) by status; the thinking icon pulses (`iconPulse`).
- `.agent-connector-path` (inside `.agent-turn-connector`) recolors to the
  status color and fades in via `data-visible` / `data-status`.
- `.agent-placeholder` (with pulsing `.agent-placeholder-brain`s — **6** of them,
  matching the visible pipeline length) holds the empty state until the first
  card reveals (`.is-hidden` fades it out).
- `.agent-node-ghost` — dashed card-sized skeleton blocks shown *while a run is
  active*, padding the row ahead of the revealed cards. `AgentPipeline.jsx`
  computes the count as "fill the visible row for the current viewport width,
  **plus one** peeking past the edge to hint the next stage," capped at the
  pipeline stages still to come (`slotsBeforeScroll - visibleKeys.length + 1`,
  clamped to `remainingPipelineSlots`). They taper to zero at the final agent.

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
- **Generic chip** — `.chip` (+ `.chip-neutral` / `.chip-success` /
  `.chip-warning` / `.chip-error`, `.chip-square`, `.chip-mono`) is the
  consolidated small-tinted-tag primitive (`Chip`, §14). Prefer it for new tags
  over adding another bespoke `*-id` / `*-node` tag class.

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

- Modal: backdrop `opacity`, content `opacity` + a short `translateY`, all on
  `--transition-normal`. Avoid modal blur filters: the navy tint preserves focus
  without the browser-specific compositing jump.
- Buttons / inputs / connectors: `--transition-fast`.
- Cards and app shells: `--transition-normal`; cards never transform on hover.
- Honor reduced-motion intent by keeping animations to opacity/transform/filter
  so they degrade gracefully.

---

## 12. Scrollbars, selection, focus

These are global and themed — don't override per component:

- Custom WebKit scrollbar: 10px, soft light-blue gradient thumb
  (`color-mix` of the primary/secondary tokens with white — an affordance, not
  a deep brand stripe; deepens slightly on hover). This global `::-webkit-scrollbar` is the
  **single source of truth** for every scrollbar (page + panels alike).
  **Don't set `scrollbar-color`/`scrollbar-width` on `body` or any element:**
  modern Chrome now honors the standard `scrollbar-color`, which overrides the
  gradient and turns *that* element's scrollbar into a flat color that visibly
  diverges from the rest. `html` keeps only `scrollbar-gutter: stable`.
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
6. Panels/cards use `.card` + a documented status variant; no hover lift — the
   clickable `.agent-card` is the one documented exception (§9).
7. Status uses the semantic color + glow (chips/pills/dots), never a raw hex.
8. Colors/spacing/radius/motion/layout come from tokens, not literals.
9. Transitions use the motion tokens and only animate safe properties; reuse the
   named keyframes.
10. New responsive rules reuse the existing breakpoints (§3).
11. The element is rendered through a `ui/` primitive (§14) — no raw `.btn` /
    control / chip class strings in feature code. If it's genuinely new, it was
    added as a `ui/` primitive and to the showcase first.

---

## 14. Component library (templates-first)

The whole UI is composed from a small set of reusable **primitives** in
[`src/components/ui/`](src/components/ui/), exported from the barrel
[`src/components/ui/index.js`](src/components/ui/index.js):

```js
import { Button, Card, CardHeader, CardBody, TextInput, Select, TextArea,
         ToggleSwitch, Badge, StatusChip, ScoreChip, Pill, Chip, StatusDot,
         LogItem, LogList, EmptyState, Tabs, DataTable, KeyValueTable, IconButton,
         SectionHeading, AppShell, PageHeader, Banner, Footer, Modal,
         Avatar, ChatMessage, ChatTranscript, TypingIndicator, Composer,
         Stepper }
  from './components/ui';
```

Each primitive is a **thin wrapper that emits the documented classes above** —
it adds no new CSS. The class contract (this file) is unchanged; primitives just
make it the only way features build UI, so consistency is enforced by
construction rather than by review.

| Primitive | Wraps (this doc) | Key props |
|---|---|---|
| `Button` | §4 `.btn` + variant | `variant` (`primary\|secondary\|ghost\|danger`), `size` (`sm`), `icon`, `fixedWidth`, `feedbackState` (`success\|error`), `className` (premade sub-variants), `...rest` |
| `IconButton` | §4 `.btn-icon-only` | `icon`, `aria-label` |
| `TextInput` / `TextArea` / `Select` | §5 controls | native props; `Select` takes `options`/`placeholder` |
| `Dropdown` | §5 `.dropdown*` (custom listbox) | `value`, `onChange(value)`, `options` (`{value,label,disabled?}`), `placeholder`, `size` (`sm`), `variant` (`onDark`), `disabled`, `aria-label`, `id` |
| `ToggleSwitch` | §6 `.developer-toggle` | `checked`, `onChange`, `label`, `disabled` |
| `Card` / `CardHeader` / `CardBody` / `SectionHeading` | §7 `.card` | `variant` (`completed\|failed\|main\|developer-tools\|workflow\|log-panel`), `title`, `subtitle`, `actions`, `disclaimer` |
| `Badge` / `StatusChip` / `ScoreChip` / `Pill` | §10 chips/pills | `kind`/`tone`/`status`/`state` → the documented `is-*` / `eval-*` classes |
| `Chip` | §10 `.chip` (generic tag) | `tone` (`primary\|neutral\|success\|warning\|error`), `shape` (`pill\|square`), `mono` — consolidates the small tinted-tag look |
| `StatusDot` / `LogItem` / `LogList` / `EmptyState` | §10 | `status` / `level` / `entries` / `variant`+`error` |
| `Tabs` | §8 `.viewer-tab-bar` | `value`, `onChange`, `tabs` |
| `DataTable` / `KeyValueTable` | §8/§10 tables | `variant` (`eval\|file-viewer`) / `rows` |
| `AppShell` | §3 scaffold | `banner` / `header` / `footer` slots + children → `.main-content` / `.content-shell` |
| `PageHeader` | §3 `.header` masthead | `brand`, `aside`, `motif` (renders `.header-motif` hero overlay) |
| `Banner` | §3 `.outage-banner` | `children`, `detail`, `role` |
| `Footer` | §3 `.footer` | `children` → `.footer-content` |
| `Modal` | §8 shell | re-exported from `components/modals/Modal.jsx` (unchanged) |
| `Avatar` | §15 `.chat-avatar` | `role` (`user\|assistant\|agent\|runtime\|guardrail`), `icon`, `label` |
| `ChatMessage` | §15 `.chat-message` | `variant` (`user\|assistant\|agent`), `avatar`, `author`, `timestamp`, `actions`, `tone` (`success\|error\|blocked\|thinking`), `grouped` |
| `ChatTranscript` | §15 `.chat-transcript` | `count` (drives auto-scroll), `emptyState`, `children` |
| `TypingIndicator` | §15 `.chat-typing` | `label` |
| `Composer` | §15 `.composer` | `value`, `onChange`, `onSend`, `onAttach`, `isRunning`, `themeSelect`, `attachLabel`, `sendFeedback`/`attachFeedback` |
| `Stepper` | §15 `.stepper` | `nodes` (`{key,label,status,kind}`), `onNodeClick`, `activeKey` |

**Adding a new primitive** is exactly three steps:

1. Add the wrapper + a barrel export in `ui/` (no new CSS — if a class is
   missing, document it in the relevant section of this file first).
2. Add one entry to the catalog manifest
   [`src/design-system/manifest.jsx`](src/design-system/manifest.jsx) —
   `name`, `group`, `summary`, `props[]`, `usage`, and a live `Specimen`.
3. Add the class-contract assertion in `ui/ui.test.jsx`.

The showcase tile **auto-appears** from the manifest entry, and the coverage
test (`src/design-system/manifest.test.jsx`) asserts a strict 1:1 between
`ui/index.js` exports, manifest entries, and the primitive names in the table
above — a missing or orphaned entry (or a missing table row here) **fails CI**.

**Showcase:** [`design-system.html`](design-system.html) renders every manifest
entry as a searchable gallery tile (live specimen + prop table + copyable usage)
inside the app scaffold, organized Foundations → Components → Patterns. It's a
second Vite entry (`/design-system.html`); in dev builds the site footer links
to it (the link is stripped from production bundles).
[`src/design-system/manifest.jsx`](src/design-system/manifest.jsx) is the
catalog's single source of truth — this table is checked against it.

This component library is **frontend-only**: it is unrelated to the backend
`agents.json` registry and the prompt-versioning conventions.

---

## 15. Chat-first surface (conversation, process rail, live output)

The primary view is a **conversation**, not a form. It is composed only from the
chat primitives in [`src/components/ui/`](src/components/ui/) — feature code
never hand-rolls a bubble, transcript, rail, or composer. The whole surface is
assembled in [`src/sections/ChatConversation.jsx`](src/sections/ChatConversation.jsx),
which **projects** the presentation-agnostic reducer state
(`state.agents` / `state.logs` / `state.result` / `state.blocked`) into messages
via [`src/state/transcript.js`](src/state/transcript.js). The reducer is
untouched; only the view changed.

### Chat primitives

- `.chat-avatar` (+ `.chat-avatar-{user|assistant|agent|runtime|guardrail}`) —
  round identity chip; renders the role's default icon (`Avatar`).
- `.chat-message` (+ `.chat-message-{user|assistant|agent}`, optional
  `.chat-message-{success|error|blocked|thinking}` tone, `.is-grouped`) — one
  conversation turn: `.chat-message-avatar` + `.chat-message-body`
  (`.chat-message-header` → `.chat-message-author` + `.chat-message-time`,
  `.chat-message-content`, `.chat-message-actions`). User turns align right with
  the primary-gradient bubble; assistant/agent align left on a surface bubble.
  `.chat-message-embed` strips the bubble frame so a full `Card` (the
  `ResultsSection` / `FailureSection`) can render raw inside a turn.
- `.chat-transcript` — the scrollable conversation surface; `ChatTranscript`
  auto-scrolls to the newest message when its `count` grows, **unless** the user
  has scrolled up (pins within 80px of the bottom).
- `.chat-typing` (`.chat-typing-dots` → three `.chat-typing-dot`) — the
  animated "working" indicator (`TypingIndicator`).
- `.composer` — the pinned input that **supersedes** the old
  `.prompt-actions-row`: a `.composer-input` textarea + `.composer-controls`
  (theme `.composer-theme-select` on the left, `.composer-attach` +
  `.composer-send` on the right). Ctrl/⌘+Enter sends. `Composer` composes from
  `Button`/`Field` primitives, so it stays inside the templates-first contract.
  The theme select shares the Attach/Send **36px tier** (one control group, one
  height), and selecting a theme **never injects text into the textarea** — the
  selection is sent as the run's `theme` field and the select gets `.is-active`
  (primary border + glow) so it's visible that a theme prompt rides along. Its
  question textarea keeps the documented 180px large-textarea floor (five rows)
  so composing the request, rather than the empty state, receives the page's
  visual space.
- **Empty conversation hint** — `ChatTranscript.is-empty` becomes a compact,
  non-growing panel. Its `.chat-empty` row places the brain icon on the left,
  mode-aware guidance in the flexible middle, and the secondary “How it works”
  button at the right; at the existing ≤700px breakpoint the row stays intact
  and its guidance uses the smaller text token to preserve the controls.

### Stepper (process rail)

- `.stepper` — a compact inline rail rendering **all** registry nodes in order
  (`.stepper-node` + `.stepper-connector`), with a per-node status
  (`data-status`) and kind affordance (`data-node-kind`: runtime, `guardrail`
  gets the square shield badge, agent). Clicking a node fires `onNodeClick(key)`
  so the host opens that node's modal (runtime → `setup`/`output`;
  guardrail/agent → the agent detail modal). The node list is **caller-supplied
  from the registry** (`deriveStepperNodes`) — the Stepper never hardcodes keys.
- **Thinking word** — while a node is `thinking`, a cycling status word
  ("Thinking…", "Combobulating…", …) renders in the `.stepper-node-word` slot
  above its icon (`.stepper-node-word-text` fades in per word via
  `stepperWordIn`). The (empty) slot exists on every node so heights never
  shift. This replaced the old matrix `TokenStream` — the SSE stream is no
  longer consumed by the frontend.
- **Pre-run rail** — the rail is labeled “Watch the agents work!” and its title
  plus stepper are blurred, greyed, and pointer-inert until a workflow begins.
- **One-line labels** — `.stepper-node-label` is always a single ellipsized
  line (the full name lives in the node's `title` tooltip), so nodes stay one
  uniform height.
- **Auto-scroll** — the rail scrolls horizontally and auto-centers the
  `activeKey` node as agents become active; every node stays reachable by
  manual scroll too.

### Site modes & the header mode select

The header carries a **site-mode dropdown** (`.mode-select`, a `Select`
sub-variant) right after the title: **User / Teacher / Dev**. It uses the
light-on-navy masthead treatment (translucent white fill, white text/chevron,
36px tier); the popup list stays white/navy via the global `select option`
rule. Dev mode shows the developer-tools side panel (`.workspace` two-column);
user/teacher modes hide it (`.workspace.is-single`) and swap the
empty-conversation hint text. The choice persists in `localStorage` (`uiMode`).

### Blocked runs

A workflow can end in the terminal **`blocked`** status (a guardrail tripped).
The status payload carries a top-level `blocked` = `{category, reason, stage}`.
`useWorkflow` dispatches `WORKFLOW_BLOCKED`; the reducer marks the tripped
guardrail node `blocked` (status dot / `.stepper-node` / `.agent-card` all
support `data-status="blocked"` — warning-tinted) and records the block, and the
transcript shows a `.chat-message-blocked` bubble explaining the reason/category.
