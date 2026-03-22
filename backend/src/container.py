from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents import function_tool
from openai import OpenAI


DEFAULT_BOOTSTRAP_MODEL = os.getenv("OPENAI_CONTAINER_BOOTSTRAP_MODEL", "gpt-5.4")
DEFAULT_ALLOWED_DOMAINS = [
    "archive.ubuntu.com",
    "security.ubuntu.com",
    "deb.debian.org",
    "security.debian.org",
    "pypi.org",
    "files.pythonhosted.org",
]


def allowed_domains_from_env() -> list[str]:
    raw = os.getenv("OPENAI_CONTAINER_ALLOWED_DOMAINS", "")
    if not raw.strip():
        return DEFAULT_ALLOWED_DOMAINS
    return [item.strip() for item in raw.split(",") if item.strip()]


def sanitize_basename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip().lower()).strip("-")
    return cleaned[:64] or "rendered-formula"


def _run_shell_task(
    client: OpenAI,
    *,
    model: str,
    container_id: str,
    prompt: str,
) -> Any:
    environment: dict[str, Any] = {
        "type": "container_reference",
        "container_id": container_id,
    }

    return client.responses.create(
        model=model,
        tool_choice="required",
        tools=[{"type": "shell", "environment": environment}],
        input=prompt,
    )


def _upload_text_file(
    client: OpenAI,
    *,
    container_id: str,
    content: str,
    suffix: str,
) -> str:
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
    content = client.containers.files.content.retrieve(file_id, container_id=container_id)
    destination.write_bytes(content.read())


@dataclass
class LatexCompileResult:
    pdf_filename: str
    tex_filename: str
    pdf_path: Path
    tex_path: Path
    response_id: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "pdf_filename": self.pdf_filename,
            "tex_filename": self.tex_filename,
            "pdf_path": str(self.pdf_path),
            "tex_path": str(self.tex_path),
            "response_id": self.response_id,
        }


class SharedWorkflowRuntime:
    def __init__(
        self,
        *,
        client: OpenAI,
        container_id: str,
        output_dir: Path,
        model: str = DEFAULT_BOOTSTRAP_MODEL,
        allowed_domains: list[str] | None = None,
    ) -> None:
        self.client = client
        self.container_id = container_id
        self.output_dir = output_dir
        self.model = model
        self.allowed_domains = allowed_domains or allowed_domains_from_env()
        self.bootstrapped = False
        self.bootstrap_response_id: str | None = None

    def bootstrap(self) -> str:
        if self.bootstrapped:
            return self.bootstrap_response_id or ""

        domains_json = json.dumps(self.allowed_domains)
        bootstrap_prompt = f"""
Use the hosted shell container and run the setup exactly as a shell session.

Network note:
- This workflow expects package/network access only for these dependency domains: {domains_json}
- If the environment enforces an allowlist, stay within those domains.

1. Run `set -euxo pipefail`.
2. Run `apt-get update`.
3. Install these packages with apt:
   texlive-latex-base
   texlive-latex-recommended
   texlive-fonts-recommended
   texlive-latex-extra
   ghostscript
   poppler-utils
   python3-pip
4. Run `python3 -m pip install --upgrade pip`.
5. Install these Python PDF helpers:
   pymupdf
   pdf2image
6. Verify with:
   `pdflatex --version`
   `python3 -c "import fitz, pdf2image; print('pdf-python-deps-ok')"`
7. Finish with a short summary.
""".strip()

        response = _run_shell_task(
            self.client,
            model=self.model,
            container_id=self.container_id,
            prompt=bootstrap_prompt,
        )
        self.bootstrapped = True
        self.bootstrap_response_id = response.id
        return response.id

    def compile_latex(self, latex_source: str, output_basename: str) -> LatexCompileResult:
        self.bootstrap()
        basename = sanitize_basename(output_basename)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        tex_path = _upload_text_file(
            self.client,
            container_id=self.container_id,
            content=latex_source,
            suffix=".tex",
        )

        compile_prompt = f"""
Use the hosted shell container and run the following work exactly.

1. Run `set -euxo pipefail`.
2. Create `/mnt/data/out`.
3. Copy `{tex_path}` to `/mnt/data/out/{basename}.tex`.
4. Change to `/mnt/data/out`.
5. Run `pdflatex -interaction=nonstopmode -halt-on-error {basename}.tex`.
6. Run `pdflatex -interaction=nonstopmode -halt-on-error {basename}.tex` a second time.
7. Confirm that `/mnt/data/out/{basename}.pdf` exists.
8. Finish with a short summary and mention the PDF path.
""".strip()

        response = _run_shell_task(
            self.client,
            model=self.model,
            container_id=self.container_id,
            prompt=compile_prompt,
        )

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

    def build_compile_latex_tool(self):
        @function_tool
        def compile_latex_pdf(latex_source: str, output_basename: str = "isomorphic_questions") -> dict[str, Any]:
            """
            Compile a complete standalone LaTeX document into a PDF inside the shared workflow container.

            Args:
                latex_source: Full LaTeX source, including documentclass, preamble, and document body.
                output_basename: Basename for the generated .tex and .pdf files.
            """

            result = self.compile_latex(latex_source, output_basename)
            return result.as_dict()

        return compile_latex_pdf
