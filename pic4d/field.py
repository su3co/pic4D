"""Simulated fields: the pic4D ``.npz`` format.

A field file holds one or more complex 2-D phasors sampled on a plane::

    E             complex64, shape (n, nu, nv) or (nu, nv)   field phasor (any units)
    u             float, (nu,)   sample coordinates along the plane's first axis, um
    v             float, (nv,)   sample coordinates along the plane's second axis, um
    wavelength_nm float, (n,) or ()                          vacuum wavelength of each entry
    plane         str  'xy' (default), 'xz' or 'yz'          which layout axes u and v are
    offset_um     float          position of the plane on the remaining axis
                                 (z for 'xy', y for 'xz', x for 'yz'); default 0
    label         str, (n,)      optional, one caption per entry

Coordinates are in the layout frame (the GDS coordinates, z = 0 at the bottom of the
core film). The animation shows Re[E exp(-i w t)] = Re E cos wt + Im E sin wt, so the
simulated wave is seen travelling; entries with different wavelengths are drawn in their
own colours, and entries can also be shown one after the other (``sequence``).

Only the phasor matters: an intensity map |E|^2 has lost the phase and cannot be
animated this way (use the path-driven mode).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

__all__ = ["Field", "load_field", "save_field"]


@dataclass
class Field:
    E: np.ndarray            # (n, nu, nv) complex
    u: np.ndarray            # (nu,) um
    v: np.ndarray            # (nv,) um
    wavelength_nm: np.ndarray  # (n,)
    plane: str = "xy"
    offset_um: float = 0.0
    labels: tuple[str, ...] = ()

    def __post_init__(self):
        self.E = np.asarray(self.E)
        if self.E.ndim == 2:
            self.E = self.E[None]
        if self.E.ndim != 3 or not np.iscomplexobj(self.E):
            raise ValueError("E must be a complex array of shape (n, nu, nv) or (nu, nv)")
        self.u = np.asarray(self.u, dtype=np.float64)
        self.v = np.asarray(self.v, dtype=np.float64)
        n, nu, nv = self.E.shape
        if self.u.shape != (nu,) or self.v.shape != (nv,):
            raise ValueError(f"u, v must have lengths {nu}, {nv} (got {self.u.shape}, {self.v.shape})")
        self.wavelength_nm = np.broadcast_to(np.asarray(self.wavelength_nm, dtype=np.float64), (n,)).copy()
        if self.plane not in ("xy", "xz", "yz"):
            raise ValueError("plane must be 'xy', 'xz' or 'yz'")

    @property
    def n(self) -> int:
        return self.E.shape[0]

    def extent(self) -> tuple[float, float, float, float]:
        return float(self.u[0]), float(self.v[0]), float(self.u[-1]), float(self.v[-1])

    def resampled(self, max_side: int = 1024) -> "Field":
        """Same field on a uniform grid (u, v axes may be non-uniform in the file), at most
        `max_side` samples per side, linear interpolation of Re and Im."""
        from scipy.interpolate import RegularGridInterpolator

        du = np.min(np.diff(self.u)) if len(self.u) > 1 else 1.0
        dv = np.min(np.diff(self.v)) if len(self.v) > 1 else 1.0
        d = max(du, dv, max(np.ptp(self.u), np.ptp(self.v)) / max_side)
        nu = max(2, int(round(np.ptp(self.u) / d)) + 1)
        nv = max(2, int(round(np.ptp(self.v) / d)) + 1)
        uu = np.linspace(self.u[0], self.u[-1], nu)
        vv = np.linspace(self.v[0], self.v[-1], nv)
        U, V = np.meshgrid(uu, vv, indexing="ij")
        out = np.empty((self.n, nu, nv), dtype=np.complex64)
        for k in range(self.n):
            f_re = RegularGridInterpolator((self.u, self.v), self.E[k].real)
            f_im = RegularGridInterpolator((self.u, self.v), self.E[k].imag)
            out[k] = f_re((U, V)) + 1j * f_im((U, V))
        return Field(out, uu, vv, self.wavelength_nm, self.plane, self.offset_um, self.labels)

    def normalised(self, percentile: float = 99.7) -> np.ndarray:
        """E / scale, where scale is the given percentile of |E| over all entries (hot
        spots at sharp corners would otherwise wash the picture out); |E| above it clips."""
        a = np.abs(self.E)
        s = float(np.percentile(a, percentile))
        return (self.E / (s if s > 0 else 1.0)).astype(np.complex64)


def load_field(path: str | Path) -> Field:
    """Read a pic4D field file (see the module docstring)."""
    d = np.load(path, allow_pickle=False)
    plane = str(d["plane"]) if "plane" in d.files else "xy"
    off = float(d["offset_um"]) if "offset_um" in d.files else 0.0
    labels = tuple(str(s) for s in d["label"]) if "label" in d.files else ()
    return Field(d["E"], d["u"], d["v"], d["wavelength_nm"], plane, off, labels)


def save_field(path: str | Path, E, u, v, wavelength_nm, plane: str = "xy", offset_um: float = 0.0,
               labels=(), half: bool = False) -> Path:
    """Write a pic4D field file. half=True normalises the field to max |E| = 1 and rounds
    Re and Im to float16 precision (smaller files, plenty for display)."""
    f = Field(np.asarray(E), u, v, wavelength_nm, plane, offset_um, tuple(labels))
    E = f.E.astype(np.complex64)
    if half:  # complex32 does not exist: keep complex64 dtype but quantise through float16
        E = E / max(float(np.abs(E).max()), 1e-30)
        E = (E.real.astype(np.float16).astype(np.float32) + 1j * E.imag.astype(np.float16).astype(np.float32)).astype(np.complex64)
    kw = dict(E=E, u=f.u, v=f.v, wavelength_nm=f.wavelength_nm, plane=np.array(f.plane), offset_um=np.array(f.offset_um))
    if f.labels:
        kw["label"] = np.array(f.labels)
    path = Path(path)
    np.savez_compressed(path, **kw)
    return path
