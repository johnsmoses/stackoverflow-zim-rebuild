#!/usr/bin/env python3
"""Image recovery worker — fetch missing Stack Imgur images from the live CDN.

Self-contained container worker (docker/image-worker). Reads a manifest of
``hash<TAB>url`` rows, downloads each URL through a hardened aiohttp client
and writes the validated, WebP-converted payload to ``--out-dir/<hash>``.

Hardening (see docs/nas-worker.md for the full list):

- H3  Only ``--state-dir`` and ``--out-dir`` are ever written. The manifest
       is read-only input. The stage is never touched.
- H4  Per-host pacing (``--delay``), bounded concurrency, bounded retries
       with exponential backoff and Retry-After honor, TLS verification
       always on, connect/read timeouts.
- H7  On quota exhaustion (HTTP 429/403 after all retries) the run
       checkpoints and STOPS. The worker never switches interfaces or IPs.
- H10 Magic-byte validation: Content-Type is advisory only; HTML/XML/SVG and
       error bodies are rejected, decode is verified, byte caps enforced.
- H11 WebP conversion preserves provenance: ``content_sha256`` (original
       download) and ``derived_sha256`` (converted payload) are recorded
       separately in results.jsonl.

Resumable: completed hashes are checkpointed in a SQLite db under
``--state-dir`` (aiosqlite), so an interrupted run resumes without
re-downloading anything already marked ``ok``.

The module is import-safe without aiohttp/aiosqlite/pyvips so the pure
functions (``sniff_mime``, ``validate_payload``, ``convert_to_webp``,
``sha256_of``) can be unit-tested with plain python3.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

try:  # container image installs these; unit tests run without them
    import aiohttp
except Exception:  # pragma: no cover
    aiohttp = None  # type: ignore[assignment]

try:
    import aiosqlite
except Exception:  # pragma: no cover
    aiosqlite = None  # type: ignore[assignment]

try:
    import pyvips
except Exception:  # pragma: no cover
    pyvips = None  # type: ignore[assignment]

# --------------------------------------------------------------------------- #
# constants
# --------------------------------------------------------------------------- #

DEFAULT_MAX_BYTES = 26_214_400      # 25 MiB per payload (H10 byte cap)
DEFAULT_CONCURRENCY = 4
DEFAULT_DELAY = 1.0                 # conservative per-host minimum interval (H8)
DEFAULT_MAX_RETRIES = 3
DEFAULT_QUALITY = 84
CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 30.0
TOTAL_TIMEOUT = 45.0
BACKOFF_MAX = 60.0
QUOTA_BACKOFF_MAX = 300.0
MIN_CONVERT_BYTES = 4096            # tiny images are left as-is
MAX_DIMENSION = 16384
MAX_PIXELS = 100_000_000

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Referer": "https://stackoverflow.com/",
    "Accept": "image/avif,image/webp,image/apng,image/png,image/jpeg,image/gif,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_GIF_MAGICS = (b"GIF87a", b"GIF89a")
_HASH_RE = re.compile(r"[0-9a-f]{16,32}")

RESULTS_FIELDS = (
    "hash",
    "source_url",
    "status",
    "content_sha256",
    "derived_sha256",
    "mime",
    "bytes",
    "timestamp",
)


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
    and payloads that fail decode verification. Content-Type headers are
    never consulted. Testable with plain python3 (no aiohttp needed).
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
    download's SHA-256 (``content_sha256``) is always recorded separately by
    the caller. ``derived_sha256`` is the SHA-256 of the converted payload,
    or "" when no conversion was performed (original bytes kept as-is).

    Preference: pyvips, then cwebp. A conversion is kept only when it is
    meaningfully smaller than the original; otherwise the original bytes are
    stored unchanged (provenance intact either way).
    """
    original_sha = sha256_of(data)
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
# manifest + results
# --------------------------------------------------------------------------- #

@dataclass
class Item:
    hash: str
    url: str


def parse_manifest(path: Path) -> List[Item]:
    """TSV manifest: ``hash<TAB>url`` per line; blank/# lines skipped."""
    items: List[Item] = []
    try:
        fh = path.open("r", encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"error: cannot open manifest {path}: {exc}", file=sys.stderr)
        return items
    with fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            h, u = parts[0].strip(), parts[1].strip()
            if _HASH_RE.fullmatch(h) and u.startswith("http"):
                items.append(Item(h, u))
    return items


def _make_row(
    item: Item,
    status: str,
    *,
    content_sha256: str = "",
    derived_sha256: str = "",
    mime: str = "",
    bytes_out: int = 0,
) -> Dict[str, Any]:
    """One results.jsonl row (fixed schema, see RESULTS_FIELDS)."""
    return {
        "hash": item.hash,
        "source_url": item.url,
        "status": status,
        "content_sha256": content_sha256,
        "derived_sha256": derived_sha256,
        "mime": mime,
        "bytes": bytes_out,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


class ResultsWriter:
    """Append-only JSONL results writer (flushed + fsynced per line)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, row: Dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())


# --------------------------------------------------------------------------- #
# SQLite checkpoint state (aiosqlite, resumable)
# --------------------------------------------------------------------------- #

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


class WorkerState:
    """Resumable checkpoint store: completed hashes survive interruptions."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir)
        self.db_path = self.state_dir / "recovery.sqlite"
        self.conn = None

    async def open(self) -> None:
        if aiosqlite is None:  # pragma: no cover - container always has it
            raise RuntimeError("aiosqlite is required (pip install aiosqlite)")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.db_path)
        await self.conn.execute(STATE_SCHEMA)
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None

    async def done_hashes(self) -> set:
        cur = await self.conn.execute("SELECT hash FROM images WHERE status='ok'")
        rows = await cur.fetchall()
        return {r[0] for r in rows}

    async def record(
        self,
        item: Item,
        status: str,
        *,
        source: str = "",
        content_sha256: str = "",
        derived_sha256: str = "",
        bytes_in: int = 0,
        bytes_out: int = 0,
        mime: str = "",
        error: str = "",
    ) -> None:
        await self.conn.execute(
            "INSERT OR REPLACE INTO images VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                item.hash,
                item.url,
                status,
                source,
                content_sha256,
                derived_sha256,
                bytes_in,
                bytes_out,
                mime,
                (error or "")[:1000],
                time.time(),
            ),
        )
        await self.conn.commit()

    async def all_rows(self) -> List[Tuple]:
        cur = await self.conn.execute(
            "SELECT hash, url, status, source, content_sha256, derived_sha256,"
            " bytes_in, bytes_out, mime, error, updated_at FROM images ORDER BY hash"
        )
        return list(await cur.fetchall())


# --------------------------------------------------------------------------- #
# downloader
# --------------------------------------------------------------------------- #

class QuotaExhausted(Exception):
    """H7: the origin refused (429/403) after all retries — stop, never rotate."""


def _parse_retry_after(value: str) -> Optional[float]:
    value = (value or "").strip()
    if not value:
        return None
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


class RateLimiter:
    """Per-host minimum interval with jitter (H4/H8: aggregate pacing)."""

    def __init__(self, delay: float) -> None:
        self.delay = max(0.0, float(delay))
        self.next_by_host: Dict[str, float] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    async def wait(self, host: str) -> None:
        if self.delay <= 0:
            return
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            nxt = self.next_by_host.get(host, 0.0)
            if nxt > now:
                await asyncio.sleep(nxt - now)
            self.next_by_host[host] = time.monotonic() + self.delay

    def backoff(self, host: str, seconds: float) -> None:
        self.next_by_host[host] = max(
            self.next_by_host.get(host, 0.0), time.monotonic() + seconds
        )


class Downloader:
    """Bounded-concurrency, per-host-paced aiohttp downloader."""

    def __init__(
        self,
        *,
        session: Any,
        delay: float,
        concurrency: int,
        max_retries: int,
        max_bytes: int,
        quality: int,
        stop_event: asyncio.Event,
    ) -> None:
        self.session = session
        self.sem = asyncio.Semaphore(max(1, int(concurrency)))
        self.limiter = RateLimiter(delay)
        self.max_retries = max(0, int(max_retries))
        self.max_bytes = max_bytes
        self.quality = quality
        self.stop_event = stop_event

    async def fetch(self, item: Item) -> Dict[str, Any]:
        """Download + validate + convert one item.

        Returns a result dict; raises ``QuotaExhausted`` when the origin
        keeps refusing (429/403) after all retries (H7).
        """
        host = urlparse(item.url).netloc.lower() or item.url
        timeout = aiohttp.ClientTimeout(
            total=TOTAL_TIMEOUT, connect=CONNECT_TIMEOUT, sock_read=READ_TIMEOUT
        )
        last_reason = "network_error"
        for attempt in range(self.max_retries + 1):
            await self.limiter.wait(host)
            try:
                async with self.session.get(item.url, headers=HEADERS, timeout=timeout) as resp:
                    data = await resp.read()
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                last_reason = f"{type(exc).__name__}: {str(exc)[:160]}"
                if attempt >= self.max_retries:
                    break
                await asyncio.sleep(min(BACKOFF_MAX, 2 ** attempt + random.random()))
                continue

            status = resp.status
            if status == 200:
                if len(data) > self.max_bytes:
                    return {
                        "status": "too_large",
                        "mime": "",
                        "bytes_in": len(data),
                        "bytes_out": 0,
                        "content_sha256": "",
                        "derived_sha256": "",
                        "error": f"payload {len(data)} bytes exceeds max_bytes {self.max_bytes} (H10)",
                        "payload": b"",
                    }
                ok, mime, reason = validate_payload(data, self.max_bytes)
                if not ok:
                    return {
                        "status": "rejected",
                        "mime": mime,
                        "bytes_in": len(data),
                        "bytes_out": 0,
                        "content_sha256": "",
                        "derived_sha256": "",
                        "error": reason,
                        "payload": b"",
                    }
                out, derived_sha, out_mime = convert_to_webp(data, self.quality)
                return {
                    "status": "ok",
                    "mime": out_mime,
                    "bytes_in": len(data),
                    "bytes_out": len(out),
                    "content_sha256": sha256_of(data),
                    "derived_sha256": derived_sha,
                    "error": "",
                    "payload": out,
                }

            if status in (429, 403):
                # H7: quota-class. Back off, but after the last attempt the
                # run STOPS and checkpoints — no IP/interface switching.
                retry_after = resp.headers.get("Retry-After", "")
                wait = _parse_retry_after(retry_after) or min(
                    QUOTA_BACKOFF_MAX, 2 ** attempt * 30
                )
                self.limiter.backoff(host, wait)
                if attempt >= self.max_retries:
                    raise QuotaExhausted(
                        f"HTTP {status} after {self.max_retries + 1} attempts from {host}"
                    )
                await asyncio.sleep(wait)
                continue

            if 400 <= status < 500:
                return {
                    "status": "http_error",
                    "mime": "",
                    "bytes_in": 0,
                    "bytes_out": 0,
                    "content_sha256": "",
                    "derived_sha256": "",
                    "error": f"http_{status}",
                    "payload": b"",
                }

            if attempt >= self.max_retries:
                return {
                    "status": "http_error",
                    "mime": "",
                    "bytes_in": 0,
                    "bytes_out": 0,
                    "content_sha256": "",
                    "derived_sha256": "",
                    "error": f"http_{status}",
                    "payload": b"",
                }
            await asyncio.sleep(min(BACKOFF_MAX, 2 ** attempt))

        return {
            "status": "network_error",
            "mime": "",
            "bytes_in": 0,
            "bytes_out": 0,
            "content_sha256": "",
            "derived_sha256": "",
            "error": last_reason,
            "payload": b"",
        }


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #

async def _process_item(
    item: Item,
    downloader: Downloader,
    state: WorkerState,
    results: ResultsWriter,
    out_dir: Path,
    stats: Dict[str, Any],
) -> None:
    """Handle one item: download, validate, convert, checkpoint, record."""
    async with downloader.sem:
        try:
            res = await downloader.fetch(item)
        except QuotaExhausted as exc:
            # H7: checkpoint this item, flag the stop, never rotate.
            await state.record(item, "quota_exhausted", source="http", error=str(exc))
            results.write(_make_row(item, "quota_exhausted", mime="", bytes_out=0))
            downloader.stop_event.set()
            stats["seen"] += 1
            stats["quota"] += 1
            print(f"quota on {item.hash}: {exc}", flush=True)
            return

        status = res["status"]
        if status == "ok":
            dest = out_dir / item.hash
            tmp = dest.with_suffix(".tmp")
            tmp.write_bytes(res["payload"])
            os.replace(tmp, dest)
            await state.record(
                item,
                "ok",
                source="http",
                content_sha256=res["content_sha256"],
                derived_sha256=res["derived_sha256"],
                bytes_in=res["bytes_in"],
                bytes_out=res["bytes_out"],
                mime=res["mime"],
            )
            results.write(
                _make_row(
                    item,
                    "ok",
                    content_sha256=res["content_sha256"],
                    derived_sha256=res["derived_sha256"],
                    mime=res["mime"],
                    bytes_out=res["bytes_out"],
                )
            )
            stats["ok"] += 1
            stats["bytes_in"] += res["bytes_in"]
            stats["bytes_out"] += res["bytes_out"]
        else:
            await state.record(
                item,
                status,
                source="http",
                bytes_in=res["bytes_in"],
                mime=res["mime"],
                error=res["error"],
            )
            results.write(_make_row(item, status, mime=res["mime"], bytes_out=0))
            stats["rejected" if status == "rejected" else "fail"] += 1
            if status == "rejected" or res["error"]:
                print(f"{status} {item.hash}: {res['error']}", flush=True)
        stats["seen"] += 1


async def _run_workers(
    args: argparse.Namespace,
    items: List[Item],
    state: WorkerState,
    results: ResultsWriter,
    out_dir: Path,
    stats: Dict[str, Any],
) -> bool:
    """Run the bounded worker pool; returns True when a quota stop occurred."""
    stop_event = asyncio.Event()
    queue: asyncio.Queue = asyncio.Queue()
    for item in items:
        queue.put_nowait(item)

    async with aiohttp.ClientSession() as session:
        downloader = Downloader(
            session=session,
            delay=args.delay,
            concurrency=args.concurrency,
            max_retries=args.max_retries,
            max_bytes=args.max_bytes,
            quality=args.quality,
            stop_event=stop_event,
        )

        async def worker_loop(_wid: int) -> None:
            while True:
                try:
                    item = await queue.get()
                except asyncio.CancelledError:
                    return
                try:
                    if stop_event.is_set():
                        continue  # H7: drain the queue, no new work
                    await _process_item(item, downloader, state, results, out_dir, stats)
                except Exception as exc:  # defensive: never lose an item
                    await state.record(
                        item, "error", source="http",
                        error=f"{type(exc).__name__}: {str(exc)[:500]}",
                    )
                    results.write(_make_row(item, "error", mime=""))
                    stats["seen"] += 1
                    stats["fail"] += 1
                    print(f"error {item.hash}: {exc}", flush=True)
                finally:
                    queue.task_done()

        tasks = [asyncio.create_task(worker_loop(i)) for i in range(args.concurrency)]
        await queue.join()
        for t in tasks:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
    return stop_event.is_set()


async def _rewrite_results_from_state(state: WorkerState, results_path: Path) -> None:
    """Rewrite results.jsonl authoritatively from the checkpoint store.

    Append-only lines written during the run are replaced by the complete,
    deduplicated view from SQLite (survives interrupted runs cleanly).
    """
    rows = await state.all_rows()
    tmp = results_path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for (h, url, status, _source, content_sha, derived_sha, _b_in, b_out, mime, _err, ts) in rows:
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


async def main_async(args: argparse.Namespace) -> int:
    if aiohttp is None or aiosqlite is None:
        print(
            "error: aiohttp and aiosqlite are required (pip install aiohttp aiosqlite); "
            "pure functions are testable without them, a full run is not",
            file=sys.stderr,
        )
        return 2

    manifest = Path(args.manifest)
    if not manifest.is_file():
        print(f"error: manifest not found: {manifest}", file=sys.stderr)
        return 2

    items = parse_manifest(manifest)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = ResultsWriter(out_dir / "results.jsonl")

    state = WorkerState(Path(args.state_dir))
    await state.open()
    try:
        done = await state.done_hashes()
        pending = [i for i in items if i.hash not in done]
        if args.limit:
            pending = pending[: args.limit]

        stats: Dict[str, Any] = {
            "seen": 0, "ok": 0, "fail": 0, "rejected": 0, "quota": 0,
            "bytes_in": 0, "bytes_out": 0,
        }
        print(
            f"loaded={len(items)} already_done={len(done)} pending={len(pending)} "
            f"concurrency={args.concurrency} delay={args.delay}s "
            f"state={state.db_path} out={out_dir}",
            flush=True,
        )
        if not pending:
            print("nothing to do: all manifest hashes are already checkpointed ok", flush=True)
        else:
            quota_stopped = await _run_workers(args, pending, state, results, out_dir, stats)
            if quota_stopped:
                # H7: checkpoint is saved (every processed item was recorded);
                # exit 0 — a re-run resumes where the quota stop happened.
                await _rewrite_results_from_state(state, results.path)
                print("Quota exhausted; checkpointing and stopping", flush=True)
                print(f"DONE (quota stop) stats={json.dumps(stats, sort_keys=True)}", flush=True)
                return 0

        await _rewrite_results_from_state(state, results.path)
        print(f"DONE stats={json.dumps(stats, sort_keys=True)}", flush=True)
        return 0
    finally:
        await state.close()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Fetch missing Stack Imgur images from the live CDN "
        "(manifest: hash<TAB>url), validate by magic bytes, convert to WebP, "
        "checkpoint in SQLite; results.jsonl written to --out-dir."
    )
    p.add_argument("--manifest", required=True, help="TSV manifest: hash<TAB>url")
    p.add_argument("--state-dir", required=True, help="writable dir for the SQLite checkpoint db")
    p.add_argument("--out-dir", required=True, help="writable dir for out-dir/<hash> + results.jsonl")
    p.add_argument("--limit", type=int, default=0, help="max items to process this run (0 = all)")
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, help="max parallel downloads")
    p.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="min seconds between requests per host")
    p.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES, help="per-payload byte cap (H10)")
    p.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES, help="retries per item")
    p.add_argument("--quality", type=int, default=DEFAULT_QUALITY, help="WebP quality (H11)")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print(
            "interrupted; checkpoint state is saved in --state-dir, re-run to resume",
            file=sys.stderr,
        )
        return 130


if __name__ == "__main__":
    sys.exit(main())