# Troubleshooting

Operational lessons from the July 2026 rebuild, recorded as **LESSONS**
(symptom → cause → prevention). These are historical incidents — the fixes
are already in the repository (patches, scripts, runbook procedures), not
unresolved defects. If you hit a symptom, check the prevention column first;
if it still reproduces, the prevention is incomplete.

---

## LESSON — libzim title-index crash at ~21.7M questions

- **Symptom:** assembly aborts with a crash inside libzim while building
  the title index, reproducibly near ~21.7M question entries.
- **Cause:** question titles containing Unicode control/format characters
  (e.g. zero-width / bidi / private-use ranges) that libzim's title index
  cannot encode; the ZIM creator was left in a corrupted state.
- **Prevention:** the `sanitize_zim_title` patch (in the sotoki patch
  series, `patches/sotoki/`, part of the assemble-only builder work) strips
  control/format characters from titles before they reach libzim; the
  corruption-override resets (`shared.creator.can_finish = True`) were also
  removed so any remaining corruption surfaces explicitly at `finish()`.

## LESSON — sort OOM (90 GB buffer)

- **Symptom:** the metadata preparation pass exhausts memory — GNU sort
  attempted a ~90 GB in-memory buffer and the process was OOM-killed.
- **Cause:** sotoki's `utils/preparation.py` ran GNU sort with an
  unbounded/oversized buffer and no explicit temporary directory, so the
  sort stayed in RAM instead of spilling to disk.
- **Prevention:** patch 0005 bounds the sort buffer
  (`--buffer-size 32G` kept unconditionally) and passes
  `--temporary-directory` to GNU sort only when `SOTOKI_SORT_TMP` is set —
  point it at a directory on a disk-backed filesystem (see the /tmp lesson
  below).

## LESSON — /tmp tmpfs overflow

- **Symptom:** mysterious "no space left on device" failures during
  extraction, sorting, or assembly, while the WORK_ROOT filesystem still has
  plenty of room.
- **Cause:** tools writing scratch data to `/tmp`, which is a small tmpfs
  (RAM-backed) on many systems — 755 GB-stage workflows overflow it quickly.
- **Prevention:** point every temp location at disk: `TMP_DIR` (default
  `$WORK_ROOT/tmp`), `SOTOKI_SORT_TMP`, and `--tmp-dir` for assembly. Never
  let `TMPDIR` default to the OS tmpfs for real runs.

## LESSON — Redis connection refused (errno 111)

- **Symptom:** the build fails early with `Connection refused` / errno 111
  when sotoki or the audit scripts try to reach Redis.
- **Cause:** the isolated instance was never started — `bin/redis` requires
  an explicit `start`, and `bin/assemble`'s preflight demands a running
  instance.
- **Prevention:** start Redis/Valkey first: `bin/redis start` (or
  `make redis ARGS=start`), then `bin/redis status` before any build or
  update pass.

## LESSON — the `rsync --ignore-existing` incident (placeholders skipped)

- **Symptom:** after a "recovery sync", the ZIM still contained placeholder
  bytes for hashes that had actually been recovered.
- **Cause:** `rsync --ignore-existing` treats any existing target as
  complete — including placeholder files — and never replaces them, so
  recovered payloads never reached the stage.
- **Prevention:** manifest-based sync only (`recovery/sync_to_stage.py`):
  compare name + size + hash against the recorded manifest, and only ever
  overwrite placeholder or absent stage targets. See
  `docs/recovery-runbook.md` step 7.

## LESSON — occupied Redis port / unrelated process

- **Symptom:** `bin/redis start` fails, or a build connects to a Redis
  instance that behaves wrongly (wrong DB, stale data).
- **Cause:** the configured port (`REDIS_URL`, default
  `redis://127.0.0.1:6379/0`) is already held by a process that is not this
  kit's owned instance.
- **Prevention:** `bin/redis` only ever touches a process it can prove it
  owns (pidfile + uid + cmdline match). If the port is occupied by something
  unrelated, it fails with "port in use by unrelated process" — change
  `REDIS_URL` to a free port rather than killing the stranger, and verify
  identity with `bin/redis status` / `bin/redis restart`.

## LESSON — ZIM "corrupted" symptoms (1970s dates, broken random button, 404 tag links)

- **Symptom:** a built ZIM shows wrong entry timestamps (epoch 1970),
  a broken "random page" button, or tag links that 404 inside the archive.
- **Cause:** assembly-time artifacts — corrupted ZIM creator state
  (previously masked by `can_finish = True` overrides), or entries written
  from an incomplete stage — rather than a problem in the source data.
- **Prevention:** `bin/assemble` builds into a unique `.partial` file, runs
  every gate (`audit_zim.py`, `compare_baseline.py`) **before** promotion,
  and the final name is only ever touched by the last atomic `mv`. A failed
  build removes its partial and **never replaces a prior good ZIM** — the
  last good artifact is always recoverable from `$OUTPUT_DIR`.

## LESSON — interrupted incremental run

- **Symptom:** an incremental run is killed mid-pass (power, OOM, operator
  Ctrl-C); a re-run appears to skip pages it shouldn't, or pruning removes
  pages that should stay.
- **Cause:** the snapshot seen set (`snapshot:<id>:seen`) is `DEL`eted at
  the start of each pass, so an interrupted run leaves it incomplete — and
  pruning trusts the seen set.
- **Prevention:** the next run **rescans fully** before any prune; prune
  (`--prune-missing`) only ever runs after a completely successful scan
  (input exhausted, all workers joined, zero item failures, seen-set writes
  committed). Interrupted or failed runs never prune.

## LESSON — quota/429 on the CDN

- **Symptom:** the image workers log persistent 429/403 responses and
  recovery stalls.
- **Cause:** the per-origin aggregate rate budget was exceeded (many workers
  share one budget per origin).
- **Prevention:** quota-stop — the worker checkpoints every completed result
  (SQLite + `results.jsonl`), exits 0, and **never switches interfaces or
  IPs**. Raise `--delay` (or wait) to fit the aggregate budget, then re-run;
  the run resumes from the checkpoint and never re-downloads completed
  hashes. Never try to evade the limit.

## LESSON — zimcheck/zimdump missing

- **Symptom:** `make verify` reports WARN-level entries about skipped
  entry-level checks instead of a clean pass.
- **Cause:** neither the `zimcheck` binary nor an importable `libzim` is on
  the verification host.
- **Prevention:** this is expected degradation, not failure — `audit_zim.py`
  records `degraded: true` and still runs the magic-byte, header, and entry
  count checks, exiting 0 unless a completed check failed. Install
  `zimcheck`/`zimdump` (or a python env with libzim) for full-depth entry
  verification; read the audit report to know the depth achieved.

## LESSON — "baseline not restored"

- **Symptom:** `make assemble` fails with "baseline not restored:
  $WORK_ROOT/.sotoki-rebuild-ok is missing".
- **Cause:** the restore marker does not exist — WORK_ROOT was never created
  empty by `bin/bootstrap`, or a verified `restore-baseline` never completed
  (the marker is cleared at restore start and written only after every
  check passes).
- **Prevention:** run `make bootstrap` (on a fresh, empty WORK_ROOT) then
  `make restore-baseline BASELINE_BUNDLE=/path/to/bundle`. The marker gates
  all destructive operations; a verified baseline restore is the
  precondition for a trustworthy build.

## LESSON — disk full during assembly

- **Symptom:** assembly dies mid-build with ENOSPC, or the preflight refuses
  to start.
- **Cause:** insufficient free space for the output ZIM plus temp/sort
  space; the July baseline's stage is 755 GB and the outputs are 142/69 GB.
- **Prevention:** the preflight enforces **≥ 1.5× the expected output size**
  free on `OUTPUT_DIR` (from `configs/expected-counts.json`), and plan for
  ~1.5× the stage size on the WORK_ROOT filesystem overall (see
  `docs/baseline-assets.md`). Free space before the run; don't place temp
  files inside the stage (H5).