#!/usr/bin/env bash
# Runs the bot in the foreground. Takes the token from LICHESS_TOKEN, or else
# from the private token file written by scripts/set_token.sh. The token is
# never passed on a command line (where `ps` would show it).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOKEN_FILE="${MIRRORBOT_TOKEN_FILE:-$HOME/.config/mirrorbot/token}"
cd "$ROOT"

if [[ -z "${LICHESS_TOKEN:-}" && -r "$TOKEN_FILE" ]]; then
  LICHESS_TOKEN="$(tr -d '[:space:]' < "$TOKEN_FILE")"
  export LICHESS_TOKEN
fi

if [[ -z "${LICHESS_TOKEN:-}" ]]; then
  echo "ERROR: no token. Run ./scripts/set_token.sh first." >&2
  exit 1
fi

exec ./.venv/bin/python -u src/lichess_bot.py
