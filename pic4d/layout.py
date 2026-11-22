"""Read a layout and prepare it for display.

A layout is a set of 2-D polygons per GDS layer (in micrometres) plus a *layer stack*
that gives every layer a z range and a material. Real chips have extreme aspect ratios
(a 220 nm film, 0.4 um wires, millimetre dies), so :func:`legibility` chooses

* a **widening** of thin features, so a wire is at least a few pixels wide in the frame
  (``widen_um``: added to the full width of every polygon of a ``core`` layer), and
* a **vertical exaggeration** (``z_scale``), so the films read as relief.

Both are stored in the bundle metadata and printed with every render, because they are
departures from the true geometry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

__all__ = ["Layer", "Layout", "read_gds", "from_gdsfactory", "legibility", "MATERIALS"]

#: Material keys a layer may use; styles define how each one looks.
MATERIALS = ("si", "sin", "sio2", "metal", "au", "doped_n", "doped_p", "heater", "polymer", "water")


@dataclass
class Layer:
    """One entry of the layer stack.

    gds: (layer, datatype) to read. zmin/zmax: micrometres. material: a key of
    :data:`MATERIALS`. role: 'core' layers guide light (they are widened, carry the light
    and define the light paths); 'solid' layers are drawn as is; 'clad' layers are drawn
    translucent."""

    gds: tuple[int, int]
    zmin: float
    zmax: float
    material: str = "si"
    role: str = "core"
    name: str = ""
    opacity: float = 1.0

    @classmethod
    def from_dict(cls, d: dict) -> "Layer":
        g = d.get("gds", d.get("layer"))
        return cls(gds=(int(g[0]), int(g[1]) if len(g) > 1 else 0), zmin=float(d["zmin"]), zmax=float(d["zmax"]),
                   material=d.get("material", "si"), role=d.get("role", "core"), name=d.get("name", ""),
                   opacity=float(d.get("opacity", 1.0)))


@dataclass
class Layout:
    """Polygons per layer, in micrometres."""

    name: str
    polygons: dict[tuple[int, int], list[np.ndarray]] = field(default_factory=dict)

    def bounds(self, layers=None) -> tuple[float, float, float, float]:
        """(xmin, ymin, xmax, ymax) over the given layers (all by default)."""
        pts = [p for k, ps in self.polygons.items() if layers is None or k in layers for p in ps]
        if not pts:
            raise ValueError(f"layout {self.name!r} has no polygons on layers {layers}")
        allp = np.concatenate(pts)
        return float(allp[:, 0].min()), float(allp[:, 1].min()), float(allp[:, 0].max()), float(allp[:, 1].max())

    def layer_keys(self) -> list[tuple[int, int]]:
        return sorted(self.polygons)

    def merged(self, key, widen_um: float = 0.0, tolerance: float = 1e-3) -> list[np.ndarray]:
        """Polygons of one layer, unioned (no internal faces) and optionally widened."""
        import gdstk

        polys = [gdstk.Polygon(p) for p in self.polygons.get(tuple(key), [])]
        if not polys:
            return []
        if widen_um > 0:
            out = gdstk.offset(polys, widen_um / 2, join="round", tolerance=tolerance, precision=1e-4)
        else:
            out = gdstk.boolean(polys, [], "or", precision=1e-4)
        return [np.asarray(p.points, dtype=np.float64) for p in out]


def _pick_cell(lib, cell: str | None, stem: str = ""):
    """The named cell; else the cell named like the file; else the largest cell that no
    other (non-context) cell references. Context cells ('$$$...') are ignored."""
    cells = [c for c in lib.cells if not c.name.startswith("$$$")]
    if cell:
        for c in cells:
            if c.name == cell:
                return c
        raise KeyError(f"cell {cell!r} not in GDS (have {[c.name for c in cells]})")
    for c in cells:
        if stem and c.name == stem:
            return c
    referenced = {r.cell.name for c in cells for r in c.references if not isinstance(r.cell, str)}
    tops = [c for c in cells if c.name not in referenced] or cells
    if not tops:
        raise ValueError("GDS has no cells")

    def area(c):
        bb = c.bounding_box()
        return 0.0 if bb is None else (bb[1][0] - bb[0][0]) * (bb[1][1] - bb[0][1])

    return max(tops, key=area)


def read_gds(path: str | Path, cell: str | None = None) -> Layout:
    """Read a GDS file with gdstk; the largest non-context top cell unless `cell` is given.

    Coordinates are converted to micrometres whatever the file's user unit."""
    import gdstk

    lib = gdstk.read_gds(str(path))
    scale = lib.unit / 1e-6
    c = _pick_cell(lib, cell, Path(path).stem)
    lay = Layout(name=c.name)
    for p in c.get_polygons():
        lay.polygons.setdefault((p.layer, p.datatype), []).append(np.asarray(p.points, dtype=np.float64) * scale)
    for path_ in c.get_paths():
        for p in path_.to_polygons():
            lay.polygons.setdefault((p.layer, p.datatype), []).append(np.asarray(p.points, dtype=np.float64) * scale)
    return lay


def from_gdsfactory(component) -> Layout:
    """Layout from a gdsfactory Component (written to a temporary GDS and read back)."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "c.gds"
        component.write_gds(f)
        return read_gds(f, cell=component.name)


def min_feature_width(polys: list[np.ndarray], probe: float = 0.0) -> float:
    """Estimate of the narrowest feature: 2 * area / perimeter of the thinnest polygon
    (exact for long strips, an over-estimate for blobs)."""
    best = np.inf
    for p in polys:
        x, y = p[:, 0], p[:, 1]
        a = 0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))
        per = np.sum(np.hypot(np.diff(x, append=x[0]), np.diff(y, append=y[0])))
        if per > 0 and a > 0:
            best = min(best, 2 * a / per)
    return float(best)


def legibility(extent_um: float, core_width_um: float, core_thickness_um: float, *,
               min_width_frac: float = 0.0035, relief: float = 1.0, max_z_scale: float = 400.0) -> dict:
    """Display widening and vertical exaggeration for a layout.

    extent_um: largest side of the framed region. A core feature is drawn at least
    ``min_width_frac * extent`` wide (about 5 px of a 1440 px frame). The core film is
    then drawn ``relief`` times as tall as the displayed wire width (z_scale >= 1).

    Returns {'widen_um', 'display_width_um', 'z_scale'}."""
    target_w = min_width_frac * extent_um
    widen = max(0.0, target_w - core_width_um)
    disp_w = core_width_um + widen
    z_scale = max(1.0, min(max_z_scale, relief * disp_w / max(core_thickness_um, 1e-6)))
    return {"widen_um": float(widen), "display_width_um": float(disp_w), "z_scale": float(z_scale)}
