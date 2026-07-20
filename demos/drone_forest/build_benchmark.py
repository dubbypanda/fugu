"""build_benchmark.py — freeze a FIXED, DIFFICULTY-SPANNING set of forest fields into a benchmark.

Why freeze: `field.make_forest(seed)` is procedural, so the field a seed produces can drift if
`field.py` params ever change. A public benchmark must be reproducible and identical for EVERY
model we grade later, so we generate the fields ONCE, screen them for fairness, and serialise the
raw geometry (obstacles / start / goal / A* route) to `benchmark/fields.json`.

Why a RANGE of densities: a benchmark where every field has the same tree count only tests ONE
difficulty 20 times — the pass rate says nothing about robustness. So we sweep `density` tiers
(sparse -> dense) and keep several fields per tier, so "reached N of M" is a real generalisation
number across difficulty, not one difficulty repeated.

Screening (a field is KEPT only if BOTH hold, same tests as verify_forest.py):
  * FEASIBLE      — A* finds a real weaving route (route has > 2 waypoints, not the straight fallback)
  * CHEAT-BLOCKED — no clear straight y-lane crosses the field (best lane gap < CLEAR_NEEDED)
Degenerate candidates (infeasible when too dense, or a straight cheat lane open when too sparse) are
SKIPPED and logged — never silently dropped — so the kept set is all fair, hard, feasible fields.

    conda run --no-capture-output -n mujoco python build_benchmark.py

Writes benchmark/fields.json. Re-running overwrites it (the benchmark is meant to be stable — commit
it once and don't regenerate unless you intend to change the benchmark).
"""
import os
import sys
import json
import argparse
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import field as F     # noqa: E402

DEV_SEEDS = {0, 2, 3}                 # seeds the agent iterated on — never freeze these
CLEAR_NEEDED = F.TRUNK_R + 0.31       # straight-lane clearance a drone needs (verify_forest.py)
# difficulty tiers: (density multiplier, how many fields to keep at that density).
# Start at 1.15 and go up (below 1.15 the controller cleared it too easily), stepping finely through
# the hard band so the benchmark discriminates within it. 1.3 is the fair ceiling (denser turns
# degenerate: a cheat lane opens or A* goes infeasible) -- the screener skips any that fall out.
DEFAULT_TIERS = [(1.15, 5), (1.2, 5), (1.25, 5), (1.3, 5)]
MAX_TRIES_PER_TIER = 200              # safety cap so a hard tier can't loop forever


def trunk_xy(o):
    return (o["p0"][0], o["p0"][1]) if "p0" in o else (o["c"][0], o["c"][1])


def best_straight_lane(trunks_xy):
    """Max over candidate y-lanes of the min y-gap to any trunk. < CLEAR_NEEDED => cheat blocked."""
    if not trunks_xy:
        return 999.0
    tys = np.array([ty for (_, ty) in trunks_xy])
    lanes = np.linspace(-F.BAND_Y + 0.4, F.BAND_Y - 0.4, 400)
    gaps = np.abs(tys[None, :] - lanes[:, None]).min(axis=1)
    return float(gaps.max())


def screen(fld):
    """Return (ok, n_trees, best_lane, feasible, blocked) for one field."""
    trunks = [trunk_xy(o) for o in fld["obstacles"] if o.get("type") == "capsule"]
    feasible = len(fld["route"]) > 2
    best = best_straight_lane(trunks)
    blocked = best < CLEAR_NEEDED
    return (feasible and blocked), len(trunks), best, feasible, blocked


def jsonable(x):
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if isinstance(x, dict):
        return {k: jsonable(v) for k, v in x.items()}
    return x


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-seed", type=int, default=5, help="first candidate seed to try")
    ap.add_argument("--out", default=os.path.join("benchmark", "fields.json"))
    args = ap.parse_args()

    kept = []
    seed = args.start_seed
    print("building a difficulty-spanning benchmark (FEASIBLE + CHEAT-BLOCKED per field),\n"
          f"skipping dev seeds {sorted(DEV_SEEDS)}\n")
    for density, want in DEFAULT_TIERS:
        got, tries = 0, 0
        print(f"--- density {density:>4} (target {want} fields) ---")
        while got < want and tries < MAX_TRIES_PER_TIER:
            tries += 1
            if seed in DEV_SEEDS:
                print(f"seed {seed:>3}: SKIP (dev seed)")
                seed += 1
                continue
            fld = F.make_forest(seed=seed, density=density)
            ok, n_trees, best, feasible, blocked = screen(fld)
            tag = "KEEP" if ok else ("SKIP infeasible" if not feasible else "SKIP cheat-open")
            print(f"seed {seed:>3}: d={density:>4} trees={n_trees:3d} | A*={'ok' if feasible else 'FALLBACK'} | "
                  f"best-lane={best:.2f} (<{CLEAR_NEEDED:.2f}? {'yes' if blocked else 'NO'}) -> {tag}")
            if ok:
                kept.append({
                    "id": len(kept),
                    "origin_seed": seed,
                    "density": density,
                    "n_trees": n_trees,
                    "best_straight_lane": round(best, 3),
                    "start": jsonable(fld["start"]),
                    "goal": jsonable(fld["goal"]),
                    "obstacles": jsonable(fld["obstacles"]),
                    # the A* waypoint path is the PROOF a collision-free route exists (feasibility).
                    "route": jsonable(fld["route"]),
                })
                got += 1
            seed += 1
        if got < want:
            print(f"WARNING: only kept {got}/{want} at density {density} within "
                  f"{MAX_TRIES_PER_TIER} tries")

    os.makedirs(os.path.join(HERE, os.path.dirname(args.out)), exist_ok=True)
    out = os.path.join(HERE, args.out)
    counts = [k["n_trees"] for k in kept]
    payload = {
        "meta": {
            "n_fields": len(kept),
            "tiers": [{"density": d, "target": w} for d, w in DEFAULT_TIERS],
            "start_seed": args.start_seed,
            "last_seed_tried": seed - 1,
            "dev_seeds_excluded": sorted(DEV_SEEDS),
            "clear_needed": round(CLEAR_NEEDED, 3),
            "n_trees_min": min(counts) if counts else 0,
            "n_trees_max": max(counts) if counts else 0,
            "screen": "FEASIBLE (A* route > 2 pts) AND CHEAT-BLOCKED (best straight lane < clear_needed)",
            "note": "frozen fields spanning a density range — grade every model on THESE; "
                    "do not regenerate unless changing the benchmark",
        },
        "fields": kept,
    }
    with open(out, "w") as f:
        json.dump(payload, f)
    print(f"\nkept {len(kept)} fields | trees range {min(counts)}..{max(counts)} "
          f"| densities {sorted(set(k['density'] for k in kept))}")
    print(f"wrote {out}  ({os.path.getsize(out) // 1024} KB)")


if __name__ == "__main__":
    main()
