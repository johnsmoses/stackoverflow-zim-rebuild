# sotoki Patch Series

Maintainable 5-patch series reconstructing the July 2026 site-packages
sotoki install from pristine upstream `157ca9a`.

## Base commit

- Upstream: `https://github.com/openzim/sotoki`
- Base: `157ca9a1c73e3e6349f1c80bb03e058355aef743` (v3.0.2)
- Apply order: `series` file, 0001 → 0005. Apply with `git am --3way`
  (or `git apply` per patch; the series was validated with `git am`).

## Patch overview

| Patch | Commit message | Files |
|-------|----------------|-------|
| 0001 | Add staging/assemble/resume/content-threads CLI options and context fields | entrypoint.py, context.py |
| 0002 | Stage rendered questions to filesystem with manifest checkpointing | posts.py, utils/database/posts.py |
| 0003 | Add assemble-only ZIM builder, title sanitization, staged HTML fixes | scraper.py, renderer.py, utils/html.py |
| 0004 | Support offline asset loading and Redis-less tag/user fallbacks | css.py, users.py, tags.py |
| 0005 | Bound sort buffer and throttle Redis pipeline flushes | utils/preparation.py, utils/database/redisdb.py |

All patches apply on `157ca9a` in order; the result must be functionally
equivalent to the captured install (`capture/installed/`, checksums in
`capture/MANIFEST.txt`).

## Parameterization edits (applied on top of the raw capture)

The captured diffs contained machine-specific operational hacks. These were
parameterized in commit 0003/0005 so the series is portable:

- **A (scraper.py, 2 places):** hard-coded asset dir
  `/home/jmoses/sotoki-build/assets` → `Path(context.stage_dir) / "assets"`
  when `context.stage_dir` is set, else `/tmp/sotoki-assets`.
- **B (utils/preparation.py):** hard-coded
  `--temporary-directory=/home/jmoses/sotoki-build/sorttmp` removed; GNU sort
  gets `--temporary-directory` only when `SOTOKI_SORT_TMP` env var is set.
  `--buffer-size 32G` is kept unconditionally.
- **C (scraper.py assemble_zim):** hard-coded progress total `24152540` →
  computed from Redis set count with the constant as fallback.
- **D (scraper.py):** all `shared.creator.can_finish = True` corruption-override
  resets removed (7 occurrences — add_assets loop, CSS, sprite placeholders,
  assemble_zim asset-failure path). Each except block now logs a warning only;
  a corrupted creator is no longer reset to True, so corruption surfaces
  explicitly at `finish()` instead of being masked.
- **E (scraper.py assemble_zim walk):** bare `except ... continue` in the
  staging-tree walk now counts and debug-logs skipped pages (`skipped` counter
  logged after the loop).
- **F (scraper.py):** `import os` / `import json` at module top; duplicate
  in-function `import os` removed.

## Raw capture reference

- Installed files (verbatim): `../../capture/installed/`
- Unified diffs (pristine → installed): `../../capture/diffs/`
- SHA-256 checksums of every installed file and diff:
  `../../capture/MANIFEST.txt`

## Resume semantics

`--resume` is **same-input resume only**: it continues a staging/rendering run
that was interrupted mid-input with identical parameters and input data.
Snapshot-aware incremental update (picking up only new/changed StackExchange
dumps against an existing staging tree or ZIM) is a future task and is **not**
supported by this series.

## Upstream drift policy

- Never bump `base_commit` without re-validating the full series.
- A patch that no longer applies on a newer upstream is an **explicit
  failure**: abort and name the failing patch. Never fuzz-apply, silently
  skip, or proceed with an unpatched file.
- If upstream merges one of these changes, the obsolete patch may be dropped
  only as a deliberate, reviewed decision recorded in `sotoki.lock` notes.