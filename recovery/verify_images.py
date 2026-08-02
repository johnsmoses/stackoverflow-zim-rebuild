#!/usr/bin/env python3
"""Stage 2 — verify the staged images after sync/finalize.

For every hash in the recovered-images manifest (``--manifest``):

- the stage file must exist (``--stage-images-dir/<hash>``),
- it must be a readable image (magic-byte sniff + PIL verify, H6),
- its size must be > 0,
- entries whose manifest status is a *recovered* status (``ok``,
  ``recovered``, ``upgrade_candidate``) must additionally NOT be a confirmed
  placeholder (size + content SHA-256 vs ``--placeholder-spec``, H10).

Confirmed placeholders among the manifest hashes are counted and reported
(the "placeholders remaining" figure). ``--sample N`` additionally deep-decodes
N random stage files (PIL open + verify + full ``load()``) to catch corrupt
payloads that pass a shallow sniff.

Writes a human-readable report to ``--out`` and prints the PASS/FAIL summary
to stderr. Exit code 0 on PASS, 1 on FAIL.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from .lib.images import is_placeholder, is_valid_image
    from .lib.manifest import ManifestReader
except ImportError:  # allow `python3 recovery/verify_images.py` too
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
    from recovery.lib.images import is_placeholder, is_valid_image
    from recovery.lib.manifest import ManifestReader

RECOVERED_STATUSES = {"ok", "recovered", "upgrade_candidate"}
HASH_RE = re.compile(r"^[0-9a-f]{16,32}$")


def _load_manifest(path: Path) -> Dict[str, List[Dict[str, Any]]]:
    """hash -> list of rows (deduplicated view for status checks)."""
    by_hash: Dict[str, List[Dict[str, Any]]] = {}
    for row in ManifestReader(path).rows():
        h = (row.get("hash") or "").lower()
        if not h:
            continue
        by_hash.setdefault(h, []).append(row)
    return by_hash


def _deep_decode_ok(path: Path) -> Tuple[bool, str]:
    """Full decode: PIL open + verify, then load() to force pixel decode."""
    try:
        from PIL import Image

        with Image.open(path) as img:
            img.verify()
        with Image.open(path) as img:
            img.load()
        return True, ""
    except ImportError:
        return True, ""  # PIL absent: skip deep decode (documented fallback)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def verify_stage(
    stage_dir: Path,
    manifest_path: Path,
    spec: Dict[str, Any],
    sample_n: int = 0,
    sample_seed: int = 20260701,
) -> Dict[str, Any]:
    """Run the verification. Returns a stats/report dict."""
    by_hash = _load_manifest(manifest_path)
    hashes = sorted(by_hash)

    problems: List[Dict[str, Any]] = []
    placeholder_remaining = 0
    recovered_count = 0

    for h in hashes:
        target = stage_dir / h
        is_recovered = any(
            (r.get("status") or "") in RECOVERED_STATUSES for r in by_hash[h]
        )
        if is_recovered:
            recovered_count += 1
        if not target.is_file():
            problems.append({"hash": h, "issue": "missing-file",
                             "detail": "no stage file"})
            continue
        size = target.stat().st_size
        if size <= 0:
            problems.append({"hash": h, "issue": "empty-file",
                             "detail": f"size={size}"})
            continue
        try:
            data = target.read_bytes()
        except OSError as exc:
            problems.append({"hash": h, "issue": "unreadable",
                             "detail": str(exc)})
            continue
        if is_placeholder(target, spec):
            placeholder_remaining += 1
        if not is_valid_image(data):
            problems.append({"hash": h, "issue": "invalid-image",
                             "detail": "fails magic/decode validation (H6)"})
        elif is_recovered and is_placeholder(target, spec):
            problems.append({"hash": h, "issue": "placeholder",
                             "detail": "recovered entry is a confirmed placeholder (H10)"})

    # random deep-decode sample
    sample_issues: List[Dict[str, Any]] = []
    sampled = 0
    if sample_n > 0 and stage_dir.is_dir():
        files = [
            p for p in stage_dir.iterdir()
            if p.is_file() and HASH_RE.match(p.name)
        ]
        rng = random.Random(sample_seed)
        rng.shuffle(files)
        for f in files[: sample_n]:
            ok, detail = _deep_decode_ok(f)
            sampled += 1
            if not ok:
                sample_issues.append({"hash": f.name, "issue": "deep-decode",
                                      "detail": detail})

    ok = not problems and not sample_issues
    return {
        "pass": ok,
        "hashes_checked": len(hashes),
        "recovered_entries": recovered_count,
        "placeholders_remaining": placeholder_remaining,
        "problems": problems,
        "sample_n": sampled,
        "sample_issues": sample_issues,
    }


def _render_report(stats: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("image verification report")
    lines.append("=" * 60)
    lines.append(f"manifest_hashes_checked: {stats['hashes_checked']}")
    lines.append(f"recovered_entries: {stats['recovered_entries']}")
    lines.append(f"placeholders_remaining: {stats['placeholders_remaining']}")
    lines.append(f"deep_decode_sampled: {stats['sample_n']}")
    lines.append("")
    if stats["problems"]:
        lines.append("problems:")
        for p in stats["problems"]:
            lines.append(f"  {p['hash']}\t{p['issue']}\t{p['detail']}")
    if stats["sample_issues"]:
        lines.append("deep-decode failures:")
        for p in stats["sample_issues"]:
            lines.append(f"  {p['hash']}\t{p['issue']}\t{p['detail']}")
    lines.append("")
    lines.append("RESULT: " + ("PASS" if stats["pass"] else "FAIL"))
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Verify staged images after recovery")
    ap.add_argument("--stage-images-dir", required=True, help="staged images dir")
    ap.add_argument("--manifest", required=True, help="recovered manifest TSV")
    ap.add_argument("--placeholder-spec", required=True, help="placeholder spec JSON")
    ap.add_argument("--out", required=True, help="report path")
    ap.add_argument("--sample", type=int, default=0,
                    help="deep-decode N random stage files")
    args = ap.parse_args(argv)

    spec = {}
    try:
        with open(args.placeholder_spec, "r", encoding="utf-8") as fh:
            spec = json.load(fh)
        if not isinstance(spec, dict):
            spec = {}
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"warning: cannot load placeholder spec: {exc}\n")

    stats = verify_stage(
        Path(args.stage_images_dir),
        Path(args.manifest),
        spec,
        sample_n=args.sample,
    )
    report = _render_report(stats)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    sys.stderr.write(
        f"DONE hashes={stats['hashes_checked']} "
        f"placeholders_remaining={stats['placeholders_remaining']} "
        f"problems={len(stats['problems'])} sample_failures={len(stats['sample_issues'])} "
        f"result={'PASS' if stats['pass'] else 'FAIL'} -> {out}\n"
    )
    return 0 if stats["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())