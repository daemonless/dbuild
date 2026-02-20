#!/bin/sh
# Daemonless standard healthcheck for {{ name }}

if [ -n "$TUNNEL_TOKEN" ]; then
    # Standard Mode: Check /ready or HTTP endpoint
    if fetch -o /dev/null "http://127.0.0.1:{{ port }}/" >/dev/null 2>&1; then
        exit 0
    else
        exit 1
    fi
else
    # CIT Mock Mode (Tokenless): Check TCP port
    if nc -z 127.0.0.1 "{{ port }}" >/dev/null 2>&1; then
        exit 0
    else
        exit 1
    fi
fi
