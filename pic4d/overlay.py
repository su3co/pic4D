"""Title and label overlays, burnt into rendered frames (Pillow).

A label is a device name, a second line (platform, wavelength) and an optional small
caption, set at the lower left inside the title-safe area (6 % of the width, 7 % of the
height from the edges), with a colour swatch per wavelength.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

__all__ = ["Label", "find_font", "draw_label", "SAFE"]

SAFE = (0.06, 0.07)  # title-safe margins (fraction of width, height)

_FONT_DIRS = [
    Path(__file__).resolve().parent / "fonts",
    Path.home() / ".fonts",
    Path.home() / ".local/share/fonts",
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path("/Library/Fonts"),
    Path.home() / "Library/Fonts",
    Path("/System/Library/Fonts"),
]

_FALLBACKS = ["Inter", "Helvetica Neue", "Helvetica", "DejaVu Sans", "Liberation Sans", "Arial"]


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def find_font(name: str | None = None, weight: str = "regular") -> str | None:
    """Path of an installed font file matching a family name and weight
    ('regular', 'medium', 'semibold', 'bold'), or None. Extra folders can be given in
    the PIC4D_FONT_DIR environment variable (colon-separated)."""
    dirs = [Path(p) for p in os.environ.get("PIC4D_FONT_DIR", "").split(os.pathsep) if p] + _FONT_DIRS
    files = []
    for d in dirs:
        if d.is_dir():
            files += [p for p in d.rglob("*") if p.suffix.lower() in (".ttf", ".otf", ".ttc", ".woff2", ".woff")]
    wnum = {"regular": ("regular", "400", "book"), "medium": ("medium", "500"), "semibold": ("semibold", "600"),
            "bold": ("bold", "700")}[weight]
    for fam in ([name] if name else []) + _FALLBACKS:
        key = _norm(fam)
        cands = [p for p in files if _norm(p.stem).startswith(key) and "italic" not in p.stem.lower()]
        if not cands:
            continue
        for w in wnum:
            hit = [p for p in cands if w in p.stem.lower() and "display" not in p.stem.lower()]
            if hit:
                return str(sorted(hit, key=lambda p: (len(p.stem), "ext" in p.stem))[0])
        if weight == "regular":
            plain = [p for p in cands if not any(t in p.stem.lower() for t in ("bold", "medium", "semi", "light", "thin", "black", "extra"))]
            if plain:
                return str(sorted(plain, key=lambda p: len(p.stem))[0])
    return None


def _font(name, weight, size):
    if name and Path(name).is_file():
        return ImageFont.truetype(name, size)
    p = find_font(name, weight) or find_font(name, "regular")
    if p:
        return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


@dataclass
class Label:
    """What to print on a clip."""

    title: str
    subtitle: str = ""
    caption: str = ""
    swatches: list[str] = field(default_factory=list)  # '#rrggbb' per wavelength
    ink: str = "#f2f4f8"
    muted: str = "#9aa3b2"
    font: str | None = None
    position: str = "lower-left"


def draw_label(img: Image.Image, label: Label, alpha: float = 1.0) -> Image.Image:
    """Return a copy of `img` with the label drawn (alpha 0..1 fades it in or out)."""
    if alpha <= 0:
        return img
    W, H = img.size
    base = img.convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    mx, my = int(SAFE[0] * W), int(SAFE[1] * H)
    s_title, s_sub, s_cap = int(0.040 * H), int(0.024 * H), int(0.019 * H)
    f_title = _font(label.font, "semibold", s_title)
    f_sub = _font(label.font, "regular", s_sub)
    f_cap = _font(label.font, "regular", s_cap)
    a = int(255 * alpha)
    ink, muted = _rgb(label.ink), _rgb(label.muted)
    y = H - my
    lines = []
    if label.caption:
        lines.append((label.caption, f_cap, muted, s_cap, False))
    if label.subtitle:
        lines.append((label.subtitle, f_sub, ink, s_sub, True))
    lines.append((label.title, f_title, ink, s_title, False))
    x0 = mx
    for text, f, col, size, sw in lines:
        y -= int(size * 1.0)
        x = x0
        if sw and label.swatches:
            r = int(size * 0.36)
            for h in label.swatches:
                cy = y + int(size * 0.55)
                d.ellipse([x, cy - r, x + 2 * r, cy + r], fill=(*_rgb(h), a))
                x += int(2 * r + size * 0.25)
            x += int(size * 0.25)
        d.text((x, y), text, font=f, fill=(*col, a))
        y -= int(size * (0.55 if f is f_title else 0.5))
    return Image.alpha_composite(base, layer).convert("RGB")
