# Image Recovery Runbook

End-to-end guide for the image recovery flow: turning "images are missing
from the stage" into "a validated manifest of what to fetch and where from",
then recovering, syncing, and finalizing. The authoritative module-level
reference is [`recovery/README.md`](../recovery/README.md) (hardening
H1–H10, manifest schema, edge-resolver candidate scoring); this runbook is
the operational walk-through.

## Context (July 2026 reference)

The July 2026 build staged **521,489 initial placeholders** (sotoki image
download failures). Recovery recovered **410,856 (78.8%)**; the remaining
**120,633** were unrecoverable and render as placeholders. Those counts are
in `data/baseline-2026-07.json` and are the reference for expected-delta
comparison, not strict equality.

## Hardening rules that shape every step

- **Dry-run is the default.** No module opens a socket, writes payloads, or
  mutates the corpus unless you explicitly opt in. Fetch-capable code checks
  `config.fetch_ok` first and returns `status="dry-run"`.
- **`--fetch` is the opt-in.** Fetching requires `--fetch` AND disabling
  dry-run (`--no-dry-run`, or `RECOVERY_DRY_RUN=0 RECOVERY_FETCH=1`).
  `--fetch` with the dry-run default still means zero sockets.
- **No quota evasion.** On persistent 429/403 the run checkpoints and stops;
  the operator resolves it. Workers never switch interfaces or IPs.
- **Manifest-based sync only.** Never `rsync --ignore-existing` (see the
  LESSON in step 7).

## The canonical pipeline

Run every module as `python3 -m recovery.<module> ...` from the repo root.

### 1. Inventory — `recovery/inventory_stage.py`

Scan the staged HTML tree, extract every `/images/<hash>` reference (plus
the anchor URL when wrapped in `<a href=...><img src=.../images/<hash>>`),
emit one row per `(hash, page_path)`.

```bash
# illustrative
python3 -m recovery.inventory_stage --stage-dir "$STAGE_DIR" --out inventory.tsv
```

### 2. Classify — `recovery/classify_missing.py`

A hash is **missing** when its staged file is absent **or** is a confirmed
placeholder. Placeholder confirmation requires **both** the 1,852-byte size
prefilter **and** the content SHA-256 from `data/placeholder-spec.json` —
**never size alone** (real images can share the 1,852-byte size). Missing
hashes are classified per anchor source: `ia_stack_imgur`,
`sstatic_candidate`, `other_http`, `no_original_url`.

```bash
# illustrative
python3 -m recovery.classify_missing --inventory inventory.tsv \
  --stage-images-dir "$STAGE_DIR/images" \
  --out-classified classified.tsv --out-summary summary.tsv
```

### 3. IA basename manifest — `recovery/build_ia_manifest.py`

Rows whose URL matches the Stack Imgur IA naming pattern become
`(hash, ia_filename)` rows for the archive.org image corpus
(`stack-exchange-images`, 62 ZIPs / ~859 GB).

### 4. XML dump scans — `recovery/recover_unmapped.py`, `recovery/recover_posthistory.py`

Scan the dump XML for `i.stack.imgur.com` filenames and hash the canonical
sstatic forms against the still-missing set. `recover_unmapped.py` reads
raw `Posts.xml` bytes from stdin, so it is fed straight from the 7z archive
(illustrative):

```bash
# illustrative — Posts.xml streamed from the dump via a 7z pipe
7z x -so stackoverflow.com.7z Posts.xml \
  | python3 -m recovery.recover_unmapped --hashes still_missing.tsv --out xml_hits.tsv
```

`recover_posthistory.py` does the same against `PostHistory.xml`
(`--already-recovered` excludes earlier hits); `extract_all_image_hosts.py`
maps the host landscape.

### 5. External-edge resolver — `recovery/rescue_edge_cases.py`

Per-hash scored candidate synthesis (URL cleanup, camo decode, badge
transforms, YouTube/PlantUML/Mermaid/GitHub/Dropbox/Imgur specials, HTML
page scraping). Produces the deterministic candidate manifest **offline**
(dry-run); adding `--fetch --no-dry-run` performs the actual downloads,
bounded per hash, through the hardened opener.

### 6. Size upgrades — `recovery/upgrade_small_ia_images.py`

Re-download tiny IA results from the live CDN when a larger, valid result
exists; records upgrade candidates (`content_sha256` stays the
original-bytes hash).

### 7. Validated sync — `recovery/sync_to_stage.py`

The **only** stage-modifying path in the whole pipeline. Dry-run first, then
`--no-dry-run`. Every source is verified (magic bytes + decode + placeholder
check + manifest SHA) and only placeholder/absent stage targets are
overwritten; real stage images are never touched. Copies are temp-file +
atomic rename.

> **LESSON — the `rsync --ignore-existing` incident.**
> *Symptom:* placeholder files were silently left in place after a
> "recovery sync", so the ZIM still contained placeholder bytes for hashes
> that had actually been recovered.
> *Cause:* `rsync --ignore-existing` treats a partial/truncated target as
> complete and skips it; it also skips any existing file regardless of
> whether it is a placeholder.
> *Prevention:* sync only via manifest-compare (`sync_to_stage.py`) which
> checks name + size + hash and only overwrites placeholder or absent
> targets.

### 8. Finalize — `recovery/finalize_unavailable.py`

For hashes still missing after every source, write the versioned,
semantically labelled placeholder (idempotent; real images are never
overwritten). This is the implemented behavior behind the
`finalize-placeholders` make target.

### 9. Verify — `recovery/verify_images.py`

Every manifest hash has a valid, non-placeholder (for recovered entries)
stage file; placeholder count; optional random deep-decode sample; PASS/FAIL
exit.

## External sources

| Source | Detail | Terms / limits |
|---|---|---|
| **IA dump** | `https://archive.org/download/stack-exchange-images` — 62 ZIPs, ~859 GB; the highest-fidelity source for Stack Imgur assets | optional external asset, **not in git**; download only what the manifest says is missing; subject to archive.org terms |
| **Live CDN** | `https://i.sstatic.net/FILENAME` (plus the `i.stack.imgur.com` variants found in the dump) | rate-limited: one **aggregate** budget per origin shared by ALL workers; verify current origin terms before a large run; quota-stop (checkpoint-and-stop) on 429/403 — never evade |
| **XML scanning** | `Posts.xml` / `PostHistory.xml` from the dump (7z pipe into `recover_unmapped.py` / `recover_posthistory.py`) | local; discovers assets the original staging missed |

## Hash scheme

Images are addressed by

```
md5("https://i.sstatic.net/FILENAME")
```

That MD5 (of the canonical CDN URL string) is the Redis key scheme during
staging **and** recovery (`docs/provenance.md`). Critical wrinkle: **the
dump carries `i.stack.imgur.com` URLs, while sotoki hashes the sstatic
form**. Recovery therefore hashes the canonical sstatic variants of every
imgur filename discovered in the XML scans and compares them against the
still-missing set. Do not change the scheme without re-deriving every
downstream count.

## Placeholder policy

- Prefilter: staged file size == **1,852 bytes** (`PLACEHOLDER_BYTES`,
  `data/placeholder-spec.json`).
- Confirmation: content **SHA-256** must also match the spec — size alone is
  not proof.
- The spec's `sha256` field is still `null` until a canonical placeholder
  image is captured; `recovery/lib/placeholders.py::write_placeholder_for`
  returns the hash to record. Until then, size-only detection is refused and
  verification degrades to a WARN.

## Expected outcomes

- July reference: **78.8%** of placeholders recovered (410,856 / 521,489);
  the rest render as placeholders.
- New runs compare against the baseline with expected deltas — a different
  dump may recover more or fewer.
- After the pass: `verify_images.py` PASS, and `audit_stage.py` /
  `audit_zim.py` / `compare_baseline.py` see no placeholder-sized file
  shipped as a real image without a verified content hash.

## Worker usage

- **Local, single-interface is the default.** `docker/image-worker/` runs
  the same two services (`so-image-worker` = live CDN,
  `so-ia-basename` = IA ZIP shards) on one machine with a normal network
  connection, `bin/run-worker cdn|ia` as the wrapper. Workers bind nothing,
  never touch the stage, and write only to their `--out-dir` / `--state-dir`.
- **NAS/WireGuard acceleration is explicitly out of scope here.** Routing
  worker traffic through a VPN tunnel is entirely **external and
  operator-managed** (the operator configures the tunnel on the host; the
  container just uses the host network), contains **no** config/keys/rotation
  logic in this repo, and requires a **separate, independent security
  review** before production use. Running the workers requires no tunnel at
  all. See [`docs/nas-worker.md`](nas-worker.md).

## Smoke test first

See `docker/image-worker/README.md` and `docs/nas-worker.md`: serve two tiny
PNGs plus one HTML page from `127.0.0.1`, run the worker with `--limit 3`,
expect two `recovered/<hash>` files, `status: ok` rows with
`content_sha256` + `derived_sha256`, and the HTML row rejected with
`status: rejected`.

```bash
python3 -m pytest tests/recovery -q   # 37 offline tests, socket-blocking
```