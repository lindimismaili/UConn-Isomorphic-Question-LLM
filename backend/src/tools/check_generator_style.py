"""Developer-only CLI for checking generator verbosity/style preservation."""

from __future__ import annotations

import json
from pathlib import Path

try:
    from .style_testing import generator_style_report_from_outputs
except ImportError:
    from style_testing import generator_style_report_from_outputs


def _resolve_outputs_dir(raw: str) -> Path:
    """Resolve an output-folder argument relative to backend/outputs when needed."""

    candidate = Path(raw).expanduser()
    if candidate.exists():
        return candidate.resolve()

    here = Path(__file__).resolve().parent
    backend_root = here.parent
    fallback = backend_root / "outputs" / raw
    if fallback.exists():
        return fallback.resolve()

    raise FileNotFoundError(f"Output folder not found: {raw}")


def main(argv: list[str] | None = None) -> int:
    """Run a local style report against one completed output folder."""

    args = list(argv or [])
    if not args:
        print("Usage: python -m backend.src.check_generator_style <output-folder>")
        return 2

    outputs_dir = _resolve_outputs_dir(args[0])
    report = generator_style_report_from_outputs(outputs_dir)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["issue_count"] == 0 else 1


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
