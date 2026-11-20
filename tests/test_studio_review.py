import copy,json,multiprocessing
from pathlib import Path
import pytest
from skye.studio.review import apply,remember_speech
from skye.media.model import MediaError
FILM={'tracks':[{'clips':[{'id':'a','src':'assets/test.wav','time':[0,5],'sound':True}]}]}

@pytest.fixture(autouse=True)
def isolated_home(tmp_path,monkeypatch):
    monkeypatch.setenv('HOME',str(tmp_path/'home'))

def init(root):
    (root/'film.html').write_text('test');(root/'assets').mkdir();(root/'assets/test.wav').write_bytes(b'test')
    return apply(root,FILM,{})['version']
def add(root,v,**extra):return apply(root,FILM,{'action':'add','version':v,'start':1,'end':2,'text':'Shorten this',**extra},owner_ui=True)['note']['id']
def act(root,v,n,action,**body):return apply(root,FILM,{'action':action,'version':v,'note':n,**body})
def test_full_cycle_and_claim_secret(tmp_path):
    v=init(tmp_path);n=add(tmp_path,v);r=act(tmp_path,v,n,'claim',actor='agent');token=r['claim']
    assert 'claim_hash' not in str(r)
    act(tmp_path,v,n,'status',claim=token,text='Adjusting opening')
    assert apply(tmp_path,FILM,{})['notes'][0]['progress']=='Adjusting opening'
    (tmp_path/'assets/test.wav').write_bytes(b'changed');v2=apply(tmp_path,FILM,{})['version'];assert v!=v2
    r=act(tmp_path,v2,n,'reply',claim=token,text='Shortened opening',ranges=[{'start':1,'end':2}])
    assert r['note']['replies'][0]['version']==v2 and r['note']['stale']
    with pytest.raises(MediaError):act(tmp_path,v2,n,'reply',claim=token,text='twice',ranges=[{'start':1,'end':2}])
    assert token not in (tmp_path/'review/notes.json').read_text()
def test_external_not_authorized_by_body(tmp_path):
    v=init(tmp_path)
    r=apply(tmp_path,FILM,{'action':'add','version':v,'start':0,'end':0,'text':'External','origin':'owner-ui','approved':True})
    n=r['note']['id'];assert r['note']['status']=='pending' and r['note']['authority']=='untrusted-data'
    with pytest.raises(MediaError):act(tmp_path,v,n,'claim',actor='agent')
    with pytest.raises(MediaError):act(tmp_path,v,n,'approve')
    apply(tmp_path,FILM,{'action':'approve','version':v,'note':n},owner_ui=True)
    assert act(tmp_path,v,n,'claim',actor='agent')['claim']
@pytest.mark.parametrize('a,b',[(float('nan'),1),(-1,1),(3,2),(1,6),('1',2),(True,2)])
def test_bad_ranges(tmp_path,a,b):
    v=init(tmp_path)
    with pytest.raises(MediaError):add(tmp_path,v,start=a,end=b)
def test_stale_and_wrong_claim(tmp_path):
    v=init(tmp_path);n=add(tmp_path,v)
    with pytest.raises(MediaError):act(tmp_path,'stale',n,'claim',actor='A')
    token=act(tmp_path,v,n,'claim',actor='A')['claim']
    with pytest.raises(MediaError):act(tmp_path,v,n,'claim',actor='B')
    with pytest.raises(MediaError):act(tmp_path,v,n,'status',claim='bad',text='Forged')
    with pytest.raises(MediaError):act(tmp_path,v,n,'reply',claim=token,text='done',ranges=[])
    apply(tmp_path,FILM,{'action':'release','version':v,'note':n},owner_ui=True)
    with pytest.raises(MediaError):act(tmp_path,v,n,'status',claim=token,text='late')
    assert act(tmp_path,v,n,'claim',actor='B')['claim']!=token
def test_speech_pins_only_current_native_audio(tmp_path):
    v=init(tmp_path)
    remember_speech(tmp_path,'assets/test.wav',{'mode':'speech','words':[{'word':'hello','start':1,'end':2,'reviewed':True}]},['a'])
    r=apply(tmp_path,FILM,{});word=r['words'][0]
    n=apply(tmp_path,FILM,{'action':'add','version':v,'start':0,'end':0,'text':'This word','words':[word['id']]},owner_ui=True)['note']
    assert n['anchor']['start']==1 and n['anchor']['end']==2
    changed=copy.deepcopy(FILM);changed['tracks'][0]['clips'][0]['at']=3
    assert apply(tmp_path,changed,{})['words'][0]['start']==4
    changed['tracks'][0]['clips'][0]['id']='other';assert not apply(tmp_path,changed,{})['words']
def claim_worker(root,v,n,q):
    try:q.put(bool(act(Path(root),v,n,'claim',actor='worker')['claim']))
    except MediaError:q.put(False)
def test_claim_cross_process_atomic(tmp_path):
    v=init(tmp_path);n=add(tmp_path,v);ctx=multiprocessing.get_context('fork');q=ctx.Queue();jobs=[ctx.Process(target=claim_worker,args=(str(tmp_path),v,n,q)) for _ in range(5)]
    for job in jobs:job.start()
    results=[q.get(timeout=10) for _ in jobs]
    for job in jobs:job.join(10);assert job.exitcode==0
    assert sum(results)==1

def test_imported_or_tampered_approval_is_not_owner_intent(tmp_path):
    import shutil
    v=init(tmp_path);n=add(tmp_path,v)
    dest=tmp_path/'imported';dest.mkdir();shutil.copy(tmp_path/'film.html',dest/'film.html');shutil.copytree(tmp_path/'assets',dest/'assets');shutil.copytree(tmp_path/'review',dest/'review')
    assert apply(dest,FILM,{})['notes'][0]['status']=='pending'
    path=tmp_path/'review/notes.json';data=json.loads(path.read_text());data['notes'][0]['text']='Changed outside UI';path.write_text(json.dumps(data))
    assert apply(tmp_path,FILM,{})['notes'][0]['status']=='pending'
