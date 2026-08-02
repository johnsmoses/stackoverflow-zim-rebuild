# Changelog

All notable changes to the stackoverflow-zim-rebuild toolkit. Commits are
short hashes on `master`; versions follow the task plan (Tasks 1–8).

## [0.7.0] — 2026-08-01

Assembly verification tooling (Task 8): `bin/assemble` with preflight
(stage count, Redis, `verify_snapshot.py`, ≥1.5× free space), gate-then-
promote atomic promotion, run manifest; `scripts/audit_stage.py`,
`scripts/audit_zim.py`, `scripts/compare_baseline.py`,
`scripts/verify_snapshot.py`. Commit `4b9afcd`.

## [0.6.0] — 2026-08-01

Docker image workers + NAS worker docs (Task 7): hardened
`docker/image-worker/` containers (`so-image-worker`,
`so-ia-basename`), `bin/run-worker`, aggregate rate limiting + quota-stop,
WireGuard explicitly out of scope. Commits `a446b6f`, `6dae2d9`.

## [0.5.0] — 2026-08-01

Image recovery pipeline (Task 6): `recovery/` package with inventory,
classification, IA basename manifests, XML dump scans, edge resolver,
size upgrades, validated manifest-based sync, finalize, verify — dry-run by
default with `--fetch` opt-in; 37 offline fixture tests. Commit `f7edcce`.

## [0.4.0] — 2026-08-01

Baseline restore tooling (Task 5): `bin/restore-baseline` with bundle
`MANIFEST.json` / `MANIFEST.sha256` validation, full stage listing hash,
delete safety, redis lifecycle checks, restore marker gating;
`docs/baseline-assets.md`. Commit `2e9f531`.

## [0.3.0] — 2026-08-01

Snapshot-aware incremental update mode (Task 4): sotoki patches 0006–0009
(fingerprint + render contract, per-snapshot build dirs, seen sets,
opt-in hardened prune, `--archive-dir` local archives) with fixture
coverage for the incremental mode. Commit `1fac3b4`.

## [0.2.0] — 2026-08-01

sotoki patch series captured (Task 3): 5-patch series (0001–0005) from the
working site-packages install, base `157ca9a`, parameterized to remove
machine-specific paths; `scripts/capture_sotoki_patches.py` +
`scripts/check_patch_series.sh`. Commit `b884e5a`.

## [0.1.0] — 2026-08-01

Initial rebuild-kit skeleton (Tasks 1–2): repository layout, provenance
contract (`data/baseline-2026-07.json`, `sotoki.lock`), patch scaffolding,
`bin/common.sh` config framework, CI stub. Commit `132e5c9`.

---

Remaining (not yet implemented): CI/release workflow beyond the smoke-test
stub, and the legal/compliance review of publishing rebuilt ZIMs.