# Docker image workers

Self-contained container workers that recover missing StackOverflow images.
Two scripts run on one hardened image:

| Service | Script | Job |
|---|---|---|
| `so-image-worker` | `recover_convert_images.py` | Fetch `hash<TAB>url` rows from the live CDN, validate, convert to WebP |
| `so-ia-basename` | `ia_sstatic_basename_recovery.py` | Extract `hash<TAB>ia_filename` rows from Archive.org ZIP shards, validate, convert to WebP |

Both write `--out-dir/<hash>` (validated, WebP-converted payload) plus
`--out-dir/results.jsonl` and keep a resumable SQLite checkpoint in
`--state-dir/recovery.sqlite`. Inputs (manifests, IA shards) are mounted
read-only; the workers never touch the stage (see `docs/nas-worker.md` for
the full system design and the production workflow).

## Default operation: local, single interface

The DEFAULT deployment is a local, single-interface run — no NAS and no VPN
tunnel needed. The worker binds nothing, listens on no port, and uses the
host's normal network path:

```sh
docker compose -f docker/image-worker/compose.yaml build
docker compose -f docker/image-worker/compose.yaml run --rm so-image-worker \
    recover_convert_images.py \
    --manifest /work/manifests/missing_image_url_map.tsv \
    --state-dir /work/state --out-dir /work/recovered --limit 10
```

(`bin/run-worker cdn|ia ...` wraps the same compose invocation and fills in
the `/work/...` defaults for you.)

## Rate limiting — aggregate ceiling across ALL workers (H8)

Rate is budgeted per origin, AGGREGATED across every worker you run. One
budget, shared by N workers:

- Example budget: **60 requests/minute per origin** (check the origin's
  current terms — see below). With `M` workers running concurrently against
  the same origin, each worker must stay within `60/M` req/min.
- The per-request pacing is `--delay` (seconds between requests per host,
  default **1.0 s** — conservative). Scale `--delay` up when more workers
  are added: `delay >= 60 * M / budget_per_minute`.
- Concurrency (`--concurrency`, default 4) multiplies in-flight requests,
  but the per-host delay still paces the request rate; keep the aggregate
  formula above as the binding constraint.

Origin-terms compliance: the budget assumes the origin's terms permit
automated fetching at that rate. Verify the current terms before any large
run and stay below any rate limits the origin publishes. The worker never
bypasses a limit — see the next section.

## Quota-stop (H7)

When an origin answers **429 or 403** and keeps doing so after all retries,
the worker logs `Quota exhausted; checkpointing and stopping`, saves every
completed result to the SQLite checkpoint, and exits 0. It **never** switches
interfaces or IPs, and it never retries against a different route. A re-run
after the operator resolves the situation resumes from the checkpoint
(completed hashes are never re-downloaded).

## Validation and provenance (H10, H11)

- **Magic-byte validation**: Content-Type headers are advisory only. Every
  payload is sniffed; HTML/XML/SVG/error bodies are rejected (`status:
  rejected`), decode is verified when a decoder is available, and payloads
  over `--max-bytes` (default 25 MiB) are rejected.
- **Provenance**: WebP conversion preserves the original. `content_sha256`
  is the SHA-256 of the ORIGINAL payload; `derived_sha256` is the SHA-256 of
  the converted payload (empty when no conversion was performed). The two
  are recorded separately in `results.jsonl`.

## WireGuard / IP rotation is OUT OF SCOPE (H9)

This repository performs **no** interface manipulation, no IP rotation, and
no binding to any tunnel. It contains no WireGuard configuration, no keys,
and no rotation logic — the `.gitignore` patterns (`*.wg.conf`, `wg*.conf`,
`.wg*`, `*.key`, ...) exist to keep such material out of the repo if it ever
lands in a working directory. Any deployment that routes worker traffic
through a VPN tunnel is entirely **external and operator-managed**, and
requires a separate, independent security review before deployment. Running
the workers does not require a tunnel at all.

## Smoke test (no real network)

1. Create a fixture manifest `manifests/smoke.tsv` with two local PNG URLs
   and one HTML URL:

   ```
   aaaaaaaaaaaaaaaa01<TAB>http://127.0.0.1:8765/p1.png
   aaaaaaaaaaaaaaaa02<TAB>http://127.0.0.1:8765/p2.png
   aaaaaaaaaaaaaaaa03<TAB>http://127.0.0.1:8765/index.html
   ```

   (Hashes must be 16–32 lowercase hex chars; the worker only fetches
   http(s) URLs.)

2. Serve the fixture files on loopback (generate two real tiny PNGs with the
   repo's stdlib-only fixture helper):

   ```sh
   mkdir -p /tmp/so-smoke && cd /tmp/so-smoke
   python3 -c "from recovery.lib.images import tiny_png_bytes; open('p1.png','wb').write(tiny_png_bytes(6,6))"
   python3 -c "from recovery.lib.images import tiny_png_bytes; open('p2.png','wb').write(tiny_png_bytes(8,8))"
   printf '<html><body>nope</body></html>' > index.html
   python3 -m http.server 8765 &
   ```

   (Run from the repo root so `recovery.lib.images` is importable, or copy
   two real small PNGs in.)

3. Run the worker with a tiny limit:

   ```sh
   docker compose -f docker/image-worker/compose.yaml build
   docker compose -f docker/image-worker/compose.yaml run --rm so-image-worker \
       recover_convert_images.py \
       --manifest /work/manifests/smoke.tsv \
       --state-dir /work/state --out-dir /work/recovered --limit 3
   ```

   Note: the image's `ENTRYPOINT` is `python3`, so the run form is
   `... run --rm so-image-worker recover_convert_images.py ...` (no leading
   `python3`).

4. Verify: `recovered/<hash>` files exist for the two PNG rows, the HTML row
   has `"status": "rejected"` in `recovered/results.jsonl`, and the PNG rows
   carry both `content_sha256` and `derived_sha256`.

Run the second service the same way against a fixture shard:

```sh
docker compose -f docker/image-worker/compose.yaml run --rm so-ia-basename \
    ia_sstatic_basename_recovery.py \
    --manifest /work/manifests/smoke_ia.tsv \
    --ia-dir /work/ia-shards --state-dir /work/state \
    --out-dir /work/recovered --limit 10
```

## Environment

Copy `.env.example` to `.env` in this directory (never commit real values)
to point the compose mounts at your host directories. The host `state/` and
`recovered/` dirs must be writable by uid 10001 (`chown 10001:10001
state recovered`); `manifests/` and `ia-shards/` only need to be readable.