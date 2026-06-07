# Frontend Design System

The single source of truth for UI elements. **Before adding a button, modal,
input, card, badge, or color, find it here and reuse the documented class.** Do
not invent new one-off styles; if something genuinely new is needed, add it here
first so it stays consistent.

All values live as CSS custom properties in [`src/styles/app.css`](src/styles/app.css)
`:root`. Reference the tokens — never hardcode hex, px, or ms.

---

## 1. Design tokens

### Color

| Token | Use |
|---|---|
| `--color-bg`, `--color-bg-secondary` | Page background |
| `--color-surface`, `--color-surface-hover` | Card / control fills |
| `--color-border`, `--color-border-hover` | 1px borders (default / hover) |
| `--color-primary`, `--color-primary-hover`, `--color-primary-glow` | Primary actions, focus glow |
| `--color-text`, `--color-text-secondary`, `--color-text-muted` | Text hierarchy (high → low) |
| `--color-success` / `--color-warning` / `--color-error` / `--color-info` | Status |
| `--color-*-glow` | Soft status halos (shadows, badges) |

### Gradients

- `--gradient-primary` — primary buttons and brand accents (135° blue ramp).
- `--gradient-surface` / `--gradient-card` — panel and card backgrounds.

Use gradients only for primary CTAs and surfaces. Body buttons stay flat
(`--color-surface`).

### Radius

| Token | px | Use |
|---|---|---|
| `--radius-sm` | 6 | Inputs, selects, icon-only buttons, small chips |
| `--radius-md` | 12 | **Buttons**, badges, inline cards |
| `--radius-lg` | 16 | Modals, panels, cards |
| `--radius-xl` | 24 | Hero / large containers |
| `--radius-pill` | 999 | Pills, toggles, status dots |

Two-tier rule: **controls (inputs/selects/icon buttons) = `sm`; text buttons = `md`; containers = `lg`.** Don't mix.

### Control height (sizing scale)

| Token | px | Use |
|---|---|---|
| `--control-height-sm` | 36 | **Base `.btn` floor**, compact buttons, `.btn-sm`, icon-only |
| `--control-height` | 44 | Form controls (input/select); buttons promoted for more presence |
| `--control-height-lg` | 56 | Prominent action rows (prompt actions) |
| `--control-height-xl` | 72 | Developer-tools tile buttons |

Every `.btn` floors at `--control-height-sm` (36) so unrelated buttons never
render at jittery 32/33/35 px. Larger tiers are opt-in **per group**.

**Consistency rule:** every button in the same visual group uses the **same
height tier**. A row of related buttons must share one tier (don't mix 36 and 56).
Set the tier on the container (e.g. `.prompt-actions-row > .btn`), not per-button.

### Spacing

`--spacing-xs|sm|md|lg|xl|2xl` (0.25 → 3rem) plus semantic
`--panel-padding`, `--panel-gap`, `--section-gap`, `--layout-gap`.

### Shadow & blur

- `--shadow-sm|md|lg` (elevation), `--shadow-glow` (primary halo).
- `--blur-sm|md|lg` for backdrop blur (modals use `--blur-sm`).

### Motion

| Token | ms | Use |
|---|---|---|
| `--transition-fast` | 150 | Buttons, hovers, inputs |
| `--transition-normal` | 250 | Modals, panels, larger reveals |
| `--transition-slow` | 400 | Section entrances |

Animate only `opacity`, `transform`, `background-color`, `border-color`,
`box-shadow`, `color`. Never animate `all` on layout-affecting properties.

---

## 2. Buttons

Base class `.btn` provides layout, `--radius-md`, `--transition-fast`, a tactile
`:active` press, a `:focus-visible` ring, and `:disabled` (opacity 0.6). **Always
combine `.btn` with exactly one variant.**

| Class | Looks like | Use for |
|---|---|---|
| `.btn .btn-primary` | Gradient fill, white text, glow on hover | The one main action of a view |
| `.btn .btn-secondary` | Surface fill, primary text, border | Default / neutral actions |
| `.btn .btn-ghost` | Transparent, border | Low-emphasis inline actions |
| `.btn .btn-danger` | Red-tinted | Destructive (delete, stop) |
| `.btn-icon-only` | 36×36 square, `--radius-sm` | Icon-only controls (no `.btn` needed) |
| `.btn-feedback` / `.btn-error-feedback` | Locked success/error state | Transient post-click confirmation |

Modifiers (stack on top of a variant):
- `.btn-sm` — compact (`--control-height-sm`).
- `.btn-fixed-width` — fixed 144px for equal-width groups.
- `.btn-icon` — sets the 18×18 leading icon inside any `.btn`.

Size tiers are applied by **container context**, per the consistency rule:
- Prompt action row → `--control-height-lg` (56).
- Developer-tools tiles → `--control-height-xl` (72).
- Everything else → default `--control-height` (44), or `.btn-sm` (36).

Never create a new `*-btn` class with bespoke padding/font/height. Reuse a
variant + the container tier.

---

## 3. Form controls

Inputs, selects, and textareas share: `--color-surface` fill, `--color-border`,
`--radius-sm`, and the focus ring `box-shadow: 0 0 0 3px var(--color-primary-glow)`
with `border-color: var(--color-primary)`.

- Single-line controls (`input`, `select`): height `--control-height` (44).
- `textarea` (`.prompt-input`): `min-height: 180px`, `resize: vertical`,
  `--radius-md` (the one intentional exception — large text areas read better
  with the button radius).
- Placeholder text: `--color-text-muted`.

---

## 4. Modals

One shell, built by [`components/modals/Modal.jsx`](src/components/modals/Modal.jsx):

```
.modal                → fixed overlay, centers content, fade in/out (250ms)
  .modal-backdrop     → rgba(0,0,0,.72) + blur(--blur-sm)
  .modal-content      → gradient surface, --radius-lg, --shadow-lg
                        enters with translateY(-16px) scale(.97) → 0/1
    .modal-header     → title (h3) + close, bottom border
    .modal-body       → scrolls, flex:1 (use .modal-body-flush for zero padding)
    .modal-footer     → right-aligned actions, top border, gap --spacing-sm
```

Width variants: default 500px · `.modal-large` 800px · `.modal-full` ~1500px.
Footer actions are `.btn` (typically one `.btn-primary` + `.btn-secondary`).
Every modal must go through `Modal.jsx` — do not hand-roll an overlay.

---

## 5. Cards & panels

- `.card` / panel → `--gradient-card`, `--color-border`, `--radius-lg`,
  `--shadow-sm`; header is `.card-header` (title `h2` + optional `.subtitle`).
- Inner sections use `--panel-padding` and `--panel-gap`.

## 6. Status & feedback

- Log lines and badges derive their accent from the status color via a
  `--log-accent` (info/success/warning/error).
- Status dots/badges use the matching `--color-*` + `--color-*-glow`.
- Use the semantic color, never a raw hex, so theming stays centralized.

---

## 7. Checklist before shipping UI

1. Reuse a documented `.btn` variant — no new `*-btn` classes.
2. Buttons in one group share a height tier (set on the container).
3. Inputs/selects use `--control-height` + `--radius-sm` + the standard focus ring.
4. Modals go through `Modal.jsx`.
5. Colors/spacing/radius/motion come from tokens, not literals.
6. Transitions use the motion tokens and only animate safe properties.
