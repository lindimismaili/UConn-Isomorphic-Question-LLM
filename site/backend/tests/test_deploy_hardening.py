"""Regression coverage for Raspberry Pi deployment hardening."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

import pytest

from backend.src.bootstrap import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[3]
DEPLOY_SCRIPT = REPO_ROOT / "deploy" / "deploy.sh"
RUNTIME_ENV_HELPER = REPO_ROOT / "deploy" / "runtime_env.py"
BACKUP_RESTORE_HELPER = REPO_ROOT / "deploy" / "backup_restore.py"
DEPLOY_GUIDE = REPO_ROOT / "deploy" / "DEPLOY.md"
BACKUP_RESTORE_GUIDE = REPO_ROOT / "deploy" / "BACKUP_RESTORE.md"


def _run_helper(
    path: Path,
    command: str,
    key: str,
    value: str = "",
    *,
    url: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command_args = [sys.executable, str(RUNTIME_ENV_HELPER), command, str(path), key]
    if url is not None:
        command_args.append(url)
    return subprocess.run(
        command_args,
        input=value,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


@contextmanager
def _running_http_server(
    handler: type[BaseHTTPRequestHandler],
) -> Iterator[ThreadingHTTPServer]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def _shell_function(script: str, name: str, next_name: str) -> str:
    start = script.index(f"\n{name}() {{") + 1
    end = script.index(f"\n{next_name}() {{", start)
    return script[start:end]


def _compact_shell(script: str) -> str:
    return " ".join(script.replace("\\\n", " ").split())


def test_restore_guide_routes_to_reviewed_executable_state_machine() -> None:
    guide = DEPLOY_GUIDE.read_text(encoding="utf-8")
    runbook = BACKUP_RESTORE_GUIDE.read_text(encoding="utf-8")
    helper = BACKUP_RESTORE_HELPER.read_text(encoding="utf-8")

    assert "[BACKUP_RESTORE.md](BACKUP_RESTORE.md)" in guide
    assert "/usr/local/libexec/uconn-isomorphic-backup-restore" in guide
    assert "rollback_partial_restore()" not in guide
    for command in ("backup", "restore", "recover", "install", "rollback"):
        assert f"uconn-isomorphic-backup-restore {command}" in f"{guide}\n{runbook}"
    assert "physical spare-SD restore" in runbook
    assert "fcntl.flock" in helper
    assert '\"phase\": \"quiescing\"' in helper
    assert 'state[\"phase\"] = \"extracting\"' in helper
    assert 'state[\"phase\"] = \"swapping\"' in helper
    assert 'state[\"phase\"] = \"ready\"' in helper
    assert 'state[\"phase\"] = \"installing\"' in helper
    assert "tar.extractall" not in helper
    assert "ProxyHandler({})" in helper


@pytest.mark.parametrize("option", ["-h", "--help"])
def test_deploy_help_is_non_mutating_and_documents_operator_commands(
    option: str,
    tmp_path: Path,
) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(tmp_path),
            "DEPLOY_DIR": str(tmp_path / "deploy"),
            "PERSIST_DIR": str(tmp_path / "persist"),
        }
    )
    result = subprocess.run(
        ["bash", str(DEPLOY_SCRIPT), option],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    for command in (
        "--preflight",
        "--status",
        "--configure-secrets",
        "--configure-donation",
        "--configure-runtime-policy",
        "--rollback",
        "--recover-maintenance",
        "--update",
    ):
        assert command in result.stdout
    assert "uconn-isomorphic-backup-restore" in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_deployment_bash_examples_are_syntax_checked_and_have_no_angle_placeholders() -> None:
    guide = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (DEPLOY_GUIDE, BACKUP_RESTORE_GUIDE)
    )
    bash_fences = re.findall(r"```bash\n(.*?)\n```", guide, flags=re.DOTALL)

    assert bash_fences
    for index, snippet in enumerate(bash_fences):
        assert re.search(r"<[A-Za-z][A-Za-z0-9_-]*>", snippet) is None
        result = subprocess.run(
            ["bash", "-n"],
            input=snippet,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, f"Bash fence {index} failed syntax validation: {result.stderr}"


def test_professor_install_requires_a_reviewed_detached_commit() -> None:
    guide = DEPLOY_GUIDE.read_text(encoding="utf-8")
    first_install = guide[guide.index("## First Install") : guide.index("## Device Credentials")]

    assert "set -euo pipefail" in first_install
    assert "https://github.com/lindimismaili/UConn-IsomorphicQuestionLLM.git" in first_install
    assert "Reviewed 40-hex release commit" in first_install
    assert "checkout --detach" in first_install
    assert 'rev-parse HEAD)" = "${RELEASE_COMMIT}"' in first_install


def test_runtime_env_helper_updates_atomically_without_disclosing_values(tmp_path: Path) -> None:
    environment = tmp_path / "environment"
    environment.write_text(
        "# device settings\nPAYPAL_DONATE_URL=https://paypal.me/example\n",
        encoding="utf-8",
    )
    synthetic_key = "sk-test-SYNTHETIC_123"

    result = _run_helper(environment, "set", "OPENAI_API_KEY", synthetic_key)

    assert result.returncode == 0
    assert synthetic_key not in result.stdout
    assert synthetic_key not in result.stderr
    contents = environment.read_text(encoding="utf-8")
    assert "# device settings" in contents
    assert "PAYPAL_DONATE_URL=https://paypal.me/example" in contents
    assert f"OPENAI_API_KEY={synthetic_key}" in contents
    assert environment.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".environment.*"))
    assert _run_helper(environment, "has", "OPENAI_API_KEY").returncode == 0
    assert _run_helper(environment, "has", "APP_ACCESS_TOKEN").returncode == 1


def test_runtime_env_helper_refuses_duplicate_existing_keys(tmp_path: Path) -> None:
    environment = tmp_path / "environment"
    environment.write_text(
        "OPENAI_API_KEY=old-one\nAPP_ACCESS_TOKEN=keep-me\nOPENAI_API_KEY=old-two\n",
        encoding="utf-8",
    )
    before = environment.read_bytes()

    result = _run_helper(environment, "set", "OPENAI_API_KEY", "new-key")

    assert result.returncode == 2
    assert environment.read_bytes() == before
    assert "new-key" not in result.stdout
    assert "new-key" not in result.stderr


@pytest.mark.parametrize(
    "contents",
    [
        "LD_PRELOAD=/tmp/attacker.so\n",
        "HTTPS_PROXY=http://attacker.invalid\n",
        "export OPENAI_API_KEY=synthetic-key\n",
        " OPENAI_API_KEY=synthetic-key\n",
        "OPENAI_API_KEY =synthetic-key\n",
        "OPENAI_API_KEY='synthetic-key'\n",
    ],
    ids=(
        "loader-injection",
        "proxy-injection",
        "export-syntax",
        "leading-space",
        "space-before-equals",
        "quoted-value",
    ),
)
def test_runtime_env_helper_refuses_unknown_or_noncanonical_existing_lines(
    tmp_path: Path,
    contents: str,
) -> None:
    environment = tmp_path / "environment"
    environment.write_text(contents, encoding="utf-8")
    before = environment.read_bytes()

    validate = _run_helper(environment, "validate", "")
    update = _run_helper(environment, "set", "OPENAI_API_KEY", "replacement-key")

    assert validate.returncode == 2
    assert update.returncode == 2
    assert environment.read_bytes() == before
    assert "replacement-key" not in update.stdout
    assert "replacement-key" not in update.stderr


def test_runtime_env_helper_rejects_unsafe_values_and_symlink_targets(tmp_path: Path) -> None:
    environment = tmp_path / "environment"
    target = tmp_path / "target"
    target.write_text("sentinel\n", encoding="utf-8")
    environment.symlink_to(target)

    symlink_result = _run_helper(environment, "set", "OPENAI_API_KEY", "synthetic-key")
    unsafe_result = _run_helper(tmp_path / "safe-environment", "set", "OPENAI_API_KEY", "bad value")
    weak_token_result = _run_helper(
        tmp_path / "safe-environment",
        "set",
        "APP_ACCESS_TOKEN",
        "short-token",
    )

    assert symlink_result.returncode == 2
    assert target.read_text(encoding="utf-8") == "sentinel\n"
    assert unsafe_result.returncode == 2
    assert weak_token_result.returncode == 2
    assert "bad value" not in unsafe_result.stdout
    assert "bad value" not in unsafe_result.stderr
    assert "short-token" not in weak_token_result.stdout
    assert "short-token" not in weak_token_result.stderr


def test_runtime_env_helper_validates_paypal_and_bounded_runtime_policy(tmp_path: Path) -> None:
    environment = tmp_path / "environment"
    environment.write_text("", encoding="utf-8")

    valid_paypal = "https://donate.paypal.com/donate/?hosted_button_id=example"
    assert _run_helper(environment, "set", "PAYPAL_DONATE_URL", valid_paypal).returncode == 0
    assert _run_helper(environment, "has", "PAYPAL_DONATE_URL").returncode == 0
    assert (
        _run_helper(
            environment,
            "set",
            "PAYPAL_DONATE_URL",
            "https://paypal.com:8443/donate",
        ).returncode
        == 2
    )

    assert _run_helper(environment, "set", "APP_RATE_LIMIT_REQUESTS", "20").returncode == 0
    policy = _run_helper(environment, "get", "APP_RATE_LIMIT_REQUESTS")
    assert policy.returncode == 0
    assert policy.stdout.strip() == "20"
    assert _run_helper(environment, "set", "APP_RATE_LIMIT_REQUESTS", "1001").returncode == 2
    assert _run_helper(environment, "unset", "PAYPAL_DONATE_URL").returncode == 0
    assert _run_helper(environment, "has", "PAYPAL_DONATE_URL").returncode == 1


def test_runtime_env_helper_applies_complete_policy_in_one_atomic_replace(tmp_path: Path) -> None:
    environment = tmp_path / "environment"
    environment.write_text("OPENAI_API_KEY=synthetic-secret\nAPP_RATE_LIMIT_REQUESTS=12\n", encoding="utf-8")
    policy = {
        "APP_RATE_LIMIT_REQUESTS": "20",
        "APP_RATE_LIMIT_WINDOW_SECONDS": "60",
        "UPLOAD_RETENTION_SECONDS": "86400",
        "OUTPUT_RETENTION_SECONDS": "604800",
        "WORKFLOW_TTL_SECONDS": "86400",
        "WORKFLOW_MAX_ENTRIES": "200",
        "OPENAI_WORKFLOW_CONCURRENCY": "1",
        "BATCH_WORKFLOW_CONCURRENCY": "2",
        "MAX_GENERATED_QUESTIONS": "100",
    }

    valid = subprocess.run(
        [sys.executable, str(RUNTIME_ENV_HELPER), "set-many", str(environment)],
        input=json.dumps(policy),
        text=True,
        capture_output=True,
        check=False,
    )
    assert valid.returncode == 0, valid.stderr
    applied = environment.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=synthetic-secret" in applied
    assert "APP_RATE_LIMIT_REQUESTS=20" in applied

    invalid_policy = {**policy, "MAX_GENERATED_QUESTIONS": "501"}
    before = environment.read_bytes()
    invalid = subprocess.run(
        [sys.executable, str(RUNTIME_ENV_HELPER), "set-many", str(environment)],
        input=json.dumps(invalid_policy),
        text=True,
        capture_output=True,
        check=False,
    )
    assert invalid.returncode == 2
    assert environment.read_bytes() == before


def test_runtime_env_helper_authenticates_local_status_without_disclosing_token(tmp_path: Path) -> None:
    environment = tmp_path / "environment"
    synthetic_token = "synthetic-browser-token-with-32-characters"
    assert _run_helper(environment, "set", "APP_ACCESS_TOKEN", synthetic_token).returncode == 0

    class Handler(BaseHTTPRequestHandler):
        received_token = ""

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            type(self).received_token = self.headers.get("X-App-Access-Token", "")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"active_workflows": 0}')

        def log_message(self, _format: str, *_args: object) -> None:
            return

    with _running_http_server(Handler) as server:
        result = _run_helper(
            environment,
            "authenticated-get",
            "APP_ACCESS_TOKEN",
            url=f"http://127.0.0.1:{server.server_port}/api/deployment/status",
        )

    assert result.returncode == 0
    assert result.stdout == '{"active_workflows": 0}'
    assert synthetic_token not in result.stdout
    assert synthetic_token not in result.stderr
    assert Handler.received_token == synthetic_token


def test_authenticated_status_ignores_hostile_proxy_environment(tmp_path: Path) -> None:
    environment = tmp_path / "environment"
    synthetic_token = "synthetic-browser-token-with-32-characters"
    assert _run_helper(environment, "set", "APP_ACCESS_TOKEN", synthetic_token).returncode == 0

    class TargetHandler(BaseHTTPRequestHandler):
        hits = 0

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            type(self).hits += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"active_workflows": 0}')

        def log_message(self, _format: str, *_args: object) -> None:
            return

    class ProxyHandler(BaseHTTPRequestHandler):
        hits = 0

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            type(self).hits += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"active_workflows": 99}')

        def log_message(self, _format: str, *_args: object) -> None:
            return

    with _running_http_server(TargetHandler) as target, _running_http_server(ProxyHandler) as proxy:
        hostile_environment = {
            **os.environ,
            "HTTP_PROXY": f"http://127.0.0.1:{proxy.server_port}",
            "http_proxy": f"http://127.0.0.1:{proxy.server_port}",
            "ALL_PROXY": f"http://127.0.0.1:{proxy.server_port}",
            "all_proxy": f"http://127.0.0.1:{proxy.server_port}",
            "NO_PROXY": "",
            "no_proxy": "",
        }
        result = _run_helper(
            environment,
            "authenticated-get",
            "APP_ACCESS_TOKEN",
            url=f"http://127.0.0.1:{target.server_port}/api/deployment/status",
            env=hostile_environment,
        )

    assert result.returncode == 0
    assert result.stdout == '{"active_workflows": 0}'
    assert TargetHandler.hits == 1
    assert ProxyHandler.hits == 0


def test_authenticated_status_never_forwards_token_to_redirect(tmp_path: Path) -> None:
    environment = tmp_path / "environment"
    synthetic_token = "synthetic-browser-token-with-32-characters"
    assert _run_helper(environment, "set", "APP_ACCESS_TOKEN", synthetic_token).returncode == 0

    class SinkHandler(BaseHTTPRequestHandler):
        hits = 0
        received_token = ""

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            type(self).hits += 1
            type(self).received_token = self.headers.get("X-App-Access-Token", "")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"active_workflows": 0}')

        def log_message(self, _format: str, *_args: object) -> None:
            return

    with _running_http_server(SinkHandler) as sink:

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
                self.send_response(302)
                self.send_header(
                    "Location",
                    f"http://127.0.0.1:{sink.server_port}/api/deployment/status",
                )
                self.end_headers()

            def log_message(self, _format: str, *_args: object) -> None:
                return

        with _running_http_server(RedirectHandler) as redirect:
            result = _run_helper(
                environment,
                "authenticated-get",
                "APP_ACCESS_TOKEN",
                url=f"http://127.0.0.1:{redirect.server_port}/api/deployment/status",
            )

    assert result.returncode == 2
    assert SinkHandler.hits == 0
    assert SinkHandler.received_token == ""
    assert synthetic_token not in result.stdout
    assert synthetic_token not in result.stderr


@pytest.mark.parametrize(
    "payload",
    [
        b"x" * (64 * 1024 + 1),
        b"not-json",
        b'{"active_workflows": true}',
        b'{"active_workflows": -1}',
    ],
    ids=("oversize", "invalid-json", "boolean-count", "negative-count"),
)
def test_authenticated_status_rejects_untrusted_response_shapes(
    tmp_path: Path,
    payload: bytes,
) -> None:
    environment = tmp_path / "environment"
    synthetic_token = "synthetic-browser-token-with-32-characters"
    assert _run_helper(environment, "set", "APP_ACCESS_TOKEN", synthetic_token).returncode == 0

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    with _running_http_server(Handler) as server:
        result = _run_helper(
            environment,
            "authenticated-get",
            "APP_ACCESS_TOKEN",
            url=f"http://127.0.0.1:{server.server_port}/api/deployment/status",
        )

    assert result.returncode == 2
    assert result.stdout == ""
    assert synthetic_token not in result.stderr


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ('{"active_workflows": 0}', "0"),
        ('{"active_workflows": 3}', "3"),
        ("{}", "-1"),
        ('{"active_workflows": "0"}', "-1"),
        ('{"active_workflows": true}', "-1"),
        ('{"active_workflows": -1}', "-1"),
        ("[]", "-1"),
    ],
    ids=(
        "zero",
        "positive",
        "missing",
        "string",
        "boolean",
        "negative",
        "non-object",
    ),
)
def test_deploy_active_workflow_count_requires_exact_response_contract(
    payload: str,
    expected: str,
) -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            r"""
source "$1"
PAYLOAD="$2"
systemd_unit_property() {
  case "$2" in
    LoadState) printf 'loaded\n' ;;
    ActiveState) printf 'active\n' ;;
    *) return 1 ;;
  esac
}
sudo() { printf '%s\n' "$PAYLOAD"; }
active_workflow_count
""",
            "bash",
            str(DEPLOY_SCRIPT),
            payload,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


def test_process_environment_overrides_release_dotenv(tmp_path: Path, monkeypatch) -> None:
    release_env = tmp_path / ".env"
    release_env.write_text("OPENAI_API_KEY=release-default\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "device-override")

    load_dotenv(release_env)

    assert os.environ["OPENAI_API_KEY"] == "device-override"


@pytest.mark.parametrize(
    ("answer", "accepted"),
    [
        ("", False),
        ("n", False),
        ("no", False),
        ("anything", False),
        ("y", True),
        ("Y", True),
        ("yes", True),
        (" YES ", True),
    ],
)
def test_public_actions_require_explicit_yes(answer: str, accepted: bool) -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; is_explicit_yes "$2"',
            "bash",
            str(DEPLOY_SCRIPT),
            answer,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is accepted


@pytest.mark.parametrize(
    ("machine_arch", "package_arch", "accepted"),
    [
        ("aarch64", "arm64", True),
        ("aarch64", "", False),
        ("arm64", "arm64", False),
        ("armv7l", "armhf", False),
        ("x86_64", "amd64", False),
        ("", "", False),
    ],
)
def test_pi_architecture_preflight(
    machine_arch: str,
    package_arch: str,
    accepted: bool,
) -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; architecture_is_supported "$2" "$3"',
            "bash",
            str(DEPLOY_SCRIPT),
            machine_arch,
            package_arch,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is accepted


def _validate_deploy_setting(setting: str, value: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            """
source "$1"
realpath() {
  python3 -c 'import os,sys; print(os.path.normpath(sys.argv[-1]))' "$@"
}
trusted_root_is_safe() { return 0; }
DEPLOY_DIR=/var/www/uconn-test
PERSIST_DIR=/var/lib/uconn-test
SERVICE_NAME=uconn-test
CLOUDFLARED_SERVICE_NAME=uconn-test-cloudflared
CLOUDFLARED_CONFIG_FILE=/etc/cloudflared/uconn-test.yml
printf -v "$2" '%s' "$3"
DEPLOY_STATUS_DIR="/var/lib/${SERVICE_NAME}-deploy"
validate_deploy_settings
""",
            "bash",
            str(DEPLOY_SCRIPT),
            setting,
            value,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_validate_deploy_settings_accepts_safe_baseline() -> None:
    result = _validate_deploy_setting("PERSIST_DIR", "/var/lib/uconn-test")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("PERSIST_DIR", "/etc"),
        ("PERSIST_DIR", "/var/lib/.."),
        ("PERSIST_DIR", "/var/lib/professor/uconn"),
        ("DEPLOY_DIR", "/var/www/professor/uconn"),
        ("SERVICE_NAME", "."),
        ("SERVICE_NAME", ".."),
        ("SERVICE_NAME", "-uconn"),
        ("SERVICE_NAME", "nginx"),
        ("SERVICE_NAME", "uconn-test-update"),
        ("SERVICE_NAME", "uconn-test-cloudflared"),
        ("CLOUDFLARED_SERVICE_NAME", "uconn-test"),
        ("CLOUDFLARED_SERVICE_NAME", "uconn-test-update"),
    ],
)
def test_validate_deploy_settings_rejects_dangerous_targets(setting: str, value: str) -> None:
    result = _validate_deploy_setting(setting, value)
    assert result.returncode != 0


def test_deploy_contract_uses_stable_device_overrides_and_safe_prompts() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    release_env = "EnvironmentFile=-${CURRENT_RELEASE}/site/.env"
    device_env = "EnvironmentFile=${RUNTIME_ENV_FILE}"
    assert 'RUNTIME_ENV_FILE="${RUNTIME_CONFIG_DIR}/environment"' in script
    assert script.index(release_env) < script.index(device_env)
    assert 'sudo install -o root -g root -m 600 /dev/null "${RUNTIME_ENV_FILE}"' in script
    assert "os.getenv('OPENAI_API_KEY') and os.getenv('APP_ACCESS_TOKEN')" in script
    assert 'read -rsp "OpenAI API key (input hidden): "' in script
    assert 'secrets.token_urlsafe(32)' in script
    assert 'confirm_default_no "Configure optional Cloudflare public hosting?"' in script
    assert (
        'confirm_default_no "Enable automatic updates from the pinned main branch about every 30 minutes?"'
        in script
    )
    assert 'if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then' in script


def test_deploy_contract_has_pi_preflight_complete_marker_and_nginx_limits() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'architecture_is_supported "${machine_arch}" "${package_arch}"' in script
    assert "sys.version_info >= (3, 10)" in script
    assert "/proc/meminfo" in script
    assert "available_disk_mib" in script
    assert 'MIN_FREE_DISK_MIB="${MIN_FREE_DISK_MIB:-8192}"' in script
    assert 'release_is_complete "${release_dir}" "${commit_sha}"' in script
    assert '.release-ready' in script
    assert 'npm -C "${release_dir}/site/frontend" ci --no-audit --no-fund >&2' in script
    assert (
        'sudo -u "${BUILD_USER}" rm -rf "${release_dir}/site/frontend/node_modules" '
        '"${release_dir}/.build-home"'
    ) in script
    assert "client_max_body_size 64m;" in script
    assert "limit_req_status 429;" in script
    assert "limit_conn_status 429;" in script
    assert 'proxy_set_header X-App-Access-Token \\$http_x_app_access_token;' in script
    assert 'add_header X-Content-Type-Options "nosniff" always;' in script
    assert "if (-f ${ACTIVATION_STATE_FILE})" in script


def test_candidate_data_is_flushed_before_ready_marker_and_selector_switch() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    build = _shell_function(script, "build_candidate_release", "release_health_is_valid")

    harden = 'harden_release_ownership "${release_dir}"'
    flush = 'sudo sync -f "${release_dir}"'
    marker = 'write_release_marker "${release_dir}" "${marker_content}"'
    assert harden in build
    assert flush in build
    assert marker in build
    assert build.index(harden) < build.index(flush) < build.index(marker)


def test_deployment_status_writer_is_root_owned_and_symlink_safe() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    writer = _shell_function(script, "write_deployment_status", "current_release_path")

    assert 'DEPLOY_STATUS_DIR="/var/lib/${SERVICE_NAME}-deploy"' in script
    assert 'sudo install -d -o root -g root -m 755 "${DEPLOY_STATUS_DIR}"' in writer
    assert "os.open(path, os.O_RDONLY | os.O_NOFOLLOW)" in writer
    assert 'tempfile.mkstemp(prefix=".deployment-status.", dir=path.parent)' in writer
    assert "os.replace(temporary, path)" in writer
    assert "os.chown(" not in writer
    assert not any(
        "chown" in line and ("DEPLOY_STATUS_FILE" in line or "deployment_status.json" in line)
        for line in script.splitlines()
    )


def test_deployment_uses_dedicated_non_login_app_and_build_users() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'APP_USER="uconn-isomorphic"' in script
    assert 'BUILD_USER="uconn-isomorphic-build"' in script
    assert "--home-dir /nonexistent --shell /usr/sbin/nologin" in script
    assert "User=${APP_USER}" in script
    assert "Group=${APP_USER}" in script
    assert (
        'sudo -u "${BUILD_USER}" tar --no-same-owner --no-same-permissions -x -C "${release_dir}"'
        in _compact_shell(script)
    )
    assert 'sudo -H -u "${BUILD_USER}" env HOME="${release_dir}/.build-home"' in script


def test_cloudflared_uses_dedicated_user_and_strict_systemd_sandbox() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    installer = _shell_function(script, "install_cloudflared", "ensure_cloudflare_authenticated")
    unit = _shell_function(
        script,
        "install_cloudflared_service",
        "verify_public_https_redirect",
    )

    assert 'CLOUDFLARED_USER="uconn-cloudflared"' in script
    assert 'CLOUDFLARED_BIN="/usr/bin/cloudflared"' in script
    assert 'stat -c \'%U:%G\' "${CLOUDFLARED_BIN}"' in installer
    assert "Ignoring untrusted cloudflared executable" in installer
    assert "ExecStart=${CLOUDFLARED_BIN}" in unit
    assert "command -v cloudflared" not in unit
    assert 'User=${CLOUDFLARED_USER}' in unit
    assert 'Group=${CLOUDFLARED_USER}' in unit
    for directive in (
        "NoNewPrivileges=true",
        "PrivateTmp=true",
        "PrivateDevices=true",
        "ProtectSystem=strict",
        "ProtectHome=true",
        "ProtectKernelTunables=true",
        "ProtectKernelModules=true",
        "ProtectControlGroups=true",
        "RestrictSUIDSGID=true",
        "CapabilityBoundingSet=",
        "AmbientCapabilities=",
        "LockPersonality=true",
    ):
        assert directive in unit
    credential_copy = _shell_function(script, "copy_cloudflare_credentials", "write_cloudflare_config")
    assert "os.O_RDONLY | os.O_NOFOLLOW" in credential_copy
    assert 'tempfile.mkstemp(prefix=".cloudflared-credential."' in credential_copy
    assert "os.replace(temporary, destination)" in credential_copy
    assert 'EXPECTED_TUNNEL_UUID="${TUNNEL_UUID}"' in credential_copy


def test_public_cloudflare_host_requires_https_only_cors_and_verified_redirect() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    backend = _shell_function(script, "install_backend_service", "restart_installed_backend_service")
    setup = _shell_function(script, "setup_cloudflare", "disable_cloudflare_public_hosting")
    disable = _shell_function(
        script,
        "disable_cloudflare_public_hosting",
        "apply_local_only_hosting",
    )

    assert 'allowed_origins="${allowed_origins},https://${PUBLIC_HOSTNAME}"' in backend
    assert "http://${PUBLIC_HOSTNAME}" not in backend
    assert "Is Cloudflare 'Always Use HTTPS' enabled" in setup
    assert 'verify_public_https_redirect "${PUBLIC_HOSTNAME}" "/"' in setup
    assert 'verify_public_https_access "${PUBLIC_HOSTNAME}" "/api/readiness"' in setup
    assert 'disable_systemd_unit_if_present "${CLOUDFLARED_SERVICE_NAME}.service"' in setup
    assert script.count('"${CURL_BIN}" -q --noproxy \'*\'') >= 8
    assert '"http://${hostname}${request_path}"' in script
    assert '"https://${hostname}${request_path}"' in script
    assert disable.index("disable_systemd_unit_if_present") < disable.index(
        "stop_conflicting_cloudflared_services"
    )
    assert disable.index("stop_conflicting_cloudflared_services") < disable.index(
        "unset PUBLIC_HOSTNAME"
    )
    assert "conflicting_cloudflared_service_is_active" not in disable

    success = subprocess.run(
        [
            "bash",
            "-c",
            r"""
source "$1"
CURL_BIN=curl
curl() {
  printf 'HTTP/1.1 301 Moved Permanently\r\nLocation: https://professor.example/path\r\n\r\n'
}
sleep() { :; }
verify_public_https_redirect professor.example
""",
            "bash",
            str(DEPLOY_SCRIPT),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    wrong_target = subprocess.run(
        [
            "bash",
            "-c",
            r"""
source "$1"
CURL_BIN=curl
curl() {
  printf 'HTTP/1.1 301 Moved Permanently\r\nLocation: https://professor.example.attacker/\r\n\r\n'
}
sleep() { :; }
verify_public_https_redirect professor.example
""",
            "bash",
            str(DEPLOY_SCRIPT),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    access = subprocess.run(
        [
            "bash",
            "-c",
            r"""
source "$1"
CURL_BIN=curl
curl() {
  printf 'HTTP/1.1 302 Found\r\nLocation: https://uconn-team.cloudflareaccess.com/cdn-cgi/access/login\r\n\r\n'
}
sleep() { :; }
verify_public_https_access professor.example
""",
            "bash",
            str(DEPLOY_SCRIPT),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    managed_oauth_access = subprocess.run(
        [
            "bash",
            "-c",
            r"""
source "$1"
CURL_BIN=curl
curl() {
  printf 'HTTP/1.1 401 Unauthorized\r\nWWW-Authenticate: Bearer authorization_server="https://uconn-team.cloudflareaccess.com/.well-known/oauth-authorization-server"\r\n\r\n'
}
sleep() { :; }
verify_public_https_access professor.example
""",
            "bash",
            str(DEPLOY_SCRIPT),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    generic_forbidden = subprocess.run(
        [
            "bash",
            "-c",
            r"""
source "$1"
CURL_BIN=curl
curl() {
  printf 'HTTP/1.1 403 Forbidden\r\n\r\n'
}
sleep() { :; }
verify_public_https_access professor.example
""",
            "bash",
            str(DEPLOY_SCRIPT),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert success.returncode == 0, success.stderr
    assert wrong_target.returncode != 0
    assert access.returncode == 0, access.stderr
    assert managed_oauth_access.returncode == 0, managed_oauth_access.stderr
    assert generic_forbidden.returncode != 0


def test_deployment_guide_matches_idle_only_configuration_and_preview_behavior() -> None:
    guide = DEPLOY_GUIDE.read_text(encoding="utf-8")

    assert "drains active work" not in guide
    assert "drain-aware configuration command" not in guide
    assert "refuses the change unless" in guide
    assert "Both modes always rebuild the frontend" not in guide
    assert "Both modes start a fresh backend" in guide
    assert "`--web` runs `vite build`" in guide
    assert "`--dev` starts a fresh backend with `--reload`" in guide


def test_cloudflared_service_discovery_fails_closed_and_includes_loaded_instances() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    discovery = _shell_function(
        script,
        "cloudflared_service_units",
        "cloudflared_service_config_paths",
    )
    stop = _shell_function(
        script,
        "stop_conflicting_cloudflared_services",
        "print_existing_tunnels",
    )

    assert "systemctl list-unit-files" in discovery
    assert "systemctl list-units" in discovery
    assert "|| true" not in discovery
    assert "$1 !~ /@\\.service$/" in discovery
    assert 'if ! units="$(cloudflared_service_units)"; then' in stop
    assert "unmanaged cloudflared process" in stop
    assert "cloudflared_service_relation" in stop
    assert "classified ${relation}" in stop


def test_cloudflared_discovery_skips_inert_templates_but_finds_differently_named_units() -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            r"""
source "$1"
systemctl() {
  case "$1" in
    list-unit-files)
      printf '%s\n' \
        'getty@.service static -' \
        'cloudflared@.service static -' \
        'course-tunnel.service enabled enabled'
      ;;
    list-units)
      return 0
      ;;
    show)
      if [[ "$2" == *@.service ]]; then
        printf 'template must not be queried\n' >&2
        return 99
      fi
      printf '{ path=/usr/bin/cloudflared ; argv[]=/usr/bin/cloudflared tunnel run ; }\n'
      ;;
    *)
      return 98
      ;;
  esac
}
cloudflared_service_units
""",
            "bash",
            str(DEPLOY_SCRIPT),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["course-tunnel.service"]


def test_systemd_disable_verification_fails_closed_on_query_error() -> None:
    safe = subprocess.run(
        [
            "bash",
            "-c",
            r"""
source "$1"
systemctl() {
  if [[ "$1" == show ]]; then
    case "$3" in
      --property=LoadState) printf 'loaded\n' ;;
      --property=ActiveState) printf 'inactive\n' ;;
      --property=UnitFileState) printf 'disabled\n' ;;
      *) return 91 ;;
    esac
  else
    return 0
  fi
}
sudo() { systemctl "${@:2}"; }
disable_systemd_unit_if_present example.service
""",
            "bash",
            str(DEPLOY_SCRIPT),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    query_error = subprocess.run(
        [
            "bash",
            "-c",
            r"""
source "$1"
systemctl() {
  if [[ "$1" == show && "$3" == --property=LoadState ]]; then
    return 1
  fi
  return 0
}
sudo() { systemctl "${@:2}"; }
disable_systemd_unit_if_present example.service
""",
            "bash",
            str(DEPLOY_SCRIPT),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert safe.returncode == 0, safe.stderr
    assert query_error.returncode != 0


def test_nginx_is_rejecting_by_default_and_routes_exact_local_hosts() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    nginx = _shell_function(script, "install_nginx_config", "restore_nginx_install_state")

    assert 'server_names="${LOCAL_DOMAIN} ${LOCAL_HOSTNAME} localhost 127.0.0.1"' in nginx
    assert "listen ${listen_address} default_server;" in nginx
    assert "server_name _;" in nginx
    assert "return 444;" in nginx
    assert "listen ${listen_address};" in nginx
    assert "verify_nginx_app_hosts" in nginx
    assert "conflicting server name" in nginx


def test_ssh_tunnel_origin_is_allowed_without_opening_other_loopback_ports() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    backend = _shell_function(script, "install_backend_service", "restart_installed_backend_service")

    assert "http://127.0.0.1:8080" in backend
    assert "http://127.0.0.1:8081" not in backend


def test_maintenance_signals_exit_and_all_activators_share_one_lock() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    enter = _shell_function(script, "enter_deployment_maintenance", "acquire_deployment_lock")
    updater = _shell_function(script, "run_auto_update", "install_auto_update_units")
    rollback = _shell_function(script, "manual_rollback", "main")
    main = script[script.index("\nmain() {") :]

    assert "trap cleanup_deployment_transaction EXIT" in enter
    assert "exit 130" in enter
    assert "exit 143" in enter
    assert enter.index("write_deployment_maintenance") < enter.index(
        "acquire_activity_lock_exclusive"
    )
    assert 'flock -n "${DEPLOY_LOCK_FD}"' in script
    for body in (updater, rollback, main):
        assert "acquire_deployment_lock" in body
    assert updater.index("acquire_deployment_lock") < updater.index("recover_pending_activation")
    assert rollback.index("acquire_deployment_lock") < rollback.index("recover_pending_activation")
    assert main.index("acquire_deployment_lock") < main.index(
        "clear_stale_deployment_maintenance"
    )
    assert "${SERVICE_NAME}-update.lock" not in script


def test_maintenance_callers_persist_their_exact_recovery_reason() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    enter = _shell_function(script, "enter_deployment_maintenance", "acquire_deployment_lock")
    cloudflare = _shell_function(
        script,
        "setup_cloudflare",
        "disable_cloudflare_public_hosting",
    )
    local_only = _shell_function(
        script,
        "apply_local_only_hosting",
        "write_deployment_status",
    )
    layout = _shell_function(script, "ensure_release_layout", "switch_current_release")
    runtime = _shell_function(
        script,
        "prepare_secret_management",
        "configure_donation_command",
    )

    assert 'local reason="$1"' in enter
    assert "${1:-deployment}" not in enter
    assert 'enter_deployment_maintenance "cloudflare_setup"' in cloudflare
    assert 'enter_deployment_maintenance "local_only"' in local_only
    assert layout.count('enter_deployment_maintenance "ownership_migration"') == 2
    assert 'enter_deployment_maintenance "runtime_config"' in runtime
    calls = re.findall(
        r'^\s+enter_deployment_maintenance "[a-z_]+"\S*(?:\s+\S+)*$',
        script,
        flags=re.MULTILINE,
    )
    assert len(calls) == 9
    assert all(call.endswith("|| return 1") for call in calls)


def test_deployment_lock_is_non_truncating_and_not_publicly_lockable() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    lock = _shell_function(script, "acquire_deployment_lock", "ask_to_continue")

    assert 'DEPLOY_LOCK_DIR="/run/${SERVICE_NAME}-deploy-lock"' in script
    assert 'DEPLOY_ADMIN_GROUP="sudo"' in script
    assert '-g "${DEPLOY_ADMIN_GROUP}" -m 750 "${DEPLOY_LOCK_DIR}"' in lock
    assert '-g "${DEPLOY_ADMIN_GROUP}" -m 440 /dev/null "${DEPLOY_LOCK_FILE}"' in lock
    assert 'exec {DEPLOY_LOCK_FD}<"${DEPLOY_LOCK_FILE}"' in lock
    assert 'exec {DEPLOY_LOCK_FD}>"' not in lock


def test_paid_work_activity_lock_is_stable_and_recreated_by_tmpfiles() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    verify = _shell_function(script, "verify_activity_lock", "ensure_activity_lock")
    ensure = _shell_function(script, "ensure_activity_lock", "acquire_activity_lock_exclusive")
    acquire = _shell_function(
        script,
        "acquire_activity_lock_exclusive",
        "release_activity_lock",
    )
    release = _shell_function(script, "release_activity_lock", "ask_to_continue")
    updater = _shell_function(script, "run_auto_update", "install_auto_update_units")
    main = script[script.index("\nmain() {") :]

    assert 'ACTIVITY_LOCK_DIR="/run/${SERVICE_NAME}-work-lock"' in script
    assert 'ACTIVITY_LOCK_FILE="${ACTIVITY_LOCK_DIR}/activity.lock"' in script
    assert (
        'ACTIVITY_LOCK_TMPFILES="/etc/tmpfiles.d/${SERVICE_NAME}-work-lock.conf"'
        in script
    )
    assert "root:root:755" in verify
    assert "root:root:444:1" in verify
    assert '-o root -g root -m 444 /dev/null "${ACTIVITY_LOCK_FILE}"' in ensure
    assert "d %s 0755 root root -" in ensure
    assert "f %s 0444 root root -" in ensure
    assert 'systemd-tmpfiles --create "${ACTIVITY_LOCK_TMPFILES}"' in ensure
    assert 'exec {ACTIVITY_LOCK_FD}<"${ACTIVITY_LOCK_FILE}"' in acquire
    assert 'flock -x "${ACTIVITY_LOCK_FD}"' in acquire
    assert 'stat -Lc \'%d:%i\' "/proc/$$/fd/${ACTIVITY_LOCK_FD}"' in acquire
    assert "install -o root -g root -m 444 /dev/null" not in acquire
    assert 'flock -u "${ACTIVITY_LOCK_FD}"' in release
    assert 'if [[ "${ACTIVITY_LOCK_INHERITED}" -ne 1 ]]' in release
    assert updater.index("ensure_activity_lock") < updater.index(
        "recover_pending_activation"
    )
    assert main.index("install_system_packages") < main.index("ensure_activity_lock")
    assert main.index("ensure_activity_lock") < main.index("deploy_commit")


def test_persistent_ownership_migration_is_one_time_not_every_poll() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    layout = _shell_function(script, "ensure_release_layout", "switch_current_release")

    assert 'ownership_marker="${PERSIST_DIR}/.ownership-uconn-v1"' in layout
    assert 'sudo install -o root -g root -m 600 /dev/null "${ownership_marker}"' in layout
    assert 'sudo chmod 444 "${ownership_marker}"' in layout
    assert "app_uid=%s" in layout
    assert "app_gid=%s" in layout
    assert '--chown="${APP_USER}:${APP_USER}"' in layout
    assert layout.count('-exec chown -h "${APP_USER}:${APP_USER}" {} +') == 1
    assert 'if systemctl is-active --quiet "${SERVICE_NAME}"; then' in layout
    assert "Persistent ownership migration requires zero active workflows." in layout
    assert '[[ -L "${legacy_dir}" ]]' in layout
    assert "Legacy persistent data contains symlinks or special files" in layout


def test_current_release_selector_is_fsynced_before_activation_can_complete() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    switch = _shell_function(script, "switch_current_release", "write_release_version")

    rename = 'sudo mv -Tf "${next_link}" "${CURRENT_RELEASE}"'
    fsync = "os.fsync(directory_fd)"
    assert rename in switch
    assert 'os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)' in switch
    assert fsync in switch
    assert switch.index(rename) < switch.index(fsync)


def test_release_marker_schema_two_detects_source_tampering(tmp_path: Path) -> None:
    release = tmp_path / "release"
    (release / "site" / "backend").mkdir(parents=True)
    (release / "site" / "frontend").mkdir(parents=True)
    (release / "site" / "dist").mkdir(parents=True)
    (release / ".venv").mkdir()
    requirements = release / "site" / "backend" / "requirements.txt"
    package_lock = release / "site" / "frontend" / "package-lock.json"
    source = release / "site" / "backend" / "source.py"
    requirements.write_text("example==1.0\n", encoding="utf-8")
    package_lock.write_text('{"lockfileVersion": 3}\n', encoding="utf-8")
    source.write_text("VALUE = 1\n", encoding="utf-8")
    commit_sha = "a" * 40

    result = subprocess.run(
        [
            "bash",
            "-c",
            """
source "$1"
before="$(release_marker_content "$2" "$3")"
printf '%s\n' "$before" > "$2/.release-ready"
printf 'VALUE = 2\n' > "$2/site/backend/source.py"
after="$(release_marker_content "$2" "$3")"
[[ "$before" == schema=2$'\n'* ]]
[[ "$before" == *$'\nimmutable_sha256='* ]]
[[ "$before" != "$after" ]]
[[ "$(<"$2/.release-ready")" != "$after" ]]
""",
            "bash",
            str(DEPLOY_SCRIPT),
            str(release),
            commit_sha,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_updater_is_installed_root_owned_and_never_sources_mutable_config() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    updater = _shell_function(script, "install_auto_update_units", "restart_backend_after_secret_change")

    assert 'UPDATER_INSTALL_DIR="/usr/local/libexec/uconn-isomorphic"' in script
    assert 'script_source="${active_release}/deploy/deploy.sh"' in updater
    assert 'atomic_install_root_file "${script_source}" "${UPDATER_SCRIPT}" 0755' in updater
    assert "${BASH_SOURCE[0]}" not in updater
    assert "ExecStart=${UPDATER_SCRIPT} --update" in updater
    assert ".deploy.env" not in script
    assert 'source "${DEPLOY_CONFIG_FILE}"' not in script
    assert "case \"${key}\" in" in script


def test_nginx_restores_cloudflare_client_ip_and_separates_stream_policy() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "set_real_ip_from 127.0.0.1;" in script
    assert "set_real_ip_from ::1;" in script
    assert "real_ip_header CF-Connecting-IP;" in script
    assert "real_ip_recursive on;" in script
    assert "=/api/upload \\$binary_remote_addr;" in script
    assert "=/api/openai/status \\$binary_remote_addr;" in script
    assert "zone=uconn_public_status:10m rate=30r/m" in script

    stream_start = script.index(r"location ~ ^/api/workflow/[^/]+/stream\$ {")
    regular_start = script.index("location /api/ {", stream_start)
    stream = script[stream_start:regular_start]
    regular = script[regular_start : script.index("\n}", regular_start)]
    assert "proxy_read_timeout 3600s;" in stream
    assert "proxy_buffering off;" in stream
    assert "proxy_read_timeout 300s;" in regular
    assert "proxy_buffering on;" in regular
    assert "proxy_request_buffering off;" in regular


def test_same_sha_deployment_health_checks_and_self_heals_inactive_service(tmp_path: Path) -> None:
    commit_sha = "b" * 40
    events = tmp_path / "events"
    result = subprocess.run(
        [
            "bash",
            "-c",
            """
source "$1"
CURRENT_SHA="$2"
EVENTS="$3"
DEPLOY_DIR="$4"
RELEASES_DIR="$DEPLOY_DIR/releases"
CURRENT_RELEASE="$DEPLOY_DIR/current"
mkdir -p "$RELEASES_DIR/$CURRENT_SHA"
ln -s "$RELEASES_DIR/$CURRENT_SHA" "$CURRENT_RELEASE"
current_release_path() { printf '%s/%s\n' "$RELEASES_DIR" "$CURRENT_SHA"; }
current_release_commit() { printf '%s\n' "$CURRENT_SHA"; }
release_is_complete() { return 0; }
systemctl() {
  if [[ "$1" == show && "$3" == --property=ActiveState ]]; then
    printf 'inactive\n'
    return 0
  fi
  return 3
}
activate_backend_service() { printf 'activate:%s\n' "$1" >> "$EVENTS"; return 0; }
release_health_is_valid() { printf 'health:%s\n' "$1" >> "$EVENTS"; return 0; }
write_deployment_status() { printf 'status:%s:%s\n' "$1" "$2" >> "$EVENTS"; }
deploy_commit "$CURRENT_SHA" nobody true
""",
            "bash",
            str(DEPLOY_SCRIPT),
            commit_sha,
            str(events),
            str(tmp_path / "deploy"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert events.read_text(encoding="utf-8").splitlines() == [
        "activate:true",
        f"health:{commit_sha}",
        "status:success:self_healed",
    ]


def test_inactive_backend_reports_zero_without_sending_access_token() -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            """
source "$1"
systemctl() {
  case "$3" in
    --property=LoadState) printf 'loaded\n' ;;
    --property=ActiveState) printf 'inactive\n' ;;
    *) return 3 ;;
  esac
}
sudo() { printf 'unexpected sudo call\n' >&2; return 99; }
[[ "$(active_workflow_count)" == "0" ]]
""",
            "bash",
            str(DEPLOY_SCRIPT),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "unexpected" not in result.stderr


def test_candidate_activation_rechecks_workflows_and_clears_maintenance(tmp_path: Path) -> None:
    commit_sha = "c" * 40
    events = tmp_path / "events"
    counter = tmp_path / "counter"
    counter.write_text("0", encoding="utf-8")
    candidate = tmp_path / commit_sha
    result = subprocess.run(
        [
            "bash",
            "-c",
            """
source "$1"
EVENTS="$3"
COUNT_FILE="$4"
CANDIDATE="$5"
current_release_path() { printf '/tmp/releases/old\n'; }
current_release_commit() { printf 'old\n'; }
active_workflow_count() {
  count="$(<"$COUNT_FILE")"
  printf '%s' "$((count + 1))" > "$COUNT_FILE"
  if [[ "$count" -eq 0 ]]; then printf '0\n'; else printf '1\n'; fi
}
build_candidate_release() { mkdir -p "$CANDIDATE"; printf '%s\n' "$CANDIDATE"; }
enter_deployment_maintenance() { printf 'enter\n' >> "$EVENTS"; }
leave_deployment_maintenance() { printf 'leave\n' >> "$EVENTS"; }
sleep() { :; }
switch_current_release() { printf 'switch\n' >> "$EVENTS"; }
write_deployment_status() { printf 'status:%s:%s\n' "$1" "$2" >> "$EVENTS"; }
deploy_commit "$2" nobody true
""",
            "bash",
            str(DEPLOY_SCRIPT),
            commit_sha,
            str(events),
            str(counter),
            str(candidate),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    event_lines = events.read_text(encoding="utf-8").splitlines()
    assert event_lines == [
        "status:building:candidate",
        "enter",
        "leave",
        "status:deferred:candidate_ready",
    ]
    assert "switch" not in event_lines


def test_manual_rollback_verifies_target_and_restored_release_health() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    rollback = _shell_function(script, "manual_rollback", "main")

    assert 'install_backend_service && release_health_is_valid "${target_sha}"' in rollback
    assert 'install_backend_service && release_health_is_valid "${previous_sha}"' in rollback
    assert '"rolled_back" "manual_rollback_failed"' in rollback
    assert '"failed" "manual_rollback_failed"' in rollback
    assert (
        'persist_activation_transaction "${previous}" "${previous_sha}" '
        '"${target}" "${target_sha}" "false"'
    ) in _compact_shell(rollback)
    assert 'install -o root -g root -m 600 /dev/null "${ACTIVATION_RECOVERY_ALLOW_FILE}"' in rollback
    assert rollback.count("PENDING_ACTIVATION=0") >= 2


@pytest.mark.parametrize(
    ("health_result", "expected_returncode", "expected_state"),
    [
        (0, 0, "success:manual_rollback"),
        (1, 1, "failed:manual_rollback_unhealthy"),
    ],
)
def test_manual_rollback_already_selected_requires_exact_service_health(
    tmp_path: Path,
    health_result: int,
    expected_returncode: int,
    expected_state: str,
) -> None:
    target_sha = "7" * 40
    releases = tmp_path / "releases"
    target = releases / target_sha
    target.mkdir(parents=True)
    current = tmp_path / "current"
    current.symlink_to(target)
    events = tmp_path / "events"

    result = subprocess.run(
        [
            "bash",
            "-c",
            r"""
source "$1"
TARGET_SHA="$2"
RELEASES_DIR="$3"
CURRENT_RELEASE="$4"
EVENTS="$5"
HEALTH_RESULT="$6"
SERVICE_NAME="test-service"
INTEGRITY_REPAIR_ACTIVE=0
require_linux() { :; }
require_repo() { :; }
validate_deploy_settings() { :; }
require_sudo_access() { :; }
acquire_deployment_lock() { :; }
ensure_release_layout() { :; }
recover_pending_activation() { :; }
release_is_verified_direct() { printf 'integrity\n' >> "$EVENTS"; }
current_release_path() { printf '%s\n' "$RELEASES_DIR/$TARGET_SHA"; }
current_release_commit() { printf '%s\n' "$TARGET_SHA"; }
selected_release_is_verified() { printf 'selected\n' >> "$EVENTS"; }
pin_manual_rollback_updates() { printf 'pin\n' >> "$EVENTS"; }
verify_systemd_unit_install() { printf 'unit-install\n' >> "$EVENTS"; }
verify_systemd_unit_state() { printf 'unit-state\n' >> "$EVENTS"; }
release_health_is_valid() {
  printf 'commit-health\n' >> "$EVENTS"
  return "$HEALTH_RESULT"
}
write_deployment_status() { printf '%s:%s\n' "$1" "$2" >> "$EVENTS"; }
info() { :; }
err() { :; }
manual_rollback "$TARGET_SHA"
""",
            "bash",
            str(DEPLOY_SCRIPT),
            target_sha,
            str(releases),
            str(current),
            str(events),
            str(health_result),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == expected_returncode, result.stderr
    event_lines = events.read_text(encoding="utf-8").splitlines()
    assert "pin" in event_lines
    assert "unit-install" in event_lines
    assert "unit-state" in event_lines
    assert "commit-health" in event_lines
    assert expected_state in event_lines


def test_auto_update_timer_is_jittered_and_builds_without_root_package_managers() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    compact = _compact_shell(script)

    assert "OnUnitInactiveSec=30min" in script
    assert "RandomizedDelaySec=5min" in script
    assert "AccuracySec=1min" in script
    assert (
        'sudo -H -u "${BUILD_USER}" env HOME="${release_dir}/.build-home" '
        '"${release_dir}/.venv/bin/pip" install'
    ) in compact
    assert (
        'sudo -H -u "${BUILD_USER}" env HOME="${release_dir}/.build-home" '
        'npm -C "${release_dir}/site/frontend" ci'
    ) in compact
    assert 'sudo "${release_dir}/.venv/bin/pip"' not in script
    assert 'sudo npm -C "${release_dir}/site/frontend"' not in script
    assert "ExecStopPost=${UPDATER_SCRIPT} --recover-maintenance" in script
    assert "--require-hashes" in script
    assert "stop_build_user_processes" in script
    assert "application_source_digest" in script


def test_activation_transaction_is_durable_and_start_is_tightly_allowed(tmp_path: Path) -> None:
    old_sha = "a" * 40
    new_sha = "b" * 40
    events = tmp_path / "events"
    result = subprocess.run(
        [
            "bash",
            "-c",
            r"""
source "$1"
EVENTS="$2"
CANDIDATE_ROOT="$3"
OLD_SHA="$4"
NEW_SHA="$5"
DEPLOY_DIR="$CANDIDATE_ROOT/deploy"
RELEASES_DIR="$DEPLOY_DIR/releases"
CURRENT_RELEASE="$DEPLOY_DIR/current"
CANDIDATE="$RELEASES_DIR/$NEW_SHA"
mkdir -p "$RELEASES_DIR/$OLD_SHA"
ln -s "$RELEASES_DIR/$OLD_SHA" "$CURRENT_RELEASE"
ALLOW=0
current_release_path() { printf '%s/%s\n' "$RELEASES_DIR" "$OLD_SHA"; }
current_release_commit() { printf '%s\n' "$OLD_SHA"; }
release_is_complete() {
  printf 'integrity:%s\n' "$2" >> "$EVENTS"
  return 0
}
active_workflow_count() { printf '0\n'; }
build_candidate_release() { mkdir -p "$CANDIDATE"; printf '%s\n' "$CANDIDATE"; }
enter_deployment_maintenance() { printf 'enter\n' >> "$EVENTS"; }
leave_deployment_maintenance() { printf 'leave\n' >> "$EVENTS"; }
sleep() { :; }
persist_activation_transaction() { printf 'persist:%s:%s\n' "$2" "$4" >> "$EVENTS"; }
switch_current_release() { printf 'switch:%s\n' "$1" >> "$EVENTS"; }
sudo() {
  if [[ "$1" == "install" && "${*: -1}" == "$ACTIVATION_RECOVERY_ALLOW_FILE" ]]; then
    ALLOW=1
    printf 'allow\n' >> "$EVENTS"
    return 0
  fi
  if [[ "$1" == "rm" && "${*: -1}" == "$ACTIVATION_RECOVERY_ALLOW_FILE" ]]; then
    ALLOW=0
    printf 'deny\n' >> "$EVENTS"
    return 0
  fi
  command "$@"
}
activate_backend_service() {
  [[ "$ALLOW" -eq 1 ]]
  printf 'activate\n' >> "$EVENTS"
}
release_health_is_valid() {
  [[ "$ALLOW" -eq 1 ]]
  printf 'health:%s\n' "$1" >> "$EVENTS"
}
clear_activation_transaction() { printf 'clear\n' >> "$EVENTS"; }
prune_old_releases() { :; }
write_deployment_status() { printf 'status:%s:%s\n' "$1" "$2" >> "$EVENTS"; }
deploy_commit "$NEW_SHA" nobody true
""",
            "bash",
            str(DEPLOY_SCRIPT),
            str(events),
            str(tmp_path),
            old_sha,
            new_sha,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    event_lines = events.read_text(encoding="utf-8").splitlines()
    assert event_lines.index(f"integrity:{old_sha}") < event_lines.index(
        "status:building:candidate"
    )
    assert event_lines.index(f"integrity:{new_sha}") < event_lines.index(
        f"persist:{old_sha}:{new_sha}"
    )
    assert event_lines.index(f"persist:{old_sha}:{new_sha}") < event_lines.index(
        f"switch:{tmp_path / 'deploy' / 'releases' / new_sha}"
    )
    assert event_lines.index("allow") < event_lines.index("activate")
    assert event_lines.index(f"health:{new_sha}") < event_lines.index("deny")
    assert event_lines.index("deny") < event_lines.index("clear") < event_lines.index("leave")


def test_recovery_restores_valid_previous_even_when_candidate_is_missing(tmp_path: Path) -> None:
    old_sha = "c" * 40
    new_sha = "d" * 40
    releases = tmp_path / "releases"
    previous = releases / old_sha
    previous.mkdir(parents=True)
    candidate = releases / new_sha
    events = tmp_path / "events"
    state = tmp_path / "pending.json"
    state.write_text("{}\n", encoding="utf-8")
    result = subprocess.run(
        [
            "bash",
            "-c",
            r"""
source "$1"
RELEASES_DIR="$2"
ACTIVATION_STATE_FILE="$3"
EVENTS="$4"
PREVIOUS="$5"
PREVIOUS_SHA="$6"
CANDIDATE="$7"
CANDIDATE_SHA="$8"
read_activation_transaction() {
  printf '%s\n%s\n%s\n%s\ntrue\n' \
    "$PREVIOUS" "$PREVIOUS_SHA" "$CANDIDATE" "$CANDIDATE_SHA"
}
acquire_activity_lock_exclusive() { :; }
release_is_complete() {
  printf 'integrity:%s\n' "$1" >> "$EVENTS"
  [[ "$1" == "$PREVIOUS" && "$2" == "$PREVIOUS_SHA" ]]
}
switch_current_release() { printf 'switch:%s\n' "$1" >> "$EVENTS"; }
activate_backend_service() { printf 'activate\n' >> "$EVENTS"; }
release_health_is_valid() {
  printf 'health:%s\n' "$1" >> "$EVENTS"
  [[ "$1" == "$PREVIOUS_SHA" ]]
}
clear_activation_transaction() { printf 'clear\n' >> "$EVENTS"; }
leave_deployment_maintenance() { printf 'leave\n' >> "$EVENTS"; MAINTENANCE_ACTIVE=0; }
sudo() {
  if [[ "$1" == "test" ]]; then command "$@"; return; fi
  return 0
}
recover_pending_activation
""",
            "bash",
            str(DEPLOY_SCRIPT),
            str(releases),
            str(state),
            str(events),
            str(previous),
            old_sha,
            str(candidate),
            new_sha,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    event_lines = events.read_text(encoding="utf-8").splitlines()
    assert f"integrity:{candidate}" not in event_lines
    assert event_lines == [
        f"integrity:{previous}",
        f"switch:{previous}",
        "activate",
        f"health:{old_sha}",
        "clear",
        "leave",
    ]


def test_generated_production_contract_pins_keys_limits_cost_and_bounds_restarts() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'NODESOURCE_KEY_FINGERPRINT="6F71F525282841EEDAF851B42F59B5F99B1BE0B4"' in script
    assert 'CLOUDFLARE_KEY_FINGERPRINT="CC94B39C77AE7342A68B89628A682D308D4E5E73"' in script
    assert "verify_gpg_primary_fingerprint" in script
    assert r"~^/api/download-zip/ \$binary_remote_addr;" in script
    assert "proxy_request_buffering off;" in script
    assert 'add_header Content-Security-Policy "default-src' in script
    assert "StartLimitIntervalSec=5min" in script
    assert "StartLimitBurst=3" in script
    assert "StartLimitIntervalSec=10min" in script
    assert "ExecCondition=/bin/sh -c" in script
    assert "--configure-donation" in script
    assert "--configure-runtime-policy" in script


def test_different_sha_integrity_transaction_precedes_quarantine(tmp_path: Path) -> None:
    old_sha = "1" * 40
    new_sha = "2" * 40
    deploy_dir = tmp_path / "deploy"
    releases = deploy_dir / "releases"
    previous = releases / old_sha
    previous.mkdir(parents=True)
    current = deploy_dir / "current"
    current.symlink_to(previous)
    events = tmp_path / "events"

    result = subprocess.run(
        [
            "bash",
            "-c",
            r"""
source "$1"
DEPLOY_DIR="$2"
RELEASES_DIR="$DEPLOY_DIR/releases"
CURRENT_RELEASE="$DEPLOY_DIR/current"
EVENTS="$3"
OLD_SHA="$4"
NEW_SHA="$5"
current_release_path() { printf '%s/%s\n' "$RELEASES_DIR" "$OLD_SHA"; }
current_release_commit() { printf '%s\n' "$OLD_SHA"; }
release_is_complete() {
  printf 'integrity:%s:%s\n' "$1" "$2" >> "$EVENTS"
  return 1
}
active_workflow_count() { printf '0\n'; }
enter_deployment_maintenance() { printf 'maintenance\n' >> "$EVENTS"; }
sleep() { :; }
persist_activation_transaction() {
  printf 'persist:%s:%s:%s:%s\n' "$1" "$2" "$3" "$4" >> "$EVENTS"
}
persist_integrity_repair_state() { printf 'integrity-state\n' >> "$EVENTS"; }
stop_systemd_unit_if_present() { printf 'stop:%s\n' "$1" >> "$EVENTS"; }
atomic_remove_root_path() {
  printf 'unlink:%s\n' "$1" >> "$EVENTS"
  command unlink "$1"
}
fsync_directory() { printf 'fsync:%s\n' "$1" >> "$EVENTS"; }
sudo() {
  if [[ "$1" == mv ]]; then printf 'move:%s:%s\n' "${3}" "${4}" >> "$EVENTS"; fi
  command "$@"
}
build_candidate_release() {
  printf 'build\n' >> "$EVENTS"
  return 1
}
write_deployment_status() { printf 'status:%s:%s\n' "$1" "$2" >> "$EVENTS"; }
deploy_commit "$NEW_SHA" nobody true
""",
            "bash",
            str(DEPLOY_SCRIPT),
            str(deploy_dir),
            str(events),
            old_sha,
            new_sha,
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    lines = events.read_text(encoding="utf-8").splitlines()
    integrity = next(index for index, line in enumerate(lines) if line.startswith("integrity:"))
    persisted = next(index for index, line in enumerate(lines) if line.startswith("persist:"))
    integrity_state = lines.index("integrity-state")
    moved = next(index for index, line in enumerate(lines) if line.startswith("move:"))
    built = lines.index("build")
    assert integrity < persisted < integrity_state < moved < built
    assert any(line == f"fsync:{releases}" for line in lines)
    assert not current.exists()
    assert list(releases.glob(f".invalid-{old_sha}-*"))


def test_invalid_current_selector_fails_before_build_or_switch(tmp_path: Path) -> None:
    new_sha = "3" * 40
    deploy_dir = tmp_path / "deploy"
    releases = deploy_dir / "releases"
    outside = tmp_path / "outside"
    releases.mkdir(parents=True)
    outside.mkdir()
    current = deploy_dir / "current"
    current.symlink_to(outside)
    events = tmp_path / "events"

    result = subprocess.run(
        [
            "bash",
            "-c",
            r"""
source "$1"
DEPLOY_DIR="$2"
RELEASES_DIR="$DEPLOY_DIR/releases"
CURRENT_RELEASE="$DEPLOY_DIR/current"
EVENTS="$3"
NEW_SHA="$4"
OUTSIDE="$5"
current_release_path() { printf '%s\n' "$OUTSIDE"; }
current_release_commit() { printf 'outside\n'; }
enter_deployment_maintenance() { printf 'maintenance\n' >> "$EVENTS"; }
stop_systemd_unit_if_present() { printf 'stop\n' >> "$EVENTS"; }
write_deployment_status() { printf 'status:%s:%s\n' "$1" "$2" >> "$EVENTS"; }
build_candidate_release() { printf 'unexpected-build\n' >> "$EVENTS"; }
switch_current_release() { printf 'unexpected-switch\n' >> "$EVENTS"; }
deploy_commit "$NEW_SHA" nobody true
""",
            "bash",
            str(DEPLOY_SCRIPT),
            str(deploy_dir),
            str(events),
            new_sha,
            str(outside),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert events.read_text(encoding="utf-8").splitlines() == [
        "maintenance",
        "stop",
        "status:failed:integrity_selector",
    ]


def test_recover_maintenance_propagates_recovery_failure(tmp_path: Path) -> None:
    state = tmp_path / "pending.json"
    state.write_text("{}\n", encoding="utf-8")
    maintenance = tmp_path / "maintenance"
    maintenance.write_text("keep\n", encoding="utf-8")

    result = subprocess.run(
        [
            "bash",
            "-c",
            r"""
source "$1"
ACTIVATION_STATE_FILE="$2"
MAINTENANCE_FILE="$3"
require_linux() { :; }
validate_deploy_settings() { :; }
require_sudo_access() { :; }
acquire_deployment_lock() { return 0; }
recover_pending_activation() { return 1; }
sudo() { command "$@"; }
recover_maintenance_command
""",
            "bash",
            str(DEPLOY_SCRIPT),
            str(state),
            str(maintenance),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert maintenance.exists()
    assert "cleared" not in result.stdout


def test_recover_maintenance_rejects_absent_current_release(tmp_path: Path) -> None:
    events = tmp_path / "events"
    result = subprocess.run(
        [
            "bash",
            "-c",
            r"""
source "$1"
DEPLOY_DIR="$2"
CURRENT_RELEASE="$DEPLOY_DIR/current"
ACTIVATION_STATE_FILE="$DEPLOY_DIR/no-activation"
INTEGRITY_STATE_FILE="$DEPLOY_DIR/no-integrity"
RESTORE_STATE_FILE="$DEPLOY_DIR/no-restore"
EVENTS="$3"
require_linux() { :; }
validate_deploy_settings() { :; }
require_sudo_access() { :; }
acquire_deployment_lock() { return 0; }
current_release_path() { :; }
current_release_commit() { :; }
stop_systemd_unit_if_present() { printf 'stop:%s\n' "$1" >> "$EVENTS"; }
sudo() { command "$@"; }
recover_maintenance_command
""",
            "bash",
            str(DEPLOY_SCRIPT),
            str(tmp_path / "deploy"),
            str(events),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert events.read_text(encoding="utf-8").splitlines() == [
        "stop:uconn-isomorphic.service"
    ]


def test_manual_rollback_persists_update_hold_before_switch(tmp_path: Path) -> None:
    old_sha = "4" * 40
    target_sha = "5" * 40
    deploy_dir = tmp_path / "deploy"
    releases = deploy_dir / "releases"
    (releases / old_sha).mkdir(parents=True)
    (releases / target_sha).mkdir()
    (deploy_dir / "current").symlink_to(releases / old_sha)
    events = tmp_path / "events"

    result = subprocess.run(
        [
            "bash",
            "-c",
            r"""
source "$1"
DEPLOY_DIR="$2"
RELEASES_DIR="$DEPLOY_DIR/releases"
CURRENT_RELEASE="$DEPLOY_DIR/current"
EVENTS="$3"
OLD_SHA="$4"
TARGET_SHA="$5"
require_linux() { :; }
require_repo() { :; }
validate_deploy_settings() { :; }
require_sudo_access() { :; }
acquire_deployment_lock() { return 0; }
ensure_release_layout() { :; }
recover_pending_activation() { :; }
current_release_path() { printf '%s/%s\n' "$RELEASES_DIR" "$OLD_SHA"; }
current_release_commit() { printf '%s\n' "$OLD_SHA"; }
release_is_verified_direct() { printf 'verify:%s\n' "$2" >> "$EVENTS"; return 0; }
selected_release_is_verified() { return 0; }
enter_deployment_maintenance() { printf 'maintenance\n' >> "$EVENTS"; }
leave_deployment_maintenance() { printf 'leave\n' >> "$EVENTS"; }
sleep() { :; }
active_workflow_count() { printf '0\n'; }
disable_systemd_unit_if_present() { printf 'disable:%s\n' "$1" >> "$EVENTS"; }
save_deploy_config() { printf 'config:%s\n' "$AUTO_UPDATE" >> "$EVENTS"; }
persist_manual_update_hold() { printf 'hold:%s\n' "$1" >> "$EVENTS"; }
write_deployment_status() { printf 'status:%s:%s\n' "$1" "$2" >> "$EVENTS"; }
persist_activation_transaction() { printf 'activation-state\n' >> "$EVENTS"; }
switch_current_release() { printf 'switch:%s\n' "$1" >> "$EVENTS"; }
install_backend_service() { printf 'backend\n' >> "$EVENTS"; }
release_health_is_valid() { printf 'health:%s\n' "$1" >> "$EVENTS"; }
clear_activation_transaction() { printf 'clear\n' >> "$EVENTS"; }
sudo() { return 0; }
manual_rollback "$TARGET_SHA"
""",
            "bash",
            str(DEPLOY_SCRIPT),
            str(deploy_dir),
            str(events),
            old_sha,
            target_sha,
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    lines = events.read_text(encoding="utf-8").splitlines()
    assert lines.index("disable:uconn-isomorphic-update.timer") < lines.index("config:0")
    assert lines.index("config:0") < lines.index(f"hold:{target_sha}")
    assert lines.index(f"hold:{target_sha}") < lines.index("activation-state")
    switch = f"switch:{releases / target_sha}"
    assert lines.index("activation-state") < lines.index(switch)


def test_manual_update_hold_blocks_fetch_and_corruption_fails_closed(tmp_path: Path) -> None:
    hold = tmp_path / "manual-hold.json"
    hold.write_text("{}\n", encoding="utf-8")
    held_sha = "6" * 40

    for valid, expected in ((True, 0), (False, 1)):
        result = subprocess.run(
            [
                "bash",
                "-c",
                r"""
source "$1"
MANUAL_UPDATE_HOLD_FILE="$2"
HELD_SHA="$3"
VALID="$4"
require_linux() { :; }
require_repo() { :; }
validate_deploy_settings() { :; }
run_pi_preflight() { :; }
acquire_deployment_lock() { return 0; }
ensure_activity_lock() { :; }
recover_pending_activation() { :; }
read_manual_update_hold() {
  [[ "$VALID" == 1 ]] || return 1
  printf '%s\n' "$HELD_SHA"
}
current_release_commit() { printf '%s\n' "$HELD_SHA"; }
write_deployment_status() { printf '%s:%s\n' "$1" "$2"; }
ensure_release_layout() { printf 'unexpected ensure\n' >&2; return 91; }
sudo() { command "$@"; }
run_auto_update
""",
                "bash",
                str(DEPLOY_SCRIPT),
                str(hold),
                held_sha,
                "1" if valid else "0",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode == expected, result.stderr
        assert "unexpected" not in result.stderr
        expected_status = "deferred:manual_update_hold" if valid else "failed:manual_update_hold"
        assert expected_status in result.stdout


def test_manual_rollback_success_does_not_clear_failed_commit_contract() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    status = _shell_function(script, "write_deployment_status", "current_release_path")

    assert 'values["stage"] != "manual_rollback"' in status
    assert 'values["status"] == "success"' in status


def test_auto_update_uses_secure_advertised_sha_not_user_writable_ref() -> None:
    updater = _shell_function(
        DEPLOY_SCRIPT.read_text(encoding="utf-8"),
        "run_auto_update",
        "install_auto_update_units",
    )
    compact = _compact_shell(updater)

    assert "ls-remote --exit-code --refs" in compact
    assert 'fields[1] != "refs/heads/main"' in updater
    assert "--no-write-fetch-head" in compact
    assert '"${UPDATE_REMOTE_URL}" "${remote_sha}"' in compact
    assert 'cat-file -e "${remote_sha}^{commit}"' in compact
    assert "refs/remotes/uconn-auto/main" not in updater
    assert "rev-parse refs/remotes" not in updater
    assert "http.sslVerify=true" in updater
    assert "http.proxy=" in updater
    assert "HTTPS_PROXY=" in updater
    assert "CURL_CA_BUNDLE" in updater


def test_auto_update_rejects_unsafe_local_transport_config_before_network(
    tmp_path: Path,
) -> None:
    events = tmp_path / "events"
    repository = tmp_path / "repository"
    (repository / ".git").mkdir(parents=True)
    hold = tmp_path / "manual-hold"
    result = subprocess.run(
        [
            "bash",
            "-c",
            r"""
source "$1"
EVENTS="$2"
REPO_DIR="$3"
UPDATE_REMOTE_URL="https://github.com/example/project.git"
AUTO_UPDATE=1
MANUAL_UPDATE_HOLD_FILE="$4"
INTEGRITY_REPAIR_ACTIVE=0
require_linux() { :; }
require_repo() { :; }
validate_deploy_settings() { :; }
run_pi_preflight() { :; }
acquire_deployment_lock() { :; }
ensure_activity_lock() { :; }
recover_pending_activation() { :; }
ensure_release_layout() { :; }
clear_stale_deployment_maintenance() { :; }
validate_runtime_secret_installation() { :; }
runtime_env_has_value() { :; }
current_release_commit() { :; }
is_valid_update_remote_url() { :; }
stat() { printf 'professor\n'; }
id() { printf '1000\n'; }
write_deployment_status() { printf 'status:%s:%s\n' "$1" "$2" >> "$EVENTS"; }
sudo() {
  if [[ "$1" == "test" ]]; then
    command "$@"
    return
  fi
  if [[ "$*" == *" config --local --get-regexp "* ]]; then
    printf 'http.sslverify false\n'
    return 0
  fi
  if [[ "$*" == *" ls-remote "* || "$*" == *" fetch "* ]]; then
    printf 'network\n' >> "$EVENTS"
  fi
  return 1
}
run_auto_update
""",
            "bash",
            str(DEPLOY_SCRIPT),
            str(events),
            str(repository),
            str(hold),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    event_lines = events.read_text(encoding="utf-8").splitlines()
    assert "status:failed:trust" in event_lines
    assert "network" not in event_lines


def test_systemd_state_checks_are_exact_and_masks_must_be_inactive() -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            r"""
source "$1"
LOAD=loaded
ACTIVE=active
UNIT=enabled
systemctl() {
  [[ "$1" == show ]] || return 90
  case "$3" in
    --property=LoadState) printf '%s\n' "$LOAD" ;;
    --property=ActiveState) printf '%s\n' "$ACTIVE" ;;
    --property=UnitFileState) printf '%s\n' "$UNIT" ;;
    *) return 91 ;;
  esac
}
verify_systemd_unit_state example.service loaded active enabled
ACTIVE=inactive
if verify_systemd_unit_state example.service loaded active enabled; then exit 92; fi
LOAD=masked
ACTIVE=inactive
UNIT=masked
disable_systemd_unit_if_present example.service
ACTIVE=active
if disable_systemd_unit_if_present example.service; then exit 93; fi
""",
            "bash",
            str(DEPLOY_SCRIPT),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_systemd_installers_use_atomic_files_and_exact_state_verification() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    backend = _shell_function(script, "install_backend_service", "restart_installed_backend_service")
    cloudflare = _shell_function(
        script,
        "install_cloudflared_service",
        "verify_public_https_redirect",
    )
    updater = _shell_function(script, "install_auto_update_units", "restart_backend_after_secret_change")
    nginx = _shell_function(script, "install_nginx_config", "restore_nginx_install_state")

    for body in (backend, cloudflare, updater):
        assert 'sudo tee "${service_file}"' not in body
        assert "atomic_install_root_file" in body
        assert "verify_systemd_unit_install" in body
        assert "verify_systemd_unit_state" in body
        assert "if ! sudo systemctl daemon-reload" in body
    assert 'sudo tee "${timer_file}"' not in updater
    assert 'verify_systemd_unit_state "nginx.service" "loaded" "active" "enabled"' in _compact_shell(
        nginx
    )


def test_nginx_enabled_site_targets_must_be_contained_regular_files(tmp_path: Path) -> None:
    available = tmp_path / "sites-available"
    enabled = tmp_path / "sites-enabled"
    available.mkdir()
    enabled.mkdir()
    (available / "safe").write_text("server {}\n", encoding="utf-8")
    (available / "directory").mkdir()
    outside = tmp_path / "outside"
    outside.write_text("server {}\n", encoding="utf-8")

    result = subprocess.run(
        [
            "bash",
            "-c",
            r"""
source "$1"
NGINX_AVAILABLE_DIR="$2"
NGINX_ENABLED_DIR="$3"
ln -s "$NGINX_AVAILABLE_DIR/safe" "$NGINX_ENABLED_DIR/safe"
nginx_enabled_site_target_is_safe "$NGINX_ENABLED_DIR/safe"
ln -s "$4" "$NGINX_ENABLED_DIR/outside"
if nginx_enabled_site_target_is_safe "$NGINX_ENABLED_DIR/outside"; then exit 91; fi
ln -s "$NGINX_AVAILABLE_DIR/missing" "$NGINX_ENABLED_DIR/dangling"
if nginx_enabled_site_target_is_safe "$NGINX_ENABLED_DIR/dangling"; then exit 92; fi
ln -s "$NGINX_AVAILABLE_DIR/directory" "$NGINX_ENABLED_DIR/directory"
if nginx_enabled_site_target_is_safe "$NGINX_ENABLED_DIR/directory"; then exit 93; fi
ln -s "$NGINX_AVAILABLE_DIR/safe" "$NGINX_AVAILABLE_DIR/chain"
ln -s "$NGINX_AVAILABLE_DIR/chain" "$NGINX_ENABLED_DIR/chain"
if nginx_enabled_site_target_is_safe "$NGINX_ENABLED_DIR/chain"; then exit 94; fi
printf 'not a link\n' > "$NGINX_ENABLED_DIR/regular"
if nginx_enabled_site_target_is_safe "$NGINX_ENABLED_DIR/regular"; then exit 95; fi
""",
            "bash",
            str(DEPLOY_SCRIPT),
            str(available),
            str(enabled),
            str(outside),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_nginx_install_and_restore_are_atomic_and_containment_checked() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    nginx = _shell_function(script, "install_nginx_config", "restore_nginx_install_state")
    restore = _shell_function(script, "restore_nginx_install_state", "audit_nginx_enabled_sites")
    audit = _shell_function(script, "audit_nginx_enabled_sites", "verify_nginx_app_hosts")

    assert "atomic_install_root_file" in nginx
    assert "atomic_install_nginx_symlink" in nginx
    assert "atomic_remove_root_path" in nginx
    assert 'sudo tee "${NGINX_CONF}"' not in nginx
    assert "sudo ln -sf" not in nginx
    assert "atomic_install_root_file" in restore
    assert "atomic_install_nginx_symlink" in restore
    assert "atomic_remove_root_path" in restore
    assert "restore_nginx_systemd_state" in script
    assert "rollback_nginx_activation" in script
    assert "sudo cp --no-preserve=ownership,mode" in nginx
    assert "sudo cmp -s" in nginx
    assert "nginx_enabled_site_target_is_safe" in audit
    assert audit.index("nginx_enabled_site_target_is_safe") < audit.index(
        '[[ "${site}" == "${NGINX_LINK}" ]]'
    )


def test_nginx_host_verification_accepts_maintenance_503(tmp_path: Path) -> None:
    fake_curl = tmp_path / "curl"
    fake_curl.write_text("#!/bin/sh\nprintf '%s' \"${FAKE_STATUS:-503}\"\n", encoding="utf-8")
    fake_curl.chmod(0o755)
    maintenance = tmp_path / "maintenance"
    maintenance.write_text("schema=1\nreason=local_only\n", encoding="utf-8")

    result = subprocess.run(
        [
            "bash",
            "-c",
            r"""
source "$1"
CURL_BIN="$2"
MAINTENANCE_FILE="$3"
ACTIVATION_STATE_FILE="$4/no-activation"
RESTORE_STATE_FILE="$4/no-restore"
sudo() { command "$@"; }
FAKE_STATUS=503 verify_nginx_app_hosts "localhost 127.0.0.1"
if FAKE_STATUS=200 verify_nginx_app_hosts "localhost"; then exit 91; fi
""",
            "bash",
            str(DEPLOY_SCRIPT),
            str(fake_curl),
            str(maintenance),
            str(tmp_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_cloudflare_apt_repository_uses_supported_any_suite() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "https://pkg.cloudflare.com/cloudflared any main" in script
    assert "https://pkg.cloudflare.com/cloudflared stable main" not in script


def test_nginx_csp_allows_authenticated_blob_pdf_frames_only() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "frame-src 'self' blob:;" in script
    assert "object-src 'none';" in script


def test_systemd_install_verification_rejects_stale_daemon_state(tmp_path: Path) -> None:
    unit_file = tmp_path / "example.service"
    unit_file.write_text("[Service]\nExecStart=/bin/true\n", encoding="utf-8")

    result = subprocess.run(
        [
            "bash",
            "-c",
            r"""
source "$1"
UNIT_FILE="$2"
NEEDS_RELOAD=yes
systemctl() {
  [[ "$1" == show ]] || return 90
  case "$3" in
    --property=FragmentPath) printf '%s\n' "$UNIT_FILE" ;;
    --property=DropInPaths) printf '\n' ;;
    --property=LoadState) printf 'loaded\n' ;;
    --property=UnitFileState) printf 'enabled\n' ;;
    --property=NeedDaemonReload) printf '%s\n' "$NEEDS_RELOAD" ;;
    *) return 91 ;;
  esac
}
sudo() {
  if [[ "$1" == stat ]]; then printf 'root:root:644\n'; return 0; fi
  command "$@"
}
if verify_systemd_unit_install example.service "$UNIT_FILE" enabled; then exit 92; fi
NEEDS_RELOAD=no
verify_systemd_unit_install example.service "$UNIT_FILE" enabled
""",
            "bash",
            str(DEPLOY_SCRIPT),
            str(unit_file),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_integrity_repair_state_survives_repeated_failed_rebuilds(tmp_path: Path) -> None:
    integrity_state = tmp_path / "integrity.json"
    integrity_state.write_text("schema=1\nreason=release_integrity\n", encoding="utf-8")
    target_sha = "7" * 40

    for _ in range(2):
        result = subprocess.run(
            [
                "bash",
                "-c",
                r"""
source "$1"
DEPLOY_DIR="$2"
RELEASES_DIR="$DEPLOY_DIR/releases"
CURRENT_RELEASE="$DEPLOY_DIR/current"
ACTIVATION_STATE_FILE="$DEPLOY_DIR/no-activation"
INTEGRITY_STATE_FILE="$3"
TARGET_SHA="$4"
read_integrity_repair_state() { return 0; }
write_deployment_maintenance() { :; }
acquire_activity_lock_exclusive() { :; }
current_release_path() { :; }
current_release_commit() { :; }
active_workflow_count() { printf '0\n'; }
build_candidate_release() { return 1; }
write_deployment_status() { printf '%s:%s\n' "$1" "$2"; }
sudo() { command "$@"; }
recover_pending_activation
[[ "$INTEGRITY_REPAIR_ACTIVE" -eq 1 ]]
deploy_commit "$TARGET_SHA" nobody true
""",
                "bash",
                str(DEPLOY_SCRIPT),
                str(tmp_path / "deploy"),
                str(integrity_state),
                target_sha,
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode != 0
        assert "failed:integrity_rebuild" in result.stdout
        assert integrity_state.exists()

    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "if (-e ${INTEGRITY_STATE_FILE})" in script
    assert (
        "ExecCondition=/bin/sh -c '/usr/bin/test ! -e ${INTEGRITY_STATE_FILE}"
        in script
    )


def test_auto_update_reenable_keeps_hold_until_timer_verification() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    main = script[script.index("\nmain() {") :]
    enable_prompt = main[main.index('if confirm_default_no "Enable automatic updates') :]
    updater = _shell_function(script, "run_auto_update", "install_auto_update_units")
    units = _shell_function(script, "install_auto_update_units", "restart_backend_after_secret_change")

    assert enable_prompt.index("install_auto_update_units") < enable_prompt.index(
        "clear_manual_update_hold"
    )
    assert enable_prompt.index("clear_manual_update_hold") < enable_prompt.index(
        "AUTO_UPDATE=1"
    )
    assert '"${AUTO_UPDATE:-0}" != "1"' in updater
    assert "disable_systemd_unit_if_present" in units
    assert units.index('systemctl enable --now "${SERVICE_NAME}-update.timer"') < units.rindex(
        "disable_systemd_unit_if_present"
    )


def test_nginx_systemd_rollback_restores_enabled_and_active_state() -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            r"""
source "$1"
LOAD=loaded
ACTIVE=active
UNIT=enabled
systemctl() {
  if [[ "$1" == show ]]; then
    case "$3" in
      --property=LoadState) printf '%s\n' "$LOAD" ;;
      --property=ActiveState) printf '%s\n' "$ACTIVE" ;;
      --property=UnitFileState) printf '%s\n' "$UNIT" ;;
      *) return 91 ;;
    esac
    return
  fi
  case "$1" in
    enable) UNIT=enabled ;;
    disable) UNIT=disabled ;;
    restart) ACTIVE=active ;;
    stop) ACTIVE=inactive ;;
    reset-failed) : ;;
    *) return 92 ;;
  esac
}
sudo() { "$@"; }
restore_nginx_systemd_state loaded inactive disabled
[[ "$ACTIVE" == inactive && "$UNIT" == disabled ]]
restore_nginx_systemd_state loaded active enabled
[[ "$ACTIVE" == active && "$UNIT" == enabled ]]
""",
            "bash",
            str(DEPLOY_SCRIPT),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
