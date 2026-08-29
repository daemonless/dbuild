#!/bin/sh
# {{ title }} s6 service

export HOME=/config

# Signal readiness once the healthcheck passes (FD 3, see notification-fd)
s6-ready-when /healthz

echo "[INFO] Starting {{ name }}..."

cd /config
exec /usr/local/bin/s6-setuidgid bsd \
    /usr/local/bin/{{ name }} \
    --config /config \
    --data /data
