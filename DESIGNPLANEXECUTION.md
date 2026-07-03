# Design-System Upgrade — manifest-driven dashboard + enforced templates

## Context

The user wants this site to have a real design system with rules, a template
system where UI is built from templates (so editing a template updates every
instance), and a clean dashboard showing all template items — modeled on the
`smartcar2026-slides` reference repo.

**The repo already has a strong version of this**, so this is an *upgrade*, not a
build-from-scratch:

- **Tokens + rules**: `site/frontend/src/styles/app.css` `:root` (one edit
  re-themes the app via `rgba(var(--color-*-rgb), α)`) + the 15-section written
  contract `site/frontend/DESIGN.md`.
- **Template system**: ~34 thin-wrapper primitives in
  `site/frontend/src/components/ui/` (barrel `index.js`). Feature code composes
  from them, so editing a primitive/token already propagates everywhere (React +
  shared CSS classes — no external builder needed).
- **Dashboard**: a live showcase at `design-system.html` (2nd Vite entry) that
  renders the real primitives, organized Foundations → Components → Patterns.

**The gap vs. smartcar** (what this plan closes):
1. The showcase is **hand-maintained**, not manifest-driven — a new primitive can
   be silently missing (adding one = 4 synchronized manual edits).
2. It's a scrolling doc, not a clean **searchable gallery**.
3. The **rules are only partly enforced** — ESLint covers ~5 of the primitive
   class families (buttons/cards/toggles/inputs/selects), string literals only.

**Chosen scope: Full parity.** Central manifest → gallery dashboard → CI coverage
test → extend lint enforcement to all primitives (refactor ~11 feature files) →
live modal specimen → in-app dev link → docs. Outcome: the dashboard is
**complete-by-construction** (a missing/orphan primitive fails CI), reads as a
clean gallery, and "templates-first" is enforced mechanically — staying
self-contained (Vite/React + CSS tokens; **do not** reproduce smartcar's external
Omelette/dc-runtime toolchain).

All paths below are under `site/frontend/`; all commands run from there.

---

## Approach

### 1. Central manifest — single source of truth (new: `src/design-system/manifest.jsx`)

One module exports an array with **exactly one entry per `ui/index.js` export**
(34 today). Each entry carries the catalog metadata that drives the dashboard,
the docs, and the coverage test. Specimens are React components (so they can hold
local state — the Modal opener, Toggle, Tabs) with hand-chosen demo props.

```js
// src/design-system/manifest.jsx
import { Button, Chip, Modal, /* …all 34 exports… */ } from '../components/ui/index.js';
import { IconSend } from '../components/Icons.jsx';
import { useState } from 'react';

// showcase-only fixtures (kept OUT of ui/ so no demo code enters the app bundle)
const STEPPER_NODES = [ /* the 10-node fixture currently inline in Components.jsx */ ];

/** @type {{name,group,summary,props:{name,type,description?}[],usage?:string,Specimen:React.FC}[]} */
export const MANIFEST = [
  {
    name: 'Button', group: 'Buttons',
    summary: 'Single button primitive: .btn + one variant; height tier comes from the container.',
    props: [
      { name: 'variant', type: "'primary'|'secondary'|'ghost'|'danger'|null" },
      { name: 'size', type: "'sm'" },
      { name: 'icon', type: 'ReactNode' },
      { name: 'feedbackState', type: "'success'|'error'" },
    ],
    usage: `<Button variant="primary" icon={<IconSend className="btn-icon" />}>Send</Button>`,
    Specimen: () => (<>
      <Button variant="primary" icon={<IconSend className="btn-icon" />}>Primary</Button>
      <Button variant="secondary">Secondary</Button>
      <Button variant="ghost">Ghost</Button>
      <Button variant="danger">Danger</Button>
    </>),
  },
  {
    name: 'Modal', group: 'Overlays',
    summary: 'The one dialog shell (portal, focus trap, scroll-lock, stacking).',
    props: [ { name: 'isOpen', type: 'boolean', required: true },
             { name: 'onClose', type: '() => void', required: true },
             { name: 'title', type: 'ReactNode' }, { name: 'footer', type: 'ReactNode' } ],
    usage: `<Modal isOpen={open} onClose={close} title="Title" footer={…}>…</Modal>`,
    Specimen: () => { const [open, setOpen] = useState(false); return (<>
      <Button variant="secondary" onClick={() => setOpen(true)}>Open modal</Button>
      <Modal isOpen={open} onClose={() => setOpen(false)} title="Modal title"
        footer={<Button variant="primary" onClick={() => setOpen(false)}>Confirm</Button>}>
        <p className="subtitle">Body scrolls; footer actions are .btn.</p>
      </Modal></>); },
  },
  // … one entry per export. Sub-parts (CardHeader/CardBody/SectionHeading/LogItem)
  //   each get a compact in-context Specimen so coverage stays strict 1:1.
];
```

- **Groups** mirror DESIGN.md sections: `Buttons, Form controls, Toggles, Cards,
  Status & feedback, Tabs, Tables, Shell, Chat, Process rail, Overlays`.
- **`props`** is the machine-readable prop contract (folds in smartcar's `.d.ts`
  role); **`usage`** is the copyable example (folds in smartcar's `.prompt.md`
  role); **`Specimen`** is the live example. One file, no sidecars to drift.
- **Assembled at import time** (static ES module) — *not* a generate script. The
  module IS the source of truth; the coverage test enforces completeness. No stale
  build artifact, works instantly in Vite dev, directly unit-testable.
- **Keep it fetch-free** — Stepper/AgentCard/chat specimens use hardcoded
  fixtures. `AgentCard` reads only its `agent` prop (verified), so no
  `useAgentRegistry`/`fetch` is pulled in and the test needs no fetch stub.

### 2. Rebuild the dashboard as a gallery — rewrite `src/design-system/sections/Components.jsx`

Iterate `MANIFEST` instead of hand-authoring specimens:
- Group entries by `entry.group` in a fixed `GROUP_ORDER`; render one `Card` per
  group containing a `Grid` of **specimen tiles**.
- Each tile: the live `<Specimen/>` (real primitive), the `name`, the `summary`,
  a prop table via the real `KeyValueTable` (`rows = props.map(p => ({ key:
  <code>{p.name}</code>, value: p.type }))`), and a copyable `usage` snippet in a
  `<pre><code>` block.
- Add a **search box** (real `TextInput`, local state) filtering by
  `name`/`summary`/`group` — worthwhile at ~34 tiles.
- **Foundations** (tokens) and **Patterns** (compositions) keep their structure.
- Add a small `SpecimenTile` helper to `src/design-system/Specimen.jsx` (tile
  chrome via token-referencing inline styles — the showcase's existing "no new
  CSS" convention). JSX needs a capitalized tag: `const S = entry.Specimen;
  <S/>`. `main.jsx` (imports real `app.css`) and the `designSystem` Vite entry
  in `vite.config.js` are unchanged.

### 3. Coverage test — makes the dashboard complete-by-construction (new: `src/design-system/manifest.test.jsx`)

```js
import { describe, it, expect } from 'vitest';
import * as UI from '../components/ui/index.js';
import { MANIFEST } from './manifest.jsx';
import fs from 'node:fs';

const EXPORTS = Object.keys(UI).sort();
const NAMES = MANIFEST.map(e => e.name).sort();

describe('design-system manifest coverage', () => {
  it('has exactly one entry per ui/ export (no missing, no orphan)', () =>
    expect(NAMES).toEqual(EXPORTS));                 // fails CI on drift both ways
  it('every entry has group, summary, props[], Specimen', () => {
    for (const e of MANIFEST) {
      expect(e.group && e.summary).toBeTruthy();
      expect(Array.isArray(e.props)).toBe(true);
      expect(typeof e.Specimen).toBe('function');
    }
  });
  it('DESIGN.md §14 lists every primitive', () => {
    const md = fs.readFileSync(new URL('../../DESIGN.md', import.meta.url), 'utf8');
    const s14 = md.slice(md.indexOf('## 14.'), md.indexOf('## 15.'));
    for (const n of NAMES) expect(s14).toContain('`' + n + '`');
  });
});
```

Reads DESIGN.md as **text** (not `getComputedStyle`), so jsdom quirks don't bite.
Keep it a **separate file** so `ui/ui.test.jsx`'s exact-className assertions stay
untouched.

### 4. Enforce templates-first for all primitives — `eslint.config.js` (+ fix 11 sites)

First **fix the raw-class sites**, then broaden the ban (so lint stays green).
Full inventory (grepped across `src/**/*.jsx` — the 3 known + 8 more):

| # | File:line | Raw class | Replacement |
|---|---|---|---|
| 1 | `modals/CreateDataModal.jsx:149` | `create-data-step-chip` | `<Badge kind="step-chip">` |
| 2 | `modals/BatchEvalModal.jsx:76` | `create-data-step-chip` | `<Badge kind="step-chip">` |
| 3 | `modals/eval/EvalItemsTable.jsx:46` | `eval-score-chip …` (button, template literal) | `<ScoreChip>` **interactive** (see below) |
| 4 | `modals/eval/EvalItemsTable.jsx:14` | `eval-table` | `<DataTable variant="eval">` |
| 5 | `modals/eval/EvalAggregateTable.jsx:13` | `eval-table eval-aggregate-table` | `<DataTable variant="eval" className="eval-aggregate-table">` |
| 6 | `modals/FinalResultModal.jsx:130` | `file-viewer-table eval-summary-table` | `<DataTable variant="file-viewer" className="eval-summary-table">` |
| 7 | `modals/FinalResultModal.jsx:23` | `viewer-kv-table` | `<KeyValueTable rows={…}>` |
| 8 | `modals/SetupModal.jsx:55` | `viewer-kv-table` | `<KeyValueTable rows={parameters}>` (already `{key,value}`-shaped) |
| 9 | `modals/WorkflowAgentsModal.jsx:256` | `viewer-kv-table` | `<KeyValueTable rows={…}>` |
| 10 | `modals/RubricViewerModal.jsx:123` | `rubric-index` | `<Badge kind="rubric-index">` |
| 11 | `modals/RubricViewerModal.jsx:127` | `rubric-category` | `<Badge kind="rubric-category">` |

- **Leave alone** (whole-token safe, not primitive bases): `*-shell`
  (`eval-table-shell`, `file-viewer-table-shell`), `viewer-tab-content`,
  `viewer-pill*`, `status-indicator` in `AgentCard.jsx` (ignored), `chat-shell/
  chat-rail/chat-empty` layout wrappers.
- **`ScoreChip` gains an opt-in interactive variant** (in `ui/Badge.jsx`) so #3
  routes through the primitive: default stays `<span class="eval-score-chip
  is-{tone}">`; when `onClick`/`interactive` is passed, render `<button
  type="button" class="eval-score-chip is-{tone} is-clickable">`. **Add a new**
  `ui.test.jsx` assertion for the interactive form; leave the existing span
  assertion intact.
- **Extend the ban** (keep `files: ['src/components/**']` + the same three
  ignores; `src/sections/**` and `src/design-system/**` stay out of scope so
  specimens/chrome may pass demo classes freely). Add the remaining primitive
  bases to the regex and add a template-literal selector:

```js
const BANNED = "(^|\\s)(btn|btn-primary|btn-secondary|btn-ghost|btn-danger|btn-icon-only|card|" +
  "developer-toggle|prompt-input|prompt-dropdown-select|eval-status-chip|eval-score-chip|" +
  "create-data-pill|create-data-step-chip|eval-source-id|eval-reference-kind|rubric-index|" +
  "rubric-category|chip|status-indicator|log-item|log-list|log-empty-state|modal-empty-state|" +
  "viewer-tab|viewer-tab-bar|eval-table|file-viewer-table|viewer-kv-table|chat-avatar|" +
  "chat-message|chat-transcript|chat-typing|composer|stepper|token-stream)(\\s|$)";
// (a) static + conditional/&&/?: — descendant combinator instead of the current '>':
{ selector: `JSXAttribute[name.name='className'] Literal[value=/${BANNED}/]`, message: '…compose from src/components/ui/ (DESIGN.md §14)…' },
// (b) template-literal classNames (catches `eval-score-chip ${…}`):
{ selector: `JSXAttribute[name.name='className'] TemplateElement[value.cooked=/${BANNED}/]`, message: '…' },
```

Most of these (chat/stepper/token-stream/log/tabs/chip/status-dot) are **ratchet
bans with zero current violations** — feature code already composes from the
primitives; the ban just prevents regressions. **Do not** reproduce oxlint —
extend the existing flat config only.

### 5. Live Modal specimen — `src/design-system/sections/Patterns.jsx`

Replace the hand-copied static `.modal-content` block (~lines 203–227) with a
real `Modal` opened on click (same opener as the `Modal` manifest specimen —
reuse, don't hand-copy). Closes the one specimen that could drift from
`Modal.jsx`.

### 6. In-app discoverability — `src/sections/SiteChrome.jsx`

Add a **dev-only** footer link in `SiteFooter` (this file is `src/sections/**`,
outside the ESLint ban scope):

```jsx
{import.meta.env.DEV ? (
  <p className="footer-secondary"><a href="/design-system.html">Design system</a></p>
) : null}
```

`import.meta.env.DEV` keeps it out of the production bundle (showcase stays
`noindex`); product UI stays clean. The App smoke test asserts element presence
only, so this won't break it.

### 7. Docs — `DESIGN.md` §14

- Rewrite **"Adding a new primitive"** to 3 steps: (1) add the wrapper + barrel
  export in `ui/`; (2) add one `MANIFEST` entry in
  `src/design-system/manifest.jsx`; (3) add the class-contract assertion in
  `ui/ui.test.jsx`. The specimen **auto-appears** and the coverage test
  guarantees the §14 row + manifest entry exist — a miss **fails CI**.
- Note the manifest is the source of truth (the §14 table is checked against it)
  and mention the dev footer link.
- Add one line: this is **frontend-only** — unrelated to the backend
  `agents.json` registry / prompt-versioning conventions.

---

## Files to create / modify

**Create**
- `src/design-system/manifest.jsx` — the 34-entry catalog (single source of truth)
- `src/design-system/manifest.test.jsx` — coverage test

**Modify**
- `src/design-system/sections/Components.jsx` — iterate the manifest (gallery + search + prop tables + usage)
- `src/design-system/Specimen.jsx` — add `SpecimenTile` helper
- `src/design-system/sections/Patterns.jsx` — live Modal specimen
- `src/components/ui/Badge.jsx` — `ScoreChip` interactive variant
- `src/components/ui/ui.test.jsx` — new assertion for interactive `ScoreChip`
- `eslint.config.js` — broaden ban + template-literal selector
- The 11 feature files in the table (§4) — route raw classes through primitives
- `src/sections/SiteChrome.jsx` — dev-only footer link
- `DESIGN.md` — §14 rewrite

---

## Ordered execution (keeps lint + tests green at every step)

1. **`ScoreChip` interactive variant** (`ui/Badge.jsx`) + new `ui.test.jsx`
   assertion (leave the span assertion). → `npm test`
2. **Fix all 11 violation sites** (§4 table) — *before* touching ESLint. The
   `KeyValueTable`/`DataTable` conversions (#5–#9) swap `<span>` cells for the
   primitive's `<div class="viewer-kv-*">` — visually identical (class-driven CSS).
   → `npm test` + `verify-frontend-ui` skill for the untested
   FinalResult/Setup/WorkflowAgents/BatchEval modals.
3. **Broaden ESLint** (`eslint.config.js`). → `npm run lint` (clean, since step 2
   cleared violations).
4. **Add `manifest.jsx`** (34 entries incl. Modal).
5. **Add `manifest.test.jsx`**. → `npm test` (drop one entry to confirm it fails,
   then complete).
6. **Rewrite `Components.jsx`** to iterate the manifest; add `SpecimenTile`; add
   search. → `npm run dev` → open `/design-system.html`; `npm run build`.
7. **Live Modal specimen** in `Patterns.jsx`.
8. **Dev footer link** in `SiteChrome.jsx`. → dev shows link; `npm run build`.
9. **Rewrite `DESIGN.md` §14**. → manifest test's §14 check passes.

Finish with the **`run-tests` skill** (vitest + eslint + backend unaffected) and a
final `/design-system.html` visual pass via **`verify-frontend-ui`**.

---

## Verification

- **Coverage / drift**: `npm test` — `manifest.test.jsx` asserts 1:1 with
  `ui/index.js` exports and §14 doc coverage. Prove it works: temporarily remove
  an entry → test fails; restore → passes.
- **Enforcement**: `npm run lint` clean; add a stray `<div className="chip">` in a
  modal → lint errors (proves the ratchet).
- **Dashboard**: `npm run dev` → `/design-system.html` renders every primitive as
  a searchable gallery tile with live specimen + prop table + usage; `npm run
  build` succeeds (both Vite entries).
- **In-app link**: dev footer shows "Design system" → `/design-system.html`; not
  present in a production build.
- **Templates propagate**: edit a token in `app.css :root` (e.g.
  `--color-primary`) → app *and* every dashboard specimen re-theme; edit a
  primitive → all consumers + its specimen update (the existing guarantee, now
  demonstrable in the gallery).

## Risks & mitigations

- **Module-level `useAgentRegistry` cache + vitest fetch-stub** (`AgentPipeline.test.jsx`,
  `App.smoke.test.jsx`): keep `manifest.jsx`/`Components.jsx`/`Patterns.jsx`
  **fetch-free** (hardcoded Stepper/AgentCard fixtures) so `manifest.test.jsx`
  needs no stub. Don't import anything that calls `loadAgentRegistry` into the
  manifest.
- **`ui.test.jsx` exact-className assertions**: `ScoreChip`'s interactive branch
  is opt-in; all other emitted classes unchanged. Coverage work adds a *new* test
  file and touches no primitive output.
- **Structural table conversions (#5–#9)** on untested modals: gate on
  `verify-frontend-ui`. If risk-averse, phase the ban — chips/badges/pills first,
  tables/kv in a second ratchet after visual verification.
- **ESLint scope**: `src/sections/**` and `src/design-system/**` stay outside
  `src/components/**`, so the footer link and specimens may use raw classes; all
  11 fixes are under `src/components/modals/**` (in scope) and must be clean
  before step 3.
