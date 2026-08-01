# Provenance

This document records where every number and artifact in this repository
comes from, so a third party can trust (and reproduce) the build.

## Snapshot identity

Every run is tied to a `SNAPSHOT_ID` (env var, e.g. `2026-07-06`). The
snapshot identifies the StackExchange dump used as input:

- Source: StackExchange data dump (`stackoverflow.com.7z`), mirrored at
  `MIRROR_URL` (default `https://archive.org/download/stackexchange`).
- The July 2026 baseline used `SNAPSHOT_ID=2026-07-06`.

## Hash scheme

Image assets are addressed by:

```
md5("https://i.sstatic.net/FILENAME")
```

That MD5 (of the canonical CDN URL string) is the Redis key scheme used
during staging and recovery. Do not change it without re-deriving every
downstream count; it is recorded in `data/baseline-2026-07.json`.

## July 2026 baseline

Built with `sotoki` at commit `157ca9a` (patched). Exact counts are in
[`data/baseline-2026-07.json`](../data/baseline-2026-07.json):

| Count | Value |
|---|---|
| question pages | 24,152,540 |
| staged images | 4,375,716 |
| ZIM entries | 85,441,337 |
| Redis db0 keys | 55,250,097 |
| initial placeholders | 521,489 |
| recovered images | 410,856 (78.8%) |
| unrecoverable | 120,633 |

Reference archives: `stackoverflow-final.zim` (152,103,236,002 bytes) and
`stackoverflow-nopic.zim` (73,421,904,273 bytes) on archive.org.

## Patch provenance

The working sotoki that produced the July 2026 build differs from upstream
`157ca9a`. Those changes live in a **site-packages install** of sotoki and
will be captured from there as a patch series into `patches/sotoki/`
(**capture pending**, Task 3). The capture tool
(`scripts/capture_sotoki_patches.py`) enforces a 12-file allowlist so only
deliberate, reviewed changes enter the series.

Anything that cannot be traced to (a) the dump, (b) upstream sotoki
`157ca9a` plus the captured patches, or (c) the published archive.org
artifacts is **not** part of the rebuild contract.