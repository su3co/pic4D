"""Headless entry point:

    blender -b --factory-startup -P pic4d/blender/run.py -- --bundle B --style dark \
        --out OUT --size 1440x1080 --fps 30 --seconds 7 --frames 1-210 [--save-blend f.blend]
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pic4d.blender.build import build_scene, render_frames  # noqa: E402


def main(argv):
    ap = argparse.ArgumentParser(prog="pic4d-blender")
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--style", default="dark")
    ap.add_argument("--out", required=True)
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--seconds", type=float, default=7.0)
    ap.add_argument("--frames", default=None, help="a-b (1-based, inclusive); default all")
    ap.add_argument("--samples", type=int, default=48)
    ap.add_argument("--move", default=None)
    ap.add_argument("--camera", default=None, help="JSON object of camera overrides")
    ap.add_argument("--save-blend", default=None)
    ap.add_argument("--build-only", action="store_true", help="build (and save) the scene, render nothing")
    a = ap.parse_args(argv)
    w, h = (int(v) for v in a.size.lower().split("x"))
    t0 = time.time()
    info = build_scene(a.bundle, a.style, width=w, height=h, fps=a.fps, seconds=a.seconds, samples=a.samples,
                       move=a.move, camera_overrides=json.loads(a.camera) if a.camera else None, save_blend=a.save_blend)
    if a.build_only:
        print("PIC4D_BUILT", json.dumps({"frames": info["frames"], "move": info["move"], "build_s": round(time.time() - t0, 1)}))
        return
    first, last = 1, info["frames"]
    t1 = time.time()
    if a.frames and ("," in a.frames or "-" not in a.frames):  # a list of single frames
        import bpy

        sc = bpy.context.scene
        for f in (int(v) for v in a.frames.split(",")):
            sc.frame_set(f)
            sc.render.filepath = str(Path(a.out) / "frames" / f"frame_{f:04d}.png")
            bpy.ops.render.render(write_still=True)
        first, last = int(a.frames.split(",")[0]), int(a.frames.split(",")[-1])
    else:
        if a.frames:
            first, last = (int(v) for v in a.frames.split("-"))
        render_frames(Path(a.out) / "frames", first, last)
    info.update({"rendered": [first, last], "build_s": round(t1 - t0, 1), "render_s": round(time.time() - t1, 1)})
    out = Path(a.out)
    (out / f"render_{first:04d}_{last:04d}.json").write_text(json.dumps(info, indent=1))
    print("PIC4D_DONE", json.dumps({k: info[k] for k in ("rendered", "build_s", "render_s")}))


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
