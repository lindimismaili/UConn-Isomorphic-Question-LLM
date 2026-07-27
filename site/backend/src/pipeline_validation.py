"""Deterministic validation for workflow handoffs."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

from .schemas import GeneratorOutput, SourceQuestionItem, VerifierOutput


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


def _has_distractor_logic(options: Iterable[object]) -> bool:
    return all(bool(str(getattr(option, "distractor_logic", "")).strip()) for option in options)


def validate_verifier_output(
    extracted_questions: list[SourceQuestionItem],
    verified_output: VerifierOutput,
) -> ValidationReport:
    """Validate that verifier output preserves and completes source questions."""
    report = ValidationReport()
    extracted_ids = [q.source_id for q in extracted_questions]
    verified_ids = [q.source_id for q in verified_output.questions]
    for source_id in sorted(
        item for item, count in Counter(extracted_ids).items() if count > 1
    ):
        report.add(f"Extracter returned duplicate source_id {source_id}", source_id)
    for source_id in sorted(
        item for item, count in Counter(verified_ids).items() if count > 1
    ):
        report.add(f"Verifier returned duplicate source_id {source_id}", source_id)

    extracted_id_set = set(extracted_ids)
    verified_by_id = {q.source_id: q for q in verified_output.questions}
    extra_ids = sorted(set(verified_by_id) - extracted_id_set)
    for source_id in extra_ids:
        report.add(f"Verifier returned unknown source_id {source_id}", source_id)
    verified_known_order = [item for item in verified_ids if item in extracted_id_set]
    expected_known_order = [item for item in extracted_ids if item in set(verified_known_order)]
    if verified_known_order != expected_known_order:
        report.add("Verifier changed source question order")

    for source in extracted_questions:
        verified = verified_by_id.get(source.source_id)
        if verified is None:
            if not source.has_figure:
                report.add(f"Verifier omitted source_id {source.source_id}", source.source_id)
            continue
        if verified.has_figure:
            if verified.question_type == "multiple_choice" or verified.options:
                if not _has_explanations(verified.options):
                    report.add(
                        f"Verifier source_id {verified.source_id} (figure) has missing option explanations",
                        verified.source_id,
                    )
                if not _has_distractor_logic(verified.options):
                    report.add(
                        f"Verifier source_id {verified.source_id} (figure) has missing option distractor_logic",
                        verified.source_id,
                    )
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
            if not _has_distractor_logic(verified.options):
                report.add(
                    f"Verifier source_id {verified.source_id} has missing option distractor_logic",
                    verified.source_id,
                )
        if verified.question_type == "open_ended" and not verified.answer.strip():
            report.add(f"Verifier source_id {verified.source_id} is missing an answer", verified.source_id)
        if verified.question_type == "multi_part":
            if len(verified.parts) != len(source.parts):
                report.add(f"Verifier source_id {verified.source_id} changed part count", verified.source_id)
            for part in verified.parts:
                if part.question_type == "multiple_choice" or part.options:
                    if (
                        _correct_count(part.options) != 1
                        or not _has_explanations(part.options)
                        or not _has_distractor_logic(part.options)
                    ):
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


def _collect_distractor_labels(options: Iterable[object]) -> list[str]:
    labels: list[str] = []
    for option in options:
        raw = str(getattr(option, "distractor_logic", "") or "").strip().lower()
        if raw:
            labels.append(raw)
    return labels


# Tokens that carry no misconception meaning on their own.
_LOGIC_STOPWORDS = frozenset({
    "the", "a", "an", "of", "by", "and", "to", "with", "in", "on", "for",
    "is", "are", "then", "that", "it", "as", "or", "from", "into", "this",
    "be", "at", "its", "use", "uses", "using", "value", "number",
})
_MATH_SPAN_RE = re.compile(r"\\\(.*?\\\)|\\\[.*?\\\]|\$.*?\$", re.DOTALL)
_BACKSLASH_CMD_RE = re.compile(r"\\[a-zA-Z]+")


def _logic_signature(label: str) -> frozenset[str]:
    """Reduce a `distractor_logic` label to a wording/number-invariant token set.

    The misconception *logic* must be preserved across an isomorphic variant, but
    the wording and numbers must not (the generator is told to vary numbers, and
    to rewrite the label "to reference the new quantities"). So strip LaTeX math
    spans and commands — which carry the question-specific numbers — keep the
    alphabetic skeleton, and drop stopwords. Two labels describing the same error
    with different numbers/phrasing collapse to the same (or overlapping) set.
    """
    text = _MATH_SPAN_RE.sub(" ", label.lower())
    text = _BACKSLASH_CMD_RE.sub(" ", text)
    tokens = re.findall(r"[a-z]+", text)
    return frozenset(token for token in tokens if token not in _LOGIC_STOPWORDS)


def _logic_preserved(source_sig: frozenset[str], generated_sigs: list[frozenset[str]]) -> bool:
    """True if some generated label carries the same misconception logic.

    Matches on containment or majority token overlap (Jaccard >= 0.5), so a
    renumbered/reworded restatement of the same error counts as preserved while a
    genuinely different (or dropped) misconception does not. A source label with
    no checkable tokens (pure math/numbers) is not enforced.
    """
    if not source_sig:
        return True
    for gen_sig in generated_sigs:
        if not gen_sig:
            continue
        if source_sig <= gen_sig:
            return True
        union = len(source_sig | gen_sig)
        if union and len(source_sig & gen_sig) / union >= 0.5:
            return True
    return False


def validate_distractor_logic_preservation(
    source_questions: list[SourceQuestionItem],
    generated_output: GeneratorOutput,
) -> ValidationReport:
    """Verify every source misconception is preserved in the matching generated item.

    The rubric's `distractor_structure_preservation` criterion treats misconception
    preservation as the central pedagogical property; this check enforces it
    deterministically before the LLM judge is involved. Matching is on the
    misconception *logic* (a number/wording-invariant signature), not the verbatim
    label — an isomorphic variant keeps the error type but changes the numbers.
    """
    report = ValidationReport()
    generated_by_source: dict[str, list] = {}
    for generated in generated_output.isomorphic_questions:
        generated_by_source.setdefault(generated.source_id, []).append(generated)

    for source in source_questions:
        if source.has_figure:
            continue
        # Exclude the generic sentinel: it explicitly means "no specific
        # misconception to preserve".
        source_labels = [
            label for label in _collect_distractor_labels(getattr(source, "options", []))
            if label != "generic_plausible_value"
        ]
        if not source_labels:
            continue
        for generated in generated_by_source.get(source.source_id, []):
            if generated.skipped:
                continue
            generated_sigs = [
                _logic_signature(label)
                for label in _collect_distractor_labels(getattr(generated, "options", []))
            ]
            missing = [
                label for label in source_labels
                if not _logic_preserved(_logic_signature(label), generated_sigs)
            ]
            if missing:
                report.add(
                    f"Generator source_id {source.source_id} missing distractor_logic label(s): "
                    f"{', '.join(sorted(set(missing)))}",
                    source.source_id,
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
    source_ids = [q.source_id for q in source_questions]
    for source_id in sorted(
        item for item, count in Counter(source_ids).items() if count > 1
    ):
        report.add(f"Verifier returned duplicate source_id {source_id}", source_id)
    source_by_id = {q.source_id: q for q in source_questions}
    generated_ids = [generated.id for generated in generated_output.isomorphic_questions]
    for generated_id in sorted(
        item for item, count in Counter(generated_ids).items() if count > 1
    ):
        report.add(f"Generator returned duplicate question id {generated_id}")
    for generated in generated_output.isomorphic_questions:
        if generated.source_id not in source_by_id:
            report.add(f"Generator returned unknown source_id {generated.source_id}", generated.source_id)
            continue
        generated_by_source.setdefault(generated.source_id, []).append(generated)

    expected_source_order: list[str] = []
    for source in source_questions:
        expected_count = 1 if source.has_figure else max(1, int(per_source or 1))
        expected_source_order.extend([source.source_id] * expected_count)
    actual_known_order = [
        generated.source_id
        for generated in generated_output.isomorphic_questions
        if generated.source_id in source_by_id
    ]
    if actual_known_order != expected_source_order:
        report.add("Generator changed source question order or grouping")

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
                if not _has_distractor_logic(generated.options):
                    report.add(
                        f"Generator source_id {source.source_id} has missing option distractor_logic",
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
                        if (
                            _correct_count(part.options) != 1
                            or not _has_explanations(part.options)
                            or not _has_distractor_logic(part.options)
                        ):
                            report.add(
                                f"Generator source_id {source.source_id} has incomplete MC part {part.part_label}",
                                source.source_id,
                            )
                    elif not part.answer.strip():
                        report.add(
                            f"Generator source_id {source.source_id} has missing answer for part {part.part_label}",
                            source.source_id,
                        )

    distractor_report = validate_distractor_logic_preservation(source_questions, generated_output)
    if not distractor_report.valid:
        report.valid = False
        report.errors.extend(distractor_report.errors)
        for sid in distractor_report.repair_source_ids:
            if sid not in report.repair_source_ids:
                report.repair_source_ids.append(sid)
    return report
