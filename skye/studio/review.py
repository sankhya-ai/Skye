"""Version-bound review notes. Local files, locked writes, no execution of note text."""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
import hashlib,json,math,os,secrets,time
from ..media.model import MediaError

@contextmanager
def locked(root):
    folder=Path(root)/'review'
    if folder.is_symlink():raise MediaError('Review folder must be local')
    folder.mkdir(exist_ok=True)
    if (folder/'.lock').is_symlink() or (folder/'notes.json').is_symlink():raise MediaError('Review files must be local')
    with (folder/'.lock').open('a+b') as f:
        if os.name=='nt':
            import msvcrt
            f.seek(0);f.write(b'0');f.flush();f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_LOCK,1)
        else:
            import fcntl
            fcntl.flock(f,fcntl.LOCK_EX)
        try:yield folder/'notes.json'
        finally:
            if os.name=='nt':f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(f,fcntl.LOCK_UN)

def load(path):
    if not path.exists():return {'schema':1,'versions':{},'notes':[]}
    try:
        data=json.loads(path.read_text())
        if data.get('schema')!=1 or not isinstance(data.get('notes'),list):raise ValueError()
        return data
    except (ValueError,AttributeError):raise MediaError('Review data is invalid')

def write(path,data):
    temp=path.with_name('.notes-'+secrets.token_hex(6)+'.tmp')
    try:
        with temp.open('w') as f:json.dump(data,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)

def number(v):return type(v) in (int,float) and math.isfinite(v)
def text(v,name,limit=2000):
    if not isinstance(v,str) or not v.strip() or len(v)>limit:raise MediaError(f'{name}: use 1..{limit} characters')
    return v.strip()
def span(a,b,duration):
    if not number(a) or not number(b) or not 0<=a<=b<=duration+.000001:raise MediaError('Range must be within this version')
    return {'start':round(a,6),'end':round(b,6)}

def version(root,film):
    """Film plus exact bytes of local picture/audio/pages, not a mutable version label."""
    root=Path(root).resolve();h=hashlib.sha256(json.dumps(film,sort_keys=True).encode());manifest={}
    sources=[]
    for track in film.get('tracks',[]):
        for clip in track['clips']:
            src=clip.get('src','')
            if src.startswith(('/', 'http:', 'https:', 'data:', '//')) or '..' in Path(src).parts:raise MediaError('Review needs local project sources')
            p=root/src
            if not p.is_file():raise MediaError('Review source is missing')
            sources.append(p)
    for p in sorted(set([root/'film.html']+sources+list((root/'assets').rglob('*'))+list((root/'pages').rglob('*')))):
        if not p.is_file():continue
        if p.is_symlink() or not p.resolve().is_relative_to(root):raise MediaError('Review versions need local project files')
        digest=hashlib.sha256()
        with p.open('rb') as f:
            for buf in iter(lambda:f.read(1024*1024),b''):digest.update(buf)
        name=p.relative_to(root).as_posix();manifest[name]=digest.hexdigest()
        h.update(name.encode()+b'\0'+digest.digest())
    duration=max((c.get('at',0)+(c.get('time',[0,0])[1]-c.get('time',[0,0])[0])/c.get('speed',1) for t in film.get('tracks',[]) for c in t['clips']),default=0)
    if not number(duration) or duration<=0:raise MediaError('Review needs a nonempty film')
    return h.hexdigest(),{'duration':duration,'files':manifest,'created':time.time()}

def speech_words(root,film):
    """Only timing linked to present composition anchors and an actual native audio clip."""
    root=Path(root);clips=[c for t in film.get('tracks',[]) for c in t['clips']];ids={c['id'] for c in clips};out=[]
    for p in sorted((root/'review').glob('speech-*.json')):
        try:data=json.loads(p.read_text())
        except (ValueError,OSError):continue
        if not set(data.get('anchors',[])).issubset(ids) or not data.get('anchors'):continue
        source=root/data.get('source','')
        if not source.is_file() or data.get('source_hash')!=hashlib.sha256(source.read_bytes()).hexdigest():continue
        for c in clips:
            if c.get('src')!=data.get('source') or not (c.get('sound') or Path(c.get('src','')).suffix.lower() in ('.wav','.mp3','.m4a','.ogg','.flac','.aac')):continue
            a,b=c.get('time',[0,0]);speed=c.get('speed',1)
            for i,w in enumerate(data.get('words',[])):
                if not isinstance(w,dict) or not isinstance(w.get('word'),str) or not number(w.get('start')) or not number(w.get('end')) or not a<=w['start']<w['end']<=b:continue
                out.append({'id':p.stem+':'+c['id']+':'+str(i),'word':w['word'][:80],'start':c.get('at',0)+(w['start']-a)/speed,'end':c.get('at',0)+(w['end']-a)/speed,'reviewed':bool(data.get('reviewed'))})
    return out

def receipt(root,note,*,accept=False):
    # User acceptance stays on this installation, outside exported/imported projects.
    # Project JSON cannot promote an imported or edited note to owner intent.
    import sqlite3
    folder=Path.home()/'.local/state/skye';folder.mkdir(parents=True,exist_ok=True)
    digest=hashlib.sha256(json.dumps({k:note.get(k) for k in ('id','text','version','anchor')},sort_keys=True).encode()).hexdigest()
    with sqlite3.connect(folder/'review-authority.sqlite3',timeout=15) as db:
        db.execute('CREATE TABLE IF NOT EXISTS acceptance (project TEXT, note TEXT, digest TEXT, origin TEXT, PRIMARY KEY(project,note))')
        if accept:db.execute('INSERT OR REPLACE INTO acceptance VALUES (?,?,?,?)',(str(root),note['id'],digest,note['origin']))
        row=db.execute('SELECT digest,origin FROM acceptance WHERE project=? AND note=?',(str(root),note['id'])).fetchone()
    return row[1] if row and row[0]==digest else None

def apply(root,film,body,*,owner_ui=False):
    root=Path(root).resolve();action=body.get('action','list')
    if action not in ('list','add','approve','claim','status','reply','release'):raise MediaError('Unknown review action')
    with locked(root) as path:
        data=load(path);vid,meta=version(root,film);words=speech_words(root,film)
        for note in data['notes']:
            origin=receipt(root,note)
            if origin is None:
                note.update(approved=False,origin='external',status='pending');note.pop('claim_hash',None)
            else:note.update(approved=True,origin=origin)
        if action=='list':
            return {'version':vid,'duration':meta['duration'],'words':words,'versions':[{k:v for k,v in v.items() if k!='files'}|{'id':k} for k,v in data['versions'].items()], 'notes':[public(n,vid) for n in data['notes']]}
        if body.get('version')!=vid:raise MediaError('Version changed. Refresh Review')
        data['versions'].setdefault(vid,{**meta,'words':words})
        if action=='add':
            anchor=span(body.get('start'),body.get('end',body.get('start')),meta['duration']);word_ids=body.get('words',[])
            if not isinstance(word_ids,list) or len(word_ids)>1200:raise MediaError('Invalid word selection')
            if word_ids:
                selected=[w for w in words if w['id'] in word_ids]
                if len(selected)!=len(set(word_ids)):raise MediaError('Speech words changed')
                anchor=span(min(w['start'] for w in selected),max(w['end'] for w in selected),meta['duration']);anchor['words']=selected
            n={'id':secrets.token_hex(8),'version':vid,'anchor':anchor,'text':text(body.get('text'),'Note'),'origin':'owner-ui' if owner_ui else 'external','approved':owner_ui,'status':'open' if owner_ui else 'pending','created':time.time(),'replies':[]};data['notes'].append(n)
        else:
            n=next((n for n in data['notes'] if n['id']==body.get('note')),None)
            if n is None:raise MediaError('No such review note')
            if action=='approve':
                if not owner_ui:raise MediaError('Accept external notes in Review')
                if n['status']!='pending':raise MediaError('Note is not pending')
                n.update(approved=True,status='open',accepted=time.time())
            elif action=='release':
                if not owner_ui or n['status']!='working':raise MediaError('Release working claims in Review')
                n.update(status='open',progress='Released');n.pop('claim_hash',None)
            elif action=='claim':
                if not n['approved'] or n['status']!='open':raise MediaError('Note is not open for claim')
                if n['version']!=vid:raise MediaError('Note belongs to an older version. Add a new note on this version')
                token=secrets.token_urlsafe(24);n.update(status='working',actor=text(body.get('actor'),'Name',80),claim_hash=hashlib.sha256(token.encode()).hexdigest(),claimed=time.time(),progress='Working')
            else:
                token=body.get('claim','')
                if not isinstance(token,str) or not secrets.compare_digest(n.get('claim_hash','!'),hashlib.sha256(token.encode()).hexdigest()) or n['status']!='working':raise MediaError('Claim is missing, stale or held by someone else')
                if action=='status':n['progress']=text(body.get('text'),'Status',240)
                else:
                    ranges=body.get('ranges')
                    if not isinstance(ranges,list) or not 1<=len(ranges)<=50:raise MediaError('Reply needs changed time ranges')
                    changed=[span(r.get('start'),r.get('end'),meta['duration']) if isinstance(r,dict) else span(None,None,0) for r in ranges]
                    n['replies'].append({'text':text(body.get('text'),'Reply'),'version':vid,'ranges':changed,'actor':n['actor'],'created':time.time()});n.update(status='done',progress='Done');n.pop('claim_hash',None)
        if owner_ui and action in ('add','approve'):receipt(root,n,accept=True)
        write(path,data)
        result={'version':vid,'note':public(n,vid)}
        if action=='claim':result['claim']=token
        return result

def public(note,current):
    return {k:v for k,v in note.items() if k!='claim_hash'}|{'stale':note['version']!=current,'authority':'review-only' if note['approved'] else 'untrusted-data'}

def remember_speech(root,source,report,anchors):
    if report.get('mode')!='speech':return
    folder=Path(root)/'review'
    if folder.is_symlink():raise MediaError('Review folder must be local')
    folder.mkdir(exist_ok=True)
    if (folder/'.lock').is_symlink() or (folder/'notes.json').is_symlink():raise MediaError('Review files must be local')
    key=hashlib.sha256(json.dumps(anchors).encode()).hexdigest()[:16]
    write(folder/f'speech-{key}.json',{'source':source,'source_hash':hashlib.sha256((Path(root)/source).read_bytes()).hexdigest(),'anchors':anchors,'words':report.get('words',[]),'reviewed':all(w.get('reviewed') for w in report.get('words',[]))})

def main(argv=None):
    import argparse,subprocess
    p=argparse.ArgumentParser(description='Local version-bound review. External notes need acceptance in the UI.')
    p.add_argument('project',type=Path);p.add_argument('action',choices=['list','add','claim','status','reply']);p.add_argument('--version');p.add_argument('--note');p.add_argument('--actor',default='agent');p.add_argument('--claim');p.add_argument('--start',type=float);p.add_argument('--end',type=float);p.add_argument('--text-file',type=Path);p.add_argument('--ranges-file',type=Path);p.add_argument('--words-file',type=Path)
    a=p.parse_args(argv);root=a.project.resolve()
    film=json.loads(subprocess.check_output(['node',str(Path(__file__).with_name('effect-check.mjs'))],input=(root/'film.html').read_bytes()))
    body={k:v for k,v in vars(a).items() if k in ('action','version','note','actor','claim','start','end') and v is not None}
    if a.text_file:body['text']=a.text_file.read_text()
    if a.ranges_file:body['ranges']=json.loads(a.ranges_file.read_text())
    if a.words_file:body['words']=json.loads(a.words_file.read_text())
    print(json.dumps(apply(root,film,body)));return 0
if __name__=='__main__':raise SystemExit(main())
