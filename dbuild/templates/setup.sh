#!/bin/sh
# Runs as root before {{ name }} starts; a failure stops the container.
set -e

mkdir -p /config
chown -R bsd:bsd /config
