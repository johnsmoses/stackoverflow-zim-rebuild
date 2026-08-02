# License scope

`LICENSE` in this repository is the **CC0 1.0 Universal** text. It applies
**only** to the rebuild-kit tooling described below — **not** to the whole
repository. This file is the authoritative per-directory scope statement.

## Mixed-license aggregate

The repository is a mixed-license aggregate:

| Path | License | Notes |
|------|---------|-------|
| `bin/`, `scripts/`, `recovery/`, `docker/`, `configs/`, `tests/` | **CC0-1.0** | Rebuild-kit tooling: scripts, tests, containers, configs. Dedicated to the public domain per the root `LICENSE`. |
| `docs/` | **CC0-1.0** | Documentation authored for this repository. |
| `data/` | **CC0-1.0** (factual records) | `baseline-2026-07.json` and `placeholder-spec.json` are factual records describing the July 2026 build (counts, sizes, hashes). Factual data is not copyrightable as such, but the files are also placed under CC0. |
| `patches/sotoki/` | **GPL-3.0-only** | Patch series derived from [openzim/sotoki](https://github.com/openzim/sotoki) (GPL-3.0), base commit `157ca9a`. Full text: `LICENSES/GPL-3.0-only.txt`. See `patches/sotoki/README.md` and `NOTICE-ATTRIBUTION.md`. |
| `capture/` | **not distributed** | Operator-local forensic evidence (verbatim installed sotoki, diffs, `MANIFEST.txt`). Excluded from this repository's publication; it is not part of the distributed tree. |
| `.github/`, `Makefile`, `pyproject.toml`, `requirements/`, `CHANGELOG.md`, `CONTRIBUTING.md`, `README.md`, this file, `NOTICE-ATTRIBUTION.md` | **CC0-1.0** | Project metadata, build glue, CI workflows, docs. |

The `LICENSE` (CC0) text itself is not modified; this file is the companion
scope statement.

## What CC0 covers

The rebuild-kit tooling — everything except `patches/sotoki/` — is
dedicated to the public domain. `capture/` is **excluded from
distribution**: it is operator-local evidence kept out of the published
repository, not distributed under any license here. CC0 is **not** a
license for the *data* the tooling processes: Stack Exchange content
stays under its own terms (CC BY-SA 4.0 for covered Stack Exchange content), and third-party
image assets are covered by whatever terms their actual rightsholders set
(see `docs/data-and-license.md`).

## What GPL-3.0-only covers

`patches/sotoki/` (the 9-patch series, `series` file, and `README.md`) and
`capture/diffs/` + `capture/installed/` are derivative of GPL-3.0 sotoki and
are distributed under **GPL-3.0-only** — the raw capture is **not
distributed** (local evidence, excluded from publication); only the
`patches/sotoki/` series ships with the repository. They are not CC0.
Reuse of these files is governed by the GPL, version 3 only (no later
versions are granted by this repository).

## Practical rule

- **Tooling** → CC0: take, modify, relicense freely.
- **Patches** → GPL-3.0-only: reuse under GPLv3-only terms, keep the
  license notice, and share modifications under the same terms. (The raw
  `capture/` evidence is not distributed at all.)
- **Data** → treat per `docs/data-and-license.md`; provenance matters.