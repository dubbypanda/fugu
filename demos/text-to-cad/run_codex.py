#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent
OUTPUT_LOCK = threading.Lock()


def emit(message: str, *, stream=None) -> None:
    if stream is None:
        stream = sys.stdout
    with OUTPUT_LOCK:
        print(message, file=stream, flush=True)


@dataclass(frozen=True)
class ModelSpec:
    key: str
    provider: str
    model: str
    profile: str | None = None
    reasoning_effort: str | None = None
    env_key: str | None = None


@dataclass(frozen=True)
class ProviderSpec:
    key: str
    name: str
    base_url: str
    env_key: str | None = None
    wire_api: str | None = None


@dataclass(frozen=True)
class BenchmarkConfig:
    providers: dict[str, ProviderSpec]
    models: dict[str, ModelSpec]
    default_models: list[str]


def default_base_repo() -> Path:
    return HERE.resolve()


def default_workspace_root() -> Path:
    digest = hashlib.sha1(str(HERE.resolve()).encode("utf-8")).hexdigest()[:10]
    return Path("/tmp") / f"text-to-cad-workspaces-{digest}"


def default_run_id() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y%m%dT%H%M%S.") + f"{now.microsecond // 1000:03d}Z"


def load_toml(path: Path) -> dict:
    try:
        import tomllib
    except ModuleNotFoundError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ModuleNotFoundError as exc:
            raise SystemExit("Python 3.11+ or the tomli package is required to read models.toml.") from exc
    return tomllib.loads(path.read_text(encoding="utf-8"))


def load_model_config(path: Path) -> BenchmarkConfig:
    data = load_toml(path)

    providers: dict[str, ProviderSpec] = {}
    for key, raw in data.get("providers", {}).items():
        providers[key] = ProviderSpec(
            key=key,
            name=str(raw.get("name", key)),
            base_url=str(raw["base_url"]),
            env_key=raw.get("env_key"),
            wire_api=raw.get("wire_api"),
        )

    models: dict[str, ModelSpec] = {}
    for key, raw in data.get("models", {}).items():
        if not raw.get("enabled", True):
            continue
        models[key] = ModelSpec(
            key=key,
            provider=str(raw["provider"]),
            model=str(raw["model"]),
            profile=raw.get("profile"),
            reasoning_effort=raw.get("reasoning_effort"),
            env_key=raw.get("env_key"),
        )

    default_models = [str(key) for key in data.get("default_models", list(models))]
    missing = [key for key in default_models if key not in models]
    if missing:
        raise SystemExit(f"default_models contains unknown or disabled models: {', '.join(missing)}")
    return BenchmarkConfig(providers=providers, models=models, default_models=default_models)


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            values[key] = value
    return values


def prepare_workspace(base_repo: Path, workspace: Path, *, force: bool) -> None:
    if workspace.exists() and force:
        shutil.rmtree(workspace)
    if workspace.exists():
        return

    ignore = shutil.ignore_patterns(
        ".git",
        ".agents",
        ".codex",
        ".env",
        ".pytest_cache",
        ".venv",
        "__pycache__",
        "*.pyc",
        "logs",
        "models",
        "node_modules",
        "results",
        "runs",
        "viewer/node_modules",
        "tmp",
        "workspaces",
        ".DS_Store",
    )
    shutil.copytree(base_repo, workspace, ignore=ignore, symlinks=True)

    base_venv = base_repo / ".venv"
    if base_venv.exists():
        try:
            (workspace / ".venv").symlink_to(base_venv, target_is_directory=True)
        except FileExistsError:
            pass


def build_prompt(spec: ModelSpec, task_prompt: str, *, run_id: str) -> str:
    output_dir = f"results/{run_id}/{spec.key}"
    artifact_dir = f"{output_dir}/artifacts"
    return f"""Use the CAD skill available in this workspace.

This is a one-shot benchmark. Generate independently from the prompt and CAD skill only.
Do not inspect, copy, adapt, or reuse sibling result directories for other models.
Your own target result directory, {output_dir}, is the only model output directory you may read or write.

Write the frozen benchmark source to:

    {output_dir}/model.py

The source file must follow the contract in the prompt below: it must define
build(output_dir) and must be runnable as `python model.py /path/to/output_dir`.

After writing {output_dir}/model.py, run it once if possible and write
generated CAD artifacts to:

    {artifact_dir}

Generate build123d Python source and a STEP file. Treat STEP as the primary CAD
artifact. Prefer assemblies made from separate labeled parts when the object has
functional relationships.

Validate the result with the CAD skill when possible:

    skills/cad/scripts/step
    skills/cad/scripts/inspect refs --facts --planes --positioning

Express physical behavior as CAD geometry, not simulation or FEA. Use separate
parts, pivot axes, slide axes, clearances, hard stops, motion ranges, and
ghost/reference positions where helpful. Prioritize visual readability over
excessive mechanical detail. For assemblies, make part labels, axes, and the
main mating datums understandable.

Generate snapshots if possible. If validation or snapshot generation fails
because of environment, browser, or sandbox limits, record the reason in a small
text or JSON file in {artifact_dir} and continue. Do not start CAD Viewer.

In the final response, report the source file, generated files, validation
results, and any failures or caveats.

Prompt contract:

{task_prompt}
"""


def toml_value(value: str) -> str:
    return json.dumps(value)


def provider_override(provider: ProviderSpec) -> str:
    fields = [
        f"name={toml_value(provider.name)}",
        f"base_url={toml_value(provider.base_url)}",
    ]
    if provider.env_key:
        fields.append(f"env_key={toml_value(provider.env_key)}")
    if provider.wire_api:
        fields.append(f"wire_api={toml_value(provider.wire_api)}")
    return f"model_providers.{provider.key}={{" + ",".join(fields) + "}"


def codex_command(
    spec: ModelSpec,
    providers: dict[str, ProviderSpec],
    workspace: Path,
    last_message: Path,
    prompt: str,
) -> list[str]:
    common = [
        "codex",
        "--disable",
        "image_generation",
    ]
    if spec.profile:
        common += ["-p", spec.profile]

    if spec.provider in providers:
        provider = providers[spec.provider]
        common += [
            "-c",
            provider_override(provider),
            "-c",
            f"model_provider={toml_value(provider.key)}",
        ]
    elif not spec.profile:
        raise ValueError(f"unknown provider without Codex profile: {spec.provider}")

    if spec.reasoning_effort:
        common += ["-c", f"model_reasoning_effort={toml_value(spec.reasoning_effort)}"]

    common += ["-m", spec.model]

    return common + [
        "--ask-for-approval",
        "never",
        "exec",
        "-C",
        str(workspace),
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "--output-last-message",
        str(last_message),
        prompt,
    ]


def run_one(
    spec: ModelSpec,
    *,
    args: argparse.Namespace,
    env: dict[str, str],
    providers: dict[str, ProviderSpec],
) -> int:
    workspace = (args.workspace_root / args.run_id / spec.key).resolve()
    result_dir = HERE / "results" / args.run_id / spec.key

    provider = providers.get(spec.provider)
    required_env_key = spec.env_key or (provider.env_key if provider else None)
    if required_env_key and not env.get(required_env_key) and not args.dry_run:
        emit(f"[{spec.key}] missing required environment variable: {required_env_key}", stream=sys.stderr)
        return 2
    if result_dir.exists() and not args.dry_run:
        emit(
            f"[{spec.key}] result already exists: {result_dir}. "
            "Choose a new --run-id; generation results are not overwritten.",
            stream=sys.stderr,
        )
        return 2

    task_prompt = args.prompt.read_text(encoding="utf-8")
    prompt = build_prompt(spec, task_prompt, run_id=args.run_id)
    workspace_result_dir = workspace / "results" / args.run_id / spec.key
    last_message = workspace_result_dir / "last_message.md"
    command = codex_command(spec, providers, workspace, last_message, prompt)

    if args.dry_run:
        emit(f"[{spec.key}] " + " ".join(shlex.quote(part) for part in command))
        return 0

    prepare_workspace(args.base_repo.resolve(), workspace, force=args.force_workspace)
    if workspace_result_dir.exists():
        shutil.rmtree(workspace_result_dir)
    workspace_result_dir.mkdir(parents=True, exist_ok=True)
    (workspace_result_dir / "prompt.txt").write_text(task_prompt, encoding="utf-8")
    (workspace_result_dir / "codex_prompt.txt").write_text(prompt, encoding="utf-8")

    stdout_log = workspace_result_dir / "stdout.log"
    proc_env = env.copy()
    venv_bin = workspace / ".venv/bin"
    if venv_bin.exists():
        proc_env["PATH"] = str(venv_bin) + os.pathsep + proc_env.get("PATH", "")
    emit(f"[{spec.key}] running {spec.provider}:{spec.model}")
    with stdout_log.open("w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            command,
            cwd=workspace,
            env=proc_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            log_file.write(line)
            emit(f"[{spec.key}] {line.rstrip()}")
        proc.wait()

    workspace_result = workspace_result_dir
    if not workspace_result.exists():
        emit(f"[{spec.key}] no workspace result found at {workspace_result}", stream=sys.stderr)
        return proc.returncode or 1

    result_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(workspace_result, result_dir)
    emit(f"[{spec.key}] copied frozen result to {result_dir}")
    return proc.returncode


def run_models(keys: list[str], *, config: BenchmarkConfig, args: argparse.Namespace, env: dict[str, str]) -> int:
    if args.parallel == 1:
        status = 0
        for key in keys:
            status = max(status, run_one(config.models[key], args=args, env=env, providers=config.providers))
        return status

    status = 0
    max_workers = min(args.parallel, len(keys))
    emit(f"running {len(keys)} model(s) with parallel={max_workers}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(run_one, config.models[key], args=args, env=env, providers=config.providers): key
            for key in keys
        }
        for future in concurrent.futures.as_completed(futures):
            key = futures[future]
            try:
                returncode = future.result()
            except Exception as exc:
                emit(f"[{key}] failed with exception: {exc}", stream=sys.stderr)
                returncode = 1
            status = max(status, returncode)
            emit(f"[{key}] finished with status {returncode}")
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Text-to-CAD benchmark tasks through Codex.")
    parser.add_argument("models", nargs="*", help="Model keys from models.toml. Defaults to default_models.")
    parser.add_argument("--base-repo", type=Path, default=default_base_repo())
    parser.add_argument("--prompt", type=Path, default=HERE / "prompt.txt", help="Task prompt file. Edit prompt.txt or pass another file.")
    parser.add_argument("--models-file", type=Path, default=HERE / "models.toml")
    parser.add_argument("--env-file", type=Path, default=HERE / ".env")
    parser.add_argument("--fugu-env-file", type=Path, default=Path.home() / ".config/fugu/env")
    parser.add_argument("--workspace-root", type=Path, default=None, help="Isolated Codex workspaces. Defaults to /tmp.")
    parser.add_argument("--run-id", default=None, help="Result run id. Defaults to a UTC timestamp with milliseconds.")
    parser.add_argument("--force-workspace", action="store_true", help="Recreate isolated workspaces.")
    parser.add_argument("--parallel", type=int, default=1, help="Run up to N different model keys concurrently.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.parallel < 1:
        parser.error("--parallel must be >= 1")

    env = os.environ.copy()
    env.update(parse_env_file(args.env_file))
    env.update(parse_env_file(args.fugu_env_file))
    if args.workspace_root is None:
        args.workspace_root = Path(env.get("TEXT_TO_CAD_WORKSPACE_ROOT") or default_workspace_root())
    if args.run_id is None:
        args.run_id = default_run_id()
    emit(f"run id: {args.run_id}")

    config = load_model_config(args.models_file)
    keys = args.models or config.default_models
    unknown = [key for key in keys if key not in config.models]
    if unknown:
        parser.error(f"unknown model key(s): {', '.join(unknown)}. Available: {', '.join(sorted(config.models))}")
    duplicate_keys = sorted({key for key in keys if keys.count(key) > 1})
    if duplicate_keys:
        parser.error(f"duplicate model keys are not supported in one run: {', '.join(duplicate_keys)}")

    return run_models(keys, config=config, args=args, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
