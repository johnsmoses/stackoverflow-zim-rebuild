# NAS image workers

Dockerized image-recovery workers that fetch the missing StackOverflow images
(the ~120k unrecoverable placeholders from the July 2026 baseline, and any
new ones discovered by later inventories). This document covers the worker
layout, security model, rate limiting, and the workflows that feed the
validated sync. It is the authoritative reference for
`docker/image-worker/` (see also `docker/image-worker/README.md` for the
operational quickstart).

## System design overview

```
inventory/classify (recovery/*) ──► missing manifest (TSV)
        │
        ▼
docker/image-worker (this repo)
   so-image-worker     recover_convert_images.py        (live CDN)
   so-ia-basename      ia_sstatic_basename_recovery.py  (IA ZIP shards)
        │  out-dir/<hash> + results.jsonl (+ SQLite checkpoints)
        ▼
operator copies results back  (rsync/scp of out-dir + manifests)
        │
        ▼
recovery/sync_to_stage.py ──► STAGE_DIR/images          (validated sync)
```

- **Local workers are the default** — a single machine with Docker Compose,
  a normal single-interface network connection, and the manifests. No NAS,
  no tunnel, no special routing is required to run any worker.
- **NAS acceleration is optional** — the same two containers can run on a
  NAS/always-on box with more bandwidth; the artifacts are identical.
- **The stage is only ever modified by `recovery/sync_to_stage.py`.** That
  validated sync (magic bytes + decode + placeholder check + manifest SHA)
  is the *only* stage-modifying path. Workers write exclusively to their
  `--out-dir` and `--state-dir`; nothing in `docker/` can reach the stage.

## Security model

The container is deliberately boring (all hardenings below are enforced in
`docker/image-worker/compose.yaml` and the `Dockerfile`):

| Item | Setting | Why |
|---|---|---|
| Runtime user | non-root `worker` (uid 10001), `USER worker` + `user: 10001:10001` | H1: no root in the container |
| Capabilities | `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]` | H2: zero capabilities; no NET_ADMIN/NET_RAW, no privileged mode, no Docker socket, no device mounts, no `network_mode: host` |
| Root filesystem | `read_only: true`, `/tmp` as tmpfs | H4: nothing under `/` is writable except the tmpfs and the mounted work dirs |
| Inputs | manifests + IA shards mounted `:ro` | H3: read-only inputs; only `/work/state` and `/work/recovered` are writable |
| Networking | no `ports:` section, default bridge network only | H5: the workers never listen on any port |
| Secrets | `.env` + WireGuard/key patterns gitignored; `.env.example` has placeholders only | H6: nothing real is ever committed |

The workers also never open a listening socket, never read the Docker
socket, and never request more than the image's default network egress.
Payload hardening is in the scripts: magic-byte validation (Content-Type is
advisory only; HTML/XML/SVG and error bodies rejected, decode verified, byte
caps enforced — H10) and provenance-preserving WebP conversion
(`content_sha256` original + `derived_sha256` converted, H11).

## Rate limiting (H8)

Rate is budgeted **per origin, aggregated across ALL workers**:

- One budget per origin, shared by every worker you run. Example: a
  **60 requests/minute** ceiling per origin. With `M` workers running
  concurrently against the same origin, each worker must stay within
  `60/M` req/min.
- The per-request pace is `--delay` (seconds between requests per host,
  default 1.0 s — conservative). Scale it up when adding workers:
  `delay >= 60 * M / budget_per_minute`.
- `--concurrency` bounds in-flight requests per worker (default 4) but does
  not change the per-host pacing; the aggregate formula is the binding
  constraint.

**Origin-terms compliance:** the budget assumes the origin's terms permit
automated fetching at that rate. Verify the current terms of every origin
you fetch from before a large run and stay below any published limits. The
workers never bypass a limit — see quota-stop below.

## Quota-stop (H7)

When an origin answers **429 or 403** and keeps doing so after all retries
for an item, the worker logs `Quota exhausted; checkpointing and stopping`,
persists every completed result to the SQLite checkpoint, writes the
authoritative `results.jsonl`, and exits 0. It **never** switches interfaces
or IPs, and it never retries through a different route — quota handling is
checkpoint-and-stop, always. The operator resolves the situation (wait, or
adjust the aggregate budget) and re-runs; the run resumes from the
checkpoint and never re-downloads completed hashes.

## WireGuard: explicitly OUT OF SCOPE (H9)

This repository performs **no** interface manipulation, no IP rotation, and
no binding to any tunnel. It contains **no** WireGuard configuration, **no**
keys, and **no** rotation logic — in `compose.yaml`, in the scripts, in
`.env.example`, or anywhere else. The `.gitignore` patterns (`*.wg.conf`,
`wg*.conf`, `.wg*`, `*.key`, `wg0.conf`, `wg3.conf`, `*private*key*`)
exist only to keep such material out of the repo if it ever lands in a
working directory.

Routing worker traffic through a VPN tunnel (e.g. to work around a
geo-block) is **entirely external and operator-managed**: the operator
configures the tunnel on the host, and the container simply uses the host's
network. Any such deployment requires a **separate, independent security
review** before it is put into production. Running the workers requires no
tunnel at all.

## Workflows

### Smoke test (no real network)

See `docker/image-worker/README.md` — serve two tiny PNGs plus one HTML page
from `python3 -m http.server` on `127.0.0.1`, write a 3-row fixture manifest
(two image URLs, one HTML URL), then:

```sh
docker compose -f docker/image-worker/compose.yaml build
docker compose -f docker/image-worker/compose.yaml run --rm so-image-worker \
    recover_convert_images.py \
    --manifest /work/manifests/smoke.tsv \
    --state-dir /work/state --out-dir /work/recovered --limit 3
```

Expect: two `recovered/<hash>` files, `status: ok` rows with
`content_sha256` + `derived_sha256`, and the HTML row rejected with
`status: rejected`. `bin/run-worker cdn --limit 3` is the equivalent wrapper.

### Production workflow

1. **Prepare the manifest** — a TSV of missing hashes. CDN rows
   (`hash<TAB>url`) come from the recovery pipeline's classification;
   IA rows (`hash<TAB>ia_filename`) from `recovery/build_ia_manifest.py`.
2. **Run the workers** on the NAS (or locally) with `bin/run-worker cdn`
   and/or `bin/run-worker ia`, with a `--limit` for the first pass. Monitor
   stderr; a quota-stop exits 0 with the checkpoint saved.
3. **Copy results back** — the operator copies the worker's `recovered/`
   dir (hash-named files + `results.jsonl`) and, for resume, `state/`
   (SQLite checkpoints) to the build machine. Nothing in this step touches
   the stage.
4. **Build the sync manifest** — derive the recovery manifest consumed by
   `recovery/sync_to_stage.py` from the worker `results.jsonl` rows. For
   rows where a WebP conversion happened (`derived_sha256` non-empty), the
   sync manifest's `content_sha256` must be the SHA-256 of the bytes on
   disk, i.e. `derived_sha256`; otherwise use `content_sha256`. This keeps
   `sync_to_stage`'s source-hash validation correct.
5. **Validate + sync** — `python3 -m recovery.sync_to_stage` (dry-run
   first, then `--no-dry-run`). It verifies every source (magic bytes +
   decode + placeholder + hash) and only ever overwrites placeholder or
   absent stage targets; real stage images are never touched.
6. **Verify** — `recovery/verify_images.py` and the snapshot verifier audit
   the outcome (see `docs/verification.md`).

## Storage and state guidance

- **SQLite resumable checkpoints**: each worker keeps
  `state/recovery.sqlite` with one row per hash (`status`, `source`,
  `content_sha256`, `derived_sha256`, `bytes_in`, `bytes_out`, `mime`,
  `error`, `updated_at`). Interrupted runs resume from this store; hashes
  already `ok` are never re-processed. Keep the state dir per worker and per
  manifest so resume targeting stays unambiguous.
- **`results.jsonl`** in the out-dir is the authoritative report (rewritten
  from the checkpoint store at the end of every run, so it is complete and
  deduplicated). Schema: `hash, source_url, status, content_sha256,
  derived_sha256, mime, bytes, timestamp`.
- **Permissions**: host `state/` and `recovered/` must be writable by uid
  10001 (`chown 10001:10001 state recovered`); `manifests/` and `ia-shards/`
  only need to be readable. Working data is never committed (gitignore
  covers `.env`, key material, SQLite files, and the worker working dirs).
- **Disk**: `recovered/` grows by one file per recovered hash (WebP, small);
  IA shards stay on the NAS and are mounted read-only. Temporary conversion
  space uses the container's `/tmp` tmpfs.