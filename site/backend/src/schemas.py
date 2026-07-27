"""Pydantic schemas shared by the workflow runner, API server, and agents."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class OptionItem(BaseModel):
    """A single option in a multiple-choice question."""

    key: str
    text: str
    is_correct: bool
    explanation: str
    distractor_logic: str = ""


class QuestionPart(BaseModel):
    """A sub-part of a multi-part question."""

    part_label: str
    stem: str
    question_type: str
    options: list[OptionItem] = []
    answer: str = ""
    has_explicit_answer: bool = False


class QuestionClassification(BaseModel):
    """Verifier-derived classification of a source question.

    A compact per-question profile downstream agents (generator, evaluator) can
    key off. Populated by the Verifier; left null on raw extracter output so
    downstream stays backward-compatible. Controlled-vocabulary fields use the
    values listed below; concepts/equations/misconceptions are free-form lists.
    """

    # "single" | "multi_part" | "shared_scenario"
    structure: str = ""
    # "concise" (straight-to-the-point) | "narrative" (story-like / content-rich)
    language: str = ""
    # "abstract" | "life_sciences" | "engineering" | "regional" | "modern_culture"
    # | "everyday" | "other"
    scenario_type: str = ""
    # "conceptual" | "symbolic" | "numerical"
    representation: str = ""
    # Main physics concept(s) the question is built on.
    concepts: list[str] = []
    # Fundamental equation(s) in LaTeX.
    equations: list[str] = []
    # Suggested misconceptions; for multiple-choice these should align with the
    # source distractors' distractor_logic.
    misconceptions: list[str] = []


class SourceQuestionItem(BaseModel):
    """A question extracted from source material."""

    source_id: str
    source_raw: str
    stem: str
    options: list[OptionItem]
    has_explicit_answer: bool
    difficulty: str = ""
    question_type: str
    tags: list[str] = []
    has_figure: bool = False
    answer: str = ""
    parts: list[QuestionPart] = []
    points: float = 0.0
    # Generic shared-context grouping. Questions that share a non-empty group_id
    # belong to one shared-stimulus block (e.g. "For questions 1-5, consider...").
    # group_context holds that shared preamble and is repeated on every member so
    # each item is self-describing for downstream agents and the eval judge.
    group_id: str = ""
    group_context: str = ""
    # Verifier-derived per-question profile (null on raw extracter output).
    classification: Optional[QuestionClassification] = None


class NewQuestionPart(BaseModel):
    """A sub-part of a generated multi-part question."""

    part_label: str
    stem: str
    question_type: str
    options: list[OptionItem] = []
    answer: str = ""
    explanation: str = ""


class QuestionDecomposition(BaseModel):
    """Structured breakdown of a source question's reusable components.

    Populated only by the decompose-then-generate strategy
    (GENERATION_STRATEGY=decompose); left null under the default holistic
    strategy so downstream stages stay backward-compatible. Kept for
    auditability and for comparing the two generation strategies.
    """

    scenario: str = ""
    physics_concept: str = ""
    variables: list[str] = []
    numbers: list[str] = []
    distractor_roles: list[str] = []


class NewQuestionItem(BaseModel):
    """A newly generated isomorphic question."""

    id: int
    source_id: str
    stem: str
    options: list[OptionItem]
    difficulty: str = ""
    tags: list[str] = []
    generation_notes: str
    question_type: str = "multiple_choice"
    skipped: bool = False
    skip_reason: str = ""
    answer: str = ""
    explanation: str = ""
    parts: list[NewQuestionPart] = []
    points: float = 0.0
    # Mirrors the source grouping primitive: generated members of one source group
    # keep a shared group_id and an isomorphic shared preamble in group_context.
    group_id: str = ""
    group_context: str = ""
    # Filled only by the decompose-then-generate strategy; null under the
    # default holistic strategy.
    decomposition: Optional[QuestionDecomposition] = None


class NormalizerOutput(BaseModel):
    """Output from the Normalizer agent."""

    new_questions_per_question: int = Field(default=1, ge=1, le=5)
    difficulty: str = ""
    theme: str = ""
    title: str = ""
    notes: str = ""
    show_answers: bool = True


class ExtracterOutput(BaseModel):
    """Output from the Extracter agent."""

    filename: str = ""
    title: str = ""
    subtitle: str = ""
    theme: str = ""
    author: str = ""
    date: str = ""
    course: str = ""
    questions: list[SourceQuestionItem]


class VerifierOutput(BaseModel):
    """Output from the Verifier agent."""

    questions: list[SourceQuestionItem]


class GeneratorOutput(BaseModel):
    """Output from the Generator agent."""

    isomorphic_questions: list[NewQuestionItem]


class LaTeXOutput(BaseModel):
    """Output from the Latexifier agent."""

    latex_content: str
    filename: str
    pdf_filename: str = ""


class NormalizerUserInput(BaseModel):
    """User-facing request payload passed into the Normalizer agent."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = ""
    files: list[str] = []


class NormalizerInput(BaseModel):
    """Input expected by the Normalizer agent."""

    model_config = ConfigDict(extra="forbid")

    task: Literal["normalize_user_request"]
    user_input: NormalizerUserInput
    defaults: NormalizerOutput
    rules: dict[str, str]


class ExtracterInput(BaseModel):
    """Input expected by the Extracter agent."""

    model_config = ConfigDict(extra="forbid")

    task: str
    files: list[str]
    normalizer_config: NormalizerOutput
    requirements: dict[str, Any]


class VerifierInput(BaseModel):
    """Input expected by the Verifier agent."""

    model_config = ConfigDict(extra="forbid")

    task: Literal["verify_source_questions"]
    source_questions: ExtracterOutput
    requirements: dict[str, Any]


class GeneratorInput(BaseModel):
    """Input expected by the Generator agent."""

    model_config = ConfigDict(extra="forbid")

    task: str
    configuration: NormalizerOutput
    source_questions: VerifierOutput
    requirements: dict[str, Any]


class LaTeXDocumentInput(BaseModel):
    """Document metadata passed into the Latexifier agent."""

    model_config = ConfigDict(extra="forbid")

    title: str = ""
    subtitle: str = ""
    theme: str = ""
    source_title: str = ""
    course: str = ""


class LaTeXQuestionInput(BaseModel):
    """Generated question shape consumed by the Latexifier agent."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    stem: str
    options: list[OptionItem] = []
    question_type: str = "multiple_choice"
    skipped: bool = False
    skip_reason: str = ""
    answer: str = ""
    explanation: str = ""
    parts: list[NewQuestionPart] = []
    points: float = 0.0
    group_id: str = ""
    group_context: str = ""


class LaTeXInput(BaseModel):
    """Input expected by the Latexifier agent."""

    model_config = ConfigDict(extra="forbid")

    task: Literal["format_questions_to_latex"]
    document: LaTeXDocumentInput
    questions: list[LaTeXQuestionInput]
    requirements: dict[str, Any]


class EvaluationFinalResultInput(BaseModel):
    """Question context consumed by the Evaluation agent."""

    model_config = ConfigDict(extra="forbid")

    config: NormalizerOutput
    source_questions: list[SourceQuestionItem]
    verified_questions: list[SourceQuestionItem]
    generated_questions: list[NewQuestionItem]


class EvaluationInput(BaseModel):
    """Input expected by the Evaluation agent."""

    model_config = ConfigDict(extra="forbid")

    task: Literal["evaluate_generated_output_artifacts"]
    workflow_id: str
    latex_file: str
    pdf_file: str
    latex_output: LaTeXOutput
    final_result: EvaluationFinalResultInput
    rubric: Optional[dict[str, Any]] = None
    evaluation_items: list[dict[str, Any]] = []


class WorkflowInput(BaseModel):
    """Input for the workflow."""

    file_paths: list[str] = []
    user_instructions: str = ""


class WorkflowResult(BaseModel):
    """Final output from the workflow."""

    config: NormalizerOutput
    source_questions: list[SourceQuestionItem]
    verified_questions: list[SourceQuestionItem]
    generated_questions: list[NewQuestionItem]
    latex_file: str = ""
    pdf_file: str = ""
    evaluation: Optional[dict[str, Any]] = None


AGENT_OUTPUT_TYPES = {
    "normalizer": NormalizerOutput,
    "extracter": ExtracterOutput,
    "verifier": VerifierOutput,
    "generator": GeneratorOutput,
    "latexifier": LaTeXOutput,
}


AGENT_INPUT_TYPES = {
    "normalizer": NormalizerInput,
    "extracter": ExtracterInput,
    "verifier": VerifierInput,
    "generator": GeneratorInput,
    "latexifier": LaTeXInput,
    "evaluation": EvaluationInput,
}
