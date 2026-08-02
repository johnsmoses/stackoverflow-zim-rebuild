"""Placeholder image generation.

The recovery pipeline writes a deterministic placeholder for assets that
cannot be recovered. The placeholder is versioned via ``data/placeholder-spec.json``:
verification (``recovery.lib.images.is_placeholder``) requires BOTH the byte
size AND the content SHA-256 recorded in the spec (H10) — size alone is never
treated as proof of a placeholder.

``write_placeholder_for`` returns the actual ``content_sha256`` of the bytes
it wrote; recording that hash in the spec is what makes the file
round-trip-confirmable by ``is_placeholder`` (size + hash).
"""

from __future__ import annotations

import os
import struct
import zlib
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .images import convert_to_webp, sha256_of

DEFAULT_TEXT = "External visual asset unavailable"
DEFAULT_SIZE = (320, 120)


def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    chunk = tag + payload
    return (
        struct.pack(">I", len(payload))
        + chunk
        + struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)
    )


def _minimal_png(size: Tuple[int, int], color: Tuple[int, int, int] = (235, 235, 235)) -> bytes:
    """Deterministic stdlib-only fallback PNG (solid colour, no text)."""
    width, height = max(1, int(size[0])), max(1, int(size[1]))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    row = b"\x00" + bytes(color) * width
    raw = row * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw, 9))
        + _png_chunk(b"IEND", b"")
    )


def make_placeholder_png(
    text: str = DEFAULT_TEXT,
    size: Tuple[int, int] = DEFAULT_SIZE,
) -> bytes:
    """Deterministic placeholder PNG bytes (PIL preferred, zlib fallback)."""
    try:
        from PIL import Image, ImageDraw

        img = Image.new("RGB", size, (235, 235, 235))
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, size[0] - 1, size[1] - 1], outline=(120, 120, 120))
        draw.line([0, 0, size[0] - 1, size[1] - 1], fill=(170, 170, 170), width=2)
        draw.line([size[0] - 1, 0, 0, size[1] - 1], fill=(170, 170, 170), width=2)
        draw.multiline_text(
            (12, max(4, size[1] // 2 - 20)), text, fill=(80, 80, 80)
        )
        import io

        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        out = buf.getvalue()
        if out:
            return out
    except Exception:
        pass
    return _minimal_png(size)


def make_placeholder_webp(
    text: str = DEFAULT_TEXT,
    size: Tuple[int, int] = DEFAULT_SIZE,
    quality: int = 82,
) -> bytes:
    """Deterministic placeholder WebP bytes.

    Converts the PNG placeholder via ``convert_to_webp`` (cwebp then PIL).
    Falls back to the PNG bytes if neither converter is available (sniffable
    as PNG, not WebP — documented in the recovery README).
    """
    png = make_placeholder_png(text=text, size=size)
    out, _ = convert_to_webp(png, quality=quality)
    return out


def write_placeholder_for(
    hash: str,
    out_root: Pathish,
    spec: Dict[str, Any],
    text: str = DEFAULT_TEXT,
) -> Dict[str, Any]:
    """Write ``images/<hash>`` placeholder content per the spec (format).

    Returns a provenance dict with the actual ``content_sha256`` of the bytes
    written — recording that hash in the spec makes the file confirmable by
    ``is_placeholder`` (size + hash, H10).
    """
    out_root = Path(out_root)
    fmt = str(spec.get("format", "webp")).lower() if spec else "webp"
    if fmt == "png":
        data = make_placeholder_png(text=text)
    else:
        data = make_placeholder_webp(text=text)

    dest = out_root / "images" / hash
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    return {
        "hash": hash,
        "path": str(dest),
        "format": fmt,
        "bytes": len(data),
        "content_sha256": sha256_of(data),
    }