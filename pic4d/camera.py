"""Camera framing and moves (pure numpy; Blender only receives the per-frame poses).

Angles: azimuth is measured in the layout plane from +x towards +y, elevation above the
plane; the camera sits at ``target + d * (cos el cos az, cos el sin az, sin el)``.
A pose is (eye, target, up, lens_mm). :func:`look_rotation` turns it into the rotation
matrix of a Blender camera (which looks down its local -Z with +Y up).

Moves (``move=``):

* ``orbit``     a slow swing of the azimuth, ``az0 +- amplitude`` (sinusoidal, loops);
* ``turntable`` a full turn (loops);
* ``topdown``   from straight above to the perspective view, then a slow drift;
* ``flyalong``  low over the chip along the light path (needs ``path``);
* ``push``      a slow dolly-in on the target;
* ``still``     fixed.
"""

from __future__ import annotations

import math

import numpy as np

__all__ = ["direction", "up_vector", "look_rotation", "fit_distance", "poses", "MOVES", "LOOPABLE"]

MOVES = ("orbit", "turntable", "topdown", "flyalong", "push", "still")
LOOPABLE = {"orbit", "turntable", "still"}


def direction(az_deg: float, el_deg: float) -> np.ndarray:
    """Unit vector from the target to the camera."""
    a, e = math.radians(az_deg), math.radians(el_deg)
    return np.array([math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)])


def up_vector(az_deg: float, el_deg: float) -> np.ndarray:
    """Camera up, in the vertical plane of the view; well defined straight from above
    (it then points away from the azimuth the camera came from)."""
    a, e = math.radians(az_deg), math.radians(el_deg)
    return np.array([-math.sin(e) * math.cos(a), -math.sin(e) * math.sin(a), math.cos(e)])


def look_rotation(eye, target, up) -> np.ndarray:
    """3x3 rotation whose columns are the camera's local X (right), Y (up), Z (backwards)."""
    eye, target, up = (np.asarray(v, dtype=float) for v in (eye, target, up))
    f = target - eye
    f /= np.linalg.norm(f)
    r = np.cross(f, up)
    if np.linalg.norm(r) < 1e-9:  # up parallel to the view: pick any perpendicular
        r = np.cross(f, [0.0, 1.0, 0.0] if abs(f[1]) < 0.9 else [1.0, 0.0, 0.0])
    r /= np.linalg.norm(r)
    u = np.cross(r, f)
    return np.stack([r, u, -f], axis=1)


def _project(points, eye, rot, lens_mm, sensor_mm, aspect):
    """Normalised image coordinates (-1..1 across the frame width/height) of world points."""
    p = (np.asarray(points) - eye) @ rot  # camera coordinates
    depth = -p[:, 2]
    half_w = sensor_mm / (2 * lens_mm)  # tan of the half horizontal field of view
    x = p[:, 0] / np.maximum(depth, 1e-9) / half_w
    y = p[:, 1] / np.maximum(depth, 1e-9) / (half_w / aspect)
    return x, y, depth


def fit_distance(points, target, az_deg, el_deg, *, lens_mm=50.0, sensor_mm=36.0, aspect=16 / 9,
                 margin=0.08, up=None) -> float:
    """Smallest camera distance at which all `points` are inside the frame, leaving
    `margin` (fraction of the half-frame) free on every side."""
    target = np.asarray(target, dtype=float)
    d_dir = direction(az_deg, el_deg)
    up = up_vector(az_deg, el_deg) if up is None else up
    lim = 1.0 - margin
    ext = np.max(np.linalg.norm(np.asarray(points) - target, axis=1))
    lo, hi = 1e-3 * ext, 1e3 * ext
    for _ in range(80):
        mid = math.sqrt(lo * hi)
        eye = target + mid * d_dir
        x, y, depth = _project(points, eye, look_rotation(eye, target, up), lens_mm, sensor_mm, aspect)
        ok = np.all(depth > 0) and np.all(np.abs(x) <= lim) and np.all(np.abs(y) <= lim)
        hi, lo = (mid, lo) if ok else (hi, mid)
    return hi


def _smooth(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _resample_path(path, n=400, window=0.06):
    """Arc-length resampling and smoothing of a polyline (k, 3)."""
    path = np.asarray(path, dtype=float)
    seg = np.linalg.norm(np.diff(path, axis=0), axis=1)
    s = np.concatenate([[0], np.cumsum(seg)])
    if s[-1] <= 0:
        return np.repeat(path[:1], n, 0), 0.0
    ss = np.linspace(0, s[-1], n)
    res = np.stack([np.interp(ss, s, path[:, k]) for k in range(path.shape[1])], 1)
    w = max(1, int(window * n))
    ker = np.ones(2 * w + 1) / (2 * w + 1)
    padded = np.concatenate([np.repeat(res[:1], w, 0), res, np.repeat(res[-1:], w, 0)])
    sm = np.stack([np.convolve(padded[:, k], ker, mode="valid") for k in range(res.shape[1])], 1)
    return sm, float(s[-1])


def poses(move: str, n_frames: int, *, points, target, az=-55.0, el=32.0, amplitude=14.0,
          lens_mm=50.0, sensor_mm=36.0, aspect=16 / 9, margin=0.08, path=None, height=None,
          zoom=1.0, drift=6.0):
    """Per-frame camera poses [(eye, target, up, lens_mm), ...] for a move.

    points: world-space points that must stay in frame (bounding-box corners).
    zoom > 1 frames tighter than the fitted distance (some points may leave the frame).
    path: (k, 3) world polyline for 'flyalong'. Loopable moves are periodic over n_frames
    (frame n_frames would equal frame 0)."""
    if move not in MOVES:
        raise ValueError(f"unknown move {move!r}; choose from {MOVES}")
    target = np.asarray(target, dtype=float)
    u = np.arange(n_frames) / n_frames  # loop fraction, 0 <= u < 1
    kw = dict(lens_mm=lens_mm, sensor_mm=sensor_mm, aspect=aspect, margin=margin)
    out = []
    if move in ("orbit", "turntable", "still"):
        if move == "orbit":
            azs = az + amplitude * np.sin(2 * np.pi * u)
            els = el + 0.25 * amplitude * np.sin(4 * np.pi * u) * (el < 80)
        elif move == "turntable":
            azs = az + 360.0 * u
            els = np.full(n_frames, el)
        else:
            azs, els = np.full(n_frames, az), np.full(n_frames, el)
        sample = np.linspace(0, 1, 24, endpoint=False)
        if move == "orbit":
            ds = [fit_distance(points, target, az + amplitude * math.sin(2 * math.pi * s),
                               el + 0.25 * amplitude * math.sin(4 * math.pi * s) * (el < 80), **kw) for s in sample]
        elif move == "turntable":
            ds = [fit_distance(points, target, az + 360 * s, el, **kw) for s in sample]
        else:
            ds = [fit_distance(points, target, az, el, **kw)]
        d = max(ds) / zoom
        for a, e in zip(azs, els):
            out.append((target + d * direction(a, e), target, up_vector(a, e), lens_mm))
        return out
    if move == "topdown":
        k = 0.45  # fraction of the clip spent coming down
        d_top = fit_distance(points, target, az, 89.0, **kw) / zoom
        d_end = fit_distance(points, target, az + drift, el, **kw) / zoom
        for t in u:
            s = _smooth(t / k)
            e = 89.0 + (el - 89.0) * s
            a = az - 25.0 * (1 - s) + drift * (t - k) / (1 - k) * (t > k)
            d = d_top + (d_end - d_top) * s
            out.append((target + d * direction(a, e), target, up_vector(a, e), lens_mm))
        return out
    if move == "push":
        d0 = fit_distance(points, target, az, el, **kw) / zoom
        for t in u:
            s = _smooth(t)
            a = az + drift * (s - 0.5)
            d = d0 * (1.0 - 0.35 * s)
            out.append((target + d * direction(a, el), target, up_vector(a, el), lens_mm))
        return out
    # flyalong
    if path is None or len(path) < 2:
        raise ValueError("flyalong needs a light path")
    sm, length = _resample_path(path)
    ext = float(np.max(np.ptp(np.asarray(points), axis=0)))
    h = height if height is not None else 0.06 * ext
    look = 0.12 * length
    for t in u:
        s = 0.05 + 0.85 * _smooth(t)
        i = int(s * (len(sm) - 1))
        j = min(len(sm) - 1, int((s * length + look) / max(length, 1e-9) * (len(sm) - 1)))
        p, q = sm[i], sm[j]
        back = q - p
        back[2] = 0.0
        nb = np.linalg.norm(back)
        back = back / nb if nb > 1e-9 else np.array([1.0, 0.0, 0.0])
        eye = p - 0.6 * look * back + np.array([0, 0, h])
        tgt = q
        f = tgt - eye
        f /= np.linalg.norm(f)
        up = np.array([0.0, 0.0, 1.0]) - f * f[2]
        out.append((eye, tgt, up / np.linalg.norm(up), lens_mm))
    return out
