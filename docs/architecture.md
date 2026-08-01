# Architecture

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
   (db0, ~55M keys)         manifests, placeholder bookkeeping
        │
        ▼
   OUTPUT_DIR  ─────────── final ZIM(s)
   (stackoverflow-final.zim, stackoverflow-nopic.zim)
```

- **Stage** holds the extracted dump and generated content. It is the
  *source of truth for page content*; it can be re-derived from the dump.
- **Redis** holds build state and dedupe tables. It is *not* a durable
  artifact — losing it means re-indexing, which is why baseline restores
  exist. Redis is persisted as RDB snapshots (never committed to git).
- **ZIM** is the deliverable. It is *derived*, never hand-edited; the only
  post-assembly step is verification.

## Snapshot model

Planned: **snapshot-aware incremental mode**. Each run records its
`SNAPSHOT_ID` in the stage/Redis state so a later run can diff
"dump → snapshot" and only process changed posts, added images, and removed
pages.

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