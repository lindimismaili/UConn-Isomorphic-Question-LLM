"""Deterministic validation for workflow handoffs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

try:
    from .schemas import GeneratorOutput, SourceQuestionItem, VerifierOutput
except ImportError:
    from schemas import GeneratorOutput, SourceQuestionItem, VerifierOutput


@dataclass
class ValidationReport:
    """Validation result for one workflow handoff."""

    valid: bool = True
    errors: list[str] = field(default_factory=list)
    repair_source_ids: list[str] = field(default_factory=list)

    def add(self, message: str, source_id: str | None = None) -> None:
        self.valid = False
        self.errors.append(message)
        if source_id and source_id not in self.repair_source_ids:
            self.repair_source_ids.append(source_id)

    def summary(self) -> str:
        return "; ".join(self.errors)

    def model_dump(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "errors": list(self.errors),
            "repair_source_ids": list(self.repair_source_ids),
        }


def _correct_count(options: Iterable[object]) -> int:
    return sum(1 for option in options if bool(getattr(option, "is_correct", False)))


def _has_explanations(options: Iterable[object]) -> bool:
    return all(bool(str(getattr(option, "explanation", "")).strip()) for option in options)


def validate_verifier_output(
    extracted_questions: list[SourceQuestionItem],
    verified_output: VerifierOutput,
) -> ValidationReport:
    """Validate that verifier output preserves and completes source questions."""
    report = ValidationReport()
    verified_by_id = {q.source_id: q for q in verified_output.questions}
    extra_ids = sorted(set(verified_by_id) - {q.source_id for q in extracted_questions})
    for source_id in extra_ids:
        report.add(f"Verifier returned unknown source_id {source_id}", source_id)

    for source in extracted_questions:
        verified = verified_by_id.get(source.source_id)
        if verified is None:
            if not source.has_figure:
                report.add(f"Verifier omitted source_id {source.source_id}", source.source_id)
            continue
        if verified.has_figure:
            continue
        if verified.question_type == "multiple_choice" or verified.options:
            if _correct_count(verified.options) != 1:
                report.add(
                    f"Verifier source_id {verified.source_id} must have exactly one correct option",
                    verified.source_id,
                )
            if not _has_explanations(verified.options):
                report.add(
                    f"Verifier source_id {verified.source_id} has missing option explanations",
                    verified.source_id,
                )
        if verified.question_type == "open_ended" and not verified.answer.strip():
            report.add(f"Verifier source_id {verified.source_id} is missing an answer", verified.source_id)
        if verified.question_type == "multi_part":
            if len(verified.parts) != len(source.parts):
                report.add(f"Verifier source_id {verified.source_id} changed part count", verified.source_id)
            for part in verified.parts:
                if part.question_type == "multiple_choice" or part.options:
                    if _correct_count(part.options) != 1 or not _has_explanations(part.options):
                        report.add(
                            f"Verifier source_id {verified.source_id} has incomplete MC part {part.part_label}",
                            verified.source_id,
                        )
                elif not part.answer.strip():
                    report.add(
                        f"Verifier source_id {verified.source_id} has missing answer for part {part.part_label}",
                        verified.source_id,
                    )
    return report


def validate_generator_output(
    source_questions: list[SourceQuestionItem],
    generated_output: GeneratorOutput,
    per_source: int,
) -> ValidationReport:
    """Validate that generated questions satisfy the workflow contract."""
    report = ValidationReport()
    generated_by_source: dict[str, list] = {}
    source_by_id = {q.source_id: q for q in source_questions}
    for generated in generated_output.isomorphic_questions:
        if generated.source_id not in source_by_id:
            report.add(f"Generator returned unknown source_id {generated.source_id}", generated.source_id)
            continue
        generated_by_source.setdefault(generated.source_id, []).append(generated)

    for source in source_questions:
        generated_items = generated_by_source.get(source.source_id, [])
        expected_count = 1 if source.has_figure else max(1, int(per_source or 1))
        if len(generated_items) != expected_count:
            report.add(
                f"Generator source_id {source.source_id} expected {expected_count} item(s), got {len(generated_items)}",
                source.source_id,
            )
        for generated in generated_items:
            if source.has_figure:
                if (
                    not generated.skipped
                    or generated.stem
                    or generated.options
                    or generated.parts
                    or not generated.skip_reason.strip()
                ):
                    report.add(
                        f"Generator source_id {source.source_id} must be a skipped figure placeholder",
                        source.source_id,
                    )
                continue
            if generated.skipped:
                report.add(f"Generator source_id {source.source_id} was unexpectedly skipped", source.source_id)
            if generated.question_type != source.question_type:
                report.add(
                    f"Generator source_id {source.source_id} changed type {source.question_type} -> {generated.question_type}",
                    source.source_id,
                )
            if generated.question_type == "multiple_choice":
                if _correct_count(generated.options) != 1:
                    report.add(
                        f"Generator source_id {source.source_id} must have exactly one correct option",
                        source.source_id,
                    )
                if not _has_explanations(generated.options):
                    report.add(
                        f"Generator source_id {source.source_id} has missing option explanations",
                        source.source_id,
                    )
            elif generated.question_type == "open_ended":
                if generated.options or generated.parts or not generated.answer.strip():
                    report.add(
                        f"Generator source_id {source.source_id} has invalid open-ended output",
                        source.source_id,
                    )
            elif generated.question_type == "multi_part":
                if generated.options or generated.answer:
                    report.add(
                        f"Generator source_id {source.source_id} has parent options/answer on multi-part output",
                        source.source_id,
                    )
                if len(generated.parts) != len(source.parts):
                    report.add(
                        f"Generator source_id {source.source_id} changed part count",
                        source.source_id,
                    )
                for part in generated.parts:
                    if part.question_type == "multiple_choice" or part.options:
                        if _correct_count(part.options) != 1 or not _has_explanations(part.options):
                            report.add(
                                f"Generator source_id {source.source_id} has incomplete MC part {part.part_label}",
                                source.source_id,
                            )
                    elif not part.answer.strip():
                        report.add(
                            f"Generator source_id {source.source_id} has missing answer for part {part.part_label}",
                            source.source_id,
                        )
    return report

