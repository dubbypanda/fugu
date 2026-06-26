from pathlib import Path

import matplotlib.animation as animation


def save_mp4(env, states, path: str | Path, interval: int = 200) -> None:
    output = Path(path)
    if output.suffix.lower() != ".mp4":
        raise SystemExit("--output must be an .mp4 file")
    if not animation.writers.is_available("ffmpeg"):
        raise SystemExit("Saving MP4 requires ffmpeg. Install it with `brew install ffmpeg`.")
    output.parent.mkdir(parents=True, exist_ok=True)
    env.animate(list(states), interval=interval, save_path=str(output))
