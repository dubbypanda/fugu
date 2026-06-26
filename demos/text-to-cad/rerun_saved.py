#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent
DEFAULT_MODELS = ["fugu_ultra", "fugu", "gpt55", "opus48", "gemini"]


def default_base_repo() -> Path:
    return HERE.resolve()


def default_python(base_repo: Path) -> Path:
    candidate = base_repo / ".venv/bin/python"
    return candidate if candidate.exists() else Path(sys.executable)


def default_run_id() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y%m%dT%H%M%S.") + f"{now.microsecond // 1000:03d}Z"


def latest_result_run_id(keys: list[str]) -> str | None:
    result_root = HERE / "results"
    if not result_root.exists():
        return None

    candidates = []
    for path in result_root.iterdir():
        if not path.is_dir():
            continue
        if (path / "model.py").exists():
            continue
        if any((path / key / "model.py").exists() for key in keys):
            candidates.append(path)
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: path.name)[-1].name


def resolve_source_run_id(value: str, keys: list[str]) -> str | None:
    if value == "legacy":
        return None
    if value == "latest":
        return latest_result_run_id(keys)
    return value


def parse_result(stdout: str) -> dict:
    for line in stdout.splitlines():
        if line.startswith("##CAD_RESULT## "):
            return json.loads(line[len("##CAD_RESULT## ") :])
    return {
        "ok": False,
        "error": "runner produced no ##CAD_RESULT## record",
        "stdout_tail": "\n".join(stdout.splitlines()[-20:]),
    }


def find_steps(output_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in output_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".step", ".stp"}
    )


def run_command(command: list[str], *, cwd: Path, env: dict[str, str]) -> dict:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
    }


def validate_first_step(step_path: Path, *, base_repo: Path, python: Path, env: dict[str, str]) -> dict:
    validation: dict[str, object] = {"step": str(step_path), "step_cli": None, "inspect_refs": None}

    step_tool = base_repo / "skills/cad/scripts/step"
    inspect_tool = base_repo / "skills/cad/scripts/inspect"

    if step_tool.exists():
        validation["step_cli"] = run_command(
            [str(python), str(step_tool), str(step_path), "--kind", "assembly", "--force"],
            cwd=base_repo,
            env=env,
        )

    if inspect_tool.exists():
        validation["inspect_refs"] = run_command(
            [str(python), str(inspect_tool), "refs", str(step_path), "--facts", "--planes", "--positioning"],
            cwd=base_repo,
            env=env,
        )

    return validation


def run_model(
    key: str,
    *,
    source_run_id: str | None,
    run_dir: Path,
    base_repo: Path,
    python: Path,
    validate: bool,
) -> dict:
    if source_run_id:
        model_path = HERE / "results" / source_run_id / key / "model.py"
    else:
        model_path = HERE / "results" / key / "model.py"
    output_dir = run_dir / key / "artifacts"
    output_dir.mkdir(parents=True, exist_ok=True)

    record = {
        "key": key,
        "source_run_id": source_run_id or "legacy",
        "model_path": str(model_path),
        "output_dir": str(output_dir),
        "exists": model_path.exists(),
        "runner": None,
        "steps": [],
        "validation": None,
    }
    if not model_path.exists():
        record["runner"] = {"ok": False, "error": "missing frozen model.py"}
        return record

    env = os.environ.copy()
    package_paths = [
        base_repo / "packages/cadpy/src",
        base_repo / "skills/cad/scripts/packages/cadpy/src",
    ]
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(path) for path in package_paths if path.exists()]
        + ([existing_pythonpath] if existing_pythonpath else [])
    )

    completed = subprocess.run(
        [str(python), str(HERE / "model_runner.py"), str(model_path), str(output_dir)],
        cwd=base_repo,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    runner = parse_result(completed.stdout)
    runner["returncode"] = completed.returncode
    runner["stdout"] = completed.stdout
    record["runner"] = runner

    steps = find_steps(output_dir)
    record["steps"] = [str(path) for path in steps]
    if validate and steps:
        record["validation"] = validate_first_step(steps[0], base_repo=base_repo, python=python, env=env)

    return record


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild CAD artifacts from frozen model.py files.")
    parser.add_argument("models", nargs="*", default=None, help="Model keys to run. Defaults to all core models.")
    parser.add_argument("--base-repo", type=Path, default=default_base_repo())
    parser.add_argument("--python", type=Path, default=None, help="Python executable. Defaults to base .venv/bin/python.")
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument(
        "--source-run-id",
        default="latest",
        help='Codex result run id under results/. Defaults to "latest"; use "legacy" for results/<model>/model.py.',
    )
    parser.add_argument("--no-validate", action="store_true")
    args = parser.parse_args()

    base_repo = args.base_repo.resolve()
    python = args.python or default_python(base_repo)
    keys = args.models or DEFAULT_MODELS
    source_run_id = resolve_source_run_id(args.source_run_id, keys)
    run_dir = (
        args.run_dir.resolve()
        if args.run_dir
        else HERE / "runs" / default_run_id()
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"source run id: {source_run_id or 'legacy'}", flush=True)
    print(f"replay run dir: {run_dir}", flush=True)

    summary = {
        "base_repo": str(base_repo),
        "python": str(python),
        "source_run_id": source_run_id or "legacy",
        "run_dir": str(run_dir),
        "models": [],
    }
    for key in keys:
        print(f"[{key}] rebuilding frozen model", flush=True)
        summary["models"].append(
            run_model(
                key,
                source_run_id=source_run_id,
                run_dir=run_dir,
                base_repo=base_repo,
                python=python,
                validate=not args.no_validate,
            )
        )

    summary_path = run_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"summary: {summary_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
