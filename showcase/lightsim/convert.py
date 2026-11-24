"""Convert the simulated fields of the lightsim example chips into pic4D sample files.

    python showcase/lightsim/convert.py /path/to/lightsim/examples

Reads each example's ``results/`` and writes, next to the scene files here:

* ``<slug>/field.npz``  complex phasors in the pic4D field format (the grating coupler, the four inverse-design examples and
  the cat-state source);
* ``<slug>/weight.npz`` intensity maps |E|^2 (u, v, I): drawn directly in an intensity
  animation (quantum walk, bowtie nanoantenna) or used to weight the brightness of a
  path-driven one (ring sensor).

The example reports, with the solver set-ups, are at https://developer.su3labs.com/lightsim/ .
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from pic4d.field import save_field  # noqa: E402


def _decimate(E, u, v, max_u=900):
    s = max(1, int(np.ceil(len(u) / max_u)))
    return E[..., ::s, :], u[::s], v


def grating_coupler(src):
    f = np.load(src / "results" / "field.npz")
    ez, x, y = f["ez"], f["x"] * 1e6, f["y"] * 1e6
    i0 = int(np.argmin(abs(x - (-14.0))))
    j0 = int(np.argmin(abs(y - (-(2.0 + 0.8)))))  # T_BOX + 0.8, as in the example's figure
    u, v = x[i0 : i0 + ez.shape[0]], y[j0 : j0 + ez.shape[1]]
    E, u, v = _decimate(ez, u, v)
    return save_field(HERE / "grating-coupler" / "field.npz", E, u, v, float(f["wavelength"]) * 1e9,
                      plane="xz", offset_um=0.0, half=True)


def matrix_multiplier(src):
    f = np.load(src / "results" / "field.npz")
    m, n = np.meshgrid(np.arange(4), np.arange(4), indexing="ij")
    T = np.exp(-2j * np.pi * m * n / 4) / 2.0  # the target 4-point DFT of the design
    # the inputs x = T^dagger e_k send all the light to output k (linear superposition of
    # the four simulated single-input fields, as in the example's field figure)
    E = np.stack([np.tensordot(np.conj(T[k, :]), f["ey"], axes=1) for k in range(4)])
    labels = [f"inputs T^dagger e_{k + 1}: all light to output {k + 1}" for k in range(4)]
    return save_field(HERE / "matrix-multiplier" / "field.npz", E, f["x"] * 1e6, f["y"] * 1e6, [1550.0] * 4,
                      labels=labels, half=True)


def wavelength_duplexer(src):
    f = np.load(src / "results" / "field.npz")
    return save_field(HERE / "wavelength-duplexer" / "field.npz", f["ey"], f["x"] * 1e6, f["y"] * 1e6,
                      np.asarray(f["wavelengths"]) * 1e3, half=True)


def receiving_antenna(src):
    f = np.load(src / "results" / "field.npz")
    return save_field(HERE / "receiving-antenna" / "field.npz", f["ey"], f["x"] * 1e6, f["z"] * 1e6,
                      float(f["wavelength"]) * 1e3, plane="xz", offset_um=0.0, half=True)


def matrix_multiplier_at_scale(src):
    f = np.load(src / "results" / "field.npz")
    ey = f["ey"]
    n = ey.shape[0]
    # the target of the design: the Haar-random unitary of design.target(n) (seed 2026 + n)
    rng = np.random.default_rng(2026 + n)
    z = (rng.standard_normal((n, n)) + 1j * rng.standard_normal((n, n))) / np.sqrt(2)
    q, r = np.linalg.qr(z)
    d = np.diag(r)
    T = q * (d / np.abs(d))[None, :]
    outs = [0, n // 3, 2 * n // 3, n - 1]
    # the inputs x = T^dagger e_k send all the light to output k (superposition of the n single-input fields)
    E = np.stack([np.tensordot(np.conj(T[k, :]), ey, axes=1) for k in outs])
    labels = [f"inputs T^dagger e_{k + 1}: all light to output {k + 1}" for k in outs]
    E, u, v = _decimate(E, f["x"] * 1e6, f["y"] * 1e6)
    return save_field(HERE / "matrix-multiplier-at-scale" / "field.npz", E, u, v, [1550.0] * len(outs), labels=labels, half=True)


def cat_state_source(src):
    # E_y in the Si3N4 film at 1550 nm, 3-D FDTD of the traced polygons (every second sample of
    # the 25 nm mesh), for light in each of the two squeezed inputs
    f = np.load(src / "results" / "field_final_25nm.npz")
    E = np.asarray(f["Ey"])[:2]
    labels = ["light in squeezed input 1", "light in squeezed input 2"]
    E, u, v = _decimate(E, f["x_um"], f["y_um"])
    return save_field(HERE / "cat-state-source" / "field.npz", E, u, v, [1550.0] * 2, labels=labels, half=True)


def _weight(path, u, v, I):
    I = np.asarray(I, dtype=np.float32)
    np.savez_compressed(path, u=np.asarray(u, float), v=np.asarray(v, float), I=I.astype(np.float16))
    return path


#: start of the coupled region in the quantum-walk layout: 50 um facet lead + 2811 um fan-in
Q3_ARRAY_X_UM = 2861.0


def quantum_walk(src):
    # |E|^2 in the core plane of the simulated 350 um array, central guide excited; the
    # simulation starts its x axis at the start of the coupled region
    f = np.load(src / "results" / "field_walk.npz")
    I = np.asarray(f["intensity"], dtype=np.float32)
    return _weight(HERE / "quantum-walk" / "weight.npz", f["x_um"] + Q3_ARRAY_X_UM, f["y_um"], I)


def fusion_circuit(src):
    # |E|^2 in the SiN core plane of the whole fusion circuit (rail a1 excited); the
    # simulation uses the layout's coordinates
    f = np.load(src / "results" / "sin" / "field.npz")
    I = np.asarray(f["intensity"], dtype=np.float32)
    return _weight(HERE / "fusion-circuit" / "weight.npz", f["x_um"], f["y_um"], I)


def ring_sensor(src):
    e2 = np.load(src / "results" / "field.npy")
    a = np.load(src / "results" / "field_axes.npz")
    return _weight(HERE / "ring-sensor" / "weight.npz", a["x"] * 1e6, a["y"] * 1e6, e2 / e2.max())


def bowtie_nanoantenna(src):
    g = np.load(src / "results" / "grid.npz")
    m = np.load(src / "results" / "map_xy.npy")
    R = json.loads((src / "results" / "results.json").read_text())
    o = R["bowtie"][-1]["xy_origin"]
    u = g["x"][o[0] : o[0] + m.shape[0]] * 1e6
    v = g["y"][o[1] : o[1] + m.shape[1]] * 1e6
    return _weight(HERE / "bowtie-nanoantenna" / "weight.npz", u, v, m / m.max())


if __name__ == "__main__":
    root = Path(sys.argv[1])
    for slug, fn in (("grating-coupler", grating_coupler), ("matrix-multiplier", matrix_multiplier), ("wavelength-duplexer", wavelength_duplexer), ("receiving-antenna", receiving_antenna), ("matrix-multiplier-at-scale", matrix_multiplier_at_scale), ("cat-state-source", cat_state_source),
                     ("fusion-circuit", fusion_circuit), ("quantum-walk", quantum_walk), ("ring-sensor", ring_sensor), ("bowtie-nanoantenna", bowtie_nanoantenna)):
        p = fn(root / slug)
        print(f"{slug:20s} -> {Path(p).relative_to(HERE)}  {Path(p).stat().st_size / 1e3:.0f} kB")
