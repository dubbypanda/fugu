# Rubik's Cube Solver (one-shot LLM code-writing)

A reproducible comparison of LLMs on one verifiable task: write a Rubik's cube
solver from scratch, in a single shot. Each model receives the same prompt and
must return a self-contained Python module defining `solve(facelet: str) -> str`.
The model spends tokens only once (writing the solver). That frozen solver then
runs locally against a frozen set of 300 scrambled cubes, which costs no tokens.
Every returned solution is re-verified with a trusted cube engine (`cube.py`), so
a model cannot win by falsely claiming a cube is solved.

This isolates a clean signal: can the model produce correct, efficient, robust
algorithmic code in one shot?

## Quick start

All commands run from this folder.

**1. Reproduce the published numbers** (no API key, Python standard library only).
The solvers are frozen, so this is pure local CPU and calls no model:

```bash
RUBIK_CUBE_TIMEOUT=600 RUBIK_SUMMARY_OUT=summary_300.json python3 rerun_saved.py
# writes results/summary_300.json
```

Note: reproducing all solvers runs them serially and can take several hours, since
the optimal solvers search deeply on each cube. To spot-check quickly, reproduce a
single solver, e.g. `python3 rerun_saved.py fugu`.

**2. Run a model to generate a solver** (this is the step that calls Fugu or
another LLM). Set the relevant key first, then:

```bash
# Set the key for each model you run (each provider reads its own env var;
# you only need the key(s) for the model(s) you actually run):
export FUGU_API_KEY=...        # Fugu / Fugu-Ultra (OpenAI-compatible)
export OPENAI_API_KEY=...      # GPT-5.5
export ANTHROPIC_API_KEY=...   # Opus 4.8 / Fable 5
export GEMINI_API_KEY=...      # Gemini 3.1 Pro

python3 rubik_compare.py fugu_ultra   # a single model (needs only its own key)
python3 rubik_compare.py              # all models (needs all four keys above)
```

`rubik_compare.py` sends `prompt.txt` to the model, saves the returned solver to
`results/<model>_solver.py`, then runs and verifies it with `cube.py`. The one
generation call is the only thing that costs tokens; the solving is local and free.

Sanity-check the engine itself with `python3 cube.py`, which runs its self-test.

## The task contract

- Input: a 54-character facelet string (faces in order `U R F D L B`, each read
  row-major). See `prompt.txt` for the exact, complete spec given to every model.
- Output: a space-separated WCA move string (`R U R' U2 F D' L2`); an empty string
  means the cube is already solved.
- Rules: standard library only, no cube or solver library, `solve()` must be a pure
  function (no I/O), and reasonably fast (a per-cube wall-clock cap applies).
- Scoring: solve rate (correctness, re-verified) and mean turn count in HTM
  (efficiency). God's number is 20, the absolute upper bound on optimal length.

## Results (300 cubes)

| Model | Solve rate | Mean HTM | Notes |
|---|---:|---:|---|
| Fugu-Ultra | 300 / 300 | 19.72 | most efficient |
| GPT-5.5 | 300 / 300 | 19.76 | |
| Fable 5 | 300 / 300 | 20.22 | two-phase (Kociemba) solver; fast and steady (max 4 s/cube), 0 timeouts |
| Fugu | 300 / 300 | 21.15 | about 35x faster per cube than Ultra/GPT (max about 13 s vs about 250 to 305 s) |
| Claude Opus 4.8 | 0 / 300 | n/a | written module raises `IndexError` at import (no usable `solve`) |
| Gemini 3.1 Pro | 0 / 300 | n/a | `solve()` raises `IndexError` at runtime on every cube |

Two of six frontier solvers do not run at all, so the robustness axis matters as
much as the move count. Among the four solvers that run, the move counts sit in a
narrow band (about 19.7 to 21.2), so the headline is robustness plus Fugu's speed,
not a large efficiency gap.

The full per-cube result for each model (every cube's id, solution, turn count,
and time) is in `results/<model>_300.json`, e.g. `results/fugu_ultra_300.json`.

## How each model is called

The numbers come from one API call per model: the model writes the solver once.
The call functions live in `rubik_compare.py` (`call_fugu`, `call_fugu_ultra`,
`call_gpt55`, `call_opus48`, `call_fable5`, `call_gemini`). Each uses the same
prompt (`prompt.txt`), the highest reasoning effort that reliably returns output
for this task, keys read from environment variables, and no retries
(`max_retries=0`, so a failure is visible and never silently re-billed).

Keys and SDKs: `FUGU_API_KEY` (Fugu, OpenAI-compatible), `OPENAI_API_KEY`
(`openai`), `ANTHROPIC_API_KEY` (`anthropic`), `GEMINI_API_KEY` (`google-genai`).

| key | API surface | model id | effort / params |
|---|---|---|---|
| `fugu` | OpenAI-compatible @ `https://api.sakana.ai/v1` | `fugu` | `max_tokens=64000` (default reasoning effort) |
| `fugu_ultra` | OpenAI-compatible @ `https://api.sakana.ai/v1` (streamed) | `fugu-ultra` | `reasoning_effort="max"`, `max_tokens=64000` |
| `gpt55` | OpenAI Responses (`client.responses.create`) | `gpt-5.5` | `reasoning.effort="high"`, `max_output_tokens=64000` |
| `opus48` | Anthropic Messages stream | `claude-opus-4-8` | `thinking=adaptive`, `output_config.effort="high"`, `max_tokens=64000` |
| `fable5` | Anthropic Messages stream | `claude-fable-5` | `thinking=adaptive`, `output_config.effort="xhigh"`, `max_tokens=128000` |
| `gemini` | Google GenAI (`models.generate_content`) | `gemini-3.1-pro-preview` | `thinking_level=HIGH`, `max_output_tokens=65536` |

The exact Fugu call (verbatim from `rubik_compare.py`):

```python
import os, openai

# Fugu (OpenAI-compatible endpoint)
client = openai.OpenAI(api_key=os.environ["FUGU_API_KEY"],
                       base_url="https://api.sakana.ai/v1", timeout=8000, max_retries=0)
resp = client.chat.completions.create(
    model="fugu", messages=[{"role": "user", "content": PROMPT}],
    max_tokens=64000)
text = resp.choices[0].message.content

# Fugu-Ultra (same endpoint, streamed; reasoning_effort high or max only)
resp = client.chat.completions.create(
    model="fugu-ultra", messages=[{"role": "user", "content": PROMPT}],
    max_tokens=64000, extra_body={"reasoning_effort": "max"},
    stream=True, stream_options={"include_usage": True})
text = "".join(c.choices[0].delta.content or "" for c in resp if c.choices)
```

The other four models (verbatim from `rubik_compare.py`):

```python
# GPT-5.5 (OpenAI Responses API)
import openai
client = openai.OpenAI(timeout=8000, max_retries=0)          # OPENAI_API_KEY
resp = client.responses.create(
    model="gpt-5.5", input=PROMPT,
    reasoning={"effort": "high"}, max_output_tokens=64000)
text = resp.output_text

# Claude Opus 4.8 (Anthropic Messages, streamed)
import anthropic
client = anthropic.Anthropic(timeout=8000, max_retries=0)    # ANTHROPIC_API_KEY
with client.messages.stream(
        model="claude-opus-4-8", max_tokens=64000,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},                    # "max" can starve output
        messages=[{"role": "user", "content": PROMPT}]) as stream:
    for _ in stream:
        pass
text = next(b.text for b in stream.get_final_message().content if b.type == "text")

# Fable 5 (Anthropic Messages, streamed; needs the 128k budget at xhigh)
with client.messages.stream(
        model="claude-fable-5", max_tokens=128000,
        thinking={"type": "adaptive"},
        output_config={"effort": "xhigh"},
        messages=[{"role": "user", "content": PROMPT}]) as stream:
    for _ in stream:
        pass
text = next(b.text for b in stream.get_final_message().content if b.type == "text")

# Gemini 3.1 Pro (Google GenAI)
from google import genai
from google.genai import types
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"],
                      http_options=types.HttpOptions(timeout=8000 * 1000))
resp = client.models.generate_content(
    model="gemini-3.1-pro-preview", contents=PROMPT,
    config=types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.HIGH),
        max_output_tokens=65536))
text = next(p.text for p in resp.candidates[0].content.parts
            if getattr(p, "text", None) and not getattr(p, "thought", False))
```

Why these effort levels. Each model runs at the highest effort that reliably
returns output for this task, not a uniform setting:

- `opus48` uses `high`, not `max`. At `max` the reasoning consumes the whole
  `max_tokens` budget and returns an empty string for this prompt, so `high` is its
  best usable setting here. (Its 0 / 300 is a bug in the code it wrote at `high`,
  not an effort limitation.)
- `gpt55` uses `high` for the same reason; `xhigh` hung for tens of minutes.
- `fugu` uses the model's default reasoning effort (we do not set one).
- `fugu-ultra` accepts only `high` or `max` (we use `max`).
- `fable5` needs the 128k token budget to avoid starving at `xhigh`.
- `gemini` uses `thinking_level=HIGH`, its highest standard thinking level.

## Directory layout

```
prompt.txt              The exact prompt given to every model.
cube.py                 Trusted cube engine: applies moves, verifies solved, counts turns.
                        This is the ground truth, and it also generates the eval set.
eval_cubes.json         The frozen 300-cube eval set (id 0 is the "hero" cube for visuals).
build_eval_set.py       Regenerates eval_cubes.json deterministically from seeds.

rubik_compare.py        Calls each model's API with prompt.txt, saves the returned solver,
                        then runs and verifies it. This is how a model is run. (Needs API keys.)
solver_runner.py        Subprocess-side runner: imports one solver, runs it per cube with a
                        per-cube timeout, prints raw solutions (no judging).
rerun_saved.py          Reproduce from the FROZEN solvers, no API calls. Main reproduction entry.

results/
  <model>_solver.py     The exact solver code each model wrote (frozen).
  <model>_raw.txt       The raw model response it was extracted from.
  <model>_300.json      Full per-cube result for that model on the 300-cube set.
```

Models (key to label): `fugu_ultra` (Fugu-Ultra), `fugu` (Fugu), `gpt55` (GPT-5.5),
`opus48` (Claude Opus 4.8), `gemini` (Gemini 3.1 Pro), `fable5` (Fable 5).
