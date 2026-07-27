"""Evaluation pipeline helpers for batch eval runs (eval without full workflow)."""
from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from agents import RunConfig, Runner

from backend.src.container import (
    SharedWorkflowRuntime,
    allowed_domains_from_env,
    container_network_policy,
)
from backend.src.cost_controls import (
    max_generated_questions,
    max_source_questions,
    require_within_limit,
)
from backend.src.helpers import build_json_message, upload_file_to_openai
from backend.src.provider_timeouts import (
    agent_stage_timeout_seconds,
    await_with_deadline,
    drain_task_outcome,
)
from backend.src.registry import agent_entry
from backend.src.safety import safe_exception_category
from backend.src.schemas import ExtracterOutput, NormalizerOutput, SourceQuestionItem
from backend.src.workflow import container_expires_minutes, create_agents
from backend.src.workflow.guardrails import (
    GuardrailDecision,
    check_content_guardrails,
)

logger = logging.getLogger(__name__)

ToThread = Callable[..., Awaitable[Any]]
EXTRACTER_AGENT_KEY = "extracter"


class EvalPdfGuardrailBlockedError(ValueError):
    """Raised when an uploaded evaluation PDF fails a workflow guardrail."""

    def __init__(self, decision: GuardrailDecision) -> None:
        self.decision = decision
        super().__init__(
            decision.reason
            or "The uploaded evaluation PDF was blocked by a content guardrail."
        )


def _make_extraction_config() -> NormalizerOutput:
    return NormalizerOutput(new_questions_per_question=1, notes="Evaluation-only extraction; do not generate or rewrite questions.")


def _extracter_max_turns() -> int:
    """Return the validated registry turn cap for eval-only PDF extraction."""
    return int(agent_entry(EXTRACTER_AGENT_KEY).get("maxTurns", 6))


def _extraction_payload(pdf_path: Path, config: NormalizerOutput, *, generated_pdf: bool) -> dict[str, Any]:
    source_kind = "generated questions" if generated_pdf else "source questions"
    return {
        "task": "extract_source_questions",
        "files": [pdf_path.name],
        "normalizer_config": config.model_dump(),
        "requirements": {
            "purpose": f"Extract {source_kind} exactly as written for evaluation.",
            "preserve_math_latex": True,
            "extract_metadata": ["title", "theme", "author", "date", "course"],
            "determine_title_from_source": True,
            "determine_theme_from_extracted_information": True,
            "must_include_multiple_choice": True,
            "multiple_choice_option_keys": ["A", "B", "C", "D"],
            "set_has_explicit_answer_only_if_marked": True,
            "preserve_question_order": True,
            "do_not_generate_new_questions": True,
            "do_not_rewrite_or_simplify_questions": True,
            "include_explanations_when_present": True,
            "difficulty_rule": "leave question difficulty empty unless normalizer_config.difficulty is non-empty",
        },
    }


class EvalPdfExtractionRuntime:
    """Bounded batch lifecycle with strict one-container-per-PDF isolation.

    A processed PDF is untrusted and may cause the Extracter to write arbitrary
    files or state inside Code Interpreter. Deleting only its attachment cannot
    prove that state is gone, so every extraction gets a fresh container and the
    whole container is deleted before another PDF can start.
    """

    def __init__(
        self,
        *,
        client,
        run_config: RunConfig,
        output_dir: Path,
        to_thread: ToThread = asyncio.to_thread,
        max_files: int = 1,
    ) -> None:
        if isinstance(max_files, bool) or not isinstance(max_files, int) or max_files < 1:
            raise ValueError("max_files must be a positive integer")
        self.client = client
        self.run_config = run_config
        self.output_dir = output_dir
        self.to_thread = to_thread
        self.max_files = max_files
        self.extraction_count = 0
        self._lock = asyncio.Lock()
        self._closed = False

    async def __aenter__(self) -> EvalPdfExtractionRuntime:
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
        await self.aclose()

    async def _cleanup_call(
        self,
        func: Callable[..., Any] | None,
        *args: Any,
        resource_kind: str,
        resource_id: str,
        **kwargs: Any,
    ) -> bool:
        if not resource_id:
            return True
        if not callable(func):
            logger.warning(
                "Could not delete temporary OpenAI eval %s %s "
                "(cleanup API unavailable)",
                resource_kind,
                resource_id,
            )
            return False
        task = asyncio.create_task(self.to_thread(func, *args, **kwargs))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as cancellation:
            # A blocking SDK cleanup already running in a worker cannot be
            # cancelled safely. Wait for it before preserving cancellation.
            cleanup_result = await drain_task_outcome(task)
            if isinstance(cleanup_result, BaseException):
                self._closed = True
                logger.warning(
                    "Could not delete temporary OpenAI eval %s %s (%s)",
                    resource_kind,
                    resource_id,
                    safe_exception_category(cleanup_result),
                )
            raise cancellation
        except Exception as exc:
            logger.warning(
                "Could not delete temporary OpenAI eval %s %s (%s)",
                resource_kind,
                resource_id,
                safe_exception_category(exc),
            )
            return False
        return True

    def _validate_client(self) -> None:
        containers = getattr(self.client, "containers", None)
        container_files = getattr(containers, "files", None)
        provider_files = getattr(self.client, "files", None)
        if (
            containers is None
            or not callable(getattr(containers, "create", None))
            or not callable(getattr(containers, "delete", None))
            or not callable(getattr(container_files, "create", None))
            or not callable(getattr(provider_files, "delete", None))
        ):
            raise RuntimeError(
                "OpenAI SDK does not support isolated container files and cleanup; "
                "upgrade openai to a version with the complete Containers Files API"
            )

    async def _heartbeat_loop(
        self,
        retrieve: Callable[..., Any],
        container_id: str,
    ) -> None:
        interval = max(30, min(container_expires_minutes() * 60 // 2, 300))
        while True:
            await asyncio.sleep(interval)
            retrieve_task = asyncio.create_task(
                self.to_thread(retrieve, container_id)
            )
            try:
                await asyncio.shield(retrieve_task)
            except asyncio.CancelledError as cancellation:
                await drain_task_outcome(retrieve_task)
                raise cancellation
            except Exception as exc:
                logger.warning(
                    "Evaluation PDF container heartbeat failed (%s)",
                    safe_exception_category(exc),
                )

    async def _upload_provider_file(self, pdf_path: Path) -> str:
        upload_task = asyncio.create_task(
            self.to_thread(
                upload_file_to_openai,
                self.client,
                pdf_path,
                logger,
            )
        )
        try:
            return str(await asyncio.shield(upload_task))
        except asyncio.CancelledError as cancellation:
            outcome = await drain_task_outcome(upload_task)
            file_id = "" if isinstance(outcome, BaseException) else str(outcome)
            cleanup_task = asyncio.create_task(
                self._cleanup_call(
                    getattr(getattr(self.client, "files", None), "delete", None),
                    file_id,
                    resource_kind="input file",
                    resource_id=file_id,
                )
            )
            cleanup_outcome = await drain_task_outcome(cleanup_task)
            cleanup_succeeded = cleanup_outcome is True
            if not cleanup_succeeded:
                self._closed = True
            raise cancellation

    async def _attach_container_file(
        self,
        *,
        container_id: str,
        file_id: str,
    ) -> None:
        create = self.client.containers.files.create
        attach_task = asyncio.create_task(
            self.to_thread(
                create,
                container_id,
                file_id=file_id,
            )
        )
        try:
            await asyncio.shield(attach_task)
        except asyncio.CancelledError as cancellation:
            # The whole container is deleted by the outer finally block, but a
            # worker-thread attachment must finish before that deletion begins.
            await drain_task_outcome(attach_task)
            raise cancellation

    async def _extract_isolated(
        self,
        *,
        pdf_path: Path,
        generated_pdf: bool = False,
    ) -> ExtracterOutput:
        """Bootstrap, process, and destroy one PDF's isolated provider scope."""
        self._validate_client()
        allowed_domains = allowed_domains_from_env()
        container_id = ""
        file_id = ""
        heartbeat_task: asyncio.Task[None] | None = None
        cleanup_cancelled: asyncio.CancelledError | None = None
        try:
            create_task = asyncio.create_task(
                self.to_thread(
                    self.client.containers.create,
                    name="isomorphic-eval-pdf-extraction",
                    expires_after={
                        "anchor": "last_active_at",
                        "minutes": container_expires_minutes(),
                    },
                    file_ids=[],
                    network_policy=container_network_policy(allowed_domains),
                )
            )
            try:
                container = await asyncio.shield(create_task)
            except asyncio.CancelledError as cancellation:
                outcome = await drain_task_outcome(create_task)
                if not isinstance(outcome, BaseException):
                    container_id = str(outcome.id)
                raise cancellation
            container_id = str(container.id)

            shared_runtime = SharedWorkflowRuntime(
                client=self.client,
                container_id=container_id,
                output_dir=self.output_dir,
                allowed_domains=allowed_domains,
            )
            bootstrap_task = asyncio.create_task(
                self.to_thread(shared_runtime.bootstrap)
            )
            try:
                await asyncio.shield(bootstrap_task)
            except asyncio.CancelledError as cancellation:
                await drain_task_outcome(bootstrap_task)
                raise cancellation

            agents = create_agents(
                container_id=container_id,
                agent_keys={EXTRACTER_AGENT_KEY},
            )
            retrieve = getattr(self.client.containers, "retrieve", None)
            if callable(retrieve):
                heartbeat_task = asyncio.create_task(
                    self._heartbeat_loop(retrieve, container_id)
                )

            # Upload untrusted bytes only after the declared document
            # dependencies have completed installation and verification.
            file_id = await self._upload_provider_file(pdf_path)
            await self._attach_container_file(
                container_id=container_id,
                file_id=file_id,
            )
            config = _make_extraction_config()
            payload = _extraction_payload(
                pdf_path,
                config,
                generated_pdf=generated_pdf,
            )
            file_refs = [{"type": "input_file", "file_id": file_id}]
            extracter_input = build_json_message(payload, file_refs=file_refs)
            max_turns = _extracter_max_turns()
            result = await await_with_deadline(
                Runner.run(
                    agents[EXTRACTER_AGENT_KEY],
                    input=extracter_input,
                    run_config=self.run_config,
                    max_turns=max_turns,
                ),
                operation="Eval PDF Extracter",
                timeout_seconds=agent_stage_timeout_seconds(),
            )
            extraction: ExtracterOutput = result.final_output
            if not extraction.questions:
                retry_payload = {
                    **payload,
                    "task": "extract_source_questions_retry",
                    "reason": "previous_result_had_zero_questions",
                    "requirements": {
                        **payload["requirements"],
                        "must_return_all_questions": True,
                    },
                }
                retry_result = await await_with_deadline(
                    Runner.run(
                        agents[EXTRACTER_AGENT_KEY],
                        input=build_json_message(
                            retry_payload,
                            file_refs=file_refs,
                        ),
                        run_config=self.run_config,
                        max_turns=max_turns,
                    ),
                    operation="Eval PDF Extracter retry",
                    timeout_seconds=agent_stage_timeout_seconds(),
                )
                extraction = retry_result.final_output
            for question in extraction.questions:
                if question.options:
                    question.question_type = "multiple_choice"
            extraction.filename = extraction.filename or pdf_path.name
            if generated_pdf:
                require_within_limit(
                    "Generated question",
                    len(extraction.questions),
                    max_generated_questions(),
                    "MAX_GENERATED_QUESTIONS",
                )
            else:
                require_within_limit(
                    "Source question",
                    len(extraction.questions),
                    max_source_questions(),
                    "MAX_SOURCE_QUESTIONS",
                )

            decision = await check_content_guardrails(
                client=self.client,
                extracter_output=extraction,
                normalizer_output=config,
                to_thread=self.to_thread,
                run_config=self.run_config,
            )
            if not decision.allowed:
                raise EvalPdfGuardrailBlockedError(decision)
            return extraction
        finally:
            active_exception = sys.exc_info()[0] is not None
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                await drain_task_outcome(heartbeat_task)

            cleanup_succeeded = True
            for func, args, kind, identifier in (
                (
                    getattr(getattr(self.client, "containers", None), "delete", None),
                    (container_id,),
                    "container",
                    container_id,
                ),
                (
                    getattr(getattr(self.client, "files", None), "delete", None),
                    (file_id,),
                    "input file",
                    file_id,
                ),
            ):
                try:
                    cleanup_succeeded = (
                        await self._cleanup_call(
                            func,
                            *args,
                            resource_kind=kind,
                            resource_id=identifier,
                        )
                        and cleanup_succeeded
                    )
                except asyncio.CancelledError as exc:
                    cleanup_cancelled = exc
            if not cleanup_succeeded:
                self._closed = True
                if not active_exception and cleanup_cancelled is None:
                    raise RuntimeError(
                        "Evaluation PDF provider cleanup failed; "
                        "no further files will be extracted"
                    )
            if cleanup_cancelled is not None:
                raise cleanup_cancelled

    async def extract(
        self,
        *,
        pdf_path: Path,
        generated_pdf: bool = False,
    ) -> ExtracterOutput:
        """Extract one PDF only after reserving its bounded isolated session."""
        async with self._lock:
            if self._closed:
                raise RuntimeError("Evaluation PDF extraction runtime is closed")
            if self.extraction_count >= self.max_files:
                raise RuntimeError(
                    "Evaluation PDF extraction limit reached "
                    f"({self.max_files} file(s))"
                )
            self.extraction_count += 1
            return await self._extract_isolated(
                pdf_path=pdf_path,
                generated_pdf=generated_pdf,
            )

    async def aclose(self) -> None:
        """Prevent new sessions after any in-flight isolated extraction ends."""
        async with self._lock:
            self._closed = True


async def extract_questions_from_pdf(
    *,
    client,
    pdf_path: Path,
    run_config: RunConfig,
    generated_pdf: bool = False,
    to_thread: ToThread = asyncio.to_thread,
    runtime: EvalPdfExtractionRuntime | None = None,
) -> ExtracterOutput:
    """Extract and screen one PDF within a caller-owned bounded lifecycle."""
    if runtime is not None:
        if runtime.client is not client or runtime.run_config is not run_config:
            raise ValueError(
                "The eval extraction lifecycle must use this client and RunConfig"
            )
        return await runtime.extract(
            pdf_path=pdf_path,
            generated_pdf=generated_pdf,
        )

    owned_runtime = EvalPdfExtractionRuntime(
        client=client,
        run_config=run_config,
        output_dir=pdf_path.parent,
        to_thread=to_thread,
    )
    async with owned_runtime:
        return await owned_runtime.extract(
            pdf_path=pdf_path,
            generated_pdf=generated_pdf,
        )

def question_to_generated_item(question: SourceQuestionItem, *, source_id: str, index: int, pdf_name: str) -> dict[str, Any]:
    raw = question.source_raw.strip()
    stem = question.stem.strip() or raw
    question_type = question.question_type or (
        "multiple_choice"
        if question.options
        else "multi_part"
        if question.parts
        else "open_ended"
    )
    return {
        "id": index, "source_id": source_id,
        "stem": stem,
        "options": [o.model_dump() for o in question.options],
        "difficulty": question.difficulty,
        "tags": list(question.tags),
        "generation_notes": f"Extracted from existing PDF: {pdf_name}",
        "question_type": question_type,
        "skipped": False, "skip_reason": "", "answer": question.answer,
        "parts": [{"part_label": p.part_label, "stem": p.stem, "question_type": p.question_type, "options": [o.model_dump() for o in p.options], "answer": p.answer} for p in question.parts],
    }

def build_eval_final_result(*, source_extraction: ExtracterOutput, generated_extraction: ExtracterOutput, generated_pdf: Path) -> dict[str, Any]:
    source_qs = source_extraction.questions
    generated_extracted = generated_extraction.questions
    source_ids = [question.source_id.strip() for question in source_qs]
    generated_ids = [question.source_id.strip() for question in generated_extracted]
    if len(source_qs) != len(generated_extracted):
        raise ValueError(
            "Evaluation PDF pairing requires the same number of source and generated questions; "
            f"got {len(source_qs)} source and {len(generated_extracted)} generated"
        )
    if (
        any(not source_id for source_id in source_ids)
        or len(set(source_ids)) != len(source_ids)
    ):
        raise ValueError("Source PDF extraction must contain unique, non-empty source IDs")
    if (
        any(not source_id for source_id in generated_ids)
        or len(set(generated_ids)) != len(generated_ids)
    ):
        raise ValueError("Generated PDF extraction must contain unique, non-empty source IDs")
    if set(generated_ids) != set(source_ids):
        raise ValueError(
            "Generated PDF source IDs do not match the source PDF; refusing positional pairing"
        )

    generated_qs = []
    for i, gen in enumerate(generated_extracted, start=1):
        generated_qs.append(
            question_to_generated_item(
                gen,
                source_id=gen.source_id.strip(),
                index=i,
                pdf_name=generated_pdf.name,
            )
        )
    config = NormalizerOutput(new_questions_per_question=1, theme=source_extraction.theme or "", title=source_extraction.title or "Evaluation Run", notes="Evaluation-only run.")
    return {"config": config.model_dump(), "source_questions": [q.model_dump() for q in source_qs], "verified_questions": [q.model_dump() for q in source_qs], "generated_questions": generated_qs}
