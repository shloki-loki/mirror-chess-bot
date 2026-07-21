"""Chess accuracy model — Lichess / Chess.com compatible.

Pipeline:  centipawns  ->  win%  ->  per-move accuracy  ->  running mean.

All win% values are from the perspective of the side that just moved
(or the side to move). The constants below are the published
Lichess / Chess.com values, so our numbers line up with what players see.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# --- published constants -------------------------------------------------
WIN_SIGMOID_K = 0.00368208   # centipawn -> win% steepness
ACC_A = 103.1668             # accuracy curve scale
ACC_B = 0.04354              # accuracy curve decay
ACC_C = 3.1669               # accuracy curve offset
MATE_CP = 10000              # cp value assigned to a forced mate


def win_percent(cp: float) -> float:
    """Centipawns (mover's POV) -> win expectancy in [0, 100]."""
    return 50.0 + 50.0 * (2.0 / (1.0 + math.exp(-WIN_SIGMOID_K * cp)) - 1.0)


def move_accuracy(win_before: float, win_after: float) -> float:
    """Per-move accuracy in [0, 100] from the win% drop the move caused."""
    delta = max(0.0, win_before - win_after)          # gaining eval never hurts
    acc = ACC_A * math.exp(-ACC_B * delta) - ACC_C
    return max(0.0, min(100.0, acc))


def accuracy_to_winloss(accuracy: float) -> float:
    """Invert move_accuracy: a target accuracy -> the win% loss it implies."""
    accuracy = max(0.0, min(100.0, accuracy))
    inner = (accuracy + ACC_C) / ACC_A                # acc = A*e^(-B*d) - C
    inner = min(1.0, max(1e-9, inner))
    return -math.log(inner) / ACC_B                   # d = -ln((acc+C)/A)/B


@dataclass
class RunningAccuracy:
    """Simple running mean of one player's per-move accuracies."""
    moves: list[float] = field(default_factory=list)

    def add(self, acc: float) -> None:
        self.moves.append(acc)

    @property
    def n(self) -> int:
        return len(self.moves)

    @property
    def mean(self) -> float:
        # No moves yet == "perfect so far"; keeps the target well-defined.
        return sum(self.moves) / len(self.moves) if self.moves else 100.0

    def required_next(self, target_mean: float) -> float:
        """Accuracy the next move needs so the running mean becomes target_mean."""
        return target_mean * (self.n + 1) - sum(self.moves)
