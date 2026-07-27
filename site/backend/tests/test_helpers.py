"""Unit tests for pure helpers in ``backend.src.helpers``.

Covers strict prompt loading/rendering (placeholder substitution + validation),
``build_json_message`` structure, and the metadata/question-type reconciliation
helpers. All offline, no OpenAI client.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.src.helpers import (
    apply_requested_difficulty,
    build_json_message,
    load_prompt,
    merge_extracted_metadata_into_config,
    normalize_question_types,
    render_prompt,
    resolve_prompt_path,
)
from backend.src.schemas import ExtracterOutput, NormalizerOutput


class _CapturingLogger:
    """Minimal logger stub capturing warning/info calls."""

    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.infos: list[str] = []

    def warning(self, msg: str, *args: object) -> None:
        self.warnings.append(msg % args if args else msg)

    def info(self, msg: str, *args: object) -> None:
        self.infos.append(msg % args if args else msg)


class PromptPathTests(unittest.TestCase):
    def test_resolve_prompt_path_found_and_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "normalizer.txt").write_text("hi", encoding="utf-8")
            self.assertEqual(resolve_prompt_path(root, "normalizer"), root / "normalizer.txt")
            self.assertIsNone(resolve_prompt_path(root, "missing"))

    def test_load_prompt_returns_required_text_or_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "p.txt").write_text("Prompt body", encoding="utf-8")
            log = _CapturingLogger()
            self.assertEqual(load_prompt(root, log, "p"), "Prompt body")
            with self.assertRaises(FileNotFoundError):
                load_prompt(root, log, "nope")

    def test_load_prompt_rejects_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "p.txt").write_text(" \n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "empty"):
                load_prompt(root, _CapturingLogger(), "p")


class RenderPromptTests(unittest.TestCase):
    def test_substitutes_placeholders(self) -> None:
        # NOTE: render_prompt's 3rd positional param is ``name`` (the prompt
        # file), so placeholder variables cannot be called "name" — use
        # ``greeting``/``count`` here.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "t.txt").write_text("Hello {greeting}, run {count} times.", encoding="utf-8")
            log = _CapturingLogger()
            out = render_prompt(root, log, "t", greeting="world", count=3)
            self.assertEqual(out, "Hello world, run 3 times.")
            self.assertFalse(log.warnings)

    def test_rejects_unsubstituted_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "t.txt").write_text("Value {present} but {leftover} remains.", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, r"\{leftover\}"):
                render_prompt(root, _CapturingLogger(), "t", present="ok")

    def test_rejects_missing_required_placeholder_in_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "t.txt").write_text("No runtime placeholder.", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, r"\{expected\}"):
                render_prompt(root, _CapturingLogger(), "t", expected="value")

    def test_braces_in_prose_are_not_format_errors(self) -> None:
        # render_prompt uses plain replacement, so LaTeX/shell braces are safe.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "t.txt").write_text("\\begin{document} {x} \\end{document}", encoding="utf-8")
            log = _CapturingLogger()
            out = render_prompt(root, log, "t", x="Q")
            self.assertIn("\\begin{document}", out)
            self.assertIn("Q", out)


class BuildJsonMessageTests(unittest.TestCase):
    def test_single_user_message_with_json_text(self) -> None:
        msg = build_json_message({"a": 1, "b": "two"})
        self.assertEqual(len(msg), 1)
        self.assertEqual(msg[0]["role"], "user")
        content = msg[0]["content"]
        self.assertEqual(content[-1]["type"], "input_text")
        # The text part is valid JSON round-tripping to the payload.
        self.assertEqual(json.loads(content[-1]["text"]), {"a": 1, "b": "two"})

    def test_prepends_file_refs(self) -> None:
        refs = [{"type": "input_file", "file_id": "file-123"}]
        msg = build_json_message({"a": 1}, file_refs=refs)
        content = msg[0]["content"]
        self.assertEqual(content[0], refs[0])
        self.assertEqual(content[-1]["type"], "input_text")


class MetadataReconciliationTests(unittest.TestCase):
    def _extraction(self, **overrides: object) -> ExtracterOutput:
        base = {"filename": "src.pdf", "title": "Extracted Title", "theme": "Extracted Theme", "questions": []}
        base.update(overrides)
        return ExtracterOutput.model_validate(base)

    def test_merge_fills_blank_config_fields_only(self) -> None:
        config = NormalizerOutput(title="", theme="")
        merge_extracted_metadata_into_config(config, self._extraction())
        self.assertEqual(config.title, "Extracted Title")
        self.assertEqual(config.theme, "Extracted Theme")

    def test_merge_keeps_user_supplied_values(self) -> None:
        config = NormalizerOutput(title="User Title", theme="User Theme")
        merge_extracted_metadata_into_config(config, self._extraction())
        self.assertEqual(config.title, "User Title")
        self.assertEqual(config.theme, "User Theme")

    def test_apply_requested_difficulty_sets_all(self) -> None:
        extraction = self._extraction(
            questions=[
                {
                    "source_id": "1",
                    "source_raw": "r",
                    "stem": "s",
                    "options": [],
                    "has_explicit_answer": False,
                    "question_type": "open_ended",
                    "difficulty": "old",
                }
            ]
        )
        apply_requested_difficulty(extraction.questions, "hard")
        self.assertEqual(extraction.questions[0].difficulty, "hard")


class NormalizeQuestionTypesTests(unittest.TestCase):
    def test_options_imply_multiple_choice(self) -> None:
        extraction = ExtracterOutput.model_validate(
            {
                "filename": "",
                "questions": [
                    {
                        "source_id": "1",
                        "source_raw": "r",
                        "stem": "s",
                        "options": [
                            {"key": "A", "text": "a", "is_correct": True, "explanation": ""}
                        ],
                        "has_explicit_answer": True,
                        "question_type": "open_ended",
                    }
                ],
            }
        )
        normalize_question_types(extraction.questions)
        self.assertEqual(extraction.questions[0].question_type, "multiple_choice")


if __name__ == "__main__":
    unittest.main()
