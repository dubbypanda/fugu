# Text-to-CAD

Compact Codex harness for comparing text-to-CAD generation across Fugu and
OpenRouter-backed models.

This work follows the CAD-agent workflow from
[earthtojake/text-to-cad](https://github.com/earthtojake/text-to-cad), a skills
library for CAD, robotics, and hardware-design agents.

The default example is intentionally small:

```text
Create a mechanical iris as a CAD assembly.
```

The task prompt lives in `prompt.txt`. Edit that file to change the default
task, or pass `--prompt path/to/prompt.txt` for one run.

## Quick Start

Run from this directory:

```bash
cd demos/text-to-cad
bash setup.sh
```

Edit `.env` and set the keys you need:

```bash
SAKANA_API_KEY=...
OPENROUTER_API_KEY=...
```

`SAKANA_API_KEY` is required for `fugu_ultra` and `fugu`.
`OPENROUTER_API_KEY` is required for `gpt55`, `opus48`, and `gemini`.

## Run The Example

Run the simple mechanical iris prompt against the four comparison models:

```bash
.venv/bin/python run_codex.py --parallel 4 fugu_ultra gpt55 opus48 gemini
```

The command prints a run id such as:

```text
run id: 20260625T121959.914Z
```

Render the generated STEP files as orbit GIFs and make one side-by-side
comparison GIF:

```bash
.venv/bin/python render_compare.py \
  --run-dir results/<run-id> \
  fugu_ultra gpt55 opus48 gemini
```

Open:

```text
results/<run-id>/compare.gif
```

For a single-model smoke test:

```bash
.venv/bin/python run_codex.py fugu_ultra
```

To run every model listed in `models.toml`:

```bash
.venv/bin/python run_codex.py --parallel 5
```

## Models

Model keys are defined in `models.toml`:

| Key | Provider | Default reasoning |
| --- | --- | --- |
| `fugu_ultra` | `codex -p fugu -m fugu-ultra` | Codex/Fugu profile default |
| `fugu` | `codex -p fugu -m fugu` | Codex/Fugu profile default |
| `gpt55` | `openai/gpt-5.5` through OpenRouter | `xhigh` |
| `opus48` | `anthropic/claude-opus-4.8` through OpenRouter | `max` |
| `gemini` | `google/gemini-3.1-pro-preview` through OpenRouter | `high` |

The harness does not edit `~/.codex/config.toml`. Provider and model choices
are read from `models.toml` and passed to `codex exec` as one-shot CLI flags and
`-c` overrides.

## What Gets Written

Each live run writes frozen model output here:

```text
results/<run-id>/<model>/
```

The key file is:

```text
results/<run-id>/<model>/model.py
```

Generated CAD artifacts are usually under:

```text
results/<run-id>/<model>/artifacts/
```

Each result also saves the raw task prompt and full Codex prompt:

```text
results/<run-id>/<model>/prompt.txt
results/<run-id>/<model>/codex_prompt.txt
```

`render_compare.py` writes:

```text
results/<run-id>/_snapshots/<model>.gif
results/<run-id>/compare.gif
```

## Isolation

Live Codex execution is isolated outside this directory. `run_codex.py` copies
this self-contained workspace into:

```text
/tmp/text-to-cad-workspaces-<repo hash>/<run-id>/<model>/
```

Codex runs there, then only `results/<run-id>/<model>` is copied back into this
directory. `.env`, `.venv`, prior `results/`, and prior `runs/` are not copied
into the temporary workspace. On macOS this may appear as `/private/tmp/...`.

Use `TEXT_TO_CAD_WORKSPACE_ROOT` in `.env` or `--workspace-root` to override the
temporary workspace root.

## Useful Commands

Print the exact Codex command without calling any model:

```bash
.venv/bin/python run_codex.py --dry-run fugu_ultra
```

Use an explicit run id:

```bash
.venv/bin/python run_codex.py --run-id iris-demo-001 fugu_ultra
```

If a saved result already exists for the same run id and model, the harness
stops instead of overwriting it. To repeat a trial, run without `--run-id` or
choose a new one.

Rebuild CAD artifacts from already generated `model.py` files without calling
any model API:

```bash
.venv/bin/python rerun_saved.py --source-run-id <run-id>
```

Render a specific saved result directory:

```bash
.venv/bin/python render_compare.py --run-dir results/<run-id>
```

Run with a different prompt file:

```bash
.venv/bin/python run_codex.py --prompt prompts/my-task.txt fugu_ultra gpt55
```

## Layout

```text
.env.example        Environment variable template.
models.toml         Benchmark-local model/provider config.
prompt.txt          Default mechanical iris prompt.
setup.sh            Convenience setup for .env, CAD runtime, and Fugu Codex support.
run_codex.py        Live Codex harness; writes frozen model.py files.
model_runner.py     Subprocess runner for one frozen model.py.
rerun_saved.py      Rebuild STEP artifacts from frozen model.py files.
render_compare.py   Render STEP orbit GIFs and compose a comparison GIF.
skills/cad/         Vendored CAD generation, validation, and snapshot skill.
packages/cadpy/     Vendored CAD helper runtime used by the CAD skill.
results/            Frozen model outputs, one directory per live run.
```

The CAD skill and `cadpy` helper runtime are vendored in this directory. Live
Codex runs copy this directory into an isolated `/tmp` workspace, symlink the
local `.venv` when present, and copy only `results/<run-id>/<model>` back.

## Files To Commit

Commit the harness, prompt, config, and frozen
`results/<run-id>/<model>/model.py` files you want to keep. Do not commit live
workspaces, CAD artifacts, logs, rendered GIFs, or `.env`; they are ignored by
default.
