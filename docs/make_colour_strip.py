"""Draw docs/colours.png: pic4D's wavelength -> colour rule from 380 to 1700 nm."""

from pathlib import Path

from PIL import Image, ImageDraw

from pic4d.colors import wavelength_to_srgb
from pic4d.overlay import _font

W, H, PAD = 1600, 210, 40
im = Image.new("RGB", (W, H), "#ffffff")
d = ImageDraw.Draw(im)
lo, hi = 380, 1700
x0, x1, y0, y1 = PAD, W - PAD, 40, 120
for x in range(x0, x1):
    nm = lo + (hi - lo) * (x - x0) / (x1 - x0)
    d.line([(x, y0), (x, y1)], fill=tuple(int(255 * c) for c in wavelength_to_srgb(nm)))
f = _font(None, "regular", 18)
fb = _font(None, "semibold", 18)
for nm in (400, 500, 600, 700, 800, 1000, 1200, 1271, 1331, 1400, 1550, 1625):
    x = x0 + (nm - lo) / (hi - lo) * (x1 - x0)
    d.line([(x, y1), (x, y1 + 8)], fill="#1d2129", width=2)
    t = str(nm)
    d.text((x - d.textlength(t, font=f) / 2, y1 + 12), t, font=f, fill="#1d2129")
for a, b, name in ((380, 700, "visible"), (700, 1260, "near infrared (line of purples)"), (1260, 1675, "telecom O · E · S · C · L · U")):
    xa = x0 + (a - lo) / (hi - lo) * (x1 - x0)
    xb = x0 + (b - lo) / (hi - lo) * (x1 - x0)
    d.text(((xa + xb) / 2 - d.textlength(name, font=fb) / 2, 10), name, font=fb, fill="#1d2129")
d.text((x0, H - 28), "wavelength (nm)", font=f, fill="#5d6470")
Path(__file__).with_name("colours.png").parent.mkdir(exist_ok=True)
im.save(Path(__file__).with_name("colours.png"), optimize=True)
