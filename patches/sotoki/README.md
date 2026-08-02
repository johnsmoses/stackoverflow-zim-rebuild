# sotoki Patch Series

> **License: GPL-3.0-only — NOT CC0.** This patch series is a derivative
> work of [openzim/sotoki](https://github.com/openzim/sotoki) (GPL-3.0),
> base commit `157ca9a1c73e3e6349f1c80bb03e058355aef743` (v3.0.2). The
> patches are distributed under the **GNU General Public License, version 3
> only** (the same terms as upstream sotoki); the full license text is in
> [`LICENSES/GPL-3.0-only.txt`](../../LICENSES/GPL-3.0-only.txt). They are
> **not** CC0: the repository-root `LICENSE` (CC0-1.0) covers only the
> rebuild-kit tooling, not this directory. See
> [`LICENSE.scope.md`](../../LICENSE.scope.md) and
> [`NOTICE-ATTRIBUTION.md`](../../NOTICE-ATTRIBUTION.md).

Maintainable 9-patch series reconstructing the July 2026 site-packages
sotoki install from pristine upstream `157ca9a`, plus the snapshot-aware
incremental update mode (Task 4).

## Base commit

- Upstream: `https://github.com/openzim/sotoki`
- Base: `157ca9a1c73e3e6349f1c80bb03e058355aef743` (v3.0.2)
- Apply order: `series` file, 0001 → 0009. Apply with `git am --3way`
  (or `git apply` per patch; the series was validated with `git am`).

## Patch overview

| Patch | Commit message | Files |
|-------|----------------|-------|
| 0001 | Add staging/assemble/resume/content-threads CLI options and context fields | entrypoint.py, context.py |
| 0002 | Stage rendered questions to filesystem with manifest checkpointing | posts.py, utils/database/posts.py |
| 0003 | Add assemble-only ZIM builder, title sanitization, staged HTML fixes | scraper.py, renderer.py, utils/html.py |
| 0004 | Support offline asset loading and Redis-less tag/user fallbacks | css.py, users.py, tags.py |
| 0005 | Bound sort buffer and throttle Redis pipeline flushes | utils/preparation.py, utils/database/redisdb.py |
| 0006 | Add snapshot-aware incremental CLI options and context fields | entrypoint.py, context.py |
| 0007 | Add canonical per-question fingerprint and render-contract incremental staging | posts.py |
| 0008 | Run metadata passes and prune stale pages in incremental mode | scraper.py |
| 0009 | Support local dump archives via --archive-dir | archives.py, scraper.py, tests/incremental/test_incremental.py |

Patches 0001–0005 apply on `157ca9a` in order; the result must be
functionally equivalent to the captured install (`capture/installed/`,
checksums in `capture/MANIFEST.txt`). Patches 0006–0009 build on top and
implement the snapshot-aware incremental update mode.

**Series dates:** all nine patches were authored on 2026-08-01 (UTC
capture 2026-08-01T22:03:15Z; patch `Date` headers Sat, 1 Aug 2026, local
build time). 0001–0005 were captured from the July 2026 site-packages
install; 0006–0009 were written on top for the incremental update mode.

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

## Incremental update mode (0006–0009)

Four commits implementing the snapshot-aware incremental update mode
(planner amendments A1–A6 + independent prune hardenings H1–H5). Key concepts:

- **Snapshot model.** Each run carries an immutable `--snapshot-id`
  (`^[A-Za-z0-9._-]+$`). The per-question staging directory is
  `stage/html/<hex-shard>/<post_id>/` with a `manifest.json` next to
  `index.html`.
- **Per-snapshot build dir (A1).** With `--incremental`, the build dir name
  includes the snapshot id and prepared dump files (`posts_complete.xml`,
  `Tags.xml`) are always re-extracted from the selected archive — stale
  prepared files are never reused. With `--archive-dir`, the local dump
  archive is copied into the build dir and its SHA-256 is stamped to
  `archive.sha256`.
- **Fingerprint + render contract (A4).** `RENDER_CONTRACT_VERSION = "1"` in
  `posts.py`. `fingerprint_post()` hashes every renderer-consumed field
  (question, comment, answer, links; permutation-invariant via sorted Ids /
  Tags / json keys). A staged page is skipped (bytes + mtime untouched) only
  when the existing manifest's `render_contract_version` matches AND
  `source_sha256` equals the current fingerprint AND `index.html` exists.
  Incremental manifests are `schema_version: 2`; legacy full-build manifests
  stay `schema_version: 1`.
- **Seen sets.** Each snapshot has its own `snapshot:<id>:seen` set (fresh
  `DEL` at the start of the pass, `SADD` per staged page — including
  unchanged skipped pages, so prune never removes them). `stage:done:questions`
  remains the `--resume` checkpoint for legacy full builds only.
- **Metadata reset (A2).** Incremental rebuilds the scraper dataset from an
  EMPTY DB: `FLUSHDB` (never `FLUSHALL`) runs before the tag/question
  metadata passes even with `--keep-redis`, with the sanitized Redis endpoint
  and logical DB logged. The logical DB is dedicated to sotoki.
- **Prune (A3, H1–H5, opt-in via `--prune-missing`).** After a COMPLETELY
  successful full scan (input exhausted, all workers joined, zero item
  failures, seen-set writes committed), any staged v2 page absent from the
  current seen set is a candidate — regardless of the manifest's previous
  snapshot id. v1 pages are never touched. Hardening: exclusive
  `stage_root/.incremental.lock` (flock + pid, concurrent runs rejected);
  per-candidate path validation (resolved path must equal
  `stage_root/html/<2-char hex shard>/<post_id>`, manifest `post_id` must
  equal the directory name, no symlinks anywhere in the path, containment
  via resolved-path prefix); the prune plan is journaled to
  `prune-plan-<snapshot>.jsonl` with fsync BEFORE any removal, then per-item
  results go to `prune-results-<snapshot>.jsonl`; on the first failure the
  remaining candidates are quarantined. Production prune policy requires
  independent human review before use.
- **User-card policy (A5).** Staged question user cards are HISTORICAL:
  `OwnerDisplayName` is in the fingerprint so name edits re-render, but
  reputation/badge changes do not trigger re-render. See
  `docs/update-runbook.md`.

## Raw capture reference

- Installed files (verbatim): `../../capture/installed/`
- Unified diffs (pristine → installed): `../../capture/diffs/`
- SHA-256 checksums of every installed file and diff:
  `../../capture/MANIFEST.txt`

## Resume semantics

`--resume` is **same-input resume only**: it continues a staging/rendering run
that was interrupted mid-input with identical parameters and input data.
Snapshot-aware incremental update (picking up only new/changed StackExchange
dumps against an existing staging tree or ZIM) is implemented by commits
0006–0009 (`--incremental --snapshot-id ...`).

## Tests

`tests/incremental/test_incremental.py` (committed with 0009) covers the
incremental mode with a dict-backed fake database — no redis, libzim, or
network required. Run with:

```bash
python3 -m pytest tests/incremental/ -q
```

## Upstream drift policy

- Never bump `base_commit` without re-validating the full series.
- A patch that no longer applies on a newer upstream is an **explicit
  failure**: abort and name the failing patch. Never fuzz-apply, silently
  skip, or proceed with an unpatched file.
- If upstream merges one of these changes, the obsolete patch may be dropped
  only as a deliberate, reviewed decision recorded in `sotoki.lock` notes.