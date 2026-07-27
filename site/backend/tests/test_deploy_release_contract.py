"""Regression checks for the release-based Raspberry Pi deployment contract."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEPLOY_SCRIPT = REPO_ROOT / "deploy" / "deploy.sh"


def _compact_shell(script: str) -> str:
    return " ".join(script.replace("\\\n", " ").split())


def test_auto_updater_builds_immutable_releases_and_never_merges() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    compact = _compact_shell(script)

    assert 'RELEASES_DIR="${DEPLOY_DIR}/releases"' in script
    assert 'CURRENT_RELEASE="${DEPLOY_DIR}/current"' in script
    assert "git -C \"${REPO_DIR}\" archive --format=tar" in script
    assert "switch_current_release" in script
    assert (
        'sudo -H -u "${BUILD_USER}" env HOME="${release_dir}/.build-home" '
        'python3 -m venv --copies "${release_dir}/.venv"'
    ) in compact
    assert "harden_release_ownership" in script
    assert "schema=2" in script
    assert "immutable_sha256" in script
    assert 'sudo find -P "${release_dir}" -xdev -exec chown -h root:root {} +' in script
    assert 'sudo mv "${staging}" "${release_dir}"' not in script
    assert (
        'sudo -H -u "${BUILD_USER}" env HOME="${release_dir}/.build-home" '
        'npm -C "${release_dir}/site/frontend" ci --no-audit --no-fund >&2'
    ) in compact
    assert "git_as_owner reset --hard origin/main" not in script
    assert "git_as_owner clean -ffd" not in script


def test_auto_updater_preserves_runtime_data_and_reports_failure() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    for directory in ("uploads", "outputs", "runtime"):
        assert f'"${{PERSIST_DIR}}/{directory}"' in script
    assert "release_health_is_valid" in script
    assert '"rolled_back" "rollback"' in script
    assert '"deferred" "quarantined"' in script
    assert "OnUnitInactiveSec=30min" in script
    assert "RandomizedDelaySec=5min" in script
    assert "TimeoutStartSec=30min" in script
    assert 'DEPLOY_STATUS_DIR="/var/lib/${SERVICE_NAME}-deploy"' in script
    assert "enter_deployment_maintenance" in script
    assert "leave_deployment_maintenance" in script


def test_root_updater_executes_installed_copy_and_not_repository_shell_data() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'UPDATER_SCRIPT="${UPDATER_INSTALL_DIR}/deploy.sh"' in script
    assert 'script_source="${active_release}/deploy/deploy.sh"' in script
    assert 'atomic_install_root_file "${script_source}" "${UPDATER_SCRIPT}" 0755' in script
    assert 'sudo install -o root -g root -m 755 "${BASH_SOURCE[0]}" "${UPDATER_SCRIPT}"' not in script
    assert "ExecStart=${UPDATER_SCRIPT} --update" in script
    assert ".deploy.env" not in script
    assert 'source "${DEPLOY_CONFIG_FILE}"' not in script
