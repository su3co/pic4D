"""Light paths from layout polygons (no field data needed).

The path-driven animation sends pulses along the centre lines of the waveguides, from an
input port, splitting wherever the guides split. It needs, for every point of the chip,
the distance light has travelled to get there. :func:`extract` computes it:

1. rasterise the core polygons on a fine grid (at least 3 pixels across the narrowest
   wire);
2. close gaps narrower than ``coupling_gap_um`` (a dilation), so directional couplers,
   ring-bus gaps and coupled arrays count as connected, as light crosses them;
3. thin the result to a one-pixel skeleton (the centre lines), drop short spurs;
4. run Dijkstra on the skeleton graph from the pixel nearest the source: the geodesic
   **arrival distance**;
5. give every pixel of the chip the arrival distance of its nearest skeleton pixel.

Optionally, ``routes`` gives, per wavelength, the output point(s) that wavelength leaves
by (a demultiplexer's channel map from the design); a wavelength is then drawn only where
one of its outputs lies downstream. Without routes every colour goes everywhere.

This is geometry, not physics: the resulting animation illustrates where light goes, not
how much of it, and must not be captioned as a simulation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

__all__ = ["Grid", "PathResult", "rasterize", "extract", "skeleton_endpoints"]


@dataclass
class Grid:
    """A raster aligned with the layout: pixel (j, i) is centred on (x0 + (i + .5) px, y0 + (j + .5) px)."""

    x0: float
    y0: float
    px: float
    nx: int
    ny: int

    @classmethod
    def around(cls, bounds, px: float, margin: float = 0.0) -> "Grid":
        xmin, ymin, xmax, ymax = bounds
        x0, y0 = xmin - margin, ymin - margin
        nx = int(np.ceil((xmax + margin - x0) / px)) + 1
        ny = int(np.ceil((ymax + margin - y0) / px)) + 1
        return cls(x0, y0, px, nx, ny)

    def to_px(self, xy) -> np.ndarray:
        xy = np.asarray(xy, dtype=np.float64)
        return np.stack([(xy[..., 0] - self.x0) / self.px - 0.5, (xy[..., 1] - self.y0) / self.px - 0.5], -1)

    def to_um(self, ij) -> np.ndarray:
        """(row j, col i) pixel indices -> (x, y) micrometres."""
        ij = np.asarray(ij, dtype=np.float64)
        return np.stack([self.x0 + (ij[..., 1] + 0.5) * self.px, self.y0 + (ij[..., 0] + 0.5) * self.px], -1)

    @property
    def extent(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x0 + self.nx * self.px, self.y0 + self.ny * self.px)


def rasterize(polys, grid: Grid) -> np.ndarray:
    """Boolean (ny, nx) mask of the union of polygons (row j is y)."""
    from PIL import Image, ImageDraw

    img = Image.new("1", (grid.nx, grid.ny), 0)
    d = ImageDraw.Draw(img)
    for p in polys:
        q = grid.to_px(p) + 0.5
        if len(q) >= 3:
            d.polygon([tuple(v) for v in q], fill=1, outline=1)
    return np.array(img, dtype=bool)


def _disk(r: int) -> np.ndarray:
    y, x = np.mgrid[-r : r + 1, -r : r + 1]
    return x * x + y * y <= r * r + 0.5


_OFFS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def _neighbour_count(sk: np.ndarray) -> np.ndarray:
    from scipy import ndimage

    k = np.ones((3, 3), dtype=np.int32)
    k[1, 1] = 0
    return ndimage.convolve(sk.astype(np.int32), k, mode="constant") * sk


def prune_spurs(sk: np.ndarray, length_px: int) -> np.ndarray:
    """Remove skeleton branches shorter than ``length_px`` that end freely.

    Free ends of real guides are shortened by the same amount, which is harmless: the
    arrival distance is extended to them from the nearest remaining pixel."""
    sk = sk.copy()
    for _ in range(max(0, int(length_px))):
        n = _neighbour_count(sk)
        ends = (n == 1)
        if not ends.any():
            break
        sk[ends] = False
    return sk


def _graph(sk: np.ndarray, px: float):
    from scipy.sparse import coo_matrix

    js, is_ = np.nonzero(sk)
    idx = -np.ones(sk.shape, dtype=np.int64)
    idx[js, is_] = np.arange(len(js))
    rows, cols, w = [], [], []
    H, W = sk.shape
    for dj, di in _OFFS:
        j2, i2 = js + dj, is_ + di
        ok = (j2 >= 0) & (j2 < H) & (i2 >= 0) & (i2 < W)
        a = np.nonzero(ok)[0]
        b = idx[j2[a], i2[a]]
        good = b >= 0
        rows.append(a[good])
        cols.append(b[good])
        w.append(np.full(good.sum(), px * (np.sqrt(2) if dj and di else 1.0)))
    rows, cols, w = np.concatenate(rows), np.concatenate(cols), np.concatenate(w)
    n = len(js)
    g = coo_matrix((w, (rows, cols)), shape=(n, n)).tocsr()
    return g, js, is_, idx


@dataclass
class PathResult:
    """Output of :func:`extract` (all distances in micrometres)."""

    grid: Grid
    core: np.ndarray            # (ny, nx) bool, true-width core mask
    skeleton: np.ndarray        # (ny, nx) bool
    arrival: np.ndarray         # (ny, nx) float32, arrival distance extended to every pixel; nan if unreachable
    route_masks: np.ndarray     # (n_wavelengths, ny, nx) bool
    main_path: np.ndarray       # (k, 2) polyline in um, source to the farthest reachable point
    endpoints: np.ndarray       # (m, 2) free ends of the skeleton (candidate ports), um
    weight: np.ndarray | None   # (ny, nx) brightness weight carried along the path (see `weight=`)
    max_arrival: float
    source: tuple[float, float]
    notes: list[str] = field(default_factory=list)


def skeleton_endpoints(sk: np.ndarray, grid: Grid) -> np.ndarray:
    n = _neighbour_count(sk)
    js, is_ = np.nonzero(n == 1)
    return grid.to_um(np.stack([js, is_], -1)) if len(js) else np.zeros((0, 2))


def extract(core_polys, *, source=None, px: float | None = None, coupling_gap_um: float = 0.45,
            routes: list[list[tuple[float, float]]] | None = None, n_wavelengths: int = 1,
            max_pixels: int = 60_000_000, spur_um: float | None = None, min_width_um: float | None = None,
            weight=None) -> PathResult:
    """Arrival-distance map of light entering at `source` (x, y um, or a list of such
    points for several inputs lit at once; default: the leftmost free end).

    core_polys: true-width core polygons (um). px: analysis pixel (default: a third of the
    narrowest feature, coarsened if the grid would exceed `max_pixels`).
    routes: per wavelength, a list of output points, or None for 'everywhere'.
    weight: optional callable (points (k, 2) um) -> brightness in [0, 1], nan where it is
    unknown (outside a simulated intensity map). Unknown centre-line points inherit the
    value of the point light came from, so guides leaving the map keep the brightness they
    left it with; points upstream of the map get 1."""
    from scipy import ndimage
    from scipy.sparse.csgraph import dijkstra
    from scipy.spatial import cKDTree
    from skimage.morphology import skeletonize

    from .layout import min_feature_width

    allp = np.concatenate(core_polys)
    bounds = (allp[:, 0].min(), allp[:, 1].min(), allp[:, 0].max(), allp[:, 1].max())
    w = min_width_um or min_feature_width(core_polys)
    notes = []
    if px is None:
        px = max(w / 3.0, 1e-3)
    margin = coupling_gap_um + 4 * px
    area = (bounds[2] - bounds[0] + 2 * margin) * (bounds[3] - bounds[1] + 2 * margin)
    if area / px**2 > max_pixels:
        px2 = float(np.sqrt(area / max_pixels))
        notes.append(f"analysis pixel coarsened from {px:.3g} to {px2:.3g} um (grid limit)")
        px = px2
    grid = Grid.around(bounds, px, margin)
    core = rasterize(core_polys, grid)
    r = int(round(coupling_gap_um / 2 / px))
    closed = ndimage.binary_dilation(core, structure=_disk(r)) if r > 0 else core
    sk = skeletonize(closed)
    spur = spur_um if spur_um is not None else max(1.5 * w, coupling_gap_um) + 2 * px
    sk = prune_spurs(sk, int(np.ceil(spur / px)))
    if not sk.any():
        raise ValueError("no centre lines found: are the core layers right?")
    g, js, is_, idx = _graph(sk, px)
    pts = grid.to_um(np.stack([js, is_], -1))
    tree = cKDTree(pts)
    ends = skeleton_endpoints(sk, grid)
    if source is None:
        cand = ends if len(ends) else pts
        source = tuple(cand[np.argmin(cand[:, 0])])
        notes.append(f"source defaulted to the leftmost free end {source[0]:.2f}, {source[1]:.2f} um")
    srcs = np.atleast_2d(np.asarray(source, dtype=float))
    _, s_idx = tree.query(srcs)
    s_idx = np.unique(np.atleast_1d(s_idx)).astype(int)
    dist, pred, _ = dijkstra(g, directed=False, indices=s_idx, return_predecessors=True, min_only=True)
    source = tuple(srcs[0])
    reach = np.isfinite(dist)
    if (~reach).any():
        notes.append(f"{int((~reach).sum())} of {len(dist)} centre-line pixels are not connected to the source")

    # main path: source -> farthest reachable pixel
    far = int(np.argmax(np.where(reach, dist, -1)))
    chain = [far]
    while pred[chain[-1]] >= 0 and len(chain) <= len(pred):
        chain.append(int(pred[chain[-1]]))
    main = pts[chain[::-1]]

    # routes: forward reachability of each wavelength's outputs
    nw = max(1, n_wavelengths if routes is None else len(routes))
    masks_sk = np.ones((nw, len(dist)), dtype=bool)
    if routes is not None:
        bits = np.zeros(len(dist), dtype=np.int64)
        for k, outs in enumerate(routes):
            if not outs:
                bits |= (1 << k)  # unrestricted wavelength: mark it everywhere below
                continue
            for o in outs:
                _, oi = tree.query(o)
                bits[oi] |= (1 << k)
        unrestricted = sum(1 << k for k, outs in enumerate(routes) if not outs)
        order = np.argsort(np.where(reach, dist, -np.inf))[::-1]
        indptr, indices = g.indptr, g.indices
        for u in order:
            if not reach[u]:
                continue
            nb = indices[indptr[u] : indptr[u + 1]]
            down = nb[dist[nb] > dist[u]]
            if len(down):
                bits[u] |= np.bitwise_or.reduce(bits[down])
        bits |= unrestricted
        masks_sk = np.stack([(bits >> k) & 1 == 1 for k in range(nw)])

    w_sk = None
    if weight is not None:
        w_sk = np.asarray(weight(pts), dtype=np.float64)
        for u in np.argsort(np.where(reach, dist, np.inf)):
            if not reach[u]:
                break
            if np.isnan(w_sk[u]):
                p_ = pred[u]
                w_sk[u] = w_sk[p_] if p_ >= 0 else 1.0
        w_sk = np.nan_to_num(w_sk, nan=1.0)

    # extend to all pixels via nearest skeleton pixel
    dt_in = np.ones(sk.shape, dtype=bool)
    dt_in[js, is_] = False
    _, (nj, ni) = ndimage.distance_transform_edt(dt_in, return_indices=True)
    near = idx[nj, ni]
    arrival = np.where(reach[near], dist[near], np.nan).astype(np.float32)
    route_masks = masks_sk[:, near]
    wmap = None if w_sk is None else w_sk[near].astype(np.float32)
    return PathResult(grid=grid, core=core, skeleton=sk, arrival=arrival, route_masks=route_masks,
                      main_path=main, endpoints=ends, weight=wmap, max_arrival=float(np.nanmax(arrival)),
                      source=(float(source[0]), float(source[1])), notes=notes)
