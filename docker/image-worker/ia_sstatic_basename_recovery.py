#!/usr/bin/env python3
"""IA basename recovery worker — extract matching entries from Archive.org ZIP shards.

Self-contained container worker (docker/image-worker). Reads a manifest of
``hash<TAB>ia_filename`` rows (Archive.org stack-exchange-images layout:
``<letter>.zip`` shards containing ``<letter>/<letter>/<filename>``), scans
each shard READ-ONLY, extracts members whose basename matches, validates by
magic bytes (H10), converts to WebP preserving provenance (H11) and writes
``--out-dir/<hash>`` plus ``--out-dir/results.jsonl``.

Hardening (see docs/nas-worker.md):

- H3  ``--ia-dir`` and ``--manifest`` are read-only inputs; the script only
       ever writes under ``--state-dir`` and ``--out-dir``. The stage is
       never touched.
- H10 Magic-byte validation: Content-Type is advisory only; HTML/XML/SVG and
       error bodies are rejected, decode is verified, byte caps enforced.
- H11 Provenance-preserving WebP conversion: ``content_sha256`` (original)
       and ``derived_sha256`` (converted) recorded separately.
- Resumable: every result is checkpointed in a SQLite db under
       ``--state-dir``; an interrupted run resumes without re-extracting.

The module is import-safe without pyvips so the pure functions
(``sniff_mime``, ``validate_payload``, ``convert_to_webp``) can be
unit-tested with plain python3.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import pyvips
except Exception:  # pragma: no cover
    pyvips = None  # type: ignore[assignment]

DEFAULT_MAX_BYTES = 26_214_400      # 25 MiB per payload (H10 byte cap)
DEFAULT_QUALITY = 84
MIN_CONVERT_BYTES = 4096            # tiny images are left as-is
MAX_DIMENSION = 16384
MAX_PIXELS = 100_000_000

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_GIF_MAGICS = (b"GIF87a", b"GIF89a")
_HASH_RE = re.compile(r"[0-9a-f]{16,32}")

STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    hash TEXT PRIMARY KEY,
    url TEXT,
    status TEXT,
    source TEXT,
    content_sha256 TEXT,
    derived_sha256 TEXT,
    bytes_in INTEGER,
    bytes_out INTEGER,
    mime TEXT,
    error TEXT,
    updated_at REAL
)"""


# --------------------------------------------------------------------------- #
# pure helpers (unit-testable without third-party modules)
# --------------------------------------------------------------------------- #

def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sniff_mime(data: bytes) -> str:
    """Best-effort MIME from magic bytes (Content-Type is advisory, H10)."""
    if not data:
        return "empty"
    head = data[:4096]
    d = head.lstrip().lower()
    # HTML/XML/SVG bodies (error pages, redirects, captcha challenges) are
    # never images; check them first so they can never slip through.
    if d.startswith(b"<!doctype") or d.startswith(b"<html"):
        return "text/html"
    if b"<title>just a moment" in d or b"cloudflare" in d[:4096]:
        return "text/html"
    if d.startswith(b"<?xml") or d.startswith(b"<svg"):
        return "image/svg+xml"
    if data.startswith(_PNG_MAGIC):
        return "image/png"
    if data.startswith(_JPEG_MAGIC):
        return "image/jpeg"
    if data.startswith(_GIF_MAGICS):
        return "image/gif"
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    if data.startswith(b"\x00\x00\x01\x00"):
        return "image/x-icon"
    return "unknown"


def _decode_ok(data: bytes) -> bool:
    """Decode verification with pixel limits (H10)."""
    try:
        if pyvips is not None:
            img = pyvips.Image.new_from_buffer(data, "", access="sequential")
            w, h = img.width, img.height
            return 0 < w <= MAX_DIMENSION and 0 < h <= MAX_DIMENSION and w * h <= MAX_PIXELS
    except Exception:
        pass
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        img.verify()
        w, h = img.size
        return 0 < w <= MAX_DIMENSION and 0 < h <= MAX_DIMENSION and w * h <= MAX_PIXELS
    except Exception:
        pass
    # No decoder installed: strong magic bytes are the fallback (documented).
    return True


def validate_payload(data: bytes, max_bytes: int = DEFAULT_MAX_BYTES) -> Tuple[bool, str, str]:
    """Pure H10 validation: returns ``(ok, mime, reason)``.

    Rejects HTML/XML/SVG/error bodies, unknown content, oversized payloads
    and payloads that fail decode verification. Testable with plain python3.
    """
    if not data:
        return False, "", "empty payload"
    if len(data) > max_bytes:
        return (
            False,
            "",
            f"payload {len(data)} bytes exceeds max_bytes {max_bytes} (H10)",
        )
    mime = sniff_mime(data)
    if mime in ("empty", "unknown"):
        return False, mime, "magic bytes did not match a raster image"
    if mime in ("text/html", "image/svg+xml"):
        return False, mime, f"{mime} bodies are rejected (HTML/XML/SVG never accepted, H10)"
    if not _decode_ok(data):
        return False, mime, "payload does not decode as a valid image"
    return True, mime, ""


def convert_to_webp(data: bytes, quality: int = DEFAULT_QUALITY) -> Tuple[bytes, str, str]:
    """WebP conversion preserving provenance (H11).

    Returns ``(bytes_to_store, derived_sha256, out_mime)``. The ORIGINAL
    payload's SHA-256 (``content_sha256``) is always recorded separately by
    the caller. ``derived_sha256`` is the SHA-256 of the converted payload,
    or "" when no conversion was performed. Preference: pyvips, then cwebp.
    A conversion is kept only when meaningfully smaller than the original.
    """
    mime = sniff_mime(data)
    if mime in ("image/webp", "image/gif", "image/svg+xml", "text/html", "empty", "unknown"):
        return data, "", mime
    if mime not in ("image/png", "image/jpeg", "image/bmp", "image/x-icon"):
        return data, "", mime
    if len(data) < MIN_CONVERT_BYTES:
        return data, "", mime

    if pyvips is not None:
        try:
            img = pyvips.Image.new_from_buffer(data, "", access="sequential")
            out = bytes(img.webpsave_buffer(Q=quality, effort=4, strip=True))
            if out and len(out) < len(data) * 0.98:
                return out, sha256_of(out), "image/webp"
        except Exception:
            pass

    cwebp = shutil.which("cwebp")
    if cwebp:
        try:
            with tempfile.TemporaryDirectory() as td:
                src = Path(td) / "in.img"
                dst = Path(td) / "out.webp"
                src.write_bytes(data)
                subprocess.run(
                    [cwebp, "-quiet", "-q", str(quality), "-m", "4", str(src), "-o", str(dst)],
                    check=True,
                    timeout=60,
                    capture_output=True,
                )
                if dst.exists() and dst.stat().st_size > 0:
                    out = dst.read_bytes()
                    if out and len(out) < len(data) * 0.98:
                        return out, sha256_of(out), "image/webp"
        except (OSError, subprocess.SubprocessError):
            pass

    return data, "", mime


# --------------------------------------------------------------------------- #
# state + results
# --------------------------------------------------------------------------- #

def open_state(state_dir: Path) -> sqlite3.Connection:
    """Resumable SQLite checkpoint store (stdlib sqlite3, synchronous)."""
    state_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(state_dir / "recovery.sqlite"))
    conn.execute(STATE_SCHEMA)
    conn.commit()
    return conn


def write_results_line(path: Path, row: Dict[str, Any]) -> None:
    """Append one results.jsonl row (flushed + fsynced per line)."""
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _row(hash_: str, source: str, status: str, *, content_sha256: str = "",
         derived_sha256: str = "", mime: str = "", bytes_out: int = 0) -> Dict[str, Any]:
    return {
        "hash": hash_,
        "source_url": source,
        "status": status,
        "content_sha256": content_sha256,
        "derived_sha256": derived_sha256,
        "mime": mime,
        "bytes": bytes_out,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def record(
    conn: sqlite3.Connection,
    results_path: Path,
    hash_: str,
    source: str,
    status: str,
    *,
    zip_name: str = "",
    member: str = "",
    content_sha256: str = "",
    derived_sha256: str = "",
    bytes_in: int = 0,
    bytes_out: int = 0,
    mime: str = "",
    error: str = "",
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO images VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            hash_,
            source,
            status,
            f"ia:{zip_name}:{member}",
            content_sha256,
            derived_sha256,
            bytes_in,
            bytes_out,
            mime,
            (error or "")[:1000],
            time.time(),
        ),
    )
    write_results_line(
        results_path,
        _row(hash_, source, status, content_sha256=content_sha256,
             derived_sha256=derived_sha256, mime=mime, bytes_out=bytes_out),
    )


def rewrite_results_from_state(conn: sqlite3.Connection, results_path: Path) -> None:
    """Rewrite results.jsonl authoritatively from the checkpoint store."""
    rows = conn.execute(
        "SELECT hash, url, status, content_sha256, derived_sha256, bytes_out,"
        " mime, updated_at FROM images ORDER BY hash"
    ).fetchall()
    tmp = results_path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for (h, url, status, content_sha, derived_sha, b_out, mime, ts) in rows:
            row = {
                "hash": h,
                "source_url": url,
                "status": status,
                "content_sha256": content_sha or "",
                "derived_sha256": derived_sha or "",
                "mime": mime or "",
                "bytes": b_out or 0,
                "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(
                    timespec="seconds"
                ),
            }
            fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    os.replace(tmp, results_path)


# --------------------------------------------------------------------------- #
# main flow
# --------------------------------------------------------------------------- #

def parse_manifest(path: Path) -> List[Tuple[str, str, str]]:
    """TSV manifest: ``hash<TAB>ia_filename``; returns (hash, raw, basename)."""
    rows: List[Tuple[str, str, str]] = []
    try:
        fh = path.open("r", encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"error: cannot open manifest {path}: {exc}", file=sys.stderr)
        return rows
    with fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            h, fname = parts[0].strip(), parts[1].strip()
            if not _HASH_RE.fullmatch(h) or not fname:
                continue
            name = Path(fname.split("?", 1)[0]).name
            if name:
                rows.append((h, fname, name))
    return rows


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Extract matching basenames from Archive.org stack-exchange-images "
        "ZIP shards (manifest: hash<TAB>ia_filename); validate by magic bytes, "
        "convert to WebP, checkpoint in SQLite; results.jsonl written to --out-dir."
    )
    p.add_argument("--manifest", required=True, help="TSV manifest: hash<TAB>ia_filename")
    p.add_argument("--ia-dir", required=True, help="read-only dir holding <letter>.zip shards")
    p.add_argument("--state-dir", required=True, help="writable dir for the SQLite checkpoint db")
    p.add_argument("--out-dir", required=True, help="writable dir for out-dir/<hash> + results.jsonl")
    p.add_argument("--limit", type=int, default=0, help="max items to process this run (0 = all)")
    p.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES, help="per-payload byte cap (H10)")
    p.add_argument("--quality", type=int, default=DEFAULT_QUALITY, help="WebP quality (H11)")
    args = p.parse_args(argv)

    ia_root = Path(args.ia_dir)
    if not ia_root.is_dir():
        print(f"error: --ia-dir not found or not a directory: {ia_root}", file=sys.stderr)
        return 2

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    results_path = out / "results.jsonl"

    conn = open_state(Path(args.state_dir))
    try:
        done = {r[0] for r in conn.execute("SELECT hash FROM images WHERE status='ok'")}
        byzip: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)
        for h, fname, name in parse_manifest(Path(args.manifest)):
            if h in done or (out / h).is_file():
                continue
            byzip[name[0].lower() + ".zip"].append((h, fname, name))

        total = sum(len(v) for v in byzip.values())
        print(
            f"loaded={total} zips={len(byzip)} already_done={len(done)} "
            f"ia_dir={ia_root} out={out}",
            flush=True,
        )
        if not total:
            print("nothing to do: no manifest rows remain to extract", flush=True)

        stats = {"seen": 0, "ok": 0, "fail": 0, "rejected": 0}
        start = time.time()
        for zname in sorted(byzip):
            zp = ia_root / zname
            rows = byzip[zname]
            if not zp.is_file():
                for h, fname, _name in rows:
                    record(conn, results_path, h, fname, "fail",
                           zip_name=zname, error="missing_zip")
                    stats["fail"] += 1
                    stats["seen"] += 1
                conn.commit()
                continue

            wanted = {name: (h, fname) for h, fname, name in rows}
            try:
                with zipfile.ZipFile(zp) as z:
                    members = {
                        Path(i.filename).name: i
                        for i in z.infolist()
                        if not i.is_dir() and Path(i.filename).name in wanted
                    }
                    for name, (h, fname) in wanted.items():
                        info = members.get(name)
                        if info is None:
                            record(conn, results_path, h, fname, "fail",
                                   zip_name=zname, error="missing_member")
                            stats["fail"] += 1
                            stats["seen"] += 1
                            continue
                        try:
                            data = z.read(info)
                            ok, mime, reason = validate_payload(data, args.max_bytes)
                            if not ok:
                                record(conn, results_path, h, fname, "rejected",
                                       zip_name=zname, member=info.filename,
                                       mime=mime, bytes_in=len(data), error=reason)
                                stats["rejected"] += 1
                                stats["seen"] += 1
                                continue
                            converted, derived_sha, out_mime = convert_to_webp(
                                data, args.quality
                            )
                            tmp = out / (h + ".tmp")
                            tmp.write_bytes(converted)
                            os.replace(tmp, out / h)
                            record(
                                conn, results_path, h, fname, "ok",
                                zip_name=zname, member=info.filename,
                                content_sha256=sha256_of(data),
                                derived_sha256=derived_sha,
                                bytes_in=len(data), bytes_out=len(converted),
                                mime=out_mime,
                            )
                            stats["ok"] += 1
                            stats["seen"] += 1
                        except Exception as exc:  # defensive per-member
                            record(conn, results_path, h, fname, "error",
                                   zip_name=zname, member=info.filename,
                                   error=f"{type(exc).__name__}: {str(exc)[:500]}")
                            stats["fail"] += 1
                            stats["seen"] += 1
                    conn.commit()
            except (zipfile.BadZipFile, OSError) as exc:
                print(f"zip error {zname}: {exc}", flush=True)
                for h, fname, _name in rows:
                    record(conn, results_path, h, fname, "error",
                           zip_name=zname, error=f"zip_error:{type(exc).__name__}")
                    stats["fail"] += 1
                    stats["seen"] += 1
                conn.commit()
            print(
                f"zip={zname} seen={stats['seen']}/{total} ok={stats['ok']} "
                f"fail={stats['fail']} elapsed={time.time() - start:.0f}s",
                flush=True,
            )

        rewrite_results_from_state(conn, results_path)
        print(
            f"DONE stats={json.dumps(stats, sort_keys=True)}",
            flush=True,
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())