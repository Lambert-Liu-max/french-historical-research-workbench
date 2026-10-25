from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_json(path: Path, value) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def scan_number(path: Path | str) -> int:
    numbers = re.findall(r"\d+", Path(path).stem)
    return int(numbers[-1]) if numbers else 10**12


def ordered_images(source: Path) -> list[Path]:
    return sorted(
        (path for path in source.iterdir() if path.is_file() and path.suffix.casefold() in IMAGE_EXTENSIONS),
        key=lambda path: (scan_number(path), path.name.casefold()),
    )


def inventory(source: Path, manifest_path: Path) -> dict:
    files = ordered_images(source)
    if not files:
        raise RuntimeError(f"no JPG/JPEG/PNG/TIFF scans found in {source}")
    records = []
    for order, path in enumerate(files, 1):
        before = path.stat()
        digest = file_sha256(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError(f"source changed while hashing: {path.name}")
        records.append({
            "order": order, "scan_id": path.name, "source_path": str(path.resolve()),
            "bytes": after.st_size, "mtime_ns": after.st_mtime_ns, "sha256": digest,
        })
    payload = {
        "schema_version": "1.0", "created_at": now(), "source_folder": str(source.resolve()),
        "source_policy": "read_only", "scan_count": len(records), "files": records,
    }
    if manifest_path.exists():
        old = read_json(manifest_path)
        old_signature = [(row["scan_id"], row["bytes"], row["sha256"]) for row in old.get("files", [])]
        new_signature = [(row["scan_id"], row["bytes"], row["sha256"]) for row in records]
        if old_signature != new_signature:
            raise RuntimeError("source inventory changed; refusing to reuse prior model state")
        payload["created_at"] = old.get("created_at", payload["created_at"])
        if old.get("source_folder") != payload["source_folder"] or any(
            left.get("source_path") != right.get("source_path")
            for left, right in zip(old.get("files", []), records)
        ):
            payload["relocated_at"] = now()
            atomic_json(manifest_path, payload)
            return payload
        return old
    atomic_json(manifest_path, payload)
    return payload


def windows(files: list[dict], size: int, overlap: int) -> list[dict]:
    if not 0 <= overlap < size:
        raise ValueError("window overlap must be smaller than window size")
    result, start = [], 0
    while start < len(files):
        chunk = files[start:start + size]
        result.append({
            "window_id": f"window-{chunk[0]['order']:04d}-{chunk[-1]['order']:04d}",
            "first_order": chunk[0]["order"], "last_order": chunk[-1]["order"],
            "scan_ids": [row["scan_id"] for row in chunk],
        })
        if start + size >= len(files):
            break
        start += size - overlap
    return result


def validate_source(item: dict) -> Path:
    path = Path(item["source_path"])
    if not path.is_file() or path.stat().st_size != item["bytes"] or file_sha256(path) != item["sha256"]:
        raise RuntimeError(f"source scan changed: {item['scan_id']}")
    return path


def derivative(item: dict, output: Path, max_pixels: int = 2048) -> Path:
    source = validate_source(item)
    if output.exists() and output.stat().st_size:
        return output
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise RuntimeError("Pillow is required for archive image processing; install the project dependencies") from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name("." + output.name + ".tmp.jpg")
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((max_pixels, max_pixels), Image.Resampling.LANCZOS)
        image.save(temporary, "JPEG", quality=90, optimize=True)
    os.replace(temporary, output)
    validate_source(item)
    return output
