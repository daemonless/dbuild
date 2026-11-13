#!/bin/sh
# Start {{ name }}; /init has already dropped to the run user.

export HOME=/config
{{ run_hints }}
cd /config
exec {{ run_binary }}{{ run_args }}
