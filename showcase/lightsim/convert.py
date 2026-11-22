"""Convert the simulated fields of the lightsim example chips into pic4D sample files.

    python showcase/lightsim/convert.py /path/to/lightsim/examples

Reads each example's ``results/`` and writes, next to the scene files here:

* ``<slug>/field.npz``  complex phasors in the pic4D field format (d3, i1, i2, i3);
* ``<slug>/weight.npz`` intensity maps |E|^2 (u, v, I) used only to weight the
  brightness of a path-driven or intensity animation (q3, s2, s3).

The example reports, with the solver set-ups, are at https://su3labs.com/lightsim/ .
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


def d3(src):
    f = np.load(src / "results" / "field.npz")
    ez, x, y = f["ez"], f["x"] * 1e6, f["y"] * 1e6
    i0 = int(np.argmin(abs(x - (-14.0))))
    j0 = int(np.argmin(abs(y - (-(2.0 + 0.8)))))  # T_BOX + 0.8, as in the example's figure
    u, v = x[i0 : i0 + ez.shape[0]], y[j0 : j0 + ez.shape[1]]
    E, u, v = _decimate(ez, u, v)
    return save_field(HERE / "d3-grating-coupler" / "field.npz", E, u, v, float(f["wavelength"]) * 1e9,
                      plane="xz", offset_um=0.0, half=True)


def i1(src):
    f = np.load(src / "results" / "field.npz")
    m, n = np.meshgrid(np.arange(4), np.arange(4), indexing="ij")
    T = np.exp(-2j * np.pi * m * n / 4) / 2.0  # the target 4-point DFT of the design
    # the inputs x = T^dagger e_k send all the light to output k (linear superposition of
    # the four simulated single-input fields, as in the example's field figure)
    E = np.stack([np.tensordot(np.conj(T[k, :]), f["ey"], axes=1) for k in range(4)])
    labels = [f"inputs T^dagger e_{k + 1}: all light to output {k + 1}" for k in range(4)]
    return save_field(HERE / "i1-matmul" / "field.npz", E, f["x"] * 1e6, f["y"] * 1e6, [1550.0] * 4,
                      labels=labels, half=True)


def i2(src):
    f = np.load(src / "results" / "field.npz")
    return save_field(HERE / "i2-duplexer" / "field.npz", f["ey"], f["x"] * 1e6, f["y"] * 1e6,
                      np.asarray(f["wavelengths"]) * 1e3, half=True)


def i3(src):
    f = np.load(src / "results" / "field.npz")
    return save_field(HERE / "i3-receiver" / "field.npz", f["ey"], f["x"] * 1e6, f["z"] * 1e6,
                      float(f["wavelength"]) * 1e3, plane="xz", offset_um=0.0, half=True)


def _weight(path, u, v, I):
    I = np.asarray(I, dtype=np.float32)
    np.savez_compressed(path, u=np.asarray(u, float), v=np.asarray(v, float), I=I.astype(np.float16))
    return path


def q3(src):
    f = np.load(src / "results" / "field_3d.npz")
    I = np.asarray(f["intensity"], dtype=np.float32)
    return _weight(HERE / "q3-quantum-walk" / "weight.npz", f["x_um"], f["y_um"], I)


def s2(src):
    e2 = np.load(src / "results" / "field.npy")
    a = np.load(src / "results" / "field_axes.npz")
    return _weight(HERE / "s2-ring-sensor" / "weight.npz", a["x"] * 1e6, a["y"] * 1e6, e2 / e2.max())


def s3(src):
    g = np.load(src / "results" / "grid.npz")
    m = np.load(src / "results" / "map_xy.npy")
    R = json.loads((src / "results" / "results.json").read_text())
    o = R["bowtie"][-1]["xy_origin"]
    u = g["x"][o[0] : o[0] + m.shape[0]] * 1e6
    v = g["y"][o[1] : o[1] + m.shape[1]] * 1e6
    return _weight(HERE / "s3-sers" / "weight.npz", u, v, m / m.max())


if __name__ == "__main__":
    root = Path(sys.argv[1])
    for slug, fn in (("d3-grating-coupler", d3), ("i1-matmul", i1), ("i2-duplexer", i2), ("i3-receiver", i3),
                     ("q3-quantum-walk", q3), ("s2-ring-sensor", s2), ("s3-sers", s3)):
        p = fn(root / slug)
        print(f"{slug:20s} -> {Path(p).relative_to(HERE)}  {Path(p).stat().st_size / 1e3:.0f} kB")
