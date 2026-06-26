#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_MODELS = [
    ("fugu_ultra", "fugu-ultra"),
    ("fugu", "fugu"),
    ("gpt55", "openai/gpt-5.5"),
    ("opus48", "anthropic/claude-opus-4.8"),
    ("gemini", "google/gemini-3.1-pro-preview"),
]
COLORS = {
    "fugu_ultra": (28, 185, 132),
    "fugu": (44, 144, 255),
    "gpt55": (235, 161, 44),
    "opus48": (235, 84, 112),
    "gemini": (139, 92, 246),
}
MODEL_NAMES = dict(DEFAULT_MODELS)


def default_base_repo() -> Path:
    return HERE.resolve()


def default_python(base_repo: Path) -> Path:
    candidate = base_repo / ".venv/bin/python"
    return candidate if candidate.exists() else Path(sys.executable)


def latest_run_dir() -> Path:
    runs = sorted(path for path in (HERE / "runs").glob("*") if path.is_dir())
    if not runs:
        raise SystemExit("No runs found. Run rerun_saved.py first or pass --run-dir.")
    return runs[-1]


def find_font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    from PIL import ImageFont

    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def find_step(model_dir: Path) -> Path | None:
    artifacts = model_dir / "artifacts"
    matches = sorted(
        path
        for path in artifacts.rglob("*")
        if path.is_file() and path.suffix.lower() in {".step", ".stp"}
    )
    return matches[0] if matches else None


def extract_saved_path(stdout: str) -> Path | None:
    for line in stdout.splitlines():
        match = re.search(r"saved snapshot:\s+(.+)$", line.strip())
        if match:
            return Path(match.group(1))
    return None


def render_snapshot(step_path: Path, gif_path: Path, *, base_repo: Path, python: Path, args: argparse.Namespace) -> None:
    if gif_path.exists() and not args.force_snapshots:
        return
    gif_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(args.playwright_browsers_path)
    command = [
        str(python),
        str(base_repo / "skills/cad/scripts/snapshot"),
        "--input",
        str(step_path),
        "--mode",
        "orbit",
        "--output",
        str(gif_path),
        "--width",
        str(args.snapshot_width),
        "--height",
        str(args.snapshot_height),
    ]
    completed = subprocess.run(
        command,
        cwd=base_repo,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )
    saved = extract_saved_path(completed.stdout)
    if saved and saved != gif_path and saved.exists():
        saved.replace(gif_path)


def load_frames(path: Path, frame_count: int, size: tuple[int, int]) -> list[Image.Image]:
    from PIL import Image, ImageSequence

    with Image.open(path) as image:
        frames = [frame.convert("RGB") for frame in ImageSequence.Iterator(image)]
    if not frames:
        raise RuntimeError(f"No frames in {path}")
    sampled = [frames[round(index * (len(frames) - 1) / max(frame_count - 1, 1))] for index in range(frame_count)]
    return [frame.resize(size, Image.Resampling.LANCZOS) for frame in sampled]


def grid_layout(keys: list[str]) -> tuple[int, int, dict[str, tuple[int, int, int]]]:
    count = len(keys)
    if count <= 3:
        cols, rows = count, 1
    elif count == 4:
        cols, rows = 2, 2
    else:
        cols, rows = 3, (count + 2) // 3
    layout = {key: (index % cols, index // cols, cols) for index, key in enumerate(keys)}
    return cols, rows, layout


def display_run_label(run_dir: Path) -> str:
    try:
        return str(run_dir.relative_to(HERE))
    except ValueError:
        return run_dir.name


def compose_compare(
    run_dir: Path,
    snapshots: dict[str, Path],
    output_path: Path,
    *,
    frame_count: int,
    models: list[tuple[str, str]],
) -> None:
    from PIL import Image, ImageDraw

    panel_w, panel_h = 640, 430
    label_h = 46
    model_keys = [key for key, _name in models]
    cols, rows, layout = grid_layout(model_keys)
    width = panel_w * cols
    height = (panel_h + label_h) * rows
    title_font = find_font(24, bold=True)
    label_font = find_font(20, bold=True)
    small_font = find_font(14)
    run_label = display_run_label(run_dir)

    frames_by_model = {
        key: load_frames(path, frame_count, (panel_w, panel_h))
        for key, path in snapshots.items()
        if path.exists()
    }

    output_frames: list[Image.Image] = []
    names = dict(models)

    for index in range(frame_count):
        canvas = Image.new("RGB", (width, height), (14, 17, 22))
        draw = ImageDraw.Draw(canvas, "RGBA")
        for key, _name in models:
            col, row, cols = layout[key]
            cell_w = width // cols
            x = col * cell_w
            y = row * (panel_h + label_h)
            draw.rectangle((x, y, x + cell_w, y + label_h), fill=(31, 36, 46, 255))
            color = COLORS[key]
            draw.rounded_rectangle((x + 18, y + 13, x + 34, y + 29), radius=4, fill=color + (255,))
            draw.text((x + 46, y + 9), names[key], font=label_font, fill=(242, 246, 252, 255))
            if key in frames_by_model:
                frame = frames_by_model[key][index]
                px = x + (cell_w - frame.width) // 2
                canvas.paste(frame, (px, y + label_h))
            else:
                draw.text((x + 30, y + label_h + panel_h // 2), "missing STEP/render", font=title_font, fill=(100, 111, 128, 255))
            draw.rectangle((x, y, x + cell_w - 1, y + label_h + panel_h - 1), outline=(68, 78, 96, 255), width=1)
        draw.text((18, height - 24), run_label, font=small_font, fill=(120, 132, 150, 255))
        output_frames.append(canvas)

    output_frames[0].save(
        output_path,
        save_all=True,
        append_images=output_frames[1:],
        duration=1000 // 12,
        loop=0,
        optimize=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Render and compose a side-by-side mechanical iris comparison GIF.")
    parser.add_argument("models", nargs="*", help="Model keys to include. Defaults to all known models.")
    parser.add_argument("--base-repo", type=Path, default=default_base_repo())
    parser.add_argument("--python", type=Path, default=None)
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument("--playwright-browsers-path", type=Path, default=Path("/tmp/fugu-ms-playwright"))
    parser.add_argument("--snapshot-width", type=int, default=720)
    parser.add_argument("--snapshot-height", type=int, default=460)
    parser.add_argument("--frames", type=int, default=72)
    parser.add_argument("--force-snapshots", action="store_true")
    args = parser.parse_args()

    base_repo = args.base_repo.resolve()
    python = args.python or default_python(base_repo)
    run_dir = args.run_dir.resolve() if args.run_dir else latest_run_dir()
    snapshot_dir = run_dir / "_snapshots"
    snapshots: dict[str, Path] = {}
    model_keys = args.models or [key for key, _name in DEFAULT_MODELS]
    unknown = [key for key in model_keys if key not in MODEL_NAMES]
    if unknown:
        raise SystemExit(f"Unknown model key(s): {', '.join(unknown)}")
    models = [(key, MODEL_NAMES[key]) for key in model_keys]

    for key, _name in models:
        step = find_step(run_dir / key)
        if step is None:
            continue
        gif_path = snapshot_dir / f"{key}.gif"
        print(f"[{key}] snapshot {step}", flush=True)
        render_snapshot(step, gif_path, base_repo=base_repo, python=python, args=args)
        snapshots[key] = gif_path

    if not snapshots:
        raise SystemExit(f"No STEP files found in {run_dir}")

    output_path = run_dir / "compare.gif"
    compose_compare(run_dir, snapshots, output_path, frame_count=args.frames, models=models)
    (run_dir / "render_summary.json").write_text(
        json.dumps({"run_dir": str(run_dir), "snapshots": {k: str(v) for k, v in snapshots.items()}, "compare": str(output_path)}, indent=2),
        encoding="utf-8",
    )
    print(f"comparison: {output_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
