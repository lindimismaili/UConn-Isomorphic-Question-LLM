# 07 — Output Ratings (good / bad, saved into workflow data)

> Let users (and the professor) rate an output **good or bad**, and persist that rating
> into the workflow's data so it feeds the existing grader/prompt-tuning loop.

---

## 1. Today

There is **no rating** anywhere — no field on the workflow record
([state.py:810-827](../site/backend/src/api/state.py#L810-L827)), no slot in
`WorkflowResult`/`schemas.py`, no endpoint (grep confirms). The frontend `ChatMessage`
already supports an `actions` row that's currently unused
([Chat.jsx:66,106](../site/frontend/src/components/ui/Chat.jsx#L66)) — the natural home.

---

## 2. Data model

Two granularities, both worth capturing:

- **Run-level** rating: one thumbs up/down for the whole generation.
- **Per-question** rating: up/down on each generated question — the highest-value signal
  for this repo's grader/meta-evaluator work (`grader-review` skill,
  `tools/meta_evaluator.py`), since it localizes *which* question failed.

### Storage
- Add `rating` to the workflow record and mirror it to the `run_ledger.rating` column
  ([06](06-workflow-history-and-users.md) §4) so the admin history table can sort/filter
  by rating and the meta-evaluator can mine it.
- Persist per-question ratings into the run's `final_result.json` (or a sibling
  `ratings.json` in the output folder) so they travel with the artifacts the
  meta-evaluator already reads. Shape:

```jsonc
{
  "run_rating": { "value": 1, "by": "user_id", "at": "2026-...Z", "note": "" },
  "question_ratings": {
    "<question id/source_id>": { "value": -1, "by": "user_id", "at": "..." }
  }
}
```

`value`: `+1` good, `-1` bad, absent = unrated. Store `by` (user_id from
[06](06-workflow-history-and-users.md)) and timestamp so multiple raters / professor
overrides are distinguishable. Last-write-wins per (target, user), keep the professor's
as authoritative if present.

---

## 3. API

- `POST /api/workflow/{id}/rating` — body `{ scope: "run"|"question", target_id?, value:
  1|-1|0 }` (`0` clears). Validates the workflow exists and is `completed`; writes the
  workflow record + ledger + the output-folder ratings file (through `to_thread` — no
  blocking I/O on the loop, per repo convention).
- Rating is a **user** action, so it needs the identity from
  [06](06-workflow-history-and-users.md) (`user_id`), but should be allowed to
  non-admins (students rate their own runs). The **professor override** comes through the
  admin surface and is marked authoritative.
- `GET` ratings are included in the existing result/status payloads and the admin
  workflow detail.

---

## 4. Frontend (templates-first)

- New `ui/` primitive **`RatingControl`** (thumbs up/down) — per DESIGN.md §14, add it to
  `ui/`, the design-system showcase, and `ui/ui.test.jsx` before using it. Reuse the
  existing `Button feedbackState="success|error"` lock
  ([Button.jsx:49-50](../site/frontend/src/components/ui/Button.jsx#L49-L50)) for the
  confirmed state.
- **Run-level:** pass it via the result bubble's `actions` row
  ([ChatConversation.jsx:242-263](../site/frontend/src/sections/ChatConversation.jsx#L242-L263),
  currently not passing `actions`) or the `FinalResultModal` footer.
- **Per-question:** inside `QuestionCard`
  ([FinalResultModal.jsx:82-106](../site/frontend/src/components/modals/FinalResultModal.jsx#L82-L106)).
- Optimistic UI with rollback on error; POST through `utils/api.js`.
- Admin **Workflows** table shows the rating column and lets the professor override.

---

## 5. Why it's worth wiring to the grader loop

The repo already has a grader self-improvement pipeline
(`tools/meta_evaluator.py`, `grader-review` skill) that mines rationales + failure tags.
Human good/bad labels are a direct, high-quality signal for that loop and for the
judge ↔ professor agreement tooling (`tools/judge_agreement.py`). Storing ratings in the
output folder (§2) means the meta-evaluator can consume them with no new plumbing — a
small feature with outsized value for question quality over the semester.

---

## 6. Acceptance criteria
- A user can rate a completed run and individual questions; the rating persists to the
  workflow record, the ledger, and the output folder, and survives a reload.
- The admin history table shows and can filter by rating; the professor can override.
- Per-question ratings are readable by the meta-evaluator without extra glue.
- No blocking I/O on the event loop; ratings write via `to_thread`.

*Cross-refs: identity → [06](06-workflow-history-and-users.md); admin table → [03](03-admin-dashboard.md) §4.*
