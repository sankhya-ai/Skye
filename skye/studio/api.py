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
        elif action == 'ask':
            from .agent import propose
            selected = body.get('selected')
            if not isinstance(selected, list) or len(selected) > 1000 or any(not isinstance(x, str) for x in selected):
                raise MediaError('Select clips in the timeline first')
            handler._json(job_start(handler.state_dir, 'timeline-edit', lambda progress:
                propose(handler.state_dir, studio, body['project'], selected,
                        body.get('goal', ''), body.get('adapter', 'claude-code'))), 202)
        else:
            handler._json({'error': 'Unknown editor action'}, 404)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        handler._json({'error': str(exc)}, 400)
    return True
