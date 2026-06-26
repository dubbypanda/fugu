# Sokoban

Solve Sokoban boards with Sakana Fugu and render the attempt as an MP4.

This demo asks Fugu for short JSON action plans, applies those moves in a real
Sokoban environment, then asks again from the updated board state until the
puzzle is solved or the step budget is exhausted.

## Why this is a good test

Sokoban is a good test because it requires planning several moves ahead. The
rules are simple, but a bad push can make the puzzle impossible to solve.

## Setup

```bash
cd demos/sokoban
uv sync
cp .env.example .env
```

Fill in:

```bash
API_KEY=...
BASE_URL=...
MODEL=...
```

## Run

```bash
uv run python main.py
```

Example:

```bash
uv run python main.py --seed 5
```

Videos are saved as `results/<model>/seed-<seed>.mp4` unless `--output` is set.
