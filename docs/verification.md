# Verification

`make verify` runs after `assemble`. It audits the stage, the ZIM, and the
baseline deltas. Design principle: **compare against expected deltas, never
strict equality with the July 2026 counts** — the dump has grown since.

Expected-count config lives in `configs/expected-counts.json` (July baseline
counts plus `tolerance`, `large_delta_pct` and the `min_*` sanity
thresholds). `data/baseline-2026-07.json` stays the canonical baseline
reference with provenance; `configs/expected-counts.json` is the machine
readable copy used by the tools.

## 0. Assembly (`bin/assemble`) — preflight, gates, atomic promotion

`bin/assemble [--snapshot-id X] [--flavour full|nopic] [--skip-preflight]
[--skip-zimcheck] [--out-name NAME]`

Preconditions (never skippable):

- `WORK_ROOT` is set and `STAGE_DIR` contains question pages.
- `$WORK_ROOT/.sotoki-rebuild-ok` exists — a verified baseline restore is
  the precondition for a trustworthy build. Missing marker ⇒ FAIL with
  "baseline not restored".

Preflight (skip with `--skip-preflight`):

1. stage question page count > 0;
2. redis reachable (`bin/redis status` reports a running instance);
3. `verify_snapshot.py --require-redis` passes;
4. free space on `OUTPUT_DIR` ≥ 1.5× the expected output size (from
   `configs/expected-counts.json`).

Locking and output hygiene (H2):

- A non-blocking `flock -n $OUTPUT_DIR/.assemble.lock` serializes builds; a
  concurrent assemble is refused.
- The ZIM is built into `$TMP_DIR` (sotoki's `--tmp-dir`), then moved to
  the **final output directory** as `$OUT_NAME.partial.$$` so the promotion
  rename is a same-filesystem atomic `mv`. Temp/output files are **never**
  placed inside the stage directory (H5).
- Symlinked or non-regular output paths are rejected (`test -L` / `test -f`
  on the resolved output path) before anything is written.

Gate-then-promote flow (the `.partial → verify → rename` contract):

```
sotoki ... --assemble-only --stage-dir $STAGE_DIR --keep --keep-redis
    (nopic adds --without-images)
        ↓ exit 0
$OUTPUT_DIR/$OUT_NAME.partial.$$     ← unique partial, same filesystem
        ↓ ALL of:
  scripts/audit_zim.py --zim $PARTIAL   (runs zimcheck when available,
                                         unless --skip-zimcheck)
  scripts/compare_baseline.py --zim $PARTIAL --stage-dir $STAGE_DIR
        ↓ every gate PASS
atomic mv $OUT_NAME.partial.$$ → $OUT_NAME
```

A failed build removes the failed partial and exits non-zero; a prior good
ZIM at the final name is **never** replaced, because the final path is only
touched by the last atomic `mv`.

The run manifest `$OUT_DIR/$OUT_NAME.manifest.json` is written atomically
(temp file + rename) and records: snapshot id, flavour, start/end time,
input stage counts (question pages, images), output bytes + SHA-256, sotoki
path/commit, python version, the exact sotoki args, and every gate result
with its report path.

## 1. Stage audit (`scripts/audit_stage.py`)

```
audit_stage.py --stage-dir DIR [--manifest MANIFEST_JSONL]
               [--placeholder-spec SPEC_JSON] [--redis-url URL]
               [--redis-cardinality KEY]... [--redis-zcard KEY]...
               [--out REPORT_JSON] [--sample N] [--strict] [--skip-redis]
```

- Question page count matches the manifest recorded during `update`
  (**invariant**: never zero; must equal the page manifest count exactly
  when `--manifest` is given).
- Every question dir must carry a `manifest.json` (missing siblings FAIL).
- Duplicate `zim_path` values across manifests FAIL.
- `answer_redirects` coverage (sampled, or all with `--strict`): missing or
  non-list values are failures.
- Image references: every `/images/([0-9a-f]{16,32})` reference in sampled
  page content must resolve to a staged image file; missing references FAIL.
- Placeholder inventory: files matching `data/placeholder-spec.json` by
  size **and** content SHA-256 — size alone is not proof (see the spec).
  When the spec has no sha256 yet, size-only detection is refused and the
  check degrades to a WARN.
- MIME sampling: sampled files' magic bytes must match their extension.
- Input hashes (sampled, or all with `--strict`): any `*_sha256` field in a
  page manifest naming a sibling file must match that file's real SHA-256.

### Redis cardinalities (H1 semantics)

Cardinality checks run only when keys are explicitly configured with
`--redis-cardinality KEY` (SCARD) / `--redis-zcard KEY` (ZCARD) — e.g.
`--redis-cardinality stage:done:questions`. Every configured key is
**required**:

- redis-cli is invoked via `subprocess` with a **fixed argv** (never a
  shell); the command whitelist is **PING / SCARD / ZCARD only**;
- connection timeout 2 s, subprocess timeout 10 s;
- integer responses are **strictly parsed** — a query failure (non-zero
  exit, timeout, unparseable response) is distinguished from a
  legitimately-zero cardinality and both FAIL for a required key (zero is
  never "warned away" or coerced into success);
- only loopback endpoints are accepted (`redis://127.0.0.1` or
  `redis://localhost`); anything else is rejected;
- the full URL (and any credentials in it) is never logged — only the
  sanitized `host:port` endpoint.

## 2. ZIM audit (`scripts/audit_zim.py`)

```
audit_zim.py --zim PATH [--zimcheck-bin PATH] [--zimdump-bin PATH]
             [--baseline JSON] [--out REPORT_JSON] [--sample N]
             [--allow-non-zim] [--no-zimcheck]
```

- **Exact magic check (H3):** the first 4 bytes are read and unpacked as a
  little-endian 32-bit integer (`struct '<I'`) and must equal `0x044D495A`
  — the bytes `b"ZIM\x04"` interpreted numerically. This is not merely an
  ASCII "ZIM" prefix check. FAILs without `--allow-non-zim`.
- Header parse: the 80-byte header yields `entry_count` (uint32 at offset
  24) plus version/uuid/main-page metadata.
- Entry count sanity: > 0, and greater than the 90% question floor derived
  from the baseline reference (`--baseline`, default
  `configs/expected-counts.json`).
- Entry-level checks via `zimdump` if present, else `libzim` if importable:
  homepage, `/questions/`, `/tags/` and `/users/` existence, and sampled
  content extraction (non-empty, valid UTF-8).
- `zimcheck` runs automatically when the binary is available (or
  `--zimcheck-bin`); its output is embedded in the report and a non-zero
  exit FAILs the audit. `--no-zimcheck` opts out (what `bin/assemble
  --skip-zimcheck` passes).

### Degraded-verification WARN policy

When neither `zimdump` nor an importable `libzim` exists, entry-level
checks cannot run. The audit records `degraded: true` in the report and
emits a WARN for each skipped entry-level check (magic + header + count
checks still run). A degraded audit exits 0 unless a completed check
failed — the report must be read to know the verification depth. This
policy exists so a toolchain without libzim can still gate on the
structural invariants that do not require entry access.

## 3. Baseline comparison (`scripts/compare_baseline.py`)

```
compare_baseline.py --zim PATH [--stage-dir DIR] [--baseline JSON]
                    [--tolerance PCT] [--out REPORT_JSON]
                    [--allow-large-delta] [--allow-non-zim]
```

Compare against `data/baseline-2026-07.json` (24,152,540 question pages;
4,375,716 staged images; 85,441,337 ZIM entries; 55,250,097 Redis db0 keys;
521,489 initial placeholders; 410,856 recovered / 120,633 unrecoverable;
152,103,236,002 / 73,421,904,273 bytes for full/nopic).

| Metric | Rule | Status on breach |
|---|---|---|
| ZIM bytes (full or nopic per filename) | FAIL only when below **1% of baseline** (implausibly small) | ordinary growth is informational and **never** fails |
| ZIM entries | FAIL when 0, or when the absolute delta exceeds `large_delta_pct` (default 50%) **without `--allow-large-delta`** — unexplained **growth AND shrinkage** both FAIL | pass when within limit |
| stage question pages | FAIL when 0 (or below `min_question_pages`); outside `tolerance` (2%) is a WARN — exactness is `audit_stage.py`'s job | pass/fail as above |
| stage images | FAIL when 0; outside tolerance is a WARN | pass/fail as above |
| placeholders | report-only vs `initial_placeholders` + `unrecoverable` — never gated | INFO |

Report rows carry expected vs actual columns; exit is non-zero on any FAIL.

## 4. Invariants (fail fast, always)

1. Question page count is **never zero** and never shrinks by more than the
   documented deletion delta; a ZIM entry count may not drop by more than
   50% (or grow by more than 50%) without an explained root cause
   (`--allow-large-delta` is the explicit override).
2. Page count == page-manifest count (no orphans, no double-counts).
3. No placeholder-sized file is shipped as a real image without a verified
   content hash (see `data/placeholder-spec.json`).
4. `sotoki.lock` state is `applied` and `check_patch_series.sh` passes
   before any ZIM is declared verifiable.
5. A ZIM is only promoted over a prior good ZIM after every structural
   check and baseline comparison pass (see section 0).