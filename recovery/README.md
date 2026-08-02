# Image recovery pipeline

The complete image recovery pipeline for the rebuild: a shared library, the
core modules that turn "images are missing from the stage" into "here is a
manifest of what to fetch and where from", and the Stage 2 edge resolver,
sync/finalize and verification tooling. Everything is network-inert by
default and every artifact is a versioned TSV/JSONL manifest.

## Canonical flow

1. **Inventory** — `inventory_stage.py`: scan the staged HTML tree, extract
   every `/images/<hash>` reference (and the anchor URL when wrapped in
   `<a href=...><img src=.../images/<hash>>`), emit one row per
   `(hash, page_path)`.
2. **Identify placeholder/absent** — `classify_missing.py`: a hash is
   missing when its staged file is absent OR is a confirmed placeholder
   (size AND content SHA-256 vs `data/placeholder-spec.json`, H10). Never by
   size alone.
3. **Classify URL-bearing records** — same step: missing hashes get one
   class per anchor source: `ia_stack_imgur`, `sstatic_candidate`,
   `other_http`, `no_original_url`.
4. **IA basename manifest** — `build_ia_manifest.py`: rows whose URL matches
   the Stack Imgur IA naming pattern become `(hash, ia_filename)` rows for
   the archive.org corpus.
5. **XML dump scans** — `recover_unmapped.py` (raw Posts.xml) and
   `recover_posthistory.py` (PostHistory.xml) scan the dump for
   `i.stack.imgur.com` filenames and hash the canonical sstatic forms
   against the still-missing set; `extract_all_image_hosts.py` maps the host
   landscape.
6. **External-edge resolver** — `rescue_edge_cases.py`: per-hash scored
   candidate synthesis (URL cleanup, camo decode, badge transforms, YouTube/
   PlantUML/Mermaid/GitHub/Dropbox/Imgur specials, HTML page scraping) →
   candidate manifest (dry-run) or recovered payloads (`--fetch`).
7. **Size upgrades** — `upgrade_small_ia_images.py`: prefer larger live-CDN
   results over tiny IA results; records upgrade candidates.
8. **Validated sync** — `sync_to_stage.py`: manifest-compare + copy into the
   stage, never `rsync --ignore-existing`, never clobbering real stage
   images.
9. **Final unavailable placeholders** — `finalize_unavailable.py`: for hashes
   still missing after every source, write the versioned semantically
   labelled placeholder (idempotent).
10. **Verify** — `verify_images.py`: every manifest hash has a valid,
    non-placeholder (for recovered entries) stage file; placeholder count;
    optional random deep-decode sample.

Every manifest carries: `schema_version, hash, source_url, source_class,
status, content_sha256, derived_sha256, mime, bytes, timestamp,
tool_version`. Deduplication is per `(hash, source_url)` — a hash with many
sources keeps many rows (H8).

## Modules

| Module | Purpose |
|---|---|
| `recovery/__init__.py` | package version (`recovery-0.1.0`) |
| `recovery/lib/config.py` | `RecoveryConfig` dataclass; `RECOVERY_*` env / `--config` JSON; `fetch_ok` gate |
| `recovery/lib/manifest.py` | `ManifestWriter` (TSV+JSONL, append-only, atomic temp+rename, (hash,url) dedup) + `ManifestReader` |
| `recovery/lib/images.py` | sniff/decode/placeholder checks; hardened `download_image` (H2–H7); `convert_to_webp`; `validate_url`; throttle + checkpoint stores |
| `recovery/lib/placeholders.py` | deterministic placeholder PNG/WebP; `write_placeholder_for` (records the versioned sha) |
| `recovery/inventory_stage.py` | CLI: inventory TSV from the staged HTML tree |
| `recovery/classify_missing.py` | CLI: absent/placeholder detection + URL classification + summary |
| `recovery/recover_unmapped.py` | CLI: raw-bytes Posts.xml scanner (port) → manifest |
| `recovery/recover_posthistory.py` | CLI: PostHistory.xml scanner (port) → manifest |
| `recovery/extract_all_image_hosts.py` | CLI: unique image hosts from Posts.xml → TSV |
| `recovery/build_ia_manifest.py` | CLI: IA basename manifest from classified rows |
| `recovery/rescue_edge_cases.py` | CLI: external-edge resolver — scored candidate system (clean_url, camo decode, badge transforms, specials, page scrape), candidate manifest by default, downloads with `--fetch` |
| `recovery/upgrade_small_ia_images.py` | CLI: re-download tiny IA files from the live CDN; record upgrade candidates (larger + valid) |
| `recovery/sync_to_stage.py` | CLI: validated manifest-based sync into the stage (placeholder/absent targets only; atomic copy) |
| `recovery/finalize_unavailable.py` | CLI: final versioned placeholders for still-missing hashes (idempotent) |
| `recovery/verify_images.py` | CLI: verify manifest hashes have valid stage files; placeholder count; deep-decode sample; PASS/FAIL |

Run every CLI as a module (`python3 -m recovery.inventory_stage ...`) from
the repo root, or execute the file directly; both work.

## Manifest schema

| Field | Meaning |
|---|---|
| `schema_version` | `1` |
| `hash` | the sotoki image hash (md5 of the canonical URL) |
| `source_url` | origin of the record (download URL / page anchor / IA URL) |
| `source_class` | provenance bucket: `stage`, `xml_dump`, `posthistory_xml`, `ia_stack_imgur`, ... |
| `status` | attempt status: `candidate`, `recovered`, `dry-run`, `quota_exhausted`, `error`, ... |
| `content_sha256` | SHA-256 of the ORIGINAL downloaded bytes (H7) |
| `derived_sha256` | SHA-256 of the WebP-converted bytes; empty when no conversion (H7) |
| `mime` | sniffed MIME |
| `bytes` | payload size |
| `timestamp` | UTC ISO-8601 |
| `tool_version` | `sotoki-<ver>` or `recovery-<ver>` |

TSV and JSONL carry identical data; JSONL is a JSON object per line.

## Dry-run and `--fetch`

**Dry-run is the default** (H1). In dry-run mode:

- no sockets are ever opened (fetch-capable code checks `config.fetch_ok`
  first and returns `status="dry-run"`),
- no payloads are written,
- no corpus is mutated,
- manifests explicitly requested (`--out`, `--out-classified`, ...) are
  still written.

To actually fetch, pass `--fetch` AND disable dry-run
(`--no-dry-run` / `RECOVERY_DRY_RUN=0 RECOVERY_FETCH=1`). `--fetch` with the
dry-run default still means zero sockets. Stage 2 modules follow the same
rule: `rescue_edge_cases.py --classified ... --out-manifest ... --out-dir ...`
produces the deterministic candidate manifest offline; adding
`--fetch --no-dry-run` performs the actual downloads (page scraping included,
bounded per hash).

## Hardening (H1–H10)

1. **H1** — network gated behind explicit `--fetch`; dry-run default.
2. **H2** — HTTP(S) only; credentials rejected; every hop (initial URL and
   each redirect) resolved and validated to a global IP; connected peer
   re-validated (private/loopback/link-local/ULA/multicast/reserved refused).
3. **H3** — TLS verification always on (never disabled); max 5 redirects;
   10 s connect + 30 s read timeouts; 25 MB streaming byte cap; temp-file +
   atomic rename promotion.
4. **H4** — descriptive User-Agent; per-host throttle (default 0.5 s);
   bounded concurrency (default 1); Retry-After honored; exponential backoff
   (base 2 s, max 60 s, max 5 retries); resumable `CheckpointStore` state
   file.
5. **H5** — no automatic VPN/IP rotation or quota evasion: on 429/403 after
   retries the run STOPS and checkpoints; the operator must intervene.
6. **H6** — Content-Type advisory only: magic bytes + decode verification;
   HTML/XML/SVG rejected; byte limits (decompression bombs) and pixel limits
   (max 100 MP / 16384²) enforced.
7. **H7** — WebP conversion preserves original SHA-256/provenance;
   `content_sha256` = original, `derived_sha256` = converted asset.
8. **H8** — dedup per `(hash, source_url)`: one-to-many source/attempt
   records are preserved.
9. **H9** — tests use fixtures/mocks only; a socket-blocking fixture forbids
   accidental outbound sockets in the test suite.
10. **H10** — placeholder detection: size 1852 is a prefilter only; exact
    versioned content SHA-256 confirmation required. Never classify or
    delete by size alone (spec `sha256` is still `null` in
    `data/placeholder-spec.json` — record it once the canonical placeholder
    image is captured; `write_placeholder_for` returns the hash to record).

Stage 2 applies the same ten hardenings everywhere:

- `rescue_edge_cases.py` reuses `download_image`/`fetch_page_text`
  (H2–H7), records every attempted `(hash, url)` in the manifest (H8),
  stops on quota exhaustion (H5), and is dry-run by default (H1).
  `fetch_page_text` (recovery/lib/images.py) fetches HTML *pages* through
  the same hardened opener for scraping — page text is never written as an
  image (H6 intact).
- `upgrade_small_ia_images.py` re-downloads through `download_image`;
  `content_sha256` stays the original-bytes hash (H7).
- `sync_to_stage.py` validates source (magic bytes + decode + placeholder
  check + manifest SHA) and only ever overwrites placeholder/absent targets
  (H10); copies are temp-file + atomic rename (H3).
- `finalize_unavailable.py` writes placeholder content via
  `write_placeholder_for` so it round-trips with `is_placeholder` (H10);
  real images are never overwritten.
- `verify_images.py` reports placeholder counts and fails on
  missing/invalid/placeholder recovered entries.

## Edge resolver candidate scores

`page_candidates_enhanced` scores HTML candidates: meta og:/twitter: 90,
JSON-LD 85, `link[rel=image_src]` 80, download anchors 60, `<source>` 55,
srcset 50 + descriptor/100 (capped +30), `<img src>` 40, lazy attrs
(data-src/data-original/data-lazy-src/data-full/...) 35, background-image 30,
favicon 0 (appended last). Special transforms are tried before the cleaned
original URL; the whole synthesis is deterministic, so dry-run candidate
manifests are reproducible with zero sockets.

## External sources

- **IA dump** — `https://archive.org/download/stack-exchange-images`:
  62 ZIPs, ~859 GB, the highest-fidelity source for Stack Imgur assets.
- **Live CDN** — `https://i.sstatic.net/<filename>` (and the
  `i.stack.imgur.com` variants found in the dump).
- **Optional acceleration** — NAS / WireGuard workers (see
  `docs/nas-worker.md`); nothing in this repo references any
  private host.

## Tests

`tests/recovery/` — pytest, offline-only (socket-blocking fixture):

```
python -m pytest tests/recovery -q
```