#!/usr/bin/env python3
"""compare_baseline.py — compare a produced ZIM and stage against the July baseline.

Delta philosophy (see docs/verification.md): verification accepts ranges and
deltas intentionally — it never demands equality with the July 2026 counts.

Checks (each emits PASS / FAIL / WARN / INFO; exit is non-zero on any FAIL):

  1. ZIM bytes vs the reference bytes for the flavour (full or nopic,
     detected from the filename): FAIL only when the produced ZIM is
     implausibly small — below 1% of the baseline bytes. Ordinary size
     growth (the dump keeps growing) is informational and never fails.
  2. entry count (read from the ZIM header) vs baseline zim_entries:
     FAIL when 0, or when the absolute delta exceeds the large-delta
     threshold (default 50%, from configs/expected-counts.json
     large_delta_pct) — BOTH unexplained growth AND shrinkage beyond the
     threshold FAIL, unless --allow-large-delta. Entry count unavailable
     (non-ZIM file) FAILs unless --allow-non-zim (then WARN).
  3. stage question pages vs baseline questions (requires --stage-dir):
     FAIL when 0; tolerance mismatch is reported (exactness is
     audit_stage.py's job, so outside-tolerance is a WARN here).
  4. stage images vs baseline staged_images (requires --stage-dir):
     FAIL when 0; outside tolerance is a WARN.
  5. placeholders vs baseline initial_placeholders + unrecoverable
     (requires --stage-dir): REPORT ONLY — never a FAIL; placeholder
     accounting is documented in docs/verification.md.

Min sanity thresholds (min_question_pages / min_zim_entries) come from the
baseline JSON when present, else from configs/expected-counts.json; going
below them is a FAIL.

usage: compare_baseline.py --zim PATH [--stage-dir DIR] [--baseline JSON]
                           [--tolerance PCT] [--out REPORT_JSON]
                           [--allow-large-delta] [--allow-non-zim]
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import struct
import sys

ZIM_MAGIC = 0x044D495A  # b"ZIM\x04" as little-endian uint32 (H3)
ZIM_HEADER = struct.Struct("<IHH16sIIQQQQIIQ")  # 80 bytes; entry_count at offset 24


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def write_report_atomic(path: str, report: dict) -> None:
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


class Report:
    def __init__(self) -> None:
        self.rows: list[dict] = []  # expected vs actual columns

    def add(self, metric: str, status: str, expected, actual, note: str = "") -> None:
        self.rows.append({
            "metric": metric,
            "status": status,
            "expected": expected,
            "actual": actual,
            "note": note,
        })
        print(f"{status:<4} {metric:<24} expected={expected!s:<16} actual={actual!s:<16} {note}")

    def summary(self) -> dict:
        statuses = [r["status"] for r in self.rows]
        return {
            "pass": statuses.count("PASS"),
            "warn": statuses.count("WARN"),
            "fail": statuses.count("FAIL"),
            "info": statuses.count("INFO"),
            "result": "FAIL" if "FAIL" in statuses else "PASS",
        }


def read_zim_entry_count(path: str) -> int | None:
    """entry_count from the ZIM header; None when the file is not a ZIM.

    The exact magic (H3) is required: a file whose first 4 bytes are not the
    little-endian uint32 0x044D495A is not a ZIM and its "header" is
    unreliable, so the entry count is treated as unknown.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(ZIM_HEADER.size)
    except OSError:
        return None
    if len(head) < ZIM_HEADER.size:
        return None
    try:
        magic, _major, _minor, _uuid, entry_count, _rest = ZIM_HEADER.unpack(head)[:6]
    except struct.error:
        return None
    if magic != ZIM_MAGIC:
        return None
    return entry_count


def count_question_dirs(html_root: str) -> int:
    total = 0
    if not os.path.isdir(html_root):
        return 0
    try:
        shards = os.scandir(html_root)
    except OSError:
        return 0
    with shards:
        for shard in shards:
            if not shard.is_dir(follow_symlinks=False):
                continue
            try:
                with os.scandir(shard.path) as posts:
                    for post in posts:
                        if post.is_dir(follow_symlinks=False):
                            total += 1
            except OSError:
                continue
    return total


def count_image_files(images_root: str) -> int:
    total = 0
    if not os.path.isdir(images_root):
        return 0
    for _root, _dirs, files in os.walk(images_root):
        total += len(files)
    return total


def count_placeholders(images_root: str, spec: dict) -> dict:
    """Placeholder count by BOTH size and content SHA-256 (never size alone)."""
    size = spec.get("size_bytes")
    sha = spec.get("sha256")
    result = {"confirmed": 0, "size_candidates": 0, "determinable": bool(size and sha)}
    if not size or not sha:
        return result
    if not os.path.isdir(images_root):
        return result
    for entry in os.scandir(images_root):
        if not entry.is_file():
            continue
        try:
            if entry.stat().st_size != size:
                continue
        except OSError:
            continue
        result["size_candidates"] += 1
        try:
            import hashlib
            h = hashlib.sha256()
            with open(entry.path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    h.update(chunk)
            if h.hexdigest() == sha.lower():
                result["confirmed"] += 1
        except OSError:
            continue
    return result


def load_json(path: str, what: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"ERROR: cannot read {what} {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"ERROR: {what} {path} is not a JSON object")
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--zim", required=True, help="path to the produced ZIM")
    ap.add_argument("--stage-dir", help="stage directory (enables question/image/placeholder checks)")
    ap.add_argument("--baseline", default=None, help="baseline JSON (default: data/baseline-2026-07.json)")
    ap.add_argument("--tolerance", type=float, default=None, help="tolerance fraction for stage counts (default from configs/expected-counts.json)")
    ap.add_argument("--out", help="write JSON report to this path (atomic)")
    ap.add_argument("--allow-large-delta", action="store_true", help="permit entry-count deltas beyond the large-delta threshold")
    ap.add_argument("--allow-non-zim", action="store_true", help="tolerate a ZIM whose header cannot be parsed (entry delta becomes WARN)")
    args = ap.parse_args()

    if not os.path.isfile(args.zim):
        print(f"ERROR: --zim {args.zim} is not a regular file", file=sys.stderr)
        return 2

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    baseline_path = args.baseline or os.path.join(repo_root, "data", "baseline-2026-07.json")
    baseline = load_json(baseline_path, "baseline")
    counts = baseline.get("counts", baseline)
    refs = baseline.get("reference_artifacts", {})

    expected_cfg = load_json(os.path.join(repo_root, "configs", "expected-counts.json"), "expected-counts config")
    tolerance = args.tolerance if args.tolerance is not None else float(expected_cfg.get("tolerance", 0.02))
    large_delta_pct = float(expected_cfg.get("large_delta_pct", 50))
    min_question_pages = int(counts.get("min_question_pages", expected_cfg.get("min_question_pages", 0)))
    min_zim_entries = int(counts.get("min_zim_entries", expected_cfg.get("min_zim_entries", 0)))

    report = Report()

    # --- 1. ZIM bytes -----------------------------------------------------
    zim_bytes = os.path.getsize(args.zim)
    is_nopic = "nopic" in os.path.basename(args.zim).lower()
    if is_nopic:
        ref_bytes = counts.get("nopic_zim_bytes", refs.get("stackoverflow-nopic.zim", {}).get("bytes"))
    else:
        ref_bytes = counts.get("full_zim_bytes", refs.get("stackoverflow-final.zim", {}).get("bytes"))
    if not ref_bytes:
        report.add("zim_bytes", "WARN", "baseline has no reference bytes", zim_bytes,
                   "no reference bytes configured; size delta not evaluated")
    else:
        pct = (zim_bytes - int(ref_bytes)) / int(ref_bytes) * 100
        if zim_bytes < int(ref_bytes) * 0.01:
            report.add("zim_bytes", "FAIL", int(ref_bytes), zim_bytes,
                       f"implausibly small: {zim_bytes / 1e9:.2f} GB is below 1% of baseline {int(ref_bytes) / 1e9:.2f} GB")
        else:
            direction = "growth" if pct >= 0 else "shrinkage"
            report.add("zim_bytes", "PASS", int(ref_bytes), zim_bytes,
                       f"bytes delta {pct:+.2f}% ({direction}) — informational; size growth never fails")

    # --- 2. entry count ----------------------------------------------------
    entry_count = read_zim_entry_count(args.zim)
    expected_entries = int(counts.get("zim_entries", 0))
    if entry_count is None:
        if args.allow_non_zim:
            report.add("zim_entries", "WARN", expected_entries, "unknown",
                       "header unparsable; entry delta not evaluated (--allow-non-zim)")
        else:
            report.add("zim_entries", "FAIL", expected_entries, "unknown",
                       "header unparsable; entry count cannot be verified (use --allow-non-zim to tolerate)")
    elif entry_count == 0:
        report.add("zim_entries", "FAIL", expected_entries, 0, "entry count is 0 (invariant: > 0)")
    elif expected_entries and min_zim_entries and entry_count < min_zim_entries:
        report.add("zim_entries", "FAIL", expected_entries, entry_count,
                   f"below min_zim_entries threshold {min_zim_entries}")
    elif expected_entries:
        delta_pct = abs(entry_count - expected_entries) / expected_entries * 100
        direction = "growth" if entry_count >= expected_entries else "shrinkage"
        if delta_pct > large_delta_pct and not args.allow_large_delta:
            report.add("zim_entries", "FAIL", expected_entries, entry_count,
                       f"large unexplained delta: {delta_pct:.1f}% ({direction}) exceeds "
                       f"{large_delta_pct:.0f}% without --allow-large-delta")
        else:
            report.add("zim_entries", "PASS", expected_entries, entry_count,
                       f"delta {delta_pct:.1f}% ({direction}) within {large_delta_pct:.0f}% limit")
    else:
        report.add("zim_entries", "PASS", "no baseline zim_entries", entry_count, "sanity only")

    # --- 3/4/5. stage checks ------------------------------------------------
    if args.stage_dir:
        questions = count_question_dirs(os.path.join(args.stage_dir, "html"))
        expected_questions = int(counts.get("questions", 0))
        if questions == 0:
            report.add("stage_questions", "FAIL", expected_questions, questions,
                       "question page count is 0 (invariant: > 0)")
        elif min_question_pages and questions < min_question_pages:
            report.add("stage_questions", "FAIL", expected_questions, questions,
                       f"below min_question_pages threshold {min_question_pages}")
        elif expected_questions:
            ratio = abs(questions - expected_questions) / expected_questions
            if ratio <= tolerance:
                report.add("stage_questions", "PASS", expected_questions, questions,
                           f"within {tolerance:.1%} tolerance")
            else:
                report.add("stage_questions", "WARN", expected_questions, questions,
                           f"outside {tolerance:.1%} tolerance (exactness is audit_stage.py's job)")
        else:
            report.add("stage_questions", "PASS", "no baseline questions", questions, "sanity only")

        images = count_image_files(os.path.join(args.stage_dir, "images"))
        expected_images = int(counts.get("staged_images", 0))
        if images == 0:
            report.add("stage_images", "FAIL", expected_images, images,
                       "staged image count is 0 (invariant: > 0)")
        elif expected_images:
            ratio = abs(images - expected_images) / expected_images
            if ratio <= tolerance:
                report.add("stage_images", "PASS", expected_images, images,
                           f"within {tolerance:.1%} tolerance")
            else:
                report.add("stage_images", "WARN", expected_images, images,
                           f"outside {tolerance:.1%} tolerance (exactness is audit_stage.py's job)")
        else:
            report.add("stage_images", "PASS", "no baseline staged_images", images, "sanity only")

        spec_path = os.path.join(repo_root, "data", "placeholder-spec.json")
        spec = load_json(spec_path, "placeholder spec")
        ph = count_placeholders(os.path.join(args.stage_dir, "images"), spec)
        initial = int(counts.get("initial_placeholders", 0))
        unrecoverable = int(counts.get("unrecoverable", 0))
        if ph["determinable"]:
            report.add("placeholders", "INFO",
                       f"initial={initial} unrecoverable={unrecoverable}",
                       f"confirmed={ph['confirmed']}",
                       "report-only: placeholder drift is documented, not gated")
        else:
            report.add("placeholders", "INFO",
                       f"initial={initial} unrecoverable={unrecoverable}",
                       "not determinable",
                       "placeholder spec has no sha256; size-only counting refused (policy)")
    else:
        report.add("stage_questions", "INFO", "not checked", "no --stage-dir", "pass --stage-dir to enable")
        report.add("stage_images", "INFO", "not checked", "no --stage-dir", "pass --stage-dir to enable")

    summary = report.summary()
    print(f"RESULT: {summary['result']} "
          f"(pass={summary['pass']} warn={summary['warn']} fail={summary['fail']} info={summary['info']})")

    full = {
        "tool": "compare_baseline.py",
        "zim": args.zim,
        "stage_dir": args.stage_dir,
        "baseline": baseline_path,
        "tolerance": tolerance,
        "large_delta_pct": large_delta_pct,
        "generated_at": utc_now(),
        "rows": report.rows,
        "summary": summary,
    }
    if args.out:
        write_report_atomic(args.out, full)
    return 1 if summary["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())