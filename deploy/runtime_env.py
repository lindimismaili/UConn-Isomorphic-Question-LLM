#!/usr/bin/env python3
"""Safely inspect or update the Raspberry Pi's persistent runtime environment.

Secret values are accepted only on standard input. They are never written to
stdout, placed in process arguments, or interpreted as shell syntax.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

POLICY_RANGES = {
    "APP_RATE_LIMIT_REQUESTS": (1, 1_000),
    "APP_RATE_LIMIT_WINDOW_SECONDS": (1, 3_600),
    "UPLOAD_RETENTION_SECONDS": (3_600, 31_536_000),
    "OUTPUT_RETENTION_SECONDS": (3_600, 31_536_000),
    "WORKFLOW_TTL_SECONDS": (300, 2_592_000),
    "WORKFLOW_MAX_ENTRIES": (1, 10_000),
    "OPENAI_WORKFLOW_CONCURRENCY": (1, 8),
    "BATCH_WORKFLOW_CONCURRENCY": (1, 8),
    "MAX_GENERATED_QUESTIONS": (1, 500),
}
ALLOWED_KEYS = frozenset(
    {"OPENAI_API_KEY", "APP_ACCESS_TOKEN", "PAYPAL_DONATE_URL", *POLICY_RANGES}
)
SECRET_KEYS = frozenset({"OPENAI_API_KEY", "APP_ACCESS_TOKEN"})
SAFE_VALUE = re.compile(r"^[A-Za-z0-9._~+/=-]+$")
DONATION_URL_SAFE = re.compile(r"^https://[A-Za-z0-9._~/%?&=+-]+$")
MIN_ACCESS_TOKEN_LENGTH = 32
MAX_ACCESS_TOKEN_LENGTH = 512
MAX_STATUS_RESPONSE_BYTES = 64 * 1024
MAX_DONATION_URL_BYTES = 2048
MAX_ENVIRONMENT_BYTES = 64 * 1024
ASSIGNMENT = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


class _NoRedirect(HTTPRedirectHandler):
    """Never forward the access token to a redirect destination."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _assignment_value(line: str, key: str) -> str | None:
    match = re.match(rf"^[ \t]*{re.escape(key)}[ \t]*=(.*)$", line)
    if match is None:
        return None
    return match.group(1).strip().strip("\"'")


def has_value(path: Path, key: str) -> bool:
    value = _read_value(path, key)
    if value is None:
        return False
    if key == "APP_ACCESS_TOKEN":
        try:
            _validate_value(key, value)
        except ValueError:
            return False
    return True


def _read_value(path: Path, key: str) -> str | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            os.close(fd)
            return None
        with os.fdopen(fd, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except (OSError, UnicodeError):
        return None
    for line in lines:
        value = _assignment_value(line, key)
        if value:
            return value
    return None


def _validate_target(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("runtime environment path must not be a symlink")
    if path.exists() and not path.is_file():
        raise ValueError("runtime environment path must be a regular file")
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError("runtime environment parent must be a real directory")


def _validate_value(key: str, value: str) -> None:
    if not value:
        raise ValueError("value cannot be empty")
    if key in SECRET_KEYS and not SAFE_VALUE.fullmatch(value):
        raise ValueError("secret contains unsupported characters")
    if key == "APP_ACCESS_TOKEN" and not (
        MIN_ACCESS_TOKEN_LENGTH <= len(value) <= MAX_ACCESS_TOKEN_LENGTH
    ):
        raise ValueError("browser access token must contain 32 to 512 characters")
    if key == "PAYPAL_DONATE_URL":
        if (
            len(value.encode("utf-8")) > MAX_DONATION_URL_BYTES
            or not value.isascii()
            or DONATION_URL_SAFE.fullmatch(value) is None
        ):
            raise ValueError("donation URL contains unsupported characters")
        try:
            parsed = urlsplit(value)
            hostname = (parsed.hostname or "").lower().rstrip(".")
            port = parsed.port
        except ValueError as exc:
            raise ValueError("donation URL has an invalid authority") from exc
        allowed_host = (
            hostname in {"paypal.com", "paypal.me"}
            or hostname.endswith((".paypal.com", ".paypal.me"))
        )
        if (
            parsed.scheme != "https"
            or not allowed_host
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
        ):
            raise ValueError("donation URL must be a hosted HTTPS PayPal URL")
    if key in POLICY_RANGES:
        if not value.isascii() or not value.isdigit():
            raise ValueError("runtime policy value must be an integer")
        minimum, maximum = POLICY_RANGES[key]
        numeric_value = int(value)
        if not minimum <= numeric_value <= maximum:
            raise ValueError(f"runtime policy value must be from {minimum} to {maximum}")


def validate_environment_file(path: Path) -> dict[str, str]:
    """Parse the complete systemd EnvironmentFile using the deployment allowlist."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ValueError("runtime environment could not be opened safely") from exc
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_ENVIRONMENT_BYTES:
            raise ValueError("runtime environment must be a small regular file")
        with os.fdopen(fd, encoding="utf-8") as handle:
            fd = -1
            lines = handle.read().splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError("runtime environment is not valid UTF-8 text") from exc
    finally:
        if fd >= 0:
            os.close(fd)

    values: dict[str, str] = {}
    for line in lines:
        if not line or line.startswith("#"):
            continue
        match = ASSIGNMENT.fullmatch(line)
        if match is None:
            raise ValueError("runtime environment contains a non-canonical assignment")
        key, value = match.groups()
        if key not in ALLOWED_KEYS:
            raise ValueError(f"runtime environment contains unsupported key {key}")
        if key in values:
            raise ValueError(f"runtime environment contains duplicate key {key}")
        _validate_value(key, value)
        values[key] = value
    return values


def _write_environment(path: Path, updated: list[str]) -> None:
    fd, temporary_name = tempfile.mkstemp(prefix=".environment.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(updated) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def set_values(path: Path, values: dict[str, str]) -> None:
    _validate_target(path)
    if not values or any(key not in ALLOWED_KEYS for key in values):
        raise ValueError("runtime environment update contains an unsupported key")
    for key, value in values.items():
        _validate_value(key, value)

    if path.exists():
        validate_environment_file(path)
    try:
        existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    except OSError as exc:
        raise ValueError("could not read runtime environment") from exc

    updated: list[str] = []
    replaced: set[str] = set()
    for line in existing:
        matched_key = next(
            (key for key in values if _assignment_value(line, key) is not None),
            None,
        )
        if matched_key is None:
            updated.append(line)
        elif matched_key not in replaced:
            updated.append(f"{matched_key}={values[matched_key]}")
            replaced.add(matched_key)

    missing = [key for key in values if key not in replaced]
    if missing and updated and updated[-1]:
        updated.append("")
    updated.extend(f"{key}={values[key]}" for key in missing)
    _write_environment(path, updated)


def set_value(path: Path, key: str, value: str) -> None:
    set_values(path, {key: value})


def unset_value(path: Path, key: str) -> None:
    _validate_target(path)
    if path.exists():
        validate_environment_file(path)
    try:
        existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    except OSError as exc:
        raise ValueError("could not read runtime environment") from exc
    updated = [line for line in existing if _assignment_value(line, key) is None]

    _write_environment(path, updated)


def authenticated_local_get(path: Path, key: str, url: str) -> bytes:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != "/api/deployment/status"
    ):
        raise ValueError("authenticated request target is not allowed")
    token = _read_value(path, key)
    if token is None:
        raise ValueError("browser access token is not configured")
    request = Request(url, headers={"X-App-Access-Token": token})
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=3) as response:
            payload = response.read(MAX_STATUS_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise ValueError("authenticated local status request failed") from exc
    if len(payload) > MAX_STATUS_RESPONSE_BYTES:
        raise ValueError("authenticated local status response is too large")
    try:
        parsed_payload = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("authenticated local status response is not valid JSON") from exc
    active_workflows = parsed_payload.get("active_workflows") if isinstance(parsed_payload, dict) else None
    if isinstance(active_workflows, bool) or not isinstance(active_workflows, int) or active_workflows < 0:
        raise ValueError("authenticated local status response has an invalid workflow count")
    return payload


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("get", "has", "set", "set-many", "unset", "authenticated-get", "validate"),
    )
    parser.add_argument("path", type=Path)
    parser.add_argument("key", nargs="?")
    parser.add_argument("url", nargs="?")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.command == "validate":
        if args.key is not None or args.url is not None:
            return 2
        try:
            validate_environment_file(args.path)
        except ValueError as exc:
            print(f"runtime environment validation refused: {exc}", file=sys.stderr)
            return 2
        return 0

    if args.command == "set-many":
        if args.key is not None or args.url is not None:
            return 2
        try:
            payload = json.load(sys.stdin)
            if (
                not isinstance(payload, dict)
                or set(payload) != set(POLICY_RANGES)
                or not all(isinstance(key, str) and isinstance(value, str) for key, value in payload.items())
            ):
                raise ValueError("runtime policy payload is incomplete or invalid")
            set_values(args.path, payload)
        except (ValueError, json.JSONDecodeError) as exc:
            print(f"runtime environment update refused: {exc}", file=sys.stderr)
            return 2
        return 0

    if args.key not in ALLOWED_KEYS:
        return 2
    if args.command == "has":
        if args.url is not None:
            return 2
        return 0 if has_value(args.path, args.key) else 1

    if args.command == "get":
        if args.url is not None or args.key not in POLICY_RANGES:
            return 2
        value = _read_value(args.path, args.key)
        if value is None:
            return 1
        print(value)
        return 0

    if args.command == "authenticated-get":
        if args.key != "APP_ACCESS_TOKEN" or args.url is None:
            return 2
        try:
            payload = authenticated_local_get(args.path, args.key, args.url)
        except ValueError as exc:
            print(f"runtime environment request refused: {exc}", file=sys.stderr)
            return 2
        sys.stdout.buffer.write(payload)
        return 0

    if args.url is not None:
        return 2
    if args.command == "unset":
        try:
            unset_value(args.path, args.key)
        except ValueError as exc:
            print(f"runtime environment update refused: {exc}", file=sys.stderr)
            return 2
        return 0
    value = sys.stdin.read()
    try:
        set_value(args.path, args.key, value)
    except ValueError as exc:
        print(f"runtime environment update refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
