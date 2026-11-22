"""Encoding (ffmpeg): PNG frames -> H.264 MP4, posters, showreels, animated previews."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .overlay import Label, _font, _rgb, draw_label

__all__ = ["encode", "poster", "title_card", "showreel", "preview", "probe", "ascii_filter", "snap_background"]

# RGB -> BT.709 limited-range YUV with accurate rounding (flat grounds such as #ffffff survive
# the round trip exactly), tagged so that players decode with the same matrix
VF709 = ["-vf", "scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int,format=yuv420p"]
X264 = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high", "-movflags", "+faststart", "-an",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]


def _ffmpeg():
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found on PATH")
    return exe


def _frames(frames_dir):
    fs = sorted(Path(frames_dir).glob("*.png"))
    if not fs:
        raise FileNotFoundError(f"no PNG frames in {frames_dir}")
    return fs


_ASCII_CACHE: dict = {}


def ascii_filter(img: Image.Image, cols: int = 200, charset: str = " .'`^,:;-~+=*!?%#&$@", boost: float = 1.6,
                 background=None, font=None) -> Image.Image:
    """Redraw a frame as coloured monospace characters (the 'ASCII' look).

    The frame is cut into `cols` columns of character cells; each cell gets the
    character whose ink coverage matches its brightness and the cell's colour, boosted
    by `boost`. Output has the same size as the input."""

    W, H = img.size
    cw = W / cols
    key = (W, H, cols, charset, font)
    if key not in _ASCII_CACHE:
        size = max(6, int(round(cw * 1.65)))
        f = _font(font or "IBM Plex Mono", "regular", size)
        bbox = f.getbbox("M")
        gw, gh = max(1, int(round(cw))), max(1, int(round(cw * 1.9)))
        masks = []
        for ch in charset:
            m = Image.new("L", (gw, gh), 0)
            ImageDraw.Draw(m).text((-bbox[0] + (gw - (bbox[2] - bbox[0])) / 2, (gh - size) / 2 - 1), ch, font=f, fill=255)
            masks.append(np.asarray(m, dtype=np.float32) / 255.0)
        masks = np.stack(masks)
        cover = masks.mean(axis=(1, 2))
        order = np.argsort(cover)
        _ASCII_CACHE[key] = (masks[order], cover[order] / max(cover.max(), 1e-6), gw, gh)
    masks, cover, gw, gh = _ASCII_CACHE[key]
    nr, nc = H // gh, W // gw
    small = np.asarray(img.convert("RGB").resize((nc, nr), Image.BOX), dtype=np.float32) / 255.0
    bg = np.array(_rgb(background), dtype=np.float32) / 255.0 if background else np.median(small.reshape(-1, 3), axis=0)
    w = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    lum = np.clip(small @ w - bg @ w, 0, None)
    lum = np.clip(lum / max(float(np.percentile(lum, 99.7)), 1e-3), 0, 1) ** 0.8
    idx = np.searchsorted(cover, lum).clip(0, len(cover) - 1)
    peak = np.maximum(small.max(axis=-1, keepdims=True), 1e-3)
    col = np.clip(small / peak * np.clip(lum[..., None] * boost + 0.2, 0, 1), 0, 1)
    tiles = masks[idx]  # (nr, nc, gh, gw)
    out = bg + tiles[..., None] * (col[:, :, None, None, :] - bg)
    out = out.transpose(0, 2, 1, 3, 4).reshape(nr * gh, nc * gw, 3)
    canvas = np.empty((H, W, 3), dtype=np.float32)
    canvas[:] = bg
    canvas[: nr * gh, : nc * gw] = out
    return Image.fromarray((canvas * 255).astype(np.uint8))


def snap_background(img: Image.Image, colour: str, tol: int = 3) -> Image.Image:
    """Set pixels within `tol` levels (every channel) of the background colour to exactly
    that colour, so a video ground matches the page it sits on (render noise and faint
    ambient occlusion far from the chip otherwise leave it a level or two off)."""
    a = np.asarray(img.convert("RGB")).copy()
    c = np.array(_rgb(colour), dtype=np.int16)
    m = np.all(np.abs(a.astype(np.int16) - c) <= tol, axis=-1)
    a[m] = c.astype(np.uint8)
    return Image.fromarray(a)


def _pipe(frames, out, fps, crf, label=None, fade_label=0.5, extra=(), post=None, snap=None):
    first = Image.open(frames[0])
    W, H = first.size
    cmd = [_ffmpeg(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(fps), "-i", "-", *VF709, *X264, "-preset", "slow", "-crf", str(crf), "-r", str(fps), *extra, str(out)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    n = len(frames)
    for i, f in enumerate(frames):
        im = Image.open(f).convert("RGB")
        if snap is not None:
            im = snap_background(im, *snap)
        if post is not None:
            im = post(im)
        if label is not None:
            t = i / fps
            a = 1.0 if fade_label <= 0 else min(1.0, t / fade_label, (n - 1 - i) / fps / fade_label + 1e-9)
            im = draw_label(im, label, max(0.0, a))
        p.stdin.write(im.tobytes())
    p.stdin.close()
    if p.wait() != 0:
        raise RuntimeError(f"ffmpeg failed: {' '.join(cmd)}")


def encode(frames_dir, out, *, fps=30, crf=20, label: Label | None = None, max_mb: float | None = None,
           fade_label=0.5, max_crf=34, post=None, snap: tuple | None = None) -> dict:
    """Encode a PNG sequence to H.264 (yuv420p, faststart, no audio), optionally burning in
    a label. With max_mb, the CRF is raised until the file fits. `post` is an optional
    per-frame filter (Image -> Image), e.g. :func:`ascii_filter`. `snap=('#ffffff', 3)`
    makes the ground exactly the page colour (see :func:`snap_background`)."""
    frames = _frames(frames_dir)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    while True:
        _pipe(frames, out, fps, crf, label, fade_label, post=post, snap=snap)
        mb = out.stat().st_size / 1e6
        if max_mb is None or mb <= max_mb or crf >= max_crf:
            break
        crf += 2
    return {"path": str(out), "mb": round(mb, 2), "crf": crf, "frames": len(frames), "seconds": len(frames) / fps}


def poster(frames_dir, out, *, index=None, quality=90, label: Label | None = None, snap: tuple | None = None):
    """JPEG poster from one frame (middle by default)."""
    frames = _frames(frames_dir)
    f = frames[len(frames) // 2 if index is None else index]
    im = Image.open(f).convert("RGB")
    if snap:
        im = snap_background(im, *snap)
    if label:
        im = draw_label(im, label)
    im.save(out, quality=quality, optimize=True, progressive=True, subsampling=0)
    return str(out)


def title_card(out, *, size=(1920, 1080), seconds=3.0, fps=30, title="pic4D",
               subtitle="photonic integrated circuits, animated in Blender", background="#0c0e12",
               ink="#edf1f0", muted="#8b949e", colours=("#3f00ff", "#0097ff", "#00ffe7", "#ff8900"), font=None, crf=18):
    """A short animated title card: the name, a subtitle and a pulse of light running
    along a line in a few wavelength colours."""
    W, H = size
    n = int(seconds * fps)
    ft = _font(font, "semibold", int(0.12 * H))
    fs = _font(font, "regular", int(0.028 * H))
    bg = _rgb(background)
    cmd = [_ffmpeg(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps),
           "-i", "-", *VF709, *X264, "-preset", "slow", "-crf", str(crf), str(out)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    y_line = int(0.60 * H)
    x0, x1 = int(0.30 * W), int(0.70 * W)
    xs = np.arange(x0, x1)
    for i in range(n):
        t = i / fps
        a = min(1.0, t / 0.6, (seconds - t) / 0.6)
        im = Image.new("RGB", (W, H), bg)
        d = ImageDraw.Draw(im)
        tw = d.textlength(title, font=ft)
        mix = lambda c: tuple(int(bg[k] + (c[k] - bg[k]) * max(a, 0)) for k in range(3))  # noqa: E731
        d.text(((W - tw) / 2, int(0.36 * H)), title, font=ft, fill=mix(_rgb(ink)))
        sw = d.textlength(subtitle, font=fs)
        d.text(((W - sw) / 2, int(0.64 * H)), subtitle, font=fs, fill=mix(_rgb(muted)))
        d.line([(x0, y_line), (x1, y_line)], fill=mix((40, 48, 58)), width=2)
        glow = Image.new("RGB", (W, H), (0, 0, 0))
        g = ImageDraw.Draw(glow)
        for k, c in enumerate(colours):
            head = x0 + ((t / seconds * 1.2 + k / len(colours) * 0.5) % 1.0) * (x1 - x0)
            tail = np.clip(1 - (head - xs) / (0.18 * (x1 - x0)), 0, 1) * (xs <= head)
            col = np.array(_rgb(c))
            for x, v in zip(xs[::2], tail[::2]):
                if v > 0.02:
                    cc = tuple(int(q * v * a) for q in col)
                    g.line([(x, y_line - 1), (x + 2, y_line - 1)], fill=cc, width=3)
        from PIL import ImageChops, ImageFilter

        im = ImageChops.add(im, glow.filter(ImageFilter.GaussianBlur(6)))
        im = ImageChops.add(im, glow)
        p.stdin.write(im.tobytes())
    p.stdin.close()
    p.wait()
    return str(out)


def probe(path) -> dict:
    """Duration, size and stream info of a video (ffprobe)."""
    exe = shutil.which("ffprobe")
    r = subprocess.run([exe, "-v", "error", "-show_entries", "format=duration,size:stream=width,height,codec_name,pix_fmt,r_frame_rate",
                        "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def showreel(clips, out, *, title: str | None = None, fade=0.5, crf=21, fps=30, size=(1920, 1080)) -> dict:
    """Concatenate clips (labels already burnt in) with cross-fades; an optional title
    card first. All clips must share size and frame rate."""
    out = Path(out)
    clips = [str(c) for c in clips]
    if title:
        clips = [title] + clips
    durs = [float(probe(c)["format"]["duration"]) for c in clips]
    cmd = [_ffmpeg(), "-y", "-loglevel", "error"]
    for c in clips:
        cmd += ["-i", c]
    parts = [f"[{i}:v]fps={fps},scale={size[0]}:{size[1]},format=yuv420p,setsar=1[v{i}]" for i in range(len(clips))]
    last, t = "v0", durs[0]
    for i in range(1, len(clips)):
        off = t - fade
        parts.append(f"[{last}][v{i}]xfade=transition=fade:duration={fade}:offset={off:.3f}[x{i}]")
        last, t = f"x{i}", off + durs[i]
    cmd += ["-filter_complex", ";".join(parts), "-map", f"[{last}]", *X264, "-preset", "slow", "-crf", str(crf), str(out)]
    subprocess.run(cmd, check=True)
    return {"path": str(out), "seconds": round(t, 2), "mb": round(out.stat().st_size / 1e6, 2)}


def preview(video, out, *, width=640, fps=12, start=0.0, seconds=None, colors=128) -> dict:
    """Small animated preview (GIF or WebP by extension) for places without video."""
    out = Path(out)
    trim = ["-ss", str(start)] + (["-t", str(seconds)] if seconds else [])
    vf = f"fps={fps},scale={width}:-2:flags=lanczos"
    if out.suffix.lower() == ".webp":
        cmd = [_ffmpeg(), "-y", "-loglevel", "error", *trim, "-i", str(video), "-vf", vf, "-c:v", "libwebp_anim",
               "-lossless", "0", "-q:v", "60", "-loop", "0", "-an", str(out)]
    else:
        vf += f",split[a][b];[a]palettegen=max_colors={colors}:stats_mode=diff[p];[b][p]paletteuse=dither=sierra2_4a:diff_mode=rectangle"
        cmd = [_ffmpeg(), "-y", "-loglevel", "error", *trim, "-i", str(video), "-vf", vf, "-loop", "0", str(out)]
    subprocess.run(cmd, check=True)
    return {"path": str(out), "mb": round(out.stat().st_size / 1e6, 2)}
