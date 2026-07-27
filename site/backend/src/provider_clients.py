"""Application-owned OpenAI Agents SDK client configuration.

The backend also uses a synchronous :class:`openai.OpenAI` client for Files,
Containers, and Evals.  Agents SDK model calls use an ``AsyncOpenAI`` client,
so they need the same finite timeout and retry policy explicitly; otherwise the
SDK lazily creates a separate client with its own defaults.
"""

from __future__ import annotations

import hashlib
import threading

from agents.models.openai_provider import OpenAIProvider
from openai import AsyncOpenAI

from backend.src.provider_timeouts import openai_transport_options

_provider_lock = threading.Lock()
_agents_sdk_client: AsyncOpenAI | None = None
_agents_sdk_provider: OpenAIProvider | None = None
_api_key_digest = ""


def _key_digest(api_key: str) -> str:
    """Identify key changes without retaining an extra plaintext copy."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def configure_agents_sdk_model_provider(api_key: str) -> OpenAIProvider:
    """Return the process-owned Agents SDK provider with bounded transport policy.

    Initialization is protected for callers from multiple request threads.  A
    running process never silently switches credentials underneath in-flight
    model calls; rotating the API key requires the normal service restart.
    """
    cleaned_key = (api_key or "").strip()
    if not cleaned_key:
        raise RuntimeError("OPENAI_API_KEY is required to configure the Agents SDK.")
    digest = _key_digest(cleaned_key)

    global _agents_sdk_client, _agents_sdk_provider, _api_key_digest
    with _provider_lock:
        if _agents_sdk_provider is not None:
            if digest != _api_key_digest:
                raise RuntimeError(
                    "OPENAI_API_KEY changed while the process was running; "
                    "restart the service before starting more paid work."
                )
            return _agents_sdk_provider

        client = AsyncOpenAI(
            api_key=cleaned_key,
            **openai_transport_options(),
        )
        provider = OpenAIProvider(openai_client=client)
        _agents_sdk_client = client
        _agents_sdk_provider = provider
        _api_key_digest = digest
        return provider


def configured_agents_sdk_model_provider() -> OpenAIProvider | None:
    """Return the configured provider, or ``None`` for offline/injected tests."""
    with _provider_lock:
        return _agents_sdk_provider


async def close_agents_sdk_model_provider() -> None:
    """Close and reset the application-owned Agents SDK provider."""
    global _agents_sdk_client, _agents_sdk_provider, _api_key_digest
    with _provider_lock:
        client = _agents_sdk_client
        provider = _agents_sdk_provider
        _agents_sdk_client = None
        _agents_sdk_provider = None
        _api_key_digest = ""

    if provider is not None:
        await provider.aclose()
    if client is not None:
        await client.close()
