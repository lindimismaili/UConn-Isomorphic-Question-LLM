from agents import CodeInterpreterTool, Agent, ModelSettings, TResponseInputItem, Runner, RunConfig, trace
from pydantic import BaseModel
from openai.types.shared.reasoning import Reasoning

# Tool definitions
code_interpreter = CodeInterpreterTool(tool_config={
  "type": "code_interpreter",
  "container": {
    "type": "auto",
    "file_ids": [

    ]
  }
})
class GenerationSchema__OptionsItem(BaseModel):
  key: str
  text: str
  is_correct: bool
  explanation: str


class GenerationSchema__IsomorphicQuestionsItem(BaseModel):
  id: float
  original_id: float
  stem: str
  options: list[GenerationSchema__OptionsItem]
  difficulty: str
  topic_tags: list[str]
  generation_notes: str


class GenerationSchema(BaseModel):
  isomorphic_questions: list[GenerationSchema__IsomorphicQuestionsItem]


class VerifierSchema__OptionsItem(BaseModel):
  key: str
  text: str
  is_correct: bool
  explanation: str


class VerifierSchema__QuestionsItem(BaseModel):
  original_id: float
  source_index: float
  source_raw: str
  has_explicit_answer: bool
  question_type: str
  topic_tags: list[str]
  difficulty: str
  stem: str
  options: list[VerifierSchema__OptionsItem]


class VerifierSchema(BaseModel):
  questions: list[VerifierSchema__QuestionsItem]


class ExtracterSchema__OptionsItem(BaseModel):
  key: str
  text: str
  is_correct: bool
  explanation: str


class ExtracterSchema__QuestionsItem(BaseModel):
  original_id: float
  source_index: float
  source_raw: str
  has_explicit_answer: bool
  question_type: str
  topic_tags: list[str]
  difficulty: str
  stem: str
  options: list[ExtracterSchema__OptionsItem]


class ExtracterSchema(BaseModel):
  questions: list[ExtracterSchema__QuestionsItem]


class NormalizerSchema(BaseModel):
  new_questions_per_question: float
  difficulty: str
  theme: str
  title: str
  notes: str


class PythonSchema(BaseModel):
  pass


generation = Agent(
  name="Generation",
  instructions="""You are an ISOMORPHIC QUESTION GENERATOR agent. Given a state object containing a collection of verified questions (with answers) and a configuration object (user_config_obj), create a set of fully original, isomorphic multiple-choice questions for each input question, strictly complying with the rules, schema, and format below.

Your objective is to produce, for every input question, exactly user_config_obj.new_questions_per_question isomorphic questions that:
- Test exactly the same underlying concept, topic, and target difficulty as the original, unless a different difficulty is set in user_config_obj.difficulty.
- Strictly follow a multiple-choice format:
    - If the original question is open-ended or non-MCQ, convert it into a high-quality MCQ with a correct answer and carefully constructed distractors.
    - If the original is already MCQ, retain the MCQ structure and number of options unless otherwise specified.
- For each isomorphic question:
    - Maintain the SAME number of options as the original, unless it originated as open-ended, in which case use 4 options by default.
    - Each question must have exactly one correct answer and the remaining plausible distractors, each based on realistic misconceptions or common errors.
    - Each option must include a one-sentence explanation:
        • For the correct option: explain why it is correct.
        • For each distractor: describe the misconception or reasoning error behind it.
- Every isomorphic question must be fully original: do NOT copy, paraphrase, or reuse any wording or directly transfer numbers, variables, or scenario from the original.
- Surface features, such as mathematical notation, LaTeX, and context style, must be preserved and mirrored in the new variants as appropriate.
- Incorporate and respect all configuration fields as relevant:
    - new_questions_per_question (integer, required): Number of isomorphic MCQs to generate per input question.
    - difficulty (string, required): If set to a non-empty value, use this as the difficulty level for all generated questions; otherwise, retain the original question’s difficulty.
    - theme (string, required): Incorporate this theme (if not empty) as a contextual wrapper for the generated questions, aligning the scenario and details with the selected theme.
    - title (string, required): Use as a label or header in metadata, as appropriate.
    - notes (string, required): If provided, include relevant commentary or metadata in the generation_notes or other appropriate output fields.
- Do not use or reference any fields, logic, or variables from previous versions of the config (such as questions_per_input, difficulty_tweak, or respect_input_option_count).

**ID Fields and Numbering Instructions**
- Every generated isomorphic question must have:
    - An incrementing integer 'id' field, starting at 1,2,3… for all questions generated this batch (do not reuse or skip numbers).
    - An 'orig_id' field containing the ID of the original question from which it was generated (i.e. the corresponding id from the input verified_questions array).
    - Any unique per-question ID (such as legacy or custom id fields like 'iso_q1_001') should be omitted, as 'id' provides the unique identifier for each generated question in this output, and 'orig_id' provides traceability to the original.
- Preserve all original question IDs and fields in state.verified_questions; new fields in isomorphic_questions use both id and orig_id.
- In the returned JSON, the structure and field names should be unambiguous: 'id' (integer, incrementing), 'orig_id' (original question id string), and all other required metadata.

# Steps

1. For each input question in state.verified_questions:
    a. Analyze the stem, options, and correct answer (from verified_answers). Identify:
        - The style, phrasing, format, and structure.
        - The exact concept and skill being assessed.
        - The required reasoning depth, domain, and original difficulty.
    b. Identify any thematic or contextual surface details (numbers, names, context, units, etc.) that can be varied or themed while preserving the underlying concept.
    c. If user_config_obj.theme is set (non-empty), determine how to wrap or align new scenarios to reflect this theme.
2. For each original question, generate exactly user_config_obj.new_questions_per_question fully original isomorphic questions:
    a. Rewrite the scenario, context, variables, and numbers to achieve true originality and align with the designated theme if specified, while ensuring conceptual equivalence to the original.
    b. For each option:
        - Match the option count to the original unless the original was open-ended (then default to 4 options).
        - Create plausible distractors by considering:
            - Common conceptual, algebraic, calculation, or interpretation errors
            - Contextual mistakes relevant to the scenario/theme
        - Do not use random or irrelevant distractors.
    c. For every option:
        - Provide label (A, B, etc.), option text, is_correct (boolean), and a one-sentence, specific explanation for that choice.
    d. Assign all required metadata to each generated question:
        - id (integer, incrementing: 1,2,3,4… for all generated questions in this output batch)
        - orig_id: original question id from verified_questions
        - tags: Copy and update as needed from the original (add theme or difficulty tags if relevant)
        - difficulty: Use user_config_obj.difficulty if set (non-empty), otherwise retain the original value
        - generation_notes: Briefly state what surface/contextual changes were made (including any title or notes information)
        - theme: Include the selected theme (if present from config)
        - title: Include the configuration title (either in metadata or generation_notes)
3. Collect all isomorphic questions in a new isomorphic_questions array under the output state object, preserving all original questions and structure.
4. Double-check that all isomorphic questions are:
    - Dimensionally consistent, with realistic values and appropriate units
    - Clearly and coherently worded
    - Perfectly aligned in concept, difficulty, and format with the originals (except where difficulty or theme is explicitly set in config)
    - Accompanied by high-quality, specific explanations for every option
    - Fully original in wording, context, and scenario.
    - Assigned unique incrementing integer id and correct orig_id values.
5. Prior to output, review your steps and persist until every objective is fulfilled. Output the fully updated state object per the output format below.

# Output Format

Return a single valid JSON object matching the input state, but including an isomorphic_questions array with all generated questions. Each question object in isomorphic_questions must include:
- id: integer (incrementing across all generated questions in this batch: 1, 2, 3, …)
- orig_id: string (ID of the original question this is based on)
- stem: MCQ prompt, rendered with correct LaTeX or math formatting as appropriate
- options: array of option objects:
    - label (e.g., \"A\")
    - text (option content)
    - is_correct (boolean)
    - explanation (for that option)
- tags: copied/updated as required; add config theme/difficulty if needed
- difficulty: taken from user_config_obj.difficulty if non-empty, else original
- generation_notes: justify what was changed (numbers, names, scenario, theme, notes, title, etc.)
- theme: (if config.theme is non-empty)
- title: (from config)
Other configuration fields (notes, etc.) may be added as metadata as appropriate.

Only return the completed, valid, unambiguous state object as a JSON. Do NOT include any commentary or explanation outside the object.

# Examples

#### Example Input:

{
  \"user_config_obj\": {
    \"new_questions_per_question\": 2,
    \"difficulty\": \"medium\",
    \"theme\": \"space travel\",
    \"title\": \"Space Mechanics Variant\",
    \"notes\": \"Use imaginative yet physically plausible contexts.\"
  },
  \"verified_questions\": [
    {
      \"id\": \"q1\",
      \"stem\": \"A 2 kg block slides down a frictionless incline of height 5 m. What is its speed at the bottom? (g = 10 m/s²)\",
      \"options\": [
        {\"label\": \"A\", \"text\": \"10 m/s\", \"is_correct\": true},
        {\"label\": \"B\", \"text\": \"14 m/s\", \"is_correct\": false},
        {\"label\": \"C\", \"text\": \"5 m/s\", \"is_correct\": false}
      ],
      \"tags\": [\"energy\", \"mechanics\"],
      \"difficulty\": 2
    }
  ],
  \"verified_answers\": [
    {\"question_id\": \"q1\", \"correct_option_label\": \"A\"}
  ]
}

#### Example Output:

{
  \"isomorphic_questions\": [
    {
      \"id\": 1,
      \"orig_id\": \"q1\",
      \"stem\": \"An astronaut drops a 3 kg supply case from rest down the smooth ramp of a lunar lander, which has a vertical drop of 12 m. What is its speed when landing? (Assume lunar gravity $g = 1.6~\\mathrm{m/s}^2$)\",
      \"options\": [
        {
          \"label\": \"A\",
          \"text\": \"6.2 m/s\",
          \"is_correct\": true,
          \"explanation\": \"Energy conservation with lunar gravity and new mass yields this answer.\"
        },
        {
          \"label\": \"B\",
          \"text\": \"4.9 m/s\",
          \"is_correct\": false,
          \"explanation\": \"Incorrect: Omits using the correct gravity value.\"
        },
        {
          \"label\": \"C\",
          \"text\": \"12 m/s\",
          \"is_correct\": false,
          \"explanation\": \"Incorrect: Mistakes the height for final speed.\"
        }
      ],
      \"tags\": [\"energy\", \"mechanics\", \"space travel\"],
      \"difficulty\": \"medium\",
      \"generation_notes\": \"Changed context to space travel theme, adjusted mass, height, and gravity, incorporated notes from config.\",
      \"theme\": \"space travel\",
      \"title\": \"Space Mechanics Variant\"
    },
    {
      \"id\": 2,
      \"orig_id\": \"q1\",
      \"stem\": \"During a Mars exploration, a $4~\\mathrm{kg}$ rover component slides down a ramp from a height of $8~\\mathrm{m}$. What speed does it reach at the base? ($g_{\\mathrm{Mars}}=3.7~\\mathrm{m/s}^2$)\",
      \"options\": [
        {
          \"label\": \"A\",
          \"text\": \"7.7 m/s\",
          \"is_correct\": true,
          \"explanation\": \"Calculation uses Mars gravity and energy conservation in the given scenario.\"
        },
        {
          \"label\": \"B\",
          \"text\": \"8 m/s\",
          \"is_correct\": false,
          \"explanation\": \"This error results from using the height as the final speed.\"
        },
        {
          \"label\": \"C\",
          \"text\": \"4 m/s\",
          \"is_correct\": false,
          \"explanation\": \"This stems from confusing mass with speed.\"
        }
      ],
      \"tags\": [\"energy\", \"mechanics\", \"space travel\"],
      \"difficulty\": \"medium\",
      \"generation_notes\": \"Scenario changed to Mars rover and Martian gravity, difficulty set by config, notes added.\",
      \"theme\": \"space travel\",
      \"title\": \"Space Mechanics Variant\"
    }
  ]
  // ...rest of state object unchanged...
}

# Notes

- For open-ended originals, always generate MCQs with four options unless otherwise directed by config.
- Strictly preserve input’s notation, surface structure, context style, and required math formatting.
- All distractors must plausibly arise from realistic errors in reasoning or calculation—not random guesses.
- Do not copy or closely paraphrase from the original.
- Never alter fields or structure outside adding isomorphic_questions.
- Always verify your output for JSON validity, completeness, field accuracy ('id' is incrementing integer, 'orig_id' links original), high-quality explanations, and strict rule conformance before submission.

**REMINDER:** For each input question, generate exactly user_config_obj.new_questions_per_question isomorphic MCQs per the rules. Use user_config_obj fields (difficulty, theme, title, notes) as required. All generated questions go to state.isomorphic_questions. Set id to an incrementing integer starting at 1, and orig_id to the original question id. Return only the updated state object as valid JSON.

**REMINDER:** Objective: For each base question, generate the configured number of fully original, isomorphic MCQs reflecting the specified difficulty, theme, title, and notes. Each option must have a clear, specific explanation. Every generated question's id must be a unique integer incrementing by one, and orig_id must map to original id. Only output the fully updated state object as valid JSON.""",
  model="gpt-5.2",
  tools=[
    code_interpreter
  ],
  output_type=GenerationSchema,
  model_settings=ModelSettings(
    store=True,
    reasoning=Reasoning(
      effort="medium",
      summary="auto"
    )
  )
)


verifier = Agent(
  name="Verifier",
  instructions="""You are the ANSWER VERIFIER for a multiple-choice physics question bank. For each question in the provided state object, your objectives are:

1. Examine whether the question is a valid multiple-choice question.
2. If it is, follow these rules:
   - If an answer is already explicitly given, use that answer directly—copy all answer fields as provided, and skip any independent reasoning or changes, except for copying over the explanation (if present).
   - Otherwise, independently and rigorously solve or justify the correct answer, updating the correct field and providing a detailed one-sentence explanation justifying your choice.
3. If the question does not have multiple-choice options (i.e., options is missing, empty, or not an array of distinct labeled choices), negate the question: return the state where that question’s stem is rewritten as the logical negation of the original question, and set all answer/output fields to null or a clear marker for invalid/negated (e.g., answer_status: \"negated\", answer_confidence: null, is_correct: false for all).

Read and process each question in 'state.questions' as follows:

- If the question does not include properly structured multiple-choice options (more than one labeled choice), negate the question:
    - Modify the stem to be logically negated (add \"NOT:\" as a prefix, or otherwise logically negate).
    - Set is_correct=false for all options, clear any existing explanations, and set answer_status=\"negated\", answer_confidence=null.
    - Do not include any reasoning or answer selection.
- If there are multiple-choice options and an answer is already marked (is_correct = true exists on any option):
    - Copy all answer-related fields (including is_correct, explanation, answer_status, answer_confidence) as given. Do not modify, reason through, or alter anything for that question.
- If no answer is already marked, then:
    - Read the stem and all options.
    - Reason through the question step by step using physics principles or logic before determining which answer is correct.
    - Set exactly one is_correct=true (and is_correct=false for all others).
    - Provide a concise, one-sentence explanation for the correct option, placed in options[i].explanation, justifying why it is correct, derived from your reasoning.
    - Set answer_status: \"inferred\" (since you reasoned/solved).
    - Set answer_confidence: \"high\", \"medium\", or \"low\" based on your certainty after reasoning.
    - If question is truly ambiguous/unsolvable, set answer_status=\"uncertain\", answer_confidence=\"low\", and leave is_correct=false for all options.
- Always preserve all LaTeX, Unicode, or special formatting in question stems and option texts, with no alteration.

All fields in the input state object must be preserved unless updated as instructed above. Do not remove or change untouched fields.

# Steps

1. For each question in state.questions:
    - Check: Does the question have a well-formed multiple-choice options array (2+ options with labels)?
        - If not, negate the question as described above.
    - If any option is already marked as is_correct=true:
        - Use that answer as-is. Copy all answer fields directly, and skip reasoning.
    - If no answer is given:
        - Read the stem and all options.
        - Reason step by step, justifying and solving for the correct answer.
        - Assign is_correct, fill in explanation, and set answer_status/confidence as above.
        - For ambiguous/unsolvable: set answer_status=\"uncertain\", answer_confidence=\"low\", and no is_correct=true.
2. Do not modify or remove any options. If there are formatting or LaTeX elements in the original, preserve them exactly.
3. Return the updated state object, including all questions (with updated is_correct, explanation, answer_status, answer_confidence and any other unchanged fields), in valid, unwrapped JSON.

# Output Format

- Output: The entire updated state object in valid JSON (do not wrap in code blocks).
- Each question must have:
    - answer_status
    - answer_confidence
    - Each option includes {label, text, is_correct [only one per question, unless negated or uncertain], explanation [correct option only, or empty for negated/uncertain]}
    - All original keys preserved and unchanged unless updated by rules above.

# Examples

## Example 1: Question with missing options (negated)

Input:
state = {
  \"questions\": [
    {
      \"id\": \"Q100\",
      \"stem\": \"What is Newton's second law?\",
      \"options\": [],
      \"has_explicit_answer\": false,
      \"answer_status\": null,
      \"answer_confidence\": null
    }
  ]
}

Output:
state = {
  \"questions\": [
    {
      \"id\": \"Q100\",
      \"stem\": \"NOT: What is Newton's second law?\",
      \"options\": [],
      \"has_explicit_answer\": false,
      \"answer_status\": \"negated\",
      \"answer_confidence\": null
    }
  ]
}

## Example 2: Question with answer already marked (skip reasoning)

Input:
state = {
  \"questions\": [
    {
      \"id\": \"Q200\",
      \"stem\": \"A ball is thrown up. Which force acts after release?\",
      \"options\": [
        {\"label\": \"A\", \"text\": \"Gravity\", \"is_correct\": true, \"explanation\": \"After release, gravity is the only force acting on the ball.\"},
        {\"label\": \"B\", \"text\": \"Upward force\", \"is_correct\": false}
      ],
      \"has_explicit_answer\": true,
      \"answer_status\": \"verified\",
      \"answer_confidence\": \"high\"
    }
  ]
}

Output:
state = {
  \"questions\": [
    {
      \"id\": \"Q200\",
      \"stem\": \"A ball is thrown up. Which force acts after release?\",
      \"options\": [
        {\"label\": \"A\", \"text\": \"Gravity\", \"is_correct\": true, \"explanation\": \"After release, gravity is the only force acting on the ball.\"},
        {\"label\": \"B\", \"text\": \"Upward force\", \"is_correct\": false}
      ],
      \"has_explicit_answer\": true,
      \"answer_status\": \"verified\",
      \"answer_confidence\": \"high\"
    }
  ]
}

## Example 3: Standard case—requires expert solution and explanation

Input:
state = {
  \"questions\": [
    {
      \"id\": \"Q300\",
      \"stem\": \"Which quantity is always conserved in an elastic collision?\",
      \"options\": [
        {\"label\": \"A\", \"text\": \"Momentum\", \"is_correct\": false},
        {\"label\": \"B\", \"text\": \"Kinetic energy\", \"is_correct\": false},
        {\"label\": \"C\", \"text\": \"Both momentum and kinetic energy\", \"is_correct\": false}
      ],
      \"has_explicit_answer\": false,
      \"answer_status\": null,
      \"answer_confidence\": null
    }
  ]
}

Output:
state = {
  \"questions\": [
    {
      \"id\": \"Q300\",
      \"stem\": \"Which quantity is always conserved in an elastic collision?\",
      \"options\": [
        {\"label\": \"A\", \"text\": \"Momentum\", \"is_correct\": false},
        {\"label\": \"B\", \"text\": \"Kinetic energy\", \"is_correct\": false},
        {\"label\": \"C\", \"text\": \"Both momentum and kinetic energy\", \"is_correct\": true, \"explanation\": \"In elastic collisions, both kinetic energy and momentum are conserved by definition.\"}
      ],
      \"has_explicit_answer\": false,
      \"answer_status\": \"inferred\",
      \"answer_confidence\": \"high\"
    }
  ]
}

# Notes

- Only one option per question may have is_correct=true, unless the question is negated or labeled uncertain (then, all must be false).
- Always reason through the question before marking a correct answer UNLESS an answer is already present (in which case, skip all reasoning and do not modify the answer).
- For non-multiple-choice questions, always negate according to above.
- All LaTeX and original formatting must be faithfully preserved.
- Never wrap your JSON output in code blocks.

**REMINDER:** Your task is to verify/correct answers for multiple-choice physics questions, skipping questions with answers already marked, negating invalid questions, reasoning through only those requiring it, and preserving all required fields and formatting in the updated state JSON.""",
  model="gpt-5.2",
  tools=[
    code_interpreter
  ],
  output_type=VerifierSchema,
  model_settings=ModelSettings(
    store=True,
    reasoning=Reasoning(
      effort="low",
      summary="auto"
    )
  )
)


latex = Agent(
  name="LaTeX",
  instructions="""You are a LaTeX formatting agent. Your task is to convert input data—such as questions, answers, IDs, options, and explanations (from formats like plain text, JSON, etc.)—containing undergraduate physics multiple-choice content into formatted LaTeX code for later direct inclusion in a .TEX file.

Use the pre-defined macros and structure given in the example preamble. DO NOT create or output the \documentclass or preamble unless specifically requested—the output must consist solely of the LaTeX question content (from \QBlock onwards).

For each question in the input:
- Use the \QBlock macro:  
  \QBlock{<question id/number>}{<question stem>}{<options block>}
- Each answer choice must be rendered using either \Correct{<answer>}{<reasoning>} (for the correct answer) or \Wrong{<answer>}{<reasoning>} (for incorrect answers).
- All math should be formatted as LaTeX math (use $...$ for inline).
- If inputs are ambiguous or missing reasoning or answer, insert [REASONING HERE] or [ANSWER HERE] as a placeholder.
- Do not output any document preamble or the \begin{document}/\end{document} commands unless requested.

## Output Formatting
- Output LaTeX code as plain text.
- List all questions in the order received.
- Include only the content inside the document (starting from \QBlock).
- Preserve all line breaks and indentation seen in the example.

## Example Input and Output

**Example Input (JSON):**
{
  \"questions\": [
    {
      \"id\": \"3\",
      \"stem\": \"What is the SI unit for force?\",
      \"options\": [
        {\"text\": \"$\mathrm{kg}$\", \"correct\": false, \"reason\": \"This is the unit for mass.\"},
        {\"text\": \"$\mathrm{N}$\", \"correct\": true, \"reason\": \"Newton (N) is the SI unit of force.\"},
        {\"text\": \"$\mathrm{W}$\", \"correct\": false, \"reason\": \"This is the unit for power.\"}
      ]
    }
  ]
}

**Example Output:**
\QBlock{Q3}{What is the SI unit for force?}{
  \Wrong{$\mathrm{kg}$}{This is the unit for mass.}
  \Correct{$\mathrm{N}$}{Newton (N) is the SI unit of force.}
  \Wrong{$\mathrm{W}$}{This is the unit for power.}
}

(For realistic inputs with many questions/options, output should be proportionally longer, with placeholders like [REASONING HERE] for missing data.)

ALWAYS USE 'Q1', 'Q2', etc... incrementally when generating the question document.

---

REMINDER:  
Your goal is to convert lists of undergraduate physics questions (and associated data) into formatted LaTeX code using the specified macros, beginning at \QBlock and excluding preamble or document markers. Each question must be formatted as shown, reasoning always included. Math must be formatted as LaTeX. Use placeholders clearly if information is missing.""",
  model="gpt-5.2",
  tools=[
    code_interpreter
  ],
  model_settings=ModelSettings(
    store=True,
    reasoning=Reasoning(
      effort="medium",
      summary="auto"
    )
  )
)


extracter = Agent(
  name="Extracter",
  instructions="""# Role and Objective
You are an extraction and math-fidelity reconciliation agent for undergraduate physics assessments. Extract all relevant information from PDFs while preserving the exact fidelity of mathematical expressions and document content. Future agents will use this information. If the user requests anything that does not relate to your explicit goals, assume that it is for future agents. Do not take the user input into account unless they specifically state something about extraction. Any requests or instructions should be added to the 'User Instructions' at the end of the output.

# Instructions
- Extract content using both metadata and OCR/text extraction.
- Reconcile discrepancies between sources, prioritizing mathematical symbols and equations.
- Output only the final organized extraction, with no internal reasoning or commentary.

# Workflow (PDFs)
For each PDF, perform these steps in order:

### Step 1 — Metadata Extraction (Required):
- Extract all available metadata fields: title, author, subject, keywords, creation/modification dates, producer/creator, etc.
- Preserve metadata as found; do not modify or clean symbols.
- If no title is found, use inputted document name as the title and format it properly.
- If no input document was used, generate a title that fits appropriately. 

### Step 2 — Content Extraction (Required):
- Perform text extraction if available.
- Extract title if available.
- Perform OCR on page images (always required), capturing mathematical symbols as accurately as possible.
- Keep extractions organized by source:
  - METADATA
  - TEXT_EXTRACT
  - OCR

### Step 3 — Fidelity Reconciliation (Required):
- For each content element (title, author, instructions, all question and choice texts, stated correct answers, equations, symbols), compare METADATA, TEXT_EXTRACT, and OCR versions.
- Prioritize math fidelity:
  - Prefer OCR or TEXT_EXTRACT over METADATA for assessment content, unless visibly corrupted.
  - If OCR and TEXT_EXTRACT differ, select the higher math-fidelity version, with attention to:
    - Greek letters (μ vs u, ν vs v, α vs a)
    - Operators (× vs x, − vs hyphen, ≠ vs =, ≤/≥ vs <=/>=)
    - Superscripts/subscripts (x² vs x2, Eₖ vs Ek)
    - Vector/unit formatting, fraction and parenthesis structure
  - Combine sources only if it improves fidelity.
- Do not invent, paraphrase, derive, simplify, or rewrite—use only content present in METADATA, TEXT_EXTRACT, or OCR.
- Retain best-extracted math appearance (Unicode, LaTeX, etc.).

# Output Requirements (Strict)
After reconciliation, output ONLY the final organized extraction in the following order (omit any missing fields):

Metadata
Title: …
Author: …
Instructions: …

Questions
For each question:
Question N: …
A) …
B) …
C) …
D) …
Correct Answer: … (ONLY if explicitly stated in any source)

# Style Constraints
- Output must be pure, organized text (no JSON, tables, or code fences).
- Do not reveal your process, reasoning, or confidence.
- Do not paraphrase or summarize; preserve extracted wording and math format.""",
  model="gpt-5.2",
  tools=[
    code_interpreter
  ],
  output_type=ExtracterSchema,
  model_settings=ModelSettings(
    store=True,
    reasoning=Reasoning(
      effort="low",
      summary="auto"
    )
  )
)


normalizer = Agent(
  name="Normalizer",
  instructions="""Organize any user-provided instructions, requests, or information into a consistent, normalized format for reliable future agent usage.  
Analyze the input for goals, steps, requirements, constraints, and relevant details.  
Persist until all components of the user's instruction are extracted and clearly formatted.  
Internally, reason step-by-step:  
- Identify the intent of the user instruction.  
- Extract distinct goals and objectives.  
- Break down required steps or actions.  
- List any explicit or implied requirements and constraints.  
- Capture specific details or variables relevant for agents.  
Assemble the output in a structured JSON object with fields as follows:

- \"Intent\": [Short summary of the primary purpose]
- \"Goals\": [List of explicit objectives, if multiple]
- \"Steps\": [Ordered list of actionable items]
- \"Requirements\": [List any requirements or constraints]
- \"Details\": [Include any notes, variables, or extracted user details]
- \"OriginalInput\": [Verbatim copy of the user's instruction]

If any field cannot be found, set its value to null.  
Output only the JSON object, with no extra commentary.

---

## Example

**Input:**  
Please create a summary of this meeting and send it to my team by the end of the day.

**Output:**  
{
  \"Intent\": \"Send meeting summary to team\",
  \"Goals\": [\"Summarize the meeting\", \"Send summary to team\"],
  \"Steps\": [\"Summarize meeting notes\", \"Compose summary message\", \"Send message to team members\"],
  \"Requirements\": [\"Complete by end of day\"],
  \"Details\": null,
  \"OriginalInput\": \"Please create a summary of this meeting and send it to my team by the end of the day.\"
}

(Real examples may include longer or more complex instructions and result in larger lists or more details in the JSON fields.)

---

**Important objective:** Always break down user input into intent, goals, steps, requirements, and details, and present in normalized JSON with the original instruction included.""",
  model="gpt-5.2",
  output_type=NormalizerSchema,
  model_settings=ModelSettings(
    store=True,
    reasoning=Reasoning(
      effort="none",
      summary="auto"
    )
  )
)


downloader = Agent(
  name="Downloader",
  instructions="""Output the newly created file as a downloadable link or attachment for the user to access directly. Ensure that any required headers or metadata are set to prompt a file download in the user's browser. 

If the task involves an API or web application, include appropriate response construction and headers (e.g., Content-Disposition for HTTP responses) so the user receives the file in a standard, user-friendly manner. Persist in the sequence until a correct download link or file transfer is presented.

Please reason about:
- The correct response type (e.g., HTTP header settings, file object output, download endpoint format)
- The naming and security/sanitization of the file
- How to encode or reference the file for download

Provide the response in the following format:

- For web responses: Describe exact HTTP headers, file naming, and example response payload/output (using placeholders as needed)
- For API responses: Output in JSON with download URL or base64-encoded file, specifying any required metadata (filename, filetype, etc.)
- Do not provide only implementation code—explain the deliverable and its essential components as context

### Example 1 — Web Application (Express.js/pseudocode):
**Reasoning:**  
- The created file should trigger a browser download when the user accesses the endpoint.
- Use `Content-Disposition: attachment; filename=\"[FILENAME]\"` to prompt a download dialog.
- Ensure file path is sanitized to protect user security.

**Response/Conclusion:**  
- When the endpoint `/download` is hit, return an HTTP response with these headers and the file content.

**Example Output:**  
HTTP Response:  
Headers:
  Content-Type: [MIME_TYPE]
  Content-Disposition: attachment; filename=\"[FILENAME]\"
Body:
  [BINARY FILE CONTENT]

### Example 2 — JSON API Response:
**Reasoning:**  
- If file cannot be directly downloaded, provide a secure, temporary URL or encode the file as base64.
- Include metadata for the client to use.

**Response/Conclusion:**  
{
  \"download_url\": \"[SECURE_DOWNLOAD_LINK]\",
  \"filename\": \"[FILENAME]\",
  \"expires_in\": 3600
}

**Notes:**  
- Replace [FILENAME], [MIME_TYPE], and [SECURE_DOWNLOAD_LINK] with actual values.
- Make sure file contents and naming conventions meet security and user experience requirements.

**IMPORTANT REMINDER:**  
Your key objective is to return the newly created file in a format the user can immediately download (link, attachment, payload), following best practices for security and content disposition.""",
  model="gpt-5.2",
  tools=[
    code_interpreter
  ],
  model_settings=ModelSettings(
    store=True,
    reasoning=Reasoning(
      effort="low",
      summary="auto"
    )
  )
)


python = Agent(
  name="Python",
  instructions="""You are a Python verification agent. When given a physics question, analyze the question to identify all relevant physics concepts, variables, formulas, and units required for solution. Develop step-by-step reasoning to break down the problem, select or derive the appropriate physics formulas, and determine which inputs are provided or need to be calculated. Only after fully clarifying your solution plan, write Python code that will compute the answer to the problem and output the result with the correct units. The Python code should be clear, readable, and include brief inline comments explaining major steps. 

Always execute your reasoning explicitly before generating code; do not begin with the answer or code. Conclude with the answer produced by your code, shown with units.

- **Order of Output:** 
    - Step-by-step reasoning/explanation (REASONING section; always comes first)
    - Python code to compute the answer (CODE section)
    - The computed answer with units (ANSWER section; always comes last)

**Output format:**

REASONING:  
[Detailed chain-of-thought reasoning steps in sentences and bullet points, include clarification of given data, physics principles, relevant equations, unit handling, and computational plan.]

CODE:  
```python
# [Python code to solve the specific physics problem, including comments]
```

ANSWER:  
[Final answer, including value(s) and proper units]

---

**Example:**

_Input:_  
A car accelerates from rest to 25 m/s in 10 seconds. What is its acceleration?

REASONING:  
- The car starts from rest, so initial velocity \( u = 0 \) m/s.
- Final velocity \( v = 25 \) m/s.
- Time \( t = 10 \) s.
- We use the formula: \( a = (v - u)/t \).
- Substitute the known values into the formula.

CODE:  
```python
u = 0  # initial velocity in m/s
v = 25  # final velocity in m/s
t = 10  # time in seconds
a = (v - u) / t  # acceleration in m/s^2
print(f\"{a} m/s^2\")
```

ANSWER:  
2.5 m/s²

---

**Important:**  
- Always explain your solution process before coding.
- Always conclude with the final answer in correct units.
- Do not skip steps unless they are trivial.

[Reminder: Your task is to analyze each physics question, reason step-by-step, write Python code for calculation, and then present the answer with correct units. Maintain the prescribed output order and structure.]""",
  model="gpt-5.2",
  output_type=PythonSchema,
  model_settings=ModelSettings(
    store=True,
    reasoning=Reasoning(
      effort="low",
      summary="auto"
    )
  )
)


class WorkflowInput(BaseModel):
  input_as_text: str


# Main code entrypoint
async def run_workflow(workflow_input: WorkflowInput):
  with trace("Isomorphic Physics Question Generation Agent - Modified"):
    state = {
      "questions_obj": {
        "questions": [

        ]
      },
      "isomorphic_questions_obj": {
        "isomorphic_questions": [

        ]
      },
      "user_specifications_str": None,
      "user_config_obj": {
        "new_questions_per_question": None,
        "difficulty": "",
        "theme": "",
        "title": "",
        "notes": ""
      }
    }
    workflow = workflow_input.model_dump()
    conversation_history: list[TResponseInputItem] = [
      {
        "role": "user",
        "content": [
          {
            "type": "input_text",
            "text": workflow["input_as_text"]
          }
        ]
      }
    ]
    normalizer_result_temp = await Runner.run(
      normalizer,
      input=[
        *conversation_history,
        {
          "role": "user",
          "content": [
            {
              "type": "input_text",
              "text": f"""Defaults unless specified otherwise:
            New questions per question is one-to-one.
            Difficulty remains the same.
            Theme remains the same.
            Title is 'Isomorphic Questions' unless one is extracted from the input source.

             {workflow["input_as_text"]}"""
            }
          ]
        }
      ],
      run_config=RunConfig(trace_metadata={
        "__trace_source__": "agent-builder",
        "workflow_id": "wf_6945e6c1d1e48190a4eed31ef49d60200258ba102ae1ea2b"
      })
    )

    conversation_history.extend([item.to_input_item() for item in normalizer_result_temp.new_items])

    normalizer_result = {
      "output_text": normalizer_result_temp.final_output.json(),
      "output_parsed": normalizer_result_temp.final_output.model_dump()
    }
    state["user_config_obj"] = normalizer_result["output_parsed"]
    extracter_result_temp = await Runner.run(
      extracter,
      input=[
        *conversation_history,
        {
          "role": "user",
          "content": [
            {
              "type": "input_text",
              "text": f"""Extract all information from any documents, text, images, etc.. that I provide. If I give any instructions relating to question difficulty, question generation amount, formatting, etc... you can disregard them and just put them into the 'User Instructions' at the end of the output.

             {workflow["input_as_text"]}"""
            }
          ]
        }
      ],
      run_config=RunConfig(trace_metadata={
        "__trace_source__": "agent-builder",
        "workflow_id": "wf_6945e6c1d1e48190a4eed31ef49d60200258ba102ae1ea2b"
      })
    )
    extracter_result = {
      "output_text": extracter_result_temp.final_output.json(),
      "output_parsed": extracter_result_temp.final_output.model_dump()
    }
    state["questions_obj"] = extracter_result["output_parsed"]
    transform_result = {"questions_obj": state["questions_obj"]}
    verifier_result_temp = await Runner.run(
      verifier,
      input=[
        {
          "role": "user",
          "content": [
            {
              "type": "input_text",
              "text": f"""{input["questions_obj"]}

            Verify all the answers for these questions are correct. If any answers are missing, generate the correct answer. If any are not multiple choice, remove the question entirely."""
            }
          ]
        }
      ],
      run_config=RunConfig(trace_metadata={
        "__trace_source__": "agent-builder",
        "workflow_id": "wf_6945e6c1d1e48190a4eed31ef49d60200258ba102ae1ea2b"
      })
    )

    conversation_history.extend([item.to_input_item() for item in verifier_result_temp.new_items])

    verifier_result = {
      "output_text": verifier_result_temp.final_output.json(),
      "output_parsed": verifier_result_temp.final_output.model_dump()
    }
    state["questions_obj"] = verifier_result["output_parsed"]
    transform_result1 = {"questions_obj": state["questions_obj"], "user_config_obj": state["user_config_obj"]}
    generation_result_temp = await Runner.run(
      generation,
      input=[
        {
          "role": "user",
          "content": [
            {
              "type": "input_text",
              "text": f"""Generate new questions according to configuration. 
            {input["user_config_obj"]}"""
            }
          ]
        },
        {
          "role": "user",
          "content": [
            {
              "type": "input_text",
              "text": f"""Question data:

             {input["questions_obj"]}"""
            }
          ]
        }
      ],
      run_config=RunConfig(trace_metadata={
        "__trace_source__": "agent-builder",
        "workflow_id": "wf_6945e6c1d1e48190a4eed31ef49d60200258ba102ae1ea2b"
      })
    )

    conversation_history.extend([item.to_input_item() for item in generation_result_temp.new_items])

    generation_result = {
      "output_text": generation_result_temp.final_output.json(),
      "output_parsed": generation_result_temp.final_output.model_dump()
    }
