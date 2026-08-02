#!/usr/bin/env python3
"""Stage 1 — recover unmapped hashes from the StackExchange Posts.xml dump.

Port of ``sotoki/recover_unmapped.py`` onto the standard manifest schema.

The dump's ``Body`` attributes contain URLs AS AUTHORED, i.e.
``i.stack.imgur.com`` (http, https, or protocol-relative), not the
``i.sstatic.net`` rewrite the live site (and sotoki) saw. sotoki hashed
``md5("https://i.sstatic.net/<FILENAME>")``, so we extract the filename from
any host variant and hash the canonical sstatic form plus fallbacks, matching
against the unmapped set.

Works on RAW escaped XML — no entity decoding needed (imgur filenames are
pure ``[A-Za-z0-9]`` + extension). Reads stdin as bytes (safe on a 103 GB
pipe). Output is a standard recovery manifest (``--source-class xml_dump``).

Usage::

    7z x -so stackoverflow.com-Posts.7z | \\
        python3 -m recovery.recover_unmapped --hashes still_unmapped.txt --out r.tsv
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import time

try:
    from .lib.manifest import ManifestWriter
except ImportError:  # allow `python3 recovery/recover_unmapped.py` too
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
    from recovery.lib.manifest import ManifestWriter

#: filename portion after any known image host, in raw escaped XML bytes
HOST_RE = re.compile(
    rb"i\.(?:stack\.imgur\.com|sstatic\.net)/"
    rb"([A-Za-z0-9]{3,12}\.(?:png|jpe?g|gif|webp|bmp|tiff?|svg)|[A-Za-z0-9]{3,12})",
    re.IGNORECASE,
)


def candidate_urls(fname: str) -> "list[str]":
    """All URL forms sotoki might have hashed for this filename."""
    return [
        f"https://i.sstatic.net/{fname}",      # primary (post-2024 rewrite)
        f"https://i.stack.imgur.com/{fname}",
        f"http://i.stack.imgur.com/{fname}",
        f"http://i.sstatic.net/{fname}",
    ] + ([f"https://i.sstatic.net/{fname}.png"] if "." not in fname else [])


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Recover unmapped image hashes from Posts.xml (stdin)"
    )
    ap.add_argument("--hashes", required=True, help="one unmapped hash per line")
    ap.add_argument("--out", required=True, help="output manifest TSV")
    ap.add_argument(
        "--source-class", default="xml_dump", help="source_class for rows"
    )
    ap.add_argument(
        "--debug-sample",
        type=int,
        default=0,
        help="print first N raw matching lines (truncated) then continue",
    )
    args = ap.parse_args()

    with open(args.hashes, encoding="utf-8") as fh:
        wanted = {line.strip() for line in fh if line.strip()}
    sys.stderr.write(f"loaded {len(wanted)} unmapped hashes\n")

    found: "dict[str, str]" = {}
    seen_fnames: "set[str]" = set()
    debug_left = args.debug_sample
    t0 = time.time()
    nbytes = 0
    nlines = 0

    with ManifestWriter(args.out) as out:
        for raw in sys.stdin.buffer:
            nlines += 1
            nbytes += len(raw)
            if nlines % 2_000_000 == 0:
                elapsed = time.time() - t0
                sys.stderr.write(
                    f"{nlines / 1e6:.0f}M lines, {nbytes / 2**30:.1f} GiB, "
                    f"{len(found)} recovered, {nbytes / 2**20 / elapsed:.0f} MiB/s\n"
                )

            if b"imgur" not in raw and b"sstatic" not in raw:
                continue

            if debug_left > 0:
                sys.stderr.write("RAW SAMPLE: " + repr(raw[:400]) + "\n")
                debug_left -= 1

            for m in HOST_RE.finditer(raw):
                fname = m.group(1).decode("ascii")
                if fname in seen_fnames:
                    continue
                seen_fnames.add(fname)
                for url in candidate_urls(fname):
                    h = hashlib.md5(url.encode("utf-8")).hexdigest()
                    if h in wanted and h not in found:
                        # Always download from sstatic (live CDN) regardless of
                        # which URL form produced the hash.
                        dl = f"https://i.sstatic.net/{fname}"
                        found[h] = dl
                        out.add(
                            hash=h,
                            source_url=dl,
                            source_class=args.source_class,
                            status="candidate",
                        )

    elapsed = time.time() - t0
    sys.stderr.write(
        f"DONE: {nlines} lines, {nbytes / 2**30:.1f} GiB in {elapsed / 60:.1f} min "
        f"recovered {len(found)} / {len(wanted)} "
        f"({100 * len(found) / max(len(wanted), 1):.1f}%) "
        f"unique filenames seen: {len(seen_fnames)}\n"
    )


if __name__ == "__main__":
    main()