# stackoverflow-zim-rebuild

Rebuild/update toolkit for the **StackOverflow ZIM archive** (Kiwix) from a
fresh StackExchange XML dump, on top of a **patched sotoki**.

## Purpose

StackOverflow's full ZIM build depends on a specific patched version of
[sotoki](https://github.com/openzim/sotoki) that was never merged upstream.
This repository is the *rebuild kit*: it pins that exact source state, records
the patches, and provides the scripts and reference baselines needed to
reproduce or incrementally update the archive with **any new StackExchange
dump** — without the original builder's private data.

Scope today: **Tasks 1–2 scaffolding**. Patch capture, build scripts, and
recovery tooling are planned but not yet implemented (see the Makefile
targets, which all print `not yet implemented`).

## Reference artifacts

The two published archives on archive.org are the ground truth for
verification:

| Artifact | Size | Notes |
|---|---|---|
| `stackoverflow-final.zim` | 142 GB (152,103,236,002 bytes) | Full build with images |
| `stackoverflow-nopic.zim` | 69 GB (73,421,904,273 bytes) | Build without images |

## Baseline facts (July 2026 build)

| Metric | Count |
|---|---|
| Question pages | 24,152,540 |
| Staged images | 4,375,716 |
| ZIM entries | 85,441,337 |
| Redis db0 keys | 55,250,097 |
| Initial placeholders | 521,489 |
| Images recovered | 410,856 (78.8%) |
| Unrecoverable images | 120,633 |

Exact counts live in [`data/baseline-2026-07.json`](data/baseline-2026-07.json).
Future runs are compared against these with **expected deltas**, never strict
equality (see [`docs/verification.md`](docs/verification.md)).

## The patched sotoki

sotoki is pinned at upstream base commit `157ca9a` (see
[`sotoki.lock`](sotoki.lock)). The working build used a patched copy from a
site-packages install; the diff will be captured as a patch series in
`patches/sotoki/` — **capture pending** (Task 3). See
[`docs/patch-maintenance.md`](docs/patch-maintenance.md) for the policy.

## Build flow (planned)

1. `make bootstrap` — clone/patch sotoki, prepare stage + Redis
2. `make restore-baseline` — restore July 2026 baseline state (optional)
3. `make update` — incremental update from the fresh dump
4. `make recover-images` — recover missing images (IA dump, CDN, scanners)
5. `make finalize-placeholders` — replace verified placeholder bytes
6. `make assemble` — build the ZIM
7. `make verify` — zimcheck/zimdump + baseline comparison

## Storage requirements

Real runs need **many TB of free storage**: the July build's stage tree held
~4.4M images plus the extracted dump, Redis held 55M keys, and the output ZIM
is 142 GB (69 GB for nopic). Plan for:

- stage (extracted XML + content): hundreds of GB to low TB
- Redis RDB: tens of GB
- assets/images cache: tens to hundreds of GB
- output ZIMs: 69–142 GB each

The `.gitignore` keeps all of it out of git — **no data files, dumps, RDBs,
images, or secrets are ever committed.**

## Quick start (scaffolding checks only)

```bash
make help          # list targets
make config-check  # validate .env / WORK_ROOT defaults (via bin/common.sh)
cp .env.example .env   # only if you want to override defaults
bash scripts/check_patch_series.sh --package-path . --base-commit 157ca9a --dry-run
python scripts/capture_sotoki_patches.py --package-path . --dry-run
```

## Layout

```
bin/                 bash library (common.sh: env + helpers)
data/                reference baselines & specs (small, tracked)
docs/                provenance, architecture, configuration, maintenance
patches/sotoki/      patch series (capture pending)
scripts/             python/bash tooling (scaffolding today)
requirements/        python dependency pins
.github/workflows/   CI stub
sotoki.lock          upstream + patch-series contract
```

## License

This repository is **CC0-1.0** (see [`LICENSE`](LICENSE)). Note: sotoki
itself is **GPL-3.0** and StackExchange content is **CC BY-SA 4.0**; see
[`docs/data-and-license.md`](docs/data-and-license.md) for the full picture.