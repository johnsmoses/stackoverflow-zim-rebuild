#!/usr/bin/env python3
"""Dry-run scaffolding for capturing the patched sotoki files.

Task 3 will turn this into a real capture tool. For now it only reports
which allowlisted files exist in a given sotoki package directory, with
line counts, and writes nothing.

Usage:
    python scripts/capture_sotoki_patches.py --package-path /path/to/sotoki --dry-run

Only the standard library is used (pathlib, argparse, sys).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ALLOWLIST = [
    "scraper.py",
    "posts.py",
    "utils/database/posts.py",
    "utils/html.py",
    "entrypoint.py",
    "css.py",
    "users.py",
    "utils/database/redisdb.py",
    "tags.py",
    "context.py",
    "renderer.py",
    "utils/preparation.py",
]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Capture (or, in dry-run, report) the allowlisted patched sotoki files."
        )
    )
    parser.add_argument(
        "--package-path",
        required=True,
        help=(
            "Path to the installed sotoki package directory "
            "(e.g. .../site-packages/sotoki)"
        ),
    )
    parser.add_argument(
        "--out-dir",
        default="patches/sotoki",
        help="Directory where captured patches will be written (default: patches/sotoki)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report which allowlisted files exist and their line counts; write nothing.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    pkg = Path(args.package_path)

    if not pkg.is_dir():
        print(f"ERROR: package path is not a directory: {pkg}", file=sys.stderr)
        return 2

    found: list[tuple[str, Path, int]] = []
    missing: list[str] = []
    for rel in ALLOWLIST:
        f = pkg / rel
        if f.is_file():
            line_count = sum(
                1 for _ in f.open(encoding="utf-8", errors="replace")
            )
            found.append((rel, f, line_count))
        else:
            missing.append(rel)

    print(f"package path: {pkg}")
    print(f"allowlisted files: {len(ALLOWLIST)}")
    print(f"present: {len(found)}  missing: {len(missing)}")
    for rel, _f, lines in found:
        print(f"  [ok]    {rel:32s} {lines:7d} lines")
    for rel in missing:
        print(f"  [miss]  {rel}")

    if args.dry_run:
        print()
        print("Dry run: nothing was written.")
        print(f"(Capture would write to: {args.out_dir})")
        return 0

    print(
        "Capture is not yet implemented; use --dry-run to report only.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())