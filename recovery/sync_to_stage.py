#!/usr/bin/env python3
"""Stage 2 — validated sync of recovered images into the stage.

Manifest-based replacement for ``sotoki-build/sync_recovered_to_stage.py``
(which used ``rsync --ignore-existing`` and blind directory copies — the
lesson: a recovered file must NEVER overwrite a stage file that is a real
image, and sync decisions must come from the manifest + placeholder spec, not
from "which directory has the file").

For every manifest entry (``--recovery-manifest``):

1. Validate the SOURCE (``--source-dir/<hash>``): exists, is a readable image
   (magic bytes + decode, H6), is NOT a confirmed placeholder (size + content
   SHA-256 vs ``--placeholder-spec``, H10), and its content SHA-256 matches
   the manifest row.
2. Decide the TARGET (``--stage-images-dir/<hash>``): it may only be
   overwritten when it is absent OR a confirmed placeholder (verified against
   the spec). A real image on disk is never touched.
3. Copy atomically (temp file + rename) — never ``rsync --ignore-existing``.

Outputs ``sync-applied.jsonl`` (``--out``) and ``sync-skipped.jsonl``
(``--out-skipped``, default sibling ``sync-skipped.jsonl``). Skip reasons:
``already-real``, ``not-placeholder``, ``sha-mismatch``, ``missing-source``.
Before/after placeholder counts for the affected hashes are reported on
stderr. ``--dry-run`` (default) computes the plan and writes the logs but
copies nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

try:
    from .lib.images import is_placeholder, is_valid_image, sha256_of
    from .lib.manifest import ManifestReader, default_tool_version, utc_now
except ImportError:  # allow `python3 recovery/sync_to_stage.py` too
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
    from recovery.lib.images import is_placeholder, is_valid_image, sha256_of
    from recovery.lib.manifest import ManifestReader, default_tool_version, utc_now

#: statuses that mean "a real payload was recovered" in the recovery manifest
RECOVERED_STATUSES = {"ok", "recovered", "upgrade_candidate"}


def load_recovery_rows(path: Path) -> List[Dict[str, Any]]:
    """Read the recovered-images manifest, deduplicated by hash.

    For hashes with several rows, prefer the row that carries a
    ``content_sha256`` (a verified payload) so the source can be validated.
    """
    reader = ManifestReader(path)
    best: Dict[str, Dict[str, Any]] = {}
    for row in reader.rows():
        h = (row.get("hash") or "").lower()
        if not h:
            continue
        prev = best.get(h)
        if prev is None or (not prev.get("content_sha256") and row.get("content_sha256")):
            best[h] = row
    return [best[h] for h in sorted(best)]


def analyze_sync(
    hash: str,
    row: Dict[str, Any],
    source_dir: Path,
    stage_dir: Path,
    spec: Dict[str, Any],
) -> Dict[str, Any]:
    """Validate source + decide target action for one hash.

    Returns an action dict with ``status`` in
    ``applied | would-apply | skipped`` and a ``reason``.
    """
    source = source_dir / hash
    if not source.is_file():
        return _skip(hash, "missing-source", "source file missing")
    try:
        data = source.read_bytes()
    except OSError as exc:
        return _skip(hash, "missing-source", f"source unreadable: {exc}")
    if not is_valid_image(data):
        return _skip(hash, "missing-source", "source is not a valid image (H6)")
    if is_placeholder(source, spec):
        return _skip(hash, "missing-source", "source is a confirmed placeholder (H10)")

    manifest_sha = (row.get("content_sha256") or "").lower()
    if not manifest_sha:
        return _skip(hash, "sha-mismatch", "manifest carries no content_sha256")
    actual_sha = sha256_of(data)
    if actual_sha != manifest_sha:
        return _skip(
            hash, "sha-mismatch",
            f"source sha {actual_sha[:12]} != manifest {manifest_sha[:12]}",
        )

    target = stage_dir / hash
    if target.is_file():
        if is_placeholder(target, spec):
            return _apply(hash, source, target, data, manifest_sha, "placeholder-target")
        try:
            target_data = target.read_bytes()
        except OSError:
            target_data = b""
        if is_valid_image(target_data):
            return _skip(hash, "already-real", "target is a real image; not overwriting")
        return _skip(hash, "not-placeholder", "target exists but is unclassifiable; not clobbering")
    return _apply(hash, source, target, data, manifest_sha, "absent-target")


def _skip(hash: str, reason: str, detail: str) -> Dict[str, Any]:
    return {"hash": hash, "status": "skipped", "reason": reason, "detail": detail}


def _apply(
    hash: str,
    source: Path,
    target: Path,
    data: bytes,
    content_sha256: str,
    reason: str,
) -> Dict[str, Any]:
    return {
        "hash": hash,
        "status": "applied",
        "reason": reason,
        "source": str(source),
        "target": str(target),
        "bytes": len(data),
        "content_sha256": content_sha256,
    }


def copy_atomic(source: Path, target: Path) -> None:
    """temp file + fsync + atomic rename into the stage (H3 discipline)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "wb") as fh:
            with source.open("rb") as src:
                shutil.copyfileobj(src, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def count_placeholders(hashes: Sequence[str], stage_dir: Path, spec: Dict[str, Any]) -> int:
    n = 0
    for h in hashes:
        p = stage_dir / h
        if p.is_file() and is_placeholder(p, spec):
            n += 1
    return n


def run(
    manifest_path: Path,
    source_dir: Path,
    stage_dir: Path,
    spec: Dict[str, Any],
    dry_run: bool,
) -> Dict[str, Any]:
    """Plan and (unless dry-run) apply the sync. Returns stats."""
    rows = load_recovery_rows(manifest_path)
    hashes = [r["hash"] for r in rows]

    applied: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    placeholders_before = count_placeholders(hashes, stage_dir, spec)

    for row in rows:
        h = row["hash"]
        action = analyze_sync(h, row, source_dir, stage_dir, spec)
        if action["status"] == "applied":
            if not dry_run:
                copy_atomic(Path(action["source"]), Path(action["target"]))
                action = {**action, "status": "applied"}
            else:
                action = {**action, "status": "would-apply"}
            action = {
                **action,
                "timestamp": utc_now(),
                "tool_version": default_tool_version(),
            }
            applied.append(action)
        else:
            skipped.append({**action, "timestamp": utc_now(),
                            "tool_version": default_tool_version()})

    placeholders_after = count_placeholders(hashes, stage_dir, spec)
    return {
        "rows": len(rows),
        "applied": applied,
        "skipped": skipped,
        "placeholders_before": placeholders_before,
        "placeholders_after": placeholders_after,
        "dry_run": dry_run,
    }


def _write_jsonl(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def _load_spec(path: Path) -> Dict[str, Any]:
    import json as _json

    try:
        with path.open("r", encoding="utf-8") as fh:
            data = _json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, _json.JSONDecodeError) as exc:
        sys.stderr.write(f"warning: cannot load placeholder spec {path}: {exc}\n")
        return {}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Validated sync of recovered images into the stage"
    )
    ap.add_argument("--recovery-manifest", required=True, help="recovered images TSV")
    ap.add_argument("--source-dir", required=True, help="recovered images dir")
    ap.add_argument("--stage-images-dir", required=True, help="staged images dir")
    ap.add_argument("--placeholder-spec", required=True, help="placeholder spec JSON")
    ap.add_argument("--out", required=True, help="sync-applied.jsonl path")
    ap.add_argument(
        "--out-skipped",
        default=None,
        help="sync-skipped.jsonl path (default: sibling of --out)",
    )
    ap.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="dry-run (default): plan + logs only, no copies",
    )
    ap.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="apply the sync",
    )
    args = ap.parse_args(argv)

    out = Path(args.out)
    out_skipped = Path(args.out_skipped) if args.out_skipped else (
        out.parent / "sync-skipped.jsonl"
    )
    spec = _load_spec(Path(args.placeholder_spec))

    stats = run(
        Path(args.recovery_manifest),
        Path(args.source_dir),
        Path(args.stage_images_dir),
        spec,
        dry_run=args.dry_run,
    )
    _write_jsonl(out, stats["applied"])
    _write_jsonl(out_skipped, stats["skipped"])

    mode = "DRY-RUN" if stats["dry_run"] else "APPLIED"
    sys.stderr.write(
        f"DONE [{mode}] rows={stats['rows']} applied={len(stats['applied'])} "
        f"skipped={len(stats['skipped'])} "
        f"placeholders_before={stats['placeholders_before']} "
        f"placeholders_after={stats['placeholders_after']} "
        f"-> {out}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())