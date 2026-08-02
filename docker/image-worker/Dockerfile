# Image recovery worker image (Task 7).
#
# Self-contained container for the two worker scripts:
#   - recover_convert_images.py        (live-CDN downloader + WebP converter)
#   - ia_sstatic_basename_recovery.py  (Archive.org ZIP shard extractor)
#
# Hardening implemented here (full list in docs/nas-worker.md):
#   H1  non-root runtime user (uid 10001, created below; `USER worker`)
#   H3  only /work/state and /work/recovered are ever written by the scripts
#   H4  read-only root filesystem + /tmp tmpfs are enforced in compose.yaml
#   H10 magic-byte payload validation is in the scripts (Content-Type advisory)
#   H11 provenance-preserving WebP conversion (content_sha256 + derived_sha256)
#
# The image opens no listening ports and requires no privileges; compose.yaml
# drops all capabilities and disables new privileges on top of this.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIPS_CONCURRENCY=2

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl file \
    build-essential python3-dev pkg-config \
    libvips-dev libwebp-dev \
    && rm -rf /var/lib/apt/lists/*

# pyvips is the preferred WebP converter; cwebp (libwebp-dev) is the fallback
# used when pyvips is unavailable or fails for a payload. Both ship in the
# image, so conversion never depends on the host. pyvips 3.1.1 is pinned:
# it matches the libvips 8.16 headers of the current python:3.12-slim base
# (Debian trixie); the toolchain above covers source builds when no wheel
# matches the interpreter.
RUN pip install --no-cache-dir aiohttp aiosqlite pyvips==3.1.1

# H1: non-root runtime user; /work is the only writable area (state/recovered
# subdirs), everything else is read-only via compose.yaml (H4).
RUN useradd -m -u 10001 worker \
    && mkdir -p /work/manifests /work/state /work/recovered /work/ia-shards \
    && chown -R worker:worker /work

WORKDIR /work
COPY recover_convert_images.py /work/recover_convert_images.py
COPY ia_sstatic_basename_recovery.py /work/ia_sstatic_basename_recovery.py

USER worker
# Entrypoint is python3 so `docker compose run --rm <service> <script> ...`
# (or via bin/run-worker) executes the requested worker with the given args.
ENTRYPOINT ["python3"]