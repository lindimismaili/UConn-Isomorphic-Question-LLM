"""Durable, non-secret operations state for the admin dashboard.

The public application only consumes ``public_status``.  File browsing,
settings, audit events, and incidents are available exclusively through the
admin router after Cloudflare Access identity validation.
"""

from __future__ import annotations

import hashlib
import json
import os
import smtplib
import sqlite3
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from agents.logger import logger

from backend.src.paths import RUNTIME_DIR

OPENAI_STATUS_URL = "https://status.openai.com/api/v2/status.json"
OPERATIONS_DB_PATH = RUNTIME_DIR / "operations.sqlite3"
OPERATIONS_PROBE_PATH = RUNTIME_DIR / "operations_probe.json"
RUNTIME_CONFIG_PATH = Path(os.getenv("RUNTIME_CONFIG_PATH", str(RUNTIME_DIR / "admin-runtime.env")))

CODE_COST = "API TOKEN COST ISSUES"
CODE_SERVER = "API SERVER ISSUES"
CODE_AUTH = "API KEY AUTHENTICATION ISSUES"
CODE_MANUAL = "SCHEDULED MAINTENANCE"
_MAX_INCIDENTS = 100


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Operations:
    """SQLite-backed operational state with intentionally narrow primitives."""

    def __init__(self, path: Path = OPERATIONS_DB_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL,
                    title TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    status TEXT NOT NULL,
                    opened_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    occurrences INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL,
                    outcome TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS visitors (
                    visitor_hash TEXT PRIMARY KEY,
                    seen_at REAL NOT NULL
                );
                """
            )

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._connect() as connection:
            row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, json.JSONDecodeError):
            return default

    def set_setting(self, key: str, value: Any) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, json.dumps(value, ensure_ascii=False), utc_now()),
            )

    def audit(self, actor: str, action: str, target: str, outcome: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO audit_events (at, actor, action, target, outcome) VALUES (?, ?, ?, ?, ?)",
                (utc_now(), actor[:320], action[:120], target[:320], outcome[:320]),
            )

    def audit_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT at, actor, action, target, outcome FROM audit_events ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def open_incident(self, *, code: str, title: str, detail: str, severity: str = "error") -> dict[str, Any]:
        now = utc_now()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM incidents WHERE code = ? AND status = 'open' ORDER BY id DESC LIMIT 1", (code,)
            ).fetchone()
            if existing:
                connection.execute(
                    "UPDATE incidents SET detail = ?, updated_at = ?, occurrences = occurrences + 1 WHERE id = ?",
                    (detail[:500], now, existing["id"]),
                )
                return {**dict(existing), "detail": detail[:500], "updated_at": now, "occurrences": existing["occurrences"] + 1, "new": False}
            cursor = connection.execute(
                """INSERT INTO incidents (code, title, detail, severity, status, opened_at, updated_at)
                VALUES (?, ?, ?, ?, 'open', ?, ?)""",
                (code, title[:160], detail[:500], severity[:32], now, now),
            )
            incident_id = cursor.lastrowid
        return {"id": incident_id, "code": code, "title": title[:160], "detail": detail[:500], "severity": severity, "status": "open", "opened_at": now, "updated_at": now, "occurrences": 1, "new": True}

    def resolve_incidents(self, *, code: str | None = None) -> None:
        with self._connect() as connection:
            if code:
                connection.execute("UPDATE incidents SET status = 'resolved', updated_at = ? WHERE status = 'open' AND code = ?", (utc_now(), code))
            else:
                connection.execute("UPDATE incidents SET status = 'resolved', updated_at = ? WHERE status = 'open'", (utc_now(),))

    def incidents(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id, code, title, detail, severity, status, opened_at, updated_at, occurrences
                FROM incidents ORDER BY id DESC LIMIT ?""",
                (max(1, min(limit, _MAX_INCIDENTS)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def current_maintenance(self) -> dict[str, Any] | None:
        value = self.get_setting("maintenance", None)
        return value if isinstance(value, dict) and value.get("active") else None

    def public_status(self) -> dict[str, Any]:
        maintenance = self.current_maintenance()
        if not maintenance:
            return {"available": True, "maintenance": False, "code": "", "message": "Service available.", "detectedAt": utc_now()}
        return {
            "available": False,
            "maintenance": True,
            "code": str(maintenance.get("code") or CODE_MANUAL),
            "message": str(maintenance.get("message") or "The site is temporarily unavailable for maintenance."),
            "detectedAt": str(maintenance.get("detectedAt") or utc_now()),
        }

    def set_maintenance(self, *, active: bool, code: str = CODE_MANUAL, message: str = "", actor: str = "system") -> dict[str, Any]:
        if active:
            value = {"active": True, "code": code[:120], "message": message[:500] or "The site is temporarily unavailable for maintenance.", "detectedAt": utc_now()}
            self.set_setting("maintenance", value)
            self.audit(actor, "maintenance.enable", value["code"], "success")
            return value
        self.set_setting("maintenance", {"active": False, "clearedAt": utc_now()})
        self.resolve_incidents()
        self.audit(actor, "maintenance.disable", "service", "success")
        return {"active": False}

    def record_visitor(self, visitor_id: str) -> None:
        if not visitor_id or len(visitor_id) > 160:
            return
        salt = os.getenv("VISITOR_HASH_SALT", "local-anonymous-visitor-salt")
        digest = hashlib.sha256(f"{salt}:{visitor_id}".encode("utf-8")).hexdigest()
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO visitors (visitor_hash, seen_at) VALUES (?, ?) ON CONFLICT(visitor_hash) DO UPDATE SET seen_at=excluded.seen_at",
                (digest, now),
            )
            connection.execute("DELETE FROM visitors WHERE seen_at < ?", (now - 300,))

    def active_visitors(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM visitors WHERE seen_at >= ?", (time.time() - 300,)).fetchone()
        return int(row["count"] if row else 0)

    def write_runtime_secret(self, key: str, value: str) -> None:
        """Atomically update a narrow persistent runtime env override without logging it."""
        allowed = {"OPENAI_API_KEY", "OPENAI_ADMIN_KEY"}
        if key not in allowed or not value.strip() or "\n" in value or "\r" in value:
            raise ValueError("Unsupported runtime secret")
        current: dict[str, str] = {}
        try:
            for line in RUNTIME_CONFIG_PATH.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    name, existing = line.split("=", 1)
                    if name.replace("_", "").isalnum() and name.upper() == name:
                        current[name] = existing
        except OSError:
            pass
        current[key] = value.strip()
        RUNTIME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = RUNTIME_CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text("".join(f"{name}={current[name]}\n" for name in sorted(current)), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(RUNTIME_CONFIG_PATH)

    def load_probe(self) -> dict[str, Any]:
        try:
            payload = json.loads(OPERATIONS_PROBE_PATH.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {"status": "unavailable"}
        except (OSError, json.JSONDecodeError):
            return {"status": "unavailable", "checked_at": ""}


def classify_openai_failure(exc: BaseException | str) -> tuple[str, str] | None:
    """Map known billing, auth, and service failures to public-safe incidents."""
    text = str(exc).lower()
    status = getattr(exc, "status_code", None)
    if any(marker in text for marker in ("insufficient_quota", "billing_hard_limit", "credit", "quota")):
        return CODE_COST, "OpenAI billing or quota is preventing workflows from running."
    if status in {401, 403} or any(marker in text for marker in ("invalid api key", "authentication", "incorrect api key")):
        return CODE_AUTH, "OpenAI rejected the configured API credentials."
    if status in {500, 502, 503, 504}:
        return CODE_SERVER, "OpenAI API service errors are preventing workflows from running."
    return None


def fetch_openai_status(timeout: float = 5.0) -> dict[str, Any]:
    try:
        request = Request(OPENAI_STATUS_URL, headers={"User-Agent": "IsomorphicQuestionGenerator/1.0"})
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        status = payload.get("status", {}) if isinstance(payload, dict) else {}
        indicator = str(status.get("indicator", "unknown")).lower()
        return {"status": "ok", "indicator": indicator, "description": str(status.get("description", "Unknown")), "openai_down": indicator in {"major", "critical"}, "checked_at": utc_now()}
    except (URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("OpenAI status check failed: %s", exc)
        return {"status": "unknown", "indicator": "unknown", "description": "Unable to verify OpenAI status right now.", "openai_down": False, "checked_at": utc_now()}


def fetch_openai_cost_summary(timeout: float = 10.0) -> dict[str, Any]:
    """Return current-day organization costs when an Owner admin key is configured."""
    admin_key = os.getenv("OPENAI_ADMIN_KEY", "").strip()
    if not admin_key:
        return {"configured": False, "available": False, "amount": None, "currency": "USD", "checked_at": utc_now()}
    now = int(time.time())
    query = urlencode({"start_time": now - 86_400, "end_time": now, "bucket_width": "1d", "limit": 1})
    request = Request(
        f"https://api.openai.com/v1/organization/costs?{query}",
        headers={"Authorization": f"Bearer {admin_key}", "User-Agent": "IsomorphicQuestionGenerator/1.0"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        buckets = payload.get("data", []) if isinstance(payload, dict) else []
        amount = 0.0
        currency = "USD"
        for bucket in buckets:
            for result in bucket.get("results", []) if isinstance(bucket, dict) else []:
                value = result.get("amount", {}) if isinstance(result, dict) else {}
                amount += float(value.get("value", 0) or 0)
                currency = str(value.get("currency", currency) or currency)
        return {"configured": True, "available": True, "amount": round(amount, 4), "currency": currency, "checked_at": utc_now()}
    except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("OpenAI cost check failed: %s", exc)
        return {"configured": True, "available": False, "amount": None, "currency": "USD", "checked_at": utc_now()}


def send_incident_email(incident: dict[str, Any]) -> None:
    """Best-effort generic SMTP alerting; a delivery failure is itself only logged."""
    recipients = [value.strip() for value in os.getenv("ALERT_RECIPIENTS", "").split(",") if value.strip()]
    host = os.getenv("SMTP_HOST", "").strip()
    if not recipients or not host:
        return
    message = EmailMessage()
    message["Subject"] = f"[Isomorphic Generator] {incident['code']}"
    message["From"] = os.getenv("SMTP_FROM", recipients[0])
    message["To"] = ", ".join(recipients)
    message.set_content(f"{incident['title']}\n\n{incident['detail']}\n\nDetected: {incident['opened_at']}")
    try:
        with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=15) as smtp:
            if os.getenv("SMTP_STARTTLS", "true").lower() not in {"0", "false", "no"}:
                smtp.starttls()
            username = os.getenv("SMTP_USERNAME", "")
            if username:
                smtp.login(username, os.getenv("SMTP_PASSWORD", ""))
            smtp.send_message(message)
    except Exception as exc:
        logger.warning("Could not send operations alert: %s", exc)


operations = Operations()
