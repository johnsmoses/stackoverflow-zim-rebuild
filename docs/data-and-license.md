# Data & licensing

Three different license regimes touch this project. They are distinct; do
not merge them.

## This repository: CC0-1.0

The rebuild-kit code, scripts, docs, and baseline JSON in this repository
are dedicated to the public domain under **CC0-1.0** (see `LICENSE`). This
covers the tooling only — not the data it processes.

## sotoki: GPL-3.0

[sotoki](https://github.com/openzim/sotoki) is **GPL-3.0**. It is used as an
external tool (pinned at `157ca9a` + the captured patch series) and is not
vendored into this repository. Its patches, once captured into
`patches/sotoki/`, are derivative works of GPL-3.0 sotoki and inherit
GPL-3.0 — that is fine, because the patches are also only distributed with
sotoki's own GPL terms and this repo ships them as a patch series, not as
a fork bundle.

## StackExchange data: CC BY-SA 4.0

The StackExchange data dump (including the `stackoverflow.com.7z` XML dump
and the text it contains) is licensed **CC BY-SA 4.0** (attribution +
share-alike). A rebuilt ZIM containing StackOverflow content must carry
appropriate attribution and is subject to share-alike obligations. The
published reference artifacts on archive.org carry their own attribution
notices — preserve them when mirroring.

## Recovered external images: varies

Image assets recovered from the live CDN (`i.sstatic.net`), the IA image
dump, or edge resolvers are hosted content whose rights belong to their
posters. StackExchange-hosted images fall under the StackExchange network
CC BY-SA terms where applicable; third-party-hosted images vary. The
recovery pipeline records provenance per asset (`data/provenance` notes +
Redis bookkeeping) but this repo does **not** assert redistribution rights
for them.

## IA corpus: optional external asset

The archive.org image-recovery corpus (`RECOVERY_ROOT`, 62 ZIPs / ~859 GB)
is an optional external input. Downloading and using it is subject to
archive.org's terms; it is treated as an input mirror, not a license grant.

## Copyrighted bulk assets are NOT in git

No ZIMs, dumps, RDBs, images, or other bulk copyrighted assets are ever
committed (see `.gitignore`). Only tiny reference counts/specs
(`data/*.json`) are tracked. If you redistribute a rebuilt ZIM, you are
responsible for the license obligations of its content — this repository
provides the tooling and provenance, not the redistribution rights.