#!/bin/sh
#
# dbuild CI wrapper (backward-compatibility shim)
#
# This script now delegates to `dbuild ci-run`.  It is kept for
# repos that still reference dbuild-ci.sh directly.
#
# Usage: sh dbuild-ci.sh [VARIANT]
#
# Env vars:
#   DBUILD_REF   - override the dbuild version to fetch (default: main)
#   DBUILD_PATH  - explicit path to dbuild checkout (skips fetch)
#   GITHUB_TOKEN - registry authentication
#   GITHUB_ACTOR - registry username (optional)
#

set -e

DBUILD_REF="${DBUILD_REF:-main}"

# ── Locate dbuild ────────────────────────────────────────────────────

if [ -n "$DBUILD_PATH" ] && [ -d "$DBUILD_PATH/dbuild" ]; then
    DBUILD_DIR="$DBUILD_PATH"
elif [ -d "../dbuild/dbuild" ]; then
    DBUILD_DIR="$(cd ../dbuild && pwd)"
else
    echo "Fetching dbuild ${DBUILD_REF}..."
    mkdir -p /tmp/dbuild
    fetch -qo /tmp/dbuild.tar.gz \
        "https://github.com/daemonless/dbuild/archive/${DBUILD_REF}.tar.gz"
    tar -xzf /tmp/dbuild.tar.gz -C /tmp/dbuild --strip-components=1
    DBUILD_DIR="/tmp/dbuild"
fi

export PYTHONPATH="$DBUILD_DIR"

# ── Variant filter (optional) ────────────────────────────────────────

VARIANT_FLAG=""
if [ -n "$1" ]; then
    VARIANT_FLAG="--variant $1"
fi

# ── Delegate to dbuild ci-run ────────────────────────────────────────

exec python3 -m dbuild ci-run $VARIANT_FLAG
