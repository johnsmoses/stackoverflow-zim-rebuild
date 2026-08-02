#!/usr/bin/env python3
"""Stage 1 — build the Internet Archive basename manifest.

From the classified output, emit one row per (hash, source_url) whose record
is ``ia_stack_imgur`` or whose URL matches the Stack Imgur IA dump naming
pattern (``https?://i.stack.imgur.com/<5 alnum>.(png|jpe?g|gif)``).

The manifest lets a later stage (live IA/XML candidate manifest, Stage 2)
download ``https://archive.org/download/stack-exchange-images/<ia_filename>``
and validate the result against ``hash``.

Output columns (standard recovery fields + ``ia_filename`` after ``hash``)::

    hash, ia_filename, source_url, source_class, status, content_sha256,
    derived_sha256, mime, bytes, timestamp, tool_version

Dry-run is the default (H1): the manifest is written, no network is touched.
``--fetch`` exists so later stages that consume this manifest can be run with
real network access.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.parse
from pathlib import Path

try:
    from .lib.manifest import default_tool_version, utc_now
except ImportError:  # allow `python3 recovery/build_ia_manifest.py` too
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
    from recovery.lib.manifest import default_tool_version, utc_now

IA_STACK_IMGUR_RE = re.compile(
    r"^https?://i\.stack\.imgur\.com/[A-Za-z0-9]{5}\.(?:png|jpe?g|gif)$", re.I
)

HEADER = (
    "hash\tia_filename\tsource_url\tsource_class\tstatus\tcontent_sha256\t"
    "derived_sha256\tmime\tbytes\ttimestamp\ttool_version"
)


def ia_filename_of(url: str) -> str:
    """Basename of the URL path, e.g. https://i.stack.imgur.com/AbC12.png ->
    AbC12.png."""
    path = urllib.parse.urlparse(url).path
    return path.rsplit("/", 1)[-1]


def read_classified(path: Path) -> "list[dict]":
    rows: "list[dict]" = []
    with path.open("r", encoding="utf-8") as fh:
        header = fh.readline().rstrip("\n").split("\t")
        for lineno, line in enumerate(fh, start=2):
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < len(header):
                sys.stderr.write(
                    f"warning: {path}:{lineno}: expected {len(header)} columns, "
                    f"got {len(parts)}; skipping\n"
                )
                continue
            rows.append(dict(zip(header, parts)))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Build the IA basename manifest from classified records"
    )
    ap.add_argument("--classified", required=True, help="classify_missing TSV")
    ap.add_argument("--out", required=True, help="output IA manifest TSV")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="dry-run (default): write the manifest, touch no network",
    )
    ap.add_argument(
        "--fetch",
        action="store_true",
        default=False,
        help="enable network fetching (consumed by later stages)",
    )
    args = ap.parse_args()

    if args.fetch and args.dry_run:
        sys.stderr.write(
            "note: --fetch with --dry-run still means no sockets (H1); "
            "pass --no-dry-run for live runs\n"
        )

    rows = read_classified(Path(args.classified))
    tool_version = default_tool_version()
    ts = utc_now()

    seen_pairs: set = set()
    emitted = 0
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        fh.write(HEADER + "\n")
        for row in rows:
            url = row.get("source_url", "")
            if row.get("source_class") == "ia_stack_imgur" or IA_STACK_IMGUR_RE.match(url):
                key = (row["hash"], url)
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                fh.write(
                    "\t".join(
                        [
                            row["hash"],
                            ia_filename_of(url),
                            url,
                            "ia_stack_imgur",
                            row.get("status", "candidate"),
                            "",
                            "",
                            "",
                            "",
                            ts,
                            tool_version,
                        ]
                    )
                    + "\n"
                )
                emitted += 1

    sys.stderr.write(
        f"DONE: classified_rows={len(rows)} ia_manifest_rows={emitted} "
        f"(dry_run={args.dry_run}) -> {out_path}\n"
    )


if __name__ == "__main__":
    main()