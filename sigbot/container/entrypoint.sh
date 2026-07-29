#!/bin/bash
# Container supervisor: runs the upstream signal-cli-rest-api entrypoint and
# sigbot_server side by side; if either dies, the container exits so the
# runtime's restart policy can recover the pair together.
#
# Layout expectations (see //sigbot:image):
#   /entrypoint.sh            upstream bbernhard/signal-cli-rest-api launcher
#   /sigbot/sigbot_server     the bot (config+state under /data, mount it)
#   /home/.local/share/signal-cli   signal account data (mount to persist!)
set -uo pipefail

: "${MODE:=json-rpc}"   # sigbot needs the receive websocket -> json-rpc
export MODE

/entrypoint.sh &
signal_pid=$!

/sigbot/sigbot_server --workdir /data &
bot_pid=$!

wait -n "$signal_pid" "$bot_pid"
status=$?
kill "$signal_pid" "$bot_pid" 2>/dev/null
wait "$signal_pid" "$bot_pid" 2>/dev/null
exit "$status"
