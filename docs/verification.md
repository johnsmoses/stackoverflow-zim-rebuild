# Verification

`make verify` runs after `assemble`. It audits the stage, the ZIM, and the
baseline deltas. Design principle: **compare against expected deltas, never
strict equality with the July 2026 counts** — the dump has grown since.

## 1. Stage audit

- Question page count matches the manifest recorded during `update`
  (**invariant**: never zero; must equal the page manifest count exactly).
- Every staged image filename in the manifest exists on disk with the
  recorded size/hash; missing or truncated files are reported (manifest
  check, not `--ignore-existing`-style trust).
- Placeholder inventory: count of files matching `data/placeholder-spec.json`
  (size **and** content SHA-256 — size alone is not proof, see the spec).

## 2. ZIM audit

- `zimcheck` on the produced ZIM: integrity, metadata, and internal
  consistency checks; exit non-zero on any failure.
- `zimdump` spot checks: sample question pages render, images resolve, no
  dangling internal links.
- Both `stackoverflow-final.zim` and `stackoverflow-nopic.zim` are audited
  when both are produced.

## 3. Baseline comparison (expected deltas)

Compare against `data/baseline-2026-07.json` (24,152,540 question pages;
4,375,716 staged images; 85,441,337 ZIM entries; 55,250,097 Redis db0 keys;
521,489 initial placeholders; 410,856 recovered / 120,633 unrecoverable).

Expected direction of change, and it must be **explained** in the verify
report:

| Metric | Expected delta |
|---|---|
| question pages | ↑ (dump grows) — must exceed 0 growth and match manifest |
| staged images | ↑ or ≈ (new posts add images) |
| ZIM entries | ↑ (more pages + entries) |
| Redis db0 keys | ↑ (new asset keys) |
| placeholders | ↑ or ↓ (new failures vs. recovery); recovery rate ≈ 78.8% ± tolerance |
| ZIM bytes | ↑ vs. 152,103,236,002 / 73,421,904,273 (larger dump) |

Any metric that moved the *wrong* way (e.g. question page count dropped
sharply, or ZIM entries shrank) is a failure with a required root-cause
investigation.

## 4. Invariants (fail fast, always)

1. Question page count is **never zero** and never shrinks by more than the
   documented deletion delta.
2. Page count == page-manifest count (no orphans, no double-counts).
3. No placeholder-sized file is shipped as a real image without a verified
   content hash (see `data/placeholder-spec.json`).
4. `sotoki.lock` state is `applied` and `check_patch_series.sh` passes
   before any ZIM is declared verifiable.