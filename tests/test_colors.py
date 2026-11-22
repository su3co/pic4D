import colorsys

from pic4d import colors


def hue(nm):
    r, g, b = colors.wavelength_to_srgb(nm)
    return colorsys.rgb_to_hsv(r, g, b)[0] * 360


def test_hex_format():
    h = colors.wavelength_to_hex(1550)
    assert h.startswith("#") and len(h) == 7


def test_o_band_channels_are_distinct():
    hs = [hue(nm) for nm in (1271, 1291, 1311, 1331)]
    for a, b in zip(hs, hs[1:]):
        assert abs(a - b) > 12, hs
    assert abs(hue(1271) - hue(1331)) > 60


def test_visible_colours_are_familiar():
    r, g, b = colors.wavelength_to_srgb(532)
    assert g > r and g > b  # green laser looks green
    r, g, b = colors.wavelength_to_srgb(450)
    assert b > r and b > g
    r, g, b = colors.wavelength_to_srgb(650)
    assert r > 0.9 and g < 0.2


def test_c_band_is_warm_and_nir_is_purple():
    r, g, b = colors.wavelength_to_srgb(1550)
    assert r == max(r, g, b) and b < 0.05
    r, g, b = colors.wavelength_to_srgb(1000)
    assert r > 0.3 and b > 0.3 and g < 0.05


def test_display_wavelength_monotonic_in_telecom():
    xs = range(1260, 1676, 5)
    ds = [colors.display_wavelength(x) for x in xs]
    assert all(b >= a for a, b in zip(ds, ds[1:]))


def test_band_names():
    assert colors.band_name(1550) == "C band"
    assert colors.band_name(1310) == "O band"
    assert colors.band_name(785) == "NIR"


def test_limit_luminance():
    c = colors.wavelength_to_linear_rgb(560)  # yellow, very bright
    d = colors.limit_luminance(c, 0.3)
    assert colors.luminance(d) <= 0.3 + 1e-6
