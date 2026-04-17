#!/usr/bin/env python3
"""Extract text and metadata from a DOCX file into normalized JSON."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from docx import Document
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency: python-docx."
    ) from exc


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_datetime(value: Any) -> str | None:
    """Normalize DOCX metadata date values into ISO-8601 strings."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).isoformat()
        return value.isoformat()
    text = str(value).strip()
    return text or None


def append_block(blocks: list[dict[str, Any]], kind: str, text: str, block_idx: int) -> int:
    """Append a non-empty content block and return the next block index."""
    clean = text.strip()
    if not clean:
        return block_idx
    blocks.append(
        {
            "block_id": f"b{block_idx}",
            "page": None,
            "kind": kind,
            "text": clean,
            "confidence": None,
        }
    )
    return block_idx + 1


def read_docx(path: Path) -> dict[str, Any]:
    """Read a DOCX file into the normalized extraction payload."""
    doc = Document(str(path))
    blocks: list[dict[str, Any]] = []
    text_parts: list[str] = []
    block_idx = 1

    for para in doc.paragraphs:
        block_idx = append_block(blocks, "paragraph", para.text, block_idx)

    for table in doc.tables:
        for row in table.rows:
            row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
            block_idx = append_block(blocks, "table", row_text, block_idx)

    for section in doc.sections:
        for para in section.header.paragraphs:
            block_idx = append_block(blocks, "header", para.text, block_idx)
        for para in section.footer.paragraphs:
            block_idx = append_block(blocks, "footer", para.text, block_idx)

    text_parts = [b["text"] for b in blocks]

    core = doc.core_properties
    metadata_extra = {
        "subject": core.subject or None,
        "keywords": core.keywords or None,
        "category": core.category or None,
        "comments": core.comments or None,
        "last_modified_by": core.last_modified_by or None,
        "revision": core.revision,
    }

    return {
        "source": {
            "path": str(path),
            "type": "docx",
            "filename": path.name,
            "sha256": sha256_file(path),
        },
        "metadata": {
            "title": core.title or None,
            "author": core.author or None,
            "created_at": normalize_datetime(core.created),
            "modified_at": normalize_datetime(core.modified),
            "page_count": None,
            "image": None,
            "extra": metadata_extra,
        },
        "text": "\n\n".join(text_parts),
        "blocks": blocks,
    }


def parse_args() -> argparse.Namespace:
    """Parse the command line arguments for the DOCX extractor."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Path to a DOCX file")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSON path (default: outputs/extracted/<stem>.json)",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    return parser.parse_args()


def main() -> int:
    """Run the DOCX extraction CLI and write the normalized JSON payload."""
    args = parse_args()
    input_path = args.input.expanduser().resolve()

    if not input_path.exists() or not input_path.is_file():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 2
    if input_path.suffix.lower() != ".docx":
        print(f"Expected a .docx file: {input_path}", file=sys.stderr)
        return 2

    output_path = args.output
    if output_path is None:
        output_path = Path("outputs/extracted") / f"{input_path.stem}.json"
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    payload = read_docx(input_path)
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
