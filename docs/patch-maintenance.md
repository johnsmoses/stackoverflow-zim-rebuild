# Patch maintenance

The whole rebuild hinges on a patched sotoki. This document defines how
patches are captured, ordered, applied, and validated.

## Pinned base

- Upstream: `https://github.com/openzim/sotoki`
- Base commit: `157ca9a` (recorded in `sotoki.lock`)

The July 2026 build used a patched copy installed in a site-packages
environment. **Capture is pending** (`patch_series.status: capture-pending`
in `sotoki.lock`); the patch series will live in `patches/sotoki/`.

## Capture (Task 3, planned)

- Capture from the working **site-packages install** of sotoki
  (`.../site-packages/sotoki/`), diffed against the upstream checkout at
  `157ca9a`.
- A **12-file allowlist** is enforced by
  `scripts/capture_sotoki_patches.py`:
  `scraper.py`, `posts.py`, `utils/database/posts.py`, `utils/html.py`,
  `entrypoint.py`, `css.py`, `users.py`, `utils/database/redisdb.py`,
  `tags.py`, `context.py`, `renderer.py`, `utils/preparation.py`.
- Nothing outside the allowlist may enter the series; the capture tool
  reports (dry-run) which files exist and their line counts before any
  writing is implemented.
- Captured diffs are recorded as `.patch` files in `patches/sotoki/`,
  numbered in dependency order (e.g. `0001-...patch`, `0002-...patch`).

## Ordering & application

- Patches apply **in numbered order** on top of `157ca9a`, each generated
  with `git format-patch` semantics so `git apply`/`git am` can verify them.
- Application and verification run through
  `scripts/check_patch_series.sh --package-path DIR --base-commit 157ca9a`
  (dry-run scaffolding today; exits 0 in dry-run mode without touching
  anything).
- Every patch must apply **cleanly** with `--3way` off and whitespace
  errors rejected. A patch that requires fuzz is a failed patch.

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

## Reproducibility contract

Given `157ca9a` + `patches/sotoki/` in order, the resulting source must be
functionally equivalent to the site-packages install that produced the
July 2026 baseline (counts in `data/baseline-2026-07.json`).