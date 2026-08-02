# Test fixtures layout

This repository keeps fixtures **inline and synthetic** — no dump data, no
network, nothing large in git (see `CONTRIBUTING.md`). Nothing here ever
touches the real StackExchange dump, the IA image corpus, or a VPN.

## tests/recovery/ — inline fixtures

The 37 recovery tests build everything they need at runtime:

- **Synthetic images**: tiny generated PNGs come from
  `recovery.lib.images.tiny_png_bytes()` (`recovery/lib/images.py`); content
  hashes come from `sha256_of()`. Tests write these into `tmp_path`-based
  stage/state directories and never persist anything into the repo.
- **Manifests / TSVs / JSON specs**: built in-test from small literal dicts
  and rows (see `test_manifest.py`, `test_sync_to_stage.py`,
  `test_upgrade_small.py`).
- **Networking**: `tests/recovery/conftest.py` replaces
  `socket.socket` / `create_connection` / `getaddrinfo` with raising stubs,
  so any accidental outbound socket fails the test. The recovery library's
  own `fetch_ok` dry-run default backs this up.

Run them with:

```bash
python3 -m pytest tests/recovery -q   # 37 offline tests
```

## tests/ci/ — patched-sotoki gate

`tests/ci/incremental_regression_test.py` (Task 10 / H8) is a pure-logic
regression gate that imports `fingerprint_post` and `should_skip_render`
**from a temporary patched clone of openzim/sotoki** (upstream base
`157ca9a` + `patches/sotoki/` 0001-0009). The patched dependency is never
committed into this repo.

The clone location is passed via the `SOTOKI_PATCHED_SRC` environment
variable (path to the clone's `src/` directory) or the
`--patched-sotoki-src=<dir>` pytest option:

```bash
SOTOKI_PATCHED_SRC=/tmp/sotoki-patched/src python3 -m pytest tests/ci -q
```

Without either, the module **skips** with a clear message — local runs
without a clone stay graceful. The CI `patch-apply-gate` job builds the
clone (`git clone` → checkout `157ca9a` → `git am` the series), verifies
the assembled tree hash against `sotoki.lock`, and then runs this test in
the same job/workspace.

## Docker worker smoke test

The image-worker containers have their own fixture procedure: two tiny
local PNGs (plus one HTML page) served on `127.0.0.1`, no real network.
See `docker/image-worker/README.md` and `docs/nas-worker.md`.

## End-to-end fixture stages

For the fixture-based synthetic stage procedure (before any real multi-day
run), follow **docs/quickstart.md → "Before any real run: fixture tests"**,
which walks the offline pytest suite and the local-image worker smoke test
as the mandatory pre-flight checks.