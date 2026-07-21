"""Palm-forest obstacle field for the forest-drone navigation benchmark.

WE own this file. It defines the obstacle vocabulary, the 3D collision geometry, the
forward depth-sensor, and the procedural forest-corridor generator. Identical for every
model. It uses box/capsule/sphere collision proxies, raycast depth perception, and a
feasible-by-construction corridor; the obstacles are palm trees and the vehicle flies.

Design (locked in):
  * A long corridor the drone traverses start -> goal, densely lined with PALM TREES.
  * Each palm = a thin vertical TRUNK (capsule) + a leafy CROWN (sphere) on top. BOTH are
    solid collision. The 1032-tri palm mesh is skinned on for looks later (v1 = primitives:
    a brown 'bark' capsule + a green 'frond' sphere, so it already reads as a palm).
  * Trees come in VARYING HEIGHTS, so there is no single altitude that clears the forest:
    the route must climb and descend (full 3D navigation), not just weave horizontally.
  * Gates alternate y / z / narrow / scatter (as in the AUV) so the feasible slot weaves in
    BOTH axes, plus 'narrow' gaps < the drone's rotor span that can only be passed by
    banking. The slot centres trace a smooth 3D weave -> feasible by construction.
  * The corridor is SEALED (AUV sealed-pipe lesson): invisible side walls close the sides
    and a low ceiling closes the top, with the tallest crowns reaching it, so a policy
    cannot climb above the canopy and cruise straight over everything.

Collision is PHYSICAL (MuJoCo contacts in env.py): obstacles are solid, and the FIRST
contact with any tree / wall / ground ends the run (instant death). The clearance
field below is used for the forward DEPTH sensor (raycasting), exactly like the AUV sonar.
"""
import numpy as np

# --- corridor geometry (metres) --------------------------------------------
LENGTH = 100.0          # start (x=0) -> goal (x=LENGTH). 80 -> 100 m, a longer
                        # weave so navigation has more room to separate a good controller from a poor one.
BAND_Y = 5.0            # half-width of the forest band in y
GROUND_Z = 0.0          # the ground plane (trees are rooted here; hitting it = crash)
CEIL_Z = 6.0            # a LOW, SEALED ceiling: the drone flies in (GROUND_Z, CEIL_Z). Every
                        # trunk reaches it, so there is no "fly over the trees" cheat (the same
                        # anti-cheat as the AUV sealed pipe). Crowns sit ABOVE it as canopy.
Z_MID = 3.0             # centre altitude of the flight band
GATE_DX = 5.0           # spacing between gates along x
GATE_X0 = 9.0           # first gate (clear run-up + room to take off and stabilize)
SLOT_HALF = 1.7         # half-width of an open slot (drone half-span ~0.3 + margin)
TRUNK_R = 0.22          # palm trunk radius
CROWN_R = 1.35          # palm crown (frond ball) radius
NARROW_GAP = 0.55       # centre-to-centre trunk gap in a 'narrow' gate. Drone rotor span
                        # ~0.6 m, so the drone MUST roll to slip through (banking test).
WALL_HZ = CEIL_Z / 2.0  # half-height of the invisible side walls (cover 0..CEIL_Z)

# distributed-forest layout (with a reference photo: spread trees out over
# the WHOLE corridor so MANY paths exist and the drone CHOOSES its own -- no pre-carved lane)
# DENSE no-wall setup ("無理ゲー"): a thick staggered palm forest whose
# DENSITY alone blocks the straight-line cheat (no baffle walls needed) -- back to the original
# first-setup look. Baffles below are kept but DISABLED (N_BAFFLES=0); re-enable them later.
GRID_SX = 1.8           # column pitch -- DENSE but eased a touch so a slow
GRID_SY = 1.8           # tracker can actually REACH; still no straight y-lane crosses (staggered
                        # cols offset half a row) so a straight flyer clips. Balances floor vs cheat.
GRID_JIT = 0.35         # per-palm random jitter (organic)
MIN_SEP = 1.1           # palms cannot be closer than this centre-to-centre (roots)
BLOCKER_PITCH = 9.0     # spacing of the centreline (y~0) blocker trunks down the corridor

# baffle slalom (plan B anti-cheat): a few solid full-height walls with the opening on
# ALTERNATING sides, so the flyable route S-curves and NO straight line reaches the goal.
N_BAFFLES = 0           # DISABLED (dense no-wall forest). Set >0 to re-add walls.
BAFFLE_OPEN = 3.6       # opening width at the alternating edge (flyable)
BAFFLE_T = 0.3          # wall half-thickness in x
TRUNK_TOP = 7.5         # every trunk runs ground -> here (past the CEIL_Z ceiling); crown on top
                        # sits ABOVE the ceiling as canopy. Uniform tall trunks (matches the photo).
Z_FLY = 3.0             # cruise altitude the reference plans at (mid-band)
PLAN_RES = 0.25         # A* grid resolution (m)
PLAN_CLEAR = 0.55       # added to TRUNK_R when blocking A* cells ~= drone half-span (~0.31) +
                        # ~0.24 m margin. Near the drone's ACTUAL clearance need so A* finds a
                        # route wherever the drone physically fits (a bigger value made A* fail
                        # in the dense forest and fall back to a straight line = the cheat).


# --- collision geometry (shared vocab with the AUV: box / capsule / sphere) --
def _seg_clearance(p, a, b, r):
    p, a, b = np.asarray(p, float), np.asarray(a, float), np.asarray(b, float)
    ab = b - a
    denom = float(ab @ ab)
    t = 0.0 if denom < 1e-12 else float(np.clip((p - a) @ ab / denom, 0.0, 1.0))
    return float(np.linalg.norm(p - (a + t * ab))) - r


def _box_clearance(p, c, h, yaw_deg):
    p, c, h = np.asarray(p, float), np.asarray(c, float), np.asarray(h, float)
    dx, dy, dz = p - c
    a = np.radians(-yaw_deg)
    ca, sa = np.cos(a), np.sin(a)
    q = np.array([abs(ca * dx - sa * dy) - h[0],
                  abs(sa * dx + ca * dy) - h[1],
                  abs(dz) - h[2]])
    outside = float(np.linalg.norm(np.maximum(q, 0.0)))
    inside = float(min(np.max(q), 0.0))
    return outside + inside


def clearance(o, p):
    """Signed distance from world point p to the obstacle surface (negative inside)."""
    t = o["type"]
    if t == "box":
        return _box_clearance(p, o["c"], o["h"], o.get("yaw", 0.0))
    if t == "capsule":
        return _seg_clearance(p, o["p0"], o["p1"], o["r"])
    if t == "sphere":
        return float(np.linalg.norm(np.asarray(p, float) - np.asarray(o["c"], float))) - o["r"]
    raise ValueError(f"unknown obstacle type {t!r}")


def nearest(obstacles, p):
    best, bi = float("inf"), -1
    for i, o in enumerate(obstacles):
        c = clearance(o, p)
        if c < best:
            best, bi = c, i
    return best, bi


# --- forward depth sensor (a camera/lidar-style range fan) ------------------
# A forward-looking fan of range beams in the drone BODY frame (x=fwd, y=left, z=up).
# One distance per beam (capped at DEPTH_RANGE) -> realistic partial perception: the drone
# senses the trees locally, it is NOT handed the forest map. (AUV called this 'sonar'.)
DEPTH_AZ = (-60.0, -40.0, -24.0, -12.0, -4.0, 4.0, 12.0, 24.0, 40.0, 60.0)  # deg, +=left
DEPTH_EL = (-40.0, -20.0, 0.0, 20.0, 40.0)                                  # deg, +=up
DEPTH_RANGE = 10.0


def depth_dirs_body():
    dirs = []
    for el in DEPTH_EL:
        ce, se = np.cos(np.radians(el)), np.sin(np.radians(el))
        for az in DEPTH_AZ:
            ca, sa = np.cos(np.radians(az)), np.sin(np.radians(az))
            dirs.append((az, el, np.array([ce * ca, ce * sa, se])))
    return dirs


_DEPTH_DIRS = depth_dirs_body()


def _ray_hit(o, O, D, max_range):
    """ANALYTIC ray/obstacle intersection -> distance along unit D (or max_range if miss).
    Fast (no sphere-marching): trunks are vertical cylinders, walls are AABBs, crowns spheres.
    This keeps the 100 Hz depth sensor cheap even in a dense forest."""
    t = o["type"]
    if t == "capsule":                      # vertical trunk = cylinder [z0,z1], radius r
        (cx, cy, z0), (_, _, z1) = o["p0"], o["p1"]
        r = o["r"]
        ox, oy = O[0] - cx, O[1] - cy
        dx, dy = D[0], D[1]
        a = dx * dx + dy * dy
        if a < 1e-12:
            return max_range                # vertical ray: treat as miss (no side hit)
        b = 2.0 * (ox * dx + oy * dy)
        c = ox * ox + oy * oy - r * r
        disc = b * b - 4 * a * c
        if disc < 0.0:
            return max_range
        s = np.sqrt(disc)
        for tt in ((-b - s) / (2 * a), (-b + s) / (2 * a)):
            if 0.0 < tt < max_range:
                zh = O[2] + tt * D[2]
                if z0 - 0.05 <= zh <= z1 + 0.05:
                    return tt
        return max_range
    if t == "box":                          # AABB (walls/ceiling, yaw 0): slab method
        c, h = o["c"], o["h"]
        tmin, tmax = 0.0, max_range
        for k in range(3):
            if abs(D[k]) < 1e-12:
                if O[k] < c[k] - h[k] or O[k] > c[k] + h[k]:
                    return max_range
            else:
                t1 = (c[k] - h[k] - O[k]) / D[k]
                t2 = (c[k] + h[k] - O[k]) / D[k]
                if t1 > t2:
                    t1, t2 = t2, t1
                tmin = max(tmin, t1)
                tmax = min(tmax, t2)
                if tmin > tmax:
                    return max_range
        return tmin if tmin > 0.0 else max_range
    if t == "sphere":
        c, r = o["c"], o["r"]
        ox, oy, oz = O[0] - c[0], O[1] - c[1], O[2] - c[2]
        b = 2.0 * (ox * D[0] + oy * D[1] + oz * D[2])
        cc = ox * ox + oy * oy + oz * oz - r * r
        disc = b * b - 4 * cc
        if disc < 0.0:
            return max_range
        s = np.sqrt(disc)
        tt = (-b - s) / 2.0
        if tt <= 0.0:
            tt = (-b + s) / 2.0
        return tt if 0.0 < tt < max_range else max_range
    return max_range


def depth_scan(obstacles, origin, fwd, up, max_range=DEPTH_RANGE):
    """Cast the body-frame beam fan from `origin` given heading (fwd) and up. Returns a list
    of {az, el, dist} (dist capped at max_range)."""
    origin = np.asarray(origin, float)
    fwd = np.asarray(fwd, float)
    up = np.asarray(up, float)
    fwd = fwd / (np.linalg.norm(fwd) + 1e-9)
    up = up / (np.linalg.norm(up) + 1e-9)
    left = np.cross(up, fwd)
    left = left / (np.linalg.norm(left) + 1e-9)
    # cheap cull: only obstacles whose horizontal footprint is within range of the origin
    cand = []
    for o in obstacles:
        if o["type"] == "capsule":
            hx, hy = o["p0"][0] - origin[0], o["p0"][1] - origin[1]
            if hx * hx + hy * hy < (max_range + o["r"]) ** 2:
                cand.append(o)
        else:
            cand.append(o)                  # few walls/crowns -> keep
    out = []
    for az, el, db in _DEPTH_DIRS:
        dw = db[0] * fwd + db[1] * left + db[2] * up
        dist = max_range
        for o in cand:
            h = _ray_hit(o, origin, dw, dist)
            if h < dist:
                dist = h
        out.append({"az": az, "el": el, "dist": round(float(dist), 3)})
    return out


# --- rendering (native primitive == collision proxy) ------------------------
_MATS = {                       # material name -> the look (defined in env.py's asset block)
    "bark": None, "frond": None, "wall": None, "seal": None,
}


def render_geom(o, name, mat=None):
    """MJCF geom string for the obstacle. SOLID (contype/conaffinity 1) so the drone
    physically hits it. mat hint picks the look; 'seal' renders translucent so the top-down
    camera can see inside the sealed corridor while staying physically solid. `mat` overrides
    the look (e.g. 'invisible' to hide a collision primitive under a cosmetic mesh)."""
    mat = mat or o.get("mat", "wall")
    common = f'material="{mat}" contype="1" conaffinity="1"'
    t = o["type"]
    if t == "box":
        c, h = o["c"], o["h"]
        return (f'<geom name="{name}" type="box" pos="{c[0]} {c[1]} {c[2]}" '
                f'size="{h[0]} {h[1]} {h[2]}" euler="0 0 {o.get("yaw", 0.0)}" {common}/>')
    if t == "capsule":
        a, b = o["p0"], o["p1"]
        return (f'<geom name="{name}" type="capsule" '
                f'fromto="{a[0]} {a[1]} {a[2]} {b[0]} {b[1]} {b[2]}" '
                f'size="{o["r"]}" {common}/>')
    if t == "sphere":
        c = o["c"]
        return (f'<geom name="{name}" type="sphere" pos="{c[0]} {c[1]} {c[2]}" '
                f'size="{o["r"]}" {common}/>')
    raise ValueError(f"unknown obstacle type {t!r}")


def proxy(o):
    """The geometry the MODEL sees under the 'map' ablation (drop cosmetic hints)."""
    return {k: v for k, v in o.items() if k not in ("mat", "mesh")}


# --- palm + boundary constructors -------------------------------------------
def _palm(x, y, trunk_top, crown_r=CROWN_R, trunk_r=TRUNK_R):
    """One palm = a vertical bark trunk (ground -> trunk_top) + a green crown ball on top.
    Returns the two obstacle dicts."""
    trunk = {"type": "capsule", "p0": [x, y, GROUND_Z], "p1": [x, y, trunk_top],
             "r": trunk_r, "mat": "bark"}
    crown = {"type": "sphere", "c": [x, y, trunk_top], "r": crown_r, "mat": "frond"}
    return [trunk, crown]


def _trunk_only(x, y, trunk_top, trunk_r=TRUNK_R):
    return {"type": "capsule", "p0": [x, y, GROUND_Z], "p1": [x, y, trunk_top],
            "r": trunk_r, "mat": "bark"}


# --- reference route planner (A*) -------------------------------------------
def plan_route(trunks_xy, start_xy, goal_xy, clear=PLAN_CLEAR, res=PLAN_RES, walls=()):
    """A* at the flight altitude (2D, since trunks are vertical columns): find a feasible (x, y)
    route from start to goal weaving between trunks AND through the baffle openings, or None if
    fully blocked. A cell is blocked if within TRUNK_R+clear of any trunk centre, or inside a
    baffle wall (+ a drone-radius margin). `walls` = list of (cx, cy, hx, hy) AABBs."""
    import heapq
    x0, x1 = 0.0, LENGTH
    y0, y1 = -BAND_Y, BAND_Y
    nx = int((x1 - x0) / res) + 1
    ny = int((y1 - y0) / res) + 1
    blocked = np.zeros((nx, ny), dtype=bool)
    br = TRUNK_R + clear
    for (tx, ty) in trunks_xy:
        ix0 = max(0, int((tx - br - x0) / res)); ix1 = min(nx - 1, int((tx + br - x0) / res))
        iy0 = max(0, int((ty - br - y0) / res)); iy1 = min(ny - 1, int((ty + br - y0) / res))
        for ix in range(ix0, ix1 + 1):
            cx = x0 + ix * res
            for iy in range(iy0, iy1 + 1):
                cy = y0 + iy * res
                if (cx - tx) ** 2 + (cy - ty) ** 2 < br * br:
                    blocked[ix, iy] = True
    mg = clear                                      # drone-radius margin around the walls
    for (wcx, wcy, whx, why) in walls:
        ix0 = max(0, int((wcx - whx - mg - x0) / res)); ix1 = min(nx - 1, int((wcx + whx + mg - x0) / res))
        iy0 = max(0, int((wcy - why - mg - y0) / res)); iy1 = min(ny - 1, int((wcy + why + mg - y0) / res))
        blocked[ix0:ix1 + 1, iy0:iy1 + 1] = True

    def cell(px, py):
        return (int(round((px - x0) / res)), int(round((py - y0) / res)))

    si, sj = cell(*start_xy)
    gi, gj = cell(*goal_xy)
    si = min(max(si, 0), nx - 1); sj = min(max(sj, 0), ny - 1)
    gi = min(max(gi, 0), nx - 1); gj = min(max(gj, 0), ny - 1)
    if blocked[si, sj] or blocked[gi, gj]:
        return None
    nbrs = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
            (1, 1, 1.414), (1, -1, 1.414), (-1, 1, 1.414), (-1, -1, 1.414)]
    openh = [(0.0, si, sj)]
    g = {(si, sj): 0.0}
    came = {}
    goal_c = (gi, gj)
    while openh:
        _, ci, cj = heapq.heappop(openh)
        if (ci, cj) == goal_c:
            path = [(ci, cj)]
            while (ci, cj) in came:
                ci, cj = came[(ci, cj)]
                path.append((ci, cj))
            path.reverse()
            return [(x0 + i * res, y0 + j * res) for (i, j) in path]
        for di, dj, w in nbrs:
            ni, nj = ci + di, cj + dj
            if not (0 <= ni < nx and 0 <= nj < ny) or blocked[ni, nj]:
                continue
            ng = g[(ci, cj)] + w
            if ng < g.get((ni, nj), 1e18):
                g[(ni, nj)] = ng
                came[(ni, nj)] = (ci, cj)
                h = abs(ni - gi) + abs(nj - gj)
                heapq.heappush(openh, (ng + h, ni, nj))
    return None


def _seg_clear(a, b, trunks_xy, clear, walls=(), step=0.15):
    """True if the straight segment a->b stays > TRUNK_R+clear from every trunk AND outside every
    baffle wall (+ margin) -- line of sight for the route smoother."""
    ax, ay = a; bx, by = b
    d = float(np.hypot(bx - ax, by - ay))
    n = max(1, int(d / step))
    br2 = (TRUNK_R + clear) ** 2
    for k in range(n + 1):
        t = k / n
        px, py = ax + (bx - ax) * t, ay + (by - ay) * t
        for (tx, ty) in trunks_xy:
            if (px - tx) ** 2 + (py - ty) ** 2 < br2:
                return False
        for (wcx, wcy, whx, why) in walls:
            if abs(px - wcx) < whx + clear and abs(py - wcy) < why + clear:
                return False
    return True


def shortcut_route(route, trunks_xy, clear, walls=()):
    """String-pull the jagged A* grid path into a few long, collision-free segments so a drone
    can actually FOLLOW it smoothly (a raw 8-connected path zig-zags and gets clipped)."""
    if len(route) <= 2:
        return route
    out = [route[0]]
    i = 0
    while i < len(route) - 1:
        j = len(route) - 1
        while j > i + 1 and not _seg_clear(route[i], route[j], trunks_xy, clear, walls):
            j -= 1
        out.append(route[j])
        i = j
    return out


# --- the forest course ------------------------------------------------------
def make_forest(seed=0, density=1.0, n_gates=14, slot_half=2.0,
                weave_y=2.2, weave_z=1.9, period_y=25.0, period_z=18.0, banking=False,
                background=True):
    """Build a DISTRIBUTED palm forest (with a reference photo): palms spread
    evenly across the WHOLE corridor at realistic root-limited spacing, so MANY routes exist and
    the drone CHOOSES its own -- NO pre-carved lane. Trunks all run to the (low, sealed) ceiling
    so there is no fly-over cheat; fronds sit above as canopy and the drone weaves horizontally
    between the trunks below. Returns obstacles, start, goal, ground/ceil, an A*-planned `route`
    (waypoints) proving feasibility, and `path(x) -> (y, z)` (the scripted reference follows it).

    `density` scales how many palms fill the corridor (1.0 = the calibrated default ~209 trees;
    >1 packs them tighter -> harder, <1 thins them -> easier). Grid pitch and min-separation scale
    as 1/sqrt(density) so tree COUNT scales ~linearly with `density`. Used to build a benchmark that
    spans a RANGE of difficulties, not one fixed density.

    `slot_half / n_gates / weave_* / period_* / banking / background` are kept for signature
    compatibility but unused (the forest is now a jittered grid + planner, not gates)."""
    rng = np.random.default_rng(seed)
    obstacles = []
    # density knob: tighter grid + smaller root-spacing => more trees (~linear in `density`).
    _dscale = float(density) ** 0.5
    sx = GRID_SX / _dscale
    sy = GRID_SY / _dscale
    min_sep = MIN_SEP / _dscale
    # jitter GROWS with density. If it shrank with the cell (jit/_dscale) the
    # dense grid became too REGULAR -> a clean straight lane opened between aligned rows and every
    # high-density field failed the cheat-blocked screen. Scaling jitter UP keeps the dense forest
    # scrambled so no straight lane survives, at any density.
    jit = GRID_JIT * float(density)

    # 1) BAFFLE SLALOM (plan B): N solid full-height walls, opening on ALTERNATING edges. The
    #    flyable route must S-curve through them, so no straight line reaches the goal -> the
    #    straight-cheat is blocked by geometry, letting the forest itself be sparse + flyable.
    baffles = []       # (cx, cy, hx, hy) AABBs (for A* + rendering)
    bx0, bx1 = GATE_X0 + 6.0, LENGTH - 8.0
    for i in range(N_BAFFLES):
        bx = bx0 + (bx1 - bx0) * (i + 0.5) / N_BAFFLES
        side = 1 if (i % 2 == 0) else -1               # opening at +y edge, then -y edge, ...
        wall_cy = -side * BAFFLE_OPEN / 2.0            # wall fills all BUT the opening edge
        wall_hy = (2.0 * BAND_Y - BAFFLE_OPEN) / 2.0
        baffles.append((bx, wall_cy, BAFFLE_T, wall_hy))

    # 2) SPARSE staggered palms across the width (wide flyable gaps), but NOT on the baffle rows.
    placed_xy = []
    col = 0
    gx = GATE_X0
    while gx < LENGTH - 3.0:
        if any(abs(gx - bx) < 1.8 for (bx, _, _, _) in baffles):   # keep baffle rows clear
            col += 1; gx += sx; continue
        y_off = (sy * 0.5) if (col % 2) else 0.0
        gy = -BAND_Y + 0.5 + y_off
        while gy <= BAND_Y - 0.5 + 1e-6:
            px = gx + float(rng.uniform(-jit, jit))
            py = gy + float(rng.uniform(-jit, jit))
            gy += sy
            if any((px - ox) ** 2 + (py - oy) ** 2 < min_sep ** 2 for ox, oy in placed_xy):
                continue
            placed_xy.append((px, py))
        col += 1
        gx += sx

    # CENTRELINE BLOCKERS: plant trunks right on y~0 at a fixed pitch down the
    # whole corridor so the straight start->goal line is obstructed from the very first metres and
    # stays blocked -- a naive "aim at the goal" flyer hits a trunk immediately, and the drone is
    # forced to weave OFF the centreline right away. Fixed pitch (not density-scaled) so EVERY field,
    # sparse or dense, has a blocked centre. A* still routes around them (screened for feasibility).
    bx = GATE_X0
    while bx < LENGTH - 3.0:
        by = float(rng.uniform(-0.25, 0.25))          # essentially on the centreline
        if not any((bx - ox) ** 2 + (by - oy) ** 2 < min_sep ** 2 for ox, oy in placed_xy):
            placed_xy.append((bx, by))
        bx += BLOCKER_PITCH

    # NOTE on the straight-line cheat: most random seeds are dense enough that
    # NO clear y-lane crosses the field, so a straight flyer clips something. A minority of seeds
    # are "degenerate" -- dense everywhere EXCEPT one open lane, which is ALSO the only feasible
    # route (plugging that lane just makes A* infeasible, so it reopens). Rather than force every
    # seed, we CURATE at the eval level: run_dev / grade_survival use only seeds VERIFIED
    # cheat-blocked by verify_forest.py; degenerate open seeds (e.g. seed 1) are skipped.

    # 3) plan an honest route through the forest (A*).
    start_xy, goal_xy = (0.0, 0.0), (LENGTH, 0.0)
    for _ in range(40):
        route = plan_route(placed_xy, start_xy, goal_xy, clear=PLAN_CLEAR, walls=baffles)
        if route is not None:
            break
        if not placed_xy:
            break
        placed_xy.sort(key=lambda p: abs(p[1]))
        placed_xy.pop(0)
    if route is None:
        route = [start_xy, goal_xy]
    else:
        route = shortcut_route(route, placed_xy, PLAN_CLEAR, walls=baffles)   # smooth -> followable

    # 4) build the tall palms + the baffle walls (full height, visible)
    for (px, py) in placed_xy:
        obstacles += _palm(px, py, TRUNK_TOP)
    for (bcx, bcy, bhx, bhy) in baffles:
        obstacles.append({"type": "box", "c": [bcx, bcy, CEIL_Z / 2.0],
                          "h": [bhx, bhy, CEIL_Z / 2.0], "yaw": 0.0, "mat": "wall"})

    # 4) smooth the A* route into path(x): keep waypoints with strictly increasing x, interp y
    rx = [start_xy[0]]
    ry = [start_xy[1]]
    for (wx, wy) in route:
        if wx > rx[-1] + 1e-6:
            rx.append(wx); ry.append(wy)
    if rx[-1] < goal_xy[0]:
        rx.append(goal_xy[0]); ry.append(goal_xy[1])
    rx = np.asarray(rx); ry = np.asarray(ry)
    route_xyz = [[float(x), float(y), Z_FLY] for x, y in zip(rx, ry)]

    def path(x):
        return (float(np.interp(float(x), rx, ry)), Z_FLY)

    # --- seal the corridor (AUV sealed-pipe lesson), now VISIBLE so the boundary is clear ----
    midx, hx = LENGTH / 2.0, LENGTH / 2.0 + 6.0
    # side walls at y = +/-BAND_Y (close the sides; can't detour around the forest)
    for sy in (BAND_Y + 0.15, -(BAND_Y + 0.15)):
        obstacles.append({"type": "box", "c": [midx, sy, CEIL_Z / 2.0],
                          "h": [hx, 0.08, CEIL_Z / 2.0], "yaw": 0.0, "mat": "fence"})
    # ceiling (close the top; every trunk reaches it -> no fly-over layer). Translucent so the
    # top-down camera can still see the forest inside.
    obstacles.append({"type": "box", "c": [midx, 0.0, CEIL_Z],
                      "h": [hx, BAND_Y + 0.2, 0.08], "yaw": 0.0, "mat": "seal"})
    # NOTE: the ground plane itself is a real MuJoCo floor added in env.py (hitting it = crash);
    # it is not in this obstacle list (so the depth fan does not "see" the floor as a wall).

    start = [start_xy[0], start_xy[1], Z_FLY]
    goal = [goal_xy[0], goal_xy[1], Z_FLY]

    return {"obstacles": obstacles, "start": start, "goal": goal, "path": path,
            "route": route_xyz, "ground_z": GROUND_Z, "ceil_z": CEIL_Z, "length": LENGTH}
