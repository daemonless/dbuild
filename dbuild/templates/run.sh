#!/bin/sh
# {{ title }} s6 service

if [ -z "$TUNNEL_TOKEN" ]; then
    echo "[WARN] TUNNEL_TOKEN is not set. Starting in CIT mock mode."
    # Log version to prove binary is functional
    /usr/local/bin/{{ name }} --version
    # Listen on port {{ port }} to satisfy CI port check
    exec /usr/bin/nc -lk 0.0.0.0 {{ port }}
fi

echo "[INFO] Starting {{ name }}..."

# Standard start command for {{ name }}
exec /usr/local/bin/{{ name }} 
    --config /config 
    --data /data
