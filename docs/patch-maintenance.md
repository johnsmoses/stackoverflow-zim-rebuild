# Patch maintenance

The whole rebuild hinges on a patched sotoki. This document defines how
patches are captured, ordered, applied, and validated.

## Pinned base

- Upstream: `https://github.com/openzim/sotoki`
- Base commit: `157ca9a1c73e3e6349f1c80bb03e058355aef743` (v3.0.2),
  recorded in `sotoki.lock` as `157ca9a`.
- Series location: `patches/sotoki/` (status `captured` in `sotoki.lock`).

## Capture

- Captured from the working **site-packages install** of sotoki
  (`.../site-packages/sotoki/`), diffed against the upstream checkout at
  `157ca9a`. See `patches/sotoki/README.md` for the patch series
  provenance; the raw capture evidence is retained locally, not published.
- A **12-file allowlist** enforced by `scripts/capture_sotoki_patches.py`:
  `scraper.py`, `posts.py`, `utils/database/posts.py`, `utils/html.py`,
  `entrypoint.py`, `css.py`, `users.py`, `utils/database/redisdb.py`,
  `tags.py`, `context.py`, `renderer.py`, `utils/preparation.py`.
- Nothing outside the allowlist may enter the series.
- Captured diffs are converted into a **5-patch series** in
  `patches/sotoki/`, numbered in dependency order, each generated with
  `git format-patch` semantics:

  1. `0001` — staging/assemble/resume/content-threads CLI options + context
     fields (`entrypoint.py`, `context.py`)
  2. `0002` — staging write path with manifest checkpointing (`posts.py`,
     `utils/database/posts.py`)
  3. `0003` — assemble-only ZIM builder, title sanitization, staged HTML
     fixes (`scraper.py`, `renderer.py`, `utils/html.py`)
  4. `0004` — offline asset loading + Redis-less tag/user fallbacks
     (`css.py`, `users.py`, `tags.py`)
  5. `0005` — bounded sort buffer + throttled Redis pipeline flushes
     (`utils/preparation.py`, `utils/database/redisdb.py`)

### Parameterization

The raw capture contained machine-specific operational hacks; these were
parameterized before the series was committed so the patches are portable:

- Hard-coded asset dir `/home/jmoses/sotoki-build/assets` → staged-assets
  path from `context.stage_dir` (fallback `/tmp/sotoki-assets`).
- Hard-coded GNU sort `--temporary-directory` → only when `SOTOKI_SORT_TMP`
  env var is set (`--buffer-size 32G` kept unconditionally).
- Hard-coded progress total `24152540` → computed from Redis set count with
  the constant as fallback.
- All `shared.creator.can_finish = True` corruption-override resets removed
  (7 occurrences); corrupt creators now fail explicitly at `finish()`.
- Staging-tree walk skips are counted and logged instead of silent `continue`.
- `import os`/`import json` at module top; duplicate in-function import removed.

See `patches/sotoki/README.md` for the full edit list (A–F).

## Ordering & application

- Patches apply **in numbered order** on top of `157ca9a` (see
  `patches/sotoki/series`).
- Application and verification run through
  `scripts/check_patch_series.sh --package-path DIR --base-commit 157ca9a`.
- Every patch must apply **cleanly**. The reference validation is
  `git am --3way patches/sotoki/0001-*.patch ... 0005-*.patch` on a fresh
  clone checked out at `157ca9a`; the applied tree must be identical to the
  captured worktree, and `python3 -m py_compile` must pass on all files.
- A patch that requires fuzz or whitespace toleration is a failed patch.

## Upstream drift policy

- `sotoki.lock`'s `base_commit` must **not** be bumped without re-validating
  the entire series against the new base.
- If upstream sotoki moves and a patch no longer applies, the failure must
  be **explicit**: `check_patch_series.sh` (or the build) aborts with the
  failing patch named and a non-zero exit. **Never** silently skip a patch,
  fuzz-apply it, or proceed with an unpatched file.
- A newer upstream may *obsolete* a patch (the change was merged). That is
  a deliberate, reviewed decision recorded in `sotoki.lock` notes — not an
  automatic pass.

## Resume semantics

The `--resume` flag added by this series is **same-input resume only**: it
continues a staging/rendering run that was interrupted mid-input with
identical parameters and input data. Snapshot-aware incremental update
(picking up only new/changed StackExchange dumps against an existing staging
tree or ZIM) is a future task and is not covered by this series.

## Reproducibility contract

Given `157ca9a` + `patches/sotoki/` in order, the resulting source must be
functionally equivalent to the site-packages install that produced the
July 2026 baseline (counts in `data/baseline-2026-07.json`).