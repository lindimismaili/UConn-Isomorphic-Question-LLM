# Workflow Outline

## Goal
This project turns raw physics question sources (PDFs, images, or text) into a clean, structured question schema and then produces high‑quality isomorphic multiple‑choice variants. The pipeline is built to preserve mathematical fidelity, standardize metadata, verify answers, and generate consistent MCQ outputs based on user‑specified configuration.

## Core Objects
Two primary objects flow through the system:

1) User Request (configuration)
- newQuestionAmountPerInputQuestion
- newDifficultyLevel
- newTheme
- newTitle
- moreInstructions

2) Question Schema (content)
- filename
- title
- author
- instructions
- date
- class
- questions[]
  - sourceID
  - explicitAnswer
  - questionType (open_ended | multiple_choice)
  - topics[]
  - difficulty
  - stem
  - options[]
    - key
    - text
    - isCorrect
    - explanation

## Workflow Steps

1) Normalize User Request
- The Normalizer reads user input and produces the User Request object.
- Defaults are applied when values are not provided.

2) Extract Questions
- The Extracter reads source content and outputs a Question Schema object.
- Metadata is captured when available; missing fields are left empty.
- Mathematical fidelity is preserved.

3) Verify Answers
- The Verifier checks each multiple‑choice question.
- If an explicit correct answer exists, it is preserved.
- If no answer is marked, the Verifier infers the correct option and adds explanations.
- Non‑MCQ items remain open‑ended and are not converted here.

4) Generate Isomorphic Questions
- The Generation agent creates new MCQs for each input question using the User Request object.
- Each new question is fully original but concept‑equivalent.
- Difficulty and theme are overridden only when provided.
- Explanations are included for every option.

## How It Works End‑to‑End
- Input text (and any provided files) enter the workflow.
- The User Request object is built first to guide downstream behavior.
- The Question Schema object is extracted, then verified.
- The Generator produces new, schema‑compliant MCQs aligned to the request.

This structure keeps configuration and content separate, making the system easier to extend, audit, and reuse across different sources or output formats.
