#!/usr/bin/env bash
# Live feed of MirrorBot games: who challenged, who's playing, how it ended.
# Prints a timestamped line per event and pops a macOS notification when a
# game starts or finishes. Leave it running in a terminal tab; Ctrl-C to stop.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$ROOT/bot.log"

notify() {  # title, message
  osascript -e "display notification \"${2//\"/}\" with title \"${1//\"/}\" sound name \"Glass\"" \
    >/dev/null 2>&1 || true
}

echo "Watching MirrorBot games (Ctrl-C to stop)…"
if pgrep -f "src/lichess_bot.py" >/dev/null; then
  echo "Bot is running."
else
  echo "WARNING: bot is NOT running — start it with scripts/start_tmux.sh"
fi
echo

tail -n 0 -F "$LOG" 2>/dev/null \
  | grep --line-buffered -E "accepting|declining|start —|finished:|offered a draw|opponent offered|FATAL|401|authenticated" \
  | while IFS= read -r line; do
      printf '%s  %s\n' "$(date '+%H:%M')" "$line"
      case "$line" in
        *"start —"*)
          opp="${line#* vs }"; notify "MirrorBot: game started" "vs ${opp%% |*}" ;;
        *"finished:"*)
          notify "MirrorBot: game over" "${line#*finished: }" ;;
        *FATAL*|*401*)
          notify "MirrorBot: problem" "Check the bot log" ;;
      esac
    done
