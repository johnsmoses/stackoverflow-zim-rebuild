# System Design

High-level boundaries and hard-won lessons that shape the rebuild design.

## Stage / Redis / ZIM boundaries

```
StackExchange dump (stackoverflow.com.7z)
        │  extract
        ▼
   STAGE_DIR  ────────────  intermediate content (XML-derived pages,
   (question pages,         staged images at ASSET_CACHE_DIR)
    images)
        │  sotoki (patched)
        ▼
   Redis (REDIS_DIR)  ───── state + dedupe: image URL → md5 key, page
   (db0, ~55M keys)         manifests, placeholder bookkeeping, incremental
                            snapshot seen sets
        │
        ▼
   OUTPUT_DIR  ─────────── final ZIM(s)
   (stackoverflow-final.zim, stackoverflow-nopic.zim)
```

- **Stage** holds the extracted dump and generated content. It is the
  *source of truth for page content*; it can be re-derived from the dump.
- **Redis** holds build state and dedupe tables. It is *not* a durable
  artifact — losing it means re-indexing, which is why baseline restores
  exist. Redis is persisted as RDB snapshots (never committed to git). The
  logical DB sotoki uses is **dedicated to sotoki**: incremental rebuilds
  start with a `FLUSHDB` (never `FLUSHALL`) of that DB.
- **ZIM** is the deliverable. It is *derived*, never hand-edited; the only
  post-assembly step is verification.

## Snapshot model (implemented, Task 4)

**Snapshot-aware incremental mode** (commits 0006–0009 of the sotoki patch
series). Each run records an immutable `SNAPSHOT_ID` and diffs
"dump → existing staged tree" by fingerprinting every renderer-consumed
field:

- **Per-snapshot build dir + archive provenance.** The build dir name
  includes the snapshot id and prepared files (`posts_complete.xml`,
  `Tags.xml`) are always re-extracted from the selected archive — stale
  prepared files are never reused. With `--archive-dir`, the local dump
  archive is copied into the build dir and its SHA-256 is stamped at
  `archive.sha256`.
- **Fingerprint + render contract.** `RENDER_CONTRACT_VERSION = "1"` in
  `posts.py`; `fingerprint_post()` is a permutation-invariant SHA-256 over
  every renderer-consumed field of the question, its comments, its answers,
  and its links. A staged page is skipped (bytes + mtime untouched, no
  manifest rewrite) only when the existing manifest matches the contract
  version AND the current fingerprint AND `index.html` exists. Incremental
  manifests are `schema_version: 2`; legacy full-build manifests are
  `schema_version: 1` and are never pruned.
- **Seen sets.** Each snapshot owns a `snapshot:<id>:seen` Redis set, reset
  with `DEL` at the start of each pass (an interrupted rerun therefore
  rescans fully before any pruning). Unchanged skipped pages are added to the
  seen set too, so the prune pass keeps them. `stage:done:questions` remains
  the `--resume` checkpoint for legacy full builds only.
- **Prune policy (opt-in `--prune-missing`, hardened).** Only after a
  COMPLETELY successful full scan (input exhausted, all workers joined, zero
  item failures, seen-set writes committed) is any staged v2 page absent from
  the current seen set eligible — regardless of the manifest's previous
  snapshot id. Hardening:
  - exclusive `stage_root/.incremental.lock` (flock + pid) rejects concurrent
    runs;
  - per-candidate path validation (resolved path must equal
    `stage_root/html/<2-char hex shard>/<post_id>`, manifest `post_id` must
    equal the directory name, no symlinks anywhere in the path, containment
    via resolved-path prefix);
  - the prune plan is journaled to `prune-plan-<snapshot>.jsonl` with fsync
    BEFORE any removal; per-item results land in
    `prune-results-<snapshot>.jsonl`; on the first failure the remaining
    candidates are quarantined;
  - production prune policy requires oracle review before use.
- **User-card freshness.** Staged question user cards are HISTORICAL:
  `OwnerDisplayName` is fingerprinted so display-name edits re-render, but
  reputation/badge changes do not trigger re-render.

**Never** resume by trusting an ID-only marker (e.g. "last processed post
ID"). Post IDs are not monotonic across dump snapshots (edits, deletions,
and re-imports break the assumption), and an ID-only resume silently
produces a corrupted ZIM. Any resume must validate the snapshot manifest
before trusting incremental state.

## Manifest-based sync (never `rsync --ignore-existing`)

File synchronization between hosts and archive.org mirrors **must** use
manifest-based sync (compare name + size + hash against a recorded
manifest). **Never** use `rsync --ignore-existing`: it silently treats a
truncated/partial file as complete, which is exactly how an 85M-entry ZIM
build ends up with corrupt assets. The manifest also lets `recover-images`
prove which files are genuinely missing vs. merely unrecovered.

## Image recovery sources

Placeholders (521,489 in July) are replaced in priority order:

1. **IA dump** — the StackExchange image dump on archive.org
   (62 ZIPs, ~859 GB total). Highest fidelity; download what the manifest
   says is missing.
2. **Live CDN** — fetch `https://i.sstatic.net/FILENAME` directly,
   typically through a WireGuard tunnel to avoid geo-blocking
   (`.wg*.conf` files are gitignored; nothing committed).
3. **XML filename scanner** — scan the dump XML for image filenames that
   were never staged, to discover assets the original staging missed.
4. **Edge resolver** — for anything still missing, resolve via CDN edge
   variations (alt hosts/schemes) before giving up and keeping the
   placeholder.

Recovery bookkeeping lives in Redis (`RECOVERY_ROOT`/asset cache); the
verification step audits the outcome (see `docs/verification.md`).