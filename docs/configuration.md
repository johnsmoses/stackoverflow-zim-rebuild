# Configuration

All configuration flows through the environment. `bin/common.sh` sources
`.env` if present, then derives defaults from `WORK_ROOT` for anything left
unset. Copy `.env.example` to `.env` and override only what differs.

Derived defaults are shown as `${WORK_ROOT}/...` — the default value of any
`*_DIR`/`*_PATH` variable is relative to `WORK_ROOT` unless overridden.

## Variables

| Variable | Default | Meaning |
|---|---|---|
| `WORK_ROOT` | `./work` (repo-relative) | Root of **all** working data: stage, output, tmp, cache, redis, mirror, assets. |
| `STAGE_DIR` | `${WORK_ROOT}/stage` | Extracted dump + generated page content. |
| `OUTPUT_DIR` | `${WORK_ROOT}/out` | Final ZIM output (final + nopic). |
| `TMP_DIR` | `${WORK_ROOT}/tmp` | Scratch space for extraction/sorting. |
| `CACHE_DIR` | `${WORK_ROOT}/cache` | Download cache (dump chunks, patches, tool wheels). |
| `REDIS_DIR` | `${WORK_ROOT}/redis` | Redis persistence (RDB snapshots live here). |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Redis connection for build state. |
| `SOTOKI_SOURCE_DIR` | `${WORK_ROOT}/sotoki-src` | Checkout of upstream sotoki (base commit pinned in `sotoki.lock`). |
| `SOTOKI_VENV` | `${WORK_ROOT}/.venv-sotoki` | Virtualenv with the patched sotoki installed. |
| `MIRROR_DIR` | `${WORK_ROOT}/mirror` | Where the dump archive is stored. |
| `MIRROR_URL` | `https://archive.org/download/stackexchange` | Dump download source. |
| `DUMP_ARCHIVE` | `stackoverflow.com.7z` | Expected dump archive filename. |
| `SNAPSHOT_ID` | `2026-07-06` | Snapshot identity recorded in state (see `docs/provenance.md`). |
| `THREADS` | `8` | Parallelism for page/parse work. |
| `CONTENT_THREADS` | `4` | Parallelism for content/image fetching. |
| `PLACEHOLDER_BYTES` | `1852` | Byte size of a sotoki download-failure placeholder (see `data/placeholder-spec.json`). |
| `IA_ROOT` | `https://archive.org/download/stackoverflow-final-zim` | Reference artifact (final ZIM) for verification/download. |
| `RECOVERY_ROOT` | `https://archive.org/download/stackoverflow-images-recovery` | Image recovery corpus source. |
| `ASSET_CACHE_DIR` | `${WORK_ROOT}/assets` | Downloaded/staged image assets (the July build staged 4,375,716). |

## Rules

- `WORK_ROOT` must always be set (either in `.env` or by default).
- Paths may be relative (resolved against the repo root) or absolute.
- `bin/common.sh` calls `require_path` on the critical directories in
  `config-check`; a script that sources it inherits all defaults and the
  `log()`/`die()` helpers.
- No secrets belong in `.env`-derived configuration; private keys and
  `.wg*.conf` files are gitignored and out of scope.