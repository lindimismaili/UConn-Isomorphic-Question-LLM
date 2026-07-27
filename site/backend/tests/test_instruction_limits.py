"""Instruction-size admission regressions for every paid workflow entrypoint."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.src.safety import MAX_INSTRUCTION_CHARS, validate_instruction_text
from backend.src.server import app
from backend.tests.support import IdempotentTestClient


def test_instruction_validator_normalizes_and_bounds_text() -> None:
    assert validate_instruction_text("  make it harder  ") == "make it harder"
    assert validate_instruction_text("x" * MAX_INSTRUCTION_CHARS) == (
        "x" * MAX_INSTRUCTION_CHARS
    )
    with pytest.raises(ValueError, match="at most 20000"):
        validate_instruction_text("x" * (MAX_INSTRUCTION_CHARS + 1))


@pytest.mark.parametrize(
    ("path", "data", "files", "provider_patch"),
    [
        (
            "/api/workflow/start",
            {},
            None,
            "backend.src.api.workflows.get_openai_client",
        ),
        (
            "/api/workflow/rerun-from-agent",
            {"sourceOutputFolder": "unused", "startAgent": "generator"},
            None,
            "backend.src.api.workflows.get_openai_client",
        ),
        (
            "/api/workflow/start-from-agent-input",
            {"startAgent": "verifier"},
            {
                "agentInput": (
                    "input.json",
                    b"{}",
                    "application/json",
                )
            },
            "backend.src.api.workflows.get_openai_client",
        ),
        (
            "/api/create-data/start",
            {"confirmWorkflowCosts": "true"},
            None,
            "backend.src.api.batch.get_openai_client",
        ),
    ],
)
def test_paid_http_starts_reject_oversized_instruction_before_provider(
    path: str,
    data: dict[str, str],
    files,
    provider_patch: str,
) -> None:
    request_data = {
        **data,
        "prompt": "x" * (MAX_INSTRUCTION_CHARS + 1),
    }
    with patch(provider_patch) as provider, IdempotentTestClient(app) as client:
        response = client.post(
            path,
            data=request_data,
            files=files,
        )

    assert response.status_code == 400
    assert "at most 20000" in response.json()["detail"]
    provider.assert_not_called()
