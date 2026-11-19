"""Non-destructive prepared-alpha edge corrections, without model inference."""
from pathlib import Path
import base64
import io
import json
import math
import shutil
import uuid
from ..media.model import MediaError


def _path(root, base, relative):
    if not isinstance(relative,str) or not relative or Path(relative).is_absolute() or '..' in Path(relative).parts or ':' in relative or '\\' in relative:
        raise MediaError('Mask paths must be relative project files')
    path=(base/relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():raise MediaError('Mask file is missing or outside the project')
    return path


def _manifest(root,relative):
    path=_path(root,root,relative)
    try:data=json.loads(path.read_text())
    except (OSError,ValueError) as exc:raise MediaError('Cannot read prepared mask manifest') from exc
    if not isinstance(data,dict) or not isinstance(data.get('frames'),list) or not data['frames'] or len(data['frames'])>9000:raise MediaError('Invalid prepared mask frame list')
    fps=data.get('fps');start=data.get('start')
    if type(fps) not in (float,int) or not math.isfinite(fps) or fps<=0 or type(start) not in (float,int) or not math.isfinite(start) or start<0:raise MediaError('Invalid prepared mask timing')
    frames=[_path(root,path.parent,f) for f in data['frames']]
    return path,data,frames


def read(studio,project):
    from PIL import Image
    snap=studio.request(f'/api/projects/{project}');root=Path(snap['project']['path']).resolve();rows=[]
    for ti,track in enumerate(snap['value']['tracks']):
        for c in track['clips']:
            if not c.get('matte'):continue
            _,data,frames=_manifest(root,c['matte'])
            # Preview the alpha actually used at the clip's first source time.
            index=math.floor((c.get('time',[0])[0]-data['start'])*data['fps']+1e-5)
            if not 0<=index<len(frames):raise MediaError('Prepared mask has no frame for the clip source start')
            with Image.open(frames[index]) as image:
                if image.mode!='RGBA':raise MediaError('Prepared mask frame needs an alpha channel')
                alpha=image.getchannel('A');alpha.thumbnail((320,220));buf=io.BytesIO();alpha.save(buf,format='PNG')
            rows.append({'id':c['id'],'source':c['src'],'matte':c['matte'],'track':ti+1,'locked':bool(track.get('locked')),'frames':len(frames),'preview':'data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()})
    return {'base':snap['rev'],'rows':rows}


def change(studio,project,body):
    from PIL import Image,ImageFilter
    snap=studio.request(f'/api/projects/{project}')
    if body.get('base')!=snap['rev']:raise MediaError('Project changed. Refresh masks')
    found=next(((t,c) for t in snap['value']['tracks'] for c in t['clips'] if c['id']==body.get('clip') and c.get('matte')),None)
    if not found:raise MediaError('Choose a prepared mask layer')
    track,clip=found
    if track.get('locked'):raise MediaError('Mask track is locked')
    grow=body.get('grow',0);feather=body.get('feather',0)
    if type(grow) is not int or not -20<=grow<=20 or type(feather) not in (int,float) or not math.isfinite(feather) or not 0<=feather<=20:raise MediaError('Grow must be integer -20..20 pixels; feather must be 0..20 pixels')
    if not grow and not feather:raise MediaError('Choose a nonzero edge correction')
    root=Path(snap['project']['path']).resolve();_,data,frames=_manifest(root,clip['matte']);out=(root/'assets'/('mask-edit-'+uuid.uuid4().hex[:12])).resolve()
    if not out.is_relative_to(root):raise MediaError('Mask output must stay inside the project')
    out.mkdir(parents=True)
    try:
        names=[]
        for i,frame in enumerate(frames):
            with Image.open(frame) as image:
                if image.mode!='RGBA':raise MediaError('Prepared mask frame needs an alpha channel')
                alpha=image.getchannel('A')
                if grow:alpha=alpha.filter((ImageFilter.MaxFilter if grow>0 else ImageFilter.MinFilter)(2*abs(grow)+1))
                if feather:alpha=alpha.filter(ImageFilter.GaussianBlur(feather))
                result=Image.new('RGBA',image.size,'white');result.putalpha(alpha);name=f'f{i:06d}.png';result.save(out/name);names.append(name)
        data={**data,'frames':names,'correction':{'source':clip['matte'],'grow_px':grow,'feather_px':feather}};(out/'manifest.json').write_text(json.dumps(data))
        if studio.request(f'/api/projects/{project}')['rev']!=snap['rev']:raise MediaError('Project changed while preparing masks. Retry from the current version')
    except Exception:
        shutil.rmtree(out,ignore_errors=True);raise
    return {'project':project,'base':snap['rev'],'ops':[{'op':'fields','clip':clip['id'],'fields':{'matte':str((out/'manifest.json').relative_to(root))}}],'answer':f'Prepared {len(frames)} corrected alpha frames. Original masks kept. Native Undo restores the previous mask.'}
