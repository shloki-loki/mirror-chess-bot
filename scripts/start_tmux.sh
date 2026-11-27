#!/usr/bin/env bash
# Start the bot inside a detached tmux session so it survives closing the
# terminal. `caffeinate -i` keeps the Mac awake while the bot runs.
#
# The token is read by run_bot.sh from the private file that
# scripts/set_token.sh writes, so it never appears on a command line.
#
# Usage:
#   ./scripts/set_token.sh               # once, or whenever the token changes
#   ./scripts/start_tmux.sh              # session name: mirrorbot
#   ./scripts/start_tmux.sh mysession    # custom name
set -euo pipefail

SESSION="${1:-mirrorbot}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOKEN_FILE="${MIRRORBOT_TOKEN_FILE:-$HOME/.config/mirrorbot/token}"

if [[ ! -r "$TOKEN_FILE" ]]; then
  echo "ERROR: no saved token. Run ./scripts/set_token.sh first." >&2
  exit 1
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "Session '$SESSION' is already running."
  echo "  attach: tmux attach -t $SESSION"
  exit 0
fi

# `env -u` keeps any LICHESS_TOKEN in this shell out of the tmux session, so
# run_bot.sh always takes the saved file.
env -u LICHESS_TOKEN tmux new-session -d -s "$SESSION" \
  "caffeinate -i '$ROOT/scripts/run_bot.sh' 2>&1 | tee -a '$ROOT/bot.log'; exec ${SHELL:-/bin/bash}"

echo "Started tmux session '$SESSION'."
echo "  watch:  tmux attach -t $SESSION      (detach with: Ctrl-b then d)"
echo "  logs:   tail -f $ROOT/bot.log"
echo "  stop:   tmux kill-session -t $SESSION"
