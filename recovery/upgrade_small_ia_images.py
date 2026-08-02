#!/usr/bin/env python3
"""Stage 2 — upgrade tiny IA-recovered images from the live CDN.

Port of ``sotoki-build/upgrade_small_ia_images.py``. Input is a TSV of
IA-recovered hashes whose payload was tiny (<1500 bytes is the usual
threshold, applied when the manifest was built). Each row carries the live
CDN URL to re-download. When fetching is enabled the module re-downloads
from the live CDN through ``recovery.lib.images.download_image`` (H2–H7) and
records an *upgrade candidate* when the new payload is larger AND valid.

Output upgrade manifest columns::

    hash, source_url, old_bytes, new_bytes, old_sha256, new_sha256, status,
    content_sha256, derived_sha256, mime, timestamp, tool_version

``status`` is ``candidate`` (dry-run), ``upgrade_candidate`` (larger+valid),
``no_improvement`` (valid but not larger), or the downloader status
(``http_error``, ``invalid_content``, ``quota_exhausted``, ...).

Provenance is preserved (H7): ``content_sha256``/``new_sha256`` is the hash
of the ORIGINAL downloaded bytes, ``derived_sha256`` the WebP-converted form.

Dry-run (default, H1): the candidate list is emitted from the input manifest
without any network access and nothing is written to ``--out-dir``.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

try:
    from .lib.config import RecoveryConfig
    from .lib.images import download_image, sha256_of
    from .lib.manifest import default_tool_version, utc_now
except ImportError:  # allow `python3 recovery/upgrade_small_ia_images.py` too
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
    from recovery.lib.config import RecoveryConfig
    from recovery.lib.images import download_image, sha256_of
    from recovery.lib.manifest import default_tool_version, utc_now

HEADER = (
    "hash\tsource_url\told_bytes\tnew_bytes\told_sha256\tnew_sha256\tstatus\t"
    "content_sha256\tderived_sha256\tmime\ttimestamp\ttool_version"
)

_SCHEMA_HEADER_RE = re.compile(r"^schema_version\b")


def load_small_manifest(path: Path) -> List[Dict[str, Any]]:
    """Load the small-manifest TSV into item dicts.

    Accepts either the standard recovery manifest schema (rows carry
    ``bytes``/``content_sha256`` as the old values) or a plain
    ``hash<TAB>source_url`` two-column file.
    """
    items: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        first = fh.readline()
        if _SCHEMA_HEADER_RE.match(first):
            # standard recovery manifest: reuse the schema reader
            from .lib.manifest import ManifestReader

            for row in ManifestReader(path).rows():
                h = row.get("hash", "")
                url = row.get("source_url", "")
                if not h or not url:
                    continue
                items.append(
                    {
                        "hash": h,
                        "source_url": url,
                        "old_bytes": _as_int(row.get("bytes", "")),
                        "old_sha256": row.get("content_sha256", ""),
                        "source_class": row.get("source_class", "ia_stack_imgur"),
                    }
                )
            return items
        header = first.rstrip("\n").split("\t")
        for lineno, line in enumerate(fh, start=2):
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < len(header):
                parts += [""] * (len(header) - len(parts))
            row = dict(zip(header, parts))
            h = row.get("hash", "").lower()
            url = row.get("source_url", "")
            if not h or not url:
                continue
            items.append(
                {
                    "hash": h,
                    "source_url": url,
                    "old_bytes": _as_int(row.get("old_bytes", "")),
                    "old_sha256": row.get("old_sha256", ""),
                    "source_class": row.get("source_class", "ia_stack_imgur"),
                }
            )
    return items


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _old_from_disk(hash: str, stage_dir: Optional[Path]) -> "tuple[int, str]":
    """(old_bytes, old_sha256) from the stage file when available."""
    if stage_dir is None:
        return 0, ""
    p = stage_dir / hash
    if not p.is_file():
        return 0, ""
    try:
        data = p.read_bytes()
    except OSError:
        return 0, ""
    return len(data), sha256_of(data)


def upgrade_items(
    items: Sequence[Dict[str, Any]],
    config: RecoveryConfig,
    out_dir: Path,
    stage_dir: Optional[Path] = None,
    download: Optional[Callable[..., Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Re-download each item and record the upgrade decision.

    ``download`` is injectable so tests can substitute a fake (H9); when
    None the module-level ``download_image`` is looked up at call time. A
    payload that is valid but not an improvement is removed again (no junk
    in the upgrade dir).
    """
    if download is None:
        download = download_image
    rows: List[Dict[str, Any]] = []
    ts = utc_now()
    tool_version = default_tool_version()
    for item in items:
        h = item["hash"]
        url = item["source_url"]
        old_bytes = item.get("old_bytes") or 0
        old_sha = item.get("old_sha256") or ""
        if not old_bytes and not old_sha:
            old_bytes, old_sha = _old_from_disk(h, stage_dir)

        base = {
            "hash": h,
            "source_url": url,
            "old_bytes": old_bytes,
            "new_bytes": 0,
            "old_sha256": old_sha,
            "new_sha256": "",
            "status": "candidate",
            "content_sha256": "",
            "derived_sha256": "",
            "mime": "",
            "timestamp": ts,
            "tool_version": tool_version,
        }

        if not config.fetch_ok:
            rows.append(base)
            continue

        result = download(url, out_dir / h, config, hash=h)
        status = result.get("status", "error")
        if status == "ok":
            new_bytes = result.get("bytes") or 0
            if new_bytes > old_bytes:
                status = "upgrade_candidate"
            else:
                status = "no_improvement"
                try:
                    (out_dir / h).unlink(missing_ok=True)
                except OSError:
                    pass
            rows.append(
                {
                    **base,
                    "new_bytes": new_bytes,
                    "new_sha256": result.get("content_sha256", ""),
                    "status": status,
                    "content_sha256": result.get("content_sha256", ""),
                    "derived_sha256": result.get("derived_sha256", ""),
                    "mime": result.get("mime", ""),
                }
            )
        else:
            rows.append({**base, "status": status})
    return rows


def write_upgrade_manifest(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        fh.write(HEADER + "\n")
        for row in rows:
            fh.write(
                "\t".join(
                    str(row.get(field, ""))
                    for field in (
                        "hash", "source_url", "old_bytes", "new_bytes",
                        "old_sha256", "new_sha256", "status", "content_sha256",
                        "derived_sha256", "mime", "timestamp", "tool_version",
                    )
                )
                + "\n"
            )


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Upgrade tiny IA-recovered images from the live CDN"
    )
    ap.add_argument("--small-manifest", required=True, help="TSV of small IA files")
    ap.add_argument("--out-upgrade-manifest", required=True, help="output TSV")
    ap.add_argument("--out-dir", required=True, help="upgraded images dir")
    ap.add_argument(
        "--stage-images-dir",
        default=None,
        help="optional staged images dir (source of old_bytes/old_sha256)",
    )
    ap.add_argument("--config", default=None, help="optional RecoveryConfig JSON")
    ap.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="dry-run (default): candidate list only, no sockets",
    )
    ap.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="enable live mode (with --fetch)",
    )
    ap.add_argument(
        "--fetch",
        action="store_true",
        default=False,
        help="enable network downloads (requires --no-dry-run)",
    )
    ap.add_argument("--delay", type=float, default=0.5, help="per-host delay s")
    ap.add_argument("--limit", type=int, default=0, help="max items to process")
    args = ap.parse_args(argv)

    config = RecoveryConfig.from_args(args)
    items = load_small_manifest(Path(args.small_manifest))
    if args.limit:
        items = items[: args.limit]

    out_dir = Path(args.out_dir)
    if config.fetch_ok:
        out_dir.mkdir(parents=True, exist_ok=True)

    rows = upgrade_items(
        items,
        config,
        out_dir,
        stage_dir=Path(args.stage_images_dir) if args.stage_images_dir else None,
    )
    write_upgrade_manifest(Path(args.out_upgrade_manifest), rows)

    by_status: Dict[str, int] = {}
    for row in rows:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    mode = "DRY-RUN" if config.dry_run else "LIVE"
    sys.stderr.write(
        f"DONE [{mode}] items={len(items)} statuses={by_status} "
        f"-> {args.out_upgrade_manifest}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())