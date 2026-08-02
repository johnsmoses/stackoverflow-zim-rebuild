#!/usr/bin/env python3
"""Stage 1 — extract all unique image hosts from Posts.xml (stdin).

Port of ``sotoki/extract_all_image_hosts.py``: unescape each line, match
``<img ... src="http(s)://...">``, count unique hosts, and emit a sorted TSV
of ``host<TAB>count<TAB>sample_url`` for the whole landscape.
"""

from __future__ import annotations

import argparse
import html as htmlmod
import re
import sys
from collections import Counter

IMG_SRC_RE = re.compile(r'<img[^>]*\s+src=["\'](https?://[^"\'\s>]+)', re.I)
HOST_RE = re.compile(r"https?://([^/]+)", re.I)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Extract unique image hosts from Posts.xml (stdin)"
    )
    ap.add_argument("--out-host-counts", required=True, help="output TSV")
    ap.add_argument(
        "--top",
        type=int,
        default=0,
        help="only emit the top N hosts (0 = all)",
    )
    args = ap.parse_args()

    hosts: Counter = Counter()
    samples: "dict[str, str]" = {}
    count = 0

    for line in sys.stdin:
        count += 1
        if count % 5_000_000 == 0:
            print(f"  {count:,} lines, {len(hosts)} unique hosts", flush=True)

        decoded = htmlmod.unescape(line)
        for m in IMG_SRC_RE.finditer(decoded):
            url = m.group(1)
            hm = HOST_RE.match(url)
            if hm:
                host = hm.group(1)
                hosts[host] += 1
                if host not in samples:
                    samples[host] = url[:120]

    with open(args.out_host_counts, "w", encoding="utf-8") as fh:
        fh.write("host\tcount\tsample_url\n")
        for host, n in hosts.most_common(args.top or None):
            fh.write(f"{host}\t{n}\t{samples.get(host, '')}\n")

    print(f"Scanned {count:,} lines; unique image hosts: {len(hosts)}", flush=True)
    for host, n in hosts.most_common(20):
        print(f"{n:>10,}  {host}   eg: {samples.get(host, '')}", flush=True)


if __name__ == "__main__":
    main()