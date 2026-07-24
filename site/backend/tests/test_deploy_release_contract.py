"""Regression checks for the release-based Raspberry Pi deployment contract."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEPLOY_SCRIPT = REPO_ROOT / "deploy" / "deploy.sh"


def test_auto_updater_builds_immutable_releases_and_never_merges() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'RELEASES_DIR="${DEPLOY_DIR}/releases"' in script
    assert 'CURRENT_RELEASE="${DEPLOY_DIR}/current"' in script
    assert "git -C \"${REPO_DIR}\" archive --format=tar" in script
    assert "switch_current_release" in script
    assert 'sudo python3 -m venv "${release_dir}/.venv"' in script
    assert 'sudo mv "${staging}" "${release_dir}"' not in script
    assert 'npm -C "${release_dir}/site/frontend" ci --no-audit --no-fund >&2' in script
    assert "git_as_owner reset --hard origin/main" not in script
    assert "git_as_owner clean -ffd" not in script


def test_auto_updater_preserves_runtime_data_and_reports_failure() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    for directory in ("uploads", "outputs", "runtime"):
        assert f'"${{PERSIST_DIR}}/{directory}"' in script
    assert "release_health_is_valid" in script
    assert '"rolled_back" "rollback"' in script
    assert '"deferred" "quarantined"' in script
    assert "OnUnitInactiveSec=60" in script
    assert "TimeoutStartSec=30min" in script
