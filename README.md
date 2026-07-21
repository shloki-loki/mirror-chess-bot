# MirrorBot — a Lichess bot that mirrors its opponent's accuracy

MirrorBot plays on Lichess and tries to make every game a **draw** while keeping
its own [accuracy](#how-accuracy-is-defined) roughly level with the opponent's.
It runs on Stockfish, but instead of playing the strongest move it picks the move
that best balances two goals:

1. **Draw-seek (primary).** Pull the evaluation back toward equal — fight at full
   strength when behind, give the advantage back when ahead — so games tend to
   end level. "Tie in all permutations" is the ideal it aims at.
2. **Accuracy-match (secondary).** Among similarly drawish moves, pick the one
   that keeps its running accuracy tracking the opponent's.

> **Why not just mirror accuracy?** Two players can have identical accuracy and
> still get a decisive result — accuracy converges to ~100% for *both* sides in a
> decided game, so "match accuracy" alone is satisfied even while losing. Draw
> must therefore dominate; accuracy-matching lives in the equal zone where most
> of a game is played. See [Limitations](#limitations).

---

## How accuracy is defined

Standard Lichess / Chess.com pipeline (`src/accuracy.py`):

```
centipawns ──► win%  ──►  per-move accuracy  ──►  running mean
```

- **win%** `= 50 + 50·(2/(1+e^(−0.00368208·cp)) − 1)`  (mover's POV)
- **move accuracy** `= 103.1668·e^(−0.04354·ΔWin) − 3.1669`, `ΔWin = win_before − win_after`
- **game accuracy** = running mean of move accuracies (invertible, so the
  controller can solve for the accuracy a move needs).

The same full-strength Stockfish that picks the bot's move also acts as the
impartial **judge** that scores every played move (both sides).

---

## Setup

```bash
brew install stockfish                 # engine (Apple Silicon path auto-detected)
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

If Stockfish isn't on the default path, set `STOCKFISH_PATH=/path/to/stockfish`.

---

## Watch it work (no Lichess account needed)

`src/simulate.py` plays MirrorBot against a fixed-Elo Stockfish "human" and prints
both players' running accuracy each ply, then a summary.

```bash
./.venv/bin/python src/simulate.py --elos 1600,2200,2800 --color both --quiet
```

Typical result: **draws across the whole Elo spread**, with a small accuracy gap.

---

## Play live on Lichess

You need a **dedicated bot account** (a normal account that has played games can't
become a bot). These are the steps — you do them yourself; the token stays with
you and is never shared.

1. **Create a fresh Lichess account** for the bot at <https://lichess.org/signup>.
   Do **not** play any games with it (the upgrade below requires zero games).
2. **Generate a token** with the `bot:play` scope while logged in as that account:
   <https://lichess.org/account/oauth/token/create?scopes[]=bot:play&description=MirrorBot>
   → *Create* → copy the token (shown once).
3. **Point the bot at it:**
   ```bash
   export LICHESS_TOKEN=lip_xxxxxxxxxxxx
   ```
4. **Upgrade the account to a BOT** (one-time, **irreversible**):
   ```bash
   ./.venv/bin/python src/lichess_bot.py --upgrade
   ```
5. **Run the bot:**
   ```bash
   ./.venv/bin/python src/lichess_bot.py
   ```
6. **Challenge it:** from your main account open the bot's profile and send a
   challenge (Standard, at least 1+0 — ultrabullet is auto-declined). It also
   auto-accepts incoming challenges that meet those criteria.

The bot handles multiple games at once, adapts think-time to its clock, and logs
`me X.X vs opp X.X` accuracy as it plays.

### Keep it running

- **On your Mac (tmux)** — survives closing the terminal, keeps the Mac awake:
  ```bash
  export LICHESS_TOKEN=lip_xxxx
  ./scripts/start_tmux.sh          # attach: tmux attach -t mirrorbot | logs: tail -f bot.log
  ```
- **24/7 without your laptop (cloud)** — see [DEPLOY.md](DEPLOY.md) for a
  `systemd` service on a small Linux VM.

Run only **one** instance per account at a time.

---

## Train / calibrate the controller

`src/train_calibrate.py` plays batches of games and does coordinate-descent over
the controller weights to **minimize the accuracy gap, subject to a draw-rate
floor** (so tuning can't quietly reintroduce the "match accuracy while losing"
failure mode). It writes the best parameters to `data/best_params.json`.

```bash
./.venv/bin/python src/train_calibrate.py --rounds 2 --elos 1500,2000,2500 --movetime 0.04
```

Optionally validate the accuracy *formula* on real games from the
[Lichess HuggingFace dataset](https://huggingface.co/datasets/Lichess/standard-chess-games)
— it checks that the more-accurate side actually tends to win:

```bash
./.venv/bin/python src/train_calibrate.py --with-hf 200 --rounds 0
```

(Needs `pip install datasets`; on Python 3.14 use a 3.11/3.12 venv if wheels lag.)

To use tuned params in live play, load `data/best_params.json` into
`ControllerParams` where `LichessBot` is constructed.

---

## Limitations

- **A forced draw isn't always possible.** Against a determined, accurate
  opponent you cannot *force* a draw; after a huge opponent blunder the bot can
  only shed the advantage gradually (it gives back within its top-N candidate
  moves, not by dumping pieces). It maximizes draw probability — it can't
  guarantee it.
- **Accuracy is relative to the best move**, so it saturates near 100% in clearly
  won/lost positions. That's inherent to the metric (Lichess/Chess.com share it)
  and is *why* draw-seeking, not accuracy-matching, is the primary objective.

---

## File map

| File | Purpose |
|------|---------|
| `src/accuracy.py` | win% + accuracy formulas, running-mean tracker |
| `src/engine.py` | Stockfish wrapper (analysis, MultiPV, Elo-limited play) |
| `src/controller.py` | move selection: draw-seek + accuracy-match |
| `src/simulate.py` | local self-play demo & metrics |
| `src/lichess_bot.py` | live Lichess bot (event stream, game play) |
| `src/train_calibrate.py` | calibration loop + HF formula validation |
| `data/best_params.json` | tuned controller params (written by calibration) |
