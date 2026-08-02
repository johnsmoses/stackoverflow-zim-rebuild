#!/usr/bin/env python3
"""audit_zim.py — audit a produced ZIM file before promotion.

Checks (each emits PASS / FAIL / WARN / INFO; exit is non-zero on any FAIL):

  1. magic (H3): the first 4 bytes are read and unpacked as a little-endian
     uint32 (struct '<I') and must equal 0x044D495A exactly — the check is
     on the numeric magic, never merely an ASCII "ZIM" prefix. FAILs without
     --allow-non-zim.
  2. header parse: 80-byte ZIM header; entry_count (uint32 at offset 24) is
     extracted. A header that cannot be parsed FAILs unless --allow-non-zim
     (then WARN, header-only).
  3. entry count sanity: > 0, and greater than 90% of the baseline question
     count (the stage-question reference; baseline default:
     configs/expected-counts.json, override with --baseline). FAILs below.
  4. entry-level checks (homepage, /questions/, /tags/, /users/ existence,
     sampled content extraction) via zimdump if present, else libzim if
     importable, else header-only with a degraded-verification WARN
     recorded in the report. A degraded audit still exits 0 unless a
     *completed* check failed.
  5. zimcheck: run automatically when the binary is available (or given via
     --zimcheck-bin); its output is embedded in the report and a non-zero
     exit FAILs the audit. Use --no-zimcheck to skip (see bin/assemble
     --skip-zimcheck).

usage: audit_zim.py --zim PATH [--zimcheck-bin PATH] [--zimdump-bin PATH]
                    [--baseline JSON] [--out REPORT_JSON] [--sample N]
                    [--allow-non-zim] [--no-zimcheck]
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import struct
import subprocess
import sys

ZIM_MAGIC = 0x044D495A  # b"ZIM\x04" read as little-endian uint32
ZIM_HEADER = struct.Struct("<IHH16sIIQQQQIIQ")  # 80 bytes; magic is the uint32 at offset 0
ZIM_MIN_HEADER_BYTES = ZIM_HEADER.size

REQUIRED_PATHS = ("questions", "tags", "users")
HOMEPAGE_NAMES = ("index", "main", "home")

# zimdump/libzim content extraction caps
SAMPLE_CONTENT_CAP = 2 * 1024 * 1024
ZIMCHECK_TIMEOUT = 3600


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
        self.checks: dict[str, dict] = {}

    def _add(self, name: str, status: str, detail: str, counts: dict | None = None) -> None:
        entry = {"status": status, "detail": detail}
        if counts:
            entry["counts"] = counts
        self.checks[name] = entry
        print(f"{status:<4} {name:<26} {detail}")

    def pass_(self, name: str, detail: str, counts: dict | None = None) -> None:
        self._add(name, "PASS", detail, counts)

    def fail(self, name: str, detail: str, counts: dict | None = None) -> None:
        self._add(name, "FAIL", detail, counts)

    def warn(self, name: str, detail: str, counts: dict | None = None) -> None:
        self._add(name, "WARN", detail, counts)

    def info(self, name: str, detail: str, counts: dict | None = None) -> None:
        self._add(name, "INFO", detail, counts)

    def failed_checks(self) -> list[str]:
        return [k for k, v in self.checks.items() if v["status"] == "FAIL"]

    def summary(self) -> dict:
        statuses = [v["status"] for v in self.checks.values()]
        return {
            "pass": statuses.count("PASS"),
            "warn": statuses.count("WARN"),
            "fail": statuses.count("FAIL"),
            "info": statuses.count("INFO"),
            "result": "FAIL" if "FAIL" in statuses else "PASS",
        }


def parse_zim_header(path: str) -> dict | None:
    """Parse the 80-byte ZIM header; None when the file is not a ZIM."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(ZIM_MIN_HEADER_BYTES)
    except OSError as exc:
        print(f"ERROR: cannot read {path}: {exc}", file=sys.stderr)
        return None
    if len(head) < ZIM_MIN_HEADER_BYTES:
        return None
    try:
        magic, major, minor, uuid, entry_count, cluster_count, url_ptr, title_ptr, \
            cluster_ptr, mime_list_pos, main_page, layout_page, checksum_pos = \
            ZIM_HEADER.unpack(head)
    except struct.error:
        return None
    return {
        "magic": magic,  # uint32 little-endian; must == 0x044D495A (H3)
        "major_version": major,
        "minor_version": minor,
        "uuid": uuid.hex(),
        "entry_count": entry_count,
        "cluster_count": cluster_count,
        "main_page_index": main_page,
        "header_bytes": ZIM_MIN_HEADER_BYTES,
    }


def _normalize_zim_path(path: str) -> str:
    """Strip a leading namespace prefix (e.g. 'A/questions/1/slug' -> 'questions/1/slug')."""
    p = path.strip()
    parts = p.split("/", 1)
    if len(parts) == 2 and len(parts[0]) == 1 and parts[0].isalpha():
        return parts[1]
    return p


class EntryListing:
    """Best-effort entry listing/access via zimdump, libzim, or nothing.

    ``available`` names the backend actually used; ``degraded`` is True when
    entry-level checks could not be performed at all (header-only audit).
    """

    def __init__(self, zim_path: str, zimdump_bin: str | None, sample_n: int) -> None:
        self.zim_path = zim_path
        self.sample_n = sample_n
        self.available = False
        self.degraded = True
        self.backend: str | None = None
        self.paths: list[str] = []
        self._content_fn = None

        if zimdump_bin:
            self._init_zimdump(zimdump_bin)
        if not self.available:
            try:
                self._init_libzim()
            except Exception:  # noqa: BLE001 — optional backend
                self.available = False

    # -- zimdump ---------------------------------------------------------
    def _init_zimdump(self, zimdump_bin: str) -> None:
        try:
            out = subprocess.run(
                [zimdump_bin, "list", self.zim_path],
                capture_output=True,
                text=True,
                timeout=600,
            )
        except (OSError, subprocess.TimeoutExpired):
            return
        if out.returncode != 0:
            return
        paths: list[str] = []
        for line in out.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("Path") or line.startswith("Namespace"):
                continue
            # zimdump list rows: <path> [namespace url title ...]; take the
            # first token that contains a '/' (real ZIM paths always do).
            for token in line.split():
                if "/" in token:
                    paths.append(_normalize_zim_path(token))
                    break
        if not paths:
            return
        self.paths = paths
        self.backend = "zimdump"
        self.available = True
        self.degraded = False
        self._content_fn = self._content_zimdump

    def _content_zimdump(self, zimdump_bin: str, path: str) -> bytes | None:
        try:
            out = subprocess.run(
                [zimdump_bin, "show", self.zim_path, path],
                capture_output=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if out.returncode != 0:
            return None
        return out.stdout[:SAMPLE_CONTENT_CAP]

    # -- libzim ------------------------------------------------------------
    def _init_libzim(self) -> None:
        try:
            import zim  # type: ignore
        except ImportError:
            return
        archive = zim.Archive(self.zim_path)
        self._archive = archive
        paths: list[str] = []
        try:
            count = archive.entry_count
            for i in range(count):
                paths.append(_normalize_zim_path(archive.get_entry_by_id(i).path))
        except Exception:  # noqa: BLE001
            try:
                paths = [_normalize_zim_path(item.path) for item in archive.iter_items()]
            except Exception:  # noqa: BLE001
                return
        if not paths:
            return
        self.paths = paths
        self.backend = "libzim"
        self.available = True
        self.degraded = False
        self._content_fn = self._content_libzim

    def _content_libzim(self, _zimdump_bin: str, path: str) -> bytes | None:
        try:
            item = self._archive.get_entry_by_path(path).get_item()
            data = bytes(item.content)
            return data[:SAMPLE_CONTENT_CAP]
        except Exception:  # noqa: BLE001
            return None

    def content(self, path: str) -> bytes | None:
        if self._content_fn is None:
            return None
        try:
            return self._content_fn(None, path)
        except Exception:  # noqa: BLE001
            return None

    def sample_paths(self, n: int) -> list[str]:
        if not self.paths:
            return []
        if n >= len(self.paths):
            return list(self.paths)
        step = len(self.paths) / n
        return [self.paths[int(i * step)] for i in range(n)]


def check_magic(header: dict | None, allow_non_zim: bool, report: Report) -> None:
    if header is None:
        if allow_non_zim:
            report.warn("magic", "file not readable/too short; --allow-non-zim set (non-ZIM tolerated)")
        else:
            report.fail("magic", f"file too short or unreadable (need >= {ZIM_MIN_HEADER_BYTES} bytes for a ZIM header)")
        return
    magic = header["magic"]
    if magic == ZIM_MAGIC:
        report.pass_("magic", f"exact ZIM magic 0x{ZIM_MAGIC:08X} (struct '<I') verified", {"magic": f"0x{magic:08X}"})
    elif allow_non_zim:
        report.warn("magic", f"magic 0x{magic:08X} != 0x{ZIM_MAGIC:08X}; --allow-non-zim set (tolerated)", {"magic": f"0x{magic:08X}"})
    else:
        report.fail("magic", f"magic 0x{magic:08X} != expected 0x{ZIM_MAGIC:08X} (little-endian b'ZIM\\x04')", {"magic": f"0x{magic:08X}"})


def check_entry_count(header: dict | None, baseline: dict, allow_non_zim: bool, report: Report) -> None:
    if header is None:
        if allow_non_zim:
            report.warn("entry_count", "header unparsable; entry count unknown (--allow-non-zim)")
        else:
            report.fail("entry_count", "header unparsable; entry count cannot be verified")
        return
    entry_count = header["entry_count"]
    questions = int(baseline.get("questions", 0))
    floor = int(questions * 0.9)
    if entry_count == 0:
        report.fail("entry_count", "entry count is 0 (invariant: > 0)", {"entry_count": 0})
        return
    if entry_count <= floor:
        report.fail(
            "entry_count",
            f"entry count {entry_count} is not greater than the 90% question floor "
            f"({floor} of baseline questions={questions})",
            {"entry_count": entry_count, "floor": floor},
        )
    else:
        report.pass_(
            "entry_count",
            f"entry count {entry_count} > 0 and > floor {floor} (90% of baseline questions={questions})",
            {"entry_count": entry_count, "floor": floor},
        )


def check_required_paths(listing: EntryListing, report: Report) -> None:
    if not listing.available:
        report.warn(
            "required_paths",
            "header-only audit (no zimdump/libzim); homepage/questions/tags/users existence not verified",
        )
        return
    path_set = set(listing.paths)
    missing = [p for p in REQUIRED_PATHS if p not in path_set]
    has_questions_dir = any(p == "questions" or p.startswith("questions/") for p in listing.paths)
    if "questions" in path_set:
        has_questions_dir = True
    if not has_questions_dir:
        missing.append("questions/ (dir)")
    homepage_ok = any(
        p == name or p.startswith(f"{name}/") or name in p.split("/")[-1]
        for p in listing.paths
        for name in HOMEPAGE_NAMES
    )
    if missing or not homepage_ok:
        report.warn(
            "required_paths",
            f"some expected paths missing (homepage_ok={homepage_ok}; missing={missing or 'none'})",
            {"missing": missing, "homepage_ok": homepage_ok, "backend": listing.backend},
        )
    else:
        report.pass_(
            "required_paths",
            "homepage, /questions/, /tags/ and /users/ all present",
            {"backend": listing.backend},
        )


def check_content_sample(listing: EntryListing, sample_n: int, report: Report) -> None:
    if not listing.available:
        report.warn("content_sample", "header-only audit (no zimdump/libzim); content extraction skipped")
        return
    sampled = listing.sample_paths(sample_n)
    if not sampled:
        report.warn("content_sample", "entry listing empty; nothing to sample", {"sampled": 0})
        return
    failures = 0
    empty = 0
    invalid_utf8 = 0
    for path in sampled:
        data = listing.content(path)
        if data is None:
            failures += 1
            continue
        if not data:
            empty += 1
            failures += 1
            continue
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            invalid_utf8 += 1
            failures += 1
    detail = (f"sampled {len(sampled)} entries via {listing.backend}; "
              f"{failures} failures (empty={empty}, invalid-utf8={invalid_utf8})")
    if failures:
        report.fail("content_sample", detail, {"sampled": len(sampled), "failures": failures})
    else:
        report.pass_("content_sample", detail, {"sampled": len(sampled), "failures": 0})


def run_zimcheck(zim_path: str, zimcheck_bin: str, report: Report) -> None:
    print(f"RUN   zimcheck     {zimcheck_bin} {zim_path}")
    try:
        out = subprocess.run(
            [zimcheck_bin, zim_path],
            capture_output=True,
            text=True,
            timeout=ZIMCHECK_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        report.fail("zimcheck", f"timed out after {ZIMCHECK_TIMEOUT}s")
        return
    combined = (out.stdout or "") + (out.stderr or "")
    tail = combined.strip().splitlines()[-200:]
    tail_text = "\n".join(tail)
    if out.returncode != 0:
        report.fail("zimcheck", f"zimcheck exited {out.returncode}", {"output_tail": tail_text})
    else:
        report.pass_("zimcheck", "zimcheck completed with exit 0", {"output_tail": tail_text})


def load_baseline(baseline_path: str) -> dict:
    try:
        with open(baseline_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"ERROR: cannot read baseline {baseline_path}: {exc}") from exc
    counts = data.get("counts", data) if isinstance(data, dict) else {}
    if not isinstance(counts, dict):
        raise SystemExit(f"ERROR: baseline {baseline_path} has no counts object")
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--zim", required=True, help="path to the ZIM file to audit")
    ap.add_argument("--zimcheck-bin", help="zimcheck binary (default: search PATH)")
    ap.add_argument("--zimdump-bin", help="zimdump binary (default: search PATH)")
    ap.add_argument("--baseline", default=None, help="baseline JSON with 'counts' (default: configs/expected-counts.json)")
    ap.add_argument("--out", help="write JSON report to this path (atomic)")
    ap.add_argument("--sample", type=int, default=20, help="number of entries to content-sample (default 20)")
    ap.add_argument("--allow-non-zim", action="store_true", help="tolerate a file that is not a ZIM (header-only audit)")
    ap.add_argument("--no-zimcheck", action="store_true", help="do not run zimcheck even when available")
    args = ap.parse_args()

    if args.sample <= 0:
        print("ERROR: --sample must be > 0", file=sys.stderr)
        return 2
    if not os.path.isfile(args.zim):
        print(f"ERROR: --zim {args.zim} is not a regular file", file=sys.stderr)
        return 2

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    baseline_path = args.baseline or os.path.join(repo_root, "configs", "expected-counts.json")
    baseline = load_baseline(baseline_path)

    zimdump_bin = args.zimdump_bin or shutil.which("zimdump")
    listing = EntryListing(args.zim, zimdump_bin, args.sample)

    report = Report()
    header = parse_zim_header(args.zim)

    check_magic(header, args.allow_non_zim, report)
    check_entry_count(header, baseline, args.allow_non_zim, report)
    check_required_paths(listing, report)
    check_content_sample(listing, args.sample, report)

    degraded = listing.degraded
    if degraded:
        report.warn(
            "degraded",
            "no zimdump or importable libzim: entry-level checks are header-only "
            "(degraded verification policy — WARN, not FAIL)",
        )

    zimcheck_bin = args.zimcheck_bin or shutil.which("zimcheck")
    if zimcheck_bin and not args.no_zimcheck:
        run_zimcheck(args.zim, zimcheck_bin, report)
    elif args.no_zimcheck:
        report.info("zimcheck", "skipped (--no-zimcheck / bin/assemble --skip-zimcheck)")
    else:
        report.info("zimcheck", "zimcheck not found in PATH; structural checks limited to audit_zim.py")

    summary = report.summary()
    print(f"RESULT: {summary['result']} "
          f"(pass={summary['pass']} warn={summary['warn']} fail={summary['fail']} info={summary['info']})")

    full = {
        "tool": "audit_zim.py",
        "zim": args.zim,
        "generated_at": utc_now(),
        "baseline": baseline_path,
        "header": header,
        "entry_backend": listing.backend,
        "degraded": degraded,
        "checks": report.checks,
        "summary": summary,
    }
    if args.out:
        write_report_atomic(args.out, full)
    return 1 if summary["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())