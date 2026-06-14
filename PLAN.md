# Plan: Theme injection, exam-level theme + per-question tags, LaTeX template extraction, OE/multi-part fixes

## Context

Four related improvements to the isomorphic question pipeline, all driven by user request:

1. **Theme propagation is lossy.** Selecting a theme today only appends the theme's *prompt* text into the textarea; the *label* is dropped and nothing is stored structurally. The normalizer re-parses a snake_case label from prose, and the generator's theme rule is pinned to a stale 4-value enum (`life_science, engineering, everyday_activity, physics_abstract`). The user wants the selected theme's **label + prompt** injected into the user prompt, stored verbatim as `theme: "Label: Prompt"`, and re-applied at generation time.
2. **Per-question identity should be tags, not a per-question theme.** The exam gets **one overall theme**; each question gets a **`tags`** list (1–3 word snake_case, ≤5) describing its specific context, with on-the-fly reuse so similar questions share tags instead of spawning near-duplicates. (Per Q&A: remove the per-question `theme` field; rename `topic_tags` → `tags`.)
3. **LaTeX preamble should live in one file and be a heavy guideline, not a hard rule.** `latex_example.txt` is dead → delete. `latex_template.tex` is also dead today; instead of deleting it, make it the **single source** of the LaTeX preamble/macros: strip the inline LaTeX out of `latexifier.txt`, leave a `{latex_template}` placeholder, and inject the file's contents at runtime. Frame it as a strong default that may be adapted for non-traditional questions.
4. **Subtitle / color legend lies for non-MC exams.** The header legend always says "Correct in RED, incorrect in BLUE" even for open-ended/multi-part. Per Q&A (Split answer + explanation): give OE & multi-part a separate **answer (red) + explanation (blue)**, and make the legend type-aware.

No `agents.json` change is required (themes already live in `_config.themes`), so no registry restart caveat applies.

---

## 1. Theme: inject label+prompt, store `"Label: Prompt"`, re-apply in generator

**Frontend — `frontend/src/components/PromptInput.jsx`**
- `handleDropdownChange`: inject `"{label}: {prompt}"` into the textarea (currently injects only `prompt`). Update the previous-line cleanup so switching/clearing themes removes the old `"Label: Prompt"` block (adapt `removePromptLine`, lines 44–52, 145–157).
- On send, derive the selected theme string `"{label}: {prompt}"` from the selected dropdown item and pass it up through `onSend`.

**Frontend — `frontend/src/App.jsx` + `frontend/src/hooks/useWorkflow.js`**
- Thread the theme string from `handleSend` (App.jsx:491) into `run(...)` (useWorkflow.js:243) and append `startForm.append('theme', theme)` next to the existing fields (useWorkflow.js:288).

**Backend API — `backend/src/api/workflows.py`**
- Add `theme: str = Form("")` to `start_workflow` (line 75) and forward it into `run_workflow_async` (line 140).

**Backend runner — `backend/src/api/state.py`**
- Add `theme` param to `run_workflow_async` (line 1099) and pass through `_engine_run_workflow_async` (lines ~1195–1199).

**Backend engine — `backend/src/workflow/engine.py`**
- Thread `theme` to the engine's `run_workflow_async` and into `build_normalizer_input(prompt_text, file_names, theme)` (line 611).

**Payload — `backend/src/workflow/payloads.py`**
- `build_normalizer_input(prompt, file_names, theme="")` (line 10): set `defaults.theme = theme` and update the `rules.theme` text to: *"If a theme is provided in defaults, it is a user-selected `Label: guidance` theme — copy it verbatim into the output theme. Otherwise leave empty unless the user requests one."* CLI keeps default `""` (theme can still arrive via prompt text).

**Normalizer prompt — `backend/data/prompts/normalizer.txt`** (lines 21–24)
- Replace the snake_case-derivation rule with: copy `defaults.theme` verbatim when non-empty; otherwise derive a short label from a free-text request; else blank.

**Generator prompt — `backend/data/prompts/generator.txt`**
- Rule 2 "Theme" (lines 37–40): delete the 4-value enum. Replace with: *the overall exam theme arrives in `configuration.theme` as a `Label: guidance` string (or a short label). Re-cast every generated question's scenario/context to fit this theme's guidance while preserving the core concept, difficulty, answer structure, numeric reasoning, and distractor logic. If no theme is provided, preserve the source's real-world context.*

`config.theme` (now possibly `"Label: Prompt"`) already survives `merge_extracted_metadata_into_config` because that helper only fills theme when blank.

---

## 2. Exam-level theme + per-question `tags` (remove per-question `theme`, rename `topic_tags`)

**Schemas — `backend/src/schemas.py`**
- `SourceQuestionItem` (line 41–42) and `NewQuestionItem` (line 67–68): **remove** `theme`; **rename** `topic_tags: list[str]` → `tags: list[str] = []`.
- Keep exam/document-level `theme` on `NormalizerOutput` (line 83) and `ExtracterOutput` (line 95) — this is the "overall theme".

**Helpers — `backend/src/helpers.py`**
- Delete `dominant_question_theme` (109), `_is_valid_theme` (19), `_THEME_RE` (16) — all keyed on the now-removed per-question theme.
- Simplify `merge_extracted_metadata_into_config` (133–157): theme fallback becomes `if not config.theme and extraction.theme: config.theme = extraction.theme.strip()` (drop the snake_case validation + dominant-theme call).

**Extracter prompt — `backend/data/prompts/extracter.txt`**
- "Theme labeling" (104–109): replace with **tag labeling** — each question gets a `tags` list (1–3 word snake_case, ≤5, reuse earlier tags for consistency). Set the *document* theme directly from overall subject matter; update line 39 ("dominant theme across questions") accordingly.

**Verifier prompt — `backend/data/prompts/verifier.txt`**
- "Theme requirements" (75–81): remove the enum enforcement. Replace with: preserve each question's `tags` unchanged.

**Generator prompt — `backend/data/prompts/generator.txt`**
- Line 25 output checklist: drop per-question `theme`; require `tags`.
- Add a **Tags** section: produce 1–3 word snake_case tags (≤5) for topics/actions/physical-context/goals; maintain a running internal list while generating and **reuse** an existing tag when a later question shares context (e.g. don't add `sprinting` if `running` was already used).

**Downstream dict builders**
- `backend/src/eval_pipeline.py` (69–70): drop `"theme": question.theme`; rename `"topic_tags"` → `"tags": list(question.tags)`. (Line 83 keeps `source_extraction.theme` — document-level.)
- `backend/src/workflow/resume.py` (203–204): drop the `theme` setdefault; rename `topic_tags` → `tags`.

**Tests** — rename `topic_tags=` → `tags=` in the 8 test files (test_workflow_e2e, test_workflow_payloads, test_agent_isolation, test_distractor_logic_preservation, test_engine_workflow, test_pipeline_validation, test_style_testing) and remove any per-question `theme=` on question fixtures (keep `theme=` on `NormalizerOutput`/`ExtracterOutput`).

---

## 3. Extract LaTeX preamble into `latex_template.tex`; inject at runtime

**Delete** `backend/data/prompts/latex_example.txt` (zero references).

**Rewrite** `backend/data/latex_template.tex` to hold **only** the authoritative preamble + macros (no `\begin{document}`, no example questions). Base it on the current inline block in `latexifier.txt` (lines 20–161, which has the correct 4-arg `\QBlock`, `\Correct`/`\Wrong`, `\MPBlock`, `\MCPart`, `\SkipBlock`, colors incl. `SkipGray`), with the OE/multi-part updates from §4.

**Latexifier prompt — `backend/data/prompts/latexifier.txt`**
- Replace the entire inline LaTeX block (lines 20–161) with a single `{latex_template}` placeholder.
- Reframe the heading (line 18) from "Use this EXACT preamble" to a **strong default / heavy guideline**: apply it for standard MC/open-ended/multi-part; if a question genuinely doesn't fit, adapt or extend the macros while preserving the same visual style, colors, answer-toggle behavior, and spacing.

**Agent build — `backend/src/workflow/agents.py`**
- Special-case `latexifier` in `create_agents` (line 67): instead of `load_prompt`, use `render_prompt(PROMPTS_DIR, logger, "latexifier", latex_template=<contents of LATEX_TEMPLATE_PATH>)`. `render_prompt` (`helpers.py:67`) already does brace-safe plain-string substitution, so LaTeX braces in the template are safe. Import `render_prompt` and `LATEX_TEMPLATE_PATH` (already in `paths.py:25`); read the file defensively.

---

## 4. OE / multi-part: answer in red + explanation in blue; type-aware legend

**Schemas — `backend/src/schemas.py`**
- Add `explanation: str = ""` to `NewQuestionItem` (OE solution reasoning), `NewQuestionPart` (OE sub-part reasoning), and `LaTeXQuestionInput`.

**Payload — `backend/src/workflow/payloads.py`**
- `build_latex_questions` (207–222): add `"explanation": q.explanation`. (Part explanations flow automatically via `part.model_dump()`.)
- `build_latex_input` document/theme rule unchanged; latexifier handles the `Label:` split (below).

**Generator prompt — `backend/data/prompts/generator.txt`**
- Open-ended rules (74–81) and multi-part OE parts (83–92): set `answer` = concise final result, `explanation` = supporting reasoning/derivation (LaTeX equations). MC keeps per-option explanations.

**`latex_template.tex` macros** (the rewritten file)
- `\OEBlock` → 4 args `{Q}{stem}{answer}{explanation}`: answer in `AnsRed`, explanation in `WrongBlue` gated by `\ifshowreason`.
- `\OEPart` → 4 args `{label}{stem}{answer}{explanation}`: same coloring.
- MC macros unchanged (correct red / wrong blue, with per-option explanations).

**Latexifier prompt — `backend/data/prompts/latexifier.txt`**
- Update macro-usage rules (220–236) to pass `explanation` to `\OEBlock`/`\OEPart`.
- **Type-aware legend** (lines 176–177, 204–208): choose the third header line by question types present — all-MC → "Correct answers in RED, incorrect in BLUE"; any OE/multi-part → "Answers in RED, explanations in BLUE"; mixed → combine. Omit entirely for student/answers-hidden versions.
- **Title from `Label:`**: the document theme may arrive as `Label: guidance`; use only the text before the first colon for `<TITLE>`/`<SUBTITLE>`, never the guidance text. Keep the subtitle about *source context*, not question format (stop emitting "Multiple choice").

---

## Critical files (summary)

- Schemas/helpers: `backend/src/schemas.py`, `backend/src/helpers.py`
- Payload/engine/runner: `backend/src/workflow/payloads.py`, `backend/src/workflow/engine.py`, `backend/src/workflow/agents.py`, `backend/src/api/state.py`, `backend/src/api/workflows.py`, `backend/src/eval_pipeline.py`, `backend/src/workflow/resume.py`
- Prompts/data: `normalizer.txt`, `extracter.txt`, `verifier.txt`, `generator.txt`, `latexifier.txt`, `latex_template.tex`; **delete** `latex_example.txt`
- Frontend: `PromptInput.jsx`, `App.jsx`, `useWorkflow.js`
- Tests: the 8 `topic_tags` files above

## Verification

1. **Backend**: `.venv/bin/python -m pytest` + `.venv/bin/python -m ruff check .` (use the `run-tests` skill). Update/extend tests: normalizer payload copies a provided theme verbatim; latexifier instructions contain injected template (no leftover `{latex_template}`); `build_latex_questions` carries `explanation`.
2. **Frontend**: `npm -C frontend test` + `npm -C frontend run lint`; build with `npm -C frontend run build`.
3. **Prompt snapshot**: after editing `backend/data/prompts/*.txt`, run `python scripts/prompt_versions.py snapshot -m "theme injection + tags + latex template extraction + OE/MP colors"` (per repo Git workflow).
4. **UI smoke** (Vite dev + backend on :8000): pick a theme → confirm `"Label: Prompt"` is injected into the textarea; confirm the start request sends a `theme` field.
5. **End-to-end (optional, needs `OPENAI_API_KEY`)**: CLI run, then inspect `final_result.json` for `config.theme == "Label: Prompt"`, per-question `tags` (reused across questions), no per-question `theme`; open the PDF to confirm the title uses only the label, the legend matches question types, and OE/multi-part show answer (red) + explanation (blue).
