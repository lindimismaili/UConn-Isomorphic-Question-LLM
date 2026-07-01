"""Tests for the grader self-improvement meta-evaluator.

These build small synthetic ``evaluation_agents.jsonl`` + ``evaluation_report.json``
fixtures inline in tmp dirs (no mocking of the analysis) and assert:

- failure ``[TAG]`` mining from grader ``sample`` reasoning,
- proposal ranking + the failure-tag -> target-file mapping,
- the reward-hacking filter rejects a pass-rate-only proposal,
- the labeled-mode Cohen's kappa gate reports PASS/FAIL per criterion.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.src.tools import meta_evaluator as me  # noqa: E402
from backend.src.tools.meta_evaluator import (  # noqa: E402
    Proposal,
    analyze_eval_dirs,
    build_proposals,
    build_report,
    compute_kappa_gate,
    load_calibration_labels,
)

# --------------------------------------------------------------------------- #
# Fixture builders
# --------------------------------------------------------------------------- #


def _grader_sample(result_value: float, conclusion: str) -> dict:
    """A grader sample mirroring the on-disk shape parse_sample_steps expects.

    sample.output[i].content is a JSON string: {result, steps:[{conclusion,description}]}.
    """
    content = json.dumps(
        {
            "result": result_value,
            "steps": [
                {"conclusion": "intermediate reasoning", "description": "detail"},
                {"conclusion": conclusion, "description": "final justification"},
            ],
        }
    )
    return {"output": [{"role": "assistant", "content": content}]}


def _result(criterion: str, score: float, conclusion: str = "") -> dict:
    return {
        "name": f"{criterion}-abc123ef-0000-1111-2222-333344445555",
        "type": "score_model",
        "score": score,
        "passed": score >= 100.0,
        "sample": _grader_sample(score, conclusion),
    }


def _item(source_id: str, results: list[dict]) -> dict:
    return {
        "object": "eval.run.output_item",
        "datasource_item": {"source_id": source_id},
        "results": results,
    }


def _write_eval_dir(base: Path, items: list[dict], deterministic_checks: dict | None = None) -> Path:
    """Create an evaluation_outputs dir with a report + agents jsonl."""
    eval_dir = base / "evaluation_outputs"
    eval_dir.mkdir(parents=True, exist_ok=True)
    (eval_dir / "evaluation_agents.jsonl").write_text(
        "\n".join(json.dumps(i) for i in items), encoding="utf-8"
    )
    report = {
        "status": "complete",
        "passing_score_threshold": 1.0,
        "criterion_scores": {},
        "deterministic_checks": deterministic_checks or {},
    }
    (eval_dir / "evaluation_report.json").write_text(json.dumps(report), encoding="utf-8")
    return eval_dir


def _ns(**kwargs):
    import argparse

    defaults = {
        "eval_dir": [],
        "labels": None,
        "kappa_floor": me.DEFAULT_KAPPA_FLOOR,
        "created_at": "2026-06-30T00:00:00+00:00",
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# --------------------------------------------------------------------------- #
# Tag mining
# --------------------------------------------------------------------------- #


def test_tag_mining_from_sample(tmp_path: Path):
    items = [
        _item("q1", [_result("physics_correctness", 0.0, "[WRONG_ANSWER] the marked option is wrong.")]),
        _item("q2", [_result("physics_correctness", 0.0, "[WRONG_ANSWER] answer key mismatch.")]),
        _item("q3", [_result("physics_correctness", 100.0, "pass")]),
    ]
    eval_dir = _write_eval_dir(tmp_path, items)
    analysis = analyze_eval_dirs([eval_dir])
    crit = analysis["criteria"]["physics_correctness"]
    assert crit.items_scored == 3
    assert crit.items_failed == 2
    assert crit.items_passed == 1
    assert crit.failure_tag_counts["WRONG_ANSWER"] == 2
    assert crit.dominant_tag == "WRONG_ANSWER"
    assert set(crit.evidence_by_tag["WRONG_ANSWER"]) == {"q1", "q2"}


def test_untagged_failure_defaults_to_other(tmp_path: Path):
    items = [_item("q1", [_result("clerical_quality", 0.0, "no bracketed tag here")])]
    eval_dir = _write_eval_dir(tmp_path, items)
    analysis = analyze_eval_dirs([eval_dir])
    crit = analysis["criteria"]["clerical_quality"]
    assert crit.failure_tag_counts["OTHER"] == 1


def test_na_score_counted_separately(tmp_path: Path):
    items = [
        _item("q1", [_result("numerical_variation", -1.0, "n/a")]),
        _item("q2", [_result("numerical_variation", 0.0, "[NUMERIC_INCONSISTENT] inconsistent.")]),
    ]
    eval_dir = _write_eval_dir(tmp_path, items)
    analysis = analyze_eval_dirs([eval_dir])
    crit = analysis["criteria"]["numerical_variation"]
    assert crit.na_count == 1
    assert crit.items_failed == 1
    assert crit.items_scored == 1


# --------------------------------------------------------------------------- #
# Proposal building + tag -> file mapping + ranking
# --------------------------------------------------------------------------- #


def test_tag_maps_to_expected_target_files(tmp_path: Path):
    items = [
        _item("q1", [_result("distractor_structure_preservation", 0.0, "[DISTRACTOR_ROLE_LOST] lost a role.")]),
        _item("q2", [_result("distractor_structure_preservation", 0.0, "[DISTRACTOR_ROLE_LOST] role dropped.")]),
    ]
    eval_dir = _write_eval_dir(tmp_path, items)
    analysis = analyze_eval_dirs([eval_dir])
    proposals = build_proposals(analysis, None, me.DEFAULT_KAPPA_FLOOR)
    targets = {p.target_file for p in proposals}
    # DISTRACTOR_ROLE_LOST routes to generator.txt, verifier.txt, and the rubric criterion.
    assert "data/prompts/generator.txt" in targets
    assert "data/prompts/verifier.txt" in targets
    assert "data/rubric.json:distractor_structure_preservation" in targets
    # Every proposal cites a corroborating signal + a criterion.
    for p in proposals:
        assert p.corroborating_signal == "failure_tag"
        assert p.criterion == "distractor_structure_preservation"


def test_proposals_are_ranked_descending(tmp_path: Path):
    items = [
        _item("q1", [_result("physics_correctness", 0.0, "[WRONG_ANSWER] wrong.")]),
        _item("q2", [_result("physics_correctness", 0.0, "[WRONG_ANSWER] wrong.")]),
        _item("q3", [_result("physics_correctness", 0.0, "[WRONG_ANSWER] wrong.")]),
        _item("q4", [_result("scenario_variation", 0.0, "[OTHER] weak.")]),
    ]
    eval_dir = _write_eval_dir(tmp_path, items)
    analysis = analyze_eval_dirs([eval_dir])
    proposals = build_proposals(analysis, None, me.DEFAULT_KAPPA_FLOOR)
    scores = [p.rank_score for p in proposals]
    assert scores == sorted(scores, reverse=True)
    assert len(proposals) >= 1


def test_deterministic_finding_produces_proposal(tmp_path: Path):
    items = [_item("q1", [_result("physics_correctness", 100.0, "pass")])]
    checks = {
        "generation_contract": {
            "status": "failed",
            "errors": ["q3: missing distractor role 'sign error'"],
        }
    }
    eval_dir = _write_eval_dir(tmp_path, items, deterministic_checks=checks)
    analysis = analyze_eval_dirs([eval_dir])
    assert analysis["deterministic_findings"], "expected a deterministic finding"
    proposals = build_proposals(analysis, None, me.DEFAULT_KAPPA_FLOOR)
    det = [p for p in proposals if p.corroborating_signal == "deterministic_check"]
    assert det
    # deterministic proposals should outrank the (absent) tag proposals here.
    assert det[0].rank_score > 0


# --------------------------------------------------------------------------- #
# Reward-hacking guard
# --------------------------------------------------------------------------- #


def test_reward_hack_guard_rejects_pass_rate_only_proposal():
    # A proposal with an unrecognized signal + no evidence is pure pass-rate
    # inflation and must be filtered.
    bad = Proposal(
        target_file="data/rubric.json:physics_correctness",
        change_type="loosen_guidance",
        criterion="physics_correctness",
        rationale="raise the judge pass rate",
        evidence={},
        expected_metric_moved="physics_correctness pass_rate",
        suggested_text="be more lenient",
        confidence=0.9,
        corroborating_signal="pass_rate_only",
    )
    assert me._passes_reward_hack_guard(bad) is False

    # An otherwise-identical proposal but backed by a concrete failure tag passes.
    good = Proposal(
        target_file="data/prompts/generator.txt",
        change_type="add_correctness_clause",
        criterion="physics_correctness",
        rationale="wrong answers observed",
        evidence={"tag_count": 3, "failing_source_ids": ["q1", "q2", "q3"]},
        expected_metric_moved="physics_correctness pass_rate",
        suggested_text="verify the marked answer",
        confidence=0.8,
        corroborating_signal="failure_tag",
    )
    assert me._passes_reward_hack_guard(good) is True


def test_reward_hack_guard_rejects_empty_evidence_tag():
    # Signal says failure_tag but no concrete example/count -> rejected.
    bad = Proposal(
        target_file="data/prompts/generator.txt",
        change_type="add_correctness_clause",
        criterion="physics_correctness",
        rationale="claims a tag but no evidence",
        evidence={"tag_count": 0, "failing_source_ids": []},
        expected_metric_moved="physics_correctness pass_rate",
        suggested_text="x",
        confidence=0.8,
        corroborating_signal="failure_tag",
    )
    assert me._passes_reward_hack_guard(bad) is False


def test_build_proposals_filters_uncorroborated(tmp_path: Path):
    # All-passing eval dir with no deterministic findings -> zero proposals.
    items = [_item("q1", [_result("physics_correctness", 100.0, "pass")])]
    eval_dir = _write_eval_dir(tmp_path, items)
    analysis = analyze_eval_dirs([eval_dir])
    proposals = build_proposals(analysis, None, me.DEFAULT_KAPPA_FLOOR)
    assert proposals == []


# --------------------------------------------------------------------------- #
# Labeled mode — Cohen's kappa gate PASS/FAIL
# --------------------------------------------------------------------------- #


def _write_labels(path: Path, rows: list[dict], dataset_version: str = "v1") -> None:
    lines = []
    for row in rows:
        row = {**row, "dataset_version": dataset_version}
        lines.append(json.dumps(row))
    path.write_text("\n".join(lines), encoding="utf-8")


def test_kappa_gate_pass_and_fail(tmp_path: Path):
    labels = tmp_path / "cal.jsonl"
    rows = []
    # physics_correctness: perfect agreement -> PASS.
    for i in range(6):
        label = i % 2
        rows.append(
            {
                "source_id": f"pc{i}",
                "criterion": "physics_correctness",
                "human_or_reference_label": label,
                "grader_label": label,
            }
        )
    # scenario_variation: total disagreement -> kappa low -> FAIL.
    for i in range(6):
        rows.append(
            {
                "source_id": f"sv{i}",
                "criterion": "scenario_variation",
                "human_or_reference_label": i % 2,
                "grader_label": (i + 1) % 2,
            }
        )
    _write_labels(labels, rows)

    records, version = load_calibration_labels(labels)
    assert version == "v1"
    gate = compute_kappa_gate(records, me.DEFAULT_KAPPA_FLOOR)
    assert gate["physics_correctness"]["gate"] == "PASS"
    assert gate["physics_correctness"]["cohen_kappa"] == 1.0
    assert gate["scenario_variation"]["gate"] == "FAIL"


def test_kappa_gate_na_excluded_and_direction(tmp_path: Path):
    labels = tmp_path / "cal.jsonl"
    rows = [
        # N/A on either side excluded from kappa.
        {"source_id": "a", "criterion": "numerical_variation",
         "human_or_reference_label": "na", "grader_label": 1},
        {"source_id": "b", "criterion": "numerical_variation",
         "human_or_reference_label": 1, "grader_label": 1},
        # grader inflates: ref=0 grader=1
        {"source_id": "c", "criterion": "numerical_variation",
         "human_or_reference_label": 0, "grader_label": 1},
    ]
    _write_labels(labels, rows)
    records, _ = load_calibration_labels(labels)
    gate = compute_kappa_gate(records, me.DEFAULT_KAPPA_FLOOR)
    row = gate["numerical_variation"]
    assert row["n_na_excluded"] == 1
    assert row["n_compared"] == 2
    assert row["grader_inflated_count"] == 1


def test_kappa_failing_criterion_yields_calibration_proposal(tmp_path: Path):
    # A criterion failing the kappa gate should produce a rubric calibration
    # proposal targeting the rubric criterion (grader problem, not generator).
    labels = tmp_path / "cal.jsonl"
    rows = []
    for i in range(6):
        rows.append(
            {
                "source_id": f"sv{i}",
                "criterion": "scenario_variation",
                "human_or_reference_label": i % 2,
                "grader_label": (i + 1) % 2,
            }
        )
    _write_labels(labels, rows)
    records, version = load_calibration_labels(labels)
    gate = compute_kappa_gate(records, me.DEFAULT_KAPPA_FLOOR)

    # Empty eval analysis + a failing gate -> a kappa_gap proposal.
    empty_analysis = {"criteria": {}, "deterministic_findings": [], "dirs_read": [], "threshold": 1.0}
    proposals = build_proposals(empty_analysis, gate, me.DEFAULT_KAPPA_FLOOR)
    kappa_props = [p for p in proposals if p.corroborating_signal == "kappa_gap"]
    assert kappa_props
    assert kappa_props[0].target_file == "data/rubric.json:scenario_variation"
    # kappa_gap outranks a hypothetical tag proposal (highest signal weight).
    assert kappa_props[0].rank_score > 0

    report = build_report(
        empty_analysis, proposals, gate,
        kappa_floor=me.DEFAULT_KAPPA_FLOOR, dataset_version=version,
        created_at="2026-06-30T00:00:00+00:00",
    )
    assert report["kappa_gate_summary"]["overall_gate"] == "FAIL"
    assert "scenario_variation" in report["kappa_gate_summary"]["criteria_failing_gate"]
    assert report["dataset_version"] == "v1"


# --------------------------------------------------------------------------- #
# End-to-end run (report shape) + multi-dir ingest
# --------------------------------------------------------------------------- #


def test_run_meta_evaluation_end_to_end(tmp_path: Path):
    items_a = [_item("q1", [_result("physics_correctness", 0.0, "[WRONG_ANSWER] wrong.")])]
    items_b = [_item("q2", [_result("physics_correctness", 0.0, "[WRONG_ANSWER] wrong again.")])]
    dir_a = _write_eval_dir(tmp_path / "runA", items_a)
    dir_b = _write_eval_dir(tmp_path / "runB", items_b)

    args = _ns(eval_dir=[dir_a, dir_b])
    report = me.run_meta_evaluation(args)

    # Both dirs aggregated into one criterion.
    crit = report["criterion_analysis"]["physics_correctness"]
    assert crit["items_failed"] == 2
    assert crit["failure_tag_counts"]["WRONG_ANSWER"] == 2
    assert report["proposal_count"] >= 1
    assert report["dataset_version"] == "unlabeled"
    # Markdown renders without error and mentions PROPOSE-ONLY.
    md = me.render_markdown(report)
    assert "PROPOSE-ONLY" in md
    assert "physics_correctness" in md


def test_report_scores_fallback_without_items(tmp_path: Path):
    # No agents jsonl; only a report with criterion_scores + failure_tag_counts.
    eval_dir = tmp_path / "evaluation_outputs"
    eval_dir.mkdir(parents=True)
    report = {
        "passing_score_threshold": 1.0,
        "criterion_scores": {
            "clerical_quality": {
                "items_scored": 4,
                "items_passed": 1,
                "na_count": 0,
                "failure_tag_counts": {"CLERICAL": 3},
            }
        },
        "deterministic_checks": {},
    }
    (eval_dir / "evaluation_report.json").write_text(json.dumps(report), encoding="utf-8")
    analysis = analyze_eval_dirs([eval_dir])
    crit = analysis["criteria"]["clerical_quality"]
    assert crit.items_failed == 3
    assert crit.failure_tag_counts["CLERICAL"] == 3
    proposals = build_proposals(analysis, None, me.DEFAULT_KAPPA_FLOOR)
    assert any(p.target_file == "data/prompts/generator.txt" for p in proposals)
