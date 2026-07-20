import numpy as np

def policy(obs):
    for k in ['pos', 'vel', 'forward', 'up', 'angvel', 'goal', 'to_goal']:
        if k in obs: obs[k] = np.array(obs[k], dtype=float)

    HOVER = 3.25
    left = np.cross(obs['up'], obs['forward'])

    num_bins = 121
    angles = np.linspace(-60, 60, num_bins)
    dists = np.full(num_bins, 10.0)
    
    if obs.get('depth'):
        for d in obs['depth']:
            if abs(d['el']) < 30:
                dist = max(d['dist'], 0.1)
                az = d['az']
                spread = np.degrees(np.arcsin(min(0.6 / dist, 1.0)))
                mask = np.abs(angles - az) <= spread
                dists[mask] = np.minimum(dists[mask], dist)

    smoothed = np.copy(dists)
    for _ in range(3):
        for i in range(1, num_bins - 1):
            smoothed[i] = 0.25 * dists[i-1] + 0.5 * dists[i] + 0.25 * dists[i+1]
        dists = np.copy(smoothed)

    target_heading_deg = np.degrees(obs['heading_error'])
    
    best_score = -np.inf
    best_az = 0.0
    
    for i, az in enumerate(angles):
        d = smoothed[i]
        
        score = min(d, 6.0) * 15.0
        score -= abs(az - target_heading_deg) * 1.5
        score -= abs(az) * 0.5
        
        if d < 1.2:
            score -= 1000.0
            
        if score > best_score:
            best_score = score
            best_az = az

    target_az_rad = np.radians(best_az)
    center_d = smoothed[np.argmin(np.abs(angles - best_az))]
    
    # Very conservative speed
    v_mag = np.clip(center_d * 0.8, 0.5, 4.0)
    
    min_front_d = np.min(smoothed[np.abs(angles) < 20])
    if min_front_d < 2.5:
        v_mag = min(v_mag, 1.5)
        
    target_vx_body = v_mag * np.cos(target_az_rad)
    target_vy_body = v_mag * np.sin(target_az_rad)
    
    vx_body = np.dot(obs['vel'], obs['forward'])
    vy_body = np.dot(obs['vel'], left)
    
    acc_x_body = 2.0 * (target_vx_body - vx_body)
    acc_y_body = 2.5 * (target_vy_body - vy_body)

    yaw_rad = np.arctan2(obs['forward'][1], obs['forward'][0])
    fwd_xy = np.array([np.cos(yaw_rad), np.sin(yaw_rad), 0.0])
    left_xy = np.array([-np.sin(yaw_rad), np.cos(yaw_rad), 0.0])

    acc_x_world = acc_x_body * fwd_xy[0] + acc_y_body * left_xy[0]
    acc_y_world = acc_x_body * fwd_xy[1] + acc_y_body * left_xy[1]

    acc_z = 4.0 * obs['height_error'] - 2.5 * obs['vel'][2]

    acc_world = np.array([acc_x_world, acc_y_world, acc_z + 9.81])

    max_tilt_tan = 0.6 # ~31 degrees max tilt for stability
    F_z_att = max(acc_world[2], 2.0)
    acc_horiz_mag = np.hypot(acc_world[0], acc_world[1])
    if acc_horiz_mag > F_z_att * max_tilt_tan:
        scale = (F_z_att * max_tilt_tan) / acc_horiz_mag
        acc_world[0] *= scale
        acc_world[1] *= scale

    target_up = acc_world / np.linalg.norm(acc_world)

    up_z = max(obs['up'][2], 0.2)
    desired_thrust_acc = acc_world[2] / up_z
    
    if obs['pos'][2] < 0.6 or obs['up'][2] < 0.4:
        desired_thrust_acc = 18.0
        target_up = np.array([0.0, 0.0, 1.0])

    C = np.clip((desired_thrust_acc / 9.81) * HOVER, 0.0, 10.0)

    torque_world = np.cross(obs['up'], target_up)
    if np.linalg.norm(torque_world) < 1e-4 and np.dot(obs['up'], target_up) < 0:
        torque_world = obs['forward']
        
    err_roll = np.dot(torque_world, obs['forward'])
    err_pitch = np.dot(torque_world, left)

    target_yaw_rate = np.clip(target_az_rad * 2.0, -2.0, 2.0)

    kp_att = 18.0
    kd_att = 4.5
    kp_yaw = 10.0

    p_roll = np.clip(kp_att * err_roll - kd_att * obs['angvel'][0], -5.0, 5.0)
    p_pitch = np.clip(kp_att * err_pitch - kd_att * obs['angvel'][1], -5.0, 5.0)
    p_yaw = np.clip(kp_yaw * (target_yaw_rate - obs['angvel'][2]), -4.0, 4.0)

    t1 = C - p_roll + p_pitch - p_yaw
    t2 = C + p_roll + p_pitch + p_yaw
    t3 = C + p_roll - p_pitch - p_yaw
    t4 = C - p_roll - p_pitch + p_yaw

    return [np.clip(t1, 0, 13), np.clip(t2, 0, 13), np.clip(t3, 0, 13), np.clip(t4, 0, 13)]
