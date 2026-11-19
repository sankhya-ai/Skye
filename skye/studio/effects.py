"""Short-form editor effects as reusable Skye presets.

Two kinds of helpers, both plain data for film.html / applyOps:

- clip helpers: return clip dicts (src, time, at, style, track) — the moving
  picture effects (shake, speed ramp, glitch, whip, mirror, pip, color pop).
- page helpers: write self-contained HTML pages like scenes.py does — the
  overlay effects (freeze-pop, caption-pop, kinetic type, film burn, grain +
  vignette, flash, sticker, outline glow).

Everything is CSS keyframes under the film clock, so the same frame time draws
the same picture in the editor, in previews and in the export. No external
assets except a caller-provided still for matte effects (freeze-pop, outline
glow, cutout pop).
"""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")


# ---------------------------------------------------------------- clip styles

def shake(amp: float = 16.0, dur: float = 0.35, name: str = "fx-shake") -> str:
    """CSS: a beat-hit jitter. Add `animation: <name> <dur>s linear both` to a
    clip's style where the hit lands (delay with `animation-delay`)."""
    return (
        f"@keyframes {name} {{"
        f"0% {{ translate: 0 0 }} 20% {{ translate: {-amp}px {amp * .6:.1f}px }}"
        f"40% {{ translate: {amp}px {-amp * .5:.1f}px }} 60% {{ translate: {-amp * .7:.1f}px {-amp * .4:.1f}px }}"
        f"80% {{ translate: {amp * .6:.1f}px {amp * .3:.1f}px }} 100% {{ translate: 0 0 }} }}"
    )


def ramp_clips(src: str, start: float, end: float, speeds: list[float], at: float,
               style: str = "", track: int | None = None) -> list[dict[str, Any]]:
    """A speed ramp as touching clips: the source span [start, end) split evenly,
    each piece at its own constant speed (the film model's speed is per-clip).
    Returns clip dicts; timeline positions follow one another from `at`."""
    if not speeds or end <= start:
        raise ValueError("speeds must be non-empty and end > start")
    span = (end - start) / len(speeds)
    clips = []
    t = at
    for i, s in enumerate(speeds):
        if not 0.1 <= s <= 100:
            raise ValueError("speed outside the film model's 0.1-100")
        c: dict[str, Any] = {"src": src, "time": [round(start + i * span, 3), round(start + (i + 1) * span, 3)],
                             "at": round(t, 3)}
        if s != 1:
            c["speed"] = s
        if style:
            c["style"] = style
        if track is not None:
            c["track"] = track
        clips.append(c)
        t += span / s
    return clips


def glitch_clips(src: str, start: float, end: float, at: float, px: int = 10,
                 base_style: str = "object-fit:cover") -> list[dict[str, Any]]:
    """RGB-split glitch: the clip on three tracks, the copies offset and blended,
    flashing on/off in steps. Returns clip dicts (tracks 0, 1, 2)."""
    jitter = (f"animation: fx-glitch {round(end - start, 3)}s steps(6, end) both")
    out = [{"src": src, "time": [start, end], "at": at, "style": base_style, "track": 0},
           {"src": src, "time": [start, end], "at": at, "track": 1,
            "style": f"{base_style};mix-blend-mode:screen;filter:hue-rotate(90deg) saturate(3);{jitter};--fx-gx:{-px}px"},
           {"src": src, "time": [start, end], "at": at, "track": 2,
            "style": f"{base_style};mix-blend-mode:screen;filter:hue-rotate(-90deg) saturate(3);{jitter};--fx-gx:{px}px"}]
    return out


GLITCH_CSS = (
    "@keyframes fx-glitch {"
    "0% { translate: var(--fx-gx, 0px) 0; opacity: 1 } 15% { translate: 0 0; opacity: 0 }"
    "30% { translate: var(--fx-gx, 0px) 3px; opacity: 1 } 45% { translate: 0 0; opacity: 0 }"
    "60% { translate: var(--fx-gx, 0px) -3px; opacity: 1 } 80% { translate: 0 0; opacity: 0 }"
    "100% { translate: 0 0; opacity: 0 } }"
)


def whip_out(length: float = 0.4, name: str = "fx-whip-out") -> str:
    """CSS for the outgoing clip of a whip pan: fast slide with a motion smear."""
    return (
        f"@keyframes {name} {{ 0% {{ translate: 0 0; filter: blur(0px) }}"
        f" 100% {{ translate: -110vw 0; filter: blur(14px) }} }}"
    )


def whip_in(length: float = 0.4, name: str = "fx-whip-in") -> str:
    """CSS for the incoming clip of a whip pan. `length` documents the beat; the
    caller sets `animation: <name> <length>s ease-in both` on the incoming clip."""
    return (
        f"@keyframes {name} {{ 0% {{ translate: 110vw 0; filter: blur(14px) }}"
        f" 100% {{ translate: 0 0; filter: blur(0px) }} }}"
    )


def swipe_in(direction: str = "left", name: str = "fx-swipe") -> str:
    """CSS for a swipe reveal (clip-path wipe) on the incoming clip."""
    edges = {"left": ("inset(0 100% 0 0)", "inset(0 0 0 0)"),
             "right": ("inset(0 0 0 100%)", "inset(0 0 0 0)"),
             "up": ("inset(100% 0 0 0)", "inset(0 0 0 0)"),
             "down": ("inset(0 0 100% 0)", "inset(0 0 0 0)")}
    if direction not in edges:
        raise ValueError("direction must be left, right, up or down")
    a, b = edges[direction]
    return f"@keyframes {name} {{ 0% {{ clip-path: {a} }} 100% {{ clip-path: {b} }} }}"


def mirror_clips(src: str, start: float, end: float, at: float,
                 base_style: str = "object-fit:cover") -> list[dict[str, Any]]:
    """Mirror split screen: the clip twice, left half normal, right half flipped."""
    return [
        {"src": src, "time": [start, end], "at": at, "track": 0,
         "style": f"{base_style};clip-path: inset(0 50% 0 0)"},
        {"src": src, "time": [start, end], "at": at, "track": 0,
         "style": f"{base_style};clip-path: inset(0 0 0 50%);scale: -1 1"},
    ]


def color_pop_style(gray_style: str = "object-fit:cover") -> str:
    """Style for the background clip of a color-pop: grey under the cutout layer."""
    return f"{gray_style};filter:saturate(0) contrast(1.05)"


# ------------------------------------------------------------------ page maker

_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<style>
html,body{{margin:0;height:100%;overflow:hidden;background:transparent}}
body{{width:100vw;height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;
font-family:{font};text-align:center}}
{css}
</style></head><body>{body}
<script>window.film={{duration:{dur},frame(t){{}}}};</script></body></html>"""


def _write(project_root: str | Path, page_name: str, body: str, css: str, dur: float,
           font: str = "Arial,Helvetica,sans-serif") -> dict[str, Any]:
    if not _SAFE.match(page_name):
        raise ValueError("page_name must be letters, digits and dashes")
    pages = Path(project_root) / "pages"
    pages.mkdir(parents=True, exist_ok=True)
    (pages / f"{page_name}.html").write_text(_PAGE.format(body=body, css=css, dur=dur, font=font), encoding="utf-8")
    return {"src": f"pages/{page_name}.html", "len": dur}


def freeze_pop(project_root: str | Path, page_name: str, image: str, label: str = "",
               dur: float = 1.6, outline: str = "#ffffff") -> dict[str, Any]:
    """A frozen cutout still pops in with a hard outline, then holds. `image` is a
    path from the page (e.g. ../assets/freeze.png) — a transparent PNG works best."""
    eimg, elab = html.escape(image, quote=True), html.escape(label)
    cap = (f'<div style="font-size:64px;font-weight:900;color:{outline};letter-spacing:.04em;'
           f'text-shadow:0 4px 24px rgba(0,0,0,.5);animation:fp-label {dur}s ease-out both">{elab}</div>') if label else ""
    body = f"""<div style="animation:fp {dur}s cubic-bezier(.2,.9,.3,1.4) both;display:flex;flex-direction:column;align-items:center">
 <img src="{eimg}" style="width:86vh;filter:drop-shadow(0 0 0 {outline}) drop-shadow(0 0 18px rgba(0,0,0,.45))">
 {cap}</div>"""
    css = ("@keyframes fp{0%{transform:scale(.55);opacity:0}18%{transform:scale(1.06);opacity:1}"
           "30%{transform:scale(1)}85%{transform:scale(1);opacity:1}100%{transform:scale(1.04);opacity:0}}"
           "@keyframes fp-label{0%,25%{opacity:0;transform:translateY(30px)}45%,85%{opacity:1;transform:none}100%{opacity:0}}")
    return _write(project_root, page_name, body, css, dur)


def caption_pop(project_root: str | Path, page_name: str, words: list[str], dur: float | None = None,
                fg: str = "#ffffff", accent: str = "#ffd23f", bg: str = "transparent",
                per_word: float = 0.28, size: int = 120) -> dict[str, Any]:
    """Bounce-in captions, one word at a time, the current word lit in `accent`.
    `words` may carry '<em>word</em>'-style emphasis by wrapping in *asterisks*."""
    dur = dur or max(0.9, per_word * len(words) + 0.6)
    spans = []
    for i, w in enumerate(words):
        hot = w.startswith("*") and w.endswith("*")
        w2 = html.escape(w.strip("*"))
        color = accent if hot else fg
        spans.append(
            f'<span style="display:inline-block;margin:0 .12em;color:{color};'
            f'animation:cw {dur}s cubic-bezier(.2,.9,.3,1.5) both;animation-delay:{i * per_word:.2f}s">{w2}</span>')
    body = f'<div style="max-width:920px;line-height:1.3;font-weight:900;font-size:{size}px;text-shadow:0 4px 18px rgba(0,0,0,.55)">{"".join(spans)}</div>'
    css = ("@keyframes cw{0%{opacity:0;transform:scale(.4) translateY(40px)}8%{opacity:1;transform:scale(1.12)}"
           "14%{transform:scale(1)}88%{opacity:1}100%{opacity:1;transform:scale(1)}}")
    return _write(project_root, page_name, body, css, dur)


def ktype(project_root: str | Path, page_name: str, lines: list[str], dur: float = 2.4,
          fg: str = "#ffffff", bg: str = "#101010", accent: str = "#e5484d") -> dict[str, Any]:
    """Kinetic typography: lines slam in from alternating sides. Plain background."""
    blocks = []
    for i, ln in enumerate(lines):
        side = "-60vw" if i % 2 == 0 else "60vw"
        color = accent if ln.startswith("*") else fg
        blocks.append(
            f'<div style="font-weight:900;font-size:110px;line-height:1.15;color:{color};'
            f'animation:kt {dur}s cubic-bezier(.2,.8,.25,1) both;animation-delay:{i * .35:.2f}s;--kt-from:{side}">{html.escape(ln.strip("*"))}</div>')
    css = ("@keyframes kt{0%{opacity:0;transform:translateX(var(--kt-from))}12%{opacity:1;transform:none}"
           "88%{opacity:1}100%{opacity:0}}")
    page = _write(project_root, page_name, "".join(blocks), css, dur)
    p = Path(project_root) / "pages" / f"{page_name}.html"
    p.write_text(p.read_text().replace("background:transparent", f"background:{bg}"), encoding="utf-8")
    return page


def film_burn(project_root: str | Path, page_name: str, dur: float = 0.8) -> dict[str, Any]:
    """A light-leak / film-burn flash to cover a cut. Screen-blended over footage."""
    body = '<div style="width:100%;height:100%;mix-blend-mode:screen;animation:fb ' + str(dur) + 's ease-in both"></div>'
    css = ("@keyframes fb{0%{opacity:0;background:radial-gradient(circle at 30% 40%,#ffb347,transparent 60%)}"
           "30%{opacity:.95;background:radial-gradient(circle at 60% 50%,#ff7b54,#e5484d 45%,transparent 75%)}"
           "100%{opacity:0;background:radial-gradient(circle at 70% 60%,#ffb347,transparent 80%)}}")
    return _write(project_root, page_name, body, css, dur)


def grain_vignette(project_root: str | Path, page_name: str, dur: float = 30.0,
                   strength: float = 0.5) -> dict[str, Any]:
    """A full-film overlay: animated grain + vignette. Put it on the top track for
    the whole reel. `strength` 0-1 scales both."""
    s = max(0.0, min(1.0, strength))
    body = ('<svg style="position:absolute;inset:0;width:100%;height:100%;opacity:' + f"{.18 * s:.2f}" + '" '
            'filter="contrast(160%) brightness(120%)"><filter id="n"><feTurbulence type="fractalNoise" baseFrequency=".9" numOctaves="2">'
            f'<animate attributeName="seed" values="1;4;2;5;3;1" dur="{dur}s" repeatCount="indefinite"/></feTurbulence></filter>'
            '<rect width="100%" height="100%" filter="url(#n)"/></svg>'
            '<div style="position:absolute;inset:0;background:radial-gradient(ellipse at center,transparent 55%,rgba(0,0,0,' + f"{.55 * s:.2f}" + ') 100%)"></div>')
    return _write(project_root, page_name, body, "", dur)


def flash(project_root: str | Path, page_name: str, color: str = "#ffffff", dur: float = 0.12) -> dict[str, Any]:
    """A full-frame flash cut between beats."""
    body = f'<div style="width:100%;height:100%;background:{color}"></div>'
    return _write(project_root, page_name, body, "", dur)


def sticker(project_root: str | Path, page_name: str, emoji: str, dur: float = 1.2,
            size: int = 260, rotate: float = -8.0) -> dict[str, Any]:
    """A big emoji/sticker popping in with a bounce and a tilt."""
    body = f'<div style="font-size:{size}px;animation:st {dur}s cubic-bezier(.2,.9,.3,1.6) both;transform:rotate({rotate}deg)">{html.escape(emoji)}</div>'
    css = "@keyframes st{0%{opacity:0;transform:scale(.2) rotate(0deg)}25%{opacity:1;transform:scale(1.15) rotate(var(--r,0deg))}45%{transform:scale(1)}85%{opacity:1}100%{opacity:0;transform:scale(1.05)}}"
    return _write(project_root, page_name, body, css, dur)


def outline_glow(project_root: str | Path, page_name: str, image: str, dur: float = 2.0,
                 color: str = "#7cf7ff", width: int = 14) -> dict[str, Any]:
    """A cutout still with a glowing subject outline. `image` is a transparent PNG
    path from the page (e.g. ../assets/subject.png)."""
    eimg = html.escape(image, quote=True)
    drops = " ".join(f"drop-shadow(0 0 {width}px {color})" for _ in range(2))
    body = f'<img src="{eimg}" style="width:100vw;height:100vh;object-fit:cover;filter:{drops};animation:og {dur}s ease-out both">'
    css = "@keyframes og{0%{opacity:0}15%{opacity:1}85%{opacity:1}100%{opacity:0}}"
    return _write(project_root, page_name, body, css, dur)


EFFECTS = {
    "freeze-pop": freeze_pop,
    "caption-pop": caption_pop,
    "ktype": ktype,
    "film-burn": film_burn,
    "grain-vignette": grain_vignette,
    "flash": flash,
    "sticker": sticker,
    "outline-glow": outline_glow,
}


def render_effect(template: str, project_root: str | Path, page_name: str, **params: Any) -> dict[str, Any]:
    """Write one effect page into <project>/pages/<page_name>.html. Raises KeyError
    for an unknown preset."""
    return EFFECTS[template](project_root, page_name, **params)
