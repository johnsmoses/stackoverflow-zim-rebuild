#!/usr/bin/env python3
"""Stage 1 — classify: mark inventory hashes as missing and classify
URL-bearing records.

A hash is MISSING when its staged file is absent OR is a confirmed
placeholder (size AND content SHA-256 vs ``data/placeholder-spec.json``,
H10). Missing hashes are classified from the anchor href captured during
inventory:

- ``ia_stack_imgur``  — URL matches the Stack Imgur IA dump naming pattern
- ``sstatic_candidate`` — i.sstatic.net URL (live CDN candidate)
- ``other_http``      — any other http(s) URL
- ``no_original_url`` — no usable original URL captured

Classification output columns:
``hash<TAB>source_url<TAB>source_class<TAB>status<TAB>page``

One row per (hash, source_url) — a hash with several anchor sources yields
several rows (H8). ``--out-summary`` gets a human-readable tally.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

try:
    from .lib.images import is_placeholder
except ImportError:  # allow `python3 recovery/classify_missing.py` too
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
    from recovery.lib.images import is_placeholder

IA_STACK_IMGUR_RE = re.compile(
    r"^https?://i\.stack\.imgur\.com/[A-Za-z0-9]{5}\.(?:png|jpe?g|gif)$", re.I
)

CLASS_HEADER = "hash\tsource_url\tsource_class\tstatus\tpage"


def normalize_url(url: str) -> str:
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    return url


def host_of(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url, re.I)
    return m.group(1).lower() if m else ""


def classify_url(url: str) -> "tuple[str, str, str]":
    """Return (source_class, host, normalized_url)."""
    url = normalize_url(url)
    host = host_of(url)
    if IA_STACK_IMGUR_RE.match(url):
        return "ia_stack_imgur", host, url
    if host == "i.sstatic.net":
        return "sstatic_candidate", host, url
    if url.startswith(("http://", "https://")):
        return "other_http", host, url
    return "no_original_url", "", url


def load_inventory(path: Path) -> "dict[str, list[dict]]":
    """Group inventory rows by hash. Expects the inventory_stage columns."""
    by_hash: "dict[str, list[dict]]" = defaultdict(list)
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
            row = dict(zip(header, parts))
            h = row.get("hash", "").lower()
            if h:
                by_hash[h].append(row)
    return by_hash


def load_spec(path: Path) -> dict:
    if not path:
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"warning: cannot load placeholder spec {path}: {exc}\n")
        return {}


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Classify missing image hashes from an inventory"
    )
    ap.add_argument("--inventory", required=True, help="inventory_stage TSV")
    ap.add_argument("--stage-images-dir", required=True, help="staged images dir")
    ap.add_argument(
        "--placeholder-spec",
        default="data/placeholder-spec.json",
        help="placeholder spec JSON (default: data/placeholder-spec.json)",
    )
    ap.add_argument("--out-classified", required=True)
    ap.add_argument("--out-summary", required=True)
    args = ap.parse_args()

    inv_path = Path(args.inventory)
    images_dir = Path(args.stage_images_dir)
    spec = load_spec(Path(args.placeholder_spec))

    by_hash = load_inventory(inv_path)
    total = len(by_hash)

    classified: "list[dict]" = []
    absent = 0
    placeholder_confirmed = 0
    present = 0
    placeholder_size_unconfirmed = 0
    class_counts: Counter = Counter()
    host_counts: Counter = Counter()

    for h, rows in sorted(by_hash.items()):
        img_path = images_dir / h
        if not img_path.is_file():
            absent += 1
            missing = True
        elif is_placeholder(img_path, spec):
            placeholder_confirmed += 1
            missing = True
        else:
            present += 1
            if img_path.stat().st_size == int(spec.get("size_bytes", 0)):
                placeholder_size_unconfirmed += 1
            missing = False

        if not missing:
            continue

        anchor_rows = [r for r in rows if r.get("anchor_href", "").strip()]
        if not anchor_rows:
            classified.append(
                {
                    "hash": h,
                    "source_url": "",
                    "source_class": "no_original_url",
                    "status": "missing",
                    "page": rows[0].get("page_path", ""),
                }
            )
            class_counts["no_original_url"] += 1
            continue

        seen_pairs: set = set()
        for row in anchor_rows:
            url = row.get("anchor_href", "").strip()
            cls, host, norm_url = classify_url(url)
            key = (h, norm_url)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            classified.append(
                {
                    "hash": h,
                    "source_url": norm_url,
                    "source_class": cls,
                    "status": "missing",
                    "page": row.get("page_path", ""),
                }
            )
            class_counts[cls] += 1
            if host:
                host_counts[host] += 1

    out_class = Path(args.out_classified)
    out_class.parent.mkdir(parents=True, exist_ok=True)
    with out_class.open("w", encoding="utf-8") as fh:
        fh.write(CLASS_HEADER + "\n")
        for row in classified:
            fh.write(
                "\t".join(
                    [
                        row["hash"],
                        row["source_url"],
                        row["source_class"],
                        row["status"],
                        row["page"],
                    ]
                )
                + "\n"
            )

    out_sum = Path(args.out_summary)
    out_sum.parent.mkdir(parents=True, exist_ok=True)
    with out_sum.open("w", encoding="utf-8") as fh:
        fh.write(f"inventory_hashes={total}\n")
        fh.write(f"absent={absent}\n")
        fh.write(f"placeholder_confirmed={placeholder_confirmed}\n")
        fh.write(f"present={present}\n")
        fh.write(f"placeholder_size_unconfirmed={placeholder_size_unconfirmed}\n")
        fh.write(f"missing_hashes={absent + placeholder_confirmed}\n")
        fh.write(f"classified_rows={len(classified)}\n")
        fh.write("\nclass_counts:\n")
        for k, v in class_counts.most_common():
            fh.write(f"{k}\t{v}\n")
        fh.write("\nhost_counts_top:\n")
        for k, v in host_counts.most_common(30):
            fh.write(f"{k}\t{v}\n")

    sys.stderr.write(
        f"DONE: total={total} absent={absent} placeholder={placeholder_confirmed} "
        f"present={present} classified_rows={len(classified)}\n"
    )


if __name__ == "__main__":
    main()