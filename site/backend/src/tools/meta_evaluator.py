"""Grader self-improvement "meta-evaluator" for the isomorphic-question generator.

This offline tool implements the Analyze -> Improve half of the eval-driven
"flywheel" (see research notes I-evals-docs.md / B-eval.md) WITHOUT relying on
human labels. It ingests one or more evaluation output directories, mines each
failing item's ``[TAG]`` failure code and grader rationale (via
``evaluation.parse_sample_steps``), folds in the deterministic pre-judge checks,
and emits **ranked, propose-only** recommendations that map dominant failure
tags to specific hand-curated files (prompts / rubric).

Design constraints (encoded here):

- **PROPOSE-ONLY.** It never edits ``data/prompts/*.txt`` or ``data/rubric.json``;
  those are hand-curated and prompt-versioned via ``scripts/prompt_versions.py``.
  It only writes a report (``meta_evaluation_report.json``) + a ledger entry.
- **Reward-hacking guard.** Every proposal must cite an evidence signal (a
  deterministic-check failure, a grader self-tag with concrete examples, or a
  labeled-mode kappa/agreement gap). Reports explicitly distinguish grader
  self-report from independently corroborated evidence. A proposal whose only
  expected effect is raising the judge's pass rate is filtered out. See
  ``_passes_reward_hack_guard``.
- **Optional labeled mode.** When a calibration dataset graded by the latest
  grader (or professor labels) is supplied via ``--labels``, it computes per
  criterion Cohen's kappa + inflate/miss confusion by REUSING
  ``tools.judge_agreement`` and applies a kappa gate (``--kappa-floor``).
- **Experiment ledger.** Each run appends one JSONL record to
  ``backend/runtime/meta_eval_ledger.jsonl`` keyed on
  ``(prompt_version, rubric_version, dataset_version, created_at)``.

No module-load side effects: all filesystem reads and ``datetime.now`` calls
happen inside functions, and heavy accessors are lazy.

CLI (run from ``site/``)::

    .venv/bin/python -m backend.src.tools.meta_evaluator --eval-dir <outputs/<ts>/evaluation_outputs>
    .venv/bin/python -m backend.src.tools.meta_evaluator --eval-dir A --eval-dir B --json-out report.json
    .venv/bin/python -m backend.src.tools.meta_evaluator --eval-dir <dir> --labels data/calibration/gold.jsonl
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from backend.src.evaluation import (
    EVALUATION_ITEMS_FILENAME,
    EVALUATION_REPORT_FILENAME,
    FAILURE_TAGS,
    parse_sample_steps,
)
from backend.src.paths import PROMPTS_DIR, RUBRIC_PATH, RUNTIME_DIR
from backend.src.tools.judge_agreement import cohen_kappa

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

META_EVAL_REPORT_FILENAME = "meta_evaluation_report.json"
LEDGER_FILENAME = "meta_eval_ledger.jsonl"

# Default kappa floor for the labeled-mode gate. 0.4 is the Landis-Koch "fair"
# ceiling; below it the grader is not trustworthy for that criterion.
DEFAULT_KAPPA_FLOOR = 0.4
DEFAULT_MIN_KAPPA_SAMPLES = 5


class MetaEvaluationInputError(ValueError):
    """Raised when evaluation or calibration input is missing or malformed."""

# Failure tag -> target files mapping. This is the documented "attribution"
# table: a dominant failure tag on a criterion routes a proposal to the
# hand-curated file(s) most likely to move the metric. Prompts/rubric are the
# only touch points (propose-only); we never target engine/schema code here.
#
# Each entry lists ordered (target_file, change_type) pairs. change_type is a
# short verb the human reviewer uses to know what kind of edit is proposed.
TAG_TARGET_MAP: dict[str, list[tuple[str, str]]] = {
    "CONCEPT_DRIFT": [
        ("data/prompts/generator.txt", "add_preservation_clause"),
        ("data/rubric.json:preserve_physical_content", "tighten_guidance"),
    ],
    "MISSING_CONVERSION": [
        ("data/prompts/generator.txt", "add_unit_conversion_clause"),
        ("data/rubric.json:preserve_physical_content", "tighten_guidance"),
    ],
    "NUMERIC_INCONSISTENT": [
        ("data/prompts/generator.txt", "add_recompute_clause"),
        ("data/prompts/verifier.txt", "add_numeric_check_clause"),
        ("data/rubric.json:numerical_variation", "tighten_guidance"),
    ],
    "WRONG_ANSWER": [
        ("data/prompts/verifier.txt", "add_answer_verification_clause"),
        ("data/prompts/generator.txt", "add_correctness_clause"),
        ("data/rubric.json:physics_correctness", "tighten_guidance"),
    ],
    "DISTRACTOR_ROLE_LOST": [
        ("data/prompts/generator.txt", "add_distractor_role_clause"),
        ("data/prompts/verifier.txt", "add_distractor_logic_clause"),
        ("data/rubric.json:distractor_structure_preservation", "tighten_guidance"),
    ],
    "GENERIC_DISTRACTOR": [
        ("data/prompts/generator.txt", "add_specific_distractor_clause"),
        ("data/prompts/verifier.txt", "add_distractor_logic_clause"),
        ("data/rubric.json:distractor_structure_preservation", "tighten_guidance"),
    ],
    "CLERICAL": [
        ("data/prompts/generator.txt", "add_style_clause"),
    ],
    "UNDERSPECIFIED": [
        ("data/prompts/generator.txt", "add_solvability_clause"),
        ("data/rubric.json:solvable_and_specified", "tighten_guidance"),
    ],
    "OTHER": [
        ("data/prompts/generator.txt", "review_manually"),
    ],
}

# Per-tag one-line rationale + a concrete, minimal suggested edit the human can
# adapt. Kept generic (no per-run numbers) so the proposal reads sensibly.
TAG_SUGGESTION: dict[str, dict[str, str]] = {
    "CONCEPT_DRIFT": {
        "rationale": "Generated questions drift to a different physics concept/law than the source.",
        "suggested_text": (
            "Preserve the SAME governing law, model, and reasoning path as the source; only the "
            "story, names, and numbers may change. Do not swap in a different concept or topic."
        ),
        "expected_metric_moved": "preserve_physical_content pass_rate",
    },
    "MISSING_CONVERSION": {
        "rationale": "The source's required unit-conversion burden is dropped in the generated item.",
        "suggested_text": (
            "When the source requires a unit conversion (stem units differ from answer-choice units), "
            "keep an equivalent conversion step; never state the stem directly in the answer units."
        ),
        "expected_metric_moved": "preserve_physical_content pass_rate",
    },
    "NUMERIC_INCONSISTENT": {
        "rationale": "New numbers are introduced without recomputing every dependent value consistently.",
        "suggested_text": (
            "After changing source values, recompute the final answer, every distractor, and all "
            "derived quantities from the new numbers; verify dimensional consistency."
        ),
        "expected_metric_moved": "numerical_variation pass_rate",
    },
    "WRONG_ANSWER": {
        "rationale": "The marked-correct option is not actually correct for the generated question.",
        "suggested_text": (
            "Independently solve the generated question and confirm the marked-correct option is the "
            "unique correct answer before emitting it; never present a misconception as correct."
        ),
        "expected_metric_moved": "physics_correctness pass_rate",
    },
    "DISTRACTOR_ROLE_LOST": {
        "rationale": "A source distractor role is missing or altered in the generated option set.",
        "suggested_text": (
            "Reproduce every source distractor_logic role one-to-one; each generated distractor must "
            "declare the same distractor_logic label and a value that follows from that error."
        ),
        "expected_metric_moved": "distractor_structure_preservation pass_rate",
    },
    "GENERIC_DISTRACTOR": {
        "rationale": "A specific-misconception distractor was replaced with a generic plausible number.",
        "suggested_text": (
            "Do not substitute a generic plausible wrong number for a role that encoded a specific "
            "misconception; only 'generic_plausible_value' source roles may be generic."
        ),
        "expected_metric_moved": "distractor_structure_preservation pass_rate",
    },
    "CLERICAL": {
        "rationale": "Typos, broken math notation, duplicate options, or ambiguous wording appear.",
        "suggested_text": (
            "Emit clean spelling/grammar and intact math notation; no duplicate or missing options "
            "and no ambiguous wording."
        ),
        "expected_metric_moved": "clerical_quality pass_rate",
    },
    "UNDERSPECIFIED": {
        "rationale": "The generated question is underspecified or not solvable exactly as written.",
        "suggested_text": (
            "Give enough information for exactly one intended answer; keep any conversion step the "
            "source implied and state all assumptions needed to force the answer."
        ),
        "expected_metric_moved": "solvable_and_specified pass_rate",
    },
    "OTHER": {
        "rationale": "Failures did not map to a specific taxonomy tag; manual review recommended.",
        "suggested_text": (
            "Read the cited grader rationales and localize the shared root cause before editing."
        ),
        "expected_metric_moved": "overall pass_rate",
    },
}


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #


@dataclass
class CriterionAnalysis:
    """Per-criterion aggregate mined across all ingested eval dirs."""

    name: str
    items_scored: int = 0
    items_passed: int = 0
    items_failed: int = 0
    na_count: int = 0
    failure_tag_counts: Counter = field(default_factory=Counter)
    # source_ids of failing items per tag, for concrete evidence in proposals.
    evidence_by_tag: dict[str, list[str]] = field(default_factory=dict)

    @property
    def fail_rate(self) -> Optional[float]:
        total = self.items_passed + self.items_failed
        if total == 0:
            return None
        return round(self.items_failed / total, 4)

    @property
    def na_rate(self) -> Optional[float]:
        total = self.items_passed + self.items_failed + self.na_count
        if total == 0:
            return None
        return round(self.na_count / total, 4)

    @property
    def dominant_tag(self) -> Optional[str]:
        if not self.failure_tag_counts:
            return None
        return self.failure_tag_counts.most_common(1)[0][0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "items_scored": self.items_scored,
            "items_passed": self.items_passed,
            "items_failed": self.items_failed,
            "na_count": self.na_count,
            "fail_rate": self.fail_rate,
            "na_rate": self.na_rate,
            "failure_tag_counts": dict(self.failure_tag_counts),
            "dominant_tag": self.dominant_tag,
            "evidence_by_tag": {k: v[:8] for k, v in self.evidence_by_tag.items()},
        }


@dataclass
class Proposal:
    """A single propose-only recommendation."""

    target_file: str
    change_type: str
    criterion: str
    rationale: str
    evidence: dict[str, Any]
    expected_metric_moved: str
    suggested_text: str
    confidence: float
    # The evidence signal that lets this proposal past the reward-hacking guard.
    # ``failure_tag`` is the grader's own diagnosis; ``deterministic_check`` and
    # ``kappa_gap`` are independently corroborated.
    corroborating_signal: str
    rank_score: float = 0.0

    @property
    def evidence_provenance(self) -> str:
        """Describe whether evidence comes from the grader or an independent source."""
        return {
            "failure_tag": "grader_self_report",
            "deterministic_check": "independent_pipeline_check",
            "kappa_gap": "independent_reference_labels",
        }.get(self.corroborating_signal, "unknown")

    @property
    def independently_corroborated(self) -> bool:
        return self.corroborating_signal in {"deterministic_check", "kappa_gap"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_file": self.target_file,
            "change_type": self.change_type,
            "criterion": self.criterion,
            "rationale": self.rationale,
            "evidence": self.evidence,
            "expected_metric_moved": self.expected_metric_moved,
            "suggested_text": self.suggested_text,
            "confidence": round(self.confidence, 4),
            "corroborating_signal": self.corroborating_signal,
            "evidence_provenance": self.evidence_provenance,
            "independently_corroborated": self.independently_corroborated,
            "rank_score": round(self.rank_score, 4),
        }


# --------------------------------------------------------------------------- #
# Ingest
# --------------------------------------------------------------------------- #


def _resolve_eval_report_and_items(eval_dir: Path) -> tuple[Path, Path]:
    """Return (report_path, items_path) for one evaluation_outputs dir.

    Accepts either the ``evaluation_outputs`` dir directly, or a run dir that
    contains it (``<outputs>/<ts>`` or ``<outputs>/<ts>/evaluation_outputs``).
    """
    if not eval_dir.exists():
        raise MetaEvaluationInputError(f"evaluation path does not exist: {eval_dir}")
    if not eval_dir.is_dir():
        raise MetaEvaluationInputError(f"evaluation path is not a directory: {eval_dir}")

    candidates = [eval_dir, eval_dir / "evaluation_outputs"]
    for base in candidates:
        report = base / EVALUATION_REPORT_FILENAME
        if report.is_file():
            return report, base / EVALUATION_ITEMS_FILENAME
    expected = " or ".join(str(base / EVALUATION_REPORT_FILENAME) for base in candidates)
    raise MetaEvaluationInputError(f"missing evaluation report; expected {expected}")


def _load_json(path: Path, *, description: str) -> dict[str, Any]:
    """Load one required JSON object and preserve parse context in errors."""
    if not path.is_file():
        raise MetaEvaluationInputError(f"missing {description}: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MetaEvaluationInputError(
            f"invalid {description} JSON at {path}:{exc.lineno}:{exc.colno}: {exc.msg}"
        ) from exc
    except OSError as exc:
        raise MetaEvaluationInputError(f"could not read {description} {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise MetaEvaluationInputError(f"{description} must contain a JSON object: {path}")
    return data


def _load_jsonl(
    path: Path,
    *,
    description: str,
    required: bool = False,
) -> list[dict[str, Any]]:
    """Load JSONL objects, failing on malformed records instead of dropping them."""
    if not path.exists():
        if required:
            raise MetaEvaluationInputError(f"missing {description}: {path}")
        return []
    if not path.is_file():
        raise MetaEvaluationInputError(f"{description} is not a file: {path}")
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise MetaEvaluationInputError(f"could not read {description} {path}: {exc}") from exc
    for line_number, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MetaEvaluationInputError(
                f"invalid {description} JSON at {path}:{line_number}:{exc.colno}: {exc.msg}"
            ) from exc
        if not isinstance(obj, dict):
            raise MetaEvaluationInputError(
                f"{description} record at {path}:{line_number} must be a JSON object"
            )
        records.append(obj)
    return records


def _base_criterion_name(result_name: str) -> str:
    """Strip the UUID suffix from an eval result name (``crit-<uuid>`` -> ``crit``).

    Backend result names carry a UUID suffix. Matching by the longest known
    rubric prefix would need the rubric loaded; here we split on the first
    ``-<hex-ish>`` boundary conservatively: known criterion names contain no
    hyphen, so splitting on the first hyphen recovers the base name.
    """
    name = str(result_name or "")
    if "-" in name:
        return name.split("-", 1)[0]
    return name


def _is_fail_score(score: Any, threshold: float) -> Optional[bool]:
    """Classify a numeric result score: True=fail, False=pass, None=N/A/absent.

    Scores may be on a 0/1 scale or a 0-100 scale depending on the run; both are
    handled because we compare against the run's own ``passing_score_threshold``
    for the 0/1 scale and treat any positive < threshold*100 heuristically. The
    contract is: negative => N/A; >= pass threshold => pass; else fail.
    """
    if not isinstance(score, (int, float)):
        return None
    value = float(score)
    if value < 0:
        return None  # N/A (-1)
    # Normalize a percentage scale (0-100) down to 0-1 for the threshold compare.
    normalized = value / 100.0 if value > 1.0 else value
    return normalized < float(threshold)


def analyze_eval_dirs(eval_dirs: Iterable[Path]) -> dict[str, Any]:
    """Ingest + analyze one or more evaluation output dirs.

    Returns a dict with ``criteria`` (name -> CriterionAnalysis), aggregated
    ``deterministic_checks`` findings, and bookkeeping (dirs read, threshold).
    """
    requested_dirs = [Path(eval_dir) for eval_dir in eval_dirs]
    if not requested_dirs:
        raise MetaEvaluationInputError("at least one --eval-dir is required")

    criteria: dict[str, CriterionAnalysis] = {}
    deterministic_findings: list[dict[str, Any]] = []
    dirs_read: list[str] = []
    threshold: Optional[float] = None
    reports_seen: set[Path] = set()

    for eval_dir in requested_dirs:
        report_path, items_path = _resolve_eval_report_and_items(eval_dir)
        resolved_report = report_path.resolve()
        if resolved_report in reports_seen:
            raise MetaEvaluationInputError(f"duplicate evaluation directory: {report_path.parent}")
        reports_seen.add(resolved_report)

        report = _load_json(report_path, description="evaluation report")
        report_status = str(report.get("status", "")).strip().lower()
        if report_status and report_status not in {"complete", "completed"}:
            raise MetaEvaluationInputError(
                f"evaluation report is not complete ({report_status!r}): {report_path}"
            )
        dirs_read.append(str(report_path.parent))
        raw_threshold = report.get("passing_score_threshold", 1.0)
        try:
            report_threshold = float(raw_threshold)
        except (TypeError, ValueError) as exc:
            raise MetaEvaluationInputError(
                f"invalid passing_score_threshold in {report_path}: {raw_threshold!r}"
            ) from exc
        if not math.isfinite(report_threshold) or not 0.0 <= report_threshold <= 100.0:
            raise MetaEvaluationInputError(
                f"passing_score_threshold must be between 0 and 100 in {report_path}"
            )
        if threshold is None:
            threshold = report_threshold
        elif not math.isclose(threshold, report_threshold):
            raise MetaEvaluationInputError(
                "evaluation reports use different passing_score_threshold values: "
                f"{threshold} and {report_threshold}"
            )

        # Deterministic checks: fold in any non-passing signal as independent
        # evidence for reward-hack-safe proposals.
        before_findings = len(deterministic_findings)
        deterministic_findings.extend(_extract_deterministic_findings(report, report_path))
        before_scores = sum(
            entry.items_scored + entry.na_count for entry in criteria.values()
        )

        # Prefer mining raw grader items (they carry the sample rationale). Fall
        # back to the report's precomputed criterion_scores when items absent.
        items = _load_jsonl(items_path, description="evaluation grader items")
        if items:
            _mine_items(items, criteria, report_threshold)
        else:
            _mine_report_scores(report, criteria)

        after_scores = sum(
            entry.items_scored + entry.na_count for entry in criteria.values()
        )
        if after_scores == before_scores and len(deterministic_findings) == before_findings:
            raise MetaEvaluationInputError(
                f"evaluation directory contains no grader evidence or deterministic findings: "
                f"{report_path.parent}"
            )

    return {
        "criteria": criteria,
        "deterministic_findings": deterministic_findings,
        "dirs_read": dirs_read,
        "threshold": threshold if threshold is not None else 1.0,
    }


def _get_criterion(criteria: dict[str, CriterionAnalysis], name: str) -> CriterionAnalysis:
    entry = criteria.get(name)
    if entry is None:
        entry = CriterionAnalysis(name=name)
        criteria[name] = entry
    return entry


def _mine_items(
    items: list[dict[str, Any]],
    criteria: dict[str, CriterionAnalysis],
    threshold: float,
) -> None:
    """Mine per-item grader results: pass/fail counts + failure tags + evidence."""
    for item_index, item in enumerate(items, start=1):
        results = item.get("results")
        if not isinstance(results, list):
            raise MetaEvaluationInputError(
                f"evaluation grader item {item_index} has no valid results array"
            )
        datasource = item.get("datasource_item") or item.get("item") or {}
        source_id = str(datasource.get("source_id", "")) if isinstance(datasource, dict) else ""
        for result_index, result in enumerate(results, start=1):
            if not isinstance(result, dict):
                raise MetaEvaluationInputError(
                    f"evaluation grader item {item_index} result {result_index} must be an object"
                )
            name = _base_criterion_name(result.get("name", ""))
            if not name:
                raise MetaEvaluationInputError(
                    f"evaluation grader item {item_index} result {result_index} has no criterion name"
                )
            score = result.get("score")
            if not isinstance(score, (int, float)) or isinstance(score, bool):
                raise MetaEvaluationInputError(
                    f"evaluation grader item {item_index} result {result_index} has invalid score "
                    f"{score!r}"
                )
            entry = _get_criterion(criteria, name)
            fail = _is_fail_score(score, threshold)
            if fail is None:
                entry.na_count += 1
                continue
            entry.items_scored += 1
            if fail:
                entry.items_failed += 1
                tag = _tag_from_result(result)
                entry.failure_tag_counts[tag] += 1
                if source_id:
                    entry.evidence_by_tag.setdefault(tag, []).append(source_id)
            else:
                entry.items_passed += 1


def _tag_from_result(result: dict[str, Any]) -> str:
    """Extract the ``[TAG]`` failure code from a grader result's sample.

    Reuses ``parse_sample_steps`` (imported from evaluation.py) to read the
    grader rationale, then scans for the first recognized bracketed tag.
    """
    valid = set(FAILURE_TAGS)
    steps = parse_sample_steps(result.get("sample"))
    # Prefer the final step's conclusion (where the grader is told to place the
    # tag), then fall back to any step text.
    ordered: list[str] = []
    for step in reversed(steps):
        ordered.append(str(step.get("conclusion", "")))
        ordered.append(str(step.get("description", "")))
    for text in ordered:
        start = 0
        while "[" in text[start:]:
            open_idx = text.index("[", start)
            close_idx = text.find("]", open_idx)
            if close_idx == -1:
                break
            candidate = text[open_idx + 1 : close_idx]
            if candidate in valid:
                return candidate
            start = close_idx + 1
    return "OTHER"


def _mine_report_scores(report: dict[str, Any], criteria: dict[str, CriterionAnalysis]) -> None:
    """Fallback: use the report's precomputed criterion_scores block.

    Used when the raw ``evaluation_agents.jsonl`` is absent. This still gives
    fail counts + failure_tag_counts (the report carries them since the tag
    taxonomy landed), just without per-item source-id evidence.
    """
    scores = report.get("criterion_scores")
    if scores is not None and not isinstance(scores, dict):
        raise MetaEvaluationInputError("evaluation report criterion_scores must be an object")
    if not isinstance(scores, dict):
        return
    for name, block in scores.items():
        if not str(name).strip():
            raise MetaEvaluationInputError("evaluation report contains an empty criterion name")
        if not isinstance(block, dict):
            raise MetaEvaluationInputError(
                f"evaluation report criterion_scores[{name!r}] must be an object"
            )
        entry = _get_criterion(criteria, name)
        try:
            items_scored = int(block.get("items_scored", 0) or 0)
            items_passed = int(block.get("items_passed", 0) or 0)
            na_count = int(block.get("na_count", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise MetaEvaluationInputError(
                f"evaluation report criterion_scores[{name!r}] has invalid counts"
            ) from exc
        if min(items_scored, items_passed, na_count) < 0 or items_passed > items_scored:
            raise MetaEvaluationInputError(
                f"evaluation report criterion_scores[{name!r}] has inconsistent counts"
            )
        entry.items_scored += items_scored
        entry.items_passed += items_passed
        entry.items_failed += max(items_scored - items_passed, 0)
        entry.na_count += na_count
        tag_counts = block.get("failure_tag_counts")
        if tag_counts is not None and not isinstance(tag_counts, dict):
            raise MetaEvaluationInputError(
                f"evaluation report criterion_scores[{name!r}].failure_tag_counts "
                "must be an object"
            )
        if isinstance(tag_counts, dict):
            for tag, count in tag_counts.items():
                try:
                    parsed_count = int(count)
                except (ValueError, TypeError):
                    raise MetaEvaluationInputError(
                        f"evaluation report criterion_scores[{name!r}] has invalid "
                        f"failure tag count for {tag!r}"
                    ) from None
                if parsed_count < 0:
                    raise MetaEvaluationInputError(
                        f"evaluation report criterion_scores[{name!r}] has a negative "
                        f"failure tag count for {tag!r}"
                    )
                entry.failure_tag_counts[str(tag)] += parsed_count


def _extract_deterministic_findings(report: dict[str, Any], report_path: Path) -> list[dict[str, Any]]:
    """Pull non-passing deterministic-check signals from a report."""
    findings: list[dict[str, Any]] = []
    checks = report.get("deterministic_checks")
    if not isinstance(checks, dict):
        return findings
    label = str(report_path.parent)

    contract = checks.get("generation_contract")
    if isinstance(contract, dict):
        errors = contract.get("errors") or []
        if contract.get("status") == "failed" or errors:
            findings.append(
                {
                    "check": "generation_contract",
                    "source": label,
                    "status": contract.get("status", "failed"),
                    "errors": list(errors)[:12],
                }
            )

    figure_skips = checks.get("figure_skips")
    if isinstance(figure_skips, dict) and figure_skips.get("status") == "failed":
        findings.append(
            {
                "check": "figure_skips",
                "source": label,
                "status": "failed",
                "errors": list(figure_skips.get("errors") or [])[:12],
            }
        )

    coverage = checks.get("coverage_mismatch")
    if isinstance(coverage, dict):
        findings.append(
            {
                "check": "coverage_mismatch",
                "source": label,
                "status": "failed",
                "detail": coverage,
            }
        )
    return findings


# --------------------------------------------------------------------------- #
# Labeled mode (Cohen's kappa gate) — reuses judge_agreement helpers
# --------------------------------------------------------------------------- #


def load_calibration_labels(path: Path) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Load a versioned calibration JSONL.

    Each record: ``{source_id, criterion, human_or_reference_label, grader_label,
    dataset_version}``. Labels are 0/1/'na' (or -1 for N/A). Returns
    (records, dataset_version).
    """
    path = Path(path)
    records = _load_jsonl(path, description="calibration labels", required=True)
    if not records:
        raise MetaEvaluationInputError(f"calibration labels file is empty: {path}")

    dataset_versions: set[str] = set()
    seen_keys: set[tuple[str, str]] = set()
    required_fields = {
        "source_id",
        "criterion",
        "human_or_reference_label",
        "grader_label",
        "dataset_version",
    }
    for index, obj in enumerate(records, start=1):
        missing = sorted(required_fields - obj.keys())
        if missing:
            raise MetaEvaluationInputError(
                f"calibration record {index} in {path} is missing: {', '.join(missing)}"
            )
        source_id = str(obj.get("source_id", "")).strip()
        criterion = str(obj.get("criterion", "")).strip()
        version = obj.get("dataset_version")
        if not source_id:
            raise MetaEvaluationInputError(
                f"calibration record {index} in {path} has an empty source_id"
            )
        if not criterion:
            raise MetaEvaluationInputError(
                f"calibration record {index} in {path} has an empty criterion"
            )
        if not isinstance(version, str) or not version.strip():
            raise MetaEvaluationInputError(
                f"calibration record {index} in {path} has an invalid dataset_version"
            )
        _norm_label(
            obj.get("human_or_reference_label"),
            context=f"calibration record {index} human_or_reference_label",
        )
        _norm_label(
            obj.get("grader_label"),
            context=f"calibration record {index} grader_label",
        )
        key = (source_id, criterion)
        if key in seen_keys:
            raise MetaEvaluationInputError(
                f"duplicate calibration pair in {path}: source_id={source_id!r}, "
                f"criterion={criterion!r}"
            )
        seen_keys.add(key)
        dataset_versions.add(version.strip())

    if len(dataset_versions) != 1:
        versions = ", ".join(sorted(dataset_versions))
        raise MetaEvaluationInputError(
            f"calibration labels mix dataset_version values: {versions}"
        )
    return records, next(iter(dataset_versions))


def _norm_label(value: Any, *, context: str = "calibration label") -> Any:
    """Normalize a calibration label to 0, 1, or ``"na"``; reject ambiguity."""
    if value is None:
        return "na"
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise MetaEvaluationInputError(f"{context} must be 0, 1, or N/A; got {value!r}")
        if float(value) == -1.0:
            return "na"
        if float(value) == 0.0:
            return 0
        if float(value) == 1.0:
            return 1
        raise MetaEvaluationInputError(f"{context} must be 0, 1, or N/A; got {value!r}")
    text = str(value).strip().lower()
    if text in {"", "na", "n/a", "-1"}:
        return "na"
    if text in {"1", "pass", "passed", "true", "yes"}:
        return 1
    if text in {"0", "fail", "failed", "false", "no"}:
        return 0
    raise MetaEvaluationInputError(f"{context} must be 0, 1, or N/A; got {value!r}")


def _validate_kappa_settings(kappa_floor: float, minimum_samples: int) -> None:
    if (
        isinstance(kappa_floor, bool)
        or not isinstance(kappa_floor, (int, float))
        or not math.isfinite(float(kappa_floor))
        or not 0.0 <= float(kappa_floor) <= 1.0
    ):
        raise MetaEvaluationInputError(
            f"kappa floor must be a finite number between 0 and 1; got {kappa_floor!r}"
        )
    if isinstance(minimum_samples, bool) or not isinstance(minimum_samples, int) or minimum_samples < 2:
        raise MetaEvaluationInputError(
            f"minimum kappa samples must be an integer of at least 2; got {minimum_samples!r}"
        )


def compute_kappa_gate(
    records: list[dict[str, Any]],
    kappa_floor: float,
    minimum_samples: int = DEFAULT_MIN_KAPPA_SAMPLES,
) -> dict[str, dict[str, Any]]:
    """Per-criterion Cohen's kappa + inflate/miss confusion + PASS/FAIL gate.

    Reuses ``judge_agreement.cohen_kappa``. Reference label vs grader label; N/A
    on either side excludes the pair (mirrors judge_agreement).
    """
    _validate_kappa_settings(kappa_floor, minimum_samples)
    if not records:
        raise MetaEvaluationInputError("calibration dataset contains no records")

    by_criterion: dict[str, list[tuple[int, int]]] = {}
    criteria_seen: set[str] = set()
    inflate: Counter = Counter()  # ref=0, grader=1 (grader inflates)
    miss: Counter = Counter()  # ref=1, grader=0 (grader misses)
    na_excluded: Counter = Counter()

    for index, rec in enumerate(records, start=1):
        if not isinstance(rec, dict):
            raise MetaEvaluationInputError(
                f"calibration record {index} must be a JSON object"
            )
        criterion = str(rec.get("criterion", "")).strip()
        if not criterion:
            raise MetaEvaluationInputError(
                f"calibration record {index} has an empty criterion"
            )
        criteria_seen.add(criterion)
        ref = _norm_label(
            rec.get("human_or_reference_label"),
            context=f"calibration record {index} human_or_reference_label",
        )
        grader = _norm_label(
            rec.get("grader_label"),
            context=f"calibration record {index} grader_label",
        )
        if ref == "na" or grader == "na":
            na_excluded[criterion] += 1
            continue
        by_criterion.setdefault(criterion, []).append((int(ref), int(grader)))
        if ref == 0 and grader == 1:
            inflate[criterion] += 1
        elif ref == 1 and grader == 0:
            miss[criterion] += 1

    gate: dict[str, dict[str, Any]] = {}
    for criterion in sorted(criteria_seen):
        pairs = by_criterion.get(criterion, [])
        kappa = cohen_kappa(pairs)
        agree = sum(1 for a, b in pairs if a == b)
        raw_agreement = round(agree / len(pairs), 4) if pairs else None
        sufficient_evidence = len(pairs) >= minimum_samples
        passed = sufficient_evidence and kappa is not None and kappa >= kappa_floor
        gate[criterion] = {
            "n_compared": len(pairs),
            "n_na_excluded": na_excluded.get(criterion, 0),
            "cohen_kappa": kappa,
            "raw_agreement": raw_agreement,
            "grader_inflated_count": inflate.get(criterion, 0),
            "grader_missed_count": miss.get(criterion, 0),
            "kappa_floor": kappa_floor,
            "minimum_samples": minimum_samples,
            "evidence_status": "sufficient" if sufficient_evidence else "insufficient_samples",
            "gate": "PASS" if passed else "FAIL",
        }
    return gate


# --------------------------------------------------------------------------- #
# Improve (propose-only) + reward-hacking guard
# --------------------------------------------------------------------------- #


def _passes_reward_hack_guard(proposal: Proposal) -> bool:
    """Reject any proposal that is not backed by a concrete evidence signal.

    REWARD-HACKING GUARD: a proposal whose only expected effect is raising the
    judge's pass rate (i.e. with no deterministic-check failure, no grader
    self-tag with concrete examples, and no kappa/agreement gap) is a grade-
    inflation risk and MUST be filtered out. Output metadata makes clear that a
    grader self-tag is diagnostic evidence, not independent corroboration.
    """
    if proposal.corroborating_signal not in {"deterministic_check", "failure_tag", "kappa_gap"}:
        return False
    evidence = proposal.evidence or {}
    if proposal.corroborating_signal == "failure_tag":
        # Must cite at least one concrete failing example / count.
        return bool(evidence.get("failing_source_ids")) or int(evidence.get("tag_count", 0)) > 0
    if proposal.corroborating_signal == "deterministic_check":
        return bool(evidence.get("check"))
    if proposal.corroborating_signal == "kappa_gap":
        return (
            evidence.get("cohen_kappa") is not None
            and evidence.get("evidence_status") == "sufficient"
        )
    return False


def _tag_proposals(criteria: dict[str, CriterionAnalysis]) -> list[Proposal]:
    """Build failure-tag-driven proposals (one per dominant tag per criterion)."""
    proposals: list[Proposal] = []
    for name, analysis in criteria.items():
        if not analysis.failure_tag_counts:
            continue
        # Consider every tag that appears at least once; rank favors dominant.
        for tag, count in analysis.failure_tag_counts.items():
            if tag not in TAG_TARGET_MAP:
                continue
            targets = TAG_TARGET_MAP[tag]
            suggestion = TAG_SUGGESTION.get(tag, TAG_SUGGESTION["OTHER"])
            failing_ids = analysis.evidence_by_tag.get(tag, [])
            is_dominant = tag == analysis.dominant_tag
            # Confidence scales with concentration of this tag among failures.
            total_fail = max(analysis.items_failed, 1)
            share = count / total_fail
            confidence = round(min(0.95, 0.35 + 0.6 * share), 4)
            for target_file, change_type in targets:
                evidence = {
                    "criterion": name,
                    "tag": tag,
                    "tag_count": count,
                    "is_dominant_tag": is_dominant,
                    "fail_rate": analysis.fail_rate,
                    "failing_source_ids": failing_ids[:8],
                }
                proposals.append(
                    Proposal(
                        target_file=target_file,
                        change_type=change_type,
                        criterion=name,
                        rationale=suggestion["rationale"],
                        evidence=evidence,
                        expected_metric_moved=suggestion["expected_metric_moved"],
                        suggested_text=suggestion["suggested_text"],
                        confidence=confidence,
                        corroborating_signal="failure_tag",
                    )
                )
    return proposals


def _deterministic_proposals(findings: list[dict[str, Any]]) -> list[Proposal]:
    """Build proposals backed by deterministic-check failures."""
    proposals: list[Proposal] = []
    for finding in findings:
        check = finding.get("check", "")
        if check == "coverage_mismatch":
            proposals.append(
                Proposal(
                    target_file="data/prompts/generator.txt",
                    change_type="investigate_coverage",
                    criterion="(pipeline)",
                    rationale=(
                        "The eval run returned fewer graded items than were submitted "
                        "(coverage_mismatch); some generated items were dropped or errored."
                    ),
                    evidence={"check": check, **finding.get("detail", {})},
                    expected_metric_moved="items_evaluated coverage",
                    suggested_text=(
                        "Ensure every generated question is well-formed and self-contained so the "
                        "grader does not drop it; check for empty stems or malformed options."
                    ),
                    confidence=0.5,
                    corroborating_signal="deterministic_check",
                )
            )
        elif check in {"generation_contract", "figure_skips"}:
            criterion = "distractor_structure_preservation" if check == "generation_contract" else "(figures)"
            proposals.append(
                Proposal(
                    target_file="data/prompts/generator.txt",
                    change_type="fix_contract_violation",
                    criterion=criterion,
                    rationale=(
                        f"Deterministic pre-judge check '{check}' failed: the generator output "
                        "violated a structural contract."
                    ),
                    evidence={"check": check, "errors": finding.get("errors", [])},
                    expected_metric_moved="deterministic_checks pass",
                    suggested_text=(
                        "Reproduce every source distractor_logic role one-to-one and keep skipped "
                        "figure placeholders empty with a skip_reason, per the generation contract."
                    ),
                    confidence=0.7,
                    corroborating_signal="deterministic_check",
                )
            )
    return proposals


def _kappa_proposals(gate: dict[str, dict[str, Any]], kappa_floor: float) -> list[Proposal]:
    """Build calibration proposals from a labeled-mode kappa gate.

    A criterion that FAILS the kappa gate indicates the *grader/rubric* is the
    problem (low agreement with reference labels), so the target is the rubric
    guidance / evaluator, not the generator.
    """
    proposals: list[Proposal] = []
    for criterion, row in gate.items():
        if row.get("gate") != "FAIL":
            continue
        # Insufficient calibration evidence must fail the gate, but it is not
        # evidence that rubric guidance is wrong. Do not propose a rubric edit
        # until the minimum number of independently labeled comparisons exists.
        if row.get("evidence_status") != "sufficient":
            continue
        inflated = int(row.get("grader_inflated_count", 0))
        missed = int(row.get("grader_missed_count", 0))
        direction = "inflates (marks bad items pass)" if inflated >= missed else "misses (fails good items)"
        proposals.append(
            Proposal(
                target_file=f"data/rubric.json:{criterion}",
                change_type="calibrate_guidance",
                criterion=criterion,
                rationale=(
                    f"Grader agreement with reference labels is below the kappa floor "
                    f"(kappa={row.get('cohen_kappa')} < {kappa_floor}); the grader {direction}."
                ),
                evidence={
                    "cohen_kappa": row.get("cohen_kappa"),
                    "raw_agreement": row.get("raw_agreement"),
                    "grader_inflated_count": inflated,
                    "grader_missed_count": missed,
                    "n_compared": row.get("n_compared"),
                    "evidence_status": row.get("evidence_status"),
                },
                expected_metric_moved=f"{criterion} cohen_kappa",
                suggested_text=(
                    "Clarify this criterion's guidance/scoring_guide to resolve the disagreement "
                    "direction above; add an explicit pass/fail boundary example if needed."
                ),
                confidence=0.8,
                corroborating_signal="kappa_gap",
            )
        )
    return proposals


def _rank_proposals(
    proposals: list[Proposal],
    criteria: dict[str, CriterionAnalysis],
) -> list[Proposal]:
    """Rank proposals by (corroboration weight x fail-rate x confidence).

    kappa-gap and deterministic proposals outrank pure-tag ones because they are
    the strongest signal a change is warranted (not just pass-rate movement).
    """
    signal_weight = {"kappa_gap": 3.0, "deterministic_check": 2.0, "failure_tag": 1.0}
    for proposal in proposals:
        fail_rate = 0.0
        analysis = criteria.get(proposal.criterion)
        if analysis and analysis.fail_rate is not None:
            fail_rate = analysis.fail_rate
        base = signal_weight.get(proposal.corroborating_signal, 1.0)
        # Dominant-tag proposals get a small bump.
        dominant_bump = 1.15 if proposal.evidence.get("is_dominant_tag") else 1.0
        proposal.rank_score = round(base * (0.3 + fail_rate) * proposal.confidence * dominant_bump, 4)
    return sorted(proposals, key=lambda p: p.rank_score, reverse=True)


def build_proposals(
    analysis: dict[str, Any],
    kappa_gate: Optional[dict[str, dict[str, Any]]],
    kappa_floor: float,
) -> list[Proposal]:
    """Assemble, reward-hack-filter, and rank all proposals."""
    criteria: dict[str, CriterionAnalysis] = analysis["criteria"]
    raw: list[Proposal] = []
    raw.extend(_tag_proposals(criteria))
    raw.extend(_deterministic_proposals(analysis["deterministic_findings"]))
    if kappa_gate:
        raw.extend(_kappa_proposals(kappa_gate, kappa_floor))

    # REWARD-HACKING FILTER: drop any proposal not backed by concrete evidence
    # (see _passes_reward_hack_guard). This is the explicit guard.
    filtered = [p for p in raw if _passes_reward_hack_guard(p)]
    return _rank_proposals(filtered, criteria)


# --------------------------------------------------------------------------- #
# Versioning + ledger
# --------------------------------------------------------------------------- #


def prompt_version_hash() -> str:
    """Hash the effective prompt files, including uncommitted content."""
    if not PROMPTS_DIR.is_dir():
        raise MetaEvaluationInputError(f"prompts directory does not exist: {PROMPTS_DIR}")
    prompt_paths = sorted(path for path in PROMPTS_DIR.rglob("*.txt") if path.is_file())
    if not prompt_paths:
        raise MetaEvaluationInputError(f"prompts directory contains no .txt files: {PROMPTS_DIR}")
    return _content_hash(prompt_paths, base=PROMPTS_DIR)


def rubric_version_hash() -> str:
    """Hash the effective rubric file, including uncommitted content."""
    if not RUBRIC_PATH.is_file():
        raise MetaEvaluationInputError(f"rubric file does not exist: {RUBRIC_PATH}")
    return _content_hash([RUBRIC_PATH], base=RUBRIC_PATH.parent)


def _content_hash(paths: Iterable[Path], *, base: Path) -> str:
    """Return a stable SHA-256 over relative names and exact file bytes."""
    digest = hashlib.sha256()
    digest.update(b"uconn-meta-evaluator-input-v1\0")
    try:
        for path in sorted((Path(path) for path in paths), key=lambda item: item.as_posix()):
            relative = path.relative_to(base).as_posix()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    except (OSError, ValueError) as exc:
        raise MetaEvaluationInputError(f"could not hash effective grader inputs: {exc}") from exc
    return f"sha256:{digest.hexdigest()}"


def append_ledger_entry(
    ledger_path: Path,
    entry: dict[str, Any],
) -> None:
    """Append one locked, durable JSONL record to the experiment ledger."""
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Report assembly + rendering
# --------------------------------------------------------------------------- #


def build_report(
    analysis: dict[str, Any],
    proposals: list[Proposal],
    kappa_gate: Optional[dict[str, dict[str, Any]]],
    *,
    kappa_floor: float,
    dataset_version: Optional[str],
    minimum_kappa_samples: int = DEFAULT_MIN_KAPPA_SAMPLES,
    created_at: Optional[str] = None,
) -> dict[str, Any]:
    """Assemble the full meta-evaluation report dict."""
    criteria: dict[str, CriterionAnalysis] = analysis["criteria"]
    created = created_at or _now_iso()
    gate_summary: Optional[dict[str, Any]] = None
    if kappa_gate is not None:
        failing = [c for c, r in kappa_gate.items() if r.get("gate") == "FAIL"]
        insufficient = [
            c for c, r in kappa_gate.items() if r.get("evidence_status") != "sufficient"
        ]
        gate_summary = {
            "kappa_floor": kappa_floor,
            "minimum_samples_per_criterion": minimum_kappa_samples,
            "criteria_failing_gate": sorted(failing),
            "criteria_with_insufficient_evidence": sorted(insufficient),
            "overall_gate": "FAIL" if failing or not kappa_gate else "PASS",
            "gate_reason": (
                "no calibration criteria were available"
                if not kappa_gate
                else "one or more criteria failed or lacked minimum evidence"
                if failing
                else "all criteria met the kappa floor with sufficient evidence"
            ),
        }
    return {
        "created_at": created,
        "prompt_version": prompt_version_hash(),
        "rubric_version": rubric_version_hash(),
        "dataset_version": dataset_version or "unlabeled",
        "eval_dirs_read": analysis["dirs_read"],
        "passing_score_threshold": analysis["threshold"],
        "criterion_analysis": {name: c.to_dict() for name, c in criteria.items()},
        "deterministic_findings": analysis["deterministic_findings"],
        "kappa_gate": kappa_gate,
        "kappa_gate_summary": gate_summary,
        "proposals": [p.to_dict() for p in proposals],
        "proposal_count": len(proposals),
    }


def render_markdown(report: dict[str, Any]) -> str:
    """Render a human-readable markdown summary of the meta-evaluation."""
    lines: list[str] = []
    lines.append("# Meta-Evaluation Report (PROPOSE-ONLY)")
    lines.append("")
    lines.append(f"- created_at: {report['created_at']}")
    lines.append(f"- prompt_version: {report['prompt_version']}")
    lines.append(f"- rubric_version: {report['rubric_version']}")
    lines.append(f"- dataset_version: {report['dataset_version']}")
    lines.append(f"- eval dirs read: {len(report['eval_dirs_read'])}")
    lines.append("")

    lines.append("## Per-criterion failure analysis")
    lines.append("")
    lines.append("| criterion | scored | failed | fail_rate | na | dominant_tag |")
    lines.append("|---|---|---|---|---|---|")
    for name, c in sorted(report["criterion_analysis"].items()):
        lines.append(
            f"| {name} | {c['items_scored']} | {c['items_failed']} | "
            f"{c['fail_rate']} | {c['na_count']} | {c['dominant_tag'] or '-'} |"
        )
    lines.append("")

    gate_summary = report.get("kappa_gate_summary")
    if gate_summary:
        lines.append("## Kappa gate (labeled mode)")
        lines.append("")
        lines.append(f"- floor: {gate_summary['kappa_floor']}")
        lines.append(
            f"- minimum comparisons per criterion: "
            f"{gate_summary['minimum_samples_per_criterion']}"
        )
        lines.append(f"- overall gate: **{gate_summary['overall_gate']}**")
        lines.append(f"- reason: {gate_summary['gate_reason']}")
        failing = gate_summary["criteria_failing_gate"]
        lines.append(f"- criteria failing gate: {', '.join(failing) if failing else '(none)'}")
        lines.append("")
        gate = report.get("kappa_gate") or {}
        lines.append("| criterion | n | evidence | kappa | gate | inflated | missed |")
        lines.append("|---|---|---|---|---|---|---|")
        for criterion, row in sorted(gate.items()):
            lines.append(
                f"| {criterion} | {row['n_compared']} | {row['evidence_status']} | "
                f"{row['cohen_kappa']} | "
                f"{row['gate']} | {row['grader_inflated_count']} | {row['grader_missed_count']} |"
            )
        lines.append("")

    findings = report.get("deterministic_findings") or []
    if findings:
        lines.append("## Deterministic-check findings")
        lines.append("")
        for finding in findings:
            lines.append(f"- **{finding.get('check')}** ({finding.get('status')}) — {finding.get('source')}")
        lines.append("")

    lines.append(f"## Ranked proposals ({report['proposal_count']}) — review, do NOT auto-apply")
    lines.append("")
    if not report["proposals"]:
        lines.append(
            "_No sufficiently evidenced proposals. "
            "(Proposals with only pass-rate effect are filtered.)_"
        )
    for idx, proposal in enumerate(report["proposals"], start=1):
        lines.append(
            f"### {idx}. `{proposal['target_file']}` — {proposal['change_type']} "
            f"(rank {proposal['rank_score']}, conf {proposal['confidence']})"
        )
        lines.append(f"- criterion: `{proposal['criterion']}`")
        lines.append(f"- signal: `{proposal['corroborating_signal']}`")
        lines.append(f"- evidence provenance: `{proposal['evidence_provenance']}`")
        lines.append(
            f"- independently corroborated: "
            f"{'yes' if proposal['independently_corroborated'] else 'no'}"
        )
        lines.append(f"- rationale: {proposal['rationale']}")
        lines.append(f"- expected metric moved: {proposal['expected_metric_moved']}")
        lines.append(f"- suggested text: {proposal['suggested_text']}")
        lines.append(f"- evidence: `{json.dumps(proposal['evidence'], ensure_ascii=False)}`")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def run_meta_evaluation(args: argparse.Namespace) -> dict[str, Any]:
    """Core pipeline usable from tests: analyze -> gate -> propose -> report."""
    minimum_kappa_samples = getattr(
        args, "minimum_kappa_samples", DEFAULT_MIN_KAPPA_SAMPLES
    )
    _validate_kappa_settings(args.kappa_floor, minimum_kappa_samples)
    analysis = analyze_eval_dirs(args.eval_dir)

    kappa_gate: Optional[dict[str, dict[str, Any]]] = None
    dataset_version: Optional[str] = None
    if args.labels:
        records, dataset_version = load_calibration_labels(Path(args.labels))
        kappa_gate = compute_kappa_gate(
            records,
            args.kappa_floor,
            minimum_samples=minimum_kappa_samples,
        )

    proposals = build_proposals(analysis, kappa_gate, args.kappa_floor)
    report = build_report(
        analysis,
        proposals,
        kappa_gate,
        kappa_floor=args.kappa_floor,
        dataset_version=dataset_version,
        minimum_kappa_samples=minimum_kappa_samples,
        created_at=getattr(args, "created_at", None),
    )
    return report


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Grader self-improvement meta-evaluator: mine failure tags + deterministic "
            "checks from evaluation outputs and emit ranked, PROPOSE-ONLY grader/prompt fixes."
        ),
    )
    parser.add_argument(
        "--eval-dir",
        dest="eval_dir",
        type=Path,
        action="append",
        required=True,
        help="An evaluation_outputs dir (or a run dir containing it). Repeatable for a batch.",
    )
    parser.add_argument(
        "--labels",
        type=Path,
        default=None,
        help="Optional calibration JSONL (data/calibration/*.jsonl) for the Cohen's kappa gate.",
    )
    parser.add_argument(
        "--kappa-floor",
        dest="kappa_floor",
        type=float,
        default=DEFAULT_KAPPA_FLOOR,
        help=f"Per-criterion Cohen's kappa floor for the labeled-mode gate (default {DEFAULT_KAPPA_FLOOR}).",
    )
    parser.add_argument(
        "--min-kappa-samples",
        dest="minimum_kappa_samples",
        type=int,
        default=DEFAULT_MIN_KAPPA_SAMPLES,
        help=(
            "Minimum non-N/A comparisons required for a criterion to pass the "
            f"kappa gate (default {DEFAULT_MIN_KAPPA_SAMPLES})."
        ),
    )
    parser.add_argument(
        "--json-out",
        dest="json_out",
        type=Path,
        default=None,
        help="Write the JSON report here (default: <first eval-dir>/meta_evaluation_report.json).",
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=None,
        help=f"Experiment ledger JSONL (default: backend/runtime/{LEDGER_FILENAME}).",
    )
    parser.add_argument(
        "--no-ledger",
        action="store_true",
        help="Do not append to the experiment ledger.",
    )
    parser.add_argument(
        "--created-at",
        dest="created_at",
        default=None,
        help="Override the run timestamp (ISO 8601); defaults to now.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    try:
        report = run_meta_evaluation(args)
    except MetaEvaluationInputError as exc:
        print(f"meta-evaluator input error: {exc}", file=sys.stderr)
        return 2

    # Resolve output paths.
    default_dir = Path(args.eval_dir[0])
    if (default_dir / "evaluation_outputs").exists():
        default_dir = default_dir / "evaluation_outputs"
    json_out = args.json_out or (default_dir / META_EVAL_REPORT_FILENAME)
    json_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # Ledger.
    if not args.no_ledger:
        ledger_path = args.ledger or (RUNTIME_DIR / LEDGER_FILENAME)
        append_ledger_entry(
            ledger_path,
            {
                "created_at": report["created_at"],
                "prompt_version": report["prompt_version"],
                "rubric_version": report["rubric_version"],
                "dataset_version": report["dataset_version"],
                "eval_dirs_read": report["eval_dirs_read"],
                "proposal_count": report["proposal_count"],
                "kappa_gate_overall": (report.get("kappa_gate_summary") or {}).get("overall_gate"),
                "report_path": str(json_out),
            },
        )

    print(render_markdown(report))
    print(f"\nWrote {json_out}")
    gate_summary = report.get("kappa_gate_summary")
    if gate_summary and gate_summary.get("overall_gate") == "FAIL":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
