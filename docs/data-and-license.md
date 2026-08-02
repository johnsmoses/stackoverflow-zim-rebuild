# Data & licensing

Four distinct license regimes touch this project. They are distinct; do not
merge them. This document is the compliance reference for **redistribution**
of a rebuilt ZIM. It was expanded to cover what the July 2026 build actually
contains and what the license obligations are.

## 1. This repository: a mixed-license aggregate

The repository is **not** uniformly CC0. The root `LICENSE` is CC0-1.0 and
covers the rebuild-kit tooling only:

- **CC0-1.0:** `bin/`, `scripts/`, `recovery/`, `docker/`, `configs/`,
  `docs/`, `tests/`, project metadata. Dedicated to the public domain.
- **CC0-1.0 (factual records):** `data/baseline-2026-07.json` and
  `data/placeholder-spec.json` are factual records describing the July 2026
  build (counts, sizes, hashes, recovery rates).
- **GPL-3.0-only:** `patches/sotoki/` (the 9-patch series, `series`, and
  `README.md`) is derivative of
  [sotoki](https://github.com/openzim/sotoki) (GPL-3.0) and is distributed
  under **GPL-3.0-only**, full text in
  `LICENSES/GPL-3.0-only.txt`. It is **not** CC0. (`capture/` — verbatim
  installed files + diffs — is operator-local forensic evidence and is
  **excluded** from this repository's distribution; it is not published,
  see `LICENSE.scope.md`.)
- Per-directory mapping: see `LICENSE.scope.md` and `NOTICE-ATTRIBUTION.md`.

The repository ships **no ZIMs, dumps, RDBs, or bulk images** (see
`.gitignore`). Only tiny reference counts/specs (`data/*.json`) and the
patches are tracked.

## 2. sotoki: GPL-3.0

[sotoki](https://github.com/openzim/sotoki) is **GPL-3.0**. It is used as an
external tool pinned at upstream `157ca9a` + the captured 9-patch series.
The patches in `patches/sotoki/` are derivative works of GPL-3.0 sotoki and
inherit **GPL-3.0-only**. This repository ships them as a patch series (not
a fork bundle) so they can be applied onto a GPL-compliant sotoki checkout.
The build output — a ZIM — is a *transformation of Stack Exchange content*
(see §4); sotoki's license governs the *software*, not the ZIM's content.

## 3. StackExchange data: CC BY-SA 4.0 (covered Stack Exchange content only)

The StackExchange data dump (the `stackoverflow.com.7z` XML dump on
archive.org, and the text it contains) is licensed **CC BY-SA 4.0**:

- **License URL:** <https://creativecommons.org/licenses/by-sa/4.0/>
- **Source:** Stack Exchange data dump — Stack Overflow dump
  `stackoverflow.com.7z` from the
  [Stack Exchange data dump](https://archive.org/details/stackexchange)
  collection on archive.org (July 2026 build used the dump identified by
  `SNAPSHOT_ID=2026-07-06`).

**Scope of the CC BY-SA license:** it covers the *Stack Exchange content*
(questions, answers, comments, user profile text) contained in the dump. It
does **not** automatically cover third-party assets that happen to be
referenced or embedded (see §6).

### Attribution requirements (covered content)

A rebuilt ZIM containing StackOverflow content must:

- attribute **Stack Overflow / Stack Exchange** and the individual
  **contributors** whose posts are included;
- carry attribution through **post/profile identifiers** where reasonably
  practicable: e.g. the post link (question URL with `q/<post-id>`) and the
  author display name / user profile link, which the build preserves in the
  rendered pages;
- provide the license name and link (CC BY-SA 4.0) so downstream users can
  find the terms;
- indicate **changes** — see §5. CC BY-SA 4.0 requires that
  modifications be indicated in a reasonable manner.

### Share-alike

CC BY-SA 4.0 is share-alike: if you redistribute a rebuilt ZIM (or a
derivative of it), you must distribute it under CC BY-SA 4.0 (or a license
compatible as defined by CC BY-SA 4.0 section 3(b)) for the covered
Stack Exchange content, keep the attribution, and indicate changes. The
share-alike obligation attaches to the *covered content*, not to the
tooling that produced it.

## 4. The ZIM is a transformation, not a verbatim copy

The rebuilt ZIM is **derived**, not a byte-for-byte copy of the dump. The
following modifications occur in the pipeline and must be disclosed to
downstream users:

- **XML → HTML conversion:** every post is rendered from dump XML into HTML
  pages by sotoki (patched); markup, layout, and interlinking differ from
  the raw dump.
- **Image recovery:** assets that failed to stage are replaced via the
  recovery pipeline — from the archive.org `stack-exchange-images` corpus,
  the live CDN (`i.sstatic.net`), dump XML scans, or edge resolvers.
- **WebP conversion:** recovered images are converted to WebP for
  compression; byte content differs from the original asset.
- **Placeholders:** assets that cannot be recovered are replaced by a
  deterministic placeholder image (see `data/placeholder-spec.json`); those
  pages no longer contain the original image.
- **Omissions:** unrecoverable assets, dead links, and content excluded by
  the build configuration are absent from the ZIM.
- **Metadata passes:** tags, user cards, and per-snapshot bookkeeping are
  re-derived during the build; the ZIM's structure is generated output.

Every redistributed ZIM should carry a notice stating that it is a
transformed/derived build of the Stack Exchange data dump, listing the
modifications above, and pointing at the source dump and license.

## 5. Where the attribution/license info must live

Archive.org item metadata alone is **not sufficient** for redistribution
compliance. Attribution and license information must be present:

- **inside** each ZIM (e.g. a top-level `license`, `attribution`, and
  `source` entry / page — the ZIM standard supports such metadata entries),
  and/or
- **alongside** each ZIM wherever it is distributed (sidecar
  `ATTRIBUTION.txt`/`LICENSE.txt`, item description, README), so a
  downloader who never visits archive.org still sees the terms.

The reference artifacts on archive.org (`stackoverflow-final-zim`,
`stackoverflow-nopic-zim`) carry their own attribution notices — preserve
and reproduce them when mirroring or redistributing those ZIMs.

## 6. Third-party image assets: unknown rights, not blanket-licensed

Image assets embedded or referenced in the ZIM come from several sources
and their copyright status is **not uniform**:

- **Stack Exchange-hosted images** (e.g. `i.stack.imgur.com`): to the
  extent they are part of the Stack Exchange network's posted content, they
  may fall under the Stack Exchange content license — but this is not
  guaranteed for every file, and the safest position is that each asset
  must be evaluated individually.
- **External-hosted images** (uploaded to arbitrary third-party hosts and
  merely linked from posts): rights belong to the original uploader /
  rightsholder under whatever terms they chose; this repository and the
  rebuild **do not assert** redistribution rights for them. This document
  deliberately makes **no claim** about who owns these assets — ownership
  and licensing must be determined per asset from its provenance.
- **Placeholders:** generated by this tooling; see
  `data/placeholder-spec.json` (CC0 factual spec, generated image itself is
  tool output).

Consequence: a redistributed ZIM must **not** be labeled as "CC BY-SA 4.0"
wholesale. The correct statement is: *covered Stack Exchange content is CC
BY-SA 4.0; other embedded assets are covered by their own terms and were
included based on availability, not on a verified license grant.*

## 7. archive.org availability is NOT a copyright license

The fact that files are downloadable from archive.org — the data dump, the
`stack-exchange-images` corpus, or the reference ZIMs — is an **access
service**, not a copyright license. Availability does not grant permission
to copy, transform, redistribute, or republish. The actual terms come from
the content license (CC BY-SA 4.0 for covered Stack Exchange content) or
from the individual asset rightsholders. When redistributing, rely on the
license evidence in the provenance records, not on "it was on archive.org".

## 8. The `stack-exchange-images` corpus: input mirror, per-asset provenance

The archive.org image-recovery corpus (`stack-exchange-images`, 62 ZIPs /
~859 GB) is an **optional external input** to the recovery pipeline. Using
it is subject to archive.org's terms and to the rights of the assets it
contains. It is an access service, **not** a blanket license grant for the
images.

For every asset the pipeline uses, per-asset provenance must be retained:

- original URL and post association (which page/answer referenced it);
- the asset hash (the build keys images by
  `md5("https://i.sstatic.net/FILENAME")`);
- the recovery source (corpus ZIP, CDN, dump XML scan, edge resolver,
  placeholder);
- license status (known / unknown) and any transformation notice (e.g.
  WebP conversion).

The recovery pipeline records this per-asset provenance (versioned TSV/JSONL
manifests under `recovery/` output, plus Redis bookkeeping; see
`docs/provenance.md` and `docs/recovery-runbook.md`). Keep those manifests
with any redistributed build so downstream users can evaluate rights per
asset.

## 9. Rights / takedown contact and policy

If you are a rightsholder and believe a rebuilt ZIM (or this repository)
includes content that should not be redistributed:

- **Where requests go:** takedowns for the published ZIMs are routed
  through the hosting site — archive.org's standard takedown process (the
  item's "report this item" / DMCA process, or
  <https://archive.org/help/terms-condition.php>). Requests regarding the
  rebuild-kit repository (once published) go through the hosting
  platform's own process. The repository maintainer does not act as a
  takedown intermediary, and no maintainer contact is listed.
- **What to include:** the item/ZIM name and the specific content
  (post URL, asset hash, or page path) so the request can be mapped to
  provenance records.
- **Policy:** every published build keeps its provenance manifests
  (`recovery/*.jsonl`/TSV, per-snapshot bookkeeping). Takedowns are
  resolved by the hosting site against the published artifacts; for
  subsequently built ZIMs, a request is addressed by removing the specific
  asset from the provenance and from any subsequently built ZIM.
  Already-published ZIMs are handled on a case-by-case basis by the hosting
  site. This repository does **not** assert redistribution rights for any
  asset whose provenance is unknown; such assets are included on an
  availability basis only and can be removed on request.

## 10. Summary checklist for redistribution

1. State the ZIM is a **transformed build** (XML→HTML, image recovery, WebP
   conversion, placeholders, omissions — §4).
2. Attribute Stack Overflow / Stack Exchange and contributors via
   post/profile identifiers (§3).
3. Provide the CC BY-SA 4.0 license URL for covered content, inside or
   alongside the ZIM (§5).
4. State that third-party assets are **not** blanket-licensed (§6).
5. Do **not** cite archive.org availability as a license (§7).
6. Keep and publish per-asset provenance manifests (§8).
7. Route rights/takedown requests through the hosting site (§9).

## Copyrighted bulk assets are NOT in git

No ZIMs, dumps, RDBs, images, or other bulk assets are ever committed (see
`.gitignore`). Only tiny reference counts/specs (`data/*.json`), the patch
series, and provenance/manifest tooling are tracked. If you redistribute a
rebuilt ZIM, you are responsible for the license obligations of its content —
this repository provides the tooling and provenance, not the redistribution
rights.