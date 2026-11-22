"""Command line: ``pic4d <command>`` (or ``python -m pic4d``).

    pic4d prepare scene.toml bundle/          scene file -> bundle (GDS, paths, field textures)
    pic4d render scene.toml -o out/ --style dark --size 1440x1080 --seconds 7 [--frames 1-60]
    pic4d encode out/frames clip.mp4 --title "Ring sensor" --subtitle "SOI · 1550 nm"
    pic4d showreel a.mp4 b.mp4 ... -o reel.mp4 --title-card
    pic4d preview reel.mp4 reel.gif
    pic4d ports chip.gds --layer 1/0          free ends of the guides (candidate ports)
    pic4d colors 1271 1331 1550               display colours of wavelengths
    pic4d styles                              built-in styles
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _blender(path=None):
    exe = path or os.environ.get("PIC4D_BLENDER") or shutil.which("blender")
    if not exe:
        sys.exit("Blender not found: pass --blender or set PIC4D_BLENDER")
    return exe


def cmd_prepare(a):
    from .bundle import prepare

    m = prepare(a.scene, a.bundle)
    print(json.dumps({"bundle": a.bundle, "legibility": m["legibility"], "light": m["light"]["mode"], "notes": m["notes"]}, indent=1))


def cmd_render(a):
    from .bundle import prepare

    out = Path(a.out)
    bundle = out / "bundle"
    if not (bundle / "scene.json").exists() or a.reprepare:
        prepare(a.scene, bundle)
    cmd = [_blender(a.blender), "-b", "--factory-startup", "-P", str(HERE / "blender" / "run.py"), "--",
           "--bundle", str(bundle), "--style", a.style, "--out", str(out), "--size", a.size, "--fps", str(a.fps),
           "--seconds", str(a.seconds), "--samples", str(a.samples)]
    if a.frames:
        cmd += ["--frames", a.frames]
    if a.move:
        cmd += ["--move", a.move]
    if a.save_blend:
        cmd += ["--save-blend", a.save_blend]
    subprocess.run(cmd, check=True)
    if a.mp4:
        from .overlay import Label
        from .styles import load_style
        from .video import encode

        meta = json.loads((bundle / "scene.json").read_text())
        st = load_style(a.style)
        lab = None
        if a.label:
            ov = st.get("overlay", {})
            lab = Label(meta["name"], meta.get("subtitle", ""), swatches=list(dict.fromkeys(meta["light"].get("colour_hex", []))),
                        ink=ov.get("ink", "#f2f4f8"), muted=ov.get("muted", "#9aa3b2"), font=ov.get("font"))
        print(json.dumps(encode(out / "frames", a.mp4, fps=a.fps, label=lab, max_mb=a.max_mb)))


def cmd_encode(a):
    from .overlay import Label
    from .video import encode

    lab = Label(a.title, a.subtitle or "", a.caption or "", swatches=a.swatch or [], ink=a.ink, muted=a.muted, font=a.font) if a.title else None
    post = None
    if a.ascii:
        from .video import ascii_filter

        post = lambda im: ascii_filter(im, cols=a.ascii, font=a.font)  # noqa: E731
    print(json.dumps(encode(a.frames, a.out, fps=a.fps, crf=a.crf, label=lab, max_mb=a.max_mb, post=post)))


def cmd_showreel(a):
    from .video import showreel, title_card

    title = None
    if a.title_card:
        title = str(Path(a.out).with_suffix(".title.mp4"))
        title_card(title, font=a.font)
    print(json.dumps(showreel(a.clips, a.out, title=title, fade=a.fade)))


def cmd_preview(a):
    from .video import preview

    print(json.dumps(preview(a.video, a.out, width=a.width, fps=a.fps)))


def cmd_ports(a):
    from .layout import read_gds
    from .paths import extract

    lay = read_gds(a.gds, a.cell)
    k = tuple(int(v) for v in a.layer.split("/"))
    from .layout import min_feature_width

    r = extract(lay.merged(k), coupling_gap_um=a.gap, min_width_um=min_feature_width(lay.polygons[k]))
    for x, y in r.endpoints:
        print(f"{x:10.3f} {y:10.3f}")


def cmd_colors(a):
    from . import colors

    for nm in a.nm:
        print(f"{nm:8.1f} nm  {colors.wavelength_to_hex(nm)}  {colors.band_name(nm)}")


def cmd_styles(_a):
    from .styles import list_styles, load_style

    for s in list_styles():
        print(f"{s:10s} {load_style(s).get('description', '')}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pic4d", description="photonic integrated circuits, animated in Blender")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare"); p.add_argument("scene"); p.add_argument("bundle"); p.set_defaults(f=cmd_prepare)
    p = sub.add_parser("render")
    p.add_argument("scene"); p.add_argument("-o", "--out", required=True)
    p.add_argument("--style", default="dark"); p.add_argument("--size", default="1920x1080")
    p.add_argument("--fps", type=int, default=30); p.add_argument("--seconds", type=float, default=7.0)
    p.add_argument("--frames"); p.add_argument("--samples", type=int, default=48); p.add_argument("--move")
    p.add_argument("--blender"); p.add_argument("--save-blend"); p.add_argument("--reprepare", action="store_true")
    p.add_argument("--mp4"); p.add_argument("--label", action="store_true"); p.add_argument("--max-mb", type=float)
    p.set_defaults(f=cmd_render)
    p = sub.add_parser("encode")
    p.add_argument("frames"); p.add_argument("out"); p.add_argument("--fps", type=int, default=30)
    p.add_argument("--crf", type=int, default=20); p.add_argument("--max-mb", type=float)
    p.add_argument("--title"); p.add_argument("--subtitle"); p.add_argument("--caption")
    p.add_argument("--swatch", action="append"); p.add_argument("--ink", default="#f2f4f8")
    p.add_argument("--muted", default="#9aa3b2"); p.add_argument("--font")
    p.add_argument("--ascii", type=int, nargs="?", const=200, default=None, metavar="COLS",
                   help="redraw frames as coloured ASCII characters (COLS columns)")
    p.set_defaults(f=cmd_encode)
    p = sub.add_parser("showreel")
    p.add_argument("clips", nargs="+"); p.add_argument("-o", "--out", required=True)
    p.add_argument("--title-card", action="store_true"); p.add_argument("--fade", type=float, default=0.5)
    p.add_argument("--font"); p.set_defaults(f=cmd_showreel)
    p = sub.add_parser("preview")
    p.add_argument("video"); p.add_argument("out"); p.add_argument("--width", type=int, default=640)
    p.add_argument("--fps", type=int, default=12); p.set_defaults(f=cmd_preview)
    p = sub.add_parser("ports")
    p.add_argument("gds"); p.add_argument("--layer", default="1/0"); p.add_argument("--cell")
    p.add_argument("--gap", type=float, default=0.45); p.set_defaults(f=cmd_ports)
    p = sub.add_parser("colors"); p.add_argument("nm", type=float, nargs="+"); p.set_defaults(f=cmd_colors)
    p = sub.add_parser("styles"); p.set_defaults(f=cmd_styles)
    a = ap.parse_args(argv)
    a.f(a)


if __name__ == "__main__":
    main()
