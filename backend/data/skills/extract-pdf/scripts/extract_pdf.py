#!/usr/bin/env python3
"""Extract text and metadata from a PDF into normalized JSON."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from pypdf import PdfReader
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency: pypdf."
    ) from exc

try:
    import pdfplumber
except ImportError:  # pragma: no cover
    pdfplumber = None


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_datetime(value: Any) -> str | None:
    """Normalize PDF metadata date values into ISO-8601 strings."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).isoformat()
        return value.isoformat()

    raw = str(value).strip()
    if not raw:
        return None

    # PDF dates often look like D:20240218153010Z or D:20240218153010-05'00'
    if raw.startswith("D:"):
        raw = raw[2:]
    m = re.match(r"^(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?", raw)
    if not m:
        return str(value)

    parts = [int(p) if p is not None else None for p in m.groups()]
    year = parts[0]
    month = parts[1] or 1
    day = parts[2] or 1
    hour = parts[3] or 0
    minute = parts[4] or 0
    second = parts[5] or 0

    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc).isoformat()
    except ValueError:
        return str(value)


def read_pdf(path: Path) -> dict[str, Any]:
    """Read a PDF file into the normalized extraction payload."""
    reader = PdfReader(str(path))
    raw_meta = reader.metadata or {}
    normalized_meta: dict[str, Any] = {}
    for key, value in raw_meta.items():
        clean_key = str(key).lstrip("/")
        normalized_meta[clean_key] = None if value is None else str(value)

    page_count = len(reader.pages)
    needs_ocr_pages: list[int] = []
    blocks: list[dict[str, Any]] = []
    text_parts: list[str] = []

    plumber_doc = None
    if pdfplumber is not None:
        try:
            plumber_doc = pdfplumber.open(str(path))
        except Exception:
            plumber_doc = None

    try:
        for idx, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if not text and plumber_doc is not None:
                try:
                    text = (plumber_doc.pages[idx - 1].extract_text() or "").strip()
                except Exception:
                    text = ""

            if not text:
                needs_ocr_pages.append(idx)

            blocks.append(
                {
                    "block_id": f"p{idx}b1",
                    "page": idx,
                    "kind": "paragraph",
                    "text": text,
                    "confidence": None,
                }
            )
            if text:
                text_parts.append(text)
    finally:
        if plumber_doc is not None:
            plumber_doc.close()

    return {
        "source": {
            "path": str(path),
            "type": "pdf",
            "filename": path.name,
            "sha256": sha256_file(path),
        },
        "metadata": {
            "title": raw_meta.get("/Title") if raw_meta else None,
            "author": raw_meta.get("/Author") if raw_meta else None,
            "created_at": normalize_datetime(raw_meta.get("/CreationDate") if raw_meta else None),
            "modified_at": normalize_datetime(raw_meta.get("/ModDate") if raw_meta else None),
            "page_count": page_count,
            "image": None,
            "extra": {
                "raw_pdf_metadata": normalized_meta,
                "needs_ocr_pages": needs_ocr_pages,
            },
        },
        "text": "\n\n".join(text_parts),
        "blocks": blocks,
    }


def parse_args() -> argparse.Namespace:
    """Parse the command line arguments for the PDF extractor."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Path to a PDF file")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSON path (default: outputs/extracted/<stem>.json)",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    return parser.parse_args()


def main() -> int:
    """Run the PDF extraction CLI and write the normalized JSON payload."""
    args = parse_args()
    input_path = args.input.expanduser().resolve()

    if not input_path.exists() or not input_path.is_file():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 2
    if input_path.suffix.lower() != ".pdf":
        print(f"Expected a .pdf file: {input_path}", file=sys.stderr)
        return 2

    output_path = args.output
    if output_path is None:
        output_path = Path("outputs/extracted") / f"{input_path.stem}.json"
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    payload = read_pdf(input_path)
    with output_path.open("w", encoding="utf-8") as f:
        if args.pretty:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        else:
            json.dump(payload, f, ensure_ascii=False)

    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
