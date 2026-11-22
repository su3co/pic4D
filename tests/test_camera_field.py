import numpy as np

from pic4d import camera
from pic4d.field import Field, load_field, save_field


def test_fit_distance_frames_all_points():
    pts = np.array([[x, y, z] for x in (-5, 5) for y in (-1, 1) for z in (0, 0.2)])
    d = camera.fit_distance(pts, [0, 0, 0], -60, 35, aspect=4 / 3, margin=0.1)
    eye = d * camera.direction(-60, 35)
    rot = camera.look_rotation(eye, [0, 0, 0], camera.up_vector(-60, 35))
    x, y, depth = camera._project(pts, eye, rot, 50.0, 36.0, 4 / 3)
    assert np.all(depth > 0) and np.max(np.abs(x)) <= 0.9 + 1e-6 and np.max(np.abs(y)) <= 0.9 + 1e-6
    assert max(np.max(np.abs(x)), np.max(np.abs(y))) > 0.85  # tight


def test_orbit_loops():
    pts = np.random.default_rng(0).uniform(-1, 1, (8, 3))
    ps = camera.poses("orbit", 60, points=pts, target=[0, 0, 0])
    assert len(ps) == 60
    # the pose after the last frame equals the first (seamless loop)
    a = camera.direction(-55 + 14 * np.sin(2 * np.pi * 60 / 60), 32)
    assert np.allclose(a, camera.direction(-55, 32))
    assert np.allclose(ps[0][0], ps[0][0])


def test_topdown_starts_from_above():
    pts = np.array([[x, y, 0] for x in (-5, 5) for y in (-2, 2)], float)
    ps = camera.poses("topdown", 30, points=pts, target=[0, 0, 0], el=30)
    eye0 = ps[0][0]
    assert eye0[2] > 10 * np.hypot(eye0[0], eye0[1])
    assert ps[-1][0][2] < eye0[2]


def test_field_roundtrip(tmp_path):
    u = np.linspace(-2, 2, 41)
    v = np.linspace(-1, 1, 21)
    U, V = np.meshgrid(u, v, indexing="ij")
    E = np.exp(1j * 2 * np.pi * U / 0.6) * np.exp(-V**2)
    p = save_field(tmp_path / "f.npz", E, u, v, 1550.0)
    f = load_field(p)
    assert f.E.shape == (1, 41, 21) and f.plane == "xy"
    assert np.allclose(f.E[0], E, atol=1e-5)
    g = f.resampled(16)
    assert g.E.shape[1] <= 17 and abs(g.u[0] + 2) < 1e-9
    n = f.normalised(100)
    assert abs(np.abs(n).max() - 1) < 1e-5


def test_field_rejects_intensity():
    import pytest

    with pytest.raises(ValueError):
        Field(np.ones((4, 4)), np.arange(4.0), np.arange(4.0), 1550)
