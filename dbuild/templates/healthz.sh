#!/bin/sh
# Daemonless standard healthcheck for {{ name }}

fetch -qo /dev/null "http://127.0.0.1:{{ port }}/" >/dev/null 2>&1
