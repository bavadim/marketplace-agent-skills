#!/usr/bin/env python3
"""Tile local product images for native agent vision; optional local Tesseract OCR."""
import argparse
import datetime as dt
import hashlib
import json
import subprocess
from pathlib import Path


def parse_tsv(output):
    """Tesseract TSV contains literal quotes, not CSV-quoted multiline fields."""
    import csv, io
    words = []
    for row in csv.DictReader(io.StringIO(output), delimiter="\t", quoting=csv.QUOTE_NONE):
        text = (row.get("text") or "").strip()
        if row.get("level") == "5" and text:
            words.append({"text": text, "confidence": float(row["conf"]),
                          "bbox": [int(row[k]) for k in ("left", "top", "width", "height")]})
    return words


def prepare(source, output, tile_height=1400, overlap=160, language=None):
    from PIL import Image, ImageOps
    if tile_height < 256 or not 0 <= overlap < tile_height:
        raise ValueError("tile-height >= 256 and 0 <= overlap < tile-height required")
    source, output = Path(source).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    with Image.open(source) as original:
        im = ImageOps.exif_transpose(original).convert("RGB")
        width, height = im.size
        tiles = []
        for index, y in enumerate(range(0, height, tile_height - overlap), 1):
            bottom = min(y + tile_height, height)
            tile = im.crop((0, y, width, bottom))
            path = output / f"{digest[:12]}-{index:03d}.png"
            tile.save(path)
            entry = {"evidence_id": f"{digest[:12]}:{index}", "path": str(path),
                     "bbox": [0, y, width, bottom], "ocr": None, "vision_status": "pending"}
            if language:
                proc = subprocess.run(["tesseract", str(path), "stdout", "-l", language,
                                       "--psm", "11", "tsv"], capture_output=True, text=True, timeout=60)
                if proc.returncode:
                    raise RuntimeError("Tesseract failed; verify installed languages with tesseract --list-langs")
                words = parse_tsv(proc.stdout)
                entry["ocr"] = {"engine": "tesseract", "language": language, "words": words,
                                "text": " ".join(w["text"] for w in words)}
            tiles.append(entry)
            if bottom == height:
                break
    return {"schema_version": 1, "source": str(source), "sha256": digest,
            "image_size": [width, height], "coordinates": "EXIF-oriented source pixels",
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(), "tiles": tiles,
            "note": "OCR is supporting evidence. Inspect tiles visually before accepting BOM-critical specs."}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("image")
    p.add_argument("--output", required=True)
    p.add_argument("--tile-height", type=int, default=1400)
    p.add_argument("--overlap", type=int, default=160)
    p.add_argument("--ocr", metavar="LANG", help="Installed Tesseract language, e.g. eng or rus+eng")
    args = p.parse_args()
    manifest = prepare(args.image, args.output, args.tile_height, args.overlap, args.ocr)
    path = Path(args.output) / (manifest["sha256"][:12] + "-manifest.json")
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(path.resolve())


if __name__ == "__main__":
    main()
