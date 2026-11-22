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
