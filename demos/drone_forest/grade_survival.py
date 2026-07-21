"""grade_survival.py — grade a forest-drone controller on the frozen benchmark, under the
survivability rule.

The rule (the one a real drone lives by): a tree impact whose peak force exceeds `--terminate-force`
newtons DESTROYS the drone and ends the run there as a failure. This is `env.run_episode`'s built-in
`destroy_force` — the SAME code path the showcase clips (`render.py`) use, so the grade and the GIFs
can never disagree. A controller "reaches intact" only if it gets to the goal without ever taking a
lethal hit; otherwise it is scored on how far it got before it was destroyed (or lost control).

    # grade the four demo arms (fugu_ultra / opus / gpt / gemini) -> leaderboard + refresh metrics.json
    conda run --no-capture-output -n mujoco python grade_survival.py

    # quick sanity check (one forest, ~seconds)
    conda run --no-capture-output -n mujoco python grade_survival.py --smoke

    # grade a NEW controller you wrote against task.md
    conda run --no-capture-output -n mujoco python grade_survival.py --policy path/to/policy.py --label mymodel

Runs episodes in parallel (--workers); the full 4-arm x 20-forest sweep is a few CPU-minutes, so for a
long run use tmux/nohup so closing the terminal does not kill it.
"""
import os
import sys
import json
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import env as E                    # noqa: E402
from benchmark import load_fields  # noqa: E402

# The four arms shipped with this demo: display name -> folder.
ARMS = [
    ("Fugu-Ultra",     "fugu_ultra"),
    ("Opus 4.8",       "opus"),
    ("GPT-5.5",        "gpt"),
    ("Gemini 3.1 Pro", "gemini"),
]
PROTO = {
    "fugu_ultra": "agentic; ran first, verified no held-out access",
    "opus":       "agentic; isolated anti-cheat box",
    "gpt":        "agentic; isolated anti-cheat box",
    "gemini":     "agentic; isolated anti-cheat box",
}


def _mean(xs):
    return round(sum(xs) / len(xs), 2) if xs else 0.0


def load_policy(path):
    import numpy as np
    with open(path) as f:
        code = f.read()
    ns = {"np": np, "numpy": np}
    exec(compile(code, path, "exec"), ns)
    if "policy" not in ns or not callable(ns["policy"]):
        raise ValueError(f"{path} has no callable policy(obs)")
    return ns["policy"]


def grade_one(policy_path, fld, terminate_force):
    """One episode via the SAME run_episode + destroy_force path the GIFs use.

    run_episode ends the run at the first lethal hit, so E.score() already yields the survival score
    (a destroyed/failed run is scored on progress-to-the-death-point; an intact reach on speed+cleanliness).
    """
    policy = load_policy(policy_path)     # fresh per field -> no state carryover
    r = E.run_episode(policy, goal=fld["goal"], obstacles=fld["obstacles"], start=fld["start"],
                      render=False, perception="depth", destroy_force=terminate_force)
    if r.get("error"):
        return {"id": fld["id"], "error": r["error"]}
    return {
        "id": fld["id"],
        "reached": bool(r["reached_goal"]),           # reaches intact (destroyed runs end before the goal)
        "collisions": int(r["collisions"]),
        "peak_force": float(r["peak_hit_force"]),
        "destroyed": bool(r["crash_reason"] == "destroyed"),
        "score": float(E.score(r)),
    }


def _worker(task):
    """Top-level (picklable) worker: grade one (model, field)."""
    name, policy_path, fld, T = task
    return name, grade_one(policy_path, fld, T)


def write_metrics(name, folder, rows, T):
    rows = sorted(rows, key=lambda r: r["id"])
    n = len(rows)
    out = {
        "model": name,
        "protocol": PROTO.get(folder, "agentic"),
        "survivability_rule_N": T,
        "reaches_intact": f"{sum(1 for r in rows if r['reached'])}/{n}",
        "mean_score": _mean([r["score"] for r in rows]),
        "max_impact_N": round(max((r["peak_force"] for r in rows), default=0.0), 1),
        "mean_collisions": _mean([r["collisions"] for r in rows]),
        "per_field": [{"id": r["id"], "reached": r["reached"], "collisions": r["collisions"],
                       "peak_force_N": round(r["peak_force"], 1), "destroyed": r["destroyed"],
                       "score": round(r["score"], 2)} for r in rows],
    }
    path = os.path.join(HERE, folder, "artifacts", "metrics.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    return path


def main():
    import multiprocessing as mp
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", help="grade a single controller file (default: the four demo arms)")
    ap.add_argument("--label", default="model", help="display name for --policy")
    ap.add_argument("--terminate-force", type=float, default=200.0,
                    help="a tree impact above this many N destroys the drone (survivability rule)")
    ap.add_argument("--ids", nargs="+", type=int, help="grade only these field ids (default: all 20)")
    ap.add_argument("--smoke", action="store_true", help="one field only — a quick ~seconds sanity check")
    ap.add_argument("--no-write", action="store_true", help="do not refresh the arms' metrics.json")
    ap.add_argument("--workers", type=int, default=max(1, min((os.cpu_count() or 4) - 2, 12)))
    args = ap.parse_args()

    fields = load_fields()
    if args.ids:
        fields = [f for f in fields if f["id"] in args.ids]
    if args.smoke:
        fields = fields[:1]
    T = args.terminate_force

    if args.policy:
        path = args.policy if os.path.isabs(args.policy) else os.path.join(HERE, args.policy)
        targets = [(args.label, path, None)]        # (name, policy_path, folder-or-None)
    else:
        targets = [(nm, os.path.join(HERE, fo, "scripts", "policy.py"), fo) for nm, fo in ARMS]

    tasks = [(name, path, fld, T) for name, path, _ in targets for fld in fields]
    nproc = max(1, min(args.workers, len(tasks)))
    print(f"benchmark: {len(fields)} forest(s) x {len(targets)} model(s) = {len(tasks)} episodes "
          f"| destroyed > {T:.0f} N | {nproc} workers\n", flush=True)

    results = {name: [] for name, _, _ in targets}
    done = 0
    with mp.Pool(processes=nproc) as pool:
        for name, r in pool.imap_unordered(_worker, tasks):
            done += 1
            if r.get("error"):
                print(f"  [{done:>3}/{len(tasks)}] {name}: {r['error']}", flush=True)
                continue
            results[name].append(r)
            print(f"  [{done:>3}/{len(tasks)}] {name:>14} f{r['id']:>2}: "
                  f"reach={str(r['reached']):>5} bumps={r['collisions']:>3} "
                  f"peakF={r['peak_force']:>4.0f}N destroyed={str(r['destroyed']):>5} "
                  f"score={r['score']:>6.2f}", flush=True)

    if not args.policy and not args.no_write and not args.smoke and not args.ids:
        print()
        for name, _, folder in targets:
            p = write_metrics(name, folder, results[name], T)
            print(f"  wrote {os.path.relpath(p, HERE)}")

    print(f"\n=== LEADERBOARD  (a tree impact > {T:.0f} N destroys the drone) ===")
    print(f"{'model':>16} | {'reaches intact':>14} | {'mean score':>10} | {'max impact':>10}")
    print("-" * 60)
    order = sorted(results.items(), key=lambda kv: -_mean([r["score"] for r in kv[1]]) if kv[1] else 0)
    for name, rows in order:
        if not rows:
            continue
        n = len(rows)
        reach = sum(1 for r in rows if r["reached"])
        print(f"{name:>16} | {str(reach)+'/'+str(n):>14} | {_mean([r['score'] for r in rows]):>10.2f} | "
              f"{max(r['peak_force'] for r in rows):>7.0f} N")


if __name__ == "__main__":
    main()
