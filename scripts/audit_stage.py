#!/usr/bin/env python3
"""audit_stage.py — audit a restored/staged Stack Overflow staging tree.

Checks (each emits PASS / FAIL / WARN / INFO; exit is non-zero on any FAIL):

  a. question page count: <stage>/html/<shard>/<post_id> directories.
     Invariant: never zero. With --manifest (the page manifest JSONL
     recorded during the update run) the count must equal the manifest page
     count exactly.
  b. missing manifest.json siblings: every question dir must carry a
     manifest.json; any dir without one is a FAIL.
  c. duplicate zim_path across question manifests: any duplicate is a FAIL.
  d. answer_redirects coverage (sampled, or all with --strict): each
     sampled manifest must carry a list of answer ids; every missing or
     non-list value is counted as a failure (FAIL on any).
  e. image references: every /images/([0-9a-f]{16,32}) reference found in
     sampled page content must resolve to a staged image file; missing
     references FAIL.
  f. placeholder count: files matching the placeholder spec by BOTH size and
     content SHA-256 — never by size alone (see data/placeholder-spec.json).
     When the spec carries no sha256, size-only detection is refused and the
     check degrades to a WARN.
  g. MIME sampling: sampled files' magic bytes must match their extension.
  h. redis cardinalities (H1 semantics) — only when cardinality keys are
     explicitly configured via --redis-cardinality / --redis-zcard:

       * redis-cli is invoked via subprocess with a FIXED argv list (never
         a shell) and the command whitelist is PING / SCARD / ZCARD only;
       * connection timeout 2s, subprocess timeout 10s;
       * integer responses are strictly parsed — a query failure (non-zero
         exit, timeout, unparseable response) is distinguished from a
         legitimately-zero cardinality;
       * only loopback endpoints (127.0.0.1 or localhost) are accepted;
       * the full URL is never logged — only host:port (credentials are
         never echoed);
       * every configured cardinality is REQUIRED: an unreachable redis or
         a query error FAILs the audit, and a zero cardinality also FAILs
         (invariant: > 0). It is never silently warned away or treated as
         zero.

  i. input hashes (sampled, or all with --strict): for each question dir,
     any manifest field named <file>_sha256 (e.g. index_sha256 -> index.html)
     must match the actual SHA-256 of that sibling file.

usage: audit_stage.py --stage-dir DIR [--manifest MANIFEST_JSONL]
                      [--placeholder-spec SPEC_JSON] [--redis-url URL]
                      [--redis-cardinality KEY]... [--redis-zcard KEY]...
                      [--out REPORT_JSON] [--sample N] [--strict]
                      [--skip-redis]
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.parse

# --- constants --------------------------------------------------------------

IMG_REF_RE = re.compile(r"/images/([0-9a-f]{16,32})")
HEX_SHARD_RE = re.compile(r"[0-9a-f]{2}")
PLACEHOLDER_SPEC_FIELDS = ("size_bytes", "sha256")
MAGIC = {
    ".png": b"\x89PNG\r\n\x1a\n",
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
    ".gif": b"GIF8",
    ".webp": b"RIFF",
    ".svg": b"<",
}
HTML_MAGIC = b"<"

REDIS_CONNECT_TIMEOUT = 2  # seconds, passed to redis-cli (-t)
REDIS_SUBPROCESS_TIMEOUT = 10  # seconds, enforced on the subprocess
REDIS_ALLOWED_COMMANDS = ("PING", "SCARD", "ZCARD")
REDIS_ALLOWED_HOSTS = ("127.0.0.1", "localhost")


# --- small helpers ----------------------------------------------------------


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sample_index(total: int, n: int) -> list[int]:
    """Deterministic, evenly-spread sample of indices 0..total-1 (max n)."""
    if total <= 0 or n <= 0:
        return []
    if n >= total:
        return list(range(total))
    step = total / n
    return [int(i * step) for i in range(n)]


def write_report_atomic(path: str, report: dict) -> None:
    """Write JSON report via temp file + atomic rename (same directory)."""
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


class Report:
    """Per-check status collector with a JSON-friendly shape."""

    def __init__(self) -> None:
        self.checks: dict[str, dict] = {}

    def _add(self, name: str, status: str, detail: str, counts: dict | None = None) -> None:
        entry = {"status": status, "detail": detail}
        if counts:
            entry["counts"] = counts
        self.checks[name] = entry
        print(f"{status:<4} {name:<28} {detail}")

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


def iter_question_dirs(html_root: str):
    """Yield question dir paths: html/<shard>/<post_id> (depth-2 dirs)."""
    if not os.path.isdir(html_root):
        return
    try:
        shards = os.scandir(html_root)
    except OSError:
        return
    with shards:
        for shard in shards:
            if not shard.is_dir(follow_symlinks=False):
                continue
            try:
                posts = os.scandir(shard.path)
            except OSError:
                continue
            with posts:
                for post in posts:
                    if post.is_dir(follow_symlinks=False):
                        yield post.path


def count_question_dirs(html_root: str) -> int:
    return sum(1 for _ in iter_question_dirs(html_root))


def count_image_files(images_root: str) -> int:
    total = 0
    if not os.path.isdir(images_root):
        return 0
    for _root, _dirs, files in os.walk(images_root):
        total += len(files)
    return total


def load_json(path: str, what: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"ERROR: cannot read {what} {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"ERROR: {what} {path} is not a JSON object")
    return data


# --- redis access (H1) -------------------------------------------------------


class RedisQueryError(RuntimeError):
    pass


class RedisProbe:
    """Loopback-only redis-cli probe with whitelisted commands.

    H1 compliance: fixed argv (never a shell), command whitelist
    (PING/SCARD/ZCARD), connection timeout 2s, subprocess timeout 10s,
    strict integer parsing, loopback-only endpoints, sanitized logging
    (host:port only — the full URL and any credentials are never logged).
    """

    def __init__(self, url: str) -> None:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "redis":
            print(
                f"ERROR: redis URL must start with redis:// (got scheme '{parsed.scheme or 'none'}')",
                file=sys.stderr,
            )
            raise SystemExit(2)
        host = parsed.hostname
        if host not in REDIS_ALLOWED_HOSTS:
            print(
                "ERROR: redis URL host must be loopback only (127.0.0.1 or localhost); "
                f"got '{host or '(empty)'}' — non-local endpoints are rejected",
                file=sys.stderr,
            )
            raise SystemExit(2)
        try:
            port = parsed.port or 6379
        except ValueError as exc:
            print(f"ERROR: redis URL port is invalid: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        db = 0
        path = parsed.path.lstrip("/")
        if path:
            if not path.isdigit():
                print(f"ERROR: redis URL db path must be numeric, got '{path}'", file=sys.stderr)
                raise SystemExit(2)
            db = int(path)
        self.host = host
        self.port = port
        self.db = db
        self.last_error: str | None = None
        # sanitized endpoint for ALL logging — never the full URL, never
        # credentials, never the password portion.
        self.endpoint = f"{host}:{port}"

    def _run(self, *argv: str) -> str:
        if not argv or argv[0].upper() not in REDIS_ALLOWED_COMMANDS:
            raise RedisQueryError(f"command not allowed: {argv[0] if argv else '(none)'}")
        cmd = [
            "redis-cli",
            "-h", self.host,
            "-p", str(self.port),
            "-t", str(REDIS_CONNECT_TIMEOUT),
            "--raw",
        ]
        if self.db:
            cmd += ["-n", str(self.db)]
        cmd += list(argv)
        try:
            out = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=REDIS_SUBPROCESS_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            raise RedisQueryError(
                f"timeout after {REDIS_SUBPROCESS_TIMEOUT}s ({self.endpoint})"
            ) from exc
        if out.returncode != 0:
            detail = (out.stderr or out.stdout or "").strip()
            raise RedisQueryError(
                f"redis-cli {argv[0].upper()} failed (rc={out.returncode}, {self.endpoint})"
                + (f": {detail[:200]}" if detail else "")
            )
        return out.stdout.strip()

    def ping(self) -> bool:
        try:
            return self._run("PING") == "PONG"
        except RedisQueryError as exc:
            self.last_error = str(exc)
            return False

    def cardinality(self, command: str, key: str) -> int:
        """Strictly parse an integer cardinality; failures raise, never coerce.

        A legitimately-zero cardinality is a valid integer and is returned
        as 0 — the caller decides whether zero is acceptable (for required
        keys it is not). A query failure (exit code, timeout, parse error)
        raises RedisQueryError instead of becoming 0.
        """
        raw = self._run(command.upper(), key)
        if not re.fullmatch(r"[0-9]+", raw):
            raise RedisQueryError(
                f"unparseable {command.upper()} response for '{key}' "
                f"({self.endpoint}): {raw[:80]!r}"
            )
        return int(raw)


def check_redis_cardinalities(probe: RedisProbe, scard_keys, zcard_keys, report: Report) -> None:
    """H1: every configured cardinality is required.

    PING failure, query error, or a zero cardinality is a FAIL — never a
    WARN and never silently treated as zero.
    """
    required = [(k, "SCARD") for k in scard_keys] + [(k, "ZCARD") for k in zcard_keys]
    if not required:
        report.info(
            "redis",
            "no cardinality keys configured (--redis-cardinality/--redis-zcard); redis check skipped",
        )
        return
    if not probe.ping():
        report.fail(
            "redis",
            f"unreachable at {probe.endpoint}"
            + (f" ({probe.last_error})" if probe.last_error else "")
            + "; required cardinalities "
            f"{[k for k, _ in required]} could not be read — required-cardinality "
            "failures are never downgraded to warnings",
            {"required": len(required)},
        )
        return
    report.pass_(
        "redis",
        f"reachable at {probe.endpoint} (PING OK); checking "
        f"{len(required)} required cardinalit{'y' if len(required) == 1 else 'ies'}",
    )
    for key, command in required:
        name = f"redis_{command.lower()}_{key}"
        try:
            value = probe.cardinality(command, key)
        except RedisQueryError as exc:
            report.fail(
                name,
                f"query error for {key} ({command}, {probe.endpoint}): {exc}",
            )
            continue
        if value == 0:
            report.fail(
                name,
                f"{key} cardinality is 0 ({command}) — legitimately zero but a "
                "required key must be non-empty (invariant: > 0)",
                {"cardinality": 0},
            )
        else:
            report.pass_(
                name,
                f"{key} cardinality = {value} ({command})",
                {"cardinality": value},
            )


# --- per-check implementations -----------------------------------------------


def check_question_count(html_root: str, manifest_path: str | None, report: Report) -> None:
    questions = count_question_dirs(html_root)
    if questions <= 0:
        report.fail("question_page_count", f"count={questions} (invariant: > 0)", {"question_pages": questions})
        return
    if not manifest_path:
        report.pass_("question_page_count", f"count={questions} > 0", {"question_pages": questions})
        return
    manifest_pages = 0
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    report.fail(
                        "question_page_count",
                        f"manifest line {lineno} is not valid JSON: {exc}",
                    )
                    return
                if not isinstance(record, dict) or not record:
                    report.fail(
                        "question_page_count",
                        f"manifest line {lineno} is not a JSON object",
                    )
                    return
                manifest_pages += 1
    except OSError as exc:
        report.fail("question_page_count", f"cannot read page manifest {manifest_path}: {exc}")
        return
    if questions == manifest_pages:
        report.pass_(
            "question_page_count",
            f"count={questions} equals page manifest count={manifest_pages}",
            {"question_pages": questions, "manifest_pages": manifest_pages},
        )
    else:
        report.fail(
            "question_page_count",
            f"count={questions} != page manifest count={manifest_pages}",
            {"question_pages": questions, "manifest_pages": manifest_pages},
        )


def check_missing_manifests(dirs, report: Report) -> None:
    missing = [d for d in dirs if not os.path.isfile(os.path.join(d, "manifest.json"))]
    if missing:
        report.fail(
            "missing_manifests",
            f"{len(missing)} question dirs lack manifest.json (e.g. {os.path.relpath(missing[0], os.path.dirname(os.path.dirname(missing[0])))})",
            {"missing": len(missing), "total": len(dirs)},
        )
    else:
        report.pass_("missing_manifests", f"all {len(dirs)} question dirs carry manifest.json", {"missing": 0, "total": len(dirs)})


def check_duplicate_zim_paths(dirs, report: Report) -> None:
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    unreadable = 0
    for d in dirs:
        try:
            with open(os.path.join(d, "manifest.json"), encoding="utf-8") as fh:
                manifest = json.load(fh)
        except (OSError, json.JSONDecodeError):
            unreadable += 1
            continue
        if not isinstance(manifest, dict):
            unreadable += 1
            continue
        zim_path = manifest.get("zim_path")
        if not isinstance(zim_path, str) or not zim_path:
            continue
        if zim_path in seen:
            duplicates.append(f"{zim_path} ({seen[zim_path]} and {os.path.basename(d)})")
        else:
            seen[zim_path] = os.path.basename(d)
    if unreadable:
        report.warn(
            "duplicate_zim_path_unreadable",
            f"{unreadable} manifests unreadable; skipped for duplicate check",
            {"unreadable": unreadable},
        )
    if duplicates:
        report.fail(
            "duplicate_zim_path",
            f"{len(duplicates)} duplicate zim_path values (e.g. {duplicates[0]})",
            {"duplicates": len(duplicates)},
        )
    else:
        report.pass_("duplicate_zim_path", f"no duplicate zim_path across {len(dirs)} manifests", {"duplicates": 0})


def check_answer_redirects(dirs, sample_n, strict, report: Report) -> None:
    if strict:
        sampled = dirs
    else:
        sampled = [dirs[i] for i in sample_index(len(dirs), sample_n)]
    if not sampled:
        report.warn("answer_redirects", "no question dirs to sample", {"sampled": 0})
        return
    failures = 0
    for d in sampled:
        try:
            with open(os.path.join(d, "manifest.json"), encoding="utf-8") as fh:
                manifest = json.load(fh)
            redirects = manifest.get("answer_redirects")
            ok = isinstance(redirects, list)
        except (OSError, json.JSONDecodeError):
            ok = False
        if not ok:
            failures += 1
    detail = f"sampled {len(sampled)} of {len(dirs)} manifests; {failures} missing/invalid answer_redirects"
    if failures == 0:
        report.pass_("answer_redirects", detail, {"sampled": len(sampled), "failures": 0})
    else:
        report.fail("answer_redirects", detail, {"sampled": len(sampled), "failures": failures})


def check_image_refs(dirs, images_root, sample_n, strict, report: Report) -> None:
    if strict:
        sampled = dirs
    else:
        sampled = [dirs[i] for i in sample_index(len(dirs), sample_n)]
    refs: dict[str, int] = {}
    unreadable = 0
    for d in sampled:
        try:
            with open(os.path.join(d, "index.html"), encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            unreadable += 1
            continue
        for match in IMG_REF_RE.finditer(content):
            refs[match.group(1)] = refs.get(match.group(1), 0) + 1
    if unreadable:
        report.warn("image_refs_unreadable", f"{unreadable} sampled index.html unreadable", {"unreadable": unreadable})
    if not refs:
        report.pass_("image_refs", f"no /images/<hash> references in {len(sampled)} sampled pages", {"refs": 0})
        return
    # Build a basename-without-extension index of staged images once.
    index: set[str] = set()
    if os.path.isdir(images_root):
        for entry in os.scandir(images_root):
            if not entry.is_file():
                continue
            base = entry.name
            if "." in base:
                base = base.rsplit(".", 1)[0]
            index.add(base)
    missing = sorted(h for h in refs if h not in index and not os.path.isfile(os.path.join(images_root, h)))
    total_refs = sum(refs.values())
    if missing:
        report.fail(
            "image_refs",
            f"{len(missing)} of {len(refs)} referenced images have no staged file "
            f"(e.g. {missing[0]})",
            {"referenced": len(refs), "missing": len(missing), "total_refs": total_refs},
        )
    else:
        report.pass_(
            "image_refs",
            f"all {len(refs)} referenced images resolve to staged files",
            {"referenced": len(refs), "missing": 0, "total_refs": total_refs},
        )


def check_placeholders(images_root, spec_path, report: Report) -> None:
    spec = load_json(spec_path, "placeholder spec")
    size = spec.get("size_bytes")
    sha = spec.get("sha256")
    if not isinstance(size, int) or size <= 0:
        report.warn("placeholders", f"placeholder spec has no valid size_bytes: {size!r}")
        return
    candidates = 0
    confirmed = 0
    if os.path.isdir(images_root):
        for entry in os.scandir(images_root):
            if not entry.is_file():
                continue
            try:
                if entry.stat().st_size != size:
                    continue
            except OSError:
                continue
            candidates += 1
            if sha:
                try:
                    if sha256_file(entry.path) == sha.lower():
                        confirmed += 1
                except OSError:
                    continue
    if sha:
        report.pass_(
            "placeholders",
            f"{confirmed} placeholder files confirmed by size={size} AND content SHA-256 "
            f"({candidates} size candidates)",
            {"confirmed": confirmed, "size_candidates": candidates},
        )
    else:
        report.warn(
            "placeholders",
            f"spec sha256 is null; size-only placeholder detection refused (policy: "
            f"never size alone) — {candidates} files match size={size} but are unconfirmed",
            {"confirmed": 0, "size_candidates": candidates},
        )


def check_mime_sample(html_root, images_root, sample_n, report: Report) -> None:
    files: list[str] = []
    if os.path.isdir(html_root):
        for d in list(iter_question_dirs(html_root))[:sample_n]:
            p = os.path.join(d, "index.html")
            if os.path.isfile(p):
                files.append(p)
    if os.path.isdir(images_root):
        try:
            imgs = [e.path for e in os.scandir(images_root) if e.is_file()]
        except OSError:
            imgs = []
        for i in sample_index(len(imgs), max(sample_n // 2, 1)):
            files.append(imgs[i])
    if not files:
        report.warn("mime_sample", "no files to sample", {"sampled": 0})
        return
    mismatches = []
    checked = 0
    for path in files:
        ext = os.path.splitext(path)[1].lower()
        try:
            with open(path, "rb") as fh:
                head = fh.read(16)
        except OSError:
            continue
        if ext in (".html", ".htm"):
            if head.lstrip()[:1] == HTML_MAGIC:
                checked += 1
            else:
                mismatches.append(os.path.basename(path))
        elif ext in MAGIC:
            expected = MAGIC[ext]
            if ext == ".webp":
                ok = head.startswith(b"RIFF") and head[8:12] == b"WEBP"
            elif ext == ".svg":
                ok = head.lstrip()[:1] == b"<"
            else:
                ok = head.startswith(expected)
            if ok:
                checked += 1
            else:
                mismatches.append(os.path.basename(path))
    if mismatches:
        report.fail(
            "mime_sample",
            f"{len(mismatches)} sampled files have magic bytes inconsistent with their "
            f"extension (e.g. {mismatches[0]})",
            {"checked": checked, "mismatches": len(mismatches)},
        )
    else:
        report.pass_("mime_sample", f"{checked} sampled files match extension magic bytes", {"checked": checked, "mismatches": 0})


def check_input_hashes(dirs, sample_n, strict, report: Report) -> None:
    """Verify manifest *_sha256 fields against sibling files (sampled or all)."""
    if strict:
        sampled = dirs
    else:
        sampled = [dirs[i] for i in sample_index(len(dirs), sample_n)]
    if not sampled:
        report.warn("input_hashes", "no question dirs to sample", {"checked": 0})
        return
    checked = 0
    mismatches = []
    unreadable = 0
    for d in sampled:
        manifest_path = os.path.join(d, "manifest.json")
        try:
            with open(manifest_path, encoding="utf-8") as fh:
                manifest = json.load(fh)
        except (OSError, json.JSONDecodeError):
            unreadable += 1
            continue
        if not isinstance(manifest, dict):
            unreadable += 1
            continue
        for key, expected in manifest.items():
            if not key.endswith("_sha256") or not isinstance(expected, str) or not expected:
                continue
            base = key[: -len("_sha256")]
            target = None
            for candidate in (base, f"{base}.html", f"{base}.json"):
                p = os.path.join(d, candidate)
                if os.path.isfile(p):
                    target = p
                    break
            if target is None:
                continue
            checked += 1
            try:
                actual = sha256_file(target)
            except OSError:
                mismatches.append(f"{os.path.basename(d)}/{os.path.basename(target)} (unreadable)")
                continue
            if actual != expected.lower():
                mismatches.append(f"{os.path.basename(d)}/{os.path.basename(target)}")
    if unreadable:
        report.warn("input_hashes_unreadable", f"{unreadable} sampled manifests unreadable", {"unreadable": unreadable})
    if not checked:
        report.info(
            "input_hashes",
            f"no *_sha256 hash fields in sampled manifests to verify (sampled {len(sampled)})",
            {"checked": 0},
        )
        return
    if mismatches:
        report.fail(
            "input_hashes",
            f"{len(mismatches)} hash mismatch(es) (e.g. {mismatches[0]})",
            {"checked": checked, "mismatches": len(mismatches)},
        )
    else:
        report.pass_("input_hashes", f"{checked} recorded *_sha256 values match actual files", {"checked": checked, "mismatches": 0})


# --- main --------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage-dir", required=True, help="restored stage directory")
    ap.add_argument("--manifest", help="page manifest JSONL recorded during update")
    ap.add_argument("--placeholder-spec", default=None, help="placeholder spec JSON (default: data/placeholder-spec.json)")
    ap.add_argument("--redis-url", default=os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"), help="redis:// URL (loopback only)")
    ap.add_argument("--redis-cardinality", action="append", default=[], metavar="KEY", help="required SCARD key (repeatable)")
    ap.add_argument("--redis-zcard", action="append", default=[], metavar="KEY", help="required ZCARD key (repeatable)")
    ap.add_argument("--out", help="write JSON report to this path (atomic)")
    ap.add_argument("--sample", type=int, default=100, help="sampling size for content checks (default 100)")
    ap.add_argument("--strict", action="store_true", help="check ALL question dirs, not a sample")
    ap.add_argument("--skip-redis", action="store_true", help="skip the redis cardinality checks entirely")
    args = ap.parse_args()

    if args.sample <= 0:
        print("ERROR: --sample must be > 0", file=sys.stderr)
        return 2

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec_path = args.placeholder_spec or os.path.join(repo_root, "data", "placeholder-spec.json")

    report = Report()
    html_root = os.path.join(args.stage_dir, "html")
    images_root = os.path.join(args.stage_dir, "images")

    check_question_count(html_root, args.manifest, report)

    dirs = list(iter_question_dirs(html_root))
    if dirs:
        check_missing_manifests(dirs, report)
        check_duplicate_zim_paths(dirs, report)
        check_answer_redirects(dirs, args.sample, args.strict, report)
        check_image_refs(dirs, images_root, args.sample, args.strict, report)
        check_input_hashes(dirs, args.sample, args.strict, report)
    else:
        report.warn("missing_manifests", "no question dirs to audit", {"missing": 0, "total": 0})
        report.warn("duplicate_zim_path", "no question dirs to audit", {"duplicates": 0})

    check_placeholders(images_root, spec_path, report)
    check_mime_sample(html_root, images_root, args.sample, report)

    if not args.skip_redis:
        probe = RedisProbe(args.redis_url)
        check_redis_cardinalities(probe, args.redis_cardinality, args.redis_zcard, report)
    else:
        report.info("redis", "redis checks skipped (--skip-redis)")

    summary = report.summary()
    print(f"RESULT: {summary['result']} "
          f"(pass={summary['pass']} warn={summary['warn']} fail={summary['fail']} info={summary['info']})")

    full = {
        "tool": "audit_stage.py",
        "stage_dir": args.stage_dir,
        "generated_at": utc_now(),
        "checks": report.checks,
        "summary": summary,
    }
    if args.out:
        write_report_atomic(args.out, full)
    return 1 if summary["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())