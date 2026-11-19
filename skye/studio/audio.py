"""Audio arrangement and gain edits through native timeline operations only."""
from pathlib import Path
import json
import math
import subprocess
import uuid
from urllib.parse import unquote, urlsplit
from ..media.model import MediaError
from .storyboard import describe

VIDEO={'.mp4','.mov','.webm','.mkv','.m4v'}
AUDIO={'.wav','.mp3','.m4a','.aac','.ogg','.opus','.flac','.aiff','.aif'}


def read(state,studio,project):
    snap=studio.request(f'/api/projects/{project}')
    board=describe(state,project,snap)
    rows=[]
    for ti,track in enumerate(snap['value']['tracks']):
        for c in track['clips']:
            if not c.get('sound') and Path(urlsplit(c['src']).path).suffix.lower() not in VIDEO|AUDIO:continue
            tm=c.get('time',[0,0]);fade=next((o['fade'] for o in c.get('overrides',[]) if o and 'fade' in o),[0,0])
            rows.append({'id':c['id'],'source':c['src'],'at':c.get('at',0),'duration':(tm[1]-tm[0])/c.get('speed',1),'volume':c.get('volume',1),'fade':fade,'track':ti,'locked':bool(track.get('locked')),'trackMuted':bool(track.get('muted')),'bed':c.get('attrs',{}).get('data-skye-audio')=='bed','sound':bool(c.get('sound')) or Path(urlsplit(c['src']).path).suffix.lower() in AUDIO})
    return {'base':snap['rev'],'rows':rows,'shots':[{'id':s['id'],'shot':s['shot'],'at':s['at'],'duration':s['duration']} for s in board['shots']],'duration':max((s['at']+s['duration'] for s in board['shots']),default=0)}


def change(state,studio,project,body):
    snap=studio.request(f'/api/projects/{project}')
    if snap['rev']!=body.get('base'):raise MediaError('Project changed. Refresh the mixer')
    def number(name,lo,hi,default):
        value=body.get(name,default)
        if not isinstance(value,(int,float)) or not math.isfinite(value) or not lo<=value<=hi:raise MediaError(f'{name} must be {lo}..{hi}')
        return value
    action=body.get('action');ops=[]
    if action=='levels':
        row=next((r for r in read(state,studio,project)['rows'] if r['id']==body.get('clip')),None)
        if not row:raise MediaError('Choose a sound or footage clip')
        if row['locked']:raise MediaError('Audio track is locked')
        volume=number('volume',0,2,1);fade_in=number('fadeIn',0,60,0);fade_out=number('fadeOut',0,60,0)
        if fade_in+fade_out>row['duration']+.001:raise MediaError('Audio fades exceed the clip duration')
        # Video fade is a picture fade too. Keep these controls sound-only.
        ops=[{'op':'set','clip':row['id'],'field':'volume','value':volume}]
        if row['sound']:ops.append({'op':'props','edits':[{'clip':row['id'],'prop':'fade','value':[fade_in,fade_out]}]})
    elif action=='insert':
        root=Path(snap['project']['path']).resolve();src=body.get('source','');url=urlsplit(src)
        source=(root/unquote(url.path)).resolve()
        if url.scheme or url.netloc or Path(url.path).is_absolute() or not source.is_relative_to(root) or not source.is_file() or source.suffix.lower() not in AUDIO|VIDEO:raise MediaError('Choose an imported audio or video file')
        source_in=number('sourceIn',0,86400,0)
        board=describe(state,project,snap);target=body.get('target','bed')
        shot=next((s for s in board['shots'] if s['id']==target),None)
        if target!='bed' and not shot:raise MediaError('Choose the whole-film bed or a current shot')
        at=shot['at'] if shot else 0
        duration=shot['duration'] if shot else max((s['at']+s['duration'] for s in board['shots']),default=0)
        if duration<=0:raise MediaError('Add a picture shot before adding sound')
        probe=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration:stream=codec_type','-of','json',str(source)],capture_output=True,text=True,timeout=20)
        info=json.loads(probe.stdout)
        if not any(s.get('codec_type')=='audio' for s in info.get('streams',[])):raise MediaError('This file has no audio stream')
        if source_in+duration>float(info['format']['duration'])+.001:raise MediaError('Audio source is too short for this target. Choose a longer source or a shorter shot. No automatic looping or stretching')
        cid='sound-'+uuid.uuid4().hex[:10]
        clip={'id':cid,'src':str(source.relative_to(root)),'time':[source_in,source_in+duration],'attrs':{'data-skye-audio':'bed' if not shot else 'shot'}}
        if source.suffix.lower() in VIDEO:clip['sound']=True
        if shot:clip['attrs']['data-skye-shot-owner']=shot['id']
        ops=[{'op':'insert','clip':clip,'at':at,'newTrack':len(snap['value']['tracks'])}]
    elif action=='remove':
        row=next((r for r in read(state,studio,project)['rows'] if r['id']==body.get('clip')),None)
        if not row or not row['sound']:raise MediaError('Only a separate sound clip can be removed here')
        if row['locked']:raise MediaError('Audio track is locked')
        ops=[{'op':'remove','clip':row['id']}]
    else:raise MediaError('Choose an audio action')
    return {'project':project,'base':snap['rev'],'ops':ops,'answer':'Audio change prepared. Native Undo restores the previous mix.'}
