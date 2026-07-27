"""Suite-wide safety defaults: tests must never discover real provider secrets."""

from __future__ import annotations

import os

# Set an explicit empty value before backend modules load their project dotenv
# files. Individual offline tests inject fakes or patch this value as needed.
os.environ["OPENAI_API_KEY"] = ""
os.environ["OPENAI_AGENTS_DISABLE_TRACING"] = "1"
