"""Offline code-shot templates. Generated pages run only on the film origin."""
from pathlib import Path
import hashlib
import shutil
from ..media.model import MediaError

HERE=Path(__file__).parent
TEMPLATES={'orbit':'3D Orbit','kinetic':'Kinetic type'}

def validate_code(code):
    if not isinstance(code,str) or not 1<=len(code.encode())<=55000:
        raise MediaError('Code shot must be 1 to 55,000 bytes')
    if 'window.film' not in code or 'frame' not in code:
        raise MediaError('Expose window.film with frame(t); draw from time, not an animation loop')
    return code


def template(root,kind,duration):
    if kind not in TEMPLATES:raise MediaError('Choose a code-shot template')
    if kind=='orbit':
        source=HERE/'code-assets'/'three-0.187.0'
        target=Path(root)/'assets/skye-code/three-0.187.0'
        target.mkdir(parents=True,exist_ok=True)
        for filename in ('three.module.js','three.core.js','LICENSE'):
            dst=target/filename;src=source/filename
            if dst.exists() and hashlib.sha256(dst.read_bytes()).digest()!=hashlib.sha256(src.read_bytes()).digest():
                raise MediaError('A different Three.js asset already uses this path. Rename it before inserting')
            if not dst.exists():shutil.copyfile(src,dst)
    code=(HERE/'code-templates'/(kind+'.html')).read_text()
    # Templates use a fixed clock contract, never wall time or a requestAnimationFrame loop.
    return code.replace('duration:2,',f'duration:{duration},')


def read_code(studio,project,clip):
    snap=studio.request(f'/api/projects/{project}')
    c=next((c for t in snap['value']['tracks'] for c in t['clips'] if c['id']==clip),None)
    if not c or c.get('attrs',{}).get('data-skye-shot')!='code':raise MediaError('Select a code shot')
    root=Path(snap['project']['path']).resolve();path=(root/c['src']).resolve()
    if not path.is_relative_to(root) or path.suffix!='.html' or not path.is_file():raise MediaError('Code page is unavailable')
    code=path.read_text()
    if len(code.encode())>55000:raise MediaError('Open this large page in your code editor')
    return {'clip':clip,'code':code,'base':snap['rev']}
