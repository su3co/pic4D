
from pic4d.layout import Layer, legibility, min_feature_width, read_gds


def test_read_gds_picks_real_cell(ybranch_gds):
    lay = read_gds(ybranch_gds)
    assert lay.name == "ybranch"
    assert (1, 0) in lay.polygons and (99, 0) in lay.polygons
    x0, y0, x1, y1 = lay.bounds([(1, 0)])
    assert abs(x0) < 1e-6 and abs(x1 - 30) < 1e-6 and y1 > 2.5


def test_merge_and_widen(ybranch_gds):
    lay = read_gds(ybranch_gds)
    merged = lay.merged((1, 0))
    assert len(merged) == 1  # the three pieces touch
    w0 = min_feature_width(merged)
    assert 0.4 < w0 < 0.6
    wide = lay.merged((1, 0), widen_um=1.0)
    assert min_feature_width(wide) > w0 + 0.6


def test_legibility():
    leg = legibility(3000.0, 0.5, 0.22)
    assert leg["display_width_um"] > 5  # a wire on a 3 mm die must be widened
    assert leg["z_scale"] > 1
    small = legibility(5.0, 0.5, 0.22)
    assert small["widen_um"] == 0.0


def test_layer_from_dict():
    l = Layer.from_dict({"gds": [2, 0], "zmin": 0, "zmax": 0.15, "material": "si", "role": "solid"})
    assert l.gds == (2, 0) and l.role == "solid"
