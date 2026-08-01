#!/usr/bin/env bash
# common.sh — shared helpers for stackoverflow-zim-rebuild.
#
# Source this from scripts:
#     source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/bin/common.sh"
#
# Can also be executed directly:
#     bash bin/common.sh config-check
#
# No hardcoded machine-specific paths: every path derives from WORK_ROOT.
set -euo pipefail

# Locate repo root (parent of bin/)
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Load .env if present
if [[ -f "${REPO_ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/.env"
  set +a
fi

# --- derive defaults from WORK_ROOT --------------------------------------
WORK_ROOT="${WORK_ROOT:-${REPO_ROOT}/work}"

STAGE_DIR="${STAGE_DIR:-${WORK_ROOT}/stage}"
OUTPUT_DIR="${OUTPUT_DIR:-${WORK_ROOT}/out}"
TMP_DIR="${TMP_DIR:-${WORK_ROOT}/tmp}"
CACHE_DIR="${CACHE_DIR:-${WORK_ROOT}/cache}"
REDIS_DIR="${REDIS_DIR:-${WORK_ROOT}/redis}"
REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379/0}"

SOTOKI_SOURCE_DIR="${SOTOKI_SOURCE_DIR:-${WORK_ROOT}/sotoki-src}"
SOTOKI_VENV="${SOTOKI_VENV:-${WORK_ROOT}/.venv-sotoki}"

MIRROR_DIR="${MIRROR_DIR:-${WORK_ROOT}/mirror}"
MIRROR_URL="${MIRROR_URL:-https://archive.org/download/stackexchange}"
DUMP_ARCHIVE="${DUMP_ARCHIVE:-stackoverflow.com.7z}"

SNAPSHOT_ID="${SNAPSHOT_ID:-2026-07-06}"

THREADS="${THREADS:-8}"
CONTENT_THREADS="${CONTENT_THREADS:-4}"

PLACEHOLDER_BYTES="${PLACEHOLDER_BYTES:-1852}"

IA_ROOT="${IA_ROOT:-https://archive.org/download/stackoverflow-final-zim}"
RECOVERY_ROOT="${RECOVERY_ROOT:-https://archive.org/download/stackoverflow-images-recovery}"

ASSET_CACHE_DIR="${ASSET_CACHE_DIR:-${WORK_ROOT}/assets}"

# --- helpers --------------------------------------------------------------
log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

# Reject empty/unset path variables
require_path() {
  local name="$1" value="$2"
  if [[ -z "${value}" ]]; then
    die "${name} is unset or empty"
  fi
}

# --- direct execution mode -------------------------------------------------
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  case "${1:-config-check}" in
    config-check)
      for v in WORK_ROOT STAGE_DIR OUTPUT_DIR TMP_DIR CACHE_DIR REDIS_DIR \
               SOTOKI_SOURCE_DIR SOTOKI_VENV MIRROR_DIR ASSET_CACHE_DIR; do
        require_path "$v" "${!v}"
      done
      log "config-check: OK (WORK_ROOT=${WORK_ROOT})"
      log "config-check: STAGE_DIR=${STAGE_DIR}"
      ;;
    *)
      echo "usage: bash common.sh [config-check]" >&2
      exit 2
      ;;
  esac
fi