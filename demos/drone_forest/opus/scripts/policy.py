"""policy.py — reactive forest-drone controller.

Cascade design (see task.md), from inner to outer:
  * attitude PD keeps the drone upright (body-up tilt via roll/pitch torque);
  * PRIORITY MIXING gives attitude torque precedence over collective thrust, so a big recovery
    torque is actually delivered instead of being lost to rotor saturation (the thing that
    otherwise turns a bump into an unrecoverable tumble);
  * a tilt-authority gate fades navigation out and levels the drone when it tips over (recovery);
  * collective thrust holds the goal altitude;
  * yaw holds forward toward the goal and damps the body yaw rate (weak actuator, ~9x weaker than
    roll/pitch, so it is only lightly relied on);
  * outer navigation is an ADAPTIVE widest-corridor gap follower: from the live depth fan it finds
    the heading whose clear "corridor" (a drone-width tube) is longest, preferring a wide corridor
    (well-centred, clean) and falling back to a narrow one in tight spots so it never stalls; it
    moves along that heading at a reaction-time-limited speed, with a trap-escape for dead ends.

Strictly reactive: everything comes from `obs` (depth fan, IMU/state, goal bearing). No map,
no waypoint table, no seed branching. Std lib + numpy only.
"""
import numpy as np

HOVER = 3.25
G = 9.81

# --- outer loop (navigation) ---
VX = 2.2           # forward cruise speed (m/s) cap
T_REACT = 1.05     # never travel faster than (clearance ahead / T_REACT): reaction-time speed limit
KV = 1.4           # accel per velocity-error (outer P)
A_CAP = 3.8        # cap on |desired horizontal accel| (m/s^2)
SPEED_FLOOR = 0.10 # floor speed (fraction of VX) so it always creeps + can steer when boxed in
CORRIDOR_WIDE = 0.75  # preferred corridor half-width: flies well-centred between trunks (clean)
CORRIDOR_TIGHT = 0.42 # fallback half-width for tight spots so the drone threads instead of stalling
WIDE_OK = 3.2         # if the widest corridor is clear at least this far, take it; else thread tight
# --- trap escape ---
STUCK_V = 0.16     # low-passed forward speed below which the drone is deemed trapped
ESCAPE_V = 1.3     # lateral sweep speed used to break out of a trap
ESCAPE_STEPS = 120 # hold the escape sweep this many control steps (~1.2 s)
CAND = (-50.0, -40.0, -30.0, -22.0, -14.0, -7.0, 0.0, 7.0, 14.0, 22.0, 30.0, 40.0, 50.0)
BEAR_W = 0.05      # penalty (per deg) for steering away from the goal bearing
BEAR_CAP = 35.0    # clamp the goal bearing used for steering (ignore spurious large yaw)
STICK = 1.2        # bonus (m-equivalent) for keeping the previous turn side (hysteresis)
AZ_LP = 0.09       # low-pass factor on the steering azimuth (kills lurching)
TILT_CAP = 0.24    # cap on |up_horizontal| -> ~14 deg max tilt
TILT_SLEW = 0.022  # max change in a tilt setpoint per control step (rate limit)

# --- walls ---
WALL_Y = 5.0
WALL_MARGIN = 1.3  # start pushing back this far from a wall
WALL_K = 5.0

# --- attitude / altitude / yaw gains (100 Hz) ---
# Roll/pitch are very strong (Tx=0.72*roll, Ty=0.56*pitch -> ~18 rad/s^2 per unit), so modest
# commands suffice; yaw is ~9x weaker (Tz=0.0804*yaw), so it needs a much larger command budget.
KP_ATT = 9.0
KD_ATT = 4.0
TORQUE_CAP = 2.0
KP_YAW = 1.6
KD_YAW = 1.6
YAW_CAP = 1.5
KP_Z = 6.0
KD_Z = 3.2
C_CAP = 7.0
LEVEL_OK = 0.85    # up_z above this -> full nav authority
LEVEL_MIN = 0.50   # up_z below this -> pure recovery (level out, ignore nav)

# module state
_saz = 0.0         # smoothed steering azimuth (deg)
_ux = 0.0
_uy = 0.0
_prog = 0.0        # low-passed forward (world +x) speed, for trap detection
_esc_t = 0         # remaining escape-sweep steps
_esc_dir = 1.0     # escape sweep lateral direction (+1 left, -1 right)


def _slew(cur, target, rate):
    if target > cur + rate:
        return cur + rate
    if target < cur - rate:
        return cur - rate
    return target


def _clearance_by_az(depth):
    """min beam distance per azimuth over the near-horizontal elevations (-20,0,20)."""
    clr = {}
    for b in depth:
        if -21.0 <= b["el"] <= 21.0:
            az, d = b["az"], b["dist"]
            if az not in clr or d < clr[az]:
                clr[az] = d
    return clr


def _scan_corridor(beams, half, gb, saz, drange):
    """For each candidate heading, find the clear-corridor length (how far the drone can travel
    before a trunk enters a `half`-wide tube around that heading). Return the heading whose
    corridor is longest, biased toward the goal bearing and toward the committed side, plus that
    heading's corridor length. Wider `half` -> the chosen path is better centred between trunks
    (cleaner); narrower `half` -> threads tighter gaps."""
    best_th, best_score, best_cc = 0.0, -1e9, drange
    for th in CAND:
        thr_rad = np.radians(th)
        cc = drange
        for az_r, d in beams:
            rel = az_r - thr_rad
            fwd_d = d * np.cos(rel)
            if fwd_d > 0.0 and abs(d * np.sin(rel)) < half and fwd_d < cc:
                cc = fwd_d
        score = cc - BEAR_W * abs(th - gb)
        if saz != 0.0 and (th > 0) == (saz > 0):
            score += STICK
        if score > best_score:
            best_score, best_th, best_cc = score, th, cc
    return best_th, best_cc


def policy(obs):
    global _saz, _ux, _uy, _prog, _esc_t, _esc_dir

    up = np.asarray(obs["up"], float)
    fwd = np.asarray(obs["forward"], float)
    vel = np.asarray(obs["vel"], float)
    angvel = np.asarray(obs["angvel"], float)
    pos = np.asarray(obs["pos"], float)
    depth = obs.get("depth", [])
    drange = obs.get("depth_range", 10.0)

    up_x, up_y, up_z = up[0], up[1], up[2]
    vx, vy, vz = vel[0], vel[1], vel[2]

    # ---------- steering: ADAPTIVE widest-corridor gap follower (accounts for drone width) ----------
    # First demand a WIDE corridor (flies well-centred between trunks -> few scrapes). If no wide
    # corridor extends far enough (a genuinely tight spot in a dense forest), fall back to threading
    # a NARROW corridor so the drone keeps moving instead of stalling. This resolves the tension
    # between clean flight (wants a wide required corridor) and dense feasibility (wants a narrow one).
    clr = _clearance_by_az(depth)
    beams = [(np.radians(az), d) for az, d in clr.items()]
    gb = float(np.clip(np.degrees(obs["heading_error"]), -BEAR_CAP, BEAR_CAP))

    best_th, best_cc = _scan_corridor(beams, CORRIDOR_WIDE, gb, _saz, drange)
    if best_cc < WIDE_OK:
        best_th, best_cc = _scan_corridor(beams, CORRIDOR_TIGHT, gb, _saz, drange)
    # low-pass the steering azimuth so the 25 Hz depth refresh doesn't make it lurch
    _saz += AZ_LP * (best_th - _saz)
    head = np.radians(_saz)
    fwd_ang = np.arctan2(fwd[1], fwd[0])

    # Move ALONG the chosen gap. Speed is the lesser of a cruise cap and a reaction-time limit
    # (never travel faster than the chosen corridor's clear length / T_REACT), so the drone slows
    # into tight spots on its own. Then split into forward + lateral components.
    speed = min(VX, best_cc / T_REACT)
    speed = max(speed, VX * SPEED_FLOOR)
    vf = speed * np.cos(head)
    vlat = speed * np.sin(head)

    # ---------- trap escape ----------
    # Reactive gap-following can get boxed in and oscillate in place (progress ~ 0). Track a
    # low-passed forward progress; if it stalls while still far from the goal, commit a decisive
    # lateral sweep toward the more open side for a fixed spell to break out of the trap. The
    # commitment is held (not re-decided every step) so it doesn't just dither left/right again.
    _prog += 0.02 * (vx - _prog)
    far = obs["distance"] > 3.0
    if _esc_t > 0:
        _esc_t -= 1
        vlat += ESCAPE_V * _esc_dir
        vf *= 0.5
    elif far and _prog < STUCK_V:
        left_open = sum(d for az, d in clr.items() if az > 0.0)
        right_open = sum(d for az, d in clr.items() if az < 0.0)
        _esc_dir = 1.0 if left_open >= right_open else -1.0
        _esc_t = ESCAPE_STEPS

    # ---------- desired world horizontal velocity ----------
    cf, sf = np.cos(fwd_ang), np.sin(fwd_ang)
    # forward unit = (cf, sf); left unit = (-sf, cf)
    vdes_x = vf * cf - vlat * sf
    vdes_y = vf * sf + vlat * cf

    ax_des = KV * (vdes_x - vx)
    ay_des = KV * (vdes_y - vy)

    # wall repulsion (live position, not a map): push toward centre near a wall
    y = pos[1]
    if y > WALL_Y - WALL_MARGIN:
        ay_des -= WALL_K * (y - (WALL_Y - WALL_MARGIN))
    elif y < -(WALL_Y - WALL_MARGIN):
        ay_des -= WALL_K * (y + (WALL_Y - WALL_MARGIN))

    amag = np.hypot(ax_des, ay_des)
    if amag > A_CAP:
        s = A_CAP / amag
        ax_des *= s
        ay_des *= s

    # ---------- desired tilt (nav authority fades as the drone tips over) ----------
    auth = float(np.clip((up_z - LEVEL_MIN) / (LEVEL_OK - LEVEL_MIN), 0.0, 1.0))
    ux_t = auth * float(np.clip(ax_des / G, -TILT_CAP, TILT_CAP))
    uy_t = auth * float(np.clip(ay_des / G, -TILT_CAP, TILT_CAP))
    slew = TILT_SLEW + (1.0 - auth) * 0.10  # slew back to level fast when recovering
    _ux = _slew(_ux, ux_t, slew)
    _uy = _slew(_uy, uy_t, slew)

    # ---------- inner attitude PD (BODY-FRAME, valid at any yaw) ----------
    # Roll torque is about body-x (forward), pitch about body-y (left). The leveling+lean error
    # is the rotation that carries body-up toward the target up; we project it (and the angular
    # rates) onto the body axes so control stays correct even when the drone has yawed -- otherwise
    # a yaw excursion (e.g. from scraping a trunk) miscouples the loop and tips the drone over.
    left = np.cross(up, fwd)                       # body +y in world coords
    nt = np.hypot(np.hypot(_ux, _uy), 1.0)
    up_target = np.array([_ux, _uy, 1.0]) / nt     # desired up direction (world)
    e = np.cross(up, up_target)                    # world-frame error axis (|e| ~ sin angle)
    roll = KP_ATT * float(fwd @ e) - KD_ATT * float(fwd @ angvel)
    pitch = KP_ATT * float(left @ e) - KD_ATT * float(left @ angvel)
    roll = float(np.clip(roll, -TORQUE_CAP, TORQUE_CAP))
    pitch = float(np.clip(pitch, -TORQUE_CAP, TORQUE_CAP))

    # ---------- yaw: hold forward toward the goal + damp the body yaw rate ----------
    yaw = KP_YAW * obs["heading_error"] - KD_YAW * float(up @ angvel)
    yaw = float(np.clip(yaw, -YAW_CAP, YAW_CAP))

    # ---------- altitude ----------
    az_cmd = HOVER + KP_Z * obs["height_error"] - KD_Z * vz
    C = az_cmd / up_z if up_z > 0.6 else az_cmd
    C = float(np.clip(C, 0.5, C_CAP))

    # ---------- torque-budget mixing: fit torque to headroom, do NOT inflate collective ----------
    # The four rotor commands are C plus a zero-mean differential pattern, so total thrust is 4*C
    # whatever the torques. If a torque would drive a rotor below 0, raising C to compensate would
    # add net thrust and make the drone BALLOON into the ceiling. Instead, keep C at what altitude
    # control asked for and scale the torques down together so the worst rotor excursion fits the
    # headroom min(C, 13-C). This preserves as much attitude authority as possible without climbing.
    budget = max(0.3, min(C, 13.0 - C) - 0.2)
    tot = abs(roll) + abs(pitch) + abs(yaw)
    if tot > budget:
        k = budget / tot
        roll *= k
        pitch *= k
        yaw *= k

    thrust1 = C - roll + pitch - yaw   # rear-right
    thrust2 = C + roll + pitch + yaw   # rear-left
    thrust3 = C + roll - pitch - yaw   # front-left
    thrust4 = C - roll - pitch + yaw   # front-right
    return [float(np.clip(t, 0.0, 13.0)) for t in (thrust1, thrust2, thrust3, thrust4)]
