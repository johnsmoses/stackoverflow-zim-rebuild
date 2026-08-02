#!/usr/bin/env python3
"""Stage 1 — inventory: scan the staged HTML tree for image references.

For every staged page, extract ``/images/<hash>`` (also ``../images/...``)
references and emit one TSV row per ``(hash, page_path)`` pair with a
reference count and the anchor ``href`` URL when the reference is wrapped in
an ``<a href=...><img src=.../images/<hash>>`` element (captured for later
classification of URL-bearing records).

Output columns:
``hash<TAB>page_path<TAB>count<TAB>source_class<TAB>anchor_href``

``source_class`` is always ``stage``. Dedup key: (hash, page_path).
"""

from __future__ import annotations

import argparse
import html as htmlmod
import re
import sys
import time
from collections import Counter
from pathlib import Path

#: any image hash reference in a staged page
IMG_HASH_RE = re.compile(
    r"(?:\.\./)*images/([0-9a-f]{16,32})(?:[\"?#<\s]|$)", re.I
)
#: <a href="original-url"><img src=".../images/<hash>"> wrapper (ported from
#: the sotoki-build classifier)
ANCHOR_IMG_RE = re.compile(
    r'<a\b[^>]*\bhref=["\']([^"\']+)["\'][^>]*>\s*'
    r'<img\b[^>]*\bsrc=["\'][^"\']*/images/([0-9a-f]{16,32})["\'][^>]*>',
    re.I | re.S,
)

HEADER = "hash\tpage_path\tcount\tsource_class\tanchor_href"


def scan_page(path: Path) -> "tuple[dict, dict]":
    """Return (counts_per_hash, anchor_url_per_hash) for one page."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {}, {}
    counts: Counter = Counter()
    for m in IMG_HASH_RE.finditer(text):
        counts[m.group(1).lower()] += 1
    anchors: dict = {}
    for url, h in ANCHOR_IMG_RE.findall(text):
        h = h.lower()
        if h not in anchors:
            anchors[h] = htmlmod.unescape(url.strip())
    return dict(counts), anchors


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Inventory image references in the staged HTML tree"
    )
    ap.add_argument("--stage-dir", required=True, help="staged html tree root")
    ap.add_argument("--out", required=True, help="output TSV path")
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        help="stop after scanning N pages (0 = unlimited)",
    )
    args = ap.parse_args()

    stage = Path(args.stage_dir)
    if not stage.is_dir():
        ap.error(f"--stage-dir not a directory: {stage}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    pages = sorted(stage.rglob("*.html"))
    if args.limit:
        pages = pages[: args.limit]

    rows: dict = {}  # (hash, page_path) -> (count, anchor)
    scanned = 0
    t0 = time.time()
    with out_path.open("w", encoding="utf-8") as fh:
        fh.write(HEADER + "\n")
        for page in pages:
            counts, anchors = scan_page(page)
            rel = page.relative_to(stage).as_posix()
            for h in sorted(counts):
                key = (h, rel)
                if key not in rows:
                    rows[key] = (counts[h], anchors.get(h, ""))
            scanned += 1
            if scanned % 20000 == 0:
                sys.stderr.write(
                    f"scanned={scanned} pages unique_refs={len(rows)} "
                    f"elapsed={time.time() - t0:.0f}s\n"
                )
        for (h, rel), (count, anchor) in sorted(rows.items()):
            fh.write(f"{h}\t{rel}\t{count}\tstage\t{anchor}\n")

    sys.stderr.write(
        f"DONE: scanned={scanned} pages, unique (hash,page) refs={len(rows)} "
        f"-> {out_path}\n"
    )


if __name__ == "__main__":
    main()