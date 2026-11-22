import json
from pathlib import Path

import numpy as np
import pytest

from pic4d.bundle import prepare
from pic4d.styles import list_styles, load_style

DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo" / "scene.toml"


def test_prepare_demo(tmp_path):
    pytest.importorskip("skimage")
    m = prepare(DEMO, tmp_path)
    assert (tmp_path / "scene.json").exists()
    assert json.loads((tmp_path / "scene.json").read_text())["light"]["mode"] == "path"
    a = np.load(tmp_path / "light_arrival.npy")
    assert a.shape[-1] == 4 and a[..., 0].max() > 100  # arrival distances along ~130 um of guide
    assert m["legibility"]["z_scale"] >= 1
    assert m["light"]["colour_hex"][0].startswith("#")
    g = np.load(tmp_path / "geometry.npz")
    assert len(g.files) == 4  # two layers, points and offsets each


def test_styles_load():
    names = list_styles()
    for need in ("white", "black", "dark", "blueprint"):
        assert need in names
    for n in names:
        st = load_style(n)
        assert "materials" in st and "light" in st and st["background"].startswith("#")


def test_crop_to_frame(tmp_path):
    pytest.importorskip("skimage")
    from pic4d.bundle import load_scene

    cfg = load_scene(DEMO)
    cfg["frame"] = [0.0, -5.0, 40.0, 5.0]
    cfg.setdefault("display", {})["crop_to_frame"] = True
    m = prepare(cfg, tmp_path)
    sb = m["substrate"]["bounds"]
    assert sb[2] < 50 and sb[0] >= -2  # the die is cut out around the frame
    g = np.load(tmp_path / "geometry.npz")
    assert g["L1_pts"][:, 0].max() <= 42.0


def test_vertical_plane_crop_and_fade(tmp_path, ybranch_gds):
    """plane_z_um keeps a height range of an xz field plane (and the die no deeper than it);
    plane_fade is passed on as [sides, top, bottom]."""
    from pic4d.field import save_field

    u, v = np.linspace(-2, 32, 69), np.linspace(-3.0, 2.5, 23)
    E = np.exp(1j * 2 * np.pi * u[:, None] / 0.6) * np.ones_like(v)[None]
    fld = tmp_path / "field.npz"
    save_field(fld, E, u, v, 1550.0, plane="xz", offset_um=0.0)
    cfg = {"gds": str(ybranch_gds), "layer": [{"gds": [1, 0], "zmin": 0.0, "zmax": 0.22, "role": "core"}],
           "display": {"widen_um": 0.0, "z_scale": 1.0}, "light": {"mode": "field", "field": str(fld)}}
    deep = prepare(cfg, tmp_path / "a")
    cfg["light"].update({"plane_z_um": [-0.6, 1.7], "plane_fade": [0.06, 0.3, 0.0]})
    m = prepare(cfg, tmp_path / "b")
    pb = m["light"]["plane_bounds_um"]
    assert pb[1] == pytest.approx(-0.6) and pb[3] == pytest.approx(1.7)
    assert m["light"]["plane_fade"] == [0.06, 0.3, 0.0]
    assert deep["light"]["plane_fade"] == [0.0, 0.0, 0.0]
    # the die reaches 3 um down for the uncropped plane, only 0.6 um (or its default) when cropped
    assert m["substrate"]["thickness_scene"] < 0.5 * deep["substrate"]["thickness_scene"]
    cfg["light"]["plane_z_um"] = [1.0, 0.5]
    with pytest.raises(ValueError):
        prepare(cfg, tmp_path / "c")
    cfg["light"].update({"plane_z_um": [-0.6, 1.7], "plane_fade": 0.8})
    with pytest.raises(ValueError):
        prepare(cfg, tmp_path / "d")
