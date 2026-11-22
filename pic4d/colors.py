"""Wavelength -> display colour.

Light in photonic chips is mostly infrared, so pic4D needs a *documented* false-colour
rule that keeps channels a few tens of nanometres apart visibly different. The rule:

* **Visible, 380-700 nm**: the familiar spectral hue, from Dan Bruton's piecewise-linear
  approximation of the spectrum in sRGB (violet - blue at 440 nm - cyan at 490 nm -
  green at 510 nm - yellow at 580 nm - red from 645 nm), at full brightness. Spectral
  colours lie outside the sRGB gamut, so any display colour is an approximation; this
  one keeps hues evenly spread and saturated.
* **Telecom, 1260-1675 nm** (O, E, S, C, L and U bands): mapped onto a *display
  wavelength* in the visible by the piecewise-linear table :data:`TELECOM_ANCHORS`, then
  coloured as above. The table spends more of the spectrum on the O band, where CWDM
  grids put channels 20 nm apart: 1271, 1291, 1311 and 1331 nm come out violet, blue,
  sky blue and cyan; 1550 nm is orange; the L band is red.
* **Near infrared, 700-1260 nm** (e.g. 785 nm Raman pumps, 850 nm VCSELs): the hue wheel
  is closed through the purples, so the NIR is drawn on the line of purples that runs
  from the deep red of 700 nm to the violet that the O band starts at.
* Below 380 nm the colour is that of 380 nm, above 1675 nm that of 1675 nm.

All functions take wavelengths in nanometres.
"""

from __future__ import annotations

import colorsys
import math

__all__ = [
    "TELECOM_ANCHORS",
    "display_wavelength",
    "wavelength_to_linear_rgb",
    "wavelength_to_srgb",
    "wavelength_to_hex",
    "band_name",
    "linear_to_srgb",
    "srgb_to_linear",
    "hex_to_linear",
    "limit_luminance",
]

#: Telecom wavelength (nm) -> display wavelength (nm) anchors, linearly interpolated.
TELECOM_ANCHORS: tuple[tuple[float, float], ...] = (
    (1260.0, 413.0),  # O band starts violet: 1271 violet, 1291 blue, 1311 azure, 1331 cyan
    (1350.0, 513.0),  # end of the CWDM O-band grid: green
    (1460.0, 545.0),  # E/S boundary: yellow-green
    (1530.0, 575.0),  # S/C boundary: yellow
    (1550.0, 610.0),  # C band centre: orange
    (1565.0, 620.0),  # C/L boundary
    (1625.0, 645.0),  # L/U boundary: red
    (1675.0, 665.0),
)

_BANDS = (
    (380.0, 700.0, "visible"),
    (700.0, 1260.0, "NIR"),
    (1260.0, 1360.0, "O band"),
    (1360.0, 1460.0, "E band"),
    (1460.0, 1530.0, "S band"),
    (1530.0, 1565.0, "C band"),
    (1565.0, 1625.0, "L band"),
    (1625.0, 1675.0, "U band"),
)


def band_name(nm: float) -> str:
    """Name of the band a wavelength falls in ('C band', 'O band', 'visible', 'NIR', ...)."""
    for lo, hi, name in _BANDS:
        if lo <= nm < hi:
            return name
    return "UV" if nm < 380 else "IR"


def _bruton_srgb(nm: float) -> tuple[float, float, float]:
    """Bruton's spectral approximation, encoded sRGB, full brightness."""
    w = min(max(nm, 380.0), 700.0)
    if w < 440:
        return ((440 - w) / 60, 0.0, 1.0)
    if w < 490:
        return (0.0, (w - 440) / 50, 1.0)
    if w < 510:
        return (0.0, 1.0, (510 - w) / 20)
    if w < 580:
        return ((w - 510) / 70, 1.0, 0.0)
    if w < 645:
        return (1.0, (645 - w) / 65, 0.0)
    return (1.0, 0.0, 0.0)


def _spectral_linear_rgb(nm: float) -> tuple[float, float, float]:
    """Full-brightness linear sRGB colour of a visible spectral line."""
    return tuple(srgb_to_linear(c) for c in _bruton_srgb(nm))  # type: ignore[return-value]


def display_wavelength(nm: float) -> float:
    """Visible wavelength used to colour `nm`. Returns nan inside the NIR purple range."""
    if nm <= 700.0:
        return min(max(nm, 380.0), 700.0)
    if nm < TELECOM_ANCHORS[0][0]:
        return float("nan")
    a = TELECOM_ANCHORS
    if nm >= a[-1][0]:
        return a[-1][1]
    for (x0, y0), (x1, y1) in zip(a, a[1:]):
        if x0 <= nm <= x1:
            return y0 + (y1 - y0) * (nm - x0) / (x1 - x0)
    return a[-1][1]  # pragma: no cover


def wavelength_to_linear_rgb(nm: float) -> tuple[float, float, float]:
    """Linear-sRGB display colour (max channel 1) for a wavelength in nm."""
    d = display_wavelength(nm)
    if not math.isnan(d):
        return _spectral_linear_rgb(d)
    # NIR: the line of purples, from deep red (700 nm) to the violet the O band starts at
    t = (nm - 700.0) / (TELECOM_ANCHORS[0][0] - 700.0)
    t = min(max(t, 0.0), 1.0)
    a = _spectral_linear_rgb(700.0)
    b = _spectral_linear_rgb(TELECOM_ANCHORS[0][1])
    c = [(1 - t) * ai + t * bi for ai, bi in zip(a, b)]
    m = max(c)
    return (c[0] / m, c[1] / m, c[2] / m)


def linear_to_srgb(c: float) -> float:
    """sRGB transfer function (linear 0..1 -> encoded 0..1)."""
    c = min(max(c, 0.0), 1.0)
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def srgb_to_linear(c: float) -> float:
    """Inverse sRGB transfer function."""
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def wavelength_to_srgb(nm: float) -> tuple[float, float, float]:
    """Encoded sRGB (0..1) display colour of a wavelength."""
    return tuple(linear_to_srgb(c) for c in wavelength_to_linear_rgb(nm))  # type: ignore[return-value]


def wavelength_to_hex(nm: float) -> str:
    """'#rrggbb' display colour of a wavelength."""
    return "#" + "".join(f"{round(255 * c):02x}" for c in wavelength_to_srgb(nm))


def hex_to_linear(h: str) -> tuple[float, float, float]:
    """'#rrggbb' -> linear sRGB triple."""
    h = h.lstrip("#")
    return tuple(srgb_to_linear(int(h[i : i + 2], 16) / 255.0) for i in (0, 2, 4))  # type: ignore[return-value]


def luminance(rgb_linear) -> float:
    """Relative luminance of a linear-sRGB colour."""
    r, g, b = rgb_linear
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def limit_luminance(rgb_linear, max_lum: float) -> tuple[float, float, float]:
    """Darken a colour (keeping hue and saturation) until its luminance is <= max_lum.

    Used by light-background styles so that yellow or cyan light still reads on white."""
    r, g, b = (linear_to_srgb(c) for c in rgb_linear)
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    for _ in range(40):
        lin = tuple(srgb_to_linear(c) for c in colorsys.hls_to_rgb(h, l, s))
        if luminance(lin) <= max_lum:
            return lin  # type: ignore[return-value]
        l *= 0.95
    return lin  # type: ignore[return-value]
