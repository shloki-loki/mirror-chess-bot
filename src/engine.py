"""Stockfish UCI wrapper built on python-chess.

One `Engine` instance is a running Stockfish process. We use two of them:
a full-strength one that acts as the bot's brain *and* the impartial judge of
move quality, and (in simulation) a second one throttled to a fixed Elo to
stand in for a human opponent.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

import chess
import chess.engine

from accuracy import MATE_CP, win_percent

_CANDIDATE_PATHS = [
    os.environ.get("STOCKFISH_PATH"),
    "/opt/homebrew/bin/stockfish",   # Apple Silicon brew
    "/usr/local/bin/stockfish",      # Intel brew
    "/usr/games/stockfish",          # Debian/Ubuntu apt
    "/usr/bin/stockfish",
]

# Stockfish's UCI_Elo only accepts this window.
SF_MIN_ELO, SF_MAX_ELO = 1320, 3190


def find_stockfish() -> str:
    for p in _CANDIDATE_PATHS:
        if p and os.path.exists(p):
            return p
    found = shutil.which("stockfish")
    if found:
        return found
    raise FileNotFoundError(
        "Stockfish not found. Install with `brew install stockfish` "
        "or set STOCKFISH_PATH=/path/to/stockfish."
    )


@dataclass
class Candidate:
    move: chess.Move
    cp: int      # score in centipawns, POV of the side to move at `board`
    win: float   # win% for the side to move


class Engine:
    def __init__(self, path: str | None = None, threads: int = 1, hash_mb: int = 128):
        self.engine = chess.engine.SimpleEngine.popen_uci(path or find_stockfish())
        self.engine.configure({"Threads": threads, "Hash": hash_mb})

    # --- lifecycle --------------------------------------------------------
    def close(self) -> None:
        try:
            self.engine.quit()
        except Exception:
            pass

    def __enter__(self) -> "Engine":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- analysis (bot brain / judge) ------------------------------------
    def analyse(self, board: chess.Board, multipv: int = 1,
                movetime: float = 0.1) -> list[Candidate]:
        """Top `multipv` moves, best-first, scored from the side-to-move POV."""
        if board.is_game_over():          # no legal moves — don't call the engine
            return []
        infos = self.engine.analyse(
            board, chess.engine.Limit(time=movetime), multipv=multipv
        )
        if isinstance(infos, dict):        # multipv==1 can return a bare dict
            infos = [infos]
        stm = board.turn
        out: list[Candidate] = []
        for info in infos:
            pv = info.get("pv")
            if not pv:
                continue
            cp = info["score"].pov(stm).score(mate_score=MATE_CP)
            out.append(Candidate(move=pv[0], cp=cp, win=win_percent(cp)))
        return out

    # --- weakened play (simulated opponent) ------------------------------
    def set_skill(self, elo: int | None) -> None:
        """Cap playing strength to a given Elo, or None for full strength."""
        if elo is None:
            self.engine.configure({"UCI_LimitStrength": False})
        else:
            elo = max(SF_MIN_ELO, min(SF_MAX_ELO, int(elo)))
            self.engine.configure({"UCI_LimitStrength": True, "UCI_Elo": elo})

    def play(self, board: chess.Board, movetime: float = 0.1) -> chess.Move:
        result = self.engine.play(board, chess.engine.Limit(time=movetime))
        return result.move
