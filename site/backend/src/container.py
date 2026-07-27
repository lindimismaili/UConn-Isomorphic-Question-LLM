"""OpenAI runtime helpers for one explicitly owned workflow container scope."""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agents import function_tool
from agents.logger import logger
from openai import OpenAI

from .helpers import render_prompt
from .paths import PROMPTS_DIR
from .provider_timeouts import (
    await_cancellation_safe_blocking_call,
    container_task_timeout_seconds,
)
from .registry import (
    setup_allowed_domains as _registry_allowed_domains,
)
from .registry import (
    setup_bootstrap_model as _registry_bootstrap_model,
)
from .registry import (
    setup_container_packages as _registry_packages,
)
from .registry import (
    setup_max_output_tokens as _registry_setup_max_output_tokens,
)


def allowed_domains_from_env() -> list[str]:
    """Return a validated container network allowlist from configuration."""
    raw = os.getenv("OPENAI_CONTAINER_ALLOWED_DOMAINS", "")
    configured = (
        _registry_allowed_domains()
        if not raw.strip()
        else [item.strip() for item in raw.split(",") if item.strip()]
    )
    domains: list[str] = []
    seen: set[str] = set()
    for item in configured:
        domain = item.lower().rstrip(".")
        labels = domain.split(".")
        if (
            not domain
            or len(domain) > 253
            or len(labels) < 2
            or any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                or re.fullmatch(r"[a-z0-9-]+", label) is None
                for label in labels
            )
        ):
            raise ValueError(
                "OPENAI_CONTAINER_ALLOWED_DOMAINS entries must be bare DNS names "
                f"without schemes, paths, ports, or wildcards; got {item!r}"
            )
        if domain not in seen:
            seen.add(domain)
            domains.append(domain)
    return domains


def container_network_policy(allowed_domains: list[str]) -> dict[str, Any]:
    """Build the provider-enforced policy for a workflow container."""
    if not allowed_domains:
        return {"type": "disabled"}
    return {"type": "allowlist", "allowed_domains": list(allowed_domains)}


def max_generated_artifact_bytes() -> int:
    """Maximum bytes accepted for each provider-generated PDF or TeX file."""
    maximum = 512 * 1024 * 1024
    raw = (os.getenv("MAX_GENERATED_ARTIFACT_BYTES", str(50 * 1024 * 1024)) or "").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"MAX_GENERATED_ARTIFACT_BYTES must be an integer from 1 to {maximum}"
        ) from exc
    if not 1 <= value <= maximum:
        raise ValueError(
            f"MAX_GENERATED_ARTIFACT_BYTES must be an integer from 1 to {maximum}"
        )
    return value


def sanitize_basename(value: str) -> str:
    """Sanitize a filename stem for safe use inside the container."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip().lower()).strip("-")
    return cleaned[:64] or "rendered-formula"


def _run_shell_task(
    client: OpenAI,
    *,
    model: str,
    container_id: str,
    prompt: str,
) -> Any:
    """Execute a shell prompt against the owned container and return the response."""
    environment: dict[str, Any] = {
        "type": "container_reference",
        "container_id": container_id,
    }

    return client.responses.create(
        model=model,
        tool_choice="required",
        tools=[{"type": "shell", "environment": environment}],
        input=prompt,
        max_tool_calls=10,
        max_output_tokens=_registry_setup_max_output_tokens(),
        parallel_tool_calls=False,
        store=False,
        timeout=container_task_timeout_seconds(),
    )


def _upload_text_file(
    client: OpenAI,
    *,
    container_id: str,
    content: str,
    suffix: str,
) -> str:
    """Upload text content into the container file space and return its path."""
    with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8") as handle:
        handle.write(content)
        temp_path = Path(handle.name)

    try:
        with temp_path.open("rb") as file_handle:
            container_file = client.containers.files.create(
                container_id=container_id,
                file=file_handle,
            )
    finally:
        temp_path.unlink(missing_ok=True)

    return container_file.path


def _find_container_file(client: OpenAI, *, container_id: str, suffix: str, basename: str) -> Any:
    """Locate a container file by basename and suffix.

    Raises:
        FileNotFoundError: If the expected file does not exist in the container.
    """
    candidates = list(client.containers.files.list(container_id=container_id, limit=100))
    expected = f"{basename}{suffix}"
    for item in reversed(candidates):
        if Path(item.path).name == expected:
            return item
    raise FileNotFoundError(f"Could not find {expected} in container {container_id}.")


def _download_container_file(
    client: OpenAI,
    *,
    container_id: str,
    file_id: str,
    destination: Path,
) -> None:
    """Stream a bounded container file into place without buffering it in RAM."""
    content_api = client.containers.files.content
    streaming_api = getattr(content_api, "with_streaming_response", None)
    retrieve = getattr(streaming_api, "retrieve", None)
    if not callable(retrieve):
        raise RuntimeError(
            "OpenAI SDK container downloads do not expose streaming responses; "
            "upgrade the openai package before generating artifacts"
        )
    streaming_response = retrieve(
        file_id,
        container_id=container_id,
    )

    maximum = max_generated_artifact_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with streaming_response as content:
            iter_bytes = getattr(content, "iter_bytes", None)
            if not callable(iter_bytes):
                raise RuntimeError(
                    "OpenAI SDK streaming response does not expose byte iteration; "
                    "upgrade the openai package before generating artifacts"
                )
            with tempfile.NamedTemporaryFile(
                "wb",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".partial",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                os.chmod(temporary_path, 0o600)
                total = 0
                for chunk in iter_bytes(chunk_size=64 * 1024):
                    if not isinstance(chunk, (bytes, bytearray, memoryview)):
                        raise RuntimeError(
                            "OpenAI container download returned a non-byte chunk"
                        )
                    total += len(chunk)
                    if total > maximum:
                        raise ValueError(
                            "Generated artifact exceeds the configured "
                            f"{maximum}-byte limit (MAX_GENERATED_ARTIFACT_BYTES)"
                        )
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


@dataclass
class LatexCompileResult:
    """Details about a LaTeX compile operation performed in the container."""

    pdf_filename: str
    tex_filename: str
    pdf_path: Path
    tex_path: Path
    response_id: str

    def as_dict(self) -> dict[str, Any]:
        """Return provider-safe metadata without exposing local filesystem paths."""
        return {
            "pdf_filename": self.pdf_filename,
            "tex_filename": self.tex_filename,
            "response_id": self.response_id,
        }


class CompileAttemptBudget:
    """Thread-safe workflow-wide ceiling for paid LaTeX shell attempts."""

    def __init__(
        self,
        max_attempts: int = 3,
        *,
        on_claim: Callable[[int], None] | None = None,
    ) -> None:
        self.max_attempts = max_attempts
        self.attempts = 0
        self._on_claim = on_claim
        self._lock = threading.Lock()

    def claim(self) -> None:
        with self._lock:
            if self.attempts >= self.max_attempts:
                raise ValueError(
                    "LaTeX compile attempt limit reached for this workflow."
                )
            self.attempts += 1
            attempts = self.attempts
        if self._on_claim is not None:
            self._on_claim(attempts)


class SharedWorkflowRuntime:
    """Runtime bound to one explicitly owned provider-container scope."""

    def __init__(
        self,
        *,
        client: OpenAI,
        container_id: str,
        output_dir: Path,
        model: str | None = None,
        allowed_domains: list[str] | None = None,
        compile_attempt_budget: CompileAttemptBudget | None = None,
        record_shell_call: Callable[[], None] | None = None,
    ) -> None:
        self.client = client
        self.container_id = container_id
        self.output_dir = output_dir
        self.model = model or _registry_bootstrap_model()
        self.allowed_domains = allowed_domains or allowed_domains_from_env()
        self.compile_attempt_budget = (
            compile_attempt_budget or CompileAttemptBudget()
        )
        self.record_shell_call = record_shell_call
        self.bootstrapped = False
        self.bootstrap_response_id: str | None = None

    def bootstrap(self) -> str:
        """Install document-extraction dependencies inside this container.

        Returns:
            The response ID for the document bootstrap shell task.
        """
        if self.bootstrapped:
            return self.bootstrap_response_id or ""

        bootstrap_prompt = render_prompt(
            PROMPTS_DIR,
            logger,
            "bootstrap",
            domains_json=json.dumps(self.allowed_domains),
            packages=" ".join(_registry_packages()),
        )

        response = _run_shell_task(
            self.client,
            model=self.model,
            container_id=self.container_id,
            prompt=bootstrap_prompt,
        )
        if self.record_shell_call is not None:
            self.record_shell_call()
        self.bootstrapped = True
        self.bootstrap_response_id = response.id
        return response.id

    def compile_latex(self, latex_source: str, output_basename: str) -> LatexCompileResult:
        """Provision TeX if needed, compile LaTeX, and download the artifacts."""
        self.compile_attempt_budget.claim()
        basename = sanitize_basename(output_basename)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        tex_path = _upload_text_file(
            self.client,
            container_id=self.container_id,
            content=latex_source,
            suffix=".tex",
        )

        compile_prompt = render_prompt(
            PROMPTS_DIR,
            logger,
            "bootstrapCompile",
            tex_path=tex_path,
            basename=basename,
        )

        response = _run_shell_task(
            self.client,
            model=self.model,
            container_id=self.container_id,
            prompt=compile_prompt,
        )
        if self.record_shell_call is not None:
            self.record_shell_call()

        pdf_file = _find_container_file(
            self.client,
            container_id=self.container_id,
            suffix=".pdf",
            basename=basename,
        )
        tex_file = _find_container_file(
            self.client,
            container_id=self.container_id,
            suffix=".tex",
            basename=basename,
        )

        local_pdf = self.output_dir / f"{basename}.pdf"
        local_tex = self.output_dir / f"{basename}.tex"
        _download_container_file(
            self.client,
            container_id=self.container_id,
            file_id=pdf_file.id,
            destination=local_pdf,
        )
        _download_container_file(
            self.client,
            container_id=self.container_id,
            file_id=tex_file.id,
            destination=local_tex,
        )

        return LatexCompileResult(
            pdf_filename=local_pdf.name,
            tex_filename=local_tex.name,
            pdf_path=local_pdf,
            tex_path=local_tex,
            response_id=response.id,
        )

    async def compile_latex_async(
        self,
        latex_source: str,
        output_basename: str,
    ) -> LatexCompileResult:
        """Compile in cancellable steps so Stop cannot start later paid calls."""
        self.compile_attempt_budget.claim()
        basename = sanitize_basename(output_basename)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        tex_path = await await_cancellation_safe_blocking_call(
            asyncio.to_thread,
            _upload_text_file,
            self.client,
            container_id=self.container_id,
            content=latex_source,
            suffix=".tex",
        )
        compile_prompt = render_prompt(
            PROMPTS_DIR,
            logger,
            "bootstrapCompile",
            tex_path=tex_path,
            basename=basename,
        )
        response = await await_cancellation_safe_blocking_call(
            asyncio.to_thread,
            _run_shell_task,
            self.client,
            model=self.model,
            container_id=self.container_id,
            prompt=compile_prompt,
        )
        if self.record_shell_call is not None:
            self.record_shell_call()
        pdf_file = await await_cancellation_safe_blocking_call(
            asyncio.to_thread,
            _find_container_file,
            self.client,
            container_id=self.container_id,
            suffix=".pdf",
            basename=basename,
        )
        tex_file = await await_cancellation_safe_blocking_call(
            asyncio.to_thread,
            _find_container_file,
            self.client,
            container_id=self.container_id,
            suffix=".tex",
            basename=basename,
        )
        local_pdf = self.output_dir / f"{basename}.pdf"
        local_tex = self.output_dir / f"{basename}.tex"
        await await_cancellation_safe_blocking_call(
            asyncio.to_thread,
            _download_container_file,
            self.client,
            container_id=self.container_id,
            file_id=pdf_file.id,
            destination=local_pdf,
        )
        await await_cancellation_safe_blocking_call(
            asyncio.to_thread,
            _download_container_file,
            self.client,
            container_id=self.container_id,
            file_id=tex_file.id,
            destination=local_tex,
        )
        return LatexCompileResult(
            pdf_filename=local_pdf.name,
            tex_filename=local_tex.name,
            pdf_path=local_pdf,
            tex_path=local_tex,
            response_id=response.id,
        )

    def build_compile_latex_tool(self):
        """Expose LaTeX compilation as an OpenAI function tool."""
        @function_tool(
            timeout=container_task_timeout_seconds(),
            timeout_behavior="raise_exception",
        )
        async def compile_latex_pdf(
            latex_source: str,
            output_basename: str = "isomorphic_questions",
        ) -> dict[str, Any]:
            """
            Compile a complete standalone LaTeX document into a PDF inside the
            active downstream workflow container.

            Args:
                latex_source: Full LaTeX source, including documentclass, preamble, and document body.
                output_basename: Basename for the generated .tex and .pdf files.

            Returns:
                JSON-serializable metadata describing the generated files.
            """

            result = await self.compile_latex_async(
                latex_source,
                output_basename,
            )
            return result.as_dict()

        return compile_latex_pdf
