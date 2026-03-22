from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


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
    difficulty: str
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
    difficulty: str
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
    """Output from the LaTeX Formatter agent."""

    latex_content: str
    filename: str
    pdf_filename: str = ""


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
