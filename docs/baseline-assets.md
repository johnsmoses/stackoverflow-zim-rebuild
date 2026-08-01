# Baseline bundle assets

The July 2026 baseline bundle is the reference restore source for the
rebuild kit. It is produced **externally** (from the original build's
storage) and consumed read-only by `bin/restore-baseline`. Nothing in this
repository commits the bundle itself — a 755GB stage cannot live in git.

## July reference facts

| Item | Value |
|---|---|
| Stage size | 755 GB |
| Question pages | 24,152,540 (24.15M) |
| Staged images | 4,375,716 (4.37M) |
| Redis db0 keys | 55,250,097 (55.25M) |
| `stackoverflow-final.zim` | 152,103,236,002 bytes (archive.org) |
| `stackoverflow-nopic.zim` | 73,421,904,273 bytes (archive.org) |

Exact counts live in `data/baseline-2026-07.json`; the bundle's
`MANIFEST.json` embeds the same counts so restore verification does not
depend on network access.

## Bundle layout

```
<bundle>/
├── MANIFEST.json        schema_version, bundle_id, created_at, tool,
│                        tool_commit, counts (from data/baseline-2026-07.json),
│                        sha256 of redis/baseline.rdb and of assets/ and zim/
│                        contents, stage_listing_hash, entries (relative
│                        paths of the small files covered by MANIFEST.sha256)
├── MANIFEST.sha256      sha256sum -c format: MANIFEST.json, redis/baseline.rdb,
│                        assets/**, zim/** — NOT stage/ (see checksum policy)
├── stage/               the 755GB staged tree (html/<2-char hex shard>/<post_id>/index.html,
│                        images/, …) — verified via stage_listing_hash
├── redis/baseline.rdb   redis db0 dump at baseline time (55.25M keys)
├── zim/*.zim            optional: the two reference ZIMs, if you want them restored too
└── assets/              image asset cache
```

## Producing a bundle

1. **Stage**: `rsync -a /path/to/original-stage/ <bundle>/stage/` (source
   trailing slash: copy the tree, not the directory itself).
2. **Redis RDB**: against the baseline build's redis,
   `redis-cli -u $REDIS_URL SAVE` then copy the dump to
   `<bundle>/redis/baseline.rdb` (or `redis-cli --rdb`).
3. **ZIMs (optional)**: copy `stackoverflow-final.zim` / `stackoverflow-nopic.zim`
   into `<bundle>/zim/`.
4. **MANIFEST.json**: embed schema_version, bundle_id, created_at, tool,
   tool_commit, the counts from `data/baseline-2026-07.json`, per-entry
   sha256 for the small files, an `entries` list (relative paths of
   MANIFEST.json, redis/baseline.rdb, assets/** and zim/**, i.e. everything
   MANIFEST.sha256 covers), and the stage listing hash:
   ```sh
   cd <bundle>
   find stage -type f | sort | xargs sha256sum | sha256sum
   ```
   Record that digest as `stage_listing_hash`.
5. **MANIFEST.sha256**: `cd <bundle> && sha256sum MANIFEST.json redis/baseline.rdb \
   $(find assets zim -type f 2>/dev/null) > MANIFEST.sha256`. The stage is
   deliberately **not** listed here — it is covered by `stage_listing_hash`.

## Checksum policy

- **Small entries are exhaustively covered** by `MANIFEST.sha256`
  (`sha256sum -c`): MANIFEST.json, redis/baseline.rdb, every asset, every
  zim file. Any mismatch aborts the restore.
- **The stage is verified by default with the full listing hash**
  (`stage_listing_hash` in MANIFEST.json): a streaming sha256 over
  `find stage -type f | sort | xargs sha256sum`. This is a complete
  verification — every path and every byte — tolerant of the long runtime a
  755GB stage implies. `bin/restore-baseline` computes the same digest and
  compares; a mismatch fails the restore.
- `--no-stage-verify` is the **explicit opt-out** (e.g. when the stage was
  already verified at sync time and you only need the small-file checks).
- `--strict-stage` forces the listing verification even in `--validate-only`
  mode.
- Sampling is **never** sufficient — a handful of files proves nothing about
  24M pages.

## Restore procedure

```sh
export BASELINE_BUNDLE=/path/to/bundle
make restore-baseline                 # full restore into a fresh WORK_ROOT
make restore-baseline ARGS="--replace"        # re-restore over the existing stage
make restore-baseline ARGS="--validate-only"  # validate a bundle without touching targets
make restore-baseline ARGS="--no-redis"       # stage/assets only (redis kept as-is)
make restore-baseline ARGS="--no-zim"         # skip optional zim copies
make restore-baseline ARGS="--no-stage-verify" # explicit opt-out of the big listing
```

Details:

- **D1 delete safety**: every source/destination is canonicalized with
  `realpath -m`; empty paths, `/`, `$HOME`, the repo root, source-target
  overlap and symlinked destinations are rejected. Deletion is restricted to
  the fixed children `WORK_ROOT/stage` (rsync `--delete`), `WORK_ROOT/redis`
  (RDB replace) and `WORK_ROOT/assets` (copy only, never `--delete`).
  `--replace` requires `WORK_ROOT/.sotoki-rebuild-ok` to exist. All
  restore/redis operations are serialized with
  `flock -n $WORK_ROOT/.restore.lock`.
- **D2 redis lifecycle**: only loopback `REDIS_URL` is accepted
  (`redis://127.0.0.1:PORT` or `redis://localhost:PORT`, port 1-65535). A
  port that is occupied by anything that is not conclusively this kit's
  owned instance (pidfile + uid + cmdline match) fails with "port in use by
  unrelated process". `bin/redis stop` uses `SHUTDOWN NOSAVE` only after
  identity verification; unknown orphans are never killed.
- **D3 restore state**: MANIFEST `entries` are validated (no absolute
  paths, no `..`, nothing escaping the bundle) before any target is
  modified. An owned redis instance is stopped before its RDB is replaced.
  The success marker is cleared at restore start and written only after
  every check (checksums, listing hash, layout, snapshot verification)
  passes.
- **Never write to the bundle**: the bundle is opened read-only; all writes
  go under WORK_ROOT.

## Marker gating

`WORK_ROOT/.sotoki-rebuild-ok` gates destructive operations:

- Created by `bin/bootstrap` only when it created an **empty** WORK_ROOT
  (never in a non-empty, unrecognized directory).
- Recreated by `bin/restore-baseline` only after a fully verified restore.
- Required by `bin/restore-baseline --replace` before any `--delete` runs.
- **Future builds must require it**: `make update` and friends should refuse
  to run unless the marker exists — a verified baseline restore is the
  precondition for a trustworthy incremental build.

## Storage guidance

Plan for roughly **1.5x the stage size** on the WORK_ROOT filesystem:
1.0x for the restored stage, plus headroom for the redis RDB, assets,
output ZIMs and tmp/sort space. 755GB stage → ~1.1-1.2TB free is the
comfortable minimum.