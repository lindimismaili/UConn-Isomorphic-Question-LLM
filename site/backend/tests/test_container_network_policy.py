"""Container egress allowlist validation and provider-policy regressions."""

from __future__ import annotations

import asyncio
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.src import container
from backend.src.paths import PROMPTS_DIR
from backend.src.schemas import LaTeXOutput
from backend.src.workflow import engine


def test_container_domain_override_is_normalized_and_deduplicated() -> None:
    with patch.dict(
        "os.environ",
        {"OPENAI_CONTAINER_ALLOWED_DOMAINS": "PYPI.ORG., files.pythonhosted.org,pypi.org"},
    ):
        domains = container.allowed_domains_from_env()

    assert domains == ["pypi.org", "files.pythonhosted.org"]
    assert container.container_network_policy(domains) == {
        "type": "allowlist",
        "allowed_domains": domains,
    }


@pytest.mark.parametrize(
    "value",
    [
        "https://pypi.org",
        "pypi.org/simple",
        "pypi.org:443",
        "*.pypi.org",
        "localhost",
        "-invalid.example",
    ],
)
def test_container_domain_override_rejects_non_dns_values(value: str) -> None:
    with patch.dict(
        "os.environ",
        {"OPENAI_CONTAINER_ALLOWED_DOMAINS": value},
    ):
        with pytest.raises(ValueError, match="bare DNS names"):
            container.allowed_domains_from_env()


def test_empty_allowlist_disables_container_network() -> None:
    assert container.container_network_policy([]) == {"type": "disabled"}


def test_shell_task_is_single_call_and_disables_parallel_tools() -> None:
    client = MagicMock()
    client.responses.create.return_value = SimpleNamespace(id="response")
    container._run_shell_task(
        client,
        model="test-model",
        container_id="container-test",
        prompt="run one bounded task",
    )
    kwargs = client.responses.create.call_args.kwargs
    assert kwargs["max_tool_calls"] == 10
    assert kwargs["parallel_tool_calls"] is False


def test_bootstrap_prompts_split_document_and_latex_dependencies() -> None:
    document_prompt = (PROMPTS_DIR / "bootstrap.txt").read_text(encoding="utf-8")
    compile_prompt = (PROMPTS_DIR / "bootstrapCompile.txt").read_text(encoding="utf-8")

    assert "document extraction" in document_prompt
    assert "poppler-utils" in document_prompt
    assert "texlive-" not in document_prompt
    assert "pdflatex" not in document_prompt
    assert "if ! command -v pdflatex" in compile_prompt
    assert "texlive-latex-base" in compile_prompt
    assert compile_prompt.count("pdflatex -no-shell-escape") == 2


def test_compile_latex_does_not_run_document_bootstrap() -> None:
    def fake_download(*_args, destination: Path, **_kwargs) -> None:
        destination.write_bytes(
            b"%PDF-1.4 test" if destination.suffix == ".pdf" else b"test tex"
        )

    with tempfile.TemporaryDirectory() as tmp:
        runtime = container.SharedWorkflowRuntime(
            client=MagicMock(),
            container_id="container-test",
            output_dir=Path(tmp),
            allowed_domains=["deb.debian.org"],
        )
        runtime.bootstrap = MagicMock()
        with (
            patch.object(container, "_upload_text_file", return_value="/mnt/data/source.tex"),
            patch.object(container, "render_prompt", return_value="compile prompt"),
            patch.object(
                container,
                "_run_shell_task",
                return_value=SimpleNamespace(id="response-test"),
            ) as shell_task,
            patch.object(
                container,
                "_find_container_file",
                side_effect=[
                    SimpleNamespace(id="pdf-file"),
                    SimpleNamespace(id="tex-file"),
                ],
            ),
            patch.object(container, "_download_container_file", side_effect=fake_download),
        ):
            result = runtime.compile_latex(
                "\\documentclass{article}\\begin{document}Q\\end{document}",
                "questions",
            )

    runtime.bootstrap.assert_not_called()
    shell_task.assert_called_once()
    assert result.pdf_filename == "questions.pdf"
    assert result.tex_filename == "questions.tex"


def test_compile_attempt_budget_is_workflow_wide_and_fail_closed() -> None:
    seen: list[int] = []
    budget = container.CompileAttemptBudget(
        max_attempts=1,
        on_claim=seen.append,
    )

    budget.claim()

    assert seen == [1]
    with pytest.raises(ValueError, match="attempt limit"):
        budget.claim()
    assert budget.attempts == 1


def test_compile_tool_metadata_omits_local_filesystem_paths(tmp_path: Path) -> None:
    result = container.LatexCompileResult(
        pdf_filename="questions.pdf",
        tex_filename="questions.tex",
        pdf_path=tmp_path / "private" / "questions.pdf",
        tex_path=tmp_path / "private" / "questions.tex",
        response_id="response",
    )

    payload = result.as_dict()

    assert payload == {
        "pdf_filename": "questions.pdf",
        "tex_filename": "questions.tex",
        "response_id": "response",
    }
    assert str(tmp_path) not in str(payload)


def test_post_agent_materialization_cancellation_stops_before_paid_shell(
    tmp_path: Path,
) -> None:
    started = threading.Event()
    release = threading.Event()
    shell_task = MagicMock(return_value=SimpleNamespace(id="response"))

    def blocking_upload(*_args, **_kwargs) -> str:
        started.set()
        if not release.wait(timeout=5):
            raise TimeoutError("test upload was not released")
        return "/mnt/data/questions.tex"

    async def drive() -> None:
        runtime = container.SharedWorkflowRuntime(
            client=MagicMock(),
            container_id="container-test",
            output_dir=tmp_path,
            allowed_domains=[],
        )
        with (
            patch.object(container, "_upload_text_file", side_effect=blocking_upload),
            patch.object(container, "_run_shell_task", shell_task),
        ):
            task = asyncio.create_task(
                engine.materialize_latex_output_async(
                    LaTeXOutput(
                        latex_content=(
                            "\\documentclass{article}"
                            "\\begin{document}Q\\end{document}"
                        ),
                        filename="questions.tex",
                        pdf_filename="questions.pdf",
                    ),
                    tmp_path,
                    runtime,
                    to_thread=asyncio.to_thread,
                )
            )
            assert await asyncio.to_thread(started.wait, 1)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task

    try:
        asyncio.run(drive())
    finally:
        release.set()

    shell_task.assert_not_called()


class _StreamingContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.closed = False

    def iter_bytes(self, *, chunk_size: int):
        assert chunk_size == 64 * 1024
        yield from self.chunks

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self.closed = True


def test_container_file_download_streams_atomically_with_private_mode(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "questions.pdf"
    streamed = _StreamingContent([b"%PDF-", b"bounded"])
    client = MagicMock()
    client.containers.files.content.with_streaming_response.retrieve.return_value = (
        streamed
    )

    with patch.dict(
        "os.environ",
        {"MAX_GENERATED_ARTIFACT_BYTES": "100"},
    ):
        container._download_container_file(
            client,
            container_id="container-test",
            file_id="file-test",
            destination=destination,
        )

    assert destination.read_bytes() == b"%PDF-bounded"
    assert destination.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob("*.partial"))
    assert streamed.closed is True
    client.containers.files.content.retrieve.assert_not_called()
    client.containers.files.content.with_streaming_response.retrieve.assert_called_once_with(
        "file-test",
        container_id="container-test",
    )


def test_container_file_download_rejects_oversize_and_preserves_existing_file(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "questions.tex"
    destination.write_bytes(b"previous")
    streamed = _StreamingContent([b"1234", b"5"])
    client = MagicMock()
    client.containers.files.content.with_streaming_response.retrieve.return_value = (
        streamed
    )

    with (
        patch.dict(
            "os.environ",
            {"MAX_GENERATED_ARTIFACT_BYTES": "4"},
        ),
        pytest.raises(ValueError, match="MAX_GENERATED_ARTIFACT_BYTES"),
    ):
        container._download_container_file(
            client,
            container_id="container-test",
            file_id="file-test",
            destination=destination,
        )

    assert destination.read_bytes() == b"previous"
    assert not list(tmp_path.glob("*.partial"))
    assert streamed.closed is True


@pytest.mark.parametrize(
    "value",
    ["", "0", "not-an-integer", str(512 * 1024 * 1024 + 1)],
)
def test_generated_artifact_limit_rejects_unsafe_values(value: str) -> None:
    with patch.dict(
        "os.environ",
        {"MAX_GENERATED_ARTIFACT_BYTES": value},
    ):
        with pytest.raises(ValueError, match="MAX_GENERATED_ARTIFACT_BYTES"):
            container.max_generated_artifact_bytes()
