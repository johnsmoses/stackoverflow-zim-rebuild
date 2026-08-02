# stackoverflow-zim-rebuild

Rebuild/update toolkit for the **StackOverflow ZIM archive** (Kiwix) from a
fresh StackExchange XML dump, on top of a **patched sotoki**.

## What this is

StackOverflow's full ZIM build depends on a specific patched version of
[sotoki](https://github.com/openzim/sotoki) that was never merged upstream.
This repository is the *rebuild kit*: it pins that exact source state
(upstream base `157ca9a` + a 9-patch series), records the patches, and
provides the scripts and reference baselines needed to reproduce or
incrementally update the archive with **any new StackExchange dump** —
without the original builder's private data.

The core flow is:

`make bootstrap` → `make restore-baseline` → incremental `update` →
`recover-images` → `finalize-placeholders` → `make assemble` → `make verify`

Tasks 1–8 are implemented and tested (see [Status](#status) and
[`CHANGELOG.md`](CHANGELOG.md)). For a fast, safe first run read
[`docs/quickstart.md`](docs/quickstart.md) **before** starting any multi-day
run, and run the fixture tests first (below).

## Reference artifacts

The two published archives on archive.org are the ground truth for
verification. Sizes are the July 2026 upload records; the MD5 checksums
below are likewise from the **July 2026 upload records** for these items.

| Artifact | Size | MD5 (July 2026 upload records) | Notes |
|---|---|---|---|
| `stackoverflow-final.zim` | 142 GB (152,103,236,002 bytes) | `5a2ba64aba5264df6722bfae1eb887b5` | Full build with images |
| `stackoverflow-nopic.zim` | 69 GB (73,421,904,273 bytes) | `95beed5489d09793051b9f753d220d78` | Build without images |

Items: `stackoverflow-final-zim` /
`stackoverflow-nopic-zim` on archive.org.

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
[`sotoki.lock`](sotoki.lock)). The working build used a patched copy captured
from a site-packages install; the difference is recorded as a **9-patch
series** in `patches/sotoki/`:

- **0001–0005** — the captured build patches (CLI options, staged rendering
  with manifest checkpointing, assemble-only ZIM builder + title
  sanitization, offline asset loading, bounded sort buffer + throttled
  Redis flushes), parameterized so no machine-specific paths remain.
- **0006–0009** — the **snapshot-aware incremental update mode**
  (`--incremental --snapshot-id`, fingerprint + render contract, per-snapshot
  build dirs, seen sets, opt-in hardened prune, local dump archives via
  `--archive-dir`).

See [`docs/patch-maintenance.md`](docs/patch-maintenance.md) for the policy
and `patches/sotoki/README.md` for the per-patch detail.

## Build flow

1. `make bootstrap` — tool checks, WORK_ROOT skeleton + restore marker,
   patched sotoki venv (`SOTOKI_VENV`)
2. `make restore-baseline` — restore the July 2026 baseline bundle
   (set `BASELINE_BUNDLE=...`); the bundle is produced externally, see
   [`docs/baseline-assets.md`](docs/baseline-assets.md)
3. `make update` — incremental update from a fresh dump. This target is a
   guard: it fails fast until the operator supplies the prerequisites and
   points at [`docs/update-runbook.md`](docs/update-runbook.md), which
   documents the patched sotoki `--incremental --snapshot-id` command
   sequence
4. `make recover-images` — image recovery via the `recovery/` pipeline
   (inventory → classify → IA manifest → XML scan → edge resolver → sync;
   parameterized modules, dry-run by default). The make target is a guard
   that fails fast and points at
   [`docs/recovery-runbook.md`](docs/recovery-runbook.md)
5. `make finalize-placeholders` — replace verified placeholder bytes
   (`recovery/finalize_unavailable.py`). The make target is a guard that
   fails fast and points at
   [`docs/recovery-runbook.md`](docs/recovery-runbook.md)
6. `make assemble` — build the ZIM (`bin/assemble`, atomic promotion,
   preflight + gates)
7. `make verify` — zimcheck/zimdump + baseline comparison
   ([`docs/verification.md`](docs/verification.md))

## Storage and time estimates

Reference figures from the July 2026 run:

- Stage tree: **755 GB** (24.15M question pages + 4.37M staged images)
- Redis: **55.25M keys** (db0, tens of GB RDB)
- Outputs: **142 GB** (full) / **69 GB** (nopic)
- Plan for **~1.5× the stage size** free on the WORK_ROOT filesystem
  (≈1.1–1.2 TB for the July baseline; keep more headroom if you retain the
  dump archive and both output ZIMs — see
  [`docs/baseline-assets.md`](docs/baseline-assets.md))
- Full (re-)render pass: **~2 days**; assemble-only: **~14 h**
  (order-of-magnitude, hardware-dependent)

The `.gitignore` keeps all working data out of git — **no data files,
dumps, RDBs, images, or secrets are ever committed.**

## Requirements

- Python **3.12+**
- `7z` or `7za` (dump extraction)
- `git` (sotoki clone + patch application)
- `redis-server` **or** `valkey-server` (isolated loopback instance via
  `bin/redis`; Valkey is the tested default)
- **~1.5 TB free** on the WORK_ROOT filesystem (see above)
- `docker` + `docker compose` (optional — only for the image-recovery
  workers, `docker/image-worker/`)
- `zimcheck` / `zimdump` (optional — `audit_zim.py` degrades gracefully when
  absent, see [`docs/verification.md`](docs/verification.md))

## Quick start

```bash
git clone <this repo> && cd stackoverflow-zim-rebuild
cp .env.example .env        # edit WORK_ROOT, DUMP_ARCHIVE, SNAPSHOT_ID
make config-check           # validate .env / WORK_ROOT defaults
make bootstrap              # tool checks + patched sotoki venv
make restore-baseline BASELINE_BUNDLE=/path/to/bundle   # external bundle
# ... update / recover-images / finalize-placeholders ...
make assemble SNAPSHOT_ID=2027-01 FLAVOUR=full
make verify ZIM=$WORK_ROOT/out/stackoverflow-2027-01-full.zim
```

**Read [`docs/quickstart.md`](docs/quickstart.md) first** — it contains the
step-by-step flow, the external artifacts table, and a **critical first-run
warning**: the first incremental update over a legacy v1 stage re-renders
*everything* once (v1 manifests carry no fingerprints). Before any real run,
execute the fixture tests:

```bash
python3 -m pytest tests/recovery -q        # 37 offline recovery tests
# + the docker image-worker smoke test (docs/nas-worker.md)
```

## Status

| Task | State |
|---|---|
| 1–2: skeleton, provenance contract, patch scaffolding | done (`132e5c9`) |
| 3: sotoki patch series captured (5 patches, base `157ca9a`) | done (`b884e5a`) |
| 4: snapshot-aware incremental mode (patches 0006–0009) + fixture tests | done (`1fac3b4`) |
| 5: baseline restore tooling | done (`2e9f531`) |
| 6: image recovery pipeline + tests (37 offline tests) | done (`f7edcce`) |
| 7: Docker image workers + NAS docs | done (`a446b6f`) |
| 8: assembly verification tooling (`bin/assemble`, audit/compare scripts) | done (`4b9afcd`) |

**Remaining (operational):** CI/release workflow is implemented (Task 10,
commit `c4d100b` — see `.github/workflows/ci.yml` and
`.github/workflows/release.yml`) and the legal/compliance documentation is
done (Task 11 — see
[`docs/data-and-license.md`](docs/data-and-license.md),
[`LICENSE.scope.md`](LICENSE.scope.md), and
[`NOTICE-ATTRIBUTION.md`](NOTICE-ATTRIBUTION.md)). What remains is
operational: running CI on the hosted platform and creating the first
tagged release (`release.yml` triggers on `v*` tags).

## Layout

```
bin/                 bash library + CLI wrappers (common.sh, bootstrap,
                     restore-baseline, redis, assemble, run-worker)
data/                reference baselines & specs (small, tracked)
docs/                quickstart, runbooks, design, configuration, verification
patches/sotoki/      9-patch series (0001-0009) + series + README
recovery/            image recovery pipeline (parameterized modules, dry-run
                     by default) + recovery/README.md
scripts/             python/bash tooling (capture, patch check, audit,
                     compare, verify)
docker/image-worker/ hardened container workers for image recovery
configs/             expected-counts.json, valkey.conf.template
requirements/        python dependency pins
.github/workflows/   CI (patch-apply gate, unit tests, config validation,
                     doc links, worker image) + release workflow
sotoki.lock          upstream + patch-series contract
```

## License

This is a **mixed-license repository**: tooling, docs, and data are
**CC0-1.0** (see [`LICENSE`](LICENSE)), while `patches/sotoki/` is
**GPL-3.0-only** — derived from [openzim/sotoki](https://github.com/openzim/sotoki)
(GPL-3.0), full text in
[`LICENSES/GPL-3.0-only.txt`](LICENSES/GPL-3.0-only.txt). See
[`LICENSE.scope.md`](LICENSE.scope.md) for the per-directory scope and
[`NOTICE-ATTRIBUTION.md`](NOTICE-ATTRIBUTION.md) for attribution. Note:
StackExchange content is **CC BY-SA 4.0**; see
[`docs/data-and-license.md`](docs/data-and-license.md) for the full picture.