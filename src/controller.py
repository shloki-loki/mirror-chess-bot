"""MirrorBot move selection.

Objective (per project spec): **draw-seek primary, match accuracy soft**.

Draw-seek aims slightly *above* equal, not at it: a small edge for the bot,
shrinking toward dead level as pieces come off. From a slightly better
position a draw is easy to force (repeat moves, trade down, offer a draw);
from a slightly worse one it isn't. The bot never delivers checkmate unless
the only alternative is a clearly lost position, and it actively prefers
forced draws: instant draws, repetitions, and trades near the target.

Closeness to the target is judged two ways at once: win% (the main signal)
and plain pawns. Win% flattens out far from equal (+8 and mate-in-1 both
look like ~100%), so the pawn term keeps the bot sensible when an opponent
keeps hanging mates or when it is far behind.

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

from accuracy import MATE_CP, RunningAccuracy, move_accuracy, win_percent
from engine import Candidate, Engine


@dataclass
class ControllerParams:
    multipv: int = 16            # size of the candidate menu
    w_draw: float = 1.0         # weight: steer the eval toward equal (draw)
    w_acc: float = 0.30         # weight: match the opponent's accuracy
    loss_aversion: float = 3.0  # how much harder we avoid the losing side of 50%
    damping: float = 0.5        # blend exact catch-up with opponent's mean (0..1)
    min_move_acc: float = 35.0  # never deliberately play below this accuracy
    lost_cp: int = -300         # if every non-mating move is worse than this, mate
    edge_mid_cp: int = 100      # target edge with all pieces on the board
    edge_end_cp: int = 25       # target edge in a bare endgame (merges to draw)
    w_win: float = 0.5          # share of the draw-pull measured in win%
    w_pawn: float = 0.5         # share measured in pawns (scaled to win% units)
    repeat_bonus: float = 6.0   # pull toward repeating a position (-> threefold)
    trade_bonus: float = 0.6    # per pawn-value captured while near the target
    verify_factor: float = 3.0  # deeper re-check of give-back moves (x movetime)
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

    def _target_cp(self, board: chess.Board) -> int:
        """Edge to aim for: bigger in the middlegame, near zero in the endgame."""
        vals = {chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}
        npm = sum(len(board.pieces(pt, c)) * v
                  for pt, v in vals.items() for c in (chess.WHITE, chess.BLACK))
        phase = min(1.0, npm / 62.0)                     # 1 = opening, 0 = bare kings
        return round(self.p.edge_end_cp
                     + (self.p.edge_mid_cp - self.p.edge_end_cp) * phase)

    @staticmethod
    def _is_mate_now(board: chess.Board, move: chess.Move) -> bool:
        b = board.copy(stack=False)
        b.push(move)
        return b.is_checkmate()

    def _draw_cost(self, board: chess.Board, c: Candidate, target_cp: int) -> float:
        """How far `c` lands from the target edge, in win% units (lower = better)."""
        la = self.p.loss_aversion
        dw = c.win - win_percent(target_cp)
        win_pen = dw if dw >= 0 else -dw * la
        dp = (max(-1500, min(1500, c.cp)) - target_cp) / 100.0
        pawn_pen = (dp if dp >= 0 else -dp * la) * 9.0   # ~9 win% per pawn near 0
        cost = self.p.w_win * win_pen + self.p.w_pawn * pawn_pen

        # Forced-draw seeking, only while we're not worse off than slightly behind.
        if c.cp >= -50:
            after = board.copy()
            after.push(c.move)
            if after.is_repetition(2):                    # one more repeat = draw
                cost -= self.p.repeat_bonus
            if board.is_capture(c.move) and c.cp <= target_cp + 150:
                captured = board.piece_at(c.move.to_square)
                val = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
                       chess.ROOK: 5, chess.QUEEN: 9}.get(
                    captured.piece_type if captured else chess.PAWN, 1)
                cost -= self.p.trade_bonus * val          # simplify toward a draw
        return cost

    def choose(self, board: chess.Board) -> MoveDecision:
        target = self._target_move_accuracy()
        target_cp = self._target_cp(board)

        # 1. A move that draws on the spot always wins the argument.
        for move in board.legal_moves:
            if self._ends_in_draw(board, move):
                self.me.add(target)
                return MoveDecision(move, target, target, 50.0)

        cands = self.engine.analyse(board, multipv=self.p.multipv,
                                    movetime=self.p.search_movetime)
        best_win = cands[0].win

        # 2. Split off winning moves (mate now, or a forced-mate line). If the
        #    top-N menu is all winning, widen it to every legal move.
        normal = [c for c in cands if not self._wins(board, c.move, c.cp)]
        winning = [c for c in cands if self._wins(board, c.move, c.cp)]
        if not normal and board.legal_moves.count() > len(cands):
            wide = self.engine.analyse(board, multipv=min(256, board.legal_moves.count()),
                                       movetime=self.p.search_movetime)
            normal = [c for c in wide if not self._wins(board, c.move, c.cp)]
            winning = [c for c in wide if self._wins(board, c.move, c.cp)]

        # 3. Take the win only if declining it means being clearly lost —
        #    otherwise an opponent could ignore mate threats and grab material.
        if winning and normal and normal[0].cp < self.p.lost_cp:
            self.me.add(100.0)
            return MoveDecision(winning[0].move, target, 100.0, winning[0].win)

        # 4. Every move wins: keep the game going with the least-winning move
        #    that is not checkmate itself; mate only if literally forced to.
        if not normal:
            slow = [c for c in winning if not self._is_mate_now(board, c.move)]
            pick = min(slow, key=lambda c: c.cp) if slow else winning[0]
            acc = move_accuracy(best_win, pick.win)
            self.me.add(acc)
            return MoveDecision(pick.move, target, acc, pick.win)

        # 5. Score the normal moves: draw-pull toward the edge (win% + pawns),
        #    forced-draw bonuses, then accuracy-matching as the tie-breaker.
        scored = []
        for c in normal:
            m_acc = move_accuracy(best_win, c.win)
            cost = (self.p.w_draw * self._draw_cost(board, c, target_cp)
                    + self.p.w_acc * abs(m_acc - target))
            scored.append((cost, c, m_acc))
        scored.sort(key=lambda t: t[0])

        # 6. A deliberate give-back (well below the best move) gets a deeper
        #    look first: a quick search can think a move is level when it loses.
        best_cp = normal[0].cp
        for cost, c, m_acc in scored[:4]:
            if best_cp - c.cp <= 150:
                break                                     # not a give-back
            after = board.copy(stack=False)
            after.push(c.move)
            deep = self.engine.analyse(after, multipv=1,
                                       movetime=self.p.search_movetime * self.p.verify_factor)
            deep_cp = -deep[0].cp if deep else c.cp
            if deep_cp >= min(c.cp - 100, -50):
                break                                     # holds up: play it
        else:   # every give-back checked turned out bad: play the safest move
            safe = [t for t in scored if best_cp - t[1].cp <= 150]
            cost, c, m_acc = safe[0] if safe else (0.0, normal[0],
                                                   move_accuracy(best_win, normal[0].win))

        self.me.add(m_acc)
        return MoveDecision(c.move, target, m_acc, c.win)
