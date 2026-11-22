import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _strip(x0, x1, y, w):
    return np.array([[x0, y - w / 2], [x1, y - w / 2], [x1, y + w / 2], [x0, y + w / 2]])


@pytest.fixture
def ybranch_gds(tmp_path):
    """A 0.5 um wire that splits into two arms (S-bends), plus a context cell."""
    gdstk = pytest.importorskip("gdstk")
    lib = gdstk.Library(unit=1e-6, precision=1e-9)
    lib.new_cell("$$$CONTEXT_INFO$$$")
    c = lib.new_cell("ybranch")
    w = 0.5
    c.add(gdstk.Polygon(_strip(0, 10, 0, w), layer=1))
    for s in (+1, -1):
        p = gdstk.FlexPath([(10, 0), (14, 0), (18, 2.5 * s), (30, 2.5 * s)], w, bend_radius=2.0, layer=1)
        c.add(*p.to_polygons())
    c.add(gdstk.rectangle((-2, -6), (32, 6), layer=99))
    f = tmp_path / "ybranch.gds"
    lib.write_gds(str(f))
    return f
