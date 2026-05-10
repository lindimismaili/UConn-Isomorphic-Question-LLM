"""Pydantic schemas shared by the workflow runner, API server, and agents."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict


class OptionItem(BaseModel):
    """A single option in a multiple-choice question."""

    key: str
    text: str
    is_correct: bool
    explanation: str


class QuestionPart(BaseModel):
    """A sub-part of a multi-part question."""

    part_label: str
    stem: str
    question_type: str
    options: list[OptionItem] = []
    answer: str = ""
    has_explicit_answer: bool = False


class SourceQuestionItem(BaseModel):
    """A question extracted from source material."""

    source_id: str
    source_raw: str
    stem: str
    options: list[OptionItem]
    has_explicit_answer: bool
    difficulty: str = ""
    question_type: str
    theme: str = ""
    topic_tags: list[str]
    has_figure: bool = False
    answer: str = ""
    parts: list[QuestionPart] = []


class NewQuestionPart(BaseModel):
    """A sub-part of a generated multi-part question."""

    part_label: str
    stem: str
    question_type: str
    options: list[OptionItem] = []
    answer: str = ""


class NewQuestionItem(BaseModel):
    """A newly generated isomorphic question."""

    id: int
    source_id: str
    stem: str
    options: list[OptionItem]
    difficulty: str = ""
    theme: str = ""
    topic_tags: list[str]
    generation_notes: str
    question_type: str = "multiple_choice"
    skipped: bool = False
    skip_reason: str = ""
    answer: str = ""
    parts: list[NewQuestionPart] = []


class NormalizerOutput(BaseModel):
    """Output from the Normalizer agent."""

    new_questions_per_question: int = 1
    difficulty: str = ""
    theme: str = ""
    title: str = ""
    notes: str = ""


class ExtracterOutput(BaseModel):
    """Output from the Extracter agent."""

    filename: str = ""
    title: str = ""
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
    parts: list[NewQuestionPart] = []


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
    "latex": LaTeXOutput,
}


AGENT_INPUT_TYPES = {
    "normalizer": NormalizerInput,
    "extracter": ExtracterInput,
    "verifier": VerifierInput,
    "generator": GeneratorInput,
    "latex": LaTeXInput,
    "evaluation": EvaluationInput,
}
