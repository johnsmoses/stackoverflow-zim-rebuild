# Incremental Update Runbook

How to update the Stack Overflow ZIM from a fresh StackExchange dump using
the snapshot-aware incremental mode (sotoki patch commits 0006–0009).

## 1. Immutable snapshot ID + archive provenance

- Every incremental run requires `--snapshot-id <id>` (`^[A-Za-z0-9._-]+$`,
  e.g. `2026-07-01`). The snapshot id names the run: the per-snapshot build
  dir, the `snapshot:<id>:seen` Redis set, and the prune journals
  (`prune-plan-<id>.jsonl`, `prune-results-<id>.jsonl`) all carry it.
- **Never reuse a snapshot id** for a different dump. A snapshot id is
  immutable and identifies exactly one archive.
- Archive provenance: with `--archive-dir <dir>` the local
  `stackoverflow.com.7z` is copied into the per-snapshot build dir (nothing
  is downloaded) and its SHA-256 is stamped at `build_dir/archive.sha256`.
  Record that digest with the snapshot for reproducibility. Prepared files
  (`posts_complete.xml`, `Tags.xml`) are always re-extracted from the
  selected archive; stale prepared files are never reused.

## 2. Redis ownership + FLUSHDB semantics

- The logical Redis DB used by sotoki is **dedicated to sotoki**. Never
  point it at a DB shared with other workloads.
- Incremental mode rebuilds metadata from an **empty** dataset: a
  `FLUSHDB` (never `FLUSHALL`) runs before the tag/question metadata passes,
  even with `--keep-redis` (which only disables the exit-time flush). The
  sanitized endpoint and logical DB number are logged before the flush.
- `--keep-redis` remains required when a later `--assemble-only` run needs
  the question scores / list-page state. Without it, the DB is flushed at
  exit.

## 3. Legacy v1 migration (one-time)

Staging trees produced by the legacy full build (pre-Task-4) carry
`schema_version: 1` manifests. Incremental mode treats them as follows:

- v1 pages are re-rendered on the first incremental pass (the skip decision
  requires a v2 manifest, so every v1 page gets a fresh v2 manifest with a
  fingerprint), unless `index.html` is absent, in which case it is rendered
  too.
- The prune pass **never touches v1 manifests**. If you want stale legacy
  pages removed, first run one incremental pass (which upgrades every
  surviving page to v2), then a subsequent `--prune-missing` pass can remove
  v2 pages absent from the new snapshot.

## 4. Interruption safety

- The snapshot seen set is `DEL`eted at the start of each pass, so an
  interrupted run leaves an incomplete seen set; the next run **rescans
  fully** and only then may prune.
- An exclusive lock (`stage_root/.incremental.lock`, flock + pid) is held
  from seen-set reset through the optional prune. A concurrent incremental
  run is rejected with an error; wait for the other run to finish (or remove
  a stale lock after verifying no process holds it) before retrying.
- Prune only ever runs after a fully successful scan: input exhausted, all
  workers joined, zero item failures, seen-set writes committed. An
  interrupted or failed run never prunes.

## 5. Explicit prune procedure (`--prune-missing`, opt-in)

Pruning is strictly opt-in and post-success. The procedure is:

1. **Plan** — every candidate v2 page absent from `snapshot:<id>:seen` is
   validated (resolved path must be exactly
   `stage_root/html/<2-char hex shard>/<post_id>`, manifest `post_id` must
   equal the directory name, no symlinks in the path, containment by
   resolved-prefix) and journaled to `prune-plan-<id>.jsonl` with **fsync
   before any removal**.
2. **Remove** — validated directories are removed (`shutil.rmtree`).
3. **Results** — each item is recorded (`removed` / `failed`) in
   `prune-results-<id>.jsonl`. On the first failure the remaining candidates
   are **quarantined** (left untouched).

Run it as:

```bash
sotoki -d stackoverflow.com --mirror <mirror> --title "Stack Overflow" \
  --description "..." --stage-dir <stage> --incremental \
  --snapshot-id 2026-07-01 --archive-dir <local-dumps> \
  --prune-missing --keep-redis
```

> **Production prune policy:** before running `--prune-missing` against the
> production staging tree, the prune plan (and the release policy for the
> removed pages) requires **oracle review** — removal is irreversible
> without re-extraction and re-rendering.

## 6. Assembly / verification flow

1. Staging run (above) writes/updates `stage/html/<shard>/<post_id>/`
   (`index.html` + v2 `manifest.json`) and the snapshot seen set.
2. Optionally inspect `prune-plan-*.jsonl` / `prune-results-*.jsonl` and the
   `archive.sha256` stamp.
3. Assemble the ZIM (needs the Redis state from the staging run):

```bash
sotoki -d stackoverflow.com --mirror <mirror> --title "Stack Overflow" \
  --description "..." --stage-dir <stage> --assemble-only --keep-redis
```

4. Verify: `zimcheck` / `zimdump` plus baseline comparison
   (see `docs/verification.md`). Counts are compared against
   `data/baseline-2026-07.json` with **expected deltas**, never strict
   equality.

## 7. User-card freshness policy

Staged question user cards are **historical**: `OwnerDisplayName` is part of
the fingerprint, so a display-name edit re-renders the page; reputation or
badge changes do **not** trigger re-render. To refresh reputation/badges on
the live site, regenerate user profile pages independently (`--without-...`
full pass or a future user-pass task); question pages are not affected.