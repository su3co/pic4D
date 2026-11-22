"""Build a pic4D scene inside Blender from a bundle (needs bpy and numpy only).

    from pic4d.blender.build import build_scene, render_frames
    info = build_scene("bundle/", style="black", width=1920, height=1080, fps=30, seconds=7)
    render_frames("out/frames", 1, 210)

The scene is fully animated (per-frame keys on the camera and on the light clock), so
the saved .blend plays back in Blender's UI exactly as rendered.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import bpy
import bmesh
import numpy as np
from mathutils import Matrix, Vector
from mathutils.geometry import tessellate_polygon

from .. import camera as cammod
from .. import colors
from ..styles import load_style

PREFIX = "pic4d"


# ----------------------------------------------------------------------------- helpers
def _lin(hexstr):
    return (*colors.hex_to_linear(hexstr), 1.0)


def _clear_scene(clear=True):
    """Headless: start from an empty factory scene. In the UI (clear=False): a new scene."""
    if clear:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        sc = bpy.context.scene
    else:
        sc = bpy.data.scenes.new("pic4D")
        bpy.context.window.scene = sc
    sc.unit_settings.system = "NONE"
    return sc


class _U:
    """Bundle units: micrometres -> Blender units."""

    def __init__(self, meta):
        u = meta["units"]
        self.S = u["S"]
        self.cx, self.cy = u["origin_um"]
        self.zs = u["z_scale"]

    def x(self, x):
        return (np.asarray(x) - self.cx) * self.S

    def y(self, y):
        return (np.asarray(y) - self.cy) * self.S

    def z(self, z):
        return np.asarray(z) * self.S * self.zs


def _nodes(mat):
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    return nt, nt.nodes, nt.links


def _math(nodes, links, op, a, b=None, clamp=False, loc=(0, 0)):
    n = nodes.new("ShaderNodeMath")
    n.operation = op
    n.use_clamp = clamp
    n.location = loc
    for i, v in enumerate((a, b)):
        if v is None:
            continue
        if isinstance(v, (int, float)):
            n.inputs[i].default_value = float(v)
        else:
            links.new(v, n.inputs[i])
    return n.outputs[0]


def _vmath(nodes, links, op, a, b=None, scale=None):
    n = nodes.new("ShaderNodeVectorMath")
    n.operation = op
    for i, v in enumerate((a, b)):
        if v is None:
            continue
        if isinstance(v, (tuple, list)):
            n.inputs[i].default_value = tuple(v)
        else:
            links.new(v, n.inputs[i])
    if scale is not None:
        if isinstance(scale, (int, float)):
            n.inputs["Scale"].default_value = float(scale)
        else:
            links.new(scale, n.inputs["Scale"])
    return n.outputs[0] if op != "DOT_PRODUCT" else n.outputs[1]


def _principled(nodes, spec, base=None):
    p = nodes.new("ShaderNodeBsdfPrincipled")
    p.inputs["Base Color"].default_value = _lin(spec.get("color", "#cccccc"))
    p.inputs["Roughness"].default_value = spec.get("roughness", 0.5)
    p.inputs["Metallic"].default_value = spec.get("metallic", 0.0)
    if "specular" in spec:
        p.inputs["Specular IOR Level"].default_value = spec["specular"]
    if spec.get("alpha", 1.0) < 1.0:
        p.inputs["Alpha"].default_value = spec["alpha"]
    return p


def solid_material(name, spec):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    nt, nodes, links = _nodes(mat)
    out = nodes.new("ShaderNodeOutputMaterial")
    p = _principled(nodes, spec)
    links.new(p.outputs[0], out.inputs["Surface"])
    if spec.get("alpha", 1.0) < 1.0:
        mat.surface_render_method = "BLENDED"
        mat.use_transparency_overlap = False
    mat.diffuse_color = _lin(spec.get("color", "#cccccc"))
    return mat


def _image_from_array(name, arr, folder: Path | None):
    """Float RGBA image from an (h, w, 4) array; saved as EXR next to the bundle if possible."""
    h, w = arr.shape[:2]
    img = bpy.data.images.get(name)
    if img is not None:
        bpy.data.images.remove(img)
    img = bpy.data.images.new(name, w, h, alpha=True, float_buffer=True)
    img.colorspace_settings.name = "Non-Color"
    img.alpha_mode = "CHANNEL_PACKED"
    img.pixels.foreach_set(np.ascontiguousarray(arr, dtype=np.float32).ravel())
    if folder is not None:
        try:
            img.filepath_raw = str(folder / f"{name}.exr")
            img.file_format = "OPEN_EXR"
            img.save()
            img.source = "FILE"
            img.reload()
            img.colorspace_settings.name = "Non-Color"
            img.alpha_mode = "CHANNEL_PACKED"
        except Exception:  # pragma: no cover - keep the generated image
            pass
    return img


# ----------------------------------------------------------------------------- geometry
def _ccw(p):
    x, y = p[:, 0], p[:, 1]
    return np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)) > 0


def _clean(p, eps):
    keep = np.ones(len(p), dtype=bool)
    d = np.linalg.norm(np.diff(p, axis=0, append=p[:1]), axis=1)
    keep &= d > eps
    q = p[keep]
    return q if len(q) >= 3 else None


def extrude_polygons(name, polys_scene, z0, z1, material, bevel=0.0, collection=None):
    """One mesh object of prisms from 2-D polygons (scene units)."""
    bm = bmesh.new()
    for p in polys_scene:
        p = _clean(np.asarray(p, dtype=np.float64), 1e-7)
        if p is None:
            continue
        if not _ccw(p):
            p = p[::-1]
        n = len(p)
        vb = [bm.verts.new((float(x), float(y), z0)) for x, y in p]
        vt = [bm.verts.new((float(x), float(y), z1)) for x, y in p]
        tris = tessellate_polygon([[Vector((float(x), float(y), 0.0)) for x, y in p]])
        for a, b, c in tris:
            try:
                bm.faces.new((vt[a], vt[b], vt[c]))
                bm.faces.new((vb[c], vb[b], vb[a]))
            except ValueError:
                pass
        for i in range(n):
            j = (i + 1) % n
            try:
                bm.faces.new((vb[i], vb[j], vt[j], vt[i]))
            except ValueError:
                pass
    bm.normal_update()
    # orient top faces up (tessellation order is not guaranteed)
    for f in bm.faces:
        c = f.calc_center_median()
        if abs(f.normal.z) > 0.9:
            want = 1.0 if abs(c.z - z1) < abs(c.z - z0) else -1.0
            if f.normal.z * want < 0:
                f.normal_flip()
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    (collection or bpy.context.scene.collection).objects.link(ob)
    me.materials.append(material)
    if bevel > 0:
        m = ob.modifiers.new("bevel", "BEVEL")
        m.width = bevel
        m.segments = 2
        m.limit_method = "ANGLE"
        m.angle_limit = math.radians(40)
        m.use_clamp_overlap = True
        m.harden_normals = False
    return ob


def box(name, x0, y0, x1, y1, z0, z1, material, bevel=0.0):
    p = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
    return extrude_polygons(name, [p], z0, z1, material, bevel)


# ----------------------------------------------------------------------------- clock
def clock_group(n_weights: int):
    """Shader node group with the animated values: 'loop' (0..1 over the clip) and
    'w0'..'wn' (sequence weights). Keyed per frame by :func:`key_clock`."""
    name = f"{PREFIX}_clock"
    ng = bpy.data.node_groups.get(name)
    if ng:
        return ng
    ng = bpy.data.node_groups.new(name, "ShaderNodeTree")
    out = ng.nodes.new("NodeGroupOutput")
    names = ["loop"] + [f"w{k}" for k in range(n_weights)]
    for i, nm in enumerate(names):
        ng.interface.new_socket(name=nm, in_out="OUTPUT", socket_type="NodeSocketFloat")
        v = ng.nodes.new("ShaderNodeValue")
        v.name = v.label = nm
        v.location = (-300, -120 * i)
        v.outputs[0].default_value = 1.0 if nm.startswith("w") else 0.0
        ng.links.new(v.outputs[0], out.inputs[i])
    return ng


def sequence_weights(n, u, crossfade):
    """Weights of n entries shown one after the other over the loop (u in [0, 1)),
    with smooth cross-fades of `crossfade` (fraction of each entry's slot)."""
    if n == 1:
        return [1.0]
    pos = u * n
    w = []
    for k in range(n):
        d = (pos - k) % n  # 0..n, entry k is fully on for d in [cf/2, 1 - cf/2]
        cf = max(1e-3, crossfade)

        def ramp(x):
            x = min(max(x, 0.0), 1.0)
            return x * x * (3 - 2 * x)

        up = ramp((d + cf / 2) / cf) if d < cf / 2 else (ramp((d - (n - cf / 2)) / cf) if d > n - cf / 2 else 1.0)
        down = 1.0 - ramp((d - (1 - cf / 2)) / cf) if 1 - cf / 2 < d < 1 + cf / 2 else (0.0 if 1 + cf / 2 <= d <= n - cf / 2 else 1.0)
        w.append(float(min(up, down)) if d < 1 + cf / 2 or d > n - cf / 2 else 0.0)
    s = sum(w)
    return [x / s for x in w] if s > 0 else w


def key_clock(ng, f0, n_frames, weights_fn=None):
    loop = ng.nodes["loop"].outputs[0]
    for i in range(n_frames + 1):
        f = f0 + i
        u = i / n_frames
        loop.default_value = u
        loop.keyframe_insert("default_value", frame=f)
        if weights_fn:
            for k, w in enumerate(weights_fn(u % 1.0)):
                s = ng.nodes[f"w{k}"].outputs[0]
                s.default_value = w
                s.keyframe_insert("default_value", frame=f)


# ----------------------------------------------------------------------------- light shaders
class _LightGraph:
    """Builds the light emission (colour, amount) inside one material."""

    def __init__(self, nodes, links, meta, style, units, clock):
        self.n, self.l, self.meta, self.style, self.u = nodes, links, meta, style, units
        g = nodes.new("ShaderNodeGroup")
        g.node_tree = clock
        self.clock = g

    def _uv(self, bounds_um, axes="xy"):
        """Texture coordinates from world position for a rectangle given in um."""
        n, l, u = self.n, self.l, self.u
        geo = n.new("ShaderNodeNewGeometry")
        sep = n.new("ShaderNodeSeparateXYZ")
        l.new(geo.outputs["Position"], sep.inputs[0])
        a0, b0, a1, b1 = bounds_um
        conv = {"x": (u.x, 0), "y": (u.y, 1), "z": (u.z, 2)}
        comps = []
        for ax, lo, hi in ((axes[0], a0, a1), (axes[1], b0, b1)):
            fn, idx = conv[ax]
            s0, s1 = float(fn(lo)), float(fn(hi))
            t = _math(n, l, "SUBTRACT", sep.outputs[idx], s0)
            comps.append(_math(n, l, "DIVIDE", t, s1 - s0))
        comb = n.new("ShaderNodeCombineXYZ")
        l.new(comps[0], comb.inputs[0])
        l.new(comps[1], comb.inputs[1])
        return comb.outputs[0]

    def _tex(self, img, uv, clip=False):
        t = self.n.new("ShaderNodeTexImage")
        t.image = img
        t.interpolation = "Linear"
        t.extension = "CLIP" if clip else "EXTEND"
        self.l.new(uv, t.inputs[0])
        sep = self.n.new("ShaderNodeSeparateColor")
        self.l.new(t.outputs["Color"], sep.inputs[0])
        return [sep.outputs[0], sep.outputs[1], sep.outputs[2], t.outputs["Alpha"]]

    def _combine(self, amounts):
        """amounts: list of (amount socket, colour linear) -> (colour, total amount)."""
        n, l = self.n, self.l
        total = None
        col = None
        for amt, c in amounts:
            term = _vmath(n, l, "SCALE", tuple(c), None, scale=amt)
            col = term if col is None else _vmath(n, l, "ADD", col, term)
            total = amt if total is None else _math(n, l, "ADD", total, amt)
        norm = _math(n, l, "MAXIMUM", total, 1e-4)
        inv = _math(n, l, "DIVIDE", 1.0, norm)
        col = _vmath(n, l, "SCALE", col, None, scale=inv)
        return col, total

    def path(self, imgs, glow=False):
        n, l, L = self.n, self.l, self.meta["light"]
        st = self.style["light"]
        tb = L["texture_bounds_um"]
        uv = self._uv(tb)
        arr = self._tex(imgs["arrival"], uv)
        rts = self._tex(imgs["routes"], uv)
        D, reach, glowm, weight = arr
        loop = self.clock.outputs["loop"]
        m = float(L["pulses_per_loop"])
        sp = float(L["spacing_um"])
        nw = len(L["wavelength_nm"])
        base = L.get("base") if L.get("base") is not None else st.get("base", 0.08)
        sharp = st.get("pulse_sharpness", 5.0)
        amounts = []
        for k in range(nw):
            x = _math(n, l, "MULTIPLY", loop, m)
            x = _math(n, l, "SUBTRACT", x, _math(n, l, "DIVIDE", D, sp))
            x = _math(n, l, "ADD", x, k / nw)
            s = _math(n, l, "FRACT", x)
            head = _math(n, l, "MULTIPLY", _math(n, l, "SUBTRACT", 1.0, s), 30.0, clamp=True)
            b = _math(n, l, "MULTIPLY", _math(n, l, "POWER", s, sharp), head)
            b = _math(n, l, "ADD", _math(n, l, "MULTIPLY", b, 1.0 - base), base)
            mask = _math(n, l, "MULTIPLY", rts[k], glowm if glow else reach)
            if glow:
                mask = _math(n, l, "MULTIPLY", mask, _math(n, l, "GREATER_THAN", D, -0.5))
            b = _math(n, l, "MULTIPLY", _math(n, l, "MULTIPLY", b, mask), weight)
            amounts.append((b, self._display_colour(L["colour_linear"][k])))
        return self._combine(amounts)

    def field(self, imgs):
        n, l, L = self.n, self.l, self.meta["light"]
        st = self.style["light"]
        axes = L["plane"]
        uv = self._uv(L["extent_um"], axes)
        loop = self.clock.outputs["loop"]
        gamma = st.get("field_gamma", 1.0)
        neg = st.get("field_negative", 0.4)
        amounts = []
        for k, img in enumerate(imgs["field"]):
            re, im, mag, _ = self._tex(img, uv, clip=True)
            ph = _math(n, l, "MULTIPLY", loop, 2 * math.pi * L["cycles"][k])
            v = _math(n, l, "ADD", _math(n, l, "MULTIPLY", re, _math(n, l, "COSINE", ph)),
                      _math(n, l, "MULTIPLY", im, _math(n, l, "SINE", ph)))
            pos = _math(n, l, "POWER", _math(n, l, "MAXIMUM", v, 0.0), gamma)
            ng = _math(n, l, "POWER", _math(n, l, "MAXIMUM", _math(n, l, "MULTIPLY", v, -1.0), 0.0), gamma)
            b = _math(n, l, "ADD", pos, _math(n, l, "MULTIPLY", ng, neg))
            b = _math(n, l, "MINIMUM", b, 1.5)
            if L.get("sequence"):
                b = _math(n, l, "MULTIPLY", b, self.clock.outputs[f"w{k}"])
            amounts.append((b, self._display_colour(L["colour_linear"][k])))
        return self._combine(amounts)

    def intensity(self, imgs):
        n, l, L = self.n, self.l, self.meta["light"]
        uv = self._uv(L["extent_um"], "xy")
        r, _, _, _ = self._tex(imgs["intensity"], uv, clip=True)
        loop = self.clock.outputs["loop"]
        ph = _math(n, l, "MULTIPLY", loop, 2 * math.pi * L.get("beats", 3))
        mod = _math(n, l, "ADD", _math(n, l, "MULTIPLY", _math(n, l, "COSINE", ph), -0.35), 0.65)
        b = _math(n, l, "MULTIPLY", r, mod)
        return self._combine([(b, self._display_colour(L["colour_linear"][0]))])

    def _display_colour(self, c):
        ml = self.style["light"].get("max_luminance")
        return colors.limit_luminance(c, ml) if ml else tuple(c)


def light_material(name, kind, base_spec, meta, style, units, clock, imgs, glow=False):
    """kind: 'path' or 'field'. glow=False -> a solid core with light inside; glow=True ->
    a transparent plane with light only."""
    st = style["light"]
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    nt, nodes, links = _nodes(mat)
    out = nodes.new("ShaderNodeOutputMaterial")
    lg = _LightGraph(nodes, links, meta, style, units, clock)
    col, amt = {"path": lambda: lg.path(imgs, glow=glow), "field": lambda: lg.field(imgs),
                "intensity": lambda: lg.intensity(imgs)}[kind]()
    em = nodes.new("ShaderNodeEmission")
    links.new(col, em.inputs["Color"])
    add = st.get("blend", "over") == "add"
    if glow:
        tr = nodes.new("ShaderNodeBsdfTransparent")
        if add:
            links.new(_math(nodes, links, "MULTIPLY", amt, st.get("glow_strength", 1.0)), em.inputs["Strength"])
            mix = nodes.new("ShaderNodeAddShader")
            links.new(tr.outputs[0], mix.inputs[0])
            links.new(em.outputs[0], mix.inputs[1])
        else:
            em.inputs["Strength"].default_value = st.get("glow_strength", 1.0)
            mix = nodes.new("ShaderNodeMixShader")
            fac = _math(nodes, links, "MULTIPLY", amt, st.get("glow_opacity" if kind == "path" else "field_opacity", 0.6), clamp=True)
            links.new(fac, mix.inputs[0])
            links.new(tr.outputs[0], mix.inputs[1])
            links.new(em.outputs[0], mix.inputs[2])
        links.new(mix.outputs[0], out.inputs["Surface"])
        mat.surface_render_method = "BLENDED"
        mat.use_transparency_overlap = True
        mat.use_backface_culling = False
    else:
        base = _principled(nodes, base_spec)
        if add:
            links.new(_math(nodes, links, "MULTIPLY", amt, st.get("core_strength", 5.0)), em.inputs["Strength"])
            mix = nodes.new("ShaderNodeAddShader")
            links.new(base.outputs[0], mix.inputs[0])
            links.new(em.outputs[0], mix.inputs[1])
        else:
            em.inputs["Strength"].default_value = st.get("core_strength", 1.0)
            mix = nodes.new("ShaderNodeMixShader")
            fac = _math(nodes, links, "MULTIPLY", amt, st.get("core_gain", 1.0), clamp=True)
            links.new(fac, mix.inputs[0])
            links.new(base.outputs[0], mix.inputs[1])
            links.new(em.outputs[0], mix.inputs[2])
        links.new(mix.outputs[0], out.inputs["Surface"])
    return mat


# ----------------------------------------------------------------------------- world, ground, lights
def setup_world(style):
    sc = bpy.context.scene
    w = bpy.data.worlds.new(f"{PREFIX}_world")
    sc.world = w
    w.use_nodes = True
    nt = w.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld")
    bg_cam = nt.nodes.new("ShaderNodeBackground")
    bg_cam.inputs["Color"].default_value = _lin(style["background"])
    bg_cam.inputs["Strength"].default_value = 1.0
    bg_lit = nt.nodes.new("ShaderNodeBackground")
    bg_lit.inputs["Color"].default_value = _lin(style.get("world_color", "#ffffff"))
    bg_lit.inputs["Strength"].default_value = style.get("world_strength", 0.3)
    lp = nt.nodes.new("ShaderNodeLightPath")
    mix = nt.nodes.new("ShaderNodeMixShader")
    nt.links.new(lp.outputs["Is Camera Ray"], mix.inputs[0])
    nt.links.new(bg_lit.outputs[0], mix.inputs[1])
    nt.links.new(bg_cam.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs["Surface"])


def _vref(style):
    """Unshadowed linear brightness of a white diffuse ground (sun + world)."""
    v = style.get("world_strength", 0.0)
    for li in style.get("lights", []):
        if li["type"] == "SUN":
            v += li["strength"] * math.sin(math.radians(li["elevation"])) / math.pi
    return max(v, 1e-3)


def ground(style, z, size):
    g = style["ground"]
    mat = bpy.data.materials.new(f"{PREFIX}_ground")
    nt, nodes, links = _nodes(mat)
    out = nodes.new("ShaderNodeOutputMaterial")
    if g["kind"] in ("shadow_white", "flat_shadow"):
        d = nodes.new("ShaderNodeBsdfDiffuse")
        d.inputs["Color"].default_value = (1, 1, 1, 1)
        s2r = nodes.new("ShaderNodeShaderToRGB")
        links.new(d.outputs[0], s2r.inputs[0])
        bw = nodes.new("ShaderNodeRGBToBW")
        links.new(s2r.outputs["Color"], bw.inputs[0])
        f = _math(nodes, links, "DIVIDE", bw.outputs[0], 0.96 * _vref(style))
        f = _math(nodes, links, "MINIMUM", f, 1.0)
        val = _math(nodes, links, "ADD", _math(nodes, links, "MULTIPLY", _math(nodes, links, "SUBTRACT", 1.0, f), -g.get("shadow", 0.4)), 1.0)
        em = nodes.new("ShaderNodeEmission")
        comb = nodes.new("ShaderNodeCombineColor")
        for i in range(3):
            links.new(val, comb.inputs[i])
        bgc = colors.hex_to_linear(style["background"])
        tint = nodes.new("ShaderNodeMix")
        tint.data_type = "RGBA"
        tint.blend_type = "MULTIPLY"
        tint.inputs[0].default_value = 1.0
        links.new(comb.outputs[0], tint.inputs[6])
        tint.inputs[7].default_value = (*bgc, 1)
        links.new(tint.outputs[2], em.inputs["Color"])
        links.new(em.outputs[0], out.inputs["Surface"])
    elif g["kind"] == "grid":
        p = _principled(nodes, {"color": g["color"], "roughness": g.get("roughness", 0.6)})
        geo = nodes.new("ShaderNodeNewGeometry")
        sep = nodes.new("ShaderNodeSeparateXYZ")
        links.new(geo.outputs["Position"], sep.inputs[0])
        sp = g.get("spacing_frac", 0.04) * 10.0
        lines = None
        for idx in (0, 1):
            fr = _math(nodes, links, "FRACT", _math(nodes, links, "DIVIDE", sep.outputs[idx], sp))
            dist = _math(nodes, links, "MINIMUM", fr, _math(nodes, links, "SUBTRACT", 1.0, fr))
            ln = _math(nodes, links, "LESS_THAN", dist, 0.025)
            lines = ln if lines is None else _math(nodes, links, "MAXIMUM", lines, ln)
        mixc = nodes.new("ShaderNodeMix")
        mixc.data_type = "RGBA"
        links.new(lines, mixc.inputs[0])
        mixc.inputs[6].default_value = _lin(g["color"])
        mixc.inputs[7].default_value = _lin(g["line"])
        links.new(mixc.outputs[2], p.inputs["Base Color"])
        links.new(p.outputs[0], out.inputs["Surface"])
    else:
        p = _principled(nodes, {"color": g.get("color", "#050608"), "roughness": g.get("roughness", 0.25)})
        links.new(p.outputs[0], out.inputs["Surface"])
    return box(f"{PREFIX}_ground", -size, -size, size, size, z - 0.01, z, mat)


def setup_lights(style, target, extent):
    for i, li in enumerate(style.get("lights", [])):
        if li["strength"] <= 0:
            continue
        ld = bpy.data.lights.new(f"{PREFIX}_light{i}", li["type"])
        ld.color = colors.hex_to_linear(li.get("color", "#ffffff"))
        ob = bpy.data.objects.new(ld.name, ld)
        bpy.context.scene.collection.objects.link(ob)
        dirv = cammod.direction(li["azimuth"], li["elevation"])
        dist = 2.0 * extent
        ob.location = Vector(target) + Vector(dirv * dist)
        rot = cammod.look_rotation(ob.location, target, cammod.up_vector(li["azimuth"], li["elevation"]))
        ob.rotation_mode = "QUATERNION"
        ob.rotation_quaternion = Matrix(rot.tolist()).to_quaternion()
        if li["type"] == "SUN":
            ld.energy = li["strength"]
            ld.angle = math.radians(li.get("angle", 5))
        else:
            ld.energy = li["strength"] * (dist ** 2) * 12.0
            ld.size = li.get("size", 1.0) * extent * 0.4
        try:
            ld.use_shadow_jitter = True
        except AttributeError:
            pass


def setup_compositor(style):
    b = style.get("bloom")
    sc = bpy.context.scene
    if not b:
        sc.render.use_compositing = False
        return
    tree = bpy.data.node_groups.new(f"{PREFIX}_comp", "CompositorNodeTree")
    tree.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    rl = tree.nodes.new("CompositorNodeRLayers")
    out = tree.nodes.new("NodeGroupOutput")
    g = tree.nodes.new("CompositorNodeGlare")
    g.inputs["Type"].default_value = "Bloom"
    g.inputs["Quality"].default_value = "High"
    g.inputs["Threshold"].default_value = b["threshold"]
    g.inputs["Strength"].default_value = b["strength"]
    g.inputs["Size"].default_value = b["size"]
    tree.links.new(rl.outputs["Image"], g.inputs["Image"])
    tree.links.new(g.outputs["Image"], out.inputs[0])
    sc.compositing_node_group = tree
    sc.render.use_compositing = True


# ----------------------------------------------------------------------------- main entry
def build_scene(bundle, style="white", *, width=1920, height=1080, fps=30, seconds=7.0, samples=48,
                move=None, camera_overrides=None, save_blend=None, clear=True):
    """Build the whole scene. Returns a dict of metadata (legibility, frames, camera)."""
    bundle = Path(bundle)
    meta = json.loads((bundle / "scene.json").read_text())
    st = load_style(style)
    ov = meta.get("style_overrides", {}).get(st["name"], {})
    if ov:  # per-scene tweaks of a style, e.g. [style_overrides.white.light] field_gamma = 0.5
        st = {**st, **{k: v for k, v in ov.items() if not isinstance(v, dict)}}
        for k, v in ov.items():
            if isinstance(v, dict):
                st[k] = {**st.get(k, {}), **v}
    sc = _clear_scene(clear)
    U = _U(meta)
    E = meta["units"]["scene_extent"]
    leg = meta["legibility"]
    geo = np.load(bundle / "geometry.npz")

    # --- images and clock
    L = meta["light"]
    imgs = {}
    nwt = 1
    if L["mode"] == "path":
        imgs["arrival"] = _image_from_array(f"{PREFIX}_arrival", np.load(bundle / "light_arrival.npy"), bundle)
        imgs["routes"] = _image_from_array(f"{PREFIX}_routes", np.load(bundle / "light_routes.npy"), bundle)
    elif L["mode"] == "field":
        imgs["field"] = [_image_from_array(f"{PREFIX}_field{k}", np.load(bundle / fn), bundle) for k, fn in enumerate(L["files"])]
        nwt = len(imgs["field"])
    elif L["mode"] == "intensity":
        imgs["intensity"] = _image_from_array(f"{PREFIX}_intensity", np.load(bundle / "light_intensity.npy"), bundle)
    clock = clock_group(nwt)

    # --- layers
    mats_solid = {}

    def solid(key, opacity=1.0):
        spec = dict(st["materials"].get(key) or st["materials"]["si"])
        if opacity < 1.0:
            spec["alpha"] = spec.get("alpha", 1.0) * opacity
        name = key if opacity >= 1.0 else f"{key}_{opacity:.2f}"
        if name not in mats_solid:
            mats_solid[name] = solid_material(f"{PREFIX}_{name}", spec)
        return mats_solid[name]

    disp_w_scene = leg["display_width_um"] * U.S
    top_core = 0.0
    for lm in meta["layers"]:
        pts, off = geo[f"{lm['key']}_pts"], geo[f"{lm['key']}_off"]
        polys = [np.stack([U.x(pts[a:b, 0]), U.y(pts[a:b, 1])], 1) for a, b in zip(off[:-1], off[1:])]
        z0, z1 = float(U.z(lm["zmin"])), float(U.z(lm["zmax"]))
        th = z1 - z0
        bev = 0.16 * min(th, 0.5 * disp_w_scene) if lm["role"] != "clad" else 0.0
        if lm["role"] == "core" and L["mode"] in ("path", "field") and not (L["mode"] == "field" and L["plane"] != "xy"):
            mat = light_material(f"{PREFIX}_core_{lm['key']}", L["mode"], st["materials"].get(lm["material"], st["materials"]["si"]),
                                 meta, st, U, clock, imgs)
            top_core = max(top_core, z1)
        else:
            mat = solid(lm["material"], lm.get("opacity", 1.0))
            if lm["role"] == "core":
                top_core = max(top_core, z1)
        extrude_polygons(f"{PREFIX}_{lm['name']}", polys, z0, z1, mat, bevel=bev)

    # --- substrate, cladding, ground
    sb = meta["substrate"]
    x0, y0, x1, y1 = (float(U.x(sb["bounds"][0])), float(U.y(sb["bounds"][1])), float(U.x(sb["bounds"][2])), float(U.y(sb["bounds"][3])))
    ztop = float(U.z(sb["ztop_um"]))
    zbot = ztop - sb["thickness_scene"]
    box(f"{PREFIX}_substrate", x0, y0, x1, y1, zbot, ztop - 1e-5, solid(sb["material"] if sb["material"] in st["materials"] else "substrate"),
        bevel=0.15 * sb["thickness_scene"])
    cl = meta.get("cladding")
    if cl and st.get("show_cladding"):
        box(f"{PREFIX}_cladding", x0, y0, x1, y1, float(U.z(cl["zmin"])), float(U.z(cl["zmax"])), solid(cl["material"]))
    ground(st, zbot, 60 * E)

    # --- light planes (glow above the chip, or the field plane)
    if L["mode"] == "path":
        tb = L["texture_bounds_um"]
        zg = top_core + 0.002 * E
        gm = light_material(f"{PREFIX}_glow", "path", None, meta, st, U, clock, imgs, glow=True)
        box(f"{PREFIX}_glow", float(U.x(tb[0])), float(U.y(tb[1])), float(U.x(tb[2])), float(U.y(tb[3])), zg, zg + 1e-5, gm)
    elif L["mode"] in ("field", "intensity"):
        a0, b0, a1, b1 = L.get("plane_bounds_um") or L["extent_um"]
        gm = light_material(f"{PREFIX}_field", L["mode"], None, meta, st, U, clock, imgs, glow=True)
        if L["plane"] == "xy":
            zf = max(top_core, float(U.z(L["offset_um"]))) + 0.002 * E
            box(f"{PREFIX}_field", float(U.x(a0)), float(U.y(b0)), float(U.x(a1)), float(U.y(b1)), zf, zf + 1e-5, gm)
        else:
            yf = float(U.y(L["offset_um"])) - 0.003 * E
            me = bpy.data.meshes.new(f"{PREFIX}_field")
            vs = [(float(U.x(a0)), yf, float(U.z(b0))), (float(U.x(a1)), yf, float(U.z(b0))),
                  (float(U.x(a1)), yf, float(U.z(b1))), (float(U.x(a0)), yf, float(U.z(b1)))]
            me.from_pydata(vs, [], [(0, 1, 2, 3)])
            ob = bpy.data.objects.new(f"{PREFIX}_field", me)
            sc.collection.objects.link(ob)
            me.materials.append(gm)

    for ob in bpy.data.objects:  # light planes glow; they cast no shadows
        if ob.name.startswith((f"{PREFIX}_glow", f"{PREFIX}_field")):
            ob.visible_shadow = False

    # --- frames, clock keys
    n_frames = int(round(seconds * fps))
    sc.frame_start, sc.frame_end = 1, n_frames
    sc.render.fps = fps
    wfn = None
    if L["mode"] == "field" and L.get("sequence"):
        wfn = lambda u: sequence_weights(nwt, u, L.get("crossfade", 0.25))  # noqa: E731
    key_clock(clock, 1, n_frames, wfn)

    # --- camera
    cam_cfg = dict(meta.get("camera", {}))
    cam_cfg.update(camera_overrides or {})
    if move:
        cam_cfg["move"] = move
    # frame the die block (where it lies inside the framed region) from its bottom to the top layer
    fx0, fy0, fx1, fy1 = meta["frame_um"]
    sbx = sb["bounds"]
    if meta.get("frame_explicit", True):
        mx = 0.02 * (fx1 - fx0 + fy1 - fy0)
        bx0, by0 = max(sbx[0], fx0 - mx), max(sbx[1], fy0 - mx)
        bx1, by1 = min(sbx[2], fx1 + mx), min(sbx[3], fy1 + mx)
    else:  # the whole die
        bx0, by0, bx1, by1 = sbx
    ztop_all = max(top_core, max(float(U.z(lm["zmax"])) for lm in meta["layers"]))
    corners = np.array([[float(U.x(x)), float(U.y(y)), z] for x in (bx0, bx1) for y in (by0, by1) for z in (zbot, ztop_all)])
    if L["mode"] in ("field", "intensity") and L.get("plane") in ("xz", "yz"):
        # a vertical field plane rises above the chip: keep all of it in frame
        a0, b0, a1, b1 = L.get("plane_bounds_um") or L["extent_um"]
        yf = float(U.y(L["offset_um"]))
        extra = np.array([[float(U.x(a)), yf, float(U.z(b))] for a in (a0, a1) for b in (b0, b1)])
        corners = np.vstack([corners, extra])
    tgt = np.array(cam_cfg.get("target", [0.0, 0.0, 0.0]), dtype=float)
    if "target_um" in cam_cfg:
        tx, ty = cam_cfg["target_um"]
        tgt = np.array([float(U.x(tx)), float(U.y(ty)), 0.0])
    path = None
    if meta.get("main_path_um"):
        mp = np.asarray(meta["main_path_um"])
        path = np.stack([U.x(mp[:, 0]), U.y(mp[:, 1]), np.full(len(mp), top_core)], 1)
    mv = cam_cfg.get("move", "orbit")
    if mv == "flyalong" and path is None:
        mv = "push"
    ps = cammod.poses(mv, n_frames, points=corners, target=tgt, az=cam_cfg.get("azimuth", -60.0),
                      el=cam_cfg.get("elevation", 35.0), amplitude=cam_cfg.get("amplitude", 14.0),
                      lens_mm=cam_cfg.get("lens_mm", 50.0), aspect=width / height, margin=cam_cfg.get("margin", 0.08),
                      path=path, zoom=cam_cfg.get("zoom", 1.0), drift=cam_cfg.get("drift", 6.0),
                      height=cam_cfg.get("height"))
    cd = bpy.data.cameras.new(f"{PREFIX}_camera")
    cd.sensor_fit = "HORIZONTAL"
    cd.sensor_width = 36.0
    cd.clip_start = 0.001 * E
    cd.clip_end = 400 * E
    co = bpy.data.objects.new(cd.name, cd)
    sc.collection.objects.link(co)
    sc.camera = co
    co.rotation_mode = "QUATERNION"
    for i, (eye, t, up, lens) in enumerate(ps):
        f = 1 + i
        co.location = Vector(eye)
        co.rotation_quaternion = Matrix(cammod.look_rotation(eye, t, up).tolist()).to_quaternion()
        cd.lens = lens
        co.keyframe_insert("location", frame=f)
        co.keyframe_insert("rotation_quaternion", frame=f)
        cd.keyframe_insert("lens", frame=f)
    if st.get("dof_fstop"):
        cd.dof.use_dof = True
        cd.dof.aperture_fstop = st["dof_fstop"]
        cd.dof.focus_distance = float(np.linalg.norm(ps[0][0] - ps[0][1]))

    setup_world(st)
    setup_lights(st, tgt, E)
    setup_compositor(st)

    # --- render settings
    r = sc.render
    r.engine = "BLENDER_EEVEE"
    ee = sc.eevee
    ee.taa_render_samples = samples
    try:
        ee.use_raytracing = st["ground"]["kind"] == "glossy"
        ee.ray_tracing_method = "SCREEN"
        ee.use_fast_gi = True
        ee.fast_gi_method = "AMBIENT_OCCLUSION_ONLY" if st["ground"]["kind"] in ("shadow_white", "flat_shadow") else "GLOBAL_ILLUMINATION"
        ee.use_shadows = True
        ee.shadow_ray_count = 2
        ee.shadow_step_count = 8
    except AttributeError:
        pass
    r.resolution_x, r.resolution_y = width, height
    r.resolution_percentage = 100
    r.film_transparent = False
    r.image_settings.file_format = "PNG"
    r.image_settings.color_mode = "RGB"
    r.image_settings.color_depth = "8"
    vs = sc.view_settings
    vs.view_transform = st.get("view_transform", "AgX")
    try:
        vs.look = st.get("look", "None")
    except TypeError:
        vs.look = "None"
    vs.exposure = st.get("exposure", 0.0)
    sc.display_settings.display_device = "sRGB"

    info = {"legibility": leg, "frames": n_frames, "fps": fps, "move": mv, "style": st["name"],
            "camera": {k: v for k, v in cam_cfg.items()}, "notes": meta.get("notes", [])}
    if save_blend:
        bpy.ops.wm.save_as_mainfile(filepath=str(save_blend))
    return info


def render_frames(out_dir, first, last, prefix="frame_"):
    """Render frames first..last (inclusive) to out_dir/frame_####.png."""
    sc = bpy.context.scene
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    sc.frame_start, sc.frame_end = first, last
    sc.render.filepath = str(out / f"{prefix}####")
    sc.render.use_file_extension = True
    bpy.ops.render.render(animation=True)
