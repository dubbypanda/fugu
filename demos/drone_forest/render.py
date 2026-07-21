"""render.py — render a showcase clip of a controller on ONE frozen benchmark field.

Companion to grade_survival.py (which grades all 20 fields, numbers only). This renders a single
field so you can WATCH it, under the SAME survivability rule the grader uses and BY DEFAULT: a tree
impact over 200 N destroys the drone, which then cuts thrust and tumbles to the forest floor. So any
field you pick shows the real rule with no extra flags — Fugu-Ultra flies it clean, the baselines drop.
The field is loaded from the frozen benchmark (benchmark/fields.json) by id, matching the grade table.

Run in the `mujoco` env with EGL — just a field id and an arm:

    MUJOCO_GL=egl conda run --no-capture-output -n mujoco python render.py --id 19 --arm opus
    MUJOCO_GL=egl conda run --no-capture-output -n mujoco python render.py --id 6  --arm fugu_ultra

Writes videos/<name>_field<id>_<speed>x.mp4 (videos/ is gitignored — clips are local; the README uses
the GIF previews under each arm's artifacts/).
"""
import os
import sys
import argparse
import numpy as np
import imageio.v2 as imageio

HERE = os.path.dirname(os.path.abspath(__file__))
VID = os.path.join(HERE, "videos")     # showcase clips (mp4) land here; gitignored, README uses GIF previews
sys.path.insert(0, HERE)
import env as E                     # noqa: E402
from benchmark import load_field    # noqa: E402


def load_policy(path):
    with open(path) as f:
        code = f.read()
    ns = {"np": np, "numpy": np}
    exec(compile(code, path, "exec"), ns)
    if "policy" not in ns or not callable(ns["policy"]):
        raise ValueError(f"{path} has no callable policy(obs)")
    return ns["policy"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", type=int, required=True, help="benchmark field id to render (0-19)")
    ap.add_argument("--arm", choices=["fugu_ultra", "opus", "gpt", "gemini"],
                    help="render one of the demo arms (shorthand for its scripts/policy.py)")
    ap.add_argument("--policy", help="a controller file to render instead of an arm")
    ap.add_argument("--label", help="output filename prefix (default: the arm/model name)")
    ap.add_argument("--camera", default="follow", choices=["follow", "topdown"])
    ap.add_argument("--speed", type=float, default=2.0,
                    help="playback speed (2 = 2x, encodes at 30*speed fps). Adds a speed suffix to the filename.")
    ap.add_argument("--terminate-force", type=float, default=200.0,
                    help="a tree impact above this many N destroys the drone (it then falls) — the rule; leave as-is")
    args = ap.parse_args()

    if args.policy:
        policy_rel, name = args.policy, (args.label or "clip")
    else:
        arm = args.arm or "fugu_ultra"
        policy_rel, name = os.path.join(arm, "scripts", "policy.py"), (args.label or arm)

    fld = load_field(args.id, os.path.join(HERE, "benchmark", "fields.json"))
    policy = load_policy(os.path.join(HERE, policy_rel))
    r = E.run_episode(policy, goal=fld["goal"], obstacles=fld["obstacles"], start=fld["start"],
                      render=True, camera=args.camera, perception="depth",
                      destroy_force=args.terminate_force, destroy_fall=True,
                      label=f"{name}  (held-out field {args.id}, "
                            f"density {fld['density']}, {fld['n_trees']} trees)")
    frames = r.pop("_frames")
    suffix = "" if args.speed == 1.0 else f"_{args.speed:g}x"
    os.makedirs(VID, exist_ok=True)
    out = os.path.join(VID, f"{name}_field{args.id}{suffix}.mp4")
    if frames:
        imageio.mimsave(out, frames, fps=int(round(30 * args.speed)))
    sc = E.score(r)
    print(f"\n== field {args.id} (origin_seed {fld['origin_seed']}, density {fld['density']}, "
          f"{fld['n_trees']} trees) ==")
    for k, v in r.items():
        if k != "_frames":
            print(f"  {k:18s}: {v}")
    print(f"  {'score':18s}: {sc}")
    if frames:
        print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
