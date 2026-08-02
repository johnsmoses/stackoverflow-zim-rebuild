#!/usr/bin/env python3
"""Stage 2 — final placeholder generation for still-unavailable hashes.

Runs AFTER every recovery source (IA, XML scans, edge resolver, upgrades,
validated sync) has finished. For every hash that is still pending, write the
versioned, semantically labelled placeholder
(``make_placeholder_webp("External visual asset unavailable")``) to
``--stage-images-dir/<hash>`` so no staged HTML reference 404s in the ZIM.

Placeholder content comes from ``recovery.lib.placeholders.write_placeholder_for``
so it round-trips with ``is_placeholder`` (size + content SHA-256 vs the
``--placeholder-spec``, H10). The log records the reason and the last
attempted URL for every hash.

Safety rules:

- A target that is a real image (valid + not a confirmed placeholder) is
  never overwritten — the operator gave us the *still missing* set, but a
  defensive re-check costs nothing.
- Already-placeholder targets (confirmed via the spec, or byte-identical to
  the current canonical placeholder) are skipped — the run is idempotent:
  re-running leaves no pending hash without a stage file and changes nothing.

``--dry-run`` (default) only logs what would be written.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

try:
    from .lib.images import is_placeholder, is_valid_image, sha256_of
    from .lib.manifest import default_tool_version, utc_now
    from .lib.placeholders import (
        make_placeholder_png,
        make_placeholder_webp,
        write_placeholder_for,
    )
except ImportError:  # allow `python3 recovery/finalize_unavailable.py` too
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
    from recovery.lib.images import is_placeholder, is_valid_image, sha256_of
    from recovery.lib.manifest import default_tool_version, utc_now
    from recovery.lib.placeholders import (
        make_placeholder_png,
        make_placeholder_webp,
        write_placeholder_for,
    )


def load_pending(path: Path) -> Dict[str, str]:
    """hash -> last attempted URL from the classified-remaining TSV.

    Tolerates the classify_missing columns (hash, source_url, source_class,
    status, page) as well as a plain one-column hash list.
    """
    pending: Dict[str, str] = {}
    with path.open("r", encoding="utf-8") as fh:
        header = fh.readline().rstrip("\n").split("\t")
        has_source_url = "source_url" in header
        if not header or header[0] not in ("hash", "source_url"):
            # plain hash list without header
            if not has_source_url:
                pending[_clean_hash(header[0])] = ""
            else:
                sys.stderr.write(f"warning: {path}: unrecognised header; skipping\n")
        for lineno, line in enumerate(fh, start=2):
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            row = dict(zip(header, parts))
            h = _clean_hash(row.get("hash", ""))
            if h:
                pending[h] = row.get("source_url", "")
    return pending


def _clean_hash(h: str) -> str:
    return (h or "").strip().lower()


def canonical_placeholder_bytes(spec: Dict[str, Any]) -> bytes:
    """The bytes the current spec says a placeholder should be."""
    fmt = str(spec.get("format", "webp")).lower() if spec else "webp"
    if fmt == "png":
        return make_placeholder_png()
    return make_placeholder_webp()


def _is_canonical_placeholder(target: Path, spec: Dict[str, Any]) -> bool:
    """Byte-identical to the canonical placeholder (spec-version independent)."""
    try:
        if target.stat().st_size == 0:
            return False
        data = target.read_bytes()
    except OSError:
        return False
    return sha256_of(data) == sha256_of(canonical_placeholder_bytes(spec))


def _placeholder_root(stage_images_dir: Path) -> Path:
    """Root for write_placeholder_for so the file lands in stage_images_dir.

    ``write_placeholder_for`` writes to ``<root>/images/<hash>``; when the
    stage images dir is itself named ``images`` (repo convention) the root is
    its parent, otherwise the root is the dir itself and the file is moved.
    """
    if stage_images_dir.name == "images":
        return stage_images_dir.parent
    return stage_images_dir


def finalize_hashes(
    pending: Dict[str, str],
    stage_dir: Path,
    spec: Dict[str, Any],
    dry_run: bool,
) -> List[Dict[str, Any]]:
    """Write (or plan) placeholders for every pending hash. Returns log rows."""
    log: List[Dict[str, Any]] = []
    ts = utc_now()
    tool_version = default_tool_version()
    for h in sorted(pending):
        target = stage_dir / h
        last_url = pending[h]
        base = {
            "hash": h,
            "status": "",
            "reason": "",
            "last_url": last_url,
            "placeholder_sha256": "",
            "bytes": 0,
            "timestamp": ts,
            "tool_version": tool_version,
        }
        if target.is_file():
            if is_placeholder(target, spec) or _is_canonical_placeholder(target, spec):
                log.append(
                    {**base, "status": "already-placeholder",
                     "reason": "confirmed placeholder present"}
                )
                continue
            try:
                data = target.read_bytes()
            except OSError:
                data = b""
            if is_valid_image(data):
                log.append(
                    {**base, "status": "already-recovered",
                     "reason": "real image present; not finalizing"}
                )
                continue
        if dry_run:
            log.append({**base, "status": "would-write", "reason": "pending"})
            continue
        info = write_placeholder_for(h, _placeholder_root(stage_dir), spec)
        dest = stage_dir / h
        if Path(info["path"]) != dest:
            os.replace(info["path"], dest)
        log.append(
            {
                **base,
                "status": "written",
                "reason": "unavailable after all recovery sources",
                "placeholder_sha256": info["content_sha256"],
                "bytes": info["bytes"],
            }
        )
    return log


def _write_jsonl(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def _load_spec(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"warning: cannot load placeholder spec {path}: {exc}\n")
        return {}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Final placeholder generation for still-unavailable hashes"
    )
    ap.add_argument("--classified-remaining", required=True,
                    help="TSV of hashes still missing after all recovery")
    ap.add_argument("--stage-images-dir", required=True, help="staged images dir")
    ap.add_argument("--placeholder-spec", required=True, help="placeholder spec JSON")
    ap.add_argument("--out", required=True, help="finalize-log.jsonl path")
    ap.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="dry-run (default): log only, no writes",
    )
    ap.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="write the placeholders",
    )
    args = ap.parse_args(argv)

    pending = load_pending(Path(args.classified_remaining))
    spec = _load_spec(Path(args.placeholder_spec))
    log = finalize_hashes(
        pending, Path(args.stage_images_dir), spec, dry_run=args.dry_run
    )
    _write_jsonl(Path(args.out), log)

    by_status: Dict[str, int] = {}
    for row in log:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    mode = "DRY-RUN" if args.dry_run else "WRITTEN"
    sys.stderr.write(
        f"DONE [{mode}] pending={len(pending)} statuses={by_status} -> {args.out}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())