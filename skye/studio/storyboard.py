"""Storyboard read model over the current film. Notes keyed by stable clip IDs."""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import threading
from ..media.model import MediaError
_LOCK=threading.RLock()


def _file(state,project):
    if not isinstance(project,str) or not re.fullmatch(r'[\w-]{1,80}',project):raise MediaError('Invalid project')
    folder=Path(state)/'studio-storyboards';folder.mkdir(parents=True,exist_ok=True)
    return folder/(project+'.json')


def notes(state,project):
    try:return json.loads(_file(state,project).read_text())
    except (FileNotFoundError,ValueError):return {}


def save_note(state,studio,project,clip,note):
    if not isinstance(note,str) or len(note)>2000:raise MediaError('Shot note must be at most 2000 characters')
    snap=studio.request(f'/api/projects/{project}')
    if clip not in {c['id'] for t in snap['value']['tracks'] for c in t['clips']}:raise MediaError('Shot no longer exists')
    with _LOCK:
        data=notes(state,project);data[clip]=note
        path=_file(state,project);temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,ensure_ascii=False));temp.replace(path)
    return {'clip':clip,'note':note}


def describe(state,project,snapshot,*,thumbnails=False,studio=None):
    saved=notes(state,project);film=snapshot.get('value') or {};root=Path(snapshot.get('project',{}).get('path','/nonexistent')).resolve()
    video=re.compile(r'\.(mp4|mov|webm|mkv|m4v)(?:$|[?#])',re.I)
    overlays=[c for t in film.get('tracks',[]) for c in t['clips'] if not video.search(c.get('src','')) and not c.get('sound')]
    footage=[c for t in film.get('tracks',[]) for c in t['clips'] if video.search(c.get('src','')) and not c.get('sound') and not c.get('matte')]
    def overlaps_footage(c):
        a=c.get('at',0);tm=c.get('time',[0,c.get('len',0)]);b=a+(tm[1]-tm[0])/c.get('speed',1)
        return any(v.get('at',0)<b-1e-6 and v.get('at',0)+(v.get('time',[0,0])[1]-v.get('time',[0,0])[0])/v.get('speed',1)>a+1e-6 for v in footage)
    shots=[]
    for ti,t in enumerate(film.get('tracks',[])):
        for c in t['clips']:
            src=c.get('src','')
            if c.get('attrs',{}).get('data-skye-edge'):continue
            if c.get('sound') or (not video.search(src) and not c.get('attrs',{}).get('data-skye-shot') and (not src.lower().endswith(('.html','.htm')) or overlaps_footage(c))) or c.get('matte') or 'glitch' in c.get('id',''):continue
            start,end=c.get('time',[0,c.get('len',0)]);speed=c.get('speed',1);at=c.get('at',0);duration=(end-start)/speed if speed else 0
            if duration<=0:continue
            effects=[];texts=[]
            for over in overlays:
                oa=over.get('at',0);ot=over.get('time',[0,0]);od=(ot[1]-ot[0])/over.get('speed',1)
                if oa>=at+duration-1e-6 or oa+od<=at+1e-6:continue
                name=Path(over.get('src','').split('#')[0]).stem;effects.append(name)
                page=(root/over.get('src','').split('#')[0]).resolve()
                if page.is_relative_to(root) and page.suffix.lower() in ('.html','.htm') and page.is_file() and page.stat().st_size<500000:
                    from html.parser import HTMLParser
                    class Text(HTMLParser):
                        def __init__(self):super().__init__();self.skip=0;self.words=[]
                        def handle_starttag(self,tag,attrs):
                            if tag in ('script','style'):self.skip+=1
                        def handle_endtag(self,tag):
                            if tag in ('script','style'):self.skip=max(0,self.skip-1)
                        def handle_data(self,data):
                            if not self.skip and data.strip():self.words.append(data.strip())
                    parser=Text();parser.feed(page.read_text(errors='replace'));text=' '.join(parser.words)
                    if text:texts.append(text[:250])
            for over in c.get('overrides',[]):
                if over:effects.append('clip adjustment')
            if c.get('style'):effects.append('clip style')
            if speed!=1:effects.append(f'{speed:g}x speed')
            item={'id':c['id'],'shot':len(shots)+1,'track':ti+1,'at':at,'duration':round(duration,3),'source':src,'sourceIn':start,'sourceOut':end,'text':texts,'effects':list(dict.fromkeys(effects)),'note':saved.get(c['id'],''),'code':c.get('attrs',{}).get('data-skye-shot')=='code','locked':bool(t.get('locked')),'placeholder':c.get('attrs',{}).get('data-skye-shot')=='placeholder','transition':json.loads(c.get('attrs',{}).get('data-skye-transition','{}')).get('effect','cut')}
            if thumbnails:
                source=(root/src.split('#')[0]).resolve()
                if source.is_relative_to(root) and source.is_file():
                    cache=Path(state)/'studio-storyboards'/'frames';cache.mkdir(parents=True,exist_ok=True)
                    key=hashlib.sha256(f'{source}:{source.stat().st_mtime_ns}:{start}:{duration}:{speed}'.encode()).hexdigest()[:24];thumb=cache/(key+'.jpg')
                    if not thumb.exists() and video.search(src):
                        proc=subprocess.run(['ffmpeg','-v','error','-ss',str(start+min(duration*speed/2,.5)),'-i',str(source),'-frames:v','1','-vf','scale=480:270:force_original_aspect_ratio=decrease,pad=480:270:(ow-iw)/2:(oh-ih)/2','-q:v','4','-f','image2pipe','-vcodec','mjpeg','-'],capture_output=True,timeout=25)
                        if proc.returncode==0 and proc.stdout:thumb.write_bytes(proc.stdout)
                    if not thumb.exists() and not video.search(src) and studio:
                        import urllib.request,urllib.parse
                        route='/api/projects/'+project+'/media?'+urllib.parse.urlencode({'path':src,'what':'frame','ms':round((start+min(duration/2,.5))*1000),'w':480,'clip':c['id']})
                        request=urllib.request.Request(studio.origin+route,headers={'x-studio-key':studio.session['key']})
                        try:
                            with urllib.request.urlopen(request,timeout=25) as response:thumb.write_bytes(response.read())
                        except OSError:pass
                    if thumb.exists():item['thumbnail']='data:image/jpeg;base64,'+base64.b64encode(thumb.read_bytes()).decode()
            shots.append(item)
    shots.sort(key=lambda s:(s['at'],s['track']))
    for i,shot in enumerate(shots,1):shot['shot']=i
    return {'project':project,'name':snapshot.get('project',{}).get('name',project),'stage':film.get('stage',{}),'rev':snapshot.get('rev'),'shots':shots}


def plan_change(state,studio,project,body):
    """Storyboard mutations return native operations, never write the film directly."""
    import math,uuid,html
    _file(state,project)
    snap=studio.request(f'/api/projects/{project}');film=snap['value']
    if body.get('base')!=snap['rev']:raise MediaError('Project changed. Refresh the storyboard')
    board=describe(state,project,snap);shots=board['shots'];action=body.get('action')
    spanning=body.get('spanning','reject')
    if spanning not in ('reject','anchor'):raise MediaError('Choose how spanning music and overlays are handled')
    shot_ids={s['id'] for s in shots}
    def anchored(c):
        if spanning!='anchor' or c['id'] in shot_ids:return False
        x,y=window(c)
        return any(x<s['at']-1e-6 and y>s['at']+1e-6 for s in shots) or any(x<s['at']+s['duration']-1e-6 and y>s['at']+s['duration']+1e-6 for s in shots)
    rows=[(t,c) for t in film['tracks'] for c in t['clips']]
    def window(c):
        a=c.get('at',0);time=c.get('time',[0,c.get('len',0)]);return a,a+(time[1]-time[0])/c.get('speed',1)
    ops=[]
    if action=='insert':
        index=body.get('index')
        if not isinstance(index,int) or not 0<=index<=len(shots):raise MediaError('Invalid insertion point')
        duration=body.get('duration',2)
        source_in=body.get('sourceIn',0)
        if not isinstance(source_in,(int,float)) or not math.isfinite(source_in) or source_in<0:raise MediaError('Source in must be a positive time or zero')
        if not isinstance(duration,(int,float)) or not math.isfinite(duration) or not .1<=duration<=60:raise MediaError('Shot duration must be 0.1..60 seconds')
        at=shots[index]['at'] if index<len(shots) else max((s['at']+s['duration'] for s in shots),default=0)
        note=body.get('note','')
        if not isinstance(note,str) or len(note)>2000:raise MediaError('Shot note must be at most 2000 characters')
        src=body.get('source','');cid='shot-'+uuid.uuid4().hex[:10]
        if body.get('kind')=='code':
            from .codeshots import template
            src='pages/'+cid+'.html'
            page=template(snap['project']['path'],body.get('template','orbit'),duration)
            clip={'id':cid,'src':src,'time':[0,duration],'attrs':{'data-skye-shot':'code'}}
        elif src:
            root=Path(snap['project']['path']).resolve();source=(root/src).resolve()
            if Path(src).is_absolute() or '..' in Path(src).parts or not source.is_relative_to(root) or not source.is_file() or source.suffix.lower() not in {'.mp4','.mov','.webm','.mkv','.png','.jpg','.jpeg','.webp'}:raise MediaError('Choose an imported video or image inside the project')
            # Do not guess source duration or silently extend beyond the real clip.
            if source.suffix.lower() in {'.mp4','.mov','.webm','.mkv'}:
                probe=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(source)],capture_output=True,text=True,timeout=20)
                total=float(json.loads(probe.stdout)['format']['duration'])
                if source_in+duration>total+.001:raise MediaError('Requested shot is longer than the source clip')
            clip={'id':cid,'src':src,'time':[source_in,source_in+duration],'style':'object-fit:cover','attrs':{'data-skye-shot':'imported'}}
        else:
            src='pages/'+cid+'.html'
            page='<!doctype html><html><head><meta charset="utf-8"><style>body{margin:0;background:#eeeeef;color:#777;font:48px Arial;display:grid;place-content:center;height:100vh;text-align:center;padding:6vw;box-sizing:border-box}small{font-size:24px}</style></head><body><div>Planned shot</div><small>'+html.escape(note or 'Add footage when ready')+'</small><script>window.film={duration:'+str(duration)+',frame(){}};</script></body></html>'
            clip={'id':cid,'src':src,'time':[0,duration],'attrs':{'data-skye-shot':'placeholder'}}
        # A boundary insert may move every track. Never quietly edit locked data.
        for t,c in rows:
            if anchored(c):continue
            a,b=window(c)
            if t.get('locked') and b>at+1e-6:raise MediaError('Insertion would change a locked track')
            if a<at-1e-6 and b>at+1e-6:raise MediaError('A clip spans this boundary. Split it in the editor before inserting')
        from .ripple import plan_insert
        target=shots[min(index,len(shots)-1)]['track']-1 if shots else 0
        if not film['tracks']:
            ops=[{'op':'insert','clip':clip,'at':at,'newTrack':0}]
        else:
            import copy
            ripple_film=copy.deepcopy(film)
            for track in ripple_film['tracks']:track['clips']=[c for c in track['clips'] if not anchored(c)]
            ops=plan_insert(ripple_film,target,at,clip,duration)
        if body.get('kind')=='code' or not body.get('source'):
            studio.request(f'/api/projects/{project}/page',{'path':src,'text':page,'expect':None},method='PUT')
        with _LOCK:
            saved=notes(state,project);saved[cid]=note;path=_file(state,project);path.write_text(json.dumps(saved))
    elif action=='code':
        from .codeshots import validate_code,read_code
        current=read_code(studio,project,body.get('clip'));code=validate_code(body.get('code'))
        clip=next(c for t,c in rows if c['id']==body.get('clip'))
        if any(t.get('locked') and c['id']==clip['id'] for t,c in rows):raise MediaError('Code shot is locked')
        path='pages/code-'+uuid.uuid4().hex[:12]+'.html'
        studio.request(f'/api/projects/{project}/page',{'path':path,'text':code,'expect':None},method='PUT')
        ops=[{'op':'fields','clip':clip['id'],'fields':{'src':path}}]
    elif action=='reorder':
        order=body.get('order')
        if not isinstance(order,list) or len(order)!=len(shots) or len(set(order))!=len(order) or set(order)!={s['id'] for s in shots}:raise MediaError('Order must contain each current shot exactly once')
        byid={s['id']:s for s in shots};mapping={};cursor=shots[0]['at'] if shots else 0
        for cid in order:
            mapping[cid]=cursor;cursor+=byid[cid]['duration']
        for left,right in zip(shots,shots[1:]):
            if abs(left['at']+left['duration']-right['at'])>.002:raise MediaError('Reorder requires adjacent nonoverlapping shots')
        for t,c in rows:
            if anchored(c):continue
            x,y=window(c)
            match=next((s for s in shots if x>=s['at']-1e-6 and y<=s['at']+s['duration']+1e-6),None)
            if match is None:
                if x>=cursor-1e-6:continue
                raise MediaError('A clip spans a shot boundary. Split it in the editor before reordering')
            at=round(mapping[match['id']]+x-match['at'],3)
            if abs(at-x)>.0001:
                if t.get('locked'):raise MediaError('Reorder would change a locked track')
                ops.append({'op':'move','clip':c['id'],'at':at})
        if not ops:raise MediaError('Shot order is unchanged')
    elif action=='transition':
        chosen=next((s for s in shots if s['id']==body.get('clip')),None)
        if chosen is None or chosen==shots[-1]:raise MediaError('Select a shot with a following shot')
        following=shots[shots.index(chosen)+1];effect=body.get('effect')
        if abs(chosen['at']+chosen['duration']-following['at'])>.002:raise MediaError('Transition requires adjacent shots')
        if chosen['locked'] or following['locked']:raise MediaError('Transition would change a locked shot')
        if effect not in ('cut','flash','film-burn','glitch','whip-out','fade-black'):raise MediaError('Unsupported transition')
        # Stored edge operations make replacement and Cut reversible and precise.
        current=next(c for t,c in rows if c['id']==chosen['id'])
        meta=current.get('attrs',{});old=meta.get('data-skye-transition')
        if old:
            record=json.loads(old)
            for cid in record.get('inserted',[]):
                if any(c['id']==cid for _,c in rows):ops.append({'op':'remove','clip':cid})
            for cid,fields in record.get('restore',{}).items():ops.append({'op':'fields','clip':cid,'fields':fields})
            # Apply restoration to a local value before generating the replacement.
            checked=subprocess.run(['node',str(Path(__file__).with_name('check.mjs'))],input=json.dumps({'film':film,'ops':ops}),capture_output=True,text=True,timeout=20)
            if checked.returncode:raise MediaError(checked.stderr[-1000:])
            clean=json.loads(checked.stdout)
        else:clean=film
        generated=[];restore={}
        if effect=='fade-black':
            for shot,fade in ((chosen,[0,min(.35,chosen['duration']/2)]),(following,[min(.35,following['duration']/2),0])):
                clip=next(c for t in clean['tracks'] for c in t['clips'] if c['id']==shot['id'])
                if clip.get('overrides'):raise MediaError('Existing clip adjustments must be cleared before a fade')
                restore[clip['id']]={'overrides':clip.get('overrides')}
                generated.append({'op':'set','clip':clip['id'],'field':'overrides','value':[{'fade':fade}]})
        elif effect!='cut':
            # Trusted generator uses the complete current film and a stable selected ID.
            import tempfile
            with tempfile.TemporaryDirectory(prefix='skye-transition-') as folder:
                tmp=Path(folder)
                code="import {filmHtml} from '"+Path(__file__).with_name('vendor').joinpath('openfilm/src/film-doc.mjs').as_uri()+"';let s='';for await(const c of process.stdin)s+=c;process.stdout.write(filmHtml(JSON.parse(s)));"
                text=subprocess.check_output(['node','--input-type=module','-e',code],input=json.dumps(clean).encode()).decode();(tmp/'film.html').write_text(text)
                from .edit import apply_effect,read_shots,EditError
                track=next(i for i,t in enumerate(clean['tracks'],1) if any(c['id']==chosen['id'] for c in t['clips']))
                number=next(s.index for s in read_shots(tmp,track) if s.clip_id==chosen['id'])
                duration=min(.35,chosen['duration']/2)
                try:apply_effect(tmp,number,effect,at=chosen['at']+chosen['duration']-duration,dur=duration,track=track)
                except EditError as exc:raise MediaError(str(exc)) from exc
                diff=subprocess.run(['node',str(Path(__file__).with_name('effect-diff.mjs'))],input=json.dumps({'before':clean,'text':(tmp/'film.html').read_text(),'selected':chosen['id']}),capture_output=True,text=True,timeout=20)
                if diff.returncode:raise MediaError(diff.stderr[-1000:])
                generated=json.loads(diff.stdout)
                for page in tmp.glob('pages/**/*.html'):studio.request(f'/api/projects/{project}/page',{'path':page.relative_to(tmp).as_posix(),'text':page.read_text(),'expect':None},method='PUT')
                for op in generated:
                    if op['op']=='fields':
                        orig=next(c for t in clean['tracks'] for c in t['clips'] if c['id']==op['clip'])
                        restore[op['clip']]={k:orig.get(k) for k in op['fields']}
        for op in generated:
            if op['op']=='insert':op['clip'].setdefault('attrs',{})['data-skye-edge']=chosen['id']
        inserted=[op['clip']['id'] for op in generated if op['op']=='insert']
        attrs=dict(current.get('attrs',{}));attrs.pop('data-skye-transition',None)
        if effect!='cut':attrs['data-skye-transition']=json.dumps({'effect':effect,'inserted':inserted,'restore':restore})
        ops+=generated+[{'op':'fields','clip':chosen['id'],'fields':{'attrs':attrs}}]
    elif action in ('delete','move'):
        index=next((i for i,s in enumerate(shots) if s['id']==body.get('clip')),None)
        if index is None:raise MediaError('Shot no longer exists')
        chosen=shots[index];a=chosen['at'];b=a+chosen['duration']
        if action=='delete':
            for t,c in rows:
                if anchored(c):continue
                x,y=window(c)
                if y<=a+1e-6:continue
                if t.get('locked'):raise MediaError('Deletion would change a locked track')
                if x>=b-1e-6:ops.append({'op':'move','clip':c['id'],'at':round(x-(b-a),3)})
                elif x>=a-1e-6 and y<=b+1e-6:ops.append({'op':'remove','clip':c['id']})
                else:raise MediaError('A clip spans this shot boundary. Split it in the editor first')
        else:
            direction=body.get('direction');j=index+(-1 if direction=='up' else 1 if direction=='down' else 0)
            if j==index or not 0<=j<len(shots):raise MediaError('No adjacent shot in that direction')
            left,right=sorted((chosen,shots[j]),key=lambda s:s['at']);cut=right['at'];lo=left['at'];hi=right['at']+right['duration']
            if abs(left['at']+left['duration']-cut)>.002:raise MediaError('Reorder requires adjacent nonoverlapping shots')
            for t,c in rows:
                if anchored(c):continue
                x,y=window(c)
                if y<=lo+1e-6 or x>=hi-1e-6:continue
                if t.get('locked'):raise MediaError('Reorder would change a locked track')
                if x>=lo-1e-6 and y<=cut+1e-6:delta=right['duration']
                elif x>=cut-1e-6 and y<=hi+1e-6:delta=-left['duration']
                else:raise MediaError('A clip spans this boundary. Split it in the editor before reordering')
                ops.append({'op':'move','clip':c['id'],'at':round(x+delta,3)})
    else:raise MediaError('Unknown storyboard action')
    # Validate with the actual native core, without the selected-clip proposal restriction.
    result=subprocess.run(['node',str(Path(__file__).with_name('check.mjs'))],input=json.dumps({'film':film,'ops':ops}),capture_output=True,text=True,timeout=20)
    if result.returncode:raise MediaError(result.stderr[-2000:])
    return {'project':project,'base':snap['rev'],'ops':ops,'answer':'Storyboard edit ready'}
