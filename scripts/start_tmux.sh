#!/usr/bin/env bash
# Start the bot inside a detached tmux session so it survives closing the
# terminal. `caffeinate -i` keeps the Mac awake while the bot runs.
#
# Usage:
#   export LICHESS_TOKEN=lip_xxxxxxxx
#   ./scripts/start_tmux.sh              # session name: mirrorbot
#   ./scripts/start_tmux.sh mysession    # custom name
set -euo pipefail

SESSION="${1:-mirrorbot}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -z "${LICHESS_TOKEN:-}" ]]; then
  echo "ERROR: export LICHESS_TOKEN first, then re-run this script." >&2
  exit 1
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "Session '$SESSION' is already running."
  echo "  attach: tmux attach -t $SESSION"
  exit 0
fi

# -e injects the token into the tmux session environment (tmux >= 3.2) so it is
# never placed on the bot's command line. `exec $SHELL` keeps the pane alive
# after the bot exits so you can read any final output.
tmux new-session -d -s "$SESSION" -e "LICHESS_TOKEN=$LICHESS_TOKEN" \
  "caffeinate -i '$ROOT/scripts/run_bot.sh' 2>&1 | tee -a '$ROOT/bot.log'; exec ${SHELL:-/bin/bash}"

echo "Started tmux session '$SESSION'."
echo "  watch:  tmux attach -t $SESSION      (detach with: Ctrl-b then d)"
echo "  logs:   tail -f $ROOT/bot.log"
echo "  stop:   tmux kill-session -t $SESSION"
