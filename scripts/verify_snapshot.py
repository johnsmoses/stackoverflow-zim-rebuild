#!/usr/bin/env python3
"""verify_snapshot.py — verify a restored baseline snapshot.

Checks (each prints PASS/FAIL/WARN; exit is non-zero if any check FAILs):

  1. stage question pages: depth-2 dirs under <stage>/html/<shard>/<post_id>,
     must be > 0 (invariant) and within tolerance of baseline 'questions'.
  2. stage images: files under <stage>/images, must be > 0 (invariant) and
     within tolerance of baseline 'staged_images'.
  3. structure: <stage>/html contains 2-char hex shard dirs and sample
     index.html files exist.
  4. redis: DBSIZE within tolerance of baseline 'redis_db0_keys' (WARN when
     unreachable unless --require-redis, which FAILs instead). When the
     baseline is a full July-scale build (>= 10M questions), the
     stage:done:questions set must contain more than 1000 entries.

Redis access uses redis-py when importable, otherwise redis-cli via
subprocess. No third-party imports are required.

usage: verify_snapshot.py --stage <dir> --redis-url <url> --baseline <json>
                          [--tolerance 0.02] [--require-redis] [--skip-redis]
"""
import argparse
import json
import os
import re
import subprocess
import sys

try:
    import redis as _redis_py  # type: ignore
except ImportError:  # pragma: no cover - redis-py optional
    _redis_py = None


class RedisClient:
    """Thin redis access: redis-py when available, redis-cli otherwise."""

    def __init__(self, url):
        self.url = url
        self._conn = None
        if _redis_py is not None:
            try:
                self._conn = _redis_py.from_url(
                    url, socket_connect_timeout=3, socket_timeout=5
                )
            except Exception:  # noqa: BLE001
                self._conn = None

    def _raw(self, *args):
        if self._conn is not None:
            result = self._conn.execute_command(*args)
            if result is True:  # redis-py maps PONG to a bool
                return "PONG"
            if result is False:
                return ""
            if isinstance(result, bytes):
                return result.decode()
            return str(result)
        out = subprocess.run(
            ["redis-cli", "-u", self.url, "--raw", *args],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if out.returncode != 0:
            raise RuntimeError(f"redis-cli {' '.join(args)}: {out.stderr.strip()}")
        return out.stdout.strip()

    def ping(self):
        try:
            return self._raw("PING") == "PONG"
        except Exception:  # noqa: BLE001
            return False

    def dbsize(self):
        try:
            return int(self._raw("DBSIZE"))
        except Exception:  # noqa: BLE001
            return None

    def scard(self, key):
        try:
            return int(self._raw("SCARD", key))
        except Exception:  # noqa: BLE001
            return None


def load_baseline(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    counts = data.get("counts", data)
    return {
        "questions": int(counts["questions"]),
        "staged_images": int(counts["staged_images"]),
        "redis_db0_keys": int(counts["redis_db0_keys"]),
    }


def within_tolerance(expected, actual, tolerance):
    if expected == 0:
        return actual == 0
    return abs(actual - expected) / expected <= tolerance


def count_question_dirs(html_root):
    """Count stage/html/<shard>/<post_id> directories (depth-2 dirs)."""
    total = 0
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


def count_image_files(images_root):
    total = 0
    for root, _dirs, files in os.walk(images_root):
        total += len(files)
    return total


def check_structure(stage, report):
    html = os.path.join(stage, "html")
    if not os.path.isdir(html):
        report.append(("FAIL", "structure", "stage/html missing"))
        return
    try:
        shards = [
            d for d in os.listdir(html) if os.path.isdir(os.path.join(html, d))
        ]
    except OSError as exc:
        report.append(("FAIL", "structure", f"cannot list stage/html: {exc}"))
        return
    hex_shards = [s for s in shards if re.fullmatch(r"[0-9a-f]{2}", s)]
    if not hex_shards:
        report.append(("FAIL", "structure", "stage/html has no 2-char hex shard dirs"))
        return
    report.append(("PASS", "structure", f"{len(hex_shards)} hex shard dirs found"))
    found = 0
    for shard in sorted(hex_shards)[:3]:
        shard_path = os.path.join(html, shard)
        try:
            posts = sorted(os.listdir(shard_path))[:3]
        except OSError:
            continue
        for post in posts:
            if os.path.isfile(os.path.join(shard_path, post, "index.html")):
                found += 1
    if found == 0:
        report.append(("FAIL", "structure", "no sample index.html found under shard dirs"))
    else:
        report.append(("PASS", "structure", f"{found} sample index.html files found"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage", required=True, help="restored stage directory")
    ap.add_argument(
        "--redis-url",
        default=os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"),
        help="redis:// URL (loopback); defaults to $REDIS_URL or redis://127.0.0.1:6379/0",
    )
    ap.add_argument("--baseline", required=True, help="baseline JSON with counts")
    ap.add_argument("--tolerance", type=float, default=0.02)
    ap.add_argument("--require-redis", action="store_true", help="FAIL if redis is unreachable")
    ap.add_argument("--skip-redis", action="store_true", help="skip all redis checks")
    args = ap.parse_args()

    if not 0 < args.tolerance < 1:
        print(f"WARN  tolerance {args.tolerance} outside (0,1); continuing anyway")
    if args.require_redis and args.skip_redis:
        print("ERROR --require-redis and --skip-redis are mutually exclusive", file=sys.stderr)
        return 2

    baseline = load_baseline(args.baseline)
    report = []

    # --- stage counts -------------------------------------------------------
    questions = count_question_dirs(os.path.join(args.stage, "html"))
    images = count_image_files(os.path.join(args.stage, "images"))
    if questions <= 0:
        report.append(("FAIL", "stage_questions", f"count={questions} (invariant: > 0)"))
    elif within_tolerance(baseline["questions"], questions, args.tolerance):
        report.append(("PASS", "stage_questions", f"count={questions} within {args.tolerance:.0%} of baseline {baseline['questions']}"))
    else:
        report.append(("FAIL", "stage_questions", f"count={questions} outside {args.tolerance:.0%} of baseline {baseline['questions']}"))

    if images <= 0:
        report.append(("FAIL", "stage_images", f"count={images} (invariant: > 0)"))
    elif within_tolerance(baseline["staged_images"], images, args.tolerance):
        report.append(("PASS", "stage_images", f"count={images} within {args.tolerance:.0%} of baseline {baseline['staged_images']}"))
    else:
        report.append(("FAIL", "stage_images", f"count={images} outside {args.tolerance:.0%} of baseline {baseline['staged_images']}"))

    check_structure(args.stage, report)

    # --- redis --------------------------------------------------------------
    if not args.skip_redis:
        client = RedisClient(args.redis_url)
        reachable = client.ping()
        if not reachable:
            if args.require_redis:
                report.append(("FAIL", "redis", f"unreachable at {args.redis_url} (--require-redis)"))
            else:
                report.append(("WARN", "redis", f"unreachable at {args.redis_url}; skipping redis checks"))
        else:
            dbsize = client.dbsize()
            expected = baseline["redis_db0_keys"]
            if dbsize is None:
                report.append(("WARN", "redis_dbsize", "could not read DBSIZE"))
            elif within_tolerance(expected, dbsize, args.tolerance):
                report.append(("PASS", "redis_dbsize", f"dbsize={dbsize} within {args.tolerance:.0%} of baseline {expected}"))
            else:
                report.append(("FAIL", "redis_dbsize", f"dbsize={dbsize} outside {args.tolerance:.0%} of baseline {expected}"))
            if baseline["questions"] >= 10_000_000:
                scard = client.scard("stage:done:questions")
                if scard is None:
                    report.append(("FAIL", "redis_stage_done", "could not read SCARD stage:done:questions"))
                elif scard > 1000:
                    report.append(("PASS", "redis_stage_done", f"SCARD stage:done:questions={scard} > 1000"))
                else:
                    report.append(("FAIL", "redis_stage_done", f"SCARD stage:done:questions={scard} <= 1000"))
            else:
                report.append(("WARN", "redis_stage_done", f"small test baseline (questions={baseline['questions']}); SCARD stage:done:questions check skipped"))

    # --- summary -------------------------------------------------------------
    failed = 0
    for status, check, detail in report:
        print(f"{status:<4} {check:<18} {detail}")
        if status == "FAIL":
            failed += 1
    if failed:
        print(f"RESULT: FAIL ({failed} failed checks)")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())