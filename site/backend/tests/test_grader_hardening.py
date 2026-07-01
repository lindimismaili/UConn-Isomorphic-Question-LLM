"""Tests for the hardened OpenAI-Evals grader.

Covers: reproducibility seed, per-criterion reasoning effort, distractor_logic
injection into the grader input, the FAILURE_TAGS taxonomy, parse_sample_steps,
failure-tag aggregation, the GRADER_MODEL_OVERRIDE knob, and report enrichment.
"""

from __future__ import annotations

from types import SimpleNamespace

from backend.src import evaluation
from backend.src.evaluation import (
    FAILURE_TAGS,
    GRADER_MODEL_OVERRIDE_ENV,
    GRADER_SEED,
    parse_sample_steps,
)

# ---------------------------------------------------------------------------
# FAILURE_TAGS vocabulary
# ---------------------------------------------------------------------------


def test_failure_tags_vocabulary_is_the_fixed_set():
    assert FAILURE_TAGS == (
        "CONCEPT_DRIFT",
        "MISSING_CONVERSION",
        "NUMERIC_INCONSISTENT",
        "WRONG_ANSWER",
        "DISTRACTOR_ROLE_LOST",
        "GENERIC_DISTRACTOR",
        "CLERICAL",
        "UNDERSPECIFIED",
        "OTHER",
    )


def test_failure_tags_importable_and_frozen():
    # Importable by other modules (meta-evaluator) and a tuple (immutable order).
    assert isinstance(FAILURE_TAGS, tuple)
    assert len(set(FAILURE_TAGS)) == len(FAILURE_TAGS)


# ---------------------------------------------------------------------------
# Reproducibility seed + per-criterion reasoning effort in testing criteria
# ---------------------------------------------------------------------------


def test_testing_criteria_carry_seed_and_per_criterion_reasoning():
    criteria = evaluation._build_testing_criteria(model="test-grader")
    by_name = {c["name"]: c for c in criteria}

    # Every grader is seeded for reproducibility.
    for crit in criteria:
        assert crit["sampling_params"]["seed"] == GRADER_SEED
        assert crit["model"] == "test-grader"

    # Hard criteria get raised reasoning; unset ones inherit the global default.
    global_effort = evaluation.rubric_reasoning_effort()
    assert by_name["physics_correctness"]["sampling_params"]["reasoning_effort"] == "high"
    assert (
        by_name["distractor_structure_preservation"]["sampling_params"]["reasoning_effort"]
        == "high"
    )
    assert by_name["preserve_physical_content"]["sampling_params"]["reasoning_effort"] == "medium"
    assert by_name["solvable_and_specified"]["sampling_params"]["reasoning_effort"] == "medium"
    assert by_name["scenario_variation"]["sampling_params"]["reasoning_effort"] == global_effort


def test_reasoning_effort_by_criterion_map():
    effort = evaluation.reasoning_effort_by_criterion()
    assert effort["physics_correctness"] == "high"
    assert effort["distractor_structure_preservation"] == "high"
    assert effort["preserve_physical_content"] == "medium"
    assert effort["solvable_and_specified"] == "medium"
    # Unset criteria fall back to the global effort.
    assert effort["clerical_quality"] == evaluation.rubric_reasoning_effort()


def test_criterion_reasoning_effort_falls_back():
    assert evaluation.criterion_reasoning_effort({"reasoning": "high"}) == "high"
    assert (
        evaluation.criterion_reasoning_effort({"reasoning": "  "})
        == evaluation.rubric_reasoning_effort()
    )
    assert evaluation.criterion_reasoning_effort({}) == evaluation.rubric_reasoning_effort()


# ---------------------------------------------------------------------------
# Failure-tag instruction reaches every grader's system prompt
# ---------------------------------------------------------------------------


def test_grader_system_prompt_lists_failure_tags():
    criteria = evaluation._build_testing_criteria(model="m")
    system = criteria[0]["input"][0]["content"]
    for tag in FAILURE_TAGS:
        assert tag in system
    assert "[WRONG_ANSWER]" in system


def test_grader_user_content_references_distractor_logic_field():
    criteria = evaluation._build_testing_criteria(model="m")
    user = criteria[0]["input"][1]["content"]
    assert "{{ item.source_distractor_logic }}" in user
    assert "{{ item.source_question_text }}" in user
    assert "{{ item.generated_question_text }}" in user


# ---------------------------------------------------------------------------
# distractor_logic injection into rendered text + item schema
# ---------------------------------------------------------------------------


def test_item_schema_includes_source_distractor_logic():
    schema = evaluation._build_item_schema()
    assert "source_distractor_logic" in schema["properties"]
    assert "source_distractor_logic" in schema["required"]


def test_option_lines_append_distractor_logic_when_present():
    lines = evaluation._format_option_lines(
        [
            {
                "key": "A",
                "text": "5 m/s",
                "is_correct": False,
                "explanation": "used mass not weight",
                "distractor_logic": "mass_for_weight_swap",
            },
            {"key": "B", "text": "10 m/s", "is_correct": True, "explanation": "correct"},
        ]
    )
    assert "distractor_logic: mass_for_weight_swap" in lines[0]
    # Legacy option without a label renders unchanged (no stray field).
    assert "distractor_logic:" not in lines[1]


def test_summarize_source_distractor_logic():
    question = {
        "options": [
            {"distractor_logic": "sign_error"},
            {"distractor_logic": "forgot_conversion"},
            {"distractor_logic": ""},
        ]
    }
    summary = evaluation._summarize_source_distractor_logic(question)
    assert summary == "sign_error; forgot_conversion"
    # Legacy item with no labels -> empty string (robust to absence).
    assert evaluation._summarize_source_distractor_logic({"options": []}) == ""


def test_summarize_source_distractor_logic_from_parts():
    question = {
        "parts": [
            {"options": [{"distractor_logic": "role_a"}]},
            {"options": [{"distractor_logic": "role_b"}]},
        ]
    }
    assert evaluation._summarize_source_distractor_logic(question) == "role_a; role_b"


# ---------------------------------------------------------------------------
# parse_sample_steps mirrors the JS parseSampleSteps contract
# ---------------------------------------------------------------------------


def test_parse_sample_steps_reads_output_content_json():
    sample = {
        "output": [
            {
                "content": (
                    '{"result": 0, "steps": ['
                    '{"conclusion": "[WRONG_ANSWER] bad", "description": "the answer is wrong"}'
                    "]}"
                )
            }
        ]
    }
    steps = parse_sample_steps(sample)
    assert steps == [
        {"conclusion": "[WRONG_ANSWER] bad", "description": "the answer is wrong"}
    ]


def test_parse_sample_steps_non_json_content_returns_single_step():
    sample = {"output": [{"content": "free text reasoning"}]}
    steps = parse_sample_steps(sample)
    assert steps == [{"conclusion": "", "description": "free text reasoning"}]


class _ModelDumpSample:
    """Minimal stand-in for an OpenAI SDK sample object (has model_dump)."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def model_dump(self, *args, **kwargs) -> dict:
        return self._payload


def test_parse_sample_steps_handles_missing_and_object_samples():
    assert parse_sample_steps(None) == []
    assert parse_sample_steps({}) == []
    assert parse_sample_steps({"output": []}) == []
    # SDK-style object (has model_dump) is normalized via to_jsonable.
    obj = _ModelDumpSample(
        {"output": [{"content": '{"steps": [{"conclusion": "c", "description": "d"}]}'}]}
    )
    assert parse_sample_steps(obj) == [{"conclusion": "c", "description": "d"}]


# ---------------------------------------------------------------------------
# Failure-tag mining + aggregation
# ---------------------------------------------------------------------------


def _sample_with_tag(tag: str) -> dict:
    return {
        "output": [
            {
                "content": (
                    '{"result": 0, "steps": ['
                    f'{{"conclusion": "[{tag}] reason", "description": "why"}}'
                    "]}"
                )
            }
        ]
    }


def test_extract_failure_tag_from_sample():
    assert evaluation._extract_failure_tag(_sample_with_tag("WRONG_ANSWER")) == "WRONG_ANSWER"
    # Unknown bracket text is not a valid tag.
    assert evaluation._extract_failure_tag(_sample_with_tag("NOT_A_TAG")) is None
    assert evaluation._extract_failure_tag(None) is None


def test_aggregate_reports_failure_tag_counts():
    def _result(name, score, sample=None):
        return SimpleNamespace(name=name, score=score, sample=sample)

    def _item(results):
        return SimpleNamespace(results=results)

    items = [
        _item([_result("physics_correctness", 0.0, _sample_with_tag("WRONG_ANSWER"))]),
        _item([_result("physics_correctness", 0.0, _sample_with_tag("WRONG_ANSWER"))]),
        _item([_result("physics_correctness", 0.0, _sample_with_tag("CONCEPT_DRIFT"))]),
        # A pass contributes no tag.
        _item([_result("physics_correctness", 1.0, None)]),
        # A fail with an unparsable sample falls back to OTHER.
        _item([_result("physics_correctness", 0.0, {"output": []})]),
    ]
    summary, _ = evaluation._aggregate_question_scores(items)
    tags = summary["physics_correctness"]["failure_tag_counts"]
    assert tags["WRONG_ANSWER"] == 2
    assert tags["CONCEPT_DRIFT"] == 1
    assert tags["OTHER"] == 1
    # Passes never produce a tag.
    assert sum(tags.values()) == 4  # four failing items total
    # na_count/na_rate still present alongside the new field.
    assert summary["physics_correctness"]["na_count"] == 0


# ---------------------------------------------------------------------------
# GRADER_MODEL_OVERRIDE knob
# ---------------------------------------------------------------------------


def test_grader_model_override_env(monkeypatch):
    monkeypatch.setenv(GRADER_MODEL_OVERRIDE_ENV, "cross-family-judge")
    assert evaluation.rubric_model() == "cross-family-judge"
    monkeypatch.delenv(GRADER_MODEL_OVERRIDE_ENV, raising=False)
    # Falls back to agents.json evaluation.model (rubric_model default).
    assert evaluation.rubric_model() == evaluation._rubric_config()["model"]


def test_grader_model_override_blank_falls_back(monkeypatch):
    monkeypatch.setenv(GRADER_MODEL_OVERRIDE_ENV, "   ")
    assert evaluation.rubric_model() == evaluation._rubric_config()["model"]


# ---------------------------------------------------------------------------
# Report enrichment: base report carries seed + reasoning map
# ---------------------------------------------------------------------------


def test_base_report_carries_seed_and_reasoning_map():
    report = evaluation._build_base_report(
        status="skipped",
        grader_model="m",
        dataset_path=None,
        items_evaluated=0,
    )
    assert report["grader_seed"] == GRADER_SEED
    assert report["reasoning_effort_by_criterion"]["physics_correctness"] == "high"
