"""policy.py — the starting point every model iterates from (an identical hover stub).

This is the working file: read `task.md`, then edit this into a real reactive controller, checking
your progress with `run_dev.py`. Every model in this demo began from a byte-for-byte copy of this
stub, so none gets a head start.

The stub only hovers (equal thrust on all four rotors -> no torque -> no steering). It does NOT
complete the task; it is a valid, upright starting point. See `task.md` for the full spec.
"""
import numpy as np

HOVER = 3.25   # per-rotor thrust that cancels gravity (see task.md)


def policy(obs):
    # Hover: all four rotors equal -> no torque, no steering. Replace with a real reactive
    # controller that stabilizes attitude AND weaves through the forest.
    return [HOVER, HOVER, HOVER, HOVER]
