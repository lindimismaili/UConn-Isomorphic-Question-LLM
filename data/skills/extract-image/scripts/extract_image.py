#!/usr/bin/env python3
"""Extract OCR text and metadata from an image into normalized JSON."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

try:
    from PIL import ExifTags, Image
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Missing dependency: pillow. Install with `python -m pip install pillow pytesseract`.") from exc

try:
    import pytesseract
except ImportError:
    pytesseract = None

SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def exif_to_dict(image: Image.Image) -> dict[str, Any]:
    exif = image.getexif()
    if not exif:
        return {}
    data: dict[str, Any] = {}
    for tag_id, value in exif.items():
        key = ExifTags.TAGS.get(tag_id, str(tag_id))
        data[str(key)] = str(value)
    return data


def run_ocr(image: Image.Image) -> tuple[str, float | None, list[str]]:
    warnings: list[str] = []
    if pytesseract is None:
        warnings.append("pytesseract is not installed; OCR text is empty.")
        return "", None, warnings

    try:
        text = pytesseract.image_to_string(image).strip()
    except Exception as exc:
        warnings.append(f"OCR failed: {exc}")
        return "", None, warnings

    confidence: float | None = None
    try:
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        conf_values = []
        for raw_conf in data.get("conf", []):
            raw_text = str(raw_conf).strip()
            if not raw_text or raw_text == "-1":
                continue
            conf_values.append(float(raw_text))
        if conf_values:
            confidence = round(sum(conf_values) / len(conf_values), 2)
    except Exception as exc:
        warnings.append(f"Could not compute OCR confidence: {exc}")

    return text, confidence, warnings


def read_image(path: Path) -> dict[str, Any]:
    with Image.open(path) as image:
        image.load()
        width, height = image.size
        mode = image.mode
        image_format = image.format
        dpi = image.info.get("dpi")
        exif = exif_to_dict(image)
        ocr_text, conf, warnings = run_ocr(image)

    blocks = []
    if ocr_text:
        blocks.append(
            {
                "block_id": "p1b1",
                "page": 1,
                "kind": "ocr",
                "text": ocr_text,
                "confidence": conf,
            }
        )

    return {
        "source": {
            "path": str(path),
            "type": "image",
            "filename": path.name,
            "sha256": sha256_file(path),
        },
        "metadata": {
            "title": None,
            "author": None,
            "created_at": None,
            "modified_at": None,
            "page_count": 1,
            "image": {
                "width": width,
                "height": height,
                "mode": mode,
                "format": image_format or path.suffix.lstrip(".").upper(),
                "dpi": dpi,
            },
            "extra": {
                "exif": exif,
                "ocr_warnings": warnings,
            },
        },
        "text": ocr_text,
        "blocks": blocks,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Path to an image file")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSON path (default: outputs/extracted/<stem>.json)",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = args.input.expanduser().resolve()

    if not input_path.exists() or not input_path.is_file():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 2
    if input_path.suffix.lower() not in SUPPORTED_SUFFIXES:
        print(f"Unsupported image type: {input_path.suffix}", file=sys.stderr)
        return 2

    output_path = args.output
    if output_path is None:
        output_path = Path("outputs/extracted") / f"{input_path.stem}.json"
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    payload = read_image(input_path)
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
