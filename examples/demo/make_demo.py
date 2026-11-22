"""Write demo.gds: a 1x2 MMI splitter whose arms pass a ring resonator and a
Mach-Zehnder delay; 220 nm silicon wires, 0.5 um wide (about 120 x 40 um).

    python make_demo.py
"""

import gdstk

W = 0.5
lib = gdstk.Library(unit=1e-6, precision=1e-9)
c = lib.new_cell("demo")


def wire(points, radius=5.0):
    c.add(*gdstk.FlexPath(points, W, bend_radius=radius, layer=1).to_polygons())


wire([(0, 0), (10, 0)])
c.add(gdstk.rectangle((10, -1.6), (22, 1.6), layer=1))  # MMI body
for s in (+1, -1):
    c.add(gdstk.Polygon([(22, s * 0.8 - 0.4), (25, s * 0.8 - W / 2), (25, s * 0.8 + W / 2), (22, s * 0.8 + 0.4)], layer=1))
    wire([(25, s * 0.8), (30, s * 0.8), (40, s * 10), (110, s * 10), (120, s * 10)])
# ring on the upper arm (gap 0.2 um)
r = 6.0
c.add(gdstk.ellipse((70, 10 + W + 0.2 + r), r + W / 2, r - W / 2, tolerance=0.01, layer=1))
# a 2 um slab under the MMI (partial etch)
c.add(gdstk.rectangle((8, -3), (24, 3), layer=2))
lib.write_gds("demo.gds")
print("wrote demo.gds")
