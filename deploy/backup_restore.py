#!/usr/bin/env python3
"""Fail-closed backup and restore control plane for the Raspberry Pi deployment."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import grp
import gzip
import hashlib
import io
import json
import os
import pwd
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

SERVICE = "uconn-isomorphic"
DEPLOY_ROOT = Path("/var/www/uconn-isomorphic")
RELEASES_ROOT = DEPLOY_ROOT / "releases"
CURRENT_RELEASE = DEPLOY_ROOT / "current"
PERSIST_ROOT = Path("/var/lib/uconn-isomorphic")
DEPLOY_STATE_ROOT = Path("/var/lib/uconn-isomorphic-deploy")
CONFIG_ROOT = Path("/etc/uconn-isomorphic")
DEPLOY_CONFIG = CONFIG_ROOT / "deploy.conf"
RUNTIME_ENV = CONFIG_ROOT / "environment"
RUNTIME_ENV_HELPER = Path("/usr/local/libexec/uconn-isomorphic-runtime-env")
INSTALLED_HELPER = Path("/usr/local/libexec/uconn-isomorphic-backup-restore")
TRANSACTION_ROOT = Path("/var/lib/uconn-isomorphic-restore")
TRANSACTION_STATE = Path(f"/var/lib/{SERVICE}-restore.pending")
RESTORE_ALLOW_FILE = Path(f"/run/{SERVICE}-restore-install-allowed")
MAINTENANCE_FILE = Path(f"/run/{SERVICE}-deployment-maintenance")
ACTIVATION_STATE = DEPLOY_STATE_ROOT / "pending_activation.json"
INTEGRITY_STATE = DEPLOY_STATE_ROOT / "integrity_repair.json"
LOCK_DIR = Path(f"/run/{SERVICE}-deploy-lock")
LOCK_FILE = LOCK_DIR / "deployment.lock"
ACTIVITY_LOCK_DIR = Path(f"/run/{SERVICE}-work-lock")
ACTIVITY_LOCK_FILE = ACTIVITY_LOCK_DIR / "activity.lock"
DEPLOY_ADMIN_GROUP = "sudo"
SYSTEMCTL = Path("/usr/bin/systemctl")
SS = Path("/usr/bin/ss")
GIT = Path("/usr/bin/git")
EXPECTED_ROOTS = (CONFIG_ROOT, PERSIST_ROOT, DEPLOY_STATE_ROOT)
ARCHIVE_ROOTS = tuple(path.relative_to("/") for path in EXPECTED_ROOTS)
DERIVED_LINKS = {
    PERSIST_ROOT / "runtime/deployment_status.json": DEPLOY_STATE_ROOT
    / "deployment_status.json"
}
CONFIG_KEYS = {
    "SOURCE_REPO_DIR",
    "LOCAL_HOSTNAME",
    "LOCAL_DOMAIN",
    "DEPLOY_DIR",
    "SERVICE_NAME",
    "BACKEND_PORT",
    "PERSIST_DIR",
    "RELEASE_RETENTION",
    "CLOUDFLARED_SERVICE_NAME",
    "CLOUDFLARED_CONFIG_FILE",
    "AUTO_UPDATE",
    "TUNNEL_NAME",
    "PUBLIC_HOSTNAME",
    "TUNNEL_UUID",
    "UPDATE_REMOTE_URL",
}
REQUIRED_CONFIG = {
    "DEPLOY_DIR": str(DEPLOY_ROOT),
    "SERVICE_NAME": SERVICE,
    "PERSIST_DIR": str(PERSIST_ROOT),
}
SHA_RE = re.compile(r"[0-9a-f]{40}")
STAMP_RE = re.compile(r"[0-9]{8}T[0-9]{6}Z")
GITHUB_REMOTE_RE = re.compile(
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?"
)
BACKUP_FILESYSTEMS = {"ext4", "btrfs", "xfs", "f2fs"}
STATE_SCHEMA = 1
MANIFEST_NAME = "uconn-backup-manifest.json"
SOURCE_BUNDLE_NAME = "uconn-source.bundle"
MARKER_NAME = ".uconn-restore-stage"
PRE_RESTORE_MARKER = ".uconn-pre-restore"
CHUNK_SIZE = 1024 * 1024
SPACE_MARGIN = 512 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 500_000
MAX_ARCHIVE_EXPANDED_BYTES = 100 * 1024 * 1024 * 1024
MAX_ARCHIVE_PATH_BYTES = 4096
MAX_ARCHIVE_PATH_PARTS = 128
MAX_CONTROL_JSON_BYTES = 1024 * 1024
MAX_DEPLOY_CONFIG_BYTES = 64 * 1024
MAX_ENVIRONMENT_BYTES = 64 * 1024
RUNTIME_POLICY_RANGES = {
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
RUNTIME_ALLOWED_KEYS = frozenset(
    {
        "OPENAI_API_KEY",
        "APP_ACCESS_TOKEN",
        "PAYPAL_DONATE_URL",
        *RUNTIME_POLICY_RANGES,
    }
)
RUNTIME_SECRET_KEYS = frozenset({"OPENAI_API_KEY", "APP_ACCESS_TOKEN"})
RUNTIME_SAFE_VALUE = re.compile(r"^[A-Za-z0-9._~+/=-]+$")
RUNTIME_DONATION_URL_SAFE = re.compile(r"^https://[A-Za-z0-9._~/%?&=+-]+$")
RUNTIME_ASSIGNMENT = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
INTERRUPTED_BACKUP_PARTIAL_RE = re.compile(
    rf"\.{re.escape(SERVICE)}-[0-9]{{8}}T[0-9]{{6}}Z-[0-9a-f]{{40}}"
    r"\.tgz(?:\.sha256)?\.[0-9a-f]{24}\.partial"
)
TRANSACTION_ID_RE = r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{16}"
ORPHAN_TRANSACTION_FILE_RE = re.compile(
    r"(?:"
    r"\.source-bundle\.[A-Za-z0-9_-]{6,64}\.partial"
    r"|\.source-[0-9a-f]{64}\.[A-Za-z0-9_-]{6,64}\.partial"
    r"|source-[0-9a-f]{64}\.bundle"
    r"|[0-9a-f]{64}\.tgz"
    r")"
)
ORPHAN_INSTALLER_RE = re.compile(rf"installer-({TRANSACTION_ID_RE})")
ORPHAN_INSTALLER_STAGE_RE = re.compile(rf"\.installer-({TRANSACTION_ID_RE})\.partial")


class BackupRestoreError(RuntimeError):
    """An operator-safe failure that must leave the deployment fail-closed."""


@dataclass(frozen=True)
class DeployConfig:
    values: dict[str, str]

    @property
    def backend_port(self) -> int:
        raw = self.values["BACKEND_PORT"]
        if not raw.isdigit() or not 1 <= int(raw) <= 65535:
            raise BackupRestoreError("deploy.conf has an invalid BACKEND_PORT")
        return int(raw)

    @property
    def update_remote_url(self) -> str:
        value = self.values.get("UPDATE_REMOTE_URL", "")
        if not GITHUB_REMOTE_RE.fullmatch(value):
            raise BackupRestoreError(
                "deploy.conf must contain one supported HTTPS GitHub UPDATE_REMOTE_URL"
            )
        return value


def _run(
    args: Sequence[str | os.PathLike[str]],
    *,
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [os.fspath(arg) for arg in args],
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        command = Path(os.fspath(args[0])).name
        raise BackupRestoreError(f"{command} failed with status {result.returncode}")
    return result


def _require_root() -> None:
    if sys.platform != "linux":
        raise BackupRestoreError("backup/restore is supported only on the Raspberry Pi Linux host")
    if os.geteuid() != 0:
        raise BackupRestoreError("run this command through sudo as the professor account")


def _safe_regular(path: Path, *, uid: int = 0, mode: int | None = None) -> os.stat_result:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise BackupRestoreError(f"required file is missing: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != uid or metadata.st_gid != uid:
        raise BackupRestoreError(f"unsafe file metadata: {path}")
    if mode is not None and stat.S_IMODE(metadata.st_mode) != mode:
        raise BackupRestoreError(f"unexpected mode on {path}")
    return metadata


def _safe_directory(path: Path, *, uid: int = 0, mode: int | None = None) -> os.stat_result:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise BackupRestoreError(f"required directory is missing: {path}") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise BackupRestoreError(f"unsafe directory: {path}")
    if metadata.st_uid != uid or metadata.st_gid != uid:
        raise BackupRestoreError(f"unsafe directory ownership: {path}")
    if mode is not None and stat.S_IMODE(metadata.st_mode) != mode:
        raise BackupRestoreError(f"unexpected mode on {path}")
    return metadata


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_tree(root: Path) -> None:
    for directory, names, files in os.walk(root, topdown=False, followlinks=False):
        directory_path = Path(directory)
        for name in files:
            path = directory_path / name
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        for name in names:
            child = directory_path / name
            if child.is_symlink():
                raise BackupRestoreError(f"symlink appeared while flushing {child}")
        _fsync_directory(directory_path)


def _atomic_json(path: Path, payload: dict[str, Any], *, mode: int = 0o600) -> None:
    parent = _safe_directory(
        path.parent,
        mode=0o700 if path.parent == TRANSACTION_ROOT else None,
    )
    if stat.S_IMODE(parent.st_mode) & 0o022:
        raise BackupRestoreError(f"JSON parent directory is writable by other users: {path.parent}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(temporary, 0, 0)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _read_json_nofollow(path: Path, *, mode: int = 0o600) -> dict[str, Any]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != mode
            or metadata.st_size > MAX_CONTROL_JSON_BYTES
        ):
            raise BackupRestoreError(f"unsafe metadata on {path}")
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            descriptor = -1
            payload = json.load(handle)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BackupRestoreError(f"invalid JSON in {path}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(payload, dict):
        raise BackupRestoreError(f"invalid object in {path}")
    return payload


def read_deploy_config(path: Path = DEPLOY_CONFIG) -> DeployConfig:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) & 0o022
            or metadata.st_size > MAX_DEPLOY_CONFIG_BYTES
        ):
            raise BackupRestoreError("deploy.conf must be a root-owned non-writable file")
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            descriptor = -1
            lines = handle.read().splitlines()
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    return _parse_deploy_config(lines)


def _parse_deploy_config(lines: Sequence[str]) -> DeployConfig:
    values: dict[str, str] = {}
    for line in lines:
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if (
            not separator
            or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key)
            or key not in CONFIG_KEYS
            or key in values
            or len(value) > 1024
            or any(character in value for character in ("\x00", "\r", "\n"))
        ):
            raise BackupRestoreError("deploy.conf is ambiguous or contains an unknown key")
        values[key] = value
    if any(values.get(key) != expected for key, expected in REQUIRED_CONFIG.items()):
        raise BackupRestoreError("backup/restore supports only the standard deployment layout")
    for key in ("BACKEND_PORT", "UPDATE_REMOTE_URL"):
        if key not in values:
            raise BackupRestoreError(f"deploy.conf is missing {key}")
    config = DeployConfig(values)
    _ = config.backend_port
    _ = config.update_remote_url
    return config


def _validate_runtime_value(key: str, value: str) -> None:
    if not value:
        raise BackupRestoreError(f"runtime environment value is empty: {key}")
    if key in RUNTIME_SECRET_KEYS and not RUNTIME_SAFE_VALUE.fullmatch(value):
        raise BackupRestoreError(f"runtime environment secret is malformed: {key}")
    if key == "APP_ACCESS_TOKEN" and not 32 <= len(value) <= 512:
        raise BackupRestoreError("runtime environment browser token has an unsafe length")
    if key == "PAYPAL_DONATE_URL":
        if (
            len(value.encode("utf-8")) > 2048
            or not value.isascii()
            or RUNTIME_DONATION_URL_SAFE.fullmatch(value) is None
        ):
            raise BackupRestoreError("runtime environment PayPal URL is malformed")
        try:
            parsed = urlsplit(value)
            hostname = (parsed.hostname or "").lower().rstrip(".")
            port = parsed.port
        except ValueError as exc:
            raise BackupRestoreError("runtime environment PayPal URL is malformed") from exc
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
            raise BackupRestoreError("runtime environment PayPal URL is not hosted by PayPal")
    if key in RUNTIME_POLICY_RANGES:
        if not value.isascii() or not value.isdigit():
            raise BackupRestoreError(f"runtime policy is not an integer: {key}")
        minimum, maximum = RUNTIME_POLICY_RANGES[key]
        if not minimum <= int(value) <= maximum:
            raise BackupRestoreError(f"runtime policy is outside its supported range: {key}")


def validate_runtime_environment(path: Path = RUNTIME_ENV) -> dict[str, str]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size > MAX_ENVIRONMENT_BYTES
        ):
            raise BackupRestoreError("runtime environment metadata is unsafe")
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            descriptor = -1
            lines = handle.read().splitlines()
    except UnicodeError as exc:
        raise BackupRestoreError("runtime environment is not valid UTF-8") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    return _parse_runtime_environment(lines)


def _parse_runtime_environment(lines: Sequence[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        if not line or line.startswith("#"):
            continue
        match = RUNTIME_ASSIGNMENT.fullmatch(line)
        if match is None:
            raise BackupRestoreError(
                "runtime environment contains a non-canonical assignment"
            )
        key, value = match.groups()
        if key not in RUNTIME_ALLOWED_KEYS:
            raise BackupRestoreError(f"runtime environment contains unsupported key: {key}")
        if key in values:
            raise BackupRestoreError(f"runtime environment contains duplicate key: {key}")
        _validate_runtime_value(key, value)
        values[key] = value
    if not RUNTIME_SECRET_KEYS.issubset(values):
        raise BackupRestoreError("runtime environment is missing required credentials")
    return values


def _systemd_property(unit: str, property_name: str) -> str:
    result = _run(
        (
            SYSTEMCTL,
            "show",
            unit,
            "--no-pager",
            f"--property={property_name}",
            "--value",
        )
    )
    value = result.stdout.strip()
    if not value:
        raise BackupRestoreError(f"systemd returned empty {property_name} for {unit}")
    return value


def _unit_snapshot(unit: str, *, required: bool = False) -> dict[str, str]:
    load = _systemd_property(unit, "LoadState")
    if load == "not-found":
        if required:
            raise BackupRestoreError(f"required unit is missing: {unit}")
        return {"load": load, "active": "inactive", "enabled": "not-found"}
    if load not in {"loaded", "masked"}:
        raise BackupRestoreError(f"unsafe LoadState={load} for {unit}")
    return {
        "load": load,
        "active": _systemd_property(unit, "ActiveState"),
        "enabled": _systemd_property(unit, "UnitFileState"),
    }


def _stop_unit(unit: str, *, required: bool = False) -> dict[str, str]:
    snapshot = _unit_snapshot(unit, required=required)
    if snapshot["load"] == "not-found":
        return snapshot
    _run((SYSTEMCTL, "stop", unit))
    if _systemd_property(unit, "ActiveState") != "inactive":
        raise BackupRestoreError(f"unit did not become inactive: {unit}")
    return snapshot


def _disable_unit(unit: str, *, required: bool = False) -> dict[str, str]:
    snapshot = _unit_snapshot(unit, required=required)
    if snapshot["load"] == "not-found":
        return snapshot
    _run((SYSTEMCTL, "disable", "--now", unit))
    active = _systemd_property(unit, "ActiveState")
    enabled = _systemd_property(unit, "UnitFileState")
    if active != "inactive" or enabled not in {"disabled", "masked"}:
        raise BackupRestoreError(f"unit did not become durably disabled: {unit}")
    return snapshot


def _start_unit(unit: str) -> None:
    _run((SYSTEMCTL, "start", unit))
    if _systemd_property(unit, "ActiveState") != "active":
        raise BackupRestoreError(f"unit did not become active: {unit}")


def _cloudflared_units() -> list[str]:
    units = {f"{SERVICE}-cloudflared.service"}
    loaded = _run(
        (SYSTEMCTL, "list-units", "--all", "--type=service", "--plain", "--no-legend")
    )
    for line in loaded.stdout.splitlines():
        fields = line.split()
        unit = fields[0] if fields else ""
        if unit.endswith(".service") and "@.service" not in unit:
            units.add(unit)
    unit_files = _run((SYSTEMCTL, "list-unit-files", "--type=service", "--no-legend"))
    for line in unit_files.stdout.splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        unit, unit_file_state = fields[:2]
        if (
            unit.endswith(".service")
            and "@.service" not in unit
            and unit_file_state
            in {"enabled", "enabled-runtime", "linked", "linked-runtime", "alias"}
        ):
            units.add(unit)
    matched: set[str] = set()
    for unit in sorted(units):
        snapshot = _unit_snapshot(unit)
        if unit == f"{SERVICE}-cloudflared.service":
            matched.add(unit)
        elif snapshot["load"] != "not-found":
            exec_start = _systemd_property(unit, "ExecStart")
            if re.search(r"(^|[/ ])cloudflared([ ;}]|$)", exec_start):
                matched.add(unit)
    return sorted(matched)


def _validate_maintenance() -> None:
    _safe_regular(MAINTENANCE_FILE, mode=0o644)
    try:
        payload = MAINTENANCE_FILE.read_bytes()
    except OSError as exc:
        raise BackupRestoreError("deployment maintenance marker could not be read") from exc
    if payload != b"schema=1\nreason=deployment\n":
        raise BackupRestoreError("deployment maintenance marker has unexpected content")


def _write_maintenance() -> None:
    parent = _safe_directory(MAINTENANCE_FILE.parent)
    if stat.S_IMODE(parent.st_mode) & 0o022:
        raise BackupRestoreError("maintenance parent is writable by other users")
    try:
        descriptor = os.open(
            MAINTENANCE_FILE,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o644,
        )
    except FileExistsError as exc:
        raise BackupRestoreError("deployment maintenance is already active") from exc
    try:
        os.fchmod(descriptor, 0o644)
        os.fchown(descriptor, 0, 0)
        os.write(descriptor, b"schema=1\nreason=deployment\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(MAINTENANCE_FILE.parent)
    _validate_maintenance()


def _ensure_restore_maintenance() -> None:
    if not MAINTENANCE_FILE.exists() and not MAINTENANCE_FILE.is_symlink():
        _write_maintenance()
        return
    _validate_maintenance()


def _remove_maintenance() -> None:
    _validate_maintenance()
    _remove_marker(MAINTENANCE_FILE)


@contextlib.contextmanager
def activity_lock_exclusive() -> Iterator[int]:
    directory = _safe_directory(ACTIVITY_LOCK_DIR, mode=0o755)
    if directory.st_nlink < 2:
        raise BackupRestoreError("activity lock directory metadata is unsafe")
    descriptor = os.open(
        ACTIVITY_LOCK_FILE,
        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    try:
        opened = os.fstat(descriptor)
        current = ACTIVITY_LOCK_FILE.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != 0
            or opened.st_gid != 0
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != 0o444
            or current.st_dev != opened.st_dev
            or current.st_ino != opened.st_ino
        ):
            raise BackupRestoreError("activity lock metadata or inode is unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield descriptor
    finally:
        os.close(descriptor)


def _remove_marker(path: Path) -> None:
    if path.is_symlink():
        raise BackupRestoreError(f"refusing symlinked marker: {path}")
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
    _fsync_directory(path.parent)


@contextlib.contextmanager
def deployment_lock() -> Iterator[int]:
    if LOCK_DIR.is_symlink() or (LOCK_DIR.exists() and not LOCK_DIR.is_dir()):
        raise BackupRestoreError(f"unsafe deployment lock directory: {LOCK_DIR}")
    try:
        admin_gid = grp.getgrnam(DEPLOY_ADMIN_GROUP).gr_gid
    except KeyError as exc:
        raise BackupRestoreError(
            f"required deployment group is missing: {DEPLOY_ADMIN_GROUP}"
        ) from exc
    LOCK_DIR.mkdir(mode=0o750, parents=True, exist_ok=True)
    os.chown(LOCK_DIR, 0, admin_gid)
    os.chmod(LOCK_DIR, 0o750)
    lock_directory_metadata = LOCK_DIR.lstat()
    if (
        lock_directory_metadata.st_uid != 0
        or lock_directory_metadata.st_gid != admin_gid
        or stat.S_IMODE(lock_directory_metadata.st_mode) != 0o750
    ):
        raise BackupRestoreError("deployment lock directory metadata could not be secured")
    descriptor = os.open(
        LOCK_FILE,
        os.O_RDONLY | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o440,
    )
    try:
        os.fchown(descriptor, 0, admin_gid)
        os.fchmod(descriptor, 0o440)
        lock_metadata = os.fstat(descriptor)
        if (
            lock_metadata.st_uid != 0
            or lock_metadata.st_gid != admin_gid
            or stat.S_IMODE(lock_metadata.st_mode) != 0o440
        ):
            raise BackupRestoreError("deployment lock file metadata could not be secured")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupRestoreError("another deployment, backup, or restore owns the lock") from exc
        yield descriptor
    finally:
        os.close(descriptor)


def _loopback_json(url: str, *, token: str | None = None) -> dict[str, Any]:
    parsed_url = urlsplit(url)
    if (
        parsed_url.scheme != "http"
        or parsed_url.hostname != "127.0.0.1"
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.fragment
    ):
        raise BackupRestoreError("control-plane URL must be exact loopback HTTP")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(
            self,
            request: Any,
            file_pointer: Any,
            code: int,
            message: str,
            headers: Any,
            new_url: str,
        ) -> None:
            del request, file_pointer, code, message, headers, new_url

    headers = {"X-App-Access-Token": token} if token else {}
    request = urllib.request.Request(url, headers=headers, method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=5) as response:
            payload = response.read(1024 * 1024)
    except OSError as exc:
        raise BackupRestoreError(f"loopback request failed: {url}") from exc
    try:
        parsed = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BackupRestoreError(f"loopback endpoint returned invalid JSON: {url}") from exc
    if not isinstance(parsed, dict):
        raise BackupRestoreError(f"loopback endpoint returned a non-object: {url}")
    return parsed


def _active_work_count(config: DeployConfig) -> int:
    snapshot = _unit_snapshot(f"{SERVICE}.service", required=True)
    if snapshot["active"] == "inactive":
        if _listeners(config.backend_port):
            raise BackupRestoreError("backend unit is inactive but its port still has a listener")
        return 0
    if snapshot["active"] != "active":
        raise BackupRestoreError(f"backend has unsafe ActiveState={snapshot['active']}")
    _safe_regular(RUNTIME_ENV, mode=0o600)
    _safe_regular(RUNTIME_ENV_HELPER, mode=0o755)
    result = _run(
        (
            RUNTIME_ENV_HELPER,
            "authenticated-get",
            RUNTIME_ENV,
            "APP_ACCESS_TOKEN",
            f"http://127.0.0.1:{config.backend_port}/api/deployment/status",
        )
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise BackupRestoreError("deployment status returned invalid JSON") from exc
    count = payload.get("active_workflows") if isinstance(payload, dict) else None
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise BackupRestoreError("deployment status did not return a valid active-work count")
    return count


def _listeners(port: int) -> str:
    result = _run((SS, "-H", "-ltn", f"sport = :{port}"))
    return result.stdout.strip()


def _processes_matching(marker: str) -> list[int]:
    matches: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        command = raw.replace(b"\x00", b" ").decode("utf-8", "replace")
        if marker in command:
            matches.append(int(entry.name))
    return sorted(matches)


def _backend_processes() -> list[int]:
    return _processes_matching("backend.src.server:app")


def _managed_open_files() -> list[tuple[int, str]]:
    roots = tuple(f"{path}/" for path in EXPECTED_ROOTS)
    matches: list[tuple[int, str]] = []

    def managed(target: str) -> bool:
        normalized = target.removesuffix(" (deleted)")
        return any(
            normalized == str(root) or normalized.startswith(prefix)
            for root, prefix in zip(EXPECTED_ROOTS, roots, strict=True)
        )

    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        descriptor_dir = entry / "fd"
        try:
            descriptors = list(descriptor_dir.iterdir())
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        for descriptor in descriptors:
            try:
                target = os.readlink(descriptor)
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
            normalized = target.removesuffix(" (deleted)")
            if managed(normalized):
                matches.append((int(entry.name), normalized))
                break
        else:
            referenced = ""
            for link_name in ("cwd", "exe", "root"):
                try:
                    target = os.readlink(entry / link_name)
                except (FileNotFoundError, PermissionError, ProcessLookupError):
                    continue
                if managed(target):
                    referenced = target.removesuffix(" (deleted)")
                    break
            if not referenced:
                try:
                    mappings = (entry / "maps").read_text(
                        encoding="utf-8",
                        errors="replace",
                    )
                except (FileNotFoundError, PermissionError, ProcessLookupError):
                    mappings = ""
                for line in mappings.splitlines():
                    fields = line.split(maxsplit=5)
                    if len(fields) == 6 and fields[5].startswith("/") and managed(fields[5]):
                        referenced = fields[5].removesuffix(" (deleted)")
                        break
            if referenced:
                matches.append((int(entry.name), referenced))
    return sorted(matches)


def _assert_no_backend_writers(config: DeployConfig, *, include_web_ports: bool) -> None:
    processes = _backend_processes()
    if processes:
        raise BackupRestoreError(f"unmanaged backend process remains: PID {processes[0]}")
    if include_web_ports:
        cloudflared_processes = _processes_matching("cloudflared")
        if cloudflared_processes:
            raise BackupRestoreError(
                f"unmanaged cloudflared process remains: PID {cloudflared_processes[0]}"
            )
    open_files = _managed_open_files()
    if open_files:
        raise BackupRestoreError(
            f"process {open_files[0][0]} still has a managed path open"
        )
    ports = [config.backend_port, 80, 443] if include_web_ports else [config.backend_port]
    for port in dict.fromkeys(ports):
        if _listeners(port):
            raise BackupRestoreError(f"port {port} still has a listener")


def _quiesce_for_restore(config: DeployConfig) -> None:
    _ensure_restore_maintenance()
    for unit in (
        f"{SERVICE}-update.timer",
        f"{SERVICE}-update.service",
        *(_cloudflared_units()),
        f"{SERVICE}.service",
        "nginx.service",
    ):
        _disable_unit(unit, required=unit == f"{SERVICE}.service")
    _assert_no_backend_writers(config, include_web_ports=True)


def _sha256_file(path: Path) -> str:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    digest = hashlib.sha256()
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise BackupRestoreError(f"not a regular file: {path}")
        while chunk := os.read(descriptor, CHUNK_SIZE):
            digest.update(chunk)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _digest_add(digest: Any, value: str | bytes) -> None:
    encoded = value.encode("utf-8", "surrogateescape") if isinstance(value, str) else value
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)


def _immutable_release_digest(root: Path) -> str:
    digest = hashlib.sha256()

    def visit(path: Path, relative: str) -> None:
        metadata = path.lstat()
        _digest_add(digest, relative)
        _digest_add(
            digest,
            f"{stat.S_IFMT(metadata.st_mode):o}:{stat.S_IMODE(metadata.st_mode):o}",
        )
        if relative == ".venv":
            _digest_add(digest, "excluded-venv")
        elif stat.S_ISDIR(metadata.st_mode):
            for child in sorted(path.iterdir(), key=lambda item: item.name):
                child_relative = child.name if not relative else f"{relative}/{child.name}"
                if child_relative != ".release-ready":
                    visit(child, child_relative)
        elif stat.S_ISREG(metadata.st_mode):
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                while chunk := os.read(descriptor, CHUNK_SIZE):
                    digest.update(chunk)
            finally:
                os.close(descriptor)
        elif stat.S_ISLNK(metadata.st_mode):
            _digest_add(digest, os.readlink(path))
        else:
            raise BackupRestoreError(f"unsupported release entry: {relative}")

    visit(root, "")
    return digest.hexdigest()


def verify_active_release() -> tuple[Path, str]:
    try:
        selector = CURRENT_RELEASE.lstat()
    except FileNotFoundError as exc:
        raise BackupRestoreError("current release selector is missing") from exc
    if not stat.S_ISLNK(selector.st_mode) or selector.st_uid != 0 or selector.st_gid != 0:
        raise BackupRestoreError("current release selector is not a root-owned symlink")
    resolved = CURRENT_RELEASE.resolve(strict=True)
    commit = resolved.name
    if not SHA_RE.fullmatch(commit) or resolved != RELEASES_ROOT / commit:
        raise BackupRestoreError("current release is outside the exact releases directory")
    _safe_directory(resolved)
    marker = resolved / ".release-ready"
    _safe_regular(marker, mode=0o444)
    values: dict[str, str] = {}
    for line in marker.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in values:
            raise BackupRestoreError("release marker is ambiguous")
        values[key] = value
    required = {
        "schema",
        "commit",
        "requirements_sha256",
        "package_lock_sha256",
        "immutable_sha256",
    }
    if set(values) != required or values["schema"] != "2" or values["commit"] != commit:
        raise BackupRestoreError("release marker does not match the selected commit")
    requirements = resolved / "site/backend/requirements.txt"
    package_lock = resolved / "site/frontend/package-lock.json"
    version = resolved / "site/dist/version.json"
    uvicorn = resolved / ".venv/bin/uvicorn"
    index = resolved / "site/dist/index.html"
    uvicorn_metadata = _safe_regular(uvicorn)
    _safe_regular(index)
    if not stat.S_IMODE(uvicorn_metadata.st_mode) & 0o111:
        raise BackupRestoreError("release uvicorn entry point is not executable")
    if values["requirements_sha256"] != _sha256_file(requirements):
        raise BackupRestoreError("release requirements digest does not match")
    if values["package_lock_sha256"] != _sha256_file(package_lock):
        raise BackupRestoreError("release frontend lock digest does not match")
    if values["immutable_sha256"] != _immutable_release_digest(resolved):
        raise BackupRestoreError("release immutable digest does not match")
    allowed_links = {
        resolved / "site/backend" / name: PERSIST_ROOT / name
        for name in ("uploads", "outputs", "runtime")
    }
    reject_nested_mounts((resolved,))
    for directory, names, files in os.walk(resolved, followlinks=False):
        directory_path = Path(directory)
        metadata = directory_path.lstat()
        if (
            metadata.st_uid != 0
            or metadata.st_gid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise BackupRestoreError(
                f"release tree has unsafe directory metadata: {directory_path}"
            )
        for name in (*names, *files):
            child = directory_path / name
            child_metadata = child.lstat()
            if child_metadata.st_uid != 0 or child_metadata.st_gid != 0:
                raise BackupRestoreError(f"release tree has an unowned entry: {child}")
            if stat.S_ISLNK(child_metadata.st_mode):
                continue
            if (
                not (
                    stat.S_ISDIR(child_metadata.st_mode)
                    or stat.S_ISREG(child_metadata.st_mode)
                )
                or stat.S_IMODE(child_metadata.st_mode) & 0o022
            ):
                raise BackupRestoreError(f"release tree has an unsafe entry: {child}")
    for runtime_root in (
        resolved / ".venv",
        resolved / "site/dist",
        resolved / "site/backend",
    ):
        if runtime_root.is_symlink() or not runtime_root.is_dir():
            raise BackupRestoreError(f"required release directory is unsafe: {runtime_root}")
        for directory, names, files in os.walk(runtime_root, followlinks=False):
            directory_path = Path(directory)
            metadata = directory_path.lstat()
            if (
                metadata.st_uid != 0
                or metadata.st_gid != 0
                or stat.S_IMODE(metadata.st_mode) & 0o022
            ):
                raise BackupRestoreError(f"release directory is writable or unowned: {directory_path}")
            for name in (*names, *files):
                child = directory_path / name
                child_metadata = child.lstat()
                if stat.S_ISLNK(child_metadata.st_mode):
                    expected = allowed_links.get(child)
                    if expected is None or child.resolve(strict=True) != expected.resolve(strict=True):
                        raise BackupRestoreError(f"release contains an unexpected symlink: {child}")
                elif (
                    not (
                        stat.S_ISDIR(child_metadata.st_mode)
                        or stat.S_ISREG(child_metadata.st_mode)
                    )
                    or child_metadata.st_uid != 0
                    or child_metadata.st_gid != 0
                    or stat.S_IMODE(child_metadata.st_mode) & 0o022
                ):
                    raise BackupRestoreError(f"release entry is unsafe: {child}")
    try:
        version_payload = json.loads(version.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupRestoreError("release version metadata is invalid") from exc
    if not isinstance(version_payload, dict) or version_payload.get("commit") != commit:
        raise BackupRestoreError("release version metadata does not match")
    return resolved, commit


def _decode_mount_path(raw: str) -> Path:
    return Path(
        re.sub(
            r"\\([0-7]{3})",
            lambda match: chr(int(match.group(1), 8)),
            raw,
        )
    )


def _mount_records() -> list[tuple[Path, str]]:
    records: list[tuple[Path, str]] = []
    for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) < 10 or "-" not in fields:
            raise BackupRestoreError("could not parse mountinfo")
        separator = fields.index("-")
        if separator + 1 >= len(fields):
            raise BackupRestoreError("could not parse mountinfo filesystem type")
        records.append((_decode_mount_path(fields[4]), fields[separator + 1]))
    return records


def _mount_points() -> list[Path]:
    return [mount for mount, _filesystem in _mount_records()]


def _mount_for_path(path: Path) -> tuple[Path, str]:
    candidates = [
        (mount, filesystem)
        for mount, filesystem in _mount_records()
        if mount == path or mount in path.parents
    ]
    if not candidates:
        raise BackupRestoreError(f"could not identify the filesystem for {path}")
    return max(candidates, key=lambda item: len(item[0].parts))


def _device_uses_luks(
    device: int,
    *,
    sys_dev_block: Path = Path("/sys/dev/block"),
) -> bool:
    """Return whether a mounted block device has a LUKS dm-crypt ancestor."""
    pending = [f"{os.major(device)}:{os.minor(device)}"]
    seen: set[str] = set()
    while pending:
        major_minor = pending.pop()
        if major_minor in seen:
            continue
        seen.add(major_minor)
        if len(seen) > 128:
            raise BackupRestoreError("backup block-device ancestry is unexpectedly deep")
        block = sys_dev_block / major_minor
        try:
            block.resolve(strict=True)
        except (FileNotFoundError, OSError, RuntimeError) as exc:
            raise BackupRestoreError(
                f"cannot verify backup block-device ancestry for {major_minor}"
            ) from exc
        dm_uuid = block / "dm/uuid"
        try:
            identifier = dm_uuid.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            identifier = ""
        except (OSError, UnicodeError) as exc:
            raise BackupRestoreError(
                f"cannot read device-mapper identity for {major_minor}"
            ) from exc
        if identifier.startswith(("CRYPT-LUKS1-", "CRYPT-LUKS2-")):
            return True
        slaves = block / "slaves"
        try:
            children = sorted(slaves.iterdir(), key=lambda path: path.name)
        except FileNotFoundError:
            children = []
        except OSError as exc:
            raise BackupRestoreError(
                f"cannot inspect block-device parents for {major_minor}"
            ) from exc
        for child in children:
            try:
                child_device = (child / "dev").read_text(encoding="ascii").strip()
            except (OSError, UnicodeError) as exc:
                raise BackupRestoreError(
                    f"cannot identify parent block device for {major_minor}"
                ) from exc
            if not re.fullmatch(r"[0-9]+:[0-9]+", child_device):
                raise BackupRestoreError(
                    f"invalid parent block-device identity below {major_minor}"
                )
            pending.append(child_device)
    return False


def reject_nested_mounts(roots: Sequence[Path], mounts: Sequence[Path] | None = None) -> None:
    mount_points = list(mounts) if mounts is not None else _mount_points()
    for root in roots:
        for mount in mount_points:
            if mount == root or root in mount.parents:
                raise BackupRestoreError(
                    f"managed roots and their descendants must not be mount points: {mount}"
                )


def _tarinfo(path: Path, archive_name: str, metadata: os.stat_result) -> tarfile.TarInfo:
    info = tarfile.TarInfo(archive_name)
    info.mode = stat.S_IMODE(metadata.st_mode)
    info.uid = metadata.st_uid
    info.gid = metadata.st_gid
    info.mtime = int(metadata.st_mtime)
    if stat.S_ISDIR(metadata.st_mode):
        info.type = tarfile.DIRTYPE
    elif stat.S_ISREG(metadata.st_mode):
        info.type = tarfile.REGTYPE
        info.size = metadata.st_size
    else:
        raise BackupRestoreError(f"unsupported file type in backup: {path}")
    return info


def _add_tree(archive: tarfile.TarFile, path: Path, archive_name: str, device: int) -> None:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        expected = DERIVED_LINKS.get(path)
        if expected is not None and path.resolve(strict=True) == expected.resolve(strict=True):
            return
    if metadata.st_dev != device:
        raise BackupRestoreError(f"filesystem boundary changed while backing up {path}")
    info = _tarinfo(path, archive_name, metadata)
    if info.isdir():
        archive.addfile(info)
        for child in sorted(path.iterdir(), key=lambda item: item.name):
            _add_tree(archive, child, f"{archive_name}/{child.name}", device)
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != metadata.st_dev
            or opened.st_ino != metadata.st_ino
            or opened.st_size != metadata.st_size
        ):
            raise BackupRestoreError(f"file changed while opening backup source: {path}")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            archive.addfile(info, handle)
        finished = os.fstat(descriptor)
        if (
            finished.st_dev != opened.st_dev
            or finished.st_ino != opened.st_ino
            or finished.st_size != opened.st_size
            or finished.st_mtime_ns != opened.st_mtime_ns
            or finished.st_ctime_ns != opened.st_ctime_ns
        ):
            raise BackupRestoreError(f"file changed while being backed up: {path}")
    finally:
        os.close(descriptor)


def _archive_manifest(
    config: DeployConfig,
    commit: str,
    source_bundle_sha256: str,
) -> dict[str, Any]:
    return {
        "schema": 2,
        "created_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "active_commit": commit,
        "update_remote_url": config.update_remote_url,
        "roots": [path.as_posix() for path in ARCHIVE_ROOTS],
        "restore_protocol": 1,
        "source_bundle": SOURCE_BUNDLE_NAME,
        "source_bundle_sha256": source_bundle_sha256,
    }


def _create_archive(
    descriptor: int,
    config: DeployConfig,
    commit: str,
    source_bundle: Path,
    source_bundle_sha256: str,
) -> None:
    with os.fdopen(os.dup(descriptor), "wb") as raw:
        with (
            gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed,
            tarfile.open(fileobj=compressed, mode="w|") as archive,
        ):
            manifest_bytes = (
                json.dumps(
                    _archive_manifest(config, commit, source_bundle_sha256),
                    sort_keys=True,
                )
                + "\n"
            ).encode()
            manifest_info = tarfile.TarInfo(MANIFEST_NAME)
            manifest_info.mode = 0o600
            manifest_info.uid = 0
            manifest_info.gid = 0
            manifest_info.mtime = int(time.time())
            manifest_info.size = len(manifest_bytes)
            archive.addfile(manifest_info, io.BytesIO(manifest_bytes))
            source_metadata = source_bundle.lstat()
            if (
                not stat.S_ISREG(source_metadata.st_mode)
                or source_metadata.st_uid != 0
                or source_metadata.st_gid != 0
                or stat.S_IMODE(source_metadata.st_mode) != 0o600
            ):
                raise BackupRestoreError("source bundle metadata is unsafe")
            bundle_info = _tarinfo(source_bundle, SOURCE_BUNDLE_NAME, source_metadata)
            bundle_descriptor = os.open(source_bundle, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                with os.fdopen(bundle_descriptor, "rb", closefd=False) as handle:
                    archive.addfile(bundle_info, handle)
            finally:
                os.close(bundle_descriptor)
            for root, archive_root in zip(EXPECTED_ROOTS, ARCHIVE_ROOTS, strict=True):
                metadata = _safe_directory(root)
                _add_tree(archive, root, archive_root.as_posix(), metadata.st_dev)
        raw.flush()
        os.fsync(raw.fileno())


def _source_size(roots: Sequence[Path]) -> int:
    total = 0
    for root in roots:
        for directory, names, files in os.walk(root, followlinks=False):
            directory_path = Path(directory)
            for name in names:
                child = directory_path / name
                if child.is_symlink() and (
                    child not in DERIVED_LINKS
                    or child.resolve(strict=True) != DERIVED_LINKS[child].resolve(strict=True)
                ):
                    raise BackupRestoreError(
                        f"backup source contains a symlink: {child}"
                    )
            for name in files:
                child = directory_path / name
                metadata = child.lstat()
                if stat.S_ISLNK(metadata.st_mode):
                    expected = DERIVED_LINKS.get(child)
                    if (
                        expected is not None
                        and child.resolve(strict=True) == expected.resolve(strict=True)
                    ):
                        continue
                if not stat.S_ISREG(metadata.st_mode):
                    raise BackupRestoreError(f"backup source contains a special file: {child}")
                total += metadata.st_size
                if total > MAX_ARCHIVE_EXPANDED_BYTES:
                    raise BackupRestoreError("managed data exceeds the supported backup size")
    return total


def _safe_destination(path: Path) -> tuple[int, Path]:
    resolved = path.resolve(strict=True)
    descriptor = os.open(resolved, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        os.close(descriptor)
        raise BackupRestoreError(
            "backup destination must be a root-owned directory not writable by other users"
        )
    mount_point, filesystem = _mount_for_path(resolved)
    if mount_point == Path("/") or filesystem not in BACKUP_FILESYSTEMS:
        os.close(descriptor)
        raise BackupRestoreError(
            "backup destination must be on a separately mounted Linux filesystem "
            f"({', '.join(sorted(BACKUP_FILESYSTEMS))})"
        )
    try:
        encrypted = _device_uses_luks(metadata.st_dev)
    except Exception:
        os.close(descriptor)
        raise
    if not encrypted:
        os.close(descriptor)
        raise BackupRestoreError(
            "backup destination must be backed by a verifiable LUKS dm-crypt device"
        )
    return descriptor, resolved


def _entry_exists_at(directory_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _create_partial_at(
    directory_fd: int,
    *,
    prefix: str,
    suffix: str,
) -> tuple[int, str]:
    for _attempt in range(128):
        name = f"{prefix}{secrets.token_hex(12)}{suffix}"
        try:
            descriptor = os.open(
                name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | os.O_CLOEXEC
                | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_fd,
            )
        except FileExistsError:
            continue
        return descriptor, name
    raise BackupRestoreError("could not allocate a unique backup partial file")


def _unlink_at(directory_fd: int, name: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.unlink(name, dir_fd=directory_fd)


def _cleanup_interrupted_backup_partials(directory_fd: int) -> None:
    removed = False
    for name in os.listdir(directory_fd):
        if INTERRUPTED_BACKUP_PARTIAL_RE.fullmatch(name) is None:
            continue
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise BackupRestoreError(
                f"interrupted backup partial has unsafe metadata: {name}"
            )
        os.unlink(name, dir_fd=directory_fd)
        removed = True
    if removed:
        os.fsync(directory_fd)


def _destination_path_matches(directory_fd: int, path: Path) -> bool:
    opened = os.fstat(directory_fd)
    try:
        current = path.lstat()
    except FileNotFoundError:
        return False
    return (
        stat.S_ISDIR(current.st_mode)
        and not stat.S_ISLNK(current.st_mode)
        and current.st_dev == opened.st_dev
        and current.st_ino == opened.st_ino
    )


def _assert_current_helper_matches_release(release: Path) -> None:
    installed = Path(__file__).resolve()
    source = release / "deploy/backup_restore.py"
    _safe_regular(installed, mode=0o755)
    _safe_regular(source)
    if _sha256_file(installed) != _sha256_file(source):
        raise BackupRestoreError(
            "installed backup helper does not match the selected release; "
            "rerun the reviewed installer before backing up"
        )


def _refresh_installed_helper_from_active_release() -> None:
    release, _ = verify_active_release()
    source = release / "deploy/backup_restore.py"
    source_metadata = _safe_regular(source)
    if source_metadata.st_nlink != 1:
        raise BackupRestoreError("active-release backup helper has an unsafe link count")
    parent_metadata = _safe_directory(INSTALLED_HELPER.parent)
    if stat.S_IMODE(parent_metadata.st_mode) & 0o022:
        raise BackupRestoreError("installed helper directory is writable by other users")
    if INSTALLED_HELPER.exists() or INSTALLED_HELPER.is_symlink():
        installed_metadata = _safe_regular(INSTALLED_HELPER, mode=0o755)
        if installed_metadata.st_nlink != 1:
            raise BackupRestoreError("installed backup helper has an unsafe link count")

    source_descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    staged_descriptor = -1
    staged: Path | None = None
    try:
        staged_descriptor, staged_name = tempfile.mkstemp(
            prefix=f".{INSTALLED_HELPER.name}.",
            dir=INSTALLED_HELPER.parent,
        )
        staged = Path(staged_name)
        opened_source = os.fstat(source_descriptor)
        if (
            not stat.S_ISREG(opened_source.st_mode)
            or opened_source.st_dev != source_metadata.st_dev
            or opened_source.st_ino != source_metadata.st_ino
            or opened_source.st_nlink != 1
        ):
            raise BackupRestoreError("active-release backup helper changed while opening")
        os.fchmod(staged_descriptor, 0o755)
        os.fchown(staged_descriptor, 0, 0)
        while chunk := os.read(source_descriptor, CHUNK_SIZE):
            view = memoryview(chunk)
            while view:
                written = os.write(staged_descriptor, view)
                view = view[written:]
        os.fsync(staged_descriptor)
        os.close(staged_descriptor)
        staged_descriptor = -1
        if _sha256_file(staged) != _sha256_file(source):
            raise BackupRestoreError("staged backup helper content changed")
        os.replace(staged, INSTALLED_HELPER)
        _fsync_directory(INSTALLED_HELPER.parent)
        installed_metadata = _safe_regular(INSTALLED_HELPER, mode=0o755)
        if (
            installed_metadata.st_nlink != 1
            or _sha256_file(INSTALLED_HELPER) != _sha256_file(source)
        ):
            raise BackupRestoreError("refreshed backup helper failed verification")
    finally:
        os.close(source_descriptor)
        if staged_descriptor >= 0:
            os.close(staged_descriptor)
        if staged is not None:
            with contextlib.suppress(FileNotFoundError):
                staged.unlink()


def _finish_backup_maintenance(
    config: DeployConfig,
    commit: str,
    timer: dict[str, str],
) -> None:
    _start_unit(f"{SERVICE}.service")
    _wait_for_exact_health(config, commit)
    if timer["active"] == "active":
        _start_unit(f"{SERVICE}-update.timer")
    _remove_maintenance()


def backup(destination: Path) -> tuple[Path, Path]:
    config = read_deploy_config()
    with deployment_lock():
        blockers = (
            (TRANSACTION_STATE, "restore"),
            (ACTIVATION_STATE, "activation"),
            (INTEGRITY_STATE, "integrity-repair"),
            (MAINTENANCE_FILE, "maintenance"),
        )
        for blocker, label in blockers:
            if blocker.exists() or blocker.is_symlink():
                raise BackupRestoreError(f"a {label} transaction or gate is pending")
        release, commit = verify_active_release()
        _assert_current_helper_matches_release(release)
        validate_runtime_environment()
        _ensure_transaction_root()
        _cleanup_orphan_transaction_artifacts()
        checkout, owner_name = _validate_checkout(
            Path(config.values.get("SOURCE_REPO_DIR", "")),
            commit,
            config.update_remote_url,
        )
        source_bundle, source_bundle_digest = _create_source_bundle(
            checkout,
            pwd.getpwnam(owner_name),
            commit,
        )
        destination_fd = -1
        timer: dict[str, str] | None = None
        try:
            reject_nested_mounts(EXPECTED_ROOTS)
            destination_fd, destination_path = _safe_destination(destination)
            _cleanup_interrupted_backup_partials(destination_fd)
            required_bytes = (
                _source_size(EXPECTED_ROOTS)
                + source_bundle.stat().st_size
                + SPACE_MARGIN
            )
            filesystem = os.fstatvfs(destination_fd)
            if filesystem.f_bavail * filesystem.f_frsize < required_bytes:
                raise BackupRestoreError(
                    "backup destination lacks source size plus the 512 MiB safety margin"
                )
            backend = _unit_snapshot(f"{SERVICE}.service", required=True)
            timer = _unit_snapshot(f"{SERVICE}-update.timer")
            if backend["active"] != "active":
                raise BackupRestoreError(
                    "backup requires the managed backend to be active and healthy"
                )
            if timer["active"] not in {"active", "inactive"}:
                raise BackupRestoreError(
                    f"update timer has unsafe ActiveState={timer['active']}"
            )
            _write_maintenance()
            with contextlib.ExitStack() as quiescence:
                quiescence.enter_context(activity_lock_exclusive())

                def finish_maintenance() -> None:
                    _finish_backup_maintenance(config, commit, timer)

                quiescence.callback(finish_maintenance)
                time.sleep(1)
                _wait_for_exact_health(config, commit)
                if _active_work_count(config) != 0:
                    raise BackupRestoreError("backup requires an exact zero active-work count")
                if timer["active"] == "active":
                    _stop_unit(f"{SERVICE}-update.timer")
                _stop_unit(f"{SERVICE}.service", required=True)
                _assert_no_backend_writers(config, include_web_ports=False)
                _, stopped_commit = verify_active_release()
                if stopped_commit != commit:
                    raise BackupRestoreError(
                        "active release changed while entering backup maintenance"
                    )
                stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                archive_name = f"{SERVICE}-{stamp}-{commit}.tgz"
                checksum_name = f"{archive_name}.sha256"
                archive_tmp_fd, archive_tmp_name = _create_partial_at(
                    destination_fd,
                    prefix=f".{archive_name}.",
                    suffix=".partial",
                )
                checksum_tmp_fd, checksum_tmp_name = _create_partial_at(
                    destination_fd,
                    prefix=f".{checksum_name}.",
                    suffix=".partial",
                )
                anchored_destination = Path(f"/proc/self/fd/{destination_fd}")
                archive_tmp = anchored_destination / archive_tmp_name
                archive_path = destination_path / archive_name
                checksum_path = destination_path / checksum_name
                if _entry_exists_at(destination_fd, archive_name) or _entry_exists_at(
                    destination_fd, checksum_name
                ):
                    os.close(archive_tmp_fd)
                    os.close(checksum_tmp_fd)
                    raise BackupRestoreError(
                        "refusing to overwrite an existing backup generation"
                    )
                try:
                    os.fchmod(archive_tmp_fd, 0o600)
                    os.fchown(archive_tmp_fd, 0, 0)
                    os.fchmod(checksum_tmp_fd, 0o600)
                    os.fchown(checksum_tmp_fd, 0, 0)
                    if (
                        stat.S_IMODE(os.fstat(archive_tmp_fd).st_mode) != 0o600
                        or stat.S_IMODE(os.fstat(checksum_tmp_fd).st_mode) != 0o600
                    ):
                        raise BackupRestoreError(
                            "backup destination cannot enforce root-only file permissions"
                        )
                    _create_archive(
                        archive_tmp_fd,
                        config,
                        commit,
                        source_bundle,
                        source_bundle_digest,
                    )
                    os.close(archive_tmp_fd)
                    archive_tmp_fd = -1
                    manifest, _ = validate_archive(archive_tmp, expected_commit=commit)
                    if manifest["active_commit"] != commit:
                        raise BackupRestoreError("backup archive failed its commit self-check")
                    digest = _sha256_file(archive_tmp)
                    with os.fdopen(checksum_tmp_fd, "w", encoding="ascii") as handle:
                        checksum_tmp_fd = -1
                        handle.write(f"{digest}  {archive_name}\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                    # Publish the checksum before the archive. A kill can leave
                    # only a harmless checksum, never an unpaired secret archive.
                    os.rename(
                        checksum_tmp_name,
                        checksum_name,
                        src_dir_fd=destination_fd,
                        dst_dir_fd=destination_fd,
                    )
                    os.rename(
                        archive_tmp_name,
                        archive_name,
                        src_dir_fd=destination_fd,
                        dst_dir_fd=destination_fd,
                    )
                    os.fsync(destination_fd)
                    archive_metadata = os.stat(
                        archive_name,
                        dir_fd=destination_fd,
                        follow_symlinks=False,
                    )
                    checksum_metadata = os.stat(
                        checksum_name,
                        dir_fd=destination_fd,
                        follow_symlinks=False,
                    )
                    if (
                        not stat.S_ISREG(archive_metadata.st_mode)
                        or not stat.S_ISREG(checksum_metadata.st_mode)
                        or stat.S_IMODE(archive_metadata.st_mode) != 0o600
                        or stat.S_IMODE(checksum_metadata.st_mode) != 0o600
                        or archive_metadata.st_uid != 0
                        or archive_metadata.st_gid != 0
                        or checksum_metadata.st_uid != 0
                        or checksum_metadata.st_gid != 0
                    ):
                        raise BackupRestoreError(
                            "completed backup files are not protected as mode 0600"
                        )
                    if not _destination_path_matches(destination_fd, destination_path):
                        raise BackupRestoreError(
                            "backup destination path changed; files remained anchored "
                            "to the originally opened directory"
                        )
                finally:
                    if archive_tmp_fd >= 0:
                        os.close(archive_tmp_fd)
                    if checksum_tmp_fd >= 0:
                        os.close(checksum_tmp_fd)
                    _unlink_at(destination_fd, archive_tmp_name)
                    _unlink_at(destination_fd, checksum_tmp_name)
                return archive_path, checksum_path
        finally:
            if destination_fd >= 0:
                os.close(destination_fd)
            with contextlib.suppress(FileNotFoundError):
                source_bundle.unlink()
                _fsync_directory(TRANSACTION_ROOT)


def _parse_checksum(checksum_path: Path, archive_name: str) -> str:
    descriptor = os.open(checksum_path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size > 4096
        ):
            raise BackupRestoreError("checksum is not a small regular file")
        raw = os.read(descriptor, 4096).decode("ascii")
    except UnicodeDecodeError as exc:
        raise BackupRestoreError("checksum is not ASCII") from exc
    finally:
        os.close(descriptor)
    match = re.fullmatch(r"([0-9a-f]{64})  ([^\n]+)\n?", raw)
    if not match or match.group(2) != archive_name:
        raise BackupRestoreError("checksum does not name the selected archive exactly")
    return match.group(1)


def _copy_archive_once(archive: Path, checksum: Path) -> tuple[Path, str]:
    if archive.name in {"", ".", ".."}:
        raise BackupRestoreError("archive filename is invalid")
    if archive.is_symlink() or checksum.is_symlink():
        raise BackupRestoreError("restore source files must not be symlinks")
    source_directory_fd, source_directory = _safe_destination(archive.parent)
    try:
        if checksum.parent.resolve(strict=True) != source_directory:
            raise BackupRestoreError(
                "archive and checksum must share one verified LUKS source directory"
            )
        expected = _parse_checksum(checksum, archive.name)
        _ensure_transaction_root()
        source_fd = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            temporary_fd, temporary_name = tempfile.mkstemp(
                prefix=".incoming.", suffix=".tgz", dir=TRANSACTION_ROOT
            )
        except Exception:
            os.close(source_fd)
            raise
        temporary = Path(temporary_name)
        digest = hashlib.sha256()
        try:
            try:
                source_metadata = os.fstat(source_fd)
                if (
                    not stat.S_ISREG(source_metadata.st_mode)
                    or source_metadata.st_uid != 0
                    or source_metadata.st_gid != 0
                    or source_metadata.st_nlink != 1
                    or stat.S_IMODE(source_metadata.st_mode) != 0o600
                ):
                    raise BackupRestoreError("archive source metadata is unsafe")
                if source_metadata.st_size > MAX_ARCHIVE_EXPANDED_BYTES:
                    raise BackupRestoreError("archive is larger than the supported restore limit")
                if (
                    shutil.disk_usage(TRANSACTION_ROOT).free
                    < source_metadata.st_size + SPACE_MARGIN
                ):
                    raise BackupRestoreError(
                        "restore staging lacks archive size plus the 512 MiB safety margin"
                    )
                os.fchmod(temporary_fd, 0o600)
                os.fchown(temporary_fd, 0, 0)
                while chunk := os.read(source_fd, CHUNK_SIZE):
                    digest.update(chunk)
                    view = memoryview(chunk)
                    while view:
                        written = os.write(temporary_fd, view)
                        view = view[written:]
                os.fsync(temporary_fd)
            finally:
                os.close(source_fd)
                os.close(temporary_fd)
            actual = digest.hexdigest()
            if not secrets.compare_digest(expected, actual):
                raise BackupRestoreError("archive checksum does not match")
            staged = TRANSACTION_ROOT / f"{actual}.tgz"
            if staged.exists():
                _safe_regular(staged, mode=0o600)
                if _sha256_file(staged) != actual:
                    raise BackupRestoreError("existing staged archive has the wrong digest")
                temporary.unlink()
            else:
                os.replace(temporary, staged)
            _fsync_directory(TRANSACTION_ROOT)
            if not _destination_path_matches(source_directory_fd, source_directory):
                raise BackupRestoreError("restore source directory changed during the copy")
            return staged, actual
        finally:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()
    finally:
        os.close(source_directory_fd)


def _member_root(name: str) -> Path:
    if (
        len(name.encode("utf-8", "surrogateescape")) > MAX_ARCHIVE_PATH_BYTES
        or "\x00" in name
    ):
        raise BackupRestoreError("archive member path exceeds the supported limit")
    pure = PurePosixPath(name)
    if (
        pure.is_absolute()
        or not pure.parts
        or len(pure.parts) > MAX_ARCHIVE_PATH_PARTS
        or pure.as_posix() != name
        or any(
            part in {"", ".", ".."} or len(part.encode("utf-8", "surrogateescape")) > 255
            for part in pure.parts
        )
    ):
        raise BackupRestoreError(f"unsafe archive member path: {name}")
    path = Path(*pure.parts)
    if path in {Path(MANIFEST_NAME), Path(SOURCE_BUNDLE_NAME)}:
        return path
    for root in ARCHIVE_ROOTS:
        if path == root or root in path.parents:
            return root
    raise BackupRestoreError(f"archive member is outside the managed roots: {name}")


def validate_archive(
    path: Path,
    *,
    expected_commit: str | None = None,
) -> tuple[dict[str, Any], list[tarfile.TarInfo]]:
    _safe_regular(path, mode=0o600)
    seen: set[str] = set()
    roots_seen: set[Path] = set()
    members: list[tarfile.TarInfo] = []
    manifest: dict[str, Any] | None = None
    source_bundle_digest: str | None = None
    expanded_bytes = 0
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            for member in archive:
                if member.name in seen:
                    raise BackupRestoreError(f"duplicate archive member: {member.name}")
                seen.add(member.name)
                root = _member_root(member.name)
                if member.pax_headers and (
                    set(member.pax_headers) != {"path"}
                    or member.pax_headers["path"] != member.name
                ):
                    raise BackupRestoreError(
                        f"unsupported PAX metadata on archive member: {member.name}"
                    )
                if root == Path(MANIFEST_NAME):
                    if not member.isfile() or member.size > 64 * 1024:
                        raise BackupRestoreError("backup manifest is not a small regular file")
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        raise BackupRestoreError("backup manifest cannot be read")
                    manifest = json.loads(extracted.read().decode("utf-8"))
                    continue
                if root == Path(SOURCE_BUNDLE_NAME):
                    if (
                        source_bundle_digest is not None
                        or not member.isfile()
                        or member.mode != 0o600
                    ):
                        raise BackupRestoreError("source bundle entry is invalid")
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        raise BackupRestoreError("source bundle cannot be read")
                    bundle_digest = hashlib.sha256()
                    bundle_size = 0
                    while chunk := extracted.read(CHUNK_SIZE):
                        bundle_size += len(chunk)
                        if bundle_size > MAX_ARCHIVE_EXPANDED_BYTES:
                            raise BackupRestoreError("source bundle exceeds the restore limit")
                        bundle_digest.update(chunk)
                    source_bundle_digest = bundle_digest.hexdigest()
                    expanded_bytes += bundle_size
                    if expanded_bytes > MAX_ARCHIVE_EXPANDED_BYTES:
                        raise BackupRestoreError("archive exceeds the restore expansion limits")
                    continue
                if not (member.isdir() or member.isfile()):
                    raise BackupRestoreError(f"unsupported archive member type: {member.name}")
                if member.mode & 0o7022:
                    raise BackupRestoreError(f"unsafe mode bits in archive: {member.name}")
                expanded_bytes += member.size
                if (
                    len(seen) > MAX_ARCHIVE_MEMBERS
                    or expanded_bytes > MAX_ARCHIVE_EXPANDED_BYTES
                ):
                    raise BackupRestoreError("archive exceeds the restore expansion limits")
                roots_seen.add(root)
                members.append(member)
    except (tarfile.TarError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BackupRestoreError("archive validation failed") from exc
    if manifest is None or not isinstance(manifest, dict):
        raise BackupRestoreError("archive is missing its manifest")
    if set(manifest) != {
        "schema",
        "created_utc",
        "active_commit",
        "update_remote_url",
        "roots",
        "restore_protocol",
        "source_bundle",
        "source_bundle_sha256",
    }:
        raise BackupRestoreError("backup manifest has unexpected fields")
    if (
        manifest["schema"] != 2
        or manifest["restore_protocol"] != 1
        or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
            str(manifest["created_utc"]),
        )
        or not SHA_RE.fullmatch(str(manifest["active_commit"]))
        or not GITHUB_REMOTE_RE.fullmatch(str(manifest["update_remote_url"]))
        or manifest["roots"] != [root.as_posix() for root in ARCHIVE_ROOTS]
        or roots_seen != set(ARCHIVE_ROOTS)
        or manifest["source_bundle"] != SOURCE_BUNDLE_NAME
        or not re.fullmatch(r"[0-9a-f]{64}", str(manifest["source_bundle_sha256"]))
        or source_bundle_digest != manifest["source_bundle_sha256"]
    ):
        raise BackupRestoreError("backup manifest does not satisfy the restore contract")
    if expected_commit is not None and manifest["active_commit"] != expected_commit:
        raise BackupRestoreError("backup commit does not match the reviewed checkout")
    return manifest, members


def _transaction_id(manifest: dict[str, Any], digest: str) -> str:
    timestamp = datetime.strptime(
        manifest["created_utc"],
        "%Y-%m-%dT%H:%M:%SZ",
    ).replace(tzinfo=UTC)
    return f"{timestamp.strftime('%Y%m%dT%H%M%SZ')}-{digest[:16]}"


def _extract_source_bundle(
    archive_path: Path,
    manifest: dict[str, Any],
    archive_digest: str,
) -> Path:
    bundle = TRANSACTION_ROOT / f"source-{archive_digest}.bundle"
    if bundle.exists() or bundle.is_symlink():
        _safe_regular(bundle, mode=0o600)
        if _sha256_file(bundle) != manifest["source_bundle_sha256"]:
            raise BackupRestoreError("existing transaction source bundle has the wrong digest")
        return bundle

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".source-{archive_digest}.",
        suffix=".partial",
        dir=TRANSACTION_ROOT,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, 0, 0)
        found = False
        digest = hashlib.sha256()
        with tarfile.open(archive_path, mode="r:gz") as archive:
            for member in archive:
                if member.name != SOURCE_BUNDLE_NAME:
                    continue
                if found or not member.isfile():
                    raise BackupRestoreError("backup source bundle is ambiguous")
                found = True
                source = archive.extractfile(member)
                if source is None:
                    raise BackupRestoreError("backup source bundle cannot be read")
                while chunk := source.read(CHUNK_SIZE):
                    digest.update(chunk)
                    view = memoryview(chunk)
                    while view:
                        written = os.write(descriptor, view)
                        view = view[written:]
        if not found or digest.hexdigest() != manifest["source_bundle_sha256"]:
            raise BackupRestoreError("backup source bundle digest does not match")
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, bundle)
        _fsync_directory(TRANSACTION_ROOT)
        return bundle
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _harden_root_tree(root: Path) -> None:
    for directory, names, files in os.walk(root, topdown=False, followlinks=False):
        directory_path = Path(directory)
        for name in (*names, *files):
            child = directory_path / name
            metadata = child.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                os.lchown(child, 0, 0)
            elif stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode):
                os.chown(child, 0, 0)
                os.chmod(child, stat.S_IMODE(metadata.st_mode) & ~0o022)
            else:
                raise BackupRestoreError(f"installer snapshot contains a special file: {child}")
        os.chown(directory_path, 0, 0)
        os.chmod(directory_path, stat.S_IMODE(directory_path.lstat().st_mode) & ~0o022)


def _materialize_installer(
    archive_path: Path,
    manifest: dict[str, Any],
    archive_digest: str,
    transaction_id: str,
) -> tuple[Path, str]:
    bundle = _extract_source_bundle(archive_path, manifest, archive_digest)
    installer = TRANSACTION_ROOT / f"installer-{transaction_id}"
    build_stage = TRANSACTION_ROOT / f".installer-{transaction_id}.partial"
    marker = installer / ".uconn-installer-snapshot"
    if build_stage.exists() or build_stage.is_symlink():
        _safe_directory(build_stage)
        reject_nested_mounts((build_stage,))
        shutil.rmtree(build_stage)
        _fsync_directory(TRANSACTION_ROOT)
    if installer.exists() or installer.is_symlink():
        if installer.is_symlink() or not installer.is_dir():
            raise BackupRestoreError("installer snapshot path is unsafe")
        _safe_regular(marker, mode=0o400)
        try:
            marker_payload = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise BackupRestoreError("installer snapshot marker is invalid") from exc
        if marker_payload != {
            "commit": manifest["active_commit"],
            "transaction_id": transaction_id,
        }:
            raise BackupRestoreError("installer snapshot marker does not match")
    else:
        try:
            build_stage.mkdir(mode=0o700)
            os.chown(build_stage, 0, 0)
            os.chmod(build_stage, 0o700)
            _fsync_directory(TRANSACTION_ROOT)
            _clean_root_git(
                "clone",
                "--no-checkout",
                "--no-local",
                str(bundle),
                str(build_stage),
            )
            _clean_root_git(
                "-C",
                str(build_stage),
                "checkout",
                "--detach",
                "--force",
                manifest["active_commit"],
            )
            _clean_root_git(
                "-C",
                str(build_stage),
                "remote",
                "set-url",
                "origin",
                manifest["update_remote_url"],
            )
            _harden_root_tree(build_stage)
            staged_marker = build_stage / ".uconn-installer-snapshot"
            descriptor = os.open(
                staged_marker,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o400,
            )
            try:
                payload = json.dumps(
                    {
                        "commit": manifest["active_commit"],
                        "transaction_id": transaction_id,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
                os.write(descriptor, payload + b"\n")
                os.fchmod(descriptor, 0o400)
                os.fchown(descriptor, 0, 0)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            _fsync_tree(build_stage)
            os.replace(build_stage, installer)
            _fsync_directory(TRANSACTION_ROOT)
        except Exception:
            if build_stage.exists() and not build_stage.is_symlink():
                _safe_directory(build_stage)
                reject_nested_mounts((build_stage,))
                shutil.rmtree(build_stage)
                _fsync_directory(TRANSACTION_ROOT)
            raise
    digest = _immutable_release_digest(installer)
    with contextlib.suppress(FileNotFoundError):
        bundle.unlink()
        _fsync_directory(TRANSACTION_ROOT)
    return installer, digest


def _require_stage_space(members: Sequence[tarfile.TarInfo]) -> None:
    bytes_by_device: dict[int, int] = {}
    representative: dict[int, Path] = {}
    for root in EXPECTED_ROOTS:
        device = root.parent.stat().st_dev
        representative[device] = root.parent
        archive_root = root.relative_to("/")
        root_bytes = sum(
            member.size
            for member in members
            if member.isfile()
            and archive_root in Path(*PurePosixPath(member.name).parts).parents
        )
        bytes_by_device[device] = bytes_by_device.get(device, 0) + root_bytes
    for device, required in bytes_by_device.items():
        if shutil.disk_usage(representative[device]).free < required + SPACE_MARGIN:
            raise BackupRestoreError(
                f"restore filesystem lacks expanded data plus safety margin: {representative[device]}"
            )


def _ensure_transaction_root() -> None:
    if TRANSACTION_ROOT.is_symlink() or (
        TRANSACTION_ROOT.exists() and not TRANSACTION_ROOT.is_dir()
    ):
        raise BackupRestoreError(f"unsafe restore transaction directory: {TRANSACTION_ROOT}")
    TRANSACTION_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(TRANSACTION_ROOT, 0, 0)
    os.chmod(TRANSACTION_ROOT, 0o700)
    _safe_directory(TRANSACTION_ROOT, mode=0o700)


def _cleanup_orphan_transaction_artifacts() -> None:
    removed = False
    for candidate in TRANSACTION_ROOT.iterdir():
        if ORPHAN_TRANSACTION_FILE_RE.fullmatch(candidate.name):
            metadata = _safe_regular(candidate, mode=0o600)
            if metadata.st_nlink != 1:
                raise BackupRestoreError(
                    f"orphan restore artifact has unsafe link count: {candidate}"
                )
            candidate.unlink()
            removed = True
            continue
        installer_match = ORPHAN_INSTALLER_RE.fullmatch(candidate.name)
        stage_match = ORPHAN_INSTALLER_STAGE_RE.fullmatch(candidate.name)
        if installer_match is None and stage_match is None:
            continue
        _safe_directory(candidate)
        reject_nested_mounts((candidate,))
        if installer_match is not None:
            marker = candidate / ".uconn-installer-snapshot"
            _safe_regular(marker, mode=0o400)
            try:
                payload = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise BackupRestoreError(
                    f"orphan installer marker is invalid: {candidate}"
                ) from exc
            if (
                not isinstance(payload, dict)
                or set(payload) != {"commit", "transaction_id"}
                or not SHA_RE.fullmatch(str(payload["commit"]))
                or payload["transaction_id"] != installer_match.group(1)
            ):
                raise BackupRestoreError(
                    f"orphan installer marker does not match its path: {candidate}"
                )
        shutil.rmtree(candidate)
        removed = True
    if removed:
        _fsync_directory(TRANSACTION_ROOT)


def _git_as_owner(
    checkout: Path,
    owner: pwd.struct_passwd,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    environment = {
        "HOME": owner.pw_dir,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "PATH": "/usr/bin:/bin",
    }
    return subprocess.run(
        [
            "/usr/bin/sudo",
            "-H",
            "-u",
            owner.pw_name,
            "env",
            *(f"{key}={value}" for key, value in environment.items()),
            "/usr/bin/git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "protocol.file.allow=never",
            "-c",
            "credential.helper=",
            "-C",
            str(checkout),
            *arguments,
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def _clean_root_git(*arguments: str) -> subprocess.CompletedProcess[str]:
    return _run(
        (
            "/usr/bin/env",
            "-i",
            "HOME=/root",
            "PATH=/usr/bin:/bin",
            "GIT_CONFIG_NOSYSTEM=1",
            "GIT_CONFIG_GLOBAL=/dev/null",
            "GIT_TERMINAL_PROMPT=0",
            "GIT_NO_REPLACE_OBJECTS=1",
            GIT,
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "credential.helper=",
            *arguments,
        )
    )


def _create_source_bundle(
    checkout: Path,
    owner: pwd.struct_passwd,
    commit: str,
) -> tuple[Path, str]:
    _ensure_transaction_root()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".source-bundle.",
        suffix=".partial",
        dir=TRANSACTION_ROOT,
    )
    temporary = Path(temporary_name)
    environment = {
        "HOME": owner.pw_dir,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "PATH": "/usr/bin:/bin",
    }
    try:
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, 0, 0)
        result = subprocess.run(
            [
                "/usr/bin/sudo",
                "-H",
                "-u",
                owner.pw_name,
                "env",
                *(f"{key}={value}" for key, value in environment.items()),
                GIT,
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "credential.helper=",
                "-C",
                str(checkout),
                "bundle",
                "create",
                "-",
                "HEAD",
            ],
            stdout=descriptor,
            stderr=subprocess.PIPE,
            text=False,
            check=False,
        )
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        if result.returncode != 0:
            raise BackupRestoreError("could not create the exact-commit source bundle")
        _clean_root_git("bundle", "verify", str(temporary))
        heads = _clean_root_git("bundle", "list-heads", str(temporary)).stdout.splitlines()
        if heads != [f"{commit} HEAD"]:
            raise BackupRestoreError("source checkout moved while its bundle was created")
        digest = _sha256_file(temporary)
        return temporary, digest
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _validate_checkout(
    checkout: Path,
    commit: str,
    expected_remote: str,
) -> tuple[Path, str]:
    if not SHA_RE.fullmatch(commit):
        raise BackupRestoreError("restore commit must be a full lowercase 40-hex SHA")
    resolved = checkout.resolve(strict=True)
    metadata = resolved.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise BackupRestoreError("professor checkout is not a real directory")
    owner = pwd.getpwuid(metadata.st_uid)
    if metadata.st_uid < 1000 or owner.pw_name == "root":
        raise BackupRestoreError("professor checkout must belong to a normal non-root account")
    git_dir = resolved / ".git"
    if git_dir.is_symlink() or not git_dir.exists():
        raise BackupRestoreError("professor checkout is not a safe Git worktree")
    result = _git_as_owner(resolved, owner, "rev-parse", "HEAD")
    if result.returncode != 0 or result.stdout.strip() != commit:
        raise BackupRestoreError("professor checkout is not at the reviewed backup commit")
    remote = _git_as_owner(resolved, owner, "remote", "get-url", "origin")
    if remote.returncode != 0 or remote.stdout.strip() != expected_remote:
        raise BackupRestoreError("professor checkout origin does not match the backup remote")
    for arguments in (
        ("diff", "--quiet", "--", "deploy/deploy.sh", "deploy/backup_restore.py"),
        ("diff", "--cached", "--quiet", "--", "deploy/deploy.sh", "deploy/backup_restore.py"),
    ):
        if _git_as_owner(resolved, owner, *arguments).returncode != 0:
            raise BackupRestoreError("deployment helpers differ from the reviewed commit")
    untracked = _git_as_owner(
        resolved,
        owner,
        "ls-files",
        "--others",
        "--exclude-standard",
        "--",
        "deploy/deploy.sh",
        "deploy/backup_restore.py",
    )
    if untracked.returncode != 0 or untracked.stdout.strip():
        raise BackupRestoreError("untracked deployment helpers are not allowed")
    return resolved, owner.pw_name


def _new_state(
    *,
    transaction_id: str,
    staged_archive: Path,
    digest: str,
    archive_name: str,
    commit: str,
    checkout: Path,
    checkout_owner: str,
    installer_root: Path,
    installer_digest: str,
    control_config: DeployConfig,
) -> dict[str, Any]:
    roots: dict[str, Any] = {}
    for root in EXPECTED_ROOTS:
        stage = root.parent / f".{root.name}.restore-stage-{transaction_id}"
        backup_path = root.parent / f".{root.name}.pre-restore-{transaction_id}"
        roots[str(root)] = {
            "original": root.exists() or root.is_symlink(),
            "stage": str(stage),
            "backup": str(backup_path),
            "status": "pending",
            "rollback": "pending",
        }
    return {
        "schema": STATE_SCHEMA,
        "phase": "quiescing",
        "transaction_id": transaction_id,
        "archive": str(staged_archive),
        "archive_name": archive_name,
        "archive_sha256": digest,
        "commit": commit,
        "checkout": str(checkout),
        "checkout_owner": checkout_owner,
        "installer_root": str(installer_root),
        "installer_digest": installer_digest,
        "control_config": {
            "BACKEND_PORT": str(control_config.backend_port),
            "UPDATE_REMOTE_URL": control_config.update_remote_url,
        },
        "roots": roots,
    }


def _validate_state(payload: dict[str, Any]) -> dict[str, Any]:
    required = {
        "schema",
        "phase",
        "transaction_id",
        "archive",
        "archive_name",
        "archive_sha256",
        "commit",
        "checkout",
        "checkout_owner",
        "installer_root",
        "installer_digest",
        "control_config",
        "roots",
    }
    if set(payload) != required or payload["schema"] != STATE_SCHEMA:
        raise BackupRestoreError("restore journal has an unexpected schema")
    if payload["phase"] not in {
        "quiescing",
        "extracting",
        "swapping",
        "ready",
        "installing",
        "completed",
        "rolling_back",
    }:
        raise BackupRestoreError("restore journal has an unsupported phase")
    if not SHA_RE.fullmatch(str(payload["commit"])) or not re.fullmatch(
        r"[0-9a-f]{64}", str(payload["archive_sha256"])
    ):
        raise BackupRestoreError("restore journal identifiers are invalid")
    if not re.fullmatch(
        r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{16}", str(payload["transaction_id"])
    ):
        raise BackupRestoreError("restore transaction id is invalid")
    expected_archive = TRANSACTION_ROOT / f"{payload['archive_sha256']}.tgz"
    if (
        payload["archive"] != str(expected_archive)
        or not isinstance(payload["archive_name"], str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}", payload["archive_name"])
        or not isinstance(payload["checkout"], str)
        or not Path(payload["checkout"]).is_absolute()
        or ".." in Path(payload["checkout"]).parts
        or not isinstance(payload["checkout_owner"], str)
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}", payload["checkout_owner"])
        or payload["installer_root"]
        != str(TRANSACTION_ROOT / f"installer-{payload['transaction_id']}")
        or not re.fullmatch(r"[0-9a-f]{64}", str(payload["installer_digest"]))
        or not isinstance(payload["control_config"], dict)
        or set(payload["control_config"]) != {"BACKEND_PORT", "UPDATE_REMOTE_URL"}
    ):
        raise BackupRestoreError("restore journal paths or identities are invalid")
    try:
        journal_config = DeployConfig(dict(payload["control_config"]))
        _ = journal_config.backend_port
        _ = journal_config.update_remote_url
    except (AttributeError, TypeError, ValueError):
        raise BackupRestoreError("restore journal control config is invalid") from None
    roots = payload["roots"]
    if not isinstance(roots, dict) or set(roots) != {str(root) for root in EXPECTED_ROOTS}:
        raise BackupRestoreError("restore journal roots are invalid")
    for root in EXPECTED_ROOTS:
        entry = roots[str(root)]
        expected_stage = root.parent / f".{root.name}.restore-stage-{payload['transaction_id']}"
        expected_backup = root.parent / f".{root.name}.pre-restore-{payload['transaction_id']}"
        if (
            not isinstance(entry, dict)
            or set(entry) != {"original", "stage", "backup", "status", "rollback"}
            or not isinstance(entry["original"], bool)
            or entry["stage"] != str(expected_stage)
            or entry["backup"] != str(expected_backup)
            or entry["status"] not in {"pending", "staged", "backed_up", "restored"}
            or entry["rollback"] not in {"pending", "restored"}
        ):
            raise BackupRestoreError(f"restore journal entry is invalid for {root}")
    return payload


def _state_config(state: dict[str, Any]) -> DeployConfig:
    return DeployConfig(dict(state["control_config"]))


def _read_state() -> dict[str, Any]:
    return _validate_state(_read_json_nofollow(TRANSACTION_STATE))


def _write_state(state: dict[str, Any]) -> None:
    _validate_state(state)
    _atomic_json(TRANSACTION_STATE, state)


def _validate_installer_snapshot(
    state: dict[str, Any],
    config: DeployConfig,
) -> Path:
    installer = Path(state["installer_root"])
    _safe_directory(installer)
    _safe_directory(installer / ".git")
    marker = installer / ".uconn-installer-snapshot"
    _safe_regular(marker, mode=0o400)
    try:
        marker_payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BackupRestoreError("installer snapshot marker is invalid") from exc
    if marker_payload != {
        "commit": state["commit"],
        "transaction_id": state["transaction_id"],
    }:
        raise BackupRestoreError("installer snapshot marker does not match the journal")
    for relative in (
        "deploy/deploy.sh",
        "deploy/backup_restore.py",
        "deploy/runtime_env.py",
    ):
        metadata = _safe_regular(installer / relative)
        if relative.endswith(".sh") and not stat.S_IMODE(metadata.st_mode) & 0o111:
            raise BackupRestoreError("installer deployment script is not executable")
    if _immutable_release_digest(installer) != state["installer_digest"]:
        raise BackupRestoreError("root-owned installer snapshot digest changed")
    head = _clean_root_git("-C", str(installer), "rev-parse", "HEAD").stdout.strip()
    remote = _clean_root_git(
        "-C",
        str(installer),
        "remote",
        "get-url",
        "origin",
    ).stdout.strip()
    if head != state["commit"] or remote != config.update_remote_url:
        raise BackupRestoreError("installer snapshot Git identity changed")
    return installer


def _stage_marker(stage: Path, transaction_id: str) -> Path:
    return stage / MARKER_NAME


def _pre_restore_marker(root: Path) -> Path:
    return root / PRE_RESTORE_MARKER


def _ensure_pre_restore_marker(root: Path, transaction_id: str) -> None:
    marker = _pre_restore_marker(root)
    if marker.exists() or marker.is_symlink():
        _safe_regular(marker, mode=0o400)
        if marker.read_text(encoding="ascii").strip() != transaction_id:
            raise BackupRestoreError(f"pre-restore marker does not match: {root}")
        return
    descriptor = os.open(
        marker,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o400,
    )
    try:
        os.write(descriptor, f"{transaction_id}\n".encode("ascii"))
        os.fchmod(descriptor, 0o400)
        os.fchown(descriptor, 0, 0)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(root)


def _has_pre_restore_marker(root: Path, transaction_id: str) -> bool:
    marker = _pre_restore_marker(root)
    if not marker.exists() and not marker.is_symlink():
        return False
    _safe_regular(marker, mode=0o400)
    if marker.read_text(encoding="ascii").strip() != transaction_id:
        raise BackupRestoreError(f"pre-restore marker does not match: {root}")
    return True


def _quarantine(path: Path, transaction_id: str, label: str) -> Path:
    if not (path.exists() or path.is_symlink()):
        raise BackupRestoreError(f"cannot quarantine missing path: {path}")
    for index in range(1000):
        suffix = "" if index == 0 else f"-{index}"
        destination = path.parent / f".{path.name}.{label}-{transaction_id}{suffix}"
        if not destination.exists() and not destination.is_symlink():
            os.replace(path, destination)
            _fsync_directory(path.parent)
            return destination
    raise BackupRestoreError(f"could not allocate a quarantine path for {path}")


def _prepare_stage(stage: Path, transaction_id: str) -> None:
    if stage.exists() or stage.is_symlink():
        marker = _stage_marker(stage, transaction_id)
        if (
            not stage.is_symlink()
            and stage.is_dir()
            and marker.is_file()
            and not marker.is_symlink()
            and marker.read_text(encoding="ascii").strip() == transaction_id
        ):
            _quarantine(stage, transaction_id, "partial-stage")
        else:
            raise BackupRestoreError(f"unrecognized restore staging path: {stage}")
    stage.mkdir(mode=0o700)
    os.chown(stage, 0, 0)
    marker = _stage_marker(stage, transaction_id)
    descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(descriptor, f"{transaction_id}\n".encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(stage)
    _fsync_directory(stage.parent)


def _safe_extract_member(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    destination_root: Path,
    archive_root: Path,
) -> None:
    relative = Path(*PurePosixPath(member.name).parts).relative_to(archive_root)
    destination = destination_root / relative
    if member.isdir():
        destination.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chown(destination, 0, 0)
        os.chmod(destination, 0o700)
        return
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    for parent in (destination.parent, *destination.parents):
        if parent == destination_root.parent:
            break
        if parent.is_symlink():
            raise BackupRestoreError(f"symlink appeared in restore stage: {parent}")
    source = archive.extractfile(member)
    if source is None:
        raise BackupRestoreError(f"could not read archive member: {member.name}")
    descriptor = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            shutil.copyfileobj(source, handle, CHUNK_SIZE)
            handle.flush()
            os.fsync(handle.fileno())
        os.fchown(descriptor, 0, 0)
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def _extract_stages(state: dict[str, Any]) -> None:
    archive_path = Path(state["archive"])
    _safe_regular(archive_path, mode=0o600)
    if _sha256_file(archive_path) != state["archive_sha256"]:
        raise BackupRestoreError("durable staged archive digest changed")
    _, members = validate_archive(archive_path, expected_commit=state["commit"])
    for root in EXPECTED_ROOTS:
        entry = state["roots"][str(root)]
        if entry["status"] in {"staged", "backed_up", "restored"}:
            continue
        stage = Path(entry["stage"])
        _prepare_stage(stage, state["transaction_id"])
        archive_root = root.relative_to("/")
        with tarfile.open(archive_path, mode="r:gz") as archive:
            member_names = {
                member.name
                for member in members
                if Path(*PurePosixPath(member.name).parts) == archive_root
                or archive_root in Path(*PurePosixPath(member.name).parts).parents
            }
            for member in archive:
                if member.name in member_names:
                    member_path = Path(*PurePosixPath(member.name).parts)
                    if member_path == archive_root:
                        continue
                    _safe_extract_member(archive, member, stage, archive_root)
        _normalize_restored_tree(stage, root)
        _fsync_tree(stage)
        _fsync_directory(stage.parent)
        entry["status"] = "staged"
        _write_state(state)
    state["phase"] = "swapping"
    _write_state(state)


def _normalize_restored_tree(stage: Path, final_root: Path) -> None:
    for directory, names, files in os.walk(stage, topdown=True, followlinks=False):
        directory_path = Path(directory)
        metadata = directory_path.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise BackupRestoreError(f"unsafe restored directory: {directory_path}")
        os.chown(directory_path, 0, 0)
        os.chmod(directory_path, 0o700)
        for name in names:
            child = directory_path / name
            if child.is_symlink():
                raise BackupRestoreError(f"restored archive contains a symlink: {child}")
        for name in files:
            child = directory_path / name
            metadata = child.lstat()
            if not stat.S_ISREG(metadata.st_mode):
                raise BackupRestoreError(f"restored archive contains a special file: {child}")
            os.chown(child, 0, 0)
            os.chmod(child, 0o600)
    os.chmod(stage, 0o755)
    if final_root == CONFIG_ROOT:
        os.chmod(stage / "deploy.conf", 0o644)
        os.chmod(stage / "environment", 0o600)
        read_deploy_config(stage / "deploy.conf")
        validate_runtime_environment(stage / "environment")
    if final_root == PERSIST_ROOT:
        with contextlib.suppress(FileNotFoundError):
            (stage / ".ownership-uconn-v1").unlink()
    if final_root == DEPLOY_STATE_ROOT:
        for blocker_name in ("pending_activation.json", "integrity_repair.json"):
            blocker = stage / blocker_name
            if blocker.exists() or blocker.is_symlink():
                raise BackupRestoreError(
                    f"backup contains unresolved deployment transaction: {blocker_name}"
                )


def _reconcile_root(state: dict[str, Any], root: Path) -> None:
    entry = state["roots"][str(root)]
    stage = Path(entry["stage"])
    backup_path = Path(entry["backup"])
    root_exists = root.exists() or root.is_symlink()
    stage_exists = stage.exists() or stage.is_symlink()
    backup_exists = backup_path.exists() or backup_path.is_symlink()

    if backup_exists:
        _safe_directory(backup_path)
        _ensure_pre_restore_marker(backup_path, state["transaction_id"])
        if entry["status"] in {"pending", "staged"}:
            entry["status"] = "backed_up"
            _write_state(state)
    elif entry["original"]:
        if not root_exists:
            raise BackupRestoreError(f"original and rollback copy are both missing: {root}")
        if entry["status"] in {"backed_up", "restored"}:
            raise BackupRestoreError(f"rollback copy disappeared for {root}")
    elif entry["status"] == "backed_up":
        entry["status"] = "staged"
        _write_state(state)

    if entry["status"] == "staged":
        marker = _stage_marker(stage, state["transaction_id"])
        if marker.exists() or marker.is_symlink():
            _safe_regular(marker, mode=0o600)
            if marker.read_text(encoding="ascii").strip() != state["transaction_id"]:
                raise BackupRestoreError(f"restore stage marker does not match: {stage}")
            marker.unlink()
            _fsync_directory(stage)
        if entry["original"]:
            if root.is_symlink() or not root.is_dir():
                raise BackupRestoreError(f"managed root is unsafe before swap: {root}")
            if backup_exists:
                entry["status"] = "backed_up"
                _write_state(state)
            else:
                if _pre_restore_marker(root).exists() or _pre_restore_marker(root).is_symlink():
                    raise BackupRestoreError(
                        f"managed root already contains a restore marker: {root}"
                    )
                os.replace(root, backup_path)
                _fsync_directory(root.parent)
                _ensure_pre_restore_marker(backup_path, state["transaction_id"])
                entry["status"] = "backed_up"
                _write_state(state)
        else:
            entry["status"] = "backed_up"
            _write_state(state)

    root_exists = root.exists() or root.is_symlink()
    stage_exists = stage.exists() or stage.is_symlink()
    if entry["status"] == "backed_up":
        if root_exists and not stage_exists:
            _safe_directory(root)
            entry["status"] = "restored"
            _write_state(state)
            return
        if root_exists:
            raise BackupRestoreError(f"unexpected target appeared during restore: {root}")
        _safe_directory(stage)
        os.replace(stage, root)
        _fsync_directory(root.parent)
        entry["status"] = "restored"
        _write_state(state)
    elif entry["status"] == "restored":
        _safe_directory(root)
        if stage_exists:
            raise BackupRestoreError(f"restore stage remained after completed swap: {stage}")


def _swap_stages(state: dict[str, Any]) -> None:
    for root in EXPECTED_ROOTS:
        _reconcile_root(state, root)
    for root in EXPECTED_ROOTS:
        _fsync_tree(root)
        _fsync_directory(root.parent)
    state["phase"] = "ready"
    _write_state(state)


def restore(archive: Path, checksum: Path, checkout: Path, commit: str) -> dict[str, Any]:
    config = read_deploy_config()
    with deployment_lock():
        if TRANSACTION_STATE.exists() or TRANSACTION_STATE.is_symlink():
            raise BackupRestoreError("a restore transaction already exists; use recover or rollback")
        for blocker, label in (
            (ACTIVATION_STATE, "activation"),
            (INTEGRITY_STATE, "integrity-repair"),
            (MAINTENANCE_FILE, "maintenance"),
        ):
            if blocker.exists() or blocker.is_symlink():
                raise BackupRestoreError(f"a {label} transaction or gate is pending")
        _ensure_transaction_root()
        _cleanup_orphan_transaction_artifacts()
        reject_nested_mounts(EXPECTED_ROOTS)
        staged, digest = _copy_archive_once(archive, checksum)
        try:
            manifest, members = validate_archive(staged, expected_commit=commit)
            _require_stage_space(members)
            resolved_checkout, owner = _validate_checkout(
                checkout,
                commit,
                config.update_remote_url,
            )
            if manifest["update_remote_url"] != config.update_remote_url:
                raise BackupRestoreError(
                    "backup remote does not match this device's reviewed update remote"
                )
            transaction_id = _transaction_id(manifest, digest)
            installer_root, installer_digest = _materialize_installer(
                staged,
                manifest,
                digest,
                transaction_id,
            )
        except Exception:
            staged.unlink(missing_ok=True)
            _fsync_directory(TRANSACTION_ROOT)
            raise
        state = _new_state(
            transaction_id=transaction_id,
            staged_archive=staged,
            digest=digest,
            archive_name=archive.name,
            commit=commit,
            checkout=resolved_checkout,
            checkout_owner=owner,
            installer_root=installer_root,
            installer_digest=installer_digest,
            control_config=config,
        )
        _validate_installer_snapshot(state, config)
        _write_state(state)
        _ensure_restore_maintenance()
        with activity_lock_exclusive():
            _quiesce_for_restore(config)
            state["phase"] = "extracting"
            _write_state(state)
            _extract_stages(state)
            _swap_stages(state)
        return state


def recover() -> dict[str, Any]:
    with deployment_lock():
        state = _read_state()
        config = _state_config(state)
        reject_nested_mounts(EXPECTED_ROOTS)
        if state["phase"] not in {"completed", "rolling_back"}:
            _validate_installer_snapshot(state, config)
        _ensure_restore_maintenance()
        with activity_lock_exclusive():
            if state["phase"] == "completed":
                if _unit_snapshot(f"{SERVICE}.service", required=True)["active"] != "active":
                    _start_unit(f"{SERVICE}.service")
                _finalize_completed_state(state, config)
                return state
            _quiesce_for_restore(config)
            if state["phase"] == "rolling_back":
                _rollback_state(state)
                return state
            if state["phase"] == "quiescing":
                state["phase"] = "extracting"
                _write_state(state)
            if state["phase"] == "extracting":
                _extract_stages(state)
            if state["phase"] == "swapping":
                _swap_stages(state)
        return state


def _remove_marker_bound_tree(path: Path, marker: Path, expected: str) -> None:
    if not path.exists() and not path.is_symlink():
        return
    _safe_directory(path)
    _safe_regular(marker)
    if marker.read_text(encoding="utf-8").strip() != expected:
        raise BackupRestoreError(f"transaction tree marker does not match: {path}")
    reject_nested_mounts((path,))
    shutil.rmtree(path)
    _fsync_directory(path.parent)


def _remove_stage_if_present(state: dict[str, Any], root: Path) -> None:
    stage = Path(state["roots"][str(root)]["stage"])
    _remove_marker_bound_tree(
        stage,
        _stage_marker(stage, state["transaction_id"]),
        state["transaction_id"],
    )


def _remove_partial_stage_quarantines(state: dict[str, Any], root: Path) -> None:
    prefix = f".{root.name}.partial-stage-{state['transaction_id']}"
    pattern = re.compile(rf"{re.escape(prefix)}(?:-[1-9][0-9]*)?")
    for candidate in root.parent.iterdir():
        if pattern.fullmatch(candidate.name) is None:
            continue
        _remove_marker_bound_tree(
            candidate,
            _stage_marker(candidate, state["transaction_id"]),
            state["transaction_id"],
        )


def _remove_installer_if_present(state: dict[str, Any]) -> None:
    installer = Path(state["installer_root"])
    marker = installer / ".uconn-installer-snapshot"
    expected = json.dumps(
        {
            "commit": state["commit"],
            "transaction_id": state["transaction_id"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    _remove_marker_bound_tree(installer, marker, expected)


def _remove_staged_archive_if_present(state: dict[str, Any]) -> None:
    archive = Path(state["archive"])
    if not archive.exists() and not archive.is_symlink():
        return
    _safe_regular(archive, mode=0o600)
    if _sha256_file(archive) != state["archive_sha256"]:
        raise BackupRestoreError("refusing to remove a changed staged archive")
    archive.unlink()
    _fsync_directory(TRANSACTION_ROOT)


def _rollback_state(state: dict[str, Any]) -> Path:
    if state["phase"] != "rolling_back":
        state["phase"] = "rolling_back"
        _write_state(state)
    for root in reversed(EXPECTED_ROOTS):
        entry = state["roots"][str(root)]
        backup_path = Path(entry["backup"])
        if entry["rollback"] == "restored":
            _remove_stage_if_present(state, root)
            continue
        backup_exists = backup_path.exists() or backup_path.is_symlink()
        root_exists = root.exists() or root.is_symlink()
        if backup_exists:
            _safe_directory(backup_path)
            _ensure_pre_restore_marker(backup_path, state["transaction_id"])
            if root_exists:
                _quarantine(root, state["transaction_id"], "failed-restore")
            os.replace(backup_path, root)
            _fsync_directory(root.parent)
            _has_pre_restore_marker(root, state["transaction_id"])
        elif entry["original"]:
            _safe_directory(root)
            if entry["status"] in {"backed_up", "restored"} and not _has_pre_restore_marker(
                root,
                state["transaction_id"],
            ):
                raise BackupRestoreError(f"rollback copy is missing for {root}")
        elif root_exists:
            _quarantine(root, state["transaction_id"], "failed-restore")
        entry["rollback"] = "restored"
        _write_state(state)
        _remove_stage_if_present(state, root)

    for root in EXPECTED_ROOTS:
        entry = state["roots"][str(root)]
        if entry["original"]:
            marker = _pre_restore_marker(root)
            if marker.exists() or marker.is_symlink():
                _safe_regular(marker, mode=0o400)
                if marker.read_text(encoding="ascii").strip() != state["transaction_id"]:
                    raise BackupRestoreError(f"rollback marker does not match for {root}")
                marker.unlink()
                _fsync_directory(root)
        _remove_partial_stage_quarantines(state, root)

    history = TRANSACTION_ROOT / f"rolled-back-{state['transaction_id']}.json"
    _atomic_json(history, state)
    _remove_marker(RESTORE_ALLOW_FILE)
    _remove_staged_archive_if_present(state)
    _remove_installer_if_present(state)
    _remove_marker(TRANSACTION_STATE)
    return history


def rollback_restore() -> Path:
    with deployment_lock():
        state = _read_state()
        config = _state_config(state)
        reject_nested_mounts(EXPECTED_ROOTS)
        _ensure_restore_maintenance()
        with activity_lock_exclusive():
            _quiesce_for_restore(config)
            return _rollback_state(state)


def _wait_for_exact_health(config: DeployConfig, commit: str) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            health = _loopback_json(
                f"http://127.0.0.1:{config.backend_port}/api/health"
            )
            version = _loopback_json(
                f"http://127.0.0.1:{config.backend_port}/version.json"
            )
            if health.get("status") == "healthy" and version.get("commit") == commit:
                return
        except BackupRestoreError:
            pass
        time.sleep(1)
    raise BackupRestoreError("backend did not serve the exact restored commit within 60 seconds")


def _write_restore_allow() -> None:
    parent = _safe_directory(RESTORE_ALLOW_FILE.parent)
    if stat.S_IMODE(parent.st_mode) & 0o022:
        raise BackupRestoreError("restore-install authorization parent is unsafe")
    if RESTORE_ALLOW_FILE.is_symlink():
        raise BackupRestoreError("restore-install allow marker is a symlink")
    descriptor = os.open(
        RESTORE_ALLOW_FILE,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        os.fchown(descriptor, 0, 0)
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(RESTORE_ALLOW_FILE.parent)


def _rotate_access_token() -> str:
    _safe_regular(RUNTIME_ENV_HELPER, mode=0o755)
    token = secrets.token_urlsafe(32)
    _run(
        (
            RUNTIME_ENV_HELPER,
            "set",
            RUNTIME_ENV,
            "APP_ACCESS_TOKEN",
        ),
        input_text=token,
    )
    return token


def _restore_install_command(
    state: dict[str, Any],
    lock_descriptor: int,
    activity_lock_descriptor: int,
) -> list[str]:
    installer = Path(state["installer_root"])
    return [
        "/usr/bin/env",
        "-i",
        "HOME=/root",
        "USER=root",
        "LOGNAME=root",
        "PATH=/usr/sbin:/usr/bin:/sbin:/bin",
        "UCONN_RESTORE_ORCHESTRATED=1",
        f"UCONN_RESTORE_LOCK_FD={lock_descriptor}",
        f"UCONN_RESTORE_ACTIVITY_LOCK_FD={activity_lock_descriptor}",
        f"UCONN_RESTORE_INSTALLER_ROOT={installer}",
        "/bin/bash",
        str(installer / "deploy/deploy.sh"),
        "--restore-install-inner",
        state["commit"],
    ]


def _finalize_completed_state(
    state: dict[str, Any],
    config: DeployConfig,
) -> None:
    _wait_for_exact_health(config, state["commit"])
    history = TRANSACTION_ROOT / f"completed-{state['transaction_id']}.json"
    _atomic_json(history, state)
    _remove_marker(RESTORE_ALLOW_FILE)
    if MAINTENANCE_FILE.exists() or MAINTENANCE_FILE.is_symlink():
        _remove_maintenance()
    _remove_staged_archive_if_present(state)
    for root in EXPECTED_ROOTS:
        _remove_stage_if_present(state, root)
        _remove_partial_stage_quarantines(state, root)
    _remove_installer_if_present(state)
    # The journal is the last transaction artifact removed. Any earlier cleanup
    # failure therefore leaves a durable, retryable completed transaction.
    _remove_marker(TRANSACTION_STATE)
    # Refresh only after the durable restore journal is gone. The child
    # installer must never replace the executable that owns an active restore.
    _refresh_installed_helper_from_active_release()


def install_restored_release() -> None:
    with deployment_lock() as lock_descriptor:
        state = _read_state()
        config = _state_config(state)
        if state["phase"] not in {"ready", "installing", "completed"}:
            raise BackupRestoreError("restore is not ready for exact-commit installation")
        if state["phase"] != "completed":
            _validate_installer_snapshot(state, config)
            checkout, owner = _validate_checkout(
                Path(state["checkout"]),
                state["commit"],
                config.update_remote_url,
            )
            if owner != state["checkout_owner"] or str(checkout) != state["checkout"]:
                raise BackupRestoreError("restore checkout identity changed")
        if state["phase"] == "ready" and not sys.stdout.isatty():
            raise BackupRestoreError(
                "first restore install must run in an interactive terminal "
                "so the replacement browser token is not written to a log"
            )
        _ensure_restore_maintenance()
        with activity_lock_exclusive() as activity_descriptor:
            if state["phase"] == "completed":
                if _unit_snapshot(f"{SERVICE}.service", required=True)["active"] != "active":
                    _start_unit(f"{SERVICE}.service")
                _finalize_completed_state(state, config)
                print(
                    f"Restore completed and exact commit {state['commit']} "
                    "passed health verification."
                )
                print(
                    "After the physical acceptance drill, remove retained rollback data with: "
                    f"sudo {Path(__file__).resolve()} finalize "
                    f"--transaction-id {state['transaction_id']}"
                )
                return
            _quiesce_for_restore(config)
            if state["phase"] == "ready":
                token = _rotate_access_token()
                print("New browser access token (shown once):", flush=True)
                print(token, flush=True)
                state["phase"] = "installing"
                _write_state(state)
                _write_restore_allow()
            else:
                if not RESTORE_ALLOW_FILE.exists() and not RESTORE_ALLOW_FILE.is_symlink():
                    _write_restore_allow()
                else:
                    _safe_regular(RESTORE_ALLOW_FILE, mode=0o600)
                print("Retrying the exact-commit install; the already-rotated token is unchanged.")
            result = subprocess.run(
                _restore_install_command(
                    state,
                    lock_descriptor,
                    activity_descriptor,
                ),
                text=True,
                check=False,
                pass_fds=(lock_descriptor, activity_descriptor),
            )
            if result.returncode != 0:
                with contextlib.suppress(BackupRestoreError):
                    _disable_unit(f"{SERVICE}.service", required=True)
                for unit in _cloudflared_units():
                    with contextlib.suppress(BackupRestoreError):
                        _disable_unit(unit)
                _remove_marker(RESTORE_ALLOW_FILE)
                raise BackupRestoreError(
                    "exact-commit installer failed; restore journal and maintenance remain"
                )
            active, commit = verify_active_release()
            del active
            if commit != state["commit"]:
                raise BackupRestoreError("installer activated a different commit")
            _wait_for_exact_health(config, commit)
            _disable_unit(f"{SERVICE}-update.timer")
            for unit in _cloudflared_units():
                _disable_unit(unit)
            state["phase"] = "completed"
            _write_state(state)
            _finalize_completed_state(state, config)
            print(f"Restore completed and exact commit {commit} passed health verification.")
            print(
                "After the physical acceptance drill, remove retained rollback data with: "
                f"sudo {Path(__file__).resolve()} finalize "
                f"--transaction-id {state['transaction_id']}"
            )


def finalize_restore(transaction_id: str) -> Path:
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{16}", transaction_id):
        raise BackupRestoreError("finalize requires one exact restore transaction id")
    with deployment_lock():
        for blocker, label in (
            (TRANSACTION_STATE, "restore"),
            (ACTIVATION_STATE, "activation"),
            (INTEGRITY_STATE, "integrity-repair"),
            (MAINTENANCE_FILE, "maintenance"),
        ):
            if blocker.exists() or blocker.is_symlink():
                raise BackupRestoreError(f"cannot finalize while {label} state is pending")
        history = TRANSACTION_ROOT / f"completed-{transaction_id}.json"
        state = _validate_state(_read_json_nofollow(history))
        if state["transaction_id"] != transaction_id or state["phase"] != "completed":
            raise BackupRestoreError("completed restore history does not match")
        config = read_deploy_config()
        _, active_commit = verify_active_release()
        if active_commit != state["commit"]:
            raise BackupRestoreError("cannot finalize after switching away from restored commit")
        _wait_for_exact_health(config, active_commit)
        for root in EXPECTED_ROOTS:
            backup_path = Path(state["roots"][str(root)]["backup"])
            _remove_marker_bound_tree(
                backup_path,
                _pre_restore_marker(backup_path),
                transaction_id,
            )
            _remove_stage_if_present(state, root)
            _remove_partial_stage_quarantines(state, root)
        _remove_staged_archive_if_present(state)
        _remove_installer_if_present(state)
        finalized = TRANSACTION_ROOT / f"finalized-{transaction_id}.json"
        _atomic_json(
            finalized,
            {
                "schema": 1,
                "transaction_id": transaction_id,
                "commit": active_commit,
                "finalized_utc": datetime.now(UTC)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z"),
            },
        )
        return finalized


def status() -> dict[str, Any]:
    if not (TRANSACTION_STATE.exists() or TRANSACTION_STATE.is_symlink()):
        return {"pending": False}
    state = _read_state()
    if state["phase"] not in {"completed", "rolling_back"}:
        _validate_installer_snapshot(state, _state_config(state))
    return {
        "pending": True,
        "phase": state["phase"],
        "commit": state["commit"],
        "transaction_id": state["transaction_id"],
        "checkout": state["checkout"],
        "installer_root": state["installer_root"],
        "installer_digest": state["installer_digest"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Atomic backup and phase-journaled restore for the Pi deployment"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("--destination", type=Path, required=True)
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--archive", type=Path, required=True)
    restore_parser.add_argument("--checksum", type=Path, required=True)
    restore_parser.add_argument("--checkout", type=Path, required=True)
    restore_parser.add_argument("--commit", required=True)
    subparsers.add_parser("recover")
    subparsers.add_parser("rollback")
    subparsers.add_parser("install")
    finalize_parser = subparsers.add_parser("finalize")
    finalize_parser.add_argument("--transaction-id", required=True)
    subparsers.add_parser("status")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        _require_root()
        installed_self = Path(__file__).resolve(strict=True)
        _safe_regular(installed_self, mode=0o755)
        if Path(__file__).is_symlink():
            raise BackupRestoreError("refusing to run through a symlinked helper path")
        if arguments.command == "backup":
            archive, checksum = backup(arguments.destination)
            print(f"Backup created: {archive}")
            print(f"Checksum created: {checksum}")
        elif arguments.command == "restore":
            state = restore(
                arguments.archive,
                arguments.checksum,
                arguments.checkout,
                arguments.commit,
            )
            print(
                "Restore data swap is durable and ready. "
                f"Run `sudo {Path(__file__).resolve()} install` to rotate the browser token "
                f"and reinstall exact commit {state['commit']}."
            )
        elif arguments.command == "recover":
            state = recover()
            print(f"Restore recovery is now in phase: {state['phase']}")
        elif arguments.command == "rollback":
            history = rollback_restore()
            print(f"Pre-restore roots were recovered; journal retained at {history}")
        elif arguments.command == "install":
            install_restored_release()
        elif arguments.command == "finalize":
            finalized = finalize_restore(arguments.transaction_id)
            print(f"Retained pre-restore data was safely finalized: {finalized}")
        elif arguments.command == "status":
            print(json.dumps(status(), sort_keys=True))
        else:
            raise BackupRestoreError("unsupported command")
    except (BackupRestoreError, OSError, subprocess.SubprocessError) as exc:
        print(f"backup/restore refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
