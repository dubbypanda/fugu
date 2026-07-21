"""benchmark.py — load the FROZEN benchmark fields (built by build_benchmark.py).

Every grader (grade_survival.py, render.py) loads fields from here instead of
calling field.make_forest, so all models are graded on identical, reproducible geometry.

    from benchmark import load_fields
    for fld in load_fields():          # each fld has: id, origin_seed, obstacles, start, goal
        run_episode(policy, goal=fld["goal"], obstacles=fld["obstacles"], start=fld["start"])
"""
import os
import json

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT = os.path.join(HERE, "benchmark", "fields.json")


def load_fields(path=DEFAULT):
    """Return the list of frozen field dicts (id, origin_seed, n_trees, start, goal, obstacles)."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found — build it first:\n"
            f"    conda run --no-capture-output -n mujoco python build_benchmark.py --n 20 --start-seed 5")
    with open(path) as f:
        payload = json.load(f)
    fields = payload["fields"]
    # tuples read back as lists from JSON; run_episode accepts either, but normalise start/goal.
    for fld in fields:
        fld["start"] = tuple(fld["start"])
        fld["goal"] = tuple(fld["goal"])
    return fields


def load_field(field_id, path=DEFAULT):
    """Return a single frozen field by its benchmark id."""
    for fld in load_fields(path):
        if fld["id"] == field_id:
            return fld
    raise KeyError(f"field id {field_id} not in {path}")
