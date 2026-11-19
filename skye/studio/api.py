"""Skye-authenticated control surface; editor keys never go to worker proposals."""
from __future__ import annotations
import json
from . import start
from ..media.api import job_start
from ..media.model import MediaError


def post(handler, path: str):
    if not path.startswith('/api/studio/'):
        return False
    try:
        n = int(handler.headers.get('Content-Length', '0'))
        if not 0 < n <= 64_000:
            raise MediaError('Editor request exceeds 64 KB')
        body = json.loads(handler.rfile.read(n))
        if not isinstance(body, dict):
            raise MediaError('Editor request must be an object')
        studio = start(handler.state_dir)
        action = path.removeprefix('/api/studio/')
        if action == 'start':
            handler._json({'url': studio.session['url'], 'origin': studio.origin})
        elif action == 'projects':
            handler._json(studio.request('/api/projects'))
        elif action == 'preflight':
            from .storyboard import _file
            from .preflight import inspect
            project=body.get('project');_file(handler.state_dir,project)
            handler._json(inspect(studio,project))
        elif action == 'code-shot':
            from .storyboard import _file
            from .codeshots import read_code
            project=body.get('project');_file(handler.state_dir,project)
            handler._json(read_code(studio,project,body.get('clip')))
        elif action == 'assets':
            from .storyboard import _file
            project=body.get('project');_file(handler.state_dir,project)
            listing=studio.request(f'/api/projects/{project}/files')
            handler._json({'folder':studio.request(f'/api/projects/{project}').get('folder',''),'files':[f for f in listing.get('files',[]) if f.get('kind') in ('video','image')]})
        elif action == 'import-url':
            from .importer import import_url
            handler._json(import_url(studio, body.get('project'), body.get('url'), body.get('rel')))
        elif action == 'storyboard-layout':
            from .storyboard import save_layout, _file
            project=body.get('project');_file(handler.state_dir,project)
            handler._json(save_layout(handler.state_dir,studio,project,body))
        elif action == 'storyboard-change':
            from .storyboard import plan_change
            handler._json(plan_change(handler.state_dir,studio,body.get('project'),body))
        elif action in ('storyboard', 'shot-note'):
            from .storyboard import describe, save_note, _file
            project=body.get('project');_file(handler.state_dir,project)
            if action == 'shot-note':
                handler._json(save_note(handler.state_dir,studio,project,body.get('clip'),body.get('note')))
            else:
                handler._json(describe(handler.state_dir,project,studio.request(f'/api/projects/{project}'),thumbnails=True,studio=studio))
        elif action == 'ask':
            from .agent import propose
            from .commands import local_propose
            selected = body.get('selected')
            if not isinstance(selected, list) or len(selected) > 1000 or any(not isinstance(x, str) for x in selected):
                raise MediaError('Select clips in the timeline first')
            handler._json(job_start(handler.state_dir, 'timeline-edit', lambda progress:
                (local_propose(handler.state_dir, studio, body['project'], selected, body.get('goal', ''))
                 if body.get('adapter') == 'local' else
                 propose(handler.state_dir, studio, body['project'], selected,
                        body.get('goal', ''), body.get('adapter', 'claude-code')))), 202)
        else:
            handler._json({'error': 'Unknown editor action'}, 404)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        handler._json({'error': str(exc)}, 400)
    return True
