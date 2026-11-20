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
        elif action == 'review':
            from .review import apply
            from .storyboard import _file
            from urllib.parse import urlparse
            project=body.get('project');_file(handler.state_dir,project)
            snap=studio.request(f'/api/projects/{project}')
            # Browser provenance is derived here, never accepted from JSON.
            # Review text is data, not a general-purpose execution grant.
            origin=handler.headers.get('Origin','')
            owner_ui=bool(origin and urlparse(origin).netloc==handler.headers.get('Host') and handler.headers.get('Sec-Fetch-Site')=='same-origin')
            handler._json(apply(snap['project']['path'],snap['value'],body,owner_ui=owner_ui))
        elif action=='edge-preview':
            from .storyboard import edge_preview
            handler._json(edge_preview(handler.state_dir,studio,body.get('project'),body.get('clip')))
        elif action in ('masks','mask-change'):
            from . import mask_editor
            from .storyboard import _file
            project=body.get('project');_file(handler.state_dir,project)
            if action=='masks':handler._json(mask_editor.read(studio,project))
            else:handler._json(job_start(handler.state_dir,'mask-correction',lambda progress:mask_editor.change(studio,project,body)),202)
        elif action == 'track-preview':
            from .storyboard import describe, _file
            import urllib.request, urllib.parse, base64
            project=body.get('project');_file(handler.state_dir,project)
            snap=studio.request(f'/api/projects/{project}');shot=next((s for s in describe(handler.state_dir,project,snap)['shots'] if s['id']==body.get('clip')),None)
            if not shot:raise MediaError('Shot no longer exists')
            route=f'/api/projects/{project}/media?'+urllib.parse.urlencode({'path':'film.html','what':'frame','ms':round(shot['at']*1000),'w':480})
            req=urllib.request.Request(studio.origin+route,headers={'x-studio-key':studio.session['key']})
            with urllib.request.urlopen(req,timeout=30) as response:preview='data:image/jpeg;base64,'+base64.b64encode(response.read()).decode()
            handler._json({'preview':preview})
        elif action == 'track-prepare':
            from .commands import effect_proposal
            from .storyboard import _file
            project=body.get('project');_file(handler.state_dir,project)
            selected=[body.get('clip')]
            if not isinstance(selected[0],str):raise MediaError('Choose a footage shot')
            plan={k:body[k] for k in ('text','quad','roi','fps','replace') if k in body};plan['effect']='track-text'
            handler._json(job_start(handler.state_dir,'planar-tracking',lambda progress:effect_proposal(handler.state_dir,studio,project,selected,plan)),202)
        elif action in ('audio','audio-change'):
            from .audio import read, change
            from .storyboard import _file
            project=body.get('project');_file(handler.state_dir,project)
            handler._json(read(handler.state_dir,studio,project) if action=='audio' else change(handler.state_dir,studio,project,body))
        elif action in ('motion-text','motion-text-change'):
            from .motion import text_layers
            from .storyboard import _file
            project=body.get('project');_file(handler.state_dir,project)
            handler._json(text_layers(studio,project,body if action=='motion-text-change' else None))
        elif action in ('voice-list','voice-generate'):
            from .voice import voices,create
            if action=='voice-list':handler._json(voices())
            else:
                from .storyboard import _file
                project=body.get('project');_file(handler.state_dir,project)
                from ..media.tts import validate,ENGINE
                validate(body.get('text'),body.get('voice','Danny'),body.get('language','en'),body.get('speed',1),body.get('engine',ENGINE))
                handler._json(job_start(handler.state_dir,'local-voice',lambda progress:create(handler.state_dir,studio,project,body,progress)),202)
        elif action in ('motion-timing','motion-design'):
            from .storyboard import _file
            from pathlib import Path
            project=body.get('project');_file(handler.state_dir,project)
            snap=studio.request(f'/api/projects/{project}')
            if action=='motion-timing':
                from .timing import analyze
                handler._json(analyze(Path(snap['project']['path']),body.get('source'),body.get('mode')))
            else:
                from .design import lint
                film=snap['value'];stage=film.get('stage',{})
                handler._json(lint([c for t in film['tracks'] for c in t['clips']],stage.get('w',1920),stage.get('h',1080)))
        elif action == 'compose':
            from .motion import prepare
            handler._json(prepare(handler.state_dir,studio,body.get('project'),body))
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
            handler._json({'folder':studio.request(f'/api/projects/{project}').get('folder',''),'files':[f for f in listing.get('files',[]) if f.get('kind') in ('video','image','audio')]})
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
