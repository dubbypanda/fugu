"""run_dev.py — the iteration feedback harness (the only signal you tune against).

Flies your policy.py headless on three DEV practice forests (seeds 0, 2, 3) and prints, per seed,
whether it REACHED, how far it got, bumps, time, and score + the MEAN. Edit policy.py, re-run, iterate.

    conda run --no-capture-output -n mujoco python run_dev.py

These three practice forests are generated on the fly (F.make_forest); they are NOT the graded set.
Your final policy is graded by `grade_survival.py` on the 20 FROZEN forests in `benchmark/fields.json`,
which are disjoint from these three practice seeds. So only a GENERAL reactive controller helps:
overfitting these three layouts (or reading the frozen fields) will fail the grade. When the baselines
in this demo were produced, the agent ran in an isolated copy of this folder that did NOT contain
`benchmark/` at all (see the README's integrity section), so it could not peek even in principle.
"""
import os
import sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import env as E       # noqa: E402  (simulator — copied into the box)
import field as F     # noqa: E402  (forest generator — copied into the box)

# Three fixed practice forests. Densities span the difficulty band; each is A*-feasible (a real
# collision-free route exists). These are the ONLY forests you may iterate on. NB: this file does NOT
# import build_benchmark and knows nothing about which forests are held out — that stays outside the box.
DEV = [(0, 1.15), (2, 1.20), (3, 1.30)]
POLICY_PATH = os.path.join(HERE, "policy.py")
ITER_FILE = os.path.join(HERE, ".iteration")   # simple per-box round counter (this run only)


def bump_iteration():
    """Increment and return how many times run_dev has been run in this box = the iteration number."""
    try:
        n = int(open(ITER_FILE).read().strip())
    except Exception:
        n = 0
    n += 1
    try:
        with open(ITER_FILE, "w") as f:
            f.write(str(n))
    except Exception:
        pass
    return n


def load_policy():
    """Load policy.py fresh (resets any module-level state), the same way the grader loads it."""
    with open(POLICY_PATH) as f:
        code = f.read()
    ns = {"np": np, "numpy": np}
    exec(compile(code, POLICY_PATH, "exec"), ns)
    if "policy" not in ns or not callable(ns["policy"]):
        raise ValueError("policy.py must define a callable policy(obs)")
    return ns["policy"]


def make_dev(seed, density):
    """Build one A*-feasible practice forest (fall back to nearby densities if a route degenerates)."""
    for d in [density, 1.20, 1.15, 1.25, 1.30]:
        fld = F.make_forest(seed=seed, density=d)
        if len(fld["route"]) > 2:      # A* found a real weaving route -> feasible
            return d, fld
    return density, F.make_forest(seed=seed, density=density)


def _progress(r):
    sl = r["straight_line"] or 1.0
    return max(0.0, min(1.0, 1.0 - r["min_distance"] / sl))


def main():
    it = bump_iteration()
    print(f"================  ITERATION {it}  ================")
    scores = []
    for seed, dens in DEV:
        d, fld = make_dev(seed, dens)
        policy = load_policy()                       # fresh per seed -> no state carryover
        r = E.run_episode(policy, goal=fld["goal"], obstacles=fld["obstacles"],
                          start=fld["start"], render=False, perception="depth")
        sc = E.score(r)
        scores.append(sc)
        status = "REACHED" if r["success"] else "FAIL   "
        end = r["crash_reason"] or ("goal" if r["reached_goal"] else "timeout")
        print(f"seed {seed:>2} d={d:>4} | score {sc:6.2f} | {status} | end={str(end):>8} | "
              f"reached={_progress(r) * 100:5.1f}% | min_dist={r['min_distance']:6.2f}m | "
              f"bumps={r['collisions']:>2} | t={r['sim_time']:5.1f}s"
              + (f" | ERR {r['error']}" if r['error'] else ""))
    print(f"\nMEAN score over {len(scores)} dev seeds: {sum(scores) / len(scores):.2f}")
    print("Reach every practice seed cleanly; your real grade is on 20 held-out forests you never see here.")


if __name__ == "__main__":
    main()
