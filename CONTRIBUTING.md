# Contributing

Thanks for contributing to the StackOverflow ZIM rebuild kit. This is a
small, deliberately constrained repo: the value is reproducibility, so the
rules below protect the reproducibility contract first.

## Pull requests

- PRs go to **`master`**. The CI workflow (`.github/workflows/ci.yml`)
  runs on every push/PR to `master`.
- Branch naming: `topic/short-description` (e.g. `topic/fix-restore-marker`,
  `topic/update-patch-0007`). No machine-specific, user-specific, or
  workflow-run specific names.
- The offline pytest suite **must pass** and must stay offline: every test
  run uses `tests/recovery/conftest.py`, which raises on any outbound
  socket. Do not add tests that open network connections, and do not weaken
  that fixture.
- `python3 -m pytest tests/recovery -q` (37 tests) plus
  `python3 -m py_compile` on any touched `.py` are the local pre-submit
  checks; CI additionally runs shellcheck, pyflakes, config/doc validation
  and the patch-apply gate.

## Patch-series rule (patches/sotoki/)

`patches/sotoki/` is the difference between upstream sotoki at
`157ca9a` and the working build. Any change to that directory must:

1. **Apply cleanly** on the pinned base commit `157ca9a` — `git am` of the
   series (0001→0009, in `series` order) must succeed; a failure on a newer
   upstream must be explicit, never silent.
2. **Update `sotoki.lock` deliberately**: recompute the canonical tree hash
   (`git checkout 157ca9a` → `git am patches/sotoki/*.patch` →
   `git rev-parse HEAD^{tree}`) and write it into
   `patch_series.tree_hash`. Drift is a release-blocking failure in CI.
3. **Pass the `patch-apply-gate` CI job**, which rebuilds the patched
   clone, compares the assembled tree hash to `sotoki.lock`, and runs
   `tests/ci/incremental_regression_test.py` (the same-ID / changed-content
   re-render gate). Never commit a patch series that fails the gate.

Capture/order/apply policy details live in
[`docs/patch-maintenance.md`](docs/patch-maintenance.md).

## Upstream-rebase rule

Bumping the upstream base commit (`openzim/sotoki` beyond `157ca9a`) is a
**deliberate, reviewed act**, not a routine dependency bump:

- Rebase the entire 9-patch series onto the new base and re-validate the
  fixture tests **and** the incremental-render gate
  (`tests/ci/incremental_regression_test.py`) against the rebased clone.
- Update `sotoki.lock` (`upstream.base_commit`, `patch_series.tree_hash`,
  and the note) in the same commit that changes the series.
- **Dependabot / Renovate must never auto-merge upstream sotoki changes**
  into this repo. If automated bots are configured, scope them to ignore
  `sotoki.lock` and `patches/sotoki/`; upstream changes only enter through
  the manual rebase process above.

## What never goes in this repo

- No secrets, API keys, `.env` values, or WireGuard material (see
  `.gitignore`). The repo contains no interface configs or keys and must
  stay that way.
- No hardcoded machine-specific paths (e.g. `/home/...`); every path
  derives from `WORK_ROOT` or `.env` (see `docs/configuration.md`).
- No large data in git: no dumps, no stages, no image corpora, no ZIMs.
  Reference data lives in `data/` as small JSON baselines; the actual
  bundles are external artifacts (see `docs/baseline-assets.md`).

## Commit style

One logical change per commit, imperative subject line, reference the task
when applicable (e.g. "Add CI patch-apply gate and release workflow
(Task 10)"). Do not include regenerated artifacts or fixture noise.