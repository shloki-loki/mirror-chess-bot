#!/usr/bin/env bash
# Save the bot's Lichess token (bot:play scope) to a private file, readable
# only by you, outside the repo. Input is hidden and never echoed.
set -euo pipefail

TOKEN_FILE="${MIRRORBOT_TOKEN_FILE:-$HOME/.config/mirrorbot/token}"
mkdir -p "$(dirname "$TOKEN_FILE")"
chmod 700 "$(dirname "$TOKEN_FILE")"

printf 'Paste the MirroringPeep bot:play token (hidden), then press Enter: '
IFS= read -rs token
echo
token="$(printf '%s' "$token" | tr -d '[:space:]')"
if [[ "$token" != lip_* ]]; then
  echo "That doesn't look like a Lichess token (should start with lip_). Nothing saved." >&2
  exit 1
fi

( umask 077; printf '%s\n' "$token" > "$TOKEN_FILE" )
echo "Saved to $TOKEN_FILE (only you can read it)."
