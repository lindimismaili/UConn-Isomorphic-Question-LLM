# 02 — Frontend UI/UX Review

> **Status — dated audit; status warning added July 27, 2026.** Findings describe
> evidence at review time, not guaranteed current behavior. Reverify each item
> against the code and tests before acting; current handoff status lives in
> [`docs/PROFESSOR_HANDOFF.md`](../docs/PROFESSOR_HANDOFF.md).

Findings from a deep read of `site/frontend/src/` against `site/frontend/DESIGN.md`.
Grouped by severity with `file:line` anchors. **[blocks admin]** marks items that shape
how the admin dashboard must be built.

---

## High

### H1 — No error boundary anywhere; one render throw blanks the whole app
[main.jsx:10-14](../site/frontend/src/main.jsx#L10-L14) mounts `<App/>` with no error
boundary. The result/eval modals do heavy `JSON.parse` + deep field access on backend
payloads ([FinalResultModal.jsx:187-198](../site/frontend/src/components/modals/FinalResultModal.jsx#L187-L198),
`WorkflowAgentsModal.jsx:150-174`, `ResultsSection.jsx:159-173`). A malformed payload
that throws during render takes down the entire conversation with a blank page and no
recovery.
**Fix:** add a top-level `ErrorBoundary` plus ones wrapping each rich modal body and the
`ResultsSection` embed, rendering a `Card variant="failed"` fallback. **Prerequisite
before embedding a heavier admin dashboard.**

### H2 — "Stop" silently wipes the entire transcript, logs, inputs, and result
[ChatConversation.jsx:136-139](../site/frontend/src/sections/ChatConversation.jsx#L136-L139)
→ `onSend({stop:true})` → [App.jsx:148-162](../site/frontend/src/App.jsx#L148-L162)
`handleClearInputs` runs `abortWorkflow()` + `CLEAR_INPUTS` + `CLEAR_AGENTS` +
`CLEAR_LOGS`. `CLEAR_AGENTS` → `resetAgents` ([reducer.js:413-420](../site/frontend/src/state/reducer.js#L413-L420))
sets every node `inactive`, so the transcript's `STARTED` filter
([transcript.js:15,102-104](../site/frontend/src/state/transcript.js#L15)) drops every
agent bubble. Pressing the red **Stop** during a run **erases all progress, logs, and
partial output** with no confirmation — users expect a pause.
**Fix:** Stop should only `abortWorkflow()` + dispatch `WORKFLOW_ABORTED`, keeping the
transcript. Move "Clear" to its own affordance with a confirm.

### H3 — Mode gating is cosmetic, not access control **[blocks admin]**
The User/Teacher/Dev distinction is a client-side `localStorage.uiMode` string with zero
enforcement ([App.jsx:43-50](../site/frontend/src/App.jsx#L43-L50),
[App.jsx:223-245](../site/frontend/src/App.jsx#L223-L245),
[SiteChrome.jsx](../site/frontend/src/sections/SiteChrome.jsx)). Anyone
can set `localStorage.uiMode='dev'` (or a future `'admin'`) in devtools and get the
gated surface. Dev mode is low-stakes today, but **an admin/professor dashboard must be
gated server-side** (authenticated endpoint / role), never by `uiMode`. Treat the header
dropdown as a view switch, not authorization. → See
[04-auth-and-security.md](04-auth-and-security.md).

---

## Medium

- **M1 — Native `alert()` for validation/errors** ([ChatConversation.jsx:142,146](../site/frontend/src/sections/ChatConversation.jsx#L142),
  `useFileViewer.jsx:54`, `TestFilesModal.jsx:62`) — blocking, unstyled, off-design.
  DESIGN.md §10 defines inline states; surface these as composer validation text or a
  log/toast. The composer's `attachFeedback` error-lock (`Chat.jsx:272`) is a reusable
  pattern.
- **M2 — Composer textarea has no accessible name** ([Chat.jsx:239-247](../site/frontend/src/components/ui/Chat.jsx#L239-L247);
  `TextArea` at `Field.jsx:22-25` emits a bare `<textarea>` with only a placeholder).
  Screen-reader users get an unlabeled primary control. Add `aria-label="Message"`.
- **M3 — The whole result surface renders inside an `aria-live="polite"` region**
  ([Chat.jsx:143-150](../site/frontend/src/components/ui/Chat.jsx#L143-L150) sets
  `role="log" aria-live="polite"` on the full transcript; the big `ResultsSection` card
  is embedded as a message). The entire card is announced and re-announced on every DOM
  change. Scope `aria-live` to a small status sub-region; mark rich cards
  `aria-live="off"`.
- **M4 — Possible React key collision** ([FinalResultModal.jsx:68-69,83](../site/frontend/src/components/modals/FinalResultModal.jsx#L68-L69)):
  `key = q.id ?? q.source_id` is `undefined` when a question has neither. Add
  `?? index`.
- **M5 — `WorkflowAgentsModal` hardcodes agent keys** ([WorkflowAgentsModal.jsx:173-174](../site/frontend/src/components/modals/WorkflowAgentsModal.jsx#L173-L174):
  `countFor('extracter')`, `countFor('generator')`) — violates the derive-from-registry
  rule; renames in `agents.json` silently break counts. Resolve via the derived registry
  `countFields` map. (The literals in `design-system/*`/`manifest.jsx`/tests are fixtures,
  fine.)
- **M6 — Dead duplicate composer `PromptInput.jsx`** — never imported outside its own
  test; reimplements send/attach with its own `alert()`s and raw `.btn-label` markup. A
  maintenance trap. **Delete it and its test.**
- **M7 — `serviceInactive` disables the whole textarea, trapping a draft**
  ([ChatConversation.jsx:273](../site/frontend/src/sections/ChatConversation.jsx#L273)):
  if OpenAI goes down mid-sentence the textarea greys out and the draft becomes
  uneditable. Keep the textarea editable; only disable **send**, show the outage in the
  existing banner.
- **M8 — Auto-scroll can't tell "message appended" from "content grew"**
  ([Chat.jsx:124-139](../site/frontend/src/components/ui/Chat.jsx#L124-L139) only reacts
  to message *count*). The result card expands in place after `useFileContent` resolves,
  so the newest content can render below the fold. Add a `ResizeObserver`/nonce signal.

---

## Low

- **L1 — `developer-tool-btn` is an undocumented bespoke button class**
  ([DeveloperToolsPanel.jsx:41,53,65,75](../site/frontend/src/components/DeveloperToolsPanel.jsx#L41)) —
  not in DESIGN.md §4's allowed list. Document it or fold into the `.developer-tools-row`
  tier.
- **L2 — All ~10 modals are permanently mounted** ([Modal.jsx:285-319](../site/frontend/src/components/modals/Modal.jsx#L285-L319)
  always portals; `App.jsx:249-301` renders every modal). Closed modals still render
  children (map over files) and hold stale `aria-labelledby`. Gate heavy modal children
  on `isOpen` before adding more.
- **L3 — `handleZipDownload` clicks a detached anchor without append/cleanup**
  ([ResultsSection.jsx](../site/frontend/src/components/ResultsSection.jsx)) —
  ignored in some browsers. Reuse the `useFileViewer` download path.
- **L4 — `Stepper` both reads `rest['aria-label']` and spreads `{...rest}`**
  ([Stepper.jsx:107,130-136](../site/frontend/src/components/ui/Stepper.jsx#L107)) —
  harmless but fragile; destructure it out explicitly.
- **L5 — Footer flashes `"Version -"`** ([App.jsx:51,83-99](../site/frontend/src/App.jsx#L51))
  before the version fetch resolves; add a loading state. Cosmetic.

---

## What's solid (don't "fix")
Interval/subscription cleanup is correct everywhere (OpenAI poll, thinking-word interval,
dropdown listeners, registry subscribers, batch abort refs) — no leaks. The workflow
poller guards stale runs via `runIdRef`/`inFlightRef` ([useWorkflow.js:117,126,264-269](../site/frontend/src/hooks/useWorkflow.js#L117)).
Modal focus-trap/scroll-lock/focus-restore/stacking is robust (§8). The `Dropdown`
primitive is genuinely accessible (`aria-activedescendant`, typeahead, flip, portal).

---

## Enhancements that the admin work builds on

### E1 — "Rate this output good/bad" — where it slots in
The structural home exists but is unused: `ChatMessage` supports an `actions` row
([Chat.jsx:66,106](../site/frontend/src/components/ui/Chat.jsx#L66) → `.chat-message-actions`)
that `ChatConversation.jsx:242-263` never passes. Two levels:
- **Run-level:** thumbs up/down in the result bubble's `actions` row, or in
  `ResultsSection` `.results-view-buttons` / the `FinalResultModal` footer.
- **Per-question:** inside `QuestionCard` ([FinalResultModal.jsx:82-106](../site/frontend/src/components/modals/FinalResultModal.jsx#L82-L106)) —
  the most useful signal for the existing grader/prompt-tuning loop.

Per DESIGN.md §14 (templates-first), add a `RatingControl` `ui/` primitive (+ showcase
specimen + `ui.test.jsx` assertion) rather than raw buttons; reuse the
`Button feedbackState="success|error"` lock ([Button.jsx:49-50](../site/frontend/src/components/ui/Button.jsx#L49-L50))
for the confirmed state. Full design → [07-ratings.md](07-ratings.md).

### E2 — Admin dashboard route/view — where it slots in
There is **no router**; the app is a single `AppShell` ([App.jsx:199-247](../site/frontend/src/App.jsx#L199-L247))
and the design-system showcase is a *second Vite HTML entry* (`design-system.html`).
Recommended: **in-app mode** — add `{value:'admin',label:'Admin'}` to `SITE_MODES`
([SiteChrome.jsx](../site/frontend/src/sections/SiteChrome.jsx)), accept
it in the `mode` coercion, and branch in `App.jsx:209-222` to render `<AdminDashboard/>`
in place of `ChatConversation` (mirroring how dev mode swaps the aside). Compose from
`ui/` primitives (`Card`, `DataTable`, `Tabs`, `StatusChip`). **Gate on real auth (H3),
not `uiMode`.** Alternative: a separate `admin.html` Vite entry for an isolated bundle.
Full design → [03-admin-dashboard.md](03-admin-dashboard.md).

---

## Highest-leverage fixes
**H1 (error boundary)**, **H2 (Stop wipes transcript)**, **H3 (mode ≠ auth)** — H3 most
directly shapes the admin build. Do H1 and H3 before/with the dashboard.
