"""Behavioral contracts for the Raspberry Pi backup/restore state machine."""

from __future__ import annotations

import contextlib
import fcntl
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HELPER_PATH = REPO_ROOT / "deploy" / "backup_restore.py"
DEPLOY_SCRIPT = REPO_ROOT / "deploy" / "deploy.sh"


def _load_helper() -> ModuleType:
    spec = importlib.util.spec_from_file_location("uconn_backup_restore", HELPER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def helper() -> ModuleType:
    return _load_helper()


def _state_for_roots(helper: ModuleType, roots: tuple[Path, ...]) -> dict[str, object]:
    transaction_id = "20260727T120000Z-0123456789abcdef"
    installer_root = helper.TRANSACTION_ROOT / f"installer-{transaction_id}"
    return {
        "schema": 1,
        "phase": "swapping",
        "transaction_id": transaction_id,
        "archive": str(helper.TRANSACTION_ROOT / f"{'a' * 64}.tgz"),
        "archive_name": "example.tgz",
        "archive_sha256": "a" * 64,
        "commit": "b" * 40,
        "checkout": "/home/professor/UConn-IsomorphicQuestionLLM",
        "checkout_owner": "professor",
        "installer_root": str(installer_root),
        "installer_digest": "c" * 64,
        "control_config": {
            "BACKEND_PORT": "3002",
            "UPDATE_REMOTE_URL": "https://github.com/example/project.git",
        },
        "roots": {
            str(root): {
                "original": True,
                "stage": str(root.parent / f".{root.name}.restore-stage-{transaction_id}"),
                "backup": str(root.parent / f".{root.name}.pre-restore-{transaction_id}"),
                "status": "staged",
                "rollback": "pending",
            }
            for root in roots
        },
    }


def _manifest(helper: ModuleType, commit: str = "b" * 40) -> dict[str, object]:
    bundle = b"test source bundle"
    return {
        "schema": 2,
        "created_utc": "2026-07-27T12:00:00Z",
        "active_commit": commit,
        "update_remote_url": "https://github.com/example/project.git",
        "roots": [root.as_posix() for root in helper.ARCHIVE_ROOTS],
        "restore_protocol": 1,
        "source_bundle": helper.SOURCE_BUNDLE_NAME,
        "source_bundle_sha256": __import__("hashlib").sha256(bundle).hexdigest(),
    }


def _write_archive(
    helper: ModuleType,
    path: Path,
    *,
    extra_member: tarfile.TarInfo | None = None,
) -> None:
    with tarfile.open(path, "w:gz") as archive:
        manifest = (json.dumps(_manifest(helper)) + "\n").encode()
        manifest_info = tarfile.TarInfo(helper.MANIFEST_NAME)
        manifest_info.mode = 0o600
        manifest_info.size = len(manifest)
        archive.addfile(manifest_info, fileobj=__import__("io").BytesIO(manifest))
        bundle = b"test source bundle"
        bundle_info = tarfile.TarInfo(helper.SOURCE_BUNDLE_NAME)
        bundle_info.mode = 0o600
        bundle_info.size = len(bundle)
        archive.addfile(bundle_info, fileobj=__import__("io").BytesIO(bundle))
        for root in helper.ARCHIVE_ROOTS:
            root_info = tarfile.TarInfo(root.as_posix())
            root_info.type = tarfile.DIRTYPE
            root_info.mode = 0o755
            archive.addfile(root_info)
            file_info = tarfile.TarInfo(f"{root.as_posix()}/proof")
            file_info.mode = 0o600
            file_info.size = 1
            archive.addfile(file_info, fileobj=__import__("io").BytesIO(b"x"))
        if extra_member is not None:
            archive.addfile(extra_member)
    path.chmod(0o600)


def test_helper_is_executable_and_standard_library_only() -> None:
    metadata = HELPER_PATH.stat()
    assert stat.S_IMODE(metadata.st_mode) == 0o755
    source = HELPER_PATH.read_text(encoding="utf-8")
    assert "fcntl.flock" in source
    assert "ProxyHandler({})" in source
    assert "os.O_NOFOLLOW" in source
    assert "os.replace" in source
    assert "tar.extractall" not in source
    assert "shell=True" not in source
    assert "sys.stdout.isatty()" in source


def test_nested_mounts_are_rejected_even_on_the_same_device(helper: ModuleType) -> None:
    root = Path("/var/lib/uconn-isomorphic")
    with pytest.raises(helper.BackupRestoreError, match="must not be mount points"):
        helper.reject_nested_mounts(
            (root,),
            mounts=(Path("/"), root / "uploads" / "usb"),
        )


def test_managed_root_itself_cannot_be_a_mount_point(helper: ModuleType) -> None:
    root = Path("/var/lib/uconn-isomorphic")
    with pytest.raises(helper.BackupRestoreError, match="must not be mount points"):
        helper.reject_nested_mounts((root,), mounts=(Path("/"), root))


def test_luks_device_ancestry_is_required_and_detected(
    helper: ModuleType,
    tmp_path: Path,
) -> None:
    sys_dev_block = tmp_path / "sys-dev-block"
    plain = sys_dev_block / "8:1"
    plain.mkdir(parents=True)
    assert not helper._device_uses_luks(
        os.makedev(8, 1),
        sys_dev_block=sys_dev_block,
    )

    logical = sys_dev_block / "253:2"
    encrypted_parent = sys_dev_block / "253:1"
    (logical / "slaves/encrypted").mkdir(parents=True)
    (logical / "slaves/encrypted/dev").write_text("253:1\n", encoding="ascii")
    (encrypted_parent / "dm").mkdir(parents=True)
    (encrypted_parent / "dm/uuid").write_text(
        "CRYPT-LUKS2-deadbeef-uconn\n",
        encoding="ascii",
    )
    assert helper._device_uses_luks(
        os.makedev(253, 2),
        sys_dev_block=sys_dev_block,
    )


def test_partial_publication_stays_anchored_after_destination_path_swap(
    helper: ModuleType,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "destination"
    moved = tmp_path / "moved"
    destination.mkdir()
    directory_fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
    try:
        partial_fd, partial_name = helper._create_partial_at(
            directory_fd,
            prefix=".generation.",
            suffix=".partial",
        )
        os.write(partial_fd, b"proof")
        os.fsync(partial_fd)
        os.close(partial_fd)

        destination.rename(moved)
        destination.mkdir()
        os.rename(
            partial_name,
            "generation.tgz",
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )

        assert (moved / "generation.tgz").read_bytes() == b"proof"
        assert not (destination / "generation.tgz").exists()
        assert not helper._destination_path_matches(directory_fd, destination)
    finally:
        os.close(directory_fd)


def test_next_backup_removes_only_safe_interrupted_secret_partials(
    helper: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    partial = (
        f".{helper.SERVICE}-20260727T120000Z-{'b' * 40}.tgz."
        f"{'c' * 24}.partial"
    )
    checksum_partial = (
        f".{helper.SERVICE}-20260727T120000Z-{'b' * 40}.tgz.sha256."
        f"{'d' * 24}.partial"
    )
    unrelated = ".someone-elses-file.partial"
    for name in (partial, checksum_partial, unrelated):
        (tmp_path / name).write_bytes(b"secret")
        (tmp_path / name).chmod(0o600)

    real_stat = os.stat

    def root_owned_stat(path, *args, **kwargs):
        metadata = real_stat(path, *args, **kwargs)
        fields = list(metadata)
        fields[4] = 0
        fields[5] = 0
        return os.stat_result(fields)

    monkeypatch.setattr(helper.os, "stat", root_owned_stat)
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        helper._cleanup_interrupted_backup_partials(descriptor)
    finally:
        os.close(descriptor)

    assert not (tmp_path / partial).exists()
    assert not (tmp_path / checksum_partial).exists()
    assert (tmp_path / unrelated).exists()


def test_restore_requires_archive_and_checksum_in_same_verified_directory(
    helper: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    other = tmp_path / "other"
    source.mkdir()
    other.mkdir()
    archive = source / "backup.tgz"
    checksum = other / "backup.tgz.sha256"
    archive.touch()
    checksum.touch()
    directory_fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY)
    monkeypatch.setattr(
        helper,
        "_safe_destination",
        lambda _path: (os.dup(directory_fd), source.resolve()),
    )
    try:
        with pytest.raises(helper.BackupRestoreError, match="share one verified LUKS"):
            helper._copy_archive_once(archive, checksum)
    finally:
        os.close(directory_fd)


def test_backup_premaintenance_failure_closes_destination_without_marker(
    helper: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "destination"
    destination.mkdir()
    destination_fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
    source_bundle = tmp_path / "source.bundle"
    source_bundle.write_bytes(b"bundle")
    maintenance_calls: list[str] = []
    monkeypatch.setattr(helper, "TRANSACTION_STATE", tmp_path / "restore.pending")
    monkeypatch.setattr(helper, "ACTIVATION_STATE", tmp_path / "activation.pending")
    monkeypatch.setattr(helper, "INTEGRITY_STATE", tmp_path / "integrity.pending")
    monkeypatch.setattr(helper, "MAINTENANCE_FILE", tmp_path / "maintenance")
    monkeypatch.setattr(helper, "_ensure_transaction_root", lambda: None)
    monkeypatch.setattr(helper, "_cleanup_orphan_transaction_artifacts", lambda: None)
    config = SimpleNamespace(
        values={"SOURCE_REPO_DIR": str(tmp_path)},
        update_remote_url="https://github.com/example/project.git",
    )
    monkeypatch.setattr(helper, "read_deploy_config", lambda: config)
    monkeypatch.setattr(helper, "deployment_lock", contextlib.nullcontext)
    monkeypatch.setattr(helper, "verify_active_release", lambda: (tmp_path, "b" * 40))
    monkeypatch.setattr(helper, "_assert_current_helper_matches_release", lambda _release: None)
    monkeypatch.setattr(helper, "validate_runtime_environment", lambda: {})
    monkeypatch.setattr(
        helper,
        "_validate_checkout",
        lambda *_args: (tmp_path, "professor"),
    )
    monkeypatch.setattr(
        helper,
        "_create_source_bundle",
        lambda *_args: (source_bundle, "d" * 64),
    )
    monkeypatch.setattr(
        helper.pwd,
        "getpwnam",
        lambda _name: SimpleNamespace(pw_name="professor"),
    )
    monkeypatch.setattr(helper, "reject_nested_mounts", lambda _roots: None)
    monkeypatch.setattr(
        helper,
        "_safe_destination",
        lambda _destination: (destination_fd, destination),
    )
    monkeypatch.setattr(helper, "_source_size", lambda _roots: 0)
    monkeypatch.setattr(helper, "SPACE_MARGIN", 0)
    monkeypatch.setattr(
        helper,
        "_unit_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            helper.BackupRestoreError("snapshot failed")
        ),
    )
    monkeypatch.setattr(
        helper,
        "_write_maintenance",
        lambda: maintenance_calls.append("maintenance"),
    )

    with pytest.raises(helper.BackupRestoreError, match="snapshot failed"):
        helper.backup(destination)

    with pytest.raises(OSError):
        os.fstat(destination_fd)
    assert maintenance_calls == []


def test_restore_journal_precedes_any_quiesce_mutation(
    helper: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staged = tmp_path / f"{'a' * 64}.tgz"
    staged.touch()
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    recorded: list[dict[str, object]] = []
    config = SimpleNamespace(
        backend_port=3002,
        update_remote_url="https://github.com/example/project.git",
    )
    monkeypatch.setattr(helper, "TRANSACTION_STATE", tmp_path / "restore.pending")
    monkeypatch.setattr(helper, "ACTIVATION_STATE", tmp_path / "activation.pending")
    monkeypatch.setattr(helper, "INTEGRITY_STATE", tmp_path / "integrity.pending")
    monkeypatch.setattr(helper, "MAINTENANCE_FILE", tmp_path / "maintenance")
    monkeypatch.setattr(helper, "_ensure_transaction_root", lambda: None)
    monkeypatch.setattr(helper, "_cleanup_orphan_transaction_artifacts", lambda: None)
    monkeypatch.setattr(helper, "read_deploy_config", lambda: config)
    monkeypatch.setattr(helper, "deployment_lock", contextlib.nullcontext)
    monkeypatch.setattr(helper, "reject_nested_mounts", lambda _roots: None)
    monkeypatch.setattr(
        helper,
        "_copy_archive_once",
        lambda _archive, _checksum: (staged, "a" * 64),
    )
    monkeypatch.setattr(
        helper,
        "validate_archive",
        lambda _path, **_kwargs: (
            {
                "update_remote_url": config.update_remote_url,
                "created_utc": "2026-07-27T12:00:00Z",
            },
            [],
        ),
    )
    monkeypatch.setattr(helper, "_require_stage_space", lambda _members: None)
    monkeypatch.setattr(
        helper,
        "_materialize_installer",
        lambda *_args: (tmp_path / "installer", "c" * 64),
    )
    monkeypatch.setattr(helper, "_validate_installer_snapshot", lambda *_args: None)
    monkeypatch.setattr(helper, "_ensure_restore_maintenance", lambda: None)
    monkeypatch.setattr(helper, "activity_lock_exclusive", contextlib.nullcontext)
    monkeypatch.setattr(
        helper,
        "_validate_checkout",
        lambda *_args: (checkout, "professor"),
    )
    monkeypatch.setattr(
        helper,
        "_write_state",
        lambda state: recorded.append(json.loads(json.dumps(state))),
    )
    monkeypatch.setattr(
        helper,
        "_quiesce_for_restore",
        lambda _config: (_ for _ in ()).throw(
            helper.BackupRestoreError("quiesce fault")
        ),
    )

    with pytest.raises(helper.BackupRestoreError, match="quiesce fault"):
        helper.restore(staged, tmp_path / "checksum", checkout, "b" * 40)

    assert recorded
    assert recorded[0]["phase"] == "quiescing"


@pytest.mark.parametrize("failure_index", range(6))
def test_each_restore_quiesce_failure_remains_recoverable_after_journal(
    helper: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    failure_index: int,
) -> None:
    events: list[str] = []
    config = SimpleNamespace(backend_port=3002)
    monkeypatch.setattr(
        helper,
        "_ensure_restore_maintenance",
        lambda: events.append("maintenance"),
    )
    monkeypatch.setattr(
        helper,
        "_cloudflared_units",
        lambda: ["first-cloud.service", "second-cloud.service"],
    )

    def fail_at_selected_unit(unit: str, *, required: bool = False) -> None:
        del required
        events.append(unit)
        if len(events) - 2 == failure_index:
            raise helper.BackupRestoreError(f"fault at {unit}")

    monkeypatch.setattr(helper, "_disable_unit", fail_at_selected_unit)
    monkeypatch.setattr(
        helper,
        "_assert_no_backend_writers",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(helper.BackupRestoreError, match="fault at"):
        helper._quiesce_for_restore(config)

    assert events[0] == "maintenance"
    assert len(events) == failure_index + 2


def test_archive_validator_rejects_link_and_escape_members(
    helper: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(helper, "_safe_regular", lambda *_args, **_kwargs: None)
    link = tarfile.TarInfo("etc/uconn-isomorphic/escape")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/shadow"
    link.mode = 0o600
    link_archive = tmp_path / "link.tgz"
    _write_archive(helper, link_archive, extra_member=link)
    with pytest.raises(helper.BackupRestoreError, match="unsupported archive member"):
        helper.validate_archive(link_archive, expected_commit="b" * 40)

    escape = tarfile.TarInfo("../outside")
    escape.type = tarfile.DIRTYPE
    escape.mode = 0o700
    escape_archive = tmp_path / "escape.tgz"
    _write_archive(helper, escape_archive, extra_member=escape)
    with pytest.raises(helper.BackupRestoreError, match="unsafe archive member"):
        helper.validate_archive(escape_archive, expected_commit="b" * 40)


def test_archive_validator_rejects_noncanonical_alias_member(
    helper: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(helper, "_safe_regular", lambda *_args, **_kwargs: None)
    alias = tarfile.TarInfo("./etc/uconn-isomorphic/proof")
    alias.mode = 0o600
    alias.size = 0
    archive = tmp_path / "alias.tgz"
    _write_archive(helper, archive, extra_member=alias)

    with pytest.raises(helper.BackupRestoreError, match="unsafe archive member"):
        helper.validate_archive(archive, expected_commit="b" * 40)


def test_archive_validator_requires_exact_manifest_commit(
    helper: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(helper, "_safe_regular", lambda *_args, **_kwargs: None)
    archive = tmp_path / "valid.tgz"
    _write_archive(helper, archive)
    manifest, members = helper.validate_archive(archive, expected_commit="b" * 40)
    assert manifest["active_commit"] == "b" * 40
    assert members
    with pytest.raises(helper.BackupRestoreError, match="reviewed checkout"):
        helper.validate_archive(archive, expected_commit="c" * 40)


def test_kill_after_original_rename_resumes_to_restored_root(
    helper: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "managed"
    roots = (root,)
    monkeypatch.setattr(helper, "EXPECTED_ROOTS", roots)
    monkeypatch.setattr(helper, "_write_state", lambda _state: None)
    monkeypatch.setattr(helper.os, "fchown", lambda *_args: None)
    monkeypatch.setattr(
        helper,
        "_safe_directory",
        lambda path, **_kwargs: Path(path).lstat(),
    )
    state = _state_for_roots(helper, roots)
    entry = state["roots"][str(root)]
    stage = Path(entry["stage"])
    backup = Path(entry["backup"])
    stage.mkdir()
    (stage / "new").write_text("new", encoding="utf-8")
    backup.mkdir()
    (backup / "old").write_text("old", encoding="utf-8")

    helper._reconcile_root(state, root)

    assert entry["status"] == "restored"
    assert (root / "new").read_text(encoding="utf-8") == "new"
    assert (backup / "old").read_text(encoding="utf-8") == "old"
    assert not stage.exists()


def test_kill_after_restored_rename_is_reconciled_without_second_move(
    helper: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "managed"
    roots = (root,)
    monkeypatch.setattr(helper, "EXPECTED_ROOTS", roots)
    monkeypatch.setattr(helper, "_write_state", lambda _state: None)
    monkeypatch.setattr(helper.os, "fchown", lambda *_args: None)
    monkeypatch.setattr(
        helper,
        "_safe_directory",
        lambda path, **_kwargs: Path(path).lstat(),
    )
    state = _state_for_roots(helper, roots)
    entry = state["roots"][str(root)]
    entry["status"] = "backed_up"
    root.mkdir()
    (root / "new").write_text("new", encoding="utf-8")
    backup = Path(entry["backup"])
    backup.mkdir()
    (backup / "old").write_text("old", encoding="utf-8")

    helper._reconcile_root(state, root)

    assert entry["status"] == "restored"
    assert (root / "new").read_text(encoding="utf-8") == "new"
    assert (backup / "old").read_text(encoding="utf-8") == "old"


def test_rollback_resumes_after_power_loss_between_replace_and_journal(
    helper: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "managed"
    roots = (root,)
    monkeypatch.setattr(helper, "EXPECTED_ROOTS", roots)
    monkeypatch.setattr(helper, "TRANSACTION_ROOT", tmp_path)
    monkeypatch.setattr(helper, "TRANSACTION_STATE", tmp_path / "restore.pending")
    monkeypatch.setattr(helper, "RESTORE_ALLOW_FILE", tmp_path / "restore-allowed")
    monkeypatch.setattr(helper, "_safe_directory", lambda path, **_kwargs: Path(path).lstat())
    monkeypatch.setattr(helper, "_safe_regular", lambda path, **_kwargs: Path(path).lstat())
    monkeypatch.setattr(helper.os, "fchown", lambda *_args: None)
    monkeypatch.setattr(helper, "_remove_stage_if_present", lambda *_args: None)
    monkeypatch.setattr(helper, "_remove_staged_archive_if_present", lambda *_args: None)
    monkeypatch.setattr(helper, "_remove_installer_if_present", lambda *_args: None)
    monkeypatch.setattr(helper, "_remove_marker", lambda *_args: None)
    monkeypatch.setattr(helper, "_atomic_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(helper, "_write_state", lambda _state: None)

    state = _state_for_roots(helper, roots)
    state["phase"] = "rolling_back"
    entry = state["roots"][str(root)]
    entry["status"] = "restored"
    root.mkdir()
    (root / "new").write_text("new", encoding="utf-8")
    backup = Path(entry["backup"])
    backup.mkdir()
    (backup / "old").write_text("old", encoding="utf-8")
    marker = helper._pre_restore_marker(backup)
    marker.write_text(f"{state['transaction_id']}\n", encoding="ascii")
    marker.chmod(0o400)

    real_replace = os.replace
    failed = False

    def replace_then_power_loss(source: Path, destination: Path) -> None:
        nonlocal failed
        real_replace(source, destination)
        if Path(source) == backup and Path(destination) == root and not failed:
            failed = True
            raise helper.BackupRestoreError("simulated power loss")

    monkeypatch.setattr(helper.os, "replace", replace_then_power_loss)
    with pytest.raises(helper.BackupRestoreError, match="simulated power loss"):
        helper._rollback_state(state)

    assert not backup.exists()
    assert entry["rollback"] == "pending"
    assert (root / "old").read_text(encoding="utf-8") == "old"

    monkeypatch.setattr(helper.os, "replace", real_replace)
    helper._rollback_state(state)

    assert entry["rollback"] == "restored"
    assert (root / "old").read_text(encoding="utf-8") == "old"
    assert not helper._pre_restore_marker(root).exists()
    quarantines = list(tmp_path.glob(".managed.failed-restore-*"))
    assert len(quarantines) == 1
    assert (quarantines[0] / "new").read_text(encoding="utf-8") == "new"


def test_completed_transaction_removes_marker_bound_partial_stages(
    helper: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "managed"
    roots = (root,)
    state = _state_for_roots(helper, roots)
    transaction_id = state["transaction_id"]
    partial = tmp_path / f".managed.partial-stage-{transaction_id}"
    partial.mkdir()
    marker = helper._stage_marker(partial, transaction_id)
    marker.write_text(f"{transaction_id}\n", encoding="ascii")
    marker.chmod(0o600)
    unrelated = tmp_path / f".managed.partial-stage-{transaction_id}-0"
    unrelated.mkdir()
    monkeypatch.setattr(helper, "_safe_directory", lambda path, **_kwargs: Path(path).lstat())
    monkeypatch.setattr(helper, "_safe_regular", lambda path, **_kwargs: Path(path).lstat())
    monkeypatch.setattr(helper, "reject_nested_mounts", lambda _roots: None)

    helper._remove_partial_stage_quarantines(state, root)

    assert not partial.exists()
    assert unrelated.exists()


def test_new_transaction_cleans_only_verified_orphan_artifacts(
    helper: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transaction_id = "20260727T120000Z-0123456789abcdef"
    orphan_file = tmp_path / ".source-bundle.abcdef.partial"
    orphan_archive = tmp_path / f"{'a' * 64}.tgz"
    installer_stage = tmp_path / f".installer-{transaction_id}.partial"
    installer = tmp_path / f"installer-{transaction_id}"
    retained_history = tmp_path / f"completed-{transaction_id}.json"
    for path in (orphan_file, orphan_archive):
        path.write_bytes(b"orphan")
        path.chmod(0o600)
    installer_stage.mkdir()
    installer.mkdir()
    marker = installer / ".uconn-installer-snapshot"
    marker.write_text(
        json.dumps({"commit": "b" * 40, "transaction_id": transaction_id}),
        encoding="utf-8",
    )
    marker.chmod(0o400)
    retained_history.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(helper, "TRANSACTION_ROOT", tmp_path)
    monkeypatch.setattr(helper, "_safe_directory", lambda path, **_kwargs: Path(path).lstat())
    monkeypatch.setattr(helper, "_safe_regular", lambda path, **_kwargs: Path(path).lstat())
    monkeypatch.setattr(helper, "reject_nested_mounts", lambda _roots: None)

    helper._cleanup_orphan_transaction_artifacts()

    assert not orphan_file.exists()
    assert not orphan_archive.exists()
    assert not installer_stage.exists()
    assert not installer.exists()
    assert retained_history.exists()


def test_installer_snapshot_is_published_only_after_complete_build(
    helper: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transaction_id = "20260727T120000Z-0123456789abcdef"
    bundle = tmp_path / "source.bundle"
    bundle.write_bytes(b"bundle")
    manifest = _manifest(helper)
    monkeypatch.setattr(helper, "TRANSACTION_ROOT", tmp_path)
    monkeypatch.setattr(helper, "_extract_source_bundle", lambda *_args: bundle)
    monkeypatch.setattr(
        helper,
        "_clean_root_git",
        lambda *_args: SimpleNamespace(stdout="", returncode=0),
    )
    monkeypatch.setattr(helper, "_harden_root_tree", lambda _path: None)
    monkeypatch.setattr(helper, "_fsync_tree", lambda _path: None)
    monkeypatch.setattr(helper, "_fsync_directory", lambda _path: None)
    monkeypatch.setattr(helper, "_immutable_release_digest", lambda _path: "d" * 64)
    monkeypatch.setattr(helper.os, "chown", lambda *_args: None)
    monkeypatch.setattr(helper.os, "fchown", lambda *_args: None)

    installer, digest = helper._materialize_installer(
        tmp_path / "archive.tgz",
        manifest,
        "a" * 64,
        transaction_id,
    )

    assert installer == tmp_path / f"installer-{transaction_id}"
    assert digest == "d" * 64
    assert installer.is_dir()
    assert (installer / ".uconn-installer-snapshot").is_file()
    assert not (tmp_path / f".installer-{transaction_id}.partial").exists()


def test_state_validator_rejects_forged_stage_path(helper: ModuleType) -> None:
    state = _state_for_roots(helper, helper.EXPECTED_ROOTS)
    first = str(helper.EXPECTED_ROOTS[0])
    state["roots"][first]["stage"] = "/tmp/attacker-controlled"
    with pytest.raises(helper.BackupRestoreError, match="journal entry"):
        helper._validate_state(state)


def test_loopback_health_ignores_hostile_proxy_environment(
    helper: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TargetHandler(BaseHTTPRequestHandler):
        hits = 0

        def do_GET(self) -> None:  # noqa: N802
            type(self).hits += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"healthy"}')

        def log_message(self, _format: str, *_args: object) -> None:
            return

    class ProxyHandler(BaseHTTPRequestHandler):
        hits = 0

        def do_GET(self) -> None:  # noqa: N802
            type(self).hits += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"forged"}')

        def log_message(self, _format: str, *_args: object) -> None:
            return

    target = ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
    threads = [
        threading.Thread(target=server.serve_forever, daemon=True)
        for server in (target, proxy)
    ]
    for thread in threads:
        thread.start()
    try:
        for key in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
            monkeypatch.setenv(key, f"http://127.0.0.1:{proxy.server_port}")
        for key in ("NO_PROXY", "no_proxy"):
            monkeypatch.setenv(key, "")
        payload = helper._loopback_json(
            f"http://127.0.0.1:{target.server_port}/api/health"
        )
    finally:
        for server in (target, proxy):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)

    assert payload == {"status": "healthy"}
    assert TargetHandler.hits == 1
    assert ProxyHandler.hits == 0


def test_non_root_cli_refuses_without_touching_device(helper: ModuleType) -> None:
    if os.geteuid() == 0:
        pytest.skip("test is meaningful only under the normal non-root test account")
    assert helper.main(["status"]) == 1


def test_restore_install_command_uses_clean_root_environment_and_inherited_lock(
    helper: ModuleType,
) -> None:
    state = _state_for_roots(helper, helper.EXPECTED_ROOTS)
    command = helper._restore_install_command(state, 19, 23)

    assert command[:3] == ["/usr/bin/env", "-i", "HOME=/root"]
    assert "PATH=/usr/sbin:/usr/bin:/sbin:/bin" in command
    assert "UCONN_RESTORE_ORCHESTRATED=1" in command
    assert "UCONN_RESTORE_LOCK_FD=19" in command
    assert "UCONN_RESTORE_ACTIVITY_LOCK_FD=23" in command
    assert f"UCONN_RESTORE_INSTALLER_ROOT={state['installer_root']}" in command
    assert command[-2:] == ["--restore-install-inner", "b" * 40]
    assert "/usr/bin/sudo" not in command


def test_recover_completed_transaction_does_not_require_removed_installer(
    helper: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state_for_roots(helper, helper.EXPECTED_ROOTS)
    state["phase"] = "completed"
    events: list[str] = []
    monkeypatch.setattr(helper, "deployment_lock", contextlib.nullcontext)
    monkeypatch.setattr(helper, "activity_lock_exclusive", contextlib.nullcontext)
    monkeypatch.setattr(helper, "_read_state", lambda: state)
    monkeypatch.setattr(helper, "reject_nested_mounts", lambda _roots: None)
    monkeypatch.setattr(helper, "_ensure_restore_maintenance", lambda: events.append("maintenance"))
    monkeypatch.setattr(
        helper,
        "_validate_installer_snapshot",
        lambda *_args: (_ for _ in ()).throw(AssertionError("installer must not be required")),
    )
    monkeypatch.setattr(
        helper,
        "_quiesce_for_restore",
        lambda *_args: (_ for _ in ()).throw(AssertionError("completed restore must not quiesce")),
    )
    monkeypatch.setattr(
        helper,
        "_unit_snapshot",
        lambda *_args, **_kwargs: {"active": "inactive"},
    )
    monkeypatch.setattr(helper, "_start_unit", lambda _unit: events.append("start"))
    monkeypatch.setattr(
        helper,
        "_finalize_completed_state",
        lambda *_args: events.append("finalize"),
    )

    assert helper.recover() is state
    assert events == ["maintenance", "start", "finalize"]


@pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() != 0,
    reason="exact activity-lock metadata requires a root Linux test host",
)
def test_activity_exclusive_lock_blocks_shared_work_admission(
    helper: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "work-lock"
    directory.mkdir(mode=0o755)
    lock_file = directory / "activity.lock"
    lock_file.touch(mode=0o444)
    lock_file.chmod(0o444)
    monkeypatch.setattr(helper, "ACTIVITY_LOCK_DIR", directory)
    monkeypatch.setattr(helper, "ACTIVITY_LOCK_FILE", lock_file)

    contender = os.open(lock_file, os.O_RDONLY)
    try:
        with helper.activity_lock_exclusive():
            with pytest.raises(BlockingIOError):
                fcntl.flock(contender, fcntl.LOCK_SH | fcntl.LOCK_NB)
        fcntl.flock(contender, fcntl.LOCK_SH | fcntl.LOCK_NB)
    finally:
        os.close(contender)


@pytest.mark.parametrize(
    "fault_point",
    ["allow", "maintenance", "archive", "stage", "installer"],
)
def test_completed_cleanup_keeps_journal_until_all_other_artifacts_are_removed(
    helper: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault_point: str,
) -> None:
    state = _state_for_roots(helper, helper.EXPECTED_ROOTS)
    state["phase"] = "completed"
    allow = tmp_path / "restore-allowed"
    maintenance = tmp_path / "maintenance"
    maintenance.touch()
    journal = tmp_path / "restore.pending"
    events: list[str] = []
    fail_once = {fault_point}

    monkeypatch.setattr(helper, "RESTORE_ALLOW_FILE", allow)
    monkeypatch.setattr(helper, "MAINTENANCE_FILE", maintenance)
    monkeypatch.setattr(helper, "TRANSACTION_STATE", journal)
    monkeypatch.setattr(helper, "TRANSACTION_ROOT", tmp_path)
    monkeypatch.setattr(helper, "_wait_for_exact_health", lambda *_args: None)
    monkeypatch.setattr(helper, "_atomic_json", lambda *_args, **_kwargs: events.append("history"))

    def fail_at(name: str) -> None:
        events.append(name)
        if name in fail_once:
            fail_once.remove(name)
            raise helper.BackupRestoreError(f"fault at {name}")

    def remove_marker(path: Path) -> None:
        fail_at("allow" if path == allow else "journal")

    monkeypatch.setattr(helper, "_remove_marker", remove_marker)
    monkeypatch.setattr(helper, "_remove_maintenance", lambda: fail_at("maintenance"))
    monkeypatch.setattr(
        helper,
        "_remove_staged_archive_if_present",
        lambda _state: fail_at("archive"),
    )
    monkeypatch.setattr(
        helper,
        "_remove_stage_if_present",
        lambda _state, _root: fail_at("stage"),
    )
    monkeypatch.setattr(
        helper,
        "_remove_installer_if_present",
        lambda _state: fail_at("installer"),
    )
    monkeypatch.setattr(
        helper,
        "_refresh_installed_helper_from_active_release",
        lambda: fail_at("helper"),
    )

    with pytest.raises(helper.BackupRestoreError, match=f"fault at {fault_point}"):
        helper._finalize_completed_state(state, SimpleNamespace(backend_port=3002))

    assert "journal" not in events

    events.clear()
    helper._finalize_completed_state(state, SimpleNamespace(backend_port=3002))
    assert events.index("journal") > events.index("installer")
    assert events.index("helper") > events.index("journal")


def test_runtime_environment_parser_is_strict_and_requires_credentials(
    helper: ModuleType,
) -> None:
    values = helper._parse_runtime_environment(
        [
            "# managed by deploy.sh",
            "OPENAI_API_KEY=sk-test_123",
            f"APP_ACCESS_TOKEN={'x' * 32}",
            "PAYPAL_DONATE_URL=https://paypal.me/example",
            "MAX_GENERATED_QUESTIONS=20",
        ]
    )
    assert values["MAX_GENERATED_QUESTIONS"] == "20"

    invalid_cases = (
        (["OPENAI_API_KEY=abc", f"APP_ACCESS_TOKEN={'x' * 32}", "LD_PRELOAD=/tmp/x"], "unsupported"),
        (
            [
                "OPENAI_API_KEY=abc",
                "OPENAI_API_KEY=def",
                f"APP_ACCESS_TOKEN={'x' * 32}",
            ],
            "duplicate",
        ),
        ([" OPENAI_API_KEY=abc", f"APP_ACCESS_TOKEN={'x' * 32}"], "non-canonical"),
        (
            [
                "OPENAI_API_KEY=abc",
                f"APP_ACCESS_TOKEN={'x' * 32}",
                'PAYPAL_DONATE_URL=https://paypal.me/example"quoted',
            ],
            "PayPal URL",
        ),
        (["OPENAI_API_KEY=abc"], "missing required"),
    )
    for lines, message in invalid_cases:
        with pytest.raises(helper.BackupRestoreError, match=message):
            helper._parse_runtime_environment(lines)


def test_deploy_config_parser_rejects_unknown_duplicate_and_ambiguous_lines(
    helper: ModuleType,
) -> None:
    baseline = [
        f"DEPLOY_DIR={helper.DEPLOY_ROOT}",
        f"SERVICE_NAME={helper.SERVICE}",
        f"PERSIST_DIR={helper.PERSIST_ROOT}",
        "BACKEND_PORT=3002",
        "UPDATE_REMOTE_URL=https://github.com/example/project.git",
    ]
    config = helper._parse_deploy_config(baseline)
    assert config.backend_port == 3002

    for extra in (
        "LD_PRELOAD=/tmp/attacker.so",
        "BACKEND_PORT=3003",
        " BACKEND_PORT=3003",
    ):
        with pytest.raises(helper.BackupRestoreError, match="ambiguous|unknown"):
            helper._parse_deploy_config([*baseline, extra])


@pytest.mark.skipif(
    shutil.which("flock") is None or not Path("/proc/self/fd").is_dir(),
    reason="requires Linux procfs and util-linux flock",
)
@pytest.mark.parametrize(("parent_holds_lock", "expected_status"), [(False, 1), (True, 0)])
def test_restore_inner_accepts_only_the_inherited_lock_owner(
    tmp_path: Path,
    parent_holds_lock: bool,
    expected_status: int,
) -> None:
    lock_directory = tmp_path / "lock"
    lock_directory.mkdir()
    lock_file = lock_directory / "deployment.lock"
    lock_file.touch()
    result = subprocess.run(
        [
            "bash",
            "-c",
            r"""
source "$1"
DEPLOY_LOCK_DIR="$2"
DEPLOY_LOCK_FILE="$3"
DEPLOY_ADMIN_GROUP=sudo
stat() {
  if [[ "$*" == *"$DEPLOY_LOCK_DIR" ]]; then
    printf 'root:sudo:750\n'
  elif [[ "$*" == *"$DEPLOY_LOCK_FILE" ]]; then
    printf 'root:sudo:440:1\n'
  else
    command stat "$@"
  fi
}
exec {inherited_fd}<"$DEPLOY_LOCK_FILE"
if [[ "$4" == "held" ]]; then
  flock -n "$inherited_fd"
fi
if validate_inherited_restore_lock "$inherited_fd"; then
  exit 0
fi
exit 1
""",
            "bash",
            str(DEPLOY_SCRIPT),
            str(lock_directory),
            str(lock_file),
            "held" if parent_holds_lock else "free",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == expected_status, result.stderr


@pytest.mark.skipif(
    shutil.which("flock") is None or not Path("/proc/self/fd").is_dir(),
    reason="requires Linux procfs and util-linux flock",
)
@pytest.mark.parametrize(("parent_holds_lock", "expected_status"), [(False, 1), (True, 0)])
def test_restore_inner_accepts_only_the_inherited_activity_lock_owner(
    tmp_path: Path,
    parent_holds_lock: bool,
    expected_status: int,
) -> None:
    lock_directory = tmp_path / "work-lock"
    lock_directory.mkdir()
    lock_file = lock_directory / "activity.lock"
    lock_file.touch()
    result = subprocess.run(
        [
            "bash",
            "-c",
            r"""
source "$1"
ACTIVITY_LOCK_DIR="$2"
ACTIVITY_LOCK_FILE="$3"
stat() {
  if [[ "$1" == "-Lc" ]]; then
    command stat "$@"
  elif [[ "$*" == *"$ACTIVITY_LOCK_DIR" ]]; then
    printf 'root:root:755\n'
  elif [[ "$*" == *"$ACTIVITY_LOCK_FILE" ]]; then
    printf 'root:root:444:1\n'
  else
    command stat "$@"
  fi
}
exec {inherited_fd}<"$ACTIVITY_LOCK_FILE"
if [[ "$4" == "held" ]]; then
  flock -x -n "$inherited_fd"
fi
if validate_inherited_activity_lock "$inherited_fd"; then
  exit 0
fi
exit 1
""",
            "bash",
            str(DEPLOY_SCRIPT),
            str(lock_directory),
            str(lock_file),
            "held" if parent_holds_lock else "free",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == expected_status, result.stderr


@pytest.mark.skipif(
    shutil.which("flock") is None or not Path("/proc/self/fd").is_dir(),
    reason="requires Linux procfs and util-linux flock",
)
def test_restore_child_never_unlocks_the_parent_activity_lease(tmp_path: Path) -> None:
    lock_directory = tmp_path / "work-lock"
    lock_directory.mkdir()
    lock_file = lock_directory / "activity.lock"
    lock_file.touch()
    result = subprocess.run(
        [
            "bash",
            "-c",
            r"""
source "$1"
ACTIVITY_LOCK_DIR="$2"
ACTIVITY_LOCK_FILE="$3"
stat() {
  if [[ "$1" == "-Lc" ]]; then
    command stat "$@"
  elif [[ "$*" == *"$ACTIVITY_LOCK_DIR" ]]; then
    printf 'root:root:755\n'
  elif [[ "$*" == *"$ACTIVITY_LOCK_FILE" ]]; then
    printf 'root:root:444:1\n'
  else
    command stat "$@"
  fi
}
exec {parent_fd}<"$ACTIVITY_LOCK_FILE"
flock -x -n "$parent_fd"
exec {inherited_fd}<&${parent_fd}
validate_inherited_activity_lock "$inherited_fd"
release_activity_lock
exec {probe_fd}<"$ACTIVITY_LOCK_FILE"
if flock -s -n "$probe_fd"; then exit 91; fi
exec {parent_fd}>&-
flock -s -n "$probe_fd"
""",
            "bash",
            str(DEPLOY_SCRIPT),
            str(lock_directory),
            str(lock_file),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_deploy_has_fail_closed_restore_install_contract() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'RESTORE_INSTALL_ALLOW_FILE="/run/${SERVICE_NAME}-restore-install-allowed"' in script
    assert "--restore-install-inner" in script
    assert "validate_restore_install_context" in script
    assert "validate_inherited_restore_lock" in script
    assert "validate_inherited_activity_lock" in script
    assert 'payload["phase"] != "installing"' in script
    assert 'payload["commit"] != expected' in script
    assert '"installer_root"' in script
    assert '"installer_digest"' in script
    assert '[[ "${RESTORE_INSTALL_INNER}" -ne 1 ]]' in script
    assert "UCONN_RESTORE_LOCK_FD" in script
    assert "UCONN_RESTORE_ACTIVITY_LOCK_FD" in script
    restore_inner = script.split("restore_install_inner() {", 1)[1].split(
        "\nmain() {",
        1,
    )[0]
    assert "install_backup_restore_helper" not in restore_inner
    assert 'REPO_DIR="${CONFIGURED_SOURCE_REPO_DIR}"' in restore_inner
    finalizer = HELPER_PATH.read_text(encoding="utf-8").split(
        "def _finalize_completed_state(",
        1,
    )[1].split("\ndef install_restored_release", 1)[0]
    assert finalizer.index("_remove_marker(TRANSACTION_STATE)") < finalizer.index(
        "_refresh_installed_helper_from_active_release()"
    )
    assert "set \\$uconn_restore_block 0;" in script
    remote_validator = script.split("is_valid_update_remote_url() {", 1)[1].split(
        "\n}",
        1,
    )[0]
    assert "https://github\\.com/" in remote_validator
    assert "git@github" not in remote_validator
