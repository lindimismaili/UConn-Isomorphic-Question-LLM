"""Environment-loading regression tests."""

from __future__ import annotations

import os
from unittest.mock import patch

from backend.src.bootstrap import (
    PRIVATE_PROCESS_UMASK,
    configure_private_umask,
    load_dotenv,
)


def test_private_umask_is_owner_only():
    with patch("backend.src.bootstrap.os.umask") as umask:
        configure_private_umask()
    umask.assert_called_once_with(PRIVATE_PROCESS_UMASK)
    assert PRIVATE_PROCESS_UMASK == 0o077


def test_project_local_env_precedence(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    local_path = tmp_path / ".env.local"
    env_path.write_text(
        "HANDOFF_BASE_ONLY=base\nHANDOFF_LOCAL_WINS=base\nHANDOFF_PROCESS_WINS=base\n",
        encoding="utf-8",
    )
    local_path.write_text(
        "HANDOFF_LOCAL_WINS=local\nHANDOFF_PROCESS_WINS=local\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("HANDOFF_BASE_ONLY", raising=False)
    monkeypatch.delenv("HANDOFF_LOCAL_WINS", raising=False)
    monkeypatch.setenv("HANDOFF_PROCESS_WINS", "process")

    load_dotenv(env_path)

    assert os.environ["HANDOFF_BASE_ONLY"] == "base"
    assert os.environ["HANDOFF_LOCAL_WINS"] == "local"
    assert os.environ["HANDOFF_PROCESS_WINS"] == "process"
