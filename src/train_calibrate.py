"""Calibration loop — tune the controller by playing games and adjusting params.

Objective (per project spec): **minimize the accuracy gap** between the bot and
its opponent. Because pure gap-minimisation collapses to the degenerate "match
accuracy while losing" behaviour (accuracy converges to 100% for both sides in a
decided game), we minimise the gap *subject to a draw-rate floor* so the Q1 goal
— games tend to draw — is preserved. Both numbers are reported every step.

    python src/train_calibrate.py --rounds 2 --elos 1500,2000,2500 --movetime 0.04
    python src/train_calibrate.py --with-hf 200      # also validate on real games

Writes the best parameters to data/best_params.json.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, replace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from controller import ControllerParams
from engine import Engine
from simulate import play_game

# Search grid (coordinate descent visits each in turn).
GRID = {
    "w_acc":         [0.15, 0.30, 0.50, 0.80],
    "w_draw":        [0.70, 1.00, 1.40],
    "loss_aversion": [2.0, 3.0, 4.5],
    "damping":       [0.3, 0.5, 0.7],
    "min_move_acc":  [25.0, 35.0, 45.0],
}


def evaluate(params: ControllerParams, brain: Engine, opp: Engine,
             elos: list[int], colors: list[bool], movetime: float) -> dict:
    """Play the matrix of games once; return gap / draw-rate / score."""
    rows = [play_game(brain, opp, elo, bw, movetime, params, verbose=False)
            for elo in elos for bw in colors]
    n = len(rows)
    return {
        "gap": sum(r["gap"] for r in rows) / n,
        "draw_rate": sum(1 for r in rows if r["result"] == "1/2-1/2") / n,
        "score": sum(r["bot_score"] for r in rows) / n,   # bot POV, 0.5 == balanced
    }


def calibrate(rounds: int, elos: list[int], movetime: float,
              draw_floor: float) -> ControllerParams:
    colors = [True, False]
    best = ControllerParams(search_movetime=movetime,
                            judge_movetime=max(0.03, movetime * 0.6))

    with Engine() as brain, Engine() as opp:
        m = evaluate(best, brain, opp, elos, colors, movetime)
        best_gap = m["gap"] if m["draw_rate"] >= draw_floor else float("inf")
        print(f"[init]  gap {m['gap']:.2f} | draws {m['draw_rate']*100:.0f}% "
              f"| score {m['score']:.2f}  {_fmt(best)}")

        for rnd in range(1, rounds + 1):
            for name, values in GRID.items():
                for v in values:
                    if getattr(best, name) == v:
                        continue
                    cand = replace(best, **{name: v})
                    m = evaluate(cand, brain, opp, elos, colors, movetime)
                    ok = m["draw_rate"] >= draw_floor
                    flag = ""
                    if ok and m["gap"] < best_gap - 1e-9:
                        best, best_gap, flag = cand, m["gap"], "  <-- keep"
                    print(f"[r{rnd}] {name}={v:<5} gap {m['gap']:.2f} | "
                          f"draws {m['draw_rate']*100:3.0f}% | score {m['score']:.2f}{flag}")

    print(f"\n[best] gap {best_gap:.2f}  {_fmt(best)}")
    out = os.path.join(os.path.dirname(__file__), "..", "data", "best_params.json")
    with open(out, "w") as f:
        json.dump(asdict(best), f, indent=2)
    print(f"[best] written to {os.path.relpath(out)}")
    return best


def _fmt(p: ControllerParams) -> str:
    return (f"(w_acc={p.w_acc}, w_draw={p.w_draw}, loss_av={p.loss_aversion}, "
            f"damp={p.damping}, min_acc={p.min_move_acc})")


# --------------------------------------------------------------------------
# Optional: validate the accuracy *formula* on real Lichess games (HF dataset).
# --------------------------------------------------------------------------
def validate_on_hf(n_games: int) -> None:
    """Recompute our per-game accuracy from real games' embedded engine evals
    and check that the more-accurate side tends to win — a sanity check that the
    win% -> accuracy pipeline behaves like the real thing."""
    try:
        from datasets import load_dataset
    except Exception as e:
        print(f"[hf] `datasets` not available ({e}). "
              f"Try a Python 3.11/3.12 venv:  pip install datasets python-chess")
        return

    from accuracy import move_accuracy, win_percent

    print(f"[hf] streaming {n_games} games from Lichess/standard-chess-games ...")
    ds = load_dataset("Lichess/standard-chess-games", split="train", streaming=True)

    seen = 0
    agree = 0            # accuracy-advantage sign matches result sign
    decisive = 0
    for row in ds:
        pgn = row.get("movetext") or row.get("pgn") or ""
        if "%eval" not in pgn:                     # need embedded evals
            continue
        result = row.get("Result") or _result_from_pgn(pgn)
        if result not in ("1-0", "0-1"):
            continue                               # only decisive games here
        accs = _simple_eval_accuracies(pgn, move_accuracy, win_percent)
        if not accs:
            continue
        w_acc, b_acc = accs
        decisive += 1
        if (w_acc > b_acc) == (result == "1-0"):   # more-accurate side won?
            agree += 1
        seen += 1
        if seen >= n_games:
            break

    if decisive:
        print(f"[hf] {decisive} decisive games | "
              f"more-accurate side won {100*agree/decisive:.1f}% of the time "
              f"(a healthy formula lands well above 50%).")
    else:
        print("[hf] no eval-annotated decisive games found in the stream sample.")


def _result_from_pgn(pgn: str) -> str:
    for tok in ("1-0", "0-1", "1/2-1/2"):
        if pgn.strip().endswith(tok):
            return tok
    return "*"


import re
_EVAL_RE = re.compile(r"\[%eval ([^\]]+)\]")


def _eval_to_cp(s: str) -> int:
    s = s.strip()
    if s.startswith("#"):                      # mate score
        n = int(s[1:])
        return 10000 if n > 0 else -10000
    return int(round(float(s) * 100))


def _simple_eval_accuracies(pgn, move_accuracy, win_percent):
    """Walk the eval sequence once and average per-move accuracy per side."""
    evals = [_eval_to_cp(x) for x in _EVAL_RE.findall(pgn)]
    if len(evals) < 6:
        return None
    # eval[i] is White-POV after ply i+1 (the move that side just made).
    wins_white_pov = [win_percent(cp) for cp in evals]
    w_accs, b_accs = [], []
    for i in range(1, len(wins_white_pov)):
        before = wins_white_pov[i - 1]
        after = wins_white_pov[i]
        mover_white = (i % 2 == 1)             # ply 1 = White's 1st move
        if mover_white:
            w_accs.append(move_accuracy(before, after))          # White wants win% up
        else:
            b_accs.append(move_accuracy(100 - before, 100 - after))
    if not w_accs or not b_accs:
        return None
    return sum(w_accs) / len(w_accs), sum(b_accs) / len(b_accs)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--elos", default="1500,2000,2500")
    ap.add_argument("--movetime", type=float, default=0.04)
    ap.add_argument("--draw-floor", type=float, default=0.80,
                    help="reject any param set whose draw rate falls below this")
    ap.add_argument("--with-hf", type=int, metavar="N", default=0,
                    help="also validate the accuracy formula on N real games")
    args = ap.parse_args()

    if args.with_hf:
        validate_on_hf(args.with_hf)

    elos = [int(e) for e in args.elos.split(",")]
    calibrate(args.rounds, elos, args.movetime, args.draw_floor)


if __name__ == "__main__":
    main()
