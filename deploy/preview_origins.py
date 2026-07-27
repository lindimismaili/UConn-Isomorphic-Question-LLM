"""Build the exact browser-origin allowlist used by local preview launchers."""

from __future__ import annotations

import argparse
import importlib
import ipaddress
import json
import os
import re
import secrets
import socket
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from urllib.parse import urlsplit

_HOSTNAME_RE = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
    re.IGNORECASE,
)
_ACCESS_TOKEN_RE = re.compile(r"[A-Za-z0-9._~+/=-]{32,512}")


def _valid_non_loopback_ipv4(value: str) -> str | None:
    """Return a canonical usable IPv4 address, or ``None``."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if (
        address.version != 4
        or address.is_loopback
        or address.is_unspecified
        or address.is_multicast
    ):
        return None
    return str(address)


def _normalize_hostname(value: str, *, label: str) -> str:
    hostname = value.strip().lower().rstrip(".")
    if not hostname or not _HOSTNAME_RE.fullmatch(hostname):
        raise ValueError(f"{label} must be a DNS hostname or IPv4 address.")
    return hostname


def _normalize_origin(value: str, *, label: str) -> str:
    raw = value.strip()
    if not raw or any(character.isspace() or ord(character) < 32 for character in raw):
        raise ValueError(f"{label} contains whitespace or control characters.")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{label} is not a valid origin.") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            f"{label} must be an exact HTTP(S) origin without credentials, path, query, or fragment."
        )

    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower().rstrip(".")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        hostname = _normalize_hostname(hostname, label=label)
        rendered_host = hostname
    else:
        rendered_host = f"[{address}]" if address.version == 6 else str(address)

    default_port = 80 if scheme == "http" else 443
    rendered_port = "" if port in {None, default_port} else f":{port}"
    return f"{scheme}://{rendered_host}{rendered_port}"


def _origin_for_host(host: str, port: int, *, label: str) -> str:
    normalized_ip = _valid_non_loopback_ipv4(host)
    normalized_host = normalized_ip or _normalize_hostname(host, label=label)
    return _normalize_origin(f"http://{normalized_host}:{port}", label=label)


def _deduplicated(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def discover_lan_hosts() -> list[str]:
    """Discover non-loopback IPv4 addresses suitable for an advertised LAN URL."""
    override = (os.getenv("PREVIEW_LAN_HOST") or "").strip()
    if override:
        normalized_ip = _valid_non_loopback_ipv4(override)
        if normalized_ip:
            return [normalized_ip]
        hostname = _normalize_hostname(override, label="PREVIEW_LAN_HOST")
        try:
            resolved = socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_STREAM)
        except OSError as exc:
            raise ValueError(
                "PREVIEW_LAN_HOST must resolve to a non-loopback IPv4 address."
            ) from exc
        addresses = _deduplicated(
            address
            for result in resolved
            if (address := _valid_non_loopback_ipv4(result[4][0])) is not None
        )
        if not addresses:
            raise ValueError("PREVIEW_LAN_HOST must resolve to a non-loopback IPv4 address.")
        return [hostname]

    candidates: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            candidates.append(probe.getsockname()[0])
    except OSError:
        pass

    try:
        candidates.extend(
            result[4][0]
            for result in socket.getaddrinfo(
                socket.gethostname(),
                None,
                socket.AF_INET,
                socket.SOCK_STREAM,
            )
        )
    except OSError:
        pass

    addresses = _deduplicated(
        address
        for candidate in candidates
        if (address := _valid_non_loopback_ipv4(candidate)) is not None
    )
    if not addresses:
        raise ValueError(
            "--lan could not determine a non-loopback IPv4 address. "
            "Connect this computer to the trusted LAN or set PREVIEW_LAN_HOST "
            "to its reachable LAN hostname or IPv4 address."
        )
    return addresses


def resolve_preview_access_token(
    *,
    lan_mode: bool,
    configured_token: str | None,
    token_factory: Callable[[int], str] = secrets.token_urlsafe,
) -> tuple[str, bool]:
    """Return the configured LAN token or a strong process-only replacement."""
    if not lan_mode:
        return "", False
    token = (configured_token or "").strip()
    if token:
        if not _ACCESS_TOKEN_RE.fullmatch(token):
            raise ValueError(
                "APP_ACCESS_TOKEN must be 32-512 characters using only "
                "letters, numbers, and . _ ~ + / = -"
            )
        return token, False
    return token_factory(32), True


def build_preview_origin_config(
    *,
    local_domain: str,
    dev_port: int,
    web_port: int,
    lan_mode: bool,
    extra_origins: str = "",
    lan_hosts: Iterable[str] | None = None,
) -> dict[str, object]:
    """Return canonical preview origins and LAN URLs for the selected mode."""
    loopback_hosts = _deduplicated((local_domain, "localhost", "127.0.0.1"))
    origins = [
        _normalize_origin(origin, label="ALLOWED_ORIGINS entry")
        for origin in extra_origins.split(",")
        if origin.strip()
    ]
    for host in loopback_hosts:
        for port in _deduplicated((dev_port, web_port)):
            origins.append(_origin_for_host(host, port, label="LOCAL_DOMAIN"))

    lan_origins: list[str] = []
    if lan_mode:
        selected_hosts = discover_lan_hosts() if lan_hosts is None else list(lan_hosts)
        for host in selected_hosts:
            lan_origins.append(_origin_for_host(host, dev_port, label="LAN preview host"))
        lan_origins = _deduplicated(lan_origins)
        if not lan_origins:
            raise ValueError(
                "--lan could not determine a non-loopback IPv4 address. "
                "Connect this computer to the trusted LAN or set PREVIEW_LAN_HOST "
                "to its reachable LAN hostname or IPv4 address."
            )
        origins.extend(lan_origins)

    allowed_origins = _deduplicated(origins)
    return {
        "allowedOrigins": allowed_origins,
        "allowedOriginsValue": ",".join(allowed_origins),
        "lanOrigins": lan_origins,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-dir", type=Path, required=True)
    parser.add_argument("--local-domain", required=True)
    parser.add_argument("--dev-port", type=int, required=True)
    parser.add_argument("--web-port", type=int, required=True)
    parser.add_argument("--lan", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not 1 <= args.dev_port <= 65535 or not 1 <= args.web_port <= 65535:
        print("[preview] Preview ports must be between 1 and 65535.", file=sys.stderr)
        return 2
    try:
        config = build_preview_origin_config(
            local_domain=args.local_domain,
            dev_port=args.dev_port,
            web_port=args.web_port,
            lan_mode=args.lan,
            extra_origins=os.getenv("ALLOWED_ORIGINS", ""),
        )
        configured_token = os.getenv("APP_ACCESS_TOKEN")
        if args.lan and not (configured_token or "").strip():
            site_dir = args.site_dir.expanduser().resolve()
            if not (site_dir / "backend" / "src" / "bootstrap.py").is_file():
                raise ValueError(f"Backend bootstrap was not found under {site_dir}.")
            sys.path.insert(0, str(site_dir))
            bootstrap = importlib.import_module("backend.src.bootstrap")
            paths = importlib.import_module("backend.src.paths")
            bootstrap.load_dotenv(paths.ENV_PATH)
            configured_token = os.getenv("APP_ACCESS_TOKEN")
        access_token, generated = resolve_preview_access_token(
            lan_mode=args.lan,
            configured_token=configured_token,
        )
        config["accessToken"] = access_token
        config["accessTokenGenerated"] = generated
    except ValueError as exc:
        print(f"[preview] {exc}", file=sys.stderr)
        return 2
    print(json.dumps(config, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
