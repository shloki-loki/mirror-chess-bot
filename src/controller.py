"""MirrorBot move selection.

Objective (per project spec): **draw-seek primary, match accuracy soft**.

Each turn we ask Stockfish for the top-N moves. Every candidate carries two
signals:

  * how *accurate* it is (its win% drop vs. the best move), and
  * where it leaves the *evaluation* (win% near 50 == equal == heading to a draw).

We pick the candidate that best trades off:
    - matching the accuracy this move *needs* to keep our running accuracy
      level with the opponent's, and
    - pulling the position back toward equal so games tend to draw.

Because the candidate menu is the engine's own top-N, the bot can never be
*more* accurate than the best move and can never blunder outright — it only
chooses *which* good-to-mediocre move lands it on target.
"""
from __future__ import annotations

from dataclasses import dataclass

import chess

from accuracy import MATE_CP, RunningAccuracy, move_accuracy
from engine import Engine


@dataclass
class ControllerParams:
    multipv: int = 16            # size of the candidate menu
    w_draw: float = 1.0         # weight: steer the eval toward equal (draw)
    w_acc: float = 0.30         # weight: match the opponent's accuracy
    loss_aversion: float = 3.0  # how much harder we avoid the losing side of 50%
    damping: float = 0.5        # blend exact catch-up with opponent's mean (0..1)
    min_move_acc: float = 35.0  # never deliberately play below this accuracy
    search_movetime: float = 0.15   # think time when choosing our move
    judge_movetime: float = 0.08    # think time when scoring a played move


@dataclass
class MoveDecision:
    move: chess.Move
    target_move_acc: float   # accuracy we aimed for this move
    chosen_move_acc: float   # accuracy of the move we actually played
    eval_after_win: float    # our win% after the move (≈50 == equal)


class MirrorController:
    """Holds both players' running accuracy and picks the bot's moves."""

    def __init__(self, engine: Engine, params: ControllerParams | None = None):
        self.engine = engine
        self.p = params or ControllerParams()
        self.me = RunningAccuracy()    # the bot's per-move accuracies
        self.opp = RunningAccuracy()   # the opponent's per-move accuracies

    # ---- scoring the opponent (impartial judge) --------------------------
    def score_played_move(self, board_before: chess.Board, move: chess.Move) -> float:
        """Accuracy of `move`, judged by the full-strength engine."""
        best = self.engine.analyse(board_before, multipv=1,
                                   movetime=self.p.judge_movetime)
        best_win = best[0].win if best else 50.0
        after = board_before.copy(stack=False)
        after.push(move)
        # After the move it's the other side to move, so their win% negates.
        reply = self.engine.analyse(after, multipv=1, movetime=self.p.judge_movetime)
        after_win = (100.0 - reply[0].win) if reply else best_win
        return move_accuracy(best_win, after_win)

    def record_opponent_move(self, board_before: chess.Board, move: chess.Move) -> float:
        acc = self.score_played_move(board_before, move)
        self.opp.add(acc)
        return acc

    # ---- choosing the bot's move ----------------------------------------
    def _target_move_accuracy(self) -> float:
        """The accuracy this move should have to track the opponent."""
        target_mean = self.opp.mean                       # what we want to equal
        exact = self.me.required_next(target_mean)         # exact catch-up this move
        # Damp so we don't lurch to a huge blunder to "catch down" in one move.
        blended = (1.0 - self.p.damping) * exact + self.p.damping * target_mean
        return max(self.p.min_move_acc, min(100.0, blended))

    @staticmethod
    def _ends_in_draw(board: chess.Board, move: chess.Move) -> bool:
        """True if playing `move` ends the game as a draw right away
        (stalemate, dead position, threefold repetition, 50-move rule)."""
        b = board.copy()
        b.push(move)
        return (b.is_stalemate() or b.is_insufficient_material()
                or b.is_repetition(3) or b.halfmove_clock >= 100)

    @staticmethod
    def _wins(board: chess.Board, move: chess.Move, cp: int) -> bool:
        """True if `move` checkmates, or keeps a forced mate for us on the board."""
        if cp >= MATE_CP - 1000:
            return True
        b = board.copy(stack=False)
        b.push(move)
        return b.is_checkmate()

    def choose(self, board: chess.Board) -> MoveDecision:
        target = self._target_move_accuracy()

        # 1. A move that draws on the spot always wins the argument.
        for move in board.legal_moves:
            if self._ends_in_draw(board, move):
                self.me.add(target)
                return MoveDecision(move, target, target, 50.0)

        cands = self.engine.analyse(board, multipv=self.p.multipv,
                                    movetime=self.p.search_movetime)
        best_win = cands[0].win

        # 2. Never win: drop checkmates and forced-mate lines. If the top-N menu
        #    is all mates, widen it to every legal move before giving up.
        safe = [c for c in cands if not self._wins(board, c.move, c.cp)]
        if not safe:
            n_legal = board.legal_moves.count()
            if n_legal > len(cands):
                wide = self.engine.analyse(board, multipv=min(256, n_legal),
                                           movetime=self.p.search_movetime)
                safe = [c for c in wide if not self._wins(board, c.move, c.cp)]
        if safe:
            cands = safe          # (only if literally every move mates do we mate)

        best_c, best_acc, best_cost = cands[0], 100.0, float("inf")
        for c in cands:
            m_acc = move_accuracy(best_win, c.win)     # accuracy of this candidate

            # Primary: pull the resulting eval toward equal (win% -> 50). The
            # losing side of 50 is penalised `loss_aversion` times harder, so we
            # fight at full strength when behind and only *give back* when ahead
            # — never voluntarily crossing into a lost position.
            d = c.win - 50.0
            draw_pen = d if d >= 0 else (-d) * self.p.loss_aversion

            # Secondary: among similarly-drawish moves, match opponent accuracy.
            acc_term = abs(m_acc - target)

            cost = self.p.w_draw * draw_pen + self.p.w_acc * acc_term
            if cost < best_cost:
                best_cost, best_c, best_acc = cost, c, m_acc

        self.me.add(best_acc)
        return MoveDecision(best_c.move, target, best_acc, best_c.win)
