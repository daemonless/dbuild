#!/bin/sh
# bootstrap.sh — Prepare a FreeBSD CI environment for dbuild.
#
# Installs python3, fetches dbuild, and runs ci-prepare (which installs
# podman, buildah, skopeo, trivy, ocijail, and configures networking).
#
# Default: fetched from GitHub by the CI workflow template.
# Override: place .daemonless/bootstrap.sh in your image repo.
#
# Environment:
#   DBUILD_REF  - dbuild version to fetch (default: v2)
#   DBUILD_DIR  - install location (default: /tmp/dbuild)

set -e

DBUILD_REF="${DBUILD_REF:-v2}"
DBUILD_DIR="${DBUILD_DIR:-/tmp/dbuild}"

# ── Install python3 + PyYAML (minimum to run dbuild) ────────────────
mkdir -p /etc/pkg
echo 'FreeBSD: { url: "http://pkg.FreeBSD.org/${ABI}/latest" }' > /etc/pkg/FreeBSD.conf
pkg update -f && pkg install -y python3 py311-yaml

# ── Fetch dbuild ────────────────────────────────────────────────────
fetch -qo /tmp/dbuild.tar.gz \
  "https://github.com/daemonless/dbuild/archive/${DBUILD_REF}.tar.gz"
mkdir -p "$DBUILD_DIR"
tar -xzf /tmp/dbuild.tar.gz -C "$DBUILD_DIR" --strip-components=1

# ── Prepare build environment ───────────────────────────────────────
PYTHONPATH="$DBUILD_DIR" python3 -m dbuild ci-prepare
