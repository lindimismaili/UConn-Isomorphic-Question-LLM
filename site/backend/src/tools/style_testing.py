"""Developer-only local style checks for generated question artifacts.

This module is intentionally not used by the runtime workflow. It exists so
prompt or agent changes can be regression-tested locally against saved outputs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..artifacts import load_agent_input_payload, load_agent_output_payload
from ..schemas import GeneratorOutput, NewQuestionItem, SourceQuestionItem

WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)?")
SENTENCE_END_RE = re.compile(r"[.!?]+(?=\s|$)")
LATEX_MARKER_RE = re.compile(r"(\\\(|\\\[|\$|\\frac|\\vec|\\hat|\\text\{|\\begin\{)")
DIRECTIVE_START_WORDS = {
    "calculate",
    "compute",
    "determine",
    "estimate",
    "evaluate",
    "explain",
    "find",
    "identify",
    "justify",
    "predict",
    "show",
    "solve",
    "state",
}


def _normalize_text(value: Any) -> str:
    """Collapse whitespace in question text for simple style analysis."""

    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _word_count(text: str) -> int:
    """Count approximate word tokens in freeform question text."""

    return len(WORD_RE.findall(text))


def _sentence_count(text: str) -> int:
    """Count approximate sentence boundaries in freeform question text."""

    if not text:
        return 0
    sentence_count = len(SENTENCE_END_RE.findall(text))
    return max(1, sentence_count)


def _verbosity_bucket(word_count: int) -> str:
    """Map a word count to a coarse verbosity label."""

    if word_count >= 55:
        return "very_verbose"
    if word_count >= 32:
        return "verbose"
    if word_count >= 16:
        return "medium"
    return "concise"


def _opening_style(text: str) -> str:
    """Classify whether a question opens narratively or as a direct directive."""

    match = WORD_RE.search(text.lower())
    if match and match.group(0) in DIRECTIVE_START_WORDS:
        return "direct"
    return "narrative"


def _text_style_profile(text: str) -> dict[str, Any]:
    """Build a compact style profile for one question stem."""

    normalized = _normalize_text(text)
    word_count = _word_count(normalized)
    sentence_count = _sentence_count(normalized)
    return {
        "word_count": word_count,
        "sentence_count": sentence_count,
        "avg_words_per_sentence": round(word_count / sentence_count, 2)
        if sentence_count
        else 0.0,
        "verbosity_bucket": _verbosity_bucket(word_count),
        "opening_style": _opening_style(normalized),
        "has_question_mark": "?" in normalized,
        "latex_marker_count": len(LATEX_MARKER_RE.findall(normalized)),
    }


def _style_guidance_for_profile(
    *,
    question_type: str,
    stem_profile: dict[str, Any],
    part_profiles: list[dict[str, Any]],
) -> str:
    """Render a short natural-language guidance string for style preservation."""

    bits = [
        (
            f"Match a {stem_profile['verbosity_bucket']} {question_type.replace('_', ' ')} "
            f"stem of about {stem_profile['word_count']} words "
            f"across {stem_profile['sentence_count']} sentence(s)."
        )
    ]
    if stem_profile["opening_style"] == "narrative":
        bits.append("Keep a scenario-led setup instead of compressing it into a bare directive.")
    else:
        bits.append("Keep the phrasing direct and task-focused instead of adding narrative padding.")
    if part_profiles:
        bits.append(
            f"Preserve the {len(part_profiles)}-part structure and keep each sub-part close to the source's detail level."
        )
    return " ".join(bits)


def _question_style_target(question: SourceQuestionItem) -> dict[str, Any]:
    """Summarize the source question style for downstream generation."""

    stem_profile = _text_style_profile(question.stem)
    part_profiles = [
        {
            "part_label": part.part_label,
            "question_type": part.question_type,
            "stem": _text_style_profile(part.stem),
        }
        for part in question.parts
    ]
    return {
        "source_id": question.source_id,
        "question_type": question.question_type,
        "stem": stem_profile,
        "part_count": len(question.parts),
        "parts": part_profiles,
        "guidance": _style_guidance_for_profile(
            question_type=question.question_type,
            stem_profile=stem_profile,
            part_profiles=part_profiles,
        ),
    }


def build_source_style_targets(
    questions: list[SourceQuestionItem],
) -> dict[str, dict[str, Any]]:
    """Build per-source style targets for developer reports."""

    return {question.source_id: _question_style_target(question) for question in questions}


def _word_ratio_bounds(
    *,
    source_words: int,
    question_type: str,
    opening_style: str,
) -> tuple[float, float]:
    """Return conservative acceptable word-count ratios for style matching."""

    if source_words <= 0:
        return (0.0, float("inf"))

    if source_words < 12:
        low, high = 0.55, 1.9
    elif source_words < 20:
        low, high = 0.65, 1.65
    elif source_words < 35:
        low, high = 0.7, 1.5
    else:
        low, high = 0.75, 1.35

    if question_type == "open_ended":
        low = max(low, 0.75 if source_words < 35 else 0.8)
        high = min(high, 1.45 if source_words < 35 else 1.3)

    if opening_style == "narrative" and source_words >= 25:
        low = max(low, 0.78)

    return (low, high)


def _compare_style_profiles(
    *,
    label: str,
    question_type: str,
    source_profile: dict[str, Any],
    generated_profile: dict[str, Any],
) -> list[str]:
    """Return human-readable style drift messages for one stem/part."""

    messages: list[str] = []
    source_words = int(source_profile.get("word_count", 0) or 0)
    generated_words = int(generated_profile.get("word_count", 0) or 0)
    if source_words > 0:
        low, high = _word_ratio_bounds(
            source_words=source_words,
            question_type=question_type,
            opening_style=str(source_profile.get("opening_style", "narrative")),
        )
        ratio = generated_words / source_words if source_words else 1.0
        if ratio < low or ratio > high:
            messages.append(
                f"{label} drifted from {source_words} to {generated_words} words "
                f"(target ratio {low:.2f}-{high:.2f}, observed {ratio:.2f})."
            )

    source_sentences = int(source_profile.get("sentence_count", 0) or 0)
    generated_sentences = int(generated_profile.get("sentence_count", 0) or 0)
    if source_sentences >= 3 and generated_sentences < source_sentences - 1:
        messages.append(
            f"{label} lost sentence-level scaffolding ({source_sentences} sentences to {generated_sentences})."
        )

    source_opening_style = str(source_profile.get("opening_style", "narrative"))
    generated_opening_style = str(generated_profile.get("opening_style", "narrative"))
    if (
        source_opening_style == "narrative"
        and generated_opening_style == "direct"
        and source_words >= 25
    ):
        messages.append(
            f"{label} shifted from a narrative/setup style to a direct prompt style."
        )
    if (
        source_opening_style == "direct"
        and generated_opening_style == "narrative"
        and source_words <= 18
    ):
        messages.append(
            f"{label} added narrative padding even though the source is concise and directive."
        )

    return messages


def find_style_drift_issues(
    source_questions: list[SourceQuestionItem],
    generated_questions: list[NewQuestionItem],
) -> list[dict[str, Any]]:
    """Detect generated questions whose verbosity/style drifted too far from source."""

    source_by_id = {question.source_id: question for question in source_questions}
    targets_by_id = build_source_style_targets(source_questions)
    issues: list[dict[str, Any]] = []

    for generated in generated_questions:
        if generated.skipped:
            continue
        source = source_by_id.get(generated.source_id)
        target = targets_by_id.get(generated.source_id)
        if source is None or target is None:
            continue

        stem_profile = _text_style_profile(generated.stem)
        messages = _compare_style_profiles(
            label="Stem",
            question_type=source.question_type,
            source_profile=target["stem"],
            generated_profile=stem_profile,
        )

        if source.question_type == "multi_part":
            if len(generated.parts) != len(source.parts):
                messages.append(
                    f"Part structure changed from {len(source.parts)} to {len(generated.parts)} parts."
                )
            else:
                for source_part, generated_part, part_target in zip(
                    source.parts,
                    generated.parts,
                    target["parts"],
                    strict=False,
                ):
                    part_messages = _compare_style_profiles(
                        label=f"Part {source_part.part_label}",
                        question_type=source_part.question_type,
                        source_profile=part_target["stem"],
                        generated_profile=_text_style_profile(generated_part.stem),
                    )
                    messages.extend(part_messages)

        if messages:
            issues.append(
                {
                    "source_id": generated.source_id,
                    "generated_id": generated.id,
                    "question_type": source.question_type,
                    "messages": messages,
                    "source_style_target": target,
                    "generated_style_observed": {
                        "stem": stem_profile,
                        "part_count": len(generated.parts),
                        "parts": [
                            {
                                "part_label": part.part_label,
                                "question_type": part.question_type,
                                "stem": _text_style_profile(part.stem),
                            }
                            for part in generated.parts
                        ],
                    },
                }
            )

    return issues


def summarize_style_drift_issues(
    issues: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Group style drift issues by source_id for developer reports."""

    summary: dict[str, dict[str, Any]] = {}
    for issue in issues:
        source_id = str(issue.get("source_id", ""))
        if not source_id:
            continue
        current = summary.setdefault(
            source_id,
            {
                "messages": [],
                "source_style_target": issue.get("source_style_target", {}),
                "generated_style_observed": [],
                "generated_ids": [],
            },
        )
        for message in issue.get("messages", []):
            if message not in current["messages"]:
                current["messages"].append(message)
        current["generated_style_observed"].append(issue.get("generated_style_observed", {}))
        generated_id = issue.get("generated_id")
        if generated_id not in current["generated_ids"]:
            current["generated_ids"].append(generated_id)
    return summary


def normalize_generated_question_sequence(
    source_questions: list[SourceQuestionItem],
    generated_questions: list[NewQuestionItem],
) -> list[NewQuestionItem]:
    """Order generated questions by source order and renumber IDs sequentially."""

    source_order = {
        question.source_id: index for index, question in enumerate(source_questions)
    }
    ordered_pairs = sorted(
        enumerate(generated_questions),
        key=lambda pair: (source_order.get(pair[1].source_id, len(source_order)), pair[0]),
    )
    ordered = [question for _index, question in ordered_pairs]
    for index, question in enumerate(ordered, start=1):
        question.id = index
    return ordered


def _message_input_texts(value: Any) -> list[str]:
    """Extract input_text strings from an Agents SDK message payload."""

    texts: list[str] = []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        content = value.get("content")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "input_text":
                    raw_text = item.get("text")
                    if isinstance(raw_text, str):
                        texts.append(raw_text)
        return texts
    if isinstance(value, list):
        for item in value:
            texts.extend(_message_input_texts(item))
    return texts


def extract_agent_input_payload(value: Any) -> dict[str, Any]:
    """Extract the structured JSON payload embedded in an agent input artifact."""

    if isinstance(value, dict):
        if "input" in value and isinstance(value["input"], (dict, list, str)):
            return extract_agent_input_payload(value["input"])
        structured_keys = (
            "task",
            "configuration",
            "source_questions",
            "draft_isomorphic_questions",
            "requirements",
        )
        if any(key in value for key in structured_keys):
            return value

    for text in _message_input_texts(value):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def generator_style_report_from_outputs(outputs_dir: Path) -> dict[str, Any]:
    """Analyze one completed output folder's generator artifacts."""

    generator_input = load_agent_input_payload(outputs_dir, "generator")
    generator_output = load_agent_output_payload(outputs_dir, "generator")
    if generator_input is None:
        raise FileNotFoundError(f"Missing generator input artifact in {outputs_dir}")
    if generator_output is None:
        raise FileNotFoundError(f"Missing generator output artifact in {outputs_dir}")

    input_payload = extract_agent_input_payload(generator_input)
    source_container = input_payload.get("source_questions", {})
    source_list = source_container.get("questions", []) if isinstance(source_container, dict) else []
    source_questions_parsed = [
        SourceQuestionItem.model_validate(item) for item in source_list if isinstance(item, dict)
    ]
    generator_output_parsed = GeneratorOutput.model_validate(generator_output)
    generated_questions = normalize_generated_question_sequence(
        source_questions_parsed,
        generator_output_parsed.isomorphic_questions,
    )
    issues = find_style_drift_issues(source_questions_parsed, generated_questions)
    return {
        "outputs_dir": str(outputs_dir),
        "source_question_count": len(source_questions_parsed),
        "generated_question_count": len(generated_questions),
        "issue_count": len(issues),
        "issues": issues,
        "issues_by_source_id": summarize_style_drift_issues(issues),
    }
