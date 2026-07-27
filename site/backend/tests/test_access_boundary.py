"""Regression tests for the optional production API access boundary."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch
from urllib.error import URLError

from backend.src.auth import (
    AccessBoundaryConfig,
    is_cost_bearing_post,
    is_rate_limited_request,
)
from backend.src.server import create_app
from backend.tests.support import IdempotentTestClient as TestClient


class AccessBoundaryTests(unittest.TestCase):
    token = "test-access-token-with-at-least-32-characters"
    trusted_origin = "https://professor.example"

    def _client(
        self,
        *,
        token: str = "",
        rate_limit_requests: int = 10,
        rate_limit_window_seconds: int = 60,
    ) -> TestClient:
        config = AccessBoundaryConfig(
            access_token=token,
            rate_limit_requests=rate_limit_requests,
            rate_limit_window_seconds=rate_limit_window_seconds,
            trusted_origins=(self.trusted_origin,),
        )
        return TestClient(create_app(access_config=config))

    def test_auth_disabled_preserves_local_api_and_docs(self) -> None:
        client = self._client()
        self.assertEqual(client.get("/api/agents").status_code, 200)
        self.assertEqual(client.get("/docs").status_code, 200)
        self.assertTrue(client.get("/api/readiness").json()["authenticated"])

    def test_app_access_token_environment_switches_the_boundary(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("APP_ACCESS_TOKEN", None)
            self.assertEqual(TestClient(create_app()).get("/api/agents").status_code, 200)

        with patch.dict(os.environ, {"APP_ACCESS_TOKEN": self.token}):
            client = TestClient(create_app())
            self.assertEqual(client.get("/api/agents").status_code, 401)
            self.assertEqual(
                client.get(
                    "/api/agents",
                    headers={"X-App-Access-Token": self.token},
                ).status_code,
                200,
            )

    def test_rate_limit_environment_is_configurable(self) -> None:
        with patch.dict(
            os.environ,
            {
                "APP_ACCESS_TOKEN": self.token,
                "APP_RATE_LIMIT_REQUESTS": "3",
                "APP_RATE_LIMIT_WINDOW_SECONDS": "45",
            },
        ):
            config = AccessBoundaryConfig.from_env()
        self.assertTrue(config.auth_required)
        self.assertEqual(config.rate_limit_requests, 3)
        self.assertEqual(config.rate_limit_window_seconds, 45)

    def test_environment_rejects_weak_or_malformed_access_tokens(self) -> None:
        for token in (
            "short-token",
            "x" * 513,
            "valid-length-token-that-contains spaces",
        ):
            with self.subTest(token_length=len(token)):
                with patch.dict(os.environ, {"APP_ACCESS_TOKEN": token}):
                    with self.assertRaisesRegex(ValueError, "APP_ACCESS_TOKEN"):
                        AccessBoundaryConfig.from_env()

    def test_auth_enabled_rejects_missing_bad_and_noncanonical_headers(self) -> None:
        client = self._client(token=self.token)
        self.assertEqual(client.get("/api/agents").status_code, 401)
        self.assertEqual(
            client.get("/api/agents", headers={"X-App-Access-Token": "wrong"}).status_code,
            401,
        )
        self.assertEqual(
            client.get("/api/agents", headers={"Authorization": f"Bearer {self.token}"}).status_code,
            401,
        )
        response = client.get("/api/agents", headers={"X-App-Access-Token": self.token})
        self.assertEqual(response.status_code, 200)
        duplicate = client.get(
            "/api/agents",
            headers=[
                ("X-App-Access-Token", self.token),
                ("X-App-Access-Token", self.token),
            ],
        )
        self.assertEqual(duplicate.status_code, 401)

    def test_public_allowlist_stays_public_when_auth_is_enabled(self) -> None:
        client = self._client(token=self.token)
        self.assertEqual(client.get("/api/health").status_code, 200)
        public_config = client.get("/api/public-config")
        self.assertEqual(public_config.status_code, 200)
        self.assertEqual(public_config.json()["maxInstructionChars"], 20_000)
        self.assertEqual(client.get("/version.json").status_code, 200)
        with patch("backend.src.api.health.urlopen", side_effect=URLError("offline")):
            self.assertEqual(client.get("/api/openai/status").status_code, 200)

        self.assertEqual(client.get("/api/deployment/status").status_code, 401)
        self.assertEqual(client.get("/api/readiness").status_code, 401)

    def test_readiness_requires_auth_and_discloses_no_secret_or_path(self) -> None:
        client = self._client(token=self.token)
        response = client.get(
            "/api/readiness",
            headers={"X-App-Access-Token": self.token},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.json()),
            {"authenticated", "openaiApiKeyConfigured"},
        )
        self.assertTrue(response.json()["authenticated"])
        serialized = response.text.lower()
        self.assertNotIn(self.token.lower(), serialized)
        self.assertNotIn("/users/", serialized)

    def test_docs_are_disabled_when_auth_is_required(self) -> None:
        client = self._client(token=self.token)
        self.assertEqual(client.get("/docs").status_code, 401)
        self.assertEqual(
            client.get("/docs", headers={"X-App-Access-Token": self.token}).status_code,
            404,
        )
        self.assertEqual(
            client.get("/redoc", headers={"X-App-Access-Token": self.token}).status_code,
            404,
        )
        self.assertEqual(
            client.get("/openapi.json", headers={"X-App-Access-Token": self.token}).status_code,
            404,
        )

    def test_rate_limit_runs_after_auth_and_sets_retry_after(self) -> None:
        client = self._client(
            token=self.token,
            rate_limit_requests=1,
            rate_limit_window_seconds=60,
        )
        endpoint = "/api/workflow/start"

        # Rejected authentication must not consume the only permitted request.
        for _ in range(3):
            self.assertEqual(client.post(endpoint).status_code, 401)

        with patch(
            "backend.src.api.workflows.get_openai_client",
            side_effect=RuntimeError("offline"),
        ):
            first = client.post(endpoint, headers={"X-App-Access-Token": self.token})
            second = client.post(endpoint, headers={"X-App-Access-Token": self.token})

        self.assertEqual(first.status_code, 503)
        self.assertEqual(second.status_code, 429)
        self.assertGreaterEqual(int(second.headers["Retry-After"]), 1)

    def test_rate_limit_also_bounds_local_compatibility_mode(self) -> None:
        client = self._client(rate_limit_requests=1, rate_limit_window_seconds=60)
        with patch(
            "backend.src.api.workflows.get_openai_client",
            side_effect=RuntimeError("offline"),
        ):
            first = client.post("/api/workflow/start")
            second = client.post("/api/workflow/start")
        self.assertEqual(first.status_code, 503)
        self.assertEqual(second.status_code, 429)

    def test_rate_limits_are_isolated_per_cost_route(self) -> None:
        client = self._client(
            token=self.token,
            rate_limit_requests=1,
            rate_limit_window_seconds=60,
        )
        headers = {"X-App-Access-Token": self.token}
        with (
            patch(
                "backend.src.api.workflows.get_openai_client",
                side_effect=RuntimeError("offline"),
            ),
            patch(
                "backend.src.api.batch.get_openai_client",
                side_effect=RuntimeError("offline"),
            ),
        ):
            workflow = client.post("/api/workflow/start", headers=headers)
            create_data = client.post("/api/create-data/start", headers=headers)
        self.assertEqual(workflow.status_code, 503)
        self.assertEqual(create_data.status_code, 503)

    def test_cost_bearing_route_matcher_is_narrow_and_complete(self) -> None:
        for path in (
            "/api/workflow/start",
            "/api/workflow/rerun-from-agent",
            "/api/workflow/start-from-agent-input",
            "/api/create-data/start",
            "/api/eval-batch/start",
            "/api/outputs/run-name/compare-professor",
            "/api/outputs/07-27-2026/12-00-00/compare-professor",
        ):
            self.assertTrue(is_cost_bearing_post("POST", path), path)
        self.assertFalse(is_cost_bearing_post("GET", "/api/workflow/start"))
        self.assertFalse(is_cost_bearing_post("POST", "/api/upload"))
        self.assertTrue(is_rate_limited_request("POST", "/api/upload"))
        self.assertFalse(is_cost_bearing_post("POST", "/api/create-data/job-id/stop"))
        self.assertTrue(is_rate_limited_request("GET", "/api/download-zip/workflow-id"))

    def test_public_config_exposes_only_valid_paypal_urls(self) -> None:
        client = self._client(token=self.token)
        valid_urls = (
            "https://www.paypal.com/donate/?hosted_button_id=example",
            "https://paypal.me/example",
        )
        for url in valid_urls:
            with self.subTest(url=url), patch.dict(os.environ, {"PAYPAL_DONATE_URL": url}):
                self.assertEqual(client.get("/api/public-config").json()["paypalDonateUrl"], url)

        invalid_urls = (
            "http://paypal.me/example",
            "https://evilpaypal.com/example",
            "https://paypal.com.example.test/donate",
            "https://paypal.com@evil.example/donate",
            "https://paypal.com:8443/donate",
            "javascript:alert(1)",
        )
        for url in invalid_urls:
            with self.subTest(url=url), patch.dict(os.environ, {"PAYPAL_DONATE_URL": url}):
                self.assertNotIn("paypalDonateUrl", client.get("/api/public-config").json())

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PAYPAL_DONATE_URL", None)
            payload = client.get("/api/public-config").json()
            self.assertTrue(payload["authRequired"])
            self.assertEqual(payload["evaluationPolicy"]["maxItems"], 100)
            self.assertGreater(payload["evaluationPolicy"]["graderCount"], 0)
            self.assertEqual(payload["createDataPolicy"]["maxEntries"], 5)
            self.assertNotIn("paypalDonateUrl", payload)

    def test_health_is_liveness_only(self) -> None:
        response = self._client(token=self.token).get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "healthy"})
        serialized = response.text.lower()
        for forbidden in ("openai", "api_key", "uploads", "outputs", "/users/"):
            self.assertNotIn(forbidden, serialized)

    def test_unsafe_api_methods_reject_hostile_origin_without_auth(self) -> None:
        client = self._client()
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                response = client.request(
                    method,
                    "/api/create-data/missing/stop",
                    headers={"Origin": "https://attacker.example"},
                )
                self.assertEqual(response.status_code, 403)

        # Origin enforcement is limited to unsafe API methods.
        response = client.get(
            "/api/agents",
            headers={"Origin": "https://attacker.example"},
        )
        self.assertEqual(response.status_code, 200)

    def test_trusted_or_missing_origin_preserves_browser_and_cli_requests(self) -> None:
        client = self._client()
        trusted = client.post(
            "/api/create-data/missing/stop",
            headers={"Origin": self.trusted_origin},
        )
        cli = client.post("/api/create-data/missing/stop")
        self.assertEqual(trusted.status_code, 404)
        self.assertEqual(cli.status_code, 404)

    def test_null_duplicate_and_near_match_origins_are_rejected(self) -> None:
        client = self._client()
        for origin in (
            "null",
            f"{self.trusted_origin}.attacker.example",
            f"{self.trusted_origin}/",
        ):
            with self.subTest(origin=origin):
                response = client.post(
                    "/api/create-data/missing/stop",
                    headers={"Origin": origin},
                )
                self.assertEqual(response.status_code, 403)

        duplicate = client.post(
            "/api/create-data/missing/stop",
            headers=[
                ("Origin", self.trusted_origin),
                ("Origin", self.trusted_origin),
            ],
        )
        self.assertEqual(duplicate.status_code, 403)

    def test_valid_token_does_not_bypass_hostile_origin(self) -> None:
        client = self._client(token=self.token)
        response = client.post(
            "/api/create-data/missing/stop",
            headers={
                "Origin": "https://attacker.example",
                "X-App-Access-Token": self.token,
            },
        )
        self.assertEqual(response.status_code, 403)

        trusted_without_token = client.post(
            "/api/create-data/missing/stop",
            headers={"Origin": self.trusted_origin},
        )
        self.assertEqual(trusted_without_token.status_code, 401)
        self.assertEqual(
            trusted_without_token.headers["access-control-allow-origin"],
            self.trusted_origin,
        )

    def test_cors_preflight_remains_available_without_access_token(self) -> None:
        client = self._client(token=self.token)
        response = client.options(
            "/api/workflow/start",
            headers={
                "Origin": self.trusted_origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-App-Access-Token",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["access-control-allow-origin"],
            self.trusted_origin,
        )

    def test_app_factory_shares_configured_cors_origins_with_boundary(self) -> None:
        with patch.dict(
            os.environ,
            {"ALLOWED_ORIGINS": self.trusted_origin},
        ):
            client = TestClient(
                create_app(
                    access_config=AccessBoundaryConfig(
                        access_token="",
                        rate_limit_requests=10,
                        rate_limit_window_seconds=60,
                    )
                )
            )
        allowed = client.post(
            "/api/create-data/missing/stop",
            headers={"Origin": self.trusted_origin},
        )
        denied = client.post(
            "/api/create-data/missing/stop",
            headers={"Origin": "https://attacker.example"},
        )
        self.assertEqual(allowed.status_code, 404)
        self.assertEqual(denied.status_code, 403)


if __name__ == "__main__":
    unittest.main()
