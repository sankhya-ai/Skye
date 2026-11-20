"""Offline composition recipes -> individually editable native timeline clips."""
from pathlib import Path
import html,json,math,re,subprocess,uuid
from ..media.model import MediaError

LAYOUTS=('stack','arc','scatter','split','cards','wall','flip','title')
PALETTES={'mint':('#101b1a','#aaffd8','#fff9ed','#ed3449'),'paper':('#f3efe7','#db3047','#141816','#aaffd8'),'gold':('#e9c54c','#121b18','#121b18','#d72b43')}
TEXT='''<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;height:100%;overflow:hidden;background:transparent}body{display:grid;grid-template-columns:minmax(0,1fr);place-items:center}#words{font:900 92vh Arial,sans-serif;white-space:pre;text-align:center;line-height:1.02;transform-origin:center}</style></head><body><div id="words">YOUR WORDS</div><script>const el=document.getElementById('words');function fit(){el.style.transform='none';const r=el.getBoundingClientRect();el.style.transform='scale('+Math.min(1,(innerWidth-12)/Math.max(1,r.width),(innerHeight-8)/Math.max(1,r.height))+')';}new MutationObserver(fit).observe(el,{childList:true,characterData:true,subtree:true});addEventListener('resize',fit);window.film={frame:fit};</script></body></html>'''

def _num(v,lo,hi,label):
    if type(v) not in (int,float) or not math.isfinite(v) or not lo<=v<=hi:raise MediaError(f'{label} must be {lo}..{hi}')
    return float(v)

def prepare(state,studio,project,body):
    from .storyboard import _file
    _file(state,project)
    snap=studio.request(f'/api/projects/{project}');film=snap['value'];root=Path(snap['project']['path']).resolve()
    if body.get('base')!=snap['rev']:raise MediaError('Project changed. Reopen Compose')
    recipe=body.get('recipe',{})
    if not isinstance(recipe,dict) or set(recipe)-{'scenes','bpm','start'}:raise MediaError('Recipe needs scenes, optional bpm and start')
    scenes=recipe.get('scenes');bpm=_num(recipe.get('bpm',120),40,240,'BPM');end=max((c.get('at',0)+(c.get('time',[0,0])[1]-c.get('time',[0,0])[0])/c.get('speed',1) for t in film['tracks'] for c in t['clips']),default=0);start=_num(recipe.get('start',end),0,86400,'Start')
    if start<end-.001:raise MediaError('Compose appends after the current film')
    if not isinstance(scenes,list) or not 1<=len(scenes)<=24:raise MediaError('Use 1..24 scenes')
    # Validate the entire recipe and every source before writing anything.
    probes={};validated=[]
    def source(src,duration,source_in):
        if src=='':return ''
        if not isinstance(src,str) or not src.startswith('assets/') or '..' in Path(src).parts:raise MediaError('Choose imported assets')
        p=(root/src).resolve()
        if not p.is_relative_to(root) or not p.is_file() or p.suffix.lower() not in {'.mp4','.mov','.webm','.png','.jpg','.jpeg','.webp'}:raise MediaError('Choose imported picture media')
        if p.suffix.lower() in {'.mp4','.mov','.webm'}:
            if src not in probes:
                r=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(p)],capture_output=True,text=True,timeout=20)
                try:probes[src]=float(json.loads(r.stdout)['format']['duration'])
                except (ValueError,KeyError):raise MediaError('Media duration unavailable')
            if source_in+duration>probes[src]+.001:raise MediaError('Source is shorter than its scene')
        return src
    for row in scenes:
        if not isinstance(row,dict) or set(row)-{'words','layout','palette','source','second','sourceIn','beats','duration','negative'}:raise MediaError('Unsupported scene field')
        words=row.get('words',[])
        if isinstance(words,str):words=[s.strip() for s in words.split('|') if s.strip()]
        if not isinstance(words,list) or not 1<=len(words)<=5 or any(not isinstance(w,str) or not w.strip() or len(w)>70 for w in words):raise MediaError('Use 1..5 short phrase groups per scene')
        layout=row.get('layout','stack');palette=row.get('palette','mint')
        if layout not in LAYOUTS or palette not in PALETTES:raise MediaError('Unknown motion style')
        duration=_num(row['duration'],.8,12,'Duration') if 'duration' in row else _num(row.get('beats',4),1,24,'Beats')*60/bpm
        si=_num(row.get('sourceIn',0),0,86400,'Source in')
        negative=row.get('negative',False)
        if type(negative) is not bool:raise MediaError('Negative must be true or false')
        validated.append({**row,'words':words,'layout':layout,'palette':palette,'duration':duration,'sourceIn':si,'source':source(row.get('source',''),duration,si),'second':source(row.get('second',''),duration,si),'negative':negative})
    prefix='motion-'+uuid.uuid4().hex[:10];pages={};ops=[];keys={};W=film.get('stage',{}).get('w',1920);H=film.get('stage',{}).get('h',1080);at=start
    def add(src,dur,when,box=None,style='',attrs=None,overrides=None,media_in=0):
        cid=prefix+'-'+str(len(ops));clip={'id':cid,'src':src,'at':round(when,3),'time':[media_in,round(media_in+dur,3)],'attrs':attrs or {'data-skye-motion':'layer'}}
        if box:
            clip['box']={k:round(v,3) for k,v in box.items()}
            if src.endswith('.html'):clip['size']={'w':round(box['w']), 'h':round(box['h'])}
        if style:clip['style']=style
        if not src.endswith('.html'):clip['style']=clip.get('style','')+';object-fit:cover'
        if overrides:clip['overrides']=overrides
        if Path(src).suffix.lower() in {'.mp4','.mov','.webm'}:clip['volume']=0
        ops.append({'op':'insert','clip':clip,'newTrack':0});return cid
    def page(name,text):
        path=f'pages/{prefix}-{name}.html';pages[path]=text;return path
    textpage=page('text',TEXT)
    def anim(cid,prop,body,dur):
        name=cid+'--'+prop;keys[name]=body
        clip=next(o['clip'] for o in ops if o.get('clip',{}).get('id')==cid)
        clip['style']=clip.get('style','')+f';animation:{name} {dur:.3f}s linear both'
    for i,row in enumerate(validated):
        d=row['duration'];bg,accent,fg,red=PALETTES[row['palette']];layout=row['layout'];src=row['source'];si=row['sourceIn'];words=row['words']
        back=page(f'bg{i}',f'<!doctype html><html><body style="margin:0;background:{bg}"><script>window.film={{frame(){{}}}}</script></body></html>')
        add(back,d,at,attrs={'data-skye-motion':'scene','data-skye-shot':'motion'})
        if src:
            if layout in ('cards','wall'):
                count=6 if layout=='wall' else 2
                for j in range(count):
                    x=(j%3)*.34-.025 if layout=='wall' else .18+j*.36;y=(j//3)*.55-.04 if layout=='wall' else .12+j*.05;w=.37 if layout=='wall' else .4;h=.59 if layout=='wall' else .78
                    cid=add(row['second'] if j%2 and row['second'] else src,d,at,{'x':x*W,'y':y*H,'w':w*W,'h':h*H,'r':(-1)**j*7},media_in=si)
                    anim(cid,'translate',f'0%{{translate:0px 50px}}100%{{translate:{(-1)**j*50}px -45px}}',d)
                    if row['negative']:
                        name=prefix+f'-negative{i}-{j}';keys[name]='0%,7%,12%,100%{filter:invert(0)}8%,11%{filter:invert(1)}';ops[-1]['clip']['style']+=f', {name} {d}s steps(1,end) both'
            else:
                box={'x':0,'y':0,'w':W,'h':H}
                if layout=='split':box['w']=W*.5
                cid=add(src,d,at,box,style='filter:brightness(.62) saturate(.8)',attrs={'data-skye-motion':layout},media_in=si)
                if layout=='flip':anim(cid,'rotate','0%{rotate:0deg;animation-timing-function:cubic-bezier(.65,0,.25,1)}28%{rotate:180deg}100%{rotate:180deg}',d)
                else:anim(cid,'scale','0%{scale:1.03}100%{scale:1.13}',d)
                if layout=='split' and row['second']:add(row['second'],d,at,{'x':W*.5,'y':0,'w':W*.5,'h':H},style='filter:brightness(.65)',media_in=si)
                if row['negative']:
                    name=prefix+f'-negative{i}';keys[name]='0%,7%,12%,100%{filter:brightness(.62) invert(0)}8%,11%{filter:brightness(.8) invert(1)}';clip=next(o['clip'] for o in ops if o.get('clip',{}).get('id')==cid);clip['style']+=f', {name} {d}s steps(1,end) both'
        # Distinct, staggered phrase groups are separate text clips, not a canvas.
        for j,word in enumerate(words):
            n=len(words);delay=min(j*.18,d*.14*j);dur=d-delay;x=.06;y=.1+j*(.7/max(2,n));ww=.86;hh=.32 if n<3 else .24;r=0
            if layout in ('scatter','cards','wall'):x=[.06,.33,.11,.45,.16][j];y=[.13,.34,.61,.76,.02][j];ww=[.63,.61,.8,.48,.65][j];hh=[.23,.21,.25,.12,.12][j];r=[-8,6,-4,8,-4][j]
            elif layout=='arc':x=[.04,.46,.48,.13,.65][j];y=[.39,.09,.63,.75,.78][j];ww=.52 if j==0 else .46;hh=.22 if j==0 else .15;r=[-16,5,16,-8,10][j]
            elif layout=='title':x=.08;y=.15+j*.4;ww=.84;hh=.4 if j==0 else .18;r=-3 if j==0 else 0
            elif layout=='flip':y=.14+j*.25;r=0
            elif layout=='split':x=.05 if j%2==0 else .44;y=.14+j*.23;ww=.55;r=(-1)**j*5
            color=accent if j%2==0 else ('#fff9ed' if src else fg)
            cid=add(textpage,dur,at+delay,{'x':x*W,'y':y*H,'w':ww*W,'h':hh*H,'r':r},attrs={'data-skye-motion':'text','data-skye-phrase':word},overrides=[{'at':'#words','text':word,'style':{'color':color}}])
            end=max(55,100-100*.25/dur)
            anim(cid,'translate',f'0%{{translate:{(1 if j%2 else -1)*150}px 55px;opacity:0;scale:.8;filter:blur(10px);animation-timing-function:cubic-bezier(.12,.85,.2,1)}}22%{{translate:0px 0px;opacity:1;scale:1;filter:blur(0px)}}{end:.3f}%{{translate:0px 0px;opacity:1;scale:1;filter:blur(0px);animation-timing-function:cubic-bezier(.7,0,1,.3)}}100%{{translate:-100px -35px;opacity:0;scale:1.25;filter:blur(8px)}}',dur)
        if i>0:
            mosaic=page(f'blocks{i}',f'''<!doctype html><html><body style="margin:0;overflow:hidden"><script>for(let i=0;i<48;i++){{const e=document.createElement('div');e.style.cssText='position:absolute;left:'+(i%8*12.5)+'vw;top:'+Math.floor(i/8)*16.67+'vh;width:12.6vw;height:16.8vh;background:'+(i%5?'{{accent}}':'{{red}}');document.body.append(e)}}const tiles=[...document.body.children].filter(e=>e.tagName==='DIV');window.film={{frame(t){{tiles.forEach((e,i)=>e.style.opacity=t/.22<((i*29)%47)/47?'1':'0')}}}};</script></body></html>'''.replace('{accent}',accent).replace('{red}',red))
            add(mosaic,.22,at,attrs={'data-skye-motion':'blocks'})
        at+=d
    # Reuse non-overlapping lanes. One track per phrase would bury a short reel
    # under dozens of mostly-empty rows in the native editor.
    groups={0:[],1:[],2:[],3:[]}
    for op in ops:
        c=op['clip'];kind=c['attrs'].get('data-skye-motion');tier=0 if kind=='scene' else 2 if kind=='text' else 3 if kind=='blocks' else 1
        groups[tier].append(c)
    arranged=[]
    for tier in sorted(groups):
        lanes=[]
        for c in sorted(groups[tier],key=lambda c:c['at']):
            lane=next((l for l in lanes if l[-1]['at']+l[-1]['time'][1]-l[-1]['time'][0]<=c['at']+.0001),None)
            if lane is None:lane=[];lanes.append(lane)
            lane.append(c)
        for lane in lanes:
            for j,c in enumerate(lane):arranged.append({'op':'insert','clip':c,**({'newTrack':0} if j==0 else {'track':0})})
    ops=arranged
    ops.append({'op':'keyframes','set':keys})
    check=subprocess.run(['node',str(Path(__file__).with_name('check.mjs'))],input=json.dumps({'film':film,'ops':ops}),text=True,capture_output=True,timeout=20)
    if check.returncode:raise MediaError('Motion recipe failed native validation')
    if studio.request(f'/api/projects/{project}')['rev']!=snap['rev']:raise MediaError('Project changed. Reopen Compose')
    for path,text in pages.items():studio.request(f'/api/projects/{project}/page',{'path':path,'text':text,'expect':None},method='PUT')
    return {'project':project,'base':snap['rev'],'ops':ops,'answer':f'{len(validated)} scenes, {sum(len(s["words"]) for s in validated)} text layers. No model called.','duration':round(at-start,3)}

def main(argv=None):
    """Offline CLI; use only with the project closed in the editor."""
    import argparse,hashlib,tempfile
    parser=argparse.ArgumentParser(description='Compose a film from built-in motion recipes. Close this project in the editor first.')
    parser.add_argument('project',type=Path);parser.add_argument('--recipe',type=Path,required=True);parser.add_argument('--apply',action='store_true')
    args=parser.parse_args(argv);root=args.project.resolve();film_path=root/'film.html';before=film_path.read_text();rev=hashlib.sha256(before.encode()).hexdigest()
    parsed=subprocess.run(['node',str(Path(__file__).with_name('effect-check.mjs'))],input=before,text=True,capture_output=True,check=True)
    value=json.loads(parsed.stdout)
    class Offline:
        def request(self,route,body=None,method=None):
            if route.endswith('/page'):
                p=root/body['path'];p.parent.mkdir(exist_ok=True,parents=True)
                if p.exists():raise MediaError('Generated page already exists')
                p.write_text(body['text']);return {}
            if film_path.read_text()!=before:raise MediaError('Project changed. Stop editing it before composing')
            return {'value':value,'rev':rev,'project':{'path':str(root)}}
    with tempfile.TemporaryDirectory() as state:
        result=prepare(Path(state),Offline(),'offline',{'base':rev,'recipe':json.loads(args.recipe.read_text())})
    if args.apply:
        writer="import{readFileSync}from'node:fs';import{applyOps}from'./vendor/openfilm/studio/server/ops.mjs';import{filmHtml}from'./vendor/openfilm/src/film-doc.mjs';const x=JSON.parse(readFileSync(0,'utf8'));process.stdout.write(filmHtml(applyOps(x.film,x.ops),x.before));"
        output=subprocess.run(['node','--input-type=module','-e',writer],cwd=Path(__file__).parent,input=json.dumps({'film':value,'ops':result['ops'],'before':before}),text=True,capture_output=True,check=True).stdout
        if film_path.read_text()!=before:raise MediaError('Project changed before save')
        backup=root/'.film/backups'/('before-'+rev[:12]+'.html');backup.parent.mkdir(parents=True,exist_ok=True);backup.write_text(before)
        tmp=film_path.with_suffix('.compose.tmp');tmp.write_text(output);tmp.replace(film_path)
        print(json.dumps({'applied':True,'duration':result['duration'],'backup':str(backup),'answer':result['answer']}))
    else:print(json.dumps({'applied':False,'duration':result['duration'],'ops':result['ops'],'answer':result['answer']}))
    return 0


def text_layers(studio,project,body=None):
    snap=studio.request(f'/api/projects/{project}');rows=[]
    for track in snap['value']['tracks']:
        for c in track['clips']:
            if c.get('attrs',{}).get('data-skye-motion')!='text':continue
            over=next((o for o in c.get('overrides',[]) if o.get('at')=='#words'),{})
            rows.append({'id':c['id'],'text':over.get('text',''),'at':c.get('at',0),'locked':bool(track.get('locked'))})
    rows.sort(key=lambda r:r['at'])
    if body is None:return {'base':snap['rev'],'rows':rows}
    if body.get('base')!=snap['rev']:raise MediaError('Project changed. Reopen Words')
    edits=body.get('edits')
    if not isinstance(edits,dict) or not 1<=len(edits)<=120:raise MediaError('Choose text layers')
    by_id={r['id']:r for r in rows};clips={c['id']:c for t in snap['value']['tracks'] for c in t['clips']};ops=[]
    for cid,text in edits.items():
        if cid not in by_id or by_id[cid]['locked']:raise MediaError('Text layer is missing or locked')
        if not isinstance(text,str) or not text.strip() or len(text)>70:raise MediaError('Use 1..70 characters per phrase')
        overrides=[{**o,'text':text} if o.get('at')=='#words' else o for o in clips[cid].get('overrides',[])]
        ops.append({'op':'set','clip':cid,'field':'overrides','value':overrides})
    return {'project':project,'base':snap['rev'],'ops':ops,'answer':'Words prepared. Native Undo restores them.'}

if __name__=='__main__':raise SystemExit(main())
