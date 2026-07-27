"""Optional API token, browser-origin boundary, and resource rate limiting."""

from __future__ import annotations

import math
import os
import re
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

ACCESS_TOKEN_HEADER = "X-App-Access-Token"
DEFAULT_RATE_LIMIT_REQUESTS = 12
DEFAULT_RATE_LIMIT_WINDOW_SECONDS = 60
MIN_ACCESS_TOKEN_LENGTH = 32
MAX_ACCESS_TOKEN_LENGTH = 512
_ACCESS_TOKEN_PATTERN = re.compile(
    rf"[A-Za-z0-9._~+/=-]{{{MIN_ACCESS_TOKEN_LENGTH},{MAX_ACCESS_TOKEN_LENGTH}}}"
)
_UNSAFE_API_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_PUBLIC_GET_PATHS = frozenset(
    {
        "/api/health",
        "/api/openai/status",
        "/api/public-config",
        "/version.json",
    }
)
_DOC_PATH_PREFIXES = ("/docs", "/redoc")
_COST_BEARING_POST_PATHS = frozenset(
    {
        "/api/workflow/start",
        "/api/workflow/rerun-from-agent",
        "/api/workflow/start-from-agent-input",
        "/api/create-data/start",
        "/api/eval-batch/start",
    }
)


def _positive_int_from_env(name: str, default: int) -> int:
    """Return a positive integer environment value or a safe default."""
    try:
        value = int((os.getenv(name) or "").strip())
    except ValueError:
        return default
    return value if value > 0 else default


def validate_access_token(value: str | None, *, allow_empty: bool = True) -> str:
    """Return a canonical strong token or raise without exposing its value."""
    token = (value or "").strip()
    if not token and allow_empty:
        return ""
    if not _ACCESS_TOKEN_PATTERN.fullmatch(token):
        raise ValueError(
            "APP_ACCESS_TOKEN must be 32-512 characters using only "
            "letters, numbers, and . _ ~ + / = -"
        )
    return token


@dataclass(frozen=True)
class AccessBoundaryConfig:
    """Startup configuration for authentication and cost-route throttling."""

    access_token: str = field(default="", repr=False)
    rate_limit_requests: int = DEFAULT_RATE_LIMIT_REQUESTS
    rate_limit_window_seconds: int = DEFAULT_RATE_LIMIT_WINDOW_SECONDS
    trusted_origins: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.access_token:
            canonical = validate_access_token(self.access_token, allow_empty=False)
            if canonical != self.access_token:
                raise ValueError("APP_ACCESS_TOKEN must not contain surrounding whitespace")

    @classmethod
    def from_env(
        cls,
        *,
        trusted_origins: tuple[str, ...] = (),
    ) -> AccessBoundaryConfig:
        """Load access-boundary configuration after the project `.env` is loaded."""
        return cls(
            access_token=validate_access_token(os.getenv("APP_ACCESS_TOKEN")),
            rate_limit_requests=_positive_int_from_env(
                "APP_RATE_LIMIT_REQUESTS",
                DEFAULT_RATE_LIMIT_REQUESTS,
            ),
            rate_limit_window_seconds=_positive_int_from_env(
                "APP_RATE_LIMIT_WINDOW_SECONDS",
                DEFAULT_RATE_LIMIT_WINDOW_SECONDS,
            ),
            trusted_origins=trusted_origins,
        )

    @property
    def auth_required(self) -> bool:
        return bool(self.access_token)


def validated_paypal_donate_url(value: str | None) -> str | None:
    """Return a safe HTTPS PayPal donation URL, otherwise ``None``."""
    raw = (value or "").strip()
    if not raw or any(char.isspace() or ord(char) < 32 for char in raw):
        return None
    try:
        parsed = urlsplit(raw)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError:
        return None

    allowed_host = (
        hostname in {"paypal.com", "paypal.me"}
        or hostname.endswith(".paypal.com")
        or hostname.endswith(".paypal.me")
    )
    if (
        parsed.scheme.lower() != "https"
        or not allowed_host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    return raw


def is_cost_bearing_post(method: str, path: str) -> bool:
    """Return whether a POST consumes bounded disk, compute, or provider cost."""
    if method.upper() != "POST":
        return False
    if path in _COST_BEARING_POST_PATHS:
        return True
    return path.startswith("/api/outputs/") and path.endswith("/compare-professor")


def is_rate_limited_request(method: str, path: str) -> bool:
    """Return whether an operation needs per-client resource admission."""
    return is_cost_bearing_post(method, path) or (
        (method.upper() == "POST" and path == "/api/upload")
        or (method.upper() == "GET" and path.startswith("/api/download-zip/"))
    )


def _is_protected_path(path: str) -> bool:
    return (
        path == "/api"
        or path.startswith("/api/")
        or path == "/openapi.json"
        or path.startswith(_DOC_PATH_PREFIXES)
    )


def _is_unsafe_api_request(method: str, path: str) -> bool:
    return method in _UNSAFE_API_METHODS and (path == "/api" or path.startswith("/api/"))


def _header_values(scope: Scope, header_name: str) -> list[bytes]:
    encoded_name = header_name.lower().encode("ascii")
    return [
        value
        for name, value in scope.get("headers", [])
        if name.lower() == encoded_name
    ]


def _rate_limit_bucket(method: str, path: str) -> str:
    """Normalize dynamic resource paths without combining unrelated operations."""
    normalized_method = method.upper()
    if normalized_method == "POST" and path.startswith("/api/outputs/"):
        if path.endswith("/compare-professor"):
            return "POST:/api/outputs/*/compare-professor"
    if normalized_method == "GET" and path.startswith("/api/download-zip/"):
        return "GET:/api/download-zip/*"
    return f"{normalized_method}:{path}"


class _SlidingWindowRateLimiter:
    """Small in-process limiter keyed by client and normalized operation."""

    def __init__(self, *, request_limit: int, window_seconds: int):
        self.request_limit = max(1, request_limit)
        self.window_seconds = max(1, window_seconds)
        self._requests: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._last_cleanup = time.monotonic()

    def consume(self, client_id: str) -> tuple[bool, int]:
        """Consume one request and return ``(allowed, retry_after_seconds)``."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            if now - self._last_cleanup >= self.window_seconds:
                stale_clients = [
                    key
                    for key, timestamps in self._requests.items()
                    if not timestamps or timestamps[-1] <= cutoff
                ]
                for key in stale_clients:
                    self._requests.pop(key, None)
                self._last_cleanup = now
            recent = self._requests.setdefault(client_id, deque())
            while recent and recent[0] <= cutoff:
                recent.popleft()
            if len(recent) >= self.request_limit:
                retry_after = max(1, math.ceil(recent[0] + self.window_seconds - now))
                return False, retry_after
            recent.append(now)
            return True, 0


class AccessBoundaryMiddleware:
    """Enforce browser origin, token, then per-client resource admission."""

    def __init__(self, app: ASGIApp, *, config: AccessBoundaryConfig):
        self.app = app
        self.config = config
        self.rate_limiter = _SlidingWindowRateLimiter(
            request_limit=config.rate_limit_requests,
            window_seconds=config.rate_limit_window_seconds,
        )
        self.resource_rate_limiter = _SlidingWindowRateLimiter(
            request_limit=config.rate_limit_requests * 4,
            window_seconds=config.rate_limit_window_seconds,
        )
        self.trusted_origins = frozenset(config.trusted_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "")).upper()
        path = str(scope.get("path", ""))
        state = scope.setdefault("state", {})
        # In local compatibility mode, the request has satisfied the configured
        # access policy because no credential is required.
        state["authenticated"] = not self.config.auth_required

        # CORS preflight is an authorization probe rather than a state-changing
        # request. The inner CORSMiddleware validates its requested origin/method.
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        if _is_unsafe_api_request(method, path):
            origin_values = _header_values(scope, "Origin")
            if origin_values:
                try:
                    supplied_origin = (
                        origin_values[0].decode("ascii")
                        if len(origin_values) == 1
                        else ""
                    )
                except UnicodeDecodeError:
                    supplied_origin = ""
                if (
                    len(origin_values) != 1
                    or supplied_origin.lower() == "null"
                    or supplied_origin not in self.trusted_origins
                ):
                    await _json_response(
                        scope,
                        receive,
                        send,
                        status_code=403,
                        content={"detail": "Origin not allowed"},
                    )
                    return

        if self.config.auth_required:
            if method == "GET" and path in _PUBLIC_GET_PATHS:
                await self.app(scope, receive, send)
                return
            if not _is_protected_path(path):
                await self.app(scope, receive, send)
                return

            supplied_values = _header_values(scope, ACCESS_TOKEN_HEADER)
            supplied_token = supplied_values[0] if len(supplied_values) == 1 else b""
            token_matches = secrets.compare_digest(
                supplied_token,
                self.config.access_token.encode("utf-8"),
            )
            if len(supplied_values) != 1 or not token_matches:
                await _json_response(
                    scope,
                    receive,
                    send,
                    status_code=401,
                    content={"detail": "Authentication required"},
                )
                return

            state["authenticated"] = True
        limiter = None
        if is_cost_bearing_post(method, path):
            limiter = self.rate_limiter
        elif is_rate_limited_request(method, path):
            limiter = self.resource_rate_limiter
        if limiter is not None:
            client = scope.get("client")
            client_address = str(client[0]) if client else "unknown"
            client_id = f"{client_address}\0{_rate_limit_bucket(method, path)}"
            allowed, retry_after = limiter.consume(client_id)
            if not allowed:
                await _json_response(
                    scope,
                    receive,
                    send,
                    status_code=429,
                    content={"detail": "Rate limit exceeded"},
                    headers={"Retry-After": str(retry_after)},
                )
                return

        await self.app(scope, receive, send)


async def _json_response(
    scope: Scope,
    receive: Receive,
    send: Send,
    *,
    status_code: int,
    content: dict[str, str],
    headers: dict[str, str] | None = None,
) -> None:
    response = JSONResponse(
        status_code=status_code,
        content=content,
        headers=headers,
    )
    await response(scope, receive, send)
