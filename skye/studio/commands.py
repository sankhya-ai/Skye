"""Deterministic human commands and trusted effect-to-timeline proposals. No models."""
from __future__ import annotations
import json
from pathlib import Path
import re
import subprocess
import tempfile
from .edit import apply_effect, EditError
from ..media.model import MediaError

EFFECTS = {'caption-pop', 'ktype', 'hook', 'meme', 'flash', 'film-burn', 'grain', 'shake', 'punch', 'whip-in', 'whip-out', 'glitch', 'mirror', 'sticker'}

def parse_command(text: str) -> dict:
    """Deliberately small grammar: reject unknown intent instead of guessing."""
    text = text.strip()
    if not text or len(text) > 4000:
        raise MediaError('Describe one edit in at most 4000 characters')
    match = re.fullmatch(r'track\s+["\'](.+)["\']', text, re.I | re.S)
    if match:
        return {'effect': 'track-text', 'text': match[1]}
    match = re.fullmatch(r'reference\s+"([^"]+)"(?:\s+text\s+"([^"]+)")?', text, re.I)
    if match:
        return {'reference': match[1], 'text': match[2] or ''}
    match = re.fullmatch(r'(?:add\s+)?(caption|text|caption-pop|ktype|hook|meme)\s+["\'](.+)["\']', text, re.I | re.S)
    if match:
        return {'effect': {'caption': 'caption-pop', 'text': 'ktype'}.get(match[1].lower(), match[1].lower()), 'text': match[2]}
    match = re.fullmatch(r'(?:add\s+)?(punch|shake|flash|grain|film-burn|whip-in|whip-out|glitch|mirror|sticker)', text, re.I)
    if match:
        return {'effect': match[1].lower()}
    match = re.fullmatch(r'(?:set\s+)?volume\s+(\d+(?:\.\d+)?)\s*%', text, re.I)
    if match and float(match[1]) <= 100:
        return {'volume': float(match[1]) / 100}
    if text.lower() in ('mute', 'mute audio'):
        return {'volume': 0}
    raise MediaError('Local commands: caption "your words", text "your words", punch, shake, flash, grain, glitch, mirror, volume 50%, mute. For other edits choose your own Claude Code or Codex.')


def effect_proposal(state, studio, project, selected, plan):
    """Generate pages locally, return native ops. No worker-authored HTML or paths."""
    if not isinstance(project, str) or not re.fullmatch(r'[\w-]{1,80}', project):
        raise MediaError('Invalid editor project')
    snap = studio.request(f'/api/projects/{project}')
    film = snap.get('value')
    if not film:
        raise MediaError('Project film cannot be read')
    picked = set(selected)
    rows = [c for t in film['tracks'] for c in t['clips']]
    if not picked or not picked <= {c['id'] for c in rows}:
        raise MediaError('Select clips first')
    links = {c.get('attrs', {}).get('data-link') for c in rows if c['id'] in picked} - {None, ''}
    picked.update(c['id'] for c in rows if c.get('attrs', {}).get('data-link') in links)
    if any(t.get('locked') and any(c['id'] in picked for c in t['clips']) for t in film['tracks']):
        raise MediaError('A selected or linked track is locked')
    answer = 'Local edit ready. No model was called.'
    if not isinstance(plan, dict):
        raise MediaError('Unsupported local effect plan')
    if set(plan) == {'volume'}:
        from .agent import validate_ops
        ops = validate_ops(film, [{'op': 'set', 'clip': c['id'], 'field': 'volume', 'value': plan['volume']} for c in rows if c['id'] in picked], sorted(picked))
    else:
        if not ((not (set(plan) - {'effect', 'text'}) and plan.get('effect') in EFFECTS | {'track-text'}) or ('reference' in plan and not (set(plan) - {'reference', 'text', 'reference_shot'}))):
            raise MediaError('Unsupported local effect plan')
        # One footage clip is intentional. Linked sound is scope, not another shot.
        videos = [(ti, ci, c) for ti,t in enumerate(film['tracks']) for ci,c in enumerate(t['clips']) if c['id'] in picked and re.search(r'\.(mp4|webm|mov|mkv)(?:$|[?#])', c.get('src', ''), re.I) and not c.get('sound')]
        if len(videos) != 1:
            raise MediaError('Select one footage clip for effects')
        ti, ci, clip = videos[0]
        with tempfile.TemporaryDirectory(prefix='skye-effect-') as temp:
            root = Path(temp)
            # Preserve all existing project text; native normalizer resolves IDs and at.
            (root / 'film.html').write_text(snap['text'], encoding='utf-8')
            from .edit import read_shots
            shots = read_shots(root, ti + 1)
            number = next(s.index for s in shots if s.clip_id == clip['id'])
            try:
                if 'reference' in plan or plan.get('effect') == 'track-text':
                    project_root = Path(snap['project']['path']).resolve()
                    rel = plan.get('reference') if 'reference' in plan else shots[number-1].src
                    if not isinstance(rel, str) or Path(rel).is_absolute() or '..' in Path(rel).parts:
                        raise MediaError('Use a local asset inside this project')
                    source = (project_root/rel).resolve()
                    if not source.is_relative_to(project_root) or not source.is_file() or source.suffix.lower() not in {'.mp4','.webm','.mov','.mkv'}:
                        raise MediaError('Use a local video asset inside this project')
                    if 'reference' in plan:
                        from .reference import analyze, apply_analysis
                        analysis = analyze(source)
                        apply_analysis(root, number, analysis, reference_shot=plan.get('reference_shot',1), text=plan.get('text',''), track=ti+1)
                        answer = f"Reference measured: {len(analysis['shots'])} shots, {analysis['median_shot_seconds']}s median. Applied measured timing with generic presets, not an exact style match. No model was called."
                    else:
                        from .tracking import track_text
                        import os, shutil
                        dest = root/rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        try: os.link(source,dest)
                        except OSError: shutil.copyfile(source,dest)
                        report = track_text(root,number,plan.get('text',''),track=ti+1)
                        metadata = (root/report['tracking']).read_text().replace('<', '\\u003c')
                        page = root/report['page']
                        page.write_text(page.read_text() + '<script type="application/json" id="tracking-data">'+metadata+'</script>')
                        answer = f"Planar text tracked across {report['frames']} samples; minimum {report['min_inliers']} inliers. Inspect for drift. No model was called."
                else:
                    apply_effect(root, number, plan['effect'], text=plan.get('text', ''), track=ti+1, offset=0)
            except (EditError, ValueError, ImportError) as exc:
                raise MediaError(str(exc)) from exc
            result = subprocess.run(['node', str(Path(__file__).with_name('effect-diff.mjs'))], input=json.dumps({'before': film, 'text': (root/'film.html').read_text(), 'selected': clip['id']}), text=True, capture_output=True, timeout=20)
            if result.returncode:
                raise MediaError('The effect could not be applied safely. Your film is unchanged. Try Undo or refresh the shot, then retry. If this continues, use the manual effect controls.')
            ops = json.loads(result.stdout)
            if studio.request(f'/api/projects/{project}')['rev'] != snap['rev']:
                raise MediaError('Project changed. Ask again')
            # Pages have unique generated names, and never replace existing assets.
            for page in root.glob('pages/**/*.html'):
                studio.request(f'/api/projects/{project}/page', {'path': page.relative_to(root).as_posix(), 'text': page.read_text(), 'expect': None}, method='PUT')
    if studio.request(f'/api/projects/{project}')['rev'] != snap['rev']:
        raise MediaError('Project changed. Ask again')
    return {'project': project, 'base': snap['rev'], 'ops': ops, 'selected': sorted(picked), 'answer': answer}


def local_propose(state, studio, project, selected, goal):
    return effect_proposal(state, studio, project, selected, parse_command(goal))
