#!/usr/bin/env python3
"""Write a bounded, credential-free Pi operations report for the dashboard."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


def service_state(name: str) -> str:
    result = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True, timeout=8, check=False)
    return result.stdout.strip()[:64] or "unknown"


def remote_status(url: str) -> str:
    try:
        with urlopen(Request(url, headers={"User-Agent": "uconn-operations-probe"}), timeout=8) as response:
            return "ok" if 200 <= response.status < 400 else f"http-{response.status}"
    except (URLError, TimeoutError, OSError):
        return "unreachable"


def log_excerpt(unit: str) -> list[str]:
    """Return a small redacted journal excerpt; never expose arbitrary commands."""
    try:
        result = subprocess.run(
            ["journalctl", "-u", unit, "-n", "30", "--no-pager", "-o", "short-iso"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    lines = []
    for raw in result.stdout.splitlines()[-30:]:
        clean = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED_OPENAI_KEY]", raw)
        clean = re.sub(r"(?i)(bearer\s+)[^\s]+", r"\1[REDACTED]", clean)
        clean = re.sub(r"(?i)(api[_ -]?key[=:]\s*)[^\s,;]+", r"\1[REDACTED]", clean)
        lines.append(clean[:500])
    return lines


def main() -> None:
    target = Path(os.environ["OPERATIONS_PROBE_PATH"])
    service = os.environ.get("SERVICE_NAME", "uconn-isomorphic")
    cloudflared = os.environ.get("CLOUDFLARED_SERVICE_NAME", f"{service}-cloudflared")
    public_host = os.environ.get("PUBLIC_HOSTNAME", "").strip()
    volume = Path(os.environ.get("PERSIST_DIR", "/var/lib/uconn-isomorphic"))
    disk = shutil.disk_usage(volume)
    report = {
        "status": "ok",
        "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "backend": service_state(service),
        "nginx": service_state("nginx"),
        "cloudflared": service_state(cloudflared),
        "updater": service_state(f"{service}-update.timer"),
        "disk_free": disk.free,
        "disk_used": disk.used,
        "load_1m": round(os.getloadavg()[0], 2),
        "cloudflare_status": remote_status("https://www.cloudflarestatus.com/api/v2/status.json"),
        "public_health": remote_status(f"https://{public_host}/api/health") if public_host else "not-configured",
        "logs": {"backend": log_excerpt(service), "cloudflared": log_excerpt(cloudflared)},
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as handle:
        json.dump(report, handle, separators=(",", ":"))
        handle.write("\n")
        tmp = Path(handle.name)
    os.chmod(tmp, 0o640)
    tmp.replace(target)


if __name__ == "__main__":
    main()
