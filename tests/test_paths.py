import numpy as np
import pytest

from pic4d.layout import read_gds
from pic4d.paths import extract


@pytest.fixture
def ypaths(ybranch_gds):
    pytest.importorskip("skimage")
    lay = read_gds(ybranch_gds)
    return extract(lay.merged((1, 0)), source=(0.2, 0.0), coupling_gap_um=0.3,
                   routes=[[(29.5, 2.5)], [(29.5, -2.5)]])


def _at(r, x, y):
    i, j = np.round(r.grid.to_px([x, y])).astype(int)
    return i, j


def test_arrival_grows_along_the_guide(ypaths):
    r = ypaths
    vals = [r.arrival[_at(r, x, 0)[1], _at(r, x, 0)[0]] for x in (1, 4, 8)]
    assert vals[0] < vals[1] < vals[2]
    assert abs(vals[2] - vals[0] - 7.0) < 0.6  # distances in um


def test_both_arms_reached_equally(ypaths):
    r = ypaths
    up = r.arrival[_at(r, 28, 2.5)[1], _at(r, 28, 2.5)[0]]
    dn = r.arrival[_at(r, 28, -2.5)[1], _at(r, 28, -2.5)[0]]
    assert np.isfinite(up) and abs(up - dn) < 0.5
    assert 26 < up < 32  # ~ path length less the trimmed free end


def test_routes_split_colours(ypaths):
    r = ypaths
    iu, ju = _at(r, 28, 2.5)
    idn, jdn = _at(r, 28, -2.5)
    i0, j0 = _at(r, 5, 0)
    assert r.route_masks[0][ju, iu] and not r.route_masks[0][jdn, idn]
    assert r.route_masks[1][jdn, idn] and not r.route_masks[1][ju, iu]
    assert r.route_masks[0][j0, i0] and r.route_masks[1][j0, i0]  # trunk carries both


def test_endpoints_and_main_path(ypaths):
    r = ypaths
    assert len(r.endpoints) == 3
    assert np.linalg.norm(r.main_path[0] - np.array([0.2, 0.0])) < 1.6  # free ends are trimmed by the spur length
    assert r.main_path[-1][0] > 28
