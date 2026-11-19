"""Storyboard scene presets: ready-made text pages for a film.

Each preset renders one self-contained HTML page (no external assets) sized
to fill the stage, animated with CSS keyframes. Pages run under the film's
clock, so the same frame time always draws the same picture in the editor,
in previews and in the export. `write_scene` puts the page in a project's
pages/ folder and returns the clip fields an insert op needs.

Brand defaults follow the Tura palette; every color is a parameter.
"""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

PAPER = "#fffdf8"
INK = "#172a38"
NAVY = "#10293e"
TERRACOTTA = "#be6d4b"
BLUE = "#4e83b0"

_BASE = """<!doctype html><html><head><meta charset="utf-8">
<style>
html,body{margin:0;height:100%;overflow:hidden}
body{width:100vw;height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;
background:@BG@;color:@FG@;font-family:Georgia,'Times New Roman',serif;text-align:center}
@keyframes rise{0%{opacity:0;transform:translateY(60px)}12%{opacity:1;transform:none}88%{opacity:1}100%{opacity:0}}
@keyframes pop{0%{opacity:0;transform:scale(.6)}10%{opacity:1;transform:scale(1)}92%{opacity:1}100%{opacity:0}}
</style></head><body>@BODY@
<script>window.film={duration:@DUR@,frame(t){}};</script></body></html>"""


def _page(body: str, bg: str, fg: str, duration: float = 4.0) -> str:
    return (_BASE.replace("@BG@", bg).replace("@FG@", fg)
            .replace("@BODY@", body).replace("@DUR@", repr(float(duration))))


def hook(line1: str, line2: str, bg: str = PAPER, fg: str = INK, accent: str = TERRACOTTA) -> str:
    """Two lines in sequence: a setup, then its turn. Default 2.5s."""
    e1, e2 = html.escape(line1), html.escape(line2)
    body = f"""
<div style="font-size:84px;line-height:1.25;max-width:900px">
 <div style="animation:rise 1.3s cubic-bezier(.2,.7,.3,1) both">{e1}</div>
 <div style="animation:rise 2.4s cubic-bezier(.2,.7,.3,1) .9s both;color:{accent};font-style:italic">{e2}</div>
</div>"""
    return _page(body, bg, fg, 2.5)


def meme(top: str, bottom: str, bg: str = NAVY, fg: str = PAPER) -> str:
    """Top/bottom caption card, the meme-editor staple. Default 2s."""
    et, eb = html.escape(top.upper()), html.escape(bottom.upper())
    body = f"""
<div style="font-family:'Arial Black',Arial,sans-serif;font-size:96px;line-height:1.15;max-width:960px;
text-transform:uppercase;letter-spacing:.01em;-webkit-text-stroke:3px {NAVY};text-shadow:0 6px 0 rgba(0,0,0,.25)">
 <div style="animation:pop 1.2s cubic-bezier(.2,.9,.3,1.4) both">{et}</div>
 <div style="animation:pop 2.2s cubic-bezier(.2,.9,.3,1.4) .7s both;margin-top:640px">{eb}</div>
</div>"""
    return _page(body, bg, fg, 2.0)


def caption(kicker: str, main: str, bg: str = PAPER, fg: str = INK, accent: str = TERRACOTTA) -> str:
    """Small kicker over a main line with an accent bar. Default 2.5s."""
    ek, em = html.escape(kicker), html.escape(main)
    body = f"""
<div style="animation:rise 2.5s cubic-bezier(.2,.7,.3,1) both;max-width:900px">
 <div style="font-size:44px;letter-spacing:.35em;text-transform:uppercase;color:{accent}">{ek}</div>
 <div style="width:120px;height:6px;background:{accent};margin:36px auto"></div>
 <div style="font-size:76px;line-height:1.25">{em}</div>
</div>"""
    return _page(body, bg, fg, 2.5)


def endcard(title: str, subtitle: str, url: str, bg: str = NAVY, fg: str = PAPER, dot: str = BLUE) -> str:
    """Mark, name, tagline and address. Default 4s."""
    et, es, eu = html.escape(title), html.escape(subtitle), html.escape(url)
    body = f"""
<div style="animation:rise 4s cubic-bezier(.2,.7,.3,1) both">
 <div style="font-size:220px;line-height:1">t<span style="color:{dot}">.</span></div>
 <div style="font-size:120px;letter-spacing:.08em">{et}</div>
 <div style="font-size:52px;font-style:italic;opacity:.85;margin-top:24px">{es}</div>
 <div style="font-size:44px;letter-spacing:.2em;margin-top:64px;color:{dot}">{eu}</div>
</div>"""
    return _page(body, bg, fg, 4.0)


TEMPLATES = {
    "hook": (hook, 2.5),
    "meme": (meme, 2.0),
    "caption": (caption, 2.5),
    "endcard": (endcard, 4.0),
}

_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")


def render_scene(template: str, **params: Any) -> str:
    """Render one preset page to HTML. Raises KeyError for an unknown preset."""
    fn, _ = TEMPLATES[template]
    return fn(**params)


def write_scene(project_root: str | Path, template: str, page_name: str, **params: Any) -> dict[str, Any]:
    """Write a preset page into <project>/pages/<page_name>.html.

    Returns {"src": "pages/<page_name>.html", "len": <default seconds>} for
    an insert op. page_name is a bare name (letters, digits, dashes).
    """
    if not _SAFE.match(page_name):
        raise ValueError("page_name must be letters, digits and dashes")
    fn, default_len = TEMPLATES[template]
    root = Path(project_root)
    pages = root / "pages"
    pages.mkdir(parents=True, exist_ok=True)
    target = pages / f"{page_name}.html"
    target.write_text(fn(**params), encoding="utf-8")
    return {"src": f"pages/{page_name}.html", "len": default_len}
