"""Fixed forest-flight simulation for the Fugu drone-controller benchmark.

WE own this file. It is identical for every model. A model only writes a
`policy(obs) -> action` function (see task.md); we drop it into this env, run a
fixed-seed episode, score it, and optionally render an mp4.

The vehicle is the Skydio X2 quadrotor (assets/skydio_x2/x2.xml, MuJoCo Menagerie,
Apache-2.0). It is a full 6-DOF free body under GRAVITY with FOUR rotor thrusters
(one <motor> per rotor). It is open-loop UNSTABLE: all rotors push along body +z, so
the controller must actively keep it upright AND navigate. This is the deliberate hard
version — no stabilized inner loop is provided.

Control abstraction the model sees:
    obs    = dict (3D pose + full IMU: quat / angvel / vel, goal-relative info, battery,
             a forward depth fan)
    action = 4 rotor thrust commands, each in [0, CTRL_MAX]  (thrust1..thrust4)
The model must synthesize collective (climb) + differential (roll/pitch/yaw torque ->
tilt -> translate) from 4 numbers, thread a palm forest WITHOUT touching anything (the
FIRST contact ends the run), and finish before the battery dies.
"""
import os
import sys
import uuid
import numpy as np
import mujoco

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")
XML_PATH = os.path.join(ASSETS, "skydio_x2", "x2.xml")
sys.path.insert(0, HERE)
import field as F  # noqa: E402

# --- fixed simulation constants (identical for every model) ---
CONTROL_HZ = 100         # policy queried 100x / sim-second. A quadrotor needs a FAST loop to
                         # stabilize (attitude control); 10 Hz (the AUV rate) cannot hold it up.
SENSE_HZ = 25            # the depth fan refreshes at 25 Hz (realistic camera latency) and is
                         # cached between refreshes -> the 100 Hz control loop stays cheap.
MAX_SIM_TIME = 600.0     # the only time limit: a generous backstop so a drone that never makes
                         # progress (hovers / circles forever) still ends. With battery removed
                         #, failure = losing control (ground/tumble) or this
                         # timeout; 600 s is far more than a ~100 m weave needs, so a real flight is
                         # never cut off -- navigation, not the clock, is the wall.
COAST_AFTER_DEAD = 3.0   # sim seconds to let it fall/coast after the battery dies, then end
N_ROTORS = 4
CTRL_MIN, CTRL_MAX = 0.0, 13.0   # per-rotor thrust command range; MUST match x2.xml motor ctrlrange
HOVER_CTRL = 3.25        # ~per-rotor thrust that cancels gravity (from x2.xml hover keyframe)
GOAL_RADIUS = 2.0        # reached-goal distance (m)
COLLISION_COOLDOWN = 0.5  # sec the drone must be CLEAR of all obstacles before a new contact
                          # counts again -> one bump (even flickering contact) = ONE event.
COLLISION_PENALTY = 3.0   # score deducted per bump (12 -> 3). Bumps are unlimited,
                          # so at 12 a plow-through (12-16 bumps) drove the score below the floor and
                          # every bumpy reach tied at 60.5; 3 keeps them spread and above the floor
                          # while still ranking a clean flight clearly above a bumpy one.
DESTROY_FALL_MAX = 4.0    # (render only) max seconds to animate the dead-stick fall after a fatal
                          # obstacle hit when destroy_fall=True: thrust is cut, the wreck is flung off
                          # the tree it hit and tumbles across the floor until it settles. Cosmetic.
BATTERY = 1600.0         # VESTIGIAL: battery was REMOVED as a constraint. Energy
                         # is still tracked for info (`energy_used`) but never ends a run and is not
                         # scored. A ~100 m weave lasts seconds while a real drone flies for minutes,
                         # so energy is not a real limit for this task; failure = losing control
                         # (ground/tumble) or the MAX_SIM_TIME backstop. Kept only so `energy_used`
                         # has a reference scale; nothing depends on the exact value.
MAX_TILT_KILL = None     # (optional) kill if tilted past this; None = only physical contact kills


# --------------------------------------------------------------------------- HUD
def _hud_font(size):
    from PIL import ImageFont
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


_HUD_FONT = None
_HUD_FONT_BIG = None


def _haloed(draw, xy, text, font, fill=(0, 0, 0)):
    x, y = xy
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        draw.text((x + dx, y + dy), text, fill=(255, 255, 255), font=font)
    draw.text((x, y), text, fill=fill, font=font)


def _minimap_layout(obstacles, goal, length):
    """Top-down 2D footprint per obstacle (world x,y): trees (capsule/sphere) -> dots, the
    long thin side walls -> the strip's rails, the ceiling box -> skipped."""
    dots, rects = [], []
    rail_y = None
    for o in obstacles:
        if o["type"] == "box":
            cx, cy, _ = o["c"]
            hx, hy, _ = o["h"]
            if hx > 20.0:                     # a long corridor boundary
                if hy < 1.0:                  # thin in y -> side wall -> rail
                    rail_y = abs(cy)
                # else ceiling (full y, thin z) -> not meaningful top-down; skip
            else:
                rects.append((cx, cy, hx, hy))
        elif o["type"] == "sphere":
            dots.append((o["c"][0], o["c"][1]))
        elif o["type"] == "capsule":
            (x0, y0, _), (x1, y1, _) = o["p0"], o["p1"]
            dots.append((x0, y0))             # vertical trunk -> a dot
    return {"dots": dots, "rects": rects,
            "rail_y": rail_y if rail_y is not None else F.BAND_Y,
            "goal_xy": (goal[0], goal[1]), "length": length}


def _draw_minimap(d, W, H, mm, drone_xy):
    x0, x1 = int(W * 0.30), int(W - 24)
    yc, hh = H - 40, 26
    Lx = max(mm["length"], 1.0)
    ry = mm["rail_y"] + 0.6

    def sx(wx):
        return x0 + (x1 - x0) * float(np.clip(wx / Lx, 0.0, 1.0))

    def sy(wy):
        return yc - hh * float(np.clip(wy / ry, -1.0, 1.0))

    d.rectangle([x0 - 10, yc - hh - 8, x1 + 10, yc + hh + 8], fill=(14, 26, 16))
    d.rectangle([x0 - 10, yc - hh - 8, x1 + 10, yc + hh + 8], outline=(120, 165, 120))
    d.line([x0, sy(ry - 0.6), x1, sy(ry - 0.6)], fill=(150, 150, 150), width=2)
    d.line([x0, sy(-(ry - 0.6)), x1, sy(-(ry - 0.6))], fill=(150, 150, 150), width=2)
    green = (95, 175, 95)
    for (cx, cy) in mm["dots"]:
        px, py = sx(cx), sy(cy)
        d.ellipse([px - 2, py - 2, px + 2, py + 2], fill=green)
    for (cx, cy, hx, hy) in mm["rects"]:
        d.rectangle([sx(cx - hx), sy(cy + hy), sx(cx + hx), sy(cy - hy)], fill=green)
    gx, gy = mm["goal_xy"]
    d.ellipse([sx(gx) - 5, sy(gy) - 5, sx(gx) + 5, sy(gy) + 5], fill=(235, 45, 45))
    ax, ay = drone_xy
    px, py = sx(ax), sy(ay)
    d.ellipse([px - 5, py - 5, px + 5, py + 5], fill=(70, 200, 255), outline=(255, 255, 255))


def _draw_hud(frame, dist, alt, t, label=None, status="FLYING", hits=0,
              flash=False, minimap=None, drone_xy=None, hit_force=None, hit_fatal=False,
              destroy_limit=None):
    """Overlay: label (top-left), status (under it), HITS n (top-centre), distance/altitude/
    time (bottom-left). `flash` draws a red border on a bump frame. (No battery bar: battery is
    not a constraint.)"""
    global _HUD_FONT, _HUD_FONT_BIG
    from PIL import Image, ImageDraw
    if _HUD_FONT is None:
        _HUD_FONT = _hud_font(26)
        _HUD_FONT_BIG = _hud_font(32)
    img = Image.fromarray(frame)
    d = ImageDraw.Draw(img)
    if flash:
        fcol = flash if isinstance(flash, tuple) else (235, 40, 40)   # tuple = explicit colour; True = red
        for w in range(16):
            d.rectangle([w, w, img.width - 1 - w, img.height - 1 - w], outline=fcol)
    if label:
        _haloed(d, (18, 16), label, _HUD_FONT_BIG)
    scol = {"FLYING": (235, 235, 235), "REACHED": (60, 210, 90),
            "CRASHED": (235, 60, 50), "DESTROYED": (235, 50, 45)}.get(status, (235, 235, 235))
    _haloed(d, (18, 58 if label else 18), status, _HUD_FONT_BIG, fill=scol)
    # HITS counter (top-centre, SECOND row): dropped from y=18 to y=58 so it clears the top
    # title line (the model label spans the width at BIG font ~32px, y16-48) instead of
    # overprinting it ("deHITSy" overlap). bumps are counted (cost score) but never end the run.
    hx = img.width // 2 - 78
    htxt = f"HITS {hits}"
    hcol = (235, 60, 50) if hits > 0 else (235, 235, 235)
    _haloed(d, (hx, 58), htxt, _HUD_FONT_BIG, fill=hcol)
    # per-hit impact force, SIDE BY SIDE with HITS (not stacked below): shows the force vs the destroy
    # limit -> yellow "HIT 47 N < 200 N" if survivable, red "HIT 246 N > 200 N" on the fatal hit.
    if hit_force is not None and destroy_limit is not None:
        fcol = (235, 50, 45) if hit_fatal else (240, 200, 40)
        cmp = ">" if hit_fatal else "<"
        gap = int(d.textlength(htxt + "    ", font=_HUD_FONT_BIG))
        _haloed(d, (hx + gap, 58), f"HIT {hit_force:.0f} N {cmp} {destroy_limit:.0f} N",
                _HUD_FONT_BIG, fill=fcol)
    # persistent rule caption (small), so the 200 N threshold is stated throughout
    if destroy_limit is not None:
        _haloed(d, (18, img.height - 134),
                f"rule: an impact over {destroy_limit:.0f} N destroys the drone",
                _HUD_FONT, fill=(230, 175, 90))
    lines = [f"distance to goal: {dist:5.2f} m",
             f"altitude: {alt:5.2f} m",
             f"time: {t:5.2f} s"]
    y = img.height - 100
    for i, line in enumerate(lines):
        _haloed(d, (18, y + i * 32), line, _HUD_FONT)
    if minimap is not None and drone_xy is not None:
        _draw_minimap(d, img.width, img.height, minimap, drone_xy)
    return np.asarray(img)


def _oblique_camera(lookat, dist, elev_deg, azim_deg, name="cam", track=False):
    lookat = np.asarray(lookat, float)
    e = np.radians(elev_deg)
    a = np.radians(azim_deg)
    offset = dist * np.array([np.cos(e) * np.cos(a), np.cos(e) * np.sin(a), np.sin(e)])
    z_cam = offset / np.linalg.norm(offset)
    world_up = np.array([0.0, 0.0, 1.0])
    x_cam = np.cross(world_up, z_cam)
    x_cam /= np.linalg.norm(x_cam)
    y_cam = np.cross(z_cam, x_cam)
    xy = " ".join(f"{v:.5f}" for v in [*x_cam, *y_cam])
    if track:
        p = " ".join(f"{v:.5f}" for v in offset)
        return f'<camera name="{name}" mode="trackcom" pos="{p}" xyaxes="{xy}"/>'
    p = " ".join(f"{v:.5f}" for v in (lookat + offset))
    return f'<camera name="{name}" pos="{p}" xyaxes="{xy}"/>'


# --- materials + extra sensors injected into the stock x2.xml ---------------
_EXTRA_SENSORS = """  <sensor>
    <framepos    name="s_pos"    objtype="site" objname="imu"/>
    <framequat   name="s_quat"   objtype="site" objname="imu"/>
    <framelinvel name="s_linvel" objtype="site" objname="imu"/>
    <frameangvel name="s_angvel" objtype="site" objname="imu"/>
    <framexaxis  name="s_fwd"    objtype="site" objname="imu"/>
    <framezaxis  name="s_up"     objtype="site" objname="imu"/>
  </sensor>
"""

PALM_OBJ = os.path.join(ASSETS, "palm_tree_v2", "palm_tree.obj")
PALM_PNG = os.path.join(ASSETS, "palm_tree_v2", "diffuse.png")
PALM_NATIVE_H = 439.74    # palm_tree.obj native height (Y-up, base at y=0)

_MATERIALS = (
    '  <material name="bark"  rgba="0.42 0.30 0.18 1" specular="0.1" shininess="0.1"/>\n'
    '  <material name="frond" rgba="0.20 0.55 0.24 1" specular="0.1" shininess="0.15"/>\n'
    '  <material name="wall"  rgba="0.55 0.55 0.55 1" specular="0.1" shininess="0.1"/>\n'
    '  <material name="seal"  rgba="0.6 0.7 0.85 0.10" specular="0.2" shininess="0.3"/>\n'
    '  <material name="fence" rgba="0.55 0.72 0.90 0.28" specular="0.3" shininess="0.4"/>\n'
    # NOTE: x2.xml already defines an "invisible" material (rgba 0 0 0 0) -> reuse it.
    '  <material name="groundmat" rgba="0.28 0.42 0.20 1" specular="0.05" shininess="0.05"/>\n'
)

# bright daytime look: sky gradient + haze + a strong headlight
_VISUAL = ('  <visual>\n'
           '    <global offwidth="1920" offheight="1080"/>\n'
           '    <headlight diffuse="0.65 0.65 0.62" ambient="0.45 0.45 0.45" '
           'specular="0.15 0.15 0.15"/>\n'
           '    <rgba haze="0.82 0.90 1.0 1"/>\n'
           '  </visual>\n')
_SKY = ('  <texture name="sky" type="skybox" builtin="gradient" '
        'rgb1="0.45 0.66 0.92" rgb2="0.82 0.90 1.0" width="512" height="512"/>\n')


def _palm_mesh_assets(tops):
    """One <mesh> per distinct rounded palm height so trees of different heights render at
    the right size (MuJoCo mesh scale is per-asset). Returns (asset_xml, {rounded_top: name})."""
    keys = sorted({round(float(t)) for t in tops})
    xml, name_of = [], {}
    xml.append(f'<texture name="palmtex" type="2d" file="{PALM_PNG}"/>')
    xml.append('<material name="palmmat" texture="palmtex" specular="0.05" shininess="0.05"/>')
    for h in keys:
        s = (h + F.CROWN_R) / PALM_NATIVE_H          # native 440 -> ~(top+crown) metres
        nm = f"palm_{h}"
        name_of[h] = nm
        xml.append(f'<mesh name="{nm}" file="{PALM_OBJ}" scale="{s:.5f} {s:.5f} {s:.5f}"/>')
    return "\n".join(xml), name_of


def _build_model(goal, obstacles=(), start=(0.0, 0.0, 4.5)):
    """Load the stock x2.xml and inject: extra frame sensors, tree/wall materials, a ground
    plane, the goal site, the obstacle geoms, and the cameras. Written to a unique temp XML
    inside assets/skydio_x2/ so the mesh/texture relative paths still resolve."""
    with open(XML_PATH) as f:
        xml = f.read()

    # robust integrator (the drone is stiff under gravity + rotor forces)
    xml = xml.replace('<option timestep="0.01" density="1.225" viscosity="1.8e-5"/>',
                      '<option timestep="0.005" density="1.225" viscosity="1.8e-5" '
                      'integrator="implicitfast"/>')

    # materials + sky + daytime visual (incl. offscreen framebuffer) + extra sensors
    xml = xml.replace("</asset>", _MATERIALS + _SKY + "  </asset>")
    xml = xml.replace("</mujoco>", _VISUAL + _EXTRA_SENSORS + "</mujoco>")

    gx, gy, gz = goal
    inject = [
        # a daytime sun (directional) on top of the tracking spotlight already in x2.xml
        '<light directional="true" pos="0 0 40" dir="-0.25 -0.15 -1" '
        'diffuse="0.85 0.85 0.78" specular="0.2 0.2 0.2"/>',
        # a real, collidable ground plane (hitting it = crash) under the whole corridor
        f'<geom name="ground" type="plane" pos="{F.LENGTH / 2.0} 0 {F.GROUND_Z}" '
        f'size="{F.LENGTH / 2.0 + 8.0} {F.BAND_Y + 4.0} 0.1" material="groundmat" '
        f'contype="1" conaffinity="1"/>',
        f'<site name="goal" pos="{gx} {gy} {gz}" size="{GOAL_RADIUS}" '
        f'rgba="0.95 0.13 0.13 0.42" type="sphere"/>',
    ]

    # ground-rooted palms -> render as the palm MESH (visual) with the collision primitives
    # (trunk capsule + crown sphere) hidden underneath but still solid (AUV mesh/proxy split).
    ground_palms, hidden = [], set()
    for i, o in enumerate(obstacles):
        if (o.get("mat") == "bark" and o["type"] == "capsule"
                and abs(o["p0"][2] - F.GROUND_Z) < 0.05):
            x, y, top = o["p1"][0], o["p1"][1], o["p1"][2]
            ground_palms.append((x, y, top))
            hidden.add(i)
            for j, c in enumerate(obstacles):
                if (c.get("mat") == "frond" and c["type"] == "sphere"
                        and abs(c["c"][0] - x) < 1e-6 and abs(c["c"][1] - y) < 1e-6
                        and abs(c["c"][2] - top) < 1e-6):
                    hidden.add(j)
    if ground_palms:
        asset_xml, name_of = _palm_mesh_assets([t for _, _, t in ground_palms])
        xml = xml.replace("</asset>", asset_xml + "\n  </asset>")
        for (x, y, top) in ground_palms:
            nm = name_of[round(float(top))]
            inject.append(f'<geom type="mesh" mesh="{nm}" pos="{x} {y} {F.GROUND_Z}" '
                          f'euler="90 0 0" material="palmmat" contype="0" conaffinity="0" '
                          f'mass="0" group="2"/>')

    for i, o in enumerate(obstacles):
        inject.append(F.render_geom(o, f"obs_{i}", mat="invisible" if i in hidden else None))

    # chase cam: behind (-x) and above, looking forward along +x toward the goal
    inject.append(_oblique_camera((0, 0, 0), dist=3.6, elev_deg=20.0,
                                  azim_deg=200.0, name="follow", track=True))
    # near-top layout camera over the corridor midpoint (eye-check the forest / weave)
    midx = 0.5 * (start[0] + goal[0])
    inject.append(_oblique_camera((midx, 0.0, F.Z_MID), dist=max(24.0, gx * 1.1),
                                  elev_deg=74.0, azim_deg=270.0, name="topdown"))
    # near-top view framed so the corridor fills the frame edge-to-edge (start..goal, minimal sky)
    inject.append(_oblique_camera((midx, 0.0, F.Z_MID), dist=57.0,
                                  elev_deg=80.0, azim_deg=270.0, name="topzoom"))
    xml = xml.replace("</worldbody>", "\n".join(inject) + "\n</worldbody>")

    tmp = os.path.join(ASSETS, "skydio_x2", f"_scene_{uuid.uuid4().hex[:12]}.xml")
    with open(tmp, "w") as f:
        f.write(xml)
    try:
        model = mujoco.MjModel.from_xml_path(tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return model


# ----------------------------------------------------------------------- observe
def _sd(data, name):
    return data.sensor(name).data.copy()


def make_obs(data, goal, battery_frac, obstacles=None, obstacles_proxy=None,
             perception="depth", depth=None):
    pos = _sd(data, "s_pos")
    vel = _sd(data, "s_linvel")
    quat = _sd(data, "s_quat")
    fwd = _sd(data, "s_fwd")
    up = _sd(data, "s_up")
    angvel = _sd(data, "s_angvel")
    goal = np.asarray(goal, float)
    to_goal = goal - pos
    dist = float(np.linalg.norm(to_goal))

    # signed yaw error in the horizontal plane (+ = goal is to the drone's left)
    fh = fwd[:2]
    nfh = np.linalg.norm(fh)
    fh = fh / nfh if nfh > 1e-9 else np.array([1.0, 0.0])
    th = to_goal[:2]
    nth = np.linalg.norm(th)
    th = th / nth if nth > 1e-9 else np.array([1.0, 0.0])
    cross = fh[0] * th[1] - fh[1] * th[0]
    dot = float(np.clip(fh @ th, -1, 1))
    heading_error = float(np.arctan2(cross, dot))

    ob = {
        "pos": pos.tolist(),
        "vel": vel.tolist(),
        "quat": quat.tolist(),           # orientation [w, x, y, z]
        "forward": fwd.tolist(),
        "up": up.tolist(),
        "angvel": angvel.tolist(),
        "goal": goal.tolist(),
        "to_goal": to_goal.tolist(),
        "distance": dist,
        "heading_error": heading_error,  # rad, + = goal to the left
        "height_error": float(goal[2] - pos[2]),  # + = goal is above
        "battery": float(battery_frac),
        "time": float(data.time),
    }
    if perception == "depth":
        if depth is None:                    # not precomputed -> compute now (unthrottled)
            depth = F.depth_scan(obstacles or [], pos, fwd, up)
        ob.update({"depth": depth, "depth_range": F.DEPTH_RANGE})
    else:  # "map" ablation
        ob["obstacles"] = obstacles_proxy if obstacles_proxy is not None else []
    return ob


# ------------------------------------------------------------------------ run
def run_episode(policy, goal=(F.LENGTH, 0.0, F.Z_MID), obstacles=(), start=(0.0, 0.0, F.Z_MID),
                seed=0, render=False, fps=30, width=1280, height=720, label=None,
                camera="follow", perception="depth", destroy_force=None, destroy_fall=False):
    """Run one fixed episode with `policy`. Returns a result dict (+ frames if rendered).

    Collision model: TREE/WALL bumps are COUNTED but never end the run and have
    no limit (they only cost score); the only failures are losing control - touching the GROUND is an
    INSTANT crash, and tumbling is 'unstable' - or the MAX_SIM_TIME timeout. No battery limit.

    `destroy_force` (OPT-IN, default None = unchanged behavior): if set (N), an obstacle contact whose
    normal force exceeds it DESTROYS the drone (ends the run as a 'destroyed' fail) - the survivability
    rule. Rendering then flashes the border YELLOW on a survivable graze and RED on the fatal hit, shows
    a 'DESTROYED' status, and freezes on the death frame. Leave None for the standard grader.

    `destroy_fall` (render only, needs destroy_force set): on the fatal hit, instead of freezing on the
    death frame, CUT all rotor thrust and keep stepping the sim so gravity drops the wreck to the forest
    floor (a real physical fall, no explosion asset), then hold briefly where it lands. Does not affect
    scoring or any non-rendered run."""
    model = _build_model(goal, obstacles, start=start)
    data = mujoco.MjData(model)
    SIM_DT = float(model.opt.timestep)

    # spawn at `start`, level (identity quat). free joint qpos = [x,y,z, qw,qx,qy,qz]
    data.qpos[0:3] = [float(start[0]), float(start[1]), float(start[2])]
    data.qpos[3:7] = [1.0, 0.0, 0.0, 0.0]
    data.qvel[:] = 0.0
    mujoco.mj_forward(model, data)

    obstacles = list(obstacles)
    obstacles_proxy = [F.proxy(o) for o in obstacles]

    # geom bookkeeping for collision. Drone geoms = every geom on body "x2". Two classes of
    # solid world geom, handled DIFFERENTLY: obs_* trees/walls = bumps that are counted but never end
    # the run; the ground plane = an INSTANT crash on any contact.
    x2_body = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "x2")
    drone_geoms, obstacle_geoms, ground_geoms = set(), set(), set()
    for k in range(model.ngeom):
        nm = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, k)
        if model.geom_bodyid[k] == x2_body:
            drone_geoms.add(k)
        elif nm and nm.startswith("obs_"):
            obstacle_geoms.add(k)   # trees + walls: bumps are counted (cost score) but never end the run
        elif nm == "ground":
            ground_geoms.add(k)     # the floor: touching it is an INSTANT crash

    control_period = 1.0 / CONTROL_HZ
    sense_period = 1.0 / SENSE_HZ
    next_ctrl = 0.0
    next_sense = 0.0
    depth_cache = None
    action = np.full(N_ROTORS, HOVER_CTRL)
    n_steps = int(MAX_SIM_TIME / SIM_DT)
    skip = max(1, int(round(1.0 / (SIM_DT * fps))))

    frames = []
    renderer = mujoco.Renderer(model, height, width) if render else None
    mm_layout = _minimap_layout(obstacles, goal, abs(goal[0] - start[0]) or 1.0) \
        if (renderer is not None and obstacles) else None

    goal_v = np.asarray(goal, float)
    reached, reach_time = False, None
    min_dist = float("inf")
    energy, energy_reach = 0.0, None
    battery_dead, death_time = False, None
    crashed, crash_time, crash_step = False, None, -10 ** 9   # crashed = terminal FAIL (foul / ground)
    crash_reason = None                                       # "ground" | "unstable" (loss of control)
    collisions, touching, clear_time, last_col_step = 0, False, 0.0, -10 ** 9
    last_hit_force = 0.0        # strongest normal force in the most recent bump episode (N)
    peak_hit_force = 0.0        # strongest obstacle-contact normal force over the WHOLE run (N)
    path_length = 0.0
    prev_pos = _sd(data, "s_pos").copy()
    straight = float(np.linalg.norm(goal_v - prev_pos))

    def battery_frac():
        return 1.0   # battery removed as a constraint: obs always sees full

    def _touching(world_set):
        for ci in range(data.ncon):
            c = data.contact[ci]
            g1, g2 = c.geom1, c.geom2
            if (g1 in drone_geoms and g2 in world_set) or \
               (g2 in drone_geoms and g1 in world_set):
                return True
        return False

    _fbuf = np.zeros(6)

    def _obstacle_force():
        """Total obstacle-contact normal force this step (N). Only called when destroy_force is set."""
        tot = 0.0
        for ci in range(data.ncon):
            c = data.contact[ci]
            g1, g2 = c.geom1, c.geom2
            if (g1 in drone_geoms and g2 in obstacle_geoms) or (g2 in drone_geoms and g1 in obstacle_geoms):
                mujoco.mj_contactForce(model, data, ci, _fbuf)
                tot += abs(float(_fbuf[0]))
        return tot

    try:
        for i in range(n_steps):
            if data.time >= next_ctrl and not battery_dead:
                if perception == "depth" and data.time >= next_sense:
                    depth_cache = F.depth_scan(obstacles, _sd(data, "s_pos"),
                                               _sd(data, "s_fwd"), _sd(data, "s_up"))
                    next_sense += sense_period
                obs = make_obs(data, goal, battery_frac(), obstacles=obstacles,
                               obstacles_proxy=obstacles_proxy, perception=perception,
                               depth=depth_cache)
                try:
                    raw = policy(obs)
                    action = np.clip(np.asarray(raw, float).reshape(-1), CTRL_MIN, CTRL_MAX)
                    if action.shape[0] != N_ROTORS:
                        raise ValueError(f"policy returned {action.shape[0]} values, "
                                         f"expected {N_ROTORS}")
                except Exception as e:
                    return _result(False, None, min_dist, data.time, energy, energy_reach,
                                   battery_dead, crashed=False,
                                   error=f"policy raised: {type(e).__name__}: {e}",
                                   frames=frames, path_length=path_length, straight_line=straight)
                next_ctrl += control_period

            data.ctrl[:N_ROTORS] = action
            energy += float(np.abs(action).sum()) * SIM_DT   # tracked for info only (no battery limit)
            mujoco.mj_step(model, data)

            pos = _sd(data, "s_pos")
            if not np.all(np.isfinite(pos)):
                return _result(reached, reach_time, min_dist, data.time, energy, energy_reach,
                               battery_dead, crashed=True, crash_reason="unstable",
                               error="sim went unstable (non-finite)",
                               frames=frames, path_length=path_length, straight_line=straight)
            path_length += float(np.linalg.norm(pos - prev_pos))
            prev_pos = pos.copy()
            d = float(np.linalg.norm(goal_v - pos))
            min_dist = min(min_dist, d)

            # GROUND STRIKE = INSTANT crash: touching the floor ends the run
            # right away as a failure - no 3-bump tolerance, no waiting for the battery to die.
            # Guarded by `not battery_dead` so a battery-dead drone that simply falls and lands is
            # still attributed to the battery, not mislabelled a ground-crash.
            if not crashed and not battery_dead and _touching(ground_geoms):
                crashed, crash_time, crash_step, crash_reason = True, data.time, i, "ground"

            # tree/wall bumps are COUNTED (one event per contact episode) but NEVER end the run and
            # NEVER cap out (removed the 4-bump foul). A bump only costs score;
            # the drone can bump as often as it likes and go on, as long as it keeps control. The
            # ONLY ways to fail are losing control (unstable / hitting the GROUND) or the battery.
            if _touching(obstacle_geoms):
                f_now = _obstacle_force() if destroy_force is not None else 0.0
                if not touching:
                    collisions += 1
                    last_col_step = i
                    last_hit_force = 0.0            # start a new bump episode
                touching = True
                clear_time = 0.0
                last_hit_force = max(last_hit_force, f_now)   # strongest force in this bump so far
                peak_hit_force = max(peak_hit_force, f_now)    # strongest over the whole run
                # survivability rule (opt-in): a hit harder than destroy_force DESTROYS the drone
                if destroy_force is not None and not crashed and f_now > destroy_force:
                    crashed, crash_time, crash_step, crash_reason = True, data.time, i, "destroyed"
                    if render:
                        def _dead_frame(p, t):
                            renderer.update_scene(data, camera=camera)
                            img = renderer.render()
                            d_now = float(np.linalg.norm(goal_v - p))
                            return _draw_hud(img, d_now, float(p[2]), t, label,
                                             status="DESTROYED", hits=collisions, flash=(235, 40, 40),
                                             hit_force=f_now, hit_fatal=True, destroy_limit=destroy_force,
                                             minimap=mm_layout, drone_xy=(p[0], p[1]))
                        if destroy_fall:
                            # DEAD-STICK FALL + KNOCK: cut all thrust, then fling the wreck off the tree
                            # it just hit so it scatters and TUMBLES across the forest floor (real MuJoCo
                            # physics, no explosion asset). Render until it settles or the budget ends.
                            def _tree_xy(o):
                                c = o.get("c") or o.get("p0")
                                return float(c[0]), float(c[1])
                            dx, dy = float(pos[0]), float(pos[1])
                            if obstacles:
                                tx, ty = _tree_xy(min(obstacles, key=lambda o:
                                                      (_tree_xy(o)[0] - dx) ** 2 + (_tree_xy(o)[1] - dy) ** 2))
                                ox, oy = dx - tx, dy - ty
                                nrm = (ox * ox + oy * oy) ** 0.5 or 1.0
                                ox, oy = ox / nrm, oy / nrm
                            else:
                                ox, oy = 1.0, 0.0
                            kick = float(np.clip(f_now / 45.0, 4.0, 7.0))       # m/s flung outward off the tree
                            data.qvel[0:3] = [ox * kick, oy * kick, 1.5]        # outward + a small upward pop
                            data.qvel[3:6] = [oy * 14.0, -ox * 14.0, 9.0]       # hard tumble about a horiz axis
                            t_dead, fall_step, rest_hold = data.time, 0, 0
                            rest_need = int(0.25 / SIM_DT)
                            while data.time < t_dead + DESTROY_FALL_MAX:
                                data.ctrl[:N_ROTORS] = 0.0
                                mujoco.mj_step(model, data)
                                p = _sd(data, "s_pos")
                                if not np.all(np.isfinite(p)):
                                    break
                                if fall_step % skip == 0:
                                    frames.append(_dead_frame(p, data.time))
                                fall_step += 1
                                # settle test: on the ground AND barely moving for a beat
                                if _touching(ground_geoms) and float(np.linalg.norm(data.qvel[0:3])) < 0.3:
                                    rest_hold += 1
                                    if rest_hold > rest_need:
                                        break
                                else:
                                    rest_hold = 0
                            p = _sd(data, "s_pos")
                            if np.all(np.isfinite(p)):
                                frames.extend([_dead_frame(p, data.time)]
                                              * max(1, int(round(0.6 * fps))))
                        else:
                            frames.extend([_dead_frame(pos, data.time)]
                                          * max(1, int(round(1.5 * fps))))   # freeze on the death frame
            else:
                clear_time += SIM_DT
                if clear_time >= COLLISION_COOLDOWN:
                    touching = False
            if crash_reason == "destroyed":
                break

            if d < GOAL_RADIUS and not reached and not crashed:
                reached, reach_time, energy_reach = True, data.time, energy

            if render and i % skip == 0:
                status = "REACHED" if reached else "CRASHED" if crashed else "FLYING"
                recent_bump = (0 <= i - last_col_step < 200)
                if destroy_force is not None:
                    flash = (240, 200, 40) if recent_bump else None   # yellow: survivable graze
                else:
                    flash = recent_bump                                # legacy default red flash
                show_force = destroy_force is not None and recent_bump and last_hit_force > 0
                renderer.update_scene(data, camera=camera)
                img = renderer.render()
                frames.append(_draw_hud(img, d, float(pos[2]), data.time,
                                        label, status=status, hits=collisions,
                                        flash=flash,
                                        hit_force=(last_hit_force if show_force else None),
                                        hit_fatal=False, destroy_limit=destroy_force,
                                        minimap=mm_layout, drone_xy=(pos[0], pos[1])))

            # end conditions
            if crashed and not render:
                break
            if crashed and render and data.time > crash_time + 1.0:
                break
            if reached and not render:
                break
            if reached and render and data.time > reach_time + 1.2:
                break
            if battery_dead and data.time > death_time + COAST_AFTER_DEAD:
                break
    finally:
        if renderer is not None:
            renderer.close()

    return _result(reached, reach_time, min_dist, data.time, energy, energy_reach,
                   battery_dead, crashed=crashed, crash_reason=crash_reason,
                   collisions=collisions, hit_force=peak_hit_force, frames=frames,
                   path_length=path_length, straight_line=straight)


def _result(reached, reach_time, min_dist, sim_time, energy, energy_reach, battery_dead,
            crashed=False, crash_reason=None, collisions=0, hit_force=0.0, error=None, frames=None,
            path_length=0.0, straight_line=0.0):
    e_score = energy_reach if (reached and energy_reach is not None) else energy
    battery_remaining = 1.0   # battery not a constraint; kept in the dict as full
    success = bool(reached) and not crashed and error is None   # reached, still in control (bumps unlimited)
    # single death-mode label for diagnostics ("instrument HOW it dies", not just where):
    #   None (success) | "ground" (floor strike) | "unstable" (tumbled / sim blew up) |
    #   "error" (policy raised). Bumps never end a run, so there is no collision death mode.
    if success:
        failure_mode = None
    elif crashed:
        failure_mode = crash_reason or "crash"
    elif battery_dead:
        failure_mode = "battery_dead"
    elif error is not None:
        failure_mode = "error"
    else:
        failure_mode = None
    return {
        "reached_goal": bool(reached),
        "success": success,
        "crashed": bool(crashed),           # crashed = terminal fail: ground strike OR > MAX_COLLISIONS
        "crash_reason": crash_reason,       # "ground" | "foul" | "unstable" | None
        "failure_mode": failure_mode,       # unified death-mode label (see above)
        "collisions": int(collisions),
        "peak_hit_force": round(float(hit_force), 2),   # strongest obstacle impact over the run (N)
        "time_to_goal": reach_time,
        "min_distance": round(min_dist, 3),
        "sim_time": round(sim_time, 2),
        "battery_remaining": round(battery_remaining, 4),
        "energy_used": round(e_score, 2),
        "battery_dead": bool(battery_dead),
        "path_length": round(path_length, 3),
        "straight_line": round(straight_line, 3),
        "error": error,
        "_frames": frames or [],
    }


def score(result):
    """Higher is better, NON-OVERLAPPING tiers (any reach out-scores any non-reach):
      success  reached (bumps unlimited) -> 100 + 40*speed - 3*collisions, floored >60
      failed   lost control (ground/tumble) / timeout / error -> 60 * progress-to-goal   [0, 60]
    `speed` = clip(1 - time_to_goal / MAX_SIM_TIME): reaching SOONER scores higher, so a dawdler is
    penalised. Each bump costs COLLISION_PENALTY, so a CLEAN reach ranks above a bumpy one (bumps are
    unlimited and never end the run, but they still cost score). Battery was removed as a constraint
   , so quality among reachers = clean-vs-bumpy + fast-vs-slow. The success score
    is floored just above the failure band so even a very bumpy reach still beats a non-reach.
    `progress` = how far a failed run got, so flying farther before losing control ranks above
    dropping early."""
    if result["error"] and not result["reached_goal"]:
        sl = result["straight_line"] or 1.0
        progress = float(np.clip(1.0 - result["min_distance"] / sl, 0.0, 1.0))
        return round(60.0 * progress, 2)
    if result["success"]:
        ttg = result["time_to_goal"] or MAX_SIM_TIME
        speed = float(np.clip(1.0 - ttg / MAX_SIM_TIME, 0.0, 1.0))
        # battery removed: the old +40*battery term is gone; quality among
        # reachers is now clean-vs-bumpy (collision penalty) and fast-vs-slow (speed bonus).
        s = 100.0 + 40.0 * speed - COLLISION_PENALTY * result.get("collisions", 0)
        return round(max(60.5, s), 2)   # any reach stays above the [0,60] non-reach band
    sl = result["straight_line"] or 1.0
    progress = float(np.clip(1.0 - result["min_distance"] / sl, 0.0, 1.0))
    return round(60.0 * progress, 2)


def render_still(goal, obstacles=(), start=(0.0, 0.0, F.Z_MID), camera="topdown",
                 width=1280, height=720):
    """Render ONE frame of the static scene -- a fast layout preview."""
    model = _build_model(goal, obstacles, start=start)
    data = mujoco.MjData(model)
    data.qpos[0:3] = [float(start[0]), float(start[1]), float(start[2])]
    data.qpos[3:7] = [1.0, 0.0, 0.0, 0.0]
    mujoco.mj_forward(model, data)
    r = mujoco.Renderer(model, height, width)
    r.update_scene(data, camera=camera)
    img = r.render()
    r.close()
    return img
