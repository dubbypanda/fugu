import math
import numpy as np

# Self-contained reactive forest-drone controller.
# Uses only live depth + state.  A cascaded velocity/attitude controller produces
# raw rotor thrusts for the fixed Skydio-X2 layout.

MASS = 1.325
G = 9.81
TMAX = 13.0
HOVER = 3.2495625

# Rotor allocation: f -> [total thrust, body roll torque, body pitch torque, body yaw torque]
_ALLOC = np.array([
    [1.0,     1.0,     1.0,     1.0],
    [-0.18,   0.18,    0.18,   -0.18],
    [0.14,    0.14,   -0.14,   -0.14],
    [-0.0201, 0.0201, -0.0201, 0.0201],
], dtype=float)
_ALLOC_INV = np.linalg.inv(_ALLOC)

_STATE = {
    "last_t": None,
    "steer": 0.0,
    "bias": 0.0,
    "last_x": -1e9,
}


def _clamp(x, lo, hi):
    try:
        x = float(x)
    except Exception:
        x = 0.0
    if not math.isfinite(x):
        x = 0.0
    return lo if x < lo else hi if x > hi else x


def _wrap(a):
    return (float(a) + math.pi) % (2.0 * math.pi) - math.pi


def _vec(v, default):
    try:
        a = np.asarray(v, dtype=float).reshape(-1)
        if a.size >= 3:
            a = a[:3]
        else:
            a = np.asarray(default, dtype=float)[:3]
    except Exception:
        a = np.asarray(default, dtype=float)[:3]
    a = np.where(np.isfinite(a), a, np.asarray(default, dtype=float)[:3])
    return a.astype(float)


def _unit(v, default):
    a = np.asarray(v, dtype=float)[:3]
    n = float(np.linalg.norm(a))
    if math.isfinite(n) and n > 1e-9:
        return a / n
    d = np.asarray(default, dtype=float)[:3]
    n = float(np.linalg.norm(d))
    return d / (n + 1e-9)


def _percentile(a, p, default):
    if len(a) == 0:
        return default
    a = np.asarray(a, dtype=float)
    if a.size < 3:
        return float(np.min(a))
    return float(np.percentile(a, p))


def _parse_depth(obs):
    rmax = _clamp(obs.get("depth_range", 10.0), 1.0, 50.0)
    azs = []
    els = []
    ds = []
    for b in obs.get("depth", []) or []:
        try:
            if isinstance(b, dict):
                az = float(b.get("az", 0.0))
                el = float(b.get("el", 0.0))
                d = float(b.get("dist", rmax))
            else:
                az = float(b[0]); el = float(b[1]); d = float(b[2])
        except Exception:
            continue
        if not math.isfinite(d):
            d = rmax
        azs.append(math.radians(az))
        els.append(math.radians(el))
        ds.append(_clamp(d, 0.02, rmax))
    if not azs:
        return np.zeros(0), np.zeros(0), np.zeros(0), rmax
    return np.asarray(azs), np.asarray(els), np.asarray(ds), rmax


def _depth_points(az, el, dist, rmax):
    # Horizontal proxy points from beams near the horizon.  Palms are vertical, so
    # el=-20/0/+20 all see the same trunks; dropping z gives a robust 2-D local scan.
    if dist.size == 0:
        return np.zeros((0, 2), dtype=float)
    m = (np.abs(el) <= math.radians(24.0)) & (dist < 0.995 * rmax)
    if not np.any(m):
        return np.zeros((0, 2), dtype=float)
    ce = np.cos(el[m])
    x = dist[m] * ce * np.cos(az[m])
    y = dist[m] * ce * np.sin(az[m])
    pts = np.column_stack([x, y])
    # Filter numerical/backward artifacts.
    return pts[pts[:, 0] > 0.03]


def _sector_clear(c, az, el, dist, rmax):
    if dist.size == 0:
        return 0.45 * rmax, 0.45 * rmax, False
    da = (az - c + math.pi) % (2.0 * math.pi) - math.pi
    # Mid-elevation beams are the trunk/corridor signal.  Very steep beams mostly see ceiling.
    mask = (np.abs(da) < math.radians(12.5)) & (np.abs(el) <= math.radians(24.0))
    if np.any(mask):
        vals = dist[mask]
        low = _percentile(vals, 18.0, 0.45 * rmax)
        mn = float(np.min(vals))
        known = True
    else:
        low = 0.42 * rmax
        mn = low
        known = False
    # Smooth weighted average around c so directions between discrete beams are usable.
    w = np.exp(-0.5 * (da / math.radians(13.0)) ** 2 - 0.5 * (el / math.radians(22.0)) ** 2)
    sw = float(np.sum(w))
    avg = float(np.sum(w * dist) / sw) if sw > 1e-9 else low
    clear = _clamp(0.58 * low + 0.42 * avg, 0.0, rmax)
    return clear, mn, known


def _choose_steer(obs, az, el, dist, rmax, pts, dt):
    global _STATE
    heading = _wrap(_clamp(obs.get("heading_error", 0.0), -math.pi, math.pi))
    max_a = math.radians(56.0)
    goal_a = _clamp(heading, -max_a, max_a)
    prev = _clamp(_STATE.get("steer", 0.0), -max_a, max_a)
    bias = _clamp(_STATE.get("bias", 0.0), -1.0, 1.0)

    ahead, ahead_min, _ = _sector_clear(0.0, az, el, dist, rmax)
    left30, _, _ = _sector_clear(math.radians(30), az, el, dist, rmax)
    right30, _, _ = _sector_clear(math.radians(-30), az, el, dist, rmax)
    close = _clamp((5.0 - ahead) / 5.0, 0.0, 1.0)
    front_close = 0.0
    if dist.size:
        fm = (np.abs(az) < math.radians(24.0)) & (np.abs(el) <= math.radians(24.0))
        if np.any(fm):
            front_close = _clamp((4.0 - float(np.min(dist[fm]))) / 4.0, 0.0, 1.0)
    avoid_strength = max(close, front_close)

    # Persistent side preference prevents left/right chatter in staggered trunks.
    if avoid_strength > 0.05:
        if abs(left30 - right30) > 0.20:
            side = 1.0 if left30 > right30 else -1.0
        elif abs(prev) > math.radians(8):
            side = 1.0 if prev > 0.0 else -1.0
        elif abs(bias) > 0.08:
            side = 1.0 if bias > 0.0 else -1.0
        else:
            side = 1.0 if goal_a >= 0.0 else -1.0
        held = _STATE.get("avoid_side", 0.0)
        if abs(held) > 0.5 and avoid_strength > 0.12:
            # Commit through a gap; do not swap sides on every 25-Hz depth refresh.
            side = 0.75 * held + 0.25 * side
            side = 1.0 if side >= 0.0 else -1.0
        _STATE["avoid_side"] = side
        bias = _clamp(0.78 * bias + 0.22 * side, -1.0, 1.0)
    else:
        bias *= 0.94
        if ahead > 5.6:
            _STATE["avoid_side"] = 0.0

    best = goal_a
    best_score = -1e100
    best_clear = ahead
    best_path_clear = ahead

    # Candidate scoring: line clearance through local depth points + sector range + progress.
    angles = np.linspace(-max_a, max_a, 81)
    for c in angles:
        c = float(c)
        uc = np.array([math.cos(c), math.sin(c)])
        sector, mn, known = _sector_clear(c, az, el, dist, rmax)

        path_clear = rmax
        penalty = 0.0
        if pts.shape[0]:
            s = pts @ uc
            # signed lateral distance from candidate ray to obstacle surface points
            lat = uc[0] * pts[:, 1] - uc[1] * pts[:, 0]
            active = (s > 0.05) & (s < min(rmax, 7.5))
            if np.any(active):
                ss = s[active]
                ll = lat[active]
                aa = np.abs(ll)
                # Surface points: keep the vehicle centre comfortably farther away.
                safety = 0.76 + 0.22 * np.clip((3.5 - ss) / 3.5, 0.0, 1.0)
                intr = np.maximum(0.0, safety - aa)
                near_w = (1.0 + 2.2 * np.maximum(0.0, (3.2 - ss) / 3.2))
                penalty = float(np.sum((intr / safety) ** 2 * near_w * 4.0))
                blocked = aa < safety
                if np.any(blocked):
                    path_clear = min(path_clear, float(np.min(ss[blocked])))

        # Directly trust sectors too; a low range in the chosen cone means the line is blocked.
        path_clear = min(path_clear, sector)
        progress = math.cos(_wrap(c - goal_a))
        edge = abs(c) / max_a
        smooth = abs(_wrap(c - prev))

        # When an obstacle is close ahead, clearance dominates; otherwise home toward the goal.
        score = 0.88 * min(path_clear, 6.0) + 0.34 * min(sector, 6.0)
        score += (1.55 - 0.85 * close) * progress
        score -= (0.35 + 0.70 * (1.0 - close)) * abs(_wrap(c - goal_a))
        score -= 0.42 * smooth
        score -= 0.38 * edge * edge
        score -= penalty
        if not known:
            score -= 0.20
        if abs(c) > 1e-4:
            score += 0.62 * avoid_strength * bias * (1.0 if c > 0 else -1.0)
        # A very short path at high speed is dangerous; reject unless all options are bad.
        if path_clear < 0.88:
            score -= 10.0 * (0.88 - path_clear) ** 2
        if mn < 0.45:
            score -= 12.0 * (0.45 - mn) ** 2

        if score > best_score:
            best_score = score
            best = c
            best_clear = sector
            best_path_clear = path_clear

    # If the goal direction is wide open, don't overreact to side returns.
    goal_clear, _, _ = _sector_clear(goal_a, az, el, dist, rmax)
    if goal_clear > 4.8 and ahead > 3.8:
        best = 0.70 * goal_a + 0.30 * best
        best_path_clear = max(best_path_clear, goal_clear)

    # Slew limiting; faster changes only in danger.
    turn_rate = math.radians(95.0 + 230.0 * avoid_strength)
    delta = _wrap(best - prev)
    steer = prev + _clamp(delta, -turn_rate * max(dt, 0.005), turn_rate * max(dt, 0.005))
    steer = _clamp(steer, -max_a, max_a)

    if avoid_strength > 0.10 and abs(steer) > math.radians(7):
        bias = _clamp(0.88 * bias + 0.12 * (1.0 if steer > 0 else -1.0), -1.0, 1.0)

    _STATE["steer"] = steer
    _STATE["bias"] = bias
    return steer, best_path_clear, ahead, close, bias



def _update_point_map(pos, xb, yb, zb, az, el, dist, rmax, t):
    """Accumulate a tiny local obstacle map from live depth endpoints."""
    mp = _STATE.setdefault("map", [])
    if dist.size:
        for a, e, d in zip(az, el, dist):
            if d >= 0.985 * rmax or abs(e) > math.radians(24.5):
                continue
            ce = math.cos(float(e))
            db0 = ce * math.cos(float(a)); db1 = ce * math.sin(float(a)); db2 = math.sin(float(e))
            dw = db0 * xb + db1 * yb + db2 * zb
            h = pos + float(d) * dw
            if not (0.25 < h[2] < 5.75):
                continue
            dh = np.array([dw[0], dw[1]], dtype=float)
            nd = float(np.linalg.norm(dh))
            if nd > 1e-6:
                # The return is the near surface.  Push a little farther along the ray so the
                # stored disk is closer to the trunk/wall centreline.
                hxy = np.array([h[0], h[1]], dtype=float) + 0.24 * dh / nd
            else:
                hxy = np.array([h[0], h[1]], dtype=float)
            if not np.all(np.isfinite(hxy)):
                continue
            # Cluster/update nearby points; prevents unbounded growth and smooths 25-Hz scans.
            replaced = False
            for i, (px, py, tt) in enumerate(mp):
                if (px - hxy[0]) ** 2 + (py - hxy[1]) ** 2 < 0.28 ** 2:
                    mp[i] = (0.65 * px + 0.35 * float(hxy[0]),
                             0.65 * py + 0.35 * float(hxy[1]), t)
                    replaced = True
                    break
            if not replaced:
                mp.append((float(hxy[0]), float(hxy[1]), t))
    # Keep only local/recent points.  Behind/side space is unsafe to reason about anyway.
    x = float(pos[0]); y = float(pos[1])
    mp = [(px, py, tt) for (px, py, tt) in mp
          if px > x - 2.5 and px < x + 12.5 and abs(py - y) < 8.0 and t - tt < 9.0]
    if len(mp) > 260:
        mp = sorted(mp, key=lambda q: (q[0] - x) ** 2 + (q[1] - y) ** 2)[:260]
    _STATE["map"] = mp
    return mp


def _seg_clearance_xy(a, b, points):
    """Return min clearance from segment a->b to mapped obstacle points and a soft penalty."""
    if not points:
        return 5.0, 0.0
    ax, ay = float(a[0]), float(a[1]); bx, by = float(b[0]), float(b[1])
    vx, vy = bx - ax, by - ay
    vv = vx * vx + vy * vy + 1e-9
    minc = 9.0
    pen = 0.0
    for px, py, tt in points:
        # Ignore points outside a small capsule around the forward segment.
        wx, wy = px - ax, py - ay
        u = (wx * vx + wy * vy) / vv
        if u < -0.15 or u > 1.18:
            continue
        u2 = 0.0 if u < 0.0 else 1.0 if u > 1.0 else u
        dx = px - (ax + u2 * vx); dy = py - (ay + u2 * vy)
        d = math.hypot(dx, dy)
        if d < minc:
            minc = d
        # Stored point approximates centre/surface; keep a wide centre-line margin.
        safe = 0.92
        if d < safe:
            pen += ((safe - d) / safe) ** 2 * (1.4 + 1.5 * (1.0 - max(0.0, min(1.0, u2))))
        elif d < 1.45:
            pen += 0.08 * (1.45 - d)
    return minc, pen


def _plan_world_path(pos, vel, to_goal, goal_h, xh, yh, az, el, dist, rmax, mp):
    # Current forward depth for emergency speed/turn decisions.
    ahead, _, _ = _sector_clear(0.0, az, el, dist, rmax)
    close = _clamp((4.2 - ahead) / 4.2, 0.0, 1.0)
    gx = float(pos[0] + to_goal[0]); gy = float(pos[1] + to_goal[1])
    remaining = max(0.5, gx - float(pos[0]))
    L = min(5.2, max(2.8, 0.45 * remaining))
    xT = min(gx, float(pos[0]) + L)

    old_y = _STATE.get("target_y", float(pos[1]))
    # Candidate lateral targets.  The forest corridor in this task is about +/-5 m; stay
    # inside a softer +/-4.35 m to avoid the wall/rotor contact while still using the width.
    y_min, y_max = -4.35, 4.35
    candidates = list(np.linspace(y_min, y_max, 47))
    candidates += [float(pos[1]), float(pos[1]) + 0.7, float(pos[1]) - 0.7, gy, old_y]

    best_y = float(pos[1]); best_score = -1e99; best_clear = 0.0
    a = np.array([pos[0], pos[1]], dtype=float)
    for yc in candidates:
        yc = _clamp(yc, y_min, y_max)
        b = np.array([xT, yc], dtype=float)
        seg_len = float(np.linalg.norm(b - a))
        if seg_len < 0.2:
            continue
        clear, pen = _seg_clearance_xy(a, b, mp)
        wall_pen = 0.0
        if abs(yc) > 3.9:
            wall_pen = 0.55 * (abs(yc) - 3.9) ** 2
        # Check whether this segment points within/near the current camera FOV; mapped points
        # let us accept a little outside, but unseen hard side-slips are penalised.
        dvec = np.array([b[0] - pos[0], b[1] - pos[1], 0.0])
        dhat = _unit(dvec, goal_h)
        rel = math.atan2(float(np.dot(dhat, yh)), float(np.dot(dhat, xh)))
        fov_pen = 0.0 if abs(rel) < math.radians(58) else 1.2 * (abs(rel) - math.radians(58))
        # Favour large clearance first, then smooth/y-to-goal.  Goal-y is weak: the map should
        # choose the gap, not a memorised centreline.
        score = 2.8 * min(clear, 2.6) - 5.0 * pen
        score -= 0.20 * abs(yc - gy) + 0.22 * abs(yc - old_y) + 0.08 * abs(yc - pos[1])
        score -= wall_pen + fov_pen
        # Small reward for making real x progress; all candidates have same xT but very large
        # lateral sweeps reduce actual forward component.
        score += 0.20 * (b[0] - pos[0]) / (seg_len + 1e-9)
        if clear < 0.62:
            score -= 18.0 * (0.62 - clear) ** 2
        if score > best_score:
            best_score, best_y, best_clear = score, yc, clear

    # Smooth the lateral target so the drone commits through a selected gap instead of dithering.
    target_y = 0.80 * float(old_y) + 0.20 * float(best_y)
    # But if the old target is now very unsafe, switch faster.
    if best_clear < 0.85 or abs(best_y - old_y) < 0.4:
        target_y = 0.55 * float(old_y) + 0.45 * float(best_y)
    target_y = _clamp(target_y, y_min, y_max)
    _STATE["target_y"] = target_y

    b = np.array([xT, target_y, 0.0], dtype=float)
    path_h = _unit(np.array([b[0] - pos[0], b[1] - pos[1], 0.0]), goal_h)
    path_clear, _ = _seg_clearance_xy(np.array([pos[0], pos[1]]), np.array([xT, target_y]), mp)
    path_clear = min(path_clear, ahead)
    steer = math.atan2(float(np.dot(path_h, yh)), float(np.dot(path_h, xh)))
    return path_h, path_clear, ahead, close, steer


def _mix_from_force_torque(T, tau):
    vec = np.array([T, tau[0], tau[1], tau[2]], dtype=float)
    f = _ALLOC_INV @ vec
    if not np.all(np.isfinite(f)):
        return np.full(4, HOVER, dtype=float)
    return np.clip(f, 0.0, TMAX)


def policy(obs):
    global _STATE
    if not isinstance(obs, dict):
        obs = {}

    t = _clamp(obs.get("time", 0.0), 0.0, 1e9)
    last_t = _STATE.get("last_t", None)
    if last_t is None or t < float(last_t) - 0.02 or t < 0.03:
        _STATE = {"last_t": t, "steer": 0.0, "bias": 0.0, "last_x": -1e9, "map": [], "target_y": 0.0, "avoid_side": 0.0}
        dt = 0.01
    else:
        dt = _clamp(t - float(last_t), 0.005, 0.05)
        _STATE["last_t"] = t

    pos = _vec(obs.get("pos", [0.0, 0.0, 3.0]), [0.0, 0.0, 3.0])
    vel = _vec(obs.get("vel", [0.0, 0.0, 0.0]), [0.0, 0.0, 0.0])
    xb = _unit(_vec(obs.get("forward", [1.0, 0.0, 0.0]), [1.0, 0.0, 0.0]), [1.0, 0.0, 0.0])
    zb = _unit(_vec(obs.get("up", [0.0, 0.0, 1.0]), [0.0, 0.0, 1.0]), [0.0, 0.0, 1.0])

    # Orthonormal body frame, columns are body axes in world frame.
    xb = xb - zb * float(np.dot(xb, zb))
    xb = _unit(xb, [1.0, 0.0, 0.0])
    yb = _unit(np.cross(zb, xb), [0.0, 1.0, 0.0])
    xb = _unit(np.cross(yb, zb), xb)
    R = np.column_stack([xb, yb, zb])

    # Horizontal body axes for navigation.
    xh = np.array([xb[0], xb[1], 0.0], dtype=float)
    xh = _unit(xh, [1.0, 0.0, 0.0])
    yh = np.array([-xh[1], xh[0], 0.0], dtype=float)

    to_goal = _vec(obs.get("to_goal", [80.0 - pos[0], -pos[1], 3.0 - pos[2]]), [1.0, 0.0, 0.0])
    dist_goal = _clamp(obs.get("distance", np.linalg.norm(to_goal)), 0.0, 1e9)
    goal_h = _unit(np.array([to_goal[0], to_goal[1], 0.0]), xh)

    az, el, dr, rmax = _parse_depth(obs)
    pts = _depth_points(az, el, dr, rmax)
    steer, path_clear, ahead, close, bias = _choose_steer(obs, az, el, dr, rmax, pts, dt)

    # Desired travel direction.  The depth-selected ray is useful only while the
    # camera is roughly looking down-course.  If yaw has been knocked far away from
    # the goal, do NOT fly along body-forward (which may be backwards); slow down and
    # use the world goal direction so the attitude loop yaws the camera back.
    body_path_h = _unit(math.cos(steer) * xh + math.sin(steer) * yh, goal_h)
    align_goal = _clamp(float(np.dot(xh, goal_h)), -1.0, 1.0)
    body_weight = _clamp((align_goal + 0.15) / 0.95, 0.0, 1.0)
    path_h = _unit(body_weight * body_path_h + (1.0 - body_weight) * goal_h, goal_h)
    vel_h_for_yaw = np.array([vel[0], vel[1], 0.0], dtype=float)
    vhn_for_yaw = float(np.linalg.norm(vel_h_for_yaw[:2]))
    if vhn_for_yaw > 0.45:
        travel_h = _unit(0.55 * path_h + 0.45 * (vel_h_for_yaw / vhn_for_yaw), path_h)
    else:
        travel_h = path_h.copy()
    align_travel = _clamp(float(np.dot(xh, travel_h)), -1.0, 1.0)
    if dist_goal < 8.0 and path_clear > 2.0:
        a = _clamp((8.0 - dist_goal) / 6.0, 0.0, 1.0)
        path_h = _unit((1.0 - a) * path_h + a * goal_h, path_h)

    # Speed schedule from free distance; keep enough pace for the battery budget.
    free = _clamp((path_clear - 0.70) / max(1.0, rmax - 0.70), 0.0, 1.0)
    # braking-safe speed for available clearance (with conservative accel)
    v_stop = math.sqrt(max(0.0, 2.0 * 3.4 * max(0.0, path_clear - 0.35)))
    target_speed = min(0.80 + 2.35 * free, v_stop, 2.70)
    if ahead < 1.6:
        target_speed = min(target_speed, 0.35 + 0.25 * ahead)
    elif ahead < 4.2:
        target_speed = min(target_speed, 0.55 + 0.28 * ahead)
    if dr.size == 0:
        target_speed = min(target_speed, 1.2)
    if dist_goal < 8.0:
        target_speed = min(target_speed, max(0.0, 0.55 * dist_goal))
    # If the nose is far from the goal direction, pause/creep while yawing back.
    # This avoids wasting the battery flying backwards after a close avoidance turn.
    target_speed *= _clamp((align_travel + 0.10) / 0.86, 0.0, 1.0)
    if dist_goal < 0.9:
        target_speed = 0.0
    if target_speed < 0.28 and ahead > 0.55 and dist_goal > 2.2:
        target_speed = 0.28
    target_speed = _clamp(target_speed, 0.0, 2.75)

    vel_h = np.array([vel[0], vel[1], 0.0], dtype=float)
    v_des = path_h * target_speed

    # Obstacle-side repulsion from nearby depth points; lateral only to avoid backing into
    # unseen space, plus damping if we are very near an object in front.
    lat_push = 0.0
    if pts.shape[0]:
        rng = 3.2
        m = (pts[:, 0] > 0.05) & (pts[:, 0] < rng)
        if np.any(m):
            p = pts[m]
            w = ((rng - p[:, 0]) / rng) ** 2 / (0.35 + np.abs(p[:, 1]))
            # obstacle at positive body-y is left -> push right (negative y)
            lat_push = float(np.sum(-np.sign(p[:, 1]) * w) / (np.sum(w) + 1e-9))
            lat_push = _clamp(lat_push, -1.0, 1.0)
    lat_push += 0.25 * bias * close
    lat_push = _clamp(lat_push, -1.0, 1.0)

    kp_v = 0.92 + 0.22 * close
    acc_h = kp_v * (v_des - vel_h)
    acc_h += yh * (1.35 * lat_push * close)
    # Keep actual travel aligned with the selected/yawed path.  This is the important
    # anti-graze term: it damps blind side-slip while still allowing smooth committed turns.
    side_h = np.array([-path_h[1], path_h[0], 0.0], dtype=float)
    side_v = float(np.dot(vel_h, side_h))
    acc_h -= side_h * ((0.55 + 0.85 * close) * side_v)
    danger = max(_clamp((1.65 - path_clear) / 1.65, 0.0, 1.0), _clamp((0.95 - ahead) / 0.95, 0.0, 1.0))
    acc_h -= vel_h * (1.25 * danger)

    # Keep horizontal accelerations within a realistic tilt envelope.
    max_tilt = math.radians(24.0 + 7.0 * close)
    max_acc = G * math.tan(max_tilt)
    an = float(np.linalg.norm(acc_h[:2]))
    if an > max_acc:
        acc_h *= max_acc / (an + 1e-9)

    # Altitude hold at the goal altitude (normally 3 m), with hard floor/ceiling guards.
    height_error = _clamp(obs.get("height_error", 3.0 - pos[2]), -10.0, 10.0)
    a_z = 5.8 * height_error - 4.1 * float(vel[2])
    if pos[2] < 1.2:
        a_z += 7.0 * (1.2 - pos[2])
    if pos[2] < 0.75:
        a_z += 14.0 * (0.75 - pos[2])
    if pos[2] > 5.15:
        a_z -= 5.0 * (pos[2] - 5.15)
    a_z = _clamp(a_z, -5.0, 5.2)

    a_cmd = np.array([acc_h[0], acc_h[1], G + a_z], dtype=float)
    f_norm = float(np.linalg.norm(a_cmd))
    if not math.isfinite(f_norm) or f_norm < 1e-6:
        a_cmd = np.array([0.0, 0.0, G], dtype=float)
        f_norm = G
    zb_des = a_cmd / f_norm

    # Desired heading: look where we are actually travelling/intending to travel, so the
    # forward-only depth fan points into the next gap rather than sideways across it.
    xc = np.array([travel_h[0], travel_h[1], 0.0], dtype=float)
    xc = _unit(xc, [1.0, 0.0, 0.0])
    yb_des = np.cross(zb_des, xc)
    if float(np.linalg.norm(yb_des)) < 1e-6:
        yb_des = np.array([0.0, 1.0, 0.0])
    yb_des = _unit(yb_des, [0.0, 1.0, 0.0])
    xb_des = _unit(np.cross(yb_des, zb_des), xc)
    R_des = np.column_stack([xb_des, yb_des, zb_des])

    # Geometric attitude PD.  Convert world angular velocity to body components.
    Err = 0.5 * (R_des.T @ R - R.T @ R_des)
    e_R = np.array([Err[2, 1], Err[0, 2], Err[1, 0]], dtype=float)
    omega_w = _vec(obs.get("angvel", [0.0, 0.0, 0.0]), [0.0, 0.0, 0.0])
    omega_b = R.T @ omega_w

    # Slightly stronger roll/pitch than yaw; yaw reaction torque is weak and should not steal
    # too much thrust from stabilisation.
    Kp = np.array([18.5, 18.5, 2.8])
    Kd = np.array([3.45, 3.45, 0.85])
    tau = -Kp * e_R - Kd * omega_b
    tau[0] = _clamp(tau[0], -2.0, 2.0)
    tau[1] = _clamp(tau[1], -2.0, 2.0)
    tau[2] = _clamp(tau[2], -0.32, 0.32)

    # Collective thrust along current body up.  Keep positive margin when tilted.
    T_cmd = MASS * float(np.dot(a_cmd, zb))
    if zb[2] < 0.35:
        T_cmd = max(T_cmd, MASS * G * 0.55)
    T_cmd = _clamp(T_cmd, 1.5, 31.0)

    f = _mix_from_force_torque(T_cmd, tau)
    return [float(f[0]), float(f[1]), float(f[2]), float(f[3])]
