"""Play live games on Lichess as a BOT account.

Streams the account event feed, auto-accepts sensible challenges, and plays each
game with a per-game MirrorController. Requires a Lichess **bot** account and a
personal API token with the `bot:play` scope in the LICHESS_TOKEN env var.

    export LICHESS_TOKEN=xxxxxxxx
    python src/lichess_bot.py                 # play
    python src/lichess_bot.py --upgrade       # one-time: turn the account into a BOT

See README for how to create the account and token.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import chess
import requests

from controller import ControllerParams, MirrorController
from engine import Engine

BASE = "https://lichess.org"


def _log(msg: str) -> None:
    """Print immediately — worker threads must not sit in a buffer."""
    print(msg, flush=True)

# Which challenges we accept.
ACCEPT_VARIANTS = {"standard"}
MIN_INITIAL_SEC = 60          # decline ultra-bullet: no time to analyse
ACCEPT_RATED = True           # accept rated and casual

# Draw offers we make (we always accept theirs).
MAX_DRAW_OFFERS = 2           # per game
DRAW_OFFER_FROM_PLY = 40      # not before move 20
DRAW_OFFER_GAP_PLIES = 30     # 15 moves between offers


class LichessBot:
    def __init__(self, token: str, params: ControllerParams | None = None):
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}"})
        self.params = params or ControllerParams()
        self.username = self._whoami()
        _log(f"[lichess] authenticated as {self.username}")

    # ---- REST helpers ----------------------------------------------------
    def _whoami(self) -> str:
        r = self.s.get(f"{BASE}/api/account")
        r.raise_for_status()
        return r.json()["username"].lower()

    def upgrade_to_bot(self) -> None:
        r = self.s.post(f"{BASE}/api/bot/account/upgrade")
        if r.status_code == 200:
            _log("[lichess] account upgraded to BOT.")
        else:
            _log(f"[lichess] upgrade failed ({r.status_code}): {r.text}")

    def _post(self, path: str) -> None:
        try:
            r = self.s.post(f"{BASE}{path}", timeout=10)
        except requests.RequestException as e:
            _log(f"[lichess] POST {path} failed: {e}")
        else:
            if r.status_code != 200:
                _log(f"[lichess] POST {path} -> {r.status_code}: {r.text[:300]}")

    # ---- event loop ------------------------------------------------------
    def run(self) -> None:
        _log("[lichess] streaming events — waiting for challenges...")
        # Reconnect loop: a laptop sleep/wake or network blip silently kills the
        # stream. The read timeout turns a dead socket into an exception (Lichess
        # sends frequent keep-alives, so 65s of silence means it's really dead),
        # and the loop reconnects instead of going permanently deaf.
        while True:
            try:
                with self.s.get(f"{BASE}/api/stream/event",
                                stream=True, timeout=(10, 65)) as resp:
                    for line in resp.iter_lines():
                        if not line:
                            continue                       # keep-alive ping
                        event = json.loads(line)
                        etype = event.get("type")
                        if etype == "challenge":
                            self._on_challenge(event["challenge"])
                        elif etype == "gameStart":
                            g = event["game"]
                            gid = g.get("gameId") or g.get("id")
                            threading.Thread(target=self._safe_play,
                                             args=(gid,), daemon=True).start()
            except requests.RequestException as e:
                _log(f"[lichess] event stream error ({e}); reconnecting…")
            else:
                _log("[lichess] event stream ended; reconnecting…")
            time.sleep(3)

    def _on_challenge(self, ch: dict) -> None:
        cid = ch["id"]
        variant = ch.get("variant", {}).get("key")
        limit = ch.get("timeControl", {}).get("limit", 0)
        rated = ch.get("rated", False)
        ok = (variant in ACCEPT_VARIANTS
              and limit >= MIN_INITIAL_SEC
              and (ACCEPT_RATED or not rated))
        if ok:
            _log(f"[lichess] accepting challenge {cid} ({variant}, {limit}s)")
            self._post(f"/api/challenge/{cid}/accept")
        else:
            _log(f"[lichess] declining challenge {cid} ({variant}, {limit}s)")
            self._post(f"/api/challenge/{cid}/decline")

    # ---- one game --------------------------------------------------------
    def _safe_play(self, game_id: str) -> None:
        try:
            self._play(game_id)
        except Exception:  # never let one game kill the process — but be loud
            _log(f"[game {game_id}] FATAL:\n{traceback.format_exc()}")

    def _play(self, game_id: str) -> None:
        # A DEDICATED session per game. Sharing one Session across the event
        # stream, the game stream, and move POSTs starves the connection pool —
        # the bot makes one move, then the game stream stops delivering updates.
        sess = requests.Session()
        sess.headers.update(self.s.headers)
        url = f"{BASE}/api/bot/game/stream/{game_id}"

        with Engine() as brain:
            ctrl = MirrorController(brain, replace(self.params))
            st = {"color": None, "fen": "startpos", "processed": 0}

            while True:  # reconnect if the stream drops mid-game
                try:
                    with sess.get(url, stream=True, timeout=(10, 65)) as resp:
                        for line in resp.iter_lines():
                            if not line:
                                continue                       # keep-alive ping
                            if self._handle(json.loads(line), ctrl, sess,
                                            game_id, st):
                                return                         # game finished
                except requests.RequestException as e:
                    _log(f"[game {game_id}] stream error ({e}); reconnecting…")
                _log(f"[game {game_id}] stream ended; reconnecting…")
                time.sleep(1)

    def _handle(self, ev: dict, ctrl: MirrorController, sess: requests.Session,
                game_id: str, st: dict) -> bool:
        """Process one stream event. Returns True when the game is over."""
        t = ev.get("type")
        if t == "gameFull":
            st["fen"] = ev.get("initialFen", "startpos")
            st["color"] = (chess.WHITE
                           if ev["white"].get("id", "").lower() == self.username
                           else chess.BLACK)
            opp = ev["black"] if st["color"] == chess.WHITE else ev["white"]
            st["opp"] = opp.get("name") or opp.get("id") or "?"
            clock = ev.get("clock") or {}
            tc = (f"{clock.get('initial', 0) // 60000}+{clock.get('increment', 0) // 1000}"
                  if clock else "?")
            _log(f"[game {game_id}] start — we are "
                 f"{'White' if st['color'] else 'Black'} vs {st['opp']}"
                 f" ({opp.get('rating', '?')}) | {tc} | lichess.org/{game_id}")
            return self._sync(ctrl, sess, game_id, ev["state"], st)
        if t == "gameState":
            return self._sync(ctrl, sess, game_id, ev, st)
        # chatLine / opponentGone / etc.: ignored
        return False

    def _sync(self, ctrl: MirrorController, sess: requests.Session,
              game_id: str, state: dict, st: dict) -> bool:
        """Reconcile with the latest move list; move if it's our turn.
        Returns True when the game has ended."""
        status = state.get("status")
        if status not in (None, "started", "created"):
            winner = state.get("winner")
            me = "white" if st["color"] == chess.WHITE else "black"
            outcome = ("ABORTED" if status in ("aborted", "noStart")
                       else "DRAW" if not winner or status == "draw"
                       else "bot WON" if winner == me else "bot LOST")
            _log(f"[game {game_id}] finished: {status} — {outcome} "
                 f"vs {st.get('opp', '?')}")
            return True

        my_color, processed = st["color"], st["processed"]
        moves = state.get("moves", "").split()
        board = chess.Board() if st["fen"] in ("startpos", "") \
            else chess.Board(st["fen"])
        for uci in moves[:processed]:                 # fast replay, no engine
            board.push_uci(uci)

        for uci in moves[processed:]:                 # score newly-seen moves
            move = chess.Move.from_uci(uci)
            if board.turn != my_color:                # opponent's move -> mirror
                ctrl.record_opponent_move(board, move)
            board.push(move)
        st["processed"] = len(moves)

        _log(f"[game {game_id}] {len(moves)} plies | "
             f"turn: {'us' if board.turn == my_color else 'them'}")

        # The opponent offered a draw: that is exactly what we want — take it.
        their_offer = state.get("bdraw" if my_color == chess.WHITE else "wdraw")
        if their_offer:
            _log(f"[game {game_id}] opponent offered a draw — accepting")
            sess.post(f"{BASE}/api/bot/game/{game_id}/draw/yes", timeout=10)

        # Already moved at this ply (e.g. a draw-offer event echoed the old
        # move list before our move landed): don't think or play twice.
        if st.get("moved_at") == st["processed"]:
            return False

        if board.turn == my_color and not board.is_game_over():
            ctrl.p.search_movetime = self._budget(state, my_color)
            ctrl.p.judge_movetime = max(0.05, ctrl.p.search_movetime * 0.6)
            dec = ctrl.choose(board)
            uci = dec.move.uci()
            _log(f"[game {game_id}] play {uci} | move_acc {dec.chosen_move_acc:.0f} "
                 f"| me {ctrl.me.mean:.1f} vs opp {ctrl.opp.mean:.1f}")
            # We want draws, but sparingly: at most MAX_DRAW_OFFERS per game,
            # none before move 20, 15+ moves apart, and only when level or
            # ahead (so it's an easy "yes" for them, never a nag).
            ply = st["processed"]
            offer = (ply >= DRAW_OFFER_FROM_PLY
                     and st.get("offers", 0) < MAX_DRAW_OFFERS
                     and ply - st.get("last_offer", -999) >= DRAW_OFFER_GAP_PLIES
                     and dec.eval_after_win >= 45.0)
            q = "?offeringDraw=true" if offer else ""
            r = sess.post(f"{BASE}/api/bot/game/{game_id}/move/{uci}{q}", timeout=10)
            if r.status_code != 200:
                _log(f"[game {game_id}] move rejected ({r.status_code}): {r.text}")
            else:
                st["moved_at"] = ply
            if r.status_code == 200 and offer:
                st["last_offer"] = ply
                st["offers"] = st.get("offers", 0) + 1
                _log(f"[game {game_id}] offered a draw")
        return False

    @staticmethod
    def _budget(state: dict, my_color: bool) -> float:
        """Seconds to think: a small slice of our remaining clock."""
        remaining_ms = state.get("wtime" if my_color else "btime", 60000)
        secs = max(0.05, min(0.6, (remaining_ms / 1000.0) / 40.0))
        return secs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--upgrade", action="store_true",
                    help="one-time: convert the account to a BOT account, then exit")
    args = ap.parse_args()

    token = os.environ.get("LICHESS_TOKEN")
    if not token:
        sys.exit("Set LICHESS_TOKEN (a token with the bot:play scope).")

    bot = LichessBot(token)
    if args.upgrade:
        bot.upgrade_to_bot()
        return
    bot.run()


if __name__ == "__main__":
    main()
