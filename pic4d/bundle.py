"""Scene files and bundles.

``prepare(scene_file, out_dir)`` reads a scene file (TOML, see ``examples/``), the GDS and
the optional field, and writes a *bundle*: everything Blender needs, in plain JSON and
NumPy files, so the Blender side needs nothing but numpy::

    bundle/scene.json     units, layers, substrate, light, camera, legibility metadata
    bundle/geometry.npz   display polygons per layer (um)
    bundle/light_*.npy    light textures (arrival map and masks, or field phasors)

Coordinates stay in micrometres in the bundle; ``scene.json['units']`` says how Blender
maps them: X = (x - cx) * S, Y = (y - cy) * S, Z = z * S * z_scale.
"""

from __future__ import annotations

import json
import math
import tomllib
from pathlib import Path

import numpy as np

from . import colors
from .layout import Layer, Layout, legibility, min_feature_width, read_gds

__all__ = ["load_scene", "prepare", "SCENE_EXTENT"]

#: Size of the framed region in Blender units.
SCENE_EXTENT = 10.0


def load_scene(path: str | Path) -> dict:
    """Read a TOML scene file; relative file names are resolved against its folder."""
    path = Path(path)
    cfg = tomllib.loads(path.read_text())
    base = path.parent
    for sect, key in (("", "gds"), ("light", "field"), ("light", "weight")):
        d = cfg if not sect else cfg.get(sect, {})
        if d.get(key):
            d[key] = str((base / d[key]).resolve())
    cfg.setdefault("name", path.stem)
    return cfg


def _flatten(polys):
    if not polys:
        return np.zeros((0, 2)), np.zeros(1, dtype=np.int64)
    off = np.cumsum([0] + [len(p) for p in polys]).astype(np.int64)
    return np.concatenate(polys).astype(np.float64), off


def _clip_box(polys, box):
    """Keep the part of the polygons inside box = (x0, y0, x1, y1)."""
    import gdstk

    if not polys:
        return []
    r = gdstk.rectangle(box[:2], box[2:])
    out = gdstk.boolean([gdstk.Polygon(p) for p in polys], r, "and", precision=1e-4)
    return [np.asarray(p.points) for p in out]


def _clip_half(polys, y0):
    """Keep the part of the polygons with y >= y0 (cut-away for a vertical field plane)."""
    import gdstk

    if not polys:
        return []
    big = 1e7
    box = gdstk.rectangle((-big, y0), (big, big))
    out = gdstk.boolean([gdstk.Polygon(p) for p in polys], box, "and", precision=1e-4)
    return [np.asarray(p.points) for p in out]


def _blur_norm(mask, sigma_px, width_px):
    from scipy import ndimage

    if sigma_px <= 0.3:
        return mask.astype(np.float32)
    g = ndimage.gaussian_filter(mask.astype(np.float32), sigma_px)
    peak = math.erf(width_px / (2 * math.sqrt(2) * sigma_px))
    return np.clip(g / max(peak, 1e-3), 0, 1).astype(np.float32)


def prepare(scene_file: str | Path, out_dir: str | Path, *, layout: Layout | None = None) -> dict:
    """Build a bundle from a scene file. Returns the scene.json dictionary."""
    from .paths import Grid, extract, rasterize

    cfg = load_scene(scene_file) if not isinstance(scene_file, dict) else scene_file
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []

    lay = layout or read_gds(cfg["gds"], cfg.get("cell") or None)
    layers = [Layer.from_dict(d) for d in cfg.get("layer", [])]
    if not layers:
        raise ValueError("scene file needs at least one [[layer]]")
    missing = [l.gds for l in layers if l.gds not in lay.polygons]
    if missing:
        notes.append(f"layers {missing} have no polygons in the layout")
    layers = [l for l in layers if l.gds in lay.polygons]
    cores = [l for l in layers if l.role == "core"] or layers[:1]
    core_keys = sorted({l.gds for l in cores})
    core_polys_true = [p for k in core_keys for p in lay.merged(k)]

    lb = lay.bounds([l.gds for l in layers])
    frame = tuple(cfg.get("frame", lb))
    extent = max(frame[2] - frame[0], frame[3] - frame[1])
    cx, cy = 0.5 * (frame[0] + frame[2]), 0.5 * (frame[1] + frame[3])
    S = SCENE_EXTENT / extent

    disp = cfg.get("display", {})
    core_w = disp.get("core_width_um") or min_feature_width([p for k in core_keys for p in lay.polygons[k]])
    t_core = max(l.zmax - l.zmin for l in cores)
    leg = legibility(extent, core_w, t_core, min_width_frac=disp.get("min_width_frac", 0.0035),
                     relief=disp.get("relief", 1.0))
    if "widen_um" in disp:
        leg["widen_um"] = float(disp["widen_um"])
        leg["display_width_um"] = core_w + leg["widen_um"]
    if "z_scale" in disp:
        leg["z_scale"] = float(disp["z_scale"])
    leg["core_width_um"] = float(core_w)
    leg["extent_um"] = float(extent)
    zs = leg["z_scale"]

    light = dict(cfg.get("light", {}))
    mode = light.get("mode", "path")
    section_y = None
    fld = None
    if mode == "field":
        from .field import load_field

        fld = load_field(light["field"])
        if fld.plane == "xz" and light.get("section", True):
            section_y = fld.offset_um

    # --- geometry -----------------------------------------------------------------
    # [display] crop_to_frame = true cuts the die out at the framed region (a sample piece),
    # so a long chip does not run off the edge of the picture
    cm = 0.04 * extent
    crop_box = (frame[0] - cm, frame[1] - cm, frame[2] + cm, frame[3] + cm)
    geo = {}
    layer_meta = []
    for i, l in enumerate(layers):
        polys = lay.merged(l.gds, widen_um=leg["widen_um"] if l.role == "core" else 0.0)
        if section_y is not None:
            polys = _clip_half(polys, section_y)
        if disp.get("crop_to_frame"):
            polys = _clip_box(polys, crop_box)
        pts, off = _flatten(polys)
        geo[f"L{i}_pts"], geo[f"L{i}_off"] = pts, off
        layer_meta.append({"key": f"L{i}", "gds": list(l.gds), "zmin": l.zmin, "zmax": l.zmax,
                           "material": l.material, "role": l.role, "name": l.name or f"{l.gds[0]}/{l.gds[1]}",
                           "n_polygons": len(polys), "opacity": l.opacity})
    np.savez_compressed(out / "geometry.npz", **geo)

    sub = dict(cfg.get("substrate", {}))
    margin = sub.get("margin_um", 0.04 * extent)
    sb = [max(lb[0], frame[0] - 0.6 * extent) - margin, max(lb[1], frame[1] - 0.6 * extent) - margin,
          min(lb[2], frame[2] + 0.6 * extent) + margin, min(lb[3], frame[3] + 0.6 * extent) + margin]
    if disp.get("crop_to_frame"):
        sb = [max(sb[0], crop_box[0]), max(sb[1], crop_box[1]), min(sb[2], crop_box[2]), min(sb[3], crop_box[3])]
    if section_y is not None:
        sb[1] = section_y
    zmin_all = min(l.zmin for l in layers)
    # display thickness of the die block (scene units), deeper if a vertical field reaches below
    t_disp = sub.get("thickness_frac", 0.035) * SCENE_EXTENT
    if fld is not None and fld.plane in ("xz", "yz"):
        t_disp = max(t_disp, (zmin_all - fld.v[0]) * S * zs * 1.02)
    substrate = {"bounds": sb, "ztop_um": zmin_all, "thickness_scene": t_disp,
                 "material": sub.get("material", "substrate"), "box_um": sub.get("box_um")}
    clad = cfg.get("cladding")
    if clad:
        clad = {"zmin": clad.get("zmin", zmin_all), "zmax": clad["zmax"], "material": clad.get("material", "sio2"),
                "bounds": sb}

    # --- light ----------------------------------------------------------------------
    wl = light.get("wavelength_nm", [1550.0])
    wl = [float(w) for w in (wl if isinstance(wl, list) else [wl])]
    lmeta = {"mode": mode, "wavelength_nm": wl,
             "colour_linear": [list(colors.wavelength_to_linear_rgb(w)) for w in wl],
             "colour_hex": [colors.wavelength_to_hex(w) for w in wl],
             "z_top_um": max(l.zmax for l in cores), "caption": light.get("caption", "")}
    main_path = None
    if mode == "path":
        routes = light.get("routes")
        if routes is not None and len(routes) != len(wl):
            raise ValueError("light.routes needs one list of output points per wavelength")
        if len(wl) > 4:
            raise ValueError("path mode draws at most 4 wavelengths")
        wfun = None
        if light.get("weight"):
            from scipy.interpolate import RegularGridInterpolator

            wz = np.load(light["weight"])
            Iw0 = np.asarray(wz["I"], dtype=np.float64)
            ref = max(float(np.percentile(Iw0, light.get("percentile", 99.5))), 1e-30)
            interp = RegularGridInterpolator((wz["u"], wz["v"]), Iw0, bounds_error=False, fill_value=np.nan)
            gam = light.get("weight_gamma", 0.5)

            def wfun(xy):
                return np.clip(interp(np.asarray(xy)) / ref, 0, 1) ** gam

        pr = extract(core_polys_true, source=light.get("source"), coupling_gap_um=light.get("coupling_gap_um", 0.45),
                     routes=routes, n_wavelengths=len(wl), px=light.get("analysis_px_um"), min_width_um=core_w,
                     weight=wfun)
        notes += pr.notes
        # texture grid over the frame (plus the part of the layout around it)
        tb = [max(lb[0], sb[0]), max(lb[1], sb[1]), min(lb[2], sb[2]), min(lb[3], sb[3])]
        dw = leg["display_width_um"]
        tpx = max(dw / 3.0, max(tb[2] - tb[0], tb[3] - tb[1]) / light.get("texture_max", 4096))
        tg = Grid.around(tb, tpx, margin=2 * dw)
        disp_polys = [p for k in core_keys for p in lay.merged(k, widen_um=leg["widen_um"])]
        dmask = rasterize(disp_polys, tg)
        # sample the analysis maps at texture pixel centres (nearest)
        cen = tg.to_um(np.stack(np.meshgrid(np.arange(tg.ny), np.arange(tg.nx), indexing="ij"), -1))
        ij = np.round(pr.grid.to_px(cen)).astype(np.int64)
        jj = np.clip(ij[..., 1], 0, pr.grid.ny - 1)
        ii = np.clip(ij[..., 0], 0, pr.grid.nx - 1)
        arr = pr.arrival[jj, ii]
        reach = np.isfinite(arr)
        glow = _blur_norm(dmask, light.get("glow_um", 1.6 * dw) / tpx, dw / tpx)
        weight = np.ones_like(glow)
        if wfun is not None:
            wpx = wfun(np.stack([cen[..., 0], cen[..., 1]], -1))  # the map's own detail inside it
            weight = np.where(np.isnan(wpx), pr.weight[jj, ii], wpx).astype(np.float32)
        tex = np.stack([np.where(reach, arr, -1.0), reach.astype(np.float32), glow, weight], -1).astype(np.float32)
        np.save(out / "light_arrival.npy", tex)
        rm = np.zeros((tg.ny, tg.nx, 4), dtype=np.float32)
        for k in range(len(wl)):
            rm[..., k] = pr.route_masks[k][jj, ii]
        np.save(out / "light_routes.npy", rm)
        L = pr.max_arrival
        n_p = light.get("pulses", 3)
        lmeta.update({"weighted": bool(light.get("weight")), "texture_bounds_um": list(tg.extent), "max_arrival_um": L, "spacing_um": L / n_p,
                      "pulses_per_loop": int(light.get("pulses_per_loop", n_p)), "source_um": list(pr.source),
                      "endpoints_um": pr.endpoints.round(3).tolist(), "files": ["light_arrival.npy", "light_routes.npy"]})
        main_path = pr.main_path
    elif mode == "field":
        f = fld.resampled(light.get("texture_max", 1024))
        En = f.normalised(light.get("percentile", 99.7))
        files = []
        for k in range(f.n):
            a = np.stack([En[k].real, En[k].imag, np.abs(En[k]), np.ones(En[k].shape, np.float32)], -1)
            a = np.transpose(a, (1, 0, 2)).astype(np.float32)  # (nv, nu, 4): image rows along v
            fn = f"light_field_{k}.npy"
            np.save(out / fn, a)
            files.append(fn)
        if light.get("wavelength_nm") is None:
            wl = [float(w) for w in f.wavelength_nm]
            lmeta.update({"wavelength_nm": wl, "colour_linear": [list(colors.wavelength_to_linear_rgb(w)) for w in wl],
                          "colour_hex": [colors.wavelength_to_hex(w) for w in wl]})
        elif len(wl) != f.n:
            wl = (wl * f.n)[: f.n]
            lmeta.update({"wavelength_nm": wl, "colour_linear": [list(colors.wavelength_to_linear_rgb(w)) for w in wl],
                          "colour_hex": [colors.wavelength_to_hex(w) for w in wl]})
        lam0 = min(wl)
        cyc = light.get("cycles", 8)
        lmeta.update({"plane": f.plane, "offset_um": f.offset_um, "extent_um": list(f.extent()), "files": files,
                      "cycles": [int(round(cyc * lam0 / w)) for w in wl], "sequence": bool(light.get("sequence", False)),
                      "crossfade": float(light.get("crossfade", 0.25)), "labels": list(f.labels),
                      "section": section_y is not None})
        if disp.get("crop_to_frame"):  # draw the field plane only over the cut-out
            e = list(f.extent())
            ax = {"xy": (0, 1), "xz": (0, None), "yz": (1, None)}[f.plane]
            for k, a in enumerate(ax):
                if a is not None:
                    e[k] = max(e[k], crop_box[a])
                    e[k + 2] = min(e[k + 2], crop_box[a + 2])
            lmeta["plane_bounds_um"] = e
    elif mode == "intensity":
        # a simulated |E|^2 map, drawn as a glow whose brightness is modulated in time;
        # the map is simulated, the modulation is only an illustration
        w = np.load(light["weight"])
        I = np.asarray(w["I"], dtype=np.float32)
        bg = float(np.percentile(I, light.get("background_percentile", 50))) if light.get("subtract_background", True) else 0.0
        top = max(float(np.percentile(I, light.get("percentile", 99.5))) - bg, 1e-30)
        In = np.clip((I - bg) / top, 0, 1) ** light.get("weight_gamma", 0.5)
        a = np.stack([In, np.ones_like(In), np.ones_like(In), np.ones_like(In)], -1)
        np.save(out / "light_intensity.npy", np.transpose(a, (1, 0, 2)).astype(np.float32))
        u, v = np.asarray(w["u"]), np.asarray(w["v"])
        lmeta.update({"plane": "xy", "offset_um": float(light.get("offset_um", lmeta["z_top_um"])),
                      "extent_um": [float(u[0]), float(v[0]), float(u[-1]), float(v[-1])],
                      "files": ["light_intensity.npy"], "beats": int(light.get("beats", 3))})
        if disp.get("crop_to_frame"):
            e = lmeta["extent_um"]
            lmeta["plane_bounds_um"] = [max(e[0], crop_box[0]), max(e[1], crop_box[1]), min(e[2], crop_box[2]), min(e[3], crop_box[3])]
    if lmeta.get("plane_bounds_um"):  # and no wider than the cut-out die
        e = lmeta["plane_bounds_um"]
        e[0], e[2] = max(e[0], sb[0]), min(e[2], sb[2])
        if lmeta.get("plane", "xy") == "xy":
            e[1], e[3] = max(e[1], sb[1]), min(e[3], sb[3])
    lmeta["base"] = light.get("base")

    cam = dict(cfg.get("camera", {}))
    meta = {
        "pic4d": 1,
        "name": cfg.get("name"),
        "subtitle": cfg.get("subtitle", ""),
        "caption": light.get("caption", ""),
        "units": {"S": S, "origin_um": [cx, cy], "z_scale": zs, "scene_extent": SCENE_EXTENT},
        "frame_um": list(frame),
        "frame_explicit": "frame" in cfg and not disp.get("crop_to_frame"),
        "layout_bounds_um": list(lb),
        "layers": layer_meta,
        "substrate": substrate,
        "cladding": clad,
        "light": lmeta,
        "camera": cam,
        "legibility": leg,
        "style_overrides": cfg.get("style_overrides", {}),
        "main_path_um": None if main_path is None else main_path[:: max(1, len(main_path) // 600)].round(4).tolist(),
        "notes": notes,
        "source_gds": Path(cfg["gds"]).name,
    }
    (out / "scene.json").write_text(json.dumps(meta, indent=1))
    return meta
