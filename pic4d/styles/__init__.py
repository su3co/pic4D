"""Style presets: small JSON files, not code. ``load_style('black')`` or a path to your own.

A style sets the background, ground, world and lights, the colour transform and bloom,
how every material looks, how the light is drawn (``light``) and the overlay ink."""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

__all__ = ["load_style", "list_styles"]


def list_styles() -> list[str]:
    """Names of the built-in styles."""
    return sorted(p.stem for p in HERE.glob("*.json"))


def load_style(style) -> dict:
    """A style dict from a built-in name, a JSON path, or a dict (returned as is).

    A JSON style may name a built-in parent with "extends": the keys it sets replace the
    parent's (materials and light are merged key by key)."""
    if isinstance(style, dict):
        st = style
    else:
        p = Path(style)
        if not p.suffix:
            p = HERE / f"{style}.json"
        if not p.exists():
            raise FileNotFoundError(f"style {style!r} not found; built-in styles: {list_styles()}")
        st = json.loads(p.read_text())
    if st.get("extends"):
        base = load_style(st["extends"])
        merged = {**base, **{k: v for k, v in st.items() if k not in ("materials", "light", "overlay", "extends")}}
        for k in ("materials", "light", "overlay"):
            merged[k] = {**base.get(k, {}), **st.get(k, {})}
        st = merged
    return st
