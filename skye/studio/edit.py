"""Per-shot effect editing: one command puts any preset on a shot of a film.

A "shot" is the Nth video clip of the footage track (the last <section> of
film.html that holds <video> clips), counted from 1 in document order. Each
effect resolves to pages written into pages/ plus clip elements inserted into
film.html (or style edits on the shot itself), so the change is plain film
data: the editor, previews and the export all read it the same way.

Timing model (film-doc): a clip's animation clock starts at its `at`, so CSS
animation delays are shot-relative. Keyframes may only use translate, scale
and rotate. Overlay pages are transparent iframes on a track above the video.

    from skye.studio import edit
    edit.apply_effect(project_root, shot=3, effect="caption-pop",
                      text="code.|games.|rockets.")
"""
from __future__ import annotations

import html
import json
import math
import tempfile
import uuid
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import effects, scenes


class EditError(Exception):
    pass


@dataclass
class Shot:
    index: int          # 1-based, document order within the footage track
    clip_id: str
    src: str            # file part of src (before #t=)
    time: tuple[float, float]
    at: float
    speed: float
    dur: float          # timeline seconds: (end - start) / speed
    style: str


_CLIP = re.compile(r'<(video|iframe|img|audio)\b[^>]*?>', re.S)
_ATTR = re.compile(r'([\w-]+)="([^"]*)"')
_TIME = re.compile(r'#t=([\d.]+)(?:,([\d.]+))?')


def _attrs(el: str) -> dict[str, str]:
    return dict(_ATTR.findall(el))


def _clip_window(el: str) -> tuple[float, float] | None:
    """(at, timeline duration) of one clip element, or None when undetermined."""
    a = _attrs(el)
    at = float(a.get('at', '0') or 0)
    m = _TIME.search(a.get('src', ''))
    if not m:
        return None
    start = float(m.group(1))
    end = float(m.group(2)) if m.group(2) else start
    speed = float(a.get('speed', '1') or 1)
    return at, (end - start) / speed


def read_shots(project_root: str | Path, track: int | None = None) -> list[Shot]:
    """Shots of the footage track: the last <section> that has <video> clips."""
    film = Path(project_root) / 'film.html'
    if not film.exists():
        raise EditError(f'{film}: no film.html here')
    text = film.read_text(encoding='utf-8')
    sections = [m.group(0) for m in re.finditer(r'<section\b[^>]*>.*?</section>', text, re.S)]
    candidates = [(i, sec) for i, sec in enumerate(sections, 1)
                  if '<video' in sec and 'data-skye-effect' not in sec]
    if track is not None:
        candidates = [(i, sec) for i, sec in candidates if i == track]
    if not candidates:
        raise EditError('no footage track found; --track uses section numbers from 1')
    if len(candidates) != 1:
        raise EditError('multiple video tracks; select one with --track (section number from 1)')
    footage = candidates[0][1]
    shots = []
    for m_el in re.finditer(r'<video\b[^>]*?>', footage, re.S):
        el = m_el.group(0)
        a = _attrs(el)
        if 'id' not in a or 'at' not in a:
            raise EditError('shot commands require explicit id and at attributes on footage clips')
        win = _clip_window(el)
        if win is None:
            raise EditError(f'clip {a.get("id", "?")}: src needs a #t=start,end range')
        at, dur = win
        m = _TIME.search(a['src'])
        shots.append(Shot(index=len(shots) + 1, clip_id=a.get('id', f'v{len(shots)+1}'),
                          src=a['src'].split('#')[0], time=(float(m.group(1)), float(m.group(2))),
                          at=at, speed=float(a.get('speed', '1') or 1), dur=dur,
                          style=a.get('style', '')))
    return shots


# ---------------------------------------------------------------- film surgery

def _inject_keyframes(text: str, keyframes: str) -> str:
    for name in re.findall(r'@keyframes ([\w-]+)', keyframes):
        if f'@keyframes {name}' in text:
            return text
    if '</style>' not in text:
        raise EditError('film.html has no <style> block for keyframes')
    return text.replace('</style>', keyframes + '\n</style>', 1)


def _sections_spans(text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group(1)) for m in re.finditer(r'<section\b[^>]*>(.*?)</section>', text, re.S)]


def _overlay_section(text: str, at: float, dur: float) -> tuple[str, bool]:
    """Insert point for an overlay clip of [at, at+dur): the first top section
    with room, else a marker to open a new topmost section."""
    for start, end, body in _sections_spans(text):
        if '<video' in body or '<audio' in body:
            break
        windows = [w for w in (_clip_window(m.group(0)) for m in _CLIP.finditer(body)) if w]
        if all(at + dur <= w_at or at >= w_at + w_dur for w_at, w_dur in windows):
            return body, False
    return '', True


def _insert_overlay(text: str, clip_html: str, at: float, dur: float) -> str:
    body, needs_new = _overlay_section(text, at, dur)
    if needs_new:
        return re.sub(r'(<body[^>]*>)', r'\1\n<section data-skye-effect="overlay">\n  ' + clip_html + '\n</section>', text, count=1)
    for start, end, b in _sections_spans(text):
        if b is body or b == body:
            return text[:end - len('</section>')] + '  ' + clip_html + '\n' + text[end - len('</section>'):]
    raise EditError('could not place the overlay clip')


def _edit_shot_clip(text: str, shot: Shot, transform) -> str:
    """Rewrite the shot's own <video> element (transform(old_tag) -> new_tag)."""
    pattern = re.compile(r'<video\b[^>]*id="' + re.escape(shot.clip_id) + r'"[^>]*>(?:\s*</video>)?')
    m = pattern.search(text)
    if not m:
        raise EditError(f'shot clip id={shot.clip_id} not found in film.html')
    return text[:m.start()] + transform(m.group(0)) + text[m.end():]


def _set_style(tag: str, style: str) -> str:
    closing = '</video>' if tag.endswith('</video>') else ''
    if closing:
        tag = tag[:-len(closing)]
    if 'style="' in tag:
        return re.sub(r'style="[^"]*"', lambda _: f'style="{style}"', tag, count=1) + closing
    return tag[:-1].rstrip('/') + f' style="{style}">' + closing


def _write_film(project_root: str | Path, text: str) -> None:
    check = subprocess.run(['node', str(Path(__file__).with_name('effect-check.mjs'))],
                           input=text, text=True, capture_output=True, timeout=15)
    if check.returncode:
        raise EditError('film validation failed, original preserved: ' + check.stderr[-2000:])
    target = Path(project_root) / 'film.html'
    target.with_suffix('.html.bak').write_bytes(target.read_bytes())
    tmp = target.with_suffix('.html.tmp')
    tmp.write_text(text, encoding='utf-8')
    tmp.replace(target)


# ------------------------------------------------------------------- effects

def _page_clip(page: dict[str, Any], at: float, dur: float | None = None) -> tuple[str, float, float]:
    d = dur if dur is not None else page['len']
    return f'<iframe src="{page["src"]}#t=0,{round(d, 3)}" at="{round(at, 3)}"></iframe>', at, d


def _words(text: str) -> list[str]:
    return [w.strip() for w in text.split('|') if w.strip()]


def apply_effect(project_root: str | Path, shot: int, effect: str, *,
                 text: str = '', at: float | None = None, dur: float | None = None,
                 image: str = '', label: str = '', speeds: str = '',
                 zoom: float = 1.15, amp: float = 7.0, offset: float = 0.2,
                 shape: str = 'star', strength: float = 0.45,
                 fg: str = '#ffffff', accent: str = '#ffd23f', track: int | None = None) -> dict[str, Any]:
    """Put one effect on one shot. Returns a record of what changed."""
    root = Path(project_root)
    film_path = root / 'film.html'
    body = film_path.read_text(encoding='utf-8')
    checked = subprocess.run(['node', str(Path(__file__).with_name('effect-check.mjs'))],
                             input=body, text=True, capture_output=True, timeout=15)
    if checked.returncode:
        raise EditError('invalid input film: ' + checked.stderr[-2000:])
    shots = read_shots(root, track)
    if not 1 <= shot <= len(shots):
        raise EditError(f'shot {shot}: the film has {len(shots)} shots')
    s = shots[shot - 1]
    t0 = s.at + offset if at is None else at
    values = [t0, offset, zoom, amp, strength] + ([dur] if dur is not None else [])
    if not all(math.isfinite(v) for v in values) or (dur is not None and dur <= 0):
        raise EditError('effect timing and numeric parameters must be finite; duration must be positive')
    if not s.at <= t0 < s.at + s.dur:
        raise EditError('effect starts outside the selected shot')
    if dur is not None and t0 + dur > s.at + s.dur + 1e-6:
        raise EditError('effect extends past the selected shot')
    if effect in ('shake', 'punch', 'whip-in', 'whip-out', 'mirror', 'ramp', 'text-behind') and 'animation' in s.style:
        raise EditError('shot already animated; refusing to replace existing motion or reset it across split clips')
    if effect in ('ramp', 'text-behind'):
        tag = next((m.group(0) for m in re.finditer(r'<video\b[^>]*>', body)
                    if _attrs(m.group(0)).get('id') == s.clip_id), '')
        extra = set(_attrs(tag)) - {'id', 'src', 'at', 'speed', 'style'}
        if extra:
            raise EditError('baking/splitting clips with extra fields is not yet supported: ' + ', '.join(sorted(extra)))
    change: dict[str, Any] = {'shot': shot, 'effect': effect, 'at': round(t0, 3)}

    nonce = uuid.uuid4().hex[:8]
    def overlay(page, p_at=None, p_dur=None):
        p_dur = min(page['len'] if p_dur is None else p_dur, s.at + s.dur - (t0 if p_at is None else p_at))
        clip, c_at, c_dur = _page_clip(page, t0 if p_at is None else p_at, p_dur)
        return _insert_overlay(body, clip, c_at, c_dur), c_dur

    if effect in ('caption-pop', 'ktype', 'caption', 'meme', 'hook'):
        words = _words(text)
        if not words:
            raise EditError(f'{effect} needs --text "one|two|three"')
        if effect == 'caption-pop':
            page = effects.caption_pop(root, f'fx-cap-{shot}-{nonce}', words, fg=fg, accent=accent)
        elif effect == 'ktype':
            page = effects.ktype(root, f'fx-kt-{shot}-{nonce}', words, dur=dur or 2.4, fg=fg, accent=accent, overlay=True)
        elif effect == 'caption':
            page = scenes.write_scene(root, 'caption', f'fx-cap-{shot}-{nonce}', kicker=words[0],
                                      main=' '.join(words[1:]) or words[0])
        elif effect == 'meme':
            page = scenes.write_scene(root, 'meme', f'fx-meme-{shot}-{nonce}', top=words[0],
                                      bottom=' '.join(words[1:]) or words[0])
        else:
            page = scenes.write_scene(root, 'hook', f'fx-hook-{shot}-{nonce}', line1=words[0],
                                      line2=' '.join(words[1:]) or words[0])
        body, used = overlay(page, p_dur=dur)
        change['dur'] = used

    elif effect == 'flash':
        body, used = overlay(effects.flash(root, f'fx-flash-{shot}-{nonce}', fg, dur or 0.12), p_at=t0)
        change['dur'] = used
    elif effect == 'film-burn':
        body, used = overlay(effects.film_burn(root, f'fx-burn-{shot}-{nonce}', dur or 0.4))
        change['dur'] = used
    elif effect == 'grain':
        body, used = overlay(effects.grain_vignette(root, f'fx-grain-{shot}-{nonce}', dur or s.dur, strength=strength))
        change['dur'] = used
    elif effect == 'sticker':
        page = effects.sticker(root, f'fx-sticker-{shot}-{nonce}', shape, dur=dur or 1.2)
        body, used = overlay(page)
        change['dur'] = used
    elif effect == 'freeze-pop':
        if not image:
            raise EditError('freeze-pop needs --image pages/../assets/<cutout>.png')
        page = effects.freeze_pop(root, f'fx-freeze-{shot}-{nonce}', image, label=label, dur=dur or 2.0)
        body, used = overlay(page)
        change['dur'] = used

    elif effect == 'shake':
        name = f'fx-shake-{shot}'
        body = _inject_keyframes(body, effects.shake(amp=amp, dur=dur or 0.35, name=name))
        body = _edit_shot_clip(body, s, lambda tag: _set_style(
            tag, f'{s.style};animation:{name} {dur or 0.35}s linear both;animation-delay:{round(t0-s.at,3)}s'))
        change['dur'] = dur or 0.35
    elif effect == 'punch':
        name = f'fx-punch-{shot}'
        body = _inject_keyframes(body, f'@keyframes {name} {{ 0% {{ scale: 1 }} 100% {{ scale: {zoom} }} }}')
        body = _edit_shot_clip(body, s, lambda tag: _set_style(
            tag, f'{s.style};animation:{name} {s.dur}s ease-out both'))
        change['dur'] = s.dur
    elif effect in ('whip-out', 'whip-in'):
        w_dur = dur or 0.4
        name = f'fx-{effect}-{shot}'
        kf = effects.whip_out(w_dur, name) if effect == 'whip-out' else effects.whip_in(w_dur, name)
        body = _inject_keyframes(body, kf)
        delay = f'animation-delay:{round(s.dur - w_dur, 3)}s;' if effect == 'whip-out' else ''
        ease = 'ease-in' if effect == 'whip-out' else 'ease-out'
        body = _edit_shot_clip(body, s, lambda tag: _set_style(
            tag, f'{s.style};animation:{name} {w_dur}s {ease} both;{delay}'))
        change['dur'] = w_dur
    elif effect == 'glitch':
        g_dur = min(dur or 0.7, s.at + s.dur - t0)
        g_at = t0
        src_start = s.time[0] + (g_at - s.at) * s.speed
        clips = effects.glitch_clips(s.src, src_start, src_start + g_dur * s.speed, at=g_at)
        body = _inject_keyframes(body, effects.GLITCH_CSS)
        for d in clips[1:]:
            a, b = d['time']
            clip = f'<video src="{s.src}#t={round(a,3)},{round(b,3)}" at="{d["at"]}" speed="{s.speed}" volume="0" style="{d["style"]}"></video>'
            body = _insert_overlay(body, clip, d['at'], g_dur)
        change['dur'] = g_dur
    elif effect == 'mirror':
        a, b = s.time
        right = (f'<video src="{s.src}#t={a},{b}" at="{s.at}" speed="{s.speed:g}" '
                 f'volume="0" style="{s.style};clip-path:inset(0 0 0 50%);animation:fx-mirror-flip-{shot} {s.dur}s both"></video>')
        body = _inject_keyframes(body, f'@keyframes fx-mirror-flip-{shot} {{ 0% {{ scale: -1 1 }} 100% {{ scale: -1 1 }} }}')
        body = _edit_shot_clip(body, s, lambda tag: _set_style(tag, f'{s.style};clip-path:inset(0 50% 0 0)'))
        body = _insert_overlay(body, right, s.at, s.dur)
        change['dur'] = s.dur
    elif effect == 'ramp':
        rates = [float(x) for x in speeds.split(',') if x.strip()] if speeds else [0.75, 1.5]
        if s.speed != 1:
            raise EditError('ramp: shot already has a speed; ramp the source clip instead')
        if not rates or any(not math.isfinite(r) or not 1/16 <= r <= 16 for r in rates):
            raise EditError('ramp speeds must be in [1/16, 16]')
        a, b = s.time
        span = (b - a) / len(rates)
        total = sum(span / r for r in rates)
        if abs(total-s.dur) > 0.001:
            raise EditError('ramp must preserve shot duration; adjust speeds or use the timeline ripple editor')
        tags = []
        cursor = s.at
        for i, r in enumerate(rates):
            d = span / r
            tags.append(f'<video id="{s.clip_id}-ramp-{i+1}" src="{s.src}#t={round(a + i*span,3)},{round(a + (i+1)*span,3)}" '
                        f'at="{round(cursor,3)}" speed="{r:g}" style="{s.style}"></video>')
            cursor += d
        body = _edit_shot_clip(body, s, lambda tag: '\n  '.join(tags))
        change['dur'] = cursor - s.at
    elif effect == 'text-behind':
        if not text:
            raise EditError('text-behind needs --text "LINE ONE|LINE TWO"')
        out = text_behind(root, s, _words(text), t0, dur or min(2.0, s.at + s.dur - t0))
        a, b = s.time
        start, end = out['range']
        pieces = []
        if start > a + 1e-3:
            pieces.append(f'<video id="{s.clip_id}-pre" src="{s.src}#t={a},{round(start,3)}" at="{s.at}" style="{s.style}"></video>')
        pieces.append(f'<video id="{s.clip_id}" src="{out["baked"]}#t=0,{round(end-start,3)}" at="{round(t0,3)}" style="{s.style}"></video>')
        if b > end + 1e-3:
            pieces.append(f'<video id="{s.clip_id}-post" src="{s.src}#t={round(end,3)},{b}" at="{round(t0 + (end-start),3)}" style="{s.style}"></video>')
        body = _edit_shot_clip(body, s, lambda tag: '\n  '.join(pieces))
        change.update(out)
        _write_film(root, body)
        return change
    else:
        raise EditError(f'unknown effect {effect!r}; see docs/video/EFFECTS-CLI.md')

    _write_film(root, body)
    return change


def text_behind(root: Path, shot: Shot, lines: list[str], at: float, dur: float) -> dict[str, Any]:
    """Bake text-behind-people into the shot's source range: per-frame u2netp
    masks (temporally smoothed, feathered), text composited under the people,
    written to assets/text-behind-N.mp4, and the shot clip pointed at it.
    Runs fully local (rembg + ffmpeg); ~0.75s of compute per frame here."""
    if shot.style.strip().strip(';') not in ('', 'object-fit:cover'):
        raise EditError('text-behind currently requires a plain object-fit:cover shot')
    if shot.speed != 1:
        raise EditError('text-behind: bake it on an un-sped shot')
    start = shot.time[0] + (at - shot.at)
    work = root / '.skye-effects' / f'text-behind-{shot.index}-{uuid.uuid4().hex[:8]}'
    work.mkdir(parents=True, exist_ok=True)
    src = (root / shot.src).resolve()
    if not src.is_file():
        raise EditError('text-behind needs a local source file')
    stage = re.search(r'width=(\d+),?\s*height=(\d+)', (root/'film.html').read_text())
    if not stage:
        raise EditError('text-behind needs an explicit width/height viewport')
    W, H = map(int, stage.groups())
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-ss', f'{start:.3f}',
                    '-i', str(src), '-t', f'{dur:.3f}', '-vf', f'fps=30,scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}',
                    str(work / 'f%04d.png'), '-y'], check=True)
    try:
        from rembg import remove, new_session
        import numpy as np
        from PIL import Image, ImageDraw, ImageFilter, ImageFont
    except ImportError as e:
        raise EditError(f'text-behind needs rembg, numpy and Pillow: {e}')
    try:
        font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 92)
    except Exception:
        font = ImageFont.load_default()
    txt = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(txt)
    y0 = int(H * 0.60)
    for i, line in enumerate(lines):
        bb = d.textbbox((0, 0), line, font=font, stroke_width=5)
        d.text(((W - (bb[2] - bb[0])) / 2, y0 + i * 130), line, font=font,
               fill=(250, 248, 242, 235), stroke_width=5, stroke_fill=(10, 10, 12, 235))
    txt = txt.filter(ImageFilter.GaussianBlur(0.6))
    session = new_session('u2netp')
    prev = None
    frames = sorted(work.glob('f*.png'))
    for fp in frames:
        im = Image.open(fp).convert('RGB')
        m = np.asarray(remove(im, session=session, only_mask=True, post_process_mask=True),
                       dtype=np.float32) / 255.0
        if prev is not None:
            m = 0.65 * m + 0.35 * prev
        prev = m
        mf = Image.fromarray((m * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(2.2))
        base = im.convert('RGBA')
        base.alpha_composite(txt)
        person = im.convert('RGBA')
        person.putalpha(mf)
        base.alpha_composite(person)
        base.convert('RGB').save(work / ('o' + fp.name[1:]))
    if not frames:
        raise EditError('text-behind extracted no frames')
    (root/'assets').mkdir(exist_ok=True)
    out_rel = f'assets/text-behind-{shot.index}-{uuid.uuid4().hex[:8]}.mp4'
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-framerate', '30',
                    '-i', str(work / 'o%04d.png'), '-ss', f'{start:.3f}', '-i', str(src),
                    '-t', f'{dur:.3f}', '-map', '0:v', '-map', '1:a?', '-c:v', 'libx264',
                    '-crf', '16', '-preset', 'medium', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', '-shortest', str(root / out_rel), '-y'], check=True)
    return {'baked': out_rel, 'range': [round(start, 3), round(start + dur, 3)],
            'note': 'baked window inserted; original source retained'}


def apply_recipe(project_root: str | Path, shot: int, recipe: list[dict], *, track: int | None = None) -> list[dict]:
    """Apply a human/agent-described style recipe. Restore the film if a step fails.

    This does not analyze reference pixels or accept executable recipe content.
    A recipe may contain one clip-motion effect plus any number of overlays.
    Split/bake operations must be last because they change the shot list.
    """
    import inspect
    if not isinstance(recipe, list) or not 1 <= len(recipe) <= 30:
        raise EditError('recipe must contain 1 to 30 effect objects')
    allowed = set(inspect.signature(apply_effect).parameters) - {'project_root', 'shot', 'track'}
    for i, step in enumerate(recipe):
        if not isinstance(step, dict) or 'effect' not in step or set(step) - allowed:
            raise EditError('recipe steps need effect and supported parameters only')
        if step['effect'] in ('text-behind', 'ramp') and i != len(recipe)-1:
            raise EditError('split/bake effect must be the last recipe step')
    root = Path(project_root)
    film = root/'film.html'
    before = film.read_bytes()
    backup = root/'film.html.bak'
    old_backup = backup.read_bytes() if backup.exists() else None
    known = {p for folder in ('pages', 'assets') for p in (root/folder).glob('*') if p.is_file()}
    try:
        changes = [apply_effect(root, shot, track=track, **step) for step in recipe]
        backup.write_bytes(before)
        return changes
    except Exception:
        film.write_bytes(before)
        if old_backup is None:
            backup.unlink(missing_ok=True)
        else:
            backup.write_bytes(old_backup)
        for folder in ('pages', 'assets'):
            for p in (root/folder).glob('*'):
                if p.is_file() and p not in known:
                    p.unlink()
        raise


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog='python -m skye.studio.edit',
                                description='Put one effect on one shot of a film (docs/video/EFFECTS-CLI.md).')
    p.add_argument('project')
    p.add_argument('--shot', type=int, default=None, help='shot number, from 1, on the footage track')
    p.add_argument('--effect', default=None,
                   help='caption-pop, ktype, caption, meme, hook, flash, film-burn, grain, sticker, '
                        'freeze-pop, shake, punch, whip-in, whip-out, glitch, mirror, ramp, text-behind')
    p.add_argument('--recipe', help='JSON style recipe file; no reference analysis is performed')
    p.add_argument('--text', default='', help='words/lines separated by |')
    p.add_argument('--at', type=float, default=None, help='timeline second; default 0.2s into the shot')
    p.add_argument('--dur', type=float, default=None)
    p.add_argument('--offset', type=float, default=0.2, help='default at = shot.at + offset')
    p.add_argument('--image', default='', help='freeze-pop cutout image (path from the page)')
    p.add_argument('--label', default='')
    p.add_argument('--speeds', default='', help='ramp: comma list, e.g. 0.5,1,2')
    p.add_argument('--zoom', type=float, default=1.15)
    p.add_argument('--amp', type=float, default=7.0)
    p.add_argument('--shape', default='star', help='sticker: star, burst or heart')
    p.add_argument('--strength', type=float, default=0.45)
    p.add_argument('--fg', default='#ffffff')
    p.add_argument('--accent', default='#ffd23f')
    p.add_argument('--track', type=int, help='footage section number from 1, required with multiple video tracks')
    p.add_argument('--list', action='store_true', help='list shots and exit')
    args = p.parse_args(argv)
    try:
        if args.list:
            for s in read_shots(args.project, args.track):
                print(f'shot {s.index}: at {s.at:.2f}s, {s.dur:.2f}s long, src {s.src}#t={s.time[0]},{s.time[1]}')
            return 0
        if args.shot is None or (args.effect is None and args.recipe is None):
            p.error('--shot and --effect are required (or use --list)')
        if args.recipe:
            result = apply_recipe(args.project, args.shot, json.loads(Path(args.recipe).read_text()), track=args.track)
            print(json.dumps(result))
            return 0
        change = apply_effect(args.project, args.shot, args.effect, text=args.text, at=args.at,
                              dur=args.dur, offset=args.offset, image=args.image, label=args.label,
                              speeds=args.speeds, zoom=args.zoom, amp=args.amp, shape=args.shape,
                              strength=args.strength, fg=args.fg, accent=args.accent, track=args.track)
        print('applied:', change)
        return 0
    except EditError as e:
        p.error(str(e))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
