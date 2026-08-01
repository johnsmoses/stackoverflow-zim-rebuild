#!/usr/bin/env bash
# check_patch_series.sh — dry-run-safe scaffolding for validating the
# patches/sotoki/ series against a pinned upstream base commit.
#
# Patch application is NOT yet implemented (Task 3). This script only
# validates arguments and tool availability, then exits 0 in dry-run mode.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: check_patch_series.sh --package-path DIR --base-commit COMMIT [--dry-run]

  --package-path DIR   Path to the installed sotoki package (or a checkout)
  --base-commit COMMIT Upstream base commit the patch series must apply onto
  --dry-run            Validate arguments/tools only; do not apply anything
EOF
}

PACKAGE_PATH=""
BASE_COMMIT=""
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --package-path) PACKAGE_PATH="${2:-}"; shift 2 ;;
    --base-commit)  BASE_COMMIT="${2:-}"; shift 2 ;;
    --dry-run)      DRY_RUN=1; shift ;;
    -h|--help)      usage; exit 0 ;;
    *) echo "error: unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

[[ -n "$PACKAGE_PATH" ]] || { echo "error: --package-path is required" >&2; usage; exit 2; }
[[ -n "$BASE_COMMIT" ]] || { echo "error: --base-commit is required" >&2; usage; exit 2; }

if ! command -v git >/dev/null 2>&1; then
  echo "error: git is required but was not found in PATH" >&2
  exit 1
fi

if [[ ! -d "$PACKAGE_PATH" ]]; then
  echo "error: package path is not a directory: $PACKAGE_PATH" >&2
  exit 1
fi

echo "package-path: $PACKAGE_PATH"
echo "base-commit:  $BASE_COMMIT"
echo "patch application is not yet implemented"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "dry-run: exiting 0 without applying anything"
  exit 0
fi

echo "error: non-dry-run mode is not yet implemented; use --dry-run" >&2
exit 1