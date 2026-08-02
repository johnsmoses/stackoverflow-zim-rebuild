#!/usr/bin/env python3
"""Stage 1 — recover unmatched hashes from PostHistory.xml (stdin).

Port of ``sotoki/recover_posthistory.py`` onto the standard manifest schema.

Scans PostHistory.xml for ``i.stack.imgur.com`` URLs, hashes the candidate
sstatic/imgur forms, and matches against the still-unmatched hash set
(``--hashes`` minus anything already recovered via ``--already-recovered``).
Output is a standard recovery manifest (``--source-class posthistory_xml``).
"""

from __future__ import annotations

import argparse
import hashlib
import html as htmlmod
import re
import sys
import time

try:
    from .lib.manifest import ManifestWriter
except ImportError:  # allow `python3 recovery/recover_posthistory.py` too
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
    from recovery.lib.manifest import ManifestWriter

IMG_RE = re.compile(r"i\.stack\.imgur\.com/([A-Za-z0-9]+)\.([a-zA-Z]+)", re.I)


def candidate_urls(fname: str, ext: str) -> "list[str]":
    return [
        f"https://i.sstatic.net/{fname}.{ext}",
        f"https://i.stack.imgur.com/{fname}.{ext}",
        f"http://i.stack.imgur.com/{fname}.{ext}",
    ]


def load_hashes(path: str) -> "set[str]":
    with open(path, encoding="utf-8") as fh:
        return {line.strip() for line in fh if line.strip()}


def load_recovered(path: str) -> "set[str]":
    """Hashes already recovered from an existing recovery manifest.

    Locates the ``hash`` column via the TSV header (the standard manifest
    schema puts ``schema_version`` first); falls back to the first column
    for legacy ``hash<TAB>url<TAB>form`` files.
    """
    recovered: "set[str]" = set()
    with open(path, encoding="utf-8", errors="replace") as fh:
        header = fh.readline().rstrip("\n")
        fields = header.split("\t")
        try:
            col = fields.index("hash")
        except ValueError:
            col = 0
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) > col and parts[col].strip():
                recovered.add(parts[col].strip())
    return recovered


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Recover unmatched hashes from PostHistory.xml (stdin)"
    )
    ap.add_argument("--hashes", required=True, help="unmatched hashes, one per line")
    ap.add_argument("--out", required=True, help="output manifest TSV")
    ap.add_argument(
        "--already-recovered",
        default="",
        help="earlier recovery TSV whose first column is excluded",
    )
    ap.add_argument(
        "--source-class", default="posthistory_xml", help="source_class for rows"
    )
    ap.add_argument(
        "--debug-sample",
        type=int,
        default=0,
        help="print first N raw matching lines (truncated) then continue",
    )
    args = ap.parse_args()

    wanted = load_hashes(args.hashes)
    if args.already_recovered:
        recovered = load_recovered(args.already_recovered)
    else:
        recovered = set()
    still_open = wanted - recovered
    sys.stderr.write(
        f"Unmatched after prior recovery: {len(still_open):,} "
        f"(wanted {len(wanted)}, already recovered {len(recovered)})\n"
    )

    found = 0
    count = 0
    debug_left = args.debug_sample
    start = time.time()

    with ManifestWriter(args.out) as out:
        for line in sys.stdin:
            count += 1
            if count % 2_000_000 == 0:
                elapsed = time.time() - start
                sys.stderr.write(
                    f"  {count // 1_000_000}M lines, {found:,} recovered, "
                    f"{elapsed:.0f}s\n"
                )

            decoded = htmlmod.unescape(line)
            if debug_left > 0:
                sys.stderr.write("SAMPLE: " + repr(decoded[:400]) + "\n")
                debug_left -= 1

            seen: "set[tuple[str, str]]" = set()
            for m in IMG_RE.finditer(decoded):
                seen.add((m.group(1), m.group(2)))

            for fname, ext in seen:
                for url in candidate_urls(fname, ext):
                    h = hashlib.md5(url.encode("utf-8")).hexdigest()
                    if h in still_open:
                        out.add(
                            hash=h,
                            source_url=url,
                            source_class=args.source_class,
                            status="candidate",
                        )
                        found += 1
                        still_open.discard(h)
                        if found % 1000 == 0:
                            sys.stderr.write(f"  matched {found:,} so far\n")
                        break

    elapsed = time.time() - start
    sys.stderr.write(
        f"\nDone: {count:,} lines in {elapsed:.0f}s; recovered {found:,}; "
        f"still unmatched {len(still_open):,}\n"
    )


if __name__ == "__main__":
    main()