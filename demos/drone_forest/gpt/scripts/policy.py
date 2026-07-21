import numpy as np

HOVER = 3.25

_last_time = -1.0
_last_steer = 0.0
_stuck_time = 0.0


def _clip(x, lo, hi):
    return float(np.clip(x, lo, hi))


def _unit(v, fallback):
    n = float(np.linalg.norm(v))
    if n < 1e-8:
        return np.asarray(fallback, dtype=float)
    return np.asarray(v, dtype=float) / n


def _depth_rows(obs):
    rows = {}
    rng = float(obs.get("depth_range", 10.0))
    for b in obs.get("depth", ()):
        az = float(b["az"])
        el = float(b["el"])
        rows[(az, el)] = min(float(b["dist"]), rng)
    return rows, rng


def _range_at(rows, az, rng, els=(0.0, -20.0, 20.0)):
    vals = [rows.get((az, el), rng) for el in els]
    return min(vals) if vals else rng


def _choose_steer(obs):
    global _last_steer
    rows, rng = _depth_rows(obs)
    azs = sorted({k[0] for k in rows.keys()})
    if not azs:
        return _clip(float(obs.get("heading_error", 0.0)), -0.6, 0.6), rng, rng

    goal = _clip(float(obs.get("heading_error", 0.0)), -1.05, 1.05)
    goal_deg = float(np.degrees(goal))
    ranges = {az: _range_at(rows, az, rng) for az in azs}
    front = min(ranges.get(-4.0, rng), ranges.get(4.0, rng),
                ranges.get(-12.0, rng), ranges.get(12.0, rng))
    best_score = -1e9
    best_az = 0.0
    for az in azs:
        d = ranges[az]
        near = []
        for bz in azs:
            da = abs(bz - az)
            if da <= 24.0:
                near.append((1.0 / (1.0 + da / 14.0), ranges[bz]))
        wsum = sum(w for w, _ in near) or 1.0
        local = sum(w * dd for w, dd in near) / wsum
        min_near = min(dd for _, dd in near) if near else d

        align = np.cos(np.radians(az - goal_deg))
        forward = np.cos(np.radians(az))
        continuity = -abs(np.radians(az) - _last_steer)
        close = max(0.0, 4.2 - min_near)
        very_close = max(0.0, 2.2 - d)

        score = (
            0.70 * local
            + 1.85 * align
            + 0.85 * forward
            + 0.55 * continuity
            - 1.55 * close * close
            - 2.10 * very_close * very_close
        )
        if score > best_score:
            best_score = score
            best_az = az

    steer = np.radians(best_az)

    if front < 4.0:
        left = max(ranges.get(12.0, 0.0), ranges.get(24.0, 0.0), ranges.get(40.0, 0.0))
        right = max(ranges.get(-12.0, 0.0), ranges.get(-24.0, 0.0), ranges.get(-40.0, 0.0))
        side = 1.0 if left >= right else -1.0
        steer = 0.72 * steer + 0.28 * side * 0.55

    steer = _clip(0.72 * _last_steer + 0.28 * steer, -0.95, 0.95)
    _last_steer = steer
    return steer, front, _range_at(rows, best_az, rng)


def policy(obs):
    global _last_time, _last_steer, _stuck_time

    t = float(obs.get("time", 0.0))
    if t < _last_time:
        _last_steer = 0.0
        _stuck_time = 0.0
    dt = 0.01 if _last_time < 0.0 else max(0.0, min(0.05, t - _last_time))
    _last_time = t

    pos = np.asarray(obs["pos"], dtype=float)
    vel = np.asarray(obs["vel"], dtype=float)
    fwd = _unit(np.asarray(obs["forward"], dtype=float), [1.0, 0.0, 0.0])
    up = _unit(np.asarray(obs["up"], dtype=float), [0.0, 0.0, 1.0])
    left = _unit(np.cross(up, fwd), [0.0, 1.0, 0.0])
    fwd_h = _unit(np.array([fwd[0], fwd[1], 0.0]), [1.0, 0.0, 0.0])
    left_h = _unit(np.array([left[0], left[1], 0.0]), [0.0, 1.0, 0.0])

    steer, front_clear, target_clear = _choose_steer(obs)
    travel = np.cos(steer) * fwd_h + np.sin(steer) * left_h
    travel = _unit(np.array([travel[0], travel[1], 0.0]), [1.0, 0.0, 0.0])

    clear = min(front_clear, target_clear)
    speed = 2.1 + 2.45 * _clip((clear - 2.0) / 5.0, 0.0, 1.0)
    if abs(steer) > 0.55:
        speed *= 0.86
    if clear < 2.2:
        speed = min(speed, 1.95)

    to_goal = np.asarray(obs["to_goal"], dtype=float)
    goal_h = _unit(np.array([to_goal[0], to_goal[1], 0.0]), [1.0, 0.0, 0.0])
    v_des_h = speed * _unit(0.78 * travel + 0.22 * goal_h, [1.0, 0.0, 0.0])

    if vel[0] < 0.25 and front_clear < 4.2:
        _stuck_time = min(2.0, _stuck_time + dt)
    else:
        _stuck_time = max(0.0, _stuck_time - 2.0 * dt)
    if _stuck_time > 0.35:
        side = 1.0 if _last_steer >= 0.0 else -1.0
        v_des_h += left_h * side * (0.55 + 0.75 * min(1.0, _stuck_time))

    v_h = np.array([vel[0], vel[1], 0.0])
    a_h = 1.55 * (v_des_h - v_h)

    rows, _ = _depth_rows(obs)
    avoid = np.zeros(3)
    for (az, el), dist in rows.items():
        if abs(el) > 20.0:
            continue
        margin = 3.2 if abs(az) < 18.0 else 2.45
        if dist >= margin:
            continue
        if abs(az) < 6.0:
            side = 1.0 if _last_steer >= 0.0 else -1.0
        else:
            side = -np.sign(az)
        strength = ((margin - dist) / margin) ** 2
        avoid += strength * side * left_h
    if np.linalg.norm(avoid[:2]) > 1e-6:
        a_h += 3.7 * _unit(avoid, [0.0, 0.0, 0.0]) * min(1.0, float(np.linalg.norm(avoid)))

    recover = float(up[2]) < 0.78
    max_tilt_vec = 0.49
    if recover:
        max_tilt_vec = 0.0
        a_h *= 0.0
    tilt = np.array([a_h[0] / 9.8, a_h[1] / 9.8, 0.0])
    ntilt = float(np.linalg.norm(tilt[:2]))
    if ntilt > max_tilt_vec:
        tilt *= max_tilt_vec / ntilt
    desired_up = _unit(np.array([tilt[0], tilt[1], 1.0]), [0.0, 0.0, 1.0])

    err_axis = np.cross(up, desired_up)
    angvel = np.asarray(obs["angvel"], dtype=float)
    roll_rate = float(np.dot(angvel, fwd))
    pitch_rate = float(np.dot(angvel, left))
    yaw_rate = float(np.dot(angvel, up))
    roll_err = float(np.dot(err_axis, fwd))
    pitch_err = float(np.dot(err_axis, left))

    recover_amt = _clip((0.85 - float(up[2])) / 0.85, 0.0, 1.0)
    att_kp = 2.15 + 8.00 * recover_amt
    att_kd = 0.38 + 0.35 * recover_amt
    roll = _clip(att_kp * roll_err - att_kd * roll_rate, -8.2, 8.2)
    pitch = _clip(att_kp * pitch_err - att_kd * pitch_rate, -8.2, 8.2)

    yaw_err = _clip(0.80 * steer + 0.20 * float(obs.get("heading_error", 0.0)), -0.9, 0.9)
    if recover:
        yaw_err *= _clip((float(up[2]) - 0.20) / 0.45, 0.0, 1.0)
    yaw = 0.72 * yaw_err - 0.22 * yaw_rate

    h_err = float(obs.get("height_error", 3.0 - pos[2]))
    if pos[2] < 2.25:
        h_err += 0.9 * (2.25 - pos[2])
    if pos[2] > 4.35:
        h_err -= 0.6 * (pos[2] - 4.35)
    collective = HOVER / max(0.55, float(up[2])) + 0.85 * h_err - 0.48 * float(vel[2])
    if recover and pos[2] > 1.5:
        collective = min(collective, 4.3)
    collective = _clip(collective, 1.4, 7.4)

    thrust1 = collective - roll + pitch - yaw
    thrust2 = collective + roll + pitch + yaw
    thrust3 = collective + roll - pitch - yaw
    thrust4 = collective - roll - pitch + yaw
    return [
        _clip(thrust1, 0.0, 13.0),
        _clip(thrust2, 0.0, 13.0),
        _clip(thrust3, 0.0, 13.0),
        _clip(thrust4, 0.0, 13.0),
    ]
