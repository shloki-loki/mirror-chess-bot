"""Local self-play demo: MirrorBot vs. a fixed-Elo Stockfish 'human'.

Runs one or more full games and reports, for each, the final result and both
players' cumulative accuracy — so you can watch the bot mirror its opponent and
steer toward draws without needing a Lichess token.

    python src/simulate.py --elos 1500,2000,2600 --movetime 0.1 --color both
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import chess

from controller import ControllerParams, MirrorController
from engine import Engine

MAX_PLIES = 200         # adjudicate anything longer to keep demos bounded
ADJUDICATE_CP = 60      # |eval| below this at the cap == call it a draw


def play_game(brain: Engine, opp: Engine, opp_elo: int, bot_white: bool,
              movetime: float, params: ControllerParams, verbose: bool) -> dict:
    board = chess.Board()
    ctrl = MirrorController(brain, params)
    opp.set_skill(opp_elo)
    bot_color = chess.WHITE if bot_white else chess.BLACK

    if verbose:
        print(f"\n  ply  mover      move    move_acc   bot_acc  opp_acc   eval(bot%)")
        print(f"  {'-'*63}")

    ply = 0
    while not board.is_game_over(claim_draw=True) and ply < MAX_PLIES:
        ply += 1
        if board.turn == bot_color:
            dec = ctrl.choose(board)
            san = board.san(dec.move)
            board.push(dec.move)
            if verbose:
                print(f"  {ply:>3}  BOT      {san:>7}   {dec.chosen_move_acc:>7.1f}  "
                      f"{ctrl.me.mean:>7.1f}  {ctrl.opp.mean:>7.1f}   {dec.eval_after_win:>6.1f}")
        else:
            before = board.copy(stack=False)
            move = opp.play(board, movetime)
            acc = ctrl.record_opponent_move(before, move)
            san = board.san(move)
            board.push(move)
            if verbose:
                print(f"  {ply:>3}  opp      {san:>7}   {acc:>7.1f}  "
                      f"{ctrl.me.mean:>7.1f}  {ctrl.opp.mean:>7.1f}")

    # Result (adjudicate a hit time/ply cap by the current evaluation).
    if board.is_game_over(claim_draw=True):
        result = board.result(claim_draw=True)
        reason = _termination(board)
    else:
        ev = brain.analyse(board, multipv=1, movetime=movetime)
        cp = ev[0].cp if ev else 0
        if abs(cp) < ADJUDICATE_CP:
            result, reason = "1/2-1/2", "adjudicated draw (cap)"
        else:
            white_better = cp > 0 if board.turn == chess.WHITE else cp < 0
            result = "1-0" if white_better else "0-1"
            reason = "adjudicated (cap)"

    bot_score = _score_for(result, bot_color)
    return {
        "opp_elo": opp_elo,
        "bot_white": bot_white,
        "result": result,
        "reason": reason,
        "bot_score": bot_score,           # 1 win / 0.5 draw / 0 loss, bot POV
        "plies": ply,
        "bot_acc": ctrl.me.mean,
        "opp_acc": ctrl.opp.mean,
        "gap": abs(ctrl.me.mean - ctrl.opp.mean),
    }


def _termination(board: chess.Board) -> str:
    if board.is_checkmate():
        return "checkmate"
    if board.is_stalemate():
        return "stalemate"
    if board.is_insufficient_material():
        return "insufficient material"
    if board.can_claim_fifty_moves():
        return "fifty-move"
    if board.can_claim_threefold_repetition():
        return "threefold"
    return "draw"


def _score_for(result: str, color: bool) -> float:
    if result == "1/2-1/2":
        return 0.5
    white_won = result == "1-0"
    return 1.0 if white_won == (color == chess.WHITE) else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--elos", default="1600,2200,2800",
                    help="comma-separated opponent Elos to test")
    ap.add_argument("--movetime", type=float, default=0.1, help="seconds/move")
    ap.add_argument("--color", choices=["white", "black", "both"], default="both")
    ap.add_argument("--multipv", type=int, default=16)
    ap.add_argument("--w-acc", type=float, default=0.30)
    ap.add_argument("--w-draw", type=float, default=1.0)
    ap.add_argument("--loss-aversion", type=float, default=3.0)
    ap.add_argument("--damping", type=float, default=0.5)
    ap.add_argument("--quiet", action="store_true", help="summary only")
    args = ap.parse_args()

    params = ControllerParams(
        multipv=args.multipv, w_acc=args.w_acc, w_draw=args.w_draw,
        loss_aversion=args.loss_aversion, damping=args.damping,
        search_movetime=args.movetime,
        judge_movetime=max(0.05, args.movetime * 0.6),
    )
    elos = [int(e) for e in args.elos.split(",")]
    colors = {"white": [True], "black": [False], "both": [True, False]}[args.color]

    rows: list[dict] = []
    with Engine() as brain, Engine() as opp:
        for elo in elos:
            for bw in colors:
                r = play_game(brain, opp, elo, bw, args.movetime, params,
                              verbose=not args.quiet)
                rows.append(r)
                side = "White" if bw else "Black"
                print(f"\n  >> opp Elo {elo} | bot {side} | {r['result']} "
                      f"({r['reason']}) | bot_acc {r['bot_acc']:.1f} "
                      f"opp_acc {r['opp_acc']:.1f} | gap {r['gap']:.1f}")

    n = len(rows)
    draws = sum(1 for r in rows if r["result"] == "1/2-1/2")
    mean_gap = sum(r["gap"] for r in rows) / n
    mean_score = sum(r["bot_score"] for r in rows) / n
    print("\n" + "=" * 64)
    print(f"  GAMES {n} | draws {draws}/{n} ({100*draws/n:.0f}%) | "
          f"mean |accuracy gap| {mean_gap:.2f} | bot score {mean_score:.2f}")
    print("=" * 64)


if __name__ == "__main__":
    main()
