# Task: write a controller for an autonomous forest drone

You are given a **quadrotor drone**. It must fly through a dense **palm forest** from the
start to a goal while **staying in control** (do not tumble or hit the ground).

The drone is **not** self-stabilizing: all four rotors push along the drone's own "up"
axis, so gravity will tip it over and drop it if you do nothing. **Your controller must
keep the drone upright AND navigate at the same time.** To move horizontally you must
*tilt* the drone so some thrust points the way you want to go, then level back out.

The forest is a field of palm trunks spread across the whole corridor, staggered so there
is **no straight way through** — you must weave left and right between the trunks. The
corridor has solid side walls and a low ceiling (the trunks run all the way up to it), so
you cannot fly around the sides or over the top; the only way is through.

Write a Python function `policy(obs) -> action`, called 100 times per simulated second.
It returns the 4 rotor commands for that instant. The simulation runs your function
unchanged for the whole episode. **Output only the controller — no training.**

## Observation (`obs`, a dict)
- `pos` `[x, y, z]` — drone position (m). Forward down the corridor is +x; up is +z.
- `vel` `[vx, vy, vz]` — linear velocity (world frame).
- `quat` `[w, x, y, z]` — the drone's orientation.
- `forward` `[3]`, `up` `[3]` — the drone's body axes in world coordinates.
- `angvel` `[3]` — angular velocity (roll/pitch/yaw rates). **Use this to stay upright.**
- `goal` `[3]`, `to_goal` `[3]` (= goal − pos), `distance` — the goal and its range.
- `heading_error` — signed yaw error to the goal (radians, **+ = goal is to your left**).
- `height_error` — `goal_z − pos_z` (**+ = goal is above you**).
- `battery` — always `1.0`. Battery is **not** a constraint in this task (a real drone has minutes
  of endurance for a flight that lasts seconds), so you never need to conserve energy; fly the way
  that reaches cleanly and quickly.
- `time` — elapsed simulated seconds.
- `depth` — a forward-looking range fan: a list of beams, each
  `{"az": deg, "el": deg, "dist": m}`. `az` is the horizontal angle (**+ = left**), `el`
  the vertical angle (**+ = up**), `dist` the distance to the nearest obstacle along that
  beam, capped at `depth_range` if nothing is hit. This is your ONLY view of the forest —
  there is no map. Use it to sense the trunks and weave between them.
  **LIMITED FIELD OF VIEW — the fan only looks FORWARD: `az` spans about −60°…+60° and `el`
  about −40°…+40°. You are BLIND behind you and to either side past ±60° — anything there
  simply does not appear in `depth`. You cannot see a trunk you have already passed, nor one
  directly beside or behind you, so backing up or sliding sideways moves you into space you
  cannot sense. The fan also refreshes at ~25 Hz — slower than your 100 Hz control — so each
  reading is held for a few control steps before it updates.**
- `depth_range` — the cap (m).

## Action (`action`): 4 floats — the rotor thrusts `[thrust1, thrust2, thrust3, thrust4]`, each in [0, 13]
**Hover ≈ 3.25 on every rotor** cancels gravity; max is 13. Each rotor pushes along the
drone's **+z (up)** axis; the differences between them create the torques that tilt you.

**The rotor layout is FIXED and given — use it exactly (body axes: +x forward, +y left, +z up):**

| action index | rotor position (x, y) | corner | spin (yaw reaction) |
|---|---|---|---|
| `thrust1` | (−0.14, −0.18) | rear-right  | − |
| `thrust2` | (−0.14, +0.18) | rear-left   | + |
| `thrust3` | (+0.14, +0.18) | front-left  | − |
| `thrust4` | (+0.14, −0.18) | front-right | + |

Pick four scalars — collective `C` (throttle, start near hover ≈ 3.25), and body torques
`roll`, `pitch`, `yaw` — then mix them to the four rotors with these EXACT signs (derived
from the layout above; `+roll` = torque about +x, `+pitch` = about +y, `+yaw` = about +z):

```
thrust1 = C - roll + pitch - yaw     # rear-right
thrust2 = C + roll + pitch + yaw     # rear-left
thrust3 = C + roll - pitch - yaw     # front-left
thrust4 = C - roll - pitch + yaw     # front-right
return [clip(thrust1, 0, 13), clip(thrust2, 0, 13), clip(thrust3, 0, 13), clip(thrust4, 0, 13)]
```

You still have to write the CONTROLLER — an attitude/stabilization loop that computes `C`,
`roll`, `pitch`, `yaw` each step from the drone's orientation (`quat`/`up`) and rates
(`angvel`) to stay upright, plus an outer loop that tilts toward the goal and away from
trunks. Set all four rotors equal and you cannot steer; get the stabilization wrong and the
drone tips over and falls. (Tune the sign of your outer loop to the mixing above.)

## Rules / scoring
- **Reach the goal** (within 2 m). Reaching, while staying in control, is the objective.
- **Avoid the trunks and walls, but bumping them is survivable.** A bump does **not** end the run
  and there is **no limit** on how many bumps you may take. Each bump **costs score**, so a clean
  flight ranks higher, and a bump can destabilize you, so recovering your attitude afterward
  matters. What actually ends the run is **losing control**: if you tumble or tip over, you fail.
- **Touching the GROUND is an INSTANT crash** — the run ends immediately as a failure. Never let
  the drone sink to the floor; the moment it hits the ground the run is over. Staying in the air and
  in control is the one hard requirement. (There is a generous time limit only to stop a drone that
  never makes progress; a real flight finishes long before it.)
- Among reachers, **reaching sooner and with fewer bumps ranks higher** — so don't dawdle, and weave
  cleanly. If you fail, flying **farther** before losing control still ranks above dropping early.

## Output format
Return a single Python code block defining `policy(obs)`. You may keep state in module-level
variables for memory across calls. Use only the standard library plus `numpy` (as `np`). Do
not read files or the network.
