#!/usr/bin/env bash
# Runs the bot in the foreground. Expects LICHESS_TOKEN in the environment.
# The token is NEVER written to disk by this script.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -z "${LICHESS_TOKEN:-}" ]]; then
  echo "ERROR: LICHESS_TOKEN is not set." >&2
  echo "  Run:  export LICHESS_TOKEN=lip_xxxxxxxx" >&2
  exit 1
fi

exec ./.venv/bin/python -u src/lichess_bot.py
