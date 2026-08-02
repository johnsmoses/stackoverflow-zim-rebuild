# Quickstart

Fast path for a new user: go from an empty machine to a running,
verifiable rebuild/update of the StackOverflow ZIM. This is the *happy
path* — for the deep detail read [`docs/system-design.md`](system-design.md),
[`docs/update-runbook.md`](update-runbook.md), and
[`docs/recovery-runbook.md`](recovery-runbook.md) before your first
multi-day run.

Commands below that depend on your exact environment are marked
**illustrative** — verify the flags against your `.env` and the referenced
documents before executing them in production.

## Prerequisites

| Requirement | Notes |
|---|---|
| Python **3.12+** | checked by `bin/bootstrap` |
| `7z` or `7za` | dump extraction |
| `git` | sotoki clone + `git am` patch application |
| `redis-server` **or** `valkey-server` | isolated loopback instance (`bin/redis`); Valkey is the tested default |
| **~1.5 TB free** | on the WORK_ROOT filesystem (≈1.5× the ~755 GB July stage; see [`docs/baseline-assets.md`](baseline-assets.md)) |
| `docker` + `docker compose` | optional — only for the image-recovery workers (`docker/image-worker/`) |
| `zimcheck` / `zimdump` | optional — verification degrades to structural checks when absent |

## Step 1 — clone, configure

```bash
git clone <this-repo> && cd stackoverflow-zim-rebuild
cp .env.example .env
```

Edit `.env` minimally:

- `WORK_ROOT` — where all working data lives (default `./work`; needs the
  ~1.5 TB filesystem).
- `DUMP_ARCHIVE` — expected dump archive filename (default
  `stackoverflow.com.7z`).
- `SNAPSHOT_ID` — an immutable id for this run (default `2026-07-06`;
  **never reuse** an id for a different dump).

Every other variable has a `WORK_ROOT`-derived default — see
[`docs/configuration.md`](configuration.md).

## Step 2 — bootstrap

```bash
make bootstrap
```

Runs the tool checks (refuses to run as root), creates the WORK_ROOT
skeleton, writes the restore marker `$WORK_ROOT/.sotoki-rebuild-ok` (only on
a freshly created empty WORK_ROOT), clones `openzim/sotoki` at `157ca9a`,
applies `patches/sotoki/` (0001→0009) with `git am --3way`, and installs the
patched sotoki into `$SOTOKI_VENV`. A patched checkout is detected and
skipped on re-runs.

## Step 3 — restore the baseline

The July 2026 baseline bundle (755 GB stage + Redis RDB + optional ZIMs) is
produced **externally** from the original build's storage — it is not in
git and not downloadable from this repo. Obtain it from the archive
operator, or produce it yourself following
[`docs/baseline-assets.md`](baseline-assets.md).

```bash
make restore-baseline BASELINE_BUNDLE=/path/to/bundle
```

`bin/restore-baseline` validates the bundle manifest + checksums (including
the full stage listing hash), then restores stage / Redis RDB / assets
under WORK_ROOT. Options: `ARGS="--replace"`, `--validate-only`,
`--no-redis`, `--no-zim`, `--no-stage-verify` (see
[`docs/baseline-assets.md`](baseline-assets.md)).

Without a baseline restore you cannot run a trustworthy incremental build:
the marker `$WORK_ROOT/.sotoki-rebuild-ok` gates assembly (see
[`docs/troubleshooting.md`](troubleshooting.md), "baseline not restored").

## Step 4 — fresh dump + incremental update

Obtain a fresh StackExchange dump (`stackoverflow.com.7z`) from the
archive.org StackExchange mirror
(`https://archive.org/download/stackexchange`, the default `MIRROR_URL`)
and place it where `MIRROR_DIR` points.

`make update` is the intended entry point ("incremental update from fresh
dump"); its recipe is currently a thin placeholder and there is no
`bin/update` script yet — `bin/update` may be a future addition. The
**implemented** update flow is the patched sotoki snapshot-aware incremental
mode (`--incremental --snapshot-id`, `--archive-dir`), documented in
[`docs/update-runbook.md`](update-runbook.md). An **illustrative** invocation
is:

```bash
# illustrative — exact flags per your environment (docs/update-runbook.md)
"$SOTOKI_VENV/bin/sotoki" -d stackoverflow.com --mirror "$MIRROR_DIR" \
  --title "Stack Overflow" --description "..." --stage-dir "$STAGE_DIR" \
  --incremental --snapshot-id "$SNAPSHOT_ID" --archive-dir "$MIRROR_DIR" \
  --keep-redis
```

This re-extracts `posts_complete.xml` / `Tags.xml` from the selected
archive, fingerprints every renderer-consumed field, skips unchanged pages,
and records the `snapshot:<id>:seen` set. Pruning is **opt-in** and only
safe after a fully successful scan (`--prune-missing`; see the runbook's
prune procedure).

> **CRITICAL first-run warning:** if your stage comes from the legacy v1
> full build (pre-Task-4), the FIRST incremental pass re-renders
> **everything** once, because v1 manifests carry no fingerprints. Budget
> **~2 days** for that first pass. Subsequent runs are incremental and only
> re-render changed pages.

## Step 5 — image recovery + placeholder finalization

`make recover-images` and `make finalize-placeholders` are the documented
targets; like `update` their recipes are currently placeholders, and the
implemented capability lives in the `recovery/` pipeline
([`docs/recovery-runbook.md`](recovery-runbook.md)) plus the optional
Docker workers ([`docs/nas-worker.md`](nas-worker.md)). Everything is
**dry-run by default**; fetching requires an explicit `--fetch`
(`--no-dry-run`).

The happy path for a missing-image pass is: inventory the staged HTML tree,
classify missing hashes, build the IA basename manifest, run the local
workers (`bin/run-worker cdn|ia`, `--limit` for the first pass), copy
results back, sync into the stage, finalize the still-missing hashes, and
verify — each step in `docs/recovery-runbook.md`.

## Step 6 — assemble

```bash
make assemble SNAPSHOT_ID=2027-01 FLAVOUR=full     # or FLAVOUR=nopic
```

`bin/assemble` preflights (stage page count, Redis reachable,
`verify_snapshot.py`, ≥1.5× free space), runs sotoki `--assemble-only` into
a unique `.partial` file in the final output directory, then gates on
`audit_zim.py` + `compare_baseline.py` **before** the atomic promotion
rename. A prior good ZIM is never replaced by a failed build. Output:
`$OUTPUT_DIR/stackoverflow-2027-01-full.zim` (+ `.manifest.json`). See
[`docs/verification.md`](verification.md).

## Step 7 — verify

```bash
make verify ZIM=$WORK_ROOT/out/stackoverflow-2027-01-full.zim
```

Runs `audit_stage.py`, `audit_zim.py` (zimcheck when available), and
`compare_baseline.py` against `data/baseline-2026-07.json` — **expected
deltas**, never strict equality (the dump has grown since July 2026). See
[`docs/verification.md`](verification.md).

## Before any real run: fixture tests

Never start a multi-day run without exercising the offline fixture tests
first:

```bash
make config-check
python3 -m pytest tests/recovery -q     # 37 offline tests, socket-blocking
```

The Docker worker smoke test (two tiny local PNGs served on `127.0.0.1`,
no real network) is in [`docs/nas-worker.md`](nas-worker.md) and
`docker/image-worker/README.md`.

## External artifacts

| Artifact | Where from | Needed for | Notes |
|---|---|---|---|
| StackExchange dump (`stackoverflow.com.7z`) | `https://archive.org/download/stackexchange` | incremental update (Step 4) | source of truth for content |
| July 2026 baseline bundle (stage 755 GB + `redis/baseline.rdb` + optional ZIMs) | external, produced per [`docs/baseline-assets.md`](baseline-assets.md) | `restore-baseline` (Step 3) | not in git; never committed |
| IA image dump (62 ZIPs, ~859 GB) | `https://archive.org/download/stack-exchange-images` | image recovery (Step 5) | optional external asset; highest-fidelity image source |
| Reference ZIMs | `https://archive.org/download/stackoverflow-final-zim`, `https://archive.org/download/stackoverflow-nopic-zim` | verification ground truth | MD5s from the July 2026 upload records: `5a2ba64aba5264df6722bfae1eb887b5` (final), `95beed5489d09793051b9f753d220d78` (nopic) |
| Image-recovery corpus | `RECOVERY_ROOT` (`https://archive.org/download/stackoverflow-images-recovery`) | image recovery (Step 5) | optional; see `docs/configuration.md` |

Checksums: the two ZIM MD5s above are quoted from the **July 2026 upload
records** (this repo does not invent or regenerate checksums; sizes live in
`data/baseline-2026-07.json`). The baseline bundle's own checksums are
verified by `restore-baseline` from its `MANIFEST.sha256` /
`stage_listing_hash`.