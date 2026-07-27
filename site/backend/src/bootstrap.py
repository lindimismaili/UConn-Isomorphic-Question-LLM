"""Process bootstrap helpers shared by the CLI and the FastAPI server."""

from __future__ import annotations

import os
from pathlib import Path

PRIVATE_PROCESS_UMASK = 0o077


def configure_private_umask() -> None:
    """Keep newly created assessment data readable only by the app owner."""
    os.umask(PRIVATE_PROCESS_UMASK)


def _load_env_file(path: Path) -> None:
    """Load one env file without overriding the process environment."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('\'"'))


def load_dotenv(path: Path) -> None:
    """Populate ``os.environ`` from project-local environment files.

    When ``path`` is the project's ``.env``, an adjacent ignored ``.env.local``
    is loaded first. Precedence is therefore:

    1. values already present in the process environment;
    2. values in ``.env.local``;
    3. values in an optional ignored ``.env``.

    The helper never logs values and preserves the ``setdefault`` behavior
    callers relied on before environment loading was centralized.
    """
    if path.name == ".env":
        _load_env_file(path.with_name(".env.local"))
    _load_env_file(path)
