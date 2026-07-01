# Agents SDK Preview Compatibility Fix

## Problem

The backend imports `ModelRefusalError`, which is available in current
`openai-agents` releases but absent from the existing project environment's
version 0.13.2. `deploy/preview.sh` considers Python dependencies ready when
only `fastapi` and `uvicorn` import successfully, so it does not update an
otherwise populated but incompatible environment.

## Design

Raise the `openai-agents` minimum version to the first verified release that
exports `ModelRefusalError`. Extend the preview dependency probe to import that
exception alongside `fastapi` and `uvicorn`. If the probe fails, the existing
requirements installation path updates the environment before starting the
backend.

This keeps dependency installation conditional, preserves the preview script's
current environment-selection behavior, and avoids weakening the backend's
refusal handling.

## Verification

Add a regression test for the preview dependency probe. Demonstrate that it
fails against the old probe, then passes after the script change. Update the
active environment through the normal requirements file, verify the backend
module imports, exercise the preview health endpoint, and run the repository's
backend and frontend test/lint suites.

## Scope

Only the dependency declaration, preview dependency readiness check, and its
regression coverage are in scope. No agent, guardrail, workflow, or frontend
behavior changes are included.
