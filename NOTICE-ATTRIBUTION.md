# NOTICE — Attribution & Compliance

This file is the concise compliance notice for this repository and for
rebuilds/ZIMs produced from it. It supplements the full documents:

- `LICENSE` (CC0-1.0) + `LICENSE.scope.md` — repository license scope
- `LICENSES/GPL-3.0-only.txt` — the GPLv3-only license text
- `docs/data-and-license.md` — detailed licensing & redistribution reference
- `patches/sotoki/README.md` — sotoki patch series details
- `docs/provenance.md` — where every number and artifact comes from

## 1. sotoki attribution

- Upstream: <https://github.com/openzim/sotoki>
- Base commit: `157ca9a1c73e3e6349f1c80bb03e058355aef743` (v3.0.2)
- sotoki is **GPL-3.0**; this repository's patch series is a derivative
  work and is distributed under **GPL-3.0-only** (full text:
  `LICENSES/GPL-3.0-only.txt`). The patches are **not** CC0.
- Nine local modifications, all authored 2026-08-01:
  - `0001` — staging/assemble/resume/content-threads CLI options
  - `0002` — filesystem staging of rendered questions with manifests
  - `0003` — assemble-only ZIM builder, title sanitization, staged HTML fixes
  - `0004` — offline asset loading, Redis-less tag/user fallbacks
  - `0005` — bounded sort buffer, throttled Redis pipeline flushes
  - `0006` — snapshot-aware incremental CLI options
  - `0007` — per-question fingerprint + render-contract staging
  - `0008` — metadata passes + stale-page pruning in incremental mode
  - `0009` — local dump archives via `--archive-dir`

## 2. Stack Exchange source & license

- Source: Stack Exchange data dump on archive.org (Stack Overflow dump,
  `stackoverflow.com.7z`; July 2026 build = `SNAPSHOT_ID=2026-07-06`).
- License: **CC BY-SA 4.0** —
  <https://creativecommons.org/licenses/by-sa/4.0/>
- The license covers the **Stack Exchange content** (questions, answers,
  comments, profile text). It does **not** blanket-license third-party
  assets (see §5).

## 3. Attribution method

Attribute **Stack Overflow / Stack Exchange** and the **contributors** whose
posts appear, using post/profile identifiers where reasonably practicable:

- post links (`/questions/<post-id>`) and author display names are
  preserved in the rendered pages;
- user profile links are retained in user cards;
- include the CC BY-SA 4.0 name and URL in or alongside the ZIM.

## 4. Modifications (the ZIM is a transformation)

The ZIM is **derived**, not a verbatim copy. Disclose that:

- **XML → HTML conversion** of all posts by sotoki (patched);
- **image recovery** from the archive.org `stack-exchange-images` corpus,
  the live CDN (`i.sstatic.net`), dump XML scans, and edge resolvers;
- **WebP conversion** of recovered images;
- **placeholders** replacing unrecoverable assets (see
  `data/placeholder-spec.json`);
- **omissions** of unrecoverable assets and excluded content.

## 5. Mixed license / external images

- Tooling: **CC0-1.0**. sotoki patches: **GPL-3.0-only**. Covered Stack
  Exchange content: **CC BY-SA 4.0**.
- **Third-party image assets are NOT blanket-licensed.** External-hosted
  images are included on an availability basis; their rights belong to
  their actual rightsholders under their own terms, and this project does
  not assert redistribution rights for them. Evaluate per-asset provenance
  before redistribution.

## 6. archive.org references (not a license grant)

Reference items on archive.org:

- `stackoverflow-final-zim` — full build with images (142 GB)
- `stackoverflow-nopic-zim` — build without images (69 GB)
- `stack-exchange-images` — image-recovery corpus (62 ZIPs / ~859 GB)

**archive.org availability is an access service, not a copyright license.**
Nothing in this notice grants permission to copy, transform, or
redistribute beyond the actual license terms of the underlying content.

## 7. Rights / takedown contact

- Takedown requests for the published ZIMs are routed through the hosting
  site — archive.org's standard takedown process (the item's "report this
  item" / DMCA process, or
  <https://archive.org/help/terms-condition.php>). The repository
  maintainer does not act as a takedown intermediary.
- Requests regarding the rebuild-kit repository (once published) go through
  the hosting platform's own process. No maintainer contact is listed here.
- When filing, include the item/ZIM and the specific post URL, asset hash,
  or page path so the request can be mapped to provenance records (see
  `docs/data-and-license.md` §9).
- Provenance manifests (per-asset source, hash, post association) are kept
  with every published build to support rights evaluation and takedowns.