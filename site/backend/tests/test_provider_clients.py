"""Agents SDK provider ownership and transport-policy regressions."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from backend.src import provider_clients


class _FakeAsyncClient:
    instances: list["_FakeAsyncClient"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.close_calls = 0
        self.instances.append(self)

    async def close(self) -> None:
        self.close_calls += 1


class _FakeProvider:
    instances: list["_FakeProvider"] = []

    def __init__(self, *, openai_client) -> None:
        self.openai_client = openai_client
        self.close_calls = 0
        self.instances.append(self)

    async def aclose(self) -> None:
        self.close_calls += 1


class ProviderClientLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        asyncio.run(provider_clients.close_agents_sdk_model_provider())
        _FakeAsyncClient.instances.clear()
        _FakeProvider.instances.clear()

    def tearDown(self) -> None:
        asyncio.run(provider_clients.close_agents_sdk_model_provider())

    def test_provider_is_singleton_bounded_and_resettable(self) -> None:
        transport = {"timeout": 17.0, "max_retries": 0}
        with (
            patch.object(
                provider_clients,
                "AsyncOpenAI",
                _FakeAsyncClient,
            ),
            patch.object(
                provider_clients,
                "OpenAIProvider",
                _FakeProvider,
            ),
            patch.object(
                provider_clients,
                "openai_transport_options",
                return_value=transport,
            ),
        ):
            first = provider_clients.configure_agents_sdk_model_provider(
                "test-key-one"
            )
            second = provider_clients.configure_agents_sdk_model_provider(
                "test-key-one"
            )
            self.assertIs(first, second)
            self.assertEqual(len(_FakeAsyncClient.instances), 1)
            self.assertEqual(
                _FakeAsyncClient.instances[0].kwargs,
                {"api_key": "test-key-one", **transport},
            )
            with self.assertRaisesRegex(RuntimeError, "restart"):
                provider_clients.configure_agents_sdk_model_provider(
                    "test-key-two"
                )

            asyncio.run(provider_clients.close_agents_sdk_model_provider())
            self.assertEqual(first.close_calls, 1)
            self.assertEqual(_FakeAsyncClient.instances[0].close_calls, 1)
            self.assertIsNone(
                provider_clients.configured_agents_sdk_model_provider()
            )

            replacement = (
                provider_clients.configure_agents_sdk_model_provider(
                    "test-key-two"
                )
            )
            self.assertIsNot(replacement, first)


if __name__ == "__main__":
    unittest.main()
