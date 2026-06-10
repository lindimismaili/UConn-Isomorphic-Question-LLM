---
name: verify-frontend-ui
description: Verify a frontend/CSS/layout change in this repo by driving the running app in a real browser. Use after editing frontend/src (app.css, components) — especially for responsive/breakpoint behavior, hover/focus states, scrollbars, or anything where you should measure the rendered DOM instead of trusting the code. Covers starting the Vite dev server with port fallback, the backend dependency, and measuring elements with the Playwright MCP.
---

# Verify frontend UI changes in the browser

This frontend (`frontend/`, React 19 + Vite 7) ships visual changes often, and
several bugs only show up at specific viewport widths or in computed styles. Don't
ship a layout/CSS change on reasoning alone — drive the real app and **measure**.

Read [frontend/DESIGN.md](../../frontend/DESIGN.md) first: it owns the tokens, the
`.btn`/modal/card systems, the §3 responsive breakpoints, and the documented
exceptions (e.g. only `.agent-card` lifts on hover; the global
`::-webkit-scrollbar` is the single source of truth — never set `scrollbar-color`
per element).

## 1. Start the app

```bash
node_modules/.bin/vite --host 127.0.0.1 --port 5180   # from frontend/; auto-bumps if taken
```

- Port `5173` is frequently held by another local project — Vite picks the next
  free port automatically (don't use `--strictPort` unless you want it to fail).
- The dev server proxies `/api`, `/version.json`, `/data` to the **backend on
  `:8000`**. The agent pipeline, theme dropdown, and dev-tools need it; start the
  backend (`.venv/bin/python -m uvicorn backend.src.server:app --port 8000`) when
  testing those. The top panels, prompt card, and dev-tools layout render without
  the backend, which is enough for most CSS/layout work.

## 2. Drive + measure with the Playwright MCP

- `browser_navigate` to the dev URL, then `browser_resize` to walk the §3
  breakpoints: `1300` (2-col), `1040` (stacked), `900`, `520`, `380` (mobile).
- **Measure, don't eyeball.** Use `browser_evaluate` with `getBoundingClientRect()`
  and `getComputedStyle()` to read heights, `overflow`, `flex`, and same-row checks
  (compare element `.top` values). This is how the dev-tools log-clipping
  (`card-body` collapsing to ~40px under `overflow: hidden`) and the prompt-row
  wrap bugs were pinned down.
- Screenshots go in a workspace `tmp/`; read them back, then delete when done.

## 3. Hover/focus styles without a live run

To check a `:hover`/`:focus` style when no real element exists (e.g. agent cards
need a running workflow), inject a throwaway element via `browser_evaluate`, then
`browser_hover` it and read the computed `transform` / `border-color`:

```js
// inject a sample .agent-card, then hover #__samplecard and assert:
//   getComputedStyle(el).transform     === translateY(-4px) matrix
//   getComputedStyle(el).borderColor   === rgba(255,255,255,0.85)
```

## 4. Testing modal viewers without a live workflow

The Results section shows after a completed workflow. To test modal viewer CSS
and layout without running a full workflow, inject a minimal results fixture via
`browser_evaluate` against the app state (or open the app with the Results section
pre-populated from a saved `final_result.json`).

Key modal size classes to assert (use `getBoundingClientRect()` on `.modal-content`
after opening each modal):
- `.final-result-modal.modal-full` — ~1200px wide, ~900px tall
- `.csv-viewer-modal.modal-full` — ~1600px wide
- `.criterion-reasoning-modal.modal-full` — ~680px wide
- `.tex-viewer-modal.modal-full` — ~1100px wide
- `.agent-detail-modal` — `min(94vw, 1240px)` wide, 260px details sidebar

For stacked modals (a viewer opened from inside another modal), assert the layer
order and backdrop styling on each `.modal.is-visible`: `style.zIndex` is
`1000 + stack depth`, and every backdrop except the bottom one carries
`.is-stacked` (transparent, blur-only).

For the criterion chip drill-down: verify `button.eval-score-chip.is-clickable`
has `cursor: pointer` and a focus ring via `getComputedStyle(...).cursor` and
`:focus-visible` outline.

## 5. Before finishing

- `npm -C frontend test` and `npm -C frontend run lint` (some pre-existing
  `react-hooks` warnings in `BatchEvalModal.jsx`, `InputToAgentModal.jsx`, and
  `useBatchEval.js` are not yours — diff to confirm; lint must report 0 errors).
- Remove `tmp/` screenshots and stop the dev server.
- If the change alters a documented rule or adds an exception, update
  [frontend/DESIGN.md](../../frontend/DESIGN.md) — it must not go stale.
