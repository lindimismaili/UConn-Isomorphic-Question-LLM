"""Process bootstrap helpers shared by the CLI and the FastAPI server."""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: Path) -> None:
    """Populate os.environ from a `.env` file if present.

    Existing environment values win — this mirrors the `setdefault` semantics
    callers relied on before the helper was centralized.
    """
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
